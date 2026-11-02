"""Dataset dimostrativo: blog italiano di finanza personale con casi tipici di curation."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .pipeline import Dataset

DOMAIN = "https://www.esempio-finanza.it/blog/"

# (slug, tema/keyword principale, archetipo)
ARTICLES = [
    ("come-fare-il-730-precompilato", "730 precompilato", "decay"),
    ("migliori-conti-deposito", "migliori conti deposito", "striking"),
    ("conto-deposito-vincolato-o-libero", "conto deposito vincolato", "cannibal_a"),
    ("conti-deposito-guida", "conto deposito", "cannibal_b"),
    ("cos-e-il-tfr", "cos'è il tfr", "healthy"),
    ("tfr-in-busta-paga", "tfr in busta paga", "ctr"),
    ("isee-come-calcolarlo", "calcolo isee", "decay"),
    ("bonus-mobili-requisiti", "bonus mobili", "stale"),
    ("etf-cosa-sono", "etf cosa sono", "healthy"),
    ("etf-migliori-da-comprare", "migliori etf", "striking"),
    ("pac-piano-accumulo", "pac piano di accumulo", "striking"),
    ("pac-o-pic-differenze", "pac o pic", "thin"),
    ("buoni-fruttiferi-postali", "buoni fruttiferi postali", "decay"),
    ("btp-valore-come-funziona", "btp valore", "stale"),
    ("btp-italia-guida", "btp italia", "cannibal_a"),
    ("btp-italia-rendimento", "btp italia rendimento", "cannibal_b"),
    ("mutuo-prima-casa-under-36", "mutuo prima casa under 36", "stale"),
    ("tasso-fisso-o-variabile", "mutuo tasso fisso o variabile", "ctr"),
    ("surroga-mutuo", "surroga mutuo", "striking"),
    ("estinzione-anticipata-mutuo", "estinzione anticipata mutuo", "healthy"),
    ("carta-di-credito-o-debito", "carta di credito o debito", "thin"),
    ("carte-prepagate-migliori", "migliori carte prepagate", "decay"),
    ("bonifico-istantaneo-costi", "bonifico istantaneo costi", "ctr"),
    ("iban-cos-e", "cos'è l'iban", "healthy"),
    ("cashback-di-stato", "cashback di stato", "zombie"),
    ("lotteria-degli-scontrini", "lotteria degli scontrini", "zombie"),
    ("risparmiare-sulla-spesa", "come risparmiare sulla spesa", "striking"),
    ("regola-50-30-20", "regola 50 30 20", "healthy"),
    ("fondo-emergenza", "fondo di emergenza", "thin"),
    ("assicurazione-vita-conviene", "assicurazione vita conviene", "stale"),
    ("previdenza-complementare", "previdenza complementare", "striking"),
    ("fondo-pensione-deducibilita", "deducibilità fondo pensione", "decay"),
    ("pensione-anticipata-2025", "pensione anticipata", "stale"),
    ("quota-103", "quota 103", "zombie"),
    ("partita-iva-forfettaria", "regime forfettario", "decay"),
    ("regime-forfettario-limiti", "regime forfettario limiti", "cannibal_a"),
    ("forfettario-soglia-85000", "regime forfettario soglia", "cannibal_b"),
    ("f24-come-si-paga", "come pagare f24", "ctr"),
    ("imu-seconda-casa", "imu seconda casa", "healthy"),
    ("cedolare-secca", "cedolare secca", "striking"),
    ("bollo-auto-esenzione", "esenzione bollo auto", "stale"),
    ("criptovalute-tasse", "tasse criptovalute", "striking"),
    ("bitcoin-come-comprare", "come comprare bitcoin", "decay"),
    ("oro-investimento", "investire in oro", "thin"),
    ("inflazione-come-proteggersi", "proteggersi dall'inflazione", "stale"),
    ("bonus-bollette", "bonus bollette", "zombie"),
    ("detrazioni-spese-mediche", "detrazione spese mediche", "healthy"),
    ("superbonus-110", "superbonus 110", "zombie"),
    ("bonus-ristrutturazione", "bonus ristrutturazione", "ctr"),
    ("assegno-unico", "assegno unico", "decay"),
]

SUFFIXES = ["", " 2025", " come funziona", " requisiti", " guida", " cos'è", " conviene",
            " esempio", " calcolo", " opinioni", " online", " differenza", " scadenza", " quando"]


def _profile(arch: str, rng: np.random.Generator) -> dict:
    """Parametri di base per archetipo."""
    p = dict(impr=rng.integers(8000, 40000), pos=rng.uniform(2, 4.5), ctr_mult=1.0,
             trend=rng.uniform(-0.1, 0.15), age=rng.integers(60, 200), words=rng.integers(1500, 2600))
    if arch == "decay":
        p.update(trend=rng.uniform(-0.65, -0.35), age=rng.integers(400, 800), pos=rng.uniform(4, 8))
    elif arch == "striking":
        p.update(pos=rng.uniform(7, 15), impr=rng.integers(20000, 60000), age=rng.integers(250, 500),
                 words=rng.integers(1000, 1600))
    elif arch == "ctr":
        p.update(pos=rng.uniform(1.8, 4.5), ctr_mult=rng.uniform(0.25, 0.45))
    elif arch == "stale":
        p.update(age=rng.integers(800, 1400), trend=rng.uniform(-0.3, -0.1), pos=rng.uniform(5, 9))
    elif arch == "thin":
        p.update(words=rng.integers(350, 650), pos=rng.uniform(8, 16), impr=rng.integers(5000, 15000))
    elif arch.startswith("cannibal"):
        p.update(pos=rng.uniform(6, 14), impr=rng.integers(10000, 30000), age=rng.integers(300, 700))
    elif arch == "zombie":
        p.update(impr=rng.integers(20, 90), pos=rng.uniform(35, 60), trend=rng.uniform(-0.8, -0.5),
                 age=rng.integers(900, 1500), words=rng.integers(300, 600))
    return p


def _article_text(kw: str, words: int, rng: np.random.Generator) -> tuple[str, int]:
    sections = ["Cos'è", "Come funziona", "Requisiti", "Vantaggi e svantaggi", "Esempi pratici",
                "Domande frequenti", "Errori da evitare", "Conclusioni"]
    n = max(2, min(len(sections), words // 300))
    paras = []
    for s in sections[:n]:
        paras.append(f"## {s}: {kw}")
        paras.append(f"Paragrafo dimostrativo su {kw}. " * max(3, words // (n * 12)))
    return "\n".join(paras), n


def build(seed: int = 7, today: datetime | None = None) -> Dataset:
    rng = np.random.default_rng(seed)
    today = today or datetime.now()
    pages, queries, content, seo = [], [], [], []
    cannibal_kw: dict[str, str] = {}

    for i, (slug, kw, arch) in enumerate(ARTICLES):
        url = f"{DOMAIN}{slug}/"
        p = _profile(arch, rng)

        # query per pagina: keyword principale + varianti long tail
        n_q = rng.integers(8, 14)
        page_rows = []
        for j in range(n_q):
            q = kw + SUFFIXES[j]
            share = 0.45 if j == 0 else rng.uniform(0.02, 0.12)
            impr = max(5, int(p["impr"] * share))
            pos = max(1.0, p["pos"] + (0 if j == 0 else rng.uniform(-1.5, 9)))
            from .config import expected_ctr
            ctr = expected_ctr(pos) * p["ctr_mult"] * rng.uniform(0.8, 1.15)
            page_rows.append(dict(url=url, query=q, impressions=impr, position=round(pos, 1),
                                  clicks=int(impr * ctr)))
        # coppie cannibalizzate: la "b" si posiziona anche sulla keyword della "a"
        if arch == "cannibal_a":
            cannibal_kw["pending"] = kw
        if arch == "cannibal_b" and "pending" in cannibal_kw:
            a_kw = cannibal_kw.pop("pending")
            for s in SUFFIXES[:4]:
                impr = int(p["impr"] * rng.uniform(0.08, 0.2))
                pos = rng.uniform(8, 18)
                page_rows.append(dict(url=url, query=a_kw + s, impressions=impr, position=round(pos, 1),
                                      clicks=int(impr * 0.02)))
        q_df = pd.DataFrame(page_rows)
        q_df["ctr"] = q_df["clicks"] / q_df["impressions"]
        queries.append(q_df)

        clicks = int(q_df["clicks"].sum() * 1.15)  # le query anonimizzate non compaiono
        impressions = int(q_df["impressions"].sum() * 1.2)
        position = float(np.average(q_df["position"], weights=q_df["impressions"]))
        clicks_prev = int(clicks / (1 + p["trend"]))
        pages.append(dict(url=url, clicks=clicks, impressions=impressions,
                          ctr=clicks / impressions, position=round(position, 1),
                          clicks_prev=clicks_prev, impressions_prev=int(impressions / (1 + p["trend"] / 2)),
                          position_prev=round(position + p["trend"] * -4, 1)))

        text, h2 = _article_text(kw, int(p["words"]), rng)
        published = today - timedelta(days=int(p["age"]) + int(rng.integers(0, 400)))
        modified = today - timedelta(days=int(p["age"]))
        title = kw.capitalize() + (": guida completa" if arch != "ctr" else "")
        content.append(dict(
            url=url, title=title,
            meta_description=None if arch == "ctr" else f"Scopri tutto su {kw}: guida aggiornata con esempi.",
            h1=kw.capitalize(), h2_count=h2, word_count=int(p["words"]),
            published=published, modified=modified, internal_links=int(rng.integers(0, 12)), text=text,
        ))
        seo.append(dict(
            url=url, traffic=int(clicks / 3 * rng.uniform(0.7, 1.3)),
            traffic_prev=int(clicks_prev / 3 * rng.uniform(0.7, 1.3)),
            keywords=int(n_q * rng.uniform(3, 8)), top_keyword=kw,
            top_keyword_volume=int(p["impr"] * 0.6 / 3), top_keyword_position=int(round(p["pos"])),
            referring_domains=int(rng.integers(0, 40)), traffic_value=round(clicks / 3 * 0.4, 2),
        ))

    return Dataset(pages=pd.DataFrame(pages), queries=pd.concat(queries, ignore_index=True),
                   content=pd.DataFrame(content), seo=pd.DataFrame(seo))
