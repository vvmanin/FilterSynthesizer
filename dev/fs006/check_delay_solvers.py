# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-006 numeric checks for the production module `delay_solvers.py` (and, at the end, the
engine entry points that use it).

Run from anywhere:  python dev/fs006/check_delay_solvers.py
Every check asserts; a clean run ends with "ALL CHECKS PASSED". The expected numbers come from
dev/FS-006_bessel_eqdelay_design_note.md (section numbers quoted below) and from the verified
reference implementation fs006_reference.py next to this file.
"""
import math
import os
import sys
import time

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import delay_solvers as ds          # noqa: E402  (production)
import fs006_reference as ref       # noqa: E402  (verified reference)

A3 = 10 * math.log10(2.0)           # 3.0103 dB
B, EQ = ds.BESSEL, ds.EQDELAY


def ok(msg):
    print(f"  ok  {msg}")


def section(title):
    print(f"\n{title}")


# -------------------------------------------------------------------------------------------
section("1. Bessel poles (note §2.7) and delay identity (§2.2)")
for n, table in ref.BESSEL_MAG_REF.items():
    p = ds.prototype_corner(B, n, A3)
    err = max(min(abs(q - r) / abs(r) for q in p) for r in table)
    assert err < 1e-7, (n, err)
    # production (besselap) vs reference (np.roots on the exact integer coefficients; np.roots
    # itself is off by ~3e-12..1e-11 at n = 10 depending on the LAPACK build)
    pr = ref.bessel_poles_delay(n)
    pd = ds.bessel_poles_delay(n)
    e2 = max(min(abs(q - r) / abs(r) for q in pd) for r in pr)
    assert e2 < 2e-11, (n, e2)
ok("n = 1..10 match the 8-digit table (< 1e-7) and np.roots on exact coefficients (< 2e-11)")
for n in range(1, 21):
    pd = ds.bessel_poles_delay(n)
    w = np.linspace(0, 3, 301)
    d = np.max(np.abs(ds.group_delay(pd, w) - (1 - ds.bessel_delay_error(n, w))))
    assert d < 1e-9, (n, d)
    assert abs(ds.group_delay(pd, [0.0])[0] - 1.0) < 1e-12
    pc = ds.prototype_corner(B, n, A3)
    assert abs(ds._db(pc, [1.0])[0] + A3) < 1e-9
ok("n = 1..20: tau = 1 - eps_n(x) to 1e-9, tau(0) = 1, -3.0103 dB at w = 1")

section("2. W3 table (note §2.4) and W_alpha at other alpha")
W3 = {1: 1.0, 2: 1.3616541287, 3: 1.7556723687, 4: 2.1139176749, 5: 2.4274107022,
      6: 2.7033950612, 7: 2.9517221470, 8: 3.1796172375, 9: 3.3916931389, 10: 3.5909805946}
for n, v in W3.items():
    assert abs(ds.corner_product(B, n, A3) - v) < 6e-11, (n, ds.corner_product(B, n, A3), v)
ok("W3(n), n = 1..10, to 10 digits")
for a in (0.1, 1.0, 6.0, 12.0):
    for n in (1, 3, 8, 20):
        w_closed = ds.corner_product(B, n, a)
        w_direct = ds._first_crossing(ds.bessel_poles_delay(n), a)
        assert abs(w_closed / w_direct - 1) < 1e-9, (a, n, w_closed, w_direct)
    for n in (2, 5, 12):
        pe, _ = ds.eqdelay_poles(n, 0.01)
        assert abs(ds._db(pe / ds.corner_product(EQ, n, a, 0.01), [1.0])[0] + a) < 1e-9
ok("W_alpha: closed form = direct crossing (alpha = 0.1/1/6/12 dB); equiripple corner exact")

section("3. Bessel flatness vs order (note §2.6)")
w3 = ds.corner_product(B, 4, A3)
f = lambda om: float(ds.bessel_delay_error(4, om * w3)) - 0.01
lo, hi = 0.5, 1.5
for _ in range(100):
    m = 0.5 * (lo + hi)
    lo, hi = (m, hi) if f(m) < 0 else (lo, m)
assert abs(lo - 0.915) < 5e-4, lo
ok(f"n = 4: 1 % delay error at f/fc = {lo:.4f} (table 0.915)")
for om in (0.3, 0.8, 1.2, 2.0):     # fixed Omega_d (-3 dB mode): raising n never hurts
    errs = [float(ds.bessel_delay_error(n, om * ds.corner_product(B, n, A3))) for n in range(1, 21)]
    assert all(b <= a * (1 + 1e-12) for a, b in zip(errs, errs[1:])), (om, errs)
ok("error at fixed Omega_d is non-increasing in n (n = 1..20)")

section("4. Equiripple delay (note §3): ripple, alternation, tau(0), band edge")
for n in range(2, 13):
    for delta in (0.001, 0.01, 0.05):
        p, wp = ds.eqdelay_poles(n, delta)
        assert np.all(p.real < 0)
        w = np.linspace(0, wp, 20001)
        E = ds.group_delay(p, w) - 1
        assert abs(np.max(np.abs(E)) / delta - 1) < 1e-6, (n, delta)
        s = np.sign(np.diff(E))
        idx = [0] + [i for i in range(1, len(s)) if s[i] != s[i - 1] and s[i] != 0] + [len(E) - 1]
        ext = E[idx]
        assert len(ext) == n + 1, (n, delta, len(ext))
        assert np.all(np.abs(np.abs(ext) / delta - 1) < 1e-5), (n, delta, ext / delta)
        assert np.all(np.sign(ext[1:]) == -np.sign(ext[:-1])), (n, delta)
        assert abs(E[0] - (delta if n % 2 else -delta)) < 1e-9 * max(1, delta), (n, delta)
        pr, wpr = ref.eqdelay_poles(n, delta)
        e2 = max(min(abs(q - r) / abs(r) for q in p) for r in pr)
        assert e2 < 1e-9 and abs(wp / wpr - 1) < 1e-9, (n, delta, e2)
p, wp = ds.eqdelay_poles(4, 0.01)
assert abs(wp - 3.2675704984686833) < 1e-9
w3r = ref.w3db(ref.eqdelay_poles(6, 0.01)[0])
assert abs(ds.corner_product(EQ, 6, A3, 0.01) - w3r) < 1e-9
ok("n = 2..12 x delta = 0.1/1/5 %: max|tau-1| = delta (1e-6), n+1 alternating extrema, "
   "tau(0) = 1 +/- delta, same poles as the reference; wp(4, 1 %) = 3.267570")

section("5. Order selection (note §6.4 item 6 + anchor semantics)")
sel = ds.select_delay_order


def pick(resp, crit, n_max, anchor="corner", fc=1e3, tau0=None, delta=0.01, kind="LP", **kw):
    return sel(resp, kind, A3, delta, anchor, fc, tau0, crit, n_max, **kw)


r = pick(B, {"fd_hz": 900.0, "eps": 0.01}, 20)
assert r["feasible"] and r["n"] == 4, r
r = pick(B, {"fs_hz": 5e3, "as_db": 40.0}, 20)
assert r["feasible"] and r["n"] == 4, r
r = pick(B, {"fs_hz": 3e3, "as_db": 40.0}, 20)
assert not r["feasible"] and r["n"] == 10 and abs(r["rows"][0][1] - 34.145) < 0.01, r
r = pick(EQ, {"fd_hz": 1.4e3}, 15)
assert r["feasible"] and r["n"] == 4, r
r = pick(EQ, {"fs_hz": 3e3, "as_db": 40.0}, 15)
assert not r["feasible"] and r["n"] == 8 and abs(r["rows"][0][1] - 36.386) < 0.01, r
ok("Bessel 1 % to 900 Hz -> 4; 40 dB @ 5k -> 4; 40 dB @ 3k -> infeasible, 34.1 dB @ n=10; "
   "EqDelay +/-1 % to 1.4k -> 4; 40 dB @ 3k -> infeasible, 36.4 dB @ n=8")
r = pick(B, {"f_min_hz": 500.0}, 20, anchor="delay", fc=None, tau0=1e-3)
assert r["feasible"] and r["n"] == 8 and r["fc_hz"] >= 500.0, r
r = pick(B, {"tau_max_s": 0.4e-3}, 20)
assert r["feasible"] and r["n"] == 5 and r["tau_nom_s"] <= 0.4e-3, r
r = pick(B, {"tau_max_s": 0.4e-3, "fs_hz": 5e3, "as_db": 40.0}, 20)
assert r["feasible"] and r["n"] == 4, r            # floor present -> smallest n
r = pick(B, {"tau_max_s": 0.2e-3, "fs_hz": 5e3, "as_db": 40.0}, 20)
assert not r["feasible"], r
# BP translation near the band behaves like the LP at Omega = 2 (f0 - fs) / B: 850 Hz -> 3
r = pick(B, {"fs_hz": 850.0, "as_db": 20.0}, 10, kind="BP", fc=None, f1_hz=950.0, f2_hz=1050.0,
         mapping=ds.BP_TRANSLATION)
assert r["feasible"] and r["n"] == 3, r
ok("delay anchor tau0 = 1 ms, fc >= 500 Hz -> 8; corner anchor tau0 <= 0.4 ms -> 5 (largest); "
   "ceiling + floor -> smallest; BP 20 dB @ 850 Hz (950-1050 Hz) -> 3")

section("6. Stopband notches keep the delay (note §5.1)")
z0, p0, _, _ = ds.design_delay_lp(B, 4, A3, 40.0)
zn, pn, _, nt = ds.design_delay_lp(B, 4, A3, 40.0, notches=[3.0], hold_corner=False)
assert np.allclose(pn, p0, rtol=0, atol=0) and len(zn) == 2


def phase_gd(p, z, w, h=1e-6):
    H = lambda x: np.prod(1j * x - z) / np.prod(1j * x - p) if len(z) else 1 / np.prod(1j * x - p)
    ph = lambda x: np.unwrap(np.angle([H(v) for v in x]))
    return -(ph(w + h) - ph(w - h)) / (2 * h)


w = np.linspace(0.05, 2.5, 60)
dgd = np.max(np.abs(phase_gd(pn, zn, w) - phase_gd(p0, z0, w)))
assert dgd < 1e-6, dgd
zh, ph, _, nh = ds.design_delay_lp(B, 4, A3, 40.0, notches=[3.0], hold_corner=True)
assert abs(ds._db(ph, [1.0], zh)[0] + A3) < 1e-9 and nh["pole_scale"] > 1
t_ratio = ds.group_delay(ph, [0.0])[0] / ds.group_delay(p0, [0.0])[0]
assert abs(t_ratio * nh["pole_scale"] - 1) < 1e-12
_, _, _, nr = ds.design_delay_lp(B, 4, A3, 40.0, notches=[0.8])
assert nr["warnings"], nr
ok(f"notch at 3 fc: delta tau (phase-derivative) = {dgd:.1e}; corner held with scale "
   f"{nh['pole_scale']:.4f} (tau0 / scale); passband notch rejected")

section("7. Bandpass mappings (note §4.2-4.3), n = 4, b = 0.1")
wp1, wp2 = 0.95, 1.05 / 1.0
g = math.sqrt(wp1 * wp2)
wp1, wp2 = wp1 / g, wp2 / g
Wd = 0.915                                    # LP 1 % band edge (f/fc), n = 4
for mapping, target, tol in ((ds.BP_TRANSLATION, 1.01, 0.02), (ds.BP_CLASSIC, 9.13, 0.1)):
    po, ze, k, _, _, nt = ds.synthesize_delay_bp(B, 4, wp1, wp2, A3, 0.01, mapping)
    e = ds._rel_db(po, ze, [wp1, wp2], nt["center_w"])
    assert np.all(np.abs(e + A3) < 1e-6), (mapping, e)
    Bw = wp2 - wp1
    if mapping == ds.BP_TRANSLATION:
        ww = np.linspace(nt["center_w"] - Bw / 2 * Wd, nt["center_w"] + Bw / 2 * Wd, 2001)
    else:
        Om = np.linspace(-Wd, Wd, 2001)
        ww = (Om * Bw + np.sqrt((Om * Bw) ** 2 + 4 * wp1 * wp2)) / 2
    t = ds.group_delay(po, ww, ze)
    pp = 100 * (t.max() - t.min()) / t.mean()
    assert abs(pp - target) < tol, (mapping, pp)
    ok(f"{mapping}: edges at -3.0103 dB, delay p-p over the mapped 1 % band = {pp:.3f} %, "
       f"{nt['n_origin_zeros']} origin zeros")
assert ds.bp_fold_limit(B, 10, A3) > 0.8 and ds.bp_fold_limit(EQ, 10, A3, 0.01) > 0.6

section("8. Highpass mirror (note §4.1) -- why HP is not offered")
pc = ds.prototype_corner(B, 4, A3)
r_hp = ds.group_delay(1 / pc, [2.0])[0] / ds.group_delay(pc, [0.0])[0]
assert abs(r_hp - 0.25) < 0.003, r_hp
ok(f"tau_HP(2 fc) / tau_LP(0) = {r_hp:.4f} (-> (fc/f)^2 = 0.25)")

section("9. Engine entry points (end to end, physical units)")
import filter_engine as fe          # noqa: E402  (imports streamlit; no server needed)

er = fe.synthesize_lowpass(B, 4, 1000.0, A3, 40.0)
info = er["delay_info"]
secs = sorted((abs(p) / (2 * math.pi), abs(p) / (-2 * p.real)) for p in er["poles"] if p.imag > 0)
assert abs(secs[0][0] - 1430.17) < 0.05 and abs(secs[0][1] - 0.52193) < 5e-5, secs
assert abs(secs[1][0] - 1603.36) < 0.05 and abs(secs[1][1] - 0.80554) < 5e-5, secs
assert abs(info["tau_dc_s"] - 336.4e-6) < 0.05e-6, info
H = lambda f: er["k"] / abs(np.prod(2j * math.pi * f - er["poles"]))
assert abs(20 * math.log10(H(1000.0)) + A3) < 1e-6
gd900 = ds.group_delay(er["poles"], [2 * math.pi * 900.0])[0]
assert 1 - gd900 / info["tau_dc_s"] < 0.01
ok("Bessel LP n=4, 1 kHz: 1430.2 Hz/Q 0.5219 + 1603.4 Hz/Q 0.8055, -3.0103 dB @ 1 kHz, "
   f"tau0 = {info['tau_dc_s']*1e6:.1f} us, < 1 % sag at 900 Hz")
er = fe.synthesize_lowpass(EQ, 4, 1000.0, A3, 40.0, delay_ripple=0.01)
fp = er["delay_info"]["flat_band_hz"][1]
assert abs(fp - 1419.0) < 1.0, fp
ok(f"Equiripple +/-1 % LP n=4, 1 kHz: delay band edge {fp:.1f} Hz (table 1.419 fc)")
try:
    fe.synthesize_highpass(B, 4, 1000.0, A3, 40.0)
    raise AssertionError("highpass should be refused")
except ValueError:
    ok("Highpass refused for delay responses")
for mapping in (ds.BP_TRANSLATION, ds.BP_CLASSIC):
    er = fe.synthesize_bandpass(EQ, 4, 4, 950.0, 1050.0, A3, 40.0, 40.0,
                                delay_ripple=0.01, bp_mapping=mapping)
    d = er["delay_info"]
    for f in (950.0, 1050.0):
        mag = er["k"] * abs(np.prod(2j * math.pi * f - er["zeros"])) / abs(np.prod(2j * math.pi * f - er["poles"]))
        assert abs(20 * math.log10(mag) + A3) < 1e-6, (mapping, f, 20 * math.log10(mag))
    ok(f"Equiripple BP n=4, 950-1050 Hz, {mapping}: edges -3.0103 dB, "
       f"delay p-p over f1..f2 = {d['delay_pp_pct']:.2f} %")
try:
    fe.synthesize_bandreject(B, 4, 4, 900.0, 1100.0, A3, 40.0)
    raise AssertionError("band-reject should be refused")
except ValueError:
    ok("Band-Reject refused for delay responses")
er = fe.synthesize_lowpass("Butterworth", 4, 1000.0, A3, 40.0)
assert "delay_info" not in er
ok("Butterworth LP untouched (no delay_info)")

section("10. Timing (fresh caches)")
ds._eqdelay_cached.cache_clear()
ds._eqdelay_corner.cache_clear()
row = []
for n in (4, 8, 12, 15):
    t0 = time.perf_counter()
    for delta in (0.001, 0.01, 0.05):
        ds.eqdelay_poles(n, delta)
    row.append(f"n={n}: {(time.perf_counter() - t0) / 3 * 1e3:.0f} ms")
print("  equiripple solve (mean of 3 ripples): " + ", ".join(row))
ds._eqdelay_cached.cache_clear()
ds._eqdelay_corner.cache_clear()
t0 = time.perf_counter()
pick(EQ, {"fs_hz": 3e3, "as_db": 80.0}, 15, delta=0.001)
print(f"  worst-case equiripple order scan (infeasible, n <= 15, delta = 0.1 %): "
      f"{time.perf_counter() - t0:.2f} s")
t0 = time.perf_counter()
pick(B, {"fs_hz": 3e3, "as_db": 80.0}, 20)
print(f"  Bessel order scan (infeasible, n <= 20): {(time.perf_counter() - t0) * 1e3:.0f} ms")

print("\nALL CHECKS PASSED")
