# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""
hw_plots.py — cascade Bode (ideal/design vs realized hardware) + Monte-Carlo
envelope, for the "Resulting Response & Schematic" tab.

The NUMERIC CORE (per-section target/realized responses, cascade product,
Monte-Carlo band) is pure NumPy and has NO plotly dependency, so it imports
and unit-tests without plotly. The FIGURE BUILDER lazy-imports plotly inside
the function (mirrors plot_utils.py), so this module is safe to import even
where plotly is absent.

Units note: the BOM rows use the solver's internal units — capacitors in uF,
resistors in MOhm — and the response functions (tf_derivation.make_response_func)
expect those same units plus the op-amp symbols A_ol / GBWP_hz / Ro. We never
convert; we just pass the row values straight through.
"""

import numpy as np
import sympy as sp


# =====================================================================
#  Per-section responses (plotly-free)
# =====================================================================
def comp_dict(row, names, eval_opamp):
    """Build the {symbol: value} dict make_response_func's H() needs.

    Components come from the BOM row (uF / MOhm); op-amp symbols (A_ol,
    GBWP_hz, Ro) come from eval_opamp. Split parallel C2 collapses to the
    single TF symbol C2 = C2a + C2b (parallel caps add)."""
    cd = {}
    for nm in names:
        v = row.get(nm)
        if v is not None:
            cd[nm] = v
        elif nm in eval_opamp:
            cd[nm] = eval_opamp[nm]
        elif nm == "C2" and row.get("C2a") is not None and row.get("C2b") is not None:
            cd[nm] = row["C2a"] + row["C2b"]
        elif nm == "C1" and row.get("C1a") is not None and row.get("C1b") is not None:
            cd[nm] = row["C1a"] + row["C1b"]     # -C1s twin: C1 = C1a || C1b

    # FAIL LOUDLY on a component the TF needs but the BOM does not carry as a
    # real, non-zero value. unified_solver_v2._assemble fills every ABSENT
    # designator with 0.0 (so the schematic can suppress its label), which means
    # a mismatch between the TF's symbol list and the row -- a stale cached TF
    # for a cell whose netlist changed under the same name, say -- would silently
    # evaluate with a ZERO capacitor or a SHORTED resistor. The response then
    # loses its notch and the stopband plateau lifts, with no error raised.
    # A wrong Bode plot is far worse than a traceback, so raise.
    bad = [nm for nm in names
           if nm not in cd or cd[nm] is None
           or (nm[0] in "RC" and not (float(cd[nm]) > 0.0))]
    if bad:
        raise KeyError(
            f"comp_dict: transfer function needs {bad} but the BOM row "
            f"(topology {row.get('topology')!r}) has no positive value for them. "
            f"This usually means a STALE cached response function -- the cell's "
            f"netlist changed under an unchanged name. Restart the app / clear "
            f"caches, and make sure the cache key is salted with "
            f"tf_derivation_v2.cell_struct_sig().")
    return cd


def realized_response(H, names, row, eval_opamp, w):
    """Complex response of one section's realized BOM over angular freq w."""
    return H(comp_dict(row, names, eval_opamp), np.asarray(w, dtype=float))


