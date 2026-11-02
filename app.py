"""Dashboard SEO Content Curation.

Avvio:  streamlit run app.py
"""
from __future__ import annotations

import io
import os
import tempfile
import zipfile
from dataclasses import replace

import pandas as pd
import plotly.express as px
import streamlit as st

from curation import sample_data
from curation.config import SIGNALS, ScoringConfig, Thresholds, Weights
from curation.pipeline import Dataset, queries_for, run
from curation.scoring import ACTIONS, cannibalization_groups
from curation.sources import csv_import

try:  # .env opzionale
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

st.set_page_config(page_title="SEO Content Curation", page_icon="📝", layout="wide")

# Palette (riferimento dataviz): blu categoriale slot 1 + rampa sequenziale blu
BLUE = "#2a78d6"
SEQ_BLUE = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"]

ss = st.session_state
ss.setdefault("dataset", None)
ss.setdefault("source_label", None)
ss.setdefault("briefs", {})


# =========================================================================== sidebar
def sidebar() -> tuple[ScoringConfig, str | None]:
    st.sidebar.header("⚙️ Impostazioni")
    url_filter = st.sidebar.text_input(
        "Filtro URL (regex)", value="", placeholder="/blog/",
        help="Analizza solo gli URL che contengono questo pattern (es. la sezione editoriale).") or None

    with st.sidebar.expander("Pesi dei segnali", expanded=False):
        w = Weights()
        weights = Weights(**{k: st.slider(label, 0.0, 2.0, getattr(w, k), 0.1, key=f"w_{k}")
                             for k, label in SIGNALS.items()})
        potential_share = st.slider(
            "Peso del potenziale di traffico", 0.0, 1.0, 0.4, 0.05,
            help="Quanto conta il volume di clic recuperabili rispetto all'intensità dei segnali.")

    with st.sidebar.expander("Soglie", expanded=False):
        t = Thresholds()
        th = replace(
            t,
            candidate_min_priority=st.slider("Priorità minima per essere candidato", 0, 100,
                                             int(t.candidate_min_priority)),
            decay_full=st.slider("Calo clic che vale segnale pieno", 0.1, 1.0, t.decay_full, 0.05,
                                 format="%.2f"),
            min_clicks_prev=st.number_input("Clic minimi nel periodo precedente", 0, 10000, t.min_clicks_prev),
            striking_min_pos=st.number_input("Striking distance: da posizione", 1.0, 50.0, t.striking_min_pos),
            striking_max_pos=st.number_input("Striking distance: a posizione", 1.0, 100.0, t.striking_max_pos),
            target_position=st.number_input("Posizione obiettivo (stima potenziale)", 1, 10, t.target_position),
            min_impressions_ctr=st.number_input("Impressioni minime per CTR gap", 0, 100000, t.min_impressions_ctr),
            stale_start_days=st.number_input("Datato dopo (giorni)", 0, 3650, t.stale_start_days),
            stale_full_days=st.number_input("Molto datato dopo (giorni)", 1, 3650, t.stale_full_days),
            min_words=st.number_input("Parole minime", 0, 10000, t.min_words),
            cannibal_min_impressions=st.number_input("Cannibalizzazione: impressioni min. per query",
                                                     0, 100000, t.cannibal_min_impressions),
        )

    with st.sidebar.expander("Brief AI (Claude)", expanded=False):
        key = st.text_input("ANTHROPIC_API_KEY", type="password",
                            help="Facoltativa. Senza chiave il tool prepara il prompt da incollare in "
                                 "Claude.ai. Lascia vuoto se la variabile d'ambiente è già impostata.")
        if key:
            ss["anthropic_key"] = key
        ss["use_web"] = st.toggle("Analizza la SERP con ricerca web", value=True)
        ss["effort"] = st.select_slider("Profondità di analisi", ["low", "medium", "high", "xhigh"], "high")

    return ScoringConfig(weights=weights, thresholds=th, potential_share=potential_share), url_filter


