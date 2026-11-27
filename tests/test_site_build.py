"""The site builder, on a tiny curated layer written for each test.

The tables are written the way the curated layer writes them: the columns and
types of the registry, one Parquet file per table. Their numbers are made up
and round, so that the expected data files can be read at a glance.
"""

from __future__ import annotations

import dataclasses
import json
import math
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.csv
import pyarrow.parquet as pq
import pytest

from fuel_price_lakehouse.curate import tables as registry
from fuel_price_lakehouse.curate.tables import (
    PUBLIC_TABLES,
    STATION_LEVEL_COLUMNS,
    TABLES,
    Column,
)
from fuel_price_lakehouse.layout import curated_dir, quality_dir
from fuel_price_lakehouse.quality.report import build_report, publishable
from fuel_price_lakehouse.reference import PRODUCT_BY_ID, REGION_OF_STATE, REGIONS, STATES
from fuel_price_lakehouse.site import data_files, folder
from fuel_price_lakehouse.site import downloads as site_downloads
from fuel_price_lakehouse.site.build import SiteBuildError, build, check_data_files

from .test_quality_checks import MONTHLY, RUN_DAY, Lake, at, not_staged

# Three surveyed weeks, named by their Sunday. Nothing was surveyed in the week of 4 October.
SEP_20, SEP_27, OCT_11 = date(2026, 9, 20), date(2026, 9, 27), date(2026, 10, 11)
WEEKS = ["2026-09-20", "2026-09-27", "2026-10-04", "2026-10-11"]

REFERENCE_EVENTS = data_files.EVENTS_FILE
TABLE_BY_NAME = {table.name: table for table in TABLES}
ARROW_TYPES = {
    "DATE": pa.date32(),
    "VARCHAR": pa.string(),
    "BIGINT": pa.int64(),
    "DOUBLE": pa.float64(),
    "BOOLEAN": pa.bool_(),
}

EVENT = {
    "date": "2026-09-10",
    "title_pt": "Um evento",
    "title_en": "An event",
    "detail_pt": "O que aconteceu.",
    "detail_en": "What happened.",
    "fuels": ["GASOLINA", "ETANOL"],
    "source_url": "https://example.org/noticia",
    "source_quote": "O trecho da fonte que sustenta o evento.",
}


def clock() -> datetime:
    return datetime(2026, 11, 26, 12, 30, 45, tzinfo=UTC)


def weekly(week: date, product: str, mean: float, n_stations: int = 30, **key: str) -> dict:
    """A row of a weekly aggregate; ``key`` is the state, region or flag type of its table."""
    return {
        "survey_week": week,
        "product": product,
        **key,
        "unit": PRODUCT_BY_ID[product].unit,
        "n_stations": n_stations,
        "n_observations": n_stations + 2,
        "price_mean": mean,
        "price_median": round(mean - 0.01, 4),
        "price_p10": round(mean - 0.5, 4),
        "price_p90": round(mean + 0.5, 4),
    }


def national(
    week: date,
    product: str,
    mean: float,
    *,
    usd: float | None,
    deflator: float = 1.0,
    provisional: bool = False,
    n_states: int = 27,
) -> dict:
    return {
        **weekly(week, product, mean),
        "n_states": n_states,
        "usd_brl_mean": usd,
        "ipca_deflator": deflator,
        "ipca_provisional": provisional,
        "price_mean_real": round(mean * deflator, 4),
    }


def observation(cnpj: str, state: str, municipality: str, day: date, week: date) -> dict:
    """A row of the fact table: one price collected at one station."""
    return {
        "station_cnpj": cnpj,
        "product": "GASOLINA",
        "collected_on": day,
        "survey_week": week,
        "sale_price": 6.0,
        "purchase_price": None,
        "unit": "BRL/L",
        "state_code": state,
        "region_code": REGION_OF_STATE[state],
        "municipality": municipality,
        "brand": "BRANCA",
        "source_kind": "monthly",
        "source_period": f"{day:%Y-%m}",
        "n_candidates": 1,
        "price_conflict": False,
    }


