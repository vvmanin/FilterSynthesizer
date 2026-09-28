# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  spice_cells.py  [Tier D — FS-008 netlist IR, no Streamlit]
#
#  The machine-readable netlist of every cell. The cell modules (Tier B) hold
#  hand-written KCL only; this module states the same circuits as two-terminal
#  parts on named nodes, so the LTspice writer (spice_export), the numeric
#  check (dev/fs008/check_spice_export.py) and later the .asc drawing can all
#  read connectivity from ONE place.
#
#  Each family table below is READ OFF its module's NON-IDEAL KCL
#  (build_nonideal / am_eqs / _build_case), not off the ideal path, because the
#  non-ideal model keeps every node (the ideal path merges V- into V+). Every
#  entry of a family's superset is listed for every variant, with its state:
#     True     part present
#     "open"   part absent, its two nodes stay separate    (conductance 0)
#     "short"  part absent, its two nodes are one net      (e.g. a = in on
#              2nd-order cells, R5 = wire on a unity follower)
#  The open/short marks are what the Phase-3 template gating reads. Their
#  correctness is not taken on trust: the dev check compares mna_ac() of every
#  variant against the cell's own non-ideal transfer function.
#
#  Units: rows carry R in MOhm and C in uF (tf_symbols); the IR carries SI
#  (ohm, farad). Op-amp parameters: A_ol (V/V), GBWP_hz (Hz), Ro_ohm (ohm).
#
#  Node "0" is ground. A section's input node is "in", its output node "out"
#  (the tapped op-amp output). Op-amp model = the tool's: inputs draw no
#  current, output = A(s)*(V+ - V-) behind Ro, A(s) = A_ol*wc/(wc + s*A_ol),
#  wc = 2*pi*GBWP_hz.
# =====================================================================

import re

import numpy as np

import cells_first_order as FO
import tf_derivation_v2 as TF

GND = "0"

# AM kind sets (mirror cells_am_core; kept local so this module never needs
# the AM core's private names)
_AM_C1 = ("HP", "HPn", "LPn", "N", "BP2")      # C1  a -> m1
_AM_R2 = ("LP", "HPn", "LPn", "N")             # R2  a -> p2
_AM_R1IN = ("LP2", "BP")                       # R1  a -> m1
_AM_TAP2 = ("LP2", "BP2")                      # section output = out2

_BP3_RE = re.compile(r"^2BP1(HP|LP)-MFB(-QE)?$")


def _on(flag, absent="open"):
    """State of a gated part: present when `flag`, else `absent`."""
    return True if flag else absent


# =====================================================================
#  Family tables: topo -> (entries, opamps, out_node, template_id)
#  entry  = (key, kind, n1, n2, state)
#  opamp  = (slot, inp, inn, out)
# =====================================================================
def _sk_lp(t):
    o3, notch, gain = t["order"] == 3, t["notch"], t["gain"]
    unity_follower = gain == "unity" and not notch             # _gates shorted_r5
    e = [("R1", "R", "in", "a", _on(o3, "short")),             # 2nd order: a = in
         ("C1", "C", "a", GND, _on(o3)),
         ("R2", "R", "a", "b", True),
         ("R3", "R", "b", "c", True),
         ("C2", "C", "a", "c", _on(notch)),
         ("C3", "C", "c", GND, True),
         ("C4", "C", "b", "out", True),
         ("R4", "R", "a", "m", _on(notch)),
         ("R5", "R", "m", "out", "short" if unity_follower else True),
         ("R6", "R", "m", GND, _on(gain == "gained")),
         ("R7", "R", "b", GND, _on(t["has_R7"]))]
    return e, [("U1", "c", "m", "out")], "out", "SK_LP"


def _sk_hp(t):
    o3, notch, gain = t["order"] == 3, t["notch"], t["gain"]
    follower = gain in ("unity", "atten") and not notch
    e = [("C1", "C", "in", "a", _on(o3, "short")),
         ("R1", "R", "a", GND, _on(o3)),
         ("C2", "C", "a", "b", True),
         ("C3", "C", "b", "p", True),
         ("C4", "C", "b", GND, _on(gain == "atten")),
         ("R2", "R", "a", "p", _on(notch)),
         ("R3", "R", "p", GND, True),
         ("R4", "R", "a", "m", _on(notch)),
         ("R5", "R", "m", "out", "short" if follower else True),
         ("R6", "R", "b", "out", True),
         ("R7", "R", "m", GND, _on(gain == "gained")),
         ("R8", "R", "b", GND, _on(t.get("has_R8")))]
    return e, [("U1", "p", "m", "out")], "out", "SK_HP"


