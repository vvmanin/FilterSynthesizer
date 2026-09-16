# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

import numpy as np
from scipy import signal

def calculate_w0_q(root):
    """Calculates natural frequency (w0) and Quality factor (Q) for a complex root."""
    w0 = np.abs(root)
    real_part = np.real(root)
    if abs(real_part) < 1e-12:
        q = float('inf')
    else:
        q = w0 / (-2.0 * real_part)
    return w0, q

def categorize_roots(roots, tol=1e-5):
    """Separates roots into Origin, Real, and Complex Pairs using dynamic tolerance."""
    origin_roots = []
    real_roots = []
    complex_pairs = []
    used_indices = set()
    
    for i, r in enumerate(roots):
        if i in used_indices: continue
        
        mag = max(abs(r), 1.0)
        local_tol = tol * mag
        
        if abs(r.real) < local_tol and abs(r.imag) < local_tol:
            origin_roots.append(r)
            used_indices.add(i)
        elif abs(r.imag) < local_tol:
            real_roots.append(r.real)
            used_indices.add(i)
        else:
            conjugate_found = False
            for j, r_conj in enumerate(roots):
                if j != i and j not in used_indices:
                    if abs(r.real - r_conj.real) < local_tol and abs(r.imag + r_conj.imag) < local_tol:
                        complex_pairs.append(r if r.imag > 0 else r_conj)
                        used_indices.add(i)
                        used_indices.add(j)
                        conjugate_found = True
                        break
            if not conjugate_found:
                complex_pairs.append(r if r.imag > 0 else np.conj(r))
                used_indices.add(i)
                
    return origin_roots, real_roots, complex_pairs

def build_stage_bricks(poles, zeros, scale_type, wc):
    """Analyzes all roots and builds the initial 'Bricks' for the UI."""
    origin_z, real_z, complex_z_pairs = categorize_roots(zeros)
    origin_p, real_p, complex_p_pairs = categorize_roots(poles)
    
    pole_bricks, zero_bricks = [], []
    
    for i, p in enumerate(complex_p_pairs):
        w0, q = calculate_w0_q(p)
        pole_bricks.append({"id": f"p_pair_{i}", "type": "Complex Pair", "root": p, "w0": w0, "q": q})
        
    for i, p in enumerate(real_p):
        pole_bricks.append({"id": f"p_real_{i}", "type": "Real", "root": p, "w0": abs(p), "q": 0.0}) # Q=0 for sorting
        
    for i, z in enumerate(complex_z_pairs):
        w0, q = calculate_w0_q(z)
        zero_bricks.append({"id": f"z_pair_{i}", "type": "Complex Pair", "root": z, "w0": w0, "q": q})
        
    for i, z in enumerate(origin_z):
        zero_bricks.append({"id": f"z_origin_{i}", "type": "Origin", "root": z, "w0": 0.0, "q": 0.0})
        
    return pole_bricks, zero_bricks

