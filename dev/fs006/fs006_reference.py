# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-006 reference implementation (numpy only).

Bessel (maximally flat group delay) and equiripple-group-delay all-pole low-pass prototypes,
normalization helpers, order selection, and self-tests. Frequencies are angular (rad/s) in the
prototype domain. "Delay-normalized" means tau(0) = 1 s (Bessel) or nominal delay = 1 s (equiripple).
"-3 dB normalized" means |H(j1)| = 1/sqrt(2).

This file is a verified reference for porting into filter_solvers.py; it is not tied to the project API.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass

import numpy as np

LN2_DB = 10.0 * math.log10(2.0)  # 3.0103 dB

# =====================================================================================
# Common all-pole helpers
# =====================================================================================

def group_delay(poles, w, zeros=()):
    """Group delay of prod(s - z)/prod(s - p) at s = jw.
    Pole -sigma + j beta contributes sigma / (sigma^2 + (w - beta)^2).
    Zero at a + j b contributes -(-a) / (a^2 + (w - b)^2); zeros with a == 0 (jw axis, origin) contribute 0
    (apart from a pi phase jump exactly at the zero, where |H| = 0)."""
    w = np.atleast_1d(np.asarray(w, float))[:, None]
    p = np.asarray(poles, complex)[None, :]
    tau = np.sum(-p.real / (p.real ** 2 + (w - p.imag) ** 2), axis=1)
    z = np.asarray(zeros, complex)
    if z.size:
        z = z[np.abs(z.real) > 0][None, :]
        if z.size:
            tau -= np.sum(-z.real / (z.real ** 2 + (w - z.imag) ** 2), axis=1)
    return tau


def mag_db(poles, w, zeros=(), gain=None):
    """|H(jw)| in dB; default gain gives unity at DC for all-pole / zeros away from the origin."""
    w = np.atleast_1d(np.asarray(w, float))
    s = 1j * w[:, None]
    p = np.asarray(poles, complex)
    z = np.asarray(zeros, complex)
    h = 1.0 / np.prod(s - p[None, :], axis=1)
    if z.size:
        h = h * np.prod(s - z[None, :], axis=1)
    if gain is None:
        gain = abs(np.prod(-p) / (np.prod(-z) if z.size else 1.0))
    return 20 * np.log10(np.abs(gain * h))


def w3db(poles, lo=1e-9, hi=None):
    """-3.0103 dB frequency of a monotone-ish LP (bisection on the first crossing from DC)."""
    if hi is None:
        hi = 10 * max(abs(p) for p in poles)
    f = lambda w: mag_db(poles, [w])[0] + LN2_DB
    # march to bracket the FIRST crossing
    ws = np.geomspace(max(lo, 1e-6 * hi), hi, 4000)
    v = mag_db(poles, ws) + LN2_DB
    i = int(np.argmax(v < 0))
    a, b = (ws[i - 1] if i > 0 else lo), ws[i]
    for _ in range(200):
        m = 0.5 * (a + b)
        if f(m) > 0:
            a = m
        else:
            b = m
    return 0.5 * (a + b)


def sections(poles):
    """(w0, Q) per second-order section (Q=None for a real pole), sorted by ascending Q."""
    out = []
    for p in poles:
        if abs(p.imag) < 1e-12 * abs(p):
            out.append((abs(p.real), None))
        elif p.imag > 0:
            out.append((abs(p), abs(p) / (-2 * p.real)))
    return sorted(out, key=lambda t: (t[1] or 0.0))


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


def bessel_poles_delay(n: int) -> np.ndarray:
    """Poles of theta_n(0)/theta_n(s): tau(0) = 1 s. np.roots on exact integer coefficients.
    Accuracy (vs 60-digit reference): <= 3e-12 relative for n <= 10, 2.6e-11 at n = 12, 2.6e-9 at n = 15.
    For n > 15 use scipy.signal.besselap(n, norm='delay') (Aberth iteration, ~1e-15 up to n = 30+)."""
    if n < 1:
        raise ValueError("order must be >= 1")
    a = bessel_theta_coeffs(n)
    r = np.roots([float(c) for c in reversed(a)])
    # symmetrize conjugate pairs exactly
    up = sorted([z for z in r if z.imag > 1e-12], key=lambda z: z.imag)
    re = [complex(z.real, 0.0) for z in r if abs(z.imag) <= 1e-12]
    return np.array(re + up + [np.conj(z) for z in up])


def bessel_delay_error(n: int, w_tau):
    """Exact relative delay error 1 - tau(w)/tau(0) of the Bessel LP at w*tau0 = w_tau:
    tau(w)/tau0 = 1 - (w tau0)^(2n) / |theta_n(j w tau0)|^2  (Storch/Thomson identity)."""
    w_tau = np.asarray(w_tau, float)
    return w_tau ** (2 * n) / np.abs(bessel_theta_eval(n, 1j * w_tau)) ** 2


