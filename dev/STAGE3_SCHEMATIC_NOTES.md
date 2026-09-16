# Stage 3 — schematics + QE frontier widening

Eleven files changed (`schematic_svg.py` added this round). Self-test passes.

---

## 1. Schematic wiring — four new canvases

`svg_filename()` now resolves each cell to the exact file you supplied:

```
2BP1HP-MFB      -> 2BP1HP-MFB.drawio.svg
2BP1HP-MFB-QE   -> 2BP1HP-MFB-QE.drawio.svg
2BP1LP-MFB      -> 2BP1LP-MFB.drawio.svg
2BP1LP-MFB-QE   -> 2BP1LP-MFB-QE.drawio.svg
```

Anchor maps (`ANCHORS_MFB_BP3_HP` / `ANCHORS_MFB_BP3_LP`) inherit the 2BP-MFB
core and add your measured input-network positions:

```python
ANCHORS_MFB_BP3_HP = {**ANCHORS_MFB_BP, "C0": (150, 185, "middle")}
ANCHORS_MFB_BP3_LP = {**ANCHORS_MFB_BP, "R0": (120, 182, "middle"),
                                        "C0": (215, 410, "middle")}
```

**One thing to check on screen:** the inherited core anchors (R1–R5, C1, C2, U)
still carry the values marked `PLACEHOLDER` in `ANCHORS_MFB_BP` — that comment
predates this work. Your C0/R0 numbers are real. If a label sits off its part on
the new artwork, it is the core anchors that need re-measuring, not C0/R0. I
flagged this in the code beside the maps.

Verified label emission:

| cell | designators drawn |
|---|---|
| `2BP1HP-MFB` | R1 R2 R3 · C1 C2 **C0** · U1 |
| `2BP1LP-MFB-QE` | R1 R2 R3 R4 R5 **R0** · C1 C2 **C0** · U1 |
| `2BP-MFB` (regression) | R1 R2 R3 · C1 C2 · U1 — unchanged |

The QE divider (R4/R5) auto-suppresses on the plain cells via `_present()`, as
with every other family.

**Single source of truth.** The `C3→C0` / `R6→R0` map now lives once, in
`schematic_svg.bp3_alias()`. `topology_tab._bp3_alias` delegates to it, so the
BOM table, the per-part list, the Sort-by menu and the drawn schematic cannot
drift apart. The label loop in `build_annotations` applies the alias and looks
the anchor up by *designator* — an empty alias for every other cell leaves those
paths byte-identical.

## 2. `_r5_is_feedback` widened — your condition holds

Widened to exactly the three Q-enhancement families, mirroring
`rescale_isolated_r5r6`:

```
LP-MFB + qe (not notch)   divider {R5, R6}
HP-MFB + qe (not notch)   divider {R4, R5}
BP-MFB + qe               divider {R4, R5}
```

Your condition was: *do it if it means the feedback ratio ends up set for the
best sensitivity the allowed R spread permits.* It does, and here is why the
implication holds. Excluding these cells from the frontier makes the `top_k`
prune order them **purely by `sens_score`**. Sensitivity on a QE cell is
monotone in the enhancement factor `E = Q/Q_passive` (every log-derivative of the
damping picks up `beta/(beta-Delta) = E`), while the spread law relaxes as
`4(Q/E)²`. So "lowest sens_score among solutions that passed the spread guard"
*is* "smallest positive-feedback ratio that fits the allowed spread".

Measured:

| pool | before | after |
|---|---|---|
| LP-MFB Q=3 | 2.460 — all QE | **2.038 — all plain** (−17%) |
| LP-MFB Q=8 | 2.489 — all QE | 2.489 — all QE *(spread forces QE)* |
| HP-MFB Q=3 | 1.341 — 11 QE + 1 plain | 1.341 — **all plain** |
| HP-MFB Q=8 | 8.620 — all QE | 8.620 — all QE *(spread forces QE)* |
| BP-MFB Q=6 | 1.213 — 8 QE + 4 plain | 1.213 — **all plain** |

Two distinct wins: LP-MFB Q=3 gets a genuinely better optimum, and the HP/BP
Q=3–6 pools go from offering one low-sensitivity option buried among QE rows to
offering twelve. At high Q the QE cell still wins — correctly, because the plain
cell's spread no longer fits. That is the hand-off working as intended.

**Deliberately not widened**, with reasons in the docstring: VCVS `NOTCH`
(free-scale trio, but never pooled with an R5-less twin, and it is the family the
frontier was written for), `NOTCH-MFB` (free-scale pair is {R1,R4}; R5 is the
real m→gnd gain leg), `AM` (free DOF is the matched pair R7=R8; R5 is a real
integrator resistor), `LP-MFB LS` ({R2,R6,R7}), and the VCVS LP/HP notch cells
(R5 *is* the derived feedback resistor — the low-R5/HF-hump trade).

Regression confirms the scoping: `2LPn-MFB`, `2N-MFB`, `2BP VCVS`, `3HP-MFB` and
the `2LPn-gained`/`+R7` pool are **byte-identical**; only `3LP-MFB` moved
(`3LP-MFB-QE` → `3LP-MFB`, sens 2.460 → 2.040), which is the intended effect.

## 3. Pairing — untouched, as requested.

---

## Remaining

Core anchor verification on the new canvases (see §1). Everything else from the
original four-stage plan is done.
