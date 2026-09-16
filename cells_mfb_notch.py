# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cells_mfb_notch.py
#  Multiple-Feedback (Rauch / Friend) pure-NOTCH cell family — single
#  op-amp biquads that realize a symmetric notch (band-reject) with the
#  zero pinned to the pole frequency (wz = w0).  Registered under
#  FAMILY = "NOTCH-MFB"; the generic engine (tf_derivation_v2) dispatches
#  by topo["family"] exactly as for the VCVS "NOTCH" module and the
#  LP/HP-MFB modules, and the per-section topology radio (VCVS | MFB | ...)
#  selects which family's cells are offered for a given cascade section.
#
#  TWO cells (order fixed at 2, single op-amp):
#     2N-MFB        gained / UNITY-capable notch, NON-inverting
#     2N-MFB-atten  attenuating-only  notch,      NON-inverting
#
#  Both are pure notches (wz = w0) and NON-inverting.  They differ only in
#  the op-amp (-) node network, which sets the reachable passband gain:
#
#  2N-MFB-atten  (the minimal cell)  ---------------------------------------
#     Nodes: in, a, out(=op-amp V2), m(op-amp -), p(op-amp +), gnd
#        C1 in-a | C2 m-out | R1 in-p | R2 a-m | R3 a-out | R4 p-gnd
#        (+)=p, (-)=m, out=V2
#     H(0) = H(inf) = R4/(R1+R4)  -> a positive divider strictly < 1, so
#     this cell is ATTENUATING-ONLY.  wz = w0 is STRUCTURAL (num & den share
#     an identical s^0 coeff), so it carries NO notch-frequency residual and
#     NO separate symmetry residual -- placing w0 places the (symmetric) zero.
#     Vars: C1,C2,R1,R2,R3,R4 (all free; R5_constraint = None).
#
#  2N-MFB  (gained / unity)  ------------------------------------------------
#     Nodes: in, a, out(=op-amp V2), m(op-amp -), p(op-amp +), gnd
#        C1 in-a | C2 m-out | C3 m-gnd | R1 in-p | R2 a-m | R3 a-out
#        R4 p-gnd | R5 m-gnd
#        (+)=p, (-)=m, out=V2
#     Adds, at the op-amp (-) node, a resistor R5 (m->gnd) and a capacitor
#     C3 (m->gnd) -- a frequency-dependent leg that boosts the closed-loop
#     gain.  R5 lifts the DC gain and C3 lifts the HF gain:
#         H(0)   = R4 (R2+R3+R5) / [ R5 (R1+R4) ]
#         H(inf) = R4 (C2+C3)    / [ C2 (R1+R4) ]
#     Each is the atten divider R4/(R1+R4) times a boost factor > 1
#     ((R2+R3+R5)/R5 at DC, (C2+C3)/C2 at HF), so the passband gain can be
#     set to UNITY or ANY value > 1 (and also < 1).  Crucially the op-amp (+)
#     input stays on the R1/R4 INPUT DIVIDER (NOT tied to the input) -- that
#     divider is what injects the negative term the numerator's s^1
#     coefficient needs for an on-axis (jw) zero; tying (+) to the input
#     instead destroys the zero (no notch).  Here wz = w0 is NOT structural,
#     so the symmetric notch is pinned by requiring H(0) = H(inf) = K (which,
#     together with the on-axis-zero residual, forces the numerator to
#     K*(s^2 + w0^2)).  Vars: C1,C2,C3,R1,R2,R3,R4,R5 (all free;
#     R5_constraint = None -- R5 is a real free element, NOT an algebraic
#     constraint slot).
#
#  SOLVER OFFERING (topology_tab): for a sub-unity target BOTH cells are
#  offered in parallel and ranked together by sens_score (2N-MFB-atten is
#  the fewer-part cell; 2N-MFB also covers it).  For a unity or gained target
#  ONLY 2N-MFB is offered (2N-MFB-atten cannot reach >= 1).
#
#  Shared structural facts (verified in-session against the symbolic TF)
#  ---------------------------------------------------------------------
#    * Both cells are pure notches (wz = w0) and NON-inverting (SIGN = +1);
#      the passband gain is read at DC (h0_f) by the solver.
#    * The symmetric (purely-imaginary) zero is obtained by zeroing the
#      numerator's s^1 coefficient -- enforced as a numerator RESIDUAL (the
#      engine's algebraic slot is the symbol R5, which on the gained cell is a
#      real m->gnd element, not a constraint).  All components stay free.
#    * The op-amp (+) input divider {R1 (in->p), R4 (p->gnd)} is UNLOADED on
#      BOTH cells (the (+) input draws no current), so only its RATIO R1/R4
#      affects the response -- the COMMON scale of {R1,R4} is a free DOF
#      (H invariant under R1,R4 *k), handled by
#      unified_solver_v2.rescale_isolated_r5r6 (pins the pair into a sane
#      window while preserving the ratio, i.e. the gain).
#
#  FEASIBILITY / GRACEFUL DEGRADATION
#    * 2N-MFB-atten: gain is the divider R4/(R1+R4) < 1 AND coupled to Q by
#      g > Q^2/(1+Q^2); a target >= 1 (or too small for the Q) yields an empty
#      BOM.  The PRACTICAL window (finite resistor spread) is narrower than the
#      theoretical band -- the spread grows without bound toward both edges.
#    * 2N-MFB: reaches unity and gain > 1 (verified g = 1, 2, 4 with deep
#      on-frequency nulls and modest spreads); out-of-reach targets degrade to
#      an empty BOM via the usual probe.
#
#  Op-amp node mapping:  (+) = Vp,  (-) = Vm,  output = V2 behind Ro.
#  Non-ideal TF kept as a RAW rational function (no expand), GBW one-pole
#  model identical to the VCVS / LP-MFB / HP-MFB modules.
#
#  Validated (tf_derivation_v2.self_test): denominator degree == 2; two
#  finite (on-axis) zeros that do not cancel a pole (nzeros == {2});
#  ideal-limit (non-ideal -> ideal) max err ~1e-11, for BOTH cells.
# =====================================================================

