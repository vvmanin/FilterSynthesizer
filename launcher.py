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
     tf_cache_v3.json cache and any orphaned temp files need a writable home,
     and the user-editable SVG folder needs to live NEXT TO the EXE.
  3) Boot Streamlit programmatically: Streamlit doesn't expose a clean
     library API for "start a server", so we invoke its CLI through
     streamlit.web.cli with a constructed argv. This is the documented
     pattern (and what `streamlit run` does internally).
"""

# --- 1) freeze_support MUST come first --------------------------------------
import multiprocessing
multiprocessing.freeze_support()        # no-op in dev, critical in the EXE

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
                webbrowser.open_new_tab(f"http://localhost:{port}")
                return
            except OSError:
                time.sleep(0.2)


def main():
    # Worker processes spawned by the solver: freeze_support() above has
    # already short-circuited them, so we never reach this point in workers.
    # The main process continues here.

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
