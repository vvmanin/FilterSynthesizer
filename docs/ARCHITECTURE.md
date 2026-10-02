# Filter Synthesizer — Architecture Map & Quick-Reference Instructions

Streamlit app for analog active-filter design: spec → poles/zeros → biquad cascade → component-level hardware realization with op-amp non-ideal correction, E-series snapping, Monte Carlo analysis, and schematic SVG output.

---

## Four Tiers

| Tier | Role |
|---|---|
| **A — Approximation** | User spec → prototype poles/zeros → cascade stages |
| **B — Cell Library** | Symbolic circuit topologies (Sallen-Key / MFB) → ideal & non-ideal transfer functions |
| **C — Synthesis Engine** | Solve component values → op-amp correct → snap to E-series → score |
| **D — UI / Viz** | Streamlit tabs, plots, schematics, Monte Carlo |

---

## File Map

### Tier A — Approximation Math
| File | Size | Purpose |
|---|---|---|
| `filter_engine.py` | 32K | Top-level `synthesize_{lowpass,highpass,bandpass,bandreject}()` — calls solvers, returns engine_results dict; `synthesize_custom()` = thin entry over `custom_tf.design_custom` (FS-007) |
| `custom_tf.py` | 44K | **Custom H(s) (FS-007), no Streamlit.** `parse_spec` (forms coeff / f0q / ts / roots → normalized zpk + ω_n; table cells may be typed text), `poly_roots` (balanced `np.roots` + precision-aware repeated-root `_merge`), `zeros_from_numerator` (even-part method), `snap_roots`, gate (`gate_poles` / `gate_zeros` / `gate_structure`), `lp_prototype_to` (`scipy.signal.lp2*_zpk`), `peak_gain` / `detect_type` / `measure_edges` (log-magnitude sums), `conditioning`, `preflight_pairing` (read-only `pairing_utils`), `roots_sig`, entry `design_custom` → `{errors, warnings, preflight, info, engine_results}`. Checks: `python dev/fs007/check_custom_tf.py` |
| `filter_solvers.py` | 92K | **Largest file.** All prototype solvers: Butterworth/Chebyshev/InvCheby/Elliptic for LP, plus BP transforms. Key fns: `solve_butterworth_lp`, `solve_chebyshev_lp`, `solve_inv_chebyshev_lp`, `solve_elliptic_lp`, `synthesize_bgb`, `synthesize_asym_cheby1_bp`, `synthesize_slot_based_inv_cheby`. Lines ~1-700 = LP solvers; ~700-1000 = BP helpers; ~1000+ = BP/BR asymmetric synthesis |
| `delay_solvers.py` | 46K | **Delay responses (FS-006): Bessel + Equiripple Delay, lowpass and bandpass only (`DELAY_FILTER_TYPES`).** Delay-normalized prototypes (`bessel_poles_delay` via `scipy.signal.besselap`; `eqdelay_poles` = seeded Remez/Newton, `lru_cache`d), corner product W_α = ω_α·τ (`corner_product`), engine drop-ins `design_delay_lp` (corner-normalized LP + stopband notches on fixed poles, corner held by `_corner_hold_scale`; `ems_m` > 0 = FS-021 Equiripple Magnitude Stopband: `solve_delay_stopband_notches` places the notches so every stopband hump (`_stopband_humps`) sits at −A_s) and `synthesize_delay_bp` (pole-translation or classic BP), `select_delay_order` (order from specs), `make_delay_info` (the `delay_info` summary). Checks: `python dev/fs006/check_delay_solvers.py` (§11 = FS-021) |
| `filter_utils.py` | 4K | Tiny: `evaluate_h()`, `find_crossing()` |
| `tf_utils.py` | 8K | Root formatting, LaTeX poly display, coefficient tables |
| `pairing_utils.py` | 28K | `build_stage_bricks`, `auto_pair_stages` (LP/HP/BP/BR), `classify_section` (+ `notch_forcing_error` / `NOTCH_EPS`: Q-aware pure-notch rule; `near_notch_section`: FS-033 dual-solve test), `compute_stage_gains` |
| `plot_utils.py` | 24K | Magnitude/phase/GD/pole-zero/mnemoscheme Plotly figures |

