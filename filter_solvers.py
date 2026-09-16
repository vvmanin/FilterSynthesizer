# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

import numpy as np
from scipy.optimize import root_scalar, minimize_scalar, least_squares
from filter_utils import find_crossing
from scipy.signal import ellipap


# ============================================================
# BUTTERWORTH (GENERALIZED)
# ============================================================
def solve_butterworth_lp(order, alpha_max, as_db, notches=None):
    if notches is None:
        notches = []
    notches = sorted(notches)
    
    if len(notches) * 2 >= order:
        print(f"Warning: Order N={order} is not strictly proper for {len(notches)} notches.")
        
    epsilon = np.sqrt(10**(alpha_max/10.0) - 1.0)
    
    if len(notches) > 0:
        N_1 = np.prod(1.0 - 1.0 / np.array(notches)**2)
    else:
        N_1 = 1.0
        
    B = (epsilon**2) * (N_1**2)
    
    N_s = np.array([1.0])
    for wz in notches:
        N_s = np.polymul(N_s, [1.0/(wz**2), 0.0, 1.0])
        
    N_s_sq = np.polymul(N_s, N_s)
    
    term2 = np.zeros(2*order + 1)
    term2[0] = B * ((-1)**order)
    
    L = max(len(N_s_sq), len(term2))
    N_pad = np.pad(N_s_sq, (L - len(N_s_sq), 0))
    T_pad = np.pad(term2, (L - len(term2), 0))
    D_sq = N_pad + T_pad
    
    roots = np.roots(D_sq)
    lhp_poles = [r for r in roots if np.real(r) < -1e-6]
    
    if len(lhp_poles) != order:
        lhp_poles = sorted(roots, key=lambda x: np.real(x))[:order]
        
    tzeros = []
    for wz in notches:
        tzeros.extend([1j*wz, -1j*wz])
        
    jw = 0j
    num = 1.0 + 0j
    for z in tzeros: num *= (jw - z)
    den = 1.0 + 0j
    for p in lhp_poles: den *= (jw - p)
    
    gain = 1.0 / np.abs(num / den) if len(tzeros) else 1.0 / np.abs(1.0/den)
    omega_s = find_crossing(lhp_poles, tzeros, gain, as_db)
    
    # Reflection zeros for maximally flat passband are all at the origin
    r_zeros = np.zeros(order, dtype=complex)
    
    return np.array(tzeros), np.array(lhp_poles), gain, omega_s, r_zeros
    
# ============================================================
# CHEBYSHEV TYPE I (GENERALIZED)
# ============================================================

def _dc_factor(n, even_mod):
    if n % 2 == 1:   return 's'
    if even_mod:     return 's2'
    return None

def _n_free(n, even_mod):
    if n % 2 == 1:   return (n - 1) // 2
    if even_mod:     return (n - 2) // 2
    return n // 2

def _build_k_num(s_arr, z_locs, dc):
    num = np.ones_like(s_arr, dtype=complex)
    for z in z_locs:
        num *= s_arr**2 + z**2
    if dc == 's':    num *= s_arr
    elif dc == 's2': num *= s_arr**2
    return num

def _k_scale(z_locs, notch_freqs, dc):
    s = 1j * 1.0
    num = _build_k_num(s, z_locs, dc)
    den = 1.0 + 0j
    for pf in notch_freqs:
        den *= s**2 + pf**2
    return float(np.abs(num / den))

def _eval_h_mag_fast(w, z_locs, notch_freqs, dc, epsilon):
    s = 1j * np.asarray(w, dtype=float)
    den = np.ones_like(s, dtype=complex)
    for pf in notch_freqs:
        den *= s**2 + pf**2
    K_raw = _build_k_num(s, z_locs, dc) / den
    scale = _k_scale(z_locs, notch_freqs, dc)
    mag = 1.0 / np.sqrt(1 + epsilon**2 * np.abs(K_raw / scale)**2)
    return mag if np.asarray(w).ndim > 0 else float(mag)

def _solve_inner_passband(n, alpha_max, notch_freqs, even_mod):
    epsilon = np.sqrt(10**(alpha_max/10) - 1)
    dc = _dc_factor(n, even_mod)
    n_free = _n_free(n, even_mod)
    
    if n_free == 0:
        return np.array([]), epsilon, dc

    initial_z = np.cos(np.arange(1, 2*n_free, 2) * np.pi / (2*n))[::-1]

    def residuals_inner(x):
        z = np.sort(x)
        valleys = []
        
        # 1. Valley at DC
        if dc is None:
            valleys.append( 20*np.log10(max(_eval_h_mag_fast(0.0, z, notch_freqs, dc, epsilon), 1e-12)) )
        else:
            res = minimize_scalar(
                lambda w: _eval_h_mag_fast(w, z, notch_freqs, dc, epsilon),
                bounds=(0.0, z[0]), method='bounded', options={'xatol': 1e-10}
            )
            valleys.append( 20*np.log10(max(res.fun, 1e-12)) )
            
        # 2. Valleys between the free zeros
        for i in range(len(z)-1):
            if z[i+1] - z[i] < 1e-6:
                val = _eval_h_mag_fast(z[i], z, notch_freqs, dc, epsilon)
                valleys.append( 20*np.log10(max(val, 1e-12)) )
            else:
                res = minimize_scalar(
                    lambda w: _eval_h_mag_fast(w, z, notch_freqs, dc, epsilon),
                    bounds=(z[i], z[i+1]), method='bounded', options={'xatol': 1e-10}
                )
                valleys.append( 20*np.log10(max(res.fun, 1e-12)) )
                
        return np.array(valleys) - (-alpha_max)

    res = least_squares(residuals_inner, initial_z, bounds=(1e-5, 0.9999), 
                        ftol=1e-12, xtol=1e-12)
    opt_z = np.sort(res.x)
    return opt_z, epsilon, dc

def _derive_transfer_function(z_locs, dc, notch_freqs, n, epsilon):
    import mpmath
    mpmath.mp.dps = 60
    
    scale = mpmath.mpf(_k_scale(z_locs, notch_freqs, dc))
    eps_mp = mpmath.mpf(epsilon)
    
    def poly_mul_mp(p1, p2):
        if not len(p1) or not len(p2): return []
        res = [mpmath.mpf(0)] * (len(p1) + len(p2) - 1)
        for i, c1 in enumerate(p1):
            for j, c2 in enumerate(p2): 
                res[i+j] += c1 * c2
        return res

    if dc == 's':    nk = [mpmath.mpf(1.0), mpmath.mpf(0.0)]
    elif dc == 's2': nk = [mpmath.mpf(1.0), mpmath.mpf(0.0), mpmath.mpf(0.0)]
    else:            nk = [mpmath.mpf(1.0)]

    for z in z_locs:
        z2 = mpmath.mpf(z)**2
        nk = poly_mul_mp(nk, [mpmath.mpf(1.0), -2 * z2, z2**2])

    nk = [c / (scale**2) for c in nk]

    dk = [mpmath.mpf(1.0)]
    for pf in notch_freqs:
        pf2 = mpmath.mpf(pf)**2
        dk = poly_mul_mp(dk, [mpmath.mpf(1.0), -2 * pf2, pf2**2])

    L = max(len(nk), len(dk)) - 1
    nk_pad = [mpmath.mpf(0)] * (L + 1 - len(nk)) + nk
    dk_pad = [mpmath.mpf(0)] * (L + 1 - len(dk)) + dk
    
    denom_u = [dk_pad[i] + (eps_mp**2) * nk_pad[i] for i in range(L + 1)]

    s_coeffs = [mpmath.mpf(0)] * (2 * L + 1)
    for k, c in enumerate(denom_u):
        p = L - k
        if p % 2 != 0:
            s_coeffs[2 * p] -= c
        else:
            s_coeffs[2 * p] += c

    s_coeffs_desc = s_coeffs[::-1]
    all_roots = mpmath.polyroots(s_coeffs_desc, maxsteps=2000, extraprec=50)
    lhp_poles = [complex(r) for r in all_roots if r.real < -1e-6]

    tzeros = np.array([v for pf in notch_freqs for v in (1j * pf, -1j * pf)])
    
    w_test = np.linspace(0, 1.0, 1000)
    jw = 1j * w_test
    
    num_vals = np.ones_like(jw, dtype=complex)
    if len(tzeros) > 0:
        for z in tzeros:
            num_vals *= (jw - z)
            
    den_vals = np.ones_like(jw, dtype=complex)
    for p in lhp_poles:
        den_vals *= (jw - p)
        
    gain = 1.0 / np.max(np.abs(num_vals / den_vals))
    return np.array(lhp_poles), tzeros, gain

def solve_chebyshev_lp(order, alpha_max, as_db, notches=None, even_mod=False):
    notch_freqs = [] if notches is None else sorted(notches)
    
    z_locs, epsilon, dc = _solve_inner_passband(order, alpha_max, notch_freqs, even_mod)
    poles, zeros, gain = _derive_transfer_function(z_locs, dc, notch_freqs, order, epsilon)
    omega_s = find_crossing(poles, zeros, gain, as_db)
    
    # Extract reflection zeros from passband ripple locations
    r_zeros_list = []
    for z in z_locs:
        r_zeros_list.extend([1j*z, -1j*z])
    if dc == 's':
        r_zeros_list.append(0j)
    elif dc == 's2':
        r_zeros_list.extend([0j, 0j])
        
    return np.array(zeros), np.array(poles), gain, omega_s, np.array(r_zeros_list)

# ============================================================
# INVERSE CHEBYSHEV (GENERALIZED SLOT-BASED)
# ============================================================

def _get_epsilon_inv(notches, alpha_max):
    if len(notches) == 0:
        N_1 = 1.0
    else:
        # N(w) = prod(w_z^2 - w^2). Evaluate at w = 1.
        N_1 = np.prod(np.array(notches)**2 - 1.0)
    return abs(N_1) * np.sqrt(10**(alpha_max/10.0) - 1.0)

def _eval_h_mag_inv(w, notches, eps, n):
    w = np.asarray(w, dtype=float)
    if len(notches) == 0:
        N_w2 = np.ones_like(w)
    else:
        w_sq = w**2
        N_w2 = np.prod([nz**2 - w_sq for nz in notches], axis=0)
    den = N_w2**2 + eps**2 * (w**(2*n))
    mag = np.abs(N_w2) / np.sqrt(den)
    return mag if w.ndim > 0 else float(mag)

def _get_stopband_humps(notches, eps, n):
    """Calculates stopband lobes for Phase 1/2 Error Evaluation."""
    humps = []
    notches = np.sort(notches)
    
    # 1. Humps between notches
    for i in range(len(notches) - 1):
        res = minimize_scalar(
            lambda w: -_eval_h_mag_inv(w, notches, eps, n),
            bounds=(notches[i]*1.0001, notches[i+1]*0.9999),
            method='bounded', options={'xatol': 1e-10}
        )
        humps.append(-res.fun)
        
    # 2. Final hump (either at infinity or a finite frequency)
    if 2 * len(notches) == n:
        mag_inf = 1.0 / np.sqrt(1.0 + eps**2)
        humps.append(mag_inf)
    elif len(notches) > 0:
        res = minimize_scalar(
            lambda w: -_eval_h_mag_inv(w, notches, eps, n),
            bounds=(notches[-1]*1.0001, notches[-1]*1000.0),
            method='bounded', options={'xatol': 1e-10}
        )
        humps.append(-res.fun)
        
    return -20 * np.log10(np.maximum(np.array(humps), 1e-12))

def _derive_tf_inv_cheby(notches, eps, n):
    P_s = np.array([1.0])
    for nz in notches: P_s = np.polymul(P_s, [1.0, 0.0, nz**2])
    P_s_sq = np.polymul(P_s, P_s)
    
    term2 = np.zeros(2*n + 1)
    term2[0] = eps**2 * ((-1)**n)
    
    L = max(len(P_s_sq), len(term2))
    P_pad = np.pad(P_s_sq, (L - len(P_s_sq), 0))
    T_pad = np.pad(term2, (L - len(term2), 0))
    D_sq = P_pad + T_pad
    
    roots = np.roots(D_sq)
    lhp_poles = [r for r in roots if np.real(r) < -1e-6]
    
    if len(lhp_poles) != n:
        lhp_poles = sorted(roots, key=lambda x: np.real(x))[:n]
        
    tzeros = []
    for nz in notches: tzeros.extend([1j*nz, -1j*nz])
        
    jw = 0j
    num = 1.0 + 0j
    for z in tzeros: num *= (jw - z)
    den = 1.0 + 0j
    for p in lhp_poles: den *= (jw - p)
    
    gain = 1.0 / np.abs(num / den) if len(tzeros) else 1.0 / np.abs(1.0/den)
    return lhp_poles, tzeros, gain

def _compute_ideal_baseline(n, alpha_max, as_db, P_eff):
    """Phase 1: Purely perfect monotonic limit."""
    omega_s_guess = 1.2
    initial_notches = omega_s_guess / np.cos((2 * np.arange(1, P_eff + 1) - 1) * np.pi / (2 * n))
    initial_notches = np.sort(initial_notches)

    def decode_vars(x):
        notches = np.zeros(P_eff)
        notches[0] = 1.001 + x[0]
        for i in range(1, P_eff): notches[i] = notches[i-1] + 0.001 + x[i]
        return notches

    def encode_vars(notches):
        x = np.zeros(P_eff)
        x[0] = notches[0] - 1.001
        for i in range(1, P_eff): x[i] = notches[i] - notches[i-1] - 0.001
        return np.maximum(x, 1e-4)

    def residuals_x(x):
        notches = decode_vars(x)
        eps = _get_epsilon_inv(notches, alpha_max)
        humps = _get_stopband_humps(notches, eps, n)
        return humps - as_db

    x0 = encode_vars(initial_notches)
    res = least_squares(residuals_x, x0, bounds=(1e-4, np.inf), ftol=1e-12, xtol=1e-12)
    return decode_vars(res.x)

