# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cells_bp.py
#  VCVS "2BP" Sallen-Key band-pass cell family — a single-op-amp biquad
#  whose numerator is a single s term (one transmission zero pinned at the
#  origin, one at infinity), giving the classic 2nd-order band-pass shape.
#  Added behind the registry seam (ROADMAP §3, Item 5), mirroring
#  cells_lp / cells_hp / cells_notch.  VCVS lands first; the MFB / DABP
#  band-pass topologies come later.
#
#  User-provided nodal netlist (authoritative):
#    Nodes: in, out(=op-amp output V2), a, p(op-amp +), m(op-amp -), gnd
#      C1 a   gnd
#      C2 a   p
#      R1 in  a
#      R2 a   out
#      R3 p   gnd
#      R4 m   gnd      # gained cell only; OPEN on the atten cell
#      R5 out m        # gained cell only; SHORT on the atten cell
#
#  This is a Sallen-Key band-pass: the input drives node a through R1, the
#  RC network (C1,C2,R1,R2,R3) sets the complex pole pair, and a
#  NON-INVERTING gain block K = 1 + R5/R4 (R4 m->gnd, R5 out->m) sits around
#  the op-amp.  The "2BP-atten" cell removes that block (R4 open, R5 short)
#  so the op-amp is a unity follower (K = 1).
#
#  STRUCTURAL FACTS (derived; verified in-session against the symbolic TF):
#    * The numerator is K*C2*R3/R1 * s  -- a SINGLE s term.  The s^0
#      numerator coefficient is STRUCTURALLY zero (no algebraic constraint
#      needed, unlike the notch cell which must solve R5 to zero its s^1
#      numerator coefficient).  So band-pass cells carry NO derived-R5
#      constraint: R5 is a free search variable on the gained cell and is
#      simply absent (shorted) on the atten cell.
#    * Pole/Q:   w0^2   = (1/R1 + 1/R2)/(C1 C2 R3)
#                w0/Q   = [ (1/R1+1/R2) C2 R3  -  K C2 R3/R2  +  C1 + C2 ]
#                         / (C1 C2 R3)
#      K is the Q-boost knob: raising K lowers the s^1 denominator
#      coefficient, raising Q (the standard Sallen-Key positive-feedback Q
#      enhancement).
#    * Leading-coefficient gain Ki = b_lead/a_lead = K/(R1 C1)  [rad/s].
#      This is the numerator coefficient the biquad-pairing stage hands down
#      (units rad/s).  The center-frequency magnitude is |H(jw0)| = Ki Q/w0.
#      The stage is NON-INVERTING (sign +1, the default; no out["sign"]).
#
#  THE TWO CELLS (order is always 2 for this single-op-amp band-pass):
#    gain   : "gained" -> name "2BP"        |  "atten" -> name "2BP-atten"
#    R4/R5  : present (gain block, K=1+R5/R4)|  R4 open / R5 short (K=1)
#  Both cells can hit ANY (w0, Q, Ki) target; the atten cell simply has
#  fewer free components (no R4/R5 gain trim, so it leans on R1..R3 + C1,C2).
#  Because R4 and R5 enter the TF ONLY through their ratio R5/R4 (= K-1),
#  their COMMON scale is a free, redundant DOF on the gained cell -- handled
#  by unified_solver_v2.rescale_isolated_r5r6 exactly like the LP/notch
#  isolated feedback branch.
#
#  Validated (tf_derivation_v2.self_test): denominator degree == 2; exactly
#  ONE finite zero, at the origin, that does not cancel a pole
#  (nzeros == {1}); ideal-limit (non-ideal -> ideal) max err ~1e-11.
# =====================================================================

import sympy as sp

from tf_symbols import (s, Va, Vp, Vm, V2,
                        R1, R2, R3, R4, R5, R6, R7, R8, Ro,
                        C1, C2, C3, C4, A_ol, GBWP_hz,
                        p1, w0, wz, Q, K)

FAMILY = "BP"


# =====================================================================
# Topology descriptor helpers
# =====================================================================
def topo_name(topo):
    # The R4/R5 gain-block cell is the full/primary band-pass -> "2BP".
    # The block-less unity variant keeps the "-atten" suffix.
    return "2BP" if topo["gain"] == "gained" else f"2BP-{topo['gain']}"


def all_cells():
    """The two VCVS band-pass cells. order is fixed at 2 (single op-amp).
    notch is carried (= False always) so the family-agnostic self_test, which
    reads topo["notch"], keeps working; has_R7 (= False always) is carried so
    Tier-C code that reads topo["has_R7"] keeps working."""
    return [
        {"family": FAMILY, "order": 2, "gain": "gained", "notch": False,
         "has_R7": False},
        {"family": FAMILY, "order": 2, "gain": "atten",  "notch": False,
         "has_R7": False},
    ]


def dc_gain_to_K(topo, design_subs, dc_gain):
    """Convert a desired numerator coefficient into the leading-coefficient K
    the gain residual expects.

    For the band-pass the pairing stage hands down Ki = b_lead/a_lead =
    K/(R1 C1) directly (units rad/s), which IS the leading-coefficient the
    residual targets -- so K = Ki with no (rad/s)^n rescale (that LP factor
    came from matching a DC value against a rad/s-normalised K; here the
    target already IS the rad/s leading coefficient).  Both cells take it
    directly; the atten cell can still realize any Ki (its single s-term
    numerator scales with R1, C1 just like the gained cell)."""
    return float(dc_gain)


