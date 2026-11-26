"""Types and helpers shared by the staging modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


class SchemaDriftError(Exception):
    """The raw file does not have the columns this source is known to publish.

    This fails the whole file: guessing which column is which would put wrong
    values under right names.
    """


@dataclass(frozen=True)
class StagedFrames:
    """One raw file split in two: rows that passed every error rule, and the rest."""

    staged: pd.DataFrame
    quarantine: pd.DataFrame
    error_counts: dict[str, int]
    """Rows failing each error rule (a row can count under several rules)."""
    warning_counts: dict[str, int]


def write_parquet(frame: pd.DataFrame, destination: Path, schema: pa.Schema) -> None:
    """Write ``frame`` with exactly ``schema``, atomically.

    The explicit schema keeps column types stable across files (an all-null
    column would otherwise be written with a different type), and the rename
    means a reader never sees a half-written file.
    """
    table = pa.Table.from_pandas(frame, schema=schema, preserve_index=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        pq.write_table(table, temporary, compression="zstd")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