def _sk_notch(t):
    e = [("C1", "C", "a", GND, True),
         ("C2", "C", "a", "p", True),
         ("R1", "R", "in", "a", True),
         ("R2", "R", "a", "out", True),
         ("R3", "R", "p", GND, True),
         ("R4", "R", "in", "m", True),
         ("R5", "R", "out", "m", True),
         ("R6", "R", "m", GND, _on(t.get("has_R6")))]
    return e, [("U1", "p", "m", "out")], "out", "SK_N"


def _sk_bp(t):
    gained = t["gain"] == "gained"
    e = [("C1", "C", "a", GND, True),
         ("C2", "C", "a", "p", True),
         ("R1", "R", "in", "a", True),
         ("R2", "R", "a", "out", True),
         ("R3", "R", "p", GND, True),
         ("R4", "R", "m", GND, _on(gained)),
         ("R5", "R", "out", "m", _on(gained, "short"))]
    return e, [("U1", "p", "m", "out")], "out", "SK_BP"


def _mfb_lp(t):
    o3, notch = t["order"] == 3, t["notch"]
    if notch and t.get("ls"):
        node_a = o3 or bool(t.get("r1"))
        e = [("R1", "R", "in", "a", _on(node_a, "short")),
             ("C1", "C", "a", GND, _on(o3)),
             ("C2", "C", "a", "b", True),
             ("C3", "C", "m", "out", True),
             ("R2", "R", "a", "p", True),
             ("R3", "R", "b", "m", True),
             ("R4", "R", "b", "out", True),
             ("R5", "R", "b", GND, True),
             ("R6", "R", "p", GND, True),
             ("R7", "R", "p", "out", _on(t.get("r7")))]
        return e, [("U1", "p", "m", "out")], "out", "MFB_LPn_LS"
    if notch:                                                  # Friend SAB
        e = [("R1", "R", "in", "a", _on(o3, "short")),
             ("C1", "C", "a", GND, _on(o3)),
             ("C2", "C", "b", "m", True),
             ("C3", "C", "b", "out", True),
             ("R2", "R", "a", "b", True),
             ("R3", "R", "a", "p", True),
             ("R4", "R", "b", GND, True),
             ("R5", "R", "m", GND, True),
             ("R6", "R", "m", "out", True),
             ("R7", "R", "p", "out", True),
             ("R8", "R", "p", GND, True)]
        return e, [("U1", "p", "m", "out")], "out", "MFB_LPn"
    qe = bool(t["qe"])
    e = [("R1", "R", "in", "a", _on(o3, "short")),
         ("C1", "C", "a", GND, _on(o3)),
         ("C2", "C", "b", GND, True),
         ("C3", "C", "m", "out", True),
         ("R2", "R", "a", "b", True),
         ("R3", "R", "b", "m", True),
         ("R4", "R", "b", "out", True),
         ("R5", "R", "p", "out", _on(qe)),
         ("R6", "R", "p", GND, _on(qe))]
    return e, [("U1", "p" if qe else GND, "m", "out")], "out", "MFB_LP"


