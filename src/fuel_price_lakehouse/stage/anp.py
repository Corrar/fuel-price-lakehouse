"""Staging of ANP price files: one typed row per observation, or quarantine with the reasons.

A raw file holds what ANP published, every field as text. Here each row is read
(whitespace normalised, CNPJ unmasked, date and prices parsed) and judged by the
rules below. A row that breaks any error rule goes to quarantine exactly as it
was in the raw layer, with the ids of every rule it broke; the others become
the staged table.

Decisions that shape the rules:

- A row with nothing in it is reported once, under ``row_not_blank``, and
  passes every other rule. One semester file ends with thousands of such rows;
  counting them again under every rule an empty field breaks would bury the
  real problems.
- A value that cannot be read is reported under the rule that names it
  (``collected_on_is_a_date``, ``sale_price_is_a_number``...). Rules that need
  the value let the row pass, so each rule counts a different problem.
- Warning rules run on the rows that passed the error rules, so the weekly
  median and the uniqueness of a key are worked out without rejected rows,
  exact duplicates included.
- Text is kept as published apart from whitespace: no case folding. A product
  or a state written in another case is quarantined and shows up in the report
  instead of being silently mapped. The brand is the exception. It is an open
  list that no rule can check, so it is upper-cased, as every brand in the
  files is: a second spelling must not split one brand in two.
"""

from __future__ import annotations

import calendar
import re
from datetime import UTC, date

import pandas as pd
import pyarrow as pa
from pandera.pandas import Check, Column, DataFrameSchema

from fuel_price_lakehouse.collect.anp import LAST_FOUR_WEEKS, MONTHLY, SEMESTER
from fuel_price_lakehouse.config import Clock, parse_month
from fuel_price_lakehouse.reference import (
    PRODUCT_BY_ID,
    PRODUCTS,
    REGION_OF_STATE,
    REGIONS,
    UNIT_CUBIC_METRE,
)
from fuel_price_lakehouse.stage import cnpj, text
from fuel_price_lakehouse.stage.model import DATE, ID_LIST, SchemaDriftError, StagedFrames, split
from fuel_price_lakehouse.stage.rules import Check as RowCheck
from fuel_price_lakehouse.stage.rules import FileContext, Rule, Severity

SOURCE_COLUMNS: tuple[str, ...] = (
    "Regiao - Sigla",
    "Estado - Sigla",
    "Municipio",
    "Revenda",
    "CNPJ da Revenda",
    "Nome da Rua",
    "Numero Rua",
    "Complemento",
    "Bairro",
    "Cep",
    "Produto",
    "Data da Coleta",
    "Valor de Venda",
    "Valor de Compra",
    "Unidade de Medida",
    "Bandeira",
)

SALE_PRICE_BOUNDS: dict[str, tuple[float, float]] = {
    "GASOLINA": (1.00, 15.00),
    "GASOLINA ADITIVADA": (1.00, 15.00),
    "DIESEL": (0.80, 15.00),
    "DIESEL S10": (0.80, 15.00),
    "ETANOL": (0.30, 12.00),
    "GNV": (0.30, 12.00),
    "GLP": (10.00, 300.00),
}
"""Lowest and highest sale price accepted per product, in BRL per unit.

Wide on purpose: the bounds are there to catch a wrong unit or a slipped
decimal comma (599 for 5,99), and must never reject a real price, including the
much lower ones of earlier decades if older history is loaded.
"""

MEDIAN_RATIO_BOUNDS = (0.5, 2.0)
"""A sale price outside this multiple of its product's weekly median gets a warning."""

_SALE_PRICE_DECIMALS = 3
_PURCHASE_PRICE_DECIMALS = 4
_POSTAL_CODE_TEXT = "[0-9]{5}-?[0-9]{3}"
_UNKNOWN_POSTAL_CODE = "00000000"
_SEMESTER_PERIOD = re.compile(r"([0-9]{4})-S([12])")

