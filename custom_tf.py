# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin
"""Custom H(s) (FS-007) -- Tier A, no Streamlit.

The user types a transfer function instead of choosing an approximation. Every input form
(coefficients, factored (f0, Q), factored Tietze-Schenk, root coordinates) is parsed to one
normalized zpk plus w_n; that is snapped to exactly clean root positions, passed through the
realizability gate (only origin / jw / infinity zeros with exact conjugates and strictly-LHP
poles leave this module -- what the pairers and cells assume, docs/CONTRACTS.md §2), mapped
from a lowpass prototype if asked, measured (peak, type, passband edges) and returned as an
`engine_results` dict with `custom_info`.

Math base, measured numerics and the gate table: dev/FS-007_custom_tf_design_note.md.
Checks: python dev/fs007/check_custom_tf.py
"""

import hashlib
import math
import re

import numpy as np
from scipy import signal
from scipy.optimize import brentq, minimize_scalar

from pairing_utils import build_stage_bricks, auto_pair_stages

CUSTOM = "Custom H(s)"
FORMS = ("coeff", "f0q", "ts", "roots")
MODE_COMPLETE, MODE_PROTOTYPE = "complete", "prototype"
GAIN_NORMALIZE, GAIN_AS_ENTERED = "normalize", "as_entered"
UNIT_MULT = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9}

MAX_POLES_COMPLETE = 30                       # the largest standard design (Butterworth BP 15+15)
MAX_PROTO_ORDER = {"Lowpass": 20, "Highpass": 20, "Bandpass": 15, "Band-Reject": 15}
U_FLOOR = 1e-12            # input precision of values pasted at full precision
MIN_DIGITS = 6             # typed values count as at least this precise (the note's reference)
TAU_FACTOR = 4.0           # merge accepted while the backward error <= 4 u_in
REAL_TOL = 1e-9            # |Im r| <= REAL_TOL |r| -> real;  |Re r| <= REAL_TOL |r| -> on the jw axis
CONJ_TOL = 1e-6            # pasted full root lists: conjugate partners within this (relative)
PROTO_EDGE_MAX_DB = 40.0   # prototype |H(j1)| below the peak: beyond this, ω = 1 is no edge
Q_WARN = 100.0
CANCEL_TOL = 1e-6
COND_TRIALS = 8
COND_WARN_DB = 0.1
N_GRID = 20000
_DB = 20.0 / math.log(10.0)                   # ln|H| -> dB
_LN_FLOOR = -2000.0                           # ln|H| clip (exact notches give -inf)

FS013 = "RHP zeros (all-pass / non-minimum-phase) are FS-013"
FS014 = "off-axis LHP zeros need general-numerator cells (FS-014)"


class CustomTFError(Exception):
    """Input that cannot become a Custom design. `messages` is a list of user-facing lines."""

    def __init__(self, messages):
        self.messages = [messages] if isinstance(messages, str) else list(messages)
        super().__init__("; ".join(self.messages))


# =====================================================================
#  Spec (the panel's serializable state; FS-011 saves it, FS-012 drives it)
# =====================================================================
def default_spec():
    """Butterworth n = 4, normalized (-3 dB at w_n), entered identically in every form."""
    q1, q2 = 0.541196, 1.306563
    return {
        "version": 1, "form": "f0q", "scale": "normalized", "f_norm": 1.0, "unit": "kHz",
        "coeff": {"num": [1.0], "den": [1.0, 2.613126, 3.414214, 2.613126, 1.0],
                  "order": "desc"},
        "f0q": {"pole_pairs": [{"f0": 1.0, "Q": q1}, {"f0": 1.0, "Q": q2}], "real_poles": [],
                "zero_pairs": [], "real_zeros": [], "n_origin": 0, "K": 1.0},
        "ts": {"stages": [{"a": 1.847759, "b": 1.0}, {"a": 0.765367, "b": 1.0}], "A0": 1.0},
        "roots": {"poles": [[-0.382683, 0.923880], [-0.923880, 0.382683]], "zeros": [],
                  "K": 1.0, "k_form": "K"},     # k_form "C": K holds Saal's C = 1/K
    }


def _num(v):
    """A table cell (number or typed text) as float, or None when blank / NaN."""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip().replace("∞", "inf")
        if not v:
            return None
        try:
            v = float(v)
        except ValueError:
            raise CustomTFError(f"'{v}' is not a number.")
    v = float(v)
    if math.isnan(v):
        return None
    if math.isinf(v):
        return v
    return v


def _sig_digits(x):
    """Significant digits of a typed value from its shortest repr: 2.61313 -> 6, 1.0 -> 1."""
    if x == 0 or not math.isfinite(x):
        return 1
    mant = repr(abs(float(x))).lower().split("e")[0].replace(".", "")
    mant = mant.lstrip("0").rstrip("0")
    return max(1, len(mant))


def _u_in(coeffs, digits=None):
    """Input precision of one polynomial: half a unit in the last digit of its most precisely
    typed coefficient (relative), floored at U_FLOOR. `digits` (per coefficient, None = unknown)
    are the digits counted in the typed text; the float repr loses trailing zeros (1.02010).
    Short values ("1 0.5 1") are taken as exact to at least MIN_DIGITS digits: a 1-digit
    precision would let the merge fuse a Q = 2 conjugate pair into a double real pole."""
    digits = list(digits or [])
    ds = [(digits[i] if i < len(digits) and digits[i] else _sig_digits(c))
          for i, c in enumerate(coeffs) if c != 0]
    d = max(max(ds, default=17), MIN_DIGITS)
    return max(0.5 * 10.0 ** (1 - d), U_FLOOR)


def text_digits(tok):
    """Significant digits of a pasted token ('1.02010' -> 6, '2.5e3' -> 2)."""
    m = tok.strip().lstrip("+-").lower().split("e")[0].replace(".", "").lstrip("0")
    return max(1, len(m)) if m else 1


# ---------------------------------------------------------------------
#  Paste boxes
# ---------------------------------------------------------------------
_SPLIT = re.compile(r"[,\s;]+")


