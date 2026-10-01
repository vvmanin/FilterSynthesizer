# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028 post-S2-2 profile: where a section solve spends its time now.

Run from the repo root:

    python dev/fs028/probe_profile.py LPn3-MFB LPn3g-MFB LP3-AM N2-VCVS --opamp tl072
    python dev/fs028/probe_profile.py --q10 --opamp tl072      # the maintainer's Q = 10 section
    python dev/fs028/probe_profile.py LPn3-MFB --opamp tl072 --top 40   # + raw cProfile listing

One warm-up solve (TF cache + process memos), then one solve under cProfile,
non-instrumented (the production path: one process, no pool). Prints the
inclusive time of the pipeline stages and of the hot spots inside the snapper,
score_solution and the non-ideal correction, plus the work counts that drive
them (solutions snapped, resistor combos, response calls).
"""
import argparse
import contextlib
import cProfile
import os
import pstats
import time

import fs028_common as C

# (label, file suffix, function name) -> inclusive time from cProfile
SPOTS = [
    ("synthesize total",            "filter_synthesis.py", "synthesize"),
    ("  run_synthesis (P1+P3+ZM)",  "unified_solver_v2.py", "run_synthesis"),
    ("  solve_nonideal (NI)",       "nonideal_solver.py", "solve_nonideal"),
    ("    _correct_batch (NI LM)",  "nonideal_solver.py", "_correct_batch"),
    ("    score_solution",          "scoring.py", "score_solution"),
    ("      _response_metrics",     "scoring.py", "_response_metrics"),
    ("      minimize_scalar",       "_minimize.py", "minimize_scalar"),
    ("  design_cases (snap prep)",  "filter_synthesis.py", "design_cases"),
    ("  snap_to_hardware",          "discrete_snapper.py", "snap_to_hardware"),
    ("    get_two_nearest",         "discrete_snapper.py", "get_two_nearest"),
    ("    make_response_func",      "tf_derivation_v2.py", "make_response_func"),
    ("H() lambdified, all callers", "tf_derivation_v2.py", "H"),
    ("am_mna response, all",        "am_mna.py", None),
]


def _match(stats, fsuf, name):
    """Sum inclusive time / calls of matching functions. name None = the
    top-level-most function of the file (max cumtime)."""
    hits = [(k, v) for k, v in stats.stats.items() if k[0].replace("\\", "/").endswith(fsuf)
            and (name is None or k[2] == name)]
    if not hits:
        return None
    if name is None:
        k, v = max(hits, key=lambda kv: kv[1][3])
        return v[1], v[3]
    # recursive / multiple defs: take the max cumtime entry (they nest)
    k, v = max(hits, key=lambda kv: kv[1][3])
    return sum(h[1][1] for h in hits), v[3]


def _callers_split(stats, fsuf, name):
    """Inclusive time of fn split by direct caller (file:function)."""
    out = {}
    for k, v in stats.stats.items():
        if k[0].replace("\\", "/").endswith(fsuf) and k[2] == name:
            for ck, cv in v[4].items():
                lbl = f"{os.path.basename(ck[0])}:{ck[2]}"
                d = out.setdefault(lbl, [0, 0.0])
                d[0] += cv[1]; d[1] += cv[3]
    return sorted(out.items(), key=lambda kv: -kv[1][1])


def _count_snap(res):
    """Resistor combos the snapper evaluated (2^n per solution, minus duplicates)."""
    import discrete_snapper as DS
    from tf_derivation_v2 import cell_components
    cases = res.get("cases") or {}
    n_sol = 0; n_combo = 0; nr = {}
    for s in res.get("continuous") or []:
        topo = s["topology"]
        cm = cell_components(cases[(topo, "ideal")])
        nr[topo] = len(cm["resistors"])
        n_sol += 1
        n_combo += 2 ** len(cm["resistors"])
    return n_sol, n_combo, nr


def profile_case(cid, sec, fam, topos, dct, opamp, top):
    wd = os.path.join(C.WORK_DIR, f"w{os.getpid()}")
    with C.workdir(wd):
        with contextlib.redirect_stdout(open(os.devnull, "w")):
            C.run_section(sec, fam, topos, dct, preset="Warm", opamp=opamp,
                          n_cores=1, instrument=False)
            C.run_section(sec, fam, topos, dct, preset="Balanced", opamp=opamp,
                          n_cores=1, instrument=False)      # second warm: memos at steady state
        pr = cProfile.Profile()
        t = time.perf_counter()
        with contextlib.redirect_stdout(open(os.devnull, "w")):
            pr.enable()
            res, info = C.run_section(sec, fam, topos, dct, preset="Balanced", opamp=opamp,
                                      n_cores=1, instrument=False)
            pr.disable()
        wall = time.perf_counter() - t
        # unprofiled wall, for the profiler overhead
        t = time.perf_counter()
        with contextlib.redirect_stdout(open(os.devnull, "w")):
            C.run_section(sec, fam, topos, dct, preset="Balanced", opamp=opamp,
                          n_cores=1, instrument=False)
        wall0 = time.perf_counter() - t
    st = pstats.Stats(pr)
    n_sol, n_combo, nr = _count_snap(res)
    print(f"\n=== {cid}  ({', '.join(topos)})  opamp={'tl072' if opamp else 'ideal'}")
    print(f"wall unprofiled {wall0:.3f} s | profiled {wall:.3f} s | "
          f"snapped {n_sol} sols, {n_combo} combos, resistors/cell {nr} | "
          f"ideal {len(res.get('ideal_continuous') or [])}")
    tot = None
    for lbl, fsuf, name in SPOTS:
        m = _match(st, fsuf, name)
        if m is None:
            continue
        calls, ct = m
        if tot is None:
            tot = ct
        print(f"  {lbl:<32s} {ct:7.3f} s  {100*ct/tot:5.1f} %  calls {calls}")
    print("  H() by caller:")
    for lbl, (n, ct) in _callers_split(st, "tf_derivation_v2.py", "H")[:6]:
        print(f"    {lbl:<40s} {ct:7.3f} s  calls {n}")
    if top:
        st.sort_stats("tottime").print_stats(top)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--opamp", default="tl072", choices=["ideal", "tl072"])
    ap.add_argument("--q10", action="store_true", help="add the maintainer's Q = 10 section")
    ap.add_argument("--top", type=int, default=0, help="also print the top-N tottime rows")
    a = ap.parse_args()
    opamp = C.TL072 if a.opamp == "tl072" else None
    todo = [c for c in C.bench_cases() if c[0] in a.cases]
    if a.q10:
        # S2-2 note §12: 2nd-order LPn, f0 999.4 Hz, Q 10.01, fz 1254 Hz, gain 1.1 -> VCVS
        sec = C._sec("Q10", 2, "LPn", 999.4, 10.01, fz=1254.0, gain=1.1)
        r = C.route(sec, "VCVS")
        todo.append(("Q10-VCVS", sec, "VCVS", r[0], r[1]))
    for cid, sec, fam, topos, dct in todo:
        profile_case(cid, sec, fam, topos, dct, opamp, a.top)


if __name__ == "__main__":
    main()
