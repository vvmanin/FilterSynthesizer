# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cells_mfb.py
#  Multiple-Feedback (Rauch / Friend) low-pass cell family.
#
#  Sibling to cells_lp.py (VCVS / Sallen-Key) on the orthogonal
#  TOPOLOGY-FAMILY axis. Registered under FAMILY = "LP-MFB"; the generic
#  engine (tf_derivation_v2) dispatches to it by topo["family"] exactly as
#  it does for the VCVS "LP" module, and the per-section topology radio
#  (VCVS | MFB | ...) selects which family's cells are offered for a given
#  cascade section.
#
#  TWELVE cells (the "LP MFB family"):
#     2LP-MFB     3LP-MFB        all-pole Rauch MFB              (inverting)
#     2LP-MFB-QE  3LP-MFB-QE     all-pole + positive-FB Q-boost (inverting)
#     2LPn-MFB    3LPn-MFB       low-pass notch (Friend SAB)    (NON-inverting)
#     ---- low-sensitivity LP-notch branch (all NON-inverting) ----------------
#     3LPn-MFB-LS         3LPn-MFB-LS+R7
#     2LPn-MFB-LS         2LPn-MFB-LS+R7
#     2LPn-MFB-LS+R1      2LPn-MFB-LS+R1+R7
#
#  Naming follows the schematic-not-operating-point rule: gain is an R-ratio
#  on a fixed circuit, so it earns NO suffix; QE adds the (R5,R6) +divider, so
#  it does. The LPn cell carries R8 (the V+ -> gnd leg) as a built-in element;
#  the R8-open special case is intentionally NOT a separate cell (it pins
#  H(inf)=1 / gain=wz^2/w0^2, makes R7 redundant, and reaches only a subset of
#  targets -- see ROADMAP notes).
#
#  ============ LOW-SENSITIVITY LP-NOTCH BRANCH (topo flag "ls") =============
#  A SECOND LP-notch realization: the R<->C dual of the MFB2 HP-notch with the
#  op-amp (+) divider left RESISTIVE.  ONE netlist, three optional elements:
#
#     C1  a-gnd    3rd order only (the LP input pole)
#     C2  a-b
#     C3  m-out
#     R1  in-a     3rd order, and the 2nd-order "+R1" cells
#     R2  a-p      R3  b-m    R4  b-out    R5  b-gnd    R6  p-gnd
#     R7  p-out    "+R7" cells only (positive feedback)
#     (+)=p (= Vc, the LP family's (+) symbol), (-)=m
#
#  ALL SIX carry numerator degree 2 (a lone zero at infinity in the 3rd-order
#  cells), so the notch residual indexing below (monic num coeffs [1, c1, c0])
#  serves every order VERBATIM -- unlike the HP family, whose notch numerator
#  degree equals the order.  SIGN = +1, as for the base LPn cell.
#
#  ---- what each optional element buys (all verified symbolically) ----------
#  Let  x = R4/R5,  r = wz^2/w0^2 = (fz/f0)^2 > 1,  g = DC gain.
#
#  2nd order, the master identity:      g / r = H(inf)
#
#  * BARE 2LPn-MFB-LS (7 parts: C2,C3,R2..R6).  The (+) node hangs on the
#    UNLOADED two-leg divider {R2,R6} straight off the source, so
#        H(inf) = R6/(R2+R6) = 1/(1+rho),   rho = R2/R6
#    and matching (w0,Q,wz,on-axis) caps rho <= 1/(r Q^2).  Hence
#        CEILING  g < r                 (H(inf) < 1)
#        FLOOR    g > r^2 Q^2/(1+r Q^2)
#    Q is realized PASSIVELY (the den s^1 coefficient has no negative term), so
#    S_Q < 1 and the finite-GBW null is ~12 dB deeper than the Friend SAB -- but
#    the price is a fixed capacitor ratio  C2/C3 = 4 Q^2 (1+x) >= 4 r Q^2, and
#    the cell has ZERO real DOF (7 vars, 5 residuals, and both remaining
#    directions are the trivial R<->C impedance scale and the {R2,R6} scale).
#
#  * +R7 (p->out).  Cannot touch the ceiling: at s->inf C3 shorts m to out, so
#    Vp = Vout and R7 carries no current -> H(inf) = R6/(R2+R6) unchanged.
#    It DOES relax the floor, exactly:
#        rho_max = 1/(2 Q sqrt(r) - 1)   attained at  R2 = R6 || R7
#        g_min   = r - sqrt(r)/(2 Q)              [window width O(1/Q) not O(1/Q^2)]
#    and -- the real prize -- Q becomes a pure RESISTOR ratio (no capacitance
#    survives in the Q expression), which ANNIHILATES the 4 r Q^2 cap-ratio law
#    (144x -> 4x at Q=5).  Cost: S_Q rises (0.74 -> 2.7..12).  R7 -> inf
#    degenerates back to the bare cell, so +R7 strictly contains it: the pair is
#    solved together and ranked by sens_score.
#
#  * +R1 (in->a).  Node `a` stops being the source, so the (+) divider gains a
#    THIRD leg:  H(0) = (1+x) * R6/(R1+R2+R6).  Attenuation moves out of rho
#    (still Q-bounded) and into R1/R6, which is unbounded:
#        FLOOR VANISHES -- the cell spans (0, r), atten / unity / gained
#        CEILING SURVIVES:  H(inf) = R3R4R5R6/M < 1 strictly, M > R3R4R5R6
#    Q stays passive (den s^1 numerator = C2 R1R2R5 + C3 S (R1+R2+R6), all
#    positive) so S_Q stays < 1 -- measured 0.29..0.57, i.e. BETTER than the
#    bare cell, since the extra DOF gives the optimizer room.  R1 does NOT
#    relieve the cap ratio; only R7 does.  The bare cell is the R1 -> 0 boundary
#    face of this one.
#
#  Consequence for the 3rd order: R1 (not the dropped a->out feedback cap) is
#  what buys attenuation, so 3LPn-MFB-LS spans (0, r) with no floor, and its
#  +R7 twin additionally breaks the ceiling.
#  ==========================================================================
#
#  SIGN CONVENTION (cascade tracks per-section sign, as for VCVS):
#     all-pole MFB : H(0) = -R4/R2          -> SIGN = -1  (inverting)
#     LPn      MFB : H(inf)=R8/(R3+R8) > 0  -> SIGN = +1  (non-inverting)
#  The non-inverting LPn sign is structural: the transmission zero requires
#  feed-forward into V+ (the R3 leg), and that feed-forward is what makes the
#  section non-inverting. There is no inverting realization of this netlist,
#  with or without R8.
#
#  NOTCH CONSTRAINT: R3 (the in->V+ feed-forward resistor) sets the on-axis
#  zero. Solving the s^1-numerator null for R3 always yields a ratio of
#  positive-component products (R3 > 0 guaranteed). Because the engine's
#  algebraic-constraint slot is hard-wired to the symbol R5 -- which in THIS
#  netlist is a real, independent element (m->gnd) -- the notch is enforced
#  here as an extra RESIDUAL (s^1 numerator -> 0) with R3 kept as a free
#  search variable, instead of via R5_constraint. No engine change required.
#
#  Op-amp port mapping:  (+) = Vc,  (-) = Vm,  output = V2 behind Ro.
#  Internal nodes:  a = Va (3rd-order input pole),  b = Vb.
#  Non-ideal TF kept as a RAW rational function (no expand), GBW one-pole
#  model identical to the VCVS module.
#
#  Op-amp node netlists (user convention)
#  --------------------------------------
#  ALL-POLE (a=in when 2nd order: R1 shorted, C1 open):
#     C1 a-gnd(3rd) | C2 b-gnd | C3 m-out
#     R1 in-a(3rd)  | R2 a-b   | R3 b-m  | R4 b-out
#     (+)=gnd (basic)  or  =p with R5 p-out, R6 p-gnd (QE)
#  LP-NOTCH (a=in when 2nd order):
#     C1 a-gnd(3rd) | C2 b-m | C3 b-out
#     R1 in-a(3rd)  | R2 a-b | R3 a-p | R4 b-gnd
#     R5 m-gnd | R6 m-out | R7 p-out | R8 p-gnd
#     (+)=p, (-)=m
#  LP-NOTCH-LS (a=in only on the bare 2nd-order cells):
#     C1 a-gnd(3rd) | C2 a-b | C3 m-out
#     R1 in-a(3rd, +R1) | R2 a-p | R3 b-m | R4 b-out | R5 b-gnd | R6 p-gnd
#     R7 p-out(+R7)
#     (+)=p, (-)=m
# =====================================================================