def target_response(sec, w, eff_dc=1.0):
    """Per-section *design* (math) target, normalized to unity passband then
    scaled to the section's effective passband gain. LP/LPn normalize at DC, HP/
    HPn at the HF plateau (the passband side). K cancels in the normalization.

    The passband side comes from the authoritative family classifier
    (family_from_section), NOT a raw sec['family'] lookup: hw_sections dicts
    carry no 'family' key and an HPn notch has no origin zeros, so the old
    `sec.get('family')`/has_origin_zero test misread every HPn as LP and
    normalized it at DC -- collapsing the cascade passband gain (e.g. an LPn·HPn
    band-reject read 1.5x instead of 1.0x)."""
    from discrete_snapper import get_T_target
    from pairing_utils import family_from_section
    cfg = {"f0": sec["f0_hz"], "Q": sec["Q"],
           "f1": sec.get("f1_hz") or sec["f0_hz"],
           "fz": sec.get("fz_hz") or sec["f0_hz"]}
    order = sec["order"]
    notch = sec["notch"]
    fam = family_from_section(sec)
    # BP1LP / BP1HP are the 3rd-order asymmetric band-pass shapes (complex pair
    # + absorbed real pole). They normalize at the resonant peak exactly like a
    # plain BP; only the NUMERATOR degree differs, which get_T_target takes via
    # `absorb`. Without this they would fall through to the is_hp test below --
    # they DO carry origin zeros -- and be normalized on an HF plateau that does
    # not exist, collapsing the section's contribution to the cascade.
    absorb = {"BP1HP": "hp", "BP1LP": "lp"}.get(fam)
    is_bp = (fam == "BP") or (absorb is not None)
    is_hp = (not is_bp) and ((fam in ("HP", "HPn")) or bool(sec.get("has_origin_zero")))
    w = np.asarray(w, dtype=float)
    if order == 1:
        # 1st-order design target, unity-normalized (DC=1 LP / HF=1 HP) then
        # scaled by eff_dc (the section's passband gain). K cancels here too.
        w0 = 2 * np.pi * sec["f0_hz"]
        jw = 1j * w
        T = (jw / (jw + w0)) if is_hp else (w0 / (jw + w0))
        return eff_dc * T
    family = "BP" if is_bp else ("HP" if is_hp else "LP")
    T = get_T_target(w, cfg, order=order, notch=notch, family=family,
                     absorb=absorb)
    if is_bp:
        # Band-pass passband is the resonant peak at f0; normalize |T| there so
        # the overlay's passband sits at eff_dc (the center gain at f0). For the
        # 3rd-order asymmetric cells the TRUE peak is pulled off f0 by the
        # absorbed real pole, but f0 is where eff_dc is defined (section_dc_gain
        # evaluates there too), so both sides of the overlay use the same point
        # and the normalization stays consistent.
        w0 = 2 * np.pi * cfg["f0"]
        T0 = get_T_target(np.array([w0]), cfg, order=order, notch=notch,
                          family="BP", absorb=absorb)[0]
    elif is_hp:
        # HP passband is at HF: normalize at a frequency well above every pole
        # and zero, where |T| has settled to its leading-coeff ratio (the math
        # target has no op-amp rolloff, so it plateaus there). DC would be ~0.
        w_hi = 2 * np.pi * max(cfg["f0"], cfg["f1"], cfg["fz"]) * 1.0e3
        T0 = get_T_target(np.array([w_hi]), cfg, order=order, notch=notch,
                          family="HP")[0]
    else:
        T0 = get_T_target(np.array([0.0]), cfg, order=order, notch=notch,
                          family="LP")[0]
    if abs(T0) < 1e-300:
        T0 = 1.0
    return eff_dc * T / T0


def cascade(arrs):
    """Product of section responses (the overall cascade transfer)."""
    out = np.ones_like(np.asarray(arrs[0]), dtype=complex)
    for a in arrs:
        out = out * a
    return out


# =====================================================================
#  Scalar transforms
# =====================================================================
def mag_db(H):
    return 20.0 * np.log10(np.maximum(np.abs(H), 1e-12))


def phase_deg(H):
    return np.degrees(np.unwrap(np.angle(H)))


def group_delay_s(w, H):
    """Group delay in seconds via phase derivative (fallback only — blows up at
    notches because the unwrapped phase steps sharply there). Prefer the
    analytic forms below for anything with transmission zeros."""
    ph = np.unwrap(np.angle(H))
    return -np.gradient(ph, np.asarray(w, dtype=float))


# ---- analytic group delay (clean at notches) ----------------------------
def _laplace_symbol(case):
    return (set(case["tf_den"].free_symbols) - set(case["tf_var_list"])).pop()


def build_loggrad(case):
    """Lambdify H'(s)/H(s) = (N'D - ND')/(DN) for a cell. Returns (fn, names);
    fn(s_array, *component_values) with component order = names. Group delay is
    then -Re(fn(jw, ...)). Exact — no phase-unwrap, no rooting, smooth at notches
    (realized zeros are slightly off-axis, so H'/H stays large-but-finite)."""
    if case.get("tf_num") is None and case.get("mna"):
        # AM non-ideal cases are lightweight (numeric MNA) and carry no symbolic
        # TF; analytic group delay needs the closed form, so materialize it now
        # (lazy, cached one cell at a time by the caller's @st.cache_resource).
        import tf_derivation_v2 as _TF
        _TF.ensure_symbolic_tf(case)
    s = _laplace_symbol(case)
    num, den = case["tf_num"], case["tf_den"]
    hh = (sp.diff(num, s) * den - num * sp.diff(den, s)) / (den * num)
    names = [str(v) for v in case["tf_var_list"]]
    return sp.lambdify([s] + list(case["tf_var_list"]), hh, "numpy"), names


