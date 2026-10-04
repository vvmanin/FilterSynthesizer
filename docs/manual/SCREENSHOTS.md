# Screenshots — what to type, check and capture

Figures for **v1.1.0**, taken by hand. Every file goes to `docs\manual\img\`
under the exact name in its heading — the documents refer to them by that name.

- **Part A — Quick Start** (20 figures, one worked example, top to bottom).
  The pipeline is the v1.0.3 one; every figure is retaken because the app's
  CSS changed (blue/amber design-control boxes, compact tabs) and the app now
  has **four** tabs. About 40 minutes, most of it waiting for two solves.
- **Part B — User Manual only** (new features since v1.0.3). Independent
  set-ups; do them in any order after Part A. About 45 minutes.

`doc_drift.py` reads the **Controls:** line under each heading to tell you which
screenshots a later UI change made stale. Keep those lines when you edit this file.

---

## Before you start (once per session)

1. **Version is 1.1.0.** `_version.py` → `__version__ = "1.1.0"` *before*
   shooting: the title line on every full-page figure records the version.
2. **Fresh terminal**, so `FILTERSYNTHESIZER_DEBUG` is not set (it adds a
   solver-call expander and a debug SPICE-model picker). Start the app and open
   it in **Chrome or Edge** in a new tab (a new tab is a fresh session):

   ```bat
   python -m streamlit run app.py
   ```

3. **Light theme:** the app's ⋮ menu (top right) → Settings → Light.
4. **Fixed screen geometry: Chrome's device mode.** This keeps every figure at
   the same scale and sharp in print:
   - `F12` opens DevTools, then `Ctrl+Shift+M` shows the device toolbar.
   - In the toolbar choose **Responsive** and type **1440 × 900**.
   - Toolbar ⋮ (its right end) → **Add device pixel ratio** → set **2.0**.
5. **Op-amp library at shipped state:** no user parts saved. If
   `%LOCALAPPDATA%\FilterSynthesizer\opamp_library_user.json` exists, rename it
   for the session (it adds parts to every op-amp list).
6. **For B9 only:** have one vendor model zip at hand (e.g. OPA1656 from the TI
   product page). Download it yourself in the normal browser.

### How to take a shot

- **A region:** click inside DevTools, `Ctrl+Shift+P`, type `area`, choose
  **Capture area screenshot**, drag a rectangle over the region. Chrome saves a
  PNG to *Downloads* — rename it and move it to `docs\manual\img\`.
- **The whole page:** `Ctrl+Shift+P` → **Capture screenshot**.
- **A region taller than the window** (marked *tall* below): change the height
  in the device toolbar from 900 to **1600** first, then back to 900 after.
- **A popover** (B7): Chrome's area capture closes it. Use `Win+Shift+S` for
  that one figure and set nothing else differently.

### Rules for every shot

- About **10 px of margin** around the region, roughly even on all sides.
- **Nothing moving:** no "Running…" in the corner, no spinner.
- **Nothing floating:** move the mouse off plots (Plotly shows a toolbar and
  tooltips on hover), and click an empty part of the page so no input keeps a
  focus outline.
- **Nothing extra:** no part of a neighbouring section, and never cut through
  text or a box border. Include a blue/amber box's **whole border** when the
  region is inside one.

*Without device mode* (fallback): `Win+Shift+S` → Rectangle, paste into Paint and
save as PNG, then in `tools\build_pdf.py` set `CAPTURE_DPR` to your Windows display
scale (1.25, 1.5 …). Text size will vary a little more between figures.

---

# Part A — Quick Start (worked example)

5th-order Butterworth low-pass, 2 kHz, passband gain 1.5 V/V. The screenshots
carry the run's real numbers, so after this part send them before the Quick
Start prose is rewritten.

## Sidebar and approximation

### 01-first-run.png
- **Do:** nothing — the app as it opens.
- **Check:** plots have appeared on **Response Plots** (wait a few seconds);
  four tabs across the top.
- **Shoot:** the whole page.

Controls: none

### 02a-sidebar-type.png
- **Type:** Response **Butterworth** · Filter Type **Lowpass** · Order **5** ·
  Unit **kHz** · Corner Frequency **2** · Passband Gain **1.5**. Leave the rest.
  Then click an empty spot.
- **Check:** title reads *Filter Synthesizer v1.1.0 - Butterworth Lowpass*; no
  yellow box in the sidebar. The Response list now has seven entries
  (… Bessel, Equiripple Delay, Custom H(s)) — all must be visible.
- **Shoot:** from **Filter Configuration** down to the bottom edge of the **Order** box.

Controls: `app.py:Response` `widget_filter_type_delay | widget_filter_type` `widget_sym_order`

### 02b-sidebar-freq.png
- **Shoot:** from **Frequency Specifications** down to the bottom edge of the
  **Passband Gain, V/V** box.

Controls: `ui_components.py:Unit` `widget_fc` `widget_gain`

### 03-magnitude.png
- **Do:** Tab **Response Plots**. Leave **Phase** and **Group Delay** unticked.
- **Check:** −3 dB at 2 kHz, flat passband, a single curve.
- **Shoot:** the magnitude plot only.

Controls: none

### 04a-pole-zero-map.png
- **Do:** still on **Response Plots**, scroll to the grey **Roots & Transfer
  Function** frame at the end of the tab → open the **Pole-Zero Map** expander.
- **Check:** five poles on the unit circle.
- **Shoot:** the expander, header included. Then close it.

Controls: `app.py:Stretch Real Axis` `app.py:Magnification`

### 04b-roots.png
- **Do:** same frame → open **Root Locations**.
- **Check:** five poles — one real, two complex pairs.
- **Shoot:** the expander, header included. Then close it.

Controls: `scale_roots`

## Pairing

### 05-pairing.png
- **Do:** Tab **Biquad Pairing & Cascading** → tick **Enable 3rd-Order Sections
  (Absorb 1st-Order Poles)**.
- **Check:** the cascade shows **two** stages, one of them 3rd order.
- **Shoot:** the blue box from its top border (the ticked checkbox) down to the
  bottom of the mnemoscheme.

Controls: `app.py:Enable 3rd-Order Sections (Absorb 1st-Order Poles)` `app.py:🔄 Auto-Pair (Reset)`

### 06-gain-distribution.png
- **Do:** Remaining Gain Distribution → **Apply Remaining Gain to First Stage**.
- **Shoot:** the blue box holding the radio group and the **Calculated
  Remainder** box beside it.

Controls: `app.py:Remaining Gain Distribution`

## Topology — section 1

### 07-topology-top.png
- **Do:** Tab **Topology**. **Batch mode** stays off.
- **Check:** two sections; Section 1 is order 3 and shows fp, f₀ and Q.
- **Shoot:** the whole page.

Controls: `hw_batch` `hw_solve_all`

### 08-convergence.png
- **Do:** open **Convergence Settings**.
- **Shoot:** the blue box with the open expander. Then close it.

Controls: `hw_effort` `hw_pole_tol_pct` `hw_gain_tol_pct` `hw_topk`

### 09-section-settings.png — *tall*
- **Do:** open **⚙ Section 1 — topology & component settings**. Change nothing.
- **Check:** Op-amp **Ideal**, with the **✎ Edit** button beside it.
- **Shoot:** the expander. Then close it.

Controls: `hw_fam_*` `hw_opamp_choice_*` `hw_cmin_*` `hw_cmax_*` `hw_rmin_*` `hw_rmax_*` `hw_ratio_*` `hw_cser_*` `hw_rser_*_*`

### 10-solving.png
- **Do:** Section 1 → **Solve section**. Shoot straight away, while it solves.
- **Shoot:** from the **Section 1 ·** header line down to the **⚙️ Solving…** caption.

Controls: `hw_solve_*`

### 11-bom-table.png
- **Do:** wait until the table appears. **Do not select a row yet.**
- **Shoot:** the amber box, from **Sort by** down to the bottom of the table.

Controls: `hw_sortf_*` `hw_sortd_*` `hw_df_*`

### 22-report-blocked.png
Taken now because this is the only moment the report is blocked — once a row
is picked, the pick stays.
- **Do:** Tab **Resulting Response & Schematic**.
- **Check:** the warning *Report generation is not available yet…*
- **Shoot:** from the blue **Pick a BOM…** box down to the greyed-out **Generate Report** button.

Controls: `report_btn_blocked`

### 12-bom-picked.png — *tall*
- **Do:** Tab **Topology** → in Section 1's table, tick the checkbox at the far
  left of **row 0**.
- **Check:** green *Selected #0* box; resistor list with grey ideal values;
  the capacitor list headed **Capacitors**; schematic.
- **Shoot:** from the green **Selected #0** box down to the bottom of the schematic.

Controls: `hw_df_*`

### 13-schematic.png
- **Shoot:** the schematic only, with the **⬇ SVG** (and **⬇ PNG**, if shown) button under it.

Controls: `*_svg_*` `*_png_*`

### Section 2 — no figure
- **Do:** Section 2 → **Solve section** → wait → tick **row 0** in its table.
- **Check:** at the foot of the tab, *Overall filter* shows a realized passband
  gain near **1.5 V/V**, and no "inverted" warning. Note the exact value and
  section 2's parts — the prose quotes them.

## Result and report

### 14-cascade-bode.png
- **Do:** Tab **Resulting Response & Schematic**.
- **Check:** the design and realized curves nearly overlap in the passband.
- **Shoot:** the **Bode — design vs realized** plot with its checkbox row.

Controls: `resp_show_linear` `resp_show_phase` `resp_show_gd`

### 15-monte-carlo.png — *tall*
- **Do:** resistor **tol %** 2, **Capacitor tol (%)** 5, **Distribution**
  Uniform (±tol), **Envelope** min–max, Runs 2000, Seed 0 → **Run Monte-Carlo**
  and wait for it to finish.
- **Shoot:** the blue box from **Monte-Carlo tolerances** down to the bottom of
  the plot, band included. Note the DC-gain spread in the caption.

Controls: `resp_ctol` `resp_runs` `resp_dist` `resp_seed` `resp_envelope` `resp_mc_run`

### 15b-monte-carlo-linear-scale.png
- **Do:** tick **Linear Mag.** above the plot.
- **Shoot:** from **Bode — design vs realized** down to the bottom of the plot.
  Untick it afterwards.

Controls: `resp_show_linear`

### 16-report-block.png — *tall*
- **Do:** scroll to **Generate Report**. Leave every option at its default.
- **Shoot:** from the **Generate Report** heading down to the red button
  (title / project / author fields included).

Controls: `rep2_cover` `rep2_coeff` `rep2_norm` `rep2_metrics` `rep2_warn` `rep2_phgd` `rep2_mc` `rep2_zoom` `rep2_title` `rep2_project` `rep2_author` `report_btn`

### 17-report-download.png
- **Do:** **Generate Report** → wait.
- **Shoot:** the **Generate Report** and **⬇ Download PDF** buttons.
- **Also:** download the PDF and keep it — the manual's report chapter is
  written from it.

Controls: `report_dl`

### 60-ltspice-export.png — *tall*
Used by the User Manual; the Quick Start may reuse it in its last chapter.
- **Do:** same tab, the **LTspice export** block just above Generate Report.
  Leave Supply Vs and MC runs at their defaults.
- **Check:** the per-section table lists both sections with op-amp *Ideal* and
  SPICE model *FS generic*.
- **Shoot:** from the **LTspice export** heading down to the
  **⬇ Download LTspice files (.zip)** button.
- **Also:** download the zip and keep it (README text for the manual).

Controls: `spice_vs` `spice_mc_runs` `spice_dl`

## Failure state

### 20-no-realization.png
- **Do:** Tab **Topology** → open Section 1 settings → **Max R ratio = 2** → close
  → **Solve section** → wait. After the solve, the explanation can take up to
  half a minute more to appear. Afterwards set **Max R ratio** back to its default.
- **Check:** a yellow or red box that mentions the *envelope* or a *ratio*.
- **Shoot:** from the **Section 1 ·** header line down to the bottom of that box.

Controls: `hw_ratio_*`

---

# Part B — User Manual only (features since v1.0.3)

Each block starts from its own set-up. Where it says *reload*, press `F5` for a
fresh session.

## Response Plots

### 33-phase-gd-overlay.png
- **Set-up:** the Part A example (Butterworth LP 5, 2 kHz, gain 1.5).
- **Do:** Response Plots → tick **Phase** and **Group Delay**.
- **Check:** dashed phase and dotted group delay on the magnitude plot, each on
  its own right-hand axis, with a legend.
- **Shoot:** the plot with the checkbox row above it.

Controls: `app.py:Phase` `app.py:Group Delay`

### 34-probes.png
- **Do:** same design; type 1, 2 and 5 (kHz) into **Probe 1–3**.
- **Shoot:** the Frequency Probes row with the Gain/Phase readouts beside each input.

Controls: `probe_*_*`

### 35-tf-hs.png
- **Do:** Roots & Transfer Function frame → open **Transfer Function H(s)** →
  **Form** = Factored (Cascaded Biquads).
- **Shoot:** the whole grey frame, Domain Scale on top, this expander open.

Controls: `scale_roots` `unit_roots` `tf_form_roots`

## Bessel and Equiripple Delay (FS-006, FS-021)

### 30a-delay-sidebar-top.png
- **Set-up:** reload. Response **Bessel** · Filter Type **Lowpass** · Unit **kHz** ·
  Order selection **From specs** · Specify by **Group delay** · τ₀ **0.5** (ms) ·
  Order criterion **Flat delay up to f_d**, f_d **1**, ε **1**.
- **Check:** the green order result box (**n = …**) and the derived corner
  under τ₀.
- **Shoot:** the sidebar from **Filter Configuration** down to the bottom edge
  of the derived-corner caption under τ₀. Printed as a row with 30b.

Controls: `app.py:Response` `widget_delay_order_mode` `widget_delay_anchor` `widget_tau0`

### 30b-delay-sidebar-specs.png
- **Shoot:** from **Passband & Stopband Specs** down to the end of the **Delay
  Specs** block.

Controls: `widget_crit_*` `widget_crit_fd` `widget_crit_eps`

### 31-group-delay-detail.png
- **Do:** same design → Response Plots → **Group Delay Detail**.
- **Check:** metrics row + zoomed τ(f) with the tolerance band.
- **Shoot:** heading, metrics row and plot.

Controls: none

### 32-ems-notches.png — *tall*
- **Set-up:** Response **Equiripple Delay** · Lowpass · Order selection
  **Manual**, Order **7** · Specify by **Corner frequency** 1 kHz ·
  Delay ripple ±δ default · Stopband A_s **40**.
- **Do:** Response Plots → Manual Notch Placement → tick **Equiripple Magnitude
  Stopband**.
- **Check:** rows with **Active** checkboxes and read-only solved frequencies;
  the readout (notches, humps at −A_s, f_s, pole scale, τ(0) before → after).
- **Shoot:** the blue Manual Notch box, checkbox to readout.

Controls: `widget_ems` `ems_active_*` `widget_delay_ripple`

### 32a-spec-top-ems-notches.png
- **Shoot:** the sidebar of the same design, from **Filter Configuration** to
  the **Order** box. Printed as a row with 32b.

Controls: `widget_delay_order_mode`

### 32b-spec-bot-ems-notches.png
- **Shoot:** the rest of the sidebar, from **Frequency Specifications** to the
  end of **Delay Specs**.

Controls: `widget_delay_anchor`

## Custom H(s) (FS-007, FS-035)

### 40-custom-sidebar.png — *tall*
- **Set-up:** reload. Response **Custom H(s)** (Mode stays *Lowpass prototype*).
- **Shoot:** the sidebar from **Filter Configuration** to its bottom.

Controls: `widget_custom_mode` `widget_custom_gain_mode` `widget_custom_as`

### 41-custom-panel.png — *tall*
- **Do:** Response Plots → the blue **Custom Transfer Function H(s)** panel,
  as it opens: Input form **Factored (f₀, Q)**, the Butterworth n = 4 example.
- **Shoot:** the whole panel, form radio to the diagnostics, ＋ / − buttons visible.

Controls: `custom_form` `custom_tbl_*_*` `custom_add_*` `custom_del_*` `custom_reset`

### 42-custom-roots.png
- **Do:** Input form **Roots (σ, ω)**; leave Gain *As entered* in the sidebar.
  Open **Paste roots**.
- **Check:** the **Leading constant** K / C radio is enabled.
- **Shoot:** form radio, the tables, Leading constant + value field and the
  open Paste roots expander.

Controls: `custom_paste_poles` `custom_paste_zeros` `custom_paste_apply_roots`

### 43a-custom-complete-sidebar.png
- **Do:** sidebar Mode **Complete H(s)**; Input form **Coefficients**.
- **Shoot:** the sidebar from **Custom Transfer Function** to **Gain** (shows
  *Filter Type: … (detected from H(s))* and **Scale**). Printed as a row with 43b.

Controls: `widget_custom_mode` `widget_custom_scale_ts`

### 43b-custom-paste.png
- **Do:** Response Plots → open **Paste coefficients**.
- **Shoot:** the open expander.

Controls: `custom_paste_num` `custom_paste_den` `custom_paste_order` `custom_paste_apply`

## Topology tab (FS-005, FS-028, FS-033)

### 50-batch-mode.png — *tall*
- **Set-up:** the Part A example, pairing as in Part A (two sections).
- **Do:** Topology → toggle **Batch mode — shared envelope & op-amp** on.
- **Check:** **⚙ Shared settings — all sections (Batch mode)** opens; each
  section's own settings expander reads *topology settings* only.
- **Shoot:** from the Batch mode toggle down to the **Solve all sections**
  button, Shared settings expander open.

Controls: `hw_batch` `hw_solve_all`

### 51-solve-all.png
- **Do:** **Solve all sections**; shoot while both sections show *Solving…*.
- **Shoot:** from the toggle down to Section 2's *Solving…* caption.
  Afterwards turn Batch mode off.

Controls: `hw_solve_all`

### 52-opamp-edit.png  *(popover — use `Win+Shift+S`)*
- **Do:** Section 1 settings → Op-amp **OPA1656** → **✎ Edit**.
- **Check:** Name, A_ol, GBWP, Ro, Description, SPICE model, **Save**, and the
  Built-in / User file paths at the bottom. (*Revert to shipped values* appears
  only after a built-in part is edited and saved; *Delete part* only for a
  user part — not needed in the figure.)
- **Shoot:** the open popover with the Op-amp box beside it.

Controls: `hw_opamp_choice_*` `hw_oped_name_*_*` `hw_oped_aol_*_*` `hw_oped_gbwp_*_*` `hw_oped_ro_*_*` `hw_oped_desc_*_*` `hw_oped_spice_*_*` `hw_oped_save_*`

### 53-opamp-custom.png
- **Do:** Op-amp **Custom**.
- **Shoot:** the Op-amp box down to **Save to library** (A_ol, GBWP, Ro,
  Save as part). Set Op-amp back to **Ideal** afterwards.

Controls: `hw_aol_*` `hw_gbwp_*` `hw_ro_*` `hw_opnew_name_*` `hw_opnew_save_*`

### 54-first-order-section.png
- **Set-up:** Part A example with **Enable 3rd-Order Sections** *unticked*
  (three sections, one 1st order).
- **Do:** Topology → open the 1st-order section's **component settings**.
- **Shoot:** the expander and the section's gain/Solve row.

Controls: `hw_fo_real_*` `hw_dc_chk_*`

### 56-near-notch.png
- **Set-up:** Response **Elliptic** · **Bandpass** · Order (LP & HP) **9** ·
  kHz · corners **1.9** and **2.2** · Passband Gain 1.5 · α_max 1 dB · A_s 40.
  Section 4 (f₀ = 1910 Hz, f_z = 1884 Hz) shows *ℹ Near-notch section: f_z is
  1.4 % …*.
- **Do:** Section 4 settings → family **AM** → **Solve section**.
- **Shoot:** from the section header line, settings expander open, down to the
  first rows of its BOM table (sorted by *Snap cost*).

Controls: none

## Resulting Response — LTspice (FS-008, FS-029)

### 61-vendor-models.png — *tall*
- **Set-up:** Part A example; Section 1 op-amp **OPA1656**, re-solve and pick
  row 0 (any op-amp with a vendor model works).
- **Do:** Resulting Response → LTspice export → open **Vendor model files**.
- **Check:** status table with the product-page link, the disclaimer, the
  consent tick and the file picker; the **Export … with simplified generic
  models** checkbox above.
- **Shoot:** the expander, checkbox included.

Controls: `spice_generic_vendor` `spice_vendor_consent_*` `spice_vendor_up_*`

### 62-vendor-import.png
- **Do:** tick the consent box → choose the downloaded zip in the file picker.
- **Check:** **Model subcircuit** and the pin-role boxes are filled.
- **Shoot:** from the file picker down to **Import the … model**. Then import
  it, so the status table reads *imported* (optional re-shoot of 61).

Controls: `spice_vendor_sub_*` `spice_vendor_role_*_*_*` `spice_vendor_inst_*`

### 61b-vendor-models-imported.png
- **Do:** after the import, close **Vendor model files**.
- **Shoot:** the whole **LTspice export** block: both sections on the OPA1656
  model, and the captions about real models and vendor files.
- **Do not** share or commit the zip this state downloads — it contains TI's
  model file. The Part A zip (figure 60, Ideal op-amps) is the one to keep.

Controls: none

---

## When you're done

Send me the whole `img` folder zipped, the report PDF from figure 17 and the
LTspice zip from figure 60 (Ideal op-amps — not one from Part B).
The screenshots carry the example's real numbers — which
pole pair absorbed the real pole, f₀/Q/fp per section, the chosen parts, the
realized gain, the Monte-Carlo spread — so they are what the prose is written
from.

Retired in v1.1.0: `04-roots.png` (replaced by `04b-roots.png`).
