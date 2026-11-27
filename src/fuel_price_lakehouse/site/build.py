"""Build the static site: the dashboard, the data files it reads, the public downloads.

The site is what this project publishes, so this is where the rules of
``docs/decisions/0002-what-is-published.md`` are applied one last time:

- Only a checked run is published. The builder reads ``quality/report.json``
  first and refuses unless ``quality.report.publishable`` says yes, and unless
  the report is at least as recent as the curated build: a report written
  before the tables were built again has not checked them. The caller has the
  same circuit breaker; this one is here so that no other way of calling the
  builder can skip it.
- Only the public tables are read (``site/downloads.py``), apart from four
  counts over the fact table that identify nobody (``site/data_files.py``).
- The data files are a contract with the dashboard (``site/contract.py``),
  checked before anything is written.
- A failed build changes nothing: the site is put together beside the
  destination and swapped in at the end (``site/folder.py``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from fuel_price_lakehouse.config import Clock, utc_now
from fuel_price_lakehouse.curate.build import REPORT_NAME as CURATED_BUILD_NAME
from fuel_price_lakehouse.curate.tables import PUBLIC_TABLES
from fuel_price_lakehouse.layout import curated_dir, quality_dir
from fuel_price_lakehouse.quality.report import REPORT_NAME, publishable
from fuel_price_lakehouse.site.contract import (
    NATIONAL,
    QUALITY_KEYS,
    RUN_KEYS,
    SiteBuildError,
    check_data_files,
    check_keys,
)
from fuel_price_lakehouse.site.data_files import FACT_TABLE, data_files
from fuel_price_lakehouse.site.downloads import export_csv, publish_tables
from fuel_price_lakehouse.site.folder import DATA, copy_static, replacing, write_json

__all__ = ["SiteBuildError", "SiteReport", "build", "check_data_files"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SiteReport:
    """What one build put in the site folder."""

    out: Path
    run_id: str
    latest_week: str
    weeks: int
    """Length of the week axis, weeks without a survey included."""
    data_bytes: dict[str, int]
    """Size of each data file, by name."""
    static_files: int
    """Files copied from the front-end folder. 0 when there is none yet."""


def build(root: Path, out: Path, *, now: Clock = utc_now) -> SiteReport:
    """Build the site from the data under ``root`` and put it at ``out``.

    Raises ``SiteBuildError`` when the run may not be published or a check
    fails. Whatever the failure, ``out`` is then exactly as it was before.
    """
    generated_at = now()
    report = _checked_report(root)
    _require_tables(root)
    with replacing(out) as site, duckdb.connect() as connection:
        static_files = copy_static(site)
        publish_tables(root, site, connection)
        files = data_files(connection, root, report, generated_at)
        check_data_files(files)
        export_csv(connection, site)
        data_bytes = {name: write_json(site / DATA / name, data) for name, data in files.items()}
        # Without this file GitHub Pages runs Jekyll over the folder.
        (site / ".nojekyll").touch()
    meta = files["meta.json"]
    result = SiteReport(
        out=out,
        run_id=meta["run_id"],
        latest_week=meta["latest_week"],
        weeks=len(files[NATIONAL.name]["weeks"]),
        data_bytes=data_bytes,
        static_files=static_files,
    )
    logger.info(
        "site: run %s (%s), %d weeks to %s, %s bytes of data (states.json %s), "
        "%d tables to download, %d front-end files -> %s",
        result.run_id,
        report["status"],
        result.weeks,
        result.latest_week,
        f"{sum(data_bytes.values()):,}",
        f"{data_bytes['states.json']:,}",
        len(meta["downloads"]),
        static_files,
        out,
    )
    return result


def _checked_report(root: Path) -> dict[str, Any]:
    """The quality report of the run, if that run may be published."""
    path = quality_dir(root) / REPORT_NAME
    if not path.is_file():
        raise SiteBuildError(f"{path} not found: the site is only built from a checked run")
    report = json.loads(path.read_text(encoding="utf-8"))
    check_keys(report, QUALITY_KEYS, str(path))
    check_keys(report["run"], RUN_KEYS, f"{path}: run")
    if not publishable(report):
        raise SiteBuildError(
            f"{path}: the run {report['run']['id']} has status {report['status']!r} "
            "or a failed check; only a run that passed or warned is published"
        )
    checked_at = datetime.fromisoformat(report["run"]["finished_at"])
    built_at = _curated_build_time(root)
    # The report keeps whole seconds; the build, microseconds.
    if checked_at < built_at.replace(microsecond=0):
        raise SiteBuildError(
            f"{path}: the run {report['run']['id']} finished before the curated build of "
            f"{built_at.isoformat()} and has not checked its tables; run the quality report again"
        )
    return report


def _curated_build_time(root: Path) -> datetime:
    """When the curated tables on disk were built, from what the build saved about itself."""
    path = curated_dir(root) / CURATED_BUILD_NAME
    try:
        return datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["built_at"])
    except (OSError, ValueError, KeyError) as error:
        reason = type(error).__name__
        raise SiteBuildError(f"{path}: no curated build to publish ({reason})") from error


def _require_tables(root: Path) -> None:
    """Every table the site reads has its files: the public ones and the fact table."""
    curated = curated_dir(root)
    missing = [
        table.name for table in (FACT_TABLE, *PUBLIC_TABLES) if not any(curated.glob(table.path))
    ]
    if missing:
        raise SiteBuildError(f"{curated}: curated tables not built: {', '.join(missing)}")
