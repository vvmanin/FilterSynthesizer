# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cells_mfb_hp.py
#  Multiple-Feedback (Rauch / Friend) HIGH-pass cell family — the R<->C
#  swap of cells_mfb.py (the LP-MFB Rauch family), with origin zeros that
#  block DC. Registered under FAMILY = "HP-MFB"; the generic engine
#  (tf_derivation_v2) dispatches to it by topo["family"] exactly as it does
#  for the VCVS "HP" module and the LP-MFB module, and the per-section
#  topology radio (VCVS | MFB | ...) selects which family's cells are offered
#  for a given cascade section.
#
#  SIX cells (the "HP MFB family"):
#     2HP-MFB     3HP-MFB        all-pole Rauch MFB              (inverting)
#     2HP-MFB-QE  3HP-MFB-QE     all-pole + positive-FB Q-boost (inverting)
#     2HPn-MFB    3HPn-MFB       high-pass notch                (NON-inverting)
#
#  EIGHT cells (the "HP MFB family"):
#     2HP-MFB     3HP-MFB        all-pole Rauch MFB              (inverting)
#     2HP-MFB-QE  3HP-MFB-QE     all-pole + positive-FB Q-boost (inverting)
#     2HPn-MFB    3HPn-MFB       high-pass notch, ATTEN only    (NON-inverting)
#     2HPn-MFB2   3HPn-MFB2      high-pass notch, UNITY/GAINED  (NON-inverting)
#
#  The MFB2 notch (topo flag "v2") is a SECOND HP-notch realization with a
#  different netlist; it is the gain COMPLEMENT of the original HPn-MFB:
#     original HPn-MFB : H(inf) = R8/(R3+R8)               -> |gain| < 1 (atten)
#     MFB2     HPn-MFB2: H(inf) = [R4/(R3+R4)]*[(C3+C4)/C3] (divider * cap-ratio)
#  The cap ratio (C3+C4)/C3 > 1 LIFTS the +divider, so MFB2 reaches unity and
#  gained -- exactly the range the original cannot. It does NOT reach atten:
#  matching f0/Q/fz forces, for the 2nd order, the HF gain into a band
#     K  in  ( r^2/(r + 1/Q^2) ,  r ) ,   r = (f0/fz)^2 = w0^2/wz^2
#  pinned just below the freq-ratio-squared and NARROWING as Q rises (atten
#  K<1 needs Q < 1/sqrt(r(r-1)), i.e. very low Q). The 3rd order (extra C1/R1
#  HP input pole + R5 a->out feedback = 3 more DOF) widens this to cover unity
#  through well-gained, especially for larger f0/fz, but still never atten.
#  Net: original = atten, MFB2 = unity/gained, meeting at K~1 with NO sub-unity
#  gap (the original solves cleanly to K=0.99). Both share FAMILY="HP-MFB" and
#  are solved + ranked TOGETHER by sens_score; a target outside a cell's
#  reachable band simply yields no solution from that cell and drops out of the
#  BOM (same graceful-degradation path the original uses for |gain|>=1).
#
#  MFB2 reuses the HP-notch residual machinery VERBATIM (same denominator
#  match, same monic notch indexing -- numerator degree == order with a lone
#  origin zero in the 3rd-order cell -- same K=H(inf) gain convention, same
#  SIGN=+1); only the nodal netlist (_notch_eqs_v2) and var_list differ. R5
#  (a->out) is electrically inert in the 2nd-order ideal (node a is the driven
#  input source), so it is carried only for the 3rd order.
#
#  ORIGINAL SIX-cell description (all-pole + atten-notch) follows:
#
#  Naming follows the schematic-not-operating-point rule (same as LP-MFB):
#  gain is a free component ratio on a fixed circuit, so it earns NO suffix;
#  QE adds the (R4,R5) positive-feedback divider, so it does. QE and non-QE
#  all-pole are solved TOGETHER in one BOM list ranked by sens_score (the
#  engine does this automatically when both names are in the topology list).
#
#  "gain" for HP is the HIGH-FREQUENCY gain (the passband sits above the
#  cutoff; the origin zeros block DC), so it is the leading-coefficient ratio
#  H(inf) = b_lead/a_lead -- NOT H(0) (which is 0 for every HP cell).
#
#  SIGN CONVENTION (cascade tracks per-section sign, as for VCVS / LP-MFB):
#     all-pole MFB : H(inf) = -C2/C4 (2nd) < 0    -> SIGN = -1  (inverting)
#     HPn      MFB : H(inf) = R8/(R3+R8) > 0      -> SIGN = +1  (non-inverting)
#  The QE all-pole is ALSO inverting: H(inf)_QE = C2(R4+R5)/(C2 R4 - C4 R5),
#  and denominator stability (w0^2 > 0) forces (C2 R4 - C4 R5) < 0, so
#  H(inf)_QE < 0 always. Both all-pole variants therefore hard-code SIGN = -1.
#
#  STRUCTURAL HF-GAIN CONSTRAINT (HPn only): H(inf) = R8/(R3+R8) is a voltage
#  divider of two positive resistors, so the high-pass-notch HF passband gain
#  is ALWAYS < 1. Targets with |HF gain| >= 1 are unreachable on this netlist
#  (the gain residual cannot be driven to zero) and degrade gracefully to an
#  empty BOM -- the UI carries a note. Sub-unity targets solve to machine
#  precision (validated: K=0.7 -> residual ~1e-31).
#
#  NOTCH CONSTRAINT: R3 (the a->V+ feed-forward resistor) and the symmetric
#  on-axis zero are enforced as RESIDUALS, with R5 (a real, independent a->m
#  element) kept as a FREE search variable -- the engine's algebraic-constraint
#  slot is hard-wired to the symbol R5, which here is NOT a derivable feedback
#  element, so (exactly as LP-MFB keeps R3 free) the notch is enforced via two
#  numerator residuals instead of via R5_constraint. No engine change required.
#  Because the HPn numerator degree EQUALS the order (the 3rd-order cell has a
#  lone origin zero -> trailing monic coeff 0), the residual indexing follows
#  the VCVS-HP convention (cells_hp.py): symmetric-zero coeff at monic[-2]
#  (2nd) / monic[-3] (3rd), zero-frequency coeff at monic[-1] (2nd) /
#  monic[-2] (3rd). This is UNLIKE LP-MFB's notch (always numerator degree 2).
#
#  Op-amp port mapping:  (+) = Vp,  (-) = Vm,  output = V2 behind Ro.
#  Internal nodes:  a = Va (3rd-order input pole),  b = Vb.
#  Non-ideal TF kept as a RAW rational function (no expand), GBW one-pole
#  model identical to the VCVS / LP-MFB modules.
#
#  Op-amp node netlists (user convention)
#  --------------------------------------
#  ALL-POLE (a=in when 2nd order: C1 shorted, R1 open):
#     C1 in-a(3rd) | C2 a-b | C3 b-m | C4 b-out
#     R1 a-gnd(3rd) | R2 b-gnd | R3 m-out
#     (+)=gnd (basic)  or  =p with R4 p-gnd, R5 p-out (QE)
#  HP-NOTCH (a=in when 2nd order):
#     C1 in-a(3rd) | C2 b-m | C3 b-out
#     R1 a-gnd(3rd) | R2 a-b | R3 a-p | R4 b-gnd
#     R5 a-m | R6 m-out | R7 p-out | R8 p-gnd
#     (+)=p, (-)=m
# =====================================================================