# =========================================================================== data loading
def data_loader():
    st.subheader("1 · Fonti dati")
    mode = st.radio("Origine", ["Demo", "Carica file", "Connessioni API"], horizontal=True,
                    label_visibility="collapsed")

    if mode == "Demo":
        st.caption("Blog dimostrativo di finanza personale (50 articoli) con casi tipici: "
                   "decadimento, striking distance, CTR basso, contenuti datati o scarni, cannibalizzazione.")
        if st.button("Carica dataset demo", type="primary"):
            ss.dataset, ss.source_label, ss.briefs = sample_data.build(), "Demo", {}
            st.rerun()

    elif mode == "Carica file":
        c1, c2 = st.columns(2)
        with c1:
            f_pages = st.file_uploader("GSC · Pagine (periodo corrente)", type=["csv", "xlsx"])
            f_prev = st.file_uploader("GSC · Pagine (periodo di confronto)", type=["csv", "xlsx"])
            f_queries = st.file_uploader("GSC · Pagina + query", type=["csv", "xlsx"],
                                         help="Colonne: page/url, query, clicks, impressions, ctr, position")
        with c2:
            f_content = st.file_uploader("Contenuti (Screaming Frog / CMS)", type=["csv", "xlsx"],
                                         help="Colonne riconosciute: address/url, title, meta description, "
                                              "h1, word count, date published/modified…")
            f_seo = st.file_uploader("Ahrefs / Semrush · Top pages", type=["csv", "xlsx"])
            crawl_missing = st.checkbox("Scarica il testo degli articoli dal sito (per i brief AI)", value=False)
        if st.button("Importa file", type="primary"):
            ds = Dataset()
            try:
                if f_pages:
                    ds.pages = csv_import.load_pages(f_pages.getvalue(), f_prev.getvalue() if f_prev else None,
                                                     f_pages.name, f_prev.name if f_prev else None)
                if f_queries:
                    ds.queries = csv_import.load_queries(f_queries.getvalue(), f_queries.name)
                if f_content:
                    ds.content = csv_import.load_content(f_content.getvalue(), f_content.name)
                if f_seo:
                    ds.seo = csv_import.load_seo_tool(f_seo.getvalue(), f_seo.name)
                if crawl_missing:
                    from curation.sources import crawler
                    urls = pd.concat([ds.pages["url"], ds.content["url"], ds.seo["url"]]).dropna().unique()
                    with st.spinner(f"Crawling di {len(urls)} URL…"):
                        crawled = crawler.crawl(list(urls))
                    ds.content = crawled if ds.content.empty else ds.content.drop(columns=["text"]).merge(
                        crawled[["url", "text"]], on="url", how="left")
                ss.dataset, ss.source_label, ss.briefs = ds, "File caricati", {}
            except Exception as exc:  # noqa: BLE001
                st.error(f"Errore nell'import: {exc}")

    else:
        api_loader()

    if ss.dataset is not None:
        counts = ss.dataset.sources()
        st.success(f"Fonte: **{ss.source_label}** · pagine GSC {counts['pages']} · righe query "
                   f"{counts['queries']} · contenuti {counts['content']} · dati SEO tool {counts['seo']}")


