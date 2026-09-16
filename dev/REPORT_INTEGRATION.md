# PDF Report — integration notes

Adds a **Generate Report** block at the foot of the *📉 Resulting Response &
Schematic* tab that emits an A4 PDF summary of the whole design.

## Files

| File | Action |
|---|---|
| `report_pdf.py` | **new** — pure PDF builder (no streamlit). `build_report(ctx, opts) -> bytes` |
| `report_ui.py` | **new** — the Streamlit block: checkboxes, gate, context assembly, download |
| `tf_utils.py` | **replace** — adds `poly_to_terms()` / `roots_to_biquad_factors()`; the two existing functions become one-line wrappers, so nothing else changes |
| `response_tab.py` | **replace** — gate calls at the four early-returns + the report block at the end |
| `app.py` | **patch** — two inserts (below) |
| `topology_tab.py` | **patch** — one insert (below) |
| `requirements.txt` | **replace** |
| `build.bat` | **patch** — two extra pip packages |

Nothing in the solver, cell library or plotting path is touched.

---

## Dependencies

```
reportlab      # PDF engine (platypus; mixed portrait/landscape page templates)  REQUIRED
matplotlib     # print-size vector figures + mathtext + the DejaVuSans TTF       REQUIRED
cairosvg       # schematic SVG -> raster, best fidelity                          optional
svglib         # pure-Python vector SVG fallback, no native DLLs                 optional
```

`report_pdf` is imported **lazily** by `report_ui`, so a missing package can
never stop `app.py` from starting: the Generate Report block degrades to a
disabled button naming what to install.

Schematics are drawn by cairosvg when it loads, otherwise by svglib (pure
Python, vector, narrower SVG coverage), otherwise replaced by a one-line note.
**If the Topology tab shows no "⬇ PNG" button next to "⬇ SVG", cairosvg is not
loading on that machine** — it needs the native cairo DLLs, which is the usual
Windows failure. `pip install svglib` is the no-DLL way out.

No kaleido. The report re-plots at the exact physical page size instead of
exporting the interactive Plotly figures, which avoids an ~80 MB bundled
Chromium and gives vector output. DejaVuSans ships inside matplotlib and is
registered with ReportLab at import, so `Ω µ σ ∥ ₀` render (ReportLab's
built-in Helvetica is WinAnsi and has no `Ω`).

`cairosvg` is optional at runtime: if it is missing the section pages print a
one-line note instead of the schematic and the rest of the report is unaffected.

### `build.bat`

```bat
python -m pip install ^
    pyinstaller==6.* ^
    streamlit ^
    numpy scipy sympy plotly pandas ^
    cairosvg svglib reportlab matplotlib
```

### `FilterSynthesizer.spec`

If PyInstaller trips on the matplotlib data files, add:

```python
from PyInstaller.utils.hooks import collect_data_files
datas += collect_data_files("matplotlib")        # mpl-data/fonts/ttf/DejaVuSans.ttf
hiddenimports += ["matplotlib.backends.backend_agg", "reportlab.rl_settings"]
```

`matplotlib.use("Agg")` is set at the top of `report_pdf.py`, so no GUI
backend is ever probed.

---

## Patch 1 — `app.py`

### 1a — spec + engine snapshot

The sidebar spec and `engine_results` are plain locals in `app.py`, so
`response_tab.py` (a separate module) cannot see them. `response_tab.
_find_engine_results()` was already trying to find them by scanning
`session_state` and **always returned `None`** — that helper, plus
`_find_target_gain` and `_overlay_scale`, is dead code and can be deleted.

**Insert immediately BEFORE line 519, `with tab_plots:`**

