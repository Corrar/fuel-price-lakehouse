"""Incremental collection of Banco Central SGS series into the raw layer.

The API has no "changed since" filter, so each run asks again for a short
window before the last observation it already holds. That overlap is what
catches late publications and revisions; the staged layer keeps the most
recently fetched value for each date.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from fuel_price_lakehouse import http
from fuel_price_lakehouse.layout import Layer, Source, partition_dir
from fuel_price_lakehouse.manifest import Entry, Manifest
from fuel_price_lakehouse.report import Outcome, Report, Status

log = logging.getLogger(__name__)

SOURCE = Source.BCB_SGS.value
BASE_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"
MAX_DAILY_WINDOW_YEARS = 10
_DATE = "%d/%m/%Y"


@dataclass(frozen=True)
class Series:
    code: int
    name: str
    daily: bool
    overlap_days: int
    """How far before the last known observation each run looks again."""

    @property
    def dataset(self) -> str:
        return f"sgs_{self.code}"


SERIES: tuple[Series, ...] = (
    Series(1, "usd_brl_sell_rate", daily=True, overlap_days=30),
    Series(433, "ipca_monthly_change", daily=False, overlap_days=100),
)


def windows(start: date, end: date, *, daily: bool) -> list[tuple[date, date]]:
    """Split ``start..end`` into request windows the API accepts.

    Daily series are refused beyond ten years per request; monthly series have
    no such limit.
    """
    if start > end:
        return []
    if not daily:
        return [(start, end)]
    result = []
    cursor = start
    while cursor <= end:
        year = cursor.year + MAX_DAILY_WINDOW_YEARS
        try:
            limit = cursor.replace(year=year)
        except ValueError:  # 29 February
            limit = cursor.replace(year=year, day=28)
        stop = min(end, limit - timedelta(days=1))
        result.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return result


def pending_ranges(
    entries: list[Entry], series: Series, since: date, today: date
) -> list[tuple[date, date]]:
    """Date ranges still to request, given what the manifest already holds."""
    if not entries:
        return [(since, today)]
    earliest = min(date.fromisoformat(e.details["window_start"]) for e in entries)
    latest = max(date.fromisoformat(e.details["last_observation"]) for e in entries)
    ranges = []
    if since < earliest:
        ranges.append((since, earliest - timedelta(days=1)))
    ranges.append((max(since, latest - timedelta(days=series.overlap_days)), today))
    return ranges


def parse_observations(body: bytes) -> list[date]:
    """Dates in an SGS response, after checking it has the expected shape."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise http.FetchError(f"SGS response is not JSON: {error}") from error
    if not isinstance(payload, list):
        raise http.FetchError(f"SGS response is not a list: {str(payload)[:200]}")
    dates = []
    for item in payload:
        if not isinstance(item, dict) or not {"data", "valor"} <= item.keys():
            raise http.FetchError(f"unexpected SGS item: {str(item)[:200]}")
        try:
            dates.append(datetime.strptime(item["data"], _DATE).date())
        except (TypeError, ValueError) as error:
            raise http.FetchError(f"unexpected SGS date: {item['data']!r}") from error
    return dates


def _is_empty_window(response: httpx.Response) -> bool:
    """The API answers 404 both for "no data in this window" and "no such series"."""
    return (
        response.status_code == 404
        and "json" in response.headers.get("Content-Type", "").lower()
        and "not found" in response.text.lower()
    )


def collect(
    client: httpx.Client,
    manifest: Manifest,
    root: Path,
    *,
    since: date,
    today: date,
    policy: http.RetryPolicy,
    series: tuple[Series, ...] = SERIES,
    dry_run: bool = False,
    pause: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    report = Report()
    for one in series:
        entries = manifest.for_dataset(SOURCE, one.dataset)
        for range_start, range_end in pending_ranges(entries, one, since, today):
            for start, end in windows(range_start, range_end, daily=one.daily):
                sleep(pause)
                period = f"{start:%Y%m%d}-{end:%Y%m%d}"
                try:
                    outcome = _collect_window(
                        client, manifest, root, one, start, end, period, policy, dry_run
                    )
                except (http.FetchError, httpx.HTTPError, OSError) as error:
                    log.error("SGS %s %s failed: %s", one.code, period, error)
                    outcome = Outcome(SOURCE, one.dataset, period, Status.FAILED, detail=str(error))
                report.add(outcome)
    return report


def _collect_window(
    client: httpx.Client,
    manifest: Manifest,
    root: Path,
    series: Series,
    start: date,
    end: date,
    period: str,
    policy: http.RetryPolicy,
    dry_run: bool,
) -> Outcome:
    if dry_run:
        return Outcome(SOURCE, series.dataset, period, Status.PLANNED, detail=series.name)

    params = {
        "formato": "json",
        "dataInicial": start.strftime(_DATE),
        "dataFinal": end.strftime(_DATE),
    }
    response = http.get(client, BASE_URL.format(code=series.code), policy=policy, params=params)
    if _is_empty_window(response):
        return Outcome(SOURCE, series.dataset, period, Status.EMPTY, detail="no observations")
    if response.status_code != 200:
        raise http.FetchError(f"HTTP {response.status_code}: {response.text[:200]}")

    body = response.content
    observations = parse_observations(body)
    if not observations:
        return Outcome(SOURCE, series.dataset, period, Status.EMPTY, detail="no observations")

    url = str(response.request.url)
    sha256 = hashlib.sha256(body).hexdigest()
    if manifest.has(url, sha256):
        return Outcome(SOURCE, series.dataset, period, Status.UNCHANGED, detail="same content")

    target = (
        partition_dir(
            Layer.RAW,
            Source.BCB_SGS,
            series.dataset,
            root,
            start=f"{start:%Y%m%d}",
            end=f"{end:%Y%m%d}",
            sha256=sha256[:12],
        )
        / "data.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)

    manifest.add(
        Entry(
            source=SOURCE,
            dataset=series.dataset,
            period=period,
            url=url,
            filename="data.json",
            size_bytes=len(body),
            sha256=sha256,
            raw_path=target.relative_to(root).as_posix(),
            fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
            details={
                "series_code": series.code,
                "series_name": series.name,
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "observations": len(observations),
                "first_observation": min(observations).isoformat(),
                "last_observation": max(observations).isoformat(),
            },
        )
    )
    manifest.save()
    detail = f"{len(observations)} observations up to {max(observations).isoformat()}"
    return Outcome(SOURCE, series.dataset, period, Status.DOWNLOADED, len(body), detail)
