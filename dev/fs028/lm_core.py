# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028 prototypes: log-space Levenberg-Marquardt for the Phase-1/3 solves.

PROTOTYPE CODE FOR THE ANALYSIS (dev/FS-028_solver_performance_analysis.md), not
production code; nothing in the app imports it.

* `lm_log(fun, jac, x0, lb, ub)` -- LM on u = log(x) (every R and C is positive),
  Tikhonov / min-norm steps so the usual under-determined coefficient systems
  converge to a nearby root, projected active-set handling of the box. Stops as
  soon as sum(r^2) < 1e-20. Success follows scipy TRF semantics: True when a
  termination criterion fired (tiny cost, tiny step, no descent left), False when
  the iteration cap was hit -- callers apply their own cost gates.
* `LSAdapter` -- drop-in for scipy.optimize.least_squares as unified_solver_v2 /
  zero_manifold_solver call it; `wrap_workers()` tags each task so the adapter
  knows the box semantics (Phase-1 ratio / anchored, Phase 3, ZM). Options: only
  Phase 1 or only Phase 3 (`only`), Phase-3 sensitivity polish (`p3_polish`).
* `batch_lm_log`, `batch_funcs` -- the same LM vectorized over many starts (one
  numpy evaluation per iteration); fixed variables get a degenerate box.
* `sens2_funcs`, `polish`, `polish_p1_results` -- descent of the ranking's own
  sensitivity proxy along the solution manifold (tangent step + LM retraction).
