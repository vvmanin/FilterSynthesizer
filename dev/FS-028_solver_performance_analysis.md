# FS-028 — Topology solver performance — analysis (Stage 1)

Purpose: find where the Topology-tab section solve spends its time, and which changes would
cut it. Rank them by gain, by effect on the resulting BOMs, and by risk, before any solver
code is touched. This is the Stage-1 deliverable of FS-028. It is **analysis only**: no
production file was changed. Every number here can be reproduced with the scripts in
`dev/fs028/` (§10). The raw results are in `dev/fs028/results/`.

Maintainer answers to the item's open questions (2026-09-28), which set the direction:

| Question | Answer |
|---|---|
| Target | **Wall time per section** is the blocker. The machine has 32 cores, and all 32 are fully loaded during a solve. |
| Tolerance | A small change in converged values is acceptable **if ~10 ppm buys ≥ 2× speed**. |
| Emphasis | Prefer **alternative approaches** over tuning the existing solver for a 0.5 % gain. One idea from an earlier chat: machine learning derives a fast analytical expression for the initial seed at different inputs, and the existing numerical solver then reaches the precise values. |
| Why it matters | Solve time is what blocks an **automated self-adjusting batch mode**. In that mode an orchestrator solves all sections and re-tunes the initial values of any section that did not converge. |

Conventions:
- **CPU-s** are serial CPU seconds, measured on the analysis machine: a 4-core Linux
  container with Python 3.11, numpy 2.3.5, scipy 1.17.1 and sympy 1.14. Absolute seconds will
  differ on the maintainer's machine. The **ratios** carry over.
- The **32-core model** is the wall time of one section solve with 32 workers. It is built
  from the measured per-task times: pool.map(chunksize=1) scheduling is simulated, and the
  serial stages and one worker's initializer are added (§1.2). It assumes 32 real cores and
  fork. Windows spawn adds more (§2.3).
- **Phase 1 / Phase 3 / ZM / NI** follow `unified_solver_v2`: the multistart, the
  snapped-cap resistor solve, the zero-manifold parallel-C2 path, and the non-ideal op-amp
  correction in `nonideal_solver`.
- Line numbers refer to HEAD `a03ad46`.

---

## 0. Answer first

**Where the time goes**

1. **96 % of the solve CPU is `scipy.optimize.least_squares`** (TRF with box bounds) inside the
   Phase-1 and Phase-3 multistarts: Phase 1 is 38 % and Phase 3 is 58 % (§2.1). Across 31
   benchmark sections that is 36 900 calls and 4.2 M residual evaluations. 3 988 of those
   calls run to their `max_nfev` cap without converging.
   The solver is not slow because each evaluation is expensive. It is slow because (a) TRF
   needs ≈ 45 evaluations even from a start 2–5 % away from a root, where LM needs 5: its
   steps stall near the box bounds. And (b) 53 % of all Phase-1 starts cannot converge, but
   still spend much or all of their budget (§3). The extreme is `3LPn-unity`: 209 of 210
   starts fail, and all 120 anchored starts hit 700 evaluations.
2. **About half of the 32-core wall time is fixed per-section overhead, not search** (§2.2,
   §2.3). The main process re-derives the symbolic TFs for every MFB/AM/notch solve (the
   dc_gain path). It does so again for the snapper. Every Phase-1/3 worker re-lambdifies all
   residuals and Jacobians. And with a real op-amp, **every one of the 32 non-ideal workers
   re-derives the cells symbolically (up to ≈ 10 s each)**, for 0.1–3 s of actual correction
   work. A large part of the "all 32 cores fully loaded" picture is this: two process pools
   spawned per section and initialised in parallel.
3. **The tolerance lever is weak.** Loosening TRF's `xtol/ftol` to 1e-7 (the ≈ 10 ppm trade)
   gives **1.3×**. Adding a 4× cut of the iteration budgets gives **2.0×**, but then the best
   BOM changes in 6 of 28 sections (§4.1). The cost sits in starts that fail and in stalled
   iterations, not in the last digits.

**What to do: relative solving-time coefficients**

Measured over the 31-section benchmark set (Balanced, ideal op-amp) unless marked *est.*.
*Coefficient* = new / baseline; lower is faster. Details in §5.

| # | Approach | CPU per section | Wall per section, 32 cores | Effect on BOMs | Risk | Verdict |
|---|---|---|---|---|---|---|
| A | Tune existing TRF: tolerances only / + budgets ÷4 | 0.74 / 0.50 | 0.88 / 0.74 | ≈ none (2 of 28 best sens better) / 6 of 28 change (3 better, 3 worse) | low | too little; ruled out as the main path |
| B | Compile once: design-parametric residuals, no per-section derivation, lambdify or pool start-up (S2-1) | 0.97 serial; **0.48 of the core-seconds a 32-worker box burns** (op-amp mode) | **0.56** (op-amp mode 0.45; MFB 0.37 / 0.32) *est.* | none (same math) | low–med | **do** (prerequisite) |
| C | Log-space projected Levenberg–Marquardt, scalar drop-in for TRF (pools kept) | **0.15** (6.9×) | 0.55 (the fixed overheads remain) | Phase 3: near-neutral; Phase 1: different valley sample (18 same / 4 better / 6 worse) | med | superseded by D |
| D | **Vectorized (batched) LM**, whole Phase 1 / 3 in one process, no pool (S2-2) + B | **0.016** (Phase 1 91×, Phase 3 187× less CPU, measured) *est.* | **0.21 on one core** (op-amp mode 0.27) *est.*; 32 sections in parallel in batch mode | as C, plus safeguard F | med | **do** (main gain) |
| E | **Learned seeds** (the ML idea): analytic polynomial seed and/or valley atlas, + LM polish (S2-3) + D | **0.013**; Phase 1 → 2–35 ms per cell | **0.18 on one core** *est.* | measured on 3 cells: best valley as good as a full cold multistart | med | **do after D** (robustness, retries) |
| F | Explicit sensitivity descent on the solution manifold (quality safeguard) | small in batch form | — | best sens **better** than baseline where it applies | low–med | **pair with D** |

**Stage 2 progress:** S2-1 (B) landed 2026-09-30, BOMs bit-identical to HEAD — results and one
correction to row B ("same math" alone is not enough for identical BOMs) in §11.

Recommended path (§7): **B → D + F → E → the self-adjusting orchestrator**. B removes the
fixed overhead at no change in results. D moves the multistart off the process pool and cuts
its CPU by one to three orders of magnitude, with F as its quality guard. E makes seeds, and
therefore retries, nearly free. Together they bring a section from **0.3–24 s (median
≈ 2 s) on 32 busy cores** (today's model, more on Windows) to **0.05–6 s (median 0.3 s;
0.7 s with a real op-amp) on one core** (*est.*, §5). The orchestrator can then try several
strategies per section, and all sections in parallel, within the time one solve takes
today.

---

## 1. Method

### 1.1 Benchmark set

`dev/fs028/fs028_common.section_set()` holds 11 sections with realistic targets from standard
prototypes at a ~1 kHz corner:

| Id | Kind | From | f0 (Hz) | Q | fz (Hz) | f1 (Hz) | Gain |
|---|---|---|---:|---:|---:|---:|---|
| LPn3 | 3rd-order LP-notch | elliptic LP 5, 0.5 dB / 60 dB | 735.4 | 1.273 | 2847 | 402.8 | 1 |
| LPn2 | 2nd-order LP-notch | same filter | 1016.6 | 5.551 | 1852 | — | 1 |
| LPn3g | as LPn3, DC gain 2 | — | 735.4 | 1.273 | 2847 | 402.8 | 2 |
| LP2 / LP3 | all-pole LP | Butterworth-like | 1000 | 1.307 / 1.0 | — | — / 1000 | 1 |
| HPn3 / HPn2 | HP-notch | elliptic HP 5 | 1359.8 / 983.7 | 1.273 / 5.551 | 351 / 540 | 2483 / — | 1 |
| HP2 | all-pole HP | Butterworth-like | 1000 | 1.307 | — | — | 1 |
| BP2 | band-pass | — | 1000 | 5 | — | — | 1 (centre) |
| N2 | pure notch | — | 1000 | 2 | 1000 | — | 1 |
| BP1LP | 3rd-order band-pass | — | 1000 | 5 | — | 300 | 1 (centre) |

Each section is run for each family (VCVS / MFB / AM) that the Topology tab would solve it
with. The cells and `dc_gain` are chosen by `route()`, which is the logic of
`topology_tab._render_section` for an untouched UI. The envelope is the tab's default: C 68 pF–10 nF
E12, R 300 Ω–2 MΩ E48, max R ratio 500. The tolerances are 1 % / 0.5 %, and top_k is 30.
AM uses Equalize on. This gives **31 cases** (BP1LP is MFB-only).
Note that `3LPn-gained` automatically pulls in its `+R7` twin (`run_synthesis`).

### 1.2 Instrumented runs and the 32-core model

`fs028_common.instrumented()` replaces every `ProcessPoolExecutor` in the solver with an
in-process serial pool. It times every worker task, initializer, `least_squares` call and
pipeline stage. Nothing else changes, and the results are the same as a pooled run. The
32-core model then combines four parts: the serial stages, one initializer, and the Phase-1/3
and NI task lists scheduled greedily over 32 workers in dispatch order (`sched_wall`).
Check: `LPn3-AM` Balanced with TL072 took **17.4 s** in a real 4-worker fork pool and 18.0 s
with spawn. Its serial run is 57.6 CPU-s, and the model schedules that to 15.6 s on 4
workers. **The model is about 10 % optimistic** (pool and pickling overhead), and the real
32-core numbers will be at least that much higher.
Reproducibility (the Stage-1 validation): three sections re-run from the repository root
(LP2-VCVS, N2-MFB, LPn3-AM) gave **identical top-5 BOMs** and serial CPU within 7–12 % of the
baseline. The baseline ran under heavier machine load; results are deterministic, times are
not.

### 1.3 Limits

- The analysis machine has 4 cores. Per-task CPU carries over, but the absolute 32-core wall
  numbers are a model. They assume 32 real cores. SMT, turbo behaviour under full load and
  memory bandwidth will make the real box slower than the model, not faster.
- **Windows spawn cost was not measured on Windows.** §2.3 gives the Linux spawn numbers. The
  maintainer can measure the real ones with `probe_pool.py --workers 32` and
  `bench_sections.py --mode pool --spawn --cores 32` (§10).
- The prototypes in `dev/fs028/lm_core.py` are analysis code. Their speed-ups are
  lower bounds for production code, but their quality evidence is limited to this benchmark
  set (§6).

---

## 2. Baseline: where the time goes

### 2.1 CPU split

Balanced, ideal op-amp, 31 sections: **1506 CPU-s** in total. **Phase 1: 572 s (38 %)**,
**Phase 3: 878 s (58 %)**, main-process symbolic derivation 30 s (2 %), worker initialisers
16 s, snapper 5 s. With TL072 the total is 1554 s, of which the NI correction tasks are only
15 s. Per section the median is 29 CPU-s and the mean 49 CPU-s; the heavy tail is
`HPn3-AM` at 393 s, `LPn3g-VCVS` at 311 s and `HPn3-VCVS` at 92 s. The full tables are in
Appendix A.1 (ideal) and A.2 (TL072).

Of the Phase-1 and Phase-3 time, **99.9 % is inside `least_squares`**. The rest is the
bookkeeping around the solves (`_assemble`, the sensitivity loop, harvest), and it is
negligible.

| | calls | residual evaluations | CPU-s | hit max_nfev |
|---|---:|---:|---:|---:|
| Phase 1 (`unified_solver_v2.py:586`, `:614`) | 10 338 | 1 979 695 | 572 | 1 392 |
| Phase 3 (`:663`, `:677`, ladder `:886`) | 26 587 | 2 225 887 | 877 | 2 596 |

### 2.2 The 32-core wall: half search, half fixed cost

The 32-core model per section: median **1.8 s**, mean 3.6 s, max 17.5 s (ideal). With TL072:
median 2.0 s, mean 4.7 s, max 23.5 s. Summed over the benchmark, the share of each
stage in the modelled wall time is:

| Stage | ideal | TL072 |
|---|---:|---:|
| main process, run_synthesis: TF derivation (dc_gain path), task building, harvest | 19 % | 15 % |
| Phase-1/3 worker initializer (load cases + lambdify residuals and Jacobians) | 15 % | 12 % |
| Phase 1 (parallel) | 19 % | 15 % |
| Phase 3 (parallel) | 31 % | 24 % |
| NI worker initializer (**re-derives every cell symbolically**) | — | **22 %** |
| NI correction tasks | — | 1 % |
| snapper: TF derivation again (dc_gain path) + snap | 16 % | 11 % |

So even with perfect 32-way scaling, **roughly half of the wall time does not parallelise**.
That half is derivation, compilation and initialisation, and it is repeated for every section
and every re-solve.

### 2.3 Fixed per-section overheads (why all 32 cores are busy)

Each `synthesize()` call spins **up to two fresh process pools**. `run_synthesis` builds one
(`unified_solver_v2.py:1467`) and `solve_nonideal` builds the other (`nonideal_solver.py:278`).
Every worker runs an initializer. `probe_pool.py`, 4 workers, per-worker CPU:

| Section | Pool | fork, per worker | spawn (Windows method), per worker |
|---|---|---:|---:|
| LPn3-MFB | Phase 1/3 | 0.81 s | 1.71 s |
| LPn3-MFB | NI | 3.80 s | 5.30 s |
| HPn3-VCVS | Phase 1/3 | 2.04 s | 2.84 s |
| HPn3-VCVS | NI | 4.10 s | **9.61 s** |
| LPn3g-VCVS | Phase 1/3 | 1.85 s | 2.50 s |
| LPn3g-VCVS | NI | 2.02 s | 4.93 s |

With 32 workers, that is **≈ 55–90 CPU-s (Phase-1/3 pool) + 160–310 CPU-s (NI pool) per
section solve**. It runs on all cores at once, before and between the actual searches. The
causes:

- **Symbolic derivation is repeated.** In dc_gain mode (every MFB and AM section, VCVS notch
  and atten sections), `TF.get_cases(k_map=…)` bypasses the cache and re-derives the ideal
  **and** non-ideal TFs (`tf_derivation_v2.py:479`). This happens in `run_synthesis`
  (`unified_solver_v2.py:1383`), again for the snapper (`filter_synthesis.py:242`), and in
  **every** NI worker. `solve_nonideal` forces the fresh-derive path even in K mode, to dodge
  the slow sympify of the 766k-op AM TF (`nonideal_solver.py:269`). The main process alone
  spends up to 8.5 s on `LPn3-MFB` in `run_synthesis` and another 4.8 s in the snapper.
- **Lambdify is repeated.** Each Phase-1/3 worker lambdifies every cell's residuals, Jacobian,
  a1/a2, H(0) and H(∞) (`_init_worker`, `:403`): 0.04–6.3 s per worker, times 32.
- **Spawn and import.** The imports alone (numpy + scipy.optimize + sympy + the app modules)
  cost 0.72–0.9 s per worker on Linux. On Windows the per-process start and the imports are
  slower; this was not measured here (§1.3).

These costs are independent of the search effort. They are also why a re-solve of the same
section in the self-adjusting loop would pay them again every time.

### 2.4 Search presets and the TF cache

| Preset (ideal, 31 sections) | Serial CPU | 32-core model: mean / median / max |
|---|---:|---|
| Fast | 865 s (0.57× Balanced) | 2.84 / 1.37 / 13.1 s (0.80× Balanced) |
| Balanced | 1506 s | 3.57 / 1.80 / 17.5 s |
| Thorough | 2268 s (1.51× Balanced) | 4.33 / 2.51 / 24.2 s (1.21× Balanced) |

**The preset barely moves the wall of a 32-core user.** Fast halves the search CPU but only
takes 20 % off the wall, because the fixed per-section costs of §2.3 do not shrink with
search effort. Thorough adds 51 % CPU for 21 % more wall. **It does not reliably find better
BOMs either.** Against Balanced, its best sensitivity is better in 3 sections, the same in 21
and *worse* in 4, and the same 3 sections stay without a BOM. The best BOM is set by which
valleys the multistart happens to sample (§4.3), not by how many starts it spends. All
three presets list the same total of 644 BOMs, because most sections fill the top-30 list.

**Cold vs warm TF cache** (`bench_sections.py --cold`, Fast, TL072):

| Section | Main-process derivation, cold / warm | Every NI worker, cold / warm |
|---|---|---|
| LPn3-VCVS | 3.95 s / 0.46 s | 3.10 s / 3.42 s |
| HPn3-VCVS | 9.68 s / 2.55 s | 8.12 s / 9.08 s |
| N2-VCVS (dc_gain path) | 0.65 s / 0.65 s | 0.40 s / 0.36 s |

The cache helps only the main process, and only on the VCVS K path. Even there, every new
design re-derives its ideal cells, because the cache key contains the numeric targets. The
dc_gain path (MFB, AM, notch, atten) and **every NI worker** re-derive on every solve,
cold or warm. A cold `HPn3-VCVS` with TL072 models to **23 s** on 32 cores, where 13 CPU-s of
solve work would need less than 1 s.

### 2.5 Sections without a BOM

3 of the 31 cases return **no BOM**: `LPn2-VCVS` (2LPn-unity, Q = 5.55), `HPn3-MFB`
(3HPn-MFB2) and `HPn2-MFB`. With batched LM and **10× the Balanced start count** (2 100–4 200
starts, ≤ 3 s on one core, under Phase 1's own acceptance rule) Phase 1 still finds no
convergent root for any of them. So more starts do not rescue them. Rescuing them needs a
different envelope, cell or gain split. That is exactly the orchestrator's job (§8), and a
fast solver makes "prove there is no root" cheap.

---

## 3. Why the multistart is slow: root causes

### 3.1 Failing starts spend the full budget

`probe_phase1.py` records every start's cost after each evaluation.

| Section | Starts | Converged | Distinct valleys | CPU on failed starts |
|---|---:|---:|---:|---:|
| LPn3-VCVS (3LPn-unity) | 210 | **1** | 1 | 35.0 of 35.0 s — all 120 anchored starts at `max_nfev` = 700 |
| LPn3-AM | 420 | 32 | 20 | 17.7 of 18.6 s — anchored 0 / 240 |
| LPn3-MFB | 210 | 141 | 105 | 8.4 of 12.6 s |

The whole Balanced BOM list of `3LPn-unity` comes from **one** lucky start. Across the set,
anchored starts are the most wasteful: 5 880 starts, 3 065 converged, 302 CPU-s, and in AM
cells typically 0 converged.

**Early abort** ("stop a start whose cost is still > thr after k evaluations") saves 25–55 %
of Phase-1 CPU at k = 100 / thr = 0.1 without losing a converged start on LPn3-VCVS / AM. But
on MFB it loses 17 of 141 converged starts, because TRF's *successful* starts are also slow
(median 67–109 evaluations). It is a weak, risky lever (table in `probe_phase1.py` output).

### 3.2 TRF is slow even next to a root

A converged `3LPn-unity` root was perturbed by log-normal noise and re-solved 40 times per
width. TRF used the Phase-1 settings (wide box); LM is `lm_core.lm_log`, run unbounded and
projected onto the same box (`probe_basin.py`, output in `results/probe_basin.txt`):

| Perturbation | TRF converged | TRF evaluations (median) | TRF ms/start | LM (box) converged | LM evaluations | LM ms/start |
|---|---:|---:|---:|---:|---:|---:|
| ×1.02 | 39/40 | 46 | 56 | **40/40** | **5** | 0.69 |
| ×1.05 | 37/40 | 46 | 40 | **40/40** | **5** | 0.71 |
| ×1.22 | 36/40 | 50 | 43 | **40/40** | **6** | 0.82 |
| ×1.65 | 29/40 | 43 | 70 | **40/40** | **7** | 0.87 |
| ×2.72 | 26/40 | 55 | 84 | **40/40** | **8** | 1.00 |

From 5 % off the root, TRF needs 20–23 evaluations to reach cost 1e-10 and 36–49 to
terminate. Its trial points often jump the cost up (to ~1e+1), and in other draws it sits on
plateaus of 10–20 evaluations (cost 2e-5 or 8e-4). That is consistent with the Coleman–Li
scaling of `trf`, which slows every variable that sits near a bound, and the Phase-1 boxes
put many variables near a bound. LM converges quadratically in 5–8 evaluations, **≈ 50–100×
less time per start**. **The basin of attraction is wide**: even ×2.7 errors converge with
LM. That is what makes learned seeds (§4.5) work.

### 3.3 Phase-3 fallback storms

When the hints miss, `phase3_worker` falls back to 8 log-uniform starts (24 for manifold-0
cells), each allowed 500 evaluations (`:677`). In AM, most snapped-cap combinations cannot
reach the manifold-0 acceptance. So `HPn3-AM` makes **7 747 Phase-3 calls (370 CPU-s)**, and
`LPn3-AM` makes 3 600 fallback solves, none of them reaching a cost < 1e-6. On MFB (`LPn3-MFB`),
124 of the 269 hint solves stop at `max_nfev` = 300 (22 of 30 s). The VCVS notch cells add the R5 ladder:
6 finite-difference solves per natural solution, about 2/3 of `HPn3-VCVS` Phase 3.

### 3.4 The open CONTRACTS §7 lever (`nonideal_solver` tolerances) is moot

The non-ideal correction *tasks* total 15 CPU-s over the 31 sections with TL072, which is
≈ 1 % of the solve. Loosening its `xtol/ftol` from 1e-12 to 1e-9 cannot matter for speed. The
non-ideal stage costs time through its pool initializer (§2.3), not through its solves.

### 3.5 What the tolerances do (the "10 ppm" question)

The residuals are relative coefficient errors, so |r| ≈ 1e-5 is ≈ 10 ppm. The pipeline's gates
are far looser than the solver tolerances: Phase-1 accepts cost < 1e-5 (|r| up to ≈ 3e-3),
Phase 3 accepts up to 5e-3, and the snapper then moves every part to an E-series value (E48
steps are ≈ 5 %). The 1e-9…1e-12 `xtol/ftol` therefore buy nothing downstream. But loosening
them saves little (§4.1), because the time is not spent there.

---

## 4. Candidates evaluated

### 4.1 A — Tune the existing TRF (tolerances, budgets)

`ab_lm.py --variants trf-loose-tol trf-loose`, all 31 sections:

| Variant | Serial CPU (1506 s baseline) | Speed-up | Best sens vs baseline | Baseline best BOM still produced |
|---|---:|---:|---|---:|
| `xtol = ftol = 1e-7`, budgets unchanged | 1120 s | **1.3×** | 26 same, 2 better, 0 worse | 26 / 28 |
| `1e-8` and `max_nfev ÷ 4` (Phase 1/3/ZM) | 747 s | **2.0×** | 22 same, 3 better, 3 worse | 21 / 28 |

The tolerance alone does not reach the maintainer's 2× bar. The budget cut reaches it, but
it changes results as much as a new algorithm would, and it leaves the fixed overheads of
§2.3 untouched. It is kept only as a fallback quick win if Stage 2 is delayed.

### 4.2 B — Compile once: remove the per-section fixed cost

- **Design-parametric residuals.** Every cell's `build_ideal(topo, {})` works with *symbolic*
  targets. The residuals can be lambdified once with `(components…, p1, w0, wz, Q, K)` as
  arguments. Checked for **all 80 cells**: at random points they equal the per-design
  residuals to ≤ 1e-9 (`probe_atlas.param_funcs` builds them). This is a one-time derivation
  of 67 s for all 80 cells, cacheable on disk like the non-ideal TFs. After that, a new design
  costs **no** sympy work at all. Per-topology K is just the K argument.
- The non-ideal TF is already design- and K-independent. The fresh-derive paths in dc_gain
  mode and in the NI workers are **work-arounds for slow sympify of huge cached expressions**.
  Caching the lambdified or generated *code* instead of the expression tree removes both.
- **No per-section pools.** A persistent pool, with workers that keep compiled cell kernels,
  removes spawn, import and init. With D, the solve needs no pool at all.

The effect is a wall of **p1 + p3 + ZM + NI + snap** only. That is 0.56× today's modelled
wall on average, 0.45× with a real op-amp, and 0.32–0.37× for MFB sections (§5). The 32× replication of
initializer CPU (§2.3) disappears. Results are unchanged, because the math is the same.

### 4.3 C — Log-space projected Levenberg–Marquardt (scalar drop-in)

`lm_core.lm_log` is LM on u = log x (every R and C is positive). Its steps are Tikhonov /
minimum-norm, which suits under-determined coefficient systems. It projects onto the box with
an active set, which removes TRF's near-bound slow-down. It stops at cost < 1e-20
(|r| ≈ 1e-10). `LSAdapter` swaps it in for `least_squares` inside Phase 1/3/ZM. Harvest,
gates, `_assemble`, NI and the snapper are untouched. `ab_lm.py --variants lm lm-p3 lm-p3pol`:

| Variant | Serial CPU (1506 s baseline) | Speed-up | Best sens: same / better / worse | Baseline best BOM still produced |
|---|---:|---:|---|---:|
| `lm`: LM in Phase 1 and 3 | 220 s | **6.9×** (per section 2.4–25×) | 18 / 4 / 6 | 17 / 28 |
| `lm-p3`: LM in Phase 3 only (Phase 1 still TRF) | 711 s | 2.1× | 24 / 0 / 4 (all ≤ +3.6 %) | 22 / 28 |
| `lm-p3pol`: `lm` + Phase-3 sensitivity polish (scalar prototype, §4.6) | 573 s | 2.6× | 17 / **6** / 5 | 17 / 28 |

The BOM counts are unchanged, except `LP2-VCVS` (30 → 25 with LM in Phase 1). The best
`snap_cost` is lower than the baseline in most sections when Phase 3 uses LM: LM converges to
the exact root (|r| ≈ 1e-10), where TRF stops at its tolerance or at the loose manifold-0
acceptance. Examples: `LP3-VCVS` 3.08 → 0.031, `N2-VCVS` 3.47 → 0.354. Per-section tables are
in Appendix A.6–A.8 (A.4–A.5 for the TRF variants of §4.1).

Two findings matter for Stage 2:
- **Phase-3 LM is close to quality-neutral.** With LM in Phase 3 only (`lm-p3`), 24 of 28
  best sensitivities are identical and 4 are within +0.5…+3.6 %; snap cost is mostly better.
  Phase 3 is where most of the CPU goes (58 %).
- **Phase-1 LM samples different valleys.** It often finds more of them (BP2-VCVS: 40 vs 33
  distinct), but not the same ones. On `BP2-VCVS` the best sensitivity got worse
  (2.90 → 7.06 with LM in Phase 1 alone). Tracing it: the same snapped caps
  (C1 = 10 nF, C2 = 1.5 nF) are present, but **Phase 3 lands at a different point of the
  2-dimensional resistor manifold** because its hint differs. And on `LPn3g-VCVS` (3.91 →
  4.08), the baseline's best BOMs sit **on the envelope edge** (C2 = C_min = 68 pF,
  R4 = 1.69 MΩ against R_max = 2 MΩ). TRF
  drifts there with its reflective bound handling; projected LM does not, even with
  Thorough's 2× starts. So **the baseline's best BOM depends on solver dynamics, not on an
  objective**. A faster solver must therefore bring an explicit quality step (F).