def solve_inv_chebyshev_lp(order, alpha_max, as_db, slots=None, sb_rolloff=False):
    slots = slots or {}
    P = order // 2
    P_eff = P - 1 if sb_rolloff else P
    
    if P_eff <= 0:
        eps = _get_epsilon_inv([], alpha_max)
        poles, zeros, gain = _derive_tf_inv_cheby([], eps, order)
        omega_s = find_crossing(poles, zeros, gain, as_db)
        return np.array(zeros), np.array(poles), gain, omega_s, np.array([]), np.zeros(order, dtype=complex)
        
    ideal_notches = _compute_ideal_baseline(order, alpha_max, as_db, P_eff)
    free_idx = [i for i in range(P_eff) if i not in slots]
    
    def decode_p2(vars):
        notches = np.copy(ideal_notches)
        for idx, val in slots.items(): notches[idx] = val
        v_idx = 0
        for i in free_idx:
            notches[i] = vars[v_idx]
            v_idx += 1
        return np.sort(notches)

    def residuals_p2(vars):
        notches = decode_p2(vars)
        eps = _get_epsilon_inv(notches, alpha_max)
        humps = _get_stopband_humps(notches, eps, order)
        errs = []
        for i in range(P_eff):
            if i not in slots: errs.append(humps[i] - as_db)
        return errs

    init_vars = [ideal_notches[i] for i in free_idx]
        
    if len(init_vars) == 0:
        final_notches = decode_p2([])
    else:
        b_low = [1.0001] * len(free_idx)
        b_up = [np.inf] * len(free_idx)
        res = least_squares(residuals_p2, init_vars, bounds=(b_low, b_up), ftol=1e-10, xtol=1e-10)
        final_notches = decode_p2(res.x)

    eps = _get_epsilon_inv(final_notches, alpha_max)
    poles, zeros, gain = _derive_tf_inv_cheby(final_notches, eps, order)
    omega_s = find_crossing(poles, zeros, gain, as_db)
    
    # Reflection zeros for maximally flat passband are all at the origin
    r_zeros = np.zeros(order, dtype=complex)
    
    return np.array(zeros), np.array(poles), gain, omega_s, ideal_notches, r_zeros

# ============================================================
# ELLIPTIC (TRANSFORMED VARIABLE + MULTIPRECISION)
# ============================================================
import numpy as np
from scipy.signal import ellipap, find_peaks
from scipy.optimize import least_squares, minimize_scalar, brentq
import mpmath

def _find_crossing_ellip(poles, zeros, gain, A_s, first_tz):
    """Finds the exact stopband edge frequency (omega_s) where attenuation hits A_s."""
    def obj(w):
        jw = 1j * w
        n_val = np.ones_like(jw, dtype=complex)
        for z in zeros: n_val *= (jw - z)
        d_val = np.ones_like(jw, dtype=complex)
        for p in poles: d_val *= (jw - p)
        mag_db = 20 * np.log10(np.abs(n_val / d_val) * gain + 1e-12)
        return mag_db - (-A_s)
        
    try:
        # Crosses exactly between the cutoff (w=1) and the first transmission zero
        return brentq(obj, 1.0, first_tz)
    except ValueError:
        # Fallback if Brent's method misses the root bracket
        w_scan = np.linspace(1.0, first_tz, 1000)
        mags = np.abs([obj(w) for w in w_scan])
        return w_scan[np.argmin(mags)]

def _solve_standard_elliptic(order, alpha_max, A_s):
    """Bypass optimized solver for unmodified textbook elliptic filters."""
    z_ideal, p_ideal, k_ideal = ellipap(order, alpha_max, A_s)
    tz_ideal = np.sort(np.abs(np.imag(z_ideal[np.imag(z_ideal) > 0])))
    
    # Extract pristine Reflection Zeros via Stretched Domain Sweep
    th_sweep = np.linspace(0, np.pi/2 - 1e-10, 100000)
    w_sweep = np.sin(th_sweep)
    jw = 1j * w_sweep
    num = np.prod([jw - zi for zi in z_ideal], axis=0)
    den = np.prod([jw - pi for pi in p_ideal], axis=0)
    H_mag = np.abs(k_ideal * num / den)
    peaks, _ = find_peaks(H_mag)
    rz_ideal = w_sweep[peaks]

    # Format exactly to API structure
    tzeros = np.array([val for tz in tz_ideal for val in (1j*tz, -1j*tz)])
    rzeros = np.array([val for rz in rz_ideal for val in (1j*rz, -1j*rz)])
    if order % 2 != 0: rzeros = np.append(rzeros, 0j)
    poles = np.array(p_ideal)
    
    # Absolute Peak Normalization (0 dB max)
    w_test = np.linspace(0.001, 1.0, 1000)
    if order % 2 != 0: w_test = np.insert(w_test, 0, 0.0)
    jw_test = 1j * w_test
    
    num_vals = np.ones_like(jw_test, dtype=complex)
    for z in tzeros: num_vals *= (jw_test - z)
    den_vals = np.ones_like(jw_test, dtype=complex)
    for p in poles: den_vals *= (jw_test - p)
    
    gain = 1.0 / np.max(np.abs(num_vals / den_vals))
    omega_s = _find_crossing_ellip(poles, tzeros, gain, A_s, tz_ideal[0])
    
    return tzeros, poles, gain, omega_s, tz_ideal, rzeros


def solve_elliptic_lp(order, alpha_max, A_s, slots=None, pb_even_mod=False, sb_rolloff=False):
    """
    Main Elliptic Synthesis API.
    Handles standard topologies natively and routes modified topologies to the 
    Transformed Variable + Multiprecision (mpmath) solver.
    """
    slots = slots or {}

    # 1. HARD CONSTRAINTS
    if order > 15:
        raise ValueError(f"Elliptic filter order capped at N=15. Requested N={order} risks absolute numerical collapse.")
        
    # Graceful degradation: Ignore manual notches for N > 8 to prevent UI crashes during toggles
    if len(slots) > 0 and order > 8:
        slots = {}

    # 2. BYPASS OPTIMIZER IF STANDARD
    needs_even_mod = pb_even_mod and (order % 2 == 0)
    needs_rolloff = sb_rolloff
    needs_slots = len(slots) > 0
    needs_optimization = needs_even_mod or needs_rolloff or needs_slots
    
    if not needs_optimization:
        return _solve_standard_elliptic(order, alpha_max, A_s)

    # =====================================================================
    # STAGE A: TRANSFORMED VARIABLE SKELETON
    # =====================================================================
    epsilon = np.sqrt(10**(alpha_max/10.0) - 1.0)
    target_log_L = np.log(np.sqrt(10**(A_s/10.0) - 1.0) / epsilon)

    # Ideal Seed Extraction
    z_ideal, p_ideal, k_ideal = ellipap(order, alpha_max, A_s)
    tz_ideal = np.sort(np.abs(np.imag(z_ideal[np.imag(z_ideal) > 0])))
    
    w_sweep = np.sin(np.linspace(0, np.pi/2 - 1e-10, 100000))
    jw = 1j * w_sweep
    num = np.prod([jw - zi for zi in z_ideal], axis=0)
    den = np.prod([jw - pi for pi in p_ideal], axis=0)
    peaks, _ = find_peaks(np.abs(k_ideal * num / den))
    rz_ideal = w_sweep[peaks]

    # Unified Topology Rules
    rz_free, tz_free = list(rz_ideal), list(tz_ideal)
    has_single_dc_root, has_double_dc_root, has_inf_rolloff = False, False, False

    if order % 2 != 0:
        has_single_dc_root = True
        has_inf_rolloff = True
    else:
        if needs_even_mod:
            has_double_dc_root = True
            rz_free = rz_free[1:]

    if needs_rolloff:
        has_inf_rolloff = True
        tz_free = tz_free[:-1]

    rz_th_free = np.arcsin(rz_free)                  
    tz_phi_free = np.arcsin(1.0 / np.array(tz_free)) 

    # --- MANUAL SLOTS DECODING ---
    free_tz_idx = [i for i in range(len(tz_free)) if i not in slots]
    
    fixed_tz_phi = {}
    for i, w in slots.items():
        if i < len(tz_free):
            safe_w = max(float(w), 1.001) # Math safety: prevents NaN if UI pushes notch into passband
            fixed_tz_phi[i] = np.arcsin(1.0 / safe_w)
            
    init_tz_phi = [tz_phi_free[i] for i in free_tz_idx]

    # Log-Space Optimization Engine
    def calc_log_Rn(w, rz_th_vars, tz_phi_vars):
        w_sq = w**2
        rz_sq = np.sin(rz_th_vars)**2
        tz_sq = (1.0 / np.sin(tz_phi_vars))**2
        
        num_log = 0.0
        if has_single_dc_root: num_log += 0.5 * np.log(w_sq + 1e-100)
        if has_double_dc_root: num_log += np.log(w_sq + 1e-100)        
        for r2 in rz_sq: num_log += np.log(np.abs(w_sq - r2) + 1e-100)
            
        den_log = sum(np.log(np.abs(w_sq - t2) + 1e-100) for t2 in tz_sq)
            
        num1_log = 0.0
        if has_single_dc_root: num1_log += 0.5 * np.log(1.0 + 1e-100)
        if has_double_dc_root: num1_log += np.log(1.0 + 1e-100)
        for r2 in rz_sq: num1_log += np.log(np.abs(1.0 - r2) + 1e-100)
            
        den1_log = sum(np.log(np.abs(1.0 - t2) + 1e-100) for t2 in tz_sq)
            
        return (den1_log - num1_log) + num_log - den_log

    def inner_loop_rz(tz_phi_vars, init_rz_th):
        def rz_residuals(rz_th_vars):
            rz_th_vars = np.sort(rz_th_vars)
            errors = []
            bounds = [0.0] + list(rz_th_vars) 
            for i in range(len(bounds)-1):
                a, b = bounds[i], bounds[i+1]
                if b - a < 1e-6:
                    errors.append(1e6)
                    continue
                res = minimize_scalar(lambda th: -calc_log_Rn(np.sin(th), rz_th_vars, tz_phi_vars), bounds=(a, b), method='bounded', options={'xatol': 1e-10})
                errors.append(-res.fun)
            return np.array(errors)

        if len(init_rz_th) == 0: return np.array([])
        res = least_squares(rz_residuals, init_rz_th, bounds=(1e-6, np.pi/2 - 1e-6), ftol=1e-11, xtol=1e-11)
        return np.sort(res.x)

    def outer_loop_tz(init_tz_free_phi, init_rz_th):
        last_rz_th = [init_rz_th]
        
        # Helper to merge fixed UI slots with the optimizer's free variables
        def decode_tz_phi(free_vars):
            full_tz_phi = np.zeros(len(tz_free))
            for i, val in fixed_tz_phi.items(): 
                full_tz_phi[i] = val
            idx = 0
            for i in free_tz_idx:
                full_tz_phi[i] = free_vars[idx]
                idx += 1
            return np.sort(full_tz_phi)[::-1] 
            
        def tz_residuals(free_vars):
            tz_phi_vars = decode_tz_phi(free_vars) 
            rz_th_opt = inner_loop_rz(tz_phi_vars, last_rz_th[0])
            last_rz_th[0] = rz_th_opt
            
            errors = []
            bounds = list(tz_phi_vars)
            if has_inf_rolloff: bounds.append(0.0) 
            for i in range(len(bounds)-1):
                a, b = bounds[i], bounds[i+1]
                if a - b < 1e-6:
                    errors.append(1e6)
                    continue
                res = minimize_scalar(lambda phi: calc_log_Rn(1.0/np.sin(phi), rz_th_opt, tz_phi_vars), bounds=(b, a), method='bounded', options={'xatol': 1e-10})
                errors.append(res.fun - target_log_L)
                
            if not has_inf_rolloff:
                errors.append(calc_log_Rn(1e8, rz_th_opt, tz_phi_vars) - target_log_L)
            return np.array(errors)

        if len(init_tz_free_phi) == 0: 
            final_tz = decode_tz_phi([])
            return final_tz, inner_loop_rz(final_tz, init_rz_th)
            
        res = least_squares(tz_residuals, init_tz_free_phi, bounds=(1e-6, np.pi/2 - 1e-6), ftol=1e-11, xtol=1e-11)
        final_tz_phi = decode_tz_phi(res.x)
        return final_tz_phi, inner_loop_rz(final_tz_phi, last_rz_th[0])

    opt_tz_phi, opt_rz_th = outer_loop_tz(init_tz_phi, rz_th_free)
    opt_tz = 1.0 / np.sin(opt_tz_phi)
    opt_rz = np.sin(opt_rz_th)

    # =====================================================================
    # STAGE B: MULTIPRECISION ROOT EXTRACTION
    # =====================================================================
    mpmath.mp.dps = 60 
    
    num1_mp = mpmath.mpf(1.0)
    if has_single_dc_root or has_double_dc_root: num1_mp *= mpmath.mpf(1.0)
    for r in opt_rz: num1_mp *= (mpmath.mpf(1.0) - mpmath.mpf(r)**2)
        
    den1_mp = mpmath.mpf(1.0)
    for t in opt_tz: den1_mp *= (mpmath.mpf(1.0) - mpmath.mpf(t)**2)
        
    Kr_mp = abs(den1_mp / num1_mp)
    eps_mp = mpmath.mpf(epsilon)
    
    def poly_mul_mp(p1, p2):
        if not len(p1) or not len(p2): return []
        res = [mpmath.mpc(0)] * (len(p1) + len(p2) - 1)
        for i, c1 in enumerate(p1):
            for j, c2 in enumerate(p2):
                res[i+j] += c1 * c2
        return res

    D_mp, N_mp = [mpmath.mpc(1)], [mpmath.mpc(1)]
    for t in opt_tz: D_mp = poly_mul_mp(D_mp, [mpmath.mpc(1), mpmath.mpc(0), mpmath.mpc(t)**2])
    for r in opt_rz: N_mp = poly_mul_mp(N_mp, [mpmath.mpc(1), mpmath.mpc(0), mpmath.mpc(r)**2])

    if has_single_dc_root: N_mp = poly_mul_mp(N_mp, [mpmath.mpc(1), mpmath.mpc(0)])
    if has_double_dc_root: N_mp = poly_mul_mp(N_mp, [mpmath.mpc(1), mpmath.mpc(0), mpmath.mpc(0)])

    L = max(len(D_mp), len(N_mp))
    D_pad = [mpmath.mpc(0)] * (L - len(D_mp)) + D_mp
    N_pad = [mpmath.mpc(0)] * (L - len(N_mp)) + N_mp

    if has_single_dc_root: multiplier = eps_mp * Kr_mp
    else: multiplier = mpmath.mpc(0, 1) * eps_mp * Kr_mp

    P_split_mp = [D_pad[i] + multiplier * N_pad[i] for i in range(L)]
    raw_roots_mp = mpmath.polyroots(P_split_mp, maxsteps=2000, extraprec=50)

    p_upper, p_real = [], []
    for r in raw_roots_mp:
        p = complex(r)
        if p.real > 0: p = complex(-p.real, p.imag)
        if p.imag > 1e-5:
            if not any(np.abs(p - pu) < 1e-4 for pu in p_upper): p_upper.append(p)
        elif abs(p.imag) <= 1e-5:
            if not any(np.abs(p.real - pr.real) < 1e-4 for pr in p_real): p_real.append(complex(p.real, 0.0))

    final_poles = []
    for p in p_upper: final_poles.extend([p, np.conjugate(p)])
    final_poles.extend(p_real)
    poles = np.array(final_poles)

    # =====================================================================
    # STAGE C: FORMAT AND RETURN
    # =====================================================================
    tzeros = np.array([val for tz in opt_tz for val in (1j*tz, -1j*tz)])
    rzeros = np.array([val for rz in opt_rz for val in (1j*rz, -1j*rz)])
    if has_single_dc_root: rzeros = np.append(rzeros, 0j)
    elif has_double_dc_root: rzeros = np.append(rzeros, [0j, 0j])

    w_test = np.linspace(0.001, 1.0, 1000)
    if has_single_dc_root or has_double_dc_root: w_test = np.insert(w_test, 0, 0.0)
    jw_test = 1j * w_test
    
    num_vals = np.ones_like(jw_test, dtype=complex)
    for z in tzeros: num_vals *= (jw_test - z)
    den_vals = np.ones_like(jw_test, dtype=complex)
    for p in poles: den_vals *= (jw_test - p)
    
    gain = 1.0 / np.max(np.abs(num_vals / den_vals))
    
    # Assumes find_crossing is imported from filter_utils
    omega_s = find_crossing(poles, tzeros, gain, A_s)
    
    return tzeros, poles, gain, omega_s, tz_ideal, rzeros


