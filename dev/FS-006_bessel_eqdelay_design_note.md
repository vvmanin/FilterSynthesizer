# FS-006 — Bessel and equiripple-delay responses: math base and synthesis design note

Purpose: the mathematical base, the root-synthesis algorithms, and the reasoning behind the scope
decisions for FS-006 (Bessel = maximally flat group delay; equiripple group delay). It is written
for a coding session that has the project open. It does not refer to project internals beyond the file
names in the item. The companion file `dev/fs006/fs006_reference.py` holds a verified numpy-only implementation of
everything below, with embedded seeds and self-tests. Every number in this note was computed from it,
or from a 50–60-digit mpmath cross-check.

Conventions. Prototype frequencies are angular (rad/s). *Delay-normalized* means τ(0) = 1 s for
Bessel, or nominal delay τ_nom = 1 s for equiripple. *−3 dB normalized* means |H(j1)| = 1/√2.
Ω = f/f_c is the frequency normalized to the −3 dB cutoff. n is the order of the LP prototype.

---

## 0. Decisions and answers (summary)

| Question | Answer (reasoning in the section noted) |
|---|---|
| Bessel normalization | Store and compute the prototype **delay-normalized** (exact, from integer polynomials). Default UI meaning of f_c is the **−3 dB** frequency, so it matches the rest of the tool and the published active-filter tables. Offer an alternative "specify group delay τ₀" input for LP. Conversion is one scale factor per order, W₃(n) = ω₃dB·τ₀ (§2.4). |
| Bessel roots | Use exact integer coefficients of θₙ(s) with `np.roots`. The error is ≤ 3·10⁻¹² for n ≤ 10 and 2.6·10⁻⁹ at n = 15. Past n ≈ 15, use `scipy.signal.besselap` (≤ 10⁻¹⁵ to n = 30) (§2.5). |
| Order from spec | The delay-flatness criterion has a **closed form**: ε(ω) = (ωτ₀)²ⁿ/\|θₙ(jωτ₀)\|². The attenuation criterion is **not monotone in n**, so there is no closed-form order formula. Scan n = 1…n_max. If no order meets the spec, report the best attenuation reached and the order that reached it (§2.6). |
| Equiripple-delay spec | Inputs: ripple **±δ (% of nominal delay)** plus f_c (−3 dB) or τ_nom. Order is manual, or auto from a delay band edge f_d and/or a stopband point (f_s, A_s). There are no passband-ripple or stopband-ripple parameters (§3.5). |
| Equiripple-delay roots | Chebyshev (Remez) approximation of constant delay, solved by Newton on pole parameters. **One precomputed seed per order** (δ = 1 %) plus a double-precision continuation reaches any δ in 0.05 %…20 % in ≤ 0.3 s for n ≤ 12. Starting from Bessel poles in double precision fails for n ≳ 7 because the Jacobian condition number grows like ~10ⁿ (§3.3–3.4). |
| HP | Offer it as the magnitude mirror of the LP prototype (s → ω_c/s), but **do not claim flat delay**. A rational HP cannot have constant delay over its passband: τ_HP → τ₀·ω_c²/ω² → 0. Hide the delay spec fields for HP (§4.1). |
| BP | Offer it with a **narrowband meaning**. The standard LP→BP reactance transform tilts the delay by ≈ b·Ω_d peak-to-peak (b = fractional bandwidth), which already exceeds the LP's own 1 % flatness at b ≈ 1–2 %. Recommended option: a **pole-translation BP**. It preserves the LP delay shape to ~1 % up to b ≈ 20 % and uses only standard biquads (§4.2–4.3). |
| BR | **Do not offer** for these two responses, or offer it only as "magnitude-only, delay not preserved". The BR delay grows ~50× toward the notch edges, so no passband has flat delay (§4.4). |
| Asymmetric BP/BR | Moving zeros between the origin and infinity, or moving jω-axis zeros, is **exactly delay-neutral**. It only tilts or reshapes the magnitude. A wideband BP made as an LP×HP cascade has flat delay only near its top edge (§4.5). |
| Manual notches | jω-axis transmission zeros **do not change the group delay at all**, so a stopband notch keeps the Bessel or equiripple delay exactly. The cost is passband droop, a feasibility limit Ω_z > 1.848 for one pair when −3 dB must stay at f_c, and a smaller high-frequency slope. A **passband** notch cannot be linear-phase: its pole pair adds a delay bump of area exactly π, with a slowly decaying 1/Δω² tail (§5). |

---

## 1. Key facts used throughout: poles own the delay

**F1. Per-root group delay.** For H(s) = K·∏(s − zᵢ)/∏(s − pₖ) evaluated at s = jω:

$$\tau(\omega) = \sum_k \frac{\sigma_k}{\sigma_k^2 + (\omega-\beta_k)^2} \;-\; \sum_i \frac{-a_i}{a_i^2 + (\omega - b_i)^2},
\qquad p_k = -\sigma_k + j\beta_k,\; z_i = a_i + j b_i .$$

A left-half-plane (LHP) pole adds positive delay, a Lorentzian of height 1/σ centred at β. An LHP zero
subtracts delay. A right-half-plane (RHP) zero adds delay (the all-pass mechanism).

**F2. An even numerator contributes zero group delay.** If N(s) = N_e(s²) has real coefficients, then
N(jω) = N_e(−ω²) is real. Its phase is piecewise constant, with a jump of π at each jω-axis zero, where |H| = 0
anyway. So:

- zeros at the origin or at infinity (sᵏ has constant phase k·π/2),
- jω-axis zero pairs s² + ω_z² (notches, elliptic-type transmission zeros),
- real zero pairs ±σ, (σ² − s²), which are non-minimum-phase,
- quadrantal quads ±a ± jb,

all leave τ(ω) **exactly** unchanged. Numerical check: adding any of these to a Bessel n = 4 changes τ by
< 5·10⁻¹⁰ s, which is finite-difference noise. **Consequence:** the delay of every filter in this item is decided
entirely by where the poles are. The zeros shape only the magnitude.

**F3. Scaling.** Scaling all poles by a gives τ_a(ω) = τ(ω/a)/a. Delay-normalized and −3 dB-normalized
prototypes differ by exactly this scaling.

**F4. Area theorem.** Over 0…∞, a real LHP pole contributes a total phase lag of π/2 and a conjugate LHP pair
contributes π, so ∫₀^∞ τ dω = n·π/2 for an nth-order all-pole filter. A conjugate pole pair near jω_n contributes a delay bump of area π
whatever its Q. This is used for the passband-notch result in §5.3.

---

## 2. Bessel (Thomson) — maximally flat delay

### 2.1 Definition

The ideal delay is e^{−sτ₀}. The Bessel LP is the all-pole approximation whose delay is maximally flat at ω = 0:

$$H_n(s) = \frac{\theta_n(0)}{\theta_n(s)},\qquad
\theta_n(s) = \sum_{k=0}^{n} a_k s^k,\qquad a_k = \frac{(2n-k)!}{2^{\,n-k}\,k!\,(n-k)!}\;\;(\text{integers}).$$

θₙ is the *reverse Bessel polynomial*. It satisfies the three-term recurrence
θₙ = (2n−1)·θₙ₋₁ + s²·θₙ₋₂, θ₀ = 1, θ₁ = s + 1. This recurrence is the stable way to evaluate θₙ(jω)
without big coefficients. Coefficient lists (a₀ … aₙ): n = 2: 3, 3, 1; n = 3: 15, 15, 6, 1;
n = 4: 105, 105, 45, 10, 1; n = 5: 945, 945, 420, 105, 15, 1. Note that a₀ = a₁, which gives τ(0) = a₁/a₀ = 1.

