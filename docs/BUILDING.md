# Packaging FilterSynthesizer as a single-click EXE

This folder contains everything needed to bundle the Streamlit app into a
double-clickable `FilterSynthesizer.exe` — no `pip install`, no `streamlit run`.

## What gets built

```
dist/FilterSynthesizer/
├── FilterSynthesizer.exe                 ← double-click this
├── _internal/                    ← Python, libs, app modules (must travel with the EXE)
└── Section_Schematic_Diagrams/   ← USER-EDITABLE: drop new .drawio.svg files here
```

Ship the whole `dist\FilterSynthesizer\` folder. Zipping it is fine.

## Quick start

1. Have Python 3.11 or 3.12 on PATH (`python --version` from a fresh terminal).
2. Put `app.py` (your Streamlit entrypoint), `launcher.py`, `FilterSynthesizer.spec`,
   `build.bat`, and the `Section_Schematic_Diagrams/` folder in the same
   directory as the rest of the app modules.
3. Double-click `build.bat`. First build takes ~5 minutes (downloads + compiles).
4. Open `dist\FilterSynthesizer\FilterSynthesizer.exe`. The browser opens to the app.

## What the launcher handles for you

`launcher.py` is what PyInstaller actually wraps. It deals with three things
the naive `pyinstaller app.py` approach gets wrong:

- **Multiprocessing.** `ProcessPoolExecutor` on Windows spawns workers by
  re-launching the EXE. Without `multiprocessing.freeze_support()` at the very
  top of the launcher, each of your 32 solver workers would re-boot Streamlit
  → instant fork bomb. The launcher's first line fixes this.
- **Writable paths.** If the user installs into Program Files, the cwd is
  read-only and `tf_cache_v3.json` can't be written. The launcher chdir's to
  `%LOCALAPPDATA%\FilterSynthesizer\` before Streamlit starts; cache + logs land there.
- **SVG folder editability.** The launcher points `FILTERSYNTHESIZER_SVG_DIR` at the
  `Section_Schematic_Diagrams\` folder NEXT TO the EXE, so users can add new
  schematic SVGs without a rebuild. `schematic_svg.py` already honors that env
  var (a 2-line edit; see "Source edits required").

## Source edits required

One small change has already been applied:

- **`schematic_svg.py`** — `SVG_DIR` now consults `FILTERSYNTHESIZER_SVG_DIR` first.
  Default behavior (dev mode) is unchanged.

No other source files need touching.

## What "single-click" actually looks like for the user

1. Double-click `FilterSynthesizer.exe`.
2. A console window appears (so any startup error is visible — flip to
   `console=False` in `FilterSynthesizer.spec` once you trust the build).
3. ~10–20 seconds later the default browser opens on `http://localhost:8501`.
   Subsequent launches are 2–4 seconds.
4. The app is fully usable. Closing the console window quits the server.

## Common pitfalls and fixes

| Symptom on first run                                  | Cause / fix                                                                                                                                              |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ModuleNotFoundError: No module named 'X'`            | Add `"X"` to `hiddenimports` in `FilterSynthesizer.spec`. Symbolic / dynamic imports (sympy submodules, plotly templates) need explicit naming.                  |
| Browser shows blank page                              | Streamlit static assets weren't bundled. Verify `collect_data_files("streamlit")` is in the spec's `datas`.                                              |
| EXE launches → 32 EXE icons flash in taskbar          | `freeze_support()` not the first line of `launcher.py`. Don't move it.                                                                                   |
| `tf_cache_v3.json.tmp.*` leak in install dir          | EXE was launched with the install dir as cwd. Verify launcher's `os.chdir(writable_app_data())` runs early. Already in place.                            |
| Windows Defender flags `FilterSynthesizer.exe` as a virus     | Known false positive for unsigned PyInstaller binaries. Code-sign the EXE, OR submit it to Microsoft for whitelisting, OR distribute via a signed installer (Inno Setup, NSIS). |
| First launch is slow (20+ s)                          | sympy + lambdify cold start. Subsequent launches use `tf_cache_v3.json` and are fast. Consider shipping a pre-warmed cache file in `Section_Schematic_Diagrams\`'s parent.       |
| App works in dev but missing SVGs in EXE              | Check that `dist\FilterSynthesizer\Section_Schematic_Diagrams\` exists and contains the `.drawio.svg` files. `build.bat` copies them automatically.              |
| PNG schematic download button is missing              | `cairosvg` failed to bundle (it has C extensions). Either fix `cairosvg` in the bundle, or just live with SVG-only downloads — PNG was the bonus path.   |

## Build size & startup expectations

- Bundle size: **300–500 MB** on disk (mostly scipy + sympy + Streamlit frontend).
- First launch: **10–20 s** to first Streamlit page.
- Subsequent launches: **2–4 s** (cache is warm).
- Synthesis run (the heavy phase): same wall time as the dev install — there
  is NO performance penalty from PyInstaller bundling once Python is up.

## Cross-platform notes

- **macOS:** the same `FilterSynthesizer.spec` builds a `.app` bundle; sign it with a
  Developer ID certificate or Gatekeeper will block it.
- **Linux:** produces an ELF executable + `_internal/` folder; ships fine but
  AppImage is a common wrapper if you want a single file.
- The launcher's path logic uses `LOCALAPPDATA` (Win), `XDG_DATA_HOME` (Linux),
  and falls back to `~/.local/share` — so it picks the right writable dir on
  every OS without changes.

## Alternatives considered

- **`--onefile`** (single self-extracting EXE): rejected — it unpacks to a
  temp dir every launch (5–10 s extra), and the SVG folder + cache can't live
  inside the bundle. The current `--onedir` approach is faster and gives you
  an editable SVG folder.
- **stlite (Streamlit-in-browser via Pyodide):** not viable. This app uses
  `ProcessPoolExecutor` workers and large sympy `lambdify` jobs — Pyodide
  has no real multiprocessing and would choke on the symbolic stage.
- **Docker:** great for servers, not "single-click" for end users on Windows.
- **Inno Setup / NSIS installer wrapping the EXE:** worth doing once you've
  validated the bundle works — gives Start Menu entries, uninstaller, signed
  distribution. Out of scope here.
