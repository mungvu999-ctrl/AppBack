"""Footprints, wall-backed placement and the collision classifier shared by the furniture rules.

Port of the B2 experiment (`F:/runs/abc/rules`, KET-QUA-LOI §4-6) to layer millimetres (y down).

Frame: a model's footprint is its pack bbox (`data/model_bbox.json`; width along local x, depth along local y),
turned by `rotationDeg` about its centre with the standard rotation `(x cos - y sin, x sin + y cos)`. The model front
faces local +y, so an item facing direction n has `rotationDeg = atan2(-n.x, n.y)`. This is the compiler's
convention (`rotZ = -rotationDeg` in its Y-up frame) and the document's (`boundingBox` = footprint before rotation).

Collisions: a footprint shrunk by `PEN_MM` (touching is not a collision) is tested against walls, its room, door
clearance (`DOOR_CLEAR_MM` each side), window clearance (only when the item is taller than the sill) and every other
floor-standing footprint; overlaps under `AREA_MM2` are ignored. `doc` = already present between document boxes, only
logged; `rule` = involves a suggestion. Rules never leave a `rule` collision: they slide along the walls or skip.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache, cached_property
from itertools import pairwise
from pathlib import Path
from typing import Any, Final, Literal

import shapely
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.prepared import PreparedGeometry, prep

from packages.domain.spatial.model import SpatialLayer
from packages.domain.suggestions.model import Suggestion
from packages.domain.suggestions.rules.geom import XY, frame, js_round, ring, room_at

BBOX_PATH: Final = Path(__file__).parents[1] / "data" / "model_bbox.json"
FINISH_FLOOR_MM: Final = 20
"""Finish floor above the slab in every room (compiler `FINISH_FLOOR_M`); `liftMm` counts from it."""
FLOOR_ITEM_MAX_LIFT_MM: Final = 300
"""Items lifted less than this stand on the floor and take part in collisions."""
DOOR_CLEAR_MM: Final = 800
WINDOW_CLEAR_MM: Final = 400
WINDOW_SILL_SLACK_MM: Final = 50
PEN_MM: Final = 20
AREA_MM2: Final = 2000
WALL_PROBE_MM: Final = 20
WALL_BUFFER_MM: Final = 60
MIN_EDGE_MM: Final = 300
WALL_CLEAR_MM: Final = 10
SOLID_BACK_MM: Final = 150
DEFAULT_HEIGHT_MM: Final[Mapping[str, int]] = {"wardrobe": 2000}
"""Document boxes carry no height most of the time. ponytail: kind table; read `heightMm` once AI fills it."""
DEFAULT_ITEM_HEIGHT_MM: Final = 800

CollisionType = Literal["wall", "outside-room", "opening-clearance", "window", "item-item"]


@cache
def _bboxes() -> dict[str, dict[str, list[float]]]:
    """Model id → bbox in metres (Blender Z-up, front towards -y), see the file's `source`."""
    models: dict[str, dict[str, list[float]]] = json.loads(BBOX_PATH.read_text(encoding="utf-8"))["models"]
    return models


def known_model(model_id: str) -> bool:
    """The bbox table has this model."""
    return model_id in _bboxes()


def model_size(model_id: str) -> tuple[int, int, int]:
    """(width, depth, height) of a model's bbox in mm."""
    bb = _bboxes()[model_id]
    w, d, h = (js_round((bb["max"][i] - bb["min"][i]) * 1000) for i in range(3))
    return w, d, h


def model_origin(
    model_id: str, centre: XY, rotation_deg: float, lift_mm: float, scale: float = 1.0
) -> tuple[float, float, float]:
    """Model origin (layer mm, z above the slab) that puts its bbox centre on `centre` and its bbox bottom on the
    finish floor + `lift_mm` (F0: pack origins are not always at the bottom centre)."""
    bb = _bboxes()[model_id]
    cx = (bb["min"][0] + bb["max"][0]) / 2 * 1000 * scale
    cy = -(bb["min"][1] + bb["max"][1]) / 2 * 1000 * scale  # Blender +y is local -y here (front towards local +y)
    th = math.radians(rotation_deg)
    c, s = math.cos(th), math.sin(th)
    return (
        round(centre[0] - (cx * c - cy * s), 1),
        round(centre[1] - (cx * s + cy * c), 1),
        round(FINISH_FLOOR_MM + lift_mm - bb["min"][2] * 1000 * scale, 1),
    )


