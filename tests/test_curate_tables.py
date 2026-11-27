"""The table registry: what is public, what a contract catches, what gets exported."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from importlib import resources

import duckdb
import pytest

from fuel_price_lakehouse.curate.build import SQL_STEPS, build
from fuel_price_lakehouse.curate.tables import (
    PUBLIC_TABLES,
    STATION_LEVEL_COLUMNS,
    TABLES,
    Column,
    Table,
    export_public,
    violations,
)
from fuel_price_lakehouse.layout import curated_dir

(STATE_TABLE,) = (table for table in TABLES if table.name == "agg_price_weekly_state")

VALID_ROW = {
    "survey_week": date(2026, 10, 4),
    "state_code": "SP",
    "product": "GASOLINA",
    "unit": "BRL/L",
    "n_stations": 3,
    "n_observations": 3,
    "price_mean": 6.0,
    "price_median": 6.0,
    "price_p10": 5.9,
    "price_p90": 6.1,
}


def found_in(table: Table, *rows: dict) -> list[str]:
    """The contract violations of ``rows`` taken as the content of ``table``."""
    with duckdb.connect() as connection:
        columns = ", ".join(f'"{column.name}" {column.type}' for column in table.columns)
        connection.execute(f"CREATE TABLE {table.name} ({columns})")
        for row in rows:
            values = [row[column.name] for column in table.columns]
            connection.execute(
                f"INSERT INTO {table.name} VALUES ({', '.join('?' * len(values))})", values
            )
        return violations(connection, table)


def test_no_public_table_has_a_station_level_column():
    for table in PUBLIC_TABLES:
        leaked = {column.name for column in table.columns} & STATION_LEVEL_COLUMNS
        assert not leaked, f"{table.name} would publish {sorted(leaked)}"


def test_public_tables_are_exactly_the_aggregates_and_the_two_series():
    # Publishing one more table is a decision (docs/decisions/0002): it has to be made here too.
    assert [table.name for table in PUBLIC_TABLES] == [
        "usd_brl_daily",
        "ipca_monthly",
        "agg_price_weekly_national",
        "agg_price_weekly_region",
        "agg_price_weekly_state",
        "agg_price_weekly_flag",
    ]


def test_station_column_added_to_a_public_table_breaks_its_contract():
    leaky = replace(
        STATE_TABLE, columns=(*STATE_TABLE.columns, Column("station_cnpj", "VARCHAR", "CNPJ"))
    )

    problems = found_in(leaky, VALID_ROW | {"station_cnpj": "00000000000001"})

    assert problems == [
        "agg_price_weekly_state: public table with the station-level column station_cnpj"
    ]


def test_table_that_keeps_its_promises_has_no_violations():
    other_state = VALID_ROW | {"state_code": "RJ"}
    assert found_in(STATE_TABLE, VALID_ROW, other_state) == []


@pytest.mark.parametrize(
    ("change", "broken_promise"),
    [
        ({"product": None}, '"product" IS NOT NULL'),
        ({"state_code": "XX"}, "\"state_code\" IN ('AC', "),
        ({"product": "QUEROSENE"}, "\"product\" IN ('GASOLINA', "),
        ({"survey_week": date(2026, 10, 5)}, "isodow(survey_week) = 7"),  # a Monday
        ({"n_stations": 2}, "n_stations >= 3"),
        ({"n_observations": 2}, "n_observations >= n_stations"),
        ({"price_p10": 6.5}, "price_p10 <= price_median AND price_median <= price_p90"),
        ({"price_p90": 5.5}, "price_p10 <= price_median AND price_median <= price_p90"),
    ],
)
def test_contract_names_the_promise_a_row_breaks(change, broken_promise):
    (problem,) = found_in(STATE_TABLE, VALID_ROW | change)

    assert problem.startswith(f"agg_price_weekly_state: 1 rows break [{broken_promise}")


def test_contract_catches_a_repeated_key():
    problems = found_in(STATE_TABLE, VALID_ROW, VALID_ROW | {"price_mean": 6.5})

    assert problems == [
        "agg_price_weekly_state: 1 keys (survey_week, state_code, product) are repeated"
    ]


def test_contract_catches_a_column_of_the_wrong_type_or_out_of_place():
    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE usd_brl_daily AS "
            "SELECT 5.43::DOUBLE AS usd_brl, '2026-10-05' AS observed_on"
        )
        (usd_brl_daily,) = (table for table in TABLES if table.name == "usd_brl_daily")
        (problem,) = violations(connection, usd_brl_daily)

    assert problem == (
        "usd_brl_daily: columns are [('usd_brl', 'DOUBLE'), ('observed_on', 'VARCHAR')], "
        "expected [('observed_on', 'DATE'), ('usd_brl', 'DOUBLE')]"
    )


def test_every_table_is_described_column_by_column():
    for table in TABLES:
        names = [column.name for column in table.columns]
        assert len(set(names)) == len(names), table.name
        assert table.description.endswith("."), table.name
        assert set(table.key) <= set(names), table.name
        for column in table.columns:
            assert column.description and not column.description.endswith("."), column.name
            assert not (column.name in table.key and column.nullable), column.name


def test_every_table_has_a_sql_file_and_every_sql_file_runs():
    sql_folder = resources.files("fuel_price_lakehouse.curate").joinpath("sql")
    on_disk = {entry.name for entry in sql_folder.iterdir() if entry.name.endswith(".sql")}

    assert on_disk == {f"{step}.sql" for step in SQL_STEPS}
    assert {table.name for table in TABLES} <= set(SQL_STEPS)


def test_export_copies_the_public_tables_and_nothing_else(tmp_path):
    root, destination = tmp_path / "data", tmp_path / "release"
    build(root)

    copied = export_public(root, destination)

    public = sorted(f"{table.name}.parquet" for table in PUBLIC_TABLES)
    assert sorted(path.name for path in destination.iterdir()) == public
    assert sorted(path.name for path in copied) == public
    for path in copied:
        assert path.read_bytes() == (curated_dir(root) / path.name).read_bytes()


def test_export_refuses_when_the_tables_were_not_built(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"usd_brl_daily\.parquet: table not built yet"):
        export_public(tmp_path / "data", tmp_path / "release")
