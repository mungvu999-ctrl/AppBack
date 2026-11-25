"""Đường bao phòng → đường bao mái → các chữ nhật mái (NHA-02, thiết kế §2.C Câu 5 bước 1-5).

Pascal chỉ lưu được mặt mái chữ nhật, nên đường bao vuông góc (L/T/U) được phủ bằng các chữ nhật cực đại chồng nhau;
hình không phủ được thì trả mã từ chối để hỏi người dùng vẽ tay. Nằm ở `packages/vision` vì cần shapely. Chưa nối vào
luồng nào; nơi dùng đầu tiên là NHA-07a.
"""

import itertools
import math
from collections.abc import Iterable, Sequence
from typing import Final, Literal

import shapely
from shapely import affinity

from packages.domain.spatial.model import ROOF_NODES_MAX
from packages.domain.spatial.roof_build import RoofRect, RoofRects

type XY = tuple[float, float]
type RoofRefusal = Literal["no_rooms", "split", "hole", "poor_fit", "complex"]
type _Box = tuple[float, float, float, float]  # (u0, v0, u1, v1) trong khung trục chính

_CLOSE_MM: Final = 150.0  # khép khe còn lại giữa hai phòng khi tường trong dày hơn tường ngoài (khe tới 300 mm)
_SIMPLIFY_MM: Final = 10.0
_NEAR_RECT: Final = 0.96  # diện tích / khung bao; đúng ngưỡng công cụ mái của Pascal (`roof-footprint.ts:81`)
_SNAP_MM: Final = 100.0  # hai đường lưới gần hơn mức này là một: toạ độ phòng lệch vài mm không đẻ thêm mặt mái
_MAX_LINES: Final = 8  # mỗi trục; chữ U cần 4. Quá mức này là hình răng cưa, không ghép chữ nhật nổi
_MAX_RECTS: Final = ROOF_NODES_MAX - 1
_MAX_MISFIT: Final = 0.02  # phần phủ thiếu, và phần phủ thừa, so với diện tích đường bao
_INSIDE: Final = 0.5 + 1e-6  # ô lưới bị cạnh chéo cắt đúng một nửa không tính là nằm trong


def roof_outline(rooms_mm: Sequence[Sequence[XY]], grow_mm: float) -> tuple[XY, ...] | RoofRefusal:
    """Đường bao phần nhà mà mái che: hợp các phòng, nới ra `grow_mm` (nửa bề dày tường ngoài), lấy biên ngoài.

    Lấy từ phòng chứ không từ tường để tường rào và tường chắn sân thượng không lọt vào. Nơi gọi tự bỏ phòng không có
    mái (loggia, sân thượng, ban công). Từ chối khi không còn phòng nào có diện tích, khi các phòng rời thành nhiều
    khối, hoặc khi đường bao có lỗ (sân trong, giếng trời). Phòng tự cắt được sửa bằng `make_valid`, giữ đủ các múi.
    `grow_mm` âm là lỗi của nơi gọi.
    """
    if grow_mm < 0:
        raise ValueError(f"grow_mm phải không âm, nhận {grow_mm}")
    rooms = [shapely.make_valid(shapely.Polygon(outline)) for outline in rooms_mm if len(outline) >= 3]
    polygons = [room for room in rooms if room.area > 0]
    if not polygons:
        return "no_rooms"
    grown = shapely.union_all(polygons).buffer(grow_mm + _CLOSE_MM, join_style="mitre")
    closed = shapely.normalize(grown.buffer(-_CLOSE_MM, join_style="mitre")).simplify(_SIMPLIFY_MM)
    if not isinstance(closed, shapely.Polygon):
        return "split"
    if closed.interiors:
        return "hole"
    return tuple((float(x), float(y)) for x, y in closed.exterior.coords[:-1])


def _main_axis_deg(outline: Sequence[XY]) -> float:
    """Góc trục chính trong [-90, 90): hướng cạnh cho khung bao nhỏ nhất, cạnh dài của khung dọc trục.

    Chỉ thử hướng của chính các cạnh, như `fitRoofFootprint` của Pascal: khung bao nhỏ nhất của bao lồi có thể nằm chéo
    với một chữ L mảnh.
    """
    candidates: list[tuple[float, float]] = []
    for (x0, y0), (x1, y1) in itertools.pairwise([*outline, outline[0]]):
        angle = math.atan2(y1 - y0, x1 - x0)
        cos, sin = math.cos(angle), math.sin(angle)
        us = [x * cos + y * sin for x, y in outline]
        vs = [-x * sin + y * cos for x, y in outline]
        width, depth = max(us) - min(us), max(vs) - min(vs)
        # Tròn diện tích tới mm² và nới 1 µm: nhiễu số thực không đổi khung giữa hai hướng cho cùng một khung bao.
        candidates.append((round(width * depth), angle if width + 1e-3 >= depth else angle + math.pi / 2))
    degrees = round(math.degrees(min(candidates)[1]), 6)
    return (degrees + 90.0) % 180.0 - 90.0


