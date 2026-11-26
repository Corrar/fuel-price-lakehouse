# 0002 — What is published, and where the state of a run lives

- Status: accepted
- Date: 2026-11-26
- Supersedes part of [0001](0001-storage-between-runs.md)

## Context

[0001](0001-storage-between-runs.md) planned to keep every Parquet layer as
assets of a public GitHub release, so that each scheduled run could pick up
where the previous one stopped. Two things changed since then.

**The licence of the ANP data is not declared.** No official source that could
be read states a licence for the dataset
([`../sources.md`](../sources.md), section 1.6). Decreto 8.777/2016 makes
federal open data free to use with credit to the source, and the only contrary
signal is the site-wide footer of gov.br. The owner of this repository decided
on the cautious reading: **publish aggregates, with credit; publish nothing at
station level.**

**Release assets are public.** Anything stored there is a download link. The
staged and curated station-level tables therefore cannot live in a release.

## Decision

### What leaves the machine that ran the pipeline

| Output | Published | Where |
| --- | --- | --- |
| Dashboard: weekly aggregates by country, region and state, and the quality report | Yes | GitHub Pages |
| Aggregate tables (`agg_*`), exchange rate and IPCA tables, as Parquet | Yes | Assets of the `data-latest` release |
| Collection manifest: URL, size and SHA-256 of every source file ingested | Yes | Asset of the `data-latest` release |
| Quality history: one line of counts per run | Yes | Asset of the `data-latest` release |
| Raw, staged and quarantine layers; station-level curated tables | **No** | Stay on the runner |

Guard rails, enforced in code rather than by convention:

- The set of public tables is a whitelist in one place, and a test fails if a
  public table has a station-level column (CNPJ, station name, address).
- **Small-cell suppression**: an aggregate row built from fewer than three
  distinct stations is not written, so a public number never stands for one or
  two identifiable businesses.
- The quality report carries counts only, never example rows.
- Every public view credits "ANP — Série Histórica de Preços de Combustíveis"
  and "Banco Central do Brasil — SGS".

### Where the state between runs lives

The layers that cannot be published still have to survive between runs for the
pipeline to be incremental. They are kept in the **GitHub Actions cache**,
which 0001 rejected as a *store* and which is used here only as an
*accelerator*:

- A run restores `data/` from the cache, processes what is new, and saves it
  again.
- If the cache is gone (it is evicted after seven days without access, or when
  the repository passes 10 GB), the run rebuilds from the public sources. That
  costs one long run (about 600 MB of downloads for the default history, see
  `sources.md` section 1.3) and nothing else: no state is lost that cannot be
  recomputed.
- The one thing that cannot be recomputed is the **quality history** (what
  past runs saw). It is small and contains only counts, so it is published as
  a release asset and restored from there when the cache is empty.
- The pipeline runs **twice a week** (Tuesday, after ANP's Monday update, and
  Friday). The second run picks up late publications and new exchange rates,
  and keeps the cache from reaching the seven-day limit.

### Audits

Change detection for ANP files relies on size, and finished years of the
Banco Central series are not requested again. Both are cheap and can miss a
republication that keeps the size, or a revised old value. The first scheduled
run of each month therefore runs with `--full-check`: everything is downloaded
again and compared by SHA-256, and anything that changed is re-ingested, with
the replaced version noted in the manifest (`superseded`).

## Consequences

- Anyone can verify the published aggregates: the manifest lists the exact
  source files (with hashes) and the code is in this repository.
- The station-level tables exist only where the pipeline runs. To explore
  them, clone the repository and run `make pipeline`.
- The raw layer keeps the current content of each source file as a Parquet
  copy, not the original bytes, and a republication replaces it. The SHA-256
  in the manifest is of the downloaded bytes; it can be checked against the
  source, not against the Parquet file. Keeping every original file would
  need about 5 GB for the full history, which no free storage option here
  holds privately.
- If ANP declares an open licence for the dataset, publishing the station-level
  tables is a change to the whitelist, not to the design.
