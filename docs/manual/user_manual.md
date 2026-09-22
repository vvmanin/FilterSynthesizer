---
title: User Manual
subtitle: What every control does, which topology to choose, and what to do when the solver says no.
app: FilterSynthesizer
cover: yes
---

<!-- ===================================================================
     OUTLINE — structure only. Prose is written in the next pass.
     Rules this document lives by:
       * LOOKUP, not narrative. Nobody reads it end to end. Tables, short
         entries, one idea per row. Anything that wants to be a story
         belongs in the Quick Start.
       * Every control appears in §3 exactly once, with its session-state
         key in backticks. tools/doc_drift.py uses those keys to tell you
         which paragraphs a UI change broke — so the key is not decoration,
         it is the index.
       * §6 is keyed by the VERBATIM message the app prints, because that
         is what a stuck reader will search for.
       * Regenerate the §3 table bodies with:
             python tools/ui_inventory.py --markdown
         then edit the "What it does" column into plain language. Never
         hand-type the keys; copy them from the inventory.
     =================================================================== -->

# 1. How the app is organized

<!-- TODO prose, one page. The single most useful thing to say first:
     the five tabs are a chain of gates, and the app enforces it.
        sidebar spec -> engine
        Tab 1/2  read the approximation
        Tab 3    pair into sections        -> publishes the cascade
        Tab 4    solve each section, PICK a BOM
        Tab 5    cascade response, Monte-Carlo, PDF report
     Say which message you get when you skip ahead, and that picking a row
     is a separate act from solving. Include the gate diagram as a table. -->

![The five tabs, in the order they must be used.](img/01-first-run.png)

![Tab 4 with both sections listed — the point where the maths becomes hardware.](img/07-topology-top.png)

## What persists and what resets

<!-- TODO: the behaviours that surprise people, all real:
     - a solve runs in the BACKGROUND; other sections and tabs stay live
     - results are cached per configuration, so re-opening a tab never re-solves
     - changing the sidebar spec regenerates the cascade and drops picks for
       sections that no longer exist
     - the engine debounces ~0.85 s after a sidebar edit before recomputing
     - the Topology tab re-renders every 2 s while it is open -->

# 2. Concepts and vocabulary

<!-- TODO: one short entry each, no theory lectures. Link from first use in
     the Quick Start. -->

| Term | Means |
|---|---|
| Section (stage) | TODO — one cascade element: a biquad, a biquad with an absorbed real pole, or a lone real pole |
| Absorption | TODO — an odd order leaves a real pole; it joins a complex pair to form a 3rd-order section |
| Cell | TODO — a named circuit realization, e.g. `3LP-gained`, `2BP-MFB-QE` |
| BOM | TODO — one candidate set of component values for a section |
| Snapping | TODO — moving ideal values onto E-series parts |
| `sens_score` | TODO — component sensitivity; the default ranking, lower is better |
| `snap_cost` | TODO — how far the snapped parts sit from the ideal values |
| Envelope | TODO — the R/C limits a solution must fit inside |
| Design vs realized | TODO — the maths target vs what the snapped parts actually do |
| Sign | TODO — inverting cells contribute −1; an odd count inverts the output |

# 3. Control reference

<!-- Regenerate the table bodies with: python tools/ui_inventory.py --markdown -->

## 3.1 Sidebar — the specification

<!-- TODO: table. Controls to cover (keys from the inventory; the two radios
     at the top have no key and are identified by label):
     Response, Filter Type, Order `widget_sym_order`, Asymmetric
     `is_asym_checkbox`, LP/HP order `widget_lp_order` `widget_hp_order`,
     Unit, Corner Frequency `widget_fc`, passband corners `widget_f1`
     `widget_f2`, Passband Gain `widget_gain`, passband ripple/attenuation,
     stopband `sym_as_val` / `as_sl_val` / `as_su_val`, the Response
     Modifications checkboxes, and the notch pins `pin_notch_*` /
     `val_notch_*`. -->

### Coupled behaviour worth knowing

<!-- TODO: the sidebar is not a set of independent boxes.
     - the two corner frequencies push each other apart
     - symmetric/asymmetric orders stay in sync until HP is edited
     - order limits change with the response and filter type
     - which Response Modifications appear, and when -->

### Validation rules

<!-- TODO: table of what is rejected and why. Verbatim:
     "Band-Reject filters require an EVEN total order."
     "Asymmetric Elliptic Band-Reject requires BOTH LP and HP orders to be even."
     "Asymmetric Elliptic Band-Reject requires the difference between orders to be <= 2."
     plus the soft warning "Max total order for Asymmetric Elliptic is 15." -->

## 3.2 Tab 1 — Response Plots

<!-- TODO -->

## 3.3 Tab 2 — Roots & Transfer Function

<!-- TODO -->

## 3.4 Tab 3 — Biquad Pairing & Cascading

<!-- TODO: pairing, the mnemoscheme, gain distribution across stages -->

## 3.5 Tab 4 — Topology & Hardware Synthesis

