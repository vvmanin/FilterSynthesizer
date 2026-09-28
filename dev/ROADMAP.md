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
| FS-008 | LTspice export with Monte Carlo presets (AC + MC; design note) | P1 | PLANNED | plan xhigh / build high | FS-005 (hard, done) |
| FS-009 | Noise analysis in LTspice output | P2 *(s)* | PROPOSED | medium | FS-008 (hard) |
| FS-010 | QSpice compatibility | P3 | PROPOSED | medium | FS-008 (hard) |
| FS-011 | Project save / load | P2 | PROPOSED | high | FS-003 (done), FS-007 (soft, done) |
| FS-012 | AI integration (external API/MCP or built-in assistant) | P2 | PROPOSED | plan xhigh / build high | FS-011 (soft) |
| FS-013 | All-pass (phase) responses + all-pass cells | P3 | PROPOSED | max | FS-006 (soft) |
| FS-014 | Topology family expansion — research | P3 | PROPOSED | max | — |
| FS-015 | Topology tab Overall filter: BP values off (validate BR) | P2 *(s)* | PROPOSED | plan high / build medium | — |
| FS-016 | Auto-pairing review: all types, esp. BP/BR; Q < 0.5 pairs | P2 *(s)* | PROPOSED | plan xhigh / build high | — |
| FS-017 | Manual pairing override: unreliable clicks | P2 *(s)* | PROPOSED | medium | — |
| FS-018 | Op-amp data: provenance field + review of shipped parts | P2 *(s)* | PROPOSED | medium | — |
| FS-019 | Wider op-amp library: first batch + standing process | P3 *(s)* | PROPOSED | low | FS-018 (hard) |
| FS-020 | Equiripple phase-error (Zverev linear-phase) response | P3 *(s)* | PROPOSED | plan xhigh / build high | FS-006 (hard) |
| FS-022 | PDF report: group-delay axis blown up by notch spikes | P2 *(s)* | PROPOSED | medium | — |
| FS-023 | PDF report: group-delay detail plot for delay responses | P3 *(s)* | PROPOSED | medium | FS-006 (hard), FS-022 (soft) |
| FS-024 | Step and impulse response plots (design vs realized) | P3 *(s)* | PROPOSED | high | — |
| FS-025 | Gaussian and other non-overshooting responses | P3 *(s)* | PROPOSED | plan xhigh / build high | FS-006 (hard), FS-024 (soft) |
| FS-026 | LTspice transient export (step; impulse from the step) | P2 *(s)* | PROPOSED | high | FS-008 (hard), FS-024 (hard) |
| FS-027 | Realized response with inter-stage loading (feasibility first) | P3 *(s)* | PROPOSED | plan high / build high | FS-008 (hard) |

*(s)* = suggested priority, awaiting maintainer confirmation.

Suggested order: FS-008 (the P1 track; FS-005 and FS-006 are
done); FS-007 is done (built before FS-016, whose gaps it gates). The UI polish
track FS-001 → FS-004 is done.

Non-urgent follow-ups added 2026-09-27, ranked by implementation convenience
(easiest first): FS-022 (a report-data fix in one place) → FS-023 (one more
optional report plot, reuses FS-006's detail plot) → FS-024 (new time-domain
computation for ideal and realized, UI + report) → FS-025 (new approximation
families; research first).

Defect items FS-015/016/017 have no hard dependencies and block nothing; slot
them between feature items. File-overlap notes (to avoid rework, not
blockers): FS-016 before taking BOM baselines for other items' validation (it can change
stage assignments); FS-007 gates today's pairer gaps (FS-016 may relax its gate rows).

Op-amp data items: FS-018 before FS-009 (it defines how the noise fields are
sourced) and before FS-019 (additions follow its curation rule).

SPICE track: FS-008 (AC + Monte Carlo) first; FS-026 (transient) after both
FS-008 and FS-024; FS-009 / FS-010 plug into FS-008's bundle builder and IR;
FS-027 (loaded realized response) reuses FS-008's IR and MNA.

---

