# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

"""
schematic_svg.py  —  overlay designators + values onto a section schematic.

ARCHITECTURE
------------
You (the user) provide one finished SVG per topology, all drawn on the SAME
canvas with surviving components in the SAME positions. This module does NOT
draw or remove any circuit geometry. For a given section it just:
  1. loads the topology's base SVG,
  2. writes a designator + value next to each component THAT IS PRESENT,
  3. skips (suppresses) the label for any component whose BOM value is absent.

Designator format:  "{stage}{SYMBOL}"  ->  1C1, 2R6, 3R5
Split parallel C2:  "{stage}C2.1" / "{stage}C2.2"  (values C2a / C2b)
Op-amp:             "{stage}U1"  (one op-amp per section, stage prefixed)

Only dependency is the value formatters from discrete_snapper.
"""

import os
import re
import re

from discrete_snapper import _fmt_cap, _fmt_res

# ======================= EDIT HERE · SVG FOLDER ===========================
# Folder holding the topology SVGs (named like "2LPn-g-R7-C2s.drawio.svg").
# Resolution order:
#   1) env var FILTERSYNTHESIZER_SVG_DIR -- used by the packaged EXE launcher to point
#      at the user-editable folder NEXT TO the EXE (so SVGs can be added or
#      replaced without rebuilding the bundle).
#   2) this module's folder + "Section_Schematic_Diagrams" -- the dev default.
# Override from the app if they live elsewhere, e.g.:
#      schematic_svg.SVG_DIR = r"C:\\...\\Diagram Builder"
SVG_DIR = (os.environ.get("FILTERSYNTHESIZER_SVG_DIR")
           or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "Section_Schematic_Diagrams"))

# =========================================================================

# ======================= EDIT HERE · FONTS / STYLE ========================
FONT_FAMILY  = "Segoe UI, Arial, sans-serif"
DESIG_SIZE   = 14        # designator font size, px
DESIG_WEIGHT = "700"       # "400" normal · "700" bold
DESIG_COLOR  = "#16202e"
VALUE_SIZE   = 12        # value font size, px
VALUE_WEIGHT = "500"
VALUE_COLOR  = "#1f6fb2"
LINE_GAP     = 16         # px from designator baseline down to value baseline
# =========================================================================

# ===================== EDIT HERE · LABEL POSITIONS =======================
# Coordinates are in the SVG's OWN viewBox units. Origin is TOP-LEFT;
# x increases to the right, y increases DOWNWARD.
# Each entry:  "SYMBOL": (x, y, align)   align in {"start","middle","end"}.
# (x, y) is the baseline of the DESIGNATOR; the value prints LINE_GAP below.
# These match the 827x583 master. Surviving parts keep these positions in
# every reduced SVG, so this one table serves all topologies.
ANCHORS = {
    "R1": (120, 230, "middle"),   # signal row, left
    "R2": (240, 230, "middle"),   # signal row, mid
    "R3": (360, 230, "middle"),   # signal row, right (into op-amp +)
    "R4": (240,  66, "middle"),   # upper row, left  (Va - Vm)
    "R5": (610,  66, "middle"),   # upper row, right (Vm - Vout)
    "R6": (555, 410, "start"),    # vertical, gain-set (Vm - GND)
    "R7": (312, 410, "start"),    # vertical, (Vb - GND)
    "C1": (228, 410, "end"),      # vertical shunt, left  (Va - GND)
    "C2": (210, 294, "middle"),   # horizontal, (Va - Vc)
    "C3": (440, 410, "start"),    # vertical shunt, mid   (Vc - GND)
    "C4": (385, 134, "middle"),   # horizontal, (Vb - Vout)
    # split-C2 variants (used only when the provided SVG shows two parallel caps)
    "C2.1": (210, 294, "middle"),
    "C2.2": (210, 349, "middle"),
    # op-amp designator -> prints "{stage}U1"
    "U":   (624, 294, "middle"),
}
# =========================================================================

# =========================================================================
# 1st-ORDER cells. The ni families share 3 drawn variants per family with a
# COMMON designator/position system (one anchor table, absent parts suppressed);
# the inv families share a single schematic. Coords are tuned to the 827x583
# master canvas (same viewBox as ANCHORS), via designator_tuner.html.
# Designators present:  1*-ni : R1,R2,R3,R4,C1,U ;  1*-inv : R1,R2,C1,U.
_FO_CELL_RE = re.compile(r"^1(LP|HP)-(ni|inv)-(atten|unity|gained)$")

# HP order-2/3 VCVS cells (item 2). gain is a clean 3-way axis (no +R7 twin);
# HP cells never use parallel-C2, so there is no "-C2s" variant. The artwork is
# named exactly by the canonical cell name, e.g. "3HPn-unity.drawio.svg".
_HP_CELL_RE = re.compile(r"^(\d+)(HPn|HP)-(unity|gained|atten)(\+R8)?$")

# Unified designator-placement map shared by all 12 HP topologies (surviving
# parts keep these positions in every reduced HP SVG, mirroring the LP master).
# R8 is the optional b->gnd feedback attenuator on the +R8 twins; it sits in the
# same slot as C4 (both connect node b to gnd, and are never present together).
ANCHORS_HP = {
    "R1": (210, 410, "middle"), "R2": (240, 335, "middle"),
    "R3": (450, 410, "middle"), "R4": (240,  66, "middle"),
    "R5": (610,  66, "middle"), "R6": (360, 125, "middle"),
    "R7": (570, 410, "middle"), "R8": (335, 410, "middle"),
    "C1": (145, 235, "middle"), "C2": (265, 235, "middle"),
    "C3": (385, 235, "middle"), "C4": (335, 410, "middle"),
    "U":  (624, 294, "middle"),
}

# VCVS pure-notch (2N) cells (item: notch). One inverting biquad on the 827x583
# master. "2N" carries R6 (the gain-set m->gnd leg); "2N-atten" is the SAME
# artwork/positions WITHOUT R6 -- its R6 value is absent in the BOM, so that one
# label is suppressed automatically and the single table serves both cells.
# Designators present:  2N : R1..R6, C1, C2, U ;  2N-atten : R1..R5, C1, C2, U.
_NOTCH_CELL_RE = re.compile(r"^2N(-atten)?$")

