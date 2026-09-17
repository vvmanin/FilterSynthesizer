# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

import os
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

# Must run before any process pool is built. Streamlit installs a fresh
# __main__ whose __spec__ is None and whose __file__ is app.py, which makes
# multiprocessing order every spawned worker to re-execute this whole file.
import mp_fix
mp_fix.neutralize_main()
import streamlit as st
import concurrent.futures
import numpy as np
import pandas as pd

from filter_engine import synthesize_lowpass, synthesize_highpass, synthesize_bandpass, synthesize_bandreject
from plot_utils import plot_main_magnitude, plot_passband_magnitude, plot_phase_delay, evaluate_h_complex, plot_pole_zero_map, plot_mnemoscheme_map
from tf_utils import clean_roots, poly_to_latex, roots_to_biquad_latex, build_coeff_table, format_val, format_latex_val
from pairing_utils import build_stage_bricks, auto_pair_stages, find_clicked_brick, compute_stage_gains
from topology_tab import render_topology_tab
from response_tab import render_response_tab
from _version import __version__, APP_NAME

# Import all UI components
from ui_components import (
    draw_order_block, 
    draw_frequency_block, 
    draw_gain_block, 
    draw_ripple_block, 
    draw_modifications_block, 
    validate_filter_specs
)


from pool_utils import run_in_pool, format_exc_for_ui, env_summary


# ============================================================
# BAND-REJECT "EQUALIZE DC AND HF GAINS" GAIN DISTRIBUTION
# ============================================================
def _stage_rho(stage, p_bricks, z_bricks):
    """ρ = (wz/w0)² for a notch stage — the finite (complex-pair) zero frequency
    over the pole frequency — and 1.0 for a stage with no finite zero. A notch
    section has H(0)/H(∞) = ρ, so ρ is exactly the DC-to-HF gain ratio. It is a
    pure ratio, hence identical in Hz and rad/s."""
    pb = next((b for b in p_bricks if b['id'] == stage['pole_id']), None)
    w0 = abs(pb['w0']) if pb else 0.0
    wz = 0.0
    for zid in stage.get('zero_ids', []):
        zb = next((b for b in z_bricks if b['id'] == zid), None)
        if zb is not None and zb['type'] == 'Complex Pair':
            wz = abs(zb['w0'])
    if w0 > 1e-12 and wz > 1e-12:
        return (wz / w0) ** 2
    return 1.0


def _section_peak_mag(k, stage, p_bricks, z_bricks):
    """Standalone peak |H(jω)| of one section at leading-coeff gain k, for the
    Peak-Mag display column (a notch can peak above both its DC and HF gains via
    the Q-bump, so the swept maximum is the honest figure)."""
    from scipy import signal
    zeros, poles = [], []
    pb = next((b for b in p_bricks if b['id'] == stage['pole_id']), None)
    if pb:
        poles.append(pb['root'])
        if pb['type'] == 'Complex Pair':
            poles.append(np.conj(pb['root']))
    aid = stage.get('absorbed_real_id', 'None')
    if aid != 'None':
        ab = next((b for b in p_bricks if b['id'] == aid), None)
        if ab:
            poles.append(ab['root'])
    for zid in stage.get('zero_ids', []):
        zb = next((b for b in z_bricks if b['id'] == zid), None)
        if zb:
            zeros.append(zb['root'])
            if zb['type'] == 'Complex Pair':
                zeros.append(np.conj(zb['root']))
    corners = [abs(p) for p in poles if abs(p) > 1e-9] + [abs(z) for z in zeros if abs(z) > 1e-9]
    if not corners:
        return abs(k)
    w = np.logspace(np.log10(min(corners) * 0.01), np.log10(max(corners) * 100.0), 1500)
    try:
        _, h = signal.freqs_zpk(zeros, poles, k, worN=w)
        return float(max(np.max(np.abs(h)), abs(k)))   # include the HF plateau (=|k|)
    except Exception:
        return abs(k)


def _equalize_dc_hf_ks(stages, p_bricks, z_bricks, raw_ks, k_remainder):
    """Band-Reject 'Equalize DC and HF gains of LP and HP sections' distribution.

    Splits the cascade gain as K_i ∝ 1/√ρ_i (ρ_i = (fz_i/f0_i)²), renormalized so
    ∏K_i equals the SAME product every other option yields (∏raw_ks · k_remainder
    = k_system · target_gain). Since a notch section has H(0)/H(∞) = ρ, choosing
    K_i = C/√ρ_i makes its DC gain (C·√ρ) and HF gain (C/√ρ) symmetric about the
    common C: the LPn section (ρ>1) becomes gained at DC, the HPn section (ρ<1)
    becomes gained at HF, the LPn DC gain equals the mirrored HPn HF gain, and
    ∏H_i(0) = ∏H_i(∞) = the target — i.e. BOTH the LF and HF passbands land on the
    user's defined passband gain. Returns (final_ks, peak_mags)."""
    n = len(stages)
    if n == 0:
        return [], []
    rhos = [_stage_rho(s, p_bricks, z_bricks) for s in stages]
    base = [1.0 / np.sqrt(r) if r > 1e-12 else 1.0 for r in rhos]
    target_prod = float(np.prod(raw_ks)) * float(k_remainder)
    prod_base = float(np.prod(base))
    if prod_base > 1e-300 and target_prod > 0:
        alpha = (target_prod / prod_base) ** (1.0 / n)
    else:
        alpha = 1.0
    final_ks = [b * alpha for b in base]
    peak_mags = [_section_peak_mag(final_ks[i], stages[i], p_bricks, z_bricks)
                 for i in range(n)]
    return final_ks, peak_mags


# ============================================================
# MAIN PAGE CONFIGURATION & CSS
# ============================================================
st.set_page_config(page_title=APP_NAME, layout="wide")

st.markdown("""
    <style>
    /* Apply Arial generally, but safely */
    html, body { font-family: 'Arial', sans-serif; font-size: 14px; }
    
    /* Explicitly protect Streamlit's internal icon fonts so arrows don't turn into text */
    .material-icons, .material-symbols-rounded { font-family: 'Material Symbols Rounded' !important; }
    
    h1 { font-size: 26px !important; font-weight: 600 !important; }
    [data-testid="stSidebar"] h2 { font-size: 18px !important; color: #2e7bcf; }
    .stRadio label, .stCheckbox label { font-size: 12px !important; }

    /* Shrink the massive st.metric numbers */
    [data-testid="stMetricValue"] { font-size: 20px !important; }
    [data-testid="stMetricDelta"] { font-size: 14px !important; }
    
    /* Hides Streamlit's custom +/- buttons */
    div[data-testid="stNumberInputStepUp"] { display: none !important; }
    div[data-testid="stNumberInputStepDown"] { display: none !important; }
    
    /* Hides standard browser spinners */
    input[type="number"]::-webkit-inner-spin-button, 
    input[type="number"]::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
    input[type="number"] { -moz-appearance: textfield; }

    /* =========================================
       SIDEBAR CONDENSING TRICKS
       ========================================= */
    /* 1. Shrink the massive flexbox gaps between widgets */
    [data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
        gap: 0.3rem !important;
    }
    
    /* 2. Squeeze the empty space around horizontal lines (---) */
    [data-testid="stSidebar"] hr {
        margin-top: 0.4rem !important;
        margin-bottom: 0.4rem !important;
    }
    
    /* 3. Trim the invisible padding above and below headers */
    [data-testid="stSidebar"] h2, 
    [data-testid="stSidebar"] h4,
    [data-testid="stSidebar"] p {
        padding-top: 0.2rem !important;
        padding-bottom: 0rem !important;
        margin-bottom: 0rem !important;
    }
    
    /* 4. Condense the radio buttons and checkboxes */
    [data-testid="stSidebar"] .stRadio > div {
        gap: 0.2rem !important;
    }
    [data-testid="stSidebar"] .stCheckbox {
        padding-bottom: 0rem !important;
        min-height: 1.5rem !important;
    }
    [data-testid="stSidebar"] .stCheckbox label {
        min-height: 1.5rem !important;
    }
    
    /* 5. Tweak number input container margins */
    div[data-testid="stNumberInput"] {
        margin-bottom: -0.2rem !important;
    }
    </style>
""", unsafe_allow_html=True)



# ============================================================
# LEFT SIDEBAR CHASSIS
# ============================================================
with st.sidebar:
    st.header("Filter Configuration")
    
    response = st.radio("Response", ["Butterworth", "Chebyshev", "Inverse Chebyshev", "Elliptic"], index=0)
    st.markdown("---")
    
    filter_type = st.radio("Filter Type", ["Lowpass", "Highpass", "Bandpass", "Band-Reject"], index=0)
    st.markdown("---")
    
    final_lp_order, final_hp_order = draw_order_block(response, filter_type)
    st.markdown("---")
    
    f1_val, f2_val, freq_unit = draw_frequency_block(filter_type)
    st.markdown("---")
    
    final_gain_units = draw_gain_block()
    st.markdown("---")
    
    final_alpha, final_as_lp, final_as_hp = draw_ripple_block(response, filter_type)
        
    pb_mod_lp, pb_mod_hp, sb_roll_lp, sb_roll_hp = draw_modifications_block(
    response, filter_type, final_lp_order, final_hp_order
)
    st.markdown("---")

# ============================================================
# MAIN CANVAS CHASSIS
# ============================================================

# DYNAMIC TITLE
st.title(f"{APP_NAME} v{__version__} - {response} {filter_type}")

is_valid, error_msg = validate_filter_specs(response, filter_type, final_lp_order, final_hp_order)

if not is_valid:
    st.error(f"**Mathematical Constraint Violation:** {error_msg}")
    st.warning("Please adjust your specifications in the sidebar to continue.")
    st.stop()

