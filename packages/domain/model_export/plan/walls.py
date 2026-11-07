"""Chân tường 2D của một tầng (hợp đồng M1 §1, §4.1-4.2): dựng footprint rồi chia chồng lấn tất định.

Đầu vào là dict tường đúng dây API (mm, trục y hướng xuống); đầu ra tính bằng mét, Y hướng lên (`units.to_m`).

Tường cong: `curveOffsetMm` là sagitta có dấu theo quy ước `curveOffset` của Pascal
(`packages/domain/spatial/model.py`, `packages/domain/spatial/arc.py`): với dây `start→end` dài L **trong toạ độ
document (mm, y xuống)**, pháp tuyến `n = (-dy, dx) / L`, đỉnh cung = trung điểm dây - sagitta · n. Ví dụ dây
(0,0)→(4000,0), sagitta 1000 mm → n = (0, 1), đỉnh cung ở (2000, -1000) mm, tức (2, 1) m sau khi lật Y.
Cung lấy mẫu bằng `arc_points` trong mm (24 đoạn/180°, bước 7,5°) rồi mới đổi sang mét.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

from packages.domain.model_export.records import RecordBook
from packages.domain.model_export.units import GRID, polygons_of
from packages.domain.spatial.arc import arc_points

ARC_STEP_DEG: Final = 180 / 24
"""Bước lấy mẫu cung: 24 đoạn cho mỗi 180° (§4.1)."""
TOUCH_TOLERANCE_M: Final = 0.03
"""Đầu tường cách centreline tường khác ≤ t_kia/2 + ngần này thì coi là chạm (§4.1)."""
MIN_LENGTH_M: Final = 0.02
"""Tường ngắn hơn 20 mm không dựng (§1)."""
MIN_PIECE_M2: Final = 1e-6
"""Mảnh chân tường nhỏ hơn ngần này bị bỏ (§4.2)."""
OVERLAP_RATIO: Final = 0.5
"""Tường bị tường trước nuốt quá ngần này chiều dài centreline → ghi `degraded major`."""


@dataclass(frozen=True)
class WallPlan:
    """Kết quả chia chân tường: `pieces` theo thứ tự ưu tiên, `union` = hợp mọi `own`."""

    pieces: list[dict[str, Any]]
    union: BaseGeometry


@dataclass(frozen=True)
class _Wall:
    """Tường đã đổi sang mét, chưa kéo dài đầu."""

    id: str
    kind: str
    thickness: float
    height: float
    points: list[tuple[float, float]]
    length: float

    @property
    def curved(self) -> bool:
        """Tường cong khi đường tim có hơn hai đỉnh."""
        return len(self.points) > 2

    @property
    def line(self) -> LineString:
        """Đường tim (cung đã lấy mẫu với tường cong)."""
        return LineString(self.points)


def _parse(wall: dict[str, Any], height_default_m: float) -> _Wall:
    """Dict dây API → `_Wall` mét; tường cong lấy mẫu cung trong mm (quy ước dấu ở docstring mô-đun)."""
    line = wall["centreline"]
    start, end = line["start"], line["end"]
    sagitta = wall.get("curveOffsetMm") or 0
    pts_mm = arc_points((start["x"], start["y"]), (end["x"], end["y"]), sagitta, ARC_STEP_DEG)
    points = [(x / 1000, -y / 1000) for x, y in pts_mm]  # như `to_m`, nhưng đỉnh cung là số thực
    height_mm = wall.get("heightMm")
    return _Wall(
        id=wall["id"],
        kind=wall.get("kind", "partition"),
        thickness=wall.get("thicknessMm", 0) / 1000,
        height=height_mm / 1000 if height_mm else height_default_m,
        points=points,
        length=LineString(points).length,
    )


def _touches(tip: tuple[float, float], other: _Wall) -> bool:
    """Đầu tường `tip` chạm tường `other` khi cách đường tim của nó ≤ t/2 + 30 mm."""
    return other.line.distance(Point(tip)) <= other.thickness / 2 + TOUCH_TOLERANCE_M


def _footprint(wall: _Wall, others: list[_Wall]) -> BaseGeometry:
    """Chân tường: dải dày t dọc đường tim; tường thẳng kéo dài đúng t/2 ở mỗi đầu chạm tường khác."""
    points = list(wall.points)
    if not wall.curved:
        (x0, y0), (x1, y1) = points
        ux, uy = (x1 - x0) / wall.length, (y1 - y0) / wall.length
        half = wall.thickness / 2
        if any(_touches(points[0], o) for o in others):
            points[0] = (x0 - ux * half, y0 - uy * half)
        if any(_touches(points[1], o) for o in others):
            points[1] = (x1 + ux * half, y1 + uy * half)
    strip = LineString(points).buffer(wall.thickness / 2, cap_style="flat", join_style="mitre")
    return shapely.set_precision(strip, GRID)


def _solid(geom: BaseGeometry) -> BaseGeometry:
    """Chỉ giữ các mảnh đa giác ≥ `MIN_PIECE_M2` (bỏ đường/điểm/mảnh vụn sau phép trừ)."""
    parts = [p for p in polygons_of(geom) if p.area >= MIN_PIECE_M2]
    if len(parts) == 1:
        return parts[0]
    return MultiPolygon(parts)


def _sort_key(wall: _Wall) -> tuple[bool, float, float, str]:
    """Khoá ưu tiên §4.2: envelope trước, dày trước, dài trước, id tăng dần."""
    return (wall.kind != "envelope", -wall.thickness, -wall.length, wall.id)


def _main_owner(geom: BaseGeometry, owners: list[tuple[str, BaseGeometry]]) -> str:
    """Id tường trước có phần `own` giao `geom` lớn nhất (đo theo độ dài nếu `geom` là đường)."""

    def measure(own: BaseGeometry) -> float:
        hit = geom.intersection(own)
        return float(hit.area or hit.length)

    return max(owners, key=lambda item: (measure(item[1]), item[0]))[0]


def _faces(footprints: list[BaseGeometry]) -> list[list[BaseGeometry]]:
    """Chia mặt phẳng theo mọi biên chân tường (nối nút trên lưới `GRID`), giao từng mặt cho tường đứng trước nhất
    phủ nó. Bằng `own_i = footprint_i - hợp own_j (j < i)` nhưng các mảnh dùng chung đúng một cạnh, nên tổng diện tích
    các `own` bằng diện tích hợp (phép trừ lần lượt trên lưới để lại chồng lấn ~1e-5 m² ở tường xiên).
    """
    lines = shapely.union_all([fp.boundary for fp in footprints], grid_size=GRID)
    owned: list[list[BaseGeometry]] = [[] for _ in footprints]
    for face in shapely.polygonize([lines]).geoms:
        point = face.point_on_surface()
        owner = next((i for i, fp in enumerate(footprints) if fp.covers(point)), None)
        if owner is None:
            owner = next((i for i, fp in enumerate(footprints) if fp.distance(point) <= GRID), None)
        if owner is not None:
            owned[owner].append(face)
    return owned


def wall_plan(walls: list[dict[str, Any]], level_id: str, height_default_m: float, book: RecordBook) -> WallPlan:
    """Chân tường của một tầng, chia chồng lấn tất định; kết quả không phụ thuộc thứ tự `walls`.

    Tường dài < 20 mm hoặc dày ≤ 0 → `skipped major`. Tường bị nuốt hết → `degraded major` "tường trùng với
    tường X" và không có mảnh; bị nuốt > 50 % chiều dài → `degraded major` "tường chồng thẳng hàng với X".
    """
    parsed: list[_Wall] = []
    for wall in sorted((_parse(raw, height_default_m) for raw in walls), key=_sort_key):
        if wall.thickness <= 0:
            book.add(level_id, "wall", wall.id, "skipped", "major", "tường có bề dày ≤ 0")
        elif wall.length < MIN_LENGTH_M:
            book.add(level_id, "wall", wall.id, "skipped", "major", "tường ngắn hơn 20 mm")
        else:
            parsed.append(wall)
    footprints = [_footprint(wall, [o for o in parsed if o is not wall]) for wall in parsed]
    faces = _faces(footprints)
    pieces: list[dict[str, Any]] = []
    owners: list[tuple[str, BaseGeometry]] = []
    prior: BaseGeometry = Polygon()
    for index, wall in enumerate(parsed):
        own = _solid(shapely.union_all(faces[index], grid_size=GRID))
        if own.is_empty:
            owner = _main_owner(footprints[index], owners)
            book.add(level_id, "wall", wall.id, "degraded", "major", f"tường trùng với tường {owner}")
            continue
        if owners and wall.line.intersection(prior).length > OVERLAP_RATIO * wall.length:
            owner = _main_owner(wall.line, owners)
            book.add(level_id, "wall", wall.id, "degraded", "major", f"tường chồng thẳng hàng với {owner}")
        prior = shapely.union(prior, own, grid_size=GRID)
        owners.append((wall.id, own))
        pieces.append(
            {
                "id": wall.id,
                "kind": wall.kind,
                "heightM": wall.height,
                "thicknessM": wall.thickness,
                "own": own,
                "curved": wall.curved,
                "centreline": (wall.points[0], wall.points[-1]),
                "lengthM": wall.length,
            }
        )
    return WallPlan(pieces=pieces, union=prior)
