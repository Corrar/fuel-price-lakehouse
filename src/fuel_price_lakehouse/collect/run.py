"""Collect what is new from both sources into the raw layer.

Safe to run as often as wanted: files already in the manifest and unchanged at
the source are not downloaded or rewritten.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path

import httpx

from fuel_price_lakehouse.collect import anp, bcb_sgs, landing
from fuel_price_lakehouse.collect.http import Fetcher, RetryPolicy
from fuel_price_lakehouse.collect.manifest import FileRecord, Manifest, Outcome
from fuel_price_lakehouse.collect.raw import (
    CorruptFileError,
    Provenance,
    file_sha256,
    write_anp_csv,
    write_json_records,
)
from fuel_price_lakehouse.config import Clock, utc_now
from fuel_price_lakehouse.layout import Source, downloads_dir, manifest_path

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


def rebuild_raw(root: Path) -> list[Outcome]:
    """Recreate the raw layer from the landing zone, without touching the network.

    For when the way raw files are read changes (an encoding rule, the CSV
    reader): the bytes that were downloaded are converted again, and the
    manifest keeps saying when and from where they were collected.
    """
    manifest = Manifest.load(manifest_path(root))
    outcomes = []
    for record in manifest.files.values():
        if not record.ingested or record.raw_path is None:
            continue
        landed = root / record.landing_path if record.landing_path else None
        if landed is None or not landed.exists():
            detail = "no landed copy: collect it again with --full-check"
            outcomes.append(Outcome(record.key, "skipped", detail=detail))
            continue
        try:
            rows = _convert(record, landed, root)
        except CorruptFileError as error:
            outcomes.append(Outcome(record.key, "failed", detail=str(error)))
            continue
        record.rows = rows
        record.raw_file_sha256 = file_sha256(root / record.raw_path)
        outcomes.append(Outcome(record.key, "rebuilt", rows=rows, detail=f"{rows:,} rows"))
    manifest.save()
    return outcomes


def _convert(record: FileRecord, landed: Path, root: Path) -> int:
    assert record.raw_path and record.sha256 and record.collected_at  # ingested records
    destination = root / record.raw_path
    provenance = Provenance(record.url, record.sha256, datetime.fromisoformat(record.collected_at))
    original = downloads_dir(root) / record.sha256[:12]
    try:
        landing.restore(landed, original)
        if record.source == Source.ANP:
            copy = write_anp_csv(original, destination, provenance)
            record.details["encoding"] = copy.encoding
            record.details["csv_parsing"] = "lenient" if copy.lenient else "strict"
            return copy.rows
        return write_json_records(original.read_bytes(), destination, provenance)
    finally:
        original.unlink(missing_ok=True)