### Tier B — Cell Library (symbolic topology definitions)
| File | Size | Family | Topology type |
|---|---|---|---|
| `cells_lp.py` | 12K | LP | Sallen-Key (VCVS), 12 cells: order×gain×notch×R7 grid |
| `cells_hp.py` | 12K | HP | Sallen-Key |
| `cells_bp.py` | 12K | BP | Sallen-Key |
| `cells_notch.py` | 12K | Notch | Sallen-Key |
| `cells_first_order.py` | 8K | 1st-order | LP/HP first-order cells |
| `cells_mfb.py` | 16K | LP | MFB (multiple-feedback) |
| `cells_mfb_hp.py` | 20K | HP | MFB |
| `cells_mfb_bp.py` | 12K | BP | MFB |
| `cells_mfb_notch.py` | 16K | Notch | MFB |
| `cells_am.py` / `cells_am_hp.py` / `cells_am_bp.py` / `cells_am_notch.py` | 4-5K each | LP / HP / BP / Notch | Ackerberg-Mossberg (thin per-family wrappers) |
| `cells_am_core.py` | 16K | — | Shared Ackerberg-Mossberg nodal (MNA) model |
| `tf_derivation_v2.py` | 31K | — | **Cell registry/dispatcher**: `all_cells()`, `derive_all()`, `get_cases()`, caching. Routes to cell modules by `topo["family"]`. FS-028 S2-1: `get_templates()` = each cell's design-parametric ideal case (`build_ideal(topo, {})`, targets symbolic) + non-ideal case, derived once per process and cached in the TF cache (`template_key`, `IDEAL_MODEL_REV`); `design_cases()` = the solver pipeline's cases from those templates, per-cell numeric `targets` attached, no symbolic work |
| `tf_symbols.py` | 4K | — | Shared SymPy symbols (s, R1-R7, C1-C4, A_ol, etc.) |