### Convergence Settings

![Convergence Settings. The defaults are right for a first run.](img/08-convergence.png){.half}

| Control | Key | Default | What it does |
|---|---|---|---|
| Search thoroughness | `hw_effort` | Balanced | TODO — breadth of the multistart search: Fast / Balanced / Thorough |
| Pole & notch frequency tolerance (%) | `hw_pole_tol_pct` | 1.00 | TODO — how far realized f₀ and f_z may sit from target; gates the parallel-C2 path only, and does not constrain Q |
| Passband gain tolerance (%) | `hw_gain_tol_pct` | 0.50 | TODO — DC gain for LP/notch, HF gain for HP |
| Max candidates to refine | `hw_topk` | 30 | TODO — how many ideal solutions get op-amp corrected and snapped |

<!-- TODO: one paragraph on WHEN to change these, which is the part a tooltip
     cannot carry: raise thoroughness only after a solve returns nothing and
     the envelope is already wide; lower top_k when a gained or notch cell is
     slow. -->

### Per-section settings

![Section settings: family, op-amp, envelope, E-series.](img/09-section-settings.png){.tall}

| Control | Key | Default | What it does |
|---|---|---|---|
| Topology family | `hw_fam_*` | VCVS | TODO — see §4 |
| Op-amp | `hw_opamp_choice_*` | Ideal | TODO — library part or Custom |
| A_ol / GBWP / Ro | `hw_aol_*`, `hw_gbwp_*`, `hw_ro_*` | 1e5 / 1e6 / 1200 Ω | TODO — only when Custom is selected |
| C_min, C_max | `hw_cmin_*`, `hw_cmax_*` | 6.8e-5, 1e-2 µF | TODO |
| R_min, R_max | `hw_rmin_*`, `hw_rmax_*` | 0.3, 2000 kΩ | TODO |
| Max R ratio | `hw_ratio_*` | 500 | TODO — rejects solutions whose resistor spread exceeds this |
| Capacitor E-series | `hw_cser_*` | E12 | TODO — single choice |
| Resistor E-series | `hw_rser_*_*` | E48 | TODO — multiple allowed |
| Equalize R, C values | `hw_ameq_*` | on | TODO — AM only: balanced handbook design |
| Gained MFB | `hw_mfbgain_*` | off | TODO — 2nd-order HP-notch only |
| Lower Sensitivity MFB | `hw_mfbls_*` | off | TODO — LP-notch only |
| Eliminate R1 | `hw_mfbls_nor1_*` | off | TODO — LP-notch LS, 2nd order only |

### Per-section gain

| Control | Key | What it does |
|---|---|---|
| Custom DC / HF / DC-HF gain | `hw_dc_chk_*`, `hw_dc_val_*` | TODO — override the gain the Pairing tab allocated |
| Override section Ki | `hw_ki_chk_*`, `hw_ki_val_*` | TODO — band-pass sections only; the units follow the section shape |
| Realization (1st order) | `hw_fo_real_*` | TODO — inverting or non-inverting |

<!-- TODO: explain the gain label changing with the section kind (DC gain /
     HF gain / DC-HF gain / Center gain) and why: where the passband sits. -->

### The candidates table

![Candidate BOMs. Component columns adapt to the cells that were solved.](img/11-bom-table.png)

| Control | Key | What it does |
|---|---|---|
| Sort by | `hw_sortf_*` | TODO — sorts by the raw numeric value, not the displayed string |
| Descending | `hw_sortd_*` | TODO |
| The table itself | `hw_df_*` | TODO — click a row to select that BOM |
| Solve section / Re-solve | `hw_solve_*` | TODO |

<!-- TODO: how to read a row — Topology, Sens, Snap cost, the gain column,
     then components. Why ties are broken by snap_cost. Why component columns
     come and go. Why capacitors are "not snapped" and resistors are. -->

### Overall filter

<!-- TODO: the realized DC/HF and passband gain readouts, the band-reject
     two-band variant, the band-pass centre-gain variant, and the inverted-
     output warning. -->

## 3.6 Tab 5 — Resulting Response & Schematic

| Control | Key | Default | What it does |
|---|---|---|---|
| Capacitor tol (%) | `resp_ctol` | 5.0 | TODO |
| Runs | `resp_runs` | 2000 | TODO |
| Distribution | `resp_dist` | Gaussian (tol = 3σ) | TODO — the rated tolerance is the spec limit in both modes |
| Seed | `resp_seed` | 0 | TODO — makes the band reproducible |
| Envelope | `resp_envelope` | p1–p99 | TODO |
| Run Monte-Carlo | `resp_mc_run` | — | TODO |

<!-- TODO: the resistor tolerance bands UI, the phase / group-delay overlay
     checkboxes, and the stacked per-section schematics.
     Fill the keys from tools/ui_inventory.py --print. -->

### Report options

![The report section list. Everything is on by default.](img/16-report-block.png)

