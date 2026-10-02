# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-033 checks: near-notch sections (f_z close to f0) are LPn/HPn, not notch.

    python dev/fs033/check_near_notch.py            # 1 rule + 2 classification table
    python dev/fs033/check_near_notch.py --solve    # + 3 solve reclassified sections

1. The Q-aware rule: pairing_utils.notch_forcing_error matches the dense-grid
   max |H_true - H_forced| / K, exact notches stay 'notch', the maintainer's
   case (f0 2032 Hz, Q 9.83, fz 2108 Hz) is 'LPn'.
2. Old (|wz/w0 - 1| < 5 %) vs new classification of every section of a set of
   engine designs, paired exactly as the Pairing tab pairs them.
   Every reclassified section is a near-notch (pairing_utils.near_notch_section),
   which topology_tab solves on BOTH cell sets and ranks by snap cost.
3. Every reclassified 2nd-order section solved (ideal op-amp) in VCVS / MFB / AM
   on the 2N cells (old routing) and the LPn / HPn cells: snap cost and the
   realized-vs-target error max ||H_real| - |H_target|| / G (G = the section
   passband gain), via the FS-028 harness (= topology_tab routing), and the row
   the merged BOM table ranks first (lowest snap cost).
"""
import io
import os
import sys
import contextlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "dev", "fs028"))

import pairing_utils as pu                                   # noqa: E402

TWO_PI = 2 * np.pi


def ok(msg):
    print(f"  ok  {msg}")


def section(title):
    print(f"\n=== {title} ===")


def old_family(order, pole_type, n_origin, wz, w0):
    """The pre-FS-033 rule (|wz/w0 - 1| < 0.05 -> notch)."""
    if wz is not None:
        if w0 and abs(wz / w0 - 1.0) < 0.05:
            return "notch"
        return "LPn" if wz > w0 else "HPn"
    return pu._family_from_features(order, pole_type, n_origin, wz, w0, 0.0)


# ---------------------------------------------------------------------
section("1. Rule: eps bound, exact notches, maintainer case")
w = None
for Q in (0.5, 0.707, 1.0, 5.0, 10.0, 30.0):
    for ratio in (0.97, 0.995, 1.001, 1.01, 1.037):
        w0, wz = 1.0, ratio
        w = np.concatenate([np.linspace(0.0, 3.0, 400001), np.logspace(0.5, 3, 2000)])
        s = 1j * w
        D = s * s + s * w0 / Q + w0 * w0
        grid = np.max(np.abs((wz * wz - w0 * w0) / D))
        eps = pu.notch_forcing_error(wz, w0, Q)
        assert abs(eps / grid - 1.0) < 0.01, (Q, ratio, eps, grid)
ok("notch_forcing_error = dense-grid max |H_true - H_forced|/K within 1 % "
   "(Q 0.5 ... 30, wz/w0 0.97 ... 1.037)")
for Q in (0.5, 2.0, 50.0, 200.0):
    for d in (1e-9, -1e-9, 1e-7):
        fam = pu._family_from_features(2, "Complex Pair", 0, 1.0 + d, 1.0, Q)
        assert fam == "notch", (Q, d, fam)
ok("exact notches (wz/w0 = 1 ± 1e-9, 1 + 1e-7; Q up to 200) stay 'notch'")
f0, Q, fz = 2032.0, 9.83, 2108.0
eps = pu.notch_forcing_error(TWO_PI * fz, TWO_PI * f0, Q)
fam = pu.family_from_section({"order": 2, "is_complex_pair": True, "notch": True,
                              "n_origin_zeros": 0, "f0_hz": f0, "Q": Q, "fz_hz": fz})
assert fam == "LPn" and 0.6 < eps < 0.9, (fam, eps)
ok(f"maintainer case (f0 2032 Hz, Q 9.83, fz 2108 Hz): eps = {eps:.3f} -> {fam}")
fam = pu.family_from_section({"order": 2, "is_complex_pair": True, "notch": True,
                              "n_origin_zeros": 0, "f0_hz": f0, "Q": Q, "fz_hz": f0 / 1.037})
assert fam == "HPn", fam
ok("mirror (fz = f0/1.037) -> HPn")


# ---------------------------------------------------------------------
section("2. Classification, old (5 %) vs new (eps < 1e-3)")
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    import filter_engine as fe                               # imports streamlit
    import custom_tf as ct


def sections_of(er, filter_type, absorb=False):
    """Section rows exactly as app.py builds hw_sections (raw rad/s bricks)."""
    pb, zb = pu.build_stage_bricks(er["poles"], er["zeros"], "Denormalized", 1.0)
    stages = pu.auto_pair_stages(pb, zb, absorb_1st_order=absorb, filter_type=filter_type)
    rows = []
    for stg in stages:
        c = pu.classify_section(stg, pb, zb)
        old = old_family(c["order"], "Real" if c["order"] == 1 else "Complex Pair",
                         c["n_origin_zeros"], c["wz"], c["w0"])
        rows.append(dict(c, stage=stg["stage_num"], old=old, Q=stg["q"]))
    return rows


def designs():
    f1, f2 = 1000.0, 4000.0
    out = []
    with contextlib.redirect_stdout(io.StringIO()):
        for n in (13, 9, 7, 5):
            out.append((f"Elliptic BP n={n} {f1:.0f}-{f2:.0f} Hz", "Bandpass",
                        fe.synthesize_bandpass("Elliptic", n, n, f1, f2, 0.5, 60.0, 60.0)))
        out.append(("Elliptic BP n=13 1.8-2.3 kHz", "Bandpass",
                    fe.synthesize_bandpass("Elliptic", 13, 13, 1800.0, 2300.0, 0.5, 60.0, 60.0)))
        for n in (7, 5, 3):
            out.append((f"Elliptic BR n={n}", "Band-Reject",
                        fe.synthesize_bandreject("Elliptic", n, n, f1, f2, 0.5, 60.0)))
        for resp in ("Butterworth", "Chebyshev"):
            for n in (5, 4, 3):
                out.append((f"{resp} BR n={n}", "Band-Reject",
                            fe.synthesize_bandreject(resp, n, n, f1, f2, 0.5, 40.0)))
            out.append((f"{resp} BR n=5 narrow", "Band-Reject",
                        fe.synthesize_bandreject(resp, 5, 5, 1900.0, 2100.0, 0.5, 40.0)))
        # engine-imprecise centers: the nominal notch pair comes out 1e-4 / 7e-6 off
        out.append(("Butterworth BR n=5 1980-2020 Hz", "Band-Reject",
                    fe.synthesize_bandreject("Butterworth", 5, 5, 1980.0, 2020.0, 0.5, 40.0)))
        out.append(("Inverse Chebyshev BR n=5 1995-2005 Hz", "Band-Reject",
                    fe.synthesize_bandreject("Inverse Chebyshev", 5, 5, 1995.0, 2005.0, 0.5, 40.0)))
        for n in (11, 9):
            out.append((f"Elliptic LP n={n}", "Lowpass",
                        fe.synthesize_lowpass("Elliptic", n, 1000.0, 0.5, 80.0)))
            out.append((f"Elliptic HP n={n}", "Highpass",
                        fe.synthesize_highpass("Elliptic", n, 1000.0, 0.5, 80.0)))
    # Custom H(s): a rounded notch -- (s^2 + 1.0404) / (s^2 + s/8 + 1) at 1 kHz,
    # i.e. the zero typed 2 % off a Q = 8 pole (old rule: notch).
    sp = ct.default_spec()
    sp.update(form="coeff", scale="normalized", f_norm=1.0, unit="kHz")
    sp["coeff"] = {"num": ["1", "0", "1.0404"], "den": ["1", "0.125", "1"], "order": "desc"}
    r = ct.design_custom(sp, "Band-Reject", ct.MODE_COMPLETE)
    assert not r["errors"], r["errors"]
    out.append(("Custom H(s) zero 2 % off a Q 8 pole", "Band-Reject", r["engine_results"]))
    return out


changed = []
for name, ft, er in designs():
    rows = sections_of(er, ft)
    n_ch = sum(r["old"] != r["family"] for r in rows)
    print(f"\n  {name}: {len(rows)} sections, {n_ch} reclassified")
    for r in rows:
        if r["wz"] is None:
            continue
        eps = pu.notch_forcing_error(r["wz"], r["w0"], r["Q"])
        flag = "  <-- changed" if r["old"] != r["family"] else ""
        print(f"    S{r['stage']:<2} o{r['order']} f0 {r['w0'] / TWO_PI:9.2f} Hz  Q {r['Q']:7.3f}  "
              f"fz/f0 {r['wz'] / r['w0']:.6f}  eps {eps:9.2e}  {r['old']:>5} -> {r['family']}{flag}")
        if r["old"] != r["family"]:
            changed.append((name, r))
        if r["order"] == 2:
            nn = pu.near_notch_section({"order": 2, "notch": True, "f0_hz": r["w0"] / TWO_PI,
                                        "fz_hz": r["wz"] / TWO_PI, "Q": r["Q"]})
            assert nn == (r["old"] != r["family"]), (name, r)
        if r["old"] == "notch" and eps < 1e-6:
            assert r["family"] == "notch", (name, r)
assert all(r["old"] == "notch" for _n, r in changed), "only old notches may move"
print()
ok(f"{len(changed)} sections reclassified notch -> LPn/HPn; every exact notch "
   "(eps < 1e-6) stays 'notch'; reclassified 2nd-order = near-notch (dual solve)")


# ---------------------------------------------------------------------
if "--solve" not in sys.argv:
    print("\n(section 3 skipped: pass --solve)")
    sys.exit(0)

section("3. Reclassified sections: old 2N vs new LPn/HPn cells (ideal op-amp)")
import fs028_common as C                                     # noqa: E402
from tf_derivation_v2 import make_response_func, cell_components  # noqa: E402


def target_mag(sec, f):
    s = 1j * TWO_PI * f
    w0, wz = TWO_PI * sec["f0_hz"], TWO_PI * sec["fz_hz"]
    return np.abs(sec["K_radps"] * (s * s + wz * wz) / (s * s + s * w0 / sec["Q"] + w0 * w0))


def realized_err(res, sec, row):
    """max ||H_real| - |H_target|| / G over f0/10 ... 10 f0, G = passband gain."""
    cases = res["cases"]
    case = cases[(row["topology"], "ideal")]
    names = cell_components(case)
    comp = {k: row[k] for k in names["caps"] + names["resistors"]
            if row.get(k) not in (None, "OPEN", "OPEN  ")}
    Hf, _ = make_response_func(case)
    f = np.logspace(np.log10(sec["f0_hz"] / 10), np.log10(sec["f0_hz"] * 10), 4001)
    hr = np.abs(Hf(comp, TWO_PI * f))
    ht = target_mag(sec, f)
    G = max(ht[0], ht[-1])
    return float(np.max(np.abs(hr - ht)) / G)


def solve(sec, family):
    r = C.route(sec, family)
    if r is None:
        return None
    topos, dct = r
    with contextlib.redirect_stdout(io.StringIO()):
        res, _info = C.run_section(sec, family, topos, dct, preset="Balanced",
                                   instrument=False, n_cores=os.cpu_count())
    sn = res.get("snapped") or []
    if not sn:
        return {"topos": topos, "n": 0}
    best = min(sn, key=lambda x: x.get("snap_cost", np.inf))
    return {"topos": topos, "n": len(sn), "topo": best["topology"],
            "cost": best["snap_cost"], "err": realized_err(res, sec, best)}


seen = set()
with C.workdir():
    for name, r in changed:
        if r["order"] != 2:
            continue
        key = (round(r["w0"], 3), round(r["Q"], 4), round(r["wz"], 3))
        if key in seen:
            continue
        seen.add(key)
        f0, fz, Q = r["w0"] / TWO_PI, r["wz"] / TWO_PI, r["Q"]
        print(f"\n  {name} S{r['stage']}: f0 {f0:.1f} Hz Q {Q:.3f} fz/f0 {fz / f0:.4f} "
              f"-> {r['family']}")
        for fam_hw in ("VCVS", "MFB", "AM"):
            line, best = [], None
            for lbl, fam in (("2N", "notch"), (r["family"], r["family"])):
                sec = C._sec(f"{lbl}", 2, fam, f0, Q, fz=fz)
                o = solve(sec, fam_hw)
                if o is None:
                    line.append(f"{lbl}: gated")
                elif not o["n"]:
                    line.append(f"{lbl} {'/'.join(o['topos'])}: NO BOM")
                else:
                    line.append(f"{o['topo']}: cost {o['cost']:.3g} err {o['err']:.3g}")
                    if best is None or o["cost"] < best["cost"]:
                        best = o
            pick = f"-> {best['topo']}" if best else "-> no BOM"
            print(f"    {fam_hw:4}  " + "  |  ".join(line) + f"   {pick}")
