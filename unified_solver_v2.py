# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  unified_solver_v2.py
#  Topology-driven parallel synthesis for the full 10-cell VCVS family.
#
#  Generalizes the original unified_solver to ANY cell produced by
#  tf_derivation_v2, by reading the cell's var_list / R5_constraint /
#  gain-mode instead of hardcoding the 3LPn layout.
#
#  Two Phase-1 search modes (both preserved):
#    - ratio   : RC-invariant; any cap may hit C_max  (bulletproof)
#    - anchored: 3rd-order fixes C1=C_max; 2nd-order fixes C4=C_max.
#                Non-convergence of anchored mode is NOT an error
#                (some gain/order combos simply won't anchor) -- ratio
#                mode still covers the space.
#
#  Phase 3 snaps caps to the C-series and realigns resistors. R5 is
#  handled per cell: algebraic constraint (notch), free variable
#  (notchless-gained), or absent (unity followers).
#
#  Parallel across all cores via ProcessPoolExecutor + per-worker
#  topology rebuild (BLAS pinned to 1 thread/worker).
# =====================================================================

import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import time
import tempfile
import uuid
import gc
import numpy as np
import sympy as sp
from itertools import product as iproduct
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from scipy.optimize import least_squares

import tf_derivation_v2 as TF
import zero_manifold_solver as ZM
import cells_mfb_hp

# LP-MFB notch only: R8 (the V+ -> gnd leg) absorbs the HF-floor / passband-gain
# target and may legitimately need to sit BELOW the user's R_min. It is allowed
# down to R_min * R8_RELAX_FACTOR; every other resistor still honours R_min. (The
# gain residual only drives R8 small when the target gain demands it, so ordinary
# targets still settle R8 within the normal window.)
R8_RELAX_FACTOR = 0.02

# Passband-gain slack used by the HP-MFB notch gate in _assemble(). Mirrors
# topology_tab.GAIN_UNITY_TOL: a realized HF gain within +-2% of the target is
# what the UI already treats as "unity". It is the floor of the effective gate,
# which is max(cfg["gain_tol"], GAIN_SLACK).
GAIN_SLACK = 0.02


_PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37]


# =====================================================================
# Cap E-series grid (mirrors discrete_snapper philosophy)
# =====================================================================
_E_SERIES = {
    "E3":  [1.0, 2.2, 4.7],
    "E6":  [1.0, 1.5, 2.2, 3.3, 4.7, 6.8],
    "E12": [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2],
    "E24": [1.0,1.1,1.2,1.3,1.5,1.6,1.8,2.0,2.2,2.4,2.7,3.0,3.3,3.6,3.9,
            4.3,4.7,5.1,5.6,6.2,6.8,7.5,8.2,9.1],
}

def cap_grid(series_str, c_min, c_max):
    parts = [p.strip().upper() for p in series_str.split(",")]
    base = set()
    for p in parts:
        if p in _E_SERIES:
            base.update(_E_SERIES[p])
    arr = np.array(sorted(base))
    # Decade multipliers in µF. Upper bound 1e1 -> base*10 reaches ~91 µF, so any
    # user C_max up to ~91 µF (e.g. 2.2 µF, 10 µF) is realizable. The window is
    # then clipped to [c_min, c_max] below, so a small C_max (e.g. the 0.01 µF
    # default) yields exactly the same grid as before -- the extra high decades
    # are filtered out, giving zero change for normal designs. (Previously the
    # top multiplier was 1e-1, hard-capping every cap at ~0.91 µF regardless of
    # the user's C_max.)
    mult = [10**i for i in range(-6, 2)]
    grid = np.sort([round(v*m, 10) for m in mult for v in arr])
    return grid[(grid >= c_min*0.99) & (grid <= c_max*1.01)]

def two_nearest(val, grid):
    idx = np.searchsorted(grid, val)
    c = []
    if idx > 0:            c.append(float(grid[idx-1]))
    if idx < len(grid):    c.append(float(grid[idx]))
    return list(dict.fromkeys(c)) or [float(grid[np.abs(grid-val).argmin()])]


# =====================================================================
# Per-cell metadata (derived from var_list)
# =====================================================================
def cell_layout(case):
    """Return dict describing the variable layout for a topology cell."""
    names = [str(v) for v in case["var_list"]]
    cap_names = [n for n in names if n.startswith("C")]
    res_names = [n for n in names if n.startswith("R")]
    topo = case["topo"]
    return {
        "names": names,
        "cap_names": cap_names,
        "res_names": res_names,
        "n_caps": len(cap_names),
        "n_res": len(res_names),
        "n_residuals": len(case["res_eqs"]),
        "family": topo.get("family", "LP"),
        "order": topo["order"],
        "gain": topo.get("gain", "gained"),
        "notch": topo["notch"],
        "qe": topo.get("qe", False),
        # LP-MFB low-sensitivity notch branch. These MIRROR the topo-dict key
        # names on purpose: rescale_isolated_r5r6() is handed this layout dict
        # (not the topo), and every guard it probes -- family/qe/notch/order --
        # resolves against it. A renamed key there reads as None and the guard
        # silently inverts, so keep "ls"/"r1"/"r7" verbatim.
        "ls": topo.get("ls", False),        # low-sensitivity LP-notch cell
        "r1": topo.get("r1", False),        # ...with the in->a attenuator leg
        "r7": topo.get("r7", False),        # ...with the p->out positive feedback
        "has_R7": topo.get("has_R7", False),
        "has_R6": topo.get("has_R6", False),
        "c1_split": topo.get("c1_split", False),
        "has_R5_constraint": case["R5_constraint"] is not None,
        # HPn-MFB2 (the second HP-notch realization). Its (+)-input divider
        # R3(a->p) / R4(p->gnd) is CAPACITOR-FREE, so R4 must be excluded from
        # the MAX_R_RATIO core check -- see _assemble().
        "v2": topo.get("v2", False),
    }

def weyl_starts(n, lb, ub):
    lb = np.asarray(lb); ub = np.asarray(ub); dim = len(lb)
    out = []
    for i in range(1, n+1):
        pt = np.array([lb[j] + ((i*np.sqrt(_PRIMES[j])) % 1.0)*(ub[j]-lb[j])
                       for j in range(dim)])
        out.append(np.clip(pt, lb+1e-12, ub-1e-12))
    return out


def loguniform_starts(n, lb, ub):
    """Same additive-recurrence (Weyl) low-discrepancy sequence as weyl_starts,
    but laid down in LOG space, so a multi-decade box is sampled uniformly
    per-decade with NO extra start budget. Linear weyl_starts in a wide box puts
    ~all points in the top decade; log starts spread them across orders of
    magnitude. Used only for the supplementary wide ratio pass (large-spread
    roots such as the HP-MFB2 notch near its gain floor) and the Phase-3
    manifold-0 fallback -- both ADDITIVE to the unchanged legacy passes, so no
    previously-found solution is displaced."""
    lb = np.asarray(lb, float); ub = np.asarray(ub, float); dim = len(lb)
    llo, lhi = np.log(lb), np.log(ub)
    out = []
    for i in range(1, n + 1):
        frac = np.array([(i*np.sqrt(_PRIMES[j])) % 1.0 for j in range(dim)])
        out.append(np.exp(llo + frac*(lhi - llo)))
    return out


def seed_to_ratio_x0(seed, lay, lb, ub):
    """Map a cell-supplied analytic seed onto a Phase-1 "ratio"-mode x0.

    Ratio mode is RC-SCALE INVARIANT (phase1_worker rescales caps by
    gamma = C_max/max(C) and resistors by 1/gamma afterwards), so only the
    SHAPE of a seed matters and we are free to pick the common factor. Choose
    the gamma that centres caps and resistors in their boxes in log space, then
    clip into [lb, ub]. Returns None if the seed does not name every variable.

    A clipped seed is still a good start -- least_squares begins on the box face
    nearest the true root instead of somewhere random -- so clipping is not
    treated as failure.
    """
    names = lay["names"]
    if any(n not in seed for n in names):
        return None
    nC = lay["n_caps"]
    vals = np.array([float(seed[n]) for n in names], dtype=float)
    if not np.all(np.isfinite(vals)) or np.any(vals <= 0.0):
        return None
    lb = np.asarray(lb, float); ub = np.asarray(ub, float)
    # caps scale as *gamma, resistors as /gamma. Pick log-gamma that puts the
    # geometric mean of each block at the geometric centre of its box.
    lg = np.log(vals)
    box_c = 0.5*(np.log(lb[:nC]) + np.log(ub[:nC]))
    box_r = 0.5*(np.log(lb[nC:]) + np.log(ub[nC:]))
    want_c = float(np.mean(box_c - lg[:nC]))          # +log gamma
    want_r = float(np.mean(lg[nC:] - box_r))          # +log gamma
    g = np.exp(0.5*(want_c + want_r))
    out = np.concatenate([vals[:nC]*g, vals[nC:]/g])
    return np.clip(out, lb*(1.0 + 1e-9), ub*(1.0 - 1e-9))


