"""`reconcile`: determinism, user state across reruns, stale handling, version bumps, cap (contract §3, §6)."""

from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import JsonValue

from packages.domain.model_export.snapshot import canonical_json
from packages.domain.spatial.model import SpatialLayer
from packages.domain.suggestions import messages
from packages.domain.suggestions.model import HostRef, Stale, Suggestion, effective_params, suggestions_to_json
from packages.domain.suggestions.reconcile import (
    Reconciled,
    SuggestionCapError,
    UnknownSuggestionError,
    apply_state,
    reconcile,
)
from packages.domain.suggestions.rules import RULES
from packages.domain.suggestions.rules.base import Draft, RuleInput, RuleOutput
from packages.domain.suggestions.ruleset import Ruleset, RuleSpec, load_ruleset
from packages.testing.factories.suggestions import flat_layer, flat_parts, layer, ruleset_with, sid

RS = load_ruleset()
WIN = sid("D", 2)
C1, C2 = f"DRESS-C1-{WIN}", f"DRESS-C2-{WIN}"
SKIRT = f"DRESS-R1-{sid('R', 1)}-{sid('W', 1)}"
LAMP = f"DRESS-L1-{sid('R', 1)}"


def run(
    lay: SpatialLayer, prev: Sequence[Suggestion] = (), rs: Ruleset = RS, revision: int = 1, **kw: Any
) -> Reconciled:
    """`reconcile` with the shipped table by default."""
    return reconcile(prev, lay, rs, revision=revision, **kw)


def get(result: Reconciled, sid_: str) -> Suggestion:
    """One record by id."""
    return next(s for s in result.suggestions if s.id == sid_)


def no_window() -> SpatialLayer:
    """The fixture plan without its window."""
    parts = flat_parts()
    parts["openings"] = parts["openings"][:1]
    return layer(**parts)


def with_state(items: Sequence[Suggestion], *rows: dict[str, Any]) -> list[Suggestion]:
    """User state applied the way the writer does."""
    return apply_state(items, rows, RS)


def test_same_inputs_twice__byte_identical() -> None:
    """Same inputs → byte-identical canonical JSON; rerunning on its own output changes nothing."""
    a = run(flat_layer(), revision=3)
    b = run(flat_layer(), revision=3)
    assert canonical_json(suggestions_to_json(a.suggestions)) == canonical_json(suggestions_to_json(b.suggestions))
    again = run(flat_layer(), a.suggestions, revision=3)
    assert again.suggestions == a.suggestions
    assert a.meta.model_dump(by_alias=True) == {"ruleset": "2026.11-1", "rulesetSha": RS.sha, "basisRevision": 3}
    assert [s.id for s in a.suggestions] == sorted(s.id for s in a.suggestions)


def test_input_order_shuffled__same_ids_and_order() -> None:
    """Entity order in the layer and record order in `prev` do not matter."""
    parts = flat_parts()
    shuffled = layer(**{k: list(reversed(v)) for k, v in parts.items()})
    base = run(flat_layer())
    other = run(shuffled, list(reversed(base.suggestions)), revision=1)
    assert [s.id for s in other.suggestions] == [s.id for s in base.suggestions]
    assert other.suggestions == base.suggestions


def test_param_change__ids_unchanged() -> None:
    """Moving the window or changing a table value keeps every id; only params change."""
    base = run(flat_layer())
    parts = flat_parts()
    parts["openings"][1]["offsetMm"] = 1200
    moved = run(layer(**parts), base.suggestions, revision=2)
    taller = run(
        flat_layer(), base.suggestions, ruleset_with(lambda r: r["rules"]["R1"]["params"].update(heightMm=100))
    )
    assert [s.id for s in moved.suggestions] == [s.id for s in base.suggestions] == [s.id for s in taller.suggestions]
    assert get(taller, SKIRT).params["heightMm"] == 100
    assert get(moved, LAMP).created_revision == 1


def test_rerun_keeps_disabled_and_override() -> None:
    """User state and override survive a rerun on a changed layer; createdRevision stays the first one."""
    base = run(flat_layer(), revision=3)
    touched = with_state(
        base.suggestions,
        {"id": C1, "state": "disabled"},
        {"id": SKIRT, "state": "overridden", "override": {"heightMm": 120}},
    )
    parts = flat_parts()
    parts["openings"][1]["widthMm"] = 1200
    again = run(layer(**parts), touched, revision=4)
    assert get(again, C1).state == "disabled"
    assert (get(again, SKIRT).state, get(again, SKIRT).override) == ("overridden", {"heightMm": 120})
    assert get(again, SKIRT).created_revision == 3


