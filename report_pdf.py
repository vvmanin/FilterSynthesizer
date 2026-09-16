# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""
report_pdf.py — A4 synthesis report builder for the Filter Synthesizer.

PURE module: imports numpy / matplotlib / reportlab / cairosvg only. It never
imports streamlit and never touches st.session_state, so it can be exercised
headlessly (see `python report_pdf.py --selftest`). The Streamlit side lives in
report_ui.py, which assembles the context dict described below and calls
`build_report(ctx, opts) -> bytes`.

Why matplotlib rather than plotly+kaleido
-----------------------------------------
The report has a *print* layout ("fitted to half page, ratio preserved"), which
means the figure has to be drawn at an exact physical size. Re-plotting at the
target size gives vector output straight into the PDF, needs no bundled
Chromium (kaleido is ~80 MB and fights PyInstaller), and brings DejaVuSans
along for the Unicode glyphs ReportLab's built-in Helvetica cannot draw
(Ω is not in WinAnsi; σ, ∥, ₀ likewise).

CONTEXT SCHEMA (everything is plain Python / numpy — no streamlit objects)
-------------------------------------------------------------------------
ctx = {
  "meta":   {"title", "author", "project", "date", "tool_version", "design_hash"},
  "spec":   [(label, value_str), ...],            # ordered input-parameter rows
  "engine": {"poles": ndarray(rad/s), "zeros": ndarray(rad/s),
             "k": float, "gain_units": float, "w_norm": float},
  "stages": [ {"stage_num", "poles": [complex rad/s], "zeros": [complex rad/s],
               "f0_hz", "Q", "fz_hz", "f1_hz", "K_radps", "peak_mag",
               "order", "notch"} , ...],
  "sections": [ {"n", "topology", "kind", "svg" (str|None), "row", "cont_row",
                 "opamp_name", "opamp_params" (dict|None), "env" (dict),
                 "metrics": {"sens", "snap_cost", "dc", "dc_label", "dc_target"}},
                ... ],                             # one per picked section
  "bode":   {"f": ndarray(Hz), "ideal": complex[], "realized": complex[],
             "mc": dict|None, "mc_params": dict|None,
             "ideal_gd": ndarray|None, "realized_gd": ndarray|None,
             "marks": [(f_hz, "f0"|"fz")], "passband": (f_lo, f_hi)|None},
  "warnings": [str, ...],
}

