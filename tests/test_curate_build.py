"""The curated build, end to end: tiny staged files in, Parquet tables read back."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import fuel_price_lakehouse.curate.build as builder
from fuel_price_lakehouse.curate.build import REPORT_NAME, build
from fuel_price_lakehouse.curate.tables import TABLES, Column, ContractError
from fuel_price_lakehouse.layout import curated_dir, staging_state_path
from fuel_price_lakehouse.stage import anp, bcb_sgs

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


def clock() -> datetime:
    return NOW


def observation(station: int = 1, day: str = "2026-10-05", price: float = 6.0, **other) -> dict:
    """One staged ANP row: station number ``station`` sells gasoline in São Paulo."""
    row = {
        "region_code": "SE",
        "state_code": "SP",
        "municipality": "SAO PAULO",
        "station_name": f"POSTO {station} LTDA",
        "station_cnpj": f"{station:014d}",
        "street": "RUA A",
        "street_number": "10",
        "address_complement": None,
        "neighbourhood": "CENTRO",
        "postal_code": "01000000",
        "product": "GASOLINA",
        "collected_on": date.fromisoformat(day),
        "sale_price": price,
        "purchase_price": None,
        "unit": "BRL/L",
        "brand": "BRANCA",
    }
    return row | other


def survey(day: str, *prices: float, first_station: int = 1, **other) -> list[dict]:
    """One price per station on ``day``: stations ``first_station``, the next one, ..."""
    return [
        observation(station, day, price, **other)
        for station, price in enumerate(prices, start=first_station)
    ]


def stage_anp(
    root: Path,
    rows: list[dict],
    *,
    kind: str = "monthly",
    period: str = "2026-10",
    ingested_at: datetime = NOW,
    listed: bool = True,
) -> str:
    """Write ``rows`` as the staged file of one ANP source file. Returns its key."""
    name = f"{kind}-{period}"
    url = f"https://example.test/{name}.csv"
    tracking = {
        "_source_kind": kind,
        "_source_period": period,
        "_source_url": url,
        "_source_sha256": "0" * 64,
        "_ingested_at": ingested_at,
        "_warnings": [],
    }
    rows = [row | tracking | {"_source_row_number": n} for n, row in enumerate(rows, start=1)]
    path = (
        f"staged/anp/fuel_prices/kind={kind}/fuel_group=automotive/period={period}/{name}.parquet"
    )
    _write(root, path, rows, anp.STAGED_ARROW_SCHEMA)
    if listed:
        _list_in_state(root, url, "anp", path, len(rows))
    return url


def stage_sgs(root: Path, series: int, values: dict[str, float]) -> None:
    """Write the staged window of an SGS series: ``{"2026-10-05": 5.43, ...}``."""
    year = min(values)[:4]
    key = f"bcb_sgs/{series}/{year}"
    rows = [
        {
            "series_code": series,
            "observed_on": date.fromisoformat(day),
            "value": value,
            "_source_url": f"https://example.test/{key}",
            "_source_sha256": "0" * 64,
            "_source_row_number": n,
            "_ingested_at": NOW,
            "_warnings": [],
        }
        for n, (day, value) in enumerate(values.items(), start=1)
    ]
    path = f"staged/bcb_sgs/series/series={series}/year={year}/data.parquet"
    _write(root, path, rows, bcb_sgs.STAGED_ARROW_SCHEMA)
    _list_in_state(root, key, "bcb_sgs", path, len(rows))


def _write(root: Path, relative: str, rows: list[dict], schema: pa.Schema) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def _list_in_state(root: Path, key: str, source: str, staged_path: str | None, rows: int) -> None:
    state = read_state(root)
    state["files"][key] = {
        "source": source,
        "details": {},
        "raw_sha256": "0" * 64,
        "staging_version": 1,
        "staged_at": NOW.isoformat(),
        "staged_path": staged_path,
        "quarantine_path": None,
        "rows_read": rows,
        "rows_staged": rows,
        "rows_quarantined": 0,
        "rule_failures": {},
        "rule_warnings": {},
        "error": None,
    }
    write_state(root, state)


def read_state(root: Path) -> dict:
    path = staging_state_path(root)
    if not path.exists():
        return {"version": 1, "files": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(root: Path, state: dict) -> None:
    path = staging_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def read(root: Path, name: str, **where) -> list[dict]:
    """Rows of a curated table, in file order, optionally only those with these values."""
    (table,) = (table for table in TABLES if table.name == name)
    rows = [
        row
        for file in sorted(curated_dir(root).glob(table.path))
        for row in pq.ParquetFile(file).read().to_pylist()
    ]
    return [row for row in rows if all(row[column] == value for column, value in where.items())]


def plant(monkeypatch: pytest.MonkeyPatch, step: str, statement: str) -> None:
    """Make the SQL step ``step`` also run ``statement``: a bug planted in the build."""
    original = builder._sql
    monkeypatch.setattr(
        builder, "_sql", lambda name: original(name) + (statement if name == step else "")
    )


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under the curated folder, with its content."""
    curated = curated_dir(root)
    return {
        path.relative_to(curated).as_posix(): path.read_bytes()
        for path in sorted(curated.rglob("*"))
        if path.is_file()
    }


