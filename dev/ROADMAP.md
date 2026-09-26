# FilterSynthesizer — Roadmap

Feature work-item state machine and planning prompt. The binding cross-tier
rules every item must respect live in `docs/CONTRACTS.md`; the tier model and
file map live in `docs/ARCHITECTURE.md`.

Earlier hardware-synthesis items (dispatch gate, 1st-order, HP, notch, BP, MFB,
Ackerberg-Mossberg) have all landed or been dropped; their history is in git
and in the other `dev/*.md` notes.

---

## 1. States

| State | Meaning | Who moves it out |
|---|---|---|
| `PROPOSED` | Idea captured; scope not yet agreed | Maintainer |
| `PLANNED` | Scope, tiers, validation agreed; ready to start | Claude or maintainer |
| `ACTIVE` | Being implemented | Claude |
| `VALIDATING` | Code done; waiting on the maintainer's checks | Maintainer |
| `DONE` | Validated and committed | — (terminal) |
| `BLOCKED` | Cannot proceed; `Blocker:` says why | Whoever clears the blocker |
| `DROPPED` | Abandoned or superseded; `Notes:` says why | — (terminal) |

### Transitions

```
PROPOSED ──agree scope──▶ PLANNED ──start──▶ ACTIVE ──code done──▶ VALIDATING ──checks pass + commit──▶ DONE
    │                        │                 │  ▲                     │
    │                        │                 ▼  │                     └──checks fail──▶ ACTIVE
    │                        │              BLOCKED
    └────────────────────────┴──────────────────────▶ DROPPED   (from any non-terminal state)
```

- `PROPOSED → PLANNED` needs every template field filled, including
  **Validation**, and every `Open questions` entry answered. The maintainer
  approves the plan.
- `PLANNED → ACTIVE` only if all hard `Depends on` items are `DONE`.
- **At most one item `ACTIVE`** at a time.
- `ACTIVE → VALIDATING`: Claude states what it checked itself and prints a
  suggested commit message (git is read-only for Claude — see `CLAUDE.md`).
- `VALIDATING → DONE` only on the maintainer's word that checks passed and the
  commit is made. Claude never marks an item `DONE` on its own.
- `DONE` / `DROPPED` items move to §6 as a one-line entry; their full block is
  deleted from §5 and their row from the board.
- Items may be added, re-scoped or re-prioritized at any time; record the
  reason in `Notes:`.

## 2. Priority levels

| Level | Meaning |
|---|---|
| **P0** | Correctness defect or broken build — wrong results, crash, bundle fails. Preempts everything, including an `ACTIVE` P1–P3 item. |
| **P1** | Next up. Committed for the current release. |
| **P2** | Wanted; scheduled after P1 items. |
| **P3** | Nice to have / exploratory. No commitment. |

Ordering within a level: dependencies first, then smaller diff first.
A priority marked *(suggested)* was proposed by Claude, not set by the
maintainer — confirm it at `PROPOSED → PLANNED`.

## 3. Effort levels

Recommended Claude Code reasoning effort per item (`Effort:` field). Planning
and building can differ — written `plan X / build Y`.

| Effort | Use for |
|---|---|
| **low** | Doc/text edits, renames, moving constants, comment fixes |
| **medium** | UI layout/styling inside one tab; single-module refactor with no math change |
| **high** | Multi-file feature within one or two tiers; new UI flows; file-format writers |
| **xhigh** | Cross-tier features; new approximation math; anything touching solver/cell math or `docs/CONTRACTS.md` |
| **max** | Research-heavy items with open theory or design questions (new topology families, all-pass synthesis) |

General rule: any item spanning more than one tier is **planned** in plan mode
at `xhigh`, even if the build step runs lower. Items touching Tier A/B/C math
must name their numeric check in `Validation:` (this codebase has no test net).

## 4. Planning protocol (for Claude at the start of a session)

1. Read the board (§5). If an item is `ACTIVE`, resume it. If one is
   `VALIDATING`, ask the maintainer for the outcome before starting anything
   else.
2. Otherwise pick the highest-priority `PLANNED` item whose hard dependencies
   are `DONE`; propose it to the maintainer and wait for a go-ahead. If nothing
   is `PLANNED`, offer to plan the top `PROPOSED` item (resolve its open
   questions first).
