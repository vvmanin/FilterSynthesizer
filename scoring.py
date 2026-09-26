# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  scoring.py
#  Non-ideality scoring layer for the 3rd-order low-pass-notch section.
#
#  For each synthesized solution it computes the IDEAL response and the
#  REAL (finite A_ol, 1-pole GBWP, output Ro) response at the SAME
#  component values, extracts engineering metrics from each, and reports
#  the degradation. These become extra columns next to the sensitivity
#  score for ranking by real-world robustness.
#
#  WHY RESPONSE-BASED (not pole/zero root-finding):
#  The non-ideal TF is a high-order rational function whose expanded
#  polynomial (order ~15 after LUsolve, before the spurious common
#  factor cancels) is extremely ill-conditioned -- coefficients span
#  A_ol ~ 1e5 down to capacitor terms ~ 1e-4. np.roots on it returns
#  spurious/clustered roots and a phantom pole near 1e19 Hz, so Q and
#  stability cannot be read reliably from roots. The POINTWISE response
#  H(jw)=num/den is exact (the common factor divides out numerically;
#  verified by the ideal-limit test to 1e-10). All metrics below are
#  therefore taken from the frequency response, which is also exactly
#  what an engineer reads off a Bode plot.
# =====================================================================

import numpy as np
from scipy.optimize import minimize_scalar

from tf_derivation_v2 import (get_cases, make_response_func, cell_components,
                              p1, w0, wz, Q, K)
import sympy as sp


# ---- a few common real op-amps (A_ol [V/V], GBWP [Hz], Ro [ohm]) ----
#  Ro here is in the same unit base as your resistors (MOhm) -> convert.
#  NOTE: your solver works in MOhm. Ro of tens of ohms = e.g. 50 ohm
#  = 50e-6 MOhm. Pass opamp Ro already in MOhm to stay consistent.
OPAMP_LIBRARY = {
    # name        A_ol     GBWP(Hz)   Ro(MOhm)
    "ideal":    dict(A_ol=1e12, GBWP_hz=1e15, Ro=1e-12),
    "TL072":    dict(A_ol=2e5,  GBWP_hz=3e6,  Ro=50e-6),
    "LM358":    dict(A_ol=1e5,  GBWP_hz=1e6,  Ro=100e-6),
    "OPA1656":  dict(A_ol=5e6,  GBWP_hz=53e6, Ro=20e-6),
    "NE5532":   dict(A_ol=1e5,  GBWP_hz=10e6, Ro=30e-6),
}


# =====================================================================
# 1. METRIC EXTRACTION FROM A FREQUENCY RESPONSE
# =====================================================================
def _response_metrics(Hfun, comp, f_lo=1.0, f_hi=1e6, n=8000, notch_window=None):
    """Extract Bode metrics from a single response. Returns a dict.
    notch_window=(f_lo, f_hi) restricts where the notch is searched."""
    f = np.logspace(np.log10(f_lo), np.log10(f_hi), n)
    w = 2 * np.pi * f
    mag = np.abs(Hfun(comp, w))

    dc_gain = float(mag[:50].mean())

    # --- notch search ---
    # Restrict argmin to a window if given. Essential for the NON-IDEAL
    # response: a slow op-amp adds steep HF rolloff whose floor can be
    # deeper than the real notch, so a global argmin locks onto the grid
    # edge instead of the notch. Windowing around the (reliably detected)
    # ideal notch frequency fixes this.
    if notch_window is not None:
        wlo, whi = notch_window
        sel = (f >= wlo) & (f <= whi)
        idx = np.where(sel)[0]
        ni_local = int(idx[np.argmin(mag[sel])])
    else:
        ni_local = int(np.argmin(mag))

    lo = np.log10(f[max(ni_local - 2, 0)])
    hi = np.log10(f[min(ni_local + 2, len(f) - 1)])
    if lo >= hi:                       # notch at a grid edge
        f_notch = float(f[ni_local]); notch_mag = float(mag[ni_local])
    else:
        res = minimize_scalar(
            lambda lf: float(np.abs(Hfun(comp, np.array([2*np.pi*10**lf]))[0])),
            bounds=(lo, hi), method="bounded")
        f_notch = float(10 ** res.x); notch_mag = float(res.fun)
    notch_depth_db = float(20 * np.log10(notch_mag / dc_gain)) if dc_gain > 0 else np.nan

    # --- passband peak (resonant bump below the notch) ---
    below = f < f_notch * 0.8
    if below.any():
        sub = mag[below]
        pk_i = int(np.argmax(sub))
        peak_gain = float(sub[pk_i]); f_peak = float(f[below][pk_i])
    else:
        peak_gain = dc_gain; f_peak = f_lo
    peak_db = float(20 * np.log10(peak_gain / dc_gain)) if dc_gain > 0 else np.nan

    return {
        "dc_gain": dc_gain,
        "f_notch": f_notch,
        "notch_depth_db": notch_depth_db,
        "f_peak": f_peak,
        "peak_db": peak_db,          # peaking above DC (proxy for Q)
    }