def rescale_isolated_r5r6(full, topo, cfg=None, r5r6_lo=0.01, r5r6_hi=0.15):
    """For gained-NOTCHLESS cells (3LP-gained, 2LP-gained), R5 and R6 form an
    isolated branch whose ABSOLUTE scale is a free degree of freedom -- only
    the R5/R6 ratio affects the response (verified: residuals invariant under
    R5,R6 *k). Rescale both by a common factor so they land in
    [r5r6_lo, r5r6_hi] MOhm (default 10k-150k). If the ratio is too wide for
    both to fit, centre the pair's geometric mean in the window. The response
    is unchanged either way. Mutates and returns `full`."""
    # AM (all four families): R7 designates the matched inverter pair R7 = R8.
    # Only the ratio R8/R7 (fixed at 1 by construction) enters the ideal
    # response, so R7 cancels from it entirely -- its absolute scale is a free
    # DOF the ideal optimizer leaves wherever it started (zero Jacobian
    # column). Pin it to the geometric mean of the composite-integrator pair
    # {R5, R6} (the handbook equal-R choice, generalized), clipped into
    # [R_min, R_max]. The ideal response and sens_score are unchanged; the
    # non-ideal correction and the BOM then carry a sane matched-pair value.
    _fam = topo.get("family", "")
    if isinstance(_fam, str) and _fam.endswith("-AM"):
        r5 = full.get("R5"); r6 = full.get("R6")
        if r5 and r6 and cfg is not None:
            tgt = (r5 * r6) ** 0.5
            full["R7"] = min(max(tgt, float(cfg["R_min"])), float(cfg["R_max"]))
        # R8 == R7: the matched inverter pair is one nominal value, but emit it as
        # a real second component so the BOM lists it and the non-ideal response /
        # Monte-Carlo vary R7 and R8 independently (mismatch -> GB-compensation loss).
        if full.get("R7") is not None:
            full["R8"] = full["R7"]
        return full
    # LP-MFB QE: the positive-feedback divider R5 (p->out) / R6 (p->gnd) sets
    # k = R6/(R5+R6); only that RATIO affects the response, so the COMMON scale of
    # {R5,R6} is a free DOF (verified: H invariant under R5,R6 *k). The ideal
    # optimizer leaves the scale near MOhm. Pin the LOWER of the pair into
    # [R_min, 10*R_min] (geometric centre), preserving the ratio, while keeping
    # the larger within R_max. Response unchanged.
    if topo.get("family") == "LP-MFB" and topo.get("qe") and not topo.get("notch"):
        r5 = full.get("R5"); r6 = full.get("R6")
        if r5 and r6 and cfg is not None:
            lo = float(cfg["R_min"]); hi = 10.0 * lo
            lo_pair, hi_pair = min(r5, r6), max(r5, r6)
            f = ((lo / lo_pair) * (hi / lo_pair)) ** 0.5      # geo-centre lower in [lo,hi]
            if hi_pair * f > cfg["R_max"]:                    # keep larger within R_max
                f = cfg["R_max"] / hi_pair
            full["R5"] = r5 * f; full["R6"] = r6 * f
        return full
    # LP-MFB LS, BARE 2nd-order cells only (2LPn-MFB-LS and 2LPn-MFB-LS+R7):
    # node `a` IS the driven input, so the op-amp (+) group {R2 (a->p), R6
    # (p->gnd)} -- plus R7 (p->out) when present -- is fed only by ideal sources
    # and drives nothing. Only its internal RATIOS enter the response, so the
    # COMMON scale of the group is a free DOF (verified: monic TF coefficients
    # invariant under R2,R6[,R7] *k to ~1e-15) and the ideal optimizer leaves it
    # wherever it started (zero Jacobian column).
    #
    # The "+R1" cells and BOTH 3rd-order cells have a real node `a` that R2
    # loads, which destroys the invariance (same check: ~1e-1 relative change).
    # Hence the `not r1` / `order == 2` gate.
    #
    # Placement differs from the other divider branches ON PURPOSE: R2 sits in
    # the {R1..R4} "core" that the MAX_R_RATIO check reads, so pinning the group's
    # LOWER member near R_min (the QE / NOTCH-MFB convention) would inflate the
    # core spread and reject otherwise-good solutions. Instead put R2 on the
    # geometric mean of the fixed core resistors {R3,R4}, clipped so every member
    # stays inside [R_min, R_max]; if the spread is wider than the window, centre
    # the group's geometric mean. Response unchanged either way.
    if (topo.get("family") == "LP-MFB" and topo.get("ls")
            and topo.get("order") == 2 and not topo.get("r1")):
        keys = [k for k in ("R2", "R6", "R7") if full.get(k)]
        if len(keys) >= 2 and cfg is not None:
            vals = [full[k] for k in keys]
            core = [full[k] for k in ("R3", "R4") if full.get(k)]
            tgt = (float(np.prod(core)) ** (1.0 / len(core))) if core \
                else 10.0 * float(cfg["R_min"])
            lo_p, hi_p = min(vals), max(vals)
            f_min = float(cfg["R_min"]) / lo_p        # f*lo_p >= R_min
            f_max = float(cfg["R_max"]) / hi_p        # f*hi_p <= R_max
            if f_min <= f_max:
                f = min(max(tgt / full["R2"], f_min), f_max)
            else:                                     # group wider than the window
                f = ((float(cfg["R_min"]) * float(cfg["R_max"])) ** 0.5
                     / (lo_p * hi_p) ** 0.5)
            for k in keys:
                full[k] = full[k] * f
        return full
    # HP-MFB QE: the positive-feedback divider R4 (p->gnd) / R5 (p->out) sets
    # k = R4/(R4+R5); only that RATIO affects the response, so the COMMON scale of
    # {R4,R5} is a free DOF (verified: H invariant under R4,R5 *k to ~1e-16). This
    # is the R<->C-swapped twin of the LP-MFB-QE branch above; the divider symbols
    # differ (R4/R5 here vs R5/R6 there) because the HP all-pole netlist uses C4 for
    # the feedback cap and frees R4,R5 for the +divider. Pin the LOWER of the pair
    # into [R_min, 10*R_min] (geometric centre), keeping the larger within R_max.
    if topo.get("family") == "HP-MFB" and topo.get("qe") and not topo.get("notch"):
        r4 = full.get("R4"); r5 = full.get("R5")
        if r4 and r5 and cfg is not None:
            lo = float(cfg["R_min"]); hi = 10.0 * lo
            lo_pair, hi_pair = min(r4, r5), max(r4, r5)
            f = ((lo / lo_pair) * (hi / lo_pair)) ** 0.5      # geo-centre lower in [lo,hi]
            if hi_pair * f > cfg["R_max"]:                    # keep larger within R_max
                f = cfg["R_max"] / hi_pair
            full["R4"] = r4 * f; full["R5"] = r5 * f
        return full
    # BP-MFB QE: the positive-feedback divider R4 (p->gnd) / R5 (p->out) sets
    # k = R4/(R4+R5); only that RATIO affects the response, so the COMMON scale of
    # {R4,R5} is a free DOF (verified: H invariant under R4,R5 *k to ~1e-16). This
    # is electrically the SAME +divider as the HP-MFB-QE branch above (same symbols)
    # -- the band-pass all-pole core uses C2 for the a->out feedback and frees R4,R5
    # for the +divider. Pin the LOWER of the pair into [R_min, 10*R_min] (geometric
    # centre), keeping the larger within R_max. Response unchanged.
    if topo.get("family") == "BP-MFB" and topo.get("qe"):
        r4 = full.get("R4"); r5 = full.get("R5")
        if r4 and r5 and cfg is not None:
            lo = float(cfg["R_min"]); hi = 10.0 * lo
            lo_pair, hi_pair = min(r4, r5), max(r4, r5)
            f = ((lo / lo_pair) * (hi / lo_pair)) ** 0.5      # geo-centre lower in [lo,hi]
            if hi_pair * f > cfg["R_max"]:                    # keep larger within R_max
                f = cfg["R_max"] / hi_pair
            full["R4"] = r4 * f; full["R5"] = r5 * f
        return full
    # NOTCH-MFB: the op-amp (+) input divider {R1 (in->p), R4 (p->gnd)} is UNLOADED
    # (the (+) input draws no current), so only its RATIO R1/R4 affects the response
    # -- the COMMON scale of {R1,R4} is a free DOF (verified: H invariant under
    # R1,R4 *k to ~1e-16; the passband gain R4/(R1+R4) depends only on the ratio).
    # Pin the LOWER of the pair into [R_min, 10*R_min] (geometric centre), keeping
    # the larger within R_max. Gain -- hence the whole response -- is unchanged.
    if topo.get("family") == "NOTCH-MFB":
        r1 = full.get("R1"); r4 = full.get("R4")
        if r1 and r4 and cfg is not None:
            lo = float(cfg["R_min"]); hi = 10.0 * lo
            lo_pair, hi_pair = min(r1, r4), max(r1, r4)
            f = ((lo / lo_pair) * (hi / lo_pair)) ** 0.5      # geo-centre lower in [lo,hi]
            if hi_pair * f > cfg["R_max"]:                    # keep larger within R_max
                f = cfg["R_max"] / hi_pair
            full["R1"] = r1 * f; full["R4"] = r4 * f
        return full
    # NOTCH: the response depends only on RATIOS within {R4,R5,R6} (gain = R5/R4,
    # Q via R5/R6, the symmetric-notch constraint via R5/R6 and R5/R4) -- the
    # COMMON scale of this triple is a free DOF (verified to machine precision:
    # the response is invariant under R4,R5,R6 *k, and the derived R5 scales by
    # exactly k). Rescale the present subset (R6 is absent on the atten cell)
    # into [r5r6_lo, r5r6_hi] MOhm so the feedback trio lands in a sane window;
    # if the spread is too wide, centre the geometric mean. Response unchanged.
    if topo.get("family", "LP") == "NOTCH":
        present = [k for k in ("R4", "R5", "R6") if full.get(k)]
        vals = [full[k] for k in present]
        if len(vals) >= 2:
            lo_p, hi_p = min(vals), max(vals)
            f_min = r5r6_lo / lo_p          # f*lo_p >= lo
            f_max = r5r6_hi / hi_p          # f*hi_p <= hi
            if f_min <= f_max:
                f = (f_min * f_max) ** 0.5
            else:
                f = ((r5r6_lo * r5r6_hi) ** 0.5) / ((lo_p * hi_p) ** 0.5)
            for k in present:
                full[k] = full[k] * f
        return full
    # BP: the gain block (R4 m->gnd, R5 out->m) enters the response ONLY through
    # the ratio R5/R4 (= K-1), so the COMMON scale of {R4,R5} is a free DOF
    # exactly like the notch feedback trio. Rescale the present pair into
    # [r5r6_lo, r5r6_hi] MOhm (the atten cell has neither, so this is a no-op
    # there). Response unchanged.
    if topo.get("family", "LP") == "BP":
        present = [k for k in ("R4", "R5") if full.get(k)]
        vals = [full[k] for k in present]
        if len(vals) >= 2:
            lo_p, hi_p = min(vals), max(vals)
            f_min = r5r6_lo / lo_p
            f_max = r5r6_hi / hi_p
            if f_min <= f_max:
                f = (f_min * f_max) ** 0.5
            else:
                f = ((r5r6_lo * r5r6_hi) ** 0.5) / ((lo_p * hi_p) ** 0.5)
            for k in present:
                full[k] = full[k] * f
        return full
    # LP-only below. This isolated ratio-only branch exists for LP gained-notchless
    # cells. In HP the passband (HF) gain is 1 + R5/R7, so rescaling R5 would
    # CHANGE the gain, and R6 sits in the b->out branch that shapes the poles
    # -- the HP gained-notchless free dimension is NOT an R5/R6 common scale.
    if topo.get("family", "LP") != "LP":
        return full
    if not (topo["gain"] == "gained" and not topo["notch"]):
        return full
    r5 = full.get("R5"); r6 = full.get("R6")
    if not r5 or not r6:
        return full
    lo_pair = min(r5, r6); hi_pair = max(r5, r6)
    f_min = r5r6_lo / lo_pair        # f*lo_pair >= lo
    f_max = r5r6_hi / hi_pair        # f*hi_pair <= hi
    if f_min <= f_max:
        f = (f_min * f_max) ** 0.5   # geometric centre of feasible factors
    else:
        gmean = (r5 * r6) ** 0.5
        f = ((r5r6_lo * r5r6_hi) ** 0.5) / gmean
    full["R5"] = r5 * f
    full["R6"] = r6 * f
    return full


