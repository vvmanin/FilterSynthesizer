# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028 probe: learned seeds for Phase 1 (the "ML seed + numerical polish" idea).

One cell, residuals derived ONCE with symbolic design targets (p1, w0, wz, Q, K
become lambdify arguments -- no per-design derivation), everything solved with the
vectorized projected LM of lm_core. Three ways to seed Phase 1 for an unseen target:

  cold   the production start set (ratio + wide + anchored, Balanced sizes)
  atlas  k-nearest-neighbour lookup: the accepted valleys of the k closest
         training targets (log-target distance) are the starts   ("valley atlas")
  poly   an explicit analytic seed: quadratic polynomial in the log targets,
         least-squares fitted to the lowest-sensitivity valley of every training
         target, predicts one component vector (+ a few jittered copies)

Training = cold solves on N_train random targets (the atlas IS that data; the
polynomial is fitted to it). Test = N_test new random targets. Reports acceptance
rate, distinct valleys (harvest's 8 % dedup), best sensitivity proxy relative to
cold, LM iterations and single-core time per target.

    python dev/fs028/probe_atlas.py --cell 3LPn-unity --train 300 --test 60
"""
import argparse
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fs028_common as C          # noqa: E402,F401  (puts the repo root on sys.path)
import lm_core                    # noqa: E402

ENV = dict(C_min=6.8e-5, C_max=1e-2, R_min=3e-4, R_max=2.0)
F0 = 1000.0


def param_funcs(name):
    """Residuals / Jacobian / sensitivity proxy with the design targets as
    arguments: res_b(X, T), jac_b(X, T), s2_b(X, T); T rows = [p1, w0, wz, Q, K]."""
    import sympy as sp
    import tf_derivation_v2 as TF
    import unified_solver_v2 as US
    topo = TF.topo_for_name(name)
    t = time.perf_counter()
    cs = TF.derive_ideal(topo, {})
    t_derive = time.perf_counter() - t
    vl = list(cs["var_list"])
    ts = [TF.p1, TF.w0, TF.wz, TF.Q, TF.K]
    eqs = list(cs["res_eqs"])
    m, n = len(eqs), len(vl)
    t = time.perf_counter()
    rf = sp.lambdify(vl + ts, eqs, "numpy", cse=True)
    Jm = sp.Matrix(eqs).jacobian(vl)
    nz = [(i, j) for i in range(m) for j in range(n) if Jm[i, j] != 0]
    jf = sp.lambdify(vl + ts, [Jm[i, j] for i, j in nz], "numpy", cse=True)
    af = sp.lambdify(vl + ts, [cs["a1_expr"], cs["a2_expr"]], "numpy", cse=True)
    t_lamb = time.perf_counter() - t
    ii = np.array([p[0] for p in nz]); jj = np.array([p[1] for p in nz])

    def res_b(X, T):
        out = rf(*X.T, *T.T)
        R = np.empty((X.shape[0], m))
        for k, v in enumerate(out):
            R[:, k] = v
        return R

    def jac_b(X, T):
        J = np.zeros((X.shape[0], m, n))
        for k, v in enumerate(jf(*X.T, *T.T)):
            J[:, ii[k], jj[k]] = v
        return J

    def ab(X, T):
        v = af(*X.T, *T.T)
        return (np.broadcast_to(np.asarray(v[0], float), X.shape[:1]),
                np.broadcast_to(np.asarray(v[1], float), X.shape[:1]))

    def s2_b(X, T):
        a1b, a2b = ab(X, T)
        tot = np.zeros(X.shape[0])
        for i in range(n):
            Xp = X.copy(); Xp[:, i] *= 1.01
            p1, p2 = ab(Xp, T)
            with np.errstate(all="ignore"):
                tot += ((p1 - a1b) / a1b / 0.01) ** 2 + ((p2 - a2b) / a2b / 0.01) ** 2
        return tot

    lay = US.cell_layout(cs)
    return dict(res_b=res_b, jac_b=jac_b, s2_b=s2_b, lay=lay, n=n, m=m,
                t_derive=t_derive, t_lambdify=t_lamb)


def targets(rng, N, topo):
    """Random design targets for the cell's section kind, unity passband gain
    (band-pass: centre gain in [0.5, 4]). Features = the dimensionless targets."""
    fam = topo.get("family", "LP"); order = int(topo["order"]); notch = bool(topo["notch"])
    hp = fam.startswith("HP"); bp = fam.startswith("BP")
    q = np.exp(rng.uniform(np.log(0.6), np.log(8.0 if bp else 3.0), N))
    zlo, zhi = ((0.17, 0.67) if hp else (1.5, 6.0))
    rz = np.exp(rng.uniform(np.log(zlo), np.log(zhi), N)) if notch else np.ones(N)
    rp = np.exp(rng.uniform(np.log(0.3), np.log(1.2), N)) if order == 3 else np.ones(N)
    g = np.exp(rng.uniform(np.log(0.5), np.log(4.0), N)) if bp else np.ones(N)
    w0 = 2 * np.pi * F0
    if bp:
        K = g * w0 / q
    elif hp:
        K = np.ones(N)
    else:
        a0 = (rp * w0) * w0 ** 2 if order == 3 else np.full(N, w0 ** 2)
        K = a0 / ((rz * w0) ** 2 if notch else 1.0)
    if fam.endswith("-MFB") or fam.endswith("-AM"):
        import tf_derivation_v2 as TF
        K = np.array([TF.dc_gain_to_K(topo, {TF.p1: rp[i] * w0, TF.w0: w0, TF.wz: rz[i] * w0,
                                             TF.Q: q[i], TF.K: 1.0}, g[i] if bp else 1.0)
                      if not bp else K[i] for i in range(N)], float)
    T = np.column_stack([rp * w0, np.full(N, w0), rz * w0, q, K])
    feats = np.column_stack([np.log(q), np.log(rz), np.log(rp) + (np.log(g) if bp else 0.0)])
    return T, feats


def start_set(lay, preset="Balanced"):
    """(X0 rows, lb rows, ub rows, mode) for ONE target -- the production set."""
    import unified_solver_v2 as US
    p = C.SEARCH_PRESETS[preset]
    cfg = dict(ENV)
    rows = []
    lb_r, ub_r = US.ratio_bounds(lay, cfg)
    for x in US.weyl_starts(p["ratio_starts"], lb_r, ub_r):
        rows.append((x, lb_r, ub_r, "ratio"))
    lb_w, ub_w = US.ratio_bounds(lay, cfg, wide=True)
    for x in US.loguniform_starts(max(8, p["ratio_starts"] // 2), lb_w, ub_w):
        rows.append((x, lb_w, ub_w, "wide"))
    lb_a, ub_a, anchor, free_caps = US.anchored_bounds(lay, cfg)
    names = lay["names"]
    ai = names.index(anchor)
    order = [names.index(c) for c in free_caps] + [names.index(r) for r in lay["res_names"]]
    for xf in US.weyl_starts(p["anchored_starts"], lb_a, ub_a):
        x = np.empty(len(names)); lb = np.empty(len(names)); ub = np.empty(len(names))
        x[order] = xf; lb[order] = lb_a; ub[order] = ub_a
        x[ai] = lb[ai] = ub[ai] = ENV["C_max"]           # anchor held by a degenerate box
        rows.append((x, lb, ub, "anchored"))
    return rows


def accept_rows(X, cost, modes, lay):
    """phase1_worker's acceptance -> physical component vectors (or None)."""
    nC = lay["n_caps"]
    out = []
    for x, c, md in zip(X, cost, modes):
        if md == "anchored":
            out.append(x.copy() if c < 1e-6 else None)
            continue
        if not c < 1e-5:
            out.append(None); continue
        g = ENV["C_max"] / np.max(x[:nC])
        cp = x[:nC] * g; rp = x[nC:] / g
        if cp.min() < ENV["C_min"] * 0.95 or rp.max() > ENV["R_max"] * 1.5:
            out.append(None); continue
        out.append(np.concatenate([cp, rp]))
    return out


def distinct(vecs, nC, rtol=0.08):
    keep = []
    for v in vecs:
        cv = v[:nC]
        if not any(np.allclose(cv, k[:nC], rtol=rtol) for k in keep):
            keep.append(v)
    return keep


def solve_rows(F, rows, T1, max_iter=100):
    X0 = np.array([r[0] for r in rows]); LB = np.array([r[1] for r in rows])
    UB = np.array([r[2] for r in rows]); TT = np.broadcast_to(T1, (len(rows), 5))
    res = lambda X, idx: F["res_b"](X, TT[idx])
    jac = lambda X, idx: F["jac_b"](X, TT[idx])
    X, Cc, conv, it, nev = lm_core.batch_lm_log(res, jac, X0, tol=1e-20, max_iter=max_iter,
                                                lb=LB, ub=UB, with_idx=True)
    # phase1_worker requires r.success: an iteration-capped start is rejected
    return X, np.where(conv, Cc, np.inf), it


def phys_box(n, nC):
    lb = np.array([ENV["C_min"]] * nC + [ENV["R_min"]] * (n - nC))
    ub = np.array([ENV["C_max"]] * nC + [ENV["R_max"]] * (n - nC))
    return lb, ub


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", default="3LPn-unity")
    ap.add_argument("--train", type=int, default=300)
    ap.add_argument("--test", type=int, default=60)
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--seeds", type=int, default=48)
    a = ap.parse_args()
    rng = np.random.default_rng(7)
    F = param_funcs(a.cell)
    lay = F["lay"]; nC = lay["n_caps"]; n = F["n"]
    print(f"{a.cell}: n={n} m={F['m']}  one-time symbolic derive {F['t_derive']:.2f}s, "
          f"lambdify {F['t_lambdify']:.2f}s (then reused for EVERY design)")
    base_rows = start_set(lay)
    modes = [r[3] for r in base_rows]

    # ---------------- training: cold solves -> atlas
    import tf_derivation_v2 as TF
    topo = TF.topo_for_name(a.cell)
    Ttr, ftr = targets(rng, a.train, topo)
    atlas = []
    t = time.perf_counter()
    for T1 in Ttr:
        X, Cc, _ = solve_rows(F, base_rows, T1)
        acc = [v for v in accept_rows(X, Cc, modes, lay) if v is not None]
        atlas.append(distinct(acc, nC))
    t_train = time.perf_counter() - t
    feas = [i for i, v in enumerate(atlas) if v]
    print(f"training: {a.train} targets, cold solve {t_train:.1f}s total "
          f"({1e3*t_train/a.train:.0f} ms/target), feasible {len(feas)}, "
          f"valleys/target median {np.median([len(v) for v in atlas]):.0f}")

    # ---------------- analytic seed: quadratic polynomial on log-targets
    def phi(f):
        f = np.atleast_2d(f)
        cols = [np.ones(len(f))] + [f[:, i] for i in range(3)]
        cols += [f[:, i] * f[:, j] for i in range(3) for j in range(i, 3)]
        return np.column_stack(cols)
    Y = []; Fx = []
    for i in feas:
        vs = np.array(atlas[i])
        s2 = F["s2_b"](vs, np.broadcast_to(Ttr[i], (len(vs), 5)))
        best = vs[int(np.argmin(s2))]
        y = np.log(best); y[:nC] -= np.log(ENV["C_max"]); y[nC:] += np.log(ENV["C_max"])
        Y.append(y); Fx.append(ftr[i])
    Y = np.array(Y); Phi = phi(np.array(Fx))
    coef = np.linalg.solve(Phi.T @ Phi + 1e-6 * np.eye(Phi.shape[1]), Phi.T @ Y)
    fit_err = np.sqrt(np.mean((Phi @ coef - Y) ** 2, axis=0))
    print(f"poly seed: {Phi.shape[1]} terms x {n} outputs, in-sample RMS log-error "
          f"{np.mean(fit_err):.3f} (x{np.exp(np.mean(fit_err)):.2f})")

    # ---------------- test
    Tte, fte = targets(rng, a.test, topo)
    lbp, ubp = phys_box(n, nC)
    stats = {k: [] for k in ("cold", "atlas", "poly")}
    for T1, f1 in zip(Tte, fte):
        t = time.perf_counter()
        X, Cc, it = solve_rows(F, base_rows, T1)
        acc = [v for v in accept_rows(X, Cc, modes, lay) if v is not None]
        dc = distinct(acc, nC)
        tc = time.perf_counter() - t
        s_cold = (np.sqrt(np.min(F["s2_b"](np.array(dc), np.broadcast_to(T1, (len(dc), 5)))))
                  if dc else np.nan)
        stats["cold"].append((len(acc) / len(base_rows), len(dc), s_cold, tc, it))
        # atlas
        d = np.linalg.norm(ftr[feas] - f1, axis=1)
        nb = [feas[j] for j in np.argsort(d)[:a.k]]
        seeds = [v for j in nb for v in atlas[j]]
        if len(seeds) > a.seeds:
            seeds = [seeds[j] for j in np.linspace(0, len(seeds) - 1, a.seeds).astype(int)]
        t = time.perf_counter()
        if seeds:
            rows = [(np.clip(s, lbp, ubp), lbp, ubp, "wide") for s in seeds]
            X, Cc, it = solve_rows(F, rows, T1)
            acc = [v for v in accept_rows(X, Cc, ["wide"] * len(rows), lay) if v is not None]
        else:
            acc, it = [], 0
        da = distinct(acc, nC)
        ta = time.perf_counter() - t
        s_at = (np.sqrt(np.min(F["s2_b"](np.array(da), np.broadcast_to(T1, (len(da), 5)))))
                if da else np.nan)
        stats["atlas"].append((len(acc) / max(1, len(seeds)), len(da), s_at, ta, it))
        # poly (1 seed + 4 jittered)
        y = (phi(f1) @ coef)[0]
        x = np.exp(y); x[:nC] *= ENV["C_max"]; x[nC:] /= ENV["C_max"]
        seeds = [x] + [x * np.exp(0.15 * rng.standard_normal(n)) for _ in range(4)]
        t = time.perf_counter()
        rows = [(np.clip(s, lbp, ubp), lbp, ubp, "wide") for s in seeds]
        X, Cc, it = solve_rows(F, rows, T1)
        acc = [v for v in accept_rows(X, Cc, ["wide"] * len(rows), lay) if v is not None]
        dp = distinct(acc, nC)
        tp = time.perf_counter() - t
        s_p = (np.sqrt(np.min(F["s2_b"](np.array(dp), np.broadcast_to(T1, (len(dp), 5)))))
               if dp else np.nan)
        stats["poly"].append((len(acc) / len(rows), len(dp), s_p, tp, it))

    cold = np.array(stats["cold"])
    feas_t = np.isfinite(cold[:, 2])
    print(f"\ntest: {a.test} targets, {int(feas_t.sum())} feasible (cold found >= 1 valley)")
    print(f"{'method':<7} {'accept rate':>11} {'valleys':>8} {'best sens / cold':>17} "
          f"{'found any':>9} {'ms/target':>10} {'LM iters':>8}")
    for k in ("cold", "atlas", "poly"):
        s = np.array(stats[k])[feas_t]
        rel = s[:, 2] / cold[feas_t, 2]
        print(f"{k:<7} {np.mean(s[:, 0]):11.2f} {np.median(s[:, 1]):8.0f} "
              f"{np.nanmedian(rel):8.3f} (p90 {np.nanpercentile(rel, 90):.2f}) "
              f"{np.mean(np.isfinite(s[:, 2])):9.2f} {1e3*np.mean(s[:, 3]):10.1f} "
              f"{np.median(s[:, 4]):8.0f}")


if __name__ == "__main__":
    main()
