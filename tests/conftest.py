from pathlib import Path

import pytest

from fuel_price_lakehouse.http import RetryPolicy

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def landing_html() -> str:
    return (FIXTURES / "anp_landing.html").read_text(encoding="utf-8")


@pytest.fixture
def policy() -> RetryPolicy:
    return RetryPolicy.no_wait()


def no_sleep(_seconds: float) -> None:
    """Stand-in for time.sleep so tests never wait."""
