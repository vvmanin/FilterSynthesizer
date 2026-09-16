# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  first_order_solver.py   [Tier C — closed-form 1st-order realizer]
#
#  The HYBRID half that BYPASSES unified_solver_v2 (ROADMAP item 1):
#  component values come from the verified closed forms (one pole, one gain
#  -> R,C), NOT a multistart optimizer. Hid/Hni come from cells_first_order
#  (Tier B) so Bode/MC still see the real op-amp.
#
#  Returns a result dict shaped EXACTLY like filter_synthesis.synthesize:
#     {'snapped':[rows], 'continuous':[rows], 'cases':{(name,kind):case},
#      'mode':'nonideal'|'ideal'}
#  so topology_tab._render_results / response_tab consume it with no changes.
#
#  Closed forms (units: C in uF, R in MOhm, so tau = R*C directly; w0 in rad/s):
#     1LP-ni  unity : R1=1/(w0 C1)
#             gained: R1=1/(w0 C1), 1+R3/R4 = G                (G>=1)
#             atten : R1=1/(w0 G C1), R2=1/(w0 (1-G) C1)       (G<1)
#     1LP-inv       : R2=1/(w0 C1), R1=R2/|G|                  (|G| any)
#     1HP-ni  unity : R2=1/(w0 C1)                             (HF gain)
#             gained: R2=1/(w0 C1), 1+R3/R4 = G                (HF gain, G>=1)
#             atten : R2=G/(w0 C1), R1=(1-G)/(w0 C1)           (HF gain, G<1)
#     1HP-inv       : R1=1/(w0 C1), R2=|G| R1                  (|G| any)
# =====================================================================

import numpy as np
from itertools import product

import cells_first_order as fo
from tf_derivation_v2 import make_response_func
from discrete_snapper import (E_SERIES_BASE, build_merged_resistor_grid,
                              get_two_nearest)
from scoring import metrics_for

# Capacitor E-series (E3/E6 not in discrete_snapper.E_SERIES_BASE -> add here).
_CAP_SERIES = {
    "E3":  [1.0, 2.2, 4.7],
    "E6":  [1.0, 1.5, 2.2, 3.3, 4.7, 6.8],
    "E12": E_SERIES_BASE["E12"],
    "E24": E_SERIES_BASE["E24"],
}

GAIN_UNITY_TOL = 0.02        # |G-1| within this -> 'unity' mode (matches topology_tab)

# ni-gained gain-divider: the SERIES resistance R3+R4 is constrained to this band.
R3R4_SERIES_LO_MOHM, R3R4_SERIES_HI_MOHM = 0.005, 0.050   # 5 kOhm .. 50 kOhm


def build_cap_grid(series_str, c_min, c_max):
    """E-series cap grid (uF) within [c_min, c_max]. Mirrors
    discrete_snapper.build_merged_resistor_grid for resistors."""
    parts = [p.strip().upper() for p in series_str.split(",")]
    base = set()
    for p in parts:
        base.update(_CAP_SERIES.get(p, E_SERIES_BASE.get(p, [])))
    if not base:
        base.update(_CAP_SERIES["E12"])
    base = np.array(sorted(base))
    mult = [10**i for i in range(-7, 3)]                 # uF decades
    grid = np.sort([round(v*m, 12) for m in mult for v in base])
    return grid[(grid >= c_min*0.99) & (grid <= c_max*1.01)]


def nearest_lower_caps(series_str, c_max, n=3):
    """The n largest E-series cap values (uF) at or below c_max (i.e. the n
    values closest to C_max from below). The 1st-order tab makes the cap the
    design DOF, so the solutions list is realized at these n standard caps under
    the C_max bound. Returns ascending; fewer than n if the series runs out."""
    parts = [p.strip().upper() for p in series_str.split(",")]
    base = set()
    for p in parts:
        base.update(_CAP_SERIES.get(p, E_SERIES_BASE.get(p, [])))
    if not base:
        base.update(_CAP_SERIES["E12"])
    base = np.array(sorted(base))
    mult = [10**i for i in range(-7, 3)]
    grid = np.sort([round(v*m, 12) for m in mult for v in base])
    below = grid[grid <= c_max * (1 + 1e-9)]
    if below.size == 0:
        return np.array([])
    return below[::-1][:max(1, int(n))][::-1]            # n largest <= c_max, ascending


