# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

import contextlib
import math

import streamlit as st
import pandas as pd

import custom_tf
from custom_tf import CUSTOM, MODE_COMPLETE, MODE_PROTOTYPE, GAIN_NORMALIZE, GAIN_AS_ENTERED
from delay_solvers import (
    DELAY_RESPONSES, DELAY_FILTER_TYPES, BESSEL, EQDELAY, DELTA_MIN, DELTA_MAX,
    BP_TRANSLATION, BP_CLASSIC, max_order as delay_max_order,
)

FILTER_TYPES = ["Lowpass", "Highpass", "Bandpass", "Band-Reject"]
UNIT_MULT = {"Hz": 1, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}
RECIPROCAL_UNIT = {"Hz": "s", "kHz": "ms", "MHz": "µs", "GHz": "ns"}   # τ in 1/unit
DELAY_ORDER_MODES = ["Manual", "From specs"]
DELAY_ANCHORS = ["Corner frequency", "Group delay"]
BP_MAPPINGS = ["Delay-preserving (arithmetic)", "Classic (geometric)"]
CRIT_TAU = "Max group delay (latency budget)"
CRIT_FMIN = "Min corner frequency"
CRIT_FLAT = "Flat delay up to f_d"
CRIT_STOP = "Stopband: A_s at f_s"

# ============================================================
# CALLBACK FUNCTIONS (Instant Memory Savers)
# ============================================================
def sync_symmetric():
    val = st.session_state.widget_sym_order
    st.session_state._mem_order = val
    st.session_state._mem_lp = val
    if not st.session_state.get("_hp_custom", False):
        st.session_state._mem_hp = val

def sync_lp():
    val = st.session_state.widget_lp_order
    st.session_state._mem_lp = val
    st.session_state._mem_order = val

def sync_hp():
    st.session_state._mem_hp = st.session_state.widget_hp_order
    st.session_state._hp_custom = True

def sync_fc():
    val = st.session_state.widget_fc
    st.session_state._mem_fc = val
    st.session_state._mem_f1 = val

def sync_f1():
    val = st.session_state.widget_f1
    st.session_state._mem_f1 = val
    st.session_state._mem_fc = val
    if st.session_state._mem_f2 <= val:
        new_f2 = val * 2.0
        st.session_state._mem_f2 = new_f2
        st.session_state.widget_f2 = new_f2 

def sync_f2():
    val = st.session_state.widget_f2
    st.session_state._mem_f2 = val
    if st.session_state._mem_f1 >= val:
        new_f1 = val / 2.0
        st.session_state._mem_f1 = new_f1
        st.session_state._mem_fc = new_f1
        st.session_state.widget_f1 = new_f1

def sync_alpha():
    st.session_state._mem_alpha = st.session_state.widget_alpha

def sync_as_sym():
    val = st.session_state.widget_as_sym
    st.session_state._mem_as = val
    st.session_state._mem_as_lp = val
    if not st.session_state.get("_as_hp_custom", False):
        st.session_state._mem_as_hp = val

def sync_as_lp():
    val = st.session_state.widget_as_lp
    st.session_state._mem_as_lp = val
    st.session_state._mem_as = val

def sync_as_hp():
    st.session_state._mem_as_hp = st.session_state.widget_as_hp
    st.session_state._as_hp_custom = True

def sync_gain():
    st.session_state._mem_gain = st.session_state.widget_gain


def _mem_widget(widget, label, key, default, **kw):
    """A keyed radio / selectbox / number_input / checkbox whose value survives being
    hidden: it is mirrored into session_state['_mem_<key>'] by an on_change callback (the
    same `_mem_*` pattern as the callbacks above). Returns the widget's current value."""
    mem = "_mem_" + key
    if mem not in st.session_state:
        st.session_state[mem] = default

    def _sync():
        st.session_state[mem] = st.session_state[key]

    if widget is st.radio or widget is st.selectbox:
        opts = kw["options"]
        if st.session_state[mem] not in opts:
            st.session_state[mem] = default
        return widget(label, index=opts.index(st.session_state[mem]), key=key,
                      on_change=_sync, **kw)
    cur = st.session_state[mem]
    if "min_value" in kw:
        cur = max(kw["min_value"], cur)
    if "max_value" in kw:
        cur = min(kw["max_value"], cur)
    st.session_state[mem] = cur
    return widget(label, value=cur, key=key, on_change=_sync, **kw)


def design_control(key, variant="input"):
    """FS-001: a keyed container styled as a design-control section — its
    controls change the design result (read-only sections stay plain). The
    look lives in app.py's CSS, which targets the `st-key-dctl_*` /
    `st-key-dcsel_*` classes Streamlit (>= 1.39) puts on keyed containers.
    variant "input" = blue (design inputs), "select" = amber (picking one of
    the solver's results, e.g. the BOM table). `key` must be app-unique.
    Use as a context manager, or hold it and re-enter / call its methods to
    add later elements to the same box."""
    prefix = "dcsel_" if variant == "select" else "dctl_"
    return st.container(key=prefix + key)

# ============================================================
# UI COMPONENT BLOCKS
# ============================================================
def draw_filter_type(response):
    """Filter Type radio. Bessel / Equiripple Delay offer only the types that can have a flat
    delay (Streamlit cannot grey out a single radio option, so the others are left out). The
    choice lives in _mem_filter_type, so it survives the switch between the two option lists
    (each list has its own widget key); an unavailable type falls back to Lowpass."""
    delay = response in DELAY_RESPONSES
    opts = list(DELAY_FILTER_TYPES) if delay else FILTER_TYPES
    key = "widget_filter_type_delay" if delay else "widget_filter_type"
    if st.session_state.get("_mem_filter_type") not in opts:
        st.session_state._mem_filter_type = opts[0]

    def _sync():
        st.session_state._mem_filter_type = st.session_state[key]

    ft = st.radio("Filter Type", opts, index=opts.index(st.session_state._mem_filter_type),
                  key=key, on_change=_sync)
    if delay:
        st.caption(f"{response}: lowpass and bandpass only — a highpass or band-reject "
                   "passband cannot have a flat group delay.")
    return ft


