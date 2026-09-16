# Ackerberg–Mossberg cells — finite-GB analysis & open items

Companion to `AM_IDEAL_TF_ANALYSIS.md` (ideal transfer functions, CAS-verified)
and `AM_INTEGRATION_README.md` (the code integration). This document reports the
**finite gain-bandwidth (GB)** behaviour of the 12 AM cells and resolves the
open items you raised. Every pole-accuracy number here comes from the *shipped*
symbolic model (`cells_am_core.am_eqs`), so this analysis doubles as a physics
validation of the non-ideal TFs the solver actually snaps against.

Design point for all sweeps: balanced core (handbook `m = 1`), `f0 = 10 kHz`,
`C2 = C3 = 1 nF`, `R5 = R6 = 1/(ω0 C2)`, `R4 = Q·R5`, matched pair `R7 = R8 = R5`.
Op-amp: `A_ol = 10⁵` (100 dB), `Ro = 50 Ω`, one-pole rolloff, GB swept as the
ratio `ft/f0`. All three amps are identical (the standard AM assumption).

## Method (why the pole numbers are trustworthy)

The one-pole op-amp `A(s) = A_ol·wc/(wc + s·A_ol)` makes a naive closed-loop
denominator a **degree-23** rational with a coefficient span of ~300 decades —
`np.roots` and `mpmath.polyroots` both fail on it, and the exact clearing root
`−wc/A_ol` is *not* even a root of the raw LUsolve denominator (LUsolve injects
pivot artifacts). The robust route, used here, is a **quadratic matrix pencil**:
multiply every nodal row by `(wc + s·A_ol)` so each entry is degree ≤ 2 in `s`,
giving `M(s) = M0 + s·M1 + s²·M2`; the closed-loop poles are the generalized
eigenvalues of the `12×12` companion pencil (`scipy.linalg.eig`, with the pencil
`s → ω0·x` scaled and row/column equilibrated so the QZ survives the dynamic
range). The `n_rows` copies of the exactly-known clearing root are filtered out
by location. Magnitude metrics (gain errors, stopband floors) use the shipped
`num/den` response directly, where any spurious common factor cancels.

---

## (1) Active GB compensation — verified

The whole reason to spend three op-amps is the matched inverter pair `R7 = R8`,
which makes the U3 feedback path actively compensate the finite-GB phase lag of
the U1–U2 integrator loop. The prediction is a **second-order** residual pole
error, `ΔQ/Q ∝ (ω0/ωt)²` and *Q-independent*, versus the single-integrator
`ΔQ/Q ∝ Q·ω0/ωt` of an uncompensated two-integrator biquad.

Measured (2BP-AM, production model) against a script-local **reference** that is
the same loop with U3 replaced by an *ideal* inverter (`V3 = −V2` exactly), i.e.
the uncompensated two-integrator behaviour:

| ft/f0 | ΔQ/Q (AM, Q=5) | ΔQ/Q (ideal-U3 ref, Q=5) | ratio |
|------:|---------------:|-------------------------:|------:|
|   10  |  +7.4e-3       |  +4.42                   | ~600× |
|  100  |  +1.0e-3       |  +0.110                  | ~110× |
| 1000  |  +2.6e-5       |  +0.010                  | ~380× |

The compensation buys **~100×** lower Q error across the band. The Q-independence
is just as important: at Q = 20 the uncompensated reference runs away
(`ΔQ/Q = +3.4` near `ft/f0 ≈ 50`, i.e. the stage nearly oscillates), while the
AM residue at `ft/f0 = 100` is `+9.4e-4 ≈` its own Q = 5 value — flat in Q, as
predicted. `Δω0/ω0 ≈ −(ω0/ωt)` is first-order and **not** removed by the
compensation (it is a frequency shift, not a Q error); the existing
`nonideal_solver` pre-distortion corrects it by design.

### (1b) Attribution of the residual slope