### 4.4 D — Vectorized (batched) LM, one process, no pool

`lm_core.batch_lm_log` runs the same projected log-space LM on **all starts at once**. The
lambdified numpy residuals broadcast over an (N, n) array, the Jacobian comes from a
per-entry lambdify (`batch_funcs`), and the step is a batched `np.linalg.solve`. Fixed
variables (the anchor cap, or the snapped caps in Phase 3) get a degenerate box, so one
kernel serves every mode. Single core:

| | Production TRF CPU | Batched LM | Factor | Same outcome? |
|---|---:|---:|---:|---|
| Phase 1, LPn3-VCVS (210 starts) | 36.7 s | **0.22 s** | 166× | same 1/210 converged, same valley |
| Phase 1, LPn3-MFB (210) | 13.2 s | 0.20 s | 68× | 133 vs 141 converged; 105 vs 105 distinct valleys |
| Phase 1, LPn3-AM (420) | 19.5 s | 0.28 s | 70× | 32 vs 32 converged |
| Phase 1, HPn3-AM (420) | 22.1 s | 0.25 s | 89× | 98 vs 80 converged |
| Phase 1, LPn3g-VCVS (420) | 58.0 s | 1.67 s | 35× | 192 vs 203 converged |
| Phase 3, HPn3-AM (520 tasks; production task list, TRF semantics) | 332.7 s | **0.88 s** | 380× | 220 / 220 tasks reach acceptance |
| Phase 3, LPn3g-VCVS (1010 tasks) | 152.3 s | 1.37 s | 111× | 915 vs 919 |
| Phase 3, HPn3-VCVS (348 tasks) | 12.9 s | 0.022 s | 590× | 348 / 348 |
| Phase 3, LPn3-MFB (244 tasks) | 24.9 s | 0.015 s | 1600× | 244 / 244 |
| Phase 3, N2-VCVS (66 tasks) | 3.0 s | 0.044 s | 69× | 66 / 66 |
| **All 31 sections, Phase 1** (production start sets) | 572 s | **6.3 s** | **91×** (35–427×) | a different local solver reaches partly different valleys (§4.3) |
| **All 31 sections, Phase 3** (task lists from an LM Phase 1) | 803 s | **4.3 s** | **187×** (7–1200×) | tasks reaching acceptance **identical in every section** |

All 31 sections are listed in Appendix A.9. The whole multistart of a section fits in
**≈ 0.02–3 s of one core**. That is less than today's 32-core wall. It needs no process pool
(and so none of the Windows/PyInstaller multiprocessing fragility for the solve). In batch
mode it leaves 31 cores free for other sections. **With B, lambdify (0.1–5 s per cell) becomes
a once-per-session or cached cost, which is why B is a prerequisite.** The R5 ladder
(finite-difference solves today) batches the same way. It was not prototyped; it is estimated
at the same order as Phase 3.

### 4.5 E — Learned seeds: the ML idea, measured

`probe_atlas.py` uses the design-parametric residuals (B) and the batched LM (D). It trains
on cold solves of random targets, then seeds **unseen** targets three ways:
- **cold**: the production start set.
- **atlas**: the valleys of the k = 4 nearest training targets, by distance in
  log(Q, fz/f0, f1/f0 or gain), used as starts.
- **poly**: an explicit **analytic seed**, a 10-term quadratic polynomial in the log-targets,
  least-squares fitted to each training target's lowest-sensitivity valley. It predicts one
  component vector, used with 4 jittered copies, and LM then polishes it to |r| < 1e-10.

| Cell (train / test targets) | Method | Seeds accepted | Distinct valleys | Best sens vs cold | ms per target | LM iterations |
|---|---|---:|---:|---|---:|---:|
| 3LPn-unity (300 / 60) | cold | 65 % | 49 | 1.000 | 157 | 100 (capped) |
| | atlas | **100 %** | 34 | 1.000 (p90 1.00) | 35 | 16 |
| | poly | **100 %** | 3 | **0.999** (p90 1.00) | **5.1** | **5** |
| 2BP (200 / 40) | cold | 82 % | 39 | 1.000 | 97 | 100 |
| | atlas | 100 % | 28 | 1.003 (p90 1.38) | 14 | 8 |
| | poly | 100 % | 3 | 1.009 (p90 1.06) | **1.9** | 5 |
| 3LPn-MFB (200 / 40) | cold | 63 % | 101 | 1.000 | 193 | 100 |
| | atlas | 100 % | 43 | 1.003 (p90 1.04) | 29 | 14 |
| | poly | 100 % | 4 | **0.989** (p90 1.00) | **4.2** | 5 |

Only feasible test targets are counted (cold found at least one valley). All methods use
Phase 1's acceptance rule, and an iteration-capped start is rejected. The raw output is in
`results/probe_atlas.txt`.

Findings:
- **The idea works, and it needs only a crude model.** A polynomial in the *log*-targets is
  a power law with curvature: component ≈ c · Q^a · (fz/f0)^b · (f1/f0)^d · …, the same form
  as a handbook design equation. It is the "fast analytical expression" of the maintainer's
  proposal, fitted automatically per cell. The polynomial's in-sample error is a factor
  ×1.4–×2.4 per component. Every seed still converged, because the basin is wide
  (§3.2). In ~5 LM iterations the seed reaches a valley **as good as the best of a full cold
  multistart**. That takes 2–5 ms instead of 0.1–0.2 s (batched) or 10–60 CPU-s (production
  TRF).
- **Seeds do not replace the search; they anchor it.** The analytic seed yields 3–4 valleys.
  A BOM list of 30 alternatives needs the diversity of the atlas or of a (cheap, batched)
  cold multistart. So use both: seeds first, cold starts for breadth.
- Training is cheap offline with B + D: 0.1–0.2 s per target per cell. For 80 cells × 1 000
  targets that is ≈ 3–4 CPU-hours, or ≈ 10 min on 32 cores. The model per cell is tiny (10 ×
  n coefficients, plus optional atlas points). It must be versioned with the cell's structure
  signature and model revision (CONTRACTS §1), like the TF cache.
- Out of the training domain, the fallback is the cold batched multistart. It is always
  affordable after D.
- The atlas can also **learn online**: every successful solve adds its valleys. A re-solve of
  a similar section then starts at the right place, and that is precisely what the
  self-adjusting loop needs.

### 4.6 F — Explicit sensitivity optimisation on the solution manifold

