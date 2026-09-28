# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028 probe: Phase 1 as ONE vectorized solve instead of N process-pool tasks.

Builds exactly the Phase-1 start set run_synthesis builds (ratio + wide +
anchored + analytic seeds, per cell), then solves it three ways:

  trf    scipy bounded TRF per start (the production phase1_worker)
  lm     projected log-space LM per start (lm_core.lm_log)
  batch  all starts of a (cell, mode) group in one batch_lm_log call

and applies phase1_worker's acceptance (cost gate, RC rescale, envelope check)
to each. Reports single-core CPU, converged starts and distinct valleys
(harvest's 8 % cap-vector dedup).

    python dev/fs028/probe_batch.py LPn3-VCVS LPn3-MFB --preset Balanced
"""
import argparse
import contextlib
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fs028_common as C          # noqa: E402
import lm_core                    # noqa: E402


MAX_ITER = 100


def build_problem(sec, fam, topos, dct, preset):
    """cfg, cases and the Phase-1 start groups, as run_synthesis builds them."""
    import unified_solver_v2 as US
    import tf_derivation_v2 as TF
    import cells_mfb_hp
    p = C.PROBE_PRESETS[preset]
    cfg = C.build_cfg(sec)
    if fam == "AM":
        cfg["equalize_rc"] = True
    cfg = {**cfg, "gain_tol": C.CONV_DEFAULT["gain_tol"]}
    design = {TF.p1: 2 * np.pi * cfg["f1"], TF.w0: 2 * np.pi * cfg["f0"],
              TF.wz: 2 * np.pi * cfg["fz"], TF.Q: cfg["Q"], TF.K: cfg.get("K", 1.0)}
    design_str = {str(k): float(v) for k, v in design.items()}
    avail = {TF.topo_name(t) for t in TF.all_cells()}
    topologies = []
    for name in topos:
        topologies.append(name)
        if name.endswith("-gained") and "LPn" in name and name + "+R7" in avail:
            topologies.append(name + "+R7")
    k_map = None
    n2t = {TF.topo_name(t): t for t in TF.all_cells()}
    if dct is not None:
        def _cell_dc(topo):
            if (topo.get("family") == "HP-MFB" and topo.get("notch")
                    and topo.get("v2") and topo.get("order") == 3):
                fl = cells_mfb_hp.mfb2_v2_gain_floor(cfg)
                g = abs(float(dct))
                return g if g >= fl else fl
            return dct
        k_map = {nm: TF.dc_gain_to_K(n2t[nm], design, _cell_dc(n2t[nm])) for nm in topologies}
    with contextlib.redirect_stdout(open(os.devnull, "w")):
        cases = TF.get_cases(design, verbose=False, k_map=k_map, topo_names=topologies)
    cases = US.apply_equalize(cases, cfg)
    groups = []
    for name in topologies:
        lay = US.cell_layout(cases[(name, "ideal")])
        lb_r, ub_r = US.ratio_bounds(lay, cfg)
        groups.append((name, "ratio", np.array(US.weyl_starts(p["ratio_starts"], lb_r, ub_r)), lb_r, ub_r))
        lb_w, ub_w = US.ratio_bounds(lay, cfg, wide=True)
        n_wide = max(8, p["ratio_starts"] // 2)
        groups.append((name, "wide", np.array(US.loguniform_starts(n_wide, lb_w, ub_w)), lb_w, ub_w))
        lb_a, ub_a, _anc, _fc = US.anchored_bounds(lay, cfg)
        groups.append((name, "anchored", np.array(US.weyl_starts(p["anchored_starts"], lb_a, ub_a)), lb_a, ub_a))
        seeds = []
        for sd in TF.analytic_seeds(n2t[name], design_str):
            x0 = US.seed_to_ratio_x0(sd, lay, lb_r, ub_r)
            if x0 is not None:
                seeds.append(x0)
        if seeds:
            groups.append((name, "seed", np.array(seeds), lb_r, ub_r))
    return cfg, design, cases, groups


def accept(name, mode, x, cost, cfg, lay, anchor=None, free_caps=None):
    """phase1_worker's acceptance; returns the cap dict or None."""
    nC = lay["n_caps"]
    if mode in ("ratio", "wide", "seed"):
        if not cost < 1e-5:
            return None
        c = x[:nC]; rr = x[nC:]
        gamma = cfg["C_max"] / np.max(c)
        cp = c * gamma; rp = rr / gamma
        if min(cp) < cfg["C_min"] * 0.95 or max(rp) > cfg["R_max"] * 1.5:
            return None
        return dict(zip(lay["cap_names"], cp))
    if not cost < 1e-6:
        return None
    fc = dict(zip(free_caps, x[:len(free_caps)]))
    fc[anchor] = cfg["C_max"]
    return {c: fc[c] for c in lay["cap_names"]}


def distinct(capsets, rtol=0.08):
    per = {}
    for name, caps in capsets:
        cv = np.array([caps[k] for k in sorted(caps)])
        lst = per.setdefault(name, [])
        if not any(np.allclose(cv, e, rtol=rtol) for e in lst):
            lst.append(cv)
    return {k: len(v) for k, v in per.items()}


def run(cid, preset, methods):
    import unified_solver_v2 as US
    from scipy.optimize import least_squares
    case = [c for c in C.bench_cases() if c[0] == cid][0]
    _, sec, fam, topos, dct = case
    with C.workdir(os.path.join(C.WORK_DIR, f"b{os.getpid()}")):
        cfg, design, cases, groups = build_problem(sec, fam, topos, dct, preset)
    out = {}
    for meth in methods:
        t_setup = 0.0; t_solve = 0.0; ok = []; n_starts = 0; per_mode = {}
        for name, mode, X0, lb, ub in groups:
            case_i = cases[(name, "ideal")]
            lay = US.cell_layout(case_i)
            anchor = free_caps = None
            vn = lay["names"]
            if mode == "anchored":
                lb_a, ub_a, anchor, free_caps = US.anchored_bounds(lay, cfg)
            t = time.perf_counter()
            res_b, jac_b = lm_core.batch_funcs(case_i)
            t_setup += time.perf_counter() - t
            if mode == "anchored":
                aidx = vn.index(anchor)
                free_idx = [vn.index(c) for c in free_caps] + [vn.index(r) for r in lay["res_names"]]

                def full(Xf, aidx=aidx, free_idx=free_idx):
                    Xfull = np.empty((Xf.shape[0], len(vn)))
                    Xfull[:, free_idx] = Xf
                    Xfull[:, aidx] = cfg["C_max"]
                    return Xfull
                rb = (lambda Xf, f=full, r=res_b: r(f(Xf)))
                jb = (lambda Xf, f=full, j=jac_b, fi=free_idx: j(f(Xf))[:, :, fi])
            else:
                rb, jb = res_b, jac_b
            t = time.perf_counter()
            if meth == "batch":
                X, Cc, conv, it, nev = lm_core.batch_lm_log(rb, jb, X0, tol=1e-20,
                                                            max_iter=MAX_ITER, lb=lb, ub=ub)
                # phase1_worker demands r.success: an iteration-capped start is rejected
                results = [(X[i], Cc[i] if conv[i] else np.inf) for i in range(len(X0))]
            else:
                results = []
                for x0 in X0:
                    f = (lambda x, rb=rb: rb(x[None, :])[0])
                    jf = (lambda x, jb=jb: jb(x[None, :])[0])
                    if meth == "lm":
                        x, r, nf, nj, cv, st = lm_core.lm_log(f, jf, x0, tol=1e-20,
                                                              max_iter=MAX_ITER, lb=lb, ub=ub)
                        results.append((x, float(r @ r) if cv else np.inf))
                    else:
                        tol = 1e-11 if mode == "anchored" else 1e-10
                        try:
                            r = least_squares(f, np.clip(x0, lb, ub), jac=jf, bounds=(lb, ub),
                                              method="trf", xtol=tol, ftol=tol, max_nfev=700)
                            results.append((r.x, float(np.sum(r.fun ** 2)) if r.success else np.inf))
                        except Exception:
                            results.append((x0, np.inf))
            t_solve += time.perf_counter() - t
            n_starts += len(X0)
            got = [(name, acc) for x, c in results
                   if (acc := accept(name, mode, np.asarray(x), c, cfg, lay, anchor, free_caps))]
            ok += got
            pm = per_mode.setdefault(mode, [0, 0])
            pm[0] += len(X0); pm[1] += len(got)
        out[meth] = dict(setup=t_setup, solve=t_solve, n=n_starts, ok=len(ok),
                         valleys=distinct(ok), per_mode=per_mode)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--preset", default="Balanced")
    ap.add_argument("--methods", nargs="+", default=["trf", "lm", "batch"])
    ap.add_argument("--max-iter", type=int, default=100, help="LM iteration cap")
    a = ap.parse_args()
    global MAX_ITER
    MAX_ITER = a.max_iter
    import json
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    jl = os.path.join(HERE, "results", f"probe_batch_p1_{a.preset}.jsonl")
    for cid in a.cases:
        o = run(cid, a.preset, a.methods)
        with open(jl, "a") as f:
            f.write(json.dumps({"case": cid, "preset": a.preset, **o}, default=float) + "\n")
        print(f"\n=== {cid} ({a.preset}) Phase 1 ===")
        for meth, d in o.items():
            pm = " ".join(f"{k}:{v[1]}/{v[0]}" for k, v in d["per_mode"].items())
            print(f"  {meth:<6} solve {d['solve']:7.3f}s (+lambdify {d['setup']:.2f}s)  "
                  f"converged {d['ok']:4d}/{d['n']:<4d} valleys {d['valleys']}  [{pm}]")


if __name__ == "__main__":
    main()