# =====================================================================
# Worker process state
# =====================================================================
_W = {}

def _init_worker(design_str, cfg, topo_names, k_map=None, cache_file=None):
    design = {sp.Symbol(k): v for k, v in design_str.items()}
    if cache_file is not None:
        # Workers LOAD the orchestrator-derived cases; they never derive or
        # write a cache. This removes the fork-time 32x-simultaneous derivation
        # memory spike and every multi-process cache race (the prior worker-side
        # get_cases() was the source of the intermittent BrokenProcessPool).
        cases = TF.load_cases(cache_file)
    else:
        cases = TF.get_cases(design, verbose=False, k_map=k_map,
                             topo_names=topo_names)
    cases = apply_equalize(cases, cfg)     # idempotent; matches orchestrator
    _W["cfg"] = cfg
    # name -> K actually used in that cell's residuals. For every HP family
    # dc_gain_to_K() returns sign*|HF gain|, so abs(K) IS the target passband
    # gain -- _assemble() gates the realized H(inf) against it.
    _W["k_map"] = dict(k_map or {})
    _W["cases"] = cases
    _W["funcs"] = {}
    _W["zm_funcs"] = {}
    for name in topo_names:
        c = cases[(name, "ideal")]
        lay = cell_layout(c)
        vl = c["var_list"]
        res_f = sp.lambdify(vl, c["res_eqs"], "numpy", cse=True)
        r5_f = (sp.lambdify(vl, c["R5_constraint"].subs(_design_targets(design)), "numpy", cse=True)
                if c["R5_constraint"] is not None else None)
        a1_f = sp.lambdify(vl, c["a1_expr"], "numpy", cse=True)
        a2_f = sp.lambdify(vl, c["a2_expr"], "numpy", cse=True)
        # realized DC gain H(0)=num(0)/den(0) (any common s-free factor cancels);
        # used to report the MFB passband gain, which is an R-ratio rather than
        # the VCVS 1+R5/R6 form.
        h0_expr = c["tf_num"].subs(TF.s, 0) / c["tf_den"].subs(TF.s, 0)
        h0_f = sp.lambdify(vl, h0_expr, "numpy", cse=True)
        # realized HF gain H(inf) = ratio of leading s-coefficients. For HP cells
        # H(0)=0 (origin zeros block DC), so h0_f is useless there; the HP passband
        # gain is this leading-coeff plateau instead (all-pole |C2/C4| etc;
        # notch R8/(R3+R8)). Cheap: LC of each s-polynomial, ratio taken numerically.
        hinf_expr = sp.Poly(c["tf_num"], TF.s).LC() / sp.Poly(c["tf_den"], TF.s).LC()
        hinf_f = sp.lambdify(vl, hinf_expr, "numpy", cse=True)
        # analytic Jacobian of the residual vector w.r.t. ALL vars; each solve
        # site slices the columns it varies (see phase1/phase3 workers).
        jac_f = sp.lambdify(vl, sp.Matrix(c["res_eqs"]).jacobian(list(vl)),
                            "numpy", cse=True)
        nidx = {str(v): i for i, v in enumerate(vl)}
        _W["funcs"][name] = dict(layout=lay, res_f=res_f, r5_f=r5_f,
                                 a1_f=a1_f, a2_f=a2_f, h0_f=h0_f, hinf_f=hinf_f,
                                 var_names=lay["names"],
                                 jac_f=jac_f, nidx=nidx,
                                 res_cols=[nidx[n] for n in lay["res_names"]])
        # zero-manifold C2 cells also get the parallel-C2 prepped funcs
        if name in ZM.PARALLEL_C2_CELLS:
            _W["zm_funcs"][name] = ZM.prep_cell_funcs(c, design)

def _design_targets(design):
    # design dict already has the numeric target subs; R5_constraint was
    # stored already substituted in tf_derivation? No -- it is symbolic in
    # p1,w0,wz,Q,K. Substitute here.
    return design


# =====================================================================
# Bounds (per layout)
# =====================================================================
def ratio_bounds(lay, cfg, wide=False):
    nC, nR = lay["n_caps"], lay["n_res"]
    if wide:
        # Supplementary WIDE window. The legacy window's CAP floor (0.01) is the
        # binding constraint: the single common RC-scale factor that the ratio
        # mode applies must place BOTH the cap block in [c_lo,c_hi] AND the
        # resistor block in [r_lo,r_hi]. A root whose caps sit ~1 decade above
        # the legacy floor relative to its resistors (the HP-MFB2 notch near its
        # gain floor is exactly this) leaves NO feasible common scale, so the
        # narrow window can't represent it at all and Phase 1 finds nothing.
        # Dropping the cap floor (and resistor floor) ~1 decade restores the
        # overlap. Ceilings are unchanged -- these roots never need the top, so
        # widening up would only thin the start density for no gain. ~1 decade
        # only => start density stays high (no compute blow-up).
        #
        # The RESISTOR floor must span the user's ACTUAL envelope. A hard 3e-4
        # caps the representable resistor spread at 1.2/3e-4 = 4000, but the
        # HP-MFB2 HP-notch near its gain floor needs the WHOLE window (measured
        # 1.2e4-1.7e4 for R in [300 ohm, 5 MOhm]) -- the (+)-divider R4/R3 =
        # D/(1-D) diverges there. With the legacy floor phase-1 pins against the
        # box wall and never converges. Derive it from cfg, but never NARROWER
        # than the legacy 3e-4 (so no existing envelope loses start density).
        r_lo = 3e-4
        if cfg.get("R_min") and cfg.get("R_max"):
            r_lo = min(r_lo, 1.2 * float(cfg["R_min"]) / float(cfg["R_max"]))
        lb = np.array([1e-3]*nC + [r_lo]*nR)
        ub = np.array([1.5]*nC + [1.2]*nR)
    else:
        lb = np.array([0.01]*nC + [0.001]*nR)
        ub = np.array([1.5]*nC + [1.2]*nR)
    return lb, ub

def _relax_r8_lb(lb, lay, cfg, res_offset):
    """LP-MFB notch only: drop R8's lower bound to R_min*R8_RELAX_FACTOR so it can
    absorb the HF-floor/gain target below R_min. `lb` is the bound vector whose
    resistor block starts at `res_offset` (ordered by lay['res_names'])."""
    if lay.get("family") == "LP-MFB" and "R8" in lay["res_names"]:
        lb[res_offset + lay["res_names"].index("R8")] = cfg["R_min"] * R8_RELAX_FACTOR
    return lb


def anchored_bounds(lay, cfg):
    """Fix one cap at C_max to pin the RC scale: C1 for 3rd-order; for
    2nd-order the family's main cap -- C4 (LP) or C2 (HP, which has no C4
    except in the atten cell, where C4 is the small divider cap and must
    NOT be the anchor). Returns (lb, ub, anchor_cap_name, free_caps)."""
    fam = lay.get("family", "LP")
    if isinstance(fam, str) and fam.endswith("-AM"):
        anchor = "C2"            # AM core Miller cap; present in every AM cell
                                 # (3rd-order AM uses C4 for the input pole and
                                 # C1 = H*C2 on the feedforward cells, so the
                                 # generic order-3 C1 anchor would mis-pin)
    elif lay.get("family") == "BP-MFB" and lay["order"] == 3:
        # 3rd-order band-pass caps are C3 (the input-network cap, printed C0),
        # C1 and C2. Which is LARGEST depends on where the absorbed real pole
        # sits: measured, C3 dominates for f1 <~ 0.3*f0 and C1 above that. The
        # generic order-3 "C1" anchor below would therefore pin the wrong cap
        # for low-f1 targets, forcing the others above C_max and wasting the
        # whole anchored start family. Pick from the target instead.
        anchor = "C3" if float(cfg.get("f1", 0.0)) < 0.5*float(cfg["f0"]) else "C1"
    elif lay.get("ls"):
        # LP-MFB LS branch (caps C1?,C2,C3): the on-axis-zero condition forces
        # C2/C3 = 4 Q^2 (1 + R4/R5) > 1, so C2 is ALWAYS the largest cap at both
        # orders. Pin it at C_max and let the others fall below -- the generic
        # order-3 "C1" anchor would mis-pin the 3rd-order LS cells.
        anchor = "C2"
    elif lay["order"] == 3:
        anchor = "C1"
    elif fam == "HP":
        anchor = "C2"
    elif fam == "NOTCH":
        anchor = "C1"            # notch has only C1,C2; pin C1 (C2 shapes the zero)
    elif fam == "BP":
        anchor = "C1"            # band-pass has only C1,C2; pin C1 (C2 shapes the pole)
    elif fam == "LP-MFB":
        anchor = "C3"            # MFB caps are C1,C2,C3 (no C4); pin C3 to set scale
    elif fam == "HP-MFB":
        anchor = "C3"            # HP-MFB caps: all-pole C1,C2,C3,C4 / notch C1,C2,C3;
                                 # C3 is present in BOTH (b->m all-pole, b->out notch)
    elif fam == "BP-MFB":
        anchor = "C1"            # BP-MFB caps are C1,C2; pin C1 (C2 shapes the pole)
    elif fam == "NOTCH-MFB":
        anchor = "C1"            # NOTCH-MFB caps are C1,C2; pin C1 (C2 shapes the zero/pole)
    else:
        anchor = "C4"
    if anchor not in lay["cap_names"]:        # safety for unusual cells
        anchor = lay["cap_names"][0]
    free_caps = [c for c in lay["cap_names"] if c != anchor]
    lb = np.array([cfg["C_min"]*1.1]*len(free_caps) + [cfg["R_min"]*2.0]*lay["n_res"])
    ub = np.array([cfg["C_max"]*0.9]*len(free_caps) + [cfg["R_max"]*0.9]*lay["n_res"])
    lb = _relax_r8_lb(lb, lay, cfg, len(free_caps))
    return lb, ub, anchor, free_caps

def phase3_res_bounds(lay, cfg):
    lb = np.full(lay["n_res"], float(cfg["R_min"]))
    lb = _relax_r8_lb(lb, lay, cfg, 0)
    return lb, np.full(lay["n_res"], cfg["R_max"])


