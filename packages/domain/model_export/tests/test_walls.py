"""Chân tường 2D (hợp đồng M1 §4.1-4.2): kéo dài đầu chạm, tường cong, chia chồng lấn tất định, ghi sổ."""

import json
from pathlib import Path
from typing import Any, Final

import pytest
import shapely
from shapely.geometry import Point

from packages.domain.model_export.plan.walls import WallPlan, wall_plan
from packages.domain.model_export.records import RecordBook

DATA: Final = Path(__file__).parent / "data"


def _wall(
    wall_id: str, x0: int, y0: int, x1: int, y1: int, t: int = 200, kind: str = "partition", **extra: Any
) -> dict[str, Any]:
    """Dict tường đúng dây API (mm, y xuống)."""
    return {
        "id": wall_id,
        "centreline": {"start": {"x": x0, "y": y0}, "end": {"x": x1, "y": y1}},
        "thicknessMm": t,
        "heightMm": 3000,
        "kind": kind,
        **extra,
    }


def _plan(walls: list[dict[str, Any]]) -> tuple[WallPlan, RecordBook]:
    """Chạy `wall_plan` cho tầng `L-1`, cao mặc định 3,3 m."""
    book = RecordBook()
    return wall_plan(walls, "L-1", 3.3, book), book


def _areas(plan: WallPlan) -> dict[str, float]:
    """Diện tích `own` theo id tường."""
    return {piece["id"]: piece["own"].area for piece in plan.pieces}


def _assert_partition(plan: WallPlan) -> None:
    """Các `own` rời nhau: tổng diện tích = diện tích hợp (±1e-8 m²), mọi hình hợp lệ."""
    assert plan.union.is_valid
    assert all(piece["own"].is_valid for piece in plan.pieces)
    assert sum(_areas(plan).values()) == pytest.approx(plan.union.area, abs=1e-8)


def test_wall_plan__l_corner_extends_touching_ends_by_half_thickness() -> None:
    """Góc L: đầu chạm kéo dài đúng t/2, đầu tự do giữ nguyên; tường dài hơn giữ phần góc."""
    plan, book = _plan([_wall("W-B", 4000, 0, 4000, 3000), _wall("W-A", 0, 0, 4000, 0)])
    assert [p["id"] for p in plan.pieces] == ["W-A", "W-B"]
    a, b = plan.pieces
    assert a["own"].bounds == pytest.approx((0.0, -0.1, 4.1, 0.1))
    assert a["own"].area == pytest.approx(0.82, abs=1e-9)
    assert b["own"].area == pytest.approx(0.62 - 0.04, abs=1e-9)
    assert b["centreline"] == ((4.0, 0.0), (4.0, -3.0))
    assert (b["lengthM"], b["thicknessM"], b["heightM"], b["curved"]) == (3.0, 0.2, 3.0, False)
    assert plan.union.area == pytest.approx(1.40, abs=1e-9)
    assert book.records == []
    _assert_partition(plan)


def test_wall_plan__t_junction_extends_stem_into_thicker_wall() -> None:
    """Chữ T: tường mỏng chạm thân tường dày, kéo dài 50 mm; phần lọt vào tường dày thuộc tường dày."""
    plan, _ = _plan([_wall("W-STEM", 3000, 0, 3000, 3000, t=100), _wall("W-RUN", 0, 0, 6000, 0)])
    areas = _areas(plan)
    assert areas["W-RUN"] == pytest.approx(1.2, abs=1e-9)
    assert areas["W-STEM"] == pytest.approx(0.1 * 3.05 - 0.1 * 0.15, abs=1e-9)
    assert plan.pieces[0]["own"].bounds == pytest.approx((0.0, -0.1, 6.0, 0.1))
    _assert_partition(plan)


def test_wall_plan__cross_gives_crossing_square_to_lower_id() -> None:
    """Chữ thập: cùng dày, cùng dài → id nhỏ hơn giữ ô giao 0,2 x 0,2 m."""
    plan, book = _plan([_wall("W-B", 3000, -3000, 3000, 3000), _wall("W-A", 0, 0, 6000, 0)])
    assert _areas(plan) == pytest.approx({"W-A": 1.2, "W-B": 1.16}, abs=1e-9)
    assert book.records == []
    _assert_partition(plan)