# --- fuel_price: one row per station, product and date --------------------------------------


@pytest.mark.parametrize(
    ("kinds", "winner"),
    [
        (("last_four_weeks", "monthly", "semester"), "semester"),
        (("last_four_weeks", "monthly"), "monthly"),
    ],
)
def test_same_observation_in_several_files_is_taken_from_the_most_closed_kind(
    tmp_path, kinds, winner
):
    periods = {"semester": "2026-S2", "monthly": "2026-10", "last_four_weeks": "latest"}
    for age, kind in enumerate(kinds):
        # The better kind is always the older download: recency must not decide.
        stage_anp(
            tmp_path,
            [observation()],
            kind=kind,
            period=periods[kind],
            ingested_at=NOW - timedelta(days=age),
        )

    report = build(tmp_path, now=clock)

    (row,) = read(tmp_path, "fuel_price")
    assert (row["source_kind"], row["source_period"]) == (winner, periods[winner])
    assert row["n_candidates"] == len(kinds)
    assert row["price_conflict"] is False
    assert report.rows_staged == len(kinds)
    assert report.rows_curated == 1
    assert report.duplicates_removed == len(kinds) - 1


def test_within_a_kind_the_most_recently_ingested_file_wins(tmp_path):
    stage_anp(tmp_path, [observation(price=5.0)], period="2026-10", ingested_at=NOW)
    stage_anp(
        tmp_path, [observation(price=6.0)], period="2026-11", ingested_at=NOW + timedelta(hours=1)
    )

    build(tmp_path, now=clock)

    (row,) = read(tmp_path, "fuel_price")
    assert row["sale_price"] == 6.0
    assert row["source_period"] == "2026-11"


def test_within_a_file_the_first_row_wins(tmp_path):
    stage_anp(tmp_path, [observation(price=5.0), observation(price=6.0)])

    build(tmp_path, now=clock)

    (row,) = read(tmp_path, "fuel_price")
    assert row["sale_price"] == 5.0


def test_candidates_are_counted_and_disagreement_on_the_price_is_flagged(tmp_path):
    stage_anp(
        tmp_path,
        [observation(1, price=6.0), observation(2, price=6.0), observation(3, price=6.0)],
        kind="monthly",
        period="2026-10",
    )
    stage_anp(
        tmp_path,
        [observation(1, price=6.0), observation(2, price=6.5)],
        kind="last_four_weeks",
        period="latest",
    )

    report = build(tmp_path, now=clock)

    by_station = {row["station_cnpj"][-1]: row for row in read(tmp_path, "fuel_price")}
    assert {s: (r["n_candidates"], r["price_conflict"]) for s, r in by_station.items()} == {
        "1": (2, False),  # twice, same price
        "2": (2, True),  # twice, two prices
        "3": (1, False),
    }
    assert by_station["2"]["sale_price"] == 6.0  # the monthly file's price
    assert report.rows_staged == 5
    assert report.rows_curated == 3
    assert report.duplicates_removed == 2
    assert report.price_conflicts == 1


