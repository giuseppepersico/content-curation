# SEO Content Curation

Tool che analizza gli articoli editoriali di un sito e **seleziona quelli da sottoporre a content
curation** per posizionarsi meglio: per ognuno calcola una priorità, consiglia l'azione
(refresh, espansione, title/meta, accorpamento, pruning) e genera con Claude un **brief
operativo per la redazione**.

## Avvio rapido

```bash
pip install -r requirements.txt
cp .env.example .env        # inserisci le chiavi che hai
streamlit run app.py
```

Nella dashboard scegli **Demo** per provarla subito con un blog di esempio (50 articoli).

Da riga di comando (utile per job schedulati):

```bash
python cli.py --demo -o report.xlsx
python cli.py --pages gsc_pagine.csv --pages-prev gsc_pagine_prev.csv \
    --queries gsc_query.csv --content screaming_frog.csv --filter /blog/ -o report.xlsx
python cli.py --demo --briefs 3          # + brief AI per i primi 3 candidati
```

## Fonti dati

Ogni fonte è facoltativa: più fonti colleghi, più segnali vengono calcolati.

| Fonte | Cosa fornisce | Come collegarla |
|---|---|---|
| **Google Search Console** | clic, impressioni, CTR, posizione per pagina e per pagina+query, con confronto tra periodi | API con service account (aggiungi la sua email come utente della proprietà), oppure export CSV |
| **Contenuti** | title, meta description, H1/H2, parole, date di pubblicazione e aggiornamento, testo | crawling da sitemap XML, WordPress REST API, oppure export Screaming Frog o CMS |
| **Ahrefs** | traffico stimato e trend, keyword, top keyword, referring domains | API v3 `site-explorer/top-pages`, oppure export CSV |
| **Semrush** | keyword organiche aggregate per URL | Analytics API `domain_organic`, oppure export CSV |

Gli import CSV riconoscono le intestazioni in italiano e in inglese (es. `Pagine principali`,
`Clic`, `Impressioni`, `Address`, `Word Count`) e i numeri in formato italiano (`1.234`, `3,5%`).
Gli URL vengono normalizzati (querystring rimossa, slash finale) per unire le fonti.

> Il report di GSC con **pagina + query** insieme non è esportabile dall'interfaccia web:
> usa la connessione API, Looker Studio o il bulk export su BigQuery. Senza questo dato,
> striking distance e cannibalizzazione si basano solo sui dati di pagina.

## Come vengono selezionati gli articoli

Sei segnali, ognuno normalizzato tra 0 e 1:

| Segnale | Logica |
|---|---|
| Decadimento | calo % dei clic rispetto al periodo di confronto (−50% = segnale pieno) |
| Striking distance | quota di clic non sfruttati sulle query in posizione 4-20, se portate alla posizione obiettivo (3) |
| CTR sotto le attese | scarto tra CTR reale e CTR atteso per quella posizione (solo top 10) |
| Contenuto datato | giorni dall'ultimo aggiornamento (da 180 a 730 giorni) |
| Contenuto scarno | parole sotto la soglia minima (900) |
| Cannibalizzazione | impressioni su query per cui un altro URL del sito si posiziona meglio |

**Priorità (0-100)** = 60% intensità pesata dei segnali + 40% potenziale. Il potenziale è il
percentile dei **clic recuperabili**: clic persi, gap di CTR e guadagno sulle query in striking
distance. Un articolo con molti problemi ma senza domanda di ricerca non scavalca quindi uno
con un'opportunità concreta. Pesi e soglie si regolano dalla sidebar
(default in `curation/config.py`).

**Azione consigliata**, in ordine di precedenza:

1. Cannibalizzazione → accorpare o differenziare.
2. Visibilità quasi nulla + datato + scarno → pruning (noindex o redirect 301).
3. CTR basso in top 6 → title e meta description.
4. Calo di traffico o contenuto datato → refresh.
5. Striking distance o testo scarno → espansione.

Sono candidati gli articoli con priorità ≥ 35 (soglia regolabile) e un'azione diversa da
"Monitorare", più tutti i candidati al pruning.

## Brief AI

Il brief viene generato con Claude (`claude-opus-5-5`, modificabile con `CLAUDE_MODEL`). Il
modello riceve metriche, query GSC reali e testo dell'articolo. Se l'opzione è attiva, usa
anche la **ricerca web** per analizzare la SERP della query principale e individuare i gap
rispetto ai competitor.

Il brief contiene:
- diagnosi;
- intento di ricerca e keyword;
- gap rispetto alla SERP;
- interventi sul contenuto, con gli H2 proposti;
- title, meta description e H1 proposti;
- linking interno;
- checklist per il redattore.

Puoi generarlo per un singolo articolo oppure in blocco, con export ZIP dei file Markdown.

Note:
- Ogni brief è una chiamata API a pagamento. La profondità di analisi (`effort`) si regola
  dalla sidebar.
- La richiesta usa il fallback lato server (`fallbacks: "default"`): se i classificatori di
  sicurezza la rifiutano, viene rieseguita automaticamente sul modello di fallback raccomandato.

## Struttura

```
app.py                   dashboard Streamlit
cli.py                   esecuzione da riga di comando
curation/
  config.py              pesi, soglie, curva CTR attesa
  schema.py              schema dati canonico + normalizzazione URL
  scoring.py             segnali, priorità, azioni consigliate
  pipeline.py            unione delle fonti ed esecuzione
  brief.py               generazione brief con Claude
  sample_data.py         dataset demo
  sources/
    csv_import.py        import da CSV/Excel
    gsc.py               Google Search Console API
    crawler.py           sitemap, crawling, WordPress REST API
    seo_tools.py         Ahrefs API v3, Semrush API
tests/                   pytest
```

## Test

```bash
python -m pytest -q
```
