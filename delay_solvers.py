# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Tier A -- delay-oriented all-pole prototypes (FS-006).

Bessel (maximally flat group delay) and Equiripple Delay (Chebyshev approximation of a
constant group delay, Ulbrich-Piloty type), plus everything the engine and the sidebar need
around them: corner normalization at an arbitrary attenuation alpha, stopband notches that
leave the delay untouched, the two bandpass mappings, order selection from specs, and the
`delay_info` summary consumed by Tier D.

Conventions
-----------
* Prototype frequencies are angular (rad/s). "Delay-normalized" poles have a nominal delay of
  1 s: tau(0) = 1 for Bessel, the centre of the +/-delta ripple band for Equiripple Delay.
* "Corner-normalized" poles have |H(j1)| = -alpha dB relative to DC, the same convention as
  every other LP solver in `filter_solvers.py`; the engine multiplies them by 2*pi*fc.
* W_alpha(n) = omega_alpha * tau_nom converts one normalization into the other.

Math base, measured tables and the reasoning behind every rule here:
`dev/FS-006_bessel_eqdelay_design_note.md`. The verified reference implementation this file
was ported from, and the offline seed generator: `dev/fs006/`. Numeric checks:
`python dev/fs006/check_delay_solvers.py`.
"""
from __future__ import annotations

import functools
import math

import numpy as np
from scipy.optimize import least_squares, minimize_scalar
from scipy.signal import besselap

from filter_solvers import _transform_lp_to_bp

BESSEL = "Bessel"
EQDELAY = "Equiripple Delay"
DELAY_RESPONSES = (BESSEL, EQDELAY)
# Only these have a flat-delay passband. A rational highpass has finite gain at infinity, so its
# phase tends to a constant and tau -> 0 across the passband; a band-reject's delay rises steeply
# toward the notch edges in both passbands (design note sections 4.1 and 4.4).
DELAY_FILTER_TYPES = ("Lowpass", "Bandpass")

# Order limits (FS-006). besselap is exact far beyond 20; the equiripple solver has one
# embedded seed per order up to 15. A bandpass doubles the order, hence the lower cap.
MAX_ORDER_LP = {BESSEL: 20, EQDELAY: 15}
MAX_ORDER_BP = 10

# Equiripple delay ripple (fraction). The solver is tested to 0.05 % ... 20 %; the UI offers
# 0.05 % ... 10 % because the magnitude starts to peak noticeably above that.
DELTA_MIN, DELTA_MAX = 0.0005, 0.10

# Bandpass mappings.
BP_TRANSLATION = "translation"   # delay-preserving, arithmetic symmetry
BP_CLASSIC = "classic"           # reactance transform, geometric symmetry, delay tilted

LN10_20 = math.log(10.0) / 20.0


def max_order(response, filter_type):
    return MAX_ORDER_BP if filter_type == "Bandpass" else MAX_ORDER_LP[response]


# =====================================================================================
# Common helpers
# =====================================================================================

def group_delay(poles, w, zeros=()):
    """Group delay of prod(s - z)/prod(s - p) at s = jw (analytic, per root).
    Zeros on the jw axis (notches, origin) contribute nothing."""
    w = np.atleast_1d(np.asarray(w, float))[:, None]
    p = np.asarray(poles, complex)[None, :]
    tau = np.sum(-p.real / (p.real ** 2 + (w - p.imag) ** 2), axis=1)
    z = np.asarray(zeros, complex)
    if z.size:
        z = z[np.abs(z.real) > 0][None, :]
        if z.size:
            tau -= np.sum(-z.real / (z.real ** 2 + (w - z.imag) ** 2), axis=1)
    return tau


def _db(poles, w, zeros=(), w_ref=0.0):
    """20 log10 |H(jw) / H(j w_ref)|, evaluated as sums of logs (no overflow at n = 20).
    Zeros exactly at the origin are skipped when w_ref = 0 (they are handled by the caller)."""
    w = np.atleast_1d(np.asarray(w, float))
    p = np.asarray(poles, complex)
    z = np.asarray(zeros, complex)
    with np.errstate(divide="ignore"):
        v = (np.sum(np.log(np.abs(1j * w_ref - p)))
             - np.sum(np.log(np.abs(1j * w[:, None] - p[None, :])), axis=1))
        if z.size:
            v = v + (np.sum(np.log(np.abs(1j * w[:, None] - z[None, :])), axis=1)
                     - np.sum(np.log(np.abs(1j * w_ref - z))))
    return v / LN10_20


def _first_crossing(poles, atten_db, zeros=(), w_lo=None, grow=8):
    """First frequency (from DC upwards) where the DC-referenced magnitude has fallen by
    atten_db. Robust to magnitude peaking below the crossing (equiripple delay) and to
    notches above it. None if it is not reached within ~1e8 x the pole radius."""
    rmax = float(np.max(np.abs(poles)))
    lo = w_lo if w_lo is not None else 1e-7 * rmax
    hi = 10.0 * rmax
    for _ in range(grow):
        ws = np.geomspace(lo, hi, 4000)
        v = _db(poles, ws, zeros) + atten_db
        i = int(np.argmax(v < 0))
        if v[i] < 0:
            a, b = (ws[i - 1] if i > 0 else 0.0), ws[i]
            for _ in range(100):
                m = 0.5 * (a + b)
                if _db(poles, [m], zeros)[0] + atten_db > 0:
                    a = m
                else:
                    b = m
            return 0.5 * (a + b)
        lo, hi = hi, hi * 100.0
    return None


def section_q_max(poles):
    q = [abs(p) / (-2.0 * p.real) for p in np.asarray(poles, complex) if p.imag > 1e-12 * abs(p)]
    return max(q) if q else 0.5


def _symmetrize(p):
    """Exact conjugate pairs: real poles first, then the upper half, then its mirror."""
    p = np.asarray(p, complex)
    up = sorted([z for z in p if z.imag > 1e-12 * abs(z)], key=lambda z: z.imag)
    re = [complex(z.real, 0.0) for z in p if abs(z.imag) <= 1e-12 * abs(z)]
    return np.array(re + up + [np.conj(z) for z in up], dtype=complex)


# =====================================================================================
# Bessel (Thomson)
# =====================================================================================

def bessel_theta_coeffs(n: int) -> list[int]:
    """Reverse Bessel polynomial theta_n(s) = sum_k a_k s^k, a_k = (2n-k)! / (2^(n-k) k! (n-k)!) (exact)."""
    return [math.factorial(2 * n - k) // (2 ** (n - k) * math.factorial(k) * math.factorial(n - k))
            for k in range(n + 1)]


def bessel_theta_eval(n: int, s):
    """theta_n(s) via theta_k = (2k-1) theta_{k-1} + s^2 theta_{k-2}, theta_0 = 1, theta_1 = s + 1."""
    s = np.asarray(s, complex)
    t0, t1 = np.ones_like(s), s + 1.0
    if n == 0:
        return t0
    for k in range(2, n + 1):
        t0, t1 = t1, (2 * k - 1) * t1 + s * s * t0
    return t1


@functools.lru_cache(maxsize=64)
def _bessel_poles_delay_cached(n):
    _, p, _ = besselap(n, norm="delay")
    return _symmetrize(p)


def bessel_poles_delay(n: int) -> np.ndarray:
    """Poles of theta_n(0)/theta_n(s): tau(0) = 1 s. scipy's besselap (Aberth-Ehrlich) is
    accurate to ~1e-15 far beyond n = 20; np.roots on the integer coefficients loses 1e-9 by n = 15."""
    if n < 1:
        raise ValueError("order must be >= 1")
    return _bessel_poles_delay_cached(int(n)).copy()


def bessel_delay_error(n: int, w_tau):
    """Exact relative delay error 1 - tau(w)/tau(0) of the Bessel LP at w*tau0 = w_tau:
    tau(w)/tau0 = 1 - (w tau0)^(2n) / |theta_n(j w tau0)|^2  (Storch/Thomson identity).
    One-sided (the delay only sags) and monotone in n at fixed w_tau."""
    w_tau = np.asarray(w_tau, float)
    return w_tau ** (2 * n) / np.abs(bessel_theta_eval(n, 1j * w_tau)) ** 2


@functools.lru_cache(maxsize=512)
def bessel_corner_product(n: int, alpha_db: float) -> float:
    """W_alpha(n) = omega_alpha * tau0: the alpha-dB frequency of the delay-normalized Bessel,
    the unique root of |theta_n(jw)|^2 = 10^(alpha/10) theta_n(0)^2 (the magnitude is monotone)."""
    a0 = float(bessel_theta_coeffs(n)[0])
    tgt = alpha_db * LN10_20 + math.log(a0)
    f = lambda w: math.log(abs(complex(bessel_theta_eval(n, 1j * w)))) - tgt
    lo, hi = 0.0, 4.0 * n + 4.0
    while f(hi) < 0:
        hi *= 2.0
    for _ in range(200):
        m = 0.5 * (lo + hi)
        if f(m) < 0:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi)


# =====================================================================================
# Equiripple group delay (all-pole, Chebyshev approximation of constant delay on [0, wp])
# Ported unchanged from dev/fs006/fs006_reference.py (design note section 3).
# =====================================================================================
# Parameters x = [ln s1, ln b1, ..., ln sm, ln bm, (ln s_r)] for poles -s_k +/- j b_k (+ real pole -s_r).
# tau(w) = sum over all poles s/(s^2 + (w - b)^2). Alternation: tau(w_i) - 1 = s_i * delta,
# w_0 = 0 < w_1 < ... < w_n = wp, s_i = -(-1)^(n-i). Unknowns: x (n), delta; interior w_i by exchange.

def _x_to_poles(x, n):
    m = n // 2
    s = np.exp(x[0:2 * m:2]); b = np.exp(x[1:2 * m:2])
    p = list(-s + 1j * b) + list(-s - 1j * b)
    if n % 2:
        p.append(-math.exp(x[2 * m]) + 0j)
    return np.array(p)


def _poles_to_x(p, n):
    up = sorted([z for z in p if z.imag > 1e-12], key=lambda z: z.imag)
    re = [z for z in p if abs(z.imag) <= 1e-12]
    x = []
    for z in up:
        x += [math.log(-z.real), math.log(z.imag)]
    if n % 2:
        x.append(math.log(-re[0].real))
    return np.array(x)


def _tau_derivs(p, w):
    """tau, tau', tau'' at the frequencies w."""
    w = np.atleast_1d(np.asarray(w, float))[:, None]
    s = -p.real[None, :]; u = w - p.imag[None, :]
    d = s * s + u * u
    t = np.sum(s / d, axis=1)
    t1 = np.sum(-2 * s * u / d ** 2, axis=1)
    t2 = np.sum(2 * s * (3 * u * u - s * s) / d ** 3, axis=1)
    return t, t1, t2


