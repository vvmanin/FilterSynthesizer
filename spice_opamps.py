# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  spice_opamps.py  [Tier D — FS-008 op-amp model library, no Streamlit]
#
#  The local op-amp library of dummy .asc files (design note §5, App. A):
#  one op-amp per file, wired to the fixed seat terminals INP / INN / OUT /
#  VCC / VEE, plus the model's directives (.lib / .include / an embedded
#  .subckt) and an optional ';FS: key=value ...' metadata comment.
#
#    LTspice_Library/opamps/<stem>.asc     built-in dummies (shipped)
#    <user overlay>/opamps/<stem>.asc      the user's own; same stem wins
#    .../models/                           model files the dummies reference
#                                          (vendor files are never shipped)
#
#  opamp_library.json `spice_model` = a dummy stem. Ideal / Custom / unmapped
#  parts use _FS_generic (the tool's own A_ol/GBWP/Ro model).
#
#  One dummy yields BOTH output forms, so the .cir and the .asc always use
#  the same model:
#    - drawing : its symbol + adapter wires, copied into a seat (spice_asc)
#    - netlist : the X-line pin order (derived from the adapter wiring and the
#                symbol's pin geometry, or ';FS: pins=...'), the model name,
#                and its directives with model-file paths made absolute (or
#                bare, when the files go into the zip).
# =====================================================================

import os
import re

import spice_asc as SA
from _version import APP_SLUG

FS_GENERIC = "_FS_generic"
SEAT_TEMPLATE = "_seat_template"
ROLES = ("INP", "INN", "OUT", "VCC", "VEE", "0")
_INNER_OK = ("VCC", "VEE", "0")
_LIB_RE = re.compile(r"^(\s*\.(?:lib|include|inc)\s+)(\"[^\"]+\"|\S+)(.*)$", re.I)


# =====================================================================
#  Folders
# =====================================================================
def library_dir():
    """Env FILTERSYNTHESIZER_LTSPICE_DIR (set by the EXE launcher), else the
    copy next to this module (dev)."""
    return (os.environ.get("FILTERSYNTHESIZER_LTSPICE_DIR")
            or os.path.join(os.path.dirname(os.path.abspath(__file__)), "LTspice_Library"))


def overlay_dir():
    """Per-user library (same folder rule as opamp_library.user_path)."""
    env = os.environ.get("FILTERSYNTHESIZER_LTSPICE_USER_DIR")
    if env:
        return env
    base = (os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
            or os.path.join(os.path.expanduser("~"), ".local", "share"))
    return os.path.join(base, APP_SLUG, "LTspice_Library")


def ltspice_lib_dirs():
    """LTspice's own lib folders (sym/, sub/), used to find a built-in part's
    .asy (pin geometry, model file). Env FILTERSYNTHESIZER_LTSPICE_LIB first."""
    cands = [os.environ.get("FILTERSYNTHESIZER_LTSPICE_LIB")]
    if os.environ.get("LOCALAPPDATA"):
        cands.append(os.path.join(os.environ["LOCALAPPDATA"], "LTspice", "lib"))
    cands.append(os.path.join(os.path.expanduser("~"), "Documents", "LTspiceXVII", "lib"))
    return [c for c in cands if c and os.path.isdir(c)]


def model_dirs():
    return [os.path.join(overlay_dir(), "models"), os.path.join(library_dir(), "models")]


# =====================================================================
#  Calibration (cached by file stamp)
# =====================================================================
_CACHE = {}


