# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""
FilterSynthesizer launcher -- the entry script PyInstaller wraps into FilterSynthesizer.exe.

What this script does, in order:
  1) freeze_support: must be the FIRST thing, before any other import. When
     Python's ProcessPoolExecutor spawns a worker on Windows it re-launches
     the EXE; freeze_support() detects that case and lets the worker run its
     task without re-booting the whole Streamlit server (otherwise each of
     the 32 workers would spawn its own Streamlit, fork-bombing the machine).
  2) Resolve writable paths: the EXE may sit in Program Files (read-only); the
     tf_cache cache and any orphaned temp files need a writable home, and the
     user-editable SVG folder needs to live NEXT TO the EXE.
  3) Boot Streamlit programmatically: Streamlit doesn't expose a clean
     library API for "start a server", so we invoke its CLI through
     streamlit.web.cli with a constructed argv. This is the documented
     pattern (and what `streamlit run` does internally).

Also provides  FilterSynthesizer.exe --selftest , which writes a diagnostic
report and exits without starting the server.
"""

# --- 1) freeze_support MUST come first --------------------------------------
import multiprocessing
multiprocessing.freeze_support()        # no-op in dev, critical in the EXE

# CPython imports _pylong lazily FROM C for large-int divmod / str conversion,
# so PyInstaller's static analysis never sees it and leaves it out of the
# bundle. SymPy's heugcd hits that path on big polynomial coefficients and the
# EXE dies with ModuleNotFoundError. Import it here so it gets collected.
# 3.12+ only; harmless to skip on 3.11, which has no such module.
try:
    import _pylong  # noqa: F401
except ImportError:
    pass

# --- standard imports -------------------------------------------------------
import os
import sys
import socket
import threading
import webbrowser
from pathlib import Path
from _version import APP_SLUG


APP_ENTRY = "app.py"        # <-- your Streamlit app's main file
APP_NAME = APP_SLUG
DEFAULT_PORT = 8501


def resource_path(rel):
    """Path that works in dev AND in a PyInstaller bundle. PyInstaller extracts
    bundled data to sys._MEIPASS (a temp dir for --onefile, or _internal/ for
    --onedir). In dev mode it resolves next to this script."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), rel)


def exe_dir():
    """Directory of the EXE (or this script, in dev). This is where the
    user-editable SVG folder and any external assets live."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def writable_app_data():
    """Per-user writable directory for the symbolic cache and any logs."""
    base = (os.environ.get("LOCALAPPDATA")          # Windows
            or os.environ.get("XDG_DATA_HOME")       # Linux convention
            or os.path.join(os.path.expanduser("~"), ".local", "share"))
    p = Path(base) / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def find_free_port(preferred):
    """Return the preferred port if free, otherwise pick a free ephemeral one
    so a second instance / busy port doesn't make the EXE crash silently."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
