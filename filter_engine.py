# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

import numpy as np
import streamlit as st
from scipy.optimize import minimize_scalar, root_scalar
from filter_solvers import (
    solve_butterworth_lp, 
    solve_chebyshev_lp, 
    solve_inv_chebyshev_lp, 
    solve_elliptic_lp,
    synthesize_bgb, 
    synthesize_cheby1_bp_arbitrated, 
    synthesize_slot_based_inv_cheby, 
    synthesize_slot_based_elliptic_bp,
    synthesize_butterworth_gbr,
    synthesize_cheby1_gbr,
    synthesize_invcheby_gbr,
    synthesize_elliptic_gbr,
    find_crossings_br
)

def calculate_physical_gain_lp(physical_zeros, physical_poles, target_dc_db=0.0):
    """
    Post-Facto Gain Normalization for Lowpass Filters.
    Evaluates the unscaled roots at DC (s=0) and calculates the exact K required.
    """
    target_mag = 10 ** (target_dc_db / 20.0)
    
    # Evaluate at s = 0 (DC)
    num = np.prod(-physical_zeros) if len(physical_zeros) > 0 else 1.0 + 0j
    den = np.prod(-physical_poles)
    
    unscaled_dc_gain = np.abs(num / den)
    
    k_phys = target_mag / unscaled_dc_gain
    return float(k_phys)

def analyze_stopband_compliance(zeros_phys, poles_phys, k_phys, as_db, fc_hz):
    """
    Scans the stopband humps and HF asymptote to ensure the -As target is still met.
    """
    from scipy.optimize import minimize_scalar, root_scalar
    target_limit_db = -as_db

    def mag_db(f_hz):
        w = 2 * np.pi * f_hz
        jw = 1j * w
        num = k_phys * np.prod(jw - zeros_phys) if len(zeros_phys) > 0 else k_phys + 0j
        den = np.prod(jw - poles_phys)
        return 20 * np.log10(max(abs(num / den), 1e-12))
        
    def safe_minimize(f_min, f_max):
        if f_max <= f_min + 1e-5: return None
        try:
            return minimize_scalar(lambda f: -mag_db(f), bounds=(f_min, f_max), method='bounded')
        except ValueError:
            return None

    notches_hz = sorted([abs(z.imag)/(2*np.pi) for z in zeros_phys if abs(z.real) < 1e-6 and z.imag > 1e-6])
    
    hf_f = max(notches_hz) * 1e4 if notches_hz else fc_hz * 1e4
    if mag_db(hf_f) > target_limit_db + 1e-4:
        return None, 'corrupted'
        
    status = 'normal'
    violating_humps = []
    
    if len(notches_hz) > 0:
        for i in range(len(notches_hz) - 1):
            gap = notches_hz[i+1] - notches_hz[i]
            if gap < 1e-3: continue
            
            res = safe_minimize(notches_hz[i] + gap * 0.01, notches_hz[i+1] - gap * 0.01)
            if res and -res.fun > target_limit_db + 1e-4:
                status = 'degraded'
                violating_humps.append(res.x)
                
        res = safe_minimize(notches_hz[-1] * 1.001, notches_hz[-1] * 1000.0)
        if res and -res.fun > target_limit_db + 1e-4:
            status = 'degraded'
            violating_humps.append(res.x)
            
    if status == 'degraded' and violating_humps:
        f_start = violating_humps[-1]
        f_end = f_start * 1.1
        while mag_db(f_end) > target_limit_db and f_end < 1e12:
            f_end *= 2.0
        try:
            sol = root_scalar(lambda f: mag_db(f) - target_limit_db, bracket=[f_start, f_end], method='brentq')
            return sol.root, 'degraded'
        except ValueError:
            return f_start, 'degraded'
        
    return None, 'normal'

