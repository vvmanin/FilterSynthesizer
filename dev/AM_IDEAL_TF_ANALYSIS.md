# Ackerberg–Mossberg (AM) Family — Ideal-Opamp Analysis

Status: analysis only (no implementation). All formulas below were re-derived from the
netlist by nodal analysis and verified symbolically with a CAS (28/28 checks pass:
all three transfer functions, the handbook reduction, every degenerate cell, the
rejected-combination conditions, and the equal-swing property).

Scope: 2nd-order AM biquad, ideal opamps (A → ∞). Third-order absorption is covered
in §7 only to the depth needed to confirm it stays exact. Finite-GB (non-ideal)
behavior is deliberately deferred; open questions for that stage are listed in §8.

---

## 1. Netlist under analysis

Nodes: `a` = input, `m1` = U1(−), `out1` = U1 out, `p2` = U2(+), `out2` = U2 out,
`m3` = U3(−), `out3` = U3 out. Grounded: U1(+), U2(−), U3(+).

| Branch | From–To | Role |
|---|---|---|
| C1 | a–m1   | input (feedforward, s² path to out1) |
| R1 | a–m1   | input (s path, − sign) |
| R2 | a–p2   | input (s⁰ path to out1) |
| R3 | a–m3   | input (s path, + sign) |
| C2 | m1–out1 | core: Miller integrator cap |
| R4 | m1–out1 | core: damping (Q) |
| R5 | m1–out2 | core: loop closure |
| R6 | out1–p2 | core: composite-integrator input |
| C3 | p2–out3 | core: composite-integrator cap |
| R7 | out3–m3 | core: inverter feedback |
| R8 | out2–m3 | core: inverter input |

The core `{C2, C3, R4, R5, R6, R7, R8}` is present in every cell; the four input
branches `{C1, R1, R2, R3}` are the selectable degrees of freedom (0..4 of them
populated). U1 = Miller integrator, (U2, U3) = actively compensated non-inverting
integrator (the AM composite), which is the entire reason this family exists.

## 2. Exact transfer functions

With ideal opamps, `m1`, `p2`, `m3` are virtual grounds. KCL at those three nodes
(`Va` = input, `V1..V3` = opamp outputs, `Gk = 1/Rk`):

```
m1:  (s·C1 + G1)·Va + (s·C2 + G4)·V1 + G5·V2 = 0
p2:   G2·Va + G6·V1 + s·C3·V3               = 0
m3:   G3·Va + G8·V2 + G7·V3                 = 0
```

### 2.1 Common denominator — Key fact 1

Every tap shares the same denominator:

```
D(s)  = s² + s·1/(R4·C2) + (R8/R7)·1/(R5·R6·C2·C3)

ω0²   = (R8/R7) / (R5·R6·C2·C3)
ω0/Q  = 1/(R4·C2)          ⇒   Q = ω0·R4·C2 = R4·sqrt( R8·C2 / (R7·R5·R6·C3) )
```

Pole placement (ω0, Q) is therefore completely decoupled from the choice of output
tap and from the input branches. Tap + input branches shape only the numerator.
Note that R7, R8 enter the poles only as the ratio R8/R7.

### 2.2 The three tap transfer functions

**out1 (U1 output):**

```
T1(s) = − [ (C1/C2)·s²
          + ( 1/R1 − R8/(R3·R5) )·(s/C2)
          + (R8/R7)·1/(R2·R5·C2·C3) ] / D(s)
```

Coefficient → input-branch mapping at out1 (each term controlled independently):
s² ← C1 ; s ← R1 (−, i.e. inverting BP) and/or R3 (+, sign-selectable) ; s⁰ ← R2.

**out2 (U2 output):**

```
T2(s) = − [ (R8/R3)·s²
          + ( R8/(R3·R4·C2) − R8/(R2·R7·C3) + C1·R8/(R6·R7·C2·C3) )·s
          + (R8/R7)·( 1/(R1·R6) − 1/(R2·R4) )/(C2·C3) ] / D(s)
```

At out2 the coefficients are *entangled*: R3 feeds both s² and s; R2 feeds both
s and s⁰ (with negative signs); R1 feeds s⁰. Only single-branch cases are clean.

