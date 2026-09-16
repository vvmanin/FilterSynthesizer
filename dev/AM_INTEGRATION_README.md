# Ackerberg–Mossberg (AM) three-op-amp biquad family — integration

> **Follow-up:** five post-integration fixes (R8 in BOM/MC, the `-C1s`
> parallel-C1 gain split, the corrected equal-swing derivation, the
> "Equalize R, C values" toggle, and `3LP-AM2`) took the family from 12 to
> **20 cells**. See `AM_FOLLOWUP_FIXES.md` for those; this file documents the
> original 12-cell integration.

Adds the **AM (Ackerberg–Mossberg) state-variable** topology family alongside
the VCVS (Sallen-Key) and MFB (Friend) families, selectable per cascade section.
AM is the classic three-op-amp resonator: a Miller integrator (U1) closed through
an **actively-compensated** integrator (U2) and a unity inverter (U3). Its
signature is the matched inverter pair `R7 = R8`, which gives active GB
compensation — Q error `∝ (f0/ft)²` instead of the single-amp `Q·f0/ft` — the
reason to spend three op-amps. All 12 base cells are verified (closed-form TF,
`self_test`, and end-to-end `synthesize()`), wired through the solver, snapper,
non-ideal pre-distortion, and Streamlit UI, and carry schematic-designator maps.

## Cells added (12)

| cell | order | kind | tap | sign | passband gain |
|------|-------|------|-----|------|---------------|
| `2LP-AM`   | 2 | LP     | out1 | −1 | `−R6/R2` (DC) |
| `2LP-AM2`  | 2 | LP     | out2 | −1 | `−R5/R1` (DC) — classic 1974 AM |
| `3LP-AM`   | 3 | LP     | out1 | −1 | `−R6/R2` (DC) |
| `2LPn-AM`  | 2 | LP-notch | out1 | −1 | `−C1/C2` (DC·ωz²/ω0²) |
| `3LPn-AM`  | 3 | LP-notch | out1 | −1 | `−C1/C2` |
| `2HP-AM`   | 2 | HP     | out1 | −1 | `−C1/C2` (HF) |
| `3HP-AM`   | 3 | HP     | out1 | −1 | `−C1/C2·C4/(C4+C1)` (HF) |
| `2HPn-AM`  | 2 | HP-notch | out1 | −1 | `−C1/C2` (HF) |
| `3HPn-AM`  | 3 | HP-notch | out1 | −1 | `−C1/C2` (HF) |
| `2BP-AM`   | 2 | BP     | out1 | −1 | `R4/R1` (peak) |
| `2BP-AM2`  | 2 | BP     | out2 | −1 | `C1·R4/(C3·R6)` (peak) — **implemented, not offered** |
| `2N-AM`    | 2 | notch  | out1 | −1 | `−C1/C2` (DC=HF) |

Every AM cell is **inverting** (`SIGN = −1`); the cascade tracks the per-section
sign exactly as for the MFB all-pole cells.

### Two pooled pairs (solved together, ranked by `sens_score`)

- **`2LP-AM` + `2LP-AM2`** — the two low-pass taps of the same resonator, ideal-
  exactly equivalent (same `D(s)`, same part count) and GB-equivalent (mirror-
  symmetric pole errors, identical DC error and stopband floor — see
  `AM_NONIDEAL_ANALYSIS.md §3`). Both are offered for a 2nd-order LP section and
  ranked together, the same pooled mechanism as `2BP-MFB`/`2BP-MFB-QE`.