```python
# ------------------------------------------------------------
# 1B. REPORT SNAPSHOT  (read by report_ui.py / report_pdf.py)
# ------------------------------------------------------------
# Flat, streamlit-free mirror of the sidebar spec and the engine result.
# Nothing else in the app reads these keys, so this block is inert unless a
# report is generated.
def _rep_slots(slots, label):
    if not slots:
        return None
    return (label, "; ".join(f"slot {i} → {v/multiplier:g} {freq_unit}"
                             for i, v in sorted(slots.items())))

def _rep_edge(v, stat):
    if not v:
        return "—"
    txt = f"{v/multiplier:,.4f} {freq_unit}"
    if stat == "degraded":
        return txt + " (extended)"
    if str(stat).startswith("corrupted"):
        return txt + " (does not meet A_s)"
    return txt

_is_band = filter_type in ("Bandpass", "Band-Reject")
_tot_order = final_lp_order + (final_hp_order if _is_band else 0)
_corners = (f"{f1_val:g}…{f2_val:g} {freq_unit}" if _is_band and f2_val
            else f"{f1_val:g} {freq_unit}")

_rep = [("Response", response), ("Filter type", filter_type)]
if _is_band:
    _rep += [("Order (LP / HP)", f"{final_lp_order} / {final_hp_order}"),
             ("Total order", f"{_tot_order}"),
             ("Lower corner f1", f"{f1_val:g} {freq_unit}"),
             ("Upper corner f2", f"{f2_val:g} {freq_unit}" if f2_val else "—")]
else:
    _rep += [("Order", f"{final_lp_order}"),
             ("Corner frequency fc", f"{f1_val:g} {freq_unit}")]
_rep += [("Passband gain", f"{final_gain_units:g} V/V "
                           f"({20*np.log10(final_gain_units):+.2f} dB)"),
         ("Passband ripple α_max" if response in ("Chebyshev", "Elliptic")
          else "Passband attenuation", f"{final_alpha:g} dB")]
if _is_band and final_as_lp != final_as_hp:
    _rep += [("Stopband A_sl (lower)", f"{final_as_hp:g} dB"),
             ("Stopband A_su (upper)", f"{final_as_lp:g} dB")]
else:
    _rep += [("Stopband A_s", f"{final_as_lp:g} dB")]
_rep += [("Passband even-order mod.",
          (("LP " if pb_mod_lp else "") + ("HP " if pb_mod_hp else "")) or "off"),
         ("Stopband roll-off",
          (("LP " if sb_roll_lp else "") + ("HP " if sb_roll_hp else "")) or "off")]
for _lbl, _sl in (("Manual notches", globals().get("active_slots")),
                  ("Manual notches (lower)", globals().get("active_slots_hp")),
                  ("Manual notches (upper)", globals().get("active_slots_lp")),
                  ("Manual notches (band-reject)", globals().get("active_slots_br"))):
    _row = _rep_slots(_sl, _lbl)
    if _row:
        _rep.append(_row)
if not any(k.startswith("Manual notches") for k, *_ in _rep):
    _rep.append(("Manual notches", "none (all free)"))

# Calculated stopband edges -- a 3-tuple marks a FULL-WIDTH row in the report's
# spec table, so the long "Lower = …, Upper = …" string is not squeezed into a
# half-width cell. Mirrors the read-only engine output shown under the plots.
if engine_results:
    if filter_type in ("Lowpass", "Highpass"):
        _rep.append(("Calculated stopband edge f_s",
                     _rep_edge(engine_results.get("f_stop_hz"),
                               engine_results.get("sb_status", "normal")), True))
    elif filter_type == "Bandpass":
        _rep.append(("Calculated stopband edges",
                     "Lower = " + _rep_edge(engine_results.get("f_stop_hp_hz"),
                                            engine_results.get("sb_status_hp", "normal"))
                     + ",  Upper = " + _rep_edge(engine_results.get("f_stop_lp_hz"),
                                                 engine_results.get("sb_status_lp", "normal")),
                     True))
    elif filter_type == "Band-Reject":
        _sbr = engine_results.get("sb_status_hp", "normal")
        _rep.append(("Calculated stopband edges",
                     "Lower = " + _rep_edge(engine_results.get("f_stop_hp_hz"), _sbr)
                     + ",  Upper = " + _rep_edge(engine_results.get("f_stop_lp_hz"), _sbr),
                     True))

st.session_state["report_spec"] = _rep
st.session_state["report_spec_short"] = (
    f"{response.replace(' ', '')}_{filter_type.replace('-', '')}_n{_tot_order}")
st.session_state["report_subtitle"] = (
    f"{response} {filter_type} · order {_tot_order} · {_corners}")

if engine_results:
    _wn = (2 * np.pi * np.sqrt(f1_val * f2_val) * multiplier
           if _is_band and f2_val else 2 * np.pi * real_fc)
    st.session_state["report_engine"] = {
        "poles": np.asarray(engine_results["poles"]),
        "zeros": np.asarray(engine_results["zeros"]),
        "k": float(engine_results["k"]),
        "gain_units": float(final_gain_units),
        "w_norm": float(_wn)}
    # Window (and heading) of the optional detail plot. For a band-reject the
    # interesting band is the notch, so it is a STOPBAND detail, not a passband
    # one; everything else zooms its passband.
    _lo = f1_val * multiplier
    _hi = (f2_val or f1_val) * multiplier
    if filter_type == "Lowpass":
        st.session_state["report_passband"] = (real_fc / 30.0, real_fc * 2.0)
        st.session_state["report_detail_label"] = "Passband detail"
    elif filter_type == "Highpass":
        st.session_state["report_passband"] = (real_fc / 2.0, real_fc * 30.0)
        st.session_state["report_detail_label"] = "Passband detail"
    elif filter_type == "Band-Reject":
        st.session_state["report_passband"] = (_lo / 1.6, _hi * 1.6)
        st.session_state["report_detail_label"] = "Stopband detail"
    else:
        st.session_state["report_passband"] = (_lo / 3.0, _hi * 3.0)
        st.session_state["report_detail_label"] = "Passband detail"

    # TRUE passband edges (Hz) for the report's passband-gain metric -- not the
    # widened detail-plot window above. `None` is an open end, clamped to the
    # plotted grid. A band-reject has two independent passband branches.
    if filter_type == "Lowpass":
        st.session_state["report_passbands"] = [("Passband", None, real_fc)]
    elif filter_type == "Highpass":
        st.session_state["report_passbands"] = [("Passband", real_fc, None)]
    elif filter_type == "Band-Reject":
        st.session_state["report_passbands"] = [("Lower passband", None, _lo),
                                                ("Upper passband", _hi, None)]
    else:
        st.session_state["report_passbands"] = [("Passband", _lo, _hi)]
```