ANCHORS_NOTCH = {
    "R1": (240, 230, "middle"), "R2": (360, 125, "middle"),
    "R3": (450, 410, "middle"), "R4": (240,  66, "middle"),
    "R5": (610,  66, "middle"), "R6": (570, 410, "middle"),
    "C1": (335, 410, "middle"), "C2": (385, 235, "middle"),
    "U":  (624, 294, "middle"),
}


# VCVS Sallen-Key band-pass cells (item 5). One canvas serves both: the gained
# "2BP" (R1..R5, C1, C2, U) and the unity-buffer "2BP-atten" whose R4 (=0.0) and
# R5 (=None) auto-suppress via _present, leaving R1..R3, C1, C2, U.
# NOTE: these designator coordinates are PLACEHOLDERS mirroring the notch layout
# so the label machinery is wired and correct; swap in the real Diagram-Builder
# anchors when the "2BP.drawio.svg" canvas is finalized.
_BP_CELL_RE = re.compile(r"^2BP(-atten)?$")

ANCHORS_BP = {
    "R1": (240, 230, "middle"), "R2": (360, 125, "middle"),
    "R3": (450, 410, "middle"), "R4": (570,  410, "middle"),
    "R5": (610,  66, "middle"),
    "C1": (335, 410, "middle"), "C2": (385, 235, "middle"),
    "U":  (624, 294, "middle"),
}


# MFB (Friend/Rauch) op-amp cells. Two canvases, both on the 827x583 master:
#  - LP/QE  : all-pole Rauch (2LP-MFB, 3LP-MFB) and the +Q-enhancement divider
#             twins (2LP-MFB-QE, 3LP-MFB-QE). One table; R1/C1 auto-suppress on
#             the 2nd-order cells and R5/R6 (the p->out / p->gnd QE divider)
#             auto-suppress on the non-QE cells via _present.
#  - notch  : LP-notch Friend SAB (2LPn-MFB, 3LPn-MFB) carrying R7 (p->out) and
#             R8 (p->gnd). One table; R1/C1 auto-suppress on the 2nd-order cell.
# Artwork is named exactly by the canonical cell name, e.g. "2LPn-MFB.drawio.svg"
# (no +R7 / -C2s variants -- MFB caps snap cleanly without a parallel split).
_MFB_CELL_RE = re.compile(r"^([23])(LP|LPn)-MFB(-QE)?$")

ANCHORS_MFB_LP = {
    "R1": (120, 180, "middle"), "R2": (240, 180, "middle"),
    "R3": (360, 180, "middle"), "R4": (360,  85, "middle"),
    "R5": (645, 370, "middle"), "R6": (570, 410, "middle"),
    "C1": (220, 410, "middle"), "C2": (340, 410, "middle"),
    "C3": (635, 135, "middle"), "U":  (624, 294, "middle"),
}

ANCHORS_MFB_NOTCH = {
    "R1": (120, 180, "middle"), "R2": (240, 180, "middle"),
    "R3": (240, 325, "middle"), "R4": (330, 410, "middle"),
    "R5": (450, 410, "middle"), "R6": (610, 129, "middle"),
    "R7": (645, 370, "middle"), "R8": (570, 410, "middle"),
    "C1": (220, 410, "middle"), "C2": (390, 185, "middle"),
    "C3": (390,  90, "middle"), "U":  (624, 294, "middle"),
}

# ---------------------------------------------------------------------------
#  LOW-SENSITIVITY LP-notch branch -- SIX cells, ONE unified anchor table:
#      3LPn-MFB-LS        3LPn-MFB-LS+R7
#      2LPn-MFB-LS        2LPn-MFB-LS+R7
#      2LPn-MFB-LS+R1     2LPn-MFB-LS+R1+R7
#  Netlist (single canvas, optional parts):
#      C1 a-gnd (3rd only) | C2 a-b | C3 m-out
#      R1 in-a (3rd, +R1)  | R2 a-p | R3 b-m | R4 b-out | R5 b-gnd | R6 p-gnd
#      R7 p-out (+R7 only)
#  Every cell is a subset of that netlist, so ONE coordinate table serves all
#  six: build_annotations() emits a label only when the BOM row actually carries
#  the designator (_present()), so C1 vanishes on the 2nd-order cells, R1 on the
#  bare 2nd-order cells, and R7 on the non-+R7 cells -- automatically, with no
#  per-cell table. R8 and C4 never appear on this branch.
#
#  NOTE: the coordinates below are PLACEHOLDERS (they mirror the MFB-notch
#  layout) so the label machinery is wired and correct end-to-end. Replace the
#  numbers once the Diagram-Builder canvases exist; nothing else changes.
# ---------------------------------------------------------------------------
_MFB_LS_CELL_RE = re.compile(r"^([23])LPn-MFB-LS(\+R1)?(\+R7)?$")

ANCHORS_MFB_LP_NOTCH_LS = {
    "R1": (120, 183, "middle"),   # in -> a       (3rd order and +R1 cells)
    "R2": (240, 328, "middle"),   # a  -> p       (+) divider, upper leg
    "R3": (360, 183, "middle"),   # b  -> m
    "R4": (360, 88, "middle"),   # b  -> out
    "R5": (332, 410, "middle"),   # b  -> gnd
    "R6": (572, 410, "middle"),   # p  -> gnd     (+) divider, lower leg
    "R7": (610, 328, "middle"),   # p  -> out     (+R7 cells only)
    "C1": (218, 410, "middle"),   # a  -> gnd     (3rd order only)
    "C2": (265, 190, "middle"),   # a  -> b
    "C3": (635, 135, "middle"),   # m  -> out
    "U":  (624, 283, "middle"),
}


