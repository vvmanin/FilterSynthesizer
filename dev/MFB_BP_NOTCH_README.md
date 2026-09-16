# MFB Band-Pass & Notch cells — integration summary

Adds Multiple-Feedback (Rauch/Friend) realizations for **band-pass** and
**pure-notch** cascade sections to FilterSynthesizer, behind the existing cell-module
registry seam. All cells are verified (symbolic derivation, `self_test`, and
end-to-end `synthesize()`), wired through the solver, snapper, and Streamlit UI,
and carry schematic-designator maps.

## Cells added

| Cell           | Family      | Order | Sign            | Passband gain                     |
|----------------|-------------|-------|-----------------|-----------------------------------|
| `2BP-MFB`      | `BP-MFB`    | 2     | −1 (inverting)  | center peak, free ratio           |
| `2BP-MFB-QE`   | `BP-MFB`    | 2     | −1 (inverting)  | center peak, +FB Q-boost          |
| `2N-MFB`       | `NOTCH-MFB` | 2     | +1 (non-invert) | `R4/(R1+R4)`·boosts — **unity / gained / sub-unity** |
| `2N-MFB-atten` | `NOTCH-MFB` | 2     | +1 (non-invert) | `R4/(R1+R4)` < 1 (attenuating-only)|

The two band-pass cells are solved **in parallel** and ranked by `sens_score`
(plain cell compact at low Q; QE positive-feedback cell reaches higher Q with a
gentler spread). The two notch cells are offered by gain (see below).

## The notch pair — unity / gained / attenuating

`2N-MFB-atten` is the original minimal notch (C1,C2,R1,R2,R3,R4): its passband
gain `H(0)=H(∞)=R4/(R1+R4)` is a positive divider **strictly < 1**, so it is
attenuating-only, and `wz=w0` is structural.

`2N-MFB` (C1,C2,C3,R1,R2,R3,R4,R5) adds, at the op-amp **−** node, a resistor
**R5 (m→gnd)** and a capacitor **C3 (m→gnd)**:

- `H(0)  = R4·(R2+R3+R5) / [R5·(R1+R4)]`  — R5 lifts the DC gain
- `H(∞)  = R4·(C2+C3)    / [C2·(R1+R4)]`  — C3 lifts the HF gain

Each is the atten divider `R4/(R1+R4)` times a boost > 1, so the passband can be
set to **unity, any gain > 1, or sub-unity**. The op-amp **+** input stays on the
**R1/R4 input divider** (not tied to the input) — that divider injects the
negative term the numerator's s¹ coefficient needs for the on-axis (jw) zero;
tying **+** to the input instead destroys the null. Here `wz=w0` is *not*
structural, so the symmetric notch is pinned by driving **both** `H(0)` and
`H(∞)` to `K` (which, with the on-axis-zero residual, forces the numerator to
`K·(s²+w0²)`). Verified: g = 1, 2, 4 realize deep on-frequency nulls with tight
spreads (R-ratio 6–11 at unity, 6–7 at 2×).

**Offering rule** (topology_tab): a **sub-unity** target offers **both** cells,
ranked together by `sens_score` (`2N-MFB-atten` is the fewer-part cell; `2N-MFB`
also covers it). A **unity or gained** target offers **only `2N-MFB`**
(`2N-MFB-atten` cannot reach ≥ 1).

## Files

**New**
- `cells_mfb_bp.py` — `BP-MFB` family (both band-pass cells).
- `cells_mfb_notch.py` — `NOTCH-MFB` family (`2N-MFB` gained/unity + `2N-MFB-atten`).

