"""Whitespace normalisation and the date and number readers."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from fuel_price_lakehouse.stage import text


def column(*values: str | None) -> pd.Series:
    return pd.Series(list(values), dtype="str")


def test_clean_strips_and_collapses_whitespace():
    cleaned = text.clean(column("  POSTO   BRASIL ", "RUA\tA\r\n 10", "S/N"))
    assert cleaned.tolist() == ["POSTO BRASIL", "RUA A 10", "S/N"]


def test_clean_treats_the_no_break_space_as_a_space():
    cleaned = text.clean(column("AV\u00a0\u00a0BRASIL", "\u00a0KM 4\u00a0"))
    assert cleaned.tolist() == ["AV BRASIL", "KM 4"]


def test_clean_turns_text_with_nothing_in_it_into_missing():
    cleaned = text.clean(column("", "   ", "\u00a0", None, "x"))
    assert cleaned.isna().tolist() == [True, True, True, True, False]


def test_clean_keeps_everything_else_as_published():
    cleaned = text.clean(column("Posto São João", "SANT¿ANA", "LOTE: 04;"))
    assert cleaned.tolist() == ["Posto São João", "SANT¿ANA", "LOTE: 04;"]


def test_clean_keeps_the_index_and_the_text_dtype():
    values = pd.Series(["a ", None], index=[7, 3], dtype="str")
    cleaned = text.clean(values)
    assert cleaned.index.tolist() == [7, 3]
    assert cleaned.dtype == values.dtype


@pytest.mark.parametrize("values", [column(), column(None, None)])
def test_clean_of_a_column_without_text_is_still_a_text_column(values):
    cleaned = text.clean(values)
    assert cleaned.dtype == values.dtype
    assert cleaned.isna().all()
    assert len(cleaned) == len(values)


def test_map_distinct_calls_the_function_once_per_distinct_value():
    seen: list[str] = []

    def shout(value: str) -> str:
        seen.append(value)
        return value.upper()

    mapped = text.map_distinct(column("a", "b", "a", None, "a"), shout)

    assert mapped[:3].tolist() == ["A", "B", "A"]
    assert mapped.isna().tolist() == [False, False, False, True, False]
    assert sorted(seen) == ["a", "b"]


def test_dates_are_read_as_day_month_year():
    parsed = text.parse_date(column("05/10/2026", "31/12/2020", "29/02/2024"))
    assert parsed.dt.date.tolist() == [date(2026, 10, 5), date(2020, 12, 31), date(2024, 2, 29)]


@pytest.mark.parametrize(
    "value",
    [
        "31/02/2026",  # no such day
        "29/02/2023",  # not a leap year
        "05/13/2026",  # month and day swapped
        "00/00/0000",
        "5/10/2026",
        "05/10/26",
        "2026-10-05",
        "05-10-2026",
        "05/10/2026 00:00",
        "",
        None,
    ],
)
def test_anything_that_is_not_a_real_dd_mm_yyyy_date_is_missing(value):
    assert text.parse_date(column(value)).isna().all()


def prices(*values: str | None, max_decimals: int = 3) -> pd.Series:
    """The values read the way ANP writes a price: unsigned, with a decimal comma."""
    return text.parse_decimal(column(*values), separator=",", max_decimals=max_decimals)


def test_numbers_are_read_with_a_decimal_comma():
    assert prices("5,99", "5,999", "6", "0,5", "110,00").tolist() == [5.99, 5.999, 6.0, 0.5, 110.0]


def test_number_of_decimals_is_limited_by_the_caller():
    assert prices("1,5431", max_decimals=3).isna().all()
    assert prices("1,5431", max_decimals=4).tolist() == [1.5431]


@pytest.mark.parametrize(
    "value",
    [
        "5.99",
        "1.234,56",
        "-5,99",
        "+5,99",
        "R$ 5,99",
        "5,",
        ",99",
        "5,9 9",
        "5,99,1",
        "1e3",
        "",
        None,
    ],
)
def test_anything_that_is_not_an_unsigned_decimal_is_missing(value):
    assert prices(value).isna().all()


def test_decimal_mark_and_sign_are_what_the_source_writes():
    rates = column("5.4372", "-0.32", "7", "5,43")
    signed = text.parse_decimal(rates, separator=".", signed=True)
    unsigned = text.parse_decimal(rates, separator=".")

    assert signed[:3].tolist() == [5.4372, -0.32, 7.0]
    assert signed.isna().tolist() == [False, False, False, True]
    assert unsigned.isna().tolist() == [False, True, False, True]


def test_decimals_are_not_limited_unless_the_caller_says_so():
    assert text.parse_decimal(column("0.123456789"), separator=".").tolist() == [0.123456789]


@pytest.mark.parametrize("alone", [True, False], ids=["only value", "among other values"])
@pytest.mark.parametrize("digits", [16, 25, 309, 400])
def test_run_of_digits_too_long_for_an_exact_number_is_missing(digits, alone):
    """Read as a number it would be an absurd price, infinity, or an overflow that stops the run."""
    values = ["9" * digits] if alone else ["5,99", "9" * digits, "6"]
    parsed = prices(*values)
    assert parsed.isna().tolist() == ([True] if alone else [False, True, False])


def test_longest_whole_number_read_is_exact():
    assert prices("999999999999999").tolist() == [999_999_999_999_999.0]


@pytest.mark.parametrize("values", [column(), column(None), column("x")])
def test_readers_return_typed_columns_even_without_a_valid_value(values):
    assert text.parse_date(values).dtype == "datetime64[s]"
    assert text.parse_decimal(values, separator=",", max_decimals=3).dtype == "float64"
