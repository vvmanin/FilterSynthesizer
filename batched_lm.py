# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  batched_lm.py  --  vectorized projected log-space Levenberg-Marquardt
#                     (FS-028 S2-2)
#
#  WHY: the Phase-1 / Phase-3 / zero-manifold multistarts are thousands of
#  small (4-11 variable) nonlinear least-squares solves. scipy's bounded TRF
#  spent ~45 evaluations even next to a root (Coleman-Li scaling stalls every
#  variable near a bound) and ran each start as its own process-pool task.
#  Here every start of a solve runs in ONE numpy batch: one residual and one
#  Jacobian evaluation per iteration for all rows, a batched linear solve for
#  the steps. Measured in FS-028 (dev/FS-028_solver_performance_analysis.md
#  §4.4): Phase 1 ~91x, Phase 3 ~187x less CPU, on one core, no pool.
#
#  METHOD (per row, all rows at once):
#    * u = log x: every R and C is positive, steps are relative, a box
#      [lb, ub] is a box in u.
#    * Tikhonov / minimum-norm LM step: for the usual under-determined
#      coefficient systems (m <= n) dU = -J^T (J J^T + lam I)^-1 r lands on a
#      NEARBY root; for m > n the normal-equation form is used.
#      lam = mu * trace(J J^T) / m, mu /5 on an accepted step, x8 on a rejected.
#      `metric` picks the norm "nearby" is measured in (log x or x), see solve.
#    * projected active set: a variable on a bound whose descent points
#      outward is frozen for the step (its Jacobian column zeroed), the step is
#      clipped into the box. No Coleman-Li scaling -> no stall near bounds.
#    * a variable with lb == ub is FIXED (the anchor cap in Phase 1, the
#      snapped caps in Phase 3): one kernel over the full component vector
#      serves every solve mode.
#  STOP (TRF semantics, so callers keep their `success` rules):
#    status 1  cost < tol                         (root; tol=1e-20 -> |r|~1e-10)
#    status 2  accepted step with relative cost decrease <= ftol, or
#              max |dU| <= xtol                   (TRF's ftol / xtol)
#    status 3  no descent left (mu saturated, or projected gradient zero)
#    status 0  iteration cap (TRF: max_nfev)  -> not converged
#    status -1 non-finite residual / Jacobian -> not converged
#  converged = status > 0.
# =====================================================================

import os

import numpy as np

MU_MAX = 1e12
MU_MIN = 1e-15


def legacy_trf():
    """True when the pre-S2-2 solve path (scipy TRF, Phase-1/3 process pool,
    per-row non-ideal TRF) is selected: environment FS_SOLVER=trf. Kept as a
    fallback while the batched solver is validated; read at call time."""
    return os.environ.get("FS_SOLVER", "").strip().lower() == "trf"


class Result:
    """X (N, n) solution, cost (N,) sum of squared residuals (inf if
    non-finite), status (N,) (see module doc), conv (N,) = status > 0,
    nit = iterations run."""
    __slots__ = ("X", "cost", "status", "conv", "nit")

    def __init__(self, X, cost, status, nit):
        self.X = X
        self.cost = cost
        self.status = status
        self.conv = status > 0
        self.nit = nit


def _solve_stack(A, b):
    """Batched A x = b; a singular matrix yields x = 0 for that row only (the
    step is then rejected and mu grows) instead of failing the whole batch."""
    try:
        return np.linalg.solve(A, b[..., None])[..., 0]
    except np.linalg.LinAlgError:
        out = np.zeros_like(b)
        for k in range(A.shape[0]):
            try:
                out[k] = np.linalg.solve(A[k], b[k])
            except np.linalg.LinAlgError:
                pass
        return out


