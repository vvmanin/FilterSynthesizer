# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  zero_manifold_solver.py
#  Specialized Phase-3 path for ZERO-MANIFOLD topology cells that carry
#  a C2 capacitor: {3LPn-gained, 3LPn-unity, 2LPn-gained}.
#
#  PROBLEM (diagnosed empirically):
#  These cells are fully determined (free resistors == residuals). When C2
#  is snapped to a single E-series value, its quantization error (up to
#  ~10%) forces the resistors into extreme ratios (60+) and high
#  sensitivity (12-74). A commercial reference for the same topology/targets
#  achieves R-ratio ~1.5 and far lower sensitivity by using non-grid
#  (effectively continuous) capacitors.
#
#  SOLUTION (preserves "caps on E-series" ideology):
#  Give C2 fine granularity via a PARALLEL PAIR of E-series caps
#  (C2 = Ca || Cb = Ca + Cb). This yields ~10-15x finer C2 resolution
#  (e.g. 284 distinct sums vs 27 singles), so the nearest reachable C2 is
#  within <1% of optimal instead of ~10%. Resistors then stay
#  well-conditioned. Verified: sensitivity drops from ~13 to ~3.4, R-ratio
#  from 60+ to ~3.5.
#
#  POLE/ZERO TOLERANCE:
#  Among all reachable (C2-pair, C3, C4) combinations, resistors are solved
#  and we accept solutions whose pole frequencies (and notch, if it must
#  move) stay within +-1% of target. Within that tolerance band, results
#  are ranked by SENSITIVITY (lowest first) -- this is what steers the
#  search to the commercial-quality region. Single-C2 solutions that fall
#  inside the tolerance are included too.
#
#  STRUCTURE:
#  The per-combo solve is factored into solve_one_combo() so the SAME
#  validated logic runs both serially (solve_zero_manifold) and in parallel
#  (unified_solver_v2's zero-manifold worker). prep_cell_funcs() builds all
#  lambdified callables once; build_candidates() enumerates (C2,C3,C4)
#  combos from Phase-1 valleys.
# =====================================================================

import numpy as np
import sympy as sp
from itertools import product as iproduct
from scipy.optimize import least_squares

import tf_derivation_v2 as TF


# cells this path applies to (zero-manifold AND C2 present).
# Verified: manifold==0 and C2 in the component set for exactly these four.
# cells this path applies to (zero-manifold AND C2 present).
# NOTE: 3LPn-gained / 3LPn-unity are ALSO zero-manifold+C2, but were removed:
# their parallel-C2 search was too slow (~300s) and 3rd-order cells have
# ample single-capacitor solutions, so they do not need C2 paralleling. They
# fall through to the standard cap-snap Phase-3 like other cells.
# 2LPn-atten (unity SK + R7 input divider, sub-unity DC gain) is also a
# 2nd-order notch zero-manifold cell, so it gets the same parallel-C2 path.
PARALLEL_C2_CELLS = {"2LPn-gained", "2LPn-unity", "2LPn-atten"}


# =====================================================================
# E-series + parallel-pair C2 granularity
# =====================================================================
def _eseries_grid(series_str, c_min, c_max):
    E = {
        "E6":  [1.0, 1.5, 2.2, 3.3, 4.7, 6.8],
        "E12": [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2],
        "E24": [1.0,1.1,1.2,1.3,1.5,1.6,1.8,2.0,2.2,2.4,2.7,3.0,3.3,3.6,
                3.9,4.3,4.7,5.1,5.6,6.2,6.8,7.5,8.2,9.1],
    }
    base = set()
    for p in [x.strip().upper() for x in series_str.split(",")]:
        if p in E:
            base.update(E[p])
    arr = np.array(sorted(base))
    mult = [10**i for i in range(-6, 0)]
    grid = np.sort([round(v*m, 12) for m in mult for v in arr])
    return grid[(grid >= c_min*0.99) & (grid <= c_max*1.01)]


def _parallel_c2_values(grid, c_min, c_max):
    """All single values + parallel-pair sums (Ca+Cb), within [c_min,c_max].
    Returns sorted unique list of (value, representation) where representation
    is either ('single', Ca) or ('parallel', Ca, Cb)."""
    out = {}
    for v in grid:
        if c_min <= v <= c_max:
            out.setdefault(round(v, 12), ("single", float(v)))
    for i, a in enumerate(grid):
        for b in grid[i:]:                       # a <= b, avoid dup pairs
            ssum = a + b
            if c_min <= ssum <= c_max:
                key = round(ssum, 12)
                if key not in out:               # prefer single representation
                    out[key] = ("parallel", float(a), float(b))
    return sorted(out.items())                   # by value