### 2.2 Group delay in closed form (key to order selection)

For the delay-normalized Bessel LP, with x = ωτ₀:

$$\frac{\tau(\omega)}{\tau_0} = 1 - \frac{x^{2n}}{|\theta_n(jx)|^2},\qquad
\varepsilon_n(x) \equiv 1-\frac{\tau}{\tau_0} = \frac{x^{2n}}{|\theta_n(jx)|^2}
= |H_n(jx)|^2\,\frac{x^{2n}}{\theta_n(0)^2}.$$

This is a known identity for Bessel polynomials. It is verified here numerically to ≤ 1.4·10⁻¹³ for n = 1…10, and
the reference self-test re-checks it. It gives three facts at once:

- τ(ω) − τ₀ = O(ω²ⁿ). All delay derivatives up to order 2n − 1 vanish at DC, which is the meaning of *maximally flat*.
- The delay error is **one-sided**: τ ≤ τ₀ everywhere. The delay only sags; it never peaks.
- The error at any frequency costs one polynomial evaluation. No numerical group delay is needed.

Magnitude: |Hₙ(jx)|² = θₙ(0)²/|θₙ(jx)|². |θₙ(jx)|² is a polynomial in x² with positive coefficients
(e.g. n = 3: 225 + 45x² + 6x⁴ + x⁶; checked exactly for n ≤ 20), so the magnitude is strictly monotone. There is no peaking, and the −3 dB
point is a unique root.

**Gaussian limit.** As n → ∞, |Hₙ|² → exp(−x²/(2n−1)). In −3 dB normalization this means
A(Ω) → 10·log₁₀2 · Ω² = 3.0103·Ω² dB. Selectivity near cutoff is therefore bounded no matter how high the
order is (§2.6).

### 2.3 Other properties worth showing in the UI

Step response, −3 dB-normalized prototypes, computed:

| n | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Bessel overshoot % | 0 | 0.43 | 0.75 | 0.84 | 0.77 | 0.64 | 0.49 | 0.34 | 0.22 | 0.12 |
| Butterworth overshoot % | 0 | 4.32 | 8.15 | 10.8 | 12.8 | 14.3 | 15.4 | 16.3 | 17.1 | 17.8 |

The 10–90 % rise time is ≈ 0.34–0.35/f_c for every order.

Section Q stays low: the maximum per order is 0.577, 0.691, 0.806, 0.916, 1.023, 1.126, 1.226, 1.322, 1.415
for n = 2…10 (table in §2.8). This matters for hardware sensitivity. The equiripple-delay filters of §3 reach
Q ≈ 3.6 at n = 10.

### 2.4 Normalizations and conversions

Three normalizations are in use. All three are the same pole set times a scalar:

| name | condition | poles |
|---|---|---|
| delay | τ(0) = 1 s | roots of θₙ |
| −3 dB ("mag") | \|H(j1)\| = 1/√2 | roots / W₃(n) |
| phase (scipy default) | HF asymptote equals the Butterworth one | roots / a₀^{1/n} |

W₃(n) = ω₃dB·τ₀ is the −3 dB frequency of the delay-normalized filter. It is the unique positive root of
|θₙ(jx)|² = 2·θₙ(0)²:

| n | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| W₃(n) | 1.0000000000 | 1.3616541287 | 1.7556723687 | 2.1139176749 | 2.4274107022 |
| a₀^{1/n} | 1.0000000000 | 1.7320508076 | 2.4662120743 | 3.2010858729 | 3.9362834270 |

| n | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|
| W₃(n) | 2.7033950612 | 2.9517221470 | 3.1796172375 | 3.3916931389 | 3.5909805946 |
| a₀^{1/n} | 4.6716548509 | 5.4071302986 | 6.1426728797 | 6.8782612886 | 7.6138823665 |

For large n, W₃(n) approaches √((2n−1)·ln 2) from below; at n = 10 the asymptote is 1.1 % high.

**Real-world scaling.**

- −3 dB mode: poles_real = 2π·f_c · poles_delay / W₃(n), and the DC group delay is τ₀ = W₃(n)/(2π·f_c).
  Example: n = 4, f_c = 1 kHz gives τ₀ = 336.4 µs.
- Delay mode: poles_real = poles_delay / τ₀, and the derived −3 dB frequency is f_c = W₃(n)/(2π·τ₀).

**Recommendation.** Keep the solver output delay-normalized. That is exact, and its poles are integer-polynomial
roots, which makes table validation easy. Normalize in one place. The UI default "f_c = −3 dB" is consistent
with Butterworth and with the TI and Williams active-filter tables. A delay-mode input is the natural spec for
delay lines, pulse and data applications. Show the derived quantity of the other mode (τ₀ or f_c) in the results
panel either way.

### 2.5 Computing the poles

1. Build a_k exactly with Python ints (`math.factorial`).
2. `np.roots(reversed(float(a_k)))`.
3. Symmetrize conjugate pairs: keep the upper half and mirror it, so pairing sees exact conjugates.

Accuracy against a 60-digit mpmath reference (maximum relative error over all poles):

| n | 5 | 8 | 10 | 12 | 15 | 20 | 25 |
|---|---|---|---|---|---|---|---|
| `np.roots` on exact coefficients | 1.7e−15 | 1.5e−13 | 2.8e−12 | 2.6e−11 | 2.6e−9 | 2.1e−6 | 2.1e−3 |
| `scipy.signal.besselap` | 6e−17 | 2e−16 | 3e−16 | 3e−16 | 7e−16 | 5e−16 | 5e−16 |

A Newton polish with the recurrence does not help much, because evaluating θₙ near its roots in double precision
is itself ill-conditioned. If SciPy is already a dependency, `besselap(n, norm='delay')` is the most robust
choice: it uses Aberth–Ehrlich iteration with asymptotic starting values. Its `norm='mag'` and `norm='phase'`
outputs match the W₃(n) and a₀^{1/n} scalings above (checked to ≤ 3·10⁻¹², the `np.roots` error). Otherwise, cap
Bessel at n ≤ 12–15 with `np.roots`.

### 2.6 Order selection from a spec

Criteria. Use any subset; the chosen order is the smallest n that meets all of them.

- **Delay flatness.** Relative delay error ≤ ε on [0, f_d].
  - −3 dB mode: εₙ(Ω_d·W₃(n)) ≤ ε, with Ω_d = f_d/f_c.
  - Delay mode: εₙ(2π·f_d·τ₀) ≤ ε.
  - Closed form, from §2.2. It is monotone: for a fixed Ω_d, raising n always helps.
- **Stopband.** Attenuation ≥ A_s at f_s.
  - −3 dB mode: A(Ω_s) = 20·log₁₀|θₙ(jΩ_s·W₃)/θₙ(0)|.
  - **Not monotone in n.** In −3 dB mode it peaks and then decays toward the Gaussian limit 3.0103·Ω² dB.
  - In delay mode (fixed τ₀) it also peaks and then *falls* quickly, because each extra order widens the band.

f/f_c at which the Bessel delay error first reaches ε (−3 dB normalized):

| n | ε = 0.1 % | 1 % | 5 % | 10 % |
|---|---|---|---|---|
| 1 | 0.032 | 0.101 | 0.229 | 0.333 |
| 2 | 0.228 | 0.414 | 0.645 | 0.798 |
| 3 | 0.454 | 0.686 | 0.944 | 1.105 |
| 4 | 0.662 | 0.915 | 1.180 | 1.341 |
| 5 | 0.854 | 1.117 | 1.386 | 1.547 |
| 6 | 1.034 | 1.304 | 1.574 | 1.735 |
| 7 | 1.203 | 1.478 | 1.749 | 1.910 |
| 8 | 1.363 | 1.640 | 1.912 | 2.073 |
| 9 | 1.514 | 1.794 | 2.066 | 2.227 |
| 10 | 1.658 | 1.939 | 2.211 | 2.372 |