# =====================================================================
# PHASE 1 worker
# =====================================================================
def phase1_worker(task):
    cfg = _W["cfg"]; name = task["topo_name"]
    F = _W["funcs"][name]; lay = F["layout"]
    res_f = F["res_f"]; jac_f = F["jac_f"]; nC = lay["n_caps"]; nres = lay["n_residuals"]
    x0 = task["x0"]

    if task["mode"] == "ratio":
        lb, ub = task.get("lb"), task.get("ub")
        if lb is None:                     # legacy call path -> legacy window
            lb, ub = ratio_bounds(lay, cfg)
        lb = np.asarray(lb, float); ub = np.asarray(ub, float)
        def obj(x):
            errs = list(res_f(*x))
            # soft ordering hint only if >=2 caps after C1 (legacy C3<=C2)
            return np.array(errs, dtype=float)
        def jacf(x):                       # x is in full var_list order -> all columns
            return np.asarray(jac_f(*x), dtype=float)
        r = least_squares(obj, x0, jac=jacf, bounds=(lb, ub), method="trf",
                          xtol=1e-10, ftol=1e-10, max_nfev=700)
        cost = float(np.sum(np.array(r.fun)[:nres]**2))
        if not (r.success and cost < 1e-5):
            return None
        c = np.array(r.x[:nC]); rr = np.array(r.x[nC:])
        gamma = cfg["C_max"] / np.max(c)
        c_phys = (c*gamma).tolist(); r_phys = (rr/gamma).tolist()
        if min(c_phys) < cfg["C_min"]*0.95 or max(r_phys) > cfg["R_max"]*1.5:
            return None
        caps = {n: v for n, v in zip(lay["cap_names"], c_phys)}
        return {"topo_name": name, "caps": caps, "r_hint": r_phys, "cost": cost,
                "wide": bool(task.get("wide", False))}

    else:  # anchored
        lb, ub, anchor, free_caps = anchored_bounds(lay, cfg)
        cap_order = lay["cap_names"]
        # full-jac columns matching x = [free_caps..., resistors...]
        anch_cols = [F["nidx"][c] for c in free_caps] + [F["nidx"][r] for r in lay["res_names"]]
        def _full_args(x):
            fc = {n: v for n, v in zip(free_caps, x[:len(free_caps)])}
            fc[anchor] = cfg["C_max"]
            return [fc[c] for c in cap_order] + list(x[len(free_caps):])
        def obj(x):
            return np.array(res_f(*_full_args(x)), dtype=float)
        def jacf(x):
            return np.asarray(jac_f(*_full_args(x)), dtype=float)[:, anch_cols]
        try:
            r = least_squares(obj, x0, jac=jacf, bounds=(lb, ub), method="trf",
                              xtol=1e-11, ftol=1e-11, max_nfev=700)
        except Exception:
            return None
        cost = float(np.sum(np.array(r.fun)[:nres]**2))
        if not (r.success and cost < 1e-6):
            return None   # anchored non-convergence is acceptable, not an error
        fc = {n: v for n, v in zip(free_caps, r.x[:len(free_caps)])}
        fc[anchor] = cfg["C_max"]
        caps = {c: fc[c] for c in cap_order}
        return {"topo_name": name, "caps": caps,
                "r_hint": r.x[len(free_caps):].tolist(), "cost": cost,
                "wide": False}


# =====================================================================
# PHASE 3 worker
# =====================================================================
def phase3_worker(task):
    cfg = _W["cfg"]; name = task["topo_name"]
    F = _W["funcs"][name]; lay = F["layout"]
    res_f = F["res_f"]; r5_f = F["r5_f"]; jac_f = F["jac_f"]; res_cols = F["res_cols"]
    cap_names = lay["cap_names"]; res_names = lay["res_names"]
    cap_vals = [task["cap_combo"][c] for c in cap_names]
    lb3, ub3 = phase3_res_bounds(lay, cfg)

    def obj(x):
        return np.array(res_f(*(cap_vals + list(x))), dtype=float)
    def jacf(x):
        return np.asarray(jac_f(*(cap_vals + list(x))), dtype=float)[:, res_cols]

    # Acceptance threshold scales with the solution manifold. Square (zero-
    # manifold) and low-slack cells cannot absorb cap-snap error exactly --
    # but the residual that remains is a small coefficient mismatch that the
    # downstream discrete snapper absorbs when picking E-series resistors.
    # Verified: a 2e-3 residual on a manifold-0 cell still places the notch
    # on-target to <0.1% and >80 dB deep. So we accept generously here and
    # let the snapper do final hardware selection.
    manifold = lay["n_res"] - lay["n_residuals"]
    if manifold >= 2:
        accept = 1e-6
    elif manifold == 1:
        accept = 1e-3
    else:                       # manifold == 0 (fully determined)
        accept = 5e-3

    best_r, best_c = None, 1e100
    for h in task["r_hints"]:
        x0 = np.clip(np.asarray(h, float), lb3+1e-12, ub3-1e-12)
        r = least_squares(obj, x0, jac=jacf, bounds=(lb3, ub3), xtol=1e-9, ftol=1e-9, max_nfev=300)
        c = float(np.sum(np.array(r.fun)**2))
        if c < best_c: best_c, best_r = c, r
        if best_c <= accept: break
    if best_c > accept:
        # Fallback restarts when the harvested hints don't land. Manifold-0
        # cells (the fully-determined HP/LP notch families) are the hardest --
        # their resistor solution is a single point with an often ill-conditioned
        # Jacobian, so the legacy 8 LINEAR starts in [R_min,R_max] frequently
        # miss it. Use LOG-distributed starts (cover the R window per-decade) and
        # more of them for manifold-0. Purely additive: this block only runs when
        # the hints already failed, so good-case output is unchanged.
        n_fb = 24 if manifold == 0 else 8
        for x0 in loguniform_starts(n_fb, lb3, ub3):
            r = least_squares(obj, x0, jac=jacf, bounds=(lb3, ub3), xtol=1e-11, ftol=1e-11, max_nfev=500)
            c = float(np.sum(np.array(r.fun)**2))
            if c < best_c and r.success: best_c, best_r = c, r
            if best_c <= accept: break
    if best_r is None or best_c > accept:
        return None

    # Reusable assembler: turn a resistor vector + its target-cost into a full
    # scored solution dict (R5 from the notch constraint, gain, bound/ratio
    # checks, coefficient sensitivity). Returns None if it fails a hard check.
    def _assemble(xvec, cost):
        Rsol = {n: float(v) for n, v in zip(res_names, xvec)}
        full = dict(task["cap_combo"]); full.update(Rsol)
        # equalize (handbook R5=R6, C2=C3): the ideal solve dropped R6,C3 -> mirror
        # them back before anything reads them (rescale pins R7=R8=sqrt(R5*R6)).
        if cfg.get("equalize_rc") and str(lay.get("family","")).endswith("-AM"):
            if full.get("R5") is not None and "R6" not in full:
                full["R6"] = full["R5"]
            if full.get("C2") is not None and "C3" not in full:
                full["C3"] = full["C2"]
        if r5_f is not None:
            va = [full[n] for n in lay["names"]]
            R5_val = float(r5_f(*va))
            if R5_val <= 0:
                return None
            full["R5"] = R5_val
        elif "R5" in Rsol:
            full["R5"] = Rsol["R5"]          # free (notchless-gained)
        rescale_isolated_r5r6(full, lay, cfg)
        fam = lay.get("family", "LP")
        if fam == "HP":
            if lay["gain"] == "gained":
                r7 = full.get("R7")
                internal_gain = 1.0 + full.get("R5", 0)/r7 if r7 else 1.0
            elif lay["gain"] == "atten":
                C2v, C4v = full.get("C2"), full.get("C4")
                if C2v and C4v:
                    if lay["order"] == 3:
                        C1v = full.get("C1") or 0.0
                        den = C1v*C2v + C1v*C4v + C2v*C4v
                        internal_gain = (C1v*C2v/den) if den else 1.0
                    else:
                        internal_gain = C2v/(C2v + C4v)
                else:
                    internal_gain = 1.0
            else:
                internal_gain = 1.0
        elif fam == "NOTCH":
            # Inverting biquad: |H(0)| = |H(inf)| = R5/R4 (the same at DC and HF).
            # The atten cell (no R6) still reports its emergent R5/R4 magnitude.
            r4 = full.get("R4"); r5 = full.get("R5")
            internal_gain = (r5 / r4) if (r4 and r5) else 1.0
        elif fam == "BP":
            # Band-pass passband gain is the swept CENTER-frequency peak (= Ki Q/w0),
            # not a DC/HF plateau; it is read per-solution by the realized-peak
            # evaluator (topology_tab._realized_dc), so the BOM "_dc" comes from
            # there. Report a neutral 1.0 here. Sign stays +1 (non-inverting).
            internal_gain = 1.0
        elif fam == "LP-MFB":
            # Gain is a free R-ratio on a fixed circuit (-R4/R2 all-pole; a
            # node-divider for the notch). Read it straight off the realized DC
            # transfer function rather than any VCVS 1+R5/R6 form.
            internal_gain = abs(float(F["h0_f"](*[full[n] for n in lay["names"]])))
        elif fam == "HP-MFB":
            # HP passband gain is the HIGH-FREQUENCY plateau = |H(inf)| (H(0)=0
            # here -- origin zeros block DC, so h0_f is meaningless). all-pole:
            # |C2/C4| (2nd) etc; notch: R8/(R3+R8) < 1 structurally. Read straight
            # off the realized TF via the leading-coeff ratio.
            internal_gain = abs(float(F["hinf_f"](*[full[n] for n in lay["names"]])))
        elif fam == "BP-MFB":
            # Band-pass passband gain is the swept CENTER-frequency peak (= |Ki| Q/w0),
            # read per-solution by the realized-peak evaluator (topology_tab._realized_dc),
            # so the BOM "_dc" comes from there. Report a neutral 1.0 here. The stage is
            # INVERTING; sign is set below (out["sign"] = -1).
            internal_gain = 1.0
        elif fam == "NOTCH-MFB":
            # Pure-notch MFB passband gain = |H(0)| = |H(inf)| = R4/(R1+R4), a positive
            # divider < 1. Read straight off the realized DC transfer function (h0_f),
            # exactly like the LP-MFB all-pole gain readout.
            internal_gain = abs(float(F["h0_f"](*[full[n] for n in lay["names"]])))
        elif fam == "LP-AM":
            # AM LP gain is a free R-ratio (-R6/R2 at out1, -R5/R1 at out2):
            # read it straight off the realized DC transfer function.
            internal_gain = abs(float(F["h0_f"](*[full[n] for n in lay["names"]])))
        elif fam == "HP-AM":
            # AM HP passband gain is the HF plateau |H(inf)| = C1/C2 (x input
            # divider at 3rd order); H(0) = 0 -- read the leading-coeff ratio.
            internal_gain = abs(float(F["hinf_f"](*[full[n] for n in lay["names"]])))
        elif fam == "BP-AM":
            # Band-pass passband gain is the swept CENTER-frequency peak, read
            # per-solution by topology_tab._realized_dc. Neutral 1.0 here;
            # the stage is INVERTING (out["sign"] = -1 below).
            internal_gain = 1.0
        elif fam == "NOTCH-AM":
            # AM pure-notch plateau |H(0)| = |H(inf)| = C1/C2 -- read the
            # leading-coeff ratio (well-defined at both ends once wz = w0).
            internal_gain = abs(float(F["hinf_f"](*[full[n] for n in lay["names"]])))
        else:
            if lay["gain"] == "gained":
                if lay["has_R7"]:
                    internal_gain = 1.0 + full.get("R7", 0)/full["R6"]
                else:
                    internal_gain = 1.0 + full.get("R5", 0)/full["R6"] if "R6" in full else 1.0
            else:
                internal_gain = 1.0
        # R-range check. Every resistor honours R_max; lower bound is R_min,
        # except LP-MFB's R8 which may sit down to R_min*R8_RELAX_FACTOR.
        r8_floor = cfg["R_min"] * R8_RELAX_FACTOR
        for k, v in full.items():
            if not k.startswith("R") or v is None:
                continue
            lo = r8_floor if (k == "R8" and fam == "LP-MFB") else cfg["R_min"]
            if v < lo or v > cfg["R_max"]:
                return None
        # MAX_R_RATIO bounds the RC-NETWORK spread. The HPn-MFB2 (+)-input
        # divider R3 (a->p) / R4 (p->gnd) touches NO capacitor -- node p is
        # cap-free -- so R4/R3 = D/(1-D) IS the passband-gain setting, not a
        # network spread, and it necessarily blows up as the cell approaches its
        # structural gain floor K -> 1+. Policing it as a spread makes the routed
        # cell return ZERO solutions for every target in ~(0.99, 1.012): the
        # minimum achievable R1..R4 ratio at K=1.00005 is 1.08e4 against a 9000
        # limit, while R1..R3 alone is 83. Same escape LP-MFB grants R8 via
        # R8_RELAX_FACTOR.
        core_names = (("R1", "R2", "R3")
                      if (fam == "HP-MFB" and lay["notch"] and lay.get("v2"))
                      else ("R1", "R2", "R3", "R4"))
        core = [full[r] for r in core_names if r in full]
        if len(core) >= 2 and max(core)/min(core) > cfg["MAX_R_RATIO"]:
            return None
        # HF-gain gate (HP-MFB notch cells). 3HPn-MFB2 is MANIFOLD-0 (6 resistors,
        # 6 residuals), so phase3_worker's acceptance is 5e-3 on the AGGREGATE
        # squared residual -- it can bury sqrt(5e-3) = 7.1% of RELATIVE gain error
        # and still report "converged" (measured: target 1.05 -> realized 1.1157;
        # target 1.5 -> realized 1.557). Now that the 0.99 margin makes MFB2 the
        # SOLE cell above unity, that is the dominant error, so check the realized
        # H(inf) directly. internal_gain is |hinf_f(...)| off the exact TF and, for
        # every HP cell, |K| is the target HF gain (dc_gain_to_K -> sign*|gain|).
        # Slack = max(gain_tol, GAIN_SLACK): targets between the routing margin and
        # MFB2's envelope-limited floor (~1.006) land within 1.6%, i.e. inside the
        # +-2% window the UI already calls "unity".
        gain_err = None
        if fam == "HP-MFB" and lay["notch"]:
            Kt = abs(float(_W.get("k_map", {}).get(name) or 0.0))
            if Kt > 0.0:
                gain_err = abs(internal_gain - Kt) / Kt
                slack = max(float(cfg.get("gain_tol", 0.005) or 0.005), GAIN_SLACK)
                if gain_err > slack:
                    return None
        va = [full[n] for n in lay["names"]]
        a1b = F["a1_f"](*va); a2b = F["a2_f"](*va)
        ss = 0.0
        for i in range(len(va)):
            p = list(va); p[i] *= 1.01
            ss += ((F["a1_f"](*p)-a1b)/a1b/0.01)**2 + ((F["a2_f"](*p)-a2b)/a2b/0.01)**2
        out = {"topology": name, "sens_score": float(np.sqrt(ss)),
               "internal_gain": internal_gain, "cost": cost,
               "gain_err": gain_err}
        if fam == "NOTCH":
            out["sign"] = -1        # single-op-amp notch is inverting (H = -R5/R4)
        elif fam == "LP-MFB":
            out["sign"] = +1 if lay["notch"] else -1   # notch non-inv; all-pole inv
        elif fam == "HP-MFB":
            out["sign"] = +1 if lay["notch"] else -1   # notch non-inv; all-pole inv
        elif fam == "BP-MFB":
            out["sign"] = -1        # MFB band-pass is inverting (Ki = b_lead/a_lead < 0)
        elif fam == "NOTCH-MFB":
            out["sign"] = +1        # MFB pure notch is non-inverting (H(0)=R4/(R1+R4)>0)
        elif isinstance(fam, str) and fam.endswith("-AM"):
            out["sign"] = -1        # every AM cell inverts (see cells_am_core)
        for k in ["C1","C2","C3","C4","R1","R2","R3","R4","R5","R6","R7","R8"]:
            out[k] = full.get(k, None if k in ("R5","R7","R8") else 0.0)
        # carry parallel-cap provenance (-C1s twins: C1 = C1a||C1b) so the snapper
        # (out = dict(sol)), the BOM display, and the schematic-name suffix all
        # see the split. C1 itself already holds the parallel sum.
        for k in ("C1a", "C1b", "C1_parallel", "C2a", "C2b", "C2_parallel"):
            if full.get(k) is not None:
                out[k] = full[k]
        return out

    sols = []
    nat = _assemble(best_r.x, best_c)
    if nat is not None:
        sols.append(nat)

    # --- R5 trade-off ladder ---
    # Keep the natural best-fit solution above (it is best where the feedback
    # path is inherently low-resistance), then ADD feasible solutions at
    # progressively LOWER R5 -- distributed from just above R_min up to the
    # natural R5 -- using the free DOF on the manifold (>= 1). Each is pinned to
    # its R5 target while the design targets stay met (weighted _BIG), so the
    # realized response is unchanged; only the feedback resistance / sensitivity
    # trade-off differs. Lower R5 means a smaller HF hump but worse coefficient
    # sensitivity; the user picks the point they want. Enabled by reg_weight>0.
    w5 = float(cfg.get("reg_weight", 0.0) or 0.0)
    if w5 > 0.0 and manifold >= 1 and r5_f is not None and nat is not None:
        _BIG = 100.0
        R5_nat = nat.get("R5") or 0.0
        lo = cfg["R_min"] * 1.05
        hi = R5_nat * 0.92
        if R5_nat > cfg["R_min"]*1.5 and hi > lo:
            n_seeds = 6
            seen = set()
            for t in np.geomspace(lo, hi, n_seeds):
                def obj_force(x, t=float(t)):
                    va = cap_vals + list(x)
                    base = [_BIG*v for v in res_f(*va)]
                    base.append(_BIG * (float(r5_f(*va))/t - 1.0))   # pin R5 -> t
                    return np.array(base, dtype=float)
                try:
                    rb = least_squares(obj_force, best_r.x, bounds=(lb3, ub3),
                                       jac="2-point", xtol=1e-10, ftol=1e-10, max_nfev=200)
                    cb = float(np.sum(np.array(res_f(*(cap_vals + list(rb.x))))**2))
                    if cb > max(accept, 1e-4):
                        continue                       # left the design manifold
                    s = _assemble(rb.x, cb)
                    if s is None or not s.get("R5"):
                        continue
                    if s["R5"] >= R5_nat*0.98:
                        continue                       # not genuinely lower
                    b = round(np.log10(s["R5"]) * 40)  # ~6%-wide R5 buckets
                    if b in seen:
                        continue                       # near-duplicate R5
                    seen.add(b)
                    sols.append(s)
                except Exception:
                    pass

    return sols if sols else None