opts = {  # report checkboxes; every key optional, default False
  "cover", "coeff_table", "metrics", "mc", "phase_gd",
  "normalized_roots", "passband_zoom", "warnings",
}
"""

import io
import os
import datetime

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import mathtext
from matplotlib.font_manager import FontProperties, findfont
from matplotlib.lines import Line2D
from matplotlib import ticker

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph,
                                Spacer, Table, TableStyle, Image, PageBreak,
                                NextPageTemplate, KeepTogether)

# Single source of truth for component value strings — the same helpers the
# Topology tab and the schematic annotator use, so the PDF can never drift from
# the screen.
from discrete_snapper import _fmt_cap, _fmt_res
from schematic_svg import bp3_alias
try:
    from schematic_svg import sanitize_svg as _sanitize_svg
except ImportError:                       # schematic_svg not patched yet
    _sanitize_svg = None
import tf_utils as TU


# =====================================================================
#  0.  FONTS
# =====================================================================
# DejaVuSans ships inside matplotlib, so registering it with ReportLab costs no
# extra bundled data file and guarantees Ω / µ / σ / ∥ / ₀ render.
_MPL_TTF = os.path.join(os.path.dirname(matplotlib.__file__),
                        "mpl-data", "fonts", "ttf")

FONT = "DejaVuSans"
FONT_B = "DejaVuSans-Bold"
FONT_I = "DejaVuSans-Oblique"
_FONTS_READY = False


def _register_fonts():
    global _FONTS_READY, FONT, FONT_B, FONT_I
    if _FONTS_READY:
        return
    try:
        for name, fname in ((FONT, "DejaVuSans.ttf"),
                            (FONT_B, "DejaVuSans-Bold.ttf"),
                            (FONT_I, "DejaVuSans-Oblique.ttf")):
            path = os.path.join(_MPL_TTF, fname)
            if not os.path.exists(path):                 # fall back to whatever
                path = findfont(FontProperties(family="DejaVu Sans"))
            pdfmetrics.registerFont(TTFont(name, path))
        pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_B,
                                      italic=FONT_I, boldItalic=FONT_B)
    except Exception:                                    # last-ditch: core fonts
        FONT, FONT_B, FONT_I = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
    _FONTS_READY = True


matplotlib.rcParams["mathtext.fontset"] = "dejavusans"
matplotlib.rcParams["font.family"] = "DejaVu Sans"
matplotlib.rcParams["axes.unicode_minus"] = False


# =====================================================================
#  1.  PAGE GEOMETRY / STYLES
# =====================================================================
MARGIN = 15 * mm
PW, PH = A4                                     # 595.3 x 841.9 pt
CONTENT_W = PW - 2 * MARGIN                     # ~511 pt
CONTENT_H = PH - 2 * MARGIN - 22                # minus footer band
LW, LH = landscape(A4)
CONTENT_WL = LW - 2 * MARGIN
CONTENT_HL = LH - 2 * MARGIN - 22
HALF_H = CONTENT_H * 0.48                       # "half page" target for figures

# Section palette — identical hex values to plot_utils.plot_mnemoscheme_map so
# the printed pole-zero map colours match the screen exactly.
STAGE_COLORS = ['#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
                '#8c564b', '#e377c2', '#bcbd22', '#17becf']
C_IDEAL = "#1f6fb2"       # hw_plots._C_IDEAL
C_REAL = "#d1495b"        # hw_plots._C_REAL
C_BAND = "#788296"        # hw_plots._C_BAND, opaque equivalent
C_MEDIAN = "#5a6478"
C_GD_I = "#2e8b57"
C_GD_R = "#9aa648"
GREY = colors.HexColor("#777777")


def _styles():
    _register_fonts()
    base = ParagraphStyle("body", fontName=FONT, fontSize=8.2, leading=10.6,
                          alignment=TA_LEFT, textColor=colors.black)
    return {
        "body": base,
        "small": ParagraphStyle("small", parent=base, fontSize=7.0, leading=8.8),
        "tiny": ParagraphStyle("tiny", parent=base, fontSize=6.3, leading=7.8,
                               textColor=GREY),
        "h1": ParagraphStyle("h1", parent=base, fontName=FONT_B, fontSize=15,
                             leading=18, spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=base, fontName=FONT_B, fontSize=10.5,
                             leading=13, spaceBefore=7, spaceAfter=3),
        "h3": ParagraphStyle("h3", parent=base, fontName=FONT_B, fontSize=8.8,
                             leading=11, spaceBefore=5, spaceAfter=2),
        "cap": ParagraphStyle("cap", parent=base, fontSize=6.8, leading=8.4,
                              textColor=GREY),
        "ctr": ParagraphStyle("ctr", parent=base, alignment=TA_CENTER),
        "coverT": ParagraphStyle("coverT", parent=base, fontName=FONT_B,
                                 fontSize=26, leading=31, alignment=TA_CENTER),
        "coverS": ParagraphStyle("coverS", parent=base, fontSize=12, leading=17,
                                 alignment=TA_CENTER, textColor=GREY),
    }


def _table_style(head=True, grid=colors.HexColor("#c8c8c8")):
    cmds = [("FONT", (0, 0), (-1, -1), FONT, 7.4),
            ("GRID", (0, 0), (-1, -1), 0.4, grid),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2.0),
            ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3.5)]
    if head:
        cmds += [("FONT", (0, 0), (-1, 0), FONT_B, 7.4),
                 ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eceff3"))]
    return TableStyle(cmds)


# =====================================================================
#  2.  NUMBER / VALUE FORMATTING
# =====================================================================
def _eng(v, sig=4):
    """Compact engineering-ish scalar for tables."""
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v == 0:
        return "0"
    a = abs(v)
    if 1e-3 <= a < 1e5:
        return f"{v:.{sig}g}"
    return f"{v:.{max(sig - 1, 2)}e}"


def _hz(v):
    if v is None:
        return "—"
    v = float(v)
    if v >= 1e9:
        return f"{v/1e9:.6g} GHz"
    if v >= 1e6:
        return f"{v/1e6:.6g} MHz"
    if v >= 1e3:
        return f"{v/1e3:.6g} kHz"
    return f"{v:.6g} Hz"


def _ohm_band(lo_ohm, hi_ohm):
    """(min,max) in Ω -> '0…1 MΩ' / '>1 MΩ' for the Monte-Carlo caption."""
    def s(x):
        if x >= 1e12:
            return "∞"
        if x >= 1e6:
            return f"{x/1e6:g}M"
        if x >= 1e3:
            return f"{x/1e3:g}k"
        return f"{x:g}"
    if hi_ohm >= 1e12:
        return f">{s(lo_ohm)}Ω"
    return f"{s(lo_ohm)}…{s(hi_ohm)}Ω"


# =====================================================================
#  3.  MATH TYPESETTING  (mathtext -> vector PDF image -> flowable)
# =====================================================================
# The whole-filter TF of a 10th-order elliptic band-reject is an 11-term
# denominator of ~400 characters. It cannot fit one A4 line at any readable
# size, so terms are packed greedily into lines and broken ONLY at +/- term
# boundaries (never mid-term), with the operator repeated at the head of the
# continuation line. Nothing is ever clipped: the block grows downward and, if
# it still overflows the frame, the font steps down before anything is dropped.

_MT_PARSER = mathtext.MathTextParser("path")


def _math_metrics(latex, fs):
    """(width, height, depth) in points for a mathtext fragment at `fs`."""
    try:
        w, h, d, *_ = _MT_PARSER.parse(f"${latex}$", dpi=72,
                                       prop=FontProperties(size=fs))
        return float(w), float(h), float(d)
    except Exception:
        return 0.62 * fs * len(latex), 1.25 * fs, 0.28 * fs


def _pack_terms(terms, max_w, fs, sep=" "):
    """Greedy line packing. Returns [latex_line, ...]; never splits a term."""
    if not terms:
        return ["0"]
    sep_w = _math_metrics(r"\;", fs)[0] if sep else 0.0
    lines, cur, cur_w = [], [], 0.0
    for t in terms:
        tw = _math_metrics(t, fs)[0]
        add = tw + (sep_w if cur else 0.0)
        if cur and cur_w + add > max_w:
            lines.append(sep.join(cur))
            cur, cur_w = [t], tw
        else:
            cur.append(t)
            cur_w += add
    if cur:
        lines.append(sep.join(cur))
    return lines


def _math_block_image(lhs, num_lines, den_lines, width, fs, dpi=300):
    r"""Render 'lhs  num/den' as a fraction with a rule, as a reportlab Image.

    `lhs` is a mathtext fragment (e.g. r'H(s) = 1.23 \cdot 10^{4} \;\cdot\;'),
    `num_lines` / `den_lines` are pre-packed mathtext lines.
    """
    pad, gap, bar_gap = 3.0, 3.5, 4.5
    lm = [_math_metrics(s, fs) for s in num_lines]
    ld = [_math_metrics(s, fs) for s in den_lines]
    lw, lh, ldp = _math_metrics(lhs, fs) if lhs else (0.0, 0.0, 0.0)

    num_h = sum(m[1] for m in lm) + gap * max(len(lm) - 1, 0)
    den_h = sum(m[1] for m in ld) + gap * max(len(ld) - 1, 0)
    H = pad + num_h + bar_gap + 1.0 + bar_gap + den_h + pad
    H = max(H, lh + 2 * pad)
    W = float(width)

    fig = plt.figure(figsize=(W / 72.0, H / 72.0), dpi=dpi)
    fig.patch.set_alpha(0.0)

    x0 = (lw + 6.0) if lhs else 0.0
    col_w = max(W - x0 - 2.0, 10.0)
    xc = x0 + col_w / 2.0

    y = H - pad
    for s, (w, h, d) in zip(num_lines, lm):
        fig.text((xc - w / 2.0) / W, (y - (h - d)) / H, f"${s}$",
                 fontsize=fs, ha="left", va="baseline", color="black")
        y -= (h + gap)
    y += gap
    bar_y = y - bar_gap
    fig.add_artist(Line2D([x0 / W, (x0 + col_w) / W], [bar_y / H, bar_y / H],
                          transform=fig.transFigure, color="black", lw=0.9))
    y = bar_y - bar_gap
    for s, (w, h, d) in zip(den_lines, ld):
        fig.text((xc - w / 2.0) / W, (y - (h - d)) / H, f"${s}$",
                 fontsize=fs, ha="left", va="baseline", color="black")
        y -= (h + gap)

    if lhs:
        fig.text(0.0, (bar_y - (lh / 2.0 - ldp) + 1.0) / H, f"${lhs}$",
                 fontsize=fs, ha="left", va="baseline", color="black")

    return _fig_to_flowable(fig, W, H, dpi=dpi)


def _fig_to_flowable(fig, w_pt, h_pt, dpi=300):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, transparent=True,
                bbox_inches=None, pad_inches=0)
    plt.close(fig)
    buf.seek(0)
    img = Image(buf, width=w_pt, height=h_pt)
    img.hAlign = "LEFT"
    return img


def tf_flowable(k_val, num_coeffs_or_roots, den_coeffs_or_roots, width,
                lhs_name="H(s)", factored_num=False, factored_den=False,
                fs=9.0, max_h=None):
    """Fraction flowable for a transfer function with the gain isolated.

    Numerator/denominator are handed in already in the display domain (rad/s).
    `factored_*` selects biquad-factor rendering (3rd-order sections print the
    real pole in its own bracket, exactly like the Biquad-Pairing tab).
    """
    if factored_num:
        num_terms = TU.roots_to_biquad_factors(num_coeffs_or_roots, "Denormalized")
        num_sep = ""
    else:
        num_terms = TU.poly_to_terms(num_coeffs_or_roots, "Denormalized")
        num_sep = " "
    if factored_den:
        den_terms = TU.roots_to_biquad_factors(den_coeffs_or_roots, "Denormalized")
        den_sep = ""
    else:
        den_terms = TU.poly_to_terms(den_coeffs_or_roots, "Denormalized")
        den_sep = " "

    k_latex = TU.format_latex_val(float(k_val), "Denormalized")
    lhs = rf"{lhs_name} = {k_latex} \;\cdot\;"

    for size in (fs, fs - 0.7, fs - 1.4, fs - 2.0, fs - 2.6):
        if size < 5.5:
            size = 5.5
        lw = _math_metrics(lhs, size)[0]
        avail = max(width - lw - 8.0, width * 0.35)
        nl = _pack_terms(num_terms, avail, size, num_sep)
        dl = _pack_terms(den_terms, avail, size, den_sep)
        est = 8.0 + 1.35 * size * (len(nl) + len(dl)) + 10.0
        if max_h is None or est <= max_h or size <= 5.5:
            return _math_block_image(lhs, nl, dl, width, size)
    return _math_block_image(lhs, nl, dl, width, 5.5)


# =====================================================================
#  4.  FIGURES  (matplotlib ports of the on-screen plotly views)
# =====================================================================
def _freq_tick(v, _pos=None):
    """1k / 10k / 250 rather than matplotlib's 10^3 — readable on a narrow
    detail plot where the majors can land mid-decade."""
    if v <= 0:
        return ""
    for div, suf in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if v >= div * 0.999:
            return f"{v/div:g}{suf}"
    return f"{v:g}"


def _log_freq_axis(ax, lo, hi):
    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(_freq_tick))
    if hi > lo > 0 and np.log10(hi / lo) < 2.0:
        # narrow window: a decade grid alone would label almost nothing
        ax.xaxis.set_minor_locator(
            ticker.LogLocator(base=10.0, subs=(2.0, 3.0, 5.0, 7.0), numticks=20))
        ax.xaxis.set_minor_formatter(ticker.FuncFormatter(_freq_tick))
        ax.tick_params(axis="x", which="minor", labelsize=5.6)


def _new_fig(w_pt, h_pt, dpi=200):
    fig = plt.figure(figsize=(w_pt / 72.0, h_pt / 72.0), dpi=dpi)
    fig.patch.set_facecolor("white")
    return fig


def pz_map_flowable(engine, stages, width, height, dpi=200):
    """Pole-zero map with per-section colours, ω0 circles and pairing links —
    a print port of plot_utils.plot_mnemoscheme_map (same palette, same
    pairing rule), drawn at the exact half-page size."""
    fig = _new_fig(width, height, dpi)
    ax = fig.add_axes([0.10, 0.12, 0.72, 0.82])

    poles = np.asarray(engine.get("poles", []), dtype=complex)
    zeros = np.asarray(engine.get("zeros", []), dtype=complex)

    theta = np.linspace(0, 2 * np.pi, 361)
    assigned_p, assigned_z, handles = [], [], []

    for idx, stg in enumerate(stages):
        c = STAGE_COLORS[idx % len(STAGE_COLORS)]
        tag = str(stg["stage_num"])
        sp = [complex(v) for v in stg.get("poles", [])]
        sz = [complex(v) for v in stg.get("zeros", [])]
        assigned_p += sp
        assigned_z += sz

        cx = [p for p in sp if abs(p.imag) > 1e-6]
        if cx:
            w0 = abs(cx[0])
            ax.plot(w0 * np.cos(theta), w0 * np.sin(theta), ls=":", lw=0.9,
                    color=c, alpha=0.45, zorder=1)

        # pairing links — identical rule to the mnemoscheme
        if sp and sz:
            two_real = len(sp) == 2 and all(abs(p.imag) < 1e-6 for p in sp)
            if two_real:
                links = [(p, z) for p in sp for z in sz]
            else:
                links, free = [], list(sz)
                for p in sorted(sp, key=lambda x: (-abs(x.imag), x.real)):
                    if free:
                        zz = min(free, key=lambda z: abs(z - (p + 1e-9j)))
                        free.remove(zz)
                        links.append((p, zz))
            for p, z in links:
                ax.plot([p.real, z.real], [p.imag, z.imag], ls="--", lw=1.1,
                        color=c, alpha=0.8, zorder=2)

        if sp:
            ax.plot([p.real for p in sp], [p.imag for p in sp], "x", ms=6.5,
                    mew=1.6, color=c, ls="none", zorder=4)
        if sz:
            ax.plot([z.real for z in sz], [z.imag for z in sz], "o", ms=6.0,
                    mfc="none", mew=1.4, color=c, ls="none", zorder=4)
        for r in sp + sz:
            ax.annotate(tag, (r.real, r.imag), textcoords="offset points",
                        xytext=(5, 2), fontsize=6.2, color=c, weight="bold",
                        zorder=5)
        handles.append(Line2D([], [], color=c, lw=2.0, label=f"Section {tag}"))

    def _orphans(all_r, taken):
        return [r for r in all_r
                if not any(abs(r - t) <= 1e-5 * max(abs(r), 1.0) for t in taken)]

    op = _orphans(poles, assigned_p)
    oz = _orphans(zeros, assigned_z)
    if op:
        ax.plot([r.real for r in op], [r.imag for r in op], "x", ms=6.5, mew=1.6,
                color="black", ls="none", zorder=4)
    if oz:
        ax.plot([r.real for r in oz], [r.imag for r in oz], "o", ms=6.0,
                mfc="none", mew=1.4, color="black", ls="none", zorder=4)
    if op or oz:
        handles.append(Line2D([], [], color="black", lw=2.0, label="Unassigned"))

    all_r = list(poles) + list(zeros)
    mx = max([abs(r.real) for r in all_r], default=1.0)
    my = max([abs(r.imag) for r in all_r], default=1.0)
    lx, ly = max(mx, 1e-9) * 1.12, max(my, 1e-9) * 1.15
    lim = max(lx, ly)
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_aspect("equal", adjustable="box")
    ax.axhline(0, color="black", lw=0.6, alpha=0.35)
    ax.axvline(0, color="black", lw=0.6, alpha=0.35)
    ax.grid(True, ls=":", lw=0.4, alpha=0.45)
    ax.set_xlabel("Real (rad/s)", fontsize=7.5)
    ax.set_ylabel("Imaginary (rad/s)", fontsize=7.5)
    ax.tick_params(labelsize=6.5)
    ax.ticklabel_format(style="sci", scilimits=(-2, 3), axis="both")
    ax.xaxis.get_offset_text().set_fontsize(6.2)
    ax.yaxis.get_offset_text().set_fontsize(6.2)
    if handles:
        leg_p = Line2D([], [], color="#444444", marker="x", ls="none",
                       mew=1.4, label="pole")
        leg_z = Line2D([], [], color="#444444", marker="o", mfc="none",
                       ls="none", mew=1.2, label="zero")
        ax.legend(handles=handles + [leg_p, leg_z], fontsize=6.3,
                  loc="upper left", bbox_to_anchor=(1.01, 1.0),
                  frameon=False, handlelength=1.6)
    return _fig_to_flowable(fig, width, height, dpi=dpi)


def _mag_db(H):
    return 20.0 * np.log10(np.abs(np.asarray(H)) + 1e-300)


def _phase_deg(H):
    return np.degrees(np.unwrap(np.angle(np.asarray(H))))


def _gd_ms(f, H):
    """Group delay in ms by phase differentiation — the fallback used when the
    caller has no analytic H'/H curve to hand (hw_plots.build_loggrad is only
    evaluated when the tab's Group-delay checkbox is on)."""
    w = 2.0 * np.pi * np.asarray(f, dtype=float)
    ph = np.unwrap(np.angle(np.asarray(H)))
    return -np.gradient(ph, w) * 1e3


