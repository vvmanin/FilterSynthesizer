# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cells_am_notch.py
#  Ackerberg-Mossberg PURE-NOTCH cell family (FAMILY = "NOTCH-AM").
#
#  ONE cell over the shared 3-op-amp AM core (cells_am_core.py):
#     2N-AM     symmetric notch at out1, inputs {C1 (a->m1), R2 (a->p2)} --
#               the same netlist as 2LPn-AM/2HPn-AM with wz driven to the
#               section's fz (= f0 for a band-reject section):
#                  num = -(C1/C2)(s^2 + wz^2),
#                  wz^2 = (R8/R7)/(R2 R5 C1 C3),
#                  H(inf) = -C1/C2,  H(0) = H(inf)*wz^2/w0^2.
#               The on-axis zero is STRUCTURAL (the s^1 numerator term does
#               not exist), so the null depth never depends on component
#               matching -- tolerances shift wz, they cannot fill the notch.
#               With wz = w0 met, H(0) = H(inf) follows automatically, so a
#               single gain residual (H(inf) -> SIGN*K) pins the whole
#               passband -- unlike 2N-MFB, which needs both ends driven.
#
#  SIGN: INVERTING (SIGN = -1) -- note this differs from the NON-inverting
#  MFB notch pair; the cascade tracks per-section sign either way.
#
#  GAIN CONVENTION (same linear-gain interface as cells_mfb_notch): the
#  passband gain is the dimensionless plateau |H(0)| = |H(inf)| = C1/C2;
#  dc_gain_to_K is a magnitude passthrough and the residual targets SIGN*K.
#
#  NOTE FOR THE SNAPPER: C1/C2 is a purely CAPACITIVE ratio (like HP-AM),
#  so discrete_snapper carries the capacitive-gain penalty here too.
#
#  R7 designates the MATCHED inverter pair R7 = R8 (one symbol, one BOM
#  value, two physical resistors) -- see cells_am_core.py header.
# =====================================================================

import sympy as sp

from tf_symbols import p1, w0, wz, Q, K
import cells_am_core as CORE

FAMILY = "NOTCH-AM"

SIGN = -1      # inverting: H(0) = H(inf) = -C1/C2


def topo_name(topo):
    return "2N-AM-C1s" if topo.get("c1_split") else "2N-AM"


def all_cells():
    # 2N-AM drives its input through C1 (plateau gain = C1/C2, and C1 co-sets wz),
    # so it gets a parallel-C1 twin (-C1s) to reach grid-unreachable C1 values.
    base = {"family": FAMILY, "order": 2, "kind": "N", "tap": 1,
            "notch": True, "qe": False, "has_R7": False}
    return [base, dict(base, c1_split=True)]


def dc_gain_to_K(topo, design_subs, dc_gain):
    """Pure notch: the passband gain IS the dimensionless DC/HF plateau
    (magnitude passthrough; sign applied in the residual as SIGN*K)."""
    return abs(float(dc_gain))


def var_list(topo):
    return CORE.core_var_list(topo)


def build_ideal(topo, target_subs):
    return CORE.build_ideal_common(topo, target_subs,
                                   K_signed_in_residual=SIGN)


def build_nonideal(topo):
    return CORE.build_nonideal_common(topo)