def test_override_key_wins__generated_param_updates_below() -> None:
    """A table change updates params under the override; the effective value stays the user's."""
    base = run(flat_layer())
    touched = with_state(base.suggestions, {"id": SKIRT, "state": "overridden", "override": {"heightMm": 120}})
    rs = ruleset_with(lambda r: r["rules"]["R1"]["params"].update(heightMm=100, thicknessMm=15))
    again = get(run(flat_layer(), touched, rs, revision=2), SKIRT)
    assert again.params["heightMm"] == 100
    assert effective_params(again)["heightMm"] == 120
    assert effective_params(again)["thicknessMm"] == 15


def test_rule_stops__marked_stale_not_dropped() -> None:
    """Window removed → its records stay, stale since that revision; `since` does not move on later runs."""
    base = run(flat_layer())
    gone = run(no_window(), base.suggestions, revision=2)
    for sid_ in (C1, C2, f"DRESS-D2-{WIN}-L"):
        assert get(gone, sid_).stale == Stale(since=2, reason_vi=messages.HOST_GONE)
    later = run(no_window(), gone.suggestions, revision=5)
    assert get(later, C1).stale == Stale(since=2, reason_vi=messages.HOST_GONE)
    assert not get(later, C1).is_effective()


def test_host_restored__suggestion_reactivates_with_state() -> None:
    """Undoing the deletion revives the same id with the user's state and no stale mark."""
    base = run(flat_layer())
    off = with_state(base.suggestions, {"id": C1, "state": "disabled"})
    gone = run(no_window(), off, revision=2)
    back = run(flat_layer(), gone.suggestions, revision=3)
    assert get(back, C1).stale is None
    assert get(back, C1).state == "disabled"
    assert get(back, C2).stale is None


def test_adopted_without_furniture__coerced_disabled() -> None:
    """Adopted stays adopted while its furniture carries the origin; deleted furniture → disabled, logged."""
    base = run(flat_layer())
    adopted = [
        s.model_copy(update={"state": "adopted", "adopted_as": sid("F", 9)}) if s.id == LAMP else s
        for s in base.suggestions
    ]
    kept = run(flat_layer(), adopted, revision=2, adopted={LAMP: sid("F", 9)})
    assert (get(kept, LAMP).state, get(kept, LAMP).adopted_as) == ("adopted", sid("F", 9))
    dropped = run(flat_layer(), adopted, revision=3)
    assert (get(dropped, LAMP).state, get(dropped, LAMP).adopted_as) == ("disabled", None)
    assert [(n.host, n.reason_vi) for n in dropped.notes if n.status == "log"] == [(LAMP, messages.ADOPTED_GONE)]


def test_furniture_origin__marks_suggestion_adopted() -> None:
    """A layer item carrying `origin.suggestionId` adopts that record (`adoptedAs` = the item), whatever its state."""
    base = run(flat_layer())
    off = with_state(base.suggestions, {"id": LAMP, "state": "disabled"})
    got = run(flat_layer(), off, revision=2, adopted={LAMP: sid("F", 7)})
    assert (get(got, LAMP).state, get(got, LAMP).adopted_as) == ("adopted", sid("F", 7))
    again = run(flat_layer(), got.suggestions, revision=3, adopted={LAMP: sid("F", 7)})
    assert get(again, LAMP) == get(got, LAMP)


def test_minor_bump__unknown_override_keys_dropped_and_logged() -> None:
    """Version 2 of R1 no longer allows thickness: that key goes (logged); a record left without keys is active."""
    base = run(flat_layer())
    other = f"DRESS-R1-{sid('R', 1)}-{sid('W', 3)}"
    touched = with_state(
        base.suggestions,
        {"id": SKIRT, "state": "overridden", "override": {"heightMm": 120, "thicknessMm": 15}},
        {"id": other, "state": "overridden", "override": {"thicknessMm": 15}},
    )

    def bump(raw: dict[str, Any]) -> None:
        """R1 v2 with a narrower override list."""
        raw["rules"]["R1"].update(version=2, overridable=["heightMm", "material"])

    again = run(flat_layer(), touched, ruleset_with(bump), revision=2)
    assert (get(again, SKIRT).rule_version, get(again, SKIRT).override) == (2, {"heightMm": 120})
    assert (get(again, other).state, get(again, other).override) == ("active", None)
    logged = {n.host: n.reason_vi for n in again.notes if n.status == "log"}
    assert logged == {
        SKIRT: messages.dropped_override(["thicknessMm"]),
        other: messages.dropped_override(["thicknessMm"]),
    }