def test_wall_plan__duplicate_wall_is_swallowed_and_recorded() -> None:
    """Tường trùng: tường sau không còn mảnh nào, ghi `degraded major` nêu tường giữ chỗ."""
    plan, book = _plan([_wall("W-B", 0, 0, 4000, 0), _wall("W-A", 0, 0, 4000, 0)])
    assert [p["id"] for p in plan.pieces] == ["W-A"]
    [record] = book.records
    assert (record.level, record.kind, record.id, record.status, record.severity) == (
        "L-1",
        "wall",
        "W-B",
        "degraded",
        "major",
    )
    assert record.reason == "tường trùng với tường W-A"


def test_wall_plan__collinear_overlap_over_half_is_recorded() -> None:
    """Chồng thẳng hàng: W-B (1→5 m) bị W-A (0→4,1 m sau kéo dài) nuốt 3,1/4 chiều dài → ghi sổ, còn mảnh 0,9 m."""
    plan, book = _plan([_wall("W-A", 0, 0, 4000, 0), _wall("W-B", 1000, 0, 5000, 0)])
    assert _areas(plan) == pytest.approx({"W-A": 0.82, "W-B": 0.18}, abs=1e-9)
    [record] = book.records
    assert (record.id, record.status, record.severity) == ("W-B", "degraded", "major")
    assert record.reason == "tường chồng thẳng hàng với W-A"
    _assert_partition(plan)


def test_wall_plan__oblique_wall_is_exact_rectangle() -> None:
    """Tường xiên 3-4-5: chữ nhật 5 x 0,2 m, góc rơi đúng lưới 0,1 mm."""
    plan, _ = _plan([_wall("W-X", 0, 0, 3000, 4000)])
    [piece] = plan.pieces
    assert piece["own"].area == pytest.approx(1.0, abs=1e-12)
    assert piece["lengthM"] == pytest.approx(5.0)
    assert piece["centreline"] == ((0.0, 0.0), (3.0, -4.0))


def test_wall_plan__oblique_meets_straight_without_overlap() -> None:
    """Tường xiên chạm tường thẳng: mảnh không chồng nhau dù đỉnh giao không nằm trên lưới."""
    plan, _ = _plan([_wall("W-X", 1000, 0, 4000, 4000, t=100), _wall("W-H", 0, 0, 6000, 0)])
    assert _areas(plan)["W-H"] == pytest.approx(1.2, abs=1e-9)
    _assert_partition(plan)


def test_wall_plan__curved_wall_follows_pascal_sign_convention() -> None:
    """Cong: dây (0,0)→(4000,0) mm, sagitta +1000 → n = (0, 1), đỉnh cung (2000, -1000) mm = (2, 1) m.

    Bán kính 2,5 m, góc quét 4·atan(0,5) ≈ 106,26° → 16 đoạn (bước ≤ 7,5°); diện tích ≈ 0,2 x cung 4,6365 m.
    """
    plan, _ = _plan([_wall("W-C", 0, 0, 4000, 0, curveOffsetMm=1000)])
    [piece] = plan.pieces
    own = piece["own"]
    assert piece["curved"] is True
    assert piece["centreline"] == ((0.0, 0.0), (4.0, 0.0))
    assert own.contains(Point(2.0, 1.05))
    assert not own.contains(Point(2.0, -0.5))
    assert own.bounds[3] == pytest.approx(1.1, abs=5e-4)
    assert piece["lengthM"] == pytest.approx(4.6339, abs=1e-3)
    assert own.area == pytest.approx(0.2 * 4.6365, abs=5e-3)


def test_wall_plan__negative_curve_bends_the_other_way() -> None:
    """Sagitta âm → đỉnh cung phía y document dương, tức Y mét âm."""
    plan, _ = _plan([_wall("W-C", 0, 0, 4000, 0, curveOffsetMm=-1000)])
    assert plan.pieces[0]["own"].bounds[1] == pytest.approx(-1.1, abs=5e-4)


