"""Result quality from records + post-build geometry check (HOP-DONG-M1 §5)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from packages.domain.model_export.records import Record

Quality = Literal["ready", "degraded", "blocked"]


def quality_of(records: Iterable[Record | Mapping[str, Any]], geometry_issues: Iterable[Any] = ()) -> Quality:
    """`blocked` if any critical record or any geometry issue (open/degenerate architecture
    is critical per §4.6), `degraded` if any major, else `ready`."""
    sev = {r.severity if isinstance(r, Record) else r["severity"] for r in records}
    if "critical" in sev or any(True for _ in geometry_issues):
        return "blocked"
    return "degraded" if "major" in sev else "ready"
