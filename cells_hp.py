# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cells_hp.py
#  High-pass / HP-notch VCVS (Sallen-Key) cell family — the R<->C swap of
#  cells_lp, with origin zeros that block DC.  First family added behind
#  the registry seam (ROADMAP §3); BP later copies this pattern.
#
#  3-axis topology grid (12 cells):
#    order : 3 | 2          (3rd-order has a real pole; 2nd-order does not)
#    gain  : "gained" | "unity" | "atten"
#    notch : True | False
#
#  Unlike LP (where "atten" = unity SK + an R7 *resistive* input divider),
#  HP attenuation is its own gain mode set by a *capacitive* divider (C4),
#  so gain is a clean 3-way axis here. has_R7 is a derived field
#  (= gain=="gained") kept only so Tier-C code that reads topo["has_R7"]
#  keeps working.
#
#  "gain" for HP is the HIGH-FREQUENCY gain (passband sits above the
#  cutoff; the origin zeros block DC):
#     unity  : H(inf) = 1            (R5 shorted -> unity follower)
#     gained : H(inf) = 1 + R5/R7    (resistive; K = target HF gain)
#     atten  : H(inf) = capacitive divider < 1
#                 2nd order : C2/(C2+C4)
#                 3rd order : C1*C2/(C1*C2 + C1*C4 + C2*C4)
#              (caps-only; K carries the target HF gain)
#
#  Unified nodal schematic (authoritative):
#    Nodes: in, out, a, b, m(op-amp -), p(op-amp +), gnd
#      C1 in a    # 3rd-order only; else short (a = in)
#      C2 a  b    # always
#      C3 b  p    # always
#      C4 b  gnd  # atten only; else open
#      R1 a  gnd  # 3rd-order only; else open
#      R2 a  p    # notch only; else open
#      R3 p  gnd  # always
#      R4 a  m    # notch only; else open
#      R5 out m   # always (shorted for unity/atten notchless follower)
#      R6 b  out  # always
#      R7 m  gnd  # gained only; else open
#
#  Validated (scratch_hp_*): denominator degree == order for all 12 cells;
#  ideal-limit (non-ideal -> ideal) max err ~1e-10; notchless numerators
#  are pure const*s^order (all zeros at origin); notch numerators factor as
#  K*(s^2+wz^2) [2nd] and K*s*(s^2+wz^2) [3rd] with on-axis zero pairs.
# =====================================================================

import sympy as sp

from tf_symbols import (s, Va, Vb, Vp, Vm, V2,
                        R1, R2, R3, R4, R5, R6, R7, R8, Ro,
                        C1, C2, C3, C4, A_ol, GBWP_hz,
                        p1, w0, wz, Q, K)

FAMILY = "HP"


# =====================================================================
# Topology descriptor helpers
# =====================================================================
def topo_name(topo):
    o, g, n = topo["order"], topo["gain"], topo["notch"]
    r8 = "+R8" if topo.get("has_R8") else ""
    return f"{o}{'HPn' if n else 'HP'}-{g}{r8}"


# Cells that get an optional b->gnd feedback attenuator (R8) twin. R8 is the
# extra DOF (the notebook's "R7") that lets the notch feedback resistor R5
# settle near the floor instead of being forced high; it only helps where R5
# is a constrained feedback element with room to move -- the gained notch
# cells (both orders) and the 3rd-order unity notch cell. Each listed cell is
# emitted BOTH without R8 (standard) and with R8 (the +R8 twin); the solver
# tries both and keeps the better-sensitivity BOM.
_R8_TWIN_CELLS = {(2, "gained", True), (3, "unity", True), (3, "gained", True)}


def all_cells():
    """The 12 HP topology cells, plus the 3 optional +R8 (b->gnd attenuator)
    twins. gain is a clean 3-way axis (gained / unity / atten); has_R7 is
    derived (gained only). has_R8 marks the b->gnd feedback-attenuator twin."""
    out = []
    for order in (3, 2):
        for gain in ("gained", "unity", "atten"):
            for notch in (True, False):
                base = {"family": FAMILY, "order": order, "gain": gain,
                        "notch": notch, "has_R7": (gain == "gained"),
                        "has_R8": False}
                out.append(base)
                if (order, gain, notch) in _R8_TWIN_CELLS:
                    out.append({**base, "has_R8": True})
    return out


