"""Sàn kết cấu, sàn hoàn thiện, trần 2D (hợp đồng M1 §1 dòng "Phòng", §4.3)."""

import json
from pathlib import Path
from typing import Any, Final

import pytest
import shapely
from shapely.geometry import Polygon, box

from packages.domain.model_export.plan.slabs import SlabPlan, slab_plan
from packages.domain.model_export.plan.walls import wall_plan
from packages.domain.model_export.records import RecordBook
from packages.domain.model_export.units import polygons_of

DATA: Final = Path(__file__).parent / "data"


def _room(room_id: str, *points: tuple[int, int]) -> dict[str, Any]:
    """Dict phòng đúng dây API (mm, y xuống)."""
    return {"id": room_id, "outline": [{"x": x, "y": y} for x, y in points]}


def _wall(wall_id: str, x0: int, y0: int, x1: int, y1: int) -> dict[str, Any]:
    """Tường envelope dày 200 mm."""
    return {
        "id": wall_id,
        "centreline": {"start": {"x": x0, "y": y0}, "end": {"x": x1, "y": y1}},
        "thicknessMm": 200,
        "heightMm": 3300,
        "kind": "envelope",
    }


BOX_WALLS: Final = [
    _wall("W-N", 0, 0, 4000, 0),
    _wall("W-E", 4000, 0, 4000, 3000),
    _wall("W-S", 4000, 3000, 0, 3000),
    _wall("W-W", 0, 3000, 0, 0),
]
"""Hộp 4 x 3 m theo tim tường; phủ ngoài 4,2 x 3,2 m, lọt lòng 3,8 x 2,8 m."""
ROOM: Final = _room("R-A", (0, 0), (4000, 0), (4000, 3000), (0, 3000))


def _plan(
    rooms: list[dict[str, Any]],
    walls: list[dict[str, Any]] = BOX_WALLS,
    height_m: float = 3.3,
    holes: list[Any] | None = None,
) -> tuple[SlabPlan, RecordBook]:
    """Chạy `wall_plan` rồi `slab_plan` cho tầng `L-1`."""
    book = RecordBook()
    walls_out = wall_plan(walls, "L-1", height_m, book)
    return slab_plan(rooms, walls_out.union, "L-1", height_m, book, holes or []), book


def test_slab_plan__box_room_structural_finish_ceiling() -> None:
    """Kết cấu = phòng hợp tường (4,2 x 3,2), hoàn thiện = lọt lòng (3,8 x 2,8), trần cùng hình ở 2,7 m."""
    plan, book = _plan([ROOM])
    assert plan.structural.area == pytest.approx(4.2 * 3.2, abs=1e-9)
    assert isinstance(plan.structural, Polygon)
    assert list(plan.structural.interiors) == []
    [finish] = plan.finishes
    assert finish["roomId"] == "R-A"
    assert finish["geom"].area == pytest.approx(3.8 * 2.8, abs=1e-9)
    assert finish["geom"].bounds == pytest.approx((0.1, -2.9, 3.9, -0.1))
    [ceiling] = plan.ceilings
    assert ceiling["geom"].equals(finish["geom"])
    assert ceiling["z"] == pytest.approx(2.7)
    assert book.records == []


def test_slab_plan__low_storey_lowers_ceiling_and_holes_are_cut() -> None:
    """Tầng 2,8 m → trần 2,55 m; ô cầu thang 1 x 1 m trừ khỏi sàn hoàn thiện và trần, không khỏi kết cấu."""
    hole = box(1.0, -2.0, 2.0, -1.0)
    plan, _ = _plan([ROOM], height_m=2.8, holes=[hole])
    assert plan.ceilings[0]["z"] == pytest.approx(2.55)
    assert plan.finishes[0]["geom"].area == pytest.approx(3.8 * 2.8 - 1.0, abs=1e-9)
    assert plan.ceilings[0]["geom"].area == pytest.approx(3.8 * 2.8 - 1.0, abs=1e-9)
    assert plan.structural.area == pytest.approx(4.2 * 3.2, abs=1e-9)


