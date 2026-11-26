from pathlib import Path

import pytest

from fuel_price_lakehouse.layout import Layer, Source, layer_dir, partition_dir


def test_layer_dir_is_root_layer_source():
    assert layer_dir(Layer.RAW, Source.ANP) == Path("data/raw/anp")


def test_layer_dir_honours_custom_root(tmp_path):
    assert layer_dir(Layer.CURATED, Source.BCB_SGS, tmp_path) == tmp_path / "curated" / "bcb_sgs"


def test_partition_dir_is_hive_style_and_keeps_key_order():
    path = partition_dir(Layer.STAGED, Source.ANP, "fuel_prices", year=2024, month=7)
    assert path == Path("data/staged/anp/fuel_prices/year=2024/month=7")


def test_partition_dir_without_partitions_is_the_dataset_dir():
    path = partition_dir(Layer.QUARANTINE, Source.ANP, "fuel_prices")
    assert path == Path("data/quarantine/anp/fuel_prices")


@pytest.mark.parametrize("value", ["../etc", "a/b", "", "..", "sp ace", "a=b"])
def test_partition_values_cannot_escape_the_dataset_dir(value):
    with pytest.raises(ValueError, match="unsafe partition value"):
        partition_dir(Layer.RAW, Source.ANP, "fuel_prices", period=value)


def test_dataset_name_is_checked_too():
    with pytest.raises(ValueError, match="unsafe dataset name"):
        partition_dir(Layer.RAW, Source.ANP, "../fuel_prices")


def test_every_layer_gets_its_own_directory():
    dirs = {layer_dir(layer, Source.ANP) for layer in Layer}
    assert len(dirs) == len(Layer)