### 1b — per-stage roots in rad/s

`hw_sections` carries f0 / Q / fz / f1 / K, but the report also needs the actual
root locations so it can print each section's transfer function and colour the
pole-zero map by section. Both come straight from the untouched rad/s bricks, so
this is independent of the tab's Hz/rad-s radio.

**Insert immediately AFTER the `st.session_state.hw_gen = hash(...)` statement
(~line 1366), inside `with tab_pairing:`**

```python
            # --- report snapshot: per-stage roots (rad/s) + design gains ---
            _rep_stages = []
            for _i, _stg in enumerate(st.session_state.stage_routing):
                _sp, _sz = [], []
                _pb2 = _brk(_stg['pole_id'], p_bricks)
                if _pb2:
                    _sp.append(_pb2['root'])
                    if _pb2['type'] == 'Complex Pair':
                        _sp.append(np.conj(_pb2['root']))
                _ar2 = _stg.get('absorbed_real_id', 'None')
                if _ar2 != 'None':
                    _ab2 = _brk(_ar2, p_bricks)
                    if _ab2:
                        _sp.append(_ab2['root'])
                for _zid in _stg.get('zero_ids', []):
                    _zb2 = _brk(_zid, z_bricks)
                    if _zb2:
                        _sz.append(_zb2['root'])
                        if _zb2['type'] == 'Complex Pair':
                            _sz.append(np.conj(_zb2['root']))
                _rep_stages.append({
                    **hw_sections[_i], 'poles': _sp, 'zeros': _sz,
                    'peak_mag': (float(peak_mags[_i])
                                 if _i < len(peak_mags) else None)})
            st.session_state['report_pairing'] = {'stages': _rep_stages}
```

---

## Patch 2 — `topology_tab.py`

`discrete_snapper.snap_to_hardware` does `out = dict(sol)` and then overwrites
the resistors in place, so the **continuous (ideal) values only survive in the
paired `continuous` list**. `_render_section` already resolves that twin as
`c_row` but never persists it, so the Response tab cannot show it. Three
one-line additions fix that.

**2a** — after `store = st.session_state.setdefault("bom_picks", {})` (~L1153):

```python
    cstore = st.session_state.setdefault("bom_picks_cont", {})   # pre-snap twin
```

**2b** — after `store[n] = _PICKS[n] = s_row              # durable copies` (~L1157):

```python
        cstore[n] = c_row                         # ideal (continuous) values
```

**2c** — in the restore branch, after:

```python
        except ValueError:
            c_row = {}
```
add
```python
        cstore[n] = c_row
```

**2d** (optional, keeps the store from going stale) — next to the existing purge
at ~L1969:

```python
        st.session_state.bom_picks_cont = {
            k: v for k, v in st.session_state.get("bom_picks_cont", {}).items()
            if k in valid}
```

---

## Report contents

**Always included**

| Page | Content |
|---|---|
| 1 | §1 specification (incl. a full-width *calculated stopband edges* row) · §2 whole-filter H(s), denormalized, rad/s, isolated gain constant · §2a/§2b coefficient tables. These flow as one block: anything that does not fit spills onto a continuation page. |
| 2 | §3 pole-zero map with pairing links and per-section colours — **always starts a new page** · §4 root locations, rad/s, with the owning section per root · §4a normalized roots. Again one flowing block. |
| per section | §5.n design parameters (f₀, ω₀, Q, f_z, f_p, Kᵢ, peak \|H\|) → schematic → BOM (snapped black, ideal grey in brackets) + op-amp → **Solver constraints** (R/C series and ranges, its own block) → quality metrics → section H(s), rad/s |
| landscape | §6 Bode, design vs realized, log scale. Phase and group delay overlay the same axes and read off their own right-hand scales (phase on the inner spine, GD on a second spine offset outboard). Monte-Carlo caption and the **passband-gain** table below; the plot is sized around that block so the page never splits. |
| landscape | §7 detail plot over the band of interest — titled **Stopband detail** for a band-reject, **Passband detail** otherwise. |

