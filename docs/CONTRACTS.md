# FilterSynthesizer — Cross-Tier Contracts

Binding design rules for the hardware-synthesis layer. These are
state-independent: they hold regardless of which families or cells exist, and
every feature item in `dev/ROADMAP.md` must respect them. Change a contract only
deliberately, and record the change here in the same unit of work.

For the tier model and file map see `docs/ARCHITECTURE.md`.

---

## 1. Family-modular cells behind a registry (Tier B)

- Do **not** monolithically refactor validated cell code, and do **not** pile new
  families into `tf_derivation_v2.py`. It is a **generic symbolic engine**
  (nodal solver, derive machinery, `make_response_func`, `get_cases`/cache) plus
  a `REGISTRY` of per-family cell modules (`cells_*.py`).
- A cell module is keyed by its `FAMILY` tag and exposes:
  `FAMILY`, `all_cells()`, `topo_name(topo)`, `var_list(topo)`, `_gates(topo)`,
  `dc_gain_to_K(topo, design_subs, dc_gain)`, `build_ideal(topo, target_subs)`,
  `build_nonideal(topo)`; optionally `analytic_seeds(topo, design)` (strictly
  additive Phase-1 starts; a failure must never break a run).
- Tier C consumes every registered module identically. A new topology family =
  new `cells_*.py` + one `REGISTRY` entry; Tier C should not need to change.
- **Cache invalidation:** the TF cache (`tf_cache_v6.json`) is per-cell and keyed
  by structure (`md5(var_list)`). A change to a nodal model that keeps
  `var_list` is invisible to that key — bump `NONIDEAL_MODEL_REV` (model-only
  change) or the cache file version (`CACHE_PATH_V2`).
- **Design-parametric templates and compiled kernels (FS-028 S2-1).** The
  solver pipeline (`run_synthesis`, the snapper, `solve_nonideal`) takes its
  cases from `tf_derivation_v2.design_cases`: each cell's ideal case is derived
  ONCE with symbolic targets (`build_ideal(topo, {})`); the design targets
  p1, w0, wz, Q, K may appear only in `res_eqs` (never in `R5_constraint`,
  `a1_expr`/`a2_expr` or the TF — a `build_ideal` must keep it so). Those
  cases carry numeric `targets`, and their `res_eqs` are evaluated only through
  `cell_kernels`; never lambdify them over `var_list` alone. The TRF path uses
  `design_sources` (the targets substituted into the raw template, then any
  Equalize substitution — the old derive-then-equalize order), which is
  source-identical to the old per-design lambdify, so results are
  bit-identical. The design-parametric `res` group (targets as arguments)
  agrees only to rounding, which re-samples the chaotic multistart; it is the
  batched solver's (S2-2, the default; validated on its own, §7), replayed on
  N rows at once by `cell_kernels.load_batched`. That replay swaps only
  `array` (a lambdified Matrix) for a broadcasting stand-in, so a `res` / `jac`
  / `r5` kernel may use arithmetic and `array` only: a new cell must pass
  `python dev/fs028/check_kernels.py --cells <name>` (batched vs scalar
  replay). The template key is the non-ideal salt plus
  `IDEAL_MODEL_REV`: bump it when a `build_ideal` changes shape without
  changing `var_list`. The kernel cache file is named after the TF cache
  (`<stem>_kernels_k<KERNEL_REV>.json`) and keyed by the template key, so any
  TF-cache bump above invalidates the kernels too; bump
  `cell_kernels.KERNEL_REV` when the kernel generator changes. `get_cases`
  (per-design, numeric `res_eqs`) stays for the other callers.

## 2. Section classification (Tier A → D)

Section family is a **math** property, derived from order + zero structure +
the pole↔zero frequency ratio, independent of filter type.
`pairing_utils.classify_section(stage, p_bricks, z_bricks, wz_tol=0.05)` is
authoritative; `pairing_utils.family_from_section(sec)` is its Section-dict
wrapper (prefers a producer-stored `sec['family']`, then `sec['n_origin_zeros']`,
then reconstruction).

Returns `{order, family, w0, Q, wz, n_origin_zeros}`. Rules:

- Order: real pole → 1; complex pair → 2; + absorbed real pole → 3.
- No zeros → `LP`.
- Origin zeros, order 2: 1 → `BP`, 2 → `HP`.
- Origin zeros, order 3: 1 → `BP1LP` (num ~ s), 2 → `BP1HP` (num ~ s²),
  3 → `HP`.
