# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  filter_synthesis.py
#  Unified driver for the 3rd-order LP-notch section.
#
#  ONE entry point, op-amp mode selected by a single parameter:
#     opamp = None        -> IDEAL   (infinite-gain op-amp, no Ro)
#     opamp = {A_ol,...}   -> NON-IDEAL (finite gain, GBWP pole, Ro)
#
#  Pipeline (both modes share stages 1-2-4; stage 3 is mode-dependent):
#     1. Ideal synthesis            (unified_solver.run_unified_synthesis)
#     2. [non-ideal only] resistor pre-distortion
#                                   (nonideal_solver.solve_nonideal)
#     3. Discrete hardware snap      (discrete_snapper.snap_to_hardware)
#     4. Two tables: continuous + snapped
#
#  Reuses the existing validated modules unchanged. No algorithm is
#  modified -- this only routes data and selects the op-amp model that
#  the snapper's exact-physics evaluator uses.
# =====================================================================

import numpy as np

from tf_derivation_v2 import get_cases, p1, w0, wz, Q, K
from unified_solver_v2 import run_synthesis
from nonideal_solver import solve_nonideal
from discrete_snapper import snap_to_hardware, print_snapped_table


# --- S4: bound the refine budget for the ONE cell kind that is genuinely
# expensive per candidate -------------------------------------------------
# The 3rd-order AM notch cells (3LPn-AM / 3HPn-AM and their C1-split twins) carry
# a ~766k-op non-ideal transfer function; their non-ideal correction + snap cost
# is orders of magnitude above any other cell's. The (frontier + rest)[:top_k]
# prune below already scales cost with candidate count, so for a section built
# from these cells we additionally clamp the budget. This is GATED on the realized
# candidate topologies — it can only ever fire for a 3rd-order AM notch section, so
# no other family/topology is affected in any way (the precaution). It only ever
# LOWERS the budget, never raises it.
_HEAVY_NI_TOPK_CAP = 12
_HEAVY_NI_FAMILIES = {"LP-AM", "HP-AM"}       # AM families that contain 3rd-order notch cells
_HEAVY_NI_NOTCH_KINDS = {"LPn", "HPn", "N"}


def _has_heavy_ni_cells(solutions):
    """True iff any realized candidate is a 3rd-order AM NOTCH cell (the only
    ~766k-op non-ideal TFs). Purely an identity test on the cell's topo dict."""
    import tf_derivation_v2 as _TF
    n2t = {_TF.topo_name(t): t for t in _TF.all_cells()}
    for s in solutions:
        t = n2t.get(s.get("topology"))
        if (t is not None
                and t.get("family") in _HEAVY_NI_FAMILIES
                and int(t.get("order", 2)) == 3
                and t.get("kind") in _HEAVY_NI_NOTCH_KINDS):
            return True
    return False


def _r5_is_feedback(topo):
    """True when a cell's R5 is a genuine FEEDBACK resistor, i.e. when trading
    it against sensitivity is a real design choice worth protecting from the
    top_k truncation below.

    False for the three Q-ENHANCEMENT cells, where R5 is one half of an
    isolated, capacitor-free op-amp (+) divider:

        LP-MFB + qe (not notch)   divider {R5, R6}
        HP-MFB + qe (not notch)   divider {R4, R5}
        BP-MFB + qe               divider {R4, R5}

    These are exactly the branches unified_solver_v2.rescale_isolated_r5r6
    pins, and for the same reason: only the divider RATIO enters the response,
    so the pair's common scale is a redundant degree of freedom. R5's absolute
    value there is an artifact of the pinning rule, not a design choice, and
    ordering on it is meaningless.

    Excluding them makes the top_k prune order these cells purely by
    sens_score. That is the behaviour we want, because sensitivity on a QE cell
    is monotone in the Q-enhancement factor E = Q/Q_passive (every log
    derivative of the damping coefficient picks up the factor beta/(beta-Delta)
    = E), while the resistor-spread law relaxes as 4(Q/E)^2. So "lowest
    sens_score among the solutions that passed the spread guard" IS "the
    smallest positive-feedback ratio that fits the allowed R spread" -- the
    divider ends up set for the best achievable sensitivity rather than left
    wherever its multistart basin happened to land.

    Deliberately NOT widened past these three:
      * VCVS NOTCH -- {R4,R5,R6} is a free-scale trio, but that family is never
        pooled with an R5-less twin, so the frontier never mis-orders it, and
        it is the family the frontier was written for.
      * NOTCH-MFB -- its free-scale divider is {R1,R4}; R5 is the real m->gnd
        gain-set leg.
      * AM -- its free DOF is the matched inverter pair R7 = R8; R5 is a real
        composite-integrator resistor.
      * LP-MFB LS -- its free-scale group is {R2,R6,R7}; R5 is untouched.
      * The plain LP/HP (VCVS) notch cells -- R5 is the derived notch feedback
        resistor, exactly the low-R5/HF-hump trade the frontier exists for.
    """
    fam = topo.get("family")
    qe = bool(topo.get("qe"))
    notch = bool(topo.get("notch"))
    if fam == "BP-MFB" and qe:
        return False
    if fam in ("LP-MFB", "HP-MFB") and qe and not notch:
        return False
    return True


