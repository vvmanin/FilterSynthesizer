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

### Tier D — UI & Visualization
| File | Size | Purpose |
|---|---|---|
| `app.py` | 72K, 1460 lines | **Main Streamlit app.** Sidebar (L195-218): response/type/order/freq/gain/ripple. 5 tabs below. |
| `ui_components.py` | 16K | Sidebar widget blocks: `draw_order_block`, `draw_frequency_block`, `draw_gain_block`, `draw_ripple_block`, `draw_modifications_block`, `validate_filter_specs` |
| `topology_tab.py` | 68K | Tab 4 "Topology": per-section hardware solver UI, convergence settings, results table, schematics. `render_topology_tab()` entry. Lines ~1-160 = helpers; ~220-510 = job management; ~510-800 = results rendering; ~800-880 = 1st-order; ~880-1150 = `_render_section`; ~1150-1290 = `_render_overall` cascade; ~1294 = `render_topology_tab` |
| `response_tab.py` | 24K | Tab 5 "Resulting Response": ideal vs realized Bode overlay, Monte Carlo. `render_response_tab()` entry |
| `schematic_svg.py` | 28K | SVG schematic annotation & rendering: `render_svg()`, `build_annotations()`, `download_buttons()` |
| `hw_plots.py` | 20K | Hardware-level Bode/phase/GD plots, Monte Carlo engine: `monte_carlo()`, `bode_figure()` |
| `section_router.py` | 4K | `route_section()` — maps a cascade stage to the correct cell family |
| `launcher.py` | 8K | Desktop launcher (exe/port/browser) |

### Documentation
| File | Purpose |
|---|---|
| `ROADMAP.md` | Master architecture doc, tier contracts, work-item tracker |
| `MFB_INTEGRATION_README.md` | MFB topology integration notes |
| `MFB_BP_NOTCH_README.md` | MFB bandpass/notch specifics |
| `MFB_FOLLOWUP_FIXES.md` | Post-integration bugfixes |
| `PACKAGING.md` | Build/packaging instructions |

---

## app.py Section Map (1460 lines)

| Lines | Section |
|---|---|
| 1-35 | Imports, process pool |
| 37-117 | Band-reject gain equalization helpers (`_stage_rho`, `_section_peak_mag`, `_equalize_dc_hf_ks`) |
| 118-191 | Page config, CSS |
| 192-218 | **Sidebar** — response, filter type, order, freq, gain, ripple, modifications |
| 220-244 | Main canvas title, validation, 5-tab creation |
| 246-444 | **Section 1: Engine run** — background workers for LP/HP/BP/BR synthesis, result unpacking, post-processing |
| 445-802 | **Tab 1: Response Plots** (tab_plots) — magnitude, passband detail, phase/GD, pole-zero map |
| 803-962 | **Tab 2: Roots & TF** (tab_roots) — zero/pole tables, LaTeX TF display, coefficient table |
| 963-1455 | **Tab 3: Biquad Pairing** (tab_pairing) — mnemoscheme, stage gain distribution, per-stage TF details |
| 1456-1457 | **Tab 4: Topology** → delegates to `render_topology_tab()` |
| 1459-1460 | **Tab 5: Response** → delegates to `render_response_tab()` |

---

## Data Flow

```
Sidebar specs
  → filter_engine.synthesize_*()
    → filter_solvers.solve_*_lp() → poles, zeros, gain
  → pairing_utils.auto_pair_stages() → stages list
  → [Tab 1-3: plots, roots, pairing in app.py]
  → [Tab 4: topology_tab]
    → section_router.route_section() → cell family
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

- **engine_results**: dict with keys `poles`, `zeros`, `k`, `slots`, `fc_hz`, etc. Returned by `filter_engine.synthesize_*()`.
- **stage**: dict with `pole_id`, `zero_ids`, `absorbed_real_id`, `type`. Created by `auto_pair_stages()`.
- **brick**: dict with `id`, `root`, `w0`, `Q`, `type` ("Complex Pair"/"Real"). From `build_stage_bricks()`.
- **topo**: dict with `family`, `order`, `gain`, `notch`, `has_R7`, plus symbolic circuit equations.
- **case**: dict with ideal/nonideal TF expressions, component names, substitution maps. From `derive_all()`.
- **cfg**: synthesis config dict — `w0`, `Q`, `wz`, `K`, cap/res series, ranges. Built by `topology_tab._build_cfg()`.

---

## Query Instructions

1. **For sidebar/UI changes**: look at `ui_components.py` (widget blocks) or `app.py` L192-218 (sidebar chassis).
2. **For plot changes**: `plot_utils.py` (main response plots) or `hw_plots.py` (hardware-level/MC plots).
3. **For adding a new cell topology**: see any `cells_*.py` as template + register in `tf_derivation_v2.py`.
4. **For solver/optimization bugs**: `unified_solver_v2.py` (main solver), `zero_manifold_solver.py` (alt solver).
5. **For filter math/approximation**: `filter_solvers.py` (prototype), `filter_engine.py` (orchestrator).
6. **For schematic rendering**: `schematic_svg.py`.
7. **For scoring/metrics**: `scoring.py`.
8. **For topology tab UI**: `topology_tab.py` — use section map above to target the right line range.
9. **For response tab / Monte Carlo**: `response_tab.py` + `hw_plots.py`.
10. **For 1st-order sections**: `first_order_solver.py` + `cells_first_order.py`.
