# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""
spice_ui.py — "LTspice export" block of the Resulting Response tab (FS-008).

Vs, the Monte-Carlo run count, a per-section table and one zip download with
the AC nominal and AC Monte-Carlo schematics (.asc) and netlists (.cir) plus
a README. Each section's op-amp model is picked automatically: the Topology
tab's part -> its `spice_model` dummy stem in the op-amp library (FS generic
for Ideal / Custom / parts without one). A per-section override is shown only
with FILTERSYNTHESIZER_DEBUG=1. Parts whose dummy needs a vendor model file
get the *simplified generic models* checkbox and the *Vendor model files*
panel (FS-029: per part a product-page link, consent, the user's own zip /
model file, subckt + pin roles confirmed, imported behind FS_<PART>.lib);
a part whose model is not imported yet is exported with FS generic.
All file content comes from spice_export (no Streamlit there); this module
only gathers the inputs and shows the result. The bundle is plain string
building, so it is rebuilt on every render -- no Generate button.

Widget keys: spice_vs, spice_mc_runs, spice_opamp_{n} (debug), spice_generic_vendor,
spice_vendor_consent_{stem}, spice_vendor_up_{stem}, spice_vendor_sub_{stem},
spice_vendor_role_{stem}_{subckt}_{k}, spice_vendor_inst_{stem},
spice_vendor_redo_{stem}, spice_vendor_allow.
"""

import pandas as pd
import streamlit as st

import opamp_library as oplib
import spice_export as SX
import spice_opamps as SO
from topology_tab import DEBUG_UI, opamp_label, settings_tag

_AUTO = "Auto"
_FS = "FS generic"

_DISCLAIMER = (
    "Vendor SPICE models are the vendors' copyrighted files. FilterSynthesizer does not "
    "ship them and never downloads them for you. Download the model yourself from the "
    "vendor's product page (link below); by downloading it you accept the vendor's terms. "
    "A file you import here is stored only on this PC, in your models folder, and is copied "
    "unmodified into **your own** LTspice export zips, together with a small wrapper the "
    "tool generates around it, so they run as they are. Until a part's model is imported, "
    "its sections are exported with the FS generic model. Vendor "
    "terms typically allow use only for your own design work with the vendor's parts "
    "(TI: *only for development of an application that uses the TI products*; other "
    "reproduction prohibited) — **do not share export zips that contain vendor model "
    "files**; the README in each such zip says which files they are.")


def _eng(x):
    """SPICE text for display: '3Meg' -> '3 M', '10T' -> '10 T'."""
    t = SX.fmt_value(x).replace("Meg", "M")
    return t[:-1] + " " + t[-1] if t[-1].isalpha() else t + " "


def _model_text(inf):
    if not inf["fs_generic"]:
        return f"{inf['stem']} · model {inf['model']}"
    p = inf["params"]
    ro = _eng(p["Ro_ohm"]) if p["Ro_ohm"] else "0 "
    return (("Model not imported · " if inf.get("not_installed") else
             "Simplified · " if inf["simplified"] else "")
            + f"FS generic · A_ol {_eng(p['A_ol']).strip()} · "
            f"GBWP {_eng(p['GBWP_hz'])}Hz · Ro {ro}Ω")


def _part_model(n):
    """The section's op-amp part -> its `spice_model` dummy stem, or None
    (FS generic). Ideal and Custom always use FS generic: their parameters
    are the tool's own model."""
    choice = st.session_state.get(f"hw_opamp_choice_{settings_tag(n)}") or ""
    if choice in (oplib.IDEAL_LABEL, oplib.CUSTOM_LABEL):
        return None
    e = oplib.get(choice)
    return (e or {}).get("spice_model") or None


def _model_picker(sections_data, lib):
    """Debug only: one override selectbox per section; returns {n: stem | None}."""
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
            help="Debug (FILTERSYNTHESIZER_DEBUG=1). Auto = the op-amp part's "
                 "`spice_model` in the op-amp library.")
        out[n] = auto if choice == _AUTO else (None if choice == _FS else choice)
    return out


