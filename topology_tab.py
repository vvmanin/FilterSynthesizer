# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  topology_tab.py   (v2)
#  Streamlit "Topology & Hardware Synthesis" tab.
#
#  Consumes the per-section summary the pairing tab publishes into
#  st.session_state.hw_sections, classifies each section, and dispatches
#  the General LP solver (filter_synthesis.synthesize) on a *gated*
#  background fabric:
#    - a ~1-min 32-core solve never blocks the Streamlit script thread
#    - simultaneous users never oversubscribe the box (BoundedSemaphore)
#    - the solver's own ProcessPoolExecutor is never nested (called from a
#      thread, never submitted into a process pool)
#    - stale results discarded via a generation token (hw_gen)
#    - per-section results cached, so reruns / tab switches never resolve
#
#  v2 changes:
#    - Per-section: topology family (VCVS now; MFB/others stubbed),
#      component envelope, C-series (single), R-series (multi).
#    - Global "Convergence Settings" expander: search effort + pole_tol
#      + gain_tol + reg_weight.
#    - Cores frozen at 32 (no UI) for the web demo.
#    - Default rank = sens_score; metric-only sort (component columns carry
#      k/M suffixes that would sort as text, so their sort is suppressed).
#    - Pick a solution via single-row dataframe selection (index in col '#'
#      is the fallback if your Streamlit lacks selection).
#
#  Requires Streamlit >= 1.37 (st.fragment(run_every=...), st.dataframe
#  selection, st.toggle).
# =====================================================================

import math
import os
import threading
import concurrent.futures

import numpy as np
import pandas as pd
import streamlit as st

from filter_synthesis import synthesize, IDEAL_OPAMP
from tf_derivation_v2 import make_response_func
from tf_derivation_v2 import cell_struct_sig as TF_struct_sig
from discrete_snapper import _fmt_cap, _fmt_res   # uF for caps, MOhm for R
import cells_mfb_hp
from cells_mfb_hp import HF_GAIN_MARGIN, V2_GAIN_FLOOR, notch_cells_for_gain

import streamlit.components.v1 as components
import schematic_svg as schematic                  # per-section schematic overlay
import pairing_utils                               # classify_section / family_from_section
import first_order_solver as fos                   # closed-form 1st-order realizer
import solvability_probe as solvprobe              # failure-path global feasibility probe

# Module-level durable pick store: stage_num -> chosen BOM row. Survives even a
# full st.session_state reset / clear (single-user web demo). Mirrored into
# session_state["bom_picks"] (note: NOT hw_-prefixed, so a prefix-based state
# reset elsewhere can't wipe it).
_PICKS = {}


def _eng(v):
    """Compact scientific string, e.g. 1e+5, 9.5e+4 (no leading-zero exponent)."""
    try:
        s = f"{float(v):.2e}"            # "1.00e+05"
    except (TypeError, ValueError):
        return str(v)
    m, e = s.split("e")
    m = m.rstrip("0").rstrip(".")        # "1", "9.5"
    e = int(e)
    return f"{m}e{'+' if e >= 0 else '-'}{abs(e)}"


def opamp_label(n):
    """String shown under U{n} in the schematic: the part number, or — when
    'Custom…' is selected — the op-amp parameter string
    'A_ol = 1e+5; GBWP = 9.5e+4 Hz; Ro = 1200 Ω'. None if no op-amp chosen."""
    choice = st.session_state.get(f"hw_opamp_choice_{n}")
    if not choice:
        return None
    if OPAMP_LIBRARY.get(choice) == "CUSTOM":
        a = st.session_state.get(f"hw_aol_{n}", 1e5)
        g = st.session_state.get(f"hw_gbwp_{n}", 0.95e5)
        r = st.session_state.get(f"hw_ro_{n}", 1000.0)
        return f"A_ol = {_eng(a)}; GBWP = {_eng(g)} Hz; Ro = {r:g} Ω"
    return str(choice).split("/")[0].strip()


# =====================================================================
#  CONFIG
# =====================================================================
# Hardcoded for the web demo. Expose a control only for local/desktop use.
N_CORES = 32

# Ro in MOhm (1.0e-3 MOhm = 1 kohm). Extend with real parts over time.
OPAMP_LIBRARY = {
    "Ideal (no op-amp limits)": None,
    "AD8505 / AD8506 / AD8508": dict(A_ol=1e5, GBWP_hz=0.95e5, Ro=1.0e-3),
    "LMV358A": dict(A_ol=1e5, GBWP_hz=1e6, Ro=1.2e-3),
    "MAX9636/MAX9637/ MAX9638": dict(A_ol=1e5, GBWP_hz=1.5e6, Ro=1e-4),
    "MAX40100": dict(A_ol=1.41e6, GBWP_hz=1.5e6, Ro=1e-4),
    "Custom…": "CUSTOM",
}

# Search-effort -> unified_solver multistart breadth.
SEARCH_PRESETS = {
    "Fast":     dict(ratio_starts=30,  anchored_starts=60,  max_valleys=6,  hints_per_combo=2),
    "Balanced": dict(ratio_starts=60,  anchored_starts=120, max_valleys=12, hints_per_combo=3),
    "Thorough": dict(ratio_starts=120, anchored_starts=240, max_valleys=20, hints_per_combo=4),
}

# Cap solver supports E6/E12/E24 today; E3 needs the one-line _E_SERIES patch.
C_SERIES_OPTIONS = ["E3", "E6", "E12"]
# Resistor grid supports exactly these.
R_SERIES_OPTIONS = ["E12", "E24", "E48", "E96"]

TOPOLOGY_FAMILIES = ["VCVS (Sallen-Key)", "MFB (Friend)", "AM (Ackerberg–Mossberg)"]   # VCVS/MFB cover all sections; AM = 3-op-amp state-variable biquad (LP/LPn, HP/HPn, BP, pure-notch)

# Attenuator cells (2LP-atten / 2LPn-atten = unity SK + R7 input divider,
# DC gain < 1) are defined in tf_derivation_v2 and verified.
ATTEN_AVAILABLE = True

GAIN_UNITY_TOL = 0.02   # |H(0) - 1| within this -> unity cell admissible


def _gain_label_for(topo_name):
    """Passband-gain column/label by family, inferred from the cell name.
    The VCVS notch (2N-*) is inverting with H(0) = H(inf), so its passband
    gain is the SAME at DC and HF -> 'DC/HF gain'. HP/HPn passbands sit at HF
    -> 'HF gain'; LP at DC -> 'DC gain'. ('2HPn-*' starts with '2H', not
    '2N', so the notch test never catches the HP-notch cells.)"""
    nm = topo_name or ""
    if nm.startswith("2BP"):
        return "Center gain"           # band-pass passband is the resonant peak
    if nm.startswith("2N"):
        return "DC/HF gain"
    return "HF gain" if "HP" in nm else "DC gain"


# =====================================================================
#  BACKGROUND JOB FABRIC  (one cross-session singleton)
# =====================================================================
@st.cache_resource
def _job_runner():
    """`pool`: dispatch threads (each calls synthesize(), which spins its
    OWN process pool -- calling from a thread avoids nesting). `gate`: bound
    concurrent heavy solves. 1 == one solve saturates the box; next queues."""
    return {
        "pool": concurrent.futures.ThreadPoolExecutor(max_workers=4),
        "gate": threading.BoundedSemaphore(1),
    }


def _ensure_state():
    st.session_state.setdefault("hw_results", {})
    st.session_state.setdefault("hw_jobs", {})
    st.session_state.setdefault("hw_picked", {})   # stage_num -> chosen BOM row
    st.session_state.setdefault("bom_picks", {})   # durable mirror (no hw_ prefix on purpose)
    st.session_state.setdefault("hw_picked_meta", {})  # stage_num -> {case, eval_opamp, mode}


# =====================================================================
#  CLASSIFICATION
# =====================================================================
def section_dc_gain(sec):
    """Section passband gain implied by the rad/s K (which the Pairing tab sets
    from its Remaining-Gain-Distribution). H = K·N(s)/D(s) with N, D monic, so the
    passband gain is K·N(0)/D(0) for LP/allpole/notch (passband at DC) and the
    leading-coefficient ratio K for HP (passband at HF — the origin zeros block
    DC). This is the per-section default gain and the unity/gained selector.
    (1st-order: D = s+ωp, ωp = 2π f0.)"""
    w0 = 2 * np.pi * sec["f0_hz"]
    fam = pairing_utils.family_from_section(sec)
    if fam in ("BP", "BP1LP", "BP1HP"):
        # Band-pass passband is the resonant peak at f0. With a MONIC
        # denominator and numerator Ki·s^m, evaluating at s = jω₀ gives
        #   2nd order  (m=1): |H| = Ki·Q/ω₀                    Ki in rad/s
        #   BP1HP      (m=2): |H| = Ki·Q/|jω₀+p₁|              Ki in rad/s
        #   BP1LP      (m=1): |H| = Ki·Q/(ω₀·|jω₀+p₁|)         Ki in (rad/s)²
        # because the biquad factor contributes jω₀²/Q at resonance and the
        # absorbed real pole contributes the extra (jω₀+p₁). The Ki UNITS differ
        # per shape -- pairing_utils.compute_stage_gains already emits
        # b_lead/a_lead, i.e. (rad/s)^(n_poles-n_zeros), so it is right by
        # construction and nothing upstream needs a rescale.
        if not w0:
            return 1.0
        if fam == "BP":
            return sec["K_radps"] * sec["Q"] / w0
        pp = 2 * np.pi * float(sec.get("f1_hz") or 0.0)
        mag = abs(complex(pp, w0))                     # |jω₀ + p₁|
        if mag <= 0:
            return 1.0
        num = sec["K_radps"] * sec["Q"]
        return num / mag if fam == "BP1HP" else num / (w0 * mag)
    is_hp = fam in ("HP", "HPn") or bool(sec.get("has_origin_zero"))
    if sec["order"] == 1:
        return sec["K_radps"] if is_hp else sec["K_radps"] / w0   # HF gain : DC gain
    if is_hp:
        # HP passband sits at HF: H(inf) = leading-coeff ratio = K (numerator
        # ~ K·s^order over a monic denominator). The origin zeros block DC.
        return sec["K_radps"]
    a0 = (2 * np.pi * sec["f1_hz"]) * w0 ** 2 if sec["order"] == 3 else w0 ** 2
    b0 = (2 * np.pi * sec["fz_hz"]) ** 2 if sec["notch"] else 1.0
    return sec["K_radps"] * b0 / a0


def gain_mode(sec):
    return "unity" if abs(section_dc_gain(sec) - 1.0) <= GAIN_UNITY_TOL else "gained"


def section_kind(sec):
    """(kind, reason): 'lp' | 'hp' | 'notch' | 'first_order' | 'pending'.

    Thin wrapper over the authoritative classifier (pairing_utils.
    family_from_section), feeding the 4.4 dispatch:
      order-1 LP/HP            -> 'first_order' (closed-form solver, item 1)
      order 2/3 LP/LPn         -> 'lp'          (12-cell LP unified solver)
      order 2/3 HP/HPn         -> 'hp'          (16-cell HP unified solver, item 2)
      order 2   pure notch     -> 'notch'       (VCVS 2N notch solver, item 3)
      every other family       -> 'pending'     (NEVER sent to a mismatched
                                                 solver -- the 4.4 garbage gate)
    Gain mode (unity vs gained vs atten) is decided LATER from the effective
    gain, so a per-section Custom-gain override can still flip the cell."""
    fam = pairing_utils.family_from_section(sec)
    order = int(sec.get("order", 2))
    if order == 1 and fam in ("LP", "HP"):
        return ("first_order", None)
    if fam in ("LP", "LPn") and order in (2, 3):
        return ("lp", None)
    if fam in ("HP", "HPn") and order in (2, 3):
        return ("hp", None)
    if fam == "notch" and order == 2:
        return ("notch", None)          # VCVS 2N pure-notch cells (item 3)
    if fam == "BP" and order == 2:
        return ("bp", None)             # VCVS Sallen-Key band-pass cells (item 5)
    if fam in ("BP1LP", "BP1HP") and order == 3:
        # 3rd-order asymmetric band-pass: complex pair + absorbed real pole.
        # Same 'bp' kind on purpose -- the Ki-based gain control, the snapper's
        # peak-normalized mode and the Resulting-Response inclusion test all key
        # off it, and every one of those applies unchanged. Only the CELL SET
        # differs, and that is chosen from `fam` further down.
        return ("bp", None)
    label = {"notch": "pure notch", "BP": "band-pass",
             "BP1LP": "band-pass + absorbed real pole",
             "BP1HP": "band-pass + absorbed real pole"}.get(fam, fam)
    return ("pending", f"{label} section (family {fam}) — solver not yet available; "
                       "gated to avoid mis-solving on a mismatched cell.")


# =====================================================================
#  cfg + signature
# =====================================================================
def _build_cfg(sec, env, conv, k_override=None):
    """f1/fz are dummies (= f0) for cells that ignore them, but must exist
    (run_synthesis reads them unconditionally). K is the rad/s leading coeff.

    k_override (band-pass only): when not None it REPLACES sec["K_radps"] as
    cfg["K"] -- the effective Ki the solver targets (the section's pairing Ki,
    or the user's per-section override). Backward-compatible (None -> the old
    behaviour for every other family)."""
    return dict(
        env,
        reg_weight=conv["reg_weight"],
        f0=sec["f0_hz"], Q=sec["Q"],
        fz=sec["fz_hz"] if sec["notch"] else sec["f0_hz"],
        f1=sec["f1_hz"] if sec["order"] == 3 else sec["f0_hz"],
        K=float(k_override) if k_override is not None else sec["K_radps"],
    )


