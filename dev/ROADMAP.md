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
| FS-001 | Design-control section style (all tabs) | P2 *(s)* | PROPOSED | medium | — |
| FS-002 | Response Plots: phase/GD on main plot, compact sections | P2 *(s)* | PROPOSED | medium | FS-001 (soft) |
| FS-003 | Fold "Roots & Transfer Function" tab into Response Plots | P2 *(s)* | PROPOSED | medium | FS-002 (hard) |
| FS-004 | Biquad Pairing tab: compact layout, rad/s note font | P2 *(s)* | PROPOSED | medium | FS-001 (soft) |
| FS-006 | Bessel and equiripple-delay responses | P1 | VALIDATING | xhigh | — |
| FS-007 | Custom filter design (coefficients or poles/zeros) | P1 | PROPOSED | plan xhigh / build high | FS-003 (soft), FS-006 (soft) |
| FS-008 | LTspice export with Monte Carlo presets | P1 | PROPOSED | plan xhigh / build high | FS-005 (hard) |
| FS-009 | Noise analysis in LTspice output | P2 *(s)* | PROPOSED | medium | FS-008 (hard) |
| FS-010 | QSpice compatibility | P3 | PROPOSED | medium | FS-008 (hard) |
| FS-011 | Project save / load | P2 | PROPOSED | high | FS-003, FS-007 (soft) |
| FS-012 | AI integration (external API/MCP or built-in assistant) | P2 | PROPOSED | plan xhigh / build high | FS-011 (soft) |
| FS-013 | All-pass (phase) responses + all-pass cells | P3 | PROPOSED | max | FS-006 (soft) |
| FS-014 | Topology family expansion — research | P3 | PROPOSED | max | — |
| FS-015 | Topology tab Overall filter: BP values off (validate BR) | P2 *(s)* | PROPOSED | plan high / build medium | — |
| FS-016 | Auto-pairing review: all types, esp. BP/BR; Q < 0.5 pairs | P2 *(s)* | PROPOSED | plan xhigh / build high | — |
| FS-017 | Manual pairing override: unreliable clicks | P2 *(s)* | PROPOSED | medium | — |
| FS-018 | Op-amp data: provenance field + review of shipped parts | P2 *(s)* | PROPOSED | medium | — |
| FS-019 | Wider op-amp library: first batch + standing process | P3 *(s)* | PROPOSED | low | FS-018 (hard) |
| FS-020 | Equiripple phase-error (Zverev linear-phase) response | P3 *(s)* | PROPOSED | plan xhigh / build high | FS-006 (hard) |
| FS-021 | Equiripple-magnitude stopband for delay responses | P1 *(s)* | PROPOSED | plan xhigh / build high | FS-006 (hard) |

*(s)* = suggested priority, awaiting maintainer confirmation.

Suggested order: FS-006 → FS-008 (the P1 track; its prerequisite FS-005 is
done), FS-007 in parallel planning; UI polish FS-001 → FS-002 → FS-003
→ FS-004 can be interleaved as low-risk medium-effort sessions.

Defect items FS-015/016/017 have no hard dependencies and block nothing; slot
them between feature items. File-overlap notes (to avoid rework, not
blockers): FS-017 before FS-004 (same Biquad Pairing tab region in `app.py`);
FS-016 before taking BOM baselines for other items' validation (it can change
stage assignments); FS-016 before FS-007 build (custom designs reuse pairing).

Op-amp data items: FS-018 before FS-009 (it defines how the noise fields are
sourced) and before FS-019 (additions follow its curation rule).

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