def _vendor_use(stems, lib):
    """{stem: {dummy, sections, parts, files}} of the sections whose dummy
    needs a vendor model file."""
    out = {}
    for n, stem in stems.items():
        d = lib.get(stem) if stem else None
        if d is None or d["errors"] or d["fs_generic"]:
            continue
        vf = SO.vendor_files(d)
        if vf:
            u = out.setdefault(stem, {"dummy": d, "sections": [], "parts": [], "files": vf})
            u["sections"].append(n)
            lbl = opamp_label(n) or stem
            if lbl not in u["parts"]:
                u["parts"].append(lbl)
    return out


_ROLE_TEXT = {"INP": "in+", "INN": "in−", "VCC": "V+", "VEE": "V−", "OUT": "out"}


def _consent_box(stem, part):
    return st.checkbox(f"I download the {part} model from the vendor myself, under the "
                       f"vendor's terms, for my own design work, and will not share export "
                       f"zips that contain it", key=f"spice_vendor_consent_{stem}")


def _import_part(stem, u, part, installed):
    """FS-029: product-page link, consent, pick the vendor's zip / model file,
    confirm the model subckt and its pin roles, import behind FS_<PART>.lib."""
    src = u["dummy"]["meta"].get("source", "")
    who = ", ".join(u["parts"])
    if installed and not st.checkbox(f"Re-import the {who} model (a new revision)",
                                     key=f"spice_vendor_redo_{stem}"):
        return
    with st.container(border=True):
        st.markdown(f"**{who}** — import the vendor model")
        if src.startswith("http"):
            st.link_button(f"Open the {part} product page ↗", src)
        ok = _consent_box(stem, part)
        up = st.file_uploader("The vendor's zip or model file, from wherever you saved it "
                              "(.zip, .lib, .txt, .mod, .cir, …)",
                              key=f"spice_vendor_up_{stem}", disabled=not ok)
        cands = []
        if up is not None:
            try:
                cands = SO.model_candidates(up.name, up.getvalue(), part)
            except ValueError as e:
                st.error(f"{up.name}: {e}")
                return
        elif not installed:
            cands = SO.installed_candidates(part)
            if cands:
                st.caption(f"`{cands[0]['file']}` is already in your models folder — it can "
                           f"be imported without a new download.")
        if not cands or not ok:
            return
        i = 0
        if len(cands) > 1:
            i = st.selectbox("Model subcircuit", range(len(cands)),
                             format_func=lambda k: f"{cands[k]['subckt']}  ({cands[k]['file']}, "
                                                   f"{len(cands[k]['pins'])} pins)",
                             key=f"spice_vendor_sub_{stem}")
        c = cands[i]
        opts = list(SO.WRAP_PORTS)
        cols = st.columns(5)
        roles = [cols[k].selectbox(f"Pin {k + 1} `{pin}`", opts,
                                   index=opts.index(g) if g in opts else k,
                                   format_func=_ROLE_TEXT.get,
                                   key=f"spice_vendor_role_{stem}_{c['subckt']}_{k}")
                 for k, (pin, g) in enumerate(zip(c["pins"], c["roles"]))]
        st.caption(f"Subckt `{c['subckt']}` in `{c['file']}`. "
                   + ("Pin roles read from the model's pin names / pinout note — check them."
                      if c["confident"] else
                      "**Pin roles could not be read from the model** — set each from the "
                      "vendor's pinout note (the comment above its .subckt line)."))
        if st.button(f"Import the {part} model", key=f"spice_vendor_inst_{stem}", type="primary"):
            try:
                wrap, vend = SO.install_wrapped(part, c, roles, src)
            except (ValueError, OSError) as e:
                st.error(str(e))
            else:
                st.success(f"Imported {vend} (unchanged) behind {wrap}.")
                st.rerun()