### FS-008 — LTspice export with Monte Carlo presets
- **State:** PLANNED
- **Priority:** P1
- **Effort:** plan xhigh / build high
- **Tiers:** D (new modules; Tier A/B/C and the TF cache untouched)
- **Depends on:** FS-005 (hard, done)
- **Contracts:** §6 (Solution schema is the writer's input — correct its stale keys: R8, `C1a/C1b/C1_parallel`, absent = 0.0 or None); §1 relied on read-only (`topo_for_name`, `all_cells`, `derive_nonideal` for the dev check)
- **Files:** new `spice_cells.py` (netlist IR per cell superset with open/short gating, `mna_ac`, DC-path check), new `spice_export.py` (no Streamlit: `.asc` model, template transform, auto-layout, column assembly, export-time connectivity self-check, values/MC/directives, FS generic subckt, zip + README), new `spice_ui.py` (Streamlit block), `response_tab.py` (one call after the MC settings), new `LTspice_Library/` (`symbols.asc` calibration, `cells/` superset templates, `opamps/` dummies + `_seat_template.asc`, `models/` for user-supplied model files, `tools/opamp_openloop.asc`), new `dev/fs008/check_spice_export.py` and `dev/fs008/make_opamp_dummy.py`, `FilterSynthesizer.spec` + `build.bat` + `launcher.py` (data dir, env var `FILTERSYNTHESIZER_LTSPICE_DIR`), `opamp_library.json` (`spice_model` = dummy stem, phase 3), `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md` §6, `CLAUDE.md` (new-cell checklist: + IR entry, + optional template)
- **Goal:** One zip download with LTspice schematics of the whole solved cascade (sections in series, so real inter-stage loading), op-amps copied from a local library of one-op-amp dummy `.asc` files, split supply ±Vs/2 around GND, AC nominal + AC Monte Carlo pre-set from the tool's MC settings — opens and simulates directly.
- **Scope:**
  - In — `<spec>_AC.asc` (nominal) and `<spec>_AC_MC.asc` (MC) + `README.txt`; sections stacked in a column (section 1 on top), joined by net labels; drawing per section from an exact-variant template → family superset template (gated: open parts deleted, shorted parts replaced by a wire) → auto-layout fallback, every section re-checked against the IR at export (a mismatching drawing is never written); op-amp dummies (LTspice built-in part / part + external `.lib` / model in a directive block) mapped by `spice_model`, copied into fixed-coordinate **seats** in each cell (adapter wiring in the dummy makes any symbol fit; one copy per instance, no shared labels, directives once per file), FS generic dummy (the tool's A_ol/GBWP/Ro model) for Ideal / Custom / unmapped parts; dummy check, dummy generator and open-loop harness for the maintainer's library work; Vs input (default 5 V); LTspice 24.x only; MC as `.param` per tolerance band + `.func TOL` + `.step param run` + `.save V(out)` + `.meas` probes; UI block in Resulting Response; hooks for FS-026 (source abstraction, per-analysis directives, bundle builder).
  - Out — transient (FS-026, after FS-024), MC in the time domain (maintainer decision), noise (FS-009), QSpice (FS-010), ideal inter-stage buffers (maintainer: real cascade only), UniversalOpamp modes, MC run 1 as nominal, LTspice XVII, choosing which dummies ship (maintainer), shipping or embedding vendor model files, a loaded realized response inside the tool (FS-027).
- **Validation:**
  - `python dev/fs008/check_spice_export.py`: IR vs every cell's non-ideal TF for all 92 cells (80 registry + 12 first-order, random values, |ΔH| ≤ 1e-9·max|H|, split-cap and AM R8 ≠ R7 variants); DC-path check per cell; value formatting round trip (no bare `M`/`F`, ASCII only); `.asc` round trip — re-extracted connectivity equals the IR for auto-layout and every template × variant; MC band assignment equals `hw_plots._r_tol_frac`; buffered cascade MNA equals the tool's product ≤ 1e-9 and the loaded deviation is reported; FS generic Ideal clamp ≤ 1e-6 dB.
  - `python verify.py` still passes.
  - Maintainer, LTspice 24, one design per family (SK / MFB / AM × LP/HP/BP/notch, first-order ni/inv): both files open and run without errors; with FS generic dummies the `.meas` values match the README's loaded-MNA expectations ≤ 0.01 dB; MC `.meas` spread comparable to the tool's p1–p99 band; the note's §15.2 [verify] list; FS generic, a built-in-part dummy and an external-`.lib` dummy all fit the same template seat and run at the chosen Vs.
- **Open questions:** none (answered 2026-09-28 — see Notes and the design note §17).
- **Notes:**
  - Design note: `dev/FS-008_ltspice_export_design_note.md` (decisions §0, caveats, build phases §14, validation §15).
  - Decisions (maintainer, 2026-09-28): export from the tool, opened in LTspice; `.asc` schematic, hybrid (hand-drawn superset templates per family + gating, reliable handling of optional and shorted parts); one file = whole cascade in series, column layout, net-label joins; op-amps from a local library of dummy `.asc` files, copied per section, user may swap them later; supply entered in the tool, drawn as two Vs/2 sources with GND at the midpoint; real cascade only; separate nominal and MC files (random functions never return nominal); v1 = AC + AC MC; transient split out to FS-026.
  - Decisions, second round (maintainer, 2026-09-28): designators `R201` / `C202A` / `U201`; default Vs 5 V; LTspice 24.x only; op-amps copied from dummies into fixed-coordinate seats (hierarchical block with a unified symbol kept as the documented alternative); no MC-run-1 nominal — nominal only in its own file; which dummies ship is the maintainer's call (guidelines: design note Appendix A); loaded realized response in the tool → FS-027, decided on feasibility.
  - The tool's realized response ignores inter-stage loading (plain product of unloaded sections) — LTspice differs in the far stopband by design; validation compares against the IR's loaded-cascade MNA instead.
  - Vendor model files are never shipped or embedded; dummies reference them (`.lib`) or use LTspice built-in symbols.
- **Updated:** 2026-09-28

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
- **Notes:** With FS-008's op-amp dummies the op-amp noise comes from the chosen SPICE model; only the FS generic subckt needs noise sources from `en_nV_rtHz` / `in_pA_rtHz` (values sourced per FS-018). A `.noise` file is one more entry in FS-008's bundle builder (design note §11.4, §16).
- **Updated:** 2026-09-28

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
- **Updated:** 2026-09-26

### FS-015 — Topology tab Overall filter: BP values off (validate BR)
- **State:** PROPOSED
- **Priority:** P2 (suggested — not critical, per maintainer)
- **Effort:** plan high / build medium
- **Tiers:** D (possibly C if the cause is in stage-gain bookkeeping)
- **Depends on:** —
- **Contracts:** §5 (overall sign/gain = product of sections)
- **Files:** `topology_tab.py` (`_render_overall`, ~L1869), possibly `pairing_utils.compute_stage_gains`
- **Goal:** The Overall filter section of the Topology tab reports the correct cascade values for band-pass filters, and is confirmed correct for band-reject.
- **Scope:** In — find which overall values deviate for BP (gain, f0/fc, Q/bandwidth…) and why; fix the overall computation/display only. Confirm BR (and spot-check LP/HP) is correct. Out — per-section solutions and BOMs must not change.
- **Validation:** For 2–3 BP designs (even/odd order, gained/unity) and 2 BR designs: overall values match (a) the Response Plots target and (b) the product of the realized section responses evaluated independently (scratch check); per-section BOMs identical to baseline.
- **Open questions:** Which values differ, and by roughly how much, on a reproducing design (spec to record in Notes)?
- **Notes:** Isolated to the Overall section — can land any time without touching other items. Hypothesis to check first: overall BP gain built from per-section peak gains, whereas the cascade peak ≠ product of individual section peaks when section centre frequencies differ.
- **Updated:** 2026-09-26

### FS-016 — Auto-pairing review: all types, esp. BP/BR; Q < 0.5 pairs
- **State:** PROPOSED
- **Priority:** P2 (suggested — not critical; manual re-pair is the workaround)
- **Effort:** plan xhigh / build high
- **Tiers:** A
- **Depends on:** —
- **Contracts:** §2 (stages feed classification), §6 (Stage schema — keep unchanged)
- **Files:** `pairing_utils.py` (`auto_pair_bandpass` L75, `auto_pair_bandreject` L212, `auto_pair_stages` L327, real-pole absorption), `filter_solvers.py`/`filter_engine.py` only if Q < 0.5 pairs originate in the prototype
- **Goal:** Auto-pairing produces a sound section distribution for every filter type — correct pole-zero proximity pairing, sensible section ordering, valid real-pole absorption — and never emits a "2nd-order pair" made of two real poles (Q < 0.5) unless deliberately.
- **Scope:** Phase 1 (investigation, no code): a test matrix over types × responses × orders; record every non-optimal distribution, invalid absorption and Q < 0.5 pair with its spec in `Notes`; determine whether Q < 0.5 pairs come from pairing or from the approximation stage. Phase 2 (fix): targeted changes per finding. In — LP/HP/BP/BR auto-pairing. Out — manual override UI (FS-017); Stage dict schema changes.
- **Validation:** Re-run the Phase-1 matrix: every recorded case now correct; for designs that were already correct, stage assignments unchanged (scratch diff over the matrix); pairing results feed Topology without new `pending` sections.
- **Open questions:** Criteria for "optimal" per type (pole-zero proximity, ascending-Q order, gain distribution, dynamic range)? Should two real poles ever be combined deliberately into one 2nd-order section (valid for a Q < 0.5 biquad), or always split into 1st-order sections?
- **Notes:** Can be split into two sessions per phase. Changes stage assignments → retake BOM baselines afterwards.
  - Inputs from FS-007 planning (code reading, 2026-09-27; confirm in Phase 1 — details in `dev/FS-007_custom_tf_design_note.md` §13): `auto_pair_bandpass` uses only `real_poles[0]` (`pairing_utils.py` L140), so further real poles vanish from the cascade — a wideband odd-order BP gives two real poles per real prototype pole when |r|·B > 2ω₀; it also dumps leftover origin zeros into the lowest stage even if that stage holds a jω pair (L151-162), which `classify_section` then ignores; for a BR real+real stage `hw_sections.f0_hz` comes from p₁ alone while Q = q_eff (app.py L1826), and `_stage_rho` uses the same ω₀; the Tab 3 pairing signature (app.py L1488) omits `filter_type`. FS-007 gates these cases for Custom designs until fixed here.
- **Updated:** 2026-09-27

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
- **Depends on:** FS-008 (hard — writer, bundle builder, hooks), FS-024 (hard — time window and step metrics)
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
- **Depends on:** FS-008 (hard — reuses its netlist IR and `mna_ac`)
- **Contracts:** — (solvers and scoring stay per-section and unloaded)
- **Files:** `spice_cells.py` (two-port / cascade evaluation), `hw_plots.py` (`monte_carlo`, `cascade`), `response_tab.py` (realized curve, group delay), `report_pdf.py` / `report_ui.py` (same curves), `docs/ARCHITECTURE.md`
- **Goal:** The Resulting Response tab (and report) shows the realized cascade *with* inter-stage loading — each section's op-amp output impedance driving the next section's input — so it matches the LTspice export instead of an unloaded product.
- **Scope:** Step 1 — feasibility prototype and decision: per-section ABCD parameters from the IR (two MNA solves per frequency: output open / output shorted), chain-multiplied; nominal and 2000-run MC timing vs today; group-delay approach (numeric derivative or analytic via the MNA). Step 2 (only if step 1 says go) — switch the realized curve, MC and report to it, keeping the unloaded product available for comparison. Out — loading-aware synthesis or scoring.
- **Validation:** Loaded curve equals the LTspice FS-generic AC result (FS-008) ≤ 0.01 dB; with ideal buffers it equals today's product ≤ 1e-9; MC runtime for 2000 runs within ~2× today's.
- **Open questions:** Keep a toggle (loaded / unloaded) or replace? Does the HF-rise warning logic move to the loaded curve?
- **Notes:** Found while planning FS-008 (design note §10, §16 with the feasibility sketch). Maintainer: open the item; build decision after the feasibility step.
- **Updated:** 2026-09-28

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

---

## 7. User-manual backlog

UI or behaviour changes that have landed (or are `VALIDATING`) but are not yet
described in `docs/manual/user_manual.md` / `quick_start.md`. The manuals are
updated in batches (`docs/manual/DOC_WORKFLOW.md`: `doc_drift.py`, prose,
screenshots, `--accept`, PDF build); a batch deletes the entries it covered.

- **FS-005** (op-amp library): the Topology tab's per-section op-amp picker now
  lists the JSON library (built-in parts + the user overlay
  `%LOCALAPPDATA%\FilterSynthesizer\opamp_library_user.json`); its Edit popover
  saves Custom as a named part, edits built-ins as overrides with revert, and
  deletes user parts.
- **FS-006** (Bessel / Equiripple Delay):
  - Response radio: + Bessel, Equiripple Delay. Filter Type offers only Lowpass
    / Bandpass for them (caption says why).
  - Order: "Order selection" Manual / From specs (result box in the sidebar);
    BP label "Prototype order n (BP order = 2n)"; order limits Bessel LP 1–20,
    Equiripple LP 1–15, BP 1–10.
  - Frequency (LP): "Specify by" Corner frequency / Group delay; τ₀ input in the
    reciprocal of the unit (kHz → ms …) with the derived corner shown below.
  - "Delay Specs" block: Bandpass mapping (Delay-preserving / Classic), Delay
    ripple ±δ (Equiripple), Order criterion radio (Max group delay / Min corner
    frequency / Flat delay up to f_d [+ ε for Bessel] / Stopband A_s at f_s).
  - The Passband Attenuation box defines the corner for these responses too.
  - Tab 1: "Group Delay Detail" (metrics row + zoomed τ(f) plot with the
    tolerance band); Manual Notch Placement caption (stopband only, pole
    scaling) and the BP "not available" box.
  - Report: spec rows Delay spec, Bandpass mapping, Delay ripple, τ(0) / τ_nom
    or delay at centre, flat-delay band, delay p-p, max section Q, order
    selection.
  - New widget keys: `widget_filter_type`, `widget_filter_type_delay`,
    `widget_delay_order_mode`, `widget_delay_anchor`, `widget_tau0`,
    `widget_delay_bp_map`, `widget_delay_ripple`, `widget_crit_corner`,
    `widget_crit_delay`, `widget_crit_tau_max`, `widget_crit_f_min`,
    `widget_crit_fd`, `widget_crit_eps`, `widget_crit_fs`.
  - Stale screenshots: at least `02a-sidebar-type`; run `doc_drift.py` for the
    full list.
- **FS-021** (Equiripple Magnitude Stopband, Bessel / Equiripple Delay LP):
  - Tab 1 Manual Notch Placement: checkbox "Equiripple Magnitude Stopband"
    (manual order + corner anchor only; otherwise a one-line note). When on,
    each row has an **Active** checkbox instead of Pin and a read-only solved
    frequency; default ⌊(n − 1)/2⌋ rows active (at least 1); pins are kept
    and come back when the mode is switched off.
  - Readout under the rows: number of notches, humps at −A_s (max error),
    f_s, far-stopband roll-off or flat floor, pole scale and τ(0) before → after.
  - Report: the "Manual notches" row lists the solved notches and the pole
    scale.
  - New widget keys: `widget_ems`, `ems_active_{i}`.
- **FS-001** (design-control section style):
  - Sections whose controls change the result sit in a blue accent-bordered,
    tinted box: Manual Notch Tuning/Placement (Tab 1); 3rd-order checkbox +
    Auto-Pair + mnemoscheme and Remaining Gain Distribution (Biquad Pairing);
    Convergence Settings and each section's settings + gain/Ki/Solve row
    (Topology); Monte-Carlo tolerances (Resulting Response).
  - The Topology Sort + BOM table sit in an amber box (picking a result); BOM
    rows are zebra-striped.
  - The manual could explain the colour code once. Stale screenshots: every
    tab that has one of these sections; run `doc_drift.py` for the list.
- **FS-002** (Response Plots overlays and compaction):
  - The **Phase** / **Group Delay** checkboxes no longer open a separate
    "Phase & Group Delay" plot: they overlay dashed phase (deg) and dotted
    group delay (ms) on the Magnitude Response plot, each on its own
    right-hand axis, with a legend. The user manual's Phase row (Tab 1 table)
    is now wrong.
  - Frequency Probes: Gain and Phase readouts sit beside each probe input on
    two lines (was one line under it). No horizontal rules between Stopband
    Edges, Frequency Probes and Manual Notch; notch rows are tighter.
  - Stale screenshots: Tab 1 (Response Plots); run `doc_drift.py` for the list.
- **FS-003** (Roots & Transfer Function folded into Response Plots):
  - The app has 4 tabs; the **Roots & Transfer Function** tab is gone. Its
    content is at the end of **Response Plots**, after Manual Notch, in a grey
    frame headed "Roots & Transfer Function": Domain Scale (+ Pole-Zero Map
    Units when Denormalized), a collapsed **Root Locations** expander (tables
    and K), a collapsed **Pole-Zero Map** expander (Stretch Real Axis inside), and a
    collapsed **Transfer Function H(s)** expander whose radio picks Expanded
    (Isolated Gain) / Expanded (Distributed Gain) / Factored (Cascaded
    Biquads). User manual §3.3 and the tab table (§ at L23), quick_start L74,
    SCREENSHOTS.md L91 and `ui_inventory.json` (tab list) are now wrong; tab
    numbers after Tab 1 shift down by one.
  - Response Plots is visibly more compact (smaller gaps / heading and alert
    padding, fonts unchanged).
  - New widget key: `tf_form_roots`.
  - Stale screenshots: Tab 1 and the former Tab 2; run `doc_drift.py`.
- **FS-004** (Biquad Pairing compaction):
  - No horizontal rules in the tab; tighter gaps between the units radio,
    mnemoscheme box, Hardware Stage Parameters and the Section blocks (fonts
    unchanged). "Frequencies expressed in …" is now body-size italic text.
  - Stale screenshots: Biquad Pairing tab; run `doc_drift.py`.
- **FS-007** (Custom H(s)):
  - Response radio: + **Custom H(s)**. Its sidebar, in order: **Custom Transfer
    Function** — Mode Complete H(s) / Lowpass prototype (default); **Scale**
    Normalized / Absolute (complete mode only; always Normalized for
    Tietze–Schenk); **Filter Type** — radio in prototype mode, in complete mode
    the line "Filter Type: … (detected from H(s))" (no radio; an unrecognised
    shape stops with an error); **Frequency Specifications** — complete: Unit +
    "Norm. frequency f_n" (Normalized) or nothing (Absolute), plus the measured
    edges; prototype LP/HP: Corner Frequency; prototype BP/BR: "Passband
    definition" Corners (Lower / Upper Passband Corner) / Normalized width
    ("Centre frequency f₀" + "Normalized width Δ = (f₂ − f₁)/f₀", derived
    corners shown); **Gain** Normalize / As entered (default; shows "G = … V/V
    from H(s)"); "Passband edge level α (dB below peak)" (complete) or the
    measured prototype edge attenuation (prototype); "Stopband reference A_s
    (dB, plot only)". No Order or Response Modifications block.
  - Response Plots starts with a blue **Custom Transfer Function H(s)** panel:
    Input form (Coefficients / Factored (f₀, Q) / Factored (Tietze–Schenk) /
    Roots (σ, ω)), the form's tables (fixed rows; ＋ adds and − removes the
    last row), n₀ (complete mode, (f₀, Q) form), K / A₀ (greyed and
    recalculated to the Passband Gain under Normalize), Paste expanders
    (coefficients with Descending / Ascending order; roots), "Reset to the
    example", and diagnostics (counts, edges, entered peak gain G, detected
    type, conditioning, |H(jω_n)| and f₋₃dB/f_n, gate errors, warnings, pairing
    pre-flight). Errors stop the run under the panel.
  - For Custom: no Calculated Stopband Edges readout and no Manual Notch box;
    Roots & TF "Normalized" uses ω_n (2π·f_n); Biquad Pairing hides "Equalize DC
    and HF gains" for an asymmetric band-reject; Topology's overall target gain
    is the entered G.
  - Report: rows Mode, Entry, Structure, Passband / Target edges, Gain mode,
    Entered peak gain G, Warnings; α / A_s labels as in the sidebar; no
    modification or manual-notch rows; file stem `Custom_<type>_n<poles>`.
  - New widget keys: `widget_custom_mode`, `widget_custom_scale`,
    `widget_custom_scale_ts`, `widget_custom_fn`, `widget_custom_pbdef`,
    `widget_custom_bw`, `widget_custom_gain_mode`, `widget_custom_alpha`,
    `widget_custom_as`, `custom_form`, `custom_n0`, `custom_K_f0q`,
    `custom_K_roots`, `custom_A0`, `custom_paste_num`, `custom_paste_den`,
    `custom_paste_order`, `custom_paste_poles`, `custom_paste_zeros`,
    `custom_paste_apply`, `custom_paste_apply_roots`, `custom_reset`,
    `custom_add_<table>`, `custom_del_<table>`, editors
    `custom_tbl_<table>_<rev>`; non-widget `_custom_spec`, `_custom_rev`,
    `_custom_seen`, `_custom_sig`, `_custom_base_*`, `_custom_paste_msg`; and
    `hw_pb_gain` (now written for every design).
  - Stale screenshots: sidebar (Response list); run `doc_drift.py`.
