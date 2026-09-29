# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  spice_export.py  [Tier D — FS-008 LTspice writer, no Streamlit]
#
#  Writes, in ONE export cycle from ONE IR, LTspice-24 netlists (.cir, the
#  phase-1 format) AND schematics (.asc, spice_asc: a section is drawn from its
#  hand-drawn cell template, LTspice_Library/cells/, else auto-laid-out) of
#  the whole solved cascade -- sections in series, so the real inter-stage
#  loading is simulated -- with a split supply +-Vs/2 around GND, AC nominal
#  and AC Monte Carlo pre-set from the tool's MC settings.
#  Op-amps come from the local model library (spice_opamps): a section whose
#  part has a `spice_model` dummy uses that model in both files; Ideal /
#  Custom / unmapped parts use FS generic (the tool's own A_ol / GBWP / Ro
#  model as an inline .subckt). dev/FS-008_ltspice_export_design_note.md §14.
#
#  Everything here is plain string building from spice_cells' IR, so it is
#  cheap enough to run on every Streamlit render.
# =====================================================================

import datetime
import io
import math
import os
import re
import unicodedata
import zipfile

import numpy as np

import spice_asc as SA
import spice_cells as SC
import spice_opamps as SO
from _version import APP_NAME, __version__

EOL = "\r\n"

# FS generic clamp for the tool's "Ideal" op-amp (A_ol 1e12, GBWP 1e15 Hz,
# Ro 1e-6 ohm would put a 1e12-ohm resistor in LTspice's matrix). The deviation
# is ~noise gain x (1/A_ol + f/GBWP) -- invisible (dev check 7).
A_OL_MAX = 1e9
GBWP_MAX_HZ = 1e13
RO_MIN_OHM = 1e-3                     # below this Ro is omitted (pure VCVS)

AC_POINTS = 600                       # the tool's grid (response_tab._freq_grid)


