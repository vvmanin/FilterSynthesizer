# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""
response_tab.py — "Resulting Response & Schematic" tab.

Cascade view of the SELECTED per-section BOMs:
  * upper section : Monte-Carlo controls (multi-range resistor tolerances)
  * Bode plot     : ideal/design curve + realized cascade + Monte-Carlo band,
                    with optional phase / group-delay overlays (checkboxes,
                    default off, next to the plot)
  * below         : each picked section's schematic, stacked in stage order
                    (the same SVG annotation used in the Topology tab)

Reads st.session_state.hw_picked (chosen BOM rows) and hw_picked_meta (the
nonideal case + op-amp needed to rebuild each section's response), both
populated by the Topology tab when a row is selected.
"""

import re

import numpy as np
import streamlit as st
import streamlit.components.v1 as components

import hw_plots as pf
import plot_utils as PU
import schematic_svg as schematic
import tf_derivation_v2 as TF
import cells_first_order as FO
from filter_synthesis import IDEAL_OPAMP
from topology_tab import (section_kind, section_dc_gain, opamp_label,
                          _ensure_state, OPAMP_LIBRARY, _PICKS)
import report_ui


# ---------------------------------------------------------------------
def _effective_dc(sec):
    """Same rule the Topology tab uses: custom override if enabled, else the
    math-derived H(0). Drives the design-target gain in the overlay. Band-pass
    sections read the Ki override and convert to the gain at f0 via
    section_dc_gain, matching the Topology tab's per-section Ki control.

    The Ki -> |H(f0)| conversion MUST go through section_dc_gain rather than be
    written out here: it is shape-dependent, and the three band-pass shapes all
    arrive with section_kind() == "bp".
        2nd order   |H(f0)| = Ki*Q/w0                 Ki in rad/s
        BP1HP       |H(f0)| = Ki*Q/|jw0+p1|           Ki in rad/s
        BP1LP       |H(f0)| = Ki*Q/(w0*|jw0+p1|)      Ki in (rad/s)^2
    This branch used to apply the 2nd-order form unconditionally, which left the
    IDEAL overlay floating above the realized curve on every 3rd-order
    band-pass section -- by 20*log10(sqrt(1+(p1/w0)^2)) dB for BP1HP (e.g. ~4 dB
    at f1 ~ 1.2*f0) and by a wild margin for BP1LP, whose Ki is not even in the
    same units. For 2nd-order sections section_dc_gain returns exactly
    Ki*Q/w0, so their overlay is unchanged.
    """
    n = sec["stage_num"]
    if section_kind(sec)[0] == "bp":
        if st.session_state.get(f"hw_ki_chk_{n}", False):
            ki = float(st.session_state.get(f"hw_ki_val_{n}", sec["K_radps"]))
        else:
            ki = float(sec["K_radps"])
        return float(section_dc_gain({**sec, "K_radps": ki}))
    if st.session_state.get(f"hw_dc_chk_{n}", False):
        return float(st.session_state.get(f"hw_dc_val_{n}", 1.0))
    return float(section_dc_gain(sec))


def _eff_dc_for(sec, row):
    """Design-target passband gain for the overlay. 1st-order sections use the
    chosen target gain from the BOM row (section_dc_gain assumes a 2nd-order
    denominator); everything else uses the standard rule."""
    if section_kind(sec)[0] == "first_order":
        return float(row.get("internal_gain", 1.0))
    return _effective_dc(sec)


_CELL_RE = re.compile(r"^(\d+)(LPn|LP)-(unity|gained|atten)(\+R7)?$")


def _topo_to_dict(name):
    """Canonical cell name -> topology dict (carrying its 'family') for
    tf_derivation.derive_nonideal. Resolved through the engine's family-aware
    registry lookup, so LP, HP (and future families) all work without a
    per-family regex here. Returns None for an unrecognized name."""
    try:
        return TF.topo_for_name(str(name or ""))
    except KeyError:
        return None


@st.cache_resource
def _section_H_cached(topo_name, struct_sig):
    """(H, names) for a cell, derived straight from its name (cached). The
    non-ideal TF needs only the topology (no design constants), so this is
    identical to the solver's case and never depends on solver/session state.
    1st-order names dispatch to cells_first_order (Tier B).

    `struct_sig` (tf_derivation_v2.cell_struct_sig) is part of the CACHE KEY, not
    used in the body. A cell's netlist can change under a fixed name across
    versions -- the LP-notch LS branch renamed its op-amp feedback cap C4 -> C3
    when the a->out cap was dropped -- and a name-only key would then serve the
    OLD lambdified TF for the rest of the Streamlit session. The failure is
    SILENT: unified_solver_v2._assemble writes absent designators into the BOM
    row as 0.0, so hw_plots.comp_dict feeds the stale TF a ZERO capacitor rather
    than raising. The notch disappears and the stopband plateau lifts."""
    if FO.is_first_order(topo_name):
        return TF.make_response_func(FO.derive_first_order_nonideal(FO.parse_name(topo_name)))
    topo = _topo_to_dict(topo_name)
    if topo is None:
        raise ValueError(f"unrecognized topology: {topo_name!r}")
    return TF.make_response_func(TF.derive_nonideal(topo))


def _section_H(topo_name):
    return _section_H_cached(topo_name, TF.cell_struct_sig(topo_name))


@st.cache_resource
def _loggrad_cached(topo_name, struct_sig):
    """(H'/H fn, names) for analytic group delay of a cell (cached). Same
    struct-sig salt as _section_H_cached -- see the note there."""
    if FO.is_first_order(topo_name):
        return pf.build_loggrad(FO.derive_first_order_nonideal(FO.parse_name(topo_name)))
    return pf.build_loggrad(TF.derive_nonideal(_topo_to_dict(topo_name)))


def _loggrad(topo_name):
    return _loggrad_cached(topo_name, TF.cell_struct_sig(topo_name))


def _eval_opamp(n):
    """Reconstruct the section's op-amp params from its picker state."""
    choice = st.session_state.get(f"hw_opamp_choice_{n}")
    spec = OPAMP_LIBRARY.get(choice) if choice else None
    if spec == "CUSTOM":
        spec = dict(A_ol=st.session_state.get(f"hw_aol_{n}", 1e5),
                    GBWP_hz=st.session_state.get(f"hw_gbwp_{n}", 0.95e5),
                    Ro=st.session_state.get(f"hw_ro_{n}", 1000.0) / 1e6)
    return spec if isinstance(spec, dict) else IDEAL_OPAMP


def _freq_grid(sections):
    fl, fh = [], []
    for s in sections:
        fl.append(s["f0_hz"]); fh.append(s["f0_hz"])
        if s["notch"] and s.get("fz_hz"):
            fl.append(s["fz_hz"]); fh.append(s["fz_hz"])
        if s["order"] == 3 and s.get("f1_hz"):
            fl.append(s["f1_hz"]); fh.append(s["f1_hz"])
    fmin = max(1e-3, 0.1 * min(fl))
    fmax = 50.0 * max(fh)
    f = np.logspace(np.log10(fmin), np.log10(fmax), 600)
    return f


def _rtol_parse(s):
    """Parse a kΩ text field -> float, or None for blank ('Max'/∞)."""
    s = (s or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _rtol_bands_ui():
    """Contiguous resistor tolerance bands in kΩ:  Rmin ≤ R < Rmax.
    Rmin auto-fills from the previous row's Rmax; the last band is open-ended
    ('Max'). Typing the last Rmax grows a new band. Default tol 1% per row.
    Returns r_bands as (min_ohm, max_ohm, tol_pct) for monte_carlo."""
    st.caption("**Resistor tolerance bands** — Rmin ≤ R < Rmax, in kΩ. "
               "Type a max to open the next band; blank max = ∞.")
    n = st.session_state.setdefault("rtol_n", 1)
    # auto-grow: a filled last-row Rmax opens a fresh open-ended band
    if _rtol_parse(st.session_state.get(f"rtol_max_{n-1}")) not in (None, 0.0):
        n += 1
        st.session_state["rtol_n"] = n

    head = st.columns([1.1, 1.1, 0.9])
    head[0].caption("R min (kΩ)"); head[1].caption("R max (kΩ)"); head[2].caption("tol %")
    for i in range(n):
        col = st.columns([1.1, 1.1, 0.9])
        prev_max = _rtol_parse(st.session_state.get(f"rtol_max_{i-1}")) if i > 0 else 0.0
        disp = "0" if i == 0 else (f"{prev_max:g}" if prev_max else "—")
        col[0].markdown(f"<div style='padding:6px 0 0;color:#555'>{disp}</div>",
                        unsafe_allow_html=True)
        is_last = (i == n - 1)
        col[1].text_input("max", key=f"rtol_max_{i}", label_visibility="collapsed",
                          placeholder="Max" if is_last else "")
        st.session_state.setdefault(f"rtol_tol_{i}", 1.0)
        col[2].number_input("tol", key=f"rtol_tol_{i}", min_value=0.0, step=0.1,
                            label_visibility="collapsed")
    if n > 1 and st.button("✕ remove last band", key="rtol_rm"):
        st.session_state.pop(f"rtol_max_{n-1}", None)
        st.session_state.pop(f"rtol_tol_{n-1}", None)
        st.session_state["rtol_n"] = n - 1
        st.rerun()

    bands = []
    for i in range(st.session_state["rtol_n"]):
        lo = 0.0 if i == 0 else (_rtol_parse(st.session_state.get(f"rtol_max_{i-1}")) or 0.0)
        hi = _rtol_parse(st.session_state.get(f"rtol_max_{i}"))
        tol = st.session_state.get(f"rtol_tol_{i}", 1.0) or 1.0
        hi_ohm = hi * 1e3 if hi not in (None, 0.0) else 1e12     # kΩ -> Ω, blank=∞
        bands.append((lo * 1e3, hi_ohm, tol))                   # Rmin inclusive, Rmax exclusive
    return bands


def _mc_key(sections_data, params):
    sig = []
    for d in sections_data:
        cd = pf.comp_dict(d["row"], d["names"], d["eval_opamp"])
        sig.append((d["row"].get("topology"),
                    tuple(sorted((k, round(float(v), 15)) for k, v in cd.items()))))
    return repr((sig, params))


# ---------------------------------------------------------------------
#  Whole-filter ideal curve — built the SAME way as the first/main tab
#  (plot_utils.evaluate_h_complex on the design engine's poles/zeros + a single
#  gain constant), instead of a product of per-section K_radps gains (which do
#  not aggregate to the passband gain → the constant dB offset).
# ---------------------------------------------------------------------
def _find_engine_results():
    """The design engine's whole-filter result (poles/zeros in rad/s + scalar
    'k'), as fed to plot_utils.plot_main_magnitude on the first tab. Tries the
    common session_state keys, then any dict carrying the engine signature."""
    ss = st.session_state
    for k in ("engine_results", "results", "design_results", "engine",
              "design", "engine_out", "filter_results", "design_engine"):
        v = ss.get(k)
        if isinstance(v, dict) and {"poles", "zeros", "k"} <= set(v):
            return v
    for v in ss.values():
        if isinstance(v, dict) and {"poles", "zeros", "k"} <= set(v):
            return v
    return None


def _find_target_gain():
    """Passband gain (linear) the first tab applies as engine 'k' * gain. The
    first tab uses `k_scaled = engine_results['k'] * target_gain_units`. Returns
    None if no such scalar is in session_state."""
    ss = st.session_state
    for k in ("target_gain_units", "passband_gain_linear", "passband_gain",
              "target_gain", "gain_units", "target_gain_linear"):
        v = ss.get(k)
        try:
            if v is not None and float(v) > 0:
                return float(v)
        except (TypeError, ValueError):
            pass
    return None


def _overlay_scale(realized, ideal_unit):
    """Single constant that lines a unit-gain ideal up with the realized curve
    at the passband. Both carry the design's poles/zeros, so |realized|/|ideal|
    is ~flat; anchor at the realized magnitude peak (passband / resonance
    plateau) so the design curve overlays without needing the target gain."""
    rm = np.abs(np.asarray(realized)); im = np.abs(np.asarray(ideal_unit))
    i = int(np.argmax(rm))
    return float(rm[i] / im[i]) if im[i] > 1e-300 else 1.0


def _hf_hump(realized, ideal, f, zero_freqs=()):
    """Peak dB by which the REALIZED curve (red) rises above the DESIGN curve
    (ideal, blue) over the far-band grid `f` — a DIRECT pointwise
    20·log10|realized| − 20·log10|ideal|, i.e. exactly the vertical gap between the
    two traces the Bode plot draws (both are raw magnitudes, no scaling). The
    caller passes a grid spanning the highest passband corner up to 100× it.

    Points within a half-octave of a design transmission zero are skipped: at a
    null both curves plunge, and a small null-frequency mismatch would otherwise
    read as a huge spurious "hump" that is not a resonance.

    Returns (hump_db >= 0, f_peak_hz | None). ~0 dB for a clean rolloff (single-
    op-amp MFB, or AM with a low-Rₒ op-amp)."""
    f = np.asarray(f, dtype=float)
    diff = (20.0 * np.log10(np.abs(np.asarray(realized)) + 1e-300)
            - 20.0 * np.log10(np.abs(np.asarray(ideal)) + 1e-300))
    keep = np.ones(f.shape, dtype=bool)
    for fz in zero_freqs:
        if fz and fz > 0:
            keep &= ~((f > fz / 1.5) & (f < fz * 1.5))     # ½-octave guard each zero
    if not keep.any():
        return 0.0, None
    diff = np.where(keep, diff, -np.inf)
    i = int(np.argmax(diff))
    if not np.isfinite(diff[i]):
        return 0.0, None
    return max(0.0, float(diff[i])), float(f[i])


def _design_eff_dc(sec, picked):
    """Design passband gain for the ideal curve. 2nd/3rd-order sections read it
    straight from the math (section_dc_gain / custom override) with NO realized
    BOM needed -- so 2N notch stages that were never picked still contribute.
    1st-order sections fall back to their picked BOM gain."""
    if section_kind(sec)[0] == "first_order":
        row = picked.get(sec["stage_num"], {})
        return float(row.get("internal_gain", 1.0))
    return _effective_dc(sec)


def _build_ideal(sections, picked, w):
    """Whole-filter design (ideal) curve — first-tab style — over EVERY section,
    INCLUDING the pure-notch "2N" stages. This is the math design target and is
    deliberately decoupled from the physical realization: the realized curve, by
    contrast, shorts the 2N stages out (in -> out), so the two diverge across the
    central notch by exactly the un-realized 2N contribution.

    Each section contributes its MATH design response (poles/zeros from f0/Q/fz
    + its design passband gain via target_response); the product is the complete
    filter transfer. The cascade gain is the product of the section passband
    gains (= the design passband gain), so there is no per-section K_radps offset
    and no need to anchor against the realized curve. Pure math: no realized
    components, no op-amp."""
    if not sections:
        return np.ones_like(np.asarray(w), dtype=complex)
    return pf.cascade([pf.target_response(s, w, _design_eff_dc(s, picked))
                       for s in sections])


# ---------------------------------------------------------------------
def render_response_tab():
    _ensure_state()
    st.markdown("#### Resulting Response & Schematic")

    sections = st.session_state.get("hw_sections")
    if not sections:
        st.info("Finalize the cascade and synthesize sections in the **Topology** "
                "tab first — this view combines the selected per-section BOMs.")
        report_ui.render_blocked("no cascade has been finalized yet. "
                                 "Pair the biquads, then solve each section in "
                                 "the **Topology** tab.")
        return

    realizable = sorted((s for s in sections
                         if section_kind(s)[0] in ("lp", "hp", "first_order", "notch", "bp")),
                        key=lambda s: s["stage_num"])
    # Durable stores first: bom_picks (no hw_ prefix) and the module-level _PICKS
    # both survive other tabs resetting hw_ state.
    picked = (st.session_state.get("bom_picks")
              or dict(_PICKS)
              or st.session_state.get("hw_picked", {}))
    if not realizable:
        st.caption("No realizable sections to combine yet.")
        report_ui.render_blocked("no realizable sections in the cascade yet.")
        return
    missing = [s["stage_num"] for s in realizable if s["stage_num"] not in picked]
    if missing:
        st.info("Pick a BOM for every section in the **Topology** tab to see the "
                f"cascade response (still pending: section {', '.join(map(str, missing))}).")
        if st.session_state.get("_debug_picks"):
            st.caption(f"(diag) bom_picks: {sorted(st.session_state.get('bom_picks', {}))} · "
                       f"_PICKS: {sorted(_PICKS)} · "
                       f"hw_picked: {sorted(st.session_state.get('hw_picked', {}))} · "
                   f"stages: {[s['stage_num'] for s in realizable]}")
        report_ui.render_blocked(
            "section " + ", ".join(map(str, missing)) + " still "
            f"{'has' if len(missing) == 1 else 'have'} no selected BOM. "
            "Solve and pick a row for every section in the **Topology** tab.")
        return

    # Every realized section with a chosen BOM is in the cascade now, including
    # the pure-notch "2N" stages -- so the realized curve is the full filter, not
    # just the HP/LP path. (A section left unpicked would simply be absent, i.e.
    # shorted in -> out, but the check above already requires every section.)

    # ---- gather per-section response data (cached H) in stage order ----
    # Response model is derived straight from each section's topology name and
    # cached, so this works from hw_picked alone — no solver/meta state needed.
    sections_data, marks = [], []
    for s in realizable:
        n = s["stage_num"]
        row = picked[n]
        topo = row.get("topology")
        try:
            H, names = _section_H(topo)
        except Exception as _e:
            st.warning(f"Section {n}: can't build response for `{topo}` ({_e}).")
            report_ui.render_blocked(f"section {n}'s response model could not be "
                                     f"built from `{topo}`.")
            return
        sections_data.append({"sec": s, "n": n, "row": row, "H": H, "names": names,
                              "eval_opamp": _eval_opamp(n),
                              "eff_dc": _eff_dc_for(s, row)})
        marks.append((s["f0_hz"], "f0"))
        if s["notch"] and s.get("fz_hz"):
            marks.append((s["fz_hz"], "fz"))

    f = _freq_grid(realizable)
    w = 2.0 * np.pi * f

    # ---- realized cascade + ideal (design) curve ----
    # Realized = product of EVERY picked section's BOM response (HP/LP, 1st-order
    # AND the pure-notch 2N stages) -> the full realized filter. Ideal = the same
    # sections' MATH design responses (first-tab poles/zeros, no components, no
    # op-amp). The two coincide in the passband and differ only by the per-section
    # realization error (E-series snap + finite op-amp).
    realized = pf.cascade([pf.realized_response(d["H"], d["names"], d["row"],
                                                d["eval_opamp"], w) for d in sections_data])
    ideal = _build_ideal(realizable, picked, w)

    # ---- HF-hump check (finite-Ro loop resonance, AM §5) ----
    # Scan the realized (red) vs design (ideal, blue) curves across the far branch
    # [highest corner .. 100× highest corner] on a DEDICATED grid (the plot grid
    # stops sooner), and report the largest vertical gap where red > blue and the
    # frequency it occurs at -- a direct dB subtraction of the two plotted traces.
    # Design transmission zeros are guarded out inside _hf_hump. Works for any
    # AM/MFB section mix; the message only attributes it to AM when AM is present.
    _f_hi = max([s["f0_hz"] for s in realizable]
                + [s["fz_hz"] for s in realizable if s.get("fz_hz")]
                + [s["f1_hz"] for s in realizable if s.get("f1_hz")])
    _zeros = [s["fz_hz"] for s in realizable if s.get("notch") and s.get("fz_hz")]
    _f_hf = np.logspace(np.log10(_f_hi), np.log10(100.0 * _f_hi), 500)
    _w_hf = 2.0 * np.pi * _f_hf
    _real_hf = pf.cascade([pf.realized_response(d["H"], d["names"], d["row"],
                                                d["eval_opamp"], _w_hf)
                           for d in sections_data])
    _ideal_hf = _build_ideal(realizable, picked, _w_hf)
    _hump_db, _f_pk = _hf_hump(_real_hf, _ideal_hf, _f_hf, _zeros)
    _report_warnings = []
    if _hump_db >= 1.0:
        _am = [d["n"] for d in sections_data
               if "-AM" in str(d["row"].get("topology", ""))]
        _fk = (f"{_f_pk/1e3:.1f} kHz" if _f_pk and _f_pk >= 1e3
               else (f"{_f_pk:.0f} Hz" if _f_pk else "the far band"))
        _rng = f"[{_f_hi/1e3:.1f}–{100*_f_hi/1e3:.0f} kHz]"
        if _am:
            _msg = (
                f"⚠ **HF resonance (~{_hump_db:.0f} dB at {_fk})** — over {_rng} the "
                f"realized (red) response peaks ~{_hump_db:.0f} dB above the design "
                f"(blue). This is the Ackerberg–Mossberg finite-Rₒ loop resonance "
                f"from the op-amp output impedance (AM section"
                f"{'s' if len(_am) > 1 else ''} {', '.join(map(str, _am))}), not a "
                f"synthesis error. Mitigate with a lower-Rₒ op-amp (≤ ~50 Ω) or MFB "
                f"for those sections — see AM_NONIDEAL_ANALYSIS.md §5.")
        else:
            _msg = (
                f"⚠ **HF rise (~{_hump_db:.0f} dB at {_fk})** — over {_rng} the realized "
                f"(red) response exceeds the design (blue) by ~{_hump_db:.0f} dB, most "
                f"likely finite op-amp output impedance. Consider a lower-Rₒ op-amp.")
        st.warning(_msg)
        _report_warnings.append(_msg.replace("**", "").replace("⚠ ", ""))

    # =================================================================
    #  MONTE-CARLO CONTROLS  (upper section)
    # =================================================================
    st.markdown("##### Monte-Carlo tolerances")
    st.caption("Per-component tolerance spread of the cascade. Resistors use "
               "value-banded tolerances (rows below); op-amp parameters are held fixed.")

    cc = st.columns([2.2, 1])
    with cc[0]:
        r_bands = _rtol_bands_ui()
    with cc[1]:
        c_tol = st.number_input("Capacitor tol (%)", value=5.0, min_value=0.0,
                                step=0.5, key="resp_ctol")
        n_runs = int(st.number_input("Runs", value=2000, min_value=10, max_value=20000,
                                     step=100, key="resp_runs"))
        dist_lbl = st.radio("Distribution", ["Gaussian (tol = 3σ)", "Uniform (±tol)"],
                            index=0, key="resp_dist")
        seed = int(st.number_input("Seed", value=0, min_value=0, step=1, key="resp_seed"))
        band_lbl = st.selectbox("Envelope", ["p1–p99", "p5–p95", "min–max"],
                                index=0, key="resp_envelope")

    dist_key = "uniform" if dist_lbl.startswith("Uniform") else "gaussian"
    lo, hi = {"p1–p99": (1.0, 99.0), "p5–p95": (5.0, 95.0),
              "min–max": (0.0, 100.0)}[band_lbl]
    if n_runs > 1000:
        st.caption(f"⚠ {n_runs} runs may take a few seconds.")

    params = (round(c_tol, 6), tuple(r_bands), n_runs, dist_key, seed, lo, hi)
    key = _mc_key(sections_data, params)
    # Plain-data mirror of the same settings, for the PDF report's caption.
    mc_params = dict(r_bands=list(r_bands), c_tol_pct=float(c_tol),
                     n_runs=int(n_runs), dist=dist_key, seed=int(seed),
                     lo_pct=float(lo), hi_pct=float(hi), n_sigma=3.0)
    run = st.button("Run Monte-Carlo", type="primary", key="resp_mc_run")
    store = st.session_state.get("resp_mc")
    if run:
        try:
            with st.spinner(f"Monte-Carlo — {n_runs} cascade runs…"):
                res = pf.monte_carlo(sections_data, w, n_runs=n_runs, c_tol_pct=c_tol,
                                     r_bands=r_bands, dist=dist_key, seed=seed,
                                     lo_pct=lo, hi_pct=hi)
            st.session_state["resp_mc"] = {"key": key, **res}
            store = st.session_state["resp_mc"]
        except Exception as _e:
            st.error(f"Monte-Carlo failed: {_e}")
            store = None
    mc = store if (store and store.get("key") == key) else None
    if store is not None and store.get("key") != key:
        st.caption("Inputs changed since the last run — click **Run Monte-Carlo** "
                   "to refresh the band.")

    if mc is not None:
        dc_spread = mc["mags"][:, 0]
        st.caption(f"DC-gain spread over {mc['n_runs']} runs: "
                   f"{dc_spread.min():.2f} … {dc_spread.max():.2f} dB "
                   f"(nominal {pf.mag_db(realized)[0]:.2f} dB).")

    # =================================================================
    #  BODE PLOT
    # =================================================================
    st.markdown("##### Bode — design vs realized")
    tc = st.columns([1, 1, 1, 5])
    with tc[0]:
        show_linear = st.checkbox("Linear Mag.", value=False, key="resp_show_linear")
    with tc[1]:
        show_phase = st.checkbox("Phase", value=False, key="resp_show_phase")
    with tc[2]:
        show_gd = st.checkbox("Group delay", value=False, key="resp_show_gd")

    fig_ideal_gd = fig_realized_gd = None
    if show_gd:
        ipoles, izeros = [], []
        rgd = np.zeros_like(f)
        for d in sections_data:
            ip, iz = pf.ideal_poles_zeros(d["sec"])
            ipoles.append(ip)
            izeros.append(iz)
            hh_fn, gnames = _loggrad(d["row"].get("topology"))
            cd = pf.comp_dict(d["row"], gnames, d["eval_opamp"])
            rgd = rgd + pf.group_delay_section(hh_fn, gnames, cd, w)  # delays add
        ip_all = np.concatenate(ipoles) if ipoles else np.array([])
        iz_all = np.concatenate(izeros) if izeros else np.array([])
        fig_ideal_gd = pf.group_delay_pz(w, ip_all, iz_all) * 1e3
        fig_realized_gd = rgd * 1e3

    fig = pf.bode_figure(f, ideal, realized, mc=mc,
                         show_phase=show_phase, show_gd=show_gd,
                         show_linear=show_linear,
                         ideal_gd=fig_ideal_gd, realized_gd=fig_realized_gd,
                         section_marks=marks, freq_unit="Hz")
    st.plotly_chart(fig, use_container_width=True)

    # =================================================================
    #  STACKED SECTION SCHEMATICS  (same diagrams as the Topology tab)
    # =================================================================
    st.markdown("##### Section schematics")
    for d in sections_data:
        n = d["n"]
        row = d["row"]
        topo = row.get("topology")
        st.caption(f"Section {n} · `{topo}`")
        try:
            svg = schematic.render_svg(topo, n, row, opamp_pn=opamp_label(n))
            components.html(schematic.schematic_iframe_html(svg, max_width=760),
                            height=560, scrolling=False)
            schematic.download_buttons(st, svg, topo, n, key_prefix=f"resp_sch_{n}")
        except FileNotFoundError:
            st.caption(f"⚠ Schematic SVG not found — expected "
                       f"`{schematic.svg_filename(topo, row)}` in "
                       f"`{schematic.SVG_DIR}`.")
        except Exception as _e:
            st.caption(f"⚠ Schematic render error: {_e}")

    # =================================================================
    #  GENERATE REPORT
    # =================================================================
    # Everything below the gate is already computed above, so the report costs
    # nothing until the button is pressed. `run_mc` lets the report build its
    # Monte-Carlo page even when the band on screen is absent or stale.
    def _run_mc_for_report():
        res = pf.monte_carlo(sections_data, w, n_runs=n_runs, c_tol_pct=c_tol,
                             r_bands=r_bands, dist=dist_key, seed=seed,
                             lo_pct=lo, hi_pct=hi)
        st.session_state["resp_mc"] = {"key": key, **res}
        return st.session_state["resp_mc"]

    report_ui.render_report_section(
        sections_data=sections_data, picked=picked, f=f,
        ideal=ideal, realized=realized, mc=mc, mc_params=mc_params,
        marks=marks, warnings=_report_warnings, run_mc=_run_mc_for_report,
        ideal_gd=fig_ideal_gd, realized_gd=fig_realized_gd)