def _mfb_hp(t):
    o3, notch = t["order"] == 3, t["notch"]
    if notch and t.get("v2"):                                  # MFB2 HP-notch
        e = [("C1", "C", "in", "a", _on(o3, "short")),
             ("R1", "R", "a", GND, _on(o3)),
             ("C2", "C", "b", "m", True),
             ("C3", "C", "b", "out", True),
             ("C4", "C", "b", GND, True),
             ("R2", "R", "a", "b", True),
             ("R3", "R", "a", "p", True),
             ("R4", "R", "p", GND, True),
             ("R5", "R", "a", "out", _on(o3)),                 # 3rd order only
             ("R6", "R", "m", "out", True),
             ("R7", "R", "p", "out", _on(t.get("r7")))]
        return e, [("U1", "p", "m", "out")], "out", "MFB_HPn2"
    if notch:
        e = [("C1", "C", "in", "a", _on(o3, "short")),
             ("R1", "R", "a", GND, _on(o3)),
             ("C2", "C", "b", "m", True),
             ("C3", "C", "b", "out", True),
             ("R2", "R", "a", "b", True),
             ("R3", "R", "a", "p", True),
             ("R4", "R", "b", GND, True),
             ("R5", "R", "a", "m", True),
             ("R6", "R", "m", "out", True),
             ("R7", "R", "p", "out", True),
             ("R8", "R", "p", GND, True)]
        return e, [("U1", "p", "m", "out")], "out", "MFB_HPn"
    qe = bool(t["qe"])
    e = [("C1", "C", "in", "a", _on(o3, "short")),
         ("R1", "R", "a", GND, _on(o3)),
         ("C2", "C", "a", "b", True),
         ("C3", "C", "b", "m", True),
         ("C4", "C", "b", "out", True),
         ("R2", "R", "b", GND, True),
         ("R3", "R", "m", "out", True),
         ("R4", "R", "p", GND, _on(qe)),
         ("R5", "R", "p", "out", _on(qe))]
    return e, [("U1", "p" if qe else GND, "m", "out")], "out", "MFB_HP"


def _mfb_bp(t):
    ab, qe = t.get("absorb"), bool(t.get("qe"))
    if ab == "lp":                     # in -R6- x, x -C3- gnd
        e = [("R6", "R", "in", "x", True),
             ("C3", "C", "x", GND, True)]
    else:                              # in -C3- x (hp); 2nd order: x = in
        e = [("C3", "C", "in", "x", _on(ab == "hp", "short"))]
    e += [("R1", "R", "x", "a", True),
          ("R2", "R", "a", GND, True),
          ("C1", "C", "a", "m", True),
          ("C2", "C", "a", "out", True),
          ("R3", "R", "m", "out", True),
          ("R4", "R", "p", GND, _on(qe)),
          ("R5", "R", "p", "out", _on(qe))]
    tid = {"hp": "MFB_BP1HP", "lp": "MFB_BP1LP"}.get(ab, "MFB_BP")
    return e, [("U1", "p" if qe else GND, "m", "out")], "out", tid


def _mfb_notch(t):
    g = bool(t.get("gained"))
    e = [("C1", "C", "in", "a", True),
         ("C2", "C", "m", "out", True),
         ("C3", "C", "m", GND, _on(g)),
         ("R1", "R", "in", "p", True),
         ("R2", "R", "a", "m", True),
         ("R3", "R", "a", "out", True),
         ("R4", "R", "p", GND, True),
         ("R5", "R", "m", GND, _on(g))]
    return e, [("U1", "p", "m", "out")], "out", "MFB_N"


def _am(t):
    kind, o3 = t["kind"], t["order"] == 3
    e = []
    if kind in _AM_R1IN:
        e.append(("R1", "R", "a", "m1", True))
    # 3rd-order absorbed-pole prefilter (cells_am_core.am_eqs); 2nd order: a = in
    if kind in ("HP", "HPn"):          # C4 series in->a, R1 shunt a->gnd
        pre = [("C4", "C", "in", "a", _on(o3, "short")),
               ("R1", "R", "a", GND, _on(o3))]
    else:                              # series R (R3 on LP2, else R1), C4 shunt
        pre = [("R3" if kind == "LP2" else "R1", "R", "in", "a", _on(o3, "short")),
               ("C4", "C", "a", GND, _on(o3))]
    used = {x[0] for x in e}
    for p in pre:                      # 2BP-AM: R1 is its biquad input, so its
        if p[0] in used:               # prefilter slot is only the a = in wire
            if p[4] == "short":
                e.append((None, "W", p[2], p[3], "short"))
        else:
            e.append(p)
    e += [("C1", "C", "a", "m1", _on(kind in _AM_C1)),
          ("R2", "R", "a", "p2", _on(kind in _AM_R2)),
          ("C2", "C", "m1", "out1", True),
          ("R4", "R", "m1", "out1", True),
          ("R5", "R", "m1", "out2", True),
          ("R6", "R", "out1", "p2", True),
          ("C3", "C", "p2", "out3", True),
          ("R7", "R", "out3", "m3", True),
          ("R8", "R", "out2", "m3", True)]
    ops = [("U1", GND, "m1", "out1"), ("U2", "p2", GND, "out2"),
           ("U3", GND, "m3", "out3")]
    return e, ops, ("out2" if kind in _AM_TAP2 else "out1"), f"AM_{kind}"


