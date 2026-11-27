# Dashboard front-end

The public page of fuel-price-lakehouse: weekly fuel prices in Brazil (ANP
survey, Banco Central dollar and IPCA) and the data quality report of the run
that produced them. A zero-build static folder: plain HTML, CSS and JavaScript
ES modules. Charts use Observable Plot and D3, vendored with their licences in
`vendor/` together with the fonts; the page makes no request to any other host.

## Run it locally

The page reads its data with `fetch`, so it needs an HTTP server (a `file://`
URL cannot read the JSON files):

```sh
cd build/site                     # or any folder laid out as below
python3 -m http.server 8000       # then open http://127.0.0.1:8000/
```

## Layout

The pipeline's site builder copies this folder as it is and writes the data
next to it. The front-end loads `data/<file>.json` and links
`downloads/<file>`, both relative to `index.html`, and depends on nothing
outside its own folder.

```
index.html  styles.css  js/  vendor/  README.md                 <- this folder
data/*.json                                                       <- written by the builder
downloads/*.parquet, downloads/*.csv                              <- written by the builder
.nojekyll                                                         <- written by the builder
```

## Data files

Their shapes are checked by the site builder before anything is written
(`src/fuel_price_lakehouse/site/contract.py`). Every series is aligned with `weeks`
(every Sunday from the first survey week to the latest, contiguous); `null`
means "not surveyed or suppressed" and is drawn as a gap, never as zero.

| File | Needed | Contents | If it does not load |
| --- | --- | --- | --- |
| `meta.json` | required | run id, latest week, IPCA reference month, `products`, `states`, `regions`, `totals`, `downloads`, `repository` | error page |
| `national.json` | required | `weeks`, `usd_brl`, `ipca_deflator`, `ipca_provisional`, per product `mean, median, p10, p90, mean_real, n_stations, n_states` | error page |
| `states.json` | optional | per product and state `mean, median, p10, p90, n_stations` | state map, ranking, trajectories, state comparisons show a notice |
| `regions.json` | optional | same shape, keyed by region code | regions unavailable in the explorer |
| `flags.json` | optional | per product, `white_flag` / `branded`: `mean, median, n_stations` | brands section shows a notice |
| `events.json` | optional | `[{date, title_pt, title_en, detail_pt, detail_en, fuels, source_url}]` | no event markers |
| `quality.json` | optional | `run, status, freshness, funnel, rules, files, checks, history` | quality section and quality chip show a notice |

A file that fails, or comes in an unexpected shape, is named in a banner under
the navigation; the rest of the page keeps working. A product, state or region
absent from the files is simply not offered. Texts the pipeline writes in
English only (check `observed` / `expected` / `detail`, table descriptions in
`meta.downloads`) are shown as they are, marked `lang="en"` and labelled as
English on the Portuguese page.

## Sections

| `#/<id>` | What it answers |
| --- | --- |
| `overview` | Price of a litre this week (headline written from the data), what the project is in three lines, freshness and check status, every fuel and the dollar with weekly and 12-month changes, dearest and cheapest states, size of the dataset |
| `findings` | Three annotated charts: inflation, the dollar (both indexed on one axis), the spread between states |
| `explorer` | Any fuel for Brazil, a region or up to five places; nominal or real; p10–p90 band (one place at a time); dollar in an aligned companion chart; numbered events listed under the chart; period by preset, brush or date fields |
| `states` | Tile map and ranking against the national average for a chosen week, then every state's trajectory with Brazil and the selected state picked out |
| `parity` | Ethanol ÷ gasoline against the 70% rule: four headline figures, the ranking of the states, the trend |
| `flags` | Branded minus white-flag price, week by week |
| `quality` | Status and freshness, row funnel (raw = staged + quarantine, then what is published), where the quarantine comes from, rules, checks, source files, run history |
| `about` | What the pipeline does, architecture with the counts of the latest run, engineering guarantees, the public tables to download, links |

## Code

```
js/main.js            boot, navigation, scroll spy, view lifecycle
js/chrome.js          masthead, sticky nav with the quality chip, missing-file banner, footer
js/data.js            fetch of the seven files (two required, five optional) and the model
js/series.js          pure helpers: null-aware search, changes, places, real terms, gaps, latest figures
js/state.js           the store and its mirror in the URL hash
js/i18n.js            t(), Intl formatters, labels from the data; js/strings.js holds every string as [pt, en]
js/theme.js           light / dark, colour tokens for charts
js/charts.js          time-series chart (Plot + D3 overlay) and sparkline
js/chart-events.js    numbered event markers and their cards
js/brush.js           period strip with a D3 brush
js/figure.js          figure frame (title, takeaway, unit, source, table twin) and legend
js/tooltip.js         the shared tooltip and crosshair ownership
js/controls.js        select, segmented control, toggle, week scrubber, date range, roving focus
js/table.js           sortable table with an optional row limit
js/ui.js              icons, signed change, headings, status badges, credits, notices
js/dom.js  js/lib.js  element builders (text only, never innerHTML); vendored globals
js/views/*.js         one module per section; quality-tables.js and architecture.js serve quality.js and about.js
```

A view is `mount(section, {model, goTo})`; it builds its DOM and returns
`update(changedKeys)`. The store calls every `update` with the keys that changed
(`fuel`, `places`, `period`, `real`, `band`, `usd`, `events`, `week`, `uf`, plus
`theme` and `resize`); a language change remounts the views.

Everything a visitor chooses lives in the hash, defaults left out, so any view
can be shared:

```
#/explorer?fuel=ETANOL&places=BR,SP,R-NE&period=3y&prices=real&usd=1
#/states?fuel=GNV&week=2026-10-04&uf=SP
#/explorer?period=2022-01-23..2024-02-18&band=0&events=0&lang=en
```

Derived from the files, never written in the code: latest week, products and
units, which states have data, the headline fuel, every sentence with a number,
rule, check and file lists, run history, row counts in the architecture
diagram. Fixed in the code: product ids `GASOLINA` / `ETANOL` and the rule id
`row_not_blank` (contract ids), the 70% rule of thumb, the diverging classes of
the state map and the tile layout.

## Accessibility and design

- Every chart has a title, a takeaway written from the data, a unit line, a
  source credit and a table twin; one y axis per chart; the dollar is grey and
  dashed everywhere; colour never carries meaning alone (status has an icon and
  a word, deviations a sign).
- Charts are readable with the arrow keys; tiles and rankings are one tab stop
  each with arrow-key movement; the period can be typed; the skip link keeps the
  URL state. axe-core reports no violation in either theme at 1440 and 390 px.
- Newsreader for headlines, IBM Plex Sans for data and interface, IBM Plex Mono
  for identifiers. Light and dark themes follow the system until toggled.

## Checks

`node tools/check_site.mjs`, run by CI, checks that every script parses and
that every interface string exists in both languages and is used. Screenshots
of the views are in the repository's `docs/images/`.
