"""Rule registry: fixed run order, one pure function per rule id.

A rule reads the layer (never changes it) plus the effective suggestions of the rules before it (P2 sees P1's bed,
P3 sees D1's rug, D3 sees P2's tables, F3 sees F2's runs), and returns drafts and notes. Notes record "skipped or
degraded, why" so the writer can log them and `reconcile` can explain a stale record. AID waits for the concept
wiring and produces nothing yet.
"""

from __future__ import annotations

from typing import Final

from packages.domain.suggestions.rules.base import Rule, pending
from packages.domain.suggestions.rules.furniture import f1_model_pick
from packages.domain.suggestions.rules.items import (
    d1_rug,
    d3_vase,
    d4_picture,
    d5_plant,
    p1_primary,
    p2_bedside,
    p3_coffee_table,
)
from packages.domain.suggestions.rules.kitchen import f2_base_run, f3_upper_run
from packages.domain.suggestions.rules.openings import c1_casing, c2_sash, d2_curtain
from packages.domain.suggestions.rules.rooms import l1_ceiling_light
from packages.domain.suggestions.rules.walls import r1_skirting, r2_wall_tile, r3_cornice, r4_railing

RULES: Final[dict[str, Rule]] = {
    "R1": r1_skirting,
    "R2": r2_wall_tile,
    "R3": r3_cornice,
    "R4": r4_railing,
    "C1": c1_casing,
    "C2": c2_sash,
    "F1": f1_model_pick,
    "F2": f2_base_run,
    "F3": f3_upper_run,
    "P1": p1_primary,
    "P2": p2_bedside,
    "D1": d1_rug,
    "P3": p3_coffee_table,
    "D2": d2_curtain,
    "D3": d3_vase,
    "D4": d4_picture,
    "D5": d5_plant,
    "L1": l1_ceiling_light,
    "AID": pending,
}
"""Rules in run order (dict order is the order `reconcile` runs them). D1 runs before P3 so the coffee table can
stand on the rug."""
