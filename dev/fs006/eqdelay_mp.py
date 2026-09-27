# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Extended-precision (mpmath) Remez solver (offline seed generator for fs006_reference.py) for equiripple group-delay all-pole LP prototypes.

Offline use: generates the seed table (continuation from Bessel needs > double precision for n >~ 7,
because the Remez Jacobian near the maximally-flat point has cond ~ 10^n).

Normalization: tau_nom = 1 s, |tau(w) - 1| <= delta on 0 <= w <= wp (rad/s),
n+1 alternation points w_0=0 < ... < w_n=wp, signs s_i = -(-1)^(n-i) (band-edge extremum = 1 - delta).
Parameters x: [ln sigma_1, ln beta_1, ..., ln sigma_m, ln beta_m, (ln sigma_real)], poles -sigma +/- j beta.
"""
import math
import mpmath as mp
import numpy as np

mp.mp.dps = 50
START_EPS = 1e-6


def _split(x, n):
    m = n // 2
    P = [(mp.exp(x[2 * k]), mp.exp(x[2 * k + 1])) for k in range(m)]
    R = mp.exp(x[2 * m]) if n % 2 else None
    return P, R


def tau_mp(x, n, w):
    P, R = _split(x, n)
    t = mp.mpf(0)
    for s, b in P:
        t += s / (s * s + (w - b) ** 2) + s / (s * s + (w + b) ** 2)
    if R is not None:
        t += R / (R * R + w * w)
    return t


def dtau_mp(x, n, w):
    P, R = _split(x, n)
    t = mp.mpf(0)
    for s, b in P:
        for u in (w - b, w + b):
            d = s * s + u * u
            t -= 2 * s * u / (d * d)
    if R is not None:
        d = R * R + w * w
        t -= 2 * R * w / (d * d)
    return t


def d2tau_mp(x, n, w):
    P, R = _split(x, n)
    t = mp.mpf(0)
    for s, b in P:
        for u in (w - b, w + b):
            d = s * s + u * u
            t += 2 * s * (3 * u * u - s * s) / d ** 3
    if R is not None:
        d = R * R + w * w
        t += 2 * R * (3 * w * w - R * R) / d ** 3
    return t


def jac_row(x, n, w):
    P, R = _split(x, n)
    row = []
    for s, b in P:
        u1, u2 = w - b, w + b
        d1, d2 = s * s + u1 * u1, s * s + u2 * u2
        row.append(s * ((u1 * u1 - s * s) / d1 ** 2 + (u2 * u2 - s * s) / d2 ** 2))
        row.append(b * (2 * s * u1 / d1 ** 2 - 2 * s * u2 / d2 ** 2))
    if R is not None:
        d = R * R + w * w
        row.append(R * (w * w - R * R) / d ** 2)
    return row


def newton(x, d, n, ext, sgn, iters=60, max_step=0.5):
    for _ in range(iters):
        F = mp.matrix([tau_mp(x, n, w) - 1 - sg * d for w, sg in zip(ext, sgn)])
        J = mp.matrix([jac_row(x, n, w) + [-sg] for w, sg in zip(ext, sgn)])
        step = mp.lu_solve(J, -F)
        smax = max(abs(step[i]) for i in range(n))
        lam = min(1, max_step / smax) if smax > 0 else 1
        x = [xi + lam * step[i] for i, xi in enumerate(x)]
        d = d + lam * step[n]
        if smax * lam < mp.mpf(10) ** (-mp.mp.dps + 10):
            break
    return x, d


def move_extrema(x, n, ext):
    """Newton on tau'(w)=0 from the previous interior extremal frequencies, safeguarded to stay
    between the neighbouring (old) extremal points; falls back to a golden-section search of |E|."""
    new = [ext[0]]
    for i in range(1, len(ext) - 1):
        lo, hi = (ext[i - 1] + ext[i]) / 2, (ext[i] + ext[i + 1]) / 2
        w = ext[i]
        ok = True
        for _ in range(40):
            dw = dtau_mp(x, n, w) / d2tau_mp(x, n, w)
            w = w - dw
            if not (lo < w < hi):
                ok = False
                break
            if abs(dw) < mp.mpf(10) ** (-mp.mp.dps + 10) * (1 + abs(w)):
                break
        if not ok:
            f = lambda t: -abs(tau_mp(x, n, t) - 1)
            a, b = lo, hi
            g = (mp.sqrt(5) - 1) / 2
            c, d = b - g * (b - a), a + g * (b - a)
            for _ in range(200):
                if f(c) < f(d):
                    b = d
                else:
                    a = c
                c, d = b - g * (b - a), a + g * (b - a)
            w = (a + b) / 2
        new.append(w)
    new.append(ext[-1])
    return new


def check_alternation(x, n, ext, sgn, d):
    if any(not (a < b) for a, b in zip(ext[:-1], ext[1:])):
        return False
    for w, sg in zip(ext, sgn):
        e = tau_mp(x, n, w) - 1
        if abs(e - sg * d) > abs(d) * mp.mpf("1e-6"):
            return False
    return True


def remez(n, wp, x, ext, iters=60):
    sgn = [-(-1) ** (n - i) for i in range(n + 1)]
    d = mp.mpf(0)
    for _ in range(iters):
        x, d = newton(x, d, n, ext, sgn)
        new = move_extrema(x, n, ext)
        shift = max(abs(a - b) for a, b in zip(new, ext))
        ext = new
        if shift < mp.mpf(10) ** (-30) * wp:
            break
    x, d = newton(x, d, n, ext, sgn)
    if d <= 0 or not check_alternation(x, n, ext, sgn, d):
        raise RuntimeError("alternation lost")
    return x, d, ext


def bessel_x(n):
    from fs006_reference import bessel_theta_coeffs
    a = bessel_theta_coeffs(n)
    r = mp.polyroots([mp.mpf(c) for c in reversed(a)], maxsteps=800, extraprec=800)
    up = sorted([z for z in r if mp.im(z) > mp.mpf(10) ** -30], key=lambda z: mp.im(z))
    x = []
    for z in up:
        x += [mp.log(-mp.re(z)), mp.log(mp.im(z))]
    if n % 2:
        re = [z for z in r if abs(mp.im(z)) <= mp.mpf(10) ** -30][0]
        x.append(mp.log(-mp.re(re)))
    return x


def bessel_err_band(n, eps):
    """w where the Bessel (delay-normalized) delay error reaches eps (closed form w^2n/|theta(jw)|^2)."""
    from fs006_reference import bessel_theta_eval as theta_eval
    lo, hi = 1e-9, 200.0
    f = lambda w: 2 * n * math.log(w) - math.log(abs(complex(theta_eval(n, 1j * w))) ** 2) - math.log(eps)
    for _ in range(200):
        mid = math.sqrt(lo * hi)
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return lo


def eqdelay_mp(n, delta, tol=mp.mpf("1e-20"), x0=None, wp0=None, ext0=None):
    """Continuation in wp from a seed (default: Bessel at a tiny band), then secant on wp to hit delta."""
    delta = mp.mpf(delta)
    if n == 1:
        sig = 1 / (1 + delta)
        return [mp.log(sig)], mp.sqrt(sig / (1 - delta) - sig * sig), [mp.mpf(0), mp.sqrt(sig / (1 - delta) - sig * sig)]
    if x0 is None:
        x = bessel_x(n)
        wp = mp.mpf(bessel_err_band(n, START_EPS))
        ext = [mp.sqrt(wp ** 2 * (1 - mp.cos(i * mp.pi / n)) / 2) for i in range(n + 1)]
    else:
        x, wp, ext = list(x0), mp.mpf(wp0), list(ext0)
    x, d, ext = remez(n, wp, x, ext)
    hist = [(wp, d, x, ext)]
    ratio_cap = mp.mpf(1.15) if d < delta else 1 / mp.mpf(1.15)
    up = d < delta
    while (d < delta) == up:
        wl, dl, xl, el = hist[-1]
        r = (delta / dl) ** (mp.mpf(1) / (2 * n))
        r = min(r, ratio_cap) if up else max(r, ratio_cap)
        while True:
            try:
                wn = wl * r
                xn, dn, en = remez(n, wn, xl, [e * r for e in el])
                break
            except (RuntimeError, ZeroDivisionError):
                r = mp.sqrt(r)
                if abs(r - 1) < mp.mpf("1e-6"):
                    raise
        hist.append((wn, dn, xn, en))
        d = dn
    (w0, d0, _, _), (w1, d1, x1, e1) = hist[-2], hist[-1]
    for _ in range(60):
        lw = mp.log(w0) + (mp.log(delta) - mp.log(d0)) * (mp.log(w1) - mp.log(w0)) / (mp.log(d1) - mp.log(d0))
        wn = mp.exp(lw)
        xn, dn, en = remez(n, wn, x1, [e * wn / w1 for e in e1])
        if abs(dn / delta - 1) < tol:
            return xn, wn, en
        (w0, d0), (w1, d1, x1, e1) = (w1, d1), (wn, dn, xn, en)
    raise RuntimeError("secant failed")


def x_to_poles(x, n):
    P, R = _split(x, n)
    out = []
    for s, b in P:
        out += [complex(-s, b), complex(-s, -b)]
    if R is not None:
        out.append(complex(-R, 0))
    return np.array(out)


if __name__ == "__main__":
    import time, json, sys, os
    deltas = [0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1]
    NMAX = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    out = "eqdelay_seed_table.json"
    res = json.load(open(out)) if os.path.exists(out) else {}
    for n in range(1, NMAX + 1):
        seed = None
        for delta in deltas:
            key = f"{n}|{delta}"
            if key in res:
                r = res[key]
                seed = ([mp.mpf(v) for v in r["x"]], mp.mpf(r["wp"]), [mp.mpf(v) for v in r["ext"]])
                continue
            t0 = time.time()
            if seed is None:
                for se in (1e-6, 1e-5, 1e-4, 1e-7, 1e-3):
                    START_EPS = se
                    try:
                        x, wp, ext = eqdelay_mp(n, delta)
                        break
                    except (ZeroDivisionError, RuntimeError) as ex:
                        print(f"   n={n} start_eps={se} failed: {ex}")
                else:
                    raise RuntimeError("no start worked")
            else:
                x, wp, ext = eqdelay_mp(n, delta, x0=seed[0], wp0=seed[1], ext0=seed[2])
            seed = (x, wp, ext)
            p = x_to_poles(x, n)
            res[key] = {"n": n, "delta": delta, "wp": mp.nstr(wp, 25),
                        "x": [mp.nstr(v, 25) for v in x],
                        "ext": [mp.nstr(v, 25) for v in ext],
                        "poles": [[z.real, z.imag] for z in p]}
            json.dump(res, open(out, "w"), indent=1)
            print(f"n={n:2d} delta={delta:<7g} wp={mp.nstr(wp, 12):>14} t={time.time()-t0:5.1f}s")
            sys.stdout.flush()
