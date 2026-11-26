"""CNPJ, the Brazilian company registration number: the key of a fuel station.

ANP publishes it masked and padded (``" 11.222.333/0001-81"``) in most files
and as 14 bare digits in a few, so the mask is never required: it is removed
and the 14 digits that remain are the key.

Only the numeric CNPJ is accepted. The alphanumeric format Receita Federal
announced for new registrations does not occur in the files this was built on;
a row carrying one would go to quarantine under ``cnpj_has_14_digits`` and show
up in the quality report, which is the signal to extend this module.
"""

from __future__ import annotations

import re

_LENGTH = 14
_MASK = re.compile(r"[\s./-]")
_FIRST_WEIGHTS = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_SECOND_WEIGHTS = (6, *_FIRST_WEIGHTS)


def digits(text: str) -> str | None:
    """The 14 digits of a CNPJ written with or without its mask, or None.

    Only whitespace and the mask characters (``.``, ``/``, ``-``) are removed.
    Anything else, a letter for example, is not punctuation and makes the value
    something other than a CNPJ.

    >>> digits(" 11.222.333/0001-81")
    '11222333000181'
    >>> digits("11.222.333/0001-8") is None
    True
    """
    bare = _MASK.sub("", text)
    return bare if len(bare) == _LENGTH and bare.isascii() and bare.isdigit() else None


def has_valid_check_digits(cnpj: str) -> bool:
    """Whether the last two of the 14 digits are the ones the first twelve require.

    ``cnpj`` is the 14 digits returned by :func:`digits`. Each check digit is a
    modulo 11 checksum of the digits before it:

    1. multiply each digit by its weight and add the products. The weights are
       ``5 4 3 2 9 8 7 6 5 4 3 2`` for the first check digit (over the first 12
       digits) and ``6 5 4 3 2 9 8 7 6 5 4 3 2`` for the second (over the first
       13, so the first check digit takes part in the second);
    2. take the remainder of the sum divided by 11;
    3. the check digit is 0 when the remainder is below 2, else 11 minus it.

    Fourteen zeros satisfy the arithmetic but are a placeholder, not a
    registration, and are rejected.

    >>> has_valid_check_digits("11222333000181")
    True
    >>> has_valid_check_digits("11222333000180")
    False
    """
    numbers = [int(character) for character in cnpj]
    first = _check_digit(numbers[:12], _FIRST_WEIGHTS)
    second = _check_digit(numbers[:13], _SECOND_WEIGHTS)
    return any(numbers) and numbers[12:] == [first, second]


def _check_digit(numbers: list[int], weights: tuple[int, ...]) -> int:
    remainder = sum(number * weight for number, weight in zip(numbers, weights, strict=True)) % 11
    return 0 if remainder < 2 else 11 - remainder
