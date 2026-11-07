"""Ô mở → cutter + assembly (HOP-DONG-M1 §1, §1.1, §4.4)."""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from packages.domain.model_export.materials import MaterialResolver
from packages.domain.model_export.plan.openings import openings_plan
from packages.domain.model_export.records import RecordBook

LEVEL = "L-1"


def _walls(*pieces: dict[str, Any]) -> SimpleNamespace:
    """WallPlan giả: chỉ cần `pieces` (dict của A2)."""
    return SimpleNamespace(pieces=list(pieces))


def _wall(wid: str = "W-1", curved: bool = False, height: float = 3.0) -> dict[str, Any]:
    """Tường thẳng 4 m dọc +X (mét), dày 0,2 m."""
    return {"id": wid, "centreline": ((0.0, 0.0), (4.0, 0.0)), "thicknessM": 0.2, "heightM": height, "curved": curved}


def _opening(**kw: Any) -> dict[str, Any]:
    """Cửa đi 900 x 2100 tại offset 1000 mm."""
    base = {
        "id": "D-1",
        "wallId": "W-1",
        "kind": "door",
        "offsetMm": 1000,
        "widthMm": 900,
        "heightMm": 2100,
        "sillHeightMm": 0,
        "swing": "left",
    }
    return {**base, **kw}