Attenuation in dB at Ω = f/f_c (−3 dB normalized):

| n | 1.5 | 2 | 3 | 4 | 5 | 10 |
|---|---|---|---|---|---|---|
| 1 | 5.12 | 6.99 | 10.00 | 12.30 | 14.15 | 20.04 |
| 2 | 6.36 | 9.82 | 15.74 | 20.36 | 24.07 | 35.89 |
| 3 | 7.12 | 12.00 | 20.86 | 27.85 | 33.44 | 51.23 |
| 4 | 7.42 | 13.41 | 25.09 | 34.43 | 41.92 | 65.68 |
| 5 | 7.41 | 14.06 | 28.34 | 40.02 | 49.39 | 79.12 |
| 6 | 7.29 | **14.17** | 30.70 | 44.68 | 55.93 | 91.62 |
| 7 | 7.17 | 13.98 | 32.33 | 48.57 | 61.69 | 103.34 |
| 8 | 7.08 | 13.68 | 33.38 | 51.81 | 66.80 | 114.40 |
| 9 | 7.02 | 13.38 | 33.96 | 54.51 | 71.35 | 124.91 |
| 10 | 6.98 | 13.14 | **34.15** | 56.73 | 75.41 | 134.91 |
| 15 | 6.89 | 12.60 | 32.04 | 62.25 | 89.91 | 179.08 |
| 30 | 6.83 | 12.28 | 28.68 | 54.83 | 97.39 | 273.96 |
| Gaussian limit | 6.77 | 12.04 | 27.09 | 48.16 | 75.26 | 301.03 |

Best attenuation over n ≤ 12: 7.42 dB at Ω = 1.5 (n = 4); 14.17 dB at Ω = 2 (n = 6); 23.08 dB at Ω = 2.5
(n = 8); 34.15 dB at Ω = 3 (n = 10); 59.94 dB at Ω = 4 (n = 12).

Algorithm:

```
for n in 1..n_max:
    ok = delay_ok(n) and atten_ok(n)
    track best attenuation seen (value, n)
    if ok: return n
return INFEASIBLE, best      # e.g. "40 dB at 3·fc is not reachable with Bessel; best is 34.1 dB at n = 10"
```

Example results from the reference module:

- f_c = 1 kHz with delay error ≤ 1 % (one-sided sag) up to 900 Hz → n = 4.
- 40 dB at 5 kHz → n = 4.
- 40 dB at 3 kHz → infeasible (best 34.1 dB at n = 10).

For an infeasible spec, report the best value and suggest a higher f_s/f_c or the equiripple-delay response,
which is more selective at 3–4·f_c (§3.5). That response still reaches only 36.4 dB at 3·f_c with ±1 %.

### 2.7 Validation reference: Bessel poles (from 60-digit arithmetic)

Delay-normalized (τ(0) = 1). Conjugate pairs are listed once, together with the real pole:

| n | poles |
|---|---|
| 1 | −1 |
| 2 | −1.50000000 ± j0.86602540 |
| 3 | −1.83890732 ± j1.75438096, −2.32218535 |
| 4 | −2.10378940 ± j2.65741804, −2.89621060 ± j0.86723413 |
| 5 | −2.32467430 ± j3.57102292, −3.35195640 ± j1.74266142, −3.64673860 |
| 6 | −2.51593225 ± j4.49267295, −3.73570836 ± j2.62627231, −4.24835940 ± j0.86750967 |
| 7 | −2.68567688 ± j5.42069413, −4.07013916 ± j3.51717405, −4.75829053 ± j1.73928606, −4.97178686 |
| 8 | −2.83898395 ± j6.35391130, −4.36828922 ± j4.41444250, −5.20484079 ± j2.61617515, −5.58788604 ± j0.86761445 |
| 9 | −2.97926080 ± j7.29146369, −4.63843989 ± j5.31727168, −5.60442182 ± j3.49815692, −6.12936790 ± j1.73784838, −6.29701918 |
| 10 | −3.10891623 ± j8.23269946, −4.88621957 ± j6.22498548, −5.96752833 ± j4.38494719, −6.61529097 ± j2.61156792, −6.92204491 ± j0.86766520 |

−3 dB normalized:

| n | poles |
|---|---|
| 1 | −1 |
| 2 | −1.1016013 ± j0.6360098 |
| 3 | −1.0474092 ± j0.9992644, −1.3226758 |
| 4 | −0.9952088 ± j1.2571057, −1.3700678 ± j0.4102497 |
| 5 | −0.9576765 ± j1.4711243, −1.3808773 ± j0.7179096, −1.5023163 |
| 6 | −0.9306565 ± j1.6618633, −1.3818581 ± j0.9714719, −1.5714904 ± j0.3208964 |
| 7 | −0.9098678 ± j1.8364514, −1.3789032 ± j1.1915668, −1.6120388 ± j0.5892445, −1.6843682 |
| 8 | −0.8928697 ± j1.9983258, −1.3738412 ± j1.3883566, −1.6369394 ± j0.8227956, −1.7574084 ± j0.2728676 |
| 9 | −0.8783993 ± j2.1498005, −1.3675883 ± j1.5677337, −1.6523965 ± j1.0313896, −1.8071705 ± j0.5123837, −1.8566005 |
| 10 | −0.8657569 ± j2.2926048, −1.3606923 ± j1.7335057, −1.6618102 ± j1.2211002, −1.8421962 ± j0.7272576, −1.9276197 ± j0.2416235 |

Use these and SciPy as the validation reference. Published tables (Zverev; Williams & Taylor; TI) list the same
poles to 4–5 digits in the same normalizations; if a printed table disagrees in the last digit, trust the
integer-polynomial roots. Example: n = 2 gives ω₀ = 1.2720, Q = 0.5774, which in the TI form
1/(1 + a₁s + b₁s²) is a₁ = 1.3617, b₁ = 0.6180.

### 2.8 Sections (−3 dB normalized): ω₀ and Q, ascending Q

| n | sections |
|---|---|
| 1 | ω₀ = 1.00000 (real) |
| 2 | 1.27202 / Q 0.57735 |
| 3 | 1.32268 (real); 1.44762 / 0.69105 |
| 4 | 1.43017 / 0.52193; 1.60336 / 0.80554 |
| 5 | 1.50232 (real); 1.55635 / 0.56354; 1.75538 / 0.91648 |
| 6 | 1.60392 / 0.51032; 1.68917 / 0.61119; 1.90471 / 1.02331 |
| 7 | 1.68437 (real); 1.71636 / 0.53236; 1.82242 / 0.66082; 2.04949 / 1.12626 |
| 8 | 1.77847 / 0.50599; 1.83209 / 0.55961; 1.95320 / 0.71085; 2.18873 / 1.22567 |
| 9 | 1.85660 (real); 1.87840 / 0.51971; 1.94787 / 0.58941; 2.08041 / 0.76061; 2.32233 / 1.32191 |
| 10 | 1.94270 / 0.50391; 1.98055 / 0.53755; 2.06221 / 0.62047; 2.20375 / 0.80979; 2.45063 / 1.41531 |

---

## 3. Equiripple group delay (Chebyshev delay; Ulbrich–Piloty type)

### 3.1 Problem statement

Take an all-pole H(s) = K/D(s) of degree n. Normalize the nominal delay to τ_nom = 1. Find the poles such that

