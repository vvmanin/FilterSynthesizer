# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  tf_derivation_v2.py  —  generic symbolic engine + family registry
#
#  Refactored for the registry seam (ROADMAP §3). This module no longer
#  hardcodes any one family's nodal equations; it is the FAMILY-AGNOSTIC
#  engine that:
#     - holds the REGISTRY of per-family cell modules (cells_lp,
#       cells_hp, ...),
#     - dispatches derive_ideal / derive_nonideal / topo_name /
#       dc_gain_to_K by topo["family"],
#     - keeps the shared, family-independent machinery: make_response_func,
#       cell_components, the incremental per-cell cache, and self_test,
#     - re-exports every shared symbol from tf_symbols so existing
#       `from tf_derivation_v2 import p1, w0, ...` / `TF.s` call sites
#       keep working unchanged.
#
#  A cell module contributes (the seam interface):
#     FAMILY, all_cells(), topo_name(topo), var_list(topo),
#     dc_gain_to_K(topo, design_subs, gain),
#     build_ideal(topo, target_subs), build_nonideal(topo).
#
#  CACHE (v3): incremental and per-cell, so an LP-only run never pays HP
#  derivation cost and vice-versa (no 2x regression from adding a family).
#     - ideal cells are keyed by (design, name)   — res_eqs depend on the
#       numeric design targets,
#     - non-ideal cells are keyed by (name) alone — the raw TF is
#       design-independent, so it persists across design changes.
#  Pass topo_names=[...] to derive/return only the cells you need.
# =====================================================================

import os
import json
import time
import hashlib
import sympy as sp

# Shared symbols (re-exported below for backward-compatible imports)
from tf_symbols import *          # noqa: F401,F403  (s, p1, w0, wz, Q, K, R*, C*, ...)
import tf_symbols as _SYM

# Per-family cell modules
import cells_lp
import cells_hp
import cells_notch
import cells_bp
import cells_mfb
import cells_mfb_hp
import cells_mfb_bp
import cells_mfb_notch
import cells_am
import cells_am_hp
import cells_am_bp
import cells_am_notch

# ------------------------------------------------------------------
# Registry: family tag -> cell module. New families (BP, ...) register
# here; nothing else in Tier C needs to change.
# ------------------------------------------------------------------
REGISTRY = {
    cells_lp.FAMILY: cells_lp,
    cells_hp.FAMILY: cells_hp,
    cells_notch.FAMILY: cells_notch,
    cells_bp.FAMILY: cells_bp,
    cells_mfb.FAMILY: cells_mfb,
    cells_mfb_hp.FAMILY: cells_mfb_hp,
    cells_mfb_bp.FAMILY: cells_mfb_bp,
    cells_mfb_notch.FAMILY: cells_mfb_notch,
    cells_am.FAMILY: cells_am,
    cells_am_hp.FAMILY: cells_am_hp,
    cells_am_bp.FAMILY: cells_am_bp,
    cells_am_notch.FAMILY: cells_am_notch,
}

CACHE_PATH_V2 = "tf_cache_v5.json"     # bumped v4->v5: AM non-ideal cases are now
                                       # lightweight (MNA, no giant symbolic TF), so
                                       # any stale v4 entry holding a ~10 MB non-ideal
                                       # srepr must not be loaded and re-sympified.
                                       # (v3->v4 had added the per-cell structural
                                       # signature to _ni_key.)


# =====================================================================
# Registry dispatch helpers
# =====================================================================
def _module_for(topo):
    fam = topo.get("family", "LP")
    try:
        return REGISTRY[fam]
    except KeyError:
        raise ValueError(f"no cell module registered for family {fam!r} "
                         f"(have {sorted(REGISTRY)})")


def all_cells(families=None):
    """All topology cells across the requested families (default: every
    registered family). Each topo dict carries its own 'family' tag."""
    fams = list(REGISTRY) if families is None else list(families)
    out = []
    for fam in fams:
        out.extend(REGISTRY[fam].all_cells())
    return out


def topo_name(topo):
    return _module_for(topo).topo_name(topo)


def dc_gain_to_K(topo, design_subs, dc_gain):
    return _module_for(topo).dc_gain_to_K(topo, design_subs, dc_gain)


def derive_ideal(topo, target_subs):
    return _module_for(topo).build_ideal(topo, target_subs)


