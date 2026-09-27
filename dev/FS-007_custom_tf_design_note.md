# FS-007 — Custom H(s): input forms, normalization, filter types — design note

Purpose: the reasoning and the build plan for FS-007. The user types a transfer function directly
instead of choosing an approximation, and then continues through Biquad Pairing, Topology and
Resulting Response exactly as for a synthesized filter. The note is written for a coding session
that has the project open. It records the maintainer's decisions (2026-09-27), the math, the
numerical traps, measured on a scratch implementation, and every place in the app that has to learn
about the new mode.

Conventions:
- H(s) = k·∏(s − zᵢ)/∏(s − pⱼ), with roots in rad/s.
- n_p = number of poles, n_z = number of finite zeros, n₀ = zeros at the origin. The n_p − n_z
  zeros at infinity are implicit.
- A *transmission zero* is a zero on the jω axis, the origin included; zeros at ∞ count too.
- ω_n is a normalization frequency; s_n = s/ω_n.
- "Peak" means max over ω of |H(jω)|.
- Line numbers refer to HEAD `544047b`. Re-grep them before editing.

Measurements: numpy 2.3.5 and scipy 1.17.1, scratch scripts, not committed. The `check_custom_tf.py`
planned in §11 turns them into asserts.

---

## 0. Decisions and answers (summary)

| Question | Answer (section) |
|---|---|
| Where does the input live? | **Sidebar:** Response option **"Custom H(s)"** holds the design intent: mode, filter type, target frequencies, gain mode, α / A_s. **Main canvas:** an **editor panel at the top of Response Plots** holds the wide tables, paste boxes and diagnostics. The panel renders *before* the engine-run section, so its values feed the same script run (§9). *Maintainer decision.* |
| Input forms | Four forms, one internal representation (normalized zpk + ω_n), §3. (1) Polynomial coefficients N(s), D(s), entered as a table by power or pasted with an ascending/descending toggle. (2) Factored (f₀, Q) rows for poles and zeros, plus real roots, an origin-zero count and a gain. (3) Factored Tietze–Schenk rows 1 + aᵢs + bᵢs² with A₀, the most common handbook table form. (4) Root coordinates (σ, ω ≥ 0), where a row with ω > 0 is a conjugate pair. |
| Which zeros? | **Transmission zeros only** (origin, jω pairs, ∞). Every other zero is rejected with a message that names it. The formats still **carry** off-axis and RHP zeros (σ > 0, Q_z < 0), so FS-013 (all-pass) and FS-014 only have to open the gate. No format change is needed (§3.5, §12). |
| Normalized input | A radio chooses *Normalized (s_n = s/ω_n, ω_n = 2π·f_n)* or *Absolute*. Denormalization is z = ω_n·z_n, p = ω_n·p_n, k = K·ω_n^(n_p−n_z). Handbooks disagree on what "ω = 1" means, so the panel shows the measured \|H(j1)\| and f₋₃dB/f_n instead of guessing (§2). |
| LP only, or other types? | **Both modes.** **Lowpass prototype:** a normalized LP mapped to LP/HP/BP/BR at the sidebar frequencies. The mapping uses `scipy.signal.lp2{lp,hp,bp,bs}_zpk`, which carry k exactly and are already a dependency. **Complete H(s):** the entered H is the final filter (§5). |
| Type in complete mode | The **Filter Type radio decides.** It picks the pairing router, plot window, labels, report and `hw_filter_type`. The tool detects the type from \|H(0)\|, \|H(∞)\| and the peak, and **warns** on a mismatch (§5.3). *Maintainer decision.* |
| Passband edges in complete mode | Measured as the outermost −α crossings (α is the sidebar value). They are written into `f1_val` / `f2_val` by a resolution step placed **before** `real_fc` (app.py L441). Plots, probes, Tab 2 and the report then work unchanged (§5.3, §9.3). |
| Gain | A radio: **Normalize** (peak = 0 dB, then the sidebar Passband Gain) or **As entered** (the peak gain G of the entered H becomes `final_gain_units`, and may be < 1). A negative K is used as \|K\| with a warning, because sign is a realization property (§6). *Maintainer decision.* |
| Pairing / topology | **Unchanged.** Custom is a new *producer* of `engine_results` and does not change `pairing_utils`. A **realizability gate** rejects every root pattern that today's pairers or cells would mishandle silently. A **pairing pre-flight** warns when auto-pairing yields a section the cells would realize wrongly (§7). |
| Numerics | Balanced `np.roots` for the denominator. The numerator uses the even-part method (odd part ≈ 0 ⇔ zeros symmetric about the jω axis; x = s²; x real < 0 ⇔ jω zeros). Repeated roots are merged into centroids by a precision-aware backward-error test. Clean values are snapped exactly. A conditioning diagnostic warns when the typed precision does not pin the response down (§4). |
| Order limits | Complete mode: n_p ≤ 30, the largest standard design (Butterworth BP 15 + 15). Prototype mode: n ≤ 20 for LP/HP and n ≤ 15 for BP/BR. The coefficient form is limited in practice by conditioning, not by a fixed order (§4.4). |

---

## 1. What "Custom" is — and is not

Custom is a **Tier-A producer of `engine_results`** plus Tier-D input, gating and report rows.
Everything downstream of `engine_results` / `hw_sections` stays as it is. That means
`pairing_utils` (all routers), `classify_section`, the dispatch gate, cells, solvers, scoring,
`topology_tab`, `response_tab`, `hw_plots`, `report_pdf`, the TF cache and `verify.py`.

This works because of how the data is split today:
- Tabs 4 and 5 and the PDF writer never read the spec. They read `hw_sections`,
  `hw_filter_type`, `report_*` and the op-amp/MC keys from session_state.
- Tabs 1–3 read `engine_results` (`poles`, `zeros`, `k`, optional stopband and notch keys) plus a
  small set of sidebar variables: `filter_type`, `f1_val`, `f2_val`, `real_fc`, orders, α, A_s and
  the gain.
- FS-006 already showed the pattern: a *resolution step* rewrites those variables before anything
  consumes them, and new response names fall through the existing `response in [...]` branches
  safely.

"Pairing unaffected" has a price. The pairers and cells understand only origin zeros, jω zero
pairs and zeros at ∞, together with exact conjugate pairs. `build_stage_bricks` /
`categorize_roots` handle anything else **silently and wrongly** (§7.1). So the Custom path must
guarantee that it only ever emits root sets of the synthesized kind, or refuse with a message.
That guarantee is the main engineering content of this item.

---

## 2. Canonical representation and normalization

### 2.1 One internal form

Every input form is parsed to a **normalized zpk**: (z_n, p_n, K), plus ω_n [rad/s]. The
absolute scale is the same path with ω_n = 1. With s_n = s/ω_n:

