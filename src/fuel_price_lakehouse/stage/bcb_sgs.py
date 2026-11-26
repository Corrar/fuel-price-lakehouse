"""Staging of Banco Central SGS windows: typed observations, or quarantine with the reason.

A raw window is what the API answered for one series and one calendar year,
with the date and the value still as text. Here every row either becomes a
typed observation in the staged layer or is kept, untouched, in quarantine
together with the ids of the rules it broke.

Two decisions shape the rules:

- A date that cannot be read is reported once, under ``observed_on_is_a_date``.
  The rules that need the date let such a row pass, so each rule counts a
  different problem.
- When a window carries the same date twice, every row of that date is
  rejected. Nothing in the response says which value is the right one, and a
  gap in a series is visible where a wrong value is not.
"""

from __future__ import annotations

from datetime import UTC

import numpy as np
import pandas as pd
import pyarrow as pa
from pandera.pandas import Check, Column, DataFrameSchema

from fuel_price_lakehouse.config import Clock
from fuel_price_lakehouse.stage import text
from fuel_price_lakehouse.stage.model import DATE, ID_LIST, SchemaDriftError, StagedFrames, split
from fuel_price_lakehouse.stage.rules import FileContext, Rule, Severity

SOURCE_COLUMNS: tuple[str, ...] = ("data", "valor")


def _within_window_year(frame: pd.DataFrame, context: FileContext) -> pd.Series:
    observed_on = frame["observed_on"]
    return observed_on.isna() | (observed_on.dt.year == int(context.details["year"]))


def _date_unique(frame: pd.DataFrame, _context: FileContext) -> pd.Series:
    observed_on = frame["observed_on"]
    return observed_on.isna() | ~observed_on.duplicated(keep=False)


def _not_in_future(frame: pd.DataFrame, context: FileContext) -> pd.Series:
    # The collection day in UTC is never behind the day in Brazil, where the series is dated.
    collected_on = pd.Timestamp(context.collected_at.astimezone(UTC).date())
    return frame["observed_on"] <= collected_on


RULES: tuple[Rule, ...] = (
    Rule(
        "observed_on_is_a_date",
        Severity.ERROR,
        "The observation date is a real calendar date written as dd/mm/yyyy.",
        "A data da observação é uma data real do calendário, escrita como dd/mm/aaaa.",
        lambda frame, _context: frame["observed_on"].notna(),
    ),
    Rule(
        "value_is_a_number",
        Severity.ERROR,
        "The value is a number with a dot as the decimal separator, such as 5.4372 or -0.32.",
        "O valor é um número com ponto como separador decimal, como 5.4372 ou -0.32.",
        lambda frame, _context: frame["value"].notna(),
    ),
    Rule(
        "observed_on_within_window_year",
        Severity.ERROR,
        "The observation date belongs to the calendar year the request was made for.",
        "A data da observação pertence ao ano-calendário para o qual a consulta foi feita.",
        _within_window_year,
    ),
    Rule(
        "date_unique_in_window",
        Severity.ERROR,
        "No other row of the same response has the same observation date.",
        "Nenhuma outra linha da mesma resposta tem a mesma data de observação.",
        _date_unique,
    ),
    Rule(
        "observed_on_not_in_future",
        Severity.WARNING,
        "The observation date is not later than the day the response was collected.",
        "A data da observação não é posterior ao dia em que a resposta foi coletada.",
        _not_in_future,
    ),
)

_TRACKING = (
    pa.field("_source_url", pa.string(), nullable=False),
    pa.field("_source_sha256", pa.string(), nullable=False),
    pa.field("_source_row_number", pa.int64(), nullable=False),
    pa.field("_ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
)

STAGED_ARROW_SCHEMA = pa.schema(
    [
        pa.field("series_code", pa.int32(), nullable=False),
        pa.field("observed_on", pa.date32(), nullable=False),
        pa.field("value", pa.float64(), nullable=False),
        *_TRACKING,
        pa.field("_warnings", pa.list_(pa.string()), nullable=False),
    ]
)

QUARANTINE_ARROW_SCHEMA = pa.schema(
    [
        # Null when the response did not have the field at all for that row.
        pa.field("data", pa.string()),
        pa.field("valor", pa.string()),
        *_TRACKING,
        pa.field("series_code", pa.int32(), nullable=False),
        pa.field("_rule_ids", pa.list_(pa.string()), nullable=False),
        pa.field("_quarantined_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

STAGED_SCHEMA = DataFrameSchema(
    {
        "series_code": Column("int32", Check.gt(0)),
        "observed_on": Column(DATE),
        "value": Column("float64"),
        "_source_url": Column(str),
        "_source_sha256": Column(str, Check.str_matches(r"^[0-9a-f]{64}$")),
        "_source_row_number": Column("int64", Check.ge(1)),
        "_ingested_at": Column("datetime64[us, UTC]"),
        "_warnings": Column(ID_LIST),
    },
    strict=True,
    ordered=True,
    unique=["series_code", "observed_on"],
)


def stage(raw: pd.DataFrame, context: FileContext, *, now: Clock) -> StagedFrames:
    """Split one raw window into typed observations and quarantined rows."""
    # A field the API adds is not drift: it stays in the raw layer and is not read here.
    missing = [column for column in SOURCE_COLUMNS if column not in raw.columns]
    if missing:
        raise SchemaDriftError(
            f"{context.key}: source columns {missing} not found; the file has {list(raw.columns)}"
        )
    frames = split(
        _candidates(raw, context),
        RULES,
        context,
        now=now,
        staged_schema=STAGED_ARROW_SCHEMA,
        quarantine_schema=QUARANTINE_ARROW_SCHEMA,
    )
    # A staged table that breaks its contract is a bug here, not bad data: it raises.
    STAGED_SCHEMA.validate(frames.staged)
    return frames


def _candidates(raw: pd.DataFrame, context: FileContext) -> pd.DataFrame:
    """Every raw row with what its text would be once typed: missing where it is not valid."""
    return pd.DataFrame(
        {
            "data": raw["data"],
            "valor": raw["valor"],
            "series_code": np.int32(int(context.details["series"])),
            "observed_on": text.parse_date(raw["data"]),
            # The API writes numbers with a dot, and a sign only when negative
            # (the IPCA has months of deflation).
            "value": text.parse_decimal(raw["valor"], separator=".", signed=True),
            "_source_url": raw["_source_url"],
            "_source_sha256": raw["_source_sha256"],
            "_source_row_number": raw["_row_number"],
            "_ingested_at": raw["_collected_at"],
        }
    )
