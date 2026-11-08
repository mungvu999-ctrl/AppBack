"""R1 skirting and R4 loggia railing: pure 1-D interval work along walls and room edges (mirrorable in the FE)."""

from __future__ import annotations

import math
from collections import defaultdict

from packages.domain.spatial.model import Point, Room, SpatialLayer, Wall
from packages.domain.suggestions import messages
from packages.domain.suggestions.model import (
    CorniceParams,
    HostRef,
    RailingParams,
    SkirtingParams,
    WallTileParams,
    params_dict,
)
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


def _window_cuts(layer: SpatialLayer, below: int) -> dict[str, list[tuple[float, float, int]]]:
    """Per wall: windows whose sill is under `below` as (from, to, sill)."""
    cuts: dict[str, list[tuple[float, float, int]]] = defaultdict(list)
    for o in layer.openings:
        if o.kind == "window" and o.sill_height_mm < below:
            cuts[o.wall_id].append((o.offset_mm, o.offset_mm + o.width_mm, o.sill_height_mm))
    return cuts


def r2_wall_tile(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """R2: wet-room wall tile on every room face along a wall, up to min(`heightMm`, wall height), cut at doors and
    windows; under a window with a sill ≥ `minSillMm` the tile runs from the floor up to the sill."""
    p = spec.params
    out = RuleOutput()
    min_span, tol = int(p["minSpanMm"]), int(p["faceToleranceMm"])
    doors = _door_cuts(inp.layer, 0)
    walls = {w.id: w for w in inp.layer.walls}
    straight = [(w, frame(w)) for _, w in sorted(walls.items()) if not w.curve_offset_mm]
    curved = [(w, frame(w)) for _, w in sorted(walls.items()) if w.curve_offset_mm]
    for room in sorted(inp.layer.rooms, key=lambda r: r.id):
        if room.usage not in p["usages"]:
            continue
        for wall_id, _ in sorted(_faces(room, curved, tol)):
            out.notes.append(Note("R2", f"{room.id}/{wall_id}", "skipped", messages.CURVED_WALL_TILE))
        faces: dict[str, dict[str, tuple[list[Interval], list[Interval], int]]] = defaultdict(dict)
        for (wall_id, side), spans in sorted(_faces(room, straight, tol).items()):
            height = min(int(p["heightMm"]), walls[wall_id].height_mm)
            windows = [(lo, hi) for lo, hi, _ in _window_cuts(inp.layer, height).get(wall_id, [])]
            kept = subtract(union(spans), [*doors.get(wall_id, ()), *windows])
            if _rounded(kept, min_span):
                faces[wall_id][side] = (union(spans), kept, height)
        for wall_id, by_side in sorted(faces.items()):
            side = max(by_side, key=lambda s: (sum(hi - lo for lo, hi in by_side[s][1]), s == "left"))
            if len(by_side) > 1:
                out.notes.append(Note("R2", f"{room.id}/{wall_id}", "skipped", messages.OTHER_FACE))
            face, kept, height = by_side[side]
            sills = [
                (js_round(max(lo, flo)), js_round(min(hi, fhi)), sill)
                for lo, hi, sill in _window_cuts(inp.layer, height).get(wall_id, [])
                if sill >= p["minSillMm"]
                for flo, fhi in face
                if min(hi, fhi) - max(lo, flo) >= min_span
            ]
            params = WallTileParams(
                side=side,
                spans_mm=_rounded(kept, min_span),
                sill_spans_mm=tuple(sills) or None,
                height_mm=height,
                thickness_mm=p["thicknessMm"],
                tile_mm=p["tileMm"],
                material=p["material"],
            )
            out.drafts.append(
                Draft(
                    f"DRESS-R2-{room.id}-{wall_id}",
                    "wallTile",
                    HostRef(room=room.id, wall=wall_id),
                    params_dict(params),
                )
            )
    return out


def r3_cornice(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """R3: ceiling cornice in every room with a dry ceiling (not in `skipUsages`); material = the room ceiling."""
    p = spec.params
    params = params_dict(CorniceParams(height_mm=p["heightMm"], depth_mm=p["depthMm"]))
    return RuleOutput(
        drafts=[
            Draft(f"DRESS-R3-{r.id}", "cornice", HostRef(room=r.id), dict(params))
            for r in sorted(inp.layer.rooms, key=lambda r: r.id)
            if r.usage not in p["skipUsages"]
        ]
    )


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
