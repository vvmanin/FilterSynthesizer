# Sensitivity Score — Definition, Implementation, Known Limits

*Tier C reference. Covers `sens_score`, the number that ranks candidate BOMs in
`unified_solver_v2._assemble`. Companion to `AM_IDEAL_TF_ANALYSIS.md`
(coefficient derivations) and `ROADMAP.md` §Tier C.*

---

## 1. Scope

`sens_score` answers one question: **if a component drifts by 1%, how much do the
transfer-function coefficients move?** It is a *ranking heuristic* applied to
candidates that have already passed the pole / notch / gain tolerance gates. It
is not a prediction of realized spread — that is Monte Carlo
(`hw_plots.monte_carlo`), and the two must not be confused.

Everything below is computed on the **ideal** case. Non-ideal cases carry
`a1_expr = a2_expr = None` (see the cached entries), so even a run in
`mode="nonideal"` scores the ideal coefficients.

---

## 2. Minimal theory

The classical *normalized* (relative, dimensionless) sensitivity of a quantity
`y` to a component `x`:

$$S^{y}_{x}\;=\;\frac{\partial y/y}{\partial x/x}\;=\;\frac{x}{y}\frac{\partial y}{\partial x}\;=\;\frac{\partial \ln y}{\partial \ln x}$$

Read it as: **a 1% change in `x` produces an `S`% change in `y`.**

Three properties make this the right currency here:

- **Dimensionless** — resistors (MΩ) and capacitors (µF) become directly
  comparable, and no unit bookkeeping leaks into the score.
- **Scale-invariant** — invariant under the RC gauge `C→Cf, R→R/f`, which the
  solver exploits as a free DOF.
- **Monomial-exact** — for `y = x^n`, `S = n` exactly. Since our coefficients
  are sums of R·C monomials, sensitivities land near small integers, which makes
  results easy to sanity-check by eye.

---

## 3. The derivative rule actually used

No analytic derivative is taken. `_assemble` uses a **forward difference in
relative form**, with a fixed 1% step:

$$S^{y}_{x}\;\approx\;\frac{y\bigl(x(1+\delta)\bigr)-y(x)}{y(x)}\cdot\frac{1}{\delta},\qquad \delta = 0.01$$

Truncation error is **O(δ)**. For a monomial `y = x^n` the rule returns

$$\frac{(1+\delta)^n-1}{\delta}\;=\;n+\tfrac{n(n-1)}{2}\delta+O(\delta^2)$$

so the bias is `n(n−1)δ/2`. Concretely, for `a0 ∝ 1/C4` the exact value is −1 and
the rule returns **−0.9901** (see §7) — about 1% high in magnitude. Harmless for
ordering, but it means `sens_score` values are not exact sensitivities and should
not be quoted as such.

---

## 4. What `a0, a1, a2, b0, b1, b2` mean

`build_ideal` normalizes the **denominator to monic** before extracting anything.
With `H(s) = N(s)/D(s)`:

### Second order

$$D(s)=s^{2}+a_1 s+a_0,\qquad a_1=\frac{\omega_0}{Q},\quad a_0=\omega_0^{2}$$

$$N(s)=b_2 s^{2}+b_1 s+b_0$$

For a **notch**, the design condition is `b1 = 0` (zeros exactly on the jω axis)
and then `b0/b2 = ωz²`. `b1` is driven to zero by the `R5_constraint`, not by the
optimizer — see §8.4.

### Third order

`D(s) = (s + p1)(s² + (ω0/Q)s + ω0²)`, so

$$a_2=p_1+\frac{\omega_0}{Q},\qquad a_1=\omega_0^{2}+p_1\frac{\omega_0}{Q},\qquad a_0=p_1\,\omega_0^{2}$$

The naming convention is therefore **`a_k` = coefficient of `s^k`** in the monic
denominator. The leading coefficient is 1 by construction and carries no
information.

### Where they come from

In `cells_lp.build_ideal` (same idiom in the other `cells_*` modules):

