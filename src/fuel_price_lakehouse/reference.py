"""Reference data that does not come from the sources: geography and products.

Kept in code, not in a data file, because it changes about once a generation
(the last new Brazilian state dates from 1988) and every stage needs it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Region:
    code: str
    name_pt: str
    name_en: str


@dataclass(frozen=True)
class State:
    code: str
    name: str
    region: str


@dataclass(frozen=True)
class Product:
    id: str
    """The value ANP publishes in the ``Produto`` column."""
    label_pt: str
    label_en: str
    source_unit: str
    """The value ANP publishes in ``Unidade de Medida`` for this product."""
    unit: str
    """The normalised unit used from the staged layer on."""


REGIONS: tuple[Region, ...] = (
    Region("N", "Norte", "North"),
    Region("NE", "Nordeste", "Northeast"),
    Region("CO", "Centro-Oeste", "Central-West"),
    Region("SE", "Sudeste", "Southeast"),
    Region("S", "Sul", "South"),
)

STATES: tuple[State, ...] = (
    State("AC", "Acre", "N"),
    State("AL", "Alagoas", "NE"),
    State("AM", "Amazonas", "N"),
    State("AP", "Amapá", "N"),
    State("BA", "Bahia", "NE"),
    State("CE", "Ceará", "NE"),
    State("DF", "Distrito Federal", "CO"),
    State("ES", "Espírito Santo", "SE"),
    State("GO", "Goiás", "CO"),
    State("MA", "Maranhão", "NE"),
    State("MG", "Minas Gerais", "SE"),
    State("MS", "Mato Grosso do Sul", "CO"),
    State("MT", "Mato Grosso", "CO"),
    State("PA", "Pará", "N"),
    State("PB", "Paraíba", "NE"),
    State("PE", "Pernambuco", "NE"),
    State("PI", "Piauí", "NE"),
    State("PR", "Paraná", "S"),
    State("RJ", "Rio de Janeiro", "SE"),
    State("RN", "Rio Grande do Norte", "NE"),
    State("RO", "Rondônia", "N"),
    State("RR", "Roraima", "N"),
    State("RS", "Rio Grande do Sul", "S"),
    State("SC", "Santa Catarina", "S"),
    State("SE", "Sergipe", "NE"),
    State("SP", "São Paulo", "SE"),
    State("TO", "Tocantins", "N"),
)

UNIT_LITRE = "BRL/L"
UNIT_CUBIC_METRE = "BRL/m3"
UNIT_CYLINDER_13KG = "BRL/13kg"

PRODUCTS: tuple[Product, ...] = (
    Product("GASOLINA", "Gasolina comum", "Regular gasoline", "R$ / litro", UNIT_LITRE),
    Product(
        "GASOLINA ADITIVADA", "Gasolina aditivada", "Premium gasoline", "R$ / litro", UNIT_LITRE
    ),
    Product("ETANOL", "Etanol hidratado", "Hydrous ethanol", "R$ / litro", UNIT_LITRE),
    Product("DIESEL", "Diesel S500", "Diesel S500", "R$ / litro", UNIT_LITRE),
    Product("DIESEL S10", "Diesel S10", "Diesel S10", "R$ / litro", UNIT_LITRE),
    Product("GNV", "GNV", "CNG", "R$ / m³", UNIT_CUBIC_METRE),
    Product(
        "GLP", "GLP (botijão de 13 kg)", "LPG (13 kg cylinder)", "R$ / 13 kg", UNIT_CYLINDER_13KG
    ),
)

REGION_OF_STATE: dict[str, str] = {state.code: state.region for state in STATES}
PRODUCT_BY_ID: dict[str, Product] = {product.id: product for product in PRODUCTS}
