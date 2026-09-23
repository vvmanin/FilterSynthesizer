# Keeping the user documents true

The UI is not frozen. This file is the machinery that lets the two user
documents be rewritten *against* a changing interface instead of slowly drifting
away from it.

The idea in one line: **the manual is written against a machine-readable
snapshot of the UI, and a script tells you exactly which paragraphs and which
screenshots a UI change invalidated.**

---

## 1. What is where

```
FilterSynthesizer/
├─ app.py, *.py                  the application
├─ dev/                          development notes (not user-facing)
└─ docs/
   ├─ ARCHITECTURE.md, BUILDING.md
   ├─ Quick_Start.pdf            ← built output, ship this
   ├─ User_Manual.pdf            ← built output, ship this
   └─ manual/                    everything else lives here
      ├─ quick_start.md          source
      ├─ user_manual.md          source
      ├─ SCREENSHOTS.md          what to type, check and capture, per figure
      ├─ style.css               print stylesheet
      ├─ ui_inventory.json       snapshot of the UI the docs were written against
      ├─ requirements-docs.txt   the two packages the tooling needs
      ├─ DOC_WORKFLOW.md         this file
      ├─ img/                    the screenshots
      └─ tools/
         ├─ ui_inventory.py      extract every control from the source (AST)
         ├─ doc_drift.py         inventory vs docs vs screenshots -> what went stale
         └─ build_pdf.py         Markdown -> A4 PDF through Chromium
```

Nothing is added at the repository root; delete `docs/manual/` and the app is
unaffected. The tools find the app themselves, walking up from their own
location to `app.py`, so they run from any working directory. Keep
`docs/manual/` out of the PyInstaller spec — none of it is runtime.

---

## 2. One-time setup

```bat
python -m pip install -r docs\manual\requirements-docs.txt
python -m playwright install chromium
```

Chromium is only used to print the PDFs — no LaTeX, no wkhtmltopdf, no GTK/cairo
DLLs.

---

## 3. The three commands

| Command | What it does |
|---|---|
| `python docs\manual\tools\ui_inventory.py` | re-extract the UI, write `ui_inventory.json` |
| `python docs\manual\tools\doc_drift.py` | report what a UI change broke; exit 1 if anything did |
| `python docs\manual\tools\build_pdf.py` | build both PDFs |

Useful variants:

```bat
python docs\manual\tools\ui_inventory.py --print      REM every control, one per line
python docs\manual\tools\ui_inventory.py --markdown   REM paste-ready reference tables
python docs\manual\tools\doc_drift.py --accept        REM adopt the live UI as the new snapshot
```

Screenshots are taken by hand, following `SCREENSHOTS.md`.

---

## 4. The update protocol

Run this whenever the UI changes.

**Step 1 — ask what broke.**

```
> python docs\manual\tools\doc_drift.py

REMOVED — 1 control(s) gone from the source:
  - `hw_reg`  "reg_weight"  (topology_tab.py)
      documented in: docs/manual/user_manual.md
      shown in figures: 08-convergence — retake it

STALE FIGURES — 1 need retaking:
  * 08-convergence  (shows `hw_reg`)
      retake it by hand — see SCREENSHOTS.md
```

A CSS-only change moves no control but restyles every figure; the report says
so on its own line (**APP CSS CHANGED**).

**Step 2 — edit the prose.** The report names the file; search it for the key.

**Step 3 — fix `SCREENSHOTS.md`.** Update that figure's **Controls:** line, and
its instructions if the steps changed.

**Step 4 — retake the named screenshots** following `SCREENSHOTS.md`, same setup
as the first time (device mode 1440 × 900, pixel ratio 2).

**Step 5 — accept and rebuild.**

```bat
python docs\manual\tools\doc_drift.py --accept
python docs\manual\tools\build_pdf.py
```

**Step 6 — commit** the prose, the new PNGs, `SCREENSHOTS.md` and
`ui_inventory.json` together. The snapshot is what makes the *next* drift report
meaningful, so never commit it on its own.