def draw_delay_order_block(response, filter_type):
    """Order block for Bessel / Equiripple Delay (FS-006): typed in, or selected from the
    criteria drawn by draw_delay_block. Returns (lp_order, hp_order, mode, slot); in
    "From specs" mode `slot` is an st.empty() that app.py fills with the selection result,
    and the returned orders are only the fallback used while no criterion is ticked."""
    if "_mem_order" not in st.session_state: st.session_state._mem_order = 4
    st.markdown("### Order Specifications")
    mode = _mem_widget(st.radio, "Order selection", "widget_delay_order_mode",
                       DELAY_ORDER_MODES[0], options=DELAY_ORDER_MODES, horizontal=True)
    n_max = delay_max_order(response, filter_type)
    st.session_state._mem_order = max(1, min(st.session_state._mem_order, n_max))
    slot = None
    if mode == DELAY_ORDER_MODES[0]:
        label = "Prototype order n (BP order = 2n)" if filter_type == "Bandpass" else "Order"
        st.number_input(
            label, min_value=1, max_value=n_max,
            value=st.session_state._mem_order, key="widget_sym_order", on_change=sync_symmetric
        )
    else:
        slot = st.empty()
    n = st.session_state._mem_order
    return n, n, mode, slot


def draw_order_block(response, filter_type):
    if "_mem_order" not in st.session_state: st.session_state._mem_order = 4
    if "_mem_lp" not in st.session_state: st.session_state._mem_lp = 4
    if "_mem_hp" not in st.session_state: st.session_state._mem_hp = 4
    if "_hp_custom" not in st.session_state: st.session_state._hp_custom = False

    # --- DYNAMIC HARD LIMITS ---
    min_sym, max_sym = 1, 20
    min_asym, max_asym = 1, 10
    
    # Base logic: LP and HP are always symmetric in this UI
    allow_asym = filter_type in ["Bandpass", "Band-Reject"]

    # 1. Remove Asymmetric checkbox for specific Band-Reject cases
    if filter_type == "Band-Reject" and response in ["Elliptic", "Chebyshev"]:
        allow_asym = False

    # 2. Assign Specific Min/Max Limits
    if response == "Elliptic":
        min_sym = 2
        max_sym = 15
        min_asym = 1
        max_asym = 10
    elif response == "Inverse Chebyshev":
        min_sym = 1
        max_sym = 20
        min_asym = 1
        max_asym = 10
    elif response == "Chebyshev":
        min_sym = 1
        if filter_type == "Band-Reject":
            max_sym = 16
        else:
            max_sym = 20
        min_asym = 1
        max_asym = 10
    elif response == "Butterworth":
        min_sym = 1
        if filter_type == "Bandpass":
            max_sym = 15
            max_asym = 14
        elif filter_type == "Band-Reject":
            max_sym = 14
            max_asym = 14
        else:
            max_sym = 20

    st.markdown("### Order Specifications")

    if not allow_asym:
        # Pre-clamp memory so Streamlit doesn't crash if the previous value is out of bounds
        st.session_state._mem_order = max(min_sym, min(st.session_state._mem_order, max_sym))
        
        st.number_input(
            "Order", min_value=min_sym, max_value=max_sym, 
            value=st.session_state._mem_order, key="widget_sym_order", on_change=sync_symmetric
        )
        return st.session_state._mem_order, st.session_state._mem_order
    else: 
        is_asym = st.checkbox("Asymmetric", key="is_asym_checkbox")
        if not is_asym:
            st.session_state._mem_order = max(min_sym, min(st.session_state._mem_order, max_sym))
            st.number_input(
                "Order (LP & HP)", min_value=min_sym, max_value=max_sym, 
                value=st.session_state._mem_order, key="widget_sym_order", on_change=sync_symmetric
            )
            return st.session_state._mem_order, st.session_state._mem_order
        else:
            st.session_state._mem_lp = max(min_asym, min(st.session_state._mem_lp, max_asym))
            st.session_state._mem_hp = max(min_asym, min(st.session_state._mem_hp, max_asym))
            
            col1, col2 = st.columns(2)
            with col1:
                st.number_input(
                    "LP Order", min_value=min_asym, max_value=max_asym, 
                    value=st.session_state._mem_lp, key="widget_lp_order", on_change=sync_lp
                )
            with col2:
                st.number_input(
                    "HP Order", min_value=min_asym, max_value=max_asym, 
                    value=st.session_state._mem_hp, key="widget_hp_order", on_change=sync_hp
                )
                
            # Soft warning for the total order cap on Asymmetric Elliptic BP
            if response == "Elliptic" and filter_type == "Bandpass":
                if st.session_state._mem_lp + st.session_state._mem_hp > 15:
                    st.warning("⚠️ Max total order for Asymmetric Elliptic is 15. Extreme orders may cause calculation delays.")

            return st.session_state._mem_lp, st.session_state._mem_hp

def draw_frequency_block(filter_type, response=None):
    """Returns (f1, f2, unit, anchor_info). anchor_info is None except for a Bessel /
    Equiripple Delay lowpass: {"anchor": "corner" | "delay", "tau0_s", "slot"}, where in
    delay mode f1 is only a placeholder (app.py derives the corner from tau0 and writes it
    into `slot`)."""
    if "_mem_fc" not in st.session_state: st.session_state._mem_fc = 1.0
    if "_mem_f1" not in st.session_state: st.session_state._mem_f1 = 1.0
    if "_mem_f2" not in st.session_state: st.session_state._mem_f2 = 2.0
    if "_f2_init" not in st.session_state: st.session_state._f2_init = False
    
    st.markdown("### Frequency Specifications")

    unit = draw_unit_radio()

    if filter_type in ["Lowpass", "Highpass"]:
        anchor_info = None
        if response in DELAY_RESPONSES and filter_type == "Lowpass":
            anchor = _mem_widget(st.radio, "Specify by", "widget_delay_anchor", DELAY_ANCHORS[0],
                                 options=DELAY_ANCHORS, horizontal=True)
            if anchor == DELAY_ANCHORS[1]:
                tau_ui = _mem_widget(
                    st.number_input, f"Group delay τ₀ ({RECIPROCAL_UNIT[unit]})", "widget_tau0",
                    1.0, min_value=1e-6, format="%f",
                    help="Nominal group delay, held exactly: τ(0) for Bessel, the centre of the "
                         "±δ ripple band for Equiripple Delay. The corner (at the passband "
                         "attenuation below) is derived from it and the order.")
                return (st.session_state._mem_fc, None, unit,
                        {"anchor": "delay", "tau0_s": tau_ui / UNIT_MULT[unit], "slot": st.empty()})
            anchor_info = {"anchor": "corner", "tau0_s": None, "slot": None}
        st.number_input(
            "Corner Frequency", 
            value=st.session_state._mem_fc, format="%f", 
            key="widget_fc", on_change=sync_fc
        )
        return st.session_state._mem_fc, None, unit, anchor_info
    else:
        f1, f2 = _draw_band_corners()
        return f1, f2, unit, None


