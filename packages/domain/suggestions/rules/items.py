"""Furnishing rules: P1 primary items, P2 bedside tables, P3 coffee table, D1 rug, D3 vase, D4 picture, D5 plant.

All placement goes through `rules.placement` (wall-backed, collision-free, scale clamp ±`scaleTolPct`); a host that
gets nothing leaves a note with the reason. Hosts placed by an earlier rule are chained by `hostRef.suggestion` (P2 on
a P1 bed, D3 on a P2 table); their ids drop the `DRESS-` prefix inside the new id.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from shapely.geometry import Polygon

from packages.domain.spatial.model import Point, SpatialLayer
from packages.domain.suggestions import messages
from packages.domain.suggestions.model import HostRef, ItemParams, RugParams, params_dict
from packages.domain.suggestions.rules.base import Draft, Note, RuleInput, RuleOutput
from packages.domain.suggestions.rules.furniture import missing_primary_slots
from packages.domain.suggestions.rules.geom import XY, js_round
from packages.domain.suggestions.rules.kitchen import back_side, sides, wall_distance
from packages.domain.suggestions.rules.placement import (
    Obstacle,
    Placement,
    Site,
    facing,
    front,
    item_rect,
    model_size,
    place,
    rect,
)
from packages.domain.suggestions.ruleset import RuleSpec


def _pt(xy: XY) -> Point:
    """Layer point rounded like `Math.round`."""
    return Point(x=js_round(xy[0]), y=js_round(xy[1]))


def _item(
    sid: str,
    host: HostRef,
    kind: str,
    variant: str | None,
    model_id: str,
    centre: XY,
    size: tuple[int, int, int],
    rot: float,
    lift: int = 0,
) -> Draft:
    """`item` draft."""
    params = ItemParams.model_validate(
        {"furniture_kind": kind, "variant": variant, "model_id": model_id, "centre": _pt(centre), "size_mm": size,
         "rotation_deg": rot, "lift_mm": lift}
    )  # fmt: skip
    return Draft(sid, "item", host, params_dict(params))


def _short(suggestion_id: str) -> str:
    """Host suggestion id inside a chained id (`DRESS-P1-R-…-bed` → `P1-R-…-bed`)."""
    return suggestion_id.removeprefix("DRESS-")


def _placed(sid: str, got: Placement, room_id: str) -> Obstacle:
    """Obstacle for an item a rule just placed (later items of the same run avoid it)."""
    return Obstacle(sid, got.poly, got.size[2], room_id, doc=False)


def _place(site: Site, room_id: str, model_id: str, p: Mapping[str, Any], prefer: str, solid: bool) -> Placement | None:
    """`place` with a rule's parameters."""
    return place(
        site,
        room_id,
        model_id,
        prefer="centre" if prefer == "centre" else "ends",
        solid=solid,
        gap=p["gapMm"],
        solid_margin=p.get("solidMarginMm", 0),
        scale_tol_pct=p["scaleTolPct"],
        step=p["stepMm"],
    )


def _placed_notes(rule: str, sid: str, model_id: str, got: Placement | None, tol: int) -> list[Note]:
    """Skipped (no spot) or degraded (shrunk) notes of a placement."""
    if got is None:
        w, d, _ = model_size(model_id)
        return [Note(rule, sid, "skipped", messages.no_room_for(model_id, w, d, tol))]
    if got.scale < 1:
        return [Note(rule, sid, "degraded", messages.scaled(model_id, js_round(got.scale * 100)))]
    return []


