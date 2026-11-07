"""Sàn kết cấu, sàn hoàn thiện và trần 2D của một tầng (hợp đồng M1 §1 dòng "Phòng", §4.3).

Mọi phép boolean ở 2D (shapely, lưới `GRID`); Blender chỉ đùn. Không dùng boolean 3D cho sàn/trần.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from packages.domain.model_export.defaults import ceiling_height_m
from packages.domain.model_export.records import RecordBook
from packages.domain.model_export.units import GRID, polygons_of, to_m

MIN_ROOM_M2: Final = 0.2
"""Phòng nhỏ hơn 0,2 m² không dựng (§1)."""
MIN_PIECE_M2: Final = 1e-6
"""Mảnh đa giác nhỏ hơn ngần này bị bỏ (cùng ngưỡng với chân tường, §4.2)."""


@dataclass(frozen=True)
class SlabPlan:
    """`structural` = hợp của phòng và chân tường; `finishes`/`ceilings` theo phòng, sắp theo `roomId`."""

    structural: BaseGeometry
    finishes: list[dict[str, Any]]
    ceilings: list[dict[str, Any]]


def _solid(geom: BaseGeometry) -> BaseGeometry:
    """Chỉ giữ các mảnh đa giác ≥ `MIN_PIECE_M2`."""
    parts = [p for p in polygons_of(geom) if p.area >= MIN_PIECE_M2]
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def _room_polygon(room: dict[str, Any], level_id: str, book: RecordBook) -> BaseGeometry | None:
    """Đường bao phòng → đa giác mét hợp lệ, hoặc `None` (đã ghi sổ `skipped major`)."""
    outline = room.get("outline") or []
    if len(outline) < 3:
        book.add(level_id, "room", room["id"], "skipped", "major", "đường bao phòng ít hơn 3 điểm")
        return None
    poly: BaseGeometry = Polygon([to_m(p) for p in outline])
    if not poly.is_valid:
        poly = shapely.make_valid(poly)
        book.add(level_id, "room", room["id"], "degraded", "minor", "đường bao phòng tự cắt, đã sửa hình")
    poly = _solid(shapely.set_precision(poly, GRID))
    if poly.area < MIN_ROOM_M2:
        book.add(level_id, "room", room["id"], "skipped", "major", "phòng nhỏ hơn 0,2 m²")
        return None
    return poly


def slab_plan(
    rooms: list[dict[str, Any]],
    wall_union: BaseGeometry,
    level_id: str,
    height_m: float,
    book: RecordBook,
    holes: list[BaseGeometry],
) -> SlabPlan:
    """Sàn kết cấu = hợp của phòng và chân tường; hoàn thiện mỗi phòng = phòng - chân tường - `holes`; trần cùng hình.

    Trần ở `z = min(2,7, H - 0,25)` m. Phòng bị chân tường/lỗ phủ hết → `degraded major`, không có sàn/trần.
    Kết quả không phụ thuộc thứ tự `rooms`.
    """
    cut = shapely.union_all([wall_union, *holes], grid_size=GRID)
    polys: list[BaseGeometry] = [wall_union]
    finishes: list[dict[str, Any]] = []
    ceilings: list[dict[str, Any]] = []
    z = ceiling_height_m(height_m)
    for room in sorted(rooms, key=lambda r: str(r["id"])):
        poly = _room_polygon(room, level_id, book)
        if poly is None:
            continue
        polys.append(poly)
        finish = _solid(shapely.difference(poly, cut, grid_size=GRID))
        if finish.is_empty:
            book.add(level_id, "room", room["id"], "degraded", "major", "phòng bị chân tường phủ hết")
            continue
        finishes.append({"roomId": room["id"], "geom": finish})
        ceilings.append({"roomId": room["id"], "geom": finish, "z": z})
    structural = _solid(shapely.union_all(polys, grid_size=GRID))
    return SlabPlan(structural=structural, finishes=finishes, ceilings=ceilings)