def test_survey_week_runs_from_sunday_to_saturday(tmp_path):
    days = {
        "2026-10-03": "2026-09-27",  # Saturday: last day of its week
        "2026-10-04": "2026-10-04",  # Sunday: first day of the next one
        "2026-10-10": "2026-10-04",  # Saturday
        "2026-10-11": "2026-10-11",  # Sunday
    }
    stage_anp(tmp_path, [observation(day=day) for day in days])

    build(tmp_path, now=clock)

    weeks = {
        row["collected_on"].isoformat(): row["survey_week"].isoformat()
        for row in read(tmp_path, "fuel_price")
    }
    assert weeks == days


def test_fact_table_is_split_by_the_year_of_the_collection_date(tmp_path):
    stage_anp(tmp_path, [observation(day="2025-12-31"), observation(day="2026-01-01")])

    build(tmp_path, now=clock)

    fuel_price = curated_dir(tmp_path) / "fuel_price"
    assert sorted(path.name for path in fuel_price.iterdir()) == ["year=2025", "year=2026"]
    old = pq.ParquetFile(fuel_price / "year=2025" / "data.parquet").read()
    assert old.column("collected_on").to_pylist() == [date(2025, 12, 31)]
    assert "year" not in old.column_names
    # The week of 1 January 2026 started in 2025; the row still lives with its own date.
    (new_year,) = read(tmp_path, "fuel_price", collected_on=date(2026, 1, 1))
    assert new_year["survey_week"] == date(2025, 12, 28)


# --- weekly aggregates ------------------------------------------------------------------------


def test_weekly_statistics_match_a_hand_computed_example(tmp_path):
    stage_anp(tmp_path, survey("2026-10-05", 5.00, 5.10, 5.20, 5.40, 6.30))

    build(tmp_path, now=clock)

    (week,) = read(tmp_path, "agg_price_weekly_state")
    assert week == {
        "survey_week": date(2026, 10, 4),
        "state_code": "SP",
        "product": "GASOLINA",
        "unit": "BRL/L",
        "n_stations": 5,
        "n_observations": 5,
        "price_mean": 5.4,  # 27.00 / 5
        "price_median": 5.2,
        "price_p10": 5.04,  # 40 % of the way from 5.00 to 5.10
        "price_p90": 5.94,  # 60 % of the way from 5.40 to 6.30
    }


def test_station_surveyed_twice_in_a_week_counts_once_with_its_mean_price(tmp_path):
    monday = survey("2026-10-05", 5.0, 7.0, 9.0)
    wednesday = [observation(1, "2026-10-07", 6.0)]
    stage_anp(tmp_path, monday + wednesday)

    build(tmp_path, now=clock)

    (week,) = read(tmp_path, "agg_price_weekly_national")
    assert (week["n_stations"], week["n_observations"]) == (3, 4)
    assert week["price_mean"] == 7.1667  # (5.5 + 7 + 9) / 3
    assert week["price_median"] == 7.0


def test_week_that_spans_two_files_is_one_week(tmp_path):
    stage_anp(tmp_path, survey("2026-09-28", 6.0, 6.2), period="2026-09")  # Monday
    stage_anp(tmp_path, survey("2026-10-01", 6.4, 6.6, first_station=3), period="2026-10")

    build(tmp_path, now=clock)

    (week,) = read(tmp_path, "agg_price_weekly_national")
    assert week["survey_week"] == date(2026, 9, 27)
    assert week["n_stations"] == 4
    assert week["price_mean"] == 6.3


