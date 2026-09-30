# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin

# =====================================================================
#  spice_asc.py  [Tier D — FS-008 LTspice schematic (.asc) model, no Streamlit]
#
#  Phase 2 of FS-008 (dev/FS-008_ltspice_export_design_note.md §3-§5, §12):
#    - read / write LTspice 24 .asc text (records: SYMBOL + WINDOW/SYMATTR,
#      WIRE, FLAG, IOPIN, TEXT, drawing lines); any other record is rejected
#    - symbol calibration (symbols.asc: pin offsets per symbol, the SYMBOL
#      text and the file header), with the stock .asy offsets as defaults
#    - connectivity from geometry (§4.2): coinciding points join, a wire end on
#      another wire's middle joins (T), same-named FLAGs join. A pin or FLAG on
#      a wire's middle and crossing / overlapping wires are ERRORS -- the
#      exporter never draws them and LTspice's behaviour there is [verify]
#    - op-amp seats (§5.4) and the auto-layout of a section (§4.4), sections
#      stacked in a column and joined by net labels (§4.5)
#    - phase 3: a section drawn from its hand-drawn cell template
#      (draw_template: gating of open / shorted parts, dangling-stub pruning,
#      seats emptied and filled with the section's op-amp dummy); a template
#      that fails its own check falls back to the auto-layout
#    - the export-time self-check (§4.3): the drawing is re-extracted and must
#      equal the cascade IR pin by pin, else AscError and nothing is written
#    - netlist_lines(): LTspice-style netlist of a drawing (dev check)
#
#  All coordinates are LTspice units, y grows downward, grid 16.
# =====================================================================

import re

EOL = "\r\n"
GRID = 16
VERSION_LINE = "Version 4"

# Seat (§5.4): terminal offsets from the seat origin; the box is +-SEAT_HALF.
SEAT_HALF = 128
SEAT_TERMS = {"INN": (-128, -32), "INP": (-128, 32), "OUT": (128, 0),
              "VCC": (0, -128), "VEE": (0, 128)}

OPAMP2 = "Opamps\\\\opamp2"             # as LTspice writes it in a SYMBOL line

# Calibration symbols: written name, pins in SPICE order with their R0
# offset (stock .asy, design note A.3a) and the direction of the stub that
# symbols.asc draws from each pin. symbols.asc (phase 2b) is the authority.
_UP, _DOWN, _LEFT, _RIGHT = (0, -1), (0, 1), (-1, 0), (1, 0)
CAL_DEFAULT = {
    "res": ("res", [("A", (16, 16), _UP), ("B", (16, 96), _DOWN)]),
    "cap": ("cap", [("A", (16, 0), _UP), ("B", (16, 64), _DOWN)]),
    "voltage": ("voltage", [("P", (0, 16), _UP), ("N", (0, 96), _DOWN)]),
    "bv": ("bv", [("P", (0, 16), _UP), ("N", (0, 96), _DOWN)]),
    "opamp2": (OPAMP2, [("INP", (-32, 80), _LEFT), ("INN", (-32, 48), _LEFT),
                        ("VP", (0, 32), _UP), ("VN", (0, 96), _DOWN),
                        ("OUT", (32, 64), _RIGHT)]),
}
_CAL_VALUE = {"res": "1k", "cap": "1n", "voltage": "0", "bv": "V=0", "opamp2": "FS_CAL"}
CAL_ORIENTS = {"res": ("R0", "R90"), "cap": ("R0", "R90"), "voltage": ("R0",),
               "bv": ("R0",), "opamp2": ("R0",)}

# opamp2 pin -> seat role (its adapter wiring in the FS generic dummy)
OPAMP2_ROLE = {"INP": "INP", "INN": "INN", "VP": "VCC", "VN": "VEE", "OUT": "OUT"}


class AscError(ValueError):
    """A drawing that is not what the IR says -- never written."""


# =====================================================================
#  Text I/O
# =====================================================================
def decode(raw):
    """LTspice files may be UTF-16LE (with or without BOM), UTF-8 or cp1252."""
    if raw[:2] == b"\xff\xfe":
        return raw[2:].decode("utf-16-le")
    if raw[:2] == b"\xfe\xff":
        return raw[2:].decode("utf-16-be")
    if len(raw) > 3 and raw[1:2] == b"\x00" and raw[3:4] == b"\x00":
        return raw.decode("utf-16-le")
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def read_text(path):
    with open(path, "rb") as fh:
        return decode(fh.read())


def sym_base(name):
    """'Opamps\\\\opamp2' -> 'opamp2' (case-folded)."""
    return re.split(r"[\\/]+", name)[-1].lower()


_DRAW = ("LINE", "RECTANGLE", "CIRCLE", "ARC", "DATAFLAG")


def new_asc(version=VERSION_LINE):
    return {"version": version, "sheet": (880, 680), "wires": [], "flags": [],
            "iopins": [], "symbols": [], "texts": [], "drawing": []}


