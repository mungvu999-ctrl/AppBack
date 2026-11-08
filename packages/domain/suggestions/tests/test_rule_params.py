"""Rule table: sha stability and completeness (contract §5, §6)."""

import json

import pytest
from pydantic import ValidationError

from packages.domain.suggestions.model import PARAMS_BY_KIND
from packages.domain.suggestions.rules import RULES
from packages.domain.suggestions.ruleset import RULE_PARAMS_PATH, load_ruleset, ruleset_from_text
from packages.testing.factories.suggestions import ruleset_with

CONTRACT_RULES = {
    "R1", "R2", "R3", "R4", "C1", "C2", "F1", "F2", "F3", "P1", "P2", "P3",
    "D1", "D2", "D3", "D4", "D5", "L1", "AID",
}  # fmt: skip


def test_ruleset_sha_stable_crlf_lf() -> None:
    """Line endings and indentation do not change the sha; a parameter change does."""
    text = RULE_PARAMS_PATH.read_text(encoding="utf-8")
    lf = ruleset_from_text(text.replace("\r\n", "\n"))
    crlf = ruleset_from_text(text.replace("\r\n", "\n").replace("\n", "\r\n"))
    reindented = ruleset_from_text(json.dumps(json.loads(text), indent=4))
    assert lf.sha == crlf.sha == reindented.sha == load_ruleset().sha
    assert len(lf.sha) == 64
    bumped = ruleset_with(lambda raw: raw["rules"]["R1"]["params"].update(heightMm=100))
    assert bumped.sha != lf.sha


def test_every_rule_has_version_kind_name_vi_overridable() -> None:
    """Every contract rule is in the table and the registry; overridable keys are params of the rule's kind."""
    rs = load_ruleset()
    assert set(rs.rules) == CONTRACT_RULES == set(RULES)
    for rule_id, spec in rs.rules.items():
        assert spec.version >= 1, rule_id
        assert spec.kind in PARAMS_BY_KIND, rule_id
        assert spec.name_vi.strip(), rule_id
        assert spec.overridable, rule_id
        fields = {f.alias for f in PARAMS_BY_KIND[spec.kind].model_fields.values()}
        assert set(spec.overridable) <= fields, rule_id


def test_major_since_ahead_of_version__rejected() -> None:
    """`majorSince` past the current version is a broken table."""
    with pytest.raises(ValidationError, match="majorSince"):
        ruleset_with(lambda raw: raw["rules"]["C1"].update(majorSince=2))