import sympy as sp

from tf_symbols import (s, Va, Vb, Vp, Vm, V2,
                        R1, R2, R3, R4, R5, R6, R7, R8, Ro,
                        C1, C2, C3, C4, A_ol, GBWP_hz,
                        p1, w0, wz, Q, K)

FAMILY = "HP-MFB"

SIGN_ALLPOLE = -1      # H(inf) = -C2/C4 (2nd) ; QE forced negative too
SIGN_NOTCH   = +1      # H(inf) = R8/(R3+R8) > 0

# ---------------------------------------------------------------------
#  HF-gain routing margin between the two HP-notch realizations (3rd order).
#
#  The 3rd-order pair TILES the gain axis with a PUNCTURE at exactly 1:
#      3HPn-MFB  : H(inf) = R8/(R3+R8)                -> K in (0, 1)
#      3HPn-MFB2 : H(inf) = [R4/(R3+R4)]*(C3+C4)/C3   -> K in (1, r), r=(f0/fz)^2
#  Neither ATTAINS K=1, and the base cell degenerates long before it: matching K
#  forces R8/R3 = K/(1-K), so the minimum resistor spread and the residual
#  Jacobian's condition number both scale as 1/(1-K) (9 / 179 at K=0.95;
#  99 / 8.3e3 at 0.99; 999 / 2.4e4 at 0.999). That divider corner is exactly
#  where a real op-amp's loop gain collapses: the pre-distortion then moves
#  resistors >10x and walks the transmission zero off axis (measured: -38 dB at
#  337 Hz for a 363.7 Hz target). MFB2, whose divider stays moderate there,
#  delivers -63 dB at the same target.
#
#  So above the margin the two cells are ROUTED, not ranked. The 2nd-order MFB2
#  band is the narrow Q-dependent sliver ( r^2/(r+1/Q^2), r ) that never reaches
#  unity, so the 2nd-order pair keeps the solve-both-and-rank behaviour.
#
#  Why 0.99 and not lower: MFB2's envelope-limited gain floor is ~1.006 (as
#  K->1+ it needs BOTH D->1 and C4/C3->0, and C_min/C_max pins the cap ratio).
#  A target t below the margin routed to MFB2 would overshoot by (1.006-t)/t,
#  which stays inside the app's +-2% GAIN_UNITY_TOL only for t >= 0.986. 0.99
#  sits just above that floor -- it is the largest safe margin.
# ---------------------------------------------------------------------
HF_GAIN_MARGIN = 0.99