def _jac(x, n, w):
    """d tau(w_i) / d x_j (analytic; finite differences cannot resolve delta-sized changes)."""
    w = np.asarray(w, float)
    m = n // 2
    J = np.zeros((len(w), n))
    for k in range(m):
        s, b = math.exp(x[2 * k]), math.exp(x[2 * k + 1])
        u1, u2 = w - b, w + b
        d1, d2 = s * s + u1 * u1, s * s + u2 * u2
        J[:, 2 * k] = s * ((u1 * u1 - s * s) / d1 ** 2 + (u2 * u2 - s * s) / d2 ** 2)
        J[:, 2 * k + 1] = b * (2 * s * u1 / d1 ** 2 - 2 * s * u2 / d2 ** 2)
    if n % 2:
        s = math.exp(x[2 * m]); d = s * s + w * w
        J[:, 2 * m] = s * (w * w - s * s) / d ** 2
    return J


class EqDelayError(RuntimeError):
    pass


def _newton(n, ext, x, d, sgn, iters=60, max_step=0.5):
    """Solve tau(w_i; x) - 1 - s_i d = 0 (n+1 eqs, n+1 unknowns). Plain Newton with a step cap.
    (Residual-norm line search stalls here: the first full step can raise |F| yet converge.)"""
    for _ in range(iters):
        F = _tau_derivs(_x_to_poles(x, n), ext)[0] - 1 - sgn * d
        J = np.hstack([_jac(x, n, ext), -sgn[:, None]])
        step = np.linalg.solve(J, -F)
        smax = float(np.max(np.abs(step[:n])))
        lam = min(1.0, max_step / smax) if smax > 0 else 1.0
        x, d = x + lam * step[:n], d + lam * step[n]
        if smax * lam < 1e-15:
            break
    return x, d