def _job_sig(sec, topo, env, conv, opamp, dc_override):
    env_t = tuple(sorted((k, str(v)) for k, v in env.items()))
    conv_t = tuple(sorted((k, str(v)) for k, v in conv.items()))
    op_t = ("ideal",) if opamp is None else tuple(sorted(opamp.items()))
    return (
        sec["stage_num"], topo,
        round(sec["f0_hz"], 6), round(sec["Q"], 6),
        round(sec["fz_hz"], 6) if sec["notch"] else None,
        round(sec["f1_hz"], 6) if sec["order"] == 3 else None,
        round(sec["K_radps"], 9),
        None if dc_override is None else round(float(dc_override), 9),
        env_t, conv_t, op_t,
    )


# =====================================================================
#  SUBMIT / DRAIN
# =====================================================================
def _submit(sig, cfg, opamp, topos, conv, dc_override, gen):
    R = _job_runner()
    topos = [topos] if isinstance(topos, str) else list(topos)

    def task():
        with R["gate"]:                      # bound concurrency across users
            return synthesize(
                cfg, opamp=opamp, topologies=topos,
                dc_gain=dc_override,         # None -> solver uses cfg["K"]
                n_cores=N_CORES,             # frozen for the demo
                ratio_starts=conv["ratio_starts"],
                anchored_starts=conv["anchored_starts"],
                max_valleys=conv["max_valleys"],
                hints_per_combo=conv["hints_per_combo"],
                pole_tol=conv["pole_tol"],
                gain_tol=conv["gain_tol"],
                top_k=conv["top_k"],         # prune before the costly non-ideal + snap
                verbose=False,
            )

    st.session_state.hw_jobs[sig] = {"future": R["pool"].submit(task), "gen": gen}


def _drain_finished():
    """True if at least one job completed on this tick (the Response tab, which
    lives outside this fragment, then needs an app-wide rerun)."""
    import traceback
    cur = st.session_state.get("hw_gen")
    drained = False
    for sig, job in list(st.session_state.hw_jobs.items()):
        fut = job["future"]
        if not fut.done():
            continue
        try:
            result = fut.result()
        except Exception as exc:
            result = {"__error__": f"{type(exc).__name__}: {exc}",
                      "__traceback__": traceback.format_exc()}
        if job["gen"] == cur:
            st.session_state.hw_results[sig] = result
        st.session_state.hw_jobs.pop(sig, None)
        drained = True
    return drained


# =====================================================================
#  RENDER HELPERS
# =====================================================================
@st.cache_resource(show_spinner=False)
def _response_fn(topo, mode, sig, _case):
    """Lambdified component->response H(comp_dict, w). Cached per
    (topo, mode, STRUCT SIG) -- the physical TF depends only on R/C, not on the
    target design, so one build is reused across sections/reruns.
    _case (underscored) isn't hashed.

    `sig` is tf_derivation_v2.cell_struct_sig(topo): a hash of the cell's
    component set. WITHOUT it the key is the cell NAME, and a cell whose netlist
    changes under an unchanged name (the LS branch renamed its feedback cap
    C4 -> C3) keeps serving the OLD lambdified TF for the rest of the session.
    That failure is silent: absent designators live in the BOM row as 0.0, so the
    stale TF evaluates with a zero cap instead of raising."""
    H, names = make_response_func(_case)
    return H, names

# =====================================================================
#  HP passband plateau, read BELOW the finite-Ro HF hump
# =====================================================================
def _hp_hf_plateau(H, cd, corner_hz, gb, tol=0.03, n_pts=600, hump_margin=2.5):
    """Realized HF passband gain of a high-pass cell (HP / HP-MFB / HP-AM).

    A REAL op-amp does not roll off monotonically: its open-loop output
    resistance Ro works against the cell's feedback capacitor and adds a pole
    INSIDE the loop, so |H| PEAKS well above the plateau before it falls
    (measured: +12.9 dB on a 3HPn-MFB2 BOM with LMV358A; the same BOM with Ro=0
    peaks +0.2 dB, and with Ro=1.2k + a 1 GHz part it peaks +37.9 dB -- the hump
    is Ro, not GBWP). Anchoring the read on argmax(|H|) therefore locks onto the
    HUMP, and reading 1.5-8x ABOVE it lands on the post-hump skirt: that is what
    reported 0.09 for a 1.55 plateau.

    Instead: sweep from 3*f0 (pole/zero transient settled, the notch well
    behind), cut the band at f_hump / hump_margin whenever the sweep has an
    INTERIOR maximum, and read the plateau as the flattest part of what remains:
      * the first contiguous run with |d ln|H| / d ln f| <= tol -- a real
        plateau exists; median over the run is immune to grid noise;  else
      * the point of minimum |slope| -- the hump crowds the corner, so the
        saddle is the best plateau estimate that exists.
    With an ideal op-amp there is no interior maximum, the whole sweep is
    available and the flat run ends at the asymptote, so this reproduces
    H(inf) = b_lead/a_lead to 4 decimals.

    Returns None when no section corner is known (caller falls back)."""
    f0 = float(corner_hz or 0.0)
    if f0 <= 0.0:
        return None
    f_lo = 3.0 * f0
    f_hi = min(1e3 * f0, gb / 2.0, 1e8)      # never sweep into the GBWP wall
    if f_hi <= f_lo * 1.2:                   # op-amp too slow to have a passband
        f_hi = f_lo * 1.2
    f = np.logspace(np.log10(f_lo), np.log10(f_hi), n_pts)
    m = np.abs(H(cd, 2.0 * np.pi * f))
    ok = np.isfinite(m) & (m > 0.0)
    f, m = f[ok], m[ok]
    if m.size < 8:
        return float(m[-1]) if m.size else None

    i_pk = int(np.argmax(m))
    hi = m.size
    if 0 < i_pk < m.size - 1:                # interior max == the finite-Ro hump
        hi = max(int(np.searchsorted(f, f[i_pk] / hump_margin)), 1)

    sl = np.gradient(np.log(m), np.log(f))[:hi]
    mm = m[:hi]
    flat = np.abs(sl) <= tol
    if flat.any():
        j = int(np.argmax(flat))
        k = j
        while k + 1 < flat.size and flat[k + 1]:
            k += 1
        return float(np.median(mm[j:k + 1]))
    return float(mm[int(np.argmin(np.abs(sl)))])

def _realized_dc(row, cases, eval_opamp, corner_hz=None):
    """Realized passband gain of a snapped BOM from the cell's exact response on
    the snapped components + op-amp. LP: evaluated at ~DC (1 rad/s, deep in the
    LP passband). HP: the HF plateau, located by flatness below the finite-Ro hump over a band
    below the op-amp GBWP rolloff (the row carries no f0, and the plateau IS the
    max for a monotone-rising high-pass). Returns None if it can't be built."""
    topo = row.get("topology")
    mode = "nonideal" if (topo, "nonideal") in cases else "ideal"
    case = cases.get((topo, mode))
    if case is None:
        return None
    fam = case.get("topo", {}).get("family", "LP")
    try:
        H, names = _response_fn(topo, mode, TF_struct_sig(topo), case)
        cd = {}
        for nm in names:
            if row.get(nm) is not None:
                cd[nm] = row[nm]
            elif nm in eval_opamp:
                cd[nm] = eval_opamp[nm]
        if fam in ("BP", "BP-MFB", "BP-AM"):
            # Band-pass passband gain = the resonant PEAK (no DC/HF plateau).
            # Sweep a wide band below the op-amp GBWP rolloff and take the max:
            # for a 2nd-order band-pass the magnitude has a single interior peak
            # at the center frequency, so max(|H|) IS the realized center gain.
            gb = eval_opamp.get("GBWP_hz", 1e15)
            fhi = min(gb / 3.0, 1e8)
            f = np.logspace(0, np.log10(max(fhi, 100.0)), 4000)
            return float(np.max(np.abs(H(cd, 2 * np.pi * f))))           # peak gain
        if fam in ("HP", "HP-MFB", "HP-AM"):
            # HF passband gain = the plateau, read below BOTH the op-amp GBWP
            # rolloff and the finite-Ro HF hump. See _hp_hf_plateau.
            gb = eval_opamp.get("GBWP_hz", 1e15)
            g = _hp_hf_plateau(H, cd, corner_hz, gb)
            if g is not None:
                return g
            # no section corner in cfg -> legacy plateau-max fallback
            fhi = min(gb / 3.0, 1e8)
            f = np.logspace(0, np.log10(max(fhi, 100.0)), 4000)
            mag = np.abs(H(cd, 2 * np.pi * f))
            f_pk = f[int(np.argmax(mag))]
            f_hf = max(min(f_pk * 8.0, gb / 4.0), f_pk * 1.5)
            return float(abs(H(cd, np.array([2 * np.pi * f_hf]))[0]))
        return float(abs(H(cd, np.array([1.0]))[0]))                     # LP DC
    except Exception:
        return None


def _dc_hf_realized(row, case, eval_opamp, fz_hz):
    """Realized (|H(0)|, |H(∞)|) of one section's picked cell, for the Band-Reject
    two-band overall readout. Unlike `_realized_dc` (which returns the family's
    single passband side), this returns BOTH ends regardless of cell family --
    a BR cascade passes signal through every section in both the LF and HF bands.
      DC : read at 1 rad/s (deep below the notch).
      HF : the flat plateau read above the notch fz (past any Q-bump) but safely
           below the op-amp GBWP rolloff; the median over the band is robust to
           residual slope and the rolloff edge.
    Returns (None, None) if the response can't be built."""
    if case is None:
        return None, None
    try:
        H, names = make_response_func(case)
        cd = {}
        for nm in names:
            if row.get(nm) is not None:
                cd[nm] = row[nm]
            elif nm in eval_opamp:
                cd[nm] = eval_opamp[nm]
        dc = float(abs(H(cd, np.array([1.0]))[0]))                       # |H(0)|
        gb = eval_opamp.get("GBWP_hz", 1e15)
        fz = fz_hz if (fz_hz and fz_hz > 0) else 1000.0
        f_lo = fz * 3.0
        f_hi = min(fz * 60.0, gb / 5.0)
        if f_hi <= f_lo * 1.05:                  # GBWP crowds the notch -> a thin
            f_hi = max(f_lo * 1.5, min(fz * 6.0, gb / 5.0))   # band just above fz
        f = np.logspace(np.log10(f_lo), np.log10(max(f_hi, f_lo * 1.1)), 200)
        hf = float(np.median(np.abs(H(cd, 2 * np.pi * f))))             # |H(∞)|
        return dc, hf
    except Exception:
        return None, None


def _am_row_designators(s):
    """Solver-symbol -> schematic-designator value map for one AM BOM row,
    matching schematic_svg._am_labels: the 3rd-order prefilter resistor (R1, or
    R3 for the LP2 tap) prints as R0 and the prefilter cap C4 as C0. A parallel
    input cap (-C1s) is shown as a comma pair under a single C1 column (e.g.
    `1n,2n`) to keep the table narrow -- the VCVS split-C2 style -- even though
    the schematic labels the two parts C1.1/C1.2. Returns {designator: string}
    for the components this row populates."""
    t = str(s.get("topology") or "")
    o3 = t[:1] == "3"
    is_hp = "HP" in t
    is_lp2 = "LP-AM2" in t
    pre_R = "R3" if (is_lp2 and not is_hp) else "R1"
    out = {}
    for i in range(1, 9):
        nm = f"R{i}"; v = s.get(nm)
        if v is None:
            continue
        if o3 and nm == pre_R:
            out["R0"] = _fmt_res(v, True)
        elif nm == "R3":
            continue                       # R3 is only ever the LP2 prefilter (-> R0)
        else:
            out[nm] = _fmt_res(v, True)
    if s.get("C1_parallel") and s.get("C1a") and s.get("C1b"):
        out["C1"] = f"{_fmt_cap(s['C1a'])},{_fmt_cap(s['C1b'])}"
    elif s.get("C1") is not None:
        out["C1"] = _fmt_cap(s["C1"])
    if s.get("C2") is not None:
        out["C2"] = _fmt_cap(s["C2"])
    if s.get("C3") is not None:
        out["C3"] = _fmt_cap(s["C3"])
    if o3 and s.get("C4") is not None:
        out["C0"] = _fmt_cap(s["C4"])          # 3rd-order prefilter cap
    return out


def _bp3_alias(topology):
    """Solver-symbol -> schematic-designator aliases for the 3rd-order MFB
    band-pass cells (2BP1HP-MFB / 2BP1LP-MFB and their -QE twins):
    C3 -> C0 (input cap), R6 -> R0 (input series resistor, BP1LP only).

    Delegates to schematic_svg.bp3_alias so the BOM table, the per-part list and
    the drawn schematic can never drift apart. Returns {} for every other cell,
    which leaves the generic display path byte-identical.
    """
    return schematic.bp3_alias(topology)


def _alias_for_rows(rows):
    """Union of the designator aliases over a BOM pool (all rows in a pool come
    from the same solve, so their aliases agree or are empty)."""
    out = {}
    for s in rows or ():
        out.update(_bp3_alias(s.get("topology")))
    return out


def _display_pairs(alias):
    """[(display_designator, solver_symbol)] over the full C1-C4 / R1-R8 grid,
    caps then resistors, each block sorted by DISPLAY name so an aliased C0/R0
    leads its block. With an empty alias this reproduces COMP_ORDER exactly."""
    pairs = [(alias.get(k, k), k) for k in
             ("C1", "C2", "C3", "C4",
              "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8")]
    return (sorted([q for q in pairs if q[0][0] == "C"])
            + sorted([q for q in pairs if q[0][0] == "R"]))