import sympy as sp

from tf_symbols import (s, Va, Vb, Vc, Vm, V2,
                        R1, R2, R3, R4, R5, R6, R7, R8, Ro,
                        C1, C2, C3, A_ol, GBWP_hz,
                        p1, w0, wz, Q, K)

FAMILY = "LP-MFB"

SIGN_ALLPOLE = -1      # H(0) = -R4/R2
SIGN_NOTCH   = +1      # Friend SAB: H(inf) = R8/(R3+R8) > 0
                       # LS branch:  H(inf) = R6/(R2+R6) > 0 (bare) / R3R4R5R6/M (+R1)


# =====================================================================
# Topology descriptor helpers
# =====================================================================
def topo_name(topo):
    o, notch, qe = topo["order"], topo["notch"], topo["qe"]
    if notch and topo.get("ls"):
        # R1 is IMPLICIT at 3rd order (it is the input-pole resistor and always
        # present); at 2nd order it is the optional attenuator leg, so it earns
        # a suffix there. R7 always earns one.
        sfx = ""
        if o == 2 and topo.get("r1"):
            sfx += "+R1"
        if topo.get("r7"):
            sfx += "+R7"
        return f"{o}LPn-MFB-LS{sfx}"
    if notch:
        return f"{o}LPn-MFB"
    return f"{o}LP-MFB{'-QE' if qe else ''}"


