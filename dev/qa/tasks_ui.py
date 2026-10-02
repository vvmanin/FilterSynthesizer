# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""AppTest tasks: drive the real app.py headlessly (one AppTest session per task).

design_task -- set the sidebar + Pairing choices of one design, snapshot what
    the app published (engine roots, pairing stages, hw_sections, spec rows),
    analyse it, then for each variant configure the Topology tab (Batch mode)
    and click *Solve all sections*. `topology_tab._proc_submit` is patched to
    RECORD each solve job (the exact callable + arguments the real routing,
    _build_cfg and the FS-033 dual solve produce) instead of running it; the
    coordinator solves the jobs in its pool (tasks_solve). 1st-order sections
    solve in-process as in the app and are recorded here.
e2e_task -- the same, but `_proc_submit` runs the solve synchronously; then
    picks the best row per section through the BOM table's selection state,
    runs Monte Carlo, generates the PDF report through the Tab-4 button, and
    keeps the LTspice export the app built (spice_export.build_export).

All widget addresses live in ui_map.py.
"""
import concurrent.futures as cf
import importlib
import math
import os
import re
import time
import traceback

import ui_map as U
from common import jsonable, short_hash

LTSPICE_MC_RUNS = 50
_STATE = {"mode": "capture", "jobs": [], "first_order": {}, "exports": []}
_PATCHED = []


class UIError(RuntimeError):
    """A widget the harness needs is missing / not settable (the UI changed)."""


class AppException(RuntimeError):
    """app.py raised (shown by Streamlit as an exception block)."""


# =============================================================================
# patches
# =============================================================================
def _fake_submit(fn, *args, **kwargs):
    fut = cf.Future()
    rec = dict(fn=f"{fn.__module__}:{fn.__name__}", args=args, kwargs=kwargs)
    if _STATE["mode"] == "sync":
        fut.set_running_or_notify_cancel()
        t = time.perf_counter()
        try:
            res = fn(*args, **kwargs)
            fut.set_result(res)
            rec["result"] = _summ_result(res)
        except Exception as e:                             # noqa: BLE001
            fut.set_exception(e)
            rec["result"] = dict(error=f"{type(e).__name__}: {e}")
        rec["wall"] = time.perf_counter() - t
    _STATE["jobs"].append(rec)
    return fut


def _summ_result(res):
    sn = res.get("snapped") or []
    return dict(n_snapped=len(sn),
                best_snap=min((r.get("snap_cost", math.inf) for r in sn), default=None),
                best_sens=min((r.get("sens_score", math.inf) for r in sn), default=None))


class _Once:
    """logging filter: each (logger, message) once per worker -- Streamlit repeats its
    deprecation warnings on every run (tens of MB of worker log otherwise)."""
    seen = set()

    def filter(self, rec):
        key = (rec.name, str(rec.msg)[:200])
        if key in self.seen:
            return False
        self.seen.add(key)
        return True


def _dedupe_streamlit_logs():
    import streamlit.logger as SL
    once = _Once()
    for lg in list(getattr(SL, "_loggers", {}).values()):
        lg.addFilter(once)
    orig = SL.get_logger

    def get_logger(name):
        lg = orig(name)
        if once not in lg.filters:
            lg.addFilter(once)
        return lg
    SL.get_logger = get_logger


def install_patches():
    if _PATCHED:
        return
    try:
        _dedupe_streamlit_logs()
    except Exception:                                      # noqa: BLE001  (logging only)
        pass
    m, a = U.PATCH_SUBMIT
    setattr(importlib.import_module(m), a, _fake_submit)
    m, a = U.PATCH_ENGINE_POOL
    setattr(importlib.import_module(m), a, lambda fn, *x, **kw: fn(*x, **kw))

    m, a = U.PATCH_FIRST_ORDER
    fmod = importlib.import_module(m)
    orig_fo = getattr(fmod, a)

    def fo(cfg, opamp=None, topology=None, dc_gain=None, **kw):
        t = time.perf_counter()
        res = orig_fo(cfg, opamp=opamp, topology=topology, dc_gain=dc_gain, **kw)
        key = short_hash((sorted(cfg.items()), opamp and sorted(opamp.items()), topology, dc_gain))
        sn = res.get("snapped") or []
        _STATE["first_order"][key] = dict(
            topology=topology, f0_hz=cfg.get("f0"), dc_gain=dc_gain, error=res.get("__error__"),
            n_snapped=len(sn), best_snap=min((r.get("snap_cost", math.inf) for r in sn), default=None),
            wall=time.perf_counter() - t)
        return res
    setattr(fmod, a, fo)

    m, a = U.PATCH_EXPORT
    emod = importlib.import_module(m)
    orig_ex = getattr(emod, a)

    def ex(*args, **kw):
        out = orig_ex(*args, **kw)
        _STATE["exports"].append(out)
        del _STATE["exports"][:-1]
        return out
    setattr(emod, a, ex)
    _PATCHED.append(True)


# =============================================================================
# widget helpers
# =============================================================================
class Ctx:
    def __init__(self, timeout=600):
        self.timeout = timeout
        self.runs, self.run_s = 0, 0.0
        self.errors, self.warnings, self.expected = [], [], []
        self.refusals, self.limits = [], []
        self.exceptions = []

    def clear_messages(self):
        for lst in (self.errors, self.warnings, self.expected, self.refusals, self.limits):
            lst.clear()

    def as_dict(self):
        return dict(runs=self.runs, run_s=round(self.run_s, 2), errors=self.errors,
                    warnings=self.warnings, expected=self.expected[:10], refusals=self.refusals,
                    limits=self.limits, exceptions=self.exceptions)


def _expected(msg):
    return any(re.search(p, msg, re.I) for p in U.EXPECTED_MESSAGES)


def run(at, ctx):
    t = time.perf_counter()
    at.run(timeout=ctx.timeout)
    ctx.runs += 1
    ctx.run_s += time.perf_counter() - t
    for kind, lst in (("error", at.error), ("warning", at.warning)):
        for e in lst:
            msg = str(e.value)
            cls = (ctx.refusals if any(re.search(p, msg, re.I) for p in U.REFUSAL_MESSAGES) else
                   ctx.limits if any(re.search(p, msg, re.I) for p in U.LIMIT_MESSAGES) else None)
            if cls is not None:
                if msg[:300] not in cls:
                    cls.append(msg[:300])
            elif _expected(msg):
                if msg[:160] not in ctx.expected:
                    ctx.expected.append(msg[:160])
            else:
                tgt = ctx.errors if kind == "error" else ctx.warnings
                if msg[:300] not in tgt:
                    tgt.append(msg[:300])
    if len(at.exception):
        for e in at.exception:
            ctx.exceptions.append(dict(message=str(e.message)[:500],
                                       stack="\n".join(e.stack_trace)[-3000:]))
        raise AppException(str(at.exception[0].message)[:300])


def find(at, kind, key=None, label=None):
    if key is not None:
        try:
            return getattr(at, kind)(key=key)
        except (KeyError, IndexError):
            return None
    for w in getattr(at, kind):
        if w.label == label:
            return w
    return None


def need(at, kind, key=None, label=None):
    w = find(at, kind, key, label)
    if w is None:
        raise UIError(f"widget not found: {kind} {key or label!r} (update dev/qa/ui_map.py)")
    return w


def choose(w, want):
    """Radio / selectbox: an option equal to `want`, else the first starting with
    it, else `want` as the underlying value (widgets with a format_func)."""
    opts = [str(o) for o in w.options]
    if want in opts:
        val = want
    else:
        val = next((o for o in opts if o.startswith(want)), want)
    if w.value != val:
        w.set_value(val)
    return val


def setnum(w, v):
    if w.value is None or abs(float(w.value) - float(v)) > 1e-12 * max(1.0, abs(float(v))):
        w.set_value(v)


def setbox(w, v):
    if bool(w.value) != bool(v) and not w.disabled:
        w.set_value(bool(v))


# =============================================================================
# Custom H(s) specs
# =============================================================================
def custom_spec(case):
    """custom_tf spec dict for one matrix case: a scipy prototype normalized to
    w = 1 (BP/BR geometric centre 1), written in the case's entry form."""
    import numpy as np
    import scipy.signal as ss
    import custom_tf as ct
    kind, n, bt = case["proto"]
    wn = [1 / 1.5, 1.5] if bt in ("bandpass", "bandstop") else 1.0
    if kind == "butter":
        z, p, k = ss.butter(n, wn, bt, analog=True, output="zpk")
    elif kind == "cheby1":
        z, p, k = ss.cheby1(n, 1.0, wn, bt, analog=True, output="zpk")
    elif kind == "ellip":
        z, p, k = ss.ellip(n, 1.0, 40.0, wn, bt, analog=True, output="zpk")
    else:
        z, p, k = ss.bessel(n, wn, bt, analog=True, output="zpk", norm="mag")
    z, p, k = list(z), list(p), float(k)
    absolute = case.get("scale") == "absolute"
    sc = 2 * math.pi * 1e3 if absolute else 1.0
    z, p, k = [x * sc for x in z], [x * sc for x in p], k * sc ** (len(p) - len(z))
    sp = ct.default_spec()
    sp.update(form=case["form"], scale="absolute" if absolute else "normalized", f_norm=1.0,
              unit="kHz")
    up = lambda rs: [r for r in rs if r.imag > 1e-12 or abs(r.imag) <= 1e-12]   # noqa: E731
    form = case["form"]
    if form == "roots":
        rows = lambda rs: [[float(r.real), float(abs(r.imag)) if abs(r.imag) > 1e-12 else 0.0]   # noqa: E731
                           for r in up(rs)]
        sp["roots"] = {"poles": rows(p), "zeros": rows(z), "K": k, "k_form": "K"}
    elif form == "f0q":
        pp = [{"f0": abs(r) * (1 / (2 * math.pi * 1e3) if absolute else 1.0),
               "Q": abs(r) / (-2 * r.real)} for r in p if r.imag > 1e-12]
        rp = [float(-r.real) * (1 / (2 * math.pi * 1e3) if absolute else 1.0)
              for r in p if abs(r.imag) <= 1e-12]
        zp = [{"fz": abs(r) * (1 / (2 * math.pi * 1e3) if absolute else 1.0), "Qz": None}
              for r in z if r.imag > 1e-12 and abs(r.real) < 1e-9]
        n0 = sum(1 for r in z if abs(r) < 1e-12)
        sp["f0q"] = {"pole_pairs": pp, "real_poles": rp, "zero_pairs": zp, "real_zeros": [],
                     "n_origin": n0, "K": k}
    elif form == "coeff":
        num = (np.poly(z) * k).real if z else np.array([k])
        den = np.poly(p).real
        sp["coeff"] = {"num": [float(x) for x in num], "den": [float(x) for x in den],
                       "order": "desc"}
    elif form == "ts":
        stages = []
        for r in p:
            if r.imag > 1e-12:
                w0, q = abs(r), abs(r) / (-2 * r.real)
                stages.append({"a": 1 / (w0 * q), "b": 1 / w0 ** 2})
            elif abs(r.imag) <= 1e-12:
                stages.append({"a": 1 / abs(r), "b": 0.0})
        h0 = k * np.prod([-x for x in z]) / np.prod([-x for x in p])
        sp["ts"] = {"stages": stages, "A0": float(abs(h0))}
    return sp