def derive_nonideal(topo):
    return _module_for(topo).build_nonideal(topo)


def analytic_seeds(topo, design):
    """OPTIONAL per-cell closed-form Phase-1 starts.

    A cell module may define analytic_seeds(topo, design) returning a list of
    {component_name: value} dicts that are exact (or near-exact) roots of its
    residual system, expressed at an arbitrary RC scale -- unified_solver_v2
    normalises them into the scale-invariant Phase-1 "ratio" box before use.
    `design` is the float design map keyed by symbol NAME ("p1","w0","wz","Q","K").

    Cells that do not define it (every family except BP-MFB's 3rd-order cells
    today) return [] here, so the solver's start set is completely unchanged for
    them. Seeds are strictly ADDITIVE to the legacy ratio/anchored passes.
    """
    fn = getattr(_module_for(topo), "analytic_seeds", None)
    if fn is None:
        return []
    try:
        return list(fn(topo, design) or [])
    except Exception:
        return []            # a bad seed must never break a synthesis run


# name -> topo lookup across all registered families (built lazily)
_NAME_TO_TOPO = None

def _name_to_topo():
    global _NAME_TO_TOPO
    if _NAME_TO_TOPO is None:
        _NAME_TO_TOPO = {topo_name(t): t for t in all_cells()}
    return _NAME_TO_TOPO

def topo_for_name(name):
    """Resolve a cell name (e.g. '2HPn-gained') back to its topo dict."""
    return _name_to_topo()[name]


def _cell_struct_sig(topo):
    """Short hash of a cell's component set (its var_list), so a cell whose
    TOPOLOGY changes under the SAME name gets a DIFFERENT non-ideal cache key.
    A stale non-ideal TF (e.g. one derived before a component like C3/R5 was
    added to the cell) then can never be served. Design-independent, like the
    non-ideal TF itself."""
    try:
        comps = sorted(str(v) for v in _module_for(topo).var_list(topo))
    except Exception:
        comps = []
    return hashlib.md5("|".join(comps).encode()).hexdigest()[:10]


def _ni_key(name, topo):
    """Cache key for a cell's (design-independent) non-ideal TF, salted with the
    cell's structural signature so a same-name topology change invalidates it."""
    if topo is None:
        return f"{name}|nonideal"
    return f"{name}|{_cell_struct_sig(topo)}|nonideal"


def cell_struct_sig(name_or_topo):
    """PUBLIC structural signature of a cell (md5 of its sorted component set).

    Any cache keyed on a cell NAME must also carry this salt. A cell's netlist
    can change under a fixed name across versions -- e.g. the LP-notch LS branch
    renamed its op-amp feedback cap C4 -> C3 when the a->out cap was dropped --
    and a name-only key then serves a STALE transfer function. That failure is
    silent rather than loud, because absent designators are written into BOM rows
    as 0.0 (see unified_solver_v2._assemble), so the stale TF happily evaluates
    with a zero capacitor instead of raising. Result: the notch vanishes and the
    stopband plateau lifts, with no error anywhere.

    `_ni_key` already salts the on-disk non-ideal cache this way; the Streamlit
    `@st.cache_resource` lambdify caches in topology_tab / response_tab use this
    helper for the same reason."""
    topo = name_or_topo
    if isinstance(topo, str):
        try:
            topo = topo_for_name(topo)
        except KeyError:
            return "unknown"
    return _cell_struct_sig(topo)


# =====================================================================
# Component-name helper (family-agnostic)
# =====================================================================
def cell_components(case):
    """Physical component names present in a cell. R5 is included when it is
    a real element (notch via algebraic constraint, or notchless-gained as a
    free var) and omitted for unity/atten followers where R5 is shorted."""
    names = [str(v) for v in case["var_list"]]
    caps = [n for n in names if n.startswith("C")]
    resistors = [n for n in names if n.startswith("R")]
    if case.get("R5_constraint") is not None and "R5" not in resistors:
        resistors = resistors + ["R5"]      # derived component, still physical
    resistors = sorted(set(resistors))
    return {"caps": caps, "resistors": resistors}