#@st.cache_data(max_entries=50)
def synthesize_lowpass(response, order, fc_hz, alpha_max, as_db, 
                       manual_notches_hz=None, pb_even_mod=False, sb_rolloff=False):
    """
    The Traffic Controller for Lowpass Synthesis.
    Translates physical UI frequencies to normalized math, and back again.
    """
    if manual_notches_hz is None:
        manual_notches_hz = {}
        
    # 1. PRE-PROCESSING (Normalize inputs)
    wc = 2 * np.pi * fc_hz
    
    # Convert manual physical notch frequencies (Hz) to normalized rad/s slots
    normalized_slots = {}
    for slot_idx, notch_hz in manual_notches_hz.items():
        normalized_slots[slot_idx] = notch_hz / fc_hz

    # 2. ROUTING TO CORE SOLVERS
    ideal_notches_norm = np.array([])
    
    if response == "Butterworth":
        notch_list = list(normalized_slots.values())
        z_n, p_n, _, ws_n, r_zeros_norm = solve_butterworth_lp(order, alpha_max, as_db, notches=notch_list)
        
    elif response == "Chebyshev":
        notch_list = list(normalized_slots.values())
        z_n, p_n, _, ws_n, r_zeros_norm = solve_chebyshev_lp(order, alpha_max, as_db, notches=notch_list, even_mod=pb_even_mod)
        
    elif response == "Inverse Chebyshev" or response == "Elliptic":
        # Wrap the solvers in an Auto-Nudge try/except to prevent exact-boundary collapse
        try:
            if response == "Inverse Chebyshev":
                z_n, p_n, _, ws_n, ideal_notches_norm, r_zeros_norm = solve_inv_chebyshev_lp(order, alpha_max, as_db, slots=normalized_slots, sb_rolloff=sb_rolloff)
            else:
                z_n, p_n, _, ws_n, ideal_notches_norm, r_zeros_norm = solve_elliptic_lp(order, alpha_max, as_db, slots=normalized_slots, pb_even_mod=pb_even_mod, sb_rolloff=sb_rolloff)
        except Exception as e:
            if "bound" in str(e).lower() or "sign" in str(e).lower():
                # Micro-nudge to prevent bounds collapse without ruining equiripple
                nudged_slots = {k: v * 1.000001 for k, v in normalized_slots.items()}
                if response == "Inverse Chebyshev":
                    z_n, p_n, _, ws_n, ideal_notches_norm, r_zeros_norm = solve_inv_chebyshev_lp(order, alpha_max, as_db, slots=nudged_slots, sb_rolloff=sb_rolloff)
                else:
                    z_n, p_n, _, ws_n, ideal_notches_norm, r_zeros_norm = solve_elliptic_lp(order, alpha_max, as_db, slots=nudged_slots, pb_even_mod=pb_even_mod, sb_rolloff=sb_rolloff)
            else:
                raise e
    else:
        raise ValueError(f"Unknown response type: {response}")

    # 3. POST-PROCESSING (Denormalize outputs)
    # Multiply normalized rad/s roots by wc to get physical rad/s roots
    p_phys = p_n * wc
    z_phys = z_n * wc
    r_zeros_phys = r_zeros_norm * wc  # <--- Scale the reflection zeros!
    
    if ws_n is not None:
        ws_phys_hz = (ws_n * wc) / (2 * np.pi)
    else:
        ws_phys_hz = None
    
    ideal_notches_hz = ideal_notches_norm * fc_hz

    # 4. POST-FACTO GAIN CALCULATION
    target_dc_db = 0.0
    if response in ["Chebyshev", "Elliptic"] and order % 2 == 0 and not pb_even_mod:
        target_dc_db = -alpha_max
        
    k_phys = calculate_physical_gain_lp(z_phys, p_phys, target_dc_db)

    # 5. STOPBAND COMPLIANCE CHECK
    true_fs, sb_status = analyze_stopband_compliance(z_phys, p_phys, k_phys, as_db, fc_hz)
    
    if sb_status == 'degraded':
        ws_phys_hz = true_fs
    elif sb_status == 'corrupted':
        ws_phys_hz = None

    return {
        "poles": p_phys,
        "zeros": z_phys,
        "reflection_zeros": r_zeros_phys,
        "k": k_phys,
        "f_stop_hz": ws_phys_hz,
        "ideal_notches_hz": ideal_notches_hz,
        "sb_status": sb_status
    }

def calculate_physical_gain_hp(target_inf_db=0.0):
    """
    Gain Normalization for Highpass Filters.
    Because the Highpass transfer function has N zeros and N poles, 
    the unscaled gain as s -> infinity is mathematically exactly 1.0.
    """
    k_phys = 10 ** (target_inf_db / 20.0)
    return float(k_phys)

def analyze_stopband_compliance_hp(zeros_phys, poles_phys, k_phys, as_db, fc_hz):
    """
    Scans the stopband humps (between DC and fs) to ensure the -As target is met.
    """
    from scipy.optimize import minimize_scalar, root_scalar
    target_limit_db = -as_db

    def mag_db(f_hz):
        if f_hz < 1e-6: return -200.0 # DC is blocked
        w = 2 * np.pi * f_hz
        jw = 1j * w
        num = k_phys * np.prod(jw - zeros_phys) if len(zeros_phys) > 0 else k_phys + 0j
        den = np.prod(jw - poles_phys)
        return 20 * np.log10(max(abs(num / den), 1e-12))
        
    def safe_minimize(f_min, f_max):
        if f_max <= f_min + 1e-5: return None
        try:
            return minimize_scalar(lambda f: -mag_db(f), bounds=(f_min, f_max), method='bounded')
        except ValueError:
            return None
        
    notches_hz = sorted([abs(z.imag)/(2*np.pi) for z in zeros_phys if abs(z.real) < 1e-6 and z.imag > 1e-6])
    
    status = 'normal'
    violating_humps = []
    
    if len(notches_hz) > 0:
        res = safe_minimize(1e-3, notches_hz[0] - 1e-3)
        if res and -res.fun > target_limit_db + 1e-4:
            status = 'degraded'
            violating_humps.append(res.x)
                
        for i in range(len(notches_hz) - 1):
            gap = notches_hz[i+1] - notches_hz[i]
            if gap < 1e-3: continue
            
            res = safe_minimize(notches_hz[i] + gap * 0.01, notches_hz[i+1] - gap * 0.01)
            if res and -res.fun > target_limit_db + 1e-4:
                status = 'degraded'
                violating_humps.append(res.x)
                
    if status == 'degraded' and violating_humps:
        f_start = violating_humps[0] 
        f_end = f_start * 0.9
        
        while mag_db(f_end) > target_limit_db and f_end > 1e-6:
            f_end *= 0.5
            
        if f_end <= 1e-6:
            return None, 'corrupted' 
            
        try:
            sol = root_scalar(lambda f: mag_db(f) - target_limit_db, bracket=[f_end, f_start], method='brentq')
            return sol.root, 'degraded'
        except ValueError:
            return f_start, 'degraded'
        
    return None, 'normal'