# =============================================================================
# design -> sidebar
# =============================================================================
def apply_design(at, d, ctx):
    resp, ft = d["response"], d["ftype"]
    cu = d.get("custom")
    if cu:
        at.session_state[U.SS_CUSTOM_SPEC] = custom_spec(cu)
        at.session_state[U.CUSTOM_FORM] = cu["form"]
    choose(need(at, "radio", label=U.RESPONSE), resp)
    run(at, ctx)

    if cu:
        mode = need(at, "radio", key=U.CUSTOM_MODE)
        choose(mode, "Complete" if cu["mode"] == "complete" else "Lowpass prototype")
        run(at, ctx)
        if cu["mode"] == "prototype":
            choose(need(at, "radio", key=U.FILTER_TYPE), ft)
            run(at, ctx)
            if ft in ("Bandpass", "Band-Reject"):
                choose(need(at, "radio", key=U.CUSTOM_PBDEF),
                       "Normalized width" if cu.get("pbdef") == "width" else "Corners")
                run(at, ctx)
        elif cu.get("scale") == "absolute":
            w = find(at, "radio", key=U.CUSTOM_SCALE)
            if w is not None and not w.disabled:
                choose(w, "absolute")
                run(at, ctx)
        gm = find(at, "radio", key=U.CUSTOM_GAIN_MODE)
        if gm is not None:
            choose(gm, "Normalize" if cu.get("gain_mode") == "normalize" else "As entered")
            run(at, ctx)
    elif resp in ("Bessel", "Equiripple Delay"):
        choose(need(at, "radio", key=U.FILTER_TYPE_DELAY), ft)
        run(at, ctx)
    else:
        choose(need(at, "radio", key=U.FILTER_TYPE), ft)
        run(at, ctx)

    # ---- structure: order / asymmetry / delay order selection
    dl = d.get("delay") or {}
    if dl:
        choose(need(at, "radio", key=U.DELAY_ORDER_MODE),
               "From specs" if dl.get("mode") == "specs" else "Manual")
        run(at, ctx)
        if ft == "Lowpass":
            w = find(at, "radio", key=U.DELAY_ANCHOR)
            if w is not None:
                choose(w, "Group delay" if dl.get("anchor") == "delay" else "Corner frequency")
                run(at, ctx)
        if dl.get("bp_map"):
            choose(need(at, "radio", key=U.DELAY_BP_MAP),
                   "Classic" if dl["bp_map"] == "classic" else "Delay-preserving")
        if dl.get("mode") == "specs" and ft == "Lowpass":
            key = U.CRIT_DELAY if dl.get("anchor") == "delay" else U.CRIT_CORNER
            w = need(at, "radio", key=key)
            opts = [str(o) for o in w.options]
            pick = {"first": opts[0], "flat": next(o for o in opts if o.startswith("Flat")),
                    "stopband": next(o for o in opts if o.startswith("Stopband"))}[dl.get("crit", "first")]
            choose(w, pick)
        if d.get("ripple") is not None:
            setnum(need(at, "number_input", key=U.DELAY_RIPPLE), d["ripple"])
        run(at, ctx)
    if d.get("asym"):
        setbox(need(at, "checkbox", key=U.ASYM), True)
        run(at, ctx)
        setnum(need(at, "number_input", key=U.ORDER_LP), d["lp"])
        setnum(need(at, "number_input", key=U.ORDER_HP), d["hp"])
    elif d.get("order") and not cu and dl.get("mode") != "specs":
        setnum(need(at, "number_input", key=U.ORDER), d["order"])

    # ---- frequencies (f2 first: the callbacks keep f2 > f1)
    if "unit" in d and not (cu and cu["mode"] == "complete"):
        choose(need(at, "radio", label=U.UNIT), d["unit"])
    if "f2" in d and not (cu and cu["mode"] == "complete"):
        if cu and cu.get("pbdef") == "width":
            setnum(need(at, "number_input", key=U.CUSTOM_FN), math.sqrt(d["f1"] * d["f2"]))
            setnum(need(at, "number_input", key=U.CUSTOM_BW),
                   (d["f2"] - d["f1"]) / math.sqrt(d["f1"] * d["f2"]))
        else:
            setnum(need(at, "number_input", key=U.F2), d["f2"])
            run(at, ctx)
            setnum(need(at, "number_input", key=U.F1), d["f1"])
    elif "fc" in d and not (cu and cu["mode"] == "complete"):
        w = find(at, "number_input", key=U.FC)
        if w is not None:                       # hidden under the group-delay anchor
            setnum(w, d["fc"])
    w = find(at, "number_input", key=U.GAIN)
    if w is not None and d.get("gain") and not w.disabled:
        setnum(w, d["gain"])
    if d.get("alpha") is not None:
        w = next((x for x in at.sidebar.number_input if x.label in U.ALPHA_LABELS), None)
        if w is None:
            raise UIError(f"widget not found: alpha {U.ALPHA_LABELS}")
        setnum(w, d["alpha"])
    if d.get("as_l") is not None:
        setnum(need(at, "number_input", key=U.AS_LOWER), d["as_l"])
        setnum(need(at, "number_input", key=U.AS_UPPER), d["as_u"])
    elif d.get("as") is not None:
        w = find(at, "number_input", key=U.AS_SYM) or find(at, "number_input", label=U.AS_LABEL)
        if w is None:
            raise UIError("widget not found: A_s")
        setnum(w, d["as"])
    for mod, on in (d.get("mods") or {}).items():
        setbox(need(at, "checkbox", label=U.MODS[mod]), on)
    # Messages of the intermediate states the widgets passed through (e.g. the
    # default prototype before Complete mode) are not the design's: keep only
    # the final state's. Exceptions are always kept.
    ctx.clear_messages()
    run(at, ctx)

    # ---- Biquad Pairing choices
    if d.get("absorb"):
        w = find(at, "checkbox", label=U.ABSORB)
        if w is not None:                       # only shown when there are real poles
            setbox(w, True)
            run(at, ctx)
    if d.get("gain_dist", "even") != "even":
        w = find(at, "radio", label=U.GAIN_DIST)
        if w is not None:
            want = U.GAIN_DIST_PREFIX[d["gain_dist"]]
            if any(str(o).startswith(want) for o in w.options):
                choose(w, want)
                run(at, ctx)


