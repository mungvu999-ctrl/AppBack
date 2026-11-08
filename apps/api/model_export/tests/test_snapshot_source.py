"""`snapshot_source.take` ghép lớp đề xuất của document vào tầng (M2-QL), không đổi sha khi không có."""

from typing import Any

from apps.api.model_export import snapshot_source
from packages.domain.model_export.snapshot import build_snapshot, snapshot_sha

VERSIONS = snapshot_source.versions_of("c1", "p1")
LAYER: dict[str, Any] = {"walls": [], "openings": []}
SUGGESTIONS: list[dict[str, Any]] = [{"id": "sg-1", "rule": "R1", "state": "active"}]
META: dict[str, Any] = {"rulesetSha": "abc", "generatedAtRevision": 2}


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Db:
    """Phiên giả: `execute` trả các dòng cho sẵn (chỉ `take` đọc)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def execute(self, *_: Any) -> _Result:
        return _Result(self._rows)


def _row(document: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "scene_style": None,
        "level_id": "L1",
        "name": "Tầng 1",
        "floor_order": 0,
        "elevation_mm": 0,
        "height_mm": 3000,
        "hidden_in_3d": False,
        "revision": 2,
        "document": document,
    }


async def test_take__floor_with_suggestions() -> None:
    doc = {"layer": LAYER, "suggestions": SUGGESTIONS, "suggestionsMeta": META, "suggestionsEnabled": True}
    snap = await snapshot_source.take(_Db([_row(doc)]), "p", VERSIONS)  # type: ignore[arg-type]
    floor = snap.body["floors"][0]
    assert floor["suggestions"] == SUGGESTIONS
    assert floor["suggestionsMeta"] == META
    assert "suggestionsEnabled" not in floor


async def test_take__no_suggestions_keeps_m1_sha() -> None:
    plain = await snapshot_source.take(_Db([_row({"layer": LAYER})]), "p", VERSIONS)  # type: ignore[arg-type]
    empty = await snapshot_source.take(_Db([_row({"layer": LAYER, "suggestions": []})]), "p", VERSIONS)  # type: ignore[arg-type]
    m1_floor = {k: v for k, v in plain.body["floors"][0].items() if k not in ("suggestions", "suggestionsMeta")}
    m1_sha = snapshot_sha(build_snapshot("p", [m1_floor], VERSIONS))
    assert set(plain.body["floors"][0]) == {
        "levelId",
        "name",
        "order",
        "elevationMm",
        "heightMm",
        "hiddenIn3d",
        "revision",
        "layer",
    }
    assert plain.sha == empty.sha == m1_sha