**out3 (U3 output):**

```
T3(s) = − [ ( 1/(R2·C3) − C1/(R6·C2·C3) )·s
          + ( 1/(R2·R4) − 1/(R1·R6) + R8/(R3·R5·R6) )/(C2·C3) ] / D(s)
```

**Key fact 2:** T3 has at most a 1st-order numerator. Identity (CAS-verified):
`T3 = −(R7/R8)·T2 − R7/R3`. With R3 absent, out3 is a scaled, inverted copy of
out2. Consequently **HP and all notch types are impossible at out3**, and out3
serves only as a sign-flip tap.

### 2.3 Handbook consistency check

Substituting the canonic design `R1=R/k, R2=R/c, R3=R/b, R4=QR, R5=R6=R7=R8=R,
C1=aC, C2=C3=C, ω0=1/(RC)` into T1 reproduces the Schaumann/Van Valkenburg form
**exactly** (CAS-verified):

```
T(s) = − ( a·s² + s·ω0·(k−b) + c·ω0² ) / ( s² + s·ω0/Q + ω0² )
```

The `(k−b)` mechanism is the R1/R3 pair injecting the s-term with opposite signs.

## 3. Realization enumeration per tap

Convention below: "gain" quantities are magnitudes; the leading sign is stated
separately. H∞ = high-frequency gain, K = DC gain, Hmid = band-center gain.

### 3.1 out1 — the full-rank tap (all six cell types)

| Cell | Input branches | T(s) | Gain / key ratios | Sign |
|---|---|---|---|---|
| LP   | R2       | −K·ω0²/D            | K = R6/R2 | − |
| HP   | C1       | −H∞·s²/D            | H∞ = C1/C2 | − |
| BP−  | R1       | −(1/(R1C2))·s/D     | Hmid = R4/R1 | − |
| BP+  | R3       | +(R8/(R3R5C2))·s/D  | Hmid = R4·R8/(R3·R5) | + |
| N / LPn / HPn | C1 + R2 | −(C1/C2)·(s²+ωz²)/D | ωz² = (R8/R7)/(R2·R5·C1·C3); H∞ = C1/C2; K = R6/R2 | − |

**Structural-zero property (decisive for the notch family).** With only {C1, R2}
populated, the s¹ numerator term is *identically zero for any element values* —
R1 and R3 simply do not exist, so there is nothing to mistune. The transmission
zeros sit exactly on the jω axis regardless of tolerances: ideally infinite null
depth; tolerances only shift ωz, they never fill the notch. One structure covers
all three notch kinds via the free ratio

```
ωz²/ω0² = (R6/R2)·(C2/C1)      → LPn (>1), symmetric N (=1), HPn (<1)
```

and the standard biquad gain relation K = H∞·(ωz/ω0)² holds automatically.

### 3.2 out2 — the classic-core tap (LP and BP only)

| Cell | Input branches | T(s) | Gain | Sign |
|---|---|---|---|---|
| LP  | R1 | −H·ω0²/D | H = R5/R1 | − |
| BP− | C1 | −(C1·R8/(R6R7C2C3))·s/D | Hmid = C1·R4·R8/(C3·R6·R7) | − |

LP@out2{R1} is exactly the "commercial tool" circuit (omits C1, R2, R3).

**Rejected at out2 (with the explicit matching conditions):**

- **HP:** {R3} alone gives `−(R8/R3)·s·(s + 1/(R4C2))/D` — a parasitic real zero
  at −ω0/Q, not an HP. Killing the s and s⁰ terms requires adding R2 *and* R1
  with two exact balances (CAS-verified):

  ```
  s¹ = 0:   1/R2 = (R7·C3/(R4·C2))·(1/R3) + C1/(R6·C2)
  s⁰ = 0:   1/R1 = (R6/R4)·(1/R2)
  ```

  The zeros are then defined by *differences of products* — any tolerance moves
  them off the jω axis / away from the origin. This reintroduces exactly the
  matching sensitivity that the AM family otherwise avoids. **Reject.**

