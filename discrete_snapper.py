# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
# discrete_snapper.py
# Post-processing snapper with stabilized cost metrics.
# =====================================================================
import numpy as np
import time
from itertools import product
from tf_derivation_v2 import make_response_func, cell_components, all_cells, topo_name

E_SERIES_BASE = {
    "E12": [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2],
    "E24": [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1],
    "E48": [1.00, 1.05, 1.10, 1.15, 1.21, 1.27, 1.33, 1.40, 1.47, 1.54, 1.62, 1.69, 1.78, 1.87, 1.96, 2.05, 2.15, 2.26, 2.37, 2.49, 2.61, 2.74, 2.87, 3.01, 3.16, 3.32, 3.48, 3.65, 3.83, 4.02, 4.22, 4.42, 4.64, 4.87, 5.11, 5.36, 5.62, 5.90, 6.19, 6.49, 6.81, 7.15, 7.50, 7.87, 8.25, 8.66, 9.09, 9.53],
    "E96": [1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24, 1.27, 1.30, 1.33, 1.37, 1.40, 1.43, 1.47, 1.50, 1.54, 1.58, 1.62, 1.65, 1.69, 1.74, 1.78, 1.82, 1.87, 1.91, 1.96, 2.00, 2.05, 2.10, 2.15, 2.21, 2.26, 2.32, 2.37, 2.43, 2.49, 2.55, 2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01, 3.09, 3.16, 3.24, 3.32, 3.40, 3.48, 3.57, 3.65, 3.74, 3.83, 3.92, 4.02, 4.12, 4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23, 5.36, 5.49, 5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65, 6.81, 6.98, 7.15, 7.32, 7.50, 7.68, 7.87, 8.06, 8.25, 8.45, 8.66, 8.87, 9.09, 9.31, 9.53, 9.76]
}

def build_merged_resistor_grid(series_str, r_min, r_max):
    parts = [p.strip().upper() for p in series_str.split(',')]
    combined_base = set()
    for p in parts:
        if p in E_SERIES_BASE:
            combined_base.update(E_SERIES_BASE[p])
    base_array = np.array(sorted(list(combined_base)))
    multipliers = [10**i for i in range(-4, 2)] 
    grid = np.sort([round(v * m, 8) for m in multipliers for v in base_array])
    return grid[(grid >= r_min*0.99) & (grid <= r_max*1.01)]

def get_two_nearest(val, grid):
    idx = np.searchsorted(grid, val)
    cands = []
    if idx > 0: cands.append(grid[idx - 1])
    if idx < len(grid): cands.append(grid[idx])
    return list(dict.fromkeys(cands)) or [grid[np.abs(grid - val).argmin()]]

def get_T_target(w_array, cfg, order=3, notch=True, family="LP", absorb=None):
    s = 1j * w_array
    p1 = 2 * np.pi * cfg['f1']
    w0 = 2 * np.pi * cfg['f0']
    wz = 2 * np.pi * cfg['fz']
    Q = cfg['Q']; K = cfg.get('K', 1.0)
    den = (s**2 + (w0/Q)*s + w0**2)
    if order == 3:
        den = (s + p1) * den
    if family in ("HP", "HP-MFB", "HP-AM"):
        # High-pass: origin zeros block DC (passband sits at HF); a notch adds
        # an on-axis (s^2+wz^2) pair. Numerator order == filter order.
        num = K * (s**2 + wz**2) * s**(order - 2) if notch else K * s**order
    elif family in ("BP", "BP-MFB", "BP-AM"):
        # Band-pass: a SINGLE s-term numerator (one origin zero, one at infinity).
        # The peak sits at w0; |H(jw0)| = K*Q/w0 with this monic denominator.
        # BP-MFB shares this exact shape (inverting; magnitude identical), so it
        # normalizes at the peak just like the VCVS band-pass.
        #
        # EXCEPT when a real pole has been absorbed to steepen the LOWER skirt
        # (absorb="hp", cells 2BP1HP-MFB / -QE): there the denominator is cubic
        # AND the numerator is s^2, so the section still falls 1/s at HF but
        # rises s^2 at LF. absorb="lp" keeps num ~ s against the same cubic, so
        # it needs no special case. Getting this wrong silently mis-scores every
        # snap and mis-draws the design overlay by a whole 20 dB/decade.
        num = K * s**2 if absorb == "hp" else K * s
    else:
        num = K * (s**2 + wz**2) if notch else K
    return num / den