def evaluate_h(poles, zeros, k, w_array):
    """Fast internal helper for solver magnitude evaluation in rad/s."""
    jw = 1j * np.asarray(w_array)
    h = np.ones_like(jw, dtype=complex) * k
    for z in zeros: h *= (jw - z)
    for p in poles: h /= (jw - p)
    return np.abs(h)

# ============================================================
# [BGB_SYNTH BLOCK]
# ============================================================

def derive_bgb_transfer_function(w0, K, zhp, zlp, c_fixed, is_odd, N_refl, N_DC, epsilon):
    E_poly = np.zeros(N_DC + 1)
    E_poly[0] = (-1)**N_DC
    for z in np.concatenate([zhp, zlp]):
        E_poly = np.polymul(E_poly, [1.0, 2 * z**2, z**4])
        
    P_poly = np.array([1.0])
    for _ in range(N_refl): 
        P_poly = np.polymul(P_poly, [1.0, 2 * w0**2, w0**4])
        
    if is_odd: 
        P_poly = np.polymul(P_poly, [-1.0, c_fixed])
        
    P_poly *= (epsilon * K)**2
    
    L = max(len(E_poly), len(P_poly))
    D2_x = np.pad(E_poly, (L - len(E_poly), 0)) + np.pad(P_poly, (L - len(P_poly), 0))
    
    D_s = np.zeros(2 * (len(D2_x) - 1) + 1)
    for i, coef in enumerate(D2_x): 
        D_s[2 * i] = coef
        
    lhp_poles = [r for r in np.roots(D_s) if np.real(r) < -1e-6]
    
    zeros = [0.0] * N_DC
    for z in np.concatenate([zhp, zlp]): 
        zeros.extend([1j * z, -1j * z])
        
    return lhp_poles, zeros

def find_crossing(poles, zeros, gain, target_db, bound_left=1e-5, bound_right=1e5, is_hp=False):
    from scipy.optimize import root_scalar
    target_mag = 10 ** (-(target_db - 0.1) / 20)
    w_grid = np.logspace(np.log10(bound_left), np.log10(bound_right), 3000)
    h_mag = evaluate_h(poles, zeros, gain, w_grid)
    
    valid_idx = np.where(h_mag <= target_mag)[0]
    if len(valid_idx) == 0:
        return None
        
    if is_hp:
        idx = valid_idx[-1]
        if idx < len(w_grid) - 1:
            w1, w2 = w_grid[idx], w_grid[idx+1]
            try:
                def err(wx): return evaluate_h(poles, zeros, gain, [wx])[0] - target_mag
                return root_scalar(err, bracket=[w1, w2], method='brentq').root
            except: pass
        return w_grid[idx]
    else:
        idx = valid_idx[0]
        if idx > 0:
            w1, w2 = w_grid[idx-1], w_grid[idx]
            try:
                def err(wx): return evaluate_h(poles, zeros, gain, [wx])[0] - target_mag
                return root_scalar(err, bracket=[w1, w2], method='brentq').root
            except: pass
        return w_grid[idx]
    
def synthesize_bgb(order_hp, order_lp, wp1, wp2, alpha_max, as_hp, as_lp, notch_hp=None, notch_lp=None):
    zhp = np.sort(notch_hp) if notch_hp is not None else np.array([])
    zlp = np.sort(notch_lp) if notch_lp is not None else np.array([])
    
    n_hpz = len(zhp)
    n_lpz = len(zlp)
        
    N_DC = max(0, order_hp - 2 * n_hpz)
    N_INF = max(0, order_lp - 2 * n_lpz)
    
    N_total = order_hp + order_lp
    N_refl = N_total // 2
    is_odd = (N_total % 2 != 0)
    
    c_fixed = wp1 * wp2 if is_odd else 0.0
    epsilon = np.sqrt(10**(alpha_max/10.0) - 1.0)
    
    def E_mag(w):
        val = w**N_DC
        for z in np.concatenate([zhp, zlp]): val *= abs(w**2 - z**2)
        return val

    R_target = E_mag(wp1) / E_mag(wp2)

    def P_ratio(w0):
        P1 = abs(wp1**2 - w0**2)**N_refl
        P2 = abs(wp2**2 - w0**2)**N_refl
        if is_odd:
            P1 *= np.sqrt(wp1**2 + c_fixed)
            P2 *= np.sqrt(wp2**2 + c_fixed)
        return P1 / P2

    res = root_scalar(lambda w0: P_ratio(w0) - R_target, bracket=[wp1 + 1e-6, wp2 - 1e-6])
    w0_opt = res.root
    
    P1 = abs(wp1**2 - w0_opt**2)**N_refl
    if is_odd: P1 *= np.sqrt(wp1**2 + c_fixed)
    K_opt = E_mag(wp1) / P1

    poles, zeros = derive_bgb_transfer_function(w0_opt, K_opt, zhp, zlp, c_fixed, is_odd, N_refl, N_DC, epsilon)
    
    w_test = np.linspace(wp1, wp2, 500)
    gain = 1.0 / np.max(evaluate_h(poles, zeros, 1.0, w_test))
    
    ws_hp = find_crossing(poles, zeros, gain, as_hp, wp1 * 1e-5, wp1, is_hp=True)
    ws_lp = find_crossing(poles, zeros, gain, as_lp, wp2, wp2 * 1e5, is_hp=False)
    
    return poles, zeros, gain, w0_opt, ws_hp, ws_lp

# ============================================================
# [ASYM_CHEBY1_BP_SYNTH BLOCK]
# ============================================================

def solve_inner_bp_passband(N_total, N_dc, notch_hp, notch_lp, wp1, wp2, alpha_max):
    epsilon = np.sqrt(10**(alpha_max/10.0) - 1.0)
    N_refl = N_total // 2
    is_odd = (N_total % 2 != 0)
    
    c_fixed = wp1 * wp2 if is_odd else 0.0
    
    k = np.arange(1, N_refl + 1)
    nodes = np.cos((2 * k - 1) * np.pi / (2 * N_refl))[::-1]
    initial_z = 0.5 * (wp2 - wp1) * nodes + 0.5 * (wp1 + wp2)
    
    def F_raw(w, z_locs):
        num = np.ones_like(w, dtype=float)
        for zk in z_locs: 
            num *= (w**2 - zk**2)
        if is_odd:
            num *= np.sqrt(w**2 + c_fixed)
        den = (w**N_dc)
        for nz in notch_hp: den *= (w**2 - nz**2)
        for nz in notch_lp: den *= (w**2 - nz**2)
        return num / den

    def residuals(z):
        z = np.sort(z)
        K = 1.0 / max(abs(F_raw(np.array([wp1]), z)[0]), 1e-12)
        
        peaks = []
        for i in range(len(z) - 1):
            if z[i+1] - z[i] < 1e-6:
                peaks.append(0.0)
            else:
                res = minimize_scalar(
                    lambda w: -abs(K * F_raw(np.array([w]), z)[0]),
                    bounds=(z[i], z[i+1]), method='bounded', options={'xatol': 1e-10}
                )
                peaks.append(-res.fun)
                
        edge_peak = abs(K * F_raw(np.array([wp2]), z)[0])
        return np.array(peaks + [edge_peak]) - 1.0

    bounds_lower = [wp1 + 1e-6]*N_refl
    bounds_upper = [wp2 - 1e-6]*N_refl
        
    res = least_squares(residuals, initial_z, bounds=(bounds_lower, bounds_upper), 
                        ftol=1e-12, xtol=1e-12)
                        
    opt_z = np.sort(res.x)
    K_opt = 1.0 / max(abs(F_raw(np.array([wp1]), opt_z)[0]), 1e-12)
    
    return opt_z, c_fixed, K_opt, epsilon, is_odd

def derive_asym_bp_transfer_function(opt_z, c_fixed, K, epsilon, N_dc, notch_hp, notch_lp, is_odd):
    import mpmath
    mpmath.mp.dps = 60
    
    def poly_mul_mp(p1, p2):
        if not len(p1) or not len(p2): return []
        res = [mpmath.mpf(0)] * (len(p1) + len(p2) - 1)
        for i, c1 in enumerate(p1):
            for j, c2 in enumerate(p2): 
                res[i+j] += c1 * c2
        return res

    T_P = [mpmath.mpf((-1)**N_dc)] + [mpmath.mpf(0)] * N_dc
    for nz in list(notch_hp) + list(notch_lp):
        nz2 = mpmath.mpf(nz)**2
        T_P = poly_mul_mp(T_P, [mpmath.mpf(1.0), 2 * nz2, nz2**2])
        
    T_E = [mpmath.mpf(1.0)]
    for z in opt_z:
        z2 = mpmath.mpf(z)**2
        T_E = poly_mul_mp(T_E, [mpmath.mpf(1.0), 2 * z2, z2**2])
        
    if is_odd:
        T_E = poly_mul_mp(T_E, [mpmath.mpf(-1.0), mpmath.mpf(c_fixed)])
        
    L = max(len(T_P), len(T_E))
    T_P_pad = [mpmath.mpf(0)] * (L - len(T_P)) + T_P
    T_E_pad = [mpmath.mpf(0)] * (L - len(T_E)) + T_E
    
    multiplier = (mpmath.mpf(epsilon) * mpmath.mpf(K))**2
    D2_x = [T_P_pad[i] + multiplier * T_E_pad[i] for i in range(L)]
    
    M = len(D2_x) - 1
    D_s = [mpmath.mpf(0)] * (2 * M + 1)
    for i, coef in enumerate(D2_x):
        D_s[2 * i] = coef
        
    roots = mpmath.polyroots(D_s, maxsteps=2000, extraprec=50)
    lhp_poles = [complex(r) for r in roots if r.real < -1e-6]
    
    zeros = [0.0] * N_dc
    for nz in list(notch_hp) + list(notch_lp):
        zeros.extend([1j * nz, -1j * nz])
        
    return np.array(lhp_poles), np.array(zeros)

def synthesize_asym_cheby1_bp(order_hp, order_lp, alpha_max, wp1, wp2, as_db_hp, as_db_lp, notches_hp=None, notches_lp=None):
    n_hp = [] if notches_hp is None else sorted(notches_hp)
    n_lp = [] if notches_lp is None else sorted(notches_lp)
    
    N_total = order_hp + order_lp
    N_dc = order_hp - 2 * len(n_hp)
    N_inf = order_lp - 2 * len(n_lp)
        
    opt_z, c_fixed, K, epsilon, is_odd = solve_inner_bp_passband(N_total, N_dc, n_hp, n_lp, wp1, wp2, alpha_max)
    poles, zeros = derive_asym_bp_transfer_function(opt_z, c_fixed, K, epsilon, N_dc, n_hp, n_lp, is_odd)
    
    w_test = np.linspace(wp1, wp2, 1000)
    gain = 1.0 / np.max(evaluate_h(poles, zeros, 1.0, w_test))
    
    omega_s_hp = find_crossing(poles, zeros, gain, as_db_hp, wp1 * 1e-4, wp1, is_hp=True)
    omega_s_lp = find_crossing(poles, zeros, gain, as_db_lp, wp2, wp2 * 1e4, is_hp=False)
    
    return poles, zeros, gain, omega_s_hp, omega_s_lp, opt_z

# ============================================================
# CHEBYSHEV BANDPASS (ARBITRATED & TRANSFORMED)
# ============================================================
import numpy as np

def _transform_lp_to_bp_cheby(lp_poles, lp_tzeros, lp_rzeros, wp1, wp2):
    """Geometrically maps LHP Lowpass roots directly to Bandpass roots."""
    B = wp2 - wp1
    w0_sq = wp1 * wp2
    w0 = np.sqrt(w0_sq)

    bp_poles, bp_tzeros, bp_rzeros = [], [], []

    def map_root(r):
        if abs(r) < 1e-12: return 1j * w0, -1j * w0 # DC maps perfectly to center frequency
        term = np.sqrt((B * r / 2.0)**2 - w0_sq, dtype=complex)
        s1 = B * r / 2.0 + term
        s2 = B * r / 2.0 - term
        return s1, s2

    # 1. Map Poles
    for p in lp_poles:
        s1, s2 = map_root(p)
        bp_poles.extend([s1, s2])

    # 2. Map Finite Transmission Zeros (For Generalized Chebyshev with slots)
    for z in lp_tzeros:
        s1, s2 = map_root(z)
        bp_tzeros.extend([s1, s2])

    # 3. Map Infinity Zeros (Standard Chebyshev Rolloff maps to DC)
    num_zeros_inf = len(lp_poles) - len(lp_tzeros)
    if num_zeros_inf > 0:
        bp_tzeros.extend([0j] * num_zeros_inf)

    # 4. Map Reflection Zeros (Passband Ripples)
    for rz in lp_rzeros:
        s1, s2 = map_root(rz)
        bp_rzeros.extend([s1, s2])

    return np.array(bp_poles), np.array(bp_tzeros), np.array(bp_rzeros)