```python
den_sub = sp.Poly(den_norm.subs(sr5), s)   # monic, R5 eliminated
dcs     = den_sub.all_coeffs()             # [1, a1, a0]  or  [1, a2, a1, a0]
a1_expr = dcs[-2]
a2_expr = dcs[-3] if len(dcs) >= 3 else dcs[-2]
```

Note `sr5` — the dependent component `R5` is substituted out **before** the
coefficients are formed, so `a1_expr` / `a2_expr` are functions of the free
components only. They contain no design symbols (`w0`, `Q`, `wz`), which makes
them **design-independent and cacheable per cell** — confirmed by the on-disk
cache, e.g. `2LP-unity` stores `a1_expr = (R2+R3)/(C4·R2·R3)`.

> ⚠ For **second-order** cells `dcs` has length 3, so `dcs[-3]` is `dcs[0]` — the
> monic leading **1**. `a2_expr` is therefore the constant 1 and contributes
> identically zero. The cache shows this verbatim: `"a2_expr": "Integer(1)"`.
> The comment above the line says "last two denominator coeffs", which would be
> `dcs[-2]` and `dcs[-1]`. See §8.1.

---

## 5. Per-component evaluation (the shipped loop)

From `unified_solver_v2._assemble`, verbatim:

```python
va = [full[n] for n in lay["names"]]
a1b = F["a1_f"](*va); a2b = F["a2_f"](*va)
ss = 0.0
for i in range(len(va)):
    p = list(va); p[i] *= 1.01              # +1% on ONE component
    ss += ((F["a1_f"](*p)-a1b)/a1b/0.01)**2 \
        + ((F["a2_f"](*p)-a2b)/a2b/0.01)**2
out["sens_score"] = float(np.sqrt(ss))
```

Step by step:

1. `lay["names"]` is the cell's free-component list, derived from `var_list(topo)`
   — e.g. `[C2, C3, C4, R2, R3, R4]` for `2LPn-unity`.
2. `a1_f` / `a2_f` are the lambdified `a1_expr` / `a2_expr`.
3. One component at a time is scaled by 1.01; **all others are held fixed**.
4. Each relative change is divided by δ = 0.01, giving `S^{a_k}_{x_i}`.
5. Every term is **squared** and accumulated.
6. The score is `sqrt` of the total.

**Which components participate:** exactly those in `var_list`. Dependent
components (`R5`, derived from `R5_constraint`) are *not* in that list and are
never perturbed — their effect is folded into the other terms under the
assumption that R5 tracks perfectly.

---

## 6. Generalised form and cost

Let `S` be the sensitivity matrix with entries `S[k][i] = S^{a_k}_{x_i}`, over
the `K` scored coefficients and `n` free components. Then

$$\texttt{sens\_score}\;=\;\lVert S\rVert_F\;=\;\sqrt{\sum_{k}\sum_{i}\bigl(S^{a_k}_{x_i}\bigr)^{2}}$$

— the **Frobenius norm of the coefficient-sensitivity matrix**, with `K = 2`
today (`a1`, `a2`).

**Cost:** `K` base evaluations plus `K·n` perturbed evaluations = `K(n+1)`
scalar calls into small lambdified expressions. For a typical cell (`K=2, n=6`)
that is **14 evaluations** — negligible beside the resistor solve that produced
the candidate. Any improvement that stays within a small multiple of this is
effectively free.

**What the norm does and does not carry:**

| Property | Status |
|---|---|
| Dimensionless, unit-agnostic | ✅ |
| Invariant to RC gauge | ✅ |
| Cheap, deterministic, no RNG | ✅ |
| Sign / correlation between coefficients | ❌ destroyed by squaring |
| Per-component tolerance | ❌ all components weighted equally |
| Numerator (ωz, notch depth) | ❌ never evaluated |
| Frequency dependence | ❌ none — purely algebraic |
| Non-ideal op-amp effects | ❌ ideal coefficients only |

