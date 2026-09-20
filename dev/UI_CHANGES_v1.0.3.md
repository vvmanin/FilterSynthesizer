# UI changes for v1.0.3 — work order

Four edits across three files, none applied yet. Do them in this order; the
last two depend on the first two being in place.

| # | File | Change |
|---|---|---|
| 1 | `tf_utils.py` | add the line-wrapping API (new code, nothing replaced) |
| 2 | `app.py` — Tab 2 | use it for the three `st.latex` calls |
| 3 | `app.py` — CSS block | wide-screen width caps |
| 4 | `topology_tab.py` | drop `reg_weight` from the UI, rename the two tolerances to percent |

Then: bump the version, run the docs loop, tag and release.

---

## 1. `tf_utils.py` — the wrapping API

Add at the end of the file. Nothing existing changes: `poly_to_terms()` and
`roots_to_biquad_factors()` are already the term-level source of truth, and
this is the screen's half of what `report_pdf.py` already does for print.

```python
import re   # if not already imported at the top


# =====================================================================
#  LINE WRAPPING  (shared vocabulary with the PDF report)
#
#  report_pdf.py packs poly_to_terms() into lines so a long transfer
#  function wraps at +/- boundaries instead of running off the page. The
#  screen had no equivalent: poly_to_latex() joins every term into one
#  unbreakable line, so an 11th-order denominator is simply clipped at the
#  container edge. These helpers give st.latex() the same treatment.
#
#  A term is NEVER split, so a wrapped expression can never hide part of
#  itself — the same rule the report follows.
# =====================================================================
_CMD = re.compile(r"\\[a-zA-Z]+")
_SUP = re.compile(r"\^\{([^}]*)\}")


def term_width(term):
    """Rough rendered width of one LaTeX term, in character widths.

    KaTeX metrics are not reachable from Python, so this is a proxy: a
    control sequence collapses to one glyph, a superscript counts at 70 %
    (it is drawn small), braces cost nothing. It never has to be exact —
    terms are atomic, so a bad estimate costs a slightly uneven line break
    and nothing else.
    """
    t = _SUP.sub(lambda m: "x" * max(1, round(len(m.group(1)) * 0.7)), term)
    t = _CMD.sub("·", t)
    return len(t.replace("{", "").replace("}", "").replace(" ", ""))


def pack_terms(terms, max_width=72, sep=" "):
    """Group terms into lines of about `max_width`, never splitting a term.

    A single term wider than the budget gets a line of its own rather than
    being broken. Continuation lines start with the term's own +/- sign,
    because poly_to_terms() already carries it.

    `sep` must match how the flat form joins the same terms — " " for
    poly_to_latex(), "" for roots_to_biquad_latex(), which concatenates.
    That is what makes re-joining the lines reproduce the flat string
    character for character; see the round-trip test in verify.py.
    """
    lines, cur, w = [], [], 0
    for t in terms:
        tw = term_width(t)
        if cur and w + tw > max_width:
            lines.append(sep.join(cur))
            cur, w = [t], tw
        else:
            cur.append(t)
            w += tw
    if cur:
        lines.append(sep.join(cur))
    return lines or ["0"]


def poly_to_latex_lines(poly_array, scale_type="Normalized", max_width=72):
    """The polynomial as wrapped LaTeX rows (one row when it already fits)."""
    return pack_terms(poly_to_terms(poly_array, scale_type), max_width, sep=" ")


def roots_to_biquad_lines(roots, scale_type="Normalized", max_width=72):
    """The factored form as wrapped rows, breaking between whole factors."""
    return pack_terms(roots_to_biquad_factors(roots, scale_type), max_width, sep="")


def stack_lines(lines):
    """Rows -> one centred KaTeX block. A single row is returned unchanged,
    so anything that already fits renders exactly as it does today.

    `array` rather than `gathered`: it is the most widely supported
    environment in KaTeX, and a parse failure here would replace the whole
    transfer function with a red error box.
    """
    if len(lines) <= 1:
        return lines[0] if lines else "0"
    return r"\begin{array}{c}" + r" \\ ".join(lines) + r"\end{array}"


def tf_latex(num_lines, den_lines, k_latex=None, lhs="H(s)"):
    """Assemble a (wrapped) transfer function for st.latex()."""
    head = f"{lhs} = " + (f"{k_latex} \\cdot " if k_latex else "")
    return head + r"\frac{" + stack_lines(num_lines) + "}{" + stack_lines(den_lines) + "}"
```

