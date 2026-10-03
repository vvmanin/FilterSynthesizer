# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""The test matrix -- THE place to edit when the tool gains a response, option
or limit (see README "Extending").

A *design* is one sidebar state (plus the two Biquad-Pairing choices), written
in the harness's own vocabulary and applied to the real app by tasks_ui. A
*variant* is one Topology-tab configuration of a design (family for every
section, shared op-amp, envelope, gain override, family option).

Practical bounds, not exhaustive ones: orders <= 8 (BP/BR <= 5 per side),
A_s <= 80 dB, band ratios 1.2 ... 50, corners 100 Hz ... 200 kHz, and designs
whose largest section Q exceeds MAX_Q_SOLVE are pairing-analysed but not solved.
Everything is seeded: the same seed and matrix give the same designs, so run
directories compare by design id.
"""
import hashlib
import itertools
import json
import random

RESPONSES = ("Butterworth", "Chebyshev", "Inverse Chebyshev", "Elliptic",
             "Bessel", "Equiripple Delay", "Custom H(s)")
DELAY_RESPONSES = ("Bessel", "Equiripple Delay")
TYPES = ("Lowpass", "Highpass", "Bandpass", "Band-Reject")
SHORT = {"Butterworth": "BW", "Chebyshev": "CH", "Inverse Chebyshev": "IC", "Elliptic": "EL",
         "Bessel": "BS", "Equiripple Delay": "ED", "Custom H(s)": "CU",
         "Lowpass": "LP", "Highpass": "HP", "Bandpass": "BP", "Band-Reject": "BR"}

MAX_Q_SOLVE = 100.0          # designs above this are pairing-analysed only
SNAP_COST_WARN = 5.0         # best snap cost above this -> DEGRADED
SHAPE_ERR_WARN = 0.10        # best-snap ideal-case |H| deviation (fraction of peak) -> DEGRADED

# --- per-dimension value sets (kept practical) ------------------------------
LP_FREQS = (("Hz", 100.0), ("kHz", 1.0), ("kHz", 20.0), ("kHz", 200.0))
BANDS = (("kHz", 1.0, 1.2), ("kHz", 1.0, 2.0), ("kHz", 1.0, 5.0), ("Hz", 100.0, 2000.0),
         ("Hz", 50.0, 2500.0), ("kHz", 10.0, 15.0))
GAINS = (1.0, 2.0, 10.0)
CHEB_ALPHA = (0.1, 0.5, 1.0, 3.0)
ELL_ALPHA = (0.1, 0.5, 1.0)
STOP_AS = (30.0, 40.0, 60.0, 80.0)
DELAY_RIPPLE = (0.5, 1.0, 5.0)               # Equiripple Delay +-delta (%)
GAIN_DIST = ("even", "first", "last")         # + "equalize" for band-reject

# --- variants ---------------------------------------------------------------
FAMILIES = ("VCVS", "MFB", "AM")
OPAMPS = ("ideal", "typical", "slow")         # resolved against the live library in tasks_ui
ENVELOPES = {                                 # UI units: C in uF, R in kOhm
    "default": dict(cmin=6.8e-5, cmax=1e-2, rmin=0.3, rmax=2000.0, ratio=500.0,
                    cser="E12", rser=("E48",)),
    "narrow": dict(cmin=1e-3, cmax=0.1, rmin=1.0, rmax=100.0, ratio=100.0,
                   cser="E6", rser=("E24",)),
    "wide": dict(cmin=1e-5, cmax=10.0, rmin=0.1, rmax=10000.0, ratio=2000.0,
                 cser="E12", rser=("E24", "E96")),
}
GAINVARS = ("design", "atten")                # atten: per-section custom gain 0.5
ATTEN_GAIN = 0.5
FAMOPTS = ("none", "alt")     # alt: MFB LS / Gained-MFB, AM Equalize off, 1st-order inverting
PRESET = "Balanced"

# --- levels -----------------------------------------------------------------
# draws = random parameter draws per structural candidate; solve_frac = share of
# designs whose variants are captured and solved; rounds = variant rounds per
# solved design (3 families each); e2e = designs taken end-to-end (report + LTspice).
LEVELS = {     # wall times measured on a Ryzen 9 7950X3D (16 cores / 32 threads), warm cache
    "smoke":    dict(draws=1, per_cell=1, solve_frac=1.0, rounds=1, e2e=4, rotate=False,      # ~2-3 min
                     bench_presets=("Fast",), bench_ids=("LP2", "HPn2", "BP2", "N2"),
                     check_quick=True),
    "standard": dict(draws=3, per_cell=None, solve_frac=0.6, rounds=2, e2e=24, rotate=True,   # ~10-15 min
                     bench_presets=("Balanced",), bench_ids=None, check_quick=True),
    "full":     dict(draws=8, per_cell=None, solve_frac=1.0, rounds=3, e2e=60, rotate=True,   # ~1 h + build
                     presets=("Balanced", "Fast", "Thorough"),
                     bench_presets=("Fast", "Balanced", "Thorough"), bench_ids=None,
                     check_quick=False),
}
TIMEOUTS = dict(design=900, solve=900, e2e=1800, ltspice=180, check=3600, bench=900,
                build=2400, exe=600, server=300)


# =============================================================================
# pinned designs: known findings, run at every level until they are fixed (and
# after, as regression guards). (response, type, structure, parameters, why)
# =============================================================================
PINNED = [
    # FS-016 (fixed 2026-10-03) -- regression guards. The text names the flag code
    # (or "exception") the summary matches on, so a regression reads "still there".
    ("Butterworth", "Bandpass", dict(order=1, asym=False, mods={}),
     dict(unit="Hz", f1=50.0, f2=2500.0, gain=2.0, absorb=True, gain_dist="even"),
     "guard, FS-016 fixed: app exception in auto_pair_bandpass (two real BP poles, 3rd-order on)"),
    ("Butterworth", "Bandpass", dict(order=3, asym=False, mods={}),
     dict(unit="Hz", f1=50.0, f2=2500.0, gain=1.0, absorb=False, gain_dist="even"),
     "guard, FS-016 fixed: lost_pole -- a second real pole of a wide-band BP dropped"),
    ("Elliptic", "Band-Reject", dict(order=3, asym=False, mods={}),
     dict(unit="Hz", f1=50.0, f2=2500.0, gain=1.0, alpha=1.0, **{"as": 40.0}, absorb=False,
          gain_dist="even"),
     "guard, FS-016 fixed: br_real_f0 -- real+real section f0 taken from one pole"),
    ("Inverse Chebyshev", "Bandpass", dict(order=None, lp=3, hp=4, asym=True, mods={}),
     dict(unit="kHz", f1=1.0, f2=2.0, gain=1.0, as_l=40.0, as_u=40.0, absorb=True,
          gain_dist="even"),
     "guard, FS-016 fixed: unrealizable -- real pole absorbed into an HPn stage, no origin zero"),
    ("Inverse Chebyshev", "Bandpass", dict(order=None, lp=2, hp=1, asym=True, mods={}),
     dict(unit="kHz", f1=1.0, f2=2.0, gain=1.0, as_l=40.0, as_u=40.0, absorb=True,
          gain_dist="even"),
     "guard, FS-016 fixed: unrealizable -- real pole + origin zero dumped onto an LPn stage"),
]


# =============================================================================
# structural candidates per response
# =============================================================================
def _orders(resp, ft):
    """[(order|None, lp, hp, asym)] -- order limits per the sidebar, trimmed to practice."""
    out = []
    if ft in ("Lowpass", "Highpass"):
        lo = 2 if resp in ("Elliptic", "Inverse Chebyshev") else 1
        out += [(n, None, None, False) for n in range(lo, 9)]
    elif ft == "Bandpass":
        lo = 2 if resp in ("Elliptic", "Inverse Chebyshev") else 1
        out += [(n, None, None, False) for n in range(lo, 6)]
        out += [(None, lp, hp, True) for lp, hp in ((2, 3), (3, 2), (4, 2), (2, 4), (1, 3))
                if not (resp in ("Elliptic", "Inverse Chebyshev") and min(lp, hp) < 2)]
    else:  # Band-Reject: total order even; no Asymmetric for Chebyshev / Elliptic
        lo = 2 if resp in ("Elliptic", "Inverse Chebyshev") else 1
        out += [(n, None, None, False) for n in range(lo, 6)]
        if resp in ("Butterworth", "Inverse Chebyshev"):
            out += [(None, lp, hp, True) for lp, hp in ((1, 3), (3, 1), (2, 4))
                    if not (resp == "Inverse Chebyshev" and min(lp, hp) < 2)]
    return out


def _mods(resp, ft, order, lp, hp, asym):
    """Response Modifications combinations shown for this state."""
    opts = [{}]
    lp_order = order if not asym else lp
    even = lp_order is not None and lp_order % 2 == 0
    if resp in ("Chebyshev", "Elliptic") and ft != "Bandpass" and even and not asym:
        opts.append({"pb_even": True})
    if resp in ("Inverse Chebyshev", "Elliptic"):
        if ft == "Band-Reject":
            total = 2 * order if not asym else lp + hp
            if total % 4 == 0:
                opts.append({"coincident": True})
        elif ft == "Bandpass" and asym:
            opts += [{"sb_rolloff_l": True}, {"sb_rolloff_u": True}]
        else:
            opts.append({"sb_rolloff": True})
    if resp == "Elliptic" and len(opts) > 2:
        opts.append({k: True for o in opts[1:] for k in o})
    return opts


def _structural():
    """[(response, ftype, struct-dict)] -- every structural case (no frequencies yet)."""
    out = []
    for resp in ("Butterworth", "Chebyshev", "Inverse Chebyshev", "Elliptic"):
        for ft in TYPES:
            for order, lp, hp, asym in _orders(resp, ft):
                for mods in _mods(resp, ft, order, lp, hp, asym):
                    out.append((resp, ft, dict(order=order, lp=lp, hp=hp, asym=asym, mods=mods)))
    for resp in DELAY_RESPONSES:
        for n in range(1, 9):
            out.append((resp, "Lowpass", dict(order=n, delay=dict(mode="manual"))))
        for anchor in ("corner", "delay"):
            for crit in ("first", "flat", "stopband"):
                out.append((resp, "Lowpass", dict(delay=dict(mode="specs", anchor=anchor, crit=crit))))
        for n in range(1, 6):
            for bpmap in ("delay", "classic"):
                out.append((resp, "Bandpass", dict(order=n, delay=dict(mode="manual", bp_map=bpmap))))
        out.append((resp, "Bandpass", dict(delay=dict(mode="specs", bp_map="classic"))))
    for cs in custom_cases():
        out.append(("Custom H(s)", cs["ftype"], dict(custom=cs)))
    return out


# =============================================================================
# Custom H(s) cases (spec dicts in custom_tf's format, built lazily in the worker)
# =============================================================================
def custom_cases():
    """Entry forms x modes x types. 'proto' = a scipy prototype converted by
    tasks_ui.custom_spec() into the named form (normalized to w = 1)."""
    return [
        dict(name="f0q-BW4-LP", mode="complete", form="f0q", proto=("butter", 4, "low"), ftype="Lowpass"),
        dict(name="roots-CH5-HP", mode="complete", form="roots", proto=("cheby1", 5, "high"), ftype="Highpass"),
        dict(name="coeff-BW3-BP", mode="complete", form="coeff", proto=("butter", 3, "bandpass"), ftype="Bandpass"),
        dict(name="roots-EL4-BR", mode="complete", form="roots", proto=("ellip", 2, "bandstop"), ftype="Band-Reject"),
        dict(name="ts-BS4-LP", mode="complete", form="ts", proto=("bessel", 4, "low"), ftype="Lowpass"),
        dict(name="roots-EL5-LP-abs", mode="complete", form="roots", proto=("ellip", 5, "low"),
             ftype="Lowpass", scale="absolute"),
        dict(name="proto-LP", mode="prototype", form="f0q", proto=("butter", 4, "low"), ftype="Lowpass"),
        dict(name="proto-HP", mode="prototype", form="f0q", proto=("cheby1", 4, "low"), ftype="Highpass"),
        dict(name="proto-BP-corners", mode="prototype", form="f0q", proto=("butter", 3, "low"),
             ftype="Bandpass", pbdef="corners"),
        dict(name="proto-BR-width", mode="prototype", form="roots", proto=("ellip", 4, "low"),
             ftype="Band-Reject", pbdef="width"),
        dict(name="proto-LP-normalize", mode="prototype", form="f0q", proto=("butter", 5, "low"),
             ftype="Lowpass", gain_mode="normalize"),
    ]


# =============================================================================
# designs
# =============================================================================
def _design_id(resp, ft, st, params):
    parts = [SHORT[resp], SHORT[ft]]
    if st.get("custom"):
        parts.append(st["custom"]["name"])
    elif st.get("asym"):
        parts.append(f"a{st['lp']}{st['hp']}")
    elif st.get("order"):
        parts.append(f"n{st['order']}")
    d = st.get("delay") or {}
    if d.get("mode") == "specs":
        parts.append(f"spec-{d.get('anchor', 'bp')}-{d.get('crit', 'sb')}")
    if d.get("bp_map"):
        parts.append(d["bp_map"])
    if params.get("ripple"):
        parts.append(f"d{params['ripple']:g}")
    for k in sorted(st.get("mods") or {}):
        parts.append(k)
    if "fc" in params:
        parts.append(f"{params['fc']:g}{params['unit']}")
    elif "f1" in params:
        parts.append(f"{params['f1']:g}-{params['f2']:g}{params['unit']}")
    parts.append(f"g{params['gain']:g}")
    if params.get("alpha") is not None:
        parts.append(f"a{params['alpha']:g}")
    if params.get("as") is not None:
        parts.append(f"s{params['as']:g}")
    if params.get("as_l") is not None:
        parts.append(f"s{params['as_l']:g}-{params['as_u']:g}")
    if params.get("absorb"):
        parts.append("abs")
    if params.get("gain_dist", "even") != "even":
        parts.append(params["gain_dist"])
    return "_".join(parts).replace(" ", "")


def _draw_params(rng, resp, ft, st):
    p = {}
    if ft in ("Lowpass", "Highpass"):
        p["unit"], p["fc"] = rng.choice(LP_FREQS)
    else:
        p["unit"], p["f1"], p["f2"] = rng.choice(BANDS)
    p["gain"] = rng.choice(GAINS)
    if resp == "Chebyshev":
        p["alpha"] = rng.choice(CHEB_ALPHA)
    elif resp == "Elliptic":
        p["alpha"] = rng.choice(ELL_ALPHA)
    if resp in ("Inverse Chebyshev", "Elliptic"):
        if ft in ("Bandpass", "Band-Reject") and st.get("asym"):
            p["as_l"], p["as_u"] = rng.choice(STOP_AS), rng.choice(STOP_AS)
        else:
            p["as"] = rng.choice(STOP_AS if resp == "Inverse Chebyshev" else STOP_AS[1:])
    d = st.get("delay") or {}
    if resp == "Equiripple Delay":
        p["ripple"] = rng.choice(DELAY_RIPPLE)
    if d.get("mode") == "specs":
        if d.get("crit") == "stopband" or ft == "Bandpass":
            p["as"] = rng.choice((30.0, 40.0))
    p["absorb"] = rng.random() < 0.3
    gd = GAIN_DIST + (("equalize",) if ft == "Band-Reject" else ())
    p["gain_dist"] = rng.choice(gd) if rng.random() < 0.35 else "even"
    if st.get("custom"):
        p.pop("alpha", None)
        p["gain"] = 1.0 if st["custom"].get("gain_mode") != "normalize" else rng.choice(GAINS)
    return p


def _rank(*parts):
    """Stable pseudo-random rank (Python's hash() is salted per process)."""
    return int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def designs(level, seed=1):
    """Every design of the level, each {id, response, ftype, ...struct, ...params,
    solve, e2e}. Stratified: every (response, type) cell is represented.

    Each design is drawn from its own generator seeded by (seed, structural case,
    draw number), and the solve / e2e choices rank by a hash of the id. So a
    smaller level is a subset of a larger one and adding a case to the matrix
    does not reshuffle the others: run directories stay comparable by id."""
    L = LEVELS[level]
    by_cell = {}
    for resp, ft, st in _structural():
        by_cell.setdefault((resp, ft), []).append(st)
    out, seen = [], set()
    for (resp, ft), sts in by_cell.items():
        keyed = [(json.dumps(st, sort_keys=True, default=str), st) for st in sts]
        if L["per_cell"] is not None:
            keyed = sorted(keyed, key=lambda k: _rank(seed, resp, ft, k[0]))[:L["per_cell"]]
        draws = 1 if L["per_cell"] is not None else L["draws"]
        for skey, st in keyed:
            for i in range(draws):
                rng = random.Random(f"{seed}|{resp}|{ft}|{skey}|{i}")
                p = _draw_params(rng, resp, ft, st)
                if L["per_cell"] is not None:          # smoke: plain, predictable
                    p.update(absorb=False, gain_dist="even")
                    if ft in ("Lowpass", "Highpass"):
                        p["unit"], p["fc"] = "kHz", 1.0
                    else:
                        p["unit"], p["f1"], p["f2"] = "kHz", 1.0, 2.0
                did = _design_id(resp, ft, st, p)
                if did in seen:
                    continue
                seen.add(did)
                out.append(dict(id=did, response=resp, ftype=ft, **st, **p))
    for resp, ft, st, p, why in PINNED:
        did = _design_id(resp, ft, st, p)
        if did not in seen:
            seen.add(did)
            out.append(dict(id=did, response=resp, ftype=ft, **st, **p, pinned=why))
    out.sort(key=lambda d: _rank(seed, d["id"]))
    # solve selection: stratified over (response, type) so no cell goes unsolved
    by_cell = {}
    for d in out:
        by_cell.setdefault((d["response"], d["ftype"]), []).append(d)
    for ds in by_cell.values():
        k = max(1, round(len(ds) * L["solve_frac"]))
        for d in ds[:k]:
            d["solve"] = True
    for d in out:
        if d.get("pinned"):
            d["solve"] = True
    # e2e: one per (response, type) first, smallest designs first (cheap, reliable)
    cand = sorted([d for d in out if d.get("solve")],
                  key=lambda d: (d.get("order") or (d.get("lp") or 2) + (d.get("hp") or 2),
                                 _rank(seed, "e2e", d["id"])))
    picked, used = [], set()
    for d in cand:
        key = (d["response"], d["ftype"])
        if key not in used:
            used.add(key)
            picked.append(d)
    rest = [d for d in cand if d not in picked]
    for d in (picked + rest)[:L["e2e"]]:
        d["e2e"] = True
    return out


# =============================================================================
# variants
# =============================================================================
def _combos(seed):
    c = list(itertools.product(OPAMPS, ENVELOPES, GAINVARS, FAMOPTS))
    random.Random(seed).shuffle(c)
    return c


def variants(level, design_id, seed=1):
    """[(variant_id, variant)] for a solved design: every family, the other
    dimensions rotated through all their combinations (keyed by the design id,
    so a design gets the same variants at every level)."""
    L = LEVELS[level]
    design_index = _rank(seed, "variants", design_id) % 997
    out = []
    if not L["rotate"]:
        for fam in FAMILIES:
            v = dict(family=fam, opamp="ideal", env="default", gainvar="design", famopt="none",
                     preset=PRESET)
            out.append((f"{fam}-ideal-default", v))
        return out
    combos = _combos(seed)
    for r in range(L["rounds"]):
        for j, fam in enumerate(FAMILIES):
            op, env, gv, fo = combos[(design_index * 3 * L["rounds"] + r * 3 + j) % len(combos)]
            pr = L["presets"][(design_index + r + j) % len(L["presets"])] if L.get("presets") else PRESET
            v = dict(family=fam, opamp=op, env=env, gainvar=gv, famopt=fo, preset=pr)
            vid = (f"{fam}-{op}-{env}" + ("-atten" if gv == "atten" else "") + ("-alt" if fo == "alt" else "")
                   + ("" if pr == PRESET else f"-{pr}"))
            if all(vid != o[0] for o in out):
                out.append((vid, v))
    return out


def e2e_variant(design_id):
    """The one variant an end-to-end design runs: realistic part, default envelope."""
    fam = FAMILIES[_rank("e2e-family", design_id) % 3]
    return (f"{fam}-typical-default",
            dict(family=fam, opamp="typical", env="default", gainvar="design", famopt="none",
                 preset=PRESET))