def _run(
    openings: list[dict[str, Any]], walls: SimpleNamespace | None = None, rooms: list[dict[str, Any]] | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Chạy `openings_plan` với resolver rỗng (mọi vật liệu mặc định)."""
    book = RecordBook()
    cutters, assemblies = openings_plan(
        openings, walls or _walls(_wall()), rooms or [], LEVEL, MaterialResolver({}, {}), book
    )
    return cutters, assemblies, book.to_dicts()


def _box(asm: dict[str, Any], suffix: str) -> dict[str, Any]:
    """Hộp có tên kết thúc bằng `suffix`."""
    return next(b for b in asm["boxes"] if b["name"].endswith(suffix))


def test_openings_plan__door_left_cutter_exact_and_hinge_pivot() -> None:
    cutters, assemblies, records = _run([_opening()])
    assert cutters == [
        {"target": "Wall_W-1", "box": {"c": [1.45, 0.0, 1.05], "size": [0.9, 0.4, 2.1], "angle": 0.0}, "appId": "D-1"}
    ]
    (asm,) = assemblies
    assert asm["name"] == "Door_D-1"
    assert asm["group"] == "Doors"
    props = asm["props"]
    assert {k: props[k] for k in ("appId", "kind", "swing", "doorStyle", "widthM", "heightM", "sillM", "hostWall")} == {
        "appId": "D-1",
        "kind": "door",
        "swing": "left",
        "doorStyle": "",
        "widthM": 0.9,
        "heightM": 2.1,
        "sillM": 0.0,
        "hostWall": "W-1",
    }
    (pivot,) = asm["pivots"]
    assert pivot["props"]["hinge"] == "left"
    assert pivot["at"][0] == pytest.approx(1.0 + 0.045)
    assert all(b["props"]["prov_assembly"] == "default" for b in asm["boxes"])
    assert _box(asm, "Leaf0_Slab")["parent"] == "Door_D-1_Leaf0"
    assert _box(asm, "Threshold")["material"] == "default:stone"
    assert records == [
        {"level": LEVEL, "kind": "door", "id": "D-1", "status": "built", "severity": "info", "reason": ""}
    ]


def test_openings_plan__door_right_hinge_at_far_end() -> None:
    _, (asm,), _ = _run([_opening(swing="right")])
    (pivot,) = asm["pivots"]
    assert pivot["props"]["hinge"] == "right"
    assert pivot["at"][0] == pytest.approx(1.9 - 0.045)
    assert _box(asm, "Leaf0_Slab")["c"][0] < pivot["at"][0]


def test_openings_plan__door_double_two_leaves() -> None:
    _, (asm,), _ = _run([_opening(swing="double")])
    assert [p["props"]["hinge"] for p in asm["pivots"]] == ["left", "right"]
    assert _box(asm, "Leaf0_Slab")["size"][0] == pytest.approx((0.9 - 0.09) / 2)


@pytest.mark.parametrize("style", [None, "pocket"])
def test_openings_plan__sliding_door_two_glass_panels(style: str | None) -> None:
    _, (asm,), records = _run([_opening(swing="sliding", doorStyle=style)])
    assert [p["props"]["operable"] for p in asm["pivots"]] == ["slide", "slide"]
    assert _box(asm, "Panel1_Glass")["material"] == "default:glass"
    assert records[0]["status"] == "built"


def test_openings_plan__folding_door_degraded_minor() -> None:
    _, (asm,), records = _run([_opening(swing="sliding", doorStyle="folding")])
    assert len(asm["pivots"]) == 2
    assert records[0] == {
        "level": LEVEL,
        "kind": "door",
        "id": "D-1",
        "status": "degraded",
        "severity": "minor",
        "reason": "cửa xếp dựng như 2 tấm",
    }


ROOM_BELOW = {
    "id": "R-1",
    "outline": [{"x": 0, "y": 0}, {"x": 4000, "y": 0}, {"x": 4000, "y": 3000}, {"x": 0, "y": 3000}],
}
"""Phòng ở y mm dương → Y mét âm → phía -n của tường dọc +X."""
ROOM_ABOVE = {
    "id": "R-2",
    "outline": [{"x": 0, "y": 0}, {"x": 4000, "y": 0}, {"x": 4000, "y": -3000}, {"x": 0, "y": -3000}],
}


@pytest.mark.parametrize(
    ("swing", "sashes", "rooms", "sill_sign"),
    [("sliding", 2, [ROOM_BELOW], -1), ("fixed", 1, [ROOM_ABOVE], 1), ("left", 1, [], 1), ("double", 2, [], 1)],
)
def test_openings_plan__window_sashes_and_inside_sill(
    swing: str, sashes: int, rooms: list[dict[str, Any]], sill_sign: int
) -> None:
    win = _opening(id="O-1", kind="window", sillHeightMm=900, heightMm=1200, swing=swing)
    cutters, (asm,), records = _run([win], rooms=rooms)
    assert cutters[0]["box"]["c"][2] == pytest.approx(1.5)
    assert asm["name"] == "Window_O-1"
    assert asm["group"] == "Windows"
    assert len(asm["pivots"]) == sashes
    sill = _box(asm, "_Sill")
    assert math.copysign(1, sill["c"][1]) == sill_sign
    assert sill["c"][2] == pytest.approx(0.885)
    assert records[0]["status"] == "built"


def test_openings_plan__overrun_wall_end_not_clamped() -> None:
    cutters, assemblies, records = _run([_opening(offsetMm=3500, widthMm=900)])
    assert cutters[0]["box"]["c"][0] == pytest.approx(3.95)
    assert cutters[0]["box"]["size"][0] == pytest.approx(0.9)
    assert len(assemblies) == 1
    assert records[0]["status"] == "degraded"
    assert records[0]["severity"] == "major"
    assert records[0]["reason"] == "ô mở vượt đầu tường 400 mm"


def test_openings_plan__negative_offset_and_over_height_both_reported() -> None:
    cutters, _, records = _run([_opening(offsetMm=-200, heightMm=3200)])
    assert cutters[0]["box"]["c"] == [pytest.approx(0.25), 0.0, pytest.approx(1.6)]
    assert cutters[0]["box"]["size"][2] == pytest.approx(3.2)
    assert records[0]["reason"] == "ô mở vượt đầu tường 200 mm; ô mở vượt cao tường 200 mm"


def test_openings_plan__within_one_mm_tolerance_is_built() -> None:
    _, _, records = _run([_opening(offsetMm=3101, widthMm=900)])
    assert records[0]["status"] == "built"


@pytest.mark.parametrize(
    ("opening", "walls", "reason"),
    [
        (_opening(wallId="W-9"), None, "không có tường chủ"),
        (_opening(), _walls(_wall(curved=True)), "ô mở trên tường cong chưa hỗ trợ"),
        (_opening(widthMm=50), None, "ô mở rộng/cao ≤ 50 mm"),
        (_opening(heightMm=40), None, "ô mở rộng/cao ≤ 50 mm"),
    ],
)
def test_openings_plan__skipped_major(opening: dict[str, Any], walls: SimpleNamespace | None, reason: str) -> None:
    cutters, assemblies, records = _run([opening], walls=walls)
    assert (cutters, assemblies) == ([], [])
    assert records == [
        {"level": LEVEL, "kind": "door", "id": "D-1", "status": "skipped", "severity": "major", "reason": reason}
    ]


@pytest.mark.parametrize("kind", ["gap", "void"])
def test_openings_plan__gap_void_cutter_only(kind: str) -> None:
    cutters, assemblies, records = _run([_opening(kind=kind)])
    assert len(cutters) == 1
    assert assemblies == []
    assert records[0]["status"] == "built"
    assert records[0]["reason"] == "chỉ khoét lỗ"


def test_openings_plan__rotated_wall_follows_direction() -> None:
    wall = {"id": "W-2", "centreline": ((0.0, 0.0), (0.0, 3.0)), "thicknessM": 0.1, "heightM": 3.0, "curved": False}
    cutters, (asm,), _ = _run([_opening(wallId="W-2", offsetMm=500, widthMm=800)], walls=_walls(wall))
    box = cutters[0]["box"]
    assert box["c"][:2] == [0.0, pytest.approx(0.9)]
    assert box["angle"] == pytest.approx(math.pi / 2)
    assert box["size"][1] == pytest.approx(0.3)
    assert asm["pivots"][0]["angle"] == pytest.approx(math.pi / 2)
