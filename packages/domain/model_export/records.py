"""Build records ("sổ"): what was built, degraded or skipped, and why (HOP-DONG-M1 §1, §5)."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Status = Literal["built", "degraded", "skipped"]
Severity = Literal["info", "minor", "major", "critical"]


@dataclass(frozen=True)
class Record:
    """One entity's outcome; `reason` is Vietnamese text shown verbatim by the FE."""

    level: str
    kind: str
    id: str
    status: Status
    severity: Severity
    reason: str = ""


@dataclass
class RecordBook:
    """Append-only list of records with `kind:status` counters."""

    records: list[Record] = field(default_factory=list)

    def add(self, level: str, kind: str, id: str, status: Status, severity: Severity, reason: str = "") -> Record:
        """Append and return a record."""
        rec = Record(level, kind, id, status, severity, reason)
        self.records.append(rec)
        return rec

    def counts(self) -> dict[str, int]:
        """`{"wall:built": n, ...}` sorted by key."""
        return dict(sorted(Counter(f"{r.kind}:{r.status}" for r in self.records).items()))

    def to_dicts(self) -> list[dict[str, Any]]:
        """Records as plain dicts (report.json / plan.json shape)."""
        return [asdict(r) for r in self.records]
