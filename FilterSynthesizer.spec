# -*- mode: python ; coding: utf-8 -*-
#
# FilterSynthesizer.spec  -- fixes the "EXE builds but doesn't run" failure.
#
# Root cause it addresses: PyInstaller only follows imports it can see
# statically from launcher.py. The ~44 app modules are imported by app.py,
# which Streamlit executes at RUNTIME, so PyInstaller never collects them.
# We add app.py + every sibling .py as data files at the bundle root, plus
# Streamlit's static assets and package metadata.

import os
from PyInstaller.utils.hooks import (
    collect_data_files, collect_submodules, copy_metadata,
)

PROJ = os.path.abspath(os.getcwd())

# --- 1) app.py + all sibling application modules (THE key fix) --------------
#     Placed at "." so that when Streamlit runs app.py it finds them on
#     sys.path[0] (the script's own directory == bundle root / _internal).
app_py_files = [
    f for f in os.listdir(PROJ)
    if f.endswith(".py") and f not in ("launcher.py", "FilterSynthesizer.spec")
]
datas = [(os.path.join(PROJ, f), ".") for f in app_py_files]

# --- 2) ship the user-editable schematic folder as a bundled fallback ------
if os.path.isdir(os.path.join(PROJ, "Section_Schematic_Diagrams")):
    datas.append((os.path.join(PROJ, "Section_Schematic_Diagrams"),
                  "Section_Schematic_Diagrams"))

# --- 3) Streamlit frontend assets + metadata (else it errors on startup) ---
datas += collect_data_files("streamlit")

# matplotlib ships mpl-data (fonts incl. DejaVuSans that report_pdf loads by
# path) — collect it so PDF reports render outside the dev environment.
try:
    datas += collect_data_files("matplotlib")
except Exception:
    pass

for pkg in ("streamlit", "altair", "numpy", "scipy", "sympy",
            "pandas", "plotly", "pyarrow",
            "matplotlib", "reportlab", "svglib", "cairosvg"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

# --- 4) hidden imports: dynamic/lazy submodules + app modules (backup) -----
hiddenimports = []
hiddenimports += collect_submodules("streamlit")
hiddenimports += collect_submodules("sympy")   # sympy loads many submodules lazily
hiddenimports += collect_submodules("scipy")
hiddenimports += collect_submodules("matplotlib")
hiddenimports += ["reportlab", "svglib", "cairosvg"]
hiddenimports += [f[:-3] for f in app_py_files]  # belt-and-suspenders (PYZ copy)

a = Analysis(
    ["launcher.py"],
    pathex=[PROJ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],   # not used; trims size. Do NOT exclude sympy/scipy.
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FilterSynthesizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,           # keep True until the app runs; flip to False after
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FilterSynthesizer",
)
