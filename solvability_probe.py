# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  solvability_probe.py  --  global (idealised) feasibility assessment
# ---------------------------------------------------------------------
#  Runs ONLY on the failure path (stage-1 returned no realization inside
#  the user's component envelope). It answers a cheaper, scale-free
#  question on the SAME residual system every cell already exposes:
#
#      "Does a positive component vector zero the ideal residuals with
#       both the resistor spread and the capacitor spread <= N decades?"
#
#  It is topology-agnostic: it consumes only `res_eqs` / `var_list` from
#  tf_derivation_v2.derive_ideal(topo, subs), so every current cell -- and
#  any future cell that registers a family module -- works with no change
#  here.
#
#  Key facts it leans on
#  ---------------------
#  * Impedance scaling (R->aR, C->C/a) leaves the transfer function
#    unchanged, so absolute R/C levels are almost never the obstacle --
#    only the dimensionless SPREADS are. We therefore remove the scale
#    degree of freedom by centring both windows on sqrt(1/w0) (which makes
#    R*C ~ 1/w0 automatically) and bound each component to an N-decade
#    window, so feasibility within the box == "a solution with <= N-decade
#    spread exists".
#  * Infeasibility shows up as the residual being un-zeroable inside that
#    box (the VCVS gained-notch drives gain resistors -> 0; the MFB needs
#    ~10-12 decade spreads). Both are caught by the bounded probe.
#
#  Verdicts (aggregated over the family's cells)
#  ---------------------------------------------
#    FEASIBLE_GLOBAL      a cell realises within N decades -> the user's
#                         envelope is merely too tight (YELLOW). We report
#                         the spreads the easiest cell actually needs.
#    INFEASIBLE_GAIN      no cell realises with the gain constraint, but
#                         some cell realises once the gain residual is
#                         dropped -> the gain is the obstruction (RED).
#    INFEASIBLE_STRUCTURAL no cell realises even with gain relaxed -> the
#                         target shape itself is unrealisable here (RED).
# =====================================================================
import numpy as np
import sympy as sp
from scipy.optimize import least_squares

import tf_derivation_v2 as TF
from tf_derivation_v2 import p1, w0, wz, Q, K

# feasibility = every (relative) residual driven essentially to zero
_FEAS_SSE = 1e-9          # sum of squared residuals below this == a root
_DFLT_DECADES = 5.0
_DFLT_STARTS = 8
_MAX_NFEV = 250


# ---------------------------------------------------------------------
def _name2topo():
    return {TF.topo_name(t): t for t in TF.all_cells()}


def _classify(var_list):
    """Split free symbols into caps / resistors by the (universal) C*/R*
    naming used by every cell module."""
    caps = [v for v in var_list if str(v)[:1] == "C"]
    res = [v for v in var_list if str(v)[:1] == "R"]
    return caps, res


def _base_subs(cfg):
    """Target substitutions WITHOUT K, so the gain residual keeps K symbolic
    (lets us identify and optionally drop it)."""
    return {p1: 2 * np.pi * cfg["f1"], w0: 2 * np.pi * cfg["f0"],
            wz: 2 * np.pi * cfg["fz"], Q: cfg["Q"]}


def _k_target(topo, cfg, dc_gain):
    if dc_gain is None:
        return float(cfg.get("K", 1.0))
    design = {**_base_subs(cfg), K: cfg.get("K", 1.0)}
    kv = TF.dc_gain_to_K(topo, design, dc_gain)
    return float(kv) if kv is not None else float(cfg.get("K", 1.0))


