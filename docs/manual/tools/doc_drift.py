# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin
"""
doc_drift.py — tells you which pages and screenshots the last UI change broke.

Compares three things:

  1. the LIVE UI, re-extracted from the source right now (ui_inventory.py)
  2. the SNAPSHOT the manual was written against (docs/manual/ui_inventory.json)
  3. the DOCS themselves — quick_start.md, user_manual.md, SCREENSHOTS.md

and reports, per control:

  REMOVED   documented but no longer in the source   -> delete those paragraphs
  ADDED     in the source but documented nowhere     -> write them up
  CHANGED   label / default / range / help moved     -> re-read that paragraph
  STALE FIG a figure shows a control that changed    -> retake that screenshot

    python docs/manual/tools/doc_drift.py                 # report; exit 1 if anything drifted
    python docs/manual/tools/doc_drift.py --accept        # adopt the live UI as the new snapshot

A control counts as "documented" when its session-state key appears inside
backticks in one of the markdown sources — `hw_topk`. That is the whole
convention: every control gets its key printed once in the User Manual's
reference table, and this script keeps that table honest.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_inventory as INV                                    # noqa: E402

DOCS = ["docs/manual/quick_start.md", "docs/manual/user_manual.md"]
SNAPSHOT = "docs/manual/ui_inventory.json"
SHOTS = "docs/manual/SCREENSHOTS.md"

# Fields whose change means a human must re-read the prose around the control.
WATCH = ("label", "default", "min", "max", "options", "help", "panel", "widget")

BACKTICKED = re.compile(r"`([A-Za-z_][A-Za-z0-9_*]*)`")


def _ident(rec):
    """Stable identity of a control across edits."""
    return rec.get("key") or f"{rec['file']}:{rec.get('label') or rec['widget']}"


def _controls(records):
    out = {}
    for r in records:
        if r.get("kind") != "control":
            continue
        out.setdefault(_ident(r), r)
    return out


def _documented(root: Path):
    """key -> [files it is mentioned in]."""
    found = {}
    for rel in DOCS:
        p = root / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for tok in set(BACKTICKED.findall(text)):
            found.setdefault(tok, []).append(rel)
    return found


_FIG_HEAD = re.compile(r"^#{2,4}\s+(\d\d[a-z]?-[A-Za-z0-9-]+)\.png\b")
_FIG_CTL = re.compile(r"^\W*Controls\W", re.I)
_TICKED = re.compile(r"`([^`]+)`")


def _figures(root: Path):
    """figure id -> [controls it shows], parsed from SCREENSHOTS.md.

    Each figure there is a heading naming its file, and a line starting
    "Controls:" with the controls in backticks:

        ### 08-convergence.png
        Controls: `hw_effort` `hw_pole_tol_pct` `hw_gain_tol_pct` `hw_topk`
    """
    p = root / SHOTS
    if not p.exists():
        return {}
    out, cur = {}, None
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _FIG_HEAD.match(line.strip())
        if m:
            cur = m.group(1)
            out.setdefault(cur, [])
        elif cur and _FIG_CTL.match(line.strip()):
            out[cur] += [t for t in _TICKED.findall(line) if t.lower() != "none"]
    return out


def report(root: Path, accept=False):
    live_records, missing = INV.build(root)
    live = _controls(live_records)

    snap_path = root / SNAPSHOT
    if not snap_path.exists():
        print(f"No snapshot at {SNAPSHOT} — run: python tools/ui_inventory.py")
        return 2
    snap_payload = json.loads(snap_path.read_text(encoding="utf-8"))
    snap = _controls(snap_payload.get("controls", []))

    documented = _documented(root)
    figures = _figures(root)

    removed = [k for k in snap if k not in live]
    added = [k for k in live if k not in snap]
    changed = {}
    for k in live:
        if k not in snap:
            continue
        diffs = {f: (snap[k].get(f), live[k].get(f))
                 for f in WATCH if snap[k].get(f) != live[k].get(f)}
        if diffs:
            changed[k] = diffs

    undocumented = [k for k in live if k not in documented and live[k].get("key")]
    ghost = [k for k in documented if k in snap and k not in live]
    touched = set(removed) | set(changed) | set(added)
    stale_figs = {fid: [c for c in ctl if c in touched]
                  for fid, ctl in figures.items()
                  if any(c in touched for c in ctl)}

    w = sys.stdout.write
    for m in missing:
        w(f"  ! listed UI file not found: {m}\n")

    w(f"\nApp version   : {INV._version(root)}   "
      f"(snapshot was written at v{snap_payload.get('app_version', '?')})\n")
    w(f"Live controls : {len(live)}   documented: "
      f"{len(live) - len(undocumented)}   undocumented: {len(undocumented)}\n")

    if removed:
        w(f"\nREMOVED — {len(removed)} control(s) gone from the source:\n")
        for k in sorted(removed):
            where = documented.get(k)
            src = snap[k]
            w(f"  - `{k}`  \"{src.get('label')}\"  ({src['file']})\n")
            w(f"      documented in: {', '.join(where) if where else 'nowhere (nothing to do)'}\n")
            figs = [f for f, c in figures.items() if k in c]
            if figs:
                w(f"      shown in figures: {', '.join(figs)} — retake it\n")

    if added:
        w(f"\nADDED — {len(added)} new control(s):\n")
        for k in sorted(added):
            r = live[k]
            w(f"  + `{k}`  \"{r.get('label')}\"  ({r['file']}:{r['line']}"
              f"{', panel: ' + r['panel'] if r.get('panel') else ''})\n")
            if k not in documented:
                w("      not documented yet\n")

    if changed:
        w(f"\nCHANGED — {len(changed)} control(s):\n")
        for k in sorted(changed):
            w(f"  ~ `{k}`  ({live[k]['file']}:{live[k]['line']})\n")
            for f, (was, now) in changed[k].items():
                w(f"      {f}: {was!r}\n            -> {now!r}\n")
            where = documented.get(k)
            if where:
                w(f"      re-read: {', '.join(where)}\n")

    if stale_figs:
        w(f"\nSTALE FIGURES — {len(stale_figs)} need retaking:\n")
        for fid, ctl in sorted(stale_figs.items()):
            w(f"  * {fid}  (shows {', '.join('`%s`' % c for c in ctl)})\n")
            w("      retake it by hand — see SCREENSHOTS.md\n")

    if undocumented:
        w(f"\nUNDOCUMENTED — {len(undocumented)} control(s) with no key in the docs:\n")
        for k in sorted(undocumented):
            r = live[k]
            w(f"  ? `{k}`  \"{r.get('label')}\"  ({r['file']}:{r['line']})\n")

    if ghost:
        w("\nGHOSTS — documented keys that no longer exist (delete the prose):\n")
        for k in sorted(ghost):
            w(f"  x `{k}`  in {', '.join(documented[k])}\n")

    # Figures cost a solve each to capture: one that no document shows is
    # wasted time, and one a document wants but the manifest lacks never
    # appears in the PDF.
    shown, wanted_imgs = set(), set()
    for rel in DOCS:
        p = root / rel
        if p.exists():
            shown |= set(re.findall(r"img/([\w.-]+)\.png", p.read_text(encoding="utf-8")))
    wanted_imgs = set(figures)
    orphan = sorted(wanted_imgs - shown)
    unlisted = sorted(shown - wanted_imgs)
    if orphan:
        w("\nORPHAN FIGURES — captured but shown in no document:\n")
        for f in orphan:
            w(f"  o {f}  (use it, or drop it from SCREENSHOTS.md)\n")
    if unlisted:
        w("\nUNLISTED FIGURES — referenced by a document but not in SCREENSHOTS.md:\n")
        for f in unlisted:
            w(f"  ! {f}  (add it to SCREENSHOTS.md)\n")

    # A CSS-only edit changes no control, so nothing above would fire — but it
    # restyles every screenshot. The style hash is the only thing that notices.
    live_style = INV.style_digest(root)
    snap_style = snap_payload.get("style_hash")
    restyled = bool(snap_style) and snap_style != live_style
    if restyled:
        w("\nAPP CSS CHANGED — no control moved, but the layout did:\n")
        w(f"  style_hash {snap_style} -> {live_style}\n")
        w("  Layout changed — check every screenshot against the app\n")
    elif snap_style is None:
        w("\n  (snapshot predates style hashing — run --accept to start tracking CSS)\n")

    drifted = bool(removed or added or changed or restyled)
    if not drifted and not undocumented:
        w("\nNo drift. Docs match the UI.\n")

    if accept and drifted:
        snap_payload = {"app_version": INV._version(root),
                        "files": [f for f in INV.UI_FILES if f not in missing],
                        "style_hash": live_style,
                        "controls": live_records}
        snap_path.write_text(json.dumps(snap_payload, indent=1, ensure_ascii=False),
                             encoding="utf-8")
        w(f"\nSnapshot updated -> {SNAPSHOT}. Commit it with the doc edits.\n")
        return 0

    return 1 if drifted else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--root", default=None,
                    help="app source folder (holds app.py); auto-detected by default")
    ap.add_argument("--accept", action="store_true",
                    help="adopt the live UI as the new snapshot (do this WITH the doc edits)")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve() if a.root else INV.find_root()
    return report(root, accept=a.accept)


if __name__ == "__main__":
    raise SystemExit(main())