def snap_to_hardware(solutions, cfg, opamp, cases, target_dc=None):
    """Snap continuous resistors to E-series against exact non-ideal physics.

    `target_dc` (linear passband DC gain) is the gain-error reference. If
    None it is taken PER-SOLUTION from that solution's own ideal DC gain
    (which already realizes the design target). This keeps the gain metric
    correct regardless of K's (rad/s)^n units -- in DC-gain mode cfg has no
    'K', so the K-scaled abs(T_targ[0]) is NOT a valid DC reference and would
    inflate gained-cell costs (2LPn ~x10, 3LPn ~x1000). The shape terms use
    the ratio T_targ/T_targ[0], where K cancels, so they need no change."""
    print(f"Discrete Hardware Snapper | Resistor Grid: [{cfg['R_series']}]")
    t0 = time.time()

    r_grid = build_merged_resistor_grid(cfg['R_series'], cfg['R_min'], cfg['R_max'])

    Hni_funcs = {}; Hid_funcs = {}; comp_maps = {}; topo_meta = {}
    for (name, model) in cases:
        if model != "ideal":
            continue
        if (name, "nonideal") not in cases:
            continue
        Hni_funcs[name], _n = make_response_func(cases[(name, "nonideal")])
        Hid_funcs[name], _i = make_response_func(cases[(name, "ideal")])
        comp_maps[name] = cell_components(cases[(name, "ideal")])
        topo_meta[name] = cases[(name, "ideal")]["topo"]   # carries "family"

    snapped_results = []

    for sol in solutions:
        topo = sol['topology']
        meta = topo_meta[topo]
        order, notch = meta["order"], meta["notch"]
        family = meta.get("family", "LP")
        absorb = meta.get("absorb")      # BP-MFB 3rd order: "hp" -> num ~ s^2
        cmap = comp_maps[topo]
        r_names = cmap["resistors"]
        cap_names = cmap["caps"]

        # Passband reference point (w_pts[0], the gain + normalization anchor).
        # LP passband is DC -> w ~ 0 (use 1 rad/s). HP passband is the HF
        # plateau: a decade above cutoff but safely below the op-amp GBWP
        # rolloff, so the REAL-op-amp gain is read on the plateau (not on the
        # rolloff, where a finite-GBWP part would read ~0).
        if family in ("HP", "HP-MFB", "HP-AM"):
            f_hf = cfg['f0'] * 10.0
            gb = opamp.get('GBWP_hz', 1e15)
            f_hf = max(min(f_hf, gb / 5.0), cfg['f0'] * 3.0)
            w_ref = 2 * np.pi * f_hf
        elif family in ("BP", "BP-MFB", "BP-AM"):
            # Band-pass passband IS the resonant peak at f0 -> read the gain and
            # normalize the shape there (no DC/HF plateau exists). Applies to the
            # MFB band-pass too: at DC/HF a band-pass reads ~0, so normalizing at
            # w=1 (the else-branch default) would divide the shape by ~0 and
            # inflate snap_cost by many orders of magnitude.
            w_ref = 2 * np.pi * cfg['f0']
        else:
            w_ref = 1.0

        # Q/bandwidth shape point — normally f0. But a PURE notch (fz == f0, the
        # 2N family) puts the transmission ZERO exactly at f0, so the target
        # magnitude there is 0 and the fractional-error Q metric is degenerate:
        # it would divide by the 1e-6 floor and inflate the cost by ~1e6. Move
        # the Q point to the notch -3dB skirt w0*(1 + 1/(2Q)), where the target is
        # ~0.707*passband and still Q-sensitive. The notch DEPTH and centering are
        # covered separately by the 'notch' point at fz. (LPn/HPn keep f0 — there
        # the zero is off f0, so the target at f0 is non-zero and well-posed.)
        pure_notch = notch and abs(cfg['fz'] / cfg['f0'] - 1.0) < 0.05
        # Band-pass: both target and realized are normalized to their peak at f0,
        # so the Q metric AT f0 is degenerate (~1 either way). Move the Q point to
        # the -3dB skirt f0*(1 + 1/(2Q)), where |H| ~0.707*peak and is strongly
        # Q-sensitive -- exactly the pure-notch skirt trick, for the same reason.
        if family in ("BP", "BP-MFB", "BP-AM"):
            f_q = cfg['f0'] * (1.0 + 1.0 / (2.0 * max(cfg['Q'], 1e-6)))
        else:
            f_q = cfg['f0'] * (1.0 + 1.0 / (2.0 * max(cfg['Q'], 1e-6))) if pure_notch else cfg['f0']

        w_pts = [w_ref, 2*np.pi*f_q]
        tags = ['gain', 'Q']
        if order == 3:
            w_pts.insert(1, 2*np.pi*cfg['f1']); tags.insert(1, 'pole')
        if notch:
            w_pts.append(2*np.pi*cfg['fz']); tags.append('notch')
        w_evals = np.array(w_pts)
        T_targ = get_T_target(w_evals, cfg, order=order, notch=notch,
                              family=family, absorb=absorb)

        is_unity = (meta.get("gain", "gained") == "unity")
        ref = 1.0 if is_unity else abs(T_targ[0])
        if ref <= 0:
            ref = 1.0

        # TRUE target passband gain (linear), K-convention-independent. For LP
        # this is the DC gain; for HP the HF gain. Priority: explicit arg ->
        # the solution's own ideal gain (read at w_ref) -> 1.0.
        if is_unity:
            tgt_dc = 1.0
        elif target_dc is not None:
            tgt_dc = float(target_dc)
        else:
            _ic = {c: sol[c] for c in cap_names}
            for _rn in r_names:
                _ic[_rn] = sol[_rn]
            try:
                tgt_dc = float(abs(Hid_funcs[topo](_ic, np.array([w_ref]))[0]))
            except Exception:
                tgt_dc = 1.0
            if tgt_dc <= 0:
                tgt_dc = 1.0

        Tn = np.abs(T_targ) / abs(T_targ[0]) if abs(T_targ[0]) > 0 else np.abs(T_targ)

        r_cands = [get_two_nearest(sol[r], r_grid) for r in r_names]
        base_comp = {c: sol[c] for c in cap_names}
        base_comp.update(opamp)

        best_cost = float('inf'); best_combo = None
        for combo in product(*r_cands):
            comp_dict = dict(base_comp)
            for idx, nm in enumerate(r_names):
                comp_dict[nm] = combo[idx]
            # AM matched pair: R8 == R7 (R8 is not a separately-snapped resistor).
            # The non-ideal TF carries R8, so mirror it from the snapped R7. (MFB
            # HP +R8 twins already carry R8 in r_names -> this is a no-op there,
            # and cells whose TF ignores R8 just get a harmless extra key.)
            if "R8" not in comp_dict and "R7" in comp_dict:
                comp_dict["R8"] = comp_dict["R7"]
            H_real = Hni_funcs[topo](comp_dict, w_evals)

            H_dc = abs(H_real[0])           # gain at w_ref (DC for LP, HF for HP)
            Hn = np.abs(H_real) / H_dc if H_dc > 0 else np.abs(H_real)

            # =====================================================================
            # DIMENSIONLESS FRACTIONAL ERROR SCORING (K-Dimension Invariant)
            # =====================================================================
            cost = 0.0
            for i, tag in enumerate(tags):
                if tag == 'gain':
                    # passband gain tracking error vs the TRUE target gain
                    cost += (abs(H_dc - tgt_dc) / max(tgt_dc, 1e-30)) * 10

                elif tag == 'pole':
                    # Pure percentage shape deviation relative to localized pole coordinate
                    cost += (abs(Hn[i] - Tn[i]) / max(Tn[i], 1e-6)) * 10

                elif tag == 'Q':
                    # Pure percentage shape deviation relative to localized resonance coordinate
                    cost += (abs(Hn[i] - Tn[i]) / max(Tn[i], 1e-6)) * 40

                elif tag == 'notch':
                    # Transmitted leakage at the zero point relative to passband voltage envelope
                    cost += Hn[i] * 20          

            if cost < best_cost:
                best_cost = cost; best_combo = combo

        # Attenuator (HP): the HF gain is set by a CAPACITIVE divider (C1/C2/C4)
        # that the resistor snap cannot change, so it is a per-solution penalty
        # ranking cap-combos by HF-gain error (the C4 trap). Constant across
        # resistor combos -> added to the final cost, not inside the loop.
        if family == "HP" and meta.get("gain", "gained") == "atten":
            C2v = sol.get("C2"); C4v = sol.get("C4")
            realized = None
            if C2v and C4v:
                if order == 3:
                    C1v = sol.get("C1") or 0.0
                    den = C1v*C2v + C1v*C4v + C2v*C4v
                    realized = (C1v*C2v/den) if den else None
                else:
                    realized = C2v / (C2v + C4v)
            if realized is not None:
                # for atten, target_dc carries the HF-gain target (else tgt_dc
                # equals the realized gain and this term is ~0, i.e. no target)
                best_cost += (abs(realized - tgt_dc) / max(tgt_dc, 1e-30)) * 20

        # HP-MFB all-pole (basic, non-QE): the HF gain is a CAPACITIVE ratio
        # (C2/C4 for 2nd order; C1*C2/(C4*(C1+C2)) for 3rd) that the resistor
        # snap cannot touch -- the poles are all-resistor. So, exactly like the
        # VCVS-HP atten cell above, rank cap-combos by HF-gain error with a
        # per-solution penalty (constant across resistor combos) so the lowest-
        # gain-error cap combo harvest produced surfaces to the top of the list
        # ("as low as cap snapping allows"). The QE twin (R4/R5 divider) and the
        # HPn notch (R8/(R3+R8)) BOTH carry a continuous resistor gain knob, so
        # they snap tight inside the loop above and are deliberately NOT penalized
        # here -- their gain tolerance matches VCVS / LP-MFB.
        if family == "HP-MFB" and not notch and not meta.get("qe"):
            C2v = sol.get("C2"); C4v = sol.get("C4")
            realized = None
            if C2v and C4v:
                if order == 3:
                    C1v = sol.get("C1") or 0.0
                    realized = (C1v * C2v / (C4v * (C1v + C2v))) if (C1v + C2v) else None
                else:
                    realized = C2v / C4v
            if realized is not None:
                best_cost += (abs(realized - tgt_dc) / max(tgt_dc, 1e-30)) * 20

        # AM: the HP-family and pure-notch AM cells set their passband gain by
        # the CAPACITIVE ratio C1/C2 (x the C4/(C4+C1) input divider at 3rd
        # order) -- the poles are all-resistor tunable but the gain is
        # cap-locked, so (exactly like the VCVS-HP atten and HP-MFB all-pole
        # cases above) rank cap-combos by gain error with a per-solution
        # penalty, constant across resistor combos. LP-AM (R6/R2, R5/R1) and
        # BP-AM carry resistor gain knobs and snap tight inside the loop.
        if family in ("HP-AM", "NOTCH-AM"):
            C1v = sol.get("C1"); C2v = sol.get("C2")
            realized = None
            if C1v and C2v:
                realized = C1v / C2v
                if order == 3:
                    C4v = sol.get("C4") or 0.0
                    realized = realized * (C4v / (C4v + C1v)) if (C4v + C1v) > 0 else None
            if realized is not None:
                best_cost += (abs(realized - tgt_dc) / max(tgt_dc, 1e-30)) * 20

        out = dict(sol)
        for idx, nm in enumerate(r_names):
            out[nm] = best_combo[idx]
        # AM matched pair: R8 is not separately snapped (not in r_names); it
        # tracks the SNAPPED R7 value. (MFB HP +R8 twins DO snap R8 as an
        # independent resistor -> it's in r_names -> this guard leaves it alone.)
        if out.get("R8") is not None and "R8" not in r_names and "R7" in out:
            out["R8"] = out["R7"]
        out['snap_cost'] = best_cost
        snapped_results.append(out)

    print(f"  -> Evaluated true non-ideal permutations for {len(solutions)} layouts in {time.time()-t0:.2f}s.")
    return snapped_results

