# MFB follow-up fixes

Three issues from your 2LPn-MFB / QE testing. **Two files changed**, both
included here — drop them into `C:\Users\Manin\FilterSynthesizer\`, replacing the
copies you have:

| File | Why |
|------|-----|
| `unified_solver_v2.py` | Issue 1 (cap coverage) + Issue 3 (QE divider scale) |
| `nonideal_solver.py`   | Issue 2 (non-ideal speed) |

The other four files from the integration (`cells_mfb.py`,
`tf_derivation_v2.py`, `discrete_snapper.py`, `topology_tab.py`) are
**unchanged** — keep what you already installed.

---

## Issue 2 — non-ideal MFB was ~20× slower than ideal  ✅ fixed

**Cause (not MFB-specific):** every non-ideal worker process re-derived the
*entire* topology catalog. `nonideal_solver._init_worker` called
`get_cases(design)` with **no topology filter** and lambdified
`make_response_func` over **all_cells()**. Registering the 6 MFB cells grew the
catalog to 37 topologies; deriving all of them costs ~54 s, and that ran in
every worker. The ideal solver never did this (it derives only the requested
topologies), which is why VCVS showed no ideal/non-ideal gap.

**Fix:** `solve_nonideal` now passes the set of topologies actually present in
the ideal solutions down to the workers, which derive and lambdify only those.

**Result (2LPn-MFB, AD8505, your config):** **242 s → 13.3 s**, identical 14
solutions — now on par with the 12 s ideal solve. This speeds up *every*
non-ideal solve, VCVS included.

## Issue 3 — "QE" independent divider parked at MΩ  ✅ fixed

Small clarification first: in this netlist the QE divider is **R5 (p→out) /
R6 (p→gnd)**, not R7/R8 — those two only exist in the LPn notch cell. R5/R6 set
`k = R6/(R5+R6)`; only that **ratio** affects the response, so their absolute
scale is a free degree of freedom (verified: H is invariant under R5,R6 ×k).
The ideal optimizer leaves that scale near MΩ.

**Fix:** exactly your proposal — rescale the pair by a common factor so the
**lower of R5/R6 lands in `[R_min, 10·R_min]`** (geometric centre, ≈ √10·R_min),
while keeping the larger one within `R_max`. The ratio is preserved, so the
response and the sensitivity score are unchanged. Reuses the same
`rescale_isolated_r5r6` machinery the VCVS gained cells already use.

**Result (your `R_min`=1 kΩ):** `min(R5,R6)` ≈ 3.2 kΩ on both 2LP-MFB-QE
(27 BOMs) and 3LP-MFB-QE (46 BOMs); gain 1.000, response identical.

## Issue 1 — wide C window dropped mid-cap solutions  ✅ fixed

You were right: a wider `[C_min, C_max]` was **not** a superset of a narrow one.
With your config, narrow `C[2n,10n]` found 8 mid-cap combos
(C2 ≈ 3.9–6.8 nF) that wide `C[0.2n,10n]` missed entirely — and raising the
candidate count didn't bring them back.

**Cause:** both Phase-1 modes pin one cap at `C_max`, so the *other* cap carries
all the cap diversity, kept as "valleys" up to `max_valleys`. The objective is
flat across the notch's cap manifold (many cap ratios solve it equally), so over
a wide window that fixed budget gets spent on the **extreme** caps (smallest +
largest) and starves the middle. A wider window has more feasible E-series combos
but the same 20 slots, so coverage *drops*.

**Fix (two parts, in `harvest`):**
1. **Stratified retention** — bin kept valleys by cap magnitude (log-spaced) and
   keep a representative per occupied bin first, so no region is starved.
2. **Density scaling** — scale the effective valley budget with the window's
   log-width (`eff = max_valleys · max(1, ln(C_max/C_min)/ln 5)`), restoring the
   per-decade density a narrow window has. Narrow windows are unchanged
   (`eff == max_valleys`), so VCVS and existing runs are unaffected.

**Result (your `max_valleys`=20):** wide `C[0.2n,10n]` is now a **strict
superset** of narrow `C[2n,10n]` — all 8 mid-cap combos recovered, 44 BOMs vs 20,
narrow run byte-for-byte unchanged. Cost of the wider solve rose modestly
(17 s → 23 s) because it now legitimately explores ~2.4× more cap combos.

### One thing this does *not* change: ranking
Results are still ranked by sensitivity alone. With a wide window the new
small-cap solutions (C2 ≈ 0.2 nF, R6 up to ~1 MΩ) can have sensitivity *equal to
or slightly better than* the comfortable mid-R ones, so they sit at the top while
the comfortable solutions sit a few rows lower (they're present now — that's the
fix above). If you'd like comfortable-R solutions surfaced when sensitivities are
comparable, I can add an optional max-resistance tiebreaker to the sort — say the
word and I'll wire it in (it would touch the global ranking, so I left it out for
now).

---

## Regression
- `self_test(['LP'])` — all 12 VCVS cells PASS
- `self_test(['LP-MFB'])` — all 6 MFB cells PASS
- VCVS 2LP-gained (narrow C): unchanged
- 2LP-MFB / 3LP-MFB (feasible config): 11 / 78 BOMs, inverting, gain 2.0
- 2LPn-MFB: 26 BOMs, non-inverting (+1), gain 1.5