def lake() -> dict[str, list[dict]]:
    """The rows of every curated table the builder touches, by table name.

    Regular gasoline in two states and natural gas (GNV) in one, over three
    survey weeks. Acre has no gasoline cell in the second week: it had fewer
    stations than may be published. GNV has no flag rows at all. IPCA is out
    up to October, so the September weeks are deflated and the October one is
    already in reais of the reference month. No dollar rate in the last week.
    """
    return {
        "agg_price_weekly_national": [
            national(SEP_20, "GASOLINA", 6.0, usd=5.31, deflator=1.0048),
            national(SEP_27, "GASOLINA", 6.1, usd=5.32, deflator=1.0048),
            national(OCT_11, "GASOLINA", 6.2, usd=None),
            national(SEP_20, "GNV", 4.5, usd=5.31, deflator=1.0048, n_states=1),
            national(SEP_27, "GNV", 4.6, usd=5.32, deflator=1.0048, n_states=1),
            national(OCT_11, "GNV", 4.7, usd=None, n_states=1),
        ],
        "agg_price_weekly_region": [
            weekly(SEP_20, "GASOLINA", 5.9, region_code="SE"),
            weekly(SEP_27, "GASOLINA", 6.0, region_code="SE"),
            weekly(OCT_11, "GASOLINA", 6.1, region_code="SE"),
            weekly(SEP_20, "GASOLINA", 7.0, region_code="N"),
            weekly(OCT_11, "GASOLINA", 7.2, region_code="N"),
            weekly(SEP_20, "GNV", 4.5, region_code="SE"),
            weekly(SEP_27, "GNV", 4.6, region_code="SE"),
            weekly(OCT_11, "GNV", 4.7, region_code="SE"),
        ],
        "agg_price_weekly_state": [
            weekly(SEP_20, "GASOLINA", 5.9, state_code="SP"),
            weekly(SEP_27, "GASOLINA", 6.0, state_code="SP"),
            weekly(OCT_11, "GASOLINA", 6.1, state_code="SP"),
            weekly(SEP_20, "GASOLINA", 7.0, n_stations=4, state_code="AC"),
            weekly(OCT_11, "GASOLINA", 7.2, n_stations=3, state_code="AC"),
            weekly(SEP_20, "GNV", 4.5, state_code="RJ"),
            weekly(SEP_27, "GNV", 4.6, state_code="RJ"),
            weekly(OCT_11, "GNV", 4.7, state_code="RJ"),
        ],
        "agg_price_weekly_flag": [
            weekly(SEP_20, "GASOLINA", 5.8, flag_type="white_flag"),
            weekly(SEP_27, "GASOLINA", 5.9, flag_type="white_flag"),
            weekly(OCT_11, "GASOLINA", 6.0, flag_type="white_flag"),
            weekly(SEP_20, "GASOLINA", 6.1, flag_type="branded"),
            weekly(SEP_27, "GASOLINA", 6.2, flag_type="branded"),
            weekly(OCT_11, "GASOLINA", 6.3, flag_type="branded"),
        ],
        "usd_brl_daily": [
            {"observed_on": date(2026, 9, 21), "usd_brl": 5.3},
            {"observed_on": date(2026, 9, 22), "usd_brl": 5.32},
            {"observed_on": date(2026, 9, 28), "usd_brl": 5.32},
        ],
        "ipca_monthly": [
            {
                "month": date(2026, 9, 1),
                "pct_change": 0.26,
                "index": 100.26,
                "deflator_to_latest": 1.0048,
            },
            {
                "month": date(2026, 10, 1),
                "pct_change": 0.48,
                "index": 100.741248,
                "deflator_to_latest": 1.0,
            },
        ],
        "fuel_price": [
            observation("11222333000181", "SP", "SAO PAULO", date(2026, 9, 21), SEP_20),
            observation("11222333000181", "SP", "SAO PAULO", date(2026, 9, 28), SEP_27),
            observation("11444777000161", "SP", "SAO PAULO", date(2026, 9, 21), SEP_20),
            observation("22333444000181", "PI", "BOM JESUS", date(2026, 9, 22), SEP_20),
            observation("33444555000181", "RS", "BOM JESUS", date(2026, 10, 13), OCT_11),
        ],
    }


def write_table(root: Path, name: str, rows: list[dict], **extra_columns: pa.DataType) -> Path:
    table = TABLE_BY_NAME[name]
    columns = {column.name: ARROW_TYPES[column.type] for column in table.columns} | extra_columns
    path = curated_dir(root) / table.path.replace("*", "2026")
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema(list(columns.items()))), path)
    return path


def write_report(root: Path, **changes: object) -> dict:
    """A quality report with every section the dashboard reads, and little in them."""
    report = {
        "run": {
            "id": "20261126T120000Z",
            "started_at": "2026-11-26T12:00:00Z",
            "finished_at": "2026-11-26T12:03:00Z",
            "code_version": "abc1234",
            "since": "2026-09",
        },
        "status": "pass",
        "freshness": {
            "latest_collection_date": "2026-10-13",
            "latest_week": "2026-10-11",
            "days_since_latest": 44,
            "usd_latest": "2026-09-28",
            "ipca_latest_month": "2026-10",
        },
        "funnel": {
            "files": 2,
            "rows_raw": 6,
            "rows_staged": 5,
            "rows_quarantined": 1,
            "rows_curated": 10,
            "duplicates_removed": 0,
            "price_conflicts": 0,
            "cells_suppressed": 1,
        },
        "rules": [],
        "files": [],
        "checks": [],
        "history": [],
    } | changes
    path = quality_dir(root) / "report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding="utf-8")
    return report