def bode_flowable(bode, width, height, show_phase=False, show_gd=False,
                  show_mc=True, xlim=None, ylim=None,
                  title="Bode — design vs realized", dpi=200):
    """Design-vs-realized Bode on ONE set of axes, log frequency.

    Magnitude owns the left axis; phase and group delay are overlaid on the
    same plot and read off their own right-hand scales (phase on the inner
    right spine, group delay on a second spine offset outboard of it), which
    mirrors the y2/y3 arrangement of the interactive figure. Monte-Carlo is
    shaded behind the magnitude traces.
    """
    f = np.asarray(bode["f"], dtype=float)
    ideal = np.asarray(bode["ideal"])
    real = np.asarray(bode["realized"])
    mc = bode.get("mc") if show_mc else None

    n_right = int(show_phase) + int(show_gd)
    right = {0: 0.985, 1: 0.935, 2: 0.893}[n_right]
    fig = _new_fig(width, height, dpi)
    ax = fig.add_axes([0.058, 0.125, right - 0.058, 0.775])
    handles = []

    if mc is not None:
        ax.fill_between(f, mc["lo"], mc["hi"], color=C_BAND, alpha=0.45, lw=0,
                        zorder=1,
                        label=f"MC p{mc['lo_pct']:g}–p{mc['hi_pct']:g} "
                              f"({mc['n_runs']} runs)")
        ax.plot(f, mc["median"], ls=":", lw=0.9, color=C_MEDIAN,
                label="MC median", zorder=2)
    ax.plot(f, _mag_db(ideal), color=C_IDEAL, lw=1.5, label="Ideal (design)",
            zorder=5)
    ax.plot(f, _mag_db(real), color=C_REAL, lw=1.2, label="Realized (BOM)",
            zorder=6)
    for fm, _kind in (bode.get("marks") or []):
        if fm and fm > 0:
            ax.axvline(fm, color="#b0b0b0", lw=0.5, ls="--", alpha=0.7, zorder=0)
    ax.set_ylabel("Magnitude (dB)", fontsize=7.5)
    ax.set_xlabel("Frequency (Hz)", fontsize=7.5)
    ax.set_title(title, fontsize=9.0, weight="bold", pad=17)
    _lo, _hi = xlim if xlim else (f[0], f[-1])
    _log_freq_axis(ax, _lo, _hi)
    ax.grid(True, which="both", ls=":", lw=0.4, alpha=0.5)
    ax.tick_params(labelsize=6.4)
    ax.set_xlim(_lo, _hi)
    if ylim:
        ax.set_ylim(*ylim)
    handles += ax.get_legend_handles_labels()[0]

    if show_phase:
        ax2 = ax.twinx()
        ax2.plot(f, _phase_deg(ideal), color=C_IDEAL, lw=1.0, ls="--",
                 alpha=0.85, label="Ideal phase", zorder=3)
        ax2.plot(f, _phase_deg(real), color=C_REAL, lw=0.9, ls="--",
                 alpha=0.85, label="Realized phase", zorder=4)
        ax2.set_ylabel("Phase (deg)", fontsize=7.0, color="#555555")
        ax2.tick_params(axis="y", labelsize=6.4, colors="#555555")
        ax2.grid(False)
        handles += ax2.get_legend_handles_labels()[0]

    if show_gd:
        ax3 = ax.twinx()
        if show_phase:
            # second right-hand scale, parked outboard of the phase spine
            ax3.spines["right"].set_position(("outward", 36))
        gi = bode.get("ideal_gd")
        gr = bode.get("realized_gd")
        gi = _gd_ms(f, ideal) if gi is None else np.asarray(gi, float)
        gr = _gd_ms(f, real) if gr is None else np.asarray(gr, float)
        # Realized notch zeros sit just off the jw axis, so the realized GD can
        # spike ~1/|Re(zero)| at a notch. Anchor the window on the clean ideal
        # curve plus the realized 3-97% bulk, then clip, exactly as the
        # interactive figure does.
        ref = gi[np.isfinite(gi)]
        if ref.size:
            lo, hi = float(ref.min()), float(ref.max())
            gf = gr[np.isfinite(gr)]
            if gf.size:
                lo = min(lo, float(np.percentile(gf, 3)))
                hi = max(hi, float(np.percentile(gf, 97)))
            span = max(hi - lo, 1e-3)
            lo, hi = lo - 0.2 * span, hi + 0.2 * span
            gi, gr = np.clip(gi, lo, hi), np.clip(gr, lo, hi)
            ax3.set_ylim(lo, hi)
        ax3.plot(f, gi, color=C_GD_I, lw=1.0, ls=":", label="Ideal GD", zorder=3)
        ax3.plot(f, gr, color=C_GD_R, lw=0.9, ls=":", label="Realized GD",
                 zorder=4)
        ax3.set_ylabel("Group delay (ms)", fontsize=7.0, color=C_GD_I)
        ax3.tick_params(axis="y", labelsize=6.4, colors=C_GD_I)
        ax3.grid(False)
        handles += ax3.get_legend_handles_labels()[0]

    ax.legend(handles=handles, labels=[h.get_label() for h in handles],
              fontsize=6.4, loc="lower left", bbox_to_anchor=(0.0, 1.005),
              ncol=min(len(handles), 8), frameon=False, borderaxespad=0.0)
    return _fig_to_flowable(fig, width, height, dpi=dpi)