---

## 7. Worked example — `2LPn-unity`

Operating point built from the project's own algebra (script in Appendix A).
Units: C in µF, R in MΩ.

```
C2 = 3.333 nF   C3 = 10.0 nF   C4 = 50.0 nF
R2 = 4.579 kΩ   R3 = 8.299 kΩ  R4 = 10.0 kΩ   R5 = 1.200 kΩ (derived)
realized:  f0 = 1000.00 Hz   Q = 1.1035   fz = 2500.00 Hz   fz/f0 = 2.500
```

The coefficients at this point:

$$a_0=\frac{1}{C_4R_2R_3(C_2+C_3)},\qquad
a_1=\frac{C_3(R_2{+}R_3)\bigl[(C_2{+}C_3)(R_2{+}R_3)+C_4R_2\bigr]}{C_4R_2R_3(C_2{+}C_3)\bigl[C_3(R_2{+}R_3)+C_4R_2\bigr]}$$

| x | S^a1 (1% fd) | S^a1 (exact) | S^a0 (1% fd) | S^ω0 | S^Q |
|---|---|---|---|---|---|
| C2 | −0.1425 | −0.1429 | −0.2494 | −0.1247 | +0.0178 |
| C3 | +0.2096 | +0.2114 | −0.7444 | −0.3722 | **−0.5818** |
| C4 | −1.0576 | −1.0686 | −0.9901 | −0.4950 | **+0.5625** |
| R2 | −0.6816 | −0.6886 | −0.9901 | −0.4950 | +0.1866 |
| R3 | −0.3081 | −0.3114 | −0.9901 | −0.4950 | −0.1869 |
| R4 | **0.0000** | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| R5* | −0.1905 | −0.1905 | 0.0000 | 0.0000 | +0.1905 |

\* R5 is eliminated before the coefficients are formed, so the shipped loop never
perturbs it. Row shown from the R5-symbolic coefficients for comparison.

Resulting scores:

```
sens_score  (as shipped: a1 only, a2≡1, R5 excluded)  = 1.3199
            (a1 + a0, the evidently intended pair)    = 2.3021
sqrt(sum (S^Q_x)^2)                                   = 0.8515
sqrt(sum (S^w0_x)^2)                                  = 0.9430
predicted sigma_Q/Q  (5% caps, 1% resistors)          = 4.06 %
```

Three things to read off this table:

1. **R4 is exactly zero everywhere.** This is *correct*, not a bug: R4 cancels
   out of the constrained transfer function entirely (only the ratio R5/R4
   matters), so it genuinely cannot move the ideal response. It is the cell's
   gauge freedom. The limitation is that R4 *does* matter once A(s) is finite —
   the ideal-coefficient metric simply cannot see that.

2. **The metric mis-attributes.** As shipped, the worst offenders read as
   C4 (−1.06) then R2 (−0.68). By Q-sensitivity — what actually deforms the
   response shape — they are C3 (−0.58) and C4 (+0.56), with R2 nearly
   irrelevant (+0.19). A BOM chosen to minimise the shipped score is not the one
   that minimises Q spread.

3. **The 1% rule's bias is visible and benign**: −0.9901 against an exact −1.0,
   matching `n + n(n−1)δ/2` for `n = −1`.

### Free validation identities

Because ω0 depends only on the RC *product* and Q only on component *ratios*:

```
sum over caps      S^w0 = −1      sum over resistors  S^w0 = −1
sum over caps      S^Q  =  0      sum over resistors  S^Q  =  0
```

The table satisfies all four to ~2e−3 (finite-difference bias). These make an
excellent assertion in `verify.py` — they catch a mis-indexed coefficient, a
wrong `var_list`, or a stale cached expression, at zero runtime cost.

---

## 8. Known limits