def _lines(values: Iterable[float]) -> list[float]:
    """Các đường lưới: toạ độ đã sắp, gom những giá trị cách đầu nhóm không quá `_SNAP_MM`, mỗi nhóm lấy điểm giữa."""
    groups: list[list[float]] = []
    for value in sorted(values):
        if groups and value - groups[-1][0] <= _SNAP_MM:
            groups[-1][-1] = value
        else:
            groups.append([value, value])
    return [(low + high) / 2 for low, high in groups]


def _maximal_boxes(us: Sequence[float], vs: Sequence[float], framed: shapely.Polygon) -> list[_Box]:
    """Các chữ nhật cực đại `(u0, v0, u1, v1)` ghép từ ô lưới nằm trong đa giác, lớn trước.

    Cực đại để mỗi cánh xuyên hết thân: Pascal chỉ cắt mặt nhỏ ở phần nằm trong ruột mặt lớn (`roof-overlap.ts:50-56`),
    cánh thò vào nông thì đầu hồi của nó lộ ra giữa mái thân.
    """
    inside = [
        [
            framed.intersection(shapely.box(u0, v0, u1, v1)).area > _INSIDE * (u1 - u0) * (v1 - v0)
            for v0, v1 in itertools.pairwise(vs)
        ]
        for u0, u1 in itertools.pairwise(us)
    ]
    spans = [
        (i0, j0, i1, j1)
        for i0, i1 in itertools.combinations(range(len(us)), 2)
        for j0, j1 in itertools.combinations(range(len(vs)), 2)
        if all(inside[i][j] for i in range(i0, i1) for j in range(j0, j1))
    ]
    boxes = [
        (us[i0], vs[j0], us[i1], vs[j1])
        for i0, j0, i1, j1 in spans
        if not any(
            other != (i0, j0, i1, j1) and other[0] <= i0 and other[1] <= j0 and other[2] >= i1 and other[3] >= j1
            for other in spans
        )
    ]
    return sorted(boxes, key=lambda box: ((box[0] - box[2]) * (box[3] - box[1]), box))


def roof_rects(outline_mm: Sequence[XY]) -> RoofRects | RoofRefusal:
    """Các chữ nhật mái phủ đường bao, hoặc lý do không dựng được.

    Gần chữ nhật (>= 0,96 khung bao) → một mặt phủ cả khung. Còn lại → nắn cạnh về lưới hai trục rồi lấy các chữ nhật
    cực đại; từ chối `poor_fit` khi chúng phủ thiếu hoặc thừa quá 2 % diện tích đường bao (hình xiên, cong), `complex`
    khi lưới quá dày hoặc cần quá số mặt một mái chứa được.
    """
    polygon = shapely.Polygon(outline_mm) if len(outline_mm) >= 3 else shapely.Polygon()
    if polygon.is_empty or not polygon.is_valid:
        return "poor_fit"
    angle = _main_axis_deg(outline_mm)
    framed = affinity.rotate(polygon, -angle, origin=(0, 0))
    bounds = framed.bounds
    if polygon.area >= _NEAR_RECT * (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]):
        boxes = [bounds]
    else:
        corners = shapely.get_coordinates(framed)
        us, vs = _lines(corners[:, 0].tolist()), _lines(corners[:, 1].tolist())
        if max(len(us), len(vs)) > _MAX_LINES:
            return "complex"
        boxes = _maximal_boxes(us, vs, framed)
        cover = shapely.union_all([shapely.box(*box) for box in boxes])
        if max(framed.difference(cover).area, cover.difference(framed).area) > _MAX_MISFIT * framed.area:
            return "poor_fit"
        if len(boxes) > _MAX_RECTS:
            return "complex"

    cos, sin = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return RoofRects(
        angle,
        tuple(
            RoofRect(
                round((u0 + u1) / 2 * cos - (v0 + v1) / 2 * sin, 3) + 0.0,
                round((u0 + u1) / 2 * sin + (v0 + v1) / 2 * cos, 3) + 0.0,
                round(u1 - u0, 3),
                round(v1 - v0, 3),
            )
            for u0, v0, u1, v1 in boxes
        ),
    )