def _draw_band_corners():
    """Lower / Upper Passband Corner inputs (band types); -> (f1, f2) in the sidebar unit."""
    for k, v in (("_mem_fc", 1.0), ("_mem_f1", 1.0), ("_mem_f2", 2.0), ("_f2_init", False)):
        if k not in st.session_state:
            st.session_state[k] = v
    if not st.session_state._f2_init:
        st.session_state._mem_f2 = st.session_state._mem_f1 * 2.0
        st.session_state._f2_init = True
    
    if st.session_state._mem_f2 <= st.session_state._mem_f1:
        st.session_state._mem_f2 = st.session_state._mem_f1 * 2.0

    col1, col2 = st.columns(2)
    with col1:
        st.number_input(
            "Lower Passband Corner", 
            value=st.session_state._mem_f1, format="%f", 
            key="widget_f1", on_change=sync_f1
        )
    with col2:
        st.number_input(
            "Upper Passband Corner", 
            value=st.session_state._mem_f2, format="%f", 
            key="widget_f2", on_change=sync_f2
        )
    return st.session_state._mem_f1, st.session_state._mem_f2


def draw_unit_radio():
    """The frequency Unit radio. Keyless, as it always was: Custom H(s) complete mode draws it
    alone through this same call (same label / options / index -> same widget identity), so
    the choice carries over between the two sidebars."""
    return st.radio("Unit", ["Hz", "kHz", "MHz", "GHz"], horizontal=True, index=1)


def draw_gain_block(heading=True):
    if "_mem_gain" not in st.session_state: st.session_state._mem_gain = 1.0
    if "widget_gain" not in st.session_state: st.session_state.widget_gain = st.session_state._mem_gain

    if heading:
        st.markdown("### System Specifications")
    st.number_input(
        "Passband Gain, V/V",
        min_value=1.0,
        value=max(1.0, st.session_state._mem_gain),
        format="%f",
        key="widget_gain",
        on_change=sync_gain
    )
    return st.session_state._mem_gain

import streamlit as st

import streamlit as st

def draw_ripple_block(response, filter_type):
    st.markdown("### Passband & Stopband Specs")
    
    # 1. DYNAMIC PASSBAND LABEL & DEFAULT
    if response in ["Chebyshev", "Elliptic"]:
        alpha_label = "Passband Ripple α_max (dB)"
        default_alpha = 1.0
    else:
        alpha_label = "Passband Attenuation (dB)"
        default_alpha = 3.0103  # Standard corner attenuation for Butterworth/Inv Cheby
        
    alpha_max = st.number_input(alpha_label, min_value=0.01, max_value=12.0, value=default_alpha, step=0.1)

    as_lp, as_hp = 40.0, 40.0
    
    # 2. STOPBAND ATTENUATION LOGIC
    if response in ["Inverse Chebyshev", "Elliptic"]:
        if filter_type in ["Bandpass", "Band-Reject"]:
            
            if "prev_asym_state" not in st.session_state:
                st.session_state.prev_asym_state = False
            
            current_asym_state = st.session_state.get("is_asym_checkbox", False)
            
            if current_asym_state and not st.session_state.prev_asym_state:
                current_sym = st.session_state.get("sym_as_val", 40.0)
                st.session_state.as_sl_val = current_sym
                st.session_state.as_su_val = current_sym
                st.session_state.prev_asym_state = True
                
            elif not current_asym_state and st.session_state.prev_asym_state:
                current_lower = st.session_state.get("as_sl_val", 40.0)
                st.session_state.sym_as_val = current_lower
                st.session_state.prev_asym_state = False

            if current_asym_state:
                col1, col2 = st.columns(2)
                with col1:
                    as_hp = st.number_input("Lower Stopband A_sl (dB)", min_value=10.0, value=40.0, step=1.0, key="as_sl_val")
                with col2:
                    as_lp = st.number_input("Upper Stopband A_su (dB)", min_value=10.0, value=40.0, step=1.0, key="as_su_val")
            else:
                sym_as = st.number_input("Stopband Attenuation A_s (dB)", min_value=10.0, value=40.0, step=1.0, key="sym_as_val")
                as_lp = sym_as
                as_hp = sym_as
                
        else:
            sym_as = st.number_input("Stopband Attenuation A_s (dB)", min_value=10.0, value=40.0, step=1.0)
            as_lp = sym_as
            as_hp = sym_as
            
    else:
        # Butterworth and Chebyshev generalized Stopband box
        sym_as = st.number_input("Stopband Attenuation A_s (dB)", min_value=10.0, value=40.0, step=1.0)
        as_lp = sym_as
        as_hp = sym_as
        
    return alpha_max, as_lp, as_hp
    
def draw_modifications_block(response, filter_type, lp_order, hp_order):
    pb_even_lp, pb_even_hp = False, False
    sb_roll_lp, sb_roll_hp = False, False

    is_asym = st.session_state.get("is_asym_checkbox", False)

    show_pb = response in ["Chebyshev", "Elliptic"] and filter_type != "Bandpass"
    show_sb = response in ["Inverse Chebyshev", "Elliptic"]

    st.markdown("### Response Modifications")

    if show_pb:
        if filter_type == "Band-Reject" and is_asym:
            if lp_order % 2 == 0:
                pb_even_lp = st.checkbox("Lower Passband Even Ord Mod")
            if hp_order % 2 == 0:
                pb_even_hp = st.checkbox("Upper Passband Even Ord Mod")
        else:
            if lp_order % 2 == 0:
                pb_even = st.checkbox("Passband Even Order Modification")
                pb_even_lp = pb_even
                pb_even_hp = pb_even

    if show_sb:
        if filter_type == "Band-Reject":
            total_order = lp_order + hp_order
            if total_order % 4 == 0:
                sb_roll = st.checkbox("Coincident Stopband Notches")
                sb_roll_lp = sb_roll
                sb_roll_hp = sb_roll
        elif filter_type == "Bandpass" and is_asym:
            sb_roll_hp = st.checkbox("Lower Stopband Rolloff")
            sb_roll_lp = st.checkbox("Upper Stopband Rolloff")
        else:
            sb_roll = st.checkbox("Stopband Rolloff")
            sb_roll_lp = sb_roll
            sb_roll_hp = sb_roll

    # The master switch was removed here.
    return pb_even_lp, pb_even_hp, sb_roll_lp, sb_roll_hp
    

