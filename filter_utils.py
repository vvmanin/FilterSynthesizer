# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

import numpy as np
from scipy.optimize import root_scalar


def evaluate_h(poles, zeros, gain, w):
    """Evaluates the magnitude response |H(jw)| of a filter."""
    jw = 1j * np.asarray(w, dtype=float)
    poles = np.asarray(poles, dtype=complex)
    zeros = np.asarray(zeros, dtype=complex)
    
    num = (gain * np.prod(np.subtract.outer(jw, zeros), axis=1)
           if zeros.size else gain * np.ones_like(jw, dtype=complex))
    den = np.prod(np.subtract.outer(jw, poles), axis=1)
    
    return np.abs(num / den)

def find_crossing(poles, zeros, gain, target_db, w_start=None, w_end=None, is_hp=False):
    """Finds the exact frequency w_s by walking strictly outwards from the passband edge."""
    
    target_mag = 10 ** (-target_db / 20.0)
    def err(w): return evaluate_h(poles, zeros, gain, [w])[0] - target_mag

    # ==========================================
    # LEGACY MODE (Normalized LP)
    # ==========================================
    if w_start is None or w_end is None:
        w_left = 1.0
        z_mags = np.abs(zeros)
        z_mags = z_mags[z_mags > 1.0]
        w_right = np.min(z_mags) * 0.999 if z_mags.size else 1000.0
        try: return root_scalar(err, bracket=[w_left, w_right], method='brentq').root
        except: return 1.0

    # ==========================================
    # BANDPASS MODE (Directional Passband Walk)
    # ==========================================
    if is_hp:
        # Lower Stopband: Start exactly at wp1 (w_end) and walk DOWN to near DC
        # The array naturally sorts from highest frequency down to lowest
        w_grid = np.logspace(np.log10(w_end), np.log10(max(w_start, 1e-6)), 5000)
    else:
        # Upper Stopband: Start exactly at wp2 (w_start) and walk UP to infinity
        # The array naturally sorts from lowest frequency up to highest
        w_grid = np.logspace(np.log10(w_start), np.log10(w_end), 5000)

    # Evaluate all 5000 steps outwards from the passband simultaneously
    mags = evaluate_h(poles, zeros, gain, w_grid)

    # Find the indices of every point that sits below the target attenuation
    below_target_indices = np.where(mags <= target_mag)[0]

    if below_target_indices.size > 0:
        # Grab the VERY FIRST time it crossed the threshold
        idx = below_target_indices[0]
        
        if idx == 0: 
            return w_grid[0]
            
        # We have perfectly trapped the cliff between two adjacent array steps
        w_above_target = w_grid[idx-1]
        w_below_target = w_grid[idx]
        
        # Order the bracket safely for SciPy
        bracket = [min(w_above_target, w_below_target), max(w_above_target, w_below_target)]
        
        try:
            # Use Brent's method purely for final 64-bit float precision
            return root_scalar(err, bracket=bracket, method='brentq').root
        except ValueError:
            return w_below_target
            
    # Absolute safety fallback if the array somehow never crosses
    return w_start if is_hp else w_end