# HP-MFB (Friend/Rauch high-pass) op-amp cells — the R<->C-swapped twins of the
# LP-MFB canvases above, on the same 827x583 master. Two canvases:
#  - all-pole : 2HP-MFB, 3HP-MFB and the +Q-enhancement twins 2HP-MFB-QE,
#               3HP-MFB-QE. One table; R1/C1 auto-suppress on the 2nd-order
#               cells and R4/R5 (the p->gnd / p->out QE divider) auto-suppress on
#               the non-QE cells via _present. NOTE the HP all-pole carries C4
#               (b->out feedback cap) -- unlike LP-MFB, which has no C4.
#  - notch    : HP-notch 2HPn-MFB, 3HPn-MFB carrying R7 (p->out) and R8 (p->gnd).
#               One table; R1/C1 auto-suppress on the 2nd-order cell.
# Artwork is named exactly by the canonical cell name, e.g. "2HPn-MFB.drawio.svg".
_MFB_HP_CELL_RE = re.compile(r"^([23])(HP|HPn)-MFB(-QE)?$")

ANCHORS_MFB_HP_LP = {
    "R1": (210, 410, "middle"), "R2": (330, 410, "middle"),
    "R3": (610, 129, "middle"), "R4": (570, 410, "middle"),
    "R5": (645, 370, "middle"),
    "C1": (150, 185, "middle"), "C2": (270, 185, "middle"),
    "C3": (390, 185, "middle"), "C4": (390,  90, "middle"),
    "U":  (624, 294, "middle"),
}

ANCHORS_MFB_HP_NOTCH = {
    "R1": (210, 410, "middle"), "R2": (240, 180, "middle"),
    "R3": (240, 325, "middle"), "R4": (330, 410, "middle"),
    "R5": (240, 246, "middle"), "R6": (610, 129, "middle"),
    "R7": (645, 370, "middle"), "R8": (570, 410, "middle"),
    "C1": (150, 185, "middle"), "C2": (390, 185, "middle"),
    "C3": (390,  90, "middle"), "U":  (624, 294, "middle"),
}

# MFB2 HP-notch (2HPn-MFB2, 3HPn-MFB2) — the unity/gained HP-notch realization
# (HF gain = [R4/(R3+R4)]*[(C3+C4)/C3]). One table; R1, C1 AND R5 auto-suppress
# on the 2nd-order cell (R5, the a->out feedback, is electrically inert in the
# 2nd-order ideal and absent from its BOM). The 2nd-order "+R7" twin adds R7
# (p->out positive feedback) and has its OWN canvas "2HPn-MFB2+R7.drawio.svg";
# it reuses THIS anchor table (same layout plus the R7 leg), so R7's coordinate
# lives here too. Artwork: "{cell}.drawio.svg".
_MFB_HP2_CELL_RE = re.compile(r"^([23])HPn-MFB2(\+R7)?$")
ANCHORS_MFB_HP_NOTCH2 = {
    "R1": (210, 410, "middle"), "R2": (240, 180, "middle"),
    "R3": (240, 325, "middle"), "R4": (570, 410, "middle"),
    "R5": (240,  95, "middle"), "R6": (610, 129, "middle"),
    # R7 (p->out positive feedback, "+R7" twin only). PLACEHOLDER coordinates --
    # replace with the real Diagram-Builder anchor once the R7 leg is drawn on
    # the 2HPn-MFB2 canvas. Auto-suppressed on the plain cell.
    "R7": (610, 325, "middle"),
    "C1": (150, 185, "middle"), "C2": (390, 185, "middle"),
    "C3": (390,  90, "middle"), "C4": (335, 410, "middle"),
    "U":  (624, 285, "middle"),
}


# MFB (Rauch) BAND-PASS cells (item: BP-MFB). One canvas serves both: the plain
# "2BP-MFB" (C1,C2,R1,R2,R3,U) and the Q-enhanced "2BP-MFB-QE", which adds the
# R4 (p->gnd) / R5 (p->out) positive-feedback divider (auto-suppressed on the
# plain cell via _present). Both are INVERTING single-op-amp band-passes.
# NOTE: PLACEHOLDER coordinates (mirroring the MFB layouts) so the label
# machinery is wired and correct; swap in the real Diagram-Builder anchors when
# the "2BP-MFB.drawio.svg" canvas is finalized.
_MFB_BP_CELL_RE = re.compile(r"^2BP-MFB(-QE)?$")

ANCHORS_MFB_BP = {
    "R1": (240, 182, "middle"), "R2": (330, 410, "middle"),
    "R3": (610, 129, "middle"), "R4": (570, 410, "middle"),
    "R5": (645, 370, "middle"), 
    "C1": (390, 185, "middle"), "C2": (390, 90, "middle"),
    "U": (624, 294, "middle"),
}

# MFB (Rauch) 3rd-ORDER ASYMMETRIC BAND-PASS cells: a real pole absorbed into
# the band-pass biquad. Four cells, one canvas each, named exactly by the cell:
#   2BP1HP-MFB / -QE   input SERIES cap        Vin -C0- x -R1- a
#   2BP1LP-MFB / -QE   input series R + shunt  Vin -R0- x , x -C0- gnd , x -R1- a
# The biquad core is IDENTICAL to the 2nd-order 2BP-MFB canvas, so both maps
# inherit ANCHORS_MFB_BP and add only the input-network anchors the user
# measured on the new Diagram-Builder canvases. R4/R5 (QE divider) auto-suppress
# on the plain cells via _present().
#
# NOTE the inherited core anchors (R1..R5, C1, C2, U) still carry the
# PLACEHOLDER values from ANCHORS_MFB_BP. The C0/R0 positions below are real.
# If a label sits off its part on the new artwork, the core anchors are what
# need re-measuring -- C0/R0 are already correct.
_MFB_BP3_CELL_RE = re.compile(r"^2BP1(HP|LP)-MFB(-QE)?$")

