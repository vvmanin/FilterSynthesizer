# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028 baseline benchmark: Topology-tab section solves, timed per stage.

Run from the repo root:

    python dev/fs028/bench_sections.py                      # all cases, Balanced, ideal
    python dev/fs028/bench_sections.py --preset Fast Balanced Thorough --opamp tl072
    python dev/fs028/bench_sections.py --cases LPn3-VCVS HPn2-MFB --jobs 1
    python dev/fs028/bench_sections.py --mode pool --cores 4  # real process pools

--mode instrumented (default) runs each solve in ONE process with the solver's
process pools replaced by a serial pool, so every stage and every worker task is
timed exactly (serial CPU-seconds). The 32-core wall time is then modelled from
the measured task times (greedy in-order scheduling, the pool.map(chunksize=1)
behaviour) plus the serial stages. --mode pool runs the real ProcessPoolExecutor
paths (default fork on Linux; --spawn emulates Windows) for a wall-clock check.

Cases = (section, family) pairs routed exactly as topology_tab does for an
untouched UI; see fs028_common.section_set() / route(). Each result row keeps the
top-5 snapped BOMs so later optimizations can be checked against this baseline.

Writes dev/fs028/results/<tag>.json (tag = --tag or a timestamp).
"""
import argparse
import contextlib
import functools
import json
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fs028_common as C          # noqa: E402

BOM_KEYS = ["C1", "C2", "C3", "C4", "C1a", "C1b", "C2a", "C2b",
            "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]


def _bom_rows(res, n=5):
    rows = sorted(res.get("snapped") or [], key=lambda s: s.get("sens_score", 1e99))[:n]
    out = []
    for s in rows:
        r = {"topology": s.get("topology"), "sens": round(float(s.get("sens_score", 0.0)), 6),
             "snap_cost": (None if s.get("snap_cost") is None else round(float(s["snap_cost"]), 6))}
        for k in BOM_KEYS:
            v = s.get(k)
            if v is not None and v != 0.0:
                r[k] = float(v)
        out.append(r)
    return out


def run_case(args):
    cid, preset, opamp_name, mode, cores, spawn, fresh, warm = args
    case = [c for c in C.bench_cases() if c[0] == cid][0]
    _, sec, fam, topos, dct = case
    opamp = C.TL072 if opamp_name == "tl072" else None
    wd = os.path.join(C.WORK_DIR, f"w{os.getpid()}")
    row = {"case": cid, "preset": preset, "opamp": opamp_name, "mode": mode, "warm": warm,
           "topos": topos, "dc_gain": dct, "sec": {k: sec[k] for k in
           ("f0_hz", "Q", "fz_hz", "f1_hz", "K_radps", "order", "family")}}
    with C.workdir(wd, fresh=fresh):
        if fresh:
            C.clear_process_caches()
        if warm:        # one untimed solve first: TF cache + process memos warm
            with contextlib.redirect_stdout(open(os.devnull, "w")):
                C.run_section(sec, fam, topos, dct, preset="Warm", opamp=opamp,
                              n_cores=1, instrument=True)
        if mode == "pool":
            import unified_solver_v2 as US
            import nonideal_solver as NI
            ctx = mp.get_context("spawn" if spawn else "fork")
            US.ProcessPoolExecutor = functools.partial(ProcessPoolExecutor, mp_context=ctx)
            NI.ProcessPoolExecutor = functools.partial(ProcessPoolExecutor, mp_context=ctx)
            with contextlib.redirect_stdout(open(os.devnull, "w")):
                res, info = C.run_section(sec, fam, topos, dct, preset=preset, opamp=opamp,
                                          n_cores=cores, instrument=False)
            row["wall"] = info["wall"]
            row["cores"] = cores
            row["start"] = "spawn" if spawn else "fork"
        else:
            res, info = C.run_section(sec, fam, topos, dct, preset=preset, opamp=opamp,
                                      n_cores=cores, instrument=True)
            sm = C.summarize(info, 32)
            rec = info["rec"]
            row["serial_cpu"] = sm["cpu_total"]
            row["stage"] = sm["stage"]
            row["task_cpu"] = sm["cpu"]
            row["task_n"] = sm["n"]
            row["model32"] = sm["wall_model"]
            row["model4"] = C.summarize(info, 4)["wall_model"]
            row["task_ok"] = {ph: sum(1 for _t, m in v if m.get("ok")) for ph, v in rec.tasks.items()}
            # phase-1 per mode: starts, converged, CPU
            p1 = {}
            for t, m in rec.tasks.get("p1", []):
                d = p1.setdefault(m["mode"], {"n": 0, "ok": 0, "cpu": 0.0})
                d["n"] += 1; d["ok"] += int(m["ok"]); d["cpu"] += t
            row["p1_modes"] = p1
            ls = {}
            for r in rec.ls:
                d = ls.setdefault(r["ctx"], {"calls": 0, "nfev": 0, "t": 0.0, "maxed": 0})
                d["calls"] += 1; d["nfev"] += r["nfev"]; d["t"] += r["t"]
                d["maxed"] += int(r["status"] == 0)
            row["least_squares"] = ls
    row["n_snapped"] = len(res.get("snapped") or [])
    row["n_ideal"] = len(res.get("ideal_continuous") or [])
    row["top_bom"] = _bom_rows(res)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--preset", nargs="+", default=["Balanced"],
                    choices=list(C.SEARCH_PRESETS))
    ap.add_argument("--opamp", nargs="+", default=["ideal"], choices=["ideal", "tl072"])
    ap.add_argument("--cases", nargs="*", default=None, help="case ids, e.g. LPn3-VCVS")
    ap.add_argument("--families", nargs="*", default=["VCVS", "MFB", "AM"])
    ap.add_argument("--mode", default="instrumented", choices=["instrumented", "pool"])
    ap.add_argument("--cores", type=int, default=4, help="pool workers (--mode pool)")
    ap.add_argument("--spawn", action="store_true", help="spawn start method (Windows-like)")
    ap.add_argument("--cold", action="store_true", help="empty TF cache for every case")
    ap.add_argument("--warm", action="store_true",
                    help="untimed Fast solve of the same case first (warm TF cache)")
    ap.add_argument("--jobs", type=int, default=1, help="parallel cases (instrumented mode)")
    ap.add_argument("--tag", default=None)
    a = ap.parse_args()

    cases = [c[0] for c in C.bench_cases(families=tuple(a.families))
             if a.cases is None or c[0] in a.cases]
    todo = [(cid, p, o, a.mode, a.cores, a.spawn, a.cold, a.warm)
            for p in a.preset for o in a.opamp for cid in cases]
    tag = a.tag or time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(HERE, "results")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{tag}.json")
    rows = []
    t0 = time.time()
    if a.jobs > 1 and a.mode == "instrumented":
        with ProcessPoolExecutor(max_workers=a.jobs,
                                 mp_context=mp.get_context("spawn")) as ex:
            for row in ex.map(run_case, todo):
                rows.append(row); _print_row(row)
                _dump(path, rows, a)
    else:
        for t in todo:
            row = run_case(t)
            rows.append(row); _print_row(row)
            _dump(path, rows, a)
    print(f"\n{len(rows)} runs in {time.time()-t0:.0f}s -> {os.path.relpath(path, C.ROOT)}")


def _dump(path, rows, a):
    with open(path, "w") as f:
        json.dump({"args": vars(a), "rows": rows}, f, indent=1, default=float)


def _print_row(r):
    if r["mode"] == "pool":
        print(f"{r['case']:<12} {r['preset']:<9} {r['opamp']:<6} wall({r['cores']} {r['start']}) "
              f"{r['wall']:7.2f}s  BOMs {r['n_snapped']}")
        return
    m = r["model32"]
    tc = r["task_cpu"]
    print(f"{r['case']:<12} {r['preset']:<9} {r['opamp']:<6} cpu {r['serial_cpu']:6.1f}s | "
          f"p1 {tc.get('p1', 0):5.1f} p3 {tc.get('p3', 0):5.1f} zm {tc.get('zm', 0):4.1f} "
          f"ni {tc.get('ni', 0):4.1f} derive {r['stage'].get('get_cases@main', 0):4.1f}"
          f"+{r['stage'].get('get_cases@ni_init', 0):4.1f} | 32c model {m['total']:5.1f}s "
          f"| BOMs {r['n_snapped']}")


if __name__ == "__main__":
    main()
