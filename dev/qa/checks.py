# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Phase 1 -- the repo's own check scripts, run as subprocesses.

Discovered, not listed: verify.py plus every dev/fs*/check_*.py, so the check
script a new roadmap item adds runs here with no edit. ARGS only tunes the ones
that take options. docs/manual/tools/doc_drift.py runs too, as information
(UI drift vs the manuals is expected between documentation batches).
"""
import glob
import os
import re
import subprocess
import sys
import time

from common import ROOT

ARGS = {                                   # script basename -> (quick args, full args)
    "check_kernels.py": (["--jobs", "4"], ["--jobs", "8"]),
    "check_spice_export.py": (["--quick"], []),
    "check_near_notch.py": ([], ["--solve"]),
}
INFO_ONLY = {"doc_drift.py"}


def discover(quick=True):
    out = [("verify.py", [os.path.join(ROOT, "verify.py")])]
    for p in sorted(glob.glob(os.path.join(ROOT, "dev", "fs*", "check_*.py"))):
        name = os.path.basename(p)
        a = ARGS.get(name, ([], []))[0 if quick else 1]
        out.append((os.path.relpath(p, ROOT).replace("\\", "/"), [p, *a]))
    dd = os.path.join(ROOT, "docs", "manual", "tools", "doc_drift.py")
    if os.path.isfile(dd):
        out.append(("docs/manual/tools/doc_drift.py", [dd]))
    return out


def check_task(name, argv, log_path, timeout=3600):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", MPLBACKEND="Agg")
    t = time.perf_counter()
    with open(log_path, "w", encoding="utf-8", errors="replace") as f:
        try:
            rc = subprocess.run([sys.executable, *argv], cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    secs = time.perf_counter() - t
    with open(log_path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    tail = "\n".join(text.strip().splitlines()[-12:])
    base = os.path.basename(argv[0])
    if base == "verify.py":                # it never exits non-zero: read its verdict line
        m = re.search(r"realizer all-pass = (True|False)", text)
        passed = rc == 0 and bool(m) and m.group(1) == "True"
    else:
        passed = rc == 0
    status = "info" if base in INFO_ONLY else ("pass" if passed else "fail")
    return dict(name=name, rc=rc, secs=round(secs, 1), status=status, tail=tail,
                log=os.path.relpath(log_path, os.path.dirname(os.path.dirname(log_path))))