def draw_delay_block(response, filter_type, order_mode, anchor, unit):
    """"Delay Specs" block for Bessel / Equiripple Delay (FS-006; lowpass and bandpass only).
    Returns {"delta", "bp_mapping", "eps", "crit"}: delta = equiripple ripple (fraction), eps =
    the Bessel delay-error tolerance (fraction; also the reference for the flat-band readout),
    crit = the ONE order criterion in SI units (A_s is added by app.py from the Stopband
    Attenuation box)."""
    mult, tu = UNIT_MULT[unit], RECIPROCAL_UNIT[unit]
    spec = {"delta": 0.01, "bp_mapping": BP_TRANSLATION, "eps": 0.01, "crit": {}}
    if not (filter_type == "Bandpass" or response == EQDELAY
            or order_mode == DELAY_ORDER_MODES[1]):
        return spec             # manual Bessel lowpass: nothing to ask
    st.markdown("---")
    st.markdown("### Delay Specs")

    if filter_type == "Bandpass":
        m = _mem_widget(
            st.radio, "Bandpass mapping", "widget_delay_bp_map", BP_MAPPINGS[0], options=BP_MAPPINGS,
            help="Delay-preserving: the lowpass delay shape is moved to the band centre "
                 "(arithmetic symmetry; stays flat up to a fractional bandwidth of ~0.3). "
                 "Classic: the usual geometric bandpass transform; the delay is tilted across "
                 "the band.")
        spec["bp_mapping"] = BP_TRANSLATION if m == BP_MAPPINGS[0] else BP_CLASSIC
    if response == EQDELAY:
        d = _mem_widget(st.number_input, "Delay ripple ±δ (%)", "widget_delay_ripple", 1.0,
                        min_value=DELTA_MIN * 100, max_value=DELTA_MAX * 100, step=0.1,
                        format="%.2f",
                        help="Equal-ripple deviation of the group delay from its nominal "
                             "value, over the flat-delay band.")
        spec["delta"] = d / 100.0

    if order_mode == DELAY_ORDER_MODES[1]:
        crit = {}
        if filter_type == "Lowpass":
            first = CRIT_FMIN if anchor == "delay" else CRIT_TAU
            kind = _mem_widget(
                st.radio, "Order criterion", f"widget_crit_{anchor}", first,
                options=[first, CRIT_FLAT, CRIT_STOP],
                help="The order is chosen from one criterion. "
                     "Max group delay: f_c is held; the LARGEST order whose delay stays within "
                     "the budget. Min corner frequency: τ₀ is held; the smallest order whose "
                     "corner reaches the frequency. Flat delay: the smallest order whose delay "
                     "stays within tolerance up to f_d. Stopband: the smallest order with at "
                     "least A_s (Stopband Attenuation above) at f_s.")
        else:
            kind = CRIT_STOP
            st.markdown("**Order criterion:** stopband A_s at f_s (f_s outside the passband; "
                        "A_s is the Stopband Attenuation above)")
        if kind == CRIT_FMIN:
            v = _mem_widget(st.number_input, f"f_c ≥ ({unit})", "widget_crit_f_min", 0.5,
                            min_value=1e-6, format="%f")
            crit["f_min_hz"] = v * mult
        elif kind == CRIT_TAU:
            v = _mem_widget(st.number_input, f"τ₀ ≤ ({tu})", "widget_crit_tau_max", 0.4,
                            min_value=1e-6, format="%f")
            crit["tau_max_s"] = v / mult
        elif kind == CRIT_FLAT:
            v = _mem_widget(st.number_input, f"f_d ({unit})", "widget_crit_fd", 0.5,
                            min_value=1e-6, format="%f")
            crit["fd_hz"] = v * mult
            if response == BESSEL:
                e = _mem_widget(st.number_input, "Max delay error ε (%)", "widget_crit_eps", 1.0,
                                min_value=0.01, max_value=50.0, step=0.1, format="%.2f",
                                help="Bessel delay only sags: τ(f) ≥ (1 − ε)·τ₀ up to f_d.")
                spec["eps"] = e / 100.0
                crit["eps"] = e / 100.0
            else:
                st.caption("Tolerance: the ±δ ripple above.")
        else:
            v = _mem_widget(st.number_input, f"f_s ({unit})", "widget_crit_fs", 3.0,
                            min_value=1e-6, format="%f")
            crit["fs_hz"] = v * mult
        spec["crit"] = crit
    return spec


def validate_filter_specs(response, filter_type, lp_order, hp_order):
    if response in DELAY_RESPONSES and filter_type not in DELAY_FILTER_TYPES:
        # Guard only: draw_filter_type does not offer these types for the delay responses.
        return False, (f"{filter_type} is not available for {response}: its passband cannot "
                       "have a flat group delay. Use Lowpass or Bandpass.")
    if filter_type == "Band-Reject":
        total_order = lp_order + hp_order
        if total_order % 2 != 0:
            return False, f"Band-Reject filters require an EVEN total order. Your current total is {total_order}."
        if response == "Elliptic" and lp_order != hp_order:
            if lp_order % 2 != 0 or hp_order % 2 != 0:
                return False, "Asymmetric Elliptic Band-Reject requires BOTH LP and HP orders to be even."
            if abs(lp_order - hp_order) > 2:
                diff = abs(lp_order - hp_order)
                return False, f"Asymmetric Elliptic Band-Reject requires the difference between orders to be ≤ 2. Your difference is {diff}."
    return True, "Valid"


# ============================================================
# CUSTOM H(s) (FS-007)
# ============================================================
# Sidebar = the design intent: Custom Transfer Function (mode; right under Response), Scale
# (complete mode), Filter Type (radio in prototype mode; the detected type in complete mode),
# frequencies, gain mode, α / A_s. Editor panel (top of Response Plots, rendered before the
# engine run) = input form, tables, K, paste boxes and diagnostics. The panel state lives in
# the non-widget key _custom_spec (custom_tf.default_spec() layout, JSON-serializable) so it
# survives Response switches. Table cells keep the typed TEXT, so custom_tf can read the typed
# digits for its repeated-root merge (design note §4.3).
#
# Keyed widgets bound to _custom_spec are created WITHOUT a `value=`: their state is seeded
# from the spec when missing (_bind) and mirrored back by on_change. A reset then writes the
# new values into session_state, which Streamlit pushes to the browser (deleting the keys
# did not refresh a widget that keeps its identity).
CUSTOM_MODES = ["Complete H(s)", "Lowpass prototype"]
CUSTOM_GAIN_MODES = ["Normalize", "As entered"]
CUSTOM_FORMS = {"coeff": "Coefficients", "f0q": "Factored (f₀, Q)",
                "ts": "Factored (Tietze–Schenk)", "roots": "Roots (σ, ω)"}