# ---------------------------------------------------------------------
#  Structural gain FLOOR of the 3rd-order MFB2 notch.
#
#  Its band is (1, r) -- OPEN at 1. As K -> 1+ the cell needs BOTH D =
#  R4/(R3+R4) -> 1 and C4/C3 -> 0, so the divider R4/R3 = D/(1-D) diverges: at
#  K = 1 exactly NO component set exists. Phase-1 gates on the AGGREGATE squared
#  residual (1e-5 ratio / 1e-6 anchored), and the gain residual there has a
#  strictly positive infimum, so a unity target converges NOWHERE -- 0/420 starts
#  -- and the section returns an empty BOM (which the no-realization probe then
#  reports, badly, as "nearest gain 1.41"). The solver-side ratio_bounds(wide)
#  widening restores enough resistor spread to REPRESENT the corner, but cannot
#  make an unreachable K reachable -- only clamping does.
#
#  So a target at or below the floor is solved AT the floor. Measured best
#  |gain residual| for R in [300 ohm, 5 MOhm], C in [68 pF, 100 nF]:
#      K            1.005    1.010    1.015    1.020
#      |gain res|   5.0e-3   8.0e-4   4.9e-15  0.0
#  1.01 is the first value the cell closes. Every target admitted by
#  HF_GAIN_MARGIN (> 0.99) is therefore realized to within +2.0%, i.e. inside the
#  +-2% GAIN_UNITY_TOL band, and the achieved H(inf) is reported in the BOM.
#  A tighter R envelope raises the true floor; the cell then simply returns no
#  solution and the probe explains it.
#
#  ENVELOPE DEPENDENCE. How close to 1 the cell can actually reach is set by the
#  two spread ceilings the user's envelope allows -- sigma_R = R_max/R_min and
#  sigma_C = C_max/C_min. Both act and they COMPOUND (quadrature). Floor measured
#  on the FULL pipeline (120 ratio + 240 anchored starts), tight vs wide:
#      sigma_R=6667,  sigma_C=147   -> floor ~1.020 (24 sols at 1.020, 0 at 1.018)
#      sigma_R=16667, sigma_C=1470  -> floor ~1.001
#  Combined:  floor ~= 1 + sqrt[ (aR/sqrt(sigma_R))^2 + (aC/sqrt(sigma_C))^2 ].
#  A FIXED 1.01 only suits a generous envelope: with C_max=0.01uF, R_max=2 the
#  true floor is ~1.020, so clamping to 1.01 still lands in the dead zone and the
#  section returns an empty BOM. mfb2_v2_gain_floor(cfg) computes the envelope
#  value; constants (aR=1.20, aC=0.20) sit just above the fitted (1.155, 0.172)
#  so the clamp is reliably >= the feasibility floor without wasteful over-clamp.
#  Verified end-to-end: tight envelope (0 solutions at fixed 1.01) floors at
#  ~1.022 -> solutions; wide floors at ~1.011; both realized within GAIN_UNITY_TOL.
# ---------------------------------------------------------------------
V2_GAIN_FLOOR = 1.01               # legacy default (callers without an envelope)
_V2_FLOOR_AR = 1.20                # resistor-spread coefficient (conservative)
_V2_FLOOR_AC = 0.20                # cap-spread coefficient (conservative)
_V2_FLOOR_CAP = 1.10               # safety rail: never clamp higher than this