# Solver symbol -> schematic designator for these cells. The cells keep their
# input parts inside the R1..R8 / C1..C4 symbol space that every BOM, scoring
# and Monte-Carlo enumeration already walks (adding real C0/R0 sympy symbols
# would mean extending ~8 hard-coded component lists, any one of which would
# silently drop the part); the parts are RE-LABELLED here instead, exactly as
# the AM family prints its 3rd-order prefilter R1/C4 as R0/C0.
#   C3 -> C0   input cap (series on BP1HP, shunt on BP1LP)
#   R6 -> R0   input series resistor (BP1LP only)
# topology_tab imports bp3_alias() so the BOM table, the per-part list and the
# schematic all read from this one definition.
_BP3_ALIAS_HP = {"C3": "C0"}
_BP3_ALIAS_LP = {"C3": "C0", "R6": "R0"}


def bp3_alias(topology):
    """Solver-symbol -> designator map for the 3rd-order MFB band-pass cells.
    {} for every other cell, so callers can apply it unconditionally."""
    m = _MFB_BP3_CELL_RE.match(str(topology or ""))
    if not m:
        return {}
    return dict(_BP3_ALIAS_HP if m.group(1) == "HP" else _BP3_ALIAS_LP)


ANCHORS_MFB_BP3_HP = {
    **ANCHORS_MFB_BP,
    "C0": (150, 185, "middle"),          # series input cap, Vin -C0- x
}

ANCHORS_MFB_BP3_LP = {
    **ANCHORS_MFB_BP,
    "R0": (120, 182, "middle"),          # series input resistor, Vin -R0- x
    "C0": (215, 410, "middle"),          # shunt input cap, x -C0- gnd
}


# MFB (Rauch/Friend) pure-NOTCH cells (item: NOTCH-MFB). One canvas serves both:
# the gained/UNITY "2N-MFB" (C1,C2,C3,R1,R2,R3,R4,R5), whose C3 (m->gnd) and R5
# (m->gnd) form the gain-set leg, and the attenuating-only "2N-MFB-atten"
# (C1,C2,R1,R2,R3,R4), whose absent C3/R5 auto-suppress via _present. Both are
# NON-inverting; the op-amp (+) sits on the R1/R4 input divider (which preserves
# the on-axis zero). NOTE: PLACEHOLDER coordinates (mirroring the MFB notch
# layout); swap in the real Diagram-Builder anchors when the "2N-MFB.drawio.svg"
# canvas is finalized.
_MFB_N_CELL_RE = re.compile(r"^2N-MFB(-atten)?$")

ANCHORS_MFB_N = {
    "R1": (240, 328, "middle"), "R2": (360, 182, "middle"),
    "R3": (360, 87, "middle"), "R4": (570, 410, "middle"),
    "R5": (490, 410, "middle"),
    "C1": (267, 185, "middle"), "C2": (636, 134, "middle"),
    "C3": (365, 410, "middle"), 
    "U": (624, 294, "middle"),
}


# =====================================================================
# Ackerberg-Mossberg 3-op-amp cells -- UNIVERSAL designator map.
#
# ONE coordinate system shared by EVERY AM cell. Absent parts auto-suppress via
# _present(), so a single map serves all cells; only the designators a given
# cell populates are drawn. _am_labels() re-maps solver symbols -> schematic
# designators:
#
#   biquad core (all cells)   R4 R5 R6 R7 R8  C2 C3           -> same names
#   p2 input resistor         R2  (LP out1 + all notch cells) -> R2
#   m1 input resistor         R1  (2BP-AM only)               -> R1
#   m1 input capacitor        C1  (HP, notch, 2BP-AM2)        -> C1
#     ...parallel twin (-C1s) C1a, C1b                        -> C1.1, C1.2
#                             (C1.1 shares C1's position; C1.2 is the 2nd leg)
#   3rd-order prefilter (LP)  series R (R1 | R3) + shunt C4   -> R0, C0
#   3rd-order prefilter (HP)  series C4 + shunt R (R1)        -> C0, R0
#   op-amps                   U1 U2 U3
#
# 3Lxx (3LP, 3LPn, 3LP-AM2) and 3Hxx (3HP, 3HPn) place R0/C0 at DIFFERENT spots,
# so those two designators live in per-family add-on tables merged on top of the
# universal base; everything else is identical across all cells. 2nd-order
# notch cells (2LPn / 2HPn / 2N) are the SAME circuit -> they share one canvas
# (see _AM_SHARE below).
#
# COORDINATES BELOW ARE PLACEHOLDERS -- replace with the real Diagram-Builder
# anchors (designator_tuner.html) once the AM .drawio.svg canvases are drawn.
# =====================================================================
_AM_CELL_RE = re.compile(
    r"^(?:([23])(?:LP|LPn|HP|HPn)-AM|2BP-AM|2N-AM|[23]LP-AM2|2BP-AM2)(?:-C1s)?$")

_ANCHORS_AM_BASE = {
    # --- biquad core (universal across all AM cells) ---
    "R4": (320, 88, "middle"), "R5": (420, 75, "middle"),
    "R6": (420, 248, "middle"), "R7": (595, 88, "middle"),
    "R8": (730, 143, "middle"),
    "C2": (345, 173, "middle"), "C3": (472, 173, "middle"),
    # --- input elements (universal) ---
    "R1":   (200, 223, "middle"),        # m1 input resistor (2BP-AM)
    "R2":   (200, 408, "middle"),        # p2 input resistor (LP out1 + notch cells)
    "C1":   (222, 310, "middle"),        # m1 input cap (HP / notch / 2BP-AM2)
    "C1.1": (222, 310, "middle"),        # == C1 (single) / first leg of the split
    "C1.2": (222, 370, "middle"),        # second parallel leg (-C1s twins)
    # --- op-amps ---
    "U1": (345, 320, "middle"), "U2": (550, 345, "middle"),
    "U3": (595, 235, "middle"), "U": (440, 520, "middle"),
}

# 3rd-order absorbed-pole prefilter designators R0 / C0, at DIFFERENT coordinates
# for the low-pass family vs the high-pass family (merged onto the base above).
_ANCHORS_AM_3L = {"R0": (80, 223, "middle"), "C0": (80, 300, "middle")}
_ANCHORS_AM_3H = {"R0": (85, 300, "middle"), "C0": (105, 228, "middle")}

