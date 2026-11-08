"""Kitchen runs (F2/F3), furnishing (P1-P3, D1, D3-D5) and wet-room/ceiling finishes (R2, R3)."""

from pathlib import Path
from typing import Any

import pytest

from packages.domain.model_export.snapshot import canonical_json
from packages.domain.spatial.model import SpatialLayer
from packages.domain.suggestions import messages
from packages.domain.suggestions.model import Suggestion, suggestions_to_json
from packages.domain.suggestions.reconcile import Reconciled, apply_state, reconcile
from packages.domain.suggestions.rules import RULES
from packages.domain.suggestions.rules.base import RuleInput, RuleOutput
from packages.domain.suggestions.rules.placement import Site, check_collisions, item_rect
from packages.domain.suggestions.ruleset import Ruleset, load_ruleset
from packages.testing.factories.suggestions import (
    flat_layer,
    flat_parts,
    furniture,
    layer,
    opening,
    room,
    ruleset_with,
    sid,
    wall,
)

RS = load_ruleset()
DATA = Path(__file__).with_name("data")
R1, R2, R3 = sid("R", 1), sid("R", 2), sid("R", 3)
MODEL = {"id": "kitchen-x", "src": "/items/kitchen-x/model.glb"}


def run(rule: str, lay: SpatialLayer, items: tuple[Suggestion, ...] = (), rs: Ruleset = RS) -> RuleOutput:
    """One rule with the shipped parameters."""
    return RULES[rule](RuleInput(lay, items), rs.rules[rule])


def rec(lay: SpatialLayer, prev: list[Suggestion] | None = None, rs: Ruleset = RS) -> Reconciled:
    """All rules through `reconcile`."""
    return reconcile(prev or [], lay, rs, revision=1)


def get(res: Reconciled, sid_: str) -> Suggestion:
    """One record by id."""
    return next(s for s in res.suggestions if s.id == sid_)


def notes(out: RuleOutput | Reconciled) -> dict[str, tuple[str, str]]:
    """host → (status, reason)."""
    return {n.host: (n.status, n.reason_vi) for n in out.notes}


def cab(n: int, centre: tuple[int, int], size: tuple[int, int], **over: Any) -> dict[str, Any]:
    """A kitchen cabinet box."""
    return furniture(n, "kitchenCabinet", centre, size, **over)


