# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""
One-shot field diagnostics.

DESIGN GOAL: the user runs this ONCE, sends ONE file, and that file is enough
to name the cause without a follow-up question. Every section is wrapped so a
failure anywhere still produces a complete report -- a section that cannot be
collected prints why, which is itself evidence.

Stdlib only, on purpose: if numpy or streamlit is the broken thing, a reporter
that needs them reports nothing.

WHAT EACH SECTION DISCRIMINATES
-------------------------------
  [identity]   which build is actually running (ends "is this the new one?")
  [machine]    cores + RAM -> whether 32 solver workers can even fit
  [env]        PYTHONPATH / PYTHONHOME / CONDA_* -> a second interpreter's
               site-packages reachable from the frozen app
  [sys.path]   where imports actually resolve from, in order
  [modules]    each package's version AND file -> numpy loaded from OUTSIDE
               the bundle is the smoking gun for the duplicate-init error
  [bundle]     file count/size of _internal + key DLLs -> antivirus quarantine
               or a half-extracted / mixed-version folder
  [dlls]       every loaded DLL path -> PATH-based hijack (Anaconda, MKL,
               OpenMP) supplying a library the bundle was meant to provide
  [workers]    spawns a real pool worker and imports numpy inside it -- the
               exact operation that fails in the field, in isolation
  [engine]     runs a real tiny synthesis through the pool -- end-to-end