CUSTOM_SCALES = {"normalized": "Normalized (s / ω_n)", "absolute": "Absolute"}
PASTE_ORDERS = ["Descending (MATLAB / numpy)", "Ascending (a₀, a₁, …)"]
PB_DEFS = ["Corners", "Normalized width"]
_K_KEYS = {"f0q": ("custom_K_f0q", "K", "K (constant before the monic factors)"),
           "ts": ("custom_A0", "A0", "A₀ (DC gain)"),
           "roots": ("custom_K_roots", "K", "K (leading constant)")}


def _cspec():
    if "_custom_spec" not in st.session_state:
        st.session_state._custom_spec = custom_tf.default_spec()
    return st.session_state._custom_spec


def _custom_bump():
    """New editor revision: every table rebuilds its base from _custom_spec under a new key."""
    st.session_state._custom_rev = st.session_state.get("_custom_rev", 0) + 1


def _sync_spec(key, blk, fld):
    """on_change: mirror a keyed widget into _custom_spec (before the rerun, like _mem_*)."""
    def _cb():
        sp = _cspec()
        (sp if blk is None else sp.setdefault(blk, {}))[fld] = st.session_state[key]
    return _cb


def _bind(widget, label, key, blk, fld, seed, **kw):
    """A keyed widget bound to _custom_spec[blk][fld]; `seed` converts the spec value into
    the widget's value when the key has no state yet."""
    sp = _cspec()
    if key not in st.session_state:
        st.session_state[key] = seed((sp if blk is None else sp.get(blk, {})).get(fld))
    return widget(label, key=key, on_change=_sync_spec(key, blk, fld), **kw)


def _txt(v):
    c = _cell(v)
    return "" if c is None else c


def draw_custom_mode_block():
    """Right under Response: Complete H(s) / Lowpass prototype (default)."""
    st.markdown("### Custom Transfer Function")
    m = _mem_widget(st.radio, "Mode", "widget_custom_mode", CUSTOM_MODES[1], options=CUSTOM_MODES,
                    horizontal=True,
                    help="Complete H(s): the entered H is the final filter; its type is detected "
                         "from the response and its passband edges are measured at α. Lowpass "
                         "prototype: a normalized lowpass (ω = 1 is its passband edge) mapped to "
                         "the Filter Type at the frequencies below.")
    st.caption("Enter H(s) in the panel at the top of Response Plots.")
    return MODE_COMPLETE if m == CUSTOM_MODES[0] else MODE_PROTOTYPE


def draw_custom_scale_block():
    """Complete mode: Normalized (s / ω_n, ω_n from the Norm. frequency box) or Absolute.
    Tietze–Schenk tables are always normalized."""
    sp = _cspec()
    st.markdown("### Scale")
    scales = list(CUSTOM_SCALES)
    if sp.get("form") == "ts":
        st.radio("Scale", scales, index=0, format_func=CUSTOM_SCALES.get, horizontal=True,
                 disabled=True, key="widget_custom_scale_ts", label_visibility="collapsed")
        st.caption("Tietze–Schenk tables are always normalized.")
        return "normalized"
    v = _bind(st.radio, "Scale", "widget_custom_scale", None, "scale",
              lambda x: x if x in scales else scales[0], options=scales,
              format_func=CUSTOM_SCALES.get, horizontal=True, label_visibility="collapsed")
    sp["scale"] = v
    return v


def draw_custom_type_block():
    """Complete mode: no Filter Type radio -- the type is detected; app.py fills the slot."""
    slot = st.empty()
    slot.caption("Filter Type: detected from H(s)")
    return slot


def _fn_input(label, unit):
    """The normalization / centre frequency (sidebar unit), bound to _custom_spec['f_norm']."""
    v = _bind(st.number_input, f"{label} ({unit})", "widget_custom_fn", None, "f_norm",
              lambda x: float(custom_tf._num(x) or 1.0), min_value=1e-9, format="%g")
    _cspec()["f_norm"] = v
    return v


def draw_custom_frequency_block(scale):
    """Complete mode: the Unit radio, then Norm. frequency (normalized scale) or nothing
    (absolute). -> (unit, edge_slot); app.py writes the measured edges into the slot."""
    st.markdown("### Frequency Specifications")
    unit = draw_unit_radio()
    if scale == "normalized":
        _fn_input("Norm. frequency f_n", unit)
    return unit, st.empty()


def draw_custom_proto_frequency_block(filter_type):
    """Prototype mode: the usual corner (LP/HP) or, for BP/BR, a Passband definition radio:
    Corners (f1, f2) or Normalized width (centre f0 = the norm.-frequency box, Δ = (f2−f1)/f0,
    geometric centre). -> (f1, f2, unit) in the sidebar unit."""
    if filter_type not in ("Bandpass", "Band-Reject"):
        f1, f2, unit, _ = draw_frequency_block(filter_type)
        return f1, f2, unit
    st.markdown("### Frequency Specifications")
    unit = draw_unit_radio()
    d = _mem_widget(st.radio, "Passband definition", "widget_custom_pbdef", PB_DEFS[0],
                    options=PB_DEFS, horizontal=True,
                    help="Corners: the lower and upper passband edges. Normalized width: the "
                         "geometric centre f₀ and Δ = (f₂ − f₁)/f₀, as in handbook "
                         "frequency-transformation examples.")
    if d == PB_DEFS[0]:
        f1, f2 = _draw_band_corners()
        return f1, f2, unit
    f0 = _fn_input("Centre frequency f₀", unit)
    delta = _mem_widget(st.number_input, "Normalized width Δ = (f₂ − f₁) / f₀", "widget_custom_bw",
                        0.5, min_value=1e-6, format="%g")
    h = math.sqrt(1.0 + delta * delta / 4.0)
    f1, f2 = f0 * (h - delta / 2.0), f0 * (h + delta / 2.0)
    st.caption(f"Corners: f₁ = {f1:.6g}, f₂ = {f2:.6g} {unit}")
    return f1, f2, unit