def rect(centre: XY, w: float, d: float, rotation_deg: float) -> Polygon:
    """Footprint of width `w` (local x) and depth `d` (local y) at `centre`, turned by `rotation_deg`."""
    th = math.radians(rotation_deg)
    c, s = math.cos(th), math.sin(th)
    corners = ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2))
    return Polygon([(centre[0] + x * c - y * s, centre[1] + x * s + y * c) for x, y in corners])


def facing(nx: float, ny: float) -> float:
    """`rotationDeg` in [0, 360) that turns the model front (local +y) towards (nx, ny)."""
    return round(math.degrees(math.atan2(-nx, ny)) % 360, 3) % 360


def front(rotation_deg: float) -> XY:
    """Unit direction the model front faces."""
    th = math.radians(rotation_deg)
    return -math.sin(th), math.cos(th)


def run_rect(start: XY, end: XY, depth: float) -> Polygon:
    """Cabinet run footprint: back line `start`→`end`, `depth` towards its left normal (-dy, dx)."""
    length = math.dist(start, end)
    nx, ny = -(end[1] - start[1]) / length, (end[0] - start[0]) / length
    return Polygon(
        [start, end, (end[0] + nx * depth, end[1] + ny * depth), (start[0] + nx * depth, start[1] + ny * depth)]
    )


def item_rect(params: Mapping[str, Any]) -> Polygon:
    """Footprint of `item` params."""
    c, size = params["centre"], params["sizeMm"]
    return rect((c["x"], c["y"]), size[0], size[1], params["rotationDeg"])


def polygons_of(geom: BaseGeometry) -> list[Polygon]:
    """Polygons of any geometry, in shapely's order."""
    if isinstance(geom, Polygon):
        return [] if geom.is_empty else [geom]
    return [g for g in getattr(geom, "geoms", ()) if isinstance(g, Polygon) and not g.is_empty]


def _lines(geom: BaseGeometry) -> Iterator[LineString]:
    """Line pieces of an intersection result."""
    if isinstance(geom, LineString):
        if not geom.is_empty:
            yield geom
        return
    for g in getattr(geom, "geoms", ()):
        yield from _lines(g)


@dataclass(frozen=True)
class Edge:
    """Wall-backed stretch of a room edge: start point, unit direction `t`, inward normal `n`, length."""

    ax: float
    ay: float
    tx: float
    ty: float
    nx: float
    ny: float
    length: float

    def at(self, along: float, inward: float = 0.0) -> XY:
        """Point `along` the edge, `inward` into the room."""
        return self.ax + self.tx * along + self.nx * inward, self.ay + self.ty * along + self.ny * inward


@dataclass(frozen=True)
class Obstacle:
    """A floor-standing footprint. `host` = the document box whose `doc` collisions it inherits (F2 run → its box);
    `group` = keys never tested against it (a run, its box and the duplicate boxes folded into it)."""

    key: str
    poly: Polygon
    top: float
    room: str | None
    doc: bool
    host: str | None = None
    group: frozenset[str] = field(default_factory=frozenset)

    @cached_property
    def core(self) -> BaseGeometry:
        """Footprint shrunk by `PEN_MM` (touching is not a collision), computed once."""
        return self.poly.buffer(-PEN_MM, join_style="mitre")


@dataclass(frozen=True)
class CollisionNote:
    """One collision for the writer's log: `doc` ones are the document's own, `rule` ones a rule bug."""

    label: Literal["doc", "rule"]
    type: CollisionType
    item: str
    other: str | None
    area_mm2: int