def mfb2_v2_gain_floor(cfg):
    """Envelope-aware gain floor for the 3rd-order MFB2 HP-notch.

    floor = 1 + sqrt[ (aR/sqrt(R_max/R_min))^2 + (aC/sqrt(C_max/C_min))^2 ],
    capped at _V2_FLOOR_CAP; falls back to the fixed V2_GAIN_FLOOR when the
    envelope is missing. See the block comment above for the calibration."""
    import math
    try:
        sR = float(cfg["R_max"]) / float(cfg["R_min"])
        sC = float(cfg["C_max"]) / float(cfg["C_min"])
        if sR <= 1.0 or sC <= 1.0:
            return V2_GAIN_FLOOR
        fl = 1.0 + math.sqrt((_V2_FLOOR_AR / math.sqrt(sR)) ** 2
                             + (_V2_FLOOR_AC / math.sqrt(sC)) ** 2)
        return min(fl, _V2_FLOOR_CAP)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return V2_GAIN_FLOOR


def notch_cells_for_gain(order, hf_gain):
    """HP-notch MFB cell name(s) to solve for a target HF passband gain."""
    if order != 3:                      # 2nd order: no unity crossing to route
        return ["2HPn-MFB", "2HPn-MFB2"]
    return (["3HPn-MFB2"] if abs(float(hf_gain)) > HF_GAIN_MARGIN
            else ["3HPn-MFB"])


# ---------------------------------------------------------------------
#  Reachable HF-GAIN band of the 2nd-order MFB2 HP-notch pair (the "Gained
#  MFB" mode). Both edges follow from matching f0/Q/fz on the ideal netlist:
#
#    r = (f0/fz)^2 = w0^2/wz^2 > 1
#    2HPn-MFB2      K in ( Q^2 r^2/(Q^2 r + 1) , r )      [derived, closed form]
#    2HPn-MFB2+R7   the p->out positive feedback un-pins the lower edge; the
#                   ceiling stays at r. There is no clean closed form (R7
#                   couples r itself), so the floor is an EMPIRICAL constant of
#                   the base floor -- measured ~0.94 * base_floor across specs
#                   (reference spec: base 2.557 -> +R7 ~2.40). It is used only to
#                   WIDEN the reported/searched window, never to gate a solve
#                   (the solver finds whatever the envelope allows), so a mild
#                   under-estimate is safe.
#
#  Ceiling K = r is shared. The geometric-mean auto-gain uses the BASE cell's
#  band (floor_base, r): a value inside it is reachable by BOTH cells, so the
#  sens_score ranking has two BOMs to compare. A value in (floor_R7, floor_base)
#  is legal but only +R7 returns a BOM there.
# ---------------------------------------------------------------------
V2_R7_FLOOR_FACTOR = 0.94      # empirical: +R7 floor / base floor (2nd order)


def mfb2_gain_band(f0_hz, Q, fz_hz):
    """(r, floor_base, floor_r7, ceil) for the 2nd-order MFB2 HP-notch pair.

    floor_base = Q^2 r^2/(Q^2 r + 1)   (2HPn-MFB2, derived)
    floor_r7   = V2_R7_FLOOR_FACTOR * floor_base   (2HPn-MFB2+R7, empirical)
    ceil       = r = (f0/fz)^2         (shared)
    """
    f0 = float(f0_hz) or 1.0
    fz = float(fz_hz) or 1.0
    Qv = float(Q)
    r = (f0 / fz) ** 2                       # HP: r = w0^2/wz^2 > 1 (ceiling)
    floor_base = Qv * Qv * r * r / (1.0 + Qv * Qv * r)
    floor_r7 = V2_R7_FLOOR_FACTOR * floor_base
    return r, floor_base, floor_r7, r


def mfb2_geomean_gain(f0_hz, Q, fz_hz):
    """Geometric-mean HF gain of the BASE 2nd-order MFB2 band (floor_base, r).
    This is the value the "Gained MFB" checkbox drops into the Custom HF-gain
    box: mid-band, best-conditioned, and reachable by both twins."""
    import math
    r, floor_base, _f7, ceil = mfb2_gain_band(f0_hz, Q, fz_hz)
    return math.sqrt(max(floor_base, 1e-6) * ceil)