Today no step minimises `sens_score`. The best BOM is whatever the start distribution and the
solver dynamics happen to reach (§4.3). Prototype `lm_core.polish` does a batched
projected-gradient descent of the ranking's own proxy (the 1 % finite-difference formula of
`_assemble`) along {r = 0}: tangent step, then LM retraction.
- **Polishing the Phase-1 valleys fails as a replacement** (`ab_lm.py --variants
  lm-polishrep`). All valleys collapse onto one minimum, and that minimum **lies on the
  envelope boundary**. After cap snapping, Phase 3 then needs a resistor beyond R_max, and
  `BP2-VCVS` produced 0 BOMs. (On `LPn3g-VCVS` it did move the best from 4.08 to 3.93,
  against the baseline's 3.91.) Appending polished copies with a 25 % interior margin
  (`lm-polish`) changed nothing on the three sections tried (BP2-, LPn3g-, LP3-VCVS).
- **Polishing inside Phase 3 works.** With the snapped caps fixed, the resistors move along
  their manifold (dimension ≥ 1), inside the R box with a 10 % margin and within
  MAX_R_RATIO: `BP2-VCVS` best sens **2.32 vs 2.90 baseline** (plain LM 8.29), `LPn3g-MFB`
  **2.09 vs 2.15**, and 6 of 28 sections better overall (§4.3); manifold-0 cells have nothing
  to trade. In scalar form it is slow; batched it adds one Phase-3-sized solve.
- **Rule found on the way: polish only inside the snapper's range.** On `LPn2-MFB` the polish
  drove the LP-MFB notch resistor R8 to its relaxed Phase-3 floor. `R8_RELAX_FACTOR` lets R8
  go down to R_min × 0.02 (`unified_solver_v2.py:53`). But the snapper's E-series grid starts
  at R_min (`discrete_snapper.build_merged_resistor_grid`), so every polished R8 snapped to
  301 Ω, and the best snap cost rose from 0.25 to 29.7. The polish box must be the
  *snappable* range. The same mismatch exists in production whenever Phase 3 returns
  R8 < R_min: the snapper then clamps it to the grid floor. No baseline top-5 BOM showed it.
  The maintainer may want to check it separately; it is outside FS-028.
- Before the solver *optimises* `sens_score` explicitly, settle `dev/SENSITIVITY_SCORE.md` §8.1:
  `a2_expr` is the constant 1 for every 2nd-order cell, so the score only sees the s¹
  coefficient there. An optimiser will exploit whatever the score leaves out.

### 4.7 Considered, not prototyped

| Idea | Assessment |
|---|---|
| numba / C code generation of residuals and LM | Similar per-start gain to D. New heavy dependency (llvmlite), JIT or cache handling under PyInstaller. D gets the gain in plain numpy. Keep as a fallback for any stage that batches badly. |
| JAX (vmap/jit) or GPU | Elegant batching, but a very large dependency and painful Windows/PyInstaller packaging. At these sizes (thousands of 4–11-variable systems) the CPU batch is enough. Not pursued. |
| Hand-derived closed-form design equations per cell (generalising `cells_mfb_bp.analytic_seeds`) | Exact, but it is manual research for each of 80 cells and brittle when cells change. E's learned polynomial reaches the same effect automatically. Keep the hook for thin-sliver cells. |
| Symbolic elimination (Gröbner / resultants) or homotopy continuation for Phase 3 | Would give all real resistor solutions for snapped caps, but the algebra is heavy for the 3rd-order notch cells. Batched LM already makes Phase 3 ≈ 1 ms per task. |
| **Discrete-first search**: enumerate the E-series cap grid (27 values per cap in the default envelope; 20 k combos for 3 caps) and solve the resistors in batch from learned seeds, dropping Phase 1 | Promising after D + E. It covers the envelope-edge optima systematically, which the multistart reaches only by luck (§4.3). It costs ≈ 1–3 s per cell for 3-cap cells, more for 4-cap cells. Research item, Stage 3. |
| Capture–recapture stopping or pruning of the multistart | Unnecessary once a start costs microseconds (D). |
| Threads instead of processes | The batched kernels are numpy-bound. Threading could add some parallelism later, but it is not needed for a one-section solve. |

---

## 5. Relative solving-time coefficients

Aggregate over the 31 sections (Σ new / Σ baseline). The range per section is in brackets.
**CPU** is the core-seconds a section consumes (batch-mode throughput). **Wall** is what the
user waits for, one section at a time. *est.* = assembled from measured stage times, not run
end to end.

**Ideal op-amp** (31 sections; `make_tables.py`, full precision in A.10–A.11):

| Approach | CPU (serial) | Core-s burned on a 32-worker box | Wall, 32 cores | Mean per section |
|---|---:|---:|---:|---|
| Baseline | 1 | 1 | 1 | 48.6 CPU-s; 3.57 s wall |
| A1 TRF, tolerance 1e-7 | 0.74 (0.40–0.96) | 0.81 | 0.88 (0.57–1.06) | 3.15 s wall |
| A2 TRF, 1e-8 + budgets ÷4 | 0.50 (0.31–0.94) | 0.62 | 0.74 (0.45–1.04) | 2.64 s |
| B compile once *est.* | 0.97 (0.75–1.00) | 0.72 | **0.56** (0.10–0.96) | 2.01 s |
| C scalar LM, Phase 1 + 3 | 0.15 (0.04–0.36) | 0.35 | 0.55 (0.12–1.03) | 1.98 s |
| C′ scalar LM, Phase 3 only | 0.47 (0.11–0.95) | 0.60 | 0.72 (0.17–1.09) | 2.57 s |
| C″ scalar LM + Phase-3 polish (prototype) | 0.38 (0.04–1.03) | 0.53 | 0.68 (0.14–1.15) | 2.44 s |
| **D B + batched LM, one core** *est.* | **0.016** (0.006–0.04) | **0.012** | **0.21** (0.05–0.65) | **0.76 s on 1 core** |
| **E D + learned seeds** *est.* | **0.013** (0.002–0.04) | **0.010** | **0.18** (0.02–0.60) | **0.65 s on 1 core** |

**With a real op-amp (TL072)**:

| Approach | CPU (serial) | Core-s burned on a 32-worker box | Wall, 32 cores | Mean per section |
|---|---:|---:|---:|---|
| Baseline | 1 | 1 | 1 | 50.1 CPU-s; 4.72 s wall |
| B compile once *est.* | 0.95 | **0.48** | **0.45** (0.09–0.94) | 2.10 s |
| **D** *est.* | **0.025** | **0.013** | **0.27** (0.05–1.16) | **1.25 s on 1 core** |
| **E** *est.* | 0.023 | 0.011 | 0.24 | 1.15 s on 1 core |

"Core-s burned" adds what the serial CPU hides: each of the 32 workers runs the initializer
(the Phase-1/3 lambdify and the NI re-derivation), so a real pool pays it 32 times. D and E
run in one process, so their wall column compares **one core** against today's **32 cores**.
The one D section slower than today (`N2-VCVS` with TL072, 1.16) is bound by its 3.3 s of NI
correction run serially. The NI stage should therefore be batched, or keep a small persistent
pool, in S2-1/S2-2.

Per section, in absolute terms: today's 32-core model is **0.27–17.5 s, median 1.8 s**
(TL072: 0.37–23.5 s, median 2.0 s). D is **0.05–5.4 s, median 0.30 s on one core** (TL072:
0.10–5.8 s, median 0.73 s). The tail in both is `LPn3g-VCVS`: `3LPn-gained` plus its `+R7`
twin, 820 Phase-3 tasks.

How the estimates are assembled (`make_tables.coeff_table`):
- **B** = the measured Phase-1/3/ZM/NI task times (scheduled over 32 workers) + snap +
  harvest. Derivation, lambdify, pool start-up and per-worker initialisation are removed.
- **D** = measured batched Phase 1 + 2 × measured batched Phase 3 (the second one is an
  allowance for the R5 ladder, which was not prototyped) + the measured non-`least_squares`
  bookkeeping + NI + snap, all on one core.
- **E** = D with Phase 1 replaced by learned seeds (30 ms per cell) plus a quarter of the cold
  batch, kept for diversity.
- The Phase-3 batch timings come from a task list produced by an LM Phase 1. It is
  representative of, but not identical to, the production task list: it has the same order
  of tasks, and on the same list the batch matches TRF's accepted-task count in every section.

How to read it for the self-adjusting batch mode: today one section occupies **all 32 cores
for 0.3–24 s, median ≈ 2 s** (model; more on Windows), and the sections run one after
another. After B + D (+ E), one section needs **one core for 0.05–6 s, median 0.3–0.7 s**
(*est.*). So a 6-section filter with a 3-attempt retry ladder is ≈ 18 single-core solves,
which spread over 32 cores **finish in about the time one section takes today**.

---

## 6. Result quality and the tolerance question

- **Tolerance:** with LM, convergence is quadratic, so the solves end at |r| < 1e-10 (0.1 ppm)
  at no extra cost. There is no need to trade precision for speed. The Phase-1/3 acceptance
  gates and E-series snapping, which decide the BOM, stay exactly as they are.
- **The quality risk is sampling, not precision.** Any change of local solver changes which
  valleys the multistart visits. This is true for TRF with other tolerances too (§4.1: 6 of
  28 best BOMs moved with budget cuts). The evidence here: Phase 3 is near-neutral under LM
  (24 of 28 best sensitivities identical, 4 within +3.6 %), and Phase 1 is ± (with one large
  regression, BP2-VCVS). F recovers and beats the baseline where
  the resistor manifold has freedom. The envelope-edge optima (LPn3g-VCVS) need either a
  boundary-aware step or the discrete-first search (§4.7).
- **Validation rule proposed for Stage 2:** on this benchmark set (and FS-016's BOM
  baselines, if they have landed), each section's best `sens_score` must be ≤ baseline + 1 %,
  its best `snap_cost` ≤ baseline, and its BOM count ≥ baseline. Every deviation must be listed
  and accepted by the maintainer. Also required: `verify.py` passes, and one bundled-exe run.

---

## 7. Proposed Stage-2 items

Each accepted item becomes its own FS item; IDs are assigned when they are opened.

| Item | Scope | Tiers | Gain | Risk | Effort | Depends |
|---|---|---|---|---|---|---|
| **S2-1 Compile-once cell kernels** | Design-parametric ideal residuals, Jacobians and a1/a2, derived once per cell and cached as generated code, with keys per CONTRACTS §1 plus an ideal-model revision. Non-ideal response and TF loaded without re-derivation in every mode. `run_synthesis`, the snapper and `solve_nonideal` stop re-deriving. The NI correction runs in-process or in a persistent pool. | B, C | removes ≈ half of today's wall and the 32× initializer CPU; **results identical** | low–med | high | — |
| **S2-2 Batched LM solver core** | New module: vectorized projected log-space LM. Phase 1 per cell in one batch; Phase 3 (hints → fallback) in batches; ZM path; R5 ladder; F's Phase-3 sensitivity polish. `run_synthesis` keeps its interface. No process pool for the solve. Benchmark-set validation per §6. | C | Phase 1 ≈ 91× (35–427×), Phase 3 ≈ 187× (7–1200×) less CPU; one section on one core | med | xhigh | S2-1 (hard) |
| **S2-3 Learned seeds** | Offline trainer (sweep of dimensionless targets per cell, batched), a small per-cell model (polynomial seed + atlas) shipped as data, an online atlas from the user's own solves, and fallback to cold starts. Seeds are additive to the start set, like `analytic_seeds`. | B, C | Phase 1 → ms; robust thin-region cells; instant re-solves | med | high | S2-2 (hard) |
| **S2-4 Self-adjusting batch orchestrator** | Solve all sections in parallel (one process each). Failure classification from solver telemetry. Retry ladder (§8). Report what was changed. | C, D | the goal | med | high | S2-2 (hard), S2-3 (soft) |
| *(fallback)* TRF budget trim | `max_nfev ÷ 4`, `xtol/ftol` 1e-8. Only if Stage 2 is postponed. | C | 2.0× CPU | low–med | low | — |

Scoring interplay: settle `SENSITIVITY_SCORE.md` §8.1 before S2-2's polish step goes live.
That is a separate decision, not a performance item.

---

## 8. The self-adjusting orchestrator (target sketch)

What makes it possible is the cost of one attempt. It must drop from "all cores for seconds"
to "one core for a fraction of a second". The design outline for S2-4:

1. **Telemetry per attempt**, collected by the S2-2 core at no cost: Phase-1 converged
   starts and valleys per cell, Phase-3 tasks reaching acceptance, rejections by gate (R range,
   MAX_R_RATIO, gain, pole/notch tolerance), and best sens / snap cost.
2. **Failure classes → ladder rungs**. Each rung is one fast solve, and the rungs of
   different sections run in parallel:
   - no Phase-1 valley → learned seeds for the target (S2-3), then 10× starts, then the
     solvability probe;
   - valleys but no Phase-3 acceptance (cap snapping) → parallel-cap twins (`-C1s`),
     finer C series, a larger C window;
   - gates reject (MAX_R_RATIO, gain) → twin cells (+R7 / +R8 / QE) and widening within
     user-set limits;
   - poor quality → more starts plus the F polish, or another family (only if the user
     allows it).
3. **Budget and stop:** first success per section, or N rungs, or T seconds. Report which
   rung succeeded and what changed (envelope, cell, family).
4. **Learning:** every success feeds the atlas (S2-3), so the next similar section starts
   at a known valley.

Today's three no-BOM sections (§2.5) are natural acceptance tests for the ladder.

---

## 9. Risks

- **Numerical behaviour changes** (S2-2): valleys and BOMs will differ, sometimes better and
  sometimes worse. Mitigation: F, the §6 validation rule, and the baseline JSON committed here
  (`results/baseline_balanced.json`) as the reference. S2-1 alone changes no results.
- **Multiprocessing fragility** (`mp_fix.py`, `pool_utils.py`): the plan *reduces* it. The
  solve no longer needs per-section pools. Batch mode needs one persistent pool, of the kind
  `pool_utils.get_process_pool` already manages.
- **Cache and data invalidation:** compiled kernels and seed models are keyed like the TF
  cache (structure signature + model revision). A cell-model change must bump them.
  CONTRACTS §1 gains a line.
- **Windows performance is unmeasured here.** Run the §10 pool probes on the 32-core box
  before S2-1 to confirm the fixed-cost share.
- **Scope creep:** F and the discrete-first search touch result quality. Keep them behind
  the §6 validation rule and the maintainer's acceptance.

---

## 10. Reproduce

From the repository root (Python 3.11/3.12, `pip install -r requirements.txt`):

```bash
# baseline (instrumented, serial) -- results/baseline_{balanced,fast,thorough}.json, cold_fast.json
python dev/fs028/bench_sections.py --preset Balanced --opamp ideal tl072 --warm --jobs 3 --tag baseline_balanced
python dev/fs028/bench_sections.py --preset Fast --opamp ideal --warm --jobs 3 --tag baseline_fast
python dev/fs028/bench_sections.py --preset Thorough --opamp ideal --warm --jobs 3 --tag baseline_thorough
python dev/fs028/bench_sections.py --preset Fast --opamp tl072 --cold --cases LPn3-VCVS HPn3-VCVS LP2-VCVS N2-VCVS --tag cold_fast
# real pools on the 32-core Windows box (not run here)
python dev/fs028/bench_sections.py --mode pool --spawn --cores 32 --cases LPn3-MFB HPn3-VCVS
python dev/fs028/probe_pool.py LPn3-MFB HPn3-VCVS LPn3g-VCVS --workers 32
# root causes
python dev/fs028/probe_phase1.py LPn3-VCVS LPn3-MFB LPn3-AM          # starts, max_nfev hits, early abort
python dev/fs028/probe_basin.py LPn3-VCVS                            # TRF vs LM near a root
# variants through the full pipeline, vs baseline
python dev/fs028/ab_lm.py --all --variants trf-loose-tol trf-loose lm lm-p3 lm-p3pol --jobs 3
python dev/fs028/ab_lm.py BP2-VCVS LPn3g-VCVS LP3-VCVS --variants lm-p1 lm-polish lm-polishrep
python dev/fs028/ab_lm.py LPn3g-VCVS --variants lm --preset Thorough
python dev/fs028/compare_ab.py results/baseline_balanced.json results/ab_lm_Balanced_ideal.json --variant lm
# batched solver and learned seeds
python dev/fs028/probe_batch.py <all 31 case ids> --methods batch    # Phase 1 (ids: fs028_common.bench_cases)
python dev/fs028/probe_batch.py LPn2-VCVS HPn2-MFB HPn3-MFB --preset X10 --methods batch
python dev/fs028/probe_batch_p3.py --all --p1 lm                     # Phase 3, TRF vs batch on the same tasks
python dev/fs028/probe_atlas.py --cell 3LPn-unity --train 300 --test 60
python dev/fs028/probe_atlas.py --cell 2BP --train 200 --test 40
python dev/fs028/probe_atlas.py --cell 3LPn-MFB --train 200 --test 40
# tables of this note (Appendix A)
python dev/fs028/make_tables.py
```

The scripts run the TF cache in a private work dir (`$FS028_WORK`, default
`<tmp>/fs028_work`), so nothing is written next to the app.

---

## 11. Stage 2 — S2-1 compile-once cell kernels (landed 2026-09-30)

**What changed.** Each cell's ideal case is derived once with symbolic targets
(`tf_derivation_v2.get_templates`, cached in the TF cache); the solver pipeline builds its
cases from those templates (`design_cases`) in `run_synthesis`, the snapper and
`solve_nonideal` — no per-design derivation anywhere. Lambdified functions are kept as
generated Python source (`cell_kernels.py`) and Phase-1/3 workers rebuild them by `exec`
(~ms) from a per-run packs file: no sympify, no lambdify in a worker. (First cut passed the
sources in the pool initargs; on Windows spawn that serialized the start of all 32 workers
— `Process.start()` waits until each child has read initargs larger than the pipe buffer —
and showed up in the app as minutes of near-idle CPU per 3rd-order solve. Found by the
maintainer on 2026-10-01, fixed the same day, now a CONTRACTS §7 rule.) The
non-ideal correction runs in-process on the main process's response memo; its per-section
pool is gone. Checks: `python dev/fs028/check_kernels.py`; `python verify.py` passes; app
run (Elliptic LP, VCVS + TL072H and MFB sections, BOM pick, Resulting Response with group
delay) clean.

**Correction to row B ("same math → results identical").** Design-parametric residuals
(targets as arguments) agree with the per-design ones to ≤ 3.4e-13 over all 80 cells, yet
they do **not** give identical BOMs: sympy no longer folds the numbers into the expression,
and the TRF multistart is chaotic — rounding-level differences re-sample which valleys the
failing starts end in. Measured over the 31 sections (Balanced, ideal): same BOM set in
18 / 31, same top-5 in 13, same best BOM in 20; best sens 26 same / 3 better / 2 worse. That
is a reseed, not a quality change, but it is a result change bought for ≈ 10 % of speed, so
the TRF path uses **per-design residual kernels** instead: the numeric targets substituted
into the template (`build_ideal`'s own last step, so the expression is srepr-identical for
every cell) and lambdified once per design in the orchestrator (0.01–2.7 s per cell,
memoized). All 4 308 solve-path kernels (80 cells × designs × Equalize variants) are
**source-identical** to the old code's, and the design-parametric group stays built and
verified for S2-2 / S2-3, whose solvers change results anyway and are validated per §6.

**Result identity.** Every ideal, corrected and snapped row of all 31 sections, ideal and
TL072, is bit-identical to HEAD (every field, exact float compare).

**Time, instrumented (serial CPU, 32-core model, warm, same design).**

| | ideal: HEAD → S2-1 | TL072: HEAD → S2-1 |
|---|---|---|
| 32-core model, sum over 31 sections | 119.1 → 58.0 s (0.49×) | 186.4 → 68.1 s (0.37×) |
| per section median / max | 2.48 / 18.6 → 1.37 / 8.5 s | 2.96 / 29.5 → 1.59 / 8.3 s |
| main process (derivation, task build) | 21.4 → 4.2 s | 34.2 → 3.6 s |
| Phase-1/3 worker initializer | 20.3 → 0.0 s | 26.3 → 0.0 s |
| non-ideal: pool initializer + tasks | — | 37.4 + 2.1 → 0.0 + 13.3 s (serial, in-process) |
| snapper re-derivation (fs_main) | 19.0 → 0.0 s | 15.3 → 0.0 s |

**Time, real pools on the maintainer's box** (i9-14900HX: 8 P + 16 E cores, 32 threads;
Windows spawn, 32 workers). Fixed per-section cost, isolated by running the full pipeline
with a near-zero search budget; new design in a warm app ("first") and re-solve; medians of
2 alternating rounds:

| Section | ideal first | ideal re-solve | TL072 first | TL072 re-solve |
|---|---|---|---|---|
| HPn3-VCVS | 11.4 → 4.4 s | 10.2 → 3.7 s | 30.4 → 5.0 s | 32.7 → 4.3 s |
| LPn3-MFB | 10.4 → 3.4 s | 10.8 → 3.3 s | 22.5 → 3.9 s | 22.9 → 3.9 s |
| LPn3g-VCVS | 21.7 → 14.4 s | 20.6 → 10.9 s | 31.8 → 14.5 s | 33.9 → 11.0 s |
| N2-MFB | 3.0 → 2.6 s | 3.1 → 2.5 s | 6.0 → 2.6 s | 6.2 → 2.7 s |
| LP2-VCVS | 1.8 → 1.9 s | 1.9 → 1.9 s | 4.0 → 2.0 s | 4.0 → 2.0 s |
| HPn3-AM | 6.5 → 6.3 s | 8.1 → 7.6 s | 8.6 → 6.6 s | 10.5 → 7.9 s |

End-to-end Balanced solves on the same box (single runs) move the same way where the fixed
cost dominated (TL072: LPn3-MFB 27.0 → 8.2 s, HPn3-VCVS 34.7 → 12.1 s, LP2-VCVS 7.1 → 3.5 s)
and stay inside run-to-run noise where the search dominates (LPn3g-VCVS ≈ 40–50 s, HPn3-AM
≈ 20 s either way). On this hybrid CPU the 32-core model is optimistic for the search
phases: E-cores and SMT make 32 workers far less than 32× one P-core, and repeat solves of
one design differ by up to ~20 %.

**What is left of the fixed cost** (what S2-2 removes): the spawn + import of 32 workers per
section (≈ 1.8 s floor, LP2-VCVS); the per-design residual/Jacobian lambdify in the
orchestrator for 3rd-order VCVS notch cells (LPn3g-VCVS: 3LPn-gained + its R7 twin,
3.6–7.8 s by core type; free on a re-solve); the serial non-ideal correction (≤ 2.2 s per
section). The Phase-3 fallback storms (§3.3) are search, not fixed cost — LPn3g-VCVS keeps
≈ 25 CPU-s of Phase 3 even at a 2-start budget. Out of scope and unchanged: the failure-path
solvability probe (`solvability_probe`) still derives per call; it could use the templates.

**Reproduce:** `dev/fs028/check_kernels.py`; the identity and timing runs used the §10
harness (`fs028_common.run_section`, instrumented) with a snapshot of every row per section
against a `git archive HEAD` copy, and real-pool timings via `run_section(instrument=False)`
with 32 spawn workers.

---

## Appendix A — generated tables

`python dev/fs028/make_tables.py` prints these from `dev/fs028/results/`.

### A.1 Baseline, Balanced, op-amp ideal (serial CPU-seconds, this machine)

| Case | Cells | Serial CPU | Phase 1 | Phase 3 | Derive (main) | Init/worker | 32-core model | BOMs |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| LPn3-VCVS | 3LPn-unity | 38.0 | 36.7 | 0.0 | 0.5 | 0.72 | 2.6 | 8 |
| LPn3-MFB | 3LPn-MFB | 53.1 | 13.2 | 29.5 | 8.5 | 0.89 | 12.1 | 30 |
| LPn3-AM | 3LPn-AM, 3LPn-AM-C1s | 58.8 | 19.5 | 38.4 | 0.3 | 0.28 | 2.9 | 12 |
| LPn2-VCVS | 2LPn-unity | 16.4 | 15.9 | 0.0 | 0.0 | 0.40 | 1.2 | 0 |
| LPn2-MFB | 2LPn-MFB | 11.0 | 5.9 | 4.3 | 0.2 | 0.30 | 1.6 | 30 |
| LPn2-AM | 2LPn-AM, 2LPn-AM-C1s | 29.1 | 27.8 | 1.0 | 0.0 | 0.08 | 1.6 | 12 |
| LPn3g-VCVS | 3LPn-gained | 311.0 | 58.0 | 246.0 | 0.8 | 5.46 | 17.5 | 30 |
| LPn3g-MFB | 3LPn-MFB | 38.3 | 11.7 | 17.0 | 7.8 | 0.82 | 11.8 | 30 |
| LPn3g-AM | 3LPn-AM, 3LPn-AM-C1s | 46.3 | 21.4 | 24.0 | 0.3 | 0.26 | 2.5 | 12 |
| LP2-VCVS | 2LP-unity | 4.6 | 4.2 | 0.2 | 0.0 | 0.04 | 0.6 | 30 |
| LP2-MFB | 2LP-MFB, 2LP-MFB-QE | 21.3 | 19.1 | 1.8 | 0.2 | 0.23 | 1.3 | 30 |
| LP2-AM | 2LP-AM, 2LP-AM2 | 7.0 | 6.8 | 0.0 | 0.0 | 0.07 | 0.5 | 4 |
| LP3-VCVS | 3LP-unity | 10.1 | 6.8 | 2.8 | 0.0 | 0.14 | 0.9 | 30 |
| LP3-MFB | 3LP-MFB, 3LP-MFB-QE | 34.9 | 15.6 | 17.3 | 1.3 | 0.45 | 3.3 | 30 |
| LP3-AM | 3LP-AM, 3LP-AM2 | 36.8 | 30.5 | 5.2 | 0.2 | 0.32 | 2.5 | 30 |
| HPn3-VCVS | 3HPn-unity, 3HPn-unity+R8 | 91.8 | 46.3 | 40.6 | 2.2 | 1.96 | 7.9 | 30 |
| HPn3-MFB | 3HPn-MFB2 | 22.0 | 16.5 | 0.0 | 4.8 | 0.60 | 6.1 | 0 |
| HPn3-AM | 3HPn-AM, 3HPn-AM-C1s | 393.2 | 22.1 | 370.2 | 0.3 | 0.25 | 14.1 | 12 |
| HPn2-VCVS | 2HPn-unity | 6.2 | 4.1 | 1.8 | 0.1 | 0.19 | 0.8 | 30 |
| HPn2-MFB | 2HPn-MFB, 2HPn-MFB2 | 46.1 | 45.0 | 0.0 | 0.3 | 0.68 | 2.5 | 0 |
| HPn2-AM | 2HPn-AM, 2HPn-AM-C1s | 10.1 | 8.9 | 0.8 | 0.1 | 0.08 | 1.3 | 14 |
| HP2-VCVS | 2HP-unity | 4.4 | 4.1 | 0.1 | 0.0 | 0.05 | 0.5 | 30 |
| HP2-MFB | 2HP-MFB, 2HP-MFB-QE | 29.2 | 15.1 | 13.5 | 0.2 | 0.22 | 1.8 | 30 |
| HP2-AM | 2HP-AM, 2HP-AM-C1s | 12.4 | 11.8 | 0.4 | 0.0 | 0.06 | 0.8 | 14 |
| BP2-VCVS | 2BP, 2BP-atten | 21.7 | 18.3 | 2.9 | 0.1 | 0.23 | 1.3 | 30 |
| BP2-MFB | 2BP-MFB, 2BP-MFB-QE | 31.5 | 27.7 | 3.3 | 0.1 | 0.29 | 1.9 | 30 |
| BP2-AM | 2BP-AM | 3.1 | 2.9 | 0.0 | 0.0 | 0.04 | 0.3 | 2 |
| N2-VCVS | 2N | 35.2 | 11.6 | 22.1 | 0.8 | 0.42 | 2.9 | 30 |
| N2-MFB | 2N-MFB | 16.2 | 9.9 | 5.7 | 0.2 | 0.24 | 1.7 | 30 |
| N2-AM | 2N-AM, 2N-AM-C1s | 10.4 | 9.4 | 0.7 | 0.0 | 0.08 | 0.9 | 14 |
| BP1LP-MFB | 2BP1LP-MFB, 2BP1LP-MFB-QE | 55.7 | 25.5 | 29.0 | 0.2 | 0.52 | 3.2 | 30 |

