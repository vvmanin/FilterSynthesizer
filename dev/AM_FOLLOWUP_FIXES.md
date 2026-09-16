# AM follow-up fixes

Five issues from your Ackerberg–Mossberg testing. **Nine code files changed**
plus three docs, all included here — drop them into
`C:\Users\Manin\FilterSynthesizer\`, replacing the copies you have.

| File | Issue(s) |
|------|----------|
| `cells_am_core.py`     | 1 (R8 in the non-ideal model), 5 (3LP-AM2 netlist) |
| `cells_am.py`          | 5 (3LP-AM2), 2 (LP-notch `-C1s` twins) |
| `cells_am_hp.py`       | 2 (HP / HP-notch `-C1s` twins) |
| `cells_am_notch.py`    | 2 (pure-notch `-C1s` twin) |
| `unified_solver_v2.py` | 1 (R8 = R7 in BOM), 2 (parallel-C1 Phase-3), 4 (equalize) |
| `discrete_snapper.py`  | 1 (R8 tracks the snapped R7) |
| `topology_tab.py`      | 2 (offer `-C1s`), 4 (checkbox), 5 (3LP-AM2), BOM display |
| `schematic_svg.py`     | 2 (`-C1s` artwork), 5 (3LP-AM2 artwork) |
| `hw_plots.py`          | 1 (R8 in Monte-Carlo), 2 (C1 = C1a‖C1b in response/MC) |
| `AM_IDEAL_TF_ANALYSIS.md`  | 3 (equal-swing derivation, §6.3 rewritten) |
| `AM_NONIDEAL_ANALYSIS.md`  | 3 (slew note reconciled) |
| `AM_INTEGRATION_README.md` | cell count + feature notes |

Cell count went **12 → 20**: added `3LP-AM2` and seven `-C1s` parallel-input-cap
twins (`2HP-AM-C1s`, `3HP-AM-C1s`, `2HPn-AM-C1s`, `3HPn-AM-C1s`, `2LPn-AM-C1s`,
`3LPn-AM-C1s`, `2N-AM-C1s`).

---

## Issue 1 — R8 missing from the results / response / Monte-Carlo  ✅ fixed

**Cause.** The matched inverter pair R7 = R8 was carried as a *single* symbol R7
(their ratio is 1 and cancels from the ideal response, which avoids a
rank-deficient solve). So R8 never appeared in the BOM, the realized response, or
the MC spread.

**Fix.** R8 is now a genuine independent symbol in the **non-ideal** transfer
function (the ideal solve still uses the single R7 — no rank problem). The
solver sets `R8 = R7` as the nominal, the snapper makes R8 track the *snapped*
R7 value, and because R8 is now in the non-ideal `tf_var_list` it shows up in the
BOM table and is perturbed **independently** in Monte-Carlo. That independent
perturbation is the physically correct model — R7/R8 mismatch is exactly what
degrades the active GB compensation, so the MC ω0/Q spread is no longer the
~15%-optimistic figure the old single-symbol model gave.

**Result.** Every AM BOM now lists R7 *and* R8 (equal nominal), the realized
Bode uses both, and MC varies them separately. (The MFB HP `+R8` twins, where R8
is a truly separate resistor, are untouched.)

## Issue 2 — some C1/C2 (HF-gain) values unreachable; `-C1s` split  ✅ fixed

**Cause.** For the C1-input cells (HP, HP-notch, LP-notch, pure-notch) the
passband gain is the **capacitor ratio C1/C2**, which the resistor snap cannot
correct. On E12 the reachable ratios are quantized: gain 1.0 (C1 = C2) and 1.22
(1.2/1.0·…) solve, but **1.1 has no single-cap pair** within tolerance → 0 BOMs.

**Fix — the `-C1s` twin.** Each C1-input cell now has a parallel-input-cap twin
(`C1 = C1a ‖ C1b`) offered *alongside* the single-C1 cell, so both are solved in
one cycle and ranked together. Two E-series caps in parallel synthesize a far
denser value lattice, so the ratio 1.1 (and, for the notch cells, the zero
frequency wz, which C1 co-sets) becomes reachable. The split lives in Phase-3:
for each snapped C2 the C1 target is **re-computed as (gain)·C2_grid** and the
nearest parallel pair is chosen — this is the key step, because snapping C1 and
C2 independently is exactly what drifts the ratio off target. `C1a`/`C1b` flow
through to the BOM (shown as `C1a,C1b` with a "C1 = C1a ‖ C1b" caption), the
schematic (`…-C1s.drawio.svg`), and the response/MC (collapsed to the parallel
sum, mirroring the existing C2-split handling).

**Result.** `2HP-AM` gain 1.1: single-C1 → **0 BOMs**, with `2HP-AM-C1s` → **8
BOMs**, realized |H| = 1.09 (C1 = 0.27 ‖ 2.2 µF vs C2 = 2.2 µF). Same for the
LP-notch (1.5 → realized 1.54), HP-notch, and the pure notch (1.1 → 1.08, which
previously returned nothing at any budget). Gains that *were* reachable (1.0,
1.22) still solve on the single-C1 cell, which ranks first there (fewer parts).

**Note:** this borrows the principle of the VCVS `2LPn` C2-split, adapted to the
input cap C1; every notched section now has the `-C1s` alternative you asked for,
and HP (non-notch) gets it too since it has the identical C1/C2-gain limit.

## Issue 3 — the "equal node swing" relationship  ✅ corrected

You were right to distrust `ω0R5C2 = ω0R6C3 = 1`. Re-derived from the exact
internal-node transfer functions:

- The three op-amp outputs carry only **two** distinct swing levels: `|V1|`
  (out1) and `|V2| = |V3|` — out3 is the *exact* inverter of out2, so its swing
  is never independent.
- `V2/V1 = -(R5/R4)(1 + s·R4·C2)` exactly, giving at the pole frequency
  `|V2/V1|(ω0) = ω0·R5·C2·√(1 + 1/Q²)`. **Equal swing ⇒ `ω0·R5·C2 = 1/√(1+1/Q²)`**,
  which tends to 1 only as Q → ∞ (it is 0.981 at Q = 5).
- `ω0·R6·C3 = 1` is **not** an independent condition — R6, C3 don't appear in
  `V2/V1`. It falls out of the equal-swing condition plus the pole-frequency
  constraint `ω0² = 1/(R5R6C2C3)`, which forces `R6C3 = R5C2` (equal integrator
  time constants).
- So the handbook `R5 = R6 = 1/(ω0C)`, `C2 = C3` is the **Q → ∞ equal-swing
  asymptote** — clean, same-value, within ~2% of true peak-equalization for
  Q ≳ 5. Equal swing alone does *not* require R5 = R6 (at Q = 5 it gives
  R5/R6 = 0.962).

Full derivation with the numeric check is in `AM_IDEAL_TF_ANALYSIS.md §6.3`
(rewritten); the `AM_NONIDEAL_ANALYSIS.md` slew note is reconciled to the
two-level picture.

## Issue 4 — "Equalize R, C values" checkbox  ✅ added

New AM-only checkbox in the **Component envelope** block, **default ON**. When
checked it imposes the handbook balanced design — `R5 = R6 = R7 = R8` and
`C2 = C3` (equal integrators; one matched resistor array, one integrator-cap
value). Implemented by substituting `R6 → R5`, `C3 → C2` in the *ideal* solve
(the non-ideal model keeps all four independent so MC still perturbs them
separately) and mirroring the values back into the BOM. When **unchecked**, R5/R6
and C2/C3 are free for a tighter E-series fit, while the matched pair `R7 = R8` is
**always** enforced — so GBWP independence and the topology's low sensitivity are
preserved either way.

**Result.** ON: a solved 2LPn-AM shows R5 = R6 = R7 = R8 = 28.7 k, C2 = C3 =
5.6 µF. OFF: R5 = 4.6 k, R6 = 95.3 k, C2 = 6.8 µF, C3 = 8.2 µF, but R7 = R8 still.

## Issue 5 — `3LP-AM2` out2 tap  ✅ added

The out2 taps are now `2LP-AM2`, `3LP-AM2`, and `2BP-AM2`. `3LP-AM2` is the
3rd-order low-pass on the out2 tap; its absorbed-real-pole prefilter uses R3 as
the series element (LP2 already spends R1 on the biquad input), with C4 as the
shunt. Verified against the closed form `prefilter(R3,C4) × 2LP-AM2 biquad` to
machine precision, and offered beside `3LP-AM` for a 3rd-order LP section.

---

### Verification performed

- **Derivation.** All 20 AM cells pass `self_test` (order, zero count, ideal↔
  non-ideal limit). `3LP-AM2` matches its closed form to 1e-16. Each `-C1s` twin
  derives identically to its base cell. VCVS + MFB families still pass
  (`LP`, `LP-MFB`, `HP-MFB`, `BP-MFB`, `NOTCH-MFB`) — zero regressions.
- **End-to-end synthesis** (ideal): R8 present in every BOM and MC comp-dict;
  `-C1s` reaches gain 1.1 on HP, LP-notch, HP-notch, and pure notch where
  single-C1 gives 0; equalize ON enforces R5=R6=R7=R8 & C2=C3, OFF frees them
  with R7=R8 held; a C1-split + R8 BOM runs cleanly through `hw_plots.monte_carlo`.
- **Schematic** name resolution confirmed for `3LP-AM2` and all `-C1s` variants
  (`…-C1s.drawio.svg`); the placeholder AM anchor map applies until the real
  Diagram-Builder canvases are drawn.

### Still to do on your side

- **Streamlit click-through** (the sandbox has no `streamlit`, so the UI edits
  are compile-checked only, CRLF preserved): confirm the AM "Equalize R, C
  values" checkbox appears, that HP/notch sections list both the base and `-C1s`
  cell, and that a 3rd-order LP section offers `3LP-AM2`.
- **Draw the new schematics**: `3LP-AM2.drawio.svg` and the seven `…-C1s.drawio.svg`
  canvases (display-only; synthesis already works).
- Optional: model the R7/R8 pair as *tracking* in your MC tool if you want the
  matched-array reality rather than fully-independent draws.