def _stamp(path):
    try:
        st = os.stat(path)
        return (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return (path, None, None)


def calibration():
    path = os.path.join(library_dir(), "symbols.asc")
    key = ("cal", _stamp(path))
    if key not in _CACHE:
        if os.path.isfile(path):
            try:
                cal = SA.Calibration(SA.read_text(path), source=path)
            except OSError as e:
                cal = SA.Calibration()
                cal.errors.append(f"{path}: {e}")
        else:
            cal = SA.Calibration()
        _CACHE[key] = cal
    return _CACHE[key]


# =====================================================================
#  Library content written as text (dev/fs008/make_ltspice_library.py)
# =====================================================================
_O = (256, 256)                       # seat origin inside the library files
_S = (_O[0], _O[1] - 64)              # opamp2 origin: pins V+ / V- / OUT on the seat axes


def _fs_adapter():
    """opamp2 in the seat: In- (-32,-16) and In+ (-32,+16) jog to the INN / INP
    terminals, V+ / V- / OUT run straight to VCC / VEE / OUT."""
    ox, oy = _O
    w = [(-32, -16, -80, -16), (-80, -16, -80, -32), (-80, -32, -128, -32),
         (-32, 16, -80, 16), (-80, 16, -80, 32), (-80, 32, -128, 32),
         (32, 0, 128, 0), (0, -32, 0, -128), (0, 32, 0, 128)]
    return [(a + ox, b + oy, c + ox, d + oy) for a, b, c, d in w]


def _box(o=_O):
    h = SA.SEAT_HALF
    return f"RECTANGLE Normal {o[0] - h} {o[1] - h} {o[0] + h} {o[1] + h} 2"


def _terminal_flags(o=_O, roles=("INN", "INP", "OUT", "VCC", "VEE")):
    return [(o[0] + SA.SEAT_TERMS[r][0], o[1] + SA.SEAT_TERMS[r][1], r) for r in roles]


def opamp2_dummy_text(value, meta, directives=()):
    """A dummy with LTspice's opamp2 symbol (pins In+ In- V+ V- OUT) wired into
    the seat: Value = the subckt to call, `meta` = the ';FS:' fields text,
    `directives` = directive texts (lines joined with LTspice's literal \\n).
    Used for FS generic and by dev/fs008/make_opamp_dummy.py (kinds B / C)."""
    asc = SA.new_asc()
    asc["sheet"] = (560, 560)
    asc["wires"] = _fs_adapter()
    asc["flags"] = _terminal_flags()
    asc["symbols"] = [{"sym": SA.OPAMP2, "x": _S[0], "y": _S[1], "orient": "R0",
                       "attrs": ["SYMATTR InstName U1", f"SYMATTR Value {value}"]}]
    asc["texts"] = [{"x": 32, "y": 16, "align": "Left", "size": "2", "text": ";FS: " + meta}]
    asc["texts"] += [{"x": 32, "y": 432 + 48 * i, "align": "Left", "size": "2", "text": "!" + d}
                     for i, d in enumerate(directives)]
    asc["drawing"] = [_box()]
    return SA.serialize(asc)


def fs_generic_dummy_text():
    return opamp2_dummy_text("FS_OA_1", "kind=fs_generic note=The tool's own A_ol/GBWP/Ro "
                             "model. The exporter writes one .subckt FS_OA_<k> per parameter set.")


def seat_template_text():
    asc = SA.new_asc()
    asc["sheet"] = (560, 560)
    asc["flags"] = _terminal_flags()
    asc["texts"] = [{"x": 32, "y": 16, "align": "Left", "size": "2",
                     "text": ";Seat template (FS-008 App. A.3a): Save As <stem>.asc. Place ONE "
                             "op-amp symbol in R0 inside the box, wire every pin to its terminal "
                             "label (never move the labels), add the model directive and a "
                             "';FS: vs_min=.. vs_max=.. source=.. note=..' comment."}]
    asc["drawing"] = [_box()]
    return SA.serialize(asc)


def cell_template_text():
    """Start of a hand-drawn cell template (App. B): IN / OUT / VCC / VEE and
    one seat U1 with the FS generic placeholder, wired as a unity follower so
    the file simulates on its own."""
    asc = SA.new_asc()
    asc["sheet"] = (880, 680)
    ox, oy = _O
    asc["wires"] = _fs_adapter() + [
        (ox - 128, oy + 32, ox - 192, oy + 32),                          # INP stub
        (ox - 128, oy - 32, ox - 192, oy - 32),                          # INN stub
        (ox - 192, oy - 32, ox - 192, oy - 192), (ox - 192, oy - 192, ox + 192, oy - 192),
        (ox + 192, oy - 192, ox + 192, oy), (ox + 128, oy, ox + 192, oy),
        (ox, oy - 128, ox, oy - 160), (ox, oy + 128, ox, oy + 160)]
    asc["flags"] = [(ox - 192, oy + 32, "IN"), (ox + 192, oy, "OUT"),
                    (ox, oy - 160, "VCC"), (ox, oy + 160, "VEE")]
    asc["symbols"] = [{"sym": SA.OPAMP2, "x": _S[0], "y": _S[1], "orient": "R0",
                       "attrs": ["SYMATTR InstName U1", "SYMATTR Value FS_OA_1"]}]
    asc["texts"] = [{"x": ox, "y": oy, "align": "Left", "size": "2", "text": ";SEAT U1"},
                    {"x": 32, "y": 16, "align": "Left", "size": "2",
                     "text": ";Cell template (FS-008 App. B): Save As <TEMPLATE>.asc, draw the "
                             "parts of 'python spice_cells.py <TEMPLATE>', label every net. "
                             "Nothing but the placeholder inside a seat box."}]
    asc["drawing"] = [_box()]
    return SA.serialize(asc)


def models_readme_text():
    return SA.EOL.join([
        "Model files referenced by op-amp dummies (.lib / .sub / .mod / .cir).",
        "Put vendor SPICE models here (or in the per-user overlay's models/ folder).",
        "They are never committed or shipped with FilterSynthesizer: download them",
        "from the vendor yourself (the dummy's ';FS: source=' names where).",
        "A dummy's '.lib <file>' with a relative name is looked up next to the",
        "dummy, then in the overlay's models/, then here."]) + SA.EOL


# =====================================================================
#  Dummies
# =====================================================================
def parse_meta(text):
    """';FS: vs_min=4.5 vs_max=36 note=free text' -> dict. A value runs until
    the next ' key=' (so note / source may hold spaces)."""
    body = text[len(";FS:"):].strip()
    out = {}
    for tok in re.split(r"\s+(?=[A-Za-z_]+=)", body):
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip().lower()] = v.strip()
    for k in ("vs_min", "vs_max"):
        if k in out:
            try:
                out[k] = float(out[k])
            except ValueError:
                raise ValueError(f"';FS:' {k}={out[k]!r} is not a number") from None
    return out