# =====================================================================
# Topology descriptor helpers
# =====================================================================
def topo_name(topo):
    o, notch, qe = topo["order"], topo["notch"], topo["qe"]
    if notch:
        if topo.get("v2"):
            return f"{o}HPn-MFB2{'+R7' if topo.get('r7') else ''}"
        return f"{o}HPn-MFB"
    return f"{o}HP-MFB{'-QE' if qe else ''}"


def all_cells():
    """The HP-MFB cells. all-pole {basic, QE} x {2nd, 3rd}, the HP-notch
    {2nd, 3rd}, plus the MFB2 HP-notch {2nd, 3rd}. QE is meaningless for the
    notch (its positive feedback is built into the topology), so notch cells
    carry qe=False. The MFB2 notch is a SECOND HP-notch realization (different
    netlist) whose HF gain = [R4/(R3+R4)]*[(C3+C4)/C3] is NOT divider-limited,
    so it reaches atten/unity/gained -- unlike the original HPn-MFB, whose
    H(inf)=R8/(R3+R8) is < 1 (atten only). Both notch cells share the same
    family and are solved + ranked together by sens_score."""
    out = []
    for order in (3, 2):
        for qe in (False, True):
            out.append({"family": FAMILY, "order": order,
                        "notch": False, "qe": qe})
        out.append({"family": FAMILY, "order": order,
                    "notch": True, "qe": False})
        out.append({"family": FAMILY, "order": order,
                    "notch": True, "qe": False, "v2": True})
        # MFB2 +R7 twin: p->out positive feedback widens the reachable HF-gain
        # band DOWNWARD (measured floor 2.56 -> ~2.4 on the reference 2nd-order
        # spec) at the cost of S_Q. 2nd order only -- the 3rd-order MFB2 already
        # spans (1, r) via its C1/R1 input pole + R5 a->out leg, so R7 there is
        # redundant. Solved together with 2HPn-MFB2 and ranked by sens_score;
        # R7 -> inf degenerates it back to the plain cell, so the twin can only
        # enlarge the pool.
        if order == 2:
            out.append({"family": FAMILY, "order": order,
                        "notch": True, "qe": False, "v2": True, "r7": True})
    return out


def _sign(topo):
    return SIGN_NOTCH if topo["notch"] else SIGN_ALLPOLE


def dc_gain_to_K(topo, design_subs, dc_gain):
    """Desired passband (HIGH-FREQUENCY) gain -> leading-coefficient K.

    For HP the passband gain IS the leading-coefficient ratio b_lead/a_lead
    = H(inf) (the all-pole numerator is K*s^order over a degree-order
    denominator; the notch numerator is K*(s^2+wz^2)*s^(order-2)), so
    K = sign * |desired HF gain| directly -- no (rad/s)^n rescale (that LP
    factor came from matching H(0), the wrong end of an HP response). Gain is
    a free component ratio on every MFB cell, so K is always returned.

    K carries the cell's inherent sign; |dc_gain| is the requested magnitude.
    (HPn note: |H(inf)| = R8/(R3+R8) < 1 structurally, so |dc_gain| >= 1 is
    unreachable and yields an empty BOM -- see module header.)

    The 3rd-order MFB2 notch has the mirror-image constraint |H(inf)| > 1, open
    at 1, so a unity-or-below target must be lifted to the cell's gain FLOOR or
    phase-1 finds no valley and the section loses its whole BOM. The real floor
    is ENVELOPE-dependent (mfb2_v2_gain_floor), so run_synthesis pre-lifts the
    target there -- where cfg is in scope -- BEFORE calling this function. The
    fixed V2_GAIN_FLOOR clamp below is only a fallback for callers that reach
    dc_gain_to_K WITHOUT having applied the envelope floor (e.g. a direct
    nonideal_solver path); when run_synthesis already lifted the target, this
    max() is a no-op. The 2nd-order MFB2 pair is NOT clamped -- its band
    (Q^2 r^2/(1+Q^2 r), r) sits well above unity and is driven by the "Gained
    MFB" geomean, not by a near-unity target.
    """
    g = abs(float(dc_gain))
    if topo["notch"] and topo.get("v2") and topo["order"] == 3:
        g = max(g, V2_GAIN_FLOOR)
    return _sign(topo) * g