def coeff_tokens(text, order="desc"):
    """Pasted coefficients -> the typed tokens in ASCENDING power order (validated). Separators:
    comma, space, semicolon, newline; brackets ignored; scientific notation allowed. The panel
    stores the tokens themselves, so the typed digits survive (§4.3)."""
    t = re.sub(r"[\[\]\(\)]", " ", text or "")
    toks = [x for x in _SPLIT.split(t.strip()) if x]
    if not toks:
        raise CustomTFError("The paste box is empty.")
    for x in toks:
        try:
            v = float(x)
        except ValueError:
            raise CustomTFError(f"Cannot read the pasted coefficient '{x}'.")
        if not math.isfinite(v):
            raise CustomTFError("Pasted coefficients must be finite.")
    return toks[::-1] if order == "desc" else toks


def parse_coeff_text(text, order="desc"):
    """Pasted coefficients -> (ascending values, ascending typed digits)."""
    toks = coeff_tokens(text, order)
    return [float(x) for x in toks], [text_digits(x) for x in toks]


def _parse_complex_token(tok):
    t = tok.strip().replace(" ", "").lower().replace("i", "j")
    t = t.replace("+-", "-").replace("-+", "-").replace("--", "+")
    t = re.sub(r"(^|[+-])j", r"\g<1>1j", t)          # "-j" -> "-1j"
    return complex(t)


def parse_roots_text(text):
    """Pasted roots -> [[re, im], ...]. One root per line or comma-separated complex literals
    (-0.5+0.866j, MATLAB -0.5000 + 0.8660i), or 're im' / 're, im' pairs per line."""
    out = []
    for line in re.split(r"[\n;]+", re.sub(r"[\[\]]", " ", text or "")):
        line = line.strip().strip("()")
        if not line:
            continue
        toks = [x for x in re.split(r"[,\s]+", line) if x]
        plain = not any(c in "ij" for c in line.lower())
        if not (plain and len(toks) == 2):
            try:
                z = _parse_complex_token(line)                    # one root per line
                out.append([z.real, z.imag])
                continue
            except ValueError:
                pass
        try:
            if plain and len(toks) == 2:
                out.append([float(toks[0]), float(toks[1])])      # "re im" / "re, im"
            else:
                parts = [x for x in line.split(",") if x.strip()]
                for tk in (parts if len(parts) > 1 else line.split()):
                    z = _parse_complex_token(tk)
                    out.append([z.real, z.imag])
        except ValueError:
            raise CustomTFError(f"Cannot read the pasted root '{line}'.")
    if not out:
        raise CustomTFError("The paste box is empty.")
    return out


# =====================================================================
#  Numerics (§4)
# =====================================================================
def _balance_factor(c_asc):
    """g with s = g*s_hat making the end coefficients of sum c_i s^i equal in size."""
    c = np.asarray(c_asc, float)
    n = len(c) - 1
    return (abs(c[0] / c[-1])) ** (1.0 / n) if n > 0 else 1.0


def _merge(rb, cb, u_in):
    """Precision-aware repeated-root merge (§4.3) on balanced roots rb of the balanced
    ascending polynomial cb. Agglomerates closest-first (centroid linkage) and keeps the MOST
    merged partition whose rebuilt polynomial matches cb within 4 u_in (componentwise
    relative backward error). Returns (roots, n_clusters_merged)."""
    n = len(rb)
    if n < 2:
        return rb, 0
    target = np.asarray(cb, float)
    lead = target[-1]
    den = np.maximum(np.abs(target), 1e-6 * np.max(np.abs(target)))
    tau = TAU_FACTOR * u_in

    def berr(cents, mults):
        rebuilt = lead * np.poly(np.repeat(cents, mults))[::-1]
        return float(np.max(np.abs(rebuilt - target) / den))

    cents, mults = list(rb), [1] * n
    best = (list(cents), list(mults))
    while len(cents) > 1:
        bi, bj, bd = 0, 1, np.inf
        for i in range(len(cents)):
            for j in range(i + 1, len(cents)):
                d = abs(cents[i] - cents[j]) / max(abs(cents[i]), abs(cents[j]), 1e-300)
                if d < bd:
                    bi, bj, bd = i, j, d
        m = mults[bi] + mults[bj]
        c = (mults[bi] * cents[bi] + mults[bj] * cents[bj]) / m
        cents = [x for k, x in enumerate(cents) if k not in (bi, bj)] + [c]
        mults = [x for k, x in enumerate(mults) if k not in (bi, bj)] + [m]
        if berr(cents, mults) <= tau:
            best = (list(cents), list(mults))
    return np.repeat(np.asarray(best[0], complex), best[1]), sum(1 for m in best[1] if m > 1)


def poly_roots(c_asc, u_in=U_FLOOR, merge=True):
    """Roots of sum c_i s^i (c_asc[0] != 0 != c_asc[-1]) by balanced np.roots (§4.1) and the
    precision-aware merge (§4.3). Returns (roots, n_clusters_merged)."""
    c = np.asarray(c_asc, float)
    n = len(c) - 1
    if n <= 0:
        return np.array([], complex), 0
    g = _balance_factor(c)
    cb = c * g ** np.arange(n + 1)
    cb = cb / cb[-1]
    if not np.all(np.isfinite(cb)):
        raise CustomTFError("Coefficients overflow when balanced; enter the polynomial "
                            "normalized, factored or as roots.")
    rb = np.roots(cb[::-1]).astype(complex)
    nm = 0
    if merge:
        rb, nm = _merge(rb, cb, u_in)
    return rb * g, nm


def zeros_from_numerator(b_asc, g, u_in):
    """Even-part method (§4.2). N(s) = s^n0 E(s^2) when every finite zero is a transmission
    zero; x = s^2 real < 0 <=> a jw pair. g is the denominator's frequency scale (balancing).
    Returns (zeros, n_clusters_merged); non-conforming zeros are returned as they are, for the
    gate to name."""
    b = np.asarray(b_asc, float)
    bb = np.abs(b) * g ** np.arange(len(b))
    n0 = 0
    while n0 < len(b) - 1 and bb[n0] <= 1e-11 * bb.max():
        n0 += 1
    e = b[n0:]
    zeros = [0j] * n0
    if len(e) == 1:
        return zeros, 0
    eb = bb[n0:]
    odd_tol = max(1e-11, TAU_FACTOR * u_in) * eb[0::2].max()
    if eb[1::2].max() > odd_tol:                       # not mirrored about the jw axis
        r, nm = poly_roots(e, u_in)
        return zeros + list(r), nm
    xr, nm = poly_roots(e[0::2], u_in)
    for x in xr:
        if abs(x.imag) <= REAL_TOL * abs(x) and x.real < 0:
            w = math.sqrt(-x.real)
            zeros += [complex(0.0, w), complex(0.0, -w)]
        else:                                          # x > 0: +-sqrt(x); complex x: a quad
            s = np.sqrt(complex(x))
            zeros += [s, -s]
    return zeros, nm


