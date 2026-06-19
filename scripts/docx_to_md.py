"""Convert .docx specs in docs/specs/ to markdown.

Usage:
    python scripts/docx_to_md.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import docx
from docx.document import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph


def _iter_block_items(parent):
    """Yield paragraphs and tables in document order."""
    if isinstance(parent, Document):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:
        raise ValueError(parent)
    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def _para_to_md(p: Paragraph) -> str:
    text = "".join(_run_to_md(r) for r in p.runs).rstrip()
    if not text:
        return ""
    style = ((p.style.name if p.style else "") or "").lower()
    if style.startswith("heading"):
        try:
            level = int(style.split()[-1])
        except ValueError:
            level = 1
        return f"{'#' * min(level, 6)} {text}"
    if style == "title":
        return f"# {text}"
    if "list" in style or "bullet" in style:
        return f"- {text}"
    return text


def _run_to_md(r) -> str:
    t = r.text or ""
    if not t:
        return ""
    if r.bold and r.italic:
        return f"***{t}***"
    if r.bold:
        return f"**{t}**"
    if r.italic:
        return f"*{t}*"
    return t


def _table_to_md(tbl: Table) -> str:
    rows = []
    for row in tbl.rows:
        cells = [(" ".join(p.text for p in c.paragraphs)).replace("|", "\\|").strip()
                 for c in row.cells]
        rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    header = rows[0]
    sep = "| " + " | ".join("---" for _ in tbl.rows[0].cells) + " |"
    return "\n".join([header, sep, *rows[1:]])


def convert(path: Path) -> str:
    d = docx.Document(str(path))
    out: list[str] = []
    for block in _iter_block_items(d):
        if isinstance(block, Paragraph):
            line = _para_to_md(block)
            if line:
                out.append(line)
                out.append("")
        elif isinstance(block, Table):
            out.append(_table_to_md(block))
            out.append("")
    # collapse 3+ blank lines to 2
    md = "\n".join(out)
    while "\n\n\n" in md:
        md = md.replace("\n\n\n", "\n\n")
    return md.strip() + "\n"


def main() -> int:
    specs_dir = Path(__file__).resolve().parent.parent / "docs" / "specs"
    docx_files = sorted(specs_dir.glob("*.docx"))
    if not docx_files:
        print(f"No .docx files in {specs_dir}")
        return 1
    for src in docx_files:
        dst = src.with_suffix(".md")
        print(f"  {src.name} -> {dst.name}")
        dst.write_text(convert(src), encoding="utf-8")
    print(f"Converted {len(docx_files)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