def auto_pair_bandpass(p_bricks, z_bricks, absorb_1st_order=False):
    """Specialized Q-descending, zoned proximity pairing for Bandpass topologies."""
    import numpy as np
    complex_poles = [p for p in p_bricks if p['type'] == 'Complex Pair']
    real_poles = [p for p in p_bricks if p['type'] == 'Real']

    # 1. Determine Geometric Center to split Upper and Lower zeros
    if complex_poles:
        w_center = np.exp(np.mean(np.log([p['w0'] for p in complex_poles])))
    else:
        w_center = 1.0

    UZ, LZ, OZ = [], [], []
    for z in z_bricks:
        if z['w0'] < 1e-5: # Origin zeros (DC)
            OZ.append(z)
        elif z['w0'] > w_center:
            UZ.append(z)
        else:
            LZ.append(z)

    # Initialize stages and sort strictly ascending by pole frequency
    stages_pool = [{'pole': p, 'zeros': [], 'absorbed_real': None} for p in complex_poles]
    stages_pool.sort(key=lambda s: s['pole']['w0']) 

    n_uz = len(UZ)
    n_lz = len(LZ)
    
    # Mathematical safeguard: Ensure we don't request more pole pairs than exist
    n_lz = min(n_lz, len(stages_pool))
    n_uz = min(n_uz, len(stages_pool) - n_lz)

    # Slice the pole pools for Upper and Lower zone assignments
    LP_stages = stages_pool[:n_lz] if n_lz > 0 else []
    UP_stages = stages_pool[-n_uz:] if n_uz > 0 else []

    # 2. Pair UZ to Upper Poles (Highest Q -> Closest Zero)
    if UP_stages:
        UP_stages.sort(key=lambda s: s['pole']['q'], reverse=True)
        for s in UP_stages:
            if UZ:
                best_z = min(UZ, key=lambda z: abs(s['pole']['w0'] - z['w0']))
                s['zeros'].append(best_z)
                UZ.remove(best_z)

    # 3. Pair LZ to Lower Poles (Highest Q -> Closest Zero)
    if LP_stages:
        LP_stages.sort(key=lambda s: s['pole']['q'], reverse=True)
        for s in LP_stages:
            if LZ:
                best_z = min(LZ, key=lambda z: abs(s['pole']['w0'] - z['w0']))
                s['zeros'].append(best_z)
                LZ.remove(best_z)

    # 4. Pair Origin Zeros (Rule 1)
    # The middle-frequency stages that survived UZ/LZ assignments get the DC zeros
    available_stages = [s for s in stages_pool if not s['zeros']]
    available_stages.sort(key=lambda s: s['pole']['w0']) 
    
    while len(OZ) >= 2 and available_stages:
        best_s = available_stages.pop(0)
        best_s['zeros'].append(OZ.pop(0))
        best_s['zeros'].append(OZ.pop(0))

    # Handle Residual Origin Zero & Real Pole
    r_pole = real_poles[0] if real_poles else None
    standalone_real_stage = None

    if len(OZ) == 1:
        residual_oz = OZ.pop(0)
        if r_pole and not absorb_1st_order:
            # Pair them together as a standalone 1st order block
            standalone_real_stage = {'pole': r_pole, 'zeros': [residual_oz], 'absorbed_real': None}
            r_pole = None
        else:
            # Dump into lowest freq stage, keeping r_pole free to form a 3rd order section!
            if available_stages:
                best_s = min(available_stages, key=lambda s: s['pole']['w0'])
                best_s['zeros'].append(residual_oz)
            else:
                best_s = min(stages_pool, key=lambda s: s['pole']['w0'])
                best_s['zeros'].append(residual_oz)

    # Dump any straggler OZs into the lowest frequency stage
    while OZ:
        oz = OZ.pop(0)
        best_s = min(stages_pool, key=lambda s: s['pole']['w0'])
        best_s['zeros'].append(oz)

    # 5. 3rd-Order Stage Logic
    if r_pole:
        absorbed = False
        if absorb_1st_order:
            # Prioritize stages that have exactly 3 zeros (they NEED a real pole), then by lowest frequency
            stages_pool.sort(key=lambda s: (sum(2 if z['type'] == 'Complex Pair' else 1 for z in s['zeros']) != 3, s['pole']['w0']))
            for s in stages_pool:
                num_z = sum(2 if z['type'] == 'Complex Pair' else 1 for z in s['zeros'])
                # A 3rd order stage capacity is 3, allowing 3 origin zeros to merge!
                if num_z <= 3: 
                    s['absorbed_real'] = r_pole
                    absorbed = True
                    break
    
        if not absorbed:
            standalone_real_stage = {'pole': r_pole, 'zeros': [], 'absorbed_real': None}

    if standalone_real_stage:
        stages_pool.append(standalone_real_stage)

    # 6. Format and Route output
    routing = []
    for i, s in enumerate(stages_pool):
        z_ids = [z['id'] for z in s['zeros']]
        cap = 2 if s['pole']['type'] == 'Complex Pair' else 1
        if s['absorbed_real']: cap += 1
        
        for z in s['zeros']: cap -= (2 if z['type'] == 'Complex Pair' else 1)

        routing.append({
            'stage_num': 0, # Will be set during sorting
            'pole_id': s['pole']['id'],
            'w0': s['pole']['w0'],
            'q': s['pole']['q'],
            'absorbed_real_id': s['absorbed_real']['id'] if s['absorbed_real'] else 'None',
            'capacity': cap,
            'zero_ids': z_ids,
            'has_zero_pair': any(z['type'] == 'Complex Pair' for z in s['zeros']),
            'is_3rd_order': s['absorbed_real'] is not None
        })

    # Sort final stages by Q for standard hardware sequencing
    routing.sort(key=lambda x: x['q'])
    for i, r in enumerate(routing): 
        r['stage_num'] = i + 1

    return routing