**8.1 — `a2_expr` is the constant 1 for every second-order cell.**
`dcs[-3]` on a length-3 list is the monic leading 1. Half the sum is identically
zero, and the score collapses to `|S^{ω0/Q}|` alone. ω0 drift and Q drift become
indistinguishable — the very thing a sensitivity score exists to separate.
Third-order cells are unaffected (`dcs[-3] = a2`, a real coefficient).

**8.2 — The numerator is never scored.**
Only `a1_f` and `a2_f` are called. For notch cells this omits ωz placement and
notch depth — precisely the quantities the E-series cap grid struggles to hit,
and the entire motivation for the parallel-C2 path in `zero_manifold_solver`.

**8.3 — Squaring destroys the correlation the metric needs.**
The physically meaningful quantities are *signed linear combinations*:

$$S^{\omega_0}_x=\tfrac12 S^{a_0}_x,\qquad S^{Q}_x=\tfrac12 S^{a_0}_x-S^{a_1}_x$$

Squaring each coefficient term separately discards exactly the cancellation that
distinguishes a benign pure-frequency shift (`S^{a_0} = 2S^{a_1}` ⇒ `S^Q = 0`,
the RC gauge direction) from a genuine Q distortion. The current metric charges
full price for both.

**8.4 — Dependent components are invisible.**
`R5` is substituted out before the coefficients exist, so it is never perturbed
and is implicitly assumed to track its constraint exactly. In hardware R5 is an
independently snapped E-series part with its own tolerance, and it is what sets
the notch null: `b1 = 0` holds only at `R5 = R5c`. Its true contribution here
(S^Q = +0.19) is comparable to R2's and is simply absent from the score.

**8.5 — Tolerance-blind.**
A 5% capacitor and a 1% resistor contribute on equal terms, so the score cannot
express the most common real trade-off (spend on tighter caps, or on tighter
resistors?).

---

## 9. Recommended improvements

Ordered by value-per-cost. **All are O(n) small-scalar work per candidate**;
none changes the complexity of the solve.

### 9.1 Fix the second-order index — *cost: zero*

```python
a_exprs = dcs[1:]          # every non-leading monic coefficient
```

Store and lambdify the whole list instead of two hand-picked entries. Second
order yields `[a1, a0]`, third `[a2, a1, a0]` — the same count as today for
order 2, one more for order 3. Removes the dead term and makes §9.2 possible.

### 9.2 Combine signed sensitivities into ω0 / Q before squaring — *cost: zero*

Keep the per-component relative deltas **signed**, then form

```python
S_w0 = 0.5*S_a0
S_Q  = 0.5*S_a0 - S_a1
score = sqrt(sum(S_Q**2) + sum(S_w0**2))     # or weight Q higher
```

Identical number of function evaluations — only the arithmetic afterwards
changes. This is the single largest correctness gain available, and it is free.

### 9.3 Weight by actual component tolerance — *cost: one multiply per term*

The E-series and tolerance per component are already in `cfg`. Then

$$\frac{\sigma_Q}{Q}\approx\sqrt{\sum_i \bigl(S^{Q}_{x_i}\bigr)^{2}\Bigl(\frac{\sigma_i}{x_i}\Bigr)^{2}}$$

This is the standard statistical (Schoeffler-type) multiparameter measure. It
turns an abstract ordinal into a **predicted percentage** directly comparable to
the Monte-Carlo output — which also gives you a cheap regression test: the
prediction should track MC within a few percent for small tolerances.

### 9.4 Score the numerator on notch cells — *cost: +2 coefficients, ~2n evals*

`num_poly` already exists in `build_ideal`. Store `b2_expr` and `b0_expr`,
lambdify them, and add

$$S^{\omega_z}_x=\tfrac12\bigl(S^{b_0}_x-S^{b_2}_x\bigr)$$

For notch cells this is the most valuable single addition, since ωz placement is
the binding constraint on realizability.

### 9.5 Add a notch-depth term — *cost: one more derivative*

Depth is set by how exactly `b1` vanishes. A *relative* sensitivity is undefined
there (`b1 ≡ 0` at the constraint), so use the dimensionless leakage ratio