def _find_asy(sym, dirs):
    rel = re.sub(r"[\\/]+", "/", sym) + ".asy"
    for d in dirs:
        for sub in ("", "sym"):
            p = os.path.join(d, sub, *rel.split("/"))
            if os.path.isfile(p):
                return p
            p = os.path.join(d, sub, os.path.basename(rel))
            if os.path.isfile(p):
                return p
    return None


def _resolve_file(name, here):
    """Absolute path of a .lib / .include target, or None."""
    name = name.strip('"')
    if os.path.isabs(name):
        return name if os.path.isfile(name) else None
    for d in [here] + model_dirs():
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return os.path.abspath(p)
    for d in ltspice_lib_dirs():                    # LTspice's own lib/sub
        p = os.path.join(d, "sub", name)
        if os.path.isfile(p):
            return ""                               # found, but leave it bare
    return None


def load_dummy(path, origin="built-in", text=None):
    """Read and check one dummy (design note App. A.4). Returns a dict; its
    'errors' list is empty when the dummy may be used."""
    stem = os.path.splitext(os.path.basename(path))[0]
    here = os.path.dirname(os.path.abspath(path))
    d = {"stem": stem, "path": path, "origin": origin, "errors": [], "warnings": [],
         "meta": {}, "directives": [], "files": {}, "asy": None, "netlist_libs": [],
         "fs_generic": stem == FS_GENERIC, "xpins": None, "xmodel": "", "symbol": None,
         "terminals": [], "inner_flags": [], "wires": [], "seat": (0, 0)}
    err = d["errors"].append
    try:
        asc = SA.parse(text if text is not None else SA.read_text(path))
    except (OSError, ValueError) as e:
        err(f"unreadable: {e}")
        return d

    # ---- terminals and seat origin ----
    term = {}
    for x, y, n in asc["flags"]:
        if n.upper() in ("INP", "INN", "OUT"):
            if n.upper() in term:
                err(f"two {n.upper()} labels")
            term[n.upper()] = (x, y)
    if not all(r in term for r in ("INP", "INN", "OUT")):
        err("needs the terminal labels INP, INN and OUT (start from _seat_template.asc)")
        return d
    origins = {(x - SA.SEAT_TERMS[r][0], y - SA.SEAT_TERMS[r][1]) for r, (x, y) in term.items()}
    if len(origins) != 1:
        err("INP / INN / OUT are not at the seat offsets -- never move the terminal labels")
        return d
    o = origins.pop()
    d["seat"] = o
    d["terminals"] = ["INP", "INN", "OUT"]
    inner = []
    for x, y, n in asc["flags"]:
        u = n.upper()
        if u in ("INP", "INN", "OUT"):
            continue
        rel = (x - o[0], y - o[1])
        if u in ("VCC", "VEE") and rel == SA.SEAT_TERMS[u]:
            d["terminals"].append(u)
        elif u in _INNER_OK:
            inner.append((x, y, n))
        else:
            err(f"label {n!r}: only INP/INN/OUT/VCC/VEE terminals and VCC/VEE/0 inside")
        if max(abs(rel[0]), abs(rel[1])) > SA.SEAT_HALF:
            err(f"label {n!r} outside the seat box")
    d["inner_flags"] = inner
    d["wires"] = list(asc["wires"])
    for w in d["wires"]:
        if max(abs(w[0] - o[0]), abs(w[1] - o[1]), abs(w[2] - o[0]), abs(w[3] - o[1])) > SA.SEAT_HALF:
            err(f"wire {w} leaves the seat box")

    # ---- the one op-amp symbol ----
    if len(asc["symbols"]) != 1:
        err(f"needs exactly one symbol, found {len(asc['symbols'])}")
        return d
    s = asc["symbols"][0]
    d["symbol"] = s
    if s["orient"] != "R0":
        err(f"symbol orientation {s['orient']}: only R0 is supported")
    if max(abs(s["x"] - o[0]), abs(s["y"] - o[1])) > SA.SEAT_HALF:
        err("symbol outside the seat box")

    # ---- texts: directives and metadata ----
    for t in asc["texts"]:
        if t["text"][:5].upper() == "!;FS:":              # metadata typed as a directive
            t = dict(t, text=t["text"][1:])
        if t["text"].startswith("!"):
            d["directives"].append(t["text"][1:])
        elif t["text"].upper().startswith(";FS:"):
            try:
                d["meta"].update(parse_meta(t["text"]))
            except ValueError as e:
                err(str(e))
    if d["meta"].get("kind") == "fs_generic":
        d["fs_generic"] = True
    for raw in d["directives"]:
        for ln in SA.text_lines(raw):
            m = _LIB_RE.match(ln)
            if m:
                name = m.group(2)
                p = _resolve_file(name, here)
                if p is None:
                    err(f"model file {name} not found (next to the dummy, in models/ "
                        f"or LTspice's lib/sub)")
                elif p:
                    d["files"][name] = p
        try:
            raw.encode("ascii")
        except UnicodeEncodeError:
            err("non-ASCII text in a directive")

    # ---- pin geometry -> roles, X-line order ----
    base = SA.sym_base(s["sym"])
    cal = calibration()
    pins = cal.pins(base) if base == "opamp2" else None
    asy_attrs = {}
    if pins is None:
        asy = _find_asy(s["sym"], [here, os.path.join(overlay_dir(), "opamps"),
                                   os.path.join(library_dir(), "opamps")] + ltspice_lib_dirs())
        if asy:
            try:
                info = SA.parse_asy(SA.read_text(asy))
                pins, asy_attrs = info["pins"], info["attrs"]
                if os.path.dirname(os.path.abspath(asy)) == here:
                    d["asy"] = asy                  # a custom symbol travels with the export
            except (OSError, ValueError) as e:
                d["warnings"].append(f"{asy}: {e}")
    meta_pins = d["meta"].get("pins")
    if pins is not None:
        pts = [(n, (s["x"] + dx, s["y"] + dy)) for n, (dx, dy) in pins]
        nets, cerr = SA.connectivity(d["wires"], asc["flags"], pts)
        for e in cerr:
            err(e)
        xpins = []
        shared = {}
        for n, _ in pins:
            shared[nets.get(n)] = shared.get(nets.get(n), 0) + 1
        for n, (dx, dy) in pins:
            r = nets.get(n)
            if r is not None and r not in ROLES and shared[r] < 2:
                r = None                             # a dangling stub: open
            if r is not None and r not in ROLES:
                err(f"pin {n} is on an unlabelled net shared with another pin; tie it to "
                    f"a terminal or to VCC / VEE / 0")
            if r is None:
                d["warnings"].append(f"pin {n} left open")
            xpins.append((r if r in ROLES else None, (dx, dy)))
    elif meta_pins:
        xpins = []
        for r in [x.strip().upper() for x in meta_pins.split(",")]:
            if r not in ROLES + ("NC",):
                err(f"';FS: pins=' role {r!r} (use INP, INN, OUT, VCC, VEE, 0, NC)")
            off = SA.SEAT_TERMS.get(r)
            xpins.append((None if r == "NC" else r,
                          None if off is None or r not in d["terminals"]
                          else (o[0] + off[0] - s["x"], o[1] + off[1] - s["y"])))
        d["warnings"].append("pin geometry unknown (no .asy found): the X-line order comes "
                             "from ';FS: pins=' and only the seat terminals are self-checked")
    else:
        err(f"pin geometry of symbol {s['sym']} unknown: put its .asy next to the dummy, "
            f"install LTspice, or add ';FS: pins=INP,INN,VCC,VEE,OUT' (the symbol's order)")
        return d
    roles = [r for r, _ in xpins]
    for r in ("INP", "INN", "OUT"):
        if r not in roles:
            err(f"no pin reaches the {r} terminal")
    d["xpins"] = xpins

    # ---- model name and the netlist-only library of a built-in part ----
    d["xmodel"] = (SA.attr(s, "Value") or asy_attrs.get("Value")
                   or d["meta"].get("model") or "")
    if not d["xmodel"] and not d["fs_generic"]:
        err("no model name: set the symbol's Value or ';FS: model='")
    mf = d["meta"].get("lib") or asy_attrs.get("ModelFile")
    sm = asy_attrs.get("SpiceModel", "")
    if not mf and re.search(r"\.(lib|sub|mod|cir)$", sm, re.I):
        mf = sm
    if mf and not d["directives"]:
        d["netlist_libs"].append(mf)                 # LTspice finds it in lib/sub
    return d