def parse(text):
    """Records of an .asc text. Unknown record types raise ValueError."""
    asc = new_asc()
    cur = None
    for no, raw in enumerate(text.splitlines(), 1):
        ln = raw.rstrip()
        if not ln.strip():
            continue
        head = ln.split(" ", 1)[0]
        try:
            if head == "Version":
                asc["version"] = ln
            elif head == "SHEET":
                t = ln.split()
                asc["sheet"] = (int(t[2]), int(t[3]))
            elif head == "WIRE":
                asc["wires"].append(tuple(int(v) for v in ln.split()[1:5]))
            elif head == "FLAG":
                _, x, y, name = ln.split(" ", 3)
                asc["flags"].append((int(x), int(y), name.strip()))
            elif head == "IOPIN":
                _, x, y, d = ln.split(" ", 3)
                asc["iopins"].append((int(x), int(y), d.strip()))
            elif head == "SYMBOL":
                sym, x, y, orient = ln[7:].rsplit(" ", 3)
                cur = {"sym": sym, "x": int(x), "y": int(y), "orient": orient, "attrs": []}
                asc["symbols"].append(cur)
            elif head in ("WINDOW", "SYMATTR"):
                if cur is None:
                    raise ValueError(f"{head} before any SYMBOL")
                cur["attrs"].append(ln)
            elif head == "TEXT":
                p = ln.split(" ", 5)
                asc["texts"].append({"x": int(p[1]), "y": int(p[2]), "align": p[3],
                                     "size": p[4], "text": p[5] if len(p) > 5 else ""})
            elif head in _DRAW:
                asc["drawing"].append(ln)
            else:
                raise ValueError(f"unknown record type {head!r}")
        except (ValueError, IndexError) as e:
            raise ValueError(f"line {no}: {ln!r}: {e}") from None
    return asc


def serialize(asc):
    L = [asc["version"], "SHEET 1 %d %d" % tuple(asc["sheet"])]
    L += ["WIRE %d %d %d %d" % w for w in asc["wires"]]
    L += ["FLAG %d %d %s" % f for f in asc["flags"]]
    L += ["IOPIN %d %d %s" % p for p in asc["iopins"]]
    for s in asc["symbols"]:
        L.append(f"SYMBOL {s['sym']} {s['x']} {s['y']} {s['orient']}")
        L += s["attrs"]
    L += [f"TEXT {t['x']} {t['y']} {t['align']} {t['size']} {t['text']}" for t in asc["texts"]]
    L += asc["drawing"]
    return EOL.join(L) + EOL


def attr(sym, key):
    """SYMATTR value of a symbol record, or None."""
    for ln in sym["attrs"]:
        p = ln.split(" ", 2)
        if p[0] == "SYMATTR" and p[1] == key:
            return p[2] if len(p) > 2 else ""
    return None


def set_attr(sym, key, value):
    """Replace (or append) a SYMATTR line; value None removes it."""
    out, done = [], False
    for ln in sym["attrs"]:
        p = ln.split(" ", 2)
        if p[0] == "SYMATTR" and p[1] == key:
            if value is not None and not done:
                out.append(f"SYMATTR {key} {value}")
            done = True
            continue
        out.append(ln)
    if not done and value is not None:
        out.append(f"SYMATTR {key} {value}")
    sym["attrs"] = out


def text_lines(t):
    """Lines of a TEXT record (LTspice stores newlines as a literal \\n)."""
    return t.split("\\n")


def parse_asy(text):
    """{'pins': [(name, (x, y))] in SpiceOrder, 'attrs': {key: value}} of a
    symbol (.asy) file."""
    pins, attrs, cur = [], {}, None
    for ln in text.splitlines():
        p = ln.strip().split(" ", 2)
        if p[0] == "PIN" and len(p) >= 3:
            t = ln.split()
            cur = {"x": int(t[1]), "y": int(t[2]), "name": None, "order": len(pins) + 1}
            pins.append(cur)
        elif p[0] == "PINATTR" and cur is not None and len(p) == 3:
            if p[1] == "PinName":
                cur["name"] = p[2]
            elif p[1] == "SpiceOrder":
                cur["order"] = int(p[2])
        elif p[0] == "SYMATTR" and len(p) == 3:
            attrs[p[1]] = p[2]
    pins.sort(key=lambda q: q["order"])
    return {"pins": [(q["name"] or f"P{q['order']}", (q["x"], q["y"])) for q in pins],
            "attrs": attrs}


# =====================================================================
#  Geometry
# =====================================================================
def xform(orient, p):
    """Pin offset in orientation R0/R90/R180/R270 (M* = mirrored first).
    Phase 2b verifies LTspice's rotation sense; the exporter itself draws
    every symbol in R0."""
    x, y = p
    if orient.startswith("M"):
        x = -x
    for _ in range((int(orient[1:]) % 360) // 90):
        x, y = -y, x
    return (x, y)


def _between(p, a, b):
    """p strictly inside segment a-b (collinear, not an end)."""
    if p == a or p == b:
        return False
    (px, py), (ax, ay), (bx, by) = p, a, b
    if (bx - ax) * (py - ay) - (by - ay) * (px - ax) != 0:
        return False
    return min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)


def _sgn(v):
    return (v > 0) - (v < 0)


def _orient(p, q, r):
    return _sgn((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]))