def auto_pair_bandreject(p_bricks, z_bricks, absorb_1st_order=False):
    """Specialized Q-descending, split-band proximity pairing for Band-Reject topologies."""
    import numpy as np
    
    complex_poles = [p for p in p_bricks if p['type'] == 'Complex Pair']
    real_poles = sorted([p for p in p_bricks if p['type'] == 'Real'], key=lambda x: x['w0'])
    
    z_pool = list(z_bricks)
    stages_pool = []
    
    # 1. Handle Real Poles (Form a low-Q 2nd-order section first)
    while len(real_poles) >= 2:
        p1 = real_poles.pop(0)  # Lowest real pole
        p2 = real_poles.pop(-1) # Highest real pole
        
        w0_eff = np.sqrt(p1['w0'] * p2['w0'])
        q_eff = w0_eff / (p1['w0'] + p2['w0']) # Q <= 0.5
        
        assigned_z = []
        if z_pool:
            best_z = min(z_pool, key=lambda z: abs(z['w0'] - w0_eff))
            z_pool.remove(best_z)
            assigned_z.append(best_z)
            
        stages_pool.append({
            'pole': p1, 'zeros': assigned_z, 'absorbed_real': p2,
            'q_eff': q_eff, 'w0_eff': w0_eff
        })

    # Safeguard for odd number of real poles
    for rp in real_poles:
        stages_pool.append({'pole': rp, 'zeros': [], 'absorbed_real': None, 'q_eff': 0.0, 'w0_eff': rp['w0']})

    # 2. Calculate Geometric Mean of ALL zero frequencies to split the spectrum
    if z_bricks:
        # np.exp(np.mean(np.log(...))) prevents float overflow on large arrays
        z_geom_mean = np.exp(np.mean(np.log([z['w0'] for z in z_bricks])))
    else:
        z_geom_mean = 1.0

    # 3. Split complex poles into HF and LF bands
    hf_poles = [p for p in complex_poles if p['w0'] > z_geom_mean]
    lf_poles = [p for p in complex_poles if p['w0'] <= z_geom_mean]
    
    # 4. Sort both bands by Q descending (Highest Q first to suppress peaking)
    hf_poles.sort(key=lambda x: x['q'], reverse=True)
    lf_poles.sort(key=lambda x: x['q'], reverse=True)
    
    # 5. Alternating Assignment (HF -> LF -> HF -> LF...)
    while hf_poles or lf_poles:
        
        # A. Pick highest Q from HF
        if hf_poles:
            p_hf = hf_poles.pop(0)
            assigned_z = []
            if z_pool:
                best_z = min(z_pool, key=lambda z: abs(z['w0'] - p_hf['w0']))
                z_pool.remove(best_z)
                assigned_z.append(best_z)
                
            stages_pool.append({
                'pole': p_hf, 'zeros': assigned_z, 'absorbed_real': None,
                'q_eff': p_hf['q'], 'w0_eff': p_hf['w0']
            })
            
        # B. Pick highest Q from LF
        if lf_poles:
            p_lf = lf_poles.pop(0)
            assigned_z = []
            if z_pool:
                best_z = min(z_pool, key=lambda z: abs(z['w0'] - p_lf['w0']))
                z_pool.remove(best_z)
                assigned_z.append(best_z)
                
            stages_pool.append({
                'pole': p_lf, 'zeros': assigned_z, 'absorbed_real': None,
                'q_eff': p_lf['q'], 'w0_eff': p_lf['w0']
            })

    # 6. Fallbacks (Dump leftover zeros into stages without zeros)
    while z_pool:
        z = z_pool.pop()
        valid_stages = [s for s in stages_pool if not s['zeros']]
        if valid_stages:
            best_s = min(valid_stages, key=lambda s: abs(s['w0_eff'] - z['w0']))
            best_s['zeros'].append(z)
        else:
            stages_pool[-1]['zeros'].append(z) # Absolute fallback

    # 7. Format output for the UI
    routing = []
    for s in stages_pool:
        z_ids = [z['id'] for z in s['zeros']]
        cap = 2 if s['pole']['type'] == 'Complex Pair' else 1
        if s['absorbed_real']: cap += 1
        for z in s['zeros']: cap -= (2 if z['type'] == 'Complex Pair' else 1)

        routing.append({
            'stage_num': 0,
            'pole_id': s['pole']['id'],
            'w0': s['w0_eff'],
            'q': s['q_eff'],
            'absorbed_real_id': s['absorbed_real']['id'] if s['absorbed_real'] else 'None',
            'capacity': cap,
            'zero_ids': z_ids,
            'has_zero_pair': any(z['type'] == 'Complex Pair' for z in s['zeros']),
            'is_3rd_order': False # Real+Real is a 2nd order stage physically
        })
    
    # 8. Re-sort by Q ascending for final hardware cascading sequence
    routing.sort(key=lambda x: x['q'])
    for i, r in enumerate(routing): r['stage_num'] = i + 1

    return routing
    