def _count_primitives(drawing):
    """(geometry, text) primitive counts in a parsed reportlab Drawing."""
    from reportlab.graphics.shapes import Group, String
    geo = txt = 0
    stack = [drawing]
    while stack:
        node = stack.pop()
        for c in getattr(node, "contents", []):
            if isinstance(c, Group):
                stack.append(c)
            elif isinstance(c, String):
                txt += 1
            else:
                geo += 1
    return geo, txt


def _svg_via_project(svg, max_w, max_h):
    """Rasterize through schematic_svg.svg_to_png -- byte-for-byte the call
    behind the Topology tab's working '⬇ PNG' button. Tried FIRST so the report
    and the tab can never disagree about what a schematic looks like."""
    import schematic_svg as _sch
    png = _sch.svg_to_png(svg, scale=4)
    if not png:
        return None
    import struct
    w_px, h_px = struct.unpack(">II", png[16:24])
    ratio = h_px / float(w_px)
    w, h = max_w, max_w * ratio
    if h > max_h:
        h, w = max_h, max_h / ratio
    img = Image(io.BytesIO(png), width=w, height=h)
    img.hAlign = "CENTER"
    return img


def _svg_via_svglib(svg, max_w, max_h):
    """Pure-Python vector fallback. svglib needs no native cairo DLLs, which is
    what usually keeps cairosvg from loading on Windows, and it draws straight
    into the PDF as vector rather than raster. Its SVG coverage is narrower
    than cairo's (no foreignObject, partial CSS), so it is tried second.
    Returns (drawing, geometry_count, text_count)."""
    from svglib.svglib import svg2rlg                     # type: ignore
    d = svg2rlg(io.BytesIO(svg.encode("utf-8")))
    if d is None or not d.width or not d.height:
        return None
    geo, txt = _count_primitives(d)
    sc = min(max_w / float(d.width), max_h / float(d.height))
    d.scale(sc, sc)
    d.width = float(d.width) * sc
    d.height = float(d.height) * sc
    d.hAlign = "CENTER"
    return d, geo, txt


def svg_flowable(svg_text, max_w, max_h, dpi=260, notes=None):
    """Annotated schematic SVG -> a flowable fitting inside (max_w, max_h),
    width:height ratio preserved.

    Backends, in order: schematic_svg.svg_to_png (the project's own cairosvg
    call, identical to the ⬇ PNG button) then svglib. `notes` collects a short
    human-readable trace -- which backend drew, and what the others said -- so
    a schematic that comes out wrong says why on the page instead of leaving
    you to guess.
    """
    def _note(t):
        if notes is not None:
            notes.append(t)

    if not svg_text:
        _note("no SVG was produced for this section")
        return None
    i = svg_text.find("<svg")
    if i < 0:
        _note("the rendered text contains no <svg> root element")
        return None
    svg = svg_text[i:]
    if _sanitize_svg is None:
        _note("schematic_svg.sanitize_svg() is missing — a drawio export using "
              "light-dark()/var() colours will lose every shape")
    else:
        svg = _sanitize_svg(svg)

    try:
        img = _svg_via_project(svg, max_w, max_h)
        if img is not None:
            _note("backend=cairosvg")
            return img
        _note("cairosvg: not installed or returned no data")
    except Exception as e:
        _note(f"cairosvg: {type(e).__name__}: {e}")

    try:
        got = _svg_via_svglib(svg, max_w, max_h)
        if got is not None:
            d, geo, txt = got
            if geo == 0:
                _note(f"svglib parsed {txt} text items and NO geometry — the "
                      f"base drawing did not survive the converter")
            else:
                _note(f"backend=svglib ({geo} shapes, {txt} text items)")
            return d
        _note("svglib: produced an empty drawing")
    except Exception as e:
        _note(f"svglib: {type(e).__name__}: {e}")
    return None


# =====================================================================
#  5.  BOM  (mirrors topology_tab._render_choice: snapped black, ideal grey)
# =====================================================================
_AM_R_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R4": 4, "R5": 5, "R6": 6, "R7": 7, "R8": 8}