def var_list(topo):
    """Free search variables. R5 is FREE for the notch cells (it is a real,
    independent a->m element absorbed by the symmetric-zero residual); there
    is no algebraically-derived component here. NOTE the all-pole HP cell
    carries C4 (b->out) -- unlike LP-MFB, which has no C4.

    MFB2 HP-notch carries a DIFFERENT element set: three caps on node b
    (C2 b->m, C3 b->out, C4 b->gnd) and core resistors R2 (a->b), R3 (a->p),
    R4 (p->gnd), R6 (m->out). The HP input pole (C1 in->a, R1 a->gnd) and the
    a->out feedback R5 exist only for the 3rd order; R5 is electrically inert
    in the 2nd-order ideal (node a is the driven input), so it is omitted there
    -- analogous to how C1/R1 are absent for the 2nd order."""
    o, notch, qe = topo["order"], topo["notch"], topo["qe"]
    if notch and topo.get("v2"):                          # MFB2 HP-notch
        caps = ([C1] if o == 3 else []) + [C2, C3, C4]
        res = ([R1, R5] if o == 3 else []) + [R2, R3, R4, R6]
        if topo.get("r7"):                                # +R7 twin (2nd order)
            res += [R7]                                   # p->out positive feedback
    elif notch:
        caps = ([C1] if o == 3 else []) + [C2, C3]
        res = ([R1] if o == 3 else []) + [R2, R3, R4, R5, R6, R7, R8]
    else:
        caps = ([C1] if o == 3 else []) + [C2, C3, C4]
        res = ([R1] if o == 3 else []) + [R2, R3]
        if qe:
            res += [R4, R5]                      # positive-feedback +divider
    return caps + res


# =====================================================================
# Nodal-equation builders
# =====================================================================
def _allpole_eqs(topo, Vmv, vin_one, opamp_src=None):
    """KCL list for the all-pole MFB HP core.
       Vmv      : voltage at the (-) node used in the b/m equations
                  (0 or Vp ideal; the free Vm symbol non-ideal).
       vin_one  : True -> 2nd order (a == in == 1, no node-a eqn, no R1/C1).
       opamp_src: None -> ideal (no explicit output eqn; V2 solved from m-node)
                  expr -> non-ideal Thevenin source A_s*(V+ - V-) for eq_out.
    """
    qe = topo["qe"]
    Vplus = Vp if qe else sp.Integer(0)
    eqs = []
    if not vin_one:                                  # 3rd-order input pole
        eqs.append(s*C1*(Va - 1) + s*C2*(Va - Vb) + Va/R1)            # node a
        a = Va
    else:
        a = sp.Integer(1)
    eqs.append(s*C2*(Vb - a) + s*C3*(Vb - Vmv) + s*C4*(Vb - V2) + Vb/R2)  # node b
    eqs.append(s*C3*(Vmv - Vb) + (Vmv - V2)/R3)                       # node m
    if qe:
        eqs.append((Vp - V2)/R5 + Vp/R4)                             # node p (+)
    if opamp_src is not None:                                        # node out
        out = (V2 - opamp_src)/Ro + s*C4*(V2 - Vb) + (V2 - Vm)/R3
        if qe:
            out += (V2 - Vp)/R5
        eqs.append(out)
    return eqs, Vplus


def _notch_eqs(topo, Vmv, vin_one, opamp_src=None):
    """KCL list for the HP-notch MFB core. (+) = Vp, (-) via Vmv."""
    eqs = []
    if not vin_one:
        eqs.append(s*C1*(Va - 1) + Va/R1 + (Va - Vb)/R2
                   + (Va - Vp)/R3 + (Va - Vmv)/R5)                   # node a
        a = Va
    else:
        a = sp.Integer(1)
    eqs.append((Vb - a)/R2 + Vb/R4 + s*C2*(Vb - Vmv) + s*C3*(Vb - V2))  # node b
    eqs.append((Vp - a)/R3 + (Vp - V2)/R7 + Vp/R8)                   # node p (+)
    eqs.append((Vmv - a)/R5 + (Vmv - V2)/R6 + s*C2*(Vmv - Vb))       # node m
    if opamp_src is not None:                                       # node out
        eqs.append((V2 - opamp_src)/Ro + (V2 - Vm)/R6
                   + s*C3*(V2 - Vb) + (V2 - Vp)/R7)
    return eqs, Vp