def dc_gain_to_K(topo, design_subs, dc_gain):
    """Convert a desired passband (HIGH-FREQUENCY) gain into the leading-
    coefficient K the solver's gain residual expects.

    For HP the passband gain IS the leading-coefficient ratio b_lead/a_lead
    = H(inf) (the numerator is K*s^order over a monic denominator), so for
    every gained/atten cell  K = desired HF gain  directly — no (rad/s)^n
    rescale (that LP factor came from matching H(0), which is the wrong end
    of an HP response). Unity cells ignore K (return None).
    """
    if topo["gain"] == "unity":
        return None
    return float(dc_gain)


def _gates(topo):
    order, gain, notch = topo["order"], topo["gain"], topo["notch"]
    return dict(
        gc1=s*C1 if order == 3 else 0,
        gc4=s*C4 if gain == "atten" else 0,
        g1=1/R1 if order == 3 else 0,
        gr2=1/R2 if notch else 0,
        g4=1/R4 if notch else 0,
        g6=1/R6,                                   # always present in HP
        g7=1/R7 if gain == "gained" else 0,
        g8=1/R8 if topo.get("has_R8") else 0,      # optional b->gnd attenuator
        shorted_r5=(gain in ("unity", "atten") and not notch),
        order=order, gain=gain, notch=notch,
    )


def var_list(topo):
    G = _gates(topo)
    caps = (([C1] if G["order"] == 3 else []) + [C2, C3]
            + ([C4] if G["gain"] == "atten" else []))
    resis = ([R1] if G["order"] == 3 else []) + ([R2] if G["notch"] else []) + [R3]
    resis += ([R4] if G["notch"] else [])
    if not G["notch"] and not G["shorted_r5"]:
        resis.append(R5)                           # notchless-gained: R5 is free
    resis += [R6]                                  # always
    resis += ([R7] if G["gain"] == "gained" else [])
    resis += ([R8] if topo.get("has_R8") else [])  # optional b->gnd attenuator
    return caps + resis


