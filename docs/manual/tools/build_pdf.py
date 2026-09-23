# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Viacheslav Manin
"""
build_pdf.py — Markdown sources -> printable A4 PDFs.

Prints through Playwright's Chromium, so the documentation toolchain needs
no LaTeX, no wkhtmltopdf and no GTK/cairo DLLs — the three things that
usually make a Windows docs build a project of its own.

    python -m pip install -r docs/manual/requirements-docs.txt
    python -m playwright install chromium

    python docs/manual/tools/build_pdf.py            # build both documents
    python docs/manual/tools/build_pdf.py docs/manual/quick_start.md
    python docs/manual/tools/build_pdf.py --html     # keep the intermediate HTML

Markdown conventions this understands:

  ---                              front matter: title/subtitle/audience
  title: Quick Start               (plain key: value lines, no YAML needed)
  ---

  ![A caption sentence.](img/03-magnitude.png)
      An image alone in a paragraph becomes a numbered <figure> with the alt
      text as its caption. Figure numbers are assigned in document order.

  <div class="page-break"></div>   force a page break
  > [!note] text                   a callout box
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_inventory as INV                                    # noqa: E402

DEFAULT_DOCS = [
    ("docs/manual/quick_start.md", "docs/Quick_Start.pdf"),
    ("docs/manual/user_manual.md", "docs/User_Manual.pdf"),
]


def app_version(root: Path) -> str:
    vf = root / "_version.py"
    if not vf.exists():
        return ""
    try:
        for node in ast.parse(vf.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "__version__":
                        if isinstance(node.value, ast.Constant):
                            return str(node.value.value)
    except Exception:
        pass
    return ""


def split_front_matter(text: str):
    meta = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"\'')
            text = text[end + 4:]
    return meta, text.lstrip("\n")


IMG_PARA = re.compile(r"<p>\s*((?:<img [^>]*>\s*)+)</p>", re.I)
IMG_TAG = re.compile(r"<img [^>]*>", re.I)
IMG_ALT = re.compile(r'alt="([^"]*)"', re.I)
IMG_CLASS = re.compile(r'class="([^"]*)"', re.I)
CALLOUT = re.compile(r"<blockquote>\s*<p>\s*\[!(?P<kind>[a-zA-Z]+)\]\s*", re.I)


# ONE scale for every figure, so UI text is the same size in all of them.
# Screenshots are taken in Chrome's device mode at 1440 x 900, pixel ratio 2
# (see SCREENSHOTS.md). A full-width crop of the main column is then ~1004
# CSS px and maps onto the 174 mm A4 text column; everything else keeps that same mm-per-pixel, and
# only a crop wider than the column is shrunk. Without this a small crop
# (two buttons) is blown up to page width while a wide one shrinks — a
# different font size in every figure.
MM_PER_CSS_PX = 174.0 / 1004.0
CAPTURE_DPR = 2          # the device pixel ratio the screenshots were taken at
IMG_SRC = re.compile(r'src="([^"]+)"', re.I)


def _png_width(path: Path):
    """Pixel width from the PNG header — no imaging library needed."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            return int.from_bytes(head[16:20], "big")
    except OSError:
        pass
    return None


def natural_widths(html: str, base: Path) -> str:
    """Give each figure image its uniform-scale print width.

    An explicit size class ({.narrow}, {.half}, {.tall}) still wins: the
    image keeps no inline width and the class sizes its frame instead.
    """
    def sub(m):
        img = m.group(0)
        if IMG_CLASS.search(img):
            return img
        src = IMG_SRC.search(img)
        w = _png_width(base / src.group(1)) if src else None
        if not w:
            return img
        mm = w / CAPTURE_DPR * MM_PER_CSS_PX
        return img.replace("<img ", f'<img style="width:{mm:.1f}mm" ', 1)
    return IMG_TAG.sub(sub, html)


def numbered_figures(html: str) -> str:
    """A paragraph holding only images becomes one numbered figure.

    One image  -> a single framed figure.
    Several    -> a ROW: side-by-side frames under one caption and one figure
                  number — how a long panel (the sidebar) prints at a readable
                  size: split into two crops rather than shrunk to 4 pt.
                  The caption is the FIRST image's alt text.

    A size class on an image ({.narrow}, {.half}, {.tall}) moves onto its
    frame, so the frame hugs the picture instead of stretching to the column.
    One regex, one pass, so figure numbers always follow document order.
    """
    n = [0]

    def frame(img):
        cls = IMG_CLASS.search(img)
        return f'<div class="shot{" " + cls.group(1) if cls else ""}">{img}</div>'

    def sub(m):
        imgs = IMG_TAG.findall(m.group(1))
        n[0] += 1
        alt = IMG_ALT.search(imgs[0])
        row = " row" if len(imgs) > 1 else ""
        return (f'<figure class="fig{row}">' + "".join(frame(i) for i in imgs)
                + f'<figcaption><b>Figure {n[0]}.</b> {alt.group(1) if alt else ""}'
                  "</figcaption></figure>")
    return IMG_PARA.sub(sub, html)