def test_slab_plan__skips_small_and_degenerate_rooms() -> None:
    """< 3 điểm hoặc < 0,2 m² → `skipped major`, không có sàn/trần."""
    rooms = [_room("R-LINE", (0, 0), (1000, 0)), _room("R-TINY", (0, 0), (300, 0), (300, 300), (0, 300))]
    plan, book = _plan(rooms, walls=[])
    assert plan.finishes == []
    assert plan.structural.is_empty
    assert [(r.id, r.status, r.severity, r.reason) for r in book.records] == [
        ("R-LINE", "skipped", "major", "đường bao phòng ít hơn 3 điểm"),
        ("R-TINY", "skipped", "major", "phòng nhỏ hơn 0,2 m²"),
    ]


def test_slab_plan__invalid_outline_is_repaired_and_recorded() -> None:
    """Đường bao tự cắt (nơ 2 x 2 m) → `make_valid` thành hai tam giác 1 m², ghi `degraded minor`."""
    plan, book = _plan([_room("R-BOW", (0, 0), (2000, 2000), (2000, 0), (0, 2000))], walls=[])
    assert plan.finishes[0]["geom"].area == pytest.approx(2.0, abs=1e-9)
    assert len(polygons_of(plan.finishes[0]["geom"])) == 2
    [record] = book.records
    assert (record.kind, record.id, record.status, record.severity) == ("room", "R-BOW", "degraded", "minor")


def test_slab_plan__room_covered_by_wall_has_no_finish() -> None:
    """Phòng 2 x 0,15 m nằm gọn trong tường dày 200 mm → `degraded major`, không sàn/trần, vẫn vào kết cấu."""
    room = _room("R-SLIT", (0, -75), (2000, -75), (2000, 75), (0, 75))
    plan, book = _plan([room], walls=[_wall("W-N", 0, 0, 2000, 0)])
    assert plan.finishes == []
    assert plan.ceilings == []
    assert plan.structural.area == pytest.approx(0.4, abs=1e-9)
    [record] = book.records
    assert (record.id, record.status, record.severity, record.reason) == (
        "R-SLIT",
        "degraded",
        "major",
        "phòng bị chân tường phủ hết",
    )


def test_slab_plan__order_invariant() -> None:
    """Xáo phòng → cùng kết quả (sắp theo `roomId`)."""
    rooms = [ROOM, _room("R-0", (4000, 0), (6000, 0), (6000, 3000), (4000, 3000))]
    first, _ = _plan(rooms)
    second, _ = _plan(list(reversed(rooms)))
    assert [f["roomId"] for f in first.finishes] == ["R-0", "R-A"]
    assert [f["roomId"] for f in second.finishes] == ["R-0", "R-A"]
    assert shapely.symmetric_difference(first.structural, second.structural).area < 1e-8


@pytest.mark.parametrize("name", ["ch-08", "nt-02"])
def test_slab_plan__bench_fixture_structural_is_closed(name: str) -> None:
    """Ca từng hở (bench-3d r5): kết cấu hợp lệ, diện tích > 0, không mảnh < 1e-6 m², mọi phòng có sàn hợp lệ."""
    data = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    height = data["heightMm"] / 1000
    book = RecordBook()
    walls = wall_plan(data["walls"], data["levelId"], height, book)
    plan = slab_plan(data["rooms"], walls.union, data["levelId"], height, book, [])
    assert plan.structural.is_valid
    assert plan.structural.area > 0
    assert min(p.area for p in polygons_of(plan.structural)) >= 1e-6
    assert plan.structural.covers(walls.union.buffer(-1e-3))
    assert len(plan.finishes) == len(data["rooms"])
    assert all(f["geom"].is_valid and f["geom"].area > 0 for f in plan.finishes)
    assert all(isinstance(p, Polygon) for p in polygons_of(plan.structural))
