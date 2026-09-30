# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028 S2-1 check: compiled cell kernels vs the per-design path they replace.

For every registered cell (and the AM Equalize variant) at random designs:

  old  derive_ideal(topo, numeric design) [-> apply_equalize] -> lambdify
       (what unified_solver_v2._init_worker / _sens_funcs / ZM.prep_cell_funcs did)
  new  tf_derivation_v2.design_cases [-> apply_equalize] -> cell_kernels

EXACT groups -- the generated SOURCE must equal the old lambdify source, so the
solver's numbers are bit-identical:
  design (res, jac, r5 for this design)   gain (a1, a2, h0, hinf)
  sens (a1, a2 over tf_var_list)          zm (den_i, num_i)
PARAMETRIC group "res" (targets as arguments; for S2-2/S2-3, not on the TRF
path) must agree to REL_TOL at random component points: rounding only.

Then, in a fresh process, every disk-cached group is replayed from the kernel
cache file and must equal a live regeneration (same source, same numbers).

Run from the repository root (private work dir for the caches):

    python dev/fs028/check_kernels.py                 # all cells
    python dev/fs028/check_kernels.py --cells 3HPn-gained 2LPn-unity --jobs 1
"""
import argparse
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REL_TOL = 1e-9
N_DESIGNS = 3
N_POINTS = 4


def _rel(a, b):
    """Norm-wise relative difference: per row for a matrix (Jacobian), over the
    vector for a residual vector, element-wise for a scalar. Element-wise on an
    array is meaningless: entries that cancel to ~0 (or are exactly 0 in one
    form) carry pure rounding noise relative to themselves."""
    a = np.asarray(a, dtype=complex)
    b = np.asarray(b, dtype=complex)
    if a.shape != b.shape:
        return np.inf
    if a.size == 0:
        return 0.0
    if a.ndim == 2:
        scale = np.maximum(np.abs(a).max(axis=1, keepdims=True),
                           np.abs(b).max(axis=1, keepdims=True))
    else:
        scale = max(float(np.abs(a).max()), float(np.abs(b).max()))
    scale = np.maximum(scale, 1e-300)
    return float(np.max(np.abs(a - b) / scale))


def _design(rng):
    """Random targets, as the solver builds them (numpy floats)."""
    import tf_derivation_v2 as TF
    w0 = 2 * np.pi * rng.uniform(100.0, 20e3)
    return {TF.p1: w0 * rng.uniform(0.3, 1.5), TF.w0: w0, TF.wz: w0 * rng.uniform(0.3, 4.0),
            TF.Q: np.float64(rng.uniform(0.6, 6.0)), TF.K: rng.uniform(0.5, 3.0) * w0 ** 2}


def _old_exprs(old, design):
    """{group: [(fname, args, expr)]} exactly as the pre-S2-1 code lambdified."""
    import sympy as sp
    import tf_derivation_v2 as TF
    s = TF.s
    vl = list(old["var_list"])
    tfv = list(old["tf_var_list"])
    num, den = old["tf_num"], old["tf_den"]
    design_g = [("res", vl, old["res_eqs"]),
                ("jac", vl, sp.Matrix(old["res_eqs"]).jacobian(vl))]
    if old["R5_constraint"] is not None:
        design_g.append(("r5", vl, old["R5_constraint"].subs(design)))
    out = {"design": design_g,
           "gain": [("a1", vl, old["a1_expr"]), ("a2", vl, old["a2_expr"]),
                    ("h0", vl, num.subs(s, 0) / den.subs(s, 0)),
                    ("hinf", vl, sp.Poly(num, s).LC() / sp.Poly(den, s).LC())],
           "sens": [("a1", tfv, old["a1_expr"]), ("a2", tfv, old["a2_expr"])],
           "zm": ([(f"den_{i}", tfv, c) for i, c in enumerate(sp.Poly(den, s).all_coeffs())]
                  + [(f"num_{i}", tfv, c) for i, c in enumerate(sp.Poly(num, s).all_coeffs())])}
    return out


def check_cell(args):
    name, work, seed = args
    os.makedirs(work, exist_ok=True)
    os.chdir(work)
    import tf_derivation_v2 as TF
    import cell_kernels as CK
    import unified_solver_v2 as US
    rng = np.random.default_rng(seed)
    topo = TF.topo_for_name(name)
    fam = str(topo.get("family", ""))
    variants = [False, True] if fam.endswith("-AM") else [False]
    exact_bad = []                           # (variant, group, fname)
    n_exact = 0
    worst = 0.0                              # parametric group, max rel diff
    t0 = time.time()
    for _d in range(N_DESIGNS):
        design = _design(rng)
        old_raw = {(name, "ideal"): TF.derive_ideal(topo, design)}
        new_raw = TF.design_cases(design, [name])
        for eq in variants:
            cfg = {"equalize_rc": eq}
            old = US.apply_equalize(old_raw, cfg)[(name, "ideal")]
            new = US.apply_equalize(new_raw, cfg)[(name, "ideal")]
            tag = "eq" if eq else "raw"
            assert [str(v) for v in old["var_list"]] == [str(v) for v in new["var_list"]]
            ref = _old_exprs(old, design)
            got = {"design": CK.design_sources(new), "gain": CK.sources(new, "gain"),
                   "sens": CK.sources(new, "sens"), "zm": CK.sources(new, "zm")}
            for group, items in ref.items():
                if set(f for f, _a, _e in items) != set(got[group]):
                    exact_bad.append((tag, group, "kernel set"))
                    continue
                for fname, fargs, expr in items:
                    n_exact += 1
                    if CK._lamb(fargs, expr, fname)["src"] != got[group][fname]["src"]:
                        exact_bad.append((tag, group, fname))
            # the design group must also evaluate bit-identically
            d = CK.load(got["design"])
            o = CK.load({f: CK._lamb(a, e, f) for f, a, e in ref["design"]})
            n = len(old["var_list"])
            for _p in range(N_POINTS):
                x = list(np.exp(rng.uniform(np.log(1e-3), 0.0, n)))
                for fname in d:
                    if not np.array_equal(np.asarray(d[fname](*x), float),
                                          np.asarray(o[fname](*x), float), equal_nan=True):
                        exact_bad.append((tag, "design-eval", fname))
            # parametric residuals: rounding-level agreement
            k = CK.load(CK.sources(new, "res"))
            T = new["targets"]
            pairs = [("res", k["res"]), ("jac", k["jac"]), ("r5", k.get("r5"))]
            for _p in range(N_POINTS):
                x = list(np.exp(rng.uniform(np.log(1e-3), 0.0, n)))
                for fname, fn in pairs:
                    if fn is None:
                        continue
                    worst = max(worst, _rel(o[fname](*x), CK.bind(fn, T)(*x)))
    return name, n_exact, exact_bad, worst, time.time() - t0


def replay_check(names, work):
    """Fresh process: every disk-cached group, loaded from the kernel file, vs a
    live regeneration of the same group."""
    os.chdir(work)
    import tf_derivation_v2 as TF
    import cell_kernels as CK
    import unified_solver_v2 as US
    rng = np.random.default_rng(7)
    blob = TF._load_blob(CK.cache_path())["cells"]
    bad, n = [], 0
    for name in names:
        c = TF.design_cases({getattr(TF, t): 1.0 for t in TF.TARGETS}, [name])
        fam = str(c[(name, "ideal")]["topo"].get("family", ""))
        for eq in ([False, True] if fam.endswith("-AM") else [False]):
            case = US.apply_equalize(c, {"equalize_rc": eq})[(name, "ideal")]
            for group in CK.GROUPS:
                key = CK.kernel_key(case, group)
                if key not in blob:
                    continue                 # zm is generated for the ZM cells only
                n += 1
                live = CK._generate(case, group)
                if live != blob[key]:
                    bad.append((name, group, "source differs from disk"))
                disk, lf = CK.load(blob[key]), CK.load(live)
                nargs = len(case["tf_var_list"]) if group in ("sens", "zm") \
                    else len(case["var_list"])
                for fname in disk:
                    x = list(np.exp(rng.uniform(-5, 0, nargs)))
                    x += [1.0] * len(TF.TARGETS) if group == "res" else []
                    try:
                        a = np.asarray(disk[fname](*x), dtype=complex)
                        b = np.asarray(lf[fname](*x), dtype=complex)
                    except Exception:                    # noqa: BLE001
                        continue                         # both raise alike (same code)
                    if not np.array_equal(a, b, equal_nan=True):
                        bad.append((name, group, fname))
    return n, bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cells", nargs="*", default=None)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--work", default=os.path.join(tempfile.gettempdir(), "fs028_check_kernels"))
    ap.add_argument("--no-replay", action="store_true")
    a = ap.parse_args()
    import tf_derivation_v2 as TF
    names = a.cells or [TF.topo_name(t) for t in TF.all_cells()]
    os.makedirs(a.work, exist_ok=True)
    t0 = time.time()
    jobs = max(1, a.jobs)
    todo = [(n, os.path.join(a.work, f"c{i % jobs}"), 1000 + i) for i, n in enumerate(names)]
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs, mp_context=mp.get_context("spawn")) as ex:
            results = list(ex.map(check_cell, todo))
    else:
        results = [check_cell(t) for t in todo]
    fails, n_ex, worst_all = [], 0, 0.0
    for name, n_exact, bad, worst, dt in results:
        n_ex += n_exact
        worst_all = max(worst_all, worst)
        ok = not bad and worst <= REL_TOL
        if not ok:
            fails.append(name)
        print(f"{'OK ' if ok else 'BAD'} {name:18s} exact {n_exact - len(bad)}/{n_exact}"
              f"  parametric max rel {worst:.1e}  {dt:5.1f}s" + (f"  {bad[:4]}" if bad else ""))
    print(f"\n{len(results)} cells: {n_ex} exact-group kernels, source-identical unless listed; "
          f"parametric max rel {worst_all:.1e} (tol {REL_TOL:.0e}); failing: {fails}")
    if not a.no_replay:
        by_dir = {}
        for n, d, _s in todo:
            by_dir.setdefault(d, []).append(n)
        nrep, bad = 0, []
        with ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("spawn")) as ex:
            for d, ns in by_dir.items():
                k, b = ex.submit(replay_check, ns, d).result()
                nrep += k
                bad += b
        print(f"replay from disk: {nrep} groups, {'OK' if not bad else bad}")
        fails += [b[0] for b in bad]
    print(f"done in {time.time() - t0:.0f}s")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
