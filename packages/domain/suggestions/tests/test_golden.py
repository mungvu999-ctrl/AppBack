"""Golden mirror cases for the FE (`suggestionMirror.test.ts`): R1, R4, C2, F1, L1 on two bench plans.

The FE re-implements these rules for live preview and must produce exactly `tests/data/<case>.golden.json` from
`tests/data/<case>.layer.json` (contract §5, §6). A change here is a contract change for the FE mirror.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from packages.domain.spatial.model import SpatialLayer
from packages.domain.suggestions.rules import RULES
from packages.domain.suggestions.rules.base import RuleInput
from packages.domain.suggestions.ruleset import load_ruleset

DATA = Path(__file__).with_name("data")
MAX_BYTES = 200_000


def mirror(layer: SpatialLayer, rules: list[str]) -> list[dict[str, Any]]:
    """Drafts of the mirrored rules in the golden file's shape, sorted by id."""
    rs = load_ruleset()
    out = []
    for rule in rules:
        for d in RULES[rule](RuleInput(layer), rs.rules[rule]).drafts:
            host = d.host_ref.model_dump(mode="json", by_alias=True, exclude_none=True)
            out.append(
                {"id": d.id, "rule": rule, "ruleVersion": rs.rules[rule].version, "kind": d.kind, "hostRef": host,
                 "params": d.params}
            )  # fmt: skip
    return sorted(out, key=lambda s: s["id"])


@pytest.mark.parametrize("case", ["ch-04", "ch-06"])
def test_golden_mirror_case__matches(case: str) -> None:
    """Rules reproduce the stored golden suggestions; files stay small enough for the FE test."""
    golden = json.loads((DATA / f"{case}.golden.json").read_text(encoding="utf-8"))
    layer_path = DATA / golden["layer"]
    assert layer_path.stat().st_size <= MAX_BYTES
    assert (DATA / f"{case}.golden.json").stat().st_size <= MAX_BYTES
    layer = SpatialLayer.model_validate_json(layer_path.read_text(encoding="utf-8"))
    assert golden["rulesetSha"] == load_ruleset().sha
    assert mirror(layer, golden["rules"]) == golden["suggestions"]
    assert {s["rule"] for s in golden["suggestions"]} >= {"R1", "C2", "F1", "L1"}