def auto_pair_stages(p_bricks, z_bricks, absorb_1st_order=False, filter_type="Lowpass"):
    
    # Route to specialized algorithms
    if filter_type == "Bandpass":
        return auto_pair_bandpass(p_bricks, z_bricks, absorb_1st_order)
    elif filter_type == "Band-Reject":
        return auto_pair_bandreject(p_bricks, z_bricks, absorb_1st_order)
    """
    From-scratch auto-router following strict analog synthesis rules.
    """
    stages = []
    
    complex_poles = [p for p in p_bricks if p['type'] == 'Complex Pair']
    real_poles = [p for p in p_bricks if p['type'] == 'Real']
    
    # Track available zeros
    avail_z_pairs = [z for z in z_bricks if z['type'] == 'Complex Pair']
    avail_origin_z = [z for z in z_bricks if z['type'] == 'Origin']

    # --- INITIALIZE STAGES & ABSORPTION ---
    if absorb_1st_order and real_poles and complex_poles:
        # Find the lowest Q complex pole to absorb the real pole
        lowest_q_pole = min(complex_poles, key=lambda x: x['q'])
        real_to_absorb = real_poles.pop(0) # Take the first real pole
        
        stages.append({
            "pole_id": lowest_q_pole['id'],
            "w0": lowest_q_pole['w0'],
            "q": lowest_q_pole['q'],
            "absorbed_real_id": real_to_absorb['id'],
            "capacity": 3,
            "zero_ids": [],
            "has_zero_pair": False,
            "is_3rd_order": True
        })
        complex_poles.remove(lowest_q_pole)

    # Add remaining complex poles
    for p in complex_poles:
        stages.append({
            "pole_id": p['id'], "w0": p['w0'], "q": p['q'],
            "absorbed_real_id": "None", "capacity": 2, "zero_ids": [],
            "has_zero_pair": False, "is_3rd_order": False
        })
        
    # Add any remaining real poles (usually 0 after absorption, or 1 if absorption is off)
    for p in real_poles:
        stages.append({
            "pole_id": p['id'], "w0": p['w0'], "q": 0.0, # Treat Real poles as lowest Q
            "absorbed_real_id": "None", "capacity": 1, "zero_ids": [],
            "has_zero_pair": False, "is_3rd_order": False
        })

    # ==========================================================
    # PASS 1: Assign Complex Zero Pairs (Highest Q -> Lowest Q)
    # ==========================================================
    # Sort descending by Q
    stages.sort(key=lambda s: s['q'], reverse=True)
    
    for stage in stages:
        if stage['capacity'] >= 2 and avail_z_pairs:
            # Find closest zero pair based on absolute frequency distance
            closest_z = min(avail_z_pairs, key=lambda z: abs(stage['w0'] - z['w0']))
            stage['zero_ids'].append(closest_z['id'])
            stage['capacity'] -= 2
            stage['has_zero_pair'] = True
            
            # Store wz inside the stage dictionary to check highpass rule later
            stage['wz_assigned'] = closest_z['w0'] 
            
            avail_z_pairs.remove(closest_z)

    # ==========================================================
    # PASS 2: Assign Origin Zeros (Lowest Q -> Highest Q)
    # ==========================================================
    # Sort ascending by Q (Real poles will be first)
    stages.sort(key=lambda s: s['q'], reverse=False)
    
    for stage in stages:
        while stage['capacity'] >= 1 and avail_origin_z:
            
            # STRICT LIMITATION: 3rd order stage with a zero pair checking for an origin zero
            if stage['is_3rd_order'] and stage['has_zero_pair']:
                w0 = stage['w0']
                wz = stage.get('wz_assigned', 0.0)
                if not (w0 > wz): # Must be a highpass notch!
                    break # Cannot assign origin zero to this stage, move to next stage
            
            # If constraint passed, or it's a standard stage, assign origin zero
            next_oz = avail_origin_z.pop(0)
            stage['zero_ids'].append(next_oz['id'])
            stage['capacity'] -= 1

    # ==========================================================
    # FINAL SEQUENCING (Input to Output)
    # ==========================================================
    # We are already sorted Lowest Q -> Highest Q from Pass 2, which is correct for hardware sequencing!
    for i, s in enumerate(stages):
        s['stage_num'] = i + 1
        
    return stages