def _first_order(t):
    fam, real, gain = t["family"], t["realization"], t["gain"]
    if real == "inv":
        if fam == "LP":                # R1 in->m ; R2 || C1 m->out
            e = [("R1", "R", "in", "m", True),
                 ("R2", "R", "m", "out", True),
                 ("C1", "C", "m", "out", True)]
        else:                          # (R1 series C1) in->m ; R2 m->out
            e = [("R1", "R", "in", "x", True),
                 ("C1", "C", "x", "m", True),
                 ("R2", "R", "m", "out", True)]
        return e, [("U1", GND, "m", "out")], "out", f"FO_{fam}_inv"
    if fam == "LP":                    # R1 in->p ; C1 (+R2 atten) p->gnd
        e = [("R1", "R", "in", "p", True),
             ("C1", "C", "p", GND, True),
             ("R2", "R", "p", GND, _on(gain == "atten"))]
    else:                              # (R1 series C1, atten) in->p ; R2 p->gnd
        e = [("R1", "R", "in", "x", _on(gain == "atten", "short")),
             ("C1", "C", "x", "p", True),
             ("R2", "R", "p", GND, True)]
    gained = gain == "gained"          # R3 m->out, R4 m->gnd ; else R3 = wire
    e += [("R3", "R", "m", "out", _on(gained, "short")),
          ("R4", "R", "m", GND, _on(gained))]
    return e, [("U1", "p", "m", "out")], "out", f"FO_{fam}_ni"


_FAMILY_TABLE = {
    "LP": _sk_lp, "HP": _sk_hp, "NOTCH": _sk_notch, "BP": _sk_bp,
    "LP-MFB": _mfb_lp, "HP-MFB": _mfb_hp, "BP-MFB": _mfb_bp,
    "NOTCH-MFB": _mfb_notch,
    "LP-AM": _am, "HP-AM": _am, "BP-AM": _am, "NOTCH-AM": _am,
}


def topo_of(topology):
    """(topo dict, is_first_order) for a cell name; KeyError if unknown."""
    fo = FO.parse_name(topology)
    if fo is not None:
        return fo, True
    return TF.topo_for_name(str(topology or "")), False


def superset(topology):
    """Raw table for a cell: {entries, opamps, out, template}. Entries keep
    their open/short state (the gating a drawing template needs)."""
    topo, fo = topo_of(topology)
    fn = _first_order if fo else _FAMILY_TABLE.get(topo.get("family"))
    if fn is None:
        raise KeyError(f"no netlist table for family {topo.get('family')!r} "
                       f"(cell {topology!r}) -- add one to spice_cells.py")
    entries, opamps, out, tid = fn(topo)
    return {"entries": entries, "opamps": opamps, "out": out, "template": tid}


def all_cell_names():
    """Every cell the tool can realize: the registry cells + the 12 first-order."""
    return ([TF.topo_name(t) for t in TF.all_cells()]
            + [FO.topo_name(t) for t in FO.all_cells()])


# =====================================================================
#  Designators (maintainer decision: R201 = section 2, R1)
# =====================================================================
def display_alias(topology):
    """Solver key -> schematic designator for the cells whose drawing renames a
    part. Mirrors schematic_svg._am_labels / topology_tab._am_row_designators
    (AM 3rd-order prefilter -> R0 / C0) and schematic_svg.bp3_alias (3rd-order
    MFB band-pass input network -> C0 / R0). {} for every other cell."""
    t = str(topology or "")
    m = _BP3_RE.match(t)
    if m:
        return {"C3": "C0"} if m.group(1) == "HP" else {"C3": "C0", "R6": "R0"}
    if "-AM" in t and t[:1] == "3":
        pre_r = "R3" if ("LP-AM2" in t and "HP" not in t) else "R1"
        return {pre_r: "R0", "C4": "C0"}
    return {}