class LayerGeometry:
    """What every placement rule of one `reconcile` call shares: wall union, room finish areas, opening zones,
    document obstacles, plus lazily cached wall-backed edges, room containment tests and document collisions."""

    def __init__(self, layer: SpatialLayer) -> None:
        """Build once per layer (see `layer_geometry`)."""
        walls = {w.id: w for w in layer.walls}
        self.walls = shapely.union_all([_wall_rect(w) for _, w in sorted(walls.items())])
        self.wall_buf = self.walls.buffer(WALL_BUFFER_MM)
        self.rooms = {r.id: r for r in sorted(layer.rooms, key=lambda r: r.id)}
        self.finish: dict[str, BaseGeometry] = {
            rid: Polygon(ring(r.outline)).buffer(0).difference(self.walls) for rid, r in self.rooms.items()
        }
        fps: list[Polygon] = []
        doors: list[Polygon] = []
        self.windows: list[tuple[int, Polygon]] = []
        for o in sorted(layer.openings, key=lambda o: o.id):
            wall = walls.get(o.wall_id)
            if wall is None:
                continue
            f = frame(wall)
            c = f.at(o.offset_mm + o.width_mm / 2)
            angle = math.degrees(math.atan2(f.dy, f.dx))
            fps.append(rect(c, o.width_mm, wall.thickness_mm + PEN_MM, angle))
            clear = DOOR_CLEAR_MM if o.kind == "door" else WINDOW_CLEAR_MM
            zone = rect(c, o.width_mm + 100, wall.thickness_mm + 2 * clear, angle)
            if o.kind == "door":
                doors.append(zone)
            else:
                self.windows.append((o.sill_height_mm, zone))
        self.openings = shapely.union_all(fps)
        self.doors = shapely.union_all(doors)
        docs: list[Obstacle] = []
        for fur in sorted(layer.furniture, key=lambda x: x.id):
            if fur.suggested or fur.kind == "stair" or (fur.lift_mm or 0) >= FLOOR_ITEM_MAX_LIFT_MM:
                continue
            bb = fur.bounding_box
            poly = rect((fur.centre.x, fur.centre.y), bb.max.x - bb.min.x, bb.max.y - bb.min.y, fur.rotation_deg)
            height = fur.height_mm or DEFAULT_HEIGHT_MM.get(fur.kind, DEFAULT_ITEM_HEIGHT_MM)
            rid = fur.room_id if fur.room_id in self.rooms else self.room_of(fur.centre.x, fur.centre.y)
            docs.append(Obstacle(fur.id, poly, (fur.lift_mm or 0) + height, rid, doc=True))
        self.docs = tuple(docs)
        # Mutable caches, shared by reference with every `Site` built on this geometry.
        self._edges: dict[str, list[Edge]] = {}
        self._inside: dict[str, PreparedGeometry[BaseGeometry]] = {}
        self._grown: dict[float, BaseGeometry] = {}
        self._doc: list[dict[tuple[str, CollisionType, str], float]] = []

    def room_of(self, x: float, y: float) -> str | None:
        """Room whose outline contains the point."""
        room = room_at(self.rooms.values(), x, y)
        return room.id if room else None

    def grown_docs(self, gap: float) -> BaseGeometry:
        """Union of the document obstacles grown by `gap` (cached per gap)."""
        if gap not in self._grown:
            self._grown[gap] = shapely.union_all([o.poly.buffer(gap, join_style="mitre") for o in self.docs])
        return self._grown[gap]

    def inside(self, room_id: str) -> PreparedGeometry[BaseGeometry]:
        """Prepared room finish area (grown 5 mm) for containment tests."""
        if room_id not in self._inside:
            self._inside[room_id] = prep(self.finish[room_id].buffer(5))
        return self._inside[room_id]

    def wall_edges(self, room_id: str) -> list[Edge]:
        """Parts of the room's finish edges with a wall right behind them, longest first (cached per room).

        Only the stretch that really has a wall counts (KET-QUA-LOI §6: a long open edge with a short stub at its
        middle used to pass as a full wall). Pieces under `MIN_EDGE_MM` are dropped.
        """
        if room_id in self._edges:
            return self._edges[room_id]
        out: list[Edge] = []
        for poly in polygons_of(self.finish[room_id]):
            poly = orient(poly, 1.0)
            for rng in (poly.exterior, *poly.interiors):
                pts = list(rng.coords)
                for a, b in pairwise(pts):
                    length = math.dist(a, b)
                    if length < MIN_EDGE_MM:
                        continue
                    tx, ty = (b[0] - a[0]) / length, (b[1] - a[1]) / length
                    nx, ny = -ty, tx
                    off = WALL_PROBE_MM
                    probe = LineString([(a[0] - nx * off, a[1] - ny * off), (b[0] - nx * off, b[1] - ny * off)])
                    for seg in _lines(probe.intersection(self.wall_buf)):
                        if seg.length < MIN_EDGE_MM:
                            continue
                        ends = (seg.coords[0], seg.coords[-1])
                        s0, s1 = sorted((q[0] + nx * off - a[0]) * tx + (q[1] + ny * off - a[1]) * ty for q in ends)
                        out.append(Edge(a[0] + tx * s0, a[1] + ty * s0, tx, ty, nx, ny, s1 - s0))
        self._edges[room_id] = sorted(out, key=lambda e: (-round(e.length, 3), round(e.ax, 3), round(e.ay, 3)))
        return self._edges[room_id]

    def hits(
        self, poly: Polygon, room_id: str | None, top: float, core: BaseGeometry | None = None
    ) -> list[tuple[CollisionType, float]]:
        """Collision types of one footprint (or its precomputed `core`) against walls, its room, door and window
        clearance."""
        core = poly.buffer(-PEN_MM, join_style="mitre") if core is None else core
        if core.is_empty:
            return []
        out: list[tuple[CollisionType, float]] = []
        area = core.intersection(self.walls).area
        if area > AREA_MM2:
            out.append(("wall", area))
        inside = self.finish[room_id].buffer(10) if room_id in self.finish else Polygon()
        area = core.difference(inside).difference(self.walls).area
        if area > AREA_MM2:
            out.append(("outside-room", area))
        area = core.intersection(self.doors).area
        if area > AREA_MM2:
            out.append(("opening-clearance", area))
        area = sum(core.intersection(z).area for sill, z in self.windows if top > sill + WINDOW_SILL_SLACK_MM)
        if area > AREA_MM2:
            out.append(("window", area))
        return out


