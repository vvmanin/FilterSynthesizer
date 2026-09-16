# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cells_first_order.py   [Tier B — first-order cell library]
#
#  Generalized 1st-order VCVS cells: {LP,HP} x {ni,inv} x {atten,unity,gained}
#  = 12 cells. Mirrors tf_derivation_v2's case structure so Tier C/D consume
#  them identically (same case dict keys, same make_response_func).
#
#  THE HYBRID (per ROADMAP item 1):
#    - Hid / Hni live HERE (Tier B), so Bode / Monte-Carlo see the real
#      finite-A_ol / 1-pole-GBWP / Ro op-amp behaviour — identical op-amp
#      model and symbols as the LP family.
#    - component VALUES do NOT come from unified_solver_v2; they are
#      CLOSED-FORM (one pole, one gain -> R,C). That realizer lives in
#      first_order_solver.py and consumes the descriptor below.
#
#  Op-amp port mapping:
#    ni  : (+) = node p, (-) = node m, output behind Ro at node out
#    inv : (+) = gnd,    (-) = node m, output behind Ro at node out
#
#  Verified (verify.py): non-ideal -> ideal limit < 1e-9 for all 12 cells;
#  closed-form realizer hits target pole + gain to ~1e-15.
# =====================================================================

import re
import sympy as sp

# Reuse the EXACT symbols + evaluator from the LP family so a 1st-order case
# is interchangeable under make_response_func and the str(symbol) comp-dict
# keying used everywhere downstream.
from tf_derivation_v2 import (s, R1, R2, R3, R4, C1, Ro, A_ol, GBWP_hz,
                              make_response_func)

# 1-pole open-loop gain (identical to tf_derivation_v2.derive_nonideal)
_wc = 2 * sp.pi * GBWP_hz
_A_s = A_ol * _wc / (_wc + s * A_ol)

_CELL_RE = re.compile(r"^1(LP|HP)-(ni|inv)-(atten|unity|gained)$")


# =====================================================================
# Descriptor helpers
# =====================================================================
def topo_name(topo):
    """Descriptor -> canonical cell name, e.g. '1HP-ni-gained'."""
    return f"1{topo['family']}-{topo['realization']}-{topo['gain']}"


def parse_name(name):
    """Canonical 1st-order cell name -> descriptor dict, or None if it isn't
    a 1st-order cell (so callers can fall through to the LP parser)."""
    m = _CELL_RE.match(str(name or ""))
    if not m:
        return None
    fam, real, gain = m.groups()
    return {"order": 1, "family": fam, "realization": real, "gain": gain}


def is_first_order(name):
    return _CELL_RE.match(str(name or "")) is not None


def all_cells():
    """The 12 first-order descriptors."""
    return [{"order": 1, "family": fam, "realization": real, "gain": g}
            for fam in ("LP", "HP")
            for real in ("ni", "inv")
            for g in ("atten", "unity", "gained")]


def sign_of(topo):
    """Cascade sign of a realization (ROADMAP 4.3): inverting -> -1.
    A *realization* property — never set by classify_section."""
    return -1 if topo["realization"] == "inv" else +1


def cell_components(topo):
    """Physical R/C present in this cell (for BOM columns + schematic labels).
    Returns {'caps':[...], 'resistors':[...]} like tf_derivation_v2."""
    fam, real, gain = topo["family"], topo["realization"], topo["gain"]
    caps = ["C1"]
    if real == "inv":
        res = ["R1", "R2"]                       # single schematic, gain by ratio
    elif fam == "LP":
        res = {"unity": ["R1"], "atten": ["R1", "R2"],
               "gained": ["R1", "R3", "R4"]}[gain]
    else:  # HP-ni
        res = {"unity": ["R2"], "atten": ["R1", "R2"],
               "gained": ["R2", "R3", "R4"]}[gain]
    return {"caps": caps, "resistors": res}


# =====================================================================
# Nodal equation builders  (Vin = 1 reference)
# =====================================================================
def _ni_equations(topo):
    """Non-inverting node equations in [Vp, Vm, Vout] with op-amp gain `gain_blk`.
    gain_blk('ideal') gives the virtual-short constraint; ('nonideal') the
    finite-A_s / Ro model. Input network differs LP (shunt C1) vs HP (series RC)."""
    fam, gain = topo["family"], topo["gain"]
    Vp, Vm, Vout = sp.symbols("Vp Vm Vout")
    Vin = sp.Integer(1)

    # ---- input network -> node p ----
    if fam == "LP":                              # R1: in->p ; C1 (+R2 if atten): p->gnd
        shunt = s * C1 + (1 / R2 if gain == "atten" else 0)
        eq_in = (Vp - Vin) / R1 + Vp * shunt
    else:                                        # HP: (R1 series C1): in->p ; R2: p->gnd
        ys = s * C1 / (1 + s * R1 * C1) if gain == "atten" else s * C1   # R1 shorted else
        eq_in = (Vp - Vin) * ys + Vp / R2
    return Vp, Vm, Vout, eq_in


