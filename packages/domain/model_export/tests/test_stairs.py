"""Cầu thang → assembly + lỗ sàn/trần (HOP-DONG-M1 §1 dòng "Cầu thang")."""

from __future__ import annotations

import math
from typing import Any

import pytest

from packages.domain.model_export.materials import MaterialResolver
from packages.domain.model_export.plan.stairs import StairPlan, stair_axes, stairs_plan
from packages.domain.model_export.records import RecordBook

LEVEL = {"levelId": "L-0", "elevationMm": 0, "heightMm": 3300}
UPPER = {"levelId": "L-1", "elevationMm": 3305, "heightMm": 3000}


def _stair(**kw: Any) -> dict[str, Any]:
    """Cầu thang thẳng 1000 x 3000 mm, tâm (2000, 1500), người vẽ."""
    base = {
        "id": "F-1",
        "kind": "stair",
        "variant": "straight",
        "source": "human",
        "reviewed": False,
        "centre": {"x": 2000, "y": 1500},
        "rotationDeg": 0,
        "boundingBox": {"min": {"x": 1500, "y": 0}, "max": {"x": 2500, "y": 3000}},
    }
    return {**base, **kw}


def _run(
    furniture: list[dict[str, Any]], upper: dict[str, Any] | None = UPPER
) -> tuple[StairPlan, list[dict[str, Any]]]:
    """Chạy `stairs_plan` với resolver rỗng."""
    book = RecordBook()
    plan = stairs_plan(furniture, LEVEL, upper, MaterialResolver({}, {}), book)
    return plan, book.to_dicts()


def test_stairs_plan__straight_linked_to_upper() -> None:
    plan, records = _run([_stair(), {"id": "F-2", "kind": "table"}])
    (asm,) = plan.assemblies
    assert asm["name"] == "Stair_F-1"
    assert asm["group"] == "Stairs"
    assert asm["props"]["risers"] == math.ceil(3305 / 175) == 19
    assert asm["props"]["riseM"] == pytest.approx(3.305 / 19, abs=1e-4)
    treads = [b for b in asm["boxes"] if "_Tread" in b["name"]]
    assert len(treads) == 18
    assert {b["name"].rsplit("_", 1)[1] for b in asm["boxes"] if "Tread" not in b["name"]} == {
        "StringerA",
        "StringerB",
        "GlassA",
        "GlassB",
        "HandrailA",
        "HandrailB",
    }
    assert all(b["props"]["prov_assembly"] == "default" for b in asm["boxes"])
    assert all("pitch" in b for b in asm["boxes"] if "Tread" not in b["name"])
    assert plan.upper_slab_holes == plan.own_ceiling_holes
    (hole,) = plan.own_ceiling_holes
    assert hole.bounds == pytest.approx((1.5, -3.0, 2.5, 0.0))
    assert records == [
        {"level": "L-0", "kind": "stair", "id": "F-1", "status": "built", "severity": "info", "reason": "nối tầng L-1"}
    ]


@pytest.mark.parametrize("upper", [None, {"levelId": "L-1", "elevationMm": 3400, "heightMm": 3000}])
def test_stairs_plan__no_matching_upper_degraded_minor(upper: dict[str, Any] | None) -> None:
    plan, records = _run([_stair()], upper=upper)
    assert plan.upper_slab_holes == []
    assert len(plan.own_ceiling_holes) == 1
    assert plan.assemblies[0]["props"]["risers"] == math.ceil(3300 / 175)
    assert records[0]["status"] == "degraded"
    assert records[0]["severity"] == "minor"


@pytest.mark.parametrize(
    ("stair", "reason"),
    [
        (_stair(source="ai"), "cầu thang do AI phát hiện chưa được duyệt"),
        (_stair(variant="spiral"), "cầu thang chưa xác định kiểu/hướng — xác nhận trong editor"),
        (_stair(variant=None), "cầu thang chưa xác định kiểu/hướng — xác nhận trong editor"),
    ],
)
def test_stairs_plan__skipped_major(stair: dict[str, Any], reason: str) -> None:
    plan, records = _run([stair])
    assert plan.assemblies == []
    assert plan.own_ceiling_holes == []
    assert records == [
        {"level": "L-0", "kind": "stair", "id": "F-1", "status": "skipped", "severity": "major", "reason": reason}
    ]


def test_stairs_plan__ai_reviewed_is_built() -> None:
    plan, _ = _run([_stair(source="ai", reviewed=True)])
    assert len(plan.assemblies) == 1


@pytest.mark.parametrize(
    ("box", "rotation", "up"),
    [
        # 1000 x 3000, 0° → đi lên về +y document = -Y mét (toPascal yaw = 0, hướng (sin 0, cos 0)).
        ({"min": {"x": 1500, "y": 0}, "max": {"x": 2500, "y": 3000}}, 0, (0.0, -1.0)),
        # 3000 x 1000, 0° → runAlongX, yaw = π/2 → +x.
        ({"min": {"x": 500, "y": 1000}, "max": {"x": 3500, "y": 2000}}, 0, (1.0, 0.0)),
        # 1000 x 3000, 90° → yaw = -π/2 → -x.
        ({"min": {"x": 1500, "y": 0}, "max": {"x": 2500, "y": 3000}}, 90, (-1.0, 0.0)),
    ],
)
def test_stair_axes__matches_fe_direction(box: dict[str, Any], rotation: int, up: tuple[float, float]) -> None:
    ax = stair_axes(_stair(boundingBox=box, rotationDeg=rotation))
    assert ax["up"] == pytest.approx(up, abs=1e-9)
    assert (ax["length"], ax["width"]) == pytest.approx((3.0, 1.0))
    # chân thang = tâm lùi nửa chiều dài ngược hướng đi lên
    assert ax["base"] == pytest.approx((2.0 - up[0] * 1.5, -1.5 - up[1] * 1.5))


def test_stairs_plan__first_tread_near_base_last_near_top() -> None:
    plan, _ = _run([_stair()])
    treads = [b for b in plan.assemblies[0]["boxes"] if "_Tread" in b["name"]]
    # đi lên về -Y: bậc đầu gần Y = 0 (chân), bậc cuối gần Y = -3, cao dần
    assert treads[0]["c"][1] > treads[-1]["c"][1]
    assert treads[0]["c"][2] < treads[-1]["c"][2]
    assert treads[0]["angle"] == pytest.approx(-math.pi / 2)
