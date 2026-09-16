# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cells_notch.py
#  VCVS "2N" pure-notch cell family — a single-op-amp inverting biquad
#  that realizes a symmetric notch (band-reject) with the zero pinned to
#  the pole frequency.  Added behind the registry seam (ROADMAP §3,
#  Item 3), mirroring cells_lp / cells_hp.
#
#  User-provided nodal netlist (authoritative):
#    Nodes: in, out(=op-amp output V2), a, p(op-amp +), m(op-amp -), gnd
#      C1 a   gnd
#      C2 a   p
#      R1 in  a
#      R2 a   out
#      R3 p   gnd
#      R4 in  m
#      R5 out m
#      R6 m   gnd      # gained cell only; open on the atten cell
#
#  STRUCTURAL FACTS (derived; see derive_notch.py / verify_notch*.py):
#    * H(0) = H(inf) = -R5/R4  -> the passband gain is the SAME at DC and
#      HF, and the stage is INVERTING (sign -1, carried separately by the
#      solver; the gain residual targets the magnitude R5/R4).
#    * The numerator and denominator share an IDENTICAL s^0 coefficient
#      (R1+R2)/(C1 C2 R1 R2 R3), so the zero frequency EQUALS the pole
#      frequency: wz = w0 is STRUCTURAL.  This topology realizes ONLY pure
#      notches (never LPn/HPn).  Consequently there is NO independent wz
#      degree of freedom and the cell carries NO notch-frequency residual:
#      placing w0 (the a0 residual) also places the notch.
#    * Symmetric (purely-imaginary) zero pair is obtained by zeroing the
#      numerator's s^1 coefficient.  That single algebraic constraint is
#      solved for R5 (R5 is therefore a DERIVED component, never a free
#      search variable -- same role R5 plays on the LP/HP notch cells).
#
#  THE ROLE OF R6 (the user's requested observation):
#    R6 (op-amp (-) node -> gnd) is an EXTRA degree of freedom on the gain.
#    The passband gain magnitude is R5/R4 with R5 DERIVED from the symmetric-
#    notch constraint, so it is a free coordinate of the solution manifold on
#    BOTH cells: WITH R6 ("2N") and WITHOUT R6 ("2N-atten") the gain can be
#    STEERED to any target -- unity, gained (>1) or attenuating (<1) -- while
#    meeting the pole/Q and symmetric-notch constraints. Both cells therefore
#    carry the gain residual (see build_ideal) and realise a deep on-target
#    notch at the requested gain (empirically verified across 0.3..1.5: poles
#    on f0, Q on target, zeros purely imaginary on f0, gain exact, all
#    components positive).
#
#    NOTE: an earlier revision left the atten cell WITHOUT a gain residual on
#    the belief that its reachable gain "collapses to ~[0, 0.354]". That figure
#    was the spread of the UNSTEERED emergent gain, not the reachable set -- the
#    cell reaches any gain once the residual steers it. Omitting the residual
#    meant the atten cell ignored the request and emitted arbitrary sub-unity
#    gains (all at the same low sensitivity), which outranked the correct gained
#    solutions and buried the requested gain (e.g. dc_gain=0.8 returned deeply
#    attenuated notches). The residual fixes that; R6 now simply offers a second
#    (larger-manifold) realisation, and the atten cell is the cheaper one-fewer-
#    resistor alternative at the same gain.
#
#  2-cell grid (order is always 2 for this single-op-amp notch):
#    gain   : "gained" -> name "2N"   |  "atten" -> name "2N-atten"
#    has_R6 : True                    |  False  (one fewer resistor; same gains)
#  (No "unity" cell name: the "2N" cell with K=1 IS the unity notch, the
#  same way HP folds unity into its gain axis.)
#
#  Validated (verify_notch*.py + tf_derivation_v2.self_test): denominator
#  degree == 2; two finite zeros that do not cancel a pole (nzeros == {2});
#  ideal-limit (non-ideal -> ideal) max err ~1e-9; both cells place a deep
#  on-target notch and meet the gain target via R5/R4.
# =====================================================================

import sympy as sp

from tf_symbols import (s, Va, Vp, Vm, V2,
                        R1, R2, R3, R4, R5, R6, R7, R8, Ro,
                        C1, C2, C3, C4, A_ol, GBWP_hz,
                        p1, w0, wz, Q, K)