def _pair_roots(w0, Q):
    """Roots of s^2 + (w0/Q) s + w0^2 (Q < 0 mirrors them into the RHP)."""
    if Q == 0:
        raise CustomTFError("Q = 0 is not a second-order factor.")
    a = -w0 / (2.0 * Q)
    if abs(Q) > 0.5:
        b = w0 * math.sqrt(1.0 - 1.0 / (4.0 * Q * Q))
        return [complex(a, b), complex(a, -b)]
    if abs(Q) == 0.5:
        return [complex(a, 0.0), complex(a, 0.0)]
    d = w0 * math.sqrt(1.0 / (4.0 * Q * Q) - 1.0)
    return [complex(a + d, 0.0), complex(a - d, 0.0)]


def _expand_rows(rows, what):
    """Root table rows (re, im) -> roots. Im > 0 = a conjugate pair, Im = 0 = one real root.
    A table / paste holding any Im < 0 is read as a full list (roots(den) output): every
    complex root then needs its conjugate, and pairs are de-duplicated."""
    pts = []
    for row in rows:
        re_, im_ = (_num(row[0]) if len(row) > 0 else None), (_num(row[1]) if len(row) > 1 else None)
        if re_ is None and im_ is None:
            continue
        pts.append(complex(re_ or 0.0, im_ or 0.0))
    if any(not (math.isfinite(z.real) and math.isfinite(z.imag)) for z in pts):
        raise CustomTFError(f"{what}: roots must be finite.")
    full = any(z.imag < -REAL_TOL * abs(z) for z in pts)
    out = []
    if not full:
        for z in pts:
            if abs(z.imag) <= REAL_TOL * abs(z):
                out.append(complex(z.real, 0.0))
            else:
                out += [complex(z.real, abs(z.imag)), complex(z.real, -abs(z.imag))]
        return out
    reals = [z for z in pts if abs(z.imag) <= REAL_TOL * abs(z)]
    pos = [z for z in pts if z.imag > REAL_TOL * abs(z)]
    neg = [z for z in pts if z.imag < -REAL_TOL * abs(z)]
    out += [complex(z.real, 0.0) for z in reals]
    for z in pos:
        if not neg:
            raise CustomTFError(f"{what}: lone complex root {z:.6g} (no conjugate). Enter Im ≥ 0 "
                                "to mean a conjugate pair, or include its conjugate.")
        k = int(np.argmin([abs(n - z.conjugate()) for n in neg]))
        n = neg[k]
        if abs(n - z.conjugate()) > CONJ_TOL * max(abs(z), 1e-300):
            raise CustomTFError(f"{what}: lone complex root {z:.6g} (no conjugate within "
                                f"{CONJ_TOL:g} relative).")
        neg.pop(k)
        a, b = 0.5 * (z.real + n.real), 0.5 * (z.imag - n.imag)
        out += [complex(a, b), complex(a, -b)]
    if neg:
        raise CustomTFError(f"{what}: lone complex root {neg[0]:.6g} (no conjugate).")
    return out


def snap_roots(roots, scale_ref, what="roots"):
    """§4.5: exact origin, exact jw (Re := 0), exact real (Im := 0), exact conjugate pairs."""
    tmp = []
    for r in roots:
        r = complex(r)
        m = abs(r)
        if m <= 1e-12 * scale_ref:
            tmp.append(0j)
            continue
        re_, im_ = r.real, r.imag
        if abs(im_) <= REAL_TOL * m:
            im_ = 0.0
        if abs(re_) <= REAL_TOL * m:
            re_ = 0.0
        tmp.append(complex(re_ + 0.0, im_ + 0.0))
    out = [r for r in tmp if r.imag == 0.0]
    pos = [r for r in tmp if r.imag > 0.0]
    neg = [r for r in tmp if r.imag < 0.0]
    for z in sorted(pos, key=lambda x: (x.imag, x.real)):
        if not neg:
            raise CustomTFError(f"{what}: lone complex root {z:.6g}.")
        k = int(np.argmin([abs(n - z.conjugate()) for n in neg]))
        n = neg.pop(k)
        if abs(n - z.conjugate()) > CONJ_TOL * abs(z):
            raise CustomTFError(f"{what}: lone complex root {z:.6g}.")
        a, b = 0.5 * (z.real + n.real), 0.5 * (z.imag - n.imag)
        out += [complex(a, b), complex(a, -b)]
    if neg:
        raise CustomTFError(f"{what}: lone complex root {neg[0]:.6g}.")
    return np.asarray(out, complex)


# =====================================================================
#  Parsing (§3): every form -> normalized (z_n, p_n, K) + w_n
# =====================================================================
def _k_value(v, name, required):
    """K / A0 cell. Not required (Normalize: the peak is renormalized anyway, and the panel
    shows the field greyed) -> a blank / zero / invalid entry counts as 1."""
    try:
        k = _num(v)
    except CustomTFError:
        if required:
            raise
        k = None
    if k is None or k == 0 or math.isinf(k):
        if required:
            raise CustomTFError(f"{name} must be a nonzero number.")
        return 1.0
    return k