_LAST: list[tuple[SpatialLayer, LayerGeometry]] = []
"""One-slot cache: every rule of a `reconcile` call gets the same layer object, so the geometry is built once."""


def layer_geometry(layer: SpatialLayer) -> LayerGeometry:
    """Shared geometry of `layer`, rebuilt only when a different layer object comes in (layers are frozen)."""
    if not _LAST or _LAST[0][0] is not layer:
        _LAST[:] = [(layer, LayerGeometry(layer))]
    return _LAST[0][1]


class Site(LayerGeometry):
    """Per-rule view: the shared layer geometry plus the obstacles of earlier suggestions (and of items the rule
    places itself, appended as it goes)."""

    def __init__(self, layer: SpatialLayer, items: Sequence[Suggestion] = ()) -> None:
        """Shared geometry of the layer (caches included, by reference), own obstacle list."""
        self.__dict__.update(layer_geometry(layer).__dict__)
        self.obstacles: list[Obstacle] = list(self.docs)
        for s in sorted(items, key=lambda x: x.id):
            ob = suggestion_obstacle(self, s)
            if ob is not None:
                self.obstacles.append(ob)

    def blocked(self, top: float, exclude: Iterable[str] = (), gap: float = 0.0) -> PreparedGeometry[BaseGeometry]:
        """Everything a new item of height `top` must not touch: other obstacles (grown by `gap`), door clearance,
        and window clearance where the item would rise above the sill."""
        skip = set(exclude)
        if skip & {o.key for o in self.docs}:
            parts: list[BaseGeometry] = [
                o.poly.buffer(gap, join_style="mitre") for o in self.obstacles if o.key not in skip
            ]
        else:
            parts = [self.grown_docs(gap)]
            parts.extend(
                o.poly.buffer(gap, join_style="mitre") for o in self.obstacles[len(self.docs) :] if o.key not in skip
            )
        parts.append(self.doors)
        parts.extend(z for sill, z in self.windows if top > sill + WINDOW_SILL_SLACK_MM)
        return prep(shapely.union_all(parts))

    def fits(self, room_id: str, poly: Polygon, top: float, exclude: Iterable[str] = (), gap: float = 0.0) -> bool:
        """`poly` lies in the room's finish area and touches nothing `blocked` names."""
        return bool(self.inside(room_id).contains(poly)) and not self.blocked(top, exclude, gap).intersects(poly)


