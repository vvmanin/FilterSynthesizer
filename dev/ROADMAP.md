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
- **At most one item `ACTIVE`** at a time. Exception: an **analysis-only**
  item (or analysis-only stage) — one whose outputs are new files under `dev/`
  and which changes no production code — may be `ACTIVE` alongside it, in its
  own session. Its later build stage follows the one-`ACTIVE` rule.
- `ACTIVE → VALIDATING`: Claude states what it checked itself, then commits and
  pushes the work to its session `claude/*` branch (never `main` — see
  `CLAUDE.md`); the maintainer tests that branch and merges it.
- `VALIDATING → DONE` only on the maintainer's word that checks passed and the
  branch is merged. Claude never marks an item `DONE` on its own.
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
| **P4** | Parked: lowest priority, kept for later. Revisit only when nothing above it is open. |

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
   items do NOT edit the User Manual / Quick Start: they add their undocumented
   UI or behaviour changes to §7. The manuals are updated in batches
   (`docs/manual/DOC_WORKFLOW.md`), which clears the §7 entries they cover; the
   PDFs themselves are rebuilt by the maintainer.

### Item template

```markdown
### FS-NNN — <short title>
- **State:** PROPOSED | PLANNED | ACTIVE | VALIDATING | BLOCKED
- **Priority:** P0 | P1 | P2 | P3 | P4   (append "(suggested)" if Claude proposed it)
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
| FS-009 | Noise analysis in LTspice output | P2 *(s)* | PROPOSED | medium | FS-008 (hard, done) |
| FS-010 | QSpice compatibility | P3 | PROPOSED | medium | FS-008 (hard, done) |
| FS-011 | Project save / load | P2 | PROPOSED | high | FS-003 (done), FS-007 (soft, done) |
| FS-012 | AI integration (external API/MCP or built-in assistant) | P2 | PROPOSED | plan xhigh / build high | FS-011 (soft) |
| FS-013 | All-pass (phase) responses + all-pass cells | P3 | PROPOSED | max | FS-006 (soft) |
| FS-014 | Topology family expansion — research | P3 | PROPOSED | max | — |
| FS-017 | Manual pairing override: unreliable clicks | P2 *(s)* | PROPOSED | medium | — |
| FS-018 | Op-amp data: provenance field + review of shipped parts | P2 *(s)* | PROPOSED | medium | — |
| FS-019 | Wider op-amp library: first batch + standing process | P3 *(s)* | PROPOSED | low | FS-018 (hard) |
| FS-020 | Equiripple phase-error (Zverev linear-phase) response | P3 *(s)* | PROPOSED | plan xhigh / build high | FS-006 (hard) |
| FS-022 | PDF report: group-delay axis blown up by notch spikes | P2 *(s)* | PROPOSED | medium | — |
| FS-023 | PDF report: group-delay detail plot for delay responses | P3 *(s)* | PROPOSED | medium | FS-006 (hard), FS-022 (soft) |
| FS-024 | Step and impulse response plots (design vs realized) | P3 *(s)* | PROPOSED | high | — |
| FS-025 | Gaussian and other non-overshooting responses | P3 *(s)* | PROPOSED | plan xhigh / build high | FS-006 (hard), FS-024 (soft) |
| FS-026 | LTspice transient export (step; impulse from the step) | P2 *(s)* | PROPOSED | high | FS-008 (hard, done), FS-024 (hard) |
| FS-027 | Realized response with inter-stage loading (feasibility first) | P3 *(s)* | PROPOSED | plan high / build high | FS-008 (hard, done) |
| FS-030 | Topology tab: section spec resets to default when a solve finishes | P2 *(s)* | PROPOSED | plan high / build medium | — |
| FS-031 | Group-delay equalizer: all-pass stages appended to a designed filter | P3 *(s)* | PROPOSED | plan xhigh / build high | FS-013 (hard, realization stage only), FS-006 (done) |
| FS-032 | Magnitude correction of an existing system from measured Bode points | P3 *(s)* | PROPOSED | plan max / build xhigh (per stage) | FS-007 (done); FS-014 (hard, realization stage only); FS-031 (soft) |
| FS-034 | SPICE row checker: simulate a section's top BOM rows with vendor models | P4 | PROPOSED | plan high / build high | FS-008 (done), FS-029 (done) |
| FS-037 | Custom capacitor value list ("custom C row") for snapping | P1 | PROPOSED | plan high / build medium | — |
| FS-038 | Monte Carlo: optional op-amp parameter spread | P1 | PROPOSED | medium | FS-018 (soft) |

*(s)* = suggested priority, awaiting maintainer confirmation.

P1 user requests (2026-10-05, Christopher Paul): FS-037 and FS-038 touch
disjoint files (snapper / Topology vs `hw_plots` Monte Carlo / Response tab)
and can go in either order; FS-037 first if the cache-signature change should
settle before other Topology work.

Suggested order: the earlier P1 track (FS-005, FS-006, FS-008, FS-029) is done; FS-007 is done (built before FS-016, whose gaps it gates). The UI polish
track FS-001 → FS-004 is done.

Non-urgent follow-ups added 2026-09-27, ranked by implementation convenience
(easiest first): FS-022 (a report-data fix in one place) → FS-023 (one more
optional report plot, reuses FS-006's detail plot) → FS-024 (new time-domain
computation for ideal and realized, UI + report) → FS-025 (new approximation
families; research first).

Defect items FS-017/030 have no hard dependencies and block nothing; slot
them between feature items.

Op-amp data items: FS-018 before FS-009 (it defines how the noise fields are
sourced) and before FS-019 (additions follow its curation rule).

SPICE track: FS-008 (AC + Monte Carlo) and FS-029 (vendor model import) done; FS-026 (transient) after both
FS-008 and FS-024; FS-009 / FS-010 plug into FS-008's bundle builder and IR;
FS-027 (loaded realized response) reuses FS-008's IR and MNA.

Correction track (added 2026-09-30): FS-031 (group-delay equalizer) and FS-032
(magnitude correction from measured data) both run analysis first and can plan
in parallel (§1 analysis-only exception). Their ideal stages (math + plots)
need no new cells; their hardware stages wait on FS-013 (all-pass cells) and
FS-014 (general LHP zeros) respectively. FS-032's joint phase question is
answered against FS-031's equalizer — plan FS-031 first.

---

### FS-009 — Noise analysis in LTspice output
- **State:** PROPOSED
- **Priority:** P2 (suggested)
- **Effort:** medium
- **Tiers:** D
- **Depends on:** FS-008 (hard, done)
- **Contracts:** —
- **Files:** `spice_export.py`, `opamp_library.py` (noise densities)
- **Goal:** The exported file includes a ready-to-run output-noise analysis.
- **Scope:** In — `.noise` directive, op-amp voltage/current noise parameters, resistor thermal noise (native). Out — noise analysis inside the tool itself.
- **Validation:** Noise run completes; output noise of a simple unity-gain section matches a hand calculation within 1 dB.
- **Open questions:** Also compute noise inside the tool later (separate item), or SPICE-only?
- **Notes:** With FS-008's op-amp dummies the op-amp noise comes from the chosen SPICE model; only the FS generic subckt needs noise sources from `en_nV_rtHz` / `in_pA_rtHz` (values sourced per FS-018). A `.noise` file is one more entry in FS-008's bundle builder (design note §11.4, §16).
- **Updated:** 2026-09-28

### FS-010 — QSpice compatibility
- **State:** PROPOSED
- **Priority:** P3
- **Effort:** medium
- **Tiers:** D
- **Depends on:** FS-008 (hard, done)
- **Contracts:** —
- **Files:** `spice_export.py`
- **Goal:** A QSpice-flavoured export alongside LTspice.
- **Scope:** In — dialect differences (MC functions, op-amp model syntax, file format). Out — QSpice-specific features beyond parity with the LTspice export.
- **Validation:** Same AC/MC overlay checks as FS-008, run in QSpice.
- **Notes:** FS-008's `.asc` template machinery is LTspice-specific (QSpice's schematic format differs); the cheap route is netlist-first from FS-008's IR (`spice_cells.py`) with QSpice's MC functions (design note §16).
- **Updated:** 2026-09-28

### FS-011 — Project save / load
- **State:** PROPOSED
- **Priority:** P2
- **Effort:** high
- **Tiers:** D (+ C for solution serialization)
- **Depends on:** FS-003 (done), FS-007 (soft — the UI key set and spec inputs should settle first to limit format churn)
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
- **Notes:**
  - New all-pass cells follow FS-008's rule for new cells: IR entry in `spice_cells.py`, then an LTspice template or flagged as auto-layout.
  - FS-031 (2026-09-30) takes the "group-delay equalization of an existing design" use case (equalizer design math, UI, plots); this item keeps the all-pass family itself — classification, cells, dispatch, scoring, schematics — and FS-031's realization stage waits on it.
  - From FS-007 (maintainer, 2026-09-28): Custom H(s) complete mode takes its type **only** from `custom_tf.detect_type` and refuses a shape it does not recognise. An all-pass (|H| flat, |H(0)| = |H(∞)| = peak, no dip) is detected as "other" today and is also stopped earlier by the RHP-zero gate. When all-pass lands, extend `detect_type` with an all-pass class (and the Custom gate rows named in the FS-007 design note §12) — do not change the detector before then.
- **Updated:** 2026-09-28

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
- **Notes:** Each topology this survey leads to must pass FS-008's rule for new cells (LTspice IR entry, then a template in `LTspice_Library/cells/` or flagged as auto-layout until one ships).
- **Updated:** 2026-09-29

### FS-017 — Manual pairing override: unreliable clicks
- **State:** PROPOSED
- **Priority:** P2 (suggested — not critical)
- **Effort:** medium
- **Tiers:** D
- **Depends on:** —
- **Contracts:** —
- **Files:** `app.py` (Biquad Pairing mnemoscheme, `pz_mnemo_chart` `on_select` handling ~L1150–1300), `plot_utils.py` (mnemoscheme figure) if marker/selection settings are involved
- **Goal:** A single click on a pole or zero reliably registers in manual pairing mode.
- **Scope:** In — reproduce, find the cause, fix the click/selection handling. Out — pairing algorithm (FS-016); tab layout (FS-004).
- **Validation:** Build a full manual pairing on an 8th-order BP and a 6th-order BR: every click registers once; re-clicking the same element, clicking after a rerun, and undo/reset behave; auto-pair still works when manual mode is off.
- **Open questions:** Does it fail on the first click after entering manual mode, on re-clicking the same element, or randomly?
- **Notes:** Likely suspects to check first: Plotly selection state persisting across reruns (clicking an already-selected point emits no new event), and `manual_routing_active` being reset on the rerun the click triggers.
- **Updated:** 2026-09-26

### FS-018 — Op-amp data: provenance field + review of shipped parts
- **State:** PROPOSED
- **Priority:** P2 (suggested — P1 if a shipped value is known to be wrong)
- **Effort:** medium
- **Tiers:** C (data the non-ideal solver consumes), D (Edit popover)
- **Depends on:** —
- **Contracts:** —
- **Files:** `opamp_library.json`, `opamp_library.py` (`OPTIONAL` fields, `_readme`), `topology_tab.py` (`_opamp_part_editor`: show/edit the new fields), `docs/ARCHITECTURE.md`
- **Goal:** Every shipped op-amp value is traceable to a datasheet and its conditions; doubtful values are corrected or visibly marked as estimates.
- **Scope:** In — (1) schema: `source` (datasheet, revision, conditions: supply, load, typ/min) and `estimated` (list of fields not taken from the datasheet); (2) curation rule, written into the JSON `_readme` and ARCHITECTURE: which figure to use (typ vs min), how Ro is obtained when the datasheet has no open-loop output resistance (from the open-loop Zout plot, else marked estimated), naming (one entry per die; channel variants in one name, e.g. `AD8505 / AD8506 / AD8508`); (3) review A_ol, GBWP, Ro of all 8 shipped parts against their datasheets; (4) fill `en_nV_rtHz` / `in_pA_rtHz` / `spice_model` from the same datasheets when given (cheap while open; FS-008/009 need them). Out — new parts (FS-019); any change to the op-amp model itself (single-pole A_ol/GBWP + Ro).
- **Validation:** Every shipped entry has `source`; maintainer spot-checks each value against the cited datasheet; app loads with no `load_errors()`; scratch check that `source`/`estimated` survive a UI edit round-trip (`save_user` keeps optional fields). For each part whose values changed: one design re-solved, BOM change recorded vs the pre-change baseline; unchanged parts give an identical `_job_sig` (baseline unchanged).
- **Open questions:** Which parameter(s) does the maintainer already distrust (seeds the review)? Library convention: typical or worst-case (min A_ol, min GBWP) — or both as separate fields? Default supply condition to record when a datasheet gives several? Add a `category` field now (audio, precision, micropower, …) so a larger dropdown can be grouped later without re-editing entries? Show `source` in the picker caption, or only in the Edit popover?
- **Notes:** Known weak spots: open-loop Ro is rarely tabulated and none of the current values (20–1200 Ω) records its origin; the four parts from the old `scoring.py` table (TL072, LM358, OPA1656, NE5532) were rough demo figures. These are reasons to review, not confirmed errors.
- **Updated:** 2026-09-27

### FS-019 — Wider op-amp library: first batch + standing process
- **State:** PROPOSED
- **Priority:** P3 (suggested)
- **Effort:** low
- **Tiers:** C (data only)
- **Depends on:** FS-018 (hard — curation rule and `source` field first)
- **Contracts:** —
- **Files:** `opamp_library.json`; this file (standing-process paragraph)
- **Goal:** The shipped library covers the common op-amp classes, and later additions continue as routine data commits without an open-ended roadmap item.
- **Scope:** In — one batch of parts chosen by the maintainer (≤ 10), each following the FS-018 rule; a short "standing process" paragraph in this file: later additions are plain data commits (`data(opamp): add …`) with no roadmap item, unless they need code or a schema change. Out — code changes; vendor SPICE model files.
- **Validation:** App loads with zero `load_errors()` (also catches name clashes); dropdown lists the new parts; every new entry has `source`; one section solved with one new part.
- **Open questions:** Which parts or classes go in the first batch (e.g. low-noise audio, precision/zero-drift, micropower, rail-to-rail CMOS, high-speed)? Does the dropdown need grouping or a filter once it passes ~30 entries (Streamlit's selectbox already filters by typing; grouping would be a separate UI item)?
- **Notes:** Growing the library is continuous, and the state machine needs a terminal `DONE` — hence one closeable batch plus a standing rule rather than an item that stays `ACTIVE`.
- **Updated:** 2026-09-27

### FS-020 — Equiripple phase-error (Zverev linear-phase) response
- **State:** PROPOSED
- **Priority:** P3 (suggested)
- **Effort:** plan xhigh / build high
- **Tiers:** A, D
- **Depends on:** FS-006 (hard — reuses its Remez/Newton machinery, sidebar and delay plots)
- **Contracts:** —
- **Files:** `delay_solvers.py` (new residual: phase error φ(ω) + ωτ instead of delay ripple), `app.py` / `ui_components.py` (Response entry, spec in degrees), `dev/fs006/` (offline seed generation)
- **Goal:** The classic "linear phase with equiripple error" family (Zverev; Williams & Taylor 0.05° / 0.5° tables), for users who expect those published tables rather than FS-006's equiripple-delay family.
- **Scope:** In — pole family minimizing max|φ(ω) + ωτ| (alternation points in (0, ω_p]), seeds per order, validation against the published tables. Out — anything FS-006 already covers.
- **Validation:** Poles match the Zverev / Williams & Taylor 0.05° and 0.5° tables to their printed digits; phase error equiripple within the bound on a dense grid.
- **Open questions:** Is there user demand beyond FS-006's equiripple-delay family? Which error values to offer (only 0.05° / 0.5°, or continuous)?
- **Notes:** Split out of FS-006 (design note §3.6). A related idea recorded there, not itemized: non-minimum-phase magnitude equalization with real zero pairs ±σ (flat delay + flatter magnitude), which needs summing/feed-forward sections the hardware stage does not have (note §5.2).
- **Updated:** 2026-09-27

### FS-022 — PDF report: group-delay axis blown up by notch spikes
- **State:** PROPOSED
- **Priority:** P2 (suggested — a defect in the output, not urgent)
- **Effort:** medium
- **Tiers:** D
- **Depends on:** —
- **Contracts:** —
- **Files:** `report_ui.py` (build `ideal_gd` / `realized_gd` for the report), maybe `response_tab.py` (share the computation), `report_pdf.py` (`_gd_ms` fallback, ~L510–606)
- **Goal:** The report's Bode page scales its group-delay axis like the Resulting Response tab: realized-GD peaks at non-ideal notches (phase error near the notch) are clipped, and the passband delay stays readable.
- **Scope:** In — pass analytic ideal and realized GD to the report in every case; keep the existing clip window (ideal min/max + realized 3–97 % bulk, ±20 %). Out — changes to the interactive plot.
- **Validation:** Inverse Chebyshev LP n = 6 with realized notches: report GD axis spans the passband delay (not the notch spikes), same window as the tab's plot; a design without notches unchanged.
- **Open questions:** none expected once the cause is confirmed.
- **Notes:** Likely cause (read, not yet reproduced): `report_pdf.py` L583–601 already copies the tab's clipping, but anchors it on the IDEAL curve. The analytic `ideal_gd` / `realized_gd` arrays reach the report only when the Resulting Response tab's "Group delay" checkbox is on (`response_tab.py` L519–535 → `report_ui.py` L114/198). Otherwise `_gd_ms` differentiates the phase, which spikes at the π phase jump of every jω zero even for the ideal response, and the window is anchored on that spiky curve.
- **Updated:** 2026-09-27

### FS-023 — PDF report: group-delay detail plot for delay responses
- **State:** PROPOSED
- **Priority:** P3 (suggested)
- **Effort:** medium
- **Tiers:** D
- **Depends on:** FS-006 (hard), FS-022 (soft — same report GD data)
- **Contracts:** —
- **Files:** `report_ui.py` (checkbox next to "Passband detail plot"), `report_pdf.py` (matplotlib version of the detail plot), `app.py` (publish `delay_info` + tolerance to the report snapshot)
- **Goal:** For Bessel / Equiripple Delay designs, a report option "Group-delay passband detail" adds the FS-006 Group Delay Detail (zoomed τ(f), τ_nom line, ±δ or −ε band, flat-band edges) to the PDF, design vs realized.
- **Scope:** In — checkbox shown only for delay responses; ideal and realized curves; same zoom rule as the Tab 1 plot. Out — the interactive plot.
- **Validation:** Bessel LP n = 4 and Equiripple ±1 % LP n = 4 reports show the zoomed delay with the tolerance band; the realized curve stays inside it for a clean BOM; unchecked → report unchanged.
- **Open questions:** Realized curve too (needs the realized section TFs, as FS-022), or design only?
- **Updated:** 2026-09-27

### FS-024 — Step and impulse response plots (design vs realized)
- **State:** PROPOSED
- **Priority:** P3 (suggested)
- **Effort:** high
- **Tiers:** D (A helper for the ideal response)
- **Depends on:** —
- **Contracts:** —
- **Files:** `plot_utils.py` / `hw_plots.py` (time-domain figures), `app.py` (Tab 1, ideal), `response_tab.py` (realized, optionally with Monte-Carlo spread), `report_pdf.py` + `report_ui.py` (optional report page)
- **Goal:** Step and impulse responses of the ideal design and of the realized circuit, with overshoot, rise time (10–90 %), settling time and delay read out — the time-domain view that makes Bessel / Equiripple Delay (and FS-025) worth choosing.
- **Scope:** In — ideal from poles/zeros/k (partial fractions or `scipy.signal.step/impulse` on the ZPK; time span from the slowest pole); realized from the section TFs with op-amp model (same source as the realized Bode); metrics table; LP/HP/BP/BR (BP/BR: envelope or plain waveform — to decide). Out — transient simulation of non-linear effects (slew, clipping).
- **Validation:** Bessel LP n = 4: overshoot 0.84 %, rise ≈ 0.34/f_c (design note §2.3); Butterworth n = 4: 10.8 %; realized curve matches ideal within the Bode match for a clean BOM.
- **Open questions:** Where in the UI (Tab 1 section vs Resulting Response tab vs both)? Monte-Carlo envelope in time domain too?
- **Notes:** FS-026 (LTspice transient export) takes its window and presets from here: expose a Streamlit-free helper returning, for the realized cascade, `{t_stop, dt_max, t_settle, peak_per_unit_step, max_slope_per_unit_step, h_inf}`; the impulse's Dirac term H(∞)·δ(t) for HP/BR/notch is shown as its weight, not as a spike (FS-008 design note §11).
- **Updated:** 2026-09-28

### FS-025 — Gaussian and other non-overshooting responses
- **State:** PROPOSED
- **Priority:** P3 (suggested)
- **Effort:** plan xhigh / build high
- **Tiers:** A, D
- **Depends on:** FS-006 (hard — reuses the delay-response UI and order selection), FS-024 (soft — step response to show the benefit)
- **Contracts:** —
- **Files:** `delay_solvers.py` or a new sibling module, `ui_components.py`, `app.py`, `dev/` research note
- **Goal:** More responses aimed at clean transients: Gaussian (truncated-Taylor approximation of exp(−ω²)), transitional Gaussian-to-6 dB / 12 dB, and other candidates with zero or near-zero step overshoot (e.g. critically damped / all-real-pole, Legendre-Papoulis as a steeper low-overshoot option).
- **Scope:** Phase 1 — research note: definitions, pole computation, overshoot/rise/selectivity table per order vs Bessel, which ones earn a place. Phase 2 — implement the chosen ones as LP (+ translation BP if meaningful) through the FS-006 machinery. Out — highpass/band-reject (same reasoning as FS-006).
- **Validation:** Poles vs published tables (Zverev; Williams & Taylor); step overshoot per order (with FS-024); −α at f_c.
- **Open questions:** Which families? Is "non-overshooting" strict (0 %) or "low" (< 1 %)?
- **Notes:** Relation: FS-020 (equiripple phase error) is another linear-phase family — plan them together.
- **Updated:** 2026-09-27

### FS-026 — LTspice transient export (step; impulse from the step)
- **State:** PROPOSED
- **Priority:** P2 (suggested)
- **Effort:** high
- **Tiers:** D
- **Depends on:** FS-008 (hard, done — writer, bundle builder, hooks), FS-024 (hard — time window and step metrics)
- **Contracts:** —
- **Files:** `spice_export.py` (`tran` directives, step source, impulse probe node), `spice_ui.py` (transient options), README template
- **Goal:** The LTspice bundle gains a nominal step-response file whose window and amplitudes come from the tool's own time-domain evaluation, with the impulse response derived from the step.
- **Scope:** In — `<spec>_TRAN.asc` (whole cascade, same drawing as FS-008), `PULSE` step with `.step param Astep list <small> <large>` (small = linear, comparable with FS-024; large ≈ 70–80 % of the output swing → slew/clipping/recovery with real models), plot normalized to `V(out)/Astep`, impulse node `ddt(V(out))/Astep` (B-source), `.tran` window/dtmax from FS-024, `.options plotwinsize=0`. Out — Monte Carlo in the time domain (maintainer decision), narrow-pulse impulse files, step MC.
- **Validation:** With FS generic dummies the small-amplitude step matches FS-024's realized step (Bessel LP n = 4: overshoot ≈ 0.84 %; Butterworth n = 4: ≈ 10.8 %) and the impulse node matches FS-024's impulse outside the δ spike; large-amplitude run with a real-model dummy stays within the rails at the chosen Vs; file opens and runs in LTspice 24.
- **Open questions:** Rule for the large amplitude (fraction of swing, or user-set)? Add a slew-rate field to the op-amp library (FS-018) for a slew check?
- **Notes:** Reasoning (impulse with real op-amps, practice, why the derivative of a small-signal step): `dev/FS-008_ltspice_export_design_note.md` §11. Base prepared in FS-008 v1 (§11.4).
- **Updated:** 2026-09-28

### FS-027 — Realized response with inter-stage loading (feasibility first)
- **State:** PROPOSED
- **Priority:** P3 (suggested)
- **Effort:** plan high / build high
- **Tiers:** D (C-adjacent: the realized-response evaluation, not the solvers)
- **Depends on:** FS-008 (hard, done — reuses its netlist IR and `mna_ac`)
- **Contracts:** — (solvers and scoring stay per-section and unloaded)
- **Files:** `spice_cells.py` (two-port / cascade evaluation), `hw_plots.py` (`monte_carlo`, `cascade`), `response_tab.py` (realized curve, group delay), `report_pdf.py` / `report_ui.py` (same curves), `docs/ARCHITECTURE.md`
- **Goal:** The Resulting Response tab (and report) shows the realized cascade *with* inter-stage loading — each section's op-amp output impedance driving the next section's input — so it matches the LTspice export instead of an unloaded product.
- **Scope:** Step 1 — feasibility prototype and decision: per-section ABCD parameters from the IR (two MNA solves per frequency: output open / output shorted), chain-multiplied; nominal and 2000-run MC timing vs today; group-delay approach (numeric derivative or analytic via the MNA). Step 2 (only if step 1 says go) — switch the realized curve, MC and report to it, keeping the unloaded product available for comparison. Out — loading-aware synthesis or scoring.
- **Validation:** Loaded curve equals the LTspice FS-generic AC result (FS-008) ≤ 0.01 dB; with ideal buffers it equals today's product ≤ 1e-9; MC runtime for 2000 runs within ~2× today's.
- **Open questions:** Keep a toggle (loaded / unloaded) or replace? Does the HF-rise warning logic move to the loaded curve?
- **Notes:** Found while planning FS-008 (design note §10, §16 with the feasibility sketch). Maintainer: open the item; build decision after the feasibility step.
- **Updated:** 2026-09-28

---

### FS-031 — Group-delay equalizer: all-pass stages appended to a designed filter
- **State:** PROPOSED
- **Priority:** P3 (suggested)
- **Effort:** plan xhigh / build high
- **Tiers:** A (equalizer design), D (UI, plots, report); B/C only through FS-013 in Stage 3
- **Depends on:** FS-006 (done — group-delay plots and delay-oriented UI); FS-013 (hard, Stage 3 only — all-pass cells); FS-024 (soft — step response shows the payoff)
- **Contracts:** §2 (all-pass sections carry RHP zeros — classifier and producer rule), §6 (`engine_results` gains the equalizer stages or a separate block — decide in Stage 1)
- **Files:** Stage 1 — new `dev/FS-031_gd_equalizer_design_note.md` (+ `dev/fs031/` scripts). Stage 2 (expected) — new `gd_equalizer.py`, `app.py` / Response Plots (equalizer controls, overall GD), `hw_plots.py`/`plot_utils.py`, `report_pdf.py`. Stage 3 — per FS-013.
- **Goal:** For a designed filter, the user asks for phase correction over a chosen band, and the tool adds 1st/2nd-order all-pass sections whose combined group delay flattens the total (filter + equalizer) group delay within a stated ripple, with the magnitude unchanged.
- **Scope:**
  - Stage 1 (research/plan, analysis-only, no production code): problem statement (band, target = flat GD at the minimum achievable constant delay, error norm — equiripple vs least squares); order selection (sections vs residual ripple trade-off); optimiser (seeds from the GD-peak locations, constrained on pole Q/ω₀ ranges; Remez-like vs LM); where the equalizer lives in the data model and in pairing; interaction with sensitivity (high-Q all-pass sections); prototype on 4–6 designs (Butterworth/Chebyshev/elliptic LP, one BP) with results tables.
  - Stage 2 (build, ideal): equalizer design + UI + plots/report of filter, equalizer and total GD; the equalizer's poles/zeros visible in Roots; no hardware yet (sections shown as awaiting FS-013).
  - Stage 3 (build, hardware): pair and realize the all-pass sections through FS-013's cells; overall realized GD plot.
  - Out — correcting measured/external systems (FS-032); standalone all-pass responses (FS-013); magnitude change of any kind.
- **Validation:** Stage 1 — prototype tables: GD ripple vs number of sections for each test design, against published equalizer tables where available. Stage 2 — |H_total| equals |H_filter| to numerical precision; total GD ripple within the requested tolerance; filter-only results (BOMs, plots) unchanged when the equalizer is off. Stage 3 — realized total GD matches the ideal within tolerance; `verify.py` per FS-013.
- **Open questions:** Band default (passband edge(s)) and tolerance input — absolute (µs) or relative (%)? Max number of equalizer sections the UI offers? For BP/BR: equalize the passband(s) only? Equalizer sections placed after the filter only, or interleaved for dynamic range?
- **Notes:** Stage 1 may be `ACTIVE` alongside a build item (§1 analysis-only exception). Stage 2 is useful before FS-013 lands (the ideal answer tells how many all-pass sections a design needs).
- **Updated:** 2026-09-30

### FS-032 — Magnitude correction of an existing system from measured Bode points
- **State:** PROPOSED
- **Priority:** P3 (suggested)
- **Effort:** plan max / build xhigh (Stage 2), high (Stages 3–4)
- **Tiers:** A (fitting + correction solver), D (new design mode, data table, plots, report); B/C only through FS-014 in Stage 3
- **Depends on:** FS-007 (done — Custom H(s) pipeline and producer gate the correction reuses); FS-014 (hard, Stage 3 only — cells for general LHP complex zeros); FS-031 (soft — Stage 1's joint-phase answer builds on it; hard for Stage 4 if phase is corrected by a separate equalizer); FS-011 (soft — saving the measured table)
- **Contracts:** §2 producer rule (a correction filter needs zeros off the jω axis and off the origin, which today's gate refuses — relaxed only together with FS-014), §6 (new `engine_results` producer; `custom_info`-like block for the correction)
- **Files:** Stage 1 — new `dev/FS-032_magnitude_correction_design_note.md` (+ `dev/fs032/` prototype scripts and sample data). Stage 2 (expected) — new `mag_correction.py`, `app.py` (new design mode "Correct existing Bode", data table + target editor), `custom_tf.py` (reuse gate/snap), `plot_utils.py`, `report_pdf.py`.
- **Goal:** The user enters measured Bode points of an existing system (frequency, magnitude, optionally phase), specifies the desired output over a passband (flat by default, or a user-defined curve), and the tool finds pole/zero locations of a correction filter so that system × correction meets that target; the correction then continues through pairing and topology like any other design.
- **Scope:**
  - Stage 1 (research/plan, analysis-only): data input (table, CSV paste; units dB/V/V, deg; sparse and noisy data; interpolation); whether to fit a rational model of the system first (vector fitting) or optimise directly on the data; the correction problem (log-magnitude error over the band, weights, out-of-band behaviour — gain limits, roll-off; minimum-phase correction; order selection; stability — LHP poles); realizability (which zero patterns today's families take, what needs FS-014); **joint magnitude + phase**: a minimum-phase correction fixes the phase once the magnitude is chosen, so independent phase correction needs an all-pass part — decide whether a joint solve is worth it or phase is corrected afterwards by FS-031 on the corrected system; prototype on 3–4 synthetic systems (known H(s) with noise) + one real measured set if the maintainer has one.
  - Stage 2 (build, ideal): new design mode, data table, target editor, fitting + correction solver, plots of measured / correction / corrected response, output as `engine_results` for the corrections today's families can realize (others refused with a reason, per the producer rule).
  - Stage 3 (build, hardware): general-zero corrections realized once FS-014 provides the cells; producer gate relaxed with it.
  - Stage 4 (optional, per Stage 1): phase correction of the corrected system — joint solve, or FS-031's equalizer driven from the measured phase.
  - Out — time-domain / impulse-response measurements; automatic measurement import from instruments; digital (FIR/IIR) correction.
- **Validation:** Stage 1 — synthetic cases recover a known inverse within stated error and degrade gracefully with noise; results tables in the design note. Stage 2 — corrected magnitude within the user tolerance over the passband on the synthetic set; out-of-band gain within limits; the correction's poles/zeros pass the producer gate or are refused with a reason; existing modes unchanged. Stage 3 — realized corrected response matches the ideal (as FS-008's overlay checks).
- **Open questions:** Typical systems to correct (sensor, loudspeaker, transducer, cable, an existing analog stage)? Frequency span and point count to expect? Is phase data usually available? Target beyond "flat" — slope, custom curve? Correction order limit? Separate design mode or a Custom H(s) sub-mode?
- **Notes:** Largest-scope item on the board — may split further after Stage 1 (IDs then). Stage 1 may be `ACTIVE` alongside a build item (§1 analysis-only exception). Stage 1 at max effort; consider `/code-review ultra` before committing Stage 2.
- **Updated:** 2026-09-30

---

### FS-034 — SPICE row checker: simulate a section's top BOM rows with vendor models
- **State:** PROPOSED
- **Priority:** P4 (maintainer, 2026-10-01)
- **Effort:** plan high / build high (feasibility first)
- **Tiers:** D (UI, process runner), B (netlists, reused from `spice_cells` / `spice_export`)
- **Depends on:** FS-008 (done), FS-029 (done)
- **Contracts:** — (export path unchanged)
- **Files:** new runner module (simulator discovery, batch run, `.raw` / `.meas` parsing), `spice_export.py` (per-row netlists), `topology_tab.py` (action + column), `spice_ui.py` / `opamp_library.py` (model selection); bundle: `build.bat` / PyInstaller spec if a simulator is shipped
- **Goal:** In a section's BOM table, a "Verify top N rows in SPICE" action runs each row with the section's real op-amp model and shows what the in-tool model misses — above all the HF hump / stopband return of Sallen-Key cells with high feedback gain (worst for HPF) — as an overlay and an "HF excess (SPICE)" column. It informs the user's row choice; it never picks.
- **Scope:** In — feasibility: find LTspice and run it in batch mode (`-b`), or ship ngspice; vendor-model compatibility (PSpice-encrypted models fail in ngspice); `.raw` parsing; running a subprocess from the frozen exe; time per row (target 0.3–1 s, N = 5–10). Then the action, the column and the overlay. Out — SPICE inside the optimizer loop (too slow), automatic row choice or gain changes (maintainer, 2026-10-01: row choice and gain tuning stay manual), transient / noise (FS-026 / FS-009).
- **Validation:** For 3–4 sections (VCVS HP with high gain, MFB, AM, a notch), the checker's AC curve matches an LTspice run of the FS-008 export of the same row within 0.1 dB; the HF excess column flags the known hump case; the bundled exe runs it.
- **Open questions:** LTspice-only (user installs it) or bundle ngspice? Which rows by default (top N by the current sort, or the user's selection)? Keep results per row in the session only?
- **Notes:** Came out of the FS-028 S2-4 planning (2026-10-01): the in-tool op-amp model is a single pole plus a constant Ro. It captures the Ro feed-through (`cells_lp.py`), but not a second pole or the rising Zout that drive the hump on real parts. A cheaper in-tool step, an "HF excess (dB)" column from the existing model (`response_tab._hf_hump` metric per row), can come first and needs no simulator.
- **Updated:** 2026-10-01

### FS-030 — Topology tab: section spec resets to default when a solve finishes
- **State:** PROPOSED
- **Priority:** P2 (suggested — user-visible state loss, but no data loss: restoring the spec by hand brings the solution back from the result cache)
- **Effort:** plan high / build medium
- **Tiers:** D
- **Depends on:** —
- **Contracts:** —
- **Files:** `topology_tab.py`: session-state init (~L158), job submit / `_drain_finished` (~L220-335), per-section spec widgets and their `setdefault` defaults (~L1450-1690), the `run_every` BOM fragment and its `st.rerun(scope="app")` (~L2146-2194)
- **Goal:** A section's spec (topology/cell choice, op-amp, gain and other per-section inputs) is never changed by a solve finishing; only the user changes it.
- **Scope:** In — reproduce, find the cause, fix the state handling. Out — solver behavior, the result cache and its keys (other than any key-stability fix the cause requires), FS-028's performance work.
- **Validation:** Fresh launch (new process, cold TF cache and pool), 3 designs (LP, BP, BR; 3–5 sections): set non-default specs on 1, then on several sections, solve, and let them finish one at a time and together. Every spec holds across every completion rerun, repeated ≥ 5 fresh launches. Switching tabs while a solve runs, and re-solving after a spec edit, still behave; BOMs identical to baseline.
- **Open questions:** Which spec fields reset (all of a section's, or only some widgets)? Only the finished section, or others too? Seen with a single section solving? Does it follow a tab switch or a Pairing/Spec edit during the solve?
- **Notes:**
  - Reported 2026-09-30 (maintainer): with one or more sections specified and solving, when a section's solver finishes the spec sometimes returns to its default. Setting it back by hand shows the solution table again, served from the cache — so the result is stored under the right signature and only the widget state is lost. Mostly on a fresh launch, during the first solves.
  - Suspects to check first: (1) Streamlit drops a keyed widget's state when that widget is not rendered on some run — the `run_every` fragment's app-wide rerun after `_drain_finished` may land on a run where the section widgets are skipped (early return, another tab, a spinner/pending branch), after which `setdefault` re-seeds the defaults; (2) widget keys built from values that change on the first solves (signature, `hw_gen`, op-amp list or defaults computed after a cold start), so the widget comes back under a new key; (3) a `hw_gen` bump or a reset of the `hw_*` dicts on the first completion that also clears the spec keys. A durable, non-widget mirror of the spec (as `bom_picks` does for the BOM pick) is the likely fix shape.
  - Finding 2026-10-01 (FS-028 S2-4, reproduced in a minimal Streamlit 1.55 script): a keyed widget that is not rendered for a while and is then re-created during a **fragment-only** rerun comes back at the widget's own default (0, first option) and overwrites the value still held in session state — `st.session_state[k] = st.session_state[k]` does not prevent it. This matches suspect (1). The batch-mode envelope / op-amp widgets now use `ui_components._mem_widget` (a `_mem_` mirror passed back as `value` / `index`), which survives it. The per-section family / gain / option widgets do not yet. Also since 2026-10-01 the tab polls only while solves are pending (no permanent 2 s fragment), which removes most idle reruns.
- **Updated:** 2026-09-30

### FS-037 — Custom capacitor value list ("custom C row") for snapping
- **State:** PROPOSED
- **Priority:** P1 (maintainer)
- **Effort:** plan high / build medium
- **Tiers:** C (snap candidate sets only), D (input + display)
- **Depends on:** —
- **Contracts:** §6 (Solution schema unchanged; the C-series choice becomes part of the cache/result signature)
- **Files:** `topology_tab.py` (`C_SERIES_OPTIONS` ~L136, series widget, `cser` in the solve signature ~L1032), `discrete_snapper.py` (`E_SERIES_BASE` and value-grid builder), `unified_solver_v2.py` (own E-table ~L77, snap/assemble path, incl. the FS-028 batched snapper), `first_order_solver.py` (`_CAP_SERIES` ~L37), possibly `zero_manifold_solver.py`; `report_pdf.py` / `report_ui.py` / `spice_export.py` only where the series name is printed
- **Goal:** Besides E3/E6/E12, the user can pick "Custom" and type the capacitor values they actually stock (e.g. `100p, 1n, 4.7n, 10n`); every solver snaps capacitors only to those values, exactly as it does to an E-series today.
- **Scope:**
  - In — a "Custom" C-series option with a value-list input (engineering suffixes p/n/u/µ, commas or spaces; parsed, sorted, de-duplicated; invalid entries reported); values used **as given, absolute** — no decade repetition (unlike an E-series row, `4.7n` does not imply `47n`); every capacitor-snapping path uses the list (2nd/3rd-order, notch, first-order, AM split capacitors C1a/C1b / C2a/C2b and parallel combinations); the list is part of the solve signature, so changing it re-solves and does not reuse stale cached results; the BOM / report / SPICE header show "Custom (n values)".
  - Out — any change to the cell math, residuals, seeds or scoring ("should not affect under-the-hood math"); a custom resistor list (separate item if wanted); per-section lists (one list for the design).
- **Validation:** (1) With Custom = the exact E12 values over the decades the design uses, BOMs identical to the E12 run (scratch diff over 3–4 designs: LP, BP, BR with notch, one AM, one odd-order with a 1st-order section). (2) With a short list (`100p, 1n, 4.7n, 10n`): every capacitor in every BOM row is a list member; a section that cannot be met reports it (no crash, no silent off-list value). (3) Edit the list → sections re-solve; restore it → results come back. (4) Bad input (`abc`, empty, negative) → message, previous list kept. `python verify.py` passes; `dev/qa/` run per its levels.
- **Open questions:** Does the custom list replace the E-series or add to it (union — e.g. "E6 plus 3.3n")? Do the capacitor range / ratio limits (`ratio`, min/max C) still apply, or does the list define the range? Is a custom resistor list wanted as well? Should the list be saved with the project (FS-011)?
- **Notes:**
  - Requested by Christopher Paul (external user), 2026-10-05.
  - Today the snappers build a value grid by repeating a normalized E-row over decades; the custom list needs an absolute-value grid path beside it. Three separate E-tables exist (`discrete_snapper`, `unified_solver_v2`, `first_order_solver`) — route all through one candidate-grid helper rather than patching each.
- **Updated:** 2026-10-05

### FS-038 — Monte Carlo: optional op-amp parameter spread
- **State:** PROPOSED
- **Priority:** P1 (maintainer)
- **Effort:** medium
- **Tiers:** D (`hw_plots.monte_carlo`, Response tab MC controls, report)
- **Depends on:** FS-018 (soft — provenance fields could later supply per-part min/max)
- **Contracts:** —
- **Files:** `hw_plots.py` (`monte_carlo` ~L241: op-amp symbols `A_ol` / `GBWP_hz` / `Ro` are held at tolerance 0 today), `response_tab.py` (MC controls beside the R bands / C tolerance; `_eval_opamp` ~L135), `report_pdf.py` (MC parameter line ~L1002) / `report_ui.py`; optional `spice_export.py` (mirror in the LTspice MC preset)
- **Goal:** A checkbox in the Monte Carlo controls adds a random spread of the op-amp non-ideal parameters (A_ol, GBWP, Ro) to each run, set separately from the R and C tolerances, so the MC band shows the op-amp's contribution.
- **Scope:**
  - In — checkbox (off by default → results identical to today); a spread per parameter (± % or min/max relative to the nominal), using the same distribution choice (gaussian n-σ / uniform) as R/C; the spread applied per op-amp per run; ideal op-amp sections unaffected (nothing to spread, noted in the UI); report records the setting.
  - Out — new op-amp parameters (offset, slew rate, noise); temperature drift; changing the realized (nominal) response.
- **Validation:** (1) Checkbox off → MC result bit-identical to today (same seed). (2) Checkbox on with R/C tolerances set to 0 → band = op-amp-only spread; it widens with the spread and mostly near/above GBWP-limited frequencies; zero spread → band collapses to the realized curve. (3) A high-Q section with a low-GBWP part shows a visibly wider band than with an ideal-ish part. (4) Report shows the setting.
- **Open questions:** Spread entry: ± % symmetric, or asymmetric (datasheet min/typ — A_ol and GBWP are usually "min" specs, i.e. one-sided)? Global for all op-amps, or per op-amp type? Channels in one package correlated (same draw for a dual's two halves) or independent? Mirror it in the LTspice MC export (FS-008), or the app only?
- **Notes:**
  - Requested by Christopher Paul (external user), 2026-10-05.
  - `comp_dict` already places the op-amp parameters in each section's evaluation dict, so the perturbation slots into the existing per-run loop without touching the cell math.
- **Updated:** 2026-10-05

---

## 6. Closed log

One line per item: `FS-NNN — title — DONE|DROPPED YYYY-MM-DD — commit/reason`.

FS-005 — Op-amp library as a separate module/data file — DONE 2026-09-27 — 6f17ca2
FS-006 — Bessel and equiripple-delay responses — DONE 2026-09-27 — 3899437 (design note `dev/FS-006_bessel_eqdelay_design_note.md`, checks `dev/fs006/check_delay_solvers.py`)
FS-021 — Equiripple-magnitude stopband for delay responses — DONE 2026-09-27 — 6b59732 (checks `dev/fs006/check_delay_solvers.py` §11)
FS-001 — Design-control section style (all tabs) — DONE 2026-09-28 — e3b1ab6 (`ui_components.design_control`; blue = design inputs, amber = BOM pick; streamlit >= 1.39)
FS-002 — Response Plots: phase/GD on main plot, compact sections — DONE 2026-09-28 — dfd48f2 (phase y2 / GD y3 right-hand axes as in `hw_plots`; `plot_phase_delay` removed)
FS-003 — Fold "Roots & Transfer Function" tab into Response Plots — DONE 2026-09-28 — commit "feat(ui): FS-003/FS-004 …" (4 tabs; grey-framed `rp_roots` block with Domain Scale + Root Locations / Pole-Zero Map / H(s) expanders, `tf_form_roots` radio; CSS 7b compaction; fixed mnemoscheme axis units ignoring `unit_pair`)
FS-004 — Biquad Pairing tab: compact layout, rad/s note font — DONE 2026-09-28 — same commit (`bp_body` container under CSS 7b; rules and `<br>` removed; units note body-size)
FS-007 — Custom filter design (coefficients or poles/zeros) — DONE 2026-09-28 — commit "feat(custom): FS-007 Custom H(s) …" (`custom_tf.py`, Response "Custom H(s)" + editor panel; design note `dev/FS-007_custom_tf_design_note.md` incl. §14 build notes / maintainer refinements; checks `dev/fs007/check_custom_tf.py`)
FS-008 — LTspice export with Monte Carlo presets — DONE 2026-09-30 — f7c83f6 (`spice_cells` / `spice_asc` / `spice_opamps` / `spice_export` / `spice_ui`, `LTspice_Library/` with 26 cell templates; design note `dev/FS-008_ltspice_export_design_note.md`; checks `dev/fs008/check_spice_export.py`; open items — the two-TI-model helper collision and the last round's LTspice checks — moved to FS-029; the rule for new cells lives in `CLAUDE.md`)
FS-029 — Vendor op-amp model import (guided download, per-part wrapper) — DONE 2026-09-30 — maintainer's commit (`spice_opamps` model_candidates / install_wrapped / localize_model / repair_imports, FS_<PART>.lib wrapper isolates vendor helper subckts; per-part FS generic fallback; checks `dev/fs008/check_spice_export.py` 13, LTspice test `dev/fs029/make_wrapper_test.py`; LTspice 26: two TI parts run)
FS-035 — UI polish batch (Saal C leading constant, BOM caption, schematics expander) — DONE 2026-10-02 — 71ed779 (`dev/UI_POLISH_2026-10-02.md`)

FS-033 — Near-notch sections (f_z close to f₀) classified as pure notch — DONE 2026-10-04 — 67d931c (Q-aware notch rule + dual 2N / LPn-HPn solve ranked by snap cost; design note `dev/FS-033_near_notch_design_note.md`)
FS-028 — Topology solver performance — DONE 2026-10-04 — df652c2, 69c34b0, 4cc7148, 00f5dec (S2-1 compile-once cell kernels, S2-2 batched LM core, S2-2b batched snapper, S2-4 step 1 Batch mode + parallel section solves + idle polling removed; analysis `dev/FS-028_solver_performance_analysis.md`. Not built: S2-3 learned seeds (deprioritised), S2-4 steps 2 (self-adjusting rescue) and 3 (SPICE-level row check) — open as new items if wanted)
FS-036 — QA & benchmark harness (`dev/qa/`) — DONE 2026-10-04 — 1be46a1 (first full-level report `dev/qa/reports/QA_2026-10-03.md`)
FS-015 — Topology tab Overall filter: BP values off (validate BR) — DONE 2026-10-04 — bf81544 (band-pass overall gain measured on the swept cascade)
FS-016 — Auto-pairing review: all types, esp. BP/BR; Q < 0.5 pairs — DONE 2026-10-04 — 56fef88 (scope limited by the maintainer to unrealizable pairings: real+real stages, lowest-Q realizable absorption host, BP1LP / BP1HP default MFB, Custom H(s) pre-flight. Not addressed: "optimal" distribution criteria; seen, not fixed: `KeyError: '3LPn-atten'` with VCVS at sub-unity gain (QA 2026-10-02 §4.1), generic LP/HP pairer absorbs without a realizability test)
---

## 7. User-manual backlog

UI or behaviour changes that have landed (or are `VALIDATING`) but are not yet
described in `docs/manual/user_manual.md` / `quick_start.md`. The manuals are
updated in batches (`docs/manual/DOC_WORKFLOW.md`: `doc_drift.py`, prose,
screenshots, `--accept`, PDF build); a batch deletes the entries it covered.

*Empty.* The v1.1.0 batch (2026-10-04) covered every entry that was listed
here — FS-001…FS-008, FS-021, FS-029, FS-033 — plus the unlisted user-visible
parts of FS-015 (band-pass overall gain), FS-016 (3rd-order band-pass sections
default to MFB; Q < 0.5 real+real sections), FS-028 (Batch mode, parallel
section solves) and FS-035 (Custom H(s) leading constant K / C), in
`docs/manual/user_manual.md` §1–§9; screenshots retaken, Quick Start rewritten
from the new run, `doc_drift.py --accept` done (snapshot v1.1.0, no drift), both PDFs built.