- Real pole + 1 origin zero → `HP` (order 1).
- Complex-pair zero: `wz > w0` → `LPn`; `wz < w0` → `HPn`;
  `|wz/w0 − 1| < wz_tol` → `notch`.

Producers that can count origin zeros exactly (the Pairing tab in `app.py`)
must store `n_origin_zeros` — a boolean `has_origin_zero` cannot separate
`BP` from `HP`.

**Producer rule (FS-007).** The classifier, `build_stage_bricks` and the pairers
understand only origin zeros, jω zero pairs and zeros at ∞, with exact
conjugates, and strictly-LHP poles; anything else is mishandled *silently*
(real zeros dropped, off-axis zeros treated as notches, lone roots given an
invented conjugate). A producer of `engine_results` other than the synthesized
approximations — today `custom_tf.design_custom` — must emit only that
pattern, snapped exactly (jω zeros with Re = 0, origin zeros = 0, real roots
with Im = 0, exact conjugate pairs), or refuse with a message. Its gate is
relaxed only by FS-013 (RHP zeros) / FS-014 (off-axis LHP zeros), together with
their cell and schema changes.

## 3. Section → solver dispatch gate (Tier D)

**Hard rule:** a section is solved only by the solver matching its family.
A family without a solver is gated `pending` and shown as "not yet available" —
it is **never** silently sent to another family's cells (an LP cell forced onto
an HP-notch/BP response "solves" and yields high-sensitivity garbage).

The live gate is `topology_tab.section_kind(sec)`, returning one of
`first_order | lp | hp | notch | bp | pending`. Adding a family means adding
its branch there; everything unmatched falls to `pending`.

## 4. Scoring metrics by family (Tier C)

`scoring.metrics_for(family, Hfun, comp, f_lo, f_hi)` dispatches the Bode
metric extractor by family:

- `LPn` | `HPn` | `notch` → notch extractor (`_response_metrics`)
- `LP` → `{dc_gain, f_c, passband_ripple_db, rolloff_db_dec}`
- `HP` → `{hf_gain, f_c, stopband_floor_db}`
- `BP` | `BP1LP` | `BP1HP` → `{center_gain, f0, Q, BW_-3dB}` (peak-referenced;
  the 3rd-order shapes just have asymmetric skirts)

Today the only caller is `first_order_solver` (families `LP`/`HP`); 2nd/3rd-
order scoring goes through `score_solution` → `_response_metrics`.

Unknown families raise. A new family needs its extractor here before it can be
scored.

## 5. Cascade sign

Inverting cells (MFB, some AM variants, inverting 1st-order) carry
`sign = -1`. The overall sign is the product of the section signs; an odd count
inverts the output and is flagged in the UI (`compute_stage_gains` + the
overall-gain readout). Sign is a **realization** property, set by the cell —
`classify_section` never decides it. `engine_results['k']` is positive: a
Custom H(s) entered with a negative K is used as |K|, with a warning.

## 6. Data schemas

- **engine_results, Custom H(s)** (FS-007): `{poles, zeros, k (> 0, peak = 1),
  custom_info}`. No stopband keys (`f_stop_*`, `sb_status*`, `ideal_notches_*`,
  `actual_as_db`), no `reflection_zeros`, no `delay_info`: consumers read those
  with `.get` or are gated off for Custom. `custom_info` = `{mode, form, scale,
  f_norm_hz|None, w_n (rad/s; Roots & TF and report normalization), target (the
  resolved type: the radio in prototype mode, the detected type in complete mode),
  detected, n_poles, n_zeros, n_origin_zeros, n_jw_pairs, n_real_poles,
  n_complex_pole_pairs, edges_hz (f1, f2|None), alpha_db, proto_edge_db|None,
  peak_gain (entered, V/V), peak_hz (0 = DC, inf = HF), h0_db|None,
  hinf_db|None (re peak), h_j1_db?, f3db_ratio?, gain_mode, k_sign, k_entered (typed K or
  A₀; 1 when blank under Normalize),
  conditioning_db|None, n_merged_clusters, warnings, preflight, roots_sig}`.
  Brick / Stage / Section are unchanged.
- **Brick** (pairing): `{id, type ∈ {Origin, Real, Complex Pair}, root, w0, q}`
- **Stage** (`auto_pair_stages`): `{stage_num, pole_id, w0, q,
  absorbed_real_id, capacity, zero_ids, has_zero_pair, is_3rd_order,
  wz_assigned?}`