def bessel_w3db_delay(n: int) -> float:
    """w_3dB * tau0 of the delay-normalized Bessel: root of |theta_n(jw)|^2 = 2 theta_n(0)^2 (monotone)."""
    a0 = float(bessel_theta_coeffs(n)[0])
    f = lambda w: math.log(abs(complex(bessel_theta_eval(n, 1j * w)))) - math.log(math.sqrt(2) * a0)
    lo, hi = 1e-9, 4.0 * n + 4.0
    for _ in range(200):
        m = 0.5 * (lo + hi)
        if f(m) < 0:
            lo = m
        else:
            hi = m
    return 0.5 * (lo + hi)


def bessel_poles(n: int, norm: str = "mag") -> np.ndarray:
    """norm='delay': tau(0) = 1;  norm='mag': -3 dB at w = 1;  norm='phase': asymptote matches Butterworth
    (poles / a0^(1/n)), i.e. scipy's default."""
    p = bessel_poles_delay(n)
    if norm == "delay":
        return p
    if norm == "mag":
        return p / bessel_w3db_delay(n)
    if norm == "phase":
        return p / float(bessel_theta_coeffs(n)[0]) ** (1.0 / n)
    raise ValueError(norm)


def bessel_atten_db(n: int, Omega) -> np.ndarray:
    """Attenuation (positive dB) of the -3 dB normalized Bessel at Omega = f/fc."""
    Omega = np.asarray(Omega, float)
    w3 = bessel_w3db_delay(n)
    a0 = float(bessel_theta_coeffs(n)[0])
    return 20 * np.log10(np.abs(bessel_theta_eval(n, 1j * Omega * w3)) / a0)


# =====================================================================================
# Equiripple group delay (all-pole, Chebyshev approximation of constant delay on [0, wp])
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
    """d tau(w_i) / d x_j (analytic)."""
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
    # alternation check on the extremal set and on a dense grid
    p = _x_to_poles(x, n)
    e = _tau_derivs(p, ext)[0] - 1
    if d <= 0 or np.any(np.diff(ext) <= 0) or np.max(np.abs(e - sgn * d)) > 1e-7 * d:
        raise EqDelayError("alternation lost")
    return x, d, ext


# One extended-precision solution per order at delta = 1 % (nominal delay 1 s), generated by eqdelay_mp.py.
# key n -> (x = [ln sigma, ln beta per pair ..., ln sigma_real], wp, interior+edge extremal freqs)
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


def _default_seeds():
    return {(n, 0.01): (np.array(x, float), float(wp), np.array(ext, float))
            for n, (x, wp, ext) in EQDELAY_SEEDS_1PCT.items()}


def load_seed_json(path):
    """Optional denser seed set (several delta per order) in the JSON format written by eqdelay_mp.py."""
    raw = json.load(open(path))
    return {(int(r["n"]), float(r["delta"])): (np.array([float(v) for v in r["x"]]), float(r["wp"]),
                                               np.array([float(v) for v in r["ext"]])) for r in raw.values()}


def eqdelay_poles(n: int, delta: float, seeds=None, max_ratio=1.15, tol=1e-10, max_steps=100):
    """Poles (nominal delay 1 s) and band edge wp of the order-n all-pole LP whose group delay stays in
    [1 - delta, 1 + delta] on 0 <= w <= wp with n+1 equal-ripple extrema. Double precision, warm-started
    from the nearest seed (same n) and continued in wp; a secant on ln(wp) vs ln(delta) hits delta."""
    if n == 1:
        s = 1 / (1 + delta)
        return np.array([-s + 0j]), math.sqrt(s / (1 - delta) - s * s)
    seeds = seeds if seeds is not None else _default_seeds()
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


# =====================================================================================
# Order selection
# =====================================================================================

@dataclass
class DelaySpec:
    """-3 dB mode: fc given (Hz). Optional delay-flatness band f_d with tolerance tol (relative),
    optional stopband point (f_s, A_s dB). For equiripple, tol is the +/- ripple delta."""
    fc: float
    f_d: float | None = None
    tol: float = 0.01
    f_s: float | None = None
    A_s: float | None = None
    n_max: int = 12


def bessel_order(spec: DelaySpec):
    """Smallest n meeting every given criterion. Attenuation at a fixed f_s/fc is NOT monotone in n
    (it peaks, then decays toward the Gaussian limit 3.0103*(f_s/fc)^2 dB), so scan n = 1..n_max."""
    best = None
    for n in range(1, spec.n_max + 1):
        ok = True
        if spec.f_d is not None:
            w3 = bessel_w3db_delay(n)
            ok &= float(bessel_delay_error(n, spec.f_d / spec.fc * w3)) <= spec.tol
        if spec.f_s is not None:
            A = float(bessel_atten_db(n, spec.f_s / spec.fc))
            best = max(best or (A, n), (A, n))
            ok &= A >= spec.A_s
        if ok:
            return n, None
    return None, best  # infeasible within n_max; best = (max attenuation reached, at order)


