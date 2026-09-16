# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cells_mfb_bp.py
#  Multiple-Feedback (Rauch / Friend) BAND-pass cell family — the classic
#  single-op-amp MFB band-pass biquad, plus a positive-feedback
#  Q-enhancement (QE) variant.  Registered under FAMILY = "BP-MFB"; the
#  generic engine (tf_derivation_v2) dispatches to it by topo["family"]
#  exactly as it does for the VCVS "BP" module and the LP/HP-MFB modules,
#  and the per-section topology radio (VCVS | MFB | ...) selects which
#  family's cells are offered for a given cascade section.
#
#  SIX cells (the "BP MFB family"), all single-op-amp, all INVERTING:
#     2BP-MFB         the plain Rauch MFB band-pass              (2nd order)
#     2BP-MFB-QE      MFB band-pass + positive-FB Q-boost        (2nd order)
#     2BP1HP-MFB      + absorbed real pole, num ~ s^2            (3rd order)
#     2BP1HP-MFB-QE   ... with the positive-FB Q-boost           (3rd order)
#     2BP1LP-MFB      + absorbed real pole, num ~ s^1            (3rd order)
#     2BP1LP-MFB-QE   ... with the positive-FB Q-boost           (3rd order)
#
#  ============ 3rd-ORDER ASYMMETRIC BAND-PASS (topo flag "absorb") ==========
#  An asymmetric band-pass with an odd pole count leaves ONE real pole over.
#  Rather than spend a whole op-amp on a standalone 1st-order section, absorb it
#  into the band-pass biquad -- exactly what 3LP-MFB / 3HP-MFB do for their
#  families. The core is UNTOUCHED; only the input branch changes, so every
#  structural fact below (single-op-amp, inverting, no R5 constraint, no
#  numerator residual) carries over verbatim.
#
#    absorb="hp"  (2BP1HP-MFB):  break in->R1, insert a SERIES cap
#                    Vin -C3- x -R1- a
#                 num = -C3 C1 R2 R3 * s^2   (s^1 and s^0 STRUCTURALLY zero)
#                 den = cubic.  Ki = -1/(C2 R1)              [rad/s]
#                 The extra pole steepens the LOWER skirt.
#                 NO shunt resistor is needed at node x: the core ALREADY owns
#                 a shunt leg at node a (R2, a->gnd), so {C3, R1} against R2 IS
#                 the high-pass input pole. A variant with an extra R0 from x to
#                 gnd was evaluated and rejected: it relieves the resistor
#                 spread by ~2% (it only reshuffles between the delta and eps
#                 reduced coordinates and never touches R3/(R1||R2), where the
#                 4Q^2 law lives) while costing a part and a noise source.
#
#    absorb="lp"  (2BP1LP-MFB):  break in->R1, insert a series R and a shunt C
#                    Vin -R6- x , x -C3- gnd , x -R1- a
#                 num = -C1 R2 R3 * s        (s^0 STRUCTURALLY zero)
#                 den = cubic.  Ki = -1/(C3 C2 R6 R1)        [(rad/s)^2]
#                 The extra pole steepens the UPPER skirt.
#                 R6 is MANDATORY here (unlike the HP cell's missing R0): a
#                 shunt cap alone would sit straight across the ideal input
#                 source and do nothing at all.
#
#  SYMBOL <-> DESIGNATOR MAP (the AM-family convention, cells_am/schematic_svg):
#  the solver symbols stay inside the R1..R8 / C1..C4 space that every Tier-C
#  enumeration already walks (unified_solver_v2._assemble's BOM writeout,
#  zero_manifold_solver, topology_tab.COMP_ORDER, the Monte-Carlo sampler), and
#  the input-network parts are RE-LABELLED at the display layer:
#        solver C3  ->  schematic/BOM designator  C0
#        solver R6  ->  schematic/BOM designator  R0
#  This is exactly how the AM 3rd-order prefilter prints its R1/C4 as R0/C0
#  (topology_tab._am_row_designators + schematic_svg._am_labels). Adding real
#  C0/R0 sympy symbols instead would have required extending ~8 hard-coded
#  component lists, any one of which would silently drop the part from the BOM.
#
#  DEGREES OF FREEDOM (vars - residuals - redundancies):
#     2BP-MFB       5 - 3 - 1(Z-scale)              = 1
#     2BP1HP-MFB    6 - 4 - 1                       = 1
#     2BP1LP-MFB    7 - 4 - 1                       = 2
#     2BP1HP-MFB-QE 8 - 4 - 2(Z-scale, R4/R5 scale) = 2
#     2BP1LP-MFB-QE 9 - 4 - 2                       = 3
#
#  REACHABILITY (2BP1HP-MFB; derived, verified symbolically + numerically).
#  In reduced coordinates alpha = 1/(C3 R1), beta = (C1+C2)/(C1 C2 R3),
#  delta = 1/(C1 C2 R2 R3), eps = 1/(C1 C2 R1 R3) the monic cubic is
#        d2 = alpha + beta ,  d1 = alpha beta + delta + eps ,  d0 = alpha delta
#  and component positivity reduces to  -D(-alpha) > 0  where D is the monic
#  TARGET cubic. Since D(-a) = (p1 - a)(a^2 - (w0/Q) a + w0^2) and the quadratic
#  factor is strictly positive for any Q > 0.5, this collapses to
#        p1 < alpha < p1 + w0/Q          (plus the gain condition Ki beta > eps)
#  a non-empty OPEN interval for EVERY target: eps -> 0+ as alpha -> p1+, so any
#  positive Ki is reachable. There is NO gain ceiling and NO gain floor on these
#  cells -- unlike the HP-notch families. C1 -> inf at the lower edge and
#  C2 -> inf at the upper, so the capacitor spread has a clean interior minimum
#  and the search is a well-shaped 1-D problem with no flat direction.
#  2BP1LP-MFB has the same alpha window plus 0 < nu < min(alpha, d0/S),
#  nu = 1/(C3 R6).
#
#  CAVEAT the solver must respect: the window is narrow when p1 >> w0/Q (its
#  relative width is w0/(Q p1) -- down to ~4e-4 in the f1/f0 = 10, Q = 15
#  corner), so the product C3*R1 has to be placed to ~0.04% relative accuracy.
#  A blind log-uniform multistart will not find that sliver by chance, so
#  analytic_seeds() below emits closed-form starts that invert the coefficient
#  map exactly (to ~1e-16); unified_solver_v2 appends them to the Phase-1 task
#  list ADDITIVELY, on top of the unchanged legacy ratio/anchored passes.
#
#  SPREAD: the 3rd-order cells inherit the 2nd-order cell's resistor law almost
#  exactly -- min R3/(R1||R2) ~ 4 Q^2 (measured 16.7 / 104 / 916 / 6448 at
#  Q = 2 / 5 / 15 / 40 against 4Q^2 = 16 / 100 / 900 / 6400). The extra input
#  pole does NOT relieve it; the QE twin does (measured 2.6 / 8.3 / 27.6 / 75.5
#  at the same Q), which is why all four cells ship as two QE pairs. Cap spread
#  stays small (typically < 15). sens_score is low and flat at ~2.0-2.3 across
#  the whole (f1/f0, Q) grid, because Q is realized PASSIVELY on the non-QE
#  cells (beta is a sum of strictly positive terms -- no sign-indefinite
#  element), the same property that makes the plain 2BP-MFB well-behaved.
#  ==========================================================================
#
#  The two 2nd-order cells are INVERTING (sign -1, see below) with a SINGLE-s-term numerator
#  (one transmission zero at the origin, one at infinity — the standard
#  2nd-order band-pass shape), so — exactly like the VCVS band-pass —
#  neither carries a derived-R5 constraint nor any numerator residual.
#  The two cells are solved TOGETHER in one BOM list and ranked by
#  sens_score (the engine does this automatically when both names are in
#  the topology list): the QE cell reaches high Q with a gentler component
#  spread, the plain cell is simpler where Q is modest, and whichever wins
#  on sensitivity for a given (f0, Q, Ki) target rises to the top.
#
#  User-provided nodal netlists (authoritative)
#  --------------------------------------------
#  Nodes: in, a, out(=op-amp output V2), m(op-amp -), p(op-amp +), gnd
#  2BP-MFB-QE:
#     C1 a-m | C2 a-out
#     R1 in-a | R2 a-gnd | R3 m-out | R4 p-gnd | R5 p-out
#     (+)=p, (-)=m, out=V2
#  2BP-MFB (plain): the SAME core minus the {R4,R5} positive-feedback
#     divider; the op-amp (+) input is tied to gnd (Vp = 0).
#
#  STRUCTURAL FACTS (derived; verified in-session against the symbolic TF)
#  ---------------------------------------------------------------------
#  PLAIN 2BP-MFB (Vp = Vm = 0):
#     num =  -C1 R2 R3 * s                    (single s term; s^0 == 0)
#     den (monic) : s^2 + (C1+C2)/(C1 C2 R3) s + (R1+R2)/(C1 C2 R1 R2 R3)
#       w0^2  = (1/R1 + 1/R2)/(C1 C2 R3)
#       w0/Q  = (C1 + C2)/(C1 C2 R3)
#     Leading-coefficient gain  Ki = b_lead/a_lead = -1/(R1 C2)  [rad/s] < 0
#       -> the stage is INVERTING (SIGN = -1). |H(jw0)| = |Ki| Q/w0.
#
#  QE 2BP-MFB-QE (adds the R4 p->gnd / R5 p->out positive-FB divider,
#  Vp = V2 * R4/(R4+R5), virtual short Vm = Vp):
#     num =  -C1 R2 R3 (R4+R5) * s            (still a single s term)
#     Ki  =  -(R4+R5)/(C2 R1 R5)  [rad/s] < 0  (manifestly negative for
#            positive R,C) -> INVERTING as well (SIGN = -1, hard-coded).
#     The s^1 denominator coefficient GAINS a negative term
#        w0/Q  =  [ (C1+C2) R1 R2 R5 - C1 R3 R4 (R1+R2) ] / (C1 C2 R1 R2 R3 R5)
#              =  [ (C1+C2) - kappa C1 R3 (1/R1 + 1/R2) ] / (C1 C2 R3),
#        kappa = R4/R5
#     i.e. the positive feedback SUBTRACTS from the damping -> a Q BOOST
#     (setting R4 -> 0, i.e. Vp -> 0, recovers the plain cell exactly).
#     NOTE the subtracted term carries R3 (the m->out feedback resistor) and
#     scales with kappa = R4/R5 -- NOT R2 and not R4/(R4+R5). An earlier header
#     printed "C1 R4 (1 + R2/R1)/(R4+R5)"; that is wrong and hid the R3
#     dependence, which is precisely the lever the Q-enhancement turns.
#     Writing the damping as a1 = beta - Delta with beta = (C1+C2)/(C1 C2 R3)
#     (the PASSIVE damping) and Delta = kappa (1/R1 + 1/R2)/C2, the enhancement
#     factor is E = Q/Q_passive = beta/(beta - Delta). Every log-derivative of
#     a1 picks up that same factor, so sens_score ~ E * (plain-cell score)
#     while the resistor spread law relaxes as R3/(R1||R2) = 4 (Q/E)^2 (equal
#     caps). That is the whole trade: QE buys spread with sensitivity, one for
#     one. The plain cell therefore wins on sens_score at every E > 1 and the
#     QE cell earns its place only where 4 Q^2 has overrun [R_min, R_max].
#     Because R4 and R5 enter the response ONLY through the divider ratio
#     R4/(R4+R5), their COMMON scale is a free, redundant DOF (verified: H
#     invariant under R4,R5 *k to ~1e-16) — handled by
#     unified_solver_v2.rescale_isolated_r5r6 exactly like the HP-MFB-QE
#     R4/R5 divider (its R<->C-swapped twin).
#
#  GAIN CONVENTION (band-pass): the biquad-pairing stage hands down the
#  rad/s numerator coefficient Ki = b_lead/a_lead directly (units rad/s),
#  which IS the leading coefficient the gain residual targets — so
#  K = Ki with no (rad/s)^n rescale.  The cell is inverting, so the
#  residual drives b_lead/a_lead to -K (= SIGN*K); |Ki| = K sets the
#  center-frequency magnitude |H(jw0)| = |Ki| Q/w0.  cfg["K"] carries the
#  (positive) magnitude; the sign is carried by the cascade via
#  out["sign"] = -1 (unified_solver_v2._assemble), the same split the VCVS
#  notch and the LP/HP-MFB all-pole cells use.
#
#  Op-amp node mapping:  (+) = Vp,  (-) = Vm,  output = V2 behind Ro.
#  Non-ideal TF kept as a RAW rational function (no expand), GBW one-pole
#  model identical to the VCVS / LP-MFB / HP-MFB modules.
#
#  Validated (tf_derivation_v2.self_test): denominator degree == 2; exactly
#  ONE finite zero, at the origin, that does not cancel a pole
#  (nzeros == {1}); ideal-limit (non-ideal -> ideal) max err ~1e-11.
# =====================================================================

