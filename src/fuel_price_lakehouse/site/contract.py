"""The data files are a contract with the dashboard: their shapes, and the check that enforces them.

The front-end reads the files under ``data/`` with ``fetch`` and nothing else,
so their shape is the whole interface between the pipeline and the page. It is
written down here once, in ``SERIES_FILES`` and the ``*_KEYS`` constants, and
``check_data_files`` holds every file to it before anything is written.

``meta.json``
    The run, the period, the products, states and regions, totals, downloads.
``national.json``
    ``{"weeks": [...], "usd_brl": [...], "ipca_deflator": [...],
    "ipca_provisional": [...], "products": {product: {field: [...]}}}``
``regions.json``, ``states.json``, ``flags.json``
    ``{"weeks": [...], "products": {product: {key: {field: [...]}}}}`` with the
    region, the state or ``white_flag`` / ``branded`` as the key.
``events.json``
    Dated, sourced events for chart annotations.
``quality.json``
    The quality report of the run, as it is.

``weeks`` is the same in every file: every Sunday from the first survey week
with a published row to the latest one. Every array has one value per week and
``null`` where the table has no row: a week without a survey, or a cell left
out for having too few stations. A product, state or region without any row is
absent from its object.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

from fuel_price_lakehouse.curate.tables import FLAG_TYPES, STATION_LEVEL_COLUMNS
from fuel_price_lakehouse.reference import PRODUCTS, REGIONS, STATES


class SiteBuildError(Exception):
    """The site cannot be built from what is on disk.

    Either the run may not be published, or a table, the events file or a data
    file is not what it has to be. Nothing was written: the previous site, if
    there is one, is still in place.
    """


DECIMALS = {
    "mean": 3,
    "median": 3,
    "p10": 3,
    "p90": 3,
    "mean_real": 3,
    "usd_brl": 4,
    "ipca_deflator": 6,
}
"""Decimals of each published measure. Prices: the three ANP collects them
with. The exchange rate: the four Banco Central quotes it with. The deflator:
six, so that rounding it moves a deflated price by far less than its last
decimal."""

META_KEYS = (
    "generated_at",
    "run_id",
    "since",
    "latest_week",
    "ipca_reference_month",
    "min_stations_published",
    "products",
    "states",
    "regions",
    "totals",
    "downloads",
    "repository",
)
META_ITEM_KEYS = {
    "products": ("id", "label_pt", "label_en", "unit"),
    "states": ("code", "name", "region"),
    "regions": ("code", "name_pt", "name_en"),
    "downloads": ("table", "description", "rows", "files"),
}
TOTALS_KEYS = ("observations", "stations", "municipalities", "weeks")
EVENT_KEYS = ("date", "title_pt", "title_en", "detail_pt", "detail_en", "fuels", "source_url")
QUALITY_KEYS = ("run", "status", "freshness", "funnel", "rules", "files", "checks", "history")
RUN_KEYS = ("id", "started_at", "finished_at", "code_version", "since")

PRODUCT_IDS = tuple(product.id for product in PRODUCTS)
_BY_PRODUCT = ("product", PRODUCT_IDS)
_STATISTICS = {
    "mean": "price_mean",
    "median": "price_median",
    "p10": "price_p10",
    "p90": "price_p90",
    "n_stations": "n_stations",
}


@dataclass(frozen=True)
class SeriesFile:
    """A data file of weekly series, and the public table it is a reshaping of."""

    name: str
    table: str
    levels: tuple[tuple[str, tuple[str, ...]], ...]
    """The key columns after the week, outermost first, each with its values in
    the order the dashboard lists them. The file nests one object per level."""
    fields: dict[str, str]
    """Name of each array of a series, and the column it carries."""
    week_fields: dict[str, str]
    """Arrays about the week itself, at the top of the file: name and column.
    The table repeats their value on every row of the week."""


SERIES_FILES = (
    SeriesFile(
        name="national.json",
        table="agg_price_weekly_national",
        levels=(_BY_PRODUCT,),
        fields={
            "mean": "price_mean",
            "median": "price_median",
            "p10": "price_p10",
            "p90": "price_p90",
            "mean_real": "price_mean_real",
            "n_stations": "n_stations",
            "n_states": "n_states",
        },
        week_fields={
            "usd_brl": "usd_brl_mean",
            "ipca_deflator": "ipca_deflator",
            "ipca_provisional": "ipca_provisional",
        },
    ),
    SeriesFile(
        name="regions.json",
        table="agg_price_weekly_region",
        levels=(_BY_PRODUCT, ("region_code", tuple(region.code for region in REGIONS))),
        fields=_STATISTICS,
        week_fields={},
    ),
    SeriesFile(
        name="states.json",
        table="agg_price_weekly_state",
        levels=(_BY_PRODUCT, ("state_code", tuple(state.code for state in STATES))),
        fields=_STATISTICS,
        week_fields={},
    ),
    SeriesFile(
        name="flags.json",
        table="agg_price_weekly_flag",
        levels=(_BY_PRODUCT, ("flag_type", FLAG_TYPES)),
        fields={"mean": "price_mean", "median": "price_median", "n_stations": "n_stations"},
        week_fields={},
    ),
)
NATIONAL = SERIES_FILES[0]


def check_data_files(files: Mapping[str, Any]) -> None:
    """Refuse data files that are not what the dashboard was written against.

    ``files`` holds every data file by name, as ``build`` puts them together.
    No file carries a station-level field, NaN or Infinity. The series files
    share one axis of consecutive Sundays; each has exactly the keys of its
    ``SeriesFile``, products, states and regions of the reference lists only,
    and arrays as long as the axis, rounded as ``DECIMALS`` says. ``meta.json``
    has exactly its keys. Events and the quality report are checked where they
    are read.
    """
    for name, payload in files.items():
        _refuse_unpublishable_values(payload, name)
    weeks = files[NATIONAL.name]["weeks"]
    _check_week_axis(weeks)
    for spec in SERIES_FILES:
        payload = files[spec.name]
        check_keys(payload, ("weeks", *spec.week_fields, "products"), spec.name)
        if payload["weeks"] != weeks:
            raise SiteBuildError(f"{spec.name}: its weeks are not the weeks of {NATIONAL.name}")
        _check_arrays(payload, spec.week_fields, len(weeks), spec.name)
        allowed = [values for _, values in spec.levels]
        _check_series(payload["products"], allowed, spec.fields, len(weeks), spec.name)
    meta = files["meta.json"]
    check_keys(meta, META_KEYS, "meta.json")
    check_keys(meta["totals"], TOTALS_KEYS, "meta.json: totals")
    for section, keys in META_ITEM_KEYS.items():
        for item in meta[section]:
            check_keys(item, keys, f"meta.json: {section}")


def check_keys(node: Any, expected: Collection[str], where: str) -> None:
    """``node`` is an object with exactly the keys ``expected``, in any order."""
    if not isinstance(node, dict) or set(node) != set(expected):
        found = sorted(node) if isinstance(node, dict) else type(node).__name__
        raise SiteBuildError(f"{where}: expected the keys {sorted(expected)}, found {found}")


def _refuse_unpublishable_values(node: Any, where: str) -> None:
    """No station-level field and no NaN or Infinity, however deep.

    The field names are a tripwire for a payload that starts carrying rows, for
    example a quality report with examples in it. What keeps stations out of
    the series is that they are read from the public tables only. NaN and
    Infinity are not JSON: a browser refuses the whole file.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key in STATION_LEVEL_COLUMNS:
                raise SiteBuildError(f"{where}: station-level field {key!r} in a public file")
            _refuse_unpublishable_values(value, f"{where}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _refuse_unpublishable_values(value, f"{where}[{index}]")
    elif isinstance(node, float) and not math.isfinite(node):
        raise SiteBuildError(f"{where}: {node} is not a number a JSON file can carry")


def _check_week_axis(labels: Sequence[str]) -> None:
    days = [date.fromisoformat(label) for label in labels]
    contiguous = all(later - earlier == timedelta(weeks=1) for earlier, later in pairwise(days))
    if not days or days[0].isoweekday() != 7 or not contiguous:
        raise SiteBuildError("weeks: not a run of consecutive Sundays")


def _check_series(
    node: Any,
    levels: Sequence[Collection[str]],
    fields: Collection[str],
    n_weeks: int,
    where: str,
) -> None:
    """``node`` nests one object per level, with known keys only, down to one array per field."""
    if not levels:
        check_keys(node, fields, where)
        _check_arrays(node, fields, n_weeks, where)
        return
    unknown = sorted(set(node) - set(levels[0]))
    if unknown:
        raise SiteBuildError(f"{where}: {unknown} are not in the reference list")
    for key, child in node.items():
        _check_series(child, levels[1:], fields, n_weeks, f"{where}.{key}")


def _check_arrays(node: Mapping[str, Any], fields: Iterable[str], n_weeks: int, where: str) -> None:
    """Each array has one value per week, rounded the way ``DECIMALS`` says."""
    for field in fields:
        values = node[field]
        if len(values) != n_weeks:
            raise SiteBuildError(f"{where}.{field}: {len(values)} values for {n_weeks} weeks")
        decimals = DECIMALS.get(field)
        if decimals is not None and any(
            value is not None and round(value, decimals) != value for value in values
        ):
            raise SiteBuildError(f"{where}.{field}: not rounded to {decimals} decimals")