def _scan(folder, origin):
    out = {}
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for fn in names:
        stem, ext = os.path.splitext(fn)
        if ext.lower() != ".asc" or stem == SEAT_TEMPLATE:
            continue
        p = os.path.join(folder, fn)
        key = ("dummy", _stamp(p), _stamp(os.path.join(library_dir(), "symbols.asc")))
        if key not in _CACHE:
            _CACHE[key] = load_dummy(p, origin)
        out[stem] = _CACHE[key]
    return out


def dummies():
    """{stem: dummy} -- built-in, then the user overlay (same stem wins).
    _FS_generic is always present (the shipped file, or its built-in text)."""
    lib = _scan(os.path.join(library_dir(), "opamps"), "built-in")
    lib.update(_scan(os.path.join(overlay_dir(), "opamps"), "user"))
    fs = lib.get(FS_GENERIC)
    if fs is None or fs["errors"] or not fs["fs_generic"]:
        why = "missing" if fs is None else "; ".join(fs["errors"]) or "not an FS generic dummy"
        fs = load_dummy(FS_GENERIC + ".asc", "built-in text", text=fs_generic_dummy_text())
        fs["warnings"].append(f"{FS_GENERIC}.asc {why}: the built-in copy is used")
        lib[FS_GENERIC] = fs
    return lib


