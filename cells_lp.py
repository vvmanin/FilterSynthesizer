# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cells_lp.py
#  Low-pass / LP-notch VCVS (Sallen-Key) cell family.
#
#  This is the ORIGINAL, validated tf_derivation_v2 LP derivation moved
#  verbatim behind the registry seam (ROADMAP §3). Behaviour is
#  byte-for-byte identical to the inlined version; only the surface
#  changed:
#    - symbols come from tf_symbols (shared identity)
#    - topo dicts carry an explicit  "family": "LP"
#    - the public entry points are the seam interface a cell module must
#      provide:  FAMILY, all_cells, topo_name, var_list, dc_gain_to_K,
#                build_ideal, build_nonideal
#  The generic engine (tf_derivation_v2) dispatches to these by
#  topo["family"]; Tier C consumes the resulting cases unchanged.
#
#  3-axis topology grid (12 cells):
#    order : 3 | 2          (3rd-order has a real pole; 2nd-order does not)
#    gain  : "gained" | "unity"
#    notch : True | False
#    has_R7: bool           (gained+notch twin, or unity+order-2 attenuator)
#
#  Op-amp port mapping (validated): (+)=Vc, (-)=Vm (R4||R5||R6 junction),
#  output behind Ro. Non-ideal TF kept as a RAW rational function.
# =====================================================================

import sympy as sp

from tf_symbols import (s, Va, Vb, Vc, Vm, V2,
                        R1, R2, R3, R4, R5, R6, R7, Ro,
                        C1, C2, C3, C4, A_ol, GBWP_hz,
                        p1, w0, wz, Q, K)

FAMILY = "LP"


# =====================================================================
# Topology descriptor helpers
# =====================================================================
def topo_name(topo):
    o, g, n, r7 = topo["order"], topo["gain"], topo["notch"], topo["has_R7"]
    base = f"{o}{'LPn' if n else 'LP'}"
    if g == "unity" and r7:
        return f"{base}-atten"      # unity SK + R7 input divider => DC gain < 1
    return f"{base}-{g}{'+R7' if r7 else ''}"


def all_cells():
    """The valid LP topology cells. R7 is allowed with gained+notch (the +R7
    twin) and with unity 2nd-order cells (the attenuator variant, DC gain<1)."""
    out = []
    for order in (3, 2):
        for gain in ("gained", "unity"):
            for notch in (True, False):
                if gain == "gained" and notch:
                    r7opts = [True, False]
                elif gain == "unity" and order == 2:
                    r7opts = [True, False]      # 2LP-atten / 2LPn-atten (2nd-order only)
                else:
                    r7opts = [False]
                for r7 in r7opts:
                    out.append({"family": FAMILY, "order": order, "gain": gain,
                                "notch": notch, "has_R7": r7})
    return out


def dc_gain_to_K(topo, design_subs, dc_gain):
    """Convert a desired DC gain into the leading-coefficient K that the
    solver's gain residual expects, in the correct (rad/s)^n units for this
    topology.

    DC gain = H(0) = K * b0 / a0, with a0/b0 the IDEAL target free (s^0)
    coefficients:  a0 = w0^2 (2nd) | p1*w0^2 (3rd);  b0 = wz^2 (notch) | 1.
    Unity cells ignore K (return None); the unity+R7 attenuator carries the
    dimensionless sub-unity DC gain directly.
    """
    if topo["gain"] == "unity":
        return float(dc_gain) if topo["has_R7"] else None
    w0v = float(design_subs[w0])
    a0 = (float(design_subs[p1]) * w0v**2) if topo["order"] == 3 else w0v**2
    b0 = float(design_subs[wz])**2 if topo["notch"] else 1.0
    return float(dc_gain) * a0 / b0


def _gates(topo):
    order, gain, notch, has_R7 = (topo["order"], topo["gain"],
                                  topo["notch"], topo["has_R7"])
    return dict(
        c2=s*C2 if notch else 0,
        g4=1/R4 if notch else 0,
        g6=1/R6 if gain == "gained" else 0,
        g7=1/R7 if has_R7 else 0,
        shorted_r5=(gain == "unity" and not notch),
        order=order, gain=gain, notch=notch, has_R7=has_R7,
    )


def var_list(topo):
    G = _gates(topo)
    caps = ([C1] if G["order"] == 3 else []) + ([C2] if G["notch"] else []) + [C3, C4]
    resis = ([R1] if G["order"] == 3 else []) + [R2, R3] + ([R4] if G["notch"] else [])
    if not G["notch"] and not G["shorted_r5"]:
        resis.append(R5)                    # notchless-gained: R5 is free
    resis += ([R6] if G["gain"] == "gained" else []) + ([R7] if G["has_R7"] else [])
    return caps + resis