FAMILY = "NOTCH"


# =====================================================================
# Topology descriptor helpers
# =====================================================================
def topo_name(topo):
    # The R6 cell is the full/primary notch -> displayed simply as "2N".
    # The R6-less variant keeps the "-atten" suffix (its passband gain is no
    # longer free and emerges sub-unity, so the name flags that).
    return "2N" if topo["gain"] == "gained" else f"2N-{topo['gain']}"


def all_cells():
    """The two VCVS 2N notch cells. order is fixed at 2 (single op-amp).
    has_R7 is carried (= False always) only so Tier-C code that reads
    topo["has_R7"] keeps working; has_R6 marks the gain-freeing DOF."""
    return [
        {"family": FAMILY, "order": 2, "gain": "gained", "notch": True,
         "has_R6": True,  "has_R7": False},
        {"family": FAMILY, "order": 2, "gain": "atten",  "notch": True,
         "has_R6": False, "has_R7": False},
    ]


def dc_gain_to_K(topo, design_subs, dc_gain):
    """Convert a desired passband gain into the leading-coefficient K the
    gain residual expects.

    For the notch the passband gain IS the leading-coefficient magnitude
    |b_lead/a_lead| = |H(0)| = |H(inf)| = R5/R4, so K = desired |gain|
    directly -- no (rad/s)^n rescale (that LP factor came from matching a
    DC value against a rad/s-normalised K; here the gain is already the
    dimensionless coefficient ratio).

    BOTH cells now carry the gain residual and target |gain| = R5/R4. The gain
    is a free coordinate of the solution manifold on the atten cell too (R5 is
    derived from the symmetric-notch constraint, leaving enough freedom), so the
    residual steers it to ANY requested gain -- verified as a valid deep notch
    across 0.3..1.5. Without the residual the atten cell ignored K and emitted
    arbitrary sub-unity gains that buried the requested design; with it, both
    cells honour the request (2N via R6, 2N-atten with one fewer resistor).
    """
    return float(dc_gain)


def _gates(topo):
    has_r6 = topo.get("has_R6", False)
    return dict(g6=(1/R6 if has_r6 else 0), has_r6=has_r6, gain=topo["gain"])


def var_list(topo):
    """Free search variables. R5 is DERIVED from the symmetric-notch
    constraint (never searched); R6 is present only on the gained cell."""
    caps = [C1, C2]
    resis = [R1, R2, R3, R4] + ([R6] if topo.get("has_R6") else [])
    return caps + resis


