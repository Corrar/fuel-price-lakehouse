"""Reading the text of the raw layer: whitespace, Brazilian dates and numbers.

Every function takes a column of text and returns a column of the same length
and index. A value that is not what the function reads becomes missing rather
than a guess; the rules of each source then say what a missing value means.

Patterns spell out ``[0-9]`` because ``\\d`` also matches the digits of other
scripts in some regex engines, and those are not numbers here.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd

_DATE_TEXT = r"[0-9]{2}/[0-9]{2}/[0-9]{4}"
_DATE_FORMAT = "%d/%m/%Y"


def map_distinct(values: pd.Series, function: Callable[[str], str | None]) -> pd.Series:
    """Apply a Python function once per distinct value and spread the results back.

    The columns of a price file repeat heavily (the largest one has 529,424
    rows for 12,105 stations), which makes a plain Python function cheap where
    pandas has no vectorised equivalent. Missing stays missing.
    """
    codes, distinct = pd.factorize(values)
    results = pd.array([function(value) for value in distinct], dtype="str")
    return pd.Series(results.take(codes, allow_fill=True), index=values.index)


def clean(values: pd.Series) -> pd.Series:
    """Strip, and collapse every run of whitespace to one space; missing when nothing is left.

    Whitespace is what Python calls whitespace, which includes the no-break
    space (U+00A0) that some files carry in otherwise empty fields.

    >>> clean(pd.Series(["  POSTO   BRASIL ", "\\u00a0", None])).tolist()
    ['POSTO BRASIL', nan, nan]
    """
    return map_distinct(values, lambda value: " ".join(value.split()) or None)


def parse_date(values: pd.Series) -> pd.Series:
    """Dates written ``dd/mm/yyyy``; missing for any other shape or an impossible date.

    >>> parse_date(pd.Series(["05/10/2026", "31/02/2026", "5/10/2026"])).dt.day.tolist()
    [5.0, nan, nan]
    """
    shaped = values.where(values.str.fullmatch(_DATE_TEXT))
    # An explicit unit: a column with no valid date would otherwise infer its own.
    return pd.to_datetime(shaped, format=_DATE_FORMAT, errors="coerce").astype("datetime64[s]")


def parse_decimal(values: pd.Series, max_decimals: int) -> pd.Series:
    """Unsigned numbers with a decimal comma (``5,99``, ``6``); missing for anything else.

    A thousands separator, a sign or more decimals than the source is known to
    publish are signs of a different kind of value, so they are not read.

    >>> parse_decimal(pd.Series(["5,999", "6", "5.99", "5,9999"]), max_decimals=3).tolist()
    [5.999, 6.0, nan, nan]
    """
    shaped = values.where(values.str.fullmatch(rf"[0-9]+(,[0-9]{{1,{max_decimals}}})?"))
    return pd.to_numeric(shaped.str.replace(",", ".", regex=False)).astype("float64")