* `LooseTRF` -- scipy TRF with loosened tolerances / budgets (the "tune the
  existing solver" baseline variant).
"""
import numpy as np
from scipy.optimize import least_squares as _scipy_ls


class _Res:
    __slots__ = ("x", "fun", "cost", "success", "status", "nfev", "njev", "message")


def _mk(x, r, nfev, njev, ok, status, msg):
    o = _Res()
    o.x = np.asarray(x, float); o.fun = np.asarray(r, float)
    o.cost = 0.5 * float(o.fun @ o.fun)
    o.success = bool(ok); o.status = int(status); o.nfev = int(nfev); o.njev = int(njev)
    o.message = msg
    return o


def lm_log(fun, jac, x0, tol=1e-20, max_iter=100, mu0=1e-3, fd_step=1e-7,
           lb=None, ub=None):
    """Minimize ||fun(x)||^2 over lb <= x <= ub (x > 0) by LM in log coordinates.

    Projected active-set LM: a variable sitting on a bound with the descent
    direction pointing outward is frozen for the step; the Tikhonov (min-norm for
    under-determined systems) step is taken in the free variables and the result
    projected back into the box. No Coleman-Li scaling, so variables near a bound
    are not slowed down (TRF's long stalls near the bounds are consistent with it).

    tol      : stop when sum(r^2) < tol (1e-20 => |r| ~ 1e-10, far below the
               callers' acceptance thresholds and below 10 ppm)
    jac      : callable -> dr/dx (m x n) or None / "2-point" (forward differences
               in log space)
    Returns (x, r, nfev, njev, converged, status)."""
    x0 = np.asarray(x0, float)
    lo = np.log(np.broadcast_to(lb, x0.shape)) if lb is not None else np.full(x0.shape, -np.inf)
    hi = np.log(np.broadcast_to(ub, x0.shape)) if ub is not None else np.full(x0.shape, np.inf)
    u = np.clip(np.log(x0), lo, hi)
    x = np.exp(u)
    with np.errstate(all="ignore"):
        r = np.asarray(fun(x), float)
    nfev, njev = 1, 0
    if not np.all(np.isfinite(r)):
        return x, r, nfev, njev, False, -1
    c = float(r @ r)
    mu = mu0
    m = r.size
    n = u.size
    for _it in range(max_iter):
        if c < tol:
            return x, r, nfev, njev, True, 2
        with np.errstate(all="ignore"):
            if callable(jac):
                Jx = np.asarray(jac(x), float); njev += 1
            else:                                     # forward differences in u
                Jx = np.empty((m, n))
                for j in range(n):
                    up = u.copy(); up[j] += fd_step
                    Jx[:, j] = (np.asarray(fun(np.exp(up)), float) - r) / (fd_step * x[j])
                nfev += n
        J = Jx * x[None, :]                           # dr/du
        if not np.all(np.isfinite(J)):
            return x, r, nfev, njev, False, -1
        g = J.T @ r
        act = ((u <= lo + 1e-12) & (g > 0)) | ((u >= hi - 1e-12) & (g < 0))
        free = ~act
        pg = np.where(free, g, 0.0)
        if float(np.max(np.abs(pg))) < 1e-15 * max(1.0, np.sqrt(c)) or not free.any():
            return x, r, nfev, njev, True, 1          # stationary (possibly non-zero min)
        Jf = J[:, free]
        nf = Jf.shape[1]
        scale = float(np.sum(Jf * Jf)) / max(m, 1) + 1e-300
        while True:
            lam = mu * scale
            du = np.zeros(n)
            if m <= nf:
                du[free] = -Jf.T @ np.linalg.solve(Jf @ Jf.T + lam * np.eye(m), r)
            else:
                du[free] = -np.linalg.solve(Jf.T @ Jf + lam * np.eye(nf), pg[free])
            un = np.clip(u + du, lo, hi)
            xn = np.exp(un)
            with np.errstate(all="ignore"):
                rn = np.asarray(fun(xn), float)
            nfev += 1
            cn = float(rn @ rn) if np.all(np.isfinite(rn)) else np.inf
            if cn < c:
                small = float(np.max(np.abs(un - u))) < 1e-13
                u, x, r, c = un, xn, rn, cn
                mu = max(mu / 5.0, 1e-15)
                if small:
                    return x, r, nfev, njev, True, 3  # step below 1e-13 in log space
                break
            mu *= 8.0
            if mu > 1e12:
                return x, r, nfev, njev, True, 4      # no descent left: local minimum
    return x, r, nfev, njev, c < tol, 0               # iteration cap (TRF: max_nfev)


# Which solve the adapter is serving; set by the worker wrappers in wrap_workers().
#   "p1-ratio"    : RC-scale-invariant box -> the box is artificial, keep any root
#                   (phase1_worker rescales and checks the physical envelope itself)
#   "p1-anchored" : physical box -> an out-of-box root is a failed start
#   "p3" / "zm"   : physical resistor box -> fall back to bounded TRF when out of box
CTX = {"mode": None}


def wrap_workers(US):
    """Tag Phase-1 / Phase-3 / ZM tasks so LSAdapter knows the bound semantics."""
    p1, p3, zm = US.phase1_worker, US.phase3_worker, US.zm_worker

    def w1(task):
        CTX["mode"] = "p1-ratio" if task["mode"] == "ratio" else "p1-anchored"
        try:
            return p1(task)
        finally:
            CTX["mode"] = None

    def w3(task):
        CTX["mode"] = "p3"; CTX["task"] = task
        try:
            return p3(task)
        finally:
            CTX["mode"] = None; CTX["task"] = None

    def wz(task):
        CTX["mode"] = "zm"
        try:
            return zm(task)
        finally:
            CTX["mode"] = None
    US.phase1_worker, US.phase3_worker, US.zm_worker = w1, w3, wz
    return (p1, p3, zm)


class LSAdapter:
    """least_squares look-alike backed by lm_log (see module doc)."""
    def __init__(self, max_iter=100, tol=1e-20, fallback=True, stats=None, box=True,
                 only=None):
        self.max_iter = max_iter; self.tol = tol; self.fallback = fallback; self.box = box
        self.only = only          # None = every solve; "p1" / "p3" = only those phases
        self.p3_polish = False    # descend sens_score on the resistor manifold (Phase 3)
        self._s2 = {}
        self.stats = stats if stats is not None else {"lm": 0, "fallback": 0, "lm_oob": 0}

    def _polish_p3(self, fun, jac, x, r, lb, ub, margin=1.1):
        """Caps fixed (the task's snapped combo): move the resistors along
        {r = 0} to lower the ranking's sensitivity proxy, inside the R box shrunk
        by `margin` and within MAX_R_RATIO on the core resistors."""
        import unified_solver_v2 as US
        task = CTX.get("task")
        if task is None or x.size <= r.size:
            return x, r                          # manifold-0: nothing to trade
        name = task["topo_name"]
        if name not in self._s2:
            case = US._W["cases"][(name, "ideal")]
            self._s2[name] = (sens2_funcs(case), US.cell_layout(case))
        s2_full, lay = self._s2[name]
        if s2_full is None:
            return x, r
        caps = np.array([task["cap_combo"][c] for c in lay["cap_names"]])
        rn = lay["res_names"]
        s2_b = lambda R: s2_full(np.hstack([np.broadcast_to(caps, (R.shape[0], caps.size)), R]))
        res_b = lambda R: np.array([np.asarray(fun(v), float) for v in R])
        jac_b = lambda R: np.array([np.asarray(jac(v), float) for v in R])
        lo = np.maximum(lb * margin, lb); hi = np.minimum(ub / margin, ub)
        X0 = np.clip(x, lo, hi)[None, :]
        Xp, Sp = polish(res_b, jac_b, s2_b, X0, lo, hi, iters=15, lm_iters=20)
        xp = Xp[0]
        rp = np.asarray(fun(xp), float)
        cfg = US._W["cfg"]
        core = [xp[rn.index(k)] for k in ("R1", "R2", "R3", "R4") if k in rn]
        if (float(rp @ rp) < 1e-16 and Sp[0] < s2_b(x[None, :])[0]
                and (len(core) < 2 or max(core) / min(core) <= cfg["MAX_R_RATIO"])):
            self.stats["p3_polished"] = self.stats.get("p3_polished", 0) + 1
            return xp, rp
        return x, r

    def __call__(self, fun, x0, jac="2-point", bounds=(-np.inf, np.inf), method="trf",
                 xtol=1e-8, ftol=1e-8, max_nfev=None, **kw):
        mode = CTX["mode"] or ""
        if self.only is not None and not (
                (self.only == "p1" and mode.startswith("p1"))
                or (self.only == "p3" and mode in ("p3", "zm"))):
            return _scipy_ls(fun, x0, jac=jac, bounds=bounds, method=method, xtol=xtol,
                             ftol=ftol, max_nfev=max_nfev, **kw)
        lb, ub = bounds
        lb = np.broadcast_to(np.asarray(lb, float), np.shape(x0))
        ub = np.broadcast_to(np.asarray(ub, float), np.shape(x0))
        box = self.box
        x, r, nfev, njev, ok, st = lm_log(fun, jac if callable(jac) else None, x0,
                                          tol=self.tol, max_iter=self.max_iter,
                                          lb=lb if box else None, ub=ub if box else None)
        self.stats["lm"] += 1
        mode = CTX["mode"]
        if mode == "p1-ratio" and not box and np.all(np.isfinite(r)):
            return _mk(x, r, nfev, njev, ok, st if ok else 0, "lm_log")
        inb = np.all(x >= lb * (1 - 1e-9)) and np.all(x <= ub * (1 + 1e-9))
        if inb and np.all(np.isfinite(r)):
            if self.p3_polish and mode == "p3" and ok and callable(jac):
                x, r = self._polish_p3(fun, jac, x, r, lb, ub)
            return _mk(x, r, nfev, njev, ok, st if ok else 0, "lm_log")
        self.stats["lm_oob"] += 1
        if mode == "p1-anchored":
            return _mk(np.clip(x, lb, ub), r, nfev, njev, False, 0, "lm_log out of box")
        if not self.fallback:
            xc = np.clip(x, lb, ub)
            with np.errstate(all="ignore"):
                rc = np.asarray(fun(xc), float)
            return _mk(xc, rc, nfev + 1, njev, False, 0, "lm_log out of bounds")
        # bounded polish from the projected point (keeps the in-box best-fit semantics)
        self.stats["fallback"] += 1
        lo = np.where(np.isfinite(lb), lb, -np.inf); hi = np.where(np.isfinite(ub), ub, np.inf)
        xs = np.clip(x if np.all(np.isfinite(x)) else np.asarray(x0, float),
                     lo + np.abs(lo) * 1e-9 + 1e-300, hi - np.abs(hi) * 1e-9)
        rr = _scipy_ls(fun, xs, jac=jac, bounds=(lb, ub), method=method, xtol=xtol,
                       ftol=ftol, max_nfev=max_nfev, **kw)
        rr.nfev += nfev
        return rr


# =====================================================================
# Vectorized (batched) LM: many starts, one numpy evaluation per iteration
# =====================================================================
def batch_lm_log(res_b, jac_b, X0, tol=1e-20, max_iter=60, mu0=1e-3, lb=None, ub=None,
                 with_idx=False):
    """Vectorized projected LM in log coordinates.

    X0: (N, n) positive starts; res_b(X) -> (N, m); jac_b(X) -> (N, m, n).
    lb/ub: (n,) or (N, n) box (optional). Same step rule as lm_log, applied to
    every start at once (active bound variables get a zero Jacobian column, so the
    Tikhonov step leaves them put). Returns X (N, n), cost (N,), converged mask,
    iterations, evaluation count (vectorized calls). with_idx=True calls
    res_b(X_rows, rows) / jac_b(X_rows, rows) so per-row data (design targets)
    can be looked up."""
    if with_idx:
        _r, _j = res_b, jac_b
    else:
        _r = lambda Xs, rows: res_b(Xs)
        _j = lambda Xs, rows: jac_b(Xs)
    X0 = np.asarray(X0, float)
    N, n = X0.shape
    lo = np.broadcast_to(np.log(lb), (N, n)) if lb is not None else np.full((N, n), -np.inf)
    hi = np.broadcast_to(np.log(ub), (N, n)) if ub is not None else np.full((N, n), np.inf)
    U = np.clip(np.log(X0), lo, hi)
    X = np.exp(U)
    with np.errstate(all="ignore"):
        R = _r(X, np.arange(N))
    m = R.shape[1]
    C = np.where(np.all(np.isfinite(R), axis=1), np.einsum("ij,ij->i", R, R), np.inf)
    mu = np.full(N, mu0)
    active = np.isfinite(C) & (C >= tol)
    it = 0; nev = 1
    eye_m = np.eye(m); eye_n = np.eye(n)
    need_j = np.ones(N, bool)
    Jc = np.zeros((N, m, n))
    while it < max_iter and active.any():
        it += 1
        idx = np.nonzero(active)[0]
        jx = idx[need_j[idx]]
        if jx.size:
            with np.errstate(all="ignore"):
                Jc[jx] = _j(X[jx], jx) * X[jx][:, None, :]           # dr/du
            nev += 1
            need_j[jx] = False
        J = Jc[idx]
        good = np.all(np.isfinite(J), axis=(1, 2))
        J = np.where(good[:, None, None], J, 0.0)
        Ra = R[idx]
        g = np.einsum("kji,kj->ki", J, Ra)
        act = (((U[idx] <= lo[idx] + 1e-12) & (g > 0)) | ((U[idx] >= hi[idx] - 1e-12) & (g < 0)))
        J = np.where(act[:, None, :], 0.0, J)
        JJt = np.einsum("kij,klj->kil", J, J)
        scale = np.einsum("kii->k", JJt) / m + 1e-300
        lam = (mu[idx] * scale)[:, None, None]
        with np.errstate(all="ignore"):
            if m <= n:
                y = np.linalg.solve(JJt + lam * eye_m, Ra[..., None])[..., 0]
                dU = -np.einsum("kij,ki->kj", J, y)
            else:
                JtJ = np.einsum("kji,kjl->kil", J, J)
                gf = np.einsum("kji,kj->ki", J, Ra)
                dU = -np.linalg.solve(JtJ + lam * eye_n, gf[..., None])[..., 0]
        Un = np.clip(U[idx] + dU, lo[idx], hi[idx])
        Xn = np.exp(Un)
        with np.errstate(all="ignore"):
            Rn = _r(Xn, idx)
        nev += 1
        Cn = np.where(np.all(np.isfinite(Rn), axis=1), np.einsum("ij,ij->i", Rn, Rn), np.inf)
        acc = good & (Cn < C[idx])
        ai = idx[acc]
        stepmax = np.max(np.abs(Un - U[idx]), axis=1)
        U[ai] = Un[acc]; X[ai] = Xn[acc]; R[ai] = Rn[acc]; C[ai] = Cn[acc]
        mu[ai] = np.maximum(mu[ai] / 5.0, 1e-15)
        need_j[ai] = True
        rj = idx[~acc]
        mu[rj] *= 8.0
        done = np.zeros(N, bool)
        done[ai[stepmax[acc] < 1e-13]] = True
        done[idx[~good]] = True
        active = active & ~(C < tol) & ~(mu > 1e12) & ~done & np.isfinite(C)
    # converged = stopped by a termination criterion (tiny cost, stall, lambda
    # saturation, non-finite) rather than by the iteration cap -- TRF semantics
    return X, C, ~active | (C < tol), it, nev


def batch_funcs(case, var_names=None):
    """Vectorized residual / Jacobian callables for a case's res_eqs.

    Returns (res_b, jac_b): res_b(X) -> (N, m), jac_b(X) -> (N, m, n) with X in
    var_list order (or `var_names` order). Constant entries broadcast."""
    import sympy as sp
    vl = list(case["var_list"])
    if var_names is not None:
        by = {str(v): v for v in vl}
        vl = [by[nm] for nm in var_names]
    eqs = list(case["res_eqs"])
    m, n = len(eqs), len(vl)
    rf = sp.lambdify(vl, eqs, "numpy", cse=True)
    Jm = sp.Matrix(eqs).jacobian(vl)
    nz = [(i, j) for i in range(m) for j in range(n) if Jm[i, j] != 0]
    jf = sp.lambdify(vl, [Jm[i, j] for i, j in nz], "numpy", cse=True)
    ii = np.array([p[0] for p in nz], int); jj = np.array([p[1] for p in nz], int)

    def res_b(X):
        out = rf(*X.T)
        R = np.empty((X.shape[0], m))
        for k, v in enumerate(out):
            R[:, k] = v
        return R

    def jac_b(X):
        vals = jf(*X.T)
        J = np.zeros((X.shape[0], m, n))
        for k, v in enumerate(vals):
            J[:, ii[k], jj[k]] = v
        return J
    return res_b, jac_b


# =====================================================================
# Valley polishing: descend the ranking's sensitivity proxy ON the manifold
# =====================================================================
def sens2_funcs(case):
    """Vectorized S^2(X) with EXACTLY the ranking formula of _assemble /
    _valley_sens_proxy (1 % forward steps on a1, a2). Returns None when a1/a2
    reference a symbol outside var_list (cells with a derived component)."""
    import sympy as sp
    vl = list(case["var_list"])
    a1, a2 = case.get("a1_expr"), case.get("a2_expr")
    if a1 is None or a2 is None:
        return None
    if not (set(sp.sympify(a1).free_symbols) | set(sp.sympify(a2).free_symbols)) <= set(vl):
        return None
    f = sp.lambdify(vl, [a1, a2], "numpy", cse=True)

    def ab(X):
        v = f(*X.T)
        return (np.broadcast_to(np.asarray(v[0], float), X.shape[:1]),
                np.broadcast_to(np.asarray(v[1], float), X.shape[:1]))

    def s2(X):
        a1b, a2b = ab(X)
        tot = np.zeros(X.shape[0])
        for i in range(X.shape[1]):
            Xp = X.copy(); Xp[:, i] *= 1.01
            p1, p2 = ab(Xp)
            with np.errstate(all="ignore"):
                tot += ((p1 - a1b) / a1b / 0.01) ** 2 + ((p2 - a2b) / a2b / 0.01) ** 2
        return np.where(np.isfinite(tot), tot, np.inf)
    return s2


def polish(res_b, jac_b, s2_b, X, lb, ub, iters=25, lm_iters=30, h=1e-4):
    """Projected-gradient descent of s2_b along {r(X) = 0}: tangent step in log
    space, LM retraction back onto the manifold, per-point step control.
    Returns (X, S2) -- every returned point is a converged root in the box."""
    X = np.array(X, float)
    N, n = X.shape
    U = np.log(X)
    S = s2_b(X)
    alpha = np.full(N, 0.25)
    live = np.isfinite(S)
    for _ in range(iters):
        idx = np.nonzero(live & (alpha > 2e-3))[0]
        if idx.size == 0:
            break
        Xi, Ui, Si = X[idx], U[idx], S[idx]
        G = np.empty((idx.size, n))
        for j in range(n):
            Up = Ui.copy(); Up[:, j] += h
            G[:, j] = (s2_b(np.exp(Up)) - Si) / h
        with np.errstate(all="ignore"):
            J = jac_b(Xi) * Xi[:, None, :]
            JJt = np.einsum("kij,klj->kil", J, J) + 1e-12 * np.eye(J.shape[1])
            y = np.linalg.solve(JJt, np.einsum("kij,kj->ki", J, G)[..., None])[..., 0]
        Pg = G - np.einsum("kij,ki->kj", J, y)
        nrm = np.linalg.norm(Pg, axis=1)
        ok = np.isfinite(nrm) & (nrm > 1e-12)
        d = np.where(ok[:, None], -Pg / np.maximum(nrm, 1e-300)[:, None], 0.0)
        Ut = Ui + alpha[idx][:, None] * d
        Xr, Cr, conv, _it, _nev = batch_lm_log(res_b, jac_b, np.exp(Ut), tol=1e-20,
                                               max_iter=lm_iters, lb=lb, ub=ub)
        Sr = s2_b(Xr)
        acc = ok & conv & (Cr < 1e-16) & (Sr < Si)
        ai = idx[acc]
        X[ai] = Xr[acc]; U[ai] = np.log(Xr[acc]); S[ai] = Sr[acc]
        alpha[ai] *= 1.6
        alpha[idx[~acc]] *= 0.4
        live[idx[~ok]] = False
    return X, S


def polish_p1_results(p1_results, cases, cfg, stats=None, margin=1.25, add=True):
    """harvest() pre-pass: polish every converged Phase-1 valley (grouped per
    cell, one batch each) in physical units inside the component envelope shrunk
    by `margin` (the sensitivity minimum sits ON the envelope boundary; a root
    there does not survive cap snapping). add=True keeps the original valleys and
    APPENDS the polished copies (diversity preserved); add=False replaces them."""
    import unified_solver_v2 as US
    by = {}
    for i, r in enumerate(p1_results):
        if r:
            by.setdefault(r["topo_name"], []).append(i)
    out = list(p1_results)
    for name, ids in by.items():
        case = cases[(name, "ideal")]
        lay = US.cell_layout(case)
        s2_b = sens2_funcs(case)
        if s2_b is None:
            continue
        res_b, jac_b = batch_funcs(case)
        nC = lay["n_caps"]
        X = np.array([[p1_results[i]["caps"][c] for c in lay["cap_names"]]
                      + list(p1_results[i]["r_hint"]) for i in ids])
        lb = np.array([cfg["C_min"]] * nC + [cfg["R_min"]] * lay["n_res"]) * margin
        ub = np.array([cfg["C_max"]] * nC + [cfg["R_max"]] * lay["n_res"]) / margin
        X = np.clip(X, lb, ub)
        S0 = s2_b(X)
        Xp, Sp = polish(res_b, jac_b, s2_b, X, lb, ub)
        R = res_b(Xp)
        C = np.einsum("ij,ij->i", R, R)
        for k, i in enumerate(ids):
            if np.isfinite(Sp[k]) and C[k] < 1e-10 and Sp[k] < S0[k]:
                d = dict(p1_results[i])
                d["caps"] = dict(zip(lay["cap_names"], Xp[k, :nC]))
                d["r_hint"] = list(Xp[k, nC:])
                d["cost"] = float(C[k])
                if add:
                    d["polished"] = True
                    out.append(d)
                else:
                    out[i] = d
        if stats is not None:
            fin = np.isfinite(S0) & np.isfinite(Sp)
            stats.setdefault("polish", {})[name] = (
                float(np.sqrt(np.min(S0[fin]))) if fin.any() else None,
                float(np.sqrt(np.min(Sp[fin]))) if fin.any() else None, len(ids))
    return out


class LooseTRF:
    """scipy TRF with loosened tolerances / smaller budgets (the "tolerance lever"
    of CONTRACTS §7 applied to Phase 1 / 3 / ZM): xtol = ftol = `tol`, max_nfev
    scaled by `nfev_frac` (never below 30)."""
    def __init__(self, tol=1e-8, nfev_frac=0.25):
        self.tol = tol; self.frac = nfev_frac

    def __call__(self, fun, x0, jac="2-point", bounds=(-np.inf, np.inf), method="trf",
                 xtol=1e-8, ftol=1e-8, max_nfev=None, **kw):
        mx = None if max_nfev is None else max(30, int(max_nfev * self.frac))
        return _scipy_ls(fun, x0, jac=jac, bounds=bounds, method=method,
                         xtol=max(xtol, self.tol), ftol=max(ftol, self.tol), max_nfev=mx, **kw)