- **N / LPn / HPn:** the same class of cancellation conditions is needed. **Reject.**

- {R2} alone: bilinear zero at −ω0/Q ("LP with a real zero") — non-standard, unused.

### 3.3 out3 — the sign-flip tap

Since `T3 = −(R7/R8)·T2 − R7/R3`:

| Cell | Input | Gain | Sign |
|---|---|---|---|
| LP+ | R1 | (R5/R1)·(R7/R8) | + |
| LP− | R3 | R7/R3 | − |
| BP+ | C1 | C1·R4/(C3·R6) · (with R7=R8) | + |
| BP− | R2 | R4·C2/(R2·C3) | − |

HP/notch structurally impossible (no s² term). Use out3 only when a cascade needs
a free sign flip. Note out3 is inside the compensation loop; loading it is ideal-
equivalent to loading out2, but flag it for the non-ideal stage.

## 4. Recommended realization per cell type — and the out1 vs out2 LP question

### 4.1 Recommended cell set

**Group 1 — classic AM core, input R1 → m1 (one netlist, two taps):**

- **BP− = out1**, **LP = out2**. This is exactly the original Ackerberg–Mossberg
  (1974) resonator and what the commercial tools ship. LP and BP share an
  identical netlist (R1 + core), differing only in which opamp output is tapped.

**Group 2 — feedforward cells, output = out1 (the only structural-quality choice):**

- **HP = {C1} @ out1**
- **N / LPn / HPn = {C1, R2} @ out1** (structural jω-axis zeros)

**Options:**

- **BP+ = {R3} @ out1** when a non-inverting BP fixes cascade sign bookkeeping
  at zero extra cost.
- out3 taps (LP+/BP+) for sign flips.
- **LP @ out1 {R2}** kept as a valid alternate (see 4.2); worth an A/B flag for
  the non-ideal stage.

### 4.2 Why do commercial tools take LP at out2 while the handbook general biquad uses out1?

At the ideal level the two LP realizations are **exactly equivalent** — this was
verified, not assumed:

| Property | LP @ out1 {R2} | LP @ out2 {R1} |
|---|---|---|
| Denominator / ω0, Q | identical D(s) | identical D(s) |
| Element count | 6R + 2C + 3 amps (omit C1,R1,R3) | 6R + 2C + 3 amps (omit C1,R2,R3) |
| DC gain | −R6/R2 | −R5/R1 |
| Gain orthogonal to poles | yes (R2 ∉ D) | yes (R1 ∉ D) |
| Internal swing (balanced core §6.3) | equal peaks | equal peaks |
| Sign | inverting | inverting |

The differences are conventional or non-ideal only:

1. **Lineage.** The original 1974 AM paper is the R1-input two-integrator loop
   with BP@out1 and LP@out2; commercial tools copy the reference schematic.
2. **Multi-tap convenience.** The R1 input yields BP@out1 + LP@out2 + LP(+)@out3
   simultaneously — universal-filter style.