$$H(s) = K\,\frac{\prod (s_n - z_{n,i})}{\prod (s_n - p_{n,j})}
       = \underbrace{K\,\omega_n^{\,n_p-n_z}}_{k}\;\frac{\prod (s - \omega_n z_{n,i})}{\prod (s - \omega_n p_{n,j})}.$$

This is the same rule the engine already uses for BP/BR: `k_phys = k_norm * w0_center**degree_diff`
(`filter_engine.py` L601, L743).

For coefficients, N(s_n) = Σ bᵢ s_nⁱ gives the coefficient of sⁱ as bᵢ/ω_nⁱ. The leading constant
k = (b_m/a_n)·ω_n^(n−m) agrees with the formula above.

### 2.2 Units per form

| Form | Normalized | Absolute |
|---|---|---|
| Coefficients | dimensionless (s_n) | s in rad/s |
| (f₀, Q) / real-root frequencies | dimensionless ω/ω_n | sidebar unit (Hz, kHz, …) |
| Tietze–Schenk (aᵢ, bᵢ) | dimensionless; always normalized (it is a table form) | — |
| Roots (σ, ω) | dimensionless | rad/s. MATLAB / scipy output is rad/s; offering Hz as well is not worth a second unit radio. |

f_n is entered in the sidebar frequency unit.

### 2.3 What "normalized" means in the source table is not knowable

The tool does not guess. Handbooks normalize "ω = 1" to different points:

| Family / source | ω = 1 is |
|---|---|
| Butterworth (all tables) | −3.01 dB point |
| Chebyshev (most tables) | ripple-band edge (−α_max) |
| Bessel | often delay-normalized, τ(0) = 1. −3 dB at ω = W₃(n): 1.3617 for n = 2, … (FS-006 note §2.4). Tietze–Schenk / TI tables are −3 dB-normalized. |
| Inverse Chebyshev | often stopband-edge-normalized |
| Elliptic (Zverev, Saal) | often √(ω_p·ω_s) = 1, or ω_p = 1 |

So the panel shows, for any normalized entry:
- the measured |H(j1)| relative to the peak (in dB)
- f₋₃dB/f_n

A Bessel table entered with f_n = 1 kHz therefore visibly lands its −3 dB point at 2.11 kHz
(n = 4, delay-normalized). The user sees immediately that f_n must be the table's reference
frequency and not the desired corner. In prototype mode the same number drives α (§5.2).

---

## 3. Input forms

### 3.1 Polynomial coefficients

- A two-column table (`num`, `den`) with one row per power of s, **labelled by power**. This is
  unambiguous, whatever order the source prints.
- A paste box per polynomial, with an **order toggle**:
  - *descending* (MATLAB / numpy `[aₙ … a₀]`)
  - *ascending* (handbook a₀, a₁, …)
- Separators: comma, space, semicolon or newline. Scientific notation is allowed.
- Real coefficients only.
- Leading zeros are trimmed. An all-zero numerator is an error. D is normalized to a positive
  leading coefficient, which may leave a negative K (§6.3).
- The gain is implicit: k = b_m/a_n.

The coefficient form is convenient for small orders and for pasting from other tools. It is
**numerically fragile** for high orders and narrow bands, measured in §4.4. The panel says so
whenever the conditioning check fires.

### 3.2 Factored (f₀, Q)

Rows:
- **Pole pair** (f₀, Q), factor s² + (ω₀/Q)s + ω₀².
  - Q > 0.5 gives a complex pair.
  - Q = 0.5 gives a double real pole.
  - Q < 0.5 gives two real poles ω₀(−1/(2Q) ± √(1/(4Q²) − 1)).
  - Pairing will not recombine the two real poles into one section. That is FS-016's open
    question.
- **Real pole** f: pole at −ω.
- **Zero pair** (f_z, Q_z), factor s² + (ω_z/Q_z)s + ω_z².
  - Q_z blank or ∞ is a **jω pair** (notch / transmission zero), the only kind accepted now.
  - Finite Q_z > 0 is an off-axis LHP pair.
  - Q_z < 0 is the mirrored RHP pair. That is the all-pass convention: Q_z = −Q_pole.
  - Both finite cases are rejected until FS-013 / FS-014.
- **Real zero** σ_z, signed; positive means RHP. Rejected now.
- **Origin zeros:** an integer count n₀.
- **K:** the constant in front of the monic factors.

### 3.3 Factored, Tietze–Schenk (aᵢ, bᵢ)

The form printed by Tietze–Schenk and by TI's *Op Amps for Everyone* filter tables:
H(s_n) = A₀ / ∏(1 + aᵢ s_n + bᵢ s_n²).
- A row with bᵢ > 0 is a pole pair: ω₀ = 1/√bᵢ, Q = √bᵢ/aᵢ.
- A row with bᵢ = 0 is a real pole at −1/aᵢ.
- A₀ is the DC gain, so K = A₀ / (∏bᵢ · ∏a_real).
- Always normalized.
- Check: Butterworth n = 4 tabulates a = 1.8478, 0.7654 (b = 1), giving Q = 0.5412, 1.3066,
  which equals §11 case 1.
- These tables are all-pole. Zero rows, if needed, use the (f_z, Q_z) rows of §3.2.

### 3.4 Root coordinates (σ, ω)

- Separate pole and zero tables.
- Each row is (Re, Im) with **Im ≥ 0**:
  - Im > 0 is a conjugate pair.
  - Im = 0 is one real root.
  - A negative Im is read as its magnitude.
- Pasted full lists (both conjugates present, e.g. `roots(den)` output) are accepted and
  de-duplicated.
- A lone complex root with no conjugate inside the tolerance of §4.5 is an **error**. Today
  `categorize_roots` would silently invent a conjugate for it (`pairing_utils.py` L46-47).
- K is the leading constant.

### 3.5 Serializable input: `custom_spec`

The panel's state is one JSON-serializable dict. It is the unit that FS-011 will save and that
FS-012 will drive headlessly:

```text
custom_spec = {version: 1, form: "coeff"|"f0q"|"ts"|"roots",
               scale: "normalized"|"absolute", f_norm: {value, unit},
               coeff: {num: [...], den: [...], order: "desc"|"asc", digits: {...}},
               f0q:   {pole_pairs: [{f0, Q}], real_poles: [f], zero_pairs: [{fz, Qz}],
                       real_zeros: [sigma], n_origin: int, K},
               ts:    {stages: [{a, b}], A0},
               roots: {poles: [[re, im]], zeros: [[re, im]], K}}
```

- Only the block of the active form is used. The other blocks are kept, so switching forms back
  and forth loses nothing.
- `digits` records the significant digits the user typed per coefficient (§4.3).
- Mode (complete / prototype), filter type, target frequencies, gain mode and α are **sidebar**
  values. They are passed next to the spec, not inside it.

---