$$\varepsilon=\frac{b_1}{b_2\,\omega_z},\qquad \text{metric}=\frac{\partial\varepsilon}{\partial \ln x}$$

which is finite, dimensionless, and proportional to the achievable null floor.
(The exact dB also involves `|D(jωz)|`; ε is the right *ranking* quantity.)

### 9.6 Exact derivatives instead of finite differences — *cost: negative*

The coefficient expressions are small rational functions of R and C, so
`sp.diff(a_expr, x)*x/a_expr` lambdifies cheaply. Build once per cell, memoize
by `(cell name, cell_struct_sig)` exactly as `_RESP_CACHE` does — the expressions
are design-independent (§4), so the cache hits across every design and every
candidate. Per candidate this becomes **n evaluations instead of n+1** per
coefficient, and the O(δ) truncation bias disappears. Strictly faster and more
accurate.

> If symbolic diff is unwanted, a central difference
> `(y(x(1+δ)) − y(x(1−δ)))/(2δ)` is O(δ²) at 2n evaluations — still trivial, but
> 9.6 is better on both axes.

### 9.7 Perturb dependent components too — *cost: one extra coefficient set*

Keep a second set of coefficient expressions with `R5` left **symbolic**,
evaluated at `R5 = R5c`. That makes `S^{a_k}_{R5}` well-defined (the example
gives −0.19) and lets R5's own tolerance enter §9.3. Needed for any honest
notch-cell score.

### 9.8 Assert the sum identities in `verify.py` — *cost: once per cell, offline*

The four identities in §7 are exact for any correct implementation. Assert them
per cell in the self-test. They catch mis-indexing, `var_list` drift and stale
cache entries — the same class of silent failure `cell_struct_sig` was
introduced to defend against.

---

## 10. Explicitly rejected as too expensive

| Idea | Why not |
|---|---|
| Monte Carlo per candidate | 10³–10⁴ full-response evaluations each, against 14 scalar calls today — three to four orders of magnitude. Keep MC where it is: once, on the selected BOM. |
| Differentiate the **non-ideal** TF | The 3rd-order AM notch TF is a ~766k-op expression that `build_nonideal_common` deliberately refuses to build (`mna=True`). Differentiating it is catastrophic. Score ideal coefficients; let MC carry the non-ideal truth. |
| Worst-case corner enumeration (2ⁿ) | 64 corners at n=6, 4096 at n=12, and worst-case is over-pessimistic versus the statistical measure of §9.3 anyway. |
| Frequency-sweep spread per candidate | Requires `make_response_func` over a w-grid for each perturbed component: n × len(w) complex evaluations per candidate. This is what MC already does, better. |

**Net recommendation:** adopt 9.1 + 9.2 + 9.3 together — they are jointly free,
they fix the two defects that actually change the ranking (§8.1, §8.3), and they
convert `sens_score` into a quantity with physical units that MC can validate.
Then 9.4 + 9.7 for the notch families, and 9.6 as a cleanup.

---

## Appendix A — Reproduction

The table in §7 is produced by re-running `cells_lp.build_ideal`'s algebra
standalone and applying the exact rule from `_assemble`:

```python
# 1. build eqs for the 2LPn-unity gates (c2=s*C2, g4=1/R4, g6=g7=0, shorted_r5=False)
# 2. A, b = sp.linear_eq_to_matrix(eqs, [Vb, Vc, V2]);  T = A.LUsolve(b)[2]
# 3. num, den = sp.fraction(sp.simplify(T))
# 4. R5c = sp.solve(<s^1 coeff of monic num>, R5)[0]
# 5. dcs = sp.Poly(den, s).monic().all_coeffs()   ->  a1 = dcs[1], a0 = dcs[2]
# 6. S = ((f(x*1.01) - f(x))/f(x))/0.01   per component
```

Keep the standalone script next to this document if the coefficients are
revisited; it is the fastest way to check a proposed change against the shipped
behaviour without booting Streamlit.