def _nearest_c2(items, target, n=3):
    """Return up to n (value, repr) entries nearest to target C2."""
    vals = np.array([v for v, _ in items])
    idx = np.argsort(np.abs(vals - target))[:n]
    return [items[i] for i in idx]


# =====================================================================
# Shared per-cell preparation (lambdified callables built ONCE)
# =====================================================================
def prep_cell_funcs(case, design):
    """Build all lambdified functions a zero-manifold cell needs. Shared by
    the serial solver and the parallel worker so there is one code path.

    `design` substitutes p1,w0,wz,Q,K into the R5 constraint.
    Pole/notch use lambdified DENOMINATOR/NUMERATOR coefficient functions
    (fast np.roots) instead of per-call sympy expansion."""
    import unified_solver_v2 as S
    lay = S.cell_layout(case)
    vl = case["var_list"]
    s = TF.s
    res_f = sp.lambdify(vl, case["res_eqs"], "numpy", cse=True)
    a1_f = sp.lambdify(vl, case["a1_expr"], "numpy", cse=True)
    a2_f = sp.lambdify(vl, case["a2_expr"], "numpy", cse=True)
    r5_f = (sp.lambdify(vl, case["R5_constraint"].subs(design), "numpy", cse=True)
            if case.get("R5_constraint") is not None else None)
    # Analytic Jacobian of the residual vector w.r.t. ALL vars (caps+resistors).
    # solve_one_combo varies only resistors, so it slices the resistor columns.
    jac_f = sp.lambdify(vl, sp.Matrix(case["res_eqs"]).jacobian(list(vl)),
                        "numpy", cse=True)
    _nidx = {str(v): i for i, v in enumerate(vl)}
    res_cols = [_nidx[n] for n in lay["res_names"]]
    tfvars = case["tf_var_list"]
    den_cf = [sp.lambdify(tfvars, c, "numpy", cse=True)
              for c in sp.Poly(case["tf_den"], s).all_coeffs()]
    num_cf = [sp.lambdify(tfvars, c, "numpy", cse=True)
              for c in sp.Poly(case["tf_num"], s).all_coeffs()]
    return {
        "name": TF.topo_name(case["topo"]),
        "layout": lay,
        "cap_names": lay["cap_names"],
        "res_names": lay["res_names"],
        "var_names": lay["names"],
        "tf_var_names": [str(v) for v in tfvars],
        "res_f": res_f, "a1_f": a1_f, "a2_f": a2_f, "r5_f": r5_f,
        "jac_f": jac_f, "res_cols": res_cols,
        "den_cf": den_cf, "num_cf": num_cf,
        "has_notch": bool(case["topo"]["notch"]),
    }


def _roots_fast(funcs, full):
    args = [full[n] for n in funcs["tf_var_names"]]
    try:
        dc = [complex(f(*args)) for f in funcs["den_cf"]]
        nc = [complex(f(*args)) for f in funcs["num_cf"]]
    except Exception:
        return None, None
    poles = np.roots(dc) if len(dc) > 1 else np.array([])
    zeros = np.roots(nc) if len(nc) > 1 else np.array([])
    return poles, zeros


def _dominant_w0(poles):
    """Natural frequency of the complex pole pair (the 2nd-order section)."""
    cpx = [p for p in poles if abs(p.imag) > 1e-6*abs(p.real)]
    if cpx:
        return abs(cpx[0])
    return abs(poles[np.argmax(np.abs(poles))]) if len(poles) else 0.0


def _notch_w(zeros):
    """Frequency of the jw-axis zero pair (notch), if present."""
    jw = [z for z in zeros if abs(z.real) < 1e-3*abs(z.imag) and abs(z.imag) > 1]
    if jw:
        return abs(jw[0])
    fin = [z for z in zeros if abs(z) > 1]
    return abs(fin[0]) if fin else None