# =====================================================================
# 2. SCORE ONE SOLUTION
# =====================================================================
def _comp_dict(sol, comp_names):
    """Component dict from a solver solution row, using the per-cell
    component names (handles topologies missing C1/C2/R4/R5/etc)."""
    d = {}
    for k in comp_names["caps"] + comp_names["resistors"]:
        v = sol.get(k)
        if v not in (None, "OPEN", "OPEN  "):
            d[k] = v
    return d

def score_solution(sol, cases, opamp, f_lo=1.0, f_hi=1e6):
    """Augment one solution dict with ideal-vs-real degradation metrics.
    `opamp` is a dict {A_ol, GBWP_hz, Ro} (Ro in MOhm)."""
    topo = sol["topology"]
    comp_names = cell_components(cases[(topo, "ideal")])
    comp = _comp_dict(sol, comp_names)

    Hid, _ = make_response_func(cases[(topo, "ideal")])
    Hni, _ = make_response_func(cases[(topo, "nonideal")])

    comp_ni = dict(comp, **opamp)
    # AM matched pair: R8 isn't in the ideal comp_names (it equals R7), but the
    # non-ideal TF carries it -> take it from the solution row. No-op for MFB +R8
    # (R8 is already in comp via comp_names) and cells whose TF ignores R8.
    if "R8" not in comp_ni and sol.get("R8") is not None:
        comp_ni["R8"] = sol["R8"]

    mi = _response_metrics(Hid, comp, f_lo, f_hi)
    # Search the REAL notch in a band around the ideal notch so the op-amp's
    # HF rolloff floor can't be mistaken for the notch (factor-of-8 window).
    fwin = (mi["f_notch"] / 8.0, mi["f_notch"] * 8.0)
    mr = _response_metrics(Hni, comp_ni, f_lo, f_hi, notch_window=fwin)

    d_notch_hz  = mr["f_notch"] - mi["f_notch"]
    d_notch_pct = 100.0 * (mr["f_notch"] / mi["f_notch"] - 1.0) if mi["f_notch"] else np.nan
    d_depth_db  = mr["notch_depth_db"] - mi["notch_depth_db"]
    d_gain_db   = (20*np.log10(mr["dc_gain"]/mi["dc_gain"])
                   if mi["dc_gain"] > 0 else np.nan)
    d_peak_db   = mr["peak_db"] - mi["peak_db"]

    # Combined non-ideality penalty (lower = more robust). Weights chosen
    # so each term is O(1) for a "typical" mediocre op-amp; tune freely.
    penalty = (abs(d_notch_pct) / 1.0          # 1% notch shift ~ 1.0
               + abs(d_depth_db) / 10.0        # 10 dB depth loss ~ 1.0
               + abs(d_gain_db)  / 0.5         # 0.5 dB gain error ~ 1.0
               + abs(d_peak_db)  / 3.0)        # 3 dB Q-peak change ~ 1.0

    out = dict(sol)
    out.update({
        "ni_f_notch_ideal": mi["f_notch"],
        "ni_f_notch_real":  mr["f_notch"],
        "ni_d_notch_pct":   d_notch_pct,
        "ni_depth_ideal_db": mi["notch_depth_db"],
        "ni_depth_real_db":  mr["notch_depth_db"],
        "ni_d_depth_db":     d_depth_db,
        "ni_d_gain_db":      d_gain_db,
        "ni_d_peak_db":      d_peak_db,
        "ni_penalty":        float(penalty),
    })
    return out