def _move_extrema(x, n, ext):
    """Exchange step: Newton on tau'(w) = 0 from each old interior extremum, kept between the midpoints
    to its neighbours (fallback: golden-section maximization of |E| in that bracket)."""
    p = _x_to_poles(x, n)
    new = [ext[0]]
    for i in range(1, len(ext) - 1):
        lo, hi = 0.5 * (ext[i - 1] + ext[i]), 0.5 * (ext[i] + ext[i + 1])
        w, ok = ext[i], True
        for _ in range(40):
            _, t1, t2 = _tau_derivs(p, [w])
            dw = t1[0] / t2[0]
            w -= dw
            if not lo < w < hi:
                ok = False
                break
            if abs(dw) < 1e-15 * (1 + abs(w)):
                break
        if not ok:
            f = lambda t: -abs(_tau_derivs(p, [t])[0][0] - 1)
            a, b = lo, hi
            g = (math.sqrt(5) - 1) / 2
            for _ in range(100):
                c, e = b - g * (b - a), a + g * (b - a)
                if f(c) < f(e):
                    b = e
                else:
                    a = c
            w = 0.5 * (a + b)
        new.append(w)
    new.append(ext[-1])
    return np.array(new)


def _remez(n, wp, x, ext, iters=60):
    sgn = np.array([-(-1) ** (n - i) for i in range(n + 1)], float)
    d = 0.0
    for _ in range(iters):
        x, d = _newton(n, ext, x, d, sgn)
        new = _move_extrema(x, n, ext)
        shift = float(np.max(np.abs(new - ext)))
        ext = new
        if shift < 1e-13 * wp:
            break
    x, d = _newton(n, ext, x, d, sgn)
    # alternation check on the extremal set
    p = _x_to_poles(x, n)
    e = _tau_derivs(p, ext)[0] - 1
    if d <= 0 or np.any(np.diff(ext) <= 0) or np.max(np.abs(e - sgn * d)) > 1e-7 * d:
        raise EqDelayError("alternation lost")
    return x, d, ext


# One extended-precision solution per order at delta = 1 % (nominal delay 1 s), generated by
# dev/fs006/eqdelay_mp.py. key n -> (x = [ln sigma, ln beta per pair ..., ln sigma_real], wp,
# extremal freqs incl. 0 and wp)
EQDELAY_SEEDS_1PCT = {
    2: ([0.30969519371981558, -0.055041613978871431],
        0.91075989072492547,
        [0, 0.62247800848410106, 0.91075989072492547]),
    3: ([0.40699486138410534, 0.73247725055830626, 0.5911881322319017],
        2.0150645376668845,
        [0, 0.91586601519582111, 1.6795657277066471, 2.0150645376668845]),
    4: ([0.67514006858268505, 0.13627232611935328, 0.44912926308609219, 1.2056260426039167],
        3.2675704984686833,
        [0, 1.08077188776179, 2.0918410313290878, 2.9112896350669883, 3.2675704984686833]),
    5: ([0.71202192216115723, 0.88146823053093826, 0.47175791463747702, 1.5416181315234634, 0.75435841094720191],
        4.5996326520786086,
        [0, 1.1822054941662312, 2.3286260675729991, 3.3838963078908129, 4.2320308048396988, 4.5996326520786086]),
    6: ([0.78922182283860909, 0.23933372123762853, 0.73211235576430367, 1.3230279258820614, 0.48565016026490193, 1.8005476510732381],
        5.9804810734210889,
        [0, 1.2501117633046877, 2.479551082737883, 3.6602102145834983, 4.7405071575116944, 5.6058541651336702, 5.9804810734210889]),
    7: ([0.80827721797004104, 0.96038990916146638, 0.74458404524898492, 1.637189684004557, 0.49496908551213958, 2.0103776267453703, 0.82317131053692849],
        7.394034561348878,
        [0, 1.2983473448311531, 2.5839180745560983, 3.8402284401530098, 5.0416656775404087, 6.1379233401581406, 7.0146574362677603, 7.394034561348878]),
    8: ([0.84173829584628679, 0.29475292465499398, 0.82014510660395101, 1.3863797223394565, 0.75302204511099813, 1.8806412591283852, 0.50162215075591554, 2.1863409655583959],
        8.8309304612201807,
        [0, 1.3343143095621652, 2.6601194055299446, 3.9672529136338692, 5.2407764397596805, 6.4560834569829355, 7.5633520255989568, 8.4481413272280559, 8.8309304612201807]),
    9: ([0.85331289226210705, 1.0046661782757029, 0.82819749800374287, 1.6898662694412263, 0.75908676832493804, 2.0790803691333473, 0.50659539924013797, 2.3376124102519258, 0.86003640650020308],
        10.285273466983497,
        [0, 1.3620561051460409, 2.7182492588928966, 4.0616617127590846, 5.3832186730697291, 6.6686625369468775, 7.893851202965835, 9.0091499882616333, 9.8999216897188269, 10.285273466983497]),
    10: ([0.87144595899679589, 0.32812829988776343, 0.86117404888124005, 1.4230996607266566, 0.76364461701855413, 2.2463701756505445, 0.83399913241318213, 1.9256089955176885, 0.51044614009717793, 2.4701253365028091],
         11.753125758915395,
         [0, 1.384105565729665, 2.7639547338280961, 4.1347912081441063, 5.490410600710776, 6.8222632093042375, 8.1164286162008494, 9.3489984457593813, 10.470400629696485, 11.365782226786658, 11.753125758915395]),
    11: ([0.8668432209524688, 1.7211644609787524, 0.87919770947257114, 1.0323140501956367, 0.76718918041394091, 2.3908441338399968, 0.83836826917771856, 2.1182470781932228, 0.51351167018898791, 2.5879276274196763, 0.88275245315711393],
         13.231733353656889,
         [0, 1.4020071471549218, 2.800872045632921, 4.1930612454015481, 5.5743570703127814, 6.9389172439613382, 8.2784732162501982, 9.5792855672935495, 10.817570006450982, 11.943761367680278, 12.842799315149668, 13.231733353656889]),
    12: ([0.89043495801627237, 0.35013497257227949, 0.87111627662023694, 1.9528436787518668, 0.88479013858995803, 1.4466496055897733, 0.84177207401351561, 2.2810261733013513, 0.77002120700725663, 2.517900317251935, 0.51600746056954061, 2.6938986339806168],
         14.719098170029463,
         [0, 1.4168386514866202, 2.8312621381703669, 4.2406716001688638, 5.6419027317078916, 7.0310294346907671, 8.402410338080962, 9.7479392011240673, 11.053979352083124, 12.296814152419193, 13.426859849561636, 14.328865916562947, 14.719098170029463]),
    13: ([0.88900698653298971, 1.7416486877387984, 0.89597828967798299, 1.0510523115776216, 0.5180773036685814, 2.7901564397163603, 0.77233392610463003, 2.6312332699708518, 0.87444781762121104, 2.1423305539351776, 0.8444957717992313, 2.4218989769441426, 0.89807041660557241],
         16.213726428097438,
         [0, 1.4293048264034494, 2.8567402047627271, 4.2802629091559616, 5.6975691349010624, 7.1057010224069108, 8.5009031874435301, 9.8776533450262729, 11.227944409731998, 12.53820028519419, 13.784741173726257, 14.917953394264572, 15.822415207451551, 16.213726428097438]),
    14: ([0.90358008375909993, 0.36563973123474275, 0.8771155544507816, 2.3025985118951167, 0.90015886660631583, 1.4629450983412751, 0.8467229601553139, 2.5460174148796346, 0.89229593399429452, 1.9709534046448427, 0.77425692567861992, 2.7334811319384427, 0.51982067779547658, 2.8783027173101265],
         17.714473177153749,
         [0, 1.4399370293892095, 2.8783779756972967, 4.3137500055178712, 5.7442184507007639, 7.1676529520881793, 8.5812192780719521, 9.981268167240863, 11.36235158484099, 12.716525709041726, 14.030250977274207, 15.279866859922368, 16.41572531773825, 17.322251507576109, 17.714473177153749]),
    15: ([0.90342013668014642, 1.7560534486454495, 0.77588024204118933, 2.8265923446643, 0.84857697775015417, 2.6569104351667727, 0.90773560131946052, 1.0645341499148258, 0.5213085142592524, 2.9595769100033178, 0.87929835375002285, 2.4414254715495725, 0.89493049292978177, 2.1585497070199038, 0.9090654732768958],
         19.22044217366297,
         [0, 1.4490990824752683, 2.8969995711402294, 4.3424149514682835, 5.7839469988985099, 7.2198863711766723, 8.6482110729404322, 10.06616493987045, 11.470167864291779, 12.854819968274757, 14.212219179479234, 15.528848150264604, 16.781055276742602, 17.919157184766849, 18.82744228054818, 19.22044217366297]),
}

