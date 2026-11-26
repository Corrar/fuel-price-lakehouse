"""The raw layer: a faithful Parquet copy of what each source delivered.

Nothing is interpreted here. Every source column is stored as text, exactly as
it came: no trimming, no number or date parsing, empty strings stay empty
strings. The only additions are tracking columns (prefixed with ``_``) that tie
each row back to the file it came from. Typing and cleaning belong to the
staged layer.

Files are written to a temporary name and renamed at the end, so a raw file
either exists complete or does not exist.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any

import pyarrow as pa
import pyarrow.parquet as pq

_BATCH_ROWS = 50_000

# ANP files are ";"-separated and quoted only where needed. Nearly all are
# UTF-8 with a BOM; a few have no BOM and are Windows-1252.
_UTF8_BOM = b"\xef\xbb\xbf"
_ZIP_MAGIC = b"PK\x03\x04"
_ENCODING_NAMES = {"utf-8-sig": "utf-8", "utf-8": "utf-8", "cp1252": "cp1252"}
_ANP_DELIMITER = ";"


class CorruptFileError(Exception):
    """The downloaded content is not what the source is supposed to deliver."""


@dataclass(frozen=True)
class Provenance:
    """Where a raw file came from. Becomes the tracking columns."""

    source_url: str
    sha256: str
    collected_at: datetime


@dataclass(frozen=True)
class CsvCopy:
    """How an ANP file was read. Recorded in the manifest next to the file."""

    rows: int
    encoding: str
    """``utf-8`` for almost every file; ``cp1252`` for the few published in it."""
    lenient: bool
    """True when the strict CSV reader refused the file and the lenient one was used."""


def write_anp_csv(downloaded: Path, destination: Path, provenance: Provenance) -> CsvCopy:
    """Copy an ANP file (plain CSV, or a ZIP holding one CSV) to Parquet.

    Besides the common tracking columns, two more keep rows with an unexpected
    number of fields lossless instead of rejecting or silently reshaping them:

    - ``_field_count``: fields found in the row;
    - ``_extra_fields``: the fields beyond the header, when there are any.
      A row with fewer fields than the header has nulls in the missing columns
      (an empty field in the file is an empty string, never null).

    Two things vary between files and are handled here rather than failing the
    whole file, because one odd file must not stop twenty years of history:

    - encoding: a UTF-8 BOM settles it. Without one, UTF-8 is tried first and
      Windows-1252 second (the second semester of 2021 is such a file). A file
      that announces UTF-8 with a BOM and then is not valid UTF-8 is corrupt:
      reading it as something else would turn its accents into noise.
    - quoting: the strict CSV reader is tried first; if a file has malformed
      quoting the lenient reader is used, and any row it splits into the wrong
      number of fields is visible through ``_field_count``. The lenient reader
      is not allowed to swallow line breaks into a field, which is how an
      unclosed quote silently merges rows; that fails the file instead.

    Which path was taken is returned, and ends up in the manifest.
    """
    encodings = ("utf-8-sig",) if _starts_with_bom(downloaded) else ("utf-8", "cp1252")
    failure: Exception | None = None
    for encoding in encodings:
        try:
            for strict in (True, False):
                try:
                    rows = _copy_csv(downloaded, destination, provenance, encoding, strict)
                    return CsvCopy(rows, _ENCODING_NAMES[encoding], lenient=not strict)
                except csv.Error as error:
                    failure = error
            break  # both readers failed: another encoding will not help
        except UnicodeDecodeError as error:
            failure = error
    raise CorruptFileError(f"{downloaded.name}: not a readable CSV: {failure}") from failure


def _starts_with_bom(downloaded: Path) -> bool:
    with _open_csv_bytes(downloaded) as stream:
        return stream.read(len(_UTF8_BOM)) == _UTF8_BOM


def _copy_csv(
    downloaded: Path, destination: Path, provenance: Provenance, encoding: str, strict: bool
) -> int:
    with _open_csv_bytes(downloaded) as stream:
        text = io.TextIOWrapper(stream, encoding=encoding, newline="")
        reader: Iterator[list[str]] = csv.reader(text, delimiter=_ANP_DELIMITER, strict=strict)
        if not strict:
            reader = _without_merged_lines(reader, downloaded)
        header = _checked_header(next(reader, None), downloaded)
        schema = _schema(
            header,
            [pa.field("_field_count", pa.int32()), pa.field("_extra_fields", pa.string())],
        )
        return _write(destination, schema, _anp_batches(reader, header), provenance)


def _without_merged_lines(rows: Iterator[list[str]], path: Path) -> Iterator[list[str]]:
    for number, row in enumerate(rows, start=1):
        if any("\n" in value or "\r" in value for value in row):
            raise CorruptFileError(
                f"{path.name}: unbalanced quote near record {number}: lines would be merged"
            )
        yield row


def write_json_records(body: bytes, destination: Path, provenance: Provenance) -> int:
    """Copy a JSON array of flat objects (the SGS response shape) to Parquet.

    Values are kept as the text the API sent. Returns the number of records.
    """
    records = parse_json_records(body)
    names = list(dict.fromkeys(key for record in records for key in record))
    columns = {name: [_as_text(record.get(name)) for record in records] for name in names}
    return _write(destination, _schema(names, []), iter([(columns, len(records))]), provenance)


def parse_json_records(body: bytes) -> list[dict[str, object]]:
    try:
        records = json.loads(body)
    except ValueError as error:
        raise CorruptFileError(f"not valid JSON: {error}") from error
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise CorruptFileError("expected a JSON array of objects")
    return records


def _as_text(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


@contextmanager
def _open_csv_bytes(path: Path) -> Iterator[IO[bytes]]:
    if not zipfile.is_zipfile(path):
        with path.open("rb") as stream:
            if stream.read(len(_ZIP_MAGIC)) == _ZIP_MAGIC:
                raise CorruptFileError(f"{path.name}: damaged ZIP")
            stream.seek(0)
            yield stream
        return
    try:
        with zipfile.ZipFile(path) as archive:
            # Member names are unreliable (non-ASCII, no UTF-8 flag): go by position.
            members = [member for member in archive.infolist() if not member.is_dir()]
            if len(members) != 1:
                raise CorruptFileError(
                    f"{path.name}: expected 1 file in the ZIP, got {len(members)}"
                )
            with archive.open(members[0]) as stream:
                yield stream
    except (zipfile.BadZipFile, EOFError) as error:
        raise CorruptFileError(f"{path.name}: damaged ZIP: {error}") from error


def _checked_header(header: list[str] | None, path: Path) -> list[str]:
    if not header or len(header) < 2 or header[0].lstrip().startswith("<"):
        raise CorruptFileError(f"{path.name}: no CSV header found")
    if len(set(header)) != len(header) or any(name.startswith("_") for name in header):
        raise CorruptFileError(f"{path.name}: unusable header {header!r}")
    return header


def _anp_batches(
    reader: Iterable[list[str]], header: list[str]
) -> Iterator[tuple[dict[str, list[Any]], int]]:
    width = len(header)

    def empty() -> dict[str, list[Any]]:
        return {name: [] for name in [*header, "_field_count", "_extra_fields"]}

    columns, rows = empty(), 0
    for row in reader:
        for index, name in enumerate(header):
            columns[name].append(row[index] if index < len(row) else None)
        columns["_field_count"].append(len(row))
        extra = row[width:]
        columns["_extra_fields"].append(json.dumps(extra, ensure_ascii=False) if extra else None)
        rows += 1
        if rows == _BATCH_ROWS:
            yield columns, rows
            columns, rows = empty(), 0
    if rows:
        yield columns, rows


def _schema(source_columns: list[str], extra: list[pa.Field]) -> pa.Schema:
    return pa.schema(
        [
            *(pa.field(name, pa.string()) for name in source_columns),
            *extra,
            pa.field("_row_number", pa.int64()),
            pa.field("_source_url", pa.string()),
            pa.field("_source_sha256", pa.string()),
            pa.field("_collected_at", pa.timestamp("us", tz="UTC")),
        ]
    )


def _write(
    destination: Path,
    schema: pa.Schema,
    batches: Iterator[tuple[dict[str, list[Any]], int]],
    provenance: Provenance,
) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    total = 0
    try:
        with pq.ParquetWriter(temporary, schema, compression="zstd") as writer:
            for columns, rows in batches:
                columns["_row_number"] = list(range(total + 1, total + rows + 1))
                columns["_source_url"] = [provenance.source_url] * rows
                columns["_source_sha256"] = [provenance.sha256] * rows
                columns["_collected_at"] = [provenance.collected_at] * rows
                arrays = [pa.array(columns[field.name], type=field.type) for field in schema]
                writer.write_batch(pa.record_batch(arrays, schema=schema))
                total += rows
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return total