$$|E(\omega)| \le \delta \;\; \text{on}\;\; 0\le\omega\le\omega_p,\qquad E(\omega) = \tau(\omega) - 1,$$

with the widest band ω_p for a given δ, or equivalently the smallest δ for a given ω_p.

- **Degrees of freedom:** n real numbers (σ, β per conjugate pair, σ for the real pole).
- **Alternation (Chebyshev characterization):** E equioscillates at n + 1 points
  0 = ω₀ < ω₁ < … < ωₙ = ω_p, with E(ωᵢ) = sᵢ·δ and **sᵢ = −(−1)^{n−i}**.
  - The band-edge extremum is 1 − δ: the delay sags at the edge.
  - τ(0) = 1 + δ for odd n and 1 − δ for even n.
  - ω = 0 is always an extremum, because τ is even in ω.
- **Why n + 1 points.** Near the Bessel solution, a change of the parameters moves the delay by a polynomial of
  degree n − 1 in x = ω². That polynomial is added to the Bessel error −c·xⁿ. The minimax correction is then the
  shifted Chebyshev polynomial, E ≈ −δ·Tₙ*(x/x_p), whose extrema lie at xᵢ = x_p·(1 − cos(iπ/n))/2. This is also the
  right *initial* extremal set.
- **Limit δ → 0:** the extremal points merge at ω = 0, and the solution tends to the Bessel filter.
- Existence and uniqueness are not proved here. Empirically, the continuation path is smooth and a unique solution
  is found for every n ≤ 15 and δ ≤ 20 %.

### 3.2 Parameterization and derivatives

Use x = [ln σ₁, ln β₁, …, ln σ_m, ln β_m, (ln σ_r)] for poles −σ ± jβ (plus a real pole −σ_r for odd n). The
logarithms keep every pole in the LHP and every β positive without constraints. With u = ω − β and d = σ² + u²,
summed over all poles (conjugates included):

$$\tau=\sum\frac{\sigma}{d},\qquad \tau'=\sum\frac{-2\sigma u}{d^2},\qquad \tau''=\sum\frac{2\sigma(3u^2-\sigma^2)}{d^3}.$$

Jacobian columns for one pair (σ, β), with u₁ = ω − β, u₂ = ω + β, d₁,₂ = σ² + u₁,₂²:

$$\frac{\partial\tau}{\partial\ln\sigma}=\sigma\Big[\frac{u_1^2-\sigma^2}{d_1^2}+\frac{u_2^2-\sigma^2}{d_2^2}\Big],\qquad
\frac{\partial\tau}{\partial\ln\beta}=\beta\Big[\frac{2\sigma u_1}{d_1^2}-\frac{2\sigma u_2}{d_2^2}\Big],$$

and for the real pole: ∂τ/∂ln σ_r = σ_r·(ω² − σ_r²)/(σ_r² + ω²)².

The analytic Jacobian is **required**. Finite differences fail, because the quantities to resolve are ~δ-sized
differences of O(1) delays.

### 3.3 Algorithm (Remez exchange + Newton + continuation)

```
remez_fixed_band(n, wp, x, ext):             # ext = [0, w1..w_{n-1}, wp]
    d = 0
    repeat (≤ 60, typically 3–6):
        # (a) level equations: (n+1) x (n+1) Newton on (x, d)
        solve  tau(ext_i; x) - 1 - s_i·d = 0,   J = [∂tau/∂x | -s]
        plain Newton, step capped to 0.5 in log-parameters
        #   (do NOT use a residual-norm line search: the first full step often raises |F|
        #    and then converges quadratically; the line search stalls)
        # (b) exchange: each interior w_i -> local extremum of E
        Newton on tau'(w) = 0 using tau''(w), kept inside the midpoints to its neighbours;
        fallback: golden-section maximisation of |E| in that bracket
        stop when the extremal set moves < 1e-13·wp
    verify: d > 0, ext strictly increasing, |E(ext_i) - s_i d| < 1e-7·d
    return x, d, ext

eqdelay_poles(n, delta_target):
    start from the seed for order n (x, wp, ext at delta = 1 %)
    continuation in wp: r = (delta_target/d)^(1/2n), clipped to [1/1.15, 1.15];
        wp *= r, ext *= r, remez_fixed_band(...); on failure take r = sqrt(r) (≤ 12 tries);
        stop when d crosses delta_target (or already within tolerance)
    secant on (ln wp, ln d) until |d/delta_target - 1| < 1e-10
```

n = 1 is closed form: σ = 1/(1 + δ), ω_p = √(σ/(1 − δ) − σ²).

### 3.4 Why seeds, and what was measured

In double precision, starting the continuation from the Bessel poles fails for n ≳ 7. Near the Bessel point, the
equiripple δ at a band ω_p is roughly (Bessel error at ω_p)/2^{2n−1}, i.e. 10⁻⁹…10⁻¹⁵. The column-scaled
Jacobian condition number there is:

| n | 6 | 8 | 10 | 12 |
|---|---|---|---|---|
| cond near the Bessel start | 1.7·10⁴ | 1.7·10⁶ | 1.6·10⁸ | 1.7·10¹⁰ |

So the Newton-step error exceeds δ and the alternation is lost. This is intrinsic to the problem, not to the
parameterization. With 50-digit arithmetic (mpmath), the same algorithm run from Bessel converges for every
n ≤ 15, taking 1–25 s per order. For n = 14 and 15, the start has to be moved to a band where the Bessel error
is 10⁻⁵…10⁻⁴; the generator tries this automatically.

**Production recipe.** Compute one solution per order offline (δ = 1 %, n = 2…15) and embed it as constants
(`EQDELAY_SEEDS_1PCT` in the reference file, about 6 kB). At run time, continue from the seed in double
precision. Measured results:

- 12 ripple values per order in 0.05 %…20 % for every n = 2…15 (plus a 275-case random sweep with denser seeds).
- Worst |max|E|/δ − 1| on a 20 001-point grid: 1·10⁻¹⁰.
- All poles in the LHP.
- Slowest call ≈ 0.4 s (n = 15), ≤ 0.3 s for n ≤ 12.

The offline generator `dev/fs006/eqdelay_mp.py` is included for regeneration or extension (needs `mpmath`; imports `fs006_reference.py`).

### 3.5 Spec, normalization, order selection, behaviour

- **Spec.** The ripple is **±δ as a percentage of the nominal delay**. The nominal delay is the centre of the ripple
  band; τ(0) = τ_nom·(1 ± δ). Suggested UI range: 0.05 %…10 %. The solver is tested to 20 %.
- **Normalization.** Same scheme as Bessel.
  - −3 dB mode: poles_real = 2π·f_c·p/W₃(n, δ), where W₃ = ω₃dB·τ_nom is computed from the poles as the *first*
    crossing of −3.0103 dB from DC.
  - Delay mode: poles/τ_nom.
  - A useful third output is the delay-band edge, f_p = f_c·ω_p/W₃.
- **Order selection.** Take the smallest n with ω_p/ω₃dB ≥ f_d/f_c (table below), and scan n for any attenuation
  criterion. Attenuation is non-monotone in n, and even non-monotone in δ.

Delay-band edge relative to the −3 dB frequency, ω_p/ω₃dB = f_p/f_c. Delay-normalized ω_p and ω₃dB are in
parentheses:

| n | ±0.1 % | ±0.5 % | ±1 % | ±2 % | ±5 % |
|---|---|---|---|---|---|
| 1 | 0.045 | 0.100 | 0.142 (0.141, 0.990) | 0.202 | 0.324 |
| 2 | 0.377 | 0.555 | 0.651 (0.911, 1.398) | 0.758 | 0.907 |
| 3 | 0.759 | 0.965 | 1.065 (2.015, 1.892) | 1.174 | 1.337 |
| 4 | 1.102 | 1.316 | 1.419 (3.268, 2.302) | 1.529 | 1.680 |
| 5 | 1.423 | 1.644 | 1.747 (4.600, 2.633) | 1.855 | 2.004 |
| 6 | 1.715 | 1.929 | 2.027 (5.980, 2.950) | 2.128 | 2.265 |
| 7 | 1.990 | 2.210 | 2.314 (7.394, 3.195) | 2.425 | 2.585 |
| 8 | 2.239 | 2.441 | 2.527 (8.831, 3.495) | 2.606 | 2.676 |
| 9 | 2.480 | 2.698 | 2.806 (10.285, 3.666) | 2.928 | 3.124 |
| 10 | 2.697 | 2.886 | 2.960 (11.753, 3.971) | 3.017 | 3.031 |
| 11 | 2.914 | 3.130 | 3.241 (13.232, 4.083) | 3.376 | 3.621 |
| 12 | 3.108 | 3.287 | 3.354 (14.719, 4.389) | 3.401 | 3.407 |

Compared with Bessel of the same order:

- **Delay band.** At the same peak-to-peak delay variation (2δ), the equiripple band is 1.24–1.68× wider. The gain
  grows with n and shrinks with δ: ×1.60 at n = 10, ±1 %; ×1.52 at n = 4, ±1 %.
- **Selectivity.** It is better away from cutoff but not at 2·f_c. At n = 6, ±1 %: 35.5 dB at 3·f_c (Bessel 30.7)
  and 52.6 dB at 4·f_c (Bessel 44.7). For n ≥ 6, the attenuation at 2·f_c is equal to Bessel's or up to
  ~1 dB lower at ±1 % (1.6 dB lower at n = 11, ±5 %).
- **Q.** Section Q is much higher. At ±1 %: n = 6 → 1.93 (Bessel 1.02); n = 10 → 3.58 (Bessel 1.42). This means
  more sensitivity to component tolerances. Show it in the hardware stage.
- **Magnitude.** Small peaking is possible for large δ and even n: +0.03 dB (n = 6, ±5 %), +0.08 dB (n = 8),
  +0.12 dB (n = 10), +0.16 dB (n = 12). Do not assume the magnitude is monotone.
- **Step overshoot.** 0–3 % for δ ≤ 5 %. It is larger for even n (2.3–2.9 % at ±5 %), and ≤ 0.2 % for odd n ≥ 5
  at δ ≥ 1 %.

Examples from the reference module:

- f_c = 1 kHz, ±1 % delay up to 1.4 kHz → n = 4. For the same 2 % peak-to-peak variation up to 1.4·f_c, Bessel
  needs n = 6 (error 1.9 %).
- ±1 %, 40 dB at 3·f_c → infeasible for n ≤ 12; the best is 36.4 dB at n = 8.

### 3.6 Related family: equiripple *phase error*

The classic "linear phase with equiripple error" tables (Zverev; Williams & Taylor; 0.05° and 0.5°) minimise
max|φ(ω) + ωτ| rather than the delay ripple. They are a different pole family. Since φ + ωτ = 0 at DC
automatically, its alternation points lie in (0, ω_p]. The same Newton–Remez machinery applies with the phase
error as the residual (∂φ/∂σ and ∂φ/∂β are elementary arctan derivatives). Its delay is not equiripple.

Recommendation: implement the **delay-ripple** family, which matches the item's "delay ripple %" input and the
validation "equiripple-delay ripple within bound". If users expect the Zverev tables, add the phase-error family
later as a variant, and validate it against those published tables. This note does not verify that variant.

### 3.7 Validation reference: equiripple ±1 % poles

These are also the embedded seeds.

| n | ω_p·τ | ω₃dB·τ | τ(0) | poles, delay-normalized (τ_nom = 1) | poles, −3 dB normalized | sections (−3 dB): ω₀ / Q |
|---|---|---|---|---|---|---|
| 2 | 0.910760 | 1.398120 | 0.99 | −1.363010 ± j0.946446 | −0.974887 ± j0.676942 | 1.18687 / 0.60872 |
| 3 | 2.015065 | 1.891911 | 1.01 | −1.502296 ± j2.080227, −1.806133 | −0.794063 ± j1.099538, −0.954661 | 0.95466; 1.35629 / 0.85402 |
| 4 | 3.267570 | 2.302113 | 0.99 | −1.566947 ± j3.338849, −1.964308 ± j1.145994 | −0.680656 ± j1.450341, −0.853263 ± j0.497801 | 0.98786 / 0.57887; 1.60212 / 1.17689 |
| 5 | 4.599633 | 2.632646 | 1.01 | −1.602809 ± j4.672144, −2.038108 ± j2.414442, −2.126247 | −0.608821 ± j1.774695, −0.774167 ± j0.917116, −0.807646 | 0.80765; 1.20018 / 0.77514; 1.87622 / 1.54087 |
| 6 | 5.980481 | 2.950243 | 0.99 | −1.625231 ± j6.052961, −2.079469 ± j3.754773, −2.201682 ± j1.270402 | −0.550880 ± j2.051682, −0.704846 ± j1.272700, −0.746271 ± j0.430609 | 0.86159 / 0.57727; 1.45484 / 1.03203; 2.12435 / 1.92814 |
| 7 | 7.394035 | 3.195385 | 1.01 | −1.640448 ± j7.466136, −2.105565 ± j5.140702, −2.244039 ± j2.612715, −2.277712 | −0.513380 ± j2.336537, −0.658939 ± j1.608790, −0.702275 ± j0.817653, −0.712813 | 0.71281; 1.07784 / 0.76739; 1.73851 / 1.31917; 2.39227 / 2.32992 |
| 8 | 8.830930 | 3.494957 | 0.99 | −1.651398 ± j8.902579, −2.123407 ± j6.557709, −2.270829 ± j4.000341, −2.320397 ± j1.342795 | −0.472509 ± j2.547264, −0.607563 ± j1.876335, −0.649745 ± j1.144604, −0.663927 ± j0.384209 | 0.76708 / 0.57769; 1.31616 / 1.01283; 1.97225 / 1.62308; 2.59072 / 2.74145 |
| 9 | 10.285273 | 3.665545 | 1.01 | −1.659631 ± j10.356480, −2.136324 ± j7.997111, −2.289189 ± j5.418756, −2.347411 ± j2.730995, −2.363247 | −0.452765 ± j2.825359, −0.582812 ± j2.181698, −0.624515 ± j1.478295, −0.640399 ± j0.745045, −0.644719 | 0.64472; 0.98245 / 0.76706; 1.60480 / 1.28483; 2.25820 / 1.93733; 2.86141 / 3.15992 |
| 10 | 11.753126 | 3.970884 | 0.99 | −1.666034 ± j11.823929, −2.146084 ± j9.453359, −2.302508 ± j6.859325, −2.365937 ± j4.149964, −2.390365 ± j1.388367 | −0.419563 ± j2.977656, −0.540455 ± j2.380669, −0.579848 ± j1.727405, −0.595821 ± j1.045098, −0.601973 ± j0.349637 | 0.69614 / 0.57822; 1.20301 / 1.00954; 1.82213 / 1.57121; 2.44124 / 2.25851; 3.00707 / 3.58358 |

---

## 4. HP, BP, BR and asymmetric variants — does flat delay survive?

The general mechanism: a frequency transform Ω(ω) maps the LP delay as