_SEEDS = {(n, 0.01): (np.array(x, float), float(wp), np.array(ext, float))
          for n, (x, wp, ext) in EQDELAY_SEEDS_1PCT.items()}


def _eqdelay_solve(n, delta, seeds=_SEEDS, max_ratio=1.15, tol=1e-10, max_steps=100):
    """Poles (nominal delay 1 s) and band edge wp of the order-n all-pole LP whose group delay stays in
    [1 - delta, 1 + delta] on 0 <= w <= wp with n+1 equal-ripple extrema. Double precision, warm-started
    from the nearest seed (same n) and continued in wp; a secant on ln(wp) vs ln(delta) hits delta."""
    if n == 1:
        s = 1 / (1 + delta)
        return np.array([-s + 0j]), math.sqrt(s / (1 - delta) - s * s)
    cands = [(abs(math.log(k[1] / delta)), k) for k in seeds if k[0] == n]
    if not cands:
        raise EqDelayError(f"no seed for n={n}")
    key = min(cands)[1]
    x, wp, ext = seeds[key][0].copy(), seeds[key][1], seeds[key][2].copy()
    x, d, ext = _remez(n, wp, x, ext)
    hist = [(wp, d, x, ext)]
    if abs(d / delta - 1) < tol:
        return _x_to_poles(x, n), wp
    up = d < delta
    steps = 0
    while (d < delta) == up:
        steps += 1
        if steps > max_steps:
            raise EqDelayError("continuation: too many steps (target delta too far from the seeds?)")
        wl, dl, xl, el = hist[-1]
        r = (delta / dl) ** (1.0 / (2 * n))
        r = min(r, max_ratio) if up else max(r, 1 / max_ratio)
        for _ in range(12):
            try:
                xn, dn, en = _remez(n, wl * r, xl, el * r)
                if (dn - dl) * (r - 1) < 0:  # delta(wp) must be increasing
                    raise EqDelayError("non-monotone step")
                break
            except (EqDelayError, np.linalg.LinAlgError, FloatingPointError):
                r = math.sqrt(r)
        else:
            raise EqDelayError("continuation failed")
        hist.append((wl * r, dn, xn, en))
        d = dn
        if abs(d / delta - 1) < tol:
            return _x_to_poles(xn, n), wl * r
    (w0, d0, _, _), (w1, d1, x1, e1) = hist[-2], hist[-1]
    for _ in range(60):
        lw = math.log(w0) + (math.log(delta) - math.log(d0)) * (math.log(w1) - math.log(w0)) / (math.log(d1) - math.log(d0))
        wn = math.exp(lw)
        xn, dn, en = _remez(n, wn, x1, e1 * (wn / w1))
        if abs(dn / delta - 1) < tol:
            return _x_to_poles(xn, n), wn
        (w0, d0), (w1, d1, x1, e1) = (w1, d1), (wn, dn, xn, en)
    raise EqDelayError("secant did not converge")


@functools.lru_cache(maxsize=128)
def _eqdelay_cached(n, delta):
    p, wp = _eqdelay_solve(n, delta)
    return _symmetrize(p), float(wp)


def eqdelay_poles(n: int, delta: float):
    """(poles, wp): delay-normalized equiripple-delay poles (nominal delay 1 s, ripple +/-delta on
    0 <= w <= wp). Cached per (n, delta); 0.02-0.3 s per new pair for n <= 15."""
    if n < 1:
        raise ValueError("order must be >= 1")
    if not (DELTA_MIN * 0.999 <= delta <= 0.2):
        raise ValueError(f"delay ripple {delta*100:g} % is outside the solver range "
                         f"{DELTA_MIN*100:g} % ... 20 %")
    try:
        p, wp = _eqdelay_cached(int(n), round(float(delta), 12))
    except (EqDelayError, np.linalg.LinAlgError) as e:
        raise EqDelayError(f"equiripple-delay solver did not converge for n = {n}, "
                           f"delta = {delta*100:g} % ({e})") from e
    return p.copy(), wp


# =====================================================================================
# Normalization: delay <-> corner at alpha dB
# =====================================================================================

