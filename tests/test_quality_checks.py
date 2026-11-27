"""The dataset-level checks, each on a tiny data root built for the test.

``Lake`` describes a data root in which every check passes: two ANP files and
two Banco Central windows in the two state files, and nine survey weeks of
regular gasoline in the curated tables. A test changes the one thing it is
about, writes the root and reads the outcome of one check.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fuel_price_lakehouse.collect import landing
from fuel_price_lakehouse.config import Clock
from fuel_price_lakehouse.curate.tables import TABLES
from fuel_price_lakehouse.layout import (
    curated_dir,
    last_extract_path,
    manifest_path,
    staging_state_path,
)
from fuel_price_lakehouse.quality.checks import CHECKS, CheckResult, run_checks
from fuel_price_lakehouse.reference import REGION_OF_STATE

CHECK_IDS = [
    "collection_complete",
    "sources_reachable",
    "raw_files_read_strictly",
    "landing_copies_intact",
    "staging_complete",
    "row_counts_reconcile",
    "quarantine_rate",
    "file_quarantine_rate",
    "fuel_price_key_unique",
    "prices_fresh",
    "all_states_present",
    "latest_week_volume",
    "no_unexpected_survey_gap",
    "weekly_price_jump",
    "usd_fresh",
    "ipca_complete",
    "public_tables_have_no_station_columns",
]

RUN_DAY = date(2026, 11, 26)
LATEST_WEEK = date(2026, 11, 15)  # a Sunday
WEEKS = [LATEST_WEEK - timedelta(weeks=back) for back in range(8, -1, -1)]  # oldest first
LATEST_PRICE = LATEST_WEEK + timedelta(days=2)
LATEST_RATE = date(2026, 11, 25)
STATES = sorted(REGION_OF_STATE)

MONTHLY = "https://example.org/anp/2026-10-gasolina-etanol.csv"
FOUR_WEEKS = "https://example.org/anp/ultimas-4-semanas-gasolina-etanol.csv"
DOLLAR = "bcb_sgs/1/2026"
IPCA = "bcb_sgs/433/2026"
MONTHLY_LABEL = "anp monthly gasoline_ethanol 2026-10"

# ANP's own words for the first gap, and what the data shows for the second
# (docs/sources.md, section 1.8), written out week by week.
WEEKS_WITHOUT_SURVEY = [
    date(2020, 8, 23),
    date(2020, 8, 30),
    date(2020, 9, 6),
    date(2020, 9, 13),
    date(2020, 9, 20),
    date(2020, 9, 27),
    date(2020, 10, 4),
    date(2020, 10, 11),
    date(2022, 9, 18),
]

_ARROW_TYPES = {
    "VARCHAR": pa.string(),
    "DATE": pa.date32(),
    "DOUBLE": pa.float64(),
    "BIGINT": pa.int64(),
    "BOOLEAN": pa.bool_(),
}
_TABLES = {table.name: table for table in TABLES}


def at(day: date) -> Clock:
    return lambda: datetime.combine(day, time(9, 30), tzinfo=UTC)


def record(key: str, source: str, rows: int, **details: str) -> dict:
    """A collection manifest record of a file that was ingested once."""
    sha256 = hashlib.sha256(key.encode()).hexdigest()
    return {
        "key": key,
        "source": source,
        "url": key if source == "anp" else f"https://example.org/{key}",
        "status": "ok",
        "collected_at": "2026-11-26T01:00:00+00:00",
        "size_bytes": 100 * rows,
        "sha256": sha256,
        "rows": rows,
        "raw_path": f"raw/{source}/{sha256[:12]}.parquet",
        "landing_path": f"landing/{source}/{sha256[:2]}/{sha256}.zst",
        "revision": 1,
        "details": details,
    }


def staged(collected: dict, **rule_failures: int) -> dict:
    """The staging entry of a collected file; each rule failure is one quarantined row."""
    quarantined = sum(rule_failures.values())
    place = collected["raw_path"].removeprefix("raw/")
    return {
        "source": collected["source"],
        "details": collected["details"],
        "raw_sha256": collected["sha256"],
        "staging_version": 1,
        "staged_at": "2026-11-26T02:00:00+00:00",
        "staged_path": f"staged/{place}",
        "quarantine_path": f"quarantine/{place}" if quarantined else None,
        "rows_read": collected["rows"],
        "rows_staged": collected["rows"] - quarantined,
        "rows_quarantined": quarantined,
        "rule_failures": rule_failures,
        "rule_warnings": {},
        "error": None,
    }


def not_staged(collected: dict, error: str) -> dict:
    """The staging entry of a file that failed as a whole."""
    counts = {"rows_read": 0, "rows_staged": 0, "staged_path": None, "error": error}
    return {**staged(collected), **counts}


def price(week: date, state: str = "SP", station: int = 0, **changes: object) -> dict:
    """A row of ``fuel_price``: regular gasoline at one station on the Tuesday of ``week``."""
    return {
        "station_cnpj": f"{STATES.index(state):02d}{station:012d}",
        "product": "GASOLINA",
        "collected_on": week + timedelta(days=2),
        "survey_week": week,
        "sale_price": 6.0,
        "purchase_price": None,
        "unit": "BRL/L",
        "state_code": state,
        "region_code": REGION_OF_STATE[state],
        "municipality": "MUNICIPIO",
        "brand": "BRANCA",
        "source_kind": "monthly",
        "source_period": "2026-10",
        "n_candidates": 1,
        "price_conflict": False,
        **changes,
    }


def weekly(week: date, median: float = 6.0, **key: str) -> dict:
    """A row of a weekly aggregate; ``key`` is what the table has besides week and product."""
    statistics = dict.fromkeys(("price_mean", "price_median", "price_p10", "price_p90"), median)
    counts = {"n_stations": 27, "n_observations": 27}
    return {
        "survey_week": week,
        "product": "GASOLINA",
        "unit": "BRL/L",
        **key,
        **counts,
        **statistics,
    }


def national(week: date, median: float = 6.0, product: str = "GASOLINA") -> dict:
    """A row of ``agg_price_weekly_national``."""
    return {
        **weekly(week, median),
        "product": product,
        "n_states": 27,
        "usd_brl_mean": 5.4,
        "ipca_deflator": 1.0,
        "ipca_provisional": False,
        "price_mean_real": median,
    }


def _collection() -> dict[str, dict]:
    return {
        MONTHLY: record(
            MONTHLY,
            "anp",
            500,
            kind="monthly",
            fuel_group="gasoline_ethanol",
            period="2026-10",
            published_hint="",
            csv_parsing="strict",
        ),
        FOUR_WEEKS: record(
            FOUR_WEEKS,
            "anp",
            300,
            kind="last_four_weeks",
            fuel_group="gasoline_ethanol",
            period="latest",
            published_hint="23/11/2026",
        ),
        DOLLAR: record(DOLLAR, "bcb_sgs", 190, series="1", year="2026"),
        IPCA: record(IPCA, "bcb_sgs", 10, series="433", year="2026"),
    }


def _tables() -> dict[str, list[dict]]:
    return {
        "fuel_price": [price(week, state) for week in WEEKS for state in STATES],
        "usd_brl_daily": [
            {"observed_on": LATEST_RATE - timedelta(days=back), "usd_brl": 5.4} for back in range(3)
        ],
        "ipca_monthly": [
            {"month": month, "pct_change": 0.5, "index": 100.5, "deflator_to_latest": 1.0}
            for month in (date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1))
        ],
        "agg_price_weekly_national": [national(week) for week in WEEKS],
        "agg_price_weekly_region": [weekly(LATEST_WEEK, region_code="SE")],
        "agg_price_weekly_state": [weekly(LATEST_WEEK, state_code="SP")],
        "agg_price_weekly_flag": [weekly(LATEST_WEEK, flag_type="white_flag")],
    }


@dataclass
class Lake:
    """A data root in which every check passes, until a test changes something."""

    collection: dict[str, dict] = field(default_factory=_collection)
    """The records of ``manifest/collection.json``: 1,000 rows in four files."""
    staging: dict[str, dict] = field(default_factory=dict)
    """The entries of ``manifest/staging.json``. Left empty: every file staged whole."""
    tables: dict[str, list[dict]] = field(default_factory=_tables)
    """The rows of each curated table."""
    build: dict[str, int] = field(
        default_factory=lambda: {
            "duplicates_removed": 12,
            "price_conflicts": 1,
            "cells_suppressed": 3,
        }
    )
    """What ``curated/_build.json`` says of the build."""
    last_extract: dict | None = field(default_factory=lambda: {"failed": []})
    """What ``manifest/last_extract.json`` says of the latest collection."""

    def __post_init__(self) -> None:
        self.staging = self.staging or {
            key: staged(collected) for key, collected in self.collection.items()
        }

    def write(self, root: Path) -> Path:
        write_json(manifest_path(root), {"version": 1, "files": self.collection})
        if self.last_extract is not None:
            write_json(last_extract_path(root), self.last_extract)
        for collected in self.collection.values():
            if collected.get("landing_path") and collected.get("sha256"):  # bytes of that hash
                bytes_ = collected["key"].encode()
                landing.store_bytes(bytes_, collected["sha256"], collected["source"], root)
        write_json(staging_state_path(root), {"version": 1, "files": self.staging})
        for name, rows in self.tables.items():
            write_table(root, name, rows)
        write_json(curated_dir(root) / "_build.json", self.build)
        return root


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def write_table(root: Path, name: str, rows: list[dict]) -> None:
    """Write a curated table as the build does: its registered columns, one file per year."""
    table = _TABLES[name]
    schema = pa.schema([(column.name, _ARROW_TYPES[column.type]) for column in table.columns])
    files: dict[str, list[dict]] = {}
    for row in rows:
        year = row[table.partitioned_by_year_of].year if table.partitioned_by_year_of else ""
        files.setdefault(str(year), []).append(row)
    for year, part in (files or {str(RUN_DAY.year): []}).items():
        path = curated_dir(root) / table.path.replace("*", year)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(part, schema=schema), path)


def outcome(lake: Lake, root: Path, check_id: str, today: date = RUN_DAY) -> CheckResult:
    """Write the lake and evaluate one check on it."""
    results = run_checks(lake.write(root), now=at(today))
    return next(result for result in results if result.id == check_id)


def test_catalogue_has_the_published_check_ids_in_order():
    assert [check.id for check in CHECKS] == CHECK_IDS


def test_every_check_is_described_in_english_and_portuguese():
    for check in CHECKS:
        assert check.description.endswith(".") and check.description_pt.endswith(".")
        assert check.description != check.description_pt


def test_healthy_run_passes_every_check(tmp_path):
    results = run_checks(Lake().write(tmp_path), now=at(RUN_DAY))

    assert [(result.id, result.status) for result in results] == [
        (check_id, "pass") for check_id in CHECK_IDS
    ]
    assert all(result.detail == "" for result in results)


def test_data_root_with_nothing_in_it_fails_every_check_and_says_what_is_missing(tmp_path):
    results = {result.id: result for result in run_checks(tmp_path, now=at(RUN_DAY))}

    for check in CHECKS:
        result = results[check.id]
        assert result.status == ("fail" if check.severity == "error" else "warn")
        assert result.observed == "not evaluated"
    assert results["collection_complete"].detail == (
        "no collection manifest: run the collection first"
    )
    assert results["quarantine_rate"].detail == "no staging state: run the staging first"
    assert results["prices_fresh"].detail == (
        "curated table not built: fuel_price; run the curated build first"
    )
    assert list(tmp_path.iterdir()) == []  # checking writes nothing


def test_curated_tables_without_rows_fail_their_checks_instead_of_raising(tmp_path):
    lake = Lake()
    lake.tables = {name: [] for name in lake.tables}

    results = {result.id: result for result in run_checks(lake.write(tmp_path), now=at(RUN_DAY))}

    assert results["prices_fresh"].observed == "no collection date"
    assert results["usd_fresh"].observed == "no rate"
    assert results["ipca_complete"].observed == "no IPCA months"
    assert [results[check_id].status for check_id in CHECK_IDS[8:]] == [
        "pass",  # fuel_price_key_unique: no rows, no duplicated key
        "warn",
        "warn",
        "warn",
        "warn",
        "warn",
        "warn",
        "fail",  # ipca_complete
        "pass",  # public_tables_have_no_station_columns: the files are there
    ]


def test_collection_in_which_every_source_answered_reaches_them_all(tmp_path):
    result = outcome(Lake(), tmp_path, "sources_reachable")

    assert (result.status, result.observed) == ("pass", "0 failures in the latest collection")


def test_source_that_did_not_answer_warns_and_says_what_failed(tmp_path):
    lake = Lake(last_extract={"failed": [{"key": "anp", "detail": "HTTP 503 from gov.br"}]})

    result = outcome(lake, tmp_path, "sources_reachable")

    assert (result.status, result.severity) == ("warn", "warning")
    assert result.observed == "1 failures in the latest collection"
    assert result.detail == "anp: HTTP 503 from gov.br"


def test_data_root_never_collected_into_cannot_say_whether_the_sources_answered(tmp_path):
    result = outcome(Lake(last_extract=None), tmp_path, "sources_reachable")

    assert (result.status, result.observed) == ("warn", "not evaluated")
    assert result.detail == "no record of the latest collection: run the collection"


def test_file_that_was_never_collected_fails_the_run(tmp_path):
    lake = Lake()
    never = {"status": "failed", "sha256": None, "rows": None, "raw_path": None}
    lake.collection[MONTHLY].update(never, last_error="HTTP 503 from https://example.org")
    del lake.staging[MONTHLY]

    result = outcome(lake, tmp_path, "collection_complete")

    assert (result.status, result.severity) == ("fail", "error")
    assert result.observed == (
        "1 of 4 files never collected; 0 kept from an earlier download after a failed one"
    )
    assert result.detail == f"{MONTHLY_LABEL}: never collected"  # the error text is not repeated


def test_failed_download_with_an_earlier_copy_in_use_is_only_a_warning(tmp_path):
    lake = Lake()
    lake.collection[MONTHLY]["last_error"] = "HTTP 503"

    result = outcome(lake, tmp_path, "collection_complete")

    assert (result.status, result.severity) == ("warn", "warning")
    assert result.detail == f"{MONTHLY_LABEL}: latest download failed"


def test_window_that_never_downloaded_is_named_without_its_details(tmp_path):
    lake = Lake()
    lake.collection["bcb_sgs/433/2027"] = {
        "key": "bcb_sgs/433/2027",
        "source": "bcb_sgs",
        "url": "https://example.org/bcb_sgs/433/2027",
        "last_error": "timed out",
    }

    result = outcome(lake, tmp_path, "collection_complete")

    assert result.status == "fail"
    assert result.detail == "bcb_sgs series_433 2027: never collected"


def test_anp_file_read_by_the_lenient_reader_warns(tmp_path):
    lake = Lake()
    lake.collection[MONTHLY]["details"]["csv_parsing"] = "lenient"

    result = outcome(lake, tmp_path, "raw_files_read_strictly")

    assert result.status == "warn"
    assert result.observed == "1 of 2 ANP files read by the lenient reader"
    assert result.detail == MONTHLY_LABEL


def test_collected_file_without_a_staging_entry_fails_the_run(tmp_path):
    lake = Lake()
    del lake.staging[MONTHLY]

    result = outcome(lake, tmp_path, "staging_complete")

    assert result.status == "fail"
    assert result.observed == "1 of 4 collected files not staged as they are now"
    assert result.detail == f"{MONTHLY_LABEL}: not staged"


def test_file_level_staging_error_fails_the_run_without_quoting_the_error(tmp_path):
    lake = Lake()
    # A file that lost its header: its first row was taken for the column names.
    drift = "the header is not the expected one (unexpected ['POSTO UM LTDA', '11222333000181'])"
    lake.staging[MONTHLY] = not_staged(lake.collection[MONTHLY], drift)

    result = outcome(lake, tmp_path, "staging_complete")

    assert result.status == "fail"
    assert result.detail == f"{MONTHLY_LABEL}: staging failed"


def test_file_staged_before_it_was_republished_fails_the_run(tmp_path):
    lake = Lake()
    lake.collection[MONTHLY]["sha256"] = "f" * 64  # collected again since it was staged

    result = outcome(lake, tmp_path, "staging_complete")

    assert result.status == "fail"
    assert result.detail == f"{MONTHLY_LABEL}: staged from an earlier download"


def test_window_without_observations_has_nothing_to_stage(tmp_path):
    lake = Lake()
    lake.collection[IPCA].update(rows=0, raw_path=None)
    del lake.staging[IPCA]

    assert outcome(lake, tmp_path, "staging_complete").status == "pass"


def test_file_that_lost_a_row_in_staging_fails_the_run(tmp_path):
    lake = Lake()
    lake.staging[MONTHLY]["rows_staged"] -= 1

    result = outcome(lake, tmp_path, "row_counts_reconcile")

    assert result.status == "fail"
    assert result.observed == "1 of 4 staged files with row counts that do not add up"
    assert result.detail == f"{MONTHLY_LABEL}: 500 collected, 500 read, 499 staged, 0 quarantined"


def test_file_staged_with_fewer_rows_than_were_collected_fails_the_run(tmp_path):
    lake = Lake()
    lake.collection[MONTHLY]["rows"] = 501

    result = outcome(lake, tmp_path, "row_counts_reconcile")

    assert result.status == "fail"
    assert result.detail == f"{MONTHLY_LABEL}: 501 collected, 500 read, 500 staged, 0 quarantined"


def test_file_that_failed_staging_has_no_row_counts_to_reconcile(tmp_path):
    lake = Lake()
    lake.staging[MONTHLY] = not_staged(lake.collection[MONTHLY], "schema drift")

    result = outcome(lake, tmp_path, "row_counts_reconcile")

    assert result.status == "pass"  # staging_complete is the check that reports the file
    assert result.observed == "0 of 3 staged files with row counts that do not add up"


@pytest.mark.parametrize(
    ("quarantined", "status", "severity"),
    [
        (10, "pass", "warning"),  # exactly 1 % of the 1,000 rows
        (11, "warn", "warning"),
        (50, "warn", "warning"),  # exactly 5 %
        (51, "fail", "error"),
    ],
)
def test_quarantine_rate_warns_above_one_percent_and_fails_above_five(
    tmp_path, quarantined, status, severity
):
    lake = Lake()
    lake.staging[MONTHLY] = staged(lake.collection[MONTHLY], sale_price_is_a_number=quarantined)

    result = outcome(lake, tmp_path, "quarantine_rate")

    assert (result.status, result.severity) == (status, severity)
    assert result.observed.endswith(f"({quarantined} of 1000 non-blank rows)")
    assert result.expected == "at most 1%"


def test_blank_rows_count_neither_as_quarantined_nor_as_read(tmp_path):
    lake = Lake()
    lake.staging[MONTHLY] = staged(
        lake.collection[MONTHLY], row_not_blank=100, sale_price_is_a_number=9
    )

    result = outcome(lake, tmp_path, "quarantine_rate")

    assert result.status == "pass"  # 109 of 1,000 rows are in quarantine, 9 for a reason
    assert result.observed == "1.000% (9 of 900 non-blank rows)"


@pytest.mark.parametrize(("quarantined", "status"), [(5, "pass"), (6, "warn")])
def test_single_file_above_one_percent_in_quarantine_warns(tmp_path, quarantined, status):
    lake = Lake()
    lake.staging[MONTHLY] = staged(lake.collection[MONTHLY], sale_price_is_a_number=quarantined)

    result = outcome(lake, tmp_path, "file_quarantine_rate")

    assert result.status == status
    assert result.observed == f"{quarantined - 5} of 4 files above 1%"
    if status == "warn":
        assert result.detail == f"{MONTHLY_LABEL}: 1.200% (6 of 500 non-blank rows)"


@pytest.mark.parametrize(("for_a_reason", "status"), [(4, "pass"), (5, "warn")])
def test_file_ending_in_blank_rows_is_judged_on_its_other_rows(tmp_path, for_a_reason, status):
    lake = Lake()
    lake.staging[MONTHLY] = staged(
        lake.collection[MONTHLY], row_not_blank=100, sale_price_is_a_number=for_a_reason
    )

    result = outcome(lake, tmp_path, "file_quarantine_rate")

    assert result.status == status  # 4 of the 400 rows that are not blank is exactly 1 %


def test_station_with_two_prices_for_a_product_and_day_fails_the_run(tmp_path):
    lake = Lake()
    lake.tables["fuel_price"].append(price(LATEST_WEEK, sale_price=6.5))

    result = outcome(lake, tmp_path, "fuel_price_key_unique")

    assert (result.status, result.severity) == ("fail", "error")
    assert result.observed == "1 duplicated key in 244 rows"
    assert result.detail == ""  # the key is a station: counted, never shown


@pytest.mark.parametrize(("days", "status"), [(21, "pass"), (22, "warn")])
def test_prices_older_than_three_weeks_warn(tmp_path, days, status):
    result = outcome(Lake(), tmp_path, "prices_fresh", LATEST_PRICE + timedelta(days=days))

    assert result.status == status
    assert result.observed == f"{days} days (latest collection date 2026-11-17)"


def test_state_without_gasoline_prices_in_the_latest_week_warns(tmp_path):
    lake = Lake()
    lake.tables["fuel_price"] = [
        # Roraima was surveyed in the latest week, but not for regular gasoline.
        {**row, "product": "ETANOL"}
        if (row["state_code"], row["survey_week"]) == ("RR", LATEST_WEEK)
        else row
        for row in lake.tables["fuel_price"]
    ]

    result = outcome(lake, tmp_path, "all_states_present")

    assert result.status == "warn"
    assert result.observed == "26 of 27 states in the week of 2026-11-15"
    assert result.detail == "missing: RR"


@pytest.mark.parametrize(("stations", "status"), [(5, "pass"), (4, "warn")])
def test_latest_week_with_less_than_half_the_usual_stations_warns(tmp_path, stations, status):
    lake = Lake()
    lake.tables["fuel_price"] = [
        price(week, station=station)
        for week in WEEKS
        for station in range(stations if week == LATEST_WEEK else 10)
    ]

    result = outcome(lake, tmp_path, "latest_week_volume")

    assert result.status == status
    assert result.observed == f"{stations} stations; median of the previous 8 weeks 10"


def test_volume_baseline_is_the_median_of_the_eight_weeks_before_the_latest(tmp_path):
    lake = Lake()
    usual = {week: 10 if n < 4 else 20 for n, week in enumerate(WEEKS[:-1])}  # median 15
    older = {WEEKS[0] - timedelta(weeks=1): 20}  # a ninth week would take the median to 20
    lake.tables["fuel_price"] = [
        price(week, station=station)
        for week, stations in {**older, **usual, LATEST_WEEK: 8}.items()
        for station in range(stations)
    ]

    result = outcome(lake, tmp_path, "latest_week_volume")

    assert result.status == "pass"
    assert result.observed == "8 stations; median of the previous 8 weeks 15"


def test_first_week_of_a_history_has_no_volume_to_compare_with(tmp_path):
    lake = Lake()
    lake.tables["fuel_price"] = [price(LATEST_WEEK)]

    result = outcome(lake, tmp_path, "latest_week_volume")

    assert result.status == "pass"
    assert result.observed == "1 station; no earlier week to compare with"


def test_weeks_in_which_anp_ran_no_survey_are_not_gaps(tmp_path):
    lake = Lake()
    first, last = date(2020, 8, 16), date(2022, 9, 25)
    every_week = [first + timedelta(weeks=n) for n in range((last - first).days // 7 + 1)]
    lake.tables["fuel_price"] = [
        price(week) for week in every_week if week not in WEEKS_WITHOUT_SURVEY
    ]

    result = outcome(lake, tmp_path, "no_unexpected_survey_gap")

    assert result.status == "pass"
    assert result.observed == "0 unexpected and 9 known weeks without prices, of 111 weeks"


def test_week_without_prices_that_is_not_a_known_gap_warns(tmp_path):
    lake = Lake()
    lake.tables["fuel_price"] = [
        row for row in lake.tables["fuel_price"] if row["survey_week"] != WEEKS[4]
    ]

    result = outcome(lake, tmp_path, "no_unexpected_survey_gap")

    assert result.status == "warn"
    assert result.observed == "1 unexpected and 0 known weeks without prices, of 9 weeks"
    assert result.detail == "weeks starting 2026-10-18"


@pytest.mark.parametrize(
    ("median", "status", "detail"),
    [
        (4.5996, "pass", ""),
        (4.6, "warn", "GASOLINA +15.00%"),  # exactly 15 % above 4.00
        (3.4004, "pass", ""),
        (3.4, "warn", "GASOLINA -15.00%"),
    ],
)
def test_national_median_that_moves_fifteen_percent_in_a_week_warns(
    tmp_path, median, status, detail
):
    lake = Lake()
    lake.tables["agg_price_weekly_national"] = [
        national(WEEKS[-2], 4.0),
        national(LATEST_WEEK, median),
        national(WEEKS[-2], 100.0, "GLP"),
        national(LATEST_WEEK, 101.0, "GLP"),
    ]

    result = outcome(lake, tmp_path, "weekly_price_jump")

    assert (result.status, result.detail) == (status, detail)
    assert result.expected == "less than 15% for every product"


def test_price_move_is_measured_against_the_previous_published_week(tmp_path):
    lake = Lake()
    lake.tables["agg_price_weekly_national"] = [
        national(WEEKS[0], 2.0),
        national(WEEKS[1], 4.0),  # doubled, long before the latest week: history, not news
        national(LATEST_WEEK, 4.2),  # the weeks in between had too few stations to publish
    ]

    result = outcome(lake, tmp_path, "weekly_price_jump")

    assert result.status == "pass"
    assert result.observed == "largest move 5.00% (GASOLINA)"


@pytest.mark.parametrize(("days", "status"), [(7, "pass"), (8, "warn")])
def test_dollar_rate_older_than_a_week_warns(tmp_path, days, status):
    result = outcome(Lake(), tmp_path, "usd_fresh", LATEST_RATE + timedelta(days=days))

    assert result.status == status
    assert result.observed == f"{days} days (latest rate 2026-11-25)"


def test_dollar_rate_of_the_day_before_is_one_day_old(tmp_path):
    result = outcome(Lake(), tmp_path, "usd_fresh")

    assert result.observed == "1 day (latest rate 2026-11-25)"


def test_missing_ipca_month_fails_the_run(tmp_path):
    lake = Lake()
    del lake.tables["ipca_monthly"][1]

    result = outcome(lake, tmp_path, "ipca_complete")

    assert (result.status, result.severity) == ("fail", "error")
    assert result.observed == "1 missing month between 2026-08 and 2026-10"


def test_public_table_with_a_station_column_fails_the_run(tmp_path):
    root = Lake().write(tmp_path)
    path = curated_dir(root) / "agg_price_weekly_state.parquet"
    leaked = pq.read_table(path).append_column("station_cnpj", pa.array(["11222333000181"]))
    pq.write_table(leaked, path)

    results = {result.id: result for result in run_checks(root, now=at(RUN_DAY))}
    result = results["public_tables_have_no_station_columns"]

    assert (result.status, result.severity) == ("fail", "error")
    assert result.observed == "1 station-level column in the 6 public tables"
    assert result.detail == "agg_price_weekly_state: station_cnpj"


def test_missing_or_altered_landed_copy_warns(tmp_path):
    lake = Lake()
    root = lake.write(tmp_path)
    first, second = list(lake.collection.values())[:2]
    (root / first["landing_path"]).unlink()
    (root / second["landing_path"]).unlink()  # replaced by other bytes under the same name
    landing.store_bytes(b"other bytes", second["sha256"], second["source"], root)

    result = next(r for r in run_checks(root, now=at(RUN_DAY)) if r.id == "landing_copies_intact")

    assert result.status == "warn"
    assert result.observed.startswith("2 of ")
    assert "landed copy missing" in result.detail
    assert "does not match its SHA-256" in result.detail


def test_record_from_before_the_landing_zone_warns(tmp_path):
    lake = Lake()
    next(iter(lake.collection.values()))["landing_path"] = None

    result = outcome(lake, tmp_path, "landing_copies_intact")

    assert result.status == "warn"
    assert "no landed copy" in result.detail