def test_cell_with_fewer_than_three_stations_is_not_published_and_is_counted(tmp_path):
    sao_paulo = survey("2026-10-05", 6.0, 6.1, 6.2)
    rio = survey("2026-10-05", 6.5, 6.6, first_station=4, state_code="RJ", municipality="NITEROI")
    stage_anp(tmp_path, sao_paulo + rio)

    report = build(tmp_path, now=clock)

    states = {
        row["state_code"]: row["n_stations"] for row in read(tmp_path, "agg_price_weekly_state")
    }
    assert states == {"SP": 3}  # RJ has two stations: its row does not exist
    (region,) = read(tmp_path, "agg_price_weekly_region")
    (country,) = read(tmp_path, "agg_price_weekly_national")
    assert (region["region_code"], region["n_stations"]) == ("SE", 5)
    assert (country["n_stations"], country["n_states"]) == (5, 2)  # RJ still counts as surveyed
    assert report.cells_suppressed == 1
    assert report.tables["agg_price_weekly_state"] == 1


def test_flag_table_separates_unbranded_stations_and_leaves_lpg_out(tmp_path):
    unbranded = survey("2026-10-05", 5.8, 5.9, 6.0, brand="BRANCA")
    branded = survey("2026-10-05", 6.2, 6.3, 6.4, first_station=4, brand="IPIRANGA")
    lpg = survey("2026-10-05", 110.0, 115.0, 120.0, first_station=7, product="GLP", unit="BRL/13kg")
    stage_anp(tmp_path, unbranded + branded + lpg)

    build(tmp_path, now=clock)

    flags = read(tmp_path, "agg_price_weekly_flag")
    assert [(row["product"], row["flag_type"], row["price_mean"]) for row in flags] == [
        ("GASOLINA", "branded", 6.3),
        ("GASOLINA", "white_flag", 5.9),
    ]
    assert {row["product"] for row in read(tmp_path, "agg_price_weekly_national")} == {
        "GASOLINA",
        "GLP",
    }


@pytest.mark.parametrize(
    ("files", "published_weeks", "dropped"),
    [
        pytest.param(
            [("semester", "2020-S1", ["2020-01-01", "2020-01-06"])],
            ["2020-01-05"],
            1,
            id="history starts on a Wednesday: its first week is cut",
        ),
        pytest.param(
            [("monthly", "2026-03", ["2026-03-02", "2026-03-09"])],
            ["2026-03-01", "2026-03-08"],
            0,
            id="history starts on a Sunday",
        ),
        pytest.param(
            [
                ("semester", "2026-S1", ["2026-01-05", "2026-06-30"]),
                ("monthly", "2026-07", ["2026-07-01"]),  # a Wednesday, inside the history
            ],
            ["2026-01-04", "2026-06-28"],
            0,
            id="only the start of the whole history counts",
        ),
        pytest.param(
            [("last_four_weeks", "latest", ["2026-10-26"])],
            ["2026-10-25"],
            0,
            id="a rolling file holds whole weeks",
        ),
    ],
)
def test_aggregates_start_at_the_first_week_the_files_cover_entirely(
    tmp_path, files, published_weeks, dropped
):
    for kind, period, days in files:
        rows = [row for day in days for row in survey(day, 5.9, 6.0, 6.1)]
        stage_anp(tmp_path, rows, kind=kind, period=period)

    report = build(tmp_path, now=clock)

    for table in ("national", "region", "state", "flag"):
        weeks = [
            row["survey_week"].isoformat() for row in read(tmp_path, f"agg_price_weekly_{table}")
        ]
        assert weeks == published_weeks, table
    assert report.partial_weeks_dropped == dropped
    # The fact table keeps every price, those of a cut week included.
    assert len(read(tmp_path, "fuel_price")) == 3 * sum(len(days) for *_, days in files)