def _bad_pair(w1, w2):
    """Two wires that cross in their middles or overlap collinearly."""
    a, b, c, d = w1[:2], w1[2:], w2[:2], w2[2:]
    if (max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0])
            or max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1])):
        return False
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return True
    if o1 == o2 == o3 == o4 == 0:                     # collinear: overlap > a point?
        k = 0 if a[0] != b[0] or c[0] != d[0] else 1
        lo = max(min(a[k], b[k]), min(c[k], d[k]))
        hi = min(max(a[k], b[k]), max(c[k], d[k]))
        return hi > lo
    return False


def connectivity(wires, flags, pins):
    """Nets from geometry.

    wires : [(x1, y1, x2, y2)]     flags : [(x, y, name)]
    pins  : [(key, (x, y))]
    Returns ({key: net name | None (unconnected)}, [errors]). Net names are
    the FLAG names upper-cased ('0' = ground); a net without a FLAG gets
    'N$<k>'. Two different FLAG names on one net are an error."""
    parent = {}

    def find(p):
        parent.setdefault(p, p)
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    errors = []
    ws = [w for w in wires if w[:2] != w[2:]]
    ends = set()
    for w in ws:
        union(w[:2], w[2:])
        ends |= {w[:2], w[2:]}
    fpts = {(x, y) for x, y, _ in flags}
    pin_count = {}
    for _, p in pins:
        pin_count[p] = pin_count.get(p, 0) + 1

    # points on a wire's middle: a wire end joins (T), a pin / FLAG is an error
    horiz, vert, diag = {}, {}, []
    for w in ws:
        if w[1] == w[3]:
            horiz.setdefault(w[1], []).append(w)
        elif w[0] == w[2]:
            vert.setdefault(w[0], []).append(w)
        else:
            diag.append(w)
    kinds = {}
    for p in ends:
        kinds[p] = "wire end"
    for p in pin_count:
        kinds.setdefault(p, "pin")
    for p in fpts:
        kinds.setdefault(p, "flag")
    for p, kind in kinds.items():
        for w in horiz.get(p[1], []) + vert.get(p[0], []) + diag:
            if _between(p, w[:2], w[2:]):
                if kind == "wire end":
                    union(p, w[:2])
                else:
                    errors.append(f"{kind} at {p} lies on the middle of wire {w}")
    # crossings / overlaps
    for i in range(len(ws)):
        for j in range(i + 1, len(ws)):
            if _bad_pair(ws[i], ws[j]):
                errors.append(f"wires {ws[i]} and {ws[j]} cross or overlap")

    names = {}
    for x, y, n in flags:
        names.setdefault(find((x, y)), set()).add(n.strip().upper())
    for r, ns in names.items():
        if len(ns) > 1:
            errors.append("labels " + ", ".join(sorted(ns)) + " are joined into one net")
    anon, out = {}, {}
    for key, p in pins:
        if p not in ends and p not in fpts and pin_count[p] < 2:
            out[key] = None
            continue
        r = find(p)
        ns = names.get(r)
        if ns:
            out[key] = sorted(ns)[0]
        else:
            out[key] = anon.setdefault(r, f"N${len(anon) + 1}")
    return out, errors


# =====================================================================
#  Symbol calibration (symbols.asc)
# =====================================================================
class Calibration:
    """Pin offsets and SYMBOL text of the calibration symbols. Built from
    symbols.asc when given (the maintainer's LTspice-saved copy is the
    authority), else from the stock .asy defaults."""

    def __init__(self, text=None, source="defaults"):
        self.version = VERSION_LINE
        self.source = source
        self.errors = []
        self._sym = {b: v[0] for b, v in CAL_DEFAULT.items()}
        self._pins = {(b, "R0"): [(n, off) for n, off, _ in v[1]]
                      for b, v in CAL_DEFAULT.items()}
        if text is not None:
            self._load(text)

    def _load(self, text):
        try:
            asc = parse(text)
        except ValueError as e:
            self.errors.append(f"symbols.asc unreadable ({e}); defaults used")
            return
        self.version = asc["version"]
        flags = {n.upper(): (x, y) for x, y, n in asc["flags"]}
        for s in asc["symbols"]:
            b = sym_base(s["sym"])
            if b not in CAL_DEFAULT:
                continue
            got = []
            for pin, _off, _d in CAL_DEFAULT[b][1]:
                f = flags.get(f"{b}_{s['orient']}_{pin}".upper())
                far = None
                if f is not None:
                    for w in asc["wires"]:
                        if w[:2] == f:
                            far = w[2:]
                        elif w[2:] == f:
                            far = w[:2]
                if far is None:
                    self.errors.append(f"symbols.asc: no stub for {b} {s['orient']} {pin}")
                    break
                got.append((pin, (far[0] - s["x"], far[1] - s["y"])))
            else:
                self._pins[(b, s["orient"])] = got
                self._sym[b] = s["sym"]

    def sym(self, base):
        return self._sym[base]

    def pins(self, base, orient="R0"):
        """[(pin, (dx, dy))] in SPICE order, or None for an unknown symbol."""
        if base not in self._sym:
            return None
        got = self._pins.get((base, orient))
        if got is None:
            got = [(n, xform(orient, off)) for n, off in self._pins[(base, "R0")]]
        return got