def penthouse() -> SpatialLayer:
    """Bench plan r5/syn-penthouse (two AI kitchen boxes in a bedroom)."""
    return SpatialLayer.model_validate_json((DATA / "syn-penthouse.layer.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- F2 / F3


def test_f2__run_on_wall_box_upper_follows_duplicates_fold() -> None:
    """A box against W2 gets a base run (back line on the wall face, run to its left) and an upper run; a 98 %
    overlapping smaller box folds into it (degraded, listed in `mergedIds`)."""
    lay = flat_layer(furn=[cab(1, (6500, 350), (2400, 600)), cab(2, (6500, 200), (2300, 300))])
    base = run("F2", lay)
    assert [d.id for d in base.drafts] == [f"DRESS-F2-{sid('F', 1)}"]
    p = base.drafts[0].params
    assert (p["start"], p["end"]) == ({"x": 5300, "y": 50}, {"x": 7700, "y": 50})
    assert (p["lengthMm"], p["depthMm"], p["topMm"], p["mergedIds"]) == (2400, 600, 860, [sid("F", 2)])
    assert notes(base) == {sid("F", 2): ("degraded", messages.duplicate_box(100, sid("F", 1)))}
    res = rec(lay)
    upper = get(res, f"DRESS-F3-{sid('F', 1)}")
    assert upper.host_ref.suggestion == f"DRESS-F2-{sid('F', 1)}"
    assert (upper.params["slot"], upper.params["bottomMm"], upper.params["depthMm"]) == ("upper", 1450, 350)
    assert not [n for n in check_collisions(lay, res.suggestions) if n.label == "rule"]
    off = apply_state(res.suggestions, [{"id": f"DRESS-F2-{sid('F', 1)}", "state": "disabled"}], RS)
    again = rec(lay, off)
    assert get(again, f"DRESS-F3-{sid('F', 1)}").stale is not None


@pytest.mark.parametrize(
    ("box", "reason"),
    [
        (cab(1, (6500, 1500), (2400, 600)), messages.run_off_wall(0, 150, 80)),
        (cab(1, (6500, 350), (800, 600)), messages.run_too_short(800, 900)),
        (cab(1, (6500, 550), (2400, 1000)), messages.run_too_deep(1000, 900)),
        (cab(1, (2000, 350), (2400, 600)), messages.run_wrong_room("bedroom")),
        (cab(1, (6500, 350), (2400, 600), model=MODEL), messages.HAS_MODEL),
        (cab(1, (20000, 350), (2400, 600)), messages.run_wrong_room(None)),
    ],
)
def test_f2__gates_skip_with_reason(box: dict[str, Any], reason: str) -> None:
    """Floating box, too short, too deep, wrong room, user model, no room: no run, a note instead."""
    out = run("F2", flat_layer(furn=[box]))
    assert out.drafts == []
    assert notes(out) == {sid("F", 1): ("skipped", reason)}


def test_f2__run_that_would_hit_something_new_is_skipped() -> None:
    """A 300 mm box grows to a 600 mm run; a table in front that the box clears makes the run collide → skipped."""
    lay = flat_layer(furn=[cab(1, (6500, 200), (2400, 300)), furniture(2, "table", (6500, 600), (1000, 200))])
    out = run("F2", lay)
    assert out.drafts == []
    assert notes(out)[sid("F", 1)] == ("skipped", messages.collides("dãy tủ", ["item-item"]))


def test_f2__no_walls_means_floating() -> None:
    """Without any wall every back side is floating."""
    lay = layer(rooms=[room(1, "kitchen", [(0, 0), (4000, 0), (4000, 3000), (0, 3000)])],
                furn=[cab(1, (2000, 350), (2400, 600))])  # fmt: skip
    assert notes(run("F2", lay))[sid("F", 1)] == ("skipped", messages.run_off_wall(0, 150, 80))


def test_f3__window_over_the_run_blocks_the_upper_cabinets() -> None:
    """A window on the run's wall: base run stays, upper run skipped with a note."""
    parts = flat_parts()
    parts["openings"].append(opening(3, 2, "window", 2000, 1000))
    res = rec(layer(**{**parts, "furn": [cab(1, (6500, 350), (2400, 600))]}))
    ids = {s.id for s in res.suggestions}
    assert f"DRESS-F2-{sid('F', 1)}" in ids
    assert f"DRESS-F3-{sid('F', 1)}" not in ids
    assert notes(res)[sid("F", 1)] == ("skipped", messages.UPPER_BLOCKED)


def test_penthouse__floating_cabinet_and_wall_contact_gates() -> None:
    """The two AI kitchen boxes in the penthouse bedroom are refused by the role gate; allowing bedrooms, the wall
    contact gate still refuses the one standing away from the walls (KET-QUA-LOI §3)."""
    lay = penthouse()
    bedroom = {f.id for f in lay.furniture if f.kind == "kitchenCabinet" and f.room_id == "R-03H62D0F1HUUY4636OZ63G649"}
    assert len(bedroom) == 2
    out = run("F2", lay)
    assert not {d.host_ref.furniture for d in out.drafts} & bedroom
    assert all(notes(out)[f] == ("skipped", messages.run_wrong_room("bedroom")) for f in bedroom)
    loose = ruleset_with(lambda raw: raw["rules"]["F2"]["params"]["roomUsages"].append("bedroom"))
    reasons = {f: notes(run("F2", lay, rs=loose)).get(f, ("built", ""))[1] for f in bedroom}
    assert any(r.startswith("mặt sau hộp chỉ có") for r in reasons.values()), reasons


# ---------------------------------------------------------------- P1 / P2 / D1 / P3 / D3 / D4 / D5


def test_flat_plan__furnished_without_rule_collisions() -> None:
    """Bedroom: bed + wardrobe (12 m² ≥ 7), bedside tables chained on the P1 bed, vases on them; living room: sofa,
    rug, coffee table on the rug, plant; loggia: planter. Nothing the rules add collides."""
    lay = flat_layer()
    res = rec(lay)
    bed = f"DRESS-P1-{R1}-bed"
    ids = {s.id for s in res.suggestions}
    assert {bed, f"DRESS-P1-{R1}-wardrobe", f"DRESS-P1-{R2}-sofa", f"DRESS-P1-{R3}-planter"} <= ids
    tables = [s for s in res.suggestions if s.rule == "P2"]
    assert tables
    assert all(s.host_ref.suggestion == bed for s in tables)
    assert all(s.id.startswith("DRESS-P2-P1-") for s in tables)
    vases = [s for s in res.suggestions if s.rule == "D3"]
    assert len(vases) == len(tables)
    assert all(v.params["liftMm"] == 551 for v in vases)
    assert {v.host_ref.suggestion for v in vases} == {t.id for t in tables}
    sofa = f"P1-{R2}-sofa"
    assert get(res, f"DRESS-D1-{sofa}").kind == "rug"
    assert get(res, f"DRESS-P3-{sofa}").params["liftMm"] == 12
    assert get(res, f"DRESS-D5-{R2}").params["modelId"] == "plant-ph"
    assert {s.id for s in res.suggestions if s.rule == "D4"} == {f"DRESS-D4-{R1}", f"DRESS-D4-{R2}"}
    assert get(res, f"DRESS-D4-{R1}").params["liftMm"] == 1300
    assert not [n for n in check_collisions(lay, res.suggestions) if n.label == "rule"]
    site = Site(lay)
    for s in res.suggestions:
        if s.kind == "item" and s.params["liftMm"] == 0 and s.host_ref.room:
            assert site.finish[s.host_ref.room].buffer(5).contains(item_rect(s.params)), s.id


def test_p3__stands_on_the_floor_when_the_rug_is_off() -> None:
    """Disabling the D1 rug drops the coffee table back to the floor on the next run."""
    lay = flat_layer()
    first = rec(lay)
    rug = f"DRESS-D1-P1-{R2}-sofa"
    second = rec(lay, apply_state(first.suggestions, [{"id": rug, "state": "disabled"}], RS))
    assert get(second, f"DRESS-P3-P1-{R2}-sofa").params["liftMm"] == 0


def test_p1__no_wall_long_enough_or_too_small() -> None:
    """A bedroom whose walls are shorter than bed + margin: bed skipped with a reason; 3.2 m² wants no wardrobe;
    the picture finds no free wall either."""
    walls = [wall(1, (0, 0), (1800, 0)), wall(2, (1800, 0), (1800, 1800)), wall(3, (1800, 1800), (0, 1800)),
             wall(4, (0, 1800), (0, 0))]  # fmt: skip
    small = layer(walls=walls, rooms=[room(1, "bedroom", [(0, 0), (1800, 0), (1800, 1800), (0, 1800)])])
    out = run("P1", small)
    assert out.drafts == []
    assert notes(out) == {f"DRESS-P1-{R1}-bed": ("skipped", messages.no_room_for("bed-sf", 1800, 2252, 10))}
    tiny = layer(walls=[wall(1, (0, 0), (1000, 0)), wall(2, (1000, 0), (1000, 1000)), wall(3, (1000, 1000), (0, 1000)),
                        wall(4, (0, 1000), (0, 0))],
                 rooms=[room(1, "bedroom", [(0, 0), (1000, 0), (1000, 1000), (0, 1000)])])  # fmt: skip
    assert notes(run("D4", tiny)) == {f"DRESS-D4-{R1}": ("skipped", messages.NO_FREE_WALL)}


def test_p1__shrunk_bed_is_degraded() -> None:
    """Only the -10 % step fits: placed at 90 % with a degraded note."""
    walls = [wall(1, (0, 0), (1900, 0)), wall(2, (1900, 0), (1900, 2300)), wall(3, (1900, 2300), (0, 2300)),
             wall(4, (0, 2300), (0, 0))]  # fmt: skip
    lay = layer(walls=walls, rooms=[room(1, "bedroom", [(0, 0), (1900, 0), (1900, 2300), (0, 2300)])])
    out = run("P1", lay)
    assert [d.params["sizeMm"] for d in out.drafts] == [[1620, 2027, 844]]
    assert notes(out) == {f"DRESS-P1-{R1}-bed": ("degraded", messages.scaled("bed-sf", 90))}


def test_p1__real_items_fill_slots_sofa_by_box_size() -> None:
    """A user bed and a large chair box (F1 would pick a sofa) fill their slots: no P1 bed or sofa."""
    furn = [furniture(1, "bed", (2000, 2000), (1600, 2000), roomId=R1), furniture(2, "chair", (6500, 400), (2000, 800))]
    ids = {d.id for d in run("P1", flat_layer(furn=furn)).drafts}
    assert f"DRESS-P1-{R1}-bed" not in ids
    assert f"DRESS-P1-{R2}-sofa" not in ids
    assert f"DRESS-P1-{R1}-wardrobe" in ids


def test_p2_p3_d1__document_hosts_and_refusals() -> None:
    """Document bed and sofa host companions by `hostRef.furniture`; a bed outside bedrooms gets none; a sofa in a
    shallow room gets no rug and no table, each with a reason."""
    furn = [furniture(1, "bed", (2000, 1950), (1600, 2000), roomId=R1), furniture(2, "bed", (6500, 1500), (1600, 2000))]
    tables = run("P2", flat_layer(furn=furn)).drafts
    assert {d.id for d in tables} == {f"DRESS-P2-{sid('F', 1)}-L", f"DRESS-P2-{sid('F', 1)}-R"}
    assert all(d.host_ref.furniture == sid("F", 1) for d in tables)
    walls = [wall(1, (0, 0), (2400, 0)), wall(2, (2400, 0), (2400, 1500)), wall(3, (2400, 1500), (0, 1500)),
             wall(4, (0, 1500), (0, 0))]  # fmt: skip
    shallow = layer(
        walls=walls,
        rooms=[room(1, "livingRoom", [(0, 0), (2400, 0), (2400, 1500), (0, 1500)])],
        furn=[furniture(3, "chair", (1200, 350), (2000, 600), variant="sofa")],
    )
    rug, table = run("D1", shallow), run("P3", shallow)
    assert rug.drafts == []
    assert table.drafts == []
    assert notes(table) == {f"DRESS-P3-{sid('F', 3)}": ("skipped", messages.COFFEE_NO_ROOM)}
    assert notes(rug)[f"DRESS-D1-{sid('F', 3)}"][0] == "skipped"


def test_p2__blocked_side_skipped() -> None:
    """A wardrobe right next to the bed head blocks that side's table only."""
    furn = [
        furniture(1, "bed", (2000, 1950), (1600, 2000), roomId=R1),
        furniture(2, "wardrobe", (900, 2600), (600, 600)),
    ]
    out = run("P2", flat_layer(furn=furn))
    assert len(out.drafts) == 1
    assert list(notes(out).values()) == [("skipped", messages.NIGHTSTAND_NO_ROOM)]


def test_d5__no_corner_free() -> None:
    """A living room too small for the plant: skipped with the size in the reason."""
    walls = [wall(1, (0, 0), (700, 0)), wall(2, (700, 0), (700, 700)), wall(3, (700, 700), (0, 700)),
             wall(4, (0, 700), (0, 0))]  # fmt: skip
    lay = layer(walls=walls, rooms=[room(1, "livingRoom", [(0, 0), (700, 0), (700, 700), (0, 700)])])
    assert notes(run("D5", lay)) == {f"DRESS-D5-{R1}": ("skipped", messages.no_room_for("plant-ph", 844, 798, 10))}


# ---------------------------------------------------------------- R2 / R3


def test_r2__tiles_cut_at_doors_and_windows_sill_piece_under_window() -> None:
    """Bathroom faces: full height up to min(2.4 m, wall), doors cut exactly, low windows cut with a floor-to-sill
    piece, windows above the tile height ignored."""
    parts = flat_parts()
    parts["rooms"][1]["usage"] = "bathroom"
    parts["openings"] += [opening(3, 2, "window", 1000, 800), opening(4, 7, "window", 500, 800, sillHeightMm=2500)]
    out = run("R2", layer(**parts))
    got = {d.id: d.params for d in out.drafts}
    w2 = got[f"DRESS-R2-{R2}-{sid('W', 2)}"]
    assert (w2["side"], w2["spansMm"], w2["sillSpansMm"], w2["heightMm"]) == (
        "left", [[0, 1000], [1800, 5000]], [[1000, 1800, 900]], 2400
    )  # fmt: skip
    assert got[f"DRESS-R2-{R2}-{sid('W', 5)}"]["spansMm"] == [[0, 1000], [1900, 3000]]
    assert got[f"DRESS-R2-{R2}-{sid('W', 7)}"]["spansMm"] == [[0, 3000]]
    assert "sillSpansMm" not in got[f"DRESS-R2-{R2}-{sid('W', 7)}"]
    assert not any(R1 in k for k in got)


def test_r2__curved_wall_and_wall_on_both_faces_noted() -> None:
    """Curved walls are not tiled (note); a stub bordering the room twice keeps the longer face (note)."""
    notch = [(0, 0), (4000, 0), (4000, 3000), (2100, 3000), (2100, 1000), (1900, 1000), (1900, 3000), (0, 3000)]
    walls = [wall(1, (2000, 1000), (2000, 3000), thickness=200), wall(2, (0, 0), (4000, 0), curveOffsetMm=300)]
    lay = layer(walls=walls, rooms=[room(1, "bathroom", notch)])
    out = run("R2", lay)
    assert [d.params["side"] for d in out.drafts] == ["left"]
    assert sorted(r for _, r in notes(out).values()) == sorted([messages.CURVED_WALL_TILE, messages.OTHER_FACE])


def test_r3__cornice_in_dry_rooms_with_a_ceiling() -> None:
    """Bedroom and living room get a cornice; the loggia (no ceiling) does not."""
    out = run("R3", flat_layer())
    assert [d.id for d in out.drafts] == [f"DRESS-R3-{R1}", f"DRESS-R3-{R2}"]
    assert out.drafts[0].params == {"heightMm": 50, "depthMm": 30}


# ---------------------------------------------------------------- determinism


def test_penthouse_shuffled_input__byte_identical_output() -> None:
    """Entity order in the layer does not change ids, params or their order."""
    lay = penthouse()
    raw = lay.model_dump(mode="json", by_alias=True, exclude_none=True)
    for key in ("walls", "openings", "rooms", "furniture"):
        raw[key] = raw[key][1::2][::-1] + raw[key][::2]
    shuffled = SpatialLayer.model_validate(raw)
    a, b = rec(lay), rec(shuffled)
    assert canonical_json(suggestions_to_json(a.suggestions)) == canonical_json(suggestions_to_json(b.suggestions))
    assert check_collisions(lay, a.suggestions) == check_collisions(shuffled, b.suggestions)
