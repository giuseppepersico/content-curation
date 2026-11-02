"""Raccolta del contenuto degli articoli: sitemap XML + crawling, oppure WordPress REST API."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

import pandas as pd
import requests
from bs4 import BeautifulSoup

from ..schema import CONTENT_COLS, ensure_columns

UA = "Mozilla/5.0 (compatible; SEOCurationBot/1.0)"
TIMEOUT = 20
NOISE_TAGS = ["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "iframe"]


def _get(url: str, session: requests.Session) -> requests.Response:
    r = session.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
    r.raise_for_status()
    return r


# --------------------------------------------------------------------------- sitemap
def read_sitemap(sitemap_url: str, url_filter: str | None = None, limit: int = 500) -> pd.DataFrame:
    """Legge una sitemap (anche sitemap index) e ritorna url + lastmod."""
    session = requests.Session()
    found: list[dict] = []
    queue, seen = [sitemap_url], set()
    while queue and len(found) < limit:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        soup = BeautifulSoup(_get(sm, session).content, "xml")
        for loc in soup.select("sitemap > loc"):
            queue.append(loc.text.strip())
        for u in soup.select("url"):
            loc = u.find("loc")
            if not loc:
                continue
            href = loc.text.strip()
            if url_filter and not re.search(url_filter, href):
                continue
            lastmod = u.find("lastmod")
            found.append({"url": href, "lastmod": lastmod.text.strip() if lastmod else None})
            if len(found) >= limit:
                break
    df = pd.DataFrame(found, columns=["url", "lastmod"])
    df["lastmod"] = pd.to_datetime(df["lastmod"], errors="coerce", utc=True).dt.tz_localize(None)
    return df


# --------------------------------------------------------------------------- parsing
def _jsonld_dates(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    pub = mod = None
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for it in items:
            if isinstance(it, dict):
                pub = pub or it.get("datePublished")
                mod = mod or it.get("dateModified")
    return pub, mod


def parse_article(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    meta = lambda **kw: (soup.find("meta", attrs=kw) or {}).get("content")  # noqa: E731

    title = soup.title.get_text(strip=True) if soup.title else None
    desc = meta(name="description")
    pub = meta(property="article:published_time")
    mod = meta(property="article:modified_time") or meta(property="og:updated_time")
    ld_pub, ld_mod = _jsonld_dates(soup)
    pub, mod = pub or ld_pub, mod or ld_mod

    body = soup.find("article") or soup.find("main") or soup.body or soup
    for t in body.find_all(NOISE_TAGS):
        t.decompose()
    h1 = body.find("h1") or soup.find("h1")
    h2s = [h.get_text(" ", strip=True) for h in body.find_all("h2")]

    host = urlsplit(url).netloc
    internal = sum(
        1 for a in body.find_all("a", href=True)
        if urlsplit(urljoin(url, a["href"])).netloc == host
    )
    # Testo strutturato in markdown leggero: utile al brief AI
    lines = []
    for el in body.find_all(["h2", "h3", "h4", "p", "li"]):
        txt = el.get_text(" ", strip=True)
        if not txt:
            continue
        prefix = {"h2": "## ", "h3": "### ", "h4": "#### ", "li": "- "}.get(el.name, "")
        lines.append(prefix + txt)
    text = "\n".join(lines)

    return {
        "url": url, "title": title, "meta_description": desc,
        "h1": h1.get_text(" ", strip=True) if h1 else None,
        "h2_count": len(h2s), "h2_list": h2s,
        "word_count": len(re.findall(r"\w+", body.get_text(" "))),
        "published": pub, "modified": mod, "internal_links": internal, "text": text,
    }


def crawl(urls: list[str], workers: int = 8, lastmod: dict | None = None) -> pd.DataFrame:
    session = requests.Session()

    def one(u):
        try:
            return parse_article(u, _get(u, session).text)
        except Exception as exc:  # noqa: BLE001 - un URL non deve bloccare il crawl
            return {"url": u, "crawl_error": str(exc)[:200]}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(one, urls))
    df = pd.DataFrame(rows)
    for c in ("published", "modified"):
        if c not in df.columns:
            df[c] = None
        df[c] = pd.to_datetime(df[c], errors="coerce", utc=True).dt.tz_localize(None)
    if lastmod:  # fallback sulla lastmod della sitemap
        df["modified"] = df["modified"].fillna(df["url"].map(lastmod))
    return ensure_columns(df, CONTENT_COLS)


def from_sitemap(sitemap_url: str, url_filter: str | None = None, limit: int = 300,
                 workers: int = 8) -> pd.DataFrame:
    sm = read_sitemap(sitemap_url, url_filter, limit)
    return crawl(sm["url"].tolist(), workers, dict(zip(sm["url"], sm["lastmod"])))


# --------------------------------------------------------------------------- WordPress
def from_wordpress(site: str, limit: int = 300, auth: tuple[str, str] | None = None) -> pd.DataFrame:
    """Usa /wp-json/wp/v2/posts: date affidabili e HTML del solo corpo articolo."""
    base = site.rstrip("/") + "/wp-json/wp/v2/posts"
    session, rows, page = requests.Session(), [], 1
    while len(rows) < limit:
        r = session.get(base, params={"per_page": 100, "page": page, "status": "publish",
                                      "_fields": "link,title,date_gmt,modified_gmt,content,yoast_head_json"},
                        auth=auth, timeout=TIMEOUT, headers={"User-Agent": UA})
        if r.status_code == 400:  # pagina oltre l'ultima
            break
        r.raise_for_status()
        posts = r.json()
        if not posts:
            break
        for p in posts:
            parsed = parse_article(p["link"], f"<article>{p['content']['rendered']}</article>")
            yoast = p.get("yoast_head_json") or {}
            parsed.update(
                title=yoast.get("title") or BeautifulSoup(p["title"]["rendered"], "lxml").get_text(),
                meta_description=yoast.get("description"),
                h1=BeautifulSoup(p["title"]["rendered"], "lxml").get_text(),
                published=p.get("date_gmt"), modified=p.get("modified_gmt"),
            )
            rows.append(parsed)
        page += 1
    df = pd.DataFrame(rows[:limit])
    for c in ("published", "modified"):
        df[c] = pd.to_datetime(df[c], errors="coerce", utc=True).dt.tz_localize(None)
    return ensure_columns(df, CONTENT_COLS)
