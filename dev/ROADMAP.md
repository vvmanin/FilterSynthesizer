# FilterSynthesizer — Hardware Synthesis Roadmap

Single source of truth for extending the hardware-synthesis layer (1st-order,
HP, notch, BP). Lives in the repo root; each new work item gets its own chat.

---

## 0. How to use this document

- **Start every new chat** by pasting **§1–§4** (the shared primer: tier map,
  schemas, architecture decision, shared contracts) plus the **one §6 package**
  for the item you're building.
- Attach the files listed in that package — nothing more. Tier C is reused
  unchanged, so most chats do **not** need the whole repo.
- **Keep this file updated**: when an item lands, mark it done in §5 and record
  any change to a §4 contract (those are binding across chats).
- §4 contracts are the only shared abstractions later chats depend on. Item 1
  implements them; everyone else binds to them.

---

## 1. Architecture map (four tiers)

| Tier | Files | Role | Family-awareness |
|---|---|---|---|
| **A — approximation math** | `filter_engine`, `filter_solvers`, `filter_utils`, `tf_utils`, `pairing_utils`, `plot_utils` | spec → poles/zeros → cascade *stages* | already complete for LP/HP/BP/BR |
| **B — cell library** | `tf_derivation_v2` | symbolic circuit → `cases` (ideal + non-ideal TFs) | **LP-only, inlined → the extension point** |
| **C — synthesis engine + downstream** | `unified_solver_v2`, `zero_manifold_solver`, `nonideal_solver`, `discrete_snapper`, `scoring`, `filter_synthesis` | solve → continuous → op-amp-correct → E-series-snap → score | **agnostic EXCEPT `scoring` metrics (notch-specific)** |
| **D — UI / viz** | `topology_tab`, `response_tab`, `schematic_svg`, `hw_plots`, `ui_components`, `app` | route sections to cells, draw schematic/Bode/MC | routes by family |

**Key fact:** HP / BP / 1st-order are mostly **new Tier-B cell modules + Tier-D
routing/schematics**, reusing Tier C. Two Tier-C exceptions: `scoring` metric
extraction is per-family, and `unified_solver` bounds/`PARALLEL_C2_CELLS` are
LP-tuned.

---

## 2. Data schemas

**Brick** (pairing): `{id, type ∈ {Origin, Real, Complex Pair}, root, w0, q}`

**Stage** (pairing output, `auto_pair_stages`):
`{stage_num, pole_id, w0, q, absorbed_real_id, capacity, zero_ids,
has_zero_pair, is_3rd_order, wz_assigned?}`

**Section** (`hw_sections`, consumed by `topology_tab`):
`{stage_num, order, family, notch, has_origin_zero, is_complex_pair,
f0_hz, Q, fz_hz, f1_hz, K_radps, sign}`  ← `family` + `sign` are new

**Case** (`derive_ideal` / `derive_nonideal`):
`{topo, res_eqs, var_list, R5_constraint, a1_expr, a2_expr, tf_num, tf_den,
tf_var_list, den_degree}`

**Solution** (snapped):
`{topology, C1–C4 (µF, some None), C2a/C2b/C2_parallel (opt), R1–R7 (MΩ, some
None), sens_score, snap_cost, internal_gain, _dc}`

---

## 3. Architecture decision: family-modular cells behind a registry

Do **not** monolithically refactor (risks the validated LP code) and do **not**
keep piling families into `tf_derivation_v2`. Instead, create **one seam**:

- Factor `tf_derivation_v2` into a **generic symbolic engine** (nodal solver,
  `derive_ideal/nonideal` machinery, `make_response_func`, `get_cases`/cache,
  the registry) **+ per-family cell modules**: `cells_lp.py` (today's content),
  `cells_hp.py`, `cells_bp.py`, `cells_first_order.py`.
  *(Landed in item 2: `tf_symbols.py` + `cells_lp.py` + `cells_hp.py` + the
  engine/registry in `tf_derivation_v2.py`; cache is now incremental & per-cell,
  keyed by (design,name) for ideal and (name) for the design-independent
  non-ideal TF, so a single-family run derives only that family. Later added:
  `cells_notch.py` (item 3) and `cells_bp.py` (item 5 VCVS) through the same
  registry seam.)*
- Each module registers its cells through a common interface (a cell
  contributes: nodal equations + `_gates` + `var_list` + `topo_name` +
  `dc_gain_to_K` + `build_ideal` + `build_nonideal`). Tier C consumes them
  identically.

**Do the seam during item 2 (HP)** — HP is the first family that forces it; BP
then reuses the pattern. 1st-order bolts on before the seam (trivial cells).

### Cell taxonomy

- **LP (exists, 12):** `2LP-{unity,gained,atten}`, `2LPn-{unity,gained,atten}`,
  `3LP-{unity,gained}`, `3LPn-{unity,gained}`, `2LPn-gained+R7`,
  `3LPn-gained+R7`.
- **1st-order (planned, 12):** `{LP,HP} × {inv,noninv} × {atten,unity,gained}`.
- **HP (item 2, 12):** `{2,3}{HP,HPn}-{unity,gained,atten}` — the R↔C mirror of
  LP with origin zeros (DC blocked). gain is a clean 3-way axis: unity follower,
  resistive `1+R5/R7` gain, or capacitive-divider attenuation (C4); HP-notch
  uses the symmetric-R5 constraint with an order-dependent coeff index.
- **Notch (planned):** dedicated topologies — twin-T, Fliege, Bainter,
  bridged-T (NOT a single Sallen-Key form).
- **BP (planned):** MFB bandpass (inverting).

---

## 4. Shared contracts (binding — every chat depends on these)

### 4.1 `classify_section` — authoritative routing primitive (Tier A→D)

Section type comes from **order + the pole↔zero frequency ratio (wz/w0)**,
independent of filter type. This is the *authoritative* classifier:
`topology_tab` routes every section through it into the §4.4 dispatch, and the
LP solver only ever receives `LP`/`LPn`.

```python
def classify_section(stage, p_bricks, z_bricks, wz_tol=0.05) -> dict:
    """Returns:
      {'order': 1|2|3,
       'family': 'LP'|'HP'|'BP'|'LPn'|'HPn'|'notch',
       'w0': float, 'Q': float,
       'wz': float|None,        # finite zero freq (notch families)
       'n_origin_zeros': int}
    Rules (resolve stage['zero_ids'] in z_bricks):
       pole Real            -> order 1;  Complex Pair -> order 2;
       + absorbed_real      -> order 3
       no zeros                       -> LP (allpole)
       1 Origin + complex pole        -> BP
       2 Origin + complex pole        -> HP
       1 Origin + real pole           -> HP (order 1)
       Complex-Pair zero, wz > w0     -> LPn
       Complex-Pair zero, wz < w0     -> HPn
       Complex-Pair zero, |wz/w0-1|<wz_tol -> notch
    """
```

`topology_tab.section_kind` becomes a thin wrapper over this and feeds §4.4.
The wz/w0 ratio is the discriminator: >1 (zero above pole) → `LPn`; <1 (zero
below pole) → `HPn`; ≈1 → pure `notch`.

### 4.2 `scoring` family dispatch

`scoring._response_metrics` is notch-specific. Generalize via:

```python
def metrics_for(family, Hfun, comp, f_lo=1.0, f_hi=1e6) -> dict:
    """Dispatch by family:
       'LPn'|'HPn'|'notch' -> existing notch extractor
       'LP'  -> {dc_gain, f_c(-3dB), passband_ripple_db, rolloff_db_dec}
       'HP'  -> {hf_gain, f_c(-3dB), stopband_floor_db}
       'BP'  -> {center_gain, f0, Q, BW_-3dB}"""
```

`score_solution` calls `metrics_for(classify_section(...)['family'], ...)` for
both ideal and real responses; the per-family penalty combines the deltas.

### 4.3 Cascade sign

Inverting cells carry `sign = -1` (first introduced by 1st-order inverting,
reused by MFB HP/BP). Overall sign = product of section signs; an odd count
inverts the output (flag in UI). `compute_stage_gains` + the overall-gain
readout track it. **Realization choice (inv/noninv) sets sign;
`classify_section` does NOT** — sign is a realization, not a math, property.

### 4.4 Section → solver dispatch (authoritative gate)

**Fixes a live defect:** today every section — including the HP / BP / pure-notch
sections produced by BP and Band-Reject filters — is routed to the LP solver.
Some even "solve" (an LP cell forced onto an HP-notch / BP response), yielding
high-sensitivity garbage. The dispatch makes routing type-exact.

```python
SECTION_SOLVERS = {
    'LP':  lp_solver,  'LPn': lp_solver,   # EXISTS (unified_solver_v2, 12 LP cells)
    'HP':  None,       'HPn': None,        # item 2
    'notch': None,                          # item 3 (pure notch, wz ≈ w0)
    'BP':  None,                            # item 5
}

def route_section(stage, p_bricks, z_bricks):
    cls = classify_section(stage, p_bricks, z_bricks)   # §4.1
    solver = SECTION_SOLVERS.get(cls['family'])
    if solver is None:
        return ('pending', cls['family'])   # show "not yet available" — DO NOT fall back to LP
    return ('solve', solver, cls)
```

**Hard rule:** a section is solved only by its matching family solver. Families
without a solver are gated `pending` (never silently sent to LP). This ships in
item 1 and immediately stops BP/BR garbage *before* any new solver exists — the
HP/notch/BP entries just flip from `None` to their solver as items 2/3/5 land.

---

## 5. Build order & parallelization

| # | Item | Tier | Depends on | Status |
|---|---|---|---|---|
| 1 | **Foundations: dispatch gate (§4.4) + classifier + scoring seam + sign; then 1st-order LP+HP (12 cells)** | A,B,C,D | — | ☑ |
| 2 | **HP generalized VCVS (+ registry seam)** | B,C,D | — | ☑ |
| 3 | **Notch family (twin-T/Fliege/Bainter/...)** | B,D | classify_section (item 1) | ☐ |
| 4 | **BP pairing refinement** | A | — (parallel) | ☐ |
| 5 | **Generalized BP section solver (VCVS first, MFB/DABP later)** | B,C,D | items 1, 2, 4 | ◐ |

- ◐ Item 5: the **VCVS Sallen-Key band-pass landed** (both `2BP`/`2BP-atten`
  cells, full Tier-C/D + UI/viz); the inverting **MFB** and **DABP** band-pass
  topologies remain.

- **Item 1 ships the §4.4 dispatch gate first** — correctness-critical, stops the
  current BP/BR mis-solving before any new solver is written.
- Item 1 also ships the three §4 contracts → unblocks the rest.
- Item 4 is pure Tier A, **fully parallel** (shares no files with B/C/D).
- 1st-order HP is folded into item 1 (unified 12-cell LP+HP set).

**Item 2 landed (registry seam + HP family).** Tier B was split into a generic
engine + per-family cell modules behind a registry; HP cells + HP-aware Tier-C/D
followed (details in §6 Item 2). Validated: all 12 HP cells pass the ideal-limit
+ order/zero self-test (~1e-10); all 12 LP cells are byte-identical to the
pre-seam derivation (symbolic equality on tf_num/tf_den/R5_constraint/a1/a2/
residuals/nonideal); HP synthesizes end-to-end in IDEAL mode with correct
passband (HF) gains, capacitive-divider attenuators, and deep on-axis notches.

**Item 5 VCVS band-pass landed (MFB/DABP still to come).** Built the VCVS
Sallen-Key band-pass ahead of MFB, reusing the item-2 registry seam: `cells_bp.py`
adds the `2BP` (non-inverting gain block K=1+R5/R4) and `2BP-atten` (unity-buffer,
K=1) cells. Numerator is a single s term — one origin zero, one at infinity — so,
unlike the notch, there is **no derived-R5 constraint** (the s⁰ numerator
coefficient is structurally zero). Ki = K/(R1·C1) is the rad/s leading
coefficient from pairing; center gain |H(jω₀)| = Ki·Q/ω₀. The atten cell carries
a hard ceiling Q·Ki ≤ ω₀ (center gain ≤ 1, since ω₀/Q = g3/c2+g3/c1+Ki ≥ Ki),
so it is the minimal-component alternative for sub-unity sections and the BOM
ranks it beside `2BP`. Tier-C/D + UI/viz are BP-aware throughout
(`unified_solver_v2` anchor/rescale/assemble, `discrete_snapper` peak-referenced
gain + −3 dB-skirt Q metric, `section_router` BP→`bp`, `topology_tab` per-section
Ki override, `response_tab`/`hw_plots` peak-normalized overlay, `app.py`
`n_origin_zeros` so the classifier separates BP's 1 origin zero from HP's 2,
`schematic_svg` `2BP`/`2BP-atten` artwork with auto-suppressed R4/R5 on the atten
cell — anchor coordinates are PLACEHOLDERS pending the real Diagram-Builder
canvas). Validated: both cells pass the order/zero + ideal-limit self-test
(~1e-11); end-to-end synthesis hits target f₀ and center gain precisely with
sign +1.

---

## 6. Per-chat context packages (deltas from the §1–§4 primer)

### Item 1 — 1st-order + foundations
**Files:** `tf_derivation_v2.py` (cell pattern + `derive_nonideal` to mirror),
`pairing_utils.py` (full — write `classify_section`), `scoring.py` (full — add
`metrics_for` dispatch), `topology_tab.py` (`section_kind`→classifier, the
`first_order` route, sign display), `hw_plots.py` + `response_tab.py` (1st-order
`Hni` into Bode/MC), `schematic_svg.py` (4 templates), `discrete_snapper.py`
(1-resistor handling).
**Delta:** *First, ship the §4.4 dispatch gate* (`route_section` + `SECTION_SOLVERS`
in `topology_tab`, or a tiny `section_router.py`) — correctness-critical, gates
non-LP families to `pending` and immediately stops BP/BR mis-solving on LP cells.
Then deliver the other §4 contracts (`classify_section`, scoring `metrics_for`,
sign) as reusable infrastructure, not 1st-order special cases. Finally the
1st-order cells: a **hybrid** — cells live in Tier B for their `Hid`/`Hni`
(non-ideal op-amp reality, for Bode/MC) but **bypass `unified_solver_v2`**;
component values are **closed-form** (one pole, one gain → R,C). 12 cells =
`{LP,HP}×{inv,noninv}×{atten,unity,gained}`; sub-unity entered manually in the
tab like today's `atten`.

### Item 2 — HP VCVS + registry seam
**Files:** full `tf_derivation_v2.py`, `unified_solver_v2.py`,
`zero_manifold_solver.py`, `discrete_snapper.py`, `filter_synthesis.py`,
`scoring.py`, `topology_tab.py`.
**Delta:** HP Sallen-Key (R↔C swap, origin zeros). Establish the registry seam
(`cells_lp`/`cells_hp` + engine interface) that BP reuses. Closes the wz < w0
(HP-notch) case.

### Item 3 — Notch family
**Files:** `tf_derivation_v2.py` (notch topologies — may need a twin-T/Fliege
template, NOT the Sallen-Key nodal form), `unified_solver_v2.py` +
`zero_manifold_solver.py` (solve path + wz = w0 degeneracy), `scoring.py`
(notch metrics already exist), `pairing_utils.py` (`auto_pair_bandreject`
exists — route its stages), `topology_tab.py`.
**Delta:** topology survey (twin-T deep-but-tuning-sensitive; Fliege independent
Q; Bainter non-inverting depth-insensitive; bridged-T) and the
symmetric-vs-asymmetric selection criteria. `LPn`/`HPn` at wz ≈ w0 is the
*fallback floor*, not the plan.

### Item 4 — BP pairing
**Files:** full `pairing_utils.py` (`auto_pair_bandpass` — Q-descending zoned
proximity), `filter_engine.py` + `filter_solvers.py` (LP→BP mirror-pair
generation), `plot_utils.py` + `app.py`.
**Delta:** bring 2–3 concrete suboptimal BP cases; target rule = pole-zero
proximity pairing + ascending-Q ordering for minimum dynamic range/sensitivity.
**No hardware files.**

### Item 5 — BP solver
**Files:** the item-2 Tier-B/C set + `cells_bp.py` (once the seam exists) +
refined `pairing_utils.py` from item 4.
**Delta:** *VCVS Sallen-Key band-pass — DONE* (`cells_bp.py`: `2BP`/`2BP-atten`;
non-inverting, single-s numerator, no R5 constraint; full Tier-C/D + UI/viz; see
the "Item 5 VCVS band-pass landed" note in §5). *Remaining:* the inverting **MFB**
band-pass (uses item 1's sign infra — MFB is inverting, so set `out["sign"] = -1`)
and the **dual-amp DABP** (independent Q tuning); both slot into the same registry
pattern. When the real `2BP.drawio.svg` / `2BP-atten.drawio.svg` canvases are
drawn, replace the PLACEHOLDER `ANCHORS_BP` coordinates in `schematic_svg.py`.

---

## 7. Current file inventory

```
app.py                 orchestrator, tabs
ui_components.py        sidebar (gain block, modifications)
filter_engine.py        synthesize_{lowpass,highpass,bandpass,bandreject}
filter_solvers.py       approximation math
filter_utils.py         math helpers
tf_utils.py             TF helpers
pairing_utils.py        roots→bricks→stages; auto_pair_{bandpass,bandreject,generic}; compute_stage_gains
plot_utils.py           Plotly math plots
tf_derivation_v2.py     [Tier B] 12 LP cells; derive_ideal/nonideal; make_response_func; get_cases/cache
unified_solver_v2.py    [Tier C] ideal continuous synth (Phase 1 multistart + Phase 3 cap-combo/zero-manifold)
zero_manifold_solver.py [Tier C] parallel-C2 path for 2nd-order notch cells
nonideal_solver.py      [Tier C] frequency-domain op-amp correction
discrete_snapper.py     [Tier C] resistor → E-series snap
scoring.py              [Tier C] sens + non-ideality metrics (NOTCH-SPECIFIC); OPAMP_LIBRARY
filter_synthesis.py     [Tier C] top-level synthesize() orchestrator (stage1/2/3 + top_k)
topology_tab.py         [Tier D] per-section hardware-synthesis tab
response_tab.py         [Tier D] Bode / Monte-Carlo tab
schematic_svg.py        [Tier D] per-cell schematic SVG
hw_plots.py             [Tier D] Bode / MC plot builders
```

## 8. Performance notes (applied)

- `cse=True` on all lambdifies (residuals + responses). Non-ideal response
  ~2.2× faster — primary lever for the op-amp correction stage.
- Analytic Jacobian in `unified_solver` (Phase 1/3) + `zero_manifold` — ~1.3×
  (diluted by non-converging candidates hitting `max_nfev`). NOT used in
  `nonideal_solver`: for frequency-domain response matching the derivative
  polynomials are ≈ response size, so finite-difference is already near-optimal.
- Open lever (not applied — changes converged values): loosen `nonideal_solver`
  `xtol/ftol` 1e-12 → 1e-9 (snapper quantizes to E-series anyway).