# Tabs
tab_plots, tab_roots, tab_pairing, tab_topology, tab_response = st.tabs([
    "📊 Response Plots", 
    "🔢 Roots & Transfer Function", 
    "🧱 Biquad Pairing & Cascading",
    "⚙️ Topology",
    "📉 Resulting Response & Schematic"
])

multiplier = {"Hz": 1, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}[freq_unit]
real_fc = f1_val * multiplier

# ------------------------------------------------------------
# 1. RUN THE ENGINE (Hidden from UI, runs first to get data)
# ------------------------------------------------------------
engine_results = None
active_slots = {}
P_eff = 0

import time

@st.cache_data(max_entries=50, show_spinner=False)
def run_lowpass_in_background(response, order, fc_hz, alpha_max, as_db, manual_notches_hz, pb_even_mod, sb_rolloff):
    time.sleep(0.85) # <-- The Debounce Timer!
    return run_in_pool(synthesize_lowpass, response, order, fc_hz, alpha_max, as_db, manual_notches_hz, pb_even_mod, sb_rolloff)
    

@st.cache_data(max_entries=50, show_spinner=False)
def run_highpass_in_background(response, order, fc_hz, alpha_max, as_db, manual_notches_hz, pb_even_mod, sb_rolloff):
    time.sleep(0.85) # <-- The Debounce Timer!
    return run_in_pool(synthesize_highpass, response, order, fc_hz, alpha_max, as_db, manual_notches_hz, pb_even_mod, sb_rolloff)


@st.cache_data(max_entries=50, show_spinner=False)
def run_bandpass_in_background(
    response, order_hp, order_lp, f1_hz, f2_hz, alpha_max, as_hp_db, as_lp_db, 
    manual_notches_hp_hz, manual_notches_lp_hz, pb_even_mod_hp, pb_even_mod_lp, 
    sb_rolloff_hp, sb_rolloff_lp
):
    time.sleep(0.05) # <-- The Debounce Timer!
    return run_in_pool(
        synthesize_bandpass, 
        response, order_hp, order_lp, f1_hz, f2_hz, alpha_max, as_hp_db, as_lp_db, 
        manual_notches_hp_hz, manual_notches_lp_hz, pb_even_mod_hp, pb_even_mod_lp, 
        sb_rolloff_hp, sb_rolloff_lp
    )


@st.cache_data(max_entries=50, show_spinner=False)
def run_bandreject_in_background(
    response, order_lp, order_hp, f1_hz, f2_hz, alpha_max, as_db, 
    manual_notches_hz, pb_even_mod_lp, pb_even_mod_hp, sb_rolloff
):
    time.sleep(0.85) # <-- The Debounce Timer!
    return run_in_pool(
        synthesize_bandreject,
        response, order_lp, order_hp, f1_hz, f2_hz, alpha_max, as_db, 
        manual_notches_hz, pb_even_mod_lp, pb_even_mod_hp, sb_rolloff
    )


if filter_type == "Lowpass":
    P = final_lp_order // 2
    P_eff = P - 1 if sb_roll_lp and response in ["Inverse Chebyshev", "Elliptic"] else P
    safe_fallback = f2_val if f2_val is not None else f1_val * 2.0
    
    for i in range(P_eff):
        # ... (keep your existing Lowpass pin logic exactly as is) ...
        if st.session_state.get(f"pin_notch_{i}", False):
            if f"val_notch_{i}" not in st.session_state:
                if response in ["Elliptic", "Inverse Chebyshev"]:
                    raw_val = st.session_state.get('_last_free_notches', {}).get(i, safe_fallback)
                else:
                    raw_val = safe_fallback
                if raw_val is None: raw_val = safe_fallback
                st.session_state[f"val_notch_{i}"] = raw_val
            else:
                raw_val = st.session_state[f"val_notch_{i}"]
            active_slots[i] = raw_val * multiplier

    try:
        with st.spinner("Synthesizing Lowpass Filter on Ryzen Core..."):
            engine_results = run_lowpass_in_background(
                response=response, order=final_lp_order, fc_hz=real_fc,
                alpha_max=final_alpha, as_db=final_as_lp, manual_notches_hz=active_slots, 
                pb_even_mod=pb_mod_lp, sb_rolloff=sb_roll_lp
            )
    except Exception as e:
        st.error(f"**Engine Error:** {type(e).__name__}: {e}")
        with st.expander("Details (paste this into a bug report)"):
            st.code(format_exc_for_ui(e))
            st.code(env_summary())

elif filter_type == "Highpass":
    P = final_hp_order // 2
    P_eff = P - 1 if sb_roll_hp and response in ["Inverse Chebyshev", "Elliptic"] else P
    safe_fallback = f2_val if f2_val is not None else f1_val * 0.5 # HP fallback is below cutoff!
    
    for i in range(P_eff):
        if st.session_state.get(f"pin_notch_{i}", False):
            if f"val_notch_{i}" not in st.session_state:
                if response in ["Elliptic", "Inverse Chebyshev"]:
                    raw_val = st.session_state.get('_last_free_notches', {}).get(i, safe_fallback)
                else:
                    raw_val = safe_fallback
                if raw_val is None: raw_val = safe_fallback
                st.session_state[f"val_notch_{i}"] = raw_val
            else:
                raw_val = st.session_state[f"val_notch_{i}"]
            active_slots[i] = raw_val * multiplier

    try:
        with st.spinner("Synthesizing Highpass Filter on Ryzen Core..."):
            engine_results = run_highpass_in_background(
                response=response, order=final_hp_order, fc_hz=real_fc,
                alpha_max=final_alpha, as_db=final_as_hp, manual_notches_hz=active_slots, 
                pb_even_mod=pb_mod_hp, sb_rolloff=sb_roll_hp
            )
    except Exception as e:
        st.error(f"**Engine Error:** {type(e).__name__}: {e}")
        with st.expander("Details (paste this into a bug report)"):
            st.code(format_exc_for_ui(e))
            st.code(env_summary())

elif filter_type == "Bandpass":
    P_hp = final_hp_order // 2
    P_lp = final_lp_order // 2
    
    P_eff_hp = P_hp - 1 if sb_roll_hp and response in ["Inverse Chebyshev", "Elliptic"] else P_hp
    P_eff_lp = P_lp - 1 if sb_roll_lp and response in ["Inverse Chebyshev", "Elliptic"] else P_lp
    
    active_slots_hp = {}
    active_slots_lp = {}
    
    # 1. Lower Stopband (HP) Notch Extraction
    safe_fallback_hp = f1_val * 0.5 
    for i in range(P_eff_hp):
        if st.session_state.get(f"pin_notch_hp_{i}", False):
            if f"val_notch_hp_{i}" not in st.session_state:
                raw_val = st.session_state.get('_last_free_notches_hp', {}).get(i, safe_fallback_hp) if response in ["Elliptic", "Inverse Chebyshev"] else safe_fallback_hp
                if raw_val is None: raw_val = safe_fallback_hp
                st.session_state[f"val_notch_hp_{i}"] = raw_val
            else:
                raw_val = st.session_state[f"val_notch_hp_{i}"]
            active_slots_hp[i] = raw_val * multiplier

    # 2. Upper Stopband (LP) Notch Extraction
    safe_fallback_lp = f2_val * 2.0 
    for i in range(P_eff_lp):
        if st.session_state.get(f"pin_notch_lp_{i}", False):
            if f"val_notch_lp_{i}" not in st.session_state:
                raw_val = st.session_state.get('_last_free_notches_lp', {}).get(i, safe_fallback_lp) if response in ["Elliptic", "Inverse Chebyshev"] else safe_fallback_lp
                if raw_val is None: raw_val = safe_fallback_lp
                st.session_state[f"val_notch_lp_{i}"] = raw_val
            else:
                raw_val = st.session_state[f"val_notch_lp_{i}"]
            active_slots_lp[i] = raw_val * multiplier

    try:
        with st.spinner("Synthesizing Asymmetric Elliptic Bandpass on Ryzen Core..."):
            engine_results = run_bandpass_in_background(
                response=response, 
                order_hp=final_hp_order, 
                order_lp=final_lp_order, 
                f1_hz=f1_val * multiplier, 
                f2_hz=f2_val * multiplier,
                alpha_max=final_alpha, 
                as_hp_db=final_as_hp, 
                as_lp_db=final_as_lp, 
                manual_notches_hp_hz=active_slots_hp, 
                manual_notches_lp_hz=active_slots_lp, 
                pb_even_mod_hp=pb_mod_hp, 
                pb_even_mod_lp=pb_mod_lp, 
                sb_rolloff_hp=sb_roll_hp, 
                sb_rolloff_lp=sb_roll_lp
            )
    except Exception as e:
        st.error(f"**Engine Error:** {type(e).__name__}: {e}")
        with st.expander("Details (paste this into a bug report)"):
            st.code(format_exc_for_ui(e))
            st.code(env_summary())

elif filter_type == "Band-Reject":
    P_tot = (final_lp_order + final_hp_order) // 2
    P_eff_br = P_tot - 1 if sb_roll_lp and response in ["Inverse Chebyshev", "Elliptic"] else P_tot
    
    active_slots_br = {}
    f0_fallback = np.sqrt(f1_val * f2_val)
    
    # Skip manual notch collection entirely for Elliptic to protect passbands
    if response != "Elliptic":
        for i in range(P_eff_br):
            if st.session_state.get(f"pin_notch_{i}", False):
                if f"val_notch_{i}" not in st.session_state:
                    raw_val = st.session_state.get('_last_free_notches', {}).get(i, f0_fallback) if response in ["Elliptic", "Inverse Chebyshev"] else f0_fallback
                    if raw_val is None: raw_val = f0_fallback
                    st.session_state[f"val_notch_{i}"] = raw_val
                else:
                    raw_val = st.session_state[f"val_notch_{i}"]
                active_slots_br[i] = raw_val * multiplier

    try:
        strict_as = max(final_as_hp, final_as_lp)
        with st.spinner("Synthesizing Bandreject Filter on Ryzen Core..."):
            engine_results = run_bandreject_in_background(
                response=response, 
                order_lp=final_lp_order, 
                order_hp=final_hp_order, 
                f1_hz=f1_val * multiplier, 
                f2_hz=f2_val * multiplier,
                alpha_max=final_alpha, 
                as_db=strict_as, 
                manual_notches_hz=active_slots_br, 
                pb_even_mod_lp=pb_mod_lp, 
                pb_even_mod_hp=pb_mod_hp, 
                sb_rolloff=sb_roll_lp # <--- Coincident Notches toggle still passes safely!
            )
    except Exception as e:
        st.error(f"**Engine Error:** {type(e).__name__}: {e}")
        with st.expander("Details (paste this into a bug report)"):
            st.code(format_exc_for_ui(e))
            st.code(env_summary())
        