def _display_pairs(alias):
    """[(display_designator, solver_symbol)] over the C1-C4 / R1-R8 grid, caps
    then resistors, each block sorted by DISPLAY name. Byte-identical to
    topology_tab._display_pairs; duplicated here only so this module stays free
    of the streamlit import (the alias itself comes from schematic_svg, so the
    designator mapping cannot drift)."""
    pairs = [(alias.get(k, k), k) for k in
             ("C1", "C2", "C3", "C4",
              "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8")]
    return (sorted([q for q in pairs if q[0][0] == "C"])
            + sorted([q for q in pairs if q[0][0] == "R"]))


def bom_items(row, cont):
    """[(designator, snapped_str, ideal_str_or_None)] for caps then resistors.

    Capacitors carry no ideal twin: they are already E-series when the solver
    emits them (unified_solver_v2 phase 3), so discrete_snapper never re-snaps
    them and there is nothing to put in brackets. Resistors are continuous
    until discrete_snapper.snap_to_hardware overwrites them in place, so the
    ideal value only exists if the Topology tab persisted the paired
    `continuous` row (see bom_picks_cont)."""
    cont = cont or {}
    topo = str(row.get("topology") or "")
    caps, res = [], []

    if "-AM" in topo:
        o3 = topo[:1] == "3"
        is_hp = "HP" in topo
        is_lp2 = "LP-AM2" in topo
        pre_R = "R3" if (is_lp2 and not is_hp) else "R1"
        tmp = []
        for i in range(1, 9):
            nm = f"R{i}"
            if row.get(nm) is None:
                continue
            if o3 and nm == pre_R:
                tmp.append(("R0", nm))
            elif nm == "R3":
                continue
            else:
                tmp.append((nm, nm))
        tmp.sort(key=lambda pr: _AM_R_ORDER.get(pr[0], 9))
        res = tmp
        if o3 and row.get("C4") is not None:
            caps.append(("C0", "C4"))
        if row.get("C1") is not None:
            caps.append(("C1", "C1"))
        for c in ("C2", "C3"):
            if row.get(c) is not None:
                caps.append((c, c))
    else:
        pairs = _display_pairs(bp3_alias(topo))
        caps = [(d, k) for d, k in pairs if d[0] == "C" and row.get(k)]
        res = [(d, k) for d, k in pairs if d[0] == "R" and row.get(k)]

    out = []
    for d, k in caps:
        if k == "C1" and row.get("C1_parallel") and row.get("C1a") and row.get("C1b"):
            v = f"{_fmt_cap(row['C1a'])} ∥ {_fmt_cap(row['C1b'])}"
        elif k == "C2" and row.get("C2_parallel") and row.get("C2a") and row.get("C2b"):
            v = f"{_fmt_cap(row['C2a'])} ∥ {_fmt_cap(row['C2b'])}"
        else:
            v = _fmt_cap(row.get(k))
        out.append((d, f"{v}F", None))
    for d, k in res:
        ideal = cont.get(k)
        out.append((d, f"{_fmt_res(row.get(k), True)}Ω",
                    f"{_fmt_res(ideal, False)}Ω" if ideal is not None else None))
    return out


def bom_table(items, width, styles, cols=4):
    """Compact BOM grid: `cols` designator/value column pairs per row, snapped
    value in black with the ideal (pre-snap) value in grey brackets."""
    if not items:
        return Paragraph("No component values available.", styles["small"])
    st_small = styles["small"]
    cells = []
    for d, snap, ideal in items:
        txt = snap if ideal is None else f'{snap} <font color="#888888">({ideal})</font>'
        cells.append([Paragraph(f"<b>{d}</b>", st_small), Paragraph(txt, st_small)])
    rows, buf = [], []
    for c in cells:
        buf.extend(c)
        if len(buf) == 2 * cols:
            rows.append(buf)
            buf = []
    pad = 0
    if buf:
        pad = 2 * cols - len(buf)
        buf += [""] * pad
        rows.append(buf)
    unit = width / cols                     # designator narrow, value wide
    cw = []
    for _ in range(cols):
        cw += [unit * 0.30, unit * 0.70]
    t = Table(rows, colWidths=cw, hAlign="LEFT")
    stl = _table_style(head=False)
    if pad:
        stl.add("GRID", (2 * cols - pad, len(rows) - 1), (-1, -1), 0,
                colors.white)
    t.setStyle(stl)
    return t


# =====================================================================
#  6.  ROOT / PARAMETER TABLES
# =====================================================================
def _owner_map(stages, kind):
    """root -> owning section number, matched by nearest value."""
    owners = []
    for s in stages:
        for r in s.get(kind, []):
            owners.append((complex(r), s["stage_num"]))
    def who(r):
        if not owners:
            return "—"
        best = min(owners, key=lambda o: abs(o[0] - complex(r)))
        scale = max(abs(complex(r)), 1e-12)
        return str(best[1]) if abs(best[0] - complex(r)) <= 1e-4 * scale else "—"
    return who


def roots_table(engine, stages, width, styles, scale=1.0, unit="rad/s",
                sci=True):
    """Poles and zeros side by side, denormalized, with the owning section."""
    poles = sorted(np.asarray(engine.get("poles", []), dtype=complex),
                   key=lambda r: (-abs(r.imag), r.real))
    zeros = sorted(np.asarray(engine.get("zeros", []), dtype=complex),
                   key=lambda r: (-abs(r.imag), r.real))
    who_p = _owner_map(stages, "poles")
    who_z = _owner_map(stages, "zeros")
    n = max(len(poles), len(zeros))
    fmt = "{:+.6e}" if sci else "{:+.6f}"
    head = ["#", f"Pole Re ({unit})", f"Pole Im ({unit})", "Sec",
            "#", f"Zero Re ({unit})", f"Zero Im ({unit})", "Sec"]
    data = [head]
    for i in range(n):
        rowd = []
        if i < len(poles):
            p = poles[i] / scale
            rowd += [str(i + 1), fmt.format(p.real), fmt.format(p.imag),
                     who_p(poles[i])]
        else:
            rowd += ["", "", "", ""]
        if i < len(zeros):
            z = zeros[i] / scale
            rowd += [str(i + 1), fmt.format(z.real), fmt.format(z.imag),
                     who_z(zeros[i])]
        else:
            rowd += ["", "", "", ""]
        data.append(rowd)
    w = width
    cw = [w * 0.035, w * 0.135, w * 0.135, w * 0.045] * 2
    t = Table(data, colWidths=cw, repeatRows=1, hAlign="LEFT")
    t.setStyle(_table_style())
    return t


def spec_table(spec_rows, width, styles, cols=2):
    """Label/value pairs, `cols` pairs per row.

    A row given as a 3-tuple `(label, value, True)` is emitted full width on
    its own line instead — used for long one-off entries such as the
    calculated stopband edges, which would otherwise wrap badly inside a
    half-width cell."""
    if not spec_rows:
        return Paragraph("No data available for this block.", styles["small"])
    st_small = styles["small"]
    unit = width / cols
    cw = []
    for _ in range(cols):
        cw += [unit * 0.42, unit * 0.58]

    rows, spans, buf, pads = [], [], [], []

    def _flush():
        if buf:
            n = 2 * cols - len(buf)
            pads.append((len(rows), n))
            rows.append(buf + [""] * n)
            buf.clear()

    for entry in spec_rows:
        wide = len(entry) > 2 and entry[2]
        k, v = entry[0], entry[1]
        if wide:
            _flush()
            spans.append(len(rows))
            rows.append([Paragraph(f"<b>{k}</b>", st_small),
                         Paragraph(str(v), st_small)] + [""] * (2 * cols - 2))
        else:
            buf.extend([Paragraph(f"<b>{k}</b>", st_small),
                        Paragraph(str(v), st_small)])
            if len(buf) == 2 * cols:
                rows.append(list(buf))
                buf.clear()
    _flush()

    t = Table(rows, colWidths=cw, hAlign="LEFT")
    stl = _table_style(head=False)
    for r in spans:
        stl.add("SPAN", (1, r), (-1, r))
    for r, n in pads:
        if n:
            stl.add("GRID", (2 * cols - n, r), (-1, r), 0, colors.white)
    t.setStyle(stl)
    return t




