"""From the public tables to the JSON files the dashboard reads.

No statistic is computed here. Means, percentiles, the deflator and the
suppression of small cells are decided in the curated layer; this step only
turns rows into arrays aligned with one week axis, rounds them for transport,
and adds what the page needs to label them. The shapes are those of
``site/contract.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from fuel_price_lakehouse.curate.tables import MIN_STATIONS_PUBLISHED, TABLES
from fuel_price_lakehouse.layout import curated_dir
from fuel_price_lakehouse.reference import PRODUCTS, REGIONS, STATES
from fuel_price_lakehouse.site import downloads
from fuel_price_lakehouse.site.contract import (
    DECIMALS,
    EVENT_KEYS,
    NATIONAL,
    PRODUCT_IDS,
    SERIES_FILES,
    SeriesFile,
    SiteBuildError,
    check_keys,
)

REPOSITORY = "https://github.com/Corrar/fuel-price-lakehouse"

EVENTS_FILE = Path(__file__).parent / "reference" / "events.json"
"""Events the charts annotate. Kept by hand, each with the quote that backs it."""

FACT_TABLE = next(table for table in TABLES if table.name == "fuel_price")
"""Read for ``totals`` only: four counts that identify nobody."""


def data_files(
    connection: duckdb.DuckDBPyConnection,
    root: Path,
    report: dict[str, Any],
    generated_at: datetime,
) -> dict[str, Any]:
    """Every file of ``data/``, by name, ready to be checked and written.

    ``connection`` has one view per public table (``downloads.publish_tables``).
    """
    weeks = _week_axis(connection)
    labels = [week.isoformat() for week in weeks]
    files: dict[str, Any] = {
        spec.name: {
            "weeks": labels,
            **_week_arrays(connection, spec, weeks),
            "products": _series(connection, spec, weeks),
        }
        for spec in SERIES_FILES
    }
    published_products = files[NATIONAL.name]["products"]
    files["meta.json"] = _meta(connection, root, report, generated_at, weeks, published_products)
    files["events.json"] = _events()
    files["quality.json"] = report
    return files


def _meta(
    connection: duckdb.DuckDBPyConnection,
    root: Path,
    report: dict[str, Any],
    generated_at: datetime,
    weeks: list[date],
    published_products: dict[str, Any],
) -> dict[str, Any]:
    """The run, the period, what the page labels its series with, and what is behind them."""
    return {
        "generated_at": generated_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": report["run"]["id"],
        # The first month the run was asked to cover: only the run knows it. The
        # axis does not say it, since it starts on the first Sunday of a
        # published week (5 January for a history since January 2020).
        "since": report["run"]["since"],
        "latest_week": weeks[-1].isoformat(),
        "ipca_reference_month": _ipca_reference_month(connection),
        "min_stations_published": MIN_STATIONS_PUBLISHED,
        "products": [
            {
                "id": product.id,
                "label_pt": product.label_pt,
                "label_en": product.label_en,
                "unit": product.unit,
            }
            for product in PRODUCTS
            if product.id in published_products
        ],
        "states": [
            {"code": state.code, "name": state.name, "region": state.region} for state in STATES
        ],
        "regions": [
            {"code": region.code, "name_pt": region.name_pt, "name_en": region.name_en}
            for region in REGIONS
        ],
        "totals": _totals(connection, root, weeks),
        "downloads": downloads.listing(connection),
        "repository": REPOSITORY,
    }


def _week_axis(connection: duckdb.DuckDBPyConnection) -> list[date]:
    """Every Sunday from the first survey week with a published row to the latest one.

    A week without a survey is on the axis and has no value: a gap stays a gap
    and is drawn as one, instead of the weeks around it being joined.
    """
    published = " UNION ALL ".join(f"SELECT survey_week FROM {spec.table}" for spec in SERIES_FILES)
    ((first, latest),) = connection.execute(
        f"SELECT min(survey_week), max(survey_week) FROM ({published})"
    ).fetchall()
    if first is None:
        raise SiteBuildError("the public tables have no survey week: there is nothing to publish")
    return [first + timedelta(weeks=number) for number in range((latest - first).days // 7 + 1)]


def _week_arrays(
    connection: duckdb.DuckDBPyConnection, spec: SeriesFile, weeks: list[date]
) -> dict[str, list[Any]]:
    """The arrays of ``spec`` that describe the week itself, one value per week of the axis."""
    arrays: dict[str, list[Any]] = {field: [None] * len(weeks) for field in spec.week_fields}
    if not arrays:
        return arrays
    position = {week: index for index, week in enumerate(weeks)}
    columns = ", ".join(f"any_value({column})" for column in spec.week_fields.values())
    rows = connection.execute(
        f"SELECT survey_week, {columns} FROM {spec.table} GROUP BY survey_week"
    ).fetchall()
    for week, *values in rows:
        for field, value in zip(spec.week_fields, values, strict=True):
            arrays[field][position[week]] = _rounded(field, value)
    return arrays


def _series(
    connection: duckdb.DuckDBPyConnection, spec: SeriesFile, weeks: list[date]
) -> dict[str, Any]:
    """The rows of a table as nested objects: one per key level, then one array per field.

    Rows are read in the order the dashboard lists products, states and
    regions, so every object comes out with its keys in that order.
    """
    position = {week: index for index, week in enumerate(weeks)}
    keys = [column for column, _ in spec.levels]
    columns = ", ".join([*keys, *spec.fields.values()])
    order = ", ".join(f"list_position(?, {column})" for column in keys)
    rows = connection.execute(
        f"SELECT survey_week, {columns} FROM {spec.table} ORDER BY {order}",
        [list(values) for _, values in spec.levels],
    ).fetchall()
    tree: dict[str, Any] = {}
    for week, *rest in rows:
        series = tree
        for key in rest[: len(keys)]:
            series = series.setdefault(key, {})
        if not series:
            series.update((field, [None] * len(weeks)) for field in spec.fields)
        for field, value in zip(spec.fields, rest[len(keys) :], strict=True):
            series[field][position[week]] = _rounded(field, value)
    return tree


def _rounded(field: str, value: Any) -> Any:
    """Measures rounded for transport; counts, flags and missing values as they are."""
    return round(value, DECIMALS[field]) if isinstance(value, float) else value


def _ipca_reference_month(connection: duckdb.DuckDBPyConnection) -> str | None:
    """The month real prices are expressed in: the latest one with a published IPCA."""
    ((month,),) = connection.execute(
        "SELECT strftime(max(month), '%Y-%m') FROM ipca_monthly"
    ).fetchall()
    return None if month is None else str(month)


def _totals(connection: duckdb.DuckDBPyConnection, root: Path, weeks: list[date]) -> dict[str, int]:
    """How much data is behind the site: four counts over the fact table.

    This is the only read outside the public tables. The fact table is
    station-level and stays on the machine; what leaves it here is how many
    rows, stations, municipalities and weeks it has, within the weeks the site
    shows. A week of the fact table outside them (the first survey week of the
    history, which the files cover only in part) is behind no published number.
    """
    ((observations, stations, municipalities, surveyed),) = connection.execute(
        "SELECT count(*), count(DISTINCT station_cnpj),"
        " count(DISTINCT (state_code, municipality)), count(DISTINCT survey_week)"
        " FROM read_parquet(?, hive_partitioning = false)"
        " WHERE survey_week BETWEEN ? AND ?",
        [str(curated_dir(root) / FACT_TABLE.path), weeks[0], weeks[-1]],
    ).fetchall()
    return {
        "observations": observations,
        "stations": stations,
        "municipalities": municipalities,
        "weeks": surveyed,
    }


def _events() -> list[dict[str, Any]]:
    """The reference events as the dashboard gets them, oldest first.

    ``source_quote`` is not published: it is the evidence for whoever keeps
    the file, and the page links to the source instead.
    """
    events = []
    for number, event in enumerate(json.loads(EVENTS_FILE.read_text(encoding="utf-8")), start=1):
        where = f"{EVENTS_FILE}: event {number}"
        check_keys(event, (*EVENT_KEYS, "source_quote"), where)
        if not _is_iso_date(event["date"]):
            raise SiteBuildError(f"{where}: date {event['date']!r} is not YYYY-MM-DD")
        unknown = sorted(set(event["fuels"]) - set(PRODUCT_IDS))
        if unknown:
            raise SiteBuildError(f"{where}: {unknown} are not products of the reference list")
        events.append({key: event[key] for key in EVENT_KEYS})
    return sorted(events, key=lambda event: event["date"])


def _is_iso_date(value: object) -> bool:
    try:
        # The round trip rejects the other ISO forms fromisoformat accepts (20260311, 2026-W11-3).
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False
