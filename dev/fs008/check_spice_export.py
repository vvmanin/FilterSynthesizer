# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-008 checks for `spice_cells.py` (netlist IR + MNA) and `spice_export.py`
(the LTspice netlist writer).

Run from anywhere:  python dev/fs008/check_spice_export.py [--quick]
Every check asserts; a clean run ends with "ALL CHECKS PASSED". Section numbers
refer to dev/FS-008_ltspice_export_design_note.md §15.1.

  1  IR vs cell transfer function, every cell (random values, split caps, AM R8 != R7)
  2  DC path per cell (informational: a floating node is a hardware finding)
  3  value formatting round trip, suffixes, ASCII
  4  netlist round trip: parse the written .cir back (R, C, G, E, X, V, .subckt),
     solve it with an independent SPICE-semantics MNA, compare with the IR
  5  MC band mapping == hw_plots._r_tol_frac
  6  loading: buffered cascade == tool product; loaded deviation printed
  7  FS generic Ideal clamp vs IDEAL_PARAMS
"""
import math
import os
import re
import sys
import time

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import cells_first_order as FO      # noqa: E402
import hw_plots                      # noqa: E402
import opamp_library as oplib        # noqa: E402
import spice_cells as SC             # noqa: E402
import spice_export as SX            # noqa: E402
import tf_derivation_v2 as TF        # noqa: E402

QUICK = "--quick" in sys.argv
FAILS = []


def ok(msg):
    print(f"  ok  {msg}")


def fail(msg):
    FAILS.append(msg)
    print(f"  FAIL {msg}")


def response_func(name):
    fo = FO.parse_name(name)
    case = (FO.derive_first_order_nonideal(fo) if fo is not None
            else TF.derive_nonideal(TF.topo_for_name(name)))
    return TF.make_response_func(case)


def random_values(names, rng):
    """R log-uniform 1 kOhm..1 MOhm (MOhm), C 100 pF..1 uF (uF), op-amp params."""
    comp = {}
    for nm in names:
        if nm == "Ro":
            comp[nm] = 10 ** rng.uniform(1, math.log10(2000)) / 1e6
        elif nm == "A_ol":
            comp[nm] = 10 ** rng.uniform(4, 7)
        elif nm == "GBWP_hz":
            comp[nm] = 10 ** rng.uniform(5, 8)
        elif nm[0] == "R":
            comp[nm] = 10 ** rng.uniform(-3, 0)
        elif nm[0] == "C":
            comp[nm] = 10 ** rng.uniform(-4, 0)
        else:
            raise KeyError(nm)
    return comp


def opamp_of(comp):
    # Ro cancels out of an unloaded follower's TF (first-order ni unity/atten),
    # so it may be absent from the names; any value is then equivalent.
    return {"A_ol": comp["A_ol"], "GBWP_hz": comp["GBWP_hz"],
            "Ro_ohm": comp.get("Ro", 100e-6) * 1e6}


# ---------------------------------------------------------------------
def check_1_ir_vs_tf():
    print("\n[1] IR vs cell non-ideal transfer function")
    f = np.logspace(0, 7, 30)
    w = 2 * np.pi * f
    names = SC.all_cell_names()
    assert len(names) == 92, f"expected 92 cells, got {len(names)}"
    rng = np.random.default_rng(8)
    worst = 0.0
    t0 = time.time()
    n_draws = 2 if QUICK else 5
    for name in names:
        H, tf_names = response_func(name)
        try:
            sup = SC.superset(name)
        except KeyError as e:                 # a registered cell without an IR table
            fail(f"{name}: {e}")
            continue
        present = {e[0] for e in sup["entries"] if e[4] is True}
        tf_parts = {n for n in tf_names if n[0] in "RC" and n != "Ro"}
        if present != tf_parts:
            fail(f"{name}: IR parts {sorted(present)} != TF parts {sorted(tf_parts)}")
            continue
        for d in range(n_draws):
            comp = random_values(tf_names, rng)
            row = {"topology": name, **{k: v for k, v in comp.items() if k[0] in "RC" and k != "Ro"}}
            ir = SC.section_ir(row, opamp_of(comp))
            h_ir = SC.mna_ac(ir, f)
            h_tf = H(comp, w)
            err = np.max(np.abs(h_ir - h_tf)) / np.max(np.abs(h_tf))
            worst = max(worst, err)
            if not err <= 1e-9:
                fail(f"{name} draw {d}: |dH|/max|H| = {err:.2e}")
                break
            if d == 0:
                # split caps: the row carries C?a + C?b = C?, the IR draws two parts
                for c in ("C1", "C2"):
                    if c in present:
                        r2 = dict(row, **{c + "a": 0.4 * row[c], c + "b": 0.6 * row[c],
                                          c + "_parallel": True})
                        e2 = np.max(np.abs(SC.mna_ac(SC.section_ir(r2, opamp_of(comp)), f)
                                           - h_tf)) / np.max(np.abs(h_tf))
                        if not e2 <= 1e-9:
                            fail(f"{name} split {c}: {e2:.2e}")
    ok(f"{len(names)} cells x {n_draws} draws x {f.size} freqs, worst "
       f"|dH|/max|H| = {worst:.1e} ({time.time() - t0:.0f} s)")
    # AM: R8 != R7 is covered because the draws give R8 its own value; R8
    # missing from the row falls back to the matched R7.
    row = {"topology": "2LP-AM", "C2": 1e-3, "C3": 1e-3, "R2": 0.01, "R4": 0.01,
           "R5": 0.01, "R6": 0.01, "R7": 0.01}
    ir = SC.section_ir(row)
    r8 = [p for p in ir["parts"] if p["key"] == "R8"]
    assert r8 and abs(r8[0]["value"] - 1e4) < 1e-6, r8
    ok("AM row without R8 exports R8 = R7")
    try:
        SC.section_ir({"topology": "2LP-gained", "C3": 1e-3, "C4": 1e-3, "R2": 0.01,
                       "R3": 0.01, "R5": 0.01, "R6": 0.0})
        fail("a present part with value 0.0 must raise")
    except ValueError:
        ok("present part with value 0.0 raises")


def check_2_dc_path():
    print("\n[2] DC path per cell (informational)")
    rng = np.random.default_rng(2)
    bad = {}
    for name in SC.all_cell_names():
        sup = SC.superset(name)
        row = {"topology": name}
        for key, kind, _, _, st in sup["entries"]:
            if st is True:
                row[key] = 10 ** rng.uniform(-3, 0) if kind == "R" else 10 ** rng.uniform(-4, 0)
        fl = SC.dc_floating_nodes(SC.section_ir(row))
        if fl:
            bad[name] = fl
    if bad:
        for k, v in bad.items():
            print(f"  note {k}: no DC path at {v}")
    ok(f"DC path: {92 - len(bad)} of 92 cells clean"
       + (f"; {len(bad)} with a floating node (listed above)" if bad else ""))


def check_3_format():
    print("\n[3] value formatting")
    rng = np.random.default_rng(3)
    vals = list(10 ** rng.uniform(-15, 12, 4000))
    vals += [1e6, 1e-15, 4990.0, 12100.0, 680e-12, 1.5e-6, 999.9999e3, 999999.5,
             4.7000000001e-9, 1.0, 0.001, 1e9, 2.2e12, 33e-15]
    for v in vals:
        txt = SX.fmt_value(v)
        back = SX.parse_value(txt)
        if not abs(back - v) <= 1e-12 * abs(v) + 5e-6 * abs(v):     # 6 significant digits
            fail(f"round trip {v!r} -> {txt!r} -> {back!r}")
            return
        # only digits, '.', exponent and one scale suffix; never a bare M / F
        if not re.fullmatch(r"-?\d+(\.\d+)?(e[+-]?\d+)?(T|G|Meg|k|m|u|n|p|f)?", txt):
            fail(f"bad text {txt!r}")
            return
    assert SX.fmt_value(1e6) == "1Meg", SX.fmt_value(1e6)
    assert SX.fmt_value(4990.000000000001) == "4.99k"
    assert SX.fmt_value(4.7000000001e-9) == "4.7n"
    assert SX.fmt_value(1e-3) == "1m"
    assert SX.fmt_value(12100.0) == "12.1k"
    assert SX.fmt_value(999999.5) == "999.9995k" or SX.fmt_value(999999.5) == "1Meg"
    ok(f"{len(vals)} values round-trip within 6 significant digits; 1Meg / 4.99k / 4.7n / 1m")


# ---------------------------------------------------------------------
#  4 — independent SPICE-semantics reader for the written netlist
# ---------------------------------------------------------------------
def _flatten(text):
    """Parse the writer's netlist into flat elements. Supports R C V G E X and
    .subckt/.ends, .param (numbers + {expr} with the given params)."""
    lines = [ln.strip() for ln in text.splitlines()[1:]]        # line 1 = title
    params, subckts, top, cur = {}, {}, [], None
    for ln in lines:
        if not ln or ln.startswith("*") or ln.startswith(";"):
            continue
        ln = ln.split(";")[0].strip()
        low = ln.lower()
        if low.startswith(".subckt"):
            tok = ln.split()
            cur = {"pins": tok[2:], "body": []}
            subckts[tok[1].upper()] = cur
            continue
        if low.startswith(".ends"):
            cur = None
            continue
        if low.startswith(".param"):
            for k, v in re.findall(r"(\w+)\s*=\s*(\S+)", ln[6:]):
                params[k.lower()] = v
            continue
        if low.startswith("."):
            continue
        (cur["body"] if cur is not None else top).append(ln.split())
    return params, subckts, top


def _num(tok, params):
    tok = tok.strip()
    if tok.startswith("{") and tok.endswith("}"):
        expr = tok[1:-1]
        m = re.fullmatch(r"TOL\(([^,]+),(\w+)\)", expr, re.I)
        if m:
            return SX.parse_value(m.group(1))                    # nominal of an MC value
        env = {k: _num(v, params) for k, v in params.items() if not v.startswith("{")}
        env["pi"] = math.pi
        return float(eval(expr.lower(), {"__builtins__": {}}, env))
    return SX.parse_value(tok)


def netlist_response(text, f, out="OUT"):
    params, subckts, top = _flatten(text)
    elems = []

    def expand(tokens, prefix, pinmap):
        name = tokens[0]
        t = name[0].upper()

        def nd(x):
            x = x.upper()
            if x == "0":
                return "0"
            return pinmap.get(x, prefix + x)
        if t == "X":
            sub = subckts[tokens[-1].upper()]
            pm = {p.upper(): nd(n) for p, n in zip(sub["pins"], tokens[1:-1])}
            for b in sub["body"]:
                expand(b, prefix + name.upper() + ".", pm)
        elif t in "RC":
            elems.append((t, nd(tokens[1]), nd(tokens[2]), _num(tokens[3], params)))
        elif t in "GE":
            elems.append((t, nd(tokens[1]), nd(tokens[2]), nd(tokens[3]), nd(tokens[4]),
                          _num(tokens[5], params)))
        elif t == "V":
            ac = tokens.index("AC") if "AC" in tokens else None
            val = _num(tokens[ac + 1], params) if ac is not None else 0.0
            elems.append(("V", nd(tokens[1]), nd(tokens[2]), val))
        else:
            raise ValueError(f"unsupported element {name}")
    for tk in top:
        expand(tk, "", {})
    nodes = sorted({x for e in elems for x in e[1:3] + (e[3:5] if e[0] in "GE" else ())
                    if isinstance(x, str) and x != "0"})
    idx = {n: i for i, n in enumerate(nodes)}
    branches = [e for e in elems if e[0] in "VE"]
    N = len(nodes) + len(branches)
    s = 2j * np.pi * np.asarray(f, dtype=float)
    Y = np.zeros((s.size, N, N), dtype=complex)
    b = np.zeros((s.size, N), dtype=complex)

    def add(r, c, v):
        if r is not None and c is not None:
            Y[:, r, c] += v
    ix = idx.get
    k = len(nodes)
    for e in elems:
        t = e[0]
        if t in "RC":
            y = (1.0 / e[3]) if t == "R" else s * e[3]
            a, c = ix(e[1]), ix(e[2])
            add(a, a, y); add(c, c, y); add(a, c, -y); add(c, a, -y)
        elif t == "G":                           # current gm*V(nc+,nc-) from n+ to n- inside
            a, c, p, q, gm = ix(e[1]), ix(e[2]), ix(e[3]), ix(e[4]), e[5]
            add(a, p, gm); add(a, q, -gm); add(c, p, -gm); add(c, q, gm)
        elif t in "VE":
            a, c = ix(e[1]), ix(e[2])
            add(a, k, 1.0); add(c, k, -1.0)
            add(k, a, 1.0); add(k, c, -1.0)
            if t == "V":
                b[:, k] = e[3]
            else:
                p, q = ix(e[3]), ix(e[4])
                add(k, p, -e[5]); add(k, q, e[5])
            k += 1
    v = np.linalg.solve(Y, b[..., None])[..., 0]
    return v[:, idx[out.upper()]]


def demo_sections(rng, names, opamps):
    """Random sections with snapped-like values (4 significant digits, as
    E-series values have), so the written netlist carries them exactly."""
    secs = []
    for i, (name, op) in enumerate(zip(names, opamps), start=1):
        H, tf_names = response_func(name)
        comp = {k: float(f"{v:.4g}") for k, v in random_values(tf_names, rng).items()}
        row = {"topology": name, **{k: v for k, v in comp.items() if k[0] in "RC" and k != "Ro"}}
        secs.append({"n": i, "row": row, "eval_opamp": op, "H": H, "names": tf_names,
                     "sec": {"stage_num": i, "f0_hz": 1e3 * (i + 0.5), "Q": 0.8,
                             "notch": False, "order": 2}})
    return secs


def check_4_netlist():
    print("\n[4] netlist round trip (independent SPICE-semantics solve)")
    rng = np.random.default_rng(4)
    tl = oplib.named_params("TL072") if oplib.get("TL072") else dict(A_ol=2e5, GBWP_hz=3e6, Ro=50e-6)
    cascades = [
        (["3LPn-gained", "2HP-MFB-QE", "3HPn-AM"], [tl, oplib.IDEAL_PARAMS, tl]),
        (["2BP1LP-MFB-QE", "1HP-inv-gained", "2N-AM-C1s", "2N"], [tl, tl, tl, oplib.IDEAL_PARAMS]),
        (["1LP-ni-atten", "2LPn-MFB-LS+R1+R7", "2HPn-MFB2+R7", "2BP-AM2"], [tl] * 4),
    ]
    f = np.logspace(1, 6, 40)
    for names, ops in cascades:
        secs = demo_sections(rng, names, ops)
        exp = SX.build_export(secs, vs=5.0, mc_params=dict(r_bands=[(0, 1e4, 1.0), (1e4, 1e12, 0.1)],
                                                           c_tol_pct=5.0, n_runs=200, dist="gaussian"),
                              spec="demo")
        for fname, text in exp["files"].items():
            if not fname.endswith(".cir"):
                continue
            try:
                text.encode("ascii")
            except UnicodeEncodeError:
                fail(f"{fname}: non-ASCII text")
            h_net = netlist_response(text, f)
            h_ir = SC.mna_ac(exp["cascade"], f)
            err = np.max(np.abs(h_net - h_ir)) / np.max(np.abs(h_ir))
            if not err <= 1e-6:       # A_ol 1e9 (Ideal clamp) round-off ~1e-7
                fail(f"{'+'.join(names)} {fname}: netlist vs IR {err:.2e}")
            else:
                ok(f"{fname:<22} {'+'.join(names)}: |dH|/max = {err:.1e}")
        for fname in exp["files"]:
            if fname.endswith(".cir"):
                txt = exp["files"][fname]
                if not txt.rstrip().lower().endswith(".end"):
                    fail(f"{fname}: missing .end")
                if "\r\n" not in txt:
                    fail(f"{fname}: expected CRLF line endings")


def check_5_mc_bands():
    print("\n[5] MC band mapping")
    bands = [(0.0, 1e3, 0.5), (1e3, 1e5, 1.0), (1e5, 1e12, 0.1)]
    rng = np.random.default_rng(5)
    for _ in range(3000):
        v = 10 ** rng.uniform(-6, 1)              # MOhm
        want = hw_plots._r_tol_frac(v, bands, 1.0)
        i = SX.r_band_index(v * 1e6, bands)
        got = (bands[i][2] if i is not None else 1.0) / 100.0
        if abs(got - want) > 0:
            fail(f"band of {v} MOhm: {got} != {want}")
            return
    for edge in (1e3, 1e5):                        # inclusive ends, first match wins
        v = edge / 1e6
        assert (bands[SX.r_band_index(edge, bands)][2] / 100.0
                == hw_plots._r_tol_frac(v, bands, 1.0)), edge
    ok("3000 random values + band edges map like hw_plots._r_tol_frac")


def check_6_loading():
    print("\n[6] loading (informational) and buffered cascade")
    rng = np.random.default_rng(6)
    lmv = dict(A_ol=1e5, GBWP_hz=1e6, Ro=1200e-6)
    for names in (["2LP-gained", "3LP-MFB", "2HP-unity"], ["3LPn-AM", "2BP-MFB", "1LP-inv-atten"]):
        secs = demo_sections(rng, names, [lmv] * len(names))
        irs = [(d["n"], SC.section_ir(d["row"], SC.opamp_params(d["eval_opamp"])),
                SC.display_alias(d["row"]["topology"])) for d in secs]
        f = np.logspace(1, 6, 60)
        w = 2 * np.pi * f
        prod = hw_plots.cascade([hw_plots.realized_response(d["H"], d["names"], d["row"],
                                                            d["eval_opamp"], w) for d in secs])
        buf = SC.mna_ac(SC.cascade_ir(irs, buffers=True), f)
        err = np.max(np.abs(buf - prod)) / np.max(np.abs(prod))
        if not err <= 1e-9:
            fail(f"{'+'.join(names)} buffered cascade vs product {err:.2e}")
        loaded = SC.mna_ac(SC.cascade_ir(irs), f)
        dev = np.max(np.abs(20 * np.log10(np.abs(loaded) / np.abs(prod))))
        ok(f"{'+'.join(names)}: buffered == product ({err:.1e}); loaded deviation "
           f"max {dev:.3f} dB (Ro 1.2 kOhm)")


def check_7_ideal_clamp():
    print("\n[7] FS generic Ideal clamp")
    rng = np.random.default_rng(7)
    f = np.logspace(0, 6, 50)
    w = 2 * np.pi * f
    worst_db, worst_rel = 0.0, 0.0
    for name in ("2LP-gained", "3HPn-gained+R8", "2BP-MFB-QE", "2LPn-AM", "1HP-ni-gained",
                 "2N-MFB", "3LPn-MFB-LS+R7", "3HPn-AM", "2HPn-MFB2+R7"):
        H, tf_names = response_func(name)
        comp = random_values(tf_names, rng)
        comp.update(oplib.IDEAL_PARAMS)
        row = {"topology": name, **{k: v for k, v in comp.items() if k[0] in "RC" and k != "Ro"}}
        h = SC.mna_ac(SC.section_ir(row, SX.fs_generic_params(oplib.IDEAL_PARAMS)), f)
        ref = H(comp, w)
        top = np.abs(ref) >= np.max(np.abs(ref)) / 100.0         # within 40 dB of the peak
        worst_db = max(worst_db, np.max(np.abs(20 * np.log10(np.abs(h[top]) / np.abs(ref[top])))))
        worst_rel = max(worst_rel, np.max(np.abs(h - ref)) / np.max(np.abs(ref)))
    # The clamp error is ~ noise gain * (1/A_ol + f/GBWP); random draws reach
    # gains of ~100, so the bound is "invisible", not machine precision.
    if worst_db <= 1e-3 and worst_rel <= 1e-4:
        ok(f"clamped (A_ol 1e9, GBWP 1e13, no Ro) vs IDEAL_PARAMS: <= {worst_db:.1e} dB "
           f"within 40 dB of the peak, |dH|/max|H| <= {worst_rel:.1e}")
    else:
        fail(f"Ideal clamp: {worst_db:.2e} dB, rel {worst_rel:.2e}")


if __name__ == "__main__":
    t0 = time.time()
    check_1_ir_vs_tf()
    check_2_dc_path()
    check_3_format()
    check_4_netlist()
    check_5_mc_bands()
    check_6_loading()
    check_7_ideal_clamp()
    print(f"\n{time.time() - t0:.0f} s")
    if FAILS:
        print(f"\n{len(FAILS)} CHECK(S) FAILED")
        sys.exit(1)
    print("\nALL CHECKS PASSED")