def all_cells():
    """The twelve LP-MFB cells: all-pole {basic, QE} x {2nd, 3rd}, the LP-notch
    Friend SAB {2nd, 3rd}, and the six low-sensitivity LP-notch cells.

    QE is meaningless for the notch (the Friend SAB's positive feedback is built
    into its topology; the LS cells spell theirs out as the optional R7), so all
    notch cells carry qe=False.

    The LS cells are NEVER derived or solved unless the user ticks "Lower
    Sensitivity MFB" -- topology_tab passes an explicit `topologies=[...]` list
    and tf_derivation_v2.get_cases() derives only the names it is given, so a
    default run costs exactly what it did before this branch existed."""
    out = []
    for order in (3, 2):
        for qe in (False, True):
            out.append({"family": FAMILY, "order": order,
                        "notch": False, "qe": qe})
        out.append({"family": FAMILY, "order": order,
                    "notch": True, "qe": False})
        for r7 in (False, True):
            if order == 3:                       # R1 (input pole) always present
                out.append({"family": FAMILY, "order": 3, "notch": True,
                            "qe": False, "ls": True, "r1": True, "r7": r7})
            else:
                for r1 in (False, True):         # bare pair + attenuating pair
                    out.append({"family": FAMILY, "order": 2, "notch": True,
                                "qe": False, "ls": True, "r1": r1, "r7": r7})
    return out


def _has_node_a(topo):
    """True when node `a` is a REAL node rather than the driven source, i.e.
    whenever the input series resistor R1 exists. That is every 3rd-order cell
    (R1 + C1 form the input pole) and, on the LS branch, the 2nd-order "+R1"
    cells. Everything downstream -- `vin_one`, the unknown list, the node-a KCL
    -- keys off this instead of `order == 3`, which used to be the same thing."""
    return topo["order"] == 3 or bool(topo.get("r1"))


def _sign(topo):
    return SIGN_NOTCH if topo["notch"] else SIGN_ALLPOLE


def dc_gain_to_K(topo, design_subs, dc_gain):
    """Desired passband DC gain -> leading-coefficient K (correct rad/s^n
    units, correct inherent sign). Gain is a free R-ratio on every MFB cell,
    so K is always returned (never None).

        H(0) = K * b0 / a0,  a0 = w0^2 (2nd) | p1*w0^2 (3rd),
                             b0 = wz^2 (notch) | 1 (all-pole)
    K carries the cell's inherent sign; |dc_gain| is the requested magnitude.

    Every LP-notch cell realizes K = H(inf) as a positive divider < 1, so the DC
    gain is structurally capped at r = (wz/w0)^2 = (fz/f0)^2. The BARE LS cells
    add a floor as well (see the module header); the +R1 cells do not. Targets
    outside the reachable band simply fail to zero the gain residual and degrade
    to an empty BOM through the usual solvability probe -- nothing special here.
    """
    w0v = float(design_subs[w0])
    a0 = (float(design_subs[p1]) * w0v**2) if topo["order"] == 3 else w0v**2
    b0 = float(design_subs[wz])**2 if topo["notch"] else 1.0
    return _sign(topo) * abs(float(dc_gain)) * a0 / b0