def _bom_dataframe(rows):
    """Component columns adapt to the pool: a cap/resistor column appears only
    when at least one row in `rows` populates it, so the table carries just the
    components the topologies in this cycle actually use (no empty C/R columns).
    Split input/notch caps print as a comma pair under one column (e.g. "1n,2n").

    Component cells are rendered with their SI-prefix strings (e.g. "82.5k",
    "8.25n") for compact, readable display. The dataframe is pre-sorted by the
    'Sort by' selector above the table (which sorts by RAW NUMERIC values, not
    these strings), so click-to-sort on component columns is intentionally not
    used -- it would sort lexicographically.  Metric columns (Sens / snap /
    gain) stay numeric so their column-header click-sort still orders by value.
    """
    def cap2(s):
        if s.get("C2_parallel") and s.get("C2a") and s.get("C2b"):
            return f"{_fmt_cap(s['C2a'])},{_fmt_cap(s['C2b'])}"
        return _fmt_cap(s.get("C2"))
    def cap1(s):
        if s.get("C1_parallel") and s.get("C1a") and s.get("C1b"):
            return f"{_fmt_cap(s['C1a'])},{_fmt_cap(s['C1b'])}"
        return _fmt_cap(s.get("C1"))

    glabel = _gain_label_for(rows[0].get("topology")) if rows else "DC gain"
    # AM cells: use schematic designators (R0/C0 prefilter; a split input cap
    # folds into one comma C1) instead of the raw solver symbols. Columns are
    # already pool-adaptive here via `present`.
    if rows and "-AM" in str(rows[0].get("topology", "")):
        CAP_ORDER = ["C0", "C1", "C2", "C3"]
        RES_ORDER = ["R0", "R1", "R2", "R4", "R5", "R6", "R7", "R8"]
        maps = [_am_row_designators(s) for s in rows]
        present = set().union(*maps) if maps else set()
        cols = [c for c in CAP_ORDER if c in present] + \
               [c for c in RES_ORDER if c in present]
        data = []
        for i, (s, m) in enumerate(zip(rows, maps)):
            snap = s.get("snap_cost")
            row = {"#": i, "Topology": s.get("topology", "?"),
                   "Sens": f"{s.get('sens_score', 0.0):.2f}",
                   "Snap cost": f"{snap:.2f}" if snap is not None else "—",
                   glabel: (f"{s['_dc']:.3f}" if s.get("_dc") is not None else "—")}
            for c in cols:
                row[c] = m.get(c, "--")
            data.append(row)
        return pd.DataFrame(data)

    def _val(s, k):
        if k == "C1":
            return cap1(s)
        if k == "C2":
            return cap2(s)
        if k[0] == "C":
            return _fmt_cap(s.get(k))
        return _fmt_res(s.get(k), True)

    # Dynamic component columns: keep a cap/resistor column only when at least
    # one row in this pool actually populates it (value != "--"). A fixed
    # C1-C4 / R1-R8 grid leaves empty columns for whatever the cells in this
    # cycle don't use -- e.g. a pool of 2HPn-MFB / -MFB2 twins should show only
    # the components those two cells touch. This also subsumes the former
    # special-cases: first-order rows (only C1 / R1-R4) and the +R8 HP twins.
    # (designator, solver symbol) pairs -- an identity map for every cell except
    # the 3rd-order MFB band-pass ones, where C3/R6 print as C0/R0.
    present = [(d, k) for d, k in _display_pairs(_alias_for_rows(rows))
               if any(_val(s, k) != "--" for s in rows)]
    data = []
    for i, s in enumerate(rows):
        snap = s.get("snap_cost")
        row = {
            "#": i,
            "Topology": s.get("topology", "?"),
            "Sens": f"{s.get('sens_score', 0.0):.2f}",
            "Snap cost": f"{snap:.2f}" if snap is not None else "—",
            glabel: (f"{s['_dc']:.3f}" if s.get("_dc") is not None else "—"),
        }
        for d, k in present:
            row[d] = _val(s, k)
        data.append(row)
    return pd.DataFrame(data)


def _render_choice(s, cont, idx):
    _dc = s.get("_dc")
    _dc_str = f"{_dc:.3f} V/V" if _dc is not None else "n/a"
    _glabel = _gain_label_for(s.get("topology"))
    st.success(f"Selected #{idx}: `{s.get('topology','?')}`  ·  "
               f"snap_cost {s.get('snap_cost', float('nan')):.2f}  ·  "
               f"sens {s.get('sens_score', 0.0):.2f}  ·  {_glabel} {_dc_str}")
    # AM cells: label components by schematic designator (R0 / C0 prefilter).
    # A split input cap is shown exactly like the VCVS 2LPn section -- one
    # combined `C1` line plus a gray "a ∥ b" caption -- not as separate parts.
    if "-AM" in str(s.get("topology", "")):
        t = str(s.get("topology") or ""); o3 = t[:1] == "3"
        is_hp = "HP" in t; is_lp2 = "LP-AM2" in t
        pre_R = "R3" if (is_lp2 and not is_hp) else "R1"
        res_pairs = []                       # (designator, solver_symbol)
        for i in range(1, 9):
            nm = f"R{i}"
            if s.get(nm) is None:
                continue
            if o3 and nm == pre_R:
                res_pairs.append(("R0", nm))
            elif nm == "R3":
                continue
            else:
                res_pairs.append((nm, nm))
        _ro = {"R0":0,"R1":1,"R2":2,"R4":4,"R5":5,"R6":6,"R7":7,"R8":8}
        res_pairs.sort(key=lambda pr: _ro.get(pr[0], 9))
        cap_items = []
        if o3 and s.get("C4") is not None:
            cap_items.append(("C0", _fmt_cap(s["C4"])))
        if s.get("C1") is not None:                       # combined value; a split
            cap_items.append(("C1", _fmt_cap(s["C1"])))   # input cap is spelled out
        for _c in ("C2", "C3"):                           # in the ∥ caption below
            if s.get(_c) is not None:
                cap_items.append((_c, _fmt_cap(s[_c])))
        cc = st.columns(2)
        with cc[0]:
            st.markdown("**Capacitors** (not snapped)")
            for d, v in cap_items:
                st.markdown(f"`{d}` = {v}")
            if s.get("C1_parallel") and s.get("C1a") and s.get("C1b"):
                st.caption(f"C1 = {_fmt_cap(s['C1a'])} ∥ {_fmt_cap(s['C1b'])}  (parallel input cap)")
        with cc[1]:
            st.markdown("**Resistors** — snapped :gray[(ideal)]")
            for d, sym in res_pairs:
                snap_v = _fmt_res(s.get(sym), True); ideal = cont.get(sym)
                if ideal is not None:
                    st.markdown(f"`{d}` = {snap_v}  :gray[({_fmt_res(ideal, False)})]")
                else:
                    st.markdown(f"`{d}` = {snap_v}")
        return
    _pairs = _display_pairs(_bp3_alias(s.get("topology")))
    caps = [(d, k) for d, k in _pairs if d[0] == "C" and s.get(k)]
    res = [(d, k) for d, k in _pairs if d[0] == "R" and s.get(k)]
    cc = st.columns(2)
    with cc[0]:
        st.markdown("**Capacitors** (not snapped)")
        for d, k in caps:
            st.markdown(f"`{d}` = {_fmt_cap(s.get(k))}")
        if s.get("C1_parallel") and s.get("C1a") and s.get("C1b"):
            st.caption(f"C1 = {_fmt_cap(s['C1a'])} ∥ {_fmt_cap(s['C1b'])}  (parallel input cap)")
        if s.get("C2_parallel") and s.get("C2a") and s.get("C2b"):
            st.caption(f"C2 = {_fmt_cap(s['C2a'])} ∥ {_fmt_cap(s['C2b'])}")
    with cc[1]:
        st.markdown("**Resistors** — snapped :gray[(ideal)]")
        for d, k in res:
            snap_v = _fmt_res(s.get(k), True)
            ideal = cont.get(k)
            if ideal is not None:
                st.markdown(f"`{d}` = {snap_v}  :gray[({_fmt_res(ideal, False)})]")
            else:
                st.markdown(f"`{d}` = {snap_v}")


# =====================================================================
#  GLOBAL CONTROLS
# =====================================================================


# Regularization weight for the non-ideal pre-distortion solve. Fixed at the
# former UI default — a solver-internal knob with no user-facing meaning, and
# ignored entirely in ideal mode. Still visible (read-only) in each section's
# "exact solver call" expander, since it rides in cfg.
REG_WEIGHT = 0.02


def _convergence_inputs():
    """Global search settings.

    The two tolerances are ENTERED AS PERCENT and RETURNED AS FRACTIONS: the
    user reads 1.00, the solver gets 0.01. The widget keys carry a `_pct`
    suffix so a value left over under the old fraction-based keys can never be
    re-read as a percent — 0.01 would silently become a 100x tighter gate.
    """
    with st.expander("Convergence Settings", expanded=False):
        level = st.select_slider(
            "Search thoroughness", options=list(SEARCH_PRESETS.keys()),
            value="Balanced", key="hw_effort",
            help="Breadth of the brute-force search (multistarts, minima kept, hints). "
                 "Higher finds more candidate BOMs but is slower.")
        c = st.columns(2)
        with c[0]:
            pole_tol_pct = st.number_input(
                "Pole & notch frequency tolerance (%)", value=1.0,
                min_value=0.0, max_value=100.0, step=0.1, format="%.2f",
                key="hw_pole_tol_pct",
                help="How far a candidate's realized pole frequency f₀ — and, on a notch "
                     "section, its notch frequency f_z — may sit from the target. "
                     "1.00 means ±1 %. Applies to the parallel-C2 (notch) search path. "
                     "Q is not constrained by this.")
        with c[1]:
            gain_tol_pct = st.number_input(
                "Passband gain tolerance (%)", value=0.5,
                min_value=0.0, max_value=100.0, step=0.1, format="%.2f",
                key="hw_gain_tol_pct",
                help="How far a candidate's realized passband gain may sit from the "
                     "target. 0.50 means ±0.5 %. DC gain for low-pass and notch cells, "
                     "HF gain for high-pass.")
        top_k = st.number_input(
            "Max candidates to refine", min_value=1, max_value=500, value=30, step=5,
            key="hw_topk",
            help="Only the best N ideal solutions (by sensitivity) are op-amp pre-distorted "
                 "and snapped — the dominant cost for gained/notch cells. Lower = much faster, "
                 "fewer BOMs listed.")
    return dict(SEARCH_PRESETS[level],
                pole_tol=pole_tol_pct / 100.0,
                gain_tol=gain_tol_pct / 100.0,
                reg_weight=REG_WEIGHT, top_k=top_k)


# =====================================================================
#  PER-SECTION SETTINGS  (independent envelope / series / family)
# =====================================================================
def _opamp_picker(n):
    """Per-section op-amp selector (library entry or CUSTOM). Returns the op-amp
    dict {A_ol, GBWP_hz, Ro(MOhm)} or None (ideal). Shared by both settings UIs."""
    st.markdown("**Op-amp model**")
    choice = st.selectbox("Op-amp", list(OPAMP_LIBRARY.keys()),
                          key=f"hw_opamp_choice_{n}", label_visibility="collapsed")
    spec = OPAMP_LIBRARY[choice]
    if spec == "CUSTOM":
        o = st.columns(3)
        with o[0]:
            a_ol = st.number_input("A_ol (V/V)", value=1e5, format="%.2e", key=f"hw_aol_{n}")
        with o[1]:
            gbwp = st.number_input("GBWP (Hz)", value=1e6, format="%.3e", key=f"hw_gbwp_{n}")
        with o[2]:
            ro_ohm = st.number_input("Ro (Ω)", value=1200.0, step=10.0, key=f"hw_ro_{n}")
        return dict(A_ol=a_ol, GBWP_hz=gbwp, Ro=ro_ohm / 1e6)   # ohm -> MOhm
    return spec   # dict, or None for ideal


