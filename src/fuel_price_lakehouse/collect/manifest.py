"""The collection manifest: what was already ingested, from where, and what it was.

One record per upstream file (an ANP file, or one request window of a Banco
Central series). The record is what makes the collection incremental: a file
whose record still matches the source is not downloaded again. It is also what
makes a run resumable, because the manifest is saved after every file, so a run
that dies halfway keeps everything it finished.

The manifest only changes on disk when a record changes. Running the collection
twice in a row leaves the file byte-identical.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

MANIFEST_VERSION = 1


@dataclass
class FileRecord:
    """What is known about one upstream file."""

    key: str
    """Stable identity: the URL for ANP files, ``bcb_sgs/<code>/<year>`` for SGS."""
    source: str
    url: str
    status: str = "failed"
    """``ok`` once the file was ingested at least once, ``failed`` until then."""
    collected_at: str | None = None
    """UTC timestamp of the download that produced the current raw file."""
    size_bytes: int | None = None
    sha256: str | None = None
    rows: int | None = None
    raw_path: str | None = None
    """Raw Parquet file, relative to the data root. ``None`` when there are no rows."""
    landing_path: str | None = None
    """The downloaded bytes in the landing zone, relative to the data root."""
    revision: int = 0
    """How many different contents were ingested from this URL. Above 1: republished."""
    details: dict[str, str] = field(default_factory=dict)
    """Source-specific attributes (period, fuel group, ETag, ...)."""
    superseded: list[dict[str, str | int | None]] = field(default_factory=list)
    """Earlier contents of this URL, oldest first: SHA-256, size, rows, when
    each was collected and where its bytes are in the landing zone. The raw
    file holds only the current content; this is the audit trail of what the
    source republished."""
    last_error: str | None = None
    """Latest failure. A record can be ``ok`` and carry an error: the previous
    content is still valid and the next run tries the new one again."""
    last_error_at: str | None = None

    @property
    def ingested(self) -> bool:
        return self.status == "ok" and self.sha256 is not None

    def usable_in(self, root: Path) -> bool:
        """Ingested, and its raw file is still on disk.

        A record without its raw file describes work that has to be done
        again: the collector must not answer "unchanged" for it.
        """
        return self.ingested and (self.raw_path is None or (root / self.raw_path).exists())

    def history_with_current(self) -> list[dict[str, str | int | None]]:
        """``superseded`` plus the current content: what a replacement must carry on."""
        if not self.ingested:
            return list(self.superseded)
        current: dict[str, str | int | None] = {
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "rows": self.rows,
            "collected_at": self.collected_at,
            "landing_path": self.landing_path,
        }
        return [*self.superseded, current]


@dataclass(frozen=True)
class Outcome:
    """What a run did about one upstream file."""

    key: str
    action: str
    """``new``, ``updated``, ``unchanged``, ``failed``, or ``planned`` (dry run)."""
    size_bytes: int = 0
    """Bytes downloaded, or bytes that a dry run found would be downloaded."""
    rows: int = 0
    detail: str = ""


class Manifest:
    """In-memory manifest bound to a JSON file."""

    def __init__(self, path: Path, files: dict[str, FileRecord] | None = None) -> None:
        self.path = path
        self.files: dict[str, FileRecord] = files or {}
        self._saved: str | None = self._dump()

    @classmethod
    def load(cls, path: Path) -> Manifest:
        if not path.exists():
            manifest = cls(path)
            manifest._saved = None  # nothing on disk yet
            return manifest
        document = json.loads(path.read_text(encoding="utf-8"))
        version = document.get("version")
        if version != MANIFEST_VERSION:
            raise ValueError(f"{path}: unsupported manifest version {version!r}")
        return cls(path, {key: FileRecord(**record) for key, record in document["files"].items()})

    def get(self, key: str) -> FileRecord | None:
        return self.files.get(key)

    def put(self, record: FileRecord) -> None:
        self.files[record.key] = record

    def record_failure(
        self, key: str, source: str, url: str, error: str, at: str, details: dict[str, str]
    ) -> None:
        """Note a failure without losing what is known about a previous good version."""
        record = self.files.get(key) or FileRecord(key=key, source=source, url=url, details=details)
        record.last_error = error
        record.last_error_at = at
        self.files[key] = record

    def save(self) -> bool:
        """Write the manifest if it changed. Returns whether it was written.

        The write goes to a temporary file that then replaces the old one, so a
        crash mid-write never leaves a truncated manifest behind.
        """
        text = self._dump()
        if text == self._saved:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(self.path)
        self._saved = text
        return True

    def _dump(self) -> str:
        document = {
            "version": MANIFEST_VERSION,
            "files": {key: asdict(self.files[key]) for key in sorted(self.files)},
        }
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