#@st.cache_data(max_entries=50)
def synthesize_highpass(response, order, fc_hz, alpha_max, as_db, 
                        manual_notches_hz=None, pb_even_mod=False, sb_rolloff=False):
    """
    The Traffic Controller for Highpass Synthesis.
    Wraps the Lowpass prototype solvers using the s -> 1/s spectral transformation.
    """
    if manual_notches_hz is None:
        manual_notches_hz = {}
        
    wc = 2 * np.pi * fc_hz
    
    # 1. PRE-PROCESSING: Convert HP physical slots to inverted LP normalized slots
    normalized_slots_lp = {}
    for slot_idx, notch_hz in manual_notches_hz.items():
        if notch_hz > 1e-6: # Prevent div by zero
            norm_hp = notch_hz / fc_hz
            normalized_slots_lp[slot_idx] = 1.0 / norm_hp # Invert for LP solver!

    # 2. ROUTE TO LOWPASS CORE SOLVERS
    ideal_notches_norm_lp = np.array([])
    
    if response == "Butterworth":
        notch_list = list(normalized_slots_lp.values())
        z_n, p_n, _, ws_n_lp, _ = solve_butterworth_lp(order, alpha_max, as_db, notches=notch_list)
    elif response == "Chebyshev":
        notch_list = list(normalized_slots_lp.values())
        z_n, p_n, _, ws_n_lp, _ = solve_chebyshev_lp(order, alpha_max, as_db, notches=notch_list, even_mod=pb_even_mod)
    elif response == "Inverse Chebyshev" or response == "Elliptic":
        try:
            if response == "Inverse Chebyshev":
                z_n, p_n, _, ws_n_lp, ideal_notches_norm_lp, _ = solve_inv_chebyshev_lp(order, alpha_max, as_db, slots=normalized_slots_lp, sb_rolloff=sb_rolloff)
            else:
                z_n, p_n, _, ws_n_lp, ideal_notches_norm_lp, _ = solve_elliptic_lp(order, alpha_max, as_db, slots=normalized_slots_lp, pb_even_mod=pb_even_mod, sb_rolloff=sb_rolloff)
        except Exception as e:
            if "bound" in str(e).lower() or "sign" in str(e).lower():
                # Micro-nudge to prevent bounds collapse without ruining equiripple
                nudged_slots = {k: v * 1.000001 for k, v in normalized_slots_lp.items()}
                if response == "Inverse Chebyshev":
                    z_n, p_n, _, ws_n_lp, ideal_notches_norm_lp, _ = solve_inv_chebyshev_lp(order, alpha_max, as_db, slots=nudged_slots, sb_rolloff=sb_rolloff)
                else:
                    z_n, p_n, _, ws_n_lp, ideal_notches_norm_lp, _ = solve_elliptic_lp(order, alpha_max, as_db, slots=nudged_slots, pb_even_mod=pb_even_mod, sb_rolloff=sb_rolloff)
            else:
                raise e
                
    # 3. SPECTRAL TRANSFORMATION (s -> 1/s)
    p_hp_norm = 1.0 / p_n
    z_hp_norm = 1.0 / z_n if len(z_n) > 0 else np.array([], dtype=complex)
    
    # Calculate how many origin zeros are required to block DC
    num_origin_zeros = order - len(z_hp_norm)
    if num_origin_zeros > 0:
        origin_zeros = np.zeros(num_origin_zeros, dtype=complex)
        z_hp_norm = np.concatenate((z_hp_norm, origin_zeros))

    # Scale to physical frequencies
    p_phys = p_hp_norm * wc
    z_phys = z_hp_norm * wc
    
    # Transform boundary frequencies
    ws_phys_hz = fc_hz / ws_n_lp if ws_n_lp is not None else None
    ideal_notches_hp_hz = fc_hz / ideal_notches_norm_lp if len(ideal_notches_norm_lp) > 0 else np.array([])

    # 4. GAIN CALCULATION
    target_inf_db = 0.0
    if response in ["Chebyshev", "Elliptic"] and order % 2 == 0 and not pb_even_mod:
        target_inf_db = -alpha_max
        
    k_phys = calculate_physical_gain_hp(target_inf_db)

    # 5. STOPBAND COMPLIANCE CHECK
    true_fs, sb_status = analyze_stopband_compliance_hp(z_phys, p_phys, k_phys, as_db, fc_hz)
    
    if sb_status == 'degraded':
        ws_phys_hz = true_fs
    elif sb_status == 'corrupted':
        ws_phys_hz = None

    return {
        "poles": p_phys,
        "zeros": z_phys,
        "k": k_phys,
        "f_stop_hz": ws_phys_hz,
        "ideal_notches_hz": ideal_notches_hp_hz,
        "sb_status": sb_status
    }