def _frontier_eligible(solutions):
    """Map id(solution) -> bool: may this solution enter the R5 frontier?"""
    import tf_derivation_v2 as _TF
    n2t = {_TF.topo_name(t): t for t in _TF.all_cells()}
    out = {}
    for s in solutions:
        t = n2t.get(s.get("topology"))
        out[id(s)] = True if t is None else _r5_is_feedback(t)
    return out


# Sentinel op-amp for IDEAL mode: infinite gain, infinite GBWP, zero Ro.
# The non-ideal TF collapses onto the ideal TF for these values (verified
# to 1e-10 by tf_derivation's ideal-limit acceptance test), so the SAME
# snapper/evaluator path serves both modes -- no separate ideal code.
IDEAL_OPAMP = dict(A_ol=1e12, GBWP_hz=1e15, Ro=1e-12)


def _design_subs(cfg):
    return {p1: 2*np.pi*cfg["f1"], w0: 2*np.pi*cfg["f0"],
            wz: 2*np.pi*cfg["fz"], Q: cfg["Q"], K: cfg.get("K", 1.0)}


def synthesize(cfg, opamp=None, topologies=None, n_cores=None,
               ratio_starts=60, anchored_starts=120,
               max_valleys=12, hints_per_combo=3,
               pole_tol=0.01, gain_tol=0.01, max_c2_cands=4,
               top_k=None,
               dc_gain=None, cases=None, verbose=True):
    """Run the full synthesis pipeline.

    Parameters
    ----------
    cfg     : design + hardware config dict. Must contain
              f1,f0,fz,Q, C_min,C_max,R_min,R_max,MAX_R_RATIO, C_series,
              R_series (+ reg_weight for non-ideal mode). Gain is set EITHER
              by cfg["K"] (leading-coeff, per-topology (rad/s)^n units) OR by
              dc_gain (linear DC gain, converted to per-topology K).
    opamp   : None -> IDEAL mode; dict {A_ol,GBWP_hz,Ro(MOhm)} -> NON-IDEAL.
    dc_gain : None -> use cfg["K"]; else linear DC gain (e.g. 2.0 for +6 dB),
              converted to per-topology K automatically. Unity cells ignore K.
    cases   : optional pre-fetched cases (saves a lookup).
    """
    mode = "ideal" if opamp is None else "nonideal"
    eval_opamp = IDEAL_OPAMP if opamp is None else opamp

    if verbose:
        if mode == "ideal":
            print("=== SYNTHESIS MODE: IDEAL op-amp ===")
        else:
            print(f"=== SYNTHESIS MODE: NON-IDEAL op-amp "
                  f"(A_ol={eval_opamp['A_ol']:.0e}, "
                  f"GBWP={eval_opamp['GBWP_hz']:.2e} Hz, "
                  f"Ro={eval_opamp['Ro']*1e6:.0f} ohm) ===")

    # --- Stage 1: ideal continuous synthesis (identical in both modes) ---
    ideal_continuous = run_synthesis(
        cfg, topologies=topologies, n_cores=n_cores,
        ratio_starts=ratio_starts, anchored_starts=anchored_starts,
        max_valleys=max_valleys, hints_per_combo=hints_per_combo,
        pole_tol=pole_tol, gain_tol=gain_tol, max_c2_cands=max_c2_cands,
        dc_gain=dc_gain, verbose=verbose)

    if not ideal_continuous:
        return {"mode": mode, "ideal_continuous": [], "continuous": [],
                "snapped": [], "cases": cases or {}}


    # Keep only top_k ideal candidates before the (expensive) non-ideal
    # correction + snap, which both scale with candidate count. We keep the
    # (feedback-resistance R5, sensitivity) trade-off FRONTIER first -- so the
    # low-R5 / higher-sensitivity options the user may want for a smaller HF
    # hump survive truncation -- then fill the remaining slots by sensitivity.
    # S4: for a 3rd-order AM notch section, additionally clamp the refine budget
    # (gated on the realized candidates, so this is inert for every other family).
    eff_top_k = top_k
    if _has_heavy_ni_cells(ideal_continuous):
        cap = _HEAVY_NI_TOPK_CAP
        eff_top_k = cap if top_k is None else min(top_k, cap)
        if verbose and (top_k is None or top_k > cap):
            print(f"  [heavy-NI] 3rd-order AM notch section: refine budget "
                  f"top_k {top_k} -> {eff_top_k} (bounds the costly non-ideal + snap)")

    if eff_top_k is not None and len(ideal_continuous) > eff_top_k:
        def _sens(s): return s.get("sens_score", s.get("score", float("inf")))
        def _r5(s):   return s.get("R5") or 0.0
        # A solution with NO R5 scores 0.0 here and so can never satisfy the
        # `0.0 < _r5(s)` test below -- it is structurally barred from the
        # frontier. That is harmless when every cell in the pool has an R5, but
        # when a pool MIXES R5-less and R5-having cells (the BP-MFB plain/QE
        # pair is exactly that) the R5-having cell owns the whole frontier and,
        # because the frontier is concatenated AHEAD of `rest`, claims every
        # top_k slot regardless of sensitivity. Measured on the 2BP1HP-MFB pair:
        # a 43-entry all-QE frontier spanning sens 2.13->4.90 evicted the plain
        # cell's sens-2.13 optimum entirely. Restrict the frontier to cells
        # whose R5 actually IS a feedback resistor.
        elig = _frontier_eligible(ideal_continuous)
        srt = sorted(ideal_continuous, key=lambda s: (_sens(s), _r5(s)))
        frontier, best_r5 = [], float("inf")
        for s in srt:                          # ascending sens; keep strictly lower R5
            if elig.get(id(s), True) and 0.0 < _r5(s) < best_r5 - 1e-12:
                frontier.append(s); best_r5 = _r5(s)
        fids = {id(s) for s in frontier}
        rest = [s for s in srt if id(s) not in fids]
        ideal_continuous = (frontier + rest)[:eff_top_k]

    # --- Stage 2: resistor pre-distortion (NON-IDEAL only) ---
    if mode == "nonideal":
        continuous = solve_nonideal(ideal_continuous, cfg, opamp=eval_opamp,
                                    n_cores=n_cores, dc_gain=dc_gain,
                                    verbose=verbose)
    else:
        # Ideal mode: no correction needed. The continuous resistors from
        # synthesis already realize the ideal target.
        continuous = ideal_continuous

    # Cases for the snapper: only the topologies that actually appear in the
    # solutions (run_synthesis may expand a bare gained+notch LP cell into its
    # +R7 twin, so we scope to the realized set rather than the request). This
    # is family-decoupled -- an LP run derives no HP cells, and vice-versa.
    # In DC/HF-gain mode the ideal cases carry per-topology K (the nonideal TF
    # used for snapping is K-independent).
    if cases is None:
        import tf_derivation_v2 as _TF
        sol_names = sorted({s["topology"] for s in continuous})
        kmap = None
        if dc_gain is not None:
            _n2t = {_TF.topo_name(t): t for t in _TF.all_cells()}
            kmap = {nm: _TF.dc_gain_to_K(_n2t[nm], _design_subs(cfg), dc_gain)
                    for nm in sol_names if nm in _n2t}
        cases = get_cases(_design_subs(cfg), verbose=False, k_map=kmap,
                          topo_names=sol_names)

    # --- Stage 3: discrete hardware snap (exact physics, mode op-amp) ---
    snapped = snap_to_hardware(continuous, cfg, eval_opamp, cases,
                               target_dc=dc_gain)

    return {"mode": mode,
            "ideal_continuous": ideal_continuous,
            "continuous": continuous,
            "snapped": snapped,
            "cases": cases}


