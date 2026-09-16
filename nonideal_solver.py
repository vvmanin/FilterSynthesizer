# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  nonideal_solver.py
#  Non-ideal op-amp synthesis for the 3rd-order low-pass-notch section.
#
#  STRATEGY (frequency-domain correction):
#  The non-ideal TF (finite A_ol, 1-pole GBWP, output Ro) is higher
#  order than the 3rd-order design target, so it cannot match the target
#  exactly. Instead we:
#    1. Take ideal solutions (from unified_solver) as warm starts.
#    2. Hold the capacitors at their E12 values (manufacturability).
#    3. Re-optimize the resistors so the REAL (non-ideal) circuit
#       response matches the desired IDEAL response across a weighted
#       in-band frequency grid (DC .. a few x notch; HF tapered because
#       op-amp rolloff there is physically unavoidable).
#    4. Re-score the corrected design against the chosen op-amp.
#
#  This reuses the validated tf_derivation cache + make_response_func,
#  and the same caps-fixed / resistors-free philosophy as the ideal
#  solver's Phase 3. Each correction is independent -> parallelized
#  across all cores with a ProcessPoolExecutor + per-worker init.
# =====================================================================

import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import time
import numpy as np
import sympy as sp
from scipy.optimize import least_squares
from concurrent.futures import ProcessPoolExecutor

from tf_derivation_v2 import (get_cases, make_response_func, cell_components,
                              all_cells, topo_name, p1, w0, wz, Q, K)
from scoring import score_solution, OPAMP_LIBRARY


# =====================================================================
# 1. CORE: correct one ideal solution against a real op-amp
# =====================================================================
def _band_of(meta):
    """Coarse response band of a section from its topology metadata, used to
    place the pre-distortion's passband weighting. Returns one of
    'LP','LPn','HP','HPn','BP','notch'."""
    fam = meta.get("family", "LP")
    notch = bool(meta.get("notch", False))
    if fam in ("HP", "HP-MFB", "HP-AM"):
        return "HPn" if notch else "HP"
    if fam in ("BP", "BP-MFB", "BP-AM"):
        return "BP"
    if fam in ("NOTCH", "NOTCH-MFB", "NOTCH-AM"):
        return "notch"
    return "LPn" if notch else "LP"          # LP, LP-MFB, default


def _fit_grid_weight(cfg, fz, band):
    """Frequency grid + fit weight for the pre-distortion, placing weight on each
    band's MEANINGFUL targets so the corrected real-op-amp response matches the
    ideal where it matters:

      LP / LPn : passband at DC -> the legacy fz-centred low-pass ('DC-gain'
                 criterion). Kept byte-identical (LP is already good).
      HP / HPn : DC is NOT weighted. For a high-pass H(0) is either 0 (pure HP)
                 or a shelf H(0)=K*wz^2/w0^2 (HPn) that is a DERIVATIVE of the HF
                 gain and the pole/zero ratio -- not an independent target, and it
                 sits where loop gain is high so the op-amp barely moves it.
                 Instead weight what was specified: the 2*f0 & 5*f0 passband gains
                 (two points -> excludes the Q-peak at f0 and catches plateau tilt,
                 both below the uncorrectable GBWP rolloff), the POLE at f0, and
                 (HPn) the transmission ZERO at fz. A strong narrow zero anchor
                 keeps the notch deep -- dropping the legacy DC term without it
                 floats the zero, but with it the notch comes out deeper than the
                 legacy weight gave.
      BP       : passband IS the resonant peak -> anchor |H| at f0 (legacy skirt
                 retained; its DC weight is harmless since H(0)=0).
      notch    : band-reject DOES have a real DC plateau -> keep the legacy DC
                 weight, add the HF plateau at 5*f0, and strongly re-anchor the
                 deep zero at fz (at very low GBWP notch depth and HF flatness
                 genuinely compete)."""
    f0 = cfg["f0"]
    if band in ("LP", "LPn"):
        f = np.logspace(np.log10(fz / 30), np.log10(fz * 8), 240)
        wt = 1.0 / (1.0 + (f / (3 * fz)) ** 4)
        return f, 2 * np.pi * f, wt

    # bands whose passband extends above f0 need the grid to reach ~5*f0
    hi = max(fz * 8, 6 * f0)
    f = np.logspace(np.log10(fz / 30), np.log10(hi), 240)
    legacy = 1.0 / (1.0 + (f / (3 * fz)) ** 4)

    def _bump(fc, width=0.30):
        return np.exp(-0.5 * ((np.log(f) - np.log(fc)) / width) ** 2)

    if band == "HP":                                   # pure HP: no DC, no zero
        # weight 2*f0 & 5*f0 passband gains + the pole at f0
        wt = _bump(2 * f0) + _bump(5 * f0) + 0.7 * _bump(f0, 0.35)
    elif band == "HPn":                                # HP notch: no DC, anchor zero
        # weight 2*f0 & 5*f0 gains + pole(f0) + transmission zero(fz); the strong
        # narrow zero anchor both pins fz and keeps the notch deep
        wt = (_bump(2 * f0) + _bump(5 * f0) + 0.7 * _bump(f0, 0.35)
              + 3.5 * _bump(fz, 0.15))
    elif band == "BP":
        wt = legacy + _bump(f0)
    else:  # notch (band-reject): real DC plateau (legacy) + HF plateau + deep zero
        wt = legacy + _bump(5 * f0) + 3.5 * _bump(fz, 0.15)
    return f, 2 * np.pi * f, wt


