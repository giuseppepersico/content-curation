"""Generazione del brief di content curation con Claude.

Il modello riceve metriche, query reali dell'articolo e il testo attuale; se
abilitato, usa la ricerca web per analizzare la SERP della query principale e
individuare i sottotemi coperti dai competitor e assenti nell'articolo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import anthropic
import pandas as pd

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
MAX_ARTICLE_CHARS = 60_000

SYSTEM_PROMPT = """Sei un senior SEO content strategist. Il tuo compito è scrivere brief di \
content curation per articoli editoriali già pubblicati che devono posizionarsi meglio su Google.

Il brief è destinato a un redattore che deve aggiornare l'articolo. Deve essere concreto e \
azionabile: niente consigli generici ("migliora la qualità", "aggiungi keyword"), ma interventi \
specifici su questo articolo, motivati dai dati forniti.

Regole:
- Basa le raccomandazioni sulle query reali di Search Console fornite: indicano l'intento \
di ricerca effettivo. Le query in posizione 4-20 con molte impressioni sono le opportunità principali.
- Se hai a disposizione la ricerca web, analizza i primi risultati per la query principale e \
indica quali sottotemi, formati (tabelle, FAQ, step) e informazioni coprono loro e l'articolo no. \
Cita i competitor per dominio.
- Non suggerire di riempire il testo di keyword: proponi sezioni che rispondono all'intento.
- Se i dati indicano che l'articolo andrebbe accorpato a un altro o eliminato, dillo chiaramente.
- Scrivi in italiano (o nella lingua dell'articolo, se diversa), in Markdown."""

BRIEF_TEMPLATE = """## Diagnosi
(2-4 frasi: perché l'articolo sta perdendo o non sta raggiungendo posizioni, sulla base dei dati)

## Intento di ricerca e keyword
- Keyword principale e intento
- Keyword secondarie da presidiare (dalle query GSC), con la sezione in cui inserirle

## Gap rispetto alla SERP
(sottotemi, formati e informazioni presenti nei competitor e assenti qui)

## Interventi sul contenuto
(lista ordinata per impatto: sezioni da aggiungere, riscrivere, accorpare o eliminare, con gli H2/H3 proposti)

## Title, meta description e H1
- Title proposto (max 60 caratteri)
- Meta description proposta (max 155 caratteri)
- H1 proposto

## Linking interno e dati strutturati
(suggerimenti su link interni in ingresso/uscita e schema markup utili)

## Checklist per il redattore
(5-8 punti spuntabili)"""


@dataclass
class BriefResult:
    markdown: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None
    used_web_search: bool


def _fmt(v, pct=False, dec=1):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "n.d."
    if pct:
        return f"{v:+.0%}" if isinstance(v, (int, float)) else str(v)
    return f"{v:,.{dec}f}" if isinstance(v, float) else str(v)


def build_user_prompt(row: pd.Series, queries: pd.DataFrame, use_web: bool) -> str:
    metrics = f"""- URL: {row['url']}
- Title attuale: {_fmt(row.get('title'))}
- Meta description attuale: {_fmt(row.get('meta_description'))}
- H1: {_fmt(row.get('h1'))}
- Parole: {_fmt(row.get('word_count'))} · H2: {_fmt(row.get('h2_count'))} · Link interni nel testo: {_fmt(row.get('internal_links'))}
- Pubblicato: {_fmt(row.get('published'))} · Ultimo aggiornamento: {_fmt(row.get('modified'))} ({_fmt(row.get('days_since_update'))} giorni fa)
- GSC (periodo corrente): {_fmt(row.get('clicks'))} clic, {_fmt(row.get('impressions'))} impressioni, CTR {_fmt(row.get('ctr'))}, posizione media {_fmt(row.get('position'))}
- Variazione clic vs periodo precedente: {_fmt(row.get('clicks_change_pct'), pct=True)}
- Ahrefs/Semrush: traffico stimato {_fmt(row.get('traffic'))}, keyword posizionate {_fmt(row.get('keywords'))}, top keyword "{_fmt(row.get('top_keyword'))}" (vol. {_fmt(row.get('top_keyword_volume'))}, pos. {_fmt(row.get('top_keyword_position'))}), referring domains {_fmt(row.get('referring_domains'))}
- Query cannibalizzate con altri URL del sito: {_fmt(row.get('cannibal_queries'))}
- Priorità calcolata: {_fmt(row.get('priority'))}/100 · Segnale principale: {_fmt(row.get('main_signal'))}
- Azione suggerita dallo scoring: {_fmt(row.get('action'))} ({_fmt(row.get('reasons'))})"""

    if queries is not None and not queries.empty:
        q = queries[["query", "clicks", "impressions", "ctr", "position"]].copy()
        q["ctr"] = (q["ctr"] * 100).round(1)
        q["position"] = q["position"].round(1)
        header = "| query | clic | impressioni | CTR % | posizione |\n|---|---|---|---|---|"
        q_table = header + "".join(
            f"\n| {r.query} | {r.clicks:.0f} | {r.impressions:.0f} | {r.ctr} | {r.position} |"
            for r in q.itertuples())
    else:
        q_table = "(nessun dato query disponibile)"

    text = row.get("text")
    text = text if isinstance(text, str) and text.strip() else "(testo non disponibile: basati su title e query)"
    if len(text) > MAX_ARTICLE_CHARS:
        text = text[:MAX_ARTICLE_CHARS] + "\n\n[... testo troncato per lunghezza ...]"

    web = ("Usa la ricerca web per analizzare i primi risultati organici della query principale "
           "prima di scrivere la sezione 'Gap rispetto alla SERP'."
           if use_web else
           "La ricerca web non è disponibile: nella sezione 'Gap rispetto alla SERP' ragiona "
           "sui sottotemi che l'intento delle query richiede e che l'articolo non copre.")

    return f"""<metriche>
{metrics}
</metriche>

<query_search_console>
{q_table}
</query_search_console>

<articolo_attuale>
{text}
</articolo_attuale>

{web}

Scrivi il brief di content curation seguendo questa struttura:

{BRIEF_TEMPLATE}"""


def build_chat_prompt(row: pd.Series, queries: pd.DataFrame | None = None) -> str:
    """Prompt autosufficiente da incollare in Claude.ai (modalità senza chiave API)."""
    return (f"{SYSTEM_PROMPT}\n\n---\n\n{build_user_prompt(row, queries, use_web=True)}")


def generate_brief(row: pd.Series, queries: pd.DataFrame | None = None, use_web: bool = True,
                   effort: str = "high", api_key: str | None = None) -> BriefResult:
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    extra = {"tools": [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}]} if use_web else {}
    messages = [{"role": "user", "content": build_user_prompt(row, queries, use_web)}]

    total_in = total_out = 0
    for _ in range(5):  # la ricerca web può restituire pause_turn: si riprende il turno
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=32000,
            system=SYSTEM_PROMPT,
            messages=messages,
            **extra,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            # in caso di rifiuto dei classificatori di sicurezza, la richiesta viene
            # rieseguita lato server sul modello di fallback raccomandato
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            response = stream.get_final_message()
        total_in += response.usage.input_tokens
        total_out += response.usage.output_tokens
        if response.stop_reason != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": response.content}]

    if response.stop_reason == "refusal":
        raise RuntimeError("Il modello ha rifiutato la richiesta per questo articolo.")
    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if response.stop_reason == "max_tokens":
        text += "\n\n> ⚠️ Brief troncato: limite di lunghezza raggiunto."
    return BriefResult(
        markdown=text, model=response.model, input_tokens=total_in, output_tokens=total_out,
        stop_reason=response.stop_reason, used_web_search=use_web,
    )
