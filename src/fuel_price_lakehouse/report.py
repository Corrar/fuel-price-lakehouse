"""What a collection run did, in a form that can be printed and tested."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
    DOWNLOADED = "downloaded"
    UNCHANGED = "unchanged"
    EMPTY = "empty"
    PLANNED = "planned"
    FAILED = "failed"


@dataclass(frozen=True)
class Outcome:
    source: str
    dataset: str
    period: str
    status: Status
    size_bytes: int = 0
    detail: str = ""


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)

    def count(self, status: Status) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status is status)

    @property
    def failed(self) -> bool:
        return self.count(Status.FAILED) > 0

    def extend(self, other: Report) -> None:
        self.outcomes += other.outcomes
        self.warnings += other.warnings

    def summary(self) -> str:
        lines = []
        for outcome in self.outcomes:
            size = f"{outcome.size_bytes:>12,d} B" if outcome.size_bytes else " " * 14
            detail = f"  {outcome.detail}" if outcome.detail else ""
            lines.append(
                f"{outcome.status.value:<10} {outcome.source:<8} {outcome.dataset:<18} "
                f"{outcome.period:<24} {size}{detail}"
            )
        lines += [f"warning: {warning}" for warning in self.warnings]
        counts = ", ".join(
            f"{self.count(status)} {status.value}" for status in Status if self.count(status)
        )
        fetched = sum(o.size_bytes for o in self.outcomes if o.status is Status.DOWNLOADED)
        lines.append(f"total: {counts or 'nothing to do'}; {fetched:,d} bytes downloaded")
        return "\n".join(lines)
