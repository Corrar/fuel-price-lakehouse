"""Bring the staged layer and the quarantine up to date with the raw layer.

The driver walks the collection manifest and hands each raw file to the staging
module of its source, one file at a time so that memory is bounded by the
largest file. What came out is recorded in ``manifest/staging.json``. Staging
is a pure function of the raw file, its manifest record and ``STAGING_VERSION``,
so that record is enough to know what does not need doing again.

- Which files are staged: a file that was never staged; a file whose raw
  SHA-256 is not the one recorded (the source republished it); every file after
  a bump of ``STAGING_VERSION``; a file with an output listed in the state but
  gone from disk; a file whose last attempt failed; every file under
  ``full_refresh``. Any other file is ``unchanged`` and is not even opened.
- What fails one file and not the run: a raw file without a column its source
  is known to publish (``SchemaDriftError``), and a raw file the manifest lists
  that is not on disk. The state entry then carries the ``error`` and no row
  counts, the file has no outputs (older ones are deleted: they describe a
  content that is no longer the current one) and the outcome is ``failed``.
  Such a file is tried again on every run, so it recovers by itself once the
  cause is gone; while the failure stays the same, its entry is not rewritten.
  Anything else, like a staged frame that breaks its contract or a stager that
  loses rows, is a bug in the pipeline and raises.
- How a crash is survived: output files are written under a temporary name and
  renamed, and the state is saved after every file, always after the outputs it
  lists. A run that dies keeps what it finished and redoes the file it was on.
- Nothing is rewritten for nothing: the state is saved only when it changed, so
  a run with nothing new leaves ``staging.json`` and every Parquet file
  untouched, byte for byte.
- Empty outputs do not exist: a staged or quarantine file that would have no
  rows is not written, one left by an earlier content is deleted, and its path
  in the state is null. Readers take the list of files from the state, never
  from a directory listing.
- The state follows the manifest: a record that is no longer there is dropped
  from the state together with its output files.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Protocol

import pandas as pd
import pyarrow as pa

from fuel_price_lakehouse.collect.manifest import Manifest
from fuel_price_lakehouse.config import Clock, utc_now
from fuel_price_lakehouse.layout import (
    Layer,
    Source,
    manifest_path,
    sibling_in_layer,
    staging_state_path,
)
from fuel_price_lakehouse.stage.model import SchemaDriftError, StagedFrames, write_parquet
from fuel_price_lakehouse.stage.rules import FileContext

STAGING_VERSION = 1
"""Bump when the rules or a staged schema change: every file is staged again."""

STATE_VERSION = 1

logger = logging.getLogger(__name__)


class Stager(Protocol):
    """What the driver needs from the staging module of a source."""

    STAGED_ARROW_SCHEMA: pa.Schema
    QUARANTINE_ARROW_SCHEMA: pa.Schema

    def stage(self, raw: pd.DataFrame, context: FileContext, *, now: Clock) -> StagedFrames: ...


def _anp() -> Stager:
    from fuel_price_lakehouse.stage import anp

    return anp


def _bcb_sgs() -> Stager:
    from fuel_price_lakehouse.stage import bcb_sgs

    return bcb_sgs


STAGERS: dict[str, Callable[[], Stager]] = {Source.ANP: _anp, Source.BCB_SGS: _bcb_sgs}
"""How to get the stager of each source. A staging module is imported only when
a file of its source has to be staged, so a run with nothing to do stays cheap."""


@dataclass(frozen=True)
class StageOutcome:
    """What a run did about one raw file.

    The row counts describe the file as it now stands in the staged layer,
    also when the run left it unchanged. A failed file has none.
    """

    key: str
    source: str
    action: str
    """``staged``, ``unchanged`` or ``failed``."""
    rows_read: int = 0
    rows_staged: int = 0
    rows_quarantined: int = 0
    detail: str = ""


@dataclass(frozen=True)
class StagedFile:
    """The state entry of one raw file: what staging made of it."""

    source: str
    details: dict[str, str]
    """The details of the manifest record at the time the file was staged."""
    raw_sha256: str
    staging_version: int
    staged_at: str
    """UTC timestamp of the staging. For a failure that keeps repeating, of the first one."""
    staged_path: str | None = None
    """Relative to the data root, like every path here. ``None``: no row passed."""
    quarantine_path: str | None = None
    rows_read: int = 0
    rows_staged: int = 0
    rows_quarantined: int = 0
    rule_failures: dict[str, int] = field(default_factory=dict)
    """Rows that broke each error rule. A row can count under several rules."""
    rule_warnings: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    """Why the file as a whole could not be staged."""

    @property
    def outputs(self) -> list[str]:
        return [path for path in (self.staged_path, self.quarantine_path) if path is not None]


class StagingState:
    """``manifest/staging.json`` in memory, with the discipline of the collection manifest."""

    def __init__(self, path: Path, files: dict[str, StagedFile] | None = None) -> None:
        self.path = path
        self.files: dict[str, StagedFile] = files or {}
        self._saved: str | None = self._dump()

    @classmethod
    def load(cls, path: Path) -> StagingState:
        if not path.exists():
            state = cls(path)
            state._saved = None  # nothing on disk yet
            return state
        document = json.loads(path.read_text(encoding="utf-8"))
        version = document.get("version")
        if version != STATE_VERSION:
            raise ValueError(f"{path}: unsupported staging state version {version!r}")
        return cls(path, {key: StagedFile(**entry) for key, entry in document["files"].items()})

    def save(self) -> bool:
        """Write the state if it changed, atomically. Returns whether it was written."""
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
            "version": STATE_VERSION,
            "files": {key: asdict(self.files[key]) for key in sorted(self.files)},
        }
        return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


class _RawFileMissingError(Exception):
    """The manifest lists a raw file that is not on disk."""


@dataclass(frozen=True)
class _RawFile:
    """A manifest record that has rows to stage."""

    source: str
    raw_path: str
    context: FileContext


def run(root: Path, *, now: Clock = utc_now, full_refresh: bool = False) -> list[StageOutcome]:
    """Stage every raw file that needs it and return what happened to each one."""
    files = _raw_files(Manifest.load(manifest_path(root)))
    state = StagingState.load(staging_state_path(root))
    _forget_departed(root, state, {file.context.key for file in files})
    state.save()

    outcomes = []
    for file in files:
        outcome = _bring_up_to_date(root, file, state, now, full_refresh)
        state.save()  # after every file: a run that dies keeps what it finished
        outcomes.append(outcome)
        level = logging.ERROR if outcome.action == "failed" else logging.INFO
        logger.log(level, "stage: %-9s %s %s", outcome.action, outcome.key, outcome.detail)
    return outcomes


def _raw_files(manifest: Manifest) -> list[_RawFile]:
    """What there is to stage: records ingested at least once that have a raw file.

    A record that never got past a failed download has nothing yet, and an SGS
    window with no observations has no raw file.
    """
    files = []
    for record in manifest.files.values():
        # ``ingested`` implies the hash and the timestamp; naming them tells the type checker.
        if record.ingested and record.sha256 and record.collected_at and record.raw_path:
            context = FileContext(
                key=record.key,
                source_url=record.url,
                source_sha256=record.sha256,
                collected_at=datetime.fromisoformat(record.collected_at),
                details=record.details,
            )
            files.append(_RawFile(record.source, record.raw_path, context))
    return files


def _forget_departed(root: Path, state: StagingState, keys: set[str]) -> None:
    """Drop the entries of records that left the manifest, and their output files."""
    for key in sorted(state.files.keys() - keys):
        for path in state.files.pop(key).outputs:
            (root / path).unlink(missing_ok=True)
        logger.info("stage: %-9s %s", "removed", key)


def _bring_up_to_date(
    root: Path, file: _RawFile, state: StagingState, now: Clock, full_refresh: bool
) -> StageOutcome:
    key = file.context.key
    previous = state.files.get(key)
    if previous is not None and not full_refresh and _up_to_date(root, file, previous):
        return _outcome(key, previous, "unchanged")
    try:
        entry = _stage(root, file, now)
    except (SchemaDriftError, _RawFileMissingError) as error:
        entry = replace(_entry(file, now), error=str(error))
        if previous is not None and replace(previous, staged_at=entry.staged_at) == entry:
            entry = previous  # the same failure again: keep the record of the first time
    _delete_obsolete_outputs(root, file, previous, entry)
    state.files[key] = entry
    return _outcome(key, entry, "failed" if entry.error else "staged")


def _up_to_date(root: Path, file: _RawFile, entry: StagedFile) -> bool:
    return (
        entry.error is None
        and entry.raw_sha256 == file.context.source_sha256
        and entry.staging_version == STAGING_VERSION
        and all((root / path).is_file() for path in entry.outputs)
    )


def _stage(root: Path, file: _RawFile, now: Clock) -> StagedFile:
    """Stage one raw file, write its outputs and describe them."""
    raw_file = root / file.raw_path
    if not raw_file.is_file():
        raise _RawFileMissingError(
            f"raw file is missing: {file.raw_path}; run the collection again"
        )
    raw = pd.read_parquet(raw_file)
    stager = STAGERS[file.source]()
    frames = stager.stage(raw, file.context, now=now)
    rows_staged, rows_quarantined = len(frames.staged), len(frames.quarantine)
    if rows_staged + rows_quarantined != len(raw):
        # Nothing is dropped silently: this is the one place every source goes through.
        raise RuntimeError(
            f"{file.context.key}: {len(raw):,} rows read, but {rows_staged:,} staged"
            f" and {rows_quarantined:,} quarantined"
        )
    return replace(
        _entry(file, now),
        staged_path=_write(root, frames.staged, file, Layer.STAGED, stager.STAGED_ARROW_SCHEMA),
        quarantine_path=_write(
            root, frames.quarantine, file, Layer.QUARANTINE, stager.QUARANTINE_ARROW_SCHEMA
        ),
        rows_read=len(raw),
        rows_staged=rows_staged,
        rows_quarantined=rows_quarantined,
        rule_failures=frames.error_counts,
        rule_warnings=frames.warning_counts,
    )


def _write(
    root: Path, frame: pd.DataFrame, file: _RawFile, layer: Layer, schema: pa.Schema
) -> str | None:
    """Write the rows to the file's place in ``layer``. ``None`` when there are no rows."""
    if len(frame) == 0:
        return None
    relative = sibling_in_layer(file.raw_path, layer)
    write_parquet(frame, root / relative, schema)
    return relative


