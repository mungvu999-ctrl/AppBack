"""Rule registry: fixed run order, one pure function per rule id.

A rule reads the layer (never changes it) plus the effective `item` suggestions of the rules before it, and returns
drafts and notes. Notes record "skipped/degraded, why" so the writer can log them and `reconcile` can explain a
stale record. P1-P3, D1, D3-D5, F2, F3, R2, R3 and AID are registered but produce nothing yet: they wait for the
collision model (next slice).
"""

from __future__ import annotations

from typing import Final

from packages.domain.suggestions.rules.base import Rule, pending
from packages.domain.suggestions.rules.furniture import f1_model_pick
from packages.domain.suggestions.rules.openings import c1_casing, c2_sash, d2_curtain
from packages.domain.suggestions.rules.rooms import l1_ceiling_light
from packages.domain.suggestions.rules.walls import r1_skirting, r4_railing

RULES: Final[dict[str, Rule]] = {
    "R1": r1_skirting,
    "R2": pending,
    "R3": pending,
    "R4": r4_railing,
    "C1": c1_casing,
    "C2": c2_sash,
    "F1": f1_model_pick,
    "F2": pending,
    "F3": pending,
    "P1": pending,
    "P2": pending,
    "P3": pending,
    "D1": pending,
    "D2": d2_curtain,
    "D3": pending,
    "D4": pending,
    "D5": pending,
    "L1": l1_ceiling_light,
    "AID": pending,
}
"""Rules in run order (dict order is the order `reconcile` runs them)."""
