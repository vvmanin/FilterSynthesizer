# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  am_mna.py  —  numeric modified-nodal-analysis (MNA) evaluator for the
#  Ackerberg-Mossberg NON-IDEAL response (S3, the structural fix).
#
#  WHY: cells_am_core.build_nonideal_common forms the closed-loop response by
#  A.LUsolve(b) + sp.together on the full 7-unknown non-ideal nodal system. For
#  the 3rd-order NOTCH cells (3LPn-AM / 3HPn-AM) that solved transfer function is
#  a ~766k-op, ~10 MB nested rational — catastrophic to build, lambdify, srepr
#  and parse. But NOTHING downstream needs the symbolic solved form to *evaluate*
#  the response: it only needs H(jw) at a numeric grid for numeric component
#  values. This module builds the SMALL nodal matrix M(s) (each entry a tiny
#  expression, degree <= a few in s) from the EXACT same am_eqs used by the
#  symbolic path, lambdifies the entries, and solves M(jw)x = b numerically per
#  frequency (batched np.linalg.solve). Same equations -> identical response, but
#  no giant symbolic object anywhere.
#
#  It is used ONLY for the AM non-ideal path; every other family keeps its
#  existing (tiny, fast) symbolic route untouched.
# =====================================================================

import numpy as np
import sympy as sp

import cells_am_core as _core
from tf_symbols import s, A_ol, GBWP_hz, R8
# op-amp input-node symbols live in cells_am_core (module-local there)
from cells_am_core import Vm1, Vp2, Vm3


def _nodal_system(topo):
    """Symbolic (A, b, unknowns, tap_index) for the non-ideal AM nodal system —
    byte-for-byte the construction cells_am_core.build_nonideal_common uses, up to
    (but NOT including) the LUsolve/together that creates the giant TF."""
    wc = 2 * sp.pi * GBWP_hz
    A_s = A_ol * wc / (wc + s * A_ol)
    srcs = (-A_s * Vm1, A_s * Vp2, -A_s * Vm3)     # U1:+(gnd)-(m1); U2:+(p2)-(gnd); U3:+(gnd)-(m3)
    eqs = _core.am_eqs(topo, (Vm1, Vp2, Vm3), opamp_srcs=srcs, r8_sym=R8)
    unk = _core.am_unknowns(topo, nonideal=True)
    A, b = sp.linear_eq_to_matrix(eqs, unk)
    tap_idx = unk.index(_core.tap_symbol(topo))
    return A, b, unk, tap_idx


def build_mna_response(topo):
    """Return (H, names) with the SAME signature as
    tf_derivation_v2.make_response_func's result: H(comp_dict, w) -> complex array,
    `names` the component-symbol names H reads from comp_dict.

    Cost is set by the SMALL matrix entries (tens of trivial lambdifies), not by
    the solved determinant, so 3rd-order AM notch builds/evaluates as cheaply as
    any other cell."""
    A, b, unk, tap_idx = _nodal_system(topo)
    n = len(unk)

    syms = sorted(
        {x for x in (A.free_symbols | b.free_symbols) if x != s and not x.is_number},
        key=str,
    )
    args = [s] + list(syms)
    # entrywise lambdify: each entry is tiny (a few conductance/admittance terms)
    A_fns = [[sp.lambdify(args, A[i, j], "numpy", cse=True) for j in range(n)]
             for i in range(n)]
    b_fns = [sp.lambdify(args, b[i], "numpy", cse=True) for i in range(n)]
    names = [str(v) for v in syms]

    def H(comp, w):
        jw = 1j * np.asarray(w, dtype=float)
        nf = jw.shape[0]
        vals = [comp[nm] for nm in names]
        Amat = np.empty((nf, n, n), dtype=complex)
        for i in range(n):
            for j in range(n):
                Amat[:, i, j] = np.broadcast_to(A_fns[i][j](jw, *vals), (nf,))
        bvec = np.empty((nf, n, 1), dtype=complex)  # stack of column vectors
        for i in range(n):
            bvec[:, i, 0] = np.broadcast_to(b_fns[i](jw, *vals), (nf,))
        x = np.linalg.solve(Amat, bvec)         # (nf, n, 1)
        return x[:, tap_idx, 0]

    return H, names
