"""Types and helpers shared by the staging modules."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from fuel_price_lakehouse.config import Clock
from fuel_price_lakehouse.stage.rules import FileContext, Rule, Severity, evaluate

DATE = pd.ArrowDtype(pa.date32())
"""A calendar date in a staged frame: the type it has in the Parquet file."""

ID_LIST = pd.ArrowDtype(pa.list_(pa.string()))
"""The rule ids a row carries (``_warnings``, ``_rule_ids``)."""


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


def split(
    candidates: pd.DataFrame,
    rules: Sequence[Rule],
    context: FileContext,
    *,
    now: Clock,
    staged_schema: pa.Schema,
    quarantine_schema: pa.Schema,
) -> StagedFrames:
    """Judge every row of one raw file and put it in the staged table or in quarantine.

    ``candidates`` has one row per raw row and every column either table needs:
    the text as it came, for the quarantine, and what that text says once read,
    for the rules and the staged table (missing where it says nothing valid).

    Error rules see every row. Warning rules see only the rows that passed
    them, so ``_warnings`` and the warning counts describe the staged table,
    and a rule that compares rows with each other is not swayed by a row that
    was rejected.
    """
    errors = evaluate(_with_severity(rules, Severity.ERROR), candidates, context)
    # An explicit bool: with no rows the mask has dtype object, which pandas
    # would take for a list of column labels.
    rejected = errors.rejected.astype(bool)
    accepted, refused = candidates[~rejected], candidates[rejected]
    warnings = evaluate(_with_severity(rules, Severity.WARNING), accepted, context)

    staged = accepted.assign(_warnings=warnings.warning_ids.astype(ID_LIST))
    quarantine = refused.assign(
        _rule_ids=errors.error_ids[rejected].astype(ID_LIST),
        # A typed column, not a bare value: a frame with no rows would not take its type.
        _quarantined_at=pd.Series(now(), index=refused.index, dtype="datetime64[us, UTC]"),
    )
    return StagedFrames(
        staged=_shaped(staged, staged_schema),
        quarantine=_shaped(quarantine, quarantine_schema),
        error_counts=errors.error_counts,
        warning_counts=warnings.warning_counts,
    )


def _with_severity(rules: Sequence[Rule], severity: Severity) -> list[Rule]:
    return [rule for rule in rules if rule.severity is severity]


def _shaped(frame: pd.DataFrame, schema: pa.Schema) -> pd.DataFrame:
    """Exactly the columns of ``schema``, in its order and with its date type, on a fresh index.

    Rules compare dates as timestamps, which is what pandas computes with; a
    table stores the plain date its schema declares.
    """
    dates = {field.name: DATE for field in schema if pa.types.is_date32(field.type)}
    return frame[schema.names].astype(dates).reset_index(drop=True)


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
