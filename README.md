# fuel-price-lakehouse

[![CI](https://github.com/Corrar/fuel-price-lakehouse/actions/workflows/ci.yml/badge.svg)](https://github.com/Corrar/fuel-price-lakehouse/actions/workflows/ci.yml)

A small, fully automated data pipeline for Brazilian fuel prices.

Every week it will collect station-level fuel prices published by ANP (CSV
files) and macroeconomic series from Banco Central do Brasil (JSON API), check
their quality, quarantine the rows that break a rule, and publish
analysis-ready Parquet tables together with a data quality report.

> **Status: stage 1 of 5 — collection.** `make extract` collects both sources
> incrementally into a raw Parquet layer, driven by a manifest of the files
> already ingested. Nothing is cleaned, validated or published yet, so there
> are no results to show. This section is updated as each stage lands.

## Roadmap

| Stage | Scope | Status |
| --- | --- | --- |
| 0 | Sources confirmed, repository foundation, storage decision | Done |
| 1 | Incremental collection with a file manifest; raw layer | Done |
| 2 | Typed and normalised staged layer; validation rules; quarantine | Not started |
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
make extract                 # history from 2023-01 (the default)
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

| `SINCE` | Download | Time at 0.75 MB/s |
| --- | ---: | ---: |
| `2026-01` | 96 MB | 2 min |
| `2023-01` (default) | 242 MB | 5 min |
| `2020-01` | 603 MB | 13 min |
| `2004-01` (everything) | 4,813 MB | 107 min |

## Documentation

- [`docs/sources.md`](docs/sources.md) — what the two sources actually look
  like: URLs, formats, volumes, limits, licences and the data problems found
  in samples.
- [`docs/decisions/0001-storage-between-runs.md`](docs/decisions/0001-storage-between-runs.md)
  — where the data lives between runs, and why.

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

**Situação: etapa 1 de 5 — coleta.** `make extract` baixa só o que é novo das
duas fontes e grava uma cópia fiel em Parquet (camada bruta), guiado por um
registro dos arquivos já coletados. Ainda não há limpeza, validação nem
resultados publicados.