# The cubic metre is written "m³", except in the automotive file of the second
# semester of 2025, which spells it with a plain 3.
_UNIT_OF_SOURCE_TEXT = {
    **{product.source_unit: product.unit for product in PRODUCTS},
    "R$ / m3": UNIT_CUBIC_METRE,
}
_UNIT_OF_PRODUCT = {product.id: product.unit for product in PRODUCTS}
_LOWEST_PRICE = {product: low for product, (low, _) in SALE_PRICE_BOUNDS.items()}
_HIGHEST_PRICE = {product: high for product, (_, high) in SALE_PRICE_BOUNDS.items()}

_KEY = ["station_cnpj", "product", "collected_on"]
_BLANK = "_blank"


def _file_period(kind: str, period: str) -> tuple[date, date] | None:
    """First and last day a file may hold rows for; None for the rolling four-week files."""
    if kind == LAST_FOUR_WEEKS:
        return None
    if kind == MONTHLY:
        first = parse_month(period)
        last_month = first.month
    elif kind == SEMESTER and (match := _SEMESTER_PERIOD.fullmatch(period)):
        first = date(int(match[1]), 1 if match[2] == "1" else 7, 1)
        last_month = first.month + 5
    else:
        raise ValueError(f"not an ANP file kind and period: {kind!r}, {period!r}")
    return first, date(first.year, last_month, calendar.monthrange(first.year, last_month)[1])


def _region_matches_state(rows: pd.DataFrame, _context: FileContext) -> pd.Series:
    expected = rows["state_code"].map(REGION_OF_STATE)
    return expected.isna() | (rows["region_code"] == expected)


def _cnpj_check_digits_valid(rows: pd.DataFrame, _context: FileContext) -> pd.Series:
    numbers = rows["station_cnpj"]
    valid = [number for number in numbers.dropna().unique() if cnpj.has_valid_check_digits(number)]
    return numbers.isna() | numbers.isin(valid)


def _unit_matches_product(rows: pd.DataFrame, _context: FileContext) -> pd.Series:
    expected = rows["product"].map(_UNIT_OF_PRODUCT)
    return expected.isna() | (rows["unit"] == expected)


def _within_file_period(rows: pd.DataFrame, context: FileContext) -> pd.Series:
    period = _file_period(context.details["kind"], context.details["period"])
    if period is None:
        return pd.Series(True, index=rows.index)
    first, last = period
    collected_on = rows["collected_on"]
    return collected_on.isna() | collected_on.between(pd.Timestamp(first), pd.Timestamp(last))


def _not_after_collection(rows: pd.DataFrame, context: FileContext) -> pd.Series:
    # The download day in UTC is never behind the day in Brazil, where the
    # survey is dated, so a price collected and published the same day passes.
    downloaded_on = pd.Timestamp(context.collected_at.astimezone(UTC).date())
    return rows["collected_on"].isna() | (rows["collected_on"] <= downloaded_on)


def _sale_price_within_bounds(rows: pd.DataFrame, _context: FileContext) -> pd.Series:
    price = rows["sale_price"]
    # Explicit types: a file with no known product would otherwise infer its own.
    lowest = rows["product"].map(_LOWEST_PRICE).astype("float64")
    highest = rows["product"].map(_HIGHEST_PRICE).astype("float64")
    return price.isna() | lowest.isna() | price.between(lowest, highest)


def _purchase_price_is_a_number_or_empty(rows: pd.DataFrame, _context: FileContext) -> pd.Series:
    return rows["purchase_price"].notna() | text.clean(rows["Valor de Compra"]).isna()


def _sale_price_near_weekly_median(rows: pd.DataFrame, _context: FileContext) -> pd.Series:
    collected_on = rows["collected_on"]
    # ANP's survey week runs from Sunday to Saturday.
    week = collected_on - pd.to_timedelta((collected_on.dt.day_of_week + 1) % 7, unit="D")
    median = rows["sale_price"].groupby([rows["product"], week]).transform("median")
    low, high = MEDIAN_RATIO_BOUNDS
    return rows["sale_price"].between(low * median, high * median)


def _error(rule_id: str, description: str, description_pt: str, check: RowCheck) -> Rule:
    """An error rule that blank rows pass: they are counted under ``row_not_blank`` only."""

    def passes(rows: pd.DataFrame, context: FileContext) -> pd.Series:
        return rows[_BLANK] | check(rows, context)

    return Rule(rule_id, Severity.ERROR, description, description_pt, passes)


