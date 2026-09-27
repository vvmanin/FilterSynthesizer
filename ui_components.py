# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

import streamlit as st

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
    """A keyed radio / number_input / checkbox whose value survives being hidden: it is
    mirrored into session_state['_mem_<key>'] by an on_change callback (the same `_mem_*`
    pattern as the callbacks above). Returns the widget's current value."""
    mem = "_mem_" + key
    if mem not in st.session_state:
        st.session_state[mem] = default

    def _sync():
        st.session_state[mem] = st.session_state[key]

    if widget is st.radio:
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
    
    unit = st.radio("Unit", ["Hz", "kHz", "MHz", "GHz"], horizontal=True, index=1)
    
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
        return st.session_state._mem_f1, st.session_state._mem_f2, unit, None

def draw_gain_block():
    if "_mem_gain" not in st.session_state: st.session_state._mem_gain = 1.0
    if "widget_gain" not in st.session_state: st.session_state.widget_gain = st.session_state._mem_gain
    
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