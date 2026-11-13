"""Tường, nối khe ô mở và ô mở (B5-05 [6] bước 3, 3b, 4); khung kiểu dùng chung do prep đặt.

Hình học chạy bằng `shapely` 2.x vector hoá: một `linestrings` cho mọi đường tim và một
`STRtree` **mỗi pha** (lọc khối cô lập, 3b, bước 4, hộp ô mở — 3b sửa hình học tại chỗ nên
cây của pha trước hết đúng), truy vấn theo lô (`dwithin`) — trần B5-01 là
20.000 tường và 5.000 hộp nên vòng Python qua mọi cặp tường là không chạy nổi.
Toạ độ mm: gốc trên-trái, y hướng xuống.
"""

import itertools
import logging
import math
from collections import Counter, deque
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Final, Literal

import cv2
import numpy as np
import shapely
from numpy.typing import NDArray
from shapely import LineString, STRtree

from apps.worker.pipeline_build.clusters import (
    Box,
    axis_share,
    box_area,
    grow_region,
    neighbours,
    region_labels,
    wall_clusters,
)
from apps.worker.pipeline_build.constants import (
    CORNER_OVERSHOOT_MM,
    DEFAULT_WALL_HEIGHT_MM,
    DIAGONAL_ALIGN_DEG,
    FAR_CLUSTER_AREA_SHARE,
    FAR_CLUSTER_LENGTH_SHARE,
    OPENING_GAP_MAX_MM,
    OPENING_MIN_WIDTH_MM,
    OPENING_WALL_REACH_MM,
    WALL_JOIN_MM,
)
from apps.worker.pipeline_build.ids import new_spatial_id
from apps.worker.pipeline_build.residual import INK_GRAY, PageRaster
from apps.worker.pipeline_build.swing import _hit, read_leaf
from packages.core.clock import Clock
from packages.domain.rules_ai import outline_contains
from packages.domain.rules_ai.geometry import wall_side_by
from packages.domain.spatial import Opening, Point, Room, Segment, Wall, js_round
from packages.domain.spatial.thickness import snap_thickness
from packages.ml_contracts.artifacts import BoxPx, DetectionPx, WallPx
from packages.ml_contracts.labels import LABEL_TARGETS, LabelTarget, SwingDirection

Geoms = NDArray[np.object_]
_log = logging.getLogger(__name__)

_ALIGN_SIN: Final = math.sin(math.radians(2.0))
"""Lệch góc tối đa của cặp tường thẳng hàng (2°, B5-05 [6] 3b) đo bằng `|sin|` để khỏi `atan2`."""
_PERPENDICULAR_COS: Final = math.cos(math.radians(88.0))
"""Cặp tường vuông góc: `|cos|` của góc giữa hai hướng ≤ cos(88°), tức lệch 90° không quá 2°."""
_DIAGONAL_ALIGN_SIN: Final = math.sin(math.radians(DIAGONAL_ALIGN_DEG))
"""`_ALIGN_SIN` cho cặp tường chéo (`DIAGONAL_ALIGN_DEG`, BUG-IT-007)."""
_DIAGONAL_PERPENDICULAR_COS: Final = math.cos(math.radians(90.0 - DIAGONAL_ALIGN_DEG))
"""`_PERPENDICULAR_COS` cho cặp tường chéo (`DIAGONAL_ALIGN_DEG`, BUG-IT-007)."""
_DIAGONAL_MIN_SIN2: Final = math.sin(math.radians(2 * DIAGONAL_ALIGN_DEG))
"""Tường lệch trục gần nhất > `DIAGONAL_ALIGN_DEG` khi `|sin 2θ| = |2·ux·uy|` vượt số này."""
_MIN_DOUBLE_DOOR_MM: Final = 600
_MAX_SINGLE_LEAF_MM: Final = 1200
"""Cửa một cánh rộng hơn ngần này không có thật: đọc được cung mở mà rộng hơn → hai cánh (C3)."""
_SLIDING_MIN_MM: Final = 1500
"""Cửa đi không đọc được cung mở mà rộng ≥ ngần này là cửa lùa (C3)."""
_SPLIT_GAP_MM: Final = 60
_ENVELOPE_DOOR_MIN_MM: Final = 600
"""Khe thẳng hàng trên tường ngoài ≥ ngần này là cửa bộ dò bỏ sót (C5); hẹp hơn thì xây kín."""
_GUESSED_DOOR_CONFIDENCE: Final = 0.3
"""Cửa đoán từ khe tường ngoài, không đọc được cánh và hẹp hơn cửa lùa: tin thấp để người duyệt thấy (C5)."""
_INFERRED_DOOR_CONFIDENCE: Final = 0.5
"""Cửa đoán từ khe tường ngoài mà đọc được cánh hay đủ rộng thành cửa lùa (C5)."""
"""Hai ô mở cùng loại cùng tường cách nhau ≤ ngần này là một ô mở bị bộ dò cắt đôi (C4: 1400 + 600 cách 3-40 mm)."""
"""Cửa đôi hẹp hơn 600 mm là cửa đơn bị dò nhầm lớp: giữ thành cửa đơn (B11, chẩn đoán quy trình lần 2)."""
_OPENING_REACH_SHARE: Final = 0.25
"""Tầm gắn ô mở theo cỡ hộp: hộp rộng `w` với tới tường cách ≤ 0,25·w (vẫn không dưới ½ dày + 150 mm)."""
_GAP_PARALLEL_COS: Final = math.cos(math.radians(20.0))
"""Tường lệch hướng hộp ô mở ≤ 20° là cùng hướng: bước 3c không dựng tường qua khe (OPEN-ATTACH)."""
# 3b (d): đầu tường lơ lửng nhìn thẳng gặp vách trong khe mặt-tới-mặt ≤ max(450, 3 x bề dày) → kéo tới chạm.
_DANGLE_REACH_MM: Final = 450
_DANGLE_REACH_THICKNESS: Final = 3
# Khe có tâm hộp ô mở → khe cửa, kéo xa tới 1200 mm (cửa đơn ≤ 1000 + mép); khe 2 m giữ nguyên.
_DANGLE_DOOR_REACH_MM: Final = 1200
# 3b (e): mẩu tường ngắn chạy song song sát mặt một tường dày hơn (nét bậu cửa, nét kép) mà có đầu lơ lửng.
_HUG_STUB_MAX_MM: Final = 600
_HUG_FACE_MAX_MM: Final = 150
_HUG_COS_MIN: Final = math.cos(math.radians(10))
_HAIRLINE_MM: Final = 40
"""Bề dày đo dưới ngần này là nét mảnh (kính cửa lùa, nét ký hiệu), không phải tường; bậc chuẩn từng nâng lên 100."""
_NIB_MAX_MM: Final = 200
"""Mẩu ngắn hơn ngần này có đầu lơ lửng, không mang ô mở là vụn nét, không phải tường (C2)."""
# Đầu mút "chạm" tường khác khi cách tim ≤ ½ tổng bề dày + 10 mm (cùng thước của bộ đo F:/runs/r4-bd/tidy.py).
_TOUCH_SLACK_MM: Final = 10
# Luật WALL-OVERLAP của FE (AppFront rules/geometry): song song <= 5 độ chạm thân và chồng > 10 mm, hoặc tim cắt
# nhau cách mọi đầu > 50 mm. Lần kéo nào làm tăng số cặp như vậy bị hoàn lại.
_PARALLEL_SIN: Final = math.sin(math.radians(5.0))
_OVERLAP_MIN_MM: Final = 10
_JOINT_MM: Final = 50
_OPENING_LABELS: Final[frozenset[str]] = frozenset({"door", "double_door", "window"})
"""Chỉ ba nhãn này thành ô mở; nhãn đồ đạc là việc của `rooms.build_furniture`, không đếm ở đây."""
_NO_GUESSED_DOOR: Final = frozenset({"loggia", "utility", "other"})
"""Phòng phía trong khe tường ngoài là lô gia/ban công, kỹ thuật, khác: khe là lan can hay khung kính, không phải cửa
đi bộ dò bỏ sót (bench r3: không cửa đoán kiểu này nào khớp đáp án) → khép bằng tường đặc (R2')."""
_GLAZING_HIT: Final = 0.8
"""Một nét kính: ≥ 80 % điểm mẫu dọc khe (giữa 70 % khe) rơi vào mực ngoài mặt nạ tường."""
_GLAZING_SOLID: Final = 0.6
"""Quá 60 % dải bề dày là mực liền: khối đặc (tường bộ dò bỏ sót, gạch chéo), không phải nét kính."""
_OPENING_NEAR_MM: Final = 300
"""Ô mở cùng loại trên tường song song cách đường khe ≤ ngần này, khoảng chiếu giao khe: hộp đã thành ô mở."""
_BOX_TARGETS: Final[Mapping[str, LabelTarget]] = MappingProxyType(
    {"door": LABEL_TARGETS["door"], "double_door": LABEL_TARGETS["double_door"], "window": LABEL_TARGETS["window"]}
)
"""Nhãn hộp sinh đoạn khép phòng → ô mở dựng trên tường qua khe đó (C5)."""
_BRIDGE: Final = "bridge"
"""Nguồn khe là đoạn nối 3b (`WallSet.bridges`), khác nhãn hộp/`None` của `RoomCloser`."""


@dataclass(frozen=True, slots=True)
class Scaling:
    """Hai tỉ lệ của một lượt dựng (mm/px): `build` = `s_dựng`, `final` = `s` ghi ra dây.

    Bước 3-8 đổi px → mm bằng `build`; `final` chỉ dùng để thấy trước tường sẽ suy biến sau
    `rescale_unreviewed(build → final)` (bước 3, `project_default`).
    """

    build: float
    final: float

    def mm(self, value_px: float) -> int:
        """Độ dài/toạ độ px → mm nguyên ở `s_dựng` (`js_round`, K20)."""
        return js_round(value_px * self.build)

    def point(self, x_px: float, y_px: float) -> Point:
        """Điểm px → `Point` mm ở `s_dựng`."""
        return Point(x=self.mm(x_px), y=self.mm(y_px))


@dataclass(frozen=True, slots=True)
class WallSet:
    """Đầu ra bước 3-4 cho bước 5 (phòng) và bước 7 (kích thước).

    `walls` đã có `openingIds`; `input_to_wall[j]` = chỉ số trong `walls` của tường đầu vào `j`
    còn giữ (tường gộp ở 3b: cả hai `j` trỏ về tường gộp; tường bị bỏ: vắng khoá);
    `bridges` = đoạn nối khe thẳng hàng không gộp (mm), chỉ để khép phòng, không ghi ra lớp.
    """

    walls: tuple[Wall, ...]
    openings: tuple[Opening, ...]
    input_to_wall: Mapping[int, int]
    bridges: tuple[tuple[Point, Point], ...]
    leaves: Mapping[str, tuple[float, float]] = MappingProxyType({})
    """Id ô mở → hướng cánh đọc từ nét vẽ, cho `swing.settle_reversed` sau khi có phòng (QT-SWING)."""


@dataclass(frozen=True, slots=True)
class RoomCloser:
    """Đoạn `rooms.build_rooms` dùng khép phòng mà không có tường (`_gap_closers`, `_door_closers`), mm.

    `start` = đầu tường mà đoạn xuất phát, `end` = chân trên tim tường đích; `label` = nhãn hộp ô mở sinh ra đoạn
    (`door`, `double_door`, `window`), `None` = khe góc/chữ T không hộp. `close_envelope_gaps` dựng tường cho đoạn
    nằm ở mặt ngoài (C5).
    """

    start: Point
    end: Point
    label: str | None = None


@dataclass(slots=True)
class _Seg:
    """Tường đang dựng ở mm, sửa tại chỗ trong 3b.

    `inputs` = mọi chỉ số trong `walls` đã gộp vào (dựng `input_to_wall`), `measured_mm` là bề dày
    **đo** chưa bắt về chuẩn (ngưỡng khối cô lập), `alive` tắt khi bị tường khác hấp thu.
    """

    start: Point
    end: Point
    thickness_mm: int
    measured_mm: float
    confidence: float
    inputs: tuple[int, ...]
    alive: bool = True


@dataclass(frozen=True, slots=True)
class _Cand:
    """Ô mở ứng viên trước khi xử chồng khoảng; `order` là thứ tự trong `detections`."""

    wall: int
    order: int
    kind: Literal["door", "window"]
    offset_mm: int
    width_mm: int
    height_mm: int
    sill_mm: int
    swing: SwingDirection
    confidence: float
    leaf: tuple[float, float] | None = None
    """Hướng cánh tuyệt đối trên mặt bằng đọc từ nét vẽ (QT-SWING); `None` = chưa đọc được."""


@dataclass(frozen=True, slots=True)
class _Boxes:
    """Hộp ô mở (mm) cùng `STRtree` của chúng: trả lời câu hỏi khe nào có hộp cửa (3b)."""

    rects: tuple[tuple[float, float, float, float], ...]
    tree: STRtree

    def covers(self, start: Point, end: Point) -> bool:
        """Một hộp phủ ≥ 50 % chiều dài khe `start→end` khi chiếu lên trục khe."""
        span = math.dist((start.x, start.y), (end.x, end.y))
        if span <= 0:
            return False
        unit = ((end.x - start.x) / span, (end.y - start.y) / span)
        gap = LineString(((start.x, start.y), (end.x, end.y)))
        return any(_projection_covers(self.rects[index], start, unit, span) for index in _hits(self.tree, gap))


