# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""FS-029 LTspice test of the per-part vendor-model wrapper.

Writes three netlists that put LMV358A and TL072H (two TI models whose helper
subckts share names such as VOS_SRC_0) in one circuit -- two unity followers
in series, Vs = 5 V, AC 10 Hz .. 10 MHz + .op:

  test_C_flat.cir    both vendor files included at top level (the FS-008
                     failure; expected to stop with a duplicate-subckt error)
  test_A_include.cir FS_<PART>.lib wrappers with '.include <vendor copy>'
                     INSIDE the .subckt (what the app builds)
  test_B_pasted.cir  wrappers with the vendor text pasted inside (fallback)

The vendor files are read from your model folders (spice_opamps.model_dirs())
and copied, unchanged, into the output folder only -- never into the repo.
Default output: <per-user LTspice_Library>/fs029_wrapper_test/.

  python dev/fs029/make_wrapper_test.py [out_dir]
"""
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)

import spice_opamps as SO          # noqa: E402

PARTS = {"LMV358A": "lmv358a.lib", "TL072H": "tl07xh_tl08xh.lib"}


def find(name):
    for d in SO.model_dirs():
        try:
            for fn in os.listdir(d):
                if fn.lower() == name.lower():
                    return os.path.join(d, fn)
        except OSError:
            pass
    return None


def circuit(title, directives, a, b):
    return "\n".join([
        f"* {title}",
        "V1 IN 0 AC 1",
        "VP VCC 0 2.5",
        "VN 0 VEE 2.5",
        f"XU1 IN S1 VCC VEE S1 {a}",
        f"XU2 S1 OUT VCC VEE OUT {b}",
        "RL OUT 0 10k",
        *directives,
        ".ac dec 20 10 10Meg",
        ".op",
        ".end", ""])


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(SO.overlay_dir(), "fs029_wrapper_test")
    os.makedirs(out, exist_ok=True)
    wrapped = {}
    for part, fn in PARTS.items():
        src = find(fn)
        if not src:
            sys.exit(f"{fn} not found in {SO.model_dirs()} -- add the {part} model in the app first")
        with open(src, "rb") as fh:
            data = fh.read()
        cand = SO.model_candidates(fn, data, part)[0]
        if cand["roles"] != list(SO.WRAP_PORTS):     # the flat control calls it directly
            sys.exit(f"{part}: unexpected pin order in {fn}: {cand['pins']}")
        vend = SO.vendor_copy_name(part, fn)
        with open(os.path.join(out, vend), "wb") as fh:          # as the app stores it
            fh.write(SO.localize_model(data)[0])
        shutil.copyfile(src, os.path.join(out, fn))           # for the flat control
        wa = SO.wrapper_text(part, vend, cand["subckt"], cand["roles"])
        with open(os.path.join(out, SO.wrapper_name(part)), "w", newline="") as fh:
            fh.write(wa)
        # variant B: the vendor text pasted where the .include line is
        text = data.decode("latin-1").replace("\r\n", "\n")
        text = "\n".join(ln for ln in text.split("\n")          # a pasted .END would end
                         if ln.strip().lower() != ".end")        # the whole netlist
        wb = wa.replace("\r\n", "\n").replace(f".include {vend}\n", text.rstrip("\n") + "\n")
        wb = wb.replace(f".subckt FS_{part} ", f".subckt FS_{part}_B ").replace(
            f".ends FS_{part}", f".ends FS_{part}_B")
        with open(os.path.join(out, f"FS_{part}_B.lib"), "w", encoding="latin-1", newline="") as fh:
            fh.write(wb.replace("\n", "\r\n"))
        wrapped[part] = cand["subckt"]
    a, b = list(PARTS)
    sa, sb = wrapped[a], wrapped[b]
    tests = {
        "test_C_flat.cir": circuit("FS-029 control: both TI models flat (expected to fail)",
                                   [f".include {PARTS[a]}", f".include {PARTS[b]}"], sa, sb),
        "test_A_include.cir": circuit("FS-029 variant A: .include inside the wrapper subckt",
                                      [f".include FS_{a}.lib", f".include FS_{b}.lib"],
                                      f"FS_{a}", f"FS_{b}"),
        "test_B_pasted.cir": circuit("FS-029 variant B: vendor text pasted inside the wrapper",
                                     [f".include FS_{a}_B.lib", f".include FS_{b}_B.lib"],
                                     f"FS_{a}_B", f"FS_{b}_B"),
    }
    for name, text in tests.items():
        with open(os.path.join(out, name), "w", newline="") as fh:
            fh.write(text.replace("\n", "\r\n"))
    print(f"written to {out}:")
    for fn in sorted(os.listdir(out)):
        print("  " + fn)


if __name__ == "__main__":
    main()