# =====================================================================
# Candidate enumeration from Phase-1 valleys
# =====================================================================
def build_candidates(case, valleys, cfg, max_c2_cands=4):
    """From continuous Phase-1 valleys, build a deduped list of
    (cap_map, c2_repr) candidates. C2 gets fine parallel-pair granularity;
    all OTHER caps present in the cell (C1 for 3rd-order, C3, C4) are snapped
    to their two nearest E-series values. cap_map always contains every cap
    in the cell's layout."""
    import unified_solver_v2 as S
    grid = _eseries_grid(cfg["C_series"], cfg["C_min"], cfg["C_max"])
    c2_items = _parallel_c2_values(grid, cfg["C_min"], cfg["C_max"])
    lay = S.cell_layout(case)
    cap_names = lay["cap_names"]
    other_caps = [c for c in cap_names if c != "C2"]   # C1?, C3, C4

    # enumerate E-series combos for the non-C2 caps from each valley
    other_combos = set()
    for v in valleys:
        caps = v.get("caps", v)
        if any(caps.get(c) is None for c in other_caps):
            continue
        per_cap = [S.two_nearest(caps[c], grid) for c in other_caps]
        for combo in iproduct(*per_cap):
            other_combos.add(tuple(round(x, 12) for x in combo))
    c2_targets = sorted({round(v.get("caps", v).get("C2", 0), 12)
                         for v in valleys if v.get("caps", v).get("C2")})

    cands = []
    seen = set()
    for ocombo in other_combos:
        ocap = {n: val for n, val in zip(other_caps, ocombo)}
        for t in c2_targets:
            for (c2val, c2rep) in _nearest_c2(c2_items, t, n=max_c2_cands):
                cap_map = dict(ocap); cap_map["C2"] = c2val
                k = tuple(sorted((n, round(v, 12)) for n, v in cap_map.items()))
                if k in seen:
                    continue
                seen.add(k)
                cands.append((cap_map, c2rep))
    return cands


# =====================================================================
# The per-combo solve (ONE implementation; serial + parallel both call it)
# =====================================================================
def solve_one_combo(funcs, cap_map, c2rep, cfg, w0_t, wz_t, pole_tol, weyl_fn,
                    gain_tol=0.01, target_dc=None):
    """Solve resistors for one (C2,C3,C4) combo, gate on bounds/ratio,
    pole/notch tolerance, AND DC-gain tolerance, score by sensitivity.
    Returns a solution dict (with C2a/C2b parallel representation) or None.

    target_dc : required DC gain |H(0)|. For gained cells this is
                K * wz^2 / w0^2 (the isolated-gain-constant form); for unity
                cells it is 1.0. When the +-pole_tol shift moves wz/w0, the
                DC gain = K*wz^2/w0^2 drifts, so it must be re-checked here
                against gain_tol (default +-1%)."""
    cap_names = funcs["cap_names"]; res_names = funcs["res_names"]
    res_f = funcs["res_f"]; r5_f = funcs["r5_f"]
    a1_f = funcs["a1_f"]; a2_f = funcs["a2_f"]
    jac_f = funcs["jac_f"]; res_cols = funcs["res_cols"]
    cv = [cap_map[n] for n in cap_names]

    def obj(x):
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            return np.array(res_f(*(cv + list(x))), dtype=float)

    def jacf(x):
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            J = np.asarray(jac_f(*(cv + list(x))), dtype=float)
            return J[:, res_cols]            # residuals x free-resistors

    lb3 = np.array([cfg["R_min"]]*len(res_names))
    ub3 = np.array([cfg["R_max"]]*len(res_names))

    best_r, best_c = None, 1e100
    starts = [np.full(len(res_names), 0.05),
              np.full(len(res_names), 0.02),
              np.full(len(res_names), 0.1)] + list(weyl_fn(5, lb3, ub3))
    for x0 in starts:
        x0 = np.clip(np.asarray(x0, float), lb3+1e-12, ub3-1e-12)
        try:
            f0 = obj(x0)
            if not np.all(np.isfinite(f0)):
                continue
            r = least_squares(obj, x0, jac=jacf, bounds=(lb3, ub3),
                              xtol=1e-11, ftol=1e-11, max_nfev=500)
        except Exception:
            continue
        cc = float(np.sum(np.array(r.fun)**2))
        if cc < best_c:
            best_c, best_r = cc, r
    if best_r is None:
        return None

    full = dict(cap_map)
    for n, val in zip(res_names, best_r.x):
        full[n] = float(val)
    if r5_f is not None:
        full["R5"] = float(r5_f(*[full[n] for n in funcs["var_names"]]))

    # bounds + ratio gate
    allR = [v for k, v in full.items() if k.startswith("R")]
    if not allR or min(allR) < cfg["R_min"] or max(allR) > cfg["R_max"]:
        return None
    core = [full[k] for k in ["R2", "R3", "R4"] if k in full]
    if len(core) >= 2 and max(core)/min(core) > cfg["MAX_R_RATIO"]:
        return None

    # achieved pole / notch within tolerance (residual need not be ~0 for
    # a zero-manifold cell after cap quantization)
    poles, zeros = _roots_fast(funcs, full)
    if poles is None or len(poles) == 0:
        return None
    w0_a = _dominant_w0(poles)
    if w0_a <= 0 or abs(w0_a/w0_t - 1.0) > pole_tol:
        return None
    if funcs["has_notch"]:
        wz_a = _notch_w(zeros)
        if wz_a is not None and abs(wz_a/wz_t - 1.0) > pole_tol:
            return None

    # DC-gain tolerance gate. Achieved DC gain = |num(0)/den(0)|, computed
    # from the constant (s^0) coefficients of the TF. For gained cells this
    # captures the K*wz^2/w0^2 drift that the +-pole_tol shift introduces.
    dc_dev_pct = 0.0
    if target_dc is not None:
        args = [full[n] for n in funcs["tf_var_names"]]
        try:
            num0 = complex(funcs["num_cf"][-1](*args))
            den0 = complex(funcs["den_cf"][-1](*args))
            dc_a = abs(num0/den0) if den0 != 0 else None
        except Exception:
            dc_a = None
        if dc_a is None or dc_a <= 0:
            return None
        if abs(dc_a/target_dc - 1.0) > gain_tol:
            return None
        dc_dev_pct = 100*(dc_a/target_dc - 1.0)

    # sensitivity (relative dC of monic denominator coeffs)
    va = cv + [full[n] for n in res_names]
    a1b = a1_f(*va); a2b = a2_f(*va); ss = 0.0
    for j in range(len(va)):
        p = list(va); p[j] *= 1.01
        ss += ((a1_f(*p)-a1b)/a1b/0.01)**2 + ((a2_f(*p)-a2b)/a2b/0.01)**2
    sens = float(np.sqrt(ss))

    sol = {"topology": funcs["name"], "sens_score": sens,
           "internal_gain": 1.0, "cost": best_c,
           "pole_dev_pct": 100*(w0_a/w0_t - 1.0),
           "dc_gain_dev_pct": dc_dev_pct}
    for k in ["C1","C2","C3","C4","R1","R2","R3","R4","R5","R6","R7","R8"]:
        sol[k] = full.get(k, None if k in ("R5","R7","R8","C1") else 0.0)
    if c2rep[0] == "parallel":
        sol["C2a"] = c2rep[1]; sol["C2b"] = c2rep[2]; sol["C2_parallel"] = True
    else:
        sol["C2a"] = c2rep[1]; sol["C2b"] = None; sol["C2_parallel"] = False
    return sol


