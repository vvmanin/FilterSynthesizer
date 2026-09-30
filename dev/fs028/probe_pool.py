# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028 probe: fixed cost of the solver's per-section process pools.

Every section solve builds up to two ProcessPoolExecutors (Phase 1/3 in
unified_solver_v2.run_synthesis, the op-amp correction in nonideal_solver) and
every worker runs an initializer (load the case file + lambdify residuals and
Jacobians; the non-ideal worker re-derives the cells symbolically). This probe
builds each pool exactly as the solver does, forces every worker to start, and
reports wall time to "all workers ready" and the CPU the children burned
(RUSAGE_CHILDREN), for fork and spawn (spawn = the Windows start method).

Since FS-028 S2-1 the Phase-1/3 initializer only exec's compiled-kernel sources
(unified_solver_v2.worker_packs) and the non-ideal correction has no pool; the
probe follows whichever API the checkout has. On Windows there is no fork and
no RUSAGE_CHILDREN: only spawn is run and only the wall time is reported.

    python dev/fs028/probe_pool.py LPn3-MFB HPn3-VCVS --workers 4
"""
import argparse
import contextlib
import multiprocessing as mp
import os
import sys
import tempfile
import time
import uuid
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fs028_common as C          # noqa: E402


def _ready(_):
    time.sleep(0.3)               # keep the worker busy so every worker gets started
    return os.getpid()


def _children_cpu():
    try:
        import resource
    except ImportError:           # Windows: not available
        return float("nan")
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return ru.ru_utime + ru.ru_stime


def pool_cost(kind, method, workers, sec, fam, topos, dct):
    import numpy as np
    import unified_solver_v2 as US
    import nonideal_solver as NI
    import tf_derivation_v2 as TF
    cfg = C.build_cfg(sec)
    if fam == "AM":
        cfg["equalize_rc"] = True
    design = {TF.p1: 2 * np.pi * cfg["f1"], TF.w0: 2 * np.pi * cfg["f0"],
              TF.wz: 2 * np.pi * cfg["fz"], TF.Q: cfg["Q"], TF.K: cfg.get("K", 1.0)}
    dstr = {str(k): float(v) for k, v in design.items()}
    n2t = {TF.topo_name(t): t for t in TF.all_cells()}
    k_map = None if dct is None else {n: TF.dc_gain_to_K(n2t[n], design, dct) for n in topos}
    path = None
    if kind == "p13" and hasattr(US, "worker_packs"):          # S2-1
        cases = US.apply_equalize(TF.design_cases(design, topos, k_map=k_map), cfg)
        init, args = US._init_worker, (cfg, US.worker_packs(cases, topos), k_map)
    elif kind == "p13":
        with contextlib.redirect_stdout(open(os.devnull, "w")):
            cases = TF.get_cases(design, verbose=False, k_map=k_map, topo_names=topos)
        path = os.path.join(tempfile.gettempdir(), f"fs028_pool_{uuid.uuid4().hex}.json")
        TF.dump_cases({k: v for k, v in cases.items() if k[1] == "ideal"}, path)
        init, args = US._init_worker, (dstr, cfg, topos, k_map, path)
    elif not hasattr(NI, "ProcessPoolExecutor"):              # S2-1: in-process NI
        return None
    else:
        path = None
        kmap_all = {TF.topo_name(t): cfg.get("K", 1.0) for t in TF.all_cells()}
        init, args = NI._init_worker, (dstr, C.TL072, cfg, cfg["fz"], kmap_all, topos)
    c0 = _children_cpu()
    t0 = time.perf_counter()
    with ProcessPoolExecutor(workers, mp_context=mp.get_context(method),
                             initializer=init, initargs=args) as ex:
        pids = set(ex.map(_ready, range(workers * 2)))
    wall = time.perf_counter() - t0 - 0.3 * 2         # minus the forced 2 x 0.3 s sleeps
    cpu = _children_cpu() - c0
    if path:
        os.remove(path)
    return wall, cpu, len(pids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    with C.workdir(os.path.join(C.WORK_DIR, "pool")):
        for cid in a.cases:
            case = [c for c in C.bench_cases() if c[0] == cid][0]
            _, sec, fam, topos, dct = case
            methods = [m for m in ("fork", "spawn") if m in mp.get_all_start_methods()]
            for kind in ("p13", "ni"):
                for method in methods:
                    got = pool_cost(kind, method, a.workers, sec, fam, topos, dct)
                    if got is None:
                        print(f"{cid:<12} {kind:<4} in-process (no pool)")
                        break
                    wall, cpu, npid = got
                    print(f"{cid:<12} {kind:<4} {method:<6} {a.workers} workers: ready in "
                          f"{wall:6.2f}s wall, children CPU {cpu:6.2f}s "
                          f"({cpu/max(npid,1):.2f}s per worker, {npid} started)")


if __name__ == "__main__":
    main()