def test_price_halfway_between_two_published_values_is_rounded_up(tmp_path):
    # 12.177 / 4 = 3.04425 and 20.0002 / 4 = 5.00005 exactly, but as floating-point
    # numbers both are stored a hair below the half, where round() takes them down.
    stage_anp(
        tmp_path, survey("2026-10-05", 3.044, 3.044, 3.044, 3.045, product="GNV", unit="BRL/m3")
    )
    rates = {"2026-10-05": 5.0, "2026-10-06": 5.0, "2026-10-07": 5.0001, "2026-10-08": 5.0001}
    stage_sgs(tmp_path, 1, rates)

    build(tmp_path, now=clock)

    (week,) = read(tmp_path, "agg_price_weekly_national")
    assert (week["price_mean"], week["usd_brl_mean"]) == (3.0443, 5.0001)


# --- exchange rate and inflation ----------------------------------------------------------------


def test_ipca_changes_are_chained_into_an_index_and_a_deflator(tmp_path):
    stage_sgs(tmp_path, 433, {"2026-01-01": 1.0, "2026-02-01": 2.0, "2026-03-01": -0.5})

    build(tmp_path, now=clock)

    january, february, march = read(tmp_path, "ipca_monthly")
    assert [row["month"] for row in (january, february, march)] == [
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
    ]
    assert january["pct_change"] == 1.0
    assert january["index"] == pytest.approx(101.0)  # December 2025 = 100
    assert february["index"] == pytest.approx(103.02)  # 101 x 1.02
    assert march["index"] == pytest.approx(102.5049)  # 103.02 x 0.995
    assert january["deflator_to_latest"] == pytest.approx(102.5049 / 101.0)
    assert february["deflator_to_latest"] == pytest.approx(0.995)
    assert march["deflator_to_latest"] == 1.0


def test_week_is_deflated_with_the_ipca_of_the_month_of_its_wednesday(tmp_path):
    months = [
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08",
        "2026-09",
        "2026-10",
    ]
    stage_sgs(tmp_path, 433, {f"{month}-01": 1.0 for month in months})
    stage_anp(tmp_path, survey("2026-03-30", 5.9, 6.0, 6.1), period="2026-03")  # Wednesday 1 April
    stage_anp(tmp_path, survey("2026-09-28", 5.9, 6.0, 6.1), period="2026-09")  # Wed. 30 September

    build(tmp_path, now=clock)

    spring, autumn = read(tmp_path, "agg_price_weekly_national")
    assert spring["survey_week"] == date(2026, 3, 29)
    assert spring["ipca_deflator"] == pytest.approx(1.01**6)  # April, not March
    assert spring["price_mean_real"] == 6.3691  # 6.00 x 1.01^6
    assert autumn["survey_week"] == date(2026, 9, 27)
    assert autumn["ipca_deflator"] == pytest.approx(1.01)  # September, not October
    assert (spring["ipca_provisional"], autumn["ipca_provisional"]) == (False, False)


def test_week_of_a_month_without_ipca_is_provisional_and_not_deflated(tmp_path):
    stage_sgs(tmp_path, 433, {"2026-09-01": 1.0, "2026-10-01": 1.0})
    stage_anp(tmp_path, survey("2026-10-05", 5.9, 6.0, 6.1), period="2026-10")
    stage_anp(tmp_path, survey("2026-11-02", 5.9, 6.0, 6.1), period="2026-11")

    build(tmp_path, now=clock)

    october, november = read(tmp_path, "agg_price_weekly_national")
    assert (october["ipca_provisional"], october["ipca_deflator"]) == (False, 1.0)
    assert (november["ipca_provisional"], november["ipca_deflator"]) == (True, 1.0)
    assert november["price_mean_real"] == november["price_mean"] == 6.0


def test_dollar_of_the_week_is_the_mean_of_the_rates_dated_within_it(tmp_path):
    stage_sgs(
        tmp_path,
        1,
        {
            "2026-10-02": 4.0,  # Friday of the week before
            "2026-10-05": 5.0,
            "2026-10-09": 5.5,
            "2026-10-12": 9.0,  # Monday of the week after
        },
    )
    stage_anp(tmp_path, survey("2026-10-05", 5.9, 6.0, 6.1))

    build(tmp_path, now=clock)

    (week,) = read(tmp_path, "agg_price_weekly_national")
    assert week["usd_brl_mean"] == 5.25
    assert [row["usd_brl"] for row in read(tmp_path, "usd_brl_daily")] == [4.0, 5.0, 5.5, 9.0]