def prototype_delay(response, n, delta=0.01):
    """Delay-normalized poles (nominal delay 1 s)."""
    if response == BESSEL:
        return bessel_poles_delay(n)
    if response == EQDELAY:
        return eqdelay_poles(n, delta)[0]
    raise ValueError(f"not a delay response: {response}")


def corner_product(response, n, alpha_db, delta=0.01):
    """W_alpha = omega_alpha * tau_nom. For Equiripple Delay the FIRST -alpha crossing from DC
    is used, because the magnitude can peak slightly (up to ~0.5 dB at delta = 10 %)."""
    if response == BESSEL:
        return bessel_corner_product(int(n), float(alpha_db))
    return _eqdelay_corner(int(n), round(float(delta), 12), float(alpha_db))


@functools.lru_cache(maxsize=512)
def _eqdelay_corner(n, delta, alpha_db):
    return _first_crossing(eqdelay_poles(n, delta)[0], alpha_db)


def prototype_corner(response, n, alpha_db, delta=0.01):
    """Corner-normalized poles: |H(j1)| = -alpha dB (the convention of every LP solver)."""
    return prototype_delay(response, n, delta) / corner_product(response, n, alpha_db, delta)


def tau_dc_factor(response, n, delta=0.01):
    """tau(0) / tau_nom: 1 for Bessel; 1 + delta (odd n) or 1 - delta (even n) for equiripple."""
    if response == BESSEL:
        return 1.0
    return 1.0 + delta if n % 2 else 1.0 - delta


# =====================================================================================
# LP design with optional stopband notches (engine drop-in)
# =====================================================================================

def _corner_hold_scale(response, n, alpha_db, delta, notches):
    """Pole scale that keeps -alpha_db at omega = 1 with jw notches at `notches` (all > 1).
    The notches take -20 log10 prod(1 - 1/wz^2) of the corner attenuation, the poles the rest
    alpha_b, so scale = W_alpha / W_alpha_b. None when alpha_b <= 0 (the notches alone already
    take alpha at the corner). alpha_b varies continuously with the notches, so it bypasses the
    lru_caches: the FS-021 solver loop would only flood them."""
    n1 = float(np.prod([1.0 - 1.0 / wz ** 2 for wz in notches]))
    alpha_b = alpha_db + 20.0 * math.log10(n1) if n1 > 0 else -1.0
    if alpha_b <= 1e-6:
        return None
    if response == BESSEL:
        w_b = bessel_corner_product.__wrapped__(int(n), float(alpha_b))
    else:
        w_b = _first_crossing(eqdelay_poles(n, delta)[0], alpha_b)
    return corner_product(response, n, alpha_db, delta) / w_b


def _stopband_humps(poles, notches):
    """DC-referenced dB level of every stopband maximum of an LP with jw notches at `notches`
    (ascending, corner-normalized): one between each pair of notches and one above the last.
    With 2m = n the response above the last notch tends to the HF floor prod|p| / prod wz^2,
    which counts as that last maximum when no interior peak is higher."""
    wz = np.asarray(notches, float)
    p = np.asarray(poles, complex)
    zeros = np.concatenate([1j * wz, -1j * wz])
    f = lambda lw: _db(p, np.exp(lw), zeros)

    def peak(a, b, n_grid):
        lw = np.linspace(math.log(a), math.log(b), n_grid)[1:-1]
        v = f(lw)
        i = int(np.argmax(v))
        if i == len(lw) - 1:
            return float(v[i]), True          # still rising at the top of the range
        lo, hi = lw[max(i - 1, 0)], lw[i + 1]
        r = minimize_scalar(lambda x: -f([x])[0], bounds=(lo, hi), method="bounded",
                            options={"xatol": 1e-12})
        return max(float(-r.fun), float(v[i])), False

    humps = [peak(wz[k], wz[k + 1], 66)[0] for k in range(len(wz) - 1)]
    top, rising = peak(wz[-1], wz[-1] * 1e3, 402)
    if 2 * len(wz) == len(p):
        floor = (np.sum(np.log(np.abs(p))) - 2.0 * np.sum(np.log(wz))) / LN10_20
        top = floor if rising else max(top, floor)
    humps.append(top)
    return np.array(humps)


