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
  4b .asc round trip (phase 2): every cell and three cascades drawn by
     auto-layout, re-read, netlisted from the geometry (spice_asc.netlist_lines)
     and solved like 4; a corrupted drawing must fail the export self-check
  5  MC band mapping == hw_plots._r_tol_frac
  6  loading: buffered cascade == tool product; loaded deviation printed
  7  FS generic Ideal clamp vs IDEAL_PARAMS
  8  LTspice_Library contracts: symbols.asc, _FS_generic, _seat_template,
     _cell_template; the dummy check rejects bad dummies
  10 real op-amp models (phase 4 plumbing): dummies with an embedded .subckt
     + pin-order wrapper, an external .lib, and a custom .asy symbol, in a
     temporary user overlay -- the .cir and the .asc must both solve to the IR
  11 hand-drawn cell templates (phase 3): every variant of every templated
     cell, plain and with split C1 / C2, drawn from its template: re-read and
     solved = IR, no loose stubs or lone labels; every library op-amp seated
  12 vendor model files: not installed (usable, warned), simplified generic,
     install from a nested vendor zip (path-safe, rejections), consent-gated
     bundling (= IR); spec brief / HF note in the headers, .save V(OUT) in the
     nominal .asc; the wider auto-layout passes its self-check
"""
import io
import math
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile

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
import spice_asc as SA               # noqa: E402
import spice_cells as SC             # noqa: E402
import spice_export as SX            # noqa: E402
import spice_opamps as SO            # noqa: E402
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


MC = dict(r_bands=[(0, 1e4, 1.0), (1e4, 1e12, 0.1)], c_tol_pct=5.0, n_runs=200, dist="gaussian")
TL = dict(A_ol=2e5, GBWP_hz=3e6, Ro=50e-6)
CASCADES = [
    ["3LPn-gained", "2HP-MFB-QE", "3HPn-AM"],
    ["2BP1LP-MFB-QE", "1HP-inv-gained", "2N-AM-C1s", "2N"],
    ["1LP-ni-atten", "2LPn-MFB-LS+R1+R7", "2HPn-MFB2+R7", "2BP-AM2"],
]


def opamp_dummies(exp):
    by_stage = {inf["n"]: inf["dummy"] for inf in exp["sections"]}
    return {o["name"]: by_stage[o["stage"]] for o in exp["cascade"]["opamps"]}


def drawing_netlist(text, exp, extra=()):
    """The drawing's LTspice-style netlist (+ model file text) as one string."""
    lines = SA.netlist_lines(text, SO.calibration(), opamp_dummies(exp))
    return "\r\n".join(lines[:1] + list(extra) + lines[1:])


def element_set(text):
    """Top-level R / C / V / X lines, upper-cased, order-free; an R / C's two
    nodes in either order (a template draws a part whichever way round)."""
    _p, _s, top = _flatten(text)
    out = set()
    for tk in top:
        t = [x.upper() for x in tk]
        if t[0][0] in "RC":
            t[1:3] = sorted(t[1:3])
        out.add(tuple(t))
    return out