def group_delay_section(hh_fn, names, comp_dict, w):
    """Realized section group delay (seconds) from build_loggrad's fn. Cascade
    group delays ADD, so sum this over sections."""
    vals = [comp_dict[n] for n in names]
    return -np.real(hh_fn(1j * np.asarray(w, dtype=float), *vals))


def ideal_poles_zeros(sec):
    """Design (ideal) poles/zeros in rad/s for a section's target. Notch zeros
    sit on the jw axis -> their group-delay contribution is ~0 (matches the
    math tab, which shows no notch feature in GD)."""
    w0 = 2 * np.pi * sec["f0_hz"]
    Q = sec["Q"]
    if sec["order"] == 1:
        # one real pole at -w0; 1st-order HP adds a jw-axis zero at the origin
        # (its group-delay contribution is ~0, matching the math tab).
        is_hp = (sec.get("family") == "HP") or bool(sec.get("has_origin_zero"))
        return np.array([-w0 + 0j]), (np.array([0j]) if is_hp else np.array([]))
    b = w0 / Q
    disc = b * b - 4.0 * w0 * w0 + 0j
    poles = [(-b + np.sqrt(disc)) / 2.0, (-b - np.sqrt(disc)) / 2.0]
    if sec["order"] == 3 and sec.get("f1_hz"):
        poles.append(-2 * np.pi * sec["f1_hz"] + 0j)
    zeros = []
    if sec["notch"] and sec.get("fz_hz"):
        wz = 2 * np.pi * sec["fz_hz"]
        zeros = [1j * wz, -1j * wz]
    return np.array(poles), np.array(zeros)


def group_delay_pz(w, poles, zeros):
    """Analytic group delay (seconds) from poles/zeros — same formula the math
    tab uses: sum Re(1/(jw-p)) - sum Re(1/(jw-z))."""
    jw = 1j * np.asarray(w, dtype=float)
    gd = np.zeros_like(np.asarray(w, dtype=float))
    if len(poles):
        gd += np.sum(np.real(1.0 / (jw[:, None] - np.asarray(poles))), axis=1)
    if len(zeros):
        gd -= np.sum(np.real(1.0 / (jw[:, None] - np.asarray(zeros))), axis=1)
    return gd


# =====================================================================
#  Monte-Carlo (plotly-free)
# =====================================================================
def _r_tol_frac(value_mohm, r_bands, default_pct):
    """Resistor tolerance fraction from the first matching value band.
    r_bands: list of (min_ohm, max_ohm, tol_pct). value is in MOhm."""
    v_ohm = value_mohm * 1e6
    for lo, hi, tol in r_bands:
        if lo <= v_ohm <= hi:
            return tol / 100.0
    return default_pct / 100.0


