# LP non-ideal model — three nodal-equation defects (found + fixed)

Companion to `MFB_FOLLOWUP_FIXES.md` / `AM_NONIDEAL_ANALYSIS.md`. Covers the VCVS
low-pass family (`cells_lp.py`), the cache invalidation the fix required, and the
family-agnostic **passive-reciprocity audit** built to find it — which is the
reusable part of this note.

---

## 1. Symptom

Tab 5's "Realized (BOM)" curve for **2LP-unity** rolled off *faster than the
ideal* and showed no stopband floor, while LTspice with the same BOM and an
AD8505 showed the usual finite-Ro feedthrough. The same cascade built on
**2LP-gained** matched LTspice closely.

Measured on the reported BOM (R2 1.21k, R3 3.48k, C3 4.7n, C4 10n; AD8505-like
A_ol 1e5, GBW 95 kHz):

| f | ideal | tool (before) | with the fix |
|---:|---:|---:|---:|
| 10 kHz | −2.9 dB | −3.0 | −3.0 |
| 60 kHz | −29.1 | −31.3 | −10.1 |
| 100 kHz | −37.9 | −41.6 | −9.0 |
| 500 kHz | −65.8 | **−80.4** | −8.2 |

The old curve was *below* the ideal — the giveaway. The realized response of a
real Sallen-Key can never beat its own design target in the far stopband.

---

## 2. Root cause — three defects, all in `cells_lp.build_nonideal`

`build_ideal` is correct throughout and was **not** touched: under the virtual
short `Vm ≡ Vc`, so its fused forms are right. All three defects are places where
the non-ideal derivation inherited an ideal-model assumption that stops holding
once `A(s)` is finite.

### D1 — output-node KCL missing the C4 feedback current (`shorted_r5` branch)

```python
eq_vm = Vm - V2                                 # R5 short
eq5   = (V2 - A_s*(Vc - Vm)) / Ro               # <-- only the source
```

`eq5 = 0` reduces to `V2 = A_s·(Vc − Vm)`: **Ro divides out algebraically.** The
op-amp becomes an ideal zero-output-impedance VCVS, C4's current (which `eq2`
happily draws via `+V2*s*C4`) is supplied for free, and the stopband feedthrough
mechanism does not exist in the model. `Ro` was literally absent from the free
symbols of those cells' transfer functions — the as-coded response was
bit-identical for Ro = 100 Ω … 2 kΩ.

The surviving `A/(1+A)` follower rolloff then *adds* attenuation, which is why the
old curve sat below the ideal.

Affects `2LP-unity`, `2LP-atten`, `3LP-unity`.

> Contrast `cells_first_order`'s unity follower, which has the identical code
> shape and is **correct**: nothing is connected to its output node, so Ro
> legitimately cancels there. The shape alone is not the bug — the missing
> element is.

### D2 — R5's output-node current referenced to `Vc` instead of `Vm`

```python
eq_vm = Va*g4 - Vm*(g5 + g4 + g6) + V2*g5       # R5 runs V2 <-> Vm
eq5   = ... + (V2 - Vc)*g5 + ...                # <-- but here it runs V2 <-> Vc
```

`cells_hp` and `cells_notch` already use `(V2 - Vm)/R5`. Costs ≈2.3 dB on the
stopband plateau and ≈7 dB near the null. Affects every LP cell carrying R5
(all gained cells and all notch cells).

### D3 — R4's node-a current referenced to `Vc` instead of `Vm` (order 3 only)

```python
eq1 = ... + Vc*(g4 + c2)                        # R4 far end put on Vc
```