def p1_primary(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """P1: each role slot of a room not furnished by a real item gets its model, placed against a wall."""
    p = spec.params
    out = RuleOutput()
    site = Site(inp.layer, inp.items)
    for room in sorted(inp.layer.rooms, key=lambda r: r.id):
        slots = {s["slot"]: s for s in p["roles"].get(room.usage, [])}
        for name in missing_primary_slots(inp.layer, room, spec):
            slot = slots[name]
            sid = f"DRESS-P1-{room.id}-{name}"
            got = _place(site, room.id, slot["modelId"], p, slot["prefer"], slot["solid"])
            out.notes.extend(_placed_notes("P1", sid, slot["modelId"], got, p["scaleTolPct"]))
            if got is None:
                continue
            site.obstacles.append(_placed(sid, got, room.id))
            out.drafts.append(
                _item(sid, HostRef(room=room.id), slot["kind"], slot["variants"][0], slot["modelId"], got.centre,
                      got.size, got.rotation_deg)
            )  # fmt: skip
    return out


@dataclass(frozen=True)
class _Host:
    """A bed or sofa hosting companions: its hostRef, id part for chained ids, footprint, room, front and depth."""

    ref: HostRef
    key: str
    tag: str
    poly: Polygon
    room: str
    centre: XY
    normal: XY
    depth: float


def _doc_or_item(
    layer: SpatialLayer, site: Site, inp: RuleInput, variant: str, p: Mapping[str, Any]
) -> Iterator[_Host]:
    """Beds (`variant='bed'`) or sofas: document items of that kind and P1 items of that variant, sorted by key."""
    hosts: list[_Host] = []
    for f in layer.furniture:
        if f.suggested:
            continue
        bb = f.bounding_box
        w, d = bb.max.x - bb.min.x, bb.max.y - bb.min.y
        if variant == "bed":
            wanted = f.kind == "bed" or f.variant == "bed"
        else:
            wanted = f.kind == "chair" and (
                f.variant == "sofa" or (max(w, d) >= p["sofaMinLongMm"] and min(w, d) >= p["sofaMinShortMm"])
            )
        rid = f.room_id if f.room_id in site.rooms else site.room_of(f.centre.x, f.centre.y)
        if not wanted or rid is None:
            continue
        poly = rect((f.centre.x, f.centre.y), w, d, f.rotation_deg)
        a, b = back_side(site, poly)
        length = math.dist(a, b)
        n = (-(b[1] - a[1]) / length, (b[0] - a[0]) / length)
        ref = HostRef(furniture=f.id, room=rid)
        hosts.append(_Host(ref, f.id, f.id, poly, rid, (f.centre.x, f.centre.y), n, min(w, d)))
    for s in inp.items:
        q: Mapping[str, Any] = s.params
        if s.rule != "P1" or q.get("variant") != variant or s.host_ref.room is None:
            continue
        c = (q["centre"]["x"], q["centre"]["y"])
        ref = HostRef(suggestion=s.id, room=s.host_ref.room)
        hosts.append(
            _Host(ref, s.id, _short(s.id), item_rect(q), s.host_ref.room, c, front(q["rotationDeg"]), q["sizeMm"][1])
        )
    yield from sorted(hosts, key=lambda h: h.key)


def p2_bedside(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """P2: a bedside table each side of the bed head (the short side nearest a wall), in bedrooms only."""
    p = spec.params
    out = RuleOutput()
    site = Site(inp.layer, inp.items)
    w, d, h = model_size(p["modelId"])
    for bed in _doc_or_item(inp.layer, site, inp, "bed", p):
        if site.rooms[bed.room].usage != "bedroom":
            continue
        four = sides(bed.poly)
        short = sorted(four, key=lambda s: math.dist(*s))[:2]
        a, b = min(short, key=lambda s: (wall_distance(site, *s), four.index(s)))
        head = math.dist(a, b)
        t = ((b[0] - a[0]) / head, (b[1] - a[1]) / head)
        n = (-t[1], t[0])
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        rot = facing(*n)
        for sign, tag in ((-1, "L"), (1, "R")):
            along = sign * (head / 2 + w / 2 + p["gapMm"])
            inward = d / 2 + p["wallGapMm"]
            c = (mid[0] + t[0] * along + n[0] * inward, mid[1] + t[1] * along + n[1] * inward)
            fp = rect(c, w, d, rot)
            sid = f"DRESS-P2-{bed.tag}-{tag}"
            if not site.fits(bed.room, fp, h, exclude=[bed.key], gap=p["clearMm"]):
                out.notes.append(Note("P2", sid, "skipped", messages.NIGHTSTAND_NO_ROOM))
                continue
            site.obstacles.append(Obstacle(sid, fp, h, bed.room, doc=False))
            out.drafts.append(_item(sid, bed.ref, "other", "nightstand", p["modelId"], c, (w, d, h), rot))
    return out


def d1_rug(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """D1: rug under the seating group, `offsetMm` in front of the sofa centre; kept when ≥ `minInsidePct` of it lies
    inside the room shrunk by `insetMm`."""
    p = spec.params
    out = RuleOutput()
    site = Site(inp.layer, inp.items)
    rw, rd = p["sizeMm"]
    for sofa in _doc_or_item(inp.layer, site, inp, "sofa", p):
        c = (sofa.centre[0] + sofa.normal[0] * p["offsetMm"], sofa.centre[1] + sofa.normal[1] * p["offsetMm"])
        rot = facing(*sofa.normal)
        rug = rect(c, rw, rd, rot)
        pct = rug.intersection(site.finish[sofa.room].buffer(-p["insetMm"])).area / rug.area * 100
        sid = f"DRESS-D1-{sofa.tag}"
        if pct < p["minInsidePct"]:
            out.notes.append(Note("D1", sid, "skipped", messages.rug_inside(math.floor(pct), p["minInsidePct"])))
            continue
        params = RugParams(centre=_pt(c), size_mm=(rw, rd), rotation_deg=rot, thickness_mm=p["thicknessMm"])
        out.drafts.append(Draft(sid, "rug", sofa.ref, params_dict(params)))
    return out


def p3_coffee_table(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """P3: coffee table `distanceMm` in front of each sofa; it stands on the D1 rug when that rug is in effect."""
    p = spec.params
    out = RuleOutput()
    site = Site(inp.layer, inp.items)
    w, d, h = model_size(p["modelId"])
    rugs: dict[str, Any] = {s.id: s.params["thicknessMm"] for s in inp.items if s.kind == "rug"}
    for sofa in _doc_or_item(inp.layer, site, inp, "sofa", p):
        ahead = sofa.depth / 2 + p["distanceMm"] + d / 2
        c = (sofa.centre[0] + sofa.normal[0] * ahead, sofa.centre[1] + sofa.normal[1] * ahead)
        rot = facing(*sofa.normal)
        fp = rect(c, w, d, rot)
        lift = rugs.get(f"DRESS-D1-{sofa.tag}", 0)
        sid = f"DRESS-P3-{sofa.tag}"
        if not site.fits(sofa.room, fp, h + lift, exclude=[sofa.key], gap=p["clearMm"]):
            out.notes.append(Note("P3", sid, "skipped", messages.COFFEE_NO_ROOM))
            continue
        out.drafts.append(_item(sid, sofa.ref, "table", "table", p["modelId"], c, (w, d, h), rot, lift))
    return out


def d3_vase(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """D3: a vase on top of every effective P2 bedside table."""
    p = spec.params
    size = model_size(p["modelId"])
    drafts = []
    for s in sorted(inp.items, key=lambda x: x.id):
        q: Mapping[str, Any] = s.params
        if s.rule != "P2":
            continue
        c = (q["centre"]["x"], q["centre"]["y"])
        ref = HostRef(suggestion=s.id, room=s.host_ref.room)
        lift = q.get("liftMm", 0) + q["sizeMm"][2]
        drafts.append(_item(f"DRESS-D3-{_short(s.id)}", ref, "other", "vase", p["modelId"], c, size, 0.0, lift))
    return RuleOutput(drafts=drafts)


def d4_picture(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """D4: picture centred at `centreHeightMm` on the longest wall stretch with no opening behind and no tall item
    in front."""
    p = spec.params
    out = RuleOutput()
    site = Site(inp.layer, inp.items)
    w, d, h = model_size(p["modelId"])
    margin = p["sideMarginMm"]
    tall = [o.poly for o in site.obstacles if o.top > p["tallItemMm"]]
    for room in sorted(inp.layer.rooms, key=lambda r: r.id):
        if room.usage not in p["usages"]:
            continue
        sid = f"DRESS-D4-{room.id}"
        for e in site.wall_edges(room.id):
            if e.length < w + 2 * margin:
                continue
            rot = facing(e.nx, e.ny)
            behind = rect(e.at(e.length / 2), w + margin, 300, rot)
            ahead = rect(e.at(e.length / 2, 150), w + margin, 300, rot)
            if behind.intersects(site.openings) or any(ahead.intersects(t) for t in tall):
                continue
            lift = p["centreHeightMm"] - h // 2
            out.drafts.append(
                _item(
                    sid,
                    HostRef(room=room.id),
                    "other",
                    "artwork",
                    p["modelId"],
                    e.at(e.length / 2, d / 2),
                    (w, d, h),
                    rot,
                    lift,
                )
            )
            break
        else:
            out.notes.append(Note("D4", sid, "skipped", messages.NO_FREE_WALL))
    return out


def d5_plant(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """D5: a plant in a corner of each room in `usages` (placement from the ends of wall stretches)."""
    p = spec.params
    out = RuleOutput()
    site = Site(inp.layer, inp.items)
    for room in sorted(inp.layer.rooms, key=lambda r: r.id):
        if room.usage not in p["usages"]:
            continue
        sid = f"DRESS-D5-{room.id}"
        got = _place(site, room.id, p["modelId"], p, "ends", False)
        out.notes.extend(_placed_notes("D5", sid, p["modelId"], got, p["scaleTolPct"]))
        if got is not None:
            out.drafts.append(
                _item(
                    sid, HostRef(room=room.id), "other", "plant", p["modelId"], got.centre, got.size, got.rotation_deg
                )
            )
    return out
