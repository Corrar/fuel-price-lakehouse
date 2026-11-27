"""Command line entry point: one subcommand per pipeline step, and one for all of them.

    fuel-lakehouse run --since 2020-01 --site build/site

Exit codes: 0 when the step (or the run) succeeded; 1 when some files failed
and will be tried again on the next run; 2 when the quality gate failed or the
site builder refused, in which case nothing was published; 3 when it crashed.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fuel_price_lakehouse.config import DEFAULT_SINCE, parse_month, utc_now
from fuel_price_lakehouse.layout import DEFAULT_DATA_ROOT

logger = logging.getLogger("fuel_price_lakehouse")

DEFAULT_SITE = Path("build/site")

OK = 0
SOME_FILES_FAILED = 1
QUALITY_GATE_FAILED = 2
CRASHED = 3
"""Not Python's 1 for an uncaught exception: a scheduler must never mistake a crash
for a run that only lost a few files."""


def _month(text: str) -> date:
    try:
        return parse_month(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fuel-lakehouse", description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_DATA_ROOT, help="data directory")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser(
        "run", help="the whole pipeline: extract, stage, curate, report and, if it passes, site"
    )
    _add_since(run)
    _add_full_check(run)
    _add_site(run)
    _add_report_options(run)
    run.add_argument(
        "--offline",
        action="store_true",
        help="skip the collection and work from the data already collected",
    )
    # The steps read their own options; a whole run uses their defaults.
    run.set_defaults(
        handler=_run, source=None, dry_run=False, rebuild_raw=False, pause=1.0, full_refresh=False
    )

    extract = commands.add_parser(
        "extract", help="collect what is new from the sources into the raw layer"
    )
    _add_since(extract)
    _add_full_check(extract)
    extract.add_argument(
        "--source",
        choices=("anp", "bcb_sgs"),
        action="append",
        help="collect only this source (repeatable; default: all)",
    )
    extract.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be downloaded, with sizes; write nothing",
    )
    extract.add_argument(
        "--rebuild-raw",
        action="store_true",
        help="recreate the raw layer from the landed files, without the network",
    )
    extract.add_argument(
        "--pause",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="wait between requests to the same source (default 1)",
    )
    extract.set_defaults(handler=_extract)

    stage = commands.add_parser(
        "stage", help="validate raw files into the staged layer; rejected rows go to quarantine"
    )
    stage.add_argument(
        "--full-refresh",
        action="store_true",
        help="stage every file again, also the ones that did not change",
    )
    stage.set_defaults(handler=_stage)

    curate = commands.add_parser("curate", help="rebuild the curated tables from the staged layer")
    curate.set_defaults(handler=_curate)

    report = commands.add_parser(
        "report", help="check the data, write the quality report and apply the quality gate"
    )
    _add_since(report)
    _add_report_options(report)
    report.set_defaults(handler=_report)

    site = commands.add_parser("site", help="build the static dashboard from a passing run")
    _add_site(site)
    site.set_defaults(handler=_site)

    release = commands.add_parser(
        "release", help="assemble the release assets: public tables, manifest, history"
    )
    release.add_argument(
        "--out", type=Path, required=True, metavar="DIR", help="folder to write them into"
    )
    release.set_defaults(handler=_release)

    docs = commands.add_parser("docs", help="regenerate the documents generated from the code")
    docs.add_argument(
        "--repository", type=Path, default=Path(), help="repository root (default: here)"
    )
    docs.set_defaults(handler=_docs)
    return parser


def _add_since(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--since",
        type=_month,
        default=DEFAULT_SINCE,
        metavar="YYYY-MM",
        help=f"first month of history (default {DEFAULT_SINCE:%Y-%m})",
    )


def _add_full_check(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--full-check",
        action="store_true",
        help="download everything again and compare hashes",
    )


def _add_site(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--site",
        type=Path,
        default=DEFAULT_SITE,
        metavar="DIR",
        help=f"where to build the dashboard (default {DEFAULT_SITE})",
    )


def _add_report_options(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--summary",
        type=Path,
        metavar="FILE",
        help="append a Markdown summary of the run to FILE (for example $GITHUB_STEP_SUMMARY)",
    )
    command.add_argument(
        "--code-version",
        metavar="SHA",
        help="the commit the run was made with, recorded in the report",
    )
    command.add_argument(
        "--history-from",
        type=Path,
        metavar="FILE",
        help="merge a history published by earlier runs before adding this run",
    )


def _run(args: argparse.Namespace) -> int:
    started_at = utc_now()
    files_failed = False
    if not args.offline:
        files_failed |= _extract(args) != OK
    files_failed |= _stage(args) != OK
    _curate(args)
    report = _write_report(args, started_at)
    if not _gate_open(report) or _site(args) != OK:
        return QUALITY_GATE_FAILED
    return SOME_FILES_FAILED if files_failed else OK


def _extract(args: argparse.Namespace) -> int:
    from fuel_price_lakehouse.collect.http import default_client
    from fuel_price_lakehouse.collect.run import SOURCES, rebuild_raw, run

    if args.rebuild_raw:
        rebuilt = Counter(outcome.action for outcome in rebuild_raw(args.root))
        logger.info("extract: %s", _counts(rebuilt))
        return SOME_FILES_FAILED if rebuilt["failed"] else OK

    with default_client() as client:
        outcomes = run(
            args.root,
            args.since,
            client=client,
            sources=args.source or SOURCES,
            dry_run=args.dry_run,
            full_check=args.full_check,
            pause=args.pause,
        )
    counts = Counter(outcome.action for outcome in outcomes)
    megabytes = sum(outcome.size_bytes for outcome in outcomes) / 1_000_000
    verb = "to download" if args.dry_run else "downloaded"
    logger.info("extract: %s; %.1f MB %s", _counts(counts), megabytes, verb)
    return SOME_FILES_FAILED if counts["failed"] else OK


def _stage(args: argparse.Namespace) -> int:
    from fuel_price_lakehouse.stage.run import run

    outcomes = run(args.root, full_refresh=args.full_refresh)
    counts = Counter(outcome.action for outcome in outcomes)
    logger.info(
        "stage: %s; %s rows in the staged layer, %s in quarantine",
        _counts(counts),
        f"{sum(outcome.rows_staged for outcome in outcomes):,}",
        f"{sum(outcome.rows_quarantined for outcome in outcomes):,}",
    )
    return SOME_FILES_FAILED if counts["failed"] else OK


def _curate(args: argparse.Namespace) -> int:
    from fuel_price_lakehouse.curate.build import build

    report = build(args.root)
    logger.info(
        "curate: %s fact rows, %s repeated rows removed, %s aggregate cells suppressed",
        f"{report.rows_curated:,}",
        f"{report.duplicates_removed:,}",
        f"{report.cells_suppressed:,}",
    )
    return OK


def _report(args: argparse.Namespace) -> int:
    report = _write_report(args, utc_now())
    return OK if _gate_open(report) else QUALITY_GATE_FAILED


def _site(args: argparse.Namespace) -> int:
    from fuel_price_lakehouse.site.build import build
    from fuel_price_lakehouse.site.contract import SiteBuildError

    try:
        built = build(args.root, args.site)
    except SiteBuildError as error:
        logger.error("site: not built: %s", error)
        return QUALITY_GATE_FAILED
    logger.info("site: run %s, %s weeks, built in %s", built.run_id, built.weeks, built.out)
    return OK


def _release(args: argparse.Namespace) -> int:
    from fuel_price_lakehouse.release import write_release

    written = write_release(args.root, args.out)
    logger.info("release: %s files in %s", len(written), args.out)
    return OK


def _docs(args: argparse.Namespace) -> int:
    from fuel_price_lakehouse.docgen import write_all

    for path in write_all(args.repository):
        logger.info("docs: wrote %s", path)
    return OK


def _write_report(args: argparse.Namespace, started_at: datetime) -> dict[str, Any]:
    from fuel_price_lakehouse.quality.report import build_report
    from fuel_price_lakehouse.quality.summary import render_summary

    report = build_report(
        args.root,
        started_at=started_at,
        since=args.since,
        code_version=args.code_version,
        history_from=args.history_from,
    )
    failing = [check["id"] for check in report["checks"] if check["status"] != "pass"]
    logger.info("report: %s%s", report["status"], f" ({', '.join(failing)})" if failing else "")
    if args.summary is not None:
        with args.summary.open("a", encoding="utf-8") as summary:
            summary.write(render_summary(report) + "\n")
    return report


def _gate_open(report: dict[str, Any]) -> bool:
    from fuel_price_lakehouse.quality.report import publishable

    if publishable(report):
        return True
    logger.error("report: the quality gate failed; nothing is published")
    return False


def _counts(counts: Counter[str]) -> str:
    return ", ".join(f"{count} {action}" for action, count in sorted(counts.items())) or "nothing"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        return int(args.handler(args))
    except Exception:
        logger.exception("%s: crashed", args.command)
        return CRASHED