def _section_settings(sec):
    n = sec["stage_num"]
    with st.expander(f"⚙ Section {n} — topology & component settings", expanded=False):
        family = st.radio("Topology family", TOPOLOGY_FAMILIES, index=0,
                          horizontal=True, key=f"hw_fam_{n}")
        if family.startswith("MFB"):
            st.info("MFB (Friend) covers every section kind — low-pass (LP / LP-notch), "
                    "high-pass (HP / HP-notch), band-pass and pure-notch. All-pole / "
                    "band-pass cells are inverting, each with an optional Q-enhanced "
                    "(positive-feedback) twin; the notch cells are non-inverting. Gain "
                    "is a free component ratio. The HP-notch has two realizations (base "
                    "HF gain < 1 attenuating, MFB2 ≥ 1 unity/gained) and the solver picks "
                    "whichever reaches the target. The pure-notch has two cells too: "
                    "2N-MFB reaches unity and gained passbands (C3/R5 at the op-amp − "
                    "node set HF/DC gain), while 2N-MFB-atten is the fewer-part "
                    "attenuating-only cell — for a sub-unity target both are tried and "
                    "ranked, and for unity/gained only 2N-MFB is used.")
        elif family.startswith("AM"):
            st.info("AM (Ackerberg–Mossberg) is the classic THREE-op-amp "
                    "state-variable biquad: one Miller integrator closed through "
                    "an actively-compensated integrator and a unity inverter. It "
                    "covers every 2nd/3rd-order section kind (LP / LP-notch, "
                    "HP / HP-notch, band-pass, pure-notch). Every AM cell is "
                    "inverting and the passband gain is a free component ratio. "
                    "The matched inverter pair R7 = R8 gives active GB "
                    "compensation (Q error ∝ (f0/ft)² rather than Q·f0/ft) — the "
                    "reason to spend three op-amps; see AM_NONIDEAL_ANALYSIS.md. "
                    "Low-pass and band-pass each offer two taps (out1 and the "
                    "classic out2 realization), solved together and ranked.")
        elif not family.startswith("VCVS"):
            st.info("Only VCVS (Sallen-Key), MFB (Friend) and AM (Ackerberg–"
                    "Mossberg) synthesis are wired today. Other families are coming.")

        # Op-amp is per-section: high-Q / high-gain stages can be more BW-demanding.
        opamp = _opamp_picker(n)

        st.markdown("**Component envelope**")
        e = st.columns(3)
        with e[0]:
            c_min = st.number_input("C_min (µF)", value=6.8e-5, format="%.2e", key=f"hw_cmin_{n}")
            c_max = st.number_input("C_max (µF)", value=1e-2, format="%.2e", key=f"hw_cmax_{n}")
        with e[1]:
            r_min_k = st.number_input("R_min (kΩ)", value=0.3, format="%.4f", key=f"hw_rmin_{n}")
            r_max_k = st.number_input("R_max (kΩ)", value=2000.0, format="%.1f", key=f"hw_rmax_{n}")
        with e[2]:
            max_ratio = st.number_input("Max R ratio", value=500.0, step=1.0, key=f"hw_ratio_{n}",
                                        help="Phase 3 rejects any solution whose resistor spread "
                                             "exceeds this — lower = far fewer solutions. The old "
                                             "default of 60 was below what most sections need and "
                                             "had to be raised by hand almost every run: an MFB "
                                             "band-pass alone spends ~4·Q² of spread on its core, "
                                             "so 60 caps it at Q ≈ 3.9 and returns nothing above "
                                             "that. 500 clears Q ≈ 11 and leaves the guard doing "
                                             "its real job — rejecting genuinely unbuildable "
                                             "spreads — rather than gating ordinary designs.")

        st.markdown("**Capacitor E-series** (single)")
        c_series = st.radio("C series", C_SERIES_OPTIONS,
                            index=C_SERIES_OPTIONS.index("E12"), horizontal=True,
                            key=f"hw_cser_{n}", label_visibility="collapsed")
        if c_series == "E3":
            st.caption("⚠ E3 must be added to `unified_solver_v2._E_SERIES`, "
                       "else the cap grid is empty.")

        st.markdown("**Resistor E-series** (multiple)")
        rcols = st.columns(len(R_SERIES_OPTIONS))
        r_selected = []
        for i, name in enumerate(R_SERIES_OPTIONS):
            with rcols[i]:
                if st.checkbox(name, value=(name == "E48"), key=f"hw_rser_{n}_{name}"):
                    r_selected.append(name)
        if not r_selected:
            st.warning("No R series selected — defaulting to E48.")
            r_selected = ["E48"]

        # AM-only handbook constraint toggle (Issue 4). Default ON.
        equalize_rc = None
        if family.startswith("AM"):
            equalize_rc = st.checkbox(
                "Equalize R, C values", value=True, key=f"hw_ameq_{n}",
                help="Handbook balanced design: force R5 = R6 = R7 = R8 and "
                     "C2 = C3 (equal integrators, one matched R array, one C "
                     "value). Uncheck to free R5/R6 and C2/C3 for a tighter "
                     "E-series fit — the matched pair R7 = R8 still holds, so "
                     "GBWP independence and low sensitivity are preserved.")

        # MFB LP-NOTCH-only realization toggles. Both default OFF, so an
        # untouched run dispatches EXACTLY the cells it did before: the LS cells
        # are never named in `topologies`, hence never derived and never solved.
        mfb_ls, elim_r1, mfb_gained = False, False, False
        if family.startswith("MFB") and _is_hp_notch2(sec):
            mfb_gained = st.checkbox(
                "Gained MFB", value=False, key=f"hw_mfbgain_{n}",
                help="Solve the 2nd-order HP-notch on the UNITY/GAINED MFB2 "
                     "realization instead of the attenuating `2HPn-MFB`. The cap "
                     "ratio (C3+C4)/C3 lifts the (+)-input divider, so H(∞) is "
                     "raised above unity. The reachable HF gain is a NARROW band "
                     "just below (f0/fz)², so this switches Custom HF gain ON and "
                     "pre-fills the geometric mean of that band. The `2HPn-MFB2` "
                     "cell AND its `+R7` (p→out positive-feedback) twin are solved "
                     "in one cycle and ranked by sens_score — R7 → ∞ degenerates "
                     "the twin back to the plain cell and additionally lowers the "
                     "band floor, so it can only enlarge the pool. Any surplus "
                     "gain must be redistributed to the other stages.")
        if family.startswith("MFB") and _is_lp_notch(sec):
            lscols = st.columns(2)
            with lscols[0]:
                mfb_ls = st.checkbox(
                    "Lower Sensitivity MFB", value=False, key=f"hw_mfbls_{n}",
                    help="Solve the LP-notch on the low-sensitivity branch instead "
                         "of the Friend SAB `{o}LPn-MFB`. The (+) node hangs on a "
                         "resistive input divider, so Q is realized passively: "
                         "S_Q < 1 and a markedly deeper finite-GBW null. Each order "
                         "solves its cell AND its +R7 (p→out) positive-feedback twin "
                         "in one cycle, ranked together by sens_score — R7 → ∞ "
                         "degenerates back to the plain cell, so the twin can only "
                         "improve the pool. R7 is what relieves the "
                         "C2/C3 ≥ 4·(fz/f0)²·Q² capacitor-ratio law, at the cost of "
                         "S_Q.".format(o=int(sec.get("order", 2))))
            if int(sec.get("order", 2)) == 2:
                with lscols[1]:
                    elim_r1 = st.checkbox(
                        "Eliminate R1", value=False, key=f"hw_mfbls_nor1_{n}",
                        disabled=not mfb_ls,
                        help="Drop the in→a series resistor R1 and use the 7-part "
                             "`2LPn-MFB-LS` / 8-part `2LPn-MFB-LS+R7` pair. R1 is the "
                             "third leg of the (+) divider; without it the DC gain is "
                             "pinned into a narrow window just under (fz/f0)², so "
                             "Custom DC gain is switched on and pre-filled with the "
                             "geometric mean of that window. Any surplus gain or "
                             "attenuation must then be redistributed to the other "
                             "stages. Leave unchecked to let the solver pick the R1 "
                             "pair whenever the target gain falls below the window.")
            elif not mfb_ls:
                elim_r1 = False

    env = dict(C_min=c_min, C_max=c_max,
               R_min=r_min_k * 1e-3, R_max=r_max_k * 1e-3,   # kΩ -> MΩ (internal units)
               MAX_R_RATIO=max_ratio, C_series=c_series, R_series=", ".join(r_selected))
    if equalize_rc is not None:
        env["equalize_rc"] = bool(equalize_rc)   # threads into cfg via _build_cfg
    # mfb_ls / elim_r1 / mfb_gained are deliberately NOT threaded into `env`: they
    # select WHICH cells are dispatched, not the solver environment. cfg (and
    # therefore run_synthesis / solvability_probe / _job_sig's env hash) stays
    # byte-identical for every other family; the chosen cell name already enters
    # _job_sig.
    return family, env, opamp, mfb_ls, elim_r1, mfb_gained


def _is_lp_notch(sec):
    """True for the 2nd/3rd-order LOW-PASS notch sections -- the only ones with
    a low-sensitivity (LS) realization. HP-notch and pure-notch are excluded."""
    return (bool(sec.get("notch"))
            and int(sec.get("order", 2)) in (2, 3)
            and section_kind(sec)[0] == "lp")


def _is_hp_notch2(sec):
    """True for the 2nd-order HIGH-PASS notch -- the only section with the
    'Gained MFB' realization (the 2HPn-MFB2 / 2HPn-MFB2+R7 pair). 3rd-order HP
    notch already tiles unity..gained via HF_GAIN_MARGIN routing and needs no
    checkbox; LP-notch and pure-notch are excluded."""
    return (bool(sec.get("notch"))
            and int(sec.get("order", 2)) == 2
            and section_kind(sec)[0] == "hp")


def _ls_span(sec):
    """Reachable DC-gain band of the BARE 2nd-order LS pair, in closed form
    (both cells are fully determined -- see cells_mfb.py header).

        r        = (fz/f0)^2 = wz^2/w0^2 > 1
        ceiling  : g < r                            (H(inf) = R6/(R2+R6) < 1)
        floor    : 2LPn-MFB-LS      g > r^2 Q^2/(1 + r Q^2)
                   2LPn-MFB-LS+R7   g > r - sqrt(r)/(2 Q)      (always lower)

    The two cells are solved together, so the PAIR spans (floor_R7, r). Returns
    (r, g_lo, g_hi, floor_bare, floor_r7) with g_lo = floor_R7, g_hi = r.

    The "+R1" pair has no floor at all (R1 is a third leg on the (+) divider),
    so it spans (0, r) and needs no window.
    """
    f0 = float(sec["f0_hz"]) or 1.0
    Qv = float(sec["Q"])
    r = (float(sec["fz_hz"]) / f0) ** 2
    fb = r * r * Qv * Qv / (1.0 + r * Qv * Qv)          # bare cell
    f7 = r - math.sqrt(r) / (2.0 * Qv)                  # +R7 cell (lower)
    lo = min(fb, f7)
    return r, lo, r, fb, f7


def _ls_geomean_gain(sec):
    """Value the "Eliminate R1" checkbox drops into the Custom DC gain box.

    Deliberately the geometric mean of the INTERSECTION (floor_bare, r), not of
    the pooled union (floor_R7, r): a gain inside the intersection is reachable
    by BOTH cells, so the pair actually produces two candidate BOMs and the
    sens_score ranking has something to rank. A gain in (floor_R7, floor_bare)
    is legal -- the pooled window -- but only `+R7` would return a BOM there."""
    r, _lo, hi, fb, _f7 = _ls_span(sec)
    return math.sqrt(max(fb, 1e-6) * hi)


# =====================================================================
#  RESULTS  (default sens_score; metric-only sort; pick one)
# =====================================================================
def _run_probe_timeboxed(cfg, topos, dc_gain, budget_s=35.0):
    """Run the global solvability probe off the UI thread with a hard cap.
    Returns the verdict dict, or None on timeout/error (-> generic message).
    The cap allows the gain-band ladder (which derives + sweeps each shape-
    feasible cell); the probe is cached per failing config and runs off-thread,
    so a longer cap never blocks the UI."""
    import concurrent.futures as _cf
    ex = _cf.ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(solvprobe.assess, list(topos), cfg, dc_gain)
        return fut.result(timeout=budget_s)
    except Exception:
        return None
    finally:
        ex.shutdown(wait=False)               # never block the rerun on a slow probe


def _render_no_realization(cfg, topos, dc_gain):
    """Failure path. Stage-1 found nothing inside the user's envelope. Run the
    cheaper idealised global probe (ONCE per failing config, cached) to upgrade
    the generic widen-limits hint into a quantitative yellow / red diagnosis."""
    generic = ("No realization inside the component envelope. Widen R/C limits, "
               "relax Max R ratio, add more R series, or raise thoroughness.")
    if not cfg or not topos:                  # e.g. first-order path -> keep generic
        st.warning(generic)
        return

    key = (tuple(topos),
           round(float(cfg.get("f0", 0)), 6), round(float(cfg.get("Q", 0)), 6),
           round(float(cfg.get("fz", 0)), 6), round(float(cfg.get("K", 0)), 9),
           None if dc_gain is None else round(float(dc_gain), 9),
           float(cfg.get("MAX_R_RATIO", 0) or 0),
           float(cfg.get("C_min", 0) or 0), float(cfg.get("C_max", 0) or 0))
    cache = st.session_state.setdefault("_solvprobe_cache", {})
    if key not in cache:
        cache[key] = _run_probe_timeboxed(cfg, topos, dc_gain)
    verdict = cache[key]

    if not verdict:                           # probe timed out / errored
        st.warning(generic)
        return

    kind = verdict["verdict"]
    search_hint = ("Raise thoroughness (more ratio/anchored starts, valleys), "
                   "re-solve, or nudge the section gain slightly.")
    if kind == "FEASIBLE_GLOBAL":
        need_r, need_c = verdict["need_r_ratio"], verdict["need_c_ratio"]
        lim_r = float(cfg.get("MAX_R_RATIO", 0) or 0)
        cmin = float(cfg.get("C_min", 0) or 0)
        cmax = float(cfg.get("C_max", 0) or 0)
        lim_c = (cmax / cmin) if (cmin > 0 and cmax > 0) else 0.0
        bits = []
        if lim_r and need_r > lim_r * 1.02:
            bits.append(f"a resistor ratio ≈{need_r:.0f}× (your Max R ratio is {lim_r:.0f}×)")
        if lim_c and need_c > lim_c * 1.02:
            bits.append(f"a capacitor ratio ≈{need_c:.0f}× (your C_max/C_min is {lim_c:.0f}×)")
        if bits:
            # at least one spread genuinely exceeds the envelope -> widen it.
            st.warning("A realizable design exists, but not inside your envelope — it needs "
                       + "; ".join(bits) + ". Widen those limits (or add more R series) "
                       "and re-solve.")
        else:
            # the needed spreads ALREADY fit the envelope, yet stage-1 found
            # nothing -> the obstruction is the SEARCH, not the limits (telling
            # the user to widen limits here is what made widening 'do nothing').
            st.warning(f"A realizable design exists inside your envelope (it needs only "
                       f"a resistor ratio ≈{need_r:.0f}× and a capacitor ratio ≈{need_c:.0f}×, "
                       f"both within your limits), but the search didn't reach it. "
                       + search_hint)
    elif kind == "REALIZABLE_NEEDS_SEARCH":
        b = verdict.get("band")
        tg = verdict.get("target_gain")
        band_txt = (f" (this shape is realizable across a gain band of about "
                    f"{b[0]:.3g}–{b[1]:.3g}, which includes your {tg:.3g})") if b else ""
        st.warning("A realizable design almost certainly exists at this gain" + band_txt
                   + ", but the search didn't find one in your envelope. " + search_hint)
    elif kind == "INFEASIBLE_GAIN":
        tg, rg = verdict["target_gain"], verdict["reach_gain"]
        direction = verdict.get("direction") or ("lower" if rg < tg else "higher")
        b = verdict.get("band")
        band_txt = (f" Its reachable gain band for this shape is about "
                    f"{b[0]:.3g}–{b[1]:.3g}.") if b else ""
        st.error(f"No realizable solution at this gain. You asked for {tg:.3g}, but the "
                 f"nearest gain this topology can realize for the target shape is ≈{rg:.3g}."
                 + band_txt + f" Set the section gain {direction} (≈{rg:.3g}) or choose "
                 "another topology.")
    elif kind == "INFEASIBLE_STRUCTURAL":
        st.error("No realizable solution exists for these targets — the pole/zero shape "
                 "itself isn't achievable with this topology. Choose another topology.")
    else:                                     # UNKNOWN -> no confident opinion
        st.warning(generic)