def write_build(root: Path, built_at: str = "2026-11-26T12:01:30.250000+00:00") -> None:
    """What the curated build says of itself; by default, made before the report was."""
    path = curated_dir(root) / "_build.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"built_at": built_at}), encoding="utf-8")


def read(out: Path, name: str):
    return json.loads((out / "data" / name).read_text(encoding="utf-8"))


def arrays(node) -> list[list]:
    """Every array of a series file, the week axis included."""
    if isinstance(node, dict):
        return [found for value in node.values() for found in arrays(value)]
    return [node]


def snapshot(folder: Path) -> dict[str, bytes]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(autouse=True)
def reference_files(monkeypatch, tmp_path) -> None:
    """No front-end and one event, whatever the repository holds at the moment."""
    events = tmp_path / "events.json"
    events.write_text(json.dumps([EVENT]), encoding="utf-8")
    monkeypatch.setattr(data_files, "EVENTS_FILE", events)
    # A folder that is not there, unless a test creates it.
    monkeypatch.setattr(folder, "STATIC_DIR", tmp_path / "front-end")


@pytest.fixture
def root(tmp_path) -> Path:
    """A data root with the tiny curated layer and a quality report that passed."""
    root = tmp_path / "data"
    for name, rows in lake().items():
        write_table(root, name, rows)
    write_build(root)
    write_report(root)
    return root


@pytest.fixture
def out(tmp_path) -> Path:
    return tmp_path / "build" / "site"


# ---------------------------------------------------------------- the data files


def test_national_file_has_one_aligned_array_per_statistic(root, out):
    build(root, out, now=clock)

    assert read(out, "national.json") == {
        "weeks": WEEKS,
        "usd_brl": [5.31, 5.32, None, None],
        "ipca_deflator": [1.0048, 1.0048, None, 1.0],
        "ipca_provisional": [False, False, None, False],
        "products": {
            "GASOLINA": {
                "mean": [6.0, 6.1, None, 6.2],
                "median": [5.99, 6.09, None, 6.19],
                "p10": [5.5, 5.6, None, 5.7],
                "p90": [6.5, 6.6, None, 6.7],
                "mean_real": [6.029, 6.129, None, 6.2],
                "n_stations": [30, 30, None, 30],
                "n_states": [27, 27, None, 27],
            },
            "GNV": {
                "mean": [4.5, 4.6, None, 4.7],
                "median": [4.49, 4.59, None, 4.69],
                "p10": [4.0, 4.1, None, 4.2],
                "p90": [5.0, 5.1, None, 5.2],
                "mean_real": [4.522, 4.622, None, 4.7],
                "n_stations": [30, 30, None, 30],
                "n_states": [1, 1, None, 1],
            },
        },
    }


def test_state_file_nests_series_by_product_then_state(root, out):
    build(root, out, now=clock)

    assert read(out, "states.json") == {
        "weeks": WEEKS,
        "products": {
            "GASOLINA": {
                "AC": {
                    "mean": [7.0, None, None, 7.2],
                    "median": [6.99, None, None, 7.19],
                    "p10": [6.5, None, None, 6.7],
                    "p90": [7.5, None, None, 7.7],
                    "n_stations": [4, None, None, 3],
                },
                "SP": {
                    "mean": [5.9, 6.0, None, 6.1],
                    "median": [5.89, 5.99, None, 6.09],
                    "p10": [5.4, 5.5, None, 5.6],
                    "p90": [6.4, 6.5, None, 6.6],
                    "n_stations": [30, 30, None, 30],
                },
            },
            "GNV": {
                "RJ": {
                    "mean": [4.5, 4.6, None, 4.7],
                    "median": [4.49, 4.59, None, 4.69],
                    "p10": [4.0, 4.1, None, 4.2],
                    "p90": [5.0, 5.1, None, 5.2],
                    "n_stations": [30, 30, None, 30],
                },
            },
        },
    }


def test_region_file_nests_series_by_product_then_region(root, out):
    build(root, out, now=clock)

    regions = read(out, "regions.json")

    assert regions["weeks"] == WEEKS
    assert {product: list(cells) for product, cells in regions["products"].items()} == {
        "GASOLINA": ["N", "SE"],  # the order of the reference list, not of the alphabet
        "GNV": ["SE"],
    }
    assert regions["products"]["GASOLINA"]["N"] == {
        "mean": [7.0, None, None, 7.2],
        "median": [6.99, None, None, 7.19],
        "p10": [6.5, None, None, 6.7],
        "p90": [7.5, None, None, 7.7],
        "n_stations": [30, None, None, 30],
    }


def test_flag_file_compares_unbranded_and_branded_stations(root, out):
    build(root, out, now=clock)

    assert read(out, "flags.json") == {
        "weeks": WEEKS,
        "products": {
            "GASOLINA": {
                "white_flag": {
                    "mean": [5.8, 5.9, None, 6.0],
                    "median": [5.79, 5.89, None, 5.99],
                    "n_stations": [30, 30, None, 30],
                },
                "branded": {
                    "mean": [6.1, 6.2, None, 6.3],
                    "median": [6.09, 6.19, None, 6.29],
                    "n_stations": [30, 30, None, 30],
                },
            },
        },
    }