# ---------------------------------------------------------------------
def _bounded_rootfind(res_exprs, var_list, center, decades, n_starts, seed):
    """Multistart bounded log-space least-squares on `res_exprs`. Returns
    (best_sse, best_values_array). Bounds give each component an
    `decades`-wide window centred on `center`, so any root found has both
    group spreads <= `decades`."""
    if not res_exprs:                       # nothing to satisfy -> trivially feasible
        return 0.0, np.full(len(var_list), center)
    n = len(var_list)
    half = 0.5 * decades * np.log(10.0)
    lo, hi = np.log(center) - half, np.log(center) + half
    lb, ub = np.full(n, lo), np.full(n, hi)
    f = sp.lambdify(var_list, res_exprs, "numpy")
    m = len(res_exprs)

    def fun(lg):
        v = np.exp(lg)
        try:
            o = np.asarray(f(*v), dtype=float).ravel()
            return o if (o.size == m and np.all(np.isfinite(o))) else np.full(m, 1e3)
        except Exception:
            return np.full(m, 1e3)

    rng = np.random.default_rng(seed)
    best = (np.inf, None)
    for _ in range(n_starts):
        x0 = rng.uniform(lo, hi, n)
        try:
            r = least_squares(fun, x0, bounds=(lb, ub), method="trf",
                              max_nfev=_MAX_NFEV)
        except Exception:
            continue
        sse = float(np.sum(r.fun ** 2))
        if sse < best[0]:
            best = (sse, np.exp(r.x))
        if sse < _FEAS_SSE:                 # found a clean root -> stop early
            break
    return best


def _feas_box(f_full, n, m, keep, Kt, center, decades, n_starts, seed, max_nfev=_MAX_NFEV):
    """Feasibility of a residual SUBSET inside the decades-box, in log space,
    reusing residuals `f_full` lambdified ONCE with K appended. `keep` selects
    which of the m residuals must be zeroed (all of them for the with-gain and
    band checks; the non-gain subset for the shape-only check). The K argument is
    a dummy for the shape-only subset (those residuals carry no K). Returns
    (feasible, best_sse, best_values_or_None)."""
    keep = list(keep); mk = len(keep)
    half = 0.5 * decades * np.log(10.0)
    lo, hi = np.log(center) - half, np.log(center) + half
    lb, ub = np.full(n, lo), np.full(n, hi)
    rng = np.random.default_rng(seed)

    def fun(lg):
        v = np.exp(lg)
        try:
            o = np.asarray(f_full(*v, Kt), dtype=float).ravel()
            if o.size != m or not np.all(np.isfinite(o)):
                return np.full(mk, 1e3)
            return o[keep]
        except Exception:
            return np.full(mk, 1e3)

    best = (np.inf, None)
    for _ in range(n_starts):
        x0 = rng.uniform(lo, hi, n)
        try:
            r = least_squares(fun, x0, bounds=(lb, ub), method="trf", max_nfev=max_nfev)
            sse = float(np.sum(r.fun ** 2))
            if sse < best[0]:
                best = (sse, np.exp(r.x))
            if sse < _FEAS_SSE:
                break
        except Exception:
            pass
    return best[0] < _FEAS_SSE, best[0], best[1]


def _spreads(values, var_list):
    caps, res = _classify(var_list)
    idx = {str(v): i for i, v in enumerate(var_list)}
    def spread(group):
        if len(group) < 2:
            return 1.0
        vals = np.array([values[idx[str(g)]] for g in group])
        vals = vals[vals > 0]
        return float(vals.max() / vals.min()) if vals.size else 1.0
    return spread(res), spread(caps)