def snapshot(at):
    ss = at.session_state

    def g(k):
        return ss[k] if k in ss else None
    stages = []
    for s in (g(U.SS_PAIRING) or {}).get("stages", []):
        stages.append({k: v for k, v in s.items()})
    return dict(engine=g(U.SS_ENGINE), stages=stages, sections=g(U.SS_SECTIONS),
                spec=g(U.SS_SPEC), unassigned=g(U.SS_UNASSIGNED),
                incomplete=g(U.SS_INCOMPLETE), spec_short=g(U.SS_SPEC_SHORT))


def _detected_type(snap):
    for r in snap.get("spec") or []:
        if isinstance(r, (list, tuple)) and len(r) >= 2 and str(r[0]).lower() == "filter type":
            return str(r[1])
    return None


# =============================================================================
# variant -> Topology tab
# =============================================================================
def resolve_opamp(sel):
    import opamp_library as L
    lib = L.library()
    if sel == "ideal" or not lib:
        return L.IDEAL_LABEL
    if sel == "typical":
        if "TL072H" in lib:
            return "TL072H"
        names = sorted(lib, key=lambda n: lib[n]["GBWP_hz"])
        return names[len(names) // 2]
    if sel == "slow":
        return min(lib, key=lambda n: lib[n]["GBWP_hz"])
    return sel


def apply_variant(at, v, sections, ctx):
    import matrix as M
    import topology_tab as TT
    tog = need(at, "toggle", key=U.BATCH)
    if not tog.value:
        tog.set_value(True)
        run(at, ctx)
    choose(need(at, "selectbox", key=U.OPAMP), resolve_opamp(v["opamp"]))
    env = M.ENVELOPES[v["env"]]
    for k, key in U.ENV.items():
        setnum(need(at, "number_input", key=key), env[k])
    choose(need(at, "radio", key=U.CSER), env["cser"])
    for s in env["rser"]:
        setbox(need(at, "checkbox", key=U.RSER.format(s)), True)
    for s in U.R_SERIES:
        if s not in env["rser"]:
            setbox(need(at, "checkbox", key=U.RSER.format(s)), False)
    w = find(at, "select_slider", key=U.EFFORT)
    if w is not None and w.value != v.get("preset", "Balanced"):
        w.set_value(v.get("preset", "Balanced"))
    kinds = {}
    for sec in sections:
        n = sec["stage_num"]
        kinds[n] = TT.section_kind(sec)[0]
        if kinds[n] in ("lp", "hp", "notch", "bp"):
            choose(need(at, "radio", key=U.FAM.format(n)), U.FAM_PREFIX[v["family"]])
    run(at, ctx)
    if v.get("famopt") == "alt":
        for n in kinds:
            if kinds[n] == "first_order":         # alt: the inverting 1st-order cells
                w = find(at, "radio", key=U.FO_REAL.format(n))
                if w is not None:
                    choose(w, "Inverting")
            elif v["family"] == "MFB":
                for key in (U.MFB_LS, U.MFB_GAINED):
                    w = find(at, "checkbox", key=key.format(n))
                    if w is not None:
                        setbox(w, True)
            elif v["family"] == "AM":
                w = find(at, "checkbox", key=U.AMEQ.format(n))
                if w is not None:
                    setbox(w, False)
        run(at, ctx)
    if v.get("gainvar") == "atten":
        chk = []
        for n, kd in kinds.items():
            w = find(at, "checkbox", key=U.DC_CHK.format(n))
            if w is not None and kd in ("lp", "hp", "notch", "first_order") and not w.disabled:
                setbox(w, True)
                chk.append(n)
        if chk:
            run(at, ctx)
            for n in chk:
                w = find(at, "number_input", key=U.DC_VAL.format(n))
                if w is not None and not w.disabled:
                    setnum(w, M.ATTEN_GAIN)
            run(at, ctx)
    return kinds


def _job_section(job, sections):
    cfg = job["args"][0] if job["args"] else {}
    for sec in sections:
        if (abs(float(sec["f0_hz"]) - float(cfg.get("f0", -1))) <= 1e-9 * float(sec["f0_hz"])
                and abs(float(sec["Q"]) - float(cfg.get("Q", -1))) <= 1e-9 * max(float(sec["Q"]), 1)):
            return sec
    return None


def _new_app(timeout):
    from streamlit.testing.v1 import AppTest
    from common import ROOT
    import topology_tab as TT
    getattr(TT, U.PICKS_GLOBAL[1]).clear()
    return AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=timeout)


