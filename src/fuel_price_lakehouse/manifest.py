"""The collection manifest: one line per raw file ever ingested.

This is the memory of the pipeline. A run compares what the sources offer with
what is recorded here and only fetches the difference. Entries are append-only:
when a source republishes a file with different content, a new entry is added
and the old one is kept, so the history of what was ingested is never lost.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Entry:
    source: str
    dataset: str
    period: str
    url: str
    filename: str
    size_bytes: int
    sha256: str
    raw_path: str
    """Location of the raw file, relative to the data root."""
    fetched_at: str
    """UTC timestamp, ISO 8601."""
    details: dict[str, Any] = field(default_factory=dict)
    """Source-specific facts, for example the date window of an API request."""


class Manifest:
    def __init__(self, path: Path, entries: list[Entry] | None = None) -> None:
        self.path = path
        self._entries: list[Entry] = list(entries or [])

    @classmethod
    def load(cls, path: Path) -> Manifest:
        """Read the manifest at ``path``. A missing file is an empty manifest."""
        if not path.exists():
            return cls(path)
        entries = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entries.append(Entry(**json.loads(line)))
            except (json.JSONDecodeError, TypeError) as error:
                raise ValueError(f"{path}:{number}: invalid manifest line: {error}") from error
        return cls(path, entries)

    @property
    def entries(self) -> tuple[Entry, ...]:
        return tuple(self._entries)

    def latest(self, url: str) -> Entry | None:
        """Most recent entry recorded for ``url``."""
        matches = [entry for entry in self._entries if entry.url == url]
        return max(matches, key=lambda entry: entry.fetched_at, default=None)

    def has(self, url: str, sha256: str) -> bool:
        """Whether this exact content was already ingested from ``url``."""
        return any(entry.url == url and entry.sha256 == sha256 for entry in self._entries)

    def for_dataset(self, source: str, dataset: str) -> list[Entry]:
        return [e for e in self._entries if e.source == source and e.dataset == dataset]

    def add(self, entry: Entry) -> None:
        self._entries.append(entry)

    def save(self) -> None:
        """Write atomically: a crash mid-write leaves the previous manifest intact."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(asdict(e), ensure_ascii=False, sort_keys=True) for e in self._entries]
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        temporary.replace(self.path)