def check_4b_asc():
    print("\n[4b] .asc round trip (auto-layout, geometry -> netlist -> solve)")
    rng = np.random.default_rng(41)
    f = np.logspace(1, 6, 25)
    t0, worst, n_ok = time.time(), 0.0, 0
    for name in SC.all_cell_names():
        exp = SX.build_export(demo_sections(rng, [name], [TL]), mc_params=MC, spec="cell",
                              templates=False)
        if exp["asc_error"]:
            fail(f"{name}: {exp['asc_error']}")
            continue
        h_ir = SC.mna_ac(exp["cascade"], f)
        for fn in ("cell_AC.asc", "cell_AC_MC.asc"):
            text = exp["files"][fn]
            text.encode("ascii")
            if "\r\n" not in text:
                fail(f"{name} {fn}: expected CRLF")
            err = (np.max(np.abs(netlist_response(drawing_netlist(text, exp), f) - h_ir))
                   / np.max(np.abs(h_ir)))
            worst = max(worst, err)
            if not err <= 1e-6:
                fail(f"{name} {fn}: drawing vs IR {err:.2e}")
        n_ok += 1
    ok(f"{n_ok} of 92 cells auto-laid-out: re-read and solved = IR, worst |dH|/max = {worst:.1e} "
       f"({time.time() - t0:.0f} s)")

    rng = np.random.default_rng(42)
    for names, ops in zip(CASCADES, ([TL, oplib.IDEAL_PARAMS, TL], [TL, TL, TL, oplib.IDEAL_PARAMS],
                                     [TL] * 4)):
        exp = SX.build_export(demo_sections(rng, names, ops), mc_params=MC, spec="demo")
        if exp["asc_error"]:
            fail(f"{'+'.join(names)}: {exp['asc_error']}")
            continue
        for kind in ("AC", "AC_MC"):
            asc, cir = exp["files"][f"demo_{kind}.asc"], exp["files"][f"demo_{kind}.cir"]
            dn = drawing_netlist(asc, exp)
            if element_set(dn) != element_set(cir):
                diff = element_set(dn) ^ element_set(cir)
                fail(f"{'+'.join(names)} {kind}: drawing lines != .cir lines: {sorted(diff)[:4]}")
            else:
                ok(f"demo_{kind}.asc  {'+'.join(names)}: R/C/V/X lines == .cir")
    # a corrupted drawing must fail the export self-check (§4.3)
    asc = SA.parse(exp["files"]["demo_AC.asc"])
    dums = opamp_dummies(exp)
    SA.check_drawing(asc, exp["cascade"], dums, SO.calibration())       # the good one passes
    bad = 0
    for i, (x, y, n) in enumerate(asc["flags"]):
        if n not in ("0", "VCC", "VEE") and i % 7 == 0:
            other = next(m for _x, _y, m in asc["flags"] if m not in (n, "0", "VCC", "VEE"))
            mut = dict(asc, flags=asc["flags"][:i] + [(x, y, other)] + asc["flags"][i + 1:])
            try:
                SA.check_drawing(mut, exp["cascade"], dums, SO.calibration())
                fail(f"relabelled {n} -> {other} at {(x, y)} passed the self-check")
            except SA.AscError:
                bad += 1
    mut = dict(asc, wires=asc["wires"][1:])
    try:
        SA.check_drawing(mut, exp["cascade"], dums, SO.calibration())
        fail("a missing wire passed the self-check")
    except SA.AscError:
        bad += 1
    ok(f"{bad} corrupted drawings (relabelled nets, a missing wire) rejected by the self-check")