# =====================================================================
#  Values
# =====================================================================
_SUFFIX = [(1e12, "T"), (1e9, "G"), (1e6, "Meg"), (1e3, "k"), (1.0, ""),
           (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p"), (1e-15, "f")]
_SCALE = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3, "u": 1e-6,
          "n": 1e-9, "p": 1e-12, "f": 1e-15, "mil": 25.4e-6}


def fmt_value(x, digits=6):
    """SPICE engineering text with at most `digits` significant digits:
    4990 -> '4.99k', 1e6 -> '1Meg' (SPICE 'M' is milli), 1e-8 -> '10n'.
    No unit letters (a bare 'F' would be femto)."""
    x = float(x)
    if x == 0.0 or not math.isfinite(x):
        raise ValueError(f"not a component value: {x!r}")
    sign = "-" if x < 0 else ""
    a = float(f"{abs(x):.{digits}g}")            # round first, then pick the scale
    for scale, suf in _SUFFIX:
        if a >= scale * (1 - 1e-12):
            mant = f"{a / scale:.{digits}g}"
            if "e" in mant:                       # >= 1000T: fall back to plain e-form
                break
            return sign + mant + suf
    return sign + f"{a:.{digits}g}"


def parse_value(txt):
    """Inverse of fmt_value (SPICE number with scale suffix; trailing letters
    after the suffix are ignored, as SPICE does)."""
    m = re.fullmatch(r"\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([a-zA-Z]*)\s*", txt)
    if not m:
        raise ValueError(f"bad SPICE number {txt!r}")
    num, suf = float(m.group(1)), m.group(2).lower()
    for key in ("meg", "mil"):
        if suf.startswith(key):
            return num * _SCALE[key]
    return num * _SCALE.get(suf[:1], 1.0) if suf else num


def ascii_text(s):
    """Comment text as plain ASCII (LTspice netlists are read as ASCII)."""
    s = (str(s).replace("µ", "u").replace("μ", "u").replace("Ω", "ohm")
         .replace("σ", "sigma").replace("±", "+-").replace("–", "-").replace("—", "-")
         .replace("≤", "<=").replace("≥", ">=").replace("·", "*"))
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.replace("\r", " ").replace("\n", " ")


def fmt_hz(f):
    return fmt_value(f, 5) + " Hz"


# =====================================================================
#  Op-amp model: FS generic
# =====================================================================
def fs_generic_params(eval_opamp):
    """Tool op-amp dict {A_ol, GBWP_hz, Ro (MOhm)} -> the FS generic model's
    IR params, with the Ideal clamp applied."""
    p = SC.opamp_params(eval_opamp)
    p["A_ol"] = min(p["A_ol"], A_OL_MAX)
    p["GBWP_hz"] = min(p["GBWP_hz"], GBWP_MAX_HZ)
    if p["Ro_ohm"] < RO_MIN_OHM:
        p["Ro_ohm"] = 0.0
    return p


def fs_generic_subckt(name, p):
    """A(s) = A_ol/(1 + s*A_ol/(2*pi*GBWP)) behind Ro (cells_lp.build_nonideal).
    Pins follow LTspice's opamp2 order: In+ In- V+ V- OUT. vp/vn carry no
    element: the model is linear, without rails."""
    c = 1.0 / (2 * math.pi * p["GBWP_hz"])
    lines = [f".subckt {name} inp inn vp vn out",
             f"* FS generic op-amp: A_ol={fmt_value(p['A_ol'])} "
             f"GBWP={fmt_hz(p['GBWP_hz'])} Ro={fmt_value(p['Ro_ohm']) if p['Ro_ohm'] else '0'} ohm",
             "* linear, no supply rails, no noise -- replace with a real model to see those",
             "G1 0 x inp inn 1",
             f"R1 x 0 {fmt_value(p['A_ol'])}",
             f"C1 x 0 {fmt_value(c, 9)}"]        # 9 digits: sets GBWP exactly
    if p["Ro_ohm"]:
        lines += ["E1 y 0 x 0 1", f"R2 y out {fmt_value(p['Ro_ohm'])}"]
    else:
        lines += ["E1 out 0 x 0 1"]
    return lines + [".ends"]


# =====================================================================
#  Monte Carlo mapping (hw_plots.monte_carlo)
# =====================================================================
def r_band_index(r_ohm, r_bands):
    """Index of the first band containing r_ohm (inclusive ends), else None --
    the same rule as hw_plots._r_tol_frac, which then uses its 1 % default."""
    for i, (lo, hi, _tol) in enumerate(r_bands):
        if lo <= r_ohm <= hi:
            return i
    return None


DEFAULT_R_TOL_PCT = 1.0               # hw_plots.monte_carlo default_r_tol_pct


def _band_param(i):
    return "tR0" if i is None else f"tR{i + 1}"


def mc_block(r_bands, c_tol_pct, n_runs, dist, used_bands):
    lo_hi = lambda v: "inf" if v >= 1e12 else fmt_value(v) if v else "0"  # noqa: E731
    out = ["* ---- Monte Carlo (taken from the tool's Monte-Carlo settings) ----",
           "* One tolerance per resistor value band plus one for capacitors; every",
           "* part draws independently. Edit a tolerance, the distribution or the",
           "* run count here. Op-amps are not varied (as in the tool).",
           f".param tC={c_tol_pct / 100.0:g}"]
    for i, (lo, hi, tol) in enumerate(r_bands):
        if i in used_bands:
            out.append(f".param tR{i + 1}={tol / 100.0:g}")
            out.append(f"* tR{i + 1}: resistors {lo_hi(lo)} ohm <= R <= {lo_hi(hi)} ohm")
    if None in used_bands:
        out.append(f".param tR0={DEFAULT_R_TOL_PCT / 100.0:g}")
        out.append("* tR0: resistors outside every band (the tool's default)")
    gauss = ".func TOL(nom,tol) {nom*(1+gauss(tol/3))}"
    flat = ".func TOL(nom,tol) {nom*(1+flat(tol))}"
    out.append("* Gaussian: tol = 3 sigma, untruncated | Uniform: +-tol "
               "(swap the comment to switch)")
    if dist == "uniform":
        out += ["* " + gauss, flat]
    else:
        out += [gauss, "* " + flat]
    out += [f".step param run 1 {int(n_runs)} 1",
            ".save V(OUT)"]
    return out


# =====================================================================
#  Analyses (hooks for FS-026 transient)
# =====================================================================
def source_value(src):
    """VIN value text from a source spec. v1: {'kind': 'ac'}."""
    if src.get("kind") == "ac":
        return "AC 1"
    raise NotImplementedError(f"source kind {src.get('kind')!r} (FS-026)")


def ac_grid(sections):
    """(fmin, fmax, points per decade) -- the tool's grid, response_tab._freq_grid."""
    fl = []
    for sd in sections:
        s = sd["sec"]
        fl.append(float(s["f0_hz"]))
        if s.get("notch") and s.get("fz_hz"):
            fl.append(float(s["fz_hz"]))
        if s.get("order") == 3 and s.get("f1_hz"):
            fl.append(float(s["f1_hz"]))
    fmin = max(1e-3, 0.1 * min(fl))
    fmax = 50.0 * max(fl)
    ppd = int(math.ceil(AC_POINTS / math.log10(fmax / fmin)))
    return fmin, fmax, ppd


def directives(analysis, grid=None, window=None):
    if analysis == "ac":
        fmin, fmax, ppd = grid
        return [f".ac dec {ppd} {fmt_value(fmin, 5)} {fmt_value(fmax, 5)}"]
    raise NotImplementedError(f"analysis {analysis!r} (FS-026 needs FS-024's window)")


# =====================================================================
#  Cascade assembly
# =====================================================================
def _section_title(sd):
    s, row = sd["sec"], sd["row"]
    txt = f"Section {sd['n']} - {row.get('topology')}  f0={fmt_hz(s['f0_hz'])}"
    if s.get("Q"):
        txt += f" Q={float(s['Q']):.4g}"
    if s.get("notch") and s.get("fz_hz"):
        txt += f" fz={fmt_hz(s['fz_hz'])}"
    return ascii_text(txt)


def _db(h):
    return 20.0 * np.log10(np.maximum(np.abs(h), 1e-300))


def _probes(sections, grid, loaded):
    """[(name, f)] of .meas probe frequencies inside the sweep: the sweep ends,
    each section's f0 / fz, and the loaded response's peak."""
    fmin, fmax, _ = grid
    cand = [("fmin", fmin * 1.0001)]
    for sd in sections:
        s, n = sd["sec"], sd["n"]
        cand.append((f"f0_s{n}", float(s["f0_hz"])))
        if s.get("notch") and s.get("fz_hz"):
            cand.append((f"fz_s{n}", float(s["fz_hz"])))
    f_pk, _h = loaded
    cand += [("fpeak", f_pk), ("fmax", fmax / 1.0001)]
    out = []
    for name, f in cand:
        if not (fmin <= f <= fmax):
            continue
        if any(abs(math.log10(f / g)) < 1e-4 for _, g in out):
            continue
        out.append((name, f))
    return out


def build_export(sections_data, vs=5.0, mc_params=None, spec="filter",
                 n_runs=None, now=None, include_models=False, templates=True):
    """Build the LTspice bundle.

    sections_data : response_tab's list, stage order; each item has
                    n, sec (hw_sections entry), row (snapped BOM row),
                    eval_opamp ({A_ol, GBWP_hz, Ro MOhm}), and optionally
                    opamp_label (the part name shown in the tool) and
                    spice_model (a dummy stem of the op-amp model library;
                    None / missing / unusable -> FS generic).
    mc_params     : response_tab's mc_params (r_bands, c_tol_pct, n_runs, dist).
    include_models: copy the model files the dummies reference into the zip
                    and reference them by bare name (else absolute paths).
    templates     : draw a section from its hand-drawn cell template
                    (LTspice_Library/cells/<template>.asc) when there is one;
                    False = auto-layout for every section.
    Returns {files: {name: text}, extra_files: {name: path}, zip_name,
             cascade (IR), sections: [info], warnings: [str],
             expected: [(probe, f, loaded, unloaded)], all_generic, asc_error}.
    Raises ValueError for a row the IR cannot represent."""
    mc_params = dict(mc_params or {})
    r_bands = [tuple(b) for b in (mc_params.get("r_bands") or [(0.0, 1e12, DEFAULT_R_TOL_PCT)])]
    c_tol = float(mc_params.get("c_tol_pct", 5.0))
    runs = int(n_runs or mc_params.get("n_runs", 2000))
    dist = mc_params.get("dist", "gaussian")
    now = now or datetime.datetime.now()
    spec = re.sub(r"[^A-Za-z0-9_.-]+", "_", ascii_text(spec)) or "filter"

    # ---- IR per section, op-amp models, cascade ----
    lib = SO.dummies()
    fsd = lib[SO.FS_GENERIC]
    models, real, stage_dummy = {}, {}, {}
    irs_tool, irs_spice, info, warnings = [], [], [], []
    for w in fsd["warnings"]:
        warnings.append(f"Op-amp library: {w}")
    for sd in sections_data:
        row, n = sd["row"], sd["n"]
        p = fs_generic_params(sd["eval_opamp"])
        stem = sd.get("spice_model") or SO.FS_GENERIC
        dm = lib.get(stem)
        if stem != SO.FS_GENERIC and (dm is None or dm["errors"]):
            why = ("is not in the op-amp model library" if dm is None else
                   "fails the dummy check (" + "; ".join(dm["errors"]) + ")")
            warnings.append(f"Section {n}: SPICE model '{stem}' {why} -- FS generic used")
            dm = None
        if dm is None or dm["fs_generic"]:
            key = (p["A_ol"], p["GBWP_hz"], p["Ro_ohm"])
            if key not in models:
                models[key] = (f"FS_OA_{len(models) + 1}", p)
            dm, xmodel = fsd, models[key][0]
        else:
            real.setdefault(dm["stem"], dm)
            xmodel = dm["xmodel"]
            meta = dm["meta"]
            if (meta.get("vs_min") and vs < meta["vs_min"]) or (meta.get("vs_max") and vs > meta["vs_max"]):
                warnings.append(f"Section {n}: Vs = {vs:g} V is outside {dm['stem']}'s supply "
                                f"range {meta.get('vs_min', '?')} .. {meta.get('vs_max', '?')} V")
        stage_dummy[n] = dm
        alias = SC.display_alias(row.get("topology"))
        ir_tool = SC.section_ir(row, SC.opamp_params(sd["eval_opamp"]))
        ir_sp = SC.section_ir(row, dict(p, subckt=xmodel))
        irs_tool.append((n, ir_tool, alias))
        irs_spice.append((n, ir_sp, alias))
        fl = SC.dc_floating_nodes(ir_sp)
        for w in ir_sp["warnings"]:
            warnings.append(f"Section {n}: {w}")
        if fl:
            warnings.append(f"Section {n} ({row.get('topology')}): no DC path at "
                            f"{', '.join(fl)} -- a real op-amp model will not bias there")
        info.append({"n": n, "topology": row.get("topology"),
                     "opamp_label": ascii_text(sd.get("opamp_label") or ""),
                     "model": xmodel, "stem": dm["stem"], "fs_generic": dm["fs_generic"],
                     "dummy": dm, "params": p, "drawing": "auto-layout",
                     "template": ir_sp["template"], "floating": fl})
    casc = SC.cascade_ir(irs_spice)
    all_generic = not real
    tpls = {}
    for inf in info if templates else ():
        t = SO.cell_template(inf["template"])
        if t is not None:
            sup, node_map = SC.gating(inf["topology"])
            tpls[inf["n"]] = dict(t, sup=sup, node_map=node_map)
    drawn = {}
    dummy_of = {o["name"]: stage_dummy[o["stage"]] for o in casc["opamps"]}

    # ---- expected values: loaded (what LTspice solves) vs the tool (unloaded) ----
    grid = ac_grid(sections_data)
    fmin, fmax, ppd = grid
    f_sweep = np.logspace(np.log10(fmin), np.log10(fmax), 800)
    h_loaded = SC.mna_ac(casc, f_sweep)
    i_pk = int(np.argmax(np.abs(h_loaded)))
    probes = _probes(sections_data, grid, (float(f_sweep[i_pk]), h_loaded[i_pk]))
    fp = np.array([f for _, f in probes])
    hl = SC.mna_ac(casc, fp)
    hu = np.ones(fp.size, dtype=complex)
    for _n, ir, _a in irs_tool:
        hu = hu * SC.mna_ac(ir, fp)
    expected = [(name, f, complex(a), complex(b))
                for (name, f), a, b in zip(probes, hl, hu)]
    h_tool = np.prod([SC.mna_ac(ir, f_sweep) for _n, ir, _a in irs_tool], axis=0)
    band = _db(h_tool) >= np.max(_db(h_tool)) - 60.0      # ignore deep nulls
    dev = float(np.max(np.abs(_db(h_loaded[band]) - _db(h_tool[band]))))

    # ---- shared pieces: the .cir and the .asc are built from the same ones ----
    def values(mc):
        vals, used = {}, set()
        for el in casc["parts"]:
            val = fmt_value(el["value"])
            if mc:
                if el["kind"] == "R":
                    b = r_band_index(el["value"], r_bands)
                    used.add(b)
                    val = f"{{TOL({val},{_band_param(b)})}}"
                else:
                    val = f"{{TOL({val},tC)}}"
            vals[el["name"]] = val
        return vals, used

    def x_line(o):
        nets = {"INP": o["inp"], "INN": o["inn"], "OUT": o["out"],
                "VCC": "VCC", "VEE": "VEE", "0": "0"}
        return f"X{o['name']} {' '.join(SO.x_args(dummy_of[o['name']], nets, o['name']))} " \
               f"{o['subckt']}"

    def model_note(inf):
        if inf["fs_generic"]:
            return f"{inf['model']} (FS generic)"
        return f"{inf['stem']} ({inf['model']})"

    real_blocks, seen_blocks = [], set()           # [(stem, cir lines, asc blocks)]
    for stem, d in real.items():
        cir = [ln for ln in SO.directives_cir(d, include_models)]
        blocks = [b for b in SO.directives_asc(d, include_models)
                  if tuple(b) not in seen_blocks]
        seen_blocks |= {tuple(b) for b in blocks}
        real_blocks.append((stem, cir, blocks))

    def probe_lines():
        out = []
        for name, f, a, b in expected:
            if all_generic:
                out.append(f"* expected {_db(a):.4f} dB {math.degrees(np.angle(a)):.2f} deg "
                           f"(loaded MNA); tool (unloaded) {_db(b):.4f} dB")
            else:
                out.append(f"* tool (unloaded, its own op-amp model) {_db(b):.4f} dB")
            out.append(f".meas AC G_{name} FIND V(OUT) AT {fmt_value(f, 6)}")
        return out

    def head(mc, ext):
        what = "AC Monte Carlo" if mc else "AC nominal"
        ops = ("Op-amps: FS generic = the tool's own A_ol / GBWP / Ro model (linear, no rails)."
               if all_generic else
               "Op-amps: per section (see below); FS generic = the tool's own A_ol / GBWP / Ro model.")
        return [f"{spec} - {APP_NAME} LTspice export (FS-008) - {what}",
                f"Generated {now:%Y-%m-%d %H:%M} by {APP_NAME} {__version__}.",
                f"{len(sections_data)} section(s) in series (real inter-stage loading), "
                f"input IN, output OUT.",
                ops,
                "Supply: Vs drawn as two Vs/2 sources, GND (node 0) at the midpoint.",
                f"Open in LTspice 24 and Run; plot V(OUT). See README.txt."
                if ext == "cir" else
                "Run, then plot V(OUT). The same circuit as the .cir netlist; see README.txt."]

    def netlist(mc):
        vals, used = values(mc)
        by_stage = {}                               # cascade order: designators final
        for p in casc["parts"]:
            by_stage.setdefault(p["stage"], []).append(p)
        for o in casc["opamps"]:
            by_stage.setdefault(o["stage"], []).append(o)
        sec_lines = []
        for sd, inf in zip(sections_data, info):
            sec_lines += ["*", "* " + _section_title(sd),
                          f"* op-amp: {inf['opamp_label'] or 'Ideal'} -> {model_note(inf)}"]
            for el in by_stage.get(sd["n"], []):
                if "kind" in el:
                    sec_lines.append(f"{el['name']} {el['n1']} {el['n2']} {vals[el['name']]}")
                else:
                    sec_lines.append(x_line(el))
        top = ["* " + ln for ln in head(mc, "cir")] + [
            "*",
            f".param Vs={fmt_value(vs)}",
            "VPOS VCC 0 {Vs/2}",
            "VNEG 0 VEE {Vs/2}",
            f"VIN IN 0 {source_value({'kind': 'ac'})}"]
        tail = ["*", "* ---- op-amp models ----"]
        for name, p in models.values():
            tail += fs_generic_subckt(name, p)
        for stem, cir, _blocks in real_blocks:
            note = ascii_text(lib[stem]["meta"].get("note", ""))
            tail += [f"* {stem}: {lib[stem]['origin']} dummy {lib[stem]['path']}"
                     + (f" -- {note}" if note else "")] + cir
        tail += ["*", "* ---- analysis ----"] + directives("ac", grid)
        if mc:
            tail += ["*"] + mc_block(r_bands, c_tol, runs, dist, used)
        tail += ["*", "* ---- probes: LTspice reports each in the SPICE Error Log "
                      "(View > SPICE Error Log) ----"] + probe_lines()
        tail += ["*", "* Real op-amp models only: run the operating point once and check",
                 "* that every section output sits near 0 V before trusting the AC result.",
                 "* .op", ".end"]
        return EOL.join(top + sec_lines + tail) + EOL

    def drawing(mc):
        vals, used = values(mc)
        titles = {sd["n"]: [_section_title(sd),
                            f"op-amp: {inf['opamp_label'] or 'Ideal'} -> {model_note(inf)}"]
                  for sd, inf in zip(sections_data, info)}
        blocks = [[f".param Vs={fmt_value(vs)}"] + directives("ac", grid)]
        if mc:
            blocks.append(mc_block(r_bands, c_tol, runs, dist, used))
        blocks.append(["* probes: View > SPICE Error Log"] + probe_lines())
        blocks += [fs_generic_subckt(name, p) for name, p in models.values()]
        for _stem, _cir, bl in real_blocks:
            blocks += bl
        comments = [["Real op-amp models only: run the operating point once and check",
                     "that every section output sits near 0 V before trusting the AC result."],
                    [".op"]]
        return SA.draw_cascade(casc, titles, vals, dummy_of, blocks, head(mc, "asc"),
                               SO.calibration(), comment_blocks=comments,
                               templates=tpls, drawn=drawn)

    files = {}
    asc_error = None
    try:
        files[f"{spec}_AC.asc"] = drawing(False)
        files[f"{spec}_AC_MC.asc"] = drawing(True)
    except SA.AscError as e:                        # never write a wrong drawing (§4.3)
        files = {}
        asc_error = str(e)
        warnings.append(f"Schematic (.asc) not written -- the drawing failed its self-check "
                        f"against the netlist ({e}). The .cir netlists are unaffected.")
    for inf in info:
        inf["drawing"] = drawn.get(inf["n"], "auto-layout") if not asc_error else "-"
        if "rejected" in inf["drawing"] or "unreadable" in inf["drawing"]:
            warnings.append(f"Section {inf['n']}: {inf['drawing']}")
    files[f"{spec}_AC.cir"] = netlist(False)
    files[f"{spec}_AC_MC.cir"] = netlist(True)

    extra = {}
    for stem, d in real.items():
        if d["asy"]:
            extra[os.path.basename(d["asy"])] = d["asy"]
        if include_models:
            for p in d["files"].values():
                extra[os.path.basename(p)] = p
    files["README.txt"] = _readme(spec, now, vs, info, r_bands, c_tol, runs, dist,
                                  grid, expected, dev, warnings, files, real, extra,
                                  include_models, all_generic)
    for name, text in files.items():
        text.encode("ascii")                        # raises on a non-ASCII slip
    return {"files": files, "extra_files": extra, "cascade": casc, "sections": info,
            "warnings": warnings, "expected": expected, "loaded_dev_db": dev,
            "all_generic": all_generic, "asc_error": asc_error,
            "zip_name": f"FS_LTspice_{spec}_{now:%Y%m%d_%H%M}.zip"}


def zip_bytes(export):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in export["files"].items():
            z.writestr(name, text)
        for name, path in export.get("extra_files", {}).items():
            z.write(path, name)
    return buf.getvalue()


# =====================================================================
#  README
# =====================================================================
def _readme(spec, now, vs, info, r_bands, c_tol, runs, dist, grid, expected, dev,
            warnings, files, real, extra, include_models, all_generic):
    fmin, fmax, ppd = grid
    has_asc = f"{spec}_AC.asc" in files
    L = [f"{APP_NAME} - LTspice export (FS-008: schematics + netlists)",
         f"Design: {spec}    Generated: {now:%Y-%m-%d %H:%M}    Version: {__version__}",
         "",
         "FILES"]
    if has_asc:
        L += [f"  {spec}_AC.asc      schematic: nominal values, AC sweep, .meas probes",
              f"  {spec}_AC_MC.asc   schematic: Monte Carlo values ({runs} runs), .save V(OUT)"]
    L += [f"  {spec}_AC.cir      the same nominal circuit as a netlist",
          f"  {spec}_AC_MC.cir   the same Monte Carlo circuit as a netlist",
          "  The schematics and the netlists are written from one netlist model in the",
          "  same export and are checked against each other; the .cir files open without",
          "  any symbol or library.",
          "  Nominal and Monte Carlo are separate files on purpose: LTspice's random",
          "  functions never return the nominal value, and the nominal file keeps plain",
          "  values you can read and edit."]
    for name in extra:
        L.append(f"  {name:<18} used by an op-amp model (keep it next to the .asc/.cir)")
    L += ["",
         "HOW TO RUN (LTspice 24)",
         "  0. Extract the whole zip into one folder first"
         + (" (the models need their files)." if extra else "."),
         "  1. File > Open: the .asc schematic, or a .cir (set the file type to",
         "     Netlists (*.cir)).",
         "  2. Simulate > Run, then Plot Settings > Add Trace > V(out). The plot",
         "     shows magnitude (dB) and phase; right-click the right-hand (phase)",
         "     axis and choose Group Delay for tau(f).",
         "  3. View > SPICE Error Log lists the .meas probe values. In the MC file it",
         "     lists one value per run; right-click the log > Plot .step'ed .meas data",
         "     shows their spread.",
         "",
         "CIRCUIT",
         f"  Sections in series, stage order (section 1 first); input IN, output OUT,",
         "  section k's output net S<k>, internal nets S<k>_<node>.",
         "  Designators: R201 = section 2, R1 (split caps C202A / C202B, op-amps U201",
         "  written XU201). AM / 3rd-order MFB band-pass prefilter parts print as",
         "  R<k>00 / C<k>00 (the schematic's R0 / C0).",
         f"  Supply: Vs = {fmt_value(vs)} V as VPOS = VNEG = Vs/2 (+-{vs / 2:g} V), GND at the",
         "  midpoint. Change .param Vs to change both.",
         "  AC sweep: " + f"{fmt_hz(fmin)} .. {fmt_hz(fmax)}, {ppd} points/decade "
         "(the tool's grid).",
         "",
         "  Schematic: sections stacked top to bottom, joined by net labels; the",
         "  directives sit to the right of the circuit. A section is drawn from its",
         "  hand-drawn cell template (parts the variant does not use are removed, a",
         "  shorted one becomes a wire); a cell without a template is auto-laid-out",
         "  (op-amps, a row of resistors, a row of capacitors, every pin on a",
         "  labelled net). SECTIONS below says which.",
         "",
         "SECTIONS"]
    for inf in info:
        p = inf["params"]
        if inf["fs_generic"]:
            mdl = (f"{inf['model']}: A_ol={fmt_value(p['A_ol'])} GBWP={fmt_hz(p['GBWP_hz'])}"
                   f" Ro={fmt_value(p['Ro_ohm']) if p['Ro_ohm'] else '0'} ohm")
        else:
            mdl = f"{inf['stem']} (model {inf['model']})"
        L.append(f"  {inf['n']:>2}  {inf['topology']:<22} op-amp {inf['opamp_label'] or 'Ideal':<14}"
                 f" -> {mdl}")
        if has_asc:
            L.append(f"      drawing: {inf['drawing']}")
    L += ["",
          "OP-AMP MODELS",
          "  FS generic (Ideal, Custom and parts without a SPICE model): the tool's",
          "  own model, A(s) = A_ol / (1 + s*A_ol/(2*pi*GBWP)) behind Ro. It is linear,",
          "  has no supply rails, no offset and no noise, so LTspice must reproduce the",
          "  tool exactly (see EXPECTED VALUES). The Ideal op-amp is clamped to",
          "  A_ol = 1e9, GBWP = 10 THz, Ro = 0 (invisible)."]
    for stem, d in real.items():
        m = d["meta"]
        L += [f"  {stem}: from the op-amp model library ({d['origin']}: {d['path']}).",
              f"    X-line model {d['xmodel']}; pin order "
              + " ".join(r or "NC" for r, _ in d["xpins"])
              + (f"; supply {m.get('vs_min', '?')} .. {m.get('vs_max', '?')} V"
                 if ("vs_min" in m or "vs_max" in m) else "")]
        if m.get("note") or m.get("source"):
            L.append(f"    {m.get('note', '')} {m.get('source', '')}".rstrip())
        if d["files"]:
            L.append("    model files: " + ", ".join(
                os.path.basename(p) if include_models else p for p in d["files"].values()))
    if real:
        L += ["  A real model has offsets, bias currents and rails: run .op once (the",
              "  commented directive in each file) and check that every section output",
              "  sits near 0 V before trusting the AC result."]
        if not include_models and any(d["files"] for d in real.values()):
            L += ["  Model files are referenced by absolute path on the exporting machine;",
                  "  export with 'Include model files in the zip' to move the files."]
    L += ["",
          "MONTE CARLO",
          f"  Capacitors +-{c_tol:g} %; resistors by value band:"]
    for i, (lo, hi, tol) in enumerate(r_bands):
        L.append(f"    tR{i + 1}: {fmt_value(lo) if lo else '0'} .. "
                 f"{'inf' if hi >= 1e12 else fmt_value(hi)} ohm  +-{tol:g} %")
    L += [f"  Distribution: {'Uniform +-tol' if dist == 'uniform' else 'Gaussian, tol = 3 sigma'};"
          f" {runs} runs.",
          "  Differences from the tool's Monte Carlo: LTspice's random generator is not",
          "  the tool's (only statistics compare); a split capacitor is two parts with",
          "  independent draws here (the tool varies their sum as one part, so its band",
          "  is slightly wider); an untruncated Gaussian could in principle go negative",
          "  (a 6-sigma event at tol <= 50 %, ignored).",
          "",
          "EXPECTED VALUES (the .meas probes)",
          "  'loaded' = MNA of the cascade exactly as written here (LTspice must match",
          "  it to ~0.01 dB); 'tool' = the tool's realized curve, a product of unloaded",
          "  sections. The two differ only where an op-amp's output impedance meets the",
          "  next section's input impedance -- mostly the far stopband."]
    if not all_generic:
        L += ["  Real op-amp models are used, so LTspice will NOT match 'loaded' exactly:",
              "  the column is what the FS generic model (the tool's A_ol / GBWP / Ro)",
              "  would give in the same cascade -- a passband sanity reference only."]
    L += [f"  {'probe':<10}{'f':>12}{'loaded dB':>12}{'phase':>10}{'tool dB':>12}"]
    for name, f, a, b in expected:
        L.append(f"  {name:<10}{fmt_hz(f):>12}{_db(a):>12.4f}"
                 f"{math.degrees(np.angle(a)):>10.2f}{_db(b):>12.4f}")
    L += [f"  Largest loaded-vs-tool difference over the sweep (within 60 dB of the",
          f"  peak): {dev:.3f} dB.",
          ""]
    if warnings:
        L += ["WARNINGS"] + [f"  - {ascii_text(w)}" for w in warnings] + [""]
    L += ["VALIDATION CHECKLIST (FS-008; report results back)",
          "  [ ] Both .asc files open in LTspice 24 without errors; no unconnected-pin",
          "      marks, no label overlapping a part badly enough to mislead.",
          "  [ ] View > SPICE Netlist of each .asc: the R / C / V / X lines equal the",
          "      matching .cir up to line order, net-name case (XU201 = U201) and the",
          "      two nodes of an R / C (a template may draw a part either way round).",
          "  [ ] Template sections: no unconnected-pin marks, no leftover stubs of",
          "      removed parts, the seat's op-amp wired to the template's nets.",
          "  [ ] Run each .asc: the .meas values in the SPICE Error Log equal the .cir's",
          "      (and, with FS generic only, 'loaded dB' above within 0.01 dB).",
          "  [ ] The multi-line directives (.subckt, Monte Carlo block, probes) show",
          "      as several lines and simulate (LTspice's \\n encoding in TEXT).",
          "  [ ] Replace one op-amp by hand (right-click > Pick New Symbol, or edit its",
          "      Value): the circuit still simulates.",
          "  [ ] Edit .param Vs: both supply sources follow.",
          ""]
    return EOL.join(ascii_text(x) for x in L) + EOL
