# FilterSynthesizer — working agreement

Streamlit app for analog active-filter design: spec → poles/zeros → biquad
cascade → component-level hardware realization (Sallen-Key / MFB / Ackerberg-
Mossberg) with op-amp non-ideal correction, E-series snapping, Monte Carlo and
schematic SVG output. Entry point: `app.py`.

## Navigation — read this first

**`docs/ARCHITECTURE.md` is the map.** Read it before searching the codebase.
It carries the four-tier model (A approximation math / B cell library /
C synthesis engine / D UI+viz), the file-by-file purpose table, line ranges
inside the big files, the call flow, the key data structures (`engine_results`,
`stage`, `brick`, `topo`, `case`, `cfg`) and a "where do I change X" index.

`docs/CONTRACTS.md` holds the binding cross-tier rules (cell registry
interface, section classification, dispatch gate, scoring, sign, schemas).
Consult it before extending Tier B or D. `dev/ROADMAP.md` holds the feature
work-item state.
Other `dev/*.md` files are historical notes on landed work — read one only when
its topic is in play.

## Token economy — this matters here

Several files are very large (`topology_tab.py` ~110K, `filter_solvers.py` ~96K,
`unified_solver_v2.py` ~85K, `app.py` ~84K, `report_pdf.py` ~62K).

- Never read a large file whole. Use ARCHITECTURE.md's line ranges, then `Grep`
  for the symbol, then `Read` with `offset`/`limit` around the hit.
- Prefer `Grep`/`Glob` over `Read` for locating anything.
- Use `Edit` on the exact region; never rewrite a large file with `Write`.
- Delegate broad "where is X" sweeps to a search subagent so the file dumps stay
  out of the main context.

## Commands

```bash
python -m streamlit run app.py     # run from source (Python 3.11 or 3.12)
pip install -r requirements.txt    # runtime deps — bounds are deliberate
python verify.py                   # symbolic self-check of first-order cells
build.bat                          # Windows PyInstaller bundle → dist/
diagnose.bat                       # environment diagnostics
python dev/qa/run_qa.py --level smoke   # QA + benchmark harness (dev/qa/README.md)
```

There is no automated test suite. Validation is: `verify.py` for symbolic cell
work, and running the app and exercising the affected tab for everything else.
When a change touches solver or cell math, say explicitly how it was checked.
After a significant change, run `dev/qa/run_qa.py --level standard --compare last`
(or `full`, which also builds the bundle) and read its `summary.md`.

## Conventions

- Every source file starts with the SPDX header:
  `# SPDX-License-Identifier: GPL-3.0-or-later` and the copyright line.
  Keep it on new files.
- Dependency bounds in `requirements.txt` are load-bearing, especially
  `numpy<2.4` (see the comment block there). Do not relax a bound casually.
- `tf_cache_v6.json` caches derived symbolic transfer functions. Any change to
  a `cells_*.py` nodal model or to `tf_derivation_v2.py` **invalidates it** —
  bump the cache version rather than leaving stale entries.
- `mp_fix.py` and `pool_utils.py` exist to make multiprocessing survive
  PyInstaller + NumPy on Windows. Treat them as fragile.
- A new cell topology = copy an existing `cells_*.py` as template, register it
  in `tf_derivation_v2.py`, route it in `topology_tab.section_kind`, add its schematic
  to `Section_Schematic_Diagrams/`, and add its netlist entry to `spice_cells.py`
  (`python dev/fs008/check_spice_export.py` fails without it). Then check its
  LTspice drawing: a template `LTspice_Library/cells/<TEMPLATE>.asc` (spec:
  `python spice_cells.py <TEMPLATE>`; check 11), or it stays flagged as
  auto-layout until one ships (ROADMAP FS-008, rule for new cells).
- Schematic SVGs in `Section_Schematic_Diagrams/` are draw.io sources and are
  hand-edited; do not regenerate or reformat them programmatically.
- `LTspice_Library/` (FS-008): files saved back by LTspice (`symbols.asc`, dummies,
  cell templates) are authoritative -- never `--force` the generator over them.
  Vendor model files in `models/` are git-ignored and never committed.

## Git — session branch only

- **Local sessions (desktop app / CLI on my PC):** edit the files directly in
  the local project folder (the checkout I opened, not a worktree). Do not
  create a branch, commit or push unless I ask for it in the session. When done,
  list the files you changed.
- **Cloud / remote sessions:** follow the branch workflow below.
- Commit and push only to the session's `claude/*` branch; never to `main`;
  never merge.
- Never run `git reset` or `git checkout` (denied in `.claude/settings.json`),
  and never rewrite pushed history (no rebase, amend or force-push).
- When a unit of work is finished and its checks pass, commit it, push the
  branch, and say which branch and commit. I test that branch locally and merge
  it into `main` myself (fast-forward).

## Working style

- Plan before editing anything that spans tiers; state which tier(s) a change
  touches.
- While planning, ask me questions (AskUserQuestion) when a decision is
  genuinely mine to make — scope, UX choice, trade-off — rather than guessing.
- When the session is in accept-edits mode, make the changes without stopping
  to ask for my consent first; plan-level questions above still apply, but do
  not pause for "shall I proceed?" confirmations.
- Keep changes surgical. This is a single-maintainer codebase with no test net,
  so a small diff that is obviously correct beats a refactor.
- Update `docs/ARCHITECTURE.md` when a file's role changes, a module is added,
  or a data structure gains a field. Update `dev/ROADMAP.md` when an item lands.