Total serial CPU 1506 s (Phase 1 38 %, Phase 3 58 %); 32-core model median 1.8 s, mean 3.6 s, max 17.5 s.

### A.2 Baseline, Balanced, op-amp tl072 (serial CPU-seconds, this machine)

| Case | Cells | Serial CPU | Phase 1 | Phase 3 | Derive (main) | Init/worker | 32-core model | BOMs |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| LPn3-VCVS | 3LPn-unity | 39.3 | 34.4 | 0.0 | 0.5 | 4.19 | 6.1 | 8 |
| LPn3-MFB | 3LPn-MFB | 57.1 | 12.8 | 27.7 | 8.2 | 5.98 | 17.4 | 30 |
| LPn3-AM | 3LPn-AM, 3LPn-AM-C1s | 57.6 | 18.4 | 37.4 | 0.2 | 0.75 | 3.4 | 12 |
| LPn2-VCVS | 2LPn-unity | 15.4 | 15.0 | 0.0 | 0.0 | 0.26 | 1.0 | 0 |
| LPn2-MFB | 2LPn-MFB | 13.2 | 6.6 | 4.8 | 0.2 | 0.56 | 1.9 | 30 |
| LPn2-AM | 2LPn-AM, 2LPn-AM-C1s | 29.1 | 27.0 | 1.3 | 0.0 | 0.18 | 1.8 | 12 |
| LPn3g-VCVS | 3LPn-gained | 328.7 | 61.0 | 254.7 | 0.7 | 11.29 | 23.5 | 30 |
| LPn3g-MFB | 3LPn-MFB | 46.0 | 12.5 | 18.3 | 7.6 | 5.72 | 16.8 | 30 |
| LPn3g-AM | 3LPn-AM, 3LPn-AM-C1s | 46.0 | 20.7 | 23.8 | 0.2 | 0.61 | 2.9 | 12 |
| LP2-VCVS | 2LP-unity | 4.4 | 3.9 | 0.1 | 0.0 | 0.15 | 0.7 | 30 |
| LP2-MFB | 2LP-MFB, 2LP-MFB-QE | 22.1 | 19.5 | 1.8 | 0.1 | 0.36 | 1.4 | 30 |
| LP2-AM | 2LP-AM, 2LP-AM2 | 6.7 | 6.3 | 0.0 | 0.0 | 0.22 | 0.6 | 4 |
| LP3-VCVS | 3LP-unity | 11.0 | 7.7 | 2.3 | 0.0 | 0.36 | 1.2 | 30 |
| LP3-MFB | 3LP-MFB, 3LP-MFB-QE | 38.7 | 17.1 | 19.0 | 1.3 | 0.71 | 3.7 | 30 |
| LP3-AM | 3LP-AM, 3LP-AM2 | 39.9 | 31.5 | 5.8 | 0.1 | 0.60 | 2.9 | 30 |
| HPn3-VCVS | 3HPn-unity, 3HPn-unity+R8 | 101.7 | 45.0 | 40.7 | 2.5 | 11.61 | 18.1 | 30 |
| HPn3-MFB | 3HPn-MFB2 | 21.7 | 15.9 | 0.0 | 5.1 | 0.59 | 6.3 | 0 |
| HPn3-AM | 3HPn-AM, 3HPn-AM-C1s | 388.3 | 23.3 | 363.3 | 0.2 | 0.60 | 14.3 | 12 |
| HPn2-VCVS | 2HPn-unity | 7.7 | 4.5 | 1.9 | 0.1 | 0.58 | 1.3 | 30 |
| HPn2-MFB | 2HPn-MFB, 2HPn-MFB2 | 47.2 | 46.4 | 0.0 | 0.3 | 0.51 | 2.4 | 0 |
| HPn2-AM | 2HPn-AM, 2HPn-AM-C1s | 10.0 | 8.3 | 0.8 | 0.0 | 0.22 | 1.3 | 14 |
| HP2-VCVS | 2HP-unity | 4.5 | 4.0 | 0.1 | 0.0 | 0.16 | 0.6 | 30 |
| HP2-MFB | 2HP-MFB, 2HP-MFB-QE | 31.0 | 15.5 | 14.6 | 0.1 | 0.39 | 2.0 | 30 |
| HP2-AM | 2HP-AM, 2HP-AM-C1s | 11.4 | 10.3 | 0.5 | 0.0 | 0.16 | 1.0 | 14 |
| BP2-VCVS | 2BP, 2BP-atten | 22.9 | 18.9 | 2.4 | 0.1 | 0.54 | 1.9 | 30 |
| BP2-MFB | 2BP-MFB, 2BP-MFB-QE | 31.2 | 26.9 | 3.6 | 0.1 | 0.33 | 2.1 | 30 |
| BP2-AM | 2BP-AM | 2.6 | 2.3 | 0.0 | 0.0 | 0.14 | 0.4 | 2 |
| N2-VCVS | 2N | 37.0 | 11.7 | 20.4 | 0.7 | 0.72 | 3.3 | 30 |
| N2-MFB | 2N-MFB | 16.1 | 9.4 | 5.7 | 0.1 | 0.43 | 1.8 | 30 |
| N2-AM | 2N-AM, 2N-AM-C1s | 10.3 | 8.8 | 0.6 | 0.0 | 0.23 | 1.1 | 14 |
| BP1LP-MFB | 2BP1LP-MFB, 2BP1LP-MFB-QE | 55.5 | 25.7 | 28.2 | 0.2 | 0.68 | 3.4 | 30 |

Total serial CPU 1554 s (Phase 1 37 %, Phase 3 57 %); 32-core model median 2.0 s, mean 4.7 s, max 23.5 s.

### A.3 Search presets (ideal op-amp)

| Preset | Sections | Serial CPU (vs Balanced, same sections) | 32-core model mean / median / max (vs Balanced) | BOMs |
|---|---:|---|---|---:|
| Fast | 31 | 865 s (0.57x) | 2.84 / 1.37 / 13.1 s (0.80x) | 644 |
| Balanced | 31 | 1506 s (1.00x) | 3.57 / 1.80 / 17.5 s (1.00x) | 644 |
| Thorough | 31 | 2268 s (1.51x) | 4.33 / 2.51 / 24.2 s (1.21x) | 644 |

### A.4 Variant `trf-loose-tol` vs baseline (Balanced, ideal)

| Case | CPU base | CPU var | Speed-up | BOMs b/v | Best sens b/v | Best snap cost b/v |
|---|---:|---:|---:|---:|---:|---:|
| LPn3-VCVS | 38.0 | 32.67 | 1.2x | 8/8 | 1.959/1.959 | 0.0224/0.0224 |
| LPn3-MFB | 53.1 | 45.90 | 1.2x | 30/30 | 2.024/2.024 | 0.0997/0.0819 |
| LPn3-AM | 58.8 | 41.49 | 1.4x | 12/12 | 2.348/2.348 | 1.05/0.438 |
| LPn2-VCVS | 16.4 | 13.18 | 1.2x | 0/0 | nan/nan | nan/nan |
| LPn2-MFB | 11.0 | 8.88 | 1.2x | 30/30 | 1.431/1.431 | 0.377/0.159 |
| LPn2-AM | 29.1 | 15.47 | 1.9x | 12/12 | 1.400/1.400 | 0.409/0.409 |
| LPn3g-VCVS | 311.0 | 259.07 | 1.2x | 30/30 | 3.908/3.908 | 0.109/0.0273 |
| LPn3g-MFB | 38.3 | 35.57 | 1.1x | 30/30 | 2.147/2.143 | 0.133/0.116 |
| LPn3g-AM | 46.3 | 34.35 | 1.3x | 12/12 | 2.351/2.351 | 0.95/0.95 |
| LP2-VCVS | 4.6 | 3.85 | 1.2x | 30/30 | 1.213/1.213 | 0.0496/0.0218 |
| LP2-MFB | 21.3 | 18.81 | 1.1x | 30/30 | 1.161/1.144 | 0.0285/0.00868 |
| LP2-AM | 7.0 | 5.64 | 1.2x | 4/4 | 1.400/1.400 | 0.0287/0.0287 |
| LP3-VCVS | 10.1 | 8.86 | 1.1x | 30/30 | 1.912/1.912 | 3.08/0.031 |
| LP3-MFB | 34.9 | 30.12 | 1.2x | 30/30 | 1.839/1.839 | 0.243/0.029 |
| LP3-AM | 36.8 | 16.35 | 2.3x | 30/30 | 2.136/2.136 | 0.165/0.165 |
| HPn3-VCVS | 91.8 | 73.56 | 1.2x | 30/30 | 1.868/1.867 | 0.144/0.119 |
| HPn3-MFB | 22.0 | 15.23 | 1.4x | 0/0 | nan/nan | nan/nan |
| HPn3-AM | 393.2 | 280.91 | 1.4x | 12/12 | 2.078/2.078 | 1.64/0.887 |
| HPn2-VCVS | 6.2 | 5.97 | 1.0x | 30/30 | 1.213/1.213 | 0.212/0.167 |
| HPn2-MFB | 46.1 | 18.63 | 2.5x | 0/0 | nan/nan | nan/nan |
| HPn2-AM | 10.1 | 6.75 | 1.5x | 14/14 | 1.400/1.400 | 0.479/0.396 |
| HP2-VCVS | 4.4 | 2.90 | 1.5x | 30/30 | 1.213/1.213 | 0.222/0.0772 |
| HP2-MFB | 29.2 | 22.23 | 1.3x | 30/30 | 1.341/1.341 | 0.495/0.0783 |
| HP2-AM | 12.4 | 6.39 | 1.9x | 14/14 | 1.400/1.400 | 1.04/0.65 |
| BP2-VCVS | 21.7 | 14.51 | 1.5x | 30/30 | 2.902/2.902 | 0.0831/0.0832 |
| BP2-MFB | 31.5 | 20.13 | 1.6x | 30/30 | 1.213/1.213 | 0.24/0.0704 |
| BP2-AM | 3.1 | 2.34 | 1.3x | 2/2 | 1.400/1.400 | 0.908/0.908 |
| N2-VCVS | 35.2 | 22.21 | 1.6x | 30/30 | 1.371/1.371 | 3.47/0.282 |
| N2-MFB | 16.2 | 10.18 | 1.6x | 30/30 | 1.213/1.213 | 0.949/0.798 |
| N2-AM | 10.4 | 6.24 | 1.7x | 14/14 | 1.400/1.400 | 0.488/0.488 |
| BP1LP-MFB | 55.7 | 41.99 | 1.3x | 30/30 | 2.072/2.072 | 0.372/0.372 |

Total serial CPU 1506 s -> 1120 s (x1.3); best sens better 2, same 26, worse 0 (0.1 % band).

### A.5 Variant `trf-loose` vs baseline (Balanced, ideal)