# =====================================================================
#  7.  SECTION HELPERS
# =====================================================================
def section_tf_flowable(stage, width, fs=8.5):
    """Design transfer function of one section, denormalized, rad/s, gain
    isolated. Same branch rule as the Biquad-Pairing tab: <=2 roots (or an
    all-origin numerator) expand into a polynomial, otherwise the factored
    form is used — which is what puts a 3rd-order section's real pole in its
    own bracket."""
    if not stage:
        return Paragraph("Section design data unavailable.",
                         _styles()["small"])
    sp = [complex(v) for v in stage.get("poles", [])]
    sz = [complex(v) for v in stage.get("zeros", [])]

    fac_n = not (len(sz) <= 2 or all(abs(z) < 1e-6 for z in sz))
    fac_d = not (len(sp) <= 2 or all(abs(p) < 1e-6 for p in sp))
    num = TU.clean_roots(sz) if fac_n else np.atleast_1d(
        np.poly(TU.clean_roots(sz)).real if len(sz) else np.array([1.0]))
    den = TU.clean_roots(sp) if fac_d else np.atleast_1d(
        np.poly(TU.clean_roots(sp)).real if len(sp) else np.array([1.0]))

    return tf_flowable(stage.get("K_radps", 1.0), num, den, width,
                       lhs_name=f"H_{{{stage['stage_num']}}}(s)",
                       factored_num=fac_n, factored_den=fac_d, fs=fs)


def section_param_rows(stage):
    """f0 / Q / fz / fp / Ki / peak magnitude — the design targets the BOM was
    solved against. Printed above the schematic on every section page."""
    if not stage:
        return [("Design parameters", "unavailable")]
    rows = [("Order", str(stage.get("order", "—")))]
    if stage.get("f0_hz"):
        rows.append(("f₀", _hz(stage["f0_hz"])))
        rows.append(("ω₀", f"{2*np.pi*stage['f0_hz']:.6e} rad/s"))
    q = stage.get("Q")
    rows.append(("Q", f"{q:.4f}" if q else "—"))
    rows.append(("f_z (notch)", _hz(stage["fz_hz"]) if stage.get("fz_hz") else "none"))
    rows.append(("f_p (real pole)", _hz(stage["f1_hz"]) if stage.get("f1_hz") else "none"))
    rows.append(("Kᵢ", f"{stage.get('K_radps', float('nan')):.6e}"))
    pm = stage.get("peak_mag")
    rows.append(("Peak |H| (V/V)", f"{pm:.4f}" if pm is not None else "—"))
    return rows


def mc_caption(p):
    """'Res. 0…1MΩ 2%; >1MΩ 5%; cap. 5%; 2000 runs, gaussian 3σ, min–max'."""
    if not p:
        return "Monte-Carlo not run."
    bits = []
    for lo, hi, tol in (p.get("r_bands") or []):
        bits.append(f"{_ohm_band(lo, hi)} {tol:g}%")
    dist = ("gaussian 3σ" if p.get("dist") == "gaussian"
            else "uniform ±tol")
    env = {"(1.0, 99.0)": "p1–p99", "(5.0, 95.0)": "p5–p95",
           "(0.0, 100.0)": "min–max"}.get(
        str((p.get("lo_pct"), p.get("hi_pct"))),
        f"p{p.get('lo_pct'):g}–p{p.get('hi_pct'):g}")
    return (f"Res. {'; '.join(bits)}; cap. {p.get('c_tol_pct', 0):g}%; "
            f"{p.get('n_runs', 0)} runs, {dist}, {env}; seed {p.get('seed', 0)}. "
            f"Op-amp parameters held fixed.")


def passband_stats(bode, passbands, mc=None):
    """Passband gain = the HIGHEST magnitude inside a passband, i.e. the top of
    the ripple, evaluated independently for the design curve, the realized
    curve and every Monte-Carlo run.

    Why not DC gain: an even-order Chebyshev or elliptic WITHOUT the even-order
    modification has H(0) (or H(inf) for a high-pass) sitting a full ripple
    below the passband peaks, so a DC number does not describe the passband at
    all. And with several reflection zeros there are several peaks of equal
    nominal height; component tolerances lift one and drop another, so the
    winning peak can migrate between reflection zeros from run to run. Taking
    argmax over the band per run — rather than comparing at one fixed
    frequency — is what makes the spread meaningful.

    A band-reject has TWO passbands (below the lower corner, above the upper),
    reported as separate rows because their spreads are independent.

    Returns one dict per band: design / realized peak, and when `mc` is given
    the lowest and highest per-run peak, their difference, and the frequency
    range the winning peaks landed in.
    """
    f = np.asarray(bode["f"], dtype=float)
    out = []
    for label, lo, hi in (passbands or []):
        m = np.ones(f.shape, dtype=bool)
        if lo:
            m &= (f >= float(lo))
        if hi:
            m &= (f <= float(hi))
        if not m.any():
            continue
        fb = f[m]
        d = {"label": label,
             "lo": float(lo) if lo else float(f[0]),
             "hi": float(hi) if hi else float(f[-1]),
             "open_lo": lo is None, "open_hi": hi is None,
             "design": float(np.nanmax(_mag_db(np.asarray(bode["ideal"]))[m])),
             "realized": float(np.nanmax(_mag_db(np.asarray(bode["realized"]))[m]))}
        mags = None if mc is None else mc.get("mags")
        if mags is not None:
            sub = np.asarray(mags, dtype=float)[:, m]
            peaks = np.nanmax(sub, axis=1)
            # nanargmax raises on an all-NaN run; -inf makes the choice safe
            idx = np.argmax(np.nan_to_num(sub, nan=-np.inf), axis=1)
            fpk = fb[idx]
            good = np.isfinite(peaks)
            if good.any():
                d.update(mc_min=float(peaks[good].min()),
                         mc_max=float(peaks[good].max()),
                         spread=float(peaks[good].max() - peaks[good].min()),
                         f_lo=float(fpk[good].min()), f_hi=float(fpk[good].max()),
                         n_runs=int(good.sum()))
        out.append(d)
    return out


def _db_lin(db):
    """'+20.00 dB (10.00)' — dB with the dimensionless ratio alongside, so the
    table can be read without reaching for a calculator."""
    lin = 10.0 ** (float(db) / 20.0)
    txt = f"{lin:.4g}" if lin >= 1e-2 else f"{lin:.3e}"
    return f"{float(db):+.2f} dB ({txt})"


def passband_table(stats, width, styles, with_mc=True):
    """One row per passband, identified by its frequency range."""
    head = ["Range", "Design peak", "Realized peak"]
    if with_mc:
        head += ["MC lowest peak", "MC highest peak", "Spread",
                 "Peak located at"]
    data = [head]
    for d in stats:
        rng = ("≤ " + _hz(d["hi"]) if d["open_lo"] else
               "≥ " + _hz(d["lo"]) if d["open_hi"] else
               f"{_hz(d['lo'])} … {_hz(d['hi'])}")
        row = [rng, _db_lin(d["design"]), _db_lin(d["realized"])]
        if with_mc:
            if "spread" in d:
                pk = (_hz(d["f_lo"]) if abs(d["f_hi"] - d["f_lo"]) < 1e-9
                      else f"{_hz(d['f_lo'])} … {_hz(d['f_hi'])}")
                row += [_db_lin(d["mc_min"]), _db_lin(d["mc_max"]),
                        f"{d['spread']:.2f} dB", pk]
            else:
                row += ["—", "—", "—", "—"]
        data.append(row)
    n = len(head)
    cw = [width * 0.15] + [width * (0.85 / (n - 1))] * (n - 1)
    t = Table(data, colWidths=cw, repeatRows=1, hAlign="LEFT")
    t.setStyle(_table_style())
    return t


