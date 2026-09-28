# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028 probe: Phase 3 (fixed E-series caps -> resistor solve) as a batched solve.

Captures the exact Phase-3 task list harvest() produces (production TRF Phase 1),
then solves the resistor systems three ways:

  trf    production semantics: per task, hints in order then log-uniform fallback
         starts, scipy bounded TRF, stop at the first cost <= accept
  batch  stage A: every (task, hint) pair in ONE batch_lm_log call; stage B: the
         tasks still above `accept` get their fallback starts in one more call

Caps are held fixed by giving them a degenerate box (lb = ub). Reports CPU and the
number of tasks that reach phase3_worker's acceptance threshold (the manifold-
dependent 1e-6 / 1e-3 / 5e-3), which is what gates _assemble.

    python dev/fs028/probe_batch_p3.py HPn3-AM LPn3g-VCVS --preset Balanced
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


class _Captured(Exception):
    pass


def capture(cid, preset, p1="trf"):
    """Phase-3 task list from harvest(). p1="lm" runs Phase 1 with the LM adapter
    (fast; a different but representative task list)."""
    import unified_solver_v2 as US
    import zero_manifold_solver as ZM
    saved = (US.least_squares, ZM.least_squares, US.phase1_worker, US.phase3_worker, US.zm_worker)
    if p1 == "lm":
        ad = lm_core.LSAdapter(max_iter=100, only="p1")
        US.least_squares = ad; ZM.least_squares = ad
        lm_core.wrap_workers(US)
    case = [c for c in C.bench_cases() if c[0] == cid][0]
    _, sec, fam, topos, dct = case
    box = {}
    orig = US.harvest

    def h(p1_results, cases, cfg, mv, hp):
        tasks, vc, kept = orig(p1_results, cases, cfg, mv, hp)
        box.update(tasks=tasks, cases=cases, cfg=cfg)
        raise _Captured()
    US.harvest = h
    try:
        with C.workdir(os.path.join(C.WORK_DIR, f"p3{os.getpid()}")):
            with contextlib.redirect_stdout(open(os.devnull, "w")):
                try:
                    C.run_section(sec, fam, topos, dct, preset=preset, n_cores=1)
                except _Captured:
                    pass
    finally:
        US.harvest = orig
        (US.least_squares, ZM.least_squares, US.phase1_worker, US.phase3_worker,
         US.zm_worker) = saved
    return box


def accept_thr(lay):
    man = lay["n_res"] - lay["n_residuals"]
    return 1e-6 if man >= 2 else (1e-3 if man == 1 else 5e-3), man


def run_trf(box):
    import unified_solver_v2 as US
    from scipy.optimize import least_squares
    cases, cfg = box["cases"], box["cfg"]
    n_ok = 0
    t0 = time.perf_counter()
    fcache = {}
    for task in box["tasks"]:
        name = task["topo_name"]
        case = cases[(name, "ideal")]
        lay = US.cell_layout(case)
        if name not in fcache:
            import sympy as sp
            vl = case["var_list"]
            fcache[name] = (sp.lambdify(vl, case["res_eqs"], "numpy", cse=True),
                            sp.lambdify(vl, sp.Matrix(case["res_eqs"]).jacobian(list(vl)),
                                        "numpy", cse=True))
        res_f, jac_f = fcache[name]
        nC = lay["n_caps"]
        cv = [task["cap_combo"][c] for c in lay["cap_names"]]
        obj = lambda x: np.array(res_f(*(cv + list(x))), float)
        jac = lambda x: np.asarray(jac_f(*(cv + list(x))), float)[:, nC:]
        lb3, ub3 = US.phase3_res_bounds(lay, cfg)
        acc, man = accept_thr(lay)
        best = np.inf
        for hnt in task["r_hints"]:
            x0 = np.clip(np.asarray(hnt, float), lb3 + 1e-12, ub3 - 1e-12)
            r = least_squares(obj, x0, jac=jac, bounds=(lb3, ub3), xtol=1e-9, ftol=1e-9, max_nfev=300)
            best = min(best, float(np.sum(r.fun ** 2)))
            if best <= acc:
                break
        if best > acc:
            for x0 in US.loguniform_starts(24 if man == 0 else 8, lb3, ub3):
                r = least_squares(obj, x0, jac=jac, bounds=(lb3, ub3), xtol=1e-11, ftol=1e-11,
                                  max_nfev=500)
                c = float(np.sum(r.fun ** 2))
                if c < best and r.success:
                    best = c
                if best <= acc:
                    break
        n_ok += int(best <= acc)
    return time.perf_counter() - t0, n_ok


