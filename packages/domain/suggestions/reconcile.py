"""`reconcile`: regenerate the suggestion layer of one floor and merge the user's state back (contract §3).

Pure and deterministic: same inputs → same list, sorted by id. The user owns `state` and `override`; the rules own
everything else. Records the rules stop producing are marked stale, never dropped, unless they hold no user data and
the rule table changed (or the per-floor cap forces it).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from packages.domain.spatial.model import SpatialLayer
from packages.domain.suggestions import messages
from packages.domain.suggestions.model import Stale, State, Suggestion, SuggestionsMeta
from packages.domain.suggestions.rules import RULES
from packages.domain.suggestions.rules.base import Draft, Note, RuleInput
from packages.domain.suggestions.ruleset import Ruleset, RuleSpec

SUGGESTIONS_CAP: Final = 2000
"""Records per floor (contract §3 step 5)."""


class SuggestionCapError(ValueError):
    """Even after purging stale records without user data the floor holds more than the cap (writer → 422)."""


class UnknownSuggestionError(ValueError):
    """`suggestionState` names an id the floor does not have, or an override key the rule does not allow (→ 422)."""


@dataclass(frozen=True)
class Reconciled:
    """New list (sorted by id), its meta, and the notes the writer logs."""

    suggestions: list[Suggestion]
    meta: SuggestionsMeta
    notes: list[Note]


def _migrated_id(prev: Suggestion, spec: RuleSpec) -> str | None:
    """New id of a record from an older major line, through the rule's `migrate` patterns (first match wins)."""
    for pattern, template in spec.migrate.items():
        match = re.fullmatch(pattern, prev.id)
        if match:
            return match.expand(template)
    return None


def _allowed_override(prev: Suggestion, spec: RuleSpec, notes: list[Note]) -> dict[str, Any] | None:
    """Override keys still listed in `overridable`; dropped ones are logged."""
    if not prev.override:
        return None
    dropped = sorted(k for k in prev.override if k not in spec.overridable)
    if dropped:
        notes.append(Note(prev.rule, prev.id, "log", messages.dropped_override(dropped)))
    kept = {k: v for k, v in prev.override.items() if k in spec.overridable}
    return kept or None


def _merge(
    draft: Draft, rule: str, spec: RuleSpec, prev: Suggestion | None, revision: int, notes: list[Note]
) -> Suggestion:
    """Step 2/3: fresh params and version; the user's state and override carried from `prev` when there is one."""
    record: dict[str, Any] = {
        "id": draft.id,
        "rule": rule,
        "rule_version": spec.version,
        "kind": draft.kind,
        "host_ref": draft.host_ref,
        "params": draft.params,
        "created_revision": revision,
    }
    if prev is not None:
        override = _allowed_override(prev, spec, notes)
        state: State = prev.state
        if state == "overridden" and override is None:
            state = "active"
        record.update(
            created_revision=prev.created_revision, state=state, override=override, adopted_as=prev.adopted_as
        )
    return Suggestion.model_validate(record)


def _stale_reason(
    prev: Suggestion, ruleset: Ruleset, host_ids: frozenset[str], why: Mapping[tuple[str, str], str]
) -> str:
    """Step 4: the most specific reason a record is no longer produced."""
    if prev.rule not in ruleset.rules:
        return messages.RULE_GONE
    if prev.rule_version < ruleset.rules[prev.rule].major_since:
        return messages.MAJOR_STALE
    if any(i not in host_ids for i in prev.host_ref.ids()):
        return messages.HOST_GONE
    for host in (prev.id, *prev.host_ref.ids(), "/".join(prev.host_ref.ids())):
        if (prev.rule, host) in why:
            return why[(prev.rule, host)]
    return messages.NO_LONGER


def _major_stale(prev: Suggestion, revision: int) -> Suggestion:
    """A record of an older major line holding user data, rule without `migrate`: kept as is, stale "xem lại"."""
    since = prev.stale.since if prev.stale else revision
    return prev.model_copy(update={"stale": Stale(since=since, reason_vi=messages.MAJOR_STALE)})


def _purge(items: list[Suggestion], limit: int | None, reason: str, notes: list[Note]) -> list[Suggestion]:
    """Drop stale records without user data, oldest first: all of them (`limit=None`) or until `limit` remain."""
    victims = sorted(
        (s for s in items if s.stale is not None and not s.has_user_data()),
        key=lambda s: (s.stale.since if s.stale else 0, s.id),
    )
    if limit is not None:
        victims = victims[: max(len(items) - limit, 0)]
    notes.extend(Note(s.rule, s.id, "log", reason) for s in victims)
    gone = {s.id for s in victims}
    return [s for s in items if s.id not in gone]