#  Console / browser helpers
# ---------------------------------------------------------------------------
def enable_ansi():
    """Let the console interpret ANSI colour codes, so Streamlit's startup
    banner reads as text instead of  <-[34m <-[1m  noise. No-op off Windows."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        for handle in (-11, -12):            # STDOUT, STDERR
            h = k32.GetStdHandle(handle)
            mode = ctypes.c_uint32()
            if k32.GetConsoleMode(h, ctypes.byref(mode)):
                k32.SetConsoleMode(h, mode.value | 0x0004)  # VT processing
    except Exception:
        pass


def _has_http_handler():
    """True if Windows has a program registered for http:// links.

    os.startfile() SUCCEEDS even when nothing is registered: the shell puts up
    its own "We can't open this 'http' link" dialog and reports no error, so
    webbrowser.open_new_tab() returns True and cannot be used to detect this.
    Ask the registry instead and skip the call -- otherwise the user gets a
    blocking modal on every launch (Windows Sandbox, locked-down or freshly
    imaged machines)."""
    if os.name != "nt":
        return True
    import winreg
    # Per-user default browser wins when present.
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Associations"
                            r"\UrlAssociations\http\UserChoice") as k:
            if winreg.QueryValueEx(k, "ProgId")[0]:
                return True
    except OSError:
        pass
    # Machine-wide handler.
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            r"http\shell\open\command") as k:
            return bool(winreg.QueryValueEx(k, "")[0])
    except OSError:
        return False


def _banner(url, opened):
    """Always shown once the server is up, whether or not a browser opened."""
    line = "=" * 62
    print("\n" + line)
    if opened:
        print("  FilterSynthesizer is running.")
        print(f"  If no browser opened, go to:   {url}")
    else:
        print("  FilterSynthesizer is running, but no web browser is")
        print("  registered on this machine, so one could not be opened.")
        print(f"  Open this address manually:    {url}")
    print("  Keep this window open while you use the program.")
    print(line + "\n", flush=True)


def open_browser_when_ready(port):
    """Poll until Streamlit's TCP port accepts connections, then open the
    default browser. Background thread so it doesn't block the server boot."""
    import time
    deadline = time.time() + 30.0           # generous: first-launch is slow
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect(("127.0.0.1", port))
            except OSError:
                time.sleep(0.2)
                continue                     # not up yet -- keep polling
        # Port is up. Open the browser OUTSIDE the socket block so a slow
        # shell call doesn't hold the probe socket open.
        url = f"http://localhost:{port}"
        opened = False
        if _has_http_handler():
            try:
                opened = bool(webbrowser.open_new_tab(url))
            except Exception:
                opened = False
        # ALWAYS print: the return value above is unreliable on Windows, and
        # Streamlit's own URL line is buried in ANSI escapes.
        _banner(url, opened)
        return
    print(f"\n  Server did not start within 30 s. Try http://localhost:{port}\n",
          flush=True)


# ---------------------------------------------------------------------------
def _run_selftest():
    """--selftest: write a diagnostic report and exit, without the server."""
    try:
        import diagnostics
    except Exception as exc:
        print(f"diagnostics module unavailable: {type(exc).__name__}: {exc}")
        input("Press Enter to close...")
        return
    path, text = diagnostics.write_report()
    print(text)
    print("\n" + "=" * 62)
    print("  Report saved to:")
    print(f"    {path}")
    print("  Please send that file.")
    print("=" * 62 + "\n")
    input("Press Enter to close...")


def main():
    # Worker processes spawned by the solver: freeze_support() above has
    # already short-circuited them, so we never reach this point in workers.
    # The main process continues here.
    enable_ansi()

    # cache + temp files need a writable cwd
    data_dir = writable_app_data()
    os.chdir(data_dir)

    # SVGs live in an EXE-adjacent, USER-EDITABLE folder (not inside the
    # bundle), so the user can drop new schematic .drawio.svg files without
    # rebuilding. Falls back to the bundle's own copy if the external folder
    # is missing, so a fresh install still works.
    ext_svg = os.path.join(exe_dir(), "Section_Schematic_Diagrams")
    if os.path.isdir(ext_svg):
        os.environ["FILTERSYNTHESIZER_SVG_DIR"] = ext_svg
    else:
        bundled = resource_path("Section_Schematic_Diagrams")
        if os.path.isdir(bundled):
            os.environ["FILTERSYNTHESIZER_SVG_DIR"] = bundled

    # Diagnostics mode: everything above has run (so the report sees the real
    # runtime state), but the server never starts.
    if "--selftest" in sys.argv:
        _run_selftest()
        return

    # pick the port, kick the browser opener BEFORE boot so it races nicely
    port = find_free_port(DEFAULT_PORT)
    threading.Thread(target=open_browser_when_ready, args=(port,),
                     daemon=True).start()

    # Construct Streamlit's argv and hand off to its CLI. server.headless=true
    # stops Streamlit from trying to open its own browser tab -- we already
    # have one queued via open_browser_when_ready, so we'd otherwise get two.
    sys.argv = [
        "streamlit", "run", resource_path(APP_ENTRY),
        "--global.developmentMode=false",
        "--server.headless=true",
        "--server.port", str(port),
        "--browser.gatherUsageStats=false",
    ]
    from streamlit.web import cli
    sys.exit(cli.main())


if __name__ == "__main__":
    main()
