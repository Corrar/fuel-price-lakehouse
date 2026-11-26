# 0003 — Validation, quarantine, and what is incremental

- Status: accepted
- Date: 2026-11-26

## Context

The raw layer keeps every value as the text the source sent. Between that and
a table someone can analyse there are three jobs: give values a type, decide
which rows can be trusted, and remove the repetition between files (ANP
publishes the same weeks in semester, monthly and rolling four-week files).

What the collected history looks like matters for the design. In 6,974,928 rows
since 2020 the profiling found very little that is wrong: 9,114 blank rows at
the end of one file, 16 exact duplicates, 4 keys with two prices, and format
differences between files (a CNPJ written with and without its mask, `m³`
written as `m3`, one file in another encoding). The source is clean, and it
changes its formatting without notice. The validation therefore has two
purposes: catch the occasional bad row, and notice the day a file arrives in a
shape nobody has seen.

## Decisions

### 1. Rows are quarantined, never dropped

Every raw row ends up in exactly one of two places: the staged layer, or the
quarantine layer with the ids of the rules it broke. For every file,
`rows read = rows staged + rows quarantined`, and the pipeline checks it.

A row that is silently dropped is a number that no longer adds up, and nobody
can tell whether the filter or the source was wrong. A quarantined row keeps
the original text of all its fields, so a rule that turns out to be too strict
can be fixed and the rows staged again from the raw layer.

### 2. Rules are data, with two severities

A rule is a small object: an id, a severity, a one-sentence description in
English and Portuguese, and a function that says, for a whole file at once,
which rows pass. `error` rules send the row to quarantine; `warning` rules keep
the row and record the id on it.

The ids are a public contract. They are stored in the quarantine files, counted
in the quality report on the dashboard, and listed in
[`../quality-rules.md`](../quality-rules.md), which is generated from the code
and checked by a test so the document cannot drift from what runs.

Bounds are sanity bounds, not statistics. The price range of each product is
wide enough for the whole history back to 2004 and catches unit and typing
mistakes (599 for 5,99). Finer judgement ("this price is far from the others
that week") is a warning, because an unusual price is not a wrong price.

### 3. A contract on top of the rules

After the rules have split a file, the staged half is validated against a
Pandera schema: column names, types, nullability, value sets. This is not a
second set of data rules. It is a check on the pipeline itself: if the contract
fails, the staging code has a bug, and the run stops instead of routing the
problem to quarantine. Downstream code can rely on the contract without
re-checking.

### 4. A file with an unknown shape fails as a whole

If a raw file does not have the columns the source is known to publish, no
attempt is made to guess which column is which. The file is marked as failed,
produces no output, and the quality gate blocks publication. One file changing
shape is exactly the event a person should look at.

### 5. Incremental where it is expensive, full rebuild where it is cheap

| Step | Mode | Why |
| --- | --- | --- |
| Collection | Incremental, per source file (manifest: size and SHA-256) | The network is the slowest and least reliable part. |
| Staging | Incremental, per raw file (raw SHA-256 and a staging version) | Parsing and validating millions of rows takes minutes; a file that did not change gives the same result. |
| Curated tables | Rebuilt in full on every run | A few seconds in DuckDB. A full rebuild cannot leave a stale partition behind, and de-duplication across files needs all of them anyway. |

Changing a rule means bumping the staging version, which re-stages every file
from the raw layer on the next run. No migration, no backfill script.

### 6. De-duplication happens once, across files, with a fixed precedence

The staged layer keeps one file per raw file, repetition included. The curated
fact table keeps one row per station, product and collection date. When several
files carry the same key, the semester file wins over the monthly one, which
wins over the rolling four-week one: the closed files can be downloaded again,
the rolling one cannot. In the collected history the choice never changes a
price (the same key never has two prices across files); it fixes the lineage of
each row. How many candidates a key had, and whether they disagreed on the
price, is kept on the row.

## Consequences

- The quality report can show, per rule and per file, how many rows were caught,
  and the totals reconcile with the row counts of the source files.
- Adding a rule is a few lines and one test, and the documentation and the
  dashboard pick it up by themselves.
- A stricter source of truth (for example, checking each CNPJ against the
  federal registry) is out of scope: the pipeline validates the data against
  itself and against the conventions of the source, not against the world.