def test_week_without_a_dollar_rate_has_no_dollar_mean(tmp_path):
    stage_sgs(tmp_path, 1, {"2026-10-02": 4.0})
    stage_anp(tmp_path, survey("2026-10-05", 5.9, 6.0, 6.1))

    build(tmp_path, now=clock)

    (week,) = read(tmp_path, "agg_price_weekly_national")
    assert week["usd_brl_mean"] is None


# --- station dimension --------------------------------------------------------------------------


def test_station_carries_the_attributes_of_its_latest_observation(tmp_path):
    old = observation(
        day="2026-09-07", station_name="POSTO VELHO LTDA", brand="IPIRANGA", street="RUA VELHA"
    )
    new = observation(
        day="2026-10-05", station_name="POSTO NOVO LTDA", brand="BRANCA", street="RUA NOVA"
    )
    stage_anp(tmp_path, [old], period="2026-09")
    stage_anp(tmp_path, [new, new | {"product": "DIESEL"}], period="2026-10")

    build(tmp_path, now=clock)

    (station,) = read(tmp_path, "station")
    assert station["station_cnpj"] == "00000000000001"
    assert (station["station_name"], station["brand"], station["street"]) == (
        "POSTO NOVO LTDA",
        "BRANCA",
        "RUA NOVA",
    )
    assert (station["first_seen"], station["last_seen"]) == (date(2026, 9, 7), date(2026, 10, 5))
    assert (station["n_observations"], station["n_names"], station["n_brands"]) == (3, 2, 2)


def test_same_day_in_two_files_takes_the_station_from_the_latest_download(tmp_path):
    stage_anp(tmp_path, [observation(station_name="POSTO A LTDA")], kind="monthly")
    stage_anp(
        tmp_path,
        [observation(station_name="POSTO A LTDA - EPP")],
        kind="last_four_weeks",
        period="latest",
        ingested_at=NOW + timedelta(days=7),
    )

    build(tmp_path, now=clock)

    (station,) = read(tmp_path, "station")
    assert station["station_name"] == "POSTO A LTDA - EPP"  # the registry as it stands now
    assert station["n_observations"] == 1  # one observation, published twice
    (price,) = read(tmp_path, "fuel_price")
    assert price["source_kind"] == "monthly"  # while the price keeps its lineage


# --- what the build reads and writes --------------------------------------------------------------


@pytest.mark.parametrize("state", [None, {"version": 1, "files": {}}], ids=["no state", "no files"])
def test_nothing_staged_gives_empty_tables_with_their_columns(tmp_path, state):
    if state is not None:
        write_state(tmp_path, state)

    report = build(tmp_path, now=clock)

    for table in TABLES:
        files = sorted(curated_dir(tmp_path).glob(table.path))
        assert len(files) == 1, table.name
        content = pq.ParquetFile(files[0]).read()
        assert content.num_rows == 0
        assert content.column_names == [column.name for column in table.columns]
    assert (curated_dir(tmp_path) / "fuel_price" / "year=2026" / "data.parquet").is_file()
    assert report.tables == {table.name: 0 for table in TABLES}
    assert (report.rows_staged, report.rows_curated, report.cells_suppressed) == (0, 0, 0)
    assert report.partial_weeks_dropped == 0


def test_only_files_listed_in_the_staging_state_are_read(tmp_path):
    stage_anp(tmp_path, survey("2026-10-05", 5.9, 6.0, 6.1))
    stage_anp(
        tmp_path, survey("2026-10-05", 9.9, first_station=4), period="2026-08", listed=False
    )  # left on disk by an old run
    _list_in_state(tmp_path, "https://example.test/failed.csv", "anp", None, 0)  # nothing staged

    report = build(tmp_path, now=clock)

    assert report.rows_staged == 3
    assert [row["sale_price"] for row in read(tmp_path, "fuel_price")] == [5.9, 6.0, 6.1]


