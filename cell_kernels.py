# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cell_kernels.py  —  compile-once numeric kernels of the Tier-B cells
#                      (FS-028 S2-1)
#
#  WHY: every section solve used to re-derive each cell's ideal case for the
#  numeric design targets (3-6 s per 3rd-order cell) -- in run_synthesis, again
#  for the snapper, again in every non-ideal worker -- and every Phase-1/3
#  worker re-lambdified its residuals, Jacobian, a1/a2, H(0) and H(inf) (up to
#  ~6 s per worker, x n_cores). Now each cell is derived ONCE with symbolic
#  targets (tf_derivation_v2.get_templates), its functions are lambdified once
#  here, and a worker rebuilds them by exec'ing the generated Python source:
#  ~ms, no sympy work, no derivation.
#
#  WHAT: groups of kernels per (cell, Equalize variant):
#     "gain" : a1(vl), a2(vl), h0(vl), hinf(vl)    -- the worker's gain / sens set
#     "sens" : a1(tfv), a2(tfv)                    -- harvest's valley proxy
#     "zm"   : den_i(tfv), num_i(tfv)              -- zero-manifold coefficients
#     "res"  : res(vl + T), jac(vl + T), r5(vl + T) -- DESIGN-PARAMETRIC residuals
#  and, per design, `design_sources`: res(vl), jac(vl), r5(vl) with the numeric
#  targets substituted (vl = case["var_list"], tfv = case["tf_var_list"],
#  T = TARGETS). The first three are design-independent and cached on disk.
#
#  WHY TWO RESIDUAL FORMS: the Phase-1/3 TRF path uses the per-design form.
#  template.subs(design) is srepr-identical to the old per-design derivation
#  for every cell (build_ideal's last step IS that subs), so the lambdified
#  code -- and every number the solver produces -- is bit-identical to before.
#  The design-parametric "res" form agrees only to rounding (~1e-13): sympy no
#  longer folds the numbers into the expression. That is the same math, but the
#  TRF multistart is chaotic -- rounding-level changes re-sample which valleys
#  failing starts fall into, and BOM lists reshuffle like a reseed (measured in
#  FS-028: best sens 26 same / 3 better / 2 worse over 31 sections, for ~10 %
#  more speed). "res" is the form the batched solver (S2-2) and learned seeds
#  (S2-3) need; they change results anyway and are validated on their own.
#  Per-design kernels cost 0.01-2.7 s of lambdify per cell and design, in the
#  orchestrator only, memoized per process (re-solves of a design are free).
#
#  REPLAY: a lambdified function runs in lambdify's own "numpy" namespace. The
#  loader rebuilds that namespace (the globals of a trivial lambdify) and binds
#  any free cell symbol the code references to the equal tf_symbols Symbol that
#  lambdify bound. The generator checks both, so a replayed kernel behaves like
#  the live one; anything it cannot replay raises at generation time.
#
#  CACHE: <tf cache stem>_kernels_k<KERNEL_REV>.json next to the TF cache (same
#  CWD rule, same tf_cache*.json gitignore). Keys carry the template key (cell
#  name + structural signature + IDEAL/NONIDEAL model revisions) and the
#  variant; the file name carries the TF cache version and KERNEL_REV. So every
#  documented invalidation of the TF cache (CONTRACTS §1) invalidates the
#  kernels too. Bump KERNEL_REV when the generator below changes. Per-design
#  kernels are memory-only (an LRU), so the file never grows with designs.
# =====================================================================

import builtins
import inspect
import os
import threading
from collections import OrderedDict

import tf_derivation_v2 as TF
import tf_symbols as _SYM

KERNEL_REV = 1

# Order of the design-target arguments appended after the components.
TARGETS = TF.TARGETS

GROUPS = ("gain", "sens", "zm", "res")

_SRC = {}                  # kernel key -> {fname: {"src", "syms"}}  (this process)
_FNS = {}                  # source set -> {fname: callable}          (this process)
_DESIGN = OrderedDict()    # (kernel key, targets) -> per-design sources (LRU)
_DESIGN_MAX = 64
_BASE_NS = None
_LOCK = threading.RLock()
_MISSING = object()


def cache_path():
    stem, ext = os.path.splitext(TF.CACHE_PATH_V2)
    return f"{stem}_kernels_k{KERNEL_REV}{ext or '.json'}"


# =====================================================================
# Keys
# =====================================================================
def variant(case):
    """'eq' for an AM case with the Equalize substitution applied
    (unified_solver_v2.apply_equalize marks it), else 'raw'."""
    return "eq" if case.get("equalized") else "raw"


def kernel_key(case, group):
    topo = case["topo"]
    name = TF.topo_name(topo)
    return f"{TF.template_key(name, topo)}|{variant(case)}|{group}"


# =====================================================================
# Generation (main process; needs sympy)
# =====================================================================
def _code_names(co):
    out = set(co.co_names)
    for k in co.co_consts:
        if hasattr(k, "co_names"):
            out |= _code_names(k)
    return out


def _lamb(args, expr, fname):
    """Lambdify exactly as the solver always did (numpy, cse=True) and return
    {"src": source renamed to `fname`, "syms": free tf_symbols it references}."""
    import sympy as sp
    f = sp.lambdify(list(args), expr, "numpy", cse=True)
    src = inspect.getsource(f)
    head = "def _lambdifygenerated("
    if head not in src:
        raise RuntimeError(f"cell_kernels: unexpected lambdify source for {fname}")
    src = src.replace(head, f"def {fname}(", 1)
    base = _base_namespace()
    syms = []
    for nm in sorted(_code_names(f.__code__)):
        if nm not in f.__globals__:
            continue                         # attribute name or a builtin
        obj = f.__globals__[nm]
        if base.get(nm, _MISSING) is obj:
            continue
        ref = getattr(_SYM, nm, _MISSING)
        if isinstance(obj, sp.Symbol) and isinstance(ref, sp.Symbol) and obj == ref:
            # free cell symbol (never an argument). Equal, not identical: a
            # template loaded from the TF cache rebuilds its symbols via sympify.
            syms.append(nm)
            continue
        raise RuntimeError(f"cell_kernels: {fname} references {nm!r}, which the "
                           "kernel loader cannot rebuild")
    return {"src": src, "syms": syms}