def analyze_stopband_compliance_bp(zeros_phys, poles_phys, k_phys, as_hp_db, as_lp_db, f1_hz, f2_hz):
    from scipy.optimize import minimize_scalar, root_scalar
    import numpy as np
    
    TOL = 0.05 
    
    # 1. Normalize math to completely prevent Float Overflow
    f0_hz = np.sqrt(f1_hz * f2_hz)
    w0 = 2 * np.pi * f0_hz
    
    z_norm = zeros_phys / w0
    p_norm = poles_phys / w0
    k_norm = k_phys * (w0 ** (len(zeros_phys) - len(poles_phys)))
    
    def mag_db(f_hz):
        if f_hz < 1e-6: return -200.0
        fn = f_hz / f0_hz
        jw = 1j * fn
        num = k_norm * np.prod(jw - z_norm) if len(z_norm) > 0 else k_norm + 0j
        den = np.prod(jw - p_norm)
        return 20 * np.log10(max(abs(num / den), 1e-12))
        
    def safe_minimize(f_min, f_max):
        if f_max <= f_min + 1e-5: return None
        try: return minimize_scalar(lambda f: -mag_db(f), bounds=(f_min, f_max), method='bounded')
        except ValueError: return None

    notches_hz = sorted([abs(z.imag)/(2*np.pi) for z in zeros_phys if abs(z.real) < 1e-6 and z.imag > 1e-6])
    
    hp_notches = [f for f in notches_hz if f < f0_hz]
    lp_notches = [f for f in notches_hz if f > f0_hz]
    
    status_hp, status_lp = 'normal', 'normal'
    v_humps_hp, v_humps_lp = [], []
    
    # 2. Track Magnitude AND exact Frequency of the worst violator
    worst_hp_mag = mag_db(1e-6)
    worst_hp_f = 1e-6
    
    hf_f = lp_notches[-1] * 1e4 if len(lp_notches) > 0 else f2_hz * 1e4
    worst_lp_mag = mag_db(hf_f)
    worst_lp_f = hf_f
    
    # 3. Check Lower (HP) Stopband Humps
    if len(hp_notches) > 0:
        res = safe_minimize(1e-3, hp_notches[0] - 1e-3)
        if res:
            if -res.fun > worst_hp_mag: worst_hp_mag = -res.fun; worst_hp_f = res.x
            if -res.fun > -as_hp_db + TOL: status_hp = 'degraded'; v_humps_hp.append(res.x)
        for i in range(len(hp_notches)-1):
            gap = hp_notches[i+1] - hp_notches[i]
            if gap > 1e-3:
                res = safe_minimize(hp_notches[i] + gap*0.01, hp_notches[i+1] - gap*0.01)
                if res:
                    if -res.fun > worst_hp_mag: worst_hp_mag = -res.fun; worst_hp_f = res.x
                    if -res.fun > -as_hp_db + TOL: status_hp = 'degraded'; v_humps_hp.append(res.x)
                    
    # 4. Check Upper (LP) Stopband Humps
    if len(lp_notches) > 0:
        for i in range(len(lp_notches)-1):
            gap = lp_notches[i+1] - lp_notches[i]
            if gap > 1e-3:
                res = safe_minimize(lp_notches[i] + gap*0.01, lp_notches[i+1] - gap*0.01)
                if res:
                    if -res.fun > worst_lp_mag: worst_lp_mag = -res.fun; worst_lp_f = res.x
                    if -res.fun > -as_lp_db + TOL: status_lp = 'degraded'; v_humps_lp.append(res.x)
        res = safe_minimize(lp_notches[-1] * 1.001, lp_notches[-1] * 1000.0)
        if res:
            if -res.fun > worst_lp_mag: worst_lp_mag = -res.fun; worst_lp_f = res.x
            if -res.fun > -as_lp_db + TOL: status_lp = 'degraded'; v_humps_lp.append(res.x)
            
    # 5. Pack the worst dB value into the string if corrupted
    if worst_hp_mag > -as_hp_db + TOL: status_hp = f'corrupted_{abs(worst_hp_mag):.1f}'
    if worst_lp_mag > -as_lp_db + TOL: status_lp = f'corrupted_{abs(worst_lp_mag):.1f}'

    # 6. THE TRUE CORRUPTION FIX (Searching the Skirt with a Safety Margin)
    # If corrupted, our new target is the worst magnitude minus a tiny 0.01 dB safety margin.
    # This guarantees the root finder securely intercepts the main skirt.
    eff_as_hp_db = abs(worst_hp_mag) - 0.01 if worst_hp_mag > -as_hp_db + TOL else as_hp_db
    eff_as_lp_db = abs(worst_lp_mag) - 0.01 if worst_lp_mag > -as_lp_db + TOL else as_lp_db

    # ALWAYS search STRICTLY on the main transition skirt (between passband and first notch)
    f_notch_hp = hp_notches[-1] if hp_notches else f1_hz * 1e-3
    try: ws_hp = root_scalar(lambda f: mag_db(f) - (-eff_as_hp_db), bracket=[f_notch_hp, f1_hz]).root
    except: ws_hp = f_notch_hp
        
    f_notch_lp = lp_notches[0] if lp_notches else f2_hz * 1e3
    try: ws_lp = root_scalar(lambda f: mag_db(f) - (-eff_as_lp_db), bracket=[f2_hz, f_notch_lp]).root
    except: ws_lp = f_notch_lp
        
    # 7. OVERRIDE markers ONLY if strictly "degraded" 
    # (Meaning the skirt is fine, but a hump pushed the boundary outward without violating the target)
    if status_hp == 'degraded' and v_humps_hp:
        f_s = v_humps_hp[0] 
        f_e = f_s * 0.9
        while mag_db(f_e) > -as_hp_db and f_e > 1e-6: f_e *= 0.5
        if f_e > 1e-6:
            try: ws_hp = root_scalar(lambda f: mag_db(f) - (-as_hp_db), bracket=[f_e, f_s]).root
            except: pass
            
    if status_lp == 'degraded' and v_humps_lp:
        f_s = v_humps_lp[-1] 
        f_e = f_s * 1.1
        while mag_db(f_e) > -as_lp_db and f_e < f2_hz * 1e5: f_e *= 2.0
        if f_e < f2_hz * 1e5:
            try: ws_lp = root_scalar(lambda f: mag_db(f) - (-as_lp_db), bracket=[f_s, f_e]).root
            except: pass
        
    return ws_hp, status_hp, ws_lp, status_lp