def test_meta_file_describes_the_run_and_what_the_site_holds(root, out):
    build(root, out, now=clock)

    meta = read(out, "meta.json")

    assert list(meta) == [
        "generated_at",
        "run_id",
        "since",
        "latest_week",
        "ipca_reference_month",
        "min_stations_published",
        "products",
        "states",
        "regions",
        "totals",
        "downloads",
        "repository",
    ]
    assert meta["generated_at"] == "2026-11-26T12:30:45Z"
    assert meta["run_id"] == "20261126T120000Z"  # the run of the quality report
    assert meta["since"] == "2026-09"  # what that run was asked to cover
    assert meta["latest_week"] == "2026-10-11"
    assert meta["ipca_reference_month"] == "2026-10"
    assert meta["min_stations_published"] == 3
    assert meta["products"] == [  # the ones with data, in the order of the reference list
        {
            "id": "GASOLINA",
            "label_pt": "Gasolina comum",
            "label_en": "Regular gasoline",
            "unit": "BRL/L",
        },
        {"id": "GNV", "label_pt": "GNV", "label_en": "CNG", "unit": "BRL/m3"},
    ]
    assert len(meta["states"]) == len(STATES)
    assert meta["states"][0] == {"code": "AC", "name": "Acre", "region": "N"}
    assert len(meta["regions"]) == len(REGIONS)
    assert meta["regions"][0] == {"code": "N", "name_pt": "Norte", "name_en": "North"}
    assert meta["repository"] == "https://github.com/Corrar/fuel-price-lakehouse"


def test_totals_are_counted_on_the_fact_table(root, out):
    build(root, out, now=clock)

    # Bom Jesus is a municipality of Piaui and another one of Rio Grande do Sul.
    assert read(out, "meta.json")["totals"] == {
        "observations": 5,
        "stations": 4,
        "municipalities": 3,
        "weeks": 3,
    }


def test_totals_leave_out_a_week_of_the_fact_table_that_no_series_shows(root, out):
    # A first survey week the files cover only in part: in the fact table, not in the aggregates.
    sep_13 = date(2026, 9, 13)
    early = observation("44555666000181", "SC", "JOINVILLE", date(2026, 9, 17), sep_13)
    write_table(root, "fuel_price", [*lake()["fuel_price"], early])

    build(root, out, now=clock)

    assert read(out, "national.json")["weeks"][0] == "2026-09-20"
    assert read(out, "meta.json")["totals"] == {
        "observations": 5,
        "stations": 4,
        "municipalities": 3,
        "weeks": 3,
    }


def test_events_are_published_without_the_quote_that_backs_them(root, out):
    build(root, out, now=clock)

    assert read(out, "events.json") == [
        {
            "date": "2026-09-10",
            "title_pt": "Um evento",
            "title_en": "An event",
            "detail_pt": "O que aconteceu.",
            "detail_en": "What happened.",
            "fuels": ["GASOLINA", "ETANOL"],
            "source_url": "https://example.org/noticia",
        }
    ]


def test_reference_events_of_the_repository_are_publishable(root, out, monkeypatch):
    monkeypatch.setattr(data_files, "EVENTS_FILE", REFERENCE_EVENTS)
    reference = json.loads(REFERENCE_EVENTS.read_text(encoding="utf-8"))

    build(root, out, now=clock)

    events = read(out, "events.json")
    assert len(events) == len(reference) > 0
    assert [event["date"] for event in events] == sorted(event["date"] for event in reference)
    assert all("source_quote" not in event for event in events)


def test_quality_file_is_the_report_of_the_run(root, out):
    report = write_report(root, status="warn")

    build(root, out, now=clock)

    assert read(out, "quality.json") == report


def test_data_files_are_compact_json(root, out):
    build(root, out, now=clock)

    text = (out / "data" / "states.json").read_text(encoding="utf-8")
    compact = json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))
    assert text == compact + "\n"


# ---------------------------------------------------------------- gaps, absences, rounding


def test_week_without_a_survey_is_on_the_axis_with_null_in_every_series(root, out):
    build(root, out, now=clock)

    gap = WEEKS.index("2026-10-04")
    for name in ("national.json", "regions.json", "states.json", "flags.json"):
        document = read(out, name)
        assert document["weeks"] == WEEKS  # every Sunday, the one without a survey included
        series = [values for values in arrays(document) if values is not document["weeks"]]
        assert series and all(values[gap] is None for values in series)


