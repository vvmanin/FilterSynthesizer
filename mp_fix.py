# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""
Make multiprocessing safe under Streamlit.

THE PROBLEM
-----------
Streamlit does not import the app script; it compiles it and exec()s it into a
freshly built module that it installs as ``sys.modules["__main__"]``. That
module has ``__spec__ is None`` and ``__file__ == ".../app.py"``.

CPython's ``multiprocessing.spawn.get_preparation_data()`` then does:

    main_mod_name = getattr(main_module.__spec__, "name", None)
    if main_mod_name is not None:
        d['init_main_from_name'] = main_mod_name
    elif sys.platform != 'win32' or (not WINEXE and not WINSERVICE):
        main_path = getattr(main_module, '__file__', None)
        ...
        d['init_main_from_path'] = os.path.normpath(main_path)

With ``__spec__`` missing it falls into the path branch, so every spawned
worker runs ``runpy.run_path("app.py", run_name="__mp_main__")`` -- i.e. it
re-executes the ENTIRE Streamlit app before it touches our task. That
re-import re-runs every top-level import (numpy, scipy, sympy, streamlit) and
every top-level statement, which:

  * trips ``_check_not_importing_main()`` if the script builds a pool at import
    time -> the worker dies -> BrokenProcessPool -> "Engine Error: ..." in the UI;
  * re-initialises C extensions that refuse to be initialised twice. NumPy 2.4
    added exactly such a guard (numpy/numpy#29030) and raises
    ``ImportError: cannot load module more than once per process``;
  * costs seconds and hundreds of MB per worker at solve time.

THE FIX
-------
Give ``__main__`` a spec whose name is literally ``"__main__"``.
``get_preparation_data`` then sends ``init_main_from_name="__main__"``, and the
child's ``_fixup_main_from_name()`` returns immediately:

    if mod_name == "__main__" or mod_name.endswith(".__main__"):
        return

so the worker starts clean and imports only what our pickled callable needs.

This is safe here because nothing we submit to a pool lives in ``__main__``:
``synthesize_*`` come from ``filter_engine`` and the solver workers from
``unified_solver_v2`` / ``nonideal_solver``, all normal importable modules.

Call ``neutralize_main()`` from the top of app.py, BEFORE any pool is created.
It is cheap and must be re-applied on every Streamlit rerun, because Streamlit
builds a brand-new ``__main__`` object each time -- so do NOT memoise it.
"""

import sys
from importlib.machinery import ModuleSpec


def neutralize_main() -> bool:
    """Patch ``sys.modules["__main__"].__spec__`` if it is missing.

    Returns True if a patch was applied on this call, False if the current
    ``__main__`` already had a spec (normal ``python foo.py`` runs, the frozen
    launcher, pytest, ...), in which case nothing is touched.
    """
    main = sys.modules.get("__main__")
    if main is None:
        return False
    if getattr(main, "__spec__", None) is not None:
        return False
    # loader=None is fine: multiprocessing only reads spec.name.
    main.__spec__ = ModuleSpec("__main__", None)
    return True


def main_is_safe() -> bool:
    """True when a spawned worker will NOT re-execute the main script.
    Handy for a diagnostics panel."""
    main = sys.modules.get("__main__")
    return bool(getattr(getattr(main, "__spec__", None), "name", None))