**Optional (checkboxes)**

Cover page (title/project/author) · polynomial coefficient table · normalized
prototype roots · per-section quality metrics (sens, snap cost, realized vs
target gain) · Monte-Carlo band + parameters + DC-spread stat · phase &
group-delay panels on the Bode page · passband detail plot (with the same MC
shading) · design warnings carried over.

### Gate

Generation is allowed only when every realizable section has a solved and
**selected** BOM. `response_tab.render_response_tab` returns early in exactly
those cases, so it now calls `report_ui.render_blocked(reason)` from each of
its four early-return points: the heading is always present and always explains
what is missing, instead of silently disappearing.

### Line breaking of long transfer functions

`tf_utils.poly_to_terms()` is now the term-level source of truth (
`poly_to_latex` joins it with a space). The report measures each term with
matplotlib's mathtext engine and packs terms greedily into lines, breaking only
at `+`/`−` boundaries and repeating the operator on the continuation line.
A term is never split, so nothing is ever clipped; if the block still overflows
the frame the font steps down (9 → 5.5 pt) before anything is dropped.
`roots_to_biquad_factors()` does the same for the factored form, which is what
puts a 3rd-order section's real pole in its own bracket.

A 10th-order elliptic band-reject (11-term denominator, ~400 characters) wraps
onto four lines and fits the lower half of page 1.

### Monte-Carlo

The report's MC page uses `st.session_state["resp_mc"]` when it is fresh. If the
band has not been run, or its inputs changed, pressing **Generate Report** runs
it once with the current settings (deterministic — the seed is a control). The
caption prints exactly the requested form, plus the seed and the fact that
op-amp parameters are held fixed during MC:

> `Res. 0…1MΩ 2%; >1MΩ 5%; cap. 5%; 2000 runs, gaussian 3σ, min–max; seed 0. Op-amp parameters held fixed.`

---

## Known limits

* **"Half a page" is a target, not a hard frame.** A 10th-order band-reject has
  20+20 roots; §4/§4a spill onto a continuation page and the TF block grows
  downward. Content is never clipped, and §3 always begins a fresh page.
* **Schematic fidelity depends on the SVG backend.** cairosvg is preferred;
  svglib is the no-DLL fallback but ignores `<foreignObject>` and much CSS, so
  drawio labels emitted that way can drop. The injected annotation layer is
  plain `<text>` and survives either backend. The **⬇ PNG** button in the
  Topology tab is a live cairosvg indicator: no button means no cairosvg.
* **The three `app.py` snapshots are mandatory.** Without patch 1a/1b the
  Generate Report block refuses to build and names which snapshot is missing,
  rather than emitting a blank PDF.
* **Gain error reference.** With a single section, the section carries the
  whole filter gain, so the error is referenced to the passband gain the user
  specified in the sidebar and the cell is labelled *Specified passband gain*.
  With a cascade the section carries only its share, and comparing a 3.16 V/V
  stage against a 10 V/V filter target would print roughly −68 %, so the
  reference falls back to that section's own design target and the label
  changes to *Section design target*. A caption under the table states which
  reference is in force. The error is signed: negative means the built section
  falls short. "Realized" means snapped component values at zero tolerance —
  component spread lives in §6.
* **Passband gain, not DC gain.** The Monte-Carlo summary reports the top of
  the passband ripple — the highest magnitude inside each passband — because an
  even-order Chebyshev or elliptic without the even-order modification has
  H(0) (or H(∞)) a full ripple below its peaks. Each run is peak-searched
  independently with `argmax` over the band, so a peak that migrates between
  reflection zeros under tolerance is still counted; the spread is the highest
  per-run peak minus the lowest, and the table also prints the frequency range
  the winning peaks landed in. A band-reject gets one row per passband branch.
* Section transfer functions are the **design** targets, not the realized
  responses. The realized-vs-design difference is exactly what the Bode page
  shows.
* Report generation takes ~2–4 s plus ~1 s per section page (cairosvg), plus the
  MC run if it has to be triggered. The PDF is cached in
  `st.session_state["report_pdf"]` so the download button does not rebuild it.
