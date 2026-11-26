from fuel_price_lakehouse.reference import PRODUCTS, REGION_OF_STATE, REGIONS, STATES


def test_brazil_has_27_federative_units_in_5_regions():
    assert len(STATES) == len({state.code for state in STATES}) == 27
    assert {state.region for state in STATES} == {region.code for region in REGIONS}
    assert len(REGIONS) == 5


def test_region_sizes_match_the_official_division():
    sizes = {region.code: 0 for region in REGIONS}
    for region in REGION_OF_STATE.values():
        sizes[region] += 1
    assert sizes == {"N": 7, "NE": 9, "CO": 4, "SE": 4, "S": 3}


def test_product_ids_are_unique_and_each_has_one_unit():
    assert len(PRODUCTS) == len({product.id for product in PRODUCTS}) == 7
    assert {product.unit for product in PRODUCTS} == {"BRL/L", "BRL/m3", "BRL/13kg"}
