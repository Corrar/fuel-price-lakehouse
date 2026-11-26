"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fuel_price_lakehouse import http
from fuel_price_lakehouse.anp import collect as anp
from fuel_price_lakehouse.layout import DEFAULT_DATA_ROOT, manifest_path
from fuel_price_lakehouse.manifest import Manifest
from fuel_price_lakehouse.report import Report
from fuel_price_lakehouse.sgs import collect as sgs

DEFAULT_SINCE = "2022-01"
"""From 2022 on the automotive semester files are zipped (7 to 17 MB each); before
that they are plain CSV of up to 158 MB each. The default is a first run of about
290 MB; the full history since 2004 is 4.8 GB and is an explicit choice."""

SOURCE_TIMEZONE = ZoneInfo("America/Sao_Paulo")


def _month(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m").date()
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM, got {value!r}") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fuel-lakehouse", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    collect = commands.add_parser(
        "collect", help="fetch new or changed source files into the raw layer"
    )
    collect.add_argument("--source", choices=("all", "anp", "sgs"), default="all")
    collect.add_argument(
        "--since",
        type=_month,
        default=DEFAULT_SINCE,
        metavar="YYYY-MM",
        help=f"first month to collect (default: {DEFAULT_SINCE})",
    )
    collect.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    collect.add_argument(
        "--dry-run", action="store_true", help="show what would be fetched; download nothing"
    )
    collect.add_argument(
        "--force",
        action="store_true",
        help="fetch everything again; content already recorded is still not duplicated",
    )
    collect.add_argument(
        "--pause", type=float, default=1.0, help="seconds between requests (default: 1.0)"
    )
    return parser


def run_collect(args: argparse.Namespace) -> Report:
    root: Path = args.data_root
    manifest = Manifest.load(manifest_path(root))
    policy = http.RetryPolicy()
    report = Report()
    with http.build_client() as client:
        if args.source in ("all", "sgs"):
            report.extend(
                sgs.collect(
                    client,
                    manifest,
                    root,
                    since=args.since,
                    today=datetime.now(SOURCE_TIMEZONE).date(),
                    policy=policy,
                    force=args.force,
                    dry_run=args.dry_run,
                    pause=args.pause,
                )
            )
        if args.source in ("all", "anp"):
            report.extend(
                anp.collect(
                    client,
                    manifest,
                    root,
                    since=args.since,
                    policy=policy,
                    force=args.force,
                    dry_run=args.dry_run,
                    pause=args.pause,
                )
            )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    report = run_collect(args)
    print(report.summary())
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
