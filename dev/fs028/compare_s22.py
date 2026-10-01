# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028 S2-2: compare two bench_sections.py runs (baseline vs new) by the
§6 validation rule of dev/FS-028_solver_performance_analysis.md:

  best sens_score  <= baseline + 1 %      best snap_cost <= baseline
  BOM count        >= baseline            (every deviation is listed)

plus list quality (median sens of the best 10 BOMs), whether the baseline's
best BOM is still produced, and time (serial CPU of the section solve).

    python dev/fs028/compare_s22.py results/base_s21.json results/s22_v1.json
    python dev/fs028/compare_s22.py BASE NEW --opamp tl072 --md out.md
"""
import argparse
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = ["C1", "C2", "C3", "C4", "C1a", "C1b", "C2a", "C2b",
        "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]
SENS_TOL = 0.01


def _p(path):
    return path if os.path.isabs(path) or os.path.exists(path) else os.path.join(HERE, path)


def _rows(path, opamp, preset):
    return {r["case"]: r for r in json.load(open(_p(path)))["rows"]
            if r["opamp"] == opamp and r["preset"] == preset}


def _sig(b):
    return tuple([b["topology"]] + [round(float(b.get(k) or 0.0), 9) for k in KEYS])


def _q(row):
    q = row.get("snapped_q")
    if q is None:                       # older result files: top-5 only
        q = [[b["topology"], b["sens"], b["snap_cost"]] for b in row["top_bom"]]
    sens = [s for _t, s, _c in q]
    cost = [c for _t, _s, c in q if c is not None]
    return {"n": row["n_snapped"],
            "best_sens": min(sens) if sens else np.nan,
            "best_cost": min(cost) if cost else np.nan,
            "med10": float(np.median(sens[:10])) if sens else np.nan,
            "cost_med10": float(np.median([c for _t, _s, c in q[:10] if c is not None]))
                          if cost else np.nan,
            "top": {_sig(b) for b in row["top_bom"]},
            "best_sig": _sig(min(row["top_bom"], key=lambda b: b["sens"])) if row["top_bom"] else None,
            "cpu": row.get("serial_cpu", np.nan)}


def compare(base, new, opamp="ideal", preset="Balanced"):
    B, N = _rows(base, opamp, preset), _rows(new, opamp, preset)
    out = []
    for cid in B:
        if cid not in N:
            continue
        b, n = _q(B[cid]), _q(N[cid])
        flags = []
        if n["n"] < b["n"]:
            flags.append("count")
        if np.isfinite(b["best_sens"]):
            if not np.isfinite(n["best_sens"]):
                flags.append("lost all")
            elif n["best_sens"] > b["best_sens"] * (1 + SENS_TOL):
                flags.append("sens")
        if np.isfinite(b["best_cost"]) and np.isfinite(n["best_cost"]) \
                and n["best_cost"] > b["best_cost"] * (1 + SENS_TOL):
            flags.append("snap_cost")
        if not np.isfinite(b["best_sens"]) and np.isfinite(n["best_sens"]):
            flags.append("NEW BOMs")
        out.append(dict(case=cid, b=b, n=n, flags=flags,
                        kept=(b["best_sig"] in n["top"]) if b["best_sig"] else None))
    return out


def _fmt(x, f="{:.3f}"):
    return "—" if x is None or not np.isfinite(x) else f.format(x)


def report(rows, md=False):
    hdr = ["case", "BOMs b/n", "best sens b/n", "best snap cost b/n", "median-10 sens b/n",
           "best kept", "CPU s b/n", "§6 flags"]
    lines = []
    if md:
        lines.append("| " + " | ".join(hdr) + " |")
        lines.append("|" + "---|" * len(hdr))
    else:
        lines.append(f"{'case':<12} {'BOMs':>7} | {'best sens b/n':>17} | {'best cost b/n':>19} | "
                     f"{'med10 b/n':>15} | kept | {'cpu b/n':>13} | flags")
    for r in rows:
        b, n = r["b"], r["n"]
        cells = [r["case"], f"{b['n']}/{n['n']}",
                 f"{_fmt(b['best_sens'])}/{_fmt(n['best_sens'])}",
                 f"{_fmt(b['best_cost'], '{:.3g}')}/{_fmt(n['best_cost'], '{:.3g}')}",
                 f"{_fmt(b['med10'])}/{_fmt(n['med10'])}",
                 {True: "yes", False: "no", None: "—"}[r["kept"]],
                 f"{_fmt(b['cpu'], '{:.1f}')}/{_fmt(n['cpu'], '{:.2f}')}",
                 ", ".join(r["flags"]) or "ok"]
        if md:
            lines.append("| " + " | ".join(cells) + " |")
        else:
            lines.append(f"{cells[0]:<12} {cells[1]:>7} | {cells[2]:>17} | {cells[3]:>19} | "
                         f"{cells[4]:>15} | {cells[5]:>4} | {cells[6]:>13} | {cells[7]}")
    fin = [r for r in rows if np.isfinite(r["b"]["best_sens"]) and np.isfinite(r["n"]["best_sens"])]
    d = np.array([r["n"]["best_sens"] / r["b"]["best_sens"] - 1 for r in fin])
    fc = [r for r in rows if np.isfinite(r["b"]["best_cost"]) and np.isfinite(r["n"]["best_cost"])]
    dc = np.array([r["n"]["best_cost"] / r["b"]["best_cost"] - 1 for r in fc])
    dm = np.array([r["n"]["cost_med10"] / r["b"]["cost_med10"] - 1 for r in fc])
    tb = sum(r["b"]["cpu"] for r in rows)
    tn = sum(r["n"]["cpu"] for r in rows)
    summ = {"cases": len(rows),
            "best sens better / same (±1 %) / worse": (int(np.sum(d < -SENS_TOL)),
                                                        int(np.sum(np.abs(d) <= SENS_TOL)),
                                                        int(np.sum(d > SENS_TOL))),
            "best snap_cost better / same (±1 %) / worse": (
                int(np.sum(dc < -SENS_TOL)), int(np.sum(np.abs(dc) <= SENS_TOL)),
                int(np.sum(dc > SENS_TOL))),
            "median-10 snap_cost better / same / worse; geo-mean ratio n/b": (
                int(np.sum(dm < -SENS_TOL)), int(np.sum(np.abs(dm) <= SENS_TOL)),
                int(np.sum(dm > SENS_TOL)), round(float(np.exp(np.mean(np.log1p(dm)))), 3)),
            "best BOM kept": sum(1 for r in rows if r["kept"]),
            "no-BOM sections b/n": (sum(1 for r in rows if r["b"]["n"] == 0),
                                    sum(1 for r in rows if r["n"]["n"] == 0)),
            "flagged": [r["case"] for r in rows if r["flags"]],
            "serial CPU b/n": (round(tb, 1), round(tn, 2), f"x{tb / max(tn, 1e-9):.0f}")}
    return "\n".join(lines), summ


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("base")
    ap.add_argument("new")
    ap.add_argument("--opamp", nargs="+", default=["ideal", "tl072"])
    ap.add_argument("--preset", default="Balanced")
    ap.add_argument("--md", default=None, help="also write markdown tables here")
    a = ap.parse_args()
    md_out = []
    for op in a.opamp:
        rows = compare(a.base, a.new, op, a.preset)
        txt, summ = report(rows)
        print(f"\n=== {a.preset}, op-amp {op} ===\n{txt}")
        for k, v in summ.items():
            print(f"  {k}: {v}")
        if a.md:
            t, _s = report(rows, md=True)
            md_out.append(f"### {a.preset}, op-amp {op}\n\n{t}\n\n"
                          + "\n".join(f"- {k}: {v}" for k, v in summ.items()) + "\n")
    if a.md:
        with open(a.md, "w", encoding="utf-8") as f:
            f.write("\n".join(md_out))


if __name__ == "__main__":
    main()
