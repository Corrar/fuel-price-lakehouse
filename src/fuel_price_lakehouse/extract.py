"""Stage 1 entry point: collect what is new from both sources into the raw layer.

    python -m fuel_price_lakehouse.extract --since 2026-01

Safe to run as often as wanted: files already in the manifest and unchanged at
the source are not downloaded or rewritten.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from fuel_price_lakehouse import anp, bcb_sgs
from fuel_price_lakehouse.http import Fetcher, RetryPolicy, default_client
from fuel_price_lakehouse.layout import DEFAULT_DATA_ROOT, manifest_path
from fuel_price_lakehouse.manifest import Manifest, Outcome

DEFAULT_SINCE = "2023-01"
"""First month of history collected when ``--since`` is not given."""

SOURCES = ("anp", "bcb_sgs")


def parse_since(text: str) -> date:
    try:
        year, month = text.split("-")
        return date(int(year), int(month), 1)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM, got {text!r}") from None


def run(
    root: Path,
    since: date,
    *,
    client: httpx.Client,
    sources: Sequence[str] = SOURCES,
    dry_run: bool = False,
    full_check: bool = False,
    pause: float = 1.0,
    retry: RetryPolicy | None = None,
    sleep: Callable[[float], None] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    log: Callable[[str], None] = print,
) -> list[Outcome]:
    """Collect from the chosen sources and return what happened to each file."""
    manifest = Manifest.load(manifest_path(root))
    options = {"sleep": sleep} if sleep else {}
    outcomes: list[Outcome] = []
    for name, collector in (("anp", anp), ("bcb_sgs", bcb_sgs)):
        if name not in sources:
            continue
        # One fetcher per source: the pause is between requests to the same host.
        fetcher = Fetcher(client, retry=retry, pause=pause, **options)
        outcomes += collector.collect(
            fetcher, manifest, root, since, now=now, dry_run=dry_run, full_check=full_check, log=log
        )
    if not dry_run:
        manifest.save()
    return outcomes


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="extract", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--since",
        type=parse_since,
        default=DEFAULT_SINCE,
        metavar="YYYY-MM",
        help=f"first month of history to collect (default {DEFAULT_SINCE})",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_DATA_ROOT, help="data directory")
    parser.add_argument(
        "--source",
        choices=SOURCES,
        action="append",
        help="collect only this source (repeatable; default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be downloaded, with sizes; write nothing",
    )
    parser.add_argument(
        "--full-check", action="store_true", help="download everything again and compare hashes"
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="wait between requests to the same source (default 1)",
    )
    args = parser.parse_args(argv)

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
    print(f"extract: {summary or 'nothing to do'}; {megabytes:,.1f} MB {verb}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
