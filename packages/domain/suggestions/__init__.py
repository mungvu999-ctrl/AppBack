"""Suggestion layer (M2, HOP-DONG-DU-LIEU-LOP-DE-XUAT): rule table, records and the pure `reconcile`."""

from packages.domain.suggestions.model import (
    Suggestion,
    SuggestionsMeta,
    check_override,
    document_keys,
    effective_params,
    suggestions_from_json,
    suggestions_to_json,
)
from packages.domain.suggestions.reconcile import (
    SUGGESTIONS_CAP,
    Reconciled,
    SuggestionCapError,
    UnknownSuggestionError,
    apply_state,
    flag_collisions,
    reconcile,
)
from packages.domain.suggestions.rules.base import Note
from packages.domain.suggestions.rules.placement import CollisionNote, check_collisions, model_origin
from packages.domain.suggestions.ruleset import Ruleset, load_ruleset

__all__ = [
    "SUGGESTIONS_CAP",
    "CollisionNote",
    "Note",
    "Reconciled",
    "Ruleset",
    "Suggestion",
    "SuggestionCapError",
    "SuggestionsMeta",
    "UnknownSuggestionError",
    "apply_state",
    "check_collisions",
    "check_override",
    "document_keys",
    "effective_params",
    "flag_collisions",
    "load_ruleset",
    "model_origin",
    "reconcile",
    "suggestions_from_json",
    "suggestions_to_json",
]
