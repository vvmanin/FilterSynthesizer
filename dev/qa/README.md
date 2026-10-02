# dev/qa — QA & benchmark harness

One command that checks and benchmarks the whole tool after a significant
change: sidebar combinations through the real `app.py`, auto-pairing, section
solves on every family, the PDF report, the LTspice export run in LTspice, and
the PyInstaller bundle. Committed with the code, never bundled
(`FilterSynthesizer.spec` collects top-level `*.py` only).

```bash
python dev/qa/run_qa.py --level smoke                      # 24 designs, ~200 solves: ~1.5 min
python dev/qa/run_qa.py --level standard --compare last    # 800 designs, ~7.5k solves: ~9 min
python dev/qa/run_qa.py --level full --compare last        # 2069 designs, ~65k solves + build: ~1 h (est.)
```

Times measured on a Ryzen 9 7950X3D (16 cores / 32 threads, 30 workers, warm
cache); a slower PC covers the same matrix in proportionally more time, or use
`--budget`. `--build` adds `build.bat` + the exe test (~2 min) to any level.

Run it with the interpreter you run the app with (`build_venv\Scripts\python`
is the reference environment: `build.bat` creates it). Every prompt —
missing packages, the build — comes in the first minute; the rest runs
unattended. `--yes` answers yes to all of them.

Each run writes `dev/qa/results/<date>-<time>_<host>_<level>/` (gitignored):

| File | Content |
|---|---|
| `summary.md` | Verdict per area, problems grouped with reproduce ids, pinned findings, solve outcomes by family / op-amp / envelope / section family, cell + UI coverage, Streamlit deprecations, performance, benchmark, regression diff (`--compare`) |
| `fs016_pairing.md` | FS-016 Phase-1 input: every pairing flag with the design that reproduces it |
| `results.jsonl` | One record per task (`type` = check, warmup, design, job, e2e, ltspice, bench, server, build, exe, task_failure) |
| `env.json`, `run.json` | Machine, packages (with bounds), git state; level, workers, utilisation, coverage |
| `e2e/<design>/` | `report.pdf` and the LTspice files of each end-to-end design |
| `logs/` | Worker logs, check-script logs, server / build / exe logs |
| `jobs/` | Captured solve jobs per design (used by `--resume`) |