def _render_results(res, n, opamp, cfg=None, topos=None, dc_gain=None):
    if "__error__" in res:
        st.error(f"Solver error: {res.get("__traceback__")}")
        return
    snapped = res.get("snapped") or []
    if not snapped:
        _render_no_realization(cfg, topos, dc_gain)
        return

    continuous = res.get("continuous") or []
    cases = res.get("cases") or {}
    eval_opamp = opamp if opamp is not None else IDEAL_OPAMP

        # Realized DC gain from the snapped components + op-amp (computed once,
    # memoized on the row). snapped[i] <-> continuous[i] are index-aligned.
    for s in snapped:
        if "_dc" not in s:
            corner = cfg.get("f0") if cfg is not None else None
            s["_dc"] = _realized_dc(s, cases, eval_opamp, corner_hz=corner)
    paired = [(snapped[i], continuous[i] if i < len(continuous) else {})
              for i in range(len(snapped))]

    cc = st.columns([2, 1])
    _glabel = _gain_label_for(snapped[0].get("topology")) if snapped else "DC gain"

    # Build the sort menu. Metric columns are listed first; component columns
    # (caps, resistors) are appended only when AT LEAST ONE row populates them
    # -- so 1st-order rows (which drop most components) don't get unsortable
    # entries, and +R8 only appears when an +R8 twin is actually present.
    _sort_metric = {"Sens score": "sens_score", "Snap cost": "snap_cost",
                    _glabel: "_dc"}
    _sort_comp   = {}
    for _d, _k in _display_pairs(_alias_for_rows(snapped)):
        if any(s.get(_k) for s in snapped):
            _sort_comp[_d] = _k          # menu shows C0/R0, sorts the real symbol
    _sort_map = {**_sort_metric, **_sort_comp}
    _sort_options = list(_sort_map.keys())

    with cc[0]:
        sort_field = st.selectbox(
            "Sort by", _sort_options, index=0, key=f"hw_sortf_{n}",
            help="Sorts the table by the RAW NUMERIC value of the chosen column. "
                 "Component columns carry k/M/n suffixes for display only; this "
                 "selector orders by the underlying ohms / farads, so 8.25k comes "
                 "before 78.7k (in-column click-sort would sort lexicographically "
                 "and is therefore not used for components).")
    with cc[1]:
        desc = st.toggle("Descending", value=False, key=f"hw_sortd_{n}")
    rk = _sort_map[sort_field]
    if rk == "sens_score":
        # Primary rank is sensitivity, but the table DISPLAYS it rounded to 2
        # decimals, so solutions that tie at that precision (e.g. two cells both
        # showing 1.40) must not be ordered by an invisible 3rd-decimal wobble --
        # that buries a better BOM mid-table. Within each rounded-sens group,
        # order by ascending snap_cost (lowest snap first). Two-stage stable sort:
        # pre-sort by snap_cost, then by the rounded sens (Python sort is stable,
        # incl. reverse=True, so equal-sens groups keep the snap ordering).
        paired = sorted(paired, key=lambda p: (p[0].get("snap_cost") is None,
                                               p[0].get("snap_cost") or 0.0))
        paired = sorted(paired, key=lambda p: (p[0].get("sens_score") is None,
                                               round(p[0].get("sens_score") or 0.0, 2)),
                        reverse=desc)
    else:
        paired = sorted(paired, key=lambda p: (p[0].get(rk) is None, p[0].get(rk) or 0.0),
                        reverse=desc)
    rows = [p[0] for p in paired]

    df = _bom_dataframe(rows)
    try:
        event = st.dataframe(df, hide_index=True, use_container_width=True,
                             on_select="rerun", selection_mode="single-row",
                             key=f"hw_df_{n}")
        picked = list(event.selection.rows)
    except Exception:
        # Fallback for older Streamlit: pick by the index shown in column '#'.
        st.dataframe(df, hide_index=True, use_container_width=True)
        idx = st.number_input("Select solution #", min_value=0, max_value=len(rows) - 1,
                              value=0, step=1, key=f"hw_pick_{n}")
        picked = [int(idx)]

    # Resolve the active pick. The BOM table is inside a run_every fragment and
    # is re-sorted each tick, so st.dataframe's selection reads empty on auto-
    # reruns / tab switches. Treat the pick as STICKY: a fresh selection updates
    # it; an empty read keeps the last pick (cleared only when the cascade
    # changes, in render_topology_tab). This keeps hw_picked durable for the
    # Response tab regardless of fragment timing.
    s_row = c_row = None
    pick_idx = -1
    # Durable pick stores that survive other tabs / init resetting session state:
    # 'bom_picks' avoids the hw_ prefix; _PICKS survives even a full state clear.
    store = st.session_state.setdefault("bom_picks", {})
    cstore = st.session_state.setdefault("bom_picks_cont", {})   # pre-snap twin
    if picked:
        s_row, c_row = paired[picked[0]]
        pick_idx = picked[0]
        store[n] = _PICKS[n] = s_row              # durable copies
        cstore[n] = c_row                         # ideal (continuous) twin

        # Flag a genuinely NEW pick. The comparison matters: run_every fires
        # every 2 s and the sticky-pick logic re-writes the same row each tick,
        # so an unguarded flag would rerun the app forever.
        _sig = (pick_idx, str(s_row.get("topology")),
                round(float(s_row.get("sens_score") or 0.0), 6),
                round(float(s_row.get("snap_cost") or 0.0), 6))
        if st.session_state.get(f"_pick_sig_{n}") != _sig:
            st.session_state[f"_pick_sig_{n}"] = _sig
            st.session_state["_picks_dirty"] = True
        
        st.session_state.hw_picked[n] = s_row     # persist for the overall-gain readout
        _topo = s_row.get("topology")
        st.session_state.hw_picked_meta[n] = {
            "case": cases.get((_topo, "nonideal")) or cases.get((_topo, "ideal")),
            "eval_opamp": eval_opamp,
            "mode": res.get("mode"),
        }
    elif n in store or n in _PICKS:
        s_row = store.get(n) or _PICKS.get(n)     # restore from durable store
        store[n] = _PICKS[n] = s_row
        st.session_state.hw_picked[n] = s_row     # re-populate if another tab cleared it
        try:
            j = snapped.index(s_row)
            c_row = continuous[j] if j < len(continuous) else {}
        except ValueError:
            c_row = {}
        # Never clobber a good twin with an empty one: `snapped` is
        # rebuilt on a rerun, so .index() above can miss and hand back {}.
        if c_row:
            cstore[n] = c_row
        else:
            c_row = cstore.get(n) or {}

    if s_row is not None:
        _topo = s_row.get("topology")
        _render_choice(s_row, c_row, pick_idx)

        # per-section schematic: overlay designators/values on the topology SVG
        _opn = opamp_label(n)
        try:
            _svg = schematic.render_svg(_topo, n, s_row, opamp_pn=_opn)
            components.html(schematic.schematic_iframe_html(_svg, max_width=760),
                            height=560, scrolling=False)
            schematic.download_buttons(st, _svg, _topo, n, key_prefix=f"topo_sch_{n}")
        except FileNotFoundError:
            st.caption(f"⚠ Schematic SVG not found for `{_topo}` — expected "
                       f"`{schematic.svg_filename(_topo, s_row)}` in `{schematic.SVG_DIR}`.")
        except Exception as _e:
            st.caption(f"⚠ Schematic render error: {_e}")
    else:
        st.caption("Pick a row to select that BOM.")

    if res.get("mode") == "nonideal":
        st.caption("BOM checked against exact non-ideal op-amp physics.")


# =====================================================================
#  PER-SECTION BLOCK
# =====================================================================
# 1st-order sections don't take an R envelope from the user (the cap is the
# design DOF); the resistor grid spans this wide internal range and the chosen
# E-series fills it. Absurd pole resistors are still rejected by the solver.
_FO_R_LO_MOHM, _FO_R_HI_MOHM = 1e-4, 10.0          # 100 Ω .. 10 MΩ


def _first_order_settings(sec):
    """Settings UI for a 1st-order section: op-amp + C_max + cap/resistor series.
    No topology-family radio (meaningless at order 1) and no R/C-min envelope —
    the solver realizes the pole at the 3 nearest E-series caps at/below C_max."""
    n = sec["stage_num"]
    with st.expander(f"⚙ Section {n} — component settings", expanded=False):
        opamp = _opamp_picker(n)

        st.markdown("**Component envelope**")
        c_max = st.number_input("C_max (µF)", value=1e-2, format="%.2e", key=f"hw_cmax_{n}",
                                help="Upper capacitor bound. The solver realizes the pole "
                                     "at the 3 nearest E-series cap values at or below this.")

        st.markdown("**Capacitor E-series** (single)")
        c_series = st.radio("C series", C_SERIES_OPTIONS,
                            index=C_SERIES_OPTIONS.index("E12"), horizontal=True,
                            key=f"hw_cser_{n}", label_visibility="collapsed")

        st.markdown("**Resistor E-series** (multiple)")
        rcols = st.columns(len(R_SERIES_OPTIONS))
        r_selected = []
        for i, name in enumerate(R_SERIES_OPTIONS):
            with rcols[i]:
                if st.checkbox(name, value=(name == "E48"), key=f"hw_rser_{n}_{name}"):
                    r_selected.append(name)
        if not r_selected:
            st.warning("No R series selected — defaulting to E48.")
            r_selected = ["E48"]

    env = dict(C_max=c_max, C_series=c_series, R_series=", ".join(r_selected),
               R_min=_FO_R_LO_MOHM, R_max=_FO_R_HI_MOHM, cap_mode="nearest_lower", n_caps=3)
    return env, opamp


def _render_first_order(sec, n):
    """Closed-form 1st-order section: pick realization (sign) + passband gain,
    name the cell, solve via first_order_solver (bypasses unified_solver_v2),
    and hand the LP-shaped result to the shared _render_results."""
    fam = pairing_utils.family_from_section(sec)            # 'LP' or 'HP'
    env, opamp = _first_order_settings(sec)
    g_default = abs(float(section_dc_gain(sec)))            # Pairing Remaining-Gain-Distribution
    glabel = "HF gain" if fam == "HP" else "DC gain"

    c = st.columns([1.3, 1.0, 1.0, 1.4])
    with c[0]:
        realz = st.radio("Realization", ["Non-inverting", "Inverting"],
                         horizontal=True, key=f"hw_fo_real_{n}",
                         help="Non-inverting cells add sign +1 to the cascade; "
                              "inverting cells add −1 (output polarity flips).")
    real = "inv" if realz.startswith("Invert") else "ni"
    with c[1]:
        use_custom = st.checkbox(f"Custom {glabel}", key=f"hw_dc_chk_{n}",
                                 help=f"Off → use the gain this section was allocated by the "
                                      f"Pairing tab's Remaining-Gain-Distribution "
                                      f"({g_default:.3g} V/V). On → enter your own.")
    if use_custom:
        with c[2]:
            _dk = f"hw_dc_val_{n}"
            st.session_state.setdefault(_dk, round(g_default, 4) if g_default > 0 else 1.0)
            G = abs(float(st.number_input(
                glabel, min_value=0.01, step=0.1, key=_dk, label_visibility="collapsed",
                help="Non-inverting: <1 attenuator, =1 unity, >1 gained. "
                     "Inverting: any value (output inverted).")))
    else:
        G = g_default
    mode = fos.gain_to_mode(real, G)
    topo = f"1{fam}-{real}-{mode}"
    sign = -1 if real == "inv" else 1
    with c[3]:
        src = "custom" if use_custom else "from pairing"
        st.caption(f"Cell `{topo}` · sign **{sign:+d}** · |G| = {G:.3g} {glabel} ({src}) "
                   f"· pole fp = {sec['f0_hz']:,.4g} Hz")

    cfg = dict(env, f0=sec["f0_hz"])
    res = fos.synthesize_first_order(cfg, opamp=opamp, topology=topo, dc_gain=G)
    if res.get("__error__"):
        st.error(f"1st-order solver: {res['__error__']}")
        return
    if not res.get("snapped"):
        st.warning("No realization fits the constraints — raise C_max, add a resistor "
                   "E-series, or (ni-gained) relax the gain so R3+R4 lands in 5k–50k.")
        return
    _render_results(res, n, opamp)