3. Before editing, re-read the item's `Contracts` in `docs/CONTRACTS.md` and the
   files listed under `Files`; state the tiers touched.
4. Update this file at every state change (`State:` + `Updated:` + the board
   row). Update `docs/CONTRACTS.md` / `docs/ARCHITECTURE.md` in the same unit
   of work if the item changes a contract, module role or data structure. UI
   items also flag User Manual drift (`docs/manual/DOC_WORKFLOW.md`); the PDFs
   themselves are rebuilt by the maintainer.

### Item template

```markdown
### FS-NNN — <short title>
- **State:** PROPOSED | PLANNED | ACTIVE | VALIDATING | BLOCKED
- **Priority:** P0 | P1 | P2 | P3   (append "(suggested)" if Claude proposed it)
- **Effort:** low | medium | high | xhigh | max   (or "plan X / build Y")
- **Tiers:** A | B | C | D
- **Depends on:** FS-NNN (hard) / FS-NNN (soft) | —
- **Contracts:** CONTRACTS §n touched or relied on | —
- **Files:** the files expected to change (keeps the context load small)
- **Goal:** one or two sentences — the user-visible outcome
- **Scope:** what is in; what is explicitly out
- **Validation:** `verify.py` / scratch check / app cases to run, and the
  expected result (baseline comparison where relevant)
- **Open questions:** must be answered before PLANNED
- **Blocker:** (only when BLOCKED)
- **Notes:** decisions, findings
- **Updated:** YYYY-MM-DD
```

IDs are `FS-` + a running three-digit number, never reused. *Hard* dependency
= cannot start before it is `DONE`; *soft* = easier or less rework if done
first.

---

## 5. Items

### Board

| ID | Title | P | State | Effort | Depends on |
|---|---|---|---|---|---|
| FS-001 | Design-control section style (all tabs) | P2 *(s)* | PROPOSED | medium | — |
| FS-002 | Response Plots: phase/GD on main plot, compact sections | P2 *(s)* | PROPOSED | medium | FS-001 (soft) |
| FS-003 | Fold "Roots & Transfer Function" tab into Response Plots | P2 *(s)* | PROPOSED | medium | FS-002 (hard) |
| FS-004 | Biquad Pairing tab: compact layout, rad/s note font | P2 *(s)* | PROPOSED | medium | FS-001 (soft) |
| FS-005 | Op-amp library as a separate module/data file | P2 *(s)* | PROPOSED | medium | — |
| FS-006 | Bessel and equiripple-delay responses | P1 | PROPOSED | xhigh | — |
| FS-007 | Custom filter design (coefficients or poles/zeros) | P1 | PROPOSED | plan xhigh / build high | FS-003 (soft), FS-006 (soft) |
| FS-008 | LTspice export with Monte Carlo presets | P1 | PROPOSED | plan xhigh / build high | FS-005 (hard) |
| FS-009 | Noise analysis in LTspice output | P2 *(s)* | PROPOSED | medium | FS-008 (hard) |
| FS-010 | QSpice compatibility | P3 | PROPOSED | medium | FS-008 (hard) |
| FS-011 | Project save / load | P2 | PROPOSED | high | FS-003, FS-007 (soft) |
| FS-012 | AI integration (external API/MCP or built-in assistant) | P2 | PROPOSED | plan xhigh / build high | FS-011 (soft) |
| FS-013 | All-pass (phase) responses + all-pass cells | P3 | PROPOSED | max | FS-006 (soft) |
| FS-014 | Topology family expansion — research | P3 | PROPOSED | max | — |

*(s)* = suggested priority, awaiting maintainer confirmation.

Suggested order: FS-005 → FS-006 → FS-008 (the P1 track, FS-005 as its small
prerequisite), FS-007 in parallel planning; UI polish FS-001 → FS-002 → FS-003
→ FS-004 can be interleaved as low-risk medium-effort sessions.

---