**Modified**
- `tf_derivation_v2.py` — registered both families; extended the `self_test`
  single-origin-zero branch to cover `BP-MFB` (`{1}`). (Both notch cells are
  order-2 notches → `{2}` via the existing branch.) **Cache-invalidation fix:**
  the design-independent non-ideal TF was cached by cell *name* only, so a cell
  whose *topology* changed under the same name (e.g. `2N-MFB` gaining C3/R5)
  would be served a stale non-ideal TF — the realized/BOM and Monte-Carlo curves
  then computed the response *without* the new components (no notch, wrong
  passband) even though the ideal/design curve was correct. The non-ideal cache
  key now carries a per-cell structural signature (`_ni_key` = name + hash of the
  cell's `var_list`), and the cache version was bumped `v3`→`v4`, so a stale
  non-ideal can no longer be served.
- `unified_solver_v2.py` — `anchored_bounds` (anchor `C1`); `rescale_isolated_r5r6`
  (BP-MFB-QE `{R4,R5}` +divider, NOTCH-MFB `{R1,R4}` input-divider common-scale
  rescales — both notch cells share the unloaded `{R1,R4}` DOF); `_assemble`
  (`internal_gain` + `out["sign"]` for both families).
- `discrete_snapper.py` — added `BP-MFB` to the three band-pass special-cases
  (`get_T_target`, `w_ref`, `f_q`). Without it a band-pass was normalized at DC
  (where |H|≈0), inflating `snap_cost` by ~5 orders of magnitude (347447 → 0.1).
- `topology_tab.py` — MFB offered for band-pass and pure-notch sections;
  `mfb_ok` opened to all kinds; the notch offering is gain-dependent (both cells
  below unity, `2N-MFB` only at/above); captions/info updated. `_gain_label_for`
  already maps `2BP*`→"Center gain", `2N*`→"DC/HF gain".
- `schematic_svg.py` — placeholder designator maps `ANCHORS_MFB_BP`
  (`2BP-MFB`/`2BP-MFB-QE`, one map; R4/R5 auto-suppress on the plain cell) and
  `ANCHORS_MFB_N` (`2N-MFB`/`2N-MFB-atten`, one map; C3/R5 auto-suppress on
  `-atten`), wired into `_anchor_table` and `svg_filename`. Coordinates are
  placeholders mirroring the MFB layouts — swap in the real Diagram-Builder
  anchors when the `.drawio.svg` canvases are finalized.

Nothing else changed: `solvability_probe`, `scoring.metrics_for`,
`pairing_utils.family_from_section`, and `hw_plots` all key off the **section**
classification (family-agnostic), so they pick up the new cells with no edits.

## Key derived facts (verified against the symbolic TF to ~1e-16)

- **2BP-MFB**: `num = −C1·R2·R3·s` (single s-term). `Ki = −1/(R1·C2) < 0` →
  inverting. `w0² = (1/R1+1/R2)/(C1 C2 R3)`, `w0/Q = (C1+C2)/(C1 C2 R3)`.
- **2BP-MFB-QE**: adds the `R4`/`R5` positive-FB divider; `Ki = −(R4+R5)/(C2 R1 R5) < 0`
  (inverting always). +FB subtracts from the damping → Q boost. `{R4,R5}` common
  scale is a free DOF.
- **2N-MFB-atten**: `H(0)=H(∞)=R4/(R1+R4)` (< 1, non-inverting). `wz=w0`
  structural → no notch-freq / symmetry residual. `{R1,R4}` common scale free.
- **2N-MFB**: `H(0)=R4(R2+R3+R5)/[R5(R1+R4)]`, `H(∞)=R4(C2+C3)/[C2(R1+R4)]`;
  reaches unity/gained/sub-unity. `wz≠w0` structurally, so both `H(0)=K` and
  `H(∞)=K` are enforced. `{R1,R4}` common scale free.

Gain conventions: band-pass hands `Ki` down via `cfg["K"]` (dc_gain=None),
inverting sign in the residual (`SIGN·K`) and via `out["sign"]`; the notch takes
a linear `dc_gain` (the divider/boost magnitude) directly.

## Validation performed

- **`self_test` (all 49 cells)**: PASS, zero regressions. New cells show correct
  order (2), zero-count (BP = 1 origin zero, notch = 2 on-axis), and ideal-limit
  (non-ideal → ideal) agreement ~1e-11.
- **End-to-end `synthesize()`**:
  - Band-pass (f0 = 1 kHz): both cells produce non-empty BOMs at feasible Q,
    realized peak |H| ≈ 1.00 on-frequency, sign −1, `snap_cost` O(0.1). High-Q /
    tight-ratio targets degrade to empty.
  - Notch (f0 = fz = 1414 Hz, Q = 1.5): **unity** (g=1.000, R-ratio 6–11) and
    **gained** (g≈2.0, R-ratio 6–7) via `2N-MFB` only; **sub-unity** (g=0.85)
    with both cells pooled and ranked by `sens_score`. Deep symmetric nulls
    on-frequency, sign +1. Infeasible targets degrade to empty.

## Feasibility notes (cells are niche by construction)

- **Band-pass**: single-op-amp MFB band-pass Q is bounded by component spread;
  the plain cell is compact only at low Q, the QE cell extends the reach but
  still needs a wide `MAX_R_RATIO` at high Q. Beyond the budget → empty BOM.
- **Notch**: `2N-MFB-atten` is attenuating-only and Q-coupled
  (`g > Q²/(1+Q²)`), with a practical window (finite spread) narrower than the
  theoretical band. `2N-MFB` reaches unity/gained cleanly; discrete null depth is
  bounded by the E-series cap grid (finer caps or the parallel-C split deepen it).