def api_loader():
    tabs = st.tabs(["Google Search Console", "Contenuti (sitemap / WordPress)", "Ahrefs", "Semrush"])
    with tabs[0]:
        gsc_site = st.text_input("Proprietà", placeholder="sc-domain:esempio.it oppure https://www.esempio.it/")
        gsc_json = st.file_uploader("JSON del service account", type=["json"])
        gsc_path = st.text_input("…oppure percorso del file", value=os.getenv("GSC_SERVICE_ACCOUNT_FILE", ""))
        c1, c2 = st.columns(2)
        gsc_days = c1.selectbox("Periodo", [28, 90, 180], index=1, format_func=lambda d: f"Ultimi {d} giorni")
        gsc_yoy = c2.toggle("Confronta con l'anno precedente (YoY)",
                            help="Altrimenti confronta con il periodo immediatamente precedente.")
    with tabs[1]:
        content_mode = st.radio("Metodo", ["Sitemap + crawling", "WordPress REST API", "Nessuno"], horizontal=True)
        sitemap = st.text_input("URL sitemap o sito WordPress", placeholder="https://www.esempio.it/sitemap.xml")
        crawl_limit = st.number_input("Numero massimo di articoli", 10, 5000, 300)
    with tabs[2]:
        ahrefs_key = st.text_input("API key Ahrefs", type="password", value=os.getenv("AHREFS_API_KEY", ""))
        ahrefs_target = st.text_input("Target", placeholder="esempio.it/blog/")
        ahrefs_country = st.text_input("Paese", value="IT", max_chars=2)
    with tabs[3]:
        semrush_key = st.text_input("API key Semrush", type="password", value=os.getenv("SEMRUSH_API_KEY", ""))
        semrush_domain = st.text_input("Dominio", placeholder="esempio.it")
        semrush_db = st.text_input("Database", value="it")

    url_filter = st.text_input("Filtro URL applicato a tutte le fonti (regex)", placeholder="/blog/")
    if not st.button("Scarica dati", type="primary"):
        return

    ds, errors = Dataset(), []
    with st.status("Raccolta dati in corso…", expanded=True) as status:
        if gsc_site and (gsc_json or gsc_path):
            st.write("Google Search Console…")
            try:
                from curation.sources import gsc
                path = gsc_path
                if gsc_json:
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
                    tmp.write(gsc_json.getvalue()); tmp.close()
                    path = tmp.name
                ds.pages, ds.queries = gsc.fetch(path, gsc_site, gsc_days, gsc_yoy,
                                                 page_filter=url_filter.strip("^$") or None)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"GSC: {exc}")
        if sitemap and content_mode != "Nessuno":
            st.write("Contenuti degli articoli…")
            try:
                from curation.sources import crawler
                ds.content = (crawler.from_sitemap(sitemap, url_filter or None, int(crawl_limit))
                              if content_mode == "Sitemap + crawling"
                              else crawler.from_wordpress(sitemap, int(crawl_limit)))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Contenuti: {exc}")
        if ahrefs_key and ahrefs_target:
            st.write("Ahrefs…")
            try:
                from curation.sources import seo_tools
                ds.seo = seo_tools.fetch_ahrefs(ahrefs_key, ahrefs_target, ahrefs_country)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Ahrefs: {exc}")
        elif semrush_key and semrush_domain:
            st.write("Semrush…")
            try:
                from curation.sources import seo_tools
                ds.seo = seo_tools.fetch_semrush(semrush_key, semrush_domain, semrush_db, url_filter or None)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Semrush: {exc}")
        status.update(label="Raccolta completata" if not errors else "Raccolta completata con errori",
                      state="complete" if not errors else "error")
    for e in errors:
        st.error(e)
    if any(ds.sources().values()):
        ss.dataset, ss.source_label, ss.briefs = ds, "API", {}


# =========================================================================== views
def it_num(v) -> str:
    """Numero intero in formato italiano (1.234)."""
    return "n.d." if v is None or pd.isna(v) else f"{v:,.0f}".replace(",", ".")


def fmt_table(df: pd.DataFrame) -> pd.DataFrame:
    cols = {
        "priority": "Priorità", "url": "URL", "action": "Azione consigliata", "main_signal": "Segnale principale",
        "recoverable_clicks": "Clic recuperabili", "clicks": "Clic", "clicks_change_pct": "Δ clic",
        "impressions": "Impressioni", "ctr": "CTR", "position": "Posizione", "word_count": "Parole",
        "days_since_update": "Giorni dall'aggiornamento", "top_query": "Query principale",
        "reasons": "Motivazioni", "secondary_actions": "Azioni secondarie",
    }
    return df[[c for c in cols if c in df.columns]].rename(columns=cols)


COLUMN_CONFIG = {
    "Priorità": st.column_config.ProgressColumn("Priorità", min_value=0, max_value=100, format="%.0f"),
    "URL": st.column_config.LinkColumn("URL", width="medium", display_text=r"https?://[^/]+(/.*)"),
    "Δ clic": st.column_config.NumberColumn(format="percent"),
    "CTR": st.column_config.NumberColumn(format="percent"),
    "Posizione": st.column_config.NumberColumn(format="%.1f"),
    "Clic recuperabili": st.column_config.NumberColumn(format="%d"),
}


