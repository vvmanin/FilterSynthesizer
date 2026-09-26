# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  opamp_library.py
#  Single source of real op-amp parameters (FS-005). No Streamlit import:
#  the solvers use it too.
#
#  Two plain-JSON files, merged by name:
#    1) BUILT-IN  opamp_library.json -- ships with the tool (next to the EXE
#       in the bundle, next to this module in dev). Hand-editable.
#    2) USER      opamp_library_user.json in the per-user app-data folder
#       (%LOCALAPPDATA%\FilterSynthesizer). Written by the UI ("Save to
#       library", edits of any entry); also hand-editable. A user entry with
#       a built-in's name OVERRIDES it, so the shipped values stay intact as
#       the reference and "Revert" is just deleting the override. Tool
#       upgrades replace only file 1, never file 2.
#
#  Files are re-read when their mtime/size changes, so direct edits show up
#  on the next Streamlit rerun without a restart. A malformed file or entry
#  never crashes the app: it is skipped and reported by load_errors().
#
#  File units are engineering units (A_ol V/V, GBWP Hz, Ro ohm);
#  solver_params() converts Ro to the solvers' MOhm base.
#  The "Ideal" and "Custom..." dropdown rows are UI modes, not parts; only
#  their labels / defaults live here so every tab agrees on them.
# =====================================================================

import json
import math
import os

from _version import APP_SLUG

IDEAL_LABEL = "Ideal (no op-amp limits)"
CUSTOM_LABEL = "Custom…"
_RESERVED = (IDEAL_LABEL, CUSTOM_LABEL, "Custom...", "ideal", "custom")

# Near-ideal params: the non-ideal TF collapses onto the ideal TF for these
# (see filter_synthesis.IDEAL_OPAMP, which aliases this).
IDEAL_PARAMS = dict(A_ol=1e12, GBWP_hz=1e15, Ro=1e-12)      # Ro in MOhm
# Starting values of the Custom... inputs (Ro in ohm, as the UI shows it).
CUSTOM_DEFAULT = dict(A_ol=1e5, GBWP_hz=1e6, Ro_ohm=1200.0)

FORMAT_TAG = "filtersynthesizer-opamp-library"
FORMAT_VERSION = 1
REQUIRED = ("A_ol", "GBWP_hz", "Ro_ohm")
OPTIONAL = ("description", "spice_model", "en_nV_rtHz", "in_pA_rtHz")
MAX_NAME_LEN = 60

BUILTIN_FILENAME = "opamp_library.json"
USER_FILENAME = "opamp_library_user.json"


# ---------------------------------------------------------------------
#  paths
# ---------------------------------------------------------------------
def builtin_path():
    """Env FILTERSYNTHESIZER_OPAMP_FILE (set by the EXE launcher), else the
    copy next to this module (dev)."""
    return (os.environ.get("FILTERSYNTHESIZER_OPAMP_FILE")
            or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            BUILTIN_FILENAME))


def user_path():
    """Per-user writable file (same folder rule as launcher.writable_app_data)."""
    env = os.environ.get("FILTERSYNTHESIZER_OPAMP_USER_FILE")
    if env:
        return env
    base = (os.environ.get("LOCALAPPDATA")
            or os.environ.get("XDG_DATA_HOME")
            or os.path.join(os.path.expanduser("~"), ".local", "share"))
    return os.path.join(base, APP_SLUG, USER_FILENAME)


# ---------------------------------------------------------------------
#  reading
# ---------------------------------------------------------------------
def normalize(name):
    """Comparison form of a name: trimmed, inner whitespace collapsed, casefolded."""
    return " ".join(str(name).split()).casefold()


def _validate(name, raw):
    """(entry, None) or (None, error). Unknown fields are kept."""
    if not isinstance(raw, dict):
        return None, f"'{name}': entry must be an object"
    if not str(name).strip():
        return None, "entry with an empty name"
    e = dict(raw)
    for k in REQUIRED:
        v = e.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None, f"'{name}': {k} missing or not a finite number"
        e[k] = float(v)
    if e["A_ol"] <= 0 or e["GBWP_hz"] <= 0:
        return None, f"'{name}': A_ol and GBWP_hz must be > 0"
    if e["Ro_ohm"] < 0:
        return None, f"'{name}': Ro_ohm must be >= 0"
    for k in OPTIONAL:
        e.setdefault(k, None)
    e["description"] = str(e["description"] or "")
    return e, None


def _read(path, label, errors):
    """{name: raw entry} from one file, in file order. Missing file -> {}."""
    if not os.path.isfile(path):
        return {}
    dups = []

    def hook(pairs):
        seen = set()
        for k, _ in pairs:
            if k in seen:
                dups.append(k)
            seen.add(k)
        return dict(pairs)                      # last duplicate wins

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh, object_pairs_hook=hook)
    except (OSError, ValueError) as ex:
        errors.append(f"{label} library {path} could not be read ({ex}); ignored.")
        return {}
    for k in dups:
        errors.append(f"{label} library: duplicate key '{k}' — the last one is used.")
    parts = data.get("opamps") if isinstance(data, dict) else None
    if not isinstance(parts, dict):
        errors.append(f"{label} library {path}: no 'opamps' object; ignored.")
        return {}
    return parts


_CACHE = {"key": None, "lib": {}, "errors": [], "user_raw": {}}