def parse_spec(spec, prototype=False, k_required=True):
    """-> dict(z, p, K, w_n, form, scale, f_norm_hz, n_merged, coeff). z / p are in the
    ENTERED domain (s_n for normalized, rad/s for absolute) and are not snapped yet."""
    form = spec.get("form", "f0q")
    if form not in FORMS:
        raise CustomTFError(f"Unknown input form '{form}'.")
    unit = spec.get("unit", "kHz")
    mult = UNIT_MULT.get(unit, 1e3)
    scale = "normalized" if (prototype or form == "ts") else spec.get("scale", "normalized")
    f_norm_hz = None
    if prototype:
        w_n = 1.0
    elif scale == "normalized":
        fn = _num(spec.get("f_norm"))
        if fn is None or not (fn > 0) or math.isinf(fn):
            raise CustomTFError("The normalization frequency f_n must be > 0.")
        f_norm_hz = fn * mult
        w_n = 2 * math.pi * f_norm_hz
    else:
        w_n = 1.0
    fscale = 1.0 if scale == "normalized" else 2 * math.pi * mult   # (f0, Q) frequencies
    out = {"form": form, "scale": scale, "f_norm_hz": f_norm_hz, "w_n": w_n, "n_merged": 0,
           "coeff": None}
    blk = spec.get(form) or {}

    if form == "coeff":
        raw_b, raw_a = list(blk.get("num", [])), list(blk.get("den", []))
        b = [_num(v) for v in raw_b]
        a = [_num(v) for v in raw_a]
        b = [0.0 if v is None else v for v in b]
        a = [0.0 if v is None else v for v in a]
        if any(not math.isfinite(v) for v in a + b):
            raise CustomTFError("Coefficients must be finite.")
        while b and b[-1] == 0:
            b.pop()
        while a and a[-1] == 0:
            a.pop()
        if not b:
            raise CustomTFError("The numerator is empty or all zero.")
        if not a:
            raise CustomTFError("The denominator is empty or all zero.")
        if len(a) == 1:
            raise CustomTFError("The denominator has no s terms: no poles.")
        if a[-1] < 0:                                   # D with a positive leading coefficient
            a, b = [-v for v in a], [-v for v in b]
        n_p0 = 0
        while n_p0 < len(a) and a[n_p0] == 0:
            n_p0 += 1
        dig = blk.get("digits") or {}                 # explicit digits, else from typed text
        txt = lambda raw: [text_digits(x) if isinstance(x, str) and x.strip() else None
                           for x in raw]
        u_den = _u_in(a, dig.get("den") or txt(raw_a))
        u_num = _u_in(b, dig.get("num") or txt(raw_b))
        p, nm_p = poly_roots(a[n_p0:], u_den)
        p = [0j] * n_p0 + list(p)
        g = _balance_factor(a[n_p0:]) if len(a) - n_p0 > 1 else 1.0
        z, nm_z = zeros_from_numerator(b, g, u_num)
        out.update(z=z, p=p, K=b[-1] / a[-1], k_entered=b[-1] / a[-1], n_merged=nm_p + nm_z,
                   coeff={"num": b, "den": a, "u_num": u_num, "u_den": u_den})
        return out

    if form == "f0q":
        p, z = [], []
        for i, row in enumerate(blk.get("pole_pairs", [])):
            f0, Q = _num(row.get("f0")), _num(row.get("Q"))
            if f0 is None and Q is None:
                continue
            if f0 is None or not (f0 > 0):
                raise CustomTFError(f"Pole pair {i + 1}: f₀ must be > 0.")
            if Q is None or math.isinf(Q):
                raise CustomTFError(f"Pole pair {i + 1}: Q is needed (Q = ∞ puts the poles on "
                                    "the jω axis).")
            p += _pair_roots(f0 * fscale, Q)
        for f in blk.get("real_poles", []):
            f = _num(f)
            if f is not None:
                p.append(complex(-f * fscale, 0.0))
        for i, row in enumerate(blk.get("zero_pairs", [])):
            fz, Qz = _num(row.get("fz")), _num(row.get("Qz"))
            if fz is None and Qz is None:
                continue
            if fz is None or not (fz > 0):
                raise CustomTFError(f"Zero pair {i + 1}: f_z must be > 0.")
            if Qz is None or math.isinf(Qz):
                w = fz * fscale
                z += [complex(0.0, w), complex(0.0, -w)]
            else:
                z += _pair_roots(fz * fscale, Qz)
        for sz in blk.get("real_zeros", []):
            sz = _num(sz)
            if sz is not None:
                z.append(complex(sz * fscale, 0.0))
        n0 = 0 if prototype else (_num(blk.get("n_origin")) or 0)   # prototype: no origin zeros
        if n0 < 0 or n0 != int(n0):
            raise CustomTFError("The origin-zero count must be a whole number ≥ 0.")
        z += [0j] * int(n0)
        K = _k_value(blk.get("K"), "K", k_required)
        out.update(z=z, p=p, K=K, k_entered=K)
        return out

    if form == "ts":
        p, lead = [], 1.0
        for i, row in enumerate(blk.get("stages", [])):
            a, b = _num(row.get("a")), _num(row.get("b"))
            if a is None and b is None:
                continue
            a, b = a or 0.0, b or 0.0
            if b == 0:
                if a == 0:
                    raise CustomTFError(f"Row {i + 1}: a and b are both 0.")
                p.append(complex(-1.0 / a, 0.0))
                lead *= a
            elif b > 0:
                if a == 0:
                    raise CustomTFError(f"Row {i + 1}: a = 0 puts the poles on the jω axis.")
                p += _pair_roots(1.0 / math.sqrt(b), math.sqrt(b) / a)
                lead *= b
            else:
                r = np.roots([b, a, 1.0])
                p += [complex(x) for x in r]
                lead *= b
        A0 = _k_value(blk.get("A0"), "A₀", k_required)
        out.update(z=[], p=p, K=A0 / lead, k_entered=A0)
        return out

    # roots
    p = _expand_rows(blk.get("poles", []), "Poles")
    z = _expand_rows(blk.get("zeros", []), "Zeros")
    if blk.get("k_form") == "C":       # Saal: the constant of the attenuation function, K = 1/C
        K = 1.0 / _k_value(blk.get("K"), "C", k_required)
    else:
        K = _k_value(blk.get("K"), "K", k_required)
    out.update(z=z, p=p, K=K, k_entered=K)
    return out


# =====================================================================
#  Classification and gate (§7.1)
# =====================================================================
def _fmt_r(r):
    return f"{r.real:.6g}{r.imag:+.6g}j" if r.imag else f"{r.real:.6g}"