def draw_custom_gain_block():
    """-> (gain_mode, gain V/V | None, slot | None). As entered (default): app.py writes G into
    the slot; G is never written into _mem_gain (a later standard design would inherit it)."""
    st.markdown("### System Specifications")
    g = _mem_widget(st.radio, "Gain", "widget_custom_gain_mode", CUSTOM_GAIN_MODES[1],
                    options=CUSTOM_GAIN_MODES, horizontal=True,
                    help="Normalize: the peak of H becomes the Passband Gain (K / A₀ in the panel "
                         "are recalculated). As entered: the peak gain G of the entered H(s) is "
                         "the passband gain (it may be below 1).")
    if g == CUSTOM_GAIN_MODES[0]:
        return GAIN_NORMALIZE, draw_gain_block(heading=False), None
    return GAIN_AS_ENTERED, None, st.empty()


def draw_custom_ripple_block(mode):
    """-> (alpha, as_lp, as_hp, alpha_slot). Complete: α is the passband-edge level. Prototype:
    α is measured from the prototype and app.py writes it into alpha_slot."""
    st.markdown("### Passband & Stopband Specs")
    slot = None
    if mode == MODE_COMPLETE:
        alpha = _mem_widget(st.number_input, "Passband edge level α (dB below peak)",
                            "widget_custom_alpha", 3.0103, min_value=0.01, max_value=40.0,
                            step=0.1, format="%.4f",
                            help="The passband edges are the outermost frequencies where |H| is "
                                 "α below its peak; the type is detected against it too.")
    else:
        alpha, slot = 3.0103, st.empty()
    a_s = _mem_widget(st.number_input, "Stopband reference A_s (dB, plot only)",
                      "widget_custom_as", 40.0, min_value=1.0, step=1.0,
                      help="Draws the −A_s reference line; the entered H(s) fixes the stopband.")
    return alpha, a_s, a_s, slot