def _render_section(sec, conv, gen):
    n = sec["stage_num"]
    head = f"**Section {n}** · order {sec['order']}"
    if sec["order"] == 1:
        head += f" · fp = {sec['f0_hz']:,.4g} Hz"            # single real pole; no Q
    else:
        if sec["order"] == 3:                                # cascaded real pole shown first
            head += f" · fp = {sec['f1_hz']:,.4g} Hz"
        head += f" · f₀ = {sec['f0_hz']:,.4g} Hz · Q = {sec['Q']:.4g}"
        if sec["notch"]:
            head += f" · f_z = {sec['fz_hz']:,.4g} Hz"
    st.markdown(head)

    kind, reason = section_kind(sec)
    if kind == "first_order":
        _render_first_order(sec, n)
        st.divider(); return
    if kind == "pending":
        st.caption(f"⏳ {reason}")
        st.divider(); return

    is_hp = (kind == "hp")          # 'lp' and 'hp' share this rendering path
    unsupported = None              # set by a branch when no cell can realize it
    is_notch = (kind == "notch")    # VCVS 2N pure-notch cells
    is_bp = (kind == "bp")          # VCVS Sallen-Key band-pass cells

    family, env, opamp, mfb_ls, elim_r1, mfb_gained = _section_settings(sec)
    vcvs = family.startswith("VCVS")
    mfb = family.startswith("MFB")           # Multiple-Feedback (LP/LPn, HP/HPn, BP, pure-notch)
    am = family.startswith("AM")             # Ackerberg–Mossberg 3-op-amp state-variable biquad

    cfg_k_override = None            # band-pass: effective Ki injected into cfg["K"]

    cols = st.columns([1.6, 1.0, 1.0, 1.4])

    if is_bp:
        # ---- Band-pass: per-section Ki override (replaces the gain control) ----
        # The passband is the resonant PEAK, whose center gain |H(jω₀)| = Ki·Q/ω₀
        # depends on Q -- so a "custom passband gain" knob would be ambiguous here.
        # Expose the numerator coefficient Ki (rad/s) directly instead: default to
        # the pairing-stage Ki, with an optional per-section override. Both VCVS
        # band-pass cells ("2BP" and the unity-buffer "2BP-atten") are offered and
        # ranked together by the BOM.
        glabel = "Center gain"; hsym = "|H(f₀)|"
        ki_default = float(sec["K_radps"])
        # Ki is the leading-coefficient ratio b_lead/a_lead, so its UNITS follow
        # the section's pole/zero excess: rad/s for the 2nd-order cell and for
        # BP1HP (num ~ s² over a cubic), but (rad/s)² for BP1LP (num ~ s over a
        # cubic). Label it honestly rather than hard-coding "rad/s".
        _bpfam = pairing_utils.family_from_section(sec)
        _kunit = "(rad/s)²" if _bpfam == "BP1LP" else "rad/s"
        _gform = {"BP1HP": "Ki·Q/|jω₀+p₁|",
                  "BP1LP": "Ki·Q/(ω₀·|jω₀+p₁|)"}.get(_bpfam, "Ki·Q/ω₀")
        with cols[1]:
            use_ki = st.checkbox("Override section Ki value", key=f"hw_ki_chk_{n}")
        if use_ki:
            with cols[2]:
                _kk = f"hw_ki_val_{n}"
                st.session_state.setdefault(_kk, ki_default)
                eff_ki = st.number_input(
                    f"Ki ({_kunit})", min_value=1e-12, format="%.6g", key=_kk,
                    label_visibility="collapsed",
                    help=f"Band-pass numerator coefficient Ki (units {_kunit}). "
                         f"Gain at f₀ = {_gform}.")
                st.caption(f":gray[pairing Ki = {ki_default:.4g} {_kunit}]")
        else:
            eff_ki = ki_default
            with cols[2]:
                st.caption(f":gray[pairing Ki = {ki_default:.4g} {_kunit}]")
        cfg_k_override = eff_ki
        # VCVS Sallen-Key band-pass ("2BP"/"2BP-atten", non-inverting) OR the
        # Multiple-Feedback band-pass ("2BP-MFB"/"2BP-MFB-QE", inverting), by the
        # per-section family radio. Both MFB cells realize any (f0,Q,Ki) target and
        # are solved + ranked together by sens_score; the QE (positive-feedback
        # Q-boost) cell reaches high Q with a gentler spread, the plain cell wins
        # where Q is modest. Both are inverting -- the cascade tracks the sign.
        # A real pole absorbed into the band-pass biquad is realized ONLY by the
        # MFB family today (2BP1HP-MFB / 2BP1LP-MFB and their QE twins). The VCVS
        # Sallen-Key and AM state-variable band-pass cells are 2nd order: their
        # denominators are quadratic, so they cannot place the third pole at all.
        # Handing such a section to them would silently realize a DIFFERENT
        # filter -- the solver would match f0/Q/Ki and just drop p1 on the floor.
        # Flag it here and let the gate near the Solve button disable the run.
        # The radio itself stays enabled: VCVS/AM realizations are planned.
        unsupported = None
        if _bpfam in ("BP1LP", "BP1HP") and not mfb:
            unsupported = (
                f"Section {n} is a 3rd-order band-pass (complex pair + absorbed "
                f"real pole at {float(sec.get('f1_hz') or 0.0):.4g} Hz). "
                f"{'AM (Ackerberg-Mossberg)' if am else 'VCVS (Sallen-Key)'} has "
                f"no absorbed-real-pole band-pass cell yet — its band-pass "
                f"biquad is 2nd order and cannot place the third pole. "
                f"Switch this section to **MFB (Friend)** "
                f"(`2BP1{'HP' if _bpfam == 'BP1HP' else 'LP'}-MFB` / `-QE`), or "
                f"re-pair the cascade so the real pole gets its own 1st-order "
                f"stage.")
        if am:
            # AM band-pass: offer ONLY the out1 tap 2BP-AM. The out2 tap
            # 2BP-AM2 is implemented and callable by name but NOT offered
            # here -- with finite GB it has ~8-10 dB worse HF stopband
            # floor (C1 feed-through as U1’s virtual ground degrades),
            # ~10x larger residual Q error, and a Q-shrinking input cap
            # C1=C3/Q that falls below C_min at high Q. See
            # AM_NONIDEAL_ANALYSIS.md §(4). 2BP-AM is the strictly better
            # band-pass tap, so pooling the two would only ever surface it.
            topo = "2BP-AM"
            topos = ["2BP-AM"]
        elif mfb:
            # 3rd-order asymmetric band-pass (a real pole absorbed into the
            # biquad) has its own two-cell pair per absorption direction. Each
            # pair is solved TOGETHER and ranked by sens_score: the plain cell
            # realizes Q passively (sens ~2.0-2.3, flat) and wins wherever it
            # fits; its QE twin trades sensitivity for resistor spread (spread
            # ~4(Q/E)² against the plain cell's ~4Q²) and takes over once 4Q²
            # has overrun [R_min, R_max] -- around Q ~ 15 for a 1000:1 range.
            if _bpfam == "BP1HP":
                topo = "2BP1HP-MFB"
                topos = ["2BP1HP-MFB", "2BP1HP-MFB-QE"]
            elif _bpfam == "BP1LP":
                topo = "2BP1LP-MFB"
                topos = ["2BP1LP-MFB", "2BP1LP-MFB-QE"]
            else:
                topo = "2BP-MFB"
                topos = ["2BP-MFB", "2BP-MFB-QE"]
        else:
            topo = "2BP"
            topos = ["2BP", "2BP-atten"]
        mode = "bp"                  # not "atten" -> atten_blocked stays False
        dc_target = None             # snapper self-references the candidate peak gain
        eff_dc = section_dc_gain({**sec, "K_radps": eff_ki})  # |H(f₀)|, shape-aware
        sig_dc = eff_ki              # cache key tracks Ki (cfg["K"]); synthesize gets dc_gain=None
        with cols[0]:
            src = "custom Ki" if use_ki else "from Ki"
            _cells = ("`2BP-AM` (inverting)" if am else
                      (f"`{topos[0]}` / `{topos[1]}` (inverting)" if mfb else
                       "`2BP` / `2BP-atten`"))
            st.caption(f"Cell: {_cells} · {hsym} = {eff_dc:.3g} "
                       f"({src}) · Ki = {eff_ki:.4g} {_kunit}")
    else:
        # passband-gain nomenclature: an HP passband is at HF (origin zeros block
        # DC); a VCVS notch is inverting with H(0) = H(inf) (same gain DC and HF).
        if is_notch:
            glabel = "DC/HF gain"; hsym = "H(0)=H(∞)"
        elif is_hp:
            glabel = "HF gain"; hsym = "H(∞)"
        else:
            glabel = "DC gain"; hsym = "H(0)"

        # "Eliminate R1" makes the reachable DC-gain band a narrow window just
        # under (fz/f0)^2, so the section gain is no longer a free choice: force
        # Custom DC gain ON and, on the tick TRANSITION only, pre-fill the box
        # with the geometric mean of that window. Latching on the transition (not
        # every rerun) lets the user then nudge the value inside the window.
        # Writing a widget key BEFORE the widget is instantiated is the supported
        # way to seed it.
        if elim_r1 and mfb and _is_lp_notch(sec) and sec["order"] == 2:
            _prev = f"_hw_elimr1_prev_{n}"
            _dkey = f"hw_dc_val_{n}"
            if not st.session_state.get(_prev, False):
                st.session_state[_dkey] = round(_ls_geomean_gain(sec), 4)
            st.session_state[f"hw_dc_chk_{n}"] = True      # compulsory while ticked
            st.session_state[_prev] = True
        else:
            st.session_state[f"_hw_elimr1_prev_{n}"] = False

        # "Gained MFB" pins the 2nd-order HP-notch HF gain into the narrow MFB2
        # band just under (f0/fz)^2 -- same mechanism as Eliminate R1 for LP: force
        # Custom HF gain ON and, on the tick TRANSITION only, pre-fill the box with
        # the geometric mean of the BASE cell's band (reachable by both twins so
        # sens_score has two BOMs to rank). Latch on the transition so the user can
        # then nudge inside the band.
        if mfb_gained and mfb and _is_hp_notch2(sec):
            _prevg = f"_hw_mfbgain_prev_{n}"
            _dkeyg = f"hw_dc_val_{n}"
            if not st.session_state.get(_prevg, False):
                st.session_state[_dkeyg] = round(
                    cells_mfb_hp.mfb2_geomean_gain(
                        sec["f0_hz"], sec["Q"], sec["fz_hz"]), 4)
            st.session_state[f"hw_dc_chk_{n}"] = True      # compulsory while ticked
            st.session_state[_prevg] = True
        else:
            st.session_state[f"_hw_mfbgain_prev_{n}"] = False

        # Read the gain controls FIRST so the cell choice reflects them live.
        with cols[1]:
            use_custom = st.checkbox(f"Custom {glabel}", key=f"hw_dc_chk_{n}",
                                     disabled=bool(elim_r1 or mfb_gained))
        dc_override = None
        if use_custom:
            with cols[2]:
                # HP attenuates at any order (capacitive divider); LP attenuates
                # only at 2nd order (the unity + R7 resistive input divider).
                _gmin = 0.1 if (is_hp or sec["order"] == 2) else 0.1
                _dk = f"hw_dc_val_{n}"
                st.session_state.setdefault(_dk, 1.0)
                if st.session_state[_dk] < _gmin:               # order changed under a sub-unity value
                    st.session_state[_dk] = _gmin
                _hlp = ("HP: any order allows < 1 (→ capacitive-divider attenuator cell)."
                        if is_hp else
                        "2nd-order allows < 1 (→ attenuator cell); 3rd-order is ≥ 1.")
                dc_override = st.number_input(glabel, min_value=_gmin, step=0.1,
                                              key=_dk, label_visibility="collapsed",
                                              help=_hlp)

        # Effective passband gain decides unity vs gained vs atten: the override
        # when enabled, otherwise the math-derived passband gain. The cell name
        # follows the same number, so enabling a custom gain != 1 flips a unity
        # section to gained (and a custom 1.0 on a gained section flips to unity).
        eff_dc = float(dc_override) if use_custom else section_dc_gain(sec)
        if eff_dc < 1.0 - GAIN_UNITY_TOL:
            mode = "atten"        # passband gain < 1 -> input-attenuator cell
        elif abs(eff_dc - 1.0) <= GAIN_UNITY_TOL:
            mode = "unity"
        else:
            mode = "gained"
        if is_notch and am:
            # AM pure notch 2N-AM (inverting, wz=w0 driven). The on-axis
            # zero is STRUCTURAL (no s^1 numerator term exists), so the null
            # depth is matching-independent; a single gain residual pins the
            # whole passband. Any gain (unity/gained/sub-unity) is reachable
            # via the C1/C2 ratio. C1 also co-sets wz, so its parallel-C1 twin
            # (2N-AM-C1s) is offered alongside to reach grid-unreachable C1.
            topo = "2N-AM"
            topos = ["2N-AM", "2N-AM-C1s"]
            dc_target = eff_dc
        elif is_notch and mfb:
            # MFB pure notch (both cells NON-inverting, wz=w0). 2N-MFB
            # (C1,C2,C3,R1,R2,R3,R4,R5) is the gained/UNITY-capable cell -- R5
            # (m->gnd) lifts the DC gain, C3 (m->gnd) lifts the HF gain, so the
            # passband can be set to unity or any gain (opamp+ stays on the R1/R4
            # divider, which is what preserves the on-axis zero). 2N-MFB-atten
            # (C1,C2,R1,R2,R3,R4) is the attenuating-only minimal cell
            # (H(0)=H(inf)=R4/(R1+R4) < 1). For a SUB-UNITY target BOTH are offered
            # in parallel and ranked together by sens_score (2N-MFB-atten is the
            # fewer-part cell; 2N-MFB also covers it). For a UNITY or GAINED target
            # ONLY 2N-MFB is offered -- 2N-MFB-atten cannot reach >= 1.
            topo = "2N-MFB"
            if eff_dc < 1.0 - GAIN_UNITY_TOL:
                topos = ["2N-MFB-atten", "2N-MFB"]
            else:
                topos = ["2N-MFB"]
            dc_target = eff_dc
        elif is_notch:
            # VCVS notch: a single inverting biquad. The R6 cell ("2N") realizes ANY
            # |gain| -- unity (K=1), gained (>1) or mild attenuation -- so it is
            # always the primary cell. Only a section whose passband is ALREADY well
            # below unity can additionally drop R6 (the "2N-atten" cell): without R6
            # the gain is no longer free, it emerges strongly sub-unity, and higher Q
            # needs an impractical resistor spread. So the atten cell is offered as a
            # minimal-component alternative ONLY when the section is attenuating, and
            # the BOM ranks it beside the 2N cell.
            topo = "2N"
            topos = ["2N"]
            if eff_dc < 1.0 - GAIN_UNITY_TOL:
                topos.append("2N-atten")
            dc_target = eff_dc            # 2N cell tracks |gain|; atten ignores K
        else:
            if is_hp:
                prefix = "HPn" if sec["notch"] else "HP"
            else:
                prefix = "LPn" if sec["notch"] else "LP"
            topo = f"{sec['order']}{prefix}-{mode}"
            # Optional b->gnd feedback-attenuator (+R8) twin. R8 is the extra DOF
            # that lets the notch feedback resistor R5 settle near the floor
            # (otherwise it is forced high -> HF hump with a real op-amp). It helps
            # where R5 is a constrained feedback element with room to move: HP notch
            # gained (either order) and the 3rd-order HP notch unity cell. Solve
            # both the plain cell and its +R8 twin; the BOM ranks them together.
            topos = [topo]
            if is_hp and sec["notch"] and (
                    (mode == "gained" and sec["order"] in (2, 3))
                    or (mode == "unity" and sec["order"] == 3)):
                topos.append(topo + "+R8")
            # atten sections carry their sub-unity target passband gain to the
            # solver (cfg["K"] is the rad/s leading coeff for LP, the HF gain for HP).
            dc_target = eff_dc if mode == "atten" else dc_override

            # ---- Multiple-Feedback family override (LP / LPn and HP / HPn) ----
            # MFB family (LP / LPn and HP / HPn). MFB gain is a free component
            # ratio. The HP-notch has TWO complementary realizations: the base
            # HPn-MFB (HF gain = R8/(R3+R8) < 1, attenuating) and HPn-MFB2 (HF
            # gain = [R4/(R3+R4)]*[(C3+C4)/C3] >= ~1, unity/gained). Offer BOTH
            # for an HP-notch and let the solver rank whichever reaches the
            # target; the non-matching one returns an empty BOM and drops out.
            if mfb:
                fam_pre = ("HPn" if sec["notch"] else "HP") if is_hp else \
                          ("LPn" if sec["notch"] else "LP")
                if sec["notch"]:
                    if is_hp:
                        if sec["order"] == 2 and mfb_gained:
                            # ---- 2nd-order HP-notch, GAINED MFB branch --------
                            # The unity/gained MFB2 pair. The plain 2HPn-MFB2 and
                            # its +R7 (p->out positive-feedback) twin are solved in
                            # one cycle and ranked by sens_score: R7 -> inf
                            # degenerates the twin back to the plain cell AND
                            # lowers the band floor, so it strictly contains it and
                            # can only enlarge the pool. HF gain is pinned into the
                            # MFB2 band (geomean pre-filled), so the attenuating
                            # base cell is not offered here.
                            topo = "2HPn-MFB2"
                            topos = ["2HPn-MFB2", "2HPn-MFB2+R7"]
                        else:
                            # Attenuating base cell + MFB2, routed by HF gain. 3rd
                            # order splits hard at HF_GAIN_MARGIN (base divider is
                            # unmanufacturable above it); 2nd order offers both and
                            # lets the solver rank whichever reaches the target.
                            topos = notch_cells_for_gain(sec["order"], eff_dc)
                            topo = (topos[0] if len(topos) == 1 else
                                    (f"{sec['order']}HPn-MFB2"
                                     if eff_dc >= 1.0 - GAIN_UNITY_TOL
                                     else f"{sec['order']}HPn-MFB"))
                    elif not mfb_ls:                 # LP-notch, Friend SAB
                        topo = f"{sec['order']}{fam_pre}-MFB"
                        topos = [topo]
                    else:
                        # ---- LP-notch, LOW-SENSITIVITY branch --------------
                        # Every cell here is solved WITH its +R7 twin in one
                        # cycle and ranked together by sens_score: R7 -> inf
                        # degenerates the twin back into the plain cell, so the
                        # twin strictly contains it and can only enlarge the
                        # pool. Depending on the R/C envelope the +R7 cell is
                        # often the ONLY one that yields a viable BOM (it is
                        # what breaks the C2/C3 >= 4 r Q^2 capacitor-ratio law).
                        if sec["order"] == 3:
                            # R1 + C1 form the input pole -- always present, and
                            # R1 is what makes the 3rd-order cell span (0, r).
                            topo = "3LPn-MFB-LS"
                            topos = ["3LPn-MFB-LS", "3LPn-MFB-LS+R7"]
                        else:
                            _r, _glo, _ghi, _fb, _f7 = _ls_span(sec)
                            # BARE pair spans only (floor_R7, r). The +R1 pair has
                            # no floor at all -- R1 is a third leg on the (+)
                            # divider, so attenuation leaves the Q-bounded R2/R6
                            # ratio and lands in R1/R6. Route by the target gain,
                            # unless the user forced the bare pair.
                            if elim_r1 or (_glo <= eff_dc < _ghi):
                                topo = "2LPn-MFB-LS"
                                topos = ["2LPn-MFB-LS", "2LPn-MFB-LS+R7"]
                            else:
                                topo = "2LPn-MFB-LS+R1"
                                topos = ["2LPn-MFB-LS+R1", "2LPn-MFB-LS+R1+R7"]
                else:
                    topo = f"{sec['order']}{fam_pre}-MFB"
                    topos = [topo, f"{sec['order']}{fam_pre}-MFB-QE"]
                dc_target = eff_dc            # MFB always realizes the target gain

            # ---- Ackerberg–Mossberg family override (LP / LPn and HP / HPn) ----
            # Every AM cell realizes its target gain via a free component
            # ratio (no attenuating/gained split, no MFB2-style twin). The
            # LOW-PASS 2nd-order section additionally offers the classic out2
            # tap 2LP-AM2 beside the out1 tap 2LP-AM: the two are ideal-exactly
            # equivalent (same D(s), same part count) and GB-equivalent (mirror-
            # symmetric pole errors, identical DC error and stopband floor per
            # AM_NONIDEAL_ANALYSIS.md §(3)), so both are solved together and
            # ranked by sens_score. HP and all notch/3rd-order sections have a
            # single AM realization.
            if am:
                fam_pre = ("HPn" if sec["notch"] else "HP") if is_hp else \
                          ("LPn" if sec["notch"] else "LP")
                topo = f"{sec['order']}{fam_pre}-AM"
                if (not is_hp) and (not sec["notch"]):
                    # LOW-PASS out1 + out2 taps, both orders: 2LP-AM/2LP-AM2 and
                    # 3LP-AM/3LP-AM2. Resistive gain (no C1 input), so no -C1s.
                    topos = [f"{sec['order']}LP-AM", f"{sec['order']}LP-AM2"]
                else:
                    # C1-input cells (HP, HPn, LPn): offer the single-C1 cell AND
                    # its parallel-C1 twin (-C1s = C1a||C1b). Both are solved in
                    # one cycle and ranked together, so a C1/C2 HF-gain (or, for
                    # the notch cells, a wz) that no single stock cap can hit is
                    # still realized by the split. See AM_NONIDEAL_ANALYSIS.md.
                    topos = [topo, topo + "-C1s"]
                dc_target = eff_dc            # AM always realizes the target gain

        with cols[0]:
            src = "custom" if use_custom else "from K"
            st.caption(f"Cell: `{topo}` · {hsym} = {eff_dc:.3g} ({src}) · tol ±{GAIN_UNITY_TOL*100:.0f}%")
            # HP-notch gain routing: base HPn-MFB realizes atten (<1), HPn-MFB2
            # realizes unity/gained. Both are in `topos`, so a unity-or-boosted
            # target is NO LONGER pre-blocked here -- if it is genuinely out of
            # MFB2's reachable band (2nd-order ceiling ~ (f0/fz)^2), the solver
            # returns empty and the no-realization probe explains it quantitatively.
            if mfb and is_hp and sec["notch"] and sec["order"] == 2 and mfb_gained:
                _r, _fb, _f7, _ce = cells_mfb_hp.mfb2_gain_band(
                    sec["f0_hz"], sec["Q"], sec["fz_hz"])
                _gm = cells_mfb_hp.mfb2_geomean_gain(
                    sec["f0_hz"], sec["Q"], sec["fz_hz"])
                st.caption(
                    f"ℹ **Gained MFB** (2nd-order HP-notch). `2HPn-MFB2` (7 parts) "
                    f"and `2HPn-MFB2+R7` (8 parts) are solved together and ranked by "
                    f"sens_score. The cap ratio (C3+C4)/C3 lifts the (+)-divider, so "
                    f"H(∞) sits in a **narrow band below (f0/fz)² = {_r:.4g}**: "
                    f"`2HPn-MFB2` reaches **({_fb:.4g}, {_ce:.4g})** "
                    f"[= Q²r²/(1+Q²r) … r], and the p→out positive feedback of "
                    f"`+R7` lowers the floor to **≈{_f7:.4g}** at the cost of S_Q. "
                    f"HF gain is pre-set to the geometric mean {_gm:.4g} (mid-band, "
                    f"reachable by both). Nudge inside the band; redistribute any "
                    f"surplus gain to the other stages. Untick for the attenuating "
                    f"`2HPn-MFB` (HF gain < 1).")
            elif mfb and is_hp and sec["notch"] and eff_dc >= 1.0 - GAIN_UNITY_TOL:
                if sec["order"] == 3:
                    _clamped = eff_dc < V2_GAIN_FLOOR
                    st.caption(
                        f"ℹ {hsym} = {eff_dc:.3g} (> {HF_GAIN_MARGIN}) → `3HPn-MFB2` "
                        "only; the attenuating `3HPn-MFB` divider is unmanufacturable "
                        "above the margin."
                        + (f" This cell's band is open at 1, so the target is solved "
                           f"at its floor {V2_GAIN_FLOOR:g} "
                           f"(+{(V2_GAIN_FLOOR/max(eff_dc,1e-9) - 1)*100:.1f}%, inside "
                           f"the ±{GAIN_UNITY_TOL*100:.0f}% unity band); the realized "
                           "H(∞) is in the table." if _clamped else ""))
                else:
                    st.caption(f"ℹ {hsym} = {eff_dc:.3g} (≥1) routes to the gained "
                               "`2HPn-MFB2` realization; the attenuating `2HPn-MFB` is "
                               "tried in parallel and ranked together. Tick "
                               "**Gained MFB** to also solve the `+R7` twin and "
                               "auto-centre the gain.")
            if mfb and mfb_ls and (not is_hp) and sec["notch"]:
                _r, _glo, _ghi, _fb, _f7 = _ls_span(sec)
                _cmin = 4.0 * _r * sec["Q"] ** 2
                _cwin = (env["C_max"] / env["C_min"]) if env.get("C_min") else float("inf")
                if sec["order"] == 3:
                    st.caption(
                        f"ℹ Low-sensitivity branch: `3LPn-MFB-LS` (9 parts) and "
                        f"`3LPn-MFB-LS+R7` (10 parts) are solved together and ranked "
                        f"by sens_score. R1 (in→a) gives the 3rd-order cell a third "
                        f"(+)-divider leg, so it spans atten / unity / gained up to "
                        f"the ceiling g < (fz/f0)² = {_r:.4g}. Without R7 the "
                        f"capacitor ratio C2/C3 stays near 4·r·Q² ≈ {_cmin:.0f}× "
                        f"(your window is {_cwin:.0f}×); R7 removes that law at the "
                        f"cost of S_Q, and `+R7` also lifts the ceiling.")
                elif elim_r1 or (_glo <= eff_dc < _ghi):
                    _lock = " (forced by “Eliminate R1”)" if elim_r1 else ""
                    st.caption(
                        f"ℹ `2LPn-MFB-LS` (7 parts) + `2LPn-MFB-LS+R7` (8 parts), "
                        f"solved together and ranked by sens_score{_lock}. No R1, so "
                        f"the (+) node hangs on the unloaded {{R2,R6}} divider and the "
                        f"**DC gain must lie in ({_fb:.4g}, {_ghi:.4g})** — you asked "
                        f"{eff_dc:.4g}. Ceiling g < r = (fz/f0)² = {_ghi:.4g}, because "
                        f"H(∞) = R6/(R2+R6) < 1; the floor is r²Q²/(1+rQ²) = {_fb:.4g}, "
                        f"which `+R7` alone relaxes to r − √r/(2Q) = {_f7:.4g}. Any "
                        f"surplus gain/attenuation must be redistributed to the other "
                        f"stages. Capacitor ratio ≥ 4·r·Q² ≈ {_cmin:.0f}× without R7 "
                        f"(your window is {_cwin:.0f}×); R7 removes that law.")
                else:
                    st.caption(
                        f"ℹ `2LPn-MFB-LS+R1` (8 parts) + `2LPn-MFB-LS+R1+R7` (9 parts), "
                        f"solved together and ranked by sens_score. Your target "
                        f"{eff_dc:.4g} sits below the bare pair's window "
                        f"({_glo:.4g}, {_ghi:.4g}), so R1 (in→a) is kept: it is a third "
                        f"leg on the (+) divider and removes the floor entirely — the "
                        f"pair spans (0, {_ghi:.4g}) with S_Q still < 1. The ceiling "
                        f"g < (fz/f0)² = {_r:.4g} survives. Tick “Eliminate R1” to force "
                        f"the 7-part pair instead and redistribute the gain.")
            if mfb and is_notch:
                if eff_dc < 1.0 - GAIN_UNITY_TOL:
                    st.caption("ℹ Sub-unity notch: `2N-MFB-atten` (6 parts, "
                               "attenuating-only) and `2N-MFB` (gained-capable, "
                               "8 parts) are both tried and ranked together.")
                else:
                    st.caption(f"ℹ `2N-MFB` realizes this "
                               f"{'unity' if abs(eff_dc-1.0)<=GAIN_UNITY_TOL else 'gained'} "
                               "notch — C3 (m→gnd) sets the HF gain, R5 (m→gnd) the "
                               "DC gain; the op-amp (+) stays on the R1/R4 divider "
                               "so the notch null is preserved.")
        sig_dc = dc_target

    sig = _job_sig(sec, topo, env, conv, opamp, sig_dc)
    running = sig in st.session_state.hw_jobs
    have = sig in st.session_state.hw_results

    with st.expander(f"🔧 Section {n} — exact solver call", expanded=False):
        st.caption("Arguments sent to synthesize(); diff against your isolated cfg. "
                   "MAX_R_RATIO, C_max and C_series are the usual count-changing mismatches.")
        st.json({
            "cfg": _build_cfg(sec, env, conv, cfg_k_override),
            "topologies": topos,
            "dc_gain": dc_target,
            "n_cores": N_CORES,
            "search": {k: conv[k] for k in ("ratio_starts", "anchored_starts",
                                            "max_valleys", "hints_per_combo",
                                            "pole_tol", "gain_tol", "top_k")},
        })

    atten_blocked = (mode == "atten") and not ATTEN_AVAILABLE
    # MFB now covers EVERY section kind: LP/LPn, HP/HPn, band-pass (2BP-MFB / -QE)
    # and pure-notch (2N-MFB gained/unity + 2N-MFB-atten). Its gain is a free
    # component ratio, so attenuating sections are NOT blocked for MFB.
    mfb_ok = mfb
    am_ok = am               # AM gain is a free ratio -> never atten-blocked
    solvable = (vcvs and not atten_blocked) or mfb_ok or am_ok
    # A family that has no cell for this section's SHAPE can never be solved,
    # whatever its gain situation -- this outranks the atten check.
    if unsupported:
        solvable = False

    with cols[3]:
        clicked = st.button("Re-solve" if have else "Solve section",
                            key=f"hw_solve_{n}", type="primary",
                            disabled=running or not solvable)
    if not vcvs and not mfb and not am:
        st.caption("Solve disabled — select VCVS, MFB or AM for this section.")
    elif unsupported:
        st.warning(unsupported, icon="⚠️")
    elif atten_blocked:
        st.caption(f"This section needs DC gain < 1 (H(0) = {eff_dc:.3g}) → an attenuator "
                   f"cell `{topo}`, not yet wired in the solver. Coming soon.")
    elif clicked and not running:
        _submit(sig, _build_cfg(sec, env, conv, cfg_k_override), opamp, topos, conv, dc_target, gen)
        running = True

    if running:
        st.caption("⚙️ Solving… (other sections and tabs stay responsive)")
    elif have:
        _render_results(st.session_state.hw_results[sig], n, opamp,
                        cfg=_build_cfg(sec, env, conv, cfg_k_override),
                        topos=topos, dc_gain=dc_target)
    st.divider()


