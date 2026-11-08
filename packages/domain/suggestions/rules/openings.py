"""Window rules: C1 casing, C2 sash (parameters copied per window) and D2 curtains (fit checked with shapely)."""

from __future__ import annotations

from shapely.geometry import LineString, Polygon

from packages.domain.spatial.model import Opening, SpatialLayer, Wall
from packages.domain.suggestions import messages
from packages.domain.suggestions.model import CasingParams, CurtainParams, HostRef, SashParams, params_dict
from packages.domain.suggestions.rules.base import Draft, Note, RuleInput, RuleOutput
from packages.domain.suggestions.rules.geom import frame, js_round, ring, room_at
from packages.domain.suggestions.ruleset import RuleSpec


def _windows(layer: SpatialLayer) -> list[Opening]:
    """Windows sorted by id."""
    return sorted((o for o in layer.openings if o.kind == "window"), key=lambda o: o.id)


def c1_casing(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """C1: inner casing for every window (doors already carry theirs)."""
    params = params_dict(CasingParams.model_validate(spec.params))
    return RuleOutput(
        drafts=[Draft(f"DRESS-C1-{o.id}", "casing", HostRef(opening=o.id), dict(params)) for o in _windows(inp.layer)]
    )


def c2_sash(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """C2: sash frame for every window."""
    params = params_dict(SashParams.model_validate(spec.params))
    return RuleOutput(
        drafts=[Draft(f"DRESS-C2-{o.id}", "sash", HostRef(opening=o.id), dict(params)) for o in _windows(inp.layer)]
    )


def d2_curtain(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """D2: two curtain panels (L towards the wall start, R towards its end) per window of a living room.

    The rod spans the window plus `extMm[0]` each side; when that does not fit in the room it falls back through
    the next values (degraded, with a reason), and skips the window when even the last does not fit.
    """
    p = spec.params
    out = RuleOutput()
    walls: dict[str, Wall] = {w.id: w for w in inp.layer.walls}
    for o in _windows(inp.layer):
        wall = walls.get(o.wall_id)
        if wall is None:
            continue
        f = frame(wall)
        mid = o.offset_mm + o.width_mm / 2
        inside = None
        for sign in (1, -1):
            room = room_at(inp.layer.rooms, *f.at(mid, sign * (wall.thickness_mm / 2 + p["probeMm"])))
            if room is not None:
                inside = (sign, room)
                break
        if inside is None:
            out.notes.append(Note("D2", o.id, "skipped", messages.NO_ROOM_INSIDE))
            continue
        sign, room = inside
        if room.usage not in p["usages"]:
            continue
        area = Polygon(ring(room.outline)).buffer(5)
        across = sign * (wall.thickness_mm / 2 + p["offsetMm"])
        ext = next(
            (
                e
                for e in p["extMm"]
                if area.contains(
                    LineString([f.at(mid - o.width_mm / 2 - e, across), f.at(mid + o.width_mm / 2 + e, across)]).buffer(
                        p["clearanceMm"]
                    )
                )
            ),
            None,
        )
        if ext is None:
            out.notes.append(Note("D2", o.id, "skipped", messages.CURTAIN_NO_FIT))
            continue
        degraded = None if ext == p["extMm"][0] else messages.curtain_degraded(ext)
        if degraded:
            out.notes.append(Note("D2", o.id, "degraded", degraded))
        width = max(p["panelMinMm"], js_round(p["panelBaseMm"] + p["panelPerMetreMm"] * o.width_mm / 1000))
        for side in ("L", "R"):
            params = CurtainParams(
                side=side,
                width_mm=width,
                ext_mm=ext,
                top_mm=wall.height_mm - p["ceilingGapMm"],
                offset_mm=p["offsetMm"],
                pleat_mm=p["pleatMm"],
                material=p["material"],
                degraded_vi=degraded,
            )
            out.drafts.append(
                Draft(f"DRESS-D2-{o.id}-{side}", "curtain", HostRef(opening=o.id, room=room.id), params_dict(params))
            )
    return out
