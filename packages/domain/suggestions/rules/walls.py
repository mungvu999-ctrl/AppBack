"""R1 skirting and R4 loggia railing: pure 1-D interval work along walls and room edges (mirrorable in the FE)."""

from __future__ import annotations

import math
from collections import defaultdict

from packages.domain.spatial.model import Point, Room, SpatialLayer, Wall
from packages.domain.suggestions import messages
from packages.domain.suggestions.model import HostRef, RailingParams, SkirtingParams, params_dict
from packages.domain.suggestions.rules.base import Draft, Note, RuleInput, RuleOutput
from packages.domain.suggestions.rules.geom import (
    Frame,
    Interval,
    clip_to_band,
    frame,
    js_round,
    ring,
    signed_area,
    subtract,
    union,
)
from packages.domain.suggestions.ruleset import RuleSpec

PARALLEL_SIN = 0.05
"""Room edge and wall count as parallel below ~3 degrees."""


def _rounded(spans: list[Interval], min_mm: int) -> tuple[tuple[int, int], ...]:
    """Spans rounded to whole mm (`Math.round`), shorter than `min_mm` dropped."""
    out = [(js_round(lo), js_round(hi)) for lo, hi in spans]
    return tuple((lo, hi) for lo, hi in out if hi - lo >= min_mm)


def _door_cuts(layer: SpatialLayer, margin: int) -> dict[str, list[Interval]]:
    """Per wall: door openings widened by the casing margin on both sides."""
    cuts: dict[str, list[Interval]] = defaultdict(list)
    for o in layer.openings:
        if o.kind == "door":
            cuts[o.wall_id].append((o.offset_mm - margin, o.offset_mm + o.width_mm + margin))
    return cuts


def _faces(room: Room, walls: list[tuple[Wall, Frame]], tol: int) -> dict[tuple[str, str], list[Interval]]:
    """(wall id, side) → stretches of the wall axis that a room edge runs along, on the room's side."""
    pts = ring(room.outline)
    turn = 1.0 if signed_area(pts) > 0 else -1.0
    faces: dict[tuple[str, str], list[Interval]] = defaultdict(list)
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        length = math.dist(a, b)
        if length == 0:
            continue
        ex, ey = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        inx, iny = -ey * turn, ex * turn
        for wall, f in walls:
            if abs(ex * f.dy - ey * f.dx) > PARALLEL_SIN:
                continue
            _, across = f.local((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            if abs(across) > wall.thickness_mm / 2 + tol:
                continue
            s0, s1 = sorted((f.local(*a)[0], f.local(*b)[0]))
            s0, s1 = max(s0, 0.0), min(s1, f.length)
            if s1 > s0:
                side = "left" if inx * f.nx + iny * f.ny > 0 else "right"
                faces[(wall.id, side)].append((s0, s1))
    return faces


def r1_skirting(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """R1: skirting on every dry room face that runs along a wall, cut at doors (+ casing margin)."""
    p = spec.params
    out = RuleOutput()
    excluded = set(p["excludeUsages"])
    min_span, tol = int(p["minSpanMm"]), int(p["faceToleranceMm"])
    cuts = _door_cuts(inp.layer, int(p["doorMarginMm"]))
    straight: list[tuple[Wall, Frame]] = []
    curved: list[tuple[Wall, Frame]] = []
    for wall in sorted(inp.layer.walls, key=lambda w: w.id):
        (curved if wall.curve_offset_mm else straight).append((wall, frame(wall)))
    for room in sorted(inp.layer.rooms, key=lambda r: r.id):
        if room.usage in excluded:
            continue
        for wall_id, _ in sorted(_faces(room, curved, tol)):
            out.notes.append(Note("R1", f"{room.id}/{wall_id}", "skipped", messages.CURVED_WALL))
        by_wall: dict[str, dict[str, tuple[tuple[int, int], ...]]] = defaultdict(dict)
        for (wall_id, side), spans in sorted(_faces(room, straight, tol).items()):
            kept = _rounded(subtract(union(spans), cuts.get(wall_id, ())), min_span)
            if kept:
                by_wall[wall_id][side] = kept
        for wall_id, sides in sorted(by_wall.items()):
            side = max(sides, key=lambda s: (sum(hi - lo for lo, hi in sides[s]), s == "left"))
            if len(sides) > 1:
                out.notes.append(Note("R1", f"{room.id}/{wall_id}", "skipped", messages.OTHER_FACE))
            params = SkirtingParams(
                side=side,
                spans_mm=sides[side],
                height_mm=p["heightMm"],
                thickness_mm=p["thicknessMm"],
                material=p["material"],
            )
            out.drafts.append(
                Draft(
                    f"DRESS-R1-{room.id}-{wall_id}",
                    "skirting",
                    HostRef(room=room.id, wall=wall_id),
                    params_dict(params),
                )
            )
    return out


def r4_railing(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """R4: railing on loggia outline edges where no wall stands within `wallGapMm` (open part ≥ `minEdgeMm`).

    ponytail: curved walls are treated as their chord; fine for railings, revisit if curved loggias appear.
    """
    p = spec.params
    out = RuleOutput()
    gap, min_edge = int(p["wallGapMm"]), int(p["minEdgeMm"])
    walls = [(w, frame(w)) for w in sorted(inp.layer.walls, key=lambda w: w.id)]
    for room in sorted(inp.layer.rooms, key=lambda r: r.id):
        if room.usage not in p["usages"]:
            continue
        pts = ring(room.outline)
        found = False
        for k, a in enumerate(pts):
            b = pts[(k + 1) % len(pts)]
            length = math.dist(a, b)
            covered = [
                (iv[0] * length, iv[1] * length)
                for wall, f in walls
                if (iv := clip_to_band(f, a, b, wall.thickness_mm / 2 + gap, gap)) is not None
            ]
            spans = _rounded(subtract([(0.0, length)], covered), min_edge)
            if not spans:
                continue
            found = True
            start, end = room.outline[k], room.outline[(k + 1) % len(pts)]
            params = RailingParams(
                edge_index=k,
                start=Point(x=start.x, y=start.y),
                end=Point(x=end.x, y=end.y),
                spans_mm=spans,
                height_mm=p["heightMm"],
                handrail_mm=p["handrailMm"],
                glass_mm=p["glassMm"],
                post_spacing_max_mm=p["postSpacingMaxMm"],
                metal_material=p["metalMaterial"],
                glass_material=p["glassMaterial"],
            )
            out.drafts.append(Draft(f"DRESS-R4-{room.id}-e{k}", "railing", HostRef(room=room.id), params_dict(params)))
        if not found:
            out.notes.append(Note("R4", room.id, "skipped", messages.CLOSED_LOGGIA))
    return out
