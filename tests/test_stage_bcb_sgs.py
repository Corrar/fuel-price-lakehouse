"""Staging of SGS windows: every rule, and the shape of the two outputs."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import NamedTuple

import pandas as pd
import pandera.errors
import pyarrow.parquet as pq
import pytest

from fuel_price_lakehouse.collect.raw import Provenance, write_json_records
from fuel_price_lakehouse.stage import bcb_sgs
from fuel_price_lakehouse.stage.model import SchemaDriftError, StagedFrames, write_parquet
from fuel_price_lakehouse.stage.rules import FileContext, Severity

URL = (
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados?formato=json"
    "&dataInicial=01/01/2026&dataFinal=31/12/2026"
)
SHA256 = "ab" * 32
COLLECTED_AT = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 27, 9, 30, tzinfo=UTC)
CONTEXT = FileContext(
    "bcb_sgs/433/2026", URL, SHA256, COLLECTED_AT, {"series": "433", "year": "2026"}
)

JANUARY = {"data": "01/01/2026", "valor": "0.33"}
FEBRUARY = {"data": "01/02/2026", "valor": "0.70"}


class Staged(NamedTuple):
    frames: StagedFrames
    staged: list[dict]
    """Rows of the staged Parquet file, read back."""
    quarantine: list[dict]
    """Rows of the quarantine Parquet file, read back."""


def raw_window(tmp_path: Path, records: list[dict]) -> pd.DataFrame:
    """A raw window as the collector stores it, read the way the driver reads it."""
    path = tmp_path / "raw.parquet"
    write_json_records(json.dumps(records).encode(), path, Provenance(URL, SHA256, COLLECTED_AT))
    return pd.read_parquet(path)


def stage(tmp_path: Path, records: list[dict], context: FileContext = CONTEXT) -> Staged:
    frames = bcb_sgs.stage(raw_window(tmp_path, records), context, now=lambda: NOW)
    write_parquet(frames.staged, tmp_path / "staged.parquet", bcb_sgs.STAGED_ARROW_SCHEMA)
    write_parquet(
        frames.quarantine, tmp_path / "quarantine.parquet", bcb_sgs.QUARANTINE_ARROW_SCHEMA
    )
    return Staged(
        frames,
        pq.read_table(tmp_path / "staged.parquet").to_pylist(),
        pq.read_table(tmp_path / "quarantine.parquet").to_pylist(),
    )


def broken_rules(result: Staged) -> list[list[str]]:
    return [row["_rule_ids"] for row in result.quarantine]


def test_valid_rows_become_typed_observations(tmp_path):
    result = stage(tmp_path, [JANUARY, {"data": "01/08/2026", "valor": "-0.32"}])

    assert result.quarantine == []
    assert result.staged == [
        {
            "series_code": 433,
            "observed_on": date(2026, 1, 1),
            "value": 0.33,
            "_source_url": URL,
            "_source_sha256": SHA256,
            "_source_row_number": 1,
            "_ingested_at": COLLECTED_AT,
            "_warnings": [],
        },
        {
            "series_code": 433,
            "observed_on": date(2026, 8, 1),
            "value": -0.32,  # a month of deflation
            "_source_url": URL,
            "_source_sha256": SHA256,
            "_source_row_number": 2,
            "_ingested_at": COLLECTED_AT,
            "_warnings": [],
        },
    ]


def test_files_are_written_with_exactly_the_declared_schemas(tmp_path):
    stage(tmp_path, [JANUARY, {"data": "", "valor": "0.70"}])

    staged = pq.read_schema(tmp_path / "staged.parquet").remove_metadata()
    quarantine = pq.read_schema(tmp_path / "quarantine.parquet").remove_metadata()
    assert staged.equals(bcb_sgs.STAGED_ARROW_SCHEMA)
    assert quarantine.equals(bcb_sgs.QUARANTINE_ARROW_SCHEMA)
    assert [str(staged.field(name).type) for name in ("series_code", "observed_on", "value")] == [
        "int32",
        "date32[day]",
        "double",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "31/02/2026",  # not a day of the calendar
        "2026-02-01",  # ISO, not what the API writes
        "1/2/2026",
        "01/02/26",
        "",
        None,
    ],
)
def test_row_without_a_real_date_goes_to_quarantine(tmp_path, text):
    result = stage(tmp_path, [JANUARY, {"data": text, "valor": "0.70"}])

    assert [row["observed_on"] for row in result.staged] == [date(2026, 1, 1)]
    assert broken_rules(result) == [["observed_on_is_a_date"]]
    assert result.quarantine[0]["data"] == text  # kept exactly as it came


@pytest.mark.parametrize("text", ["0,70", "", None, "n/d", "1e3", "nan", " 0.70", ".70", "+0.70"])
def test_row_whose_value_is_not_a_number_goes_to_quarantine(tmp_path, text):
    result = stage(tmp_path, [JANUARY, {"data": "01/02/2026", "valor": text}])

    assert [row["value"] for row in result.staged] == [0.33]
    assert broken_rules(result) == [["value_is_a_number"]]
    assert result.quarantine[0]["valor"] == text


def test_value_without_decimals_is_a_number(tmp_path):
    result = stage(tmp_path, [{"data": "01/01/2026", "valor": "5"}])

    assert [row["value"] for row in result.staged] == [5.0]


def test_row_dated_outside_the_window_year_goes_to_quarantine(tmp_path):
    result = stage(tmp_path, [{"data": "31/12/2025", "valor": "0.52"}, JANUARY])

    assert [row["observed_on"] for row in result.staged] == [date(2026, 1, 1)]
    assert broken_rules(result) == [["observed_on_within_window_year"]]


def test_rows_sharing_a_date_all_go_to_quarantine(tmp_path):
    # Two values for one day: nothing says which one is right, so neither is staged.
    result = stage(tmp_path, [JANUARY, {"data": "01/01/2026", "valor": "0.35"}, FEBRUARY])

    assert [row["observed_on"] for row in result.staged] == [date(2026, 2, 1)]
    assert broken_rules(result) == [["date_unique_in_window"]] * 2
    assert [row["valor"] for row in result.quarantine] == ["0.33", "0.35"]


def test_unreadable_date_is_reported_only_as_such(tmp_path):
    # Two rows with the same unreadable date: not "outside the year", not "repeated".
    result = stage(tmp_path, [{"data": "", "valor": "0.33"}, {"data": "", "valor": "0.70"}])

    assert broken_rules(result) == [["observed_on_is_a_date"]] * 2
    assert result.frames.error_counts == {
        "observed_on_is_a_date": 2,
        "value_is_a_number": 0,
        "observed_on_within_window_year": 0,
        "date_unique_in_window": 0,
    }


def test_row_breaking_several_rules_lists_them_all(tmp_path):
    result = stage(tmp_path, [{"data": "31/12/2025", "valor": "0,52"}])

    assert broken_rules(result) == [["value_is_a_number", "observed_on_within_window_year"]]


def test_observation_dated_after_the_collection_is_kept_with_a_warning(tmp_path):
    on_the_day = {"data": "26/11/2026", "valor": "5.40"}
    day_after = {"data": "27/11/2026", "valor": "5.41"}

    result = stage(tmp_path, [on_the_day, day_after])

    assert result.quarantine == []
    assert [row["_warnings"] for row in result.staged] == [[], ["observed_on_not_in_future"]]
    assert result.frames.warning_counts == {"observed_on_not_in_future": 1}


def test_warnings_are_not_counted_on_rejected_rows(tmp_path):
    result = stage(tmp_path, [{"data": "27/11/2026", "valor": "?"}])

    assert broken_rules(result) == [["value_is_a_number"]]
    assert result.frames.warning_counts == {"observed_on_not_in_future": 0}


def test_quarantined_row_keeps_the_raw_text_and_its_origin(tmp_path):
    result = stage(tmp_path, [JANUARY, {"data": "01/02/2026", "valor": "0,70"}])

    assert result.quarantine == [
        {
            "data": "01/02/2026",
            "valor": "0,70",
            "_source_url": URL,
            "_source_sha256": SHA256,
            "_source_row_number": 2,
            "_ingested_at": COLLECTED_AT,
            "series_code": 433,
            "_rule_ids": ["value_is_a_number"],
            "_quarantined_at": NOW,
        }
    ]


def test_every_row_ends_up_staged_or_quarantined(tmp_path):
    records = [
        JANUARY,
        FEBRUARY,
        {"data": "01/02/2026", "valor": "0.71"},
        {"data": "bad", "valor": "bad"},
        {"data": "01/03/2025", "valor": "0.88"},
        {"data": "01/04/2026", "valor": "0.67"},
    ]

    result = stage(tmp_path, records)

    assert len(result.staged) == 2
    assert len(result.quarantine) == 4
    assert sorted(row["_source_row_number"] for row in result.staged + result.quarantine) == [
        1,
        2,
        3,
        4,
        5,
        6,
    ]


def test_window_with_no_valid_row_gives_an_empty_staged_table(tmp_path):
    result = stage(tmp_path, [{"data": "x", "valor": "0.33"}, {"data": "y", "valor": "0.70"}])

    assert result.staged == []
    assert len(result.quarantine) == 2
    assert list(result.frames.staged.columns) == bcb_sgs.STAGED_ARROW_SCHEMA.names


def test_missing_source_column_is_schema_drift(tmp_path):
    # The API renames a field: guessing would put wrong values under right names.
    raw = raw_window(tmp_path, [{"data": "01/01/2026", "value": "0.33"}])

    with pytest.raises(SchemaDriftError, match=r"\['valor'\] not found"):
        bcb_sgs.stage(raw, CONTEXT, now=lambda: NOW)


def test_field_added_by_the_api_is_not_schema_drift(tmp_path):
    result = stage(tmp_path, [{"data": "01/01/2026", "valor": "0.33", "datafim": "31/01/2026"}])

    assert [row["value"] for row in result.staged] == [0.33]
    assert result.quarantine == []


def test_staging_the_same_window_twice_gives_the_same_frames(tmp_path):
    raw = raw_window(tmp_path, [JANUARY, {"data": "", "valor": "0.70"}])

    first = bcb_sgs.stage(raw, CONTEXT, now=lambda: NOW)
    second = bcb_sgs.stage(raw, CONTEXT, now=lambda: NOW)

    pd.testing.assert_frame_equal(first.staged, second.staged)
    pd.testing.assert_frame_equal(first.quarantine, second.quarantine)
    assert list(raw.columns) == [
        "data",
        "valor",
        "_row_number",
        "_source_url",
        "_source_sha256",
        "_collected_at",
    ]  # the input frame is left alone


def test_contract_refuses_a_repeated_date(tmp_path):
    staged = stage(tmp_path, [JANUARY]).frames.staged

    with pytest.raises(pandera.errors.SchemaError):
        bcb_sgs.STAGED_SCHEMA.validate(pd.concat([staged, staged], ignore_index=True))


def test_contract_refuses_a_missing_value(tmp_path):
    staged = stage(tmp_path, [JANUARY]).frames.staged

    with pytest.raises(pandera.errors.SchemaError):
        bcb_sgs.STAGED_SCHEMA.validate(staged.assign(value=float("nan")))


def test_rule_ids_are_the_published_contract():
    assert [(rule.id, rule.severity) for rule in bcb_sgs.RULES] == [
        ("observed_on_is_a_date", Severity.ERROR),
        ("value_is_a_number", Severity.ERROR),
        ("observed_on_within_window_year", Severity.ERROR),
        ("date_unique_in_window", Severity.ERROR),
        ("observed_on_not_in_future", Severity.WARNING),
    ]


def test_every_rule_is_described_in_english_and_portuguese():
    for rule in bcb_sgs.RULES:
        assert rule.description.endswith(".")
        assert rule.description_pt.endswith(".")
        assert rule.description != rule.description_pt