R4 runs node a → the op-amp (−) node `Vm`. `cells_hp.build_nonideal` carries the
corrected split with an explicit comment ("R4 connects node a to the op-amp (−)
node Vm, kept separate here"); `cells_lp` never received that fix. Mis-predicts Q
wherever loop gain is low. Affects `3LPn-gained`, `3LPn-gained+R7`, `3LPn-unity`.

---

## 3. Why the existing self-test never caught it

`tf_derivation_v2.self_test` checks `|H_ideal − H_nonideal| < 1e-4` at
`Ro=1e-9, A_ol=1e12, GBWP=1e15`. All three defects **vanish in that limit**:
D1's truncated equation gives `V2 = Vc·A/(1+A) → Vc`, the correct ideal follower;
D2 and D3 disappear because `Vm → Vc`. The ideal-limit test is blind by
construction to any defect that only bites when loop gain is gone — which is
exactly the regime the non-ideal model exists to describe.

---

## 4. The audit — passive reciprocity

The reusable finding. Switch the op-amp's controlled source off (`A_ol → 0`) and
every remaining branch is a passive two-terminal element, so the nodal admittance
matrix **must be symmetric**. A branch current present in one node's KCL but
missing from, or misdirected at, the other node's KCL breaks symmetry exactly.

Two practical details:

* **Normalise each row's sign.** Some builders write KCL as currents-in, others
  as currents-out; a row scaled by −1 is the same equation. Evaluate the diagonal
  numerically and flip the row if it is negative. Without this, ~40 % of correct
  cells report false positives.
* **Eliminate topological short rows first.** A row of the form `Vm - V2` is a
  constraint, not a KCL. Substitute it out (dropping the op-amp input node) and
  audit the resulting supernode — which is precisely where D1 lives.

```python
def audit(eqs, nodes):
    """eqs must be ordered to match `nodes` (NB: several MFB builders emit
    node m before node p, while _unknowns lists p before m)."""
    # 1. eliminate short rows  (Vm - V2  ->  Vm := V2)
    # 2. eqs0 = [e.subs(A_ol, 0) for e in eqs]      # passive network
    # 3. A, _ = sp.linear_eq_to_matrix(eqs0, nodes)
    # 4. flip each row whose numeric diagonal is negative
    # 5. assert A[i,j] == A[j,i] for all i < j
```

The test also catches wrong-node references (D2, D3), not just omissions, and it
needs no reference model, no target design and no component values.

### Results — 73 cells, every family

| Family | Cells | Verdict |
|---|---:|---|
| LP (VCVS) | 12 | **all 12 asymmetric** — D1/D2/D3 |
| HP (VCVS) | 15 | clean |
| BP (VCVS) | 2 | clean |
| NOTCH (VCVS) | 2 | clean |
| LP-MFB | 12 | clean |
| HP-MFB | 9 | clean |
| BP-MFB | 6 | clean |
| NOTCH-MFB | 2 | clean |
| AM (Ackerberg-Mossberg) | 13 | clean |
| first-order | 12 | clean |

The MFB and AM modules are structurally safer: they share **one** `_*_eqs`
builder between the ideal and non-ideal paths, gated by `opamp_src`, so the
output-node KCL is written once against the netlist. `cells_lp` (and `cells_hp`)
duplicate the equations in `build_ideal` and `build_nonideal`, which is how the
ideal-model assumptions leaked into the non-ideal one. Worth keeping in mind for
any future family.

---

## 5. The fix

### `cells_lp.py` — `build_nonideal` only

| # | Was | Now |
|---|---|---|
| D1 | `eq5 = (V2 - A_s*(Vc-Vm))/Ro` | `... + (V2 - Vb)*s*C4` |
| D2 | `(V2 - Vc)*g5` | `(V2 - Vm)*g5` |
| D3 | `Vc*(g4 + c2)` | `Vc*c2 + Vm*g4` |

### `tf_derivation_v2.py` — cache invalidation (**load-bearing**)

Four caches key non-ideal TFs by cell name + `cell_struct_sig`, and that
signature is `md5(var_list)`. This fix changes the *model*, not the component
set, so the signature would be unchanged and all four keep serving the old TF —
silently, because the component interface still matches and nothing raises:

1. `_ni_key` → the on-disk cache
2. `_RESP_CACHE` (process-local)
3. `topology_tab._response_fn` (`@st.cache_resource`)
4. `response_tab._section_H_cached` / `_loggrad_cached` (`@st.cache_resource`)

```python
CACHE_PATH_V2 = "tf_cache_v6.json"      # v5 non-ideal entries are stale
NONIDEAL_MODEL_REV = 2                  # folded into _cell_struct_sig
```

`NONIDEAL_MODEL_REV` is what reaches the in-session Streamlit caches; the filename
bump just avoids loading a dead blob. **Bump the rev on any future
`build_nonideal` change that leaves `var_list` alone.** One-time cost: every
cell's non-ideal TF is re-derived once. `tf_cache*.json` is git-ignored, so only
stale files on developer machines are affected (safe to delete `tf_cache_v5.json`).

### `hw_plots.py` — `monte_carlo`

Pre-existing, unrelated to the LTspice discrepancy, but adjacent: `"Ro"` matched
the `startswith("R")` resistor branch and was being perturbed as if it were a BOM
part, contradicting the `# op-amp symbols: fixed` comment below it. Now guarded
explicitly ahead of the resistor branch. The trailing `else` is deliberately
retained as a catch-all for any future symbol that is neither an op-amp parameter
nor R/C-prefixed.

---

## 6. Validation

**Reciprocity** — all 12 LP cells symmetric after the fix; no other family
changed.

**Ideal-limit acceptance** (`self_test`'s own metric, gate `< 1e-4`):

| cell | err | cell | err |
|---|---|---|---|
| 2LP-unity | 3.3e-12 | 3LP-unity | 1.6e-12 |
| 2LP-atten | 2.7e-12 | 3LP-gained | 3.0e-11 |
| 2LP-gained | 7.7e-11 | 3LPn-unity | 4.1e-11 |
| 2LPn-unity | 4.1e-10 | 3LPn-gained | 6.4e-08 |
| 2LPn-atten | 1.5e-10 | 3LPn-gained+R7 | 4.9e-10 |
| 2LPn-gained | 6.2e-10 | 2LPn-gained+R7 | 7.5e-08 |

Worst 6.4e-08, four orders under the gate. Order and zero counts come from
`build_ideal`, untouched.

**Conditioning** — `Ro` now appears in all twelve LP TFs. With
`OPAMP_LIBRARY["ideal"]` (`Ro = 1e-12` MΩ = 1 µΩ) the patched unity cells still
converge to the ideal TF at 1e-12 with zero non-finite points, identical to
`Ro = 1e-9`. No ill-conditioning from the new `1/Ro` terms.

**Surfaces confirmed unaffected:** every solver residual, `var_list`,
`cell_components`, `R5_constraint`, `a1/a2_expr`, `den_degree`/`num_degree` — so
`unified_solver_v2` phases 1/3, `zero_manifold_solver`, `solvability_probe`,
`discrete_snapper`, `schematic_svg` and `topology_tab.COMP_ORDER` are all
untouched. `scoring._response_metrics`' DC read (taken at ~1 Hz) moves 0.000 dB;
`hw_plots.comp_dict`'s positive-value guard is satisfied (`Ro > 0` in every
`OPAMP_LIBRARY` entry, including `"ideal"`).

---

## 7. Behaviour changes to expect

**Stopband (the point).** `2LP-unity`, `2LP-atten`, `3LP-unity` change by up to
**27 dB** in the deep stopband, gaining the finite-Ro feedthrough floor that
LTspice shows.

**Passband** — bounds the change to `nonideal_solver` pre-distortion, which fits
near f₀, and therefore to solved BOMs:

| cells | passband Δ |
|---|---|
| all notchless | 0.000 dB — BOMs do not move |
| 2nd-order notch | ≤ 0.005 dB |
| `3LPn-unity` / `3LPn-gained+R7` | 0.06 / 0.12 dB |
| `3LPn-gained` | **2.64 dB** |

The `3LPn-gained` figure is entirely D3, and only with a marginal op-amp
(GBW ≈ 40× f₀ on a Q peaking 37 dB). Same cell with TL072: 0.044 dB; OPA1656:
0.003 dB. **Expect 3rd-order LP-notch gained BOMs to shift when a low-GBW op-amp
is selected** — the old model was most wrong exactly there.

**Design guidance, unchanged in substance but now visible in the tool:** a
unity-gain Sallen-Key low-pass leaks its stopband through C4 in proportion to the
op-amp's open-loop output impedance. Where deep wideband rejection matters, pick a
low-Ro / high-GBW part, or move the section to MFB — the same conclusion
`AM_NONIDEAL_ANALYSIS.md` §5 reaches for the AM family's HF hump.
