# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  section_router.py   [Tier C — section -> solver dispatch gate]
#
#  ROADMAP 4.4. The HARD RULE: a section is solved ONLY by its matching
#  family solver. Families without a solver are gated 'pending' and NEVER
#  silently fall back to the LP solver. This ships in item 1 and immediately
#  stops the BP/BR/HP-notch/pure-notch mis-solving on LP cells, BEFORE any new
#  2nd/3rd-order solver exists. As items 2/3/5 land, the None entries flip to
#  their family's tag.
#
#  route_section returns a SOLVER TAG (a string), not the callable, so this
#  module stays free of heavy imports (filter_synthesis / first_order_solver)
#  and is unit-testable in isolation. The caller maps the tag to a function:
#     'lp'          -> filter_synthesis.synthesize        (12 LP/LPn cells)
#     'first_order' -> first_order_solver.synthesize_first_order
# =====================================================================

from pairing_utils import classify_section

# Family -> solver tag, for 2nd/3rd-order sections. None == no solver yet.
SECTION_SOLVERS = {
    "LP":    "lp",      # unified_solver_v2 (12 LP cells)
    "LPn":   "lp",      # same solver, parallel-C2 notch path
    "HP":    None,      # item 2
    "HPn":   None,      # item 2 (wz < w0 closes here)
    "notch": None,      # item 3 (pure notch, wz ~= w0)
    "BP":    "bp",      # item 5 (VCVS Sallen-Key band-pass)
    # 3rd-order asymmetric band-pass (complex pair + absorbed real pole). Same
    # solver tag as BP -- the cascade stage is still a band-pass section and the
    # whole 'bp' rendering/gain path applies; only the CELL SET differs, and the
    # cells are chosen from the classification (cls['family']) downstream.
    "BP1LP": "bp",      # num ~ s   : 2BP1LP-MFB / -QE
    "BP1HP": "bp",      # num ~ s^2 : 2BP1HP-MFB / -QE
}

# Order-1 LP/HP are realized by the closed-form 1st-order solver (item 1),
# NOT by SECTION_SOLVERS (whose 'LP' entry targets the 2nd/3rd-order cells).
FIRST_ORDER_FAMILIES = {"LP", "HP"}


def route_section(stage, p_bricks, z_bricks, wz_tol=0.05):
    """Classify a section and pick its solver.
    Returns one of:
        ('solve', tag, cls)   -- dispatch to solver `tag` with classification cls
        ('pending', family)   -- no solver for this family yet (show, never LP)
    """
    cls = classify_section(stage, p_bricks, z_bricks, wz_tol)
    if cls["order"] == 1 and cls["family"] in FIRST_ORDER_FAMILIES:
        return ("solve", "first_order", cls)
    tag = SECTION_SOLVERS.get(cls["family"])
    if tag is None:
        return ("pending", cls["family"])
    return ("solve", tag, cls)