The measured mid-band `|ΔQ/Q|` log-log slope is ~1.4, not the textbook 2.0. This
is **not** a compensation gap: re-running with idealized `Ro = 1e-9` and
`A_ol = 1e9` (GB still finite) the slope moves toward second-order and the
residue collapses to `+7.8e-4 @ ft/f0 = 100` (from `+1.0e-3`). The ~0.1·(ω0/ωt)
first-order remainder is **finite-Ro plus input-branch loading of the Miller
node** — a real, small, Q-independent effect of the physical op-amp output
impedance and the input element loading U1's summing junction, not a shortfall of
the R7 = R8 compensation. It is already inside the shipped non-ideal TF, so the
pre-distortion sees and corrects it.

**Design guidance.** Keep `R7 = R8` matched (the BOM's single R7 value designates
the pair — ideally one resistor array/network for tracking). The compensation
degrades gracefully with mismatch; §(2) quantifies the sensitivity and turns it
into a trim.

---

## (2) `R8/R7` offset as a Q-trim — usable

Splitting the matched pair to `R8/R7 = 1 + δ` is a clean, monotone **Q trim**
(all three amps finite, 2BP-AM, Q = 5):

| ft/f0 | d(Q err)/dδ | d(ω0 err)/dδ |
|------:|------------:|-------------:|
|  30   | +0.806      | +0.468       |
| 100   | +0.595      | +0.490       |

The ω0 sensitivity sits at the ideal `+0.5` (a pair offset shifts `ωz²`-like
terms by `δ/2`); the Q sensitivity is the ideal `+0.5` **plus a GB excess**
`≈ 2Q·ω0/ωt` that grows as GB tightens — so δ becomes a *more* effective Q knob
exactly where GB has pushed Q off target. Recipe:

1. trim **δ** to null the residual Q error (e.g. at `ft/f0 = 30`, baseline
   `ΔQ = +2.4e-3` → `δ ≈ −3e-3` nulls it);
2. re-center ω0 with **R6** (the `δ/2` frequency side-shift is absorbed by the
   composite-integrator resistor, which the pre-distortion already tunes).

This is optional; the default `R7 = R8` with pre-distortion already meets targets
across the practical GB range. The trim is there for very tight `ft/f0` or when a
measured board needs a final Q touch-up.

---

## (3) LP tap comparison — `2LP-AM` vs `2LP-AM2` (offer both)

The two low-pass taps are **ideal-exactly equivalent** (identical `D(s)`,
identical part count, gain orthogonal to the poles in both — `−R6/R2` at out1,
`−R5/R1` at out2). Under finite GB they are **mirror-symmetric** and, for every
metric that matters, **equivalent** (unity gain, Q = 5):

| ft/f0 | ΔQ/Q out1 | ΔQ/Q out2 | Δω0/ω0 (both) | DC err o1/o2 |
|------:|----------:|----------:|--------------:|-------------:|
|  30   | −4.4e-3   | +4.7e-3   | −1.47e-2      | 0.000/0.000 dB |
| 100   | −4.4e-3   | +4.7e-3   | −1.47e-2      | 0.000/0.000 dB |
| 1000  | −6.1e-4   | +4.0e-4   | −1.50e-3      | 0.000/0.000 dB |

The Q errors are equal in magnitude and **opposite in sign** (out1 slightly
under-Q, out2 slightly over-Q); `Δω0` is identical; DC error is zero at both
taps; and the finite-GB stopband floors are identical (both settle to the common
`~−100 dB` Ro floor at `1000×f0`, vs the ideal −120 dB). Neither tap dominates,
and their opposite-sign Q offsets mean the *pair* straddles the target — so the
sensible thing is exactly what was asked: **offer both**, solved together and
ranked by `sens_score`, letting the discrete snap and the user's part-value
preferences pick the winner per design. This is wired (`topos = ["2LP-AM",
"2LP-AM2"]` for the 2nd-order LP section).

Stopband detail at `ft/f0 = 100` (identical to snap tolerance):

| freq | out1 | out2 | ideal |
|-----:|-----:|-----:|------:|
| 100×f0 | −77.5 | −77.5 | −80.0 dB |
| 1000×f0 | −99.9 | −99.7 | −120.0 dB |

---