@dataclass(frozen=True, slots=True)
class _Centrelines:
    """Tim tường lúc vào 3b + `STRtree`: kiểm khe có cắt tim một tường **khác** hay không.

    Tường gộp ở 3b là hợp của các tường vào trên cùng đường thẳng, nên tra trên tập lúc vào
    vẫn đúng và khỏi dựng lại cây sau mỗi lần gộp.
    """

    tree: STRtree
    slot_by_input: Mapping[int, int]

    def crosses(self, start: Point, end: Point, inputs: Sequence[int]) -> bool:
        """Khe cắt tim tường nào ngoài các tường vào `inputs` (hai đầu khe nằm trên chính chúng)."""
        skip = {self.slot_by_input[index] for index in inputs}
        gap = LineString(((start.x, start.y), (end.x, end.y)))
        return any(index not in skip for index in _hits(self.tree, gap))


def _hits(tree: STRtree, geom: shapely.Geometry) -> list[int]:
    """Chỉ số các hình trong cây giao với `geom` (một truy vấn, không quét tuyến tính)."""
    return [int(index) for index in tree.query(geom, predicate="intersects")]


def _projection_covers(
    rect: tuple[float, float, float, float], origin: Point, unit: tuple[float, float], span: float
) -> bool:
    """Hình chiếu hộp `rect` lên trục `unit` từ `origin` phủ ≥ 50 % đoạn `[0, span]`."""
    values = [(x - origin.x) * unit[0] + (y - origin.y) * unit[1] for x in rect[0::2] for y in rect[1::2]]
    low, high = max(min(values), 0.0), min(max(values), span)
    return high - low >= 0.5 * span


def _unit(seg: _Seg) -> tuple[float, float]:
    """Hướng đơn vị của tim tường (tường dài 0 đã bị bỏ ở bước 3 nên không chia cho 0)."""
    length = _length(seg)
    return ((seg.end.x - seg.start.x) / length, (seg.end.y - seg.start.y) / length)


def _length(seg: _Seg) -> float:
    """Chiều dài tim tường (mm, số thực để so với bề dày đo)."""
    return math.dist((seg.start.x, seg.start.y), (seg.end.x, seg.end.y))


def _linestrings(segs: Sequence[_Seg]) -> Geoms:
    """Mọi tim tường thành mảng `LineString` trong **một** lần gọi `shapely.linestrings`."""
    coords = [[[seg.start.x, seg.start.y], [seg.end.x, seg.end.y]] for seg in segs]
    return np.asarray(shapely.linestrings(coords), dtype=object)


def _degenerate_final(p0: Point, p1: Point, thickness_mm: int, scaling: Scaling) -> bool:
    """`project_default`: tường sẽ suy biến sau `rescale_unreviewed(s_dựng → s)` (bước 3).

    Dựng ở `s_dựng` rồi mới đổi về `s`, nên tường 0,3 px phải bị bỏ **ngay** — không thì
    `rescale_unreviewed` gặp `Segment` dài 0 và ném.
    """
    if scaling.build == scaling.final:
        return False
    ratio = scaling.final / scaling.build
    scaled = [(js_round(point.x * ratio), js_round(point.y * ratio)) for point in (p0, p1)]
    return scaled[0] == scaled[1] or js_round(thickness_mm * ratio) == 0


def _measure_walls(walls: Sequence[WallPx], scaling: Scaling, dropped: Counter[str]) -> list[_Seg]:
    """Bước 3: px → mm theo thứ tự đầu vào, bỏ tường suy biến, bắt bề dày về bảng chuẩn."""
    segs: list[_Seg] = []
    for index, wall in enumerate(walls):
        p0 = scaling.point(wall.start.x, wall.start.y)
        p1 = scaling.point(wall.end.x, wall.end.y)
        measured = wall.thickness_px * scaling.build
        thickness = snap_thickness(measured)
        if p0 == p1 or _degenerate_final(p0, p1, thickness, scaling):
            dropped["wallZeroLength"] += 1
            continue
        segs.append(_Seg(p0, p1, thickness, measured, wall.confidence, (index,)))
    return segs


def _drop_isolated(segs: list[_Seg], dropped: Counter[str]) -> list[_Seg]:
    """Bước 3: khối dài ≤ bề dày đo mà không tường nào khác cách tim ≤ `WALL_JOIN_MM` là khối cô lập."""
    if not segs:
        return segs
    geoms = _linestrings(segs)
    near = neighbours(STRtree(geoms), geoms, float(WALL_JOIN_MM))
    kept = [seg for index, seg in enumerate(segs) if _length(seg) > seg.measured_mm or near[index]]
    dropped["isolatedBlock"] += len(segs) - len(kept)
    return kept


def _drop_hairlines(segs: list[_Seg], dropped: Counter[str]) -> list[_Seg]:
    """Bước 3: bỏ tường đo mỏng hơn `_HAIRLINE_MM` (đếm `isolatedBlock`).

    Chỉ khi bản vẽ có tường dày ≥ `_HAIRLINE_MM`: cả bản toàn nét mảnh là tỉ lệ sai, bỏ hết thì mất nhà.
    """
    kept = [seg for seg in segs if seg.measured_mm >= _HAIRLINE_MM]
    if not kept:
        return segs
    dropped["isolatedBlock"] += len(segs) - len(kept)
    return kept


def _inside(dup: _Seg, host: _Seg) -> bool:
    """`dup` song song (≤ 10°), đo mỏng hơn `host`, hai đầu cách tim `host` ≤ ½ dày, chiếu trong `host` ± ½ dày."""
    if host is dup or host.measured_mm <= dup.measured_mm:
        return False
    (ax, ay), (bx, by) = _unit(dup), _unit(host)
    if abs(ax * bx + ay * by) < _HUG_COS_MIN:
        return False
    half, span = host.thickness_mm / 2, _length(host)
    for tip in (dup.start, dup.end):
        along = (tip.x - host.start.x) * bx + (tip.y - host.start.y) * by
        side = abs((tip.x - host.start.x) * by - (tip.y - host.start.y) * bx)
        if not -half <= along <= span + half or side > half:
            return False
    return True


def _drop_inside_body(segs: list[_Seg], dropped: Counter[str]) -> list[_Seg]:
    """Bước 3: tường nằm trong thân một tường dày hơn (nét kính cửa lùa giữa tường 220) là nét trùng → bỏ.

    Không giới hạn dài, không tha khi chạm hộp ô mở: ô mở về tường chủ ở bước 4 (C2).
    """
    if not segs:
        return segs
    geoms = _linestrings(segs)
    near = neighbours(STRtree(geoms), geoms, max(seg.thickness_mm for seg in segs) / 2)
    kept = [seg for index, seg in enumerate(segs) if not any(_inside(seg, segs[other]) for other in near[index])]
    dropped["isolatedBlock"] += len(segs) - len(kept)
    return kept


def _bbox(segs: Sequence[_Seg], group: Sequence[int]) -> Box:
    xs = [p.x for i in group for p in (segs[i].start, segs[i].end)]
    ys = [p.y for i in group for p in (segs[i].start, segs[i].end)]
    return (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)))


_STRAY_CLUSTER_MM: Final = 600.0
_STRAY_CLUSTER_SHARE: Final = 0.03
"""Mẩu lạc: cụm tổng dài < min(600 mm, 3 % tổng tường) mà hộp lòi khỏi hộp cụm dài nhất quá bề dày lớn nhất.

Mẩu vạch kích thước, la bàn cạnh căn (ch-13: 255 mm cách căn 880 mm, nới hộp bao thêm ~1 m) nằm trong
`FAR_CLUSTER_GAP_MM` nên vùng chính nuốt nó. Bench r3: không tường nào trong khung căn đáp án bị bỏ ở ngưỡng này;
1500 mm bỏ 20 tường thật ở tờ nhiều căn."""

_STRAY_SHELL_BAND_MM: Final = 1000.0
"""Mẩu lạc cách hộp cụm dài nhất ≤ 1 m (trụ ban công, má cửa kính nhô) chỉ bị bỏ khi nằm trên đường gióng của một
tường cụm chính (`_on_extension`, ch-13: vạch gióng ở chuỗi kích thước); xa hơn thì bỏ không cần chứng cứ thêm."""


def _on_extension(seg: _Seg, main: Sequence[_Seg]) -> bool:
    """Mẩu song song và thẳng hàng với một tường cụm chính (lệch tim ≤ nửa tổng bề dày): nét gióng kích thước."""
    unit, mid = _unit(seg), ((seg.start.x + seg.end.x) / 2, (seg.start.y + seg.end.y) / 2)
    for other in main:
        ux, uy = _unit(other)
        if abs(unit[0] * uy - unit[1] * ux) > _PARALLEL_SIN:
            continue
        offset = abs((mid[0] - other.start.x) * uy - (mid[1] - other.start.y) * ux)
        if offset <= (seg.thickness_mm + other.thickness_mm) / 2:
            return True
    return False


_FAR_REGION_AXIS_SHARE: Final = 0.6
"""Vùng phụ có `axis_share` dưới số này là phối cảnh 3D / sơ đồ nghiêng, không phải mặt bằng (BENCH3D-20 B3).

Mặt bằng 2D thẳng trục ≥ 0,92 (bench A đến F, kể cả bản xoay); phối cảnh, sơ đồ tháp vẽ nghiêng 0,24 đến 0,57; cánh
tháp xiên của tờ nhiều căn 0,60 đến 0,82. Dưới 0,6: phần lớn chiều dài vùng không theo một cặp hướng vuông góc nào."""


def _region_vectors(segs: Sequence[_Seg], indices: Collection[int]) -> NDArray[np.float64]:
    """Vectơ tim `(dx, dy)` mm của các tường `indices`."""
    return np.asarray([(segs[i].end.x - segs[i].start.x, segs[i].end.y - segs[i].start.y) for i in indices], float)


def _drop_far_clusters(segs: list[_Seg], dropped: Counter[str]) -> list[_Seg]:
    """U57: bỏ cụm tường nhỏ nằm tách xa cụm chính (sơ đồ vị trí căn, logo ở góc brochure) và mẩu lạc sát căn.

    Vùng chính = hộp cụm dài nhất, nới dần bằng mọi cụm có hộp cách vùng ≤ `FAR_CLUSTER_GAP_MM`
    (tường bị khe cửa cắt rời, ban công, cột sát căn). Cụm còn lại bị bỏ khi tổng dài <
    `FAR_CLUSTER_LENGTH_SHARE` tổng tường và hộp bao < `FAR_CLUSTER_AREA_SHARE` hộp vùng chính.
    Cụm nào (kể cả trong vùng) ngắn hơn `_STRAY_CLUSTER_MM`/`_STRAY_CLUSTER_SHARE` mà lòi khỏi hộp cụm dài
    nhất quá bề dày lớn nhất cũng bị bỏ khi cách hộp đó > `_STRAY_SHELL_BAND_MM`, hoặc khi mọi tường của nó nằm
    trên đường gióng của cụm chính; trụ/má tường song song vỏ trong dải 1 m được giữ.
    Vùng phụ (`region_labels`, không bao giờ vùng chứa cụm dài nhất) có `axis_share` < `_FAR_REGION_AXIS_SHARE` bị bỏ cả
    vùng dù lớn: phối cảnh 3D, sơ đồ tháp vẽ nghiêng (BENCH3D-20 B3); căn thứ hai thẳng trục vẫn giữ.
    Đếm vào `isolatedBlock`.
    """
    if len(segs) < 2:
        return segs
    groups = wall_clusters(_linestrings(segs), [seg.measured_mm for seg in segs])
    if len(groups) < 2:
        return segs
    lengths = [sum(_length(segs[i]) for i in group) for group in groups]
    boxes = [_bbox(segs, group) for group in groups]
    total = sum(lengths)
    main = max(range(len(groups)), key=lengths.__getitem__)
    region, far = grow_region(boxes, range(len(groups)), main)
    region_area = max(box_area(region), 1.0)
    reach = max(seg.thickness_mm for seg in segs)
    x0, y0, x1, y1 = boxes[main][0] - reach, boxes[main][1] - reach, boxes[main][2] + reach, boxes[main][3] + reach
    stray = min(_STRAY_CLUSTER_MM, _STRAY_CLUSTER_SHARE * total)
    mx0, my0, mx1, my1 = boxes[main]
    main_segs = [segs[i] for i in groups[main]]
    drop: set[int] = set()
    for index, (bx0, by0, bx1, by1) in enumerate(boxes):
        small = box_area(boxes[index]) < FAR_CLUSTER_AREA_SHARE * region_area
        far_small = index in far and small and lengths[index] < FAR_CLUSTER_LENGTH_SHARE * total
        stray_out = lengths[index] < stray and (bx0 < x0 or by0 < y0 or bx1 > x1 or by1 > y1)
        if stray_out and max(mx0 - bx1, bx0 - mx1, my0 - by1, by0 - my1) <= _STRAY_SHELL_BAND_MM:
            stray_out = all(_on_extension(segs[i], main_segs) for i in groups[index])
        if far_small or stray_out:
            drop.update(groups[index])
    labels = region_labels(boxes, lengths)
    for label in set(labels) - {labels[main]}:
        indices = [i for group, own in zip(groups, labels, strict=True) if own == label for i in group if i not in drop]
        if indices and axis_share(_region_vectors(segs, indices)) < _FAR_REGION_AXIS_SHARE:
            drop.update(indices)
    if drop:
        dropped["isolatedBlock"] += len(drop)
        _log.info("pipeline_build_far_cluster_dropped", extra={"walls": len(drop)})
    return [seg for index, seg in enumerate(segs) if index not in drop]


