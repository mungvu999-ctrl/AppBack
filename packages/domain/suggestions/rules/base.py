"""Types shared by every rule: drafts, notes, rule input/output."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import JsonValue

from packages.domain.spatial.model import SpatialLayer
from packages.domain.suggestions.model import HostRef, Suggestion
from packages.domain.suggestions.ruleset import Kind, RuleSpec


@dataclass(frozen=True)
class Draft:
    """A suggestion as a rule produces it, before user state is merged in."""

    id: str
    kind: Kind
    host_ref: HostRef
    params: dict[str, JsonValue]


@dataclass(frozen=True)
class Note:
    """Something the writer should log: a skipped/degraded host, or a reconcile event (`log`)."""

    rule: str
    host: str
    status: Literal["skipped", "degraded", "log"]
    reason_vi: str


@dataclass(frozen=True)
class RuleInput:
    """What a rule sees: the layer, earlier effective items and the selected design concept (AID)."""

    layer: SpatialLayer
    items: tuple[Suggestion, ...] = ()
    concept: Mapping[str, Any] | None = None


@dataclass
class RuleOutput:
    """Drafts plus notes of one rule run."""

    drafts: list[Draft] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)


Rule = Callable[[RuleInput, RuleSpec], RuleOutput]


def pending(_: RuleInput, __: RuleSpec) -> RuleOutput:
    """Registered rule whose geometry is not implemented yet: produces nothing (never raises)."""
    return RuleOutput()
