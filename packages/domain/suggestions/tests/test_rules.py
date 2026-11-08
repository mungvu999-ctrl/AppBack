"""Per-rule tests on small hand-built layers (see `packages/testing/factories/suggestions.py`)."""

from typing import Any

import pytest

from packages.domain.spatial.model import SpatialLayer
from packages.domain.suggestions import messages
from packages.domain.suggestions.rules import RULES
from packages.domain.suggestions.rules.base import RuleInput, RuleOutput, pending
from packages.domain.suggestions.rules.geom import Frame, clip_to_band, js_round, polylabel, subtract, union
from packages.domain.suggestions.ruleset import load_ruleset
from packages.testing.factories.suggestions import flat_layer, flat_parts, furniture, layer, opening, room, sid, wall

RS = load_ruleset()


def run(rule: str, lay: SpatialLayer) -> RuleOutput:
    """One rule on a layer with the shipped parameters."""
    return RULES[rule](RuleInput(lay), RS.rules[rule])


def by_id(out: RuleOutput) -> dict[str, dict[str, Any]]:
    """Draft params keyed by id."""
    return {d.id: d.params for d in out.drafts}


# ---------------------------------------------------------------- R1


def test_r1__faces_per_room_wall_cut_at_doors_with_casing_margin() -> None:
    """Each room face along a wall gets one record; the door on W5 (1000-1900) cuts 930-1970 on both rooms."""
    got = {k: (v["side"], v["spansMm"]) for k, v in by_id(run("R1", flat_layer())).items()}
    r1, r2, r3 = sid("R", 1), sid("R", 2), sid("R", 3)
    door_cut = [[0, 930], [1970, 3000]]
    assert got == {
        f"DRESS-R1-{r1}-{sid('W', 1)}": ("left", [[0, 4000]]),
        f"DRESS-R1-{r1}-{sid('W', 3)}": ("right", [[0, 4000]]),
        f"DRESS-R1-{r1}-{sid('W', 5)}": ("left", door_cut),
        f"DRESS-R1-{r1}-{sid('W', 6)}": ("right", [[0, 3000]]),
        f"DRESS-R1-{r2}-{sid('W', 2)}": ("left", [[0, 5000]]),
        f"DRESS-R1-{r2}-{sid('W', 4)}": ("right", [[0, 5000]]),
        f"DRESS-R1-{r2}-{sid('W', 5)}": ("right", door_cut),
        f"DRESS-R1-{r2}-{sid('W', 7)}": ("left", [[0, 3000]]),
        f"DRESS-R1-{r3}-{sid('W', 3)}": ("left", [[0, 4000]]),
        f"DRESS-R1-{r3}-{sid('W', 6)}": ("right", [[3000, 4500]]),
    }
    params = by_id(run("R1", flat_layer()))[f"DRESS-R1-{r1}-{sid('W', 1)}"]
    assert (params["heightMm"], params["thicknessMm"], params["material"]) == (90, 12, "library:preset-white")


def test_r1__wet_room_excluded_short_span_dropped() -> None:
    """Bathrooms get R2 instead; a span under `minSpanMm` left between two doors is dropped."""
    parts = flat_parts()
    parts["rooms"][1]["usage"] = "bathroom"
    parts["openings"].append(opening(3, 5, "door", 2100, 700))  # cut 2030-2870: 1970-2030 is too short
    got = by_id(run("R1", layer(**parts)))
    assert not any(sid("R", 2) in k for k in got)
    assert got[f"DRESS-R1-{sid('R', 1)}-{sid('W', 5)}"]["spansMm"] == [[0, 930], [2870, 3000]]


def test_r1__curved_wall_skipped_with_note() -> None:
    """Curved walls are not supported yet: no record, a note instead."""
    parts = flat_parts()
    parts["walls"][0]["curveOffsetMm"] = 300
    out = run("R1", layer(**parts))
    assert f"DRESS-R1-{sid('R', 1)}-{sid('W', 1)}" not in by_id(out)
    assert [n.reason_vi for n in out.notes] == [messages.CURVED_WALL]
    assert out.notes[0].host == f"{sid('R', 1)}/{sid('W', 1)}"