def gain_to_mode(realization, G):
    """Map a (signed-or-unsigned) target gain to the cell's gain token.
    ni: gained>=1, unity~=1, atten<1. inv: same |G| partition (one schematic)."""
    g = abs(G)
    if abs(g - 1.0) <= GAIN_UNITY_TOL:
        return "unity"
    return "gained" if g > 1.0 else "atten"


# =====================================================================
# Closed-form component values (continuous)
# =====================================================================
def closed_form_components(topo, w0, G, C1v, R4v=None):
    """Exact R,C for a target pole w0 (rad/s) and gain G. Returns physical
    component dict (present components only)."""
    fam, real, gain = topo["family"], topo["realization"], topo["gain"]
    g = abs(G)
    if real == "ni":
        if fam == "LP":
            if gain == "gained":
                return {"R1": 1/(w0*C1v), "R3": (g-1)*R4v, "R4": R4v, "C1": C1v}
            if gain == "unity":
                return {"R1": 1/(w0*C1v), "C1": C1v}
            return {"R1": 1/(w0*g*C1v), "R2": 1/(w0*(1-g)*C1v), "C1": C1v}   # atten
        else:  # HP-ni
            if gain == "gained":
                return {"R2": 1/(w0*C1v), "R3": (g-1)*R4v, "R4": R4v, "C1": C1v}
            if gain == "unity":
                return {"R2": 1/(w0*C1v), "C1": C1v}
            return {"R2": g/(w0*C1v), "R1": (1-g)/(w0*C1v), "C1": C1v}        # atten
    else:  # inverting, single schematic, gain by ratio
        if fam == "LP":
            return {"R2": 1/(w0*C1v), "R1": 1/(w0*g*C1v), "C1": C1v}
        return {"R1": 1/(w0*C1v), "R2": g/(w0*C1v), "C1": C1v}                # HP-inv


def analytic_sensitivity(topo, G):
    """Worst-case fractional sensitivity sum_|dln(w0)/dlnX| + sum_|dln(gain)/dlnX|
    over present R/C. First-order pole sensitivities are all 1 (-> 2). Gain part:
      ni unity 0 ; ni gained 2(G-1)/G ; ni atten 2(1-G) ; inv 2.
    Small, exact, and convention-free (used as the 'sens_score' column)."""
    g = abs(G)
    pole = 2.0
    if topo["realization"] == "inv":
        gain = 2.0
    elif topo["gain"] == "unity":
        gain = 0.0
    elif topo["gain"] == "gained":
        gain = 2.0*(g-1.0)/g if g > 0 else 0.0
    else:  # atten
        gain = 2.0*(1.0-g)
    return float(pole + gain)


# =====================================================================
# Snap (formula-derived resistors only; C1 and gained-R4 are on-grid)
# =====================================================================
def _snap_cost(Hcost, comp, wv, G, fam):
    """Dimensionless gain + (-3dB) shape error at [gain_probe, w0]."""
    Hv = np.abs(Hcost(comp, wv))
    gain_err = abs(Hv[0] - abs(G)) / max(abs(G), 1e-30)
    shape_ref = abs(G)/np.sqrt(2.0)
    pole_err = abs(Hv[1] - shape_ref) / max(shape_ref, 1e-30)
    return 10.0*gain_err + 10.0*pole_err, float(Hv[0])