def _notch_eqs_v2(topo, Vmv, vin_one, opamp_src=None):
    """KCL list for the MFB2 HP-notch core (covers atten/unity/gained HF gain).
    (+) = Vp, (-) via Vmv.  Netlist (user convention):
        C1 in-a(3rd) | C2 b-m | C3 b-out | C4 b-gnd
        R1 a-gnd(3rd) | R2 a-b | R3 a-p | R4 p-gnd | R5 a-out(3rd) | R6 m-out
    HF gain = H(inf) = [R4/(R3+R4)]*[(C3+C4)/C3] -- the cap ratio lifts the
    p-divider, so unity/gained are reachable (the original HPn-MFB cannot).
    R5 (a->out) is inert in the 2nd-order ideal (a is the input source), so its
    a->out leg is dropped for the 2nd order in both the node-a and node-out KCL.
    """
    eqs = []
    if not vin_one:                                   # 3rd-order HP input pole
        eqs.append(s*C1*(Va - 1) + Va/R1 + (Va - Vb)/R2
                   + (Va - Vp)/R3 + (Va - V2)/R5)     # node a
        a = Va
    else:
        a = sp.Integer(1)
    r7 = topo.get("r7")                               # +R7: p->out positive FB
    eqs.append((Vb - a)/R2 + s*C2*(Vb - Vmv) + s*C3*(Vb - V2) + s*C4*Vb)  # node b
    p_eq = (Vp - a)/R3 + Vp/R4                                            # node p (+)
    if r7:
        p_eq += (Vp - V2)/R7                          # extra p->out leg (2nd order)
    eqs.append(p_eq)
    eqs.append(s*C2*(Vmv - Vb) + (Vmv - V2)/R6)                           # node m
    if opamp_src is not None:                                            # node out
        out = (V2 - opamp_src)/Ro + (V2 - Vm)/R6 + s*C3*(V2 - Vb)
        if not vin_one:                              # a->out feedback (3rd order)
            out += (V2 - Va)/R5
        if r7:
            out += (V2 - Vp)/R7                       # R7 also loads the output node
        eqs.append(out)
    return eqs, Vp


def _unknowns(topo, nonideal):
    o, notch, qe = topo["order"], topo["notch"], topo["qe"]
    u = [Vb]
    if notch or qe:
        u.append(Vp)
    if nonideal:
        u.append(Vm)
    u.append(V2)
    if o == 3:
        u = [Va] + u
    return u


# =====================================================================
# Conductance-cleared transfer-function solver  (identical strategy to the
# LP-MFB module: substitute R -> 1/G so every matrix entry is POLYNOMIAL,
# take Cramer determinants -- no spurious high-degree s-factors that a
# multivariate cancel would then have to grind away -- then G -> 1/R back.)
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
    vin_one = (o == 2)
    Vmv = Vp if (notch or topo["qe"]) else sp.Integer(0)   # virtual short V- = V+
    if notch:
        builder = _notch_eqs_v2 if topo.get("v2") else _notch_eqs
    else:
        builder = _allpole_eqs
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

    # notch residuals. HPn numerator degree == order (lone origin zero in the
    # 3rd-order cell), so use the VCVS-HP indexing (NOT LP-MFB's fixed deg-2):
    #   symmetric on-axis coeff  -> 0   : monic[-2] (2nd) / monic[-3] (3rd)
    #   zero-frequency coeff     -> wz^2: monic[-1] (2nd) / monic[-2] (3rd)
    if notch:
        ncs = num_poly.monic().all_coeffs()
        sym = ncs[-2] if o == 2 else ncs[-3]
        zfr = ncs[-1] if o == 2 else ncs[-2]
        res.append(sym / wz)                        # on-axis (drive to 0)
        res.append((zfr - wz**2) / wz**2)           # zero frequency

    # gain residual (always: gain is a free component ratio on every MFB cell).
    # For HP this is the HF gain = leading-coeff ratio = H(inf).
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
    vin_one = (o == 2)
    wc = 2*sp.pi*GBWP_hz
    A_s = A_ol*wc / (wc + s*A_ol)
    if notch:
        builder = _notch_eqs_v2 if topo.get("v2") else _notch_eqs
    else:
        builder = _allpole_eqs

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
