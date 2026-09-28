# FS-008 — LTspice export (AC + Monte Carlo) — design note

Purpose: the reasoning and the build plan for FS-008. The tool writes LTspice schematics of the
whole solved cascade: sections in series, op-amps taken from a local library, component values
from the snapped BOM, and Monte Carlo pre-set from the tool's MC settings. The files open and
simulate directly. This note is for a coding session with the project open. It records the
maintainer's decisions (2026-09-27/28, second round 2026-09-28), the caveats of generating LTspice schematics, how each
caveat is handled, and what has to be checked in LTspice itself.

No code was written for this note. LTspice was not available where it was planned, so every
LTspice behaviour below that is not certain is marked **[verify]** and listed again in §15.2.
Phase 1b (§14) runs those checks before any output is trusted.

Conventions:
- A *row* is the snapped Solution dict of one section, `st.session_state.bom_picks[n]`. It holds
  R in MΩ and C in µF (`tf_symbols.py:33-35`).
- The *IR* is the netlist intermediate representation introduced here (§2).
- A *template* is a hand-drawn LTspice `.asc` file read by the exporter (§3).
- A *dummy* is a template that holds exactly one op-amp (§5).
- *Superset* means one family's full nodal model. Its cells are gated subsets of it.
- Line numbers refer to HEAD `ffc8067`. Re-grep them before editing.

---

## 0. Decisions and answers (summary)

