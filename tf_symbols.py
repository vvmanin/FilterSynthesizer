# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  tf_symbols.py
#  Shared sympy symbols for the family-modular cell library.
#
#  This is the bottom of the import graph: the generic engine
#  (tf_derivation_v2) and every per-family cell module (cells_lp,
#  cells_hp, cells_bp, ...) import their symbols from here, so the same
#  Symbol objects are used everywhere (sympy identity matters for subs,
#  lambdify and cache srepr round-trips). tf_derivation_v2 re-exports
#  every name below, so existing `from tf_derivation_v2 import p1, w0,
#  ...` call sites keep working unchanged.
#
#  Node-voltage symbols cover BOTH families:
#    LP op-amp (+) input  -> Vc
#    HP op-amp (+) input  -> Vp
#  Both are internal solve variables only; they are eliminated by the
#  nodal LUsolve and never appear in a cached transfer function (which
#  is always expressed purely in R/C symbols).
# =====================================================================

import sympy as sp

# Laplace variable
s = sp.symbols("s")

# Node voltages (Va,Vb internal; Vc = LP (+) node, Vp = HP (+) node;
# Vm = op-amp (-) node, kept separate in the non-ideal model; V2 = output)
Va, Vb, Vc, Vp, Vm, V2 = sp.symbols("Va Vb Vc Vp Vm V2")

# Passive components (resistors in MOhm, caps in uF in the numeric layer;
# Ro is the op-amp output resistance, same MOhm base). R8 is the optional HP
# feedback attenuator (node b -> gnd), the extra DOF that lets the notch
# feedback resistor R5 settle near the floor on the gained/3rd-order cells.
R1, R2, R3, R4, R5, R6, R7, R8, Ro = sp.symbols("R1 R2 R3 R4 R5 R6 R7 R8 Ro")
C1, C2, C3, C4 = sp.symbols("C1 C2 C3 C4")

# Op-amp non-idealities
A_ol, GBWP_hz = sp.symbols("A_ol GBWP_hz", positive=True)

# Design targets (rad/s + dimensionless): real pole, resonance, zero,
# quality factor, leading-coefficient gain
p1, w0, wz, Q, K = sp.symbols("p1 w0 wz Q K")

__all__ = [
    "s",
    "Va", "Vb", "Vc", "Vp", "Vm", "V2",
    "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "Ro",
    "C1", "C2", "C3", "C4",
    "A_ol", "GBWP_hz",
    "p1", "w0", "wz", "Q", "K",
]