- **Section** (`hw_sections`, consumed by `topology_tab`): `{stage_num, order,
  family, n_origin_zeros, notch, has_origin_zero, is_complex_pair, f0_hz, Q,
  fz_hz, f1_hz, K_radps, sign}`
- **Case** (`build_ideal` / `build_nonideal`): `{topo, res_eqs, var_list,
  R5_constraint, a1_expr, a2_expr, tf_num, tf_den, tf_var_list, den_degree}`
- **Solution** (snapped): `{topology, C1–C4 (µF), R1–R8 (MΩ),
  C1a/C1b/C1_parallel (opt; AM -C1s: C1 = C1a + C1b), C2a/C2b/C2_parallel
  (opt; C2 = C2a + C2b), sign (opt), sens_score, snap_cost, internal_gain,
  _dc}`. An absent part is 0.0 for C1–C4, R1–R4, R6 and None for R5, R7, R8
  (`unified_solver_v2._assemble`), so readers test `> 0`, never `is None`. AM
  rows carry R8 = R7 (the matched pair). `spice_cells.section_ir` (FS-008)
  raises on a part its netlist needs without a positive value.

## 7. Performance notes

- `cse=True` on every lambdify (residuals + responses) — non-ideal response
  ~2.2× faster; the main lever for op-amp correction.
- Analytic Jacobian in `unified_solver_v2` (Phase 1/3) and
  `zero_manifold_solver` (~1.3×). Deliberately **not** used in
  `nonideal_solver`: for frequency-domain response matching the derivative
  polynomials are about the size of the response, so finite differences are
  already near-optimal.
- Considered, not applied: loosening `nonideal_solver` `xtol/ftol` 1e-12 → 1e-9
  (the snapper quantizes anyway) — it changes converged values. FS-028 measured
  the correction tasks at ≈ 1 % of the solve, so the lever is moot.
- Compile once (FS-028 S2-1): no per-design derivation (templates, §1), no
  per-worker lambdify (Phase-1/3 workers exec the kernel sources handed over in
  the pool's initargs), and the non-ideal correction runs in-process — its
  per-section pool re-derived every cell in every worker for 0.1–3 CPU-s of
  work. Every kernel on the solve path is source-identical to what the old
  code lambdified (`dev/fs028/check_kernels.py`), so the BOMs are
  bit-identical; the only per-design symbolic work left is one subs + lambdify
  of res/jac per cell in the orchestrator (0.01-2.7 s, memoized per process).
- Keep process-pool `initargs` small (a path, names, numbers). On Windows
  spawn, `Process.start()` blocks until the child has read the pickled
  initargs through a small pipe; initargs above the buffer serialize the start
  of every worker behind the previous child's numpy/scipy/sympy import (32 × ~2 s
  of near-idle CPU). Bulk data for workers goes in a per-run file
  (`unified_solver_v2` writes the kernel packs to one). Since S2-2 this
  concerns only the `FS_SOLVER=trf` fallback and any future pool.
- Batched solve (FS-028 S2-2): a section solve runs in ONE process, no pool.
  `batched_lm.solve` takes every start of a Phase-1 / Phase-3 / ZM task list
  (and the non-ideal corrections of a topology) as one numpy batch: projected
  LM in u = log x, the TRF calls' own termination tolerances, `success` =
  stopped by a criterion (not the iteration cap). Measured on the 31-section
  benchmark: ~50–80× less serial CPU than the TRF multistart, 0.1–3 s per
  section on one core against 2–20 s on 32 workers. Rules that keep result
  quality (each measured in `dev/FS-028_solver_performance_analysis.md` §12):
  Phase 1 solves every start twice, with minimum-norm steps in x (TRF-like)
  and in log x (`P1_METRICS`), because the step metric decides which root a
  start reaches and neither sample contains the other; `harvest(...,
  merge_dup_hints=True)` keeps distinct duplicate roots of a cap vector as
  extra hints (LM roots all end near cost 1e-20, so "lowest cost per cap
  vector" no longer means anything); Phase 3 solves each hint with both step
  metrics too and keeps the lowest-sens root within `accept` (not the first
  hint's). `FS_SOLVER=trf` restores the
  S2-1 path bit for bit (scipy TRF, Phase-1/3 pool, per-row non-ideal TRF).