def classify_zeros(z):
    """-> dict(origin, jw (upper roots), real_lhp, real_rhp, off_lhp, off_rhp) on snapped z."""
    c = {"origin": 0, "jw": [], "real_lhp": [], "real_rhp": [], "off_lhp": [], "off_rhp": []}
    for r in z:
        if r == 0:
            c["origin"] += 1
        elif r.real == 0.0:
            if r.imag > 0:
                c["jw"].append(r)
        elif r.imag == 0.0:
            c["real_lhp" if r.real < 0 else "real_rhp"].append(r)
        elif r.imag > 0:
            c["off_lhp" if r.real < 0 else "off_rhp"].append(r)
    return c


def gate_zeros(z, where=""):
    """Transmission zeros only (origin, jw, infinity). Returns error lines."""
    c = classify_zeros(z)
    errs = []

    def lst(v):
        return ", ".join(_fmt_r(r) for r in v[:3]) + (" …" if len(v) > 3 else "")
    if c["real_lhp"]:
        errs.append(f"{where}Real LHP zero(s) at {lst(c['real_lhp'])}: only transmission zeros "
                    f"(origin, jω axis, ∞) are supported now — {FS014}.")
    if c["real_rhp"]:
        errs.append(f"{where}Real RHP zero(s) at {lst(c['real_rhp'])}: {FS013}.")
    if c["off_lhp"]:
        errs.append(f"{where}Off-axis LHP zero pair(s) at {lst(c['off_lhp'])}: only jω-axis "
                    f"zero pairs are supported now — {FS014}.")
    if c["off_rhp"]:
        errs.append(f"{where}RHP zero pair(s) at {lst(c['off_rhp'])}: {FS013}.")
    return errs


def gate_poles(p):
    errs = []
    n_or = sum(1 for r in p if r == 0)
    n_jw = sum(1 for r in p if r != 0 and r.real == 0.0)
    n_rhp = sum(1 for r in p if r.real > 0)          # snapped: |Re| <= REAL_TOL|p| is already 0
    if n_or:
        errs.append(f"{n_or} pole(s) at the origin: not a stable filter (the pairer would drop "
                    "them).")
    if n_jw:
        errs.append(f"{n_jw} pole(s) on the jω axis (Q = ∞): not realizable.")
    if n_rhp:
        errs.append(f"{n_rhp} pole(s) in the right half-plane: the filter would be unstable.")
    return errs


def counts(z, p):
    c = classify_zeros(z)
    n_cpairs = sum(1 for r in p if r.imag > 0)
    return {"n_poles": len(p), "n_zeros": len(z), "n_origin_zeros": c["origin"],
            "n_jw_pairs": len(c["jw"]), "n_real_poles": sum(1 for r in p if r.imag == 0.0),
            "n_complex_pole_pairs": n_cpairs}


def gate_structure(z, p, filter_type, n_max):
    """Target-level gate rows of §7.1 (improper, limits, pairer capacity)."""
    errs = []
    c = counts(z, p)
    if c["n_poles"] == 0:
        errs.append("H(s) has no poles.")
    if c["n_zeros"] > c["n_poles"]:
        errs.append(f"Improper H(s): {c['n_zeros']} finite zeros > {c['n_poles']} poles; no cell "
                    "has a numerator of higher degree than its denominator.")
    if c["n_poles"] > n_max:
        errs.append(f"{c['n_poles']} poles exceed the limit of {n_max}.")
    if c["n_jw_pairs"] > c["n_complex_pole_pairs"]:
        errs.append(f"{c['n_jw_pairs']} jω zero pairs but only {c['n_complex_pole_pairs']} complex "
                    "pole pairs: a jω pair needs a second-order section (combining two real poles "
                    "into one is FS-016's open question).")
    if filter_type in ("Bandpass", "Band-Reject") and c["n_real_poles"] >= 2:
        errs.append(f"{filter_type} with {c['n_real_poles']} real poles: today's "
                    f"{filter_type.lower()} pairer mishandles more than one real pole "
                    "(FS-016). Use a narrower band or complex poles.")
    return errs


def structure_warnings(z, p):
    w = []
    for r in p:
        if r.imag > 0:
            q = abs(r) / (-2.0 * r.real)
            if q > Q_WARN:
                w.append(f"Pole pair with Q = {q:.4g} > {Q_WARN:g}: legal, but very sensitive in "
                         "hardware.")
    for zz in z:
        for pp in p:
            if abs(zz - pp) < CANCEL_TOL * abs(pp):
                w.append(f"Near pole/zero cancellation at {_fmt_r(pp)}: a wasted section.")
                break
    return w


# =====================================================================
#  Measurement (§5.3, §6.1)
# =====================================================================
def _grid(z, p, n=N_GRID):
    mags = [abs(r) for r in list(p) + list(z) if abs(r) > 0]
    lo, hi = min(mags) / 100.0, max(mags) * 100.0
    return np.logspace(np.log10(lo), np.log10(hi), n)


def log_mag(w, z, p, k):
    """ln|H(jw)| as a sum of logs (no overflow at n = 30 in rad/s); -inf at exact notches."""
    s = 1j * np.atleast_1d(np.asarray(w, float))[:, None]
    z, p = np.asarray(z, complex), np.asarray(p, complex)
    with np.errstate(divide="ignore"):
        out = np.full(s.shape[0], math.log(abs(k)))
        if z.size:
            out = out + np.sum(np.log(np.abs(s - z[None, :])), axis=1)
        if p.size:
            out = out - np.sum(np.log(np.abs(s - p[None, :])), axis=1)
    return out


def _log_dc(z, p, k):
    if any(r == 0 for r in z):
        return -np.inf
    return math.log(abs(k)) + sum(math.log(abs(r)) for r in z) - sum(math.log(abs(r)) for r in p)


def _log_inf(z, p, k):
    return math.log(abs(k)) if len(z) == len(p) else -np.inf