def callouts(html: str) -> str:
    return CALLOUT.sub(
        lambda m: f'<blockquote class="callout {m.group("kind").lower()}"><p>', html)


def render(md_path: Path, root: Path, version: str) -> tuple[str, dict]:
    try:
        import markdown
    except ImportError:
        raise SystemExit("pip install markdown")

    raw = md_path.read_text(encoding="utf-8")
    meta, body = split_front_matter(raw)
    html = markdown.markdown(
        body,
        extensions=["tables", "fenced_code", "attr_list", "sane_lists",
                    "md_in_html", "toc", "footnotes"],
        extension_configs={"toc": {"permalink": False}},
    )
    html = natural_widths(html, md_path.parent)
    html = numbered_figures(html)
    html = callouts(html)

    css = (md_path.parent / "style.css")
    style = css.read_text(encoding="utf-8") if css.exists() else ""
    title = meta.get("title", md_path.stem.replace("_", " ").title())
    sub = meta.get("subtitle", "")
    app = meta.get("app", "FilterSynthesizer")
    stamp = meta.get("date") or dt.date.today().isoformat()

    cover = f"""
    <section class="cover">
      <div class="cover-kicker">{app}{' &nbsp;v' + version if version else ''}</div>
      <h1 class="cover-title">{title}</h1>
      {'<p class="cover-sub">' + sub + '</p>' if sub else ''}
      <p class="cover-meta">{stamp}
      {'<br>Screenshots captured at v' + version if version else ''}</p>
    </section>
    """ if meta.get("cover", "yes").lower() not in ("no", "false") else ""

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>{style}</style></head>
<body>{cover}<main>{html}</main></body></html>"""
    meta.setdefault("title", title)
    return doc, meta


def to_pdf(html: str, html_path: Path, pdf_path: Path, meta: dict, version: str):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("pip install playwright && playwright install chromium")

    html_path.write_text(html, encoding="utf-8")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    foot = (
        '<div style="width:100%;font:8pt Segoe UI,Arial;color:#777;'
        'padding:0 14mm;display:flex;justify-content:space-between;">'
        f'<span>{meta.get("title", "")}'
        f'{" &middot; v" + version if version else ""}</span>'
        '<span class="pageNumber"></span></div>')
    head = '<div></div>'
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page()
        pg.goto(html_path.as_uri(), wait_until="load")
        pg.emulate_media(media="print")
        pg.pdf(path=str(pdf_path), format="A4", print_background=True,
               display_header_footer=True, header_template=head,
               footer_template=foot,
               margin={"top": "16mm", "bottom": "16mm",
                       "left": "18mm", "right": "18mm"})
        b.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("sources", nargs="*", help="markdown files (default: both docs)")
    ap.add_argument("--root", default=None,
                    help="app source folder (holds app.py); auto-detected by default")
    ap.add_argument("-o", "--out", help="output PDF (only with a single source)")
    ap.add_argument("--html", action="store_true", help="keep the intermediate HTML")
    a = ap.parse_args(argv)

    root = Path(a.root).resolve() if a.root else INV.find_root()
    version = app_version(root)

    if a.sources:
        jobs = [(s, a.out or str(Path("docs") / (Path(s).stem.title().replace("_", "_") + ".pdf")))
                for s in a.sources]
    else:
        jobs = DEFAULT_DOCS

    rc = 0
    for src, dst in jobs:
        p = root / src
        if not p.exists():
            print(f"  ! missing source: {src}", file=sys.stderr)
            rc = 1
            continue
        html, meta = render(p, root, version)
        tmp = p.parent / f"_build_{p.stem}.html"
        out = root / dst
        to_pdf(html, tmp, out, meta, version)
        missing = [m for m in re.findall(r'src="([^"]+)"', html)
                   if not (p.parent / m).exists()]
        for m in sorted(set(missing)):
            print(f"    ! figure not found: {m}  (take it — see SCREENSHOTS.md)")
        if not a.html:
            tmp.unlink(missing_ok=True)
        print(f"  {src}  ->  {dst}   ({out.stat().st_size/1024:.0f} kB)")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
