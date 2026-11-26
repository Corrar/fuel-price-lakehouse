"""A small rule engine: named checks that decide which rows reach the staged layer.

A rule looks at a whole frame at once and answers, per row, "does this row
pass?". Rules have a severity:

- ``error``: the row cannot be trusted. It goes to quarantine with the ids of
  every error rule it broke, and never reaches the staged layer.
- ``warning``: the row is kept, and the rule id is recorded on it.

Rule ids are a public contract: they are stored in the quarantine files,
counted in the quality report and listed in ``docs/quality-rules.md``. Renaming
one is a breaking change.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import numpy as np
import pandas as pd


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class FileContext:
    """What is known about the raw file a frame came from (the manifest record)."""

    key: str
    source_url: str
    source_sha256: str
    collected_at: datetime
    details: Mapping[str, str]
    """Source-specific attributes: ``kind``, ``fuel_group`` and ``period`` for
    ANP files; ``series`` and ``year`` for SGS windows."""


Check = Callable[[pd.DataFrame, FileContext], pd.Series]
"""Returns a boolean Series aligned with the frame: True where the row passes."""


@dataclass(frozen=True)
class Rule:
    id: str
    severity: Severity
    description: str
    """One plain sentence saying what a valid row looks like (shown publicly)."""
    description_pt: str
    check: Check


@dataclass(frozen=True)
class Evaluation:
    """Per-row outcome of a rule set over one frame."""

    error_ids: pd.Series
    """For each row, the list of error rules it broke (empty list when clean)."""
    warning_ids: pd.Series
    """For each row, the list of warning rules it triggered."""
    error_counts: dict[str, int]
    """Rows failing each error rule. Every error rule is present, zeros included."""
    warning_counts: dict[str, int]

    @property
    def rejected(self) -> pd.Series:
        return self.error_ids.map(bool)


def evaluate(rules: Sequence[Rule], frame: pd.DataFrame, context: FileContext) -> Evaluation:
    """Apply every rule to ``frame``.

    A rule that returns something other than one boolean per row, in the row
    order of the frame, is a bug in the rule, not bad data, and raises: results
    are matched to rows by position.
    """
    ids = [rule.id for rule in rules]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate rule ids: {sorted(i for i in ids if ids.count(i) > 1)}")

    failed: dict[Severity, dict[str, np.ndarray]] = {Severity.ERROR: {}, Severity.WARNING: {}}
    for rule in rules:
        passed = rule.check(frame, context)
        if passed.dtype != bool or not passed.index.equals(frame.index):
            raise TypeError(
                f"rule {rule.id!r} must return one bool per row, aligned with the frame "
                f"(got dtype {passed.dtype}, {len(passed)} values for {len(frame)} rows)"
            )
        failed[rule.severity][rule.id] = ~passed.to_numpy()

    return Evaluation(
        error_ids=_ids_per_row(failed[Severity.ERROR], frame.index),
        warning_ids=_ids_per_row(failed[Severity.WARNING], frame.index),
        error_counts={rule_id: int(mask.sum()) for rule_id, mask in failed[Severity.ERROR].items()},
        warning_counts={
            rule_id: int(mask.sum()) for rule_id, mask in failed[Severity.WARNING].items()
        },
    )


def _ids_per_row(failed: dict[str, np.ndarray], index: pd.Index) -> pd.Series:
    lists: list[list[str]] = [[] for _ in range(len(index))]
    for rule_id, mask in failed.items():
        for position in np.flatnonzero(mask):
            lists[position].append(rule_id)
    return pd.Series(lists, index=index, dtype=object)