$$\tau_{\text{new}}(\omega) = \tau_{LP}\big(\Omega(\omega)\big)\cdot\frac{d\Omega}{d\omega}.$$

The zeros the transform creates (at 0, ∞ or ±jω₀) add no delay (F2). So the question is only whether the
Jacobian dΩ/dω is constant over the passband. It never is, except for a pure translation.

### 4.1 HP (s → ω_c/s)

τ_HP(ω) = τ_LP(ω_c/ω)·ω_c/ω² → τ_LP(0)·ω_c²/ω² in the passband. Computed values of τ_HP/τ_LP(0) at f/f_c = 2, 3, 5,
10 are 0.250, 0.111, 0.040, 0.010 for n ≥ 4, which is exactly (f_c/f)².

This is fundamental, not a weakness of Bessel. A rational HP has a finite, nonzero gain at ∞, so its phase tends
to a constant and its delay to 0. Linear phase over an infinite passband is impossible, and over a finite band it
needs all-pass equalization.

What a "Bessel HP" does give is the mirrored magnitude shape (no peaking, gentle knee) and a somewhat smaller step
undershoot than Butterworth HP: 16.3 / 29.2 / 33.3 % vs 20.8 / 35.0 / 38.0 % for n = 2 / 4 / 6.

**Recommendation.** Offer HP, since users know "Bessel HP" from TI/ADI tools, but label it *magnitude-mirrored,
delay not flat*. Disable the delay-mode input and the delay-flatness order criterion for HP. Order is manual or
from attenuation. The same applies to equiripple-delay HP; it is harmless but has even less meaning, so it could
simply be hidden.

### 4.2 BP by the standard reactance transform (geometric symmetry)

Ω = (ω² − ω₀²)/(Bω) and dΩ/dω = (1 + ω₀²/ω²)/B. The centre delay is 2τ_LP(0)/B, but the Jacobian changes across
the band. Over the image of the LP's flat band |Ω| ≤ Ω_d, it varies by ≈ **b·Ω_d peak-to-peak** to first order,
with the larger delay at the lower edge (b = B/ω₀). This comes on top of the LP's own delay error.

Delay peak-to-peak over the image of the LP's 1 % band (so the LP alone would be 1.00 %):

| b = B/ω₀ | n = 2 (Ω_d = 0.414) | n = 4 (Ω_d = 0.915) | n = 6 (Ω_d = 1.304) |
|---|---|---|---|
| 0.01 | 1.26 % | 1.70 % | 2.08 % |
| 0.05 | 2.50 % | 4.87 % | 6.78 % |
| 0.10 | 4.25 % | 9.13 % | 12.98 % |
| 0.20 | 8.20 % | 18.2 % | 26.0 % |
| 0.50 | 20.6 % | 46.4 % | 67.9 % |
| 1.00 | 41.8 % | 99.6 % | 154 % |

**Meaning.** A "Bessel BP" is meaningful in the narrowband sense. Its lowpass-equivalent (complex-envelope)
response is the LP prototype, so modulated pulses keep Bessel-like envelopes: low overshoot and no envelope
ringing. That is the real use of such filters (IF and pulse filters). The magnitude is geometrically symmetric,
exactly as for any reactance-transformed BP.

### 4.3 Recommended BP option: pole translation (delay-preserving)

Map every −3 dB-normalized LP pole p to

$$p' = \tfrac{B}{2}\,p + j\omega_0 \quad\text{plus its conjugate},$$

giving n poles near +jω₀ and n mirror poles near −jω₀. Put n_z zeros at the origin and 2n − n_z at infinity.

**Delay.** It is the exact LP delay shape translated to ω₀, plus a mirror term:

$$\tau(\omega) = \tfrac{2}{B}\,\tau_{LP}\!\big(\tfrac{2(\omega-\omega_0)}{B}\big) + \tfrac{2}{B}\,\tau_{LP}\!\big(\tfrac{2(\omega+\omega_0)}{B}\big).$$

The mirror term is the LP delay far in its stopband: small and smooth. Measured peak-to-peak over
ω₀ ± (B/2)·Ω_d, with the LP alone at 1.00 %:

| b | n = 2 | n = 4 | n = 6 |
|---|---|---|---|
| 0.05 | 1.000 % | 1.001 % | 1.002 % |
| 0.10 | 1.001 % | 1.007 % | 1.015 % |
| 0.20 | 1.014 % | 1.063 % | 1.133 % |
| 0.50 | 1.29 % | 2.19 % | 3.48 % |
| 1.00 | 3.8 % | 13.5 % | 29.2 % |

**Magnitude.** n_z does not affect the delay at all (F2) but tilts the magnitude. **n_z = n/2** makes the passband
arithmetically symmetric. For n = 4, b = 0.1:

- n_z = 2 gives −3.02 / −3.02 dB at ω₀ ∓ B/2.
- n_z = 4 gives −3.98 / −2.24 dB.
- At b = 0.5 with n_z = 2, the −3 dB band is [0.764, 1.243]·ω₀ vs the ideal [0.75, 1.25].
- For odd n, n_z = (n ± 1)/2 leaves about ±0.2 dB of tilt at b = 0.1.

The far skirts are asymmetric: about 20·n_z dB/dec below the passband and 20·(2n − n_z) dB/dec above it.

**Realization.** Every pole pair becomes a standard biquad with ω₀ₖ = |p′ₖ| and Qₖ = |p′ₖ|/(2|Re p′ₖ|). For
narrow bands, Qₖ ≈ ω₀/(B·|Re pₖ|), which to first order is the same as the reactance transform gives. The n_z origin zeros are assigned as one s per BP
section, and the remaining sections are LP-type, or s² for HP-type sections. No new hardware is needed, and the
poles are always stable. This is a new transform in `filter_engine.py`; its output then goes through the existing
pairing.

**Recommendation.** v1 keeps the reactance-transform BP for both responses, but evaluates the actual BP delay
variation over the passband (never reuse the LP numbers) and warns when b·Ω_d exceeds the user's tolerance. As a
follow-up (or inside FS-006 if the effort allows), add "BP (delay-preserving, arithmetic)" by pole translation.
It is the right default for these two responses when b ≲ 0.3. For BP order selection with a delay criterion,
build each candidate BP and measure its delay over the requested band inside the n-loop. There is no closed form
once the transform tilts the delay.

### 4.4 BR

With Ω = Bω/(ω₀² − ω²), dΩ/dω = B(ω₀² + ω²)/(ω₀² − ω²)². Near DC the delay is only τ_LP(0)·B/ω₀², and it rises
steeply toward the stop band, about 50× at its edges. For n = 4, b = 0.2:

| ω/ω₀ | 0 | 0.3 | 0.6 | 0.8 | 0.85 | 0.9 | 1.1 | 1.2 | 1.5 | 3.0 |
|---|---|---|---|---|---|---|---|---|---|---|
| τ/τ(0) | 1.00 | 1.32 | 3.32 | 12.7 | 22.4 | 49.5 | 48.9 | 12.6 | 2.08 | 0.16 |
| \|H\| dB | 0.0 | −0.0 | −0.1 | −0.6 | −1.1 | −2.7 | −3.3 | −0.8 | −0.2 | −0.0 |

Neither passband has anything like flat delay, and the upper passband behaves like an HP (τ → 0). A linear-phase
band-stop needs a delay-complementary structure (H = delay − BP), which is not a transform of an all-pole
prototype.

**Recommendation.** Do not offer BR for Bessel or equiripple delay. At most, offer it as "magnitude-only; delay
not preserved".

### 4.5 Asymmetric BP / BR

