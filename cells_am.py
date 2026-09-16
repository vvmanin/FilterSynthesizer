# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cells_am.py
#  Ackerberg-Mossberg LOW-PASS cell family (FAMILY = "LP-AM").
#
#  FIVE cells over the shared 3-op-amp AM core (see cells_am_core.py and
#  AM_IDEAL_TF_ANALYSIS.md):
#     2LP-AM    LP tapped at out1, input R2 (a->p2). H(0) = -R6/R2.
#     2LP-AM2   LP tapped at out2, input R1 (a->m1). H(0) = -R5/R1.
#               This is the CLASSIC Ackerberg-Mossberg (1974) low-pass, the
#               netlist commercial tools ship. Ideal-exactly equivalent to
#               2LP-AM (same D(s), same element count, gain orthogonal to
#               the poles in both); the two differ only with finite GB
#               (which node the input element loads) -- so BOTH are offered,
#               solved in parallel and ranked together, exactly like the
#               2BP-MFB / 2BP-MFB-QE pair.
#     3LP-AM    2LP-AM + exact absorbed real pole (R1 series in->a, C4
#               shunt a->gnd; the biquad input is a virtual ground, so the
#               absorption is first-order EXACT -- no back-interaction).
#     2LPn-AM   LP-notch at out1, inputs {C1 (a->m1), R2 (a->p2)}:
#               num = -(C1/C2)(s^2 + wz^2), wz^2 = (R8/R7)/(R2 R5 C1 C3).
#               The on-axis zero is STRUCTURAL (no s^1 term exists to
#               mistune) -- asserted at derivation, no matching residual.
#     3LPn-AM   2LPn-AM + the same exact R1/C4 input pole.
#
#  SIGN: every LP-AM cell is INVERTING (SIGN = -1); the cascade tracks the
#  per-section sign exactly as for the MFB all-pole cells.
#
#  GAIN CONVENTION (same as cells_mfb): H(0) = K*b0/a0 with a0 = w0^2 (2nd)
#  or p1*w0^2 (3rd) and b0 = wz^2 (notch) or 1, so
#  dc_gain_to_K = SIGN*|g|*a0/b0 and the residual drives b_lead/a_lead -> K.
#
#  R7 designates the MATCHED inverter pair R7 = R8 (one symbol, one BOM
#  value, two physical resistors) -- see cells_am_core.py header.
# =====================================================================

import sympy as sp

from tf_symbols import p1, w0, wz, Q, K
import cells_am_core as CORE

FAMILY = "LP-AM"

SIGN = -1      # every LP-AM cell inverts (H(0) = -R6/R2 or -R5/R1)


def topo_name(topo):
    o, kind = topo["order"], topo["kind"]
    suf = "-C1s" if topo.get("c1_split") else ""     # parallel input cap C1 = C1a||C1b
    if kind == "LP2":
        return f"{o}LP-AM2" + suf
    return (f"{o}LPn-AM" if topo["notch"] else f"{o}LP-AM") + suf


def all_cells():
    """qe / has_R7 are carried (False) so family-agnostic Tier-C code that
    reads topo['qe'] / topo['has_R7'] keeps working; 'tap' documents which
    op-amp output is the section output (1 = U1/out1, 2 = U2/out2).

    'c1_split' twins (parallel input cap C1 = C1a||C1b) are added for every
    C1-input cell (the LP-notch cells here). They derive IDENTICALLY to the
    base cell (single C1 symbol); the flag only tells Phase-3 to realize C1 as
    two parallel E-series caps, which reaches C1/C2 (HF-gain) and wz values the
    single-cap grid cannot -- see the -C1s note in cells_am_core / the snapper."""
    base = [
        {"family": FAMILY, "order": 2, "kind": "LP",  "tap": 1,
         "notch": False, "qe": False, "has_R7": False},
        {"family": FAMILY, "order": 2, "kind": "LP2", "tap": 2,
         "notch": False, "qe": False, "has_R7": False},
        {"family": FAMILY, "order": 3, "kind": "LP2", "tap": 2,
         "notch": False, "qe": False, "has_R7": False},   # 3LP-AM2 (out2 tap)
        {"family": FAMILY, "order": 3, "kind": "LP",  "tap": 1,
         "notch": False, "qe": False, "has_R7": False},
        {"family": FAMILY, "order": 2, "kind": "LPn", "tap": 1,
         "notch": True,  "qe": False, "has_R7": False},
        {"family": FAMILY, "order": 3, "kind": "LPn", "tap": 1,
         "notch": True,  "qe": False, "has_R7": False},
    ]
    twins = [dict(t, c1_split=True) for t in base if t["kind"] == "LPn"]
    return base + twins


def dc_gain_to_K(topo, design_subs, dc_gain):
    """Desired passband DC gain -> leading-coefficient K (correct rad/s^n
    units, inherent inverting sign): H(0) = K*b0/a0."""
    w0v = float(design_subs[w0])
    a0 = (float(design_subs[p1]) * w0v**2) if topo["order"] == 3 else w0v**2
    b0 = float(design_subs[wz])**2 if topo["notch"] else 1.0
    return SIGN * abs(float(dc_gain)) * a0 / b0


def var_list(topo):
    return CORE.core_var_list(topo)


def build_ideal(topo, target_subs):
    return CORE.build_ideal_common(topo, target_subs)


def build_nonideal(topo):
    return CORE.build_nonideal_common(topo)