"""

import os
import sys
import traceback
from datetime import datetime

_LINE = "=" * 72
_THIN = "-" * 72


# =====================================================================
#  worker-side probes  (module level: they must be picklable)
# =====================================================================
def _probe_import():
    """Runs INSIDE a spawned worker. This is the operation that fails."""
    import os
    import numpy
    return (f"pid={os.getpid()} numpy={numpy.__version__}\n"
            f"           file={numpy.__file__}")


def _probe_engine():
    """Runs INSIDE a spawned worker: a real (tiny) synthesis, end to end."""
    import os
    from filter_engine import synthesize_lowpass
    r = synthesize_lowpass("Butterworth", 4, 1000.0, 1.0, 40.0, {}, False, False)
    return f"pid={os.getpid()} poles={len(r['poles'])} k={r['k']:.6g}"


# =====================================================================
#  helpers
# =====================================================================
def _meipass():
    return getattr(sys, "_MEIPASS", None)


def _inside_bundle(path):
    """True if `path` lives under the PyInstaller bundle directory."""
    mp = _meipass()
    if not mp or not path:
        return None
    try:
        return os.path.normcase(os.path.abspath(path)).startswith(
            os.path.normcase(os.path.abspath(mp)))
    except Exception:
        return None


def _loaded_dlls():
    """Full path of every DLL loaded in this process (Windows).

    A numpy / OpenBLAS / MKL / OpenMP DLL resolved from outside the bundle is
    the signature of PATH-based hijacking -- the mechanism that ends in
    'cannot load module more than once per process'.
    """
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = k32.GetCurrentProcess()
    arr = (wintypes.HMODULE * 4096)()
    needed = wintypes.DWORD()
    if not psapi.EnumProcessModules(h, ctypes.byref(arr), ctypes.sizeof(arr),
                                    ctypes.byref(needed)):
        return []
    n = min(needed.value // ctypes.sizeof(wintypes.HMODULE), len(arr))
    buf = ctypes.create_unicode_buffer(32768)
    out = set()
    for i in range(n):
        if psapi.GetModuleFileNameExW(h, arr[i], buf, len(buf)):
            out.add(buf.value)
    return sorted(out)


def _memory():
    """(total_mb, avail_mb) without psutil."""
    if os.name != "nt":
        return (None, None)
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return (m.ullTotalPhys // (1024 * 1024), m.ullAvailPhys // (1024 * 1024))


def _antivirus():
    """Registered AV products (Windows Security Center). Best effort."""
    if os.name != "nt":
        return []
    import subprocess
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-CimInstance -Namespace root/SecurityCenter2 "
             "-ClassName AntiVirusProduct | Select-Object -ExpandProperty displayName"],
            capture_output=True, text=True, timeout=25)
        return [l.strip() for l in out.stdout.splitlines() if l.strip()]
    except Exception as exc:
        return [f"(could not query: {type(exc).__name__})"]


def _tree_stats(root):
    """(file_count, total_bytes) for a directory tree."""
    n = size = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            n += 1
            try:
                size += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return n, size


# =====================================================================
#  report sections
# =====================================================================
def _section(out, title):
    out.append("")
    out.append(_THIN)
    out.append(f"[{title}]")
    out.append(_THIN)


def _identity(out):
    _section(out, "identity")
    try:
        from _version import __version__, APP_NAME
    except Exception:
        __version__, APP_NAME = "?", "FilterSynthesizer"
    out.append(f"app          {APP_NAME} {__version__}")
    out.append(f"report time  {datetime.now().isoformat(timespec='seconds')}")
    exe = sys.executable
    out.append(f"executable   {exe}")
    try:
        ts = datetime.fromtimestamp(os.path.getmtime(exe))
        out.append(f"exe built    {ts.isoformat(timespec='seconds')}")
    except Exception:
        pass
    out.append(f"frozen       {getattr(sys, 'frozen', False)}")
    out.append(f"_MEIPASS     {_meipass() or '(not frozen)'}")
    out.append(f"python       {sys.version.split()[0]}")
    # Paths with spaces, non-ASCII, OneDrive or UNC prefixes all cause
    # machine-specific failures that never reproduce elsewhere.
    p = os.path.abspath(os.path.dirname(exe))
    flags = []
    if " " in p:
        flags.append("contains spaces")
    if not p.isascii():
        flags.append("NON-ASCII")
    if "onedrive" in p.lower():
        flags.append("ONEDRIVE (files may be virtualized)")
    if p.startswith("\\\\"):
        flags.append("UNC/network path")
    if len(p) > 200:
        flags.append("very long path")
    out.append(f"install dir  {p}")
    out.append(f"  path flags {', '.join(flags) if flags else 'none'}")


def _machine(out):
    _section(out, "machine")
    import platform
    out.append(f"os           {platform.system()} {platform.release()} "
               f"(build {platform.version()})")
    out.append(f"arch         {platform.machine()}")
    cpus = os.cpu_count()
    out.append(f"cpu count    {cpus}")
    total, avail = _memory()
    if total:
        out.append(f"RAM          {total} MB total / {avail} MB available")
        # The solver asks for N_CORES workers; each carries a heavy SymPy
        # lambdify. Flag the combination that starves a small machine.
        if cpus and total and (total / max(cpus, 1)) < 700:
            out.append("  NOTE: low RAM per core -- many solver workers may be "
                       "killed by the OS (BrokenProcessPool).")
    out.append(f"antivirus    {', '.join(_antivirus()) or '(none reported)'}")


def _environment(out):
    _section(out, "env")
    # These four are the prime suspects: any of them can put a SECOND
    # interpreter's site-packages inside the frozen process.
    for v in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONEXECUTABLE",
              "PYTHONNOUSERSITE", "CONDA_PREFIX", "CONDA_DEFAULT_ENV",
              "VIRTUAL_ENV"):
        val = os.environ.get(v)
        mark = "  <-- SUSPECT" if val and v in (
            "PYTHONPATH", "PYTHONHOME", "PYTHONEXECUTABLE",
            "CONDA_PREFIX", "VIRTUAL_ENV") else ""
        out.append(f"{v:<20}{val or '(unset)'}{mark}")
    for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
              "KMP_DUPLICATE_LIB_OK", "TEMP", "LOCALAPPDATA"):
        out.append(f"{v:<20}{os.environ.get(v) or '(unset)'}")

    out.append("")
    out.append("PATH entries (a Python/conda/MKL entry here can hijack a DLL):")
    seen_py = []
    for i, entry in enumerate(os.environ.get("PATH", "").split(os.pathsep)):
        if not entry.strip():
            continue
        low = entry.lower()
        hot = any(k in low for k in ("python", "conda", "anaconda", "miniconda",
                                     "mkl", "intel", "msys", "cygwin"))
        if hot:
            seen_py.append(entry)
        out.append(f"  {i:>2}  {entry}{'   <-- SUSPECT' if hot else ''}")
    out.append(f"  ({len(seen_py)} suspect entr{'y' if len(seen_py)==1 else 'ies'})")


def _syspath(out):
    _section(out, "sys.path")
    out.append("Import resolution order. Anything OUTSIDE the bundle directory")
    out.append("can supply a duplicate numpy and cause the load-twice error.")
    out.append("")
    for i, p in enumerate(sys.path):
        inside = _inside_bundle(p)
        tag = "" if inside is not False else "   <-- OUTSIDE BUNDLE"
        out.append(f"  {i:>2}  {p or '(empty)'}{tag}")


def _modules(out):
    _section(out, "modules")
    out.append("Version and ACTUAL file for each package. A file outside the")
    out.append("bundle means the frozen app is loading someone else's copy.")
    out.append("")
    for name in ("numpy", "scipy", "sympy", "mpmath", "streamlit", "pandas",
                 "plotly", "matplotlib", "reportlab"):
        try:
            mod = __import__(name)
            ver = getattr(mod, "__version__", "?")
            f = getattr(mod, "__file__", "") or ""
            inside = _inside_bundle(f)
            tag = "" if inside is not False else "   <-- OUTSIDE BUNDLE"
            out.append(f"[ok]   {name:<11}{ver:<12}{f}{tag}")
        except Exception as exc:
            out.append(f"[FAIL] {name:<11}{type(exc).__name__}: {exc}")
            out.append("       " + traceback.format_exc().replace("\n", "\n       "))


def _bundle(out):
    _section(out, "bundle")
    mp = _meipass()
    if not mp:
        out.append("not frozen -- nothing to inspect")
        return
    n, size = _tree_stats(mp)
    out.append(f"_internal    {mp}")
    out.append(f"files        {n}")
    out.append(f"total size   {size / (1024*1024):.1f} MB")
    out.append("")
    out.append("A file count or size that differs from a known-good install")
    out.append("means something removed files -- antivirus quarantine, or a")
    out.append("half-extracted zip.")
    out.append("")
    # numpy's bundled math DLLs are the usual quarantine victims
    for sub in ("numpy", os.path.join("numpy", ".libs"), "scipy", "streamlit"):
        p = os.path.join(mp, sub)
        if os.path.isdir(p):
            sn, ss = _tree_stats(p)
            out.append(f"  {sub:<18}{sn:>6} files  {ss/(1024*1024):>8.1f} MB")
        else:
            out.append(f"  {sub:<18}MISSING   <-- unexpected")
    out.append("")
    for dll in ("python312.dll", "python311.dll", "vcruntime140.dll",
                "vcruntime140_1.dll"):
        p = os.path.join(mp, dll)
        if os.path.isfile(p):
            out.append(f"  {dll:<22}{os.path.getsize(p):>10} bytes")


def _dlls(out):
    _section(out, "dlls")
    try:
        dlls = _loaded_dlls()
    except Exception as exc:
        out.append(f"(could not enumerate: {type(exc).__name__}: {exc})")
        return
    if not dlls:
        out.append("(none enumerated)")
        return
    out.append(f"{len(dlls)} DLLs loaded. Listing math/python libraries only;")
    out.append("any of these from OUTSIDE the bundle is the hijack signature.")
    out.append("")
    keys = ("numpy", "openblas", "blas", "lapack", "mkl", "iomp", "omp",
            "python", "scipy", "libffi")
    shown = 0
    for d in dlls:
        low = os.path.basename(d).lower()
        if any(k in low for k in keys):
            inside = _inside_bundle(d)
            tag = "" if inside is not False else "   <-- OUTSIDE BUNDLE"
            out.append(f"  {d}{tag}")
            shown += 1
    if not shown:
        out.append("  (no math/python DLLs matched -- unusual)")
    outside = [d for d in dlls if _inside_bundle(d) is False
               and not d.lower().startswith(("c:\\windows", "\\systemroot"))]
    out.append("")
    out.append(f"non-Windows DLLs loaded from outside the bundle: {len(outside)}")
    for d in outside[:40]:
        out.append(f"  {d}")


def _workers(out):
    _section(out, "workers")
    out.append("Spawns a real worker process and imports numpy inside it.")
    out.append("THIS is the operation that produces the reported error.")
    out.append("")
    try:
        import mp_fix
        out.append(f"mp_fix.main_is_safe()  {mp_fix.main_is_safe()}")
    except Exception as exc:
        out.append(f"mp_fix unavailable: {type(exc).__name__}: {exc}")
    try:
        import multiprocessing
        out.append(f"start method           {multiprocessing.get_start_method()}")
    except Exception:
        pass
    out.append("")
    from concurrent.futures import ProcessPoolExecutor
    try:
        with ProcessPoolExecutor(max_workers=2) as pool:
            out.append("[ok]   worker import: " + pool.submit(_probe_import).result(timeout=180))
    except Exception as exc:
        out.append(f"[FAIL] worker import: {type(exc).__name__}: {exc}")
        out.append(traceback.format_exc())


def _engine(out):
    _section(out, "engine")
    out.append("A real 4th-order Butterworth synthesis, run through the pool")
    out.append("exactly as the app does it.")
    out.append("")
    from concurrent.futures import ProcessPoolExecutor
    try:
        with ProcessPoolExecutor(max_workers=2) as pool:
            out.append("[ok]   " + pool.submit(_probe_engine).result(timeout=300))
    except Exception as exc:
        out.append(f"[FAIL] {type(exc).__name__}: {exc}")
        out.append(traceback.format_exc())


# =====================================================================
#  public
# =====================================================================
_SECTIONS = (("identity", _identity), ("machine", _machine),
             ("env", _environment), ("sys.path", _syspath),
             ("modules", _modules), ("bundle", _bundle), ("dlls", _dlls),
             ("workers", _workers), ("engine", _engine))


def collect(deep=True):
    """The whole report as text. Never raises: a section that fails says so."""
    out = [_LINE, "  FilterSynthesizer diagnostic report", _LINE]
    for name, fn in _SECTIONS:
        if not deep and name in ("workers", "engine"):
            continue
        try:
            fn(out)
        except Exception as exc:
            _section(out, name)
            out.append(f"(section failed: {type(exc).__name__}: {exc})")
            out.append(traceback.format_exc())
    out.append("")
    out.append(_LINE)
    out.append("  end of report")
    out.append(_LINE)
    return "\n".join(out)


def default_report_path():
    """Desktop if we can find it (impossible to miss), else the data dir."""
    for base in (os.path.join(os.path.expanduser("~"), "Desktop"),
                 os.environ.get("LOCALAPPDATA", ""),
                 os.path.expanduser("~")):
        if base and os.path.isdir(base):
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            return os.path.join(base, f"FilterSynthesizer-diagnostic-{stamp}.txt")
    return os.path.abspath("FilterSynthesizer-diagnostic.txt")


def write_report(path=None, deep=True):
    """Write the report and return its path."""
    path = path or default_report_path()
    text = collect(deep=deep)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path, text
