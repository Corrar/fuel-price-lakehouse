from datetime import date

import pytest

from fuel_price_lakehouse import anp
from fuel_price_lakehouse.http import SourceError

from .conftest import FILES, PAGE


@pytest.fixture
def discovery() -> anp.Discovery:
    return anp.discover(PAGE.format(files=FILES, updated="23/11/2026"))


def by_url(discovery: anp.Discovery, suffix: str) -> anp.AnpFile:
    return next(file for file in discovery.files if file.url.endswith(suffix))


def test_finds_only_data_files(discovery):
    urls = [file.url for file in discovery.files]
    assert len(urls) == 11
    assert all("/arquivos/shpc/" in url and not url.endswith(".pdf") for url in urls)


def test_period_comes_from_the_link_text_not_the_file_name(discovery):
    # 2022-01 is published as "precos-semestrais-ca.zip": no date in the name at all.
    odd = by_url(discovery, "precos-semestrais-ca.zip")
    assert (odd.kind, odd.fuel_group, odd.period) == ("semester", "automotive", "2022-S1")
    assert (odd.period_start, odd.period_end) == (date(2022, 1, 1), date(2022, 6, 30))


def test_semester_label_variants(discovery):
    # The page writes both "1º" and "2°".
    assert by_url(discovery, "ca-2025-02.zip").period == "2025-S2"
    assert by_url(discovery, "ca-2025-02.zip").period_end == date(2025, 12, 31)


@pytest.mark.parametrize(
    ("suffix", "group", "period"),
    [
        ("10-diesel-gnv.csv", "diesel_cng", "2026-10"),
        ("03-dados-abertos-precos-diesel-gnv.csv", "diesel_cng", "2026-03"),
        ("04-dados-abertos-precos-gasolina-etanol", "gasoline_ethanol", "2026-04"),  # no extension
        ("02-cados-abertos-preco-gasolina-etanol.csv", "gasoline_ethanol", "2026-02"),  # typo
        ("dsan/2026/10-glp.csv", "lpg", "2026-10"),
    ],
)
def test_irregular_monthly_names(discovery, suffix, group, period):
    file = by_url(discovery, suffix)
    assert (file.kind, file.fuel_group, file.period) == ("monthly", group, period)


def test_month_end_is_the_last_day(discovery):
    assert by_url(discovery, "02-cados-abertos-preco-gasolina-etanol.csv").period_end == date(
        2026, 2, 28
    )


def test_last_four_weeks_carries_the_section_update_date(discovery):
    file = by_url(discovery, "ultimas-4-semanas-gasolina-etanol.csv")
    assert (file.kind, file.fuel_group, file.period) == (
        "last_four_weeks",
        "gasoline_ethanol",
        "latest",
    )
    assert file.published_hint == "23/11/2026"
    # The page-level "Atualizado em" and the metadata link date are not section hints.
    assert by_url(discovery, "ca-2026-01.zip").published_hint == ""


def test_unclassifiable_link_is_reported_not_guessed(discovery):
    assert discovery.unrecognised == [f"{FILES}/dsan/2026/novo-arquivo.csv"]


def test_page_without_files_is_an_error():
    with pytest.raises(SourceError, match="no data files"):
        anp.discover("<html><body><h2>Em manutenção</h2></body></html>")


def test_select_from_recent_month_takes_uncovered_months_and_four_weeks(discovery):
    chosen = anp.select(discovery.files, date(2026, 7, 1))
    assert [(f.kind, f.fuel_group, f.period) for f in chosen] == [
        ("monthly", "diesel_cng", "2026-10"),
        ("monthly", "gasoline_ethanol", "2026-10"),
        ("monthly", "lpg", "2026-10"),
        ("last_four_weeks", "gasoline_ethanol", "latest"),
    ]


def test_select_prefers_the_semester_file_over_the_months_it_covers(discovery):
    chosen = anp.select(discovery.files, date(2026, 1, 1))
    periods = [(f.kind, f.fuel_group, f.period) for f in chosen]
    assert ("semester", "automotive", "2026-S1") in periods
    assert ("semester", "lpg", "2026-S1") in periods
    # February to April 2026 are inside the 2026-S1 semester files.
    assert not [p for p in periods if p[0] == "monthly" and p[2] < "2026-07"]
    # A semester that ended before `since` is left out.
    assert ("semester", "automotive", "2025-S2") not in periods


def test_select_orders_oldest_first(discovery):
    chosen = anp.select(discovery.files, date(2022, 1, 1))
    assert [f.period for f in chosen if f.kind == "semester"] == [
        "2022-S1",
        "2025-S2",
        "2026-S1",
        "2026-S1",
    ]