def designator(stage, key, alias=None):
    """'R1' in section 2 -> 'R201'; split cap 'C2a' -> 'C202A'; 'U1' -> 'U201';
    aliased 'R0' -> 'R200'."""
    base, split = (key[:-1], key[-1].upper()) if key[-1] in "ab" else (key, "")
    base = (alias or {}).get(base, base)
    return f"{base[0]}{stage}{int(base[1:]):02d}{split}"


# =====================================================================
#  Section IR from a snapped BOM row
# =====================================================================
def _positive(v):
    try:
        return v is not None and float(v) > 0.0
    except (TypeError, ValueError):
        return False


def _union_find(nodes, shorts):
    parent = {n: n for n in nodes}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    fixed = ("in", GND)                  # never renamed away (ports / ground)
    for a, b in shorts:
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        if ra in fixed and rb in fixed:
            raise ValueError(f"short joins {ra!r} and {rb!r}")
        if rb in fixed or (rb == "out" and ra not in fixed):
            ra, rb = rb, ra
        parent[rb] = ra
    return find


def opamp_params(eval_opamp):
    """Tool op-amp dict {A_ol, GBWP_hz, Ro (MOhm)} -> IR params (Ro in ohm)."""
    return {"A_ol": float(eval_opamp["A_ol"]),
            "GBWP_hz": float(eval_opamp["GBWP_hz"]),
            "Ro_ohm": float(eval_opamp["Ro"]) * 1e6}


def section_ir(row, opamp=None):
    """Netlist IR of one snapped section.

    row   : Solution dict (topology + R in MOhm / C in uF, split caps as
            C1a/C1b or C2a/C2b, AM R8 = the matched R7 twin).
    opamp : IR op-amp params {A_ol, GBWP_hz, Ro_ohm} for every slot, or None.

    Returns {topology, template, nodes, in, out, parts, opamps, warnings}.
    A present part without a positive value in the row is an error, never a
    silent open."""
    topology = row.get("topology")
    sup = superset(topology)
    shorts = [(n1, n2) for (_, _, n1, n2, st) in sup["entries"] if st == "short"]
    nodes = {"in", GND, sup["out"]}
    for (_, _, n1, n2, _) in sup["entries"]:
        nodes |= {n1, n2}
    for (_, a, b, c) in sup["opamps"]:
        nodes |= {a, b, c}
    find = _union_find(sorted(nodes), shorts)

    parts, warnings = [], []
    for key, kind, n1, n2, st in sup["entries"]:
        if st is not True:
            continue
        a, b = find(n1), find(n2)
        scale = 1e6 if kind == "R" else 1e-6            # MOhm -> ohm, uF -> F
        va, vb = row.get(key + "a"), row.get(key + "b")
        if (kind == "C" and _positive(va) and _positive(vb)
                and row.get(key + "_parallel") is not False):
            tot = row.get(key)
            if _positive(tot) and abs(float(va) + float(vb) - float(tot)) > 1e-9 * float(tot):
                warnings.append(f"{key}a + {key}b != {key} in the row; the split "
                                f"parts are exported")
            parts.append({"key": key + "a", "kind": kind, "n1": a, "n2": b,
                          "value": float(va) * scale})
            parts.append({"key": key + "b", "kind": kind, "n1": a, "n2": b,
                          "value": float(vb) * scale})
            continue
        v = row.get(key)
        if key == "R8" and not _positive(v) and "-AM" in str(topology):
            v = row.get("R7")                            # AM matched pair R8 = R7
        if not _positive(v):
            raise ValueError(f"{topology}: part {key} is present in the circuit "
                             f"but the BOM row has no positive value ({v!r})")
        parts.append({"key": key, "kind": kind, "n1": a, "n2": b,
                      "value": float(v) * scale})

    opamps = []
    for slot, inp, inn, out in sup["opamps"]:
        d = {"slot": slot, "inp": find(inp), "inn": find(inn), "out": find(out)}
        if opamp is not None:
            d.update(opamp)
        opamps.append(d)

    used = {p["n1"] for p in parts} | {p["n2"] for p in parts}
    for o in opamps:
        used |= {o["inp"], o["inn"], o["out"]}
    return {"topology": topology, "template": sup["template"],
            "nodes": sorted(used | {"in", GND}),
            "in": "in", "out": find(sup["out"]),
            "parts": parts, "opamps": opamps, "warnings": warnings}