def overview(df: pd.DataFrame):
    cand = df[df["is_candidate"]]
    c = st.columns(4)
    c[0].metric("Articoli analizzati", len(df))
    c[1].metric("Da sottoporre a curation", len(cand), f"{len(cand) / max(1, len(df)):.0%} del totale",
                delta_color="off", delta_arrow="off")
    c[2].metric("Clic recuperabili stimati", it_num(cand["recoverable_clicks"].sum()),
                help="Per il periodo analizzato: clic persi, gap di CTR e guadagno portando le query "
                     "in striking distance alla posizione obiettivo.")
    c[3].metric("Query cannibalizzate", int(df["cannibal_queries"].fillna(0).sum()))

    left, right = st.columns(2)
    with left:
        st.markdown("**Candidati per azione consigliata**")
        by_action = cand["action"].value_counts().rename_axis("Azione").reset_index(name="Articoli")
        fig = px.bar(by_action.sort_values("Articoli"), x="Articoli", y="Azione", orientation="h",
                     text="Articoli", color_discrete_sequence=[BLUE])
        fig.update_traces(textposition="outside", cliponaxis=False,
                          hovertemplate="%{y}<br><b>%{x}</b> articoli<extra></extra>")
        fig.update_layout(height=320, margin=dict(l=0, r=30, t=10, b=0), bargap=0.35,
                          xaxis=dict(showgrid=False, visible=False), yaxis_title=None)
        st.plotly_chart(fig, width="stretch", theme="streamlit")
    with right:
        st.markdown("**Posizione media e impressioni** · colore = priorità")
        plot = df.dropna(subset=["position", "impressions"]).copy()
        plot["slug"] = plot["url"].str.rstrip("/").str.rsplit("/", n=1).str[-1]
        fig = px.scatter(plot, x="position", y="impressions", color="priority", log_y=True,
                         color_continuous_scale=SEQ_BLUE, range_color=(0, 100),
                         custom_data=["slug", "priority", "action"],
                         labels={"position": "Posizione media", "impressions": "Impressioni (scala log)",
                                 "priority": "Priorità"})
        fig.update_traces(marker=dict(size=10, line=dict(width=1, color="rgba(255,255,255,0.8)")),
                          hovertemplate="<b>%{customdata[0]}</b><br>Posizione %{x:.1f} · "
                                        "%{y:,} impressioni<br>Priorità %{customdata[1]:.0f}<br>"
                                        "%{customdata[2]}<extra></extra>")
        fig.add_vrect(x0=4, x1=20, fillcolor=BLUE, opacity=0.06, line_width=0,
                      annotation_text="striking distance", annotation_position="top left")
        fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch", theme="streamlit")