def test_major_bump__migrate_map_carries_state() -> None:
    """Major bump: `migrate` moves state to the new id; same id without a mapping resets; unmatched old ids go stale."""
    base = run(flat_layer())
    old_c1 = get(base, C1).model_copy(update={"id": f"DRESS-casing-{WIN}", "state": "disabled"})
    orphan = get(base, C1).model_copy(update={"id": "DRESS-casing-old", "state": "disabled"})
    c2 = get(base, C2).model_copy(update={"state": "disabled"})
    curtain = f"DRESS-D2-{WIN}-L"
    prev = [s if s.id != curtain else s.model_copy(update={"state": "disabled"}) for s in base.suggestions]
    prev = [s for s in prev if s.id not in (C1, C2)] + [old_c1, orphan, c2]

    def bump(raw: dict[str, Any]) -> None:
        """C1 and C2 move to a new major line; only C1 maps its old ids."""
        raw["rules"]["C1"].update(version=2, majorSince=2, migrate={r"^DRESS-casing-(D-.+)$": r"DRESS-C1-\1"})
        raw["rules"]["C2"].update(version=2, majorSince=2)
        raw["rules"]["D2"].update(version=2, majorSince=2, migrate={r"^(DRESS-D2-.+)$": r"\1"})
        raw["rules"]["L1"].update(version=2, majorSince=2)

    again = run(flat_layer(), prev, ruleset_with(bump), revision=2)
    ids = {s.id for s in again.suggestions}
    assert f"DRESS-casing-{WIN}" not in ids
    assert (get(again, C1).state, get(again, C1).rule_version) == ("disabled", 2)
    assert get(again, C2).state == "active"
    assert (get(again, curtain).state, get(again, LAMP).rule_version) == ("disabled", 2)
    assert get(again, "DRESS-casing-old").stale == Stale(since=2, reason_vi=messages.MAJOR_STALE)
    logged = {(n.host, n.reason_vi) for n in again.notes if n.status == "log"}
    assert logged == {(f"DRESS-casing-{WIN}", messages.MIGRATED), (C2, messages.MAJOR_RESET)}


def stale_record(n: int, since: int, **over: Any) -> Suggestion:
    """A stale C1 record for a window that no longer exists."""
    return Suggestion.model_validate(
        {
            "id": f"DRESS-C1-{sid('D', 90 + n)}",
            "rule": "C1",
            "ruleVersion": 1,
            "kind": "casing",
            "hostRef": {"opening": sid("D", 90 + n)},
            "params": {"widthMm": 70, "thicknessMm": 16, "material": "library:preset-white"},
            "createdRevision": 1,
            "stale": {"since": since, "reasonVi": messages.HOST_GONE},
            **over,
        }
    )


def test_cap__purges_only_stale_without_user_data() -> None:
    """Over the cap: stale records without user data go, oldest first; user data is never purged."""
    live = len(run(flat_layer()).suggestions)
    prev = [stale_record(1, 3), stale_record(2, 1), stale_record(3, 2), stale_record(4, 0, state="disabled")]
    capped = run(flat_layer(), prev, revision=4, cap=live + 2)
    ids = {s.id for s in capped.suggestions}
    assert {f"DRESS-C1-{sid('D', 91)}", f"DRESS-C1-{sid('D', 94)}"} <= ids
    assert not ids & {f"DRESS-C1-{sid('D', 92)}", f"DRESS-C1-{sid('D', 93)}"}
    assert [n.reason_vi for n in capped.notes if n.status == "log"] == [messages.CAP_PURGED] * 2
    with pytest.raises(SuggestionCapError):
        run(flat_layer(), prev, revision=4, cap=live)


