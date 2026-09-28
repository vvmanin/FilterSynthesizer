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
| `filter_engine.py` | 32K | Top-level `synthesize_{lowpass,highpass,bandpass,bandreject}()` — calls solvers, returns engine_results dict |
| `filter_solvers.py` | 92K | **Largest file.** All prototype solvers: Butterworth/Chebyshev/InvCheby/Elliptic for LP, plus BP transforms. Key fns: `solve_butterworth_lp`, `solve_chebyshev_lp`, `solve_inv_chebyshev_lp`, `solve_elliptic_lp`, `synthesize_bgb`, `synthesize_asym_cheby1_bp`, `synthesize_slot_based_inv_cheby`. Lines ~1-700 = LP solvers; ~700-1000 = BP helpers; ~1000+ = BP/BR asymmetric synthesis |
| `delay_solvers.py` | 46K | **Delay responses (FS-006): Bessel + Equiripple Delay, lowpass and bandpass only (`DELAY_FILTER_TYPES`).** Delay-normalized prototypes (`bessel_poles_delay` via `scipy.signal.besselap`; `eqdelay_poles` = seeded Remez/Newton, `lru_cache`d), corner product W_α = ω_α·τ (`corner_product`), engine drop-ins `design_delay_lp` (corner-normalized LP + stopband notches on fixed poles, corner held by `_corner_hold_scale`; `ems_m` > 0 = FS-021 Equiripple Magnitude Stopband: `solve_delay_stopband_notches` places the notches so every stopband hump (`_stopband_humps`) sits at −A_s) and `synthesize_delay_bp` (pole-translation or classic BP), `select_delay_order` (order from specs), `make_delay_info` (the `delay_info` summary). Checks: `python dev/fs006/check_delay_solvers.py` (§11 = FS-021) |
| `filter_utils.py` | 4K | Tiny: `evaluate_h()`, `find_crossing()` |
| `tf_utils.py` | 8K | Root formatting, LaTeX poly display, coefficient tables |
| `pairing_utils.py` | 28K | `build_stage_bricks`, `auto_pair_stages` (LP/HP/BP/BR), `classify_section`, `compute_stage_gains` |
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
| `tf_derivation_v2.py` | 20K | — | **Cell registry/dispatcher**: `all_cells()`, `derive_all()`, `get_cases()`, caching. Routes to cell modules by `topo["family"]` |
| `tf_symbols.py` | 4K | — | Shared SymPy symbols (s, R1-R7, C1-C4, A_ol, etc.) |

### Tier C — Synthesis Engine (family-agnostic)
| File | Size | Purpose |
|---|---|---|
| `unified_solver_v2.py` | 56K | **Second largest.** Phase-1/Phase-3/ZM multistart optimization. `run_synthesis()` entry point. Lines ~1-130 = grids/layout helpers; ~270-450 = worker init + phase1; ~450-660 = phase3 worker; ~660-930 = snap/sensitivity/harvest; ~926+ = `run_synthesis` orchestrator |
| `zero_manifold_solver.py` | 20K | Alternative solver using zero-manifold approach: `solve_zero_manifold()` |
| `nonideal_solver.py` | 16K | Op-amp GBW correction: `solve_nonideal()` — adjusts ideal solutions for finite op-amp bandwidth |
| `discrete_snapper.py` | 20K | E-series resistor/cap snapping: `snap_to_hardware()` |
| `scoring.py` | 16K | Solution quality scoring: `score_solution()`, response metrics (fc error, Q error, gain error, passband ripple) |
| `filter_synthesis.py` | 8K | Thin wrapper: `synthesize()` — calls unified_solver → nonideal → snap → score pipeline |
| `solvability_probe.py` | 16K | Quick feasibility check before full solve: `probe_cell()`, `assess()` |
| `first_order_solver.py` | 16K | Closed-form 1st-order section solver: `synthesize_first_order()` |
| `verify.py` | 8K | Self-test / validation utilities |
| `opamp_library.py` | 8K | **Single source of op-amp parts** (FS-005). Merges built-in `opamp_library.json` with the per-user overlay `%LOCALAPPDATA%\FilterSynthesizer\opamp_library_user.json` (user entry of the same name overrides a built-in). `choices()`/`resolve()` for the UI picker, `named_params()` for the solvers' string API, `save_user()`/`delete_user()`/`check_new_name()` for UI edits, `IDEAL_PARAMS`, `CUSTOM_DEFAULT`. Reloads on file mtime change; bad entries go to `load_errors()`. No Streamlit import |
| `pool_utils.py` / `mp_fix.py` | 4K / 4K | Engine process pool: `get_process_pool` (`st.cache_resource`), `run_in_pool` rebuilds it after `BrokenProcessPool` and whenever an app `.py` file is newer than the pool (a `streamlit run` session hot-reloads modules in the main process only; the workers would keep stale code). `mp_fix` keeps multiprocessing alive under PyInstaller + NumPy on Windows. **Fragile** — see `CLAUDE.md` |
| `opamp_library.json` | 2K | Built-in op-amp data (JSON, hand-editable; A_ol V/V, GBWP_hz Hz, Ro_ohm Ω, optional description/spice_model/en_nV_rtHz/in_pA_rtHz). Shipped next to the exe by `build.bat` + bundled fallback; env `FILTERSYNTHESIZER_OPAMP_FILE` set by `launcher.py` |

