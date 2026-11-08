"""Suggestion records: validation and wire form."""

from typing import Any

import pytest
from pydantic import ValidationError

from packages.domain.suggestions.model import (
    HostRef,
    Suggestion,
    SuggestionsMeta,
    document_keys,
    effective_params,
    suggestions_from_json,
    suggestions_to_json,
)

ROOM = "R-0000000001"
WALL = "W-0000000001"
SKIRT: dict[str, Any] = {
    "id": f"DRESS-R1-{ROOM}-{WALL}",
    "rule": "R1",
    "ruleVersion": 1,
    "kind": "skirting",
    "hostRef": {"room": ROOM, "wall": WALL},
    "params": {
        "side": "left",
        "spansMm": [[0, 900]],
        "heightMm": 90,
        "thicknessMm": 12,
        "material": "library:preset-white",
    },
    "createdRevision": 2,
}
META = SuggestionsMeta(ruleset="2026.11-1", ruleset_sha="a" * 64, basis_revision=2)


def test_wire_round_trip__camel_case_optional_keys_absent() -> None:
    """Dump → parse gives the same record; absent options stay absent (no null)."""
    s = Suggestion.model_validate(SKIRT)
    wire = suggestions_to_json([s])
    assert wire == [{**SKIRT, "state": "active"}]
    assert suggestions_from_json(wire) == [s]


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"id": "R1-x"}, "pattern"),
        ({"params": {"side": "left"}}, "Field required"),
        ({"override": {"colour": 3}}, "không thuộc skirting"),
        ({"override": {}}, "at least 1"),
        ({"override": {"heightMm": [1]}}, "override"),
        ({"override": {"heightMm": -500}}, "greater than 0"),
        ({"override": {"heightMm": 1.5}}, "valid integer"),
        ({"override": {"material": "x"}}, "pattern"),
        ({"override": {"heightMm": 10_001}}, "vượt 10000 mm: heightMm"),
        ({"adoptedAs": "F-0000000001"}, "adoptedAs"),
        ({"hostRef": {}}, "hostRef rỗng"),
        ({"kind": "lamp"}, "kind"),
    ],
)
def test_invalid_record__rejected(patch: dict[str, Any], message: str) -> None:
    """Id pattern, per-kind params, override keys/values, adoptedAs and hostRef are checked."""
    with pytest.raises(ValidationError, match=message):
        Suggestion.model_validate({**SKIRT, **patch})


def test_effective_params__override_above_params() -> None:
    """`override` wins value by value; untouched params show through."""
    s = Suggestion.model_validate({**SKIRT, "state": "overridden", "override": {"heightMm": 120}})
    assert effective_params(s)["heightMm"] == 120
    assert effective_params(s)["thicknessMm"] == 12
    assert s.has_user_data()
    assert s.is_effective()


def test_document_keys__absent_when_empty() -> None:
    """An empty layer adds no key, so old documents stay byte-identical."""
    assert document_keys([], META) == {}
    keys = document_keys([Suggestion.model_validate(SKIRT)], META)
    assert keys["suggestionsMeta"] == {"ruleset": "2026.11-1", "rulesetSha": "a" * 64, "basisRevision": 2}
    assert keys["suggestions"][0]["id"] == SKIRT["id"]


def test_host_ref_ids__layer_entities_only() -> None:
    """`ids()` lists layer hosts, not a host suggestion."""
    ref = HostRef(room=ROOM, suggestion="DRESS-P1-x")
    assert ref.ids() == (ROOM,)
