"""Shared rule parameter table (HOP-DONG-DU-LIEU-LOP-DE-XUAT §5): `rule_params.json` + its sha.

The sha is taken over the canonical JSON of the parsed file (sorted keys, compact, UTF-8), so CRLF/LF or
re-indentation never changes it; only a real parameter change does.
"""

from __future__ import annotations

import hashlib
import json
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from packages.domain.model_export.snapshot import canonical_json
from packages.domain.spatial.model import DomainModel

RULE_PARAMS_PATH: Final = Path(__file__).with_name("rule_params.json")

Kind = Literal[
    "skirting",
    "wallTile",
    "cornice",
    "casing",
    "sash",
    "railing",
    "cabinetRun",
    "curtain",
    "rug",
    "ceilingLight",
    "item",
    "modelPick",
]
"""The twelve suggestion kinds of contract §2.2."""

RuleId = Annotated[StrictStr, Field(pattern=r"^[A-Z][A-Z0-9]{0,5}$")]
ParamKey = Annotated[StrictStr, Field(pattern=r"^[a-z][A-Za-z0-9]{0,39}$")]


class RuleSpec(DomainModel):
    """One rule of the table: `version` grows on every change; `majorSince` is the first version of the
    current major line (a stored `ruleVersion` below it is a major bump, handled through `migrate`)."""

    version: Annotated[StrictInt, Field(ge=1)]
    major_since: Annotated[StrictInt, Field(ge=1)] = 1
    kind: Kind
    name_vi: Annotated[StrictStr, Field(min_length=1)]
    editor: Literal["render", "mirror", "list"]
    params: dict[str, Any]
    overridable: tuple[ParamKey, ...]
    migrate: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    """Major bump only: `{regex: replacement}` turning an old id into the new id (`re.fullmatch` + `expand`)."""

    @model_validator(mode="after")
    def _major_not_ahead(self) -> RuleSpec:
        """`majorSince` cannot point past the current version."""
        if self.major_since > self.version:
            raise ValueError("majorSince lớn hơn version")
        return self


class Ruleset(DomainModel):
    """The whole table plus its sha (`rulesetSha` of `suggestionsMeta`)."""

    ruleset: Annotated[StrictStr, Field(min_length=1)]
    rules: dict[RuleId, RuleSpec]
    sha: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


def ruleset_from_text(text: str) -> Ruleset:
    """Parse the JSON text of a rule table and attach the sha of its canonical form."""
    raw: dict[str, Any] = json.loads(text)
    sha = hashlib.sha256(canonical_json(raw)).hexdigest()
    return Ruleset.model_validate({**raw, "sha": sha})


@cache
def load_ruleset(path: Path = RULE_PARAMS_PATH) -> Ruleset:
    """The rule table shipped with the package (cached; the file is immutable at runtime)."""
    return ruleset_from_text(path.read_text(encoding="utf-8"))