def eqdelay_order(spec: DelaySpec, seeds=None):
    best = None
    for n in range(1, spec.n_max + 1):
        p, wp = eqdelay_poles(n, spec.tol, seeds)
        w3 = w3db(p)
        ok = True
        if spec.f_d is not None:
            ok &= wp / w3 >= spec.f_d / spec.fc
        if spec.f_s is not None:
            A = -float(mag_db(p, [spec.f_s / spec.fc * w3])[0])
            best = max(best or (A, n), (A, n))
            ok &= A >= spec.A_s
        if ok:
            return n, None
    return None, best


# =====================================================================================
# Self-tests
# =====================================================================================

# -3 dB normalized Bessel poles (upper half plane + real), from 60-digit arithmetic, 8 significant digits.
BESSEL_MAG_REF = {
    1: [-1.0],
    2: [-1.1016013 + 0.6360098j],
    3: [-1.0474092 + 0.9992644j, -1.3226758],
    4: [-0.9952088 + 1.2571057j, -1.3700678 + 0.4102497j],
    5: [-0.9576765 + 1.4711243j, -1.3808773 + 0.7179096j, -1.5023163],
    6: [-0.9306565 + 1.6618633j, -1.3818581 + 0.9714719j, -1.5714904 + 0.3208964j],
    7: [-0.9098678 + 1.8364514j, -1.3789032 + 1.1915668j, -1.6120388 + 0.5892445j, -1.6843682],
    8: [-0.8928697 + 1.9983258j, -1.3738412 + 1.3883566j, -1.6369394 + 0.8227956j, -1.7574084 + 0.2728676j],
    9: [-0.8783993 + 2.1498005j, -1.3675883 + 1.5677337j, -1.6523965 + 1.0313896j, -1.8071705 + 0.5123837j,
        -1.8566005],
    10: [-0.8657569 + 2.2926048j, -1.3606923 + 1.7335057j, -1.6618102 + 1.2211002j, -1.8421962 + 0.7272576j,
         -1.9276197 + 0.2416235j],
}


def _selftest():
    print("Bessel poles vs reference (-3 dB norm), rel. error:")
    for n, ref in BESSEL_MAG_REF.items():
        p = bessel_poles(n, "mag")
        err = max(min(abs(q - r) / abs(r) for q in p) for r in ref)
        assert err < 1e-7, (n, err)
        # delay identity and -3 dB
        w = np.linspace(0, 3, 301)
        pd = bessel_poles(n, "delay")
        assert np.max(np.abs(group_delay(pd, w) - (1 - bessel_delay_error(n, w)))) < 1e-9
        assert abs(mag_db(p, [1.0])[0] + LN2_DB) < 1e-9
        print(f"  n={n:2d}: {err:.1e}  (delay identity ok, -3 dB @ 1 ok)")

    print("Bessel order selection examples:")
    print("  fc=1k, delay error <= 1% to 900 Hz:", bessel_order(DelaySpec(fc=1e3, f_d=900, tol=0.01)))
    print("  fc=1k, 40 dB at 5 kHz:", bessel_order(DelaySpec(fc=1e3, f_s=5e3, A_s=40)))
    print("  fc=1k, 40 dB at 3 kHz (infeasible, Gaussian-limited):", bessel_order(DelaySpec(fc=1e3, f_s=3e3, A_s=40)))

    print("Equiripple-delay solver (single 1 % seed per order): worst |max|E|/delta - 1|, slowest call")
    import time
    rng = np.random.default_rng(1)
    for n in range(2, 16):
        worst, slow = 0.0, 0.0
        grid = [0.0005, 0.001, 0.003, 0.01, 0.03, 0.1, 0.2] + list(np.exp(rng.uniform(np.log(5e-4), np.log(0.2), 5)))
        for delta in grid:
            t0 = time.time()
            p, wp = eqdelay_poles(n, float(delta))
            slow = max(slow, time.time() - t0)
            w = np.linspace(0, wp, 20001)
            q = np.max(np.abs(group_delay(p, w) - 1)) / delta
            assert abs(q - 1) < 1e-6 and np.all(p.real < 0), (n, delta, q)
            worst = max(worst, abs(q - 1))
        print(f"  n={n:2d}: {len(grid)} ripple values in [0.05 %, 20 %]: worst {worst:.1e}, slowest {slow:.2f} s")
    p, wp = eqdelay_poles(4, 0.01)
    assert abs(wp - 3.2675704984686833) < 1e-9
    print("Equiripple order selection: fc=1k, ±1 % delay to 1.4 kHz:", eqdelay_order(DelaySpec(fc=1e3, f_d=1.4e3, tol=0.01)))
    print("                             fc=1k, ±1 %, 40 dB at 3 kHz:", eqdelay_order(DelaySpec(fc=1e3, tol=0.01, f_s=3e3, A_s=40)))


if __name__ == "__main__":
    _selftest()
