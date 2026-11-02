"""Esecuzione da riga di comando (es. per job schedulati).

Esempi:
  python cli.py --demo -o report.xlsx
  python cli.py --pages gsc_pagine.csv --pages-prev gsc_pagine_prev.csv \
      --queries gsc_query.csv --content screaming_frog.csv -o report.xlsx
  python cli.py --demo --briefs 3 --briefs-dir brief/
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from curation import sample_data
from curation.config import ScoringConfig
from curation.pipeline import Dataset, queries_for, run
from curation.sources import csv_import


def main():
    ap = argparse.ArgumentParser(description="Seleziona gli articoli da sottoporre a content curation.")
    ap.add_argument("--demo", action="store_true", help="usa il dataset dimostrativo")
    ap.add_argument("--pages"); ap.add_argument("--pages-prev"); ap.add_argument("--queries")
    ap.add_argument("--content"); ap.add_argument("--seo")
    ap.add_argument("--sitemap", help="crawla gli articoli da questa sitemap")
    ap.add_argument("--filter", help="regex sugli URL (es. /blog/)")
    ap.add_argument("--min-priority", type=float, default=None)
    ap.add_argument("-o", "--output", default="content_curation.xlsx")
    ap.add_argument("--briefs", type=int, default=0, help="genera i brief AI per i primi N candidati")
    ap.add_argument("--briefs-dir", default="brief")
    ap.add_argument("--no-web", action="store_true", help="brief senza analisi SERP via ricerca web")
    a = ap.parse_args()

    if a.demo:
        ds = sample_data.build()
    else:
        ds = Dataset()
        if a.pages:
            ds.pages = csv_import.load_pages(a.pages, a.pages_prev)
        if a.queries:
            ds.queries = csv_import.load_queries(a.queries)
        if a.content:
            ds.content = csv_import.load_content(a.content)
        if a.seo:
            ds.seo = csv_import.load_seo_tool(a.seo)
        if a.sitemap:
            from curation.sources import crawler
            ds.content = crawler.from_sitemap(a.sitemap, a.filter)

    cfg = ScoringConfig()
    if a.min_priority is not None:
        cfg.thresholds.candidate_min_priority = a.min_priority
    df = run(ds, cfg, a.filter)
    cand = df[df["is_candidate"]]

    out = df.drop(columns=[c for c in ("text", "h2_list") if c in df.columns])
    if a.output.endswith(".csv"):
        out.to_csv(a.output, index=False)
    else:
        with pd.ExcelWriter(a.output, engine="openpyxl") as w:
            out[out["is_candidate"]].to_excel(w, sheet_name="Candidati", index=False)
            out.to_excel(w, sheet_name="Tutti gli articoli", index=False)

    print(f"Articoli analizzati: {len(df)} · candidati: {len(cand)} · report: {a.output}\n")
    for _, r in cand.head(15).iterrows():
        print(f"{r['priority']:5.1f}  {r['action']:<46}  {r['url']}")

    if a.briefs:
        from curation.brief import generate_brief
        Path(a.briefs_dir).mkdir(exist_ok=True)
        for _, r in cand[cand["action"] != "Valutare pruning (noindex o redirect 301)"].head(a.briefs).iterrows():
            print(f"\nBrief per {r['url']}…")
            b = generate_brief(r, queries_for(ds, r["url"]), use_web=not a.no_web)
            path = Path(a.briefs_dir) / f"brief-{r['url'].rstrip('/').rsplit('/', 1)[-1]}.md"
            path.write_text(f"# {r['url']}\n\n{b.markdown}", encoding="utf-8")
            print(f"  → {path} ({b.output_tokens} token)")


if __name__ == "__main__":
    main()