def test_r1__wall_on_both_faces_of_one_room__longer_face_kept() -> None:
    """A wall stub inside a notch borders the room twice: one record, the other face noted."""
    notch = [(0, 0), (4000, 0), (4000, 3000), (2100, 3000), (2100, 1000), (1900, 1000), (1900, 3000), (0, 3000)]
    lay = layer(walls=[wall(1, (2000, 1000), (2000, 3000), thickness=200)], rooms=[room(1, "bedroom", notch)])
    out = run("R1", lay)
    assert [d.params["side"] for d in out.drafts] == ["left"]
    assert [n.reason_vi for n in out.notes] == [messages.OTHER_FACE]


def test_r1__clockwise_outline_same_sides() -> None:
    """Outline winding and a repeated outline point change nothing (R1 and L1)."""
    parts = flat_parts()
    outline = list(reversed(parts["rooms"][0]["outline"]))
    parts["rooms"][0]["outline"] = [outline[0], *outline]
    assert by_id(run("L1", layer(**parts))) == by_id(run("L1", flat_layer()))
    a = by_id(run("R1", flat_layer()))
    b = by_id(run("R1", layer(**parts)))
    assert a == b


# ---------------------------------------------------------------- R4


def test_r4__open_loggia_edges_only() -> None:
    """South and east edges of the loggia are open; parts within wall gap of W3/W5/W6 are not."""
    got = by_id(run("R4", flat_layer()))
    r3 = sid("R", 3)
    assert {k: v["spansMm"] for k, v in got.items()} == {
        f"DRESS-R4-{r3}-e1": [[110, 1500]],
        f"DRESS-R4-{r3}-e2": [[0, 3890]],
    }
    assert got[f"DRESS-R4-{r3}-e1"]["start"] == {"x": 4000, "y": 3000}
    assert got[f"DRESS-R4-{r3}-e1"]["heightMm"] == 1100


def test_r4__closed_loggia_noted() -> None:
    """A loggia closed by walls gets a note instead."""
    parts = flat_parts()
    parts["walls"] += [wall(8, (4000, 3000), (4000, 4500)), wall(9, (0, 4500), (4000, 4500))]
    out = run("R4", layer(**parts))
    assert out.drafts == []
    assert [(n.host, n.reason_vi) for n in out.notes] == [(sid("R", 3), messages.CLOSED_LOGGIA)]


# ---------------------------------------------------------------- C1, C2


def test_c1_c2__one_record_per_window_copied_params() -> None:
    """Doors get nothing; each window gets casing and sash with the table values."""
    win = sid("D", 2)
    assert by_id(run("C1", flat_layer())) == {
        f"DRESS-C1-{win}": {"widthMm": 70, "thicknessMm": 16, "material": "library:preset-white"}
    }
    assert by_id(run("C2", flat_layer())) == {f"DRESS-C2-{win}": {"frameWidthMm": 40, "frameDepthMm": 45}}


# ---------------------------------------------------------------- D2


def test_d2__two_panels_full_overhang() -> None:
    """Bedroom window: L and R panels, 300 mm overhang, top = wall height - 70."""
    out = run("D2", flat_layer())
    win = sid("D", 2)
    got = by_id(out)
    assert set(got) == {f"DRESS-D2-{win}-L", f"DRESS-D2-{win}-R"}
    assert got[f"DRESS-D2-{win}-L"] == {
        "side": "L", "widthMm": 450, "extMm": 300, "topMm": 2930, "offsetMm": 120, "pleatMm": 80,
        "material": "library:fabric-linen",
    }  # fmt: skip
    assert out.drafts[0].host_ref.room == sid("R", 1)
    assert out.notes == []


def test_d2__fallback_degraded_then_skipped() -> None:
    """Near a corner the overhang falls back to 200 mm (degraded); with no room at all the window is skipped."""
    parts = flat_parts()
    parts["openings"][1]["offsetMm"] = 300
    out = run("D2", layer(**parts))
    assert {d.params["extMm"] for d in out.drafts} == {200}
    assert out.drafts[0].params["degradedVi"] == messages.curtain_degraded(200)
    assert [n.status for n in out.notes] == ["degraded"]
    parts["openings"][1]["offsetMm"] = 0
    out = run("D2", layer(**parts))
    assert out.drafts == []
    assert [n.reason_vi for n in out.notes] == [messages.CURTAIN_NO_FIT]