### FS-001 — Design-control section style (all tabs)
- **State:** PROPOSED
- **Priority:** P2 (suggested)
- **Effort:** medium
- **Tiers:** D
- **Depends on:** —
- **Contracts:** —
- **Files:** `app.py` (CSS block + tab sections), `ui_components.py` (shared helper), `topology_tab.py`, `response_tab.py`
- **Goal:** Every in-tab section whose controls change the design result is visually distinct (colour/border/background) from read-only sections, so the user can see at a glance what affects outputs.
- **Scope:** In — one shared helper (e.g. a styled container context manager) plus its CSS, applied to: Manual Notch Tuning (Response Plots), the "Enable 3rd-Order Sections…" area and Hardware Stage Parameters (Biquad Pairing), and the design-affecting sections of Topology and Resulting Response. Out — the left sidebar (unchanged); layout compaction (FS-002/003/004).
- **Validation:** Visual check of all tabs in light and dark themes; every design-affecting section styled, no read-only section styled; controls still work (change one in each styled section and confirm outputs update).
- **Open questions:** Colour/style preference (accent border, tinted background, or both)? Should the Topology tab's per-section convergence settings count as design-affecting?
- **Notes:** Check the Streamlit bound in `requirements.txt` for keyed-container CSS hooks before choosing the mechanism.
- **Updated:** 2026-09-26

### FS-002 — Response Plots: phase/GD on main plot, compact sections
- **State:** PROPOSED
- **Priority:** P2 (suggested)
- **Effort:** medium
- **Tiers:** D
- **Depends on:** FS-001 (soft — reuse its style for Manual Notch Tuning)
- **Contracts:** —
- **Files:** `app.py` (Response Plots tab; `show_phase`/`show_gd` checkboxes ~L694), `plot_utils.py` (magnitude figure), `hw_plots.py` (reference line styles only)
- **Goal:** The Phase and Group Delay checkboxes overlay their curves on the main magnitude plot instead of opening separate plots.
- **Scope:** In — remove the separate phase/GD plots; keep both checkboxes; draw phase and GD on the main plot with secondary axes; magnitude style unchanged; phase/GD styles copied from the Ideal traces on the "Resulting Response & Schematic" tab; reduce vertical space of Calculated Stopband Edges, Frequency Probes and Manual Notch Tuning without smaller fonts; Manual Notch Tuning gets the design-control style. Out — changes to computed data.
- **Validation:** For LP/HP/BP/BR (one design each): toggle each checkbox alone and both together; curves match the old separate plots numerically (spot-check a few frequencies); axes/legend readable; hover works; section heights visibly reduced, font sizes unchanged.
- **Open questions:** With both phase and GD on, use two right-hand axes, or one shared right axis with GD normalized?
- **Updated:** 2026-09-26

### FS-003 — Fold "Roots & Transfer Function" tab into Response Plots
- **State:** PROPOSED
- **Priority:** P2 (suggested)
- **Effort:** medium
- **Tiers:** D
- **Depends on:** FS-002 (hard — same tab region)
- **Contracts:** —
- **Files:** `app.py` (tab tuple ~L265; Roots & TF tab body ~L980–1140), `docs/ARCHITECTURE.md` (app.py section map), User Manual sources
- **Goal:** Remove the read-only Roots & TF tab; its content appears at the end of Response Plots.
- **Scope:** In — order after existing Response Plots content: (1) Domain Scale radio Normalized/Denormalized; (2) Root Locations — pole and zero tables + System Gain Constant K, not expandable; (3) Pole-Zero Map — expandable; (4) Transfer Function H(s) — one expander containing a radio Expanded (Isolated Gain) / Expanded (Distributed Gain) / Factored (Cascaded Biquads), showing the current content for the chosen form. Compact spacing, fonts unchanged. Out — changes to the math or tables themselves.
- **Validation:** Each former Roots & TF element present and identical in content for one LP and one BR design, in both Domain Scale modes and all three H(s) forms; widget keys unique (no Streamlit duplicate-key errors); remaining tabs still work (index shift).
- **Open questions:** Does the Pole-Zero Map Units radio and "Stretch Real Axis" checkbox stay inside the Pole-Zero Map expander?
- **Notes:** Tab count 5 → 4 — update ARCHITECTURE.md and flag manual drift.
- **Updated:** 2026-09-26

