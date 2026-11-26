"""The landing zone: every downloaded file kept byte for byte.

The raw layer is a Parquet copy, and making it involves decisions (which
encoding, how to read the CSV). Keeping the bytes that were actually
downloaded means those decisions can be revisited without the network, and the
SHA-256 in the manifest can be checked again at any time.

Files are addressed by the hash of their content, so a republished file lands
next to the version it replaces instead of over it. ZIP files are stored as
they came; everything else is compressed with zstd, which shrinks ANP's CSV
files to roughly a tenth.
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path

import pyarrow as pa

from fuel_price_lakehouse.layout import Layer, layer_dir

_CHUNK = 1024 * 1024
_ZIP = ".zip"
_COMPRESSED = ".zst"


def store_file(downloaded: Path, sha256: str, source: str, root: Path) -> Path:
    """Keep a downloaded file. Returns where it landed; a no-op if already there."""
    suffix = _ZIP if zipfile.is_zipfile(downloaded) else _COMPRESSED
    target = _path(root, source, sha256, suffix)
    if target.exists():
        return target
    with downloaded.open("rb") as source_bytes:
        _write(source_bytes.read, target, compress=suffix == _COMPRESSED)
    return target


def store_bytes(body: bytes, sha256: str, source: str, root: Path) -> Path:
    """Keep a response body. Returns where it landed; a no-op if already there."""
    target = _path(root, source, sha256, _COMPRESSED)
    if not target.exists():
        remaining = [body]
        _write(lambda _size: remaining.pop() if remaining else b"", target, compress=True)
    return target


def restore(landed: Path, destination: Path) -> None:
    """Write the original bytes of a landed file to ``destination``."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if landed.suffix == _ZIP:
        shutil.copyfile(landed, destination)
        return
    with pa.input_stream(str(landed), compression="zstd") as stream, destination.open("wb") as out:
        while chunk := stream.read(_CHUNK):
            out.write(chunk)


def sha256_of(landed: Path) -> str:
    """SHA-256 of the original bytes: must equal the hash in the file's name."""
    digest = hashlib.sha256()
    if landed.suffix == _ZIP:
        with landed.open("rb") as stream:
            while chunk := stream.read(_CHUNK):
                digest.update(chunk)
    else:
        with pa.input_stream(str(landed), compression="zstd") as stream:
            while chunk := stream.read(_CHUNK):
                digest.update(chunk)
    return digest.hexdigest()


def _path(root: Path, source: str, sha256: str, suffix: str) -> Path:
    # Two hex characters of fan-out keep directories small over the years.
    return layer_dir(Layer.LANDING, source, root) / sha256[:2] / f"{sha256}{suffix}"


def _write(read: Callable[[int], bytes], target: Path, *, compress: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        opened = (
            pa.output_stream(str(temporary), compression="zstd")
            if compress
            else temporary.open("wb")
        )
        with opened as out:
            while chunk := read(_CHUNK):
                out.write(chunk)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
