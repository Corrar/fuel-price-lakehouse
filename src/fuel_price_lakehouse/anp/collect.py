"""Incremental collection of ANP files into the raw layer."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from fuel_price_lakehouse import http
from fuel_price_lakehouse.anp.discovery import (
    LANDING_URL,
    Catalogue,
    Grain,
    RemoteFile,
    parse_landing_page,
    select,
)
from fuel_price_lakehouse.layout import Layer, Source, partition_dir, safe_filename
from fuel_price_lakehouse.manifest import Entry, Manifest
from fuel_price_lakehouse.report import Outcome, Report, Status

log = logging.getLogger(__name__)

SOURCE = Source.ANP.value


def discover(client: httpx.Client, *, policy: http.RetryPolicy) -> Catalogue:
    response = http.get(client, LANDING_URL, policy=policy)
    if response.status_code != 200:
        raise http.FetchError(f"HTTP {response.status_code} reading the ANP landing page")
    catalogue = parse_landing_page(response.text)
    if not catalogue.files:
        raise http.FetchError("no data files found on the ANP landing page; layout changed?")
    return catalogue


def collect(
    client: httpx.Client,
    manifest: Manifest,
    root: Path,
    *,
    since: date,
    policy: http.RetryPolicy,
    force: bool = False,
    dry_run: bool = False,
    pause: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    """Fetch every selected ANP file that is new or has changed at the source.

    One file failing does not stop the others. The manifest is saved after each
    file, so an interrupted run resumes where it stopped.
    """
    report = Report()
    try:
        catalogue = discover(client, policy=policy)
    except (http.FetchError, httpx.HTTPError) as error:
        # Without the catalogue nothing can be collected, but the failure is
        # reported like any other so the rest of the run still gets its summary.
        log.error("ANP landing page failed: %s", error)
        report.add(Outcome(SOURCE, "catalogue", "landing-page", Status.FAILED, detail=str(error)))
        return report
    report.warnings += [f"unclassified ANP link: {url}" for url in catalogue.unclassified]
    selected = select(catalogue.files, since)
    log.info("ANP: %d files published, %d selected", len(catalogue.files), len(selected))

    for remote in selected:
        sleep(pause)
        try:
            outcome = _collect_one(client, manifest, root, remote, policy, force, dry_run)
        except (http.FetchError, httpx.HTTPError, OSError) as error:
            log.error("ANP %s %s failed: %s", remote.dataset, remote.period, error)
            outcome = _outcome(remote, Status.FAILED, detail=str(error))
        report.add(outcome)
    return report


def _outcome(remote: RemoteFile, status: Status, size: int = 0, detail: str = "") -> Outcome:
    return Outcome(SOURCE, remote.dataset.value, remote.period, status, size, detail)


def _reject_non_data(path: Path, filename: str, content_type: str) -> None:
    """Refuse what is plainly not a data file, such as an error page served with status 200.

    This is not validation of the contents, which belongs to the next stage. It
    only keeps things that could never be data out of the raw layer.
    """
    with path.open("rb") as handle:
        head = handle.read(64)
    if "html" in content_type.lower() or head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        raise http.FetchError(f"expected a data file, got markup ({content_type or 'no type'})")
    if filename.lower().endswith(".zip") and not head.startswith(b"PK\x03\x04"):
        raise http.FetchError("file is named .zip but is not a ZIP archive")


def _collect_one(
    client: httpx.Client,
    manifest: Manifest,
    root: Path,
    remote: RemoteFile,
    policy: http.RetryPolicy,
    force: bool,
    dry_run: bool,
) -> Outcome:
    known = manifest.latest(remote.url)

    # Files for a closed period are large and rarely change: a size probe is
    # enough to skip them. The rolling file is replaced every week and may keep
    # a similar size, so it is always downloaded and compared by content.
    if known and not force and remote.grain is not Grain.LAST_4_WEEKS:
        size = http.remote_size(client, remote.url, policy=policy)
        if size == known.size_bytes:
            return _outcome(remote, Status.UNCHANGED, detail="same size as recorded")
        log.info("ANP %s %s: size %s -> %s", remote.dataset, remote.period, known.size_bytes, size)

    if dry_run:
        size = http.remote_size(client, remote.url, policy=policy) or 0
        return _outcome(remote, Status.PLANNED, size, detail=remote.filename)

    filename = safe_filename(remote.filename)
    incoming = root / "tmp" / f"anp-{remote.dataset.value}-{remote.period}-{filename}.part"
    try:
        result = http.download(client, remote.url, incoming, policy=policy)
        _reject_non_data(incoming, filename, result.content_type)
        if manifest.has(remote.url, result.sha256):
            return _outcome(remote, Status.UNCHANGED, detail="same content as recorded")

        target = (
            partition_dir(
                Layer.RAW,
                Source.ANP,
                remote.dataset.value,
                root,
                grain=remote.grain.value,
                period=remote.period,
                sha256=result.sha256[:12],
            )
            / filename
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        incoming.replace(target)
    finally:
        incoming.unlink(missing_ok=True)

    manifest.add(
        Entry(
            source=SOURCE,
            dataset=remote.dataset.value,
            period=remote.period,
            url=remote.url,
            filename=remote.filename,
            size_bytes=result.size_bytes,
            sha256=result.sha256,
            raw_path=target.relative_to(root).as_posix(),
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
            details={
                "grain": remote.grain.value,
                "replaces": known.sha256 if known else None,
            },
        )
    )
    manifest.save()
    return _outcome(remote, Status.DOWNLOADED, result.size_bytes, detail=remote.filename)