# =====================================================================
# Zero-manifold parallel worker (one (C2,C3,C4) combo per task)
# =====================================================================
def zm_worker(task):
    cfg = _W["cfg"]
    funcs = _W["zm_funcs"][task["topo_name"]]
    return ZM.solve_one_combo(funcs, task["cap_map"], task["c2rep"], cfg,
                              task["w0_t"], task["wz_t"], task["pole_tol"],
                              weyl_starts,
                              gain_tol=task["gain_tol"], target_dc=task["target_dc"])



# =====================================================================
# Valley harvest -> E-series cap combos
# =====================================================================
def _nearest(val, grid):
    """Single closest E-series grid value to `val`."""
    g = np.asarray(grid)
    return float(g[np.abs(g - val).argmin()])


def parallel_c1_candidates(target, grid, k=6):
    """(sum, C1a, C1b) triples for the k grid PAIRS whose parallel sum C1a+C1b is
    nearest `target`. Two E-series caps in parallel synthesize a far denser value
    lattice than the single grid, so a C1/C2 ratio (HP-AM / notch HF gain) or a
    notch wz that no single stock cap can hit becomes reachable. Deduped by
    rounded sum; the single-cap value (partner = the grid value closest to 0
    offset) is naturally included when it is already the best match, so a target
    that IS on-grid still yields a clean pair. This is the C1 analogue of the
    zero-manifold parallel-C2 path, kept deliberately small (k) so Phase-3 stays
    bounded."""
    g = sorted(float(x) for x in grid)
    out = []
    for a in g:
        b = _nearest(target - a, g)          # best partner for this leg
        if b <= 0:
            continue
        out.append((a + b, a, b))
    seen = {}
    for sm, a, b in sorted(out, key=lambda t: abs(t[0] - target)):
        key = round(sm, 12)
        if key not in seen:
            # canonical order (a <= b) so (a,b) and (b,a) don't both appear
            seen[key] = (sm, min(a, b), max(a, b))
        if len(seen) >= k:
            break
    return list(seen.values())


def _feasible_scale_interval(C, R, cfg, margin=1.01):
    """Closed-form interval of common RC-scale factors f for which a shape stays
    in the component box. The network is invariant under (C->C*f, R->R/f), so for
    physical caps C and resistors R the box constraints C*f in [C_min,C_max] and
    R/f in [R_min,R_max] give f in [f_lo, f_hi]:

        f_lo = max(C_min/min(C),  max(R)/R_max)
        f_hi = min(C_max/max(C),  min(R)/R_min)

    `margin` shrinks the box slightly so seeded scales land safely inside (the
    final R_min/R_max enforcement is still done by Phase-3 / _assemble). Returns
    (f_lo, f_hi); the interval is empty (f_lo > f_hi) iff no scale fits the box.
    MAX_R_RATIO is scale-invariant, so it does not enter here."""
    if not C or not R or min(C) <= 0 or min(R) <= 0:
        return 1.0, 1.0
    Cmin = cfg["C_min"] * margin; Cmax = cfg["C_max"]   # no C_max inset: the legacy
    Rmin = cfg["R_min"] * margin; Rmax = cfg["R_max"] / margin   # max cap sits AT C_max
    f_lo = max(Cmin / min(C), max(R) / Rmax)
    f_hi = min(Cmax / max(C), min(R) / Rmin)
    return f_lo, f_hi


