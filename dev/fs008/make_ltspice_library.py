# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""Write the tool-generated files of LTspice_Library/ (FS-008 phase 2).

    python dev/fs008/make_ltspice_library.py [--force]

Writes only the files that are missing; --force rewrites them. Once the
maintainer has opened a file in LTspice and saved it back (phase 2b), that
saved copy is the authority -- do not --force over it.

  symbols.asc               symbol calibration (res, cap, voltage, bv, opamp2)
  opamps/_FS_generic.asc    FS generic dummy (opamp2 wired into the seat)
  opamps/_seat_template.asc empty seat to start a new dummy from (App. A.3)
  cells/_cell_template.asc  start of a hand-drawn cell template (App. B)
  models/README.txt         where user-supplied model files go
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

import spice_asc as SA          # noqa: E402
import spice_opamps as SO       # noqa: E402

FILES = {
    "symbols.asc": SA.calibration_text,
    os.path.join("opamps", "_FS_generic.asc"): SO.fs_generic_dummy_text,
    os.path.join("opamps", "_seat_template.asc"): SO.seat_template_text,
    os.path.join("cells", "_cell_template.asc"): SO.cell_template_text,
    os.path.join("models", "README.txt"): SO.models_readme_text,
}


def main():
    force = "--force" in sys.argv
    lib = os.path.join(ROOT, "LTspice_Library")
    for rel, make in FILES.items():
        path = os.path.join(lib, rel)
        if os.path.exists(path) and not force:
            print(f"  kept     {rel}")
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="ascii", newline="") as fh:
            fh.write(make())
        print(f"  written  {rel}")


if __name__ == "__main__":
    main()
