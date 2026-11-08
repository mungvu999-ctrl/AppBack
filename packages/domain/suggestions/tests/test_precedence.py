"""Who wins (contract §3): the document over rules, model pick and reclassification limits, B3 items ignored."""

from typing import Any

import pytest

from packages.domain.suggestions import messages
from packages.domain.suggestions.reconcile import reconcile
from packages.domain.suggestions.rules import RULES
from packages.domain.suggestions.rules.base import RuleInput, RuleOutput
from packages.domain.suggestions.rules.furniture import missing_primary_slots
from packages.domain.suggestions.ruleset import load_ruleset
from packages.testing.factories.suggestions import flat_layer, furniture, room, sid

RS = load_ruleset()
BATH = room(4, "bathroom", [(9000, 0), (11000, 0), (11000, 2000), (9000, 2000)])
MODEL = {"id": "toilet-x", "src": "/items/toilet-x/model.glb"}


def f1(*items: dict[str, Any]) -> RuleOutput:
    """F1 on the fixture plan plus a bathroom and `items`."""
    return RULES["F1"](RuleInput(flat_layer(rooms=[BATH], furn=list(items))), RS.rules["F1"])


def picks(out: RuleOutput) -> dict[str, tuple[Any, ...]]:
    """Host id → (modelId, scale, kindAs, variantAs)."""
    return {
        d.host_ref.furniture or "": (
            d.params["modelId"],
            d.params["scale"],
            d.params.get("kindAs"),
            d.params.get("variantAs"),
        )
        for d in out.drafts
    }


def in_bath(n: int, kind: str, size: tuple[int, int], **over: Any) -> dict[str, Any]:
    """Item in the bathroom (by `roomId`)."""
    return furniture(n, kind, (10000, 1000), size, roomId=sid("R", 4), **over)


def test_model_pick_only_when_model_absent() -> None:
    """A user-chosen model blocks F1; the earlier pick goes stale with that reason, not deleted."""
    first = reconcile([], flat_layer(rooms=[BATH], furn=[in_bath(1, "sanitaryFixture", (480, 700))]), RS, revision=1)
    pick = f"DRESS-F1-{sid('F', 1)}"
    assert pick in {s.id for s in first.suggestions}
    chosen = flat_layer(rooms=[BATH], furn=[in_bath(1, "sanitaryFixture", (480, 700), model=MODEL)])
    second = reconcile(first.suggestions, chosen, RS, revision=2)
    stale = {s.id: s.stale for s in second.suggestions}[pick]
    assert stale is not None
    assert (stale.since, stale.reason_vi) == (2, messages.HAS_MODEL)


@pytest.mark.parametrize(
    "owner",
    [{"source": "human"}, {"reviewed": True}, {"edited": True}, {"suggested": True}],
)
def test_reclassify_skips_reviewed_edited_human_suggested(owner: dict[str, Any]) -> None:
    """Changing what an item is (chair → sofa) needs an untouched AI item; picking by size within a kind does not."""
    assert picks(f1(furniture(1, "chair", (6500, 1500), (2000, 900))))[sid("F", 1)] == ("sofa-ph", 1.0, None, "sofa")
    out = f1(furniture(1, "chair", (6500, 1500), (2000, 900), **owner))
    assert out.drafts == []
    if "suggested" not in owner:
        assert [n.reason_vi for n in out.notes] == [messages.LOCKED_KIND]
        same_kind = f1(in_bath(2, "sanitaryFixture", (480, 700), **owner))
        assert picks(same_kind)[sid("F", 2)] == ("toilet-sf", 1.0, None, None)


def test_user_furniture_blocks_primary_item() -> None:
    """A real bed fills the bed slot (by kind or variant); small rooms do not want area-gated slots."""
    spec = RS.rules["P1"]
    lay = flat_layer()
    bedroom = lay.rooms[0]
    assert missing_primary_slots(lay, bedroom, spec) == ["bed", "wardrobe"]
    with_bed = flat_layer(furn=[furniture(1, "bed", (2000, 1500), (1600, 2000), roomId=sid("R", 1))])
    assert missing_primary_slots(with_bed, with_bed.rooms[0], spec) == ["wardrobe"]
    by_variant = flat_layer(furn=[furniture(1, "other", (2000, 1500), (1200, 600), variant="wardrobe")])
    assert missing_primary_slots(by_variant, by_variant.rooms[0], spec) == ["bed"]
    small_bath = flat_layer(rooms=[BATH])
    assert missing_primary_slots(small_bath, small_bath.rooms[3], spec) == ["wc", "basin"]
    tiny = flat_layer(rooms=[room(4, "bathroom", [(9000, 0), (10000, 0), (10000, 2000), (9000, 2000)])])
    assert missing_primary_slots(tiny, tiny.rooms[3], spec) == ["wc"]
    assert missing_primary_slots(lay, lay.rooms[2], spec) == ["planter"]


def test_b3_suggested_ignored_as_host_and_obstacle() -> None:
    """B3 items waiting for review are neither F1 hosts nor items that fill a P1 slot."""
    assert f1(in_bath(1, "sanitaryFixture", (480, 700), suggested=True)) == RuleOutput()
    lay = flat_layer(furn=[furniture(1, "bed", (2000, 1500), (1600, 2000), roomId=sid("R", 1), suggested=True)])
    assert missing_primary_slots(lay, lay.rooms[0], RS.rules["P1"]) == ["bed", "wardrobe"]


def test_f1_table__size_rows_notes_and_room_lookup() -> None:
    """Each table row, the refusal notes, and room lookup by centre when `roomId` is absent."""
    out = f1(
        in_bath(1, "sanitaryFixture", (1600, 800)),
        in_bath(2, "sanitaryFixture", (900, 900)),
        in_bath(3, "sanitaryFixture", (1000, 600)),
        in_bath(4, "sanitaryFixture", (300, 300)),
        in_bath(5, "sanitaryFixture", (500, 450)),
        furniture(6, "chair", (6500, 1500), (1900, 900), variant="sofa", source="human", reviewed=True),
        furniture(7, "chair", (6500, 1500), (900, 900)),
        furniture(8, "chair", (6500, 1500), (500, 500)),
        furniture(9, "kitchenCabinet", (10000, 1000), (1000, 600)),
        furniture(10, "kitchenCabinet", (6500, 1500), (1000, 600)),
        furniture(11, "kitchenCabinet", (20000, 20000), (1000, 600)),
        furniture(12, "table", (6500, 1500), (1000, 600)),
    )
    assert picks(out) == {
        sid("F", 2): ("shower-sf", 0.947, None, None),
        sid("F", 3): ("vanity-sf", 0.943, None, None),
        sid("F", 6): ("sofa-ph", 1.0, None, None),
        sid("F", 7): ("armchair-ph", 0.912, None, "armchair"),
        sid("F", 9): ("vanity-sf", 0.943, "sanitaryFixture", "basin"),
    }
    assert {n.host: n.reason_vi for n in out.notes} == {
        sid("F", 1): "hộp cỡ bồn tắm, gói chưa có mẫu bồn tắm CC0/CC-BY",
        sid("F", 4): "hộp quá nhỏ để phân loại",
        sid("F", 5): messages.model_too_big("vanity-sf", 54, 10),
    }
    reason = next(d.params["reasonVi"] for d in out.drafts if d.host_ref.furniture == sid("F", 2))
    assert reason == f"hộp 0,90 {messages.TIMES} 0,90 m → vòi sen"