def _snap_optimal_scale(C, cg, f_lo, f_hi, n=12):
    """Scan the feasible scale interval and return the factor f that snaps the
    caps most cleanly onto the E-series grid (minimizes the summed relative
    distance of each C*f to its nearest grid value). Pure arithmetic, no solve --
    this is Increment 3. Returns None if the interval is degenerate."""
    if not (f_hi > f_lo):
        return None
    best_f, best_e = None, float("inf")
    for f in np.geomspace(f_lo, f_hi, n):
        e = 0.0
        for c in C:
            v = c * f
            e += abs(_nearest(v, cg) - v) / v
        if e < best_e:
            best_e, best_f = e, float(f)
    return best_f


def _scale_seeds_for_valley(C, R, cfg, cg):
    """Extra RC-scale realizations to emit for a valley, ON TOP OF the mandatory
    legacy f=1 (emitted verbatim by the caller).

    Design: the network is invariant under (C->C*f, R->R/f), and coefficient
    SENSITIVITY is scale-invariant too, so a shape's only scale-dependent property
    is how its values land on the E-series grid. We therefore add a seed ONLY when
    the legacy f=1 scale is itself INFEASIBLE -- i.e. pinning the largest cap at
    C_max drives some resistor below R_min (the "no realization" case, and the same
    mechanism that, at high C_max, silently excludes the GOOD low-sensitivity shapes
    and leaves only cramped ones). In that case we rescue the shape at the
    snap-optimal feasible scale (Increment 3) with a full two_nearest combo set.

    When legacy f=1 IS feasible we add nothing: the legacy BOMs already cover that
    shape, so the output is byte-identical to the prior solver (provable zero
    regression, no truncation displacement). Empty list also covers the genuinely
    infeasible shape (no in-box scale at all) -> the honest message stands."""
    if min(R) >= cfg["R_min"] and max(R) <= cfg["R_max"]:
        return []                       # legacy f=1 feasible -> no change at all
    f_lo, f_hi = _feasible_scale_interval(C, R, cfg)
    if f_lo > f_hi:
        return []                       # no in-box scale exists -> truly infeasible
    fstar = _snap_optimal_scale(C, cg, f_lo, f_hi)
    fp = fstar if fstar is not None else float(np.clip(1.0, f_lo, f_hi))
    return [(fp, "full")]               # rescue at the snap-optimal feasible scale


_SENS_FUNCS = {}


def _sens_funcs(name, cases):
    """Lambdify (once per cell, cached) the denominator-coefficient expressions
    a1,a2 used by the post-solve sens_score, so a valley's sensitivity can be
    estimated at harvest time. Returns (a1_f, a2_f, lay, covered). `covered` is
    False for cells whose full component vector is NOT spanned by caps + solved
    resistors (i.e. there is a derived resistor such as a constrained R5); for
    those a1_f/a2_f are None and the proxy degrades to +inf (cost/magnitude
    retention is then used unchanged -> no behaviour change for such cells)."""
    if name not in _SENS_FUNCS:
        case = cases[(name, "ideal")]
        lay = cell_layout(case)
        covered = set(lay["names"]) <= (set(lay["cap_names"]) | set(lay["res_names"]))
        if covered:
            vl = case["tf_var_list"]
            a1_f = sp.lambdify(vl, case["a1_expr"], "numpy", cse=True)
            a2_f = sp.lambdify(vl, case["a2_expr"], "numpy", cse=True)
        else:
            a1_f = a2_f = None
        _SENS_FUNCS[name] = (a1_f, a2_f, lay, covered)
    return _SENS_FUNCS[name]


def _valley_sens_proxy(v, lay, a1_f, a2_f):
    """Coefficient-sensitivity of a valley, using the SAME formula as the final
    sens_score (RMS fractional change of a1,a2 under +1% per-component steps) but
    evaluated at the phase-1 estimate (caps + r_hint ~ final components, since
    Phase-3 only refines the resistors). Scale-invariant, so it ranks shapes
    faithfully. Returns +inf when the component vector can't be assembled (derived
    resistor) -> such valleys are simply skipped by the sensitivity floor."""
    if a1_f is None:
        return float("inf")
    full = dict(v["caps"])
    for rn, rv in zip(lay["res_names"], v["r_hint"]):
        full[rn] = rv
    try:
        va = [full[n] for n in lay["names"]]
        a1b = float(a1_f(*va)); a2b = float(a2_f(*va))
        if a1b == 0.0 or a2b == 0.0:
            return float("inf")
        ss = 0.0
        for i in range(len(va)):
            p = list(va); p[i] *= 1.01
            ss += ((float(a1_f(*p)) - a1b) / a1b / 0.01) ** 2 \
                + ((float(a2_f(*p)) - a2b) / a2b / 0.01) ** 2
        return float(np.sqrt(ss))
    except (KeyError, ZeroDivisionError, ValueError, TypeError, OverflowError):
        return float("inf")


def harvest(p1_results, cases, cfg, max_valleys, hints_per_combo):
    results = sorted((r for r in p1_results if r), key=lambda v: v["cost"])
    # Collect the DISTINCT valleys per cell (dedup by cap vector). When the
    # [C_min,C_max] window is wide, phase-1 finds valleys spread across the whole
    # cap range; a plain "first max_valleys by cost" keep lets the extreme-cap
    # valleys (smallest+largest) consume the budget and DROP the middle of the
    # range -- so a wide window paradoxically loses mid-cap realisations that a
    # narrow window keeps. We therefore stratify retention across cap magnitude.
    distinct = defaultdict(list)
    for v in results:
        name = v["topo_name"]
        cap_names = cell_layout(cases[(name,"ideal")])["cap_names"]
        cv = np.array([v["caps"][c] for c in cap_names])
        dup = None
        for e in distinct[name]:
            if np.allclose(cv, np.array([e["caps"][c] for c in cap_names]), rtol=0.08):
                dup = e; break
        if dup is None:
            distinct[name].append(v)        # results is cost-sorted -> so is this
        elif dup.get("wide") and not v.get("wide"):
            # Same valley already represented by a WIDE root, but this is a LEGACY
            # root: prefer the legacy representative so the legacy passes' exact
            # r_hint (hence their exact BOMs) is preserved. (Cost ordering is only
            # a tie-break here; all kept roots already satisfy the phase-1 cost
            # gate, so swapping the representative changes no acceptance.)
            dup.update(v)

    cmin = max(cfg["C_min"], 1e-15)
    span = float(np.log(cfg["C_max"] / cmin)) or 1.0
    # Preserve valley DENSITY across the cap window. A wider [C_min,C_max] spans
    # more feasible E-series cap combos, so a fixed budget thins out and drops the
    # middle of the range. Scale the budget with the window's log-width (reference
    # ~ ln(5), a narrow ~half-decade window); narrow windows are unchanged.
    eff_mv = int(round(max_valleys * max(1.0, span / 1.6)))

    # Stratified selection of `budget` valleys out of `cands` (the legacy (a)
    # sensitivity-floor + (b) cap-magnitude coverage + (c) cost-fill logic,
    # factored out so it can be applied to the legacy and wide pools separately).
    def _select_valleys(name, cands, budget):
        if len(cands) <= budget:
            return list(cands)
        cap_names = cell_layout(cases[(name, "ideal")])["cap_names"]
        def _bin(v):
            gm = np.exp(np.mean([np.log(max(v["caps"][c], 1e-15)) for c in cap_names]))
            return int(np.clip(budget * np.log(gm / cmin) / span, 0, budget - 1))
        bins = defaultdict(list)
        for v in cands:
            bins[_bin(v)].append(v)
        chosen, taken = [], set()
        # (a) SENSITIVITY FLOOR: guarantee the lowest-sensitivity valleys survive.
        a1_f, a2_f, lay_s, _cov = _sens_funcs(name, cases)
        proxied = sorted(((_valley_sens_proxy(v, lay_s, a1_f, a2_f), v) for v in cands),
                         key=lambda pv: pv[0])
        for p, v in proxied:
            if len(chosen) >= budget // 2 or not np.isfinite(p):
                break
            chosen.append(v); taken.add(id(v))
        # (b) cap-magnitude coverage: one lowest-cost representative per bin.
        for b in sorted(bins):
            if len(chosen) >= budget:
                break
            rep = min(bins[b], key=lambda v: v["cost"])
            if id(rep) not in taken:
                chosen.append(rep); taken.add(id(rep))
        # (c) fill any remaining budget with the next-best leftovers by cost.
        chosen += [v for v in cands if id(v) not in taken][:max(0, budget - len(chosen))]
        return chosen[:budget]

    kept = defaultdict(list)
    for name, vals in distinct.items():
        if len(vals) <= eff_mv:
            kept[name] = vals               # narrow window: unchanged behaviour
            continue
        # REGRESSION GUARANTEE: the supplementary wide ratio pass must never
        # evict a valley the legacy passes found. Split the pool and keep legacy
        # first. If legacy alone fills/overflows the budget, behave EXACTLY as the
        # pre-wide-pass solver (select among legacy; wide dropped). Otherwise keep
        # every legacy valley and fill the remainder with the best wide valleys.
        legacy = [v for v in vals if not v.get("wide")]
        extra  = [v for v in vals if v.get("wide")]
        if len(legacy) >= eff_mv:
            kept[name] = _select_valleys(name, legacy, eff_mv)
        else:
            chosen = list(legacy)
            chosen += _select_valleys(name, extra, eff_mv - len(legacy))
            kept[name] = chosen[:eff_mv]

    cg = cap_grid(cfg["C_series"], cfg["C_min"], cfg["C_max"])
    combo_map = defaultdict(list)
    for name, valleys in kept.items():
        # zero-manifold C2 cells are routed to the parallel-C2 path instead
        # of the standard cap-snap Phase-3 (their valleys are returned in
        # `kept` for the orchestrator to dispatch via zm_worker).
        if name in ZM.PARALLEL_C2_CELLS:
            continue
        lay_c = cell_layout(cases[(name,"ideal")])
        cap_names = lay_c["cap_names"]
        c1_split = lay_c.get("c1_split", False)
        for v in valleys:
            # (1b) C1-SPLIT cells (-C1s twins): realize C1 as two parallel grid
            # caps. Enumerate parallel sums near the valley's continuous C1 (the
            # other caps snap normally via two_nearest); the resistors re-solve
            # per candidate sum in the worker, so wz/poles stay on target while
            # the denser C1a+C1b lattice hits otherwise-unreachable gains. C1a/C1b
            # ride in the combo key -> survive dict(_k) -> flow into the solution
            # via `full = dict(cap_combo)` in _assemble. Skips the legacy /
            # scale-seed blocks (parallel realization is this twin's whole job).
            # The C1 split preserves the gain-critical C1/partner ratio against
            # INDEPENDENT snapping of that partner. For HP/notch cells the gain is
            # the pure cap ratio C1/C2 (partner C2); for the BP out2 tap (2BP-AM2)
            # the peak gain is C1*R4/(C3*R6), whose cap part is C1/C3 (partner C3).
            # Snapping C1 and its partner separately drifts the ratio off target
            # (why the single-C1 base cell fails at e.g. gain 1.1); so for each
            # snapped partner we RE-TARGET C1 at ratio*partner_grid and pick the
            # parallel pair C1a||C1b nearest that. ratio = the continuous
            # C1/partner the solver converged to. Resistors re-solve per candidate.
            # The C1 split preserves the gain-critical PURE cap ratio C1/C2
            # (HP / notch cells: the passband gain is exactly C1/C2, no resistor
            # can fix it) against INDEPENDENT snapping of C2 -- for each snapped C2
            # we RE-TARGET C1 at ratio*C2_grid and pick the nearest parallel pair.
            # This is why the single-C1 base cell fails at e.g. gain 1.1. Cells
            # whose gain is resistor-trimmable (the BP out2 tap) get no -C1s twin.
            if c1_split and "C1" in cap_names and "C2" in cap_names:
                ratio = v["caps"]["C1"] / v["caps"]["C2"]
                others = [c for c in cap_names if c != "C1"]
                ocand = {c: two_nearest(v["caps"][c], cg) for c in others}
                for ovals in iproduct(*[ocand[c] for c in others]):
                    base_combo = {c: round(val,10) for c, val in zip(others, ovals)}
                    c1_target = ratio * base_combo["C2"]
                    for c1sum, c1a, c1b in parallel_c1_candidates(c1_target, cg):
                        combo = dict(base_combo)
                        combo["C1"] = round(c1sum, 12)
                        combo["C1a"] = round(c1a, 12)
                        combo["C1b"] = round(c1b, 12)
                        combo["C1_parallel"] = True
                        key = (name, tuple(sorted(combo.items())))
                        combo_map[key].append(v["r_hint"])
                continue
            # (1) LEGACY scale (largest cap pinned at C_max, f=1) -- emitted
            # UNCONDITIONALLY and verbatim, so every BOM the prior solver produced
            # is still produced (mandatory regression guard). Bit-identical block.
            cand = {c: two_nearest(v["caps"][c], cg) for c in cap_names}
            for combo_vals in iproduct(*[cand[c] for c in cap_names]):
                combo = {c: round(val,10) for c, val in zip(cap_names, combo_vals)}
                key = (name, tuple(sorted(combo.items())))
                combo_map[key].append(v["r_hint"])

            # (2) ADDED RC-scale seeds -- the scale (C*f, R/f) is a free DOF that
            # leaves the response and the coefficient sensitivity unchanged. The
            # legacy block pins f=1 (max cap at C_max); when that scale drives a
            # resistor below R_min the design was wrongly reported infeasible, and
            # even when feasible the forced scale may snap poorly. So we ALSO emit:
            # a snap-optimal alternative when f=1 is feasible, or a rescued
            # realization at a feasible scale when it is not. Applies to every
            # family (the scale pin lived in the shared phase-1, so does the fix).
            C = [v["caps"][c] for c in cap_names]
            R = list(v["r_hint"])
            for f, mode in _scale_seeds_for_valley(C, R, cfg, cg):
                sc_caps = {c: C[i] * f for i, c in enumerate(cap_names)}
                sc_rhint = [r / f for r in R]
                if mode == "full":
                    cand2 = {c: two_nearest(sc_caps[c], cg) for c in cap_names}
                else:
                    cand2 = {c: [_nearest(sc_caps[c], cg)] for c in cap_names}
                for combo_vals in iproduct(*[cand2[c] for c in cap_names]):
                    combo = {c: round(val,10) for c, val in zip(cap_names, combo_vals)}
                    key = (name, tuple(sorted(combo.items())))
                    combo_map[key].append(sc_rhint)

    tasks = []
    for (name, _k), hints in combo_map.items():
        combo = dict(_k)
        tasks.append({"topo_name": name, "cap_combo": combo,
                      "r_hints": hints[:hints_per_combo]})
    return tasks, {k: len(v) for k, v in kept.items()}, kept