### Tier D — UI & Visualization
| File | Size | Purpose |
|---|---|---|
| `app.py` | 100K, 1982 lines | **Main Streamlit app.** Sidebar (L253-290): response/type/order/freq/gain/ripple/delay. 5 tabs below. |
| `ui_components.py` | 23K | Sidebar widget blocks: `draw_filter_type` (drops HP/BR for the delay responses), `draw_order_block`, `draw_delay_order_block`, `draw_frequency_block` (+ delay anchor / τ₀ for delay LP), `draw_gain_block`, `draw_ripple_block`, `draw_delay_block`, `draw_modifications_block`, `validate_filter_specs`; `_mem_widget` = keyed widget whose value survives being hidden; `design_control(key, variant)` = keyed container styled as a design-control box (FS-001; CSS in `app.py` targets `st-key-dctl_*` blue / `st-key-dcsel_*` amber), used by all tabs |
| `topology_tab.py` | 68K | Tab 4 "Topology": per-section hardware solver UI, convergence settings, results table, schematics. `render_topology_tab()` entry. Lines ~1-160 = helpers; ~220-510 = job management; ~510-800 = results rendering; ~800-880 = 1st-order; ~880-1150 = `_render_section`; ~1150-1290 = `_render_overall` cascade; ~1294 = `render_topology_tab` |
| `response_tab.py` | 24K | Tab 5 "Resulting Response": ideal vs realized Bode overlay, Monte Carlo. `render_response_tab()` entry |
| `schematic_svg.py` | 28K | SVG schematic annotation & rendering: `render_svg()`, `build_annotations()`, `download_buttons()` |
| `hw_plots.py` | 20K | Hardware-level Bode/phase/GD plots, Monte Carlo engine: `monte_carlo()`, `bode_figure()` |
| `launcher.py` | 8K | Desktop launcher (exe/port/browser); points `FILTERSYNTHESIZER_SVG_DIR` / `FILTERSYNTHESIZER_OPAMP_FILE` at the exe-adjacent user-editable copies |

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

## app.py Section Map (1982 lines)

| Lines | Section |
|---|---|
| 1-51 | Imports, process pool |
| 53-134 | Band-reject gain equalization helpers (`_stage_rho`, `_section_peak_mag`, `_equalize_dc_hf_ks`) |
| 136-248 | Page config, CSS (block 7 = FS-001 design-control box styles) |
| 253-290 | **Sidebar** — response, filter type, order, freq, gain, ripple, delay specs, modifications |
| 292-435 | **Delay responses (FS-006): order / corner resolution** — `select_delay_order` (From specs), derived corner under the τ₀ anchor, BP fold check; `_render_delay_summary` and `_render_ems_readout` (FS-021) for Tab 1 |
| 437-461 | Main canvas title, validation, 5-tab creation |
| 463-788 | **Section 1: Engine run** — background workers for LP/HP/BP/BR synthesis (L514-536: FS-021 Equiripple Magnitude Stopband mode state — `ems_ok`/`ems_on`/`ems_m`, Active-row defaults, pins parked/restored), result unpacking, notch-grid display mapping |
| 790-955 | Report snapshot (`report_spec` rows incl. delay rows, detail windows) |
| 957-1289 | **Tab 1: Response Plots** (tab_plots) — magnitude, passband detail, group-delay detail (delay responses), phase/GD, probes, manual notch grid (delay LP: Equiripple Magnitude Stopband checkbox, Active column, readout) |
| 1292-1455 | **Tab 2: Roots & TF** (tab_roots) — zero/pole tables, LaTeX TF display, coefficient table |
| 1457-1976 | **Tab 3: Biquad Pairing** (tab_pairing) — mnemoscheme (in the `pair_box` design-control box), stage gain distribution, per-stage TF details |
| 1978-1979 | **Tab 4: Topology** → delegates to `render_topology_tab()` |
| 1981-1982 | **Tab 5: Response** → delegates to `render_response_tab()` |

---

## Data Flow