### FS-004 — Biquad Pairing tab: compact layout, rad/s note font
- **State:** PROPOSED
- **Priority:** P2 (suggested)
- **Effort:** medium
- **Tiers:** D
- **Depends on:** FS-001 (soft — styled sections come from it)
- **Contracts:** —
- **Files:** `app.py` (Biquad Pairing tab)
- **Goal:** Tighter vertical spacing between sections; the "Frequencies expressed in rad/s" note slightly larger.
- **Scope:** In — spacing; the note's font size. The "Enable 3rd-Order Sections…" area and Hardware Stage Parameters styling is delivered by FS-001. Out — pairing logic.
- **Validation:** Visual check with an odd-order design (3rd-order sections on and off); all controls still work; stage assignments unchanged vs baseline.
- **Updated:** 2026-09-26

### FS-005 — Op-amp library as a separate module/data file
- **State:** PROPOSED
- **Priority:** P2 (suggested; small prerequisite for FS-008)
- **Effort:** medium
- **Tiers:** C, D
- **Depends on:** —
- **Contracts:** —
- **Files:** new `opamp_library.py` (+ optional data file), `topology_tab.py` (L103), `scoring.py` (L39), `FilterSynthesizer.spec`/`build.bat` if a data file ships next to the exe
- **Goal:** One source of op-amp parameters, editable without touching `topology_tab.py`.
- **Scope:** In — consolidate the two existing tables (`topology_tab.OPAMP_LIBRARY`: UI list incl. Ideal/Custom; `scoring.OPAMP_LIBRARY`: TL072/LM358/OPA1656/NE5532) into one module; optional user-editable JSON/CSV next to the exe (same pattern as `Section_Schematic_Diagrams`). Room for fields FS-008/009 will need (SPICE model name, noise density). Out — adding new parts beyond the merged set.
- **Validation:** Op-amp dropdown lists the same parts; for one design per family, the non-ideal solve and top BOM match the pre-change baseline for 2 op-amps and Custom; `build.bat` bundle loads the library.
- **Open questions:** Python module only, or module + editable data file? Should `scoring`'s TL072/LM358/OPA1656/NE5532 entries appear in the UI dropdown? (Its Ro values use a different magnitude convention — confirm units when merging.)
- **Updated:** 2026-09-26

### FS-006 — Bessel and equiripple-delay responses
- **State:** PROPOSED
- **Priority:** P1
- **Effort:** xhigh
- **Tiers:** A, D
- **Depends on:** —
- **Contracts:** — (all-pole LP prototypes flow through existing pairing and §2 classification unchanged)
- **Files:** `filter_solvers.py` (new prototype solvers), `filter_engine.py`, `app.py` (Response radio L228), `ui_components.py` (spec validation), `plot_utils.py` if delay-specific plot hints are added
- **Goal:** Two new responses in the sidebar: Bessel (maximally flat delay) and equiripple group delay, for LP and HP (and BP/BR via existing transforms).
- **Scope:** In — prototype poles, normalization choice (−3 dB vs delay-normalized), order-selection rule from spec, UI entries, stopband/probe sections degrade gracefully (no ripple/stopband attenuation parameters where irrelevant). Out — hardware stage (unchanged; all-pole sections already realizable).
- **Validation:** Bessel poles for orders 1–10 match published tables to ≥ 5 significant digits; group-delay flatness check vs order; equiripple-delay ripple within the specified bound; end-to-end one LP design through Topology and Resulting Response.
- **Open questions:** Normalization for Bessel (−3 dB at fc, or unit delay)? Spec inputs for equiripple delay (delay ripple %, bandwidth)? Should BP/BR be offered, given LP→BP transforms do not preserve flat delay?
- **Updated:** 2026-09-26