# =====================================================================
#  8.  DOCUMENT
# =====================================================================
def _footer(canvas, doc):
    canvas.saveState()
    w, h = canvas._pagesize
    canvas.setStrokeColor(colors.HexColor("#cccccc"))
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, 26, w - MARGIN, 26)
    canvas.setFont(FONT, 6.4)
    canvas.setFillColor(GREY)
    canvas.drawString(MARGIN, 17, doc._report_footer)
    canvas.drawRightString(w - MARGIN, 17, f"page {doc.page}")
    canvas.restoreState()


def build_report(ctx, opts=None):
    """Assemble the A4 report and return the PDF as bytes."""
    _register_fonts()
    opts = opts or {}
    S = _styles()
    meta = ctx.get("meta", {})
    engine = ctx.get("engine", {})
    stages = ctx.get("stages", [])
    sections = ctx.get("sections", [])
    bode = ctx.get("bode", {})

    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4,
                          leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=MARGIN, bottomMargin=MARGIN + 14,
                          title=meta.get("title") or "Filter Synthesis Report",
                          author=meta.get("author") or "",
                          subject="Active filter synthesis report")
    doc._report_footer = (f"{meta.get('title','Filter Synthesis Report')} · "
                          f"{meta.get('date','')} · design {meta.get('design_hash','—')}")
    fp = Frame(MARGIN, MARGIN + 14, CONTENT_W, PH - 2 * MARGIN - 14, id="p")
    fl = Frame(MARGIN, MARGIN + 14, CONTENT_WL, LH - 2 * MARGIN - 14, id="l")
    doc.addPageTemplates([
        PageTemplate(id="portrait", frames=[fp], pagesize=A4, onPage=_footer),
        PageTemplate(id="landscape", frames=[fl], pagesize=landscape(A4),
                     onPage=_footer),
    ])

    F = []                                            # the story

    # ---------------- optional cover ----------------
    if opts.get("cover"):
        F += [Spacer(1, 150),
              Paragraph(meta.get("project") or "Active Filter Design", S["coverT"]),
              Spacer(1, 12),
              Paragraph(meta.get("subtitle") or "", S["coverS"]),
              Spacer(1, 40),
              Paragraph(f"{meta.get('author','')}", S["coverS"]),
              Paragraph(f"{meta.get('date','')}", S["coverS"]),
              Spacer(1, 30),
              Paragraph(f"{meta.get('tool_version','')}", S["cap"]),
              NextPageTemplate("portrait"), PageBreak()]

    # ================= PAGE 1 : spec + whole-filter TF =================
    F.append(Paragraph(meta.get("title") or "Filter Synthesis Report", S["h1"]))
    F.append(Paragraph(meta.get("subtitle") or "", S["cap"]))
    F.append(Spacer(1, 8))

    F.append(Paragraph("1 · Design specification", S["h2"]))
    F.append(spec_table(ctx.get("spec", []), CONTENT_W, S, cols=2))

    F.append(Paragraph("2 · Transfer function — denormalized, rad/s, "
                       "isolated gain constant", S["h2"]))
    poles = np.asarray(engine.get("poles", []), dtype=complex)
    zeros = np.asarray(engine.get("zeros", []), dtype=complex)
    k_disp = float(engine.get("k", 1.0)) * float(engine.get("gain_units", 1.0))
    num = np.atleast_1d(np.poly(TU.clean_roots(zeros)).real
                        if len(zeros) else np.array([1.0]))
    den = np.atleast_1d(np.poly(TU.clean_roots(poles)).real
                        if len(poles) else np.array([1.0]))
    F.append(tf_flowable(k_disp, num, den, CONTENT_W, lhs_name="H(s)",
                         fs=9.0, max_h=CONTENT_H * 0.42))
    dd = len(poles) - len(zeros)
    ku = "(V/V)" if dd == 0 else ("(rad/s)" if dd == 1 else f"(rad/s)^{dd}")
    F.append(Paragraph(f"Gain constant K = {k_disp:+.6e} {ku} · "
                       f"numerator degree {len(zeros)}, denominator degree "
                       f"{len(poles)}.", S["cap"]))

    if opts.get("coeff_table"):
        F.append(Paragraph("2a · Polynomial coefficients (monic, rad/s)", S["h3"]))
        md = max(len(num), len(den)) - 1
        npad = [0.0] * (md + 1 - len(num)) + list(num)
        dpad = [0.0] * (md + 1 - len(den)) + list(den)
        data = [["Power", "Numerator", "Denominator"]]
        for i in range(md + 1):
            data.append([f"s^{md-i}", f"{npad[i]:+.6e}", f"{dpad[i]:+.6e}"])
        data.append(["K", f"{k_disp:+.6e}", "—"])
        t = Table(data, colWidths=[CONTENT_W * 0.14, CONTENT_W * 0.30,
                                   CONTENT_W * 0.30], repeatRows=1, hAlign="LEFT")
        t.setStyle(_table_style())
        F.append(t)

        F.append(Paragraph("2b · Polynomial coefficients (distributed form — "
                           "K folded into the numerator)", S["h3"]))
        data2 = [["Power", "Numerator × K", "Denominator"]]
        for i in range(md + 1):
            data2.append([f"s^{md-i}", f"{npad[i]*k_disp:+.6e}",
                          f"{dpad[i]:+.6e}"])
        t2 = Table(data2, colWidths=[CONTENT_W * 0.14, CONTENT_W * 0.30,
                                     CONTENT_W * 0.30], repeatRows=1,
                   hAlign="LEFT")
        t2.setStyle(_table_style())
        F.append(t2)

    # ================= PAGE 2 : PZ map + roots =================
    F.append(PageBreak())
    F.append(Paragraph("3 · Pole–zero map — section pairing", S["h2"]))
    F.append(pz_map_flowable(engine, stages, CONTENT_W, HALF_H))
    F.append(Paragraph("Dashed links show pole↔zero pairing inside a section; "
                       "dotted circles mark each section's ω₀. Colours match "
                       "the section numbering used throughout this report.",
                       S["cap"]))
    F.append(Paragraph("4 · Root locations — denormalized, rad/s", S["h2"]))
    F.append(roots_table(engine, stages, CONTENT_W, S))

    if opts.get("normalized_roots"):
        wn = float(engine.get("w_norm") or 1.0)
        F.append(Paragraph(f"4a · Root locations — normalized "
                           f"(ω_norm = {wn:.6e} rad/s)", S["h2"]))
        F.append(roots_table(engine, stages, CONTENT_W, S, scale=wn, unit="–",
                             sci=False))

    # ================= SECTION PAGES =================
    for i, sec in enumerate(sections):
        n = sec["n"]
        stage = next((s for s in stages if s["stage_num"] == n), None) or {}
        F.append(PageBreak())
        F.append(Paragraph(f"5.{i+1} · Section {n} — {sec.get('topology','?')}",
                           S["h2"]))

        F.append(Paragraph("Design parameters", S["h3"]))
        F.append(spec_table(section_param_rows(stage), CONTENT_W, S, cols=3))

        F.append(Spacer(1, 5))
        _svg_note = []
        img = svg_flowable(sec.get("svg"), CONTENT_W * 0.92, HALF_H * 0.92,
                           notes=_svg_note)
        if img is not None:
            F.append(img)
            # Stay quiet on the happy path; say something whenever the drawing
            # came from anywhere other than the project's own rasterizer.
            if _svg_note and _svg_note[-1] != "backend=cairosvg":
                F.append(Paragraph("⚠ schematic: " + "; ".join(_svg_note),
                                   S["cap"]))
        else:
            F.append(Paragraph(
                "⚠ Schematic could not be drawn — " + "; ".join(_svg_note)
                + ". The annotated SVG is downloadable from the Topology tab.",
                S["cap"]))
        F.append(Spacer(1, 4))

        _items = bom_items(sec.get("row", {}), sec.get("cont_row"))
        _has_ideal = any(it[2] for it in _items)
        F.append(Paragraph(
            "Bill of materials — snapped value, "
            "<font color='#888888'>(ideal / pre-snap)</font>" if _has_ideal
            else "Bill of materials — snapped value", S["h3"]))
        F.append(bom_table(_items, CONTENT_W, S, cols=4))
        if not _has_ideal:
            F.append(Paragraph(
                "Pre-snap (ideal) resistor values are not available for this "
                "section: discrete_snapper overwrites them in place, so they "
                "survive only if the Topology tab stored the paired continuous "
                "row (bom_picks_cont). Re-pick the BOM row, or check patch 2 in "
                "REPORT_INTEGRATION.md. Capacitors never have an ideal twin — "
                "the solver emits them already on the E-series grid.",
                S["cap"]))

        env = sec.get("env") or {}
        op = sec.get("opamp_params")
        op_txt = (f"A_ol = {_eng(op.get('A_ol'))}, "
                  f"GBWP = {_eng(op.get('GBWP_hz'))} Hz, "
                  f"R_o = {_eng((op.get('Ro') or 0)*1e6)} Ω") if op else \
                 "ideal (no op-amp limits)"
        F.append(Spacer(1, 3))
        F.append(spec_table([("Op-amp", sec.get("opamp_name") or "—"),
                             ("Op-amp parameters", op_txt)],
                            CONTENT_W, S, cols=2))

        F.append(Paragraph("Solver constraints", S["h3"]))
        F.append(spec_table([("R series", env.get("R_series", "—")),
                             ("C series", env.get("C_series", "—")),
                             ("R range", f"{_fmt_res(env.get('R_min'))}Ω … "
                                         f"{_fmt_res(env.get('R_max'))}Ω"),
                             ("C range", f"{_fmt_cap(env.get('C_min'))}F … "
                                         f"{_fmt_cap(env.get('C_max'))}F")],
                            CONTENT_W, S, cols=2))

        if opts.get("metrics"):
            m = sec.get("metrics") or {}
            gl = m.get("dc_label", "Passband gain")
            single = bool(m.get("single_section"))
            mrows = [("Sensitivity score", _eng(m.get("sens"))),
                     ("Snap cost", _eng(m.get("snap_cost"))),
                     (f"{gl} (realized)", f"{_eng(m.get('dc'))} V/V"),
                     (f"{gl} (design)", f"{_eng(m.get('dc_design'))} V/V"),
                     ("Specified filter gain",
                      f"{_eng(m.get('spec_gain'))} V/V" if m.get("spec_gain")
                      else "—"),
                     (f"Gain error (vs {'specified' if single else 'design'})",
                      (f"{m['gain_err_pct']:+.2f} %"
                       if m.get("gain_err_pct") is not None else "—"))]
            F.append(Paragraph("Quality metrics", S["h3"]))
            F.append(spec_table(mrows, CONTENT_W, S, cols=3))
            _gnote = (
                f"{gl} (design) is |H| evaluated analytically at this section's "
                f"passband reference — f\u2080 for a band-pass, DC for a low-pass, "
                f"HF for a high-pass — from K\u1d62 and Q, which is the quantity "
                f"the BOM was solved against. On a 3rd-order section the "
                f"absorbed real pole tilts the response, so it sits slightly "
                f"below the Peak |H| in the design parameters above. "
                f"{gl} (realized) uses snapped component values at zero "
                f"tolerance; component spread is in \u00a76.")
            if single:
                _gnote += (" With one section the whole filter gain lives here, "
                           "so the error is referenced to the gain specified in "
                           "\u00a71; negative means the build falls short.")
            else:
                _gnote += (" This section carries only its share of the cascade "
                           "gain, so the error is referenced to its own design "
                           "target rather than the filter gain.")
            F.append(Paragraph(_gnote, S["cap"]))

        F.append(Paragraph("Section transfer function — denormalized, rad/s",
                           S["h3"]))
        F.append(section_tf_flowable(stage, CONTENT_W, fs=8.5))

    # ================= LANDSCAPE : Bode =================
    F.append(NextPageTemplate("landscape"))
    F.append(PageBreak())
    F.append(Paragraph("6 · Bode — design vs realized", S["h2"]))
    show_mc = bool(opts.get("mc")) and bode.get("mc") is not None
    show_pg = bool(opts.get("phase_gd"))
    # Size the plot around whatever the passband-gain block will need, so the
    # whole landscape page stays one page.
    _pstats = passband_stats(bode, ctx.get("passbands"),
                             bode.get("mc") if show_mc else None)
    _pb_h = (58 + 13 * (len(_pstats) + 1) + 26) if _pstats else 0
    fig_h = max(CONTENT_HL - 52 - _pb_h, 210)
    F.append(bode_flowable(bode, CONTENT_WL, fig_h,
                           show_phase=show_pg, show_gd=show_pg,
                           show_mc=show_mc,
                           title="Cascade response — design vs realized"))
    if show_mc:
        F.append(Paragraph("Monte-Carlo: " + mc_caption(bode.get("mc_params")),
                           S["small"]))
    else:
        F.append(Paragraph("Monte-Carlo envelope not included.", S["cap"]))

    if _pstats:
        F.append(Spacer(1, 4))
        F.append(Paragraph("Passband gain", S["h3"]))
        F.append(passband_table(_pstats, CONTENT_WL, S, with_mc=show_mc))
        _expl = ("Passband gain is the top of the passband ripple — the highest "
                 "magnitude inside the band — not the DC (or HF) gain, which "
                 "for an even-order Chebyshev or elliptic without the "
                 "even-order modification sits a full ripple lower.")
        if show_mc:
            _expl += (" Each Monte-Carlo run is peak-searched separately, so a "
                      "peak that migrates between reflection zeros is still "
                      "counted; the spread is the highest per-run peak minus "
                      "the lowest.")
        F.append(Paragraph(_expl, S["cap"]))

    if opts.get("passband_zoom") and bode.get("passband"):
        flo, fhi = bode["passband"]
        _dlbl = bode.get("detail_label") or "Passband detail"
        F.append(PageBreak())
        F.append(Paragraph(f"7 · {_dlbl} — design vs realized", S["h2"]))
        F.append(bode_flowable(bode, CONTENT_WL, CONTENT_HL - 46,
                               show_phase=False, show_gd=False,
                               show_mc=show_mc,
                               xlim=(flo, fhi),
                               ylim=_passband_ylim(bode, flo, fhi),
                               title=_dlbl))
        if show_mc:
            F.append(Paragraph("Monte-Carlo: " + mc_caption(bode.get("mc_params")),
                               S["small"]))

    if opts.get("warnings") and ctx.get("warnings"):
        F.append(NextPageTemplate("portrait"))
        F.append(PageBreak())
        F.append(Paragraph("8 · Design notes & warnings", S["h2"]))
        for wmsg in ctx["warnings"]:
            F.append(Paragraph("• " + str(wmsg), S["body"]))
            F.append(Spacer(1, 3))

    doc.build(F)
    return buf.getvalue()


def _passband_ylim(bode, flo, fhi):
    f = np.asarray(bode["f"], float)
    m = (f >= flo) & (f <= fhi)
    if not m.any():
        return None
    vals = np.concatenate([_mag_db(np.asarray(bode["ideal"])[m]),
                           _mag_db(np.asarray(bode["realized"])[m])])
    vals = vals[np.isfinite(vals)]
    if not vals.size:
        return None
    lo, hi = float(vals.min()), float(vals.max())
    pad = max((hi - lo) * 0.35, 1.0)
    return (lo - pad, hi + pad)
