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
#                                          (vendor files are never shipped: the
#                                          user adds them to the overlay's
#                                          models/ with consent, consent.json;
#                                          consented files travel in the zip)
#
#  FS-029: a vendor part's dummy includes FS_<PART>.lib, a wrapper the app
#  generates at import: '.subckt FS_<PART> INP INN VCC VEE OUT' around an
#  unchanged '.include <PART>__<vendor file>' and one X line in the vendor's
#  pin order. The vendor's helper subckts become local (two vendor models with
#  the same helper names can share a netlist) and the dummy does not depend on
#  the vendor's file or subckt name.
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

import datetime
import hashlib
import io
import json
import os
import re
import zipfile

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


def _models_stamp():
    """The model folders' file lists: a dummy re-resolves its .lib when a
    model file is added or removed."""
    out = []
    for d in model_dirs():
        try:
            out.append(tuple(sorted(os.listdir(d))))
        except OSError:
            out.append(None)
    return tuple(out)


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
        "Vendor SPICE models are the vendors' copyrighted files: they are never",
        "committed or shipped with FilterSynthesizer. Download them from the vendor",
        "yourself (the dummy's ';FS: source=' names where) and add them in the app",
        "(Resulting Response > LTspice export > Vendor model files), which stores",
        "them in the per-user models folder and records your consent in",
        "consent.json. A dummy's '.lib <file>' with a relative name is looked up",
        "next to the dummy, then in the per-user models/, then here.",
        "An imported vendor model is stored unchanged as <PART>__<its file name>,",
        "next to FS_<PART>.lib, a wrapper the app generates (it includes the vendor",
        "file inside its own .subckt FS_<PART> INP INN VCC VEE OUT)."]) + SA.EOL


# =====================================================================
#  Vendor model files: per-user folder, consent, install from an upload
# =====================================================================
CONSENT_FILE = "consent.json"
MAX_MODEL_BYTES = 20 * 1024 * 1024
_ENCRYPTED = ("$CDNENCSTART", "* LTSPICE ENCRYPTED", "BEGIN ENCRYPTED")


def user_models_dir():
    return os.path.join(overlay_dir(), "models")


def ensure_user_dirs():
    """Create the per-user overlay (opamps/, cells/, models/ + README) on this
    PC; returns the models folder. Never raises."""
    md = user_models_dir()
    try:
        for sub in ("opamps", "cells", "models"):
            os.makedirs(os.path.join(overlay_dir(), sub), exist_ok=True)
        rd = os.path.join(md, "README.txt")
        if not os.path.isfile(rd):
            with open(rd, "w", encoding="ascii", newline="") as fh:
                fh.write(models_readme_text())
    except OSError:
        pass
    return md


def vendor_files(d):
    """Model files a dummy needs from its vendor: {name: path | None}
    (installed, or None = not installed). Empty for FS generic, kind A
    (LTspice built-in) and kind C (embedded text)."""
    out = {os.path.basename(k.strip('"')): v for k, v in d.get("files", {}).items()}
    out.update({m: None for m in d.get("missing", [])})
    for k, v in out.items():                        # a wrapper without its vendor copy
        if v and wrapper_part(k) and len(model_closure(v)) < 2:
            out[k] = None
    return out


def _consent_path():
    return os.path.join(user_models_dir(), CONSENT_FILE)