# Back-compat alias (call sites that reference ANCHORS_AM directly).
ANCHORS_AM = _ANCHORS_AM_BASE

# 2nd-order notch cells share ONE canvas: 2LPn-AM / 2HPn-AM / 2N-AM are the
# identical circuit (C1 + R2 input; wz above/below/at w0 by component values
# only). 2LPn-AM (and its -C1s twin) is the drawn "basic cell"; the others reuse
# its artwork. 3rd-order notch cells are NOT shared (their LP vs HP prefilters
# differ).
_AM_SHARE = {
    "2HPn-AM":     "2LPn-AM",
    "2N-AM":       "2LPn-AM",
    "2HPn-AM-C1s": "2LPn-AM-C1s",
    "2N-AM-C1s":   "2LPn-AM-C1s",
}


def _am_anchor_table(topology):
    """Universal AM base merged with the 3rd-order prefilter add-on (LP or HP)."""
    t = str(topology or "")
    a = dict(_ANCHORS_AM_BASE)
    if t[:1] == "3":
        a.update(_ANCHORS_AM_3H if "HP" in t else _ANCHORS_AM_3L)
    return a


def _am_labels(row, stage, anchors):
    """Emit AM designator labels, mapping solver symbols -> schematic designators
    (see the universal-map header). Only populated designators are drawn."""
    t = str(row.get("topology") or "")
    o3 = t[:1] == "3"
    is_hp = "HP" in t
    is_lp2 = "LP-AM2" in t
    pre_R = "R3" if (is_lp2 and not is_hp) else "R1"      # 3rd-order prefilter resistor
    parts = []

    def emit(desig, value, cap):
        if _present(value):
            fn = _cap_text if cap else _res_text
            parts.append(_label(desig, f"{stage}{desig}", fn(value), anchors))

    # resistors R1..R8 ; the 3rd-order prefilter resistor prints as R0
    for i in range(1, 9):
        nm = f"R{i}"
        v = row.get(nm)
        if not _present(v):
            continue
        if o3 and nm == pre_R:
            emit("R0", v, cap=False)
        elif nm == "R3":
            continue                    # R3 is only ever the LP2 prefilter (-> R0)
        else:
            emit(nm, v, cap=False)

    # caps: C1 (parallel twin -> C1.1/C1.2), C2, C3 ; prefilter C4 prints as C0
    c1_split = _present(row.get("C1_parallel")) or (
        _present(row.get("C1a")) and _present(row.get("C1b")))
    if c1_split:
        emit("C1.1", row.get("C1a"), cap=True)
        emit("C1.2", row.get("C1b"), cap=True)
    else:
        emit("C1", row.get("C1"), cap=True)
    emit("C2", row.get("C2"), cap=True)
    emit("C3", row.get("C3"), cap=True)
    if o3:
        emit("C0", row.get("C4"), cap=True)
    return parts


ANCHORS_FO = {
    "1LP-ni": {                       # R1 in->p ; R2 p->gnd (atten) ; C1 p->gnd ; R3/R4 fb
        "R1": (240, 230, "middle"), "R2": (450, 410, "middle"),
        "C1": (335, 410, "middle"), "R3": (610, 66, "middle"),
        "R4": (555, 410, "start"),  "U": (624, 294, "middle"),
    },
    "1HP-ni": {                       # (R1+C1 series) in->p ; R2 p->gnd ; R3/R4 fb
        "R1": (240, 230, "middle"), "C1": (388, 230, "middle"),
        "R2": (450, 410, "middle"), "R3": (610, 66, "middle"),
        "R4": (555, 410, "start"),  "U": (624, 294, "middle"),
    },
    "1LP-inv": {                      # R1 in->m ; R2||C1 fb (m->out)
        "R1": (360, 66, "middle"),  "R2": (610, 66, "middle"),
        "C1": (633, 132, "middle"), "U": (624, 294, "middle"),
    },
    "1HP-inv": {                      # (R1+C1 series) in->m ; R2 fb (m->out)
        "R1": (360, 66, "middle"),  "C1": (488, 66, "middle"),
        "R2": (610, 66, "middle"),  "U": (624, 294, "middle"),
    },
}


def _anchor_table(topology):
    """Pick the designator-anchor table for a cell: a 1st-order skeleton table,
    the unified HP table, else the LP/LPn master ANCHORS."""
    m = _FO_CELL_RE.match(str(topology or ""))
    if m:
        fam, real, _gain = m.groups()
        return ANCHORS_FO[f"1{fam}-{real}"]
    if _HP_CELL_RE.match(str(topology or "")):
        return ANCHORS_HP
    if _NOTCH_CELL_RE.match(str(topology or "")):
        return ANCHORS_NOTCH
    if _BP_CELL_RE.match(str(topology or "")):
        return ANCHORS_BP
    # LS branch first: "-LS..." is not matched by the anchored _MFB_CELL_RE, but
    # keep the order explicit so a future relaxation of that regex stays safe.
    if _MFB_LS_CELL_RE.match(str(topology or "")):
        return ANCHORS_MFB_LP_NOTCH_LS
    m = _MFB_CELL_RE.match(str(topology or ""))
    if m:
        return ANCHORS_MFB_NOTCH if m.group(2) == "LPn" else ANCHORS_MFB_LP
    m = _MFB_HP2_CELL_RE.match(str(topology or ""))
    if m:
        return ANCHORS_MFB_HP_NOTCH2
    m = _MFB_HP_CELL_RE.match(str(topology or ""))
    if m:
        return ANCHORS_MFB_HP_NOTCH if m.group(2) == "HPn" else ANCHORS_MFB_HP_LP
    m = _MFB_BP3_CELL_RE.match(str(topology or ""))
    if m:
        return ANCHORS_MFB_BP3_HP if m.group(1) == "HP" else ANCHORS_MFB_BP3_LP
    if _MFB_BP_CELL_RE.match(str(topology or "")):
        return ANCHORS_MFB_BP
    if _MFB_N_CELL_RE.match(str(topology or "")):
        return ANCHORS_MFB_N
    if _AM_CELL_RE.match(str(topology or "")):
        return _am_anchor_table(topology)
    return ANCHORS