def _cell(v):
    """Spec value -> table text (typed text is kept as is)."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, str):
        return v if v.strip() else None
    return format(float(v), ".15g")


def _col(df, name):
    out = []
    for v in df[name].tolist():
        if v is None or (isinstance(v, float) and v != v) or (isinstance(v, str) and not v.strip()):
            out.append(None)
        else:
            out.append(v)
    return out


def _rows_add(blk, fld, empty):
    _cspec()[blk].setdefault(fld, []).append(empty() if callable(empty) else empty)
    _custom_bump()


def _rows_del(blk, fld):
    lst = _cspec()[blk].get(fld, [])
    if lst:
        lst.pop()
    _custom_bump()


def _coeff_add():
    c = _cspec()["coeff"]
    n = max(len(c.get("num", [])), len(c.get("den", [])))
    c["num"] = list(c.get("num", [])) + [None] * (n + 1 - len(c.get("num", [])))
    c["den"] = list(c.get("den", [])) + [None] * (n + 1 - len(c.get("den", [])))
    _custom_bump()


def _coeff_del():
    c = _cspec()["coeff"]
    n = max(len(c.get("num", [])), len(c.get("den", [])))
    if n > 1:
        c["num"], c["den"] = list(c.get("num", []))[:n - 1], list(c.get("den", []))[:n - 1]
    _custom_bump()


def _editor(name, data, labels, add, delete, disabled=()):
    """One st.data_editor with a fixed row count (no row-selection checkboxes) and ＋ / −
    buttons that add / remove the last row through _custom_spec. Its session state is an edit
    DELTA against the base DataFrame, so the base stays fixed while the editor lives: it is
    rebuilt from _custom_spec only when _custom_rev changes, and the returned table is never
    fed back in as the next base."""
    rev = st.session_state.get("_custom_rev", 0)
    bk = f"_custom_base_{name}"
    if st.session_state.get(bk, (None,))[0] != rev:
        st.session_state[bk] = (rev, pd.DataFrame(data, dtype=object))
    cfg = {c: st.column_config.TextColumn(lbl, disabled=c in disabled) for c, lbl in labels.items()}
    df = st.data_editor(st.session_state[bk][1], key=f"custom_tbl_{name}_{rev}",
                        num_rows="fixed", hide_index=True, use_container_width=True,
                        column_config=cfg)
    b1, b2, _ = st.columns([1, 1, 4])
    b1.button("＋", key=f"custom_add_{name}", on_click=add[0], args=add[1:],
              help="Add a row", use_container_width=True)
    b2.button("−", key=f"custom_del_{name}", on_click=delete[0], args=delete[1:],
              help="Remove the last row", use_container_width=True)
    return df


_RESET_WIDGETS = {"custom_n0": 0, "custom_K_f0q": "1", "custom_K_roots": "1", "custom_A0": "1",
                  "custom_paste_num": "", "custom_paste_den": "", "custom_paste_poles": "",
                  "custom_paste_zeros": ""}


def _custom_reset():
    old, sp = _cspec(), custom_tf.default_spec()
    for k in ("form", "scale", "f_norm", "unit"):
        sp[k] = old.get(k, sp[k])
    st.session_state._custom_spec = sp
    for k, v in _RESET_WIDGETS.items():          # pushed to the browser (see the header note)
        st.session_state[k] = v
    _custom_bump()


def _on_form_change():
    _cspec()["form"] = st.session_state["custom_form"]
    _custom_bump()


def _apply_coeff_paste():
    sp = _cspec()
    order = "desc" if st.session_state.get("custom_paste_order", PASTE_ORDERS[0]) == PASTE_ORDERS[0] else "asc"
    try:
        new = {}
        for part, key in (("num", "custom_paste_num"), ("den", "custom_paste_den")):
            if (st.session_state.get(key) or "").strip():
                new[part] = custom_tf.coeff_tokens(st.session_state[key], order)
        if not new:
            raise custom_tf.CustomTFError("Both paste boxes are empty.")
        sp["coeff"].update(new, order=order)
        st.session_state._custom_paste_msg = ("ok", "Applied to the table.")
        _custom_bump()
    except custom_tf.CustomTFError as e:
        st.session_state._custom_paste_msg = ("error", " ".join(e.messages))


def _apply_roots_paste():
    sp = _cspec()
    try:
        new = {}
        for part, key in (("poles", "custom_paste_poles"), ("zeros", "custom_paste_zeros")):
            if (st.session_state.get(key) or "").strip():
                new[part] = [[format(a, ".15g"), format(b, ".15g")]
                             for a, b in custom_tf.parse_roots_text(st.session_state[key])]
        if not new:
            raise custom_tf.CustomTFError("Both paste boxes are empty.")
        sp["roots"].update(new)
        st.session_state._custom_paste_msg = ("ok", "Applied to the tables.")
        _custom_bump()
    except custom_tf.CustomTFError as e:
        st.session_state._custom_paste_msg = ("error", " ".join(e.messages))


def _paste_msg():
    msg = st.session_state.pop("_custom_paste_msg", None)
    if msg:
        (st.success if msg[0] == "ok" else st.error)(msg[1])


def _k_field(form, gain_mode, container=None):
    """K / A₀: editable (As entered) or a slot app.py fills with the recalculated value,
    greyed out (Normalize). -> (slot, label) | None."""
    key, fld, label = _K_KEYS[form]
    with (container if container is not None else contextlib.nullcontext()):
        if gain_mode == GAIN_NORMALIZE:
            return st.empty(), label
        _bind(st.text_input, label, key, form, fld, _txt)
    return None


def draw_custom_editor(mode, freq_unit, gain_mode):
    """FS-007 editor panel (top of Response Plots, before the engine run).
    -> (spec, diag, k_slot): spec = a copy of _custom_spec for custom_tf; diag = a container
    under the inputs for draw_custom_diagnostics; k_slot = (slot, label) for the greyed K / A₀
    in Normalize mode, else None."""
    import copy
    sp = _cspec()
    proto = mode == MODE_PROTOTYPE
    sp["unit"] = freq_unit
    # Tables not rendered in the previous run lost their edit state (Streamlit drops hidden
    # widgets): rebuild every base from _custom_spec. app.py pops _custom_seen while the
    # Response is not Custom. Column labels (scale / unit) are part of the editor call too.
    sig = (proto, sp.get("scale"), freq_unit)
    if not st.session_state.get("_custom_seen") or st.session_state.get("_custom_sig") != sig:
        _custom_bump()
    st.session_state._custom_seen, st.session_state._custom_sig = True, sig
    k_slot = None

    with design_control("custom"):
        st.markdown("#### Custom Transfer Function H(s)")
        forms = list(CUSTOM_FORMS)
        if "custom_form" not in st.session_state:
            st.session_state["custom_form"] = sp.get("form", "f0q")
        st.radio("Input form", forms, format_func=CUSTOM_FORMS.get, horizontal=True,
                 key="custom_form", on_change=_on_form_change)
        form = sp["form"]
        norm = proto or form == "ts" or sp.get("scale") == "normalized"
        if proto:
            st.caption("Normalized: ω = 1 is the prototype's passband edge.")
        fu = "normalized" if proto else ("× f_n" if norm else freq_unit)

        if form == "coeff":
            blk = sp["coeff"]
            num, den = list(blk.get("num", [])), list(blk.get("den", []))
            n = max(len(num), len(den), 1)
            df = _editor("coeff_nd", {
                "pw": [f"s^{i}" for i in range(n)],
                "num": [_cell(num[i]) if i < len(num) else None for i in range(n)],
                "den": [_cell(den[i]) if i < len(den) else None for i in range(n)]},
                {"pw": "Power", "num": "Numerator N", "den": "Denominator D"},
                (_coeff_add,), (_coeff_del,), disabled=("pw",))
            blk["num"], blk["den"] = _col(df, "num"), _col(df, "den")
            st.caption("Row i holds the coefficient of sⁱ (row 0 = the constant term). "
                       + ("s is s / ω_n." if norm else "s in rad/s.")
                       + " The gain is implicit: k = b_m / a_n.")
            with st.expander("Paste coefficients"):
                p1, p2 = st.columns(2)
                p1.text_area("Numerator N", key="custom_paste_num", height=68, placeholder="1")
                p2.text_area("Denominator D", key="custom_paste_den", height=68,
                             placeholder="1 2.61313 3.41421 2.61313 1")
                st.radio("Order", PASTE_ORDERS, horizontal=True, key="custom_paste_order")
                st.button("Apply", key="custom_paste_apply", on_click=_apply_coeff_paste)
                _paste_msg()

        elif form == "f0q":
            blk = sp["f0q"]
            c1, c2 = st.columns([3, 2])
            with c1:
                st.markdown("**Pole pairs** — s² + (ω₀/Q)·s + ω₀²")
                pp = blk.get("pole_pairs", [])
                df = _editor("f0q_pp", {"f0": [_cell(r.get("f0")) for r in pp],
                                        "Q": [_cell(r.get("Q")) for r in pp]},
                             {"f0": f"f₀ ({fu})", "Q": "Q"},
                             (_rows_add, "f0q", "pole_pairs", {"f0": None, "Q": None}),
                             (_rows_del, "f0q", "pole_pairs"))
                blk["pole_pairs"] = [{"f0": a, "Q": b} for a, b in zip(_col(df, "f0"), _col(df, "Q"))]
                st.markdown("**Zero pairs** — Q_z blank = jω pair (notch)")
                zp = blk.get("zero_pairs", [])
                df = _editor("f0q_zp", {"fz": [_cell(r.get("fz")) for r in zp],
                                        "Qz": [_cell(r.get("Qz")) for r in zp]},
                             {"fz": f"f_z ({fu})", "Qz": "Q_z (blank = jω)"},
                             (_rows_add, "f0q", "zero_pairs", {"fz": None, "Qz": None}),
                             (_rows_del, "f0q", "zero_pairs"))
                blk["zero_pairs"] = [{"fz": a, "Qz": b} for a, b in zip(_col(df, "fz"), _col(df, "Qz"))]
            with c2:
                st.markdown("**Real poles** — at −ω")
                df = _editor("f0q_rp", {"f": [_cell(v) for v in blk.get("real_poles", [])]},
                             {"f": f"f ({fu})"}, (_rows_add, "f0q", "real_poles", None),
                             (_rows_del, "f0q", "real_poles"))
                blk["real_poles"] = _col(df, "f")
                st.markdown("**Real zeros** — σ_z, + = RHP")
                df = _editor("f0q_rz", {"s": [_cell(v) for v in blk.get("real_zeros", [])]},
                             {"s": f"σ_z ({fu})"}, (_rows_add, "f0q", "real_zeros", None),
                             (_rows_del, "f0q", "real_zeros"))
                blk["real_zeros"] = _col(df, "s")
            k1, k2 = st.columns(2)
            if not proto:          # a lowpass prototype has no origin zeros
                with k1:
                    _bind(st.number_input, "Zeros at the origin n₀", "custom_n0", "f0q", "n_origin",
                          lambda x: int(custom_tf._num(x) or 0), min_value=0, max_value=60, step=1)
            k_slot = _k_field("f0q", gain_mode, k2)

        elif form == "ts":
            blk = sp["ts"]
            st.markdown("**Stages** — H(s_n) = A₀ / ∏(1 + aᵢ·s_n + bᵢ·s_n²); b = 0 is a real pole")
            rows = blk.get("stages", [])
            df = _editor("ts_ab", {"a": [_cell(r.get("a")) for r in rows],
                                   "b": [_cell(r.get("b")) for r in rows]}, {"a": "aᵢ", "b": "bᵢ"},
                         (_rows_add, "ts", "stages", {"a": None, "b": None}),
                         (_rows_del, "ts", "stages"))
            blk["stages"] = [{"a": a, "b": b} for a, b in zip(_col(df, "a"), _col(df, "b"))]
            k_slot = _k_field("ts", gain_mode)
            st.caption("All-pole tables (Tietze–Schenk, TI *Op Amps for Everyone*). Zeros: use the "
                       "Factored (f₀, Q) form.")

        else:
            blk = sp["roots"]
            ru = "normalized" if norm else "rad/s"
            c1, c2 = st.columns(2)
            for col, part, title in ((c1, "poles", "Poles"), (c2, "zeros", "Zeros")):
                with col:
                    st.markdown(f"**{title}** (σ, ω) — ω > 0 = conjugate pair ({ru})")
                    rows = blk.get(part, [])
                    df = _editor(f"roots_{part}",
                                 {"re": [_cell(r[0]) if len(r) > 0 else None for r in rows],
                                  "im": [_cell(r[1]) if len(r) > 1 else None for r in rows]},
                                 {"re": "σ (Re)", "im": "ω (Im ≥ 0)"},
                                 (_rows_add, "roots", part, [None, None]),
                                 (_rows_del, "roots", part))
                    blk[part] = [[a, b] for a, b in zip(_col(df, "re"), _col(df, "im"))]
            k_slot = _k_field("roots", gain_mode)
            with st.expander("Paste roots"):
                st.caption("One root per line (−0.5+0.866j, MATLAB −0.5000 + 0.8660i, or 're im'). "
                           "Full lists with both conjugates (roots(den) output) are de-duplicated.")
                p1, p2 = st.columns(2)
                p1.text_area("Poles", key="custom_paste_poles", height=90)
                p2.text_area("Zeros", key="custom_paste_zeros", height=90)
                st.button("Apply", key="custom_paste_apply_roots", on_click=_apply_roots_paste)
                _paste_msg()

        st.button("Reset to the example (Butterworth n = 4)", key="custom_reset",
                  on_click=_custom_reset)
        diag = st.container()
    return copy.deepcopy(sp), diag, k_slot


def fill_custom_k(k_slot, res, gain_units):
    """Normalize mode: the K / A₀ that puts the peak at the sidebar Passband Gain, greyed out."""
    if not k_slot:
        return
    slot, label = k_slot
    info = res.get("info") or {}
    kv, g = info.get("k_entered"), info.get("peak_gain")
    txt = f"{gain_units * abs(kv) / g:.6g}" if (kv and g and not res.get("errors")) else "—"
    slot.text_input(label, value=txt, disabled=True,
                    help="Normalize: recalculated so the peak of H(s) equals the Passband Gain. "
                         "Switch Gain to As entered to type it.")


def draw_custom_diagnostics(res, filter_type, mode, freq_unit):
    """The panel's diagnostics (design note §9.2): counts, detected type, edges, peak gain,
    normalization readout, conditioning, merges, gate errors, warnings, pre-flight."""
    mult = UNIT_MULT[freq_unit]
    info = res.get("info") or {}

    def fz(v):
        if v is None:
            return "—"
        if v == 0:
            return "DC"
        if v == float("inf"):
            return "∞"
        return f"{v / mult:.6g} {freq_unit}"

    if "n_poles" in info:
        line = (f"**{info['n_poles']} poles** ({info['n_real_poles']} real) · "
                f"**{info['n_zeros']} finite zeros** ({info['n_origin_zeros']} at the origin, "
                f"{info['n_jw_pairs']} jω pairs)")
        if info.get("n_merged_clusters"):
            line += f" · {info['n_merged_clusters']} repeated-root cluster(s) merged"
        st.markdown(line)
    if res.get("errors"):
        st.error("**This H(s) cannot be used:**\n\n" + "\n".join(f"- {e}" for e in res["errors"]))
        if info.get("detected") and filter_type and info["detected"] != filter_type:
            st.caption(f"Detected type: {info['detected']} (Filter Type: {filter_type}).")
        return
    e1, e2 = info.get("edges_hz") or (None, None)
    edges = fz(e1) if e2 is None else f"{e1 / mult:.6g} … {e2 / mult:.6g} {freq_unit}"
    g = info.get("peak_gain", 1.0)
    det = info.get("detected", "—")
    parts = [(f"Edges at −{info['alpha_db']:.4g} dB: **{edges}**" if mode == MODE_COMPLETE
              else f"Target edges: **{edges}** · prototype edge α = **{info['proto_edge_db']:.4g} dB**"),
             f"Entered peak gain G = **{g:.6g} V/V** ({20 * math.log10(g):+.2f} dB) at "
             f"{fz(info.get('peak_hz'))}",
             f"Detected: **{det}**" + ("" if (det == filter_type or not filter_type)
                                       else f" (Filter Type: {filter_type})")]
    if info.get("conditioning_db") is not None:
        parts.append(f"Coefficient conditioning: {info['conditioning_db']:.2g} dB")
    st.markdown(" · ".join(parts))
    if info.get("h_j1_db") is not None:
        r, h1 = info.get("f3db_ratio"), info["h_j1_db"]
        st.caption(f"|H(jω_n)| = {(f'{h1:+.4g}' if math.isfinite(h1) else '−∞')} dB re the peak"
                   + (f"; f₋₃dB / f_n = {r:.5g}" if r else "")
                   + ". Handbooks disagree on what ω = 1 means (−3 dB, ripple edge, unit delay…): "
                     "f_n must be the table's reference frequency, not the desired corner.")
    if mode == MODE_PROTOTYPE and filter_type in ("Bandpass", "Band-Reject"):
        st.caption("The lowpass → bandpass / band-reject mapping does not keep a flat group delay "
                   "(a Bessel prototype loses it).")
    for w in res.get("warnings", []):
        st.warning(w)
    if res.get("preflight"):
        st.warning("**Pairing pre-flight:**\n\n" + "\n".join(f"- {m}" for m in res["preflight"]))