def reconcile(
    prev: Sequence[Suggestion],
    layer: SpatialLayer,
    ruleset: Ruleset,
    concept: Mapping[str, Any] | None = None,
    *,
    revision: int,
    adopted: Mapping[str, str] | None = None,
    prev_meta: SuggestionsMeta | None = None,
    cap: int = SUGGESTIONS_CAP,
) -> Reconciled:
    """Run every rule in order and merge with `prev` (contract §3 steps 1-6 and the version-bump policy).

    `revision` is the layer revision being written (new records get it as `createdRevision`, stale ones as `since`).
    `adopted` maps each `furniture[].origin.suggestionId` of the layer to that furniture id (the writer computes it):
    those records become `adopted` with `adoptedAs` = the furniture, adopted ones no longer in it become `disabled`.
    `prev_meta` tells whether the rule table changed since the last run (only then stale records without user data are
    purged).
    """
    notes: list[Note] = []
    old = {s.id: s for s in prev}
    carried: dict[str, Suggestion] = {}
    for s in sorted(prev, key=lambda x: x.id):
        spec = ruleset.rules.get(s.rule)
        if spec is not None and s.rule_version < spec.major_since:
            new_id = _migrated_id(s, spec)
            if new_id is not None:
                carried[new_id] = s
    out: dict[str, Suggestion] = {}
    consumed: set[str] = set()
    why: dict[tuple[str, str], str] = {}
    items: list[Suggestion] = []
    for rule, run in RULES.items():
        spec = ruleset.rules.get(rule)
        if spec is None:
            continue
        result = run(RuleInput(layer, tuple(items), concept), spec)
        notes.extend(result.notes)
        why.update(((n.rule, n.host), n.reason_vi) for n in result.notes)
        for draft in result.drafts:
            base = carried.get(draft.id)
            if base is not None:
                consumed.add(base.id)
                if base.id != draft.id:
                    notes.append(Note(rule, base.id, "log", messages.MIGRATED))
            else:
                base = old.get(draft.id)
                if base is not None and base.rule_version < spec.major_since:
                    consumed.add(base.id)
                    if base.has_user_data():
                        # §3 "bản chính" không `migrate`: giữ bản cũ cùng trạng thái người dùng, đánh `stale` để xem
                        # lại; người dùng trả về `active` không điều chỉnh thì lượt sau mới nhận bản sinh mới.
                        out[base.id] = _major_stale(base, revision)
                        notes.append(Note(rule, base.id, "log", messages.MAJOR_STALE))
                        continue
                    base = None
            merged = _merge(draft, rule, spec, base, revision, notes)
            out[merged.id] = merged
            if merged.is_effective():
                items.append(merged)
    host_ids = frozenset(e.id for e in layer.entities())
    for s in sorted(prev, key=lambda x: x.id):
        if s.id in out or s.id in consumed:
            continue
        reason = _stale_reason(s, ruleset, host_ids, why)
        since = s.stale.since if s.stale else revision
        out[s.id] = s.model_copy(update={"stale": Stale(since=since, reason_vi=reason)})
    adopted = adopted or {}
    for sid, s in out.items():
        if sid in adopted:
            if (s.state, s.adopted_as) != ("adopted", adopted[sid]):
                out[sid] = s.model_copy(update={"state": "adopted", "adopted_as": adopted[sid]})
        elif s.state == "adopted":
            out[sid] = s.model_copy(update={"state": "disabled", "adopted_as": None})
            notes.append(Note(s.rule, sid, "log", messages.ADOPTED_GONE))
    result_list = sorted(out.values(), key=lambda s: s.id)
    if prev_meta is not None and prev_meta.ruleset_sha != ruleset.sha:
        result_list = _purge(result_list, None, messages.PURGED, notes)
    if len(result_list) > cap:
        result_list = _purge(result_list, cap, messages.CAP_PURGED, notes)
        if len(result_list) > cap:
            raise SuggestionCapError(f"{len(result_list)} đề xuất > trần {cap}")
    meta = SuggestionsMeta(ruleset=ruleset.ruleset, ruleset_sha=ruleset.sha, basis_revision=revision)
    return Reconciled(result_list, meta, notes)


def apply_state(current: Sequence[Suggestion], rows: Iterable[Mapping[str, Any]], ruleset: Ruleset) -> list[Suggestion]:
    """Apply the sparse full state of body #35 `suggestionState` (`[{id, state, override?}]`).

    Records not listed go back to `active` without override (the list is the whole sparse state). Unknown ids or
    override keys outside the rule's `overridable` raise `UnknownSuggestionError`. `adoptedAs` is kept only while the
    record stays adopted.
    """
    by_id = {s.id: s for s in current}
    sent: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        sid = row.get("id")
        if not isinstance(sid, str) or sid not in by_id:
            raise UnknownSuggestionError(f"không có đề xuất {sid}")
        override = row.get("override") or {}
        spec = ruleset.rules.get(by_id[sid].rule)
        bad = sorted(k for k in override if spec is None or k not in spec.overridable)
        if bad:
            raise UnknownSuggestionError(f"{sid}: khoá điều chỉnh không được phép: {', '.join(bad)}")
        sent[sid] = row
    out = []
    for s in current:
        row = sent.get(s.id, {})
        state = row.get("state", "active")
        update = {"state": state, "override": row.get("override") or None}
        if state != "adopted":
            update["adopted_as"] = None
        out.append(Suggestion.model_validate({**s.model_dump(), **update}))
    return out
