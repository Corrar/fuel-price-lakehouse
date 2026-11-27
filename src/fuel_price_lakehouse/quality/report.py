"""The quality report of a run: what came in, what was caught, and whether it can be published.

One JSON document, ``quality/report.json``, written at the end of every run.
The dashboard shows it as it is, and the pipeline asks it one question: may
this run replace the dashboard (``publishable``)? Only a run that passed or
warned may. Its sections:

- ``run``: which run this is;
- ``status``: the worst outcome among the checks;
- ``freshness``: how recent the data is;
- ``funnel``: the rows of both sources through the layers;
- ``rules``: every staging rule with the rows it caught, zeros included, so a
  rule that caught nothing is seen to have run;
- ``files``: every collected file and what staging made of it;
- ``checks``: the dataset-level checks of ``quality/checks.py``;
- ``history``: the latest runs, one line each.

The report is public and the rows are not
(``docs/decisions/0002-what-is-published.md``), so it counts rows and never
shows one.

``quality/history.jsonl`` is the one piece of state that cannot be computed
again, because it says what past runs saw. Every run adds its line; running the
same run again replaces the line instead of adding a second one. A damaged line
is left out with a warning rather than stopping the run.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fuel_price_lakehouse.collect.anp import LAST_FOUR_WEEKS
from fuel_price_lakehouse.collect.manifest import FileRecord
from fuel_price_lakehouse.config import Clock, utc_now
from fuel_price_lakehouse.curate.build import REPORT_NAME as BUILD_REPORT_NAME
from fuel_price_lakehouse.layout import Source, curated_dir, quality_dir
from fuel_price_lakehouse.quality.checks import (
    CheckResult,
    Evidence,
    Status,
    evaluate,
    file_identity,
    gather,
    staging_problem,
)
from fuel_price_lakehouse.stage import anp, bcb_sgs
from fuel_price_lakehouse.stage.rules import Rule

logger = logging.getLogger(__name__)

REPORT_NAME = "report.json"
HISTORY_NAME = "history.jsonl"

HISTORY_KEPT = 520
"""Lines kept in the history file: five years of runs at two a week."""

HISTORY_IN_REPORT = 60
"""Latest runs repeated in the report, which is what the dashboard loads."""

PUBLISHABLE_STATUSES = frozenset({Status.PASS, Status.WARN})

RULES: tuple[tuple[str, tuple[Rule, ...]], ...] = (
    (Source.ANP.value, anp.RULES),
    (Source.BCB_SGS.value, bcb_sgs.RULES),
)
"""The staging rules of each source, in the order the report lists them."""

SOURCE_ROW_TABLES = ("fuel_price", "usd_brl_daily", "ipca_monthly")
"""The curated tables whose rows are rows of the sources, and so belong in the
funnel. The others are derived from them."""


def build_report(
    root: Path,
    *,
    started_at: datetime,
    since: date,
    code_version: str | None = None,
    history_from: Path | None = None,
    now: Clock = utc_now,
) -> dict[str, Any]:
    """Assemble the report of the run that started at ``started_at``, and save it.

    ``since`` is the first month of history the run covers. The report is
    written to ``quality/report.json`` and the run gets its line in
    ``quality/history.jsonl``. ``history_from`` is a history published by an
    earlier run: its lines are merged in by run id first, so that a run that
    starts from an empty or an old cache does not shrink the history.
    """
    finished_at = now()
    with gather(root, finished_at.date()) as evidence:
        checks = evaluate(evidence)
        report: dict[str, Any] = {
            "run": {
                "id": started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"),
                "started_at": _timestamp(started_at),
                "finished_at": _timestamp(finished_at),
                "code_version": code_version,
                "since": f"{since:%Y-%m}",
            },
            "status": _status(checks).value,
            "freshness": _freshness(evidence),
            "funnel": _funnel(evidence, _build_facts(root)),
            "rules": _rules(evidence),
            "files": _files(evidence),
            "checks": [
                {**asdict(check), "severity": check.severity.value, "status": check.status.value}
                for check in checks
            ],
        }
    directory = quality_dir(root)
    history = _add_to_history(directory / HISTORY_NAME, _history_line(report), history_from)
    report["history"] = history[-HISTORY_IN_REPORT:]
    _write(directory / REPORT_NAME, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    return report


def publishable(report: Mapping[str, Any]) -> bool:
    """The circuit breaker: may the run of ``report`` replace the dashboard?

    Only when the report says ``pass`` or ``warn`` and none of its checks
    failed. A status nobody defined, or one the checks contradict (a report
    edited by hand, or written by a bug), reads as a failure: a breaker that
    lets an unknown state through is an open breaker.
    """
    failed = any(check.get("status") == Status.FAIL for check in report.get("checks", []))
    return report.get("status") in PUBLISHABLE_STATUSES and not failed


def _timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _status(checks: list[CheckResult]) -> Status:
    """The worst outcome: ``fail`` before ``warn`` before ``pass``."""
    outcomes = {check.status for check in checks}
    if Status.FAIL in outcomes:
        return Status.FAIL
    if Status.WARN in outcomes:
        return Status.WARN
    return Status.PASS


def _ingested(evidence: Evidence) -> list[FileRecord]:
    return [record for record in (evidence.collection or {}).values() if record.ingested]


def _fact(evidence: Evidence, table: str, expression: str) -> Any:
    """One aggregate over a curated table; None when the table is not built."""
    if table not in evidence.tables:
        return None
    ((value,),) = evidence.connection.execute(f"SELECT {expression} FROM {table}").fetchall()
    return value


def _freshness(evidence: Evidence) -> dict[str, str | int | None]:
    latest_day: date | None = _fact(evidence, "fuel_price", "max(collected_on)")
    latest_week: date | None = _fact(evidence, "fuel_price", "max(survey_week)")
    usd_latest: date | None = _fact(evidence, "usd_brl_daily", "max(observed_on)")
    ipca_latest: date | None = _fact(evidence, "ipca_monthly", "max(month)")
    return {
        "latest_collection_date": latest_day.isoformat() if latest_day else None,
        "latest_week": latest_week.isoformat() if latest_week else None,
        "days_since_latest": (evidence.today - latest_day).days if latest_day else None,
        "usd_latest": usd_latest.isoformat() if usd_latest else None,
        "ipca_latest_month": f"{ipca_latest:%Y-%m}" if ipca_latest else None,
    }


def _build_facts(root: Path) -> Mapping[str, Any]:
    """What the curated build said about itself; nothing when there was no build."""
    path = curated_dir(root) / BUILD_REPORT_NAME
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _funnel(evidence: Evidence, build: Mapping[str, Any]) -> dict[str, int]:
    ingested = _ingested(evidence)
    staged = (evidence.staging or {}).values()
    return {
        "files": len(ingested),
        "rows_raw": sum(record.rows or 0 for record in ingested),
        "rows_staged": sum(entry.rows_staged for entry in staged),
        "rows_quarantined": sum(entry.rows_quarantined for entry in staged),
        "rows_curated": sum(_fact(evidence, table, "count(*)") or 0 for table in SOURCE_ROW_TABLES),
        "duplicates_removed": build.get("duplicates_removed", 0),
        "price_conflicts": build.get("price_conflicts", 0),
        "cells_suppressed": build.get("cells_suppressed", 0),
    }


def _year(record: FileRecord) -> str:
    """The year a file counts under in ``by_year``.

    A file for a period counts under the year of the period. The rolling
    last-four-weeks files have none: they count under the year ANP last updated
    them, as its page says, or else the year they were downloaded.
    """
    kind, _, period = file_identity(record.key, record.source, record.details)
    if kind != LAST_FOUR_WEEKS:
        return period[:4]
    updated_on = record.details.get("published_hint", "")  # dd/mm/yyyy
    return updated_on[-4:] if updated_on else (record.collected_at or "")[:4]


def _rules(evidence: Evidence) -> list[dict[str, Any]]:
    staging = evidence.staging or {}
    entries = []
    for source, rules in RULES:
        files = [record for record in _ingested(evidence) if record.source == source]
        raw_rows = sum(record.rows or 0 for record in files)
        # Every year the source has a file for, so that a year without a
        # single caught row still shows its zero.
        caught: dict[str, Counter[str]] = {
            year: Counter() for year in sorted({_year(record) for record in files})
        }
        for record in files:
            entry = staging.get(record.key)
            if entry is not None:
                caught[_year(record)].update(entry.rule_failures)
                caught[_year(record)].update(entry.rule_warnings)
        for rule in rules:
            by_year = {year: counts[rule.id] for year, counts in caught.items()}
            rows = sum(by_year.values())
            entries.append(
                {
                    "id": rule.id,
                    "source": source,
                    "severity": rule.severity.value,
                    "description": rule.description,
                    "description_pt": rule.description_pt,
                    "rows": rows,
                    # Four significant digits: one row in seven million is not zero.
                    "share": float(f"{rows / raw_rows:.4g}") if raw_rows else 0.0,
                    "by_year": by_year,
                }
            )
    return entries


def _files(evidence: Evidence) -> list[dict[str, Any]]:
    staging = evidence.staging or {}
    entries = []
    for record in _ingested(evidence):
        kind, fuel_group, period = file_identity(record.key, record.source, record.details)
        entry = staging.get(record.key)
        entries.append(
            {
                "source": record.source,
                "kind": kind,
                "fuel_group": fuel_group,
                "period": period,
                "rows_read": entry.rows_read if entry else 0,
                "rows_staged": entry.rows_staged if entry else 0,
                "rows_quarantined": entry.rows_quarantined if entry else 0,
                "collected_at": record.collected_at,
                "sha256": (record.sha256 or "")[:12],
                "revision": record.revision,
                "status": "failed" if staging_problem(record, entry) else "ok",
            }
        )
    return sorted(
        entries,
        key=lambda file: (file["source"], file["period"], file["kind"], file["fuel_group"] or ""),
    )


def _history_line(report: Mapping[str, Any]) -> dict[str, Any]:
    outcomes = [check["status"] for check in report["checks"]]
    funnel = report["funnel"]
    return {
        "run_id": report["run"]["id"],
        "finished_at": report["run"]["finished_at"],
        "status": report["status"],
        "rows_raw": funnel["rows_raw"],
        "rows_staged": funnel["rows_staged"],
        "rows_quarantined": funnel["rows_quarantined"],
        "rows_curated": funnel["rows_curated"],
        "latest_week": report["freshness"]["latest_week"],
        "checks_failed": outcomes.count(Status.FAIL),
        "checks_warned": outcomes.count(Status.WARN),
    }


def _add_to_history(
    path: Path, line: dict[str, Any], history_from: Path | None = None
) -> list[dict[str, Any]]:
    """Put the line of this run at the end of the history file and return the history."""
    if history_from is not None and not history_from.is_file():
        raise FileNotFoundError(f"{history_from}: no history to merge")
    runs = {run["run_id"]: run for run in _read_history(history_from) if history_from}
    runs.update((run["run_id"], run) for run in _read_history(path))  # local lines win
    runs.pop(line["run_id"], None)
    earlier = sorted(runs.values(), key=lambda run: (str(run.get("finished_at")), run["run_id"]))
    history = [*earlier, line][-HISTORY_KEPT:]
    _write(path, "".join(json.dumps(run, ensure_ascii=False) + "\n" for run in history))
    return history


def _read_history(path: Path | None) -> list[dict[str, Any]]:
    """The runs of the history file, leaving out any line that is not one.

    The file comes back from a release asset when the cache is empty, and can be
    edited by hand. A damaged line must not stop every later run from getting a
    report: it is left out, with a warning, and not written back.
    """
    if path is None or not path.exists():
        return []
    runs, damaged = [], []
    for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        run = _parsed(text)
        if isinstance(run, dict) and "run_id" in run:
            runs.append(run)
        else:
            damaged.append(str(number))
    if damaged:
        lines = "line" if len(damaged) == 1 else "lines"
        logger.warning("quality: %s: %s %s left out, not a run", path, lines, ", ".join(damaged))
    return runs


def _parsed(text: str) -> Any:
    """``text`` read as JSON; None when it is not JSON."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _write(path: Path, text: str) -> None:
    """Replace ``path`` in one step, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