def _warning(rule_id: str, description: str, description_pt: str, check: RowCheck) -> Rule:
    return Rule(rule_id, Severity.WARNING, description, description_pt, check)


RULES: tuple[Rule, ...] = (
    Rule(
        "row_not_blank",
        Severity.ERROR,
        "The row has at least one field filled in.",
        "A linha tem pelo menos um campo preenchido.",
        lambda rows, _context: ~rows[_BLANK],
    ),
    _error(
        "field_count_is_16",
        "The row was read into exactly the 16 fields of the file layout.",
        "A linha foi lida com exatamente os 16 campos do layout do arquivo.",
        lambda rows, _context: rows["_field_count"] == len(SOURCE_COLUMNS),
    ),
    _error(
        "state_is_known",
        "The state code is that of one of the 27 Brazilian states.",
        "A sigla do estado é a de uma das 27 unidades da federação.",
        lambda rows, _context: rows["state_code"].isin(list(REGION_OF_STATE)),
    ),
    _error(
        "region_matches_state",
        "The region is the one the state belongs to.",
        "A região é aquela a que o estado pertence.",
        _region_matches_state,
    ),
    _error(
        "municipality_present",
        "The municipality is filled in.",
        "O município está preenchido.",
        lambda rows, _context: rows["municipality"].notna(),
    ),
    _error(
        "station_name_present",
        "The name of the station (Revenda) is filled in.",
        "O nome da revenda está preenchido.",
        lambda rows, _context: rows["station_name"].notna(),
    ),
    _error(
        "cnpj_has_14_digits",
        "The CNPJ of the station has 14 digits once its punctuation is removed.",
        "O CNPJ da revenda tem 14 dígitos depois de retirada a pontuação.",
        lambda rows, _context: rows["station_cnpj"].notna(),
    ),
    _error(
        "cnpj_check_digits_valid",
        "The two check digits of the station's CNPJ are correct.",
        "Os dois dígitos verificadores do CNPJ da revenda estão corretos.",
        _cnpj_check_digits_valid,
    ),
    _error(
        "product_is_known",
        "The product is one of the seven fuels the survey covers.",
        "O produto é um dos sete combustíveis cobertos pela pesquisa.",
        lambda rows, _context: rows["product"].isin(list(PRODUCT_BY_ID)),
    ),
    _error(
        "unit_matches_product",
        "The unit of measure is the one its product is priced in.",
        "A unidade de medida é aquela em que o produto é cotado.",
        _unit_matches_product,
    ),
    _error(
        "brand_present",
        "The brand of the station (Bandeira) is filled in.",
        "A bandeira da revenda está preenchida.",
        lambda rows, _context: rows["brand"].notna(),
    ),
    _error(
        "collected_on_is_a_date",
        "The collection date is a real calendar date written as dd/mm/yyyy.",
        "A data da coleta é uma data real do calendário, escrita como dd/mm/aaaa.",
        lambda rows, _context: rows["collected_on"].notna(),
    ),
    _error(
        "collected_on_within_file_period",
        "The collection date falls inside the semester or the month its file covers.",
        "A data da coleta está dentro do semestre ou do mês a que o arquivo se refere.",
        _within_file_period,
    ),
    _error(
        "collected_on_not_after_collection",
        "The collection date is not later than the day the file was downloaded.",
        "A data da coleta não é posterior ao dia em que o arquivo foi baixado.",
        _not_after_collection,
    ),
    _error(
        "sale_price_is_a_number",
        "The sale price is a number with up to three decimals, such as 5,99, 5,999 or 6.",
        "O preço de venda é um número com até três casas decimais, como 5,99, 5,999 ou 6.",
        lambda rows, _context: rows["sale_price"].notna(),
    ),
    _error(
        "sale_price_within_bounds",
        "The sale price is inside the plausible range for its product.",
        "O preço de venda está dentro da faixa plausível para o seu produto.",
        _sale_price_within_bounds,
    ),
    _error(
        "purchase_price_is_a_number_or_empty",
        "The purchase price is empty or a number with up to four decimals.",
        "O preço de compra está vazio ou é um número com até quatro casas decimais.",
        _purchase_price_is_a_number_or_empty,
    ),
    _error(
        "not_an_exact_duplicate",
        "The row does not repeat, in all 16 fields, an earlier row of the same file.",
        "A linha não repete, em todos os 16 campos, uma linha anterior do mesmo arquivo.",
        lambda rows, _context: ~rows.duplicated(list(SOURCE_COLUMNS)),
    ),
    _warning(
        "postal_code_is_valid",
        "The postal code (CEP) has 8 digits and is not 00000-000.",
        "O CEP tem 8 dígitos e não é 00000-000.",
        lambda rows, _context: rows["postal_code"].notna(),
    ),
    _warning(
        "purchase_price_not_above_sale_price",
        "The purchase price is not higher than the sale price.",
        "O preço de compra não é maior que o preço de venda.",
        lambda rows, _context: ~(rows["purchase_price"] > rows["sale_price"]),
    ),
    _warning(
        "sale_price_near_weekly_median",
        "The sale price is between half and twice the median of its product "
        "in the same survey week of the file.",
        "O preço de venda está entre a metade e o dobro da mediana do seu produto "
        "na mesma semana de pesquisa do arquivo.",
        _sale_price_near_weekly_median,
    ),
    _warning(
        "key_unique_in_file",
        "No other row of the file has the same station, product and collection date.",
        "Nenhuma outra linha do arquivo tem a mesma revenda, produto e data de coleta.",
        lambda rows, _context: ~rows.duplicated(_KEY, keep=False),
    ),
)