## 4. Numerics

### 4.1 Denominator

`np.roots` on **balanced** coefficients: substitute s = ŝ·(a₀/aₙ)^(1/n) so that the end
coefficients are 1, then scale the roots back. In absolute rad/s at 10 kHz and n = 8,
a₀/a₈ ≈ 10³⁸. Balancing is free and removes that scale from the companion eigenproblem.

### 4.2 Numerator: the even-part method

A numerator whose finite zeros are all transmission zeros has the form N(s) = s^n₀·E(s²).
The method:
1. Strip the trailing zero coefficients to get n₀. The tolerance is relative to the balanced
   coefficient size.
2. **Odd-part test:** after stripping, the odd-power coefficients must be ≈ 0, relative to the
   even ones. A nonzero odd part means some zero is not mirrored about the jω axis, so the
   numerator goes to the gate as "off-axis zeros".
3. Solve E(x) = 0 with x = s², a polynomial of half the degree. Every root must be **real and
   negative**, and then ω_z = √(−x).
   - x > 0 means a ±√x real-zero pair (one RHP).
   - Complex x means a quadrantal quad.
   - Both go to the gate.

Measured on an elliptic n = 6 numerator (0.5 dB, 60 dB):
- plain `np.roots` already gives |Re z|/|z| ≤ 1.4·10⁻¹⁶
- the even-part method gives Re z = 0 exactly and ω_z within 1.6·10⁻¹⁵

Its value is therefore **structural**: jω-ness becomes a yes/no test on real coefficients, with
no tolerance on tiny real parts. It does *not* cure repeated roots; see §4.3.

### 4.3 Repeated roots — the real trap

`np.roots` splits an m-fold root into a ring of radius about ε^(1/m). The **centroid** of the
ring stays exact. Measured on (s² + 1)^m:

| m | plain `np.roots`: max \|Re z\|/\|z\| | even-part x: ring spread / \|x\| | centroid error |
|---|---|---|---|
| 2 | 6.0e-12 | 0 | 0 |
| 3 | 5.0e-06 | 6.6e-06 | 1.6e-15 |
| 4 | 8.3e-05 | 2.2e-04 | 1.3e-15 |
| 6 | 2.1e-03 | 3.4e-03 | 6.7e-16 |
| 8 | 7.4e-03 | 2.2e-02 | 4.4e-16 |
| 10 | 2.5e-02 | 5.1e-02 | 0 |

With coefficients **typed to 6 significant digits** (absolute rad/s, ω₀ = 2π·1 kHz), the typed
polynomial is no longer a perfect power. Its roots genuinely split, by 0.2 % at m = 2 and up to
59 % in x at m = 10.

Repeated jω zeros are not exotic. Every BR obtained by transforming an all-pole prototype has
(s² + ω₀²)^n in its numerator (§5.2). Without a fix, such a numerator entered as coefficients
fails the "x real negative" test and is **rejected as off-axis**.

**Fix: a precision-aware merge.**
- Agglomerate the roots (x-roots for the numerator, s-roots for the denominator), closest pair
  first.
- After each step, rebuild the polynomial from the centroids and compute its **backward error**:
  the relative coefficient mismatch against the entered polynomial.
- Accept the step while that error is ≤ τ = 4·u_in, and stop at the first rejected step.
  - u_in is the input precision: half a unit in the last significant digit the user typed,
    taken from the paste text or from the shortest `repr` of each table value.
  - For values pasted at full precision, use u_in = 10⁻¹².

Measured backward errors after merging:

| Case | merged backward error | τ at 6 digits (2·10⁻⁵) |
|---|---|---|
| (x + ω₀²)^m, m = 2…10, 6-digit coefficients | 3.8e-6 … 1.0e-5, centroid ω_z error ≤ 1.1e-6 | merge ✓ |
| exact (x + 1)^10 | 0 | merge ✓ |
| two notches 1 % apart in ω_z | 9.9e-5 | kept distinct ✓ |
| two notches 0.1 % apart | 1.0e-6 | merged |

The 0.1 % case merges because 6 typed digits cannot tell it apart from a double notch. That is
correct behaviour, and the realized response differs negligibly.

The factored and roots forms never need merging, since multiplicity is explicit. The diagnostics
recommend them whenever a merge happens.

### 4.4 Coefficient conditioning — when the coefficient form is the wrong tool

Passband deviation in dB when the denominator coefficients are rounded to d significant digits
(normalized, ω_n = 1; "!" = an RHP or jω pole appeared):

| Design | d = 4 | d = 6 | d = 9 | d = 12 |
|---|---|---|---|---|
| Butterworth LP 4 | 0.0013 | 2.2e-5 | 1.5e-8 | 1.9e-11 |
| Butterworth LP 8 | 0.026 | 1.0e-4 | 3.8e-7 | 7.8e-10 |
| Butterworth LP 15 | 3.4 | 0.0055 | 4.6e-5 | 4.0e-8 |
| Butterworth LP 20 | 8.7 ! | 0.68 | 9.1e-5 | 4.6e-7 |
| Chebyshev 0.5 dB LP 8 | 0.18 | 0.0015 | 1.5e-6 | 1.9e-9 |
| Butterworth BP 2×4, b = 0.1 | 220 ! | 0.0064 | 8.7e-4 | 8.7e-7 |
| Butterworth BP 2×5, b = 0.1 | 39 ! | 4.1 | 0.0039 | 1.3e-5 |
| Butterworth BP 2×6, b = 0.1 | 170 ! | 27 ! | 0.54 | 8.7e-4 |
| Butterworth BP 2×8, b = 0.3 | 48 ! | 17 | 0.018 | 7.5e-6 |
| Butterworth BP 2×10, b = 1.0 | 3.4 | 0.36 | 4.5e-5 | 6.5e-8 |

What this means:
- **LP/HP coefficients typed to 6 digits are fine up to n ≈ 15.**
- **Narrow-band BP/BR from expanded coefficients breaks down at 2×5–2×6 poles, even with 6–9
  digits.** Those designs must be entered factored (per biquad) or as roots, where every
  section is independent and well-conditioned.

**Diagnostic.** Coefficient form only:
1. Perturb every entered coefficient randomly by ±u_in (relative), over 8 trials.
2. Recompute the roots and the response over the measured passband.
3. Report the worst deviation. Warn above 0.1 dB, and error if any trial moves a pole into the RHP.

The message recommends the factored form. The diagnostic costs 8 root solves, milliseconds.

### 4.5 Snapping and output types

Downstream code tests root positions with **absolute** tolerances in rad/s:
- notch markers use `abs(z.real) < 1e-6` (app.py L698, plot_utils.py L150)
- `categorize_roots` uses 1e-5·max(|r|, 1)

Custom must therefore emit exactly clean values. After merging:

| Snap | Rule |
|---|---|
| jω zero | Re := 0. Always exact, since built from x < 0. |
| Origin zero | exactly 0. Built from n₀. |
| Real root | Im := 0 when \|Im\| ≤ 10⁻⁹·\|r\| |
| Conjugate pair | built as (a + jb, a − jb) from one representative |
| Complex pole | must have Re ≤ −10⁻⁹·\|p\| (gate, §7) |

Types are those downstream code depends on:
- `poles`, `zeros`: complex `np.ndarray`
- `k`: a positive Python `float` (`float(k)` at app.py L904, `val < 0` in `tf_utils.format_latex_val`)

---

## 5. Filter type: two modes

### 5.1 Why two modes

A handbook gives either a normalized **lowpass prototype**, the usual case for LP tables, or the
**final** transfer function (a BP designed elsewhere, MATLAB output, a measured-and-fitted
response). The first needs the sidebar frequencies and a frequency transformation. The second
needs neither, but its type must be named for the pairing router. One mode cannot serve both
without ambiguity, so the sidebar has a **Mode** radio: *Complete H(s)* / *Lowpass prototype*.

### 5.2 Lowpass-prototype mode

**Input.**
- Always normalized. ω = 1 is the prototype's reference edge.
- The panel hides f_n. The target comes from the existing frequency block: fc for LP/HP,
  f₁/f₂ for BP/BR, as for synthesized designs.

**Transforms.** These are `scipy.signal.lp2lp_zpk / lp2hp_zpk / lp2bp_zpk / lp2bs_zpk`. scipy is
already required (≥ 1.13), and these functions exist since 1.1. They carry k in closed form,
which the private `filter_solvers._transform_lp_to_bp/_br` helpers do not. With ω_c = 2π·fc,
ω₀ = √(ω₁ω₂) and B = ω₂ − ω₁:

| Target | Substitution | Roots | k |
|---|---|---|---|
| LP | s → s/ω_c | ω_c·z, ω_c·p | K·ω_c^(n_p−n_z) |
| HP | s → ω_c/s | ω_c/z, ω_c/p, plus (n_p−n_z) origin zeros | K·∏(−z)/∏(−p) = H_proto(0) |
| BP | s → (s² + ω₀²)/(B·s) | each r → rB/2 ± √((rB/2)² − ω₀²); plus (n_p−n_z) origin zeros | K·B^(n_p−n_z) |
| BR | s → B·s/(s² + ω₀²) | each r → (B/2)/r ± √(((B/2)/r)² − ω₀²); plus (n_p−n_z) pairs at ±jω₀ | K·∏(−z)/∏(−p) = H_proto(0) |

Derivation, for BP: each factor becomes (s_lp − r) = (s² − rBs + ω₀²)/(Bs). The quadratics
are monic, and the (Bs)^(n_z−n_p) left over gives both the origin zeros and the factor
B^(n_p−n_z). HP and BR follow in the same way from (ω_c/s − r) = −r(s − ω_c/r)/s and
(Bs/(s² + ω₀²) − r) = −r(s² − (B/r)s + ω₀²)/(s² + ω₀²).

Measured on an elliptic n = 5 prototype:
- scipy's k equals these closed forms exactly.
- H_new(s) = H_proto(mapped s) holds to ≤ 9·10⁻¹⁶ at arbitrary complex test points.
- The transformed jω zeros have Re = 0 exactly.
- scipy uses the plain quadratic formula. The small BP root of a wideband real pole is still
  accurate to 2.9·10⁻¹⁴ at f₂/f₁ = 10³, 3.5·10⁻¹³ at 10⁴ and 2.1·10⁻¹¹ at 10⁶, so no Vieta
  rewrite is needed.

**Realizability is preserved.** A jω prototype zero maps to jω zeros under all four transforms,
and a zero at ∞ maps to ∞, the origin or ±jω₀. A prototype that passes the gate yields a target
that passes it.

**Prototype rules.**
- **No origin zeros.** Such a thing is not a lowpass, and lp2hp / lp2bs would divide by zero.
  The existing `_transform_lp_to_br` also silently drops them (`filter_solvers.py`
  ~L2224, 2242-2244), which is one more reason not to reuse it.
- n_z ≤ n_p.
- If the detected type of the prototype itself is not LP, warn (§5.3).