'''    
    # Recalculate true stopband edges ONLY if genuinely degraded by user modifications
    ws_hp = None
    if status_hp == 'degraded' and v_humps_hp:
        f_s = v_humps_hp[0]
        f_e = f_s * 0.9
        while mag_db(f_e) > -as_hp_db and f_e > 1e-6: f_e *= 0.5
        if f_e <= 1e-6: status_hp = 'corrupted'
        else:
            try: ws_hp = root_scalar(lambda f: mag_db(f) - (-as_hp_db), bracket=[f_e, f_s]).root
            except: ws_hp = f_s
            
    ws_lp = None
    if status_lp == 'degraded' and v_humps_lp:
        f_s = v_humps_lp[-1]
        f_e = f_s * 1.1
        while mag_db(f_e) > -as_lp_db and f_e < 1e12: f_e *= 2.0
        try: ws_lp = root_scalar(lambda f: mag_db(f) - (-as_lp_db), bracket=[f_s, f_e]).root
        except: ws_lp = f_s
        
    return ws_hp, status_hp, ws_lp, status_lp
'''
#@st.cache_data(max_entries=50)
def synthesize_bandpass(response, order_hp, order_lp, f1_hz, f2_hz, 
                        alpha_max, as_hp_db, as_lp_db, 
                        manual_notches_hp_hz=None, manual_notches_lp_hz=None, 
                        pb_even_mod_hp=False, pb_even_mod_lp=False, 
                        sb_rolloff_hp=False, sb_rolloff_lp=False):
    """
    Traffic controller for the direct-synthesis asymmetric Bandpass solvers.
    Uses Geometric Center Normalization to prevent float64 polynomial explosion.
    """
    if manual_notches_hp_hz is None: manual_notches_hp_hz = {}
    if manual_notches_lp_hz is None: manual_notches_lp_hz = {}
    
    # 1. GEOMETRIC NORMALIZATION (The Numerical Shield)
    w1_phys = 2 * np.pi * f1_hz
    w2_phys = 2 * np.pi * f2_hz
    w0_center = np.sqrt(w1_phys * w2_phys) # Geometric center
    
    wp1_norm = w1_phys / w0_center
    wp2_norm = w2_phys / w0_center

    hp_slots_norm = {k: (v * 2 * np.pi) / w0_center for k, v in manual_notches_hp_hz.items()}
    lp_slots_norm = {k: (v * 2 * np.pi) / w0_center for k, v in manual_notches_lp_hz.items()}

    # 2. RUN SOLVERS IN THE STABLE NORMALIZED DOMAIN
    ideal_zhp_norm = np.array([])
    ideal_zlp_norm = np.array([])
    r_zeros_norm = np.array([])

    if response == "Butterworth":
        n_hp = list(hp_slots_norm.values())
        n_lp = list(lp_slots_norm.values())
        poles_n, zeros_n, k_norm, w0_opt_n, ws_hp_n, ws_lp_n = synthesize_bgb(
            order_hp, order_lp, wp1_norm, wp2_norm, alpha_max, as_hp_db, as_lp_db, n_hp, n_lp
        )
        r_zeros_norm = np.array([w0_opt_n]) # Reflection zero at the anchor point
        
    elif response == "Chebyshev":
        n_hp = list(hp_slots_norm.values())
        n_lp = list(lp_slots_norm.values())
        poles_n, zeros_n, k_norm, ws_hp_n, ws_lp_n, opt_z_n = synthesize_cheby1_bp_arbitrated(
            order_hp, order_lp, alpha_max, wp1_norm, wp2_norm, as_hp_db, as_lp_db, n_hp, n_lp
        )
        r_zeros_norm = np.array(opt_z_n) # Extract reflection zeros
        
    elif response == "Inverse Chebyshev":
        try:
            poles_n, zeros_n, k_norm, ideal_zhp_norm, ideal_zlp_norm, _, _, ws_hp_n, ws_lp_n = synthesize_slot_based_inv_cheby(
                order_hp, order_lp, wp1_norm, wp2_norm, as_hp_db, as_lp_db, alpha_max, 
                sb_rolloff_hp, sb_rolloff_lp, hp_slots_norm, lp_slots_norm
            )
        except Exception as e:
            if "bound" in str(e).lower() or "sign" in str(e).lower():
                nudged_hp = {k: v * 0.999999 for k, v in hp_slots_norm.items()} 
                nudged_lp = {k: v * 1.000001 for k, v in lp_slots_norm.items()} 
                poles_n, zeros_n, k_norm, ideal_zhp_norm, ideal_zlp_norm, _, _, ws_hp_n, ws_lp_n = synthesize_slot_based_inv_cheby(
                    order_hp, order_lp, wp1_norm, wp2_norm, as_hp_db, as_lp_db, alpha_max, 
                    sb_rolloff_hp, sb_rolloff_lp, nudged_hp, nudged_lp
                )
            else:
                raise e
        # Inv Cheby is maximally flat in the passband, so all reflection zeros stack at the anchor point
        # For dictionary compatibility, we return empty array, or you can update the solver to return w0
        r_zeros_norm = np.array([]) 

    elif response == "Elliptic":
        try:
            poles_n, zeros_n, k_norm, ideal_zhp_norm, ideal_zlp_norm, _, _, ws_hp_n, ws_lp_n, opt_z_n = synthesize_slot_based_elliptic_bp(
                order_hp, order_lp, wp1_norm, wp2_norm, as_hp_db, as_lp_db, alpha_max, 
                sb_rolloff_hp, sb_rolloff_lp, hp_slots_norm, lp_slots_norm
            )
        except Exception as e:
             if "bound" in str(e).lower() or "sign" in str(e).lower():
                nudged_hp = {k: v * 0.999999 for k, v in hp_slots_norm.items()} 
                nudged_lp = {k: v * 1.000001 for k, v in lp_slots_norm.items()} 
                poles_n, zeros_n, k_norm, ideal_zhp_norm, ideal_zlp_norm, _, _, ws_hp_n, ws_lp_n, opt_z_n = synthesize_slot_based_elliptic_bp(
                    order_hp, order_lp, wp1_norm, wp2_norm, as_hp_db, as_lp_db, alpha_max, 
                    sb_rolloff_hp, sb_rolloff_lp, nudged_hp, nudged_lp
                )
             else:
                raise e
        r_zeros_norm = np.array(opt_z_n) # Extract reflection zeros
    else:
        raise ValueError(f"Unknown response type: {response}")

    # 3. DENORMALIZE BACK TO PHYSICAL FREQUENCIES
    poles_phys = np.array(poles_n) * w0_center
    zeros_phys = np.array(zeros_n) * w0_center
    r_zeros_phys = r_zeros_norm * w0_center
    
    # Scale the Gain Constant K
    # K_phys = K_norm * (w0_center) ^ (degree_poles - degree_zeros)
    degree_diff = len(poles_phys) - len(zeros_phys)
    k_phys = k_norm * (w0_center ** degree_diff)

    ws_hp_hz = ws_hp_n * w0_center / (2 * np.pi) if ws_hp_n is not None else None
    ws_lp_hz = ws_lp_n * w0_center / (2 * np.pi) if ws_lp_n is not None else None
    
    ideal_notches_hp_hz = ideal_zhp_norm * w0_center / (2 * np.pi) if len(ideal_zhp_norm) > 0 else np.array([])
    ideal_notches_lp_hz = ideal_zlp_norm * w0_center / (2 * np.pi) if len(ideal_zlp_norm) > 0 else np.array([])

    # Run the compliance scanner
    ws_hp_deg, stat_hp, ws_lp_deg, stat_lp = analyze_stopband_compliance_bp(
        zeros_phys, poles_phys, k_phys, as_hp_db, as_lp_db, f1_hz, f2_hz
    )
    
    # Run the compliance scanner (which now handles all logic internally)
    ws_hp_deg, stat_hp, ws_lp_deg, stat_lp = analyze_stopband_compliance_bp(
        zeros_phys, poles_phys, k_phys, as_hp_db, as_lp_db, f1_hz, f2_hz
    )
    
    # Let the scanner completely control the final markers!
    # No more overrides or None fallbacks.
    ws_hp_hz = ws_hp_deg
    ws_lp_hz = ws_lp_deg
    
    return {
        "poles": poles_phys,
        "zeros": zeros_phys,
        "reflection_zeros": r_zeros_phys,
        "k": k_phys,
        "f_stop_hp_hz": ws_hp_hz,
        "f_stop_lp_hz": ws_lp_hz,
        "ideal_notches_hp_hz": ideal_notches_hp_hz,
        "ideal_notches_lp_hz": ideal_notches_lp_hz,
        "sb_status_hp": stat_hp,  
        "sb_status_lp": stat_lp   
    }