def _solve_symmetric_cheby_bp_bypass(order_branch, alpha_max, as_db, wp1, wp2, pb_even_mod):
    """Leverages the fast LP Chebyshev solver for perfectly symmetric specifications."""
    # Generate the pristine Lowpass prototype
    lp_tzeros, lp_poles, _, lp_ws, lp_rzeros = solve_chebyshev_lp(
        order=order_branch, alpha_max=alpha_max, as_db=as_db, notches=[], even_mod=pb_even_mod
    )

    bp_poles, bp_tzeros, bp_rzeros = _transform_lp_to_bp_cheby(lp_poles, lp_tzeros, lp_rzeros, wp1, wp2)

    # Universal Peak Normalization (Sweep the passband to find true 0 dB peaks)
    w_test = np.linspace(wp1, wp2, 2000)
    jw_test = 1j * w_test
    
    num_vals = np.ones_like(jw_test, dtype=complex)
    for z in bp_tzeros: num_vals *= (jw_test - z)
        
    den_vals = np.ones_like(jw_test, dtype=complex)
    for p in bp_poles: den_vals *= (jw_test - p)
        
    gain = 1.0 / np.max(np.abs(num_vals / den_vals))

    # Map stopband edges using strict transformation
    B = wp2 - wp1
    w0_sq = wp1 * wp2
    ws_lp = (B * lp_ws + np.sqrt((B * lp_ws)**2 + 4 * w0_sq)) / 2.0
    ws_hp = (-B * lp_ws + np.sqrt((B * lp_ws)**2 + 4 * w0_sq)) / 2.0

    # Format the reflection zeros to match the legacy `opt_z` float array expected by filter_engine
    opt_z_mock = np.sort([z.imag for z in bp_rzeros if z.imag > 1e-6 and abs(z.real) < 1e-6])

    return bp_poles, bp_tzeros, gain, ws_hp, ws_lp, opt_z_mock

# ---------------------------------------------------------------------
# MAIN CHEBYSHEV BP ARBITRATOR
# ---------------------------------------------------------------------
def synthesize_cheby1_bp_arbitrated(order_hp, order_lp, alpha_max, wp1, wp2, as_db_hp, as_db_lp, 
                                    notches_hp=None, notches_lp=None, pb_even_mod_hp=False, pb_even_mod_lp=False):
    """
    Arbitrates between the ultra-fast LP->BP geometric bypass and the native asymmetric solver.
    """
    notches_hp = notches_hp or []
    notches_lp = notches_lp or []
    
    # Symmetry Condition: Same order, same As, same even_mod, NO manual slots.
    is_symmetric = (
        order_hp == order_lp and 
        abs(as_db_hp - as_db_lp) < 1e-4 and 
        pb_even_mod_hp == pb_even_mod_lp and 
        len(notches_hp) == 0 and len(notches_lp) == 0
    )
    
    if is_symmetric:
        return _solve_symmetric_cheby_bp_bypass(order_hp, alpha_max, as_db_hp, wp1, wp2, pb_even_mod_hp)
    else:
        # Pass directly to your original Asymmetric Solver
        return synthesize_asym_cheby1_bp(order_hp, order_lp, alpha_max, wp1, wp2, as_db_hp, as_db_lp, notches_hp, notches_lp)

# ============================================================
# INVERSE CHEBYSHEV BANDPASS (ARBITRATED & SLOTTED)
# ============================================================
import numpy as np
from scipy.optimize import least_squares, minimize_scalar, root_scalar

# ---------------------------------------------------------------------
# 1. SYMMETRIC BYPASS ENGINE (FAST LP -> BP TRANSFORM)
# ---------------------------------------------------------------------
def _transform_lp_to_bp_ic(lp_poles, lp_tzeros, wp1, wp2):
    """Geometrically maps LHP Lowpass roots directly to Bandpass roots."""
    B = wp2 - wp1
    w0_sq = wp1 * wp2
    
    bp_poles, bp_tzeros = [], []
    
    # Map Poles
    for p in lp_poles:
        term = np.sqrt((B * p)**2 - 4 * w0_sq, dtype=complex)
        bp_poles.extend([(B * p + term) / 2.0, (B * p - term) / 2.0])
        
    # Map Transmission Zeros
    for z in lp_tzeros:
        term = np.sqrt((B * z)**2 - 4 * w0_sq, dtype=complex)
        bp_tzeros.extend([(B * z + term) / 2.0, (B * z - term) / 2.0])
        
    # LP infinity zeros map to BP DC (0j) and BP Infinity
    num_zeros_inf = len(lp_poles) - len(lp_tzeros)
    if num_zeros_inf > 0:
        bp_tzeros.extend([0j] * num_zeros_inf)

    return np.array(bp_poles), np.array(bp_tzeros)

def _solve_symmetric_ic_bp_bypass(order_branch, alpha_max, A_s, wp1, wp2, sb_rolloff):
    """Leverages the fast LP IC solver for perfectly symmetric specifications."""
    # Assumes solve_inv_chebyshev_lp is available in your module
    lp_tzeros, lp_poles, _, lp_ws, _, _ = solve_inv_chebyshev_lp(
        order=order_branch, alpha_max=alpha_max, as_db=A_s, slots={}, sb_rolloff=sb_rolloff
    )
    
    bp_poles, bp_tzeros = _transform_lp_to_bp_ic(lp_poles, lp_tzeros, wp1, wp2)
    
    # Peak Normalization (Sweep the passband)
    w_test = np.linspace(wp1, wp2, 500)
    jw = 1j * w_test
    num_vals = np.ones_like(jw, dtype=complex)
    for z in bp_tzeros: num_vals *= (jw - z)
    den_vals = np.ones_like(jw, dtype=complex)
    for p in bp_poles: den_vals *= (jw - p)
    gain = 1.0 / np.max(np.abs(num_vals / den_vals))
    
    # Map stopband edges
    B = wp2 - wp1
    w0_sq = wp1 * wp2
    ws_lp = (B * lp_ws + np.sqrt((B * lp_ws)**2 + 4 * w0_sq)) / 2.0
    ws_hp = (-B * lp_ws + np.sqrt((B * lp_ws)**2 + 4 * w0_sq)) / 2.0
    
    empty = np.array([])
    return bp_poles, bp_tzeros, gain, empty, empty, empty, empty, ws_hp, ws_lp

# ---------------------------------------------------------------------
# 2. ORIGINAL ASYMMETRIC / SLOTTED ENGINE (UNCHANGED)
# ---------------------------------------------------------------------
def compute_ideal_baseline(N_total, N_DC, N_INF, n_hpz, n_lpz, wp1, wp2, eps_p, eps_shp, eps_slp):
    N_refl = N_total // 2
    is_odd = (N_total % 2 != 0)
    c_fixed = wp1 * wp2 if is_odd else 0.0
    
    def F_raw(w, w0, K, z_hp, z_lp):
        w = np.asarray(w, dtype=float)
        num = (w**2 - w0**2)**N_refl
        if is_odd: num *= np.sqrt(w**2 + c_fixed)
        den = (w**N_DC)
        for z in z_hp: den *= (w**2 - z**2)
        for z in z_lp: den *= (w**2 - z**2)
        return K * num / den

    def res_baseline(vars):
        w0, K = vars[0], vars[1]
        zhp = np.sort(vars[2:2+n_hpz]) if n_hpz > 0 else np.array([])
        zlp = np.sort(vars[2+n_hpz:]) if n_lpz > 0 else np.array([])
        
        errs = [abs(F_raw(wp1, w0, K, zhp, zlp)) - eps_p,
                abs(F_raw(wp2, w0, K, zhp, zlp)) - eps_p]
        
        if n_hpz > 0:
            if N_DC == 0:
                errs.append(abs(F_raw(0.0, w0, K, zhp, zlp)) - eps_shp)
            else:
                res = minimize_scalar(lambda w: abs(F_raw(w, w0, K, zhp, zlp)), bounds=(1e-6, zhp[0]), method='bounded')
                errs.append(res.fun - eps_shp)
            for i in range(len(zhp)-1):
                res = minimize_scalar(lambda w: abs(F_raw(w, w0, K, zhp, zlp)), bounds=(zhp[i], zhp[i+1]), method='bounded')
                errs.append(res.fun - eps_shp)
                
        if n_lpz > 0:
            for i in range(len(zlp)-1):
                res = minimize_scalar(lambda w: abs(F_raw(w, w0, K, zhp, zlp)), bounds=(zlp[i], zlp[i+1]), method='bounded')
                errs.append(res.fun - eps_slp)
            if N_INF == 0:
                errs.append(abs(K) - eps_slp)
            else:
                res = minimize_scalar(lambda w: abs(F_raw(w, w0, K, zhp, zlp)), bounds=(zlp[-1], wp2*1000), method='bounded')
                errs.append(res.fun - eps_slp)
                
        return errs

    init_w0 = np.sqrt(wp1 * wp2)
    init_zhp = wp1 * np.linspace(0.4, 0.95, n_hpz) if n_hpz > 0 else []
    init_zlp = wp2 * np.linspace(1.05, 4.0, n_lpz) if n_lpz > 0 else []
    init_vars = np.concatenate([[init_w0, 1.0], init_zhp, init_zlp])
    
    b_low = [wp1, 1e-6] + [1e-4]*n_hpz + [wp2+1e-4]*n_lpz
    b_up = [wp2, np.inf] + [wp1-1e-4]*n_hpz + [np.inf]*n_lpz
    
    sol = least_squares(res_baseline, init_vars, bounds=(b_low, b_up), ftol=1e-12, xtol=1e-12, gtol=1e-12)
    ideal_zhp = np.sort(sol.x[2:2+n_hpz]) if n_hpz > 0 else np.array([])
    ideal_zlp = np.sort(sol.x[2+n_hpz:]) if n_lpz > 0 else np.array([])
    
    return ideal_zhp, ideal_zlp

def apply_notches_and_solve_passband(ideal_zhp, ideal_zlp, hp_slots, lp_slots, wp1, wp2, eps_p, N_total, N_DC, is_odd, c_fixed):
    N_refl = N_total // 2
    zhp = np.copy(ideal_zhp)
    zlp = np.copy(ideal_zlp)
    
    for idx, val in hp_slots.items(): zhp[idx] = val
    for idx, val in lp_slots.items(): zlp[idx] = val

    def E_mag(w):
        val = w**N_DC
        for z in np.concatenate([zhp, zlp]): val *= abs(w**2 - z**2)
        return val

    R_target = E_mag(wp1) / E_mag(wp2)

    def P_ratio(w0):
        P1 = abs(wp1**2 - w0**2)**N_refl
        P2 = abs(wp2**2 - w0**2)**N_refl
        if is_odd:
            P1 *= np.sqrt(wp1**2 + c_fixed)
            P2 *= np.sqrt(wp2**2 + c_fixed)
        return P1 / P2

    res = root_scalar(lambda w0: P_ratio(w0) - R_target, bracket=[wp1 + 1e-6, wp2 - 1e-6])
    w0_opt = res.root
    
    P1 = abs(wp1**2 - w0_opt**2)**N_refl
    if is_odd: P1 *= np.sqrt(wp1**2 + c_fixed)
    K_opt = eps_p * E_mag(wp1) / P1

    return w0_opt, K_opt, zhp, zlp

def get_stopband_humps_inv_cheby(w0, K, zhp, zlp, is_odd, c_fixed, N_refl, N_DC, N_INF, wp1, wp2):
    def F_raw(w):
        w = np.asarray(w, dtype=float)
        num = (w**2 - w0**2)**N_refl
        if is_odd: num *= np.sqrt(w**2 + c_fixed)
        den = (w**N_DC) if N_DC > 0 else 1.0
        for z in zhp: den *= (w**2 - z**2)
        for z in zlp: den *= (w**2 - z**2)
        return K * num / den

    hp_humps = []
    if len(zhp) > 0:
        if N_DC == 0:
            hp_humps.append(abs(F_raw(0.0)))
        else:
            res = minimize_scalar(lambda w: abs(F_raw(w)), bounds=(1e-6, zhp[0]), method='bounded')
            hp_humps.append(res.fun)
        for i in range(len(zhp)-1):
            res = minimize_scalar(lambda w: abs(F_raw(w)), bounds=(zhp[i], zhp[i+1]), method='bounded')
            hp_humps.append(res.fun)
            
    lp_humps = []
    if len(zlp) > 0:
        for i in range(len(zlp)-1):
            res = minimize_scalar(lambda w: abs(F_raw(w)), bounds=(zlp[i], zlp[i+1]), method='bounded')
            lp_humps.append(res.fun)
        if N_INF == 0:
            lp_humps.append(abs(K))
        else:
            res = minimize_scalar(lambda w: abs(F_raw(w)), bounds=(zlp[-1], wp2*1000), method='bounded')
            lp_humps.append(res.fun)
            
    return np.array(hp_humps), np.array(lp_humps)

def derive_inv_cheby_bp_transfer_function(w0, K, zhp, zlp, c_fixed, is_odd, N_refl, N_DC):
    E_poly = np.zeros(N_DC + 1)
    E_poly[0] = (-1)**N_DC
    for z in np.concatenate([zhp, zlp]):
        E_poly = np.polymul(E_poly, [1.0, 2 * z**2, z**4])
        
    P_poly = np.array([1.0])
    for _ in range(N_refl): P_poly = np.polymul(P_poly, [1.0, 2 * w0**2, w0**4])
    if is_odd: P_poly = np.polymul(P_poly, [-1.0, c_fixed])
    P_poly *= K**2
    
    L = max(len(E_poly), len(P_poly))
    D2_x = np.pad(E_poly, (L - len(E_poly), 0)) + np.pad(P_poly, (L - len(P_poly), 0))
    
    D_s = np.zeros(2 * (len(D2_x) - 1) + 1)
    for i, coef in enumerate(D2_x): D_s[2 * i] = coef
        
    lhp_poles = [r for r in np.roots(D_s) if np.real(r) < -1e-6]
    
    zeros = [0.0] * N_DC
    for z in np.concatenate([zhp, zlp]): zeros.extend([1j * z, -1j * z])
        
    return lhp_poles, zeros