def target_dc_gain(case, cfg, dc_gain=None):
    """Required DC gain |H(0)| for tolerance checking.
    Pure unity cells are structurally 1.0. The attenuator (unity + R7 divider)
    instead carries a SUB-UNITY target, so it must NOT short-circuit to 1.0 —
    it falls through to the dc_gain value (DC-gain mode) or the K-based form
    (cfg["K"]*wz^2/w0^2 = the math DC gain). Gained cells use the same."""
    topo = case["topo"]
    if topo["gain"] == "unity" and not topo.get("has_R7"):
        return 1.0
    if dc_gain is not None:
        return float(dc_gain)
    K = cfg.get("K", 1.0)
    w0 = 2*np.pi*cfg["f0"]; wz = 2*np.pi*cfg["fz"]
    return abs(K) * (wz**2) / (w0**2)


# =====================================================================
# Serial wrapper (standalone use / testing)
# =====================================================================
def solve_zero_manifold(case, cfg, valleys, opamp=None,
                        pole_tol=0.01, gain_tol=0.01, max_c2_cands=4,
                        verbose=False):
    """Serial driver: prep funcs, enumerate candidates, solve each combo.
    Returns solutions ranked by sensitivity (lowest first)."""
    import unified_solver_v2 as S
    name = TF.topo_name(case["topo"])
    assert "C2" in S.cell_layout(case)["cap_names"], f"{name} has no C2"

    design = {TF.p1: 2*np.pi*cfg["f1"], TF.w0: 2*np.pi*cfg["f0"],
              TF.wz: 2*np.pi*cfg["fz"], TF.Q: cfg["Q"], TF.K: cfg.get("K", 1.0)}
    funcs = prep_cell_funcs(case, design)
    w0_t = 2*np.pi*cfg["f0"]; wz_t = 2*np.pi*cfg["fz"]
    tdc = target_dc_gain(case, cfg)
    cands = build_candidates(case, valleys, cfg, max_c2_cands)

    out = []; seen = set()
    for cap_map, c2rep in cands:
        sol = solve_one_combo(funcs, cap_map, c2rep, cfg, w0_t, wz_t,
                              pole_tol, S.weyl_starts,
                              gain_tol=gain_tol, target_dc=tdc)
        if sol is None:
            continue
        key = (name, tuple(sorted((n, round(cap_map[n], 12)) for n in cap_map)),
               tuple(round(sol[r], 7) for r in funcs["res_names"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(sol)

    out.sort(key=lambda s: s["sens_score"])
    if verbose:
        if out:
            print(f"  [{name}] zero-manifold parallel-C2: {len(out)} solutions "
                  f"(best sens {out[0]['sens_score']:.2f})")
        else:
            print(f"  [{name}] zero-manifold parallel-C2: no solutions")
    return out