def test_ruleset_change__purges_stale_without_user_data() -> None:
    """Stale records without user data are dropped only when the table sha changes."""
    first = run(flat_layer())
    prev = [*first.suggestions, stale_record(1, 1), stale_record(2, 1, state="disabled")]
    same = run(flat_layer(), prev, revision=2, prev_meta=first.meta)
    assert len(same.suggestions) == len(prev)
    rs = ruleset_with(lambda r: r["rules"]["C1"]["params"].update(widthMm=80))
    changed = run(flat_layer(), prev, rs, revision=2, prev_meta=first.meta)
    assert {s.id for s in changed.suggestions} == {s.id for s in prev} - {f"DRESS-C1-{sid('D', 91)}"}


def test_stale_reasons__rule_gone_and_no_longer() -> None:
    """A rule dropped from the table, or a host that no longer qualifies, explain the stale mark."""
    base = run(flat_layer())
    rs = ruleset_with(lambda r: r["rules"].pop("C2"))
    parts = flat_parts()
    parts["rooms"][0]["usage"] = "loggia"
    again = run(layer(**parts), base.suggestions, rs, revision=2)
    assert get(again, C2).stale == Stale(since=2, reason_vi=messages.RULE_GONE)
    assert get(again, LAMP).stale == Stale(since=2, reason_vi=messages.NO_LONGER)


def test_earlier_items_feed_later_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Later rules see effective `item` records of earlier rules; a disabled one is not effective."""
    seen: list[tuple[str, ...]] = []
    item: dict[str, JsonValue] = {
        "furnitureKind": "bed",
        "modelId": "bed-sf",
        "centre": {"x": 1, "y": 1},
        "sizeMm": [1, 1, 1],
        "rotationDeg": 0,
    }
    bed = f"DRESS-P1-{sid('R', 1)}-bed"

    def p1(_: RuleInput, __: RuleSpec) -> RuleOutput:
        """Fake P1: one bed."""
        return RuleOutput(drafts=[Draft(bed, "item", HostRef(room=sid("R", 1)), item)])

    def p2(inp: RuleInput, __: RuleSpec) -> RuleOutput:
        """Fake P2: records what it saw."""
        seen.append(tuple(s.id for s in inp.items if s.kind == "item"))
        return RuleOutput()

    monkeypatch.setitem(RULES, "P1", p1)
    monkeypatch.setitem(RULES, "P2", p2)
    first = run(flat_layer())
    run(flat_layer(), with_state(first.suggestions, {"id": bed, "state": "disabled"}), revision=2)
    assert seen == [(bed,), ()]


def test_rule_missing_from_table__not_run() -> None:
    """A registered rule absent from the table produces nothing."""
    rs = ruleset_with(lambda r: r["rules"].pop("L1"))
    assert not any(s.rule == "L1" for s in run(flat_layer(), rs=rs).suggestions)


def test_apply_state__sparse_full_state_and_rejections() -> None:
    """Unlisted records return to active; unknown ids and disallowed override keys raise."""
    base = run(flat_layer())
    adopted = with_state(base.suggestions, {"id": LAMP, "state": "adopted"})
    adopted = [s.model_copy(update={"adopted_as": sid("F", 9)}) if s.id == LAMP else s for s in adopted]
    reset = with_state(adopted, {"id": C1, "state": "disabled"})
    assert (get_list(reset, LAMP).state, get_list(reset, LAMP).adopted_as) == ("active", None)
    assert get_list(reset, C1).state == "disabled"
    still = with_state(adopted, {"id": LAMP, "state": "adopted"})
    assert get_list(still, LAMP).adopted_as == sid("F", 9)
    with pytest.raises(UnknownSuggestionError, match="không có"):
        with_state(base.suggestions, {"id": "DRESS-x", "state": "disabled"})
    with pytest.raises(UnknownSuggestionError, match="spansMm"):
        with_state(base.suggestions, {"id": SKIRT, "state": "overridden", "override": {"spansMm": 1}})
    no_rule = apply_state(base.suggestions, [], ruleset_with(lambda r: r["rules"].pop("R1")))
    assert len(no_rule) == len(base.suggestions)
    with pytest.raises(UnknownSuggestionError, match="heightMm"):
        apply_state(
            base.suggestions,
            [{"id": SKIRT, "state": "overridden", "override": {"heightMm": 1}}],
            ruleset_with(lambda r: r["rules"].pop("R1")),
        )


def get_list(items: Sequence[Suggestion], sid_: str) -> Suggestion:
    """One record of a plain list."""
    return next(s for s in items if s.id == sid_)
