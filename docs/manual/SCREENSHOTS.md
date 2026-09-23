# Screenshots — what to type, check and capture

Twenty figures, taken by hand in one session, top to bottom. About 40 minutes,
most of it waiting for two solves. Every file goes to `docs\manual\img\` under
the exact name in its heading — the documents refer to them by that name.

`doc_drift.py` reads the **Controls:** line under each heading to tell you which
screenshots a later UI change made stale. Keep those lines when you edit this file.

---

## Before you start (once per session)

1. **The `widget_gain` fix is applied** in `ui_components.py`. Without it a yellow
   warning sits in the sidebar of every shot.
2. **Fresh terminal**, so `FILTERSYNTHESIZER_DEBUG` is not set. Start the app and
   open it in **Chrome or Edge** in a new tab (a new tab is a fresh session):

   ```bat
   python -m streamlit run app.py
   ```

3. **Light theme:** the app's ⋮ menu (top right) → Settings → Light.
4. **Fixed screen geometry: Chrome's device mode.** This is the step that keeps
   every figure at the same scale and sharp in print:
   - `F12` opens DevTools, then `Ctrl+Shift+M` shows the device toolbar.
   - In the toolbar choose **Responsive** and type **1440 × 900**.
   - Toolbar ⋮ (its right end) → **Add device pixel ratio** → set **2.0**.

   The page now renders exactly as on a 1440 × 900 screen at 2× pixels,
   whatever your monitor is.

### How to take a shot

- **A region:** click inside DevTools, `Ctrl+Shift+P`, type `area`, choose
  **Capture area screenshot**, drag a rectangle over the region. Chrome saves a
  PNG to *Downloads* — rename it and move it to `docs\manual\img\`.
- **The whole page:** `Ctrl+Shift+P` → **Capture screenshot**.
- **A region taller than the window** (marked *tall* below): change the height
  in the device toolbar from 900 to **1600** first, then back to 900 after.

### Rules for every shot

- About **10 px of margin** around the region, roughly even on all sides.
- **Nothing moving:** no "Running…" in the corner, no spinner.
- **Nothing floating:** move the mouse off plots (Plotly shows a toolbar and
  tooltips on hover), and click an empty part of the page so no input keeps a
  focus outline.
- **Nothing extra:** no part of a neighbouring section, and never cut through
  text or a box border.

*Without device mode* (fallback): `Win+Shift+S` → Rectangle, paste into Paint and
save as PNG, then in `tools\build_pdf.py` set `CAPTURE_DPR` to your Windows display
scale (1.25, 1.5 …). Text size will vary a little more between figures.

---

## Sidebar and approximation

### 01-first-run.png
- **Do:** nothing — the app as it opens.
- **Check:** plots have appeared on Tab 1 (wait a few seconds after load).
- **Shoot:** the whole page.

Controls: none

### 02a-sidebar-type.png
- **Type:** Response **Butterworth** · Filter Type **Lowpass** · Order **5** ·
  Unit **kHz** · Corner Frequency **2** · Passband Gain **1.5**. Leave the rest.
  Then click an empty spot.
- **Check:** title reads *Filter Synthesizer v1.0.3 - Butterworth Lowpass*; no
  yellow box in the sidebar.
- **Shoot:** from **Filter Configuration** down to the bottom edge of the **Order** box.

Controls: `app.py:Response` `app.py:Filter Type` `widget_sym_order`

### 02b-sidebar-freq.png
- **Shoot:** from **Frequency Specifications** down to the bottom edge of the
  **Passband Gain, V/V** box.

Controls: `ui_components.py:Unit` `widget_fc` `widget_gain`

### 03-magnitude.png
- **Do:** Tab **Response Plots**.
- **Check:** −3 dB at 2 kHz, flat passband.
- **Shoot:** the magnitude plot only.

Controls: none

### 04-roots.png
- **Do:** Tab **Roots & Transfer Function**.
- **Check:** five poles — one real, two complex pairs.
- **Shoot:** the pole table.

Controls: none

## Pairing

### 05-pairing.png
- **Do:** Tab **Biquad Pairing & Cascading** → tick **Enable 3rd-Order Sections
  (Absorb 1st-Order Poles)**.
- **Check:** the cascade shows **two** stages, one of them 3rd order.
- **Shoot:** from the ticked checkbox down to the bottom of the mnemoscheme.

Controls: `app.py:Enable 3rd-Order Sections (Absorb 1st-Order Poles)`

### 06-gain-distribution.png
- **Do:** Remaining Gain Distribution → **Apply Remaining Gain to First Stage**.
- **Shoot:** the radio group together with the **Calculated Remainder** box beside it.

Controls: `app.py:Remaining Gain Distribution`

## Topology — section 1

### 07-topology-top.png
- **Do:** Tab **Topology**.
- **Check:** two sections; Section 1 is order 3 and shows fp, f₀ and Q.
- **Shoot:** the whole page.

Controls: none

### 08-convergence.png
- **Do:** open **Convergence Settings**.
- **Shoot:** the expander. Then close it.

Controls: `hw_effort` `hw_pole_tol_pct` `hw_gain_tol_pct` `hw_topk`

### 09-section-settings.png — *tall*
- **Do:** open **⚙ Section 1 — topology & component settings**. Change nothing.
- **Shoot:** the expander. Then close it.

Controls: `hw_fam_*` `hw_opamp_choice_*` `hw_cmin_*` `hw_cmax_*` `hw_rmin_*` `hw_rmax_*` `hw_ratio_*` `hw_cser_*` `hw_rser_*_*`

### 10-solving.png
- **Do:** Section 1 → **Solve section**. Shoot straight away, while it solves.
- **Shoot:** from the **Section 1 ·** header line down to the **⚙️ Solving…** caption.

Controls: `hw_solve_*`

### 11-bom-table.png
- **Do:** wait until the table appears. **Do not select a row yet.**
- **Shoot:** from **Sort by** down to the bottom of the table.

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
- **Check:** green *Selected #0* box; resistor list with grey ideal values; schematic.
- **Shoot:** from the green **Selected #0** box down to the bottom of the schematic.

Controls: `hw_df_*`

### 13-schematic.png
- **Shoot:** the schematic only.

Controls: none

### Section 2 — no figure
- **Do:** Section 2 → **Solve section** → wait → tick **row 0** in its table.
- **Check:** at the foot of the tab, *Overall filter* shows a realized passband
  gain near **1.5 V/V**, and no "inverted" warning.

## Result and report

### 14-cascade-bode.png
- **Do:** Tab **Resulting Response & Schematic**.
- **Check:** the design and realized curves nearly overlap in the passband.
- **Shoot:** the Bode plot.

Controls: none

### 15-monte-carlo.png — *tall*
- **Do:** **Run Monte-Carlo** (defaults: 2000 runs, seed 0) and wait for it to finish.
- **Shoot:** from **Monte-Carlo tolerances** down to the bottom of the plot, band included.

Controls: `resp_ctol` `resp_runs` `resp_dist` `resp_seed` `resp_envelope` `resp_mc_run`

### 16-report-block.png
- **Do:** scroll to **Generate Report**.
- **Shoot:** from the **Generate Report** heading down to the red button.

Controls: `rep2_cover` `rep2_coeff` `rep2_norm` `rep2_metrics` `rep2_warn` `report_btn`

### 17-report-download.png
- **Do:** **Generate Report** → wait.
- **Shoot:** the **Generate Report** and **⬇ Download PDF** buttons.
- **Also:** download the PDF and keep it — the manual's report chapter is
  written from it.

Controls: `report_dl`

## Failure state

### 20-no-realization.png
- **Do:** Tab **Topology** → open Section 1 settings → **Max R ratio = 2** → close
  → **Solve section** → wait. After the solve, the explanation can take up to
  half a minute more to appear.
- **Check:** a yellow or red box that mentions the *envelope* or a *ratio*.
- **Shoot:** from the **Section 1 ·** header line down to the bottom of that box.

Controls: `hw_ratio_*`

---

## When you're done

Send me the whole `img` folder zipped, the report PDF from figure 17, and
`docs\manual\ui_inventory.json`. The screenshots carry the example's real
numbers — which pole pair absorbed the real pole, f₀/Q/fp per section, the
chosen parts, the realized gain — so they are what the prose is written from.