def _generate(case, group):
    import sympy as sp
    s = TF.s
    vl = list(case["var_list"])
    tfv = list(case["tf_var_list"])
    T = [getattr(_SYM, n) for n in TARGETS]
    items = []                               # (fname, args, expr)
    if group == "gain":
        num, den = case["tf_num"], case["tf_den"]
        items.append(("a1", vl, case["a1_expr"]))
        items.append(("a2", vl, case["a2_expr"]))
        items.append(("h0", vl, num.subs(s, 0) / den.subs(s, 0)))
        items.append(("hinf", vl, sp.Poly(num, s).LC() / sp.Poly(den, s).LC()))
    elif group == "sens":
        items.append(("a1", tfv, case["a1_expr"]))
        items.append(("a2", tfv, case["a2_expr"]))
    elif group == "zm":
        for i, c in enumerate(sp.Poly(case["tf_den"], s).all_coeffs()):
            items.append((f"den_{i}", tfv, c))
        for i, c in enumerate(sp.Poly(case["tf_num"], s).all_coeffs()):
            items.append((f"num_{i}", tfv, c))
    elif group == "res":
        items.append(("res", vl + T, case["res_eqs"]))
        items.append(("jac", vl + T, sp.Matrix(case["res_eqs"]).jacobian(vl)))
        if case.get("R5_constraint") is not None:
            items.append(("r5", vl + T, case["R5_constraint"]))
    else:
        raise ValueError(f"unknown kernel group {group!r}")
    return {fname: _lamb(args, expr, fname) for fname, args, expr in items}


def sources(case, group):
    """Design-independent kernel sources of one group (GROUPS) for a case from
    tf_derivation_v2.design_cases (optionally equalized). Memoized per process,
    cached on disk, generated at most once per cell and variant. Plain data
    (picklable): hand it to a worker and `load` it there."""
    key = kernel_key(case, group)
    with _LOCK:
        hit = _SRC.get(key)
        if hit is not None:
            return hit
        path = cache_path()
        hit = TF._load_blob(path)["cells"].get(key)
        if hit is None:
            hit = _generate(case, group)
            blob = TF._load_blob(path)       # re-read: keep entries written meanwhile
            blob["cells"][key] = hit
            TF._save_blob(blob, path)
        _SRC[key] = hit
        return hit


def design_sources(case):
    """Per-design residual kernels res(vl), jac(vl), r5(vl) for the case's own
    numeric `targets` -- exactly the functions the old per-design path built:
    the targets are substituted into the RAW template residuals first and the
    Equalize substitution (if any) after, the order derive-then-apply_equalize
    used, so the lambdified code is identical. Memoized per process (LRU)."""
    import sympy as sp
    name = TF.topo_name(case["topo"])
    key = (kernel_key(case, "design"), tuple(float(t).hex() for t in case["targets"]))
    with _LOCK:
        hit = _DESIGN.get(key)
        if hit is not None:
            _DESIGN.move_to_end(key)
            return hit
        subs = {getattr(_SYM, n): v for n, v in zip(TARGETS, case["targets"])}
        raw = TF.get_templates([name])[(name, "ideal")]
        res = [e.subs(subs) for e in raw["res_eqs"]]
        eq = case.get("equalize_subs")
        if eq:
            res = [e.subs(eq) for e in res]
        vl = list(case["var_list"])
        items = [("res", vl, res), ("jac", vl, sp.Matrix(res).jacobian(vl))]
        if case.get("R5_constraint") is not None:
            items.append(("r5", vl, case["R5_constraint"].subs(subs)))
        hit = {fname: _lamb(args, expr, fname) for fname, args, expr in items}
        _DESIGN[key] = hit
        while len(_DESIGN) > _DESIGN_MAX:
            _DESIGN.popitem(last=False)
        return hit


# =====================================================================
# Replay (any process; no derivation, no lambdify)
# =====================================================================
def _base_namespace():
    """The globals lambdify gives a numpy-module function (built once)."""
    global _BASE_NS
    if _BASE_NS is None:
        import sympy as sp
        ns = dict(sp.lambdify([], 0, "numpy").__globals__)
        ns.pop("_lambdifygenerated", None)
        ns.setdefault("builtins", builtins)
        _BASE_NS = ns
    return _BASE_NS


def load(srcs):
    """{fname: callable} from `sources` / `design_sources` output. Compiled once
    per process per distinct source set."""
    key = tuple(sorted((k, v["src"]) for k, v in srcs.items()))
    with _LOCK:
        hit = _FNS.get(key)
        if hit is not None:
            return hit
        out = {}
        for fname, item in srcs.items():
            ns = dict(_base_namespace())
            for nm in item.get("syms", ()):
                ns[nm] = getattr(_SYM, nm)
            exec(compile(item["src"], f"<cell_kernel {fname}>", "exec"), ns)
            out[fname] = ns[fname]
        _FNS[key] = out
        return out


def bind(fn, targets):
    """fn(*components, *targets) -> f(*components), for the "res" group.
    None stays None."""
    if fn is None:
        return None
    T = tuple(float(t) for t in targets)

    def f(*x):
        return fn(*x, *T)
    return f