def test_week_whose_month_has_no_ipca_yet_is_flagged_and_left_in_nominal_reais(root, out):
    september = {"month": date(2026, 9, 1), "pct_change": 0.26, "index": 100.26}
    write_table(root, "ipca_monthly", [september | {"deflator_to_latest": 1.0}])
    rows = [
        national(SEP_20, "GASOLINA", 6.0, usd=5.31),
        national(SEP_27, "GASOLINA", 6.1, usd=5.32),
        national(OCT_11, "GASOLINA", 6.2, usd=None, provisional=True),
    ]
    write_table(root, "agg_price_weekly_national", rows)

    build(root, out, now=clock)

    document = read(out, "national.json")
    assert read(out, "meta.json")["ipca_reference_month"] == "2026-09"
    assert document["ipca_provisional"] == [False, False, None, True]
    assert document["ipca_deflator"] == [1.0, 1.0, None, 1.0]
    assert document["products"]["GASOLINA"]["mean_real"][-1] == 6.2


def test_product_without_data_in_a_state_is_absent_from_that_state(root, out):
    build(root, out, now=clock)

    assert list(read(out, "states.json")["products"]["GNV"]) == ["RJ"]
    assert "GNV" not in read(out, "flags.json")["products"]  # no flag rows at all


def test_suppressed_cell_is_null_next_to_the_published_ones(root, out):
    build(root, out, now=clock)

    gasoline = read(out, "states.json")["products"]["GASOLINA"]
    second_week = WEEKS.index("2026-09-27")
    assert gasoline["AC"]["mean"][second_week] is None
    assert gasoline["AC"]["n_stations"][second_week] is None
    assert gasoline["SP"]["mean"][second_week] == 6.0


def test_measures_are_rounded_for_transport(root, out):
    row = national(SEP_20, "GASOLINA", 5.9996, usd=5.43218, deflator=1.0123456789)
    row |= {"price_median": 5.9994, "price_p10": 5.4321, "price_p90": 6.5678}
    row |= {"price_mean_real": 6.0737}
    write_table(root, "agg_price_weekly_national", [row])

    build(root, out, now=clock)

    document = read(out, "national.json")
    week = document["weeks"].index("2026-09-20")
    gasoline = {field: values[week] for field, values in document["products"]["GASOLINA"].items()}
    assert gasoline == {
        "mean": 6.0,  # prices: 3 decimals
        "median": 5.999,
        "p10": 5.432,
        "p90": 6.568,
        "mean_real": 6.074,
        "n_stations": 30,
        "n_states": 27,
    }
    assert document["usd_brl"][week] == 5.4322  # 4 decimals, as Banco Central quotes it
    assert document["ipca_deflator"][week] == 1.012346  # 6 decimals


# ---------------------------------------------------------------- what stops a build


@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_value_that_is_not_a_number_in_a_public_table_stops_the_build(root, out, value):
    rows = lake()["agg_price_weekly_state"]
    rows[0]["price_p90"] = value
    write_table(root, "agg_price_weekly_state", rows)

    with pytest.raises(SiteBuildError, match=r"states\.json\.products\.GASOLINA\.SP\.p90\[0\]"):
        build(root, out, now=clock)

    assert not out.exists()


def test_quality_report_that_carries_a_station_level_field_stops_the_build(root, out):
    example = {"id": "quarantine_rate", "examples": [{"station_cnpj": "11222333000181"}]}
    write_report(root, checks=[example])

    with pytest.raises(SiteBuildError, match="station-level field 'station_cnpj'"):
        build(root, out, now=clock)

    assert not out.exists()


def test_public_table_with_a_station_level_column_stops_the_build(root, out):
    rows = [row | {"station_name": "POSTO UM LTDA"} for row in lake()["agg_price_weekly_state"]]
    write_table(root, "agg_price_weekly_state", rows, station_name=pa.string())

    with pytest.raises(SiteBuildError, match="agg_price_weekly_state: columns are"):
        build(root, out, now=clock)

    assert not out.exists()


def test_cell_below_the_minimum_of_stations_stops_the_build(root, out):
    rows = [*lake()["agg_price_weekly_state"], weekly(SEP_27, "GASOLINA", 7.1, 2, state_code="AC")]
    write_table(root, "agg_price_weekly_state", rows)

    with pytest.raises(SiteBuildError, match="n_stations >= 3"):
        build(root, out, now=clock)


def test_state_outside_the_reference_list_stops_the_build(root, out):
    rows = [*lake()["agg_price_weekly_state"], weekly(SEP_20, "GASOLINA", 6.0, state_code="XX")]
    write_table(root, "agg_price_weekly_state", rows)

    with pytest.raises(SiteBuildError, match="agg_price_weekly_state"):
        build(root, out, now=clock)


def test_curated_layer_without_a_published_week_stops_the_build(root, out):
    for table in PUBLIC_TABLES:
        write_table(root, table.name, [])

    with pytest.raises(SiteBuildError, match="nothing to publish"):
        build(root, out, now=clock)