# =============================================================================
# tasks
# =============================================================================
def design_task(design, variants, timeout=600):
    """Sidebar + snapshot + analysis + captured solve jobs per variant."""
    import analysis
    import matrix as M
    install_patches()
    _STATE["mode"] = "capture"
    ctx = Ctx(timeout)
    out = dict(id=design["id"], design=design, status="ok", variants=[], jobs=[])
    t0 = time.perf_counter()
    at = _new_app(timeout)
    try:
        run(at, ctx)
        out["ui_options"] = {w.label: [str(o) for o in w.options] for w in at.sidebar.radio
                             if w.label in (U.RESPONSE, "Filter Type")}
        apply_design(at, design, ctx)
        if ctx.refusals:                   # the app stopped: its session state is a stale design
            out["status"] = "refused"
            out["ui"] = ctx.as_dict()
            out["wall"] = time.perf_counter() - t0
            return out
        snap = snapshot(at)
        out["snapshot"] = jsonable(dict(snap, stages=[{k: v for k, v in s.items()} for s in snap["stages"]]))
        if design.get("custom") and design["custom"]["mode"] == "complete":
            out["detected_type"] = _detected_type(snap)
        out["analysis"] = jsonable(analysis.analyse(design, snap))
        sections = snap.get("sections") or []
        if not sections:
            out["status"] = "no_sections"
        elif out["analysis"]["max_q"] > M.MAX_Q_SOLVE:
            out["status"] = "q_too_high"
        elif design.get("solve") and variants:
            for vid, v in variants:
                rec = dict(id=vid, variant=v, error=None)
                try:
                    _STATE["jobs"].clear()
                    _STATE["first_order"].clear()
                    kinds = apply_variant(at, v, sections, ctx)
                    need(at, "button", key=U.SOLVE_ALL).click()
                    run(at, ctx)
                    rec["kinds"] = kinds
                    rec["first_order"] = list(_STATE["first_order"].values())
                    jobs = list(_STATE["jobs"])
                    at.session_state[U.SS_JOBS] = {}
                    for j in jobs:
                        sec = _job_section(j, sections)
                        j.update(design_id=design["id"], variant_id=vid,
                                 stage=sec["stage_num"] if sec else None,
                                 section=jsonable(sec) if sec else None,
                                 topologies=list(j["kwargs"].get("topologies") or []),
                                 dc_gain=j["kwargs"].get("dc_gain"))
                        j["key"] = short_hash((j["fn"], j["args"], sorted(j["kwargs"].items())), 16)
                        j["cid"] = f"{design['id']}|{vid}|S{j['stage']}|{'+'.join(j['topologies'])}"
                    rec["n_jobs"] = len(jobs)
                    out["jobs"] += jobs
                except (UIError, AppException) as e:
                    rec["error"] = f"{type(e).__name__}: {e}"
                out["variants"].append(rec)
    except UIError as e:
        out.update(status="ui_error", error=str(e), traceback=traceback.format_exc()[-3000:])
    except AppException as e:
        out.update(status="app_exception", error=str(e))
    out["ui"] = ctx.as_dict()
    out["wall"] = time.perf_counter() - t0
    return out


