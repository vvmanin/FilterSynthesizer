# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

import numpy as np
import pandas as pd

def clean_roots(roots, tol=1e-12):
    """Snaps numerical fuzz (e.g., 1e-16) to pure 0.0 for clean polynomials."""
    cleaned = []
    for r in roots:
        real_p = r.real if abs(r.real) > tol else 0.0
        imag_p = r.imag if abs(r.imag) > tol else 0.0
        cleaned.append(complex(real_p, imag_p))
    return np.array(cleaned)

def format_val(val, scale_type):
    """Formats numbers for Pandas tables (standard programming notation)."""
    if scale_type == "Normalized":
        return f"{val:.6f}"
    return f"{val:.6e}"

def format_latex_val(val, scale_type="Normalized"):
    """Generic LaTeX number formatter that cleanly handles exponents."""
    if val == 0: return "0"
    abs_val = abs(val)
    sign = "-" if val < 0 else ""
    
    if 1e-4 <= abs_val <= 1e4:
        return f"{sign}{abs_val:.6f}"
        
    # Split the scientific notation and cast exponent to int to drop leading zeros!
    base, exp_str = f"{abs_val:.4e}".split('e')
    return f"{sign}{base} \\cdot 10^{{{int(exp_str)}}}"


def poly_to_terms(poly_array, scale_type="Normalized"):
    """Polynomial -> LIST of LaTeX terms, aggressively pruning zero-terms.

    This is the term-level source of truth for both the on-screen string
    (poly_to_latex joins with a space) and the PDF report, which packs the same
    terms into lines so a long transfer function wraps at +/- boundaries
    instead of running off the page. Splitting a term is never allowed, so the
    report can break lines without ever hiding part of the expression.
    """
    degree = len(poly_array) - 1
    terms = []

    for i, coef in enumerate(poly_array):
        power = degree - i

        if abs(coef) < 1e-10:
            continue

        abs_val = abs(coef)
        sign = "-" if coef < 0 else "+"

        if 1e-4 <= abs_val <= 1e4:
            val_str = f"{abs_val:.6f}"
        else:
            base, exp_str = f"{abs_val:.4e}".split('e')
            val_str = f"{base} \\cdot 10^{{{int(exp_str)}}}"

        if not terms and sign == "+":
            sign = ""

        if power == 0:
            var_str = ""
        elif power == 1:
            var_str = "s"
        else:
            var_str = f"s^{{{power}}}"

        if abs(abs_val - 1.0) < 1e-10 and power > 0:
            combined_term = var_str
        else:
            if power > 0:
                combined_term = f"{val_str} \\cdot {var_str}"
            else:
                combined_term = val_str

        if terms:
            terms.append(f"{sign} {combined_term}")
        else:
            terms.append(f"{sign}{combined_term}" if sign == "-" else combined_term)

    return terms or ["0"]


def poly_to_latex(poly_array, scale_type="Normalized"):
    """Converts a polynomial array into a beautifully formatted LaTeX string, aggressively pruning zero-terms."""
    return " ".join(poly_to_terms(poly_array, scale_type))


def roots_to_biquad_factors(roots, scale_type="Normalized"):
    """Roots -> LIST of bracketed biquad/real factors, e.g.
    ["(s^{2} + 1.2 \\cdot s + 3.4)", "(s + 5.6)"].

    A 3rd-order section therefore prints its real pole in its own bracket.
    roots_to_biquad_latex() is the concatenation of this list; the PDF
    report wraps between factors so long products never overflow a line."""
    if len(roots) == 0:
        return ["1"]
        
    clean_r = []
    for r in roots:
        real_part = 0.0 if abs(r.real) < 1e-10 else r.real
        imag_part = 0.0 if abs(r.imag) < 1e-10 else r.imag
        clean_r.append(complex(real_part, imag_part))
        
    clean_r = sorted(clean_r, key=lambda x: (abs(x.imag), x.real))
    
    def format_val(val):
        abs_val = abs(val)
        if 1e-4 <= abs_val <= 1e4:
            return f"{abs_val:.6f}"
        base, exp_str = f"{abs_val:.4e}".split('e')
        return f"{base} \\cdot 10^{{{int(exp_str)}}}"
    
    biquads = []
    i = 0
    while i < len(clean_r):
        r1 = clean_r[i]
        
        if abs(r1.imag) > 1e-10:
            w0_sq = r1.real**2 + r1.imag**2
            two_zeta_w0 = -2.0 * r1.real
            term = "s^{2}"
            
            if abs(two_zeta_w0) > 1e-10:
                sign = " - " if two_zeta_w0 < 0 else " + "
                if abs(abs(two_zeta_w0) - 1.0) < 1e-10:
                    term += f"{sign}s"
                else:
                    term += f"{sign}{format_val(two_zeta_w0)} \\cdot s"
                    
            if abs(w0_sq) > 1e-10:
                term += f" + {format_val(w0_sq)}"
                
            biquads.append(f"({term})")
            i += 2
            
        else:
            p0 = -r1.real
            term = "s"
            if abs(p0) > 1e-10:
                sign = " - " if p0 < 0 else " + "
                if abs(abs(p0) - 1.0) < 1e-10:
                    term += f"{sign}1"
                else:
                    term += f"{sign}{format_val(p0)}"
                
            biquads.append(f"({term})")
            i += 1
            
    return biquads


def roots_to_biquad_latex(roots, scale_type="Normalized"):
    """Converts a list of roots into a cascaded Biquad LaTeX string, aggressively pruning zero-terms."""
    return "".join(roots_to_biquad_factors(roots, scale_type))

def build_coeff_table(num_coeffs, den_coeffs, k_val, scale_type, include_k):
    """Builds a strictly formatted Pandas DataFrame for the coefficients."""
    max_deg = max(len(num_coeffs), len(den_coeffs)) - 1
    
    num_pad = [0.0] * (max_deg + 1 - len(num_coeffs)) + list(num_coeffs)
    den_pad = [0.0] * (max_deg + 1 - len(den_coeffs)) + list(den_coeffs)
    
    idx = [f"s^{max_deg - i}" if (max_deg - i) > 0 else "s^0" for i in range(max_deg + 1)]
    
    main_dict = {
        "Numerator Coefficients": [format_val(c, scale_type) for c in num_pad],
        "Denominator Coefficients": [format_val(c, scale_type) for c in den_pad]
    }
    df = pd.DataFrame(main_dict, index=idx)
    
    if include_k:
        k_dict = {
            "Numerator Coefficients": [format_val(k_val, scale_type)],
            "Denominator Coefficients": ["-"]
        }
        k_df = pd.DataFrame(k_dict, index=["K"])
        df = pd.concat([df, k_df])
        
    return df

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