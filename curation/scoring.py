"""Calcolo dei segnali di curation, dello score di priorità e dell'azione consigliata.

Ogni segnale è normalizzato in [0, 1]:
  1 = il problema/opportunità è al massimo, 0 = assente.
Lo score di priorità (0-100) combina l'intensità pesata dei segnali con il
potenziale di clic recuperabili, così un articolo con molti problemi ma senza
domanda di ricerca non scavalca uno con un'opportunità concreta.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from .config import SIGNALS, ScoringConfig, expected_ctr


def _clip01(x):
    return np.clip(x, 0.0, 1.0)


def _log_norm(series: pd.Series) -> pd.Series:
    s = np.log1p(series.fillna(0).clip(lower=0))
    m = s.max()
    return s / m if m and m > 0 else s * 0


# --------------------------------------------------------------------------- query-level
def query_features(queries: pd.DataFrame, cfg: ScoringConfig) -> pd.DataFrame:
    """Aggrega le query per URL: striking distance, potenziale e cannibalizzazione."""
    t = cfg.thresholds
    cols = ["url", "striking_queries", "striking_impressions", "potential_clicks",
            "cannibal_queries", "cannibal_impressions", "top_query", "top_query_position"]
    if queries is None or queries.empty:
        return pd.DataFrame(columns=cols)

    q = queries.dropna(subset=["url", "query"]).copy()
    q["position"] = q["position"].astype(float)
    target_ctr = expected_ctr(t.target_position)

    sd = q[(q["position"] >= t.striking_min_pos) & (q["position"] <= t.striking_max_pos)].copy()
    sd["gain"] = (sd["impressions"] * (target_ctr - sd["ctr"].fillna(0))).clip(lower=0)
    striking = sd.groupby("url").agg(
        striking_queries=("query", "count"),
        striking_impressions=("impressions", "sum"),
        potential_clicks=("gain", "sum"),
    )

    # Cannibalizzazione: stessa query con più URL rilevanti del sito
    c = q[(q["impressions"] >= t.cannibal_min_impressions) & (q["position"] <= t.cannibal_max_position)]
    multi = c.groupby("query")["url"].nunique()
    contested = c[c["query"].isin(multi[multi > 1].index)]
    # Penalizza solo gli URL "perdenti" (non il migliore per quella query)
    best = contested.sort_values(["query", "position"]).groupby("query")["url"].first()
    losers = contested[contested["url"] != contested["query"].map(best)]
    cannibal = losers.groupby("url").agg(
        cannibal_queries=("query", "nunique"), cannibal_impressions=("impressions", "sum"))

    top = (q.sort_values("clicks", ascending=False).groupby("url")
             .agg(top_query=("query", "first"), top_query_position=("position", "first")))

    out = striking.join(cannibal, how="outer").join(top, how="outer").reset_index()
    for col in cols:
        if col not in out.columns:
            out[col] = np.nan
    return out[cols]


def cannibalization_groups(queries: pd.DataFrame, cfg: ScoringConfig) -> pd.DataFrame:
    """Elenco query contese da più URL, per la vista dedicata."""
    t = cfg.thresholds
    if queries is None or queries.empty:
        return pd.DataFrame()
    c = queries[(queries["impressions"] >= t.cannibal_min_impressions)
                & (queries["position"] <= t.cannibal_max_position)]
    multi = c.groupby("query")["url"].nunique()
    g = c[c["query"].isin(multi[multi > 1].index)].sort_values(["query", "position"])
    return g[["query", "url", "clicks", "impressions", "position"]]


# --------------------------------------------------------------------------- signals
def compute_signals(df: pd.DataFrame, cfg: ScoringConfig, today: datetime | None = None) -> pd.DataFrame:
    t = cfg.thresholds
    today = pd.Timestamp(today or datetime.now())
    df = df.copy()
    num = lambda c: pd.to_numeric(df.get(c), errors="coerce") if c in df else pd.Series(np.nan, index=df.index)  # noqa: E731

    clicks, clicks_prev = num("clicks"), num("clicks_prev")
    impressions, position, ctr = num("impressions"), num("position"), num("ctr")

    # 1. Decadimento: calo % dei clic (GSC) o, in assenza, del traffico stimato (Ahrefs)
    base_cur, base_prev = clicks, clicks_prev
    if base_prev.isna().all() and "traffic_prev" in df:
        base_cur, base_prev = num("traffic"), num("traffic_prev")
    change = (base_cur - base_prev) / base_prev.replace(0, np.nan)
    df["clicks_change_pct"] = change
    df["sig_decay"] = _clip01(-change / t.decay_full).where(base_prev >= t.min_clicks_prev, 0).fillna(0)

    # 2. Striking distance: quota di potenziale non sfruttato sulle query in pos. 4-20.
    #    = clic guadagnabili / (clic attuali + clic guadagnabili). Un articolo che già
    #    capitalizza bene la sua domanda ha un valore basso anche con molte long tail.
    page_level = impressions.where((position >= t.striking_min_pos) & (position <= t.striking_max_pos))
    df["striking_impressions"] = num("striking_impressions").fillna(page_level)
    fallback_gain = (page_level * (expected_ctr(t.target_position) - ctr)).clip(lower=0)
    gain = num("potential_clicks").fillna(fallback_gain).fillna(0)
    df["sig_striking"] = _clip01(gain / (clicks.fillna(0) + gain).replace(0, np.nan)).fillna(0)

    # 3. CTR gap rispetto alla curva attesa (solo top 10 con impressioni sufficienti)
    exp = position.map(expected_ctr)
    gap = _clip01(1 - ctr / exp.replace(0, np.nan))
    df["expected_ctr"] = exp
    df["sig_ctr_gap"] = gap.where((impressions >= t.min_impressions_ctr) & (position <= 10), 0).fillna(0)

    # 4. Freschezza
    date = lambda c: pd.to_datetime(df[c], errors="coerce") if c in df else pd.Series(pd.NaT, index=df.index)  # noqa: E731
    mod, pub = date("modified"), date("published")
    last = mod.fillna(pub)
    df["days_since_update"] = (today - last).dt.days
    span = max(1, t.stale_full_days - t.stale_start_days)
    df["sig_staleness"] = _clip01((df["days_since_update"] - t.stale_start_days) / span).fillna(0)

    # 5. Thin content
    wc = num("word_count")
    df["sig_thin"] = _clip01(1 - wc / t.min_words).fillna(0)

    # 6. Cannibalizzazione
    df["sig_cannibalization"] = _log_norm(num("cannibal_impressions"))

    # Potenziale: clic recuperabili portando le query striking alla posizione obiettivo,
    # + clic persi rispetto al periodo precedente, + gap CTR sulla pagina.
    lost = (base_prev - base_cur).clip(lower=0).fillna(0)
    ctr_loss = (impressions * (exp - ctr)).clip(lower=0).where(df["sig_ctr_gap"] > 0, 0).fillna(0)
    df["recoverable_clicks"] = (gain + lost + ctr_loss).round()
    return df


def score(df: pd.DataFrame, cfg: ScoringConfig) -> pd.DataFrame:
    w = cfg.weights.__dict__
    total_w = sum(w.values()) or 1
    df = df.copy()
    # 100 = i tre segnali più pesanti sono tutti al massimo (raramente un articolo
    # ha tutti e sei i problemi insieme, quindi la media su sei appiattirebbe lo score)
    top3 = sum(sorted(w.values(), reverse=True)[:3]) or total_w
    weighted = sum(df[f"sig_{k}"] * v for k, v in w.items())
    df["signal_score"] = (weighted / top3 * 100).clip(upper=100)
    # rango percentile: distribuisce il potenziale in modo uniforme sul portafoglio
    rc = df["recoverable_clicks"].fillna(0)
    df["potential_score"] = rc.rank(pct=True).where(rc > 0, 0) * 100
    p = cfg.potential_share
    df["priority"] = ((1 - p) * df["signal_score"] + p * df["potential_score"]).round(1)
    df["main_signal"] = df[[f"sig_{k}" for k in w]].mul(pd.Series(w).values).idxmax(axis=1) \
        .str.replace("sig_", "", regex=False).map(SIGNALS)
    df.loc[df[[f"sig_{k}" for k in w]].sum(axis=1) == 0, "main_signal"] = "—"
    return df


# --------------------------------------------------------------------------- actions
ACTIONS = {
    "merge": "Accorpare / differenziare (cannibalizzazione)",
    "prune": "Valutare pruning (noindex o redirect 301)",
    "snippet": "Ottimizzare title e meta description",
    "refresh": "Aggiornare il contenuto (refresh)",
    "expand": "Espandere e approfondire",
    "monitor": "Monitorare",
}


def _n(v, default=0.0) -> float:
    """Valore numerico con fallback per None/NaN."""
    return default if v is None or pd.isna(v) else float(v)


def recommend(row: pd.Series, cfg: ScoringConfig) -> tuple[str, list[str], str]:
    """Ritorna (azione principale, azioni secondarie, motivazioni leggibili)."""
    reasons: list[str] = []
    actions: list[str] = []
    t = cfg.thresholds

    if row.get("sig_cannibalization", 0) >= 0.5:
        actions.append("merge")
        reasons.append(f"{int(_n(row.get('cannibal_queries')))} query contese con altri URL del sito")
    # senza dati GSC non si può dire che un articolo sia invisibile: niente pruning alla cieca
    low_value = (pd.notna(row.get("impressions")) and _n(row.get("impressions")) < 100
                 and _n(row.get("recoverable_clicks")) < 5)
    if low_value and row.get("sig_staleness", 0) > 0.5 and row.get("sig_thin", 0) > 0.3:
        actions.append("prune")
        reasons.append("visibilità quasi nulla, contenuto datato e scarno")
    if row.get("sig_ctr_gap", 0) >= 0.4 and _n(row.get("position"), 99) <= 6:
        actions.append("snippet")
        reasons.append(f"CTR {row.get('ctr', 0):.1%} contro {row.get('expected_ctr', 0):.1%} atteso in pos. {row.get('position', 0):.1f}")
    if row.get("sig_decay", 0) >= 0.3:
        actions.append("refresh")
        reasons.append(f"clic {row.get('clicks_change_pct', 0):+.0%} rispetto al periodo precedente")
    if row.get("sig_staleness", 0) >= 0.5 and "refresh" not in actions:
        actions.append("refresh")
        reasons.append(f"non aggiornato da {int(_n(row.get('days_since_update')))} giorni")
    if row.get("sig_striking", 0) >= 0.5 or row.get("sig_thin", 0) >= 0.4:
        actions.append("expand")
        if row.get("sig_striking", 0) >= 0.5:
            reasons.append(f"{int(_n(row.get('striking_queries')))} query in posizione {t.striking_min_pos:.0f}-{t.striking_max_pos:.0f}")
        if row.get("sig_thin", 0) >= 0.4:
            reasons.append(f"solo {int(_n(row.get('word_count')))} parole (soglia {t.min_words})")
    if not actions:
        actions.append("monitor")
    return ACTIONS[actions[0]], [ACTIONS[a] for a in actions[1:]], "; ".join(reasons)


def apply_recommendations(df: pd.DataFrame, cfg: ScoringConfig) -> pd.DataFrame:
    df = df.copy()
    rec = df.apply(lambda r: recommend(r, cfg), axis=1, result_type="expand")
    df["action"], df["secondary_actions"], df["reasons"] = rec[0], rec[1], rec[2]
    df["secondary_actions"] = df["secondary_actions"].map(lambda xs: ", ".join(xs))
    # il pruning ha priorità di traffico bassa per definizione, ma è comunque curation
    df["is_candidate"] = (
        ((df["priority"] >= cfg.thresholds.candidate_min_priority) & (df["action"] != ACTIONS["monitor"]))
        | (df["action"] == ACTIONS["prune"])
    )
    return df