# ------------------------------------------------------------
# 2. RENDER TAB: PLOTS & NOTCH GRID
# ------------------------------------------------------------
free_notches_display = {}
free_notches_display_hp = {}
free_notches_display_lp = {}

if engine_results:
    z_rad = engine_results['zeros']
    actual_rad = sorted([z.imag for z in z_rad if z.imag > 1e-6 and abs(z.real) < 1e-6])
    actual_notches_hz = [r / (2 * np.pi) for r in actual_rad]
    
    if filter_type in ["Lowpass", "Highpass"]:
        if filter_type == "Highpass": actual_notches_hz.reverse()
        pool = actual_notches_hz.copy()
        
        for slot_idx, val_hz in active_slots.items():
            if pool:
                closest_idx = np.argmin(np.abs(np.array(pool) - val_hz))
                pool.pop(closest_idx)
                
        pool_idx = 0
        for i in range(P_eff):
            if i not in active_slots:
                free_notches_display[i] = pool[pool_idx] if pool_idx < len(pool) else 0.0
                pool_idx += 1
        st.session_state['_last_free_notches'] = {k: v / multiplier for k, v in free_notches_display.items()}

    elif filter_type == "Bandpass":
        f0_hz = np.sqrt(f1_val * f2_val * multiplier * multiplier)
        pool_hp = [f for f in actual_notches_hz if f < f0_hz]
        pool_lp = [f for f in actual_notches_hz if f > f0_hz]
        pool_hp.reverse() 
        
        for slot_idx, val_hz in active_slots_hp.items():
            if pool_hp:
                closest_idx = np.argmin(np.abs(np.array(pool_hp) - val_hz))
                pool_hp.pop(closest_idx)
        pool_idx = 0
        for i in range(P_eff_hp):
            if i not in active_slots_hp:
                free_notches_display_hp[i] = pool_hp[pool_idx] if pool_idx < len(pool_hp) else 0.0
                pool_idx += 1
                
        for slot_idx, val_hz in active_slots_lp.items():
            if pool_lp:
                closest_idx = np.argmin(np.abs(np.array(pool_lp) - val_hz))
                pool_lp.pop(closest_idx)
        pool_idx = 0
        for i in range(P_eff_lp):
            if i not in active_slots_lp:
                free_notches_display_lp[i] = pool_lp[pool_idx] if pool_idx < len(pool_lp) else 0.0
                pool_idx += 1
                
        st.session_state['_last_free_notches_hp'] = {k: v / multiplier for k, v in free_notches_display_hp.items()}
        st.session_state['_last_free_notches_lp'] = {k: v / multiplier for k, v in free_notches_display_lp.items()}

    elif filter_type == "Band-Reject":
        if response != "Elliptic":
            pool = actual_notches_hz.copy()
            for slot_idx, val_hz in active_slots_br.items():
                if pool:
                    closest_idx = np.argmin(np.abs(np.array(pool) - val_hz))
                    pool.pop(closest_idx)
            pool_idx = 0
            P_tot = (final_lp_order + final_hp_order) // 2
            P_eff_br = P_tot - 1 if sb_roll_lp and response in ["Inverse Chebyshev", "Elliptic"] else P_tot
            
            for i in range(P_eff_br):
                if i not in active_slots_br:
                    free_notches_display[i] = pool[pool_idx] if pool_idx < len(pool) else 0.0
                    pool_idx += 1
            st.session_state['_last_free_notches'] = {k: v / multiplier for k, v in free_notches_display.items()}

# ------------------------------------------------------------
# 1B. REPORT SNAPSHOT  (read by report_ui.py / report_pdf.py)
# ------------------------------------------------------------
# Flat, streamlit-free mirror of the sidebar spec and the engine result.
# Nothing else in the app reads these keys, so this block is inert unless a
# report is generated.
def _rep_slots(slots, label):
    if not slots:
        return None
    return (label, "; ".join(f"slot {i} → {v/multiplier:g} {freq_unit}"
                             for i, v in sorted(slots.items())))

def _rep_edge(v, stat):
    if not v:
        return "—"
    txt = f"{v/multiplier:,.4f} {freq_unit}"
    if stat == "degraded":
        return txt + " (extended)"
    if str(stat).startswith("corrupted"):
        return txt + " (does not meet A_s)"
    return txt

_is_band = filter_type in ("Bandpass", "Band-Reject")
_tot_order = final_lp_order + (final_hp_order if _is_band else 0)
_corners = (f"{f1_val:g}…{f2_val:g} {freq_unit}" if _is_band and f2_val
            else f"{f1_val:g} {freq_unit}")

_rep = [("Response", response), ("Filter type", filter_type)]
if _is_band:
    _rep += [("Order (LP / HP)", f"{final_lp_order} / {final_hp_order}"),
             ("Total order", f"{_tot_order}"),
             ("Lower corner f1", f"{f1_val:g} {freq_unit}"),
             ("Upper corner f2", f"{f2_val:g} {freq_unit}" if f2_val else "—")]
else:
    _rep += [("Order", f"{final_lp_order}"),
             ("Corner frequency fc", f"{f1_val:g} {freq_unit}")]
_rep += [("Passband gain", f"{final_gain_units:g} V/V "
                           f"({20*np.log10(final_gain_units):+.2f} dB)"),
         ("Passband ripple α_max" if response in ("Chebyshev", "Elliptic")
          else "Passband attenuation", f"{final_alpha:g} dB")]
if _is_band and final_as_lp != final_as_hp:
    _rep += [("Stopband A_sl (lower)", f"{final_as_hp:g} dB"),
             ("Stopband A_su (upper)", f"{final_as_lp:g} dB")]
else:
    _rep += [("Stopband A_s", f"{final_as_lp:g} dB")]
_rep += [("Passband even-order mod.",
          (("LP " if pb_mod_lp else "") + ("HP " if pb_mod_hp else "")) or "off"),
         ("Stopband roll-off",
          (("LP " if sb_roll_lp else "") + ("HP " if sb_roll_hp else "")) or "off")]
for _lbl, _sl in (("Manual notches", globals().get("active_slots")),
                  ("Manual notches (lower)", globals().get("active_slots_hp")),
                  ("Manual notches (upper)", globals().get("active_slots_lp")),
                  ("Manual notches (band-reject)", globals().get("active_slots_br"))):
    _row = _rep_slots(_sl, _lbl)
    if _row:
        _rep.append(_row)
if not any(k.startswith("Manual notches") for k, *_ in _rep):
    _rep.append(("Manual notches", "none (all free)"))

# Calculated stopband edges -- a 3-tuple marks a FULL-WIDTH row in the report's
# spec table, so the long "Lower = …, Upper = …" string is not squeezed into a
# half-width cell. Mirrors the read-only engine output shown under the plots.
if engine_results:
    if filter_type in ("Lowpass", "Highpass"):
        _rep.append(("Calculated stopband edge f_s",
                     _rep_edge(engine_results.get("f_stop_hz"),
                               engine_results.get("sb_status", "normal")), True))
    elif filter_type == "Bandpass":
        _rep.append(("Calculated stopband edges",
                     "Lower = " + _rep_edge(engine_results.get("f_stop_hp_hz"),
                                            engine_results.get("sb_status_hp", "normal"))
                     + ",  Upper = " + _rep_edge(engine_results.get("f_stop_lp_hz"),
                                                 engine_results.get("sb_status_lp", "normal")),
                     True))
    elif filter_type == "Band-Reject":
        _sbr = engine_results.get("sb_status_hp", "normal")
        _rep.append(("Calculated stopband edges",
                     "Lower = " + _rep_edge(engine_results.get("f_stop_hp_hz"), _sbr)
                     + ",  Upper = " + _rep_edge(engine_results.get("f_stop_lp_hz"), _sbr),
                     True))

st.session_state["report_spec"] = _rep
st.session_state["report_spec_short"] = (
    f"{response.replace(' ', '')}_{filter_type.replace('-', '')}_n{_tot_order}")
st.session_state["report_subtitle"] = (
    f"{response} {filter_type} · order {_tot_order} · {_corners}")