**Symmetry.** Geometric only. Asymmetric BP/BR, or arithmetic pole translation (FS-006's delay BP),
are not prototype concepts; use complete mode for them.

**α is measured, not entered.**
- α_proto = −20·log₁₀(|H_proto(j1)| / peak). Butterworth gives 3.0103 dB, and a Chebyshev table
  gives its ripple.
- It is shown read-only in the sidebar and overrides `final_alpha` in the resolution step. The
  passband-detail plot then frames the prototype's own edge.
- It is clamped to ≥ 0.01 dB.

**Group delay.** LP → BP does not preserve flat delay (FS-006 note §4.2). If the prototype came from
a Bessel table, the panel says so in a caption; it cannot know, so the caption is generic.

### 5.3 Complete mode

**Type.** The **Filter Type radio is authoritative.** It selects `auto_pair_stages`'s router
(BP and BR have their own), the plot window (`plot_main_magnitude`), the labels, the report
passbands and `hw_filter_type` (the Topology overall readout).

**Detection, a hint only.** Compute g₀ = |H(0)|/peak and g∞ = |H(∞)|/peak.
- g₀ = 0 when n₀ > 0.
- g∞ = 0 when n_p > n_z, and k/peak otherwise.
- "Pass-level" means within α + 1 dB of the peak.
- "Stop-level" means at least max(10 dB, α + 3 dB) below it.

| g₀ | g∞ | interior | detected |
|---|---|---|---|
| pass | stop | — | Lowpass |
| stop | pass | — | Highpass |
| stop | stop | peak inside | Bandpass |
| pass | pass | a minimum at stop-level | Band-Reject |
| anything else | | | "other", e.g. shelving or equalizer shapes |

The thresholds are **relative to the peak**, because even-order elliptic and inverse-Chebyshev LPs
have a finite |H(∞)| floor (and the HP mirror a finite |H(0)|). A mismatch with the radio is a
warning, not an error. The user may deliberately route an odd shape through, say, the LP/HP router.

**Passband edges.** Measured at level L = peak·10^(−α/20), where α is the sidebar value, clamped
≥ 0.01 dB, and here *means* "edge level":

| Type | Edges |
|---|---|
| LP | f₁ = sup{f : \|H\| ≥ L} |
| HP | f₁ = inf{f : \|H\| ≥ L} |
| BP | f₁ = inf, f₂ = sup |
| BR | f₁ = sup below the dip, f₂ = inf above it |

- These are the **outermost** crossings. A ripple valley that only touches −α therefore never
  counts as an edge, and an α smaller than the true ripple still gives the band edge rather than
  the first valley.
- Search: a log grid over [min|root|/100, max|root|·100] with about 20k points, then a `brentq`
  refinement on the bracketing pair.
- No crossing, or a BR dip shallower than α, is an **error**: "the response never falls α below
  its peak — not a LP/HP/BP/BR shape at this α".

**What the resolution step writes.**
- `f1_val` / `f2_val`: the measured edges, in UI units.
- Orders: for LP/HP, `final_lp_order` = `final_hp_order` = n_p; for BP/BR, n_p − ⌊n_p/2⌋ and ⌊n_p/2⌋.
  They are display-only, because their consumers are gated or replaced (§9.5).

Tab 2 and the report normalize by `custom_info['w_n']`: 2π·f_n in complete-normalized mode, so
the round trip shows exactly what was typed, and otherwise the per-type rule.

---

## 6. Gain

### 6.1 Normalize (default)

The engine k is set so that the **peak is 1**. This is the engine's own convention: synthesized
LP/HP/BP/BR all normalize their passband maximum to 0 dB, and it is also the cumulative-peak
rule in `compute_stage_gains`. The sidebar Passband Gain (V/V ≥ 1) then applies as today.

The panel shows the entered peak gain G and its frequency for reference.

Peak search: the edge grid of §5.3, plus DC and ∞ where they are finite, refined with a bounded
scalar maximization around the best grid point.

### 6.2 As entered

- K is split as k_engine·G, with k_engine peak-normalized and G = the entered peak |H|.
- G becomes `final_gain_units`. The sidebar gain widget is replaced by a read-only caption.
- **G must never be written into `_mem_gain`.** The widget displays `max(1.0, _mem_gain)` but
  returns `_mem_gain` (`ui_components.py` L317, L322), so a later standard design would silently
  inherit a G < 1.

**G < 1 (an attenuating passband).** Mathematically everything handles it:
`compute_stage_gains` (no clamp, `pairing_utils.py` L537), the Tab 3 splits (guarded by
`k_remainder > 0`), and the plots and report (negative dB). Realization is the constraint.
Sub-unity sections route to attenuator cells. A **3rd-order VCVS LP cannot go below 1**
(`topology_tab.py` ~L1666-1668, "3rd-order is ≥ 1"), so with "distribute evenly" some VCVS
sections may be unsolvable while MFB and AM still work. This gets a warning.

**Required one-line fix.** The Topology overall readout takes its target gain from `hw_pb_gain`,
which is **never written anywhere**, and falls back to `_mem_gain` (`topology_tab.py` L2019-2020).
Set `st.session_state.hw_pb_gain = final_gain_units` next to `hw_filter_type` (app.py L1834), for
**every** design. It is harmless for standard designs, where the two are equal, and it stops a
Custom G from being misreported.

### 6.3 Sign

- A negative K (e.g. from a textbook's inverting MFB formula) or a negative leading-coefficient
  ratio is used as |K|, with a warning.
- The cascade sign is a realization property set by the cells (CONTRACTS §5). `classify_section`
  never decides it, and `engine_results['k']` is positive by convention.

### 6.4 BR specifics

- "Equalize DC and HF gains" (app.py L1723-1728, L104-129) makes ∏H(0) = ∏H(∞) = target only
  when ∏ρ = 1, i.e. |H(0)| = |H(∞)|. For an asymmetric custom BR it would give only the
  geometric mean. **Hide it** for Custom when |H(0)| and |H(∞)| differ by more than 0.01 dB.
- The Topology BR readout assumes equal gains in both passbands, so one of them is misreported
  for an asymmetric BR. This is a known limitation, recorded for FS-015. Not fixed here.

---

## 7. Realizability gate and pairing pre-flight

### 7.1 Gate table

Each row names what the current code does if the pattern slipped through. That is the reason the
row exists.

| Condition | Today, if let through | FS-007 |
|---|---|---|
| Parse error, NaN, empty table, all-zero numerator | — | **error** |
| Improper, n_z > n_p | surplus zeros; no cell has a numerator of higher degree | **error** |
| n_p = 0, or above the §0 limits | — | **error** |
| Pole on the jω axis or at the origin, or any RHP pole (Re p > −10⁻⁹·\|p\|) | origin poles are dropped by `build_stage_bricks` (L55); a real RHP pole gets w0 = \|p\| with its sign lost; a complex RHP pole gets Q < 0 | **error** |
| Lone complex root (roots form / paste) | `categorize_roots` invents a conjugate (L46-47) | **error** |
| Real zero ≠ 0 (LHP or RHP) | **silently dropped** by `build_stage_bricks` (L54); "Pairing Incomplete" counts bricks only, so the cascade ≠ H with no message | **error** → FS-014 (LHP) / FS-013 (RHP) |
| Off-axis complex zero pair, LHP | a 'Complex Pair' zero brick with w0 = \|z\|, then treated as a jω notch (Section keeps only `fz_hz`) | **error** → FS-014 |
| RHP complex zero pair | same as above (negative Q ignored) | **error** → FS-013 |
| More jω zero pairs than complex pole pairs | a jω pair needs a 2nd-order section; real-pole stages have capacity 1, so leftovers → "Pairing Incomplete" and the gain section is blocked | **error**, naming the counts (combining two real poles deliberately is FS-016's question) |
| Filter Type BP with ≥ 2 real poles | `auto_pair_bandpass` keeps `real_poles[0]` only (`pairing_utils.py` L140); **the others vanish from the cascade** | **error** |
| Filter Type BR with ≥ 2 real poles | the BR pairer merges p₁, p₂ into one stage; `hw_sections` then takes `f0_hz` from p₁ alone but Q = q_eff (app.py L1826), so the solver realizes s² + (p₁/q_eff)s + p₁² instead of (s + p₁)(s + p₂); `_stage_rho` has the same ω₀ (by code reading; to confirm under FS-016) | **error** |
| Near pole/zero cancellation (\|z − p\| < 10⁻⁶·\|p\|, only possible with very high-Q poles) | a wasted section | warning |
| Pole Q > 100 | legal; high sensitivity downstream | warning |
| Conditioning (§4.4) | — | warning (error if a trial crosses into the RHP) |
| Detected type ≠ Filter Type radio | a different pairing router than intended | warning |
| G < 1 in As-entered mode (§6.2) | some VCVS sections unsolvable | warning |

Errors are shown in the panel, and the run stops (`st.stop()`) **after** the panel has rendered,
so the user can fix the input.

### 7.2 Pairing pre-flight

This is a warning, shown in the panel. It checks the *result* of pairing, not the algorithm, so it
stays valid when FS-016 changes the routers.

1. Build bricks and run `auto_pair_stages(filter_type=…)` twice: once with absorb off and once
   with absorb on. These are read-only calls into Tier A.
2. For every stage, flag it if:
   - it has more zeros than its order, or
   - it has a jω pair *and* origin zeros, other than the 3rd-order HPn case (2nd-order LPn /
     notch numerators are s² + ω_z²; only 3rd-order HPn has (s² + ω_z²)·s).
3. Example this catches: the BP pairer dumps leftover origin zeros into the lowest stage even when
   that stage already holds a jω pair (`pairing_utils.py` L151-162). `classify_section` then
   ignores the origin zero (L552-556), and the hardware is wrong with no error.
4. The message suggests manual re-pairing in the Biquad Pairing tab, which remains available.

---

## 8. `engine_results` contract for Custom

```text
engine_results = {
  poles: complex ndarray (rad/s), zeros: complex ndarray (rad/s), k: float > 0 (peak-normalized),
  custom_info: {
    mode: "complete"|"prototype", form, scale, f_norm_hz|None, w_n (rad/s, Tab 2 / report),
    target: filter_type, detected: "Lowpass"|"Highpass"|"Bandpass"|"Band-Reject"|"other",
    n_poles, n_zeros, n_origin_zeros, n_jw_pairs, n_real_poles,
    edges_hz: (f1, f2|None), alpha_db, proto_edge_db|None,
    peak_gain (entered, V/V), peak_hz, h0_db|None, hinf_db|None (relative to peak),
    gain_mode: "normalize"|"as_entered", k_sign: +1|-1,
    conditioning_db|None, n_merged_clusters, warnings: [str], preflight: [str],
    roots_sig: str }
}
```

- **Stopband keys** (`f_stop_*`, `sb_status*`, `ideal_notches_*`, `actual_as_db`) are
  **omitted**. `plot_utils` reads them with `.get` behind `if fs_hz:` guards, so absence is safe.
  The app code that indexes them directly (L1028, L1045-1046, L1069-1070) is gated off (§9.5).
- `reflection_zeros` is omitted; nothing outside the engine reads it.
- `delay_info` is omitted, so the delay UI stays off.

---

## 9. UI and app flow (Tier D)

### 9.1 Sidebar with Response = "Custom H(s)"

| Block | Custom behaviour |
|---|---|
| Response radio (app.py L237) | adds "Custom H(s)"; `is_custom = response == CUSTOM` |
| Filter Type (`draw_filter_type`) | all four types; nothing to change, since the non-delay list is used |
| Order block | **replaced** by the Mode radio (`widget_custom_mode`: Complete H(s) / Lowpass prototype) |
| Frequency block | **Prototype:** as today (fc, or f₁/f₂ + unit). **Complete:** only the Unit radio (split out of `draw_frequency_block` with the same label, options and index, so the choice carries over; it is keyless today, `ui_components.py` L263), plus an `st.empty()` placeholder filled after the resolution step with the measured edges |
| Gain block | a radio `widget_custom_gain_mode` (Normalize / As entered). **Normalize:** `draw_gain_block()` as today. **As entered:** a placeholder caption "G = … V/V (… dB) from H(s)" |
| Ripple block | **Complete:** α relabelled "Passband edge level α (dB below peak)"; A_s relabelled "Stopband reference A_s (plot only)". **Prototype:** α replaced by a placeholder showing the measured prototype edge attenuation; A_s as above |
| Modifications / delay blocks | skipped |

Every variable the rest of `app.py` reads must still be assigned when blocks are skipped:
- `final_lp_order`, `final_hp_order`, `f1_val`, `f2_val` (None for LP/HP), `freq_unit`
- `delay_anchor_ui = None`, `delay_spec = None`
- `final_alpha`, `final_as_lp`, `final_as_hp`
- `pb_mod_lp/hp = sb_roll_lp/hp = False`

`validate_filter_specs` (L424) is **skipped** for Custom. Its BR "even total order" `st.stop()`
would otherwise fire on orders that are only display values, and would fire before the panel
exists.

### 9.2 Editor panel: placement and Streamlit mechanics

**Placement.** The panel is written with `with tab_plots:` directly after `st.tabs(...)` (L432),
**before** `multiplier` / `real_fc` (L440-441). Streamlit places tab content in execution order,
so the panel appears above "Magnitude Response", and its return values are available to the rest
of the same run.

It must come before L441. `real_fc` is fixed there and then consumed by:
- Tab 2 (L1300)
- the Tab 3 bricks and signature (L1463, L1488)
- the report (L899, L913-931)

**Contents.**
1. A form radio: Coefficients / Factored (f₀, Q) / Factored (Tietze–Schenk) / Roots (σ, ω).
2. A scale radio and f_n. Complete mode only; prototype mode forces normalized.
3. The tables for the active form, and the paste box(es) with an order toggle and an "Apply"
   button.
4. K or A₀, for the factored and roots forms.
5. Diagnostics:
   - parsed counts (n_p, n_z, n₀, jω pairs, real poles)
   - detected type vs the radio
   - measured edges, or the prototype edge attenuation
   - peak gain and its frequency
   - conditioning
   - merges
   - gate errors, warnings and pre-flight messages

**Keys.**
- Every widget gets an explicit `custom_*` key. Several sidebar widgets are keyless, and
  Streamlit's auto-IDs do not depend on the container.
- The notch keys `pin_notch_*`, `val_notch_*` and `ems_active_*` are never touched. They have
  ordering rules (app.py L495-519).

**State.**
- **Persistence:** the parsed spec is mirrored into a non-widget key `_custom_spec`, the same idea
  as `_mem_*`. Streamlit discards the state of widgets that are not rendered, so without the
  mirror a single run on Butterworth would wipe the tables.
- **`st.data_editor`:**
  - Its session_state entry holds an *edit delta*, not the table. The base DataFrame passed in
    must therefore stay fixed between runs: build it from `_custom_spec` only when the editor
    revision key changes (on form switch, paste-Apply or reset).
  - Never feed the returned table back in as the next base, or edits are applied twice.
  - Use `num_rows="dynamic"` and a column config with numeric types.

### 9.3 Resolution step

This runs immediately after the panel, still before L441, and is cached with `st.cache_data`
keyed on the JSON of `custom_spec` plus the sidebar values:

```text
res = filter_engine.synthesize_custom(spec_json, filter_type, mode, f1_hz, f2_hz,
                                      alpha_db, gain_mode)       # main process, ms
if res.errors:  show in panel; st.stop()
f1_val, f2_val            <- measured edges (complete) | unchanged (prototype)
final_alpha               <- measured prototype edge attenuation (prototype only)
final_lp_order/hp_order   <- display values (§5.3)
final_gain_units          <- G (as entered only)
fill the sidebar placeholders
```

- There is no process pool. The work is milliseconds of numpy, while `run_in_pool` exists for the
  heavy solvers.
- `synthesize_custom` in `filter_engine.py` is a thin wrapper over `custom_tf.design_custom`.
  That keeps one engine API, and it is the headless entry FS-012 needs.
- No FS-006 name clashes: avoid `_mult`, `_dk`, `_b`, `_lim` at module level. The FS-006 block
  (L272-415) runs only when `delay_spec` is set.

### 9.4 Engine dispatch

At L444+: `if is_custom: engine_results = custom_results`, otherwise the existing four branches.
This also skips the notch-pin collection inside those branches.

### 9.5 Consumer-by-consumer gating

| Consumer (app.py) | Custom |
|---|---|
| L689-768: notch display mapping, `_last_free_notches*` writes | **gate the whole block**. Otherwise Custom BP raises NameError on `active_slots_hp` / `P_eff_hp` (L730, L735), Custom BR on `active_slots_br` (L756), and Custom LP overwrites the `_last_free_notches` seeds of a later Elliptic design |
| L771-936: report snapshot | Custom rows (§9.6); `w_norm = custom_info['w_n']`; `report_passband(s)` work as is once `real_fc` / `_lo` / `_hi` are post-resolution |
| Tab 1 main magnitude, passband detail, phase/GD | as is (they read `filter_type`, `f1/f2`, α, A_s, G) |
| Tab 1 group-delay detail / delay summary | off (no `delay_info`) |
| L1021-1083: Calculated Stopband Edges | **gated** (it indexes `f_stop_*` directly) |
| L1085-1118: Frequency Probes | as is (defaults from `f1/f2`) |
| L1122-1267: Section C, Manual Notch Tuning, incl. its heading | **gated**. Zeros are edited in the panel. The BP/BR grids would also raise NameError |
| Tab 2 Roots & TF (L1272-1435) | as is, except `w_norm` = `custom_info['w_n']` (L1297-1300) |
| Tab 3 signature (L1488) | **replaced** for Custom by `custom_{filter_type}_{roots_sig}_mnemo_{do_absorb}`. `roots_sig` is a hash of the snapped physical roots, sorted and rounded to 10 significant digits. It leaves out α, `real_fc`, k and G: in complete mode `real_fc` moves with α, which would reset manual pairing for nothing. Changing the form with the same H(s) keeps the pairing |
| Tab 3 "Equalize DC and HF gains" (L1723-1728) | hidden for an asymmetric custom BR (§6.4) |
| Tab 3 `hw_sections` / `hw_filter_type` (L1807-1834) | as is, plus the `hw_pb_gain` line (§6.2) |
| Tabs 4–5, report PDF | unchanged |

### 9.6 Report rows

- Response "Custom H(s)" and Filter type.
- Mode.
- Entry form and scale: e.g. "Factored (f₀, Q), normalized, f_n = 1 kHz".
- Structure: "n_p poles; n_z finite zeros (n₀ at origin, m jω pairs)".
- Passband edges: measured at −α, or target edges (prototype) plus the prototype edge attenuation.
- Gain mode and entered peak gain.
- Detected type, if it mismatches.
- Warnings.
- The A_s row is relabelled "(plot reference)".
- The modification and manual-notch rows are omitted.
- `report_spec_short = f"Custom_{filter_type}_n{n_p}"`.

### 9.7 New widget keys (for the ROADMAP §7 manual backlog)

- Sidebar: `widget_custom_mode`, `widget_custom_gain_mode`.
- Panel: `custom_form`, `custom_scale`, `custom_fn`, `custom_K`, `custom_paste_num`,
  `custom_paste_den`, `custom_paste_order`, and the editor keys `custom_tbl_<form>_<part>_<rev>`.
- Non-widget: `_custom_spec`, `_custom_rev`.

---

## 10. Build plan (files and order)

1. **New `custom_tf.py`** (Tier A, no Streamlit; SPDX header):
   - `parse_spec`: all forms → normalized zpk + ω_n + typed-digit precision.
   - `roots_from_coeffs`: balancing, the even-part numerator, the precision-aware merge.
   - `snap_roots`, `gate`, `preflight_pairing` (uses `pairing_utils` read-only).
   - `lp_prototype_to`: wraps `scipy.signal.lp2*_zpk`, then snaps.
   - `peak_gain`, `detect_type`, `measure_edges`, `conditioning`.
   - `design_custom` → `engine_results` or `CustomTFError(messages)`.
2. **`filter_engine.py`:** `synthesize_custom(...)` as a thin entry.
3. **New `dev/fs007/check_custom_tf.py`:** the §11 numeric asserts. Run it before any UI work.
4. **`ui_components.py`:**
   - a `CUSTOM` name constant and Custom branches in `draw_frequency_block` (Unit radio
     split-out) and `draw_ripple_block` (labels / placeholder)
   - a new `draw_custom_mode_block` and gain-mode radio
   - a new `draw_custom_editor` for the panel
   - the `validate_filter_specs` bypass
5. **`app.py`:**
   - Response list and sidebar chassis
   - the panel and resolution step before L441
   - the engine-dispatch bypass
   - gating of L689-768, L1021-1083 and L1122-1267
   - the Tab 2 and report `w_norm`
   - the Tab 3 signature and BR-equalize hide
   - the `hw_pb_gain` line
   - the report rows
6. **Docs:**
   - `docs/ARCHITECTURE.md`: file map (`custom_tf.py`), `engine_results.custom_info`, app.py
     section map.
   - `docs/CONTRACTS.md`: §2 gets the producer rule "a custom producer emits only origin / jω / ∞
     zeros with exact conjugates and strictly LHP poles; the gate is relaxed only by FS-013 /
     FS-014", and §6 gets `custom_info`.
   - `dev/ROADMAP.md` §7: the manual backlog entry.

Not touched: `pairing_utils.py`, `tf_utils.py`, `filter_solvers.py`, every `cells_*.py`, Tier C,
`topology_tab.py`, `response_tab.py`, `report_pdf.py`, `tf_cache_v6.json`.

`tf_utils.py` was listed in the original item for "coefficient ↔ root conversion". That
conversion is Tier-A math and lives in `custom_tf.py`. `tf_utils` stays display-only, and its
existing Tab 2 tables already show every other form of the parsed H(s).

---

## 11. Validation

### 11.1 `python dev/fs007/check_custom_tf.py` (asserts; numbers from §4–§5)

1. **Coefficient round trip, Butterworth n = 4.**
   - Input: `[1, 2.61313, 3.41421, 2.61313, 1]` (6 significant digits).
   - Expected: Q = 0.541193 / 1.306574 (exact values 0.541196 / 1.306563), and a passband
     deviation from the exact design ≤ 3·10⁻⁵ dB (measured 2.19·10⁻⁵).
   - The same H entered in (f₀, Q), in Tietze–Schenk (a = 1.847759, 0.765367; b = 1) and as roots
     gives identical zpk (≤ 10⁻⁹ relative).
2. **Elliptic n = 6 (0.5 dB / 60 dB) numerator via the even-part method.**
   - Expected: Re z = 0 exactly, ω_z error ≤ 10⁻¹⁴, n₀ = 0.
3. **Scale equivalence.**
   - Normalized with f_n = 1 kHz vs the same H in absolute rad/s gives identical `engine_results`
     (≤ 10⁻¹² relative).
   - Tab-2 normalized coefficients with `w_n` = 2π·f_n reproduce the input.
4. **Transforms.**
   - H_new(s) = H_proto(mapped s) ≤ 10⁻¹⁴ at complex test points, for LP/HP/BP/BR, using an
     elliptic n = 5 prototype.
   - k checks: HP and BR k = H_proto(0); BP k = K·B^(n_p−n_z).
   - Wideband BP small root ≤ 10⁻¹² relative at f₂/f₁ = 10⁴.
5. **Against the engine.** A Chebyshev symmetric BP / BR from the tool (its symmetric bypass uses
   an LP→BP/BR transform) vs prototype mode with the same `cheb1ap` prototype:
   - equal roots ≤ 10⁻⁹ relative, and |H| equal after peak normalization.
   - Butterworth BP comes from `synthesize_bgb` (direct synthesis), so compare |H| only.
6. **Repeated roots.**
   - (s² + 1)^m for m = 1…10 with exact coefficients: merged to m-fold ±j, ω_z error ≤ 10⁻¹².
   - (s² + ω₀²)^m, m = 2…10, 6-digit absolute coefficients at ω₀ = 2π·1 kHz: merged, ω_z error
     ≤ 2·10⁻⁶.
   - Two notches 1 % apart stay two. Two notches 0.1 % apart at 6 digits merge (documented).
7. **Conditioning diagnostic.**
   - Butterworth BP 2×5, b = 0.1, 6 digits → warning.
   - Butterworth BP 2×6, b = 0.1, 6 digits → error (RHP in a trial).
   - Butterworth LP 8, 6 digits → no warning.
8. **Gate.** Each §7.1 error row fires with its message. That covers:
   - RHP pole, jω pole, origin pole
   - lone complex root
   - real zero and off-axis zero pair (message names FS-014); RHP zero pair (message names FS-013)
   - improper TF
   - more jω pairs than complex pole pairs
   - BP with 2 real poles, BR with 2 real poles
   - n_p = 31
   - no −α crossing
9. **Pre-flight.** A BP with an odd origin-zero count and notches reproduces the "jω pair +
   origin zero in a 2nd-order stage" case and warns.
10. **Type detection.**
    - The tool's own Elliptic LP n = 6 (even, finite HF floor) is detected as Lowpass.
    - Its HP mirror is detected as Highpass.
    - A shelving biquad is detected as "other".

### 11.2 Also

`python verify.py` passes unchanged. There is no Tier-B change, and this confirms it.

### 11.3 App

1. **Complete mode.** Custom LP / HP / BP / BR, each entered once in a different form:
   - every tab renders with no exception
   - one section solves in Topology
   - Resulting Response overlays the ideal curve
   - the PDF report has the Custom rows
2. **Prototype mode.** Butterworth n = 4 prototype → LP 1 kHz, HP 1 kHz, BP 800–1250 Hz and
   BR 800–1250 Hz. Magnitude plots are identical to the synthesized Butterworth designs at the
   same specs, and the stages (f₀, Q) are equal.
3. **Round trip.** Take the tool's own Tab-2 coefficients of Butterworth LP 4 / 1 kHz and of
   Elliptic LP 5, and enter them as Custom. Expected: the same stages and the same top BOM in
   Topology.
4. **As entered, with G < 1.** The Topology overall readout shows G (the `hw_pb_gain` fix).
   Switching back to Butterworth shows the sidebar gain unchanged.
5. **Response switching.** Custom → Butterworth → Custom restores the tables (`_custom_spec`).
6. **Regression.** The default Butterworth LP and an Elliptic BP give an identical Tab-3 pairing
   and identical Topology results vs `main`.

---

## 12. Future hooks

**RHP zeros (FS-013).**
- The formats already carry them: roots with σ > 0, zero pairs with Q_z < 0, real zeros with
  σ_z > 0.
- FS-013 flips the gate rows "RHP complex zero pair" and "real RHP zero" from error to allowed,
  for the families it adds.
- It also needs:
  - zero bricks that keep the real part, i.e. a real-zero brick type and Q_z on complex zero
    bricks
  - `classify_section` families for all-pass numerators
  - a **Section schema** field beyond `fz_hz` (Q_z or σ_z) and its use in `_build_cfg`
  - cells

  Those are FS-013's contract changes, and they do not touch the FS-007 input format.

**Off-axis LHP zeros** (delay-equalized LPs, lead networks, fitted responses). These need
general-numerator cells, e.g. summing-output AM / KHN / Tow-Thomas. That is FS-014's survey. The
gate row points there.

**FS-011 (save/load).** `custom_spec` plus the two sidebar keys is the complete Custom state.
`engine_results` is re-derived on load in milliseconds.

**FS-012 (headless).** `filter_engine.synthesize_custom(spec, …)` has no Streamlit dependency.

**Follow-up idea, out of scope here:** "Load current design into Custom". Store the last
synthesized zpk and offer it in the panel as absolute roots. It would make round trips one click.

---

## 13. Dependencies and findings for other items

**FS-016 (soft; ROADMAP L171 already says "FS-016 before FS-007 build").** The FS-007 gate and
pre-flight make Custom safe with today's pairers, so this is not a hard dependency. Findings from
this planning go to FS-016's Notes, by code reading, to be confirmed there:
- `auto_pair_bandpass` uses only `real_poles[0]` (L140). Further real poles vanish. A standard BP
  can hit this too: a wideband odd-order prototype gives two real poles per real prototype pole
  when |r|·B > 2ω₀.
- `auto_pair_bandpass` dumps leftover origin zeros into the lowest stage even if it holds a jω pair
  (L151-162). `classify_section` then ignores them.
- In a BR real + real stage, `hw_sections.f0_hz` comes from p₁ while Q = q_eff (app.py L1826).
  `_stage_rho` has the same ω₀.
- The Tab 3 signature (app.py L1488) lacks `filter_type`, a latent problem for standard LP ↔ HP
  switches too.
- Minor: `hw_gen` (L1835-1838) omits `fz_hz` and `n_origin_zeros`. This only matters for keeping
  per-section BOM picks.

**FS-015.** The Topology BR readout assumes equal passband gains (§6.4).

**FS-003 (soft).** The panel sits at the top of Response Plots, which FS-002 and FS-003 reshape.
Build FS-007 after them if convenient, or move the panel when they land.

**FS-013 / FS-014.** They receive the gate rows of §12.
