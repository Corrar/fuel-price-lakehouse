"""The public tables as the visitor downloads them, and the views every series is read from.

The tables are copied by the whitelist of ``curate/tables.py`` and checked
against their contract there, in the copy that is published. Every number of
the data files is then read from those copies, so a number on the dashboard is
a cell of a file the visitor can download.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from fuel_price_lakehouse.curate.tables import PUBLIC_TABLES, export_public, violations
from fuel_price_lakehouse.site.contract import SiteBuildError
from fuel_price_lakehouse.site.folder import DOWNLOADS


def publish_tables(root: Path, site: Path, connection: duckdb.DuckDBPyConnection) -> None:
    """Copy the public tables into the site and open one view per table over the copies.

    The contract is the registry's: column names and types, the reference lists
    of products, states and regions, weeks that start on Sunday, no cell below
    the minimum of stations, no station-level column.
    """
    downloads = site / DOWNLOADS
    export_public(root, downloads)
    for table in PUBLIC_TABLES:
        connection.read_parquet(str(downloads / table.path)).create_view(table.name)
    problems = [problem for table in PUBLIC_TABLES for problem in violations(connection, table)]
    if problems:
        raise SiteBuildError("public tables broke their contract:\n" + "\n".join(problems))


def export_csv(connection: duckdb.DuckDBPyConnection, site: Path) -> None:
    """Write every public table as CSV next to its Parquet file: same rows, same order."""
    for table in PUBLIC_TABLES:
        destination = site / DOWNLOADS / _csv_path(table.path)
        connection.view(table.name).write_csv(str(destination), header=True)


def listing(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """What the visitor can download, for ``meta.json``: every public table, in two formats."""
    downloads = []
    for table in PUBLIC_TABLES:
        ((rows,),) = connection.execute(f"SELECT count(*) FROM {table.name}").fetchall()
        downloads.append(
            {
                "table": table.name,
                "description": table.description,
                "rows": rows,
                "files": {
                    "parquet": f"{DOWNLOADS}/{table.path}",
                    "csv": f"{DOWNLOADS}/{_csv_path(table.path)}",
                },
            }
        )
    return downloads


def _csv_path(parquet_path: str) -> str:
    return Path(parquet_path).with_suffix(".csv").as_posix()
