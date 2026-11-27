"""What the pipeline publishes as release assets, assembled in one folder.

The scheduled workflow uploads this folder to the ``data-latest`` release
(ADR 0002): the public tables, a public view of the collection manifest, and
the quality history. Building it here, in tested code, keeps the decision of
what leaves the machine out of shell scripts.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fuel_price_lakehouse.collect.manifest import FileRecord, Manifest
from fuel_price_lakehouse.curate.tables import export_public
from fuel_price_lakehouse.layout import manifest_path, quality_dir
from fuel_price_lakehouse.quality.report import HISTORY_NAME

MANIFEST_NAME = "collection.json"

PUBLIC_RECORD_FIELDS = (
    "key",
    "source",
    "url",
    "status",
    "collected_at",
    "size_bytes",
    "sha256",
    "rows",
    "revision",
)
"""What a reader needs to check the published tables against the sources: the
exact files and their hashes. Not the local paths, the source-specific details,
or the text of the last error, which can quote a broken file."""

PUBLIC_SUPERSEDED_FIELDS = ("sha256", "size_bytes", "rows", "collected_at")


def public_manifest(manifest: Manifest) -> dict[str, Any]:
    """The collection manifest without anything that is local or could quote data."""
    return {
        "version": 1,
        "files": {key: _public_record(manifest.files[key]) for key in sorted(manifest.files)},
    }


def write_release(root: Path, out: Path) -> list[Path]:
    """Write the release assets of the data under ``root`` into ``out``. Returns them."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    written = export_public(root, out)
    collection = out / MANIFEST_NAME
    document = public_manifest(Manifest.load(manifest_path(root)))
    collection.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", "utf-8")
    written.append(collection)
    history = quality_dir(root) / HISTORY_NAME
    if history.exists():
        written.append(Path(shutil.copyfile(history, out / HISTORY_NAME)))
    return written


def _public_record(record: FileRecord) -> dict[str, Any]:
    public = {field: getattr(record, field) for field in PUBLIC_RECORD_FIELDS}
    public["superseded"] = [
        {field: version.get(field) for field in PUBLIC_SUPERSEDED_FIELDS}
        for version in record.superseded
    ]
    return public