def _render_overall(sections):
    st.markdown("---")
    st.markdown("##### Overall filter")
    realizable = [s for s in sections if section_kind(s)[0] in ("lp", "hp", "first_order", "notch", "bp")]
    picked = st.session_state.get("hw_picked", {})
    if not realizable:
        st.caption("No realizable sections to combine yet.")
        return
    missing = [s["stage_num"] for s in realizable if s["stage_num"] not in picked]
    if missing:
        st.caption("Select a solution for every section to see the overall gain "
                   f"(still pending: section {', '.join(map(str, missing))}).")
        return

    # Realized DC-gain product + cascade sign (∏ of per-stage realization signs).
    g_real_dc, overall_sign = 1.0, 1
    for s in realizable:
        row = picked[s["stage_num"]]
        dc = row.get("_dc")
        g_real_dc *= dc if dc is not None else 1.0
        overall_sign *= int(row.get("sign", 1))

    # Ideal math DC gain: 2nd/3rd-order from section_dc_gain; 1st-order uses the
    # chosen target gain (section_dc_gain assumes a 2nd-order denominator).
    g_math_dc = 1.0
    for s in realizable:
        if int(s.get("order", 2)) >= 2:
            g_math_dc *= section_dc_gain(s)
        else:
            g_math_dc *= float(picked[s["stage_num"]].get("internal_gain", 1.0))
    user_pb = st.session_state.get("hw_pb_gain",
                                   st.session_state.get("_mem_gain", 1.0))
    is_br = st.session_state.get("hw_filter_type") == "Band-Reject"
    is_hp_filter = st.session_state.get("hw_filter_type") == "Highpass"

    if overall_sign < 0:
        st.warning("⚠ Output is **inverted** — an odd number of inverting (inv) stages "
                   "gives overall sign −1. Add one more inverting stage (or an inverter) "
                   "to restore positive polarity.")

    if is_br:
        # Band-Reject has TWO passbands (below and above the stop-band). Each
        # section contributes a DC gain |H(0)| to the low band and an HF gain
        # |H(∞)| to the high band; the cascade gain in each band is the product.
        #   LF realized passband gain = target · (∏ actual |H(0)|) / (∏ ideal |H(0)|)
        #   HF realized passband gain = target · (∏ actual |H(∞)|) / (∏ ideal |H(∞)|)
        # The (actual/ideal) ratio is the realization error in that band; scaling
        # by the user target reports the realized PASSBAND gain. (For even-order
        # rippled responses |DC|/|HF| differ from the passband gain, so the ideal
        # ratio carries the band-edge-to-passband conversion.)
        ideal_dc = ideal_hf = 1.0
        actual_dc = actual_hf = 1.0
        meta_all = st.session_state.get("hw_picked_meta", {})
        complete = True
        for s in realizable:
            row = picked[s["stage_num"]]
            f0 = float(s.get("f0_hz") or 0.0)
            fz = float(s.get("fz_hz") or 0.0)
            kr = abs(float(s.get("K_radps", 1.0)))
            rho = (fz / f0) ** 2 if (s.get("notch") and f0 > 0 and fz > 0) else 1.0
            ideal_dc *= kr * rho          # math |H(0)| = |K|·(fz/f0)²
            ideal_hf *= kr                # math |H(∞)| = |K|
            if "_dchf" not in row:        # realized ends, memoized per pick
                meta = meta_all.get(s["stage_num"], {})
                row["_dchf"] = _dc_hf_realized(
                    row, meta.get("case"),
                    meta.get("eval_opamp", IDEAL_OPAMP), fz)
            a_dc, a_hf = row["_dchf"]
            if a_dc is None or a_hf is None:
                complete = False
            else:
                actual_dc *= a_dc
                actual_hf *= a_hf

        c = st.columns(2)
        if complete and ideal_dc > 1e-12 and ideal_hf > 1e-12:
            lf = user_pb * actual_dc / ideal_dc
            hf = user_pb * actual_hf / ideal_hf
            with c[0]:
                st.metric("LF passband gain", f"{lf:.4f} V/V",
                          help="Realized gain in the LOW passband (below the stop-band): "
                               "target × (∏ realized |H(0)|) ÷ (∏ ideal |H(0)|).")
            with c[1]:
                st.metric("HF passband gain", f"{hf:.4f} V/V",
                          help="Realized gain in the HIGH passband (above the stop-band): "
                               "target × (∏ realized |H(∞)|) ÷ (∏ ideal |H(∞)|).")
        else:
            with c[0]:
                st.metric("LF passband gain", "—")
            with c[1]:
                st.metric("HF passband gain", "—")
        st.caption(f"Target passband gain {user_pb:.4g} V/V · ideal |DC| product "
                   f"{ideal_dc:.4f} · ideal |HF| product {ideal_hf:.4f} · cascade sign "
                   f"{overall_sign:+d}. Band-Reject reports each passband separately; "
                   "for even-order Chebyshev/Elliptic the band edges sit above |DC|/|HF|.")
    else:
        # A band-pass cascade has DC in the STOP-band, so an "overall DC gain" is
        # meaningless (~0). Show only the realized passband (center) gain.
        is_bp = any(section_kind(s)[0] == "bp" for s in realizable)
        if is_bp:
            if abs(g_math_dc) > 1e-12:
                g_real_pb = (user_pb / g_math_dc) * g_real_dc
                st.metric("Overall realized passband gain", f"{g_real_pb:.4f} V/V",
                          help="Band-pass center-frequency gain: target PB gain × "
                               "(∏ realized section peak |H(jω₀)|) ÷ (∏ ideal section "
                               "peak Ki·Q/ω₀). Approximate — for stagger-tuned sections "
                               "the per-section peaks sit at different f₀, so the exact "
                               "cascade peak comes with the swept Bode view.")
            else:
                st.metric("Overall realized passband gain", "—")
            st.caption(f"Target passband gain {user_pb:.4g} V/V · ideal center-gain "
                       f"product {abs(g_math_dc):.4f} V/V · cascade sign {overall_sign:+d}. "
                       "Band-pass has no DC/HF passband — the gain is the resonant peak "
                       "at f₀ (DC and HF are both stop-bands).")
        else:
            c = st.columns(2)
            with c[0]:
                _band = "HF" if is_hp_filter else "DC"
                st.metric(f"Overall realized {_band} gain", f"{overall_sign * g_real_dc:+.4f} V/V",
                          help=f"∏ of the selected sections' realized {_band} gains, signed by the "
                               "∏ of their realization signs (inverting → −1).")
            with c[1]:
                if abs(g_math_dc) > 1e-12:
                    g_real_pb = (user_pb / g_math_dc) * g_real_dc
                    st.metric("Overall realized passband gain", f"{g_real_pb:.4f} V/V",
                              help="Realized DC gain scaled by the math passband/DC ratio "
                                   "(target PB gain ÷ ideal math DC gain). Approximate — the exact "
                                   "swept peak comes with the Bode view.")
                else:
                    st.metric("Overall realized passband gain", "—")
            st.caption(f"Target passband gain {user_pb:.4g} V/V · ideal math |DC| gain "
                       f"{abs(g_math_dc):.4f} V/V · cascade sign {overall_sign:+d}. For "
                       "non-rippled responses |DC| and passband coincide; even-order "
                       "Chebyshev/Elliptic peak above DC.")


