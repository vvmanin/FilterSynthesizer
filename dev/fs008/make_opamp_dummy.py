# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Generate an op-amp dummy for the LTspice export (FS-008, design note App. A.7).

Kinds B (external model file) and C (model text embedded) with LTspice's opamp2
symbol and the FS generic adapter wiring, so nothing has to be drawn:

  python dev/fs008/make_opamp_dummy.py TL072 --subckt TL072 --lib TL072.lib ^
      --pins inp,inn,vp,vn,out --vs-min 9 --vs-max 36 ^
      --source https://www.ti.com/product/TL072 --note "TI PSpice model"

  --pins   the model's .subckt pin order, written with inp inn vp vn out
           (read it off the model's .subckt line and its comment). When it
           differs from opamp2's order (inp inn vp vn out) a wrapper subckt
           <subckt>_FS is written and the symbol calls the wrapper.
  --lib    model file name (kind B): put the file in LTspice_Library/models/
           or the user overlay's models/ -- vendor files are never committed.
  --embed  a file whose .subckt text is pasted into the dummy instead (kind C;
           only own / permissively licensed text belongs in the repo).
  --user   write into the per-user overlay instead of LTspice_Library/opamps.

Afterwards: run the dummy check (printed here, and in the export's model
table), then set `spice_model` of the part to the stem (op-amp Edit popover).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

import spice_opamps as SO       # noqa: E402

OPAMP2_ORDER = ["inp", "inn", "vp", "vn", "out"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("stem")
    ap.add_argument("--subckt", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--lib")
    src.add_argument("--embed")
    ap.add_argument("--pins", default=",".join(OPAMP2_ORDER))
    ap.add_argument("--vs-min", type=float)
    ap.add_argument("--vs-max", type=float)
    ap.add_argument("--source", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--user", action="store_true")
    a = ap.parse_args()

    pins = [p.strip().lower() for p in a.pins.split(",")]
    if sorted(pins) != sorted(OPAMP2_ORDER):
        sys.exit(f"--pins must be a permutation of {','.join(OPAMP2_ORDER)}")
    directives = []
    if a.lib:
        directives.append(f".lib {a.lib}")
    else:
        with open(a.embed, encoding="ascii") as fh:
            directives.append(fh.read().strip().replace("\r\n", "\n").replace("\n", "\\n"))
    value = a.subckt
    if pins != OPAMP2_ORDER:
        value = f"{a.subckt}_FS"
        directives.append(f".subckt {value} {' '.join(OPAMP2_ORDER)}\\n"
                          f"X1 {' '.join(pins)} {a.subckt}\\n.ends {value}")
    meta = []
    if a.vs_min is not None:
        meta.append(f"vs_min={a.vs_min:g}")
    if a.vs_max is not None:
        meta.append(f"vs_max={a.vs_max:g}")
    if a.source:
        meta.append(f"source={a.source}")
    if a.note:
        meta.append(f"note={a.note}")

    folder = os.path.join(SO.overlay_dir() if a.user else SO.library_dir(), "opamps")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, a.stem + ".asc")
    with open(path, "w", encoding="ascii", newline="") as fh:
        fh.write(SO.opamp2_dummy_text(value, " ".join(meta) or "note=", directives))
    d = SO.load_dummy(path, "user" if a.user else "built-in")
    print(f"written {path}")
    for e in d["errors"]:
        print(f"  ERROR   {e}")
    for w in d["warnings"]:
        print(f"  warning {w}")
    if not d["errors"]:
        print(f"  dummy check ok; X line: XU1 {' '.join(r or 'NC' for r, _ in d['xpins'])} "
              f"{d['xmodel']}")
        print(f"  next: set the part's SPICE model to '{a.stem}' (op-amp Edit popover)")


if __name__ == "__main__":
    main()