import numpy as np
import sympy as sp

from tf_symbols import (s, Va, Vb, Vp, Vm, V2,
                        R1, R2, R3, R4, R5, R6, R7, R8, Ro,
                        C1, C2, C3, C4, A_ol, GBWP_hz,
                        p1, w0, wz, Q, K)

FAMILY = "BP-MFB"

SIGN = -1      # both cells invert: Ki = b_lead/a_lead < 0 (see header)


# =====================================================================
# Topology descriptor helpers
# =====================================================================
def topo_name(topo):
    # The plain Rauch MFB band-pass is "2BP-MFB"; the positive-feedback
    # Q-enhancement variant adds the "-QE" suffix (same schematic-not-
    # operating-point rule as the LP/HP-MFB families).
    #
    # The 3rd-order cells spell the absorbed pole out as "2BP1LP" / "2BP1HP"
    # rather than following the LP/HP families' plain order prefix. "3BP-MFB"
    # would be AMBIGUOUS -- it cannot say whether the absorbed real pole
    # steepens the upper skirt (num ~ s, the LP absorption) or the lower one
    # (num ~ s^2, the HP absorption) -- and those are different netlists with
    # different reachable sets, so they must be different names.
    ab = topo.get("absorb")
    qe = "-QE" if topo.get("qe") else ""
    if ab == "hp":
        return f"2BP1HP-MFB{qe}"
    if ab == "lp":
        return f"2BP1LP-MFB{qe}"
    return f"2BP-MFB{qe}"