- **Zero redistribution** (poles fixed; n_z zeros at the origin and 2n − n_z at infinity) is *exactly*
  delay-neutral. The measured change in τ is 0 to machine precision for n_z = 0…8, n = 4. Only the magnitude tilts.
  - Translation BP, n = 4, b = 0.1, gains at ω₀ ∓ B/2:
    n_z = 0 → −2.13 / −3.87 dB; 2 → −3.02 / −3.02; 4 → −3.91 / −2.17; 6 → −4.80 / −1.33; 8 → −5.70 / −0.48 dB.
  - For a reactance-transform BP, each zero moved away from n_z = n tilts the band edges by about
    20·log₁₀(1 ± b/2) dB (0.42 dB per zero at b = 0.1).
  - So asymmetric skirts are compatible with flat delay. Only the passband magnitude tilt limits them, and that
    tilt grows with b·|n_z − n_z,sym|.
- **Asymmetric transmission zeros** (different lower and upper stopband notches, or split BR zeros) lie on the jω
  axis and are also delay-neutral. The costs are the magnitude effects of §5.1.
- **Wideband BP as an LP(f_L) × HP(f_H) cascade.** τ = τ_LP + τ_HP, with τ_HP(f) ≈ W₃(n_H)·f_H/(2π·f²). The
  relative extra delay is (W₃(n_H)/W₃(n_L))·f_H·f_L/f², so the delay is within ε only for
  f ≳ √(f_H·f_L·W₃(n_H)/(W₃(n_L)·ε)).
  - LP n = 4 at 10 with HP n = 2 at 1: τ/τ_LP(0) = 3.70 / 2.58 / 1.71 / 1.26 at f = 1.5 / 2 / 3 / 5. No ±1 %
    region exists at all.
  - LP at 100 with HP at 1: within ±1 % only from 73 up to the LP's own 1 % edge at 91.
  - So a wideband "Bessel BP" is flat only near its top. Say so in the UI if LP+HP cascades are offered.

---

## 5. Manual notches / arbitrary transmission zeros

### 5.1 Stopband jω-axis zeros keep the delay exactly

Consider H(s) = H_B(s)·∏ᵢ(1 + s²/ω_zi²), where H_B is Bessel or equiripple delay. By F2 its group delay equals
that of H_B exactly (verified: max|Δτ| = 0). **So a notch preserving the linear-phase passband is possible, provided
the zeros are placed in the stopband.** The costs are all in the magnitude:

- **Passband droop:** |H| = |H_B|·∏|1 − ω²/ω_zi²|.
  - Keeping −3 dB at f_c is feasible only if ∏(1 − 1/Ω_zi²) > 1/√2. For a single pair, that means **Ω_z > 1.848**.
  - Without correction (Bessel n = 4), the gain at f_c and the new −3 dB point are:

    | Ω_z | 1.5 | 2.2 | 2.7 | 3.5 | 5 |
    |---|---|---|---|---|---|
    | gain at f_c | −8.12 dB | −5.02 dB | −4.29 dB | −3.75 dB | −3.37 dB |
    | new −3 dB point | 0.650·f_c | 0.790·f_c | 0.848·f_c | 0.903·f_c | 0.950·f_c |
- **Restoring −3 dB at f_c:** scale **only the poles** by a (a 1-D root find), keeping the zeros fixed. The delay
  shape stays maximally flat or equiripple, while τ₀ shrinks by 1/a and the flat band widens by a (F3). Bessel n = 4
  (plain filter: 13.41 dB at 2·f_c, 25.09 dB at 3·f_c):

  | Ω_z | a | A at 2·f_c | A at 3·f_c |
  |---|---|---|---|
  | 2.0 | 2.34 | ∞ (the zero sits there) | only 3.3 dB |
  | 2.2 | 1.69 | 19.6 dB | 12.0 dB |
  | 2.7 | 1.30 | 14.8 dB | 29.9 dB |
  | 3.5 | 1.14 | 13.8 dB | 32.6 dB |
  | 5 | 1.06 | 13.5 dB | 27.2 dB |

  So jω zeros are a tool for removing a *known interferer* (a clock, a carrier, a mains harmonic) at no delay cost.
  They are not a route to elliptic-like selectivity: close to f_c, the droop fight gives the attenuation back.
- **Roll-off:** each zero pair removes 40 dB/dec of ultimate slope, leaving 20·(n − 2m) dB/dec. For an LP to keep
  rolling off, 2m ≤ n − 1; 2m = n gives a constant HF floor.
- **Realization:** each zero pair must share a section with a complex pole pair (an LPN/HPN notch biquad), so
  m ≤ ⌊n/2⌋. The Bessel pole Q is low (≤ 1.42 for n ≤ 10). With ω_z above the paired section's ω₀ these are
  ordinary LPN sections; otherwise they are HPN sections. The existing notch pairing decides.
- **Implementation rule:** do **not** re-synthesize the poles after the user adds zeros, as elliptic or
  inverse-Chebyshev flows do. The poles come from the delay spec only. Zeros are appended, then the pole rescale for
  −3 dB is optional and the gain is normalized at DC.

### 5.2 Magnitude equalization without touching the delay (non-minimum-phase)

Real zero pairs ±σ, i.e. (1 − s²/σ²), give |N| = 1 + ω²/σ². They lift the passband and still add zero delay. For
Bessel n = 4, σ = 1.2516 (matching the ω² term of |H|²) changes the gain at 0.5 / 1 / 1.5·f_c from
−0.71 / −3.01 / −7.42 dB to +0.58 / +1.28 / +0.31 dB, with exactly the same delay. Combined with a jω pair at the same
radius, N = 1 − ω⁴/ω_z⁴, so the droop becomes 4th-order only.

This is the classical "poles for delay, even numerator for magnitude" linear-phase-selective design. But it needs
RHP zeros, i.e. sections with non-minimum-phase numerators (feed-forward or summing biquads). It is **out of
scope** unless the hardware stage supports such sections. Record it as a possible future item.

### 5.3 A passband notch cannot be linear-phase

A notch biquad (s² + ω_n²)/(s² + (ω_n/Q)s + ω_n²) inside the passband behaves as follows:

- its zeros add no delay, but its pole pair adds a bump of **area exactly π** (F4; numerically 3.141593 for every
  Q), whatever the Q;
- the peak is ≈ 2Q/ω_n and the width ≈ ω_n/Q;
- the tail at a distance Δ from ω_n is ≈ ω_n/(2QΔ²) in delay and ≈ ω_n/(2QΔ) rad in phase.

With Bessel n = 4 and ω_n = 0.5·ω_c:

| Q | 2 | 5 | 10 | 30 |
|---|---|---|---|---|
| bump peak | 3.85·τ₀ | 9.48·τ₀ | 18.9·τ₀ | 56.8·τ₀ |
| band where delay error > 1 % | whole 0…f_c | whole 0…f_c | whole 0…f_c | 0…0.94·f_c |

Keeping the error below ε outside ±Δ needs Q ≳ ω_n/(2εΔ²τ₀). For ε = 1 %, Δ = 0.1·ω_c, that is Q ≈ 1200, with
ringing time ~Q/(π·f_n). Equalizing the bump with all-passes is impossible in the "cancel" sense, because all-pass
delay is positive everywhere; you can only raise the rest of the band to the bump's level, which costs many orders
and a lot of added delay. This is the Bode gain–phase relation in action: a minimum-phase magnitude dip always
carries a phase signature.

If the tool's "manual zeros" are bare zeros with no dedicated poles, a pair inside the passband is worse still:
|1 − ω²/ω_z²| is not a notch but a broad hole that then rises as ω².