### FS-007 — Custom filter design (coefficients or poles/zeros)
- **State:** PROPOSED
- **Priority:** P1
- **Effort:** plan xhigh / build high
- **Tiers:** A, D
- **Depends on:** FS-003 (soft — tab layout settled), FS-006 (soft — sidebar Response list changes)
- **Contracts:** §2 (classification must accept user-supplied roots), §6 (`engine_results`, Brick, Stage fields)
- **Files:** `filter_engine.py` (new custom entry building `engine_results`), `pairing_utils.py` (generic pairing path), `app.py` + `ui_components.py` (input UI), `tf_utils.py` (coefficient ↔ root conversion)
- **Goal:** The user specifies H(s) directly — numerator/denominator coefficients, or poles/zeros as (f0, Q) pairs plus real roots — and continues through pairing, topology and response exactly as for a synthesized filter.
- **Scope:** In — input modes, validation (stability: LHP poles; conjugate pairing; realizable degree), gain constant handling, an `engine_results` with every key downstream tabs read (or explicit gating of sections that have no meaning, e.g. stopband edges, Manual Notch Tuning). Out — changes to hardware synthesis (Tiers B/C).
- **Validation:** Round-trip: enter the coefficients of a known Butterworth/Elliptic design and get identical poles/zeros, pairing and top BOM; invalid inputs (RHP pole, odd complex count, numerator degree > denominator) rejected with a clear message; every tab renders without exceptions.
- **Open questions:** Where does the input live — new sidebar Response option "Custom", or a separate panel? Coefficients in normalized or denormalized s? Allow RHP zeros (needed later for FS-013 all-pass)?
- **Updated:** 2026-09-26

