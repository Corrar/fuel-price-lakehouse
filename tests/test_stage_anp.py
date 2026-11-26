"""Staging of ANP price files.

Every test writes a small ANP-shaped CSV, copies it with the raw layer's own
writer, stages the result and looks at the Parquet files that come out. The
stations are made up: ``11.222.333/0001-81`` is an arithmetic example.
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, NamedTuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pandera.errors import SchemaError, SchemaErrors

from fuel_price_lakehouse.collect.raw import Provenance, write_anp_csv
from fuel_price_lakehouse.reference import PRODUCT_BY_ID
from fuel_price_lakehouse.stage import anp
from fuel_price_lakehouse.stage.model import SchemaDriftError, StagedFrames, write_parquet
from fuel_price_lakehouse.stage.rules import FileContext, Severity

URL = "https://example.test/shpc/dsan/2026/10-gasolina-etanol.csv"
SHA256 = "5f" * 32
COLLECTED_AT = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)

# One valid observation, in the order of the file layout. 05/10/2026 is a Monday.
VALID = {
    "Regiao - Sigla": "SE",
    "Estado - Sigla": "SP",
    "Municipio": "SAO PAULO",
    "Revenda": "POSTO UM LTDA",
    "CNPJ da Revenda": " 11.222.333/0001-81",
    "Nome da Rua": "RUA A",
    "Numero Rua": "100",
    "Complemento": "",
    "Bairro": "CENTRO",
    "Cep": "01000-000",
    "Produto": "GASOLINA",
    "Data da Coleta": "05/10/2026",
    "Valor de Venda": "5,99",
    "Valor de Compra": "",
    "Unidade de Medida": "R$ / litro",
    "Bandeira": "BRANCA",
}
ETHANOL = VALID | {"Produto": "ETANOL", "Valor de Venda": "3,99"}
CNG = VALID | {"Produto": "GNV", "Unidade de Medida": "R$ / m³", "Valor de Venda": "4,50"}
LPG = VALID | {"Produto": "GLP", "Unidade de Medida": "R$ / 13 kg", "Valor de Venda": "110,00"}

Row = dict[str, str] | list[str]


class Outcome(NamedTuple):
    """What staging one file left behind, read back from the Parquet files."""

    staged: list[dict[str, Any]]
    quarantine: list[dict[str, Any]]
    errors: dict[str, int]
    """Rows per error rule, rules nobody broke left out."""
    warnings: dict[str, int]


def raw_frame(tmp_path: Path, *rows: Row, header: list[str] | None = None) -> pd.DataFrame:
    """The rows as the raw layer hands them over: all text, plus the tracking columns."""
    download = tmp_path / "download.csv"
    with download.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\r\n")
        writer.writerow(header or anp.SOURCE_COLUMNS)
        writer.writerows(list(row.values()) if isinstance(row, dict) else row for row in rows)
    write_anp_csv(download, tmp_path / "raw.parquet", Provenance(URL, SHA256, COLLECTED_AT))
    return pd.read_parquet(tmp_path / "raw.parquet")


def context(
    kind: str = "monthly", period: str = "2026-10", collected_at: datetime = COLLECTED_AT
) -> FileContext:
    details = {"kind": kind, "fuel_group": "gasoline_ethanol", "period": period}
    return FileContext(URL, URL, SHA256, collected_at, details)


def stage(tmp_path: Path, *rows: Row, **file: Any) -> StagedFrames:
    return anp.stage(raw_frame(tmp_path, *rows), context(**file), now=lambda: NOW)


def run(tmp_path: Path, *rows: Row, **file: Any) -> Outcome:
    frames = stage(tmp_path, *rows, **file)
    write_parquet(frames.staged, tmp_path / "staged.parquet", anp.STAGED_ARROW_SCHEMA)
    write_parquet(frames.quarantine, tmp_path / "quarantine.parquet", anp.QUARANTINE_ARROW_SCHEMA)
    return Outcome(
        pq.read_table(tmp_path / "staged.parquet").to_pylist(),
        pq.read_table(tmp_path / "quarantine.parquet").to_pylist(),
        {rule_id: count for rule_id, count in frames.error_counts.items() if count},
        {rule_id: count for rule_id, count in frames.warning_counts.items() if count},
    )


def numbers(rows: list[dict[str, Any]]) -> list[int]:
    return [row["_source_row_number"] for row in rows]


# --- the staged table ----------------------------------------------------------------------------


def test_valid_row_is_staged_typed_and_normalised(tmp_path):
    outcome = run(tmp_path, VALID)

    assert outcome.quarantine == []
    assert outcome.staged == [
        {
            "region_code": "SE",
            "state_code": "SP",
            "municipality": "SAO PAULO",
            "station_name": "POSTO UM LTDA",
            "station_cnpj": "11222333000181",
            "street": "RUA A",
            "street_number": "100",
            "address_complement": None,
            "neighbourhood": "CENTRO",
            "postal_code": "01000000",
            "product": "GASOLINA",
            "collected_on": date(2026, 10, 5),
            "sale_price": 5.99,
            "purchase_price": None,
            "unit": "BRL/L",
            "brand": "BRANCA",
            "_source_kind": "monthly",
            "_source_period": "2026-10",
            "_source_url": URL,
            "_source_sha256": SHA256,
            "_source_row_number": 1,
            "_ingested_at": COLLECTED_AT,
            "_warnings": [],
        }
    ]
    assert (outcome.errors, outcome.warnings) == ({}, {})


def test_cnpj_is_staged_as_14_digits_with_or_without_the_mask(tmp_path):
    bare = ETHANOL | {"CNPJ da Revenda": "11222333000181"}
    outcome = run(tmp_path, VALID, bare)
    assert [row["station_cnpj"] for row in outcome.staged] == ["11222333000181"] * 2


def test_each_product_gets_its_normalised_unit(tmp_path):
    outcome = run(tmp_path, VALID, CNG, LPG)
    assert [row["unit"] for row in outcome.staged] == ["BRL/L", "BRL/m3", "BRL/13kg"]


def test_cubic_metre_written_with_a_plain_3_is_accepted(tmp_path):
    outcome = run(tmp_path, CNG | {"Unidade de Medida": "R$ / m3"})
    assert [row["unit"] for row in outcome.staged] == ["BRL/m3"]
    assert outcome.errors == {}


def test_whitespace_is_normalised_and_empty_text_becomes_null(tmp_path):
    untidy = VALID | {
        "Municipio": " SAO  PAULO ",
        "Revenda": "POSTO\u00a0UM   LTDA",
        "Nome da Rua": "  AV   BRASIL",
        "Numero Rua": " ",
        "Complemento": "\u00a0",
        "Bairro": "",
        "Bandeira": "BRANCA ",
    }
    staged = run(tmp_path, untidy).staged[0]

    assert staged["municipality"] == "SAO PAULO"
    assert staged["station_name"] == "POSTO UM LTDA"
    assert staged["street"] == "AV BRASIL"
    assert staged["brand"] == "BRANCA"
    assert staged["street_number"] is None
    assert staged["address_complement"] is None
    assert staged["neighbourhood"] is None


def test_brand_is_staged_in_upper_case(tmp_path):
    """No rule can check an open list, so one brand must not come out in two spellings."""
    days = ("05/10/2026", "06/10/2026")
    rows = [
        VALID | {"Data da Coleta": day, "Bandeira": brand}
        for day, brand in zip(days, ("Raízen", "RAÍZEN"), strict=True)
    ]
    outcome = run(tmp_path, *rows)

    assert [row["brand"] for row in outcome.staged] == ["RAÍZEN", "RAÍZEN"]
    assert (outcome.errors, outcome.warnings) == ({}, {})


def test_sale_price_keeps_the_decimals_it_was_published_with(tmp_path):
    days = ("05/10/2026", "06/10/2026", "07/10/2026")
    prices = ("5,99", "5,999", "6")
    rows = [
        VALID | {"Data da Coleta": day, "Valor de Venda": price}
        for day, price in zip(days, prices, strict=True)
    ]
    assert [row["sale_price"] for row in run(tmp_path, *rows).staged] == [5.99, 5.999, 6.0]


def test_purchase_price_is_read_when_published(tmp_path):
    outcome = run(tmp_path, VALID | {"Valor de Compra": "4,1234"})
    assert outcome.staged[0]["purchase_price"] == 4.1234
    assert outcome.warnings == {}


# --- error rules ---------------------------------------------------------------------------------

ONE_BROKEN_RULE = [
    ("state_is_known", {"Estado - Sigla": "XX"}),
    ("state_is_known", {"Estado - Sigla": "sp"}),
    ("state_is_known", {"Estado - Sigla": ""}),
    ("region_matches_state", {"Regiao - Sigla": "S"}),
    ("region_matches_state", {"Regiao - Sigla": ""}),
    ("municipality_present", {"Municipio": "  "}),
    ("station_name_present", {"Revenda": ""}),
    ("cnpj_has_14_digits", {"CNPJ da Revenda": "11.222.333/0001-8"}),
    ("cnpj_has_14_digits", {"CNPJ da Revenda": "A1.222.333/0001-81"}),
    ("cnpj_has_14_digits", {"CNPJ da Revenda": ""}),
    ("cnpj_check_digits_valid", {"CNPJ da Revenda": "11.222.333/0001-80"}),
    ("cnpj_check_digits_valid", {"CNPJ da Revenda": "00.000.000/0000-00"}),
    ("product_is_known", {"Produto": "QUEROSENE"}),
    ("product_is_known", {"Produto": "Gasolina"}),
    ("product_is_known", {"Produto": ""}),
    ("unit_matches_product", {"Unidade de Medida": "R$ / m³"}),
    ("unit_matches_product", {"Unidade de Medida": "R$ / galao"}),
    ("unit_matches_product", {"Unidade de Medida": ""}),
    ("brand_present", {"Bandeira": "\u00a0"}),
    ("collected_on_is_a_date", {"Data da Coleta": "31/09/2026"}),
    ("collected_on_is_a_date", {"Data da Coleta": "2026-10-05"}),
    ("collected_on_is_a_date", {"Data da Coleta": ""}),
    ("collected_on_within_file_period", {"Data da Coleta": "30/09/2026"}),
    ("sale_price_is_a_number", {"Valor de Venda": "5.99"}),
    ("sale_price_is_a_number", {"Valor de Venda": "5,9999"}),
    ("sale_price_is_a_number", {"Valor de Venda": ""}),
    ("sale_price_is_a_number", {"Valor de Venda": "9" * 400}),  # would overflow a float
    ("sale_price_within_bounds", {"Valor de Venda": "599"}),
    ("sale_price_within_bounds", {"Valor de Venda": "0"}),
    ("purchase_price_is_a_number_or_empty", {"Valor de Compra": "n/d"}),
    ("purchase_price_is_a_number_or_empty", {"Valor de Compra": "4,12345"}),
    ("purchase_price_is_a_number_or_empty", {"Valor de Compra": "9" * 400}),
]


@pytest.mark.parametrize("alone", [False, True], ids=["after a valid row", "only row of the file"])
@pytest.mark.parametrize(("rule_id", "change"), ONE_BROKEN_RULE)
def test_row_breaking_one_rule_goes_to_quarantine_under_that_rule_alone(
    tmp_path, rule_id, change, alone
):
    """A value that cannot be read is counted once: the rules that depend on it pass."""
    rows = [VALID | change] if alone else [VALID, VALID | change]
    outcome = run(tmp_path, *rows)

    assert len(outcome.staged) == len(rows) - 1
    assert [row["_rule_ids"] for row in outcome.quarantine] == [[rule_id]]
    assert outcome.errors == {rule_id: 1}


def test_row_breaking_several_rules_carries_all_of_them(tmp_path):
    broken = VALID | {
        "Estado - Sigla": "XX",
        "CNPJ da Revenda": "11.222.333/0001-80",
        "Data da Coleta": "amanha",
        "Valor de Venda": "barato",
        "Bandeira": "",
    }
    outcome = run(tmp_path, broken)

    assert outcome.staged == []
    assert outcome.quarantine[0]["_rule_ids"] == [
        "state_is_known",
        "cnpj_check_digits_valid",
        "brand_present",
        "collected_on_is_a_date",
        "sale_price_is_a_number",
    ]
    assert outcome.errors == dict.fromkeys(outcome.quarantine[0]["_rule_ids"], 1)


def test_blank_rows_carry_only_row_not_blank(tmp_path):
    empty = dict.fromkeys(VALID, "")
    spaces = dict.fromkeys(VALID, " \u00a0 ")
    # The second empty row repeats the first, and [] is an empty line: no field at all.
    outcome = run(tmp_path, VALID, empty, empty, spaces, [])

    assert numbers(outcome.staged) == [1]
    assert [row["_rule_ids"] for row in outcome.quarantine] == [["row_not_blank"]] * 4
    assert outcome.errors == {"row_not_blank": 4}


def test_file_with_nothing_but_blank_rows_stages_nothing(tmp_path):
    empty = dict.fromkeys(VALID, "")
    outcome = run(tmp_path, empty, empty)

    assert outcome.staged == []
    assert outcome.errors == {"row_not_blank": 2}


def test_row_with_one_field_too_many_goes_to_quarantine_with_the_extra_field(tmp_path):
    outcome = run(tmp_path, [*VALID.values(), "SOBRA"])

    assert outcome.staged == []
    assert outcome.quarantine[0]["_rule_ids"] == ["field_count_is_16"]
    assert outcome.quarantine[0]["_field_count"] == 17
    assert outcome.quarantine[0]["_extra_fields"] == '["SOBRA"]'


def test_truncated_row_goes_to_quarantine_with_the_missing_fields_as_null(tmp_path):
    outcome = run(tmp_path, list(VALID.values())[:11])  # cut after the product

    quarantined = outcome.quarantine[0]
    assert quarantined["_rule_ids"] == [
        "field_count_is_16",
        "unit_matches_product",
        "brand_present",
        "collected_on_is_a_date",
        "sale_price_is_a_number",
    ]
    assert quarantined["Produto"] == "GASOLINA"
    assert quarantined["Data da Coleta"] is None
    assert quarantined["_field_count"] == 11


@pytest.mark.parametrize(
    ("kind", "period", "day", "inside"),
    [
        ("semester", "2026-S1", "01/01/2026", True),
        ("semester", "2026-S1", "30/06/2026", True),
        ("semester", "2026-S1", "31/12/2025", False),
        ("semester", "2026-S1", "01/07/2026", False),
        ("semester", "2025-S2", "01/07/2025", True),
        ("semester", "2025-S2", "31/12/2025", True),
        ("semester", "2025-S2", "30/06/2025", False),
        ("semester", "2025-S2", "01/01/2026", False),
        ("monthly", "2024-02", "01/02/2024", True),
        ("monthly", "2024-02", "29/02/2024", True),
        ("monthly", "2024-02", "31/01/2024", False),
        ("monthly", "2024-02", "01/03/2024", False),
        ("last_four_weeks", "latest", "01/01/2020", True),  # a rolling file has no period
    ],
)
def test_collection_date_must_fall_inside_the_period_of_the_file(
    tmp_path, kind, period, day, inside
):
    outcome = run(tmp_path, VALID | {"Data da Coleta": day}, kind=kind, period=period)

    assert len(outcome.staged) == int(inside)
    assert outcome.errors == ({} if inside else {"collected_on_within_file_period": 1})


@pytest.mark.parametrize(
    ("kind", "period"), [("weekly", "2026-W40"), ("semester", "2026-S3"), ("monthly", "2026-13")]
)
def test_file_whose_kind_or_period_is_not_understood_is_refused(tmp_path, kind, period):
    with pytest.raises(ValueError, match=period):
        stage(tmp_path, VALID, kind=kind, period=period)


def test_row_dated_after_the_download_day_goes_to_quarantine(tmp_path):
    same_day = VALID | {"Data da Coleta": "20/11/2026"}
    day_after = VALID | {"Data da Coleta": "21/11/2026"}
    outcome = run(tmp_path, same_day, day_after, kind="last_four_weeks", period="latest")

    assert numbers(outcome.staged) == [1]
    assert outcome.quarantine[0]["_rule_ids"] == ["collected_on_not_after_collection"]


@pytest.mark.parametrize(
    ("product", "lowest", "below", "highest", "above"),
    [
        (VALID, "1,00", "0,99", "15,00", "15,01"),
        (VALID | {"Produto": "GASOLINA ADITIVADA"}, "1,00", "0,999", "15,00", "15,001"),
        (VALID | {"Produto": "DIESEL"}, "0,80", "0,79", "15,00", "15,01"),
        (VALID | {"Produto": "DIESEL S10"}, "0,80", "0,79", "15,00", "15,01"),
        (ETHANOL, "0,30", "0,29", "12,00", "12,01"),
        (CNG, "0,30", "0,29", "12,00", "12,01"),
        (LPG, "10,00", "9,99", "300,00", "300,01"),
    ],
)
def test_sale_price_bounds_are_inclusive_and_set_per_product(
    tmp_path, product, lowest, below, highest, above
):
    days = ("01/10/2026", "02/10/2026", "15/10/2026", "16/10/2026")
    rows = [
        product | {"Data da Coleta": day, "Valor de Venda": price}
        for day, price in zip(days, (lowest, below, highest, above), strict=True)
    ]
    outcome = run(tmp_path, *rows)

    assert numbers(outcome.staged) == [1, 3]
    assert outcome.errors == {"sale_price_within_bounds": 2}


def test_every_known_product_has_price_bounds():
    assert set(anp.SALE_PRICE_BOUNDS) == set(PRODUCT_BY_ID)


def test_repeated_row_is_quarantined_and_its_first_occurrence_is_kept(tmp_path):
    outcome = run(tmp_path, VALID, ETHANOL, VALID, VALID)

    assert numbers(outcome.staged) == [1, 2]
    assert numbers(outcome.quarantine) == [3, 4]
    assert outcome.errors == {"not_an_exact_duplicate": 2}
    assert outcome.warnings == {}  # the copies are set aside before keys are compared


def test_rows_that_differ_only_in_raw_whitespace_are_not_exact_duplicates(tmp_path):
    outcome = run(tmp_path, VALID, VALID | {"Revenda": "POSTO UM LTDA "})

    assert outcome.errors == {}
    assert [row["station_name"] for row in outcome.staged] == ["POSTO UM LTDA"] * 2
    assert outcome.warnings == {"key_unique_in_file": 2}


# --- warning rules -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cep", "postal_code"),
    [
        ("01000-000", "01000000"),
        ("01000000", "01000000"),
        ("00000-000", None),
        ("", None),
        ("1000-000", None),
        ("01000-00A", None),
    ],
)
def test_postal_code_that_is_not_a_cep_becomes_null_with_a_warning(tmp_path, cep, postal_code):
    outcome = run(tmp_path, VALID | {"Cep": cep})

    assert outcome.staged[0]["postal_code"] == postal_code
    assert outcome.staged[0]["_warnings"] == ([] if postal_code else ["postal_code_is_valid"])
    assert outcome.warnings == ({} if postal_code else {"postal_code_is_valid": 1})


def test_purchase_price_above_the_sale_price_is_kept_with_a_warning(tmp_path):
    days = ("05/10/2026", "06/10/2026", "07/10/2026")
    rows = [
        VALID | {"Data da Coleta": day, "Valor de Compra": purchase}
        for day, purchase in zip(days, ("5,98", "5,99", "6,0001"), strict=True)
    ]
    outcome = run(tmp_path, *rows)

    assert [row["purchase_price"] for row in outcome.staged] == [5.98, 5.99, 6.0001]
    assert [row["_warnings"] for row in outcome.staged] == [
        [],
        [],
        ["purchase_price_not_above_sale_price"],
    ]


def test_price_far_from_the_median_of_its_product_and_week_gets_a_warning(tmp_path):
    """Sunday 04/10/2026 to Saturday 10/10/2026 is one survey week; the median is 6,00."""
    prices_by_day = {
        "04/10/2026": "12,01",  # the Sunday opens the week: above twice the median
        "05/10/2026": "6,00",
        "06/10/2026": "6,00",
        "07/10/2026": "6,00",
        "08/10/2026": "2,99",  # below half the median
        "09/10/2026": "3,00",  # exactly half: still near
        "10/10/2026": "12,00",  # exactly twice: still near
        "11/10/2026": "2,99",  # the next Sunday is another week, where it is the median
    }
    rows = [
        VALID | {"Data da Coleta": day, "Valor de Venda": price}
        for day, price in prices_by_day.items()
    ]
    other_product = ETHANOL | {"Data da Coleta": "08/10/2026", "Valor de Venda": "2,99"}
    outcome = run(tmp_path, *rows, other_product)

    flagged = [
        row["collected_on"].day
        for row in outcome.staged
        if row["_warnings"] == ["sale_price_near_weekly_median"]
    ]
    assert flagged == [4, 8]
    assert outcome.warnings == {"sale_price_near_weekly_median": 2}
    assert outcome.staged[-1]["product"] == "ETANOL"
    assert outcome.staged[-1]["_warnings"] == []


def test_two_prices_for_the_same_station_product_and_day_are_kept_with_a_warning(tmp_path):
    outcome = run(tmp_path, VALID, VALID | {"Valor de Venda": "6,09"}, ETHANOL)

    assert [row["sale_price"] for row in outcome.staged] == [5.99, 6.09, 3.99]
    assert [row["_warnings"] for row in outcome.staged] == [
        ["key_unique_in_file"],
        ["key_unique_in_file"],
        [],
    ]


def test_row_can_carry_several_warnings(tmp_path):
    outcome = run(tmp_path, VALID | {"Cep": "00000-000", "Valor de Compra": "7,00"})

    assert outcome.staged[0]["_warnings"] == [
        "postal_code_is_valid",
        "purchase_price_not_above_sale_price",
    ]


def test_warnings_are_counted_on_staged_rows_only(tmp_path):
    rejected = VALID | {"Cep": "00000-000", "Valor de Venda": "599"}
    outcome = run(tmp_path, VALID, rejected)

    assert numbers(outcome.quarantine) == [2]
    assert outcome.warnings == {}


# --- quarantine ----------------------------------------------------------------------------------


def test_quarantine_keeps_the_row_exactly_as_it_was_in_raw(tmp_path):
    untidy = VALID | {
        "Revenda": "  POSTO   UM\u00a0LTDA ",
        "Complemento": "LOTE: 04;",
        "Valor de Venda": " 5.99",
    }
    outcome = run(tmp_path, untidy, kind="semester", period="2026-S2")

    quarantined = outcome.quarantine[0]
    assert {column: quarantined[column] for column in anp.SOURCE_COLUMNS} == untidy
    assert quarantined["CNPJ da Revenda"] == " 11.222.333/0001-81"  # padding and mask untouched
    del quarantined["_rule_ids"]
    assert {key: value for key, value in quarantined.items() if key.startswith("_")} == {
        "_field_count": 16,
        "_extra_fields": None,
        "_source_kind": "semester",
        "_source_period": "2026-S2",
        "_source_url": URL,
        "_source_sha256": SHA256,
        "_source_row_number": 1,
        "_ingested_at": COLLECTED_AT,
        "_quarantined_at": NOW,
    }


def test_every_raw_row_ends_up_in_exactly_one_of_the_two_tables(tmp_path):
    rows = [
        VALID,
        VALID | {"Valor de Venda": "abc"},
        ETHANOL,
        dict.fromkeys(VALID, ""),
        VALID,
        LPG | {"Cep": "00000-000"},
    ]
    outcome = run(tmp_path, *rows)

    assert numbers(outcome.staged) == [1, 3, 6]
    assert numbers(outcome.quarantine) == [2, 4, 5]


# --- schema drift --------------------------------------------------------------------------------


def drifted(tmp_path: Path, header: list[str]) -> pd.DataFrame:
    return raw_frame(tmp_path, ["x"] * len(header), header=header)


def test_file_without_a_source_column_is_refused(tmp_path):
    raw = drifted(tmp_path, [column for column in anp.SOURCE_COLUMNS if column != "Bandeira"])
    with pytest.raises(SchemaDriftError, match=r"missing \['Bandeira'\], unexpected \[\]"):
        anp.stage(raw, context(), now=lambda: NOW)


def test_file_with_a_renamed_column_is_refused(tmp_path):
    header = ["Preco" if column == "Valor de Venda" else column for column in anp.SOURCE_COLUMNS]
    with pytest.raises(SchemaDriftError, match=r"missing \['Valor de Venda'\].*\['Preco'\]"):
        anp.stage(drifted(tmp_path, header), context(), now=lambda: NOW)


def test_file_with_a_new_column_is_refused(tmp_path):
    raw = drifted(tmp_path, [*anp.SOURCE_COLUMNS, "Latitude"])
    with pytest.raises(SchemaDriftError, match=r"missing \[\], unexpected \['Latitude'\]"):
        anp.stage(raw, context(), now=lambda: NOW)


# --- the contract --------------------------------------------------------------------------------

TEXT = pd.StringDtype(na_value=float("nan"))
TIMESTAMP = pd.DatetimeTZDtype("us", "UTC")
ID_LIST = pd.ArrowDtype(pa.list_(pa.string()))
STAGED_DTYPES = {
    **dict.fromkeys(anp.STAGED_ARROW_SCHEMA.names, TEXT),
    "collected_on": pd.ArrowDtype(pa.date32()),
    "sale_price": "float64",
    "purchase_price": "float64",
    "_source_row_number": "int64",
    "_ingested_at": TIMESTAMP,
    "_warnings": ID_LIST,
}
QUARANTINE_DTYPES = {
    **dict.fromkeys(anp.QUARANTINE_ARROW_SCHEMA.names, TEXT),
    "_field_count": "int32",
    "_source_row_number": "int64",
    "_ingested_at": TIMESTAMP,
    "_rule_ids": ID_LIST,
    "_quarantined_at": TIMESTAMP,
}
MIXED_FILE = (VALID, VALID | {"Valor de Venda": "abc"}, LPG | {"Cep": "00000-000"})


@pytest.mark.parametrize("rows", [MIXED_FILE, ()], ids=["rows", "empty file"])
def test_frames_have_the_columns_and_types_of_the_arrow_schemas(tmp_path, rows):
    frames = stage(tmp_path, *rows)

    assert frames.staged.dtypes.to_dict() == STAGED_DTYPES
    assert frames.quarantine.dtypes.to_dict() == QUARANTINE_DTYPES
    assert list(frames.staged.columns) == anp.STAGED_ARROW_SCHEMA.names
    assert list(frames.quarantine.columns) == anp.QUARANTINE_ARROW_SCHEMA.names


@pytest.mark.parametrize("rows", [MIXED_FILE, ()], ids=["rows", "empty file"])
def test_parquet_files_have_exactly_the_published_schemas(tmp_path, rows):
    frames = stage(tmp_path, *rows)
    write_parquet(frames.staged, tmp_path / "staged.parquet", anp.STAGED_ARROW_SCHEMA)
    write_parquet(frames.quarantine, tmp_path / "quarantine.parquet", anp.QUARANTINE_ARROW_SCHEMA)

    staged = pq.read_table(tmp_path / "staged.parquet")
    quarantine = pq.read_table(tmp_path / "quarantine.parquet")
    assert staged.schema.equals(anp.STAGED_ARROW_SCHEMA)
    assert quarantine.schema.equals(anp.QUARANTINE_ARROW_SCHEMA)
    assert staged.num_rows == len(frames.staged)
    assert staged.column("collected_on").to_pylist() == frames.staged["collected_on"].tolist()
    assert staged.column("sale_price").to_pylist() == frames.staged["sale_price"].tolist()
    assert quarantine.column("_rule_ids").to_pylist() == frames.quarantine["_rule_ids"].tolist()


def test_empty_file_gives_two_empty_tables_and_zero_for_every_rule(tmp_path):
    frames = stage(tmp_path)

    assert len(frames.staged) == len(frames.quarantine) == 0
    assert frames.error_counts == {
        rule.id: 0 for rule in anp.RULES if rule.severity is Severity.ERROR
    }
    assert frames.warning_counts == {
        rule.id: 0 for rule in anp.RULES if rule.severity is Severity.WARNING
    }


def test_staging_the_same_file_twice_gives_the_same_tables(tmp_path):
    raw = raw_frame(tmp_path, *MIXED_FILE, VALID, dict.fromkeys(VALID, ""))
    first = anp.stage(raw, context(), now=lambda: NOW)
    second = anp.stage(raw, context(), now=lambda: NOW)

    pd.testing.assert_frame_equal(first.staged, second.staged)
    pd.testing.assert_frame_equal(first.quarantine, second.quarantine)
    assert first.error_counts == second.error_counts
    assert first.warning_counts == second.warning_counts


@pytest.mark.parametrize(
    "tamper",
    [
        lambda staged: staged.assign(extra=1),
        lambda staged: staged.drop(columns="brand"),
        lambda staged: staged[staged.columns[::-1]],
        lambda staged: staged.assign(station_cnpj=None),
        lambda staged: staged.assign(station_cnpj="11.222.333/0001-81"),
        lambda staged: staged.assign(postal_code="01000-000"),
        lambda staged: staged.assign(product="QUEROSENE"),
        lambda staged: staged.assign(state_code="XX"),
        lambda staged: staged.assign(unit="R$ / litro"),
        lambda staged: staged.assign(sale_price=0.0),
        lambda staged: staged.assign(sale_price=float("nan")),
        lambda staged: staged.assign(sale_price="5,99"),
        lambda staged: staged.assign(collected_on=pd.Timestamp("2026-10-05")),
        lambda staged: staged.assign(_source_kind="weekly"),
    ],
    ids=[
        "extra column",
        "missing column",
        "column order",
        "null key",
        "masked cnpj",
        "masked postal code",
        "unknown product",
        "unknown state",
        "source unit",
        "zero price",
        "null price",
        "price as text",
        "timestamp for a date",
        "unknown kind",
    ],
)
def test_contract_refuses_a_table_that_is_not_the_published_one(tmp_path, tamper):
    staged = stage(tmp_path, VALID).staged
    anp.STAGED_SCHEMA.validate(staged)  # the real one passes
    with pytest.raises((SchemaError, SchemaErrors)):
        anp.STAGED_SCHEMA.validate(tamper(staged))


def test_contract_has_no_check_that_runs_python_row_by_row():
    checks = [check for column in anp.STAGED_SCHEMA.columns.values() for check in column.checks]
    assert checks
    assert not any(check.element_wise for check in checks)


# --- the rule catalogue --------------------------------------------------------------------------


def test_rule_ids_and_severities_are_the_published_contract():
    assert [(rule.id, rule.severity.value) for rule in anp.RULES] == [
        ("row_not_blank", "error"),
        ("field_count_is_16", "error"),
        ("state_is_known", "error"),
        ("region_matches_state", "error"),
        ("municipality_present", "error"),
        ("station_name_present", "error"),
        ("cnpj_has_14_digits", "error"),
        ("cnpj_check_digits_valid", "error"),
        ("product_is_known", "error"),
        ("unit_matches_product", "error"),
        ("brand_present", "error"),
        ("collected_on_is_a_date", "error"),
        ("collected_on_within_file_period", "error"),
        ("collected_on_not_after_collection", "error"),
        ("sale_price_is_a_number", "error"),
        ("sale_price_within_bounds", "error"),
        ("purchase_price_is_a_number_or_empty", "error"),
        ("not_an_exact_duplicate", "error"),
        ("postal_code_is_valid", "warning"),
        ("purchase_price_not_above_sale_price", "warning"),
        ("sale_price_near_weekly_median", "warning"),
        ("key_unique_in_file", "warning"),
    ]


def test_every_rule_is_described_in_english_and_portuguese():
    for rule in anp.RULES:
        assert rule.description.endswith("."), rule.id
        assert rule.description_pt.endswith("."), rule.id
        assert rule.description != rule.description_pt, rule.id
