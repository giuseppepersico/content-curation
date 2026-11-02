import io

import pandas as pd

from curation import sample_data
from curation.config import ScoringConfig
from curation.pipeline import Dataset, run
from curation.scoring import ACTIONS
from curation.sources import csv_import


def _by_arch(df):
    arch = {f"{sample_data.DOMAIN}{s}/": a for s, _, a in sample_data.ARTICLES}
    return df.assign(arch=df["url"].map(arch))


def test_demo_selects_problems_and_skips_healthy():
    df = _by_arch(run(sample_data.build()))
    healthy = df[df["arch"] == "healthy"]
    decay = df[df["arch"] == "decay"]
    assert healthy["is_candidate"].mean() < 0.3
    assert decay["is_candidate"].all()
    assert decay["priority"].mean() > healthy["priority"].mean() + 30


def test_zombies_are_pruned_and_cannibals_merged():
    df = _by_arch(run(sample_data.build()))
    assert (df[df["arch"] == "zombie"]["action"] == ACTIONS["prune"]).all()
    assert (df[df["arch"] == "cannibal_b"]["action"] == ACTIONS["merge"]).all()


def test_weights_change_ranking():
    ds = sample_data.build()
    cfg = ScoringConfig()
    cfg.weights.decay, cfg.weights.striking = 0.0, 2.0
    a = run(ds).head(10)["url"].tolist()
    b = run(ds, cfg).head(10)["url"].tolist()
    assert a != b


def test_gsc_italian_export_parsing():
    csv = ("Pagine principali,Clic,Impressioni,CTR,Posizione\n"
           "https://x.it/blog/a,120,\"3.400\",\"3,53%\",\"7,2\"\n"
           "https://x.it/blog/b?utm=1,10,500,2%,12\n")
    prev = "Pagine principali,Clic,Impressioni,CTR,Posizione\nhttps://x.it/blog/a,300,5000,6%,4\n"
    pages = csv_import.load_pages(io.BytesIO(csv.encode()), io.BytesIO(prev.encode()), "a.csv", "b.csv")
    a = pages[pages["url"] == "https://x.it/blog/a/"].iloc[0]
    assert a["impressions"] == 3400 and abs(a["ctr"] - 0.0353) < 1e-6 and a["position"] == 7.2
    assert a["clicks_prev"] == 300
    assert "https://x.it/blog/b/" in pages["url"].tolist()  # querystring rimossa, slash aggiunto

    df = run(Dataset(pages=pages))
    row = df[df["url"] == "https://x.it/blog/a/"].iloc[0]
    assert row["sig_decay"] > 0.9  # -60% di clic


def test_works_with_content_only():
    content = pd.DataFrame({"url": ["https://x.it/a/", "https://x.it/b/"], "word_count": [300, 2000],
                            "modified": pd.to_datetime(["2020-01-01", "2026-10-01"])})
    df = run(Dataset(content=content))
    assert df.iloc[0]["url"] == "https://x.it/a/"