def peak_gain(z, p, k):
    """-> (ln peak, w_peak rad/s; 0 = DC, inf = HF plateau)."""
    w = _grid(z, p)
    lm = log_mag(w, z, p, k)
    i = int(np.argmax(lm))
    best, wb = float(lm[i]), float(w[i])
    lo, hi = w[max(i - 1, 0)], w[min(i + 1, len(w) - 1)]
    if hi > lo:
        r = minimize_scalar(lambda u: -float(log_mag([math.exp(u)], z, p, k)[0]),
                            bounds=(math.log(lo), math.log(hi)), method="bounded",
                            options={"xatol": 1e-10})
        if r.success and -r.fun > best:
            best, wb = float(-r.fun), math.exp(r.x)
    ldc, linf = _log_dc(z, p, k), _log_inf(z, p, k)
    if ldc >= best:
        best, wb = ldc, 0.0
    if linf > best:
        best, wb = linf, math.inf
    return best, wb


def detect_type(z, p, k, lpk, alpha_db):
    """Peak-relative type hint (§5.3). -> (type, h0_db, hinf_db) with dB relative to the peak
    (None where the gain is exactly 0)."""
    g0 = (_log_dc(z, p, k) - lpk) * _DB
    ginf = (_log_inf(z, p, k) - lpk) * _DB
    pass_l, stop_l = -(alpha_db + 1.0), -max(10.0, alpha_db + 3.0)
    ok = lambda g: g >= pass_l
    st = lambda g: g <= stop_l
    if ok(g0) and st(ginf):
        t = "Lowpass"
    elif st(g0) and ok(ginf):
        t = "Highpass"
    elif st(g0) and st(ginf):
        t = "Bandpass"
    elif ok(g0) and ok(ginf):
        lm = log_mag(_grid(z, p), z, p, k)
        t = "Band-Reject" if (np.min(lm) - lpk) * _DB <= stop_l else "other"
    else:
        t = "other"
    fin = lambda g: None if not np.isfinite(g) else float(g)
    return t, fin(g0), fin(ginf)


def measure_edges(z, p, k, lpk, alpha_db, filter_type):
    """Outermost crossings of peak - alpha (§5.3). -> (w1, w2|None) rad/s, or CustomTFError."""
    lvl = lpk - alpha_db / _DB
    w = _grid(z, p)
    lm = np.maximum(log_mag(w, z, p, k), _LN_FLOOR)
    above = lm >= lvl
    u = np.log(w)
    fail = CustomTFError(f"The response never falls {alpha_db:g} dB below its peak where a "
                         f"{filter_type.lower()} needs it: not a {filter_type} shape at this α "
                         "(or a crossing lies outside 1/100 … 100× the root span).")

    def cross(i):          # refine between grid points i and i+1
        f = lambda x: max(float(log_mag([math.exp(x)], z, p, k)[0]), _LN_FLOOR) - lvl
        try:
            return math.exp(brentq(f, u[i], u[i + 1], xtol=1e-13, rtol=1e-14))
        except ValueError:
            return float(w[i])

    idx = np.flatnonzero(above)
    if idx.size == 0:
        raise fail
    first, last = int(idx[0]), int(idx[-1])
    if filter_type == "Lowpass":
        if last >= len(w) - 1:
            raise fail
        return cross(last), None
    if filter_type == "Highpass":
        if first <= 0:
            raise fail
        return cross(first - 1), None
    if filter_type == "Bandpass":
        if first <= 0 or last >= len(w) - 1:
            raise fail
        return cross(first - 1), cross(last)
    dip = int(np.argmin(lm))                                   # Band-Reject
    if above[dip]:
        raise fail
    before = np.flatnonzero(above[:dip])
    after = np.flatnonzero(above[dip + 1:])
    if before.size == 0 or after.size == 0:
        raise fail
    return cross(int(before[-1])), cross(dip + int(after[0]))


def conditioning(coeff, w_pass_entered, trials=COND_TRIALS, seed=7):
    """§4.4: perturb every entered coefficient within +-u_in (relative, uniform -- the
    statistics of rounding to the typed digits), recompute the response over the passband.
    -> (worst deviation dB, rhp: bool)."""
    b, a = np.asarray(coeff["num"], float), np.asarray(coeff["den"], float)   # a[0] != 0 (gated)
    g = _balance_factor(a)
    pb = g ** np.arange(len(b))
    pa = g ** np.arange(len(a))
    s = 1j * np.asarray(w_pass_entered, float) / g

    def resp(bn, an):
        h = np.polyval((bn * pb)[::-1], s) / np.polyval((an * pa)[::-1], s)
        db = _DB * np.log(np.maximum(np.abs(h), 1e-300))
        return db - db.max()
    nom = resp(b, a)
    rng = np.random.default_rng(seed)
    worst, rhp = 0.0, False
    for _ in range(trials):
        bn = b * (1 + coeff["u_num"] * rng.uniform(-1.0, 1.0, size=b.size))
        an = a * (1 + coeff["u_den"] * rng.uniform(-1.0, 1.0, size=a.size))
        worst = max(worst, float(np.max(np.abs(resp(bn, an) - nom))))
        if np.max(np.roots((an * pa)[::-1]).real) > 0:
            rhp = True
    return worst, rhp


# =====================================================================
#  Prototype transforms (§5.2) and pre-flight (§7.2)
# =====================================================================
def lp_prototype_to(z, p, K, filter_type, f1_hz, f2_hz=None):
    """Normalized LP prototype -> target via scipy.signal.lp2*_zpk (k carried exactly)."""
    z, p = np.asarray(z, complex), np.asarray(p, complex)
    if filter_type == "Lowpass":
        return signal.lp2lp_zpk(z, p, K, wo=2 * math.pi * f1_hz)
    if filter_type == "Highpass":
        return signal.lp2hp_zpk(z, p, K, wo=2 * math.pi * f1_hz)
    w1, w2 = 2 * math.pi * f1_hz, 2 * math.pi * f2_hz
    wo, bw = math.sqrt(w1 * w2), w2 - w1
    if filter_type == "Bandpass":
        return signal.lp2bp_zpk(z, p, K, wo=wo, bw=bw)
    return signal.lp2bs_zpk(z, p, K, wo=wo, bw=bw)


