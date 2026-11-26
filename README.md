# fuel-price-lakehouse

[![CI](https://github.com/Corrar/fuel-price-lakehouse/actions/workflows/ci.yml/badge.svg)](https://github.com/Corrar/fuel-price-lakehouse/actions/workflows/ci.yml)

A small, fully automated data pipeline for Brazilian fuel prices.

Every week it will collect station-level fuel prices published by ANP (CSV
files) and macroeconomic series from Banco Central do Brasil (JSON API), check
their quality, quarantine the rows that break a rule, and publish
analysis-ready Parquet tables together with a data quality report.

> **Status: stage 2 of 5 — staged layer.** `make extract` collects both
> sources incrementally and `make stage` types and validates every row: rows
> that break a rule go to quarantine with the reason. Nothing is published
> yet. This section is updated as each stage lands.

## Roadmap

| Stage | Scope | Status |
| --- | --- | --- |
| 0 | Sources confirmed, repository foundation, storage decision | Done |
| 1 | Incremental collection with a file manifest; raw layer | Done |
| 2 | Typed and normalised staged layer; validation rules; quarantine | Done |
| 3 | Curated tables for analysis | Not started |
| 4 | Data quality report published on GitHub Pages | Not started |
| 5 | Weekly scheduled run on GitHub Actions | Not started |

## Planned design

```
ANP CSV files ─┐
               ├─> collect ─> raw ─> staged ─┬─> curated ─> quality report
BCB SGS API  ──┘   (manifest)                └─> quarantine
```

- **Collect**: incremental, driven by a manifest of files already ingested.
- **Raw**: a faithful Parquet copy of each downloaded file, every value kept as text.
- **Staged**: typed, normalised rows that passed validation.
- **Quarantine**: rows that broke a rule, kept with the reason.
- **Curated**: tables ready for analysis.

Stack: Python 3.12, uv, Parquet + DuckDB, Pandera, httpx + tenacity, pytest,
ruff, GitHub Actions. No paid services.

## Collection (stage 1)

```sh
make extract                 # history from 2020-01 (the default)
make extract SINCE=2026-01   # choose the first month
make extract SINCE=2004-01 ARGS=--dry-run   # list what would be downloaded, with sizes
```

What a run does:

- **ANP**: reads the landing page, finds the data files by their links (file
  names are irregular, so no URL is ever built from a date), and learns each
  file's period from the link text. A file already in the manifest is checked
  with a one-byte range request; only new files, or files whose size changed,
  are downloaded, and the SHA-256 decides whether the content really changed.
- **Banco Central**: requests series 1 (dollar) and 433 (IPCA) one calendar
  year per request, inside the API's 10-year limit. Finished years are not
  requested again; the current year is revalidated with its `ETag`.
- **Manifest** (`data/manifest/collection.json`): source URL, collection time,
  size, SHA-256 and row count of every file. It is saved after each file, so a
  run that fails halfway resumes where it stopped.
- **Raw layer** (`data/raw/`): a faithful copy in Parquet. Every source column
  is text exactly as received (decimal commas, padded CNPJ and empty strings
  included), plus tracking columns: `_source_url`, `_source_sha256`,
  `_collected_at`, `_row_number`.
- Requests go one at a time with a pause between them. Network errors, 429 and
  5xx are retried with exponential backoff.

Running it twice in a row downloads nothing and rewrites nothing. `--full-check`
downloads everything again and compares hashes, for an occasional audit.

How much history to load (measured on 2026-11-26, details in
[`docs/sources.md`](docs/sources.md)):

| `SINCE` | Download | Estimated time at 0.75 MB/s |
| --- | ---: | ---: |
| `2026-01` | 96 MB | 2 min |
| `2023-01` | 242 MB | 5 min |
| `2020-01` (default) | 603 MB | 13 min |
| `2004-01` (everything) | 4,813 MB | 107 min |

## Staged layer and quarantine (stage 2)

```sh
make stage        # type and validate every raw file that changed since the last run
```

- Every raw row ends up in exactly one place: the staged layer (typed:
  dates, prices, a 14-digit CNPJ, normalised units and whitespace) or the
  quarantine (the original text of every field, plus the ids of the rules it
  broke). For every file, rows read = rows staged + rows quarantined.
- 22 rules for ANP rows and 5 for the Banco Central series, each with an id,
  a severity and a one-sentence description in English and Portuguese.
  Error rules send a row to quarantine; warning rules keep it and tag it.
- A Pandera contract checks the staged table; if it fails, the code has a
  bug and the run stops.
- Incremental: a file is staged again only when its raw content or the
  rules changed. A second run changes no byte.

On the history since 2020: 6,965,798 rows staged and 9,130 in quarantine
(9,114 blank rows at the end of one file and 16 exact duplicates), 55 files
in about 50 seconds. The decisions behind this are in
[ADR 0003](docs/decisions/0003-validation-and-quarantine.md).

## Documentation

- [`docs/sources.md`](docs/sources.md) — what the two sources actually look
  like: URLs, formats, volumes, limits, licences and the data problems found
  in samples.
- [`docs/decisions/0001-storage-between-runs.md`](docs/decisions/0001-storage-between-runs.md)
  — where the data lives between runs, and why.
- [`docs/decisions/0002-what-is-published.md`](docs/decisions/0002-what-is-published.md)
  — what leaves the machine that runs the pipeline (aggregates only).
- [`docs/decisions/0003-validation-and-quarantine.md`](docs/decisions/0003-validation-and-quarantine.md)
  — validation rules, quarantine, and what is incremental.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
make install   # create the virtualenv from the lockfile
make check     # lint + tests, the same thing CI runs (tests never touch the network)
make format    # auto-fix and format
```

## Data sources

- ANP — Série Histórica de Preços de Combustíveis (open data).
- Banco Central do Brasil — SGS time series API (open data).

This project is not affiliated with ANP or Banco Central do Brasil. See
[`docs/sources.md`](docs/sources.md) for the terms of use of each source.

## License

The code is released under the [MIT License](LICENSE). The data belongs to its
publishers and is subject to their own terms, described in
[`docs/sources.md`](docs/sources.md).

---

## Resumo em português

Pipeline de dados automatizado para preços de combustíveis no Brasil. Toda
semana ele vai coletar os preços por posto publicados pela ANP (arquivos CSV)
e séries do Banco Central (API SGS), conferir a qualidade, separar em
quarentena as linhas que quebram alguma regra e publicar tabelas prontas para
análise, com um relatório de qualidade.

**Situação: etapa 2 de 5 — camada tratada.** `make extract` baixa só o que é
novo das duas fontes e `make stage` tipa e valida cada linha: o que quebra uma
regra vai para a quarentena com o motivo, nunca é descartado em silêncio. Ainda
não há resultados publicados.