def var_list(topo):
    """Free search variables. R3 is FREE for the notch cells (it absorbs the
    s^1-null residual); there is no algebraically-derived component here.

    LS branch element set (C4 / R8 never appear):
        caps  C1 (3rd only), C2 (a-b), C3 (m-out)
        res   R1 (3rd or +R1), R2..R6, R7 (+R7 only)
    C2 is listed before C3 on purpose: the on-axis-zero condition forces
    C2/C3 = 4 Q^2 (1 + R4/R5) > 1, so C2 is ALWAYS the larger cap and belongs
    first -- unified_solver_v2.anchored_bounds pins it at C_max."""
    o, notch, qe = topo["order"], topo["notch"], topo["qe"]
    if notch and topo.get("ls"):
        caps = ([C1] if o == 3 else []) + [C2, C3]
        res = ([R1] if _has_node_a(topo) else []) + [R2, R3, R4, R5, R6]
        if topo.get("r7"):
            res += [R7]                      # positive feedback p->out
        return caps + res
    caps = ([C1] if o == 3 else []) + [C2, C3]
    res = ([R1] if o == 3 else []) + [R2, R3, R4]
    if notch:
        res += [R5, R6, R7, R8]
    elif qe:
        res += [R5, R6]                      # positive-feedback +divider
    return caps + res


# =====================================================================
# Nodal-equation builders
# =====================================================================
def _allpole_eqs(topo, Vmv, vin_one, opamp_src=None):
    """KCL list for the all-pole MFB LP core.
       Vmv      : voltage at the (-) node used in the b/m equations
                  (0 or Vc ideal; the free Vm symbol non-ideal).
       vin_one  : True -> 2nd order (a == in == 1, no node-a eqn, no R1/C1).
       opamp_src: None -> ideal (no explicit output eqn; V2 solved from m-node)
                  expr -> non-ideal Thevenin source A_s*(V+ - V-) for eq_out.
    """
    o, qe = topo["order"], topo["qe"]
    Vplus = Vc if qe else sp.Integer(0)
    eqs = []
    if not vin_one:                                  # 3rd-order input pole
        eqs.append((Va - 1)/R1 + s*C1*Va + (Va - Vb)/R2)
        a = Va
    else:
        a = sp.Integer(1)
    eqs.append((Vb - a)/R2 + s*C2*Vb + (Vb - Vmv)/R3 + (Vb - V2)/R4)   # node b
    eqs.append((Vmv - Vb)/R3 + s*C3*(Vmv - V2))                        # node m
    if qe:
        eqs.append((Vc - V2)/R5 + Vc/R6)                              # node p (+)
    if opamp_src is not None:                                         # node out
        out = ((V2 - opamp_src)/Ro + s*C3*(V2 - Vm) + (V2 - Vb)/R4)
        if qe:
            out += (V2 - Vc)/R5
        eqs.append(out)
    return eqs, Vplus


def _notch_eqs(topo, Vmv, vin_one, opamp_src=None):
    """KCL list for the LP-notch (Friend SAB) MFB core. (+) = Vc, (-) via Vmv."""
    o = topo["order"]
    eqs = []
    if not vin_one:
        eqs.append((Va - 1)/R1 + s*C1*Va + (Va - Vb)/R2 + (Va - Vc)/R3)  # node a
        a = Va
    else:
        a = sp.Integer(1)
    eqs.append((Vb - a)/R2 + Vb/R4 + s*C2*(Vb - Vmv) + s*C3*(Vb - V2))   # node b
    eqs.append((Vc - a)/R3 + (Vc - V2)/R7 + Vc/R8)                       # node p (+)
    eqs.append(Vmv/R5 + (Vmv - V2)/R6 + s*C2*(Vmv - Vb))                 # node m
    if opamp_src is not None:                                           # node out
        eqs.append((V2 - opamp_src)/Ro + (V2 - Vm)/R6
                   + s*C3*(V2 - Vb) + (V2 - Vc)/R7)
    return eqs, Vc


