# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Solve tasks: one captured Topology-tab job, and the benchmark set.

solve_job runs the callable exactly as the app submitted it, then classifies:
  OK                 BOMs, best snap cost and the ideal-case shape error are fine
  DEGRADED           BOMs, but best snap cost > SNAP_COST_WARN or shape error > SHAPE_ERR_WARN
  NO_BOM_EXPECTED    no BOM and solvability_probe says infeasible (gain / structure)
  NO_BOM_ENVELOPE    no BOM; the probe realises it, but with more R / C spread than the envelope
  NO_BOM_SUSPICIOUS  no BOM though the probe says it fits the envelope (search missed it)
  ERROR              the solve raised (e.g. an unregistered cell name)
(TIMEOUT / CRASH are assigned by the pool.)
"""
import contextlib
import importlib
import io
import math
import time

import numpy as np

TWO_PI = 2 * math.pi


def _target(sec, w, K, dc_gain=None):
    """Section target H(jw) = K N(s)/D(s), N and D monic (topology_tab.section_dc_gain)."""
    s = 1j * w
    w0 = TWO_PI * float(sec["f0_hz"])
    q = float(sec["Q"]) or 0.5
    den = s * s + s * w0 / q + w0 * w0
    if int(sec.get("order", 2)) == 3:
        den = den * (s + TWO_PI * float(sec.get("f1_hz") or sec["f0_hz"]))
    num = np.ones_like(s)
    n0 = int(sec.get("n_origin_zeros") or 0)
    num = num * s ** n0
    if sec.get("notch") and sec.get("fz_hz"):
        wz = TWO_PI * float(sec["fz_hz"])
        num = num * (s * s + wz * wz)
    return K * num / den


def shape_error(res, sec, row):
    """max | |H_r|/max|H_r| - |H_t|/max|H_t| | over f0/10 .. 10 f0 (ideal case:
    what the snapped values give with an ideal op-amp), as a fraction of the
    passband gain -- linear, so a notch moved by 1 % does not read as 6 dB."""
    from tf_derivation_v2 import make_response_func, cell_components
    case = res["cases"].get((row["topology"], "ideal"))
    if case is None:
        return None
    names = cell_components(case)
    comp = {k: row[k] for k in names["caps"] + names["resistors"]
            if isinstance(row.get(k), (int, float)) and row.get(k) is not None}
    Hf, vars_ = make_response_func(case)
    comp = {k: comp.get(k, 0.0) for k in vars_}
    f0 = float(sec["f0_hz"])
    f = np.logspace(math.log10(f0 / 10), math.log10(f0 * 10), 2000)
    hr = np.abs(Hf(comp, TWO_PI * f))
    ht = np.abs(_target(sec, TWO_PI * f, 1.0))
    if not np.all(np.isfinite(hr)) or hr.max() <= 0:
        return float("inf")
    return float(np.max(np.abs(hr / hr.max() - ht / ht.max())))


def solve_job(job):
    import matrix as M
    mod, fn = job["fn"].split(":")
    func = getattr(importlib.import_module(mod), fn)
    t0, c0 = time.perf_counter(), time.process_time()
    with contextlib.redirect_stdout(io.StringIO()):
        res = func(*job["args"], **job["kwargs"])
    wall, cpu = time.perf_counter() - t0, time.process_time() - c0
    sn = res.get("snapped") or []
    out = dict(key=job["key"], cid=job["cid"], wall=wall, cpu=cpu, n_snapped=len(sn),
               n_continuous=len(res.get("continuous") or []), mode=res.get("mode"))
    sec = job.get("section")
    if sn:
        by_snap = sorted(sn, key=lambda r: r.get("snap_cost", math.inf))
        best = by_snap[0]
        out.update(best_snap=best.get("snap_cost"), best_topology=best.get("topology"),
                   best_sens=min(r.get("sens_score", math.inf) for r in sn),
                   med10_snap=float(np.median([r.get("snap_cost", math.inf) for r in by_snap[:10]])),
                   topologies_found=sorted({r.get("topology") for r in sn}))
        try:
            out["shape_err"] = shape_error(res, sec, best) if sec else None
        except Exception as e:                             # noqa: BLE001
            out["shape_err"] = None
            out["shape_err_note"] = f"{type(e).__name__}: {e}"[:200]
        bad_snap = (out["best_snap"] or 0) > M.SNAP_COST_WARN
        bad_shape = out.get("shape_err") is not None and out["shape_err"] > M.SHAPE_ERR_WARN
        out["outcome"] = "DEGRADED" if (bad_snap or bad_shape) else "OK"
    else:
        try:
            import solvability_probe as SP
            cfg = job["args"][0]
            with contextlib.redirect_stdout(io.StringIO()):
                v = SP.assess(job["kwargs"].get("topologies") or [], cfg, job["kwargs"].get("dc_gain"))
            verdict = v.get("verdict") if isinstance(v, dict) else str(v)
            out["probe"] = {k: v[k] for k in ("verdict", "cell", "need_r_ratio", "need_c_ratio",
                                              "band", "target_gain", "reach_gain")
                            if isinstance(v, dict) and k in v}
        except Exception as e:                             # noqa: BLE001
            verdict = f"probe error: {type(e).__name__}"
        out["verdict"] = verdict
        cfg = job["args"][0] if job["args"] else {}
        pr = out.get("probe") or {}
        wider = ((pr.get("need_r_ratio") or 0) > float(cfg.get("MAX_R_RATIO") or math.inf)
                 or (pr.get("need_c_ratio") or 0) > float(cfg.get("C_max") or 1) / float(cfg.get("C_min") or 1))
        out["outcome"] = ("NO_BOM_EXPECTED" if str(verdict).startswith("INFEASIBLE")
                          else "NO_BOM_ENVELOPE" if (verdict == "FEASIBLE_GLOBAL" and wider)
                          else "NO_BOM_SUSPICIOUS")
    return out


def warmup():
    """Derive (or load) every cell template and its kernel sources once, in one
    process, before the parallel phases -- workers then only read the caches."""
    import tf_derivation_v2 as TF
    import cell_kernels as CK
    t = time.perf_counter()
    names = sorted({TF.topo_name(c) for c in TF.all_cells()})
    with contextlib.redirect_stdout(io.StringIO()):
        cases = TF.get_templates(names)
        n_src = 0
        for name in names:
            for kind in ("ideal", "nonideal"):
                case = cases.get((name, kind))
                for g in CK.GROUPS:
                    try:
                        CK.sources(case, g)
                        n_src += 1
                    except Exception:                      # noqa: BLE001  (group n/a for this cell)
                        pass
    import cells_first_order as FO
    fo = sorted({FO.topo_name(c) for c in FO.all_cells()})
    return dict(cells=names + fo, n_sources=n_src, secs=time.perf_counter() - t)


# =============================================================================
# benchmark (the FS-028 section set, routed by dev/fs028/fs028_common)
# =============================================================================
def bench_cases(ids=None):
    import sys
    from common import ROOT
    import os
    p = os.path.join(ROOT, "dev", "fs028")
    if p not in sys.path:
        sys.path.insert(0, p)
    import fs028_common as C
    return [(cid, fam) for cid, sec, fam, topos, dct in C.bench_cases(ids=ids)]


def bench_task(case_id, family, preset, opamp_sel="ideal", repeats=3):
    """One FS-028 benchmark section, solved `repeats` times on one core: the
    minimum wall is the figure to compare (sub-second solves are noisy)."""
    import sys
    import os
    from common import ROOT
    p = os.path.join(ROOT, "dev", "fs028")
    if p not in sys.path:
        sys.path.insert(0, p)
    import fs028_common as C
    sid = case_id.rsplit("-", 1)[0]
    sec = C.section_by_id(sid)
    topos, dct = C.route(sec, family)
    op = None if opamp_sel == "ideal" else C.TL072
    walls = []
    for _ in range(max(1, repeats)):
        with contextlib.redirect_stdout(io.StringIO()):
            res, info = C.run_section(sec, family, topos, dct, preset=preset, opamp=op,
                                      instrument=False, n_cores=1)
        walls.append(info["wall"])
    sn = res.get("snapped") or []
    return dict(id=f"{case_id}|{preset}|{opamp_sel}", case=case_id, preset=preset, opamp=opamp_sel,
                wall=min(walls), wall_median=float(np.median(walls)), repeats=len(walls),
                n_snapped=len(sn),
                best_snap=min((r.get("snap_cost", math.inf) for r in sn), default=None),
                best_sens=min((r.get("sens_score", math.inf) for r in sn), default=None))
