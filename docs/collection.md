# Collection (stage 1)

How source files get into the raw layer, and how a run knows what it already
has. The facts about the sources that drive these choices are in
[`sources.md`](sources.md).

## Running it

```sh
make collect                                     # everything that is new since the last run
uv run fuel-lakehouse collect --dry-run          # show the plan and its size, download nothing
uv run fuel-lakehouse collect --source sgs       # one source only (anp | sgs | all)
uv run fuel-lakehouse collect --since 2019-01    # start earlier than the default
uv run fuel-lakehouse collect --force            # fetch everything again and compare by content
```

The command prints one line per file or request, then a total. The exit code
is `1` if anything failed, so a scheduler can alert on it.

## What ends up on disk

```
data/
├── manifest/collection.jsonl
└── raw/
    ├── anp/<dataset>/grain=<grain>/period=<period>/sha256=<12 hex>/<original file name>
    └── bcb_sgs/sgs_<code>/year=<yyyy>/sha256=<12 hex>/data.json
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
   takes the smallest set that covers the requested period: the semester
   files, a monthly file only when no semester file covers that month, and
   always the rolling files.
3. **Decide whether to download.** The source sends no `ETag` or
   `Last-Modified` and refuses `HEAD`, so change detection is done with what is
   available:

   | File | Check | Why |
   | --- | --- | --- |
   | Semester and monthly (closed periods) | One-byte range request for the size; skipped if it equals the recorded size | Large files that rarely change. A republication that changes the size is downloaded again. |
   | Last four weeks | Always downloaded, then compared by SHA-256 | Replaced every week under the same name; a size check could miss it. About 14 MB in total. |

4. **Download.** Streamed to a temporary file while the hash is computed. A
   transfer shorter than the announced length is retried. Something that is
   plainly not data is refused: markup served in place of a file (an error
   page with status 200), or a `.zip` that is not a ZIP archive.

Known limit: a closed-period file republished with **exactly the same size**
but different content is not detected by the size check. `--force` downloads
everything again and compares by content.

### Default start: January 2022

| History starting in | Files | First download |
| --- | ---: | ---: |
| 2022 (default) | 33 | 287 MB |
| 2020 | 41 | 603 MB |
| 2010 | 81 | 3,045 MB |
| 2004 (everything) | 105 | 4,813 MB |

From 2022 on the automotive semester files are zipped, 7 to 17 MB each. Before
that they are plain CSV of up to 158 MB each, which is where the size goes.
The default covers enough history for the analysis while keeping the first
run to a few minutes; `--since` goes further back when wanted. A first run
with the default took about six and a half minutes, and the run after it 77
seconds.

## Banco Central SGS: API

Series collected: **1** (US dollar selling rate, daily) and **433** (IPCA,
monthly change).

- **One request per calendar year.** The API has no "changed since" filter and
  refuses more than ten years of a daily series per request. Asking for one
  whole calendar year at a time stays far below that limit and makes the
  request for a given year identical on every run.
- **Which years are requested.** A year that is over is fetched once. The
  current year is fetched on every run, and so is the previous year for a
  while after it ends (30 days for the daily series, 100 for the monthly one).
  That is what picks up new observations, late publications and revised
  values in recent data. The staged layer must therefore keep, for each date,
  the value from the most recently fetched response.
- **No holes.** A year counts as done only when the manifest holds a response
  for it. A request that fails is made again by the next run, wherever it sits
  in the history.
- **Empty windows.** The API answers `404` when a window has no observations.
  That is recorded as "empty", not as a failure. A `404` for a series that does
  not exist has a different content type and is treated as an error.
- **Stored as received.** The response body is saved untouched; values stay as
  the strings the API sends.
- **No pile of copies.** A response identical to the one already stored for
  that year is not stored again, so a series that has not changed adds nothing.

Known limit: values in years that are already closed are not checked again
unless `--force` is used. Whether Banco Central revises old values has not
been measured.

## Failure handling

| Situation | Behaviour |
| --- | --- |
| Timeout, connection error, HTTP 429 or any 5xx | Retried up to 5 times with exponential backoff and jitter |
| Truncated or empty download | Retried |
| Other HTTP errors (403, 404, …) | Not retried; the file is marked failed |
| One file or request fails | The rest still run; exit code is `1`; the next run picks it up |
| The ANP landing page cannot be read or has no data links | Reported as one failure; the other source is still collected |
| Response with an unexpected shape | Rejected; nothing is written to the raw layer |

Requests are sequential with a one-second pause between them (`--pause`).

## Not in this stage

- Reading, typing or validating the contents of the files (stage 2).
- Publishing the manifest and data as release assets, and the weekly schedule
  (stage 5). Until then the manifest lives in the local `data/` directory.
