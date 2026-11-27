# Data sources

Everything in this document was measured against the live sources on
**2026-11-26** (requests made between roughly 00:55 and 01:20 UTC), before any
collection code was written. Numbers come from HTTP responses and from the
sampled files listed below. Anything that could not be checked is listed under
"To confirm" instead of being assumed.

- [1. ANP — fuel price history](#1-anp--fuel-price-history)
- [2. Banco Central do Brasil — SGS API](#2-banco-central-do-brasil--sgs-api)
- [3. What this means for the pipeline](#3-what-this-means-for-the-pipeline)

---

## 1. ANP — fuel price history

Station-level retail fuel prices from the weekly survey run for ANP (Agência
Nacional do Petróleo, Gás Natural e Biocombustíveis).

- Landing page:
  <https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/serie-historica-de-precos-de-combustiveis>
- The page showed "Atualizado em 23/11/2026 15h47".
- Base for every file URL below:
  `R = https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/shpc`

### 1.1 What is published

| Section on the page | Fuel group | Files | Period covered | URL pattern |
| --- | --- | --- | --- | --- |
| Weekly data grouped by semester | Automotive fuels | 45 | 2004-01 to 2026-01 | `R/dsas/ca/ca-YYYY-SS.csv` up to 2021-02, `.zip` from 2022-02; 2022-01 is `R/dsas/ca/precos-semestrais-ca.zip` |
| Weekly data grouped by semester | LPG (GLP P13) | 45 | 2004-01 to 2026-01 | `R/dsas/glp/glp-YYYY-SS.csv`, with exceptions `precos-semestrais-glp-2022-01.csv` and `precos-semestrais-glp2021-01.csv` |
| Weekly data grouped by month | Diesel (S-500, S-10) + CNG | 46 | Jan 2023 to Oct 2026 | 2023–2025: `R/dsan/YYYY/precos-diesel-gnv-MM.csv`; 2026: irregular |
| Weekly data grouped by month | Hydrous ethanol + gasoline | 46 | Jan 2023 to Oct 2026 | 2023–2025: `R/dsan/YYYY/precos-gasolina-etanol-MM.csv`; 2026: irregular |
| Weekly data grouped by month | LPG (GLP P13) | 46 | Jan 2023 to Oct 2026 | 2023–2025: `R/dsan/YYYY/precos-glp-MM.csv`; 2026: irregular |
| Last four weeks ("atualizado em 23/11/2026") | the three groups above | 3 | 26/10/2026 to 21/11/2026 (gasoline-ethanol file) | `R/qus/ultimas-4-semanas-{diesel-gnv,gasolina-etanol,glp}.csv` |

Things that matter for collection:

- **File names cannot be derived from the period.** The 2026 monthly files
  alone use five different shapes, including one with no extension
  (`04-dados-abertos-precos-gasolina-etanol`) and one with a typo
  (`02-cados-abertos-preco-gasolina-etanol.csv`). The list of files has to be
  read from the landing page on every run.
- No semester file for 2026-02 was linked yet. The monthly section has nothing
  before 2023.
- The earliest data in the 2004-01 automotive file is dated 10/05/2004.

Publication frequency, as stated by the source:

- Landing page: prices come from "uma pesquisa semanal de preços realizada por
  empresa contratada".
- Metadata PDF (`R/metadados-serie-historica-precos-combustiveis-1.pdf`, one
  page, "atualizado em 6/3/2026"): "Periodicidade de atualização: Semanal,
  mensal e semestral."

### 1.2 HTTP behaviour

| Observation | Detail |
| --- | --- |
| `HEAD` is refused | 403 on all 33 files tried. `GET` on the same URLs works. |
| Range requests | Accepted (`Accept-Ranges: bytes`). File size can be read with a one-byte range request from `Content-Range`. |
| No change validators | No `ETag`, no `Last-Modified`. `Cache-Control: max-age=10, must-revalidate`. |
| Content types | `text/csv`, `application/zip`. |
| Redirects, user agent | No redirects. No special `User-Agent` or `Referer` needed. |
| Rate limiting | No 429 or throttling in 57 sequential requests about one second apart. |
| Speed | About 0.72 to 0.79 MB/s on 7–8 MB files from the test machine. |
| TLS key exchange | A TLS 1.3 handshake carrying OpenSSL 3.5's default post-quantum key share (X25519MLKEM768) is reset after about 10 s: 10 of 10 attempts from GitHub's runners on 2026-11-27, whose Python ships OpenSSL 3.5. Offering only P-256 works for ANP and for Banco Central (15 of 15); X25519 alone works for ANP, but Banco Central refuses it. The HTTP client therefore offers P-256 only (`collect/http.py`). `curl` and OpenSSL 3.0 were not affected. |

### 1.3 Volume

Sizes as served (compressed for `.zip`), read from response headers.

| File | Bytes |
| --- | ---: |
| `dsas/ca/ca-2004-01.csv` | 48,637,859 |
| `dsas/ca/ca-2005-01.csv` | 157,737,721 |
| `dsas/ca/ca-2007-01.csv` | 145,365,707 |
| `dsas/ca/ca-2009-01.csv` | 111,592,555 |
| `dsas/ca/ca-2011-01.csv` | 112,871,260 |
| `dsas/ca/ca-2013-01.csv` | 116,041,985 |
| `dsas/ca/ca-2015-01.csv` | 121,838,059 |
| `dsas/ca/ca-2017-01.csv` | 85,783,990 |
| `dsas/ca/ca-2019-01.csv` | 87,183,889 |
| `dsas/ca/ca-2021-01.csv` | 57,911,124 |
| `dsas/ca/precos-semestrais-ca.zip` (2022-01) | 10,042,100 (90,812,404 unzipped) |
| `dsas/ca/ca-2024-01.zip` | 9,312,457 (81,365,972 unzipped) |
| `dsas/ca/ca-2026-01.zip` | 8,488,624 (72,117,162 unzipped) |
| `dsas/glp/glp-2004-01.csv` | 7,158,338 |
| `dsas/glp/glp-2026-01.csv` | 13,607,264 |
| `dsan/2026/10-gasolina-etanol.csv` | 8,376,524 |
| `dsan/2026/04-dados-abertos-precos-gasolina-etanol` | 8,269,617 |
| `qus/ultimas-4-semanas-gasolina-etanol.csv` | 8,167,637 |
| `qus/ultimas-4-semanas-diesel-gnv.csv` | 3,676,815 |
| `qus/ultimas-4-semanas-glp.csv` | 2,265,503 |

- The 13 automotive semester files measured add up to 1,072,807,330 bytes
  (1,023 MiB) as served. That is 13 of 45, so the full history is larger; the
  total was not measured.
- Row counts of the files downloaded in full: `ca-2026-01` 422,418 rows;
  last-four-weeks gasoline-ethanol 47,769 rows; `glp-2004-01` 45,925 rows.

Sizes of every file the collector would download for the full history, read
on 2026-11-26 with `make extract SINCE=2004-01 ARGS=--dry-run` (one-byte range
requests, nothing downloaded): 105 files, **4,813 MB**. That is 90 semester
files, the 12 monthly files not yet covered by a semester file (July to October
2026) and the 3 four-week files. Cumulative size by first year collected. The
time column is arithmetic (size divided by the 0.75 MB/s measured above), not a
timed download:

| History starting in | Download | Estimated time at 0.75 MB/s |
| --- | ---: | ---: |
| 2026 | 96 MB | 2 min |
| 2025 | 142 MB | 3 min |
| 2023 | 242 MB | 5 min |
| 2020 | 603 MB | 13 min |
| 2015 | 1,658 MB | 37 min |
| 2010 | 3,045 MB | 68 min |
| 2004 (everything) | 4,813 MB | 107 min |

The years up to 2021 weigh much more because the automotive semester files were
published as plain CSV: automotive and LPG together are 150 to 350 MB per year.
From 2022 on the automotive files are zipped (about 45 to 56 MB per year, all
groups).

The history since 2020 was then downloaded in full on the same day: 41 ANP
files and 14 yearly request windows of the two Banco Central series, 603.5 MB
as served, 6,974,928 ANP rows, stored as 127 MB of raw Parquet.

### 1.4 File format

Identical in every sample but one (the encoding of one file, below), from 2004
to November 2026, across semester,
monthly and four-week files.

| Property | Value |
| --- | --- |
| Container | Plain CSV, or a ZIP holding one CSV |
| Encoding | UTF-8 **with BOM**. One exception found so far: `dsas/ca/ca-2021-02.csv` is Windows-1252 without BOM (found when the history since 2020 was collected; the 15 files sampled before it were all UTF-8). |
| Separator | `;` |
| Line ending | CRLF |
| Quoting | Only when a field contains `;`, `"` or a tab; inner quotes are doubled |
| Dates | `dd/mm/yyyy` |
| Decimal separator | Comma |
| Missing value | Empty string |

Header, 16 columns, in this order:

```
Regiao - Sigla;Estado - Sigla;Municipio;Revenda;CNPJ da Revenda;Nome da Rua;Numero Rua;Complemento;Bairro;Cep;Produto;Data da Coleta;Valor de Venda;Valor de Compra;Unidade de Medida;Bandeira
```

- Products seen: `GASOLINA`, `GASOLINA ADITIVADA`, `ETANOL`, `DIESEL`,
  `DIESEL S10`, `GNV`, `GLP`. The 2004 sample has no `GASOLINA ADITIVADA` or
  `DIESEL S10`.
- Units: `R$ / litro`, `R$ / m³` (GNV), `R$ / 13 kg` (GLP). Consistent per
  product.
- ZIP member names are not reliable: the UTF-8 name flag is off and the name
  contains non-ASCII characters (`Preços semestrais - AUTOMOTIVOS_2026.01.csv`).
  Read the member by position, not by name.

**Does the schema change between periods?** Not in the samples. The header of
the semester files for 2004, 2005, 2007, 2009, 2011, 2013, 2015, 2017, 2019,
2021 and 2026 is the same: same names, order, BOM, separator and line ending.
What changes is the content:

| | 2004 samples | 2026 samples |
| --- | --- | --- |
| `Valor de Compra` (purchase price) | Filled in 67.3% of the automotive sample, 32.3% of LPG | Empty in every row. The metadata PDF says "Série disponível até agosto de 2020". |
| Price precision | Variable: `1,967`, `2,2`, and integers such as `30` | Always two decimals |
| Text casing | More mixed case | Mostly upper case |
| Distinct `Bandeira` values (automotive) | 93 | 46 |
| Packaging (automotive semester) | CSV | ZIP |

### 1.5 Data problems found in the samples

Counts refer to the file named. Three files were read in full: `ca-2026-01`
(422,418 rows), the last-four-weeks gasoline-ethanol file (47,769 rows) and
`glp-2004-01` (45,925 rows). `ca-2004-01` is the first 5 MB only (30,356 rows).

| Problem | Evidence |
| --- | --- |
| **Naive `;` splitting breaks rows** | 5,645 rows in `ca-2026-01` and 609 in the four-week file contain a quoted `;`, for example an address complement written as `"LOTE: 04;"`. With a quote-aware parser every row has 16 fields. |
| **CNPJ written two ways** | The samples all have a leading space and the mask (in the shape `; 11.222.333/0001-81;`; the example is not a real station). The two 2025-S2 semester files, read later, have 14 bare digits. After reducing to digits, all masks and check digits are valid. |
| **Decimal comma, and integers without separator** | Every price uses a comma. In `glp-2004-01`, 39,955 rows hold a bare integer price such as `30`. |
| **Purchase price empty** | 422,418 of 422,418 rows in `ca-2026-01`; 31,089 of 45,925 in `glp-2004-01`. |
| **Exact duplicate rows** | 6 in `ca-2026-01`, none in the other samples. No duplicated (CNPJ, product, date) key had conflicting prices in the samples; the full history since 2020 has 4 such keys, all in `ca-2024-01` (section 1.8). |
| **Republished rows** | The four-week file overlaps the monthly file: all 6,099 rows sampled from the end of the October 2026 monthly file (27–30/10/2026) also exist in the four-week file. 6,097 are byte-identical; 2 differ only in the station's name. |
| **Station name drift** | The same CNPJ on the same date appears as `… LTDA` in one file and `… LTDA - EPP` in the other. Names are not a stable key; the CNPJ is. |
| **Stray whitespace** | Leading or trailing spaces in non-CNPJ fields in 2,162 rows of `ca-2026-01` and 28,674 of `glp-2004-01`. Runs of repeated spaces inside a field in 40,020 rows of `ca-2026-01`. One 2004 row has a tab inside a field. |
| **Placeholder values in address fields** | `S/N` or `SN` as street number in 43,342 rows of `ca-2026-01`; `0` as complement in 2,674; `-` used as filler 356 times; neighbourhood empty in 728. |
| **Accents are inconsistent across columns** | `Municipio` is always upper case without accents (`SAO PAULO`), while `Revenda`, `Bairro`, `Nome da Rua` and `Bandeira` keep accents. |
| **Weekend collection dates** | 346 Saturday and 18 Sunday rows in `ca-2026-01`. Most rows fall Monday to Thursday. |
| **Rows are not sorted by date** | In every sample. |

Not found in the samples: unparsable dates, empty or non-numeric sale prices,
malformed CEP, wrong field counts (with a proper CSV parser), unexpected units.

### 1.6 Licence

Checked again on **2026-11-26**, looking for a licence declared for the dataset
itself rather than for the website.

| Where | What it says |
| --- | --- |
| Landing page body and metadata PDF | No licence. |
| Landing page footer | "Todo o conteúdo deste site está publicado sob a licença Creative Commons Atribuição-SemDerivações 3.0 Não Adaptada" (CC BY-ND 3.0). Site-wide text; it does not mention the data files. |
| ANP open data page (<https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos>) | No licence. |
| ANP Open Data Plan 2026-2028 (<https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/home/pda-2026-2028.pdf>) | No occurrence of "licença", Creative Commons or ODbL. It cites Decreto 8.777/2016 and lists encouraging reuse ("estímulo ao reúso") as a goal. |
| Federal open data portal, dados.gov.br | **Could not be read.** The dataset page is rendered by JavaScript and the public API answered 401 without a key. The licence field of the record is therefore unknown. |
| Third-party copy of the old dados.gov.br record (<https://data.amerigeoss.org/api/3/action/package_show?id=serie-historica-de-precos-de-combustiveis-por-revenda>) | `license_id: cc-by`, "Creative Commons Attribution", author ANP. Second-hand, undated, no CC version. Not evidence of the current terms. |
| Decreto 8.777/2016, federal open data policy (<https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2016/decreto/d8777.htm>) | Art. 2º III defines open data as published "sob licença aberta que permita sua livre utilização, consumo ou cruzamento, limitando-se a creditar a autoria ou a fonte". Art. 3º IV: "permissão irrestrita de reuso das bases de dados publicadas em formato aberto". Art. 4º: "Os dados disponibilizados pelo Poder Executivo federal e as informações de transparência ativa são de livre utilização pelos Poderes Públicos e pela sociedade." |

Conclusion:

- **No licence is explicitly declared for this dataset** in any official source
  that could be read.
- The strongest documented basis for reuse with attribution is Decreto
  8.777/2016, which ANP itself invokes for its open data. The only contrary
  signal is the site footer (CC BY-ND, "no derivatives"), which reads as
  covering the website content.
- This is a reading of public documents, not legal advice, and the one place
  where a licence would normally be declared (the dados.gov.br record) is still
  unread.

What the project publishes ([ADR 0002](decisions/0002-what-is-published.md)):
**weekly aggregates only** (Brazil, regions, states, branded and white-flag
stations), leaving out every cell built from fewer than three distinct
stations. They are served by the GitHub Pages dashboard (JSON, CSV and Parquet)
and as assets of the `data-latest` release. Nothing station-level (the landing
zone, the raw, staged and quarantine layers, the curated fact and station
tables) is published. The source is always credited as "ANP — Série Histórica
de Preços de Combustíveis". Source and contact given in the metadata PDF:
"ANP/SDC (Superintendência de Defesa da Concorrência)", `sdc@anp.gov.br`.

### 1.7 To confirm

- Contents of the semester files for the years not sampled (even years
  2006–2020, and the CSVs inside the 2022–2025 ZIPs).
- Full-file statistics for old automotive files, for the four-week diesel-CNG
  and LPG files, and for the monthly files.
- Whether already published semester files are corrected or re-issued later.
  There are no validators, and this was a single observation.
- The actual weekday of the weekly update. ANP says results are published "como
  regra geral, às sextas-feiras", but the one "atualizado em" stamp seen on the
  files page was a Monday (23/11/2026).
- The licence field of the dataset record on dados.gov.br (needs a browser or
  an API key), or a written answer from ANP (`sdc@anp.gov.br`).
- Behaviour under parallel or high-volume requests.

### 1.8 Survey conventions

Researched on 2026-11-26 from ANP's own pages and files, and checked against
the collected data. These are the facts the staged and curated layers rely on.

| Topic | What was established | Source |
| --- | --- | --- |
| **Survey week** | Sunday to Saturday. All 1,163 weeks from 2004 to 2026 in ANP's weekly spreadsheets (`DATA INICIAL` / `DATA FINAL`) start on a Sunday and end on a Saturday. Bucketing `Data da Coleta` that way reproduces ANP's station counts for the week of 15–21/11/2026 for all seven products. ANP never states the convention in words, and two week labels on its pages are off by a day, so the week is always computed from the collection date. | [weekly series since 2013](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/precos-revenda-e-de-distribuicao-combustiveis/shlp/semanal/semanal-brasil-desde-2013.xlsx) |
| **Who is surveyed** | Not a fixed panel: every week ANP selects stations from its registry to form the sample, with a quota per locality. In the data the sample changes slowly: of the stations priced for regular gasoline in one week of 2026, about 80 % (76 to 86 %) are priced again the next week, so a small weekly move includes the effect of the stations that came and went. The full design is 459 localities and 10,920 visits a week; budget cuts reduced it between July 2024 and November 2025. | [survey information page](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/precos-revenda-e-de-distribuicao-combustiveis/informacoes-levantamento-de-precos-de-combustiveis), [2025 impact report](https://www.gov.br/anp/pt-br/assuntos/consultas-e-audiencias-publicas/consulta-previa/2025/cp-03-2025/rpair.pdf) |
| **Gaps** | "Não houve pesquisa de preços entre 23/8/2020 e 17/10/2020." The week of 18–24/09/2022 has no station-level rows (change of contractor). The weeks right after each gap have far fewer stations than usual. | [survey information page](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/precos-revenda-e-de-distribuicao-combustiveis/informacoes-levantamento-de-precos-de-combustiveis); the collected data |
| **Averages** | A municipality's average is a simple mean of its stations: the curated table reproduces 2,505 of the 2,506 municipality-product averages of ANP's summary for 15–21/11/2026 ([validation](validation.md)). ANP's state and national averages are weighted by sales volume, which is not in the open data. **This project publishes simple means over surveyed stations, so its state and national figures differ slightly from ANP's bulletins.** | the collected data, compared with ANP's weekly summary |
| **`Valor de Compra`** | The distribution price paid by the station. "Série disponível até agosto de 2020"; the last value in the data is dated 17/08/2020. | [metadata PDF](https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/shpc/metadados-serie-historica-precos-combustiveis-1.pdf) |
| **`Bandeira` = `BRANCA`** | A station that "opta por não exibir marca comercial de nenhuma distribuidora" (white-flag, unbranded). | metadata PDF |
| **Products** | `GASOLINA ADITIVADA` appears from 19/10/2020. Prices are cash prices. Prices with three decimal places are the majority in 2020 and 2021, disappear in the first half of 2022, come back in 2,920 rows between July and 13 September 2022, and do not occur after that. | survey information page; the collected data |
| **Ethanol rule of thumb** | Ethanol "só é economicamente vantajoso […] quando o preço deste representar 70% ou menos do valor da gasolina comum". No more recent ANP statement was found, and gasoline now carries more ethanol than in 2016, so the dashboard presents it as a rule of thumb. | [ANP, Boletim Anual de Preços 2016, p. 66](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/arquivos-boletim-anual-de-precos/boletim-2016.pdf) |

Found while profiling the history since 2020 (6,974,928 rows in 41 files):

- The 2025-S1 automotive file ends with 9,114 rows in which all 16 fields are
  empty.
- The two 2025-S2 semester files write the CNPJ as 14 bare digits (every other
  file uses the mask with a leading space) and the GNV unit as `R$ / m3`
  instead of `R$ / m³`.
- The same station, product and date never has two different prices across
  files. Inside one file it happens for 4 keys (one station, June 2024), and
  16 rows are exact duplicates of another row.
- The month of June 2026 is identical, row for row, in the monthly file and in
  the semester file (53,454 rows). The last-four-weeks file and the monthly
  file agree on every price; 22 of 20,773 shared rows differ in the station's
  name or address only, the four-week file being the more recent registry.
- A station's name changes over time for 8 % of CNPJs and its brand for 23 %
  (many of them the renaming of Petrobras Distribuidora to Vibra). The CNPJ is
  the only stable key.

---

## 2. Banco Central do Brasil — SGS API

Time series from SGS (Sistema Gerenciador de Séries Temporais), used here for
the exchange rate and inflation context around fuel prices.

### 2.1 Endpoint

Base: `https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}`

| Form | Result |
| --- | --- |
| `/dados?formato=json&dataInicial=dd/MM/yyyy&dataFinal=dd/MM/yyyy` | 200, `application/json; charset=utf-8` |
| `/dados/ultimos/{N}?formato=json` | 200, same |
| either form with `formato=csv` | 200, `text/csv; charset=utf-8` |

No key or authentication. No `User-Agent` required.

**JSON response**: a bare array, no envelope and no pagination.

```json
[{"data":"23/11/2026","valor":"5.9708"}]
```

- Exactly two keys. **Both are strings**, including the value.
- Decimal separator is a dot. Date is `dd/MM/yyyy`. Sorted ascending.
- ISO dates in the request are rejected with 400.

**CSV response**: header `"data";"valor"`, separator `;`, every field quoted,
CRLF, and a **decimal comma** (`"5,9708"`). The two formats disagree on the
decimal separator, so the pipeline uses JSON only.

### 2.2 Series

Identity confirmed for each code on the SGS metadata page ("Dados básicos da
série"). First and last observation are what the API returned.

| Code | Official name | Unit | Frequency | First observation | Last observation |
| ---: | --- | --- | --- | --- | --- |
| 1 | Taxa de câmbio - Livre - Dólar americano (venda) - diário | u.m.c./US$ | Daily | 28/11/1984 | 25/11/2026 |
| 10813 | Taxa de câmbio - Livre - Dólar americano (compra) | u.m.c./US$ | Daily | 28/11/1984 | 25/11/2026 |
| 3698 | Taxa de câmbio - Livre - Dólar americano (venda) - Média de período - mensal | u.m.c./US$ | Monthly | 01/02/1953 | 01/10/2026 |
| 433 | Índice nacional de preços ao consumidor-amplo (IPCA) | % monthly change | Monthly | 01/01/1980 | 01/10/2026 |
| 13522 | Índice nacional de preços ao consumidor - amplo (IPCA) - em 12 meses | % | Monthly | 01/12/1980 | 01/10/2026 |
| 432 | Taxa de juros - Meta Selic definida pelo Copom | % per year | Daily | 05/03/1999 | 09/12/2026 (see quirks) |
| 11 | Taxa de juros - Selic | % per day | Daily | 04/06/1986 | 25/11/2026 |
| 189 | Índice geral de preços do mercado (IGP-M) | % monthly change | Monthly | 01/06/1989 | 01/10/2026 |

- Minimum set for this project: **1** (dollar) and **433** (IPCA). The others
  are confirmed and available if a later stage needs them.
- No oil or Brent price series was found on the Banco Central open data portal
  (searches for "brent" and "commodities" returned nothing; "petróleo" returned
  only a credit balance series).

### 2.3 Query limits

All observed, not taken from documentation.

| Request | Result |
| --- | --- |
| Daily series, no dates | **406** |
| Daily series, only `dataInicial`, more than 10 years back | **406** |
| Daily series, window of exactly 10 years (26/11/2016–26/11/2026) | 200, 2,508 rows |
| Daily series, window of 10 years plus one day | **406** |
| Daily series, only `dataInicial`, within the last 10 years | 200 |
| `ultimos/20` | 200 |
| `ultimos/21` | **400**, "A quantidade máxima de valores deve ser 20" |
| Monthly series (433, 3698, 13522, 189), no dates | 200, full history |
| Monthly series, window longer than 10 years | 200 |
| Window with no observations (a weekend) | **404**, "Value(s) not found" |
| Unknown series code | **404**, plain text "A serie nao existe!" |

So: **daily series need `dataInicial` and a window of at most 10 years;
monthly series have no such limit.** A full history of a daily series has to be
fetched in windows.

Two traps:

- **An empty window is a 404, not an empty list.** The collector has to tell
  "no data in this period" apart from "wrong URL".
- **Error bodies have four different shapes** (`error`/`message`/`syntax` for
  406; `erro.detail` for 400 and 404; `erro` as a plain string for a bad date;
  plain text for an unknown series). Errors should be classified by status
  code, not by parsing the body.

The description on the open data portal states the same limits ("A diferença
entre dataFinal e dataInicial não deve ser superior a 10 anos", last N values
"limitado a 20") and says filters became mandatory on 26 March 2025.

### 2.4 HTTP behaviour

| Observation | Detail |
| --- | --- |
| Caching | `Cache-Control: public, max-age=900` and `private` both present. JSON responses carry a weak `ETag`; a conditional request with `If-None-Match` returned 304. No `Last-Modified`. |
| Rate limiting | No rate-limit headers. No 429 or 5xx in 43 sequential requests about one second apart. The actual limit is unknown. |
| Latency | 0.24–0.96 s for small requests. Ten-year daily windows: 1.1 s for recent years, 4.6–6.3 s for old ones. |
| Compression | A 97,813-byte response came back uncompressed even when gzip was requested. |

### 2.5 Data quirks

- **Values are strings** with a fixed number of decimals: 4 for exchange rates,
  2 for IPCA, IGP-M and the Selic target, 6 for the daily Selic.
- **Daily exchange rate and Selic have business days only.** Holidays and
  weekends are simply absent, with no null rows: in series 1, 100 of the 2,608
  weekdays between 28/11/2016 and 25/11/2026 are missing. A missing day is
  normal, not a gap in collection.
- One weekend observation does exist in series 1 and 10813: Sunday 15/01/1989.
- **Series 432 (Selic target) includes future dates** and every calendar day.
  `ultimos/5` returned 05/12/2026 to 09/12/2026 on 25/11/2026. "Latest N" is
  not "latest as of today" for this series.
- **Monthly series are dated the first day of the reference month**, and the
  latest month available was October 2026 on 26 November.
- **Old exchange rates are in the currency of the time** (2,828.0000 in 1984,
  0.8570 in November 1994). The unit is "current monetary unit", so the series
  is not continuous across Brazil's currency changes. Fuel data starts in
  2004, after the last change, so this does not affect the join.
- No duplicated dates in any response (about 16,000 rows checked).

### 2.6 Licence

- The open data portal page for series 1
  (<https://dadosabertos.bcb.gov.br/dataset/1-taxa-de-cambio---livre---dolar-americano-venda---diario>)
  states "Open Data Commons Open Database License (ODbL)". The portal's
  metadata gives the same licence for series 1, 10813, 432 and 11.
- Checked again on 2026-11-26 through the portal's catalogue API
  (<https://dadosabertos.bcb.gov.br/api/3/action/package_search>): 4,260 of its
  4,261 datasets are tagged ODbL, including other IPCA series, but series 433
  still has no record of its own.
- Series 433, 13522, 3698 and 189 have **no page on the portal**, so the
  licence for them was not confirmed. Their data originates from IBGE (IPCA)
  and FGV (IGP-M), according to the SGS metadata.

### 2.7 To confirm

- Licence for series 433 (IPCA), 13522, 3698 and 189.
- Rate limits and quotas.
- Whether published values are revised later, and at what time of day new
  values appear (a single day cannot show this).
- Which time zone the API uses for "today" in the 10-year check. One
  observation at 00:58 UTC used the UTC date.
- The 10-year rule on daily series other than series 1 (10-year windows worked
  on 10813, 432 and 11; larger ones were not tried).
- Whether gzip is unsupported by the API or was removed by the network path
  used for the test.
- Why series 3698 starts on 01/02/1953 in the API while its metadata says
  31/1/1953.

---

## 3. What this means for the pipeline

Decisions the measurements above force on the later stages.

| Finding | Consequence |
| --- | --- |
| ANP file names are irregular | Discover files by reading the landing page; never build URLs from dates. |
| ANP has no `ETag`/`Last-Modified`, and `HEAD` is refused | Detect change by size (range request) and content hash, recorded in the manifest. |
| Four-week and monthly files repeat the same rows | Deduplicate on (CNPJ, product, collection date) across files, not only within a file. |
| Quoted `;` inside fields | Use a real CSV parser; a split on `;` silently corrupts thousands of rows. |
| BOM, decimal comma, padded CNPJ, stray whitespace | Normalise in the staged layer; keep the raw file untouched. |
| Purchase price empty since 2020 | Nullable column, never a validation failure. |
| Station names drift for the same CNPJ | CNPJ is the station key; names are attributes. |
| SGS daily series limited to 10 years per request | Fetch every series in calendar-year windows, well under the limit; each year is its own manifest record. |
| SGS returns 404 for an empty window | Treat 404 "Value(s) not found" as "no data", not as an error. |
| SGS values are strings | Parse the text explicitly, with the dot as decimal mark and negative values allowed (IPCA has negative months); never rely on JSON typing. |
| SGS daily series skip non-business days | Average the daily rates within each survey week and join that mean by week; a week without a rate gets no value. |