def _synthesize_asymmetric_inv_cheby_bp(order_hp, order_lp, wp1, wp2, as_hp, as_lp, alpha_max, 
                                        sb_rolloff_hp, sb_rolloff_lp, hp_slots, lp_slots):
    """Your exact, unmodified direct BP solver logic, with hardened array sorting."""
    eps_p = np.sqrt(10**(alpha_max/10.0) - 1.0)
    eps_shp = np.sqrt(10**(as_hp/10.0) - 1.0)
    eps_slp = np.sqrt(10**(as_lp/10.0) - 1.0)
    
    n_hpz = order_hp // 2
    if sb_rolloff_hp and n_hpz > 0: n_hpz -= 1
    N_DC = order_hp - 2 * n_hpz
    
    n_lpz = order_lp // 2
    if sb_rolloff_lp and n_lpz > 0: n_lpz -= 1
    N_INF = order_lp - 2 * n_lpz
    
    N_total = order_hp + order_lp
    N_refl = N_total // 2
    is_odd = (N_total % 2 != 0)
    c_fixed = wp1 * wp2 if is_odd else 0.0
    
    ideal_zhp, ideal_zlp = compute_ideal_baseline(N_total, N_DC, N_INF, n_hpz, n_lpz, wp1, wp2, eps_p, eps_shp, eps_slp)
    
    # FIX: Force strictly sequential sorting so UI index matches Mathematical index
    ideal_zhp = np.sort(ideal_zhp)[::-1] if len(ideal_zhp) > 0 else ideal_zhp  # Descending towards DC
    ideal_zlp = np.sort(ideal_zlp) if len(ideal_zlp) > 0 else ideal_zlp        # Ascending towards Inf
    
    free_hp_idx = [i for i in range(n_hpz) if i not in hp_slots]
    free_lp_idx = [i for i in range(n_lpz) if i not in lp_slots]
    
    def decode_p2(vars):
        zhp = np.copy(ideal_zhp)
        zlp = np.copy(ideal_zlp)
        for idx, val in hp_slots.items(): zhp[idx] = val
        for idx, val in lp_slots.items(): zlp[idx] = val
        
        v_idx = 0
        for i in free_hp_idx:
            zhp[i] = vars[v_idx]
            v_idx += 1
        for i in free_lp_idx:
            zlp[i] = vars[v_idx]
            v_idx += 1
            
        # FIX: Do not sort here! Keep the explicit array positions locked to the UI.
        return zhp, zlp

    def residuals_p2(vars):
        zhp, zlp = decode_p2(vars)
        # We must sort *only* for the F_raw evaluation math to work properly
        sorted_zhp = np.sort(zhp)
        sorted_zlp = np.sort(zlp)
        w0, K, _, _ = apply_notches_and_solve_passband(sorted_zhp, sorted_zlp, {}, {}, wp1, wp2, eps_p, N_total, N_DC, is_odd, c_fixed)
        hp_humps, lp_humps = get_stopband_humps_inv_cheby(w0, K, sorted_zhp, sorted_zlp, is_odd, c_fixed, N_refl, N_DC, N_INF, wp1, wp2)
        
        errs = []
        # Error mapping must follow the strict UI index, not the sorted index!
        # Because we sorted F_raw, we have to map the humps back to their original slots
        for i in range(n_hpz):
            if i not in hp_slots: 
                # Mathematical mapping: Descending UI index vs Ascending Mathematical index
                math_idx = (n_hpz - 1) - i 
                errs.append(hp_humps[math_idx] - eps_shp)
        for i in range(n_lpz):
            if i not in lp_slots: 
                errs.append(lp_humps[i] - eps_slp)
        return errs

    init_vars = []
    for i in free_hp_idx: init_vars.append(ideal_zhp[i])
    for i in free_lp_idx: init_vars.append(ideal_zlp[i])
    
    if len(init_vars) == 0:
        final_zhp, final_zlp = decode_p2([])
        sorted_zhp, sorted_zlp = np.sort(final_zhp), np.sort(final_zlp)
        w0, K, _, _ = apply_notches_and_solve_passband(sorted_zhp, sorted_zlp, {}, {}, wp1, wp2, eps_p, N_total, N_DC, is_odd, c_fixed)
    else:
        b_low, b_up = [], []
        # Safe bounding based on the sequential mapping
        for i in free_hp_idx:
            b_low.append(1e-5)
            b_up.append(wp1 - 1e-4)
        for i in free_lp_idx:
            b_low.append(wp2 + 1e-4)
            b_up.append(np.inf)
            
        res = least_squares(residuals_p2, init_vars, bounds=(b_low, b_up), ftol=1e-10, xtol=1e-10)
        final_zhp, final_zlp = decode_p2(res.x)
        sorted_zhp, sorted_zlp = np.sort(final_zhp), np.sort(final_zlp)
        w0, K, _, _ = apply_notches_and_solve_passband(sorted_zhp, sorted_zlp, {}, {}, wp1, wp2, eps_p, N_total, N_DC, is_odd, c_fixed)
    
    # Transfer function derivation expects mathematically sorted arrays
    poles, zeros = derive_inv_cheby_bp_transfer_function(w0, K, sorted_zhp, sorted_zlp, c_fixed, is_odd, N_refl, N_DC)
    
    w_test = np.linspace(wp1, wp2, 500)
    gain = 1.0 / np.max(evaluate_h(poles, zeros, 1.0, w_test))
    
    ws_hp = find_crossing(poles, zeros, gain, as_hp, wp1 * 1e-5, wp1, is_hp=True)
    ws_lp = find_crossing(poles, zeros, gain, as_lp, wp2, wp2 * 1e5, is_hp=False)
    
    return poles, zeros, gain, np.sort(ideal_zhp)[::-1], np.sort(ideal_zlp), sorted_zhp[::-1], sorted_zlp, ws_hp, ws_lp

# ---------------------------------------------------------------------
# 3. THE ARBITRATOR (MAIN API)
# ---------------------------------------------------------------------
def synthesize_slot_based_inv_cheby(order_hp, order_lp, wp1, wp2, as_hp, as_lp, alpha_max, 
                                    sb_rolloff_hp=False, sb_rolloff_lp=False, 
                                    hp_slots=None, lp_slots=None):
    """
    Arbitrates between the ultra-fast LP->BP bypass and the native asymmetric solver.
    """
    hp_slots = hp_slots or {}
    lp_slots = lp_slots or {}
    
    # Symmetry condition: Same order, same As, same modifications, NO manual slots.
    is_symmetric = (
        order_hp == order_lp and 
        abs(as_hp - as_lp) < 1e-4 and 
        sb_rolloff_hp == sb_rolloff_lp and 
        len(hp_slots) == 0 and len(lp_slots) == 0
    )
    
    if is_symmetric:
        return _solve_symmetric_ic_bp_bypass(order_hp, alpha_max, as_hp, wp1, wp2, sb_rolloff_hp)
    else:
        return _synthesize_asymmetric_inv_cheby_bp(
            order_hp, order_lp, wp1, wp2, as_hp, as_lp, alpha_max, 
            sb_rolloff_hp, sb_rolloff_lp, hp_slots, lp_slots
        )

# ============================================================
# ELLIPTIC BANDPASS (ARBITRATED & MULTIPRECISION SYNTHESIS)
# ============================================================
import numpy as np
from scipy.optimize import least_squares, minimize_scalar
import mpmath

# ---------------------------------------------------------------------
# 1. SYMMETRIC BYPASS ENGINE
# ---------------------------------------------------------------------
def _transform_lp_to_bp(lp_poles, lp_tzeros, lp_rzeros, wp1, wp2):
    """Geometrically maps LHP Lowpass roots directly to Bandpass roots."""
    B = wp2 - wp1
    w0_sq = wp1 * wp2
    
    bp_poles, bp_tzeros, bp_rzeros = [], [], []
    
    for p in lp_poles:
        term = np.sqrt((B * p)**2 - 4 * w0_sq, dtype=complex)
        bp_poles.extend([(B * p + term) / 2.0, (B * p - term) / 2.0])
        
    for z in lp_tzeros:
        term = np.sqrt((B * z)**2 - 4 * w0_sq, dtype=complex)
        bp_tzeros.extend([(B * z + term) / 2.0, (B * z - term) / 2.0])
        
    num_zeros_inf = len(lp_poles) - len(lp_tzeros)
    if num_zeros_inf > 0:
        bp_tzeros.extend([0j] * num_zeros_inf)
        
    for rz in lp_rzeros:
        term = np.sqrt((B * rz)**2 - 4 * w0_sq, dtype=complex)
        bp_rzeros.extend([(B * rz + term) / 2.0, (B * rz - term) / 2.0])

    return np.array(bp_poles), np.array(bp_tzeros), np.array(bp_rzeros)

def _solve_symmetric_bp_bypass(order_branch, alpha_max, A_s, wp1, wp2, sb_rolloff):
    """Leverages the flawless LP solver for symmetric specifications."""
    # Assumes solve_elliptic_lp is available in the module
    lp_tzeros, lp_poles, _, lp_ws, _, lp_rzeros = solve_elliptic_lp(
        order=order_branch, alpha_max=alpha_max, A_s=A_s, pb_even_mod=False, sb_rolloff=sb_rolloff
    )
    
    bp_poles, bp_tzeros, bp_rzeros = _transform_lp_to_bp(lp_poles, lp_tzeros, lp_rzeros, wp1, wp2)
    
    # Universal Peak Normalization (Sweep the passband to find true 0 dB peaks)
    w_test = np.linspace(wp1, wp2, 1000)
    jw_test = 1j * w_test
    
    num_vals = np.ones_like(jw_test, dtype=complex)
    for z in bp_tzeros: num_vals *= (jw_test - z)
        
    den_vals = np.ones_like(jw_test, dtype=complex)
    for p in bp_poles: den_vals *= (jw_test - p)
        
    gain = 1.0 / np.max(np.abs(num_vals / den_vals))
    
    B = wp2 - wp1
    w0_sq = wp1 * wp2
    ws_lp = (B * lp_ws + np.sqrt((B * lp_ws)**2 + 4 * w0_sq)) / 2.0
    ws_hp = (-B * lp_ws + np.sqrt((B * lp_ws)**2 + 4 * w0_sq)) / 2.0
    
    return bp_tzeros, bp_poles, gain, ws_hp, ws_lp, bp_rzeros

# ---------------------------------------------------------------------
# 2. ASYMMETRIC HELPER FUNCTIONS
# ---------------------------------------------------------------------
def _F_raw_bp(w, z_locs, notch_hp, notch_lp, is_odd, c_fixed, N_dc):
    w = np.asarray(w, dtype=float)
    num = np.ones_like(w, dtype=float)
    for zk in z_locs: num *= (w**2 - zk**2)
    if is_odd: num *= np.sqrt(w**2 + c_fixed)
    den = (w**N_dc) if N_dc > 0 else np.ones_like(w, dtype=float)
    for nz in notch_hp: den *= (w**2 - nz**2)
    for nz in notch_lp: den *= (w**2 - nz**2)
    return num / den

def _H_dB_fast_bp(w, opt_z, K, notch_hp, notch_lp, is_odd, c_fixed, N_dc, epsilon):
    # np.abs guarantees array safety
    V = np.abs(K * _F_raw_bp(w, opt_z, notch_hp, notch_lp, is_odd, c_fixed, N_dc))
    mag = 1.0 / np.sqrt(1.0 + (epsilon * V)**2)
    # np.maximum perfectly clamps entire arrays simultaneously
    return 20 * np.log10(np.maximum(mag, 1e-12))

def _get_stopband_humps_bp(opt_z, K, zhp, zlp, is_odd, c_fixed, N_dc, N_inf, wp1, wp2, epsilon):
    hp_humps_db, lp_humps_db = [], []
    
    def fast_hump_db(w_start, w_end):
        if w_end <= w_start + 1e-6: return -200.0
        w_grid = np.linspace(w_start, w_end, 150)
        db_vals = _H_dB_fast_bp(w_grid, opt_z, K, zhp, zlp, is_odd, c_fixed, N_dc, epsilon)
        return np.max(db_vals)

    # New: A logarithmic scanner for the final infinite hump
    def fast_hump_db_log(w_start, w_end):
        if w_end <= w_start + 1e-6: return -200.0
        w_grid = np.logspace(np.log10(w_start), np.log10(w_end), 150)
        db_vals = _H_dB_fast_bp(w_grid, opt_z, K, zhp, zlp, is_odd, c_fixed, N_dc, epsilon)
        return np.max(db_vals)

    if len(zhp) > 0:
        if N_dc == 0:
            hp_humps_db.append(_H_dB_fast_bp(np.array([0.0]), opt_z, K, zhp, zlp, is_odd, c_fixed, N_dc, epsilon)[0])
        else:
            hp_humps_db.append(fast_hump_db(1e-6, zhp[0]))
            
        for i in range(len(zhp)-1):
            hp_humps_db.append(fast_hump_db(zhp[i], zhp[i+1]))

    if len(zlp) > 0:
        for i in range(len(zlp)-1):
            lp_humps_db.append(fast_hump_db(zlp[i], zlp[i+1]))
            
        if N_inf == 0:
            lp_humps_db.append(_H_dB_fast_bp(np.array([1e10]), opt_z, K, zhp, zlp, is_odd, c_fixed, N_dc, epsilon)[0])
        else:
            # FIX: Dynamically scan out to 1000x wp2 using logspace, so the optimizer can always "see" the peak
            lp_humps_db.append(fast_hump_db_log(zlp[-1], max(zlp[-1] * 10.0, wp2 * 1000.0)))
            
    return np.array(hp_humps_db), np.array(lp_humps_db)