def check_8_library():
    print("\n[8] LTspice_Library contracts and the dummy check")
    cal = SO.calibration()
    if cal.source == "defaults" or cal.errors:
        fail(f"symbols.asc: {cal.source} {cal.errors}")
    for b, (_sym, pins, _o) in ((b, (v[0], v[1], None)) for b, v in SA.CAL_DEFAULT.items()):
        got = cal.pins(b)
        if got != [(n, off) for n, off, _d in pins]:
            print(f"  note {b}: calibrated pins {got} differ from the stock .asy (phase 2b)")
    fs = SO.dummies()[SO.FS_GENERIC]
    roles = [r for r, _ in fs["xpins"] or []]
    if fs["origin"] != "built-in" or fs["errors"] or fs["warnings"]:
        fail(f"_FS_generic.asc: {fs['origin']} {fs['errors']} {fs['warnings']}")
    elif roles != ["INP", "INN", "VCC", "VEE", "OUT"]:
        fail(f"_FS_generic.asc X-line order {roles}")
    else:
        ok("_FS_generic.asc passes the dummy check; X line = INP INN VCC VEE OUT (opamp2 order)")
    lib = SO.library_dir()
    st = SO.load_dummy(os.path.join(lib, "opamps", "_seat_template.asc"))
    if sorted(st["terminals"]) != sorted(SA.SEAT_TERMS) or st["errors"] != ["needs exactly one symbol, found 0"]:
        fail(f"_seat_template.asc: terminals {st['terminals']} errors {st['errors']}")
    else:
        ok("_seat_template.asc: five terminal labels at the seat offsets")
    # cell template: seat R0, terminals on wire ends, the placeholder wired as a follower
    ct = SA.parse(SA.read_text(os.path.join(lib, "cells", "_cell_template.asc")))
    seat = [t for t in ct["texts"] if t["text"].strip() == ";SEAT U1"]
    ends = {w[:2] for w in ct["wires"]} | {w[2:] for w in ct["wires"]}
    o = (seat[0]["x"], seat[0]["y"]) if seat else None
    s = ct["symbols"][0]
    pts = [(n, (s["x"] + dx, s["y"] + dy)) for n, (dx, dy) in cal.pins("opamp2")]
    nets, errs = SA.connectivity(ct["wires"], ct["flags"], pts)
    want = {"INP": "IN", "INN": "OUT", "OUT": "OUT", "VP": "VCC", "VN": "VEE"}
    if (not o or s["orient"] != "R0" or errs or nets != want
            or any((o[0] + dx, o[1] + dy) not in ends for dx, dy in SA.SEAT_TERMS.values())):
        fail(f"_cell_template.asc: seat {o}, {errs}, nets {nets}")
    else:
        ok("_cell_template.asc: seat U1 in R0, terminals on wire ends, placeholder = follower")
    # the dummy check rejects bad dummies
    good = SO.fs_generic_dummy_text()
    bad = {
        "moved terminal": good.replace("FLAG 128 288 INP", "FLAG 128 304 INP"),
        "extra label": good.replace("FLAG 256 128 VCC", "FLAG 256 128 VCC\r\nFLAG 176 240 FOO"),
        "rotated symbol": good.replace("256 192 R0", "256 192 R90"),
        "second symbol": good.replace("SYMATTR Value FS_OA_1",
                                      "SYMATTR Value FS_OA_1\r\nSYMBOL res 300 300 R0"),
        "wire outside the box": good.replace("WIRE 288 256 384 256", "WIRE 288 256 416 256"),
    }
    for what, text in bad.items():
        d = SO.load_dummy("bad.asc", text=text)
        if not d["errors"]:
            fail(f"dummy check accepted a dummy with a {what}")
    ok(f"dummy check rejects {len(bad)} broken dummies ({', '.join(bad)})")
    # a vendor model file not yet installed is a state, not a broken dummy
    d = SO.load_dummy("vend.asc", text=good.replace(
        "TEXT 32 16", "TEXT 32 480 Left 2 !.lib nosuch.lib\r\nTEXT 32 16"))
    if d["errors"] or d["missing"] != ["nosuch.lib"] or SO.vendor_files(d) != {"nosuch.lib": None}:
        fail(f"missing model file: errors {d['errors']} missing {d['missing']}")
    else:
        ok("a dummy whose model file is not installed is usable, flagged missing (vendor_files)")


# ---------------------------------------------------------------------
#  10 — real op-amp models through the dummy library
# ---------------------------------------------------------------------
_VENDOR = ("* vendor-style model, pins: out inn inp vp vn\n"
           ".subckt {name} out inn inp vp vn\n"
           "G1 0 x inp inn 1\nR1 x 0 200k\nC1 x 0 {c}\nE1 y 0 x 0 1\nR2 y out 50\n.ends {name}")
_C = f"{1 / (2 * math.pi * 3e6):.9g}"                   # = TL's GBWP 3 MHz with A_ol 2e5


def _dummy_from_fs(value, directives, meta):
    """An opamp2 dummy with the FS generic adapter wiring (make_opamp_dummy's)."""
    return SO.opamp2_dummy_text(value, meta, directives)


_ASY = """Version 4
SymbolType CELL
LINE Normal -48 -48 -48 48
LINE Normal -48 48 48 0
LINE Normal 48 0 -48 -48
SYMATTR Prefix X
SYMATTR Value FSTEST_OA
PIN 48 0 NONE 8
PINATTR PinName OUT
PINATTR SpiceOrder 1
PIN -48 -16 NONE 8
PINATTR PinName IN-
PINATTR SpiceOrder 2
PIN -48 16 NONE 8
PINATTR PinName IN+
PINATTR SpiceOrder 3
PIN 0 -32 NONE 8
PINATTR PinName V+
PINATTR SpiceOrder 4
PIN 0 32 NONE 8
PINATTR PinName V-
PINATTR SpiceOrder 5
"""