---

## 2. `app.py` — Tab 2, the three transfer-function forms

Add to the existing `tf_utils` import:

```python
poly_to_latex_lines, roots_to_biquad_lines, tf_latex
```

Then, in the three expanders, change **only the `st.latex(...)` line**:

```python
        # --- FORM 1: Expanded (K Outside) ---
        with st.expander("Expanded Form (Isolated Gain Constant)", expanded=True):
            num_latex_1 = poly_to_latex(num_monic, scale_type)
            den_latex_1 = poly_to_latex(den_monic, scale_type)

            tf_1 = f"H(s) = {k_latex} \\cdot \\frac{{{num_latex_1}}}{{{den_latex_1}}}"
            st.latex(tf_latex(poly_to_latex_lines(num_monic, scale_type),
                              poly_to_latex_lines(den_monic, scale_type),
                              k_latex=k_latex))
            st.code(tf_1, language="latex")     # one line, on purpose — see below
            st.dataframe(build_coeff_table(num_monic, den_monic, k_disp, scale_type,
                                           include_k=True), use_container_width=True)

        # --- FORM 2: Expanded (K Distributed) ---
        with st.expander("Expanded Form (Distributed Gain Constant)", expanded=False):
            num_dist = num_monic * k_disp
            num_latex_2 = poly_to_latex(num_dist, scale_type)

            tf_2 = f"H(s) = \\frac{{{num_latex_2}}}{{{den_latex_1}}}"
            st.latex(tf_latex(poly_to_latex_lines(num_dist, scale_type),
                              poly_to_latex_lines(den_monic, scale_type)))
            st.code(tf_2, language="latex")
            st.dataframe(build_coeff_table(num_dist, den_monic, k_disp, scale_type,
                                           include_k=False), use_container_width=True)

        # --- FORM 3: Factored (Biquad) Form ---
        with st.expander("Factored Form (Cascaded Biquads)", expanded=False):
            num_factored = roots_to_biquad_latex(clean_z, scale_type)
            den_factored = roots_to_biquad_latex(clean_p, scale_type)

            tf_3 = f"H(s) = {k_latex} \\cdot \\frac{{{num_factored}}}{{{den_factored}}}"
            st.latex(tf_latex(roots_to_biquad_lines(clean_z, scale_type),
                              roots_to_biquad_lines(clean_p, scale_type),
                              k_latex=k_latex))
            st.code(tf_3, language="latex")
            st.dataframe(build_coeff_table(num_monic, den_monic, k_disp, scale_type,
                                           include_k=True), use_container_width=True)
```

**The `st.code()` blocks stay single-line deliberately.** They exist to be
copied into a paper, a notebook or Mathematica — an `array` environment would
make that paste worse, not better. Wrapped for reading, flat for copying.

---

## 2a. The guarantee on the copy-paste string

`tf_1` / `tf_2` / `tf_3` are built by `poly_to_latex()` and
`roots_to_biquad_latex()`, which are **not touched** by this change — the new
functions sit beside them, nothing is rewritten in terms of them. The copy
string is therefore produced by exactly the same code as before.

Stronger than that: wrapping is provably presentation-only. Re-joining the
wrapped lines reproduces the flat string character for character, which makes
the guarantee testable instead of a promise. Add to `verify.py`:

```python
def test_wrapping_is_presentation_only():
    """Wrapped display lines must re-join into the exact copy-paste string.

    The user reads a wrapped H(s) but copies a flat one; those two must never
    be allowed to drift apart. `sep` is what makes this exact — poly_to_latex
    joins terms with a space, roots_to_biquad_latex concatenates them.
    """
    import numpy as np
    from tf_utils import (poly_to_latex, poly_to_latex_lines,
                          roots_to_biquad_latex, roots_to_biquad_lines)

    polys = [
        np.array([1.0]),                                   # constant
        np.array([1.0, 1.0]),                              # 1st order
        np.array([1.0, 1.414214, 1.0]),                    # 2nd order
        np.array([1.0, -2.5, 3.0, -0.125]),                # negative coeffs
        np.array([1.0, 1.2e-6, 3.4e5, 9.9e9]),             # scientific notation
        np.array([1.0, 0.0, 0.0, 5.0]),                    # pruned interior zeros
        np.concatenate([[1.0], np.linspace(0.5, 9.5, 20)]),  # 20th order
    ]
    for p in polys:
        assert " ".join(poly_to_latex_lines(p)) == poly_to_latex(p), p

    roots = [-0.2+0.97j, -0.2-0.97j, -0.55+0.7j, -0.55-0.7j, -0.9+0j]
    assert "".join(roots_to_biquad_lines(roots)) == roots_to_biquad_latex(roots)
```

Verified over those cases plus the real 10th/11th-order pair from a Butterworth
band-reject: **every round-trip exact**, and all eleven flat strings parse as
valid standalone LaTeX under KaTeX with `throwOnError`.

---

## 3. `app.py` — CSS block (after the existing rule 5)

```css
    /* 6. Wide screens: forms should not stretch with the plots. Panels get a
          readable cap; tables, schematics and Bode plots live outside
          expanders and stay full width. Long transfer functions now WRAP
          (tf_utils.pack_terms), so Tab 2 needs no exemption — the overflow
          rule below is only a safety net for a single pathological term. */
    [data-testid="stExpander"] {
        max-width: 1100px;
    }
    [data-testid="stExpander"] .katex-display {
        overflow-x: auto;
        overflow-y: hidden;
        padding-bottom: 0.4rem;
    }
    /* Cap the input BOX, not the whole widget — capping the widget would
       force a long label such as "Pole & notch frequency tolerance (%)"
       to wrap onto two lines. */
    div[data-testid="stNumberInput"] div[data-baseweb="input"] {
        max-width: 280px;
    }
    /* The sidebar keeps its own width. */
    [data-testid="stSidebar"] [data-testid="stExpander"],
    [data-testid="stSidebar"] div[data-testid="stNumberInput"] div[data-baseweb="input"] {
        max-width: none;
    }
```

Scoped by overriding the sidebar rather than by naming the main container,
because that test id was renamed between Streamlit versions
(`section.main` -> `stMain`) and the override works either way.

If lines still overflow on a laptop, lower `max_width` in
`poly_to_latex_lines` from 72 — it is one number and it is the only knob.

---

## 4. `topology_tab.py` — Convergence Settings

Add the constant above `_convergence_inputs()` and replace the function:

```python
# Regularization weight for the non-ideal pre-distortion solve. Fixed at the
# former UI default — a solver-internal knob with no user-facing meaning, and
# ignored entirely in ideal mode. Still visible (read-only) in each section's
# "exact solver call" expander, since it rides in cfg.
REG_WEIGHT = 0.02


def _convergence_inputs():
    """Global search settings.

    The two tolerances are ENTERED AS PERCENT and RETURNED AS FRACTIONS: the
    user reads 1.00, the solver gets 0.01. The widget keys carry a `_pct`
    suffix so a value left over under the old fraction-based keys can never be
    re-read as a percent — 0.01 would silently become a 100x tighter gate.
    """
    with st.expander("Convergence Settings", expanded=False):
        level = st.select_slider(
            "Search thoroughness", options=list(SEARCH_PRESETS.keys()),
            value="Balanced", key="hw_effort",
            help="Breadth of the brute-force search (multistarts, minima kept, hints). "
                 "Higher finds more candidate BOMs but is slower.")
        c = st.columns(2)
        with c[0]:
            pole_tol_pct = st.number_input(
                "Pole & notch frequency tolerance (%)", value=1.0,
                min_value=0.0, max_value=100.0, step=0.1, format="%.2f",
                key="hw_pole_tol_pct",
                help="How far a candidate's realized pole frequency f₀ — and, on a notch "
                     "section, its notch frequency f_z — may sit from the target. "
                     "1.00 means ±1 %. Applies to the parallel-C2 (notch) search path. "
                     "Q is not constrained by this.")
        with c[1]:
            gain_tol_pct = st.number_input(
                "Passband gain tolerance (%)", value=0.5,
                min_value=0.0, max_value=100.0, step=0.1, format="%.2f",
                key="hw_gain_tol_pct",
                help="How far a candidate's realized passband gain may sit from the "
                     "target. 0.50 means ±0.5 %. DC gain for low-pass and notch cells, "
                     "HF gain for high-pass.")
        top_k = st.number_input(
            "Max candidates to refine", min_value=1, max_value=500, value=30, step=5,
            key="hw_topk",
            help="Only the best N ideal solutions (by sensitivity) are op-amp pre-distorted "
                 "and snapped — the dominant cost for gained/notch cells. Lower = much faster, "
                 "fewer BOMs listed.")
    return dict(SEARCH_PRESETS[level],
                pole_tol=pole_tol_pct / 100.0,
                gain_tol=gain_tol_pct / 100.0,
                reg_weight=REG_WEIGHT, top_k=top_k)
```

Why the names changed:

- `pole_tol` gates the realized **pole frequency f₀ and, on notch sections, the
  notch frequency f_z** (`zero_manifold_solver.solve_one_combo`) — both on the
  same tolerance, and it does **not** constrain Q. "Pole frequency tolerance"
  would be wrong precisely where the control is live, since it only acts on the
  parallel-C2 notch path.
- `gain_tol` covers DC gain for low-pass/notch and HF gain for high-pass (it is
  published into `cfg` for `_assemble()`'s HF-gain gate), so "Passband gain
  tolerance" is more accurate than the current "DC-gain tolerance" help text.

---

## Verification

```bat
grep -rn "hw_reg" *.py           REM must return nothing
python -m streamlit run app.py
```

Check, in order:

1. **Tab 2** — a 5th-order filter renders on one line, exactly as before; a
   10th-order elliptic band-reject wraps in the numerator and denominator with
   nothing clipped. The `st.code` block below it is still one line.
2. **Tab 4** — Convergence Settings shows three controls in a panel that stops
   around 1100 px; the two tolerance boxes read `1.00` and `0.50`.
3. **A section's 🔧 exact solver call** — `pole_tol` shows `0.01` and
   `gain_tol` shows `0.005`. That is correct: the diagnostic reports solver
   units. Worth one sentence in the manual so nobody files it as a bug.
4. **Sidebar** — unchanged width, inputs still fill it.
5. **Re-solve a section** that was solved before the change. It should come
   back from cache instantly: `0.5/100.0` and `1.0/100.0` land on the same
   doubles as the old literals `0.005` and `0.01`, so `_job_sig`'s hash is
   unchanged.

---

## Then: release

```bat
python docs\manual\tools\ui_inventory.py
python docs\manual\tools\doc_drift.py
```

Expect `hw_pole_tol`, `hw_gain_tol` and `hw_reg` REMOVED, the two `_pct` keys
ADDED, and **APP CSS CHANGED** from the style-block hash. That last line is the
drift checker earning its place: a CSS-only edit moves no control but restyles
every figure, and nothing else would have noticed.

Then bump `_version.py` to **1.0.3**, capture the figures, build the PDFs, tag
and release. Write the release notes to cover the unreleased 1.0.2 changes as
well — 1.0.1 is the last public tag, so anyone upgrading receives both sets at
once and the notes are the only place that says so.