3. **Keeping p2 canonical.** With the input at m1, node p2 carries only {R6, C3},
   so the published active-compensation analysis of the (U2,U3) composite
   integrator applies verbatim. LP@out1 hangs R2 on p2 and slightly alters the
   composite loop's feedback factor — an effect that exists only with finite GB
   (symmetrically, LP@out2 hangs R1 on m1 and alters the Miller loop's factor).
4. **Practical for this project.** Using the identical netlist as the commercial
   reference makes cross-validation of the new family trivial.

**Recommendation:** adopt Group 1 (LP@out2) as primary; quantify out1-LP vs
out2-LP once the finite-GB model is in place (nonideal stage), where reason (3)
becomes a measurable difference.

## 5. Design equations per selected cell

Because every specification has a *dedicated closing element*, the whole family
solves sequentially in closed form.

**Core (all cells), given ω0 and Q:**

```
1. pick C2, C3 from stock              (2 free choices)
2. pick R7 = R8 = Rx                   (impedance level; hard-matched pair, §6.2)
3. R5 = m / (ω0·C2)                    (m = 1 → high-Q equal-swing; exact factor 1/√(1+1/Q²), §6.3)
4. R6 = (R8/R7) / (ω0²·R5·C2·C3)       → closes ω0 exactly
5. R4 = Q / (ω0·C2)                    → closes Q exactly (R4 appears in Q only)
```

**Per-cell input elements (last, each closed-form):**

| Cell | Element(s) | Equation |
|---|---|---|
| LP @ out2 | R1 | R1 = R5 / K |
| LP @ out1 (alt) | R2 | R2 = R6 / K |
| BP− @ out1 | R1 | R1 = R4 / Hmid |
| BP+ @ out1 | R3 | R3 = R4·R8 / (R5·Hmid) |
| HP @ out1 | C1 | C1 = H∞·C2 |
| N/LPn/HPn @ out1 | C1, R2 | C1 = H∞·C2 ;  R2 = (R8/R7)/(ωz²·R5·C1·C3)  → closes ωz exactly. DC gain then fixed: K = R6/R2 = H∞·(ωz/ω0)² (standard biquad relation, not a defect). |

**Input impedance seen by the previous stage:** LP/BP: resistive R1 (or R2, R3
into a virtual ground); HP: series C1 (capacitive load, same class as MFB-HP);
notch: R2 ∥ (series C1).

**DC bias note (for the record):** U1 has local DC feedback (R4), U3 has R7; U2
is DC-stabilized only through the outer loop (out2→R5→U1→R6→p2), which is the
normal state of affairs for two-integrator loops — the global loop is negative
at DC. No cell of the set breaks this.

## 6. Sensitivity analysis and solver constraint policy

This section answers: "follow the strict handbook equalities (R5=R6=R7=R8=R,
C2=C3=C), or free all values for better sensitivity like the existing solvers do?"

### 6.1 The monomial property — sensitivities are value-independent

Every figure of merit of this family is a **monomial** (single product of powers)
of the element values:

```
ω0 = sqrt(R8/R7) · (R5·R6·C2·C3)^(−1/2)
Q  = R4 · sqrt( R8·C2 / (R7·R5·R6·C3) )
ωz = sqrt(R8/R7) · (R2·R5·C1·C3)^(−1/2)
K  = R6/R2  (or R5/R1),   H∞ = C1/C2,   Hmid = R4/R1, ...
```

Hence every classical relative sensitivity equals the exponent of that element:
exactly 0, ±1/2 or ±1 — **independent of the numerical values chosen**:

| S | R4 | R5 | R6 | R7 | R8 | C2 | C3 | C1 | R1/R2 (input) |
|---|---|---|---|---|---|---|---|---|---|
| ω0 | 0 | −½ | −½ | −½ | +½ | −½ | −½ | 0 | 0 |
| Q  | +1 | −½ | −½ | −½ | +½ | +½ | −½ | 0 | 0 |
| ωz (notch) | 0 | −½ | 0 | −½ | +½ | 0 | −½ | −½ | −½ (R2) |
| gains | ±1 on the two ratio elements, 0 elsewhere | | | | | | | | |

**Consequences:**

1. **There is no sensitivity landscape to optimize by re-sizing.** Unlike MFB/SK,
   where S^Q depends on element ratios and the solvers hunt for low-sensitivity
   regions, the AM sensitivities are flat: any sizing already achieves |S| ≤ 1
   with Q-independence. The handbook equalities neither help nor hurt classical
   sensitivity. A sensitivity-objective solver for AM would sit on a zero gradient.
2. **Matched pairs pay off only where quantities are ratio-defined:**
   R8/R7 (enters ω0, Q, ωz identically as sqrt(R8/R7)) and the notch shape ratio
   ωz/ω0 = sqrt((R6/R2)·(C2/C1)) — same-value / tracking parts genuinely improve
   these. Elements that enter with the *same* sign (e.g., C2 and C3 in ω0) gain
   nothing from tracking.
3. The rejected out2-HP/notch variants (§3.2) are precisely the ones that would
   reintroduce value/matching-sensitive difference coefficients. The recommended
   set contains none.

### 6.2 One hard constraint to keep: R7 = R8