def _correct(ideal_sol, Hid, Hni, opamp, cfg, fz, comp_names, band="LP"):
    """Resistor re-optimization so the real response matches the desired
    ideal shape in-band. Caps held fixed. `comp_names` is the per-cell
    {'caps':[...],'resistors':[...]} from tf_derivation_v2.cell_components.
    `band` selects where the passband weighting is placed (see _fit_grid_weight)."""
    cap_names = comp_names["caps"]
    R_names = comp_names["resistors"]
    caps = {k: ideal_sol[k] for k in cap_names}

    # "Equalize R, C values" (AM handbook): the ideal solve already forced R5 = R6
    # (and C2 = C3, which stays put here because caps are held fixed). Keep R6
    # LOCKED to R5 through the real-op-amp resistor re-optimization too -- else it
    # drifts and the toggle appears to "not work" in non-ideal mode. R6 is dropped
    # from the optimized set and mirrored from R5. The flag is only ever present on
    # AM sections; the R5/R6 guard keeps this inert elsewhere.
    equalize = bool(cfg.get("equalize_rc")) and ("R5" in R_names) and ("R6" in R_names)
    opt_R = [r for r in R_names if not (equalize and r == "R6")]

    # weighted grid placed on the band's PASSBAND (LP keeps the legacy
    # DC-emphasised weight; HP/BP/notch anchor their actual passband points)
    f, w, wt = _fit_grid_weight(cfg, fz, band)

    comp_ideal = {**caps, **{r: ideal_sol[r] for r in R_names}}
    H_target = Hid(comp_ideal, w)        # desired shape (fixed)

    x0 = np.array([ideal_sol[r] for r in opt_R], dtype=float)
    lb = np.full(len(opt_R), cfg["R_min"])
    ub = np.full(len(opt_R), cfg["R_max"])
    x0 = np.clip(x0, lb + 1e-12, ub - 1e-12)

    # light regularization keeps resistors near the ideal values (avoids
    # the optimizer wandering to exotic R sets that chase HF artifacts)
    reg = cfg.get("reg_weight", 0.02)

    def resid(x):
        comp = {**caps, **{n: v for n, v in zip(opt_R, x)}, **opamp}
        if equalize:
            comp["R6"] = comp["R5"]          # R6 locked to R5 (dropped from opt_R)
        # AM matched pair: R8 == R7 (R8 is not a separately-optimized resistor,
        # so it isn't in R_names). The non-ideal TF carries R8, so mirror it from
        # R7. No-op for MFB HP +R8 twins (R8 is a real R_names resistor there) and
        # for cells whose TF ignores R8.
        if "R8" not in comp and "R7" in comp:
            comp["R8"] = comp["R7"]
        d = (Hni(comp, w) - H_target) * wt
        rr = np.concatenate([d.real, d.imag])
        # relative deviation of each resistor from its ideal warm start
        rg = reg * (x - x0) / x0
        return np.concatenate([rr, rg])

    r = least_squares(resid, x0, bounds=(lb, ub), xtol=1e-12, ftol=1e-12)
    # least_squares returns the best point it found even when it stops on the
    # iteration cap (status 0) rather than a convergence test. At low GBWP the
    # tight 1e-12 tolerance is often not reached within max_nfev, yet the fit is
    # still good -- dropping it there silently removes an entire topology from
    # the candidate pool (the symptom: a depleted snap that lands below target).
    # Keep status-0 results; reject only a hard solver failure or non-finite x.
    # The snapper re-scores every survivor, so a poor fit simply ranks low.
    if r.status < 0 or not np.all(np.isfinite(r.x)):
        return None

    n_resp = 2 * len(w)
    fit_cost = float(np.sum(r.fun[:n_resp] ** 2))

    out = dict(ideal_sol)
    for n, v in zip(opt_R, r.x):
        out[n] = float(v)
    if equalize:
        out["R6"] = out["R5"]                # keep the handbook R5 = R6
    # AM matched pair: R8 follows the re-optimized R7 (ideal_sol carried R8 = the
    # pre-optimization R7, now stale). Guarded so MFB +R8 (R8 in R_names) is left
    # to its own optimized value.
    if out.get("R8") is not None and "R8" not in R_names and "R7" in out:
        out["R8"] = out["R7"]
    out["ni_fit_cost"] = fit_cost
    out["ni_R_shift_pct"] = float(np.max(np.abs((r.x - x0) / x0)) * 100)
    return out


