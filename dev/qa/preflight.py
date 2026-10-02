# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Phase 0 -- environment check, stdlib only (runs before any app import).

* interpreter: 3.11 / 3.12 are the supported ones (CLAUDE.md); others warn.
* requirements.txt: every bound is checked against importlib.metadata. A version
  outside its bound is a WARNING, never a stop -- the run then records it as
  compatibility evidence (does the tool pass on numpy 2.4?). requirements.txt is
  never edited here; relaxing a bound stays the maintainer's decision.
* missing packages: y/N prompt to `pip install "<requirement line>"` into the
  interpreter running the harness.
* cairosvg: imported AND rendered once (it needs the Cairo DLL on Windows).
* `python` on PATH (build.bat uses it), build_venv, LTspice, CPU / RAM, git state.
"""
import importlib
import importlib.metadata as md
import os
import re
import shutil
import subprocess
import sys

from common import ROOT, say

SUPPORTED_PY = ((3, 11), (3, 12))
LTSPICE_CANDIDATES = (
    r"%ProgramFiles%\ADI\LTspice\LTspice.exe",
    r"%LOCALAPPDATA%\Programs\ADI\LTspice\LTspice.exe",
    r"%ProgramFiles%\LTC\LTspiceXVII\XVIIx64.exe",
)
LTSPICE_URL = "https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html"
OPTIONAL = {"psutil": "per-worker memory / CPU telemetry (optional)"}


# ---------------------------------------------------------------------------
# requirements.txt
# ---------------------------------------------------------------------------
_REQ = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*([^#]*)")


def parse_requirements(path=os.path.join(ROOT, "requirements.txt")):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            m = _REQ.match(line)
            if not m:
                continue
            specs = [s.strip() for s in m.group(2).split(",") if s.strip()]
            parsed = []
            for s in specs:
                mm = re.match(r"(>=|<=|==|!=|~=|>|<)\s*([0-9][0-9A-Za-z.*]*)", s)
                if mm:
                    parsed.append((mm.group(1), mm.group(2)))
            out.append({"name": m.group(1), "specs": parsed, "line": line})
    return out


def _vkey(v):
    nums = []
    for part in str(v).split("."):
        m = re.match(r"(\d+)", part)
        if not m:
            break
        nums.append(int(m.group(1)))
    return tuple(nums)


def _cmp(a, b):
    a, b = _vkey(a), _vkey(b)
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def satisfies(version, specs):
    for op, ref in specs:
        c = _cmp(version, ref.rstrip(".*"))
        ok = {">=": c >= 0, "<=": c <= 0, ">": c > 0, "<": c < 0,
              "==": c == 0, "!=": c != 0, "~=": c >= 0}[op]
        if not ok:
            return False
    return True


def check_packages():
    rows = []
    for r in parse_requirements():
        try:
            v = md.version(r["name"])
        except md.PackageNotFoundError:
            v = None
        status = "missing" if v is None else ("ok" if satisfies(v, r["specs"]) else "out_of_bounds")
        rows.append(dict(name=r["name"], required=",".join(o + x for o, x in r["specs"]),
                         installed=v, status=status, line=r["line"]))
    for name, why in OPTIONAL.items():
        try:
            v = md.version(name)
        except md.PackageNotFoundError:
            v = None
        rows.append(dict(name=name, required="optional", installed=v,
                         status="ok" if v else "optional_missing", line=name, note=why))
    return rows


def check_cairosvg():
    """'ok' | reason. The report rasterises schematics through cairosvg (Cairo DLL)
    or falls back to svglib, else prints a placeholder."""
    try:
        import cairosvg                                   # noqa: F401
        png = cairosvg.svg2png(bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" '
                                          b'width="4" height="4"/>')
        return "ok" if png[:4] == b"\x89PNG" else "renders no PNG"
    except Exception as e:                                # noqa: BLE001
        try:
            importlib.import_module("svglib")
            return f"cairosvg unusable ({type(e).__name__}); svglib fallback present"
        except ImportError:
            return (f"cairosvg unusable ({type(e).__name__}: {str(e)[:80]}); no svglib -> "
                    "report schematics become placeholders")


# ---------------------------------------------------------------------------
# machine / tools
# ---------------------------------------------------------------------------
def memory_mb():
    try:
        import psutil
        vm = psutil.virtual_memory()
        return vm.total // 2 ** 20, vm.available // 2 ** 20
    except ImportError:
        pass
    try:
        sys.path.insert(0, ROOT)
        import diagnostics                                # stdlib-only app module
        return diagnostics._memory()
    except Exception:                                     # noqa: BLE001
        return None, None


def physical_cores():
    try:
        import psutil
        n = psutil.cpu_count(logical=False)
        if n:
            return n
    except ImportError:
        pass
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Processor | Measure-Object NumberOfCores -Sum).Sum"],
                capture_output=True, text=True, timeout=20)
            return int(out.stdout.strip())
        except Exception:                                 # noqa: BLE001
            pass
    return max(1, (os.cpu_count() or 2) // 2)


def cpu_name():
    if os.name == "nt":
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_Processor).Name"],
                                 capture_output=True, text=True, timeout=20)
            name = out.stdout.strip().splitlines()
            if name:
                return name[0].strip()
        except Exception:                                 # noqa: BLE001
            pass
    import platform
    return platform.processor()


def find_ltspice():
    env = os.environ.get("FS_QA_LTSPICE")
    cands = ([env] if env else []) + [os.path.expandvars(c) for c in LTSPICE_CANDIDATES]
    for c in cands:
        if c and os.path.isfile(c):
            ver = None
            meta = os.path.join(os.path.dirname(c), "LTspice.json")
            try:
                import json
                with open(meta, encoding="utf-8") as f:
                    j = json.load(f)
                ver = (j.get("Downloads", {}).get("Download for Windows 10 64-bit and forward", {})
                       .get("Version")) or j.get("Installation")
            except Exception:                             # noqa: BLE001
                pass
            return {"path": c, "version": ver, "xvii": c.lower().endswith("xviix64.exe")}
    return None


def path_python():
    """The `python` build.bat will use (its venv is made from it)."""
    exe = shutil.which("python")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
                             capture_output=True, text=True, timeout=30)
        return {"exe": exe, "version": out.stdout.strip()}
    except Exception:                                     # noqa: BLE001
        return {"exe": exe, "version": None}


def git_state():
    def g(*a):
        try:
            return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True,
                                  timeout=30).stdout.strip()
        except Exception:                                 # noqa: BLE001
            return ""
    dirty = [ln for ln in g("status", "--porcelain").splitlines() if ln.strip()]
    return {"head": g("rev-parse", "--short", "HEAD"), "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
            "subject": g("log", "-1", "--format=%s"), "dirty_files": len(dirty),
            "dirty": dirty[:40]}


def user_overlay():
    """Per-user op-amp / LTspice overlays change results between machines: record them."""
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "FilterSynthesizer")
    f = os.path.join(base, "opamp_library_user.json")
    models = os.path.join(base, "LTspice_Library", "models")
    return {"opamp_user_file": f if os.path.isfile(f) else None,
            "vendor_models": sorted(os.listdir(models)) if os.path.isdir(models) else []}


# ---------------------------------------------------------------------------
def ask(question, assume_yes=False):
    if assume_yes:
        say(f"  {question} [y/N] y (--yes)")
        return True
    if not sys.stdin or not sys.stdin.isatty():
        say(f"  {question} [y/N] n (no terminal)")
        return False
    try:
        return input(f"  {question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def pip_install(lines):
    cmd = [sys.executable, "-m", "pip", "install", *lines]
    say("  $ " + " ".join(cmd))
    return subprocess.run(cmd).returncode == 0


def run(assume_yes=False, want_build=False):
    """Interactive preflight. Returns (env dict, ok_to_continue)."""
    say("== Preflight")
    env = {"python": sys.version.split()[0], "executable": sys.executable,
           "platform": sys.platform}
    pyv = sys.version_info[:2]
    env["python_supported"] = pyv in SUPPORTED_PY
    say(f"  Python {env['python']} ({sys.executable})"
        + ("" if env["python_supported"] else "  [WARN: supported are 3.11 / 3.12; results are "
                                              "recorded as compatibility evidence]"))

    rows = check_packages()
    missing = [r for r in rows if r["status"] == "missing"]
    if missing:
        say("  Missing packages: " + ", ".join(r["line"] for r in missing))
        if ask(f"Install them into {sys.executable} with pip?", assume_yes):
            pip_install([r["line"] for r in missing])
            importlib.invalidate_caches()
            rows = check_packages()
    for r in rows:
        tag = {"ok": "ok", "out_of_bounds": "WARN out of bounds", "missing": "MISSING",
               "optional_missing": "optional, not installed"}[r["status"]]
        say(f"    {r['name']:12s} {str(r['installed']):10s} need {r['required']:14s} {tag}")
    env["packages"] = rows
    env["out_of_bounds"] = [r["name"] for r in rows if r["status"] == "out_of_bounds"]
    hard_missing = [r["name"] for r in rows if r["status"] == "missing"]
    env["cairosvg"] = check_cairosvg() if "cairosvg" not in hard_missing else "missing"
    say(f"  cairosvg: {env['cairosvg']}")

    env["cpu"] = cpu_name()
    env["logical_cores"] = os.cpu_count()
    env["physical_cores"] = physical_cores()
    env["ram_total_mb"], env["ram_avail_mb"] = memory_mb()
    say(f"  CPU {env['cpu']}: {env['physical_cores']} cores / {env['logical_cores']} threads; "
        f"RAM {env['ram_avail_mb']} of {env['ram_total_mb']} MB free")

    env["git"] = git_state()
    say(f"  git {env['git']['branch']} @ {env['git']['head']} \"{env['git']['subject']}\""
        + (f"  ({env['git']['dirty_files']} uncommitted files)" if env["git"]["dirty_files"] else ""))
    env["user_overlay"] = user_overlay()

    env["ltspice"] = find_ltspice()
    if env["ltspice"]:
        say(f"  LTspice {env['ltspice']['version'] or ''}: {env['ltspice']['path']}"
            + ("  [XVII: the export targets LTspice 24]" if env["ltspice"]["xvii"] else ""))
    else:
        say(f"  LTspice not found -> LTspice stage skipped. Install it from {LTSPICE_URL}"
            " (or set FS_QA_LTSPICE to LTspice.exe).")

    env["path_python"] = path_python()
    env["build_venv"] = os.path.isdir(os.path.join(ROOT, "build_venv"))
    if want_build:
        pp = env["path_python"]
        say(f"  build.bat python: {pp['exe'] + ' ' + str(pp['version']) if pp else 'NOT ON PATH'}"
            f"; build_venv {'present' if env['build_venv'] else 'absent (build.bat creates it: needs network)'}")
    ok = not hard_missing
    if hard_missing:
        say("  STOP: required packages missing: " + ", ".join(hard_missing))
    return env, ok