def _snap_resistors(topo, cont, snap_names, r_grid, Hcost, cost_names, op, w0, G,
                    valid_fn=None):
    """Choose E-series neighbours of the formula-derived resistors that minimise
    the realized error against the (non-ideal or ideal) response. `valid_fn`, if
    given, is a predicate on the full snapped component dict (including the fixed
    parts, e.g. R4) that a combo must satisfy -- used for the ni-gained R3+R4
    series-range rule. Returns (None, inf, None) if no combo is valid."""
    fam = topo["family"]
    if fam == "LP":
        gain_w = max(2*np.pi, 1e-3*w0)                    # deep passband
    else:                                                 # HP plateau, kept below
        hi = 0.2*2*np.pi*op["GBWP_hz"] if op else np.inf  # the op-amp GBWP rolloff
        gain_w = max(min(10*w0, hi), 3*w0)
    wv = np.array([gain_w, w0])

    fixed = {n: cont[n] for n in cont if n not in snap_names}     # C1, gained R4
    base = dict(fixed)
    if op:
        base.update(op)
    cands = [get_two_nearest(cont[n], r_grid) for n in snap_names]

    best, best_cost, best_pb = None, float("inf"), None
    for combo in product(*cands):
        comp = dict(base)
        for nm, v in zip(snap_names, combo):
            comp[nm] = v
        if valid_fn is not None and not valid_fn(comp):
            continue
        comp_eval = {k: comp[k] for k in cost_names}             # exactly the TF's vars
        cost, pb = _snap_cost(Hcost, comp_eval, wv, G, fam)
        if cost < best_cost:
            best, best_cost, best_pb = combo, cost, pb
    if best is None:
        return None, float("inf"), None
    return dict(zip(snap_names, best)), best_cost, best_pb


