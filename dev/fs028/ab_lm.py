# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028 A/B: scipy bounded TRF (baseline) vs the log-space LM adapter
(lm_core.LSAdapter) inside Phase 1 / Phase 3 / zero-manifold, everything else
unchanged (same starts, harvest, acceptance, _assemble, non-ideal, snap).

    python dev/fs028/ab_lm.py LPn3-VCVS LPn3-MFB --preset Balanced [--opamp tl072]
    python dev/fs028/ab_lm.py --all --jobs 3

Per case: serial CPU of each stage, BOM count, best sens_score / snap_cost, and
whether the baseline's best BOM (topology + caps + resistors) is still found.
Writes dev/fs028/results/ab_lm_<preset>_<opamp>.json.
"""
import argparse
import contextlib
import json
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fs028_common as C          # noqa: E402
import lm_core                    # noqa: E402

KEYS = ["C1", "C2", "C3", "C4", "C1a", "C1b", "C2a", "C2b",
        "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]


def _sig(s):
    return (s.get("topology"),) + tuple(round(float(s.get(k) or 0.0), 9) for k in KEYS)


def _quality(res):
    sn = res.get("snapped") or []
    if not sn:
        return {"n": 0}
    sens = sorted(float(s.get("sens_score", 1e9)) for s in sn)
    cost = sorted(float(s.get("snap_cost", 1e9)) for s in sn)
    return {"n": len(sn), "best_sens": sens[0], "med5_sens": float(sens[min(2, len(sens)-1)]),
            "best_cost": cost[0],
            "best_bom": _sig(min(sn, key=lambda s: s.get("sens_score", 1e9))),
            "sigs": [_sig(s) for s in sn]}


def one(args):
    cid, preset, opamp_name, variant = args
    import unified_solver_v2 as US
    import zero_manifold_solver as ZM
    case = [c for c in C.bench_cases() if c[0] == cid][0]
    _, sec, fam, topos, dct = case
    opamp = C.TL072 if opamp_name == "tl072" else None
    wd = os.path.join(C.WORK_DIR, f"ab{os.getpid()}")
    stats = {"lm": 0, "fallback": 0, "lm_oob": 0}
    with C.workdir(wd):
        with contextlib.redirect_stdout(open(os.devnull, "w")):      # warm TF cache
            C.run_section(sec, fam, topos, dct, preset="Warm", opamp=opamp, n_cores=1)
        saved = (US.least_squares, ZM.least_squares)
        saved_w = None
        if variant.startswith("lm"):
            only = {"lm-p1": "p1", "lm-p3": "p3"}.get(
                variant.replace("-polishrep", "").replace("-polish", "").replace("-p3pol", ""))
            ad = lm_core.LSAdapter(max_iter=100, fallback=(variant != "lm-nofb"), stats=stats,
                                   only=only)
            ad.p3_polish = variant.endswith("p3pol")
            US.least_squares = ad
            ZM.least_squares = ad
            saved_w = lm_core.wrap_workers(US)
        if variant.startswith("trf-loose"):
            # trf-loose        : xtol=ftol=1e-8, max_nfev x0.25
            # trf-loose-tol    : xtol=ftol=1e-7 only (budgets unchanged)
            ad = (lm_core.LooseTRF(1e-7, 1.0) if variant == "trf-loose-tol"
                  else lm_core.LooseTRF(1e-8, 0.25))
            US.least_squares = ad
            ZM.least_squares = ad
        saved_h = US.harvest
        if variant.endswith("polish") or variant.endswith("polishrep"):
            # -polish    : APPEND polished copies of the Phase-1 valleys (25 % margin)
            # -polishrep : REPLACE the valleys by their polished versions (no margin)
            rep = variant.endswith("polishrep")

            def harvest_polished(p1_results, cases, cfg, *a, **k):
                pol = lm_core.polish_p1_results(p1_results, cases, cfg, stats,
                                                margin=1.0 if rep else 1.25, add=not rep)
                return saved_h(pol, cases, cfg, *a, **k)
            US.harvest = harvest_polished
        try:
            t = time.perf_counter()
            res, info = C.run_section(sec, fam, topos, dct, preset=preset, opamp=opamp, n_cores=1)
            wall = time.perf_counter() - t
        finally:
            US.least_squares, ZM.least_squares = saved
            if saved_w is not None:
                US.phase1_worker, US.phase3_worker, US.zm_worker = saved_w
            US.harvest = saved_h
    sm = C.summarize(info, 32)
    return {"case": cid, "preset": preset, "opamp": opamp_name, "variant": variant,
            "cpu": wall, "task_cpu": sm["cpu"], "stage": sm["stage"], "task_n": sm["n"],
            "model32": sm["wall_model"], "q": _quality(res), "lm_stats": stats}


def compare(b, v):
    qb, qv = b["q"], v["q"]
    found = None
    if qb.get("n") and qv.get("n"):
        found = qb["best_bom"] in set(map(tuple, qv["sigs"]))
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--preset", default="Balanced")
    ap.add_argument("--opamp", default="ideal", choices=["ideal", "tl072"])
    ap.add_argument("--variants", nargs="+", default=["base", "lm"])
    ap.add_argument("--jobs", type=int, default=1)
    a = ap.parse_args()
    cases = [c[0] for c in C.bench_cases()] if a.all else a.cases
    todo = [(cid, a.preset, a.opamp, v) for cid in cases for v in a.variants]
    rows = []
    if a.jobs > 1:
        with ProcessPoolExecutor(a.jobs, mp_context=mp.get_context("spawn")) as ex:
            rows = list(ex.map(one, todo))
    else:
        rows = [one(t) for t in todo]
    by = {}
    for r in rows:
        by.setdefault(r["case"], {})[r["variant"]] = r
    print(f"{'case':<12} {'variant':<8} {'cpu s':>7} {'p1':>6} {'p3':>6} {'zm':>5} {'ni':>5} "
          f"{'BOMs':>4} {'best sens':>9} {'best cost':>9}  base-best-found  lm(oob/fb)")
    for cid in cases:
        d = by.get(cid, {})
        b = d.get("base")
        for vn in a.variants:
            r = d.get(vn)
            if r is None:
                continue
            q = r["q"]; tc = r["task_cpu"]
            fnd = "" if (vn == "base" or b is None) else str(compare(b, r))
            print(f"{cid:<12} {vn:<8} {r['cpu']:7.2f} {tc.get('p1', 0):6.2f} {tc.get('p3', 0):6.2f} "
                  f"{tc.get('zm', 0):5.2f} {tc.get('ni', 0):5.2f} {q.get('n', 0):4d} "
                  f"{q.get('best_sens', float('nan')):9.3f} {q.get('best_cost', float('nan')):9.3g}  "
                  f"{fnd:<15}  {r['lm_stats']['lm_oob']}/{r['lm_stats']['fallback']}")
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    path = os.path.join(HERE, "results", f"ab_lm_{a.preset}_{a.opamp}.json")
    old = []
    if os.path.exists(path):
        with open(path) as f:
            old = [r for r in json.load(f) if (r["case"], r["variant"]) not in
                   {(x["case"], x["variant"]) for x in rows}]
    with open(path, "w") as f:
        json.dump(old + rows, f, default=float)


if __name__ == "__main__":
    main()