def calibration_text():
    """symbols.asc as the tool writes it (phase 2b: open in LTspice, check
    View > SPICE Netlist -- every device on its named nets -- and save)."""
    asc = new_asc()
    x = 64
    for b, (sym, pins) in CAL_DEFAULT.items():
        for orient in CAL_ORIENTS[b]:
            y = 128
            inst = ("U" if b == "opamp2" else b.upper()[:1]) + orient
            asc["symbols"].append({"sym": sym, "x": x, "y": y, "orient": orient,
                                   "attrs": [f"SYMATTR InstName {inst}",
                                             f"SYMATTR Value {_CAL_VALUE[b]}"]})
            for pin, off, d in pins:
                px, py = xform(orient, off)
                dx, dy = xform(orient, d)
                p, q = (x + px, y + py), (x + px + 32 * dx, y + py + 32 * dy)
                asc["wires"].append(p + q)
                asc["flags"].append((q[0], q[1], f"{b}_{orient}_{pin}"))
            x += 256
    asc["texts"].append({"x": 64, "y": 16, "align": "Left", "size": "2",
                         "text": ";FS-008 symbol calibration. Every pin has a stub to a label "
                                 "<symbol>_<orientation>_<pin>. Check View > SPICE Netlist "
                                 "(each device on its named nets, no N00x net), then save."})
    asc["sheet"] = (x + 64, 320)
    return serialize(asc)