| Case | CPU base | CPU var | Speed-up | BOMs b/v | Best sens b/v | Best snap cost b/v |
|---|---:|---:|---:|---:|---:|---:|
| LPn3-VCVS | 38.0 | 11.62 | 3.3x | 8/8 | 1.959/1.959 | 0.0224/0.0224 |
| LPn3-MFB | 53.1 | 28.19 | 1.9x | 30/30 | 2.024/2.024 | 0.0997/0.0997 |
| LPn3-AM | 58.8 | 38.87 | 1.5x | 12/12 | 2.348/2.348 | 1.05/0.438 |
| LPn2-VCVS | 16.4 | 6.63 | 2.5x | 0/0 | nan/nan | nan/nan |
| LPn2-MFB | 11.0 | 7.77 | 1.4x | 30/30 | 1.431/1.449 | 0.377/0.159 |
| LPn2-AM | 29.1 | 15.93 | 1.8x | 12/12 | 1.400/1.400 | 0.409/0.409 |
| LPn3g-VCVS | 311.0 | 148.17 | 2.1x | 30/30 | 3.908/4.003 | 0.109/0.104 |
| LPn3g-MFB | 38.3 | 29.74 | 1.3x | 30/30 | 2.147/2.143 | 0.133/0.118 |
| LPn3g-AM | 46.3 | 31.81 | 1.5x | 12/12 | 2.351/2.351 | 0.95/0.95 |
| LP2-VCVS | 4.6 | 3.71 | 1.2x | 30/30 | 1.213/1.213 | 0.0496/0.0218 |
| LP2-MFB | 21.3 | 12.03 | 1.8x | 30/30 | 1.161/1.144 | 0.0285/0.0107 |
| LP2-AM | 7.0 | 5.73 | 1.2x | 4/4 | 1.400/1.400 | 0.0287/0.0287 |
| LP3-VCVS | 10.1 | 6.34 | 1.6x | 30/30 | 1.912/1.912 | 3.08/0.031 |
| LP3-MFB | 34.9 | 19.52 | 1.8x | 30/30 | 1.839/1.839 | 0.243/0.029 |
| LP3-AM | 36.8 | 14.01 | 2.6x | 30/30 | 2.136/2.135 | 0.165/0.165 |
| HPn3-VCVS | 91.8 | 42.77 | 2.1x | 30/30 | 1.868/1.871 | 0.144/0.127 |
| HPn3-MFB | 22.0 | 13.05 | 1.7x | 0/0 | nan/nan | nan/nan |
| HPn3-AM | 393.2 | 162.10 | 2.4x | 12/12 | 2.078/2.078 | 1.64/0.887 |
| HPn2-VCVS | 6.2 | 5.86 | 1.1x | 30/30 | 1.213/1.213 | 0.212/0.167 |
| HPn2-MFB | 46.1 | 16.56 | 2.8x | 0/0 | nan/nan | nan/nan |
| HPn2-AM | 10.1 | 7.73 | 1.3x | 14/14 | 1.400/1.400 | 0.479/0.396 |
| HP2-VCVS | 4.4 | 3.78 | 1.2x | 30/30 | 1.213/1.213 | 0.222/0.0772 |
| HP2-MFB | 29.2 | 18.29 | 1.6x | 30/30 | 1.341/1.341 | 0.495/0.0783 |
| HP2-AM | 12.4 | 8.34 | 1.5x | 14/14 | 1.400/1.400 | 1.04/0.65 |
| BP2-VCVS | 21.7 | 11.76 | 1.8x | 30/30 | 2.902/1.684 | 0.0831/0.0832 |
| BP2-MFB | 31.5 | 14.26 | 2.2x | 30/30 | 1.213/1.213 | 0.24/0.0704 |
| BP2-AM | 3.1 | 2.31 | 1.3x | 2/2 | 1.400/1.400 | 0.908/0.908 |
| N2-VCVS | 35.2 | 15.84 | 2.2x | 30/30 | 1.371/1.372 | 3.47/0.369 |
| N2-MFB | 16.2 | 9.87 | 1.6x | 30/30 | 1.213/1.213 | 0.949/0.798 |
| N2-AM | 10.4 | 7.22 | 1.4x | 14/14 | 1.400/1.400 | 0.488/0.488 |
| BP1LP-MFB | 55.7 | 27.67 | 2.0x | 30/30 | 2.072/2.072 | 0.372/0.372 |

Total serial CPU 1506 s -> 747 s (x2.0); best sens better 3, same 22, worse 3 (0.1 % band).

### A.6 Variant `lm` vs baseline (Balanced, ideal)

| Case | CPU base | CPU var | Speed-up | BOMs b/v | Best sens b/v | Best snap cost b/v |
|---|---:|---:|---:|---:|---:|---:|
| LPn3-VCVS | 38.0 | 3.36 | 11.3x | 8/8 | 1.959/1.959 | 0.0224/0.0224 |
| LPn3-MFB | 53.1 | 11.47 | 4.6x | 30/30 | 2.024/1.994 | 0.0997/0.0476 |
| LPn3-AM | 58.8 | 15.76 | 3.7x | 12/12 | 2.348/2.347 | 1.05/0.609 |
| LPn2-VCVS | 16.4 | 1.72 | 9.6x | 0/0 | nan/nan | nan/nan |
| LPn2-MFB | 11.0 | 2.02 | 5.4x | 30/30 | 1.431/1.249 | 0.377/0.245 |
| LPn2-AM | 29.1 | 1.21 | 24.1x | 12/12 | 1.400/1.400 | 0.409/0.409 |
| LPn3g-VCVS | 311.0 | 66.49 | 4.7x | 30/30 | 3.908/4.075 | 0.109/0.0353 |
| LPn3g-MFB | 38.3 | 11.20 | 3.4x | 30/30 | 2.147/2.164 | 0.133/0.0393 |
| LPn3g-AM | 46.3 | 16.81 | 2.8x | 12/12 | 2.351/2.357 | 0.95/0.893 |
| LP2-VCVS | 4.6 | 0.48 | 9.6x | 30/25 | 1.213/1.213 | 0.0496/0.0218 |
| LP2-MFB | 21.3 | 2.67 | 8.0x | 30/30 | 1.161/1.155 | 0.0285/0.0107 |
| LP2-AM | 7.0 | 1.00 | 7.1x | 4/4 | 1.400/1.400 | 0.0287/0.0287 |
| LP3-VCVS | 10.1 | 2.52 | 4.0x | 30/30 | 1.912/1.914 | 3.08/0.076 |
| LP3-MFB | 34.9 | 5.52 | 6.3x | 30/30 | 1.839/1.839 | 0.243/0.0372 |
| LP3-AM | 36.8 | 3.66 | 10.1x | 30/30 | 2.136/2.136 | 0.165/0.162 |
| HPn3-VCVS | 91.8 | 10.19 | 9.0x | 30/30 | 1.868/1.869 | 0.144/0.121 |
| HPn3-MFB | 22.0 | 7.20 | 3.1x | 0/0 | nan/nan | nan/nan |
| HPn3-AM | 393.2 | 26.21 | 15.0x | 12/12 | 2.078/2.078 | 1.64/1.84 |
| HPn2-VCVS | 6.2 | 1.05 | 5.9x | 30/30 | 1.213/1.213 | 0.212/0.163 |
| HPn2-MFB | 46.1 | 4.58 | 10.1x | 0/0 | nan/nan | nan/nan |
| HPn2-AM | 10.1 | 1.08 | 9.3x | 14/14 | 1.400/1.400 | 0.479/0.396 |
| HP2-VCVS | 4.4 | 0.63 | 7.0x | 30/30 | 1.213/1.213 | 0.222/0.0772 |
| HP2-MFB | 29.2 | 2.52 | 11.6x | 30/30 | 1.341/1.341 | 0.495/0.0783 |
| HP2-AM | 12.4 | 1.10 | 11.3x | 14/14 | 1.400/1.400 | 1.04/0.65 |
| BP2-VCVS | 21.7 | 2.15 | 10.1x | 30/30 | 2.902/8.287 | 0.0831/0.0166 |
| BP2-MFB | 31.5 | 1.59 | 19.7x | 30/30 | 1.213/1.213 | 0.24/0.0704 |
| BP2-AM | 3.1 | 0.50 | 6.0x | 2/2 | 1.400/1.400 | 0.908/0.908 |
| N2-VCVS | 35.2 | 2.79 | 12.6x | 30/30 | 1.371/1.374 | 3.47/0.357 |
| N2-MFB | 16.2 | 4.48 | 3.6x | 30/30 | 1.213/1.213 | 0.949/0.798 |
| N2-AM | 10.4 | 1.33 | 7.8x | 14/14 | 1.400/1.400 | 0.488/0.488 |
| BP1LP-MFB | 55.7 | 6.54 | 8.5x | 30/30 | 2.072/2.059 | 0.372/0.372 |

Total serial CPU 1506 s -> 220 s (x6.9); best sens better 4, same 18, worse 6 (0.1 % band).

### A.7 Variant `lm-p3` vs baseline (Balanced, ideal)

| Case | CPU base | CPU var | Speed-up | BOMs b/v | Best sens b/v | Best snap cost b/v |
|---|---:|---:|---:|---:|---:|---:|
| LPn3-VCVS | 38.0 | 34.84 | 1.1x | 8/8 | 1.959/1.959 | 0.0224/0.0224 |
| LPn3-MFB | 53.1 | 22.43 | 2.4x | 30/30 | 2.024/2.034 | 0.0997/0.0487 |
| LPn3-AM | 58.8 | 32.61 | 1.8x | 12/12 | 2.348/2.348 | 1.05/0.438 |
| LPn2-VCVS | 16.4 | 14.60 | 1.1x | 0/0 | nan/nan | nan/nan |
| LPn2-MFB | 11.0 | 7.73 | 1.4x | 30/30 | 1.431/1.468 | 0.377/0.191 |
| LPn2-AM | 29.1 | 27.54 | 1.1x | 12/12 | 1.400/1.400 | 0.409/0.409 |
| LPn3g-VCVS | 311.0 | 126.35 | 2.5x | 30/30 | 3.908/3.908 | 0.109/0.0273 |
| LPn3g-MFB | 38.3 | 22.57 | 1.7x | 30/30 | 2.147/2.161 | 0.133/0.0459 |
| LPn3g-AM | 46.3 | 28.28 | 1.6x | 12/12 | 2.351/2.351 | 0.95/0.95 |
| LP2-VCVS | 4.6 | 4.28 | 1.1x | 30/30 | 1.213/1.213 | 0.0496/0.0218 |
| LP2-MFB | 21.3 | 17.84 | 1.2x | 30/30 | 1.161/1.161 | 0.0285/0.0107 |
| LP2-AM | 7.0 | 6.09 | 1.2x | 4/4 | 1.400/1.400 | 0.0287/0.0287 |
| LP3-VCVS | 10.1 | 8.15 | 1.2x | 30/30 | 1.912/1.912 | 3.08/0.031 |
| LP3-MFB | 34.9 | 16.95 | 2.1x | 30/30 | 1.839/1.839 | 0.243/0.029 |
| LP3-AM | 36.8 | 30.29 | 1.2x | 30/30 | 2.136/2.136 | 0.165/0.162 |
| HPn3-VCVS | 91.8 | 51.33 | 1.8x | 30/30 | 1.868/1.867 | 0.144/0.119 |
| HPn3-MFB | 22.0 | 20.19 | 1.1x | 0/0 | nan/nan | nan/nan |
| HPn3-AM | 393.2 | 41.87 | 9.4x | 12/12 | 2.078/2.078 | 1.64/0.887 |
| HPn2-VCVS | 6.2 | 5.08 | 1.2x | 30/30 | 1.213/1.213 | 0.212/0.167 |
| HPn2-MFB | 46.1 | 43.80 | 1.1x | 0/0 | nan/nan | nan/nan |
| HPn2-AM | 10.1 | 8.25 | 1.2x | 14/14 | 1.400/1.400 | 0.479/0.396 |
| HP2-VCVS | 4.4 | 3.77 | 1.2x | 30/30 | 1.213/1.213 | 0.222/0.0772 |
| HP2-MFB | 29.2 | 17.02 | 1.7x | 30/30 | 1.341/1.341 | 0.495/0.0783 |
| HP2-AM | 12.4 | 9.80 | 1.3x | 14/14 | 1.400/1.400 | 1.04/0.65 |
| BP2-VCVS | 21.7 | 17.97 | 1.2x | 30/30 | 2.902/3.007 | 0.0831/0.257 |
| BP2-MFB | 31.5 | 27.30 | 1.2x | 30/30 | 1.213/1.213 | 0.24/0.0704 |
| BP2-AM | 3.1 | 2.85 | 1.1x | 2/2 | 1.400/1.400 | 0.908/0.908 |
| N2-VCVS | 35.2 | 11.91 | 3.0x | 30/30 | 1.371/1.371 | 3.47/0.354 |
| N2-MFB | 16.2 | 12.47 | 1.3x | 30/30 | 1.213/1.213 | 0.949/0.798 |
| N2-AM | 10.4 | 8.93 | 1.2x | 14/14 | 1.400/1.400 | 0.488/0.488 |
| BP1LP-MFB | 55.7 | 27.46 | 2.0x | 30/30 | 2.072/2.072 | 0.372/0.372 |