import sympy as sp

from tf_symbols import (s, Va, Vp, Vm, V2,
                        R1, R2, R3, R4, R5, R6, R7, R8, Ro,
                        C1, C2, C3, C4, A_ol, GBWP_hz,
                        p1, w0, wz, Q, K)

FAMILY = "NOTCH-MFB"

SIGN = +1      # H(0) = H(inf) > 0 on both cells -> non-inverting


# =====================================================================
# Topology descriptor helpers
# =====================================================================
def topo_name(topo):
    # gained/unity cell is "2N-MFB"; the attenuating-only cell is "2N-MFB-atten"
    return "2N-MFB" if topo.get("gained") else "2N-MFB-atten"


def all_cells():
    """The two NOTCH-MFB cells. order fixed at 2 (single op-amp); notch=True so
    the family-agnostic self_test reads it correctly; has_R7=False is carried
    so Tier-C code that reads topo["has_R7"] keeps working.
       gained=True  -> 2N-MFB        (C1,C2,C3,R1,R2,R3,R4,R5): unity / gained
       gained=False -> 2N-MFB-atten  (C1,C2,R1,R2,R3,R4):       attenuating-only
    """
    return [
        {"family": FAMILY, "order": 2, "notch": True, "gained": True,
         "has_R7": False},
        {"family": FAMILY, "order": 2, "notch": True, "gained": False,
         "has_R7": False},
    ]


def dc_gain_to_K(topo, design_subs, dc_gain):
    """Convert a desired passband gain into the leading-coefficient K the gain
    residual expects.

    The passband gain IS the DC/HF value H(0) = H(inf) (a dimensionless ratio),
    so K = desired |gain| directly -- no (rad/s)^n rescale.  Both cells are
    non-inverting, so K carries the positive magnitude; the +1 sign is reported
    via out["sign"] by the solver.  2N-MFB-atten is < 1 (and coupled to Q), so
    out-of-band targets yield an empty BOM; 2N-MFB accepts unity / gained too."""
    return abs(float(dc_gain))


def var_list(topo):
    """Free search variables.  There is no algebraically-derived component (the
    symmetric-notch zero is a numerator residual), so every element is free and
    R5_constraint is None.
       2N-MFB       : C1,C2,C3,R1,R2,R3,R4,R5   (C3,R5 = the m->gnd gain leg)
       2N-MFB-atten : C1,C2,R1,R2,R3,R4
    """
    if topo.get("gained"):
        return [C1, C2, C3, R1, R2, R3, R4, R5]
    return [C1, C2, R1, R2, R3, R4]