if engine_results:
    _wn = (2 * np.pi * np.sqrt(f1_val * f2_val) * multiplier
           if _is_band and f2_val else 2 * np.pi * real_fc)
    st.session_state["report_engine"] = {
        "poles": np.asarray(engine_results["poles"]),
        "zeros": np.asarray(engine_results["zeros"]),
        "k": float(engine_results["k"]),
        "gain_units": float(final_gain_units),
        "w_norm": float(_wn)}
    # Window (and heading) of the optional detail plot. For a band-reject the
    # interesting band is the notch, so it is a STOPBAND detail, not a passband
    # one; everything else zooms its passband.
    _lo = f1_val * multiplier
    _hi = (f2_val or f1_val) * multiplier
    if filter_type == "Lowpass":
        st.session_state["report_passband"] = (real_fc / 30.0, real_fc * 2.0)
        st.session_state["report_detail_label"] = "Passband detail"
    elif filter_type == "Highpass":
        st.session_state["report_passband"] = (real_fc / 2.0, real_fc * 30.0)
        st.session_state["report_detail_label"] = "Passband detail"
    elif filter_type == "Band-Reject":
        st.session_state["report_passband"] = (_lo / 1.6, _hi * 1.6)
        st.session_state["report_detail_label"] = "Stopband detail"
    else:
        st.session_state["report_passband"] = (_lo / 3.0, _hi * 3.0)
        st.session_state["report_detail_label"] = "Passband detail"

    # TRUE passband edges (Hz) for the report's passband-gain metric -- not the
    # widened detail-plot window above. `None` is an open end, clamped to the
    # plotted grid. A band-reject has two independent passband branches.
    if filter_type == "Lowpass":
        st.session_state["report_passbands"] = [("Passband", None, real_fc)]
    elif filter_type == "Highpass":
        st.session_state["report_passbands"] = [("Passband", real_fc, None)]
    elif filter_type == "Band-Reject":
        st.session_state["report_passbands"] = [("Lower passband", None, _lo),
                                                ("Upper passband", _hi, None)]
    else:
        st.session_state["report_passbands"] = [("Passband", _lo, _hi)]

with tab_plots:
    # --- A. MAIN BODE PLOTS ---
    st.markdown("#### Magnitude Response")
    
    col_plot, col_toggles = st.columns([5, 1])
    with col_toggles:
        show_phase = st.checkbox("Phase")
        show_gd = st.checkbox("Group Delay")
        
    with col_plot:
        if engine_results is not None and filter_type in ["Lowpass", "Highpass", "Bandpass", "Band-Reject"]:
            
            # 1. Main Magnitude
            if filter_type == "Bandpass":
                fig_mag = plot_main_magnitude(
                    engine_results=engine_results, f_corner_ui=f1_val, freq_unit=freq_unit, multiplier=multiplier,
                    alpha_max=final_alpha, as_db=final_as_hp, filter_type=filter_type, target_gain_units=final_gain_units,
                    f2_corner_ui=f2_val, as_db_2=final_as_lp
                )
            elif filter_type == "Band-Reject":
                fig_mag = plot_main_magnitude(
                    engine_results=engine_results, f_corner_ui=f1_val, freq_unit=freq_unit, multiplier=multiplier,
                    alpha_max=final_alpha, as_db=final_as_lp, filter_type=filter_type, target_gain_units=final_gain_units,
                    f2_corner_ui=f2_val 
                )
            elif filter_type == "Highpass":
                fig_mag = plot_main_magnitude(
                    engine_results=engine_results, f_corner_ui=f1_val, freq_unit=freq_unit, multiplier=multiplier,
                    alpha_max=final_alpha, as_db=final_as_hp, filter_type=filter_type, target_gain_units=final_gain_units
                )
            else: # Lowpass
                fig_mag = plot_main_magnitude(
                    engine_results=engine_results, f_corner_ui=f1_val, freq_unit=freq_unit, multiplier=multiplier,
                    alpha_max=final_alpha, as_db=final_as_lp, filter_type=filter_type, target_gain_units=final_gain_units
                )
            st.plotly_chart(fig_mag, use_container_width=True)
            
            # 2. Passband Detail
            if filter_type == "Band-Reject":
                col_pb1, col_pb2 = st.columns(2)
                with col_pb1:
                    st.markdown("#### Lower Passband")
                    fig_pb_lower = plot_passband_magnitude(
                        engine_results, f1_val, freq_unit, multiplier, final_alpha, final_gain_units, filter_type="Band-Reject-Lower"
                    )
                    st.plotly_chart(fig_pb_lower, use_container_width=True)
                with col_pb2:
                    st.markdown("#### Upper Passband")
                    fig_pb_upper = plot_passband_magnitude(
                        engine_results, f1_val, freq_unit, multiplier, final_alpha, final_gain_units, filter_type="Band-Reject-Upper", f2_corner_ui=f2_val
                    )
                    st.plotly_chart(fig_pb_upper, use_container_width=True)
            else:
                st.markdown("#### Passband Detail")
                fig_pb = plot_passband_magnitude(
                    engine_results=engine_results, f_corner_ui=f1_val, freq_unit=freq_unit, multiplier=multiplier,
                    alpha_max=final_alpha, target_gain_units=final_gain_units, filter_type=filter_type, f2_corner_ui=f2_val
                )
                st.plotly_chart(fig_pb, use_container_width=True)
            
            # 3. Phase / Group Delay (Conditional)
            if show_phase or show_gd:
                st.markdown("#### Phase & Group Delay")
                fig_phase_gd = plot_phase_delay(
                    engine_results=engine_results, f_corner_ui=f1_val, freq_unit=freq_unit, 
                    multiplier=multiplier, show_phase=show_phase, show_gd=show_gd
                )
                st.plotly_chart(fig_phase_gd, use_container_width=True)
        else:
            st.info("[ Placeholder for Interactive Magnitude Plots ]", icon="📈")
        
    st.markdown("---")
    
    # --- A2. READ-ONLY ENGINE OUTPUTS ---
    if engine_results is not None:
        if filter_type in ["Lowpass", "Highpass"]:
            sb_status = engine_results.get('sb_status', 'normal')
            if sb_status == 'corrupted':
                st.error("**Stopband Attenuation does not meet the requirements** (Asymptote exceeds limit)")
            else:
                fs_ui = engine_results['f_stop_hz'] / multiplier
                if sb_status == 'degraded':
                    st.warning(f"**Calculated Stopband Edge ($f_s$):** {fs_ui:,.4f} {freq_unit} (Extended due to manual notch placement)")
                else:
                    st.success(f"**Calculated Stopband Edge ($f_s$):** {fs_ui:,.4f} {freq_unit}")
                    
        elif filter_type == "Bandpass":
            stat_hp = engine_results.get('sb_status_hp', 'normal')
            stat_lp = engine_results.get('sb_status_lp', 'normal')
            
            is_hp_corr = stat_hp.startswith('corrupted')
            is_lp_corr = stat_lp.startswith('corrupted')
            
            if is_hp_corr or is_lp_corr:
                bads = [s for s, t in zip(["Lower Stopband", "Upper Stopband"], [is_hp_corr, is_lp_corr]) if t]
                st.error(f"**Stopband Attenuation does not meet requirements** ({' & '.join(bads)} exceeds limit)")
            
            fs_hp_ui = engine_results['f_stop_hp_hz'] / multiplier if engine_results['f_stop_hp_hz'] else 0.0
            fs_lp_ui = engine_results['f_stop_lp_hz'] / multiplier if engine_results['f_stop_lp_hz'] else 0.0
            
            def format_msg(stat, fs_val, label):
                if stat.startswith('corrupted'):
                    val = stat.split('_')[1] if '_' in stat else "??"
                    return f"{fs_val:,.4f} {freq_unit} (Corrupted A_s{label}={val}dB)"
                elif stat == 'degraded':
                    return f"{fs_val:,.4f} {freq_unit} (Extended)"
                return f"{fs_val:,.4f} {freq_unit}"
                
            msg_hp = format_msg(stat_hp, fs_hp_ui, "l")
            msg_lp = format_msg(stat_lp, fs_lp_ui, "u")
            
            if stat_hp == 'degraded' or stat_lp == 'degraded': 
                st.warning(f"**Calculated Stopband Edges:** Lower = {msg_hp}, Upper = {msg_lp}")
            elif not is_hp_corr and not is_lp_corr: 
                st.success(f"**Calculated Stopband Edges:** Lower = {msg_hp}, Upper = {msg_lp}")
            else: 
                st.info(f"**Calculated Stopband Edges:** Lower = {msg_hp}, Upper = {msg_lp}")
            
        elif filter_type == "Band-Reject":
            stat_br = engine_results.get('sb_status_hp', 'normal') 
            
            fs_lp_ui = engine_results['f_stop_hp_hz'] / multiplier if engine_results['f_stop_hp_hz'] else 0.0
            fs_hp_ui = engine_results['f_stop_lp_hz'] / multiplier if engine_results['f_stop_lp_hz'] else 0.0
            
            if stat_br == 'corrupted':
                actual_as = engine_results.get('actual_as_db', 0.0)
                st.error(f"**Stopband Attenuation does not meet requirements**, actual Stopband Gain=-{actual_as:.1f} dB. Stopband Edges (-{actual_as:.1f} dB): Lower = {fs_lp_ui:,.4f} {freq_unit} Upper = {fs_hp_ui:,.4f} {freq_unit}")
            
            elif stat_br == 'degraded': 
                st.warning(f"**Calculated Stopband Edges:** Lower = {fs_lp_ui:,.4f} {freq_unit} (Extended), Upper = {fs_hp_ui:,.4f} {freq_unit} (Extended)")
            elif stat_br != 'corrupted': 
                st.success(f"**Calculated Stopband Edges:** Lower = {fs_lp_ui:,.4f} {freq_unit}, Upper = {fs_hp_ui:,.4f} {freq_unit}")
            else: 
                st.info(f"**Calculated Stopband Edges:** Lower = {fs_lp_ui:,.4f} {freq_unit}, Upper = {fs_hp_ui:,.4f} {freq_unit}")
        
    st.markdown("---")
    
    # --- B. FREQUENCY PROBES ---
    st.markdown("#### Frequency Probes")
    
    def get_probe_text(f_ui):
        if not engine_results: return "Gain: **0.00 dB** | Phase: **0.0°**"
        h = evaluate_h_complex([f_ui * multiplier], engine_results['poles'], engine_results['zeros'], engine_results['k'] * final_gain_units)[0]
        mag = 20 * np.log10(max(abs(h), 1e-12))
        phase = np.degrees(np.angle(h))
        return f"Gain: **{mag:.2f} dB** | Phase: **{phase:.1f}°**"
    
    if filter_type == "Lowpass":
        p1_def = f1_val
        p2_def = f2_val if f2_val is not None else f1_val * 2.0
        p3_def = (f2_val * 2.0) if f2_val is not None else f1_val * 10.0
    elif filter_type == "Highpass":
        p1_def = f1_val
        p2_def = f2_val if f2_val is not None else f1_val * 0.5
        p3_def = (f2_val * 0.5) if f2_val is not None else f1_val * 0.1
    else:
        p1_def = f1_val
        p2_def = f1_val * 1.5
        p3_def = f1_val * 10.0
    
    col_p1, col_p2, col_p3 = st.columns(3)
    
    with col_p1:
        p1_in = st.number_input(f"Probe 1 ({freq_unit})", value=p1_def, key=f"probe_1_{filter_type}", format="%.3f")
        st.caption(get_probe_text(p1_in))
    with col_p2:
        p2_in = st.number_input(f"Probe 2 ({freq_unit})", value=p2_def, key=f"probe_2_{filter_type}", format="%.3f")
        st.caption(get_probe_text(p2_in))
    with col_p3:
        p3_in = st.number_input(f"Probe 3 ({freq_unit})", value=p3_def, key=f"probe_3_{filter_type}", format="%.3f")
        st.caption(get_probe_text(p3_in))
        
    st.markdown("---")

    # --- C. MANUAL NOTCH GRID ---
    if response in ["Elliptic", "Inverse Chebyshev"]:
        st.markdown("#### Manual Notch Tuning")
    else:
        st.markdown("#### Manual Notch Placement")

    if filter_type in ["Lowpass", "Highpass"]:
        if P_eff > 0:
            # Enforce the strict UI-level safety guardrail for Elliptics
            is_elliptic_over_limit = (response == "Elliptic") and (
                (filter_type == "Lowpass" and final_lp_order > 8) or 
                (filter_type == "Highpass" and final_hp_order > 8)
            )
            
            if is_elliptic_over_limit:
                st.warning(f"Manual Notch Tuning for Elliptic {filter_type} filters is safely disabled for Order > 8 to prevent extreme numerical distortion.")
            else:
                for i in range(P_eff):
                    col_pin, col_val, col_unit = st.columns([1, 3, 6])
                    is_pinned = col_pin.checkbox(f"Pin {i}", key=f"pin_notch_{i}")
                    
                    safe_fallback = f2_val if f2_val is not None else (f1_val * 2.0 if filter_type == "Lowpass" else f1_val * 0.5)
                    
                    if is_pinned:
                        initial_val = st.session_state.get(f"val_notch_{i}", st.session_state.get('_last_free_notches', {}).get(i, safe_fallback) if response in ["Elliptic", "Inverse Chebyshev"] else safe_fallback)
                        col_val.number_input(f"Notch {i} freq", value=float(initial_val), format="%.4f", key=f"val_notch_{i}", label_visibility="collapsed")
                    else:
                        if engine_results and response in ["Elliptic", "Inverse Chebyshev"]:
                            display_val = free_notches_display.get(i, 0.0) / multiplier
                            col_val.number_input(f"Notch {i} freq", value=float(display_val), format="%.4f", disabled=True, label_visibility="collapsed")
                        else:
                            col_val.text_input(f"Notch {i} freq", value="∞", disabled=True, label_visibility="collapsed")
                    col_unit.markdown(f"**{freq_unit}**")
        else:
            st.info("Order is too low to support finite transmission zeros.")

    elif filter_type == "Bandpass":
        if response == "Elliptic":
            st.info("Manual Notch Tuning is disabled for Elliptic Bandpass filters to preserve twin equiripple passband integrity.")
        else:
            col_grid_hp, col_grid_lp = st.columns(2)
            
            with col_grid_hp:
                st.markdown("**Lower Stopband Notches**")
                if P_eff_hp > 0:
                    for i in range(P_eff_hp):
                        c_pin, c_val, c_unit = st.columns([1, 2, 2])
                        is_pinned = c_pin.checkbox(f"Pin {i}", key=f"pin_notch_hp_{i}")
                        safe_fb = f1_val * 0.5
                        
                        if is_pinned:
                            init_val = st.session_state.get(f"val_notch_hp_{i}", st.session_state.get('_last_free_notches_hp', {}).get(i, safe_fb) if response in ["Elliptic", "Inverse Chebyshev"] else safe_fb)
                            c_val.number_input(f"Notch HP {i}", value=float(init_val), format="%.4f", key=f"val_notch_hp_{i}", label_visibility="collapsed")
                        else:
                            if engine_results and response in ["Elliptic", "Inverse Chebyshev"]:
                                disp_val = free_notches_display_hp.get(i, 0.0) / multiplier
                                c_val.number_input(f"Notch HP {i}", value=float(disp_val), format="%.4f", disabled=True, label_visibility="collapsed")
                            else:
                                c_val.text_input(f"Notch HP {i}", value="0", disabled=True, label_visibility="collapsed")
                        c_unit.markdown(f"**{freq_unit}**")
                else:
                    st.info("Order too low.")
                    
            with col_grid_lp:
                st.markdown("**Upper Stopband Notches**")
                if P_eff_lp > 0:
                    for i in range(P_eff_lp):
                        c_pin, c_val, c_unit = st.columns([1, 2, 2])
                        is_pinned = c_pin.checkbox(f"Pin {i}", key=f"pin_notch_lp_{i}")
                        safe_fb = f2_val * 2.0
                        
                        if is_pinned:
                            init_val = st.session_state.get(f"val_notch_lp_{i}", st.session_state.get('_last_free_notches_lp', {}).get(i, safe_fb) if response in ["Elliptic", "Inverse Chebyshev"] else safe_fb)
                            c_val.number_input(f"Notch LP {i}", value=float(init_val), format="%.4f", key=f"val_notch_lp_{i}", label_visibility="collapsed")
                        else:
                            if engine_results and response in ["Elliptic", "Inverse Chebyshev"]:
                                disp_val = free_notches_display_lp.get(i, 0.0) / multiplier
                                c_val.number_input(f"Notch LP {i}", value=float(disp_val), format="%.4f", disabled=True, label_visibility="collapsed")
                            else:
                                c_val.text_input(f"Notch LP {i}", value="∞", disabled=True, label_visibility="collapsed")
                        c_unit.markdown(f"**{freq_unit}**")
                else:
                    st.info("Order too low.")

    elif filter_type == "Band-Reject":
        if response == "Elliptic":
            st.info("Manual Notch Tuning is disabled for Elliptic Band-Reject filters to preserve twin equiripple passband integrity. (Coincident Stopband Notches are still supported via the sidebar).")
        else:
            P_tot = (final_lp_order + final_hp_order) // 2
            P_eff_br = P_tot - 1 if sb_roll_lp and response in ["Inverse Chebyshev", "Elliptic"] else P_tot
            
            if P_eff_br > 0:
                for i in range(P_eff_br):
                    col_pin, col_val, col_unit = st.columns([1, 3, 6])
                    is_pinned = col_pin.checkbox(f"Pin {i}", key=f"pin_notch_{i}")
                    safe_fb = np.sqrt(f1_val * f2_val)
                    
                    if is_pinned:
                        init_val = st.session_state.get(f"val_notch_{i}", st.session_state.get('_last_free_notches', {}).get(i, safe_fb) if response in ["Elliptic", "Inverse Chebyshev"] else safe_fb)
                        col_val.number_input(f"Notch {i} freq", value=float(init_val), format="%.4f", key=f"val_notch_{i}", label_visibility="collapsed")
                    else:
                        if engine_results and response in ["Elliptic", "Inverse Chebyshev"]:
                            disp_val = free_notches_display.get(i, 0.0) / multiplier
                            col_val.number_input(f"Notch {i} freq", value=float(disp_val), format="%.4f", disabled=True, label_visibility="collapsed")
                        else:
                            col_val.text_input(f"Notch {i} freq", value="∞", disabled=True, label_visibility="collapsed")
                    col_unit.markdown(f"**{freq_unit}**")
            else:
                st.info("Order is too low to support finite transmission zeros.")
                
