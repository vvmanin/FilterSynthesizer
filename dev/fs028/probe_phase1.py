# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028 probe: what do Phase-1 / Phase-3 least_squares starts spend their
evaluations on?

For each case it runs one instrumented (serial) solve and records, for every
least_squares call in Phase 1 and Phase 3, the residual sum of squares after
each function evaluation. From those trajectories it reports:

  * per start mode (ratio / wide / anchored / seed): starts, converged, nfev and
    CPU of converged vs failed starts, how many failed starts ran into max_nfev;
  * distinct valleys vs converged starts (harvest's 8 % cap-vector dedup), i.e.
    how redundant the multistart is;
  * early-abort rules "stop a start whose cost is still > thr after k evaluations":
    CPU saved vs converged starts (and distinct valleys) lost.

    python dev/fs028/probe_phase1.py LPn3-VCVS HPn2-MFB --preset Balanced

Writes dev/fs028/results/probe_phase1_<preset>.json.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fs028_common as C          # noqa: E402

CHECK = (5, 10, 20, 30, 50, 75, 100, 150, 200, 300)
RULE_K = (10, 20, 30, 50, 100)
RULE_THR = (1e-1, 1e-2, 1e-3)


def run(cid, preset, opamp):
    import unified_solver_v2 as US
    case = [c for c in C.bench_cases() if c[0] == cid][0]
    _, sec, fam, topos, dct = case
    calls = []
    rec = C.Recorder()
    with C.workdir(os.path.join(C.WORK_DIR, f"p{os.getpid()}")):
        # warm the TF cache so derivation is not part of the probe
        C.run_section(sec, fam, topos, dct, preset="Warm", opamp=opamp, n_cores=1)
        with C.instrumented(rec):
            inner = US.least_squares

            def ls(fun, x0, *a, **k):
                costs = []

                def f(x):
                    r = fun(x)
                    costs.append(float(np.sum(np.asarray(r, float) ** 2)))
                    return r
                t = time.perf_counter()
                r = inner(f, x0, *a, **k)
                calls.append(dict(ctx=rec.ctx[-1], t=time.perf_counter() - t,
                                  task_idx=len(rec.tasks.get(rec.ctx[-1], [])),
                                  nfev=int(r.nfev), status=int(r.status),
                                  final=float(2 * r.cost), costs=costs,
                                  max_nfev=k.get("max_nfev")))
                return r
            US.least_squares = ls
            import filter_synthesis as FS
            cfg = C.build_cfg(sec)
            if fam == "AM":
                cfg["equalize_rc"] = True
            p = C.SEARCH_PRESETS[preset]
            import contextlib
            with contextlib.redirect_stdout(open(os.devnull, "w")):
                FS.synthesize(cfg, opamp=opamp, topologies=list(topos), dc_gain=dct,
                              n_cores=1, pole_tol=C.CONV_DEFAULT["pole_tol"],
                              gain_tol=C.CONV_DEFAULT["gain_tol"],
                              top_k=C.CONV_DEFAULT["top_k"], verbose=False, **p)
    # attach task-level outcome to the p1 calls (one LS call per p1 task)
    p1_tasks = rec.tasks.get("p1", [])
    p1_calls = {c["task_idx"]: c for c in calls if c["ctx"] == "p1"}
    out = {"case": cid, "preset": preset, "p1": [], "p3_calls": []}
    for i, (t, m) in enumerate(p1_tasks):
        # one least_squares call per p1 task (none if the anchored solve raised)
        c = p1_calls.get(i)
        out["p1"].append(dict(mode=m["mode"], topo=m["topo"], ok=m["ok"], t=t,
                              caps=m["caps"],
                              nfev=None if c is None else c["nfev"],
                              status=None if c is None else c["status"],
                              traj=None if c is None else _ck(c["costs"])))
    for c in calls:
        if c["ctx"] == "p3":
            out["p3_calls"].append(dict(t=c["t"], nfev=c["nfev"], status=c["status"],
                                        final=c["final"], max_nfev=c["max_nfev"]))
    return out


def _ck(costs):
    """cost after the k-th evaluation for k in CHECK (last value if it stopped)."""
    if not costs:
        return {}
    return {k: costs[min(k, len(costs)) - 1] for k in CHECK} | {"n": len(costs),
                                                                  "final": costs[-1]}


def distinct(p1, rtol=0.08):
    """harvest()'s dedup: converged starts whose cap vectors agree within rtol."""
    per = {}
    for s in p1:
        if not s["ok"]:
            continue
        cv = np.array([s["caps"][k] for k in sorted(s["caps"])])
        lst = per.setdefault(s["topo"], [])
        if not any(np.allclose(cv, e, rtol=rtol) for e in lst):
            lst.append(cv)
    return {k: len(v) for k, v in per.items()}


def report(o):
    p1 = o["p1"]
    print(f"\n=== {o['case']} ({o['preset']}) ===")
    tot = sum(s["t"] for s in p1)
    print(f"Phase 1: {len(p1)} starts, {sum(s['ok'] for s in p1)} converged, "
          f"{tot:.1f} CPU-s; distinct valleys {distinct(p1)}")
    for mode in ("ratio", "wide", "anchored", "seed"):
        ss = [s for s in p1 if s["mode"] == mode]
        if not ss:
            continue
        ok = [s for s in ss if s["ok"]]
        bad = [s for s in ss if not s["ok"]]
        maxed = sum(1 for s in bad if s["status"] == 0)
        print(f"  {mode:<9} n={len(ss):4d} ok={len(ok):4d}  CPU ok {sum(s['t'] for s in ok):6.2f}s"
              f" failed {sum(s['t'] for s in bad):6.2f}s | nfev ok med "
              f"{np.median([s['nfev'] for s in ok]) if ok else 0:.0f} / failed med "
              f"{np.median([s['nfev'] for s in bad if s['nfev']]) if bad else 0:.0f}"
              f"  failed@max_nfev {maxed}")
    # early-abort rules
    print("  early abort (cost > thr after k evals): CPU saved / converged lost / valleys lost")
    base = distinct(p1)
    for k in RULE_K:
        row = []
        for thr in RULE_THR:
            saved = 0.0; lost = 0; kept = []
            for s in p1:
                tr = s["traj"]
                if not tr or tr["n"] <= k:
                    kept.append(s); continue
                c_k = tr.get(k)
                if c_k is not None and c_k > thr:
                    saved += s["t"] * (1 - k / tr["n"])
                    lost += int(s["ok"])
                else:
                    kept.append(s)
            vl = sum(base.values()) - sum(distinct(kept).values())
            row.append(f"thr {thr:.0e}: {100*saved/max(tot,1e-9):4.0f}% / {lost:3d} / {vl:2d}")
        print(f"   k={k:<4d} " + " | ".join(row))
    p3 = o["p3_calls"]
    if p3:
        t3 = sum(c["t"] for c in p3)
        maxed = [c for c in p3 if c["status"] == 0]
        print(f"Phase 3: {len(p3)} least_squares calls, {t3:.1f} CPU-s; hit max_nfev "
              f"{len(maxed)} ({sum(c['t'] for c in maxed):.1f}s); nfev median "
              f"{np.median([c['nfev'] for c in p3]):.0f}")
        by = {}
        for c in p3:
            d = by.setdefault(c["max_nfev"], [0, 0.0, 0])
            d[0] += 1; d[1] += c["t"]; d[2] += int(c["final"] < 1e-6)
        for mx, (n, t, good) in sorted(by.items(), key=lambda kv: str(kv[0])):
            print(f"  max_nfev={mx}: calls {n} CPU {t:.1f}s  final<1e-6: {good}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--preset", default="Balanced")
    a = ap.parse_args()
    outs = []
    for cid in a.cases:
        o = run(cid, a.preset, None)
        report(o)
        outs.append(o)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    path = os.path.join(HERE, "results", f"probe_phase1_{a.preset}.json")
    old = []
    if os.path.exists(path):
        with open(path) as f:
            old = [x for x in json.load(f) if x["case"] not in a.cases]
    def rnd(x):                       # 4 significant digits keeps the file small
        if isinstance(x, float):
            return float(f"{x:.4g}")
        if isinstance(x, dict):
            return {k: rnd(v) for k, v in x.items()}
        if isinstance(x, list):
            return [rnd(v) for v in x]
        return x
    with open(path, "w") as f:
        json.dump(rnd(old + outs), f, default=float, separators=(",", ":"))


if __name__ == "__main__":
    main()