def test_wall_plan__skips_degenerate_walls_and_defaults_height() -> None:
    """Dày ≤ 0 hoặc dài < 20 mm → `skipped major`; thiếu `heightMm` → cao mặc định của tầng."""
    no_height = _wall("W-OK", 0, 0, 2000, 0)
    del no_height["heightMm"]
    plan, book = _plan([_wall("W-THIN", 0, 0, 1000, 0, t=0), _wall("W-SHORT", 0, 0, 10, 0), no_height])
    assert [p["id"] for p in plan.pieces] == ["W-OK"]
    assert plan.pieces[0]["heightM"] == 3.3
    assert {(r.id, r.status, r.severity, r.reason) for r in book.records} == {
        ("W-THIN", "skipped", "major", "tường có bề dày ≤ 0"),
        ("W-SHORT", "skipped", "major", "tường ngắn hơn 20 mm"),
    }


def test_wall_plan__envelope_wins_over_thicker_partition() -> None:
    """Envelope đứng trước dù mỏng hơn: giữ trọn chân (kéo dài 50 mm mỗi đầu vì hai tường chạm nhau), tường dày
    (kéo dài 150 mm mỗi đầu) chỉ còn phần ngoài."""
    plan, _ = _plan([_wall("W-P", 0, 0, 4000, 0, t=300), _wall("W-E", 0, 0, 4000, 0, t=100, kind="envelope")])
    assert [p["id"] for p in plan.pieces] == ["W-E", "W-P"]
    assert _areas(plan) == pytest.approx({"W-E": 0.41, "W-P": 1.29 - 0.41}, abs=1e-9)


def test_wall_plan__empty_input() -> None:
    """Không có tường → không mảnh, hợp rỗng."""
    plan, book = _plan([])
    assert plan.pieces == []
    assert plan.union.is_empty
    assert book.records == []


def _synthetic() -> list[dict[str, Any]]:
    """Gộp mọi ca tổng hợp: L, T, thập, trùng, chồng thẳng hàng, xiên, cong, bỏ qua."""
    return [
        _wall("W-01", 0, 0, 8000, 0, kind="envelope"),
        _wall("W-02", 8000, 0, 8000, 6000, kind="envelope"),
        _wall("W-03", 4000, 0, 4000, 6000, t=100),
        _wall("W-04", 0, 3000, 8000, 3000, t=100),
        _wall("W-05", 0, 3000, 8000, 3000, t=100),
        _wall("W-06", 1000, 3000, 5000, 3000, t=100),
        _wall("W-07", 0, 0, 3000, 4000, t=150),
        _wall("W-08", 0, 6000, 8000, 6000, curveOffsetMm=-1500),
        _wall("W-09", 0, 0, 5, 0),
    ]


def _fixture(name: str) -> dict[str, Any]:
    """Tường/phòng tối thiểu chép từ bench-3d r5 (ca từng hở)."""
    data: dict[str, Any] = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize("source", ["synthetic", "ch-08", "nt-02"])
def test_wall_plan__order_invariant(source: str) -> None:
    """Đảo/xáo đầu vào → cùng `own` theo id (diện tích ±1e-9, đối xứng hiệu < 1e-8 m²), cùng sổ."""
    walls = _synthetic() if source == "synthetic" else _fixture(source)["walls"]
    base, base_book = _plan(walls)
    _assert_partition(base)
    shuffled = walls[1::2] + walls[::2][::-1]
    for variant in (list(reversed(walls)), shuffled, walls[len(walls) // 2 :] + walls[: len(walls) // 2]):
        plan, book = _plan(variant)
        assert [p["id"] for p in plan.pieces] == [p["id"] for p in base.pieces]
        for got, want in zip(plan.pieces, base.pieces, strict=True):
            assert got["own"].area == pytest.approx(want["own"].area, abs=1e-9)
            assert shapely.symmetric_difference(got["own"], want["own"]).area < 1e-8
        assert book.records == base_book.records


@pytest.mark.parametrize("name", ["ch-08", "nt-02"])
def test_wall_plan__bench_fixture_partitions_cleanly(name: str) -> None:
    """Dữ liệu thật: mọi tường có mảnh, các mảnh rời nhau, không mảnh < 1e-6 m²."""
    walls = _fixture(name)["walls"]
    plan, _ = _plan(walls)
    assert len(plan.pieces) == len(walls)
    _assert_partition(plan)
    parts = [g for piece in plan.pieces for g in getattr(piece["own"], "geoms", [piece["own"]])]
    assert min(part.area for part in parts) >= 1e-6
