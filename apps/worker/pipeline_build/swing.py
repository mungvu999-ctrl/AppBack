"""Chiều mở cửa đi một cánh đọc từ nét vẽ: cánh (đoạn thẳng vuông tường) + cung 1/4 tròn quanh bản lề (QT-SWING).

Trước đây `Opening.swing` lấy theo nhãn YOLO (`door` → luôn `"left"`) và `reversed` luôn `False`: cửa 3D mở sai
phía, sai bản lề. Ở đây thử 4 giả thuyết (bản lề ở đầu `a`/`b` của khe x cánh về phía `+n`/`-n`), lấy mẫu
dọc nét cánh và nét cung mỗi giả thuyết trên ảnh mực (trang xám < `INK_GRAY`, trừ mặt nạ tường, nới vài px), chọn
giả thuyết phủ mực nhiều nhất khi nó đủ chắc; không chắc → `None` để nơi gọi giữ giá trị cũ.

K-13a: thêm giả thuyết hai cánh (cửa đôi: bản lề hai đầu khe, hai cung bán kính rộng/2) dự thi cùng bốn giả thuyết
trên, và ray cửa lùa (hai tấm song song tường so le trong bề dày tường) khi không cánh nào thắng; công tắc
`DOOR_KIND_HYPOTHESES`.

Đổi sang ngữ nghĩa FE (`AppFront/src/lib/pascal/toPascal.ts` `doorLeafOf` + `swingDirection.ts`, Pascal
`nodes/src/door/floorplan.ts`):

- `swing="left"` ⇔ Pascal `hingesSide:'left'` ⇔ bản lề ở mép khe phía `centreline.start` của tường
  (`cx - dir·w/2`); `"right"` ⇔ bản lề ở mép phía `centreline.end`.
- Phía cánh: Pascal `inward` = cánh quét sang pháp tuyến TRÁI `n = (-dy, dx)/L` của trục (mặt bằng, y xuống trang).
  FE không lưu phía mở mà tính bằng luật `swingDirectionOf` (tường bao → mở vào nhà; cửa trong → phòng không phải
  hành lang, hoà thì phòng nhỏ hơn); `reversed=True` đảo kết quả luật. Nên `reversed = (phía đọc được ≠ phía luật)`,
  tính sau khi có phòng (`settle_reversed`, port đúng luật FE).
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

import cv2
import numpy as np
from numpy.typing import NDArray

from apps.worker.pipeline_build.residual import INK_GRAY, PageRaster
from packages.domain.spatial import Opening, Point, Room, SpatialLayer, Wall

Vec = tuple[float, float]

MIN_WIDTH_PX: Final = 12.0
"""Khe hẹp hơn: quá ít điểm ảnh để phân biệt cung với nét khác."""

MIN_ARC_HIT: Final = 0.35
MIN_LEAF_HIT: Final = 0.5
"""Giả thuyết thắng phải có mực dưới ≥ 35 % điểm mẫu cung và ≥ 50 % điểm mẫu cánh (cung thường vẽ nhạt, nét đứt)."""

MIN_MARGIN: Final = 0.15
"""Điểm thắng phải hơn giả thuyết nhì ≥ 0,15: hai giả thuyết sát nhau (cửa đôi, đường kích thước) → không đoán."""

_LEAF_FROM: Final = 0.15
"""Bỏ đoạn cánh sát mặt tường (≤ 15 % rộng): ở đó là nét khung/ghi chú chung cho mọi giả thuyết."""

_PROBE_MM: Final = 300.0
"""Khoảng dò phòng hai bên mặt tường, như `SWING_PROBE_MM` của FE."""


DOOR_KIND_HYPOTHESES: Final = True
"""K-13a: bật giả thuyết hai cánh (`_double_scores`) và ray cửa lùa (`_track_leaf`); `False` → đúng hành vi cũ."""

MIN_DOUBLE_HIT: Final = 0.85
"""Giả thuyết hai cánh chỉ dự thi khi cánh và cung của CẢ hai nửa có mực dưới ≥ 85 % điểm mẫu. Bench replay 24/11
(lần đo đầu, ngưỡng `MIN_*` như cửa một cánh): 3 cửa đôi thật đạt 1,00, còn 6 cửa một cánh 740-770 mm kiểu vẽ Phần
Lan (cánh nằm trong tường) bị đọc thành hai cánh ở 0,62-0,79 — chỉnh sau khi nhìn số, xem `changes/K-13a.md`."""

MIN_TRACK_HIT: Final = 0.6
"""Ray cửa lùa: nửa khe mà tấm cửa phủ phải có mực dưới ≥ 60 % điểm mẫu của hàng song song tường."""

MAX_TRACK_OTHER: Final = 0.35
"""... và nửa khe còn lại ≤ 35 %: tấm lệch về một đầu khe; nét kín cả khe (mép tường, ngưỡng) không phải tấm lùa."""

MAX_TRACK_BUSY: Final = 0.5
"""Khung khe có > 50 % điểm là mực (nới 1 px) — nền tô màu, hoa văn lát sàn — thì không thử ray: hàng nào cũng "phủ"
(bench ch-11: lô gia lát gạch 0,86 bị đọc thành cửa lùa ở lần đo đầu)."""

LeafKind = Literal["single", "double", "sliding"]


@dataclass(frozen=True, slots=True)
class Leaf:
    """Cánh đọc được: `hinge_at_b` = bản lề ở đầu `b` của khe; `side` = +1 cánh về phía `n`, -1 phía `-n`.

    `kind`: `"single"` một cánh; `"double"` hai cánh (bản lề hai đầu, `hinge_at_b` vô nghĩa); `"sliding"` hai tấm lùa
    so le trong khe (`side` = 0, không có hướng cánh).
    """

    hinge_at_b: bool
    side: int
    kind: LeafKind = "single"


@dataclass(frozen=True, slots=True)
class _Gap:
    """Khung đọc một khe: gốc khung mực, đầu khe `a`, rộng, hướng trục `u`, pháp tuyến `n`, nửa bề dày tường (px)."""

    origin: Vec
    a: Vec
    width: float
    u: Vec
    n: Vec
    half: float

    def at(self, along: float, across: float) -> Vec:
        """Điểm cách `a` một đoạn `along` dọc khe và `across` theo pháp tuyến."""
        return (self.a[0] + self.u[0] * along + self.n[0] * across, self.a[1] + self.u[1] * along + self.n[1] * across)


_Score = tuple[float, float, float, Leaf]
"""(điểm, tỉ lệ mực trên cánh, trên cung, giả thuyết)."""


def _ink(page: PageRaster, box: tuple[int, int, int, int], radius: int) -> NDArray[np.bool_]:
    """Mực trong khung `box = (x0, y0, x1, y1)` (đã cắt theo trang), trừ tường, nới `radius` px."""
    x0, y0, x1, y1 = box
    ink = (page.gray[y0:y1, x0:x1] < INK_GRAY) & ~page.walls[y0:y1, x0:x1]
    size = 2 * radius + 1
    return np.asarray(cv2.dilate(ink.astype(np.uint8), np.ones((size, size), np.uint8)) > 0)


def _hit(ink: NDArray[np.bool_], origin: Vec, points: NDArray[np.float64]) -> float:
    """Tỉ lệ điểm mẫu (px trang) rơi vào mực; điểm ra ngoài khung tính trượt."""
    cols = np.rint(points[:, 0] - origin[0]).astype(int)
    rows = np.rint(points[:, 1] - origin[1]).astype(int)
    inside = (cols >= 0) & (rows >= 0) & (cols < ink.shape[1]) & (rows < ink.shape[0])
    hits = np.zeros(len(points), bool)
    hits[inside] = ink[rows[inside], cols[inside]]
    return float(hits.mean())


def _swing_hits(ink: NDArray[np.bool_], gap: _Gap, hinge_along: float, side: int, radius: float) -> tuple[float, float]:
    """Mực của một cánh bản lề tại `hinge_along` (dọc khe, dời ra mặt tường phía `side`) dài `radius`, cung quét về
    phía tâm khe: (tỉ lệ trên nét cánh bỏ 15 % sát tường, tỉ lệ trên cung 8°-82°)."""
    toward = 1.0 if hinge_along < gap.width / 2 else -1.0
    depths = np.linspace(_LEAF_FROM, 1.0, 24) * radius
    angles = np.radians(np.linspace(8.0, 82.0, 32))
    leaf = np.array([gap.at(hinge_along, side * (gap.half + d)) for d in depths])
    arc = np.array(
        [
            gap.at(hinge_along + toward * radius * c, side * (gap.half + radius * s))
            for c, s in zip(np.cos(angles), np.sin(angles), strict=True)
        ]
    )
    return _hit(ink, gap.origin, leaf), _hit(ink, gap.origin, arc)


def _single_scores(ink: NDArray[np.bool_], gap: _Gap) -> list[_Score]:
    """4 giả thuyết một cánh: bản lề ở `a`/`b` x cánh về `+n`/`-n`, bán kính = rộng khe."""
    scores: list[_Score] = []
    for hinge_at_b in (False, True):
        for side in (1, -1):
            leaf_hit, arc_hit = _swing_hits(ink, gap, gap.width if hinge_at_b else 0.0, side, gap.width)
            scores.append(((leaf_hit + arc_hit) / 2, leaf_hit, arc_hit, Leaf(hinge_at_b=hinge_at_b, side=side)))
    return scores


def _double_scores(ink: NDArray[np.bool_], gap: _Gap) -> list[_Score]:
    """2 giả thuyết hai cánh (K-13a): bản lề ở cả hai đầu khe, hai cánh bán kính rộng/2 cùng phía `+n`/`-n`.

    Cánh và cung lấy tỉ lệ THẤP hơn của hai nửa: cửa một cánh chỉ phủ được một nửa (cung nửa kia trống) nên không đủ
    `MIN_DOUBLE_HIT` để cạnh tranh với giả thuyết một cánh đúng.
    """
    scores: list[_Score] = []
    for side in (1, -1):
        near = _swing_hits(ink, gap, 0.0, side, gap.width / 2)
        far = _swing_hits(ink, gap, gap.width, side, gap.width / 2)
        leaf_hit, arc_hit = min(near[0], far[0]), min(near[1], far[1])
        scores.append(((leaf_hit + arc_hit) / 2, leaf_hit, arc_hit, Leaf(hinge_at_b=False, side=side, kind="double")))
    return scores


def _track_leaf(page: PageRaster, gap: _Gap, box: tuple[int, int, int, int]) -> Leaf | None:
    """Ray cửa lùa (K-13a): trong bề dày tường có một hàng mực song song tường phủ nửa khe phía `a` (≥
    `MIN_TRACK_HIT`, nửa kia ≤ `MAX_TRACK_OTHER`) và một hàng phủ nửa phía `b` — hai tấm lùa so le, không cung.

    Mực chỉ nới 1 px để hai nét kề nhau không nhoè thành một; hàng cách nhau 1 px; mẫu dọc khe bỏ 5 % hai đầu.
    """
    ink = _ink(page, box, 1)
    if ink.mean() > MAX_TRACK_BUSY:
        return None
    lo = np.linspace(0.05, 0.45, 16) * gap.width
    hi = np.linspace(0.55, 0.95, 16) * gap.width
    leans: set[int] = set()
    for across in np.arange(-math.floor(gap.half), math.floor(gap.half) + 1, dtype=float):
        cover_a = _hit(ink, gap.origin, np.array([gap.at(t, across) for t in lo]))
        cover_b = _hit(ink, gap.origin, np.array([gap.at(t, across) for t in hi]))
        if cover_a >= MIN_TRACK_HIT and cover_b <= MAX_TRACK_OTHER:
            leans.add(-1)
        elif cover_b >= MIN_TRACK_HIT and cover_a <= MAX_TRACK_OTHER:
            leans.add(1)
    return Leaf(hinge_at_b=False, side=0, kind="sliding") if leans == {-1, 1} else None


def _frame(page: PageRaster, a: Vec, b: Vec, half_thickness: float) -> tuple[_Gap, tuple[int, int, int, int]] | None:
    """Khung đọc khe `a → b` + hộp px `(x0, y0, x1, y1)` cắt theo trang; `None` khi khe quá hẹp hay ra ngoài trang."""
    ax, ay = a
    bx, by = b
    width = math.hypot(bx - ax, by - ay)
    if width < MIN_WIDTH_PX:
        return None
    ux, uy = (bx - ax) / width, (by - ay) / width
    reach = width + half_thickness + 4
    height, cols = page.gray.shape
    x0 = max(0, math.floor(min(ax, bx) - reach))
    y0 = max(0, math.floor(min(ay, by) - reach))
    x1 = min(cols, math.ceil(max(ax, bx) + reach) + 1)
    y1 = min(height, math.ceil(max(ay, by) + reach) + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    return _Gap((x0, y0), a, width, (ux, uy), (-uy, ux), half_thickness), (x0, y0, x1, y1)


def _passes(score: _Score) -> bool:
    """Đủ ngưỡng thắng: cánh ≥ `MIN_LEAF_HIT`, cung ≥ `MIN_ARC_HIT`; giả thuyết hai cánh cả hai ≥ `MIN_DOUBLE_HIT`."""
    if score[3].kind == "double":
        return score[1] >= MIN_DOUBLE_HIT and score[2] >= MIN_DOUBLE_HIT
    return score[1] >= MIN_LEAF_HIT and score[2] >= MIN_ARC_HIT


def read_leaf(page: PageRaster, a: Vec, b: Vec, half_thickness: float) -> Leaf | None:
    """Đọc kiểu + bản lề + phía cánh của cửa đi có khe `a → b` (px, trên tim tường), tường dày `2·half_thickness` px.

    Giả thuyết một cánh: bản lề `h` (đầu `a` hay `b`) dời ra mặt tường `h + s·n·t`, cánh là đoạn vuông tường dài `w`
    (bỏ 15 % sát tường), cung tâm bản lề bán kính `w` từ mép kia tới đầu cánh (mẫu ở 8°-82°). Khi
    `DOOR_KIND_HYPOTHESES`: thêm hai giả thuyết hai cánh (chỉ dự thi khi tự đủ `MIN_DOUBLE_HIT`), và khi không giả
    thuyết cánh nào thắng thì thử ray cửa lùa. Thắng = đủ `MIN_*` và hơn nhì `MIN_MARGIN`. `None` khi khe quá hẹp,
    trang lệch khổ mặt nạ, hay không đọc được gì (nơi gọi lùi về luật bề rộng). Chỉ gọi cho ô mở `kind == "door"`.
    """
    if page.gray.shape != page.walls.shape:
        return None
    framed = _frame(page, a, b, half_thickness)
    if framed is None:
        return None
    gap, box = framed
    ink = _ink(page, box, max(2, round(0.05 * gap.width)))
    scores = _single_scores(ink, gap)
    if DOOR_KIND_HYPOTHESES:
        scores += [score for score in _double_scores(ink, gap) if _passes(score)]
    scores.sort(key=lambda score: (*score[:3], score[3].hinge_at_b, score[3].side), reverse=True)
    best, runner = scores[0], scores[1]
    if _passes(best) and best[0] - runner[0] >= MIN_MARGIN:
        return best[3]
    return _track_leaf(page, gap, box) if DOOR_KIND_HYPOTHESES else None


def _inside(p: Vec, outline: Sequence[Point]) -> bool:
    """Điểm trong đa giác (tia ngang, chẵn-lẻ) — như `insideOutline` của FE."""
    inside = False
    j = len(outline) - 1
    for i, a in enumerate(outline):
        b = outline[j]
        if (a.y > p[1]) != (b.y > p[1]) and p[0] < (b.x - a.x) * (p[1] - a.y) / (b.y - a.y) + a.x:
            inside = not inside
        j = i
    return inside


def rule_side(opening: Opening, wall: Wall, rooms: Sequence[Room]) -> int:
    """Port `swingDirectionOf` (FE): +1 = `inward` (pháp tuyến trái của tim), -1 = `outward`."""
    start, end = wall.centreline.start, wall.centreline.end
    length = math.hypot(end.x - start.x, end.y - start.y)
    if not length > 0:
        return 1
    dx, dy = (end.x - start.x) / length, (end.y - start.y) / length
    along = opening.offset_mm + opening.width_mm / 2
    reach = wall.thickness_mm / 2 + _PROBE_MM

    def room_at(sign: int) -> Room | None:
        p = (start.x + dx * along - dy * reach * sign, start.y + dy * along + dx * reach * sign)
        return next(
            (r for r in rooms if r.level_id == wall.level_id and r.finish != "decking" and _inside(p, r.outline)), None
        )

    left, right = room_at(1), room_at(-1)
    if left is None or right is None:
        return -1 if right is not None else 1
    if (left.usage == "corridor") != (right.usage == "corridor"):
        return -1 if left.usage == "corridor" else 1
    return -1 if right.area_m2 < left.area_m2 else 1


def settle_reversed(layer: SpatialLayer, leaves: Mapping[str, Vec]) -> SpatialLayer:
    """Đặt `reversed=True` cho cửa có phía cánh đọc được (`leaves`: id ô mở → hướng cánh tuyệt đối trên mặt bằng)
    khác phía luật FE tính trên phòng của `layer`; ô mở/tường không còn trong lớp thì bỏ qua."""
    if not leaves:
        return layer
    walls = {wall.id: wall for wall in layer.walls}
    openings = []
    for opening in layer.openings:
        vec = leaves.get(opening.id)
        wall = walls.get(opening.wall_id)
        if vec is not None and wall is not None:
            start, end = wall.centreline.start, wall.centreline.end
            seen = 1 if (end.y - start.y) * -vec[0] + (end.x - start.x) * vec[1] > 0 else -1
            if seen != rule_side(opening, wall, layer.rooms):
                opening = opening.model_copy(update={"reversed": True})
        openings.append(opening)
    return layer.model_copy(update={"openings": tuple(openings)})