def monte_carlo(sections, w, n_runs=200, c_tol_pct=5.0, r_bands=None,
                default_r_tol_pct=1.0, dist="gaussian", seed=0,
                lo_pct=1.0, hi_pct=99.0, n_sigma=3.0):
    """Cascade Monte-Carlo over component tolerances.

    sections : list of {"H", "names", "row", "eval_opamp"} (one per stage).
    Perturbs every present resistor (per its value band) and capacitor
    (c_tol_pct); op-amp parameters are held fixed. Returns magnitude-dB
    percentile bands of the cascade plus the per-run array.

    The rated tolerance is the *spec limit* for both distributions:
      dist="uniform"  -> part is uniform on [nominal*(1-tol), nominal*(1+tol)]
      dist="gaussian" -> sigma = tol / n_sigma, so +/-tol == +/- n_sigma*sigma
                         (n_sigma=3 -> ~99.7% of parts within rated tolerance).
    """
    w = np.asarray(w, dtype=float)
    rng = np.random.default_rng(seed)
    r_bands = r_bands or [(0.0, 1e12, default_r_tol_pct)]

    # Per-section nominal comp dict + per-symbol tolerance fraction.
    plan = []
    for sd in sections:
        cd0 = comp_dict(sd["row"], sd["names"], sd["eval_opamp"])
        tol = {}
        for nm, val in cd0.items():
            if nm.startswith("R"):
                tol[nm] = _r_tol_frac(val, r_bands, default_r_tol_pct)
            elif nm.startswith("C"):
                tol[nm] = c_tol_pct / 100.0
            else:
                tol[nm] = 0.0          # op-amp symbols: fixed
        keys = [k for k in cd0 if tol[k] > 0.0]
        plan.append((sd["H"], cd0, tol, keys))

    def draw(n):
        if dist == "uniform":
            return rng.uniform(-1.0, 1.0, n)            # exactly +/-tol, flat
        return rng.standard_normal(n) / n_sigma          # tol == n_sigma * sigma

    mags = np.empty((n_runs, w.size))
    for i in range(n_runs):
        casc = np.ones(w.size, dtype=complex)
        for H, cd0, tol, keys in plan:
            cd = dict(cd0)
            if keys:
                z = draw(len(keys))
                for k, zk in zip(keys, z):
                    v = cd0[k] * (1.0 + tol[k] * zk)
                    cd[k] = v if v > 0.0 else cd0[k] * 1e-3   # never flip sign / hit 0
            casc = casc * H(cd, w)
        mags[i] = mag_db(casc)

    mags = np.where(np.isfinite(mags), mags, np.nan)
    return {
        "lo": np.nanpercentile(mags, lo_pct, axis=0),
        "hi": np.nanpercentile(mags, hi_pct, axis=0),
        "median": np.nanpercentile(mags, 50.0, axis=0),
        "mags": mags,
        "n_runs": int(n_runs),
        "lo_pct": float(lo_pct),
        "hi_pct": float(hi_pct),
    }


# =====================================================================
#  Figure builder (lazy plotly — mirrors plot_utils.py styling)
# =====================================================================
# colors
_C_IDEAL = "#1f6fb2"     # design/ideal magnitude (blue — matches the math tab)
_C_REAL = "#d1495b"      # realized BOM magnitude (red)
_C_BAND = "rgba(120,130,150,0.50)"
_C_MEDIAN = "rgba(90,100,120,0.95)"
_C_GD_I = "#2e8b57"
_C_GD_R = "#9aa648"