The private TF / kernel cache lives in `dev/qa/.cache/` (seeded from the
repo's `tf_cache*.json`; `--cold` uses a fresh one and derives every cell).

## Options

| Option | Effect |
|---|---|
| `--level smoke\|standard\|full` | Matrix size (`matrix.LEVELS`) |
| `--budget 90m` | No new tasks after the budget (minus a reserve for the benchmark); skipped tasks are counted in the summary |
| `--workers N` | Pool size (default: logical threads − 2, capped by free RAM / 700 MB) |
| `--only` / `--skip` | Stages: `checks, ui, solve, e2e, ltspice, bench, server, build, exe` |
| `--build` / `--no-build` | Build + exe test at smoke / standard (full builds by default, after a prompt) |
| `--compare last\|<dir>` | Regression diff against the newest run of the same level, or a given run |
| `--resume <dir>` | Continue an interrupted run; finished tasks are skipped, pending solves and LTspice runs are re-queued |
| `--summarize <dir>` | Rewrite the summary of an existing run (with `--compare`) |
| `--designs N`, `--seed S`, `--cold`, `--yes` | Debug subset, matrix seed, cold cache, no prompts |

Exit code: 0 = no FAIL row in the verdict, 1 = at least one, 2 = preflight
stopped (required packages missing).

## What runs

| Phase | Module | What it checks |
|---|---|---|
| 0 Preflight | `preflight.py` (stdlib only) | Python 3.11 / 3.12 (others warn); every `requirements.txt` bound vs the installed version — **out of bounds is a warning and is recorded as compatibility evidence**, the file is never edited; missing packages → y/N `pip install`; cairosvg really renders (Cairo DLL); LTspice (env `FS_QA_LTSPICE`, else the standard install paths); `python` on PATH for `build.bat`; CPU / RAM; git state; the per-user op-amp / LTspice overlay (it changes results between machines) |
| 1 Existing checks | `checks.py` | `verify.py` (its verdict line — it never exits non-zero) and every `dev/fs*/check_*.py`, discovered; `doc_drift.py` as information |
| 2 Warm-up | `tasks_solve.warmup` | Every cell template + kernel source derived / loaded once before the parallel phases |
| 3 UI designs | `tasks_ui.design_task`, `analysis.py` | The design is set through the real widgets (AppTest), then from what the app published: **root conservation** (every engine pole / zero in exactly one stage), **cascade** (product of stage TFs = engine H(s), passband gain = the sidebar's), **spec** (−α at the corners, ripple ≤ α, −A_s at the reported stopband edges), **FS-016 flags** (`analysis.FLAG_TEXT`), `st.error` / `st.warning` / exceptions of the final state |
| 4 Job capture | `tasks_ui.apply_variant` | Topology tab in Batch mode per variant; *Solve all sections* with `topology_tab._proc_submit` patched to record the jobs — the exact routing, `_build_cfg`, FS-033 dual solve of the app, no copy of it here. 1st-order sections solve in-app and are recorded |
| 5 Solves | `tasks_solve.solve_job` | Each unique job as captured; outcome class below |
| 6 End-to-end | `tasks_ui.e2e_task` | Same flow with synchronous solves; best row per section picked through the BOM table's selection state (sorted by snap cost), Monte Carlo, the PDF through the Tab-4 button (all options), the LTspice export the app builds |
| 7 LTspice | `tasks_export.py` | Every exported `.cir` simulates (`-b`) without errors and with all probes; an exact-frequency copy (`.ac list`) matches the export's loaded-MNA expectation (FS generic, ≤ 1e-3 of the peak); every `.asc` (via `-netlist`) gives the same probes as its `.cir` |
| 8 Benchmark | `tasks_solve.bench_task` | The FS-028 section set (`dev/fs028/fs028_common`) per preset, one solve per physical core, nothing else running; min of 3 repeats (the diff flags > 25 % and > 0.1 s) |
| 9 Servers / build | `tasks_build.py` | `streamlit run` from source: `/_stcore/health` + `/_stcore/script-health-check`; `build.bat` (dist tree, app modules bundled, no `dev/` file, PyInstaller warn file); the exe with a private `LOCALAPPDATA` and `FILTERSYNTHESIZER_NO_BROWSER=1`: health, script health, `--selftest` `[FAIL]` lines |
| 10 Summary | `summarize.py` | `summary.md`, `fs016_pairing.md`, regression diff |

### Verdicts and what is not a failure

`FAIL` = something broke (exception, unregistered cell, lost pole, spec check
fail, invalid PDF, LTspice error, build / exe problem). `WARN` = worth a look
(`NO_BOM_SUSPICIOUS`, app-reported limits, corner 0.05–0.2 dB off).
Deliberately *not* failures, learned from the first runs:

- **App refusals**: the app stops on a spec it cannot design ("Band too wide for
  the delay-preserving bandpass", "This H(s) cannot be used", "Mathematical
  Constraint Violation"): design status `refused`, listed under *App refusals
  and limits* (`ui_map.REFUSAL_MESSAGES`). Its session state is a stale design,
  so it is not analysed.
- **App-reported limits**: "Stopband Attenuation does not meet requirements" /
  an edge marked "(does not meet A_s)": the app says so itself → WARN.
- **Stopband edge margin**: the engine reports f_s where the attenuation is
  A_s − 0.1 dB (`filter_solvers.find_crossing`); the check allows 0.15 dB, and
  each edge is tested over its printed rounding interval (steep notch skirts).
- **Delay-preserving BP corners**: the translation BP keeps its −α corners only
  for b ≲ 0.3 (FS-006 note §4.3); wider designs get `corner: info`.

### Solve outcome classes

| Class | Meaning | Verdict |
|---|---|---|
| `OK` | BOMs; best snap cost ≤ `SNAP_COST_WARN`, ideal-case shape error ≤ `SHAPE_ERR_WARN` | pass |
| `DEGRADED` | BOMs, but one of those limits exceeded | pass (listed) |
| `NO_BOM_EXPECTED` | No BOM; `solvability_probe` says infeasible (gain / structure) | pass |
| `NO_BOM_ENVELOPE` | No BOM; the probe realises it with more R / C spread than the envelope allows | pass |
| `NO_BOM_SUSPICIOUS` | No BOM although the probe says it fits the envelope | WARN |
| `ERROR` / `TIMEOUT` / `CRASH` | The solve raised, hung past `TIMEOUTS["solve"]`, or killed its worker | FAIL |

A design or job id reproduces it: the id spells the sidebar state
(`EL_BP_a23_1-2kHz_g10_a0.1_s40-60_abs` = Elliptic band-pass, asymmetric
LP 2 / HP 3, 1–2 kHz, gain 10, α 0.1 dB, A_s 40 / 60 dB, 3rd-order absorb on);
the full design dict is in `results.jsonl`. Job ids add the variant
(`MFB-slow-narrow-atten-alt-Thorough` = family, op-amp selector, envelope,
section gain override 0.5, family option — MFB LS / Gained-MFB, AM Equalize off,
inverting 1st-order — and preset when not Balanced) and the section. Designs
are seeded per structural case, so a smaller level is a subset of a larger one
and ids stay stable when the matrix grows.

## Extending (roadmap)

| When … | Edit |
|---|---|
| a response / option / limit is added (FS-013 all-pass, FS-020, FS-025 …) | `matrix.py`: `RESPONSES`, `_structural()`, `_draw_params()`. Until then the summary lists the new UI option under *UI options the matrix does not cover* |
| a run finds a bug worth keeping an eye on | add its design to `matrix.PINNED` (runs at every level; the summary's *Pinned findings* says whether it still reproduces) |
| a widget key or label changes | `ui_map.py` (the run fails with `widget not found: … (update dev/qa/ui_map.py)`) |
| a cell / family is added (FS-014, FS-013) | nothing: routing is captured from the app, coverage comes from the registry (`tf_derivation_v2.all_cells`, `cells_first_order.all_cells`) |
| an item ships a check script | nothing, if it is `dev/fs*/check_*.py` (options: `checks.ARGS`) |
| a new stage is needed (FS-011 save/load round-trip, FS-024 step response, FS-009 noise, FS-010 QSpice, FS-026 transient, FS-027 loaded response vs LTspice) | a task function in a module here, one `pool.submit` in `run_qa.py` (usually from `on_e2e`, which has the solved design and its export), one verdict row in `summarize.py` |
| FS-016 lands | run `--level full --compare <the pre-FS-016 run>`: `fs016_pairing.md` before / after, and every changed stage assignment as a regression row |

## Limits

- AppTest cannot send plotly click events or edit `st.data_editor` tables:
  no manual pairing (FS-017); Custom H(s) specs are injected through
  `_custom_spec`; BOM rows are picked through the table's selection state.
- `LTspice -b` on an `.asc` opens the GUI (and the updater) in LTspice 24:
  schematics always go through `-netlist`.
- The source-server smoke and the exe test run the real process pools; the
  AppTest workers run the engine and the solves in-process (patched), so
  pool failures show up only there.
- Timings depend on the machine; compare benchmark rows within one machine.