# ------------------------------------------------------------
# 3. RENDER TAB: ROOTS & TRANSFER FUNCTION
# ------------------------------------------------------------
with tab_roots:
    if engine_results:
        # --- UI CONTROLS ---
        col_scale, col_units = st.columns([1, 1])
        with col_scale:
            scale_type = st.radio("Domain Scale", ["Normalized", "Denormalized"], horizontal=True, key="scale_roots")
            
        with col_units:
            if scale_type == "Normalized":
                if filter_type in ["Bandpass", "Band-Reject"]:
                    st.info("Passband Center Frequency Normalized to 1 rad/s")
                else:
                    st.info("Passband Corner Frequency Normalized to 1 rad/s")
                map_unit_choice = "rad/s"
            else:
                map_unit_choice = st.radio("Pole-Zero Map Units", ["rad/s", "Hertz"], horizontal=True, key="unit_roots")
                
        st.markdown("---")

        # --- DATA TRANSLATION MATH ---
        p_phys_rad = engine_results['poles']
        z_phys_rad = engine_results['zeros']
        k_phys = engine_results['k']
        
        # Calculate the proper normalization frequency based on topology
        if filter_type in ["Bandpass", "Band-Reject"] and f2_val is not None:
            w_norm = 2 * np.pi * np.sqrt(f1_val * f2_val) * multiplier
        else:
            w_norm = 2 * np.pi * real_fc
        
        if scale_type == "Normalized":
            p_disp = p_phys_rad / w_norm
            z_disp = z_phys_rad / w_norm
            k_disp = k_phys / (w_norm ** (len(p_phys_rad) - len(z_phys_rad)))
            
            table_unit = ""
            map_plot_roots = (p_disp, z_disp)
            map_unit_label = "rad/s"
        else:
            # Denormalized: react to the Hz/rad-s radio AND the passband gain, so the
            # leading coefficient matches the displayed polynomial domain.
            freq_div = (2 * np.pi) if map_unit_choice == "Hertz" else 1.0
            _ddeg = len(p_phys_rad) - len(z_phys_rad)
            p_disp = p_phys_rad / freq_div
            z_disp = z_phys_rad / freq_div
            k_disp = k_phys * final_gain_units / (freq_div ** _ddeg)
            table_unit = "Hz" if map_unit_choice == "Hertz" else "rad/s"
            map_plot_roots = (p_disp, z_disp)
            map_unit_label = table_unit

        # --- POLE-ZERO MAP ---

        # --- POLE-ZERO MAP ---
        st.markdown("#### Pole-Zero Map")
        col_stretch, col_slider = st.columns([1, 2])
        with col_stretch:
            stretch_axis = st.checkbox("Stretch Real Axis", value=False)
        with col_slider:
            if stretch_axis:
                stretch_factor = st.slider("Magnification", min_value=0.25, max_value=10.0, value=1.0, step=0.125, label_visibility="collapsed")
            else:
                stretch_factor = 1.0

        fig_pz = plot_pole_zero_map(map_plot_roots[0], map_plot_roots[1], scale_type, map_unit_label, stretch_factor)
        st.plotly_chart(fig_pz, use_container_width=True, key="pz_map_chart")
        st.markdown("---")

        # --- DATA TABLES ---
        st.markdown("#### Root Locations")
        
        def format_roots_to_df(roots_array, unit_str):
            if len(roots_array) == 0:
                return pd.DataFrame({"Real Part": [], "Imaginary Part (± j)": [], "Unit": []})
            data = []
            for r in sorted(roots_array, key=lambda x: x.imag):
                data.append({
                    "Real Part": f"{r.real:+.6e}",
                    "Imaginary Part (± j)": f"{r.imag:+.6e}",
                    "Unit": unit_str
                })
            return pd.DataFrame(data)

        col_tbl_p, col_tbl_z = st.columns(2)
        with col_tbl_p:
            st.markdown("**Poles**")
            st.dataframe(format_roots_to_df(p_disp, table_unit), hide_index=True, use_container_width=True)
            
        with col_tbl_z:
            st.markdown("**Zeros**")
            st.dataframe(format_roots_to_df(z_disp, table_unit), hide_index=True, use_container_width=True)

        st.markdown("---")
        
        # --- GAIN CONSTANT K ---
        degree_diff = len(p_disp) - len(z_disp)
        _ku = ("Hz" if map_unit_choice == "Hertz" else "rad/s") if scale_type == "Denormalized" else "norm rad/s"
        if degree_diff == 0:   k_unit_str = "(V/V) [Dimensionless]"
        elif degree_diff == 1: k_unit_str = f"({_ku})"
        else:                  k_unit_str = f"({_ku})^{degree_diff}"
            
        st.metric(label="System Gain Constant (K)", value=f"{k_disp:+.6e}", delta=k_unit_str, delta_color="off")
        st.markdown("---")

        # ==========================================================
        # TRANSFER FUNCTION GENERATION
        # ==========================================================
        st.markdown("#### Transfer Function H(s)")
        if scale_type == "Denormalized":
            st.caption(f"*Frequencies expressed in {'Hz' if map_unit_choice == 'Hertz' else 'rad/s'}*")

        # 1. Clean numerical fuzz & build monic arrays
        clean_p = clean_roots(p_disp)
        clean_z = clean_roots(z_disp)
        
        num_monic = np.poly(clean_z).real if len(clean_z) > 0 else np.array([1.0])
        den_monic = np.poly(clean_p).real

        # Define k_latex globally here so all forms can use the beautiful scientific notation
        k_latex = format_latex_val(k_disp, scale_type)

        # --- FORM 1: Expanded (K Outside) ---
        with st.expander("Expanded Form (Isolated Gain Constant)", expanded=True):
            num_latex_1 = poly_to_latex(num_monic, scale_type)
            den_latex_1 = poly_to_latex(den_monic, scale_type)
            
            tf_1 = f"H(s) = {k_latex} \\cdot \\frac{{{num_latex_1}}}{{{den_latex_1}}}"
            st.latex(tf_1)
            st.code(tf_1, language="latex")
            
            st.dataframe(build_coeff_table(num_monic, den_monic, k_disp, scale_type, include_k=True), use_container_width=True)

        # --- FORM 2: Expanded (K Distributed) ---
        with st.expander("Expanded Form (Distributed Gain Constant)", expanded=False):
            num_dist = num_monic * k_disp
            num_latex_2 = poly_to_latex(num_dist, scale_type)
            
            tf_2 = f"H(s) = \\frac{{{num_latex_2}}}{{{den_latex_1}}}"
            st.latex(tf_2)
            st.code(tf_2, language="latex")
            
            st.dataframe(build_coeff_table(num_dist, den_monic, k_disp, scale_type, include_k=False), use_container_width=True)

        # --- FORM 3: Factored (Biquad) Form ---
        with st.expander("Factored Form (Cascaded Biquads)", expanded=False):
            num_factored = roots_to_biquad_latex(clean_z, scale_type)
            den_factored = roots_to_biquad_latex(clean_p, scale_type)
            
            tf_3 = f"H(s) = {k_latex} \\cdot \\frac{{{num_factored}}}{{{den_factored}}}"
            st.latex(tf_3)
            st.code(tf_3, language="latex")
            
            # Reusing the Form 1 Monic table for reference
            st.dataframe(build_coeff_table(num_monic, den_monic, k_disp, scale_type, include_k=True), use_container_width=True)

    else:
        st.info("Run a synthesis setup in the sidebar to generate Roots & Transfer Function data.")

