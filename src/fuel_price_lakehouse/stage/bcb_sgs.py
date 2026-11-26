"""Staging of Banco Central SGS windows: typed observations, or quarantine with the reason.

A raw window is what the API answered for one series and one calendar year,
with the date and the value still as text. Here every row either becomes a
typed observation in the staged layer or is kept, untouched, in quarantine
together with the ids of the rules it broke.

Three decisions shape the rules:

- A date that cannot be read is reported once, under ``observed_on_is_a_date``.
  The rules that need the date let such a row pass, so each rule counts a
  different problem.
- When a window carries the same date twice, every row of that date is
  rejected. Nothing in the response says which value is the right one, and a
  gap in a series is visible where a wrong value is not.
- Warning rules run on the rows that passed the error rules: ``_warnings`` and
  the warning counts describe the staged table, not the rejected rows.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyarrow as pa
from pandera.pandas import Check, Column, DataFrameSchema

from fuel_price_lakehouse.config import Clock
from fuel_price_lakehouse.stage import text
from fuel_price_lakehouse.stage.model import SchemaDriftError, StagedFrames
from fuel_price_lakehouse.stage.rules import FileContext, Rule, Severity, evaluate

SOURCE_COLUMNS: tuple[str, ...] = ("data", "valor")

# The API writes numbers with a dot, and a sign only when negative (the IPCA
# has months of deflation). Anything else is not guessed at.
_NUMBER_TEXT = r"-?[0-9]+(\.[0-9]+)?"

_DATE32 = pd.ArrowDtype(pa.date32())
_ID_LIST = pd.ArrowDtype(pa.list_(pa.string()))


def _within_window_year(frame: pd.DataFrame, context: FileContext) -> pd.Series:
    observed_on = frame["observed_on"]
    return observed_on.isna() | (observed_on.dt.year == int(context.details["year"]))


def _date_unique(frame: pd.DataFrame, _context: FileContext) -> pd.Series:
    observed_on = frame["observed_on"]
    return observed_on.isna() | ~observed_on.duplicated(keep=False)


def _not_in_future(frame: pd.DataFrame, context: FileContext) -> pd.Series:
    return frame["observed_on"] <= pd.Timestamp(context.collected_at.date())


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
        "observed_on": Column(_DATE32),
        "value": Column("float64"),
        "_source_url": Column(str),
        "_source_sha256": Column(str, Check.str_matches(r"^[0-9a-f]{64}$")),
        "_source_row_number": Column("int64", Check.ge(1)),
        "_ingested_at": Column("datetime64[us, UTC]"),
        "_warnings": Column(_ID_LIST),
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
    candidates = _candidates(raw, context)
    errors = evaluate(_rules(Severity.ERROR), candidates, context)
    # An explicit bool: with no rows the mask has dtype object, which pandas
    # would take for a list of column labels.
    rejected = errors.rejected.astype(bool)
    accepted = candidates[~rejected]
    warnings = evaluate(_rules(Severity.WARNING), accepted, context)

    staged = accepted.assign(
        observed_on=accepted["observed_on"].astype(_DATE32),
        _warnings=warnings.warning_ids.astype(_ID_LIST),
    )
    quarantine = candidates[rejected].assign(
        _rule_ids=errors.error_ids[rejected].astype(_ID_LIST), _quarantined_at=now()
    )
    return StagedFrames(
        staged=STAGED_SCHEMA.validate(_shaped(staged, STAGED_ARROW_SCHEMA)),
        quarantine=_shaped(quarantine, QUARANTINE_ARROW_SCHEMA),
        error_counts=errors.error_counts,
        warning_counts=warnings.warning_counts,
    )


def _candidates(raw: pd.DataFrame, context: FileContext) -> pd.DataFrame:
    """Every raw row with what its text would be once typed: missing where it is not valid."""
    value_text = raw["valor"]
    values = pd.to_numeric(value_text.where(value_text.str.fullmatch(_NUMBER_TEXT)))
    return pd.DataFrame(
        {
            "data": raw["data"],
            "valor": value_text,
            "series_code": np.int32(int(context.details["series"])),
            "observed_on": text.parse_date(raw["data"]),
            # An explicit type: a window with no valid number would otherwise infer its own.
            "value": values.astype("float64"),
            "_source_url": raw["_source_url"],
            "_source_sha256": raw["_source_sha256"],
            "_source_row_number": raw["_row_number"],
            "_ingested_at": raw["_collected_at"],
        }
    )


def _rules(severity: Severity) -> list[Rule]:
    return [rule for rule in RULES if rule.severity is severity]


def _shaped(frame: pd.DataFrame, schema: pa.Schema) -> pd.DataFrame:
    """Exactly the columns of ``schema``, in its order, with a fresh index."""
    return frame[schema.names].reset_index(drop=True)