def fd_jac_log(fun, X, R, rows, cols, h=1.5e-8):
    """Forward-difference dr/du (u = log x) for the columns `cols` -> (k, m,
    len(cols)); every row and column in ONE call of `fun`."""
    k, n = X.shape
    m = R.shape[1]
    cols = list(cols)
    if not cols:
        return np.zeros((k, m, 0))
    Xp = np.repeat(X[None, :, :], len(cols), axis=0)          # (c, k, n)
    for a, j in enumerate(cols):
        Xp[a, :, j] = X[:, j] * np.exp(h)
    Rp = fun(Xp.reshape(-1, n), np.tile(rows, len(cols)))
    Rp = np.asarray(Rp, dtype=float).reshape(len(cols), k, m)
    return np.moveaxis((Rp - R[None]) / h, 0, -1)


def solve(fun, jac, X0, lb, ub, tol=1e-20, ftol=None, xtol=1e-13, max_iter=100,
          mu0=1e-3, metric="log"):
    """Minimize ||fun(x)||^2 over lb <= x <= ub (x > 0) for every row of X0.

    fun(X, rows) -> (k, m) residuals of the rows `rows` (indices into X0, for
                    per-row data) at X (k, n).
    jac(X, rows) -> (k, m, n) dr/dx, or None for forward differences in log
                    space (n extra rows per Jacobian, one `fun` call).
    X0           -> (N, n) positive starts; clipped into the box.
    lb, ub       -> (n,) or (N, n); lb == ub fixes that variable.
    tol          -> stop at sum(r^2) < tol.
    ftol, xtol   -> TRF-like termination on an accepted step, scalars or (N,)
                    per row (ftol None = off).
    metric       -> the norm the minimum-norm step minimises, which decides
                    WHICH root of an under-determined system a start reaches:
                    "log" = |dU| (equal relative moves; keeps a start's
                    component ratios), "x" = |dX| (as TRF / Gauss-Newton in
                    x: the small components move most). Both steps are taken
                    in u = log x, so positivity and the box are handled alike.
    Returns a Result."""
    X0 = np.asarray(X0, dtype=float)
    N, n = X0.shape
    LBa = np.broadcast_to(np.asarray(lb, dtype=float), (N, n))
    with np.errstate(divide="ignore"):
        lo = np.log(LBa)
        hi = np.array(np.broadcast_to(np.log(np.asarray(ub, dtype=float)), (N, n)))
    fixed = (hi - lo) <= 1e-15
    # columns fixed in EVERY row (the snapped caps of Phase 3 / ZM) never enter
    # the step: the iteration works on the remaining columns `fc` only
    fc = np.nonzero(~fixed.all(axis=0))[0]
    ftol = None if ftol is None else np.broadcast_to(np.asarray(ftol, dtype=float), (N,))
    xtol = np.broadcast_to(np.asarray(xtol, dtype=float), (N,))
    with np.errstate(divide="ignore", invalid="ignore"):
        U = np.clip(np.log(X0), lo, hi)
    # a fixed variable keeps its exact value (no exp(log(x)) rounding)
    X = np.where(fixed, LBa, np.exp(U))
    status = np.zeros(N, dtype=np.int8)
    with np.errstate(all="ignore"):
        R = np.asarray(fun(X, np.arange(N)), dtype=float)
    m = R.shape[1]
    fin = np.all(np.isfinite(R), axis=1)
    C = np.where(fin, np.einsum("ij,ij->i", R, R), np.inf)
    status[~fin] = -1
    status[fin & (C < tol)] = 1
    if fc.size == 0:                                # nothing to move anywhere
        status[status == 0] = 3
        return Result(X, C, status, 0)
    # working set: the still-active rows, compacted as rows finish; finished
    # rows are written back to X / C / status
    w = np.nonzero(status == 0)[0]
    Uw, low, hiw = U[w][:, fc], lo[w][:, fc], hi[w][:, fc]
    fxw = fixed[w][:, fc]
    Xw, Rw, Cw = X[w], R[w], C[w]
    muw = np.full(w.size, float(mu0))
    Jw = np.zeros((w.size, m, fc.size))
    need = np.ones(w.size, dtype=bool)
    ftw = None if ftol is None else ftol[w]
    xtw = xtol[w]
    nf = fc.size
    eye = np.eye(m) if m <= nf else np.eye(nf)
    it = 0
    while it < max_iter and w.size:
        it += 1
        jx = np.nonzero(need)[0]
        if jx.size:
            with np.errstate(all="ignore"):
                if jac is None:
                    Jw[jx] = fd_jac_log(fun, Xw[jx], Rw[jx], w[jx], fc)
                else:
                    Jn = np.asarray(jac(Xw[jx], w[jx]), dtype=float)[:, :, fc]
                    Jw[jx] = Jn * Xw[jx][:, fc][:, None, :]
            need[jx] = False
        good = np.all(np.isfinite(Jw), axis=(1, 2))
        g = np.einsum("kji,kj->ki", Jw, Rw)
        act = (fxw
               | ((Uw <= low + 1e-12) & (g > 0))
               | ((Uw >= hiw - 1e-12) & (g < 0))
               | ~good[:, None])
        J = np.where(act[:, None, :], 0.0, Jw)
        pg = np.where(act, 0.0, g)
        # stationary on the box (every free direction flat, or none left)
        stat = good & (np.max(np.abs(pg), axis=1) < 1e-15 * np.maximum(1.0, np.sqrt(Cw)))
        with np.errstate(all="ignore"):
            # step metric: "log" = min-norm in u (equal relative moves);
            # "x" = min-norm in x (TRF-like: small components move most)
            if metric == "x":
                Sc = 1.0 / Xw[:, fc]
                J = J * Sc[:, None, :]
            if m <= nf:
                JJt = np.einsum("kij,klj->kil", J, J)
                scale = np.einsum("kii->k", JJt) / m + 1e-300
                y = _solve_stack(JJt + (muw * scale)[:, None, None] * eye, Rw)
                dU = -np.einsum("kij,ki->kj", J, y)
            else:
                JtJ = np.einsum("kji,kjl->kil", J, J)
                scale = np.einsum("kij,kij->k", J, J) / m + 1e-300
                rhs = pg * Sc if metric == "x" else pg
                dU = -_solve_stack(JtJ + (muw * scale)[:, None, None] * eye, rhs)
            if metric == "x":
                dU = dU * Sc
            dU = np.where(np.isfinite(dU), dU, 0.0)
            Un = np.clip(Uw + dU, low, hiw)
            Xn = Xw.copy()
            Xn[:, fc] = np.where(fxw, Xw[:, fc], np.exp(Un))
            Rn = np.asarray(fun(Xn, w), dtype=float)
        fin = np.all(np.isfinite(Rn), axis=1)
        Cn = np.where(fin, np.einsum("ij,ij->i", Rn, Rn), np.inf)
        acc = (Cn < Cw) & good & ~stat
        step = np.max(np.abs(Un - Uw), axis=1)
        st = np.zeros(w.size, dtype=np.int8)
        st[~good] = -1
        st[stat] = 3
        rel = (Cw - Cn) <= ftw * Cw if ftw is not None else np.zeros(w.size, dtype=bool)
        Uw[acc] = Un[acc]
        Xw[acc] = Xn[acc]
        Rw[acc] = Rn[acc]
        Cw[acc] = Cn[acc]
        muw[acc] = np.maximum(muw[acc] / 5.0, MU_MIN)
        need[acc] = True
        rej = good & ~stat & ~acc
        muw[rej] *= 8.0
        st[acc & ((step <= xtw) | rel)] = 2
        st[acc & (Cn < tol)] = 1
        st[rej & (muw > MU_MAX)] = 3
        done = st != 0
        if done.any():
            wd = w[done]
            X[wd], C[wd], status[wd] = Xw[done], Cw[done], st[done]
            keep = ~done
            w = w[keep]
            Uw, low, hiw, fxw = Uw[keep], low[keep], hiw[keep], fxw[keep]
            Xw, Rw, Cw, muw = Xw[keep], Rw[keep], Cw[keep], muw[keep]
            Jw, need, xtw = Jw[keep], need[keep], xtw[keep]
            if ftw is not None:
                ftw = ftw[keep]
    X[w], C[w] = Xw, Cw                             # iteration cap: status stays 0
    return Result(X, C, status, it)