# =====================================================================
# Frequency-response evaluator (family-agnostic)
# =====================================================================
# Process-local memo of compiled response functions (S1). A cell's tf_num/tf_den
# (hence its lambdified fn/fd) are DESIGN- and K-INDEPENDENT — they are pure
# symbolic functions of the R/C symbols; the numeric design targets and per-cell
# K enter only res_eqs, never the TF (verified across every family). So one
# compilation per (cell, model) is reused for every design, every candidate, and
# every re-score in a process. Without this, make_response_func re-lambdified on
# EVERY call — invisible for the tiny MFB/VCVS/2nd-order-AM TFs (0.02–0.15 s) but
# ~1.5 s EACH for the ~766k-op 3rd-order-AM notch TF, paid once per scored
# candidate (top_k times) plus once per snapper topology. This memo collapses that
# to a single compile. It changes nothing numerically: the SAME fn/fd are returned.
_RESP_CACHE = {}


def _resp_key(case):
    """Cheap, faithful identity for a case's compiled response. Returns None when a
    confident key can't be formed (then we DON'T cache — a safe fallback that only
    ever costs a re-lambdify of an already-tiny TF, e.g. first-order cells whose
    case shape has no registry topo). Never risks serving a wrong function.

    The key mirrors the on-disk cache salt (_ni_key / cell_struct_sig): cell name +
    model + structural signature, plus the exact var-name interface. Two cells with
    the same name+structure have identical TFs, so this is a tight, correct key that
    is stable across designs and K-modes."""
    topo = case.get("topo")
    kind = case.get("kind")
    if topo is None or kind is None:
        return None
    try:
        name = topo_name(topo)
        sig = _cell_struct_sig(topo)
        vnames = tuple(str(v) for v in case["tf_var_list"])
    except Exception:
        return None
    return (name, str(kind), sig, vnames)


def make_response_func(case):
    import numpy as np
    key = _resp_key(case)
    hit = _RESP_CACHE.get(key) if key is not None else None
    if hit is not None:
        return hit

    # S3: AM non-ideal cases carry no solved symbolic TF; evaluate the response
    # numerically from the small nodal system (identical result, no giant object).
    # Any failure falls back to the symbolic route (deriving the TF on demand).
    if case.get("mna"):
        try:
            import am_mna
            out = am_mna.build_mna_response(case["topo"])
            if key is not None:
                _RESP_CACHE[key] = out
            return out
        except Exception:
            ensure_symbolic_tf(case)              # fill tf_num/tf_den, then symbolic path

    syms = [_SYM.s] + list(case["tf_var_list"])
    fn = sp.lambdify(syms, case["tf_num"], "numpy", cse=True)
    fd = sp.lambdify(syms, case["tf_den"], "numpy", cse=True)
    names = [str(v) for v in case["tf_var_list"]]
    def H(comp, w):
        vals = [comp[n] for n in names]
        jw = 1j*np.asarray(w, dtype=float)
        return fn(jw, *vals) / fd(jw, *vals)
    out = (H, names)
    if key is not None:
        _RESP_CACHE[key] = out
    return out


def ensure_symbolic_tf(case):
    """Fill a lightweight (MNA) AM non-ideal case's symbolic tf_num/tf_den/
    tf_var_list on demand, and return the case. No-op for cases that already carry
    the symbolic form or that were never MNA (every non-AM family). Used only by
    the rare consumer that needs the closed-form TF — the optional analytic
    group-delay overlay — so the ~766k-op solve is paid at most once, lazily, and
    never on the solver's hot path. Idempotent; mutates the case in place."""
    if case.get("tf_num") is not None or not case.get("mna"):
        return case
    import cells_am_core as _amc                  # mna is only ever set by the AM core
    sym = _amc.build_nonideal_symbolic(case["topo"])
    case["tf_num"] = sym["tf_num"]
    case["tf_den"] = sym["tf_den"]
    case["tf_var_list"] = sym["tf_var_list"]
    return case


