# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""
spice_ui.py — "LTspice export" block of the Resulting Response tab (FS-008).

Phase 1 (netlist-first): Vs, the Monte-Carlo run count, a per-section model
table and one zip download with the AC nominal and AC Monte-Carlo netlists
plus a README. All file content comes from spice_export (no Streamlit there);
this module only gathers the inputs and shows the result. The bundle is plain
string building, so it is rebuilt on every render -- no Generate button.

New widget keys: spice_vs, spice_mc_runs.
"""

import pandas as pd
import streamlit as st

import spice_export as SX
from topology_tab import opamp_label


def _eng(x):
    """SPICE text for display: '3Meg' -> '3 M', '10T' -> '10 T'."""
    t = SX.fmt_value(x).replace("Meg", "M")
    return t[:-1] + " " + t[-1] if t[-1].isalpha() else t + " "


def _model_text(p):
    ro = _eng(p["Ro_ohm"]) if p["Ro_ohm"] else "0 "
    return (f"FS generic · A_ol {_eng(p['A_ol']).strip()} · "
            f"GBWP {_eng(p['GBWP_hz'])}Hz · Ro {ro}Ω")


def render_spice_export(sections_data, mc_params):
    """Draw the export block. `sections_data` / `mc_params` are the Resulting
    Response tab's own (stage order; every section already has a BOM pick)."""
    st.markdown("---")
    st.markdown("##### LTspice export")
    st.caption("SPICE netlists of the whole cascade — sections in series, so LTspice "
               "simulates the real inter-stage loading — with every op-amp as the "
               "**FS generic** model (the tool's own A_ol / GBWP / Ro). Two files: AC "
               "nominal and AC Monte-Carlo (tolerances from the settings above), plus a "
               "README with the expected probe values. FS-008 phase 1: schematics "
               "(.asc) and real op-amp models follow.")

    cc = st.columns([1, 1, 3])
    vs = float(cc[0].number_input("Supply Vs (V)", value=5.0, min_value=0.1,
                                  max_value=100.0, step=0.5, key="spice_vs",
                                  help="Written as two Vs/2 sources with GND at the "
                                       "midpoint. The FS generic model is linear, so Vs "
                                       "only matters once real op-amp models are used."))
    runs = int(cc[1].number_input("MC runs", value=int(mc_params.get("n_runs", 2000)),
                                  min_value=1, max_value=20000, step=100,
                                  key="spice_mc_runs",
                                  help="Defaults to the Runs setting above. LTspice "
                                       "slows down plotting thousands of traces."))

    data = [dict(d, opamp_label=opamp_label(d["n"]) or "Ideal") for d in sections_data]
    try:
        exp = SX.build_export(data, vs=vs, mc_params=mc_params, n_runs=runs,
                              spec=st.session_state.get("report_spec_short", "filter"))
    except Exception as e:                      # never break the tab over an export
        st.error(f"LTspice export failed: {e}")
        return

    st.dataframe(pd.DataFrame([{
        "Section": inf["n"],
        "Cell": inf["topology"],
        "Op-amp (tool)": inf["opamp_label"],
        "SPICE model": _model_text(inf["params"]),
        "DC path": "ok" if not inf["floating"] else "floating: " + ", ".join(inf["floating"]),
    } for inf in exp["sections"]]), hide_index=True, use_container_width=True)

    for w in exp["warnings"]:
        st.warning(w)
    st.caption(f"Inter-stage loading: the exported cascade differs from the tool's "
               f"realized curve (a product of unloaded sections) by at most "
               f"{exp['loaded_dev_db']:.3f} dB within 60 dB of the peak. The README "
               f"lists the expected LTspice probe values.")
    if runs > 1000:
        st.caption(f"{runs} Monte-Carlo runs: fast to simulate, slow for LTspice to plot.")

    st.download_button("⬇ Download LTspice netlists (.zip)", data=SX.zip_bytes(exp),
                       file_name=exp["zip_name"], mime="application/zip",
                       key="spice_dl")
