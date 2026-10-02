# FS-033 — Near-notch sections: design note

State: **VALIDATING** (built 2026-10-01, follow-up fix 2026-10-02).
Checks: `python dev/fs033/check_near_notch.py [--solve]`.

## 1. Problem

`pairing_utils._family_from_features` called a section with a finite jω zero a
pure `notch` whenever `|wz/w0 − 1| < 0.05`. The 2N cells (VCVS `2N`, `2N-MFB`)
realize H(0) = H(∞), so they can only put the zero **at** f₀. A section with
f_z/f₀ = 1.037 at Q = 9.83 (maintainer's Elliptic BP, prototype order 13,
Section 10) had its zero moved by ≈ 0.7 pole bandwidths. The result was a poor
snap cost and a wrong response, and both got worse as Q rose.

## 2. Rule (Tier A)

Forcing the zero onto the pole changes the response by

    H_true − H_forced = K (wz² − w0²) / D(s),  min_ω |D(jω)| = (w0²/Q)·√(1 − 1/(4Q²))

so the worst-case error, in units of the section gain K, is

    ε = |r − 1| · Q / √(1 − 1/(4Q²))   (Q > 1/√2;  else ε = |r − 1|),   r = (wz/w0)²

`pairing_utils.notch_forcing_error(wz, w0, Q)`. A section is `notch` only if
**ε < `NOTCH_EPS` = 1e-3** (−60 dB). Otherwise `wz > w0` gives `LPn` and
`wz < w0` gives `HPn`. Exact transform notches (ε ~ 1e-8…1e-15) stay `notch`.
Section 10 has ε = 0.75 and is now `LPn`. 3rd-order near-notches, which were
gated `pending` before, now route to 3LPn / 3HPn.

## 3. Dual solve (Tier D, CONTRACTS §3 exception)

A sweep showed narrow Butterworth / Inverse-Chebyshev band-rejects whose
nominal notch pair leaves the engine 1e-4 … 7e-6 off the zero (ε 2e-3 … 4e-2).
As LPn / HPn alone those sections lose their VCVS / MFB BOM: the cells need a
component spread of ≈ 1/|r − 1|. Maintainer decision: for such uneven cases,
solve both and let **snap cost** decide, not sens score.

- `pairing_utils.near_notch_section(sec)`: order 2, zero inside the old 5 %
  window (`NEAR_NOTCH_TOL`), ε ≥ `NOTCH_EPS`.
- `topology_tab._render_section` submits a second job on the same family's 2N
  cells (`_notch_cells`). The two results are merged (`_merge_results`), the
  BOM table defaults to *Snap cost*, and an ℹ caption gives the offset and ε.
- Both jobs get the same cfg (true f_z), so a 2N row that cannot place the
  zero pays for it in snap cost.
- When a near-notch has no BOM, the message (`_render_no_realization`) names
  the spread VCVS / MFB would need and suggests AM.

## 4. Snapper (Tier C) — unchanged on purpose

The build first moved the snapper's Q point for near-notches from the notch
skirt f₀(1 + 1/2Q) to f₀. The maintainer then saw "worse results" (2026-10-02,
Section 6 VCVS f₀ 1973 Hz Q 44.7 f_z 2041 Hz; Section 8 AM f₀ 1997 Hz Q 451 f_z 2004 Hz).
HEAD vs build on identical inputs:

| Case | HEAD | build (f₀ Q point) | build after revert |
|---|---|---|---|
| S6 VCVS, TL072, best snap | 14.20 | 21.63 | 14.20 |
| S8 AM, ideal, best snap | 28.39 | 38.29 | 28.39 |
| S8 AM min sens | 1.40 | 1.40 | 1.40 |

The solutions and sens values were identical in every column. Only the metric
moved: at Q 45 – 450, f₀ is the top of a sharp resonance. The edit was
reverted, so the snapper keeps its 5 % window and reproduces HEAD exactly.

## 5. Validation summary

- ε agrees with the dense-grid `max |H_true − H_forced| / K` within 1 % (Q 0.5 … 30).
- 23 designs: 32 sections move notch → LPn/HPn, and every reclassified 2nd-order
  section is a near-notch (dual-solved). Every exact notch stays `notch`.
- Section-10-like case (ideal op-amp, Balanced):

  | Family | Before (2N) | After |
  |---|---|---|
  | VCVS | 9.81 | 0.85 (2LPn) |
  | MFB | no BOM | 0.64 |
  | AM | 0.96 | 0.57 |

- MFB now finds 2LPn-MFB BOMs in most reclassified LPn sections, where
  2N-MFB had none. Where the LPn / HPn cell has no BOM (|r − 1| ≲ 0.5 %,
  high Q), the 2N rows stay and win.
- `verify.py` and `dev/fs008/check_spice_export.py` pass.
- No cell / TF change, so `tf_cache_v6.json` and the kernel caches stay valid.

## 6. Recommendations / follow-ups

1. **Skip the 2N job for AM.** `2N-AM` already drives wz to the real f_z, so
   `2LPn-AM` / `2HPn-AM` and `2N-AM` give identical rows (S8: same snap 28.39,
   sens 1.40). The second job only duplicates rows and doubles solve time.
   One-line gate in `_render_section`: `... and not am`.
2. **Notch scoring peak window** (`scoring._response_metrics`, peak searched
   only below 0.8·f_notch). The non-ideal penalty misses the Q bump of LPn with
   f_z/f₀ < 1.25 and of every HPn. Separate item.
3. **Engine precision of narrow band-rejects.** Butterworth / Inverse-Chebyshev
   BR poles are not geometrically symmetric about the zeros (clustered-root
   imprecision). Butterworth 1980–2020 Hz and 1995–2005 Hz return identical
   pole Qs. Fixing this would turn those near-notch artifacts back into exact
   notches. Separate item.
4. **High-Q snapping.** Snap costs of 14 – 30 on Q 45 – 450 sections are intrinsic
   to E-series snapping (unchanged by FS-033). Candidates: an E96 / E192 or
   series-pair resistor option for the Q-setting resistor, or a Q-aware weight in
   the snapper's Q term, so high-Q costs are comparable across sections.
5. **Batch summary / report.** Neither shows which half of a dual solve the
   picked row came from. The topology name is enough for now; consider a
   "near-notch" tag in the batch status and the PDF report.
6. **Snap cost as the default sort everywhere?** The maintainer noted that
   sens score cannot be the single deciding parameter. Today only near-notch
   sections default to *Snap cost*; making it global (or a combined rank) is a
   UX decision for later.
