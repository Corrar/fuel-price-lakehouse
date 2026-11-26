# fuel-price-lakehouse

[![CI](https://github.com/Corrar/fuel-price-lakehouse/actions/workflows/ci.yml/badge.svg)](https://github.com/Corrar/fuel-price-lakehouse/actions/workflows/ci.yml)

A small, fully automated data pipeline for Brazilian fuel prices.

Every week it will collect station-level fuel prices published by ANP (CSV
files) and macroeconomic series from Banco Central do Brasil (JSON API), check
their quality, quarantine the rows that break a rule, and publish
analysis-ready Parquet tables together with a data quality report.

> **Status: stage 1 of 5 — collection.** The pipeline can fetch the source
> files incrementally into the raw layer and keeps a manifest of everything it
> ingested. The data is not yet cleaned or validated, so there are no analysis
> results to show. This section is updated as each stage lands.

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
- **Raw**: files exactly as downloaded.
- **Staged**: typed, normalised rows that passed validation.
- **Quarantine**: rows that broke a rule, kept with the reason.
- **Curated**: tables ready for analysis.

Stack: Python 3.12, uv, Parquet + DuckDB, Pandera, httpx + tenacity, pytest,
ruff, GitHub Actions. No paid services.

## Documentation

- [`docs/sources.md`](docs/sources.md) — what the two sources actually look
  like: URLs, formats, volumes, limits, licences and the data problems found
  in samples.
- [`docs/collection.md`](docs/collection.md) — how the incremental collection
  works: discovery, change detection, the manifest and failure handling.
- [`docs/decisions/0001-storage-between-runs.md`](docs/decisions/0001-storage-between-runs.md)
  — where the data lives between runs, and why.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
make install   # create the virtualenv from the lockfile
make check     # lint + tests, the same thing CI runs
make format    # auto-fix and format
make collect   # fetch new or changed source files into data/raw
```

`make collect` talks to the real sources. The first run downloads a few
hundred megabytes; later runs only fetch what changed. Use
`uv run fuel-lakehouse collect --dry-run` to see the plan first.

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

**Situação: etapa 1 de 5 — coleta.** O pipeline já busca os arquivos das fontes
de forma incremental para a camada bruta e mantém um registro de tudo o que
coletou. Os dados ainda não são tratados nem validados, então não há
resultados de análise.
