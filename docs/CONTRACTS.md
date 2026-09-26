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

## 3. Section → solver dispatch gate (Tier D)

**Hard rule:** a section is solved only by the solver matching its family.
A family without a solver is gated `pending` and shown as "not yet available" —
it is **never** silently sent to another family's cells (an LP cell forced onto
an HP-notch/BP response "solves" and yields high-sensitivity garbage).

The live gate is `topology_tab.section_kind(sec)`, returning one of
`first_order | lp | hp | notch | bp | pending`. Adding a family means adding
its branch there; everything unmatched falls to `pending`.

> `section_router.py` holds an older table form of this gate
> (`SECTION_SOLVERS` / `route_section`). It is not imported anywhere and its
> entries are stale; do not treat it as authoritative.

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
`classify_section` never decides it.

## 6. Data schemas

- **Brick** (pairing): `{id, type ∈ {Origin, Real, Complex Pair}, root, w0, q}`
- **Stage** (`auto_pair_stages`): `{stage_num, pole_id, w0, q,
  absorbed_real_id, capacity, zero_ids, has_zero_pair, is_3rd_order,
  wz_assigned?}`
- **Section** (`hw_sections`, consumed by `topology_tab`): `{stage_num, order,
  family, n_origin_zeros, notch, has_origin_zero, is_complex_pair, f0_hz, Q,
  fz_hz, f1_hz, K_radps, sign}`
- **Case** (`build_ideal` / `build_nonideal`): `{topo, res_eqs, var_list,
  R5_constraint, a1_expr, a2_expr, tf_num, tf_den, tf_var_list, den_degree}`
- **Solution** (snapped): `{topology, C1–C4 (µF, some None),
  C2a/C2b/C2_parallel (opt), R1–R7 (MΩ, some None), sens_score, snap_cost,
  internal_gain, _dc}`

## 7. Performance notes

- `cse=True` on every lambdify (residuals + responses) — non-ideal response
  ~2.2× faster; the main lever for op-amp correction.
- Analytic Jacobian in `unified_solver_v2` (Phase 1/3) and
  `zero_manifold_solver` (~1.3×). Deliberately **not** used in
  `nonideal_solver`: for frequency-domain response matching the derivative
  polynomials are about the size of the response, so finite differences are
  already near-optimal.
- Considered, not applied: loosening `nonideal_solver` `xtol/ftol` 1e-12 → 1e-9
  (the snapper quantizes anyway) — it changes converged values.