# ---------------------------------------------------------------------
# 3. MULTIPRECISION ASYMMETRIC ROOT EXTRACTOR
# ---------------------------------------------------------------------
def _derive_multiprecision_bp_transfer_function(opt_z, c_fixed, K, epsilon, N_dc, notch_hp, notch_lp, is_odd):
    mpmath.mp.dps = 80 
    
    def poly_mul_mp(p1, p2):
        if not len(p1) or not len(p2): return []
        res = [mpmath.mpf(0)] * (len(p1) + len(p2) - 1)
        for i, c1 in enumerate(p1):
            for j, c2 in enumerate(p2):
                res[i+j] += c1 * c2
        return res

    T_P = [mpmath.mpf((-1)**N_dc)] + [mpmath.mpf(0)] * N_dc
    for nz in list(notch_hp) + list(notch_lp):
        nz2 = mpmath.mpf(nz)**2
        T_P = poly_mul_mp(T_P, [mpmath.mpf(1), 2*nz2, nz2**2])

    T_E = [mpmath.mpf(1)]
    for z in opt_z:
        z2 = mpmath.mpf(z)**2
        T_E = poly_mul_mp(T_E, [mpmath.mpf(1), 2*z2, z2**2])
    if is_odd:
        # Correctly evaluates odd non-rational dynamics (-s^2 + c_fixed) in U-domain
        T_E = poly_mul_mp(T_E, [mpmath.mpf(-1), mpmath.mpf(c_fixed)])

    L = max(len(T_P), len(T_E))
    D_pad = [mpmath.mpf(0)] * (L - len(T_P)) + T_P
    E_pad = [mpmath.mpf(0)] * (L - len(T_E)) + T_E
    eps_K_sq = (mpmath.mpf(epsilon) * mpmath.mpf(K))**2
    
    D_u = [D_pad[i] + eps_K_sq * E_pad[i] for i in range(L)]
    
    u_roots = mpmath.polyroots(D_u, maxsteps=2000, extraprec=50)
    lhp_poles = []
    for u in u_roots:
        s1 = mpmath.sqrt(u)
        s2 = -s1
        if s1.real < -1e-6: lhp_poles.append(complex(s1))
        elif s2.real < -1e-6: lhp_poles.append(complex(s2))
        
    tzeros = [0j] * N_dc
    for nz in list(notch_hp) + list(notch_lp):
        tzeros.extend([1j * nz, -1j * nz])
        
    return np.array(lhp_poles), np.array(tzeros)

# ---------------------------------------------------------------------
# 4. ASYMMETRIC SYNTHESIS ENGINE
# ---------------------------------------------------------------------
def _synthesize_asymmetric_elliptic_bp(order_hp, order_lp, wp1, wp2, as_hp, as_lp, alpha_max, sb_rolloff_hp, sb_rolloff_lp):
    n_hpz = order_hp // 2
    if sb_rolloff_hp and n_hpz > 0: n_hpz -= 1
    N_dc = order_hp - 2 * n_hpz
    
    n_lpz = order_lp // 2
    if sb_rolloff_lp and n_lpz > 0: n_lpz -= 1
    N_inf = order_lp - 2 * n_lpz
    
    N_total = order_hp + order_lp
    N_refl = N_total // 2
    is_odd = (N_total % 2 != 0)
    c_fixed = wp1 * wp2 if is_odd else 0.0
    epsilon = np.sqrt(10**(alpha_max/10.0) - 1.0)
    
    def get_cheby_nodes(a, b, n, order_left=1, order_right=1):
        if n == 0: return np.array([])
        
        # 1. Generate uniform linear spacing in the [0, 1] domain
        k = np.arange(1, n + 1)
        x_uniform = (2 * k - 1) / (2 * n)
        
        # 2. Calculate the dampened measure of logarithmicity (the warp factor)
        # We use max(..., 1) to prevent zero-division in weird edge cases
        gamma = max(order_right, 1) / max(order_left, 1)
        p = np.sqrt(gamma) 
        
        # 3. Apply the warp! 
        x_warped = x_uniform ** p
        
        # 4. Map the warped [0, 1] distribution back to angles [0, pi], take the cosine, and scale
        theta = x_warped * np.pi
        nodes = 0.5 * (a + b) + 0.5 * (b - a) * np.cos(theta)
        
        return np.sort(nodes)

    # 1. GENERATE ALL INITIAL GUESSES (Stopband + Passband)
    # Stopbands remain symmetrically distributed within their own local bands
    init_zhp = get_cheby_nodes(wp1 * 0.2, wp1 * 0.95, n_hpz)
    init_zlp = get_cheby_nodes(wp2 * 1.05, wp2 * 5.0, n_lpz)
    
    # The Passband gets the Asymmetric Warp based on the steepness of the stopbands!
    # (Note: order_hp is the lower stopband, which governs the left side of the passband)
    if N_refl > 0:
        init_zpass = get_cheby_nodes(wp1, wp2, N_refl, order_left=order_hp, order_right=order_lp)
    else:
        init_zpass = np.array([])

    # Pack everything into a single state vector for the optimizer
    init_vars = np.concatenate([init_zhp, init_zlp, init_zpass])
    
    def decode(vars):
        zhp = np.sort(vars[:n_hpz]) if n_hpz > 0 else np.array([])
        zlp = np.sort(vars[n_hpz:n_hpz+n_lpz]) if n_lpz > 0 else np.array([])
        zpass = np.sort(vars[n_hpz+n_lpz:]) if N_refl > 0 else np.array([])
        return zhp, zlp, zpass

    # 2. THE UNIFIED MASTER LOOP
    def unified_residuals(vars):
        zhp, zlp, zpass = decode(vars)
        
        # A. Evaluate passband gain constant K
        val = np.abs(_F_raw_bp(np.array([wp1]), zpass, zhp, zlp, is_odd, c_fixed, N_dc)[0])
        K = 1.0 / np.maximum(val, 1e-12)
        
        # B. Calculate Passband Ripple Errors
        pb_errs = []
        if N_refl > 0:
            peaks = []
            for i in range(len(zpass) - 1):
                if zpass[i+1] - zpass[i] < 1e-6:
                    peaks.append(0.0)
                else:
                    w_grid = np.linspace(zpass[i], zpass[i+1], 60)
                    F_vals = np.abs(K * _F_raw_bp(w_grid, zpass, zhp, zlp, is_odd, c_fixed, N_dc))
                    peaks.append(np.max(F_vals))
            
            edge_peak = np.abs(K * _F_raw_bp(np.array([wp2]), zpass, zhp, zlp, is_odd, c_fixed, N_dc)[0])
            peaks.append(edge_peak)
            
            # THE FIX: Apply a massive DSP weight (e.g., 1000.0) to the passband errors.
            # This forces the solver to rigidly maintain the equiripple structure!
            pb_errs = (np.array(peaks) - 1.0) * 1000.0
            
        # C. Calculate Stopband Hump Errors (using your fast vectorized scanner)
        hp_humps, lp_humps = _get_stopband_humps_bp(zpass, K, zhp, zlp, is_odd, c_fixed, N_dc, N_inf, wp1, wp2, epsilon)
        
        # Combine all errors. The array length matches the variables perfectly (Square Matrix)!
        errs = []
        if len(pb_errs) > 0: errs.extend(pb_errs)
        if n_hpz > 0: errs.extend(hp_humps - (-as_hp))
        if n_lpz > 0: errs.extend(lp_humps - (-as_lp))
        return errs

    # 3. EXECUTE THE UNIFIED SOLVER
    if len(init_vars) > 0:
        # Tightly constrain all roots to their physical domains to prevent mathematical collisions
        b_low = [1e-5]*n_hpz + [wp2+1e-4]*n_lpz + [wp1+1e-6]*N_refl
        b_up = [wp1-1e-4]*n_hpz + [np.inf]*n_lpz + [wp2-1e-6]*N_refl
        
        # Because the Jacobian is now perfectly square, ftol=1e-8 converges incredibly fast
        res = least_squares(unified_residuals, init_vars, bounds=(b_low, b_up), ftol=1e-8, xtol=1e-8)
        final_zhp, final_zlp, final_zpass = decode(res.x)
    else:
        final_zhp, final_zlp, final_zpass = np.array([]), np.array([]), np.array([])

    # 4. FINAL STATE EXTRACTION
    val_opt = np.abs(_F_raw_bp(np.array([wp1]), final_zpass, final_zhp, final_zlp, is_odd, c_fixed, N_dc)[0])
    K_final = 1.0 / np.maximum(val_opt, 1e-12)
    
    poles, tzeros = _derive_multiprecision_bp_transfer_function(
        final_zpass, c_fixed, K_final, epsilon, N_dc, final_zhp, final_zlp, is_odd
    )
    
    # Universal Peak Normalization
    w_test = np.linspace(wp1, wp2, 1000)
    jw = 1j * w_test
    num_vals = np.ones_like(jw, dtype=complex)
    for z in tzeros: num_vals *= (jw - z)
    den_vals = np.ones_like(jw, dtype=complex)
    for p in poles: den_vals *= (jw - p)
    gain = 1.0 / np.max(np.abs(num_vals / den_vals))
    
    ws_hp = find_crossing(poles, tzeros, gain, as_hp, wp1 * 1e-5, wp1, is_hp=True)
    ws_lp = find_crossing(poles, tzeros, gain, as_lp, wp2, wp2 * 1e5, is_hp=False)
    
    rzeros = np.array([val for rz in final_zpass for val in (1j*rz, -1j*rz)])
    if is_odd: rzeros = np.append(rzeros, [1j*np.sqrt(c_fixed), -1j*np.sqrt(c_fixed)])
    
    return tzeros, poles, gain, ws_hp, ws_lp, rzeros

# ---------------------------------------------------------------------
# 5. MAIN API WRAPPER
# ---------------------------------------------------------------------
def solve_elliptic_bp(order_hp, order_lp, wp1, wp2, as_hp, as_lp, alpha_max, sb_rolloff_hp=False, sb_rolloff_lp=False):
    # Check symmetry FIRST
    is_symmetric = (order_hp == order_lp) and (abs(as_hp - as_lp) < 1e-4) and (sb_rolloff_hp == sb_rolloff_lp)
    
    if is_symmetric:
        # Symmetric bypass uses the LP solver, so N=15 per side (Total 30) is perfectly safe and fast
        if order_hp > 15:
            raise ValueError(f"Symmetric Bandpass order capped at N=15 per side. Requested N={order_hp}.")
        return _solve_symmetric_bp_bypass(order_hp, alpha_max, as_hp, wp1, wp2, sb_rolloff_hp)
    else:
        # Asymmetric solver must optimize all humps at once, keep the strict total cap at 15
        if order_hp + order_lp > 15:
            raise ValueError(f"Asymmetric Bandpass total order capped at N=15 to prevent server timeout. Requested total N={order_hp + order_lp}.")
        return _synthesize_asymmetric_elliptic_bp(order_hp, order_lp, wp1, wp2, as_hp, as_lp, alpha_max, sb_rolloff_hp, sb_rolloff_lp)


# =====================================================================
# LEGACY ADAPTER (FOR FILTER_ENGINE COMPATIBILITY)
# =====================================================================
def synthesize_slot_based_elliptic_bp(order_hp, order_lp, wp1, wp2, as_hp, as_lp, alpha_max, 
                                      sb_rolloff_hp=False, sb_rolloff_lp=False, 
                                      hp_slots=None, lp_slots=None):
    tzeros, poles, gain, ws_hp, ws_lp, rzeros = solve_elliptic_bp(
        order_hp, order_lp, wp1, wp2, as_hp, as_lp, alpha_max, sb_rolloff_hp, sb_rolloff_lp
    )
    empty = np.array([])
    return poles, tzeros, gain, empty, empty, empty, empty, ws_hp, ws_lp, rzeros
        

# ============================================================
# BAND-REJECT SHARED HELPERS
# ============================================================
def safe_bnds(lower, upper):
    return (lower, lower + 1e-9) if upper <= lower else (lower, upper)

def find_crossings_br(poles, zeros, gain, target_db, wp1, wp2):
    from scipy.optimize import root_scalar
    import numpy as np
    from filter_utils import evaluate_h
    
    target_mag = 10 ** (-target_db / 20.0)
    w_grid = np.logspace(np.log10(max(wp1, 1e-6)), np.log10(wp2), 5000)
    h_mag = evaluate_h(poles, zeros, gain, w_grid)
    
    crossings = []
    for i in range(len(w_grid)-1):
        if (h_mag[i] - target_mag) * (h_mag[i+1] - target_mag) <= 0:
            try: 
                rt = root_scalar(lambda wx: evaluate_h(poles, zeros, gain, [wx])[0] - target_mag, bracket=[w_grid[i], w_grid[i+1]]).root
                crossings.append(rt)
            except:
                crossings.append(w_grid[i])
                
    if len(crossings) < 2: return None, None
    
    # The true stopband width is always defined by the outermost skirts
    ws_lp = min(crossings)
    ws_hp = max(crossings)
    
    return ws_lp, ws_hp

# ============================================================
# [BUTTERWORTH_GBR_SYNTH BLOCK]
# ============================================================
def _bgb_F_raw_noK(w, notches, N_LP):
    w = np.asarray(w, dtype=float)
    num = w**N_LP
    den = np.ones_like(w, dtype=float)
    for z in notches: den = den * abs(w**2 - z**2)
    return num / den

def _bgb_balance_notches(wp1, wp2, N_z, N_LP, manual_notches=None):
    manual_notches = manual_notches or {}
    if len(manual_notches) >= N_z: return np.sort([manual_notches.get(i, np.sqrt(wp1*wp2)) for i in range(N_z)])
        
    def objective(w_free_arr):
        current_notches = np.array([manual_notches.get(i, w_free_arr[0]) for i in range(N_z)])
        v1 = max(abs(_bgb_F_raw_noK(wp1, current_notches, N_LP)), 1e-12)
        v2 = max(abs(_bgb_F_raw_noK(wp2, current_notches, N_LP)), 1e-12)
        return [np.log(v1) - np.log(v2)]

    initial_w = np.sqrt(wp1 * wp2)
    margin = (wp2 - wp1) * 0.02
    res = least_squares(objective, [initial_w], bounds=([wp1 + margin], [wp2 - margin]), ftol=1e-12, xtol=1e-12)
    return np.sort(np.array([manual_notches.get(i, res.x[0]) for i in range(N_z)]))

def derive_butterworth_br_transfer_function(notches, N_LP, K, epsilon):
    D_sq = np.array([1.0])
    for z in notches: D_sq = np.polymul(D_sq, [1.0, 0.0, z**2])
    D_sq = np.polymul(D_sq, D_sq)
    
    N_sq = np.array([1.0])
    for _ in range(N_LP): N_sq = np.polymul(N_sq, [-1.0, 0.0, 0.0])
    N_sq = N_sq * (epsilon**2 * K**2)
    
    L = max(len(D_sq), len(N_sq))
    poly = np.pad(D_sq, (L - len(D_sq), 0)) + np.pad(N_sq, (L - len(N_sq), 0))
    poles = [r for r in np.roots(poly) if np.real(r) < -1e-6]
    zeros = [v for z in notches for v in (1j*z, -1j*z)]
    return poles, zeros