def consents():
    """{file name (lower case): record} of vendor files the user added with
    consent (stored locally, bundled into their own export zips)."""
    try:
        with open(_consent_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return {k.lower(): v for k, v in (data.get("files") or {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def is_consented(path_or_name):
    return os.path.basename(path_or_name or "").lower() in consents()


def part_records():
    """{PART (upper case): import record} of the vendor models imported
    behind a wrapper (FS-029): wrapper, file, original, subckt, pins, sha256,
    source, accepted."""
    try:
        with open(_consent_path(), encoding="utf-8") as fh:
            return {k.upper(): v for k, v in (json.load(fh).get("parts") or {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def record_consent(names, source="", part=None):
    """Record the user's consent for these file names (the disclaimer was
    accepted in the app); `part` = an FS-029 import record {part, ...}.
    Raises OSError when the folder is not writable."""
    ensure_user_dirs()
    data = {"format": "filtersynthesizer-vendor-model-consent", "version": 2,
            "files": dict(consents()), "parts": part_records()}
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for n in names:
        data["files"][os.path.basename(n).lower()] = {"file": os.path.basename(n),
                                                      "accepted": now, "source": source}
    if part:
        data["parts"][part["part"].upper()] = dict(part, accepted=now, source=source)
    with open(_consent_path(), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)


def _model_problem(data):
    """None when `data` looks like a plain-text SPICE model, else why not."""
    txt = data.decode("latin-1").upper()
    if any(m in txt for m in _ENCRYPTED):
        return "the file is encrypted -- LTspice cannot read it; use the vendor's plain PSpice model"
    if ".SUBCKT" not in txt:
        return "no .subckt in the file -- not a SPICE model"
    return None


def _zip_members(data, depth=1):
    """[(base name, bytes)] of a zip's files, one nested zip deep. Paths inside
    the archive are never used (no extraction by path)."""
    out = []
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("not a readable zip file") from None
    with z:
        for info in z.infolist():
            if info.is_dir() or info.file_size > MAX_MODEL_BYTES:
                continue
            base = os.path.basename(info.filename.replace("\\", "/"))
            if not base:
                continue
            blob = z.read(info)
            if base.lower().endswith(".zip") and depth > 0:
                try:
                    out += _zip_members(blob, depth - 1)
                except ValueError:
                    pass
            else:
                out.append((base, blob))
    return out


def install_model(upload_name, data, wanted):
    """Store a vendor model the user supplied in the per-user models folder.

    upload_name : the uploaded file's name (a model file, or the vendor's zip)
    data        : its bytes
    wanted      : the file names the dummy's .lib / .include asks for
    A zip is searched (one nested zip deep) for a member whose base name is a
    wanted name (case-insensitive); only that member is written, under the
    wanted name. A plain file is stored under its wanted name. Returns the
    installed names; raises ValueError with the reason otherwise."""
    wanted = [os.path.basename(w) for w in wanted]
    if not wanted:
        raise ValueError("this model needs no vendor file")
    if len(data) > MAX_MODEL_BYTES:
        raise ValueError(f"file larger than {MAX_MODEL_BYTES >> 20} MB")
    if upload_name.lower().endswith(".zip"):
        found = {}
        for base, blob in _zip_members(data):
            for w in wanted:
                if base.lower() == w.lower():
                    found[w] = blob
        if not found:
            raise ValueError(f"the zip holds no {' / '.join(wanted)}")
    else:
        w = next((w for w in wanted if w.lower() == upload_name.lower()), None)
        if w is None and len(wanted) == 1:
            w = wanted[0]                          # renamed to what the dummy asks for
        if w is None:
            raise ValueError(f"expected one of {', '.join(wanted)}")
        found = {w: data}
    for w, blob in found.items():
        why = _model_problem(blob)
        if why:
            raise ValueError(f"{w}: {why}")
    md = ensure_user_dirs()
    for w, blob in found.items():
        with open(os.path.join(md, w), "wb") as fh:
            fh.write(blob)
    return sorted(found)


# =====================================================================
#  FS-029: import a vendor model behind a per-part wrapper
# =====================================================================
WRAP_PORTS = ("INP", "INN", "VCC", "VEE", "OUT")      # = opamp2's In+ In- V+ V- OUT
_WRAP_RE = re.compile(r"^FS_(\w+)\.lib$", re.I)
MODEL_EXTS = ("", ".lib", ".txt", ".mod", ".cir", ".sub", ".inc", ".sp", ".spi",
              ".ckt", ".lb", ".net", ".mdl")
_PIN_NAMES = {
    "INP": {"IN+", "+IN", "INP", "INPUT+", "+INPUT", "NONINV", "NI", "VIN+", "VINP",
            "INPLUS", "PLUS", "IP", "+"},
    "INN": {"IN-", "-IN", "INN", "INM", "INPUT-", "-INPUT", "INV", "VIN-", "VINN", "VINM",
            "INMINUS", "MINUS", "IM", "-"},
    "VCC": {"VCC", "V+", "+V", "VDD", "VP", "VS+", "+VS", "VPOS", "VCC+", "VSP", "AVDD"},
    "VEE": {"VEE", "V-", "-V", "VSS", "VN", "VS-", "-VS", "VNEG", "VEE-", "VSN", "AVSS", "GND"},
    "OUT": {"OUT", "VOUT", "OUTPUT", "VO", "O"},
}
_PIN_PHRASES = (("INP", r"non-?\s?inverting\s+input"),
                ("INN", r"(?<!non-)(?<!non)(?<!non )inverting\s+input"),
                ("VCC", r"positive\s+(?:power\s+)?supply"),
                ("VEE", r"negative\s+(?:power\s+)?supply"),
                ("OUT", r"\boutput\b"))


def safe_part(part):
    """A part name as it goes into FS_<PART> and <PART>__<file> (A-Z 0-9 _)."""
    return re.sub(r"\W", "_", (part or "").strip()).upper() or "PART"


def wrapper_name(part):
    return f"FS_{safe_part(part)}.lib"


def wrapper_part(name):
    """'FS_TL072H.lib' -> 'TL072H'; None for any other file name."""
    m = _WRAP_RE.match(os.path.basename(name or ""))
    return m.group(1).upper() if m else None


def vendor_copy_name(part, original):
    base = re.sub(r"[^\w.+-]", "_", os.path.basename(original.replace("\\", "/")))
    return f"{safe_part(part)}__{base}"


def model_closure(path):
    """A model file plus the files a wrapper includes from its own folder (the
    vendor copy), one level: what travels in the zip together."""
    out = [path]
    if not path or not wrapper_part(path):
        return out
    here = os.path.dirname(path)
    try:
        text = SA.read_text(path)
    except OSError:
        return out
    for ln in text.splitlines():
        m = _LIB_RE.match(ln)
        if m:
            p = os.path.join(here, os.path.basename(m.group(2).strip('"')))
            if os.path.isfile(p):
                out.append(p)
    return out


def _logical_lines(text):
    """SPICE lines with '+' continuations joined (stripped, comments kept)."""
    out = []
    for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        s = ln.strip()
        if s.startswith("+") and out and not out[-1].startswith("*"):
            out[-1] += " " + s[1:].strip()
        else:
            out.append(s)
    return out


def scan_subckts(text):
    """Top-level .subckt definitions of a model text: [{name, pins, comment}].
    Pins stop at 'PARAMS:' or a key=value token; comment = the '*' lines just
    above the .subckt (vendors put the pinout there)."""
    out, depth, cmt = [], 0, []
    for s in _logical_lines(text):
        if not s:
            continue
        if s.startswith("*"):
            cmt = (cmt + [s.lstrip("*").strip()])[-40:]
            continue
        low = s.lower()
        if low.startswith(".subckt"):
            tok = s.split(";")[0].split()
            if depth == 0 and len(tok) > 1:
                pins = []
                for t in tok[2:]:
                    if t.lower().rstrip(":") in ("params", "param") or "=" in t:
                        break
                    pins.append(t)
                out.append({"name": tok[1], "pins": pins, "comment": cmt})
            depth += 1
        elif low.startswith(".ends"):
            depth = max(0, depth - 1)
        cmt = []
    return out


def _role_of(pin):
    p = re.sub(r"[_\s]", "", pin.upper())
    return next((r for r, names in _PIN_NAMES.items() if p in names), None)


def guess_roles(pins, comment=()):
    """Roles (INP / INN / VCC / VEE / OUT) of a subckt's pins, in pin order,
    from the pin names, else from a pinout note above it ('PINOUT ORDER ...'
    tokens, or ADI's 'non-inverting input / inverting input / positive supply
    / negative supply / output' column heads). (roles, confident): roles may
    hold None; confident = five distinct roles for five pins."""
    def done(r):
        return len(pins) == 5 and None not in r and len(set(r)) == 5
    roles = [_role_of(p) for p in pins]
    if done(roles):
        return roles, True
    for ln in comment:                               # 'PINOUT ORDER +IN -IN +V -V OUT'
        m = re.search(r"pin\s*(?:out)?\s*order|pinout|pin\s+order", ln, re.I)
        if m:
            r = [_role_of(t) for t in ln[m.end():].replace(",", " ").split()][:len(pins)]
            if done(r):
                return r, True
    text = "\n".join(comment).lower()
    hits = []
    for role, rx in _PIN_PHRASES:
        m = re.search(rx, text)
        if m:
            hits.append((m.start(), role))
    r = [role for _, role in sorted(hits)]
    if done(r):
        return r, True
    return roles, False


def _name_score(sub, part):
    """How well a subckt name matches the part: 3 exact, 2 family ('X' as a
    wildcard: TL07XH_TL08XH ~ TL072H), 1 prefix, 0 none."""
    s = re.sub(r"[^A-Z0-9_]", "", sub.upper())
    p = re.sub(r"[^A-Z0-9]", "", (part or "").upper())
    if not p:
        return 0
    if s.replace("_", "") == p:
        return 3
    for piece in s.split("_"):
        if piece and re.fullmatch(re.escape(piece).replace("X", "[A-Z0-9]"), p):
            return 2
    s = s.replace("_", "")
    return 1 if s and (p.startswith(s) or s.startswith(p)) else 0


def model_candidates(upload_name, data, part):
    """Every 5-pin top-level .subckt in what the user picked (a model file of
    any extension, or a zip -- by content -- one nested zip deep), best match
    for `part` first: [{file, data, subckt, pins, roles, confident, score}].
    Raises ValueError with the reason when there is none."""
    if len(data) > MAX_MODEL_BYTES:
        raise ValueError(f"file larger than {MAX_MODEL_BYTES >> 20} MB")
    is_zip = data[:4] == b"PK\x03\x04"
    members = _zip_members(data) if is_zip else [(os.path.basename(upload_name), data)]
    out, why, wide = [], [], []
    for base, blob in members:
        if is_zip and os.path.splitext(base)[1].lower() not in MODEL_EXTS:
            continue
        prob = _model_problem(blob)
        if prob:
            if not is_zip or "encrypted" in prob:
                why.append(f"{base}: {prob}")
            continue
        for sc in scan_subckts(blob.decode("latin-1")):
            if len(sc["pins"]) != 5:
                if _name_score(sc["name"], part) >= 2:
                    wide.append(f"{sc['name']} ({len(sc['pins'])} pins)")
                continue
            roles, conf = guess_roles(sc["pins"], sc["comment"])
            out.append({"file": base, "data": blob, "subckt": sc["name"], "pins": sc["pins"],
                        "roles": roles, "confident": conf,
                        "score": 2 * _name_score(sc["name"], part) + conf})
    if not out:
        if wide:
            why.append("the model has other than 5 pins (" + ", ".join(wide) + "): only "
                       "in+ / in- / V+ / V- / out models are supported")
        raise ValueError("; ".join(why) or "no SPICE .subckt model found"
                         + (" in the zip (unpack other archive types first)" if is_zip else ""))
    out.sort(key=lambda c: -c["score"])
    return out


def wrapper_text(part, vendor_file, subckt, roles):
    """The FS_<PART> wrapper: the vendor file included unchanged inside it, one
    X line with the wrapper's ports in the vendor's pin order."""
    fs = f"FS_{safe_part(part)}"
    tgt = f'"{vendor_file}"' if " " in vendor_file else vendor_file
    return SA.EOL.join([
        f"* {fs}: FilterSynthesizer wrapper for {part} (FS-029) -- generated, not a",
        "* vendor file. It includes the vendor's model inside its own .subckt, so the",
        "* vendor's helper subcircuits stay local to it (lines LTspice allows only at top",
        "* level, e.g. a closing .END, are commented out in the copy). Ports as opamp2.",
        f"* Vendor subckt {subckt}, vendor pin order {' '.join(roles)}.",
        f".subckt {fs} {' '.join(WRAP_PORTS)}",
        f"XV {' '.join(roles)} {subckt}",            # before the include: some vendor
        f".include {tgt}",                            # files end with a .END line
        f".ends {fs}", ""])


# Directives LTspice accepts only at top level ("This directive is only allowed
# in global (top level) scope"): inside the wrapper they are commented out.
# '.end' does not match '.ends' (\b), '.op' not '.options'.
_TOP_ONLY_RE = re.compile(r"^\s*\.(end|options?|opt|temp|global|backanno|tran|ac|op|dc|"
                          r"noise|step|save|probe|meas|measure|four|net)\b", re.I)
_DISABLED = b"* FS-029 wrapper: disabled, top-level only -> "


def localize_model(data):
    """The vendor text as it can sit inside the wrapper's .subckt: every
    top-level-only directive line (TI's closing '.END', ...) commented out,
    all other bytes unchanged. Returns (bytes, [disabled lines])."""
    out, hits = [], []
    for ln in data.splitlines(keepends=True):
        txt = ln.decode("latin-1").strip()
        if _TOP_ONLY_RE.match(txt):
            hits.append(txt)
            out.append(_DISABLED + ln)
        else:
            out.append(ln)
    return b"".join(out), hits


def repair_imports():
    """Comment out top-level-only lines in vendor copies imported before
    localize_model existed (e.g. TL07xH's '.END'). Returns the fixed names;
    never raises."""
    fixed = []
    md = user_models_dir()
    for rec in part_records().values():
        p = os.path.join(md, rec.get("file") or "")
        try:
            with open(p, "rb") as fh:
                data = fh.read()
            new, hits = localize_model(data)
            if hits:
                with open(p, "wb") as fh:
                    fh.write(new)
                fixed.append(rec["file"])
        except (OSError, KeyError, TypeError):
            continue
    return fixed


def install_wrapped(part, cand, roles=None, source=""):
    """Import a vendor model for `part` from one model_candidates() entry:
    <PART>__<file> (the vendor's bytes, only top-level-only directive lines
    commented out -- localize_model) + FS_<PART>.lib, consent recorded for
    both. roles: the confirmed vendor pin roles (default: the guess).
    Returns (wrapper name, vendor copy name); raises ValueError / OSError."""
    roles = [r.upper() for r in (roles or cand["roles"]) if r]
    if len(roles) != 5 or sorted(roles) != sorted(WRAP_PORTS):
        raise ValueError("give each of the 5 pins a different role (in+, in-, V+, V-, out)")
    why = _model_problem(cand["data"])
    if why:
        raise ValueError(why)
    md = ensure_user_dirs()
    vend, wrap = vendor_copy_name(part, cand["file"]), wrapper_name(part)
    data, disabled = localize_model(cand["data"])
    with open(os.path.join(md, vend), "wb") as fh:
        fh.write(data)
    with open(os.path.join(md, wrap), "w", encoding="ascii", newline="") as fh:
        fh.write(wrapper_text(part, vend, cand["subckt"], roles))
    record_consent([wrap, vend], source, part={
        "part": safe_part(part), "wrapper": wrap, "file": vend, "original": cand["file"],
        "subckt": cand["subckt"], "pins": " ".join(cand["pins"]), "roles": " ".join(roles),
        "sha256": hashlib.sha256(cand["data"]).hexdigest(), "disabled": disabled})
    return wrap, vend


def installed_candidates(part):
    """Candidates for `part` from model files already in the per-user models
    folder with consent (e.g. added before FS-029): [candidate], best first."""
    out = []
    md = user_models_dir()
    for name, rec in consents().items():
        fn = rec.get("file", name)
        if wrapper_part(fn) or "__" in fn:
            continue
        p = os.path.join(md, fn)
        try:
            with open(p, "rb") as fh:
                data = fh.read()
            out += [c for c in model_candidates(fn, data, part) if c["score"] >= 4]
        except (OSError, ValueError):
            continue
    out.sort(key=lambda c: -c["score"])
    return out


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
         "meta": {}, "directives": [], "files": {}, "missing": [], "asy": None,
         "netlist_libs": [],
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
                if p is None:                        # a vendor file the user adds later
                    d["missing"].append(name.strip('"'))
                    d["warnings"].append(f"model file {name} not installed (next to the "
                                         f"dummy, in models/ or LTspice's lib/sub)")
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
        key = ("dummy", _stamp(p), _stamp(os.path.join(library_dir(), "symbols.asc")),
               _models_stamp())
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
def _rewrite(line, d, bundle):
    m = _LIB_RE.match(line)
    if not m:
        return line
    p = d["files"].get(m.group(2))
    if not p:
        return line                                  # not installed: the name as written
    tgt = os.path.basename(p) if p in bundle else p
    if " " in tgt:
        tgt = f'"{tgt}"'
    return m.group(1) + tgt + m.group(3)


def directives_cir(d, bundle=()):
    """The dummy's directives as netlist lines (+ '.lib' of a built-in part).
    bundle: model-file paths that travel in the zip (referenced by bare
    name); any other installed file is referenced by its absolute path."""
    out = [f".lib {mf}" for mf in d["netlist_libs"]]
    for raw in d["directives"]:
        out += [_rewrite(ln, d, bundle) for ln in SA.text_lines(raw)]
    return out


def directives_asc(d, bundle=()):
    """The dummy's directive TEXTs (as lines each) for the drawing."""
    out = [[_rewrite(ln, d, bundle) for ln in SA.text_lines(raw)] for raw in d["directives"]]
    return out


def x_args(d, nets, name):
    """X-line node list: nets = role -> net; open pins get NC_<name>_<k>."""
    return [nets[r] if r in nets else f"NC_{name}_{i}" for i, (r, _off) in enumerate(d["xpins"])]