def candidates_view(df: pd.DataFrame):
    c1, c2, c3 = st.columns([2, 2, 1])
    actions = c1.multiselect("Azione", [a for a in ACTIONS.values() if a in df["action"].unique()])
    signals = c2.multiselect("Segnale principale", sorted(df["main_signal"].dropna().unique()))
    only = c3.toggle("Solo candidati", value=True)
    view = df[df["is_candidate"]] if only else df
    if actions:
        view = view[view["action"].isin(actions)]
    if signals:
        view = view[view["main_signal"].isin(signals)]

    st.dataframe(fmt_table(view), width="stretch", hide_index=True,
                 column_config=COLUMN_CONFIG, height=520)

    export = view.drop(columns=[c for c in ("text", "h2_list") if c in view.columns])
    xls = io.BytesIO()
    with pd.ExcelWriter(xls, engine="openpyxl") as w:
        fmt_table(export).to_excel(w, sheet_name="Candidati", index=False)
        export.to_excel(w, sheet_name="Dati completi", index=False)
    d1, d2, _ = st.columns([1, 1, 4])
    d1.download_button("⬇️ Excel", xls.getvalue(), "content_curation.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    d2.download_button("⬇️ CSV", export.to_csv(index=False).encode("utf-8"), "content_curation.csv", "text/csv")


def has_api_key() -> bool:
    return bool(ss.get("anthropic_key") or os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def slug(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def chat_prompt_box(row: pd.Series, ds: Dataset):
    """Modalità senza chiave API: prompt completo da incollare in Claude.ai."""
    from curation.brief import build_chat_prompt
    prompt = build_chat_prompt(row, queries_for(ds, row["url"]))
    st.info("Nessuna chiave API Anthropic configurata. Copia il prompt qui sotto (icona in alto a destra "
            "del riquadro) e incollalo in una nuova chat su [Claude.ai](https://claude.ai), con la "
            "**ricerca web attiva** per l'analisi della SERP.")
    st.download_button("⬇️ Scarica prompt (.md)", prompt.encode("utf-8"),
                       f"prompt-{slug(row['url'])}.md", "text/markdown")
    with st.container(height=420):
        st.code(prompt, language="markdown", wrap_lines=True)


def _generate(row: pd.Series, ds: Dataset):
    import anthropic

    from curation.brief import generate_brief
    if not has_api_key():
        raise RuntimeError("chiave API mancante: inseriscila in sidebar → «Brief AI (Claude)» "
                           "oppure imposta ANTHROPIC_API_KEY nel file .env.")
    try:
        return _call(generate_brief, row, ds)
    except anthropic.AuthenticationError:
        raise RuntimeError("chiave API Anthropic non valida.") from None
    except anthropic.RateLimitError:
        raise RuntimeError("limite di richieste raggiunto: riprova tra qualche istante.") from None
    except anthropic.APIConnectionError:
        raise RuntimeError("impossibile contattare l'API Anthropic (rete).") from None


def _call(generate_brief, row: pd.Series, ds: Dataset):
    return generate_brief(row, queries_for(ds, row["url"]), use_web=ss.get("use_web", True),
                          effort=ss.get("effort", "high"), api_key=ss.get("anthropic_key"))


def detail_view(df: pd.DataFrame, ds: Dataset):
    options = df["url"].tolist()
    if not options:
        st.info("Nessun articolo.")
        return
    url = st.selectbox("Articolo", options,
                       format_func=lambda u: f"{df.loc[df.url == u, 'priority'].iloc[0]:.0f} · {u}")
    row = df[df["url"] == url].iloc[0]

    st.markdown(f"### {row.get('title') or url}")
    st.markdown(f"**Azione consigliata:** {row['action']}"
                + (f" · poi: {row['secondary_actions']}" if row["secondary_actions"] else ""))
    if row["reasons"]:
        st.caption("Perché: " + row["reasons"])

    m = st.columns(6)
    m[0].metric("Priorità", f"{row['priority']:.0f}")
    m[1].metric("Clic", it_num(row.get("clicks")),
                f"{row['clicks_change_pct']:+.0%}" if pd.notna(row.get("clicks_change_pct")) else None)
    m[2].metric("Posizione", f"{row['position']:.1f}" if pd.notna(row.get("position")) else "n.d.")
    m[3].metric("CTR", f"{row['ctr']:.1%}" if pd.notna(row.get("ctr")) else "n.d.",
                f"atteso {row['expected_ctr']:.1%}" if pd.notna(row.get("expected_ctr")) else None,
                delta_color="off", delta_arrow="off")
    m[4].metric("Parole", f"{row['word_count']:.0f}" if pd.notna(row.get("word_count")) else "n.d.")
    m[5].metric("Ultimo aggiornamento", f"{row['days_since_update']:.0f} gg fa"
                if pd.notna(row.get("days_since_update")) else "n.d.")

    left, right = st.columns([1, 1.4])
    with left:
        st.markdown("**Intensità dei segnali**")
        sig = pd.DataFrame({"Segnale": list(SIGNALS.values()),
                            "Valore": [row[f"sig_{k}"] for k in SIGNALS]})
        fig = px.bar(sig, x="Valore", y="Segnale", orientation="h", range_x=(0, 1),
                     color_discrete_sequence=[BLUE])
        fig.update_traces(hovertemplate="%{y}: <b>%{x:.0%}</b><extra></extra>")
        fig.update_layout(height=260, margin=dict(l=0, r=0, t=0, b=0), yaxis_title=None,
                          xaxis=dict(tickformat=".0%", title=None), bargap=0.4)
        fig.update_yaxes(categoryorder="array", categoryarray=list(SIGNALS.values())[::-1])
        st.plotly_chart(fig, width="stretch", theme="streamlit")
    with right:
        st.markdown("**Query da Search Console**")
        q = queries_for(ds, url)
        if q.empty:
            st.caption("Nessun dato query per questo URL.")
        else:
            q = q[["query", "clicks", "impressions", "ctr", "position"]].rename(columns={
                "query": "Query", "clicks": "Clic", "impressions": "Impressioni", "ctr": "CTR",
                "position": "Posizione"})
            st.dataframe(q, hide_index=True, width="stretch", height=260, column_config={
                "CTR": st.column_config.NumberColumn(format="percent"),
                "Posizione": st.column_config.NumberColumn(format="%.1f")})

    st.divider()
    st.markdown("#### ✨ Brief di content curation")
    if url in ss.briefs:
        b = ss.briefs[url]
        st.markdown(b.markdown)
        st.caption(f"{b.model} · {b.input_tokens:,} token in / {b.output_tokens:,} out"
                   + (" · SERP analizzata con ricerca web" if b.used_web_search else ""))
        st.download_button("⬇️ Scarica brief (.md)", b.markdown.encode("utf-8"),
                           f"brief-{url.rstrip('/').rsplit('/', 1)[-1]}.md", "text/markdown")
    if not has_api_key():
        chat_prompt_box(row, ds)
        return
    label = "Rigenera brief" if url in ss.briefs else "Genera brief con Claude"
    if st.button(label, type="primary"):
        with st.spinner("Claude sta analizzando articolo, query e SERP… (può richiedere 1-2 minuti)"):
            try:
                ss.briefs[url] = _generate(row, ds)
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Errore nella generazione del brief: {exc}")


def bulk_briefs(df: pd.DataFrame, ds: Dataset):
    cand = df[df["is_candidate"] & (df["action"] != ACTIONS["prune"])]
    if not has_api_key():
        st.markdown("Senza chiave API puoi scaricare i **prompt** per i primi N candidati: un file per "
                    "articolo da incollare in una chat di [Claude.ai](https://claude.ai) con la ricerca web attiva.")
        n = st.number_input("Numero di articoli", 1, max(1, len(cand)), min(10, max(1, len(cand))))
        from curation.brief import build_chat_prompt
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for _, r in cand.head(int(n)).iterrows():
                z.writestr(f"{int(r['priority']):03d}-prompt-{slug(r['url'])}.md",
                           build_chat_prompt(r, queries_for(ds, r["url"])))
        st.download_button(f"⬇️ Scarica {int(n)} prompt (ZIP)", buf.getvalue(), "prompt_brief.zip",
                           "application/zip", type="primary")
        return
    st.markdown("Genera i brief per i primi N candidati in ordine di priorità ed esportali in un archivio ZIP.")
    n = st.number_input("Numero di articoli", 1, max(1, len(cand)), min(5, max(1, len(cand))))
    if st.button("Genera brief in blocco", type="primary"):
        bar = st.progress(0.0)
        for i, (_, row) in enumerate(cand.head(int(n)).iterrows()):
            bar.progress(i / n, text=f"{i + 1}/{n} · {row['url']}")
            if row["url"] not in ss.briefs:
                try:
                    ss.briefs[row["url"]] = _generate(row, ds)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"{row['url']}: {exc}")
        bar.progress(1.0, text="Completato")
    if ss.briefs:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for u, b in ss.briefs.items():
                z.writestr(f"brief-{u.rstrip('/').rsplit('/', 1)[-1]}.md", f"# {u}\n\n{b.markdown}")
        st.download_button(f"⬇️ Scarica {len(ss.briefs)} brief (ZIP)", buf.getvalue(), "brief_curation.zip",
                           "application/zip")


def cannibal_view(ds: Dataset, cfg: ScoringConfig, url_filter: str | None):
    q = ds.queries
    if url_filter and not q.empty:
        q = q[q["url"].str.contains(url_filter, regex=True, na=False)]
    g = cannibalization_groups(q, cfg)
    if g.empty:
        st.info("Nessuna query contesa tra più URL con le soglie attuali (servono i dati pagina+query di GSC).")
        return
    st.caption(f"{g['query'].nunique()} query su cui si posizionano più articoli del sito. "
               "Valuta se accorpare i contenuti (redirect 301 verso il più forte) o differenziarne l'intento.")
    st.dataframe(g.rename(columns={"query": "Query", "url": "URL", "clicks": "Clic",
                                   "impressions": "Impressioni", "position": "Posizione"}),
                 hide_index=True, width="stretch", height=520,
                 column_config={"URL": st.column_config.LinkColumn(display_text=r"https?://[^/]+(/.*)"),
                                "Posizione": st.column_config.NumberColumn(format="%.1f")})


def method_view():
    st.markdown("""
#### Come vengono selezionati gli articoli

Per ogni URL il tool calcola sei **segnali** normalizzati tra 0 e 1:

| Segnale | Come si calcola | Fonte |
|---|---|---|
| **Decadimento** | calo % dei clic rispetto al periodo di confronto (−50% = segnale pieno) | GSC, o in alternativa Ahrefs |
| **Striking distance** | quota di clic non sfruttati sulle query in posizione 4-20, se portate alla posizione obiettivo | GSC pagina+query |
| **CTR sotto le attese** | scarto tra CTR reale e CTR atteso per quella posizione (solo top 10) | GSC |
| **Contenuto datato** | giorni dall'ultimo aggiornamento (da 180 a 730 giorni) | CMS / sitemap |
| **Contenuto scarno** | parole sotto la soglia minima | CMS / crawling |
| **Cannibalizzazione** | impressioni su query per cui un altro URL del sito si posiziona meglio | GSC pagina+query |

La **priorità (0-100)** combina l'intensità pesata dei segnali con il **potenziale**:
i clic recuperabili stimati (clic persi + gap di CTR + guadagno sulle query in striking
distance), espressi come percentile sul portafoglio. Pesi e soglie si regolano dalla sidebar.

L'**azione consigliata** segue regole esplicite: cannibalizzazione → accorpare/differenziare;
nessuna visibilità + datato + scarno → pruning; CTR basso in top 6 → title e meta;
calo o contenuto datato → refresh; striking distance o testo scarno → espansione.

Il **brief AI** passa a Claude metriche, query reali e testo dell'articolo e, se attivo,
gli fa analizzare la SERP con la ricerca web per individuare i gap rispetto ai competitor.
""")


# =========================================================================== main
def main():
    cfg, url_filter = sidebar()
    st.title("📝 SEO Content Curation")
    st.caption("Individua gli articoli editoriali da aggiornare, espandere, accorpare o eliminare "
               "per posizionarsi meglio, e genera i brief per la redazione.")

    with st.expander("Fonti dati", expanded=ss.dataset is None):
        data_loader()
    if ss.dataset is None:
        st.info("Carica il dataset demo o collega le tue fonti per iniziare.")
        return

    ds: Dataset = ss.dataset
    try:
        df = run(ds, cfg, url_filter)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Errore nel calcolo dello scoring: {exc}")
        st.stop()
    if df.empty:
        st.warning("Nessun URL corrisponde al filtro.")
        return

    tabs = st.tabs(["Panoramica", "Candidati", "Dettaglio e brief AI", "Brief in blocco",
                    "Cannibalizzazione", "Metodo"])
    with tabs[0]:
        overview(df)
    with tabs[1]:
        candidates_view(df)
    with tabs[2]:
        detail_view(df, ds)
    with tabs[3]:
        bulk_briefs(df, ds)
    with tabs[4]:
        cannibal_view(ds, cfg, url_filter)
    with tabs[5]:
        method_view()


main()