def _custom_symbol_dummy():
    """Custom symbol FSTEST at the seat origin (256, 256); its .asy puts OUT
    first in the SPICE order, so the X-line order must come from the .asy."""
    w = [(304, 256, 384, 256), (208, 240, 160, 240), (160, 240, 160, 224), (160, 224, 128, 224),
         (208, 272, 160, 272), (160, 272, 160, 288), (160, 288, 128, 288),
         (256, 224, 256, 128), (256, 288, 256, 384)]
    L = ["Version 4", "SHEET 1 560 560"] + ["WIRE %d %d %d %d" % x for x in w]
    L += ["FLAG 128 224 INN", "FLAG 128 288 INP", "FLAG 384 256 OUT", "FLAG 256 128 VCC",
          "FLAG 256 384 VEE", "SYMBOL FSTEST 256 256 R0", "SYMATTR InstName U1",
          "TEXT 32 16 Left 2 ;FS: vs_min=4 vs_max=36 note=custom-symbol test dummy",
          "TEXT 32 440 Left 2 !" + _VENDOR.format(name="FSTEST_OA", c=_C).replace("\n", "\\n")]
    return "\r\n".join(L) + "\r\n"


def check_10_real_models():
    print("\n[10] real op-amp models via the dummy library (temporary user overlay)")
    tmp = tempfile.mkdtemp(prefix="fs008_")
    old = os.environ.get("FILTERSYNTHESIZER_LTSPICE_USER_DIR")
    os.environ["FILTERSYNTHESIZER_LTSPICE_USER_DIR"] = tmp
    try:
        od, md = os.path.join(tmp, "opamps"), os.path.join(tmp, "models")
        os.makedirs(od)
        os.makedirs(md)
        vend_b = _VENDOR.format(name="VENDB", c=_C)
        with open(os.path.join(md, "VENDB.lib"), "w", newline="\r\n") as fh:
            fh.write(vend_b + "\n")
        wrapper = (".subckt VENDC_FS inp inn vp vn out\\nX1 out inn inp vp vn VENDC\\n.ends"
                   + "\\n" + _VENDOR.format(name="VENDC", c=_C).replace("\n", "\\n"))
        dummies = {
            "VENDC": _dummy_from_fs("VENDC_FS", [wrapper], "vs_min=9 vs_max=36 note=kind C test"),
            "VENDB": _dummy_from_fs("VENDB_FS", [".lib VENDB.lib",
                                                 ".subckt VENDB_FS inp inn vp vn out\\n"
                                                 "X1 out inn inp vp vn VENDB\\n.ends"],
                                    "note=kind B test"),
            "FSTEST": _custom_symbol_dummy(),
        }
        for stem, text in dummies.items():
            with open(os.path.join(od, stem + ".asc"), "w", newline="") as fh:
                fh.write(text)
        with open(os.path.join(od, "FSTEST.asy"), "w", newline="\r\n") as fh:
            fh.write(_ASY)
        lib = SO.dummies()
        for stem in dummies:
            if lib[stem]["errors"]:
                fail(f"{stem}: {lib[stem]['errors']}")
        x = [r for r, _ in lib["FSTEST"]["xpins"]]
        if x != ["OUT", "INN", "INP", "VCC", "VEE"]:
            fail(f"FSTEST X-line order from its .asy: {x}")
        rng = np.random.default_rng(10)
        f = np.logspace(1, 6, 30)
        names = ["3LPn-gained", "1HP-inv-gained", "2BP-AM2", "2HPn-MFB2+R7"]
        stems = ["VENDC", "FSTEST", "VENDB", None]
        for bundled in (False, True):
            secs = demo_sections(rng, names, [TL] * 4)
            for sd, stem in zip(secs, stems):
                sd["spice_model"] = stem
            if bundled:                    # the user added VENDB.lib with consent
                SO.record_consent(["VENDB.lib"], "test")
            exp = SX.build_export(secs, vs=5.0, mc_params=MC, spec="real")
            if exp["asc_error"] or exp["all_generic"]:
                fail(f"real-model export: asc_error={exp['asc_error']} all_generic={exp['all_generic']}")
                continue
            if not any("VENDC" in w and "supply range" in w for w in exp["warnings"]):
                fail("no supply-range warning for VENDC (vs_min 9 V at Vs = 5 V)")
            if "FSTEST.asy" not in exp["extra_files"] or ("VENDB.lib" in exp["extra_files"]) != bundled:
                fail(f"extra files {sorted(exp['extra_files'])} (bundled={bundled})")
            cir = exp["files"]["real_AC.cir"]
            lib_line = next(ln for ln in cir.splitlines() if ln.lower().startswith(".lib"))
            if bundled != (lib_line == ".lib VENDB.lib"):
                fail(f"model path in the netlist: {lib_line!r} (bundled={bundled})")
            h_ir = SC.mna_ac(exp["cascade"], f)
            for label, text in (("cir", cir),
                                ("asc", drawing_netlist(exp["files"]["real_AC.asc"], exp,
                                                        vend_b.splitlines()))):
                if label == "cir":
                    text = text.replace(lib_line, vend_b)
                err = np.max(np.abs(netlist_response(text, f) - h_ir)) / np.max(np.abs(h_ir))
                if not err <= 1e-6:
                    fail(f"real models, {label} (bundled={bundled}): vs IR {err:.2e}")
                else:
                    ok(f"real_AC.{label}: kind C + custom .asy + kind B .lib + FS generic "
                       f"= IR ({err:.1e}), models {'bundled' if bundled else 'by absolute path'}")
            xl = [ln for ln in cir.splitlines() if ln.startswith("XU")]
            print("       " + " | ".join(xl[:2]))
            try:
                SX.zip_bytes(exp)
            except OSError as e:
                fail(f"zip with extra files: {e}")
    finally:
        if old is None:
            os.environ.pop("FILTERSYNTHESIZER_LTSPICE_USER_DIR", None)
        else:
            os.environ["FILTERSYNTHESIZER_LTSPICE_USER_DIR"] = old
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------
#  12 — vendor model files: missing, simplified, install from a zip, consent
# ---------------------------------------------------------------------
def _zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, blob in members:
            z.writestr(name, blob)
    return buf.getvalue()