def all_cells():
    """The six BP-MFB cells: {plain, +absorbed-HP, +absorbed-LP} x {basic, QE}.

    notch is carried (= False always) so the family-agnostic self_test, which
    reads topo["notch"], keeps working; has_R7 (= False always) is carried so
    Tier-C code that reads topo["has_R7"] keeps working. "absorb" is None for
    the 2nd-order pair and "hp"/"lp" for the 3rd-order cells, and "order"
    follows it (2 vs 3) so _build_cfg populates cfg["f1"] and build_ideal
    targets the cubic.

    The two 2nd-order cells are emitted FIRST so any caller that indexed this
    list positionally keeps seeing them where they were.
    """
    out = []
    for absorb in (None, "hp", "lp"):
        for qe in (False, True):
            out.append({"family": FAMILY, "order": 2 if absorb is None else 3,
                        "absorb": absorb, "qe": qe, "notch": False,
                        "has_R7": False})
    return out


def dc_gain_to_K(topo, design_subs, dc_gain):
    """Convert a desired numerator coefficient into the leading-coefficient K
    the gain residual expects.

    For the band-pass the pairing stage hands down Ki = b_lead/a_lead
    (units rad/s) directly, which IS the leading coefficient the residual
    targets — so K = Ki with no (rad/s)^n rescale (that LP factor came from
    matching a DC value against a rad/s-normalised K; here the target
    already IS the rad/s leading coefficient).  K carries the (positive)
    magnitude; the cell's inverting sign is applied inside the gain residual
    (SIGN*K) and reported via out["sign"] by the solver.  Band-pass sections
    normally pass dc_gain=None and let cfg["K"] carry Ki, so this is a
    magnitude passthrough for the rare explicit-gain path."""
    return abs(float(dc_gain))


