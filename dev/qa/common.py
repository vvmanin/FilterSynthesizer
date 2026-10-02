# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Shared helpers of the QA harness (stdlib only: preflight imports this before
it knows whether the app's dependencies are installed)."""
import datetime
import hashlib
import json
import math
import os
import pickle
import platform
import sys

QA_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(QA_DIR, "..", ".."))
RESULTS_DIR = os.path.join(QA_DIR, "results")
CACHE_DIR = os.path.join(QA_DIR, ".cache")        # warm TF / kernel caches between runs


def setup_paths():
    for p in (ROOT, QA_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)


def stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def host():
    return platform.node() or "host"


def fmt_dur(sec):
    sec = int(round(sec or 0))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


def short_hash(obj, n=12):
    """Stable digest of a picklable object (numpy scalars pickle deterministically)."""
    try:
        data = pickle.dumps(obj, protocol=4)
    except Exception:                                     # noqa: BLE001
        data = repr(obj).encode()
    return hashlib.sha1(data).hexdigest()[:n]


def jsonable(x):
    """numpy / complex / tuple-keyed data -> plain JSON values."""
    if isinstance(x, dict):
        return {str(k) if not isinstance(k, str) else k: jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [jsonable(v) for v in x]
    if isinstance(x, complex):
        return [jsonable(x.real), jsonable(x.imag)]
    if isinstance(x, float):
        return x if math.isfinite(x) else str(x)
    if isinstance(x, (str, int, bool)) or x is None:
        return x
    t = type(x).__module__
    if t == "numpy":
        if hasattr(x, "tolist"):
            return jsonable(x.tolist())
    if hasattr(x, "item"):
        try:
            return jsonable(x.item())
        except Exception:                                 # noqa: BLE001
            pass
    if hasattr(x, "tolist"):
        return jsonable(x.tolist())
    return str(x)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(jsonable(obj), f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(jsonable(obj), ensure_ascii=False) + "\n")


def read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass                              # a line cut by a crash
    except OSError:
        pass
    return out


def say(*a):
    print(*a, flush=True)