# =====================================================================
#  PUBLIC ENTRY  (call inside `with tab_topology:`)
# =====================================================================
@st.fragment(run_every=2.0)
def render_topology_tab():
    _ensure_state()
    st.markdown("#### Topology & Hardware Synthesis")

    sections = st.session_state.get("hw_sections")
    if not sections:
        st.info("Finalize the cascade in **Biquad Pairing & Cascading** first — "
                "each section will appear here for hardware synthesis.")
        return

    gen = st.session_state.get("hw_gen")
    if st.session_state.get("_picked_gen") != gen:
        # Cascade generation changed: drop picks ONLY for stages that no longer
        # exist. A spuriously regenerated hw_gen must not wipe still-valid picks,
        # or the Response tab loses them after every full rerun.
        valid = {s["stage_num"] for s in sections}
        st.session_state.hw_picked = {k: v for k, v in st.session_state.hw_picked.items() if k in valid}
        st.session_state.hw_picked_meta = {k: v for k, v in st.session_state.hw_picked_meta.items() if k in valid}
        st.session_state.bom_picks = {k: v for k, v in st.session_state.get("bom_picks", {}).items() if k in valid}
        for _k in [k for k in _PICKS if k not in valid]:
            _PICKS.pop(_k, None)
        st.session_state._picked_gen = gen
        st.session_state.bom_picks_cont = {
            k: v for k, v in st.session_state.get("bom_picks_cont", {}).items()
            if k in valid}
    if _drain_finished():
        st.session_state["_picks_dirty"] = True

    conv = _convergence_inputs()
    # Op-amp is now per-section (in each section's settings expander).
    # Cores are frozen at N_CORES (=32) for the web demo — no UI control on purpose.
    st.markdown("---")

    pending = len(st.session_state.hw_jobs)
    if pending:
        st.caption(f"⚙️ {pending} section(s) solving in the background…")

    for sec in sections:
        _render_section(sec, conv, gen)

    _render_overall(sections)

    # A row click reruns this fragment only, so the Response tab (rendered
    # outside it, in app.py) would keep its stale placeholder until the user
    # pressed R. Ask for one app-wide rerun; the flag is popped first, so this
    # cannot loop against run_every.
    if st.session_state.pop("_picks_dirty", False):
        st.rerun(scope="app")