def print_results(result, limit=None):
    """Print the two standard tables, labeled by mode."""
    mode = result["mode"]
    if mode == "ideal":
        t1 = "CONTINUOUS RESISTORS (Ideal op-amp)"
        t2 = "DISCRETE HARDWARE BOM (Ideal physics checked)"
    else:
        t1 = "CONTINUOUS RESISTORS (Pre-distorted for Op-Amp Specs)"
        t2 = "DISCRETE HARDWARE BOM (Exact Physics Checked)"

    print_snapped_table(result["continuous"], limit=limit, title=t1)
    print_snapped_table(result["snapped"],    limit=limit, title=t2)


# =====================================================================
#  Example master cell
# =====================================================================
if __name__ == "__main__":
    cfg = {
        "f1": 10602, "f0": 10141, "fz": 34750, "Q": 1.0455, "K": 11346.0,
        "C_min": 6.8e-5, "C_max": 0.0033, "R_min": 3e-4, "R_max": 2.0,
        "MAX_R_RATIO": 18.0, "reg_weight": 0.02,
        "C_series": "E12", "R_series": "E24, E48",
    }

    # --- NON-IDEAL: pass the op-amp model ---
    opamp = dict(A_ol=1e5, GBWP_hz=0.95e5, Ro=1.0e-3)
    res_ni = synthesize(cfg, opamp=opamp, n_cores=32)
    print_results(res_ni, limit=None)

    # --- IDEAL: same call, opamp=None ---
    # res_id = synthesize(cfg, opamp=None, n_cores=32)
    # print_results(res_id, limit=None)