def dedup(results):
    best = {}
    for s in results:
        if s is None: continue
        # include the C2 parallel split (C2a/C2b) so distinct parallel
        # realizations of the same C2 sum are not collapsed; include a coarse
        # R5 bucket so the R5 trade-off ladder (same caps, different feedback
        # resistance) is preserved instead of collapsing to best-sensitivity.
        r5 = s.get("R5") or 0.0
        r5b = round(np.log10(r5)*40) if r5 > 0 else 0
        key = (s["topology"], round(s["C1"] or 0,10), round(s["C2"] or 0,10),
               round(s["C3"] or 0,10), round(s["C4"] or 0,10),
               round(s.get("C2a") or 0,12), round(s.get("C2b") or 0,12),
               round(s.get("C1a") or 0,12), round(s.get("C1b") or 0,12), r5b)
        if key not in best or s["sens_score"] < best[key]["sens_score"]:
            best[key] = s
    return list(best.values())


# =====================================================================
# ORCHESTRATOR
# =====================================================================
def apply_equalize(cases, cfg):
    """Handbook 'Equalize R,C values' toggle for AM cells (default ON in the UI).

    Forces R5 = R6 and C2 = C3 by substituting R6->R5 and C3->C2 in the IDEAL
    case (the solve) and dropping R6, C3 from its var_list -> Phase-3 enumerates
    one integrator cap (C3 = C2) and the resistor solve carries R6 = R5. The
    matched pair R7 = R8 is ALWAYS enforced (GB compensation), independent of
    this toggle. The NON-IDEAL case is left untouched, so Monte-Carlo still
    perturbs R5, R6, C2, C3 (and R7, R8) as independent physical parts; _assemble
    mirrors R6 = R5 and C3 = C2 back into the BOM. When OFF, the solver keeps the
    full DOF (R5 != R6, C2 != C3 allowed) while R7 = R8 preserves GBWP
    independence and the topology's low sensitivity.

    Returns a NEW cases dict (shallow-copied case entries) so the tf_derivation
    module cache is never mutated."""
    if not cfg.get("equalize_rc", False):
        return cases
    from tf_symbols import R5, R6, C2, C3
    sub = {R6: R5, C3: C2}
    out = dict(cases)
    for (name, kind), c in cases.items():
        fam = c.get("topo", {}).get("family", "")
        if kind != "ideal" or not (isinstance(fam, str) and fam.endswith("-AM")):
            continue
        nc = dict(c)
        nc["res_eqs"] = [e.subs(sub) for e in c["res_eqs"]]
        for key in ("a1_expr", "a2_expr", "tf_num", "tf_den"):
            if c.get(key) is not None:
                nc[key] = c[key].subs(sub)
        nc["var_list"] = [v for v in c["var_list"] if v not in (R6, C3)]
        out[(name, kind)] = nc
    return out


