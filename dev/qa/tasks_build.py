# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Phase 8 -- live servers and the PyInstaller bundle.

server_task  `python -m streamlit run app.py` from source on a free port with the
             script health check on: /_stcore/health, then /_stcore/script-health-check
             (runs app.py once in a real session -- the real engine pool included).
build_task   build.bat (stdin from NUL: it ends with `pause`; it wipes build/ and
             dist/ itself), then the dist tree, the bundled module list and
             PyInstaller's warn file.
exe_task     the bundled exe with LOCALAPPDATA pointed at a private dir (your app
             data stays untouched; warm caches copied in) and
             FILTERSYNTHESIZER_NO_BROWSER=1: port from the stdout banner, health +
             script-health-check, then `--selftest` (diagnostics incl. frozen
             multiprocessing) with its [FAIL] lines.
"""
import glob
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from common import ROOT

DIST = os.path.join(ROOT, "dist", "FilterSynthesizer")
EXE = os.path.join(DIST, "FilterSynthesizer.exe")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url, timeout):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(400).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(400).decode("utf-8", "replace")
    except Exception as e:                                 # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def _kill_tree(p):
    if p.poll() is None:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def _probe(port, log_lines, up_timeout=180, script_timeout=600):
    out = {}
    t = time.perf_counter()
    while time.perf_counter() - t < up_timeout:
        code, body = _get(f"http://127.0.0.1:{port}/_stcore/health", 3)
        if code == 200:
            break
        time.sleep(1)
    out["health"] = dict(code=code, body=body.strip()[:60], secs=round(time.perf_counter() - t, 1))
    if code == 200:
        t = time.perf_counter()
        code, body = _get(f"http://127.0.0.1:{port}/_stcore/script-health-check", script_timeout)
        out["script_health"] = dict(code=code, body=body.strip()[:200],
                                    secs=round(time.perf_counter() - t, 1))
    out["ok"] = (out["health"]["code"] == 200
                 and out.get("script_health", {}).get("code") == 200)
    return out


def _pump(stream, sink, path):
    with open(path, "w", encoding="utf-8", errors="replace") as f:
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace")
            sink.append(line)
            f.write(line)
            f.flush()


def server_task(log_dir, work_dir, timeout=300):
    port = _free_port()
    env = dict(os.environ, STREAMLIT_SERVER_SCRIPT_HEALTH_CHECK_ENABLED="true",
               PYTHONIOENCODING="utf-8")
    p = subprocess.Popen([sys.executable, "-m", "streamlit", "run", os.path.join(ROOT, "app.py"),
                          "--server.headless", "true", "--server.port", str(port),
                          "--browser.gatherUsageStats", "false"],
                         cwd=work_dir, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT)
    lines = []
    threading.Thread(target=_pump, args=(p.stdout, lines, os.path.join(log_dir, "server.log")),
                     daemon=True).start()
    try:
        out = _probe(port, lines, up_timeout=120, script_timeout=timeout)
    finally:
        _kill_tree(p)
    out["errors"] = [ln.strip() for ln in lines if re.search(r"Traceback|Error", ln)][:10]
    out["status"] = "pass" if out["ok"] else "fail"
    return out


def build_task(log_dir, timeout=2400):
    log = os.path.join(log_dir, "build.log")
    t = time.perf_counter()
    with open(log, "w", encoding="utf-8", errors="replace") as f:
        try:
            # absolute path: cmd may not search the current directory
            # (NoDefaultCurrentDirectoryInExePath)
            rc = subprocess.run(["cmd", "/c", os.path.join(ROOT, "build.bat")], cwd=ROOT,
                                stdin=subprocess.DEVNULL,
                                stdout=f, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    out = dict(rc=rc, secs=round(time.perf_counter() - t), log=log, problems=[], warnings=[])
    if not os.path.isfile(EXE):
        out["problems"].append("dist/FilterSynthesizer/FilterSynthesizer.exe missing")
        out["status"] = "fail"
        return out
    internal = os.path.join(DIST, "_internal")
    for need in ("Section_Schematic_Diagrams", "opamp_library.json", "LTspice_Library"):
        if not os.path.exists(os.path.join(DIST, need)):
            out["problems"].append(f"dist: {need} not copied next to the exe")
    # vendor SPICE models are git-ignored and never shipped (only models/README.txt)
    vendor = sorted(os.path.relpath(p, DIST) for p in
                    glob.glob(os.path.join(DIST, "**", "LTspice_Library", "**", "models", "*"),
                              recursive=True)
                    if os.path.basename(p) != "README.txt")
    if vendor:
        out["problems"].append(f"dist: vendor SPICE models shipped: {vendor[:5]}")
    for pdf in ("Quick_Start.pdf", "User_Manual.pdf"):
        if not glob.glob(os.path.join(DIST, "**", pdf), recursive=True):
            out["warnings"].append(f"dist: {pdf} missing (build.bat warns; built by the maintainer)")
    # app modules: every top-level .py except the spec's exclusions, and nothing from dev/
    app_py = {os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "*.py"))} - {"launcher.py", "verify.py"}
    bundled = {os.path.basename(p) for p in glob.glob(os.path.join(internal, "*.py"))}
    missing = sorted(app_py - bundled - {"FilterSynthesizer.spec"})
    if missing and bundled:
        out["problems"].append(f"app modules not bundled: {missing}")
    qa_names = {os.path.basename(p) for p in glob.glob(os.path.join(ROOT, "dev", "**", "*.py"),
                                                        recursive=True)}
    leaked = sorted(qa_names & bundled - app_py)
    if leaked or os.path.isdir(os.path.join(internal, "dev")):
        out["problems"].append(f"dev/ files in the bundle: {leaked or ['dev/']}")
    size = sum(os.path.getsize(p) for p in glob.glob(os.path.join(DIST, "**", "*"), recursive=True)
               if os.path.isfile(p))
    out["bundle_mb"] = round(size / 2 ** 20)
    warn = glob.glob(os.path.join(ROOT, "build", "**", "warn-*.txt"), recursive=True)
    if warn:
        # third-party "missing module" lines are PyInstaller's usual false alarms (lazy /
        # optional imports inside scipy, numpy ...): only the app's own modules count
        mods = {os.path.splitext(n)[0] for n in app_py}
        hits = []
        for ln in open(warn[0], encoding="utf-8", errors="replace"):
            m = re.match(r"missing module named '?([\w.]+)'? - imported by (.*)", ln.strip())
            if m and m.group(1).split(".")[0] in mods and "(top-level)" in m.group(2) \
                    and "optional" not in m.group(2):
                hits.append(ln.strip()[:200])
        out["missing_modules"] = hits[:20]
        if hits:
            out["warnings"].append(f"{len(hits)} app/required modules reported missing in {os.path.basename(warn[0])}")
    out["status"] = "fail" if (rc != 0 or out["problems"]) else "pass"
    return out


def exe_task(log_dir, cache_dir, timeout=600):
    if not os.path.isfile(EXE):
        return dict(status="skip", why="no exe (build not run)")
    appdata = os.path.join(log_dir, "exe_localappdata")
    tgt = os.path.join(appdata, "FilterSynthesizer")
    os.makedirs(tgt, exist_ok=True)
    for c in glob.glob(os.path.join(cache_dir, "tf_cache*.json")):
        shutil.copy(c, tgt)
    env = dict(os.environ, LOCALAPPDATA=appdata, FILTERSYNTHESIZER_NO_BROWSER="1",
               STREAMLIT_SERVER_SCRIPT_HEALTH_CHECK_ENABLED="true")
    out = {}
    p = subprocess.Popen([EXE], cwd=DIST, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines = []
    threading.Thread(target=_pump, args=(p.stdout, lines, os.path.join(log_dir, "exe.log")),
                     daemon=True).start()
    try:
        port, t = None, time.perf_counter()
        while port is None and time.perf_counter() - t < 180 and p.poll() is None:
            for ln in list(lines):
                m = re.search(r"http://localhost:(\d+)", ln)
                if m:
                    port = int(m.group(1))
            time.sleep(0.5)
        out["start_secs"] = round(time.perf_counter() - t, 1)
        if port is None:
            out["serve"] = dict(ok=False, why=f"no port banner (exit code {p.poll()})")
        else:
            out["serve"] = _probe(port, lines, up_timeout=60, script_timeout=timeout)
    finally:
        _kill_tree(p)
    out["exe_errors"] = [ln.strip() for ln in lines if re.search(r"Traceback|Error|ImportError", ln)][:10]
    # --selftest: diagnostics (modules, workers, engine) in the frozen interpreter
    try:
        st = subprocess.run([EXE, "--selftest"], cwd=DIST, env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=timeout)
        text = st.stdout.decode("utf-8", "replace")
        with open(os.path.join(log_dir, "selftest.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        fails = [ln.strip() for ln in text.splitlines() if "[FAIL]" in ln]
        m = re.search(r"Report saved to:\s*\n\s*(.+\.txt)", text)
        if m and os.path.isfile(m.group(1).strip()):
            shutil.move(m.group(1).strip(), os.path.join(log_dir, os.path.basename(m.group(1).strip())))
        out["selftest"] = dict(fails=fails, ok=not fails and "Report saved" in text)
    except subprocess.TimeoutExpired:
        out["selftest"] = dict(ok=False, fails=["timeout"])
    out["status"] = "pass" if (out["serve"].get("ok") and out["selftest"].get("ok")) else "fail"
    return out