def test_site_without_curated_tables_is_refused(tmp_path, out):
    root = tmp_path / "data"
    write_build(root)
    write_report(root)

    with pytest.raises(SiteBuildError, match="not built: fuel_price, usd_brl_daily, ipca_monthly"):
        build(root, out, now=clock)

    assert not out.exists()


def test_site_without_the_fact_table_is_refused(root, out):
    shutil.rmtree(curated_dir(root) / "fuel_price")

    with pytest.raises(SiteBuildError, match=r"not built: fuel_price$"):
        build(root, out, now=clock)


def test_empty_public_table_publishes_no_series_and_a_download_without_rows(root, out):
    write_table(root, "agg_price_weekly_flag", [])

    build(root, out, now=clock)

    assert read(out, "flags.json")["products"] == {}
    downloads = {entry["table"]: entry["rows"] for entry in read(out, "meta.json")["downloads"]}
    assert downloads["agg_price_weekly_flag"] == 0


def test_public_table_registered_with_a_station_column_is_refused(root, out, monkeypatch):
    # The whitelist is the first guard. This is the one behind it, should the whitelist go wrong.
    state = TABLE_BY_NAME["agg_price_weekly_state"]
    cnpj = Column("station_cnpj", "VARCHAR", "CNPJ of the station")
    leaky = dataclasses.replace(state, columns=(*state.columns, cnpj))
    leaky_public = tuple(leaky if table is state else table for table in PUBLIC_TABLES)
    monkeypatch.setattr(registry, "PUBLIC_TABLES", leaky_public)
    monkeypatch.setattr(site_downloads, "PUBLIC_TABLES", leaky_public)
    rows = [row | {"station_cnpj": "11222333000181"} for row in lake()["agg_price_weekly_state"]]
    write_table(root, "agg_price_weekly_state", rows, station_cnpj=pa.string())

    with pytest.raises(SiteBuildError, match="public table with the station-level column"):
        build(root, out, now=clock)

    assert not out.exists()


def test_event_about_an_unknown_fuel_stops_the_build(root, out):
    data_files.EVENTS_FILE.write_text(
        json.dumps([EVENT | {"fuels": ["QUEROSENE"]}]), encoding="utf-8"
    )

    with pytest.raises(SiteBuildError, match="QUEROSENE"):
        build(root, out, now=clock)


@pytest.mark.parametrize("day", ["10/09/2026", "2026-9-10", "20260910", "2026-09-31", None])
def test_event_whose_date_is_not_iso_stops_the_build(root, out, day):
    data_files.EVENTS_FILE.write_text(json.dumps([EVENT | {"date": day}]), encoding="utf-8")

    with pytest.raises(SiteBuildError, match="is not YYYY-MM-DD"):
        build(root, out, now=clock)


# ---------------------------------------------------------------- the contract of the data files


@pytest.fixture
def files(root, out) -> dict:
    """The data files of a good build, for a test to damage in one place."""
    build(root, out, now=clock)
    return {path.name: read(out, path.name) for path in (out / "data").iterdir()}


def test_data_files_of_a_build_keep_the_contract(files):
    check_data_files(files)


def test_week_axis_with_a_week_missing_breaks_the_contract(files):
    for name in ("national.json", "regions.json", "states.json", "flags.json"):
        files[name]["weeks"].remove("2026-10-04")

    with pytest.raises(SiteBuildError, match="consecutive Sundays"):
        check_data_files(files)


def test_week_axis_that_starts_on_a_monday_breaks_the_contract(files):
    mondays = ["2026-09-21", "2026-09-28", "2026-10-05", "2026-10-12"]
    for name in ("national.json", "regions.json", "states.json", "flags.json"):
        files[name]["weeks"] = mondays

    with pytest.raises(SiteBuildError, match="consecutive Sundays"):
        check_data_files(files)


def test_file_with_weeks_of_its_own_breaks_the_contract(files):
    files["flags.json"]["weeks"] = WEEKS[1:]

    with pytest.raises(SiteBuildError, match=r"flags\.json: its weeks are not the weeks of"):
        check_data_files(files)


def test_series_shorter_than_the_week_axis_breaks_the_contract(files):
    files["states.json"]["products"]["GASOLINA"]["SP"]["median"].pop()

    with pytest.raises(SiteBuildError, match=r"GASOLINA\.SP\.median: 3 values for 4 weeks"):
        check_data_files(files)


def test_week_level_array_shorter_than_the_axis_breaks_the_contract(files):
    files["national.json"]["usd_brl"].pop()

    with pytest.raises(SiteBuildError, match=r"usd_brl: 3 values for 4 weeks"):
        check_data_files(files)


def test_price_with_more_decimals_than_agreed_breaks_the_contract(files):
    files["regions.json"]["products"]["GASOLINA"]["SE"]["mean"][0] = 5.90004

    with pytest.raises(SiteBuildError, match=r"GASOLINA\.SE\.mean: not rounded to 3 decimals"):
        check_data_files(files)


