"""F2/F3 kitchen runs on `kitchenCabinet` boxes (KET-QUA-LOI §2-3).

F2 merges duplicate boxes (same kind, same room, overlap ≥ `dupOverlapPct` of the smaller one) into one run built
from the larger box, then gates it: room role in `roomUsages`, and the back side within `wallMaxMm` of a wall over
≥ `wallCoverPct` of its length (no floating cabinets). A run whose footprint would add a collision is skipped.
F3 builds the upper run on the back line of each effective F2 run unless an opening is on that stretch of wall.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

from packages.domain.spatial.model import Furniture, Point
from packages.domain.suggestions import messages
from packages.domain.suggestions.model import CabinetRunParams, HostRef, params_dict
from packages.domain.suggestions.rules.base import Draft, Note, RuleInput, RuleOutput
from packages.domain.suggestions.rules.geom import XY, js_round
from packages.domain.suggestions.rules.placement import (
    Obstacle,
    Site,
    doc_collisions,
    rect,
    rule_collisions,
    run_rect,
)
from packages.domain.suggestions.ruleset import RuleSpec


def sides(poly: Polygon) -> list[tuple[XY, XY]]:
    """The four sides of a rectangle, counter-clockwise in (x, y): the inside is left of each side."""
    pts: list[XY] = [(x, y) for x, y in orient(poly, 1.0).exterior.coords][:4]
    return [(pts[i], pts[(i + 1) % 4]) for i in range(4)]


def wall_distance(site: Site, a: XY, b: XY) -> float:
    """Distance from a side's midpoint to the nearest wall (infinite without walls)."""
    if site.walls.is_empty:
        return math.inf
    mid = LineString([a, b]).interpolate(0.5, normalized=True)
    return float(mid.distance(site.walls))


def back_side(site: Site, poly: Polygon, long: bool = True) -> tuple[XY, XY]:
    """Long (or short) side of a box nearest a wall, oriented so the box lies to its left."""
    four = sides(poly)
    pair = sorted(four, key=lambda s: -math.dist(*s) if long else math.dist(*s))[:2]
    return min(pair, key=lambda s: (wall_distance(site, *s), four.index(s)))


def _box(f: Furniture) -> Polygon:
    """Footprint of a document box."""
    bb = f.bounding_box
    return rect((f.centre.x, f.centre.y), bb.max.x - bb.min.x, bb.max.y - bb.min.y, f.rotation_deg)


def _gate(site: Site, f: Furniture, box: Polygon, rid: str | None, p: Mapping[str, Any]) -> str | None:
    """Why a box gets no run (None = it does)."""
    if f.model is not None:
        return messages.HAS_MODEL
    usage = site.rooms[rid].usage if rid else None
    if usage not in p["roomUsages"]:
        return messages.run_wrong_room(usage)
    lens = sorted(math.dist(*s) for s in sides(box))
    if lens[-1] < p["minRunMm"]:
        return messages.run_too_short(js_round(lens[-1]), p["minRunMm"])
    if lens[0] > p["maxBoxDepthMm"]:
        return messages.run_too_deep(js_round(lens[0]), p["maxBoxDepthMm"])
    a, b = back_side(site, box)
    cover = LineString([a, b]).intersection(site.walls.buffer(p["wallMaxMm"])).length / math.dist(a, b)
    if cover * 100 < p["wallCoverPct"]:
        return messages.run_off_wall(math.floor(cover * 100), p["wallMaxMm"], p["wallCoverPct"])
    return None


