# dev/fs028 — FS-028 solver-performance harness

Supplementary material for `dev/FS-028_solver_performance_analysis.md`, which holds the
findings. Analysis only: nothing here is imported by the app. Run everything from the
repository root. The TF cache goes to a private work dir (`$FS028_WORK`, default
`<tmp>/fs028_work`).

| Script | What it does |
|---|---|
| `fs028_common.py` | Benchmark sections (`section_set`) and the Topology-tab routing (`route`, `build_cfg`). Also the instrumented serial run (`instrumented`, `run_section`) and the 32-core model (`summarize`, `sched_wall`). |
| `bench_sections.py` | Baseline sweep. Stage times, 32-core model and top-5 BOMs per case. `--mode pool [--spawn]` runs the real process pools instead. |
| `probe_phase1.py` | Cost trajectory of every Phase-1/3 start: converged vs failed, `max_nfev` hits, early-abort rules. |
| `probe_basin.py` | Basin of attraction of a Phase-1 root: TRF vs log-space LM, and TRF's stall plateaus. |
| `probe_pool.py` | Per-section pool start-up cost (initializer + import), fork vs spawn. |
| `lm_core.py` | Prototypes: projected log-space LM (`lm_log`), a `least_squares` drop-in (`LSAdapter`), batched LM (`batch_lm_log`, `batch_funcs`), sensitivity polish (`polish`, `sens2_funcs`), loosened TRF (`LooseTRF`). |
| `ab_lm.py` | Full pipeline with a variant local solver (`lm`, `lm-p1`, `lm-p3`, `lm-p3pol`, `trf-loose`, `trf-loose-tol`, `…-polish`) vs baseline. |
| `compare_ab.py` | Variant vs baseline: CPU, BOM count, best sens / snap cost, baseline best BOM kept. |
| `probe_batch.py` | Phase 1 as one batched solve (trf / lm / batch) on the production start set. |
| `probe_batch_p3.py` | Phase 3 as batched solves on harvest's task list (vs production TRF semantics). |
| `probe_atlas.py` | Learned seeds: valley atlas (kNN) and analytic polynomial seed vs cold multistart, on design-parametric residuals. |
| `make_tables.py` | Prints the note's appendix tables from `results/`. |
| `check_kernels.py` | S2-1 check: every compiled cell kernel (`cell_kernels`, all cells, AM Equalize variant) equals the per-design derive + lambdify path it replaced, to rounding; kernels replayed from the disk cache equal the live ones bit for bit. S2-2: the batched replay of the `res` group (`load_batched` / `bind_rows`) equals the scalar replay row by row. |
| `compare_s22.py` | S2-2: two `bench_sections.py` runs (baseline vs new) by the §6 rule — best sens, best and median-10 snap cost, BOM count, no-BOM sections, baseline best BOM kept, serial CPU; `--md` writes the tables. |

The solver under test is the batched one by default; `FS_SOLVER=trf` (environment) runs the
legacy scipy-TRF pool path, bit-identical to S2-1, so both can be benchmarked from one checkout.
`bench_sections.py` rows carry `snapped_q` (every snapped BOM's topology, sens, snap cost).

`results/` holds the raw outputs: `baseline_balanced.json` (the Stage-2 reference: stage times
and top BOMs per case), `ab_lm_*.json`, `probe_batch_p1_*.jsonl`, `probe_batch_p3_*.jsonl`,
`probe_phase1_*.json` and `baseline_{fast,thorough}.json`.
