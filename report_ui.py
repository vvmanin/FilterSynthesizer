# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""
report_ui.py — the "Generate Report" block at the foot of the
"Resulting Response & Schematic" tab.

Keeps response_tab.py thin: it owns the checkbox set, the generation gate, the
context assembly from session_state, and the download button. All PDF drawing
lives in report_pdf.py (pure, streamlit-free).

GATE
----
A report may only be generated when every realizable section has a solved and
SELECTED BOM. response_tab returns early in exactly those cases, so it calls
`render_blocked(reason)` from each of its early-return points and
`render_report_section(...)` at the end. That way the "Generate Report" heading
is always present and always explains itself, instead of silently vanishing.
"""

import datetime
import hashlib

import streamlit as st

import schematic_svg as schematic
from topology_tab import opamp_label, _gain_label_for
from _version import __version__, APP_NAME

# ---------------------------------------------------------------------
#  OPTIONAL BACKEND
# ---------------------------------------------------------------------
# report_pdf pulls reportlab + matplotlib. Those are OPTIONAL at runtime: this
# module is imported by response_tab, which is imported by app.py at line 17,
# so an eager `import report_pdf` here would take the whole application down on
# any machine that has not installed them yet. Load it on first use instead and
# degrade to a disabled button that says what to install.
_RP = None
_RP_ERR = None


def _backend():
    global _RP, _RP_ERR
    if _RP is None and _RP_ERR is None:
        try:
            import report_pdf
            _RP = report_pdf
        except Exception as e:                       # ImportError and anything else
            _RP_ERR = e
    return _RP


def _missing_module(err):
    name = getattr(err, "name", None)
    if name:
        return name.split(".")[0]
    txt = str(err)
    if "No module named" in txt:
        return txt.split("No module named")[-1].strip().strip("'\"").split(".")[0]
    return None


def _render_backend_missing():
    """Shown in place of the controls when reportlab / matplotlib are absent."""
    mod = _missing_module(_RP_ERR)
    st.markdown("---")
    st.markdown("##### Generate Report")
    if mod:
        st.warning(f"PDF report support needs the `{mod}` package, which is not "
                   f"installed in this environment.")
    else:
        st.warning(f"PDF report support could not be loaded: {_RP_ERR}")
    st.code("pip install reportlab matplotlib cairosvg", language="bash")
    st.caption("matplotlib draws the print-size figures and supplies the "
               "DejaVuSans font; reportlab builds the PDF; cairosvg rasterizes "
               "the section schematics (optional — without it the schematic "
               "pages print a placeholder and everything else still works).")
    st.button("📄 Generate Report", disabled=True, key="report_btn_nobackend")


_R_SERIES = ("E12", "E24", "E48", "E96")


# ---------------------------------------------------------------------
def _section_env(n):
    """Solver environment for section `n`, read straight from the Topology
    tab's widget keys (no extra plumbing needed in topology_tab.py). The
    1st-order settings block omits C_min / R_min / R_max, hence the .get()s."""
    r_sel = [s for s in _R_SERIES if st.session_state.get(f"hw_rser_{n}_{s}")]
    rmin_k = st.session_state.get(f"hw_rmin_{n}")
    rmax_k = st.session_state.get(f"hw_rmax_{n}")
    return dict(
        C_min=st.session_state.get(f"hw_cmin_{n}"),
        C_max=st.session_state.get(f"hw_cmax_{n}"),
        R_min=(rmin_k * 1e-3) if rmin_k else None,       # kΩ -> MΩ
        R_max=(rmax_k * 1e-3) if rmax_k else None,
        C_series=st.session_state.get(f"hw_cser_{n}") or "—",
        R_series=", ".join(r_sel) if r_sel else "—",
    )


def _meta(title, project, author):
    eng = st.session_state.get("report_engine") or {}
    gen = st.session_state.get("hw_gen")
    dh = hashlib.sha1(repr((gen, eng.get("k"))).encode()).hexdigest()[:7]
    return dict(title=title or "Filter Synthesis Report",
                subtitle=st.session_state.get("report_subtitle", ""),
                project=project or "", author=author or "",
                date=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                tool_version=f"{APP_NAME} v{__version__}", design_hash=dh)


def _build_ctx(sections_data, picked, f, ideal, realized, mc, mc_params,
               marks, warnings, meta, ideal_gd=None, realized_gd=None):
    """session_state + the cascade arrays response_tab already computed -> the
    plain-data context report_pdf.build_report consumes."""
    engine = dict(st.session_state.get("report_engine") or {})
    pairing = st.session_state.get("report_pairing") or {}
    stages = pairing.get("stages", [])

    # The user-specified overall passband gain, as typed in the sidebar.
    # engine["gain_units"] is exactly that number (app.py publishes
    # final_gain_units), so no extra plumbing is needed.
    spec_gain = float(engine.get("gain_units") or 0.0)
    n_sec = len(sections_data)

    sections = []
    for d in sections_data:
        n, row = d["n"], d["row"]
        topo = row.get("topology")
        try:
            svg = schematic.render_svg(topo, n, row, opamp_pn=opamp_label(n))
        except Exception:
            svg = None
        # Gain error reference. With ONE section the section carries the whole
        # filter gain, so the number worth knowing is "did I get the 10 V/V I
        # asked for" -> compare against the user-specified gain. With a cascade
        # the section only carries its share, and comparing its gain to the
        # filter's would print nonsense (a 3.16 V/V stage against a 10 V/V
        # target reads as -68%), so the reference falls back to that section's
        # own design target. The label follows the reference so the cell can
        # never be read as the wrong quantity.
        dc = row.get("_dc")
        dc_design = d.get("eff_dc")          # topology_tab.section_dc_gain(sec)
        ref = spec_gain if (n_sec == 1 and spec_gain) else dc_design
        err = None
        if dc is not None and ref:
            try:
                err = (abs(float(dc)) / abs(float(ref)) - 1.0) * 100.0
            except ZeroDivisionError:
                err = None
        sections.append(dict(
            n=n, topology=topo, kind=d.get("kind"), svg=svg, row=row,
            cont_row=(st.session_state.get("bom_picks_cont") or {}).get(n),
            opamp_name=opamp_label(n) or "Ideal (no op-amp limits)",
            opamp_params=(d.get("eval_opamp") if isinstance(d.get("eval_opamp"), dict)
                          and d["eval_opamp"].get("GBWP_hz") else None),
            env=_section_env(n),
            metrics=dict(sens=row.get("sens_score"), snap_cost=row.get("snap_cost"),
                         dc=dc, dc_label=_gain_label_for(topo),
                         dc_design=dc_design, spec_gain=spec_gain or None,
                         gain_err_pct=err, single_section=(n_sec == 1)),
        ))

    pb = st.session_state.get("report_passband")
    return dict(meta=meta,
                passbands=st.session_state.get("report_passbands") or [],
                spec=st.session_state.get("report_spec", []),
                engine=engine, stages=stages, sections=sections,
                bode=dict(f=f, ideal=ideal, realized=realized, mc=mc,
                          mc_params=mc_params, marks=marks,
                          ideal_gd=ideal_gd, realized_gd=realized_gd,
                          passband=pb,
                          detail_label=st.session_state.get(
                              "report_detail_label") or "Passband detail"),
                warnings=warnings or [])


# ---------------------------------------------------------------------
def render_blocked(reason):
    """The Generate-Report block in its disabled state."""
    st.markdown("---")
    st.markdown("##### Generate Report")
    st.warning(f"⚠ Report generation is not available yet — {reason}")
    st.button("📄 Generate Report", disabled=True, key="report_btn_blocked",
              help="Solve and select a BOM for every section first.")


_SNAPSHOT_KEYS = {
    "report_spec": "the sidebar specification",
    "report_engine": "the engine poles/zeros/K",
    "report_pairing": "the per-section root assignment",
}


def render_report_section(sections_data, picked, f, ideal, realized,
                          mc, mc_params, marks, warnings, run_mc=None,
                          ideal_gd=None, realized_gd=None):
    """Checkboxes -> Generate -> download. Called only when the gate is open."""
    if _backend() is None:
        _render_backend_missing()
        return

    # app.py publishes three plain-data snapshots (the sidebar spec, the engine
    # result and the per-stage roots) because none of them are reachable from
    # this module otherwise. Without them the report would build but come out
    # blank, so say so plainly rather than emitting a useless PDF.
    _missing = [d for k, d in _SNAPSHOT_KEYS.items()
                if not st.session_state.get(k)]
    if _missing:
        st.markdown("---")
        st.markdown("##### Generate Report")
        st.error("Report data is not being published by `app.py` — missing "
                 + ", ".join(_missing) + ".")
        st.caption("Apply patches **1a** and **1b** from "
                   "`REPORT_INTEGRATION.md` (the two `st.session_state[\"report_*\"]` "
                   "snapshot blocks in `app.py`), then reload the page.")
        st.button("📄 Generate Report", disabled=True, key="report_btn_nodata")
        return

    st.markdown("---")
    st.markdown("##### Generate Report")
    st.caption("A4 PDF summary: specification, transfer function, pole–zero map "
               "with section pairing, per-section schematic + BOM, and the "
               "design-vs-realized Bode page.")

    c = st.columns(3)
    with c[0]:
        o_cover = st.checkbox("Cover page", value=True, key="rep2_cover")
        o_coeff = st.checkbox("Polynomial coefficient table", value=True,
                              key="rep2_coeff")
        o_norm = st.checkbox("Normalized prototype roots", value=True,
                             key="rep2_norm")
    with c[1]:
        o_metrics = st.checkbox("Per-section quality metrics", value=True,
                                key="rep2_metrics",
                                help="Sensitivity score, snap cost, realized vs "
                                     "target passband gain.")
        o_warn = st.checkbox("Design warnings", value=True, key="rep2_warn",
                             disabled=not warnings,
                             help="Carry over the HF-resonance / finite-Rₒ notes "
                                  "raised above." if warnings
                                  else "No warnings were raised for this design.")
        o_phgd = st.checkbox("Phase & group delay on Bode page", value=True,
                             key="rep2_phgd")
    with c[2]:
        o_mc = st.checkbox("Monte-Carlo band + parameters", value=True,
                           key="rep2_mc")
        _dlbl = st.session_state.get("report_detail_label") or "Passband detail"
        o_zoom = st.checkbox(f"{_dlbl} plot", value=True, key="rep2_zoom",
                             help="Design vs realized over the band of "
                                  "interest only, with the same Monte-Carlo "
                                  "shading.")

    if o_cover:
        m = st.columns(3)
        title = m[0].text_input("Report title", value="Filter Synthesis Report",
                                key="rep2_title")
        project = m[1].text_input("Project", value="", key="rep2_project")
        author = m[2].text_input("Author", value="", key="rep2_author")
    else:
        title, project, author = "Filter Synthesis Report", "", ""

    opts = dict(cover=o_cover, coeff_table=o_coeff, metrics=o_metrics,
                mc=o_mc, phase_gd=o_phgd, normalized_roots=o_norm,
                passband_zoom=o_zoom, warnings=o_warn)

    if o_mc and mc is None:
        st.caption("Monte-Carlo has not been run (or its inputs changed). "
                   "Generating the report will run it once with the settings "
                   "above — this adds a few seconds.")

    gen_key = repr((st.session_state.get("hw_gen"), sorted(opts.items()),
                    title, project, author, mc is not None))

    if st.button("📄 Generate Report", type="primary", key="report_btn"):
        try:
            with st.spinner("Building PDF report…"):
                use_mc = mc
                if o_mc and use_mc is None and run_mc is not None:
                    use_mc = run_mc()
                ctx = _build_ctx(sections_data, picked, f, ideal, realized,
                                 use_mc, mc_params, marks, warnings,
                                 _meta(title, project, author),
                                 ideal_gd=ideal_gd, realized_gd=realized_gd)
                pdf = _backend().build_report(ctx, opts)
            st.session_state["report_pdf"] = {"key": gen_key, "bytes": pdf}
        except Exception as e:
            st.session_state.pop("report_pdf", None)
            st.error(f"Report generation failed: {e}")

    store = st.session_state.get("report_pdf")
    if store:
        spec = st.session_state.get("report_spec_short", "filter")
        fname = (f"FilterReport_{spec}_"
                 f"{datetime.datetime.now():%Y%m%d_%H%M}.pdf")
        st.download_button("⬇ Download PDF", data=store["bytes"],
                           file_name=fname, mime="application/pdf",
                           key="report_dl")
        if store["key"] != gen_key:
            st.caption("Settings changed since this PDF was built — press "
                       "**Generate Report** again to refresh it.")