# =====================================================================
# Value formatting (engineering notation)
# =====================================================================
def _fmt_cap(val_uF):
    if val_uF in (None, 0, 0.0):
        return "--"
    pF = val_uF * 1e6                       
    if pF < 1000:                           
        v, unit = pF, "p"
    elif pF < 1e6:                          
        v, unit = pF / 1e3, "n"
    else:                                   
        v, unit = pF / 1e6, "u"
    s = f"{v:.3g}"
    return f"{s}{unit}"

def _fmt_res(val_MOhm, snapped=False):
    if val_MOhm in (None, "OPEN", "OPEN  ", 0, 0.0):
        return "--"
    ohm = val_MOhm * 1e6                     
    if ohm < 1e3:
        v, unit = ohm, ""
    elif ohm < 1e6:
        v, unit = ohm / 1e3, "k"
    else:
        v, unit = ohm / 1e6, "M"
    if snapped:
        s = f"{v:.3g}"                       
    else:
        s = f"{v:.1f}"                       
    return f"{s}{unit}"

def print_snapped_table(solutions, limit=20, title="DISCRETE HARDWARE BOM",
                        per_topology=False, per_topology_limit=None,
                        snapped=None):
    if not solutions:
        return

    keyf = lambda x: x.get('sens_score', x.get('score', float('inf')))

    if per_topology:
        ptl = per_topology_limit if per_topology_limit is not None else limit
        by = {}
        for s in solutions:
            by.setdefault(s.get('topology', 'Unknown'), []).append(s)
        rows = []
        for topo in sorted(by, key=lambda t: min(keyf(s) for s in by[t])):
            grp = sorted(by[topo], key=keyf)
            if ptl is not None:
                grp = grp[:ptl]
            rows.extend(grp)
    else:
        rows = sorted(solutions, key=keyf)
        if limit is not None:
            rows = rows[:limit]

    if snapped is None:
        snapped = any(s.get('snap_cost') is not None for s in rows)

    def _cap2(s):
        if s.get("C2_parallel") and s.get("C2a") and s.get("C2b"):
            return f"{_fmt_cap(s['C2a'])},{_fmt_cap(s['C2b'])}"
        return _fmt_cap(s.get("C2"))

    wI, wT, wS, wC, wCAP, wC2, wR = 3, 14, 5, 6, 7, 13, 8
    bar = "=" * 150
    print(f"\n[{title}]")
    print(bar)
    print(f"{'Idx':<{wI}} | {'Topology':<{wT}} | {'Sens':<{wS}} | {'Cost':<{wC}} | "
          f"{'C1':<{wCAP}} | {'C2':<{wC2}} | {'C3':<{wCAP}} | {'C4':<{wCAP}} | "
          f"{'R1':<{wR}} | {'R2':<{wR}} | {'R3':<{wR}} | {'R4':<{wR}} | "
          f"{'R5':<{wR}} | {'R6':<{wR}} | {'R7':<{wR}}")
    print(bar)

    for i, s in enumerate(rows):
        cost_val = s.get('snap_cost')
        cost_str = f"{cost_val:.2f}" if cost_val is not None else "N/A"
        sens = s.get('sens_score', s.get('score', 0.0))
        print(f"{i:<{wI}} | {s.get('topology','Unknown'):<{wT}} | {sens:<{wS}.2f} | "
              f"{cost_str:<{wC}} | "
              f"{_fmt_cap(s.get('C1')):<{wCAP}} | {_cap2(s):<{wC2}} | "
              f"{_fmt_cap(s.get('C3')):<{wCAP}} | {_fmt_cap(s.get('C4')):<{wCAP}} | "
              f"{_fmt_res(s.get('R1'),snapped):<{wR}} | {_fmt_res(s.get('R2'),snapped):<{wR}} | "
              f"{_fmt_res(s.get('R3'),snapped):<{wR}} | {_fmt_res(s.get('R4'),snapped):<{wR}} | "
              f"{_fmt_res(s.get('R5'),snapped):<{wR}} | {_fmt_res(s.get('R6'),snapped):<{wR}} | "
              f"{_fmt_res(s.get('R7'),snapped):<{wR}}")
    print(bar)