def test_state_outside_the_reference_list_breaks_the_contract(files):
    gasoline = files["states.json"]["products"]["GASOLINA"]
    gasoline["XX"] = gasoline["SP"]

    with pytest.raises(SiteBuildError, match=r"\['XX'\] are not in the reference list"):
        check_data_files(files)


def test_series_with_a_field_nobody_agreed_on_breaks_the_contract(files):
    files["flags.json"]["products"]["GASOLINA"]["branded"]["p90"] = [None] * len(WEEKS)

    with pytest.raises(SiteBuildError, match="expected the keys"):
        check_data_files(files)


def test_meta_file_without_one_of_its_keys_breaks_the_contract(files):
    del files["meta.json"]["totals"]["municipalities"]

    with pytest.raises(SiteBuildError, match=r"meta\.json: totals: expected the keys"):
        check_data_files(files)


# ---------------------------------------------------------------- the quality gate


@pytest.mark.parametrize("status", ["fail", "a status nobody defined"])
def test_run_that_did_not_pass_its_quality_checks_is_not_published(root, out, status):
    write_report(root, status=status)

    with pytest.raises(SiteBuildError, match=f"status '{status}'"):
        build(root, out, now=clock)

    assert not out.exists()


def test_site_is_not_built_without_a_quality_report(root, out):
    (quality_dir(root) / "report.json").unlink()

    with pytest.raises(SiteBuildError, match="not found"):
        build(root, out, now=clock)

    assert not out.exists()


def test_run_that_only_warned_is_published(root, out):
    write_report(root, status="warn")

    build(root, out, now=clock)

    assert read(out, "quality.json")["status"] == "warn"


def test_report_that_says_pass_over_a_failed_check_is_not_published(root, out):
    write_report(root, checks=[{"id": "ipca_complete", "status": "fail"}])

    with pytest.raises(SiteBuildError, match="a failed check"):
        build(root, out, now=clock)

    assert not out.exists()


def test_run_that_failed_an_error_check_is_not_published(tmp_path, out):
    lake = Lake()
    lake.staging[MONTHLY] = not_staged(lake.collection[MONTHLY], "schema drift")
    root = lake.write(tmp_path / "data")
    write_build(root, "2026-11-26T09:29:00+00:00")
    report = build_report(
        root, started_at=datetime(2026, 11, 26, 9, 0, tzinfo=UTC), since=SEP_20, now=at(RUN_DAY)
    )

    assert (report["status"], publishable(report)) == ("fail", False)
    with pytest.raises(SiteBuildError, match="status 'fail'"):
        build(root, out, now=clock)
    assert not out.exists()


def test_report_older_than_the_curated_build_does_not_publish_it(root, out):
    # The tables were built again after the report: nothing has checked them.
    write_build(root, "2026-11-26T12:03:01+00:00")

    with pytest.raises(SiteBuildError, match="finished before the curated build"):
        build(root, out, now=clock)

    assert not out.exists()


def test_report_finished_in_the_second_the_tables_were_built_publishes_them(root, out):
    write_build(root, "2026-11-26T12:03:00.900000+00:00")  # the report says 12:03:00

    build(root, out, now=clock)

    assert (out / "data" / "meta.json").is_file()


def test_site_is_not_built_without_a_curated_build(root, out):
    (curated_dir(root) / "_build.json").unlink()

    with pytest.raises(SiteBuildError, match="no curated build"):
        build(root, out, now=clock)


def test_quality_report_without_a_section_stops_the_build(root, out):
    report = write_report(root)
    del report["funnel"]
    (quality_dir(root) / "report.json").write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(SiteBuildError, match="expected the keys"):
        build(root, out, now=clock)


# ---------------------------------------------------------------- downloads


def test_downloads_are_the_public_tables_as_parquet_and_csv(root, out):
    build(root, out, now=clock)

    listed = read(out, "meta.json")["downloads"]
    assert [entry["table"] for entry in listed] == [table.name for table in PUBLIC_TABLES]
    assert sorted(path.name for path in (out / "downloads").iterdir()) == sorted(
        f"{table.name}.{extension}" for table in PUBLIC_TABLES for extension in ("csv", "parquet")
    )
    for entry, table in zip(listed, PUBLIC_TABLES, strict=True):
        source = curated_dir(root) / table.path
        parquet = out / entry["files"]["parquet"]
        assert parquet.read_bytes() == source.read_bytes()  # the table itself, byte for byte
        published = pq.read_table(parquet)
        as_csv = pyarrow.csv.read_csv(out / entry["files"]["csv"])
        assert as_csv.column_names == [column.name for column in table.columns]
        assert as_csv.cast(published.schema).equals(published)
        assert entry["rows"] == published.num_rows == len(lake()[table.name])
        assert entry["description"] == table.description