### FS-006 — Bessel and equiripple-delay responses
- **State:** VALIDATING
- **Priority:** P1
- **Effort:** xhigh
- **Tiers:** A, D
- **Depends on:** —
- **Contracts:** — (all-pole prototypes; translated/classic BP sections are LP/HP/BP by §2 rules; pairing, dispatch gate and cells unchanged; no Tier B/C change, TF cache untouched)
- **Files:** new `delay_solvers.py` (Tier A: Bessel + equiripple-delay prototypes, corner/delay normalization, order selection, stopband notches, delay BP mappings, `delay_info`); `filter_engine.py` (LP/BP branches + kwargs `delay_ripple`, `hold_corner`, `bp_mapping`, `eps_ref`; HP/BR refuse the delay responses; HP dispatch `else: raise`); `ui_components.py` (`draw_filter_type`, `draw_delay_order_block`, delay anchor in `draw_frequency_block`, `draw_delay_block`, `_mem_widget`); `app.py` (Response radio, order/corner resolution step after the sidebar, engine wrappers, Tab 1 delay summary + plot, report rows, pairing signature, notch gating); `plot_utils.py` (`plot_group_delay_detail`, `format_seconds`); `pool_utils.py` (pool rebuilt when a source file changes); new `dev/fs006/check_delay_solvers.py`; `docs/ARCHITECTURE.md`
- **Goal:** Two new responses in the sidebar — Bessel (maximally flat delay) and Equiripple Delay (±δ delay ripple) — for LP and BP, with the order either typed in or selected from one delay/corner/stopband criterion.
- **Scope:** In —
  - LP: corner anchor (f_c at the user's α, default 3.0103 dB) or group-delay anchor (τ₀); manual order, or "From specs" with ONE criterion (radio): max τ₀ (corner anchor → largest n, latency budget) / min f_c (delay anchor → smallest n, delay line), flat delay up to f_d (Bessel ε %, equiripple δ), stopband A_s at f_s.
  - BP: prototype order n (BP order 2n), user-selectable mapping — delay-preserving pole translation (default, n/2 origin zeros, edges tuned to −α) or classic geometric transform ("delay tilted"); stopband criterion only; fold check / b > 0.3 warning with measured delay p-p.
  - Manual notches for LP, stopband only: jω zeros appended to fixed poles (delay exactly unchanged); corner anchor rescales poles to hold −α at f_c, delay anchor keeps τ₀.
  - Order limits: Bessel LP 1–20, equiripple LP 1–15, BP prototype 1–10.
  - Tab 1 delay summary + group-delay detail plot; report spec rows.
  - Out — Highpass and Band-Reject (left out of the Filter Type list for these responses: no flat-delay passband exists); BP notches; asymmetric LP×HP delay BP; equiripple phase-error family (FS-020); equiripple-magnitude stopband (FS-021); non-minimum-phase magnitude equalization (RHP zeros, design note §5.2); any Tier B/C change.
- **Validation:**
  - `python dev/fs006/check_delay_solvers.py` (asserts; passes): Bessel poles n = 1–10 vs design-note §2.7 to 8 digits and vs `np.roots` on exact coefficients ≤ 2e-11; delay identity ≤ 1e-9 (n ≤ 20); W₃ table §2.4 to 10 digits; W_α at α = 0.1…12 dB vs direct crossing; equiripple n = 2–12 × δ ∈ {0.1, 1, 5} %: max|τ − 1| = δ within 1e-6, n + 1 alternations, τ(0) = 1 ± δ, same poles as the reference, ω_p(4, 1 %) = 3.267570; order cases of note §6.4 item 6 + anchor semantics + BP stopband; notch at 3·f_c → Δτ = 4e-10 (phase derivative); BP n = 4, b = 0.1: translation p-p 1.000 %, edges −3.0103 dB, classic 9.15 %; HP mirror τ(2f_c)/τ_LP(0) = 0.250 (why HP is excluded); engine: Bessel LP n = 4 sections, τ₀ = 336.4 µs; equiripple f_p = 1419.4 Hz; HP/BR refused; Butterworth untouched; timing table (equiripple 8–100 ms per design, worst-case scan ~1.6 s).
  - App (checked by Claude): Bessel LP n = 4, 1 kHz → 1430 Hz / Q 0.5219 + 1603 Hz / Q 0.8055, τ₀ = 336.4 µs, flat (−1 %) to 0.9146 kHz; equiripple ±1 % → five equal-ripple extrema, flat to 1.419 kHz, max Q 1.177; delay anchor τ₀ = 1 ms + min corner 0.5 kHz → n = 8, corner 506.05 Hz; corner anchor τ₀ ≤ 0.4 ms → n = 5; 40 dB @ 3 kHz → "closest" warning; BP 950–1050 Hz both mappings; Topology solves both Bessel LP sections, overall DC gain 1.0000, Resulting Response and Generate Report run; report rows checked headless (Streamlit AppTest).
  - Maintainer: `python verify.py` (no cell touched); visual check of the sidebar and the Group Delay Detail at the usual window width.
  - Regression: Butterworth LP/BP defaults unchanged.
- **Open questions:** none (answered 2026-09-27 — see Notes).
- **Notes:**
  - Decisions (maintainer, 2026-09-27): prototype stored delay-normalized, UI corner at the user's α; equiripple spec = ±δ % (0.05–10); BP offered with both mappings, user-selectable; BR not offered; GD + corner auto-order semantics set by the anchor radio; stopband-only manual notches for LP.
  - Review (maintainer, 2026-09-27): order criteria became a single-choice radio (several ticked criteria let the strongest one decide anyway); Highpass excluded as well as Band-Reject — a rational HP has τ → 0 across its passband, and the only remaining benefit (slightly lower step undershoot than Butterworth HP) did not justify it; HP/BR are left out of the Filter Type list (Streamlit cannot grey out one radio option); notches close to f_c may give a large stopband hump or move the corner down — accepted as is, user control is preferred over guard rails.
  - Bug found in review: "Engine Error: TypeError: synthesize_lowpass() got an unexpected keyword argument 'delay_ripple'" on a `streamlit run` session started before the edit — Streamlit hot-reloads modules in the main process only, and the cached engine-pool workers kept the old `filter_engine`. `pool_utils.run_in_pool` now rebuilds the pool when any `.py` file is newer than the pool (constant in the frozen exe).
  - Math base, measured tables and algorithms: `dev/FS-006_bessel_eqdelay_design_note.md`; verified reference solver and offline seed generator: `dev/fs006/fs006_reference.py`, `dev/fs006/eqdelay_mp.py` (moved from the chat hand-off).
  - Existing `auto_pair_bandpass` puts origin zeros in pairs into the lowest-ω₀ stages, so the translation BP with n/2 origin zeros needs no pairing change. With n_z = n instead, edges tilt to −4.4 / −1.8 dB (n = 6, b = 0.1) — hence n/2.
  - The delay-response BP is gain-normalized at its band centre (image of the LP's DC); in a very wide band (B/f0 above ~0.5) the magnitude peak sits ~0.1–0.2 dB above it, visible as a Pairing-tab gain remainder ≠ 1.
  - User manual not updated — see §7.
- **Updated:** 2026-09-27

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
- **Notes:** Op-amp data comes from `opamp_library` (FS-005): use the `spice_model` field and store only the model *name* — vendor model files carry their own licences, so never embed their text.
- **Updated:** 2026-09-27

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
- **Updated:** 2026-09-26

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

### FS-021 — Equiripple-magnitude stopband for delay responses
- **State:** PROPOSED
- **Priority:** P1 (suggested)
- **Effort:** plan xhigh / build high
- **Tiers:** A, D
- **Depends on:** FS-006 (hard)
- **Contracts:** — (notch sections are LPn/HPn by §2; no new family expected)
- **Files:** `delay_solvers.py` (notch-placement solver on fixed delay poles), `filter_engine.py` (LP branch), `app.py` (Manual Notch Placement section), maybe `ui_components.py`
- **Goal:** For a Bessel / Equiripple Delay lowpass with manual order and the corner anchor, a checkbox "Equiripple Magnitude Stopband" in the Manual Notch Placement section places the stopband notches automatically so the stopband is equiripple (every hump at A_s, like Inverse Chebyshev) while the passband group delay stays exactly that of the delay prototype (jω zeros add no delay; the poles only get the corner-holding scale).
- **Scope (maintainer's proposal, 2026-09-27):**
  - Ticking the checkbox ticks the notch pins and makes their number boxes read-only; the computed notch frequencies are shown in ascending order.
  - The number of ticked pins sets the number of notches m and so the far-stopband roll-off 20·(n − 2m) dB/dec. Default: the largest m keeping at least −20 dB/dec (m = ⌊(n − 1)/2⌋); for even n the user may also tick the last pin (m = n/2) for a flat stopband floor.
  - Unticking pins recomputes the remaining notches and refills the read-only cells.
  - Out (to investigate separately) — bandpass.
- **Validation:** (to define) every stopband hump within a tolerance of A_s; the group delay identical to the notch-free design up to the corner scale (phase-derivative check as in FS-006); −α at f_c; roll-off slope 20·(n − 2m) dB/dec; comparison of selectivity against Inverse Chebyshev / plain Bessel at the same order.
- **Open questions:** Which quantity is solved for — the notches for a given A_s (stopband edge follows, like the Inverse Chebyshev slots), or A_s maximized for a given stopband edge? Interaction with the delay anchor (corner moves instead of τ₀) — excluded or allowed? Behaviour when A_s cannot be met with m notches near f_c (the corner-holding scale grows; feasibility test ∏(1 − (f_c/f_z)²) > 10^(−α/20) from FS-006)? Reuse of the Inverse Chebyshev slot solver (`filter_solvers.solve_inv_chebyshev_lp` least-squares loop) vs a new Remez on the notch frequencies with the pole scale as an extra unknown?
- **Notes:** Proposed after FS-006 review: manual notches combine well with Bessel / Equiripple Delay (passband delay preserved exactly). Unknowns ≈ m notch frequencies + the pole scale; equations ≈ m hump levels (m − 1 between notches + 1 above the last, or the HF floor when 2m = n) + the corner condition — a square system, Newton-solvable like FS-006's equiripple delay.
- **Updated:** 2026-09-27

---

## 6. Closed log

One line per item: `FS-NNN — title — DONE|DROPPED YYYY-MM-DD — commit/reason`.

FS-005 — Op-amp library as a separate module/data file — DONE 2026-09-27 — 6f17ca2

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
