"""Unione delle fonti e calcolo del ranking finale."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import ScoringConfig
from .schema import CONTENT_COLS, PAGES_COLS, QUERIES_COLS, SEO_COLS, ensure_columns
from .scoring import apply_recommendations, compute_signals, query_features, score


@dataclass
class Dataset:
    pages: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=PAGES_COLS))
    queries: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=QUERIES_COLS))
    content: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=CONTENT_COLS))
    seo: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=SEO_COLS))

    def sources(self) -> dict[str, int]:
        return {k: len(getattr(self, k)) for k in ("pages", "queries", "content", "seo")}


def merge(ds: Dataset, url_filter: str | None = None) -> pd.DataFrame:
    """Un record per URL. La base è l'unione di tutti gli URL noti alle varie fonti."""
    frames = [ensure_columns(ds.pages, PAGES_COLS), ensure_columns(ds.content, CONTENT_COLS),
              ensure_columns(ds.seo, SEO_COLS)]
    urls = pd.concat([f["url"] for f in frames if not f.empty], ignore_index=True).dropna().unique()
    base = pd.DataFrame({"url": urls})
    if url_filter:
        base = base[base["url"].str.contains(url_filter, regex=True, na=False)]

    out = base
    for f in frames:
        if not f.empty:
            f = f.drop_duplicates("url")
            out = out.merge(f[[c for c in f.columns if c not in out.columns or c == "url"]],
                            on="url", how="left")
    return out


def run(ds: Dataset, cfg: ScoringConfig | None = None, url_filter: str | None = None) -> pd.DataFrame:
    cfg = cfg or ScoringConfig()
    df = merge(ds, url_filter)
    queries = ensure_columns(ds.queries, QUERIES_COLS)
    if url_filter and not queries.empty:
        queries = queries[queries["url"].str.contains(url_filter, regex=True, na=False)]
    qf = query_features(queries, cfg)
    df = df.merge(qf, on="url", how="left")
    df = compute_signals(df, cfg)
    df = score(df, cfg)
    df = apply_recommendations(df, cfg)
    return df.sort_values("priority", ascending=False).reset_index(drop=True)


def queries_for(ds: Dataset, url: str, limit: int = 30) -> pd.DataFrame:
    q = ensure_columns(ds.queries, QUERIES_COLS)
    return q[q["url"] == url].sort_values("impressions", ascending=False).head(limit)
