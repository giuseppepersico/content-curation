"""Connettore Google Search Console (Search Analytics API).

Autenticazione con service account: aggiungi l'email del service account come
utente (anche solo lettura) della proprietà in Search Console.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from ..schema import PAGES_COLS, QUERIES_COLS, ensure_columns

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
ROW_LIMIT = 25000


def _service(credentials_file: str):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def _query(service, site_url: str, start: date, end: date, dimensions: list[str],
           page_filter: str | None = None, max_rows: int = 100_000) -> pd.DataFrame:
    rows, start_row = [], 0
    body = {
        "startDate": start.isoformat(), "endDate": end.isoformat(),
        "dimensions": dimensions, "rowLimit": ROW_LIMIT, "dataState": "final",
    }
    if page_filter:
        body["dimensionFilterGroups"] = [{"filters": [
            {"dimension": "page", "operator": "contains", "expression": page_filter}]}]
    while start_row < max_rows:
        body["startRow"] = start_row
        resp = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
        batch = resp.get("rows", [])
        for r in batch:
            rec = dict(zip(dimensions, r["keys"]))
            rec.update(clicks=r["clicks"], impressions=r["impressions"],
                       ctr=r["ctr"], position=r["position"])
            rows.append(rec)
        if len(batch) < ROW_LIMIT:
            break
        start_row += ROW_LIMIT
    df = pd.DataFrame(rows)
    return df.rename(columns={"page": "url"})


def default_periods(days: int = 90, end: date | None = None, yoy: bool = False):
    """Periodo corrente di `days` giorni (con 3 giorni di ritardo dati) e periodo di confronto."""
    end = end or (date.today() - timedelta(days=3))
    start = end - timedelta(days=days - 1)
    if yoy:
        prev_end, prev_start = end - timedelta(days=364), start - timedelta(days=364)
    else:
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=days - 1)
    return (start, end), (prev_start, prev_end)


def fetch(credentials_file: str, site_url: str, days: int = 90, yoy: bool = False,
          page_filter: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Ritorna (pages_df, queries_df) per il periodo corrente con confronto sul precedente."""
    svc = _service(credentials_file)
    (s, e), (ps, pe) = default_periods(days, yoy=yoy)

    cur = _query(svc, site_url, s, e, ["page"], page_filter)
    prev = _query(svc, site_url, ps, pe, ["page"], page_filter)
    if not prev.empty:
        prev = prev[["url", "clicks", "impressions", "position"]].rename(columns={
            "clicks": "clicks_prev", "impressions": "impressions_prev", "position": "position_prev"})
        cur = cur.merge(prev, on="url", how="left")
    queries = _query(svc, site_url, s, e, ["page", "query"], page_filter)
    return ensure_columns(cur, PAGES_COLS), ensure_columns(queries, QUERIES_COLS)
