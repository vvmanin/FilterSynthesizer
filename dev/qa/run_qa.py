# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FilterSynthesizer QA & benchmark harness -- entry point.

    python dev/qa/run_qa.py --level smoke|standard|full [options]

Phases (one task graph on one worker pool, see README.md):
  0 preflight (interpreter, requirements, cairosvg, LTspice, build prompt)
  1 existing checks (verify.py + dev/fs*/check_*.py)       2 cache warm-up
  3 UI designs through AppTest + pairing / spec analysis    4 solve-job capture
  5 section solves                                          6 end-to-end: report + LTspice export
  7 LTspice batch runs        8 benchmark (controlled concurrency)
  9 source server smoke, build.bat, bundled exe             10 summary.md / fs016_pairing.md
All prompts happen in the first minute; the rest runs unattended.
"""
import argparse
import collections
import glob
import os
import pickle
import re
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (CACHE_DIR, QA_DIR, RESULTS_DIR, ROOT, append_jsonl, fmt_dur, host,   # noqa: E402
                    read_jsonl, say, setup_paths, stamp, write_json)
import preflight                                                                        # noqa: E402

STAGES = ("checks", "ui", "solve", "e2e", "ltspice", "bench", "server", "build", "exe")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--level", choices=("smoke", "standard", "full"), default="smoke")
    p.add_argument("--budget", help="wall-time budget, e.g. 90m or 2h: no new tasks after it")
    p.add_argument("--workers", type=int, help="pool size (default: threads - 2, RAM-capped)")
    p.add_argument("--only", help="comma list of stages to run: " + ",".join(STAGES))
    p.add_argument("--skip", help="comma list of stages to skip")
    p.add_argument("--build", action="store_true", help="include build.bat + exe test (asks first)")
    p.add_argument("--no-build", action="store_true", help="never build (full level builds by default)")
    p.add_argument("--compare", help="'last' or a run dir: regression diff in the summary")
    p.add_argument("--resume", help="continue an interrupted run dir (finished tasks are skipped)")
    p.add_argument("--summarize", help="only (re)write the summary of a run dir")
    p.add_argument("--cold", action="store_true", help="fresh TF / kernel cache (slow: derives every cell)")
    p.add_argument("--yes", action="store_true", help="answer yes to every prompt (unattended)")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--designs", type=int, help="debug: only the first N designs")
    return p.parse_args(argv)


def _budget(s):
    if not s:
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([hms]?)\s*", s)
    if not m:
        raise SystemExit(f"bad --budget {s!r} (use 90m, 2h, 5400s)")
    return float(m.group(1)) * {"h": 3600, "m": 60, "s": 1, "": 60}[m.group(2)]


def _stages(a):
    st = set(STAGES)
    if a.only:
        st = {s.strip() for s in a.only.split(",")}
    if a.skip:
        st -= {s.strip() for s in a.skip.split(",")}
    unknown = st - set(STAGES)
    if unknown:
        raise SystemExit(f"unknown stage(s): {sorted(unknown)}")
    return st


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.+-]", "_", name)[:150]


def main(argv=None):
    a = parse_args(argv)
    setup_paths()
    if a.summarize:
        import summarize
        cmp_dir = summarize.find_previous(a.summarize) if a.compare == "last" else a.compare
        say(summarize.write(a.summarize, cmp_dir))
        return 0

    stages = _stages(a)
    run_dir = os.path.abspath(a.resume) if a.resume else os.path.join(
        RESULTS_DIR, f"{stamp()}_{_safe(host())}_{a.level}")
    logs = os.path.join(run_dir, "logs")
    os.makedirs(logs, exist_ok=True)
    say(f"QA run -> {run_dir}")

    # ---------------- phase 0: preflight + every prompt, up front
    level_builds = a.level == "full" and not a.no_build
    want_build = ("build" in stages) and (a.build or level_builds) and not a.no_build
    env, ok = preflight.run(a.yes, want_build)
    if not ok:
        return 2
    do_build = False
    if want_build:
        exist = [d for d in ("build", "dist") if os.path.isdir(os.path.join(ROOT, d))]
        do_build = preflight.ask(
            "Run build.bat now? It deletes build/ and dist/"
            + (f" (present: {', '.join(exist)})" if exist else "")
            + " and rebuilds the bundle (~5-10 min"
            + ("" if env["build_venv"] else "; first run creates build_venv and downloads packages")
            + ").", a.yes)
    lts = env.get("ltspice") if "ltspice" in stages else None
    if "ltspice" in stages and not lts:
        stages.discard("ltspice")
    write_json(os.path.join(run_dir, "env.json"), env)

    import matrix as M
    import summarize
    from pool import Pool, Task

    # ---------------- caches + pool
    cache = tempfile.mkdtemp(prefix="fs_qa_cold_") if a.cold else CACHE_DIR
    os.makedirs(cache, exist_ok=True)
    if not a.cold:
        for c in glob.glob(os.path.join(ROOT, "tf_cache*.json")):
            if not os.path.exists(os.path.join(cache, os.path.basename(c))):
                shutil.copy(c, cache)
    ram = env.get("ram_avail_mb") or 16000
    workers = a.workers or max(1, min((env.get("logical_cores") or 4) - 2, int(ram / 700)))
    phys = env.get("physical_cores") or max(1, workers // 2)
    wenv = dict(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
                FILTERSYNTHESIZER_SOLVE_WORKERS="1", PYTHONIOENCODING="utf-8", MPLBACKEND="Agg",
                STREAMLIT_BROWSER_GATHER_USAGE_STATS="false")
    pool = Pool(workers, cache, logs, paths=[ROOT, QA_DIR], env=wenv,
                limits=dict(check=3, ltspice=6, build=1, server=1, exe=1, warmup=1,
                            e2e=max(2, workers // 4)),
                recycle_after=40, max_rss_mb=3000)
    T = M.TIMEOUTS
    t_start = time.time()
    budget = _budget(a.budget)
    if budget:
        pool.deadline = t_start + budget - (600 if "bench" in stages else 120)
    say(f"== Pool: {workers} workers ({phys} physical cores); cache {cache}"
        + (f"; budget {fmt_dur(budget)}" if budget else ""))

    # ---------------- results + resume
    results = os.path.join(run_dir, "results.jsonl")
    prev = read_jsonl(results) if a.resume else []
    done = collections.defaultdict(set)
    solved = {}
    for r in prev:
        t = r.get("type")
        if t == "job":
            solved[r["key"]] = {k: v for k, v in r.items() if k not in ("type", "cid", "design_id",
                                                                    "variant_id", "stage", "topologies")}
            done["job"].add(r["cid"])
        elif t in ("design", "e2e", "ltspice", "bench", "check"):
            done[t].add(r.get("id") or r.get("name"))
        elif t in ("server", "build", "exe", "warmup"):
            done[t].add(t)

    prev_e2e = {r["id"]: r for r in prev if r.get("type") == "e2e"}

    def rec(typ, **kw):
        append_jsonl(results, dict(type=typ, **kw))

    jobs_dir = os.path.join(run_dir, "jobs")
    os.makedirs(jobs_dir, exist_ok=True)
    cids_by_key = collections.defaultdict(list)
    submitted_keys = set()
    requested_cells = set()
    ui_options = {}
    cells_total = []
    counts = collections.Counter()

    def fail(task, res):
        if res["status"] == "skipped":                 # time budget: not a failure
            return
        rec("task_failure", label=task.label, status=res["status"], error=res.get("error"),
            traceback=res.get("traceback"))

    # ---- solves
    def job_record(cid_meta, metrics):
        cid, meta = cid_meta
        if cid in done["job"]:
            return
        done["job"].add(cid)
        rec("job", **{**metrics, **meta, "cid": cid})

    def on_solve(task, res):
        key = task.meta["key"]
        if res["status"] == "ok":
            m = res["value"]
            m.pop("cid", None)
        else:
            m = dict(key=key, outcome={"error": "ERROR", "timeout": "TIMEOUT", "crash": "CRASH",
                                       "skipped": "SKIPPED"}[res["status"]],
                     error=res.get("error"), wall=res.get("wall"))
            if res["status"] == "error":
                m["traceback"] = (res.get("traceback") or "")[-1500:]
        if m["outcome"] == "SKIPPED":
            return
        solved[key] = m
        counts["solve_done"] += 1
        for cm in cids_by_key[key]:
            job_record(cm, m)

    def add_job(j, fam_by_stage):
        meta = dict(key=j["key"], design_id=j["design_id"], variant_id=j["variant_id"],
                    stage=j["stage"], topologies=j["topologies"], dc_gain=j.get("dc_gain"),
                    section_family=fam_by_stage.get(j["stage"]))
        requested_cells.update(j["topologies"])
        cids_by_key[j["key"]].append((j["cid"], meta))
        if j["key"] in solved:
            job_record((j["cid"], meta), solved[j["key"]])
        elif j["key"] not in submitted_keys and "solve" in stages:
            submitted_keys.add(j["key"])
            counts["solve_sub"] += 1
            pool.submit(Task("tasks_solve:solve_job", (j,), kind="solve", timeout=T["solve"],
                             priority=30, meta={"key": j["key"]}, on_done=on_solve, label=j["cid"]))

    # ---- e2e + LTspice
    def on_ltspice(task, res):
        if res["status"] == "skipped":
            return
        counts["lt_done"] += 1
        if res["status"] != "ok":
            fail(task, res)
            rec("ltspice", id=task.meta["id"], status="fail", problems=[res.get("error")])
            return
        rec("ltspice", **res["value"])

    def on_e2e(task, res):
        if res["status"] == "skipped":
            return
        counts["e2e_done"] += 1
        if res["status"] != "ok":
            fail(task, res)
            rec("e2e", id=task.meta["id"], status=f"task_{res['status']}", error=res.get("error"))
            return
        v = res["value"]
        rec("e2e", **v)
        submit_ltspice(v)

    def submit_ltspice(v):
        ex = v.get("export") or {}
        if ex.get("ok") and lts and "ltspice" in stages and v["id"] not in done["ltspice"]:
            done["ltspice"].add(v["id"])
            counts["lt_sub"] += 1
            pool.submit(Task("tasks_export:ltspice_task",
                             (v["id"], ex["dir"], ex.get("expected"), lts["path"], ex.get("all_generic", True)),
                             kind="ltspice", timeout=T["ltspice"] * 6, priority=40,
                             meta={"id": v["id"]}, on_done=on_ltspice, label=f"ltspice {v['id']}"))

    def submit_e2e(d, idx):
        vid, v = M.e2e_variant(d["id"])
        eid = f"{d['id']}|{vid}"
        if eid in done["e2e"]:                       # resume: its LTspice run may be missing
            if eid in prev_e2e:
                submit_ltspice(prev_e2e[eid])
            return
        if "e2e" not in stages:
            return
        counts["e2e_sub"] += 1
        out_dir = os.path.join(run_dir, "e2e", _safe(d["id"]))
        pool.submit(Task("tasks_ui:e2e_task", (d, vid, v, out_dir), {"timeout": T["e2e"]},
                         kind="e2e", timeout=T["e2e"], priority=25, meta={"id": eid},
                         on_done=on_e2e, label=f"e2e {eid}"))

    # ---- designs
    def on_design(task, res):
        if res["status"] == "skipped":
            return
        d = task.meta["design"]
        counts["design_done"] += 1
        if res["status"] != "ok":
            fail(task, res)
            rec("design", id=d["id"], design=d, status=f"task_{res['status']}",
                error=res.get("error"), wall=res.get("wall"))
            return
        v = res["value"]
        jobs = v.pop("jobs", [])
        for k, opts in (v.get("ui_options") or {}).items():
            ui_options.setdefault(k, set()).update(opts)
        with open(os.path.join(jobs_dir, _safe(d["id"]) + ".pkl"), "wb") as f:
            pickle.dump(jobs, f)
        fam = {s["stage"]: s["family"] for s in (v.get("analysis") or {}).get("sections", [])}
        for vr in v.get("variants", []):
            for fo in vr.get("first_order", []):
                requested_cells.add(fo.get("topology"))
        rec("design", **v)
        for j in jobs:
            add_job(j, fam)
        if d.get("e2e") and v.get("status") == "ok":
            submit_e2e(d, task.meta["idx"])

    designs = M.designs(a.level, a.seed)
    if a.designs:
        designs = designs[:a.designs]

    def submit_designs():
        for i, d in enumerate(designs):
            if d["id"] in done["design"]:
                pkl = os.path.join(jobs_dir, _safe(d["id"]) + ".pkl")
                if os.path.isfile(pkl):                     # resume: re-queue its unsolved jobs
                    with open(pkl, "rb") as f:
                        jobs = pickle.load(f)
                    for j in jobs:
                        add_job(j, {})
                if d.get("e2e"):
                    submit_e2e(d, i)
                continue
            vs = M.variants(a.level, d["id"], a.seed) if d.get("solve") else []
            counts["design_sub"] += 1
            pool.submit(Task("tasks_ui:design_task", (d, vs), {"timeout": T["design"]}, kind="design",
                             timeout=T["design"], priority=20, meta={"design": d, "idx": i},
                             on_done=on_design, label=f"design {d['id']}"))

    def on_warmup(task, res):
        if res["status"] != "ok":
            fail(task, res)
            say(f"  warm-up failed: {res.get('error')}")
        else:
            cells_total.extend(res["value"]["cells"])
            rec("warmup", cells=len(res["value"]["cells"]), n_sources=res["value"]["n_sources"],
                secs=res["value"]["secs"])
            say(f"  warm-up: {len(res['value']['cells'])} cells in {res['value']['secs']:.0f} s")
        if "ui" in stages:
            submit_designs()
        if "server" in stages and "server" not in done["server"]:
            pool.submit(Task("tasks_build:server_task", (logs, cache), {"timeout": T["server"]},
                             kind="server", timeout=T["server"] + 200, priority=10, essential=True,
                             on_done=lambda t, r: rec("server", **(r["value"] if r["status"] == "ok"
                                                                   else dict(status="fail", why=r.get("error")))),
                             label="source server smoke"))

    # ---------------- submit the first wave
    if do_build and "build" not in done["build"]:
        def on_build(t, r):
            v = r["value"] if r["status"] == "ok" else dict(status="fail", problems=[r.get("error")])
            rec("build", **v)
            say(f"  build: {v.get('status')} ({v.get('secs', '?')} s)")
        pool.submit(Task("tasks_build:build_task", (logs,), {"timeout": T["build"]}, kind="build",
                         timeout=T["build"] + 60, priority=1, essential=True, on_done=on_build,
                         label="build.bat"))
    if "checks" in stages:
        import checks
        for name, argv in checks.discover(quick=M.LEVELS[a.level]["check_quick"]):
            if name in done["check"]:
                continue
            log = os.path.join(logs, "check_" + _safe(os.path.basename(name)) + ".log")
            pool.submit(Task("checks:check_task", (name, argv, log), {"timeout": T["check"]},
                             kind="check", timeout=T["check"] + 60, priority=5, essential=True,
                             on_done=lambda t, r: rec("check", **(r["value"] if r["status"] == "ok" else
                                                                 dict(name=t.label, status="fail", rc=r["status"],
                                                                      tail=r.get("error", ""), secs=r.get("wall")))),
                             label=name))
    pool.submit(Task("tasks_solve:warmup", kind="warmup", timeout=7200, priority=0, essential=True,
                     on_done=on_warmup, label="warm-up"))

    def progress(p):
        el = time.time() - t_start
        run = p.running()
        slow = max(run, key=lambda x: x[1]) if run else None
        say(f"  [{fmt_dur(el)}] designs {counts['design_done']}/{counts['design_sub']} · "
            f"solves {counts['solve_done']}/{counts['solve_sub']} · e2e {counts['e2e_done']}/{counts['e2e_sub']}"
            f" · ltspice {counts['lt_done']}/{counts['lt_sub']} · running {len(run)} · queued {len(p.pending)}"
            f" · util {p.utilisation() * 100:.0f}%"
            + (f" · longest: {slow[0].label[:60]} {slow[1]:.0f}s" if slow and slow[1] > 120 else ""))

    meta = dict(level=a.level, seed=a.seed, workers=workers, args=vars(a), started=stamp())
    say("== Running (Ctrl+C stops and writes a partial summary)")
    interrupted = False
    try:
        pool.start()
        pool.run(progress=progress, every=15)
        util = pool.utilisation()

        # ---------------- benchmark: controlled concurrency, nothing else running
        if "bench" in stages:
            import tasks_solve
            L = M.LEVELS[a.level]
            cases = tasks_solve.bench_cases(L["bench_ids"])
            pool.limits["bench"] = phys
            say(f"== Benchmark: {len(cases)} cases x {L['bench_presets']} at {phys} concurrent")
            for preset in L["bench_presets"]:
                for cid, fam in cases:
                    bid = f"{cid}|{preset}|ideal"
                    if bid in done["bench"]:
                        continue
                    pool.submit(Task("tasks_solve:bench_task", (cid, fam, preset), kind="bench",
                                     timeout=T["bench"], priority=50, essential=True,
                                     on_done=lambda t, r: rec("bench", **r["value"]) if r["status"] == "ok"
                                     else fail(t, r), label=f"bench {cid} {preset}"))
            pool.run(progress=progress, every=30)

        # ---------------- bundled exe (after the build, on a quiet machine)
        if do_build and "exe" in stages and "exe" not in done["exe"]:
            say("== Bundled exe")
            pool.submit(Task("tasks_build:exe_task", (logs, cache), {"timeout": T["exe"]}, kind="exe",
                             timeout=T["exe"] * 3, priority=1, essential=True,
                             on_done=lambda t, r: rec("exe", **(r["value"] if r["status"] == "ok"
                                                                else dict(status="fail", why=r.get("error")))),
                             label="exe"))
            pool.run(progress=progress, every=30)
    except KeyboardInterrupt:
        interrupted = True
        say("\n  interrupted -- writing a partial summary")
        util = pool.utilisation()
    finally:
        pool.close()

    # ---------------- coverage + summary
    ui_untested = []
    for k, opts in ui_options.items():
        known = M.RESPONSES if k == "Response" else M.TYPES
        ui_untested += [f"{k}: {o}" for o in sorted(opts) if o not in known]
    meta.update(wall=time.time() - t_start, utilisation=util, interrupted=interrupted,
                skipped_tasks=pool.stats.get("skipped", 0),
                coverage=dict(cells_total=len(cells_total),
                              cells_exercised=len(requested_cells & set(cells_total)),
                              cells_untested=sorted(set(cells_total) - requested_cells),
                              cells_unknown=sorted(c for c in requested_cells - set(cells_total) if c),
                              ui_untested=ui_untested))
    write_json(os.path.join(run_dir, "run.json"), meta)
    cmp_dir = summarize.find_previous(run_dir) if a.compare == "last" else a.compare
    path = summarize.write(run_dir, cmp_dir)
    say(f"\n== Done in {fmt_dur(meta['wall'])}: {path}")
    with open(path, encoding="utf-8") as f:
        txt = f.read()
    m = re.search(r"## Verdict\n(.*?)\n## ", txt, re.S)
    if m:
        say(m.group(1).strip())
    return 1 if re.search(r"\| FAIL \|", txt) else 0


if __name__ == "__main__":
    sys.exit(main())
