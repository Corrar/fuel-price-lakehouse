"""Settings shared by every stage of the pipeline."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

DEFAULT_SINCE = date(2020, 1, 1)
"""First month of history the pipeline covers unless told otherwise.

2020 keeps the pandemic and the 2022 price shock in view while a full rebuild
from the sources still fits in a routine CI job (about 600 MB of downloads;
the whole history since 2004 is 4.8 GB). See ``docs/sources.md``, section 1.3.
"""

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_month(text: str) -> date:
    """``YYYY-MM`` to the first day of that month."""
    try:
        year, month = text.split("-")
        return date(int(year), int(month), 1)
    except ValueError:
        raise ValueError(f"expected YYYY-MM, got {text!r}") from None