```
Sidebar specs
  → filter_engine.synthesize_*()
    → filter_solvers.solve_*_lp() → poles, zeros, gain
      (Bessel / Equiripple Delay: delay_solvers.design_delay_lp / synthesize_delay_bp; the
       order may come from app.py's resolution step via delay_solvers.select_delay_order)
  → pairing_utils.auto_pair_stages() → stages list
  → [Tab 1-3: plots, roots, pairing in app.py]
  → [Tab 4: topology_tab]
    → topology_tab.section_kind() → pairing_utils.family_from_section() → solver kind
    → tf_derivation_v2.get_cases() → symbolic TFs
    → solvability_probe.assess() → feasibility
    → unified_solver_v2.run_synthesis() → continuous solutions
    → nonideal_solver.solve_nonideal() → op-amp corrected
    → discrete_snapper.snap_to_hardware() → E-series BOM
    → scoring.score_solution() → ranked results
    → schematic_svg.render_svg() → annotated circuit
  → [Tab 5: response_tab]
    → hw_plots.monte_carlo() → statistical spread
    → hw_plots.bode_figure() → ideal vs realized overlay
```

---

## Key Data Structures

- **engine_results**: dict returned by `filter_engine.synthesize_*()`. LP/HP: `poles`, `zeros`, `k`, `f_stop_hz`, `ideal_notches_hz`, `sb_status` (+ `reflection_zeros` for LP). BP: `poles`, `zeros`, `reflection_zeros`, `k`, `f_stop_hp_hz`, `f_stop_lp_hz`, `ideal_notches_hp_hz`, `ideal_notches_lp_hz`, `sb_status_hp`, `sb_status_lp`. BR: the BP keys (with `ideal_notches_hz`) + `actual_as_db`; note `f_stop_hp_hz` is the lower edge. **Delay responses only** (Bessel / Equiripple Delay): `delay_info` = `{response, kind (LP/BP), order, alpha_db, delta, eps_ref, max_q, bp_mapping, n_origin_zeros, pole_scale, warnings, tau_dc_s, tau_nom_s, tau_center_s, w_prod, corner_hz, center_hz, flat_band_hz, delay_pp_pct, ems}` (seconds / Hz) from `delay_solvers.make_delay_info`; `ems` (FS-021, LP only) = `{m, converged, max_err_db, pole_scale}` or None, and the solved notches are then in `ideal_notches_hz`.
- **stage**: dict with `pole_id`, `zero_ids`, `absorbed_real_id`, `type`. Created by `auto_pair_stages()`.
- **brick**: dict with `id`, `root`, `w0`, `Q`, `type` ("Complex Pair"/"Real"). From `build_stage_bricks()`.
- **topo**: dict with `family`, `order`, `gain`, `notch`, `has_R7`, plus symbolic circuit equations.
- **case**: dict with ideal/nonideal TF expressions, component names, substitution maps. From `derive_all()`.
- **cfg**: synthesis config dict — `w0`, `Q`, `wz`, `K`, cap/res series, ranges. Built by `topology_tab._build_cfg()`.

---

## Query Instructions

1. **For sidebar/UI changes**: look at `ui_components.py` (widget blocks) or `app.py` L250-290 (sidebar chassis).
2. **For plot changes**: `plot_utils.py` (main response plots) or `hw_plots.py` (hardware-level/MC plots).
3. **For adding a new cell topology**: see any `cells_*.py` as template + register in `tf_derivation_v2.py`.
4. **For solver/optimization bugs**: `unified_solver_v2.py` (main solver), `zero_manifold_solver.py` (alt solver).
5. **For filter math/approximation**: `filter_solvers.py` (prototype), `filter_engine.py` (orchestrator). Bessel / Equiripple Delay live in `delay_solvers.py`; their sidebar is `ui_components.draw_delay_order_block` / `draw_delay_block` plus the resolution step in `app.py` (L270-400); math base in `dev/FS-006_bessel_eqdelay_design_note.md`, numeric checks `python dev/fs006/check_delay_solvers.py`.
6. **For schematic rendering**: `schematic_svg.py`.
7. **For scoring/metrics**: `scoring.py`.
8. **For topology tab UI**: `topology_tab.py` — use section map above to target the right line range.
9. **For response tab / Monte Carlo**: `response_tab.py` + `hw_plots.py`.
10. **For 1st-order sections**: `first_order_solver.py` + `cells_first_order.py`.
11. **For op-amp parts/parameters**: edit `opamp_library.json` (data) or `opamp_library.py` (loading, naming rules, user overlay). The per-section picker + Edit popover is `topology_tab._opamp_picker`; `response_tab._eval_opamp` and `topology_tab.opamp_label` read the same choice.
