# Stage 2 — solvers for the asymmetric BP-MFB cells

Nine files changed. Everything additive except the two requested fixes and one
ranking-bug fix (§4), which needs your sign-off.

---

## 1. `classify_section` — erroneous mapping removed

`_family_from_features` took `order` and never used it. Added one `order >= 3`
branch for complex-pair poles; nothing else touched, no pairing changes.

| origin zeros | order 3 before | order 3 after | order 2 (unchanged) |
|---|---|---|---|
| 0 | LP | LP | LP |
| 1 | BP | **BP1LP** | BP |
| 2 | **HP** ← the mis-route | **BP1HP** | HP |
| 3 | HP | HP | — |

`family_from_section`'s `has_origin_zero` boolean fallback is pinned to
`min(order, 3)` so a producer storing only the boolean still answers `HP` for
order 3 exactly as before — it cannot tell the shapes apart, so it must not be
silently re-routed. `app.py` stores the exact `n_origin_zeros`, so the real path
is unaffected.

## 2. `cells_mfb_bp.py` header — corrected

```
w0/Q = [ (C1+C2) R1 R2 R5 - C1 R3 R4 (R1+R2) ] / (C1 C2 R1 R2 R3 R5)
     = [ (C1+C2) - kappa C1 R3 (1/R1 + 1/R2) ] / (C1 C2 R3),   kappa = R4/R5
```

`R3`, not `R2`; scales with `R4/R5`, not `R4/(R4+R5)`. The stage-1
`E` / spread / sensitivity relations are folded in beside it.

## 3. The four cells

Added to `cells_mfb_bp.all_cells()` as `absorb ∈ {None, "hp", "lp"}` × `qe`.
Ideal and non-ideal both built; `tf_derivation_v2.self_test` passes all six
(degrees, origin-zero counts, no pole-zero cancellation, ideal-limit
1.5e-11 … 1.0e-09). Full cross-family self-test still passes.

**Symbol ↔ designator.** Solver symbols are `C3` and `R6`, displayed as
`C0`/`R0`. This follows the existing AM precedent
(`topology_tab._am_row_designators` + `schematic_svg._am_labels`), which prints
its 3rd-order prefilter `R1`/`C4` as `R0`/`C0`. Real `C0`/`R0` sympy symbols
would have needed ~8 hard-coded component lists extended
(`unified_solver_v2._assemble`'s BOM writeout, `zero_manifold_solver`,
`topology_tab.COMP_ORDER`, the `f"R{i}"` loops, the Monte-Carlo sampler), any
one of which would silently drop the part from the BOM. **Stage 3 needs the
matching `_bp_mfb_row_designators` remap next to the SVG labels.**

**Analytic seeds.** One closed-form inversion covers all four cells
(`tau=0` → non-QE, `nu=0` → HP). Exposed through a new optional
`analytic_seeds` hook (`tf_derivation_v2.analytic_seeds` → cell module; every
other family returns `[]`, so their start sets are untouched) and appended to
the Phase-1 task list *after* the legacy ratio/anchored passes. Seeds are exact
roots — max residual 8.9e-16 — and the ladder scans and subsamples the feasible
`t` range rather than guessing, giving 20–24 seeds per cell everywhere tested
including the `f1/f0 = 10, Q = 15` corner where the window is ~4e-4 wide.

## 4. Ranking bug found and fixed — **please review**

`filter_synthesis`' `top_k` prune builds an **R5 trade-off frontier** and
concatenates it *ahead* of everything else. The membership test is
`0.0 < _r5(s) < best_r5`, and a cell with no `R5` scores `0.0` — so it is
**structurally barred from the frontier**.

That is harmless when every cell in a pool has an `R5`. It is not harmless when
a pool *mixes* R5-less and R5-having cells, which the BP-MFB plain/QE pair
always does. Measured on the `2BP1HP-MFB` pair: a **43-entry, 100%-QE** frontier
spanning sens 2.13 → 4.90 consumed every one of the 20 `top_k` slots, evicting
the plain cell's sens-2.127 optimum before it was ever considered.

The frontier exists for the LP/HP **notch** cells, where `R5` is a real feedback
resistor and a low value means a smaller HF hump. In BP-MFB, `R5` is one half of
the capacitor-free positive-feedback divider whose absolute scale is a
*redundant* DOF that `rescale_isolated_r5r6` deliberately pins — so the prune
was ordering on a quantity with no design meaning, ahead of sensitivity.

