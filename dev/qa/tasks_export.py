# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""LTspice batch runs of one end-to-end design's export (FS-008).

For the files the app wrote:
  1. every .cir as exported (`LTspice -b`): it must simulate, without error lines,
     and report every .meas probe;
  2. an exact-frequency copy of the nominal .cir (`.ac list` at the probe
     frequencies; `FIND ... AT` on the log sweep interpolates by up to ~0.07 dB
     near a resonance): each probe against the export's loaded-MNA expectation,
     |H_lt - H_exp| / max|H_exp| <= EXACT_TOL (FS generic models only);
  3. every .asc through `LTspice -netlist` then `-b` on the .net: the drawing must
     give the same probes as its .cir.
Never `-b` on an .asc: LTspice 24 opens its GUI (and the updater) for that.
"""
import cmath
import glob
import math
import os
import re
import shutil
import subprocess
import time

EXACT_TOL = 1e-3          # relative to the largest expected |H|
ASC_TOL = 1e-4            # .asc vs .cir, relative
_MEAS = re.compile(r"^(\w+):\s*v\(out\)\s*=\s*\(\s*([-+0-9.eE]+)dB\s*,\s*([-+0-9.eE]+)\S*\)\s*at\s*([-+0-9.eE]+)",
                   re.I | re.M)
_ERR = re.compile(r"(^|\s)(error|fatal|singular matrix|unknown subcircuit|can't find|could not open|"
                  r"timestep too small)", re.I)


def _read_log(path):
    try:
        b = open(path, "rb").read()
    except OSError:
        return ""
    if b[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(b) > 1 and b[1:2] == b"\x00"):
        return b.decode("utf-16", errors="replace")
    return b.decode("latin-1", errors="replace")


def _run(exe, args, cwd, timeout):
    t = time.perf_counter()
    try:
        p = subprocess.run([exe, *args], cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
        subprocess.run(["taskkill", "/F", "/IM", os.path.basename(exe)], capture_output=True)
    return rc, time.perf_counter() - t


def _meas(text):
    out = {}
    for name, db, deg, f in _MEAS.findall(text):
        out[name.lower()] = (float(db), float(deg), float(f))
    return out


def _errors(text):
    return [ln.strip() for ln in text.splitlines()
            if _ERR.search(ln) and "ignoring" not in ln.lower()][:10]


def _sim(exe, path, timeout):
    rc, secs = _run(exe, ["-b", os.path.basename(path)], os.path.dirname(path), timeout)
    log = _read_log(os.path.splitext(path)[0] + ".log")
    for raw in glob.glob(os.path.splitext(path)[0] + "*.raw"):
        try:
            os.remove(raw)
        except OSError:
            pass
    return dict(rc=rc, secs=round(secs, 2), meas=_meas(log), errors=_errors(log),
                steps=len(re.findall(r"^\.step", log, re.M | re.I)) or None)


def _cplx(db, deg):
    return 10 ** (db / 20) * cmath.exp(1j * math.radians(deg))


def ltspice_task(e2e_id, ltdir, expected, exe, all_generic=True, timeout=120):
    out = dict(id=e2e_id, dir=ltdir, runs={}, problems=[])
    cirs = sorted(glob.glob(os.path.join(ltdir, "*.cir")))
    ascs = sorted(glob.glob(os.path.join(ltdir, "*.asc")))
    if not cirs:
        out["problems"].append("no .cir in the export")
        out["status"] = "fail"
        return out
    nominal = next((c for c in cirs if not c.endswith("_MC.cir")), cirs[0])
    names = [str(e[0]).lower() for e in expected or []]
    cir_meas = {}
    for c in cirs:
        r = _sim(exe, c, timeout)
        out["runs"][os.path.basename(c)] = {k: v for k, v in r.items() if k != "meas"} | {
            "n_meas": len(r["meas"])}
        if r["rc"] != 0 or r["errors"]:
            out["problems"].append(f"{os.path.basename(c)}: rc={r['rc']} {r['errors'][:2]}")
        if c == nominal:
            cir_meas = r["meas"]
            miss = [n for n in names if f"g_{n}" not in r["meas"]]
            if miss:
                out["problems"].append(f"{os.path.basename(c)}: probes missing {miss}")

    # exact-frequency copy of the nominal netlist
    exact = os.path.join(ltdir, "_exact")
    os.makedirs(exact, exist_ok=True)
    txt = open(nominal, encoding="latin-1").read()
    fr = re.findall(r"^\.meas AC \S+ FIND V\(OUT\) AT (\S+)", txt, re.M | re.I)
    if fr:
        txt2 = re.sub(r"^\.ac .*$", ".ac list " + " ".join(fr), txt, flags=re.M | re.I)
        xp = os.path.join(exact, "exact.cir")
        with open(xp, "w", encoding="latin-1") as f:
            f.write(txt2)
        r = _sim(exe, xp, timeout)
        if expected and all_generic:
            exp = {f"g_{str(e[0]).lower()}": complex(*e[2]) if isinstance(e[2], (list, tuple))
                   else complex(e[2]) for e in expected}
            scale = max(abs(v) for v in exp.values()) or 1.0
            errs = {}
            for k, v in exp.items():
                m = r["meas"].get(k)
                if m is None:
                    continue
                errs[k] = abs(_cplx(m[0], m[1]) - v) / scale
            worst = max(errs.values()) if errs else None
            out["exact"] = dict(worst_rel=worst, n=len(errs))
            if worst is None or worst > EXACT_TOL:
                out["problems"].append(f"exact probes vs expected: worst {worst}")
        else:
            out["exact"] = dict(note="real op-amp models: no tool expectation")

    # schematics via -netlist
    for a in ascs:
        stem = os.path.splitext(a)[0]
        rc, _ = _run(exe, ["-netlist", os.path.basename(a)], os.path.dirname(a), timeout)
        net = stem + ".net"
        if rc != 0 or not os.path.isfile(net):
            out["problems"].append(f"{os.path.basename(a)}: -netlist failed (rc={rc})")
            continue
        sub = os.path.join(ltdir, "_asc")
        os.makedirs(sub, exist_ok=True)
        net2 = os.path.join(sub, os.path.basename(net))
        shutil.move(net, net2)
        for extra in glob.glob(os.path.join(ltdir, "*.lib")) + glob.glob(os.path.join(ltdir, "*.sub")):
            shutil.copy(extra, sub)
        r = _sim(exe, net2, timeout)
        out["runs"][os.path.basename(net2)] = {k: v for k, v in r.items() if k != "meas"} | {
            "n_meas": len(r["meas"])}
        if r["rc"] != 0 or r["errors"]:
            out["problems"].append(f"{os.path.basename(a)} (netlisted): rc={r['rc']} {r['errors'][:2]}")
        if not a.endswith("_MC.asc") and cir_meas:
            diffs = []
            for k, m in cir_meas.items():
                n = r["meas"].get(k)
                if n is None:
                    diffs.append(math.inf)
                    continue
                ref = max(abs(_cplx(*cir_meas[q][:2])) for q in cir_meas) or 1.0
                diffs.append(abs(_cplx(n[0], n[1]) - _cplx(m[0], m[1])) / ref)
            out["asc_vs_cir"] = max(diffs) if diffs else None
            if not diffs or max(diffs) > ASC_TOL:
                out["problems"].append(f"{os.path.basename(a)}: drawing differs from .cir "
                                       f"({max(diffs) if diffs else 'no probes'})")
    out["status"] = "fail" if out["problems"] else "pass"
    return out
