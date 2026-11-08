"""Golden cases on three bench plans (`tests/data/<case>.golden.json`).

`suggestions` = the rules the FE mirrors for live preview (R1, R4, C2, F1, L1): the FE must produce exactly these from
`<case>.layer.json` (contract §5, §6); a change here is a contract change for the FE mirror. `serverSuggestions` =
the server-only rules from `reconcile` on an empty list (placement, kitchen runs, tiles, cornice); `collisions` =
`check_collisions` on that result, which must hold no `rule` collision.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from packages.domain.spatial.model import SpatialLayer
from packages.domain.suggestions import check_collisions, reconcile
from packages.domain.suggestions.rules import RULES
from packages.domain.suggestions.rules.base import RuleInput
from packages.domain.suggestions.ruleset import load_ruleset

DATA = Path(__file__).with_name("data")
MAX_BYTES = 200_000
CASES = ["ch-04", "ch-06", "syn-penthouse"]


def shape(id_: str, rule: str, kind: str, host: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """One record in the golden file's shape."""
    version = load_ruleset().rules[rule].version
    return {"id": id_, "rule": rule, "ruleVersion": version, "kind": kind, "hostRef": host, "params": params}


def mirror(layer: SpatialLayer, rules: list[str]) -> list[dict[str, Any]]:
    """Drafts of the mirrored rules, sorted by id."""
    rs = load_ruleset()
    out = []
    for rule in rules:
        for d in RULES[rule](RuleInput(layer), rs.rules[rule]).drafts:
            out.append(
                shape(
                    d.id, rule, d.kind, d.host_ref.model_dump(mode="json", by_alias=True, exclude_none=True), d.params
                )
            )
    return sorted(out, key=lambda s: s["id"])


def load(case: str) -> tuple[dict[str, Any], SpatialLayer]:
    """Golden file and its layer; both small enough for the FE test."""
    path = DATA / f"{case}.golden.json"
    golden = json.loads(path.read_text(encoding="utf-8"))
    layer_path = DATA / golden["layer"]
    assert layer_path.stat().st_size <= MAX_BYTES
    assert path.stat().st_size <= MAX_BYTES
    assert golden["rulesetSha"] == load_ruleset().sha
    return golden, SpatialLayer.model_validate_json(layer_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES)
def test_golden_mirror_case__matches(case: str) -> None:
    """Mirrored rules reproduce the stored golden suggestions."""
    golden, layer = load(case)
    assert mirror(layer, golden["rules"]) == golden["suggestions"]
    assert {s["rule"] for s in golden["suggestions"]} >= {"R1", "C2", "F1", "L1"}


@pytest.mark.parametrize("case", CASES)
def test_golden_server_rules_and_collisions__match(case: str) -> None:
    """Server-only rules and the collision log match; rules leave no collision of their own."""
    golden, layer = load(case)
    res = reconcile([], layer, load_ruleset(), revision=0)
    server = [
        shape(s.id, s.rule, s.kind, s.host_ref.model_dump(mode="json", by_alias=True, exclude_none=True), s.params)
        for s in res.suggestions
        if s.rule in golden["serverRules"]
    ]
    assert server == golden["serverSuggestions"]
    notes = check_collisions(layer, res.suggestions)
    got = [
        {
            "label": c.label,
            "type": c.type,
            "item": c.item,
            **({"other": c.other} if c.other else {}),
            "areaMm2": c.area_mm2,
        }
        for c in notes
    ]
    assert got == golden["collisions"]
    assert all(c.label == "doc" for c in notes)