def _design_point(ab, qe, d2, d1, d0, Ki, alpha, nu, tau):
    """ONE closed-form inversion of the coefficient map, covering all four
    3rd-order cells. Returns {name: value} with R3 = 1 ohm, or None if this
    (alpha, nu, tau) point falls outside the positive-component set.

    Reduced coordinates (header):
        alpha = 1/(C3 R1) + 1/(C3 R6)      lam = alpha - nu
        nu    = 1/(C3 R6)   (LP only; 0 for the HP cell, where R6 is absent)
        beta  = (C1+C2)/(C1 C2 R3)         delta = 1/(C1 C2 R2 R3)
        eps   = 1/(C1 C2 R1 R3)            tau   = kappa C1 R3, kappa = R4/R5

    The monic cubic is, for BOTH absorptions and BOTH QE states:
        d2 = alpha + beta - tau (delta + eps)
        d1 = delta + eps + alpha beta - tau alpha delta - tau nu eps
        d0 = alpha delta + nu eps
    Eliminating gives a closed form with u = delta + eps:
        u     = (d1 - alpha d2 + alpha^2 + tau d0) / (1 + tau alpha)
        delta = (d0 - nu u)/lam ,  eps = u - delta
        beta  = d2 - alpha + tau u
    Setting tau = 0 recovers the non-QE cells; setting nu = 0 recovers the HP
    cell (whose d0 = alpha delta has no nu term). Verified against the exact
    symbolic polynomial coefficients for all four cells.
    """
    lam = alpha - nu
    if lam <= 0.0:
        return None
    u = (d1 - alpha*d2 + alpha*alpha + tau*d0) / (1.0 + tau*alpha)
    delta = (d0 - nu*u) / lam
    eps = u - delta
    if not (delta > 0.0 and eps > 0.0):
        return None
    beta = d2 - alpha + tau*u
    if beta <= 0.0:
        return None
    # gain: Ki = (1+kappa) * m * eps * C1 * R3, with m = nu (LP) or 1 (HP)
    m = nu if ab == "lp" else 1.0
    if m <= 0.0:
        return None
    x = tau*m*eps/Ki
    if not (0.0 <= x < 1.0):
        return None
    kappa = x/(1.0 - x)
    if (kappa > 0.0) != bool(qe):          # QE needs kappa>0; plain needs kappa==0
        return None
    R3 = 1.0
    C1 = Ki/((1.0 + kappa)*m*eps*R3)
    inv_C2R3 = beta - 1.0/(C1*R3)
    if inv_C2R3 <= 0.0:
        return None
    C2 = 1.0/(R3*inv_C2R3)
    R1 = 1.0/(eps*C1*C2*R3)
    R2 = 1.0/(delta*C1*C2*R3)
    C3v = 1.0/(lam*R1)
    out = {"C3": C3v, "C1": C1, "C2": C2, "R1": R1, "R2": R2, "R3": R3}
    if ab == "lp":
        out["R6"] = 1.0/(nu*C3v)
    if qe:
        out["R5"] = 1.0
        out["R4"] = kappa
    if not all(v > 0.0 and np.isfinite(v) for v in out.values()):
        return None
    return out


