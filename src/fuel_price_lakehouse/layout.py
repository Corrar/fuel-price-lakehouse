"""Where each pipeline layer lives on disk.

Every stage reads and writes through these helpers, so the directory layout is
defined in exactly one place. Partitions use the Hive ``key=value`` convention,
which DuckDB and PyArrow both read natively.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

DEFAULT_DATA_ROOT = Path("data")

_SAFE_VALUE = re.compile(r"[A-Za-z0-9._-]+")


class Layer(StrEnum):
    """Pipeline layers, in the order data flows through them."""

    RAW = "raw"
    """Files exactly as downloaded. Never modified."""

    STAGED = "staged"
    """Typed and normalised rows that passed every validation rule."""

    QUARANTINE = "quarantine"
    """Rows that broke a rule, kept with the reason instead of being dropped."""

    CURATED = "curated"
    """Analysis-ready tables built from staged data."""


class Source(StrEnum):
    """Upstream systems the pipeline collects from."""

    ANP = "anp"
    BCB_SGS = "bcb_sgs"


def layer_dir(layer: Layer, source: Source, root: Path = DEFAULT_DATA_ROOT) -> Path:
    """Directory holding everything from ``source`` at ``layer``."""
    return root / layer.value / source.value


def manifest_path(root: Path = DEFAULT_DATA_ROOT) -> Path:
    """The collection manifest: which raw files were already ingested."""
    return root / "manifest" / "collection.jsonl"


def safe_filename(name: str) -> str:
    """``name`` reduced to characters that are safe as a single path component."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned or "file"


def partition_dir(
    layer: Layer,
    source: Source,
    dataset: str,
    root: Path = DEFAULT_DATA_ROOT,
    **partitions: str | int,
) -> Path:
    """Hive-style partition directory for ``dataset``.

    >>> partition_dir(Layer.STAGED, Source.ANP, "fuel_prices", year=2024, month=7).as_posix()
    'data/staged/anp/fuel_prices/year=2024/month=7'

    Partition keys keep the order they were passed in. Names and values are
    restricted to a conservative character set so a value coming from upstream
    data can never escape the dataset directory.
    """
    path = layer_dir(layer, source, root) / _checked(dataset, "dataset name")
    for key, value in partitions.items():
        path /= f"{_checked(key, 'partition key')}={_checked(str(value), 'partition value')}"
    return path


def _checked(value: str, what: str) -> str:
    if not _SAFE_VALUE.fullmatch(value) or set(value) == {"."}:
        raise ValueError(f"unsafe {what}: {value!r}")
    return value