def _required(name: str, kind: pa.DataType | None = None) -> pa.Field:
    return pa.field(name, kind or pa.string(), nullable=False)


_PROVENANCE = (
    _required("_source_kind"),
    _required("_source_period"),
    _required("_source_url"),
    _required("_source_sha256"),
    _required("_source_row_number", pa.int64()),
    _required("_ingested_at", pa.timestamp("us", tz="UTC")),
)

STAGED_ARROW_SCHEMA = pa.schema(
    [
        _required("region_code"),
        _required("state_code"),
        _required("municipality"),
        _required("station_name"),
        _required("station_cnpj"),
        pa.field("street", pa.string()),
        pa.field("street_number", pa.string()),
        pa.field("address_complement", pa.string()),
        pa.field("neighbourhood", pa.string()),
        pa.field("postal_code", pa.string()),
        _required("product"),
        _required("collected_on", pa.date32()),
        _required("sale_price", pa.float64()),
        pa.field("purchase_price", pa.float64()),
        _required("unit"),
        _required("brand"),
        *_PROVENANCE,
        _required("_warnings", pa.list_(pa.string())),
    ]
)

QUARANTINE_ARROW_SCHEMA = pa.schema(
    [
        # Null when the row was too short to have the field at all.
        *(pa.field(column, pa.string()) for column in SOURCE_COLUMNS),
        _required("_field_count", pa.int32()),
        pa.field("_extra_fields", pa.string()),
        *_PROVENANCE,
        _required("_rule_ids", pa.list_(pa.string())),
        _required("_quarantined_at", pa.timestamp("us", tz="UTC")),
    ]
)

# Only built-in, vectorised checks: this runs over every staged row of every
# file. Each of them is implied by an error rule or by how the raw layer writes
# its tracking columns, so a failure here is a bug in the pipeline and never a
# property of the data.
STAGED_SCHEMA = DataFrameSchema(
    {
        "region_code": Column(str, Check.isin([region.code for region in REGIONS])),
        "state_code": Column(str, Check.isin(list(REGION_OF_STATE))),
        "municipality": Column(str),
        "station_name": Column(str),
        "station_cnpj": Column(str, Check.str_matches(r"^[0-9]{14}$")),
        "street": Column(str, nullable=True),
        "street_number": Column(str, nullable=True),
        "address_complement": Column(str, nullable=True),
        "neighbourhood": Column(str, nullable=True),
        "postal_code": Column(str, Check.str_matches(r"^[0-9]{8}$"), nullable=True),
        "product": Column(str, Check.isin(list(PRODUCT_BY_ID))),
        "collected_on": Column(DATE),
        "sale_price": Column("float64", Check.gt(0)),
        "purchase_price": Column("float64", Check.ge(0), nullable=True),
        "unit": Column(str, Check.isin(sorted(set(_UNIT_OF_PRODUCT.values())))),
        "brand": Column(str),
        "_source_kind": Column(str, Check.isin([SEMESTER, MONTHLY, LAST_FOUR_WEEKS])),
        "_source_period": Column(str),
        "_source_url": Column(str),
        "_source_sha256": Column(str, Check.str_matches(r"^[0-9a-f]{64}$")),
        "_source_row_number": Column("int64", Check.ge(1)),
        "_ingested_at": Column("datetime64[us, UTC]"),
        "_warnings": Column(ID_LIST),
    },
    strict=True,
    ordered=True,
)