# ------------------------------------------------------------
# 4. RENDER TAB: BIQUAD PAIRING & CASCADING (VISUAL MNEMOSCHEME)
# ------------------------------------------------------------
with tab_pairing:
    if engine_results:
        # --- UI CONTROLS ---
        map_unit_choice = st.radio(
            "Hardware Target Units", ["rad/s", "Hertz"], horizontal=True, key="unit_pair"
        )
        
        # Force Denormalized math for the entire hardware pairing tab!
        scale_type_pair = "Denormalized"
        
        st.markdown("---")
        
        st.markdown("#### Interactive Mnemoscheme: Stage Assignments")

        # --- MANUAL ROUTING MEMORY REGISTERS ---
        if 'manual_routing_active' not in st.session_state:
            st.session_state.manual_routing_active = False
        if 'selected_brick_id' not in st.session_state:
            st.session_state.selected_brick_id = None
        if 'unassigned_zero_ids' not in st.session_state:
            st.session_state.unassigned_zero_ids = []
        if 'incomplete_stage_nums' not in st.session_state:
            st.session_state.incomplete_stage_nums = []
        
        # 1. Run Background Bricks Generation FIRST to see the actual math
        p_bricks, z_bricks = build_stage_bricks(
            engine_results['poles'], engine_results['zeros'], scale_type_pair, 2 * np.pi * real_fc
        )
        
        # 2. Dynamically count real poles
        real_pole_count = sum(1 for p in p_bricks if p['type'] == 'Real')
        
        # 3. Persistent Checkbox State
        if 'absorb_checked' not in st.session_state:
            st.session_state.absorb_checked = False
            
        do_absorb = False
        
        if real_pole_count > 0:
            # Render the checkbox and tie it to our persistent memory
            st.session_state.absorb_checked = st.checkbox(
                "Enable 3rd-Order Sections (Absorb 1st-Order Poles)", 
                value=st.session_state.absorb_checked
            )
            do_absorb = st.session_state.absorb_checked
            st.markdown("---")
        else:
            # If no real poles exist, force absorption to False for the engine
            do_absorb = False

        # Add the checkbox state to the signature so toggling it triggers a recalculation
        current_signature = f"{response}_{final_lp_order}_{real_fc}_{final_alpha}_{final_as_lp}_{len(z_bricks)}_mnemo_{do_absorb}"
        
        # If the physical filter changed OR manual routing is off, run the Auto-Router
        if 'filter_signature' not in st.session_state or st.session_state.filter_signature != current_signature:
            st.session_state.filter_signature = current_signature
            
            st.session_state.manual_routing_active = False
            st.session_state.selected_brick_id = None
            st.session_state.unassigned_zero_ids = []
            st.session_state.incomplete_stage_nums = []
            
            # FIX: Added filter_type
            st.session_state.stage_routing = auto_pair_stages(p_bricks, z_bricks, absorb_1st_order=do_absorb, filter_type=filter_type)
        
        elif not st.session_state.manual_routing_active:
             # FIX: Added filter_type
             st.session_state.stage_routing = auto_pair_stages(p_bricks, z_bricks, absorb_1st_order=do_absorb, filter_type=filter_type)

        # 4. Translate logical bricks back into physical complex coordinates for Plotly
        paired_stages_data = []
        
       # Determine the scaling factor to match the visual map axes
        if map_unit_choice == "Hertz": 
            plot_scale = 2 * np.pi
            map_unit_label_p = "Hz"
        else: 
            plot_scale = 1.0
            map_unit_label_p = "rad/s"

        for stage in st.session_state.stage_routing:
            s_poles, s_zeros = [], []
            
            # Reconstruct Poles (and scale them down to fit the viewport)
            p_brick = next((b for b in p_bricks if b['id'] == stage['pole_id']), None)
            if p_brick:
                s_poles.append(p_brick['root'] / plot_scale)
                if p_brick['type'] == 'Complex Pair': 
                    s_poles.append(np.conj(p_brick['root']) / plot_scale)
                    
            # Reconstruct Absorbed Real Poles
            abs_id = stage.get('absorbed_real_id', 'None')
            if abs_id != 'None':
                abs_brick = next((b for b in p_bricks if b['id'] == abs_id), None)
                if abs_brick: 
                    s_poles.append(abs_brick['root'] / plot_scale)
                
            # Reconstruct Zeros
            for z_id in stage.get('zero_ids', []):
                z_brick = next((b for b in z_bricks if b['id'] == z_id), None)
                if z_brick:
                    s_zeros.append(z_brick['root'] / plot_scale)
                    if z_brick['type'] == 'Complex Pair': 
                        s_zeros.append(np.conj(z_brick['root']) / plot_scale)
                        
            paired_stages_data.append({
                'stage_num': stage['stage_num'], 'poles': s_poles, 'zeros': s_zeros
            })

        # 5. Render the Visual Web & Interactive Controls
        col_title, col_reset = st.columns([3, 1])
        with col_title:
            if st.session_state.manual_routing_active:
                st.warning("⚠️ **Manual Override Active:** Background auto-router is currently bypassed.")
            else:
                st.success("🤖 **Auto-Pair Active:** Optimal pairing calculated by the background engine.")
                
        with col_reset:
            if st.button("🔄 Auto-Pair (Reset)", use_container_width=True):
                st.session_state.manual_routing_active = False
                st.session_state.selected_brick_id = None
                st.rerun()

        # --- FIXED: Isolate the unit labels and axis scaling from tab_roots ---
        if scale_type == "Normalized":
            map_unit_label_p = "rad/s"
        else:
            map_unit_label_p = "Hz" if map_unit_choice == "Hertz" else "rad/s"

        p_base_scaled = engine_results['poles'] / plot_scale
        z_base_scaled = engine_results['zeros'] / plot_scale
        
        fig_mnemo = plot_mnemoscheme_map(
            poles=p_base_scaled, zeros=z_base_scaled, 
            scale_type=scale_type_pair, unit_label=map_unit_label_p, 
            paired_stages=paired_stages_data, stretch_factor=1.0
        )
        
        # Capture the click event from Plotly!
        selection = st.plotly_chart(
            fig_mnemo, use_container_width=True, 
            key="pz_mnemo_chart", on_select="rerun"
        )
        
        # ==========================================================
        # 6. THE CLICK-TO-PAIR STATE MACHINE (Logic Engine)
        # ==========================================================
        if selection and hasattr(selection, 'selection') and selection.selection.points:
            point = selection.selection.points[0]
            click_x, click_y = point['x'], point['y']
            clicked_id, clicked_type = find_clicked_brick(click_x, click_y, p_bricks, z_bricks, plot_scale)
            
            if clicked_id:
                st.session_state.manual_routing_active = True
                
                # --- STATE 1: NOTHING SELECTED YET ---
                if st.session_state.selected_brick_id is None:
                    if clicked_type == 'pole':
                        st.session_state.selected_brick_id = clicked_id
                        st.rerun()
                    else:
                        st.warning("⚠️ Please click a **Pole** first to initiate routing.")
                        
                # --- STATE 2: A POLE IS CURRENTLY SELECTED ---
                else:
                    selected_id = st.session_state.selected_brick_id
                    action_taken = False
                    
                    # ACTION A: Self-Click (Deselect or De-Absorb)
                    if clicked_id == selected_id:
                        # Check if it's an absorbed real pole. If so, free it!
                        for stage in st.session_state.stage_routing:
                            if stage.get('absorbed_real_id') == clicked_id:
                                stage['absorbed_real_id'] = 'None'
                                stage['capacity'] -= 1
                                stage['is_3rd_order'] = False
                                
                                # Create a new standalone 1st-order stage
                                real_p = next(b for b in p_bricks if b['id'] == clicked_id)
                                st.session_state.stage_routing.append({
                                    "pole_id": clicked_id, "w0": real_p['w0'], "q": 0.0,
                                    "absorbed_real_id": "None", "capacity": 1, "zero_ids": [],
                                    "has_zero_pair": False, "is_3rd_order": False
                                })
                                break
                        action_taken = True

                    # ACTION B: Pole-to-Pole (Create 3rd Order Stage)
                    elif clicked_type == 'pole':
                        p1 = next(b for b in p_bricks if b['id'] == selected_id)
                        p2 = next(b for b in p_bricks if b['id'] == clicked_id)
                        
                        # Only allow mixing Real + Complex
                        if p1['type'] != p2['type']:
                            if not do_absorb:
                                st.error("🚫 Cannot merge poles: 'Enable 3rd-Order Sections' is unchecked.")
                            else:
                                complex_id = selected_id if p1['type'] == 'Complex Pair' else clicked_id
                                real_id = selected_id if p1['type'] == 'Real' else clicked_id
                                
                                # Remove Real Pole from its old stage
                                st.session_state.stage_routing = [s for s in st.session_state.stage_routing if s['pole_id'] != real_id]
                                for s in st.session_state.stage_routing:
                                    if s.get('absorbed_real_id') == real_id:
                                        s['absorbed_real_id'] = 'None'
                                        s['capacity'] -= 1
                                        s['is_3rd_order'] = False
                                        
                                # Inject into Complex Stage
                                for s in st.session_state.stage_routing:
                                    if s['pole_id'] == complex_id:
                                        s['absorbed_real_id'] = real_id
                                        s['capacity'] += 1
                                        s['is_3rd_order'] = True
                                        break
                        action_taken = True

                    # ACTION C: Pole-to-Zero (Swap/Assign)
                    elif clicked_type == 'zero':
                        z_target = next(b for b in z_bricks if b['id'] == clicked_id)
                        
                        # Find which stage owns the selected pole
                        target_stage = next((s for s in st.session_state.stage_routing if s['pole_id'] == selected_id or s.get('absorbed_real_id') == selected_id), None)
                                
                        if target_stage:
                            # 1. Steal the zero from its current owner
                            for s in st.session_state.stage_routing:
                                if clicked_id in s['zero_ids']:
                                    s['zero_ids'].remove(clicked_id)
                                    if z_target['type'] == 'Complex Pair':
                                        s['has_zero_pair'] = False
                                        s['capacity'] += 2
                                    else:
                                        s['capacity'] += 1
                            
                            # 2. If assigning a Complex Pair, and the target already has one, kick the old one out
                            if z_target['type'] == 'Complex Pair' and target_stage.get('has_zero_pair'):
                                old_z_id = next((z for z in target_stage['zero_ids'] if 'pair' in z), None)
                                if old_z_id:
                                    target_stage['zero_ids'].remove(old_z_id)
                                    target_stage['capacity'] += 2
                                    target_stage['has_zero_pair'] = False
                                    
                            # 3. Assign the new zero
                            target_stage['zero_ids'].append(clicked_id)
                            if z_target['type'] == 'Complex Pair':
                                target_stage['capacity'] -= 2
                                target_stage['has_zero_pair'] = True
                            else:
                                target_stage['capacity'] -= 1
                                
                        action_taken = True

                    # --- FINAL CLEANUP & RE-INDEXING ---
                    if action_taken:
                        # Re-sort stages by Q to keep hardware sequence logical, then re-number!
                        st.session_state.stage_routing.sort(key=lambda s: s['q'])
                        for idx, s in enumerate(st.session_state.stage_routing):
                            s['stage_num'] = idx + 1
                            
                        st.session_state.selected_brick_id = None
                        st.rerun()

        # UI Feedback for the first click
        if st.session_state.selected_brick_id:
            st.info("📍 **Pole Selected:** Click a zero to assign it, click a real pole to absorb it, or click the pole again to cancel/detach.")
            
        # UI Warning if there are unassigned zeros on the board
        assigned_zero_ids = [z_id for s in st.session_state.stage_routing for z_id in s.get('zero_ids', [])]
        if len(z_bricks) > len(assigned_zero_ids):
            st.error("⚠️ **Pairing Incomplete:** There are floating zeros on the board (drawn in gray). Please assign them to a stage.")

        # ==========================================================
        # 7. STAGE GAIN DISTRIBUTION & HARDWARE SECTIONS
        # ==========================================================
        st.markdown("---")
        st.markdown("#### Hardware Stage Parameters")
        
        # Only run the math if there are no floating zeros!
        if len(z_bricks) == len(assigned_zero_ids):
            
            # --- 7A. Gain Distribution Logic ---
            col_gain_ui, col_gain_info = st.columns([2, 1])
            with col_gain_ui:
                # The "Equalize DC and HF gains" strategy is meaningful only for a
                # Band-Reject (two passbands straddling the stop-band); it appears
                # in 2nd position for BR and is hidden for every other response.
                _dist_opts = ["Distribute Remaining Gain Evenly"]
                if filter_type == "Band-Reject":
                    _dist_opts.append("Equalize DC and HF gains of LP and HP sections")
                _dist_opts += [
                    "Apply Remaining Gain to First Stage",
                    "Apply Remaining Gain to Last Stage",
                ]
                dist_choice = st.radio(
                    "Remaining Gain Distribution",
                    _dist_opts,
                    horizontal=False
                )
            
            # THE FIX: Scale the roots for Hz BEFORE running the Lueder algorithm!
            freq_div = (2 * np.pi) if map_unit_choice == "Hertz" else 1.0
            
            scaled_p_bricks = []
            for b in p_bricks:
                scaled_p_bricks.append({**b, 'root': b['root']/freq_div, 'w0': b['w0']/freq_div})
                
            scaled_z_bricks = []
            for b in z_bricks:
                scaled_z_bricks.append({**b, 'root': b['root']/freq_div, 'w0': b['w0']/freq_div})
                
            degree_diff = len(engine_results['poles']) - len(engine_results['zeros'])
            k_sys_scaled = engine_results['k'] / (freq_div ** degree_diff)
            
            # Run the Lueder algorithm using the perfectly scaled roots!
            raw_stage_ks, k_remainder = compute_stage_gains(
                st.session_state.stage_routing, scaled_p_bricks, scaled_z_bricks, 
                k_sys_scaled, passband_gain_linear=final_gain_units
            )
            
            with col_gain_info:
                st.info(f"**Calculated Remainder:**\n\n{k_remainder:.4e} V/V")
                if dist_choice == "Manual Gain Distribution":
                    st.warning("Manual editing coming soon!")

            final_ks = raw_stage_ks.copy()
            peak_mags = [1.0] * len(final_ks) 
            
            N_stages = len(final_ks)
            if N_stages > 0:
                if dist_choice == "Distribute Remaining Gain Evenly":
                    even_factor = k_remainder ** (1.0 / N_stages) if k_remainder > 0 else 1.0
                    final_ks = [k * even_factor for k in final_ks]
                    peak_mags = [p * even_factor for p in peak_mags]
                elif dist_choice == "Equalize DC and HF gains of LP and HP sections":
                    final_ks, peak_mags = _equalize_dc_hf_ks(
                        st.session_state.stage_routing, scaled_p_bricks, scaled_z_bricks,
                        raw_stage_ks, k_remainder)
                elif dist_choice == "Apply Remaining Gain to First Stage":
                    final_ks[0] *= k_remainder
                    peak_mags[0] *= k_remainder
                elif dist_choice == "Apply Remaining Gain to Last Stage":
                    final_ks[-1] *= k_remainder
                    peak_mags[-1] *= k_remainder

            # --- HANDOFF -> Topology tab: per-section summary in rad/s ---
            raw_ks_radps, k_rem_radps = compute_stage_gains(
                st.session_state.stage_routing, p_bricks, z_bricks,        # RAW bricks = rad/s
                engine_results['k'], passband_gain_linear=final_gain_units)
            
            final_ks_radps = list(raw_ks_radps)
            _N = len(final_ks_radps)
            if _N > 0:
                if dist_choice == "Distribute Remaining Gain Evenly":
                    _f = k_rem_radps ** (1.0 / _N) if k_rem_radps > 0 else 1.0
                    final_ks_radps = [k * _f for k in final_ks_radps]
                elif dist_choice == "Equalize DC and HF gains of LP and HP sections":
                    final_ks_radps, _ = _equalize_dc_hf_ks(
                        st.session_state.stage_routing, p_bricks, z_bricks,
                        raw_ks_radps, k_rem_radps)
                elif dist_choice == "Apply Remaining Gain to First Stage":
                    final_ks_radps[0] *= k_rem_radps
                elif dist_choice == "Apply Remaining Gain to Last Stage":
                    final_ks_radps[-1] *= k_rem_radps
            
            def _brk(bid, bricks):
                return next((b for b in bricks if b['id'] == bid), None)
            
            hw_sections = []
            for _i, _stg in enumerate(st.session_state.stage_routing):
                _pb = _brk(_stg['pole_id'], p_bricks)
                _is_pair = bool(_pb and _pb['type'] == 'Complex Pair')
                _order = (2 if _is_pair else 1) + (1 if _stg.get('absorbed_real_id', 'None') != 'None' else 0)
                _zb = [b for b in (_brk(z, z_bricks) for z in _stg.get('zero_ids', [])) if b]
                _pair_z = next((b for b in _zb if b['type'] == 'Complex Pair'), None)
                _f1 = None
                _ar = _stg.get('absorbed_real_id', 'None')
                if _ar != 'None':
                    _ab = _brk(_ar, p_bricks)
                    if _ab:
                        _f1 = _ab['w0'] / (2 * np.pi)
                hw_sections.append({
                    'stage_num': _stg['stage_num'], 'order': _order,
                    'notch': _pair_z is not None,
                    'has_origin_zero': any(b['type'] == 'Origin' for b in _zb),
                    'n_origin_zeros': sum(1 for b in _zb if b['type'] == 'Origin'),
                    'is_complex_pair': _is_pair,
                    'f0_hz': (_pb['w0'] / (2 * np.pi)) if _pb else None,
                    'Q': _stg['q'],
                    'fz_hz': (_pair_z['w0'] / (2 * np.pi)) if _pair_z is not None else None,
                    'f1_hz': _f1,
                    'K_radps': float(final_ks_radps[_i]),
                })
            
            st.session_state.hw_sections = hw_sections
            st.session_state.hw_filter_type = filter_type   # BR-aware overall readout
            st.session_state.hw_gen = hash(tuple(
                (s['stage_num'], s['order'], s['notch'],
                 round(s['f0_hz'] or 0.0, 6), round(s['Q'], 6), round(s['K_radps'], 9))
                for s in hw_sections)) 
            # --- report snapshot: per-stage roots (rad/s) + design gains ---
            _rep_stages = []
            for _i, _stg in enumerate(st.session_state.stage_routing):
                _sp, _sz = [], []
                _pb2 = _brk(_stg['pole_id'], p_bricks)
                if _pb2:
                    _sp.append(_pb2['root'])
                    if _pb2['type'] == 'Complex Pair':
                        _sp.append(np.conj(_pb2['root']))
                _ar2 = _stg.get('absorbed_real_id', 'None')
                if _ar2 != 'None':
                    _ab2 = _brk(_ar2, p_bricks)
                    if _ab2:
                        _sp.append(_ab2['root'])
                for _zid in _stg.get('zero_ids', []):
                    _zb2 = _brk(_zid, z_bricks)
                    if _zb2:
                        _sz.append(_zb2['root'])
                        if _zb2['type'] == 'Complex Pair':
                            _sz.append(np.conj(_zb2['root']))
                _rep_stages.append({
                    **hw_sections[_i], 'poles': _sp, 'zeros': _sz,
                    'peak_mag': (float(peak_mags[_i])
                                 if _i < len(peak_mags) else None)})
            st.session_state['report_pairing'] = {'stages': _rep_stages}
            
            st.markdown("---")
            
            # --- 7B. Render the Minimalist Sections ---
            st.caption(f"*Frequencies expressed in {map_unit_choice}*")
                
            for idx, stage in enumerate(st.session_state.stage_routing):
                st.markdown(f"##### Section {stage['stage_num']}")
                
                s_k = final_ks[idx]
                s_peak = peak_mags[idx]
                
                # Extract the scaled roots to generate the LaTeX and Table
                s_poles, s_zeros = [], []
                
                p_brick = next((b for b in scaled_p_bricks if b['id'] == stage['pole_id']), None)
                if p_brick:
                    s_poles.append(p_brick['root'])
                    if p_brick['type'] == 'Complex Pair': s_poles.append(np.conj(p_brick['root']))
                        
                wp = 0.0
                abs_id = stage.get('absorbed_real_id', 'None')
                if abs_id != 'None':
                    abs_brick = next((b for b in scaled_p_bricks if b['id'] == abs_id), None)
                    if abs_brick: 
                        s_poles.append(abs_brick['root'])
                        wp = abs_brick['w0']
                    
                wz = 0.0
                for z_id in stage.get('zero_ids', []):
                    z_brick = next((b for b in scaled_z_bricks if b['id'] == z_id), None)
                    if z_brick:
                        s_zeros.append(z_brick['root'])
                        if z_brick['type'] == 'Complex Pair': 
                            s_zeros.append(np.conj(z_brick['root']))
                            wz = z_brick['w0']
                
                # EXPLICIT 2ND-ORDER EXPANSION
                # If the stage is 1st or 2nd order, cleanly expand the polynomials.
                # If it's 3rd order, keep it factored as (s^2 + ...)(s + ...)
                if len(s_zeros) == 0:
                    num_latex = "1"
                elif len(s_zeros) <= 2 or all(abs(z) < 1e-6 for z in s_zeros):
                    num_poly = np.atleast_1d(np.poly(clean_roots(s_zeros)).real)
                    num_latex = poly_to_latex(num_poly, scale_type_pair)
                else:
                    num_latex = roots_to_biquad_latex(clean_roots(s_zeros), scale_type_pair)
                    
                if len(s_poles) == 0:
                    den_latex = "1"
                elif len(s_poles) <= 2 or all(abs(p) < 1e-6 for p in s_poles):
                    den_poly = np.atleast_1d(np.poly(clean_roots(s_poles)).real)
                    den_latex = poly_to_latex(den_poly, scale_type_pair)
                else:
                    den_latex = roots_to_biquad_latex(clean_roots(s_poles), scale_type_pair)
                
                k_latex = format_latex_val(s_k, scale_type_pair)
                tf_latex = f"H_{{{stage['stage_num']}}}(s) = {k_latex} \\cdot \\frac{{{num_latex}}}{{{den_latex}}}"
                
                st.latex(tf_latex)
                st.code(tf_latex, language="latex")
                
               # --- DYNAMIC TABLE PARAMETERS ---
                w0 = stage['w0'] / freq_div
                q = stage['q']
                f_label = "f" if map_unit_choice == "Hertz" else "ω"
                
                # Base dictionary with Gain
                table_data = {
                    "Kᵢ": [f"{s_k:.4e}"]
                }
                
                # Only inject Biquad variables if the stage has complex poles (2nd or 3rd order)
                if len(s_poles) > 1:
                    table_data[f"{f_label}₀"] = [f"{w0:.4e}"]
                    table_data["Q"] = [f"{q:.4f}" if q > 0 else "N/A"]
                    table_data[f"{f_label}z"] = [f"{wz:.4e}" if wz > 0 else "None"]
                
                # Only display the standalone real pole parameter if it's a true 1st or 3rd order section
                if len(s_poles) == 1:
                    table_data[f"{f_label}p (Real)"] = [f"{w0:.4e}" if w0 > 0 else "None"]
                elif len(s_poles) == 3:
                    table_data[f"{f_label}p (Real)"] = [f"{wp:.4e}" if wp > 0 else "None"]
                    
                # Cap it off with the Peak Magnitude
                table_data["Peak Mag (V/V)"] = [f"{s_peak:.4f}"]
                
                st.dataframe(pd.DataFrame(table_data), hide_index=True, use_container_width=False)
                st.markdown("<br>", unsafe_allow_html=True)

with tab_topology:                               # the missing block
    render_topology_tab()

with tab_response:
    render_response_tab()