# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-028: compare a variant run (ab_lm.py output) with the baseline sweep
(bench_sections.py output): serial CPU, BOM count, best sens_score, best
snap_cost, and whether the baseline's best BOM is still produced.

    python dev/fs028/compare_ab.py results/baseline_balanced.json \
        results/ab_lm_Balanced_ideal.json --variant lm --opamp ideal
"""
import argparse
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS = ["C1", "C2", "C3", "C4", "C1a", "C1b", "C2a", "C2b",
        "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]


def _p(path):
    return path if os.path.isabs(path) or os.path.exists(path) else os.path.join(HERE, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("baseline")
    ap.add_argument("variant_file")
    ap.add_argument("--variant", default="lm")
    ap.add_argument("--opamp", default="ideal")
    ap.add_argument("--preset", default="Balanced")
    a = ap.parse_args()
    base = {r["case"]: r for r in json.load(open(_p(a.baseline)))["rows"]
            if r["opamp"] == a.opamp and r["preset"] == a.preset}
    var = {r["case"]: r for r in json.load(open(_p(a.variant_file)))
           if r["variant"] == a.variant and r["opamp"] == a.opamp}
    print(f"{'case':<12} {'cpu base':>8} {'cpu var':>8} {'x':>6} | {'BOMs':>9} | "
          f"{'best sens b/v':>15} | {'best cost b/v':>17} | base-best kept")
    tb = tv = 0.0
    wins = {"sens_better": 0, "sens_same": 0, "sens_worse": 0, "kept": 0, "lost_all": 0}
    for cid, b in base.items():
        v = var.get(cid)
        if v is None:
            continue
        sb = [s["sens"] for s in b["top_bom"]]
        cb = [s["snap_cost"] for s in b["top_bom"] if s["snap_cost"] is not None]
        q = v["q"]
        best_b = min(sb) if sb else np.nan
        best_v = q.get("best_sens", np.nan)
        kept = None
        if b["top_bom"] and q.get("n"):
            s0 = min(b["top_bom"], key=lambda s: s["sens"])
            sig = [s0["topology"]] + [round(float(s0.get(k) or 0.0), 9) for k in KEYS]
            kept = sig in [list(x) for x in q["sigs"]]
        tb += b["serial_cpu"]; tv += v["cpu"]
        if b["n_snapped"] and not q.get("n"):
            wins["lost_all"] += 1
        if np.isfinite(best_b) and np.isfinite(best_v):
            d = (best_v - best_b) / best_b
            wins["sens_better" if d < -1e-3 else "sens_worse" if d > 1e-3 else "sens_same"] += 1
        wins["kept"] += int(bool(kept))
        print(f"{cid:<12} {b['serial_cpu']:8.1f} {v['cpu']:8.2f} {b['serial_cpu']/max(v['cpu'],1e-9):6.1f} | "
              f"{b['n_snapped']:4d}/{q.get('n', 0):<4d} | {best_b:7.3f}/{best_v:<7.3f} | "
              f"{(min(cb) if cb else np.nan):8.3g}/{q.get('best_cost', np.nan):<8.3g} | {kept}")
    print(f"\nTOTAL serial CPU base {tb:.0f}s  variant {tv:.0f}s  -> x{tb/max(tv,1e-9):.1f}")
    print("best-sens comparison:", wins)


if __name__ == "__main__":
    main()