| Question | Answer (section) |
|---|---|
| Direction | The tool **exports** and LTspice opens the files (answers the ROADMAP open question). |
| Netlist or schematic? | **Schematic `.asc`** for the user. Internally a **netlist IR** is the single source of truth. It is checked numerically against every cell's transfer function, and it drives the drawing, the self-check and the fallback (§2). |
| Where does the drawing come from? | **Hybrid** (*maintainer decision*). One **superset template per cell family**, hand-drawn in LTspice. Each variant is derived from it by gating. An exact-variant template may override the superset. Cells without a template get an **auto-layout** from the IR, so every cell exports from day one (§3, §4). |
| Optional parts and parts replaced by a short | Must be **reliable** (*maintainer requirement*). The IR marks each absent part `open` or `short` per variant. The transform deletes open parts and replaces shorted parts by a wire. The exported drawing's connectivity is re-extracted and must equal the IR; otherwise that section falls back to auto-layout. A wrong schematic is never written (§4). |
| File scope | **One file holds the whole cascade in series**, so inter-stage loading is simulated. Sections are stacked **in a column**: section 1 at the top, the last one at the bottom. Sections connect **by net labels** (*maintainer decision*, §4.5). |
| Op-amps | **A local op-amp library of dummy `.asc` files**, one op-amp each. The op-amp is either an LTspice built-in part, a part with an external `.lib`, or a model in a directive text block. The export **copies each section's op-amp instance and its directives from the dummy**. The user can swap op-amps in the output (*maintainer decision*). UniversalOpamp is not offered, because it adds nothing over the tool's own model (*maintainer*). Recommendation: an **FS generic** dummy with the tool's A_ol/GBWP/Ro model, used for Ideal, Custom and unmapped parts and for validation (§5). |
| Op-amp placement | Every op-amp position in a cell is a **seat** with fixed terminal coordinates. Each dummy carries adapter wiring from its symbol's pins to those terminals, so any op-amp copied from its dummy fits every seat. Each instance is its own copy, with no shared labels, and directives are written once per file (*maintainer decision*, §5.4). A hierarchical block with one unified symbol is kept as the documented alternative. |
| Which dummies ship | The **maintainer's call**. Appendix A gives the guidelines, the dummy check and a generator for `opamp2`-based dummies. |
| Mapping from a tool part to a dummy | The `opamp_library` field `spice_model` holds the dummy's file stem. It is null today, and then FS generic is used (§5.2). |
| Supply | The **total supply Vs is entered in the tool**. It is drawn as **two sources of Vs/2**, positive and negative, with GND at the common node. The **default is 5 V** (±2.5 V) (*maintainer decisions*, §6). |
| Inter-stage loading | **Real cascade only** (*maintainer decision*). The tool's realized curve ignores loading. The difference is explained and validated against an MNA solve of the loaded cascade (§10). A loaded realized response *inside the tool* becomes the follow-up item **FS-027**, with a feasibility check first (*maintainer*, §16). |
| One file or several? | **Several.** In v1 these are `<spec>_AC.asc` (nominal) and `<spec>_AC_MC.asc` (Monte Carlo), zipped with a README. There are two reasons. LTspice runs one analysis type per simulation. And `mc()` / `gauss()` never return the nominal value, even with one run or without `.step` (*maintainer's reasoning, confirmed*, §7). MC run 1 is **not** used as a nominal reference; nominal lives only in the nominal file (*maintainer*). |
| Transient | **Not in v1.** A follow-up item, **FS-026**, comes after FS-024 (the tool's own time-domain evaluation), which supplies the window and presets. It uses a step (mandatory) and an impulse derived from the small-signal step. There is no Monte Carlo in the time domain (*maintainer*). v1 already builds the hooks (§11). |
| MC mapping | Gaussian `nom*(1+gauss(tol/3))` or Uniform `nom*(1+flat(tol))`, through one `.func`. There is one `.param` per resistor band plus one for caps, and `.step param run 1 N 1` with `.save V(out)`. Band membership comes from `hw_plots._r_tol_frac` itself (§9). |
| Designators | `R201` = section 2, R1. Split caps are `C202A`/`C202B`, op-amps `U201…U203`, and AM/BP3 aliases R0/C0 become `R200`/`C200` (*maintainer decision*). |
| LTspice version | **LTspice 24.x only** (*maintainer decision*). XVII is not a target. |
| Where in the UI | Resulting Response tab, in a block after the Monte Carlo settings: Vs, the op-amp→SPICE model table and a zip download (§13). |

---

## 1. What FS-008 is — and is not

FS-008 is **Tier D post-processing**. It reads what the Resulting Response tab already assembles:
- `sections_data`, which holds the row, the section spec `sec` and `eval_opamp` per section (`response_tab.py:390`);
- the MC settings mirror `mc_params` (`response_tab.py:482`);
- the op-amp choice per section, `hw_opamp_choice_{n}` (`_eval_opamp`, `response_tab.py:133`).

It then writes text files. It does not change the engine, pairing, cells, solvers, scoring, the
TF cache or `verify.py`. Tier B is read only: `tf_derivation_v2.topo_for_name` (L160),
`derive_nonideal` (L125), `make_response_func` (L266) and `cells_first_order.parse_name` (L51),
all called from the dev check and from the IR's topo lookup.

v1 is AC nominal and AC Monte Carlo. Transient is FS-026 (§11), noise is FS-009, and QSpice is
FS-010 (§16).

---

## 2. Architecture: one netlist IR, several consumers

### 2.1 Why an IR

The code has **no machine-readable netlist**. Each `cells_*.py` writes hand-coded sympy KCL, one
equation per node, over a family superset. Absent parts are removed by `_gates()`, which sets
their conductance to 0 (`cells_lp.py:88-99`). Netlists appear only as prose in docstrings
(`cells_am_core.py:125-127`, `cells_mfb.py:307-309`).

Every consumer in this feature needs connectivity:
- the template transform (what to delete or short);
- the export-time self-check;
- the auto-layout fallback;
- the validation MNA;
- later, the `.cir` netlist (FS-010) and noise sources (FS-009).

One table per superset serves all of them.

### 2.2 Content

A section IR is:

```text
Section IR  = { stage, topology, nodes: [in, out, internal…],
                parts: [ {key: "R2", kind: R|C, n1, n2, value_ohm|value_f} … ],
                opamps: [ {slot: "U1", inp, inn, out} … ] }
Superset    = { parts: [ {key, kind, n1, n2, present(topo), absent_as: open|short} … ],
                opamps: [ {slot, inp, inn, out} ], in_node, out_node(topo) }
```

The following conventions apply:
- Node names are the IR node names (`a, b, c, m, out` for Sallen-Key; `m1, p2, out1…out3, m3`
  for AM). The section input is `in`.
- A `short` merges n1 and n2 in the variant (union-find). An `open` drops the part.
- Split caps expand after gating. When the row has `C2_parallel` (or both `C2a` and `C2b`), `C2`
  becomes two parts `C2a` and `C2b` on the same nodes. `C1a/C1b` works the same way (AM `-C1s`).
- A part that is present with a value that is not > 0 (0.0 or None in the row) is an **error**,
  never a silent open.
- The op-amp slot is the tool's model: input pins draw no current, and the output is a Thevenin
  source behind Ro. The IR stores only pins. The model comes from the dummy (§5).

### 2.3 Where the tables come from

The tables are written by hand, one per superset, **read off the KCL** of the non-ideal model
(`build_nonideal`). The non-ideal model keeps every node, unlike the ideal path, which merges V−
into V+. The `present` / `absent_as` rules are copied from `_gates` and the order branches.

Two real examples show why the IR must distinguish open from short:
- **Open.** An absent `R6` in the Sallen-Key LP (`g6 = 0` unless the gain is "gained") and an
  absent `R7` (`g7 = 0`) mean the part is not there.
- **Short.** In a unity-gain LP without a notch, `shorted_r5` is set: the equation becomes
  `Vm − V2 = 0` (`cells_lp.py:212-213`), so R5 is a wire. In a 2nd-order cell the prefilter goes
  away with `Va → V1` (`cells_lp.py:231-232`). That means R1 is a short and C1 is open.

Superset count: LP, HP, notch and BP Sallen-Key; LP, HP, BP and notch MFB (with the MFB2 / LS
structures inside them); the shared Ackerberg-Mossberg core (`am_eqs`, covering 20 cells through
`kind` / `tap` / `c1_split`); and first order (ni / inv). That is **about 13–16 tables of
roughly 6–12 lines each**, against 92 variants. Correctness does not rest on reading the tables
carefully. **§15.1 check 1 proves every variant numerically.**

Rejected alternative: extracting the netlist automatically from the KCL. A two-terminal
admittance y between nodes p and q stamps +y on (p,p) and (q,q) and −y on (p,q) and (q,p), so a
sympy pass could recover it. It is attractive, because no table would need upkeep. But it is
fragile against the actual equation shapes: fused terms, `Vm` substitutions, the op-amp term
divided by Ro, and AM `R8 = R7` in the ideal path. A wrong extraction would also be as silent as
a wrong table, while a table plus the numeric check is transparent. The idea can come back later
if the table count grows.

### 2.4 Placement

The new module is **`spice_cells.py`**, Tier D with no Streamlit. It holds the IR tables,
`section_ir(row)` (built on `topo_for_name` / `FO.parse_name`), a **small complex MNA solver
`mna_ac(ir, w, opamp_params)`** and a DC-path check. The MNA serves the dev check and the
README's "expected values" (§8.3). It is about 80 lines of numpy.

Tier B stays untouched, so there is no CONTRACTS §1 change and no cache bump. The cost is that a
new cell now also needs an IR entry. That goes into the CLAUDE.md new-cell checklist, and the
check fails for a registered cell without an entry, so nobody can forget it.

---

## 3. Templates: what they can and cannot do

### 3.1 Why op-amp templates plus programmatic passives alone does not scale

The first sketch said "copy the op-amp from a template, generate the passives and wires from the
topology knowledge behind the SVG anchors". It does not scale, for three reasons:
- **The SVG anchors are label positions only.** They are `(x, y, align)` per designator in the
  827×583 viewBox (`schematic_svg.py:62-522`). They hold no pins, no wires and no orientation,
  so nothing in them can be turned into LTspice wiring.
- **The op-amp is the small part.** Drawing passives and wires programmatically means
  hand-coding coordinates for every variant. That is 92 variants (80 registry cells plus 12
  first-order), with no visual feedback while coding them. It is the same work as drawing them,
  only blind.
- **Pin positions live in LTspice's `.asy` files**, not in the `.asc`. Any programmatic wiring
  needs them per symbol and per orientation.

The instinct to use dummy `.asc` files is right. Their job is to carry what code cannot guess:
exact SYMBOL/SYMATTR text, pin geometry, the header and the encoding. They do that in three
roles.

### 3.2 Three kinds of template file (all plain LTspice-saved `.asc`)

1. **Symbol calibration, `symbols.asc`.** One `res`, `cap`, `voltage`, `bv` and `opamp2`
   in every orientation used. Each pin is stubbed by a short wire to a FLAG named
   `<symbol>_<orient>_<pin>`. Parsing it gives pin offsets per orientation, LTspice's rotation
   convention, the exact SYMBOL line text and the file header. It is verified once with LTspice's
   own netlister: every device must land on its named nets and no `N00x` net may appear (§15.2).
   The `bv` (B-source) entry is for FS-026 (§11.4).
2. **Cell templates** (§4). There is one per superset, and optionally one per exact variant.
   - InstNames are the tool's internal keys (`R1`, `C2`, `C2b`, `U1`).
   - Port FLAGs are `IN` and `OUT`, and the supply FLAGs are `VCC` and `VEE`.
   - Each internal net carries a FLAG with its IR node name. That makes LTspice's own netlist of
     the template directly comparable to the IR.
   - Each op-amp position is a **seat** (§5.4): wire ends at the fixed seat terminal points, plus
     a `;SEAT U1` comment at the seat origin. The seat may hold the FS generic block as a
     placeholder, so the template also simulates on its own.
   - Split caps get a parallel slot (`C2b`).
3. **Op-amp dummies** (§5). There is one per SPICE model, and together they form the local op-amp
   library.

### 3.3 Lookup per section

The exporter tries, in order:
1. `cells/<topology>.asc`, an exact-variant override. The maintainer draws one where gating a
   superset looks awkward.
2. `cells/<superset>.asc` with gating.
3. **Auto-layout** from the IR (§4.4).

The UI shows which one each section used.

### 3.4 Folder and packaging

The folder is `LTspice_Library/`. It holds `symbols.asc`, `cells/`, `opamps/` (the dummies
plus `_seat_template.asc`, an empty seat to start a new dummy from), `models/` (user-supplied
model files referenced by dummies, never committed from vendors) and `tools/` (the open-loop
harness, Appendix A.6). It follows the SVG pattern:
- It is bundled in `FilterSynthesizer.spec` `datas`.
- `build.bat` copies it next to the exe as a user-editable copy.
- `launcher.py` points `FILTERSYNTHESIZER_LTSPICE_DIR` at the exe-adjacent copy, falling back to
  the bundled one (like `launcher.py:219-237`).
- User dummies go in a per-user overlay, `%LOCALAPPDATA%\FilterSynthesizer\LTspice_Library\opamps\`,
  where a file of the same stem overrides a built-in one. That mirrors `opamp_library_user.json`,
  so the user's own models survive upgrades.

Root `*.py` modules are picked up by the `.spec` glob automatically. Proposed convention for
CLAUDE.md (like the SVGs): cell templates are hand-drawn in LTspice and are never regenerated or
reformatted programmatically.

---

## 4. Template transform — optional parts and shorts, reliably

### 4.1 Operations on a cell template, per section

The transform does the following to each section's template:
1. **Parse** into records: SYMBOL (plus its WINDOW/SYMATTR lines), WIRE, FLAG, IOPIN, TEXT and
   drawing lines (LINE, RECTANGLE, CIRCLE, ARC). Any unknown record type rejects the template, in
   the dev check as well.
2. **Gate.** For each IR part that is absent in this variant:
   - `open` means removing the SYMBOL block;
   - `short` means removing the SYMBOL block and adding a WIRE between its two pin points (known
     from `symbols.asc`). R and C pins are collinear along the symbol axis, so one straight
     segment always suffices.
3. **Split caps.** Keep or delete the `C1b` / `C2b` slot.
4. **Prune** dangling wires: repeatedly remove a segment whose endpoint touches no pin, FLAG or
   other wire. This is cosmetic only, because a dangling stub is electrically harmless.
5. **Values.** Set SYMATTR Value to the formatted nominal (§12), or to the MC expression (§9).
6. **Rename.** InstName becomes the stage designator (`R201`). Internal FLAGs become `S2_a`,
   `S2_m`, and so on. `IN` / `OUT` become the cascade nets of §4.5.
7. **Op-amps.** Clear each seat's interior and insert a copy of the section's dummy block, so
   its terminals land on the seat's wire ends (§5.4).
8. **Strip** the template's own TEXT directives. The exporter owns all directives. It adds a
   section title comment instead.
9. **Translate** into the column (§4.5).

### 4.2 Connectivity model

To re-extract nets from geometry, the exporter needs LTspice's joining rules:
- wire endpoints that coincide connect;
- an endpoint lying on another wire's interior connects (a T-junction);
- a pin connects when a wire endpoint sits on it;
- FLAGs with the same name connect.

Two points are **[verify]**: whether a pin lying on the interior of a wire connects, and whether
two crossing wires stay separate.

The templates are drawn so that neither case arises: every pin gets a wire *endpoint*, and there
are no crossings. The engine treats either case as an error.

### 4.3 Self-check at export time (the reliability guarantee)

After the transform, the exporter re-extracts the section's nets and compares them with the
section IR, part by part. Each part must have the same two nets (up to renaming), every op-amp
pin must be on the right net, and there must be no extra parts. On any mismatch that section
falls back to auto-layout and the UI shows a warning naming the template. **A schematic whose
netlist differs from the IR is never written.** The dev check (§15.1) runs the same comparison
for every template and every variant, so a bad template is caught before release. The export
check is the second line of defence.

### 4.4 Auto-layout fallback

It is generated from the IR and needs only `symbols.asc`.
- Parts are placed on a grid: op-amps on the first row, resistors on the next, capacitors below.
- Every R/C pin gets a short stub to a FLAG carrying its net name.
- Each op-amp is a copy of its dummy block (§5.4). A FLAG with the pin's net name goes on each
  seat terminal, so auto-layout needs no op-amp pin geometry either.
- The result is electrically exact and editable in LTspice. It reads like a drawn netlist, not a
  textbook figure.
- It is what cells without a template show until their family's template exists.
- It goes through the same §4.3 self-check.

### 4.5 Column assembly

Sections are placed top to bottom in stage order. Stage order is the physical order that matters
once loading is real, and it is the Pairing order.

- **Offsets.** Each block is translated by `(0, y_k)`. `y_k` is the running sum of block
  bounding-box heights plus a 64-unit gap. Offsets are multiples of 16 to stay on LTspice's grid.
- **Nets.** The input net is `IN` (driven by `VIN`). The output of section k is `S{k}`, and the
  next section's `IN` port is renamed to `S{k}` too. The last section's output is `OUT`.
- **Port labels.** Whether IOPIN arrows (`In` / `Out`) can mark the ports in a flat schematic and
  still join by name is **[verify]**; otherwise plain FLAGs are used.
- **Titles.** Each block gets a title comment: `;Section 2 - 2LPn-gained  f0=1.234kHz Q=0.707`.
- **Top of the sheet.** `VIN`, the supply pair `VPOS` / `VNEG` (§6), a `.param` block and the
  analysis directives go above section 1.
- **Sheet size.** `SHEET 1 W H` comes from the overall bounding box.

---

## 5. Op-amps: the local library of dummy files

### 5.1 Dummy file contract

A dummy is a normal LTspice-saved `.asc` containing:
- **Exactly one op-amp SYMBOL**, with any symbol: an LTspice built-in part (for example
  `Opamps\\LT1001`), the generic `opamp2` with Value set to a subckt name, or a custom `.asy`
  (see §5.5).
- **Adapter wiring to the seat terminals**: wires from each pin to the fixed seat terminal
  points (§5.4). Each terminal point carries a FLAG named `INP`, `INN`, `OUT`, `VCC` or `VEE`.
  These FLAGs are markers for the exporter and are never copied into the output. 3-pin symbols
  omit the supplies. Extra pins, such as shutdown or compensation, are handled *inside* the
  seat (tied to a `VCC` / `VEE` / `0` label, or left open) the way the datasheet requires. Those
  three are the only labels allowed inside a dummy, because they are the only nets meant to be
  shared.
- **Directives, optional**: TEXT lines `!.lib …`, `!.include …`, or a whole `!.subckt … .ends`
  block.
- **Metadata, optional**: a comment TEXT line
  `;FS: vs_min=4.5 vs_max=36 note=TI model, download from ti.com`.
- **Nothing else.** Any other element fails the dummy check.

Because the dummy's author wires the pins to the standard terminals, the exporter never needs
the symbol's `.asy` geometry. Any op-amp symbol fits any seat. Appendix A is the step-by-step
guide for making a dummy.

### 5.2 Mapping a tool part to a dummy

The `opamp_library.json` field `spice_model` (FS-005; null for every part and read by nothing
today, `opamp_library.py:48`) is defined as **the dummy's file stem**. For example,
`"TL072": {…, "spice_model": "TL072"}` maps to `opamps/TL072.asc`.

- A part with no mapping, `Custom…` and `Ideal` all use **FS generic** (§5.3). The UI shows the
  fallback for each section.
- The export block has a per-section **override selectbox** listing every dummy in the library
  and the user overlay, so any section can use any model.
- Filling in `spice_model` for shipped parts is Phase 3 and touches the same JSON as FS-018
  (provenance fields), so coordinate the two.

### 5.3 FS generic dummy (recommendation)

`opamps/_FS_generic.asc` holds an `opamp2` symbol and a directive text block with **the tool's
own op-amp model**, `A(s) = A_ol/(1+s·A_ol/(2π·GBWP))` behind Ro (`cells_lp.py:193-194`).

```text
.subckt FS_OA_<id> inp inn vp vn out
G1 0 x inp inn 1
R1 x 0 {A_ol}              ; V(x) = A_ol·(V+ − V−) at DC
C1 x 0 {1/(2*pi*GBWP)}     ; R1·C1 = A_ol/(2π·GBWP) → pole at GBWP/A_ol
E1 y 0 x 0 1
Ro y out {Ro}              ; omitted when Ro < 1 mΩ
.ends
```

- **Baked numbers.** The exporter substitutes its own placeholders (`%A_OL%`, `%GBWP%`, `%RO%`,
  `%ID%`) and writes one subckt per distinct parameter set. It uses no LTspice parameter passing
  into subckts, so there is no dialect risk.
- **No supply dependence.** `vp` and `vn` are pins with no elements. They are driven by the
  supply sources outside, so they do not float. The model is linear with no rails, and the README
  says so.
- **Ideal needs clamping.** `IDEAL_PARAMS` (A_ol = 1e12, GBWP = 1e15 Hz, Ro = 1e-6 Ω) would put a
  1e12 Ω resistor in the matrix. So Ideal maps to A_ol = 1e8, GBWP = 1e12 Hz and no Ro. The
  deviation from the tool's ideal is about 1e-8 × the noise gain, which is invisible. The dev
  check asserts it at ≤ 1e-6 dB.
- **Why ship it**, although the maintainer ruled out "the tool model as a mode":
  - It is the only model with which LTspice *can* reproduce the tool's numbers, so validation
    (§15) needs it.
  - Ideal and Custom picks have no real part behind them.
  - It fits the maintainer's third dummy form, "a model in a directive text block". It is just
    one more dummy in the library, labelled *"FS generic (A_ol/GBWP/Ro) — replace with a real
    model"*.

### 5.4 Putting a dummy into a cell: the op-amp seat (*maintainer decision*)

The maintainer asked whether the op-amp can be copied from the dummy sheet into the final
schematic with wiring whose ends sit at fixed coordinates, so that any op-amp fits its place in
the cell. **It can.** An `.asc` is plain text, and "copy-paste" means copying the SYMBOL and WIRE
records with translated coordinates. The fixed-coordinate wiring is the **seat**.

**Seat geometry.** The seat is a square box of ±128 grid units around a seat origin. Five
terminal points sit on its boundary. LTspice's y axis points down.

| Terminal | Offset from the seat origin | Role |
|---|---|---|
| `INN` | (−128, −32) | inverting input |
| `INP` | (−128, +32) | non-inverting input |
| `OUT` | (+128, 0) | output |
| `VCC` | (0, −128) | positive supply |
| `VEE` | (0, +128) | negative supply |

The numbers are provisional. Phase 1b fixes the input order (INN above INP, or the reverse) to
match `opamp2` in R0, so the FS generic dummy's adapter wires need no crossings. 256 × 256 units
leaves room for any common op-amp symbol.

**In a dummy**, the symbol and its adapter wires lie inside the box, and the terminal FLAGs sit
exactly on the terminal points. The seat origin is derived from the terminal FLAGs. The dummy
check rejects a dummy whose terminals are off the standard offsets.

**In a cell template**, each op-amp position has:
- wire ends at the five terminal points (the supplies go to `VCC` / `VEE` labels);
- a comment TEXT `;SEAT U1` anchored at the seat origin (`U2`, `U3` for AM);
- an optional placeholder in the box interior: the FS generic block pasted in, so the template
  simulates on its own.

Nothing else may lie in the box interior. The template check enforces that.

**On export, per seat:**
1. Remove everything strictly inside the box: the placeholder symbol, its adapter wires and its
   labels.
2. Insert a copy of the section's dummy block, translated so its terminals land on the seat
   origin plus the offsets. The adapter wires' ends meet the template's wire ends there.
3. Drop the dummy's terminal FLAGs and set the InstName (`U201`).
4. The §4.3 check confirms every op-amp pin is on the right net.

**Multiple entries of one op-amp** (the maintainer's question) are safe:
- Every seat gets its **own copy** of the block, with a unique InstName (`U201`, `U202`, `U301`,
  …).
- The terminal FLAGs `INP` / `INN` / `OUT` are **dropped**, so no two instances share a label.
  Copying them would join every op-amp's inputs into one net, which the design rules out.
- The only labels a dummy may carry inside are `VCC`, `VEE` and `0`, which are *meant* to be
  common.
- The dummy's directives (`.lib`, `.subckt`) are written **once per file**, however many seats
  use the model. FS generic with several parameter sets writes `FS_OA_1`, `FS_OA_2`, … (§5.3).

**Orientation.** v1 accepts seats in R0 only, and the template check enforces it. A cell that
wants the inverting input on the other side routes its wires to the terminals instead of flipping
the seat. Flipped seats can come later by transforming the whole block, once LTspice's rotation
convention has been verified with `symbols.asc`.

**Alternative kept on file: a hierarchical block with one unified symbol.** This was the
maintainer's fallback in case copying proved impossible. It works as follows:
- A block symbol `FS_OPAMP.asy` is drawn once, as an op-amp triangle with pins
  `INP/INN/OUT/VCC/VEE` at the seat geometry.
- For every model, the exporter writes a renamed copy `FSOA_<model>.asy`, because LTspice binds
  a block symbol to the same-named `.asc` in the schematic's folder. It also writes
  `FSOA_<model>.asc`, which is the dummy with its terminal FLAGs turned into IOPIN ports.

Its advantages:
- The fit is guaranteed by construction.
- Each model is edited in one place.
- The main sheet stays uncluttered.

Its costs, which are why it is not the primary choice:
- The output becomes **several files**, which must be extracted and kept together. That runs
  against the maintainer's "one file holds the whole cascade".
- The op-amp shows as a block rather than its real symbol.
- Probing inside needs hierarchy navigation.
- Where LTspice places `.lib` / `.subckt` directives from a sub-sheet is **[verify]**.

Both mechanisms can coexist *per model*. If a symbol ever cannot be adapted inside a seat box
(for example, it needs external parts on extra pins), that one model can be marked
`;FS: mode=block` and delivered as a block.

### 5.5 Directives, external model files and symbols

- **Directives.** Each dummy's directive TEXT is copied **once per file**, however many sections
  use it (deduplicated by content).
- **Relative `.lib` / `.include` paths** resolve relative to the *output* `.asc`, not the dummy.
  The exporter rewrites them to absolute paths in the library folder. An option, *"Include model
  files in the zip"*, instead copies the referenced files into the bundle and uses bare names.
  That is portable to another machine, and it is the user's own local file, so the copy is theirs
  to make. Missing referenced files are reported before download.
- **LTspice built-in parts** carry their model link in the symbol (a ModelFile/SpiceModel
  attribute). Copying the SYMBOL block verbatim keeps it, and nothing else is needed.
- **Custom `.asy` symbols** are only found by LTspice in its library paths or next to the
  schematic. Prefer built-in symbols, for example `opamp2` plus a `.lib`. If a dummy uses a custom
  `.asy`, the zip includes it and the README says to keep it beside the `.asc`.

### 5.6 Licensing

The repo ships only its own text and references:
- **FS generic** is our own model text.
- **Built-in ADI dummies** reference the symbol only; the user's LTspice supplies the model.
- **External-model dummies** hold a `.lib` *reference*, and their `;FS:` note says where to get
  the file.

Vendor model files are never shipped or embedded by the project. This matches the existing FS-008
note ("store only the model name"). Encrypted models are never read or embedded; a built-in part
is used through its symbol.

### 5.7 Swapping later

This is the maintainer's use case. A user replaces an op-amp in the output by
right-click → Pick New Symbol, or by editing its Value / SpiceModel. A replacement symbol with the
same pin geometry drops straight onto the adapter wires. One with a different shape needs its
pins rewired to the adapter wire ends. Labelled nets (`S2_m`, `S2_out`, …) make that easy. A
replacement that should be reused goes into the library as a dummy instead (Appendix A).

---

## 6. Supply

- **UI.** One **total supply Vs** for the whole cascade.
- **Netlist.** `.param Vs=<value>`, `VPOS VCC 0 {Vs/2}` and `VNEG 0 VEE {Vs/2}`. GND (`0`) is the
  midpoint and the signal reference, so single-supply parts see a virtual ground at mid-rail,
  which is how the tool's model already treats every signal. Editing `Vs` in LTspice changes both
  sources.
- **Default.** **5 V**, i.e. ±2.5 V (*maintainer decision*). The UI warns when Vs is outside any
  used dummy's `vs_min…vs_max`. Classic ±15 V parts (TL072, NE5532) are usually specified from a
  few volts per rail upward, above ±2.5 V. Their dummies must therefore state `vs_min`, so the
  default produces a visible warning rather than a silent, badly biased model. Check the value in
  the datasheet when making the dummy.
- **Real models need a sane DC operating point.** The offset times the cascade's DC gain can drive
  an output into a rail, and then the AC result, a linearization around that point, is
  meaningless. The AC files carry a commented `;.op` directive, and the README says to run it once
  and check the section outputs sit near 0 V.
- **Real models need a DC path from every op-amp input** (bias current). A capacitor-only input
  node is fine in the tool's model but singular or saturating in SPICE. §15.1 check 2 runs a
  DC-path analysis over the IR for all 92 cells. Any cell that fails is a real hardware issue, to
  report rather than patch around.

---

## 7. Files: why separate, and what is in the bundle

**Why separate files:**
- **One analysis type per simulation.** Several analysis directives in one file force the user to
  comment and uncomment them by hand.
- **Random functions are always random** (the maintainer's point). `mc()`, `gauss()` and `flat()`
  draw on every evaluation, whether there is a single `.step` run or no `.step` at all. So a file
  with MC expressions can never show the nominal circuit. A multiplier switch
  (`{nom*(1+mcon*gauss(tol/3))}` with `.param mcon=0`) would work, but it is an edit the user must
  know to make, and it hides the numeric values behind expressions.
- **Readable values.** The nominal file shows plain values (`4.99k`, `10n`), which is what a user
  copies to a BOM or edits.
- **No extra cost.** The drawing is generated once and reused for every file, so extra files cost
  nothing and cannot drift apart.

**v1 bundle:** `FS_LTspice_<spec>_<yyyymmdd_hhmm>.zip`, with `<spec>` from `report_spec_short`
like the PDF. It contains:
- `<spec>_AC.asc`: nominal values and `.ac`.
- `<spec>_AC_MC.asc`: MC expressions, `.step`, `.save` and `.meas` (§9).
- `README.txt`:
  - what each file is;
  - the op-amp model per section;
  - the supply;
  - the tolerance settings;
  - how to view group delay and the MC `.meas` table;
  - how LTspice's result differs from the tool's curve and why (§10);
  - the expected values (§8.3).
- The copied model files and `.asy` files, if needed (§5.5).

FS-026 adds `<spec>_TRAN.asc` to the same builder (§11.4).

Rejected alternatives:
- one file with an `mcon` switch (above);
- one file with commented alternative directives.

Not done: **MC run 1 = nominal** (`.func TOL(nom,tol) {if(run==1, nom, …)}`). The maintainer's
experience is that the first run of a stepped MC is not a trustworthy nominal reference in
practice. The nominal circuit therefore lives only in the separate nominal file (*maintainer
decision*, 2026-09-28). All N runs of the MC file are random.

---

## 8. AC analysis

### 8.1 Directives

- **Source.** `VIN IN 0 AC 1`, so V(out) *is* H(jω).
- **Sweep.** `.ac dec <ppd> <fmin> <fmax>` from the tool's grid, `_freq_grid`
  (`response_tab.py:145-156`): fmin = max(1e-3, 0.1·min f), fmax = 50·max f, over the section f0,
  fz and f1. There ppd = ⌈600 / log10(fmax/fmin)⌉. For example, a 1 kHz LP (fmin = 100 Hz,
  fmax = 50 kHz, 2.7 decades) gets about 222 points per decade.
- **Phase and group delay.** LTspice plots dB and phase by default. Group delay is available from
  the phase axis (right-click the axis), which Bessel and Equiripple Delay users will want.

### 8.2 Probes

`.meas AC` lines give numbers that can be compared with the tool, not just curves:

```text
.meas AC G_ref FIND V(out) AT <f_ref>
.meas AC G_f1  FIND V(out) AT <f1>
```

- `f_ref` is the passband reference: DC for LP, f0 for BP, the HF passband for HP, and DC for BR.
- The band edges are `f1` / `f2`.
- One stopband frequency is `f_s`, when defined.
- Each line is followed by a comment with **the tool's unloaded prediction** at that frequency.

In the MC file the same `.meas` lines give one value per run. The LTspice log then shows the
spread and can plot it against `run`. That is the only percentile-like view LTspice offers (§9).

### 8.3 Expected values in the README

When **every** section uses FS generic, the exporter runs `spice_cells.mna_ac` on the *loaded*
cascade IR (the same netlist LTspice will solve) at the probe frequencies. It prints those
expected values next to the tool's unloaded values. The user can then check the LTspice run
without any tooling (§15.2), and the size of the loading effect is visible for every design.
With real models these rows are omitted, because the models differ.

---

## 9. Monte Carlo

### 9.1 What the tool does (to mirror)

`hw_plots.monte_carlo` (`hw_plots.py:241`):
- It varies every symbol starting with R or C except Ro. The op-amp stays fixed.
- **A split cap is one part**: `comp_dict` reads `C2`, the total (`hw_plots.py:27-43`).
- Resistor tolerance comes from value bands (`_r_tol_frac`, `hw_plots.py:231`, first match,
  inclusive ends), with a default for values outside every band.
- Gaussian means tol = 3σ, untruncated. Uniform means ±tol.
- A value ≤ 0 is replaced by nom·1e-3.
- It uses one `default_rng(seed)`.
- The output is a percentile envelope.

### 9.2 LTspice mapping

The directives in the MC file:

```text
.param tC=0.05                    ; capacitors ±5 %
.param tR1=0.01                   ; resistors 0 … 10 kΩ     (band 1)
.param tR2=0.001                  ; resistors > 10 kΩ       (band 2)
.param tR0=0.01                   ; resistors outside every band (tool default)
.func TOL(nom,tol) {nom*(1+gauss(tol/3))}     ; Gaussian, tol = 3σ, untruncated
; .func TOL(nom,tol) {nom*(1+flat(tol))}      ; Uniform ±tol (swap the comment to switch)
.step param run 1 2000 1
.save V(out)
```

Component values read `{TOL(4.99k,tR1)}`.
- **Band membership** is computed by calling `hw_plots._r_tol_frac` on each nominal. The
  semantics are then identical by construction, including inclusive ends and the default.
- **Editability.** The user can change a tolerance, the distribution or N in one place.

### 9.3 Traps and differences

- **A `.func` must draw independently on every call.** Each component must get its own draw.
  Whether a `.func` body containing `gauss()` is re-evaluated per call site is **[verify]**, with a
  two-resistor test (§15.2). If it is not, the exporter writes the expression inline:
  `{4.99k*(1+gauss(tR1/3))}`.
- **Never put the random draw in a `.param`.** A `.param d=gauss(…)` is (very likely) evaluated
  once per step and *shared* by everything that uses it, which correlates all parts. That is the
  classic LTspice MC mistake.
- **Raw-file size.** An AC run stores complex doubles, 16 B per vector per point. With N = 2000 and
  600 points, V(out) alone is about 19 MB. Saving every node voltage and device current of a
  4-section cascade (about 40 vectors) is about 0.8 GB. So **`.save V(out)`** is mandatory in the
  MC file. The nominal file has no `.save`, so every node can be probed.
- **Run count.** The default is the tool's `resp_runs` (2000). AC runs are fast, but LTspice gets
  slow *plotting* thousands of traces. The UI shows a note above 1000 runs, and the value can be
  edited there.
- **Seed.** LTspice's RNG is not numpy's, so runs can match the tool only statistically. Whether
  LTspice repeats the same sequence on every run and honours `.option seed=` is **[verify]**; it
  is informational only.
- **Envelope.** LTspice draws all traces and has no percentile band. The `.meas` table (§8.2) is
  the quantitative comparison.
- **Split caps** are two physical parts in the file, `C202A` and `C202B`, each with its own draw.
  That is physically right, but narrower than the tool, which draws the sum as one part. For two
  equal halves the sum's σ drops by 1/√2. The README mentions it (finding §16).
- **Negative tail.** An untruncated Gaussian can give a negative value. The tool clamps it; LTspice
  would not. At tol = 3σ ≤ 50 % that needs a 6σ event, so it is ignored and documented.
- **Op-amps are not varied**, as in the tool. With real models the model's own typical values
  apply.

---

## 10. Inter-stage loading (real cascade only)

The tool's realized curve is a **plain product** of per-section responses. Each section is solved
with an ideal source and an unloaded output (`response_tab.py:378-407`, `hw_plots.cascade`
L136, `cells_lp.py:191` V1 = 1). The exported file is the physical cascade:
- Section k's output is the op-amp's Thevenin output. Its closed-loop output impedance is roughly
  Ro/(1+T(jω)), where T is the loop gain.
- It drives section k+1's input impedance.

In the passband T is large, so the effect is negligible. For example, TL072 with Ro = 50 Ω and
GBWP = 3 MHz, as a unity follower at 1 kHz, gives about 0.02 Ω against a 10 kΩ input.

Where T falls, at high frequency in the stopband, Z_out approaches Ro. For high-Ro parts this is
comparable to the next stage's input resistance: LMV358A has 1.2 kΩ and AD8505 has 1 kΩ, against
typical 1–10 kΩ inputs. That can reach dB level in the far stopband. It is the same region the
tool's "HF rise" warning is about (`response_tab.py:430-450`).

Consequences:
- **LTspice ≠ the tool** in the far stopband, by design. The README explains it.
- **Validation** does not compare LTspice with the tool's product. It compares LTspice (with FS
  generic) with the **MNA of the loaded IR**, which must agree to numerical precision (§15.2). The
  IR MNA with and without loading gives the tool-vs-SPICE difference as a number (§15.1 check 6).
- **The tool's model gap is real** (finding §16). With the IR and `mna_ac`, a loaded realized
  response inside the tool becomes cheap later.

---

## 11. Transient (FS-026) — impulse theory and practice, and the base built now

### 11.1 What "impulse response" means for a circuit with real op-amps

- **h(t) is defined for LTI systems only.** A circuit with real op-amp models is LTI only in
  small-signal operation around its DC operating point: away from the rails, below slew limits,
  inside the input common-mode range.
- **So a real circuit's "impulse response" means the linearized circuit's response.** The exact
  version of that is the tool's own linear model, which FS-024 computes from poles, zeros and the
  section transfer functions.
- A SPICE transient is worth running for what the linear model cannot show: slew, clipping,
  recovery and model-specific dynamics. A step does that.

### 11.2 How it is done in practice

1. **From the linear model.** This is the usual way. Datasheets, filter handbooks and design tools
   quote h(t) and the step response computed from H(s). It is exact and has no amplitude issues.
   In this project, that is FS-024.
2. **SPICE, derivative of a small-signal step: h(t) ≈ (1/A)·dv_out/dt.**
   - The step amplitude A is small enough to stay linear.
   - It uses one moderate edge, so it is gentle on slew.
   - The DC offset baseline disappears, since the derivative of a constant is 0. With real models
     the output sits on Vos·gain.
   - The same run also gives the step response.
   - Costs: the derivative amplifies timestep noise, so use `.options plotwinsize=0` (no waveform
     compression) and a bounded max timestep.
   - For HP, BR and notch cells with H(∞) ≠ 0 the step response jumps by A·H(∞) at t₀. Its
     derivative is a spike of width ≈ t_rise, which is the Dirac term H(∞)·δ(t). It must be clipped
     in the display and reported as its weight H(∞), as FS-024 must also do.
3. **SPICE, narrow small-area pulse ("quasi-impulse"): v_out ≈ A·T·h(t).**
   - The width T must be short: the pulse spectrum is flat to 1 % up to f_max only if
     T ≤ ~0.08/f_max, where f_max is the highest frequency that matters (for example 10·f_c).
   - The amplitude A must stay linear. HP and BR feed A·H(∞) straight through to the output, and
     A/t_rise must stay below the slew rate.
   - The response is small, so it must stay well above LTspice's absolute tolerances (`vntol`
     1 µV default).
   - The offset baseline must be subtracted by hand. An LTspice plot cannot subtract a `.meas`
     result.
   - It is workable for LP with modest settings. For example, a 1 kHz LP with f_max = 10 kHz gets
     T = 8 µs, and A = 1 V gives an output peak of about 24 mV. But it is fiddlier than (2) and
     gains nothing.
4. **AC analysis, then inverse FFT.** This is post-processing outside LTspice. It is equivalent to
   (1), so it belongs in the tool.

**Practice therefore:** the step is the standard SPICE time-domain test, at realistic levels. The
impulse response is taken from the linear model (1), or from SPICE as the derivative of a
small-signal step (2).

### 11.3 Recommendation for FS-026

- **One `<spec>_TRAN.asc`**, nominal only, with **no MC in the time domain** (*maintainer*).
- **Source:** `VIN IN 0 PULSE(0 {Astep} {t0} {tr})`, a step.
- **Two amplitudes in one run:** `.step param Astep list <A_small> <A_large>`.
  - `A_small` is linear. With FS generic dummies it must reproduce FS-024's realized step
    response. The loaded vs unloaded caveat of §10 applies, but it is tiny in the passband.
  - `A_large` is about 70–80 % of the available output swing, divided by the peak output per unit
    step. It shows slew, clipping and recovery with the real models.
- **Normalized plot:** plot `V(out)/Astep` so the two runs overlay when linear. Any visible
  separation *is* the non-linearity.
- **Impulse node:** `BIMP IMP 0 V=ddt(V(out))/Astep`, a behavioural source that draws no current
  and is ignored by the circuit. **[verify]** `ddt()` in `bv` and `d()` in plot expressions. The
  README notes the H(∞)·δ spike for HP, BR and notch.
- **Presets from FS-024:**
  - window t_stop and settling, with a starting guess of t₀ + ~10·max(2Q/ω₀, 1/ω₁) over the
    sections;
  - max timestep ≤ 1/(20·f_hi);
  - peak output per unit step (for `A_large`);
  - max slope per unit step (for the slew check, once the op-amp library has a slew-rate field).
- **Directives:** `.tran 0 {tstop} 0 {dtmax}`, `.options plotwinsize=0`, and no `startup` / `uic`.
  With supplies present, the DC operating point is the right starting state.

### 11.4 Base built in v1 (so FS-026 is small)

- A **source abstraction** in `spice_export.py`: `{kind: "ac" | "step" | "pulse", …}` produces the
  `VIN` Value string. Only `ac` is used in v1.
- A **per-analysis directive generator**: `directives("ac", …)` is implemented;
  `directives("tran", window, …)` raises `NotImplementedError` until FS-024 provides `window`.
- A **bundle builder** that takes a list of `(filename, analysis spec)`. FS-026 appends one entry.
- **`symbols.asc`** already calibrates `bv` (the impulse node), so FS-026 needs no new
  LTspice calibration round.
- **Interface requested from FS-024.** A Streamlit-free helper returns, for the realized cascade:
  `{t_stop, dt_max, t_settle, peak_per_unit_step, max_slope_per_unit_step, h_inf}`. This is
  recorded in the FS-024 Notes.

---

## 12. LTspice format caveats (checklist for the writer)

- **Suffixes.** SPICE `M` is **milli**, so write `Meg` (1 MΩ = `1Meg`). `F` is **femto**
  (`1F` = 1e-15), so never write a bare `F`; a trailing `F` after a scale letter (`10nF`) is
  harmless, but emit `10n`. `µ` becomes `u`.
- **Number format.** Engineering form with at most 4 significant digits for snapped E-series
  values (`4.99k`, `12.1k`, `680p`, `1.5u`). Continuous values get 6 significant digits, with no
  float noise such as `4.7000000001n`. A round-trip test is in §15.1.
- **Values in the row** are MΩ and µF. Convert R_Ω = R·1e6 and C_F = C·1e-6 in one place.
- **Encoding.**
  - Output is ASCII: no `µ`, `Ω` or `σ`. Transliterate in comments and refuse non-ASCII in values
    or directives.
  - Templates may have been saved by LTspice as UTF-16LE or cp1252. Read them with BOM detection.
  - Whether LTspice 24 writes UTF-16 is **[verify]**.
- **Line endings.** CRLF, which is harmless everywhere.
- **Header.** Copy `Version …` from `symbols.asc` (whatever the maintainer's LTspice wrote), then
  `SHEET 1 W H`.
- **Grid.** Keep coordinates on multiples of 16.
- **Multi-line TEXT** (an embedded `.subckt`): copy LTspice's own encoding of the newlines from
  the dummy verbatim. **[verify]** it when generating the FS generic block.
- **InstName.** Start it with the symbol's prefix letter (`R201`, `C202A`), so the netlist names
  stay predictable. Op-amp `X` symbols take `U201`, which LTspice netlists as `XU201`. What
  LTspice does with an InstName that doesn't start with the prefix is **[verify]**, and the design
  avoids the case.
- **Net names.**
  - Never use LTspice's auto pattern `N###`.
  - Ground is the FLAG `0`.
  - Labels are ASCII identifiers (`S2_m`, `VCC`, `VEE`, `IN`, `OUT`).
- **Zero ohms.** Shorts are wires, never 0-Ω resistors (LTspice rejects R = 0 **[verify]**).
  FS generic omits Ro when Ro < 1 mΩ, and the library allows `Ro_ohm = 0`.
- **Floating nodes** give a singular matrix at `.op`. The DC-path check covers them (§6).
- **Symbol paths** in SYMBOL lines are written as LTspice writes them (`Opamps\\opamp2`). They are
  always copied from a template, never typed.
- **One analysis per file**, as in §7.
- **Opening from the zip.** Windows Explorer can open an `.asc` inside the zip by extracting it
  alone. That works because the files are self-contained, unless a dummy needs external files.
  Then the README and the UI say "extract first".
- **LTspice version.** The target is **LTspice 24.x only** (*maintainer decision*). Templates are
  saved from 24.x, and the output copies their header.

---

## 13. UI (Resulting Response tab)

The block sits in `response_tab.py`, **after the Monte Carlo settings** (`response_tab.py:455-501`),
because it needs `mc_params` and `sections_data`. It goes before or beside the report section.
The Streamlit code lives in a new **`spice_ui.py`** (like `report_ui.py`), and `response_tab.py`
gets one call.

- **Header:** "LTspice export".
- **Supply Vs (V)** input, **default 5 V**, with a caption *"drawn as ±Vs/2, GND at midpoint"*.
  Supply-range warnings come from the dummies' metadata.
- **Table:** section, the tool's op-amp choice, the SPICE model (dummy stem, or "FS generic
  (fallback)"), a template status (exact / superset / auto-layout) and the dummy-check result. A
  dummy that fails the check is not offered, and the reason is shown. A per-section override
  selectbox lists the library dummies.
- **MC settings** shown read-only from `mc_params`: runs (editable, defaulting to `resp_runs`),
  distribution, capacitor tolerance and resistor bands. A note says they are taken from the
  settings above.
- **Checkbox:** "Include model files in the zip" (§5.5).
- **Download:** a `st.download_button` for the zip, with mime `application/zip`. Generation is
  string building (milliseconds), so the bytes are built on every render, with no Generate
  button. It is wrapped in try/except, and any error becomes a message, never a broken tab.
- **Gate:** the button is disabled, with the reason shown, when any section has no pick or is
  `pending` (dispatch gate, `topology_tab.section_kind`, L213).
- **Designators:** `spice_ui` builds the display-designator map with the helpers the report and
  schematic already use (`schematic_svg.bp3_alias` L304, `_am_labels` L422, and
  `topology_tab._am_row_designators`). It passes that map in, so `spice_export.py` stays free of
  Streamlit.
- **New widget keys** (for ROADMAP §7): `spice_vs`, `spice_opamp_{n}`, `spice_mc_runs`,
  `spice_include_models`.

---

## 14. Build plan (files, order, phases)

### Phase 1 (Claude; everything verifiable without LTspice)

1. **`spice_cells.py`**: the superset IR tables (§2), including the first-order module (which is
   not in `REGISTRY`), plus `section_ir(row)`, `cascade_ir(rows)`, `mna_ac` and `dc_paths`.
2. **`dev/fs008/check_spice_export.py`** §1–§3 (IR vs transfer function, DC path, formatting).
   It **must pass before any writer work.**
3. **`spice_export.py`** (no Streamlit), containing:
   - value formatting and the MC expression and parameter blocks;
   - the directive generator and source abstraction (§11.4);
   - the FS generic subckt;
   - the `.asc` model: parse, serialize, geometry transforms and connectivity extraction;
   - auto-layout and column assembly;
   - the §4.3 self-check;
   - the zip and README builder.
4. **`LTspice_Library/`**: a provisional `symbols.asc`, `opamps/_FS_generic.asc` and
   `opamps/_seat_template.asc`, written as text following LTspice's format. They are confirmed in
   Phase 1b. The same step adds `check_opamp_dummy()` (Appendix A.4) to `spice_export.py`.
5. **`spice_ui.py`**, plus the one-call hook in `response_tab.py`.
6. **Packaging**: the `FilterSynthesizer.spec` `datas` line, the `build.bat` copy, and the
   `launcher.py` env var.
7. **Docs**:
   - `docs/ARCHITECTURE.md`: new modules and data dir; the Tier D table.
   - `docs/CONTRACTS.md` §6: correct the stale Solution schema (R8; `C1a/C1b/C1_parallel`;
     absent = 0.0 or None).
   - `CLAUDE.md`: new-cell checklist gets "+ IR entry in `spice_cells.py`, + optional LTspice
     template"; the template folder is hand-drawn.
   - `dev/ROADMAP.md`: FS-008 state and the §7 entry.

### Phase 1b (maintainer, in LTspice 24 on Windows)

- Open `symbols.asc` and run the netlister (`LTspice -netlist symbols.asc`, **[verify]** the flag
  in 24.x). Every device must sit on its named nets. Save it back if LTspice rewrote anything.
- Open `_FS_generic.asc` and fix the seat's input order to match `opamp2` in R0 (§5.4).
- Export a design and open both files.
- Run through §15.2.

### Phase 2 (maintainer draws; Claude wires them in)

This goes one family at a time: the Sallen-Key LP superset first, since it is the most used, then
HP, MFB LP/HP, AM, BP/notch and first order.
- **Maintainer:** draws the superset per §3.2 and runs `-netlist` on it.
- **Claude:** adds the template path (gating, shorts, prune, seat insertion), extends the check
  to all variants of that family, and confirms that every variant's transformed drawing equals
  its IR.

The families that already have a template switch from auto-layout to the template.

### Phase 3 (op-amp library content — the maintainer's call, Appendix A)

Which parts get dummies, and in what order, is the **maintainer's decision**. It cannot be fully
automated: it depends on which models the maintainer trusts and has obtained legally, and on pin
orders and supply ranges read from datasheets. Claude supplies the tooling:
- **`dev/fs008/make_opamp_dummy.py`** writes an `opamp2`-based dummy from a subckt name, model
  file, vendor pin order and supply range. It reuses the FS generic adapter wiring and adds a
  pin-order wrapper when needed (Appendix A.7). No drawing is involved.
- **`tools/opamp_openloop.asc`**, an open-loop harness with one seat. The exporter writes
  `<stem>_openloop.asc` for any dummy, so its A_ol, GBWP and Ro can be compared with the library
  entry (Appendix A.6).
- **`spice_model` values** in `opamp_library.json` as dummies land, coordinated with FS-018 /
  FS-019.

---

## 15. Validation

### 15.1 Automated (Claude, here): `python dev/fs008/check_spice_export.py`

The check needs `pip install -r requirements.txt`, for sympy.

1. **IR vs cell transfer function, all 92 cells.** This covers every `all_cells()` of every
   `REGISTRY` module plus the 12 first-order cells.
   - Draw random values: R log-uniform 1 kΩ–1 MΩ; C 100 pF–1 µF; A_ol 1e4–1e7; GBWP 1e5–1e8 Hz;
     Ro 10–2000 Ω.
   - Take 5 draws × 30 frequencies from 1 Hz to 10 MHz.
   - Compare `mna_ac(section_ir)` with `make_response_func(derive_nonideal(topo))` (or the
     first-order non-ideal).
   - Pass: |ΔH| ≤ 1e-9 · max|H| per draw.
   - Also run the split-cap variants (`C2a/C2b`, `C1a/C1b`) and AM `R8 ≠ R7`.
2. **DC path.** Every node and every op-amp input reaches ground through R, sources or op-amp
   outputs with capacitors removed. Any failure is reported per cell, as a hardware finding.
3. **Formatting.** Values round-trip through SPICE parsing within 1e-12 relative. There is never a
   bare `M` or `F` suffix, and the output is ASCII only.
4. **`.asc` round trip.** For auto-layout (Phase 1) and each template × variant (Phase 2), the
   re-extracted connectivity of the written file must equal the IR, per section and for the
   cascade.
5. **MC mapping.** Each resistor's band parameter must equal `_r_tol_frac`'s choice. A 0 % band
   is written as it appears in `r_bands`; see finding §16.
6. **Loading, informational.** Compare `mna_ac(cascade_ir)` loaded with and without ideal buffers
   against the tool's product on two designs:
   - buffered must equal the product within 1e-9;
   - print the loaded deviation (dB) at the probe frequencies.
7. **FS generic Ideal clamp.** The response with the clamped parameters must equal the tool's
   `IDEAL_PARAMS` response within 1e-6 dB.
8. **Dummy and template contracts.** Every shipped dummy passes `check_opamp_dummy`. Every
   template's seats are R0, their interiors hold only the placeholder, and the terminal points
   coincide with wire ends.
9. `python verify.py` still passes. No Tier B change is expected, so this is a smoke test only.

### 15.2 Maintainer (LTspice 24)

These run on one design per family: Sallen-Key LP / HP / BP / notch; MFB LP / HP / BP / notch;
AM LP / HP / BP / notch; and first-order LP / HP ni / inv. Where possible, one design mixes
families in a single cascade.

- **Opens and runs.** Both files open without errors or warnings and run.
- **Accuracy.** With FS generic, the `.meas` values in the AC file match the README's "expected
  (loaded MNA)" values within 0.01 dB. The "tool (unloaded)" column differs only in the stopband,
  as §10 predicts.
- **MC spread.** The spread of `.meas` over runs at the passband and edge probes is comparable to
  the tool's p1–p99 band. It is slightly narrower when a split cap is present.
- **[verify] list:**
  - `.func` with `gauss()` draws independently per call: two identical resistors with
    `{TOL(10k,0.3)}` must differ in each run;
  - seed behaviour;
  - rotation convention;
  - pin-on-wire / crossing rules;
  - IOPIN flags joining by name;
  - multi-line TEXT encoding;
  - UTF-16 templates;
  - InstName prefix handling;
  - R = 0 is rejected;
  - the `-netlist` flag;
  - for FS-026 later, `ddt()` and `d()`.
- **Real part.** One design with a built-in ADI part dummy and one with an external `.lib` dummy
  both run at the chosen Vs. The commented `.op` shows the section outputs near 0 V.
- **Seats.** Test an FS generic block, a built-in-part dummy and an external-model dummy, each
  inserted into the same template seat. All connect: LTspice shows no unconnected pins, and its
  netlist puts every `U` pin on its `S…` net.
- **Swapping.** Replace one op-amp in the output file by hand. The circuit still simulates, which
  checks the labelled nets and the adapter wiring.

---

## 16. Findings for other items

- **The tool ignores inter-stage loading** (§10). The realized curve is an unloaded product. This
  becomes the follow-up item **FS-027** (the maintainer approved it; whether to build it is
  decided on feasibility). Until then, the README and §8.3 quantify the difference per design.
  - **Feasibility sketch.** At each frequency, get each section's two-port (ABCD) parameters
    from its IR with two MNA solves, one with the output open and one with it shorted. Chain-
    multiply them, then terminate with the ideal source and an open load. The unknowns per
    section are about 8–25, and batched numpy solves are cheap: milliseconds for the nominal
    curve, and seconds for 2000 MC runs × 600 frequencies, comparable to today's MC.
  - **Costs.** The realized-response path (`response_tab`, `hw_plots.monte_carlo`, the report)
    would move from symbolic per-section H to the IR MNA. Group delay (`build_loggrad`) would need
    a numeric derivative. Solvers and scoring stay per-section and unloaded; only the verification
    view changes.
- **MC split-cap correlation.** `hw_plots.comp_dict` varies `C2a+C2b` as one part, so the tool's
  MC is slightly pessimistic for split caps. It could be a small follow-up that draws each part.
- **A 0 % resistor tolerance becomes 1 %.** `response_tab.py:207` has
  `get(f"rtol_tol_{i}", 1.0) or 1.0`. It is a UI quirk that affects the tool's MC and, through
  `mc_params`, the export. It is a one-line fix, outside this item.
- **CONTRACTS §6 is stale.** The Solution schema omits R8, `C1a/C1b/C1_parallel` and the
  0.0-vs-None absent convention. It gets corrected in FS-008's build, since the writer depends on
  it.
- **FS-009 (noise).** With dummies, the noise comes from the chosen SPICE model. FS generic can add
  input noise sources from `en_nV_rtHz` / `in_pA_rtHz` when those library fields get values
  (FS-018). A `.noise` file then slots into the §11.4 bundle builder.
- **FS-010 (QSpice).** QSpice's schematic format differs, so its cheap route is **netlist-first**
  from the IR (`.cir` plus QSpice's MC functions). The template machinery is LTspice-specific.
- **FS-024.** It must expose the time-window and step-metrics helper (§11.4) for FS-026.
- **FS-018 / FS-019.** The op-amp JSON gains `spice_model` values (dummy stems). New parts should
  come with a dummy, or knowingly fall back to FS generic.

---

## 17. Open questions — answered 2026-09-28

1. **Designators.** `R201` scheme accepted.
2. **Default Vs.** 5 V (±2.5 V).
3. **Initial dummy set.** Left to the maintainer. Claude provides guidelines (Appendix A), the
   dummy check, the generator and the open-loop harness (Phase 3).
4. **LTspice versions.** 24.x only.
5. **Op-amp placement.** Copying from the dummy into a **seat** with fixed wire-end coordinates.
   It is feasible, so it is the design (§5.4). Multiple instances are separate copies with no
   shared labels. The hierarchical-block variant with a unified symbol is documented as the
   alternative.
6. **MC run 1 = nominal.** No. Nominal is only in the separate nominal file.
7. **Loaded realized response in the tool.** Yes, as **FS-027**, decided after a feasibility check
   (§16).

Nothing left open blocks PLANNED. The [verify] items (§15.2) are Phase 1b checks, not design
questions.

---

## Appendix A — Making op-amp dummies (guidelines for the maintainer)

### A.1 Which parts first

The maintainer decides; this is only a suggestion. Criteria:
- **Use.** Parts that actually appear in designs, starting with the 8 built-in library entries.
  Also parts whose tool values are doubted (FS-018), since a model gives an independent
  cross-check.
- **Model availability and licence.** There are three sources:
  - LTspice 24's own library: search the component picker. Most ADI and many former-Maxim parts
    are there. **[verify]** which of AD8505 and MAX9636 / MAX40100 are present.
  - A vendor download (TI, onsemi, ST, …). The model must be plain-text SPICE (PSpice-compatible),
    not encrypted for another simulator and not TINA-only.
  - No model at all. Then FS generic with the library's A_ol/GBWP/Ro is the honest choice.
- **Supply fit at the 5 V default.** AD8505, LMV358A, MAX9636 and MAX40100 are 5 V parts. LM358
  and OPA1656 also run at 5 V. TL072 and NE5532 are ±15 V-class parts, so record their `vs_min`
  from the datasheet and expect the warning at the default.
- **A suggested first batch**, where each item exercises one mechanism:
  1. FS generic (Phase 1).
  2. One LTspice built-in part from the library (kind A).
  3. TL072 via TI's model (kind B, the external `.lib` path).
  4. The rest as designs need them.

### A.2 Three kinds of dummy

| Kind | Symbol | Model comes from | Directives in the dummy | May be committed? |
|---|---|---|---|---|
| **A** built-in | the part's own LTspice symbol | LTspice's library (link in the symbol) | none | yes (reference only) |
| **B** external file | `opamp2`, Value = subckt name (or a wrapper) | a `.lib` / `.sub` file in `LTspice_Library/models/` or the user overlay | `.lib <file>` (+ wrapper `.subckt` if the pin order differs) | the dummy yes, the vendor file **never** |
| **C** embedded text | `opamp2`, Value = subckt name | a `.subckt … .ends` pasted in a SPICE directive | the model text | only own or permissively licensed text; vendor text only in the private user overlay |

### A.3 Step by step in LTspice 24 (kind B; A and C differ only in steps 3–4)

1. Open `LTspice_Library/opamps/_seat_template.asc` and *Save As* `<stem>.asc`. The template has
   the seat box outline and the five terminal FLAGs. Use a plain ASCII stem with no spaces, for
   example `TL072`. The stem is what goes into `spice_model`.
2. Place the op-amp symbol inside the box, in R0.
3. (B) Set its Value to the subckt name exactly as written on the model file's `.subckt` line.
   Add a SPICE directive `.lib TL072.lib`, and put the file in `LTspice_Library/models/` (or the
   user overlay's `models/`). (A) Nothing to do: the symbol brings its model. (C) Paste the whole
   `.subckt … .ends` block as a SPICE directive.
4. **Check the pin order.** This is the most common SPICE-model mistake. A swapped order usually
   still "simulates" and quietly gives nonsense.
   - `opamp2` nets its pins in the order In+, In−, V+, V−, Out. **[verify]** with `symbols.asc`.
   - Read the model's `.subckt` line and the comment naming its pins. If the order differs, add a
     wrapper directive and set the Value to the wrapper's name:

     ```text
     .subckt TL072_FS inp inn vp vn out
     X1 <the vendor's order, written with inp inn vp vn out> TL072
     .ends
     ```

5. Draw adapter wires from each pin to its terminal point.
   - Every pin must sit on a wire *end*, with no crossings and everything inside the box.
   - Tie extra pins (shutdown, compensation, …) as the datasheet requires, using only the `VCC`,
     `VEE` and `0` labels inside the box.
6. Add the metadata comment, for example
   `;FS: vs_min=9 vs_max=36 source=https://www.ti.com/product/TL072 note=TI PSpice model`, or
   `source=LTspice built-in`.
7. Save. Run the dummy check (the UI table shows the result, or run the dev script) and fix what
   it reports.
8. Run the open-loop harness (A.6).
9. Set `spice_model` for the part, in the op-amp Edit popover or the JSON.

### A.4 What the dummy check enforces

- Exactly one SYMBOL.
- The terminal FLAGs `INP`, `INN`, `OUT` (and `VCC` / `VEE`, if used) sit exactly at the seat
  offsets.
- The symbol and all wires lie inside the box.
- There are no crossing wires, and every pin sits on a wire end.
- The only labels are the terminals plus `VCC`, `VEE` and `0`.
- `.lib` / `.include` targets exist.
- The `;FS:` fields parse.
- Text is ASCII, or can be transliterated.

The InstName is free, because it is renamed on export.

### A.5 Licensing: what may be committed

- **Commit:** FS generic, the seat template, the harness, kind-A dummies, and kind-B dummies whose
  `.lib` points to a file the user downloads (with the source URL in `;FS:`).
- **Never commit:** vendor model files, vendor text pasted into kind-C dummies, or anything
  encrypted.
- The **user overlay** is private. Users put there whatever they are licensed to use.

### A.6 Testing a new dummy

- **Open loop.** The exporter writes `<stem>_openloop.asc` from `tools/opamp_openloop.asc` with
  the dummy in its seat. It uses the classic setup: the DC loop is closed through a very large
  feedback inductor, with a very large capacitor to ground, so the loop is open for AC. A second
  run injects an AC current into the output to read the output resistance.
  - Compare the model's DC gain, unity-gain frequency and output resistance with the library's
    `A_ol`, `GBWP_hz` and `Ro_ohm`.
  - A large mismatch means either the library value or the model is off. That is useful input for
    FS-018.
- **Closed loop.** Export a simple design (2nd-order Butterworth Sallen-Key LP at 1 kHz) with the
  new dummy.
  - `.op` at the chosen Vs must show the outputs near 0 V.
  - In the AC nominal run, the passband must match the FS generic run within a fraction of a dB.
    Stopband differences are the model's own.
- **Supply.** Re-run at `vs_min` and at the 5 V default.

### A.7 How much can be automated

- **Kinds B and C with `opamp2`:** fully generated by `dev/fs008/make_opamp_dummy.py` from the
  name, subckt, model file, vendor pin order and supply range. The adapter wiring is the FS
  generic dummy's, so nothing is drawn.
- **Kind A** with a built-in symbol shaped like `opamp2`: generated the same way, with only the
  symbol name changed, once a quick `-netlist` shows the symbol's pins match. **[verify]** per
  symbol.
- **Hand-drawn:** only unusually shaped symbols, via steps A.3.
- **Not automatable:** choosing parts, obtaining models legally, and reading pin orders and supply
  ranges from datasheets. That stays with the maintainer.
