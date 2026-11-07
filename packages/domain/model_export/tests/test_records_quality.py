"""Record book and quality rule (HOP-DONG-M1 §5)."""

from typing import Any

import pytest

from packages.domain.model_export.defaults import ceiling_height_m
from packages.domain.model_export.quality import quality_of
from packages.domain.model_export.records import RecordBook, Severity


def test_record_book__counts_and_dicts() -> None:
    """Counts by kind:status, dicts keep every field."""
    book = RecordBook()
    book.add("L-1", "wall", "W-1", "built", "info")
    book.add("L-1", "wall", "W-2", "built", "info")
    rec = book.add("L-1", "opening", "D-1", "degraded", "major", "ô mở vượt đầu tường 12 mm")
    assert rec.reason.startswith("ô mở")
    assert book.counts() == {"opening:degraded": 1, "wall:built": 2}
    assert book.to_dicts()[2] == {
        "level": "L-1",
        "kind": "opening",
        "id": "D-1",
        "status": "degraded",
        "severity": "major",
        "reason": "ô mở vượt đầu tường 12 mm",
    }


@pytest.mark.parametrize(
    ("severities", "issues", "expected"),
    [
        ([], [], "ready"),
        (["info", "minor"], [], "ready"),
        (["minor", "major"], [], "degraded"),
        (["major", "critical"], [], "blocked"),
        (["info"], [{"object": "Wall_W-1", "open": 2}], "blocked"),
    ],
)
def test_quality_of__rule(severities: list[Severity], issues: list[dict[str, Any]], expected: str) -> None:
    """critical or geometry issue → blocked; major → degraded; else ready."""
    book = RecordBook()
    for i, s in enumerate(severities):
        book.add("L", "wall", f"W-{i}", "built", s)
    assert quality_of(book.records, issues) == expected
    assert quality_of(book.to_dicts(), issues) == expected


def test_ceiling_height_m__min_rule() -> None:
    """min(2.7, H - 0.25)."""
    assert ceiling_height_m(3.3) == 2.7
    assert ceiling_height_m(2.8) == pytest.approx(2.55)