def fs_generic():
    return dummies()[FS_GENERIC]


# =====================================================================
#  Hand-drawn cell templates (phase 3, design note App. B)
# =====================================================================
CELL_TEMPLATE = "_cell_template"


def cell_template(tid):
    """Template `tid` (spice_cells template id) from cells/: the user
    overlay's copy wins. {tid, path, origin, asc} or {.., error} when the file
    is unreadable; None when there is no file (the section is auto-laid-out)."""
    for folder, origin in ((os.path.join(overlay_dir(), "cells"), "user"),
                           (os.path.join(library_dir(), "cells"), "built-in")):
        p = os.path.join(folder, tid + ".asc")
        if not os.path.isfile(p):
            continue
        key = ("cell", _stamp(p))
        if key not in _CACHE:
            t = {"tid": tid, "path": p, "origin": origin, "asc": None, "error": None}
            try:
                t["asc"] = SA.parse(SA.read_text(p))
            except (OSError, ValueError) as e:
                t["error"] = f"{p}: {e}"
            _CACHE[key] = t
        return _CACHE[key]
    return None


def cell_templates():
    """{tid: template} of every template file (built-in, then user)."""
    out = {}
    for folder in (os.path.join(library_dir(), "cells"), os.path.join(overlay_dir(), "cells")):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for fn in names:
            stem, ext = os.path.splitext(fn)
            if ext.lower() == ".asc" and stem != CELL_TEMPLATE:
                out[stem] = cell_template(stem)
    return out


# =====================================================================
#  Netlist form
# =====================================================================
def _rewrite(line, d, bundled):
    m = _LIB_RE.match(line)
    if not m:
        return line
    p = d["files"].get(m.group(2))
    if not p:
        return line
    tgt = os.path.basename(p) if bundled else p
    if " " in tgt:
        tgt = f'"{tgt}"'
    return m.group(1) + tgt + m.group(3)


def directives_cir(d, bundled=False):
    """The dummy's directives as netlist lines (+ '.lib' of a built-in part)."""
    out = [f".lib {mf}" for mf in d["netlist_libs"]]
    for raw in d["directives"]:
        out += [_rewrite(ln, d, bundled) for ln in SA.text_lines(raw)]
    return out


def directives_asc(d, bundled=False):
    """The dummy's directive TEXTs (as lines each) for the drawing."""
    out = [[_rewrite(ln, d, bundled) for ln in SA.text_lines(raw)] for raw in d["directives"]]
    return out


def x_args(d, nets, name):
    """X-line node list: nets = role -> net; open pins get NC_<name>_<k>."""
    return [nets[r] if r in nets else f"NC_{name}_{i}" for i, (r, _off) in enumerate(d["xpins"])]
