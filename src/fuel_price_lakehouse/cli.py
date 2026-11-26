"""Command line entry point: one subcommand per pipeline stage.

fuel-lakehouse extract --since 2023-01
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from fuel_price_lakehouse.config import DEFAULT_SINCE, parse_month
from fuel_price_lakehouse.layout import DEFAULT_DATA_ROOT

logger = logging.getLogger("fuel_price_lakehouse")


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

    extract = commands.add_parser(
        "extract", help="collect what is new from the sources into the raw layer"
    )
    extract.add_argument(
        "--since",
        type=_month,
        default=DEFAULT_SINCE,
        metavar="YYYY-MM",
        help=f"first month of history to collect (default {DEFAULT_SINCE:%Y-%m})",
    )
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
        "--full-check",
        action="store_true",
        help="download everything again and compare hashes",
    )
    extract.add_argument(
        "--pause",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="wait between requests to the same source (default 1)",
    )
    extract.set_defaults(handler=_extract)
    return parser


def _extract(args: argparse.Namespace) -> int:
    from fuel_price_lakehouse.collect.http import default_client
    from fuel_price_lakehouse.collect.run import SOURCES, run

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
    summary = ", ".join(f"{count} {action}" for action, count in sorted(counts.items()))
    verb = "to download" if args.dry_run else "downloaded"
    logger.info("extract: %s; %.1f MB %s", summary or "nothing to do", megabytes, verb)
    return 1 if counts["failed"] else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return int(args.handler(args))
