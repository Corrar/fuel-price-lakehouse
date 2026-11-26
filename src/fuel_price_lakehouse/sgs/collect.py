"""Incremental collection of Banco Central SGS series into the raw layer.

The API has no "changed since" filter, so the history is cut into calendar
years. A year that is over is fetched once. The current year is fetched on
every run, and so is the previous one for a while after it ends: that is what
picks up new observations, late publications and revised values. A year is
only considered done when the manifest holds a response for it, so a request
that fails is simply made again by the next run.
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
_DATE = "%d/%m/%Y"


@dataclass(frozen=True)
class Series:
    code: int
    name: str
    settle_days: int
    """How long after a year ends it is still fetched again on every run."""

    @property
    def dataset(self) -> str:
        return f"sgs_{self.code}"


SERIES: tuple[Series, ...] = (
    Series(1, "usd_brl_sell_rate", settle_days=30),
    Series(433, "ipca_monthly_change", settle_days=100),
)


def years_to_request(
    collected: set[int], series: Series, since: date, today: date, *, force: bool = False
) -> list[int]:
    """Calendar years to ask the API for, given the years the manifest already holds."""
    wanted = range(since.year, today.year + 1)
    if force:
        return list(wanted)
    still_open = {today.year}
    if today - date(today.year, 1, 1) < timedelta(days=series.settle_days):
        still_open.add(today.year - 1)
    return [year for year in wanted if year not in collected or year in still_open]


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
    force: bool = False,
    dry_run: bool = False,
    pause: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    """Fetch every year of every series that is missing or may still change.

    One request failing does not stop the others, and the manifest is saved
    after each stored response.
    """
    report = Report()
    for one in series:
        collected = {e.details["year"] for e in manifest.for_dataset(SOURCE, one.dataset)}
        for year in years_to_request(collected, one, since, today, force=force):
            sleep(pause)
            try:
                outcome = _collect_year(client, manifest, root, one, year, policy, dry_run)
            except (http.FetchError, httpx.HTTPError, OSError) as error:
                log.error("SGS %s %s failed: %s", one.code, year, error)
                outcome = Outcome(SOURCE, one.dataset, str(year), Status.FAILED, detail=str(error))
            report.add(outcome)
    return report


def _collect_year(
    client: httpx.Client,
    manifest: Manifest,
    root: Path,
    series: Series,
    year: int,
    policy: http.RetryPolicy,
    dry_run: bool,
) -> Outcome:
    period = str(year)
    if dry_run:
        return Outcome(SOURCE, series.dataset, period, Status.PLANNED, detail=series.name)

    # The window always spans the whole calendar year, even when it has not
    # ended, so the request URL for a year never changes between runs. One year
    # is also far below the ten-year limit the API puts on daily series.
    params = {
        "formato": "json",
        "dataInicial": date(year, 1, 1).strftime(_DATE),
        "dataFinal": date(year, 12, 31).strftime(_DATE),
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

    known = manifest.latest(url)
    target = (
        partition_dir(
            Layer.RAW, Source.BCB_SGS, series.dataset, root, year=year, sha256=sha256[:12]
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
                "year": year,
                "observations": len(observations),
                "first_observation": min(observations).isoformat(),
                "last_observation": max(observations).isoformat(),
                "replaces": known.sha256 if known else None,
            },
        )
    )
    manifest.save()
    detail = f"{len(observations)} observations up to {max(observations).isoformat()}"
    return Outcome(SOURCE, series.dataset, period, Status.DOWNLOADED, len(body), detail)