def run_synthesis(cfg, topologies=None, n_cores=None,
                  ratio_starts=60, anchored_starts=120,
                  max_valleys=12, hints_per_combo=3,
                  pole_tol=0.01, gain_tol=0.01, max_c2_cands=4,
                  dc_gain=None, verbose=True):
    """Synthesize across one or more topology cells.

    topologies : list of cell names (e.g. ["3LPn-gained","2LP-unity"]) or
                 None for ALL 10 cells.
    dc_gain    : if None, cfg["K"] is used directly (legacy). If set (linear
                 DC gain, e.g. 2.0 for +6 dB), K is computed PER TOPOLOGY via
                 K = DC * a0/b0 in the correct (rad/s)^n units. Unity cells
                 ignore K either way. This is the future-facing interface for
                 the math-synthesis stage and for user-specified gain.
    pole_tol   : pole/notch tolerance for the zero-manifold parallel-C2 path.
    gain_tol   : DC-gain tolerance for the zero-manifold path (default +-1%).
    max_c2_cands : nearest C2 single/parallel values to try per valley target.
    """
    n_cores = n_cores or os.cpu_count()
    # gain_tol is a call argument, but _assemble() (which runs inside the forked
    # phase-3 workers) needs it for the HF-gain gate. cfg is the only thing the
    # workers receive, so publish it there. Copy: never mutate the caller's dict.
    cfg = {**cfg, "gain_tol": float(gain_tol)}

    design = {TF.p1: 2*np.pi*cfg["f1"], TF.w0: 2*np.pi*cfg["f0"],
              TF.wz: 2*np.pi*cfg["fz"], TF.Q: cfg["Q"], TF.K: cfg.get("K", 1.0)}
    design_str = {str(k): float(v) for k, v in design.items()}

    # Finalize the topology set FIRST so we derive/cache only what we solve
    # (an LP run never pays HP derivation cost, and vice-versa).
    if topologies is None:
        topologies = [TF.topo_name(t) for t in TF.all_cells()]
    else:
        # R7-alias expansion (reproduces the old solver, where "3LPn gained"
        # searched BOTH With-R7 and Without-R7 as one topology). Naming a
        # gained+notch LP cell WITHOUT an explicit +R7 suffix pulls in its R7
        # twin automatically; naming "...+R7" selects only that one. (HP gained
        # cells always carry R7 as a single cell -- the "LPn" guard below
        # excludes them, so no spurious twin is added for HP.)
        avail = {TF.topo_name(t) for t in TF.all_cells()}
        expanded = []
        for name in topologies:
            expanded.append(name)
            if name.endswith("-gained") and "LPn" in name:
                twin = name + "+R7"
                if twin in avail and twin not in topologies:
                    expanded.append(twin)
        # dedup, preserve order
        seen = set(); topologies = [t for t in expanded
                                    if not (t in seen or seen.add(t))]

    # DC/HF-gain mode: per-topology K (computed only for the solved cells).
    # Different (rad/s)^n dimension per LP cell; for HP, K = target HF gain.
    k_map = None
    if dc_gain is not None:
        _name2topo = {TF.topo_name(t): t for t in TF.all_cells()}

        def _cell_dc(topo):
            # 3rd-order MFB2 HP-notch: its reachable band is OPEN at 1, and the
            # floor DEPENDS ON THE ENVELOPE (both R- and C-spread ceilings). Clamp
            # a unity/below target up to the envelope-aware floor here, where cfg
            # is in scope -- dc_gain_to_K itself only sees the fixed default. A
            # tighter envelope (e.g. C_max=0.01, R_max=2) raises the floor to
            # ~1.024, so the old fixed 1.01 still fell in the dead zone. dc_gain is
            # a magnitude for HP cells, so clamp its absolute value up to the floor.
            if (topo.get("family") == "HP-MFB" and topo.get("notch")
                    and topo.get("v2") and topo.get("order") == 3):
                fl = cells_mfb_hp.mfb2_v2_gain_floor(cfg)
                g = abs(float(dc_gain))
                return g if g >= fl else fl
            return dc_gain

        k_map = {name: TF.dc_gain_to_K(_name2topo[name], design, _cell_dc(_name2topo[name]))
                 for name in topologies if name in _name2topo}
    cases = TF.get_cases(design, verbose=False, k_map=k_map, topo_names=topologies)
    cases = apply_equalize(cases, cfg)     # handbook R5=R6, C2=C3 for AM (Issue 4)

    # name -> topo for every registered cell. Needed independently of the
    # DC/HF-gain branch below (which builds its own map only when dc_gain is
    # given) because the analytic-seed hook is used in BOTH gain modes.
    _all_topos = {TF.topo_name(t): t for t in TF.all_cells()}

    # Build Phase-1 task list
    p1_tasks = []
    for name in topologies:
        lay = cell_layout(cases[(name, "ideal")])
        lb_r, ub_r = ratio_bounds(lay, cfg)
        for x0 in weyl_starts(ratio_starts, lb_r, ub_r):
            p1_tasks.append({"topo_name": name, "mode": "ratio", "x0": x0,
                             "lb": lb_r, "ub": ub_r})
        # Supplementary WIDE ratio pass (log-seeded), ADDITIVE to the legacy
        # pass above so nothing it finds is lost. Reaches the large-spread roots
        # the narrow linear window cannot represent (e.g. the HP-MFB2 HP-notch
        # near its gain floor, the (1.0, ~1.4) coverage hole). Half the legacy
        # ratio budget, log-distributed so a wider box needs no extra starts.
        lb_w, ub_w = ratio_bounds(lay, cfg, wide=True)
        n_wide = max(8, ratio_starts // 2)
        for x0 in loguniform_starts(n_wide, lb_w, ub_w):
            p1_tasks.append({"topo_name": name, "mode": "ratio", "x0": x0,
                             "lb": lb_w, "ub": ub_w, "wide": True})
        lb_a, ub_a, _anc, _fc = anchored_bounds(lay, cfg)
        for x0 in weyl_starts(anchored_starts, lb_a, ub_a):
            p1_tasks.append({"topo_name": name, "mode": "anchored", "x0": x0})
        # ---- ANALYTIC SEEDS (additive; empty for every cell that has none) ---
        # Cells whose feasible set is a thin sliver of the component box cannot
        # rely on a low-discrepancy multistart finding it -- the 3rd-order
        # band-pass cells need 1/(C3 R1) inside an interval whose relative width
        # is w0/(Q p1), down to ~4e-4. A cell module may therefore publish
        # closed-form starts; they are appended AFTER the legacy passes so every
        # root those passes already found is still found.
        _topo = _all_topos.get(name)
        if _topo is not None:
            for _sd in TF.analytic_seeds(_topo, design_str):
                _x0 = seed_to_ratio_x0(_sd, lay, lb_r, ub_r)
                if _x0 is not None:
                    p1_tasks.append({"topo_name": name, "mode": "ratio",
                                     "x0": _x0, "lb": lb_r, "ub": ub_r,
                                     "seed": True})

    if verbose:
        print(f"Synthesis v2 | {n_cores} cores | {len(topologies)} topologies | "
              f"f1={cfg['f1']} f0={cfg['f0']} fz={cfg['fz']}")
        print(f"  Phase 1: dispatching {len(p1_tasks)} starts...")

    # Hand the orchestrator-derived cases to workers via a per-run file they
    # only LOAD (derivation happens once, here; workers never derive or write a
    # cache). Removes the 32x-simultaneous fork-time derivation memory spike and
    # all cache races -- part of the "a process in the process pool was
    # terminated abruptly" (BrokenProcessPool) failure.
    _wcache = os.path.join(tempfile.gettempdir(),
                           f"fc_wcache_{os.getpid()}_{uuid.uuid4().hex}.json")
    # IDEAL-ONLY: Phase-1/Phase-3 workers only ever read cases[(name,"ideal")]
    # (see every cases[(...)] access below). The non-ideal TFs are used solely by
    # the snapper and nonideal_solver, which run in THIS (main) process off the
    # full `cases` dict. Serialising the non-ideal cases into the worker file was
    # pure waste -- and for 3rd-order AM the non-ideal TF is ~766k ops, so its
    # srepr is a ~20 MB blob that every worker rebuilt into a 766k-node SymPy tree
    # on load (RAM x n_cores + a huge temp file -> the memory blow-up). MFB/VCVS
    # non-ideal TFs are tiny, so they never hit this. Dumping ideal-only keeps the
    # worker file small and its parse instant, with zero behavioural change.
    _ideal_cases = {k: v for k, v in cases.items() if k[1] == "ideal"}
    TF.dump_cases(_ideal_cases, _wcache)

    # Shrink the parent heap BEFORE forking workers. A COLD solve just ran heavy
    # symbolic derivation (HP 3rd-order especially), leaving a large sympy cache
    # + garbage resident; forking N workers off that bloated parent is what
    # intermittently killed a worker on the FIRST solve, while a warm re-solve
    # (which only LOADS the cache) forks from a lean parent and succeeds.
    # Clearing sympy's cache + gc makes the cold fork lean too; the retry below
    # is the safety net (mirrors the user's manual "re-solve").
    def _shrink():
        try:
            sp.core.cache.clear_cache()
        except Exception:
            pass
        gc.collect()

    def _pool_phase(workers):
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                                 initargs=(design_str, cfg, topologies, k_map,
                                           _wcache)) as pool:
            t0 = time.time()
            p1_results = list(pool.map(phase1_worker, p1_tasks, chunksize=1))
            n_ok = sum(1 for r in p1_results if r)
            if verbose:
                print(f"  Phase 1: {n_ok}/{len(p1_tasks)} converged in {time.time()-t0:.1f}s")

            p3_tasks, vc, kept = harvest(p1_results, cases, cfg, max_valleys, hints_per_combo)

            # Build zero-manifold parallel-C2 tasks (one per (C2,C3,C4) combo)
            zm_tasks = []
            w0_t = 2*np.pi*cfg["f0"]; wz_t = 2*np.pi*cfg["fz"]
            for name in topologies:
                if name not in ZM.PARALLEL_C2_CELLS:
                    continue
                valleys = kept.get(name, [])
                if not valleys:
                    continue
                cands = ZM.build_candidates(cases[(name, "ideal")], valleys, cfg,
                                            max_c2_cands=max_c2_cands)
                tdc = ZM.target_dc_gain(cases[(name, "ideal")], cfg, dc_gain=dc_gain)
                for cap_map, c2rep in cands:
                    zm_tasks.append({"topo_name": name, "cap_map": cap_map,
                                     "c2rep": c2rep, "w0_t": w0_t, "wz_t": wz_t,
                                     "pole_tol": pole_tol, "gain_tol": gain_tol,
                                     "target_dc": tdc})

            if verbose:
                print(f"  Valleys: {vc}")
                print(f"  Phase 3: dispatching {len(p3_tasks)} cap combos"
                      + (f" + {len(zm_tasks)} zero-manifold parallel-C2 combos"
                         if zm_tasks else "") + "...")
            if not p3_tasks and not zm_tasks:
                return None                     # legitimately no solutions

            t0 = time.time()
            p3_raw = list(pool.map(phase3_worker, p3_tasks, chunksize=1)) if p3_tasks else []
            p3_results = []
            for r in p3_raw:                        # workers return a list (ladder) or None
                if r is None:
                    continue
                p3_results.extend(r if isinstance(r, list) else [r])
            zm_results = list(pool.map(zm_worker, zm_tasks, chunksize=1)) if zm_tasks else []
            res = p3_results + zm_results
            n_ok = sum(1 for r in res if r)
            if verbose:
                print(f"  Phase 3: {n_ok}/{len(p3_tasks)+len(zm_tasks)} solved in {time.time()-t0:.1f}s")
            return res

    try:
        _shrink()
        # Each worker independently lambdifies the (heavy, for 3rd-order/+R8)
        # analytic Jacobian at init; forking many such workers off a freshly
        # derived parent is what intermittently kills one ("a process in the
        # process pool was terminated abruptly"). A retry at the SAME width can
        # spike again, so each retry HALVES the worker count -- fewer concurrent
        # lambdify allocations -> less fork-time memory -- ending at 1 worker,
        # which is lean enough to always succeed. This is the automatic version
        # of the user's manual "re-solve", and it no longer surfaces as an error.
        schedule = []
        w = max(1, int(n_cores))
        while True:
            if not schedule or schedule[-1] != w:
                schedule.append(w)
            if w == 1:
                break
            w = max(1, w // 2)
        all_results = None
        last_exc = None
        for attempt, workers in enumerate(schedule):
            try:
                all_results = _pool_phase(workers)
                last_exc = None
                break
            except BrokenProcessPool as exc:
                last_exc = exc
                if verbose:
                    nxt = schedule[attempt+1] if attempt+1 < len(schedule) else None
                    print(f"  worker pool broke ({workers} workers, attempt "
                          f"{attempt+1}/{len(schedule)}); "
                          + (f"retrying with {nxt} workers..." if nxt
                             else "no further fallback."))
                _shrink()
                time.sleep(0.5)
        if last_exc is not None:
            raise last_exc
        if all_results is None:
            return []

        sols = dedup(all_results)
        sols.sort(key=lambda x: x["sens_score"])
        if verbose:
            print(f"Done. {len(sols)} unique solutions.")
        return sols
    finally:
        try:
            os.remove(_wcache)
        except OSError:
            pass


if __name__ == "__main__":
    cfg = {"f1":508.9,"f0":486.76,"fz":1668,"Q":1.0455,"K":680.75,
           "C_min":6.8e-5,"C_max":0.01,"R_min":3e-4,"R_max":2.0,"MAX_R_RATIO":18.0,
           "C_series":"E12","R_series":"E24, E48"}
    sols = run_synthesis(cfg, topologies=["3LPn-gained","2LPn-unity","2LP-unity"], n_cores=2,
                         ratio_starts=15, anchored_starts=15, max_valleys=3, hints_per_combo=2)
    for s in sols[:8]:
        print(f"{s['topology']:<14} sens={s['sens_score']:.2f} "
              f"C=[{s['C1']},{s['C2']},{s['C3']},{s['C4']}] R5={s['R5']}")
