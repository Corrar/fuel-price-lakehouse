# 0001 — Where data lives between pipeline runs

- Status: accepted
- Date: 2026-11-26

## Context

The pipeline will run weekly on GitHub Actions. Every run starts on a clean
machine, so anything the next run needs has to be stored somewhere else:

- the **collection manifest** (which upstream files were already ingested,
  with size and checksum), without which the collection cannot be incremental;
- the **Parquet layers** (staged, quarantine, curated), so a run only processes
  what is new instead of rebuilding the whole history.

Constraints: no paid service, no secrets beyond the token GitHub Actions
already provides, data must stay out of git history, and anyone should be able
to download the output without an account.

## Options considered

Limits below are quoted from the GitHub documentation as read on 2026-11-26.

| Option | Limits | Verdict |
| --- | --- | --- |
| **Release assets** | Each file under 2 GiB, up to 1000 assets per release, no limit on total size or bandwidth | **Chosen** |
| Actions cache | 10 GB per repository; entries not accessed in over 7 days are removed | Rejected: a cache may disappear at any time, and a weekly job sits right on the 7-day eviction edge |
| Actions artifacts | Retention is capped by repository settings and artifacts expire | Rejected: expiry means the state is eventually lost; downloading requires a GitHub login |
| Commit data to a branch | Files over 100 MiB are blocked; repositories are recommended to stay under 1 GB | Rejected: every weekly rewrite of a Parquet file stays in history forever |
| Git LFS | 10 GiB storage and 10 GiB bandwidth per month on the free plan | Rejected: a bandwidth quota that readers of a public repo can exhaust |
| External object storage (free tiers) | Varies by provider | Rejected: needs an extra account and a long-lived secret in CI, and free tiers change |

## Decision

Store the state as **assets of a GitHub release in this repository**.

- One rolling release, tag `data-latest`, holds the current state: the
  collection manifest plus the Parquet files.
- Each run downloads the manifest (and only the partitions it needs), processes
  what is new, and uploads the changed files with the workflow's own
  `GITHUB_TOKEN` (`contents: write` on that job only). No extra secret.
- The manifest is uploaded **last**. If a run fails halfway, the old manifest
  still describes a consistent state and the next run redoes the work.
- Raw files are **not** stored: the upstream source is public and the manifest
  records the URL, size and checksum of every file ingested, so the raw layer
  can be downloaded again.
- Files are split by period (not one giant file), which keeps every asset far
  below the 2 GiB limit and lets a run replace only the periods that changed.

## Consequences

- The published data is a plain download link, readable directly by DuckDB over
  HTTPS, with no account needed.
- Release assets are not versioned: replacing a file loses the previous one.
  Accepted, because everything can be rebuilt from the public sources. Dated
  snapshot releases can be added later if history of the outputs is wanted.
- Replacing an asset is delete-then-upload, not atomic. The manifest-last rule
  above is what keeps a failed run from corrupting the state.
- A release is limited to 1000 assets, so partition granularity has to be
  chosen with that in mind (for example yearly files rather than weekly ones).
- GitHub disables scheduled workflows in public repositories after 60 days
  without repository activity. Uploading release assets may not count as
  activity, so the weekly job needs a way to detect or avoid this (to be
  handled when the schedule is added).
- Publishing the Parquet files means redistributing data derived from ANP. The
  licence that applies to the ANP files is not settled (see
  [`../sources.md`](../sources.md), section 1.6). It has to be settled before
  the first data release is made public; if redistribution turns out not to be
  allowed, the same mechanism still works with the manifest and the quality
  report only.