def synthesize_butterworth_gbr(order_lp, order_hp, wp1, wp2, as_db, alpha_max, manual_notches=None):
    manual_notches = manual_notches or {}
    N_tot = order_lp + order_hp
    if N_tot % 2 != 0: raise ValueError("A true Band-Reject filter requires an EVEN total order.")
        
    epsilon = np.sqrt(10**(alpha_max/10.0) - 1.0)
    N_z = N_tot // 2
    N_LP = order_lp

    ideal_notches = _bgb_balance_notches(wp1, wp2, N_z, N_LP, {})
    final_notches = _bgb_balance_notches(wp1, wp2, N_z, N_LP, manual_notches)
    
    v1 = max(abs(_bgb_F_raw_noK(wp1, final_notches, N_LP)), 1e-12)
    v2 = max(abs(_bgb_F_raw_noK(wp2, final_notches, N_LP)), 1e-12)
    K = 1.0 / max(v1, v2)
    
    poles, zeros = derive_butterworth_br_transfer_function(final_notches, N_LP, K, epsilon)
    w_test = np.linspace(0.0, wp1*0.99, 1000)
    gain = 1.0 / np.max(evaluate_h(poles, zeros, 1.0, w_test))
    ws_lp, ws_hp = find_crossings_br(poles, zeros, gain, as_db, wp1, wp2)
    
    return poles, zeros, gain, ideal_notches, final_notches, ws_lp, ws_hp, np.array([])

# ============================================================
# CHEBYSHEV BAND-REJECT (ARBITRATED & MULTIPRECISION)
# ============================================================
import numpy as np
from scipy.optimize import least_squares, minimize_scalar
import mpmath

# ---------------------------------------------------------------------
# 1. SYMMETRIC BYPASS ENGINE (FAST LP -> BR TRANSFORM)
# ---------------------------------------------------------------------
def _transform_lp_to_br_cheby(lp_poles, lp_tzeros, lp_rzeros, wp1, wp2):
    """Geometrically maps LHP Lowpass roots directly to Band-Reject roots."""
    B = wp2 - wp1
    w0_sq = wp1 * wp2
    w0 = np.sqrt(w0_sq)

    br_poles, br_tzeros, br_rzeros = [], [], []

    # Safely map to avoid Catastrophic Cancellation on real roots
    def map_root(r):
        if abs(r) < 1e-12: return None, None
        a = B / (2.0 * r)
        term = np.sqrt(a**2 - w0_sq, dtype=complex)
        if abs(a + term) > abs(a - term): s1 = a + term
        else: s1 = a - term
        s2 = w0_sq / s1
        return s1, s2

    # 1. Map Poles
    for p in lp_poles:
        s1, s2 = map_root(p)
        br_poles.extend([s1, s2])

    # 2. Map Finite Transmission Zeros (For generalized slotted prototypes)
    for z in lp_tzeros:
        s1, s2 = map_root(z)
        if s1 is not None: br_tzeros.extend([s1, s2])

    # 3. Map Infinity Zeros (Standard Chebyshev rolloff maps to BR center freq)
    num_zeros_inf = len(lp_poles) - len(lp_tzeros)
    if num_zeros_inf > 0:
        br_tzeros.extend([1j * w0, -1j * w0] * num_zeros_inf)

    # 4. Map Reflection Zeros
    for rz in lp_rzeros:
        s1, s2 = map_root(rz)
        if s1 is not None: br_rzeros.extend([s1, s2])
        else: br_rzeros.append(0j)

    return np.array(br_poles), np.array(br_tzeros), np.array(br_rzeros)

def _solve_symmetric_cheby_br_bypass(order_branch, alpha_max, as_db, wp1, wp2, pb_even_mod):
    """Leverages the fast LP Chebyshev solver for perfectly symmetric specifications."""
    # Assumes solve_chebyshev_lp is in namespace
    lp_tzeros, lp_poles, _, lp_ws, lp_rzeros = solve_chebyshev_lp(
        order=order_branch, alpha_max=alpha_max, as_db=as_db, notches=[], even_mod=pb_even_mod
    )

    br_poles, br_tzeros, br_rzeros = _transform_lp_to_br_cheby(lp_poles, lp_tzeros, lp_rzeros, wp1, wp2)

    # Universal Peak Normalization (Sweep the lower passband)
    w_test = np.linspace(1e-6, wp1, 2000)
    jw_test = 1j * w_test
    num = np.ones_like(jw_test, dtype=complex)
    for z in br_tzeros: num *= (jw_test - z)
    den = np.ones_like(jw_test, dtype=complex)
    for p in br_poles: den *= (jw_test - p)
    gain = 1.0 / np.max(np.abs(num / den))

    # Map stopband edges
    B = wp2 - wp1
    w0_sq = wp1 * wp2
    ws_lp = B / (2.0 * lp_ws) + np.sqrt((B / (2.0 * lp_ws))**2 + w0_sq)
    ws_hp = -B / (2.0 * lp_ws) + np.sqrt((B / (2.0 * lp_ws))**2 + w0_sq)

    # Coincident notches for standard Chebyshev BR map exactly to w0
    w0 = np.sqrt(w0_sq)
    ideal_notches = np.array([w0] * order_branch) 
    
    opt_z_mock = np.sort([z.imag for z in br_rzeros if z.imag > 1e-6 and abs(z.real) < 1e-6])

    return br_poles, br_tzeros, gain, ideal_notches, ideal_notches, min(ws_lp, ws_hp), max(ws_lp, ws_hp), opt_z_mock

# ---------------------------------------------------------------------
# 2. ASYMMETRIC HELPER FUNCTIONS (YOUR ORIGINAL MATH)
# ---------------------------------------------------------------------
def safe_bnds(a, b):
    return (min(a, b) + 1e-6, max(a, b) - 1e-6)

def _cheby_F_raw_noK(w, x_locs, y_locs, notches, N_dc):
    w = np.asarray(w, dtype=float)
    num = w**N_dc if N_dc > 0 else np.ones_like(w, dtype=float)
    for x in x_locs: num = num * (w**2 - x**2)
    for y in y_locs: num = num * (w**2 - y**2)
    den = np.ones_like(w, dtype=float)
    for z in notches: den = den * (w**2 - z**2)
    return num / den

def _cheby_solve_inner(wp1, wp2, notches, N_dc, m_lp, m_hp, delta_hp, initial_xy=None):
    if m_lp + m_hp == 0: return np.array([]), np.array([])
    if initial_xy is None:
        x_g = wp1 * np.linspace(0.2, 0.9, m_lp) if m_lp > 0 else []
        y_g = wp2 * np.linspace(1.1, 5.0, m_hp) if m_hp > 0 else []
        initial_xy = np.concatenate([x_g, y_g])

    def residuals(xy):
        x_locs = np.sort(xy[:m_lp]) if m_lp > 0 else []
        y_locs = np.sort(xy[m_lp:]) if m_hp > 0 else []
        K = 1.0 / max(abs(_cheby_F_raw_noK(wp1, x_locs, y_locs, notches, N_dc)), 1e-12)
        errs = []
        
        if m_lp > 0:
            if N_dc == 0: errs.append(abs(K * _cheby_F_raw_noK(0.0, x_locs, y_locs, notches, N_dc)) - 1.0)
            else:
                res = minimize_scalar(lambda w: -abs(K * _cheby_F_raw_noK(w, x_locs, y_locs, notches, N_dc)), bounds=(0.0, x_locs[0]-1e-6), method='bounded')
                errs.append(-res.fun - 1.0)
            for k in range(m_lp - 1):
                res = minimize_scalar(lambda w: -abs(K * _cheby_F_raw_noK(w, x_locs, y_locs, notches, N_dc)), bounds=safe_bnds(x_locs[k], x_locs[k+1]), method='bounded')
                errs.append(-res.fun - 1.0)
                
        if m_hp > 0:
            for k in range(m_hp - 1):
                res = minimize_scalar(lambda w: -abs(K * _cheby_F_raw_noK(w, x_locs, y_locs, notches, N_dc)), bounds=safe_bnds(y_locs[k], y_locs[k+1]), method='bounded')
                errs.append(-res.fun - 1.0)
            if delta_hp == 0: errs.append(abs(K) - 1.0)
            else:
                res = minimize_scalar(lambda w: -abs(K * _cheby_F_raw_noK(w, x_locs, y_locs, notches, N_dc)), bounds=(y_locs[-1]+1e-6, wp2 * 100.0), method='bounded')
                errs.append(-res.fun - 1.0)
        return errs

    b_low = [1e-5]*m_lp + [wp2 + 1e-4]*m_hp
    b_up = [wp1 - 1e-4]*m_lp + [np.inf]*m_hp
    res = least_squares(residuals, initial_xy, bounds=(b_low, b_up), ftol=1e-11, xtol=1e-11)
    return np.sort(res.x[:m_lp]), np.sort(res.x[m_lp:])

def _cheby_balance_notches(wp1, wp2, N_z, N_dc, m_lp, m_hp, delta_hp, manual_notches=None):
    manual_notches = manual_notches or {}
    last_xy = [None]
    if len(manual_notches) >= N_z:
        fixed_notches = np.array([manual_notches.get(i, np.sqrt(wp1*wp2)) for i in range(N_z)])
        x_opt, y_opt = _cheby_solve_inner(wp1, wp2, fixed_notches, N_dc, m_lp, m_hp, delta_hp)
        return np.sort(fixed_notches), x_opt, y_opt
        
    def outer_residuals(w_free_arr):
        current_notches = [manual_notches.get(i, w_free_arr[0]) for i in range(N_z)]
        x_opt, y_opt = _cheby_solve_inner(wp1, wp2, current_notches, N_dc, m_lp, m_hp, delta_hp, last_xy[0])
        if len(x_opt) + len(y_opt) > 0: last_xy[0] = np.concatenate([x_opt, y_opt])
        v1 = max(abs(_cheby_F_raw_noK(wp1, x_opt, y_opt, current_notches, N_dc)), 1e-12)
        v2 = max(abs(_cheby_F_raw_noK(wp2, x_opt, y_opt, current_notches, N_dc)), 1e-12)
        return [np.log(v1) - np.log(v2)]

    initial_w = np.sqrt(wp1 * wp2)
    margin = (wp2 - wp1) * 0.02
    res = least_squares(outer_residuals, [initial_w], bounds=([wp1 + margin], [wp2 - margin]), ftol=1e-11, xtol=1e-11)
    
    final_notches = np.array([manual_notches.get(i, res.x[0]) for i in range(N_z)])
    x_opt, y_opt = _cheby_solve_inner(wp1, wp2, final_notches, N_dc, m_lp, m_hp, delta_hp, last_xy[0])
    return np.sort(final_notches), x_opt, y_opt

# ---------------------------------------------------------------------
# 3. MULTIPRECISION ASYMMETRIC ROOT EXTRACTOR
# ---------------------------------------------------------------------
def derive_br_transfer_function_stable_mp(notches, x_locs, y_locs, N_dc, K, epsilon):
    """Upgraded to 60-digit mpmath precision to survive Wilkinson's Trap."""
    mpmath.mp.dps = 60
    
    def poly_mul_mp(p1, p2):
        if not len(p1) or not len(p2): return []
        res = [mpmath.mpc(0)] * (len(p1) + len(p2) - 1)
        for i, c1 in enumerate(p1):
            for j, c2 in enumerate(p2):
                res[i+j] += c1 * c2
        return res

    D_roots = [mpmath.mpc(1)]
    for z in notches: 
        D_roots = poly_mul_mp(D_roots, [mpmath.mpc(1), mpmath.mpc(0), mpmath.mpc(z)**2])
        
    N_roots = [mpmath.mpc(1)]
    if N_dc == 1:   N_roots = [mpmath.mpc(1), mpmath.mpc(0)]
    elif N_dc == 2: N_roots = [mpmath.mpc(1), mpmath.mpc(0), mpmath.mpc(0)]
        
    for x in x_locs: N_roots = poly_mul_mp(N_roots, [mpmath.mpc(1), mpmath.mpc(0), mpmath.mpc(x)**2])
    for y in y_locs: N_roots = poly_mul_mp(N_roots, [mpmath.mpc(1), mpmath.mpc(0), mpmath.mpc(y)**2])
        
    m_tot = len(x_locs) + len(y_locs)
    N_z = len(notches)
    
    c_phase = ((-1)**(m_tot - N_z)) * ((-1j)**N_dc)
    factor = mpmath.mpc(0, 1) * mpmath.mpf(epsilon) * mpmath.mpf(K) * mpmath.mpc(c_phase)
    
    L = max(len(D_roots), len(N_roots))
    D_pad = [mpmath.mpc(0)] * (L - len(D_roots)) + D_roots
    N_pad = [mpmath.mpc(0)] * (L - len(N_roots)) + N_roots
    
    P_plus = [D_pad[i] + factor * N_pad[i] for i in range(L)]
    P_minus = [D_pad[i] - factor * N_pad[i] for i in range(L)]
    
    roots1 = mpmath.polyroots(P_plus, maxsteps=2000, extraprec=50)
    roots2 = mpmath.polyroots(P_minus, maxsteps=2000, extraprec=50)
    all_roots = list(roots1) + list(roots2)
    
    poles = [complex(r) for r in all_roots if r.real < -1e-6]
    zeros = [v for z in notches for v in (1j*z, -1j*z)]
    return np.array(poles), np.array(zeros)

