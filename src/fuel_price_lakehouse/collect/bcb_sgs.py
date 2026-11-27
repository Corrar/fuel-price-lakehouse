"""Banco Central collector: SGS time series, fetched as JSON in date windows.

Limits measured on the live API (see ``docs/sources.md``):

- daily series need ``dataInicial`` and accept at most 10 years per request;
- a window with no observations answers 404, not an empty list;
- JSON responses carry an ``ETag`` and honour ``If-None-Match``.

Each series is requested one calendar year at a time. Fixed windows give every
request a stable identity in the manifest: a year that is over and was fetched
a while after its end is never requested again, and the current year is revalidated
with its ETag, which costs a 304 when nothing was published.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import httpx

from fuel_price_lakehouse.collect import landing
from fuel_price_lakehouse.collect.http import Fetcher, SourceError, TransientError
from fuel_price_lakehouse.collect.manifest import FileRecord, Manifest, Outcome
from fuel_price_lakehouse.collect.raw import (
    CorruptFileError,
    Provenance,
    file_sha256,
    write_json_records,
)
from fuel_price_lakehouse.config import Clock
from fuel_price_lakehouse.layout import Layer, Source, partition_dir

API = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"
DATASET = "series"

logger = logging.getLogger(__name__)

MAX_WINDOW_YEARS = 10
"""Longest window the API accepts for a daily series (406 beyond it)."""
WINDOW_YEARS = 1

SETTLE_DAYS = 45
"""A year is final only this long after it ends: December's IPCA comes out in
January, so a window fetched right at the turn of the year is still incomplete."""


@dataclass(frozen=True)
class Series:
    code: int
    name: str


SERIES = (
    Series(1, "Taxa de câmbio - Livre - Dólar americano (venda) - diário"),
    Series(433, "Índice nacional de preços ao consumidor-amplo (IPCA)"),
)


@dataclass(frozen=True)
class Window:
    code: int
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end.year - self.start.year >= MAX_WINDOW_YEARS:
            raise ValueError(f"window {self.start}..{self.end} exceeds the API limit")

    @property
    def key(self) -> str:
        return f"{Source.BCB_SGS}/{self.code}/{self.start.year}"

    @property
    def url(self) -> str:
        # The API only accepts dd/MM/yyyy; ISO dates are a 400.
        return (
            f"{API.format(code=self.code)}?formato=json"
            f"&dataInicial={self.start:%d/%m/%Y}&dataFinal={self.end:%d/%m/%Y}"
        )


def windows(code: int, since: date, today: date) -> list[Window]:
    """Calendar-year windows from the year of ``since`` to the current year."""
    return [
        Window(code, date(year, 1, 1), date(year + WINDOW_YEARS - 1, 12, 31))
        for year in range(since.year, today.year + 1, WINDOW_YEARS)
    ]


def raw_path(window: Window, root: Path) -> Path:
    directory = partition_dir(
        Layer.RAW, Source.BCB_SGS, DATASET, root, series=window.code, year=window.start.year
    )
    return directory / "data.parquet"


def collect(
    fetcher: Fetcher,
    manifest: Manifest,
    root: Path,
    since: date,
    *,
    now: Clock,
    series: tuple[Series, ...] = SERIES,
    dry_run: bool = False,
    full_check: bool = False,
) -> list[Outcome]:
    """Bring the raw layer up to date with the SGS API."""
    outcomes = []
    for one in series:
        results = []
        for window in windows(one.code, since, now().date()):
            record = manifest.get(window.key)
            if not full_check and _settled(record, window, root):
                outcome = Outcome(window.key, "unchanged")
            elif dry_run:
                outcome = Outcome(window.key, "planned")
            else:
                try:
                    outcome = _collect_window(fetcher, manifest, root, window, now, full_check)
                    _mark_if_settled(manifest.get(window.key), window, now().date())
                except (TransientError, SourceError, CorruptFileError) as error:
                    at = now().isoformat()
                    manifest.record_failure(
                        window.key, Source.BCB_SGS, window.url, str(error), at, {}
                    )
                    outcome = Outcome(window.key, "failed", detail=str(error))
                manifest.save()
            results.append(outcome)
            logger.info("bcb_sgs: %-9s %s %s", outcome.action, window.key, outcome.detail)
        if not dry_run and not _has_rows(manifest, one.code):
            # An unknown series code answers 404 exactly like an empty window does.
            detail = "no data in any window: check the series code"
            results.append(Outcome(f"{Source.BCB_SGS}/{one.code}", "failed", detail=detail))
            logger.error("bcb_sgs: failed    series %s %s", one.code, detail)
        outcomes.extend(results)
    return outcomes


def _settled(record: FileRecord | None, window: Window, root: Path) -> bool:
    return record is not None and record.usable_in(root) and record.details.get("settled") == "yes"


def _mark_if_settled(record: FileRecord | None, window: Window, today: date) -> None:
    """Flag a window checked late enough after its end: it is never requested again."""
    if record is not None and record.ingested and today >= window.end + timedelta(days=SETTLE_DAYS):
        record.details["settled"] = "yes"


def _has_rows(manifest: Manifest, code: int) -> bool:
    prefix = f"{Source.BCB_SGS}/{code}/"
    return any(key.startswith(prefix) and (r.rows or 0) > 0 for key, r in manifest.files.items())


def _is_empty_window(response: httpx.Response) -> bool:
    """Tell "no observations in this window" apart from "no such series".

    Both are a 404. The first comes as JSON; the second as plain text ("A serie
    nao existe!"), which must fail loudly instead of being stored as an empty year.
    """
    if response.status_code != 404:
        return False
    if "json" not in response.headers.get("Content-Type", ""):
        raise SourceError(f"{response.request.url}: HTTP 404 and not JSON: unknown series?")
    return True


def _collect_window(
    fetcher: Fetcher,
    manifest: Manifest,
    root: Path,
    window: Window,
    now: Clock,
    full_check: bool,
) -> Outcome:
    record = manifest.get(window.key)
    previous = record if record is not None and record.usable_in(root) else None
    etag = previous.details.get("etag", "") if previous is not None else ""
    headers = {"If-None-Match": etag} if etag and not full_check else None
    response = fetcher.get(window.url, headers=headers, allowed=(200, 304, 404))
    if response.status_code == 304:
        return Outcome(window.key, "unchanged", detail="not modified")

    body = b"[]" if _is_empty_window(response) else response.content
    sha256 = hashlib.sha256(body).hexdigest()
    new_etag = response.headers.get("ETag", "")
    if previous is not None and previous.sha256 == sha256:
        previous.details["etag"] = new_etag
        if previous.landing_path is None and response.status_code == 200:
            # A record from before the landing zone: keep the bytes now that we have them.
            backfilled = landing.store_bytes(body, sha256, Source.BCB_SGS, root)
            previous.landing_path = backfilled.relative_to(root).as_posix()
        return Outcome(window.key, "unchanged", len(body), detail="same hash")
    if response.status_code == 404 and previous is not None and previous.rows:
        raise SourceError(f"{window.url}: had {previous.rows} rows, now reports no data")

    collected_at = now()
    # Only what the API really sent is landed: the "[]" of an empty window is ours.
    landed = (
        landing.store_bytes(body, sha256, Source.BCB_SGS, root)
        if response.status_code == 200
        else None
    )
    destination = raw_path(window, root)
    rows = write_json_records(body, destination, Provenance(window.url, sha256, collected_at))
    if rows == 0:
        destination.unlink()
    manifest.put(
        FileRecord(
            key=window.key,
            source=Source.BCB_SGS,
            url=window.url,
            status="ok",
            collected_at=collected_at.isoformat(),
            size_bytes=len(body),
            sha256=sha256,
            rows=rows,
            raw_path=destination.relative_to(root).as_posix() if rows else None,
            landing_path=landed.relative_to(root).as_posix() if landed else None,
            raw_file_sha256=file_sha256(destination) if rows else None,
            revision=(record.revision if record else 0) + 1,
            details={"series": str(window.code), "year": str(window.start.year), "etag": new_etag},
            superseded=previous.history_with_current() if previous is not None else [],
        )
    )
    action = "updated" if previous is not None else "new"
    return Outcome(window.key, action, len(body), rows, f"{rows:,} rows")
