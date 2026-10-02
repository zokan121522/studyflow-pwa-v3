"""Markdown → PDF for the "MD → PDF" button.

Two deliberate choices, both about not having two truths:

1. The markdown is rendered by the project's OWN parser
   (``frontend/features/studyflow/content-blocks.js``), executed under Node,
   not by a second markdown implementation written here. The parser escapes
   HTML first and only then applies the patterns it recognises, which is what
   makes it safe to feed its output to WeasyPrint. A Python reimplementation
   would drift from the screen within weeks, and the PDF would quietly
   disagree with what the user just read.

2. WeasyPrint does the typesetting. It is in requirements.txt because text
   quality matters more here than saving a dependency: this renders headings,
   tables, code blocks and page numbers properly.

The renderer is invoked as a subprocess with a fixed argv (no shell), and it
only ever sees markdown the caller already sent over the API.
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from pathlib import Path

from weasyprint import HTML

# ── Paths ────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]          # repo root
_PARSER_JS = _REPO_ROOT / "frontend" / "features" / "studyflow" / "content-blocks.js"

# Node harness: minimal window shim, load the real parser, render, print JSON.
# Built by concatenation rather than f-string because the harness contains JS
# object literals whose braces would be read as format placeholders.
_RENDER_HARNESS = """
global.window = { App: {} };
// The module prints a load banner on stdout for the browser console. Under
// Node that banner would be captured as part of the rendered HTML and end up
// as the first line of the user's PDF, so console.log is muted for the load
// and restored afterwards — we only want _renderMd's return value on stdout.
const realLog = console.log;
console.log = () => {};
const fs = require('fs');
eval(fs.readFileSync(__JS__, 'utf8'));
const md = fs.readFileSync(process.argv[1], 'utf8');
const html = window.App.ContentBlocks._renderMd(md);
console.log = realLog;
process.stdout.write(html);
"""


class MdRenderError(RuntimeError):
    """The markdown could not be turned into HTML."""


def _sanitize_filename(name: str, max_len: int = 120) -> str:
    """Turn a block title into a filename stem that survives every filesystem.

    Strips accents (so the name is readable anywhere), drops path separators and
    control characters, and collapses the rest to single dashes. Returns
    ``documento`` if nothing usable is left, so we never produce ``.pdf``.
    """
    name = unicodedata.normalize("NFKD", name or "")
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^\w\s\-.]", " ", name, flags=re.UNICODE)
    name = re.sub(r"[\s_]+", "-", name).strip("-.")
    name = name[:max_len].strip("-.")
    return name or "documento"


def render_markdown_to_html(markdown: str) -> str:
    """Render markdown with the project's own parser. Returns an HTML fragment."""
    if not _PARSER_JS.is_file():
        raise MdRenderError(f"parser not found at {_PARSER_JS}")

    tmp_dir = Path("/tmp/md2pdf")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    md_file = tmp_dir / "input.md"
    md_file.write_text(markdown or "", encoding="utf-8")

    script = _RENDER_HARNESS.replace("__JS__", json.dumps(str(_PARSER_JS)))
    try:
        proc = subprocess.run(
            ["node", "-e", script, str(md_file)],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MdRenderError("markdown rendering timed out") from exc
    finally:
        md_file.unlink(missing_ok=True)

    if proc.returncode != 0:
        raise MdRenderError(f"markdown renderer failed: {proc.stderr[:400]}")
    return proc.stdout.strip()


# Stylesheet for the PDF. WeasyPrint supports paged media: the @page rule with
# a running footer is what puts the page number at the bottom of every page.
_CSS = """
@page {
  size: A4;
  margin: 20mm 18mm 18mm 18mm;
  @bottom-center {
    content: counter(page) " / " counter(pages);
    font-family: sans-serif; font-size: 9pt; color: #888;
  }
}
body { font-family: "DejaVu Sans", sans-serif; font-size: 10.5pt;
       line-height: 1.5; color: #1a1a1a; }
h1, h2, h3, h4 { color: #111; margin: 1.1em 0 .45em; line-height: 1.25;
                page-break-after: avoid; }
h1 { font-size: 1.7em; border-bottom: 2px solid #e5e5e5; padding-bottom: .25em; }
h2 { font-size: 1.4em; }
h3 { font-size: 1.15em; }
p { margin: .55em 0; }
ul, ol { margin: .5em 0 .5em 1.4em; padding: 0; }
li { margin: .22em 0; }
pre { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 4px;
      padding: 9px 11px; font-size: 8.8pt; white-space: pre-wrap;
      word-wrap: break-word; page-break-inside: avoid; }
code { font-family: "DejaVu Sans Mono", monospace; font-size: .92em;
       background: #f0f2f4; padding: 1px 4px; border-radius: 3px; }
pre code { background: none; padding: 0; font-size: 1em; }
blockquote { margin: .6em 0; padding: .3em .9em; border-left: 3px solid #ccc;
             color: #555; background: #fafafa; }
table { border-collapse: collapse; width: 100%; margin: .8em 0;
        font-size: 9.5pt; page-break-inside: avoid; }
th, td { border: 1px solid #d8dce0; padding: 6px 8px; text-align: left;
         vertical-align: top; }
th { background: #f0f2f4; font-weight: 600; }
tr:nth-child(even) td { background: #fafbfc; }
img { max-width: 100%; height: auto; }
hr { border: none; border-top: 1px solid #ddd; margin: 1.2em 0; }
a { color: #1155cc; text-decoration: none; }
"""


def markdown_to_pdf(
    markdown: str,
    title: str = "",
    output_path: Path | None = None,
) -> tuple[Path, int]:
    """Render markdown to a PDF file.

    Returns ``(path, page_count)``. Raises :class:`MdRenderError` when the
    markdown cannot be rendered, so the caller can answer with a 4xx instead of
    a 500 and the user learns what went wrong.
    """
    fragment = render_markdown_to_html(markdown)
    if not fragment.strip():
        raise MdRenderError("the note is empty")

    stem = _sanitize_filename(title)
    out = output_path or (Path("/tmp/md2pdf") / f"{stem}.pdf")
    out.parent.mkdir(parents=True, exist_ok=True)

    html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{stem}</title><style>{_CSS}</style></head>"
        f"<body>{fragment}</body></html>"
    )

    # raise_for_errors=False: a malformed image link should not lose the whole
    # document. WeasyPrint reports what it skipped and WeasyPrint warns.
    document = HTML(string=html).render(fail_if_major_issues=False)
    document.write_pdf(str(out))

    try:
        # pymupdf is the current name; the old `fitz` alias warns on import.
        import pymupdf

        with pymupdf.open(str(out)) as pdf:
            pages = pdf.page_count
    except Exception:
        pages = 0

    return out, pages