def _validate_pdf(b):
    if not b:
        return dict(ok=False, why="no bytes")
    pages = len(re.findall(rb"/Type\s*/Page[^s]", b))
    ok = b[:5] == b"%PDF-" and b.rstrip()[-5:] == b"%%EOF" and pages >= 1
    return dict(ok=ok, size=len(b), pages=pages)


def e2e_task(design, vid, variant, out_dir, timeout=900):
    """Full user flow with synchronous solves -> picks, MC, report, LTspice export."""
    install_patches()
    _STATE["mode"] = "sync"
    _STATE["jobs"].clear()
    _STATE["exports"].clear()
    ctx = Ctx(timeout)
    out = dict(id=f"{design['id']}|{vid}", design_id=design["id"], variant_id=vid, status="ok",
               steps={})
    os.makedirs(out_dir, exist_ok=True)
    at = _new_app(timeout)

    def step(name, fn):
        t = time.perf_counter()
        r = fn()
        out["steps"][name] = round(time.perf_counter() - t, 2)
        return r
    try:
        run(at, ctx)
        step("design", lambda: apply_design(at, design, ctx))
        if ctx.refusals:
            out["status"] = "refused"
            return out
        snap = snapshot(at)
        sections = snap.get("sections") or []
        if not sections:
            out["status"] = "no_sections"
            return out
        kinds = step("variant", lambda: apply_variant(at, variant, sections, ctx))

        def solve():
            need(at, "button", key=U.SOLVE_ALL).click()
            run(at, ctx)
            run(at, ctx)                         # drain the (already finished) futures
        step("solve", solve)
        out["jobs"] = [dict(fn=j["fn"], topologies=j["kwargs"].get("topologies"),
                            result=j.get("result"), wall=j.get("wall")) for j in _STATE["jobs"]]

        def pick():
            for n, kd in kinds.items():
                if kd == "pending":
                    continue
                w = find(at, "selectbox", key=U.SORT.format(n))
                if w is not None and U.SORT_BY in [str(o) for o in w.options]:
                    choose(w, U.SORT_BY)
            run(at, ctx)
            for n, kd in kinds.items():
                if kd != "pending" and find(at, "selectbox", key=U.SORT.format(n)) is not None:
                    at.session_state[U.DF.format(n)] = {"selection": {"rows": [0], "columns": [],
                                                                      "cells": []}}
            run(at, ctx)
        step("pick", pick)
        picks = at.session_state[U.SS_PICKS] if U.SS_PICKS in at.session_state else {}
        realizable = [n for n, kd in kinds.items() if kd != "pending"]
        out["picked"] = {str(n): dict(topology=r.get("topology"), snap_cost=r.get("snap_cost"),
                                      sens=r.get("sens_score")) for n, r in picks.items()}
        missing = [n for n in realizable if n not in picks]
        if missing:
            out["status"] = "no_bom"
            out["missing_sections"] = missing
            return out

        def mc():
            w = find(at, "number_input", key=U.MC_RUNS)
            if w is not None:
                setnum(w, 200)
            b = find(at, "button", key=U.MC_BUTTON)
            if b is not None:
                b.click()
            w = find(at, "number_input", key=U.SPICE_MC_RUNS)
            if w is not None:                    # LTspice MC netlist: 50 runs, not 2000
                setnum(w, LTSPICE_MC_RUNS)
            run(at, ctx)
        step("monte_carlo", mc)

        def report():
            for k in U.REPORT_OPTS:
                w = find(at, "checkbox", key=k)
                if w is not None and not w.disabled:
                    setbox(w, True)
            w = find(at, "text_input", key=U.REPORT_TITLE)
            if w is not None:
                w.set_value(f"QA {design['id']}")
            need(at, "button", key=U.REPORT_BUTTON).click()
            run(at, ctx)
            rp = at.session_state[U.SS_REPORT_PDF] if U.SS_REPORT_PDF in at.session_state else None
            return rp and rp.get("bytes")
        pdf = step("report", report)
        out["report"] = _validate_pdf(pdf)
        if pdf:
            with open(os.path.join(out_dir, "report.pdf"), "wb") as f:
                f.write(pdf)
        ex = _STATE["exports"][-1] if _STATE["exports"] else None
        if ex is None:
            out["export"] = dict(ok=False, why="the app built no LTspice export")
        else:
            lt = os.path.join(out_dir, "ltspice")
            os.makedirs(lt, exist_ok=True)
            for name, data in list(ex["files"].items()) + list((ex.get("extra_files") or {}).items()):
                p = os.path.join(lt, name)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
            out["export"] = dict(ok=True, dir=lt, files=sorted(ex["files"]),
                                 expected=jsonable(ex["expected"]), all_generic=ex["all_generic"],
                                 asc_error=ex["asc_error"], warnings=ex["warnings"],
                                 loaded_dev_db=ex.get("loaded_dev_db"),
                                 drawings=[s.get("drawing") for s in ex.get("sections", [])])
    except UIError as e:
        out.update(status="ui_error", error=str(e), traceback=traceback.format_exc()[-3000:])
    except AppException as e:
        out.update(status="app_exception", error=str(e))
    finally:
        out["ui"] = ctx.as_dict()
        _STATE["mode"] = "capture"
    return out