def _rect(box: BoxPx, scaling: Scaling) -> tuple[float, float, float, float]:
    """Hộp px → `(x_min, y_min, x_max, y_max)` mm (`BoxPx` đã bảo đảm `min < max`)."""
    low = scaling.point(box.x_min, box.y_min)
    high = scaling.point(box.x_max, box.y_max)
    return (float(low.x), float(low.y), float(high.x), float(high.y))


def _opening_boxes(detections: Sequence[DetectionPx], scaling: Scaling) -> _Boxes:
    """Hộp của riêng nhãn `door|double_door|window` đổi sang mm, dựng cây một lần."""
    rects = tuple(_rect(det.box, scaling) for det in detections if det.label in _OPENING_LABELS)
    return _Boxes(rects, STRtree([shapely.box(*rect) for rect in rects]))


def _near_ends(a: _Seg, b: _Seg) -> tuple[Point, Point] | None:
    """Cặp đầu mút gần nhau nhất của hai tường, chỉ khi khe `0 < g ≤ OPENING_GAP_MAX_MM`."""
    pairs = [(p, q) for p in (a.start, a.end) for q in (b.start, b.end)]
    best = min(pairs, key=lambda pair: math.dist((pair[0].x, pair[0].y), (pair[1].x, pair[1].y)))
    gap = math.dist((best[0].x, best[0].y), (best[1].x, best[1].y))
    return best if 0 < gap <= OPENING_GAP_MAX_MM else None


def _diagonal(ua: tuple[float, float], ub: tuple[float, float]) -> bool:
    """Cả hai hướng lệch trục gần nhất > `DIAGONAL_ALIGN_DEG` (bản vẽ xoay chéo, BUG-IT-007)."""
    return min(abs(2 * ua[0] * ua[1]), abs(2 * ub[0] * ub[1])) > _DIAGONAL_MIN_SIN2


def _aligned(a: _Seg, b: _Seg) -> bool:
    """Thẳng hàng: lệch góc ≤ 2° (cặp chéo ≤ `DIAGONAL_ALIGN_DEG`) **và** lệch ngang ≤ ½ bề dày lớn hơn.

    Cặp thẳng trục đo hai đầu của `b` tới tim `a` (B5-05 [6] 3b). Cặp chéo đo khoảng lệch ngang giữa
    hai đầu mút gần nhau **vuông góc với hướng trung bình**: tim nhiễu 5° qua khe 900 đã lệch 78 mm
    nếu đo tới tim `a` kéo dài, còn hai đầu mút của một tường thật vẫn cùng một đường.
    """
    ua, ub = _unit(a), _unit(b)
    diagonal = _diagonal(ua, ub)
    if abs(ua[0] * ub[1] - ua[1] * ub[0]) > (_DIAGONAL_ALIGN_SIN if diagonal else _ALIGN_SIN):
        return False
    limit = max(a.thickness_mm, b.thickness_mm) / 2
    if not diagonal:
        return all(
            abs((point.x - a.start.x) * ua[1] - (point.y - a.start.y) * ua[0]) <= limit for point in (b.start, b.end)
        )
    ends = _near_ends(a, b)
    if ends is None:
        return False
    sign = 1.0 if ua[0] * ub[0] + ua[1] * ub[1] >= 0 else -1.0
    mx, my = ua[0] + sign * ub[0], ua[1] + sign * ub[1]
    dx, dy = ends[1].x - ends[0].x, ends[1].y - ends[0].y
    return abs(dx * my - dy * mx) / math.hypot(mx, my) <= limit


def _perpendicular(a: _Seg, b: _Seg) -> bool:
    """Vuông góc trong sai số 2° (cặp chéo `DIAGONAL_ALIGN_DEG`; dùng cho 3b-b: đầu mút chạm tim vách ngang)."""
    ua, ub = _unit(a), _unit(b)
    limit = _DIAGONAL_PERPENDICULAR_COS if _diagonal(ua, ub) else _PERPENDICULAR_COS
    return abs(ua[0] * ub[0] + ua[1] * ub[1]) <= limit


def _onto(line: _Seg, point: Point) -> Point:
    """Chân vuông góc của `point` trên đường thẳng chứa tim `line` (mm nguyên)."""
    along = _along(line, (point.x, point.y))
    unit = _unit(line)
    return Point(x=js_round(line.start.x + unit[0] * along), y=js_round(line.start.y + unit[1] * along))


def _absorb(keep: _Seg, drop: _Seg) -> None:
    """Gộp `drop` vào `keep`: hai đầu xa nhau nhất, `confidence` nhỏ hơn.

    Hai đầu chiếu lên tim của tường **dài hơn** (UX41-A): lấy thẳng hai đầu xa nhất của hai tường lệch
    ngang vài mm thì tường gộp nghiêng và rời đường trục mà vector hoá đã gióng chung.
    Bề dày: hai tường chồng nhau lấy bề dày lớn hơn (nhánh mảnh trùng tường dày); hai tường cách nhau
    một khe (nối qua cửa) lấy bề dày tường dài hơn — lấy lớn hơn thì vách bếp 2,8 m thành tường 200 chỉ
    vì đầu bên kia khe là mẩu cột 0,5 m.
    """
    pairs = [(p, q) for p in (keep.start, keep.end) for q in (drop.start, drop.end)]
    far = max(pairs, key=lambda pair: math.dist((pair[0].x, pair[0].y), (pair[1].x, pair[1].y)))
    base = keep if _length(keep) >= _length(drop) else drop
    k_low, k_high = sorted(_along(base, (p.x, p.y)) for p in (keep.start, keep.end))
    d_low, d_high = sorted(_along(base, (p.x, p.y)) for p in (drop.start, drop.end))
    apart = k_high < d_low or d_high < k_low
    keep.start, keep.end = _onto(base, far[0]), _onto(base, far[1])
    keep.thickness_mm = base.thickness_mm if apart else max(keep.thickness_mm, drop.thickness_mm)
    keep.measured_mm = base.measured_mm if apart else max(keep.measured_mm, drop.measured_mm)
    keep.confidence = min(keep.confidence, drop.confidence)
    keep.inputs = keep.inputs + drop.inputs
    drop.alive = False


def _mergeable(a: _Seg, b: _Seg, boxes: _Boxes, lines: _Centrelines) -> bool:
    """Cặp thẳng hàng, khe trong trần, khe không cắt tim tường khác, khe có hộp → gộp được."""
    ends = _near_ends(a, b)
    if ends is None or not _aligned(a, b):
        return False
    if lines.crosses(ends[0], ends[1], a.inputs + b.inputs):
        return False
    return boxes.covers(ends[0], ends[1])


def _merge_aligned(
    segs: list[_Seg],
    neighbours: list[set[int]],
    boxes: _Boxes,
    lines: _Centrelines,
    dropped: Counter[str],
) -> None:
    """3b (a): gộp cặp thẳng hàng có khe mang hộp, lặp tới **điểm bất động** bằng hàng đợi ứng viên.

    Tường bị hai khe cửa chia ba đoạn phải về một tường, nên sau mỗi lần gộp tường gộp được đưa
    lại vào hàng đợi cùng mọi láng giềng của cả hai tường cũ.
    """
    queue = deque((low, high) for low, near in enumerate(neighbours) for high in near if low < high)
    while queue:
        low, high = sorted(queue.popleft())
        if low == high or not (segs[low].alive and segs[high].alive):
            continue
        if not _mergeable(segs[low], segs[high], boxes, lines):
            continue
        _absorb(segs[low], segs[high])
        neighbours[low] |= neighbours[high]
        neighbours[low].discard(low)
        queue.extend((low, other) for other in neighbours[low] if segs[other].alive)
        dropped["wallGapBridged"] += 1


def _along(seg: _Seg, point: tuple[float, float]) -> float:
    """Tham số dọc tim `seg` của điểm (mm, 0 tại `start`, `L` tại `end`)."""
    unit = _unit(seg)
    return (point[0] - seg.start.x) * unit[0] + (point[1] - seg.start.y) * unit[1]


def _foot_on_other(seg: _Seg, other: _Seg, reach: float = OPENING_GAP_MAX_MM) -> tuple[bool, Point] | None:
    """Đầu mút của `seg` cần kéo và chân trên tim `other`: `(là đầu end, chân)` hoặc `None`.

    Chỉ xét cặp vuông góc, nên hai đường không song song và định thức không bằng 0. Chân phải nằm
    trong đoạn `other` nới mỗi đầu `max(bề dày other, CORNER_OVERSHOOT_MM)` (tường lệch 0,75° hụt
    vách 29 mm, BUG-IT-001) và **ngoài** `seg`, cách đầu mút `0 < g ≤ reach`, và không
    trùng đầu mút còn lại (`Segment` cấm tường dài 0).
    """
    if not _perpendicular(seg, other):
        return None
    unit, other_unit = _unit(seg), _unit(other)
    dx, dy = other.start.x - seg.start.x, other.start.y - seg.start.y
    step = (dx * other_unit[1] - dy * other_unit[0]) / (unit[0] * other_unit[1] - unit[1] * other_unit[0])
    crossing = (seg.start.x + unit[0] * step, seg.start.y + unit[1] * step)
    slack = max(other.thickness_mm, CORNER_OVERSHOOT_MM)
    if not -slack <= _along(other, crossing) <= _length(other) + slack:
        return None
    foot = Point(x=js_round(crossing[0]), y=js_round(crossing[1]))
    if -reach <= step < 0 and foot != seg.end:
        return (False, foot)
    if _length(seg) < step <= _length(seg) + reach and foot != seg.start:
        return (True, foot)
    return None


def _clash(a: _Seg, b: _Seg) -> bool:
    """Cặp tường vi phạm WALL-OVERLAP của FE: chồng song song hoặc cắt xuyên thân (xem `_PARALLEL_SIN`)."""
    la, lb = _line(a), _line(b)
    ua, ub = _unit(a), _unit(b)
    if abs(ua[0] * ub[1] - ua[1] * ub[0]) <= _PARALLEL_SIN:
        if la.distance(lb) > (a.thickness_mm + b.thickness_mm) / 2:
            return False
        low, high = sorted(_along(a, (p.x, p.y)) for p in (b.start, b.end))
        return min(_length(a), high) - max(0.0, low) > _OVERLAP_MIN_MM
    at = la.intersection(lb)
    if not isinstance(at, shapely.Point):
        return False
    return all(math.dist((at.x, at.y), (p.x, p.y)) > _JOINT_MM for p in (a.start, a.end, b.start, b.end))


def _clashes(seg: _Seg, segs: Sequence[_Seg]) -> int:
    """Số tường sống khác mà `seg` chồng/cắt xuyên theo `_clash`."""
    return sum(other is not seg and other.alive and _clash(seg, other) for other in segs)


def _try_extend(seg: _Seg, other: _Seg, is_end: bool, foot: Point, segs: Sequence[_Seg]) -> bool:
    """Kéo đầu mút của `seg` tới `foot` (và `_stretch_to` vách `other`); tăng số cặp chồng/cắt → hoàn lại."""
    saved = (seg.start, seg.end, other.start, other.end)
    before = _clashes(seg, segs) + _clashes(other, segs)
    if is_end:
        seg.end = foot
    else:
        seg.start = foot
    _stretch_to(other, foot)
    if _clashes(seg, segs) + _clashes(other, segs) > before:
        seg.start, seg.end, other.start, other.end = saved
        return False
    return True


def _extend_once(seg: _Seg, others: Sequence[_Seg], boxes: _Boxes, segs: Sequence[_Seg]) -> bool:
    """3b (b) cho một tường: kéo đầu mút tới tim vách vuông góc đầu tiên mà khe có hộp."""
    for other in others:
        hit = _foot_on_other(seg, other)
        if hit is None:
            continue
        is_end, foot = hit
        if not boxes.covers(seg.end if is_end else seg.start, foot):
            continue
        if _try_extend(seg, other, is_end, foot, segs):
            return True
    return False


def _stretch_to(seg: _Seg, foot: Point) -> None:
    """Chân lố ra ngoài đoạn `seg` (trong dung sai của `_foot_on_other`) → kéo đầu gần chân tới chân.

    Góc khép kín thay vì hở vài chục mm; chân ở ngoài `[0, L]` nên cách đầu xa > 0, không thành dài 0.
    """
    along = _along(seg, (foot.x, foot.y))
    if along < 0:
        seg.start = foot
    elif along > _length(seg):
        seg.end = foot


def _extend_all(segs: list[_Seg], neighbours: list[set[int]], boxes: _Boxes, dropped: Counter[str]) -> None:
    """3b (b) cho mọi tường còn giữ; mỗi tường kéo nhiều nhất một đầu mút một lần."""
    for index, seg in enumerate(segs):
        if not seg.alive:
            continue
        others = [segs[other] for other in sorted(neighbours[index]) if segs[other].alive]
        if others and _extend_once(seg, others, boxes, segs):
            dropped["wallGapBridged"] += 1


def _line(seg: _Seg) -> LineString:
    """Tim `seg` thành `LineString` (mm)."""
    return LineString(((seg.start.x, seg.start.y), (seg.end.x, seg.end.y)))


def _dangling(seg: _Seg, tip: Point, segs: Sequence[_Seg]) -> bool:
    """Đầu mút `tip` không chạm tim tường sống nào khác (cách > ½ tổng bề dày + `_TOUCH_SLACK_MM`)."""
    point = shapely.points(tip.x, tip.y)
    return not any(
        other is not seg
        and other.alive
        and shapely.distance(point, _line(other)) <= (seg.thickness_mm + other.thickness_mm) / 2 + _TOUCH_SLACK_MM
        for other in segs
    )