R7 and R8 appear in every pole/zero quantity only as the ratio R8/R7, so fixing
R8 = R7 costs **zero design freedom** (ω0 remains fully reachable through R5, R6,
C2, C3). In exchange, R7 = R8 (unity inverter, matched pair, ideally one array,
same opamp types) is the condition under which the AM active GB-compensation
works: the composite integrator's phase lead cancels the Miller integrator's lag,
leaving Q errors of order (ω0/ωt)² instead of the ~Q·ω0/ωt of an uncompensated
loop. This is the reason to use AM at all — keep it as a hard equality.
(A deliberate small R8/R7 offset is a known Q-trim knob; note for the non-ideal
stage.) C2 = C3 is **not** required for the compensation → free it; offer it only
as an optional BOM-convenience toggle.

### 6.3 What the handbook equalities actually encode: equal node swings

The earlier draft stated the equal-swing condition as `ω0·R5·C2 = 1` **and**
`ω0·R6·C3 = 1`. That is imprecise on two counts; here is the exact picture,
derived from the ideal internal-node transfer functions (input at the 2LP-AM
tap, `s`-domain, computed from the same nodal system the code uses).

**There are only TWO distinct swing levels, not three.** The three op-amp
outputs are `V1` (out1, the LP output), `V2` (out2) and `V3` (out3). Solving the
ideal network gives `V3 = -V2` *exactly* (U3 is the unity inverter of V2), so
`|V3| ≡ |V2|` for all ω and every component value. Only `|V1|` and `|V2|` can
differ. The exact ratio is

```
V2/V1 = -(R5/R4)·(1 + s·R4·C2)          (exact, all ω)
```

so at the pole frequency (`R4 = Q/(ω0 C2)`, i.e. `ω0 R4 C2 = Q`):

```
|V2/V1|(ω0) = (R5/R4)·√(1 + (ω0 R4 C2)²) = ω0·R5·C2·√(1 + 1/Q²).
```

**Equal swings therefore require**

```
ω0·R5·C2 = 1 / √(1 + 1/Q²)              (→ 1 only as Q → ∞)
```

not `ω0·R5·C2 = 1`. The unit value is the **high-Q asymptote**; at Q = 5 the
exact factor is 0.981 (≈2% below 1), and using `ω0R5C2 = 1` leaves the out2 peak
~1.5% above the out1 peak (numerically confirmed: peak ratio 1.015 at `ω0R5C2=1`
vs 0.995 at the corrected value).

**`ω0·R6·C3 = 1` is not an independent swing condition** — `R6` and `C3` do not
even appear in `V2/V1`. It is a *consequence* of the equal-swing condition
together with the pole-frequency constraint `ω0² = 1/(R5 R6 C2 C3)`: substituting
`ω0 R5 C2 ≈ 1` into that constraint forces `R6 C3 = R5 C2`, i.e. the two
integrator time constants are equal (`ω0 R6 C3 = ω0 R5 C2`). So the second
equality restates "equal integrator time constants", which follows once the
first holds and ω0 is placed — it is not a second lever on the swing balance.

**Equal swing does not by itself require R5 = R6.** At Q = 5 the exact
equal-swing point has `ω0R5C2 = 0.981` and, after placing ω0, `R5/R6 = 0.962`.
The familiar handbook choice `R5 = R6 = 1/(ω0 C)` (with `C2 = C3 = C`, `R4 =
Q·R5`) is what you get by additionally imposing `ω0R5C2 = ω0R6C3 = 1` — i.e. it
is the **Q → ∞ equal-swing solution**, a clean same-value design that is within
~2% of true peak-equalization for any Q ≳ 5 and exact as Q grows. That is the
"Equalize R, C values" default in the tool (`R5 = R6 = R7 = R8`, `C2 = C3`);
unchecking it frees R5/R6 and C2/C3 (the matched pair `R7 = R8` still holds), so
the solver can trade a slight swing imbalance for a tighter E-series fit.

The solver treats none of this as a hard constraint by default: swing balance is
a soft target (penalty on `|ln(ω0·R5·C2·√(1+1/Q²))|`), tradable against E-series
snapping error, and the impedance-level guard below still applies.