def preflight_pairing(poles, zeros, filter_type):
    """§7.2: run today's auto-pairing (3rd-order off and on) and flag every stage the cells
    would realize wrongly. Checks the RESULT, so it stays valid when FS-016 changes routers."""
    pb, zb = build_stage_bricks(np.asarray(poles), np.asarray(zeros), "Denormalized", 1.0)
    has_real = any(b["type"] == "Real" for b in pb)
    msgs = []
    for absorb in ((False, True) if has_real else (False,)):
        tag = "with 3rd-order sections" if absorb else "auto-pairing"
        try:
            stages = auto_pair_stages(pb, zb, absorb_1st_order=absorb, filter_type=filter_type)
        except Exception as e:
            msgs.append(f"{tag}: the pairer failed ({type(e).__name__}: {e}).")
            continue
        assigned = [zid for s in stages for zid in s.get("zero_ids", [])]
        if len(set(assigned)) < len(zb):
            msgs.append(f"{tag}: {len(zb) - len(set(assigned))} zero(s) stay unassigned "
                        "(Pairing Incomplete).")
        for s in stages:
            pole = next((b for b in pb if b["id"] == s["pole_id"]), None)
            if pole is None:
                continue
            order = (2 if pole["type"] == "Complex Pair" else 1) + \
                (1 if s.get("absorbed_real_id", "None") not in ("None", None) else 0)
            zs = [b for b in zb if b["id"] in s.get("zero_ids", [])]
            nz = sum(2 if b["type"] == "Complex Pair" else 1 for b in zs)
            n_or = sum(1 for b in zs if b["type"] == "Origin")
            pair = next((b for b in zs if b["type"] == "Complex Pair"), None)
            hpn3 = order == 3 and n_or == 1 and pair is not None and pair["w0"] < pole["w0"]
            if nz > order:
                msgs.append(f"{tag}: stage {s['stage_num']} (order {order}) gets {nz} zeros.")
            elif pair is not None and n_or > 0 and not hpn3:
                msgs.append(f"{tag}: stage {s['stage_num']} (order {order}) gets a jω pair and "
                            f"{n_or} origin zero(s); the cells would ignore the origin zero.")
    if msgs:
        msgs.append("Re-pair the flagged stages by hand in the Biquad Pairing tab.")
    return msgs


def roots_sig(z, p):
    """Hash of the snapped physical roots (sorted, 10 significant digits) for Tab 3."""
    fmt = lambda r: f"{r.real + 0.0:.10g},{r.imag + 0.0:.10g}"
    s = "|".join(sorted(fmt(r) for r in p)) + "/" + "|".join(sorted(fmt(r) for r in z))
    return hashlib.sha1(s.encode()).hexdigest()[:16]


# =====================================================================
#  Entry
# =====================================================================
def design_custom(spec, filter_type, mode=MODE_COMPLETE, f1_hz=None, f2_hz=None,
                  alpha_db=3.0103, gain_mode=GAIN_NORMALIZE):
    """Custom H(s) -> {"errors", "warnings", "preflight", "info", "engine_results"}.
    engine_results is None when errors is non-empty; info holds what could be measured.
    Complete mode with filter_type None: the type is detected (info['target']); a shape that is
    not LP / HP / BP / BR is an error. gain_mode "normalize" does not require K / A0."""
    res = {"errors": [], "warnings": [], "preflight": [], "info": {}, "engine_results": None}
    try:
        _design(res, spec, filter_type, mode, f1_hz, f2_hz, alpha_db, gain_mode)
    except CustomTFError as e:
        res["errors"] += e.messages
    if res["errors"]:
        res["engine_results"] = None
    return res