def _blocked(seg: _Seg, other: _Seg, tip: Point, foot: Point, segs: Sequence[_Seg]) -> bool:
    """Khe từ `tip` tới mặt `other` cắt tim một tường sống thứ ba (không kéo xuyên tường)."""
    span = math.dist((tip.x, tip.y), (foot.x, foot.y))
    back = other.thickness_mm / 2
    if span <= back + 2:
        return False
    ux, uy = (foot.x - tip.x) / span, (foot.y - tip.y) / span
    gap = LineString(((tip.x + ux, tip.y + uy), (foot.x - ux * back, foot.y - uy * back)))
    return any(item.alive and item is not seg and item is not other and gap.intersects(_line(item)) for item in segs)


def _centre_in_gap(boxes: _Boxes, seg: _Seg, tip: Point, face: float) -> bool:
    """Đúng **một** tâm hộp ô mở trong khe: dọc `(0, face]` từ `tip`, ngang ≤ ½ bề dày + `OPENING_WALL_REACH_MM`.

    Hai hộp trở lên là không chắc (cửa của vách bên cạnh lọt vào dải, ch-09 lô gia) → giữ khe.
    """
    ux, uy = _unit(seg)
    if math.dist((tip.x, tip.y), (seg.start.x, seg.start.y)) < 1:
        ux, uy = -ux, -uy
    lateral = seg.thickness_mm / 2 + OPENING_WALL_REACH_MM
    inside = 0
    for x0, y0, x1, y1 in boxes.rects:
        dx, dy = (x0 + x1) / 2 - tip.x, (y0 + y1) / 2 - tip.y
        inside += 0 < dx * ux + dy * uy <= face and abs(dx * uy - dy * ux) <= lateral
    return inside == 1


def _reach_dangling(segs: list[_Seg], neighbours: Sequence[set[int]], boxes: _Boxes, dropped: Counter[str]) -> None:
    """3b (d): đầu lơ lửng còn lại → kéo theo phương tường tới tim vách vuông góc gần nhất phía trước.

    Khe mặt-tới-mặt ≤ max(`_DANGLE_REACH_MM`, `_DANGLE_REACH_THICKNESS` x bề dày) không cần hộp cửa; khe có
    tâm hộp ô mở (`_centre_in_gap`) tới `_DANGLE_DOOR_REACH_MM`: khe cửa trong bản vẽ là tường có ô mở, và
    bước 4 gắn hộp đó vào chính tường đã kéo. Vách phía trước bị tường khác chắn (`_blocked`) → giữ khe.
    """
    for index, seg in enumerate(segs):
        if not seg.alive:
            continue
        reach = max(_DANGLE_REACH_MM, _DANGLE_REACH_THICKNESS * seg.thickness_mm)
        best: dict[bool, tuple[float, Point, _Seg]] = {}
        for other in (segs[item] for item in sorted(neighbours[index])):
            hit = _foot_on_other(seg, other, max(reach, _DANGLE_DOOR_REACH_MM) + other.thickness_mm / 2)
            hit = hit if other.alive else None
            if hit is None:
                continue
            is_end, foot = hit
            tip = seg.end if is_end else seg.start
            gap = math.dist((tip.x, tip.y), (foot.x, foot.y))
            if is_end not in best or gap < best[is_end][0]:
                best[is_end] = (gap, foot, other)
        for is_end, (gap, foot, other) in best.items():
            tip = seg.end if is_end else seg.start
            face = gap - other.thickness_mm / 2
            if face > reach and not _centre_in_gap(boxes, seg, tip, face):
                continue
            if not _dangling(seg, tip, segs) or _blocked(seg, other, tip, foot, segs):
                continue
            if _try_extend(seg, other, is_end, foot, segs):
                dropped["wallGapBridged"] += 1


def _hugs(stub: _Seg, wall: _Seg) -> bool:
    """`stub` song song (≤ 10°) với `wall` dày hơn, cả hai đầu chiếu vào thân `wall`, mặt tới mặt ≤ 150 mm."""
    if wall is stub or not wall.alive or wall.thickness_mm <= stub.thickness_mm:
        return False
    (ax, ay), (bx, by) = _unit(stub), _unit(wall)
    if abs(ax * bx + ay * by) < _HUG_COS_MIN:
        return False
    span = _length(wall)
    for tip in (stub.start, stub.end):
        along = (tip.x - wall.start.x) * bx + (tip.y - wall.start.y) * by
        side = abs((tip.x - wall.start.x) * by - (tip.y - wall.start.y) * bx)
        if not 0 <= along <= span or side - (stub.thickness_mm + wall.thickness_mm) / 2 > _HUG_FACE_MAX_MM:
            return False
    return True


def _drop_hugging_stubs(segs: list[_Seg], neighbours: Sequence[set[int]], boxes: _Boxes, dropped: Counter[str]) -> None:
    """3b (e): bỏ mẩu ≤ 600 mm áp sát mặt tường dày hơn khi có đầu không chạm tường thứ ba và không mang ô mở.

    Mẩu như vậy không khép phòng nào (một đầu tự do) mà để lại đầu lơ lửng cách mặt tường 70-150 mm
    (WALL-DANGLING-END ở màn Quy chuẩn). Kéo theo trục không chạm được vì nó song song với tường kia.
    Tường chủ và tường chạm đầu mẩu đều cách mẩu < `OPENING_GAP_MAX_MM` nên chỉ dò trong `neighbours`.
    """
    for index, stub in enumerate(segs):
        if not stub.alive or _length(stub) > _HUG_STUB_MAX_MM or _hits(boxes.tree, _line(stub)):
            continue
        near = [segs[item] for item in sorted(neighbours[index])]
        host = next((wall for wall in near if _hugs(stub, wall)), None)
        if host is None:
            continue
        others = [seg for seg in near if seg is not host]
        if _dangling(stub, stub.start, others) or _dangling(stub, stub.end, others):
            stub.alive = False
            dropped["isolatedBlock"] += 1


def _buried(nib: _Seg, segs: Sequence[_Seg]) -> bool:
    """Cả hai đầu `nib` nằm trong thân **cùng** một tường sống khác (cách tim ≤ ½ dày nó + `_TOUCH_SLACK_MM`).

    Mẩu như vậy không nối hai tường nào, chỉ là phần thò của một nét đã có (W4); má cửa nối hai tường không bị bắt.
    """
    tips = shapely.points([(nib.start.x, nib.start.y), (nib.end.x, nib.end.y)])
    return any(
        other is not nib
        and other.alive
        and bool((shapely.distance(tips, _line(other)) <= other.thickness_mm / 2 + _TOUCH_SLACK_MM).all())
        for other in segs
    )


def _drop_dangling_nibs(segs: list[_Seg], neighbours: Sequence[set[int]], boxes: _Boxes, dropped: Counter[str]) -> None:
    """3b (f): mẩu < `_NIB_MAX_MM` có đầu lơ lửng hay nằm trọn trong thân tường khác (`_buried`) sau khi đã kéo
    (b, d), thân không chạm hộp ô mở → bỏ (C2).

    Thân nới thêm một bề dày khi dò hộp: mẩu cạnh khe cửa (má cửa) chạm mép hộp vẫn được giữ.
    """
    for index, nib in enumerate(segs):
        if not nib.alive or _length(nib) >= _NIB_MAX_MM or _hits(boxes.tree, _line(nib).buffer(nib.thickness_mm)):
            continue
        near = [segs[item] for item in sorted(neighbours[index])]
        if _dangling(nib, nib.start, near) or _dangling(nib, nib.end, near) or _buried(nib, near):
            nib.alive = False
            dropped["isolatedBlock"] += 1


def _bridge_segments(segs: Sequence[_Seg], neighbours: Sequence[set[int]]) -> tuple[tuple[Point, Point], ...]:
    """3b (c): cặp thẳng hàng còn khe mà không gộp → đoạn nối hai đầu gần, chỉ để khép phòng."""
    pairs = [
        (low, high)
        for low, near in enumerate(neighbours)
        for high in sorted(near)
        if low < high and segs[low].alive and segs[high].alive
    ]
    found = (_near_ends(segs[low], segs[high]) for low, high in pairs if _aligned(segs[low], segs[high]))
    return tuple(ends for ends in found if ends is not None)


def _region_labels(segs: Sequence[_Seg], geoms: Geoms) -> list[int]:
    """Số vùng (`region_labels`) của từng tường, tính trên tường **trước** nối khe."""
    groups = wall_clusters(geoms, [seg.measured_mm for seg in segs])
    lengths = [sum(_length(segs[i]) for i in group) for group in groups]
    labels = [0] * len(segs)
    for group, label in zip(groups, region_labels([_bbox(segs, group) for group in groups], lengths), strict=True):
        for i in group:
            labels[i] = label
    return labels


def _join_gaps(segs: list[_Seg], boxes: _Boxes, dropped: Counter[str]) -> tuple[tuple[Point, Point], ...]:
    """Bước 3b: gộp cặp thẳng hàng (a), kéo đầu mút tới vách (b), đầu lơ lửng (d), đoạn nối cặp còn lại (c).

    Chỉ xét cặp cùng vùng (`_region_labels`): hai căn cách nhau khe giấy trắng > `FAR_CLUSTER_GAP_MM` không bị nối
    qua khe như qua ô cửa (≤ `OPENING_GAP_MAX_MM`), nên `MULTIPLE_PLANS` còn thấy hai vùng (BENCH3D-20 B4). Khe
    ≤ `FAR_CLUSTER_GAP_MM` luôn cùng vùng (hộp cách ≤ khe), nên chỉ cặp cách > 1,5 m và lòi khỏi hộp vùng bị ảnh hưởng.
    """
    geoms = _linestrings(segs)
    tree = STRtree(geoms)
    lines = _Centrelines(tree, {seg.inputs[0]: slot for slot, seg in enumerate(segs)})
    labels = _region_labels(segs, geoms)
    near = neighbours(tree, geoms, float(OPENING_GAP_MAX_MM))
    near = [{j for j in found if labels[j] == labels[i]} for i, found in enumerate(near)]
    _merge_aligned(segs, near, boxes, lines, dropped)
    _extend_all(segs, near, boxes, dropped)
    _reach_dangling(segs, near, boxes, dropped)
    _drop_hugging_stubs(segs, near, boxes, dropped)
    _drop_dangling_nibs(segs, near, boxes, dropped)
    return _bridge_segments(segs, near)


def _attach_walls(
    segs: Sequence[_Seg], geoms: Geoms, rects: Sequence[tuple[float, float, float, float]]
) -> list[list[int]]:
    """Tường ứng viên của mỗi hộp ô mở, gần tâm trước; rỗng khi không tường nào với tới (bước 4).

    Ứng viên: tường mà **hộp** (không phải tâm) cách tim ≤ max(`thicknessMm / 2 + OPENING_WALL_REACH_MM`,
    `_OPENING_REACH_SHARE` x cạnh dài hộp) — cửa sổ rộng vẽ lệch khỏi tim xa hơn 150 mm vẫn gắn,
    0 khi tim cắt hộp — hộp thẳng trục của cửa xoay chéo là hộp vuông lớn, tâm xa tim (BUG-IT-003).
    Xếp theo khoảng cách tới **tâm** hộp, hoà lấy chỉ số nhỏ: cửa sát góc chạm cả vách vuông góc vẫn về
    tường nó nằm trên. `_opening_candidates` chọn trong danh sách. Một truy vấn `dwithin` theo lô.
    """
    ranked: list[list[int]] = [[] for _ in rects]
    if not rects or not len(geoms):
        return ranked
    boxes = np.asarray(shapely.box(*np.asarray(rects).T), dtype=object)
    reach = np.asarray([seg.thickness_mm / 2 + OPENING_WALL_REACH_MM for seg in segs])
    box_reach = np.asarray([_OPENING_REACH_SHARE * max(r[2] - r[0], r[3] - r[1]) for r in rects])
    far = max(float(reach.max()), float(box_reach.max()))
    sources, walls = STRtree(geoms).query(boxes, predicate="dwithin", distance=far)
    inside = shapely.distance(boxes[sources], geoms[walls]) <= np.maximum(reach[walls], box_reach[sources])
    centre = shapely.distance(shapely.centroid(boxes[sources]), geoms[walls])
    hits = zip(centre[inside].tolist(), walls[inside].tolist(), sources[inside].tolist(), strict=True)
    for _, wall, source in sorted(hits):
        ranked[source].append(wall)
    return ranked


def _runs_along(rect: tuple[float, float, float, float], span: tuple[float, float], length: float) -> bool:
    """Hộp nằm dọc tường đủ dài: tường ≥ `OPENING_MIN_WIDTH_MM` và hình chiếu ≥ ½ cạnh dài của hộp.

    Mẩu tường má cửa (ngắn, thường vuông góc với hộp) thường gần tâm hộp hơn tường thật mà cửa nằm trên;
    gắn vào mẩu đó thì ô mở rơi `openingTooWide` hoặc lấy rộng mặc định chồng lên ô mở bên cạnh (OPEN-ATTACH).
    """
    long_side = max(rect[2] - rect[0], rect[3] - rect[1])
    return length >= OPENING_MIN_WIDTH_MM and span[1] - span[0] >= long_side / 2