Fix: `_r5_is_feedback(topo)` gates frontier eligibility, **scoped to BP-MFB
only**. Regression-checked against a pristine copy — 3LP-MFB, 2LPn-MFB,
2HP-MFB+QE, 3HP-MFB, 2N-MFB and VCVS 2BP are **byte-identical**.

> `HP-MFB-QE`, `LP-MFB-QE` (divider R5/R6), `NOTCH-MFB` and the AM families have
> the **same** structural property. I left them alone because their BOMs are
> validated against today's ordering. Widening the predicate is a separate,
> opt-in decision.

**This is a large part of the answer to your stage-1 QE question.** The
structural `sens ∝ E` penalty is real, but the reason QE *dominated the BOM*
was this prune, not the physics. With it fixed, the plain cell wins wherever it
fits and QE surfaces only when the `4Q²` spread overruns the envelope — the
behaviour stage 1 predicted.

## 5. Response-tab plumbing

- `discrete_snapper.get_T_target` gained `absorb=`; the BP branch emits `K·s²`
  for `absorb="hp"`. Threaded from `snap_to_hardware` via `meta["absorb"]`.
  Without it every snap on a `BP1HP` section is scored against a target that is
  wrong by a whole 20 dB/decade.
- `hw_plots.target_response` recognises `BP1LP`/`BP1HP`, normalises at the
  resonant peak (not a non-existent HF plateau) and passes `absorb` through.
- `topology_tab.section_dc_gain` gives each shape its own centre gain:
  `Ki·Q/ω₀` (2nd), `Ki·Q/|jω₀+p₁|` (BP1HP), `Ki·Q/(ω₀·|jω₀+p₁|)` (BP1LP).
- Ki **units** are now labelled per shape — `rad/s` for 2nd-order and BP1HP,
  `(rad/s)²` for BP1LP. `compute_stage_gains` already emits
  `(rad/s)^(n_poles−n_zeros)`, so it is right by construction; only the labels
  and the gain formula needed generalising.
- `section_kind` returns `"bp"` for both new families on purpose, so the Ki gain
  control, the snapper's peak-normalised mode and the Resulting-Response
  inclusion test all apply unchanged.
- `anchored_bounds` gets an explicit BP-MFB order-3 branch: anchor `C3` when
  `f1 < 0.5·f0`, else `C1` (measured — `C3` is the largest cap below ~0.3·f0).

## 6. End-to-end results

`f0 = 1 kHz`, E12 caps, E48+E96 resistors, `MAX_R_RATIO = 400`, ideal and
non-ideal (`GBWP = 10 MHz`, `A_ol = 1e6`):

| section | winner | sens | pole err | Ki err |
|---|---|---|---|---|
| BP1HP `f1=0.3f0 Q=4` | `2BP1HP-MFB` | 2.13 | 1.4–3.2% | 1.4–1.6% |
| BP1LP `f1=3f0 Q=4` | `2BP1LP-MFB` | 2.02 | 4.0–5.2% | 1.7–1.9% |
| BP1HP `f1=5f0 Q=8` | `2BP1HP-MFB` | 2.19 | 1.0–3.6% | 3.9–5.5% |
| BP1LP `f1=0.2f0 Q=8` | `2BP1LP-MFB-QE` | 4.3–5.7 | 0.3% | 0.2–1.6% |

Errors are post-E-series-snap. The last row is the predicted hand-off: at
`Q = 8` the plain cell's `4Q²` spread no longer fits, and QE takes over.

## 7. Known, unchanged

`MAX_R_RATIO` defaults to **60** in the UI, which admits only `4Q² ≤ 60`, i.e.
`Q ≲ 3.9`, on any plain MFB band-pass. Verified pre-existing: the pristine tree
also returns zero solutions for `2BP-MFB` at `Q = 4` and `Q = 6`. Worth raising
the default or surfacing the limit, but I have not touched it.

Also unchanged and flagged only: for 2nd-order cells `a2_expr = dcs[-3]`
collapses to the constant `1`, so `sens_score` scores only `a1` there. The
3rd-order cells score both `d1` and `d2`, consistent with 3LP/3HP.

## Next (stage 3)

SVG artwork left of `R1` — series `C3`→`C0` for the HP pair, `R6`→`R0` tee with
`C3`→`C0` for the LP pair — plus the `_bp_mfb_row_designators` remap so the BOM
columns read `C0`/`R0`.