## (4) BP tap comparison — `2BP-AM` vs `2BP-AM2`, and the skip decision

Here the two taps are **not** equivalent. `2BP-AM` (out1, input `R1 → m1`) is the
band-pass tap of the classic resonator; `2BP-AM2` (out2, input `C1 → m1`, with
`R1/R2/R3` absent) is a legitimate ideal realization but carries three finite-GB
drawbacks:

**(a) ~10× larger residual Q error** (unity peak):

| ft/f0 | ΔQ/Q out1 | ΔQ/Q out2 (Q=5) |
|------:|----------:|----------------:|
|  30   | +2.4e-3   | +3.2e-2         |
| 100   | +1.0e-3   | +9.9e-3         |
| 1000  | +2.6e-5   | +9.2e-4         |

**(b) 8–10 dB worse HF stopband floor** — the C1 input current escapes through R5
to the out2 node as U1's virtual ground degrades with GB, a feed-through the out1
tap does not have. At `ft/f0 = 100`:

| freq | out1 (Q=5) | out2 (Q=5) | out1 (Q=20) | out2 (Q=20) |
|-----:|-----------:|-----------:|------------:|------------:|
| 100×f0 | −54.7 | −52.0 | −66.7 | −63.6 dB |
| 1000×f0 | **−63.9** | **−56.0** | **−75.9** | **−66.1** dB |

The gap grows with frequency (feed-through rises as the loop gain falls).

**(c) Q-shrinking input cap.** Unity peak on out2 needs `C1 = C3/Q`, which at
Q = 20 is `50 pF` and keeps shrinking with Q — it falls below `C_min` for
high-Q sections, where the out1 tap (a resistor input) has no such limit. The
out2 tap is also a capacitive input load on the source.

**Decision — `2BP-AM2` is implemented, verified, and callable by name, but NOT
offered in the topology picker.** For the band-pass section the AM family offers
only `["2BP-AM"]`. Pooling the two the way the LP taps are pooled would be
pointless: `2BP-AM` is better or equal on *every* metric, so it would win every
ranking and the extra cell would only add solve cost and a worse-BOM distraction.
The cell stays in the library (registered, `self_test`-passing, closed-form
verified) so it can be called directly for study or special cases; this document
is its rationale. (Contrast the LP taps in §(3), which are genuinely equivalent
and therefore *are* pooled.)

---

## Skipped / brief items

**Open item (3) — loading of the out3 node: skipped, per your instruction** —
out3 is an internal node, never used as a section output, so its external loading
is moot. (Internally its loading by the R7/C3 network *is* in the model and shows
up in the pole numbers above.)

**Noise (brief).** Three uncorrelated op-amp noise sources sum in the AM loop, so
input-referred noise is ~√3 higher than a single-amp biquad at equal impedance —
the price of the three-amp architecture, partly offset by the lower Q sensitivity
(less noise peaking from Q error). Keep node impedances moderate (`R5 = R6 =
1/(ω0 C2)` at the balanced point already does this) to keep thermal noise and
stray-C effects in check.

**Slew / large-signal (brief).** The three op-amp outputs carry only TWO
distinct swing levels — `|V1|` (out1) and `|V2| = |V3|` (out3 is the exact
inverter of out2) — and at the handbook balanced point they are peak-equalized to
within ~2% at Q = 5 (exact as Q grows; the precise equal-swing condition is
`ω0·R5·C2 = 1/√(1+1/Q²)`, see `AM_IDEAL_TF_ANALYSIS.md §6.3`). So no one amp
slew-limits well before the others — there is no single internal slew hot-spot,
unlike topologies with one high-gain internal node. Standard practice: verify the
fastest internal node's `dV/dt = 2π·f·V_pk` against the op-amp slew rate for the
intended output level.

---

## (5) Far-band HF hump/ascent with finite op-amp output impedance Ro

**Observed.** With a real op-amp, an all-AM cascade grows a rising response in the
far passband (band-reject: the passband climbs tens of dB above the notch;
band-pass: the far stopband peaks back up). Replacing every section with MFB
removes it. It is present in AM only, and an **ideal** op-amp removes it.

