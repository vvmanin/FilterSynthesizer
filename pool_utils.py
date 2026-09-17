# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""
The engine process pool, plus the error plumbing around it.

WHY THIS EXISTS
---------------
`concurrent.futures.ProcessPoolExecutor` is one-shot. The moment a single
worker dies -- bad import, OOM, killed by the OS -- the executor is marked
BROKEN and every later `submit()` raises `BrokenProcessPool` forever. Because
the pool is held in `@st.cache_resource`, that dead state survives reruns, so
ONE bad worker poisons the rest of the session: whatever the user clicks
afterwards (Component envelope, op-amp model, a different order) reports the
same error, and they reasonably conclude the feature itself is broken.

`run_in_pool()` notices a broken pool, throws it away, rebuilds and retries
once. A genuine exception raised by the task is re-raised untouched.

`format_exc_for_ui()` / `env_summary()` exist so a user-reported failure
carries the traceback and the environment instead of a single flattened line --
`st.error(f"...{e}")` alone makes remote diagnosis impossible.
"""

import concurrent.futures
import os
import platform
import sys
import traceback
from concurrent.futures.process import BrokenProcessPool

import streamlit as st

import mp_fix

# Must be re-applied on every Streamlit rerun (Streamlit rebuilds __main__ each
# time) and must happen before any pool is constructed. See mp_fix.py.
mp_fix.neutralize_main()

MAX_WORKERS = 2


@st.cache_resource(show_spinner=False)
def get_process_pool():
    """The one engine pool, shared across reruns."""
    return concurrent.futures.ProcessPoolExecutor(max_workers=MAX_WORKERS)


def run_in_pool(fn, *args, **kwargs):
    """`get_process_pool().submit(fn, ...).result()` with one rebuild-and-retry
    if the pool was found dead. Exceptions raised by `fn` propagate unchanged."""
    try:
        return get_process_pool().submit(fn, *args, **kwargs).result()
    except BrokenProcessPool:
        get_process_pool.clear()          # drop the poisoned executor
        return get_process_pool().submit(fn, *args, **kwargs).result()


def format_exc_for_ui(exc=None) -> str:
    """Full traceback as text, for an expander under the error message."""
    if exc is None:
        return traceback.format_exc()
    return "".join(traceback.format_exception(type(exc), exc,
                                              exc.__traceback__)).strip()


def env_summary() -> str:
    """One block a user can paste into a bug report. Import failures are
    reported rather than raised -- a missing package is exactly what we want to
    see here."""
    def _ver(name):
        try:
            mod = __import__(name)
            return getattr(mod, "__version__", "?")
        except Exception as exc:                      # noqa: BLE001
            return f"<not importable: {type(exc).__name__}>"

    try:
        from _version import __version__ as app_version
    except Exception:                                 # noqa: BLE001
        app_version = "?"

    lines = [
        f"FilterSynthesizer {app_version}",
        f"python   {sys.version.split()[0]}  ({platform.system()} "
        f"{platform.release()}, {platform.machine()})",
        f"frozen   {getattr(sys, 'frozen', False)}",
        f"main-safe {mp_fix.main_is_safe()}   cwd {os.getcwd()}",
    ]
    for pkg in ("streamlit", "numpy", "scipy", "sympy", "pandas", "plotly",
                "matplotlib", "reportlab"):
        lines.append(f"{pkg:<11}{_ver(pkg)}")
    return "\n".join(lines)