# =====================================================================
# IDEAL derivation
# =====================================================================
def build_ideal(topo, target_subs):
    G = _gates(topo); V1 = 1
    c2, g4, g6, g7 = G["c2"], G["g4"], G["g6"], G["g7"]

    if G["order"] == 3:
        eq1 = V1/R1 - Va*(1/R1 + g4 + s*C1 + c2 + 1/R2) + Vb/R2 + Vc*(g4 + c2)
        unk = [Va, Vb, Vc, V2]; vin = False
    else:
        unk = [Vb, Vc, V2]; vin = True
    eq2 = Va/R2 - Vb*(1/R2 + s*C4 + 1/R3 + g7) + Vc/R3 + V2*s*C4
    eq3 = Va*c2 + Vb/R3 - Vc*(c2 + s*C3 + 1/R3)
    if G["shorted_r5"]:
        eq4 = V2 - Vc                        # unity follower
    else:
        g5 = 1/R5; eq4 = Va*g4 - Vc*(g5 + g4 + g6) + V2*g5
    eqs = [eq2, eq3, eq4] if vin else [eq1, eq2, eq3, eq4]
    if vin:
        eqs = [e.subs(Va, V1) for e in eqs]

    A, b = sp.linear_eq_to_matrix(eqs, unk)
    T = sp.simplify(A.LUsolve(b)[unk.index(V2)])
    num, den = sp.fraction(T)
    num_poly = sp.Poly(num, s); den_poly = sp.Poly(den, s)
    a_lead = den_poly.LC(); b_lead = num_poly.LC()
    den_norm = den_poly.monic().as_expr()

    # R5 algebraic constraint (notch only): zero the s^1 numerator coeff
    R5c = None
    if G["notch"] and not G["shorted_r5"]:
        b1n = sp.Poly(num_poly.monic().as_expr(), s).all_coeffs()[1]
        sols = sp.solve(b1n, R5)
        R5c = sols[0] if sols else None
    sr5 = {R5: R5c} if R5c is not None else {}

    den_sub = sp.Poly(den_norm.subs(sr5), s)
    dcs = den_sub.all_coeffs()               # monic
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
        b0s = sp.Poly(num_poly.monic().as_expr().subs(sr5), s).all_coeffs()[-1]
        res.append((b0s - wz**2) / wz**2)
    if G["gain"] == "gained":
        H = (b_lead / a_lead).subs(sr5)      # leading-coeff K (dimension varies)
        res.append((H - K) / K)
    elif G["gain"] == "unity" and G["has_R7"]:
        # Attenuator: the R7 input divider sets a sub-unity DC gain. Constrain
        # H(0)=K, where K carries the *dimensionless* target DC gain (<1).
        num0 = num_poly.as_expr().subs(s, 0)
        den0 = den_poly.as_expr().subs(s, 0)
        H0 = (num0 / den0).subs(sr5)
        res.append((H0 - K) / K)
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
# NON-IDEAL derivation (raw rational TF)
# =====================================================================
def build_nonideal(topo):
    G = _gates(topo); V1 = 1
    c2, g4, g6, g7 = G["c2"], G["g4"], G["g6"], G["g7"]
    wc = 2*sp.pi*GBWP_hz
    A_s = A_ol*wc / (wc + s*A_ol)

    if G["order"] == 3:
        eq1 = V1/R1 - Va*(1/R1 + g4 + s*C1 + c2 + 1/R2) + Vb/R2 + Vc*(g4 + c2)
        unk = [Va, Vb, Vc, Vm, V2]; vin = False
    else:
        unk = [Vb, Vc, Vm, V2]; vin = True
    eq2 = Va/R2 - Vb*(1/R2 + s*C4 + 1/R3 + g7) + Vc/R3 + V2*s*C4
    eq3 = Va*c2 + Vb/R3 - Vc*(c2 + s*C3 + 1/R3)

    if G["shorted_r5"]:
        eq_vm = Vm - V2                                       # R5 short
        eq5 = (V2 - A_s*(Vc - Vm)) / Ro
    else:
        g5 = 1/R5
        eq_vm = Va*g4 - Vm*(g5 + g4 + g6) + V2*g5             # inverting node
        eq5 = (V2 - A_s*(Vc - Vm))/Ro + (V2 - Vc)*g5 + (V2 - Vb)*s*C4

    base = [eq1, eq2, eq3] if not vin else [eq2, eq3]
    eqs = base + [eq_vm, eq5]
    if vin:
        eqs = [e.subs(Va, V1) for e in eqs]

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
