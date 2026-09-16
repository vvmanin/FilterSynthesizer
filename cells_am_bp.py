# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cells_am_bp.py
#  Ackerberg-Mossberg BAND-PASS cell family (FAMILY = "BP-AM").
#
#  TWO cells over the shared 3-op-amp AM core (cells_am_core.py), solved
#  in parallel and ranked together by sens_score -- the same pooled-cell
#  mechanism as 2BP-MFB / 2BP-MFB-QE and 2LP-AM / 2LP-AM2:
#     2BP-AM    BP tapped at out1, input R1 (a->m1). num = -s/(R1 C2);
#               center gain |H(jw0)| = R4/R1. This is the band-pass tap of
#               the CLASSIC Ackerberg-Mossberg resonator (same netlist as
#               2LP-AM2, different op-amp output).
#     2BP-AM2   BP tapped at out2, input C1 (a->m1); R1/R2/R3 absent.
#               num = -(C1/(R6 C2 C3)) s; |H(jw0)| = C1 R4 R8/(C3 R6 R7)
#               = C1 R4/(C3 R6) with the matched pair. NOTE: with finite
#               GB this tap carries a parasitic HF feed-through floor (the
#               C1 input current escapes through R5 to out2 as U1's
#               virtual ground degrades) -- quantified in
#               AM_NONIDEAL_ANALYSIS.md; the ideal model is exact.
#
#  SIGN: both cells are INVERTING (SIGN = -1); Ki = b_lead/a_lead < 0.
#
#  GAIN CONVENTION (same as cells_mfb_bp): the pairing stage hands down the
#  rad/s numerator coefficient Ki = b_lead/a_lead via cfg["K"] (dc_gain =
#  None); K carries the positive magnitude, the residual drives
#  b_lead/a_lead -> SIGN*K, and the solver reports out["sign"] = -1.
#
#  R7 designates the MATCHED inverter pair R7 = R8 (one symbol, one BOM
#  value, two physical resistors) -- see cells_am_core.py header.
# =====================================================================

import sympy as sp

from tf_symbols import p1, w0, wz, Q, K
import cells_am_core as CORE

FAMILY = "BP-AM"

SIGN = -1      # both cells invert: Ki = b_lead/a_lead < 0


def topo_name(topo):
    return "2BP-AM2" if topo["kind"] == "BP2" else "2BP-AM"


def all_cells():
    # 2BP-AM2's peak gain C1*R4/(C3*R6) is resistor-trimmable (R4, R6 adjust it
    # finely on the E-series grid), so unlike the HP/notch cells C1 is NOT a
    # pinned cap ratio and needs no parallel-C1 split -- a 2BP-AM2-C1s twin would
    # be unreasonable complexity for no benefit. Neither BP cell gets a -C1s twin.
    return [
        {"family": FAMILY, "order": 2, "kind": "BP",  "tap": 1,
         "notch": False, "qe": False, "has_R7": False},
        {"family": FAMILY, "order": 2, "kind": "BP2", "tap": 2,
         "notch": False, "qe": False, "has_R7": False},
    ]


def dc_gain_to_K(topo, design_subs, dc_gain):
    """Band-pass: the target already IS the rad/s leading coefficient Ki
    (magnitude passthrough; the inverting sign is applied in the residual
    as SIGN*K and reported via out["sign"])."""
    return abs(float(dc_gain))


def var_list(topo):
    return CORE.core_var_list(topo)


def build_ideal(topo, target_subs):
    return CORE.build_ideal_common(topo, target_subs,
                                   K_signed_in_residual=SIGN)


def build_nonideal(topo):
    return CORE.build_nonideal_common(topo)