def analyze_stopband_compliance_br(zeros_phys, poles_phys, k_phys, as_db, f1_hz, f2_hz):
    from scipy.optimize import minimize_scalar
    
    def mag_db(f_hz):
        w = 2 * np.pi * f_hz
        jw = 1j * w
        num = k_phys * np.prod(jw - zeros_phys) if len(zeros_phys) > 0 else k_phys + 0j
        den = np.prod(jw - poles_phys)
        return 20 * np.log10(max(abs(num / den), 1e-12))
        
    def safe_minimize(f_min, f_max):
        if f_max <= f_min + 1e-5: return None
        try: return minimize_scalar(lambda f: -mag_db(f), bounds=(f_min, f_max), method='bounded')
        except ValueError: return None

    notches_hz = sorted([abs(z.imag)/(2*np.pi) for z in zeros_phys if abs(z.real) < 1e-6 and z.imag > 1e-6])
    status = 'normal'
    
    if len(notches_hz) > 0:
        for i in range(len(notches_hz)-1):
            gap = notches_hz[i+1] - notches_hz[i]
            if gap > 1e-3:
                res = safe_minimize(notches_hz[i] + gap*0.01, notches_hz[i+1] - gap*0.01)
                if res and -res.fun > -as_db + 1e-4:
                    status = 'degraded'
                    
    if len(notches_hz) > 0:
        mid_f = np.sqrt(notches_hz[0] * notches_hz[-1]) if len(notches_hz) > 1 else notches_hz[0]
        if mag_db(mid_f) > -as_db + 1e-4 and status == 'normal':
            status = 'corrupted'
    elif mag_db(np.sqrt(f1_hz * f2_hz)) > -as_db + 1e-4:
         status = 'corrupted'

    return status