# =====================================================================
# Derive a set of cells (ideal + non-ideal)
# =====================================================================
def derive_all(design_subs, verbose=True, k_map=None, names=None):
    """Derive the requested cells (default: every cell in every registered
    family). If k_map is given (topo_name -> K), each gained/atten cell is
    derived with its OWN K (DC/HF-gain mode). Unity cells ignore K."""
    cells = {}
    want = names if names is not None else [topo_name(t) for t in all_cells()]
    want = set(want)
    for topo in all_cells():
        name = topo_name(topo)
        if name not in want:
            continue
        t0 = time.time()
        subs = dict(design_subs)
        if k_map is not None and k_map.get(name) is not None:
            subs[_SYM.K] = k_map[name]
        cells[(name, "ideal")] = derive_ideal(topo, subs)
        cells[(name, "nonideal")] = derive_nonideal(topo)
        if verbose:
            di = cells[(name, "ideal")]
            print(f"  {name:<14} ideal(den {di['den_degree']}, "
                  f"{len(di['res_eqs'])} res) + nonideal   {time.time()-t0:.1f}s")
    return cells


# =====================================================================
# Cache (srepr round-trip) — incremental, per-cell (v3)
# =====================================================================
def _S(e):  return None if e is None else sp.srepr(e)
def _SL(l): return None if l is None else [sp.srepr(x) for x in l]
def _P(x):  return None if x is None else sp.sympify(x)
def _PL(x): return None if x is None else [sp.sympify(e) for e in x]


def _design_key(design_subs):
    """Canonical string for the numeric design targets (ideal cells depend on
    these; non-ideal cells do not)."""
    items = sorted((str(k), float(v)) for k, v in design_subs.items())
    return ";".join(f"{k}={v:.10g}" for k, v in items)


def _serialize_case(d):
    return {
        "topo": d["topo"],
        "den_degree": d["den_degree"], "num_degree": d["num_degree"],
        "res_eqs": _SL(d.get("res_eqs")),
        "R5_constraint": _S(d.get("R5_constraint")),
        "a1_expr": _S(d.get("a1_expr")), "a2_expr": _S(d.get("a2_expr")),
        "var_list": _SL(d.get("var_list")),
        "tf_num": _S(d["tf_num"]), "tf_den": _S(d["tf_den"]),
        "tf_var_list": _SL(d["tf_var_list"]),
        "mna": bool(d.get("mna", False)),
    }


def _deserialize_case(model, d):
    return {
        "kind": model, "topo": d["topo"],
        "den_degree": d["den_degree"], "num_degree": d["num_degree"],
        "res_eqs": _PL(d.get("res_eqs")),
        "R5_constraint": _P(d.get("R5_constraint")),
        "a1_expr": _P(d.get("a1_expr")), "a2_expr": _P(d.get("a2_expr")),
        "var_list": _PL(d.get("var_list")),
        "tf_num": _P(d["tf_num"]), "tf_den": _P(d["tf_den"]),
        "tf_var_list": _PL(d["tf_var_list"]),
        "mna": bool(d.get("mna", False)),
    }


def _load_blob(path):
    if os.path.exists(path):
        try:
            with open(path) as f:
                blob = json.load(f)
            if "cells" in blob:
                return blob
        except Exception:
            pass
    return {"cells": {}}