_ABSENT = (None, "", "OPEN", "OPEN  ", 0, 0.0)


def _present(v):
    return v not in _ABSENT


def _cap_text(uF):
    return f"{_fmt_cap(uF)}F"


def _res_text(MOhm):
    return f"{_fmt_res(MOhm, snapped=True)}\u03a9"


def _xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _label(symbol, desig, value, anchors=ANCHORS):
    """Two stacked <text> lines (designator over value) at anchors[symbol]."""
    if symbol not in anchors:
        return ""  # no position defined -> silently skip
    x, y, align = anchors[symbol]
    out = (
        f'<text x="{x}" y="{y}" text-anchor="{align}" '
        f'font-family="{FONT_FAMILY}" font-size="{DESIG_SIZE}" '
        f'font-weight="{DESIG_WEIGHT}" fill="{DESIG_COLOR}">{_xml_escape(desig)}</text>'
    )
    if value is not None:
        out += (
            f'<text x="{x}" y="{y + LINE_GAP}" text-anchor="{align}" '
            f'font-family="{FONT_FAMILY}" font-size="{VALUE_SIZE}" '
            f'font-weight="{VALUE_WEIGHT}" fill="{VALUE_COLOR}">{_xml_escape(value)}</text>'
        )
    return out


def build_annotations(stage, row, opamp_pn=None):
    """Return the inner SVG markup (no <g> wrapper) for one section's labels.

    `row` is a snapped/continuous BOM dict with unified keys
    (C1..C4, C2a/C2b/C2_parallel, R1..R7). Absent components are skipped.
    `opamp_pn` is the op-amp part-number string (e.g. "AD8505"); when given it
    prints under the "{stage}U1" designator. Pass None for designator only.
    """
    parts = []
    topo = row.get("topology")
    anchors = _anchor_table(topo)                  # 1st-order skeleton or LP master

    # Ackerberg-Mossberg cells use a dedicated solver-symbol -> designator map
    # (R0/C0 prefilter, C1.1/C1.2 split, U1/U2/U3). Handle and return early.
    if _AM_CELL_RE.match(str(topo or "")):
        parts = _am_labels(row, stage, anchors)
        parts.append(_label("U1", f"{stage}U1", opamp_pn, anchors))   # PN under U1
        if "U2" in anchors:
            parts.append(_label("U2", f"{stage}U2", None, anchors))
        if "U3" in anchors:
            parts.append(_label("U3", f"{stage}U3", None, anchors))
        return "".join(p for p in parts if p)

    # Solver-symbol -> designator aliases. Empty for every cell except the
    # 3rd-order MFB band-pass ones (C3->C0, R6->R0), so the loops below are
    # byte-identical elsewhere. The ANCHOR is looked up by DESIGNATOR, which is
    # what ANCHORS_MFB_BP3_* are keyed on.
    alias = bp3_alias(topo)

    # resistors R1..R8  (R8 = optional HP b->gnd attenuator on +R8 twins)
    for i in range(1, 9):
        name = f"R{i}"
        v = row.get(name)
        if _present(v):
            d = alias.get(name, name)
            parts.append(_label(d, f"{stage}{d}", _res_text(v), anchors))

    # capacitors, with split-C2 handling
    split = _present(row.get("C2_parallel")) or (
        _present(row.get("C2a")) and _present(row.get("C2b"))
    )
    for i in (1, 2, 3, 4):
        name = f"C{i}"
        if name == "C2" and split:
            parts.append(_label("C2.1", f"{stage}C2.1", _cap_text(row.get("C2a")), anchors))
            parts.append(_label("C2.2", f"{stage}C2.2", _cap_text(row.get("C2b")), anchors))
            continue
        v = row.get(name)
        if _present(v):
            d = alias.get(name, name)
            parts.append(_label(d, f"{stage}{d}", _cap_text(v), anchors))

    # op-amp (always present); opamp_pn -> part number / params under "{stage}U1"
    parts.append(_label("U", f"{stage}U1", opamp_pn, anchors))

    return "".join(p for p in parts if p)


def annotate(base_svg, stage, row, opamp_pn=None):
    """Inject the annotation layer into a base SVG string and return new SVG."""
    layer = f'\n<g id="annotations">{build_annotations(stage, row, opamp_pn)}</g>\n'
    idx = base_svg.rfind("</svg>")
    if idx == -1:
        raise ValueError("base_svg has no closing </svg> tag")
    return base_svg[:idx] + layer + base_svg[idx:]


# -------- topology -> SVG file resolver + loader -------------------------
_GAIN_ABBR = {"unity": "u", "gained": "g", "atten": "att"}
_CELL_RE = re.compile(r"^(\d+)(LPn|LP)-(unity|gained|atten)(\+R7)?$")