# ---------------------------------------------------------------------
def probe_cell(topo, cfg, dc_gain, decades=_DFLT_DECADES,
               n_starts=_DFLT_STARTS, seed=0):
    """Per-cell verdict. Returns dict with:
        feasible_with_gain : full system (pole/zero + target gain) realises
        feasible_no_gain   : pole/zero/notch realises with gain UNCONSTRAINED
        r_spread,c_spread  : (feasible_with_gain) realised component spreads
        g_natural          : (feasible_no_gain) a gain this topology CAN realise
                             for this shape -- a guaranteed-feasible gain target
    """
    d = TF.derive_ideal(topo, _base_subs(cfg))        # K left symbolic
    res = list(d["res_eqs"])
    vl = list(d["var_list"])
    w0_val = float(2 * np.pi * cfg["f0"])
    center = float(np.sqrt(1.0 / w0_val))             # scale-consistent window centre

    gain_idx = [i for i, e in enumerate(res)
                if isinstance(e, sp.Expr) and K in e.free_symbols]
    Kt = _k_target(topo, cfg, dc_gain)

    # (1) full system at the target gain
    res_full = [e.subs({K: Kt}) if isinstance(e, sp.Expr) else e for e in res]
    sse_g, val_g = _bounded_rootfind(res_full, vl, center, decades, n_starts, seed)
    feas_g = sse_g < _FEAS_SSE

    out = {"name": TF.topo_name(topo), "feasible_with_gain": feas_g,
           "feasible_no_gain": feas_g, "sse_with_gain": sse_g, "k_target": Kt}
    if feas_g:
        out["r_spread"], out["c_spread"] = _spreads(val_g, vl)
        return out

    # (2) gain unconstrained: drop the gain residual -> can the SHAPE be placed?
    if not gain_idx:                                  # no separable gain residual
        out["feasible_no_gain"] = False               # nothing to relax -> structural
        return out
    res_nog = [e for i, e in enumerate(res) if i not in gain_idx]
    sse_ng, val_ng = _bounded_rootfind(res_nog, vl, center, decades, n_starts, seed)
    feas_ng = sse_ng < _FEAS_SSE
    out["feasible_no_gain"] = feas_ng
    if feas_ng:
        # gain the topology actually produces for this realised shape:
        # gain_res = (G_expr - K)/K  ->  at K=1, G_expr = gain_res(.,K=1) + 1
        gres = res[gain_idx[0]]
        gfun = sp.lambdify(list(vl) + [K], gres, "numpy")
        try:
            out["g_natural"] = float(gfun(*val_ng, 1.0)) + 1.0
        except Exception:
            out["g_natural"] = None
    return out


def _gain_ladder(target_u):
    """Geometric gain ladder (user units) that brackets both the plausible
    reachable band and the target, so the band edges and the target's position
    relative to them are both resolved. Kept short (the failure-path probe is
    time-boxed); the edges are refined only to ladder granularity, which the
    interior-band tolerance in assess() accounts for."""
    lo = min(0.1, target_u / 4.0) if target_u else 0.1
    hi = max(20.0, target_u * 4.0) if target_u else 20.0
    return list(np.geomspace(lo, hi, 9))