def _build_case(topo, kind):
    """Construct an ideal or nonideal case dict for one descriptor."""
    fam, real, gain = topo["family"], topo["realization"], topo["gain"]

    if real == "ni":
        Vp, Vm, Vout, eq_in = _ni_equations(topo)
        eqs, unk = [eq_in], [Vp, Vm, Vout]
        if gain == "gained":                     # R3: m->out ; R4: m->gnd
            eqs.append((Vm - Vout) / R3 + Vm / R4)
            if kind == "ideal":
                eqs.append(Vp - Vm)              # virtual short
            else:
                eqs.append((Vout - _A_s * (Vp - Vm)) / Ro + (Vout - Vm) / R3)
        else:                                    # unity/atten: R3 shorted (Vm=Vout), R4 absent
            eqs.append(Vm - Vout)
            if kind == "ideal":
                eqs.append(Vp - Vm)              # follower: Vout = Vp
            else:
                eqs.append((Vout - _A_s * (Vp - Vm)) / Ro)
    else:                                        # inverting, (+) = gnd
        Vm, Vout = sp.symbols("Vm Vout")
        Vin = sp.Integer(1)
        unk = [Vm, Vout]
        if fam == "LP":                          # R1: in->m ; R2||C1: m->out
            yf = 1 / R2 + s * C1
            eqs = [(Vm - Vin) / R1 + (Vm - Vout) * yf]
        else:                                    # HP: (R1 series C1): in->m ; R2: m->out
            ys = s * C1 / (1 + s * R1 * C1)
            eqs = [(Vm - Vin) * ys + (Vm - Vout) / R2]
        if kind == "ideal":
            eqs.append(Vm)                       # virtual ground (V- = V+ = 0)
        elif fam == "LP":
            eqs.append((Vout - _A_s * (0 - Vm)) / Ro + (Vout - Vm) * (1 / R2 + s * C1))
        else:
            eqs.append((Vout - _A_s * (0 - Vm)) / Ro + (Vout - Vm) / R2)

    A, b = sp.linear_eq_to_matrix(eqs, unk)
    V2 = A.LUsolve(b)[unk.index(Vout)]
    num, den = sp.fraction(sp.together(V2))      # raw rational — NO expand
    tf_syms = sorted([x for x in (num + den).free_symbols
                      if x != s and not x.is_number], key=str)
    out = {
        "kind": kind, "topo": topo,
        "tf_num": num, "tf_den": den, "tf_var_list": tf_syms,
        "var_list": [sp.Symbol(n) for n in
                     (cell_components(topo)["caps"] + cell_components(topo)["resistors"])],
        "sign": sign_of(topo),
    }
    if kind == "ideal":
        npoly, dpoly = sp.Poly(sp.expand(num), s), sp.Poly(sp.expand(den), s)
        out["num_degree"] = int(npoly.degree())
        out["den_degree"] = int(dpoly.degree())
    else:
        out["num_degree"] = out["den_degree"] = None
    return out


def derive_first_order_ideal(topo):
    """Ideal (op-amp = virtual short) 1st-order TF case dict."""
    return _build_case(topo, "ideal")


def derive_first_order_nonideal(topo):
    """Non-ideal (finite A_ol, 1-pole GBWP, output Ro) 1st-order TF case dict.
    Raw rational, evaluated pointwise by make_response_func."""
    return _build_case(topo, "nonideal")


def derive(topo, kind="nonideal"):
    return _build_case(topo, kind)


def get_cases_first_order():
    """All 12 cells x {ideal, nonideal}, keyed (name, kind) — same shape as
    tf_derivation_v2.get_cases so it can be merged into a cases dict."""
    cases = {}
    for topo in all_cells():
        name = topo_name(topo)
        cases[(name, "ideal")] = derive_first_order_ideal(topo)
        cases[(name, "nonideal")] = derive_first_order_nonideal(topo)
    return cases