**UI rule.** For Bessel and equiripple delay, accept manual zeros only at Ω_z > 1. When −3 dB normalization is on,
also enforce the droop-feasibility test, with Ω_z > 1.848 for a single pair. Report the droop, the new −3 dB point
and the unchanged τ₀. Reject passband zeros, or allow them with an explicit warning showing the computed delay
error.

---

## 6. Implementation plan (for the coding session)

### 6.1 Pipeline

```
spec ─► order (scan n) ─► LP prototype poles, delay-normalized (Bessel: θn roots | EqDelay: seeded Remez)
     ─► normalization (−3 dB: ×1/W3(n[,δ]);  delay mode: ×1/τ0)
     ─► transform: LP | HP (mirror) | BP reactance | [BP translation, new] | BR disabled
     ─► optional user zeros appended (stopband jω only; poles NOT re-synthesized; optional pole rescale)
     ─► existing pairing and §2 classification ─► hardware stage (unchanged)
```

### 6.2 `filter_solvers.py` (port from `fs006_reference.py`)

- `bessel_poles(n, norm)`, `bessel_theta_coeffs`, `bessel_theta_eval` (recurrence), `bessel_delay_error(n, x)`
  (closed form), `bessel_w3db_delay(n)`, `bessel_atten_db(n, Ω)`.
- `eqdelay_poles(n, delta)` → (poles with τ_nom = 1, ω_p). This includes `EQDELAY_SEEDS_1PCT` and the Remez core
  (`_newton`, `_move_extrema`, `_remez`, `_jac`, `_tau_derivs`). **Cache** by (n, δ), since the order scan calls
  it for every n.
- `w3db(poles)`: the first −3.0103 dB crossing from DC, which matters because equiripple can peak slightly.
- `bessel_order(spec)`, `eqdelay_order(spec)`: return (n, None) or (None, (best_A, n_at_best)) for the
  infeasibility message.

### 6.3 Spec fields (`app.py`, `ui_components.py`)

| | Bessel | Equiripple delay |
|---|---|---|
| Frequency | f_c (−3 dB) [default] or τ₀ (LP only) | f_c (−3 dB) [default] or τ_nom (LP only) |
| Shape parameter | — | delay ripple ±δ % (0.05…10) |
| Order | manual, or auto from: delay tolerance ε % up to f_d, and/or A_s at f_s | manual, or auto from f_d and/or (f_s, A_s) |
| Passband ripple / stopband ripple | hidden | hidden |
| Results to show | τ₀, frequency where delay error = 1 %, A at f_s, section Q max | τ_nom, τ(0), band edge f_p, A at f_s, section Q max |
| HP | magnitude-mirror; delay fields hidden | same (or hide the response) |
| BP | allowed; post-check the delay, warn on tilt; [translation option] | same |
| BR | disabled (or "magnitude-only") | disabled |
| Manual zeros | stopband only; no pole re-synthesis | same |

Delay plot hints (`plot_utils.py`): draw τ₀ or τ_nom, plus the ±δ band (equiripple) or the −ε line (Bessel), up to
f_d or f_p. For BP, centre the plot on f₀.

### 6.4 Validation tests (numbers from this note)

1. Bessel poles n = 1…10 match §2.7 to the printed 8 digits (the reference passes at ≤ 4·10⁻⁸ against those
   rounded values, and ≤ 3·10⁻¹² against 60-digit references and SciPy).
2. Delay identity: group delay minus (1 − ε_n(x)) is ≤ 10⁻⁹. τ(0) = 1 in delay norm; |H(j1)| = −3.0103 dB in mag norm.
3. W₃(n) table of §2.4 to 10 digits.
4. Flatness vs order: the error at the f/f_c points of §2.6 equals ε (e.g. n = 4 at 0.915·f_c → 1 %), and increasing
   n at fixed Ω_d never increases the error.
5. Equiripple for n = 2…12 and δ ∈ {0.1 %, 1 %, 5 %}:
   - max|τ − 1| on [0, ω_p] equals δ within 10⁻⁶;
   - exactly n + 1 alternating extrema;
   - τ(0) = 1 + δ for odd n and 1 − δ for even n;
   - ω_p equals §3.5 and §3.7 (e.g. n = 4, ±1 %: 3.267570).
6. Order selection (n_max = 12):
   - Bessel (1 kHz; delay error ≤ 1 % to 900 Hz) → 4.
   - Bessel (1 kHz; 40 dB at 5 kHz) → 4.
   - Bessel (1 kHz; 40 dB at 3 kHz) → infeasible, best 34.1 dB at n = 10.
   - EqDelay (1 kHz; ±1 % to 1.4 kHz) → 4.
   - EqDelay (±1 %; 40 dB at 3 kHz) → infeasible, best 36.4 dB at n = 8.
7. End-to-end LP, Bessel n = 4, f_c = 1 kHz: τ₀ = 336.4 µs; sections 1430.2 Hz / Q 0.5219 and 1603.4 Hz / Q 0.8055;
   Resulting Response delay at 900 Hz within 1 % of τ₀; −3.01 dB at 1 kHz.
8. Adding a jω zero pair at 3·f_c leaves the group delay unchanged: exactly zero analytically, and ≤ 10⁻⁹ if the
   delay is taken by numerical phase differentiation.
9. BP n = 4, b = 0.1: reactance transform delay p-p over the mapped 1 % band ≈ 9.1 % (a warning fires);
   translation ≈ 1.01 %.
10. HP n = 4: τ_HP(2·f_c)/τ_LP(0) = 0.250.

### 6.5 Edge cases

- n = 1: Bessel and equiripple are both a single real pole. Allow it, or require n ≥ 2 for equiripple.
- Equiripple δ outside the tested range, 0.05 %…20 %: reject in the UI.
- Magnitude peaking in equiripple means W₃ must come from the first crossing, and "A at f_s" can be slightly
  negative close to f_c.
- The minimum Bessel section Q approaches 0.5 (0.504 at n = 10); keep the pairing robust for Q ≈ 0.5.
- Odd n has a real pole: a first-order section, or combine it with a pair as the existing pairing does.

---

## 7. References

- W. E. Thomson, "Delay networks having maximally flat frequency characteristics", Proc. IEE Part III, 96, 1949.
- L. Storch, "Synthesis of constant-time-delay ladder networks using Bessel polynomials", Proc. IRE, 42, 1954.
- E. Ulbrich, H. Piloty, AEÜ 14, 1960: all-pole low-pass, all-pass and band-pass design with Chebyshev
  (equiripple) approximation of constant group delay.
- A. I. Zverev, *Handbook of Filter Synthesis*, Wiley, 1967 (Bessel and "linear phase with equiripple error" tables).
- A. B. Williams, F. J. Taylor, *Electronic Filter Design Handbook*, McGraw-Hill (Bessel and linear-phase tables).
- SciPy `scipy.signal.besselap` (normalizations 'phase' / 'delay' / 'mag'; Aberth–Ehrlich root finding with
  asymptotic starting values).

## Appendix — companion files

Both live in `dev/fs006/` (moved there from the original chat hand-off; production code is the
FS-006 port, see `dev/ROADMAP.md`). Run them from that folder.

- `fs006_reference.py`: numpy-only reference with the Bessel functions, the equiripple solver with embedded seeds
  for n = 2…15, order selection and `_selftest()`. Running it prints the checks in §6.4 items 1, 2, 5 and 6.
- `eqdelay_mp.py`: offline mpmath generator for the seeds (needs `mpmath` and imports `fs006_reference.py`). Only
  needed to extend n or regenerate. Run `python eqdelay_mp.py 15`, which writes `eqdelay_seed_table.json` with
  8 δ values per order. `load_seed_json()` in the reference module reads it if denser seeds are ever wanted.