def _gap_walls(segs: Sequence[_Seg], detections: Sequence[DetectionPx], scaling: Scaling) -> list[_Seg]:
    """Bước 3c: tường qua phần khe của hộp ô mở dài mà không tường cùng hướng nào phủ (OPEN-ATTACH).

    Mẩu má cửa vector hoá thành đoạn xiên ngắn, hay tường dừng ngay trước khe, thì 3b không nối được khe: cửa chỉ
    còn tường vuông góc hoặc một đầu tường thẳng hàng để gắn → rơi `openingTooWide` hoặc lấy rộng mặc định chồng
    lên ô mở bên cạnh. Có tường nằm dọc hộp (`_runs_along`, kể cả tường chéo xuyên hộp) thì không dựng. Dọc cạnh dài
    của hộp, bỏ khoảng mà tường với tới cùng hướng (lệch ≤ 20°) đã phủ; khoảng trống
    dài nhất ≥ `OPENING_MIN_WIDTH_MM` thành tim mới qua tâm hộp, bề dày và tin cậy của tường gần tâm nhất. Hộp cắt
    hộp đã dựng tường thì bỏ (cửa trùng); hộp gần vuông (cạnh dài < 2 lần cạnh ngắn) không rõ hướng nên không dựng.
    """
    if not segs:
        return []
    geoms = _linestrings(segs)
    rects = [_rect(det.box, scaling) for det in detections if det.label in _OPENING_LABELS]
    added: list[_Seg] = []
    used: list[tuple[float, float, float, float]] = []
    for rect, walls in zip(rects, _attach_walls(segs, geoms, rects), strict=True):
        width, height = rect[2] - rect[0], rect[3] - rect[1]
        if not walls or max(width, height) < max(OPENING_MIN_WIDTH_MM, 2 * min(width, height)):
            continue
        if any(rect[0] < x1 and x0 < rect[2] and rect[1] < y1 and y0 < rect[3] for x0, y0, x1, y1 in used):
            continue
        if any(_runs_along(rect, *_placement(segs, geoms, {}, rect, wall)[2:]) for wall in walls):
            continue
        axis = 0 if width >= height else 1
        covered = [
            sorted((segs[wall].start.x, segs[wall].end.x) if axis == 0 else (segs[wall].start.y, segs[wall].end.y))
            for wall in walls
            if abs(_unit(segs[wall])[axis]) >= _GAP_PARALLEL_COS
        ]
        low, high = _longest_free(rect[axis], rect[axis + 2], covered)
        if high - low < OPENING_MIN_WIDTH_MM:
            continue
        mid = js_round((rect[1 - axis] + rect[3 - axis]) / 2)
        ends = (js_round(low), js_round(high))
        start, end = (
            (Point(x=ends[0], y=mid), Point(x=ends[1], y=mid))
            if axis == 0
            else (Point(x=mid, y=ends[0]), Point(x=mid, y=ends[1]))
        )
        near = segs[walls[0]]
        added.append(_Seg(start, end, near.thickness_mm, float(near.thickness_mm), near.confidence, ()))
        used.append(rect)
    return added


def _longest_free(low: float, high: float, covered: Sequence[Sequence[float]]) -> tuple[float, float]:
    """Khoảng con dài nhất của `[low, high]` không nằm trong khoảng `covered` nào (hoà: khoảng trước)."""
    best, cursor = (low, low), low
    for start, end in sorted((max(a, low), min(b, high)) for a, b in covered if b > low and a < high):
        if start - cursor > best[1] - best[0]:
            best = (cursor, start)
        cursor = max(cursor, end)
    return (cursor, high) if high - cursor > best[1] - best[0] else best


def _arc_chains(segs: Sequence[_Seg], arc_of: Sequence[int | None]) -> dict[int, tuple[int, ...]]:
    """Vị trí dây → mọi vị trí dây của cung chứa nó, theo thứ tự; chỉ cung `arcs.join_arcs` sẽ gộp.

    Cùng điều kiện gộp: mỗi dây còn là tường riêng một đầu vào, đủ mọi dây, số dây chẵn, nối đuôi nhau.
    """
    members: dict[int, list[tuple[int, int]]] = {}
    for slot, seg in enumerate(segs):
        if len(seg.inputs) == 1 and (arc := arc_of[seg.inputs[0]] if arc_of else None) is not None:
            members.setdefault(arc, []).append((seg.inputs[0], slot))
    total = Counter(arc for arc in arc_of if arc is not None)
    chains: dict[int, tuple[int, ...]] = {}
    for arc, pairs in members.items():
        order = tuple(slot for _, slot in sorted(pairs))
        linked = all(segs[one].end == segs[two].start for one, two in itertools.pairwise(order))
        if len(order) == total[arc] and not len(order) % 2 and linked:
            chains.update(dict.fromkeys(order, order))
    return chains


def _span_on_wall(rect: tuple[float, float, float, float], line: LineString) -> tuple[float, float]:
    """Chiếu 4 góc hộp lên tim tường → `[a, b]` đã kẹp về `[0, L]` (tham số dọc tim, mm)."""
    values = [float(shapely.line_locate_point(line, shapely.points(x, y))) for x in rect[0::2] for y in rect[1::2]]
    return (min(values), max(values))


def _candidate(
    order: int,
    detection: DetectionPx,
    wall: int,
    seg: _Seg,
    span: tuple[float, float],
    dropped: Counter[str],
    length: float | None = None,
) -> _Cand | None:
    """Bước 4 cho một hộp đã biết tường: rộng = hình chiếu hộp lên tim, `offsetMm` kẹp trong tường.

    Rộng lấy từ hộp, kẹp ≤ `L` (BUG-IT-004: rộng cố định làm rơi ô mở trên tường ngắn và chồng cửa
    sổ kề nhau); hình chiếu < `OPENING_MIN_WIDTH_MM` là suy biến → rộng mặc định của `LABEL_TARGETS`
    kẹp về `L` khi `L ≥ OPENING_MIN_WIDTH_MM`, tường ngắn hơn → `openingTooWide` (BUG-IT-009).
    Cửa đôi hẹp hơn 600 mm là cửa đơn bị dò nhầm: giữ thành cửa đơn (mặc định `door`) thay vì bỏ.
    `length` thay chiều dài `seg` khi ô mở
    đo trên cả cung (`span` tính từ đầu cung, `wall` là dây đầu).
    """
    target = LABEL_TARGETS[detection.label]
    low, high = span
    length = _length(seg) if length is None else length
    # Hộp thẳng trục của ô mở trên tường lệch góc θ chiếu dài thêm bề dày · |sin 2θ| (review P2 LOG-02).
    ux, uy = _unit(seg)
    measured = min(js_round(high - low - seg.thickness_mm * abs(2 * ux * uy)), math.floor(length))
    if target.width_mm is None and measured < _MIN_DOUBLE_DOOR_MM:
        target = LABEL_TARGETS["door"]
    if target.height_mm is None or target.sill_mm is None or target.swing is None:
        return None
    width = target.width_mm if target.width_mm is not None and measured < OPENING_MIN_WIDTH_MM else measured
    if length < width:
        # BUG-IT-009: tường đủ chỗ cho một ô mở thật (≥ OPENING_MIN_WIDTH_MM) → kẹp rộng mặc định về chiều dài.
        if length < OPENING_MIN_WIDTH_MM:
            dropped["openingTooWide"] += 1
            return None
        width = math.floor(length)
    kind: Literal["door", "window"] = "window" if detection.label == "window" else "door"
    # Trần `floor`, không `js_round`: rộng kẹp `floor(L)` + làm tròn lên lố đầu tường 1 mm (review 2 P2 LOG-02).
    offset = min(max(js_round((low + high) / 2 - width / 2), 0), math.floor(length - width))
    return _Cand(wall, order, kind, offset, width, target.height_mm, target.sill_mm, target.swing, detection.confidence)


def _read_swing(cand: _Cand, seg: _Seg, page: PageRaster | None, scaling: Scaling) -> _Cand:
    """Cửa một cánh trên tường thẳng: bản lề + phía cánh từ nét vẽ (`swing.read_leaf`); không đọc được → giữ nguyên.

    C3: không đọc được cung (hay không có trang) mà rộng ≥ `_SLIDING_MIN_MM` → `"sliding"`; đọc được mà rộng hơn
    `_MAX_SINGLE_LEAF_MM` → `"double"` (vẫn giữ phía cánh cho `settle_reversed`).

    Khe `a → b` = `[offset, offset + rộng]` dọc tim tính từ `seg.start`, đổi về px trang. Bản lề ở `a` (phía đầu
    tường) → `"left"`, ở `b` → `"right"` (Pascal `hingesSide`, `swing.py`); hướng cánh lưu tuyệt đối để
    `settle_reversed` so với luật phía mở của FE.
    """
    if cand.kind != "door" or cand.swing != "left":
        return cand
    ux, uy = _unit(seg)
    scale = scaling.build

    def at(t: float) -> tuple[float, float]:
        return ((seg.start.x + ux * t) / scale, (seg.start.y + uy * t) / scale)

    leaf = None
    if page is not None:
        leaf = read_leaf(page, at(cand.offset_mm), at(cand.offset_mm + cand.width_mm), seg.measured_mm / 2 / scale)
    if leaf is None:
        # C3: không cung mở mà rộng ≥ 1,5 m → cửa lùa (trước đây giữ "left" theo nhãn).
        return replace(cand, swing="sliding") if cand.width_mm >= _SLIDING_MIN_MM else cand
    swing: SwingDirection = "right" if leaf.hinge_at_b else "left"
    if cand.width_mm > _MAX_SINGLE_LEAF_MM:
        swing = "double"
    return replace(cand, swing=swing, leaf=(-uy * leaf.side, ux * leaf.side))


def _opening_candidates(
    segs: Sequence[_Seg],
    geoms: Geoms,
    detections: Sequence[DetectionPx],
    scaling: Scaling,
    dropped: Counter[str],
    chains: Mapping[int, tuple[int, ...]],
    page: PageRaster | None = None,
) -> list[_Cand]:
    """Bước 4: mỗi hộp ô mở → tường đầu tiên trong ứng viên của `_attach_walls` mà hộp nằm dọc (`_runs_along`),
    không có thì tường gần tâm nhất; không tường nào với tới → `openingUnattached`.

    Cung: xem `_placement` — cửa ban công cong rộng hơn một dây 10° không bị kẹp về dây hay rơi `openingTooWide`.
    `page` (trang + mặt nạ tường) cho cửa một cánh đọc bản lề/phía cánh từ nét vẽ (`_read_swing`).
    """
    chosen = [(order, det) for order, det in enumerate(detections) if det.label in _OPENING_LABELS]
    rects = [_rect(det.box, scaling) for _, det in chosen]
    cands: list[_Cand] = []
    for rect, (order, detection), walls in zip(rects, chosen, _attach_walls(segs, geoms, rects), strict=True):
        if not walls:
            dropped["openingUnattached"] += 1
            continue
        places = [_placement(segs, geoms, chains, rect, wall) for wall in walls]
        # Tường đầu tiên hộp nằm dọc; không có thì tường gần tâm nhất như trước (OPEN-ATTACH).
        host, wall, span, length = next((place for place in places if _runs_along(rect, place[2], place[3])), places[0])
        cand = _candidate(order, detection, host, segs[wall], span, dropped, length)
        if cand is not None:
            # Cung (ô mở đo trên cả cung): chưa đọc nét, giữ chiều mở theo nhãn.
            cands.append(cand if host != wall or wall in chains else _read_swing(cand, segs[wall], page, scaling))
    return cands


def _placement(
    segs: Sequence[_Seg],
    geoms: Geoms,
    chains: Mapping[int, tuple[int, ...]],
    rect: tuple[float, float, float, float],
    wall: int,
) -> tuple[int, int, tuple[float, float], float]:
    """`(tường chủ, tường gắn, hình chiếu, chiều dài)` của hộp trên `wall`; dây của cung → chiếu lên cả cung.

    Hộp gắn vào dây của một cung (`chains`) chiếu lên cả cung, ô mở đặt trên dây đầu với `offsetMm` tính từ đầu
    cung (WALLS-ARC NO-432).
    """
    chain = chains.get(wall)
    if chain is None:
        return wall, wall, _span_on_wall(rect, geoms[wall]), _length(segs[wall])
    line = shapely.linestrings(
        [[segs[chain[0]].start.x, segs[chain[0]].start.y]] + [[segs[k].end.x, segs[k].end.y] for k in chain]
    )
    return chain[0], wall, _span_on_wall(rect, line), float(line.length)


def _overlaps(a: _Cand, b: _Cand) -> bool:
    """Hai ô mở cùng tường có khoảng `[offset, offset + rộng]` chồng nhau."""
    return a.wall == b.wall and a.offset_mm < b.offset_mm + b.width_mm and b.offset_mm < a.offset_mm + a.width_mm


def _resolve_overlaps(cands: Sequence[_Cand], dropped: Counter[str]) -> list[_Cand]:
    """Chồng khoảng cùng tường → giữ `confidence` cao hơn (hoà: đứng trước), còn lại `openingOverlap`; rồi gộp mảnh."""
    kept: list[_Cand] = []
    for cand in cands:
        clashes = [other for other in kept if _overlaps(other, cand)]
        if any(other.confidence >= cand.confidence for other in clashes):
            dropped["openingOverlap"] += 1
            continue
        losers = {other.order for other in clashes}
        kept = [other for other in kept if other.order not in losers]
        dropped["openingOverlap"] += len(losers)
        kept.append(cand)
    return _merge_split(kept, dropped)


