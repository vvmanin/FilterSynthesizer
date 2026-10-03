# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Phase 9 -- summary.md, fs016_pairing.md and the regression diff.

Reads only the run directory (results.jsonl, env.json, logs/), so it can be
re-run on an old run: `python dev/qa/run_qa.py --summarize <run dir> [--compare <dir>]`.
"""
import collections
import glob
import math
import os
import re
import statistics

from common import RESULTS_DIR, fmt_dur, read_json, read_jsonl

FAIL_OUTCOMES = ("ERROR", "TIMEOUT", "CRASH")
PROBLEM_OUTCOMES = FAIL_OUTCOMES + ("NO_BOM_SUSPICIOUS",)


def _md_table(rows, head):
    if not rows:
        return "_none_\n"
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        out.append("| " + " | ".join(str(x).replace("|", "\\|").replace("\n", " ") for x in r) + " |")
    return "\n".join(out) + "\n"


def _q(vals, p):
    vals = sorted(v for v in vals if v is not None and math.isfinite(v))
    if not vals:
        return None
    return vals[min(len(vals) - 1, int(round(p * (len(vals) - 1))))]


def _f(x, nd=2):
    if x is None:
        return "–"
    if isinstance(x, float):
        if not math.isfinite(x):
            return str(x)
        return f"{x:.{nd}f}" if abs(x) >= 10 ** -nd else f"{x:.2e}"
    return str(x)


def load(run_dir):
    recs = read_jsonl(os.path.join(run_dir, "results.jsonl"))
    by = collections.defaultdict(list)
    for r in recs:
        by[r.get("type")].append(r)
    return by


def find_previous(run_dir):
    """The newest other run of the same level, for --compare last."""
    level = os.path.basename(run_dir).rsplit("_", 1)[-1]
    cands = sorted(d for d in glob.glob(os.path.join(RESULTS_DIR, f"*_{level}"))
                   if os.path.abspath(d) != os.path.abspath(run_dir)
                   and os.path.isfile(os.path.join(d, "results.jsonl")))
    return cands[-1] if cands else None


def _deprecations(run_dir):
    msgs = collections.Counter()
    for p in glob.glob(os.path.join(run_dir, "logs", "worker_*.log")):
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for ln in text.splitlines():
            if re.search(r"Please replace|deprecat", ln, re.I):
                msgs[re.sub(r"^20\d\d-\d\d-\d\d \S+ ", "", ln.strip())[:160]] += 1
    return msgs.most_common(12)


def compare(by, prev_by):
    """Regression rows between two runs (same ids)."""
    rows = []
    old_jobs = {r["cid"]: r for r in prev_by.get("job", [])}
    new_jobs = {r["cid"]: r for r in by.get("job", [])}
    good = ("OK", "DEGRADED")
    for cid, n in new_jobs.items():
        o = old_jobs.get(cid)
        if o is None:
            continue
        if o["outcome"] != n["outcome"]:
            worse = o["outcome"] in good and n["outcome"] not in good
            rows.append(("job", cid, f"{o['outcome']} -> {n['outcome']}", "WORSE" if worse else "changed"))
        elif n.get("best_snap") is not None and o.get("best_snap") is not None:
            a, b = o["best_snap"], n["best_snap"]
            if b > a * 1.2 + 0.05:
                rows.append(("job", cid, f"best snap {a:.3g} -> {b:.3g}", "WORSE"))
            elif b < a / 1.2 - 0.05:
                rows.append(("job", cid, f"best snap {a:.3g} -> {b:.3g}", "better"))
    gone = sorted(set(old_jobs) - set(new_jobs))
    new = sorted(set(new_jobs) - set(old_jobs))
    old_d = {r["id"]: r for r in prev_by.get("design", [])}
    for r in by.get("design", []):
        o = old_d.get(r["id"])
        if not o:
            continue
        if o.get("status") != r.get("status"):
            rows.append(("design", r["id"], f"{o.get('status')} -> {r.get('status')}", "changed"))
        oc = {k: v.get("status") for k, v in ((o.get("analysis") or {}).get("checks") or {}).items()}
        nc = {k: v.get("status") for k, v in ((r.get("analysis") or {}).get("checks") or {}).items()}
        for k in nc:
            if oc.get(k) and oc[k] != nc[k]:
                rows.append(("check", f"{r['id']}:{k}", f"{oc[k]} -> {nc[k]}",
                             "WORSE" if nc[k] == "fail" else "changed"))
        of = sorted(f["code"] for f in (o.get("analysis") or {}).get("flags", []))
        nf = sorted(f["code"] for f in (r.get("analysis") or {}).get("flags", []))
        if of != nf:
            rows.append(("pairing", r["id"], f"flags {of} -> {nf}", "changed"))
    ob = {r["id"]: r for r in prev_by.get("bench", [])}
    for r in by.get("bench", []):
        o = ob.get(r["id"])
        if o and o.get("wall") and r.get("wall"):
            ratio = r["wall"] / o["wall"]
            if abs(r["wall"] - o["wall"]) > 0.1 and (ratio > 1.25 or ratio < 0.8):
                rows.append(("bench", r["id"], f"wall {o['wall']:.2f} -> {r['wall']:.2f} s ({ratio:.2f}x)",
                             "slower" if ratio > 1 else "faster"))
    return rows, gone, new


def write(run_dir, compare_dir=None):
    by = load(run_dir)
    env = read_json(os.path.join(run_dir, "env.json"), {}) or {}
    meta = read_json(os.path.join(run_dir, "run.json"), {}) or {}
    L = []
    add = L.append
    git = env.get("git") or {}
    add(f"# QA run {os.path.basename(run_dir)}\n")
    add(f"- **Level** {meta.get('level')} · seed {meta.get('seed')} · **wall** {fmt_dur(meta.get('wall'))}"
        f" · {meta.get('workers')} workers · pool utilisation {_f((meta.get('utilisation') or 0) * 100, 0)} %")
    add(f"- **Code** {git.get('branch')} @ {git.get('head')} \"{git.get('subject')}\""
        + (f" + {git.get('dirty_files')} uncommitted files" if git.get("dirty_files") else ""))
    add(f"- **Machine** {env.get('cpu')} · {env.get('physical_cores')} cores / {env.get('logical_cores')} "
        f"threads · RAM {env.get('ram_total_mb')} MB · Python {env.get('python')}"
        + ("" if env.get("python_supported", True) else " (outside 3.11/3.12)"))
    pk = {p["name"]: p for p in env.get("packages", [])}
    add("- **Packages** " + ", ".join(f"{n} {p['installed']}" + (" ⚠" if p["status"] == "out_of_bounds" else "")
                                     for n, p in pk.items() if p.get("installed")))
    if env.get("out_of_bounds"):
        add(f"- ⚠ outside requirements.txt bounds: {', '.join(env['out_of_bounds'])} — this run is "
            "compatibility evidence for them")
    add(f"- cairosvg: {env.get('cairosvg')} · LTspice: "
        f"{(env.get('ltspice') or {}).get('version') or 'not found'}")
    if meta.get("skipped_tasks"):
        add(f"- ⏱ time budget reached: {meta['skipped_tasks']} tasks skipped")
    add("")

    # ---------------- verdict table
    add("## Verdict\n")
    rows = []
    ch = by.get("check", [])
    if ch:
        bad = [c for c in ch if c["status"] == "fail"]
        rows.append(("Existing checks", "FAIL" if bad else "pass",
                     f"{len(ch) - len(bad)}/{len(ch)} pass" + (": " + ", ".join(c["name"] for c in bad) if bad else "")))
    ds = by.get("design", [])
    if ds:
        st = collections.Counter(d.get("status") for d in ds)
        bad = st.get("ui_error", 0) + st.get("app_exception", 0) + st.get("task_error", 0)
        rows.append(("UI (AppTest) designs", "FAIL" if bad else "pass",
                     ", ".join(f"{k} {v}" for k, v in st.most_common())))
        cf, cw = collections.Counter(), collections.Counter()
        for d in ds:
            for k, v in ((d.get("analysis") or {}).get("checks") or {}).items():
                if v.get("status") == "fail":
                    cf[k] += 1
                elif v.get("status") == "warn":
                    cw[k] += 1
        rows.append(("Spec / cascade checks", "FAIL" if cf else ("WARN" if cw else "pass"),
                     ", ".join([f"{k} {v}" for k, v in cf.items()] + [f"{k} {v} (warn)" for k, v in cw.items()])
                     or "all pass"))
        lim = sum(1 for d in ds if (d.get("ui") or {}).get("limits"))
        if lim:
            rows.append(("App-reported limits", "WARN", f"{lim} designs: the app says a spec is not met "
                                                         "(see 'App refusals and limits')"))
        fl = collections.Counter(f["code"] for d in ds for f in (d.get("analysis") or {}).get("flags", []))
        bad = ("unrealizable", "lost_pole", "lost_zero", "pending")     # defects since FS-016
        rows.append(("Pairing flags (FS-016)",
                     "FAIL" if any(fl[c] for c in bad) else ("see report" if fl else "none"),
                     ", ".join(f"{k} {v}" for k, v in fl.most_common())))
    js = by.get("job", [])
    if js:
        oc = collections.Counter(j["outcome"] for j in js)
        bad = sum(oc.get(k, 0) for k in FAIL_OUTCOMES)
        sus = oc.get("NO_BOM_SUSPICIOUS", 0)
        rows.append(("Section solves", "FAIL" if bad else ("WARN" if sus else "pass"),
                     ", ".join(f"{k} {v}" for k, v in oc.most_common())))
    fo = [f for d in ds for v in d.get("variants", []) for f in v.get("first_order", [])]
    if fo:
        err = sum(1 for f in fo if f.get("error"))
        nob = sum(1 for f in fo if not f.get("error") and not f.get("n_snapped"))
        rows.append(("1st-order sections", "FAIL" if err else "pass",
                     f"{len(fo)} solved in-app, {nob} without BOM, {err} errors"))
    e2 = by.get("e2e", [])
    if e2:
        st = collections.Counter(e.get("status") for e in e2)
        rep = [e for e in e2 if e.get("report")]
        rep_ok = sum(1 for e in rep if e["report"].get("ok"))
        rows.append(("End-to-end flows", "FAIL" if st.get("ui_error") or st.get("app_exception") else "pass",
                     ", ".join(f"{k} {v}" for k, v in st.most_common())))
        rows.append(("PDF reports", "FAIL" if rep_ok < len(rep) else "pass", f"{rep_ok}/{len(rep)} valid"))
    lt = by.get("ltspice", [])
    if lt:
        bad = [x for x in lt if x.get("status") != "pass"]
        rows.append(("LTspice runs", "FAIL" if bad else "pass", f"{len(lt) - len(bad)}/{len(lt)} exports pass"))
    for typ, name in (("server", "Source server smoke"), ("build", "Build (build.bat)"), ("exe", "Bundled exe")):
        for r in by.get(typ, []):
            rows.append((name, r.get("status", "?").upper() if r.get("status") != "pass" else "pass",
                         r.get("why") or "; ".join(r.get("problems", [])[:3]) or ""))
    tasks_bad = [r for r in by.get("task_failure", [])]
    if tasks_bad:
        rows.append(("Harness task failures", "FAIL", f"{len(tasks_bad)} (see below)"))
    add(_md_table(rows, ("Area", "Result", "Detail")))

    # ---------------- failures
    add("## Problems to look at\n")
    prob = []
    for c in ch:
        if c["status"] == "fail":
            prob.append(("check", c["name"], f"rc={c['rc']}", c["tail"][-200:]))
    for d in ds:
        if d.get("status") in ("ui_error", "app_exception", "task_error"):
            prob.append(("design", d["id"], d.get("status"), (d.get("error") or "")[:200]))
        for chk, v in ((d.get("analysis") or {}).get("checks") or {}).items():
            if v.get("status") == "fail":
                det = ", ".join(f"{k}={_f(x, 3) if isinstance(x, float) else x}" for k, x in v.items()
                                if k != "status")
                prob.append((f"{chk} check", d["id"], "", det[:200]))
        for e in (d.get("ui") or {}).get("exceptions", [])[:1]:
            prob.append(("app exception", d["id"], "", e.get("message", "")[:200]))
        for msg in (d.get("ui") or {}).get("errors", [])[:2]:
            prob.append(("st.error", d["id"], "", msg[:200]))
        for v in d.get("variants", []):
            if v.get("error"):
                prob.append(("variant", f"{d['id']} | {v['id']}", "", v["error"][:200]))
            for f in v.get("first_order", []):
                if f.get("error"):
                    prob.append(("1st-order", f"{d['id']} | {v['id']}", f.get("topology"), str(f["error"])[:200]))
    for j in js:
        if j["outcome"] in FAIL_OUTCOMES:
            prob.append((j["outcome"], j["cid"], j.get("verdict") or "", (j.get("error") or "")[:200]))
    for e in e2:
        if e.get("status") not in ("ok", "no_bom", "no_sections"):
            prob.append(("e2e", e["id"], e.get("status"), (e.get("error") or "")[:200]))
        elif e.get("report") and not e["report"].get("ok"):
            prob.append(("report", e["id"], "invalid PDF", str(e["report"])))
    for x in lt:
        if x.get("status") != "pass":
            prob.append(("ltspice", x["id"], "", "; ".join(x.get("problems", []))[:240]))
    for t in tasks_bad:
        prob.append(("harness", t.get("label"), t.get("status"), (t.get("error") or "")[:200]))
    # one row per (kind, message shape): count + up to 3 ids that reproduce it
    groups = collections.OrderedDict()
    for kind, rid, info, msg in prob:
        key = (kind, re.sub(r"\d[\d.,e+-]*", "#", f"{info} {msg}")[:120])
        g = groups.setdefault(key, dict(n=0, ids=[], info=info, msg=msg))
        g["n"] += 1
        if len(g["ids"]) < 3:
            g["ids"].append(rid)
    add(_md_table([(k[0], g["n"], "<br>".join(g["ids"]), f"{g['info']} {g['msg']}".strip()[:240])
                   for k, g in groups.items()], ("Kind", "Count", "Reproduce with (first 3)", "Message")))

    pinned = [d for d in ds if (d.get("design") or {}).get("pinned")]
    if pinned:
        add("## Pinned findings (matrix.PINNED)\n")
        add("Known findings kept in every run until fixed, and fixed ones kept as regression "
            "guards. *Still there* = the run shows it.\n")
        rows = []
        for d in pinned:
            why = d["design"]["pinned"]
            codes = sorted({f["code"] for f in (d.get("analysis") or {}).get("flags", [])})
            fails = sorted(k for k, v in ((d.get("analysis") or {}).get("checks") or {}).items()
                           if v.get("status") == "fail")
            seen = (d.get("status") == "app_exception" and "exception" in why) or \
                any(c in why for c in codes)
            rows.append((d["id"], why, d.get("status"), ", ".join(codes + fails) or "–",
                         "still there" if seen else "**not reproduced**"))
        add(_md_table(rows, ("design", "finding", "status", "flags / failed checks", "now")))

    sus = [j for j in js if j["outcome"] == "NO_BOM_SUSPICIOUS"]
    if sus:
        add("## Worth a look: no BOM although the probe says it fits\n")
        add("The solvability probe realises these within the envelope, but the search returned no "
            "BOM: candidates for search effort / seeding (FS-028 S2-4 step 2).\n")
        by_cell = collections.defaultdict(list)
        for j in sus:
            by_cell["+".join(j.get("topologies") or [])].append(j)
        add(_md_table([(c, len(v), "<br>".join(x["cid"] for x in v[:2]))
                       for c, v in sorted(by_cell.items(), key=lambda kv: -len(kv[1]))[:25]],
                      ("cells", "jobs", "examples")))

    # ---------------- what the app refused / reported as not met
    ref = collections.Counter()
    lim = collections.Counter()
    ex_ref, ex_lim = {}, {}
    for d in ds:
        for m in (d.get("ui") or {}).get("refusals", []):
            k = re.sub(r"[-0-9.,]+", "#", m)[:90]
            ref[k] += 1
            ex_ref.setdefault(k, d["id"])
        for m in (d.get("ui") or {}).get("limits", []):
            k = re.sub(r"[-0-9.,]+", "#", m)[:90]
            lim[k] += 1
            ex_lim.setdefault(k, d["id"])
    if ref or lim:
        add("## App refusals and limits\n")
        add("Refusals: the app stops on a spec it cannot design (expected for the wider corners "
            "of the matrix). Limits: the design ran but the app reports a spec it did not meet.\n")
        add(_md_table([("refused", n, m, ex_ref[m]) for m, n in ref.most_common()]
                      + [("limit", n, m, ex_lim[m]) for m, n in lim.most_common()],
                      ("kind", "designs", "message", "example")))

    # ---------------- solve outcomes by dimension
    if js:
        add("## Solve outcomes\n")
        outs = ["OK", "DEGRADED", "NO_BOM_EXPECTED", "NO_BOM_ENVELOPE", "NO_BOM_SUSPICIOUS", "ERROR",
                "TIMEOUT", "CRASH"]
        for dim, getter in (("family", lambda j: j["variant_id"].split("-")[0]),
                            ("op-amp", lambda j: j["variant_id"].split("-")[1]),
                            ("envelope", lambda j: j["variant_id"].split("-")[2]),
                            ("section family", lambda j: (j.get("section_family") or "?")),
                            ("gain", lambda j: "atten override" if "-atten" in j["variant_id"] else "design"),
                            ("preset", lambda j: next((p for p in ("Fast", "Thorough")
                                                       if j["variant_id"].endswith("-" + p)), "Balanced"))):
            tab = collections.defaultdict(collections.Counter)
            for j in js:
                tab[getter(j)][j["outcome"]] += 1
            add(f"**by {dim}**\n")
            add(_md_table([(k, *[tab[k].get(o, 0) for o in outs]) for k in sorted(tab)], (dim, *outs)))
        add("**Worst best-snap costs (with BOM)**\n")
        top = sorted((j for j in js if j.get("best_snap") is not None), key=lambda j: -j["best_snap"])[:12]
        add(_md_table([(j["cid"], _f(j["best_snap"]), _f(j.get("shape_err"), 3), j.get("best_topology"))
                       for j in top], ("Job", "best snap", "shape err", "cell")))

    # ---------------- coverage
    cov = meta.get("coverage") or {}
    if cov:
        add("## Coverage\n")
        add(f"- cells: {cov.get('cells_exercised')} of {cov.get('cells_total')} registered cells "
            "were requested by at least one captured solve")
        if cov.get("cells_untested"):
            add(f"- **not exercised**: {', '.join(cov['cells_untested'])}")
        if cov.get("cells_unknown"):
            add(f"- ⚠ requested but not registered (routing to a missing cell): {', '.join(cov['cells_unknown'])}")
        if cov.get("ui_untested"):
            add(f"- **UI options the matrix does not cover** (add them to matrix.py): "
                f"{', '.join(cov['ui_untested'])}")
        add("")

    dep = _deprecations(run_dir)
    if dep:
        add("## Streamlit deprecations seen\n")
        add(_md_table([(n, m) for m, n in dep], ("workers", "message")))

    # ---------------- performance
    add("## Performance\n")
    prow = []
    if js:
        w = [j.get("wall") for j in js]
        prow.append(("section solve (s)", len(w), _f(_q(w, .5)), _f(_q(w, .9)), _f(max(x for x in w if x is not None))))
    if ds:
        w = [d.get("wall") for d in ds]
        prow.append(("UI design task (s)", len(w), _f(_q(w, .5)), _f(_q(w, .9)), _f(max(x for x in w if x is not None))))
    if e2:
        w = [sum((e.get("steps") or {}).values()) for e in e2]
        prow.append(("end-to-end flow (s)", len(w), _f(_q(w, .5)), _f(_q(w, .9)), _f(max(w))))
    add(_md_table(prow, ("Task", "n", "median", "p90", "max")))
    if js:
        add("**Slowest solves**\n")
        add(_md_table([(j["cid"], _f(j["wall"], 1), j["outcome"]) for j in
                       sorted(js, key=lambda j: -(j.get("wall") or 0))[:10]], ("Job", "wall s", "outcome")))
    bn = by.get("bench", [])
    if bn:
        add("**Benchmark (FS-028 section set, one core each, controlled concurrency; "
            "min of the repeats)**\n")
        by_p = collections.defaultdict(list)
        for b in bn:
            by_p[b["preset"]].append(b)
        add(_md_table([(p, len(v), _f(sum(x["wall"] for x in v), 1), _f(statistics.median(x["wall"] for x in v)),
                        _f(_q([x["wall"] for x in v], .9)), sum(1 for x in v if not x.get("n_snapped")))
                       for p, v in by_p.items()],
                      ("preset", "sections", "total s", "median s", "p90 s", "no BOM")))

    # ---------------- regression
    if compare_dir:
        prev = load(compare_dir)
        rows, gone, new = compare(by, prev)
        add(f"## Regression vs {os.path.basename(compare_dir)}\n")
        worse = [r for r in rows if r[3] in ("WORSE", "slower")]
        add(f"{len(worse)} worse, {len(rows) - len(worse)} other changes; {len(new)} new jobs, "
            f"{len(gone)} jobs gone (routing or matrix change).\n")
        add(_md_table(sorted(rows, key=lambda r: (r[3] not in ("WORSE", "slower"), r[0]))[:150],
                      ("Kind", "Id", "Change", "Verdict")))

    path = os.path.join(run_dir, "summary.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    write_fs016(run_dir, by)
    return path


def write_fs016(run_dir, by):
    """FS-016 Phase-1 input: every pairing flag with the spec that reproduces it."""
    from analysis import FLAG_TEXT
    ds = by.get("design", [])
    groups = collections.defaultdict(list)
    for d in ds:
        for f in (d.get("analysis") or {}).get("flags", []):
            groups[f["code"]].append((d, f))
    L = ["# FS-016 pairing findings\n",
         f"Run `{os.path.basename(run_dir)}`: {len(ds)} designs through the real app "
         "(sidebar → engine → auto-pairing). Each row reproduces with the design id "
         "(response_type_order_frequencies_gain_…; `abs` = 3rd-order absorb on, "
         "first/last/equalize = gain distribution). Full data: results.jsonl (type=design).\n"]
    order = ["lost_pole", "lost_zero", "extra_pole", "extra_zero", "real_pair", "q_lt_half", "br_real_f0",
             "origin_on_jw", "floating_zeros", "pending", "unrealizable", "bp3_vcvs_am", "near_notch", "very_high_q",
             "high_q", "gain_extreme", "far_zero"]
    L.append(_md_table([(c, len(groups.get(c, [])), FLAG_TEXT.get(c, "")) for c in order if groups.get(c)],
                       ("flag", "count", "meaning")))
    for c in order:
        items = groups.get(c)
        if not items:
            continue
        L.append(f"\n## {c} — {FLAG_TEXT.get(c, '')}\n")
        rows = []
        for d, f in items[:60]:
            det = {k: v for k, v in f.items() if k not in ("code", "stage")}
            rows.append((d["id"], f.get("stage"), ", ".join(f"{k}={_f(v, 4) if isinstance(v, float) else v}"
                                                            for k, v in det.items())[:160]))
        L.append(_md_table(rows, ("design", "stage", "detail")))
        if len(items) > 60:
            L.append(f"_{len(items) - 60} more_\n")
    with open(os.path.join(run_dir, "fs016_pairing.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
