"""Kiểm hình học sau dựng (BENCH3D-01): kết quả sai về hình thì bật cờ `critical` của báo cáo chất lượng.

Tách khỏi `check_integrity` (kiểm đó còn chặn lượt lưu của người dùng); hàm thuần, chỉ đọc lớp.
Ngưỡng theo bộ chấm bench-3d: W1 vỏ kín (≥ 90 % phòng trong lỗ kín của vỏ), W4 mảnh vụn (mẩu trôi, tường đôi).
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely import LineString, Point, Polygon, STRtree

from apps.worker.pipeline_build.build import AreaLabel
from apps.worker.pipeline_build.clusters import Box, grow_region, wall_clusters
from apps.worker.pipeline_build.constants import FAR_CLUSTER_LENGTH_SHARE
from packages.domain.spatial.area import clear_area_m2
from packages.domain.spatial.model import Room, SpatialLayer, Wall

ENVELOPE_MIN_WIDTH_MM: Final = 100
"""Tường mỏng hơn vẫn vẽ dày 100 mm khi dò vỏ (W1)."""
ENVELOPE_PAD_MM: Final = 25
"""Nửa ô lưới 50 mm của W1: bán kính vùng tường = `max(bề dày, 100) / 2 + 25`."""
OPEN_ENVELOPE_SHARE: Final = 0.10
"""Quá 10 % phòng trong nhà không nằm trong lỗ kín của vỏ tường → vỏ hở (W1 "Đ" cần ≥ 90 %)."""
OPEN_ENVELOPE_MIN_LEAKED: Final = 2
OPEN_ENVELOPE_BIG_ROOM_M2: Final = 6.0
"""Chỉ chặn khi ≥ 2 phòng rò, hoặc phòng rò rộng ≥ 6 m²: một hộp kỹ thuật nhỏ lòi ra ngoài vỏ (cn-03) chỉ báo."""
OUTDOOR_USAGES: Final = frozenset({"loggia"})

STUB_MAX_MM: Final = 200
"""Mẩu tường ngắn hơn số này mà cách mọi tường khác > `STUB_CLEARANCE_MM` là mảnh vụn (W4)."""
STUB_CLEARANCE_MM: Final = 50
OVERLAP_ANGLE_DEG: Final = 5.0
OVERLAP_DISTANCE_MM: Final = 30
OVERLAP_SHARE: Final = 0.5
"""Hai tường gần song song (< 5°), cách < 30 mm, chồng > 50 % tường ngắn hơn là tường đôi (W4)."""
WALL_FRAGMENTS_MIN: Final = 3

PLAN_MAX_SPAN_MM: Final = 35_000
"""Một căn hộ hay nhà ở hiếm khi dài quá 30 m; hộp bao tường dài hơn 35 m là tờ mặt bằng cả tầng nhiều căn, kể cả khi
các căn dính liền nhau thành một cụm tường (BENCH3D-08). Đo bằng mm nên phụ thuộc tỉ lệ: xem `SCALE_ERROR`."""
SCALE_ERROR: Final[Mapping[str, float]] = {"chain": 0.0, "paired": 0.0, "note": 0.0, "door": 0.17}
"""Sai số tương đối của tỉ lệ theo `scaleBasis` (BENCH3D-14, đo bench-3d r3): span chặn khi `span·(1-e)` vẫn quá
`PLAN_MAX_SPAN_MM`, chỉ báo khi `span·(1+e)` quá. Nguồn khác (`wallThickness`, `default`, không rõ) lấy
`UNKNOWN_SCALE_ERROR`: ước bề dày tường lệch tới 42 %, trên tờ cả tầng còn đánh giá thấp 2-3 lần."""
UNKNOWN_SCALE_ERROR: Final = 0.42
SCALE_FREE_WALL_SPAN: Final = 10.0
SCALE_FREE_ROOM_SPAN: Final = 6.0
"""Chỉ báo không phụ thuộc tỉ lệ (tử và mẫu cùng nhân tỉ lệ): span tường / √(trung vị diện tích phòng) ≥ 10 **và**
span phòng / √(trung vị) ≥ 6 → nhiều căn. Bench-3d r3: căn đơn cao nhất 8,1 (span tường); vế phòng chặn nhà nhỏ trên
lô rộng (tường rào dài, phòng chỉ phủ một góc)."""

ROTATED_LABEL_SHARE: Final = 0.6
ROTATED_MIN_LABELS: Final = 3
"""`ROTATED_PLAN` (chỉ báo): ≥ 3 nhãn chữ có hướng và ≥ 60 % đọc dọc. Bench-can-ho 14 bản vẽ thường: cao nhất 12 %."""

AXIS_TOLERANCE_DEG: Final = 4.0
_AXIS_BINS_PER_DEG: Final = 10
_AXIS_BINS: Final = 90 * _AXIS_BINS_PER_DEG
MIN_AXIS_SHARE: Final = 0.65
"""BENCH3D-19: mặt bằng 2D vẽ tường theo trục vuông góc; ảnh phối cảnh 3D chiếu tường thành nhiều hướng xiên. Tổng dài
tường nằm trong ±4° của 2 họ hướng vuông góc trội (mỗi họ = hướng θ và θ+90°) dưới 65 % → không phải mặt bằng. Hai họ
để nhà có một cánh xiên vẫn qua. Đo trên lớp đã dựng (bench-3d tập F + r4b, `dieu-tra-f/to-ve.md` P2): 4 ảnh 3D
0,45-0,60; mặt bằng thấp nhất 0,69 (tháp có cánh xiên + vườn cong), mọi ca A/B/C/D ≥ 0,97."""

DOORLESS_USAGES_EXEMPT: Final = frozenset({"loggia", "corridor"})
DOORLESS_CRITICAL_SHARE: Final = 0.5
DOORLESS_CRITICAL_ROOMS: Final = 2
"""BENCH3D-24: ≥ 2 phòng và ≥ 50 % phòng (trừ miễn) không tường nào mang cửa đi → chặn. Một phòng thiếu cửa là cửa
dò sót lẻ; nửa căn không lối vào là bước dò cửa đi hỏng (căn ở thật mọi phòng đều có lối vào). Đo bench-3d r5c: bắt
f-04/f-16/f-17 (tập F), không ca ĐẠT nào bị kêu mới."""
AREA_TEXT_TOLERANCE: Final = 0.15
"""BENCH3D-24 `AREA_TEXT_MISMATCH`: bản vẽ ghi diện tích căn (tim tường hoặc thông thủy, `BuiltLayer.area_notes`).
Chặn khi **không** số nào nằm trong [Σ thông thủy·(1 - 0,15); Σ tim tường·(1 + 0,15)] của các phòng: 15 % trùm phần
tường ngoài, lô gia/hộp kỹ thuật ghi gộp; lệch hơn là thiếu phòng hay tỉ lệ sai."""
UNKNOWN_USAGE_SHARE: Final = 0.8
UNKNOWN_USAGE_CRITICAL_ROOMS: Final = 3
"""`ROOM_USAGE_UNKNOWN` chỉ chặn khi tầng có ít nhất 3 phòng (1-2 phòng "khác" là bản vẽ lạ, không phải lỗi)."""
DOOR_MIN_WIDTH_MM: Final = 550
DOOR_MAX_WIDTH_MM: Final = 3600
"""K-15 `OPENING_SIZE_IMPLAUSIBLE` (chỉ báo): cửa đi rộng ngoài [550; 3600] mm sau khi nới theo sai số tỉ lệ
(`SCALE_ERROR`, như `MULTIPLE_PLANS`): báo khi `rộng·(1+e) < 550` hoặc `rộng·(1-e) > 3600`. Sàn: cửa thật hẹp nhất
là cánh WC ~600 mm (bench O3 đòi ≥ 600); 550 chừa sai số đo hộp, bench r4b + F: không ca ĐẠT nào bật (600 bật 3).
Trần: cửa lùa/hai cánh hợp lệ tới 3600 (K-13a; cửa rộng nhất bench 3117). Cửa sổ không kiểm: dải kính dài có thật
tới hết tường chủ (O3, cn-11 8,35 m ĐẠT) và hộp hẹp hơn 300 mm đã bị bước dựng thay rộng mặc định."""


@dataclass(frozen=True)
class GeometryCheck:
    """Một kiểm hình học đã bật; `detail` là số đo để người đọc `quality.json` biết vì sao."""

    code: str
    critical: bool
    detail: Mapping[str, float | tuple[str, ...]]

    def to_wire(self) -> dict[str, object]:
        """Phần tử của khoá `checks` trong `quality.json`."""
        return {"code": self.code, "critical": self.critical, "detail": dict(self.detail)}


def _line(wall: Wall) -> LineString:
    """Đường tim của tường thành `LineString` (mm)."""
    c = wall.centreline
    return LineString([(c.start.x, c.start.y), (c.end.x, c.end.y)])


def _inside_point(room: Room) -> Point:
    """Một điểm chắc chắn nằm trong phòng (kể cả phòng lõm)."""
    return Polygon([(p.x, p.y) for p in room.outline]).representative_point()


def _open_envelope(walls: Sequence[Wall], lines: Sequence[LineString], rooms: Sequence[Room]) -> GeometryCheck | None:
    """Phòng trong nhà phải nằm trong một lỗ kín của hợp vùng tường (W1 lũ từ ngoài vào)."""
    indoor = [room for room in rooms if room.usage not in OUTDOOR_USAGES]
    if not walls or not indoor:
        return None
    body = shapely.union_all(
        [
            line.buffer(max(wall.thickness_mm, ENVELOPE_MIN_WIDTH_MM) / 2 + ENVELOPE_PAD_MM, cap_style="flat")
            for wall, line in zip(walls, lines, strict=True)
        ]
    )
    parts = getattr(body, "geoms", [body])
    filled = shapely.union_all([Polygon(part.exterior) for part in parts if not part.is_empty])
    leaked = [room for room in indoor if not filled.covers(_inside_point(room))]
    share = len(leaked) / len(indoor)
    if share <= OPEN_ENVELOPE_SHARE:
        return None
    critical = len(leaked) >= OPEN_ENVELOPE_MIN_LEAKED or any(
        room.area_m2 >= OPEN_ENVELOPE_BIG_ROOM_M2 for room in leaked
    )
    return GeometryCheck("OPEN_ENVELOPE", critical, {"leakedRooms": len(leaked), "share": round(share, 3)})


def _parallel(a: LineString, b: LineString) -> bool:
    """Hai đoạn lệch hướng dưới `OVERLAP_ANGLE_DEG` (không phân biệt chiều)."""
    (ax0, ay0), (ax1, ay1) = a.coords
    (bx0, by0), (bx1, by1) = b.coords
    angle = abs(math.atan2(ay1 - ay0, ax1 - ax0) - math.atan2(by1 - by0, bx1 - bx0)) % math.pi
    return min(angle, math.pi - angle) < math.radians(OVERLAP_ANGLE_DEG)


def _wall_fragments(lines: Sequence[LineString]) -> GeometryCheck | None:
    """Mẩu tường trôi + cặp tường đôi chồng nhau (W4)."""
    if len(lines) < 2:
        return None
    geoms = np.asarray(lines, dtype=object)
    tree = STRtree(geoms)
    stubs = 0
    for index, line in enumerate(lines):
        if line.length < STUB_MAX_MM:
            near = tree.query(line, predicate="dwithin", distance=STUB_CLEARANCE_MM)
            stubs += all(other == index for other in near.tolist())
    sources, targets = tree.query(geoms, predicate="dwithin", distance=OVERLAP_DISTANCE_MM)
    overlaps = 0
    for i, j in zip(sources.tolist(), targets.tolist(), strict=True):
        if i >= j or not _parallel(lines[i], lines[j]):
            continue
        short, long_ = sorted((lines[i], lines[j]), key=lambda line: line.length)
        if (
            short.length > 0
            and short.intersection(long_.buffer(OVERLAP_DISTANCE_MM)).length > OVERLAP_SHARE * short.length
        ):
            overlaps += 1
    if stubs + overlaps < WALL_FRAGMENTS_MIN:
        return None
    return GeometryCheck("WALL_FRAGMENTS", True, {"stubs": stubs, "overlaps": overlaps})


def _scale_free_spans(span_mm: float, rooms: Sequence[Room]) -> tuple[float, float] | None:
    """(span tường, span phòng) chia cạnh phòng trung vị √(trung vị diện tích); không phòng có diện tích → `None`."""
    areas = sorted(room.area_m2 for room in rooms if room.area_m2 > 0)
    if not areas:
        return None
    side_mm = math.sqrt(float(np.median(areas))) * 1000
    x0, y0, x1, y1 = shapely.MultiPoint([(p.x, p.y) for room in rooms for p in room.outline]).bounds
    return span_mm / side_mm, max(x1 - x0, y1 - y0) / side_mm


def _multiple_plans(
    walls: Sequence[Wall], lines: Sequence[LineString], rooms: Sequence[Room], scale_basis: str | None
) -> GeometryCheck | None:
    """Tờ nhiều mặt bằng: ≥ 2 vùng tường tách xa nhau, mỗi vùng giữ ≥ `FAR_CLUSTER_LENGTH_SHARE` tổng dài (bước dựng
    giữ cả hai); hoặc span lớn so với phòng trung vị (`SCALE_FREE_*`, không phụ thuộc tỉ lệ); hoặc hộp bao mọi tường
    dài quá `PLAN_MAX_SPAN_MM` trong dải sai số của nguồn tỉ lệ (`SCALE_ERROR`) — các căn liền nhau của một tầng."""
    if len(walls) < 2:
        return None
    groups = wall_clusters(np.asarray(lines, dtype=object), [float(wall.thickness_mm) for wall in walls])
    lengths = [sum(lines[i].length for i in group) for group in groups]
    boxes: list[Box] = [shapely.MultiLineString([lines[i] for i in group]).bounds for group in groups]
    total = sum(lengths)
    remaining = set(range(len(groups)))
    big = 0
    while remaining:
        _, rest = grow_region(boxes, remaining, max(remaining, key=lengths.__getitem__))
        big += sum(lengths[i] for i in remaining - rest) >= FAR_CLUSTER_LENGTH_SHARE * total
        remaining = rest
    x0, y0, x1, y1 = shapely.MultiLineString(list(lines)).bounds
    span = max(x1 - x0, y1 - y0)
    error = SCALE_ERROR.get(scale_basis or "", UNKNOWN_SCALE_ERROR)
    detail: dict[str, float] = {"plans": big, "spanMm": round(span)}
    ratios = _scale_free_spans(span, rooms)
    if ratios is not None:
        detail |= {"wallSpanRatio": round(ratios[0], 1), "roomSpanRatio": round(ratios[1], 1)}
    scale_free = ratios is not None and ratios[0] >= SCALE_FREE_WALL_SPAN and ratios[1] >= SCALE_FREE_ROOM_SPAN
    critical = big >= 2 or scale_free or span * (1 - error) > PLAN_MAX_SPAN_MM
    if not critical and span * (1 + error) <= PLAN_MAX_SPAN_MM:
        return None
    return GeometryCheck("MULTIPLE_PLANS", critical, detail)


def _door_centres(layer: SpatialLayer) -> dict[str, list[tuple[Point, float]]]:
    """Id tường → tâm các cửa đi trên nó kèm tầm chạm phòng (bề dày tường, mm)."""
    walls = {wall.id: wall for wall in layer.walls}
    found: dict[str, list[tuple[Point, float]]] = {}
    for opening in layer.openings:
        wall = walls.get(opening.wall_id)
        if opening.kind != "door" or wall is None:
            continue
        line = LineString((p.x, p.y) for p in (wall.centreline.start, wall.centreline.end))
        centre = line.interpolate(opening.offset_mm + opening.width_mm / 2)
        found.setdefault(wall.id, []).append((centre, float(wall.thickness_mm)))
    return found


def _has_door(room: Room, doors: Mapping[str, list[tuple[Point, float]]]) -> bool:
    """Phòng có cửa đi trên một tường của nó **và** tâm cửa sát biên phòng (≤ bề dày tường).

    Một tường dài chạy qua nhiều phòng (khúc thẳng hàng đã gộp, WALL-RUN) mang cửa của phòng khác: chỉ so `wallIds`
    thì mọi phòng dọc tường đó đều "có cửa".
    """
    near = [item for wall_id in room.wall_ids for item in doors.get(wall_id, ())]
    if not near:
        return False
    rim = Polygon([(p.x, p.y) for p in room.outline]).exterior
    return any(rim.distance(centre) <= reach for centre, reach in near)


def _rooms_without_door(layer: SpatialLayer) -> GeometryCheck | None:
    """Có phòng mà không ô mở nào → chặn; còn lại chỉ báo tỉ lệ phòng không tường nào mang cửa đi.

    Phòng loại miễn (`DOORLESS_USAGES_EXEMPT`) bị lọc trước ở cả hai nhánh: tầng chỉ có lô gia/hành lang thì không bật.
    """
    rooms = [room for room in layer.rooms if room.usage not in DOORLESS_USAGES_EXEMPT]
    if not rooms:
        return None
    if not layer.openings:
        return GeometryCheck("ROOMS_WITHOUT_DOOR", True, {"rooms": len(rooms), "openings": 0})
    doors = _door_centres(layer)
    doorless = tuple(room.id for room in rooms if not _has_door(room, doors))
    if not doorless:
        return None
    share = len(doorless) / len(rooms)
    critical = len(doorless) >= DOORLESS_CRITICAL_ROOMS and share >= DOORLESS_CRITICAL_SHARE
    detail: dict[str, float | tuple[str, ...]] = {
        "doorlessRooms": len(doorless),
        "share": round(share, 3),
        "roomIds": doorless,
    }
    return GeometryCheck("ROOMS_WITHOUT_DOOR", critical, detail)


def _area_text_mismatch(layer: SpatialLayer, area_notes: Sequence[float]) -> GeometryCheck | None:
    """Tổng diện tích phòng lệch mọi số "N m²" ghi trên bản vẽ quá `AREA_TEXT_TOLERANCE` → chặn (kiểm lại tỉ lệ hay
    phòng thiếu). `ratio` = Σ tim tường / số ghi gần nhất (theo log).

    Tầng ≥ 2 phòng: số khớp diện tích **một** phòng (trong dung sai) là nhãn phòng lớn ("P.KHÁCH 35 m²"), không phải
    diện tích căn → bỏ trước khi so. Biên làm tròn 0,01 m² như `areaM2` (số ghi đúng biên không bị báo vì sai số float).
    """
    rooms = [float(room.area_m2) for room in layer.rooms]
    if len(rooms) >= 2:
        area_notes = [note for note in area_notes if not any(_within(note, area, area) for area in rooms)]
    gross = sum(rooms)
    if not area_notes or gross <= 0:
        return None
    clear = sum(float(clear_area_m2(room.outline, layer.walls) or room.area_m2) for room in layer.rooms)
    if any(_within(note, clear, gross) for note in area_notes):
        return None
    near = min(area_notes, key=lambda note: abs(math.log(gross / note)))
    detail = {"roomsM2": round(gross, 1), "textM2": near, "ratio": round(gross / near, 3)}
    return GeometryCheck("AREA_TEXT_MISMATCH", True, detail)


def _within(note: float, low: float, high: float) -> bool:
    """`note` nằm trong [low·(1 - dung sai); high·(1 + dung sai)], biên làm tròn 0,01 m²."""
    return round(low * (1 - AREA_TEXT_TOLERANCE), 2) <= note <= round(high * (1 + AREA_TEXT_TOLERANCE), 2)


def _opening_size_implausible(layer: SpatialLayer, scale_basis: str | None) -> GeometryCheck | None:
    """Cửa đi rộng phi lý (K-15): hộp dò sai hay tỉ lệ lệch; chỉ báo, không chặn. Tỉ lệ `default` (máy không đo
    được, đã có `scaleUnresolved`) thì mm vô nghĩa nên bỏ qua; nguồn tỉ lệ khác nới dải theo `SCALE_ERROR`."""
    if scale_basis == "default":
        return None
    error = SCALE_ERROR.get(scale_basis or "", UNKNOWN_SCALE_ERROR)
    odd = [
        door
        for door in layer.openings
        if door.kind == "door"
        and (door.width_mm * (1 + error) < DOOR_MIN_WIDTH_MM or door.width_mm * (1 - error) > DOOR_MAX_WIDTH_MM)
    ]
    if not odd:
        return None
    widths = [door.width_mm for door in odd]
    detail: dict[str, float | tuple[str, ...]] = {
        "doors": len(odd),
        "minWidthMm": min(widths),
        "maxWidthMm": max(widths),
        "openingIds": tuple(door.id for door in odd),
    }
    return GeometryCheck("OPENING_SIZE_IMPLAUSIBLE", False, detail)


def _room_area_text_mismatch(
    layer: SpatialLayer, labels: Sequence[AreaLabel], scale_basis: str | None
) -> GeometryCheck | None:
    """K-16 (chỉ báo): phòng chứa nhãn "N m²" mà không nhãn nào trong [thông thủy·(1 - 0,15); tim tường·(1 + 0,15)]
    của phòng đó → phòng gộp hai phòng thật hay bị chia sai, dù tổng vẫn khớp `AREA_TEXT_MISMATCH`.

    Chỉ xét nhãn nằm hẳn trong đúng một phòng (ngoài mọi phòng hay trên biên/chồng hai phòng: bỏ). Nhãn khớp diện tích
    cả tầng (tầng ≥ 2 phòng) là diện tích căn đặt trong phòng khách, không phải nhãn phòng → bỏ. Tỉ lệ `default` → bỏ
    (diện tích vô nghĩa, đã có `scaleUnresolved`). `worstRatio` = diện tích phòng / nhãn lệch nhất (theo log).
    """
    rooms = layer.rooms
    if not labels or not rooms or scale_basis == "default":
        return None
    gross = sum(float(room.area_m2) for room in rooms)
    clear = sum(float(clear_area_m2(room.outline, layer.walls) or room.area_m2) for room in rooms)
    if len(rooms) >= 2:
        labels = [label for label in labels if not _within(label.m2, clear, gross)]
    tree = STRtree([Polygon([(p.x, p.y) for p in room.outline]) for room in rooms])
    found: dict[int, list[float]] = {}
    for label in labels:
        hits = tree.query(Point(label.x_mm, label.y_mm), predicate="within").tolist()
        if len(hits) == 1:
            found.setdefault(hits[0], []).append(label.m2)
    odd = [(rooms[i], values) for i, values in sorted(found.items()) if not _room_matches(layer, rooms[i], values)]
    if not odd:
        return None
    ratios = [float(room.area_m2) / value for room, values in odd for value in values]
    worst: float = max(ratios, key=lambda ratio: abs(math.log(ratio)))
    detail: dict[str, float | tuple[str, ...]] = {
        "rooms": len(odd),
        "labelledRooms": len(found),
        "worstRatio": round(worst, 3),
        "roomIds": tuple(room.id for room, _ in odd),
    }
    return GeometryCheck("ROOM_AREA_TEXT_MISMATCH", False, detail)


def _room_matches(layer: SpatialLayer, room: Room, values: Sequence[float]) -> bool:
    """Một nhãn trong phòng nằm trong dải thông thủy-tim tường (dung sai `AREA_TEXT_TOLERANCE`) của phòng đó."""
    clear = float(clear_area_m2(room.outline, layer.walls) or room.area_m2)
    return any(_within(value, clear, float(room.area_m2)) for value in values)


def _room_usage_unknown(rooms: Sequence[Room]) -> GeometryCheck | None:
    """Phần lớn phòng là `other`: bước gán công năng hỏng; chặn từ `UNKNOWN_USAGE_CRITICAL_ROOMS` phòng."""
    if not rooms:
        return None
    share = sum(1 for room in rooms if room.usage == "other") / len(rooms)
    if share < UNKNOWN_USAGE_SHARE:
        return None
    critical = len(rooms) >= UNKNOWN_USAGE_CRITICAL_ROOMS
    return GeometryCheck("ROOM_USAGE_UNKNOWN", critical, {"share": round(share, 3)})


def _rotated_plan(label_orientation: Mapping[str, int]) -> GeometryCheck | None:
    """Phần lớn nhãn chữ đọc dọc → trang xoay 90°; chỉ báo, không chặn (mô hình dựng vẫn đúng hình)."""
    vertical, horizontal = label_orientation.get("vertical", 0), label_orientation.get("horizontal", 0)
    labels = vertical + horizontal
    if labels < ROTATED_MIN_LABELS or vertical < ROTATED_LABEL_SHARE * labels:
        return None
    detail = {"verticalLabels": vertical, "horizontalLabels": horizontal, "share": round(vertical / labels, 3)}
    return GeometryCheck("ROTATED_PLAN", False, detail)


def axis_share(walls: Sequence[Wall]) -> float:
    """Phần tổng dài tường phủ được bởi cặp họ hướng vuông góc tốt nhất (mỗi họ ±`AXIS_TOLERANCE_DEG` quanh θ và
    θ+90°). Hướng gom vào ô 0,1° (mod 90°); xét mọi cặp tâm ô, phần hai cửa sổ chồng nhau chỉ tính một lần — không
    lưới thô, không chọn tham lam. Không tường → 1 (không có gì để nghi)."""
    ends = np.asarray(
        [(w.centreline.start.x, w.centreline.start.y, w.centreline.end.x, w.centreline.end.y) for w in walls],
        dtype=np.float64,
    ).reshape(-1, 4)
    dx, dy = ends[:, 2] - ends[:, 0], ends[:, 3] - ends[:, 1]
    lengths = np.hypot(dx, dy)
    total = float(lengths.sum())
    if total <= 0:
        return 1.0
    bins = np.floor(np.degrees(np.arctan2(dy, dx)) % 90 * _AXIS_BINS_PER_DEG).astype(np.int64) % _AXIS_BINS
    hist = np.bincount(bins, weights=lengths, minlength=_AXIS_BINS)
    prefix = np.concatenate(([0.0], np.cumsum(np.concatenate((hist, hist)))))
    half = round(AXIS_TOLERANCE_DEG * _AXIS_BINS_PER_DEG)
    centres = np.arange(_AXIS_BINS)

    def arc(first: NDArray[np.int64], count: int) -> NDArray[np.float64]:
        """Tổng `hist` của `count` ô liền nhau (vòng) bắt đầu từ ô `first`."""
        start = first % _AXIS_BINS
        return prefix[start + count] - prefix[start]

    cover = arc(centres - half, 2 * half + 1)
    best = 0.0
    for gap in range(_AXIS_BINS - 2 * half):  # cặp (i, i+gap); gap lớn hơn là cặp đối xứng đã xét
        other = (centres + gap) % _AXIS_BINS
        shared = arc(other - half, 2 * half + 1 - gap) if gap <= 2 * half else 0.0
        best = max(best, float((cover + cover[other] - shared).max()))
    return best / total


def not_a_floor_plan(layer: SpatialLayer) -> float | None:
    """Tỉ lệ `axis_share` khi lớp trông như ảnh phối cảnh (dưới `MIN_AXIS_SHARE`), không thì `None`.

    Bước dựng gọi trước khi ghi `layer.json` và đánh hỏng lượt bằng `NOT_A_FLOOR_PLAN`: mô hình dựng từ ảnh 3D không
    sửa được bằng tay nên không đưa vào tầng (kiểm ở bước quality thì lớp đã ghi đè tầng)."""
    share = axis_share(layer.walls)
    return share if share < MIN_AXIS_SHARE else None


def geometry_checks(
    layer: SpatialLayer,
    *,
    scale_basis: str | None = None,
    label_orientation: Mapping[str, int] | None = None,
    area_notes: Sequence[float] = (),
    area_labels: Sequence[AreaLabel] = (),
) -> tuple[GeometryCheck, ...]:
    """Mọi kiểm hình học đã bật, thứ tự cố định; `scale_basis` (của `layer.json`, vắng = không rõ) đặt dải sai số
    tỉ lệ cho `MULTIPLE_PLANS` và `OPENING_SIZE_IMPLAUSIBLE`, `label_orientation` (cũng của `layer.json`) cho
    `ROTATED_PLAN`, `area_notes` (cũng của `layer.json`) cho `AREA_TEXT_MISMATCH`, `area_labels` (cũng của
    `layer.json`) cho `ROOM_AREA_TEXT_MISMATCH`."""
    walls = layer.walls
    lines = [_line(wall) for wall in walls]
    found = (
        _open_envelope(walls, lines, layer.rooms),
        _wall_fragments(lines),
        _multiple_plans(walls, lines, layer.rooms, scale_basis),
        _rooms_without_door(layer),
        _room_usage_unknown(layer.rooms),
        _rotated_plan(label_orientation or {}),
        _area_text_mismatch(layer, area_notes),
        _opening_size_implausible(layer, scale_basis),
        _room_area_text_mismatch(layer, area_labels, scale_basis),
    )
    return tuple(check for check in found if check is not None)
