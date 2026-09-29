# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""
spice_ui.py — "LTspice export" block of the Resulting Response tab (FS-008).

Vs, the Monte-Carlo run count, the op-amp SPICE model per section (from the
part's `spice_model` in the op-amp library, overridable here), a per-section
table and one zip download with the AC nominal and AC Monte-Carlo schematics
(.asc) and netlists (.cir) plus a README. All file content comes from
spice_export (no Streamlit there); this module only gathers the inputs and
shows the result. The bundle is plain string building, so it is rebuilt on
every render -- no Generate button.

Widget keys: spice_vs, spice_mc_runs, spice_opamp_{n}, spice_include_models.
"""

import pandas as pd
import streamlit as st

import opamp_library as oplib
import spice_export as SX
import spice_opamps as SO
from topology_tab import opamp_label

_AUTO = "Auto"
_FS = "FS generic"


def _eng(x):
    """SPICE text for display: '3Meg' -> '3 M', '10T' -> '10 T'."""
    t = SX.fmt_value(x).replace("Meg", "M")
    return t[:-1] + " " + t[-1] if t[-1].isalpha() else t + " "


def _model_text(inf):
    if not inf["fs_generic"]:
        return f"{inf['stem']} · model {inf['model']}"
    p = inf["params"]
    ro = _eng(p["Ro_ohm"]) if p["Ro_ohm"] else "0 "
    return (f"FS generic · A_ol {_eng(p['A_ol']).strip()} · "
            f"GBWP {_eng(p['GBWP_hz'])}Hz · Ro {ro}Ω")


def _part_model(n):
    """The section's op-amp part -> its `spice_model` dummy stem, or None
    (FS generic). Ideal and Custom always use FS generic: their parameters
    are the tool's own model."""
    choice = st.session_state.get(f"hw_opamp_choice_{n}") or ""
    if choice in (oplib.IDEAL_LABEL, oplib.CUSTOM_LABEL):
        return None
    e = oplib.get(choice)
    return (e or {}).get("spice_model") or None


def _model_picker(sections_data, lib):
    """One override selectbox per section; returns {n: stem | None}."""
    usable = [s for s, d in lib.items() if not d["errors"] and s != SO.FS_GENERIC]
    out = {}
    cols = st.columns(min(len(sections_data), 4) or 1)
    for i, sd in enumerate(sections_data):
        n = sd["n"]
        auto = _part_model(n)
        opts = [_AUTO, _FS] + usable
        key = f"spice_opamp_{n}"
        if st.session_state.get(key) not in opts:
            st.session_state.pop(key, None)
        choice = cols[i % len(cols)].selectbox(
            f"Section {n} SPICE model", opts, key=key,
            format_func=lambda o, a=auto: (f"Auto ({a or _FS})" if o == _AUTO else o),
            help="Auto = the op-amp part's `spice_model` in the op-amp library (Edit "
                 "popover in the Topology tab); FS generic when it has none or for "
                 "Ideal / Custom.")
        out[n] = auto if choice == _AUTO else (None if choice == _FS else choice)
    return out


def render_spice_export(sections_data, mc_params):
    """Draw the export block. `sections_data` / `mc_params` are the Resulting
    Response tab's own (stage order; every section already has a BOM pick)."""
    st.markdown("---")
    st.markdown("##### LTspice export")
    st.caption("LTspice schematics (.asc) and netlists (.cir) of the whole cascade — sections "
               "in series, so LTspice simulates the real inter-stage loading. Each op-amp uses "
               "its part's SPICE model from the local op-amp model library, or **FS generic** "
               "(the tool's own A_ol / GBWP / Ro). AC nominal and AC Monte-Carlo (tolerances "
               "from the settings above), plus a README with the expected probe values.")

    cc = st.columns([1, 1, 3])
    vs = float(cc[0].number_input("Supply Vs (V)", value=5.0, min_value=0.1,
                                  max_value=100.0, step=0.5, key="spice_vs",
                                  help="Written as two Vs/2 sources with GND at the "
                                       "midpoint. FS generic is linear, so Vs only matters "
                                       "for real op-amp models."))
    runs = int(cc[1].number_input("MC runs", value=int(mc_params.get("n_runs", 2000)),
                                  min_value=1, max_value=20000, step=100,
                                  key="spice_mc_runs",
                                  help="Defaults to the Runs setting above. LTspice "
                                       "slows down plotting thousands of traces."))

    lib = SO.dummies()
    stems = _model_picker(sections_data, lib)
    include = st.checkbox("Include model files in the zip", key="spice_include_models",
                          help="Copies the model files the op-amp dummies reference into the "
                               "zip and references them by bare name (portable; extract the "
                               "zip first). Off: absolute paths on this machine.")

    data = [dict(d, opamp_label=opamp_label(d["n"]) or "Ideal", spice_model=stems[d["n"]])
            for d in sections_data]
    try:
        exp = SX.build_export(data, vs=vs, mc_params=mc_params, n_runs=runs,
                              include_models=include,
                              spec=st.session_state.get("report_spec_short", "filter"))
    except Exception as e:                      # never break the tab over an export
        st.error(f"LTspice export failed: {e}")
        return

    st.dataframe(pd.DataFrame([{
        "Section": inf["n"],
        "Cell": inf["topology"],
        "Op-amp (tool)": inf["opamp_label"],
        "SPICE model": _model_text(inf),
        "Drawing": inf["drawing"] if not exp["asc_error"] else "— (not written)",
        "DC path": "ok" if not inf["floating"] else "floating: " + ", ".join(inf["floating"]),
    } for inf in exp["sections"]]), hide_index=True, use_container_width=True)

    for w in exp["warnings"]:
        st.warning(w)
    if exp["all_generic"]:
        st.caption(f"Inter-stage loading: the exported cascade differs from the tool's "
                   f"realized curve (a product of unloaded sections) by at most "
                   f"{exp['loaded_dev_db']:.3f} dB within 60 dB of the peak. The README "
                   f"lists the expected LTspice probe values.")
    else:
        st.caption("Real op-amp models in use: LTspice will differ from the tool's curve by "
                   "the models' own behaviour. Run the commented `.op` once and check that "
                   "every section output sits near 0 V.")
    if runs > 1000:
        st.caption(f"{runs} Monte-Carlo runs: fast to simulate, slow for LTspice to plot.")

    with st.expander("Op-amp model library", expanded=False):
        rows = [{"Model": s, "Origin": d["origin"],
                 "Status": "ok" if not d["errors"] else "rejected: " + "; ".join(d["errors"]),
                 "Notes": "; ".join(d["warnings"] + ([d["meta"]["note"]] if d["meta"].get("note") else [])),
                 "File": d["path"]} for s, d in lib.items()]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        cal = SO.calibration()
        st.caption(f"Built-in: `{SO.library_dir()}`  \nYour models: "
                   f"`{SO.overlay_dir()}\\opamps` (+ `models\\` for their files); a file with a "
                   f"built-in's name overrides it.  \nSymbol calibration: `{cal.source}`"
                   + ("  \n" + "; ".join(cal.errors) if cal.errors else ""))

    st.download_button("⬇ Download LTspice files (.zip)", data=SX.zip_bytes(exp),
                       file_name=exp["zip_name"], mime="application/zip",
                       key="spice_dl")
