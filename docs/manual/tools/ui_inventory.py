# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin
"""
ui_inventory.py — static inventory of every user-facing control in the app.

Parses the UI modules with `ast` (no import, no Streamlit, no running app) and
emits one record per st.* widget call: what it is, its label, its session-state
key, its help text, its defaults and limits, which panel it sits in.

The result is the machine-readable picture of the UI that the manual is written
against. Re-run it after any UI change and `doc_drift.py` will tell you which
pages and screenshots went stale.

    python tools/ui_inventory.py                     # write docs/manual/ui_inventory.json
    python tools/ui_inventory.py --print             # human-readable table
    python tools/ui_inventory.py --markdown          # paste-ready reference table

Dynamic keys (key=f"hw_solve_{n}") are normalised to a pattern: hw_solve_*.
That is the identity the docs and the drift checker use, so a per-section
control counts once, not once per section.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Which files hold user-facing controls. Extend when a new UI module appears;
# doc_drift.py warns if a listed file is missing.
# --------------------------------------------------------------------------
UI_FILES = [
    "app.py",
    "ui_components.py",
    "topology_tab.py",
    "response_tab.py",
    "report_ui.py",
    "schematic_svg.py",
]

# st.<name> calls that create something the user can see or operate.
WIDGETS = {
    "number_input", "text_input", "text_area", "slider", "select_slider",
    "selectbox", "multiselect", "radio", "checkbox", "toggle", "button",
    "download_button", "form_submit_button", "file_uploader", "color_picker",
    "date_input", "time_input", "camera_input", "data_editor",
}
# Containers and labelled surfaces worth inventorying for navigation/figures.
CONTAINERS = {"expander", "tabs", "popover", "form", "dialog", "status"}

# Widgets whose first positional argument is NOT a label.
NO_LABEL_FIRST = {"tabs"}


def _src(node) -> str | None:
    """Best-effort source text of an AST node, for defaults and limits."""
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return "<expr>"


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _key_pattern(node):
    """key= as a stable identity. f"hw_solve_{n}" -> 'hw_solve_*'."""
    if node is None:
        return None
    lit = _const_str(node)
    if lit is not None:
        return lit
    if isinstance(node, ast.JoinedStr):
        out = []
        for p in node.values:
            if isinstance(p, ast.Constant) and isinstance(p.value, str):
                out.append(p.value)
            else:
                out.append("*")
        return "".join(out)
    return _src(node)


def _label_of(node, widget):
    """Label text, or a marker when it is computed at runtime."""
    if widget in NO_LABEL_FIRST:
        return None
    arg = node.args[0] if node.args else None
    if arg is None:
        for kw in node.keywords:
            if kw.arg == "label":
                arg = kw.value
                break
    if arg is None:
        return None
    lit = _const_str(arg)
    if lit is not None:
        return lit
    if isinstance(arg, ast.IfExp):                      # "Re-solve" if have else "Solve section"
        a, b = _const_str(arg.body), _const_str(arg.orelse)
        if a and b:
            return f"{a} | {b}"
    if isinstance(arg, ast.JoinedStr):
        return _key_pattern(arg)
    return f"<{_src(arg)}>"


def _string_list(node, consts):
    """Literal list of strings, or a module constant that resolves to one."""
    if isinstance(node, (ast.List, ast.Tuple)):
        vals = [_const_str(e) for e in node.elts]
        if all(v is not None for v in vals):
            return vals
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id]
    # list(SOME_DICT.keys())
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "list" and node.args):
        inner = node.args[0]
        if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                and inner.func.attr == "keys"
                and isinstance(inner.func.value, ast.Name)
                and inner.func.value.id in consts):
            return consts[inner.func.value.id]
    return None


def _module_constants(tree):
    """Module-level NAME = [...] / {...} so options= can be resolved to text."""
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name):
            continue
        v = node.value
        if isinstance(v, (ast.List, ast.Tuple)):
            vals = [_const_str(e) for e in v.elts]
            if all(x is not None for x in vals):
                out[tgt.id] = vals
        elif isinstance(v, ast.Dict):
            keys = [_const_str(k) for k in v.keys]
            if all(k is not None for k in keys):
                out[tgt.id] = keys
    return out


def _st_widget(node):
    """('number_input', False) for st.number_input / st.sidebar.number_input."""
    f = node.func
    if not isinstance(f, ast.Attribute):
        return None
    name = f.attr
    if name not in WIDGETS and name not in CONTAINERS:
        return None
    base = f.value
    if isinstance(base, ast.Name) and base.id == "st":
        return name, False
    if (isinstance(base, ast.Attribute) and base.attr == "sidebar"
            and isinstance(base.value, ast.Name) and base.value.id == "st"):
        return name, True
    return None


class _Scope(ast.NodeVisitor):
    """Walks a module tracking the enclosing function and `with st.expander(...)`."""

    def __init__(self, path, consts):
        self.path = path
        self.consts = consts
        self.fn = []
        self.panel = []
        self.records = []

    # -- scope tracking ----------------------------------------------------
    def visit_FunctionDef(self, node):
        self.fn.append(node.name)
        self.generic_visit(node)
        self.fn.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_With(self, node):
        opened = 0
        for item in node.items:
            ce = item.context_expr
            if isinstance(ce, ast.Call):
                w = _st_widget(ce)
                if w and w[0] in ("expander", "popover", "form", "dialog"):
                    lbl = _label_of(ce, w[0]) or w[0]
                    self.panel.append(lbl)
                    opened += 1
        self.generic_visit(node)
        for _ in range(opened):
            self.panel.pop()

    visit_AsyncWith = visit_With

    # -- the widgets themselves -------------------------------------------
    def visit_Call(self, node):
        w = _st_widget(node)
        if w:
            self.records.append(self._record(node, *w))
        self.generic_visit(node)

    def _record(self, node, widget, in_sidebar):
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        rec = {
            "file": self.path,
            "line": node.lineno,
            "widget": widget,
            "kind": "container" if widget in CONTAINERS else "control",
            "label": _label_of(node, widget),
            "key": _key_pattern(kw.get("key")),
            "function": self.fn[-1] if self.fn else "<module>",
            "panel": " / ".join(self.panel) if self.panel else None,
            "sidebar": in_sidebar,
            "help": _const_str(kw.get("help")),
            "default": _src(kw.get("value")) if "value" in kw else None,
            "min": _src(kw.get("min_value")) if "min_value" in kw else None,
            "max": _src(kw.get("max_value")) if "max_value" in kw else None,
            "step": _src(kw.get("step")) if "step" in kw else None,
            "format": _const_str(kw.get("format")),
            "disabled": _src(kw.get("disabled")) if "disabled" in kw else None,
        }
        opt_node = kw.get("options")
        if opt_node is None and widget in ("radio", "selectbox", "multiselect",
                                           "select_slider", "tabs") and len(node.args) > 1:
            opt_node = node.args[1]
        if widget == "tabs" and node.args:
            opt_node = node.args[0]
        if opt_node is not None:
            rec["options"] = _string_list(opt_node, self.consts) or _src(opt_node)
        if rec["help"] is None and kw.get("help") is not None:
            rec["help"] = "<computed>"
        return {k: v for k, v in rec.items() if v is not None}


def scan_file(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    v = _Scope(path.name, _module_constants(tree))
    v.visit(tree)
    return v.records


def build(root: Path, files=UI_FILES):
    records, missing = [], []
    for name in files:
        p = root / name
        if not p.exists():
            missing.append(name)
            continue
        records.extend(scan_file(p))
    records.sort(key=lambda r: (r["file"], r["line"]))
    return records, missing


_STYLE = re.compile(r"<style\b", re.I)


def style_digest(root: Path, files=UI_FILES) -> str:
    """Hash of every inline <style> block in the UI sources.

    Layout is not a control, so a CSS-only edit leaves the control inventory
    byte-identical while silently restyling every screenshot. Hashing the
    style blocks gives doc_drift something to notice.
    """
    h = hashlib.sha256()
    for name in sorted(files):
        p = root / name
        if not p.exists():
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and _STYLE.search(node.value)):
                h.update(name.encode())
                h.update(node.value.encode("utf-8"))
    return h.hexdigest()[:16]


def _version(root: Path) -> str:
    vf = root / "_version.py"
    if not vf.exists():
        return "unknown"
    try:
        tree = ast.parse(vf.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "__version__":
                        return _const_str(node.value) or "unknown"
    except Exception:
        pass
    return "unknown"


def as_markdown(records):
    """Reference-table rows: controls only, grouped by file then panel."""
    out, group = [], None
    for r in records:
        if r["kind"] != "control":
            continue
        g = (r["file"], r.get("panel") or "—")
        if g != group:
            group = g
            out.append(f"\n**{r['file']}** · {r.get('panel') or 'top level'}\n")
            out.append("| Control | Key | Default | Range | What it does |")
            out.append("|---|---|---|---|---|")
        rng = ""
        if r.get("min") or r.get("max"):
            rng = f"{r.get('min', '')} … {r.get('max', '')}".strip(" …")
        elif isinstance(r.get("options"), list):
            rng = ", ".join(r["options"])
        helptxt = (r.get("help") or "").replace("\n", " ").strip()
        out.append(f"| {r.get('label') or '—'} | `{r.get('key') or '—'}` | "
                   f"{r.get('default') or '—'} | {rng or '—'} | {helptxt or 'TODO'} |")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--root", default=".", help="app source folder (holds app.py)")
    ap.add_argument("--out", default="docs/manual/ui_inventory.json")
    ap.add_argument("--print", dest="show", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    a = ap.parse_args(argv)

    root = Path(a.root).resolve()
    records, missing = build(root)
    for m in missing:
        print(f"  ! listed UI file not found: {m}", file=sys.stderr)

    if a.markdown:
        print(as_markdown(records))
        return 0
    if a.show:
        for r in records:
            if r["kind"] != "control":
                continue
            print(f"{r['file']:>18}:{r['line']:<5} {r['widget']:<15} "
                  f"{(r.get('key') or '-'):<24} {r.get('label') or '-'}")
        print(f"\n{sum(1 for r in records if r['kind'] == 'control')} controls, "
              f"{sum(1 for r in records if r['kind'] == 'container')} containers")
        return 0

    payload = {
        "app_version": _version(root),
        "files": [f for f in UI_FILES if f not in missing],
        "style_hash": style_digest(root),
        "controls": records,
    }
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    n = sum(1 for r in records if r["kind"] == "control")
    print(f"{out}: {n} controls from {len(payload['files'])} files "
          f"(app v{payload['app_version']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