def _merge_split(cands: Sequence[_Cand], dropped: Counter[str]) -> list[_Cand]:
    """C4: ô mở cùng loại cùng tường cách ≤ `_SPLIT_GAP_MM` → một khoảng, lấy thuộc tính của mảnh `confidence` cao hơn.

    Mảnh bị nhập đếm `openingOverlap`. Cửa bản lề gộp lại rộng hơn `_MAX_SINGLE_LEAF_MM` thành hai cánh.
    """
    merged: list[_Cand] = []
    for cand in sorted(cands, key=lambda item: (item.wall, item.kind, item.offset_mm)):
        prev = merged[-1] if merged else None
        if prev is None or (prev.wall, prev.kind) != (cand.wall, cand.kind):
            merged.append(cand)
            continue
        end = max(prev.offset_mm + prev.width_mm, cand.offset_mm + cand.width_mm)
        if cand.offset_mm - (prev.offset_mm + prev.width_mm) > _SPLIT_GAP_MM:
            merged.append(cand)
            continue
        lead = prev if prev.confidence >= cand.confidence else cand
        width = end - prev.offset_mm
        hinged = lead.swing in ("left", "right")
        swing: SwingDirection = "double" if hinged and width > _MAX_SINGLE_LEAF_MM else lead.swing
        order = min(prev.order, cand.order)
        merged[-1] = replace(lead, order=order, offset_mm=prev.offset_mm, width_mm=width, swing=swing)
        dropped["openingOverlap"] += 1
    return sorted(merged, key=lambda item: item.order)


def _assemble(
    segs: Sequence[_Seg], cands: Sequence[_Cand], level_id: str, clock: Clock
) -> tuple[tuple[Wall, ...], tuple[Opening, ...]]:
    """Dựng `Wall` (id mới, `openingIds` theo thứ tự `detections`) và `Opening` tương ứng."""
    wall_ids = [new_spatial_id("wall", clock) for _ in segs]
    openings = tuple(
        Opening(
            id=new_spatial_id("opening", clock),
            wall_id=wall_ids[cand.wall],
            kind=cand.kind,
            offset_mm=cand.offset_mm,
            width_mm=cand.width_mm,
            height_mm=cand.height_mm,
            sill_height_mm=cand.sill_mm,
            swing=cand.swing,
            confidence=cand.confidence,
            source="ai",
            reviewed=False,
        )
        for cand in cands
    )
    per_wall: list[list[str]] = [[] for _ in segs]
    for opening, cand in zip(openings, cands, strict=True):
        per_wall[cand.wall].append(opening.id)
    walls = tuple(
        Wall(
            id=wall_ids[index],
            level_id=level_id,
            centreline=Segment(start=seg.start, end=seg.end),
            thickness_mm=seg.thickness_mm,
            detected_thickness_mm=max(1, js_round(seg.measured_mm)),
            height_mm=DEFAULT_WALL_HEIGHT_MM,
            kind="partition",
            opening_ids=tuple(per_wall[index]),
            confidence=seg.confidence,
            source="ai",
            reviewed=False,
        )
        for index, seg in enumerate(segs)
    )
    return walls, openings


def build_walls(
    walls: Sequence[WallPx],
    detections: Sequence[DetectionPx],
    *,
    level_id: str,
    scaling: Scaling,
    clock: Clock,
    dropped: Counter[str],
    arc_of: Sequence[int | None] = (),
    page: PageRaster | None = None,
) -> WallSet:
    """Bước 3 + 3b + 3c + 4: tường mm, nối khe ô mở, tường qua khe cửa, ô mở; không sửa đối số vào.

    Chỉ nhãn `door|double_door|window` thành ô mở, nhãn khác bỏ qua **không** đếm (đồ đạc là
    việc của `rooms.build_furniture`). `dropped` cộng dồn tại chỗ theo khoá `DROPPED_KEYS`. `arc_of` (từ
    `arcs.explode_arcs`) cho biết đầu vào nào là dây của cùng một cung để ô mở đo trên cả cung. `page` bật đọc
    chiều mở cửa từ nét vẽ (QT-SWING, `swing.py`).
    """
    segs = _drop_hairlines(_measure_walls(walls, scaling, dropped), dropped)
    segs = _drop_inside_body(_drop_far_clusters(_drop_isolated(segs, dropped), dropped), dropped)
    boxes = _opening_boxes(detections, scaling)
    bridges = _join_gaps(segs, boxes, dropped) if segs else ()
    live, symbol_doors = _drop_door_symbols([seg for seg in segs if seg.alive], detections, scaling, page)
    live += _gap_walls(live, detections, scaling)
    geoms = _linestrings(live) if live else np.asarray([], dtype=object)
    chains = _arc_chains(live, arc_of)
    cands = _resolve_overlaps(_opening_candidates(live, geoms, detections, scaling, dropped, chains, page), dropped)
    live, cands = _drop_bare_gap_walls(live, cands)
    bridges = (*bridges, *_symbol_closers(live, cands, symbol_doors))
    input_to_wall = {index: position for position, seg in enumerate(live) for index in seg.inputs}
    built_walls, openings = _assemble(live, cands, level_id, clock)
    leaves = {opening.id: cand.leaf for opening, cand in zip(openings, cands, strict=True) if cand.leaf is not None}
    return WallSet(built_walls, openings, input_to_wall, bridges, MappingProxyType(leaves))


def _centreline(wall: Wall) -> LineString:
    """Tim `Wall` thành `LineString` (mm)."""
    line = wall.centreline
    return LineString(((line.start.x, line.start.y), (line.end.x, line.end.y)))


def _wall_seg(wall: Wall) -> _Seg:
    """`Wall` thành `_Seg` để dùng lại `_clash` (luật WALL-OVERLAP của FE)."""
    line = wall.centreline
    return _Seg(line.start, line.end, wall.thickness_mm, wall.thickness_mm, wall.confidence, ())


def _bridge_hosts(walls: Sequence[Wall], ends: tuple[Point, Point]) -> list[Wall]:
    """Tường thẳng hàng với đoạn nối có đầu mút trùng một đầu của nó (đoạn nối nối hai đầu gần của cặp thẳng hàng).

    Vách vuông góc chạm mép khe (chữ T/L) không phải tường chủ: kéo nó qua khe sẽ thành tường xiên.
    """
    gx, gy = ends[1].x - ends[0].x, ends[1].y - ends[0].y
    gap = math.hypot(gx, gy)
    hosts = []
    for wall in walls:
        line = wall.centreline
        if not {line.start, line.end} & set(ends):
            continue
        wx, wy = line.end.x - line.start.x, line.end.y - line.start.y
        length = math.hypot(wx, wy)
        if gap and length and abs(gx * wx + gy * wy) / (gap * length) < _HUG_COS_MIN:
            continue
        hosts.append(wall)
    return hosts


def _gap_door(
    start: Point, end: Point, thickness_mm: int, page: PageRaster | None, scaling: Scaling
) -> tuple[SwingDirection, float, tuple[float, float] | None]:
    """Chiều mở + tin cậy + hướng cánh của cửa đoán trên khe `start → end` (C5), cùng luật C3 của `_read_swing`."""
    span = math.dist((start.x, start.y), (end.x, end.y))
    ux, uy = (end.x - start.x) / span, (end.y - start.y) / span
    scale = scaling.build
    leaf = None
    if page is not None:
        a, b = (start.x / scale, start.y / scale), (end.x / scale, end.y / scale)
        leaf = read_leaf(page, a, b, thickness_mm / 2 / scale)
    if leaf is not None:
        swing: SwingDirection = "right" if leaf.hinge_at_b else "left"
        swing = "double" if span > _MAX_SINGLE_LEAF_MM else swing
        return swing, _INFERRED_DOOR_CONFIDENCE, (-uy * leaf.side, ux * leaf.side)
    if span >= _SLIDING_MIN_MM:
        return "sliding", _INFERRED_DOOR_CONFIDENCE, None
    return "left", _GUESSED_DOOR_CONFIDENCE, None


def _extend_host(
    walls: list[Wall],
    openings: list[Opening],
    lines: list[LineString],
    hosts: Sequence[Wall],
    ends: tuple[Point, Point],
) -> None:
    """Khe tường ngoài < `_ENVELOPE_DOOR_MIN_MM`: kéo một tường chủ tới đầu kia của khe (gộp, không thêm tường vụn).

    Ưu tiên tường có **cuối** ở khe (độ lệch ô mở tính từ đầu không đổi); không có thì dời đầu của tường đầu tiên và
    cộng phần kéo thêm vào `offsetMm` các ô mở của nó.
    """
    at_end = next((host for host in hosts if host.centreline.end in ends), None)
    host = hosts[0] if at_end is None else at_end
    index = next(position for position, wall in enumerate(walls) if wall.id == host.id)
    line = walls[index].centreline
    tip = line.start if at_end is None else line.end
    target = ends[1] if tip == ends[0] else ends[0]
    if at_end is not None:
        walls[index] = walls[index].model_copy(update={"centreline": Segment(start=line.start, end=target)})
    else:
        walls[index] = walls[index].model_copy(update={"centreline": Segment(start=target, end=line.end)})
        shift = js_round(math.dist((target.x, target.y), (tip.x, tip.y)))
        openings[:] = [
            item.model_copy(update={"offset_mm": item.offset_mm + shift}) if item.wall_id == host.id else item
            for item in openings
        ]
    lines[index] = _centreline(walls[index])


def _inner_room(wall: Wall, rooms: Sequence[Room]) -> Room | None:
    """Phòng phía trong khi `wall` ở mặt ngoài (một bên là phòng, cùng phép dò `wall_side_by`), không thì `None`."""
    found: list[Room] = []

    def contains(x: float, y: float) -> bool:
        """Có phòng nào chứa điểm không; nhớ phòng chứa nó."""
        room = next((room for room in rooms if outline_contains(room.outline, x, y)), None)
        if room is not None:
            found.append(room)
        return room is not None

    return found[0] if wall_side_by(wall, contains) == "exterior" else None


def _ink_rows(ends: tuple[Point, Point], thickness_mm: int, page: PageRaster | None, scaling: Scaling) -> np.ndarray:
    """Cờ từng hàng px song song khe trong dải bề dày: hàng là nét liền (`_GLAZING_HIT`, mẫu giữa 70 % khe, bỏ đầu
    tường, trên mực ngoài mặt nạ tường). Không có trang khớp mặt nạ → mảng rỗng."""
    if page is None or page.gray.shape != page.walls.shape:
        return np.zeros(0, dtype=np.bool_)
    a = np.array((ends[0].x, ends[0].y), dtype=float) / scaling.build
    b = np.array((ends[1].x, ends[1].y), dtype=float) / scaling.build
    run = float(np.hypot(*(b - a)))
    unit = (b - a) / run
    normal = np.array((-unit[1], unit[0]))
    along = a + np.outer(np.linspace(0.15, 0.85, max(8, round(run))), b - a)
    half = math.ceil(thickness_mm / 2 / scaling.build) + 1
    height, width = page.gray.shape
    lined = np.zeros(2 * half + 1, dtype=np.bool_)
    for row, offset in enumerate(range(-half, half + 1)):
        cols, rows = np.rint(along + normal * offset).astype(int).T
        if cols.min() < 0 or rows.min() < 0 or cols.max() >= width or rows.max() >= height:
            continue
        ink = (page.gray[rows, cols] < INK_GRAY) & ~page.walls[rows, cols]
        lined[row] = ink.mean() >= _GLAZING_HIT
    return lined


def ink_rail(ends: tuple[Point, Point], thickness_mm: int, page: PageRaster | None, scaling: Scaling) -> bool:
    """Có ít nhất một nét liền chạy dọc khe trong dải bề dày, không phải khối đặc (`_GLAZING_SOLID`).

    Lỏng hơn `_glazed`: ảnh độ phân giải thấp vẽ ray cửa lùa thành một vệt liền nhiều px (bench-3d f-22: 4 hàng).
    """
    lined = _ink_rows(ends, thickness_mm, page, scaling)
    return bool(lined.any()) and float(lined.mean()) <= _GLAZING_SOLID


def _glazed(ends: tuple[Point, Point], thickness_mm: int, page: PageRaster | None, scaling: Scaling) -> bool:
    """Khe có vách kính/khung cửa sổ vẽ nét mảnh: trong dải bề dày tường có ≥ 2 nét liền chạy dọc khe (`_ink_rows`).

    Dải mực liền quá `_GLAZING_SOLID` bề dày là khối đặc, không phải kính. Không có trang → `False`.
    """
    lined = _ink_rows(ends, thickness_mm, page, scaling)
    if not lined.size:
        return False
    strokes = int(lined[0]) + int(np.count_nonzero(lined[1:] & ~lined[:-1]))
    # ponytail: tường vẽ hai nét viền mà mặt nạ bỏ sót cũng ra 2 nét → cửa sổ; tách được nếu đo khoảng cách các nét.
    return strokes >= 2 and float(lined.mean()) <= _GLAZING_SOLID


def _gap_target(
    label: str | None,
    room: Room,
    ends: tuple[Point, Point],
    thickness_mm: int,
    page: PageRaster | None,
    scaling: Scaling,
) -> tuple[LabelTarget | None, float]:
    """Ô mở cho tường qua khe ≥ `_ENVELOPE_DOOR_MIN_MM` (xem `close_envelope_gaps`) + tin cậy; `None` = tường đặc."""
    if label == _BRIDGE:
        return (None if room.usage in _NO_GUESSED_DOOR else LABEL_TARGETS["door"]), _INFERRED_DOOR_CONFIDENCE
    if label is not None and label in _BOX_TARGETS:
        return _BOX_TARGETS[label], _INFERRED_DOOR_CONFIDENCE
    if _glazed(ends, thickness_mm, page, scaling):
        return LABEL_TARGETS["window"], _GUESSED_DOOR_CONFIDENCE
    return None, 0.0