| Control | Key | What it does |
|---|---|---|
| Cover page | `rep2_cover` | TODO |
| Polynomial coefficient table | `rep2_coeff` | TODO |
| Normalized prototype roots | `rep2_norm` | TODO |
| Per-section quality metrics | `rep2_metrics` | TODO |
| Design warnings | `rep2_warn` | TODO |
| Generate Report | `report_btn` | TODO |
| Download PDF | `report_dl` | TODO |

# 4. Choosing a topology family

<!-- TODO: the decision, not the theory. A table of what each family can and
     cannot realize, and what it costs:
       VCVS (Sallen-Key)  — non-inverting, fewest parts
       MFB (Friend)       — inverting, gain is a free ratio, covers every
                            section kind including the 3rd-order band-pass
                            that the other two cannot
       AM (Ackerberg-Mossberg) — three op-amps, matched R7=R8 gives active
                            GB compensation; Q error scales (f0/ft)^2
     Then: which family to pick when a section has an absorbed real pole,
     when the gain is below unity, and when Q is high. -->

# 5. Op-amps and what they do to the design

<!-- TODO: the library, the Custom entry, what A_ol / GBWP / Ro each change.
     Why the op-amp is per-section. Then the finite-Ro HF hump: what the
     warning looks like, why it is physics and not a synthesis error, and
     the two mitigations the app itself suggests. -->

# 6. When it goes wrong

<!-- Keyed by the message the app prints, verbatim, because that is what
     someone searches for. TODO: fill Cause and Fix for each. -->

| The app says | Cause | Fix |
|---|---|---|
| Finalize the cascade in **Biquad Pairing & Cascading** first | TODO | TODO |
| Finalize the cascade and synthesize sections in the **Topology** tab first | TODO | TODO |
| Pick a BOM for every section … (still pending: section N) | TODO | TODO |
| Report generation is not available yet — section N still has no selected BOM | TODO | TODO |
| No realization inside the component envelope. Widen R/C limits, relax Max R ratio, add more R series, or raise thoroughness. | TODO | TODO |
| A realizable design exists, but not inside your envelope — it needs a resistor ratio ≈N× | TODO | TODO |
| A realizable design exists inside your envelope … but the search didn't reach it. | TODO | TODO |
| No realizable solution at this gain. You asked for X, but the nearest gain this topology can realize is ≈Y | TODO | TODO |
| No realizable solution exists for these targets — the pole/zero shape itself isn't achievable with this topology. | TODO | TODO |
| Solve disabled — select VCVS, MFB or AM for this section. | TODO | TODO |
| Section N is a 3rd-order band-pass … Switch this section to **MFB (Friend)** | TODO | TODO |
| This section needs DC gain < 1 … not yet wired in the solver. Coming soon. | TODO | TODO |
| ⏳ … solver not yet available; gated to avoid mis-solving on a mismatched cell. | TODO | TODO |
| No R series selected — defaulting to E48. | TODO | TODO |
| E3 must be added to `unified_solver_v2._E_SERIES`, else the cap grid is empty. | TODO | TODO |
| ⚠ HF resonance (~N dB at F) — the realized response peaks above the design | TODO | TODO |
| ⚠ Output is **inverted** — an odd number of inverting (inv) stages | TODO | TODO |
| **Mathematical Constraint Violation:** … | TODO | TODO |
| Schematic SVG not found for `cell` — expected … | TODO | TODO |
| PDF report support needs the `reportlab` package | TODO | TODO |
| Report data is not being published by `app.py` — missing … | TODO | TODO |
| **Engine Error:** … | TODO | TODO |
| **Solver error:** … | TODO | TODO — the "Details" expander under it holds the traceback |

![An envelope too tight to build in. The message names the ratio the design actually needs.](img/20-no-realization.png)

![The report refuses to build until every section has a chosen BOM, and says which are missing.](img/22-report-blocked.png)

## No ⬇ PNG button next to ⬇ SVG

<!-- TODO: it is a live cairosvg indicator. What is lost, and the pip line. -->

## Reporting a problem

<!-- TODO: both error kinds — Engine Error and Solver error — carry a
     collapsed "Details (paste this into a bug report)" expander with the
     traceback. Say what to include: the app version from the title line,
     the sidebar spec, the section header line (order, fp, f0, Q), the
     section's settings, and that details block.
     DECIDE: whether to mention that a developer can show the exact solver
     call by launching with FILTERSYNTHESIZER_DEBUG=1. -->

# 7. Reference tables

<!-- TODO:
     - E-series offered for capacitors (E3/E6/E12) and resistors (E12..E96)
     - op-amp library entries with their A_ol / GBWP / Ro
     - default envelope values and what they mean in real parts
     - where files live: the writable app-data folder, the cache, the
       user-editable Section_Schematic_Diagrams folder
     - optional Python packages and what each one buys -->

# 8. What the PDF report contains

<!-- TODO: section by section, so a reader knows whether to tick a box.
     Include the two gain-reference rules (specified passband gain for a
     single section, section design target for a cascade) and the fact that
     the Monte-Carlo page reports the top of the passband ripple. -->