def bode_figure(f_hz, ideal, realized, mc=None, show_phase=False, show_gd=False,
                section_marks=None, freq_unit="Hz", multiplier=1.0,
                ideal_gd=None, realized_gd=None, show_linear=False,
                title="Cascade response — design vs realized"):
    """Build the cascade Bode figure.

    f_hz      : frequency grid (Hz)
    ideal     : complex cascade of the design targets
    realized  : complex cascade of the realized BOMs
    mc        : monte_carlo() result dict, or None
    show_phase/show_gd : overlay phase (deg) / group delay (ms) on right axes
    ideal_gd/realized_gd : precomputed group-delay arrays in ms (analytic, clean
                 at notches). If None, falls back to phase differentiation.
    section_marks : list of (f_hz, "f0"|"fz") for light corner vlines
    """
    import plotly.graph_objects as go

    w = 2.0 * np.pi * np.asarray(f_hz, dtype=float)
    x = np.asarray(f_hz, dtype=float) / multiplier
    fig = go.Figure()

    # Magnitude mapper -- dB by default, linear when show_linear=True. Applied
    # uniformly to the MC band (which is stored in dB), the MC median, and the
    # ideal/realized traces, so the whole magnitude axis stays in one scale.
    def _mag_of_H(H):       # complex H -> displayed magnitude
        return np.abs(H) if show_linear else mag_db(H)
    def _mag_of_db(d):      # stored-dB array -> displayed magnitude
        return np.power(10.0, np.asarray(d, float)/20.0) if show_linear else d
    _mag_title = "Magnitude (linear)" if show_linear else "Magnitude (dB)"

    # --- Monte-Carlo band (magnitude) — robust filled polygon (toself) ---
    if mc is not None:
        xb = np.concatenate([x, x[::-1]])
        yb = np.concatenate([_mag_of_db(mc["hi"]),
                             _mag_of_db(mc["lo"])[::-1]])
        fig.add_trace(go.Scatter(
            x=xb, y=yb, mode="lines", fill="toself", fillcolor=_C_BAND,
            line=dict(width=0), hoverinfo="skip",
            name=f"MC band p{mc['lo_pct']:g}–p{mc['hi_pct']:g} ({mc['n_runs']} runs)"))
        fig.add_trace(go.Scatter(x=x, y=_mag_of_db(mc["median"]), mode="lines",
                                 line=dict(color=_C_MEDIAN, width=1, dash="dot"),
                                 name="MC median"))

    # --- magnitude (left axis) ---
    fig.add_trace(go.Scattergl(x=x, y=_mag_of_H(ideal), mode="lines",
                               line=dict(color=_C_IDEAL, width=1.9),
                               name="Ideal (design)"))
    fig.add_trace(go.Scattergl(x=x, y=_mag_of_H(realized), mode="lines",
                               line=dict(color=_C_REAL, width=1.6),
                               name="Realized (BOM)"))

    layout = dict(
        xaxis=dict(title=f"Frequency ({freq_unit})", type="log"),
        yaxis=dict(title=_mag_title),
        margin=dict(l=20, r=20, t=40, b=20),
        height=460,
        title=title,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    # right-side axes: phase -> y2, group delay -> y2 or y3
    right_axes = 0
    if show_phase:
        right_axes += 1
        fig.add_trace(go.Scattergl(x=x, y=phase_deg(ideal), mode="lines", yaxis="y2",
                                   line=dict(color=_C_IDEAL, width=1.5, dash="dash"),
                                   name="Ideal phase"))
        fig.add_trace(go.Scattergl(x=x, y=phase_deg(realized), mode="lines", yaxis="y2",
                                   line=dict(color=_C_REAL, width=1.5, dash="dash"),
                                   name="Realized phase"))
        layout["yaxis2"] = dict(title="Phase (deg)", overlaying="y", side="right",
                                showgrid=False)
    if show_gd:
        gd_axis = "y3" if show_phase else "y2"
        gi = np.asarray(ideal_gd if ideal_gd is not None else group_delay_s(w, ideal) * 1e3, float)
        gr = np.asarray(realized_gd if realized_gd is not None else group_delay_s(w, realized) * 1e3, float)
        # Realized notch zeros sit just off the jw axis -> a sharp, physically-real
        # GD feature ~1/|Re(zero)| at the notch. Cap to a readable window so it
        # doesn't dwarf the passband. Anchor on the ideal GD (clean: it carries the
        # true passband peak with no notch spike) plus the realized 3-97% bulk.
        gi_f = gi[np.isfinite(gi)]
        gr_f = gr[np.isfinite(gr)]
        ref = gi_f if gi_f.size else gr_f
        gd_range = None
        if ref.size:
            lo, hi = float(np.min(ref)), float(np.max(ref))
            if gr_f.size:
                lo = min(lo, float(np.percentile(gr_f, 3)))
                hi = max(hi, float(np.percentile(gr_f, 97)))
            span = max(hi - lo, 1e-3)
            lo, hi = lo - 0.2 * span, hi + 0.2 * span
            gi = np.clip(gi, lo, hi)
            gr = np.clip(gr, lo, hi)
            gd_range = [lo, hi]
        fig.add_trace(go.Scattergl(x=x, y=gi, mode="lines", yaxis=gd_axis,
                                   line=dict(color=_C_GD_I, width=1.5, dash="dot"),
                                   name="Ideal group delay"))
        fig.add_trace(go.Scattergl(x=x, y=gr, mode="lines", yaxis=gd_axis,
                                   line=dict(color=_C_GD_R, width=1.5, dash="dot"),
                                   name="Realized group delay"))
        if show_phase:
            # third axis floats just right of the second; shrink plot to fit
            layout["xaxis"]["domain"] = [0.0, 0.90]
            layout["yaxis3"] = dict(title="Group delay (ms)", overlaying="y",
                                    side="right", anchor="free", position=1.0,
                                    showgrid=False, range=gd_range)
        else:
            layout["yaxis2"] = dict(title="Group delay (ms)", overlaying="y",
                                    side="right", showgrid=False, range=gd_range)
        right_axes += 1

    # --- light section corner marks ---
    for fmark, kind in (section_marks or []):
        fig.add_vline(x=fmark / multiplier,
                      line=dict(color=("magenta" if kind == "fz" else "black"),
                                dash="dot", width=1),
                      opacity=0.35)

    fig.update_layout(**layout)
    return fig