def assess(topo_names, cfg, dc_gain=None, decades=_DFLT_DECADES,
           n_starts=_DFLT_STARTS, seed=0):
    """Family-level verdict aggregated over the attempted cells.

    verdict is one of:
      'FEASIBLE_GLOBAL'        a cell realises shape+gain within N decades of
                               spread -> the user's envelope is merely too tight
                               (or, if those spreads already fit the envelope,
                               the search missed it). Carries need_r_ratio /
                               need_c_ratio / cell.
      'REALIZABLE_NEEDS_SEARCH' the target gain lies INSIDE the reachable gain
                               band for this shape (within the spread budget) but
                               the bounded probe didn't close it at the target
                               exactly -> a realisation almost certainly exists
                               and the obstruction is search effort / spread, NOT
                               the gain. Carries band=(g_lo,g_hi) / target_gain.
      'INFEASIBLE_GAIN'        the target gain is OUTSIDE the reachable band; the
                               nearest band EDGE (a real feasibility boundary, not
                               an arbitrary point) is reported as reach_gain, with
                               band=(g_lo,g_hi) and direction.
      'INFEASIBLE_STRUCTURAL'  the pole/zero shape itself isn't placeable here.
    All gains are in USER units (HF gain for HP, DC gain for LP, raw const BP).
    """
    n2t = _name2topo()
    cells = [n2t[n] for n in topo_names if n in n2t]
    if not cells:                                     # unrecognised topo set -> no opinion
        return {"verdict": "UNKNOWN", "decades": decades}

    feas_global = []            # (max_spread, r_spread, c_spread, name)
    shape_ok = []               # (topo, f_full, vl, n, m, center) -- shape reachable
    for t in cells:
        # Derive + lambdify ONCE per cell, then reuse for the with-gain check,
        # the shape-only check, AND the gain-band ladder (no redundant symbolic
        # work -- the HP 3rd-order derivation is the dominant cost and this keeps
        # the failure-path probe inside its time box).
        try:
            d = TF.derive_ideal(t, _base_subs(cfg))
        except Exception:
            continue
        res = list(d["res_eqs"]); vl = list(d["var_list"])
        n, m = len(vl), len(res)
        gain_idx = [i for i, e in enumerate(res)
                    if isinstance(e, sp.Expr) and K in e.free_symbols]
        try:
            f_full = sp.lambdify(list(vl) + [K], res, "numpy")
        except Exception:
            continue
        w0_val = float(2 * np.pi * cfg["f0"]); center = float(np.sqrt(1.0 / w0_val))
        Kt = _k_target(t, cfg, dc_gain)

        feas_g, _, val_g = _feas_box(f_full, n, m, range(m), Kt,
                                     center, decades, n_starts, seed)
        if feas_g:
            rs, cs = _spreads(val_g, vl)
            feas_global.append((max(rs, cs), rs, cs, TF.topo_name(t)))
            continue
        if not gain_idx:                              # no separable gain residual
            continue
        keep = [i for i in range(m) if i not in gain_idx]
        feas_ng, _, _ = _feas_box(f_full, n, m, keep, Kt,
                                  center, decades, n_starts, seed)
        if feas_ng:
            shape_ok.append((t, f_full, vl, n, m, center))

    if feas_global:
        feas_global.sort()                            # easiest (smallest spread) first
        _, rsp, csp, name = feas_global[0]
        return {"verdict": "FEASIBLE_GLOBAL", "decades": decades,
                "need_r_ratio": rsp, "need_c_ratio": csp, "cell": name}

    if not shape_ok:
        return {"verdict": "INFEASIBLE_STRUCTURAL", "decades": decades}

    # Shape is reachable but the gain residual didn't close at the target for any
    # cell. Find the REACHABLE GAIN BAND (union over the shape-feasible cells) by
    # laddering feasibility in gain (reusing each cell's lambdified residuals),
    # then classify the target's position honestly.
    target_u = abs(float(dc_gain)) if dc_gain is not None else abs(float(cfg.get("K", 1.0)))
    ladder = _gain_ladder(target_u)
    band_starts = min(n_starts, 5)        # feasibility detection needs few starts
    band_lo, band_hi, cell = None, None, None
    for (t, f_full, vl, n, m, center) in shape_ok:
        feas = []
        for gu in ladder:
            try:
                Kt = float(TF.dc_gain_to_K(t, _base_subs(cfg), gu))
            except Exception:
                continue
            ok, _, _ = _feas_box(f_full, n, m, range(m), Kt,
                                 center, decades, band_starts, seed, max_nfev=150)
            if ok:
                feas.append(gu)
        if not feas:
            continue
        lo, hi = min(feas), max(feas)
        if band_lo is None or lo < band_lo:
            band_lo = lo
        if band_hi is None or hi > band_hi:
            band_hi = hi
        cell = TF.topo_name(t)

    if band_lo is None:
        # Shape placeable but no gain on the ladder closed -> treat as structural
        # (the gain residual is not separately satisfiable here).
        return {"verdict": "INFEASIBLE_STRUCTURAL", "decades": decades}

    # Target inside the reachable band (with a small tolerance for the ladder
    # granularity) -> the gain is NOT the obstruction; the search/spread is.
    if band_lo * 0.97 <= target_u <= band_hi * 1.03:
        return {"verdict": "REALIZABLE_NEEDS_SEARCH", "decades": decades,
                "band": (band_lo, band_hi), "target_gain": target_u, "cell": cell}

    edge = band_lo if target_u < band_lo else band_hi
    direction = "higher" if target_u < band_lo else "lower"
    return {"verdict": "INFEASIBLE_GAIN", "decades": decades,
            "target_gain": target_u, "reach_gain": edge, "band": (band_lo, band_hi),
            "direction": direction, "cell": cell}
