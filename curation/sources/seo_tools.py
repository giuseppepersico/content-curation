"""Connettori Ahrefs (API v3) e Semrush (Analytics API)."""
from __future__ import annotations

import io
from datetime import date, timedelta

import pandas as pd
import requests

from ..schema import SEO_COLS, ensure_columns

TIMEOUT = 60


# --------------------------------------------------------------------------- Ahrefs
AHREFS_URL = "https://api.ahrefs.com/v3/site-explorer/top-pages"
AHREFS_SELECT = ",".join([
    "url", "sum_traffic", "sum_traffic_prev", "keywords", "top_keyword",
    "top_keyword_volume", "top_keyword_best_position", "referring_domains", "value",
])


def fetch_ahrefs(api_key: str, target: str, country: str = "IT", mode: str = "prefix",
                 compare_days: int = 90, limit: int = 1000) -> pd.DataFrame:
    """Top pages organiche del target (es. 'example.com/blog/') con confronto a N giorni."""
    today = date.today()
    params = {
        "target": target, "mode": mode, "country": country, "limit": limit,
        "select": AHREFS_SELECT, "date": today.isoformat(),
        "date_compared": (today - timedelta(days=compare_days)).isoformat(),
        "order_by": "sum_traffic:desc", "output": "json",
    }
    r = requests.get(AHREFS_URL, params=params, timeout=TIMEOUT,
                     headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    r.raise_for_status()
    df = pd.DataFrame(r.json().get("pages", []))
    if df.empty:
        return ensure_columns(df, SEO_COLS)
    df = df.rename(columns={
        "sum_traffic": "traffic", "sum_traffic_prev": "traffic_prev",
        "top_keyword_best_position": "top_keyword_position", "value": "traffic_value",
    })
    df["traffic_value"] = df["traffic_value"] / 100  # centesimi USD -> USD
    return ensure_columns(df, SEO_COLS)


# --------------------------------------------------------------------------- Semrush
SEMRUSH_URL = "https://api.semrush.com/"


def fetch_semrush(api_key: str, domain: str, database: str = "it", url_filter: str | None = None,
                  limit: int = 10000) -> pd.DataFrame:
    """Keyword organiche del dominio aggregate per URL (report domain_organic)."""
    params = {
        "type": "domain_organic", "key": api_key, "domain": domain, "database": database,
        "display_limit": limit, "export_columns": "Ph,Po,Nq,Ur,Tr,Tc",
        "display_sort": "tr_desc",
    }
    if url_filter:
        params["display_filter"] = f"+|Ur|Co|{url_filter}"
    r = requests.get(SEMRUSH_URL, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    if r.text.startswith("ERROR"):
        raise RuntimeError(f"Semrush: {r.text.strip()}")
    kw = pd.read_csv(io.StringIO(r.text), sep=";")
    kw.columns = ["keyword", "position", "volume", "url", "traffic_pct", "traffic_cost_pct"][: len(kw.columns)]
    kw["est_traffic"] = kw["volume"] * kw["position"].map(lambda p: 0.28 if p == 1 else 0.6 / p if p <= 10 else 0.005)
    kw = kw.sort_values("est_traffic", ascending=False)
    agg = kw.groupby("url").agg(
        traffic=("est_traffic", "sum"), keywords=("keyword", "count"),
        top_keyword=("keyword", "first"), top_keyword_volume=("volume", "first"),
        top_keyword_position=("position", "first"),
    ).reset_index()
    agg["traffic"] = agg["traffic"].round()
    return ensure_columns(agg, SEO_COLS)