def _entry(file: _RawFile, now: Clock) -> StagedFile:
    """The entry of ``file`` as it is now, before anything is known about its rows."""
    return StagedFile(
        source=file.source,
        details=dict(file.context.details),
        raw_sha256=file.context.source_sha256,
        staging_version=STAGING_VERSION,
        staged_at=now().isoformat(),
    )


def _delete_obsolete_outputs(
    root: Path, file: _RawFile, previous: StagedFile | None, entry: StagedFile
) -> None:
    """Leave on disk only the outputs ``entry`` lists.

    Covers the places a file of this record can be: where its outputs go now
    (a file with no rejected rows any more, a failed file, a run that died
    before saving the state) and where the previous staging put them.
    """
    candidates = {
        sibling_in_layer(file.raw_path, layer) for layer in (Layer.STAGED, Layer.QUARANTINE)
    }
    if previous is not None:
        candidates.update(previous.outputs)
    for path in candidates.difference(entry.outputs):
        (root / path).unlink(missing_ok=True)


def _outcome(key: str, entry: StagedFile, action: str) -> StageOutcome:
    return StageOutcome(
        key,
        entry.source,
        action,
        entry.rows_read,
        entry.rows_staged,
        entry.rows_quarantined,
        entry.error or f"{entry.rows_staged:,} staged, {entry.rows_quarantined:,} quarantined",
    )
