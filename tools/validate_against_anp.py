"""Check the curated prices against ANP's own weekly summary of the same week.

    uv run python tools/validate_against_anp.py resumo_semanal_lpc_<week>.xlsx [--root data]

ANP publishes, every week, a workbook with the number of stations surveyed and
the mean, minimum and maximum price for every municipality and product
(``MUNICIPIOS`` sheet) and for Brazil (``BRASIL`` sheet). The workbooks are
listed on ANP's "Levantamento de Preços de Combustíveis" page.

ANP computes the municipal mean as a simple mean of the surveyed stations, so it
can be reproduced from the open station-level data. This script recomputes, for
the week of the workbook, each municipality and product from the curated
``fuel_price`` table: the stations with a price in the week, each counted once
with the mean of its prices that week (the rule of the curated aggregates),
with exact fractions instead of floating point. A pair matches when the number
of stations, the minimum, the maximum and the mean to the cent are the same.

How ANP turns a mean into the two decimals it publishes is not documented. The
script tries four rules and prints how many published means each reproduces;
the one that reproduces them is "round to three decimals, then cut to two".

ANP's national means are weighted by the volume each distributor sells, which
is not in the open data, so for Brazil only the difference is shown.

Exit code 0 when at least ``--min-match`` of the municipal pairs match, 1 when
fewer do. Nothing is written.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

import duckdb
import openpyxl

from fuel_price_lakehouse.layout import DEFAULT_DATA_ROOT, curated_dir
from fuel_price_lakehouse.reference import STATES

CENT = Decimal("0.01")
MILLI = Decimal("0.001")

PRODUCT_OF_SUMMARY = {
    "GASOLINA COMUM": "GASOLINA",
    "GASOLINA ADITIVADA": "GASOLINA ADITIVADA",
    "ETANOL HIDRATADO": "ETANOL",
    "OLEO DIESEL": "DIESEL",
    "OLEO DIESEL S10": "DIESEL S10",
    "GNV": "GNV",
    "GLP": "GLP",
}
"""The workbook's product names (``OLEO DIESEL`` is diesel S500) to the curated ids."""

HEADER_MARK = "DATA INICIAL"
"""The first cell of the header row; the rows above it are the report's title."""

Key = tuple[str, str, str]
"""(state code, municipality, product id)."""
Stations = dict[str, list[Decimal]]
"""The prices of each station (by CNPJ) in the week."""


def _decimal(value: Fraction) -> Decimal:
    return Decimal(value.numerator) / Decimal(value.denominator)


ROUNDING_RULES: dict[str, Callable[[Fraction], Decimal]] = {
    "round to 3 decimals, then cut to 2": lambda mean: (
        _decimal(mean).quantize(MILLI, ROUND_HALF_UP).quantize(CENT, ROUND_DOWN)
    ),
    "cut to 2 decimals": lambda mean: _decimal(mean).quantize(CENT, ROUND_DOWN),
    "round half-up to 2 decimals": lambda mean: _decimal(mean).quantize(CENT, ROUND_HALF_UP),
    "round half-even to 2 decimals": lambda mean: _decimal(mean).quantize(CENT, ROUND_HALF_EVEN),
}
ANP_RULE = next(iter(ROUNDING_RULES))


def plain(text: str) -> str:
    """Upper case without accents, as the workbook writes state names."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper().strip()


STATE_OF_NAME = {plain(state.name): state.code for state in STATES}


@dataclass(frozen=True)
class Published:
    """One row of the workbook. ``state`` and ``municipality`` are empty for Brazil."""

    week: date
    state: str
    municipality: str
    product: str
    stations: int
    mean: Decimal
    minimum: Decimal
    maximum: Decimal

    @property
    def key(self) -> Key:
        return (self.state, self.municipality, self.product)


@dataclass(frozen=True)
class Computed:
    """The same figures computed from the curated table, exactly."""

    stations: int
    mean: Fraction
    minimum: Fraction
    maximum: Fraction

    @classmethod
    def of(cls, prices: Stations) -> Computed:
        means = [
            sum(map(Fraction, station), Fraction(0)) / len(station) for station in prices.values()
        ]
        return cls(len(means), sum(means, Fraction(0)) / len(means), min(means), max(means))

    def matches(self, row: Published) -> bool:
        return (
            self.stations == row.stations
            and ROUNDING_RULES[ANP_RULE](self.mean) == row.mean
            and (_decimal(self.minimum), _decimal(self.maximum)) == (row.minimum, row.maximum)
        )

    def __str__(self) -> str:
        return (
            f"n={self.stations} mean={_decimal(self.mean):.4f} "
            f"min={_decimal(self.minimum)} max={_decimal(self.maximum)}"
        )


def read_sheet(workbook: Path, sheet: str) -> list[Published]:
    """The rows of one sheet of ANP's weekly summary (``MUNICIPIOS`` or ``BRASIL``)."""
    book = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
    try:
        rows: Iterator[tuple[object, ...]] = book[sheet].iter_rows(values_only=True)
        header = next(
            [str(cell or "").strip() for cell in row] for row in rows if row[0] == HEADER_MARK
        )
        records = [dict(zip(header, row, strict=False)) for row in rows]
    finally:
        book.close()
    return [_published(record) for record in records if isinstance(record[HEADER_MARK], datetime)]


