# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028 probe: basin of attraction of a Phase-1 root, TRF vs log-space LM.

Runs one section's Phase 1 (serial, production code), takes the converged roots,
perturbs each component by log-normal noise of width sigma and re-solves with
  * production scipy TRF (Phase-1 ratio settings, wide box),
  * lm_core.lm_log unbounded (log space, min-norm steps),
  * lm_core.lm_log projected onto the same wide box.
Also prints TRF's cost trajectory from a 5 % perturbation (the stall plateaus).

    python dev/fs028/probe_basin.py LPn3-VCVS
"""
import contextlib
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fs028_common as C          # noqa: E402
import lm_core                    # noqa: E402


def main():
    import unified_solver_v2 as US
    from scipy.optimize import least_squares
    cid = sys.argv[1] if len(sys.argv) > 1 else "LPn3-VCVS"
    case = [c for c in C.bench_cases() if c[0] == cid][0]
    _, sec, fam, topos, dct = case
    roots = []
    rec = C.Recorder()
    with C.workdir(os.path.join(C.WORK_DIR, "basin")):
        with C.instrumented(rec):              # serial pool: worker state stays in _W
            orig = US.phase1_worker

            def cap(task):
                r = orig(task)
                if r is not None:
                    roots.append((task["topo_name"], r))
                return r
            US.phase1_worker = cap
            with contextlib.redirect_stdout(open(os.devnull, "w")):
                C.run_section(sec, fam, topos, dct, preset="Balanced", n_cores=1,
                              instrument=False)
    print(f"{cid}: {len(roots)} converged Phase-1 starts")
    if not roots:
        return
    rng = np.random.default_rng(0)
    name, r = roots[0]
    F = US._W["funcs"][name]; lay = F["layout"]; cfg = US._W["cfg"]
    nC = lay["n_caps"]
    x = np.array([r["caps"][c] for c in lay["cap_names"]] + list(r["r_hint"]))
    g = 1.0 / cfg["C_max"]                         # back into ratio coordinates
    xr = np.concatenate([x[:nC] * g, x[nC:] / g])
    lb, ub = US.ratio_bounds(lay, cfg, wide=True)
    res_f, jac_f = F["res_f"], F["jac_f"]
    obj = lambda z: np.array(res_f(*z), float)
    jac = lambda z: np.asarray(jac_f(*z), float)
    print(f"{name}: {len(xr)} components, {lay['n_residuals']} residuals, "
          f"|r|^2 at the root {np.sum(obj(xr)**2):.1e}")
    print(f"{'perturbation':<13} {'TRF conv':>8} {'TRF nfev':>8} {'TRF ms':>7} | "
          f"{'LM free':>7} {'evals':>5} {'ms':>5} | {'LM box':>6} {'evals':>5} {'ms':>5}")
    N = 40
    for sig in (0.02, 0.05, 0.2, 0.5, 1.0):
        st = {"trf": [0, [], 0.0], "free": [0, [], 0.0], "box": [0, [], 0.0]}
        for _ in range(N):
            z0 = np.clip(xr * np.exp(sig * rng.standard_normal(len(xr))),
                         lb * (1 + 1e-7), ub * (1 - 1e-7))
            t = time.perf_counter()
            rr = least_squares(obj, z0, jac=jac, bounds=(lb, ub), method="trf",
                               xtol=1e-10, ftol=1e-10, max_nfev=700)
            st["trf"][2] += time.perf_counter() - t
            if rr.success and np.sum(rr.fun ** 2) < 1e-5:
                st["trf"][0] += 1; st["trf"][1].append(rr.nfev)
            for key, box in (("free", False), ("box", True)):
                t = time.perf_counter()
                xs, rv, nf, nj, ok, _s = lm_core.lm_log(obj, jac, z0, tol=1e-20, max_iter=100,
                                                       lb=lb if box else None,
                                                       ub=ub if box else None)
                st[key][2] += time.perf_counter() - t
                inb = np.all(xs >= lb * (1 - 1e-9)) and np.all(xs <= ub * (1 + 1e-9))
                if ok and float(rv @ rv) < 1e-5 and (inb or not box):
                    st[key][0] += 1; st[key][1].append(nf)
        cells = []
        for key in ("trf", "free", "box"):
            n_ok, nf, tt = st[key]
            cells.append(f"{n_ok:>3}/{N} {np.median(nf) if nf else 0:5.0f} {1e3*tt/N:6.2f}")
        print(f"x{np.exp(sig):<12.2f} " + " | ".join(cells))
    print("\nTRF cost after each evaluation, three starts 5 % off the root:")
    for trial in range(3):
        costs = []

        def f(z):
            v = obj(z); costs.append(float(np.sum(v ** 2))); return v
        z0 = np.clip(xr * np.exp(0.05 * rng.standard_normal(len(xr))), lb * (1 + 1e-7),
                     ub * (1 - 1e-7))
        rr = least_squares(f, z0, jac=jac, bounds=(lb, ub), method="trf", xtol=1e-10,
                           ftol=1e-10, max_nfev=700)
        k10 = next((i for i, c in enumerate(costs) if c < 1e-10), None)
        print(f"  start {trial}: nfev {rr.nfev}, first cost < 1e-10 after {k10} evaluations")
        print("   " + " ".join(f"{c:.0e}" for c in costs[:48]))


if __name__ == "__main__":
    main()