# =====================================================================
#  Cascade (sections in series, real inter-stage loading)
# =====================================================================
def cascade_ir(sections, buffers=False, in_name="IN", out_name="OUT"):
    """Join section IRs in stage order into one netlist.

    sections : list of (stage_number, section_ir, alias_map)
    buffers  : True inserts an ideal unity buffer (VCVS, kind 'E') between
               sections -- the tool's unloaded model, used by the dev check.

    Nets: the first input is IN, section k's output is S{k} (the last is OUT),
    internal nodes are S{k}_<name>. Parts get designators (R201, C202A, U201)."""
    parts, opamps, bufs = [], [], []
    prev = in_name
    for i, (stage, ir, alias) in enumerate(sections):
        last = i == len(sections) - 1
        out_net = out_name if last else f"S{stage}"
        src = prev
        if buffers and i > 0:
            src = f"S{stage}_buf"
            bufs.append({"key": f"E{stage}", "out": src, "inp": prev})

        def net(n, _stage=stage, _ir=ir, _src=src, _out=out_net):
            if n == GND:
                return GND
            if n == _ir["in"]:
                return _src
            if n == _ir["out"]:
                return _out
            return f"S{_stage}_{n}"

        for p in ir["parts"]:
            parts.append({**p, "name": designator(stage, p["key"], alias),
                          "n1": net(p["n1"]), "n2": net(p["n2"]), "stage": stage})
        for o in ir["opamps"]:
            opamps.append({**o, "name": designator(stage, o["slot"]),
                           "inp": net(o["inp"]), "inn": net(o["inn"]),
                           "out": net(o["out"]), "stage": stage})
        prev = out_net
    nodes = set()
    for p in parts:
        nodes |= {p["n1"], p["n2"]}
    for o in opamps:
        nodes |= {o["inp"], o["inn"], o["out"]}
    for b in bufs:
        nodes |= {b["out"], b["inp"]}
    return {"nodes": sorted(nodes | {in_name, GND}), "in": in_name,
            "out": prev, "parts": parts, "opamps": opamps, "buffers": bufs}


# =====================================================================
#  Numeric AC solve (MNA)
# =====================================================================
def _gain(o, s):
    a0, wc = o["A_ol"], 2.0 * np.pi * o["GBWP_hz"]
    return a0 * wc / (wc + s * a0)


def mna_ac(ir, f_hz, ro_min_ohm=1e-3):
    """Complex V(out)/V(in) of an IR (section or cascade) at f_hz (array).

    The input node is driven by an ideal source. Op-amps follow the tool's
    model; Ro < ro_min_ohm is treated as 0 (a pure VCVS), as the FS generic
    subckt does. Cascade buffers ('buffers') are ideal unity VCVS."""
    f = np.atleast_1d(np.asarray(f_hz, dtype=float))
    s = 2j * np.pi * f
    nodes = [n for n in ir["nodes"] if n not in (GND, ir["in"])]
    idx = {n: i for i, n in enumerate(nodes)}
    vcvs = [o for o in ir["opamps"] if o.get("Ro_ohm", 0.0) < ro_min_ohm]
    bufs = list(ir.get("buffers", ()))
    n = len(nodes) + len(vcvs) + len(bufs)
    Y = np.zeros((f.size, n, n), dtype=complex)
    rhs = np.zeros((f.size, n), dtype=complex)

    def stamp(a, b, y):
        """Admittance y (array over f) between nodes a and b; Vin = 1."""
        for p, q in ((a, b), (b, a)):
            if p in idx:
                Y[:, idx[p], idx[p]] += y
                if q in idx:
                    Y[:, idx[p], idx[q]] -= y
                elif q == ir["in"]:
                    rhs[:, idx[p]] += y

    def put(row, node, coef):
        """Coefficient on node voltage `node` in equation `row`."""
        if node in idx:
            Y[:, row, idx[node]] += coef
        elif node == ir["in"]:
            rhs[:, row] -= coef

    for p in ir["parts"]:
        y = (np.full(f.size, 1.0 / p["value"], dtype=complex) if p["kind"] == "R"
             else s * p["value"])
        stamp(p["n1"], p["n2"], y)
    k = len(nodes)
    for o in ir["opamps"]:
        A = _gain(o, s)
        if o.get("Ro_ohm", 0.0) >= ro_min_ohm:          # Norton: A*Vd/Ro || Ro
            g = 1.0 / o["Ro_ohm"]
            if o["out"] in idx:
                r = idx[o["out"]]
                Y[:, r, r] += g
                put(r, o["inp"], -A * g)
                put(r, o["inn"], A * g)
        else:                                           # VCVS: V(out) = A*Vd
            r = k
            k += 1
            if o["out"] in idx:
                Y[:, idx[o["out"]], r] += 1.0
            put(r, o["out"], 1.0)
            put(r, o["inp"], -A)
            put(r, o["inn"], A)
    for b in bufs:
        r = k
        k += 1
        if b["out"] in idx:
            Y[:, idx[b["out"]], r] += 1.0
        put(r, b["out"], 1.0)
        put(r, b["inp"], -1.0)
    v = np.linalg.solve(Y, rhs[..., None])[..., 0]
    return v[:, idx[ir["out"]]]