def _gap_opening(
    wall: Wall, target: LabelTarget, confidence: float, page: PageRaster | None, scaling: Scaling, clock: Clock
) -> tuple[Opening, tuple[float, float] | None]:
    """Ô mở phủ cả tường qua khe `wall`: cửa đi lấy chiều mở/tin/cánh từ `_gap_door`, cửa sổ lấy `confidence`."""
    start, end = wall.centreline.start, wall.centreline.end
    swing: SwingDirection = target.swing or "sliding"
    leaf = None
    if target.domain_kind == "door":
        swing, confidence, leaf = _gap_door(start, end, wall.thickness_mm, page, scaling)
    opening = Opening(
        id=new_spatial_id("opening", clock),
        wall_id=wall.id,
        kind="door" if target.domain_kind == "door" else "window",
        offset_mm=0,
        width_mm=math.floor(math.dist((start.x, start.y), (end.x, end.y))),
        height_mm=target.height_mm or 2200,
        sill_height_mm=target.sill_mm or 0,
        swing=swing,
        confidence=confidence,
        source="ai",
        reviewed=False,
    )
    return opening, leaf


def _gap_wall(wall_id: str, ends: tuple[Point, Point], thickness: int, confidence: float, level_id: str) -> Wall:
    """Tường qua khe (không đầu vào, chưa ô mở) `ends`; id tạm, nơi gọi cấp id mới khi giữ tường."""
    return Wall(
        id=wall_id,
        level_id=level_id,
        centreline=Segment(start=ends[0], end=ends[1]),
        thickness_mm=thickness,
        detected_thickness_mm=thickness,
        height_mm=DEFAULT_WALL_HEIGHT_MM,
        kind="partition",
        opening_ids=(),
        confidence=confidence,
        source="ai",
        reviewed=False,
    )


def _opening_near(walls: Sequence[Wall], openings: Sequence[Opening], gap: tuple[Point, Point], kind: str) -> bool:
    """Hộp sinh đoạn khép `gap` đã thành ô mở `kind` trên tường cạnh khe: ô mở cùng loại, tường chủ song song và cách
    đường khe ≤ `_OPENING_NEAR_MM`, khoảng chiếu lên khe giao (chạm) khe — cửa sổ trên tường vuông góc gần góc không
    chặn cửa đi qua khe (Codex r3 #4)."""
    by_id = {wall.id: wall.centreline for wall in walls}
    start, end = gap
    span = math.dist((start.x, start.y), (end.x, end.y))
    ux, uy = (end.x - start.x) / span, (end.y - start.y) / span
    for opening in openings:
        line = by_id.get(opening.wall_id)
        if opening.kind != kind or line is None:
            continue
        dx, dy = line.end.x - line.start.x, line.end.y - line.start.y
        length = math.hypot(dx, dy)
        if length == 0 or abs(dx * uy - dy * ux) > length * _PARALLEL_SIN:
            continue
        ends = [
            (line.start.x + dx * t - start.x, line.start.y + dy * t - start.y)
            for t in (opening.offset_mm / length, (opening.offset_mm + opening.width_mm) / length)
        ]
        along = sorted(ex * ux + ey * uy for ex, ey in ends)
        across = abs(ends[0][0] * uy - ends[0][1] * ux)
        if across <= _OPENING_NEAR_MM and along[1] >= 0 and along[0] <= span:
            return True
    return False


_PIECE_MIN_MM: Final = 100
"""Phần hở tối thiểu của đoạn khép sau khi trừ tường song song đã phủ (`_uncovered`); ngắn hơn là mẩu vụn."""


def _uncovered(
    start: Point, end: Point, label: str | None, walls: Sequence[Wall]
) -> list[tuple[Point, Point, str | None, list[Wall]]]:
    """Đoạn khép trừ phần tường song song đã phủ (thường là tường qua khe 3c mang ô mở của chính hộp đó).

    `build_rooms` dựng đoạn khép trên tường có đầu vào nên không thấy tường qua khe 3c: đoạn khép cả khe chồng tường đó,
    `_clash` bỏ cả đoạn và vai tường hai bên ô mở vẫn hở. Trả phần còn hở (≥ `_PIECE_MIN_MM`, nhãn `None` vì ô mở đã có)
    kèm tường phủ làm chủ; không chồng gì → đoạn nguyên vẹn; cắt xuyên tường → đoạn nguyên để vòng chính bỏ như cũ.
    """
    span = math.dist((start.x, start.y), (end.x, end.y))
    if span == 0:
        return []
    probe = _Seg(start, end, 1, 1, 1.0, ())
    ua = _unit(probe)
    covered: list[tuple[float, float]] = []
    near: list[Wall] = []
    for wall in walls:
        other = _wall_seg(wall)
        if not _clash(_Seg(start, end, wall.thickness_mm, wall.thickness_mm, 1.0, ()), other):
            continue
        ub = _unit(other)
        if abs(ua[0] * ub[1] - ua[1] * ub[0]) > _PARALLEL_SIN:
            return [(start, end, label, [])]
        low, high = sorted(_along(probe, (p.x, p.y)) for p in (other.start, other.end))
        covered.append((max(0.0, low), min(span, high)))
        near.append(wall)
    if not covered:
        return [(start, end, label, [])]

    def at(t: float) -> Point:
        ratio = t / span
        return Point(x=js_round(start.x + (end.x - start.x) * ratio), y=js_round(start.y + (end.y - start.y) * ratio))

    pieces: list[tuple[Point, Point, str | None, list[Wall]]] = []
    reach = 0.0
    for low, high in [*sorted(covered), (span, span)]:
        if low - reach >= _PIECE_MIN_MM:
            pieces.append((at(reach), at(low), None, near))
        reach = max(reach, high)
    return pieces


def _point_regions(walls: Sequence[Wall]) -> Callable[[Point], int]:
    """Số vùng (`region_labels`) của tường có tim gần điểm nhất; tính một lần trên tường trước khi khép vỏ."""
    if not walls:
        return lambda _point: 0
    segs = [_wall_seg(wall) for wall in walls]
    geoms = _linestrings(segs)
    labels = _region_labels(segs, geoms)
    tree = STRtree(geoms)
    return lambda point: labels[int(tree.nearest(shapely.Point(point.x, point.y)))]


def close_envelope_gaps(
    wall_set: WallSet,
    rooms: Sequence[Room],
    *,
    level_id: str,
    scaling: Scaling,
    clock: Clock,
    page: PageRaster | None = None,
    closers: Sequence[RoomCloser] = (),
) -> WallSet:
    """C5: khe trên **mặt ngoài** các phòng (đoạn nối 3b `bridges`, đoạn khép phòng `closers`) → tường, không bỏ hở.

    Phòng đã khép qua các đoạn này nên lớp tường hở đúng chỗ đó (vỏ 3D hở, W1). Chỉ đoạn có đúng một bên là phòng
    (`_inner_room`); khe trong nhà (hai bên đều là phòng) giữ hở: lối mở thật có tồn tại (lối khách - bếp), và khe
    không bên nào là phòng (sân trong, giếng trời) cũng không bịt. Đoạn cắt tim tường khác (khe đã có tường qua khe
    3c, vách chắn ngang) hay chồng song song lên tường đang có, kể cả tường vừa thêm (`_clash`: đoạn nối xoè từ một
    đầu tới từng mảnh thẳng hàng phía bên kia), bỏ qua — chỉ phủ phần hở, không vẽ đè (W4). Đoạn khép chồng song
    song một phần tường qua khe 3c (`build_rooms` không thấy tường đó) chỉ dựng phần còn hở (`_uncovered`).

    - Khe < `_ENVELOPE_DOOR_MIN_MM` có tường chủ thẳng hàng (`_bridge_hosts`): kéo tường chủ (`_extend_host`).
    - `bridges` dài hơn: tường mang cửa đi phủ cả khe (cánh đọc từ nét vẽ, ≥ 1,5 m không cánh → lùa, còn lại
      cánh trái tin thấp), trừ phòng phía trong thuộc `_NO_GUESSED_DOOR` → tường đặc (R2').
    - Khe nối hai vùng tách rời (`_point_regions`, như `_join_gaps`: hai căn cạnh nhau trên một tờ) không thành
      tường: đoạn khép phòng của `build_rooms` qua giấy trắng giữa hai căn không được nối chúng (BENCH3D-20 B4).
    - `closers` còn lại: tường mới. Khe ≥ `_ENVELOPE_DOOR_MIN_MM` do hộp ô mở khép → mang ô mở đó (bỏ đoạn nếu
      hộp đã thành ô mở trên tường khác, `_opening_near`); không hộp mà có nét kính (`_glazed`) → cửa sổ tin thấp;
      còn lại tường đặc — không đoán cửa đi.
    """
    walls, openings = list(wall_set.walls), list(wall_set.openings)
    leaves = dict(wall_set.leaves)
    lines = [_centreline(wall) for wall in walls]
    gaps: list[tuple[Point, Point, str | None, list[Wall]]] = [
        (start, end, _BRIDGE, []) for start, end in wall_set.bridges
    ]
    for closer in closers:
        gaps += _uncovered(closer.start, closer.end, closer.label, walls)
    region_of = _point_regions(walls)
    for start, end, label, near in gaps:
        if region_of(start) != region_of(end):
            continue
        span = math.dist((start.x, start.y), (end.x, end.y))
        hosts = _bridge_hosts(walls, (start, end))
        if label == _BRIDGE:
            ux, uy = (end.x - start.x) / span, (end.y - start.y) / span
            inner = LineString(((start.x + ux, start.y + uy), (end.x - ux, end.y - uy)))
            if not hosts or any(inner.intersects(line) for line in lines):
                continue
        owners = hosts or [wall for wall in walls if start in (wall.centreline.start, wall.centreline.end)] or near
        if not owners or span == 0:
            continue
        thickness = max(host.thickness_mm for host in owners)
        if any(_clash(_Seg(start, end, thickness, thickness, 1.0, ()), _wall_seg(other)) for other in walls):
            continue
        wall = _gap_wall(owners[0].id, (start, end), thickness, min(host.confidence for host in owners), level_id)
        room = _inner_room(wall, rooms)
        if room is None:
            continue
        if span < _ENVELOPE_DOOR_MIN_MM and hosts:
            _extend_host(walls, openings, lines, hosts, (start, end))
            continue
        if (box := _BOX_TARGETS.get(label or "")) and _opening_near(walls, openings, (start, end), box.domain_kind):
            continue
        target, confidence = None, 0.0
        if span >= _ENVELOPE_DOOR_MIN_MM:
            target, confidence = _gap_target(label, room, (start, end), thickness, page, scaling)
        wall = wall.model_copy(update={"id": new_spatial_id("wall", clock)})
        if target is not None:
            opening, leaf = _gap_opening(wall, target, confidence, page, scaling, clock)
            wall = wall.model_copy(update={"opening_ids": (opening.id,)})
            openings.append(opening)
            if leaf is not None:
                leaves[opening.id] = leaf
        walls.append(wall)
        lines.append(_centreline(wall))
    return replace(wall_set, walls=tuple(walls), openings=tuple(openings), leaves=MappingProxyType(leaves))


_SYMBOL_SLACK_MM: Final = 50
"""Tường ký hiệu cửa phải nằm trọn trong hộp cửa nới mỗi phía 50 mm (cung quạt hay chạm mép hộp)."""
_SYMBOL_ALIGN_SIN: Final = math.sin(math.radians(10.0))
_DOOR_SYMBOL_LABELS: Final = frozenset({"door", "double_door"})


def _symbol_of(seg: _Seg, rect: tuple[float, float, float, float], anchors: Sequence[_Seg]) -> bool:
    """`seg` là nét ký hiệu cửa (cung quạt, cánh) của hộp `rect`, không phải tường thật.

    Hai đầu nằm trong hộp nới `_SYMBOL_SLACK_MM`, và không nằm trên tim tường neo nào (`anchors`: tường thật với tới
    hộp, có đầu ra ngoài hộp) — cùng hướng (≤ 10°) và cách tim ≤ nửa bề dày thì là mẩu tường chủ, giữ.
    """
    x0, y0, x1, y1 = rect
    s = _SYMBOL_SLACK_MM
    if not all(x0 - s <= p.x <= x1 + s and y0 - s <= p.y <= y1 + s for p in (seg.start, seg.end)):
        return False
    sx, sy = _unit(seg)
    mid = ((seg.start.x + seg.end.x) / 2, (seg.start.y + seg.end.y) / 2)
    for host in anchors:
        hx, hy = _unit(host)
        across = hx * (mid[1] - host.start.y) - hy * (mid[0] - host.start.x)
        if abs(hx * sy - hy * sx) <= _SYMBOL_ALIGN_SIN and abs(across) <= host.thickness_mm / 2:
            return False
    return True


_Wedge = tuple[float, float, float, float, float, float, float]
"""Quạt một cánh (mm): bản lề `(hx, hy)`, hướng dây khe `(tx, ty)`, pháp tuyến phía cánh `(sx, sy)`, bán kính `r`."""

_WEDGE_ARC_HIT: Final = 0.6
_WEDGE_LEAF_HIT: Final = 0.6
_WEDGE_MARGIN: Final = 0.15
"""R8-W v5: mực mảnh phủ ≥ 60 % mẫu cung và cánh, hơn quạt khác vùng ≥ 0,15 (vách tủ/placard không có cung → giữ)."""

_SOLID_MM: Final = 80.0
"""Mực đặc dày ≥ 80 mm là tường tô đen, không phải nét ký hiệu: bỏ khi đọc quạt (cánh dọc mặt tường không ăn điểm)."""

_LEAF_ANGLES: Final = tuple(range(30, 91, 5))
"""Cánh vẽ mở 30-90° so với dây khe (cvcfp hay vẽ cánh xiên 30°)."""


