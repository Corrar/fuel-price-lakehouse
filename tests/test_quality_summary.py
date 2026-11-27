"""The Markdown summary of a run, on reports built by ``build_report`` from a ``Lake``."""

from __future__ import annotations

from datetime import UTC, date, datetime

from fuel_price_lakehouse.quality.report import build_report
from fuel_price_lakehouse.quality.summary import render_summary

from .test_quality_checks import CHECK_IDS, MONTHLY, RUN_DAY, Lake, at, staged

STARTED_AT = datetime(2026, 11, 26, 9, 25, 7, tzinfo=UTC)


def summary_of(lake: Lake, root) -> str:
    report = build_report(
        lake.write(root), started_at=STARTED_AT, since=date(2020, 1, 1), now=at(RUN_DAY)
    )
    return render_summary(report)


def test_summary_of_a_run_that_passed_counts_the_checks_and_follows_the_rows(tmp_path):
    summary = summary_of(Lake(), tmp_path)

    assert summary.startswith(
        "## Data quality: PASS\n\nRun `20261126T092507Z`, history since 2020-01.\n\n"
        "Latest prices: 2026-11-17, 9 days before the run, in the survey week of 2026-11-15. "
        "Dollar rate up to 2026-11-25, IPCA up to 2026-10.\n\n"
    )
    assert "| Collected, in 4 files | 1,000 |" in summary
    assert "| Curated prices whose source rows disagreed | 1 |" in summary
    assert f"All {len(CHECK_IDS)} checks passed." in summary
    assert summary.endswith("None of the 27 staging rules caught a row.\n")


def test_summary_lists_the_checks_that_did_not_pass_and_only_those(tmp_path):
    lake = Lake()
    lake.collection[MONTHLY]["details"]["csv_parsing"] = "lenient"

    summary = summary_of(lake, tmp_path)

    assert summary.startswith("## Data quality: WARN")
    assert f"### Checks that did not pass (1 of {len(CHECK_IDS)})" in summary
    assert (
        "| `raw_files_read_strictly` | warn | 1 of 2 ANP files read by the lenient reader "
        "| 0 files | anp monthly gasoline_ethanol 2026-10 |"
    ) in summary
    assert "`collection_complete`" not in summary


def test_rule_that_caught_rows_never_shows_a_share_of_zero(tmp_path):
    lake = Lake()
    # 1 row in 2,000,000: below the six decimals the report keeps for a share.
    lake.collection[MONTHLY]["rows"] = 2_000_000 - 300
    lake.staging[MONTHLY] = staged(lake.collection[MONTHLY], not_an_exact_duplicate=1)
    lake.staging[MONTHLY]["rule_warnings"] = {"postal_code_is_valid": 2_000}

    summary = summary_of(lake, tmp_path)

    assert "### Rules that caught rows (2 of 27)" in summary
    assert "| `not_an_exact_duplicate` | anp | error | 1 | < 0.0001% |" in summary
    assert "| `postal_code_is_valid` | anp | warning | 2,000 | 0.1000% |" in summary


def test_summary_of_an_empty_data_root_says_there_are_no_prices(tmp_path):
    summary = render_summary(
        build_report(tmp_path, started_at=STARTED_AT, since=date(2020, 1, 1), now=at(RUN_DAY))
    )

    assert summary.startswith("## Data quality: FAIL")
    assert "No prices in the curated tables. Dollar rate up to no day, IPCA up to no month." in (
        summary
    )
    assert f"### Checks that did not pass ({len(CHECK_IDS)} of {len(CHECK_IDS)})" in summary