def find_clicked_brick(click_x, click_y, p_bricks, z_bricks, plot_scale):
    """
    Takes the raw X/Y coordinates from a Plotly click event, compares them against
    the scaled mathematical roots, and returns the logical ID of the clicked brick.
    """
    closest_id = None
    closest_dist = float('inf')
    brick_type = None

    # Combine all poles and zeros into one searchable list
    all_bricks = [(b, 'pole') for b in p_bricks] + [(b, 'zero') for b in z_bricks]

    for brick, b_type in all_bricks:
        # Complex pairs have two physical markers on the plot (positive and negative j)
        # We must check the distance to BOTH physical locations!
        roots_to_check = [brick['root']]
        if brick['type'] == 'Complex Pair':
            roots_to_check.append(np.conj(brick['root']))

        for r in roots_to_check:
            # Scale the mathematical root down to match the visual Plotly coordinates
            scaled_x = np.real(r) / plot_scale
            scaled_y = np.imag(r) / plot_scale

            # Calculate the Euclidean distance between the click and the root
            dist = np.sqrt((click_x - scaled_x)**2 + (click_y - scaled_y)**2)

            # Keep track of the absolute closest match
            if dist < closest_dist:
                closest_dist = dist
                closest_id = brick['id']
                brick_type = b_type

    # Because Plotly only fires an event when a specific marker is clicked, 
    # the closest_dist will be effectively 0.0 (subject to floating point rounding).
    return closest_id, brick_type

from scipy import signal
import numpy as np