def check_12_vendor_models():
    print("\n[12] vendor model files (missing / simplified / install / consent) + headers")
    tmp = tempfile.mkdtemp(prefix="fs008v_")
    user = os.path.join(tmp, "user")
    old = os.environ.get("FILTERSYNTHESIZER_LTSPICE_USER_DIR")
    os.environ["FILTERSYNTHESIZER_LTSPICE_USER_DIR"] = user
    try:
        md = SO.ensure_user_dirs()
        if not all(os.path.isdir(os.path.join(user, s)) for s in ("opamps", "cells", "models")) \
                or not os.path.isfile(os.path.join(md, "README.txt")):
            fail("ensure_user_dirs did not create opamps/ cells/ models/ + README")
        vend = _VENDOR.format(name="VENDZ", c=_C)
        with open(os.path.join(user, "opamps", "VENDZ.asc"), "w", newline="") as fh:
            fh.write(_dummy_from_fs("VENDZ_FS", [".lib vendz.lib",
                                                 ".subckt VENDZ_FS inp inn vp vn out\\n"
                                                 "X1 out inn inp vp vn VENDZ\\n.ends"],
                                    "source=https://example.com/vendz note=kind B missing test"))
        names, stems = ["3LPn-gained", "2BP-AM2"], ["VENDZ", None]

        def export(**kw):
            secs = demo_sections(np.random.default_rng(12), names, [TL] * 2)
            for sd, stem in zip(secs, stems):
                sd["spice_model"] = stem
            return SX.build_export(secs, vs=5.0, mc_params=MC, spec="vend", **kw)

        # 1. not installed: usable, referenced by name, warned, nothing bundled
        exp = export()
        v = exp["vendor"].get("VENDZ", {})
        cir = exp["files"]["vend_AC.cir"]
        if (v.get("missing") != ["vendz.lib"] or exp["extra_files"] or ".lib vendz.lib" not in cir
                or not any("not installed" in w for w in exp["warnings"])):
            fail(f"missing vendor file: vendor {v} extra {sorted(exp['extra_files'])}")
        else:
            ok("vendor file not installed: dummy used, '.lib vendz.lib' by name, warning, no bundle")
        # 2. simplified generic models
        exp = export(generic_vendor=True)
        s1 = exp["sections"][0]
        if not (exp["all_generic"] and s1["fs_generic"] and s1["simplified"] == "VENDZ"
                and not exp["vendor"] and not any("VENDZ" in w for w in exp["warnings"])):
            fail(f"generic_vendor: all_generic {exp['all_generic']} section 1 {s1['stem']} "
                 f"simplified {s1['simplified']} warnings {exp['warnings']}")
        else:
            ok("simplified generic models: the vendor part's section uses FS generic, no warning")
        # 3. install from the vendor's zip (nested one deep), path-safe
        inner = _zip([("Models/VENDZ.LIB", vend + "\n"), ("readme.txt", "x")])
        outer = _zip([("PSpice/sbom_vendz.zip", inner), ("../../evil.lib", ".subckt EVIL a b\n.ends"),
                      ("VENDZ.OLB", "binary symbol")])
        got = SO.install_model("sbom_vendz.zip", outer, ["vendz.lib"])
        listed = sorted(os.listdir(md))
        evil = [os.path.join(dp, f) for dp, _dn, fn in os.walk(tmp) for f in fn if f == "evil.lib"]
        if got != ["vendz.lib"] or listed != ["README.txt", "vendz.lib"] or evil:
            fail(f"install_model: {got}, models/ = {listed}, evil = {evil}")
        else:
            ok("install_model: vendz.lib taken from a nested vendor zip; nothing else extracted "
               "(path-traversal member ignored)")
        bad = {"encrypted": ("x.lib", b"$CDNENCSTART\n.subckt X a b\n"),
               "no .subckt": ("x.lib", b"* just text\n"),
               "zip without it": ("y.zip", _zip([("other.lib", ".subckt O a b\n.ends")]))}
        for what, (nm, blob) in bad.items():
            try:
                SO.install_model(nm, blob, ["vendz.lib"])
                fail(f"install_model accepted a file with {what}")
            except ValueError:
                pass
        ok(f"install_model rejects: {', '.join(bad)}")
        # 4. installed, no consent: absolute path, not bundled
        exp = export()
        v = exp["vendor"]["VENDZ"]
        if v["unconsented"] != ["vendz.lib"] or exp["extra_files"]:
            fail(f"installed without consent: {v}, extra {sorted(exp['extra_files'])}")
        else:
            ok("installed without consent: referenced by absolute path, not bundled")
        # 5. consent: bundled by bare name, README notice, solves = IR
        SO.record_consent(["vendz.lib"], "https://example.com/vendz")
        hf = {"db": 12.0, "f_hz": 85e3, "am_sections": [2]}
        exp = export(spec_brief=["Spec: Elliptic lowpass, order 5; fc = 1 kHz"], hf_hump=hf)
        cir, rd = exp["files"]["vend_AC.cir"], exp["files"]["README.txt"]
        if ("vendz.lib" not in exp["extra_files"] or ".lib vendz.lib" not in cir
                or "DO NOT SHARE" not in rd or exp["vendor"]["VENDZ"]["bundled"] != ["vendz.lib"]):
            fail(f"consented bundle: extra {sorted(exp['extra_files'])}")
        else:
            f = np.logspace(1, 6, 30)
            h_ir = SC.mna_ac(exp["cascade"], f)
            err = np.max(np.abs(netlist_response(cir.replace(".lib vendz.lib", vend), f) - h_ir)) \
                / np.max(np.abs(h_ir))
            (ok if err <= 1e-6 else fail)(f"consented: vendz.lib bundled by bare name, README "
                                          f"notice, netlist = IR ({err:.1e})")
        # 6. headers: spec brief + HF note in both .asc and the README; .save V(OUT)
        asc, asc_mc = exp["files"]["vend_AC.asc"], exp["files"]["vend_AC_MC.asc"]
        miss = [n for n, t in (("AC.asc", asc), ("AC_MC.asc", asc_mc), ("README", rd), ("AC.cir", cir))
                if "Spec: Elliptic lowpass" not in t or "HF hump" not in t or "lower Ro" not in t]
        if miss:
            fail(f"spec brief / HF note missing in {miss}")
        elif ".save V(OUT)" not in asc or ".save V(OUT)" in cir:
            fail("nominal .asc must carry .save V(OUT); the frozen nominal .cir must not")
        else:
            ok("spec brief + HF-hump recommendation in every header; .save V(OUT) in the nominal .asc")
        # 7. wider auto-layout still passes its self-check
        exp = export(templates=False)
        if exp["asc_error"] or any(i["drawing"] != "auto-layout" for i in exp["sections"]):
            fail(f"auto-layout export: {exp['asc_error']}")
        else:
            ok(f"auto-layout (pitch {SA.PART_PITCH}, stubs {SA.LABEL_STUB}) passes the export self-check")
    finally:
        if old is None:
            os.environ.pop("FILTERSYNTHESIZER_LTSPICE_USER_DIR", None)
        else:
            os.environ["FILTERSYNTHESIZER_LTSPICE_USER_DIR"] = old
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------
#  11 — hand-drawn cell templates x every variant (phase 3)
# ---------------------------------------------------------------------
def loose_ends(asc, cal, dummies):
    """Wire ends that reach nothing (no pin, label, other wire end or T) and
    labels on nothing -- the stubs gating must not leave behind."""
    pins = set()
    for s in asc["symbols"]:
        inst = SA.attr(s, "InstName")
        if inst in dummies:
            pins |= {pt for _r, pt in SA._xpoints(s, dummies[inst]) if pt is not None}
            # an op-amp's unused pins are not stubs; its adapter wires end on them
            pins |= {(s["x"] + dx, s["y"] + dy) for _n, (dx, dy) in
                     (cal.pins(SA.sym_base(s["sym"])) or [])}
        else:
            pins |= {(s["x"] + dx, s["y"] + dy) for _n, (dx, dy) in
                     cal.pins(SA.sym_base(s["sym"]), s["orient"])}
    deg = {}
    for w in asc["wires"]:
        for p in (w[:2], w[2:]):
            deg[p] = deg.get(p, 0) + 1
    fpts = {(x, y) for x, y, _n in asc["flags"]}
    on_mid = lambda p: any(SA._between(p, w[:2], w[2:]) for w in asc["wires"])  # noqa: E731
    bad = [p for p, k in deg.items() if k == 1 and p not in pins and p not in fpts
           and not on_mid(p)]
    bad += [f for f in asc["flags"] if f[:2] not in deg and f[:2] not in pins
            and not on_mid(f[:2])]
    return bad