def dc_floating_nodes(ir):
    """Nodes with no DC path to ground or to a driven node (the input source,
    an op-amp output, a buffer) through resistors. A real op-amp model needs
    one for every node, op-amp inputs included (bias current); an empty list
    means the circuit has a DC operating point."""
    adj = {}
    for p in ir["parts"]:
        if p["kind"] == "R":
            adj.setdefault(p["n1"], set()).add(p["n2"])
            adj.setdefault(p["n2"], set()).add(p["n1"])
    seen = {GND, ir["in"]} | {o["out"] for o in ir["opamps"]}
    seen |= {b["out"] for b in ir.get("buffers", ())}
    todo = list(seen)
    while todo:
        n = todo.pop()
        for m in adj.get(n, ()):
            if m not in seen:
                seen.add(m)
                todo.append(m)
    return sorted(set(ir["nodes"]) - seen)


# =====================================================================
#  Drawing spec for a template (python spice_cells.py [TEMPLATE | CELL])
# =====================================================================
def template_spec(template):
    """Every cell drawn from `template`, the union of its parts (key, nodes)
    and each part's state per cell -- what a hand-drawn superset must hold."""
    cells = [n for n in all_cell_names() if superset(n)["template"] == template]
    parts, states, opamps = {}, {}, {}
    for n in cells:
        sup = superset(n)
        for key, kind, n1, n2, st in sup["entries"]:
            if key is None:
                continue
            if key in parts and parts[key] != (kind, n1, n2):
                raise ValueError(f"{template}: {key} sits on different nodes in "
                                 f"different cells -- split the template")
            parts[key] = (kind, n1, n2)
            states.setdefault(key, {})[n] = st
        for slot, a, b, c in sup["opamps"]:
            opamps[slot] = (a, b, c)
    return cells, parts, states, opamps


def _print_spec(arg):
    templates = sorted({superset(n)["template"] for n in all_cell_names()})
    if arg not in templates:
        arg = superset(arg)["template"]                 # a cell name was given
    cells, parts, states, opamps = template_spec(arg)
    print(f"Template {arg}: {len(cells)} cell(s)")
    print("  " + "  ".join(cells))
    print("\nParts (draw every one; InstName = key, nets labelled by node name):")
    for key, (kind, n1, n2) in parts.items():
        marks = {st if st is not True else "on" for st in states[key].values()}
        note = "" if marks == {"on"} else "   [" + ", ".join(
            f"{c}: {'on' if s is True else s}" for c, s in states[key].items()
            if s is not True) + "]"
        print(f"  {key:<4} {n1:>4} -- {n2:<4}{note}")
    print("\nOp-amp seats (In+, In-, Out):")
    for slot, (a, b, c) in opamps.items():
        print(f"  {slot}: {a}, {b}, {c}")
    print("\nNets: 'in' -> flag IN, 'out' -> flag OUT, '0' -> ground; "
          "cells not listed in [...] have the part on.")
    print("Split caps: also draw a slot C1b / C2b in parallel with C1 / C2 (a row "
          "may carry C1a+C1b or C2a+C2b; -C1s cells always do).")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python spice_cells.py TEMPLATE|CELL\ntemplates: "
              + " ".join(sorted({superset(n)["template"] for n in all_cell_names()})))
    else:
        _print_spec(sys.argv[1])
