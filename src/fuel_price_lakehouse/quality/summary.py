"""A run at a glance, in Markdown: for the job summary on GitHub Actions and the terminal.

The report holds everything; this shows what a person checking on a run wants
first: did it pass, how recent is the data, where did the rows go, and what
needs a look. Checks that passed and rules that caught nothing are counted,
not listed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fuel_price_lakehouse.quality.checks import Status


def render_summary(report: Mapping[str, Any]) -> str:
    """The Markdown summary of a report built by ``quality.report.build_report``."""
    run, funnel = report["run"], report["funnel"]
    version = f", code `{run['code_version']}`" if run["code_version"] else ""
    sections = [
        f"## Data quality: {report['status'].upper()}",
        f"Run `{run['id']}`, history since {run['since']}{version}.",
        _freshness(report["freshness"]),
        _table(
            ("Rows", "Count"),
            "lr",
            [
                (f"Collected, in {funnel['files']:,} files", f"{funnel['rows_raw']:,}"),
                ("Staged", f"{funnel['rows_staged']:,}"),
                ("Quarantined", f"{funnel['rows_quarantined']:,}"),
                ("Curated", f"{funnel['rows_curated']:,}"),
                # ANP repeats rows between its files, and a few inside one file, a
                # few of those with another price.
                ("Repeated station, product and day, removed", f"{funnel['duplicates_removed']:,}"),
                ("Curated prices whose source rows disagreed", f"{funnel['price_conflicts']:,}"),
                ("Aggregate cells suppressed", f"{funnel['cells_suppressed']:,}"),
            ],
        ),
        *_checks(report["checks"]),
        *_rules(report["rules"]),
    ]
    return "\n\n".join(sections) + "\n"


def _freshness(freshness: Mapping[str, Any]) -> str:
    if freshness["latest_collection_date"] is None:
        prices = "No prices in the curated tables."
    else:
        days = freshness["days_since_latest"]
        prices = (
            f"Latest prices: {freshness['latest_collection_date']}, "
            f"{days} {'day' if days == 1 else 'days'} before the run, "
            f"in the survey week of {freshness['latest_week']}."
        )
    return (
        f"{prices} Dollar rate up to {freshness['usd_latest'] or 'no day'}, "
        f"IPCA up to {freshness['ipca_latest_month'] or 'no month'}."
    )


def _checks(checks: Sequence[Mapping[str, Any]]) -> list[str]:
    broken = [check for check in checks if check["status"] != Status.PASS]
    if not broken:
        return [f"All {len(checks)} checks passed."]
    return [
        f"### Checks that did not pass ({len(broken)} of {len(checks)})",
        _table(
            ("Check", "Status", "Observed", "Expected", "Detail"),
            "lllll",
            [
                (
                    f"`{check['id']}`",
                    check["status"],
                    check["observed"],
                    check["expected"],
                    check["detail"],
                )
                for check in broken
            ],
        ),
    ]


def _rules(rules: Sequence[Mapping[str, Any]]) -> list[str]:
    caught = [rule for rule in rules if rule["rows"]]
    if not caught:
        return [f"None of the {len(rules)} staging rules caught a row."]
    return [
        f"### Rules that caught rows ({len(caught)} of {len(rules)})",
        _table(
            ("Rule", "Source", "Severity", "Rows", "Share of the source"),
            "lllrr",
            [
                (
                    f"`{rule['id']}`",
                    rule["source"],
                    rule["severity"],
                    f"{rule['rows']:,}",
                    _share(rule),
                )
                for rule in caught
            ],
        ),
    ]


def _share(rule: Mapping[str, Any]) -> str:
    """The share of the source a rule caught, never 0 % when it caught a row.

    The report keeps six decimals of a share, the four of a percentage shown
    here: one row in seven million would otherwise read as nothing caught.
    """
    if rule["rows"] and not rule["share"]:
        return "< 0.0001%"
    return f"{rule['share']:.4%}"


def _table(header: Sequence[str], alignment: str, rows: Sequence[Sequence[str]]) -> str:
    """A Markdown table; ``alignment`` has one ``l`` or ``r`` per column."""
    rule = ["---:" if side == "r" else "---" for side in alignment]
    return "\n".join("| " + " | ".join(cells) + " |" for cells in (header, rule, *rows))