- **`2BP-AM` (+ `2BP-AM2`)** — the two band-pass taps are **not** GB-equivalent.
  `2BP-AM2` has ~10× larger residual Q error, an 8–10 dB worse HF stopband floor
  (C1 feed-through as U1's virtual ground degrades), and a unity-peak input cap
  `C1 = C3/Q` that shrinks below `C_min` at high Q (`AM_NONIDEAL_ANALYSIS.md §4`).
  `2BP-AM` dominates on every metric, so **`2BP-AM2` is registered, verified, and
  callable by name but deliberately NOT offered** in the topology picker — pooling
  it would only ever surface `2BP-AM` anyway. AM band-pass offers only `2BP-AM`.

## Two design decisions worth re-reading

1. **The matched inverter pair `R7 = R8` is ONE symbol (`R7`).** R7 (out3→m3) and
   R8 (out2→m3) enter every pole/zero quantity only through the ratio `R8/R7`, and
   `R7 = R8` is the condition for the AM active GB compensation — so both branches
   carry the single symbol `R7`. Consequences:
   - The **ideal TF is R7-free** (the ratio cancels exactly): R7's Jacobian column
     is zero, so the solver leaves it at its start.
   - `unified_solver_v2.rescale_isolated_r5r6` **pins R7 = √(R5·R6)** post-solve
     (a free DOF — the ideal response and `sens_score` are invariant), clipped into
     `[R_min, R_max]`, so the non-ideal correction and the BOM carry a sane value.
   - The **non-ideal TF keeps R7** (loop dynamics and loading depend on its
     absolute value), which is why R7 stays in `var_list` and the BOM.
   - The BOM's single R7 value **designates the matched pair** — two physical
     resistors of that E-series value, ideally one array/network for tracking.
     *Monte-Carlo note:* modelling the pair as one tracking value is ~15%
     optimistic on the ω0 σ vs two independent resistors; model them as tracking
     if your MC supports it.

2. **The on-axis zero of every notch cell is STRUCTURAL.** With only `{C1, R2}`
   populated the `s¹` numerator term is *identically* zero for any element values
   (the R1/R3 branches that would create it do not exist), so the transmission
   zeros sit exactly on the jω axis — tolerances shift `ωz` but can never fill the
   null. `cells_am_core.build_ideal_common` **asserts** this structural zero at
   derivation time (raw numerator coefficients at all indices other than 0 and 2
   are identically 0) and carries only a single zero-*frequency* residual, rather
   than an on-axis-null residual that could be mis-solved. This also means `2N-AM`
   pins its whole passband with one gain residual (`H(∞) → SIGN·K`); once `ωz = ω0`
   is met, `H(0) = H(∞)` follows automatically — unlike `2N-MFB`, which must drive
   both ends.

## Gain conventions (per family)

- **`LP-AM`** — `H(0) = K·b0/a0`, `a0 = ω0²` (2nd) or `p1·ω0²` (3rd), `b0 = ωz²`
  (notch) or 1: `dc_gain_to_K = SIGN·|g|·a0/b0`; residual drives `b_lead/a_lead → K`
  (K carries the sign). Realized gain read off `H(0)` (`h0_f`).
- **`HP-AM`** — HF plateau `H(∞) = b_lead/a_lead = K` directly (numerator degree =
  filter order): `dc_gain_to_K = SIGN·|g|`, no `(rad/s)ⁿ` rescale. Realized gain
  read off the leading-coeff ratio (`hinf_f`).
- **`BP-AM`** — pairing stage hands down `Ki = b_lead/a_lead` via `cfg["K"]`
  (`dc_gain = None`); K carries the positive magnitude, residual targets `SIGN·K`,
  sign reported via `out["sign"] = −1`. Center-peak gain read per-solution by
  `topology_tab._realized_dc`.
- **`NOTCH-AM`** — dimensionless plateau `|H(0)| = |H(∞)| = C1/C2`; magnitude
  passthrough, residual targets `SIGN·K`. (Note: this is **inverting**, unlike the
  non-inverting MFB notch pair.)

The `HP-AM` and `NOTCH-AM` passband gain is a purely **capacitive** ratio `C1/C2`
(× `C4/(C4+C1)` at 3rd order) that the resistor snap cannot correct, so
`discrete_snapper` carries the same per-solution capacitive-gain penalty for those
cells as it does for the HP-MFB all-pole gain. `LP-AM` and `BP-AM` carry resistor
gain knobs and snap tight in the loop.

**Sensitivity-tie note.** As with the MFB 2nd-order cells, `a2_expr = 1` for every
2nd-order AM cell, so `sens_score` counts only the `s¹` coefficient and comes out
**~1.40 flat** (a theory-flat tie). Ranking *within* a cell then devolves to
`snap_cost` — this matches the MFB convention and is intentional (do not "fix" it).
3rd-order cells have a non-trivial `a2_expr` and score normally (~1.88 seen).

## Files

**New**
- `cells_am_core.py` — the shared 3-op-amp AM core: node symbols, the `am_eqs`
  KCL builder (ideal + non-ideal from one netlist), the conductance-cleared Cramer
  TF solver (`_solve_tf_G`, same trick as `cells_mfb`), and
  `build_ideal_common` / `build_nonideal_common`. All four family modules delegate
  here so the netlist lives in exactly one place.
- `cells_am.py` — `LP-AM` family (`2LP-AM`, `2LP-AM2`, `3LP-AM`, `2LPn-AM`,
  `3LPn-AM`).
- `cells_am_hp.py` — `HP-AM` family (`2HP-AM`, `3HP-AM`, `2HPn-AM`, `3HPn-AM`).
- `cells_am_bp.py` — `BP-AM` family (`2BP-AM`, `2BP-AM2`).
- `cells_am_notch.py` — `NOTCH-AM` family (`2N-AM`).

**Modified**
- `tf_derivation_v2.py` — imported and registered all four AM families in
  `REGISTRY` (4 imports + 4 entries); extended the `self_test` zero-count branches
  to include `HP-AM` (origin zeros → `{order}`, 3rd-order notch → `{2,3}`) and
  `BP-AM` (single origin zero → `{1}`).
- `unified_solver_v2.py` — `anchored_bounds` anchors **C2** for every AM cell
  (the core Miller cap, present in all 12; the generic order-3 C1 anchor would
  mis-pin because 3rd-order AM uses C4 for the input pole and C1 = H·C2 on the
  feed-forward cells); `rescale_isolated_r5r6` pins the matched pair
  `R7 = √(R5·R6)`; `_assemble` adds the four AM `internal_gain` branches (LP→h0,
  HP/NOTCH→hinf, BP→neutral) and the `out["sign"] = −1` branch.
- `discrete_snapper.py` — added `HP-AM` / `BP-AM` to the HP-plateau and
  band-pass-peak special cases in `get_T_target` and the snap loop (reference
  point, Q-shape point, target numerator shape); added the capacitive-gain penalty
  for `HP-AM` / `NOTCH-AM` (constant across resistor combos, ranks cap combos by
  `C1/C2` error).
- `nonideal_solver.py` — `_band_of` routes `HP-AM`→HP/HPn, `BP-AM`→BP,
  `NOTCH-AM`→notch. **This edit also closes a pre-existing gap:** `BP-MFB` and
  `NOTCH-MFB` were not in `_band_of`'s tuples, so their pre-distortion fell through
  to the default LP passband weighting; they are now routed correctly alongside the
  AM families.
- `topology_tab.py` — wired the **AM (Ackerberg–Mossberg)** radio: family list,
  info text, the `am` section flag, cell selection for BP (`2BP-AM` only), pure
  notch (`2N-AM`), and LP/LPn/HP/HPn (with the `2LP-AM`/`2LP-AM2` pooled pair on
  the 2nd-order LP section); `_realized_dc` HP/BP tuples; the `am_ok` solvable gate
  (AM gain is a free ratio → never atten-blocked); captions. CRLF endings
  preserved.
- `schematic_svg.py` — `_AM_CELL_RE` for the 12 AM names → `"{cell}.drawio.svg"`,
  and a placeholder `ANCHORS_AM` designator map (three op-amps → U1/U2/U3), wired
  into `_anchor_table` and `svg_filename`. Coordinates are placeholders mirroring
  the MFB layouts — swap in the real Diagram-Builder anchors when the AM
  `.drawio.svg` canvases are finalized; a missing canvas degrades gracefully to a
  "Schematic SVG not found" caption.

Nothing else changed: `solvability_probe`, `scoring.metrics_for`,
`pairing_utils.family_from_section`, and `hw_plots` all key off the **section**
classification (family-agnostic), so they pick up the AM cells with no edits.

## Validation performed

- **Closed-form TF equality** (`test_am_derive.py`) — all 12 cells' ideal TFs
  equal the hand-derived CAS forms from `AM_IDEAL_TF_ANALYSIS.md` to machine
  precision (common `D(s) = s² + s/(R4C2) + (R8/R7)/(R5R6C2C3)`; each tap's
  numerator; 3rd-order = exact first-order prefilter × biquad).
- **`self_test`** — all 12 AM cells PASS: correct order, correct zero count (LP
  `{0}`, notch `{2}`, HP `{order}`, BP `{1}`, 3rd-order HP-notch `{2,3}`), and
  ideal-limit (non-ideal → ideal) agreement `1e-8 … 1e-11`. Existing families
  (`LP`, `LP-MFB`, `BP-MFB`, `NOTCH-MFB`) still PASS — zero regressions.
- **End-to-end `synthesize()`** (`test_am_e2e.py`, ideal mode): non-empty BOMs,
  inverting sign, realized gain within E-series snap tolerance, R7 pinned to
  `√(R5·R6)` in range, sane `snap_cost` on every cell kind. The pooled pairs
  return **both** cell names in one ranked list:
  - `2LP-AM` + `2LP-AM2`: 6 BOMs, both cells, |H| = 1.98 (want 2.0), R7 = 301 kΩ.
  - `2BP-AM` + `2BP-AM2` *(direct-name validation of the skipped cell)*: 6 BOMs,
    both cells, |H| = 1.00 (want 1.0), R7 = 332 kΩ.
  - `2HP-AM`, `2LPn-AM`, `2N-AM`, `3LP-AM`: all PASS, gains within 0.1–2.6%.
  - `3HPn-AM` (the heaviest cell): verified through the solver internals —
    ratio-mode Phase-1 converges to cost `1e-31`, realized HF gain 1.000, notch
    −335 dB (structural), R7 pins correctly. Its full process-pool e2e is
    single-core `lambdify` wall-time-bound, not correctness-bound.

## Remaining / optional

- **Streamlit UI** could not be exercised headlessly (streamlit not installed in
  the sandbox); the `topology_tab.py` edits are logic-only and `py_compile`-clean
  with CRLF preserved, but click-through the AM radio on an LP (confirm both taps
  appear), an HP, a notch, and a band-pass section.
- **Schematics** — `schematic_svg.py` has placeholder AM anchors; draw the 12 AM
  `.drawio.svg` canvases and drop in the real Diagram-Builder coordinates
  (display-only; does not affect synthesis).
- **`R7 = R8` Monte-Carlo** — model the matched pair as tracking (§ decision 1);
  the single-symbol BOM value is the shared nominal.
- **Q-trim** (`AM_NONIDEAL_ANALYSIS.md §2`) — the `R8/R7 = 1 + δ` split is a usable
  final Q knob for very tight GB; not wired into the auto-solve (the default
  matched pair + pre-distortion already meets targets), available if you want a
  board-level trim exposed.
- **Swing-balance tiebreaker** — the 2nd-order AM `sens_score` is theory-flat
  (~1.40); if you want the balanced-swing solution surfaced when sensitivities
  tie, an optional `|ω0·R5·C2 − 1|`-style tiebreaker could be added to the sort
  (touches global ranking, so left out for now — same posture as the MFB
  max-resistance tiebreaker note).
