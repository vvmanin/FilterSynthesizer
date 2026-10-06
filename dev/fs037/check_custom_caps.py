# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-037 checks for the custom capacitor list (`cap_values.py` and the three
solver call sites that snap capacitors).

Run from anywhere:  python dev/fs037/check_custom_caps.py [--quick]
Every check asserts; a clean run ends with "ALL CHECKS PASSED".

  1. parser: accepted / rejected inputs
  2. grid parity: cap_values.cap_grid == each pre-FS-037 builder (copied below
     verbatim from the solvers) for E3 / E6 / E12 / E24 over several envelopes
  3. BOM parity: Custom = the E12 values inside the envelope gives the same BOMs
     as E12 with the same C_min/C_max (2nd/3rd-order, notch, AM C1 split,
     zero-manifold parallel C2, 1st-order)
  4. membership: a short list -> every capacitor (incl. the C1a/C1b/C2a/C2b legs)
     is a list member; an infeasible section returns no rows, no crash
"""
import os
import re
import sys

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "dev", "fs028"))

import cap_values as CV             # noqa: E402

QUICK = "--quick" in sys.argv


def ok(msg):
    print(f"  ok  {msg}")


def section(title):
    print(f"\n{title}")


# =====================================================================
# 1. parser
# =====================================================================
section("1. parse_cap_list")
v, e = CV.parse_cap_list("100p, 1n 4n7;10nF")
assert not e and len(v) == 4, (v, e)
assert np.allclose(v, [1e-4, 1e-3, 4.7e-3, 1e-2]), v
ok("'100p, 1n 4n7;10nF' -> 100 pF, 1 nF, 4.7 nF, 10 nF")
v, e = CV.parse_cap_list("4.7 nF, 2.2u, 1µ, 330P, 1n, 1.0n")
assert not e and np.allclose(v, [3.3e-4, 1e-3, 4.7e-3, 1.0, 2.2]), (v, e)
ok("spaces before the unit, upper case, µ, duplicates removed")
for bad in ("abc", "-1n", "1n", "", "1n, 1.2n", "100, 1n", "0n, 1n", "4.7n7, 1n", "1nx, 10n"):
    v, e = CV.parse_cap_list(bad)
    assert v == () and e, (bad, v, e)
ok("rejected: abc, -1n, single value, empty, span < 1.5x, bare number, zero, "
   "4.7n7, 1nx")
assert CV.format_cap(4.7e-3) == "4.7 nF" and CV.format_cap(1e-4) == "100 pF" \
    and CV.format_cap(1e-3) == "1 nF" and CV.format_cap(2.2) == "2.2 µF", "format_cap"
ok("format_cap")


# =====================================================================
# 2. grid parity with the pre-FS-037 builders (verbatim copies)
# =====================================================================
_OLD_US = {
    "E3":  [1.0, 2.2, 4.7],
    "E6":  [1.0, 1.5, 2.2, 3.3, 4.7, 6.8],
    "E12": [1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2],
    "E24": [1.0,1.1,1.2,1.3,1.5,1.6,1.8,2.0,2.2,2.4,2.7,3.0,3.3,3.6,3.9,
            4.3,4.7,5.1,5.6,6.2,6.8,7.5,8.2,9.1],
}


def old_us_cap_grid(series_str, c_min, c_max):           # unified_solver_v2.cap_grid
    parts = [p.strip().upper() for p in series_str.split(",")]
    base = set()
    for p in parts:
        if p in _OLD_US:
            base.update(_OLD_US[p])
    arr = np.array(sorted(base))
    mult = [10**i for i in range(-6, 2)]
    grid = np.sort([round(v*m, 10) for m in mult for v in arr])
    return grid[(grid >= c_min*0.99) & (grid <= c_max*1.01)]


def old_zm_grid(series_str, c_min, c_max):               # zero_manifold_solver._eseries_grid
    E = {k: _OLD_US[k] for k in ("E6", "E12", "E24")}
    base = set()
    for p in [x.strip().upper() for x in series_str.split(",")]:
        if p in E:
            base.update(E[p])
    arr = np.array(sorted(base))
    mult = [10**i for i in range(-6, 0)]
    grid = np.sort([round(v*m, 12) for m in mult for v in arr])
    return grid[(grid >= c_min*0.99) & (grid <= c_max*1.01)]


def _old_fo_base(series_str):
    base = set()
    for p in [p.strip().upper() for p in series_str.split(",")]:
        base.update(_OLD_US.get(p, []))
    if not base:
        base.update(_OLD_US["E12"])
    return np.array(sorted(base))


def old_fo_build(series_str, c_min, c_max):              # first_order_solver.build_cap_grid
    base = _old_fo_base(series_str)
    mult = [10**i for i in range(-7, 3)]
    grid = np.sort([round(v*m, 12) for m in mult for v in base])
    return grid[(grid >= c_min*0.99) & (grid <= c_max*1.01)]


def old_fo_lower(series_str, c_max, n=3):                # first_order_solver.nearest_lower_caps
    base = _old_fo_base(series_str)
    mult = [10**i for i in range(-7, 3)]
    grid = np.sort([round(v*m, 12) for m in mult for v in base])
    below = grid[grid <= c_max * (1 + 1e-9)]
    if below.size == 0:
        return np.array([])
    return below[::-1][:max(1, int(n))][::-1]


section("2. grid parity (E-series grids unchanged)")
import unified_solver_v2 as US      # noqa: E402
import zero_manifold_solver as ZM   # noqa: E402
import first_order_solver as FOS    # noqa: E402

ENVS = [(6.8e-5, 1e-2), (1e-6, 1.0), (3.3e-4, 2.2), (1e-5, 47.0), (1e-3, 1e-3 * 1.2)]
n = 0
for ser in ("E3", "E6", "E12", "E24", "E6, E12", "bogus"):
    for cmin, cmax in ENVS:
        assert np.array_equal(US.cap_grid(ser, cmin, cmax), old_us_cap_grid(ser, cmin, cmax))
        assert np.array_equal(FOS.build_cap_grid(ser, cmin, cmax), old_fo_build(ser, cmin, cmax))
        for k in (1, 3, 5):
            assert np.array_equal(FOS.nearest_lower_caps(ser, cmax, k), old_fo_lower(ser, cmax, k))
        if ser != "E3":            # the old ZM grid had no E3 (empty grid) -- fixed now
            assert np.array_equal(ZM._eseries_grid(ser, cmin, cmax), old_zm_grid(ser, cmin, cmax))
        n += 1
assert ZM._eseries_grid("E3", 6.8e-5, 1e-2).size > 0
ok(f"{n} (series, envelope) pairs bit-identical in all 4 builders; ZM now has E3")

# custom grids: absolute values, clipped, no decade repetition
cust = (1e-4, 1e-3, 4.7e-3, 1e-2)
g = US.cap_grid(CV.CUSTOM, 1e-4, 1e-2, cust)
assert np.allclose(g, cust) and 4.7e-2 not in g
assert np.allclose(ZM._eseries_grid(CV.CUSTOM, 1e-4, 1e-2, cust), cust)
assert np.allclose(FOS.nearest_lower_caps(CV.CUSTOM, 1e-2, 4, cust), cust)
assert FOS.nearest_lower_caps(CV.CUSTOM, 1e-2, 3, ()).size == 0     # no E12 fallback
ok("custom grid = the list as given in every builder; empty custom -> empty (no fallback)")


# =====================================================================
# 3 / 4. solves
# =====================================================================
import fs028_common as C            # noqa: E402

E12_IN_ENV = [v * 10**d for d in range(-5, -1) for v in CV.CAP_SERIES["E12"]]
E12_IN_ENV = [x for x in E12_IN_ENV if 6.8e-5 * 0.99 <= x <= 1e-2 * 1.01]
_typed = ", ".join(CV.format_cap(x).replace(" ", "") for x in E12_IN_ENV)
E12_LIST, err = CV.parse_cap_list(_typed)
assert not err and len(E12_LIST) == len(E12_IN_ENV), err
SHORT_LIST, _ = CV.parse_cap_list("100p, 1n, 4.7n, 10n")

CAP_KEY = re.compile(r"^C\d+[ab]?$")


def env_for(series, values=None):
    env = dict(C.ENV_DEFAULT)
    if series == CV.CUSTOM:
        env.update(C_series=CV.CUSTOM, C_values=tuple(values),
                   C_min=min(values), C_max=max(values))
    else:
        env.update(C_series=series, C_min=min(E12_LIST), C_max=max(E12_LIST))
    return env


def bom_key(row):
    return (row.get("topology"),) + tuple(sorted(
        (k, round(float(v), 9)) for k, v in row.items()
        if re.match(r"^[CR]\d+[ab]?$", k) and isinstance(v, (int, float))))


def caps_of(row):
    """The physical capacitors of a BOM row: on a split row Cn holds the
    parallel sum Cna+Cnb, so the legs are the parts and Cn is skipped."""
    return {k: float(v) for k, v in row.items()
            if CAP_KEY.match(k) and isinstance(v, (int, float)) and v
            and not row.get(f"{k}_parallel")}


def on_list(v, values):
    return any(abs(v - x) <= 1e-9 * x for x in values)


# (section, family): 2nd/3rd-order LP, BP, 3rd-order LPn (notch), 2LPn
# zero-manifold parallel-C2 (C2a/C2b), AM notch with the C1a/C1b split, MFB HP.
# The two LPn sections are milder than the FS-028 elliptic ones, which give no
# VCVS BOM in the default envelope (nothing to compare).
CASES = [(C.section_by_id("LP2"), "VCVS"), (C.section_by_id("BP2"), "MFB"),
         (C._sec("LPn3m", 3, "LPn", 1000.0, 1.2, fz=3000.0, f1=800.0), "VCVS"),
         (C._sec("LPn2m", 2, "LPn", 1000.0, 0.8, fz=2500.0), "VCVS"),
         (C.section_by_id("N2"), "AM"), (C.section_by_id("HP2"), "MFB")]
if QUICK:
    CASES = CASES[:3]

section("3. BOM parity: Custom = E12 values in the window  vs  E12")
with C.workdir():
    short_counts = {}
    for sec, fam in CASES:
        sid = sec["id"]
        topos, dct = C.route(sec, fam)
        res_e, _ = C.run_section(sec, fam, topos, dct, preset="Fast", instrument=False,
                                 env=env_for("E12"))
        res_c, _ = C.run_section(sec, fam, topos, dct, preset="Fast", instrument=False,
                                 env=env_for(CV.CUSTOM, E12_LIST))
        ke = sorted(bom_key(r) for r in res_e.get("snapped") or [])
        kc = sorted(bom_key(r) for r in res_c.get("snapped") or [])
        assert ke == kc, (sid, fam, len(ke), len(kc))
        assert ke or sid not in ("LPn3m", "LPn2m"), (sid, "expected BOMs")
        legs = sum(any(k[-1] in "ab" for k in caps_of(r)) for r in res_c.get("snapped") or [])
        ok(f"{sid}-{fam:<4} {len(ke):3d} BOMs identical  ({'/'.join(topos)}"
           f"{f'; {legs} with split legs' if legs else ''})")

        # 4. membership on a short list (same section)
        res_s, _ = C.run_section(sec, fam, topos, dct, preset="Fast", instrument=False,
                                 env=env_for(CV.CUSTOM, SHORT_LIST))
        rows = res_s.get("snapped") or []
        for r in rows:
            for k, v in caps_of(r).items():
                assert on_list(v, SHORT_LIST), (sid, fam, r.get("topology"), k, v)
        short_counts[f"{sid}-{fam}"] = len(rows)

    # 1st-order: Custom realizes at every list value; E12 at the 3 nearest below C_max
    base = dict(f0=1000.0, R_series="E48", R_min=1e-4, R_max=10.0, cap_mode="nearest_lower")
    for topo, g in (("1LP-ni-unity", 1.0), ("1HP-inv-gained", 2.0), ("1LP-ni-atten", 0.5)):
        re_ = FOS.synthesize_first_order(dict(base, C_series="E12", C_max=1e-2, n_caps=3),
                                         topology=topo, dc_gain=g)
        rc_ = FOS.synthesize_first_order(dict(base, C_series=CV.CUSTOM, C_values=E12_LIST,
                                              C_max=max(E12_LIST), n_caps=len(E12_LIST)),
                                         topology=topo, dc_gain=g, top_k=1000)
        ke = {bom_key(r) for r in re_["snapped"]}
        kc = {bom_key(r) for r in rc_["snapped"]}
        assert ke and ke <= kc, (topo, len(ke), len(kc))
        rs_ = FOS.synthesize_first_order(dict(base, C_series=CV.CUSTOM, C_values=SHORT_LIST,
                                              C_max=max(SHORT_LIST), n_caps=len(SHORT_LIST)),
                                         topology=topo, dc_gain=g)
        for r in rs_["snapped"]:
            assert all(on_list(v, SHORT_LIST) for v in caps_of(r).values()), (topo, r)
        assert {round(caps_of(r)["C1"], 12) for r in rs_["snapped"]} \
            <= {round(x, 12) for x in SHORT_LIST}
        ok(f"1st-order {topo:<15} E12 rows ⊂ Custom rows ({len(ke)} ⊂ {len(kc)}); "
           f"short list -> {len(rs_['snapped'])} rows, all on-list")

section("4. short list '100p, 1n, 4.7n, 10n': every capacitor is a list member")
for k, n_rows in short_counts.items():
    ok(f"{k:<10} {n_rows:3d} BOMs" + ("  (no realization -- no crash)" if not n_rows else ""))

print("\nALL CHECKS PASSED")
