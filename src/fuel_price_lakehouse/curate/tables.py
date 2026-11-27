"""The curated tables: what each one holds, what must be true of it, and which are public.

This registry is the one description of the curated layer. The builder exports
the columns listed here, in this order; the contract derived from it is checked
against the Parquet files of every build; the data dictionary in the docs is
generated from its descriptions; and ``PUBLIC_TABLES`` is the whitelist of what
may leave the machine that ran the pipeline
(``docs/decisions/0002-what-is-published.md``).

The contracts are checked by DuckDB, on the files, for every table alike: the
fact table has millions of rows and is never loaded into pandas, and one
mechanism for all tables is easier to trust than two. Column types are DuckDB
types, as read back from Parquet.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from fuel_price_lakehouse.layout import curated_dir
from fuel_price_lakehouse.reference import (
    PRODUCTS,
    REGIONS,
    STATES,
    UNIT_CUBIC_METRE,
    UNIT_CYLINDER_13KG,
    UNIT_LITRE,
)

MIN_STATIONS_PUBLISHED = 3
"""A public aggregate row needs at least this many distinct stations, so that no
published number is the price of one or two stations. There is no complementary
suppression, so this is not a disclosure-control guarantee (ADR 0002). At 3 the
rule removes 0.08 % of the gasoline state-week cells of 2020-2026 and 15.5 % of
the natural gas (GNV) ones."""

STATION_LEVEL_COLUMNS = frozenset(
    {
        "station_cnpj",
        "station_name",
        "street",
        "street_number",
        "address_complement",
        "neighbourhood",
        "postal_code",
    }
)
"""Columns that identify one business. No public table may have any of them."""

FLAG_TYPES = ("white_flag", "branded")
SOURCE_KINDS = ("semester", "monthly", "last_four_weeks")


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    """DuckDB type of the column in the Parquet file."""
    description: str
    nullable: bool = False
    allowed: tuple[str, ...] = ()
    """The only values the column may take. Empty: any value."""


@dataclass(frozen=True)
class Table:
    name: str
    public: bool
    description: str
    key: tuple[str, ...]
    """Columns that identify a row: never null, unique together."""
    columns: tuple[Column, ...]
    rules: tuple[str, ...] = ()
    """SQL conditions every row satisfies, on top of types, nulls, keys and value sets."""
    partitioned_by_year_of: str | None = None
    """Date column whose year splits the table into one file per year."""

    @property
    def path(self) -> str:
        """Where the table is, relative to the curated directory.

        One file, or for a partitioned table a glob with ``*`` in place of the
        year, which DuckDB and PyArrow both read as it is.
        """
        if self.partitioned_by_year_of:
            return f"{self.name}/year=*/data.parquet"
        return f"{self.name}.parquet"


class ContractError(Exception):
    """A table that was just built is not what the registry says it is.

    This is a bug in the pipeline, or a staged layer that broke its own
    contract. The build stops and the tables of the previous build stay in place.
    """


_SURVEY_WEEK = Column(
    "survey_week", "DATE", "Sunday that starts the ANP survey week, which runs to Saturday"
)
_PRODUCT = Column(
    "product", "VARCHAR", "Fuel, under the name ANP gives it", allowed=tuple(p.id for p in PRODUCTS)
)
_UNIT = Column(
    "unit",
    "VARCHAR",
    "Unit of the prices: BRL per litre, per cubic metre (GNV) or per 13 kg cylinder (GLP)",
    allowed=(UNIT_LITRE, UNIT_CUBIC_METRE, UNIT_CYLINDER_13KG),
)
_STATE = Column(
    "state_code", "VARCHAR", "State (UF), two letters", allowed=tuple(s.code for s in STATES)
)
_REGION = Column(
    "region_code", "VARCHAR", "Region: N, NE, CO, SE or S", allowed=tuple(r.code for r in REGIONS)
)

_SUNDAY = "isodow(survey_week) = 7"


def _weekly(*key: Column) -> tuple[Column, ...]:
    """Columns every weekly aggregate has: its key, then the price statistics.

    Every statistic is over stations: a station surveyed twice in the week
    counts once, with the mean of its two prices.
    """
    return (
        *key,
        _UNIT,
        Column("n_stations", "BIGINT", "Distinct stations behind the figures of the row"),
        Column(
            "n_observations",
            "BIGINT",
            "Prices collected; more than n_stations when a station was surveyed twice in the week",
        ),
        Column(
            "price_mean",
            "DOUBLE",
            "Mean price over the stations, each counting once; not weighted by sales volume "
            "as ANP's official averages are, so it differs from them",
        ),
        Column("price_median", "DOUBLE", "Median of the station prices"),
        Column(
            "price_p10", "DOUBLE", "10th percentile of the station prices (linear interpolation)"
        ),
        Column(
            "price_p90", "DOUBLE", "90th percentile of the station prices (linear interpolation)"
        ),
    )


_ROWS_LEFT_OUT = (
    f"Rows built from fewer than {MIN_STATIONS_PUBLISHED} stations are not published, "
    "and weeks without a survey have no rows."
)
"""What a reader of any weekly table needs to know about the rows it does not have."""


_WEEKLY_RULES = (
    _SUNDAY,
    f"n_stations >= {MIN_STATIONS_PUBLISHED}",
    "n_observations >= n_stations",
    "price_p10 <= price_median AND price_median <= price_p90",
)

TABLES: tuple[Table, ...] = (
    Table(
        name="fuel_price",
        public=False,
        description=(
            "Every price ANP collected: one row per station, product and collection date, "
            "with the repetition between ANP's semester, monthly and four-week files removed."
        ),
        key=("station_cnpj", "product", "collected_on"),
        columns=(
            Column("station_cnpj", "VARCHAR", "CNPJ of the station, 14 digits"),
            _PRODUCT,
            Column("collected_on", "DATE", "Day the price was collected at the station"),
            Column("survey_week", "DATE", "Sunday that starts the ANP survey week of the day"),
            Column("sale_price", "DOUBLE", "Price charged at the station, in BRL per unit"),
            Column(
                "purchase_price",
                "DOUBLE",
                "Price the station paid its distributor; ANP stopped publishing it in 2020",
                nullable=True,
            ),
            _UNIT,
            _STATE,
            _REGION,
            Column("municipality", "VARCHAR", "Municipality of the station, as ANP writes it"),
            Column(
                "brand",
                "VARCHAR",
                "Distributor brand (Bandeira) on that day; BRANCA means unbranded",
            ),
            Column(
                "source_kind",
                "VARCHAR",
                "Kind of ANP file the row was taken from",
                allowed=SOURCE_KINDS,
            ),
            Column(
                "source_period",
                "VARCHAR",
                "Period of that file: a semester (2026-S1), a month (2026-10), latest for the "
                "rolling file of the last four weeks, or the collection date of an earlier "
                "version of that file kept as a snapshot",
            ),
            Column(
                "n_candidates",
                "BIGINT",
                "How many rows of ANP's files carry this station, product and date "
                "(ANP publishes an observation in up to three files)",
            ),
            Column(
                "price_conflict",
                "BOOLEAN",
                "True when those rows do not all give the same sale price",
            ),
        ),
        rules=(_SUNDAY, "collected_on BETWEEN survey_week AND survey_week + 6"),
        partitioned_by_year_of="collected_on",
    ),
    Table(
        name="station",
        public=False,
        description=(
            "Every station ANP surveyed: one row per CNPJ, with the name, brand and address "
            "of its most recent observation."
        ),
        key=("station_cnpj",),
        columns=(
            Column("station_cnpj", "VARCHAR", "CNPJ of the station, 14 digits"),
            Column("station_name", "VARCHAR", "Registered name (Revenda)"),
            Column("brand", "VARCHAR", "Distributor brand (Bandeira); BRANCA means unbranded"),
            Column("street", "VARCHAR", "Street", nullable=True),
            Column("street_number", "VARCHAR", "Street number, free text", nullable=True),
            Column("address_complement", "VARCHAR", "Address complement", nullable=True),
            Column("neighbourhood", "VARCHAR", "Neighbourhood (Bairro)", nullable=True),
            Column("postal_code", "VARCHAR", "CEP, 8 digits", nullable=True),
            Column("municipality", "VARCHAR", "Municipality, as ANP writes it"),
            _STATE,
            _REGION,
            Column("first_seen", "DATE", "First day a price was collected at the station"),
            Column("last_seen", "DATE", "Latest day a price was collected at the station"),
            Column("n_observations", "BIGINT", "Rows the station has in fuel_price"),
            Column("n_names", "BIGINT", "Distinct registered names over its history"),
            Column("n_brands", "BIGINT", "Distinct brands over its history, all products together"),
        ),
        rules=("first_seen <= last_seen",),
    ),
    Table(
        name="usd_brl_daily",
        public=True,
        description=(
            "The US dollar in Brazilian reais: one row per day with a rate "
            "(Banco Central, SGS series 1)."
        ),
        key=("observed_on",),
        columns=(
            Column("observed_on", "DATE", "Day of the rate; business days only"),
            Column("usd_brl", "DOUBLE", "Reais per US dollar, selling rate"),
        ),
        rules=("usd_brl > 0",),
    ),
    Table(
        name="ipca_monthly",
        public=True,
        description=(
            "Consumer price inflation (IPCA): one row per month, with the index and the "
            "deflator derived from it (Banco Central, SGS series 433)."
        ),
        key=("month",),
        columns=(
            Column("month", "DATE", "First day of the month"),
            Column("pct_change", "DOUBLE", "IPCA change within the month, in percent"),
            Column("index", "DOUBLE", "Chained index: 100 in the month before the first one"),
            Column(
                "deflator_to_latest",
                "DOUBLE",
                "Multiply a price of this month by it to express it in reais of the latest month "
                "with a published IPCA",
            ),
        ),
        rules=("day(month) = 1", "index > 0"),
    ),
    Table(
        name="agg_price_weekly_national",
        public=True,
        description=(
            "Weekly price statistics per product for the whole country, with the dollar "
            f"and the inflation adjustment of the week. {_ROWS_LEFT_OUT}"
        ),
        key=("survey_week", "product"),
        columns=(
            *_weekly(_SURVEY_WEEK, _PRODUCT),
            Column(
                "n_states",
                "BIGINT",
                "States with at least one price in the week, including states whose own row "
                "is not published",
            ),
            Column(
                "usd_brl_mean",
                "DOUBLE",
                "Mean of the daily dollar rates dated within the week; empty when there is none",
                nullable=True,
            ),
            Column(
                "ipca_deflator",
                "DOUBLE",
                "IPCA deflator of the month of the week's Wednesday; 1 when provisional",
            ),
            Column(
                "ipca_provisional",
                "BOOLEAN",
                "True when that month has no IPCA yet, so the week is not deflated",
            ),
            Column(
                "price_mean_real",
                "DOUBLE",
                "price_mean x ipca_deflator: the mean in reais of the latest IPCA month",
            ),
        ),
        rules=(*_WEEKLY_RULES, "NOT ipca_provisional OR ipca_deflator = 1"),
    ),
    Table(
        name="agg_price_weekly_region",
        public=True,
        description=f"Weekly price statistics per region and product. {_ROWS_LEFT_OUT}",
        key=("survey_week", "region_code", "product"),
        columns=_weekly(_SURVEY_WEEK, _REGION, _PRODUCT),
        rules=_WEEKLY_RULES,
    ),
    Table(
        name="agg_price_weekly_state",
        public=True,
        description=f"Weekly price statistics per state and product. {_ROWS_LEFT_OUT}",
        key=("survey_week", "state_code", "product"),
        columns=_weekly(_SURVEY_WEEK, _STATE, _PRODUCT),
        rules=_WEEKLY_RULES,
    ),
    Table(
        name="agg_price_weekly_flag",
        public=True,
        description=(
            "Weekly price statistics per product for unbranded and branded stations "
            f"(the six automotive fuels; GLP is not covered). {_ROWS_LEFT_OUT}"
        ),
        key=("survey_week", "product", "flag_type"),
        columns=_weekly(
            _SURVEY_WEEK,
            _PRODUCT,
            Column(
                "flag_type",
                "VARCHAR",
                "white_flag for unbranded stations (brand BRANCA), branded for the others",
                allowed=FLAG_TYPES,
            ),
        ),
        rules=(*_WEEKLY_RULES, "product <> 'GLP'"),
    ),
)

PUBLIC_TABLES: tuple[Table, ...] = tuple(table for table in TABLES if table.public)


def violations(connection: duckdb.DuckDBPyConnection, table: Table) -> list[str]:
    """Everything wrong with the relation called ``table.name``. Empty: the contract holds."""
    found = [(name, kind) for name, kind, *_ in _rows(connection, f"DESCRIBE {table.name}")]
    expected = [(column.name, column.type) for column in table.columns]
    if found != expected:
        # The other checks are written against the expected columns.
        return [f"{table.name}: columns are {found}, expected {expected}"]
    problems = []
    if table.public:
        problems += [
            f"{table.name}: public table with the station-level column {column.name}"
            for column in table.columns
            if column.name in STATION_LEVEL_COLUMNS
        ]
    conditions = _row_conditions(table)
    broken = ", ".join(f"count(*) FILTER (WHERE NOT ({condition}))" for condition in conditions)
    (counts,) = _rows(connection, f"SELECT {broken} FROM {table.name}")
    problems += [
        f"{table.name}: {count} rows break [{condition}]"
        for condition, count in zip(conditions, counts, strict=True)
        if count
    ]
    key = ", ".join(_quoted(name) for name in table.key)
    ((repeated,),) = _rows(
        connection,
        f"SELECT count(*) FROM (SELECT 1 FROM {table.name} GROUP BY {key} HAVING count(*) > 1)",
    )
    if repeated:
        problems.append(f"{table.name}: {repeated} keys ({', '.join(table.key)}) are repeated")
    return problems


def export_public(root: Path, destination: Path) -> list[Path]:
    """Copy the public tables of the latest build to ``destination``, and nothing else.

    This is the only way curated data leaves the data directory: a table that
    is not in ``PUBLIC_TABLES`` cannot be exported by mistake.
    """
    curated = curated_dir(root)
    copied = []
    for table in PUBLIC_TABLES:
        sources = sorted(curated.glob(table.path))
        if not sources:
            raise FileNotFoundError(f"{curated / table.path}: table not built yet")
        for source in sources:
            target = destination / source.relative_to(curated)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied.append(target)
    return copied


def _row_conditions(table: Table) -> list[str]:
    """One SQL condition per promise the registry makes about every row."""
    conditions = [
        f"{_quoted(column.name)} IS NOT NULL" for column in table.columns if not column.nullable
    ]
    for column in table.columns:
        if column.allowed:
            values = ", ".join(f"'{value}'" for value in column.allowed)
            conditions.append(f"{_quoted(column.name)} IN ({values})")
    return [*conditions, *table.rules]


def _quoted(name: str) -> str:
    return f'"{name}"'


def _rows(connection: duckdb.DuckDBPyConnection, query: str) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = connection.execute(query).fetchall()
    return rows
