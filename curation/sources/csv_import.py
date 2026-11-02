"""Import da export CSV/Excel (GSC, Screaming Frog, Ahrefs, Semrush, CMS).

Le intestazioni vengono riconosciute tramite alias (italiano e inglese), così si
possono caricare gli export così come escono dai tool.
"""
from __future__ import annotations

import io
import re
from typing import IO

import pandas as pd

from ..schema import (
    CONTENT_COLS, PAGES_COLS, QUERIES_COLS, SEO_COLS, ensure_columns,
)

ALIASES: dict[str, list[str]] = {
    "url": ["url", "page", "pagina", "pagine principali", "top pages", "landing page",
            "address", "indirizzo", "link", "current url"],
    "query": ["query", "query principali", "top queries", "keyword", "parola chiave"],
    "clicks": ["clicks", "clic", "click"],
    "impressions": ["impressions", "impressioni"],
    "ctr": ["ctr"],
    "position": ["position", "posizione", "average position", "posizione media"],
    "title": ["title", "title 1", "titolo", "meta title"],
    "meta_description": ["meta description", "meta description 1", "description"],
    "h1": ["h1", "h1-1"],
    "h2_count": ["h2 count", "h2_count", "numero h2"],
    "word_count": ["word count", "word_count", "parole", "numero parole"],
    "published": ["published", "date published", "data pubblicazione", "datepublished"],
    "modified": ["modified", "last modified", "date modified", "ultima modifica",
                 "datemodified", "lastmod"],
    "internal_links": ["internal links", "inlinks", "unique inlinks", "link interni"],
    "traffic": ["traffic", "organic traffic", "traffico", "sum_traffic", "current traffic"],
    "traffic_prev": ["previous traffic", "traffic_prev", "traffico precedente"],
    "keywords": ["keywords", "organic keywords", "# of keywords", "numero keyword"],
    "top_keyword": ["top keyword", "top_keyword", "keyword principale"],
    "top_keyword_volume": ["top keyword: volume", "top_keyword_volume", "volume"],
    "top_keyword_position": ["top keyword: position", "top_keyword_position"],
    "referring_domains": ["referring domains", "referring_domains", "ref. domains",
                          "domini di riferimento"],
    "traffic_value": ["traffic value", "value", "valore traffico"],
}


def _canon(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip().lower())


def _rename(df: pd.DataFrame) -> pd.DataFrame:
    lookup = {alias: target for target, aliases in ALIASES.items() for alias in aliases}
    mapping = {}
    for col in df.columns:
        target = lookup.get(_canon(col))
        if target and target not in mapping.values():
            mapping[col] = target
    return df.rename(columns=mapping)


INTEGER_METRICS = {"clicks", "impressions", "word_count", "internal_links", "h2_count", "traffic",
                   "traffic_prev", "keywords", "top_keyword_volume", "referring_domains"}


def _to_number(series: pd.Series, integer: bool = False) -> pd.Series:
    """Converte numeri in formato italiano o inglese ('1.234,5', '3,2%', '1,234').

    Il formato si decide per colonna: se qualche valore usa la virgola come separatore
    decimale (es. '7,2'), i punti della colonna sono separatori delle migliaia.
    """
    if pd.api.types.is_numeric_dtype(series):
        return series
    s = series.astype(str).str.strip().str.replace("%", "", regex=False)
    s = s.str.replace(" ", "", regex=False).str.replace(" ", "", regex=False)
    italian = s.str.match(r"^-?\d{1,3}(\.\d{3})*,\d+$").any()
    if italian:
        s = s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    else:
        if integer:  # '3.400' in una metrica intera = migliaia
            s = s.where(~s.str.match(r"^\d{1,3}(\.\d{3})+$"), s.str.replace(".", "", regex=False))
        s = s.str.replace(",", "", regex=False)
    return pd.to_numeric(s, errors="coerce")


def read_table(file: str | IO | bytes, filename: str | None = None) -> pd.DataFrame:
    name = (filename or (file if isinstance(file, str) else getattr(file, "name", ""))).lower()
    if isinstance(file, bytes):
        file = io.BytesIO(file)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(file)
    # separatore automatico (GSC IT esporta con virgola, alcuni tool con ;)
    # dtype=str: la conversione numerica la fa _to_number, che gestisce il formato italiano
    return pd.read_csv(file, sep=None, engine="python", dtype=str)


def _clean(df: pd.DataFrame, numeric: list[str]) -> pd.DataFrame:
    df = _rename(df)
    for c in numeric:
        if c in df.columns:
            df[c] = _to_number(df[c], integer=c in INTEGER_METRICS)
    if "ctr" in df.columns and df["ctr"].dropna().gt(1).any():
        df["ctr"] = df["ctr"] / 100  # CTR espresso in percentuale
    return df


def load_pages(current, previous=None, current_name=None, previous_name=None) -> pd.DataFrame:
    """Export 'Pagine' di GSC. `previous` è l'export dello stesso report sul periodo di confronto."""
    num = ["clicks", "impressions", "ctr", "position"]
    cur = _clean(read_table(current, current_name), num)
    if previous is not None:
        prev = _clean(read_table(previous, previous_name), num)
        prev = prev[["url", "clicks", "impressions", "position"]].rename(columns={
            "clicks": "clicks_prev", "impressions": "impressions_prev", "position": "position_prev"})
        cur = cur.merge(prev, on="url", how="left")
    if "ctr" not in cur.columns or cur["ctr"].isna().all():
        cur["ctr"] = cur["clicks"] / cur["impressions"].replace(0, pd.NA)
    return ensure_columns(cur, PAGES_COLS)


def load_queries(file, filename=None) -> pd.DataFrame:
    """Export pagina+query (da API GSC, Looker Studio, BigQuery bulk export...)."""
    df = _clean(read_table(file, filename), ["clicks", "impressions", "ctr", "position"])
    if "ctr" not in df.columns or df["ctr"].isna().all():
        df["ctr"] = df["clicks"] / df["impressions"].replace(0, pd.NA)
    return ensure_columns(df, QUERIES_COLS)


def load_content(file, filename=None) -> pd.DataFrame:
    """Export contenuti (es. Screaming Frog 'Internal: HTML' o export CMS)."""
    df = _clean(read_table(file, filename), ["h2_count", "word_count", "internal_links"])
    for c in ("published", "modified"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", utc=True).dt.tz_localize(None)
    return ensure_columns(df, CONTENT_COLS)


def load_seo_tool(file, filename=None) -> pd.DataFrame:
    """Export 'Top pages' di Ahrefs o 'Pages' di Semrush."""
    df = _clean(read_table(file, filename), [
        "traffic", "traffic_prev", "keywords", "top_keyword_volume",
        "top_keyword_position", "referring_domains", "traffic_value"])
    return ensure_columns(df, SEO_COLS)