### 6.4 Resulting solver policy (contrast with the MFB solvers)

- **Free variables:** C2, C3, R4, R5, R6, the level of R7=R8, input elements.
- **Hard constraint:** R7 = R8.
- **Soft constraints:** swing balance (§6.3); optionally C2 = C3 (BOM toggle).
- **Objective:** E-series snapping error + spread + swing penalty (+ an
  impedance-level guard: keep node resistances moderate, because stray-C /
  parasitic effects — unlike the classical sensitivities — *do* scale with
  impedance level).
- **Structure:** fully sequential, closed-form, with a dedicated closing element
  per spec: **R6 → ω0, R4 → Q, input R → gain, R2 (or C1) → ωz.** After snapping,
  each spec error maps through |S| ≤ 1 → predictable. No zero-manifold search or
  heavy optimization is needed; a small enumeration over stock C2, C3 (and the
  swing factor m) suffices. This is a far lighter solver than the MFB path — a
  simplification the implementation should exploit rather than reusing the heavy
  machinery by default.

## 7. Third-order absorption (3LP / 3HP / 3LPn / 3HPn) — exactness note

Because every biquad input lands on a **virtual ground**, the passive RC input
network sees a pure resistive/capacitive load to (virtual) ground. The absorbed
real pole is therefore *exactly* first-order — no back-interaction, no
approximation (contrast SK 3rd-order, where the passive section interacts with
the feedback network):

```
3LP  (R0 series, C0 shunt; biquad input conductance Gin = 1/R1 or 1/R2):
     Va/Vin = G0 / (G0 + Gin + s·C0)
     pole = (G0 + Gin)/C0,  DC factor = G0/(G0 + Gin)

3HP  (C0 series, R0 shunt; HP-cell input C1):
     Va/Vin = s·C0 / (G0 + s·(C0 + C1))
     pole = G0/(C0 + C1),  HF factor = C0/(C0 + C1)

3LPn (R0 series, C0 shunt; notch-cell input {C1, R2}):
     Va/Vin = G0 / (G0 + G2 + s·(C0 + C1))

3HPn (C0 series, R0 shunt; notch-cell input {C1, R2}):
     Va/Vin = s·C0 / (G0 + G2 + s·(C0 + C1))
```

Total TF = first-order × biquad with these substitutions. The sequential solver
of §5 stays closed-form: two new unknowns (R0, C0) close the real pole and take
up the divider's gain factor, which is folded into the cell's gain equation.

## 8. Summary decision table and open items

| Cell | Input branch(es) | Tap | Sign | Elements | Notes |
|---|---|---|---|---|---|
| LP   | R1 | out2 | − | 6R 2C 3A | classic AM; alt: {R2}@out1 (A/B flag) |
| BP−  | R1 | out1 | − | 6R 2C 3A | same netlist as LP, different tap |
| BP+  | R3 | out1 | + | 6R 2C 3A | free sign option |
| HP   | C1 | out1 | − | 5R 3C 3A | |
| N / LPn / HPn | C1 + R2 | out1 | − | 6R 3C 3A | structural jω zeros; one structure, three kinds |
| 3LP / 3HP / 3LPn / 3HPn | + R0, C0 | as above | | +1R +1C | absorption exact (§7) |

Cost note: every cell spends 3 opamps. What that buys, per the analysis:
orthogonal tuning (ω0 via R5/R6, Q via R4 alone, gain via the input element,
ωz via R2/C1), value-independent |S| ≤ 1 sensitivities, a matching-free notch,
and active GB compensation for high-Q / high-frequency sections. Scoring weights
for the 3-amp cost belong to the integration stage.

**Open items deferred to the non-ideal (finite-GB) stage:**

1. Finite-GB rederivation of D(s); verify the R7 = R8 compensation condition and
   evaluate the deliberate R8/R7 offset as a Q-trim.
2. Quantitative out1-LP vs out2-LP comparison (summing-node loading of the Miller
   vs composite loop, §4.2 reason 3).
3. Effects of loading out3 when used as the section output.
4. Noise budget per cell; slew/swing at internal taps for scoring.