def svg_filename(topology, row=None):
    """Map a canonical cell name (tf_derivation.topo_name) to its SVG filename.

    "3LPn-gained+R7" -> "3LPn-g-R7.drawio.svg"
    "2LPn-atten"     -> "2LPn-att.drawio.svg"
    With a split-C2 BOM row, "-C2s" is appended (2nd-order notch cells only).
    1st-order cells:  "1HP-ni-gained" -> "1HP-ni-gained.drawio.svg" (3 ni variants),
    "1LP-inv-*" -> "1LP-inv.drawio.svg" (a single inverting schematic, gain by ratio).
    """
    mfo = _FO_CELL_RE.match(str(topology or ""))
    if mfo:
        fam, real, gain = mfo.groups()
        if real == "inv":
            return f"1{fam}-inv.drawio.svg"          # single schematic for all gains
        return f"1{fam}-ni-{gain}.drawio.svg"        # one per gain variant
    if _HP_CELL_RE.match(str(topology or "")):
        # HP artwork is named exactly by the canonical cell name (full gain word,
        # no +R7/-C2s variants): "3HPn-unity" -> "3HPn-unity.drawio.svg".
        return f"{topology}.drawio.svg"
    if _NOTCH_CELL_RE.match(str(topology or "")):
        # Notch artwork is named exactly by the cell name: "2N" -> "2N.drawio.svg",
        # "2N-atten" -> "2N-atten.drawio.svg" (the R6-less variant, same canvas).
        # No -C2s variant: the pure-notch caps snap cleanly without a split.
        return f"{topology}.drawio.svg"
    if _BP_CELL_RE.match(str(topology or "")):
        # Band-pass artwork is named exactly by the cell name: "2BP" ->
        # "2BP.drawio.svg", "2BP-atten" -> "2BP-atten.drawio.svg" (the unity-buffer
        # variant, same canvas; R4/R5 labels auto-suppress). No -C2s variant.
        return f"{topology}.drawio.svg"
    if _MFB_LS_CELL_RE.match(str(topology or "")):
        # LS artwork, one canvas per cell, "+" sanitised to "-" exactly as the
        # VCVS "+R7" twins do ("3LPn-gained+R7" -> "3LPn-g-R7.drawio.svg"):
        #     2LPn-MFB-LS        -> 2LPn-MFB-LS.drawio.svg
        #     2LPn-MFB-LS+R1     -> 2LPn-MFB-LS-R1.drawio.svg
        #     2LPn-MFB-LS+R1+R7  -> 2LPn-MFB-LS-R1-R7.drawio.svg
        #     3LPn-MFB-LS+R7     -> 3LPn-MFB-LS-R7.drawio.svg
        # All six share ANCHORS_MFB_LP_NOTCH_LS; absent designators auto-suppress.
        return f"{str(topology).replace('+', '-')}.drawio.svg"
    if _MFB_CELL_RE.match(str(topology or "")):
        # MFB artwork is named exactly by the cell name: "2LPn-MFB" ->
        # "2LPn-MFB.drawio.svg", "3LP-MFB-QE" -> "3LP-MFB-QE.drawio.svg".
        # One canvas per cell; R1/C1 (2nd order) and R5/R6 (non-QE) labels
        # auto-suppress. No +R7 / -C2s variants.
        return f"{topology}.drawio.svg"
    m = _MFB_HP2_CELL_RE.match(str(topology or ""))
    if m:
        # MFB2 HP-notch artwork named exactly by the cell name: "2HPn-MFB2" ->
        # "2HPn-MFB2.drawio.svg", "3HPn-MFB2" -> "3HPn-MFB2.drawio.svg", and the
        # 2nd-order "+R7" twin gets its OWN canvas "2HPn-MFB2+R7.drawio.svg" (it
        # adds the p->out R7 leg, drawn separately). R1/C1 and R5 (2nd order)
        # labels auto-suppress within each canvas.
        return f"{topology}.drawio.svg"
    if _MFB_HP_CELL_RE.match(str(topology or "")):
        # HP-MFB artwork is named exactly by the canonical cell name: "2HPn-MFB" ->
        # "2HPn-MFB.drawio.svg", "3HP-MFB-QE" -> "3HP-MFB-QE.drawio.svg".
        # One canvas per cell; R1/C1 (2nd order) and R4/R5 (non-QE divider)
        # labels auto-suppress. No +R7 / -C2s variants.
        return f"{topology}.drawio.svg"
    if _MFB_BP3_CELL_RE.match(str(topology or "")):
        # 3rd-order asymmetric BP-MFB artwork named exactly by the cell name:
        # "2BP1HP-MFB.drawio.svg", "2BP1HP-MFB-QE.drawio.svg",
        # "2BP1LP-MFB.drawio.svg", "2BP1LP-MFB-QE.drawio.svg".
        # One canvas per cell (the QE twins draw the R4/R5 divider), so nothing
        # auto-suppresses between them.
        return f"{topology}.drawio.svg"
    if _MFB_BP_CELL_RE.match(str(topology or "")):
        # BP-MFB artwork named exactly by the cell name: "2BP-MFB" ->
        # "2BP-MFB.drawio.svg", "2BP-MFB-QE" -> "2BP-MFB-QE.drawio.svg" (same
        # canvas family; R4/R5 QE-divider labels auto-suppress on the plain cell).
        return f"{topology}.drawio.svg"
    if _MFB_N_CELL_RE.match(str(topology or "")):
        # NOTCH-MFB artwork named exactly by the cell name: "2N-MFB" ->
        # "2N-MFB.drawio.svg", "2N-MFB-atten" -> "2N-MFB-atten.drawio.svg" (same
        # canvas; the gained cell's C3/R5 m->gnd labels auto-suppress on -atten).
        return f"{topology}.drawio.svg"
    if _AM_CELL_RE.match(str(topology or "")):
        # Each AM cell has its own canvas named exactly by the cell name, EXCEPT
        # the 2nd-order notch trio: 2HPn-AM and 2N-AM are the identical circuit as
        # 2LPn-AM (values-only difference), so they reuse 2LPn-AM's artwork;
        # likewise their -C1s twins reuse 2LPn-AM-C1s. Unused R/C labels
        # auto-suppress. C1 split is drawn via C1.1/C1.2 on the same canvas, so no
        # separate "-C1s" file beyond what the cell name already carries.
        canon = _AM_SHARE.get(str(topology), str(topology))
        return f"{canon}.drawio.svg"
    m = _CELL_RE.match(str(topology or ""))
    if not m:
        raise ValueError(f"unrecognized topology cell name: {topology!r}")
    order, lp, gain, r7 = m.groups()
    name = f"{order}{lp}-{_GAIN_ABBR[gain]}"
    if r7:                        # "+R7" twin (gained+notch) -> "-R7"
        name += "-R7"
    if row is not None:           # split parallel C2 -> the "-C2s" artwork
        split = _present(row.get("C2_parallel")) or (
            _present(row.get("C2a")) and _present(row.get("C2b")))
        if split:
            name += "-C2s"
    return name + ".drawio.svg"


def load_base_svg(topology, row=None, svg_dir=None):
    """Read the base SVG for a topology cell from SVG_DIR (or svg_dir)."""
    path = os.path.join(svg_dir or SVG_DIR, svg_filename(topology, row))
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8") as f:
        return f.read()