PROGRESS_BAR_PROBE = """
import sys
from pathlib import Path

from fuel_price_lakehouse.curate.build import _connect

with _connect(Path(sys.argv[1])) as connection:
    connection.execute("SET progress_bar_time = 0")  # draw it at once, if at all
    connection.execute("SELECT count(*) FROM range(400000000) t(i) WHERE i % 7 = 3").fetchall()
"""


def test_build_draws_no_progress_bar_in_the_log(tmp_path):
    # DuckDB draws it on stdout even when stdout is a CI log; under pytest it stays
    # hidden, so the query runs in an interpreter of its own.
    probe = subprocess.run(
        [sys.executable, "-c", PROGRESS_BAR_PROBE, str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    assert probe.stdout == ""


def test_unknown_staging_state_version_is_refused(tmp_path):
    write_state(tmp_path, {"version": 2, "files": {}})

    with pytest.raises(ValueError, match="unsupported staging state version 2"):
        build(tmp_path, now=clock)


def test_report_is_returned_and_saved_next_to_the_tables(tmp_path):
    stage_anp(tmp_path, survey("2026-10-05", 5.9, 6.0, 6.1))
    stage_sgs(tmp_path, 1, {"2026-10-05": 5.0})

    report = build(tmp_path, now=clock)

    saved = json.loads((curated_dir(tmp_path) / REPORT_NAME).read_text(encoding="utf-8"))
    assert saved == asdict(report)
    assert report.built_at == "2026-11-26T12:00:00+00:00"
    assert report.tables == {table.name: len(read(tmp_path, table.name)) for table in TABLES}
    assert report.tables["fuel_price"] == 3
    assert report.tables["usd_brl_daily"] == 1


def test_build_leaves_only_tables_and_report_behind(tmp_path):
    stage_anp(tmp_path, survey("2026-10-05", 5.9, 6.0, 6.1))

    build(tmp_path, now=clock)

    assert sorted(snapshot(tmp_path)) == sorted(
        [
            REPORT_NAME,
            "fuel_price/year=2026/data.parquet",
            *(f"{table.name}.parquet" for table in TABLES if table.name != "fuel_price"),
        ]
    )
    assert sorted(path.name for path in curated_dir(tmp_path).iterdir() if path.is_dir()) == [
        "fuel_price"
    ]


def test_two_builds_of_the_same_staged_files_are_identical(tmp_path):
    stage_sgs(tmp_path, 1, {"2026-10-05": 5.0, "2026-10-06": 5.1})
    stage_sgs(tmp_path, 433, {"2026-09-01": 0.5, "2026-10-01": 0.1})
    stage_anp(tmp_path, survey("2026-10-05", 5.9, 6.0, 6.1, 6.15), kind="monthly")
    stage_anp(
        tmp_path,
        survey("2026-10-05", 5.9, 6.0, 6.3) + survey("2026-11-03", 6.0, 6.2, 6.4),
        kind="last_four_weeks",
        period="latest",
    )

    build(tmp_path, now=clock)
    first = snapshot(tmp_path)
    build(tmp_path, now=clock)

    assert snapshot(tmp_path) == first  # byte for byte, report included


def test_rebuild_drops_a_partition_that_no_longer_has_rows(tmp_path):
    stage_anp(tmp_path, [observation(day="2025-12-30")], period="2025-12")
    stage_anp(tmp_path, [observation(day="2026-10-05")], period="2026-10")
    build(tmp_path, now=clock)
    fuel_price = curated_dir(tmp_path) / "fuel_price"
    assert (fuel_price / "year=2025").is_dir()

    state = read_state(tmp_path)
    del state["files"]["https://example.test/monthly-2025-12.csv"]  # the source dropped the file
    write_state(tmp_path, state)
    build(tmp_path, now=clock)

    assert sorted(path.name for path in fuel_price.iterdir()) == ["year=2026"]


def test_table_that_breaks_its_contract_stops_the_build_and_keeps_the_previous_tables(tmp_path):
    stage_anp(tmp_path, survey("2026-10-05", 5.9, 6.0, 6.1))
    build(tmp_path, now=clock)
    before = snapshot(tmp_path)

    # A product the staged layer must never let through.
    stage_anp(tmp_path, [observation(4, product="QUEROSENE")], period="2026-11")
    with pytest.raises(ContractError, match=r'fuel_price: 1 rows break \["product" IN'):
        build(tmp_path, now=clock)

    assert snapshot(tmp_path) == before


@pytest.mark.parametrize(
    ("bug", "broken_promise"),
    [
        (
            "INSERT INTO agg_price_weekly_state SELECT * FROM agg_price_weekly_state;",
            "agg_price_weekly_state: 1 keys (survey_week, state_code, product) are repeated",
        ),
        (
            "UPDATE agg_price_weekly_state SET price_p10 = price_median + 1;",
            "agg_price_weekly_state: 1 rows break [price_p10 <= price_median",
        ),
    ],
    ids=["repeated key", "p10 above the median"],
)
def test_aggregate_that_breaks_its_contract_stops_the_build(
    tmp_path, monkeypatch, bug, broken_promise
):
    stage_anp(tmp_path, survey("2026-10-05", 5.9, 6.0, 6.1))
    build(tmp_path, now=clock)
    before = snapshot(tmp_path)

    plant(monkeypatch, "agg_price_weekly_state", bug)
    with pytest.raises(ContractError, match=re.escape(broken_promise)):
        build(tmp_path, now=clock)

    assert snapshot(tmp_path) == before


ADD_STATION_COLUMN = (
    "ALTER TABLE agg_price_weekly_state ADD COLUMN station_cnpj VARCHAR DEFAULT '1';"
)


def test_station_column_registered_in_a_public_table_stops_the_build(tmp_path, monkeypatch):
    stage_anp(tmp_path, survey("2026-10-05", 5.9, 6.0, 6.1))
    build(tmp_path, now=clock)
    before = snapshot(tmp_path)

    # Someone adds the CNPJ to the SQL of a public table and to its registry entry.
    (state,) = (table for table in TABLES if table.name == "agg_price_weekly_state")
    leaky = replace(state, columns=(*state.columns, Column("station_cnpj", "VARCHAR", "CNPJ")))
    tables = tuple(leaky if table is state else table for table in TABLES)
    monkeypatch.setattr(builder, "TABLES", tables)
    monkeypatch.setattr(builder, "_TABLE_BY_NAME", {table.name: table for table in tables})
    plant(monkeypatch, "agg_price_weekly_state", ADD_STATION_COLUMN)
    with pytest.raises(ContractError, match="public table with the station-level column"):
        build(tmp_path, now=clock)

    assert snapshot(tmp_path) == before


def test_column_the_registry_does_not_list_is_never_written(tmp_path, monkeypatch):
    plant(monkeypatch, "agg_price_weekly_state", ADD_STATION_COLUMN)  # in the SQL only
    stage_anp(tmp_path, survey("2026-10-05", 5.9, 6.0, 6.1))

    build(tmp_path, now=clock)

    written = pq.read_schema(curated_dir(tmp_path) / "agg_price_weekly_state.parquet")
    assert "station_cnpj" not in written.names


def test_fact_table_that_loses_a_staged_row_stops_the_build(tmp_path, monkeypatch):
    plant(monkeypatch, "fuel_price", "DELETE FROM fuel_price WHERE sale_price = 6.1;")
    stage_anp(tmp_path, survey("2026-10-05", 5.9, 6.0, 6.1))

    with pytest.raises(ContractError, match="its rows stand for 2 staged rows, 3 were staged"):
        build(tmp_path, now=clock)