Total serial CPU 1506 s -> 711 s (x2.1); best sens better 0, same 24, worse 4 (0.1 % band).

### A.8 Variant `lm-p3pol` vs baseline (Balanced, ideal)

| Case | CPU base | CPU var | Speed-up | BOMs b/v | Best sens b/v | Best snap cost b/v |
|---|---:|---:|---:|---:|---:|---:|
| LPn3-VCVS | 38.0 | 3.51 | 10.8x | 8/8 | 1.959/1.959 | 0.0224/0.0224 |
| LPn3-MFB | 53.1 | 45.97 | 1.2x | 30/30 | 2.024/1.972 | 0.0997/0.0534 |
| LPn3-AM | 58.8 | 15.71 | 3.7x | 12/12 | 2.348/2.347 | 1.05/0.609 |
| LPn2-VCVS | 16.4 | 1.54 | 10.7x | 0/0 | nan/nan | nan/nan |
| LPn2-MFB | 11.0 | 5.55 | 2.0x | 30/30 | 1.431/1.215 | 0.377/29.7 |
| LPn2-AM | 29.1 | 1.12 | 26.1x | 12/12 | 1.400/1.400 | 0.409/0.409 |
| LPn3g-VCVS | 311.0 | 284.27 | 1.1x | 30/30 | 3.908/4.075 | 0.109/0.0353 |
| LPn3g-MFB | 38.3 | 39.59 | 1.0x | 30/30 | 2.147/2.087 | 0.133/0.0643 |
| LPn3g-AM | 46.3 | 17.71 | 2.6x | 12/12 | 2.351/2.357 | 0.95/0.893 |
| LP2-VCVS | 4.6 | 0.50 | 9.1x | 30/25 | 1.213/1.213 | 0.0496/0.0218 |
| LP2-MFB | 21.3 | 4.27 | 5.0x | 30/30 | 1.161/1.147 | 0.0285/0.0107 |
| LP2-AM | 7.0 | 0.96 | 7.4x | 4/4 | 1.400/1.400 | 0.0287/0.0287 |
| LP3-VCVS | 10.1 | 2.52 | 4.0x | 30/30 | 1.912/1.914 | 3.08/0.076 |
| LP3-MFB | 34.9 | 16.01 | 2.2x | 30/30 | 1.839/1.839 | 0.243/0.0394 |
| LP3-AM | 36.8 | 4.82 | 7.6x | 30/30 | 2.136/2.136 | 0.165/0.162 |
| HPn3-VCVS | 91.8 | 39.44 | 2.3x | 30/30 | 1.868/1.870 | 0.144/0.121 |
| HPn3-MFB | 22.0 | 7.37 | 3.0x | 0/0 | nan/nan | nan/nan |
| HPn3-AM | 393.2 | 27.27 | 14.4x | 12/12 | 2.078/2.078 | 1.64/1.84 |
| HPn2-VCVS | 6.2 | 1.36 | 4.6x | 30/30 | 1.213/1.213 | 0.212/0.163 |
| HPn2-MFB | 46.1 | 4.85 | 9.5x | 0/0 | nan/nan | nan/nan |
| HPn2-AM | 10.1 | 1.03 | 9.8x | 14/14 | 1.400/1.400 | 0.479/0.396 |
| HP2-VCVS | 4.4 | 0.59 | 7.5x | 30/30 | 1.213/1.213 | 0.222/0.0772 |
| HP2-MFB | 29.2 | 7.22 | 4.0x | 30/30 | 1.341/1.341 | 0.495/0.0783 |
| HP2-AM | 12.4 | 1.10 | 11.3x | 14/14 | 1.400/1.400 | 1.04/0.65 |
| BP2-VCVS | 21.7 | 3.73 | 5.8x | 30/30 | 2.902/2.321 | 0.0831/0.202 |
| BP2-MFB | 31.5 | 3.83 | 8.2x | 30/30 | 1.213/1.213 | 0.24/0.0704 |
| BP2-AM | 3.1 | 0.47 | 6.5x | 2/2 | 1.400/1.400 | 0.908/0.908 |
| N2-VCVS | 35.2 | 5.42 | 6.5x | 30/30 | 1.371/1.379 | 3.47/0.357 |
| N2-MFB | 16.2 | 4.82 | 3.4x | 30/30 | 1.213/1.213 | 0.949/0.798 |
| N2-AM | 10.4 | 1.33 | 7.8x | 14/14 | 1.400/1.400 | 0.488/0.488 |
| BP1LP-MFB | 55.7 | 19.01 | 2.9x | 30/30 | 2.072/2.059 | 0.372/0.372 |

Total serial CPU 1506 s -> 573 s (x2.6); best sens better 6, same 17, worse 5 (0.1 % band).

### A.9 Batched LM, single core (Balanced, ideal)

| Case | Phase 1 TRF (pipeline) | Phase 1 batch | x | Phase-1 valleys (batch) | Phase 3 TRF (same tasks) | Phase 3 batch | x | tasks accepted TRF/batch |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| LPn3-VCVS | 36.7 | 0.221 | 166x | 1 | 0.21 | 0.029 | 7x | 8/8 of 8 |
| LPn3-MFB | 13.2 | 0.195 | 68x | 105 | 17.50 | 0.020 | 894x | 234/234 of 234 |
| LPn3-AM | 19.5 | 0.280 | 70x | 22 | 31.06 | 0.439 | 71x | 153/153 of 298 |
| LPn2-VCVS | 15.9 | 0.103 | 154x | 0 | - | - | - | no Phase-3 tasks |
| LPn2-MFB | 5.9 | 0.138 | 42x | 35 | 2.60 | 0.007 | 352x | 62/62 of 62 |
| LPn2-AM | 27.8 | 0.065 | 427x | 2 | 0.98 | 0.020 | 49x | 12/12 of 16 |
| LPn3g-VCVS | 58.0 | 1.671 | 35x | 122 | 119.77 | 1.345 | 89x | 745/746 of 820 |
| LPn3g-MFB | 11.7 | 0.209 | 56x | 90 | 14.55 | 0.117 | 124x | 202/202 of 202 |
| LPn3g-AM | 21.4 | 0.258 | 83x | 20 | 29.86 | 0.503 | 59x | 137/137 of 270 |
| LP2-VCVS | 4.2 | 0.018 | 230x | 17 | 0.14 | 0.020 | 7x | 25/25 of 26 |
| LP2-MFB | 19.1 | 0.090 | 211x | 50 | 1.92 | 0.032 | 60x | 87/87 of 88 |
| LP2-AM | 6.8 | 0.060 | 113x | 2 | 0.03 | 0.002 | 15x | 4/4 of 4 |
| LP3-VCVS | 6.8 | 0.066 | 104x | 71 | 2.54 | 0.064 | 40x | 145/145 of 160 |
| LP3-MFB | 15.6 | 0.165 | 95x | 200 | 15.25 | 0.114 | 134x | 382/382 of 388 |
| LP3-AM | 30.5 | 0.165 | 185x | 18 | 2.63 | 0.054 | 49x | 50/50 of 52 |
| HPn3-VCVS | 46.3 | 0.648 | 71x | 124 | 13.55 | 0.042 | 322x | 314/314 of 314 |
| HPn3-MFB | 16.5 | 0.302 | 55x | 0 | - | - | - | no Phase-3 tasks |
| HPn3-AM | 22.1 | 0.249 | 89x | 56 | 483.30 | 0.856 | 565x | 360/360 of 756 |
| HPn2-VCVS | 4.1 | 0.038 | 107x | 23 | 0.65 | 0.021 | 30x | 32/32 of 36 |
| HPn2-MFB | 45.0 | 0.299 | 151x | 0 | - | - | - | no Phase-3 tasks |
| HPn2-AM | 8.9 | 0.060 | 149x | 2 | 0.57 | 0.015 | 37x | 14/14 of 16 |
| HP2-VCVS | 4.1 | 0.018 | 236x | 40 | 0.29 | 0.014 | 21x | 68/68 of 72 |
| HP2-MFB | 15.1 | 0.085 | 178x | 99 | 12.02 | 0.095 | 126x | 224/224 of 316 |
| HP2-AM | 11.8 | 0.059 | 198x | 2 | 0.50 | 0.022 | 23x | 14/14 of 16 |
| BP2-VCVS | 18.3 | 0.177 | 104x | 38 | 1.51 | 0.004 | 346x | 56/56 of 56 |
| BP2-MFB | 27.7 | 0.119 | 233x | 74 | 3.09 | 0.026 | 118x | 121/121 of 126 |
| BP2-AM | 2.9 | 0.022 | 131x | 1 | 0.02 | 0.001 | 12x | 2/2 of 2 |
| N2-VCVS | 11.6 | 0.148 | 78x | 31 | 1.80 | 0.034 | 54x | 62/62 of 62 |
| N2-MFB | 9.9 | 0.050 | 196x | 32 | 4.61 | 0.173 | 27x | 84/84 of 108 |
| N2-AM | 9.4 | 0.092 | 102x | 2 | 0.68 | 0.021 | 32x | 14/14 of 16 |
| BP1LP-MFB | 25.5 | 0.197 | 130x | 155 | 41.77 | 0.197 | 212x | 313/313 of 347 |

Phase 1 total 572 s -> 6.3 s (x91); Phase 3 total 803 s -> 4.3 s (x187).

### A.10 Relative solving-time coefficients (Balanced, op-amp ideal)

| Approach | Sections | CPU coefficient (range) | Core-s burned, 32-worker box | Wall coefficient, 32 cores (range) | Mean CPU-s / section | Mean wall-s / section |
|---|---:|---|---:|---|---:|---:|
| baseline | 31 | 1.000 (1.000-1.00) | 1.000 | 1.000 (1.000-1.00) | 48.58 | 3.57 |
| B compile once (est.) | 31 | 0.968 (0.750-1.00) | 0.724 | 0.562 (0.098-0.96) | 47.02 | 2.01 |
| A1 TRF tolerance 1e-7 | 31 | 0.744 (0.404-0.96) | 0.807 | 0.883 (0.567-1.06) | 36.14 | 3.15 |
| A2 TRF 1e-8 + budgets /4 | 31 | 0.496 (0.306-0.94) | 0.623 | 0.738 (0.452-1.04) | 24.11 | 2.64 |
| C  scalar LM, Phase 1+3 | 31 | 0.146 (0.041-0.36) | 0.352 | 0.553 (0.116-1.03) | 7.09 | 1.98 |
| C' scalar LM, Phase 3 only | 31 | 0.472 (0.106-0.95) | 0.603 | 0.718 (0.174-1.09) | 22.92 | 2.57 |
| C'' scalar LM + Phase-3 polish | 31 | 0.380 (0.038-1.03) | 0.534 | 0.683 (0.136-1.15) | 18.48 | 2.44 |
| D  B + batched LM, 1 core (est.) | 31 | 0.016 (0.006-0.04) | 0.012 | 0.211 (0.050-0.65) | 0.76 | 0.76 |
| E  D + learned seeds (est.) | 31 | 0.013 (0.002-0.04) | 0.010 | 0.182 (0.017-0.60) | 0.65 | 0.65 |

### A.11 Relative solving-time coefficients (Balanced, op-amp tl072)

| Approach | Sections | CPU coefficient (range) | Core-s burned, 32-worker box | Wall coefficient, 32 cores (range) | Mean CPU-s / section | Mean wall-s / section |
|---|---:|---|---:|---|---:|---:|
| baseline | 31 | 1.000 (1.000-1.00) | 1.000 | 1.000 (1.000-1.00) | 50.13 | 4.72 |
| B compile once (est.) | 31 | 0.948 (0.708-1.00) | 0.475 | 0.445 (0.087-0.94) | 47.51 | 2.10 |
| D  B + batched LM, 1 core (est.) | 31 | 0.025 (0.006-0.10) | 0.013 | 0.265 (0.048-1.16) | 1.25 | 1.25 |
| E  D + learned seeds (est.) | 31 | 0.023 (0.002-0.10) | 0.011 | 0.243 (0.017-1.13) | 1.15 | 1.15 |