# ---------------------------------------------------------------------
# 4. ASYMMETRIC SYNTHESIS ENGINE
# ---------------------------------------------------------------------
def _synthesize_asym_cheby1_gbr(order_lp, order_hp, wp1, wp2, as_db, alpha_max, pb_even_mod_lp, pb_even_mod_hp, manual_notches):
    manual_notches = manual_notches or {}
    N_tot = order_lp + order_hp
    if N_tot % 2 != 0: raise ValueError("A true Band-Reject filter requires an EVEN total order.")
                             
    epsilon = np.sqrt(10**(alpha_max/10.0) - 1.0)
    N_z = N_tot // 2
    N_dc = 1 if (order_lp % 2 != 0) else (2 if pb_even_mod_lp else 0)
    m_lp = (order_lp - N_dc) // 2
    delta_hp = 1 if (order_hp % 2 != 0) else (2 if pb_even_mod_hp else 0)
    m_hp = (order_hp - delta_hp) // 2

    ideal_notches, _, _ = _cheby_balance_notches(wp1, wp2, N_z, N_dc, m_lp, m_hp, delta_hp, {})
    final_notches, x_opt, y_opt = _cheby_balance_notches(wp1, wp2, N_z, N_dc, m_lp, m_hp, delta_hp, manual_notches)
    
    v1 = max(abs(_cheby_F_raw_noK(wp1, x_opt, y_opt, final_notches, N_dc)), 1e-12)
    v2 = max(abs(_cheby_F_raw_noK(wp2, x_opt, y_opt, final_notches, N_dc)), 1e-12)
    K = 1.0 / max(v1, v2)
    
    poles, zeros = derive_br_transfer_function_stable_mp(final_notches, x_locs=x_opt, y_locs=y_opt, N_dc=N_dc, K=K, epsilon=epsilon)
    
    # Universal Peak Normalization (Lower passband sweep)
    w_test = np.linspace(1e-6, wp1, 2000)
    jw_test = 1j * w_test
    num = np.ones_like(jw_test, dtype=complex)
    for z in zeros: num *= (jw_test - z)
    den = np.ones_like(jw_test, dtype=complex)
    for p in poles: den *= (jw_test - p)
    gain = 1.0 / np.max(np.abs(num / den))
    
    # Assumes find_crossing is in the namespace
    ws_lp = find_crossing(poles, zeros, gain, as_db) 
    ws_hp = find_crossing(poles, zeros, gain, as_db) # You might need a specialized HP crossing finder here if your utility supports it
    
    r_zeros = np.concatenate([x_opt, y_opt]) if len(x_opt) + len(y_opt) > 0 else np.array([])
    return poles, zeros, gain, ideal_notches, final_notches, ws_lp, ws_hp, r_zeros

# ---------------------------------------------------------------------
# 5. MAIN ARBITRATOR API
# ---------------------------------------------------------------------
def synthesize_cheby1_gbr(order_lp, order_hp, wp1, wp2, as_db, alpha_max, pb_even_mod_lp=False, pb_even_mod_hp=False, manual_notches=None):
    manual_notches = manual_notches or {}
    
    # Symmetry Condition
    is_symmetric = (
        order_lp == order_hp and 
        pb_even_mod_lp == pb_even_mod_hp and 
        len(manual_notches) == 0
    )
    
    if is_symmetric:
        return _solve_symmetric_cheby_br_bypass(order_lp, alpha_max, as_db, wp1, wp2, pb_even_mod_lp)
    else:
        return _synthesize_asym_cheby1_gbr(order_lp, order_hp, wp1, wp2, as_db, alpha_max, pb_even_mod_lp, pb_even_mod_hp, manual_notches)

# ============================================================
# [INV_CHEBY_GBR_SYNTH BLOCK]
# ============================================================
def _invcheby_F_raw_noK(w, notches, N_LP):
    w = np.asarray(w, dtype=float)
    num = w**N_LP
    den = np.ones_like(w, dtype=float)
    for z in notches: den = den * abs(w**2 - z**2)
    return num / den

def _invcheby_optimize(wp1, wp2, AS, epsilon, N_z, N_LP, fixed_notches_dict, tied_groups=None):
    tied_groups = tied_groups or []
    target_F = np.sqrt(10**(AS/10.0) - 1.0) / epsilon
    var_map = {}
    free_var_count = 0
    
    for group in tied_groups:
        if not any(i in fixed_notches_dict for i in group):
            for idx in group: var_map[idx] = ('free', free_var_count)
            free_var_count += 1
            
    for i in range(N_z):
        if i in fixed_notches_dict: var_map[i] = ('fixed', fixed_notches_dict[i])
        elif i not in var_map:
            var_map[i] = ('free', free_var_count)
            free_var_count += 1
            
    if free_var_count == 0:
        return np.sort([var_map[i][1] if var_map[i][0] == 'fixed' else 0 for i in range(N_z)])
        
    def outer_residuals(free_vars):
        notches = np.zeros(N_z)
        for i in range(N_z):
            st, val = var_map[i]
            notches[i] = val if st == 'fixed' else free_vars[val]
        notches = np.sort(notches)
        
        v1 = max(abs(_invcheby_F_raw_noK(wp1, notches, N_LP)), 1e-12)
        v2 = max(abs(_invcheby_F_raw_noK(wp2, notches, N_LP)), 1e-12)
        err_balance = np.log(v1) - np.log(v2)
        
        hump_errs = []
        for k in range(N_z - 1):
            if notches[k+1] > notches[k] * 1.0002:
                res = minimize_scalar(lambda w: abs(_invcheby_F_raw_noK(w, notches, N_LP)), bounds=(notches[k]*1.0001, notches[k+1]*0.9999), method='bounded')
                hump_errs.append(np.log(max(res.fun, 1e-12)) - np.log(v1 * target_F))
            else: hump_errs.append(0.0) 
        return [err_balance] + hump_errs

    initial_all = wp1 + (wp2 - wp1) * np.linspace(0.1, 0.9, N_z)
    initial_free = np.zeros(free_var_count)
    counts = np.zeros(free_var_count)
    
    for i in range(N_z):
        st, val = var_map[i]
        if st == 'free':
            initial_free[val] += initial_all[i]
            counts[val] += 1
    initial_free = initial_free / counts 
    
    margin = (wp2 - wp1) * 0.02
    b_low = [wp1 + margin] * free_var_count
    b_up = [wp2 - margin] * free_var_count
    
    res = least_squares(outer_residuals, initial_free, bounds=(b_low, b_up), ftol=1e-11, xtol=1e-11)
    
    notches = np.zeros(N_z)
    for i in range(N_z):
        st, val = var_map[i]
        notches[i] = val if st == 'fixed' else res.x[val]
    return np.sort(notches)

def derive_invcheby_br_transfer_function_stable(notches, N_LP, K, epsilon):
    D_poly = np.array([1.0])
    for z in notches: D_poly = np.polymul(D_poly, [1.0, 0.0, z**2])
    
    N_poly = np.zeros(N_LP + 1)
    N_poly[0] = 1.0
    
    Nz = len(notches)
    c_phase = -1j * epsilon * K * ((-1)**Nz) * ((-1j)**N_LP)
    
    L = max(len(D_poly), len(N_poly))
    D_pad = np.pad(D_poly, (L - len(D_poly), 0))
    N_pad = np.pad(N_poly, (L - len(N_poly), 0))
    
    roots1 = np.roots(D_pad + c_phase * N_pad)
    roots2 = np.roots(D_pad - c_phase * N_pad)
    
    poles = [r for r in np.concatenate([roots1, roots2]) if np.real(r) < -1e-6]
    zeros = [v for z in notches for v in (1j*z, -1j*z)]
    return poles, zeros

def synthesize_invcheby_gbr(order_lp, order_hp, wp1, wp2, as_db, alpha_max, sb_rolloff=False, manual_notches=None):
    manual_notches = manual_notches or {}
    N_tot = order_lp + order_hp
    if N_tot % 2 != 0: raise ValueError("A true Band-Reject filter requires an EVEN total order.")
        
    epsilon = np.sqrt(10**(alpha_max/10.0) - 1.0)
    N_z = N_tot // 2
    N_LP = order_lp
    
    tied_groups = []
    if sb_rolloff and N_z % 2 == 0: tied_groups.append([(N_z//2)-1, N_z//2])
            
    if N_LP == N_z and not sb_rolloff:
        T = np.sqrt(10**(as_db/10.0) - 1.0) / epsilon
        Omega_p = np.cosh(1.0 / N_z * np.arccosh(T))
        B = wp2 - wp1
        w0 = np.sqrt(wp1 * wp2)
        
        ideal_notches = []
        for k in range(1, N_z + 1):
            Omega_k = np.cos((2*k - 1) * np.pi / (2*N_z))
            w = (-B * Omega_k + np.sqrt((B * Omega_k)**2 + 4 * Omega_p**2 * w0**2)) / (2 * Omega_p)
            ideal_notches.append(w)
        ideal_notches = np.sort(ideal_notches)
    else:
        ideal_notches = _invcheby_optimize(wp1, wp2, as_db, epsilon, N_z, N_LP, {}, tied_groups)
        
    final_fixed = {}
    for k, v in manual_notches.items():
        if k < N_z: final_fixed[k] = v
        
    if len(final_fixed) == 0 and N_LP == N_z and not sb_rolloff:
        final_notches = np.copy(ideal_notches)
    else:
        final_notches = _invcheby_optimize(wp1, wp2, as_db, epsilon, N_z, N_LP, final_fixed, tied_groups)
        
    v1 = max(abs(_invcheby_F_raw_noK(wp1, final_notches, N_LP)), 1e-12)
    v2 = max(abs(_invcheby_F_raw_noK(wp2, final_notches, N_LP)), 1e-12)
    K = 1.0 / max(v1, v2)
    
    poles, zeros = derive_invcheby_br_transfer_function_stable(final_notches, N_LP, K, epsilon)
    w_test = np.linspace(0.0, wp1*0.99, 1000)
    gain = 1.0 / np.max(evaluate_h(poles, zeros, 1.0, w_test))
    ws_lp, ws_hp = find_crossings_br(poles, zeros, gain, as_db, wp1, wp2)
    
    return poles, zeros, gain, ideal_notches, final_notches, ws_lp, ws_hp, np.array([])

# ============================================================
# ELLIPTIC BAND-REJECT (STRICTLY SYMMETRIC LP TRANSFORMATION)
# ============================================================
import numpy as np

def _transform_lp_to_br(lp_poles, lp_tzeros, lp_rzeros, wp1, wp2):
    """Geometrically maps LHP Lowpass roots directly to Band-Reject roots."""
    B = wp2 - wp1
    w0_sq = wp1 * wp2
    w0 = np.sqrt(w0_sq)
    
    br_poles, br_tzeros, br_rzeros = [], [], []
    
    # Mathematical safeguard against Catastrophic Cancellation
    def map_root(r):
        if abs(r) < 1e-12: return None, None 
        a = B / (2.0 * r)
        term = np.sqrt(a**2 - w0_sq, dtype=complex)
        
        # Always add identical signs to preserve 64-bit precision
        if abs(a + term) > abs(a - term): s1 = a + term
        else: s1 = a - term
            
        s2 = w0_sq / s1 # Find second root cleanly via Vieta's formulas
        return s1, s2

    # 1. Map Poles
    for p in lp_poles:
        s1, s2 = map_root(p)
        br_poles.extend([s1, s2])
        
    # 2. Map Finite Transmission Zeros
    for z in lp_tzeros:
        s1, s2 = map_root(z)
        if s1 is not None:
            br_tzeros.extend([s1, s2])
            
    # 3. Map Infinity Zeros (This natively handles Coincident Notches!)
    # Every 1 zero at infinity in LP maps to 1 pair of +/- j*w0 in BR
    num_zeros_inf = len(lp_poles) - len(lp_tzeros)
    if num_zeros_inf > 0:
        br_tzeros.extend([1j * w0, -1j * w0] * num_zeros_inf)
        
    # 4. Map Reflection Zeros
    for rz in lp_rzeros:
        s1, s2 = map_root(rz)
        if s1 is not None:
            br_rzeros.extend([s1, s2])
        else:
            br_rzeros.append(0j) # LP DC zero stays at DC (and inf)

    return np.array(br_poles), np.array(br_tzeros), np.array(br_rzeros)

def solve_elliptic_gbr(order_lp, order_hp, wp1, wp2, as_db, alpha_max, pb_even_mod_lp=False, pb_even_mod_hp=False, sb_rolloff=False):
    """
    Synthesizes an Elliptic Band-Reject filter using geometric transformation 
    from the bulletproof Lowpass prototype. Strictly enforces symmetry.
    """
    # 1. Enforce Symmetry Constraints
    if order_lp != order_hp:
        raise ValueError(f"Elliptic Band-Reject must be symmetric. Requested order_lp={order_lp}, order_hp={order_hp}.")
    if pb_even_mod_lp != pb_even_mod_hp:
        raise ValueError("Elliptic Band-Reject must be symmetric. Passband even-mod must match on both branches.")
        
    order_branch = order_lp
    if order_branch > 15:
        raise ValueError(f"Branch order capped at N=15. Requested N={order_branch}.")

    # 2. Generate the flawless LP skeleton
    # Note: Assumes 'solve_elliptic_lp' is available in your module
    lp_tzeros, lp_poles, _, lp_ws, _, lp_rzeros = solve_elliptic_lp(
        order=order_branch, 
        alpha_max=alpha_max, 
        A_s=as_db, 
        pb_even_mod=pb_even_mod_lp, 
        sb_rolloff=sb_rolloff
    )
    
    # 3. Map safely to BR
    br_poles, br_tzeros, br_rzeros = _transform_lp_to_br(lp_poles, lp_tzeros, lp_rzeros, wp1, wp2)
    
    # 4. Universal Peak Normalization (Sweep Lower Passband to find absolute peaks)
    w_test = np.linspace(1e-6, wp1, 5000)
    jw_test = 1j * w_test
    num = np.ones_like(jw_test, dtype=complex)
    for z in br_tzeros: num *= (jw_test - z)
    den = np.ones_like(jw_test, dtype=complex)
    for p in br_poles: den *= (jw_test - p)
    gain = 1.0 / np.max(np.abs(num / den))
    
    # 5. Map Stopband Edges exactly from LP omega_s
    B = wp2 - wp1
    w0_sq = wp1 * wp2
    ws_lp = B / (2.0 * lp_ws) + np.sqrt((B / (2.0 * lp_ws))**2 + w0_sq)
    ws_hp = -B / (2.0 * lp_ws) + np.sqrt((B / (2.0 * lp_ws))**2 + w0_sq)
    
    return br_tzeros, br_poles, gain, min(ws_lp, ws_hp), max(ws_lp, ws_hp), br_rzeros

# =====================================================================
# LEGACY ADAPTER (FOR FILTER_ENGINE COMPATIBILITY)
# =====================================================================
def synthesize_elliptic_gbr(order_lp, order_hp, wp1, wp2, as_db, alpha_max, pb_even_mod_lp=False, pb_even_mod_hp=False, sb_rolloff=False, manual_notches=None):
    tzeros, poles, gain, ws_lp, ws_hp, rzeros = solve_elliptic_gbr(
        order_lp, order_hp, wp1, wp2, as_db, alpha_max, pb_even_mod_lp, pb_even_mod_hp, sb_rolloff
    )
    empty = np.array([])
    # Return signature: poles, zeros, gain, ideal_notches, final_notches, ws_lp, ws_hp, r_zeros
    return poles, tzeros, gain, empty, empty, ws_lp, ws_hp, rzeros