---

## 5. The two conventions everything rests on

**Every control's session-state key appears in backticks, exactly once, in the
User Manual's reference table** — `` `hw_topk` ``. That is how `doc_drift.py`
knows a control is documented. A control with no `key=` is identified as
`file:label` — `app.py:Response`; give new controls a key and this stays easy.

**Every figure in `SCREENSHOTS.md` has a Controls: line** listing what it shows:

```
### 08-convergence.png
Controls: `hw_effort` `hw_pole_tol_pct` `hw_gain_tol_pct` `hw_topk`
```

That line is the only thing standing between you and a manual full of
screenshots showing buttons that no longer exist.

`doc_drift.py` also cross-checks file names: a screenshot listed in
`SCREENSHOTS.md` but used by no document is reported as an orphan, and an image
a document uses but `SCREENSHOTS.md` doesn't list is reported as unlisted.

---

## 6. Figures in the documents

**One scale for every figure.** `build_pdf.py` reads each PNG's pixel width and
prints all figures at the same millimetres per screen pixel, so UI text is the
same size in every figure; only a crop wider than the text column shrinks. That
is why screenshots are taken in device mode at a fixed 1440 × 900 and pixel
ratio 2 — `CAPTURE_DPR = 2` in `build_pdf.py` assumes it.

| Write in Markdown | Result |
|---|---|
| `![Caption.](img/x.png)` | one figure, at the common scale |
| `![Caption.](img/a.png) ![](img/b.png)` | a **row**: side by side, one caption, one number |
| `![Caption.](img/x.png){.half}` / `{.narrow}` | 74 % / 58 % of the column — rare override |

A row is how a long panel prints legibly: the whole sidebar as one figure would
set its text at about 4 pt, so it is taken as two halves (`02a`, `02b`).

**The alt text is the caption**, numbered automatically. Write it as a sentence
that says what to look at, so the page still works for someone who skips the
picture.

**Chapter breaks.** Every `#` chapter starts a new page — right for the User
Manual, which is read by chapter. The Quick Start sets `chapter-breaks: no` in
its front matter so its short chapters flow on.

**Photograph states, not just screens.** The blocked report, the "Solving…"
caption and the no-realization warning are the most valuable figures in the
manual: they are what someone is staring at when they go looking for help.

**Figure provenance is free:** the app's title line reads
`Filter Synthesizer v<version> - <response> <type>`, so any full-page figure
records the version it was taken at.

---

## 7. Shipping the documents with the EXE

`BUILDING.md` ships a folder, not a bare EXE. `build.bat` (step 7) copies
`docs\Quick_Start.pdf` and `docs\User_Manual.pdf` into `dist\FilterSynthesizer\`,
next to the EXE, so an offline user has them. They are not in
`FilterSynthesizer.spec` — the app never reads them, so bundling them into
`_internal` would only duplicate them.

The PDFs are committed at release time only: run `doc_drift.py`, then
`build_pdf.py`, and commit both PDFs with the version bump. `build.bat` never
generates them; if one is missing it warns and builds without it.

---

## 8. Known limits

- **Solver output is not bit-reproducible.** The multistart search can return a
  different BOM ordering between versions, so figures 11–13 may not reproduce
  exactly. The prose treats component values as an example, never a promise.
- **`ui_inventory.py` is a static reader.** It follows widgets called on
  column objects (`cols[0].text_input`), keys and labels held in a variable,
  loop variables, and `with st.sidebar:` / `with tab_x:` blocks through
  function calls. A control built in a loop is listed once, with its key
  normalised to a pattern (`hw_solve_*`). What it cannot see is a value passed
  in from another function: `key=f"{key_prefix}_svg_{n}"` stays `*_svg_*`, and
  a label it cannot resolve shows as `<expr>` — describe those by their panel.
- **Monte-Carlo is reproducible** — the seed is a control — so figure 15 is
  stable as long as the example and the seed are.