def _split_rows(row, sup):
    """The row as the BOM may carry it: plain, C1 split, C2 split."""
    present = {k for k, kind, _a, _b, st in sup["entries"] if kind == "C" and st is True}
    out = [("", row)]
    for c in ("C1", "C2"):
        if c in present and c in row:
            r = {k: v for k, v in row.items() if k != c}
            r[c + "a"], r[c + "b"] = float(f"{row[c] * 0.6:.4g}"), float(f"{row[c] * 0.4:.4g}")
            r[c] = r[c + "a"] + r[c + "b"]
            out.append((f" {c}a+{c}b", r))
    return out


def check_11_templates():
    print("\n[11] hand-drawn cell templates x every variant (gating, seats, pruning)")
    tids = {SC.superset(n)["template"] for n in SC.all_cell_names()}
    files = SO.cell_templates()
    for stem in sorted(set(files) - tids):
        fail(f"cells/{stem}.asc: no cell uses a template of that name")
    for stem, t in sorted(files.items()):
        if t["error"]:
            fail(f"cells/{stem}.asc unreadable: {t['error']}")
    have = sorted(t for t in tids if t in files)
    print(f"       templates: {', '.join(have)}")
    print(f"       auto-layout (no template): {', '.join(sorted(tids - set(files)))}")
    rng = np.random.default_rng(11)
    f = np.logspace(1, 6, 25)
    n_ok, worst, bad_tpl = 0, 0.0, {}
    for name in SC.all_cell_names():
        sup = SC.superset(name)
        if sup["template"] not in files:
            continue
        base = demo_sections(rng, [name], [TL])
        for tag, row in _split_rows(base[0]["row"], sup):
            secs = [dict(base[0], row=row)]
            exp = SX.build_export(secs, mc_params=MC, spec="cell")
            drawn = exp["sections"][0]["drawing"]
            if exp["asc_error"] or not drawn.startswith("template"):
                bad_tpl.setdefault(sup["template"], []).append(
                    f"{name}{tag}: {exp['asc_error'] or drawn}")
                continue
            h_ir = SC.mna_ac(exp["cascade"], f)
            for fn in ("cell_AC.asc", "cell_AC_MC.asc"):
                text = exp["files"][fn]
                err = (np.max(np.abs(netlist_response(drawing_netlist(text, exp), f) - h_ir))
                       / np.max(np.abs(h_ir)))
                worst = max(worst, err)
                if not err <= 1e-6:
                    fail(f"{name}{tag} {fn}: template drawing vs IR {err:.2e}")
            asc = SA.parse(exp["files"]["cell_AC.asc"])
            lo = loose_ends(asc, SO.calibration(), opamp_dummies(exp))
            if lo:
                fail(f"{name}{tag}: loose wire ends / lone labels left by gating: {lo[:4]}")
            n_ok += 1
    for tid, msgs in sorted(bad_tpl.items()):
        fail(f"template {tid} rejected ({len(msgs)} variant(s)), e.g. {msgs[0]}")
    ok(f"{n_ok} cell variants (plain + split C1 / C2) drawn from templates: re-read and "
       f"solved = IR (worst {worst:.1e}), no loose stubs")

    # the checks above can fail: a planted stub is seen, a mislabelled template rejected
    if "SK_LP" in files:
        exp = SX.build_export(demo_sections(rng, ["2LP-unity"], [TL]), mc_params=MC, spec="neg")
        asc = SA.parse(exp["files"]["neg_AC.asc"])
        w = asc["wires"][0]
        asc["wires"].append(w[:2] + (w[0] - 48, w[1]) if w[0] == w[2] else w[:2] + (w[0], w[1] - 48))
        if not loose_ends(asc, SO.calibration(), opamp_dummies(exp)):
            fail("loose_ends missed a planted stub")
        tpl = files["SK_LP"]["asc"]
        mut = dict(tpl, flags=[(x, y, "b" if n.lower() == "c" else n) for x, y, n in tpl["flags"]])
        sd = demo_sections(rng, ["2LP-gained"], [TL])[0]
        ir = SC.section_ir(sd["row"], dict(SX.fs_generic_params(TL), subckt="FS_OA_1"))
        casc = SC.cascade_ir([(1, ir, {})])
        sup, nm = SC.gating("2LP-gained")
        vals = {p["name"]: "1k" for p in casc["parts"]}
        dums = {o["name"]: SO.fs_generic() for o in casc["opamps"]}
        net = lambda n: SC.cascade_net(casc, 1, n)  # noqa: E731
        for label, t in (("good", tpl), ("relabelled c -> b", mut)):
            try:
                blk = SA.draw_template(t, sup, nm, casc["parts"], casc["opamps"], net, vals,
                                       dums, SO.calibration())
                SA.check_drawing(blk, casc, dums, SO.calibration(), sources=False)
                if label != "good":
                    fail(f"SK_LP template {label} passed")
            except SA.AscError as e:
                if label == "good":
                    fail(f"SK_LP template rejected: {e}")
        ok("negative tests: a planted stub is found, a mislabelled template is rejected")

    # every usable library op-amp in a template seat (geometry differs per dummy)
    lib = SO.dummies()
    stems = [s for s, d in lib.items() if not d["errors"] and s != SO.FS_GENERIC]
    for stem in stems:
        secs = demo_sections(rng, ["2LP-gained", "2BP1HP-MFB"], [TL, TL])
        for sd in secs:
            sd["spice_model"] = stem
        exp = SX.build_export(secs, mc_params=MC, spec="lib")
        kinds = [inf["drawing"] for inf in exp["sections"]]
        if exp["asc_error"] or not all(k.startswith("template") for k in kinds):
            fail(f"{stem} in template seats: {exp['asc_error'] or kinds}")
            continue
        dn = drawing_netlist(exp["files"]["lib_AC.asc"], exp)
        if element_set(dn) != element_set(exp["files"]["lib_AC.cir"]):
            fail(f"{stem} in template seats: drawing lines != .cir lines")
        else:
            ok(f"{stem} ({lib[stem]['origin']}) in template seats: R/C/V/X lines == .cir")


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
    check_4b_asc()
    check_5_mc_bands()
    check_6_loading()
    check_7_ideal_clamp()
    check_8_library()
    check_10_real_models()
    check_11_templates()
    check_12_vendor_models()
    print(f"\n{time.time() - t0:.0f} s")
    if FAILS:
        print(f"\n{len(FAILS)} CHECK(S) FAILED")
        sys.exit(1)
    print("\nALL CHECKS PASSED")