def stage(raw: pd.DataFrame, context: FileContext, *, now: Clock) -> StagedFrames:
    """Split one raw price file into staged rows and quarantined rows.

    Every raw row comes out in exactly one of the two frames. A file whose
    header is not the 16 columns of the ANP layout raises ``SchemaDriftError``.
    """
    _check_header(raw, context)
    frames = split(
        pd.concat([raw, _typed(raw, context)], axis=1),
        RULES,
        context,
        now=now,
        staged_schema=STAGED_ARROW_SCHEMA,
        quarantine_schema=QUARANTINE_ARROW_SCHEMA,
    )
    # A staged table that breaks its contract is a bug here, not bad data: it raises.
    STAGED_SCHEMA.validate(frames.staged)
    return frames


def _check_header(raw: pd.DataFrame, context: FileContext) -> None:
    published = [column for column in raw.columns if not column.startswith("_")]
    missing = [column for column in SOURCE_COLUMNS if column not in published]
    unexpected = [column for column in published if column not in SOURCE_COLUMNS]
    if missing or unexpected:
        raise SchemaDriftError(
            f"{context.key}: the header is not the expected one "
            f"(missing {missing}, unexpected {unexpected})"
        )


def _typed(raw: pd.DataFrame, context: FileContext) -> pd.DataFrame:
    """What each raw row says once its text is read: missing where it says nothing valid.

    The columns already have their staged names. ``stage`` puts them next to
    the raw row, which the rules about the row as a whole still need.
    """
    source = {column: text.clean(raw[column]) for column in SOURCE_COLUMNS}
    postal_code = source["Cep"].where(source["Cep"].str.fullmatch(_POSTAL_CODE_TEXT))
    postal_code = postal_code.str.replace("-", "", regex=False)
    return pd.DataFrame(
        {
            "region_code": source["Regiao - Sigla"],
            "state_code": source["Estado - Sigla"],
            "municipality": source["Municipio"],
            "station_name": source["Revenda"],
            "station_cnpj": text.map_distinct(source["CNPJ da Revenda"], cnpj.digits),
            "street": source["Nome da Rua"],
            "street_number": source["Numero Rua"],
            "address_complement": source["Complemento"],
            "neighbourhood": source["Bairro"],
            "postal_code": postal_code.where(postal_code != _UNKNOWN_POSTAL_CODE),
            "product": source["Produto"],
            "collected_on": text.parse_date(source["Data da Coleta"]),
            "sale_price": _price(source["Valor de Venda"], _SALE_PRICE_DECIMALS),
            "purchase_price": _price(source["Valor de Compra"], _PURCHASE_PRICE_DECIMALS),
            "unit": text.map_distinct(source["Unidade de Medida"], _UNIT_OF_SOURCE_TEXT.get),
            "brand": source["Bandeira"].str.upper(),
            "_source_kind": pd.Series(context.details["kind"], index=raw.index, dtype="str"),
            "_source_period": pd.Series(context.details["period"], index=raw.index, dtype="str"),
            "_source_row_number": raw["_row_number"],
            "_ingested_at": raw["_collected_at"],
            _BLANK: pd.DataFrame(source).isna().all(axis=1),
        }
    )


def _price(values: pd.Series, max_decimals: int) -> pd.Series:
    return text.parse_decimal(values, separator=",", max_decimals=max_decimals)