def run_batch(box):
    import unified_solver_v2 as US
    cases, cfg = box["cases"], box["cfg"]
    by = {}
    for i, task in enumerate(box["tasks"]):
        by.setdefault(task["topo_name"], []).append(i)
    t_setup = 0.0; t_solve = 0.0; n_ok = 0
    for name, ids in by.items():
        case = cases[(name, "ideal")]
        lay = US.cell_layout(case)
        t = time.perf_counter()
        res_b, jac_b = lm_core.batch_funcs(case)
        t_setup += time.perf_counter() - t
        t = time.perf_counter()
        nC = lay["n_caps"]
        lb3, ub3 = US.phase3_res_bounds(lay, cfg)
        acc, man = accept_thr(lay)
        rows, owner = [], []
        for i in ids:
            task = box["tasks"][i]
            cv = [task["cap_combo"][c] for c in lay["cap_names"]]
            for hnt in task["r_hints"]:
                rows.append(cv + list(np.clip(np.asarray(hnt, float), lb3, ub3))); owner.append(i)
        X0 = np.array(rows)
        LB = np.hstack([X0[:, :nC], np.broadcast_to(lb3, (len(X0), lb3.size))])
        UB = np.hstack([X0[:, :nC], np.broadcast_to(ub3, (len(X0), ub3.size))])
        X, Cc, conv, it, nev = lm_core.batch_lm_log(res_b, jac_b, X0, tol=1e-20, max_iter=100,
                                                    lb=LB, ub=UB)
        best = {}
        for k, i in enumerate(owner):
            best[i] = min(best.get(i, np.inf), Cc[k])
        left = [i for i in ids if not best[i] <= acc]
        if left:
            fb = US.loguniform_starts(24 if man == 0 else 8, lb3, ub3)
            rows, owner = [], []
            for i in left:
                task = box["tasks"][i]
                cv = [task["cap_combo"][c] for c in lay["cap_names"]]
                for x0 in fb:
                    rows.append(cv + list(x0)); owner.append(i)
            X0 = np.array(rows)
            LB = np.hstack([X0[:, :nC], np.broadcast_to(lb3, (len(X0), lb3.size))])
            UB = np.hstack([X0[:, :nC], np.broadcast_to(ub3, (len(X0), ub3.size))])
            X, Cc, conv, it, nev = lm_core.batch_lm_log(res_b, jac_b, X0, tol=1e-20, max_iter=100,
                                                        lb=LB, ub=UB)
            for k, i in enumerate(owner):
                best[i] = min(best[i], Cc[k])
        n_ok += sum(1 for i in ids if best[i] <= acc)
        t_solve += time.perf_counter() - t
    return t_setup, t_solve, n_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--preset", default="Balanced")
    ap.add_argument("--no-trf", action="store_true")
    ap.add_argument("--p1", default="trf", choices=["trf", "lm"],
                    help="Phase-1 solver used to produce the task list")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    cases = [c[0] for c in C.bench_cases()] if a.all else a.cases
    for cid in cases:
        box = capture(cid, a.preset, a.p1)
        if "tasks" not in box:
            print(f"\n=== {cid}: no Phase-3 tasks (Phase 1 found no valley) ===")
            continue
        nt = len(box["tasks"]); nh = sum(len(t["r_hints"]) for t in box["tasks"])
        print(f"\n=== {cid} ({a.preset}) Phase 3: {nt} tasks, {nh} hints ===")
        row = {"case": cid, "preset": a.preset, "p1": a.p1, "tasks": nt, "hints": nh}
        if not a.no_trf:
            t, ok = run_trf(box)
            print(f"  trf    {t:8.2f}s  tasks reaching accept {ok}/{nt}")
            row.update(trf_s=t, trf_ok=ok)
        ts, t, ok = run_batch(box)
        print(f"  batch  {t:8.3f}s (+lambdify {ts:.2f}s)  tasks reaching accept {ok}/{nt}")
        row.update(batch_s=t, batch_setup_s=ts, batch_ok=ok)
        import json
        os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
        with open(os.path.join(HERE, "results", f"probe_batch_p3_{a.preset}.jsonl"), "a") as f:
            f.write(json.dumps(row, default=float) + "\n")


if __name__ == "__main__":
    main()