# =====================================================================
# IDEAL derivation  (virtual short: Vm -> Vp)
# =====================================================================
def build_ideal(topo, target_subs):
    G = _gates(topo); V1 = 1
    gc1, gc4, g1, gr2, g4, g6, g7, g8 = (G["gc1"], G["gc4"], G["g1"], G["gr2"],
                                         G["g4"], G["g6"], G["g7"], G["g8"])

    # node a (order-3 only); node b; node p (op-amp +, no current); node m
    eqa = (gc1*(Va - V1) + s*C2*(Va - Vb) + g1*Va
           + gr2*(Va - Vp) + g4*(Va - Vp))
    eqb = (s*C2*(Vb - Va) + s*C3*(Vb - Vp) + gc4*Vb + g8*Vb + g6*(Vb - V2))
    eqp = (s*C3*(Vp - Vb) + (1/R3)*Vp + gr2*(Vp - Va))
    if G["shorted_r5"]:
        eqm = V2 - Vp                              # R5 short -> Vm = V2 = Vp
    else:
        eqm = g4*(Vp - Va) + (1/R5)*(Vp - V2) + g7*Vp

    if G["order"] == 3:
        unk = [Va, Vb, Vp, V2]; eqs = [eqa, eqb, eqp, eqm]
    else:
        unk = [Vb, Vp, V2]; eqs = [e.subs(Va, V1) for e in [eqb, eqp, eqm]]

    A, b = sp.linear_eq_to_matrix(eqs, unk)
    T = sp.simplify(A.LUsolve(b)[unk.index(V2)])
    num, den = sp.fraction(sp.together(T))
    num = sp.expand(num); den = sp.expand(den)
    num_poly = sp.Poly(num, s); den_poly = sp.Poly(den, s)
    a_lead = den_poly.LC(); b_lead = num_poly.LC()
    den_norm = den_poly.monic().as_expr()

    # Monic coefficients (functions of the components, R5 still symbolic) split
    # into powers of s BEFORE substituting the R5 constraint. The
    # post-substitution denominator is a deeply nested rational, and
    # sp.Poly(<that>, s) is pathologically slow (sympy puts it over a common
    # denominator and chokes -- >30s for the 3rd-order gained+notch cell).
    # Splitting first, then substituting the small R5c into each isolated
    # scalar coeff and cancelling, is cheap and exact (~0.01s vs hang).
    den_monic_co = sp.Poly(den_norm, s).all_coeffs()                    # fast
    num_monic_co = sp.Poly(num_poly.monic().as_expr(), s).all_coeffs()  # fast

    # R5 algebraic constraint (notch only): zero the inner (s^2+wz^2) block's
    # s^1 coefficient so the notch is symmetric (purely-imaginary zero pair).
    #   2nd-order numerator  K*(s^2 + a1 s + a0)        -> zero the s^1 coeff
    #   3rd-order numerator  K*s*(s^2 + a1 s + a0)      -> zero the s^2 coeff
    #     (the lone origin zero makes the inner-block s^1 coeff the GLOBAL s^2)
    R5c = None
    if G["notch"] and not G["shorted_r5"]:
        tgt = num_monic_co[-2] if G["order"] == 2 else num_monic_co[-3]
        sols = sp.solve(tgt, R5)
        R5c = sols[0] if sols else None
    sr5 = {R5: R5c} if R5c is not None else {}

    def _apply(c):                          # substitute R5c into ONE scalar coeff
        return sp.cancel(c.subs(sr5)) if sr5 else c

    dcs = [_apply(c) for c in den_monic_co]   # monic denominator coeffs, R5 applied
    if G["order"] == 3:
        dt = sp.expand((s + p1)*(s**2 + (w0/Q)*s + w0**2))
    else:
        dt = sp.expand(s**2 + (w0/Q)*s + w0**2)
    tcs = sp.Poly(dt, s).all_coeffs()

    res = [(dcs[i] - tcs[i]) / tcs[i] for i in range(1, len(dcs))]
    # coefficients for sensitivity scoring (last two denominator coeffs)
    a1_expr = dcs[-2]
    a2_expr = dcs[-3] if len(dcs) >= 3 else dcs[-2]
    if G["notch"]:
        # constant of the inner (s^2+wz^2) block = wz^2:
        #   2nd-order -> global s^0 coeff; 3rd-order -> global s^1 coeff
        b_wz = (_apply(num_monic_co[-1]) if G["order"] == 2
                else _apply(num_monic_co[-2]))
        res.append((b_wz - wz**2) / wz**2)
    if G["gain"] in ("gained", "atten"):
        # HF gain = leading-coeff ratio = H(inf). gained: resistive (1+R5/R7);
        # atten: capacitive divider (caps-only). K carries the target HF gain.
        H = _apply(b_lead / a_lead)
        res.append((H - K) / K)
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
    gc1, gc4, g1, gr2, g4, g6, g7, g8 = (G["gc1"], G["gc4"], G["g1"], G["gr2"],
                                         G["g4"], G["g6"], G["g7"], G["g8"])
    wc = 2*sp.pi*GBWP_hz
    A_s = A_ol*wc / (wc + s*A_ol)

    # R4 connects node a to the op-amp (-) node Vm (kept separate here).
    eqa = (gc1*(Va - V1) + s*C2*(Va - Vb) + g1*Va
           + gr2*(Va - Vp) + g4*(Va - Vm))
    eqb = (s*C2*(Vb - Va) + s*C3*(Vb - Vp) + gc4*Vb + g8*Vb + g6*(Vb - V2))
    eqp = (s*C3*(Vp - Vb) + (1/R3)*Vp + gr2*(Vp - Va))
    if G["shorted_r5"]:
        eqm = Vm - V2
        eqout = (V2 - A_s*(Vp - Vm))/Ro + g6*(V2 - Vb)
    else:
        eqm = g4*(Vm - Va) + (1/R5)*(Vm - V2) + g7*Vm
        eqout = (V2 - A_s*(Vp - Vm))/Ro + (1/R5)*(V2 - Vm) + g6*(V2 - Vb)

    if G["order"] == 3:
        unk = [Va, Vb, Vp, Vm, V2]; eqs = [eqa, eqb, eqp, eqm, eqout]
    else:
        unk = [Vb, Vp, Vm, V2]
        eqs = [e.subs(Va, V1) for e in [eqb, eqp, eqm, eqout]]

    A, b = sp.linear_eq_to_matrix(eqs, unk)
    V2_sol = A.LUsolve(b)[unk.index(V2)]
    num, den = sp.fraction(sp.together(V2_sol))   # NO expand
    tf_syms = sorted([x for x in (num + den).free_symbols
                      if x != s and not x.is_number], key=str)
    return {
        "kind": "nonideal", "topo": topo,
        "den_degree": None, "num_degree": None,
        "tf_num": num, "tf_den": den, "tf_var_list": tf_syms,
    }
