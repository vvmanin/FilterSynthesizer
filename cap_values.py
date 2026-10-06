# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  cap_values.py   [Tier C — capacitor candidate values]
#
#  One home for the capacitor value grids the snappers draw from (FS-037):
#    - an E-series row repeated over decades (E3 / E6 / E12 / E24), or
#    - a user's CUSTOM list ("custom C row"), used as given: absolute values,
#      no decade repetition (4.7n does not imply 47n).
#  Only the candidate set lives here -- no cell math, residuals or scoring.
#  Units: µF (the solvers' internal capacitor unit).
#
#  cfg carries the custom list as cfg["C_values"] (tuple, µF) whenever
#  cfg["C_series"] == CUSTOM; the Topology tab then also sets
#  C_min/C_max = min/max of the list (the envelope does not apply to it).
# =====================================================================
import re

import numpy as np

CUSTOM = "Custom"

CAP_SERIES = {
    "E3":  [1.0, 2.2, 4.7],
    "E6":  [1.0, 1.5, 2.2, 3.3, 4.7, 6.8],
    "E12": [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2],
    "E24": [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0, 3.3, 3.6, 3.9,
            4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1],
}

# A custom list must span at least this ratio: phase-1 bounds the free caps to
# [C_min*1.1, C_max*0.9] (unified_solver_v2), which is empty below ~1.22x.
MIN_SPAN = 1.5

_SUFFIX_UF = {"p": 1e-6, "n": 1e-3, "u": 1.0, "µ": 1.0, "μ": 1.0}


def cap_grid(series_str, c_min, c_max, decades, ndigits=12, custom=None,
             fallback=None):
    """Sorted capacitor grid (µF) within [c_min*0.99, c_max*1.01].

    series_str == CUSTOM -> the `custom` values as given (rounded to `ndigits`).
    Otherwise the union of the named E-rows times 10**d for d in `decades`
    (comma-separated names; unknown names ignored; `fallback` row name used
    when none is known). Each solver passes its own decade range and rounding,
    so its E-series grid is exactly what it built before FS-037."""
    if str(series_str).strip() == CUSTOM:
        vals = sorted({round(float(v), ndigits) for v in (custom or ())})
        grid = np.array(vals, dtype=float)
    else:
        base = set()
        for p in str(series_str).split(","):
            base.update(CAP_SERIES.get(p.strip().upper(), []))
        if not base and fallback:
            base.update(CAP_SERIES[fallback])
        arr = np.array(sorted(base))
        grid = np.sort([round(v * 10**d, ndigits) for d in decades for v in arr])
    if c_min is None:
        return grid[grid <= c_max * 1.01]
    return grid[(grid >= c_min * 0.99) & (grid <= c_max * 1.01)]


_TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<val>(?P<num>\d+(?:\.\d*)?|\.\d+)\s*(?P<suf>[pnuµμ])(?P<rkm>\d*)f?(?![a-z0-9.]))"
    r"|(?P<sep>[,;])"
    r"|(?P<bad>[^\s,;]+))",
    re.IGNORECASE)


def parse_cap_list(text):
    """Parse a typed capacitor list -> (values_µF_tuple, errors).

    Accepts p / n / u / µ suffixes with an optional F ("4.7n", "4.7 nF", "100p"),
    RKM notation ("4n7" = 4.7 nF), separated by commas, semicolons or spaces.
    Values are sorted and de-duplicated. A bare number (no suffix), zero, a
    negative or any other token is an error; so is a list with fewer than 2
    distinct values or a span max/min below MIN_SPAN. Any error -> values = ()."""
    vals, errors = [], []
    for m in _TOKEN.finditer(text or ""):
        if m.group("val"):
            num, suf, rkm = m.group("num"), m.group("suf").lower(), m.group("rkm")
            if rkm and "." in num:
                errors.append(m.group("val").strip())
                continue
            v = float(num + "." + rkm if rkm else num) * _SUFFIX_UF[suf]
            if v <= 0:
                errors.append(m.group("val").strip())
                continue
            vals.append(v)
        elif m.group("bad"):
            errors.append(m.group("bad"))
    uniq = []
    for v in sorted(vals):
        if not uniq or abs(v - uniq[-1]) > 1e-9 * v:
            uniq.append(v)
    if errors:
        return (), [f"not a capacitor value: {', '.join(errors)} "
                    "(use a p / n / u suffix, e.g. 100p, 4.7n, 4n7, 1u)"]
    if len(uniq) < 2:
        return (), ["enter at least 2 different values"]
    if uniq[-1] / uniq[0] < MIN_SPAN:
        return (), [f"the values must span at least {MIN_SPAN:g}× (largest / smallest)"]
    return tuple(uniq), []


def format_cap(v_uF):
    """4.7e-3 µF -> '4.7 nF' (pF below 1 nF, µF from 1 µF)."""
    f = float(v_uF) * 1e-6
    for unit, scale in (("pF", 1e-12), ("nF", 1e-9), ("µF", 1e-6)):
        if f < scale * 1000 * (1 - 1e-9) or unit == "µF":
            return f"{f / scale:.4g} {unit}"


def format_cap_list(values):
    """(1e-4, 1e-3) -> '100 pF, 1 nF'."""
    return ", ".join(format_cap(v) for v in values)