# =====================================================================
# Nodal-equation builder
# =====================================================================
def _notch_eqs(topo, Vmv, opamp_src=None):
    """KCL list for the MFB pure-notch core. (+) = Vp, (-) via Vmv.
       Vmv      : voltage at the (-) node used in the a/m equations
                  (Vp ideal via the virtual short; the free Vm non-ideal).
       opamp_src: None -> ideal (no explicit output eqn; V2 solved from the
                  m-node) ; expr -> non-ideal Thevenin source A_s*(V+ - V-).
    Netlist (user convention): C1 in-a | C2 m-out | R1 in-p | R2 a-m | R3 a-out
    | R4 p-gnd, plus (gained cell only) C3 m-gnd | R5 m-gnd.
    """
    gained = topo.get("gained", False)
    eqs = []
    # node a : C1 in->a, R2 a->m (to Vmv), R3 a->out
    eqs.append(s*C1*(1 - Va) + (Vmv - Va)/R2 + (V2 - Va)/R3)
    # node p (op-amp +) : R1 in->p, R4 p->gnd.  No op-amp input current.
    eqs.append((1 - Vp)/R1 - Vp/R4)
    # node m (op-amp -) : R2 a->m, C2 m->out, plus (gained) R5 m->gnd & C3 m->gnd.
    em = (Va - Vmv)/R2 + s*C2*(V2 - Vmv)
    if gained:
        em += -Vmv/R5 - s*C3*Vmv          # R5 (m->gnd) DC-gain leg, C3 (m->gnd) HF-gain leg
    eqs.append(em)
    if opamp_src is not None:             # node out (non-ideal): C2 m->out, R3 a->out, Ro
        eqs.append((V2 - opamp_src)/Ro + s*C2*(V2 - Vmv) + (V2 - Va)/R3)
    return eqs, Vp


def _unknowns(nonideal):
    return [Va, Vp, Vm, V2] if nonideal else [Va, Vp, V2]


# =====================================================================
# IDEAL derivation  (virtual short: Vm -> Vp)
# =====================================================================
def build_ideal(topo, target_subs):
    gained = topo.get("gained", False)
    eqs, _ = _notch_eqs(topo, Vp, opamp_src=None)      # virtual short V- = V+
    unk = _unknowns(nonideal=False)

    A, b = sp.linear_eq_to_matrix(eqs, unk)
    T = A.LUsolve(b)[unk.index(V2)]
    num, den = sp.fraction(sp.cancel(sp.together(T)))
    num = sp.expand(num); den = sp.expand(den)
    num_poly = sp.Poly(num, s); den_poly = sp.Poly(den, s)
    a_lead = den_poly.LC(); b_lead = num_poly.LC()

    dcs = sp.Poly(den_poly.monic().as_expr(), s).all_coeffs()   # monic den coeffs
    num_monic_co = sp.Poly(num_poly.monic().as_expr(), s).all_coeffs()

    dt = sp.expand(s**2 + (w0/Q)*s + w0**2)
    tcs = sp.Poly(dt, s).all_coeffs()
    # Denominator residuals: s^1 -> w0/Q (Q match), s^0 -> w0^2 (freq match).
    res = [(dcs[i] - tcs[i]) / tcs[i] for i in range(1, len(dcs))]

    # Symmetric-notch residual: zero the numerator s^1 coefficient so the zero
    # pair is purely imaginary.  Enforced as a RESIDUAL (not via R5_constraint):
    # the engine's algebraic slot is the symbol R5, which on the gained cell is
    # a real m->gnd element, not a constraint.
    res.append(num_monic_co[-2] / w0)                  # on-axis (drive to 0)

    # Gain residual(s).  Passband gain = H(0) = num(0)/den(0); at HF, H(inf) =
    # b_lead/a_lead.  num(0),den(0) are nonzero (the zero sits on the jw axis,
    # not at the origin), so H(0) is well-defined.
    h0 = num.subs(s, 0) / den.subs(s, 0)
    if gained:
        # wz = w0 is NOT structural here, so pin the SYMMETRIC notch (H(0)=H(inf))
        # AND the gain in one shot: drive BOTH H(0) and H(inf) to K.  Together with
        # the on-axis-zero residual this forces the numerator to K*(s^2 + w0^2).
        hinf = b_lead / a_lead
        res.append((h0 - K) / K)
        res.append((hinf - K) / K)
    else:
        # 2N-MFB-atten: wz = w0 is STRUCTURAL (num & den share s^0), so H(0)=H(inf)
        # automatically -- a single gain residual suffices.
        res.append((h0 - K) / K)

    res = [e.subs(target_subs) for e in res]

    a1_expr = dcs[-2]
    a2_expr = dcs[-3] if len(dcs) >= 3 else dcs[-2]

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
    wc = 2*sp.pi*GBWP_hz
    A_s = A_ol*wc / (wc + s*A_ol)

    _, Vplus = _notch_eqs(topo, Vm, opamp_src=sp.Integer(0))
    src = A_s*(Vplus - Vm)
    eqs, _ = _notch_eqs(topo, Vm, opamp_src=src)
    unk = _unknowns(nonideal=True)

    A, b = sp.linear_eq_to_matrix(eqs, unk)
    V2_sol = A.LUsolve(b)[unk.index(V2)]
    num, den = sp.fraction(sp.together(V2_sol))          # NO expand
    tf_syms = sorted([x for x in (num + den).free_symbols
                      if x != s and not x.is_number], key=str)
    return {
        "kind": "nonideal", "topo": topo,
        "den_degree": None, "num_degree": None,
        "tf_num": num, "tf_den": den, "tf_var_list": tf_syms,
    }