def compute_stage_gains(stages, p_bricks, z_bricks, k_system, passband_gain_linear=1.0):
    """
    Computes Lueder's cumulative peak-scaling gain using highly stable normalized ZPK evaluation.
    """
    import numpy as np
    from scipy import signal
    
    w0_list = []
    for p in p_bricks: w0_list.append(max(abs(p['root']), 1e-3))
    for z in z_bricks:
        if abs(z['root']) > 1e-6: w0_list.append(abs(z['root']))
        
    # Pivot frequency to prevent float64 over/underflow during evaluation
    w_norm = np.median(w0_list) if w0_list else 1.0
    
    w_min = min(w0_list) * 0.01 if w0_list else 0.1
    w_max = max(w0_list) * 100.0 if w0_list else 10.0
    
    # FIX 1: Drop 50k to 2k for instant UI response.
    w_sweep = np.logspace(np.log10(w_min), np.log10(w_max), 2000)
    # Inject exact corner frequencies AND true DC (0.0) to guarantee we hit the exact peaks
    w_sweep = np.sort(np.concatenate((w_sweep, np.array(w0_list), [0.0])))

    stage_k = []
    cum_zeros = []
    cum_poles = []
    k_cum_prev = 1.0

    for stage in stages:
        s_poles, s_zeros = [], []
        
        p_brick = next((b for b in p_bricks if b['id'] == stage['pole_id']), None)
        if p_brick:
            s_poles.append(p_brick['root'])
            if p_brick['type'] == 'Complex Pair': s_poles.append(np.conj(p_brick['root']))
                
        abs_id = stage.get('absorbed_real_id', 'None')
        if abs_id != 'None':
            abs_brick = next((b for b in p_bricks if b['id'] == abs_id), None)
            if abs_brick: s_poles.append(abs_brick['root'])
            
        for z_id in stage.get('zero_ids', []):
            z_brick = next((b for b in z_bricks if b['id'] == z_id), None)
            if z_brick:
                s_zeros.append(z_brick['root'])
                if z_brick['type'] == 'Complex Pair': s_zeros.append(np.conj(z_brick['root']))

        cum_poles.extend(s_poles)
        cum_zeros.extend(s_zeros)
        
        # FIX 2: Evaluate in the normalized domain to protect precision
        cum_poles_n = np.array(cum_poles) / w_norm
        cum_zeros_n = np.array(cum_zeros) / w_norm
        w_sweep_n = w_sweep / w_norm
        
        _, h_cum_n = signal.freqs_zpk(cum_zeros_n, cum_poles_n, 1.0, worN=w_sweep_n)
        m_peak_n = np.max(np.abs(h_cum_n))
        
        # Map the flawless normalized peak back to physical magnitudes
        degree_diff = len(cum_poles) - len(cum_zeros)
        m_peak_phys = m_peak_n * (w_norm ** (-degree_diff))
        
        k_cum_target = 1.0 / m_peak_phys if m_peak_phys > 1e-100 else 1.0
        ki = k_cum_target / k_cum_prev
        stage_k.append(ki)
        
        k_cum_prev = k_cum_target

    k_remainder = (k_system * passband_gain_linear) / k_cum_prev
    return stage_k, k_remainder

# =====================================================================
#  classify_section  —  authoritative section classifier (ROADMAP 4.1)
# =====================================================================
#  Section type comes from order + the pole<->zero frequency ratio (wz/w0),
#  independent of filter type. topology_tab routes every section through this
#  (via family_from_section) into the 4.4 dispatch. The rule CORE is factored
#  out so the brick-level (stage+bricks) and the UI-level (Section dict) paths
#  share one source of truth.
# =====================================================================
def _family_from_features(order, pole_type, n_origin_zeros, wz, w0, wz_tol=0.05):
    """Pure family rule. pole_type in {'Real','Complex Pair'}. wz is the finite
    (complex-pair) zero frequency or None; w0 the pole frequency."""
    if wz is not None:                                   # complex-pair zero present
        ratio = (wz / w0) if w0 else float("inf")
        if abs(ratio - 1.0) < wz_tol:
            return "notch"
        return "LPn" if ratio > 1.0 else "HPn"           # zero above pole -> LPn
    if n_origin_zeros <= 0:
        return "LP"                                      # allpole
    if pole_type == "Complex Pair":
        # ORDER MATTERS once a real pole is absorbed into the pair. With a cubic
        # denominator the origin-zero COUNT no longer separates BP from HP the
        # way it does at 2nd order:
        #     3 origin zeros -> num ~ s^3  -> a true 3rd-order HP
        #     2 origin zeros -> num ~ s^2  -> band-pass + absorbed real pole
        #     1 origin zero  -> num ~ s^1  -> band-pass + absorbed real pole
        # The 2-origin-zero case used to fall through to "HP" and was then
        # dispatched to the 3rd-order HP cells, whose numerator degree EQUALS
        # the order -- they match the denominator and the gain, converge, and
        # report success while realizing the wrong numerator. Name the two
        # asymmetric band-pass shapes instead so they route to their own cells.
        if order >= 3:
            if n_origin_zeros == 1:
                return "BP1LP"      # num ~ s   : extra pole on the UPPER skirt
            if n_origin_zeros == 2:
                return "BP1HP"      # num ~ s^2 : extra pole on the LOWER skirt
            return "HP"             # 3 origin zeros: genuine 3rd-order HP
        return "BP" if n_origin_zeros == 1 else "HP"     # 1 origin -> BP, 2 -> HP
    return "HP"                                          # real pole + origin zero -> 1st-order HP