def test_no_station_reaches_the_site(root, out):
    build(root, out, now=clock)

    stations = {row["station_cnpj"] for row in lake()["fuel_price"]}
    for path in out.rglob("*"):
        if path.suffix == ".parquet":
            assert not STATION_LEVEL_COLUMNS & set(pq.read_schema(path).names)
        elif path.is_file():
            text = path.read_text(encoding="utf-8")
            assert not any(cnpj in text for cnpj in stations), path
            assert "BOM JESUS" not in text, path  # nor a municipality


# ---------------------------------------------------------------- the site folder


def test_site_is_built_without_a_front_end_folder(root, out, caplog):
    result = build(root, out, now=clock)

    assert result.static_files == 0
    assert sorted(path.name for path in out.iterdir()) == [".nojekyll", "data", "downloads"]
    assert "no front-end" in caplog.text


def test_front_end_folder_is_copied_into_the_site(root, out):
    (folder.STATIC_DIR / "js").mkdir(parents=True)
    (folder.STATIC_DIR / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (folder.STATIC_DIR / "js" / "app.js").write_text("export {};", encoding="utf-8")

    result = build(root, out, now=clock)

    assert (out / "index.html").read_text(encoding="utf-8") == "<!doctype html>"
    assert (out / "js" / "app.js").read_text(encoding="utf-8") == "export {};"
    assert result.static_files == 2


def test_sample_data_of_the_front_end_is_not_published(root, out):
    (folder.STATIC_DIR / "data").mkdir(parents=True)
    (folder.STATIC_DIR / "data" / "meta.json").write_text('{"sample":true}', encoding="utf-8")
    (folder.STATIC_DIR / "data" / "extra.json").write_text('{"sample":true}', encoding="utf-8")
    (folder.STATIC_DIR / "index.html").write_text("<!doctype html>", encoding="utf-8")

    build(root, out, now=clock)

    assert "sample" not in read(out, "meta.json")
    assert not (out / "data" / "extra.json").exists()
    assert (out / "index.html").is_file()


def test_site_tells_github_pages_not_to_run_jekyll(root, out):
    build(root, out, now=clock)

    assert (out / ".nojekyll").read_bytes() == b""


def test_report_says_what_was_built(root, out):
    result = build(root, out, now=clock)

    assert result.out == out
    assert result.run_id == "20261126T120000Z"
    assert result.latest_week == "2026-10-11"
    assert result.weeks == 4
    assert result.data_bytes == {
        path.name: path.stat().st_size for path in (out / "data").iterdir()
    }
    assert len(result.data_bytes) == 7
    assert result.static_files == 0


def test_failed_build_leaves_the_previous_site_untouched(root, out):
    build(root, out, now=clock)
    before = snapshot(out)
    rows = lake()["agg_price_weekly_national"]
    rows[0]["price_mean"] = math.nan
    write_table(root, "agg_price_weekly_national", rows)

    with pytest.raises(SiteBuildError):
        build(root, out, now=clock)

    assert snapshot(out) == before
    assert [path.name for path in out.parent.iterdir()] == ["site"]  # no half-built folder left


def test_site_that_cannot_be_moved_into_place_leaves_the_previous_one_there(root, out, monkeypatch):
    build(root, out, now=clock)
    before = snapshot(out)
    rename = Path.rename

    def fail_moving_the_new_site_in(self: Path, target: Path) -> Path:
        if self.name.endswith(".building"):
            raise OSError("input/output error")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_moving_the_new_site_in)
    with pytest.raises(OSError, match="input/output error"):
        build(root, out, now=clock)

    assert snapshot(out) == before
    assert [path.name for path in out.parent.iterdir()] == ["site"]


def test_rebuild_replaces_the_site_as_a_whole(root, out):
    build(root, out, now=clock)
    (out / "page-of-an-older-version.html").write_text("old", encoding="utf-8")

    build(root, out, now=clock)

    assert not (out / "page-of-an-older-version.html").exists()
    assert [path.name for path in out.parent.iterdir()] == ["site"]


def test_same_inputs_and_clock_give_the_same_site_byte_for_byte(root, tmp_path):
    build(root, tmp_path / "first", now=clock)
    build(root, tmp_path / "second", now=clock)

    assert snapshot(tmp_path / "first") == snapshot(tmp_path / "second")


def test_folder_that_is_not_a_site_is_not_replaced(root, tmp_path):
    documents = tmp_path / "documents"
    documents.mkdir()
    (documents / "thesis.txt").write_text("years of work", encoding="utf-8")

    with pytest.raises(SiteBuildError, match="not replacing it"):
        build(root, documents, now=clock)

    assert (documents / "thesis.txt").read_text(encoding="utf-8") == "years of work"


def test_empty_folder_can_receive_the_site(root, out):
    out.mkdir(parents=True)

    build(root, out, now=clock)

    assert (out / "data" / "meta.json").is_file()