# =====================================================================
# 2. WORKER (rebuilds response funcs once per process)
# =====================================================================
_W = {}

def _init_worker(design_subs_str, opamp, cfg, fz, k_map=None, topo_names=None):
    design = {sp.Symbol(k): v for k, v in design_subs_str.items()}
    if topo_names is None:
        topo_names = [topo_name(t) for t in all_cells()]
    cases = get_cases(design, verbose=False, k_map=k_map, topo_names=topo_names)
    _W["cases"] = cases
    _W["opamp"] = opamp
    _W["cfg"] = cfg
    _W["fz"] = fz
    _W["resp"] = {}
    _W["comp"] = {}
    for topo in topo_names:
        Hid, _n = make_response_func(cases[(topo, "ideal")])
        Hni, _m = make_response_func(cases[(topo, "nonideal")])
        _W["resp"][topo] = (Hid, Hni)
        _W["comp"][topo] = cell_components(cases[(topo, "ideal")])

def _worker(ideal_sol):
    topo = ideal_sol["topology"]
    Hid, Hni = _W["resp"][topo]
    band = _band_of(_W["cases"][(topo, "ideal")]["topo"])
    corrected = _correct(ideal_sol, Hid, Hni, _W["opamp"], _W["cfg"], _W["fz"],
                         _W["comp"][topo], band=band)
    if corrected is None:
        return None
    # re-score the corrected design
    scored = score_solution(corrected, _W["cases"], _W["opamp"])
    return scored


# =====================================================================
# 3. ORCHESTRATOR
# =====================================================================
def solve_nonideal(ideal_solutions, cfg, opamp="TL072",
                   n_cores=None, sort_by="ni_penalty", dc_gain=None, verbose=True):
    """Correct + score a batch of ideal solutions for a real op-amp.

    ideal_solutions : list of solver rows (from unified_solver)
    cfg             : must contain f1,f0,fz,Q and R_min,R_max (K optional)
    opamp           : library name or dict {A_ol, GBWP_hz, Ro(MOhm)}
    dc_gain         : if set, per-topology K is used (DC-gain mode), matching
                      the solver; the ideal target the pre-distortion tracks
                      then carries the correct per-topology gain.
    """
    n_cores = n_cores or os.cpu_count()
    if isinstance(opamp, str):
        op = OPAMP_LIBRARY[opamp]; op_name = opamp
    else:
        op = opamp; op_name = "custom"

    fz = cfg["fz"]
    design = {p1: 2*np.pi*cfg["f1"], w0: 2*np.pi*cfg["f0"],
              wz: 2*np.pi*cfg["fz"], Q: cfg["Q"], K: cfg.get("K", 1.0)}
    design_str = {str(k): float(v) for k, v in design.items()}
    # Only the topologies that actually appear in the ideal solutions need their
    # (ideal+nonideal) cases derived and response funcs lambdified -- deriving the
    # whole catalog in every worker is what made this stage dominate runtime.
    topo_names = sorted({s["topology"] for s in ideal_solutions})
    import tf_derivation_v2 as _TF
    if dc_gain is not None:
        k_map = {_TF.topo_name(t): _TF.dc_gain_to_K(t, design, dc_gain)
                 for t in _TF.all_cells()}
    else:
        # K-mode: still force the FRESH-derive path (a non-None k_map routes
        # get_cases through derive_all) instead of the shared disk cache. The
        # cache round-trips the non-ideal TF through srepr/sympify, and the
        # 3rd-order AM non-ideal TF is ~766k ops -- its parse is catastrophically
        # slow and memory-heavy PER WORKER (x n_cores), the same blow-up the
        # Phase-1/3 worker cache had. Re-deriving it is ~5 s and bounded. K does
        # not affect the TFs this stage uses (Hid/Hni), so cfg["K"] for all is fine.
        _kK = cfg.get("K", 1.0)
        k_map = {_TF.topo_name(t): _kK for t in _TF.all_cells()}

    if verbose:
        print(f"Non-ideal synthesis | {n_cores} cores | op-amp '{op_name}' "
              f"(A_ol={op['A_ol']:.0e}, GBWP={op['GBWP_hz']:.1e} Hz, "
              f"Ro={op['Ro']*1e6:.0f} ohm)")
        print(f"  Correcting {len(ideal_solutions)} ideal solutions...")

    t0 = time.time()
    with ProcessPoolExecutor(max_workers=n_cores,
                             initializer=_init_worker,
                             initargs=(design_str, op, cfg, fz, k_map, topo_names)) as pool:
        results = list(pool.map(_worker, ideal_solutions, chunksize=1))

    out = [r for r in results if r is not None]
    if sort_by:
        out.sort(key=lambda x: (x[sort_by]
                                if np.isfinite(x.get(sort_by, np.inf)) else 1e18))
    if verbose:
        print(f"  Corrected {len(out)}/{len(ideal_solutions)} in "
              f"{time.time()-t0:.1f}s")
    return out