def _wall_rect(wall: Any) -> Polygon:
    """Wall body; ponytail: curved walls as their chord rectangle, fine for clearance tests."""
    f = frame(wall)
    return rect(f.at(f.length / 2), f.length, wall.thickness_mm, math.degrees(math.atan2(f.dy, f.dx)))


def suggestion_obstacle(site: Site, s: Suggestion) -> Obstacle | None:
    """Floor-standing footprint of an effective suggestion: items below `FLOOR_ITEM_MAX_LIFT_MM`, base runs."""
    p: Mapping[str, Any] = s.params
    if s.kind == "item" and p.get("liftMm", 0) < FLOOR_ITEM_MAX_LIFT_MM:
        poly = item_rect(p)
        c = poly.centroid
        rid = s.host_ref.room or site.room_of(c.x, c.y)
        return Obstacle(s.id, poly, p.get("liftMm", 0) + p["sizeMm"][2], rid, doc=False)
    if s.kind == "cabinetRun" and p["slot"] == "base":
        a, b = p["start"], p["end"]
        poly = run_rect((a["x"], a["y"]), (b["x"], b["y"]), p["depthMm"])
        host = s.host_ref.furniture
        group = frozenset([host or "", *p.get("mergedIds", ())])
        c = poly.centroid
        return Obstacle(s.id, poly, p["topMm"], site.room_of(c.x, c.y), doc=False, host=host, group=group)
    return None


def _overlap(a: Obstacle, b: Obstacle) -> float:
    """Overlap area of two footprints after shrinking both by `PEN_MM` (0 when their bounds are apart)."""
    ax0, ay0, ax1, ay1 = a.poly.bounds
    bx0, by0, bx1, by1 = b.poly.bounds
    if ax0 > bx1 or bx0 > ax1 or ay0 > by1 or by0 > ay1:
        return 0.0
    return float(a.core.intersection(b.core).area)


def doc_collisions(site: Site) -> dict[tuple[str, CollisionType, str], float]:
    """Collisions already present between document boxes: (id, type, other id or '') → area (pairs both ways).
    Computed once per layer geometry."""
    if site._doc:
        return site._doc[0]
    docs = site.docs
    out: dict[tuple[str, CollisionType, str], float] = {}
    for o in docs:
        for typ, area in site.hits(o.poly, o.room, o.top, o.core):
            out[(o.key, typ, "")] = area
    for i, a in enumerate(docs):
        for b in docs[i + 1 :]:
            area = _overlap(a, b)
            if area > AREA_MM2:
                out[(a.key, "item-item", b.key)] = out[(b.key, "item-item", a.key)] = area
    site._doc.append(out)
    return out