# =====================================================================
# 3. SCORE A WHOLE TABLE
# =====================================================================
def score_table(solutions, opamp="TL072", design_subs=None,
                sort_by="ni_penalty", verbose=True):
    """Score every solution. `opamp` may be a library name or a dict."""
    if isinstance(opamp, str):
        if opamp not in OPAMP_LIBRARY:
            raise ValueError(f"unknown op-amp '{opamp}'; "
                             f"choose {list(OPAMP_LIBRARY)} or pass a dict")
        op = OPAMP_LIBRARY[opamp]; op_name = opamp
    else:
        op = opamp; op_name = "custom"

    sol_names = sorted({s["topology"] for s in solutions if s.get("topology")})
    cases = get_cases(design_subs, verbose=False, topo_names=sol_names or None)
    if verbose:
        print(f"Scoring {len(solutions)} solutions against op-amp '{op_name}' "
              f"(A_ol={op['A_ol']:.0e}, GBWP={op['GBWP_hz']:.1e} Hz, "
              f"Ro={op['Ro']*1e6:.0f} ohm)")

    scored = [score_solution(s, cases, op) for s in solutions]
    if sort_by:
        scored.sort(key=lambda x: (x[sort_by] if np.isfinite(x[sort_by]) else 1e9))
    return scored


# =====================================================================
# 4. PRETTY TABLE
# =====================================================================
def print_scored_table(scored, limit=None):
    if not scored:
        print("No solutions to score.")
        return
    rows = scored if limit is None else scored[:limit]
    bar = "=" * 118
    print(bar)
    print(f"{'Idx':<3} | {'Topology':<11} | {'Sens':<5} | {'Notch shift':<11} | "
          f"{'Depth ideal/real (dB)':<22} | {'dGain':<7} | {'dPeak':<6} | {'Penalty':<7}")
    print(bar)
    for i, s in enumerate(rows):
        ns = f"{s['ni_d_notch_pct']:+.2f}%"
        depth = f"{s['ni_depth_ideal_db']:.1f}/{s['ni_depth_real_db']:.1f}"
        dg = f"{s['ni_d_gain_db']:+.3f}"
        dp = f"{s['ni_d_peak_db']:+.2f}"
        print(f"{i:<3} | {s['topology']:<11} | {s['sens_score']:<5.2f} | "
              f"{ns:<11} | {depth:<22} | {dg:<7} | {dp:<6} | {s['ni_penalty']:<7.2f}")
    print(bar)
    print("Penalty = |notch%|/1 + |dDepth|/10 + |dGain|/0.5 + |dPeak|/3  (lower = more robust)")


if __name__ == "__main__":
    # --- standalone demo with a synthetic solution row ---
    design = {p1: 2*sp.pi*508.9, w0: 2*sp.pi*486.76,
              wz: 2*sp.pi*1668.0, Q: 1.0455, K: 680.75}
    demo = [{
        "topology": "Without R7", "sens_score": 4.90,
        "C1":0.01,"C2":2.7e-4,"C3":2.7e-4,"C4":6.8e-4,
        "R1":0.043,"R2":0.1,"R3":0.2,"R4":0.15,"R5":0.3,"R6":0.5,"R7":None,
    }]
    for name in ["TL072", "OPA1656", "LM358"]:
        scored = score_table(demo, opamp=name, design_subs=design, verbose=True)
        print_scored_table(scored)
        print()


# =====================================================================
# 5. FAMILY-DISPATCHED METRIC EXTRACTION   (docs/CONTRACTS.md §4)
# =====================================================================
#   metrics_for(family, ...) is the reusable seam every later family binds to.
#   The LPn/HPn/notch branch reuses _response_metrics above unchanged (the
#   validated notch extractor); LP/HP/BP are added here. score_solution's
#   notch path is intentionally left byte-identical — generalizing it to call
#   metrics_for across families is the item-2/3/5 follow-up.
# =====================================================================
def _cross_freq(f, mag, target, going="down"):
    """Frequency where |H| crosses `target`, log-log interpolated."""
    f = np.asarray(f); mag = np.asarray(mag)
    idx = np.where(mag <= target)[0] if going == "down" else np.where(mag >= target)[0]
    if idx.size == 0:
        return float(f[-1] if going == "down" else f[0])
    i = int(idx[0])
    if i == 0:
        return float(f[0])
    x0, x1 = np.log10(f[i-1]), np.log10(f[i])
    y0, y1 = np.log10(max(mag[i-1], 1e-300)), np.log10(max(mag[i], 1e-300))
    yt = np.log10(max(target, 1e-300))
    if y1 == y0:
        return float(f[i])
    return float(10 ** (x0 + (yt - y0) * (x1 - x0) / (y1 - y0)))