# =====================================================================
#  Drawing primitives
# =====================================================================
def _snap(v):
    return int(-(-v // GRID) * GRID)


def _two_pin(asc, cal, base, inst, value, net_a, net_b, x, y, stub=32):
    """R, C or V symbol in R0 with a labelled stub on each pin."""
    asc["symbols"].append({"sym": cal.sym(base), "x": x, "y": y, "orient": "R0",
                           "attrs": [f"SYMATTR InstName {inst}", f"SYMATTR Value {value}"]})
    (_, (ax, ay)), (_, (bx, by)) = cal.pins(base)
    a, b = (x + ax, y + ay), (x + bx, y + by)
    ea, eb = (a[0], a[1] - stub), (b[0], b[1] + stub)
    asc["wires"] += [a + ea, b + eb]
    asc["flags"] += [(ea[0], ea[1], net_a), (eb[0], eb[1], net_b)]


def place_seat(asc, dummy, inst, origin, nets, value=None):
    """Copy an op-amp dummy block into a seat at `origin`.

    dummy : spice_opamps dummy dict (symbol, wires, inner flags, terminals,
            origin of its seat inside the dummy file).
    nets  : role -> net name ('INP', 'INN', 'OUT', 'VCC', 'VEE').
    The dummy's terminal FLAGs become labels with the section's nets
    (auto-layout, §4.4); its inner VCC / VEE / 0 labels are copied as is."""
    ox, oy = origin[0] - dummy["seat"][0], origin[1] - dummy["seat"][1]
    s = dummy["symbol"]
    sym = {"sym": s["sym"], "x": s["x"] + ox, "y": s["y"] + oy, "orient": s["orient"],
           "attrs": list(s["attrs"])}
    set_attr(sym, "InstName", inst)
    if value is not None:
        set_attr(sym, "Value", value)
    asc["symbols"].append(sym)
    asc["wires"] += [(w[0] + ox, w[1] + oy, w[2] + ox, w[3] + oy) for w in dummy["wires"]]
    asc["flags"] += [(x + ox, y + oy, n) for x, y, n in dummy["inner_flags"]]
    for role in dummy["terminals"]:
        tx, ty = SEAT_TERMS[role]
        asc["flags"].append((origin[0] + tx, origin[1] + ty, nets[role]))
    return sym


# =====================================================================
#  Hand-drawn cell templates (phase 3, design note App. B)
# =====================================================================
_SEAT_RE = re.compile(r"^;\s*SEAT\s+(U\d+)\s*$", re.I)


def template_seats(asc):
    """{slot: seat origin} from the ';SEAT Uk' anchors (the box centres)."""
    return {m.group(1).upper(): (t["x"], t["y"]) for t in asc["texts"]
            for m in [_SEAT_RE.match(t["text"])] if m}


def _seat_role(rel):
    """Seat terminal a wire end on the box edge feeds, else None. INN is any
    point on the left edge above the centre, INP below it, OUT the right
    edge, VCC the top, VEE the bottom; a point off the standard offset gets
    a jog along the edge to it."""
    x, y, h = rel[0], rel[1], SEAT_HALF
    if x == -h and -h < y < h and y != 0:
        return "INN" if y < 0 else "INP"
    if x == h and -h < y < h:
        return "OUT"
    if y in (-h, h) and -h < x < h:
        return "VCC" if y < 0 else "VEE"
    return None


def _in_box(p, o, strict=False):
    d = max(abs(p[0] - o[0]), abs(p[1] - o[1]))
    return d < SEAT_HALF if strict else d <= SEAT_HALF


def draw_template(tpl, sup, node_map, parts, opamps, net, values, dummies, cal):
    """One section drawn from a hand-drawn template, in template coordinates.

    tpl      : parsed template (.asc records); parts carry InstName = superset key
               (C1b / C2b: the split slot), nets carry labels named by node
    sup      : spice_cells superset of the section's cell (part states)
    node_map : spice_cells.gating node map (superset node -> surviving node)
    parts    : the section's cascade parts ('key' = IR key, 'name' = designator)
    opamps   : the section's cascade op-amps ('slot', 'name', optional 'subckt')
    net      : surviving node -> cascade net name
    values   : {part name: value text};  dummies : {op-amp name: dummy}
    Gating: a present part is renamed and valued; an open one is deleted; a
    shorted one becomes a wire between its pins. A label of a node merged away
    by a short is dropped when it is that node's only label (else renamed);
    wires left dangling are pruned, then labels left alone. Each seat is
    emptied and gets the section's op-amp dummy. Raises AscError."""
    seats = template_seats(tpl)
    want_slots = {s for s, *_ in sup["opamps"]}
    if set(seats) != want_slots:
        raise AscError(f"seats {sorted(seats)} != op-amps {sorted(want_slots)}")
    asc = new_asc(tpl["version"])

    # ---- empty the seats; find the terminal points ----
    def seat_of(p, strict):
        return next((s for s, o in seats.items() if _in_box(p, o, strict)), None)
    wires = []
    for w in tpl["wires"]:
        if w[:2] == w[2:]:
            continue
        a, b = seat_of(w[:2], False), seat_of(w[2:], False)
        if a is not None and a == b:
            continue                                   # placeholder wiring
        if seat_of(w[:2], True) or seat_of(w[2:], True):
            raise AscError(f"wire {w} enters a seat box")
        wires.append(w)
    terms = {s: {} for s in seats}
    for w in wires:
        for p in (w[:2], w[2:]):
            s = seat_of(p, False)
            if s is None:
                continue
            o = seats[s]
            role = _seat_role((p[0] - o[0], p[1] - o[1]))
            if role is None:
                raise AscError(f"{s}: wire end {p} is on the seat box but not at a terminal")
            if terms[s].setdefault(role, p) != p:
                raise AscError(f"{s}: two wires feed {role}")
    flags = [f for f in tpl["flags"] if seat_of(f[:2], True) is None]
    syms = []
    for s in tpl["symbols"]:
        if seat_of((s["x"], s["y"]), True) is None:
            syms.append(s)
        elif sym_base(s["sym"]) in ("res", "cap"):
            raise AscError(f"part {attr(s, 'InstName')} sits inside a seat box")

    # ---- gate the parts ----
    states = {k: st for k, _kind, _a, _b, st in sup["entries"] if k}
    by_key = {p["key"]: p for p in parts}
    used, placed, pin_pts = set(), [], set()
    orig_pin_pts = set()
    for s in syms:
        key = attr(s, "InstName") or ""
        base, slot_b = key, False
        if key not in states and key[-1:] in "bB" and key[:-1] in states:
            base, slot_b = key[:-1], True
        if base not in states:
            raise AscError(f"template part {key!r} is not a part of {sorted(states)}")
        pins = cal.pins(sym_base(s["sym"]), s["orient"])
        if pins is None or len(pins) != 2:
            raise AscError(f"{key}: unknown symbol {s['sym']}")
        pts = [(s["x"] + dx, s["y"] + dy) for _n, (dx, dy) in pins]
        orig_pin_pts |= set(pts)
        st = states[base]
        target = None
        if st is True:
            target = by_key.get(base + "b") if slot_b else (by_key.get(base) or by_key.get(base + "a"))
            if target is None and not slot_b:
                raise AscError(f"{key}: present in the cell but not in the section")
        elif st == "short" and not slot_b:
            wires.append(pts[0] + pts[1])
        if target is None:
            continue                                   # open, shorted or unused split slot
        sym = {"sym": s["sym"], "x": s["x"], "y": s["y"], "orient": s["orient"],
               "attrs": list(s["attrs"])}
        set_attr(sym, "InstName", target["name"])
        set_attr(sym, "Value", values[target["name"]])
        placed.append(sym)
        used.add(target["key"])
        pin_pts |= set(pts)
    missing = [p["key"] for p in parts if p["key"] not in used]
    if missing:
        raise AscError("no symbol for " + ", ".join(missing))

    # ---- labels: relabel to cascade nets; drop the lone label of a merged node ----
    count = {}
    for _x, _y, n in flags:
        count[n.strip().lower()] = count.get(n.strip().lower(), 0) + 1
    out_flags = []
    for x, y, n in flags:
        u = n.strip().lower()
        if u in ("vcc", "vee"):
            out_flags.append((x, y, u.upper()))
            continue
        if u not in node_map:
            raise AscError(f"label {n!r} is not a node of the cell ({', '.join(sorted(node_map))})")
        tgt = node_map[u]
        if tgt != u and count[u] == 1:
            continue
        out_flags.append((x, y, net(tgt)))

    # ---- prune wires left dangling, then labels left alone ----
    term_pts = {p for t in terms.values() for p in t.values()}
    orig_deg = {}
    for w in wires:
        for p in (w[:2], w[2:]):
            orig_deg[p] = orig_deg.get(p, 0) + 1
    tag_pts = {(x, y) for x, y, _n in out_flags
               if orig_deg.get((x, y), 0) == 1 and (x, y) not in orig_pin_pts}
    anchors = pin_pts | term_pts | tag_pts
    while True:
        deg = {}
        for w in wires:
            for p in (w[:2], w[2:]):
                deg[p] = deg.get(p, 0) + 1

        def loose(p):
            if deg[p] > 1 or p in anchors:
                return False
            return not any(_between(p, w[:2], w[2:]) for w in wires)
        gone = [w for w in wires if loose(w[:2]) or loose(w[2:])]
        if not gone:
            break
        for w in gone:          # a net's only label rides back to the surviving end
            for a, b in ((w[:2], w[2:]), (w[2:], w[:2])):
                if loose(a) and not loose(b):
                    for i, f in enumerate(out_flags):
                        if f[:2] == a and sum(g[2] == f[2] for g in out_flags) == 1:
                            out_flags[i] = b + (f[2],)
        wires = [w for w in wires if w not in gone]
    on_wire = {p for w in wires for p in (w[:2], w[2:])}
    out_flags = [f for f in out_flags if f[:2] in on_wire or f[:2] in pin_pts
                 or any(_between(f[:2], w[:2], w[2:]) for w in wires)]

    asc["wires"], asc["flags"], asc["symbols"] = wires, out_flags, placed
    asc["drawing"] = list(tpl["drawing"])

    # ---- fill the seats ----
    for o in opamps:
        d, org = dummies[o["name"]], seats[o["slot"]]
        ox, oy = org[0] - d["seat"][0], org[1] - d["seat"][1]
        s = d["symbol"]
        sym = {"sym": s["sym"], "x": s["x"] + ox, "y": s["y"] + oy, "orient": s["orient"],
               "attrs": list(s["attrs"])}
        set_attr(sym, "InstName", o["name"])
        if d["fs_generic"] and o.get("subckt"):
            set_attr(sym, "Value", o["subckt"])
        asc["symbols"].append(sym)
        asc["wires"] += [(w[0] + ox, w[1] + oy, w[2] + ox, w[3] + oy) for w in d["wires"]]
        asc["flags"] += [(x + ox, y + oy, n) for x, y, n in d["inner_flags"]]
        for role in d["terminals"]:
            std = (org[0] + SEAT_TERMS[role][0], org[1] + SEAT_TERMS[role][1])
            got = terms[o["slot"]].get(role)
            if got is None:
                if role in ("VCC", "VEE"):
                    asc["flags"].append(std + (role,))
                    continue
                raise AscError(f"{o['slot']}: nothing wired to {role}")
            if got != std:
                asc["wires"].append(got + std)          # jog along the box edge
    return asc


def _shift_drawing(line, dx, dy):
    """A LINE / RECTANGLE / CIRCLE / ARC / DATAFLAG record moved by (dx, dy)."""
    t = line.split(" ")
    if t[0] == "DATAFLAG":
        idx = (1, 2)
    else:
        idx = tuple(range(2, 2 + (8 if t[0] == "ARC" else 4)))
    for i in idx:
        t[i] = str(int(t[i]) + (dx if (i - idx[0]) % 2 == 0 else dy))
    return " ".join(t)


def _bbox(asc, cal):
    pts = [w[:2] for w in asc["wires"]] + [w[2:] for w in asc["wires"]]
    pts += [f[:2] for f in asc["flags"]]
    for s in asc["symbols"]:
        pts.append((s["x"], s["y"]))
        for _n, (dx, dy) in cal.pins(sym_base(s["sym"]), s["orient"]) or ():
            pts.append((s["x"] + dx, s["y"] + dy))
    for ln in asc["drawing"]:
        t = ln.split(" ")
        if t[0] in ("RECTANGLE", "LINE", "CIRCLE"):
            pts += [(int(t[2]), int(t[3])), (int(t[4]), int(t[5]))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _paste(dst, src, dx, dy):
    """Append the records of `src` moved by (dx, dy)."""
    dst["wires"] += [(w[0] + dx, w[1] + dy, w[2] + dx, w[3] + dy) for w in src["wires"]]
    dst["flags"] += [(x + dx, y + dy, n) for x, y, n in src["flags"]]
    dst["symbols"] += [dict(s, x=s["x"] + dx, y=s["y"] + dy) for s in src["symbols"]]
    dst["drawing"] += [_shift_drawing(ln, dx, dy) for ln in src["drawing"]]


def _text(asc, x, y, lines, directive):
    """One TEXT record (multi-line joined with LTspice's literal \\n)."""
    asc["texts"].append({"x": x, "y": y, "align": "Left", "size": "2",
                         "text": ("!" if directive else ";") + "\\n".join(lines)})
    return _snap(len(lines) * 40 + 24)


# =====================================================================
#  Cascade drawing: auto-layout + column + self-check
# =====================================================================
SEAT_PITCH = 640          # FS-008 next round: wide enough that net labels read
PART_PITCH = 256
LABEL_STUB = 48


def _section_block(asc, cal, x0, y0, stage, title, parts, opamps, values, dummies):
    """Auto-layout of one section at (x0, y0): title, op-amp seats, then a
    row of resistors and a row of capacitors, every pin on a labelled stub.
    Returns (width, height)."""
    _text(asc, x0, y0, title, False)
    oy = y0 + 256
    for k, o in enumerate(opamps):
        d = dummies[o["name"]]
        nets = {"INP": o["inp"], "INN": o["inn"], "OUT": o["out"], "VCC": "VCC", "VEE": "VEE"}
        place_seat(asc, d, o["name"], (x0 + 256 + SEAT_PITCH * k, oy), nets,
                   value=o.get("subckt") if d["fs_generic"] else None)
    yr, yc = oy + 240, oy + 544
    rs = [p for p in parts if p["kind"] == "R"]
    cs = [p for p in parts if p["kind"] == "C"]
    for row, yy, base in ((rs, yr, "res"), (cs, yc, "cap")):
        for i, p in enumerate(row):
            _two_pin(asc, cal, base, p["name"], values[p["name"]], p["n1"], p["n2"],
                     x0 + 64 + PART_PITCH * i, yy, stub=LABEL_STUB)
    width = max(SEAT_PITCH * max(len(opamps), 1), 64 + PART_PITCH * max(len(rs), len(cs), 1))
    return width, yc + 192 - y0


def _template_block(asc, cal, x0, y0, stage, title, parts, opamps, values, dummies,
                    casc, tpl):
    """Section `stage` drawn from its template at (x0, y0), checked on its
    own first; AscError leaves `asc` untouched. Returns (width, height)."""
    nets = casc["section_nets"][stage]                 # spice_cells.cascade_net
    blk = draw_template(tpl["asc"], tpl["sup"], tpl["node_map"], parts, opamps,
                        lambda n: nets.get(n, nets["*"] + n), values, dummies, cal)
    check_drawing(blk, {"parts": parts, "opamps": opamps}, dummies, cal, sources=False)
    th = _text(asc, x0, y0, title, False)
    x1, y1, x2, y2 = _bbox(blk, cal)
    dx, dy = _snap(x0 + 96 - x1), _snap(y0 + th + 96 - y1)
    _paste(asc, blk, dx, dy)
    return x2 - x1 + 192, y2 - y1 + th + 192


def draw_cascade(casc, titles, values, dummies, directive_blocks, header, cal,
                 comment_blocks=(), templates=None, drawn=None):
    """The whole cascade as one .asc text, self-checked against `casc`.

    casc       : spice_cells.cascade_ir (parts with name/kind/n1/n2/stage,
                 opamps with name/inp/inn/out/stage and optional 'subckt' =
                 the Value to set on the op-amp symbol).
    titles     : {stage: [comment lines]}
    values     : {part name: SPICE value text}
    dummies    : {op-amp name: dummy dict} (spice_opamps)
    directive_blocks : [[lines]] each written as one directive TEXT
    header     : [comment lines] at the top of the sheet
    comment_blocks : [[lines]] extra comment TEXTs in the directive column
    templates  : {stage: {'tid', 'asc': parsed template (None + 'error' when
                 unreadable), 'sup' / 'node_map': spice_cells.gating of the
                 cell}}; a section without one, or whose template fails, is
                 auto-laid-out
    drawn      : dict filled with {stage: 'template <tid>' | 'auto-layout'
                 [(reason)]}"""
    drawn = {} if drawn is None else drawn
    templates = templates or {}
    asc = new_asc(cal.version)
    y = _text(asc, 0, 0, header, False) + 32
    src = [("VIN", "AC 1", "IN", "0"), ("VPOS", "{Vs/2}", "VCC", "0"),
           ("VNEG", "{Vs/2}", "0", "VEE")]
    for i, (inst, val, a, b) in enumerate(src):
        _two_pin(asc, cal, "voltage", inst, val, a, b, 64 + 224 * i, y + 48)
    y += 256
    width = 64 + 224 * len(src)
    stages = []
    for o in casc["parts"] + casc["opamps"]:
        if o["stage"] not in stages:
            stages.append(o["stage"])
    for st in stages:
        parts = [p for p in casc["parts"] if p["stage"] == st]
        ops = [o for o in casc["opamps"] if o["stage"] == st]
        title = titles.get(st, [f"Section {st}"])
        tpl, why = templates.get(st), None
        if tpl is not None and tpl.get("asc") is not None:
            try:
                w, h = _template_block(asc, cal, 0, y, st, title, parts, ops, values,
                                       dummies, casc, tpl)
                drawn[st] = f"template {tpl['tid']}"
                width = max(width, w)
                y = _snap(y + h + 64)
                continue
            except AscError as e:
                why = f"template {tpl['tid']} rejected: {e}"
        elif tpl is not None:
            why = tpl.get("error") or "template unreadable"
        drawn[st] = "auto-layout" + (f" ({why})" if why else "")
        w, h = _section_block(asc, cal, 0, y, st, title, parts, ops, values, dummies)
        width = max(width, w)
        y = _snap(y + h + 64)
    xd, yd = _snap(width + 256), 0
    for lines in directive_blocks:
        yd += _text(asc, xd, yd, lines, True) + 32
    for lines in comment_blocks:
        yd += _text(asc, xd, yd, lines, False) + 32
    asc["sheet"] = (_snap(xd + 1600), _snap(max(y, yd) + 64))
    check_drawing(asc, casc, dummies, cal)
    return serialize(asc)


def _xpoints(sym, dummy):
    """[(role, absolute point | None)] of a placed op-amp, X-line order."""
    return [(role, None if off is None else (sym["x"] + off[0], sym["y"] + off[1]))
            for role, off in dummy["xpins"]]


def check_drawing(asc, casc, dummies, cal, sources=True):
    """§4.3: re-extract the drawing's nets and compare with the cascade IR,
    pin by pin. Raises AscError on any difference. sources=False checks one
    section's block (no VIN / VPOS / VNEG)."""
    want, got_pins = {}, []
    for p in casc["parts"]:
        want[p["name"]] = ("2", {p["n1"].upper(), p["n2"].upper()})
    for inst, a, b in (("VIN", "IN", "0"), ("VPOS", "VCC", "0"), ("VNEG", "0", "VEE")):
        if sources:
            want[inst] = ("2", {a, b})
    role_net = lambda o, r: {"INP": o["inp"], "INN": o["inn"], "OUT": o["out"],  # noqa: E731
                             "VCC": "VCC", "VEE": "VEE", "0": "0"}.get(r)
    for o in casc["opamps"]:
        want[o["name"]] = ("X", [None if r is None else role_net(o, r).upper()
                                 for r, _off in dummies[o["name"]]["xpins"]])
    seen = {}
    for s in asc["symbols"]:
        inst = attr(s, "InstName")
        if inst in seen:
            raise AscError(f"two symbols named {inst}")
        seen[inst] = s
        if inst not in want:
            raise AscError(f"symbol {inst} is not in the netlist")
        if want[inst][0] == "2":
            pins = cal.pins(sym_base(s["sym"]), s["orient"])
            if pins is None:
                raise AscError(f"{inst}: unknown symbol {s['sym']}")
            got_pins += [((inst, n), (s["x"] + dx, s["y"] + dy)) for n, (dx, dy) in pins]
        else:
            got_pins += [((inst, i), pt) for i, (_r, pt) in
                         enumerate(_xpoints(s, dummies[inst])) if pt is not None]
    missing = set(want) - set(seen)
    if missing:
        raise AscError("not drawn: " + ", ".join(sorted(missing)))
    nets, errors = connectivity(asc["wires"], asc["flags"], got_pins)
    if errors:
        raise AscError("; ".join(errors[:5]))
    for inst, (kind, exp) in want.items():
        if kind == "2":
            got = {nets[(inst, n)] for (i, n) in nets if i == inst}
            if got != exp:
                raise AscError(f"{inst}: drawn on {sorted(map(str, got))}, IR says {sorted(exp)}")
        else:
            for i, (r, pt) in enumerate(_xpoints(seen[inst], dummies[inst])):
                if pt is None:
                    continue
                if nets[(inst, i)] != exp[i]:
                    raise AscError(f"{inst} pin {i + 1} ({r}): drawn on {nets[(inst, i)]}, "
                                   f"IR says {exp[i]}")


def netlist_lines(text, cal, opamp_dummies):
    """LTspice-style netlist of a drawing: element lines from the geometry
    (R/C/V from the calibration, op-amps from their dummy's X-line pins) plus
    every directive TEXT. For the dev check; LTspice's own View > SPICE
    Netlist is the reference (phase 2b)."""
    asc = parse(text)
    pins, elems = [], []
    for s in asc["symbols"]:
        inst = attr(s, "InstName")
        base = sym_base(s["sym"])
        cp = cal.pins(base, s["orient"])
        if cp is not None and base != "opamp2":
            pins += [((inst, n), (s["x"] + dx, s["y"] + dy)) for n, (dx, dy) in cp]
            elems.append((inst, [(inst, n) for n, _ in cp], attr(s, "Value")))
        else:
            d = opamp_dummies[inst]
            xp = _xpoints(s, d)
            pins += [((inst, i), pt) for i, (_r, pt) in enumerate(xp) if pt is not None]
            elems.append(("X" + inst, [(inst, i) if pt is not None else None
                                       for i, (_r, pt) in enumerate(xp)],
                          attr(s, "Value") or d["xmodel"]))
    nets, errors = connectivity(asc["wires"], asc["flags"], pins)
    if errors:
        raise AscError("; ".join(errors[:5]))
    out = ["* netlist of the drawing"]
    for name, keys, val in elems:
        ns = [nets.get(k) or f"NC_{name}_{i}" if k is not None else f"NC_{name}_{i}"
              for i, k in enumerate(keys)]
        out.append(" ".join([name] + ns + [val]))
    for t in asc["texts"]:
        if t["text"].startswith("!"):
            out += text_lines(t["text"][1:])
    return out + [".end"]