def _gates(topo):
    # The gain block (R4 m->gnd, R5 out->m) is present only on the gained cell.
    # On the atten cell R5 is shorted (out == m) and R4 is open, so the op-amp
    # is a unity follower.
    return dict(gained=(topo["gain"] == "gained"), gain=topo["gain"])


def var_list(topo):
    """Free search variables. The band-pass carries no derived-R5 constraint
    (its s^0 numerator coefficient is structurally zero), so R5 is a FREE
    variable on the gained cell; R4/R5 are absent on the atten cell."""
    caps = [C1, C2]
    resis = [R1, R2, R3] + ([R4, R5] if topo["gain"] == "gained" else [])
    return caps + resis


# =====================================================================
# IDEAL derivation  (virtual short: Vm -> Vp)
# =====================================================================
def build_ideal(topo, target_subs):
    G = _gates(topo); V1 = 1

    # KCL @ a, @ p (op-amp +, no input current), @ m (Vm = Vp via the short).
    #   node a : input via R1, feedback via R2, C1 a->gnd, C2 a->p
    #   node p : C2 a->p, R3 p->gnd
    #   node m : gained -> R4 m->gnd + R5 out->m (Vm=Vp);
    #            atten  -> R5 short => V2 = Vp (unity follower)
    eqa = (V1 - Va)/R1 + (V2 - Va)/R2 - s*C1*Va - s*C2*(Va - Vp)
    eqp = s*C2*(Va - Vp) - Vp/R3
    if G["gained"]:
        eqm = (V2 - Vp)/R5 - Vp/R4
    else:
        eqm = V2 - Vp

    unk = [Va, Vp, V2]; eqs = [eqa, eqp, eqm]
    A, b = sp.linear_eq_to_matrix(eqs, unk)
    T = sp.simplify(A.LUsolve(b)[unk.index(V2)])
    num, den = sp.fraction(sp.together(T))
    num = sp.expand(num); den = sp.expand(den)
    num_poly = sp.Poly(num, s); den_poly = sp.Poly(den, s)
    a_lead = den_poly.LC(); b_lead = num_poly.LC()

    # NO R5 constraint: the s^0 numerator coefficient is structurally zero, so
    # the single-s band-pass numerator needs no algebraic pinning (contrast the
    # notch, which solves R5 to zero its s^1 numerator coefficient).
    dcs = den_poly.monic().as_expr()
    dcs = sp.Poly(dcs, s).all_coeffs()                 # monic den coeffs
    dt = sp.expand(s**2 + (w0/Q)*s + w0**2)
    tcs = sp.Poly(dt, s).all_coeffs()
    # Denominator residuals only: s^1 -> w0/Q (Q match), s^0 -> w0^2 (freq match).
    res = [(dcs[i] - tcs[i]) / tcs[i] for i in range(1, len(dcs))]

    a1_expr = dcs[-2]
    a2_expr = dcs[-3] if len(dcs) >= 3 else dcs[-2]

    # Gain residual (BOTH cells): the leading-coefficient ratio Ki = b_lead/a_lead
    # = K/(R1 C1) is the rad/s numerator coefficient the pairing stage targets.
    # K carries that target (see dc_gain_to_K / cfg["K"]).  No sign: the stage
    # is non-inverting (+1, the solver default).
    res.append((b_lead / a_lead - K) / K)

    res = [e.subs(target_subs) for e in res]

    tf_syms = sorted([x for x in (num + den).free_symbols
                      if x != s and not x.is_number], key=str)
    return {
        "kind": "ideal", "topo": topo,
        "den_degree": int(den_poly.degree()), "num_degree": int(num_poly.degree()),
        "res_eqs": res, "R5_constraint": None,
        "a1_expr": a1_expr.subs(target_subs), "a2_expr": a2_expr.subs(target_subs),
        "var_list": var_list(topo),
        "tf_num": num, "tf_den": den, "tf_var_list": tf_syms,
    }


# =====================================================================
# NON-IDEAL derivation (raw rational TF; Vm kept separate)
# =====================================================================
def build_nonideal(topo):
    G = _gates(topo); V1 = 1
    wc = 2*sp.pi*GBWP_hz
    A_s = A_ol*wc / (wc + s*A_ol)

    # The op-amp (-) node Vm is kept separate; the output node V2 carries the
    # finite-Ro Thevenin source A_s*(Vp - Vm) and the R2 (a->out) tie.
    eqa = (V1 - Va)/R1 + (V2 - Va)/R2 - s*C1*Va - s*C2*(Va - Vp)
    eqp = s*C2*(Va - Vp) - Vp/R3
    if G["gained"]:
        eqm = -Vm/R4 + (V2 - Vm)/R5                       # R4 m->gnd, R5 out->m
        eqo = (V2 - A_s*(Vp - Vm))/Ro + (V2 - Va)/R2 + (V2 - Vm)/R5
    else:
        eqm = Vm - V2                                     # R5 short -> Vm = V2
        eqo = (V2 - A_s*(Vp - Vm))/Ro + (V2 - Va)/R2      # R4 open (no m->gnd)

    unk = [Va, Vp, Vm, V2]; eqs = [eqa, eqp, eqm, eqo]
    A, b = sp.linear_eq_to_matrix(eqs, unk)
    V2_sol = A.LUsolve(b)[unk.index(V2)]
    num, den = sp.fraction(sp.together(V2_sol))           # NO expand
    tf_syms = sorted([x for x in (num + den).free_symbols
                      if x != s and not x.is_number], key=str)
    return {
        "kind": "nonideal", "topo": topo,
        "den_degree": None, "num_degree": None,
        "tf_num": num, "tf_den": den, "tf_var_list": tf_syms,
    }
