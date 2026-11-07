"""Cầu thang → assembly + lỗ sàn/trần cho plan.json (HOP-DONG-M1 §1 dòng "Cầu thang", §4.3).

Chỉ dựng `kind='stair'`, `variant='straight'`, và (`source != 'ai'` hoặc `reviewed`). Không dựng cầu thang giả.

**Quy ước hướng đi lên (khớp FE, AppFront origin/master):**
`src/lib/pascal/toPascal.ts:155` `yawOf(deg) = -rad(deg)`; `:707-722` `stairNodesOf`: `runAlongX = sizeX > sizeY`
(hộp bao `boundingBox`, mm), `length` = cạnh dài, `width` = cạnh ngắn,
`yaw = yawOf(rotationDeg) + (runAlongX ? π/2 : 0)`;
chân thang (`position`) = `centre - (sin yaw, cos yaw) · length/2` trong mặt (x, y) mm của document (Pascal z = y mm),
thân chạy theo +z cục bộ (`stair-system.tsx:166-169`). `toSpatial.ts:985-1015` làm ngược đúng quy ước đó.
Vậy trong toạ độ document (mm, y xuống): hướng đi lên = `(sin yaw, cos yaw)`, trục bề rộng = `(cos yaw, -sin yaw)`;
sau khi lật Y sang mét: lên = `(sin yaw, -cos yaw)`, rộng = `(cos yaw, sin yaw)`.
Ví dụ: hộp 1000 x 3000 mm, `rotationDeg = 0` → đi lên về +y document (-Y mét); hộp 3000 x 1000, 0° → +x;
hộp 1000 x 3000, 90° → yaw = -π/2 → đi lên về -x.

Mọi hộp ở toạ độ tầng (Z từ cốt sàn tầng). Cốn, lan can, tay vịn là hộp nghiêng: khoá `pitch` (radian, quay quanh
trục bề rộng của hộp sau `angle`; dương = đầu +X cục bộ của hộp nhô lên).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import Polygon

from packages.domain.model_export import defaults
from packages.domain.model_export.records import RecordBook

TREAD_T_M = 0.04
"""Mặt bậc dày 40 mm (giá trị prototype-v2)."""
STRINGER_T_M = 0.012
STRINGER_H_M = 0.3
"""Cốn thép 12 x 300 mm hai bên."""
RAIL_H_M = 0.92
"""Tay vịn cao 0,92 m (cùng `railingHeight` của toPascal)."""
RAIL_T_M = 0.045
"""Tay vịn 45 x 45 mm."""
GLASS_H_M = 0.85
"""Kính lan can cao 0,85 m dưới tay vịn."""


@dataclass(frozen=True)
class StairPlan:
    """Assembly cầu thang + lỗ khoét (đa giác shapely, mét) cho sàn tầng trên và trần tầng mình."""

    assemblies: list[dict[str, Any]] = field(default_factory=list)
    upper_slab_holes: list[Polygon] = field(default_factory=list)
    own_ceiling_holes: list[Polygon] = field(default_factory=list)


def stairs_plan(
    furniture: list[dict[str, Any]],
    level: dict[str, Any],
    upper_level: dict[str, Any] | None,
    materials: Any,
    book: RecordBook,
) -> StairPlan:
    """Dựng mọi cầu thang hợp lệ của tầng `level` (dòng snapshot: `levelId`, `elevationMm`, `heightMm`).

    `upper_level`: tầng kế trên theo `order` (hoặc None). Khớp khi `elevationMm` = elev + height ± 10 mm.
    """
    level_id = level["levelId"]
    plan = StairPlan()
    for f in furniture:
        if f.get("kind") != "stair":
            continue
        reason = _skip_reason(f)
        if reason:
            book.add(level_id, "stair", f["id"], "skipped", "major", reason)
            continue
        rise_mm, linked = _rise(level, upper_level)
        assembly, footprint = _stair(f, level_id, rise_mm / 1000, linked, materials, book)
        plan.assemblies.append(assembly)
        plan.own_ceiling_holes.append(footprint)
        if linked and upper_level is not None:
            plan.upper_slab_holes.append(footprint)
            book.add(level_id, "stair", f["id"], "built", "info", f"nối tầng {upper_level['levelId']}")
        else:
            book.add(level_id, "stair", f["id"], "degraded", "minor", "không có tầng trên khớp: vế lên tới cao tầng")
    return plan


def _skip_reason(f: dict[str, Any]) -> str:
    """Lý do không dựng (§1); rỗng = dựng."""
    if f.get("variant") != "straight":
        return "cầu thang chưa xác định kiểu/hướng — xác nhận trong editor"
    if f.get("source") == "ai" and not f.get("reviewed"):
        return "cầu thang do AI phát hiện chưa được duyệt"
    return ""


def _rise(level: dict[str, Any], upper: dict[str, Any] | None) -> tuple[float, bool]:
    """(độ cao vế mm, có nối tầng trên): tầng trên khớp ±10 mm → hiệu cao độ, ngược lại cao tầng mình."""
    top = level["elevationMm"] + level["heightMm"]
    if upper is not None and abs(upper["elevationMm"] - top) <= defaults.STAIR_LEVEL_TOL_MM:
        return upper["elevationMm"] - level["elevationMm"], True
    return level["heightMm"], False


def stair_axes(f: dict[str, Any]) -> dict[str, Any]:
    """Khung cầu thang theo quy ước FE (docstring mô-đun): chân, hướng lên, trục rộng (mét, Y lên), dài, rộng."""
    box = f["boundingBox"]
    size_x = (box["max"]["x"] - box["min"]["x"]) / 1000
    size_y = (box["max"]["y"] - box["min"]["y"]) / 1000
    along_x = size_x > size_y
    length, width = (size_x, size_y) if along_x else (size_y, size_x)
    yaw = -math.radians(f.get("rotationDeg") or 0) + (math.pi / 2 if along_x else 0)
    up = (math.sin(yaw), -math.cos(yaw))
    side = (math.cos(yaw), math.sin(yaw))
    cx, cy = f["centre"]["x"] / 1000, -f["centre"]["y"] / 1000
    base = (cx - up[0] * length / 2, cy - up[1] * length / 2)
    return {"base": base, "up": up, "side": side, "length": length, "width": width}


def _stair(
    f: dict[str, Any], level_id: str, rise: float, linked: bool, materials: Any, book: RecordBook
) -> tuple[dict[str, Any], Polygon]:
    """Assembly một vế thẳng: mặt bậc, 2 cốn, kính lan can + tay vịn hai bên; trả kèm đa giác chân thang."""
    ax = stair_axes(f)
    risers = max(2, math.ceil(rise * 1000 / defaults.STAIR_MAX_RISER_MM - 1e-9))
    riser_h = rise / risers
    going = ax["length"] / (risers - 1)
    angle = math.atan2(ax["up"][1], ax["up"][0])
    pitch = math.atan2(riser_h * (risers - 1), ax["length"])
    slope_len = math.hypot(ax["length"], riser_h * (risers - 1))
    name = f"Stair_{f['id']}"

    def at(s: float, w: float) -> tuple[float, float]:
        """Điểm cách chân `s` dọc thân, `w` theo trục rộng."""
        return (
            ax["base"][0] + ax["up"][0] * s + ax["side"][0] * w,
            ax["base"][1] + ax["up"][1] * s + ax["side"][1] * w,
        )

    def mat(role: str) -> str:
        """Khoá vật liệu trung tính theo vai trò."""
        return str(materials.resolve(None, role, level_id, f["id"], book))

    boxes: list[dict[str, Any]] = []

    def box(part: str, s: float, w: float, z: float, size: tuple[float, ...], material: str, tilt: float = 0.0) -> None:
        """Thêm hộp tâm tại (s, w, z), dài theo hướng đi lên."""
        x, y = at(s, w)
        entry = {
            "name": f"{name}_{part}",
            "parent": name,
            "c": _r3(x, y, z),
            "size": _r3(*size),
            "angle": round(angle, 6),
            "material": material,
            "props": {"prov_assembly": "default"},
        }
        if tilt:
            entry["pitch"] = round(tilt, 6)
        boxes.append(entry)

    for k in range(risers - 1):
        top = (k + 1) * riser_h
        box(
            f"Tread{k:02d}",
            (k + 0.5) * going,
            0,
            top - TREAD_T_M / 2,
            (going, ax["width"], TREAD_T_M),
            mat("stair_tread"),
        )
    mid_z = riser_h * (risers - 1) / 2
    for tag, sign in (("A", -1), ("B", 1)):
        w = sign * (ax["width"] / 2 - STRINGER_T_M / 2)
        box(
            f"Stringer{tag}",
            ax["length"] / 2,
            w,
            mid_z,
            (slope_len, STRINGER_T_M, STRINGER_H_M),
            mat("stair_side"),
            pitch,
        )
        glass = (slope_len, defaults.GLASS_T_M, GLASS_H_M)
        box(f"Glass{tag}", ax["length"] / 2, w, mid_z + RAIL_H_M - RAIL_T_M - GLASS_H_M / 2, glass, mat("glass"), pitch)
        rail = (slope_len, RAIL_T_M, RAIL_T_M)
        box(f"Handrail{tag}", ax["length"] / 2, w, mid_z + RAIL_H_M - RAIL_T_M / 2, rail, mat("metal"), pitch)

    hw = ax["width"] / 2
    footprint = Polygon([at(0, -hw), at(ax["length"], -hw), at(ax["length"], hw), at(0, hw)])
    assembly = {
        "name": name,
        "level": level_id,
        "group": "Stairs",
        "appId": f["id"],
        "props": {
            "appId": f["id"],
            "kind": "stair",
            "variant": "straight",
            "risers": risers,
            "riseM": round(riser_h, 4),
            "goingM": round(going, 4),
            "widthM": round(ax["width"], 4),
            "lengthM": round(ax["length"], 4),
            "linkedUpper": linked,
            "prov_footprint": "document",
            "prov_assembly": "default",
        },
        "pivots": [],
        "boxes": boxes,
    }
    return assembly, footprint


def _r3(*xs: float) -> list[float]:
    """Làm tròn 6 chữ số để plan.json tất định."""
    return [round(x, 6) for x in xs]
