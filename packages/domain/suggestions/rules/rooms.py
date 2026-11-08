"""L1 ceiling light: one per room with a ceiling, at the outline's pole of inaccessibility."""

from __future__ import annotations

from packages.domain.spatial.model import Point
from packages.domain.suggestions.model import CeilingLightParams, HostRef, params_dict
from packages.domain.suggestions.rules.base import Draft, RuleInput, RuleOutput
from packages.domain.suggestions.rules.geom import js_round, polylabel, ring
from packages.domain.suggestions.ruleset import RuleSpec


def l1_ceiling_light(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """L1: pendant model in large living rooms (`lampUsages`, `areaM2 ≥ lampMinAreaM2`), flat panel elsewhere.

    Rooms in `skipUsages` (open-air loggias) have no ceiling and get nothing. Area is the stored `areaM2`, so the
    editor mirror reads the same number.
    """
    p = spec.params
    out = RuleOutput()
    for room in sorted(inp.layer.rooms, key=lambda r: r.id):
        if room.usage in p["skipUsages"]:
            continue
        x, y = polylabel(ring(room.outline), p["precisionMm"])
        lamp = room.usage in p["lampUsages"] and room.area_m2 >= p["lampMinAreaM2"]
        params = CeilingLightParams(
            centre=Point(x=js_round(x), y=js_round(y)),
            fixture="pendant" if lamp else "panel",
            model_id=p["lampModelId"] if lamp else None,
            size_mm=None if lamp else tuple(p["panelSizeMm"]),
        )
        out.drafts.append(Draft(f"DRESS-L1-{room.id}", "ceilingLight", HostRef(room=room.id), params_dict(params)))
    return out