def solve_delay_stopband_notches(response, order, alpha_max, as_db, m, delta=0.01):
    """FS-021: m jw notch frequencies (corner-normalized, ascending, > 1) that put every stopband
    maximum at exactly -as_db, with the poles scaled at every step so that -alpha_max stays at
    omega = 1 (the corner hold of design_delay_lp). A_s is the input and the stopband edge
    follows, as in solve_inv_chebyshev_lp. The delay keeps the prototype shape (jw zeros add no
    delay); tau shrinks by the returned pole scale.

    Least squares on ordered gaps (w1 = 1 + e^y1, wk = w(k-1) + e^yk) from a corner-feasible
    geometric start; one retry further out; never raises on non-convergence (the best iterate
    is returned with converged = False).
    Returns (notches, info) with info = {m, converged, max_err_db, pole_scale}.
    """
    m = int(min(max(int(m), 0), order // 2))
    if m == 0:
        return np.array([]), {"m": 0, "converged": True, "max_err_db": 0.0, "pole_scale": 1.0}
    pc = prototype_corner(response, order, alpha_max, delta)

    def omegas(y):
        return 1.0 + np.cumsum(np.exp(y))

    def resid(y):
        wz = omegas(y)
        a = _corner_hold_scale(response, order, alpha_max, delta, wz)
        if a is None or not np.isfinite(a):
            return np.full(m, 1e3)
        return _stopband_humps(pc * a, wz) + as_db

    base = 3.0 * 1.7 ** np.arange(m)
    lim = 10.0 ** (-alpha_max / 40.0)          # half of alpha for the notches' droop at f_c
    c = 1.0
    while np.prod(1.0 - 1.0 / (c * base) ** 2) < lim:
        c *= 1.25
    best_y, best_err = None, np.inf
    for start in (c * base, 1.4 * c * base):
        y0 = np.log(np.diff(np.concatenate([[1.0], start])))
        sol = least_squares(resid, y0, x_scale="jac", ftol=1e-14, xtol=1e-14, gtol=1e-14,
                            max_nfev=100 * (m + 1))
        err = float(np.max(np.abs(sol.fun)))
        if err < best_err:
            best_y, best_err = sol.x, err
        if err < 1e-8:
            break
    wz = omegas(best_y)
    a = _corner_hold_scale(response, order, alpha_max, delta, wz)
    return wz, {"m": m, "converged": bool(best_err < 1e-8), "max_err_db": best_err,
                "pole_scale": float(a) if a else 1.0}


def design_delay_lp(response, order, alpha_max, as_db, delta=0.01, notches=(), hold_corner=True,
                    ems_m=0):
    """Corner-normalized LP (-alpha_max at omega = 1) with optional jw-axis notches.

    Poles come from the delay spec only and are never re-synthesized (design note section 5):
    a jw zero pair adds no group delay at all, so the delay stays maximally flat / equiripple.
    Notches at or below the corner are rejected. With hold_corner, all poles are scaled by one
    factor so that -alpha_max stays at omega = 1 (the delay SHAPE is kept; tau shrinks by that
    factor); without it the poles are untouched (tau exact) and the corner moves.

    ems_m > 0 (FS-021, equiripple magnitude stopband): `notches` is ignored and ems_m notches are
    solved so every stopband hump sits at -as_db (solve_delay_stopband_notches). Needs
    hold_corner; without it the mode is ignored with a warning.

    Returns (tzeros, poles, omega_s, notes) with notes = {pole_scale, corner_w, warnings, ems};
    ems is the solver info (None when the mode is off).
    """
    p = prototype_corner(response, order, alpha_max, delta)
    warnings = []
    ems = None
    if ems_m and ems_m > 0:
        if hold_corner:
            notches, ems = solve_delay_stopband_notches(response, order, alpha_max, as_db,
                                                        ems_m, delta)
            notches = list(notches)
            if not ems["converged"]:
                warnings.append(f"Equiripple stopband: the notch solve did not converge (humps "
                                f"within {ems['max_err_db']:.2g} dB of −A_s).")
        else:
            warnings.append("Equiripple magnitude stopband needs the corner anchor (it holds "
                            "f_c while placing the notches); ignored.")
            notches = ()
    kept, rejected = [], []
    for wz in sorted(float(v) for v in notches):
        (kept if wz > 1.0 + 1e-9 else rejected).append(wz)
    if rejected:
        warnings.append("Notches at or inside the passband were ignored: they cannot keep the "
                        "delay flat (a passband notch adds a delay bump of area π).")
    kept = kept[: order // 2]

    scale = 1.0
    if kept:
        if hold_corner:
            s = _corner_hold_scale(response, order, alpha_max, delta, kept)
            if s is not None:
                scale = s
                p = p * scale
            else:
                warnings.append("The corner cannot be held at f_c: the notches are too close to "
                                "it (needs ∏(1 − (f_c/f_z)²) > 10^(−α/20)). Poles left unscaled; "
                                "the corner moves down.")
    zeros = np.array([s * 1j * wz for wz in kept for s in (1, -1)], dtype=complex)
    corner_w = _first_crossing(p, alpha_max, zeros)
    omega_s = _first_crossing(p, as_db, zeros)
    return zeros, p, omega_s, {"pole_scale": scale, "corner_w": corner_w, "warnings": warnings,
                               "ems": ems}


# =====================================================================================
# Bandpass: pole translation (delay-preserving) or classic reactance transform
# =====================================================================================

def bp_fold_limit(response, n, alpha_db, delta=0.01):
    """Largest fractional bandwidth b = B/f0 for which pole translation keeps every upper pole
    above the real axis (b < 2 / max|Im p|, p corner-normalized), with a 5 % margin."""
    p = prototype_corner(response, n, alpha_db, delta)
    return 0.95 * 2.0 / float(np.max(np.abs(p.imag))) if np.any(p.imag != 0) else float("inf")


def _translate(pc, w0, B, n_z):
    up = 0.5 * B * pc + 1j * w0
    return np.concatenate([up, np.conj(up)]), np.zeros(n_z, dtype=complex)


def _rel_db(poles, zeros, w, w_ref):
    """20 log10 |H(jw)/H(j w_ref)| for BP roots (origin zeros included, w_ref > 0)."""
    w = np.atleast_1d(np.asarray(w, float))
    p = np.asarray(poles, complex)
    z = np.asarray(zeros, complex)
    num = (np.sum(np.log(np.abs(1j * w[:, None] - z[None, :])), axis=1) if z.size else 0.0)
    num_ref = np.sum(np.log(np.abs(1j * w_ref - z))) if z.size else 0.0
    den = np.sum(np.log(np.abs(1j * w[:, None] - p[None, :])), axis=1)
    den_ref = np.sum(np.log(np.abs(1j * w_ref - p)))
    return ((num - num_ref) - (den - den_ref)) / LN10_20


def synthesize_delay_bp(response, n, wp1, wp2, alpha_max, delta=0.01, mapping=BP_TRANSLATION):
    """Bandpass from the order-n corner-normalized prototype, in the engine's geometric-
    normalized domain (wp1 * wp2 = 1). The gain is set to unity at the image of the LP's DC
    (the band centre), matching the LP convention.

    translation: p' = (B/2) p + j w0 (+ conjugates), w0 = (wp1 + wp2)/2, B = wp2 - wp1. The LP delay
        shape is translated to w0 (+ a small mirror term) -- delay-preserving up to b ~ 0.2-0.3.
        n_z = n/2 origin zeros make the passband arithmetically symmetric (odd n: whichever of
        (n +/- 1)/2 is more symmetric, at least 1). A 2x2 Newton on (w0, B) then puts both
        edges at exactly -alpha.
    classic: the standard reactance transform (geometric symmetry, n origin zeros); the delay
        is tilted across the band by ~ b * Omega_d peak-to-peak.

    Returns (poles, zeros, k, None, None, notes); the engine's compliance scanner sets the
    stopband markers.
    """
    pc = prototype_corner(response, n, alpha_max, delta)
    notes = {"mapping": mapping, "tuned": None, "n_origin_zeros": None, "center_w": None}

    if mapping == BP_CLASSIC:
        poles, zeros, _ = _transform_lp_to_bp(pc, [], [], wp1, wp2)
        w_c = math.sqrt(wp1 * wp2)
        notes.update(n_origin_zeros=len(zeros), center_w=w_c)
    else:
        w0, B = 0.5 * (wp1 + wp2), wp2 - wp1
        if B / w0 >= bp_fold_limit(response, n, alpha_max, delta):
            raise ValueError("Band too wide for the delay-preserving bandpass mapping "
                             f"(fractional bandwidth {B / w0:.3g}).")
        if n % 2 == 0:
            cands = [n // 2]
        else:
            cands = [c for c in ((n - 1) // 2, (n + 1) // 2) if c >= 1]

        def asym(nz):
            po, ze = _translate(pc, w0, B, nz)
            e = _rel_db(po, ze, [wp1, wp2], w0)
            return abs(e[0] - e[1])
        n_z = min(cands, key=asym)

        # Tune (w0, B) so both edges sit at -alpha relative to the centre.
        def resid(v):
            po, ze = _translate(pc, v[0], v[1], n_z)
            return _rel_db(po, ze, [wp1, wp2], v[0]) + alpha_max

        v = np.array([w0, B])
        tuned = False
        try:
            for _ in range(30):
                F = resid(v)
                if np.max(np.abs(F)) < 1e-10:
                    tuned = True
                    break
                J = np.empty((2, 2))
                for j in range(2):
                    h = 1e-7 * v[j]
                    dv = np.zeros(2); dv[j] = h
                    J[:, j] = (resid(v + dv) - F) / h
                v = v - np.linalg.solve(J, F)
                if v[1] <= 0 or v[0] <= 0:
                    break
        except np.linalg.LinAlgError:
            tuned = False
        if tuned and v[1] / v[0] < bp_fold_limit(response, n, alpha_max, delta):
            w0, B = float(v[0]), float(v[1])
        else:
            tuned = False
        poles, zeros = _translate(pc, w0, B, n_z)
        w_c = w0
        notes.update(tuned=tuned, n_origin_zeros=n_z, center_w=w0)

    num = np.prod(1j * w_c - zeros) if len(zeros) else 1.0
    den = np.prod(1j * w_c - poles)
    k = float(1.0 / abs(num / den))
    return poles, zeros, k, None, None, notes


# =====================================================================================
# delay_info: the summary Tier D shows (engine_results["delay_info"])
# =====================================================================================

def _band_edge(poles, zeros, w_start, w_end, lo, hi, n_grid=4000):
    """First frequency moving from w_start towards w_end where tau leaves [lo, hi]."""
    ws = np.linspace(w_start, w_end, n_grid)
    t = group_delay(poles, ws, zeros)
    out = (t < lo) | (t > hi)
    if not np.any(out):
        return None
    i = int(np.argmax(out))
    if i == 0:
        return w_start
    a, b = ws[i - 1], ws[i]
    for _ in range(80):
        m = 0.5 * (a + b)
        tm = group_delay(poles, [m], zeros)[0]
        if lo <= tm <= hi:
            a = m
        else:
            b = m
    return 0.5 * (a + b)


def make_delay_info(response, kind, order, alpha_db, delta, poles, zeros, *,
                    corner_hz=None, f1_hz=None, f2_hz=None, center_w=None,
                    mapping=None, eps_ref=0.01, notes=None):
    """Delay summary from the PHYSICAL roots (rad/s). kind: 'LP' | 'BP' (a highpass or
    band-reject cannot have a flat delay, so the delay responses do not offer them).
    Times are in seconds, frequencies in Hz."""
    two_pi = 2.0 * math.pi
    poles = np.asarray(poles, complex)
    zeros = np.asarray(zeros, complex)
    notes = notes or {}
    info = {
        "response": response, "kind": kind, "order": int(order),
        "alpha_db": float(alpha_db),
        "delta": float(delta) if response == EQDELAY else None,
        "eps_ref": float(eps_ref) if response == BESSEL else None,
        "max_q": float(section_q_max(poles)),
        "bp_mapping": mapping, "n_origin_zeros": notes.get("n_origin_zeros"),
        "pole_scale": notes.get("pole_scale", 1.0),
        "ems": notes.get("ems"),
        "warnings": list(notes.get("warnings", [])),
        "tau_dc_s": None, "tau_nom_s": None, "tau_center_s": None,
        "w_prod": None, "corner_hz": corner_hz, "center_hz": None,
        "flat_band_hz": None, "delay_pp_pct": None,
    }
    tol_lo, tol_hi = ((1.0 - delta, 1.0 + delta) if response == EQDELAY else (1.0 - eps_ref, 1.0))
    fac = tau_dc_factor(response, order, delta)

    if kind == "LP":
        t0 = float(group_delay(poles, [0.0], zeros)[0])
        tn = t0 / fac
        info.update(tau_dc_s=t0, tau_nom_s=tn)
        if corner_hz:
            info["w_prod"] = two_pi * corner_hz * tn
        w_hi = 2.5 * float(np.max(np.abs(poles)))
        edge = _band_edge(poles, zeros, 0.0, w_hi, tn * tol_lo, tn * tol_hi * (1 + 1e-7))
        info["flat_band_hz"] = (0.0, edge / two_pi) if edge else None
    else:  # BP
        wc = center_w if center_w is not None else two_pi * math.sqrt(f1_hz * f2_hz)
        tc = float(group_delay(poles, [wc], zeros)[0])
        tn = tc / fac
        info.update(tau_center_s=tc, tau_nom_s=tn, center_hz=wc / two_pi)
        # BP Bessel: symmetric tolerance, the mirror term can lift one side by a hair
        lo, hi = ((tn * (1 - delta), tn * (1 + delta) * (1 + 1e-7)) if response == EQDELAY
                  else (tc * (1 - eps_ref), tc * (1 + eps_ref)))
        w1, w2 = two_pi * f1_hz, two_pi * f2_hz
        span = 2.0 * (w2 - w1)
        e_hi = _band_edge(poles, zeros, wc, wc + span, lo, hi)
        e_lo = _band_edge(poles, zeros, wc, max(wc - span, 1e-9 * wc), lo, hi)
        if e_hi and e_lo:
            info["flat_band_hz"] = (e_lo / two_pi, e_hi / two_pi)
        t = group_delay(poles, np.linspace(w1, w2, 2001), zeros)
        info["delay_pp_pct"] = float(100.0 * (t.max() - t.min()) / tc)
        b = (w2 - w1) / wc
        if mapping == BP_CLASSIC:
            info["warnings"].append(
                f"Classic (geometric) mapping: the group delay is tilted across the band "
                f"({info['delay_pp_pct']:.2f} % peak-to-peak over f1…f2).")
        elif b > 0.3:
            info["warnings"].append(
                f"Wide band (B/f0 = {b:.2f}): the delay-preserving mapping degrades above ~0.3 "
                f"({info['delay_pp_pct']:.2f} % peak-to-peak over f1…f2).")
        if mapping == BP_TRANSLATION and notes.get("tuned") is False:
            info["warnings"].append("Band-edge tuning did not converge; edges are within a few "
                                    "hundredths of a dB of −α.")
    return info


# =====================================================================================
# Order selection from specs
# =====================================================================================

def _order_data(response, n, alpha_db, delta):
    """(delay-normalized poles, W_alpha, delay-normalized equiripple band edge or None)."""
    if response == BESSEL:
        return bessel_poles_delay(n), bessel_corner_product(n, alpha_db), None
    p, wp = eqdelay_poles(n, delta)
    return p, corner_product(response, n, alpha_db, delta), wp


def _bp_atten(response, n, alpha_db, delta, f1, f2, fs, mapping):
    """Attenuation (dB, positive) of the built BP at fs, relative to the band centre."""
    w0g = math.sqrt(f1 * f2)
    poles, zeros, _, _, _, notes = synthesize_delay_bp(response, n, f1 / w0g, f2 / w0g,
                                                       alpha_db, delta, mapping)
    return -float(_rel_db(poles, zeros, [fs / w0g], notes["center_w"])[0])


def select_delay_order(response, kind, alpha_db, delta, anchor, fc_hz, tau0_s, crit,
                       n_max, f1_hz=None, f2_hz=None, mapping=BP_TRANSLATION):
    """Choose the order from specs.

    kind: 'LP' | 'BP'. anchor: 'corner' (fc_hz exact) or 'delay' (tau0_s exact, LP only;
    tau0 is the nominal delay -- tau(0) for Bessel, the ripple-band centre for equiripple).
    crit (None / missing = inactive):
        tau_max_s        ceiling  (LP, corner anchor)   tau_nom <= tau_max   (latency budget)
        f_min_hz         floor    (LP, delay anchor)    corner >= f_min      (delay line)
        fd_hz, eps       floor    (LP)                  delay within tolerance up to f_d
                                                        (Bessel: error <= eps; equiripple: +/-delta band)
        fs_hz, as_db     floor    (LP/BP)               attenuation >= A_s at f_s

    Rule: with any floor active, the smallest n meeting everything; with only ceilings, the
    largest. If nothing meets everything: the n with the fewest failures, then the smallest
    total normalized shortfall. Returns a dict {n, feasible, fc_hz, tau_nom_s, rows, message}
    where rows = [(label, achieved, target, ok, unit)] at the chosen n (unit: "s", "Hz",
    "frac" or "dB"); n is None when no criterion
    is active.
    """
    crit = {k: v for k, v in (crit or {}).items() if v is not None}
    two_pi = 2.0 * math.pi
    floors, ceilings = [], []
    if kind == "LP":
        if anchor == "corner" and "tau_max_s" in crit:
            ceilings.append("tau_max")
        if anchor == "delay" and "f_min_hz" in crit:
            floors.append("f_min")
        if "fd_hz" in crit:
            floors.append("flat")
    if "fs_hz" in crit and "as_db" in crit:
        floors.append("stop")
    if not floors and not ceilings:
        return {"n": None, "feasible": False, "fc_hz": fc_hz, "tau_nom_s": tau0_s, "rows": [],
                "message": "Tick at least one order criterion."}

    def evaluate(n):
        rows, margins = [], []
        fc, tn = fc_hz, None
        if kind == "LP":
            p, W, wp = _order_data(response, n, alpha_db, delta)
            if anchor == "delay":
                tn = tau0_s
                fc = W / (two_pi * tn)
            else:
                tn = W / (two_pi * fc_hz)
        if "tau_max" in ceilings:
            t = crit["tau_max_s"]
            margins.append((t - tn) / t)
            rows.append(("Group delay τ₀ ≤", tn, t, tn <= t * (1 + 1e-12), "s"))
        if "f_min" in floors:
            f = crit["f_min_hz"]
            margins.append((fc - f) / f)
            rows.append(("Corner f_c ≥", fc, f, fc >= f * (1 - 1e-12), "Hz"))
        if "flat" in floors:
            fd = crit["fd_hz"]
            if response == BESSEL:
                eps = crit.get("eps", 0.01)
                err = float(bessel_delay_error(n, two_pi * fd * tn))
                margins.append((eps - err) / eps)
                rows.append(("Delay error at f_d ≤", err, eps, err <= eps, "frac"))
            else:
                fp = wp / (two_pi * tn)
                margins.append((fp - fd) / fd)
                rows.append(("±δ delay band edge ≥", fp, fd, fp >= fd, "Hz"))
        if "stop" in floors:
            fs, As = crit["fs_hz"], crit["as_db"]
            if kind == "LP":
                A = -float(_db(p, [two_pi * fs * tn])[0])
            else:
                try:
                    A = _bp_atten(response, n, alpha_db, delta, f1_hz, f2_hz, fs, mapping)
                except ValueError:      # band too wide for translation at this order
                    A = 0.0
            margins.append((A - As) / As)
            rows.append(("Attenuation at f_s ≥", A, As, A >= As, "dB"))
        ok = all(r[3] for r in rows)
        shortfall = sum(-min(m, 0.0) for m in margins)
        fails = sum(not r[3] for r in rows)
        return {"n": n, "ok": ok, "fc_hz": fc, "tau_nom_s": tn, "rows": rows,
                "fails": fails, "shortfall": shortfall}

    evals = []
    chosen = None
    if floors:
        for n in range(1, n_max + 1):
            e = evaluate(n)
            evals.append(e)
            if e["ok"]:
                chosen = e
                break
    else:  # ceiling only: tau_nom grows with n, so scan up while it holds
        for n in range(1, n_max + 1):
            e = evaluate(n)
            evals.append(e)
            if e["ok"]:
                chosen = e
            else:
                break
    feasible = chosen is not None
    if not feasible:
        chosen = min(evals, key=lambda e: (e["fails"], e["shortfall"], e["n"]))

    n = chosen["n"]
    if feasible:
        msg = "meets the spec"
        if not floors:
            msg += " (largest order within the delay budget)"
    else:
        msg = f"no order ≤ {n_max} meets the spec; this is the closest"
    return {"n": n, "feasible": feasible, "fc_hz": chosen["fc_hz"],
            "tau_nom_s": chosen["tau_nom_s"], "rows": chosen["rows"], "message": msg}
