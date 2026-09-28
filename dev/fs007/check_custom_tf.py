# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-007 numeric checks for the production module `custom_tf.py`.

Run from anywhere:  python dev/fs007/check_custom_tf.py
Every check asserts; a clean run ends with "ALL CHECKS PASSED". The expected numbers come from
dev/FS-007_custom_tf_design_note.md §11.1 (section numbers quoted below).
"""
import math
import os
import sys

import numpy as np
from scipy import signal

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

import custom_tf as ct              # noqa: E402

A3 = 10 * math.log10(2.0)           # 3.0103 dB
TWO_PI = 2 * math.pi


def ok(msg):
    print(f"  ok  {msg}")


def section(title):
    print(f"\n{title}")


def rel(a, b):
    return abs(a - b) / max(abs(b), 1e-300)


def sorted_roots(r):
    return np.array(sorted(np.asarray(r, complex), key=lambda x: (round(x.real, 9), x.imag)))


def max_rel_roots(a, b):
    a, b = sorted_roots(a), sorted_roots(b)
    assert len(a) == len(b), (len(a), len(b))
    scale = max(np.max(np.abs(b)), 1e-300) if len(b) else 1.0
    return float(np.max(np.abs(a - b)) / scale) if len(b) else 0.0


def spec(form, block, scale="normalized", f_norm=1.0, unit="kHz"):
    s = ct.default_spec()
    s.update(form=form, scale=scale, f_norm=f_norm, unit=unit)
    s[form] = block
    return s


def design(sp, ft="Lowpass", mode=ct.MODE_COMPLETE, **kw):
    return ct.design_custom(sp, ft, mode, **kw)


def errors_of(sp, ft="Lowpass", **kw):
    r = design(sp, ft, **kw)
    return " | ".join(r["errors"])


def coeff_spec(num_asc, den_asc, **kw):
    return spec("coeff", {"num": list(num_asc), "den": list(den_asc), "order": "desc"}, **kw)


def round_sig(x, d):
    return float(f"{x:.{d - 1}e}")


def db_resp(z, p, k, w):
    return 20 * np.log10(np.abs(signal.freqs_zpk(z, p, k, worN=w)[1]))


# ---------------------------------------------------------------------
section("1. Coefficient round trip, Butterworth n = 4 (§11.1-1)")
r = design(coeff_spec([1.0], [1, 2.61313, 3.41421, 2.61313, 1][::-1]))
assert not r["errors"], r["errors"]
p = r["engine_results"]["poles"] / (TWO_PI * 1e3)
qs = sorted(abs(x) / (-2 * x.real) for x in p if x.imag > 0)
assert abs(qs[0] - 0.541193) < 1e-6 and abs(qs[1] - 1.306574) < 1e-6, qs
zb, pb, kb = signal.buttap(4)
w = np.linspace(1e-3, 1.0, 400)
dev = np.max(np.abs(db_resp([], p, np.prod(-p).real, w) - db_resp(zb, pb, kb, w)))
assert dev <= 3e-5, dev
ok(f"Q = {qs[0]:.6f} / {qs[1]:.6f}; passband deviation {dev:.2e} dB")

q_ex = sorted(abs(x) / (-2 * x.real) for x in pb if x.imag > 0)
forms = {
    "f0q": spec("f0q", {"pole_pairs": [{"f0": 1.0, "Q": q} for q in q_ex], "real_poles": [],
                        "zero_pairs": [], "real_zeros": [], "n_origin": 0, "K": 1.0}),
    "ts": spec("ts", {"stages": [{"a": 1 / q, "b": 1.0} for q in q_ex], "A0": 1.0}),
    "roots": spec("roots", {"poles": [[x.real, x.imag] for x in pb if x.imag > 0], "zeros": [],
                            "K": 1.0}),
    "coeff": coeff_spec([1.0], np.poly(pb).real[::-1]),
}
ers = {f: design(s)["engine_results"] for f, s in forms.items()}
for f in ("ts", "roots", "coeff"):
    d = max_rel_roots(ers[f]["poles"], ers["f0q"]["poles"])
    assert d <= 1e-9, (f, d)
    assert rel(ers[f]["k"], ers["f0q"]["k"]) <= 1e-9, f
ok("(f0, Q), Tietze-Schenk, roots and full-precision coefficients give identical zpk (<= 1e-9)")

# ---------------------------------------------------------------------
section("2. Elliptic n = 6 numerator via the even-part method (§11.1-2)")
ze, pe, ke = signal.ellipap(6, 0.5, 60)
b = np.poly(ze).real[::-1]
g = ct._balance_factor(np.poly(pe).real[::-1])
zz, nm = ct.zeros_from_numerator(b, g, ct._u_in(b))
assert all(x.real == 0.0 for x in zz) and len(zz) == 6, zz
werr = max_rel_roots([complex(0, abs(x.imag)) for x in zz], [complex(0, abs(x.imag)) for x in ze])
assert werr <= 1e-14, werr
ok(f"Re z = 0 exactly, ω_z error {werr:.1e}, n0 = 0")

# ---------------------------------------------------------------------
section("3. Scale equivalence (§11.1-3)")
fn = 1.0                                         # kHz
wn = TWO_PI * 1e3
sp_n = spec("roots", {"poles": [[x.real, x.imag] for x in pe if x.imag >= 0],
                      "zeros": [[0.0, abs(x.imag)] for x in ze if x.imag > 0], "K": ke},
            f_norm=fn)
sp_a = spec("roots", {"poles": [[x.real * wn, x.imag * wn] for x in pe if x.imag >= 0],
                      "zeros": [[0.0, abs(x.imag) * wn] for x in ze if x.imag > 0], "K": ke},
            scale="absolute")
en, ea = design(sp_n)["engine_results"], design(sp_a)["engine_results"]
assert max_rel_roots(en["poles"], ea["poles"]) <= 1e-12
assert max_rel_roots(en["zeros"], ea["zeros"]) <= 1e-12
assert rel(en["k"], ea["k"]) <= 1e-12
ok("normalized f_n = 1 kHz == absolute rad/s (<= 1e-12)")
w_n = en["custom_info"]["w_n"]
assert rel(w_n, wn) <= 1e-15
den_back = np.poly(en["poles"] / w_n).real
assert np.max(np.abs(den_back - np.poly(pe).real)) <= 1e-12
ok("Tab-2 normalization by custom_info['w_n'] reproduces the entered denominator")

# ---------------------------------------------------------------------
section("4. Prototype transforms (§11.1-4)")
z5, p5, k5 = signal.ellipap(5, 0.5, 40)
H = lambda z, p, k, s: k * np.prod([s - x for x in z]) / np.prod([s - x for x in p])
f1, f2 = 800.0, 1250.0
w1, w2 = TWO_PI * f1, TWO_PI * f2
w0, B = math.sqrt(w1 * w2), w2 - w1
maps = {"Lowpass": lambda s: s / w1, "Highpass": lambda s: w1 / s,
        "Bandpass": lambda s: (s * s + w0 * w0) / (B * s),
        "Band-Reject": lambda s: B * s / (s * s + w0 * w0)}
pts = [complex(300, 700), complex(-50, 2e3), complex(1e3, -4e3), complex(10, 9e3)]
for ft, m in maps.items():
    zt, pt, kt = ct.lp_prototype_to(z5, p5, k5, ft, f1, f2)
    e = max(abs(H(zt, pt, kt, s) - H(z5, p5, k5, m(s))) / abs(H(z5, p5, k5, m(s))) for s in pts)
    assert e <= 1e-13, (ft, e)
    if ft in ("Highpass", "Band-Reject"):
        assert rel(kt, abs(H(z5, p5, k5, 0.0))) <= 1e-14, ft
    if ft == "Bandpass":
        assert rel(kt, k5 * B ** (len(p5) - len(z5))) <= 1e-14
    if ft in ("Bandpass", "Band-Reject"):
        jw = [x for x in zt if abs(x) > 0]
        assert all(x.real == 0 for x in jw), ft
ok("H_new(s) = H_proto(mapped s) <= 1e-13; k: HP/BR = H_proto(0), BP = K·B^(np-nz); jω zeros stay on the axis")
_, pw, _ = ct.lp_prototype_to([], [-1.0], 1.0, "Bandpass", 1.0, 1e4)
big = max(pw, key=abs)
small = min(pw, key=abs)
e = rel(small, (TWO_PI) ** 2 * 1e4 / big)
assert e <= 1e-12, e
ok(f"wideband BP (f2/f1 = 1e4) small root accurate to {e:.1e}")

# ---------------------------------------------------------------------
section("5. Prototype mode vs the engine's Chebyshev symmetric BP / BR (§11.1-5)")
import filter_engine as fe                                   # noqa: E402  (imports streamlit)
for ft in ("Bandpass", "Band-Reject"):
    n, rp = 3, 1.0
    if ft == "Bandpass":
        eng = fe.synthesize_bandpass("Chebyshev", n, n, f1, f2, rp, 40.0, 40.0)
    else:
        eng = fe.synthesize_bandreject("Chebyshev", n, n, f1, f2, rp, 40.0)
    zc, pc, kc = signal.cheb1ap(n, rp)
    sp_p = spec("roots", {"poles": [[x.real, x.imag] for x in pc if x.imag >= 0], "zeros": [],
                          "K": kc})
    rr = design(sp_p, ft, ct.MODE_PROTOTYPE, f1_hz=f1, f2_hz=f2)
    assert not rr["errors"], rr["errors"]
    er = rr["engine_results"]
    d = max_rel_roots(er["poles"], eng["poles"])
    assert d <= 1e-9, (ft, d)
    ww = np.logspace(np.log10(w1 / 5), np.log10(w2 * 5), 300)
    ha = db_resp(er["zeros"], er["poles"], er["k"], ww)
    hb = db_resp(eng["zeros"], eng["poles"], eng["k"], ww)
    hb -= np.max(hb)
    ha -= np.max(ha)
    fin = np.isfinite(ha) & np.isfinite(hb) & (hb > -150)
    assert np.max(np.abs(ha[fin] - hb[fin])) <= 1e-6, ft
    assert abs(rr["info"]["proto_edge_db"] - rp) <= 1e-9
    ok(f"{ft}: equal poles ({d:.1e}) and |H| after peak normalization; prototype edge α = {rp} dB")

# ---------------------------------------------------------------------
section("6. Repeated roots (§11.1-6)")
for m in range(1, 11):
    bm = np.poly(np.repeat([1j, -1j], m)).real[::-1]
    zz, _ = ct.zeros_from_numerator(bm, 1.0, ct._u_in(bm))
    assert len(zz) == 2 * m and all(x.real == 0 for x in zz), m
    assert max(abs(abs(x.imag) - 1.0) for x in zz) <= 1e-12, m
ok("(s² + 1)^m, m = 1…10, exact coefficients: m-fold ±j, ω_z error <= 1e-12")
w0z = TWO_PI * 1e3
for m in range(2, 11):
    bm = [round_sig(c, 6) for c in np.poly(np.repeat([1j * w0z, -1j * w0z], m)).real[::-1]]
    zz, nm = ct.zeros_from_numerator(bm, w0z, ct._u_in(bm))
    assert nm >= 1 and all(x.real == 0 for x in zz), m
    e = max(abs(abs(x.imag) - w0z) / w0z for x in zz)
    assert e <= 2e-6, (m, e)
ok("(s² + ω0²)^m, m = 2…10, 6-digit absolute coefficients: merged, ω_z error <= 2e-6")


def n_distinct(ws):
    ws = sorted(set(round(abs(x.imag), 9) for x in ws if x.imag > 0))
    return len(ws)


D6 = [6, 6, 6, 6, 6]                    # typed as 6 digits ("1.02010": repr would say 5)
b1 = [round_sig(c, 6) for c in np.poly([1j, -1j, 1.01j, -1.01j]).real[::-1]]
zz, _ = ct.zeros_from_numerator(b1, 1.0, ct._u_in(b1, D6))
assert n_distinct(zz) == 2
b2 = [round_sig(c, 6) for c in np.poly([1j, -1j, 1.001j, -1.001j]).real[::-1]]
zz, _ = ct.zeros_from_numerator(b2, 1.0, ct._u_in(b2, D6))
assert n_distinct(zz) == 1
ok("notches 1 % apart stay two; 0.1 % apart at 6 digits merge (documented)")
v, d = ct.parse_coeff_text("1 2.02010 1.02010", "desc")
assert d == [6, 6, 1] and ct._u_in(v, d) == 0.5e-5
ok("paste digits: '1.02010' counts 6 (the float repr would count 5)")
r = design(coeff_spec(["1", "0", "1"], ["1", "0.5", "1"]), "Band-Reject")
assert not r["errors"] and r["info"]["n_complex_pole_pairs"] == 1 and not r["info"]["n_merged_clusters"], r
ok("short exact values ('1 0.5 1'): the Q = 2 pair is not merged (precision floor 6 digits)")

# ---------------------------------------------------------------------
section("7. Conditioning diagnostic (§11.1-7)")


def bw_bp_coeff(n, bfrac, digits=6):
    zb_, pb_, kb_ = signal.buttap(n)
    zt, pt, kt = signal.lp2bp_zpk(zb_, pb_, kb_, wo=1.0, bw=bfrac)
    den = [round_sig(c, digits) for c in np.poly(pt).real[::-1]]
    num = [0.0] * n + [round_sig(kt, digits)]
    return coeff_spec(num, den)


# The note's §4.4 table rounds once; the diagnostic draws 8 perturbations within ±u_in. At 6
# digits 2×5 crosses into the RHP in 88 % of the draws (the single rounding landed stable by
# luck), so it is an error like 2×6; the warning case is 2×6 at 9 digits (0.54 dB in §4.4).
r69 = design(bw_bp_coeff(6, 0.1, digits=9), "Bandpass")
assert not r69["errors"] and any("conditioning" in w for w in r69["warnings"]), r69
ok(f"Butterworth BP 2×6, b = 0.1, 9 digits: warning ({r69['info']['conditioning_db']:.3g} dB)")
for n in (5, 6):
    rn = design(bw_bp_coeff(n, 0.1), "Bandpass")
    # 2×6 at 6 digits: the typed rounding alone already puts poles in the RHP (§4.4 "27 !")
    assert rn["errors"] and ("RHP" in rn["errors"][0] or "right half" in rn["errors"][0]), \
        (n, rn["errors"])
    ok(f"Butterworth BP 2×{n}, b = 0.1, 6 digits: error ({rn['errors'][0][:48]}…)")
zb8, pb8, kb8 = signal.buttap(8)
r8 = design(coeff_spec([1.0], [round_sig(c, 6) for c in np.poly(pb8).real[::-1]]))
assert not r8["errors"] and not any("conditioning" in w for w in r8["warnings"]), r8
ok(f"Butterworth LP 8, 6 digits: no warning ({r8['info']['conditioning_db']:.2e} dB)")

# ---------------------------------------------------------------------
section("8. Gate rows (§7.1)")
bw4 = [[x.real, x.imag] for x in pb if x.imag > 0]


def rs(poles, zeros=(), K=1.0):
    return spec("roots", {"poles": [list(x) for x in poles], "zeros": [list(x) for x in zeros],
                          "K": K})


cases = [
    ("RHP pole", rs([[0.2, 1.0]] + bw4), "Lowpass", "right half-plane"),
    ("jω pole", rs([[0.0, 1.0]] + bw4), "Lowpass", "jω axis"),
    ("origin pole", rs([[0.0, 0.0]] + bw4), "Lowpass", "origin"),
    ("lone complex root", rs([[-0.5, 1.0], [-0.5, -0.8]]), "Lowpass", "lone complex"),
    ("real LHP zero", rs(bw4, [[-2.0, 0.0]]), "Lowpass", "FS-014"),
    ("off-axis LHP pair", rs(bw4, [[-0.3, 2.0]]), "Lowpass", "FS-014"),
    ("RHP zero pair", rs(bw4, [[0.3, 2.0]]), "Lowpass", "FS-013"),
    ("real RHP zero", rs(bw4, [[2.0, 0.0]]), "Lowpass", "FS-013"),
    ("improper", rs([[-1.0, 0.0]], [[0.0, 2.0]]), "Lowpass", "Improper"),
    ("jω pairs > complex pole pairs", rs([[-1.0, 0.0], [-2.0, 0.0], [-0.5, 0.9]],
                                         [[0.0, 2.0], [0.0, 3.0]]), "Lowpass", "jω zero pairs"),
    ("BP with 2 real poles", rs([[-0.1, 1.0], [-0.1, 1.2], [-0.5, 0.0], [-2.0, 0.0]],
                                [[0.0, 0.0], [0.0, 0.0]]), "Bandpass", "real poles"),
    ("BR with 2 real poles", rs([[-0.1, 1.0], [-0.1, 1.2], [-0.5, 0.0], [-2.0, 0.0]],
                                [[0.0, 1.1], [0.0, 1.1]]), "Band-Reject", "real poles"),
    ("n_p = 31", rs([[-1.0, 0.0]] * 31), "Lowpass", "limit of 30"),
    ("no -α crossing", rs(bw4, K=1.0), "Highpass", "never falls"),
]
for name, sp, ft, needle in cases:
    e = errors_of(sp, ft)
    assert needle in e, (name, e)
    ok(f"{name}: '{needle}'")
e = errors_of(coeff_spec(["1", "0", "1"], ["1", "0.5", "1"]), "Bandpass", mode=ct.MODE_PROTOTYPE,
              f1_hz=800.0, f2_hz=1250.0)
assert "must be its passband edge" in e, e
ok("prototype with |H(j1)| = 0 (a notch at ω = 1): 'must be its passband edge'")

# ---------------------------------------------------------------------
section("9. Pairing pre-flight (§7.2)")
wz_lo, wz_hi = 0.9, 2.0
pp = [complex(-0.05, 0.8), complex(-0.05, -0.8), complex(-0.06, 1.25), complex(-0.06, -1.25), -0.5]
zp = [complex(0, wz_lo), complex(0, -wz_lo), complex(0, wz_hi), complex(0, -wz_hi), 0j]
msgs = ct.preflight_pairing(pp, zp, "Bandpass")
assert any("jω pair" in m and "origin" in m for m in msgs), msgs
ok("BP: jω pair + origin zero dumped into one stage is flagged")
assert not ct.preflight_pairing(*[np.asarray(x) for x in (pb, [])], "Lowpass")
ok("Butterworth LP 4: no pre-flight message")

# ---------------------------------------------------------------------
section("10. Type detection (§5.3)")
for ft, (zt, pt, kt) in {"Lowpass": (ze, pe, ke),
                         "Highpass": signal.lp2hp_zpk(ze, pe, ke, 1.0)}.items():
    lpk, _ = ct.peak_gain(zt, pt, kt)
    t, _, _ = ct.detect_type(zt, pt, kt, lpk, 0.5)
    assert t == ft, (ft, t)
    ok(f"Elliptic n = 6 (finite floor) {ft}: detected {t}")
A = 2.0
zs = np.roots([1, math.sqrt(2 * A), A])
ps = np.roots([1, math.sqrt(2), 1])
lpk, _ = ct.peak_gain(zs, ps, 1.0)
t, _, _ = ct.detect_type(zs, ps, 1.0, lpk, A3)
assert t == "other", t
ok("shelving biquad (6 dB): detected other")

# ---------------------------------------------------------------------
section("11. Complete mode end to end")
r = design(forms["f0q"], "Lowpass", alpha_db=A3)
info = r["info"]
assert abs(info["edges_hz"][0] - 1e3) / 1e3 <= 1e-9, info["edges_hz"]
assert abs(info["peak_gain"] - 1.0) <= 1e-12 and info["detected"] == "Lowpass"
assert abs(info["h_j1_db"] + A3) <= 1e-9 and abs(info["f3db_ratio"] - 1.0) <= 1e-9
ok("Butterworth LP 4 at f_n = 1 kHz: −3.0103 dB edge at 1 kHz, peak 1, |H(j1)| = −3.01 dB")
sp_g = spec("f0q", {**forms["f0q"]["f0q"], "K": -0.5})
r = design(sp_g, "Lowpass", gain_mode=ct.GAIN_AS_ENTERED)
assert abs(r["info"]["peak_gain"] - 0.5) <= 1e-12 and r["info"]["k_sign"] == -1
assert any("|K|" in w for w in r["warnings"]) and any("G = 0.5" in w for w in r["warnings"])
ok("As entered, K = −0.5: G = 0.5 with the |K| and G < 1 warnings")
lpk, _ = ct.peak_gain(r["engine_results"]["zeros"], r["engine_results"]["poles"],
                      r["engine_results"]["k"])
assert abs(lpk) <= 1e-12
ok("engine k is peak-normalized (peak = 0 dB) in both gain modes")
t1, _ = ct.parse_coeff_text("1, 2.61313 3.41421;2.61313\n1", "desc")
assert t1 == [1, 2.61313, 3.41421, 2.61313, 1]
rt = ct.parse_roots_text("-0.5000 + 0.8660i\n-0.5-0.866j\n-1 0\n(-0.2, 0.9)")
assert rt[0] == [-0.5, 0.866] and rt[1] == [-0.5, -0.866] and rt[2] == [-1.0, 0.0], rt
assert rt[3] == [-0.2, 0.9], rt
ok("paste parsers: coefficients (mixed separators) and roots (MATLAB, python, 're im')")

# ---------------------------------------------------------------------
section("12. Complete mode: type from detection; Normalize without K")
for ft, sp_t in (("Lowpass", forms["f0q"]),
                 ("Band-Reject", coeff_spec(["1", "0", "1"], ["1", "0.5", "1"]))):
    r = design(sp_t, None)
    assert not r["errors"] and r["info"]["target"] == ft == r["info"]["detected"], r
    ok(f"filter_type None -> detected and used: {ft}")
r = design(rs([[-1.0, 0.0], [-3.0, 0.0]], [[-2.0, 0.0]]), None)   # zeros gated before detection
assert any("FS-014" in m for m in r["errors"])
r = design(spec("roots", {"poles": [[-0.3, 1.0]], "zeros": [[0.0, 1.3]], "K": 1.0}), None,
           alpha_db=A3)
assert r["errors"] and any("not recognised" in m for m in r["errors"]), r["errors"]
ok("an LPn-shaped biquad (|H(∞)| −4.6 dB: neither pass nor stop) -> 'not recognised' error")
for blank in ("", None, "0", "abc"):
    s_n = spec("f0q", {**forms["f0q"]["f0q"], "K": blank})
    r = design(s_n, "Lowpass", gain_mode=ct.GAIN_NORMALIZE)
    assert not r["errors"] and r["info"]["k_entered"] == 1.0, (blank, r["errors"])
    assert design(s_n, "Lowpass", gain_mode=ct.GAIN_AS_ENTERED)["errors"]
ok("Normalize: a blank / zero / invalid K counts as 1 (As entered still requires it)")
r = design(spec("ts", {"stages": [{"a": 1 / q, "b": 1.0} for q in q_ex], "A0": 2.0}), "Lowpass",
           gain_mode=ct.GAIN_NORMALIZE)
kshow = 3.0 * abs(r["info"]["k_entered"]) / r["info"]["peak_gain"]
assert abs(kshow - 3.0) <= 1e-9, kshow
ok("greyed A₀ for passband gain 3 V/V on a Butterworth LP (peak at DC) = 3")
s_p = spec("f0q", {**forms["f0q"]["f0q"], "n_origin": 2})
r = design(s_p, "Highpass", ct.MODE_PROTOTYPE, f1_hz=1e3)
assert not r["errors"] and r["info"]["n_origin_zeros"] == 4, r["errors"]
ok("prototype mode ignores a stale n₀ (the field is hidden there)")

print("\nALL CHECKS PASSED")