#@st.cache_data(max_entries=50)
def synthesize_bandreject(response, order_lp, order_hp, f1_hz, f2_hz, 
                          alpha_max, as_db, manual_notches_hz=None, 
                          pb_even_mod_lp=False, pb_even_mod_hp=False, 
                          sb_rolloff=False):
    from scipy.signal import find_peaks
    from filter_utils import evaluate_h
    
    manual_notches_hz = manual_notches_hz or {}
    
    # 1. GEOMETRIC NORMALIZATION
    w1_phys = 2 * np.pi * f1_hz
    w2_phys = 2 * np.pi * f2_hz
    w0_center = np.sqrt(w1_phys * w2_phys) 
    
    wp1_norm = w1_phys / w0_center
    wp2_norm = w2_phys / w0_center
    notches_norm = {k: (v * 2 * np.pi) / w0_center for k, v in manual_notches_hz.items()}

    # 2. RUN SOLVERS
    if response == "Butterworth":
        poles_n, zeros_n, k_norm, ideal_z_norm, final_z_norm, _, _, r_zeros_n = synthesize_butterworth_gbr(
            order_lp, order_hp, wp1_norm, wp2_norm, as_db, alpha_max, notches_norm
        )
    elif response == "Chebyshev":
        poles_n, zeros_n, k_norm, ideal_z_norm, final_z_norm, _, _, r_zeros_n = synthesize_cheby1_gbr(
            order_lp, order_hp, wp1_norm, wp2_norm, as_db, alpha_max, pb_even_mod_lp, pb_even_mod_hp, notches_norm
        )
    elif response == "Inverse Chebyshev":
        try:
            poles_n, zeros_n, k_norm, ideal_z_norm, final_z_norm, _, _, r_zeros_n = synthesize_invcheby_gbr(
                order_lp, order_hp, wp1_norm, wp2_norm, as_db, alpha_max, sb_rolloff, notches_norm
            )
        except Exception as e:
            if "bound" in str(e).lower() or "sign" in str(e).lower():
                nudged_notches = {k: v * 1.000001 for k, v in notches_norm.items()}
                poles_n, zeros_n, k_norm, ideal_z_norm, final_z_norm, _, _, r_zeros_n = synthesize_invcheby_gbr(
                    order_lp, order_hp, wp1_norm, wp2_norm, as_db, alpha_max, sb_rolloff, nudged_notches
                )
            else: raise e
    elif response == "Elliptic":
        try:
            poles_n, zeros_n, k_norm, ideal_z_norm, final_z_norm, _, _, r_zeros_n = synthesize_elliptic_gbr(
                order_lp, order_hp, wp1_norm, wp2_norm, as_db, alpha_max, pb_even_mod_lp, pb_even_mod_hp, sb_rolloff, notches_norm
            )
        except Exception as e:
            if "bound" in str(e).lower() or "sign" in str(e).lower():
                nudged_notches = {k: v * 1.000001 for k, v in notches_norm.items()}
                poles_n, zeros_n, k_norm, ideal_z_norm, final_z_norm, _, _, r_zeros_n = synthesize_elliptic_gbr(
                    order_lp, order_hp, wp1_norm, wp2_norm, as_db, alpha_max, pb_even_mod_lp, pb_even_mod_hp, sb_rolloff, nudged_notches
                )
            else: raise e
    else:
        raise ValueError(f"Unknown response type: {response}")

    # 3. DENORMALIZE
    poles_phys = np.array(poles_n) * w0_center
    zeros_phys = np.array(zeros_n) * w0_center
    r_zeros_phys = np.array(r_zeros_n) * w0_center
    
    degree_diff = len(poles_phys) - len(zeros_phys)
    k_phys = k_norm * (w0_center ** degree_diff)
    ideal_notches_hz = ideal_z_norm * w0_center / (2 * np.pi) if len(ideal_z_norm) > 0 else np.array([])

    # 4. COMPLIANCE CHECK & DYNAMIC CROSSING SCANNER
    w_grid = np.logspace(np.log10(w1_phys), np.log10(w2_phys), 5000)
    h_mag = evaluate_h(poles_phys, zeros_phys, k_phys, w_grid)
    
    peaks, _ = find_peaks(h_mag)
    actual_as_db = as_db + 10.0 
    if len(peaks) > 0:
        max_hump_mag = np.max(h_mag[peaks])
        if max_hump_mag > 1e-12:
            actual_as_db = -20 * np.log10(max_hump_mag)
            
    actual_as_db = float(actual_as_db)
    
    # Check if a hump broke through the target limit
    if actual_as_db < as_db - 0.1: 
        stat_br = 'corrupted'
        # Recalculate true crossings at the exact peak of the hump!
        ws_lp_hz, ws_hp_hz = find_crossings_br(poles_phys, zeros_phys, k_phys, actual_as_db - 0.05, w1_phys, w2_phys)
    else:
        stat_br = 'normal'
        ws_lp_hz, ws_hp_hz = find_crossings_br(poles_phys, zeros_phys, k_phys, as_db, w1_phys, w2_phys)
        
    # Convert from rad/s to Hz
    ws_lp_hz = ws_lp_hz / (2 * np.pi) if ws_lp_hz else None
    ws_hp_hz = ws_hp_hz / (2 * np.pi) if ws_hp_hz else None

    return {
        "poles": poles_phys,
        "zeros": zeros_phys,
        "reflection_zeros": r_zeros_phys,
        "k": k_phys,
        "f_stop_hp_hz": ws_lp_hz, # Lower freq (near f1)
        "f_stop_lp_hz": ws_hp_hz, # Upper freq (near f2)
        "ideal_notches_hz": ideal_notches_hz,
        "sb_status_hp": stat_br,
        "sb_status_lp": stat_br,
        "actual_as_db": actual_as_db # Pass the physical hump height to the UI
    }
       
# =====================================================================
# ISOLATED TESTING ENVIRONMENT
# =====================================================================
if __name__ == "__main__":
    print("Testing Engine Controller...")
    
    # Test: User wants an Elliptic LP, N=4, fc=1000 Hz, with a manual notch at 2500 Hz in slot 0
    fc = 1000.0
    result = synthesize_lowpass(
        response="Elliptic",
        order=4,
        fc_hz=fc,
        alpha_max=1.0,
        as_db=40.0,
        manual_notches_hz={0: 2500.0}, 
        pb_even_mod=False,
        sb_rolloff=False
    )
    
    print(f"\nTarget f_c: {fc} Hz")
    print(f"Calculated f_stop: {result['f_stop_hz']:.2f} Hz")
    print(f"Physical Gain K: {result['k']:.5e}")
    print("\nPhysical Poles (rad/s):")
    for p in result['poles']: print(f"  {p.real:+.2f} {p.imag:+.2f}j")
    print("\nPhysical Zeros (rad/s):")
    for z in result['zeros']: print(f" {z.imag:+.2f}j")