# Multiple-Feedback (Friend/Rauch) LP cell family — integration

Adds the **`LP-MFB`** topology family alongside the existing VCVS (Sallen-Key)
family, selectable per cascade section. Six cells:

| cell | topology | sign |
|------|----------|------|
| `2LP-MFB`, `3LP-MFB` | all-pole Rauch MFB | **inverting** (−1) |
| `2LP-MFB-QE`, `3LP-MFB-QE` | all-pole + positive-feedback Q-boost | **inverting** (−1) |
| `2LPn-MFB`, `3LPn-MFB` | LP-notch (Friend SAB, with R8) | **non-inverting** (+1) |

## Two design decisions worth re-reading

1. **The LP-notch cell is NON-inverting** — including the with-R8 version.
   At HF, C2/C3 short and the V+ node collapses to `H(∞) = R8/(R3+R8) > 0`;
   since `H(∞)=K` and `H(0)=K·ωz²/ω0²` share K's sign (and the constant term
   must stay positive for LHP poles), `H(0) > 0` too. A 60-design sweep found
   23 feasible, **all non-inverting, zero inverting**. This is structural: the
   transmission zero *requires* feed-forward into V+ (the R3 leg), and that
   feed-forward is exactly what makes the section non-inverting. There is **no
   inverting realization of this netlist, with or without R8.** So the sign was
   *not* a valid reason to prefer with-R8 over R8-open — but excluding R8-open
   still stands for the real reasons (pinned gain `ωz²/ω0²`, redundant R7,
   restricted target subset). The cascade tracks per-section sign (it already
   does), so the non-inverting LPn sits beside the inverting all-pole cleanly.
   *If your own tool shows with-R8 inverting, it's a sign-convention/netlist
   difference — on this netlist it's non-inverting. The sign is one constant
   (`SIGN_NOTCH`) in `cells_mfb.py` if you need it flipped.*

2. **R8 may go below R_min.** R8 absorbs the HF-floor / passband-gain target,
   so it is allowed down to `R_min × R8_RELAX_FACTOR` (default 0.02) while every
   other resistor still honours `R_min`. The gain residual only drives R8 small
   when the target demands it, so ordinary gains still settle R8 in the normal
   window. (This is the simple always-relaxed-floor reading; a strict two-pass
   "only relax if no in-range solution exists" is noted under *Remaining* below.)

## Files

- **`cells_mfb.py`** — NEW. The whole family behind the `tf_derivation_v2` seam
  (`FAMILY`, `topo_name`, `all_cells`, `dc_gain_to_K`, `var_list`, `build_ideal`,
  `build_nonideal`). Drop into the project root next to `cells_lp.py`.

- **`mfb_integration.patch`** — edits to 4 existing files. Apply from the repo
  root: `patch -p1 < mfb_integration.patch` (round-trip verified to apply clean).
  - `tf_derivation_v2.py` — import + register `cells_mfb` in `REGISTRY` (2 hunks).
  - `unified_solver_v2.py` — make `cell_layout` tolerant of MFB topos (no
    `gain`/`has_R7` keys); lambdify realized `H(0)` per cell; add an `LP-MFB`
    gain/sign branch in `_assemble`; R8 relaxed lower bound in
    `anchored_bounds`/`phase3_res_bounds` + R8-exempt R-range reject (9 hunks).
  - `discrete_snapper.py` — `meta.get("gain", …)` so MFB cells snap (2 hunks).
  - `topology_tab.py` — wire the **MFB (Friend)** radio: substitute MFB cell
    names in the LP/LPn branch, make those sections solvable, update captions
    (6 hunks). MFB is offered for LP/LP-notch sections only.

## A note on the engine (why R3-as-residual, not R5_constraint)

The engine's algebraic-constraint slot is hard-wired to the symbol `R5`
(`tf_derivation_v2.cell_components`, line ~122). In the LPn netlist **R5 is a
real, independent element** (m→gnd), so that slot can't carry the notch
constraint. The on-axis zero is therefore enforced as an extra **residual**
(s¹ numerator → 0) with R3 kept as a free search variable. No engine change
needed, and the solve stays well-conditioned (R3 enters the s¹ coefficient
linearly). A `_solve_tf_G` helper derives every cell via **conductance-cleared
Cramer determinants** (substitute R→1/G so matrix entries are polynomial, take
determinants — minimal s-degree, no spurious LUsolve factors — then G→1/R back).
This was necessary: the naive `cancel(LUsolve(...))` on the 3rd-order notch ran
**>110 s**; the determinant route does it in **~5 s**.

## Validation (all passed)

- **Engine `self_test(['LP-MFB'])`** — all 6: correct order, correct zero count
  (0 all-pole / 2 notch), ideal↔non-ideal limit agreement 1e-10…1e-12.
- **Design-solve** (standalone least-squares on each cell's residuals) — all 6
  hit arbitrary targets (poles, Q, real pole, notch freq, gain) at machine
  precision (res 1e-13…1e-15), correct sign throughout.
- **End-to-end** through the production `synthesize()` → solver → snapper:
  `2LP-MFB` (11 BOMs, sign −1, gain 2.000), `3LP-MFB` (16 BOMs, −1, 2.000),
  `2LPn-MFB` (4 BOMs, sign +1, gain 1.500) — real E-series values, ranked
  ascending by `sens_score` (best-first).
- **VCVS regression** — `self_test(['LP'])` all 12 pass; `2LP-gained` e2e
  unchanged (19 BOMs, gain 2.000). The edits are backward-compatible (`.get`
  defaults match prior VCVS behaviour; the `LP-MFB` branches only fire for the
  MFB family).

## Remaining / optional

- **Streamlit UI** could not be exercised headlessly (streamlit not installed in
  the sandbox); the `topology_tab.py` edits are logic-only and `py_compile`-clean,
  but click-through the MFB radio on an LP and an LP-notch section to confirm.
- **R8 two-pass**: implemented as an always-available relaxed floor. If you want
  the strict "relax only when no in-range solution exists", gate the floor on a
  first-pass empty result.
- **QE k-margin**: gain blows up at `R2·R5 = R4·R6` and damping → 0 as `a1→0`;
  the bounds/`_assemble` checks already reject infeasible roots, so an explicit
  `k < k_crit` margin is optional.
- **Schematics**: `schematic_svg.py` has no MFB drawings yet (display-only;
  doesn't affect synthesis).