def _notch_eqs_ls(topo, Vmv, vin_one, opamp_src=None):
    """KCL list for the LOW-SENSITIVITY LP-notch core. (+) = Vc, (-) via Vmv.

        C1 a-gnd (3rd)  | C2 a-b | C3 m-out
        R1 in-a  (3rd, +R1) | R2 a-p | R3 b-m | R4 b-out | R5 b-gnd | R6 p-gnd
        R7 p-out (+R7 only)

    Structure notes that the rest of the pipeline relies on:
      * `vin_one` is True only for the BARE 2nd-order cells (a == in). There the
        (+) divider {R2,R6} (+R7) is unloaded and fed by ideal sources only, so
        its common scale is a free DOF -- unified_solver_v2.rescale_isolated_r5r6
        pins it. With R1 present node `a` is real, R2 loads it, and the
        invariance disappears (verified numerically).
      * Without R7 the denominator's s^1 coefficient is a sum of strictly
        positive terms: Q is realized passively, which is the whole point of the
        LS branch (S_Q < 1). R7 is the single sign-indefinite element.
      * The numerator is degree 2 for BOTH orders, so build_ideal's fixed
        [1, c1, c0] monic indexing below applies unchanged.
    """
    o = topo["order"]
    has_r7 = bool(topo.get("r7"))
    eqs = []
    if not vin_one:                                   # node a is a real node
        na = (Va - 1)/R1 + (Va - Vc)/R2 + s*C2*(Va - Vb)
        if o == 3:
            na += s*C1*Va                             # LP input pole (3rd only)
        eqs.append(na)                                                # node a
        a = Va
    else:
        a = sp.Integer(1)
    eqs.append(s*C2*(Vb - a) + (Vb - Vmv)/R3 + (Vb - V2)/R4 + Vb/R5)  # node b
    p_eq = (Vc - a)/R2 + Vc/R6
    if has_r7:
        p_eq += (Vc - V2)/R7                          # positive feedback
    eqs.append(p_eq)                                                  # node p (+)
    eqs.append((Vmv - Vb)/R3 + s*C3*(Vmv - V2))                       # node m (-)
    if opamp_src is not None:                                        # node out
        out = (V2 - opamp_src)/Ro + s*C3*(V2 - Vmv) + (V2 - Vb)/R4
        if has_r7:
            out += (V2 - Vc)/R7
        eqs.append(out)
    return eqs, Vc


def _builder_for(topo):
    """Nodal-equation builder: all-pole Rauch, the Friend-SAB notch, or the
    low-sensitivity notch branch."""
    if not topo["notch"]:
        return _allpole_eqs
    return _notch_eqs_ls if topo.get("ls") else _notch_eqs


def _unknowns(topo, nonideal):
    notch, qe = topo["notch"], topo["qe"]
    u = [Vb]
    if notch or qe:
        u.append(Vc)
    if nonideal:
        u.append(Vm)
    u.append(V2)
    if _has_node_a(topo):        # == (order == 3) for every non-LS cell
        u = [Va] + u
    return u


# =====================================================================
# Conductance-cleared transfer-function solver
#
# LUsolve on a matrix with 1/R entries injects spurious high-degree s-factors
# that sp.cancel must then grind away over ~11 symbols (minutes on the 3rd-order
# notch). Instead: substitute R -> 1/G so every matrix entry is POLYNOMIAL,
# take Cramer determinants (det(A) is the minimal characteristic polynomial,
# det(A|V2<-b) the numerator -- no spurious factors), then substitute G -> 1/R
# back. No multivariate cancel is needed: any common s-free (G,C) factor between
# num and den drops out of every monic ratio and of the leading-coeff ratio.
# =====================================================================
_RES_SYMS = (R1, R2, R3, R4, R5, R6, R7, R8)
_G_SYMS = sp.symbols("G1 G2 G3 G4 G5 G6 G7 G8")
_R2G = {R: 1/g for R, g in zip(_RES_SYMS, _G_SYMS)}
_G2R = {g: 1/R for R, g in zip(_RES_SYMS, _G_SYMS)}


def _solve_tf_G(eqs, unk, out_sym):
    """Return (num_G, den_G): s-polynomials with (G,C) coefficients for the
    out_sym transfer function. Operates entirely in conductance space."""
    eqsG = [e.subs(_R2G) for e in eqs]
    A, b = sp.linear_eq_to_matrix(eqsG, unk)
    idx = unk.index(out_sym)
    den_G = A.det()
    Ab = A.copy(); Ab[:, idx] = b
    num_G = Ab.det()
    return sp.expand(num_G), sp.expand(den_G)