# =====================================================================
# Public entry
# =====================================================================
def synthesize_first_order(cfg, opamp=None, topology=None, dc_gain=None,
                           top_k=30, **_ignored):
    """Closed-form synthesize for ONE 1st-order cell name (e.g. '1HP-ni-gained').

    cfg : {f0, C_max, R_series, C_series}  (internal units: C in uF, R in MOhm).
          By default ('cap_mode'=='nearest_lower') the cap is the design DOF: the
          pole is realized at the `n_caps` (default 3) E-series cap values nearest
          C_max from below. (Legacy: pass cap_mode!='nearest_lower' with C_min to
          sweep the full [C_min,C_max] grid via build_cap_grid.) `dc_gain` is the
          target passband gain (DC for LP, HF for HP); None -> unity. `opamp` is
          {A_ol,GBWP_hz,Ro(MOhm)} or None. R_min/R_max bound the resistor grid
          (the 1st-order tab supplies wide internal defaults).
    ni-gained: the gain branch R3+R4 series resistance is held in [5k,50k]; among
          those, lowest snap cost wins. Extra kwargs are accepted and ignored."""
    topo = fo.parse_name(topology)
    if topo is None:
        return {"__error__": f"not a first-order cell name: {topology!r}"}

    w0 = 2*np.pi*float(cfg["f0"])
    G = abs(float(dc_gain)) if dc_gain is not None else 1.0
    sign = fo.sign_of(topo)

    case_id = fo.derive_first_order_ideal(topo)
    case_ni = fo.derive_first_order_nonideal(topo)
    Hid, id_names = make_response_func(case_id)
    Hni, ni_names = make_response_func(case_ni)
    cases = {(topology, "ideal"): case_id, (topology, "nonideal"): case_ni}

    op = opamp if isinstance(opamp, dict) else None
    Hcost, cost_names = (Hni, ni_names) if op else (Hid, id_names)
    metric_w = (1.0, 1e6)

    r_grid = build_merged_resistor_grid(cfg["R_series"], cfg["R_min"], cfg["R_max"])
    if cfg.get("cap_mode", "nearest_lower") == "nearest_lower":
        cap_grid = nearest_lower_caps(cfg["C_series"], cfg["C_max"],
                                      int(cfg.get("n_caps", 3)))
    else:
        cap_grid = build_cap_grid(cfg["C_series"], cfg.get("C_min", 0.0), cfg["C_max"])
    if r_grid.size == 0 or cap_grid.size == 0:
        return {"snapped": [], "continuous": [], "cases": cases,
                "mode": "nonideal" if op else "ideal"}

    # ni-gained has a 2nd DOF (R4). Hold the gain branch's SERIES resistance
    # R3+R4 in [5k,50k]. R3 = (G-1)*R4 so the nominal series is G*R4 ->
    # R4 in [5k/G, 50k/G]; the exact post-snap range is enforced by gain_valid.
    is_gained_ni = topo["gain"] == "gained" and topo["realization"] == "ni"
    gain_valid = None
    if is_gained_ni:
        lo, hi = R3R4_SERIES_LO_MOHM / G, R3R4_SERIES_HI_MOHM / G
        r4_cands = [float(r) for r in r_grid if lo * 0.7 <= r <= hi * 1.4]
        if len(r4_cands) > 24:                        # bound the search
            r4_cands = r4_cands[::max(1, len(r4_cands) // 24)]
        gain_valid = (lambda comp: R3R4_SERIES_LO_MOHM
                      <= comp.get("R3", 0.0) + comp.get("R4", 0.0)
                      <= R3R4_SERIES_HI_MOHM)
    else:
        r4_cands = [None]

    sens = analytic_sensitivity(topo, G)
    comp_names = fo.cell_components(topo)
    present_R = comp_names["resistors"]

    seen, snapped, continuous = set(), [], []
    for C1v in cap_grid:
        for R4v in r4_cands:
            cont = closed_form_components(topo, w0, G, float(C1v), R4v)
            if any(v <= 0 for v in cont.values()):
                continue
            # formula-derived resistors that get snapped (C1 + gained-R4 stay on grid)
            snap_names = [n for n in present_R
                          if not (topo["gain"] == "gained" and n == "R4")]
            # reject if any formula resistor is outside the realizable envelope
            if any(not (cfg["R_min"]*0.99 <= cont[n] <= cfg["R_max"]*1.01)
                   for n in snap_names):
                continue

            snap_R, cost, _pb = _snap_resistors(
                topo, cont, snap_names, r_grid, Hcost, cost_names, op, w0, G,
                valid_fn=gain_valid)
            if snap_R is None:                      # no neighbour meets the constraint
                continue

            row = {"topology": topology, "sign": sign, "internal_gain": G,
                   "sens_score": sens, "snap_cost": float(cost)}
            row.update({n: cont[n] for n in cont if n not in snap_names})   # C1, R4
            row.update(snap_R)

            key = tuple(round(row.get(k, 0.0), 12) for k in
                        ("C1", "R1", "R2", "R3", "R4"))
            if key in seen:
                continue
            seen.add(key)

            cont_row = {"topology": topology, "sign": sign, **cont}
            snapped.append(row); continuous.append(cont_row)

    order = sorted(range(len(snapped)),
                   key=lambda i: (snapped[i]["snap_cost"], snapped[i]["sens_score"]))
    snapped = [snapped[i] for i in order][:top_k]
    continuous = [continuous[i] for i in order][:top_k]

    # realized passband gain (DC for LP / HF for HP), only for the kept rows
    for row in snapped:
        eval_comp = {k: row[k] for k in cost_names if k in row}
        if op:
            eval_comp.update(op)
        try:
            m = metrics_for(topo["family"], Hcost, eval_comp, *metric_w)
            row["_dc"] = float(m.get("dc_gain", m.get("hf_gain", np.nan)))
            row["ni_f_c"] = float(m.get("f_c", np.nan))
        except Exception:
            row["_dc"] = None

    return {"snapped": snapped, "continuous": continuous, "cases": cases,
            "mode": "nonideal" if op else "ideal"}