def classify_section(stage, p_bricks, z_bricks, wz_tol=0.05):
    """Authoritative classifier (ROADMAP 4.1). Resolves the stage's pole and
    zeros against the bricks and returns:
        {'order':1|2|3,
         'family':'LP'|'HP'|'BP'|'BP1LP'|'BP1HP'|'LPn'|'HPn'|'notch',
         'w0':float, 'Q':float, 'wz':float|None, 'n_origin_zeros':int}
    BP1LP / BP1HP are the 3rd-order asymmetric band-pass shapes: a complex pair
    with a real pole absorbed, numerator s^1 / s^2 respectively.
    'family' is a math property; sign (realization) is NOT decided here."""
    pole = next((b for b in p_bricks if b["id"] == stage.get("pole_id")), None)
    pole_type = pole["type"] if pole else "Complex Pair"
    w0 = float(pole["w0"]) if pole else 0.0
    Q = float(pole.get("q", 0.0)) if pole else 0.0

    order = 1 if pole_type == "Real" else 2
    if stage.get("absorbed_real_id", "None") not in ("None", None):
        order += 1

    n_origin, wz = 0, None
    for zid in stage.get("zero_ids", []):
        zb = next((b for b in z_bricks if b["id"] == zid), None)
        if zb is None:
            continue
        if zb["type"] == "Origin":
            n_origin += 1
        elif zb["type"] == "Complex Pair":
            wz = float(zb["w0"])

    family = _family_from_features(order, pole_type, n_origin, wz, w0, wz_tol)
    return {"order": order, "family": family, "w0": w0, "Q": Q,
            "wz": wz, "n_origin_zeros": n_origin}


def family_from_section(sec, wz_tol=0.05):
    """UI-level family for a Section dict (topology_tab consumes Section dicts,
    not bricks). Resolution order:
      1. a producer-stored sec['family'] (the authoritative classify_section
         result) -- ALWAYS preferred when present;
      2. an explicit sec['n_origin_zeros'] count;
      3. reconstruction from Section fields via the SAME rule core.

    A boolean has_origin_zero cannot tell HP's TWO origin zeros from BP's ONE.
    Before item 2 both gated to 'pending', so it did not matter; now that HP is
    solvable, a 2nd-order complex-pair section with origin zeros and no finite
    zero defaults to HP (the VCVS-realizable family) rather than BP. For exact
    HP-vs-BP the producer (Pairing tab) should store sec['family'] (or
    sec['n_origin_zeros']) from classify_section when it builds each Section."""
    if sec.get("family"):
        return sec["family"]
    order = int(sec.get("order", 2))
    pole_type = "Complex Pair" if sec.get("is_complex_pair") else "Real"
    wz = (2*np.pi*sec["fz_hz"]) if (sec.get("notch") and sec.get("fz_hz")) else None
    w0 = 2*np.pi*float(sec.get("f0_hz", 1.0))
    if sec.get("n_origin_zeros") is not None:
        n_origin = int(sec["n_origin_zeros"])               # exact count, if stored
    elif sec.get("has_origin_zero"):
        # boolean -> assume the FULL origin-zero set for this order/pole, i.e.
        # the all-origin-zero HP reading:
        #   complex-pair 2nd-order + origin zeros -> HP  (2 origin zeros)
        #   complex-pair 3rd-order + origin zeros -> HP  (3 origin zeros)
        #   real-pole 1st-order + origin zero     -> HP  (1 origin zero)
        # The 3rd-order count is spelled out so this fallback keeps answering
        # "HP" exactly as it did before BP1LP/BP1HP existed: a producer that
        # stores only the boolean cannot distinguish them, so it must not be
        # silently re-routed onto an asymmetric band-pass cell. Producers that
        # CAN tell (app.py stores n_origin_zeros) take the exact path above.
        n_origin = (min(order, 3) if (order >= 2 and pole_type == "Complex Pair")
                    else 1)
    else:
        n_origin = 0
    return _family_from_features(order, pole_type, n_origin, wz, w0, wz_tol)
