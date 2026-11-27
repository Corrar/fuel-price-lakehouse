"""tools/validate_against_anp.py, on a small workbook shaped like ANP's weekly summary."""

from __future__ import annotations

import importlib.util
import re
import sys
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from types import ModuleType

import openpyxl
import pytest

from .test_quality_checks import LATEST_WEEK, price, write_table

TOOL = Path(__file__).parents[1] / "tools" / "validate_against_anp.py"
START, END = datetime(2026, 11, 15), datetime(2026, 11, 21)  # LATEST_WEEK, Sunday to Saturday
PRICES = [
    "PREÇO MÉDIO REVENDA",
    "DESVIO PADRÃO REVENDA",
    "PREÇO MÍNIMO REVENDA",
    "PREÇO MÁXIMO REVENDA",
    "COEF DE VARIAÇÃO REVENDA",
]
MUNICIPAL_HEADER = [
    *("DATA INICIAL", "DATA FINAL", "ESTADO", "MUNICÍPIO", "PRODUTO"),
    *("NÚMERO DE POSTOS PESQUISADOS", "UNIDADE DE MEDIDA", *PRICES),
]
NATIONAL_HEADER = [
    *("DATA INICIAL", "DATA FINAL", "BRASIL", "PRODUTO"),
    *("NÚMERO DE POSTOS PESQUISADOS", "UNIDADE DE MEDIDA", *PRICES),
]


def load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_against_anp", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up there
    spec.loader.exec_module(module)
    return module


validate = load_tool()


def workbook(path: Path, municipal: list[list], national: list[list]) -> Path:
    """A workbook with ANP's title lines, header and data rows in two sheets."""
    book = openpyxl.Workbook()
    for title, header, rows in (
        ("MUNICIPIOS", MUNICIPAL_HEADER, municipal),
        ("BRASIL", NATIONAL_HEADER, national),
    ):
        sheet = book.create_sheet(title)
        sheet.append(["AGÊNCIA NACIONAL DO PETRÓLEO, GÁS NATURAL E BIOCOMBUSTÍVEIS - ANP"])
        sheet.append([])
        sheet.append(header)
        for row in rows:
            sheet.append(row)
    book.save(path)
    return path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Three stations in São Paulo (one priced twice in the week) and one in Rio."""
    rows = [
        price(LATEST_WEEK, "SP", 0, sale_price=6.19),
        price(LATEST_WEEK, "SP", 0, sale_price=6.21, collected_on=END.date()),
        price(LATEST_WEEK, "SP", 1, sale_price=6.19),
        price(LATEST_WEEK, "SP", 2, sale_price=6.20),
        price(LATEST_WEEK, "RJ", 0, sale_price=6.50),
    ]
    write_table(tmp_path, "fuel_price", rows)
    return tmp_path


def test_published_mean_is_the_exact_mean_rounded_to_three_decimals_then_cut_to_two():
    mean = Fraction("6.199666")  # cut to two: 6.19; round half-up to two: 6.20

    assert validate.ROUNDING_RULES[validate.ANP_RULE](mean) == Decimal("6.20")
    assert validate.ROUNDING_RULES["cut to 2 decimals"](mean) == Decimal("6.19")


def test_station_priced_twice_in_the_week_counts_once_with_its_mean(root):
    cells = validate.station_prices(root, LATEST_WEEK)

    computed = validate.Computed.of(cells["SP", "MUNICIPIO", "GASOLINA"])

    assert computed.stations == 3
    assert computed.mean == (Fraction("6.20") + Fraction("6.19") + Fraction("6.20")) / 3


def test_pairs_that_agree_match_and_the_others_are_listed(root, tmp_path, capsys):
    book = workbook(
        tmp_path / "resumo.xlsx",
        municipal=[
            [
                START,
                END,
                "SÃO PAULO",
                "MUNICIPIO",
                "GASOLINA COMUM",
                3,
                "R$/l",
                6.19,
                0,
                6.19,
                6.2,
                0,
            ],
            [
                START,
                END,
                "RIO DE JANEIRO",
                "MUNICIPIO",
                "GASOLINA COMUM",
                2,
                "R$/l",
                6.4,
                0,
                6.3,
                6.5,
                0,
            ],
        ],
        national=[[START, END, "BRASIL", "GASOLINA COMUM", 4, "R$/l", 6.31, 0, 6.19, 6.5, 0]],
    )

    status = validate.main([str(book), "--root", str(root)])

    out = capsys.readouterr().out
    assert status == 1  # 1 of 2 pairs, below the default 99 %
    # São Paulo: exact mean 6.19666..., published 6.19 (6.20 with half-up rounding)
    assert re.search(r"GASOLINA +1 of 2", out)
    assert "differs ('RJ', 'MUNICIPIO', 'GASOLINA'): ours n=1" in out
    assert re.search(r"GASOLINA +4 +4 +6\.31 +6\.273 +-0\.59%", out)  # simple mean, 4 stations
    assert validate.main([str(book), "--root", str(root), "--min-match", "0.5"]) == 0


def test_workbook_of_more_than_one_week_is_refused(root, tmp_path):
    later = datetime(2026, 11, 22)
    book = workbook(
        tmp_path / "resumo.xlsx",
        municipal=[
            [
                START,
                END,
                "SÃO PAULO",
                "MUNICIPIO",
                "GASOLINA COMUM",
                3,
                "R$/l",
                6.2,
                0,
                6.2,
                6.2,
                0,
            ],
            [
                later,
                later,
                "SÃO PAULO",
                "MUNICIPIO",
                "GASOLINA COMUM",
                3,
                "R$/l",
                6.2,
                0,
                6.2,
                6.2,
                0,
            ],
        ],
        national=[],
    )

    with pytest.raises(SystemExit):
        validate.main([str(book), "--root", str(root)])