# =====================================================================
# 4. PRETTY TABLE
# =====================================================================
def print_nonideal_table(scored, limit=None):
    if not scored:
        print("No corrected solutions.")
        return
    rows = scored if limit is None else scored[:limit]
    bar = "=" * 132
    print(bar)
    print(f"{'Idx':<3} | {'Topology':<11} | {'Sens':<5} | {'FitCost':<9} | "
          f"{'Rshift':<7} | {'Notch real':<10} | {'Depth real':<10} | "
          f"{'dGain':<7} | {'Penalty':<7}")
    print(bar)
    for i, s in enumerate(rows):
        print(f"{i:<3} | {s['topology']:<11} | {s['sens_score']:<5.2f} | "
              f"{s['ni_fit_cost']:<9.2e} | {s['ni_R_shift_pct']:<6.1f}% | "
              f"{s['ni_f_notch_real']:<10.0f} | {s['ni_depth_real_db']:<10.1f} | "
              f"{s['ni_d_gain_db']:<+7.3f} | {s['ni_penalty']:<7.2f}")
    print(bar)
    print("FitCost = weighted in-band |H_real - H_ideal|^2 after correction "
          "(lower = better match). Rshift = max resistor move from ideal.")


# =====================================================================
# 5. STANDALONE DEMO
# =====================================================================
if __name__ == "__main__":
    cfg = {"f1":508.9,"f0":486.76,"fz":1668,"Q":1.0455,"K":680.75,
           "C_min":6.8e-5,"C_max":0.01,"R_min":3e-4,"R_max":2.0,
           "MAX_R_RATIO":18.0, "reg_weight":0.02}

    # Build a couple of consistent ideal solutions for the demo (R5 from
    # constraint). In real use you pass unified_solver's best_solutions.
    design = {p1:2*np.pi*cfg["f1"], w0:2*np.pi*cfg["f0"],
              wz:2*np.pi*cfg["fz"], Q:cfg["Q"], K:cfg["K"]}
    cases = get_cases(design, verbose=False)
    cid = cases[("Without R7","ideal")]
    demo = []
    for C1v, R2v in [(0.0033,0.12), (0.0047,0.10)]:
        c = {"C1":C1v,"C2":3.3e-4,"C3":3.3e-4,"C4":8.2e-4,
             "R1":0.043,"R2":R2v,"R3":0.18,"R4":0.1,"R6":0.39}
        c["R5"] = float(cid["R5_constraint"].subs({sp.Symbol(k):v for k,v in c.items()}))
        demo.append(dict(c, topology="Without R7", sens_score=5.0, R7=None))

    scored = solve_nonideal(demo, cfg, opamp="TL072", n_cores=2)
    print_nonideal_table(scored)
