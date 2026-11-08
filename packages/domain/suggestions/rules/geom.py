"""Small plane geometry in layer millimetres, written so the editor can mirror it number for number.

Rounding uses `js_round` (= `Math.round`), not Python's banker's rounding. `polylabel` follows the published
mapbox/polylabel algorithm (cell queue, centroid seed), so the FE can use that package for L1.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import count

from packages.domain.spatial.model import Point, Room, Wall

XY = tuple[float, float]
Interval = tuple[float, float]


def js_round(value: float) -> int:
    """`Math.round`: halves go up (towards +inf)."""
    return math.floor(value + 0.5)


def ring(points: Sequence[Point]) -> list[XY]:
    """Outline as float pairs (not closed)."""
    return [(float(p.x), float(p.y)) for p in points]


def signed_area(pts: Sequence[XY]) -> float:
    """Shoelace area; positive when the ring turns counter-clockwise in (x, y)."""
    return sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, [*pts[1:], pts[0]], strict=True)) / 2


def point_in_ring(x: float, y: float, pts: Sequence[XY]) -> bool:
    """Even-odd ray cast (same test as polylabel)."""
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        (ax, ay), (bx, by) = pts[i], pts[j]
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
        j = i
    return inside


def room_at(rooms: Iterable[Room], x: float, y: float) -> Room | None:
    """First room (by id) whose outline contains the point."""
    for room in sorted(rooms, key=lambda r: r.id):
        if point_in_ring(x, y, ring(room.outline)):
            return room
    return None


@dataclass(frozen=True)
class Frame:
    """Wall axis: origin at `centreline.start`, unit `d` along it, `n` = (-dy, dx) to its left."""

    ox: float
    oy: float
    dx: float
    dy: float
    length: float

    @property
    def nx(self) -> float:
        """Left normal x."""
        return -self.dy

    @property
    def ny(self) -> float:
        """Left normal y."""
        return self.dx

    def local(self, x: float, y: float) -> XY:
        """(along, across) coordinates of a point."""
        rx, ry = x - self.ox, y - self.oy
        return rx * self.dx + ry * self.dy, rx * self.nx + ry * self.ny

    def at(self, along: float, across: float = 0.0) -> XY:
        """World point from local coordinates."""
        return self.ox + self.dx * along + self.nx * across, self.oy + self.dy * along + self.ny * across


def frame(wall: Wall) -> Frame:
    """Axis of a wall's centreline (`Segment` guarantees a non-zero length)."""
    s, e = wall.centreline.start, wall.centreline.end
    length = math.hypot(e.x - s.x, e.y - s.y)
    return Frame(float(s.x), float(s.y), (e.x - s.x) / length, (e.y - s.y) / length, length)


def clip_to_band(f: Frame, a: XY, b: XY, half_width: float, margin: float) -> Interval | None:
    """Parameter range t in [0, 1] of segment a-b inside the wall rectangle grown by `margin` (Liang-Barsky)."""
    (au, av), (bu, bv) = f.local(*a), f.local(*b)
    t0, t1 = 0.0, 1.0
    for p, q in (
        (-(bu - au), au - (-margin)),
        (bu - au, (f.length + margin) - au),
        (-(bv - av), av + half_width),
        (bv - av, half_width - av),
    ):
        if p == 0:
            if q < 0:
                return None
            continue
        r = q / p
        if p < 0:
            t0 = max(t0, r)
        else:
            t1 = min(t1, r)
        if t0 > t1:
            return None
    return t0, t1


def union(intervals: Iterable[Interval]) -> list[Interval]:
    """Merge overlapping intervals, sorted."""
    out: list[Interval] = []
    for lo, hi in sorted(intervals):
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def subtract(base: Iterable[Interval], cuts: Iterable[Interval]) -> list[Interval]:
    """`base` minus `cuts`, both unions of intervals."""
    out = union(base)
    for clo, chi in union(cuts):
        nxt: list[Interval] = []
        for lo, hi in out:
            if chi <= lo or clo >= hi:
                nxt.append((lo, hi))
                continue
            if clo > lo:
                nxt.append((lo, clo))
            if chi < hi:
                nxt.append((chi, hi))
        out = nxt
    return out


def _seg_dist_sq(px: float, py: float, a: XY, b: XY) -> float:
    """Squared distance from a point to segment a-b."""
    x, y = a
    dx, dy = b[0] - x, b[1] - y
    if dx != 0 or dy != 0:
        t = ((px - x) * dx + (py - y) * dy) / (dx * dx + dy * dy)
        if t > 1:
            x, y = b
        elif t > 0:
            x += dx * t
            y += dy * t
    dx, dy = px - x, py - y
    return dx * dx + dy * dy


def _signed_dist(x: float, y: float, pts: Sequence[XY]) -> float:
    """Distance to the outline, positive inside."""
    best = min(_seg_dist_sq(x, y, pts[i], pts[i - 1]) for i in range(len(pts)))
    if best == 0:
        return 0.0
    return math.sqrt(best) if point_in_ring(x, y, pts) else -math.sqrt(best)


@dataclass(frozen=True)
class _Cell:
    """Square cell of half-size `h`; `best` bounds the distance any point inside can reach."""

    x: float
    y: float
    h: float
    d: float

    @property
    def best(self) -> float:
        """Upper bound of the distance inside the cell."""
        return self.d + self.h * math.sqrt(2)


def polylabel(pts: Sequence[XY], precision: float) -> XY:
    """Pole of inaccessibility of a simple ring (mapbox/polylabel v2)."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    min_x, min_y, max_x, max_y = min(xs), min(ys), max(xs), max(ys)
    width, height = max_x - min_x, max_y - min_y
    cell_size = max(precision, min(width, height))
    if cell_size == precision:
        return min_x, min_y

    def cell(x: float, y: float, h: float) -> _Cell:
        """Cell with its distance."""
        return _Cell(x, y, h, _signed_dist(x, y, pts))

    area2 = cx = cy = 0.0
    for i in range(len(pts)):
        a, b = pts[i], pts[i - 1]
        f = a[0] * b[1] - b[0] * a[1]
        cx += (a[0] + b[0]) * f
        cy += (a[1] + b[1]) * f
        area2 += f * 3
    bbox = cell(min_x + width / 2, min_y + height / 2, 0)
    centroid = cell(cx / area2, cy / area2, 0) if area2 else cell(pts[0][0], pts[0][1], 0)
    best = bbox if bbox.d > centroid.d else centroid
    queue: list[tuple[float, int, _Cell]] = []
    order = count()

    def probe(x: float, y: float, h: float) -> None:
        """Keep the cell if it can still beat the best by more than `precision`."""
        nonlocal best
        c = cell(x, y, h)
        if c.best > best.d + precision:
            heapq.heappush(queue, (-c.best, next(order), c))
        if c.d > best.d:
            best = c

    h = cell_size / 2
    x = min_x
    while x < max_x:
        y = min_y
        while y < max_y:
            probe(x + h, y + h, h)
            y += cell_size
        x += cell_size
    while queue:
        _, _, c = heapq.heappop(queue)
        if c.best - best.d <= precision:
            break
        h = c.h / 2
        probe(c.x - h, c.y - h, h)
        probe(c.x + h, c.y - h, h)
        probe(c.x - h, c.y + h, h)
        probe(c.x + h, c.y + h, h)
    return best.x, best.y
