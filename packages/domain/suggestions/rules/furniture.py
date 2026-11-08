"""F1 model pick by box size, and the "is this role already furnished" test P1 will build on.

Precedence (contract §3): F1 only picks a model for a host without `model`; it may reclassify (`kindAs`/`variantAs`)
only an untouched AI item (`source=ai`, not reviewed, not edited). B3 `suggested` items are neither hosts nor
obstacles: every rule skips them.
"""

from __future__ import annotations

import math
from typing import Any

from packages.domain.spatial.model import Furniture, Room, SpatialLayer
from packages.domain.suggestions import messages
from packages.domain.suggestions.model import HostRef, ModelPickParams, params_dict
from packages.domain.suggestions.rules.base import Draft, Note, RuleInput, RuleOutput
from packages.domain.suggestions.rules.geom import js_round, point_in_ring, ring, room_at
from packages.domain.suggestions.ruleset import RuleSpec


def _box(f: Furniture) -> tuple[int, int]:
    """(long, short) side of the bounding box in mm."""
    w = f.bounding_box.max.x - f.bounding_box.min.x
    d = f.bounding_box.max.y - f.bounding_box.min.y
    return max(w, d), min(w, d)


def _matches(row: dict[str, Any], f: Furniture, usage: str | None, long: int, short: int) -> bool:
    """One table row against one item; absent bounds do not constrain."""
    return (
        row["kind"] == f.kind
        and row.get("variant", f.variant) == f.variant
        and usage in row.get("roomUsages", [usage])
        and row.get("minLongMm", 0) <= long <= row.get("maxLongMm", long)
        and row.get("minShortMm", 0) <= short <= row.get("maxShortMm", short)
        and ("maxAspect" not in row or long < row["maxAspect"] * short)
    )


def _locked(f: Furniture) -> bool:
    """The user owns the item's kind: reviewed, edited or drawn by a person."""
    return f.source != "ai" or f.reviewed or f.edited


def f1_model_pick(inp: RuleInput, spec: RuleSpec) -> RuleOutput:
    """F1: first matching row of `table` picks a model; shrink beyond `scaleTolPct` is refused (kept as is)."""
    p = spec.params
    out = RuleOutput()
    rooms = {r.id: r for r in inp.layer.rooms}
    kinds = {row["kind"] for row in p["table"]}
    for f in sorted(inp.layer.furniture, key=lambda x: x.id):
        if f.suggested or f.kind not in kinds:
            continue
        if f.model is not None:
            out.notes.append(Note("F1", f.id, "skipped", messages.HAS_MODEL))
            continue
        long, short = _box(f)
        room = rooms.get(f.room_id) if f.room_id else room_at(inp.layer.rooms, f.centre.x, f.centre.y)
        row = next((r for r in p["table"] if _matches(r, f, room.usage if room else None, long, short)), None)
        if row is None:
            continue
        if row["pick"] is None:
            out.notes.append(Note("F1", f.id, "skipped", row["reasonVi"]))
            continue
        if ("kindAs" in row or "variantAs" in row) and _locked(f):
            out.notes.append(Note("F1", f.id, "skipped", messages.LOCKED_KIND))
            continue
        model = p["models"][row["pick"]]
        mw, md = model["sizeMm"][0], model["sizeMm"][1]
        ratio = min(long / max(mw, md), short / min(mw, md))
        if ratio < 1 - p["scaleTolPct"] / 100:
            reason = messages.model_too_big(row["pick"], math.floor(ratio * 100), p["scaleTolPct"])
            out.notes.append(Note("F1", f.id, "skipped", reason))
            continue
        params = ModelPickParams(
            model_id=row["pick"],
            scale=1.0 if ratio >= 1 else js_round(ratio * 1000) / 1000,
            reason_vi=messages.model_pick(long, short, model["nameVi"]),
            kind_as=row.get("kindAs"),
            variant_as=row.get("variantAs"),
        )
        out.drafts.append(Draft(f"DRESS-F1-{f.id}", "modelPick", HostRef(furniture=f.id), params_dict(params)))
    return out


def missing_primary_slots(layer: SpatialLayer, room: Room, spec: RuleSpec) -> list[str]:
    """P1 precondition: role slots of `room.usage` not yet furnished by a real item of the room.

    An item fills a slot by `kind`, by `variant`, or (slots with `boxKinds`) by a box of that kind at least
    `boxMinLongMm` x `boxMinShortMm` (the sofa F1 would pick for a large chair box); B3 `suggested` items do not count.
    Slots with `minAreaM2` above the room area are not wanted at all.
    """
    pts = ring(room.outline)
    present = [
        f
        for f in layer.furniture
        if not f.suggested and (f.room_id == room.id if f.room_id else point_in_ring(f.centre.x, f.centre.y, pts))
    ]
    return [
        slot["slot"]
        for slot in spec.params["roles"].get(room.usage, [])
        if slot.get("minAreaM2", 0) <= room.area_m2
        and not any(f.kind in slot["kinds"] or f.variant in slot["variants"] or _box_fills(f, slot) for f in present)
    ]


def _box_fills(f: Furniture, slot: dict[str, Any]) -> bool:
    """A box of a `boxKinds` kind big enough for the slot's model."""
    long, short = _box(f)
    return f.kind in slot.get("boxKinds", ()) and long >= slot["boxMinLongMm"] and short >= slot["boxMinShortMm"]