def _lp_metrics(Hfun, comp, f_lo, f_hi, n=4000):
    f = np.logspace(np.log10(f_lo), np.log10(f_hi), n); w = 2*np.pi*f
    mag = np.abs(Hfun(comp, w))
    dc = float(mag[:50].mean())
    fc = _cross_freq(f, mag, dc/np.sqrt(2), "down")
    pb = mag[f < fc]
    ripple = float(20*np.log10(max(pb.max(), 1e-30)/dc)) if pb.size and dc > 0 else 0.0
    fa, fb = 3*fc, 10*fc
    ma, mb = np.abs(Hfun(comp, np.array([2*np.pi*fa, 2*np.pi*fb])))
    roll = float(20*np.log10(max(mb, 1e-30)/max(ma, 1e-30)) / (np.log10(fb)-np.log10(fa)))
    return {"dc_gain": dc, "f_c": fc, "passband_ripple_db": ripple, "rolloff_db_dec": roll}


def _hp_metrics(Hfun, comp, f_lo, f_hi, n=4000):
    f = np.logspace(np.log10(f_lo), np.log10(f_hi), n); w = 2*np.pi*f
    mag = np.abs(Hfun(comp, w))
    # 1st-order HP is monotone up -> flat plateau -> op-amp GBWP rolloff, so the
    # plateau (true HF gain) is the response maximum. Reading the band edge would
    # instead sample the op-amp rolloff when GBWP sits near f_hi.
    pk = int(np.argmax(mag)); hf = float(mag[pk])
    fc = _cross_freq(f[:pk+1], mag[:pk+1], hf/np.sqrt(2), "up") if pk > 0 else float(f[0])
    floor = float(20*np.log10(max(mag[0], 1e-30)/hf)) if hf > 0 else np.nan
    return {"hf_gain": hf, "f_c": fc, "stopband_floor_db": floor}


def _bp_metrics(Hfun, comp, f_lo, f_hi, n=6000):
    f = np.logspace(np.log10(f_lo), np.log10(f_hi), n); w = 2*np.pi*f
    mag = np.abs(Hfun(comp, w))
    pk = int(np.argmax(mag)); center = float(mag[pk]); f0 = float(f[pk])
    half = center/np.sqrt(2)
    lo = _cross_freq(f[:pk+1], mag[:pk+1], half, "up") if pk > 0 else f0
    hi = _cross_freq(f[pk:], mag[pk:], half, "down") if pk < len(f)-1 else f0
    bw = max(hi - lo, 1e-30)
    return {"center_gain": center, "f0": f0, "Q": f0/bw, "BW_-3dB": bw}


def metrics_for(family, Hfun, comp, f_lo=1.0, f_hi=1e6):
    """Family-dispatched Bode-metric extractor (docs/CONTRACTS.md §4).
       'LPn'|'HPn'|'notch' -> notch extractor (_response_metrics, unchanged)
       'LP'  -> {dc_gain, f_c, passband_ripple_db, rolloff_db_dec}
       'HP'  -> {hf_gain, f_c, stopband_floor_db}
       'BP'|'BP1LP'|'BP1HP' -> {center_gain, f0, Q, BW_-3dB}
    BP1LP/BP1HP (3rd-order asymmetric band-pass) are still single-peaked, so
    the peak-referenced BP extractor applies; the -3 dB skirts are simply
    asymmetric."""
    if family in ("LPn", "HPn", "notch"):
        return _response_metrics(Hfun, comp, f_lo, f_hi)
    if family == "LP":
        return _lp_metrics(Hfun, comp, f_lo, f_hi)
    if family == "HP":
        return _hp_metrics(Hfun, comp, f_lo, f_hi)
    if family in ("BP", "BP1LP", "BP1HP"):
        return _bp_metrics(Hfun, comp, f_lo, f_hi)
    raise ValueError(f"metrics_for: unknown family {family!r}")
