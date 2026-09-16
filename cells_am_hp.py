# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cells_am_hp.py
#  Ackerberg-Mossberg HIGH-PASS cell family (FAMILY = "HP-AM").
#
#  FOUR cells over the shared 3-op-amp AM core (cells_am_core.py):
#     2HP-AM    HP at out1, input C1 (a->m1). H(inf) = -C1/C2.
#     3HP-AM    2HP-AM + exact absorbed real pole (C4 series in->a, R1
#               shunt a->gnd; HF factor C4/(C4+C1) folds into the gain).
#     2HPn-AM   HP-notch at out1, inputs {C1, R2}: the SAME netlist as
#               2LPn-AM -- only the target relation differs (wz < w0).
#               num = -(C1/C2)(s^2 + wz^2); the on-axis zero is STRUCTURAL.
#     3HPn-AM   2HPn-AM + the C4/R1 input pole (adds an origin zero).
#
#  SIGN: every HP-AM cell is INVERTING (SIGN = -1).
#
#  GAIN CONVENTION (same as cells_mfb_hp): the HP passband is the HF
#  plateau H(inf) = b_lead/a_lead = K directly (numerator degree equals
#  denominator degree on every cell here), so dc_gain_to_K = SIGN*|g| with
#  no (rad/s)^n rescale, and the residual drives b_lead/a_lead -> K.
#
#  NOTE FOR THE SNAPPER: H(inf) = C1/C2 (x C4/(C4+C1) at 3rd order) is a
#  purely CAPACITIVE ratio -- the resistor snap cannot correct it, exactly
#  like the HP-MFB all-pole C2/C4 gain, so discrete_snapper carries the
#  same per-solution capacitive-gain penalty for the HP-AM cells.
#
#  R7 designates the MATCHED inverter pair R7 = R8 (one symbol, one BOM
#  value, two physical resistors) -- see cells_am_core.py header.
# =====================================================================

import sympy as sp

from tf_symbols import p1, w0, wz, Q, K
import cells_am_core as CORE

FAMILY = "HP-AM"

SIGN = -1      # every HP-AM cell inverts (H(inf) = -C1/C2)


def topo_name(topo):
    o = topo["order"]
    suf = "-C1s" if topo.get("c1_split") else ""     # parallel input cap C1 = C1a||C1b
    return (f"{o}HPn-AM" if topo["notch"] else f"{o}HP-AM") + suf


def all_cells():
    # Every HP-AM cell drives its input through C1 (HF gain = C1/C2), so all get
    # a parallel-C1 twin (-C1s): C1 = C1a||C1b reaches C1/C2 ratios (and, for the
    # notch cells, wz) that the single-cap E-series grid cannot. Derives
    # identically to the base cell; the flag only steers Phase-3 cap enumeration.
    base = [
        {"family": FAMILY, "order": 2, "kind": "HP",  "tap": 1,
         "notch": False, "qe": False, "has_R7": False},
        {"family": FAMILY, "order": 3, "kind": "HP",  "tap": 1,
         "notch": False, "qe": False, "has_R7": False},
        {"family": FAMILY, "order": 2, "kind": "HPn", "tap": 1,
         "notch": True,  "qe": False, "has_R7": False},
        {"family": FAMILY, "order": 3, "kind": "HPn", "tap": 1,
         "notch": True,  "qe": False, "has_R7": False},
    ]
    return base + [dict(t, c1_split=True) for t in base]


def dc_gain_to_K(topo, design_subs, dc_gain):
    """Desired HF (passband) gain -> K. For HP cells the numerator degree
    equals the filter order, so H(inf) = b_lead/a_lead = K directly."""
    return SIGN * abs(float(dc_gain))


def var_list(topo):
    return CORE.core_var_list(topo)


def build_ideal(topo, target_subs):
    return CORE.build_ideal_common(topo, target_subs)


def build_nonideal(topo):
    return CORE.build_nonideal_common(topo)