# =====================================================================
# IDEAL derivation  (virtual short: Vm -> Vp)
# =====================================================================
def build_ideal(topo, target_subs):
    G = _gates(topo); V1 = 1
    g6 = G["g6"]

    # KCL @ a, @ p (op-amp +, no input current), @ m (Vm = Vp via the short)
    eqa = (V1 - Va)/R1 + (V2 - Va)/R2 - s*C1*Va - s*C2*(Va - Vp)
    eqp = s*C2*(Va - Vp) - Vp/R3
    eqm = (V1 - Vp)/R4 + (V2 - Vp)/R5 - g6*Vp

    unk = [Va, Vp, V2]; eqs = [eqa, eqp, eqm]
    A, b = sp.linear_eq_to_matrix(eqs, unk)
    T = sp.simplify(A.LUsolve(b)[unk.index(V2)])
    num, den = sp.fraction(sp.together(T))
    num = sp.expand(num); den = sp.expand(den)
    num_poly = sp.Poly(num, s); den_poly = sp.Poly(den, s)
    a_lead = den_poly.LC(); b_lead = num_poly.LC()
    den_norm = den_poly.monic().as_expr()

    # Split into powers of s BEFORE substituting the R5 constraint (the same
    # performance guard cells_hp documents: Poly() over the post-substitution
    # nested rational is pathologically slow; substituting R5c into each
    # isolated scalar coeff and cancelling is cheap and exact).
    den_monic_co = sp.Poly(den_norm, s).all_coeffs()
    num_monic_co = sp.Poly(num_poly.monic().as_expr(), s).all_coeffs()

    # R5 algebraic constraint: zero the numerator s^1 coefficient so the zero
    # pair is purely imaginary (symmetric notch).  2nd-order numerator
    # K*(s^2 + b1 s + b0) -> zero b1 (the s^1 coeff).
    tgt = num_monic_co[-2]
    sols = sp.solve(tgt, R5)
    R5c = sols[0] if sols else None
    sr5 = {R5: R5c} if R5c is not None else {}

    def _apply(c):
        return sp.cancel(c.subs(sr5)) if sr5 else c

    dcs = [_apply(c) for c in den_monic_co]            # monic den coeffs, R5 applied
    dt = sp.expand(s**2 + (w0/Q)*s + w0**2)
    tcs = sp.Poly(dt, s).all_coeffs()
    # Denominator residuals only: s^1 -> w0/Q (Q match), s^0 -> w0^2 (freq match).
    res = [(dcs[i] - tcs[i]) / tcs[i] for i in range(1, len(dcs))]

    # NO notch-frequency residual: wz = w0 is STRUCTURAL here (num & den share
    # an identical s^0 coeff), so the a0 residual already places the notch and
    # there is no independent wz DOF to drive.

    a1_expr = dcs[-2]
    a2_expr = dcs[-3] if len(dcs) >= 3 else dcs[-2]

    # Passband gain magnitude = |b_lead/a_lead| = |H(0)| = |H(inf)| = R5/R4
    # (H itself is -R5/R4; the inverting sign is carried by the solver). K
    # carries the target |gain|. BOTH cells carry this residual so the section
    # HONORS the requested gain. R5 is derived from the symmetric-notch
    # constraint, leaving the gain as a free manifold coordinate on the atten
    # cell as well -- so the residual steers it to ANY target (verified valid
    # across 0.3..1.5), instead of the cell emitting arbitrary sub-unity gains
    # (all at the same low sensitivity) that outranked the correct solutions and
    # buried the requested gain. R6 on the gained cell is simply an extra DOF.
    H_mag = _apply(-b_lead / a_lead)               # = R5/R4 after R5c subst
    res.append((H_mag - K) / K)

    res = [e.subs(target_subs) for e in res]

    tf_syms = sorted([x for x in (num + den).free_symbols
                      if x != s and not x.is_number], key=str)
    return {
        "kind": "ideal", "topo": topo,
        "den_degree": int(den_poly.degree()), "num_degree": int(num_poly.degree()),
        "res_eqs": res, "R5_constraint": R5c,
        "a1_expr": a1_expr.subs(target_subs), "a2_expr": a2_expr.subs(target_subs),
        "var_list": var_list(topo),
        "tf_num": num, "tf_den": den, "tf_var_list": tf_syms,
    }


# =====================================================================
# NON-IDEAL derivation (raw rational TF; Vm kept separate)
# =====================================================================
def build_nonideal(topo):
    G = _gates(topo); V1 = 1
    g6 = G["g6"]
    wc = 2*sp.pi*GBWP_hz
    A_s = A_ol*wc / (wc + s*A_ol)

    # R4/R5/R6 now meet at the op-amp (-) node Vm (kept separate); the op-amp
    # output node V2 carries the finite-Ro Thevenin source and the R2/R5 ties.
    eqa = (V1 - Va)/R1 + (V2 - Va)/R2 - s*C1*Va - s*C2*(Va - Vp)
    eqp = s*C2*(Va - Vp) - Vp/R3
    eqm = (V1 - Vm)/R4 + (V2 - Vm)/R5 - g6*Vm
    eqo = (V2 - A_s*(Vp - Vm))/Ro + (V2 - Va)/R2 + (V2 - Vm)/R5

    unk = [Va, Vp, Vm, V2]; eqs = [eqa, eqp, eqm, eqo]
    A, b = sp.linear_eq_to_matrix(eqs, unk)
    V2_sol = A.LUsolve(b)[unk.index(V2)]
    num, den = sp.fraction(sp.together(V2_sol))        # NO expand
    tf_syms = sorted([x for x in (num + den).free_symbols
                      if x != s and not x.is_number], key=str)
    return {
        "kind": "nonideal", "topo": topo,
        "den_degree": None, "num_degree": None,
        "tf_num": num, "tf_den": den, "tf_var_list": tf_syms,
    }