def _save_blob(blob, path):
    # unique per-process temp name so concurrent writers never clobber a shared
    # .tmp (the os.replace itself is atomic; the temp file must not be shared).
    # try/finally guarantees the temp is removed even if the process is killed
    # mid-write or os.replace raises -- otherwise interrupted writes leave
    # orphaned "<path>.tmp.<pid>" files lying around next to the cache.
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w") as f:
            json.dump(blob, f)
        os.replace(tmp, path)               # atomic; consumes tmp on success
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _sweep_stale_temps(path):
    """Remove leftover '<path>.tmp.<pid>' files from past interrupted writes
    (older format temps are harmless to delete; the live cache is never matched
    because its name has no '.tmp.' suffix)."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    base = os.path.basename(path) + ".tmp."
    try:
        for fn in os.listdir(d):
            if fn.startswith(base):
                try:
                    os.remove(os.path.join(d, fn))
                except OSError:
                    pass
    except OSError:
        pass


def dump_cases(cases, path):
    """Serialize a full {(name,model): case} dict to a standalone file. Used to
    hand the orchestrator-derived cases to solver workers, which then LOAD this
    file instead of re-deriving (derivation happens once, in the orchestrator;
    workers never derive or write a cache -- removes the 32x-simultaneous-derive
    memory spike and every multi-process cache race)."""
    blob = {"cases": {f"{name}|{model}": _serialize_case(d)
                      for (name, model), d in cases.items()}}
    _save_blob(blob, path)
    return path


def load_cases(path):
    """Inverse of dump_cases: load a {(name,model): case} dict from a file."""
    with open(path) as f:
        blob = json.load(f)
    out = {}
    for key, d in blob["cases"].items():
        name, model = key.rsplit("|", 1)
        out[(name, model)] = _deserialize_case(model, d)
    return out


def get_cases(design_subs=None, path=CACHE_PATH_V2, force=False, verbose=True,
              k_map=None, topo_names=None):
    """Load or derive the requested cells (ideal + non-ideal), returning a
    dict keyed by (name, model).

    topo_names : restrict to these cell names (default: every cell in every
                 registered family). Pass the family's cells you actually
                 solve so an LP run never derives HP and vice-versa.
    k_map      : per-topology K (DC/HF-gain mode). When given, cells are
                 derived fresh and NOT cached (each carries its own K).
    force      : ignore any cached ideal cells and re-derive (non-ideal cells
                 are still reused — they are design-independent).
    """
    if design_subs is None:
        design_subs = {_SYM.p1: 2*sp.pi*508.9, _SYM.w0: 2*sp.pi*486.76,
                       _SYM.wz: 2*sp.pi*1668.0, _SYM.Q: 1.0455, _SYM.K: 680.75}

    want = (list(topo_names) if topo_names is not None
            else [topo_name(t) for t in all_cells()])

    # DC/HF-gain mode: derive fresh, never touch the shared cache.
    if k_map is not None:
        if verbose:
            print(f"Deriving {len(want)} cell(s) with per-topology K "
                  "(gain mode)...")
        return derive_all(design_subs, verbose=verbose, k_map=k_map, names=want)

    dkey = _design_key(design_subs)
    _sweep_stale_temps(path)                    # clear orphaned <cache>.tmp.<pid>
    blob = _load_blob(path)
    store = blob["cells"]
    cases = {}
    to_derive = []                              # names whose ideal must be built
    n_hit = 0
    for name in want:
        topo = _name_to_topo().get(name)
        ikey = f"{dkey}|{name}|ideal"
        nkey = _ni_key(name, topo)              # design-independent + structure-salted
        have_i = (not force) and (ikey in store)
        have_n = nkey in store
        if have_i and have_n:
            cases[(name, "ideal")] = _deserialize_case("ideal", store[ikey])
            cases[(name, "nonideal")] = _deserialize_case("nonideal", store[nkey])
            n_hit += 1
        else:
            to_derive.append(name)

    if to_derive:
        if verbose:
            print(f"Deriving {len(to_derive)} cell(s) "
                  f"({n_hit} cached) ...")
        for topo in all_cells():
            name = topo_name(topo)
            if name not in set(to_derive):
                continue
            di = derive_ideal(topo, design_subs)
            nkey = _ni_key(name, topo)
            if nkey in store:
                dn = _deserialize_case("nonideal", store[nkey])
            else:
                dn = derive_nonideal(topo)
                store[nkey] = _serialize_case(dn)
            store[f"{dkey}|{name}|ideal"] = _serialize_case(di)
            cases[(name, "ideal")] = di
            cases[(name, "nonideal")] = dn
        _save_blob(blob, path)
    elif verbose:
        print(f"Loaded TF cache (v3) from {path} ({n_hit} cell(s))")
    return cases


# =====================================================================
# Self-test: order check (random sampling) + ideal-limit acceptance.
# Runs across every registered family (or a chosen subset).
# =====================================================================
def self_test(verbose=True, families=None):
    import numpy as np
    target = {_SYM.p1: 2*np.pi*508.9, _SYM.w0: 2*np.pi*486.76,
              _SYM.wz: 2*np.pi*1668.0, _SYM.Q: 1.0455, _SYM.K: 680.75}
    comp = {"C1": 0.0033, "C2": 3.3e-4, "C3": 3.3e-4, "C4": 8.2e-4,
            "R1": 0.043, "R2": 0.1, "R3": 0.2, "R4": 0.15,
            "R5": 0.3, "R6": 0.39, "R7": 0.5, "R8": 0.5}
    near_ideal = dict(Ro=1e-9, A_ol=1e12, GBWP_hz=1e15)
    w = 2*np.pi*np.logspace(0, 5, 2000)
    all_ok = True
    if verbose:
        print(f"{'cell':<16}{'fam':<5}{'order(rand)':<14}{'notch_zeros':<13}"
              f"{'ideal-limit':<14}{'verdict'}")
    for topo in all_cells(families):
        di = derive_ideal(topo, target)
        dn = derive_nonideal(topo)
        orders = set(); nzeros = set()
        for trial in range(3):
            rng = np.random.default_rng(trial)
            vv = {sp.Symbol(k): v*rng.uniform(0.5, 2.0) for k, v in comp.items()}
            def co(e):
                ee = e.subs({k: v for k, v in vv.items() if k in e.free_symbols})
                return np.array([complex(c) for c in sp.Poly(sp.expand(ee), _SYM.s).all_coeffs()])
            nc = co(di["tf_num"]); dc = co(di["tf_den"])
            z = np.roots(nc) if len(nc) > 1 else np.array([])
            p = list(np.roots(dc)) if len(dc) > 1 else []
            zk = []
            for zi in z:
                hit = None
                for j, pj in enumerate(p):
                    if abs(zi-pj) <= 1e-3*max(abs(zi), abs(pj), 1): hit = j; break
                if hit is None: zk.append(zi)
                else: p.pop(hit)
            orders.add(len(p)); nzeros.add(len(zk))
        Hi, ni = make_response_func(di); Hn, nn = make_response_func(dn)
        ci = {k: comp[k] for k in ni}
        cn = dict({k: comp[k] for k in nn if k in comp}, **near_ideal)
        md = float(np.nanmax(np.abs(Hi(ci, w) - Hn(cn, w))))
        order_ok = orders == {topo["order"]}
        # notch cells: 2 finite (on-axis) zeros that don't cancel a pole; the
        # 3rd-order HP notch also has a lone origin zero (counted as a finite
        # root at 0) -> {2} for LP/2nd, {2 or 3} acceptable for 3rd-order HP.
        if topo["notch"]:
            if topo["order"] == 3 and topo.get("family") in ("HP", "HP-MFB", "HP-AM"):
                zero_ok = nzeros.issubset({2, 3}) and (2 in nzeros or 3 in nzeros)
            else:
                zero_ok = (nzeros == {2})
        else:
            # notchless: all zeros at origin. LP -> they cancel/away => {0};
            # HP -> origin zeros are finite roots at 0 => {order}; BP -> a single
            # origin zero (the lone s-term numerator) => {1}.
            if topo.get("family") in ("HP", "HP-MFB", "HP-AM"):
                zero_ok = (nzeros == {topo["order"]})
            elif topo.get("family") in ("BP", "BP-MFB", "BP-AM"):
                # Plain band-pass: one origin zero (lone s-term numerator).
                # With a real pole absorbed the numerator is still a pure
                # monomial, but of degree 2 when the absorbed pole steepens the
                # LOWER skirt (absorb="hp", num ~ s^2) -- so that cell carries
                # TWO origin zeros. absorb="lp" keeps num ~ s, i.e. one.
                zero_ok = (nzeros == {2 if topo.get("absorb") == "hp" else 1})
            else:
                zero_ok = (nzeros == {0})
        limit_ok = md < 1e-4
        ok = order_ok and zero_ok and limit_ok
        all_ok &= ok
        if verbose:
            print(f"{topo_name(topo):<16}{topo.get('family','?'):<5}"
                  f"{str(sorted(orders)):<14}{str(sorted(nzeros)):<13}"
                  f"{md:<14.1e}{'PASS' if ok else 'FAIL'}")
    if verbose:
        print(f"\n{'ALL CELLS PASS' if all_ok else 'SOME CELLS FAILED'}")
    return all_ok


if __name__ == "__main__":
    print("=== SELF-TEST (all families) ===")
    self_test()
    print("\n=== DERIVE + CACHE (LP only, default design) ===")
    design = {_SYM.p1: 2*sp.pi*508.9, _SYM.w0: 2*sp.pi*486.76,
              _SYM.wz: 2*sp.pi*1668.0, _SYM.Q: 1.0455, _SYM.K: 680.75}
    lp_names = [topo_name(t) for t in all_cells(["LP"])]
    cases = get_cases(design, topo_names=lp_names, force=True)
    print(f"\n{len(cases)} cache entries ({len(lp_names)} LP cells x 2 models)")
