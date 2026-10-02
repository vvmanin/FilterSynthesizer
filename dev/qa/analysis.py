# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Checks computed from one design's app snapshot (engine roots, pairing stages,
hw_sections) -- pure numpy plus the app's own classifiers.

* root conservation: every engine pole / zero sits in exactly one stage;
* cascade: the product of the stage transfer functions reproduces the engine
  H(s) (shape, and the passband gain the sidebar asked for);
* spec conformance: -alpha at the corner(s), passband ripple <= alpha, -A_s at
  the stopband edge(s) the app reports;
* FS-016 pairing flags (see FLAG_TEXT).
"""
import math
import re

import numpy as np

FLAG_TEXT = {
    "lost_pole": "engine pole missing from every stage (the cascade loses it)",
    "extra_pole": "stage pole not among the engine poles",
    "lost_zero": "engine zero missing from every stage",
    "extra_zero": "stage zero not among the engine zeros",
    "real_pair": "2nd-order stage built from two real poles (Q < 0.5)",
    "q_lt_half": "stage Q < 0.5",
    "origin_on_jw": "origin zero(s) dumped onto a stage that holds a jw zero pair",
    "floating_zeros": "zeros left unassigned: hw_sections never built",
    "pending": "section family has no solver (gated 'pending', no topology in the tool)",
    "bp3_vcvs_am": "3rd-order band-pass section: only MFB has cells (VCVS / AM 'not solvable')",
    "near_notch": "near-notch section (FS-033 dual solve: LPn/HPn + 2N)",
    "high_q": "section Q > 50",
    "very_high_q": "section Q > 100 (design not solved)",
    "gain_extreme": "section passband gain > 30 or < 1/30 (dynamic range)",
    "far_zero": "notch zero more than 20x away from f0",
    "br_real_f0": "real+real stage: f0 taken from one pole while Q is the pair's",
}
TWO_PI = 2.0 * math.pi
SB_TOL_DB = 0.15             # 0.1 dB engine margin on the reported stopband edge + 0.05 dB
UNIT_MULT = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}


def _roots(x):
    return [complex(v) for v in (x if x is not None else [])]


def _match(a, b, rel=1e-6):
    """Multiset match a -> b. Returns (unmatched a, unmatched b)."""
    b = list(b)
    left = []
    for r in a:
        best, bi = None, None
        for i, s in enumerate(b):
            d = abs(r - s)
            if d <= rel * max(abs(r), abs(s), 1e-9) + 1e-9 and (best is None or d < best):
                best, bi = d, i
        if bi is None:
            left.append(r)
        else:
            b.pop(bi)
    return left, b


def _h(roots_z, roots_p, k, w):
    s = 1j * w
    num = np.full_like(s, complex(k))
    for z in roots_z:
        num = num * (s - z)
    den = np.ones_like(s)
    for p in roots_p:
        den = den * (s - p)
    return num / den


def _db(x):
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-300))


def _parse_freqs(text, unit_default="kHz"):
    """'Lower = 0.6519 kHz,  Upper = 3,068.1 Hz (does not meet A_s)' -> [Hz...]."""
    return [f for f, _d, _n in _parse_edges(text)]


def _parse_edges(text):
    """[(Hz, half the printed rounding step in Hz, the app marked it 'does not meet')]."""
    text = str(text)
    ms = list(re.finditer(r"(\d[\d,]*)(?:\.(\d*))?(?:[eE][-+]?\d+)?\s*(GHz|MHz|kHz|Hz)", text))
    out = []
    for i, m in enumerate(ms):
        mult = UNIT_MULT[m.group(3)]
        dec = len(m.group(2) or "")
        val = float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else "")) * mult
        tail = text[m.end():ms[i + 1].start() if i + 1 < len(ms) else len(text)]
        out.append((val, 0.5 * 10 ** -dec * mult, "not meet" in tail.lower()))
    return out


def _spec_rows(spec):
    rows = {}
    for r in spec or []:
        if isinstance(r, (list, tuple)) and len(r) >= 2:
            rows[str(r[0])] = str(r[1])
    return rows


def analyse(design, snap):
    import pairing_utils as PU
    import topology_tab as TT

    eng = snap.get("engine") or {}
    stages = snap.get("stages") or []
    sections = snap.get("sections") or []
    P, Z = _roots(eng.get("poles")), _roots(eng.get("zeros"))
    k = complex(eng.get("k") or 0.0)
    flags, checks = [], {}

    def flag(code, stage=None, **detail):
        flags.append(dict(code=code, stage=stage, **detail))

    # ---- root conservation -----------------------------------------------
    sp = [r for s in stages for r in _roots(s.get("poles"))]
    sz = [r for s in stages for r in _roots(s.get("zeros"))]
    lp_, xp = _match(P, sp)
    lz, xz = _match(Z, sz)
    for r in lp_:
        flag("lost_pole", root=[r.real, r.imag], f_hz=abs(r) / TWO_PI)
    for r in xp:
        flag("extra_pole", root=[r.real, r.imag])
    for r in lz:
        flag("lost_zero", root=[r.real, r.imag], f_hz=abs(r) / TWO_PI)
    for r in xz:
        flag("extra_zero", root=[r.real, r.imag])
    checks["roots"] = dict(status="fail" if (lp_ or xp or lz or xz) else "pass",
                           n_poles=len(P), n_stage_poles=len(sp), n_zeros=len(Z),
                           n_stage_zeros=len(sz))
    if snap.get("unassigned"):
        flag("floating_zeros", ids=list(snap["unassigned"]))

    # ---- cascade reconstruction ------------------------------------------
    allr = [abs(r) for r in P + Z if abs(r) > 0]
    if P and stages and allr:
        w = np.logspace(math.log10(min(allr) / 30), math.log10(max(allr) * 30), 3000)
        He = _h(Z, P, k, w)
        Hc = np.ones_like(He)
        for s in stages:
            Hc = Hc * _h(_roots(s.get("zeros")), _roots(s.get("poles")), s.get("K_radps") or 0.0, w)
        de, dc = _db(He), _db(Hc)
        pe = float(np.max(de))
        band = de > pe - 60.0
        shape = float(np.max(np.abs((dc - float(np.max(dc))) - (de - pe))[band])) if band.any() else None
        gain_target = float(design.get("gain") or 1.0)
        if eng.get("gain_units"):
            gain_target = float(eng["gain_units"])
        gain_err = float(np.max(dc) - (pe + 20 * math.log10(gain_target))) if gain_target > 0 else None
        checks["cascade"] = dict(status="pass" if (shape is not None and shape < 0.05) else "fail",
                                 shape_err_db=shape, gain_err_db=gain_err)
        checks["cascade_gain"] = dict(status="pass" if gain_err is not None and abs(gain_err) < 0.1
                                      else "warn", gain_err_db=gain_err)
    else:
        checks["cascade"] = dict(status="na")

    # ---- spec conformance --------------------------------------------------
    _spec_checks(design, snap, P, Z, k, checks)

    # ---- per-section flags --------------------------------------------------
    max_q, fams = 0.0, []
    for s in stages:
        pr = _roots(s.get("poles"))
        n = s.get("stage_num")
        reals = [r for r in pr if abs(r.imag) <= 1e-9 * max(abs(r), 1.0)]
        if len(pr) == 2 and len(reals) == 2:
            flag("real_pair", stage=n, poles=[r.real for r in reals])
            f_geo = math.sqrt(abs(reals[0] * reals[1])) / TWO_PI
            if s.get("f0_hz") and abs(float(s["f0_hz"]) / f_geo - 1) > 1e-3:
                flag("br_real_f0", stage=n, f0_hz=float(s["f0_hz"]), f_geo_hz=f_geo)
        q = float(s.get("Q") or 0.0)
        if s.get("order", 2) >= 2 and 0 < q < 0.5 and not (len(pr) == 2 and len(reals) == 2):
            flag("q_lt_half", stage=n, Q=q)
        zr = _roots(s.get("zeros"))
        has_jw = any(abs(z.real) < 1e-9 * max(abs(z), 1) and abs(z.imag) > 0 for z in zr)
        n_origin = sum(1 for z in zr if abs(z) < 1e-12)
        if has_jw and n_origin:
            flag("origin_on_jw", stage=n, n_origin=n_origin)
    for sec in sections:
        n = sec.get("stage_num")
        q = float(sec.get("Q") or 0.0)
        max_q = max(max_q, q)
        try:
            fam = PU.family_from_section(sec)
        except Exception as e:                             # noqa: BLE001
            fam = f"error: {e}"
        kind, reason = TT.section_kind(sec)
        fams.append(dict(stage=n, order=sec.get("order"), family=fam, kind=kind,
                         f0_hz=float(sec.get("f0_hz") or 0), Q=q,
                         fz_hz=float(sec["fz_hz"]) if sec.get("fz_hz") else None))
        if kind == "pending":
            flag("pending", stage=n, family=fam, order=sec.get("order"), reason=reason)
        if fam in ("BP1LP", "BP1HP"):
            flag("bp3_vcvs_am", stage=n, family=fam)
        try:
            if PU.near_notch_section(sec):
                flag("near_notch", stage=n, fz_f0=float(sec["fz_hz"]) / float(sec["f0_hz"]), Q=q)
        except Exception:                                  # noqa: BLE001
            pass
        if q > 100:
            flag("very_high_q", stage=n, Q=q)
        elif q > 50:
            flag("high_q", stage=n, Q=q)
        try:
            g = abs(float(TT.section_dc_gain(sec)))
            if g > 30 or (0 < g < 1 / 30):
                flag("gain_extreme", stage=n, gain=g)
        except Exception:                                  # noqa: BLE001
            pass
        if sec.get("fz_hz") and sec.get("f0_hz"):
            r = float(sec["fz_hz"]) / float(sec["f0_hz"])
            if r > 20 or r < 1 / 20:
                flag("far_zero", stage=n, fz_f0=r)
    return dict(flags=flags, checks=checks, max_q=max_q, n_sections=len(sections),
                sections=fams)


def _spec_checks(design, snap, P, Z, k, checks):
    """Corner attenuation, passband ripple and stopband edges against the design."""
    resp, ft = design.get("response"), design.get("ftype")
    if resp == "Custom H(s)" or not P:
        return
    d = design.get("delay") or {}
    alpha = design.get("alpha")
    if alpha is None:
        alpha = 1.0 if resp in ("Chebyshev", "Elliptic") else 3.0103
    mult = UNIT_MULT.get(design.get("unit", "kHz"), 1e3)
    corners = []
    if d.get("mode") == "specs" or d.get("anchor") == "delay":
        rows = _spec_rows(snap.get("spec"))
        for lbl, val in rows.items():
            if "corner" in lbl.lower():
                corners += _parse_freqs(val)
    elif ft in ("Lowpass", "Highpass"):
        corners = [design["fc"] * mult]
    else:
        corners = [design["f1"] * mult, design["f2"] * mult]
    if not corners:
        return
    w_all = np.logspace(math.log10(min(abs(r) for r in P) / 100),
                        math.log10(max(abs(r) for r in P) * 100), 6000)
    peak = float(np.max(_db(_h(Z, P, k, w_all))))
    att = [float(peak - _db(_h(Z, P, k, np.array([TWO_PI * f])))[0]) for f in corners]
    err = max(abs(a - alpha) for a in att)
    status = "pass" if err < 0.05 else ("warn" if err < 0.2 else "fail")
    note = None
    if d.get("bp_map") == "delay" and ft == "Bandpass" and len(corners) == 2 and status != "pass":
        b = (corners[1] - corners[0]) / (0.5 * (corners[0] + corners[1]))
        if b > 0.3:      # FS-006 note §4.3: the translation BP keeps its corners only for b <~ 0.3
            status, note = "info", f"delay-preserving BP, b = {b:.2f} > 0.3 (FS-006 note §4.3)"
    checks["corner"] = dict(status=status, alpha_db=alpha, corners_hz=corners, att_db=att,
                            err_db=err, note=note)
    # passband ripple
    if ft == "Lowpass":
        f = np.logspace(math.log10(corners[0] / 1000), math.log10(corners[0]), 800)
    elif ft == "Highpass":
        f = np.logspace(math.log10(corners[0]), math.log10(corners[0] * 1000), 800)
    elif ft == "Bandpass":
        f = np.logspace(math.log10(corners[0]), math.log10(corners[1]), 800)
    else:
        f = np.concatenate([np.logspace(math.log10(corners[0] / 1000), math.log10(corners[0]), 400),
                            np.logspace(math.log10(corners[1]), math.log10(corners[1] * 1000), 400)])
    m = _db(_h(Z, P, k, TWO_PI * f))
    ripple = float(peak - np.min(m))
    if resp not in ("Bessel", "Equiripple Delay"):
        checks["ripple"] = dict(status="pass" if ripple <= alpha + 0.02 else "fail",
                                ripple_db=ripple, alpha_db=alpha)
    # stopband edges as reported by the app (an edge it marks 'does not meet A_s' is its
    # own, reported limit; each edge is tested over its printed rounding interval)
    rows = _spec_rows(snap.get("spec"))
    edges = []
    for lbl, val in rows.items():
        if "stopband edge" in lbl.lower():
            edges = _parse_edges(val)
    a_s = [design.get("as"), design.get("as_l"), design.get("as_u")]
    if edges and resp in ("Butterworth", "Chebyshev", "Inverse Chebyshev", "Elliptic"):
        if design.get("as_l") is not None and len(edges) == 2:
            targets = [design["as_l"], design["as_u"]]
        else:
            targets = [next((x for x in a_s if x is not None), 40.0)] * len(edges)
        att_s, short, marked = [], [], []
        for (f, dlt, nm), t in zip(edges, targets):
            fs = np.array([max(f - dlt, 1e-9), f, f + dlt])
            a = float(np.max(peak - _db(_h(Z, P, k, TWO_PI * fs))))
            att_s.append(a)
            (marked if nm else short).append(t - a)
        # the engine reports the edge where the attenuation is A_s - 0.1 dB
        # (filter_solvers.find_crossing's margin), so allow that plus 0.05 dB
        worst = max(short) if short else -1.0
        status = "fail" if worst > SB_TOL_DB else ("warn" if marked and max(marked) > SB_TOL_DB else "pass")
        checks["stopband"] = dict(status=status, edges_hz=[e[0] for e in edges], att_db=att_s,
                                  target_db=targets, shortfall_db=worst,
                                  app_marked_shortfall_db=max(marked) if marked else None)