def _published(record: dict[str, object]) -> Published:
    start = record[HEADER_MARK]
    assert isinstance(start, datetime)
    state = record.get("ESTADO")
    return Published(
        week=start.date(),
        state=STATE_OF_NAME[plain(str(state))] if state else "",
        municipality=str(record.get("MUNICÍPIO") or "").strip(),
        product=PRODUCT_OF_SUMMARY[str(record["PRODUTO"]).strip()],
        stations=int(str(record["NÚMERO DE POSTOS PESQUISADOS"])),
        mean=_price(record["PREÇO MÉDIO REVENDA"]),
        minimum=_price(record["PREÇO MÍNIMO REVENDA"]),
        maximum=_price(record["PREÇO MÁXIMO REVENDA"]),
    )


def _price(cell: object) -> Decimal:
    """A price as the workbook shows it: two decimals (the cell holds a float)."""
    return Decimal(str(cell)).quantize(CENT, ROUND_HALF_UP)


def station_prices(root: Path, week: date) -> dict[Key, Stations]:
    """{(state, municipality, product): {cnpj: [prices]}} for the survey week."""
    rows = duckdb.execute(
        "SELECT state_code, municipality, product, station_cnpj, CAST(sale_price AS VARCHAR) "
        "FROM read_parquet(?, hive_partitioning = false) WHERE survey_week = ?",
        [str(curated_dir(root) / "fuel_price" / "year=*" / "data.parquet"), week],
    ).fetchall()
    cells: dict[Key, Stations] = {}
    for state, municipality, product, cnpj, price in rows:
        cells.setdefault((state, municipality, product), {}).setdefault(cnpj, []).append(
            Decimal(price)
        )
    return cells


def municipal_report(cells: dict[Key, Stations], rows: list[Published]) -> tuple[int, list[str]]:
    """How many pairs match, and the lines of the report."""
    by_rule: Counter[str] = Counter()
    matched: Counter[str] = Counter()
    total: Counter[str] = Counter()
    differences = []
    for row in rows:
        key = row.key
        total[row.product] += 1
        if key not in cells:
            differences.append(f"  differs {key}: no station in the curated table that week")
            continue
        ours = Computed.of(cells[key])
        by_rule.update(rule for rule, cut in ROUNDING_RULES.items() if cut(ours.mean) == row.mean)
        if ours.matches(row):
            matched[row.product] += 1
        else:
            differences.append(
                f"  differs {key}: ours {ours}; ANP n={row.stations} mean={row.mean} "
                f"min={row.minimum} max={row.maximum}"
            )
    lines = [f"Municipalities: {len(rows):,} municipality-product pairs in ANP's summary"]
    lines.append("  published means reproduced, by rounding rule:")
    lines += [f"    {rule}: {by_rule[rule]:,}" for rule in ROUNDING_RULES]
    lines.append(f"  pairs that match (stations, minimum, maximum, mean under '{ANP_RULE}'):")
    lines += [
        f"    {product:<20}{matched[product]:>6,} of {count:,}"
        for product, count in sorted(total.items())
    ]
    lines.append(f"    {'all':<20}{sum(matched.values()):>6,} of {len(rows):,}")
    return sum(matched.values()), [*lines, *differences]


def national_report(cells: dict[Key, Stations], rows: list[Published]) -> list[str]:
    """ANP's sales-weighted national means against the simple means of the curated table."""
    by_product: dict[str, Stations] = {}
    for (_, _, product), stations in cells.items():
        by_product.setdefault(product, {}).update(stations)
    lines = [
        "Brazil: ANP weights by distributor sales; the curated table has simple means",
        f"  {'product':<20}{'ANP n':>7}{'our n':>7}{'ANP mean':>10}{'our mean':>10}{'diff':>9}",
    ]
    for row in rows:
        if row.product not in by_product:
            lines.append(f"  {row.product:<20}{row.stations:>7}{'-':>7}")
            continue
        ours = Computed.of(by_product[row.product])
        mean = _decimal(ours.mean).quantize(MILLI, ROUND_HALF_UP)
        lines.append(
            f"  {row.product:<20}{row.stations:>7,}{ours.stations:>7,}{row.mean:>10}{mean:>10}"
            f"{100 * (mean - row.mean) / row.mean:>+8.2f}%"
        )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("workbook", type=Path, help="ANP's weekly summary (.xlsx)")
    parser.add_argument("--root", type=Path, default=DEFAULT_DATA_ROOT, help="data directory")
    parser.add_argument(
        "--min-match",
        type=float,
        default=0.99,
        metavar="SHARE",
        help="share of municipal pairs that must match for exit code 0 (default 0.99)",
    )
    args = parser.parse_args(argv)

    municipal = read_sheet(args.workbook, "MUNICIPIOS")
    weeks = {row.week for row in municipal}
    if len(weeks) != 1:
        parser.error(f"{args.workbook}: expected one survey week, found {sorted(weeks)}")
    (week,) = weeks
    cells = station_prices(args.root, week)
    print(f"Survey week {week} to {week + timedelta(days=6)}: {len(cells):,} cells in the table")
    matched, lines = municipal_report(cells, municipal)
    print("\n".join(lines))
    print()
    print("\n".join(national_report(cells, read_sheet(args.workbook, "BRASIL"))))
    return 0 if municipal and matched / len(municipal) >= args.min_match else 1


if __name__ == "__main__":
    sys.exit(main())
