# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-037 follow-up measurement (analysis only -- patches the solver in-process,
changes no production file).

Question: with a SHORT custom capacitor list, how many sections that get no BOM
would an extra RC-scale search rescue?

Today `unified_solver_v2._scale_seeds_for_valley` adds a re-scaled candidate only
when the legacy scale (largest cap at C_max, f = 1) is infeasible. Variants:

  base      production behaviour
  snapopt   also when f = 1 is feasible: the snap-optimal scale (12-point scan)
  landing   also when f = 1 is feasible: the k = 3 scales at which one capacitor
            lands EXACTLY on a list value and the others are closest overall

Run:  python dev/fs037/measure_scale_search.py
Prints one table per list and a totals line per variant.
"""
import contextlib
import io
import os
import sys
import time

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "dev", "fs028"))

import cap_values as CV             # noqa: E402
import unified_solver_v2 as US      # noqa: E402
import fs028_common as C            # noqa: E402

LISTS = {
    "4 mixed":    "100p, 1n, 4.7n, 10n",
    "E3 1n-10n":  "1n, 2.2n, 4.7n, 10n",
    "decades":    "1n, 10n, 100n",
    "stock 10":   "470p, 1n, 2.2n, 3.3n, 4.7n, 6.8n, 10n, 22n, 47n, 100n",
}

_orig_seeds = US._scale_seeds_for_valley


def _nearest_err(C, cg, f):
    return sum(abs(US._nearest(c * f, cg) - c * f) / (c * f) for c in C)


def seeds_snapopt(C, R, cfg, cg):
    out = _orig_seeds(C, R, cfg, cg)
    if out:
        return out
    f_lo, f_hi = US._feasible_scale_interval(C, R, cfg)
    if f_lo > f_hi:
        return []
    f = US._snap_optimal_scale(C, cg, f_lo, f_hi)
    return [] if f is None or abs(f - 1.0) < 1e-9 else [(f, "full")]


def seeds_landing(C, R, cfg, cg, k=3):
    out = list(_orig_seeds(C, R, cfg, cg))
    f_lo, f_hi = US._feasible_scale_interval(C, R, cfg)
    if f_lo > f_hi:
        return out
    cands = sorted({float(v) / c for c in C for v in cg if f_lo <= float(v) / c <= f_hi})
    cands = [f for f in cands if abs(f - 1.0) > 1e-9]
    cands.sort(key=lambda f: _nearest_err(C, cg, f))
    have = [f for f, _ in out]
    for f in cands:
        if len(out) >= len(have) + k:
            break
        if all(abs(f / g - 1) > 1e-6 for g in have + [x for x, _ in out]):
            out.append((f, "full"))
    return out


VARIANTS = {"base": _orig_seeds, "snapopt": seeds_snapopt, "landing": seeds_landing}


def cases():
    out = list(C.bench_cases())
    for sec in (C._sec("LPn3m", 3, "LPn", 1000.0, 1.2, fz=3000.0, f1=800.0),
                C._sec("LPn2m", 2, "LPn", 1000.0, 0.8, fz=2500.0),
                C._sec("HPn2m", 2, "HPn", 1000.0, 0.9, fz=400.0)):
        for fam in ("VCVS", "MFB", "AM"):
            r = C.route(sec, fam)
            if r:
                out.append((f"{sec['id']}-{fam}", sec, fam, r[0], r[1]))
    return out


def run(sec, fam, topos, dct, vals):
    env = dict(C.ENV_DEFAULT, C_series=CV.CUSTOM, C_values=vals,
               C_min=vals[0], C_max=vals[-1])
    t = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        res, _ = C.run_section(sec, fam, topos, dct, preset="Balanced", instrument=False,
                               env=env)
    rows = res.get("snapped") or []
    best = min((r.get("snap_cost", np.inf) for r in rows), default=None)
    return len(rows), best, time.perf_counter() - t


def main():
    allc = cases()
    totals = {v: dict(zero=0, rescued=0, more=0, t=0.0) for v in VARIANTS}
    with C.workdir():
        for lname, text in LISTS.items():
            vals, err = CV.parse_cap_list(text)
            assert not err, err
            print(f"\n=== list '{lname}': {text}")
            print(f"{'case':<14}" + "".join(f"{v:>22}" for v in VARIANTS))
            for cid, sec, fam, topos, dct in allc:
                cells = {}
                for vname, fn in VARIANTS.items():
                    US._scale_seeds_for_valley = fn
                    try:
                        cells[vname] = run(sec, fam, topos, dct, vals)
                    finally:
                        US._scale_seeds_for_valley = _orig_seeds
                    totals[vname]["t"] += cells[vname][2]
                b = cells["base"][0]
                for vname, (n, best, _t) in cells.items():
                    if n == 0:
                        totals[vname]["zero"] += 1
                    if vname != "base" and b == 0 and n > 0:
                        totals[vname]["rescued"] += 1
                    if vname != "base" and n > b:
                        totals[vname]["more"] += 1
                line = "".join(
                    f"{n:>5} rows {('%.3g' % best) if best is not None else '-':>7} {t:4.1f}s"
                    for n, best, t in cells.values())
                print(f"{cid:<14}{line}")
    n = len(allc) * len(LISTS)
    print(f"\n{n} (section, list) runs")
    for v, d in totals.items():
        print(f"  {v:<8} no-BOM {d['zero']:3d}   rescued {d['rescued']:3d}   "
              f"more rows {d['more']:3d}   time {d['t']:6.1f}s")


if __name__ == "__main__":
    main()