def rule_collisions(
    site: Site, ob: Obstacle, others: Iterable[Obstacle], doc: Mapping[tuple[str, CollisionType, str], float]
) -> list[tuple[CollisionType, str | None, float]]:
    """Collisions a suggestion's footprint adds: (type, other key, area). What its host box already had is `doc`."""
    out: list[tuple[CollisionType, str | None, float]] = [
        (typ, None, area)
        for typ, area in site.hits(ob.poly, ob.room, ob.top, ob.core)
        if (ob.host or "", typ, "") not in doc
    ]
    mine = ob.host or ob.key
    for o in others:
        if o.key == ob.key or o.key in ob.group or ob.key in o.group:
            continue
        area = _overlap(ob, o)
        if area > AREA_MM2 and (mine, "item-item", o.host or o.key) not in doc:
            out.append(("item-item", o.key, area))
    return out


def check_collisions(layer: SpatialLayer, suggestions: Sequence[Suggestion]) -> list[CollisionNote]:
    """Every collision on the floor, labelled `doc` (between document boxes) or `rule` (involves an effective
    suggestion). The writer logs them; nothing in the document changes. Pairs appear once, sorted."""
    site = Site(layer, [s for s in suggestions if s.is_effective()])
    notes: list[CollisionNote] = []
    doc = doc_collisions(site)
    for (key, typ, partner), area in sorted(doc.items()):
        if typ != "item-item" or key < partner:
            notes.append(CollisionNote("doc", typ, key, partner or None, js_round(area)))
    docs = [o for o in site.obstacles if o.doc]
    mine = [o for o in site.obstacles if not o.doc]
    for i, ob in enumerate(mine):
        for typ, other, area in rule_collisions(site, ob, [*docs, *mine[i + 1 :]], doc):
            notes.append(CollisionNote("rule", typ, ob.key, other, js_round(area)))
    return sorted(notes, key=lambda n: (n.label, n.item, n.type, n.other or ""))


@dataclass(frozen=True)
class Placement:
    """Where `place` put a model: footprint centre, rotation, uniform scale, placed bbox (mm) and footprint."""

    centre: XY
    rotation_deg: float
    scale: float
    size: tuple[int, int, int]
    poly: Polygon


def place(
    site: Site,
    room_id: str,
    model_id: str,
    *,
    prefer: Literal["centre", "ends"],
    solid: bool,
    gap: float,
    solid_margin: float,
    scale_tol_pct: float,
    step: float,
) -> Placement | None:
    """Wall-backed placement: back against a wall-backed edge (longest first), front into the room, sliding along it
    in `step` mm from its middle (`centre`) or from its ends (`ends`, i.e. corners) until the footprint fits.

    `solid` items (bed, wardrobe) need a wall stretch ≥ width + `solid_margin` and no opening within
    `SOLID_BACK_MM` of their back. Scale tries 1, then -tol/2, then -tol (B2 F4 clamp); never smaller: `None`.
    """
    w, d, h = model_size(model_id)
    room = site.inside(room_id)
    blocked = site.blocked(h, gap=gap)
    openings = prep(site.openings)
    edges = site.wall_edges(room_id)
    for scale in (1.0, 1 - scale_tol_pct / 200, 1 - scale_tol_pct / 100):
        sw, sd = w * scale, d * scale
        for e in edges:
            if e.length < sw + (solid_margin if solid else 0.0):
                continue
            raw = [sw / 2 + step * i for i in range(int((e.length - sw) // step) + 1)]
            if prefer == "centre":
                spots = sorted((abs(s - e.length / 2), s) for s in raw)
            else:
                spots = sorted((min(s - sw / 2, e.length - sw / 2 - s), s) for s in raw)
            rot = facing(e.nx, e.ny)
            for _, s in spots:
                fp = rect(e.at(s, sd / 2 + WALL_CLEAR_MM), sw, sd, rot)
                if not room.contains(fp) or blocked.intersects(fp):
                    continue
                if solid and openings.intersects(
                    LineString([e.at(s - sw / 2), e.at(s + sw / 2)]).buffer(SOLID_BACK_MM)
                ):
                    continue
                size = (js_round(w * scale), js_round(d * scale), js_round(h * scale))
                return Placement(e.at(s, sd / 2 + WALL_CLEAR_MM), rot, scale, size, fp)
    return None
