from datetime import date

from fuel_price_lakehouse.anp.discovery import Dataset, Grain, parse_landing_page, select

SHPC = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/shpc"


def by_name(catalogue):
    return {f.filename: f for f in catalogue.files}


def test_every_data_link_is_found_and_the_pdf_is_ignored(landing_html):
    catalogue = parse_landing_page(landing_html)

    assert len(catalogue.files) == 19
    assert not any(f.filename.endswith(".pdf") for f in catalogue.files)


def test_links_that_cannot_be_classified_are_reported_not_dropped(landing_html):
    assert parse_landing_page(landing_html).unclassified == (f"{SHPC}/outros/arquivo-novo.csv",)


def test_semester_period_comes_from_the_link_text_when_the_name_has_none(landing_html):
    files = by_name(parse_landing_page(landing_html))

    odd = files["precos-semestrais-ca.zip"]
    assert (odd.dataset, odd.grain, odd.period) == (Dataset.AUTOMOTIVE, Grain.SEMESTER, "2022-S1")
    assert (odd.start, odd.end) == (date(2022, 1, 1), date(2022, 6, 30))
    assert files["precos-semestrais-glp2021-01.csv"].period == "2021-S1"


def test_link_text_broken_across_lines_still_parses(landing_html):
    second_half = by_name(parse_landing_page(landing_html))["ca-2025-02.zip"]

    assert second_half.period == "2025-S2"
    assert (second_half.start, second_half.end) == (date(2025, 7, 1), date(2025, 12, 31))


def test_period_falls_back_to_the_url_when_the_text_is_useless(landing_html):
    files = by_name(parse_landing_page(landing_html))

    assert files["glp-2005-02.csv"].period == "2005-S2"
    assert files["precos-glp-12.csv"].period == "2025-12"


def test_relative_links_are_resolved_against_the_page(landing_html):
    assert by_name(parse_landing_page(landing_html))["glp-2005-02.csv"].url == (
        f"{SHPC}/dsas/glp/glp-2005-02.csv"
    )


def test_irregular_monthly_names_are_classified(landing_html):
    files = by_name(parse_landing_page(landing_html))

    no_extension = files["04-dados-abertos-precos-gasolina-etanol"]
    assert (no_extension.dataset, no_extension.period) == (Dataset.GASOLINE_ETHANOL, "2026-04")
    assert files["02-cados-abertos-preco-gasolina-etanol.csv"].period == "2026-02"
    assert files["06-dados-abertos-precos-2026-06-diesel-gnv.csv"].dataset is Dataset.DIESEL_CNG
    accented = files["precos-gasolina-etanol-03.csv"]  # "Março de 2023"
    assert (accented.start, accented.end) == (date(2023, 3, 1), date(2023, 3, 31))


def test_rolling_files_have_no_fixed_period(landing_html):
    rolling = [f for f in parse_landing_page(landing_html).files if f.grain is Grain.LAST_4_WEEKS]

    assert {f.dataset for f in rolling} == {
        Dataset.DIESEL_CNG,
        Dataset.GASOLINE_ETHANOL,
        Dataset.LPG,
    }
    assert all(f.start is None and f.period == "last-4-weeks" for f in rolling)


def test_select_prefers_semesters_and_adds_only_the_months_after_them(landing_html):
    chosen = select(parse_landing_page(landing_html).files, since=date(2022, 1, 1))

    assert [(f.dataset.value, f.period) for f in chosen] == [
        ("automotive", "2022-S1"),
        ("automotive", "2025-S2"),
        ("automotive", "2026-S1"),
        ("lpg", "2026-S1"),
        ("diesel_cng", "2026-07"),
        ("diesel_cng", "2026-10"),
        ("gasoline_ethanol", "2026-10"),
        ("lpg", "2026-10"),
        ("diesel_cng", "last-4-weeks"),
        ("gasoline_ethanol", "last-4-weeks"),
        ("lpg", "last-4-weeks"),
    ]


def test_select_keeps_the_semester_that_contains_the_start_month(landing_html):
    chosen = select(parse_landing_page(landing_html).files, since=date(2026, 3, 1))

    assert ("automotive", "2026-S1") in [(f.dataset.value, f.period) for f in chosen]
    assert ("automotive", "2025-S2") not in [(f.dataset.value, f.period) for f in chosen]


def test_select_uses_monthly_files_when_a_family_has_no_semester_file():
    catalogue = parse_landing_page(
        f'<a href="{SHPC}/dsan/2026/10-glp.csv">Outubro de 2026</a>'
        f'<a href="{SHPC}/dsan/2026/09-glp.csv">Setembro de 2026</a>'
    )

    chosen = select(catalogue.files, since=date(2026, 10, 1))

    assert [f.period for f in chosen] == ["2026-10"]