def _stamp(path):
    try:
        st = os.stat(path)
        return (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return (path, None, None)


def _load():
    key = (_stamp(builtin_path()), _stamp(user_path()))
    if key == _CACHE["key"]:
        return _CACHE
    errors = []
    lib, seen = {}, {}                          # seen: normalized -> stored name

    def add(name, raw, origin):
        e, err = _validate(name, raw)
        if err:
            errors.append(f"{origin} entry skipped — {err}")
            return
        name = " ".join(str(name).split())
        nk = normalize(name)
        if nk in (normalize(r) for r in _RESERVED):
            errors.append(f"'{name}' is a reserved name; entry skipped.")
            return
        prev = seen.get(nk)
        if prev is not None and origin == "user" and lib[prev]["origin"] == "built-in":
            e["origin"] = "edited"
            lib[prev] = e                       # override keeps the built-in's spelling
            return
        if prev is not None:
            errors.append(f"'{name}' duplicates '{prev}' (names are case-insensitive); "
                          "the later entry is used.")
            e["origin"] = lib[prev]["origin"]
            lib[prev] = e
            return
        e["origin"] = origin
        lib[name] = e
        seen[nk] = name

    for n, r in _read(builtin_path(), "Built-in", errors).items():
        add(n, r, "built-in")
    user_raw = _read(user_path(), "User", errors)
    for n, r in user_raw.items():
        add(n, r, "user")

    _CACHE.update(key=key, lib=lib, errors=errors, user_raw=user_raw)
    return _CACHE


def library():
    """{name: entry}; entry holds the file fields plus origin =
    'built-in' | 'edited' (built-in overridden by the user file) | 'user'."""
    return dict(_load()["lib"])


def load_errors():
    return list(_load()["errors"])


def get(name):
    return _load()["lib"].get(name)


def solver_params(entry):
    """Solver op-amp dict {A_ol, GBWP_hz, Ro (MOhm)} from a library entry."""
    return dict(A_ol=entry["A_ol"], GBWP_hz=entry["GBWP_hz"], Ro=entry["Ro_ohm"] / 1e6)


def choices():
    """Dropdown rows: Ideal, every part, Custom..."""
    return [IDEAL_LABEL, *_load()["lib"].keys(), CUSTOM_LABEL]


def resolve(choice):
    """Dropdown row -> None (ideal / unknown), "CUSTOM", or solver params."""
    if choice == CUSTOM_LABEL:
        return "CUSTOM"
    e = get(choice) if choice else None
    return solver_params(e) if e else None


def named_params(name):
    """Solver params by part name for the solvers' string API; 'ideal' allowed."""
    if normalize(name) in (normalize("ideal"), normalize(IDEAL_LABEL)):
        return dict(IDEAL_PARAMS)
    lib = _load()["lib"]
    for n, e in lib.items():
        if normalize(n) == normalize(name):
            return solver_params(e)
    raise ValueError(f"unknown op-amp '{name}'; choose one of "
                     f"{['ideal', *lib]} or pass a dict")


# ---------------------------------------------------------------------
#  writing (user file only)
# ---------------------------------------------------------------------
def check_new_name(name):
    """Error message for a NEW part name, or None if it is free."""
    clean = " ".join(str(name or "").split())
    if not clean:
        return "Enter a name."
    if len(clean) > MAX_NAME_LEN:
        return f"Name is longer than {MAX_NAME_LEN} characters."
    nk = normalize(clean)
    if nk in (normalize(r) for r in _RESERVED):
        return f"'{clean}' is reserved."
    for n in _load()["lib"]:
        if normalize(n) == nk:
            return f"'{clean}' conflicts with existing part '{n}'."
    return None


def _write_user(parts):
    path = user_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    doc = {"format": FORMAT_TAG, "version": FORMAT_VERSION,
           "_readme": ["User op-amp library (written by FilterSynthesizer, "
                       "hand-editable). Same fields as the built-in "
                       f"{BUILTIN_FILENAME}; an entry named like a built-in "
                       "part overrides it."],
           "opamps": parts}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    _CACHE["key"] = None                        # don't trust mtime granularity


def _user_key(name):
    """Existing key in the user file matching `name` (case-insensitive), or None."""
    for k in _load()["user_raw"]:
        if normalize(k) == normalize(name):
            return k
    return None


def save_user(name, A_ol, GBWP_hz, Ro_ohm, **optional):
    """Create or update a user entry. `name` is a new name (check it with
    check_new_name first) or an existing part's name (override / update).
    Raises ValueError on invalid values; OSError if the file can't be written."""
    name = " ".join(str(name).split())
    raw = {"A_ol": A_ol, "GBWP_hz": GBWP_hz, "Ro_ohm": Ro_ohm}
    old = get(name)
    for k in OPTIONAL:                          # keep optional fields on edit
        raw[k] = optional.get(k, old.get(k) if old else None)
    e, err = _validate(name, raw)
    if err:
        raise ValueError(err)
    parts = dict(_load()["user_raw"])
    parts.pop(_user_key(name), None)
    parts[name] = {k: e[k] for k in (*REQUIRED, *OPTIONAL)}
    _write_user(parts)


def delete_user(name):
    """Remove the user entry: deletes a user part, reverts an edited built-in.
    Returns True if something was removed."""
    k = _user_key(name)
    if k is None:
        return False
    parts = dict(_load()["user_raw"])
    del parts[k]
    _write_user(parts)
    return True