def f2_base_run(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """F2: one parametric base run per kitchen box that passes the gates; duplicates fold into the larger box."""
    p = spec.params
    out = RuleOutput()
    site = Site(inp.layer, inp.items)
    cabs = [
        f for f in sorted(inp.layer.furniture, key=lambda x: x.id) if f.kind == "kitchenCabinet" and not f.suggested
    ]
    boxes = {f.id: _box(f) for f in cabs}
    rooms = {f.id: f.room_id if f.room_id in site.rooms else site.room_of(f.centre.x, f.centre.y) for f in cabs}
    dup_of: dict[str, tuple[str, float]] = {}
    by_size = sorted(cabs, key=lambda f: (-boxes[f.id].area, f.id))
    for i, big in enumerate(by_size):
        if big.id in dup_of:
            continue
        for small in by_size[i + 1 :]:
            if small.id in dup_of or rooms[small.id] != rooms[big.id] or boxes[small.id].area == 0:
                continue
            ratio = boxes[big.id].intersection(boxes[small.id]).area / boxes[small.id].area
            if ratio * 100 >= p["dupOverlapPct"]:
                dup_of[small.id] = (big.id, ratio)
    doc = doc_collisions(site)
    for f in cabs:
        if f.id in dup_of:
            continue
        merged = sorted(k for k, (big, _) in dup_of.items() if big == f.id)
        why = _gate(site, f, boxes[f.id], rooms[f.id], p)
        sid = f"DRESS-F2-{f.id}"
        if why is None:
            a, b = back_side(site, boxes[f.id])
            start, end = Point(x=js_round(a[0]), y=js_round(a[1])), Point(x=js_round(b[0]), y=js_round(b[1]))
            poly = run_rect((start.x, start.y), (end.x, end.y), p["depthMm"])
            ob = Obstacle(sid, poly, p["heightMm"], rooms[f.id], doc=False, host=f.id, group=frozenset([f.id, *merged]))
            hits = rule_collisions(site, ob, site.obstacles, doc)
            if hits:
                why = messages.collides("dãy tủ", sorted({t for t, _, _ in hits}))
        if why is not None:
            out.notes.extend(Note("F2", host, "skipped", why) for host in (f.id, *merged))
            continue
        site.obstacles.append(ob)
        for k in merged:
            out.notes.append(Note("F2", k, "degraded", messages.duplicate_box(math.floor(dup_of[k][1] * 100), f.id)))
        params = CabinetRunParams(
            slot="base",
            start=start,
            end=end,
            length_mm=js_round(math.dist((start.x, start.y), (end.x, end.y))),
            depth_mm=p["depthMm"],
            bottom_mm=0,
            top_mm=p["heightMm"],
            module_mm=p["moduleMm"],
            merged_ids=tuple(merged) or None,
            degraded_vi=messages.merged_boxes(merged) if merged else None,
        )
        out.drafts.append(Draft(sid, "cabinetRun", HostRef(furniture=f.id), params_dict(params)))
    return out


def f3_upper_run(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """F3: upper run on the back line of every effective F2 run, skipped where it would cover an opening."""
    p = spec.params
    out = RuleOutput()
    site = Site(inp.layer)
    for s in sorted(inp.items, key=lambda x: x.id):
        if s.rule != "F2":
            continue
        base: Mapping[str, Any] = s.params
        a, b = base["start"], base["end"]
        upper = run_rect((a["x"], a["y"]), (b["x"], b["y"]), p["depthMm"])
        host = s.host_ref.furniture or s.id
        if upper.buffer(p["openingClearMm"]).intersects(site.openings):
            out.notes.append(Note("F3", host, "skipped", messages.UPPER_BLOCKED))
            continue
        params = CabinetRunParams(
            slot="upper",
            start=Point.model_validate(a),
            end=Point.model_validate(b),
            length_mm=base["lengthMm"],
            depth_mm=p["depthMm"],
            bottom_mm=p["bottomMm"],
            top_mm=p["topMm"],
            module_mm=p["moduleMm"],
        )
        out.drafts.append(
            Draft(
                f"DRESS-F3-{host}",
                "cabinetRun",
                HostRef(furniture=s.host_ref.furniture, suggestion=s.id),
                params_dict(params),
            )
        )
    return out
