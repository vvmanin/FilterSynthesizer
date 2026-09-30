# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028 shared benchmark / profiling harness (analysis only).

Nothing here changes production code. It drives `filter_synthesis.synthesize`
exactly the way `topology_tab._submit` does (same cells per section, same
dc_gain / K, same Convergence presets and component envelope defaults) and can
optionally run it INSTRUMENTED: every ProcessPoolExecutor in the solver is
replaced by an in-process serial pool, so each worker task, worker initializer,
least_squares call and pipeline stage can be timed exactly. Serial CPU-seconds
per stage are the machine-independent quantity; `sched_wall()` turns the measured
per-task times into the wall time a W-worker pool would take (greedy in-order
assignment = what pool.map(chunksize=1) does).

Used by bench_sections.py (baseline + BOMs) and the probe_*.py scripts.
"""
import os
import sys
import time
import tempfile
import contextlib
import heapq
import io

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# The TF cache path is relative (tf_cache_v6.json in the CWD). Benchmarks run in
# a private work dir so they never touch a cache next to the app, and "cold" can
# be forced by pointing at an empty dir.
WORK_DIR = os.environ.get("FS028_WORK", os.path.join(tempfile.gettempdir(), "fs028_work"))

# Topology-tab defaults (topology_tab._section_settings / _convergence_inputs).
ENV_DEFAULT = dict(C_min=6.8e-5, C_max=1e-2, R_min=0.3e-3, R_max=2000.0e-3,
                   MAX_R_RATIO=500.0, C_series="E12", R_series="E48")
SEARCH_PRESETS = {
    "Fast":     dict(ratio_starts=30,  anchored_starts=60,  max_valleys=6,  hints_per_combo=2),
    "Balanced": dict(ratio_starts=60,  anchored_starts=120, max_valleys=12, hints_per_combo=3),
    "Thorough": dict(ratio_starts=120, anchored_starts=240, max_valleys=20, hints_per_combo=4),
}
# Probe-only start budget (10 x Balanced), for "is it infeasible or just not found?"
PROBE_PRESETS = dict(SEARCH_PRESETS,
                     X10=dict(ratio_starts=600, anchored_starts=1200, max_valleys=12,
                              hints_per_combo=3),
                     # warm-up only: derives / caches the TFs, negligible solve work
                     Warm=dict(ratio_starts=2, anchored_starts=2, max_valleys=1,
                               hints_per_combo=1))
CONV_DEFAULT = dict(pole_tol=0.01, gain_tol=0.005, reg_weight=0.02, top_k=30)
GAIN_UNITY_TOL = 0.02
TL072 = dict(A_ol=2e5, GBWP_hz=3e6, Ro=50 / 1e6)          # opamp_library.solver_params form


# =====================================================================
# Benchmark section set (realistic targets from scipy prototypes, ~1 kHz)
# =====================================================================
def _pair(p):
    w0 = abs(p)
    return w0 / (2 * np.pi), w0 / (2 * abs(p.real))


def _k_for_unity(order, fam, f0, Q, fz=None, f1=None):
    """K_radps that gives the section unity passband gain (topology_tab
    section_dc_gain inverted): H = K N(s)/D(s), N and D monic."""
    w0 = 2 * np.pi * f0
    if fam in ("HP", "HPn"):
        return 1.0                                   # H(inf) = K
    if fam == "BP":
        return w0 / Q                                # |H(jw0)| = K Q / w0
    if fam == "BP1LP":
        p1 = 2 * np.pi * f1
        return w0 * abs(complex(p1, w0)) / Q
    a0 = (2 * np.pi * f1) * w0 ** 2 if order == 3 else w0 ** 2
    b0 = (2 * np.pi * fz) ** 2 if fam in ("LPn", "notch") else 1.0
    return a0 / b0


def _sec(sid, order, fam, f0, Q, fz=None, f1=None, gain=1.0):
    notch = fam in ("LPn", "HPn", "notch")
    n0 = {"HP": order, "HPn": order - 2, "BP": 1, "BP1LP": 1}.get(fam, 0)
    K = _k_for_unity(order, fam, f0, Q, fz, f1) * gain
    return {"id": sid, "stage_num": 1, "order": order, "family": fam, "notch": notch,
            "n_origin_zeros": n0, "has_origin_zero": n0 > 0, "is_complex_pair": True,
            "f0_hz": float(f0), "Q": float(Q),
            "fz_hz": float(fz) if fz is not None else None,
            "f1_hz": float(f1) if f1 is not None else None,
            "K_radps": float(K), "gain": gain}


def section_set():
    """The FS-028 benchmark sections. Values come from standard prototypes so
    the targets are the ones the app actually produces (elliptic 0.5 dB / 60 dB,
    Butterworth, ~1 kHz corner)."""
    from scipy.signal import ellip
    out = []
    # --- elliptic LP 5th order: 3rd-order LPn (real + low-Q pair + upper zero), 2nd-order LPn
    z, p, _k = ellip(5, 0.5, 60, 2 * np.pi * 1000.0, "low", analog=True, output="zpk")
    pr = [x for x in p if abs(x.imag) < 1e-6][0]
    pc = sorted([x for x in p if x.imag > 1e-6], key=lambda x: _pair(x)[1])
    zc = sorted([x for x in z if x.imag > 1e-6], key=abs)
    f1 = abs(pr) / (2 * np.pi)
    f0a, Qa = _pair(pc[0]); f0b, Qb = _pair(pc[1])
    out.append(_sec("LPn3", 3, "LPn", f0a, Qa, fz=abs(zc[1]) / (2 * np.pi), f1=f1))
    out.append(_sec("LPn2", 2, "LPn", f0b, Qb, fz=abs(zc[0]) / (2 * np.pi)))
    out.append(_sec("LPn3g", 3, "LPn", f0a, Qa, fz=abs(zc[1]) / (2 * np.pi), f1=f1, gain=2.0))
    # --- Butterworth-like all-pole LP
    out.append(_sec("LP2", 2, "LP", 1000.0, 1.3066))
    out.append(_sec("LP3", 3, "LP", 1000.0, 1.0, f1=1000.0))
    # --- elliptic HP 5th order: 3rd-order HPn + 2nd-order HPn
    z, p, _k = ellip(5, 0.5, 60, 2 * np.pi * 1000.0, "high", analog=True, output="zpk")
    pr = [x for x in p if abs(x.imag) < 1e-6][0]
    pc = sorted([x for x in p if x.imag > 1e-6], key=lambda x: _pair(x)[1])
    zc = sorted([x for x in z if abs(x.imag) > 1e-6 and x.imag > 0], key=abs)
    f1h = abs(pr) / (2 * np.pi)
    f0a, Qa = _pair(pc[0]); f0b, Qb = _pair(pc[1])
    out.append(_sec("HPn3", 3, "HPn", f0a, Qa, fz=abs(zc[0]) / (2 * np.pi), f1=f1h))
    out.append(_sec("HPn2", 2, "HPn", f0b, Qb, fz=abs(zc[1]) / (2 * np.pi)))
    out.append(_sec("HP2", 2, "HP", 1000.0, 1.3066))
    # --- band-pass, pure notch
    out.append(_sec("BP2", 2, "BP", 1000.0, 5.0))
    out.append(_sec("N2", 2, "notch", 1000.0, 2.0, fz=1000.0))
    out.append(_sec("BP1LP", 3, "BP1LP", 1000.0, 5.0, f1=300.0))
    return out


def section_by_id(sid):
    for s in section_set():
        if s["id"] == sid:
            return s
    raise KeyError(sid)


def section_dc_gain(sec):
    """topology_tab.section_dc_gain (without its Streamlit import)."""
    w0 = 2 * np.pi * sec["f0_hz"]
    fam = sec["family"]
    if fam == "BP":
        return sec["K_radps"] * sec["Q"] / w0
    if fam == "BP1LP":
        pp = 2 * np.pi * float(sec.get("f1_hz") or 0.0)
        return sec["K_radps"] * sec["Q"] / (w0 * abs(complex(pp, w0)))
    if fam in ("HP", "HPn"):
        return sec["K_radps"]
    a0 = (2 * np.pi * sec["f1_hz"]) * w0 ** 2 if sec["order"] == 3 else w0 ** 2
    b0 = (2 * np.pi * sec["fz_hz"]) ** 2 if sec["notch"] else 1.0
    return sec["K_radps"] * b0 / a0


def route(sec, family):
    """(topos, dc_target) exactly as topology_tab._render_section picks them for
    an untouched UI (no custom gain, no LS / Eliminate-R1 / Gained-MFB ticks).
    family in {"VCVS", "MFB", "AM"}. Returns None if the tab would gate it."""
    fam, o = sec["family"], sec["order"]
    eff_dc = section_dc_gain(sec)
    if eff_dc < 1.0 - GAIN_UNITY_TOL:
        mode = "atten"
    elif abs(eff_dc - 1.0) <= GAIN_UNITY_TOL:
        mode = "unity"
    else:
        mode = "gained"
    if fam in ("BP", "BP1LP"):
        if family == "AM":
            return (["2BP-AM"], None) if fam == "BP" else None
        if family == "MFB":
            return ((["2BP-MFB", "2BP-MFB-QE"], None) if fam == "BP"
                    else (["2BP1LP-MFB", "2BP1LP-MFB-QE"], None))
        return (["2BP", "2BP-atten"], None) if fam == "BP" else None
    if fam == "notch":
        if family == "AM":
            return ["2N-AM", "2N-AM-C1s"], eff_dc
        if family == "MFB":
            return (["2N-MFB-atten", "2N-MFB"] if mode == "atten" else ["2N-MFB"]), eff_dc
        return (["2N", "2N-atten"] if mode == "atten" else ["2N"]), eff_dc
    is_hp = fam in ("HP", "HPn")
    prefix = ("HPn" if sec["notch"] else "HP") if is_hp else ("LPn" if sec["notch"] else "LP")
    if family == "VCVS":
        topo = f"{o}{prefix}-{mode}"
        topos = [topo]
        if is_hp and sec["notch"] and ((mode == "gained" and o in (2, 3))
                                       or (mode == "unity" and o == 3)):
            topos.append(topo + "+R8")
        return topos, (eff_dc if mode == "atten" else None)
    if family == "MFB":
        if sec["notch"]:
            if is_hp:
                from cells_mfb_hp import notch_cells_for_gain
                return list(notch_cells_for_gain(o, eff_dc)), eff_dc
            return [f"{o}LPn-MFB"], eff_dc
        return [f"{o}{prefix}-MFB", f"{o}{prefix}-MFB-QE"], eff_dc
    if family == "AM":
        topo = f"{o}{prefix}-AM"
        if not is_hp and not sec["notch"]:
            return [f"{o}LP-AM", f"{o}LP-AM2"], eff_dc
        return [topo, topo + "-C1s"], eff_dc
    raise ValueError(family)


def build_cfg(sec, env=None, conv=None):
    """topology_tab._build_cfg (K override = the section K for band-pass)."""
    env = dict(ENV_DEFAULT if env is None else env)
    conv = dict(CONV_DEFAULT if conv is None else conv)
    return dict(env, reg_weight=conv["reg_weight"], f0=sec["f0_hz"], Q=sec["Q"],
                fz=sec["fz_hz"] if sec["notch"] else sec["f0_hz"],
                f1=sec["f1_hz"] if sec["order"] == 3 else sec["f0_hz"],
                K=sec["K_radps"])


def bench_cases(families=("VCVS", "MFB", "AM"), ids=None):
    """[(case_id, sec, family, topos, dc_target)] for every routable pair."""
    out = []
    for sec in section_set():
        if ids and sec["id"] not in ids:
            continue
        for fam in families:
            r = route(sec, fam)
            if r is None:
                continue
            topos, dct = r
            out.append((f"{sec['id']}-{fam}", sec, fam, topos, dct))
    return out


# =====================================================================
# Instrumentation
# =====================================================================
class SerialPool:
    """Drop-in for ProcessPoolExecutor: runs the initializer once and map()
    in-process, in order. Lets every task be timed without IPC noise."""
    def __init__(self, max_workers=None, initializer=None, initargs=(), **_kw):
        if initializer is not None:
            initializer(*initargs)

    def map(self, fn, it, chunksize=1):
        return [fn(x) for x in it]

    def submit(self, fn, *a, **k):
        from concurrent.futures import Future
        f = Future()
        try:
            f.set_result(fn(*a, **k))
        except Exception as exc:        # noqa: BLE001
            f.set_exception(exc)
        return f

    def shutdown(self, wait=True):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Recorder:
    def __init__(self):
        self.stage = {}                 # stage name -> total seconds (serial)
        self.tasks = {}                 # pool phase -> [(seconds, meta)]
        self.ls = []                    # least_squares calls: dict
        self.ctx = ["main"]

    def add(self, name, dt):
        self.stage[name] = self.stage.get(name, 0.0) + dt

    def task(self, phase, dt, meta):
        self.tasks.setdefault(phase, []).append((dt, meta))


@contextlib.contextmanager
def instrumented(rec):
    """Patch the solver modules for one serial, fully timed run."""
    import unified_solver_v2 as US
    import nonideal_solver as NI
    import zero_manifold_solver as ZM
    import filter_synthesis as FS
    import tf_derivation_v2 as TF

    saved = []
    # S2-1: the non-ideal correction runs in-process (no pool, shared memo).
    ni_pooled = hasattr(NI, "ProcessPoolExecutor")

    def patch(mod, name, new):
        if not hasattr(mod, name):      # hook point removed by a later stage
            return
        saved.append((mod, name, getattr(mod, name)))
        setattr(mod, name, new)

    def timed_stage(mod, name, label):
        if not hasattr(mod, name):
            return
        orig = getattr(mod, name)

        def w(*a, **k):
            t = time.perf_counter()
            try:
                return orig(*a, **k)
            finally:
                rec.add(label, time.perf_counter() - t)
        patch(mod, name, w)

    def ls_wrapper(orig, where):
        def w(*a, **k):
            t = time.perf_counter()
            r = orig(*a, **k)
            dt = time.perf_counter() - t
            rec.ls.append(dict(where=where, ctx=rec.ctx[-1], t=dt, nfev=int(r.nfev),
                               njev=int(r.njev or 0), status=int(r.status),
                               cost=float(2 * r.cost), success=bool(r.success),
                               n=len(np.atleast_1d(r.x)), m=len(np.atleast_1d(r.fun))))
            return r
        return w

    def task_wrapper(orig, phase, meta_fn):
        def w(task):
            rec.ctx.append(phase)
            t = time.perf_counter()
            try:
                out = orig(task)
            finally:
                dt = time.perf_counter() - t
                rec.ctx.pop()
            rec.task(phase, dt, meta_fn(task, out))
            return out
        return w

    patch(US, "ProcessPoolExecutor", SerialPool)
    patch(NI, "ProcessPoolExecutor", SerialPool)      # skipped when NI is in-process
    patch(US, "least_squares", ls_wrapper(US.least_squares, "US"))
    patch(ZM, "least_squares", ls_wrapper(ZM.least_squares, "ZM"))
    patch(NI, "least_squares", ls_wrapper(NI.least_squares, "NI"))

    def p1_meta(task, out):
        mode = task["mode"]
        if task.get("seed"):
            mode = "seed"
        elif task.get("wide"):
            mode = "wide"
        return dict(topo=task["topo_name"], mode=mode, ok=out is not None,
                    caps=(out or {}).get("caps"), cost=(out or {}).get("cost"))

    def p3_meta(task, out):
        return dict(topo=task["topo_name"], n_hints=len(task["r_hints"]),
                    ok=bool(out), n_out=len(out) if out else 0)

    def zm_meta(task, out):
        return dict(topo=task["topo_name"], ok=out is not None)

    def ni_meta(sol, out):
        return dict(topo=sol["topology"], ok=out is not None)

    patch(US, "phase1_worker", task_wrapper(US.phase1_worker, "p1", p1_meta))
    patch(US, "phase3_worker", task_wrapper(US.phase3_worker, "p3", p3_meta))
    patch(US, "zm_worker", task_wrapper(US.zm_worker, "zm", zm_meta))
    patch(NI, "_worker", task_wrapper(NI._worker, "ni", ni_meta))
    timed_stage(US, "_init_worker", "init_p13")
    timed_stage(NI, "_init_worker", "init_ni")
    timed_stage(US, "harvest", "harvest")
    timed_stage(US, "dedup", "dedup")
    timed_stage(TF, "dump_cases", "dump_cases")
    timed_stage(TF, "load_cases", "load_cases")
    # S2-1 compile-once stages (main process): templates + kernel sources
    timed_stage(TF, "get_templates", "get_templates")
    try:
        import cell_kernels as CK
        timed_stage(CK, "sources", "kernel_sources")
    except ImportError:
        pass
    # get_cases is reached as TF.get_cases (US), and by-name in NI and FS.
    _tf_get = TF.get_cases

    def get_cases_timed(*a, **k):
        t = time.perf_counter()
        try:
            return _tf_get(*a, **k)
        finally:
            where = rec.ctx[-1]
            rec.add("get_cases@" + ("ni_init" if where == "ni_init" else "main"),
                    time.perf_counter() - t)
    patch(TF, "get_cases", get_cases_timed)
    patch(NI, "get_cases", get_cases_timed)
    patch(FS, "get_cases", get_cases_timed)
    _ni_init = NI._init_worker

    def ni_init_ctx(*a, **k):
        # A real non-ideal worker is a fresh process: its make_response_func memo
        # starts empty. Emulate that, then give the main process its memo back.
        # (In-process NI, S2-1: the memo IS the main process's -- keep it.)
        memo = dict(TF._RESP_CACHE)
        if ni_pooled:
            TF._RESP_CACHE.clear()
        rec.ctx.append("ni_init")
        try:
            return _ni_init(*a, **k)
        finally:
            rec.ctx.pop()
            TF._RESP_CACHE.update(memo)
    patch(NI, "_init_worker", ni_init_ctx)
    timed_stage(FS, "solve_nonideal", "nonideal_total")
    timed_stage(FS, "snap_to_hardware", "snap")
    timed_stage(FS, "run_synthesis", "run_synthesis_total")
    try:
        yield rec
    finally:
        for mod, name, orig in reversed(saved):
            setattr(mod, name, orig)


def sched_wall(times, workers):
    """Wall time of pool.map(chunksize=1) over `times` (dispatch order) with
    `workers` identical workers: each task goes to the first free worker."""
    if not times:
        return 0.0
    heap = [0.0] * min(workers, len(times))
    heapq.heapify(heap)
    for t in times:
        s = heapq.heappop(heap)
        heapq.heappush(heap, s + t)
    return max(heap)


@contextlib.contextmanager
def workdir(path=None, fresh=False):
    """chdir into the benchmark work dir (TF cache lives there)."""
    d = path or WORK_DIR
    os.makedirs(d, exist_ok=True)
    if fresh:
        for fn in os.listdir(d):
            if fn.startswith("tf_cache"):
                os.remove(os.path.join(d, fn))
    old = os.getcwd()
    os.chdir(d)
    try:
        yield d
    finally:
        os.chdir(old)


def clear_process_caches():
    """Drop in-process memo caches so a 'cold' run in the same process is cold."""
    import tf_derivation_v2 as TF
    import unified_solver_v2 as US
    TF._RESP_CACHE.clear()
    US._SENS_FUNCS.clear()
    getattr(TF, "_TPL", {}).clear()              # S2-1 templates
    try:
        import cell_kernels as CK                # S2-1 kernel sources / compiled fns
        CK._SRC.clear()
        CK._FNS.clear()
    except ImportError:
        pass
    try:
        import sympy as sp
        sp.core.cache.clear_cache()
    except Exception:        # noqa: BLE001
        pass


def run_section(sec, family, topos, dc_target, preset="Balanced", opamp=None,
                n_cores=4, instrument=True, conv=None, env=None, verbose=False):
    """One Topology-tab section solve. Returns (result, info)."""
    import filter_synthesis as FS
    conv = dict(CONV_DEFAULT if conv is None else conv)
    p = PROBE_PRESETS[preset]
    cfg = build_cfg(sec, env, conv)
    if family == "AM":
        cfg["equalize_rc"] = True
    kw = dict(opamp=opamp, topologies=list(topos), dc_gain=dc_target, n_cores=n_cores,
              ratio_starts=p["ratio_starts"], anchored_starts=p["anchored_starts"],
              max_valleys=p["max_valleys"], hints_per_combo=p["hints_per_combo"],
              pole_tol=conv["pole_tol"], gain_tol=conv["gain_tol"], top_k=conv["top_k"],
              verbose=verbose)
    info = {"cfg": cfg, "kw": {k: v for k, v in kw.items() if k != "opamp"}}
    if not instrument:
        t = time.perf_counter()
        res = FS.synthesize(cfg, **kw)
        info["wall"] = time.perf_counter() - t
        return res, info
    rec = Recorder()
    with instrumented(rec):
        with contextlib.redirect_stdout(io.StringIO()) if not verbose else contextlib.nullcontext():
            t = time.perf_counter()
            res = FS.synthesize(cfg, **kw)
            info["wall"] = time.perf_counter() - t
    info["rec"] = rec
    return res, info


def summarize(info, workers=32):
    """Stage CPU-seconds and the modelled wall time at `workers` cores."""
    rec = info["rec"]
    st = dict(rec.stage)
    tk = {ph: [t for t, _m in v] for ph, v in rec.tasks.items()}
    cpu = {ph: float(sum(v)) for ph, v in tk.items()}
    n = {ph: len(v) for ph, v in tk.items()}
    wall = {ph: sched_wall(v, workers) for ph, v in tk.items()}
    import nonideal_solver as NI
    if not hasattr(NI, "ProcessPoolExecutor"):      # S2-1: NI runs in-process, serially
        wall["ni"] = cpu.get("ni", 0.0)
    init13 = st.get("init_p13", 0.0)
    initni = st.get("init_ni", 0.0)
    rs_total = st.get("run_synthesis_total", 0.0)
    pool_cpu = sum(cpu.get(ph, 0.0) for ph in ("p1", "p3", "zm"))
    rs_serial = rs_total - pool_cpu - init13     # main-process part of run_synthesis
    ni_total = st.get("nonideal_total", 0.0)
    snap = st.get("snap", 0.0)
    model = {
        "rs_main": rs_serial,
        "init_p13": init13,
        "p1": wall.get("p1", 0.0), "p3": wall.get("p3", 0.0), "zm": wall.get("zm", 0.0),
        "init_ni": initni, "ni": wall.get("ni", 0.0),
        "fs_main": max(0.0, info["wall"] - rs_total - ni_total - snap),
        "snap": snap,
    }
    model["total"] = sum(model.values())
    return {"stage": st, "cpu": cpu, "n": n, "wall_model": model,
            "cpu_total": info["wall"], "workers": workers}
