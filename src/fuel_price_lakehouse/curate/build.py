"""Build the curated tables from the staged layer: everything, on every run.

Collection and staging are incremental because the network and the parsing are
expensive. This step is not: DuckDB rebuilds every table from the staged files
in seconds, and a full rebuild cannot leave a stale partition behind
(``docs/decisions/0003-validation-and-quarantine.md``, decision 5).

How a build goes:

1. The staged files are taken from the staging state, never from a glob: a
   file left in the folder by an old run must not leak into the tables.
2. The SQL files of ``curate/sql`` run in order. Each table is written to
   Parquet as soon as it exists and is from then on read from that file, so the
   tables that come after it, and the contracts, see the bytes that will be
   published, and the 7 million rows of the fact table do not stay in memory.
3. Every table is checked against its contract (``curate/tables.py``).
4. Only then do the files move from the build folder to their place. A build
   that fails, at any point, leaves the previous tables untouched.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import duckdb

from fuel_price_lakehouse.config import Clock, utc_now
from fuel_price_lakehouse.curate.tables import (
    MIN_STATIONS_PUBLISHED,
    TABLES,
    ContractError,
    Table,
    violations,
)
from fuel_price_lakehouse.layout import Source, curated_dir, staging_state_path

logger = logging.getLogger(__name__)

STAGING_STATE_VERSION = 1
"""The version of ``manifest/staging.json`` this builder knows how to read."""

MEMORY_LIMIT = "2GB"
"""What DuckDB may hold in memory; beyond it, it spills to the build folder."""
THREADS = 4
"""The cores of a GitHub-hosted runner for a public repository; more threads would
only add memory."""

SQL_STEPS = (
    "survey_week",
    "round_published",
    "fuel_price",
    "station",
    "usd_brl_daily",
    "ipca_monthly",
    "first_full_week",
    "weekly_cell",
    "agg_price_weekly_national",
    "agg_price_weekly_region",
    "agg_price_weekly_state",
    "agg_price_weekly_flag",
)
"""The files of ``curate/sql``, in the order they run. Each one creates the
table, macro or variable it is named after; the ones that are not in the table
registry are helpers that never leave the database."""

REPORT_NAME = "_build.json"
_BUILD_FOLDER = ".build"
_TABLE_BY_NAME = {table.name: table for table in TABLES}

# What the SQL reads from the staged layer: one view per source, with the staged
# columns it uses. The types only matter when nothing is staged yet and the view
# has to be an empty table of the right shape.
_STAGED_VIEWS: dict[str, tuple[str, dict[str, str]]] = {
    Source.ANP: (
        "staged_fuel_price",
        {
            "region_code": "VARCHAR",
            "state_code": "VARCHAR",
            "municipality": "VARCHAR",
            "station_name": "VARCHAR",
            "station_cnpj": "VARCHAR",
            "street": "VARCHAR",
            "street_number": "VARCHAR",
            "address_complement": "VARCHAR",
            "neighbourhood": "VARCHAR",
            "postal_code": "VARCHAR",
            "product": "VARCHAR",
            "collected_on": "DATE",
            "sale_price": "DOUBLE",
            "purchase_price": "DOUBLE",
            "unit": "VARCHAR",
            "brand": "VARCHAR",
            "_source_kind": "VARCHAR",
            "_source_period": "VARCHAR",
            "_source_url": "VARCHAR",
            "_source_row_number": "BIGINT",
            "_ingested_at": "TIMESTAMPTZ",
        },
    ),
    Source.BCB_SGS: (
        "staged_sgs",
        {"series_code": "INTEGER", "observed_on": "DATE", "value": "DOUBLE"},
    ),
}


@dataclass(frozen=True)
class CurateReport:
    """What one build did. Saved next to the tables as ``_build.json``."""

    built_at: str
    rows_staged: int
    """Staged fuel price rows read, repetition between files included."""
    rows_curated: int
    """Rows of ``fuel_price``: ``rows_staged - duplicates_removed``."""
    duplicates_removed: int
    price_conflicts: int
    """Rows of ``fuel_price`` whose candidates disagreed on the sale price."""
    cells_suppressed: int
    """Aggregate rows left out of the public tables for having too few stations."""
    partial_weeks_dropped: int
    """Survey weeks at the start of the history left out of the aggregates because
    the files do not cover all of their days (``sql/first_full_week.sql``)."""
    tables: dict[str, int]
    """Rows per table."""


def build(root: Path, *, now: Clock = utc_now) -> CurateReport:
    """Rebuild every curated table under ``root`` and say what was done.

    Raises ``ContractError`` when a table comes out wrong; the tables of the
    previous build are then still in place.
    """
    built_at = now()
    curated = curated_dir(root)
    workspace = curated / _BUILD_FOLDER
    shutil.rmtree(workspace, ignore_errors=True)  # left behind by a build that was killed
    workspace.mkdir(parents=True)
    try:
        with _connect(workspace) as connection:
            _create_staged_views(connection, root)
            rows: dict[str, int] = {}
            for step in SQL_STEPS:
                started = time.perf_counter()
                connection.execute(_sql(step))
                if step in _TABLE_BY_NAME:
                    rows[step] = _export(
                        connection, _TABLE_BY_NAME[step], workspace, empty_year=built_at.year
                    )
                logger.info("curate: %-25s %5.1fs", step, time.perf_counter() - started)
            _check_contracts(connection)
            report = _report(connection, built_at.isoformat(), rows)
        for table in TABLES:
            _promote(table, workspace, curated)
        _write_json(curated / REPORT_NAME, asdict(report))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    logger.info(
        "curate: %s staged rows -> %s in fuel_price (%s repeated, %s price conflicts); "
        "%s small cells suppressed, partial weeks left out of the aggregates: %s",
        f"{report.rows_staged:,}",
        f"{report.rows_curated:,}",
        f"{report.duplicates_removed:,}",
        report.price_conflicts,
        f"{report.cells_suppressed:,}",
        report.partial_weeks_dropped,
    )
    return report


def _connect(workspace: Path) -> duckdb.DuckDBPyConnection:
    """An in-memory database: nothing of it survives the build except the Parquet files.

    DuckDB draws a progress bar on standard output for any query longer than two
    seconds, terminal or not; in the log of a scheduled run it is only noise, and
    the build logs the time of every step instead.
    """
    connection = duckdb.connect(
        config={
            "memory_limit": MEMORY_LIMIT,
            "threads": THREADS,
            "temp_directory": str(workspace / "duckdb"),
        }
    )
    connection.execute("SET enable_progress_bar_print = false")
    connection.execute(f"SET VARIABLE min_stations_published = {MIN_STATIONS_PUBLISHED}")
    return connection


def _staged_files(root: Path) -> dict[str, list[Path]]:
    """The staged files of each source, as listed in the staging state."""
    files: dict[str, list[Path]] = {source: [] for source in _STAGED_VIEWS}
    path = staging_state_path(root)
    if not path.exists():
        logger.warning("curate: %s not found, building empty tables", path)
        return files
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("version") != STAGING_STATE_VERSION:
        raise ValueError(f"{path}: unsupported staging state version {state.get('version')!r}")
    for key in sorted(state["files"]):  # a fixed order, whatever order the state was written in
        record = state["files"][key]
        if record["staged_path"]:
            files[record["source"]].append(root / record["staged_path"])
    return files


def _create_staged_views(connection: duckdb.DuckDBPyConnection, root: Path) -> None:
    """One view per source over exactly its listed files, so the SQL never names a path."""
    for source, files in _staged_files(root).items():
        view, columns = _STAGED_VIEWS[source]
        if files:
            listed = ", ".join(_literal(file) for file in files)
            select = f"SELECT {', '.join(columns)} FROM read_parquet([{listed}])"
        else:
            typed = ", ".join(f"CAST(NULL AS {kind}) AS {name}" for name, kind in columns.items())
            select = f"SELECT {typed} WHERE false"
        connection.execute(f"CREATE VIEW {view} AS {select}")
        logger.info("curate: %s reads %d staged files", view, len(files))


def _sql(step: str) -> str:
    # Read as a package resource, so it also works from an installed wheel.
    files = resources.files("fuel_price_lakehouse.curate")
    return files.joinpath("sql", f"{step}.sql").read_text(encoding="utf-8")


def _export(
    connection: duckdb.DuckDBPyConnection, table: Table, workspace: Path, *, empty_year: int
) -> int:
    """Write ``table`` to the workspace, read it from there from now on, return its rows.

    Rows are written sorted by the key, so two builds of the same staged files
    give the same files, row for row.
    """
    columns = ", ".join(f'"{column.name}"' for column in table.columns)
    rows = f"SELECT {columns} FROM {table.name}"
    order = f"ORDER BY {', '.join(table.key)}"
    if table.partitioned_by_year_of is None:
        written = _copy(connection, f"{rows} {order}", workspace / table.path)
    else:
        year = f"year({table.partitioned_by_year_of})"
        years = connection.execute(f"SELECT DISTINCT {year} FROM {table.name}").fetchall()
        # A table without rows still gets one file, so that it can be read and
        # has its columns: an empty partition for the year of the build.
        written = sum(
            _copy(
                connection,
                f"{rows} WHERE {year} = {value} {order}",
                workspace / table.path.replace("*", str(value)),
            )
            for (value,) in sorted(years) or [(empty_year,)]
        )
    connection.execute(
        f"DROP TABLE {table.name};"
        f"CREATE VIEW {table.name} AS SELECT * FROM read_parquet("
        f"{_literal(workspace / table.path)}, hive_partitioning = false)"
    )
    return written


def _copy(connection: duckdb.DuckDBPyConnection, query: str, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    (written,) = _row(
        connection, f"COPY ({query}) TO {_literal(destination)} (FORMAT parquet, COMPRESSION zstd)"
    )
    return int(written)


def _check_contracts(connection: duckdb.DuckDBPyConnection) -> None:
    problems = [problem for table in TABLES for problem in violations(connection, table)]
    problems += _unaccounted_staged_rows(connection)
    if problems:
        raise ContractError("curated tables broke their contract:\n" + "\n".join(problems))


def _unaccounted_staged_rows(connection: duckdb.DuckDBPyConnection) -> list[str]:
    """A problem when the fact rows do not stand for every staged row exactly once.

    De-duplication may merge the rows that share a key, never lose one: every
    staged row is one of the ``n_candidates`` of exactly one fact row. Without
    this check ``duplicates_removed`` would count a row lost by mistake as a
    repeat, and the funnel of the quality report would still add up.
    """
    staged, candidates = _row(
        connection,
        "SELECT (SELECT count(*) FROM staged_fuel_price),"
        " (SELECT coalesce(sum(n_candidates), 0) FROM fuel_price)",
    )
    if staged == candidates:
        return []
    return [f"fuel_price: its rows stand for {candidates:,} staged rows, {staged:,} were staged"]


def _report(
    connection: duckdb.DuckDBPyConnection, built_at: str, rows: dict[str, int]
) -> CurateReport:
    # Every staged row is a candidate of one fact row (checked by _unaccounted_staged_rows).
    rows_staged, price_conflicts = _row(
        connection,
        "SELECT coalesce(sum(n_candidates), 0), count(*) FILTER (WHERE price_conflict) "
        "FROM fuel_price",
    )
    (cells_suppressed,) = _row(
        connection,
        "SELECT count(*) FROM weekly_cell WHERE n_stations < getvariable('min_stations_published')",
    )
    (partial_weeks_dropped,) = _row(
        connection,
        "SELECT count(DISTINCT survey_week) FROM fuel_price"
        " WHERE survey_week < getvariable('first_full_week')",
    )
    return CurateReport(
        built_at=built_at,
        rows_staged=rows_staged,
        rows_curated=rows["fuel_price"],
        duplicates_removed=rows_staged - rows["fuel_price"],
        price_conflicts=price_conflicts,
        cells_suppressed=cells_suppressed,
        partial_weeks_dropped=partial_weeks_dropped,
        tables=rows,
    )


def _promote(table: Table, workspace: Path, curated: Path) -> None:
    """Move a finished table from the workspace to its place, as a whole."""
    entry = Path(table.path).parts[0]  # the file, or the folder of a partitioned table
    built, live = workspace / entry, curated / entry
    if live.is_dir():
        # A folder cannot be renamed over another one. The old partitions step
        # aside first, so none of them is ever read next to the new ones.
        live.rename(workspace / f"{entry}.replaced")
    built.replace(live)


def _write_json(path: Path, document: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _row(connection: duckdb.DuckDBPyConnection, query: str) -> tuple[Any, ...]:
    """The single row ``query`` returns."""
    (row,) = connection.execute(query).fetchall()
    return tuple(row)


def _literal(path: Path) -> str:
    """``path`` as a SQL string literal."""
    return "'" + str(path).replace("'", "''") + "'"
