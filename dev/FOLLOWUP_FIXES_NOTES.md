# Follow-up fixes

Ten files changed (the nine from stage 2, plus `response_tab.py`).
Cross-tree solver regression: **0 diffs**. Full self-test: **pass**.

---

## 1. Unsupported-shape gate (VCVS / AM)

A `BP1LP` / `BP1HP` section selected with **VCVS** or **AM** now disables the
Solve button and shows a warning. The radio stays fully enabled — nothing is
removed, so the realizations can drop in later.

Why it mattered: those families' band-pass cells are 2nd order, so their
denominator is quadratic and cannot place the third pole *at all*. The solver
would have matched f0/Q/Ki, dropped p1 silently, and reported success — the same
failure mode as the `classify_section` mis-route, just one layer further down.

Implemented as a general `unsupported` flag (init `None` at the top of
`_render_section`, set by whichever branch knows it has no cell). It outranks
the existing `atten_blocked` check and reuses the established
`solvable` → disabled-button → caption pattern. The message names the section's
real pole, the cells that *would* work, and the re-pairing alternative.

## 2. `C0` / `R0` designator columns

`C3` → **`C0`** and `R6` → **`R0`** for `2BP1HP-MFB` / `2BP1LP-MFB` and their
`-QE` twins, in the BOM table, the per-part "Capacitors / Resistors" lists, and
the Sort-by menu. Caps/resistors are sorted by *display* name, so `C0` and `R0`
lead their blocks — matching the schematic to come.

Unlike the AM remap this is a pure rename (no splitting, no merging), so it
needed no separate table builder — just `_bp3_alias()` plus `_display_pairs()`.
With an empty alias `_display_pairs()` reproduces the old `COMP_ORDER` **exactly**
(asserted in test), so every existing cell's table is unchanged.

The underlying solver symbols stay `C3` / `R6` deliberately — they remain inside
the `R1..R8` / `C1..C4` space that the BOM writeout, Monte-Carlo sampler and
scoring all enumerate.

## 3. Pairing — untouched, as requested.

## 4. The ~4 dB ideal-curve offset — found and fixed

`response_tab._effective_dc` hard-coded the **2nd-order** centre-gain formula
`Ki·Q/ω₀` for every section with `section_kind() == "bp"` and never reached the
shape-aware `section_dc_gain`. Because the new 3rd-order sections deliberately
report kind `"bp"` (so they inherit the Ki control, the snapper's peak-normalised
mode and the Resulting-Response inclusion test), they hit that branch and got the
wrong passband gain. The overlay is normalised to sit at `eff_dc` at f0, so the
whole ideal curve floated up by the error.

For `BP1HP` the error is `20·log10(√(1+(p₁/ω₀)²))` dB:

| f1/f0 | 0.3 | 0.8 | 1.0 | **1.2** | 2.0 | 5.0 |
|---|---|---|---|---|---|---|
| dB high | 0.37 | 2.15 | 3.01 | **3.87** | 6.99 | 14.15 |

— so ~4 dB lands at f1 ≈ 1.2·f0. For `BP1LP` it was ~86 dB, since that shape's
Ki is in (rad/s)² and the formula is not even dimensionally valid.

Fixed by routing through `section_dc_gain` (with the Ki override substituted),
exactly as the Topology tab already does. Verified against the realized
`|H(j2πf0)|` rebuilt from snapped components:

| section | realized \|H(f0)\| | section_dc_gain | err | old formula |
|---|---|---|---|---|
| BP1HP f1/f0=1.2 | 1.008 | 1.000 | 0.75% | +3.87 dB |
| BP1HP f1/f0=0.3 | 1.013 | 1.000 | 1.31% | +0.37 dB |
| BP1LP f1/f0=3.0 | 0.9973 | 1.000 | 0.27% | +85.96 dB |
| **BP 2nd order** | 1.001 | 1.000 | 0.14% | **+0.00 dB** |

Residual error is E-series snapping, not the overlay. The 2nd-order row confirms
no regression: `section_dc_gain` returns exactly `Ki·Q/ω₀` there.

## 5. Max R ratio default 60 → 500

The old 60 admitted only `4Q² ≤ 60`, i.e. **Q ≲ 3.9**, on any MFB band-pass —
below what most sections need, which is why it had to be raised by hand nearly
every run. 500 clears Q ≈ 11 and leaves the guard rejecting genuinely
unbuildable spreads rather than gating ordinary designs. Help text updated to
explain the 4Q² relationship.

Only the UI default changed; `MAX_R_RATIO` is still read from cfg everywhere, so
saved configs and the isolated-cfg diff view behave as before.

---

## Still open

- **Stage 3 (schematic SVG)** — artwork left of `R1`: series `C0` for the HP
  pair, `R0`+`C0` tee for the LP pair. `_bp3_alias()` is the map to reuse for
  the SVG labels.
- `HP-MFB-QE`, `LP-MFB-QE`, `NOTCH-MFB` and the AM families share the R5-frontier
  property fixed for BP-MFB in stage 2; widening `_r5_is_feedback` is still your
  call.