### Tier C — Synthesis Engine (family-agnostic)
| File | Size | Purpose |
|---|---|---|
| `unified_solver_v2.py` | 100K | **Second largest.** Phase-1/Phase-3/ZM multistart optimization. `run_synthesis()` entry point: builds the Phase-1 start list, then `_search` (Phase 1 → `harvest` → Phase 3 + ZM) runs either **batched in-process** (default, FS-028 S2-2: `cell_kit` + `batch_phase1` / `batch_phase3` / `batch_zm` on `batched_lm`, no pool) or, with env `FS_SOLVER=trf`, the legacy scipy-TRF workers in a process pool. Lines ~1-410 = grids/layout helpers, `rescale_isolated_r5r6`; ~410-475 = `worker_packs` + `_init_worker` (TRF pool path); ~475-640 = bounds + `phase1_worker`; ~640-820 = `_assemble_solution` (Phase-3 gates + sens score, shared by both paths); ~820-945 = `phase3_worker` (+ R5 ladder) + `zm_worker`; ~945-1340 = snap scale / sensitivity proxy (`_sens_funcs`) / `harvest` (`merge_dup_hints` = batched path) / `dedup`; ~1340-1645 = batched path (`P1_METRICS`, `P3_HINT_METRICS`, `P3_METRIC`, `cell_kit`, `batch_phase1`, `batch_phase3`, `batch_zm`); ~1645+ = `apply_equalize`, `run_synthesis` |
| `batched_lm.py` | 11K | **Batched projected log-space Levenberg–Marquardt (FS-028 S2-2).** `solve(fun, jac, X0, lb, ub, …)` runs N small least-squares problems as one numpy batch: steps in u = log x, minimum-norm (Tikhonov) steps measured in the `metric` "log" or "x", projected active set on the box, `lb == ub` = fixed variable (columns fixed in every row leave the linear algebra), working set compacted as rows finish, TRF-like termination (`status`, `conv`); `jac=None` = forward differences in log space (`fd_jac_log`). `legacy_trf()` reads the `FS_SOLVER=trf` fallback switch |
| `zero_manifold_solver.py` | 20K | Alternative solver using zero-manifold approach: `solve_zero_manifold()`; `prep_cell_funcs` (lambdify, serial path) / `assemble_cell_funcs` (same dict from compiled kernels); `solve_one_combo` = `combo_starts` (the 8 starts) + TRF + `gate_combo` (bounds / ratio / pole / notch / DC-gain gates + score — shared with `unified_solver_v2.batch_zm`) |
| `nonideal_solver.py` | 21K | Op-amp GBW correction: `solve_nonideal()` — adjusts ideal solutions for finite op-amp bandwidth. Runs in-process (FS-028 S2-1), on `design_cases` and the process's `make_response_func` memo. S2-2: each topology's rows are one batched LM (`_batch_worker` → `_correct_batch`; responses on a flattened row × frequency grid by `_flat_response`, which the AM MNA evaluator accepts too); `FS_SOLVER=trf` = the per-row TRF `_correct` |
| `cell_kernels.py` | 12K | **Compile-once cell kernels (FS-028 S2-1).** Lambdified functions kept as generated Python source, rebuilt anywhere by `load(srcs)` (exec in lambdify's numpy namespace, ~ms). Design-independent groups per (cell, Equalize variant), generated once and cached on disk (`<tf cache stem>_kernels_k<KERNEL_REV>.json`) by `sources(case, group)`: `gain` (a1, a2, h0, hinf — worker set), `sens` (harvest's a1/a2 proxy), `zm` (den_i/num_i), `res` (DESIGN-PARAMETRIC res/jac/r5, targets as trailing args, `bind`) — the batched solve path (S2-2): `load_batched` replays the same sources on N rows at once (a broadcasting `array`), `bind_rows` returns (N, m) / (N, m, n) / (N,). `design_sources(case)` = this design's numeric res/jac/r5, source-identical to the old per-design lambdify (memory LRU) — used only by the `FS_SOLVER=trf` path, which stays bit-identical to S2-1. Check: `python dev/fs028/check_kernels.py` |
| `discrete_snapper.py` | 22K | E-series resistor/cap snapping: `snap_to_hardware()`. Tries the two nearest grid values per resistor (2ⁿ combos); FS-028 S2-2b evaluates all combos of a solution in one response call (`_combo_responses`, rows flattened onto the frequency axis; per-combo fallback) |
| `scoring.py` | 16K | Solution quality scoring: `score_solution()`, response metrics (fc error, Q error, gain error, passband ripple) |
| `filter_synthesis.py` | 8K | Thin wrapper: `synthesize()` — calls unified_solver → nonideal → snap → score pipeline |
| `solvability_probe.py` | 16K | Quick feasibility check before full solve: `probe_cell()`, `assess()` |
| `first_order_solver.py` | 16K | Closed-form 1st-order section solver: `synthesize_first_order()` |
| `verify.py` | 8K | Self-test / validation utilities |
| `opamp_library.py` | 8K | **Single source of op-amp parts** (FS-005). Merges built-in `opamp_library.json` with the per-user overlay `%LOCALAPPDATA%\FilterSynthesizer\opamp_library_user.json` (user entry of the same name overrides a built-in). `choices()`/`resolve()` for the UI picker, `named_params()` for the solvers' string API, `save_user()`/`delete_user()`/`check_new_name()` for UI edits, `IDEAL_PARAMS`, `CUSTOM_DEFAULT`. Reloads on file mtime change; bad entries go to `load_errors()`. No Streamlit import |
| `pool_utils.py` / `mp_fix.py` | 4K / 4K | Engine process pool: `get_process_pool` (`st.cache_resource`), `run_in_pool` rebuilds it after `BrokenProcessPool` and whenever an app `.py` file is newer than the pool (a `streamlit run` session hot-reloads modules in the main process only; the workers would keep stale code). `mp_fix` keeps multiprocessing alive under PyInstaller + NumPy on Windows. **Fragile** — see `CLAUDE.md` |
| `opamp_library.json` | 2K | Built-in op-amp data (JSON, hand-editable; A_ol V/V, GBWP_hz Hz, Ro_ohm Ω, optional description/spice_model/en_nV_rtHz/in_pA_rtHz). Shipped next to the exe by `build.bat` + bundled fallback; env `FILTERSYNTHESIZER_OPAMP_FILE` set by `launcher.py` |
| `LTspice_Library/` | — | FS-008 data folder (bundled + copied next to the exe like the SVGs): `symbols.asc` (symbol calibration), `opamps/` (`_FS_generic.asc`, `_seat_template.asc`, maintainer dummies), `cells/` (the maintainer's hand-drawn cell templates `<TEMPLATE>.asc` — one per `spice_cells` template id, split slots C1b / C2b — plus `_cell_template.asc` to start one; all 26 template ids have one, auto-layout is the fallback), `models/` (dev-machine model files, git-ignored; users' vendor files live in the per-user overlay's `models/` with `consent.json`: an imported model is `<PART>__<vendor file>` (top-level-only lines commented out) + the generated wrapper `FS_<PART>.lib`, FS-029). Tool-written files come from `dev/fs008/make_ltspice_library.py`; a copy saved back by LTspice is authoritative |

### Tier D — UI & Visualization
| File | Size | Purpose |
|---|---|---|
| `app.py` | 107K, 2170 lines | **Main Streamlit app.** Sidebar (L311-387): response/type/order/freq/gain/ripple/delay (Custom H(s): mode, scale, detected-type line, freq/gain/ripple blocks). 4 tabs below (FS-003 folded Roots & TF into Response Plots). |
| `ui_components.py` | 23K | Sidebar widget blocks: `draw_filter_type` (drops HP/BR for the delay responses), `draw_order_block`, `draw_delay_order_block`, `draw_frequency_block` (+ delay anchor / τ₀ for delay LP), `draw_gain_block`, `draw_ripple_block`, `draw_delay_block`, `draw_modifications_block`, `validate_filter_specs`; `_mem_widget` = keyed widget whose value survives being hidden; `design_control(key, variant)` = keyed container styled as a design-control box (FS-001; CSS in `app.py` targets `st-key-dctl_*` blue / `st-key-dcsel_*` amber), used by all tabs. Custom H(s) (FS-007): `draw_unit_radio` (shared keyless Unit radio), `_draw_band_corners`, `draw_custom_mode_block` / `_scale_block` / `_type_block` / `_frequency_block` (complete) / `_proto_frequency_block` (Corners / Normalized width) / `_gain_block` / `_ripple_block`, `draw_custom_editor` (panel; state in `_custom_spec`, fixed-row `st.data_editor` + ＋/− buttons, bases fixed per `_custom_rev`; keyed widgets seeded by `_bind`), `fill_custom_k` (greyed K / A₀ under Normalize), `draw_custom_diagnostics` |
| `topology_tab.py` | 130K | Tab 3 "Topology": per-section hardware solver UI, convergence settings, results table, schematics. `render_topology_tab()` entry. **Batch mode** (FS-028 S2-4 step 1, toggle `hw_batch`, default off): one shared op-amp + component envelope above all sections (widget keys `hw_<field>_all`, `SHARED_TAG`) and a *Solve all sections* button that queues every unsolved section through the same `_submit`; each section keeps its family, gain and family options. `settings_tag(n)` → `"all"` or `n` is the one switch point; every reader of the envelope / op-amp keys goes through it (`opamp_label`, `response_tab._eval_opamp`, `spice_ui._part_model`, `report_ui._section_env`). The envelope and op-amp widgets are `ui_components._mem_widget`s, so the hidden set (per-section in Batch mode, shared in manual) keeps its values in `_mem_hw_*` mirrors. `render_topology_tab` is a plain function: it registers the tab body (`_topology_body`) as a fragment with `run_every` = 2 s only while solves are pending (`hw_jobs` non-empty), else no auto-rerun; the body requests one full run when pending-ness changes. Lines ~85-110 = `settings_tag`, `opamp_label`; ~165-420 = job management: every section solve (manual or batch) goes to `_solve_pool`, a shared persistent `ProcessPoolExecutor` of `SOLVE_WORKERS` = cores − 1 (on-demand spawn; env `FILTERSYNTHESIZER_SOLVE_WORKERS`), via `_proc_submit` (rebuilt when broken or when a source file changed); more sections than workers queue (`_job_state` → solving / queued); `_drain_finished` cancels queued solves of a stale `hw_gen`; `FS_SOLVER=trf` keeps the thread + gate path (`_job_runner`), since that solve spins its own pool; ~817 = `_convergence_inputs`; ~860-1090 = op-amp picker + library callbacks, `_envelope_inputs`; ~1095 = `_section_settings`; ~1372 = `_merge_results` (FS-033: one result from a section's two jobs); ~1394 = `_render_results`; ~1602 = 1st-order; ~1658 = `_notch_cells` (the 2N cell set per family); ~1698 = `_render_section` (FS-033 near-notch LPn/HPn sections submit a second 2N job, `alt`, and rank the merged BOMs by snap cost); ~2231 = `_render_overall` cascade; ~2380-2435 = batch helpers (`_seed_shared`, `_shared_settings`, `_batch_status`); ~2437 = `render_topology_tab` / `_topology_body` |
| `response_tab.py` | 24K | Tab 4 "Resulting Response": ideal vs realized Bode overlay, Monte Carlo, LTspice export block (`spice_ui`, FS-008). `render_response_tab()` entry |
| `schematic_svg.py` | 28K | SVG schematic annotation & rendering: `render_svg()`, `build_annotations()`, `download_buttons()` |
| `hw_plots.py` | 20K | Hardware-level Bode/phase/GD plots, Monte Carlo engine: `monte_carlo()`, `bode_figure()` |
| `spice_cells.py` | 28K | **Netlist IR (FS-008), no Streamlit.** One table per template id (26) read off every cell's non-ideal KCL: parts on named nodes with open/short state per variant (92 cells). `gating(cell)` (superset + node map after the shorts), `section_ir(row, opamp)` (SI units, split caps, AM R8), `cascade_ir` (nets IN / S<k> / S<k>_<node> / OUT, `section_nets` + `cascade_net`, designators R201 / C202A / U201, optional ideal buffers), `mna_ac` (numeric AC solve, tool op-amp model), `dc_floating_nodes`, `display_alias` / `designator`. `python spice_cells.py <TEMPLATE|CELL>` prints a template's drawing spec. New cell = new table entry (the check fails otherwise) |
| `spice_export.py` | 32K | **LTspice writer (FS-008), no Streamlit.** One export cycle, one IR, both forms: `.cir` netlists (phase-1 format, frozen) and `.asc` schematics (drawn by `spice_asc`: the cell's template from `spice_opamps.cell_template`, else auto-layout; `build_export(templates=False)` forces auto-layout; `sections[i]['drawing']` says which), AC nominal + AC Monte-Carlo (`.param` per resistor band, `.func TOL`, `.step param run`), `.meas` probes with loaded-MNA expectations (FS generic only), README. Op-amp per section: the `spice_model` dummy from `spice_opamps` (X-line pin order + directives, same model in `.cir` and `.asc`) or FS generic subckt (Ideal clamp A_ol 1e9 / GBWP 10 THz). `build_export(..., generic_vendor, spec_brief, hf_hump)` → files + extra_files (consented vendor model files, custom `.asy`) + `vendor` ({stem: files / bundled / missing / unconsented / sections / source}), `zip_bytes`. A part whose vendor model is not installed is exported with FS generic (FS-029: `sections[i]['not_installed']`, one warning per part); a bundled wrapper travels with the vendor copy it includes (`spice_opamps.model_closure`). Headers carry the spec brief (`report_spec_brief`) and the HF-hump recommendation (`hf_note`); the nominal `.asc` saves only V(OUT) so LTspice plots it. A drawing failing its self-check is not written (`.cir` still is). Hooks for FS-026 (`source_value`, `directives`). Checks: `python dev/fs008/check_spice_export.py` |
| `spice_asc.py` | 39K | **LTspice `.asc` model (FS-008 phases 2-3), no Streamlit.** `parse`/`serialize` (UTF-16/UTF-8/cp1252 read, ASCII CRLF write), `Calibration` (pin offsets + SYMBOL text from `LTspice_Library/symbols.asc`, stock `.asy` defaults), `connectivity` (§4.2 joining rules; pin/label on a wire middle and crossings are errors), `place_seat` (dummy block into a seat, §5.4), `draw_template` (phase 3: a section from its hand-drawn template — open parts deleted, shorted parts → wire, merged-node labels dropped, dangling stubs pruned, seats refilled with the dummy), `draw_cascade` (template per section, else auto-layout: seats, R row, C row, labelled stubs; column; directives right; a template failing its own check falls back) + `check_drawing` (§4.3 self-check vs the cascade IR → `AscError`), `netlist_lines` (geometry → LTspice-style netlist, dev check), `parse_asy` |
| `spice_opamps.py` | 22K | **Op-amp model library (FS-008), no Streamlit.** Dummy `.asc` files in `LTspice_Library/opamps` + per-user overlay `%LOCALAPPDATA%\FilterSynthesizer\LTspice_Library\opamps` (same stem wins; env `FILTERSYNTHESIZER_LTSPICE_DIR` / `_USER_DIR`). `load_dummy` = the dummy check (App. A.4) and the netlist form: `xpins` (X-line roles from adapter wiring + symbol geometry: calibration, `.asy` next to the dummy or in LTspice's lib, or `;FS: pins=`), `xmodel`, directives with `.lib` paths resolved (`directives_cir`/`_asc`), `;FS:` meta (vs_min/vs_max/source/note/model_url); a `.lib` target not installed is `missing` (usable, not an error). Vendor files: `ensure_user_dirs` (creates the overlay on the user's PC), `vendor_files`, `consents` / `is_consented` / `record_consent` (`models/consent.json`), `install_model` (a vendor zip, one nested zip deep, or the file itself → only the wanted `.lib`, by base name; encrypted / non-SPICE rejected; for user dummies that name the vendor file). FS-029 import behind a per-part wrapper: `model_candidates` (any extension, zip by content; `scan_subckts` top-level `.subckt` + pins, `guess_roles` from pin names / a pinout note / ADI node-assignment columns, `_name_score` incl. `X` family wildcards), `install_wrapped` (writes `<PART>__<file>` — vendor bytes with top-level-only lines such as `.END` commented out, `localize_model`; `repair_imports` fixes older imports — + `FS_<PART>.lib` = `.subckt FS_<PART> INP INN VCC VEE OUT` / `XV` in the vendor pin order / `.include`; consent + `parts` record in `consent.json` v2, `part_records`), `installed_candidates` (consented pre-FS-029 files), `wrapper_part` / `wrapper_name` / `vendor_copy_name` / `model_closure`; `vendor_files` counts a wrapper without its vendor copy as not installed. `dummies()` (FS generic always present), `calibration()`, `cell_template(tid)` / `cell_templates()` (cells/, user overlay wins); library file texts (`fs_generic_dummy_text`, `opamp2_dummy_text`, seat / cell templates) |
| `spice_ui.py` | 13K | Resulting Response tab block *LTspice export* (Vs, MC runs; model per section picked automatically from the part's `spice_model`, override selectboxes `spice_opamp_{n}` only with `FILTERSYNTHESIZER_DEBUG=1`; *Export <parts> with simplified generic models* `spice_generic_vendor`; *Vendor model files* panel — status, vendor links, disclaimer; per part (FS-029) a product-page link, consent `spice_vendor_consent_{stem}`, the user's zip / model file `spice_vendor_up_{stem}` (or a consented file already in models/), subckt `spice_vendor_sub_{stem}` + pin roles `spice_vendor_role_…` confirmed, *Import* `spice_vendor_inst_{stem}`, re-import `spice_vendor_redo_{stem}`; model table, op-amp model library status, zip download); one call from `response_tab.py` (passes the HF-hump finding) |
| `launcher.py` | 8K | Desktop launcher (exe/port/browser); points `FILTERSYNTHESIZER_SVG_DIR` / `FILTERSYNTHESIZER_OPAMP_FILE` / `FILTERSYNTHESIZER_LTSPICE_DIR` at the exe-adjacent user-editable copies |

### Documentation
| File | Purpose |
|---|---|
| `CLAUDE.md` | Working agreement for Claude Code sessions (navigation, conventions, git policy) |
| `.claude/settings.json` | Claude Code permission policy: denies `git reset` / `git checkout` (Bash) and all git write ops in PowerShell (Claude commits and pushes only its session `claude/*` branch — see `CLAUDE.md`), and edits to schematic SVGs / `docs/*.pdf` / `tf_cache_v6.json`; asks before any other Write/Edit; allows read-only tools, `git status/diff/log`, `python verify.py` |
| `docs/CONTRACTS.md` | Binding cross-tier rules: cell registry interface, section classification, dispatch gate, scoring dispatch, cascade sign, data schemas, perf notes |
| `dev/ROADMAP.md` | Feature work-item state (backlog, priorities) |
| `MFB_INTEGRATION_README.md` | MFB topology integration notes |
| `MFB_BP_NOTCH_README.md` | MFB bandpass/notch specifics |
| `MFB_FOLLOWUP_FIXES.md` | Post-integration bugfixes |
| `PACKAGING.md` | Build/packaging instructions |

---

## app.py Section Map (2170 lines)

| Lines | Section |
|---|---|
| 1-51 | Imports, process pool |
| 53-134 | Band-reject gain equalization helpers (`_stage_rho`, `_section_peak_mag`, `_equalize_dc_hf_ks`) |
| 149-309 | Page config, CSS (block 7 = FS-001 design-control box styles; 7b = FS-003/FS-004 compact tabs, scoped to the `rp_plots`/`rp_roots` (Response Plots, + grey frame on `rp_roots`) and `bp_body` (Biquad Pairing) containers; 8 = FS-002 notch-box gap) |
| 311-387 | **Sidebar** — response, filter type, order, freq, gain, ripple, delay specs, modifications; Custom H(s) (FS-007): mode (+ Scale in complete mode) right after Response, Filter Type radio only in prototype mode (complete: a slot for the detected type), then its frequency / gain-mode / α-A_s blocks |
| 389-532 | **Delay responses (FS-006): order / corner resolution** — `select_delay_order` (From specs), derived corner under the τ₀ anchor, BP fold check; `_render_delay_summary` and `_render_ems_readout` (FS-021) for Tab 1 |
| 534-559 | Main canvas title (`title_slot`), validation (skipped for Custom), 4-tab creation |
| 561-621 | **Custom H(s) (FS-007): editor panel + resolution step** — `draw_custom_editor` in container `rp_custom` (top of Response Plots, before `real_fc`), cached `_design_custom` → `synthesize_custom` (complete mode: `filter_type` None → detected type written back, sidebar line + title), greyed K (`fill_custom_k`), diagnostics, `st.stop()` on gate errors; writes measured edges (`f1_val`/`f2_val`), prototype α, display orders and the As-entered G back into the sidebar variables |
| 623-955 | **Section 1: Engine run** — Custom takes `custom_res['engine_results']`; background workers for LP/HP/BP/BR synthesis (L676-701: FS-021 Equiripple Magnitude Stopband mode state — `ems_ok`/`ems_on`/`ems_m`, Active-row defaults, pins parked/restored), result unpacking, notch-grid display mapping (not for Custom) |
| 956-1162 | Report snapshot (`report_spec` rows incl. delay rows and Custom rows, `report_spec_short`, `report_spec_brief` = the one-line spec for the LTspice headers, detail windows; `w_norm` = `custom_info['w_n']` for Custom) |
| 1150-1471 | **Tab 1: Response Plots** (tab_plots, container `rp_plots`) — magnitude, passband detail, group-delay detail (delay responses), phase/GD, stopband-edge readout (not for Custom), probes, manual notch grid (not for Custom; delay LP: Equiripple Magnitude Stopband checkbox, Active column, readout) |
| 1473-1638 | **Tab 1, continued: Roots & TF** (tab_plots, container `rp_roots`; FS-003) — Domain Scale + units radio (sets `scale_type` / `map_unit_choice`; Biquad Pairing uses its own `unit_pair`), grey frame (CSS 7b), Root Locations expander (pole/zero tables + K), Pole-Zero Map expander, H(s) expander with form radio (`tf_form_roots`) |
| 1640-2165 | **Tab 2: Biquad Pairing** (tab_pairing, container `bp_body`) — mnemoscheme (in the `pair_box` design-control box; Custom signature = `roots_sig`), stage gain distribution (BR Equalize hidden for an asymmetric Custom BR), per-stage TF details; writes `hw_sections`, `hw_filter_type`, `hw_pb_gain` |
| 2167-2168 | **Tab 3: Topology** → delegates to `render_topology_tab()` |
| 2170+ | **Tab 4: Response** → delegates to `render_response_tab()` |

---

## Data Flow

```
Sidebar specs
  → filter_engine.synthesize_*()
    → filter_solvers.solve_*_lp() → poles, zeros, gain
      (Bessel / Equiripple Delay: delay_solvers.design_delay_lp / synthesize_delay_bp; the
       order may come from app.py's resolution step via delay_solvers.select_delay_order)
  → pairing_utils.auto_pair_stages() → stages list
  → [Tab 1-2: plots + roots, pairing in app.py]
  → [Tab 3: topology_tab]
    → topology_tab.section_kind() → pairing_utils.family_from_section() → solver kind
    → tf_derivation_v2.design_cases() → per-design cases from once-derived templates
      (cell_kernels → compiled residual / Jacobian / gain kernels, design-parametric)
    → solvability_probe.assess() → feasibility
    → unified_solver_v2.run_synthesis() → continuous solutions
      (batched_lm: Phase 1 / Phase 3 / ZM as numpy batches, in-process; FS_SOLVER=trf = pool)
    → nonideal_solver.solve_nonideal() → op-amp corrected (one batched LM per topology)
    → discrete_snapper.snap_to_hardware() → E-series BOM
    → scoring.score_solution() → ranked results
    → schematic_svg.render_svg() → annotated circuit
  → [Tab 4: response_tab]
    → hw_plots.monte_carlo() → statistical spread
    → hw_plots.bode_figure() → ideal vs realized overlay
```

---

## Key Data Structures

- **engine_results**: dict returned by `filter_engine.synthesize_*()`. LP/HP: `poles`, `zeros`, `k`, `f_stop_hz`, `ideal_notches_hz`, `sb_status` (+ `reflection_zeros` for LP). BP: `poles`, `zeros`, `reflection_zeros`, `k`, `f_stop_hp_hz`, `f_stop_lp_hz`, `ideal_notches_hp_hz`, `ideal_notches_lp_hz`, `sb_status_hp`, `sb_status_lp`. BR: the BP keys (with `ideal_notches_hz`) + `actual_as_db`; note `f_stop_hp_hz` is the lower edge. **Delay responses only** (Bessel / Equiripple Delay): `delay_info` = `{response, kind (LP/BP), order, alpha_db, delta, eps_ref, max_q, bp_mapping, n_origin_zeros, pole_scale, warnings, tau_dc_s, tau_nom_s, tau_center_s, w_prod, corner_hz, center_hz, flat_band_hz, delay_pp_pct, ems}` (seconds / Hz) from `delay_solvers.make_delay_info`; `ems` (FS-021, LP only) = `{m, converged, max_err_db, pole_scale}` or None, and the solved notches are then in `ideal_notches_hz`. **Custom H(s)** (FS-007): `poles`, `zeros`, `k` (peak-normalized) + `custom_info` (entry form/scale, w_n, measured edges, entered peak gain G, detected type, counts, warnings, pre-flight, `roots_sig`; full list in `docs/CONTRACTS.md` §6); no stopband / notch / delay keys.
- **stage**: dict with `pole_id`, `zero_ids`, `absorbed_real_id`, `type`. Created by `auto_pair_stages()`.
- **brick**: dict with `id`, `root`, `w0`, `Q`, `type` ("Complex Pair"/"Real"). From `build_stage_bricks()`.
- **topo**: dict with `family`, `order`, `gain`, `notch`, `has_R7`, plus symbolic circuit equations.
- **case**: dict with ideal/nonideal TF expressions, component names, substitution maps. From `derive_all()` / `get_cases()` (per-design: `res_eqs` numeric in the targets). Solver-path cases from `design_cases()` (FS-028 S2-1) keep `res_eqs` design-parametric and add `targets` = the cell's numeric (p1, w0, wz, Q, K) (`TF.TARGETS` order; K per cell in DC/HF-gain mode); `apply_equalize` adds `equalized: True` (its own kernel variant) and `equalize_subs` (re-applied after the design subs by `cell_kernels.design_sources`). Evaluate a design-parametric `res_eqs` only through `cell_kernels`.
- **kit** (batched path, FS-028 S2-2): `unified_solver_v2.cell_kit(case)` = one cell's function dict — the Phase-1/3 worker's keys (`layout`, `res_f`, `jac_f`, `r5_f`, `a1_f`, `a2_f`, `h0_f`, `hinf_f`, `nidx`, `res_cols`, `var_names`; scalar, targets bound) plus `res_b` / `jac_b` / `r5_b` (X (N, n) in var_list order) and `zm` (the `ZM.gate_combo` dict, ZM cells only). `batch_phase1` returns `len(P1_METRICS)` × the task list (one result per start and step metric).
- **cfg**: synthesis config dict — `w0`, `Q`, `wz`, `K`, cap/res series, ranges. Built by `topology_tab._build_cfg()`.

---

## Query Instructions

1. **For sidebar/UI changes**: look at `ui_components.py` (widget blocks) or `app.py` L250-290 (sidebar chassis).
2. **For plot changes**: `plot_utils.py` (main response plots) or `hw_plots.py` (hardware-level/MC plots).
3. **For adding a new cell topology**: see any `cells_*.py` as template + register in `tf_derivation_v2.py`.
4. **For solver/optimization bugs**: `unified_solver_v2.py` (main solver), `zero_manifold_solver.py` (alt solver). The local solver is `batched_lm.py` (default); set env `FS_SOLVER=trf` to compare against the legacy scipy-TRF pool path. Solver performance (FS-028): analysis in `dev/FS-028_solver_performance_analysis.md` (§12 = S2-2), harness in `dev/fs028/` (benchmark `bench_sections.py`, §6 comparison `compare_s22.py`); compiled kernels in `cell_kernels.py` (checked by `python dev/fs028/check_kernels.py`).
5. **For filter math/approximation**: `filter_solvers.py` (prototype), `filter_engine.py` (orchestrator). Bessel / Equiripple Delay live in `delay_solvers.py`; their sidebar is `ui_components.draw_delay_order_block` / `draw_delay_block` plus the resolution step in `app.py` (L389-532); math base in `dev/FS-006_bessel_eqdelay_design_note.md`, numeric checks `python dev/fs006/check_delay_solvers.py`.
6. **For schematic rendering**: `schematic_svg.py`.
7. **For scoring/metrics**: `scoring.py`.
8. **For topology tab UI**: `topology_tab.py` — use section map above to target the right line range.
9. **For response tab / Monte Carlo**: `response_tab.py` + `hw_plots.py`.
10. **For 1st-order sections**: `first_order_solver.py` + `cells_first_order.py`.
11. **For op-amp parts/parameters**: edit `opamp_library.json` (data) or `opamp_library.py` (loading, naming rules, user overlay). The per-section picker + Edit popover is `topology_tab._opamp_picker` (also the Batch-mode shared picker, key suffix `all`); `response_tab._eval_opamp`, `topology_tab.opamp_label` and `spice_ui._part_model` read the same choice through `topology_tab.settings_tag(n)`. Callbacks that switch the picker use `_set_opamp_choice` (it is a `_mem_widget`).
12. **For Custom H(s) (FS-007)**: math in `custom_tf.py` (math base + gate table: `dev/FS-007_custom_tf_design_note.md`; checks `python dev/fs007/check_custom_tf.py`); sidebar + panel in `ui_components.py` (Custom section at the end); wiring in `app.py` §0 (L561-621) and the `is_custom` gates.
13. **For LTspice export (FS-008)**: netlist IR and MNA in `spice_cells.py`, `.asc` drawing / connectivity / self-check in `spice_asc.py`, op-amp dummies and model netlist form in `spice_opamps.py`, file writing in `spice_export.py`, UI in `spice_ui.py`; library files in `LTspice_Library/` (`dev/fs008/make_ltspice_library.py`, new dummies `dev/fs008/make_opamp_dummy.py`); design, phases and the maintainer guides (op-amp dummies, cell templates) in `dev/FS-008_ltspice_export_design_note.md`; checks `python dev/fs008/check_spice_export.py` (check 13 = FS-029 wrapped vendor models); LTspice wrapper test netlists `python dev/fs029/make_wrapper_test.py` (writes outside the repo).