def _design(res, spec, filter_type, mode, f1_hz, f2_hz, alpha_db, gain_mode):
    info, warn = res["info"], res["warnings"]
    proto = mode == MODE_PROTOTYPE
    alpha_db = max(float(alpha_db), 0.01)
    auto = (not proto) and filter_type in (None, "auto")    # complete mode: type is detected
    ps = parse_spec(spec, prototype=proto, k_required=gain_mode != GAIN_NORMALIZE)
    info.update(mode=mode, form=ps["form"], scale=ps["scale"], f_norm_hz=ps["f_norm_hz"],
                target=filter_type, gain_mode=gain_mode, n_merged_clusters=ps["n_merged"],
                alpha_db=alpha_db, proto_edge_db=None, conditioning_db=None,
                k_entered=float(ps["k_entered"]))
    K = float(ps["K"])
    info["k_sign"] = 1 if K > 0 else -1
    if K < 0 and (gain_mode != GAIN_NORMALIZE or ps["form"] == "coeff"):   # K is greyed out
        warn.append("K < 0 is used as |K|: the sign is a realization property (the cells set it; "
                    "CONTRACTS §5).")
    K = abs(K)
    pn = np.asarray(ps["p"], complex)
    if pn.size == 0:
        raise CustomTFError("H(s) has no poles.")
    ref = float(np.max(np.abs(pn)))
    if not (ref > 0):
        raise CustomTFError(gate_poles(snap_roots(pn, 1.0, "Poles")) or "H(s) has no poles.")
    pn = snap_roots(pn, ref, "Poles")
    zn = snap_roots(ps["z"], ref, "Zeros")
    info.update(counts(zn, pn))
    errs = gate_poles(pn) + gate_zeros(zn, "Prototype: " if proto else "")
    if errs:
        raise CustomTFError(errs)
    if ps["n_merged"]:
        warn.append(f"{ps['n_merged']} cluster(s) of repeated roots merged at the typed precision; "
                    "the factored or roots forms carry multiplicity exactly.")

    if proto:
        if any(r == 0 for r in zn):
            raise CustomTFError("A lowpass prototype has no zeros at the origin.")
        if len(zn) > len(pn):
            raise CustomTFError(f"Improper prototype: {len(zn)} zeros > {len(pn)} poles.")
        n_max = MAX_PROTO_ORDER[filter_type]
        if len(pn) > n_max:
            raise CustomTFError(f"Prototype order {len(pn)} exceeds the limit of {n_max} for "
                                f"{filter_type}.")
        lpk0, _ = peak_gain(zn, pn, K)
        t0, _, _ = detect_type(zn, pn, K, lpk0, 3.0103)
        if t0 != "Lowpass":
            warn.append(f"The prototype does not look like a lowpass (detected: {t0}).")
        a_proto = -(float(log_mag([1.0], zn, pn, K)[0]) - lpk0) * _DB
        info["h_j1_db"] = -a_proto
        info["f3db_ratio"] = _edge_ratio(zn, pn, K, lpk0, "Lowpass", 1.0)
        if not (a_proto <= PROTO_EDGE_MAX_DB):     # also catches a notch at ω = 1 (inf)
            raise CustomTFError(f"|H(j1)| is {a_proto:.3g} dB below the prototype's peak: ω = 1 "
                                "must be its passband edge (check the table's normalization).")
        if a_proto > 12.0:
            warn.append(f"|H(j1)| is {a_proto:.3g} dB below the peak: ω = 1 is not the prototype's "
                        "passband edge (check the table's normalization).")
        a_proto = max(a_proto, 0.01)
        info["proto_edge_db"] = a_proto
        info["alpha_db"] = a_proto
        if filter_type in ("Bandpass", "Band-Reject"):
            if not (f1_hz and f2_hz and f2_hz > f1_hz > 0):
                raise CustomTFError("The prototype needs 0 < f1 < f2.")
        elif not (f1_hz and f1_hz > 0):
            raise CustomTFError("The prototype needs a corner frequency > 0.")
        zt, pt, kt = lp_prototype_to(zn, pn, K, filter_type, f1_hz, f2_hz)
        ref_t = float(np.max(np.abs(pt)))
        p_phys = snap_roots(pt, ref_t, "Poles")
        z_phys = snap_roots(zt, ref_t, "Zeros")
        k_phys = abs(float(np.real(kt)))
        edges = (f1_hz, f2_hz if filter_type in ("Bandpass", "Band-Reject") else None)
        w_n = 2 * math.pi * (math.sqrt(f1_hz * f2_hz) if edges[1] else f1_hz)
        n_max_t = 2 * MAX_PROTO_ORDER[filter_type]      # already limited at the prototype
    else:
        w_n0 = ps["w_n"]
        p_phys, z_phys = pn * w_n0, zn * w_n0
        k_phys = K * w_n0 ** (len(pn) - len(zn))
        n_max_t = MAX_POLES_COMPLETE
        edges, w_n = None, None

    info.update(counts(z_phys, p_phys))
    lpk, wpk = peak_gain(z_phys, p_phys, k_phys)
    info["peak_gain"] = math.exp(lpk)
    info["peak_hz"] = wpk / (2 * math.pi)
    det, h0, hinf = detect_type(z_phys, p_phys, k_phys, lpk, info["alpha_db"])
    info.update(detected=det, h0_db=h0, hinf_db=hinf)
    if auto:
        filter_type = det
        info["target"] = det
    errs = gate_structure(z_phys, p_phys, filter_type if filter_type != "other" else "Lowpass",
                          n_max_t)
    if filter_type == "other":
        fmt = lambda g: "−∞ dB" if g is None else f"{g:.3g} dB"
        errs.append(f"The response is not recognised as a lowpass, highpass, bandpass or "
                    f"band-reject at α = {alpha_db:g} dB (|H(0)| {fmt(h0)}, |H(∞)| {fmt(hinf)} "
                    "re the peak). Change α or H(s).")
    if errs:
        raise CustomTFError(errs)
    warn += structure_warnings(z_phys, p_phys)

    if not proto:
        if not auto and det != filter_type:
            warn.append(f"Detected type: {det}, but the Filter Type radio says {filter_type}; "
                        f"the {filter_type.lower()} pairing router and plots are used.")
        w1, w2 = measure_edges(z_phys, p_phys, k_phys, lpk, alpha_db, filter_type)
        edges = (w1 / (2 * math.pi), None if w2 is None else w2 / (2 * math.pi))
        if ps["scale"] == "normalized":
            w_n = ps["w_n"]
            info["h_j1_db"] = (float(log_mag([w_n], z_phys, p_phys, k_phys)[0]) - lpk) * _DB
            info["f3db_ratio"] = _edge_ratio(z_phys, p_phys, k_phys, lpk, det, w_n)
        else:
            w_n = 2 * math.pi * (math.sqrt(edges[0] * edges[1]) if edges[1] else edges[0])
        if ps["coeff"] is not None:
            lvl = lpk - alpha_db / _DB
            wg = _grid(z_phys, p_phys, 4000)
            wp = wg[log_mag(wg, z_phys, p_phys, k_phys) >= lvl]
            if wp.size:
                dev, rhp = conditioning(ps["coeff"], wp / ps["w_n"])
                info["conditioning_db"] = dev
                if rhp:
                    raise CustomTFError(
                        "Coefficient conditioning: perturbing the coefficients by their typed "
                        "precision moves a pole into the RHP. The typed digits do not pin this "
                        "H(s) down — enter it factored (per biquad) or as roots.")
                if dev > COND_WARN_DB:
                    warn.append(f"Coefficient conditioning: the typed precision leaves "
                                f"{dev:.3g} dB of passband uncertainty. Enter it factored "
                                "(per biquad) or as roots for an exact design.")
    info["edges_hz"] = edges
    info["w_n"] = float(w_n)

    G = math.exp(lpk)
    k_eng = math.exp(math.log(k_phys) - lpk)
    if gain_mode == GAIN_AS_ENTERED and G < 10 ** (-0.01 / 20):   # not for 6-digit rounding
        warn.append(f"Entered peak gain G = {G:.4g} V/V < 1: an attenuating passband. Some VCVS "
                    "sections may be unsolvable (a 3rd-order VCVS lowpass cannot go below 1); "
                    "MFB and AM still work.")
    info["roots_sig"] = roots_sig(z_phys, p_phys)
    res["preflight"] = preflight_pairing(p_phys, z_phys, filter_type)
    info["warnings"] = list(warn)
    info["preflight"] = list(res["preflight"])
    res["engine_results"] = {"poles": np.asarray(p_phys, complex),
                             "zeros": np.asarray(z_phys, complex),
                             "k": float(k_eng), "custom_info": info}


def _edge_ratio(z, p, k, lpk, ftype, w_n):
    """f_-3dB / f_n for a normalized entry (LP / HP shapes only), else None."""
    if ftype not in ("Lowpass", "Highpass"):
        return None
    try:
        w, _ = measure_edges(z, p, k, lpk, 10 * math.log10(2.0), ftype)
        return w / w_n
    except CustomTFError:
        return None
