"""Schema canonico dei dataset su cui lavora la pipeline.

Ogni connettore (CSV, GSC, crawler, Ahrefs, Semrush) produce uno o più di questi
DataFrame; la pipeline li unisce per `url`.
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import pandas as pd

# Performance per pagina (GSC): periodo corrente + periodo di confronto
PAGES_COLS = [
    "url", "clicks", "impressions", "ctr", "position",
    "clicks_prev", "impressions_prev", "position_prev",
]
# Performance per pagina+query (GSC)
QUERIES_COLS = ["url", "query", "clicks", "impressions", "ctr", "position"]
# Contenuto (CMS / sitemap crawl)
CONTENT_COLS = [
    "url", "title", "meta_description", "h1", "h2_count", "word_count",
    "published", "modified", "internal_links", "text",
]
# Dati di terze parti (Ahrefs / Semrush)
SEO_COLS = [
    "url", "traffic", "traffic_prev", "keywords", "top_keyword",
    "top_keyword_volume", "top_keyword_position", "referring_domains", "traffic_value",
]


def normalize_url(url: str) -> str:
    """Normalizza un URL per il join tra fonti diverse.

    Rimuove querystring/frammento, forza lo slash finale e il dominio in minuscolo.
    """
    if not isinstance(url, str) or not url.strip():
        return url
    parts = urlsplit(url.strip())
    path = parts.path or "/"
    if not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
        path += "/"
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(), path, "", ""))


def ensure_columns(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for c in cols:
        if c not in df.columns:
            df[c] = pd.NA
    if "url" in df.columns:
        df["url"] = df["url"].map(normalize_url)
    return df[cols + [c for c in df.columns if c not in cols]]