def test_d2__no_room_inside_wrong_usage_missing_wall() -> None:
    """Window on a free wall → note; loggia window → nothing; window on a missing wall → nothing."""
    parts = flat_parts()
    parts["walls"].append(wall(8, (0, 6000), (2000, 6000)))
    parts["openings"] = [
        opening(3, 8, "window", 500, 800),
        opening(4, 6, "window", 3500, 800),
        opening(5, 99, "window", 0, 800),
    ]
    out = run("D2", layer(**parts))
    assert out.drafts == []
    assert [(n.host, n.reason_vi) for n in out.notes] == [(sid("D", 3), messages.NO_ROOM_INSIDE)]


# ---------------------------------------------------------------- L1


def test_l1__pendant_panel_and_open_air() -> None:
    """Large bedroom/living → pendant at the centre; small bathroom → panel; loggia → nothing."""
    bath = room(4, "bathroom", [(9000, 0), (11000, 0), (11000, 2000), (9000, 2000)])
    got = by_id(run("L1", flat_layer(rooms=[bath])))
    assert got == {
        f"DRESS-L1-{sid('R', 1)}": {
            "centre": {"x": 2000, "y": 1500},
            "fixture": "pendant",
            "modelId": "ceiling-lamp-ph",
        },
        f"DRESS-L1-{sid('R', 2)}": {
            "centre": {"x": 6500, "y": 1500},
            "fixture": "pendant",
            "modelId": "ceiling-lamp-ph",
        },
        f"DRESS-L1-{sid('R', 4)}": {"centre": {"x": 10000, "y": 1000}, "fixture": "panel", "sizeMm": [300, 300, 40]},
    }


# ---------------------------------------------------------------- pending rules, geometry helpers


def test_pending_rules__registered_produce_nothing() -> None:
    """AID waits for the concept wiring: registered and silent."""
    assert RULES["AID"] is pending
    assert run("AID", flat_layer()) == RuleOutput()


def test_polylabel__l_shape_degenerate_and_zero_area() -> None:
    """Pole of an L lies in its thick part; tiny or zero-area rings do not crash."""
    l_shape = [(0.0, 0.0), (6000.0, 0.0), (6000.0, 2000.0), (2000.0, 2000.0), (2000.0, 6000.0), (0.0, 6000.0)]
    x, y = polylabel(l_shape, 50)
    assert 0 < x < 2000
    assert 0 < y < 2000
    assert polylabel([(0.0, 0.0), (30.0, 0.0), (30.0, 30.0)], 50) == (0.0, 0.0)
    bowtie = [(0.0, 0.0), (1000.0, 1000.0), (1000.0, 0.0), (0.0, 1000.0)]
    assert len(polylabel(bowtie, 50)) == 2


def test_intervals_and_rounding() -> None:
    """Union merges touching spans; subtract splits; Math.round rounds halves up."""
    assert union([(5.0, 8.0), (0.0, 5.0), (10.0, 12.0)]) == [(0.0, 8.0), (10.0, 12.0)]
    assert subtract([(0.0, 10.0)], [(2.0, 3.0), (9.0, 12.0), (-5.0, -1.0)]) == [(0.0, 2.0), (3.0, 9.0)]
    assert (js_round(2.5), js_round(-2.5), js_round(1.49)) == (3, -2, 1)


def test_clip_to_band__inside_parallel_outside_crossing() -> None:
    """Liang-Barsky clip of a segment against a wall band."""
    f = Frame(0.0, 0.0, 1.0, 0.0, 1000.0)
    assert clip_to_band(f, (-100.0, 0.0), (1100.0, 0.0), 50, 0) == pytest.approx((100 / 1200, 1100 / 1200))
    assert clip_to_band(f, (0.0, 200.0), (1000.0, 200.0), 50, 0) is None
    assert clip_to_band(f, (500.0, -500.0), (500.0, 500.0), 50, 0) == pytest.approx((0.45, 0.55))
    assert clip_to_band(f, (2000.0, -500.0), (2500.0, 500.0), 50, 0) is None
    assert clip_to_band(f, (500.0, 100.0), (2500.0, 300.0), 50, 0) is None


def test_furniture_factory__box_around_centre() -> None:
    """Odd sizes keep the requested width."""
    f = furniture(1, "chair", (100, 100), (501, 400))
    assert f["boundingBox"]["max"]["x"] - f["boundingBox"]["min"]["x"] == 501
