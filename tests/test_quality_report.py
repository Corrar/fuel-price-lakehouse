"""The quality report: its shape, its numbers, its history, and what it must never contain.

The data roots are the ones of ``test_quality_checks``: a ``Lake`` in which
every check passes, changed by each test where it matters.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from fuel_price_lakehouse.layout import quality_dir
from fuel_price_lakehouse.quality.report import build_report, publishable
from fuel_price_lakehouse.stage import anp, bcb_sgs

from .test_quality_checks import (
    CHECK_IDS,
    DOLLAR,
    FOUR_WEEKS,
    IPCA,
    LATEST_WEEK,
    MONTHLY,
    RUN_DAY,
    Lake,
    at,
    not_staged,
    price,
    record,
    staged,
)

STARTED_AT = datetime(2026, 11, 26, 9, 25, 7, tzinfo=UTC)
SINCE = date(2020, 1, 1)

# The report as the dashboard reads it: every section and its keys, in order.
REPORT = ("run", "status", "freshness", "funnel", "rules", "files", "checks", "history")
SECTIONS = {
    "run": ("id", "started_at", "finished_at", "code_version", "since"),
    "freshness": (
        "latest_collection_date",
        "latest_week",
        "days_since_latest",
        "usd_latest",
        "ipca_latest_month",
    ),
    "funnel": (
        "files",
        "rows_raw",
        "rows_staged",
        "rows_quarantined",
        "rows_curated",
        "duplicates_removed",
        "price_conflicts",
        "cells_suppressed",
    ),
}
LISTS = {
    "rules": (
        "id",
        "source",
        "severity",
        "description",
        "description_pt",
        "rows",
        "share",
        "by_year",
    ),
    "files": (
        "source",
        "kind",
        "fuel_group",
        "period",
        "rows_read",
        "rows_staged",
        "rows_quarantined",
        "collected_at",
        "sha256",
        "revision",
        "status",
    ),
    "checks": (
        "id",
        "severity",
        "status",
        "description",
        "description_pt",
        "observed",
        "expected",
        "detail",
    ),
    "history": (
        "run_id",
        "finished_at",
        "status",
        "rows_raw",
        "rows_staged",
        "rows_quarantined",
        "rows_curated",
        "latest_week",
        "checks_failed",
        "checks_warned",
    ),
}

SEMESTER = "https://example.org/anp/ca-2025-01.zip"
CNPJ = re.compile(r"\d{14}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")


def report_of(lake: Lake, root: Path, started_at: datetime = STARTED_AT, **options) -> dict:
    return build_report(
        lake.write(root), started_at=started_at, since=SINCE, now=at(RUN_DAY), **options
    )


def saved(root: Path) -> dict:
    return json.loads((quality_dir(root) / "report.json").read_text(encoding="utf-8"))


def history(root: Path) -> list[dict]:
    lines = (quality_dir(root) / "history.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def semester_of_2025(**rule_failures: int) -> tuple[dict, dict]:
    """A third ANP file, of another year: its manifest record and its staging entry."""
    collected = record(
        SEMESTER, "anp", 1000, kind="semester", fuel_group="automotive", period="2025-S1"
    )
    return collected, staged(collected, **rule_failures)


def test_report_has_the_documented_sections_and_keys(tmp_path):
    report = report_of(Lake(), tmp_path)

    assert tuple(report) == REPORT
    for section, keys in SECTIONS.items():
        assert tuple(report[section]) == keys
    for section, keys in LISTS.items():
        assert report[section], section
        assert all(tuple(entry) == keys for entry in report[section])
    assert report["status"] == "pass"


def test_report_on_disk_is_the_report_returned(tmp_path):
    report = report_of(Lake(), tmp_path)

    assert saved(tmp_path) == report
    assert not list(tmp_path.rglob("*.tmp"))


def test_run_is_named_after_the_moment_it_started(tmp_path):
    in_brasilia = STARTED_AT.astimezone(timezone(timedelta(hours=-3)))

    report = report_of(Lake(), tmp_path, in_brasilia, code_version="abc1234")

    assert report["run"] == {
        "id": "20261126T092507Z",
        "started_at": "2026-11-26T09:25:07Z",
        "finished_at": "2026-11-26T09:30:00Z",
        "code_version": "abc1234",
        "since": "2020-01",
    }


def test_run_without_a_known_code_version_says_so(tmp_path):
    assert report_of(Lake(), tmp_path)["run"]["code_version"] is None


def test_freshness_is_counted_from_the_day_of_the_report(tmp_path):
    assert report_of(Lake(), tmp_path)["freshness"] == {
        "latest_collection_date": "2026-11-17",
        "latest_week": "2026-11-15",
        "days_since_latest": 9,
        "usd_latest": "2026-11-25",
        "ipca_latest_month": "2026-10",
    }


def test_funnel_follows_the_rows_of_both_sources_through_the_layers(tmp_path):
    lake = Lake()
    lake.staging[MONTHLY] = staged(lake.collection[MONTHLY], sale_price_is_a_number=7)

    assert report_of(lake, tmp_path)["funnel"] == {
        "files": 4,
        "rows_raw": 1000,  # 500 + 300 from ANP, 190 + 10 from Banco Central
        "rows_staged": 993,
        "rows_quarantined": 7,
        "rows_curated": 249,  # 243 prices, 3 dollar rates, 3 IPCA months
        "duplicates_removed": 12,
        "price_conflicts": 1,
        "cells_suppressed": 3,
    }


def test_file_that_was_never_collected_is_not_in_the_funnel_or_the_files(tmp_path):
    lake = Lake()
    lake.collection[SEMESTER] = {
        "key": SEMESTER,
        "source": "anp",
        "url": SEMESTER,
        "details": {"kind": "semester", "fuel_group": "automotive", "period": "2025-S1"},
        "last_error": "HTTP 503",
    }

    report = report_of(lake, tmp_path)

    assert report["funnel"]["files"] == 4
    assert [file["period"] for file in report["files"] if file["source"] == "anp"] == [
        "2026-10",
        "latest",
    ]


def test_every_rule_of_both_sources_is_listed_in_catalogue_order_zeros_included(tmp_path):
    rules = report_of(Lake(), tmp_path)["rules"]

    assert [(rule["source"], rule["id"]) for rule in rules] == [
        *(("anp", rule.id) for rule in anp.RULES),
        *(("bcb_sgs", rule.id) for rule in bcb_sgs.RULES),
    ]
    assert all((rule["rows"], rule["share"]) == (0, 0.0) for rule in rules)
    blank = rules[0]
    assert blank["id"] == "row_not_blank"
    assert blank["severity"] == "error"
    assert blank["description"] == anp.RULES[0].description
    assert blank["description_pt"] == anp.RULES[0].description_pt


def test_rule_counts_add_up_error_and_warning_rules_alike(tmp_path):
    lake = Lake()
    lake.collection[SEMESTER], lake.staging[SEMESTER] = semester_of_2025(row_not_blank=20)
    lake.staging[MONTHLY] = staged(lake.collection[MONTHLY], row_not_blank=4)
    lake.staging[MONTHLY]["rule_warnings"] = {"postal_code_is_valid": 9}
    lake.staging[IPCA] = staged(lake.collection[IPCA], value_is_a_number=1)

    rules = {(rule["source"], rule["id"]): rule for rule in report_of(lake, tmp_path)["rules"]}

    blank = rules["anp", "row_not_blank"]
    assert (blank["rows"], blank["share"]) == (24, 0.01333)  # of the 1,800 ANP rows
    postal_code = rules["anp", "postal_code_is_valid"]
    assert (postal_code["severity"], postal_code["rows"]) == ("warning", 9)
    number = rules["bcb_sgs", "value_is_a_number"]
    assert (number["rows"], number["share"]) == (1, 0.005)  # of the 200 Banco Central rows


def test_rule_counts_are_split_by_the_year_of_each_file(tmp_path):
    lake = Lake()
    lake.collection[SEMESTER], lake.staging[SEMESTER] = semester_of_2025(row_not_blank=20)
    lake.staging[MONTHLY] = staged(lake.collection[MONTHLY], row_not_blank=4)
    # A rolling file has no period: it counts under the year ANP's page gave it...
    lake.collection[FOUR_WEEKS]["details"]["published_hint"] = "04/01/2027"
    lake.staging[FOUR_WEEKS] = staged(lake.collection[FOUR_WEEKS], row_not_blank=2)
    # ...and, when the page gave none, under the year it was downloaded.
    undated = FOUR_WEEKS.replace("gasolina-etanol", "glp")
    lake.collection[undated] = {
        **record(undated, "anp", 100, kind="last_four_weeks", fuel_group="lpg", period="latest"),
        "collected_at": "2028-01-03T01:00:00+00:00",
    }
    lake.staging[undated] = staged(lake.collection[undated], row_not_blank=1)
    lake.collection["bcb_sgs/433/2025"] = record(
        "bcb_sgs/433/2025", "bcb_sgs", 12, series="433", year="2025"
    )
    lake.staging["bcb_sgs/433/2025"] = staged(
        lake.collection["bcb_sgs/433/2025"], value_is_a_number=1
    )

    rules = {(rule["source"], rule["id"]): rule for rule in report_of(lake, tmp_path)["rules"]}

    assert rules["anp", "row_not_blank"]["by_year"] == {
        "2025": 20,
        "2026": 4,
        "2027": 2,
        "2028": 1,
    }
    # Every year the source has a file for, also when the rule caught nothing in it.
    assert rules["anp", "field_count_is_16"]["by_year"] == dict.fromkeys(
        ("2025", "2026", "2027", "2028"), 0
    )
    assert rules["bcb_sgs", "value_is_a_number"]["by_year"] == {"2025": 1, "2026": 0}


def test_files_are_sorted_by_source_period_kind_and_fuel_group(tmp_path):
    lake = Lake()
    lake.collection[SEMESTER], lake.staging[SEMESTER] = semester_of_2025()
    for group in ("lpg", "diesel_cng"):
        key = MONTHLY.replace("gasolina-etanol", group)
        details = {"kind": "monthly", "fuel_group": group, "period": "2026-10"}
        lake.collection[key] = record(key, "anp", 10, **details)
        lake.staging[key] = staged(lake.collection[key])

    files = report_of(lake, tmp_path)["files"]

    assert [(f["source"], f["period"], f["kind"], f["fuel_group"]) for f in files] == [
        ("anp", "2025-S1", "semester", "automotive"),
        ("anp", "2026-10", "monthly", "diesel_cng"),
        ("anp", "2026-10", "monthly", "gasoline_ethanol"),
        ("anp", "2026-10", "monthly", "lpg"),
        ("anp", "latest", "last_four_weeks", "gasoline_ethanol"),
        ("bcb_sgs", "2026", "series_1", None),
        ("bcb_sgs", "2026", "series_433", None),
    ]


def test_file_entry_says_what_was_collected_and_what_staging_made_of_it(tmp_path):
    lake = Lake()
    lake.collection[MONTHLY]["revision"] = 2
    lake.staging[MONTHLY] = staged(lake.collection[MONTHLY], sale_price_is_a_number=7)
    lake.staging[DOLLAR] = not_staged(lake.collection[DOLLAR], "schema drift")

    monthly, _, dollar, _ = report_of(lake, tmp_path)["files"]

    assert monthly == {
        "source": "anp",
        "kind": "monthly",
        "fuel_group": "gasoline_ethanol",
        "period": "2026-10",
        "rows_read": 500,
        "rows_staged": 493,
        "rows_quarantined": 7,
        "collected_at": "2026-11-26T01:00:00+00:00",
        "sha256": lake.collection[MONTHLY]["sha256"][:12],
        "revision": 2,
        "status": "ok",
    }
    assert (dollar["kind"], dollar["status"], dollar["rows_read"]) == ("series_1", "failed", 0)


def test_run_with_every_check_passing_is_publishable(tmp_path):
    report = report_of(Lake(), tmp_path)

    assert report["status"] == "pass"
    assert [(check["id"], check["status"]) for check in report["checks"]] == [
        (check_id, "pass") for check_id in CHECK_IDS
    ]
    assert publishable(report)


def test_run_with_a_warning_is_published_with_the_warning(tmp_path):
    lake = Lake()
    lake.collection[MONTHLY]["details"]["csv_parsing"] = "lenient"

    report = report_of(lake, tmp_path)

    assert report["status"] == "warn"
    assert publishable(report)


def test_run_with_a_failed_check_is_not_publishable_whatever_else_warned(tmp_path):
    lake = Lake()
    lake.collection[MONTHLY]["details"]["csv_parsing"] = "lenient"
    del lake.tables["ipca_monthly"][1]

    report = report_of(lake, tmp_path)

    assert report["status"] == "fail"
    assert not publishable(report)
    assert not publishable(saved(tmp_path))  # also when read back by a later step


@pytest.mark.parametrize("status", ["a status nobody defined", None])
def test_report_whose_status_is_neither_pass_nor_warn_is_not_publishable(status):
    assert not publishable({"status": status, "checks": []})


def test_report_that_says_pass_over_a_failed_check_is_not_publishable(tmp_path):
    report = report_of(Lake(), tmp_path)
    report["checks"][0]["status"] = "fail"  # edited by hand, or written by a bug

    assert report["status"] == "pass"
    assert not publishable(report)


def test_report_of_a_data_root_with_nothing_in_it_fails_without_raising(tmp_path):
    report = build_report(tmp_path, started_at=STARTED_AT, since=SINCE, now=at(RUN_DAY))

    assert report["status"] == "fail"
    assert set(report["freshness"].values()) == {None}
    assert set(report["funnel"].values()) == {0}
    assert report["files"] == []
    assert len(report["rules"]) == len(anp.RULES) + len(bcb_sgs.RULES)
    assert all(rule["by_year"] == {} for rule in report["rules"])
    assert saved(tmp_path) == report


def test_every_run_adds_its_line_to_the_history(tmp_path):
    lake = Lake()
    report_of(lake, tmp_path, STARTED_AT)
    lake.staging[MONTHLY] = staged(lake.collection[MONTHLY], sale_price_is_a_number=7)

    report = report_of(lake, tmp_path, STARTED_AT + timedelta(days=3))

    first, second = history(tmp_path)
    assert first == {
        "run_id": "20261126T092507Z",
        "finished_at": "2026-11-26T09:30:00Z",
        "status": "pass",
        "rows_raw": 1000,
        "rows_staged": 1000,
        "rows_quarantined": 0,
        "rows_curated": 249,
        "latest_week": "2026-11-15",
        "checks_failed": 0,
        "checks_warned": 0,
    }
    assert (second["run_id"], second["rows_quarantined"]) == ("20261129T092507Z", 7)
    assert second["checks_warned"] == 1  # file_quarantine_rate: 7 of 500 rows
    assert report["history"] == [first, second]


def test_running_the_same_run_again_replaces_its_line(tmp_path):
    lake = Lake()
    report_of(lake, tmp_path, STARTED_AT - timedelta(days=3))
    report_of(lake, tmp_path, STARTED_AT)
    del lake.tables["ipca_monthly"][1]

    report_of(lake, tmp_path, STARTED_AT)

    assert [(line["run_id"], line["status"]) for line in history(tmp_path)] == [
        ("20261123T092507Z", "pass"),
        ("20261126T092507Z", "fail"),
    ]


def test_history_published_by_earlier_runs_is_merged_before_this_run(tmp_path):
    published = tmp_path / "published.jsonl"
    older = {"run_id": "20261101T101700Z", "finished_at": "2026-11-01T10:30:00Z"}
    stale = {"run_id": "20261123T092507Z", "finished_at": "2026-11-23T09:30:00Z", "status": "x"}
    published.write_text(
        "".join(json.dumps(line) + "\n" for line in (stale, older)) + "not a run\n",
        encoding="utf-8",
    )
    lake = Lake()
    report_of(lake, tmp_path / "data", STARTED_AT - timedelta(days=3))  # the cache has this one

    report_of(lake, tmp_path / "data", history_from=published)

    lines = history(tmp_path / "data")
    assert [line["run_id"] for line in lines] == [
        "20261101T101700Z",
        "20261123T092507Z",
        "20261126T092507Z",
    ]
    assert lines[1]["status"] == "pass"  # the line written here wins over the published one


def test_history_to_merge_that_is_missing_stops_the_report(tmp_path):
    with pytest.raises(FileNotFoundError, match="no history to merge"):
        report_of(Lake(), tmp_path / "data", history_from=tmp_path / "absent.jsonl")


def test_history_file_keeps_the_latest_520_runs_and_the_report_the_latest_60(tmp_path):
    earlier = [{"run_id": f"run-{number:03d}", "status": "pass"} for number in range(520)]
    path = quality_dir(tmp_path) / "history.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in earlier), encoding="utf-8")

    report = report_of(Lake(), tmp_path)

    kept = [line["run_id"] for line in history(tmp_path)]
    assert len(kept) == 520
    assert kept[0] == "run-001"  # the oldest run made room
    assert kept[-1] == "20261126T092507Z"
    assert [line["run_id"] for line in report["history"]] == kept[-60:]


def test_history_line_that_cannot_be_read_is_left_out_instead_of_stopping_the_report(
    tmp_path, caplog
):
    path = quality_dir(tmp_path) / "history.jsonl"
    path.parent.mkdir(parents=True)
    earlier = json.dumps({"run_id": "20261123T092507Z", "status": "pass"})
    path.write_text(f'{earlier}\n{{"run_id": "202611\n[1, 2]\n', encoding="utf-8")  # cut short

    report = report_of(Lake(), tmp_path)

    assert [line["run_id"] for line in history(tmp_path)] == [
        "20261123T092507Z",
        "20261126T092507Z",
    ]
    assert report["history"] == history(tmp_path)
    assert "lines 2, 3" in caplog.text


def test_report_that_cannot_be_written_leaves_the_previous_one_whole(tmp_path, monkeypatch):
    lake = Lake()
    report_of(lake, tmp_path)
    before = (quality_dir(tmp_path) / "report.json").read_bytes()
    lake.write(tmp_path)

    def disk_full(path: Path, *_args, **_kwargs) -> int:
        path.write_bytes(b'{"run": ')  # as far as it got
        raise OSError("no space left on device")

    monkeypatch.setattr(Path, "write_text", disk_full)
    with pytest.raises(OSError, match="no space left"):
        build_report(tmp_path, started_at=STARTED_AT, since=SINCE, now=at(RUN_DAY))

    assert (quality_dir(tmp_path) / "report.json").read_bytes() == before


def test_report_never_carries_a_value_of_a_row(tmp_path):
    lake = Lake()
    station = price(LATEST_WEEK, sale_price=6.5)  # a second price for a key: a failed check
    lake.tables["fuel_price"].append(station)
    # Free text kept in the state files can quote the source: a file that lost its
    # header has a station's row for column names.
    drift = "unexpected ['POSTO UM LTDA', ' 11.222.333/0001-81', 'RUA A', '11222333000181']"
    lake.staging[MONTHLY] = not_staged(lake.collection[MONTHLY], drift)
    lake.collection[FOUR_WEEKS]["last_error"] = "line 7: POSTO UM LTDA; 11.222.333/0001-81"

    report = report_of(lake, tmp_path)

    assert report["status"] == "fail"
    for text in (
        json.dumps(report, ensure_ascii=False),
        (quality_dir(tmp_path) / "report.json").read_text(encoding="utf-8"),
        (quality_dir(tmp_path) / "history.jsonl").read_text(encoding="utf-8"),
    ):
        assert not CNPJ.search(text)
        assert station["station_cnpj"] not in text
        assert "POSTO" not in text and "RUA A" not in text