**Root cause — finite output impedance Ro, NOT finite gain/GBWP.** Decomposing
the shipped non-ideal model on a balanced 2N-AM notch (f0 = 1 kHz, Q = 2,
C = 10 nF, A_ol = 1e5, GBWP = 1 MHz), the far-band level was measured with each
non-ideality switched off in turn:

| freq | full non-ideal | Ro → 0 (gain kept) | A_ol → 1e12 (Ro kept) |
|-----:|---------------:|-------------------:|----------------------:|
| 30 kHz  | +1.2 dB | −0.0 dB | +1.2 dB |
| 100 kHz | **+14.5 dB** | −0.2 dB | **+14.5 dB** |
| 300 kHz | +2.5 dB | −1.3 dB | +2.5 dB |

Setting `Ro → 0` **removes the hump entirely**; making the gain infinite while
keeping Ro leaves it **unchanged**. So the "ideal op-amp fixes it" observation is
specifically the **zero-Ro** part of ideal — the finite DC gain and GBWP are not
the cause.

**Why it scales with Ro, and why it is a resonant hump.** Once an AM integrator's
op-amp loop gain rolls off, the op-amp can no longer present a low-impedance
output, so its integrating capacitor sees Ro in series → a HF **zero at
ω ≈ 1/(Ro·C)** appears in that integrator's response (the integrator stops
integrating and its gain stops falling). The AM biquad puts **two** integrators
(U1, U2) plus the inverter (U3) in a **feedback loop**; above both Ro·C zeros but
still below GBWP (where the op-amps retain loop gain), the loop has excess HF
gain and phase and **resonates**, producing the hump. Measured hump vs Ro on the
same section:

| Ro | hump peak | at |
|---:|----------:|---:|
| 1200 Ω | +17.8 dB | 115 kHz |
|  200 Ω | +10.8 dB | 283 kHz |
|  100 Ω |  +7.9 dB | 399 kHz |
|   50 Ω |  +4.9 dB | 562 kHz |
|   10 Ω |  −0.0 dB | — |

Lower Ro pushes the Ro·C zeros up in frequency (toward/above GBWP, where the
op-amps no longer have the gain to sustain a resonance), so the hump shrinks and
climbs in frequency, vanishing by ~10 Ω.

**Why MFB is immune.** MFB is a **single** op-amp; its feedback capacitor shunts
HF signal straight to the one summing node at the output. There is no internal
loop of integrators to resonate, so Ro merely adds a benign real HF pole — the
same balanced-values check on 2LPn-MFB with the identical bad `Ro = 1200 Ω`
peaks at **−0.1 dB** (flat) vs the AM section's +18 dB. This is the well-known
reason multi-amp state-variable/biquad topologies (Tow–Thomas, KHN,
Ackerberg–Mossberg) have poorer HF behaviour than single-amp Sallen–Key/MFB when
the op-amp output impedance is non-negligible.

**This is correct behaviour, not a bug.** The Thevenin-behind-Ro model is applied
identically to every family (one Ro per op-amp); AM simply has three op-amps in a
resonant loop while MFB has one. The plot is faithfully showing a real property.
`Ro = 1200 Ω` is a very high open-loop output resistance (typical parts are
10–100 Ω, and closed-loop output impedance is far lower in-band); it exaggerates
the effect.

**Practical guidance.**
- For AM sections, use a **low-Ro op-amp** (≤ ~50 Ω keeps the hump under ~5 dB;
  ≤ ~10 Ω removes it). Effective closed-loop output impedance matters — a fast
  op-amp with a stiff output stage is what AM wants.
- The hump sits **well above the passband**; for many uses it is harmless. It
  degrades the **far** stopband, so it matters most for band-reject / band-pass
  where deep wideband rejection is required.
- When only a high-Ro op-amp is available and wideband rejection is essential,
  prefer **MFB** for those sections (immune), or add a passive HF trim after the
  AM stage. The resistor re-optimisation in `nonideal_solver` cannot remove this
  — it is a topology/Ro property, not a component-value error.