def _wedge_hypotheses(rect: tuple[float, float, float, float]) -> list[_Wedge]:
    """Giả thuyết quạt của hộp (mm).

    Họ khe: khe trên trục dài, bản lề ở hai đầu dời ra mặt tường (nửa cạnh ngắn), cánh về hai phía, bán kính = cạnh dài.
    Hộp gần vuông (dài < 2 lần ngắn) thêm họ góc: bản lề ở 4 góc, dây + cánh theo hai cạnh, bán kính = cạnh ngắn.
    """
    x0, y0, x1, y1 = rect
    width, height = x1 - x0, y1 - y0
    horizontal = width >= height
    long_side, half = (width, height / 2) if horizontal else (height, width / 2)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    a, b = ((x0, cy), (x1, cy)) if horizontal else ((cx, y0), (cx, y1))
    ux, uy = (1.0, 0.0) if horizontal else (0.0, 1.0)
    hyps: list[_Wedge] = []
    for side in (1, -1):
        sx, sy = -uy * side, ux * side
        hyps += [(a[0] + sx * half, a[1] + sy * half, ux, uy, sx, sy, long_side)]
        hyps += [(b[0] + sx * half, b[1] + sy * half, -ux, -uy, sx, sy, long_side)]
    if max(width, height) < 2 * min(width, height):
        radius = min(width, height)
        for hx, hy, dx, dy in ((x0, y0, 1.0, 1.0), (x1, y0, -1.0, 1.0), (x0, y1, 1.0, -1.0), (x1, y1, -1.0, -1.0)):
            hyps += [(hx, hy, dx, 0.0, 0.0, dy, radius), (hx, hy, 0.0, dy, dx, 0.0, radius)]
    return hyps


def _wedge_score(
    ink: NDArray[np.bool_], origin: tuple[int, int], wedge: _Wedge, build: float
) -> tuple[float, float, float]:
    """Điểm tốt nhất theo góc cánh `_LEAF_ANGLES`: `(trung bình, cánh, cung)`; cung lấy mẫu từ má kia tới cánh."""
    hx, hy, tx, ty, sx, sy, r = wedge
    depths = np.linspace(0.15, 1.0, 24) * r
    best = (0.0, 0.0, 0.0)
    for degrees in _LEAF_ANGLES:
        phi = math.radians(degrees)
        dx, dy = tx * math.cos(phi) + sx * math.sin(phi), ty * math.cos(phi) + sy * math.sin(phi)
        leaf = np.column_stack((hx + dx * depths, hy + dy * depths)) / build
        angles = np.radians(np.linspace(8.0, degrees - 8.0, 24))
        cos, sin = np.cos(angles), np.sin(angles)
        arc = np.column_stack((hx + r * (tx * cos + sx * sin), hy + r * (ty * cos + sy * sin))) / build
        leaf_hit, arc_hit = _hit(ink, origin, leaf), _hit(ink, origin, arc)
        if (leaf_hit + arc_hit) / 2 > best[0]:
            best = ((leaf_hit + arc_hit) / 2, leaf_hit, arc_hit)
    return best


def _read_wedge(rect: tuple[float, float, float, float], page: PageRaster, build: float) -> _Wedge | None:
    """R8-W v5: quạt có cánh + cung đọc được trên mực mảnh của trang.

    Không trừ mặt nạ tường (quạt nằm trong mặt nạ: lý do v4 mất tác dụng) mà bỏ mực đặc dày ≥ `_SOLID_MM`. Không giả
    thuyết nào đủ `_WEDGE_*`, hay quạt khác vùng sát điểm (< `_WEDGE_MARGIN`) → `None`. Cửa đôi/lùa thường không đọc
    được → giữ.
    """
    x0, y0, x1, y1 = (v / build for v in rect)
    reach = max(x1 - x0, y1 - y0) + 4
    height, cols = page.gray.shape
    cx0, cy0 = max(0, math.floor(x0 - reach)), max(0, math.floor(y0 - reach))
    cx1, cy1 = min(cols, math.ceil(x1 + reach) + 1), min(height, math.ceil(y1 + reach) + 1)
    if cx1 <= cx0 or cy1 <= cy0:
        return None
    ink = (page.gray[cy0:cy1, cx0:cx1] < INK_GRAY).astype(np.uint8)
    k = int(min(15, max(3, round(_SOLID_MM / build))))
    solid = np.asarray(cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((k, k), np.uint8)), dtype=np.uint8)
    thin = ink & (1 - solid)
    pad = max(2, round(0.05 * max(x1 - x0, y1 - y0)))
    dilated = np.asarray(cv2.dilate(thin, np.ones((2 * pad + 1, 2 * pad + 1), np.uint8)) > 0)
    scored = sorted(
        ((_wedge_score(dilated, (cx0, cy0), w, build), w) for w in _wedge_hypotheses(rect)), key=lambda t: -t[0][0]
    )
    passing = [item for item in scored if item[0][1] >= _WEDGE_LEAF_HIT and item[0][2] >= _WEDGE_ARC_HIT]
    if not passing:
        return None
    best = passing[0]

    def region(w: _Wedge) -> tuple[float, ...]:
        # Hai cạnh từ góc đổi vai (dây ↔ cánh) vẫn là cùng một quạt.
        return (round(w[0]), round(w[1]), round(w[2] + w[4], 3), round(w[3] + w[5], 3))

    rival = next((item for item in scored if region(item[1]) != region(best[1])), None)
    if rival is not None and best[0][0] - rival[0][0] < _WEDGE_MARGIN:
        return None
    return best[1]


def _in_wedges(seg: _Seg, wedges: Sequence[_Wedge]) -> bool:
    """Năm điểm mẫu của `seg` nằm trong cùng một quạt 1/4 tròn (nới `_SYMBOL_SLACK_MM` + 10 % bán kính): cánh, cung và
    tường giả hình V do cung vector hoá nằm trong đó; vách thật dài hơn cánh hay ở ngoài quạt thì không."""
    s = _SYMBOL_SLACK_MM
    dx, dy = seg.end.x - seg.start.x, seg.end.y - seg.start.y
    pts = [(seg.start.x + dx * f, seg.start.y + dy * f) for f in (0.0, 0.25, 0.5, 0.75, 1.0)]
    for hx, hy, tx, ty, sx, sy, r in wedges:
        uv = [((px - hx) * tx + (py - hy) * ty, (px - hx) * sx + (py - hy) * sy) for px, py in pts]
        # Hộp mảnh: bản lề ở mặt tường, nét quạt vector hoá có thể bắt đầu từ tim tường (sau mặt ≤ nửa cạnh ngắn ≤ r/4).
        if all(u >= -s and v >= -s - r / 4 and math.hypot(max(u, 0.0), max(v, 0.0)) <= 1.1 * r + s for u, v in uv):
            return True
    return False


def _swing_zone(rect: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """R6-W v2 (không có trang): hộp mảnh (cạnh dài ≥ 2 lần cạnh ngắn) nới cạnh ngắn mỗi phía một bán kính cánh."""
    x0, y0, x1, y1 = rect
    width, height = x1 - x0, y1 - y0
    if max(width, height) < 2 * min(width, height):
        return rect
    if width >= height:
        return (x0, y0 - width, x1, y1 + width)
    return (x0 - height, y0, x1 + height, y1)


def _touches(seg: _Seg, rect: tuple[float, float, float, float]) -> bool:
    """Một đầu `seg` nằm trong hộp nới `_SYMBOL_SLACK_MM`: cánh/cung quạt luôn xuất phát từ khe cửa."""
    x0, y0, x1, y1 = rect
    s = _SYMBOL_SLACK_MM
    return any(x0 - s <= p.x <= x1 + s and y0 - s <= p.y <= y1 + s for p in (seg.start, seg.end))


def _tied_outside(seg: _Seg, rect: tuple[float, float, float, float], geoms: Geoms, within: Collection[int]) -> bool:
    """Sửa sau phản biện X2-C1: đầu xa (không chạm hộp) của `seg` nối vào tường nằm ngoài vùng quạt → vách thật (vách tủ
    âm tường cạnh khe cửa, cvcfp-Ic_JN0702), giữ. Cánh cửa có đầu xa tự do hoặc nối dây cung (cũng trong vùng quạt).
    """
    x0, y0, x1, y1 = rect
    s = _SYMBOL_SLACK_MM
    far = [p for p in (seg.start, seg.end) if not (x0 - s <= p.x <= x1 + s and y0 - s <= p.y <= y1 + s)]
    if not far:
        return False
    for p in far:
        point = shapely.points(p.x, p.y)
        near = shapely.dwithin(geoms, point, s)
        if any(near[k] for k in range(len(geoms)) if k not in within):
            return True
    return False


def _drop_door_symbols(
    segs: list[_Seg], detections: Sequence[DetectionPx], scaling: Scaling, page: PageRaster | None = None
) -> tuple[list[_Seg], set[int]]:
    """R6-W: bỏ khỏi hình học tường các nét ký hiệu cửa đã thành tường, **trước** khi gắn ô mở (bước 3b → 3c).

    Baseline: dây cung quạt đi qua tâm hộp nên `_attach_walls`/`_runs_along` chọn nó làm tường chủ, ô mở nằm trên
    quạt và vách thật để hở. Ở mỗi hộp `door`/`double_door`: tường neo = tường với tới hộp (`_attach_walls`) có đầu
    ra ngoài vùng quạt; không có neo → không đủ căn cứ, giữ nguyên. Nét nằm trọn trong vùng quạt (`_swing_zone`, v2:
    hộp mảnh nới theo bán kính cánh), có một đầu chạm hộp và không nằm trên tim neo (`_symbol_of`) bị bỏ. Không hộp
    (detector bỏ sót) → quạt còn như cũ; hộp gọi sai trên tường thật chỉ bỏ nét nằm trọn trong vùng quạt, tường neo
    không bao giờ bị bỏ. Trả thứ tự (trong `detections`) của
    hộp đã bỏ nét để `_symbol_closers` khép phòng qua ô mở.
    """
    chosen = [(order, det) for order, det in enumerate(detections) if det.label in _DOOR_SYMBOL_LABELS]
    if not segs or not chosen:
        return segs, set()
    rects = [_rect(det.box, scaling) for _, det in chosen]
    geoms = _linestrings(segs)
    drop: set[int] = set()
    doors: set[int] = set()
    # ponytail: nét trọn trong hộp lệch tim neo coi là ký hiệu; vách thật ngắn < 1 hộp nằm trọn trong hộp gọi sai sẽ
    # mất — cần đọc mực (cung tròn) nếu đo thấy ca đó.
    for (order, _), rect, near in zip(chosen, rects, _attach_walls(segs, geoms, rects), strict=True):
        if page is not None:
            # R8-W v5: chỉ bỏ nét nằm trong quạt có cánh + cung đọc được trên mực thô; không đọc được → giữ hết.
            wedge = _read_wedge(rect, page, scaling.build)
            if wedge is None:
                continue
            wedges = (wedge,)
            xs = [w[0] + w[6] * d for w in wedges for d in (0.0, w[2], w[4])]
            ys = [w[1] + w[6] * d for w in wedges for d in (0.0, w[3], w[5])]
            pad = max(w[6] for w in wedges) / 4
            zone = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
            within = {k for k, seg in enumerate(segs) if _in_wedges(seg, wedges)}
            inside = within
        else:
            zone = _swing_zone(rect)
            # ponytail: quét mọi tường cho mỗi hộp (O(tường x hộp)); dùng STRtree nếu đo thấy chậm.
            within = {k for k, seg in enumerate(segs) if _symbol_of(seg, zone, ())}
            inside = {k for k in within if _touches(segs[k], rect)}
        anchors = [segs[k] for k in near if k not in within]
        found = {k for k in inside if _symbol_of(segs[k], zone, anchors)} if anchors else set()
        found = {k for k in found if not _tied_outside(segs[k], rect, geoms, within)}
        if found:
            drop |= found
            doors.add(order)
    return [seg for k, seg in enumerate(segs) if k not in drop], doors


def _symbol_closers(segs: Sequence[_Seg], cands: Sequence[_Cand], doors: Collection[int]) -> list[tuple[Point, Point]]:
    """Đoạn khép phòng trên tim tường chủ qua khoảng ô mở của cửa đã bỏ nét ký hiệu (`WallSet.bridges`, chỉ dựng phòng).

    Trước đây chính cung quạt vô tình bịt khe cửa (R5 FANMASK: bỏ quạt mà không bịt → phòng hở/gộp).
    """
    closers = []
    for cand in cands:
        if cand.order in doors:
            host = segs[cand.wall]
            ux, uy = _unit(host)
            a, b = cand.offset_mm, cand.offset_mm + cand.width_mm
            start = Point(x=js_round(host.start.x + ux * a), y=js_round(host.start.y + uy * a))
            closers.append((start, Point(x=js_round(host.start.x + ux * b), y=js_round(host.start.y + uy * b))))
    return closers


def _drop_bare_gap_walls(segs: Sequence[_Seg], cands: Sequence[_Cand]) -> tuple[list[_Seg], list[_Cand]]:
    """Bỏ tường qua khe (bước 3c, không đầu vào) không còn ô mở nào: ô mở rơi thì tường đó bịt lối (OPEN-ATTACH).

    Tường qua khe nằm cuối `segs` nên bỏ chúng không dời chỉ số tường có đầu vào (`input_to_wall` giữ nguyên);
    `wall` của ô mở đánh lại theo vị trí mới.
    """
    hosts = {cand.wall for cand in cands}
    kept = [index for index, seg in enumerate(segs) if seg.inputs or index in hosts]
    position = {old: new for new, old in enumerate(kept)}
    return [segs[index] for index in kept], [replace(cand, wall=position[cand.wall]) for cand in cands]
