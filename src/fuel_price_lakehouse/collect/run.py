"""Collect what is new from both sources into the raw layer.

Safe to run as often as wanted: files already in the manifest and unchanged at
the source are not downloaded or rewritten.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

import httpx

from fuel_price_lakehouse.collect import anp, bcb_sgs
from fuel_price_lakehouse.collect.http import Fetcher, RetryPolicy
from fuel_price_lakehouse.collect.manifest import Manifest, Outcome
from fuel_price_lakehouse.config import Clock, utc_now
from fuel_price_lakehouse.layout import manifest_path

SOURCES = ("anp", "bcb_sgs")


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
    now: Clock = utc_now,
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
            fetcher, manifest, root, since, now=now, dry_run=dry_run, full_check=full_check
        )
    if not dry_run:
        manifest.save()
    return outcomes