def analytic_seeds(topo, design):
    """Closed-form Phase-1 starts for the 3rd-order cells.

    Returns a list of {component_name: value} dicts with R3 normalised to 1 ohm
    (the caller rescales -- Phase-1 "ratio" mode is RC-scale invariant, so only
    the shape matters). These are EXACT roots of the residual system, not
    approximations: they reproduce the target poles and Ki to ~1e-16.

    Why they are needed: the feasible alpha window is
    p1 < alpha < p1 + w0/Q, whose RELATIVE width is w0/(Q p1). When the absorbed
    real pole sits well above the biquad's bandwidth that is a sliver -- ~4e-4
    in the f1/f0 = 10, Q = 15 corner -- and the product C3*R1 has to land inside
    it. A log-uniform multistart over the component box will not hit that by
    chance. The seeds put a start INSIDE the window at a ladder of positions, so
    Phase 1 converges where it otherwise would not.

    Returns [] for the 2nd-order cells: their own multistart already finds the
    root, and adding seeds there would perturb existing, validated results.
    """
    ab = topo.get("absorb")
    if not ab:
        return []
    qe = bool(topo.get("qe"))
    try:
        p1v = float(design["p1"]); w0v = float(design["w0"])
        Qv = float(design["Q"]);   Ki = abs(float(design.get("K", 1.0)))
    except (KeyError, TypeError, ValueError):
        return []
    if not (p1v > 0.0 and w0v > 0.0 and Qv > 0.5 and Ki > 0.0):
        return []
    d2 = p1v + w0v/Qv
    d1 = w0v*w0v + p1v*w0v/Qv
    d0 = p1v*w0v*w0v
    span = d2 - p1v

    # The alpha window's UPPER edge is set by the gain condition Ki*beta > eps,
    # not by the structural bound d2 -- and where it lands depends on Ki, which
    # varies over orders of magnitude between targets. So do not guess a ladder:
    # SCAN a fine log-spaced t = (alpha - p1)/(d2 - p1) in (0, 1), keep whatever
    # is feasible, then subsample it evenly. That adapts automatically, from the
    # wide windows of low-Q / low-p1 targets down to the ~4e-4 slivers.
    t_scan = np.logspace(-4.0, np.log10(0.999), 160)
    nu_grid = (0.15, 0.40, 0.65, 0.88) if ab == "lp" else (0.0,)
    # tau ladder in units of 1/w0 (tau has dimensions of time). tau = 0 is the
    # plain cell; the QE cells need tau > 0 to make kappa > 0.
    tau_grid = ((0.02, 0.08, 0.25, 0.8, 2.0) if qe else (0.0,))
    per_combo = max(2, 24 // (len(nu_grid)*len(tau_grid)))

    seeds = []
    for fr in nu_grid:
        for th in tau_grid:
            hits = []
            for t in t_scan:
                alpha = p1v + t*span
                sd = _design_point(ab, qe, d2, d1, d0, Ki,
                                   alpha, fr*alpha, th/w0v)
                if sd is not None:
                    hits.append(sd)
            if not hits:
                continue
            idx = np.unique(np.linspace(0, len(hits)-1, per_combo).astype(int))
            seeds.extend(hits[i] for i in idx)

    # Deduplicate on a coarse log signature (R3 == 1 already normalises every
    # design, so raw values are comparable), then cap the Phase-1 budget.
    seen, uniq = set(), []
    for sd in seeds:
        key = tuple(round(float(np.log10(sd[k])), 2) for k in sorted(sd))
        if key not in seen:
            seen.add(key)
            uniq.append(sd)
    return uniq[:24]


def var_list(topo):
    """Free search variables. The band-pass carries no derived-R5 constraint
    (its s^0 numerator coefficient is structurally zero), so there is no
    algebraically-derived component here. The plain cell frees C1,C2 and
    R1,R2,R3; the QE cell adds the positive-feedback divider R4,R5.

    3rd-order cells prepend the input network (see the header's symbol map --
    these print as C0/R0 on the schematic and in the BOM):
        absorb="hp" : C3 (in->x, series)
        absorb="lp" : C3 (x->gnd) + R6 (in->x, series)
    C3 is listed FIRST among the caps on purpose. It is the anchor
    unified_solver_v2.anchored_bounds pins at C_max whenever the absorbed real
    pole sits well below f0 (measured: C3 is the largest cap for f1 <~ 0.3 f0,
    C1 above that), and cell_layout derives the cap block order from here.
    """
    ab = topo.get("absorb")
    caps = ([C3] if ab else []) + [C1, C2]
    resis = ([R6] if ab == "lp" else []) + [R1, R2, R3]
    resis += [R4, R5] if topo.get("qe") else []
    return caps + resis


# =====================================================================
# Nodal-equation builder
# =====================================================================
def _bp_eqs(topo, Vmv, opamp_src=None):
    """KCL list for the MFB band-pass core.
       Vmv      : voltage at the (-) node used in the a/m equations
                  (0 or Vp ideal; the free Vm symbol non-ideal).
       opamp_src: None -> ideal (no explicit output eqn; V2 solved from the
                  m-node) ; expr -> non-ideal Thevenin source A_s*(V+ - V-).
    Netlist (user convention): C1 a-m | C2 a-out | R1 x-a | R2 a-gnd |
    R3 m-out | (QE) R4 p-gnd | R5 p-out.  (+) drives Vp (QE) or gnd (plain).
    Node x is the input-network junction: it IS the source (x == in == 1) on the
    2nd-order cells, and a real node carrying Vb on the 3rd-order cells:
        absorb="hp" : Vin -C3- x -R1- a            (x carries C3 and R1 only)
        absorb="lp" : Vin -R6- x , x -C3- gnd , x -R1- a

    The C3/R1 junction of the "hp" cell has only two elements, so it COULD be
    folded into a single series impedance R1 + 1/(s C3) and node x dropped. It
    is kept as a real node on purpose: every entry of the nodal matrix then
    stays a simple sum of admittances instead of a nested rational, which keeps
    the non-ideal `together`-only build clean and makes an extra shunt leg at x
    a one-line change if one is ever wanted.
    """
    ab = topo.get("absorb")
    qe = topo.get("qe", False)
    Vplus = Vp if qe else sp.Integer(0)
    eqs = []
    if ab == "hp":                     # node x : C3 in->x, R1 x->a
        eqs.append(s*C3*(1 - Vb) - (Vb - Va)/R1)
        x = Vb
    elif ab == "lp":                   # node x : R6 in->x, C3 x->gnd, R1 x->a
        eqs.append((1 - Vb)/R6 - s*C3*Vb - (Vb - Va)/R1)
        x = Vb
    else:                              # 2nd order: node x IS the driven source
        x = sp.Integer(1)
    # node a : input via R1 (from x), R2 a->gnd, C1 a->m (to Vmv), C2 a->out
    eqs.append((x - Va)/R1 - Va/R2 + s*C1*(Vmv - Va) + s*C2*(V2 - Va))
    # node m (op-amp -) : C1 a->m, R3 m->out.  No op-amp input current.
    eqs.append(s*C1*(Va - Vmv) + (V2 - Vmv)/R3)
    if qe:                                            # node p (op-amp +)
        eqs.append(-Vp/R4 + (V2 - Vp)/R5)            # R4 p->gnd, R5 p->out
    if opamp_src is not None:                         # node out (non-ideal)
        # The input network never touches `out`, so this equation is IDENTICAL
        # for all six cells.
        out = (V2 - opamp_src)/Ro + s*C2*(V2 - Va) + (V2 - Vmv)/R3
        if qe:
            out += (V2 - Vp)/R5
        eqs.append(out)
    return eqs, Vplus


def _unknowns(topo, nonideal):
    qe = topo.get("qe", False)
    u = [Vb] if topo.get("absorb") else []   # input-network junction (3rd order)
    u.append(Va)
    if qe:
        u.append(Vp)
    if nonideal:
        u.append(Vm)
    u.append(V2)
    return u


# =====================================================================
# IDEAL derivation  (virtual short: Vm -> Vp, or Vm -> 0 on the plain cell)
# =====================================================================
def build_ideal(topo, target_subs):
    Vmv = Vp if topo.get("qe") else sp.Integer(0)      # virtual short V- = V+
    eqs, _ = _bp_eqs(topo, Vmv, opamp_src=None)
    unk = _unknowns(topo, nonideal=False)

    A, b = sp.linear_eq_to_matrix(eqs, unk)
    T = A.LUsolve(b)[unk.index(V2)]
    num, den = sp.fraction(sp.cancel(sp.together(T)))
    num = sp.expand(num); den = sp.expand(den)
    num_poly = sp.Poly(num, s); den_poly = sp.Poly(den, s)
    a_lead = den_poly.LC(); b_lead = num_poly.LC()

    # NO R5 constraint / NO notch residual: the s^0 numerator coefficient is
    # structurally zero (single-s band-pass numerator), so nothing to pin.
    dcs = sp.Poly(den_poly.monic().as_expr(), s).all_coeffs()  # monic den coeffs
    if topo["order"] == 3:
        # Absorbed real pole: the biquad target gains the (s + p1) factor, the
        # same cubic the 3LP-MFB / 3HP-MFB cells match. THREE denominator
        # residuals instead of two -- the loop below is already order-generic.
        dt = sp.expand((s + p1)*(s**2 + (w0/Q)*s + w0**2))
    else:
        dt = sp.expand(s**2 + (w0/Q)*s + w0**2)
    tcs = sp.Poly(dt, s).all_coeffs()
    # Denominator residuals only: s^1 -> w0/Q (Q match), s^0 -> w0^2 (freq match);
    # 3rd order adds s^2 -> p1 + w0/Q. The numerator needs NO residual on ANY of
    # the six cells: it is a pure monomial (s, or s^2 on the absorb="hp" pair),
    # with every lower coefficient STRUCTURALLY zero -- not zeroed by the solver.
    res = [(dcs[i] - tcs[i]) / tcs[i] for i in range(1, len(dcs))]

    a1_expr = dcs[-2]
    a2_expr = dcs[-3] if len(dcs) >= 3 else dcs[-2]

    # Gain residual (BOTH cells): the leading-coefficient ratio Ki = b_lead/a_lead
    # is the rad/s numerator coefficient the pairing stage targets.  The cell is
    # INVERTING (Ki < 0), so the residual drives Ki -> SIGN*K = -K (K carries the
    # positive magnitude via cfg["K"]/dc_gain_to_K).  Contrast the non-inverting
    # VCVS band-pass, whose residual targets +K.
    res.append((b_lead / a_lead - SIGN*K) / K)

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
    wc = 2*sp.pi*GBWP_hz
    A_s = A_ol*wc / (wc + s*A_ol)

    # placeholder source; Vplus is returned so we can form A_s*(V+ - Vm)
    _, Vplus = _bp_eqs(topo, Vm, opamp_src=sp.Integer(0))
    src = A_s*(Vplus - Vm)
    eqs, _ = _bp_eqs(topo, Vm, opamp_src=src)
    unk = _unknowns(topo, nonideal=True)

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