# =====================================================================
# IDEAL derivation
# =====================================================================
def build_ideal(topo, target_subs):
    o, notch = topo["order"], topo["notch"]
    vin_one = not _has_node_a(topo)     # (o == 2) for every non-LS cell
    Vmv = Vc if (notch or topo["qe"]) else sp.Integer(0)   # virtual short V- = V+
    builder = _builder_for(topo)
    eqs, _ = builder(topo, Vmv, vin_one, opamp_src=None)
    unk = _unknowns(topo, nonideal=False)

    # poly coefficient work stays in conductance space (clean field, fast);
    # convert the returned TF to R-form for response eval / tf_var_list.
    num_G, den_G = _solve_tf_G(eqs, unk, V2)
    num_poly = sp.Poly(num_G, s)
    den_poly = sp.Poly(den_G, s)
    num = sp.expand(num_G).subs(_G2R)
    den = sp.expand(den_G).subs(_G2R)
    a_lead, b_lead = den_poly.LC(), num_poly.LC()
    dcs = den_poly.monic().all_coeffs()

    # target denominator
    if o == 3:
        dt = sp.expand((s + p1)*(s**2 + (w0/Q)*s + w0**2))
    else:
        dt = sp.expand(s**2 + (w0/Q)*s + w0**2)
    tcs = sp.Poly(dt, s).all_coeffs()
    res = [(dcs[i] - tcs[i]) / tcs[i] for i in range(1, len(dcs))]

    # notch residuals: s^1 numerator -> 0 (on-axis), s^0/s^2 -> wz^2.
    # Both LP-notch realizations (Friend SAB and the LS branch) have numerator
    # degree 2 at BOTH orders, so this fixed [1, c1, c0] indexing serves them all.
    if notch:
        ncs = num_poly.monic().all_coeffs()        # [1, c1, c0]
        res.append(ncs[1] / wz)                     # on-axis (drive to 0)
        res.append((ncs[2] - wz**2) / wz**2)        # zero frequency

    # gain residual (always: gain is a free R-ratio on every MFB cell)
    res.append((b_lead / a_lead - K) / K)

    res = [e.subs(target_subs).subs(_G2R) for e in res]
    a1_expr = dcs[-2]
    a2_expr = dcs[-3] if len(dcs) >= 3 else dcs[-2]

    tf_syms = sorted([x for x in (num + den).free_symbols
                      if x != s and not x.is_number], key=str)
    return {
        "kind": "ideal", "topo": topo,
        "den_degree": int(den_poly.degree()), "num_degree": int(num_poly.degree()),
        "res_eqs": res, "R5_constraint": None,
        "a1_expr": a1_expr.subs(target_subs).subs(_G2R),
        "a2_expr": a2_expr.subs(target_subs).subs(_G2R),
        "var_list": var_list(topo),
        "tf_num": num, "tf_den": den, "tf_var_list": tf_syms,
    }


# =====================================================================
# NON-IDEAL derivation (raw rational TF, finite-GBW one-pole op-amp)
# =====================================================================
def build_nonideal(topo):
    o, notch = topo["order"], topo["notch"]
    vin_one = not _has_node_a(topo)     # (o == 2) for every non-LS cell
    wc = 2*sp.pi*GBWP_hz
    A_s = A_ol*wc / (wc + s*A_ol)
    builder = _builder_for(topo)

    # placeholder source; Vplus is returned so we can form A_s*(V+ - Vm)
    eqs_tmp, Vplus = builder(topo, Vm, vin_one, opamp_src=sp.Integer(0))
    # rebuild with the real source now that Vplus is known
    src = A_s*(Vplus - Vm)
    eqs, _ = builder(topo, Vm, vin_one, opamp_src=src)
    unk = _unknowns(topo, nonideal=True)

    A, b = sp.linear_eq_to_matrix(eqs, unk)
    V2_sol = A.LUsolve(b)[unk.index(V2)]
    num, den = sp.fraction(sp.together(V2_sol))      # NO expand
    tf_syms = sorted([x for x in (num + den).free_symbols
                      if x != s and not x.is_number], key=str)
    return {
        "kind": "nonideal", "topo": topo,
        "den_degree": None, "num_degree": None,
        "tf_num": num, "tf_den": den, "tf_var_list": tf_syms,
    }
