# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028: print the markdown tables of dev/FS-028_solver_performance_analysis.md
from the raw results in dev/fs028/results/ (so every number in the note can be
regenerated).

    python dev/fs028/make_tables.py
"""
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")


def _load(name):
    p = os.path.join(RES, name)
    if not os.path.exists(p):
        return None
    if name.endswith(".jsonl"):
        out = {}
        with open(p) as f:
            for line in f:
                r = json.loads(line)
                out[r["case"]] = r                      # last row per case wins
        return out
    with open(p) as f:
        return json.load(f)


def baseline_table(base, opamp="ideal"):
    rows = [r for r in base["rows"] if r["opamp"] == opamp and r["preset"] == "Balanced"]
    print(f"\n### Baseline, Balanced, op-amp {opamp} (serial CPU-seconds, this machine)\n")
    print("| Case | Cells | Serial CPU | Phase 1 | Phase 3 | Derive (main) | Init/worker | "
          "32-core model | BOMs |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        st, tc = r["stage"], r["task_cpu"]
        init = st.get("init_p13", 0) + st.get("init_ni", 0)
        print(f"| {r['case']} | {', '.join(r['topos'])} | {r['serial_cpu']:.1f} | "
              f"{tc.get('p1', 0):.1f} | {tc.get('p3', 0):.1f} | "
              f"{st.get('get_cases@main', 0):.1f} | {init:.2f} | "
              f"{r['model32']['total']:.1f} | {r['n_snapped']} |")
    tot = sum(r["serial_cpu"] for r in rows)
    p1 = sum(r["task_cpu"].get("p1", 0) for r in rows)
    p3 = sum(r["task_cpu"].get("p3", 0) for r in rows)
    m = [r["model32"]["total"] for r in rows]
    print(f"\nTotal serial CPU {tot:.0f} s (Phase 1 {100*p1/tot:.0f} %, Phase 3 {100*p3/tot:.0f} %); "
          f"32-core model median {np.median(m):.1f} s, mean {np.mean(m):.1f} s, max {max(m):.1f} s.")


def variant_table(base, var_rows, variant, opamp="ideal"):
    b = {r["case"]: r for r in base["rows"] if r["opamp"] == opamp and r["preset"] == "Balanced"}
    v = {r["case"]: r for r in var_rows if r["variant"] == variant and r["opamp"] == opamp}
    cases = [c for c in b if c in v]
    if not cases:
        return
    print(f"\n### Variant `{variant}` vs baseline (Balanced, {opamp})\n")
    print("| Case | CPU base | CPU var | Speed-up | BOMs b/v | Best sens b/v | Best snap cost b/v |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    tb = tv = 0.0
    better = same = worse = 0
    for c in cases:
        rb, rv = b[c], v[c]
        sb = min((s["sens"] for s in rb["top_bom"]), default=np.nan)
        cb = min((s["snap_cost"] for s in rb["top_bom"] if s["snap_cost"] is not None),
                 default=np.nan)
        q = rv["q"]
        sv = q.get("best_sens", np.nan); cv = q.get("best_cost", np.nan)
        tb += rb["serial_cpu"]; tv += rv["cpu"]
        if np.isfinite(sb) and np.isfinite(sv):
            d = (sv - sb) / sb
            if d < -1e-3:
                better += 1
            elif d > 1e-3:
                worse += 1
            else:
                same += 1
        print(f"| {c} | {rb['serial_cpu']:.1f} | {rv['cpu']:.2f} | "
              f"{rb['serial_cpu']/max(rv['cpu'], 1e-9):.1f}x | {rb['n_snapped']}/{q.get('n', 0)} | "
              f"{sb:.3f}/{sv:.3f} | {cb:.3g}/{cv:.3g} |")
    print(f"\nTotal serial CPU {tb:.0f} s -> {tv:.0f} s (x{tb/max(tv,1e-9):.1f}); best sens "
          f"better {better}, same {same}, worse {worse} (0.1 % band).")


def batch_table(base, p1, p3):
    b = {r["case"]: r for r in base["rows"] if r["opamp"] == "ideal" and r["preset"] == "Balanced"}
    print("\n### Batched LM, single core (Balanced, ideal)\n")
    print("| Case | Phase 1 TRF (pipeline) | Phase 1 batch | x | Phase-1 valleys (batch) | "
          "Phase 3 TRF (same tasks) | Phase 3 batch | x | tasks accepted TRF/batch |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    s1t = s1b = s3t = s3b = 0.0
    for c, rb in b.items():
        r1 = (p1 or {}).get(c); r3 = (p3 or {}).get(c)
        t1 = rb["task_cpu"].get("p1", 0.0)
        cells = [f"| {c} | {t1:.1f}"]
        if r1 and "batch" in r1:
            bb = r1["batch"]["solve"]
            vb = sum(r1["batch"]["valleys"].values())
            cells.append(f"{bb:.3f} | {t1/max(bb,1e-9):.0f}x | {vb}")
            s1t += t1; s1b += bb
        else:
            cells.append("- | - | -")
        if r3 and "batch_s" in r3 and not r3.get("tasks"):
            cells.append("- | - | - | no Phase-3 tasks")
        elif r3 and "batch_s" in r3:
            tt = r3.get("trf_s")
            cells.append(f"{tt:.2f}" if tt is not None else "-")
            cells.append(f"{r3['batch_s']:.3f}")
            cells.append(f"{tt/max(r3['batch_s'],1e-9):.0f}x" if tt is not None else "-")
            cells.append(f"{r3.get('trf_ok', '-')}/{r3['batch_ok']} of {r3['tasks']}")
            if tt is not None:
                s3t += tt; s3b += r3["batch_s"]
        else:
            cells.append("- | - | - | -")
        print(" | ".join(cells) + " |")
    if s1b:
        print(f"\nPhase 1 total {s1t:.0f} s -> {s1b:.1f} s (x{s1t/s1b:.0f}); "
              f"Phase 3 total {s3t:.0f} s -> {s3b:.1f} s (x{s3t/max(s3b,1e-9):.0f}).")


def coeff_table(base, ab, p1b, p3b, opamp="ideal"):
    """Relative solving-time coefficients (sum new / sum baseline) per approach.

    Measured end to end: A (trf-loose-tol, trf-loose) and C (lm, lm-p3) from ab_lm.
    Assembled from measured stage times (est.):
      B  compile once: CPU = p1 + p3 + zm + ni + snap + harvest + dedup; wall = the
         32-worker schedule of p1/p3/zm/ni + snap + harvest + dedup (no derive, no
         lambdify, no pool start-up, no per-worker init).
      D  B + batched LM, one process: CPU = wall = batch p1 + 2 x batch p3 (Phase 3
         plus an equal allowance for the R5 ladder) + Phase-3 bookkeeping outside
         least_squares + ni + snap + harvest + dedup. The Phase-3 batch time comes from
         probe_batch_p3 (task list from an LM Phase 1: representative, not identical).
      E  D with Phase 1 = learned seeds (30 ms per cell) + 1/4 of the cold batch.
    """
    b = {r["case"]: r for r in base["rows"] if r["opamp"] == opamp and r["preset"] == "Balanced"}
    ab = [r for r in (ab or []) if r["opamp"] == "ideal"]
    var = {}
    for r in ab:
        var.setdefault(r["variant"], {})[r["case"]] = r
    rows = {}

    def add(name, case, cpu, wall, burn=None):
        rows.setdefault(name, {})[case] = (cpu, wall, cpu if burn is None else burn)
    for c, r in b.items():
        st, tc, m = r["stage"], r["task_cpu"], r["model32"]
        # every one of the 32 workers runs the initializer: 31 more copies of it
        rep = 31 * (st.get("init_p13", 0) + st.get("init_ni", 0))
        add("baseline", c, r["serial_cpu"], m["total"], r["serial_cpu"] + rep)
        small = st.get("harvest", 0) + st.get("dedup", 0)
        cpu_b = (tc.get("p1", 0) + tc.get("p3", 0) + tc.get("zm", 0) + tc.get("ni", 0)
                 + st.get("snap", 0) + small)
        wall_b = m["p1"] + m["p3"] + m["zm"] + m["ni"] + st.get("snap", 0) + small
        add("B compile once (est.)", c, cpu_b, wall_b)
        if opamp == "ideal":
            for vn, label in (("trf-loose-tol", "A1 TRF tolerance 1e-7"),
                              ("trf-loose", "A2 TRF 1e-8 + budgets /4"),
                              ("lm", "C  scalar LM, Phase 1+3"),
                              ("lm-p3", "C' scalar LM, Phase 3 only"),
                              ("lm-p3pol", "C'' scalar LM + Phase-3 polish")):
                v = var.get(vn, {}).get(c)
                if v is not None:
                    vrep = 31 * (v["stage"].get("init_p13", 0) + v["stage"].get("init_ni", 0))
                    add(label, c, v["cpu"], v["model32"]["total"], v["cpu"] + vrep)
        r1 = (p1b or {}).get(c); r3 = (p3b or {}).get(c)
        if r1 and r3 and "batch" in r1 and "batch_s" in r3:
            ls3 = r["least_squares"].get("p3", {}).get("t", 0.0)
            book = max(0.0, tc.get("p3", 0) - ls3)
            rest = tc.get("ni", 0) + st.get("snap", 0) + small + book
            cpu_d = r1["batch"]["solve"] + 2 * r3["batch_s"] + rest
            add("D  B + batched LM, 1 core (est.)", c, cpu_d, cpu_d)
            ncell = len(r1["batch"]["valleys"]) or 1
            cpu_e = 0.03 * ncell + 0.25 * r1["batch"]["solve"] + 2 * r3["batch_s"] + rest
            add("E  D + learned seeds (est.)", c, cpu_e, cpu_e)
    print(f"\n### Relative solving-time coefficients (Balanced, op-amp {opamp})\n")
    print("| Approach | Sections | CPU coefficient (range) | Core-s burned, 32-worker box | "
          "Wall coefficient, 32 cores (range) | Mean CPU-s / section | Mean wall-s / section |")
    print("|---|---:|---|---:|---|---:|---:|")
    base_rows = rows["baseline"]
    for name, d in rows.items():
        cs = [c for c in d if c in base_rows]
        if not cs:
            continue
        cb = sum(base_rows[c][0] for c in cs); wb = sum(base_rows[c][1] for c in cs)
        cn = sum(d[c][0] for c in cs); wn = sum(d[c][1] for c in cs)
        bb = sum(base_rows[c][2] for c in cs); bn = sum(d[c][2] for c in cs)
        rc = [d[c][0] / base_rows[c][0] for c in cs]
        rw = [d[c][1] / base_rows[c][1] for c in cs]
        print(f"| {name} | {len(cs)} | {cn/cb:.3f} ({min(rc):.3f}-{max(rc):.2f}) | "
              f"{bn/bb:.3f} | {wn/wb:.3f} ({min(rw):.3f}-{max(rw):.2f}) | {cn/len(cs):.2f} | "
              f"{wn/len(cs):.2f} |")


def preset_table():
    """Fast / Balanced / Thorough baselines (ideal op-amp), whatever has been run."""
    rows = []
    for tag in ("fast", "balanced", "thorough"):
        d = _load(f"baseline_{tag}.json")
        if d is None:
            continue
        rr = [r for r in d["rows"] if r["opamp"] == "ideal"]
        if rr:
            rows.append((tag, rr))
    if not rows:
        return
    ref = {r["case"]: r for tag, rr in rows if tag == "balanced" for r in rr}
    print("\n### Search presets (ideal op-amp)\n")
    print("| Preset | Sections | Serial CPU (vs Balanced, same sections) | 32-core model mean / median / max "
          "(vs Balanced) | BOMs |")
    print("|---|---:|---|---|---:|")
    for tag, rr in rows:
        cs = [r["case"] for r in rr if r["case"] in ref]
        cpu = sum(r["serial_cpu"] for r in rr if r["case"] in ref)
        cpu_b = sum(ref[c]["serial_cpu"] for c in cs)
        m = [r["model32"]["total"] for r in rr if r["case"] in ref]
        m_b = sum(ref[c]["model32"]["total"] for c in cs)
        print(f"| {tag.capitalize()} | {len(cs)} | {cpu:.0f} s ({cpu/max(cpu_b,1e-9):.2f}x) | "
              f"{np.mean(m):.2f} / {np.median(m):.2f} / {max(m):.1f} s ({sum(m)/max(m_b,1e-9):.2f}x) | "
              f"{sum(r['n_snapped'] for r in rr if r['case'] in ref)} |")


def main():
    base = _load("baseline_balanced.json")
    if base is None:
        print("no baseline_balanced.json -- run bench_sections.py first")
        return
    baseline_table(base, "ideal")
    baseline_table(base, "tl072")
    preset_table()
    ab = _load("ab_lm_Balanced_ideal.json") or []
    for v in ("trf-loose-tol", "trf-loose", "lm", "lm-p3", "lm-p3pol"):
        variant_table(base, ab, v)
    p1b = _load("probe_batch_p1_Balanced.jsonl"); p3b = _load("probe_batch_p3_Balanced.jsonl")
    batch_table(base, p1b, p3b)
    coeff_table(base, ab, p1b, p3b, "ideal")
    coeff_table(base, ab, p1b, p3b, "tl072")


if __name__ == "__main__":
    main()