def render_svg(topology, stage, row, opamp_pn=None, svg_dir=None):
    """Load the topology's base SVG and overlay this section's labels."""
    base = load_base_svg(topology, row, svg_dir)
    return annotate(base, stage, row, opamp_pn)


# -------- optional helpers for the Streamlit side ------------------------
def schematic_iframe_html(annotated_svg, max_width=900):
    """Wrap an annotated SVG for st.components.v1.html (deterministic render)."""
    return (
        f'<div style="width:100%;max-width:{max_width}px;margin:0 auto;">'
        f'<style>svg{{width:100%;height:auto;display:block;}}</style>'
        f'{annotated_svg[annotated_svg.find("<svg"):]}'
        f'</div>'
    )


# =====================================================================
#  CSS SANITIZER  (drawio "light-dark()" exports)
# =====================================================================
# Recent drawio builds emit dark-mode-aware colours:
#
#   <rect style="fill: var(--ge-adaptive-bg, #ffffff);"/>
#   <path stroke="#000000"
#         style="stroke: light-dark(rgb(0, 0, 0), rgb(255, 255, 255));"/>
#
# A browser resolves these, so the Topology tab's iframe looks right. Neither
# offline converter can: cairosvg hex-parses the token and dies with
# `invalid literal for int() with base 16: 'ig'`, svglib logs "Can't handle
# color" and leaves the shape with strokeColor=None AND fillColor=None, i.e.
# invisible. Because the style attribute OVERRIDES the perfectly good
# presentation attribute next to it, every wire and symbol vanishes while
# plain-coloured text still draws -- a schematic of labels floating on white.
#
# Resolving the two functions to their light-scheme value restores ordinary
# CSS that both converters understand. Applied only on the rasterization path:
# the SVG offered for download keeps its dark-mode support intact.

_CSS_FN_RE = re.compile(r"\b(light-dark|var)\s*\(", re.I)


def _split_css_args(text):
    """Split a CSS argument list at TOP-LEVEL commas, so the inner commas of
    a nested rgb(0, 0, 0) do not split the outer light-dark()."""
    args, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    args.append("".join(cur).strip())
    return args


def sanitize_svg(svg_text):
    """Resolve light-dark(a, b) -> a and var(--name, fallback) -> fallback.

    Outermost-first with explicit paren matching, so
    `light-dark(#ffffff, var(--ge-dark-color, #121212))` collapses to
    `#ffffff` in one step. A no-op for SVGs that use neither function.
    """
    out, guard = svg_text, 0
    while guard < 20000:
        m = _CSS_FN_RE.search(out)
        if not m:
            break
        open_i = m.end() - 1
        depth, close_i = 0, -1
        for j in range(open_i, len(out)):
            if out[j] == "(":
                depth += 1
            elif out[j] == ")":
                depth -= 1
                if depth == 0:
                    close_i = j
                    break
        if close_i < 0:
            break                               # unbalanced: leave it alone
        args = _split_css_args(out[open_i + 1:close_i])
        if m.group(1).lower() == "light-dark":
            repl = args[0] if args else "#000000"
        else:                                   # var(--name, fallback)
            repl = args[1] if len(args) > 1 else "none"
        out = out[:m.start()] + repl + out[close_i + 1:]
        guard += 1
    return out


def svg_to_png(annotated_svg, scale=2):
    """Rasterize an annotated SVG to PNG bytes. Returns None if no rasterizer
    is available -- callers should hide the PNG button in that case. Tries
    cairosvg first (best fidelity) and falls back to nothing if it's missing,
    so the SVG download path remains available regardless of the environment."""
    try:
        import cairosvg                                  # type: ignore
    except Exception:
        return None
    try:
        svg_text = annotated_svg[annotated_svg.find("<svg"):]
        svg_text = sanitize_svg(svg_text)       # drawio light-dark()/var()
        return cairosvg.svg2png(bytestring=svg_text.encode("utf-8"), scale=float(scale))
    except Exception:
        return None


def download_buttons(streamlit_mod, annotated_svg, topology, stage, key_prefix=""):
    """Render compact download button(s) for the annotated schematic. Always
    offers SVG; offers PNG too when a rasterizer is available. Lays the buttons
    out side-by-side. `streamlit_mod` is the imported `streamlit` module --
    passed in so this file stays import-light (no streamlit dependency at
    import time, mirroring hw_plots)."""
    st = streamlit_mod
    base = svg_filename(topology).replace(".drawio.svg", "")
    fname_svg = f"sec{stage}_{base}.svg"
    png_bytes = svg_to_png(annotated_svg)
    cols = st.columns([1, 1, 8]) if png_bytes else st.columns([1, 9])
    with cols[0]:
        st.download_button("⬇ SVG",
                           data=annotated_svg.encode("utf-8"),
                           file_name=fname_svg, mime="image/svg+xml",
                           key=f"{key_prefix}_svg_{stage}",
                           help="Download annotated schematic as SVG (vector)",
                           use_container_width=True)
    if png_bytes:
        with cols[1]:
            st.download_button("⬇ PNG", data=png_bytes,
                               file_name=f"sec{stage}_{base}.png",
                               mime="image/png",
                               key=f"{key_prefix}_png_{stage}",
                               help="Download annotated schematic as PNG (raster)",
                               use_container_width=True)


if __name__ == "__main__":
    # tiny self-test (no external files needed)
    demo_svg = '<svg viewBox="0 0 827 583" xmlns="http://www.w3.org/2000/svg"></svg>'
    demo_row = {"R1": 0.012, "R2": 0.024, "R3": 0.024, "R5": 0.010,
                "R6": 0.020, "C3": 1e-3, "C4": 1e-3, "C2_parallel": True,
                "C2a": 1e-3, "C2b": 1.2e-3, "R4": None, "R7": None, "C1": None}
    out = annotate(demo_svg, 2, demo_row)
    print("self-test labels present:",
          [t for t in ["2R1","2R4","2R7","2C1","2C2.1","2C2.2","U2"] if t in out])
