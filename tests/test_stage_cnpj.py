"""CNPJ normalisation and check digits.

The numbers here are arithmetic examples, not stations: ``11.222.333/0001-81``
is the one usually printed in documentation.
"""

from __future__ import annotations

import pytest

from fuel_price_lakehouse.stage import cnpj


@pytest.mark.parametrize(
    "text",
    [
        "11.222.333/0001-81",
        " 11.222.333/0001-81",  # the padding ANP adds
        "11222333000181",  # bare, as in the files of the second semester of 2025
        "11222333/0001-81",
        "\u00a011.222.333/0001-81 ",
    ],
)
def test_mask_and_padding_are_removed(text):
    assert cnpj.digits(text) == "11222333000181"


def test_leading_zeros_are_kept():
    assert cnpj.digits("00.000.007/0001-03") == "00000007000103"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "11.222.333/0001-8",  # 13 digits
        "11.222.333/0001-811",  # 15 digits
        "11.222.333/0001-8X",
        "A1.222.333/0001-81",  # a letter is not punctuation: nothing is left out to make 14
        "11,222,333/0001-81",
        "١١٢٢٢٣٣٣٠٠٠١٨١",  # digits of another script are not a CNPJ
        "112223330001²1",
    ],
)
def test_text_that_is_not_14_digits_is_not_a_cnpj(text):
    assert cnpj.digits(text) is None


@pytest.mark.parametrize(
    "number",
    [
        "11222333000181",
        "12345678000195",
        "11444777000161",
        "00000007000103",  # first check digit 0 because the remainder is 0
        "00000009000100",  # first check digit 0 because the remainder is 1
        "00000002000180",  # second check digit 0 because the remainder is 0
        "00000004000170",  # second check digit 0 because the remainder is 1
    ],
)
def test_correct_check_digits_are_accepted(number):
    assert cnpj.has_valid_check_digits(number)


@pytest.mark.parametrize(
    "number",
    [
        "11222333000180",  # second check digit off by one
        "11222333000171",  # first check digit off by one
        "11222333000118",  # check digits swapped
        "12222333000181",  # one digit of the root changed
        "11222333000281",  # one digit of the branch changed
        "00000007000113",  # remainder 0 must give 0, not 11 - 0
        "11111111111111",
    ],
)
def test_wrong_check_digits_are_rejected(number):
    assert not cnpj.has_valid_check_digits(number)


def test_fourteen_zeros_are_rejected_although_the_arithmetic_holds():
    assert not cnpj.has_valid_check_digits("00000000000000")
