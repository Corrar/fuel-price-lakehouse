# Collection (stage 1)

How source files get into the raw layer, and how a run knows what it already
has. The facts about the sources that drive these choices are in
[`sources.md`](sources.md).

## Running it

```sh
make collect                                     # everything that is new since the last run
uv run fuel-lakehouse collect --dry-run          # show the plan, download nothing
uv run fuel-lakehouse collect --source sgs       # one source only (anp | sgs | all)
uv run fuel-lakehouse collect --since 2019-01    # start earlier than the default
uv run fuel-lakehouse collect --force            # download ANP files again, ignoring sizes
```

The command prints one line per file or request, then a total. The exit code
is `1` if anything failed, so a scheduler can alert on it.

## What ends up on disk

```
data/
├── manifest/collection.jsonl
└── raw/
    ├── anp/<dataset>/grain=<grain>/period=<period>/sha256=<12 hex>/<original file name>
    └── bcb_sgs/sgs_<code>/start=<yyyymmdd>/end=<yyyymmdd>/sha256=<12 hex>/data.json
```

- Raw files are byte-for-byte what the source sent. Nothing is decoded,
  unzipped or fixed here; that is the job of the next stage.
- The content hash is part of the path, so a file is **never overwritten**.
  When a source republishes something, the new version lands next to the old
  one.
- `data/` is not committed to git.

## The manifest

`collection.jsonl` has one line per raw file ever ingested: source, dataset,
period, URL, original file name, size, SHA-256, path of the raw file, and when
it was fetched. It is the only state the collection needs.

- **Append-only.** A republished file adds a line (with `details.replaces`
  pointing at the previous hash) instead of changing an old one.
- **Saved after every file**, and written to a temporary file that is then
  renamed. A run that is interrupted loses at most the file in progress, and
  the next run continues from there.

## ANP: files

1. **Discover.** The landing page is read on every run and every data link is
   classified by fuel group and period. File names are never built from dates,
   because the source does not name files consistently. A link that cannot be
   classified is reported as a warning, not dropped; a page with no data links
   at all aborts the run, since that means the page layout changed.
2. **Select.** The same weeks are published three times: in semester files, in
   monthly files, and in the rolling "last four weeks" file. The collector
   takes the smallest set that covers the requested period: semester files
   while they exist, monthly files only for the months after the last semester
   file, and always the rolling files.
3. **Decide whether to download.** The source sends no `ETag` or
   `Last-Modified` and refuses `HEAD`, so change detection is done with what is
   available:

   | File | Check | Why |
   | --- | --- | --- |
   | Semester and monthly (closed periods) | One-byte range request for the size; skipped if it equals the recorded size | Large files that rarely change. A republication that changes the size is downloaded again. |
   | Last four weeks | Always downloaded, then compared by SHA-256 | Replaced every week under the same name; a size check could miss it. About 14 MB in total. |

4. **Download.** Streamed to a temporary file while the hash is computed. A
   transfer shorter than the announced length is retried, and an HTML page
   served in place of a data file is rejected.

Known limit: a closed-period file republished with **exactly the same size**
but different content is not detected by the size check. `--force` downloads
everything again and compares by content.

### Default start: January 2022

From 2022 onwards the automotive semester files are zipped, about 9 MB each.
Before that they are plain CSV of up to 157 MB each. The default covers enough
history for the analysis while keeping a first run to a few minutes; `--since`
goes further back when wanted.

## Banco Central SGS: API

Series collected: **1** (US dollar selling rate, daily) and **433** (IPCA,
monthly change).

- **Windows.** Daily series are requested in windows of at most ten years,
  which is what the API accepts. Monthly series go in a single request.
- **Incremental with overlap.** The API has no "changed since" filter. Each run
  asks again from a few weeks before the last observation it holds (30 days
  for the daily series, 100 for the monthly one) up to today. The overlap is
  what picks up late publications and revised values. The staged layer must
  therefore keep, for each date, the value from the most recently fetched
  response.
- **Empty windows.** The API answers `404` when a window has no observations.
  That is recorded as "empty", not as a failure. A `404` for a series that does
  not exist has a different content type and is treated as an error.
- **Stored as received.** The response body is saved untouched; values stay as
  the strings the API sends.
- A response that is identical to one already stored for the same request is
  not stored again.

## Failure handling

| Situation | Behaviour |
| --- | --- |
| Timeout, connection error, HTTP 429/500/502/503/504 | Retried up to 5 times with exponential backoff and jitter |
| Truncated or empty download | Retried |
| Other HTTP errors (403, 404, …) | Not retried; the file is marked failed |
| One file fails | The rest still run; exit code is `1`; the next run picks the file up |
| Response with an unexpected shape | Rejected; nothing is written to the raw layer |

Requests are sequential with a one-second pause between them (`--pause`).

## Not in this stage

- Reading, typing or validating the contents of the files (stage 2).
- Publishing the manifest and data as release assets, and the weekly schedule
  (stage 5). Until then the manifest lives in the local `data/` directory.