def _vendor_panel(use):
    """Status, product links, disclaimer + consent and the import per vendor part."""
    rows, need = [], False
    recs = SO.part_records()
    for stem, u in use.items():
        for name, path in u["files"].items():
            rec = recs.get(SO.wrapper_part(name) or "")
            if path is None:
                status = "not imported — FS generic is used"
                need = True
            elif not SO.is_consented(path):
                status = "installed — not bundled (no consent recorded)"
                need = True
            elif rec:
                status = (f"imported {rec.get('accepted', '')[:10]}: {rec.get('original')}, "
                          f"subckt {rec.get('subckt')} — bundled into the zip")
            else:
                status = "installed — bundled into the zip"
            rows.append({"Part": ", ".join(u["parts"]), "Sections": ", ".join(map(str, u["sections"])),
                         "Model file": name, "Status": status,
                         "Vendor page": u["dummy"]["meta"].get("source") or ""})
    with st.expander("Vendor model files", expanded=need):
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                     column_config={"Vendor page": st.column_config.LinkColumn("Vendor page")})
        st.caption(f"Your models folder: `{SO.user_models_dir()}`")
        st.info(_DISCLAIMER)
        for stem, u in use.items():
            for name, path in u["files"].items():
                part = SO.wrapper_part(name)
                if part:
                    _import_part(stem, u, part, installed=path is not None)
                    continue
                if path is not None:
                    continue
                # a user dummy that names the vendor file itself (pre-FS-029)
                ok = _consent_box(stem, ", ".join(u["parts"]))
                up = st.file_uploader(f"Add {name} for {', '.join(u['parts'])} "
                                      f"(the vendor's zip or the model file itself)",
                                      key=f"spice_vendor_up_{stem}", disabled=not ok)
                done = st.session_state.setdefault("spice_vendor_done", set())
                if up is not None and ok and (stem, up.name, up.size) not in done:
                    try:
                        names = SO.install_model(up.name, up.getvalue(), [name])
                        SO.record_consent(names, u["dummy"]["meta"].get("source", ""))
                    except (ValueError, OSError) as e:
                        st.error(f"{up.name}: {e}")
                    else:
                        done.add((stem, up.name, up.size))
                        st.success(f"Installed {', '.join(names)} in your models folder.")
                        st.rerun()
        pending = sorted({nm for u in use.values() for nm, p in u["files"].items()
                          if p is not None and not SO.is_consented(p)})
        if pending and st.button(f"I accept the vendor's terms for {', '.join(pending)}: "
                                 f"bundle them into my own zips", key="spice_vendor_allow"):
            try:
                SO.record_consent(pending)
            except OSError as e:
                st.error(f"Could not record the consent: {e}")
            else:
                st.rerun()


def render_spice_export(sections_data, mc_params, hf_hump=None):
    """Draw the export block. `sections_data` / `mc_params` are the Resulting
    Response tab's own (stage order; every section already has a BOM pick);
    `hf_hump` = the tab's HF-hump finding {db, f_hz, am_sections} or None."""
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

    SO.ensure_user_dirs()
    if not st.session_state.get("_spice_repaired"):   # imports made before localize_model
        st.session_state["_spice_repaired"] = True
        SO.repair_imports()
    lib = SO.dummies()
    stems = (_model_picker(sections_data, lib) if DEBUG_UI else
             {sd["n"]: _part_model(sd["n"]) for sd in sections_data})
    use = _vendor_use(stems, lib)
    generic = False
    if use:
        parts = ", ".join(p for u in use.values() for p in u["parts"])
        generic = st.checkbox(f"Export {parts} with simplified generic models",
                              key="spice_generic_vendor", value=False,
                              help="On: those sections use FS generic with the library's "
                                   "A_ol / GBWP / Ro. Off: the vendor's SPICE model where you "
                                   "have imported it (Vendor model files below); a part whose "
                                   "model is not imported yet uses FS generic anyway.")
        _vendor_panel(use)

    data = [dict(d, opamp_label=opamp_label(d["n"]) or "Ideal", spice_model=stems[d["n"]])
            for d in sections_data]
    try:
        exp = SX.build_export(data, vs=vs, mc_params=mc_params, n_runs=runs,
                              generic_vendor=generic, hf_hump=hf_hump,
                              spec=st.session_state.get("report_spec_short", "filter"),
                              spec_brief=st.session_state.get("report_spec_brief"))
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
    if any(v["bundled"] for v in exp["vendor"].values()):
        st.caption("This zip contains vendor model files — for your own use only; do not "
                   "share it (see its README).")
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