### FS-008 — LTspice export with Monte Carlo presets
- **State:** PROPOSED
- **Priority:** P1
- **Effort:** plan xhigh / build high
- **Tiers:** D (new writer module)
- **Depends on:** FS-005 (hard — op-amp parameters/model names)
- **Contracts:** §6 (Solution schema is the writer's input)
- **Files:** new `spice_export.py`, `response_tab.py` (download button; MC settings as source of tolerances), `topology_tab.py` (per-section solved values), `hw_plots.py` (MC parameter naming)
- **Goal:** Download an LTspice file of the whole solved cascade that opens and simulates directly, with component tolerances and Monte Carlo run count/distribution pre-set from the tool's MC settings.
- **Scope:** In — netlist per cell family (VCVS/MFB/AM, 1st-order), snapped E-series values, op-amp as a parametrized behavioural/universal model from the library, `.ac` sweep matching the tool's range, MC via tolerance functions + `.step`. Out — noise (FS-009), QSpice (FS-010).
- **Validation:** For one design per family: LTspice AC result overlays the tool's "realized" Bode within plotting tolerance; MC spread comparable to the tool's MC band; file opens with no errors in current LTspice.
- **Open questions:** The draft says "import" — confirmed that this means *export from the tool, opened in LTspice*? Netlist (`.cir`, simpler) or schematic (`.asc`, needs per-cell layout coordinates), or netlist first then `.asc`? Specific op-amp vendor models, or a generic GBW/Aol model?
- **Updated:** 2026-09-26

### FS-009 — Noise analysis in LTspice output
- **State:** PROPOSED
- **Priority:** P2 (suggested)
- **Effort:** medium
- **Tiers:** D
- **Depends on:** FS-008 (hard)
- **Contracts:** —
- **Files:** `spice_export.py`, `opamp_library.py` (noise densities)
- **Goal:** The exported file includes a ready-to-run output-noise analysis.
- **Scope:** In — `.noise` directive, op-amp voltage/current noise parameters, resistor thermal noise (native). Out — noise analysis inside the tool itself.
- **Validation:** Noise run completes; output noise of a simple unity-gain section matches a hand calculation within 1 dB.
- **Open questions:** Also compute noise inside the tool later (separate item), or SPICE-only?
- **Updated:** 2026-09-26

### FS-010 — QSpice compatibility
- **State:** PROPOSED
- **Priority:** P3
- **Effort:** medium
- **Tiers:** D
- **Depends on:** FS-008 (hard)
- **Contracts:** —
- **Files:** `spice_export.py`
- **Goal:** A QSpice-flavoured export alongside LTspice.
- **Scope:** In — dialect differences (MC functions, op-amp model syntax, file format). Out — QSpice-specific features beyond parity with the LTspice export.
- **Validation:** Same AC/MC overlay checks as FS-008, run in QSpice.
- **Updated:** 2026-09-26

### FS-011 — Project save / load
- **State:** PROPOSED
- **Priority:** P2
- **Effort:** high
- **Tiers:** D (+ C for solution serialization)
- **Depends on:** FS-003, FS-007 (soft — the UI key set and spec inputs should settle first to limit format churn)
- **Contracts:** §6 (Solution, Section schemas are what gets saved)
- **Files:** new `project_io.py`, `app.py` (save/load controls; session-state restore), `topology_tab.py` (solved-section results), `response_tab.py` (MC settings)
- **Goal:** Save all inputs, checkboxes and already-solved sections to a file; load it later and continue where you left off without re-solving.
- **Scope:** In — versioned JSON format; sidebar + in-tab controls + per-section solutions + op-amp/series choices; load restores state and skips re-solving; graceful handling of older/newer format versions. Out — symbolic caches (regenerated), UI-only state like expander open/closed.
- **Validation:** Save → restart app → load: every control identical, solved sections show the same BOM without re-solving, Resulting Response and MC match; loading a file with a missing/extra key warns but does not crash; works in the bundled exe.
- **Open questions:** Include the Monte Carlo results themselves, or only settings? File extension?
- **Updated:** 2026-09-26

### FS-012 — AI integration (external API/MCP or built-in assistant)
- **State:** PROPOSED
- **Priority:** P2
- **Effort:** plan xhigh / build high
- **Tiers:** A–D (needs a UI-independent entry point)
- **Depends on:** FS-011 (soft — the project file is the natural interchange format), FS-007 (soft)
- **Contracts:** likely a new one — headless design API
- **Files:** to be determined in planning; at minimum a headless `spec → engine_results → synthesis` entry point outside Streamlit
- **Goal:** Let a chatbot drive the tool from a natural-language prompt, with the model helping choose parameters.
- **Scope:** Decide between (a) exposing the engine as an API/MCP server an external assistant calls, or (b) a built-in assistant panel calling a model API. Out — anything until the variant is chosen.
- **Validation:** To be defined with the chosen variant.
- **Open questions:** Variant (a) or (b), or (a) first? Which model provider/API key handling for (b), especially in the bundled exe?
- **Updated:** 2026-09-26

### FS-013 — All-pass (phase) responses + all-pass cells
- **State:** PROPOSED
- **Priority:** P3
- **Effort:** max
- **Tiers:** A, B, C, D
- **Depends on:** FS-006 (soft — delay-oriented UI and plots)
- **Contracts:** §2 (new family with RHP zeros — the classifier assumes LHP/origin/jω zeros), §4 (new metrics: flat magnitude, group delay), §5 (sign)
- **Files:** `filter_solvers.py`, `filter_engine.py`, `pairing_utils.py`, new `cells_ap*.py` per family, `tf_derivation_v2.py` (registry), `scoring.py`, `topology_tab.py`, `schematic_svg.py`, `Section_Schematic_Diagrams/` (maintainer-drawn)
- **Goal:** Design all-pass (phase-equalizer) responses and realize them with 1st- and 2nd-order all-pass sections in the existing families.
- **Scope:** Research first: target specification (group-delay equalization of an existing design vs standalone phase response), then cells per family. Out — until research concludes.
- **Validation:** |H| flat within tolerance across band; group delay matches target; `verify.py` extended for new cells; TF cache version bumped.
- **Updated:** 2026-09-26

### FS-014 — Topology family expansion — research
- **State:** PROPOSED
- **Priority:** P3
- **Effort:** max
- **Tiers:** B (research output only)
- **Depends on:** —
- **Contracts:** §1 (any resulting family follows the registry pattern)
- **Files:** new `dev/TOPOLOGY_SURVEY.md` (the deliverable); no code
- **Goal:** Decide which additional section topologies are worth adding, given that this solver makes component-calculation complexity irrelevant.
- **Scope:** In — survey candidates (Boctor notches, KHN, Tow-Thomas, and less common single-op-amp designs); judge each on sensitivity, op-amp count, component spread, notch depth, what it adds over VCVS/MFB/AM; shortlist with rationale. Out — implementation (each shortlisted topology becomes its own item).
- **Validation:** Survey reviewed and accepted by the maintainer.
- **Updated:** 2026-09-26

---

## 6. Closed log

One line per item: `FS-NNN — title — DONE|DROPPED YYYY-MM-DD — commit/reason`.

_Empty._
