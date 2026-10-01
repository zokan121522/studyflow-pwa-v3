"""Tests for the MD → PDF service.

Two things are worth pinning down here:

* the filename sanitizer, because it is the only thing standing between a
  block title and the filesystem (a title of ``../../etc/passwd`` must not
  escape the upload folder);
* that the PDF really is produced and carries the note's text, since a silent
  failure would look identical to success from the user's side.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from services.md_to_pdf import (  # noqa: E402
    MdRenderError,
    _sanitize_filename,
    markdown_to_pdf,
    render_markdown_to_html,
)


# ── _sanitize_filename ───────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Informe Comparativo", "Informe-Comparativo"),
        ("Informe  (RA1)", "Informe-RA1"),
        # Path traversal must not survive: separators and dots are dropped.
        ("../../etc/passwd", "etc-passwd"),
        ("..", "documento"),
        (".", "documento"),
        # Accents are stripped so the name reads the same everywhere.
        ("Añadir Teams", "Anadir-Teams"),
        # Emoji are not word characters, so they become separators.
        ("Añadir 🔗 Teams", "Anadir-Teams"),
        ("", "documento"),
        ("   ", "documento"),
        (None, "documento"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert _sanitize_filename(raw) == expected


def test_sanitize_filename_never_returns_a_dot_only_name():
    """A stem of only dots would produce a hidden '.pdf' with no name."""
    for raw in ("...", "..", ". . .", "/", "///"):
        stem = _sanitize_filename(raw)
        assert stem.strip("-.") != "", f"{raw!r} produced an empty stem"


def test_sanitize_filename_is_bounded():
    assert len(_sanitize_filename("a" * 500)) <= 120


# ── render_markdown_to_html ───────────────────────────────────────

def test_render_uses_the_project_parser_and_escapes_html():
    """The output must come from content-blocks.js, not a second parser.

    Asserting on ``<table>`` is deliberate: only the project parser emits
    tables, so this fails if someone swaps in a Python markdown library that
    renders the note differently from the screen.
    """
    html = render_markdown_to_html("| A | B |\n| --- | --- |\n| 1 | 2 |")
    assert "<table" in html, f"the project parser renders tables: {html!r}"


def test_render_keeps_user_newlines_as_line_breaks():
    """Same fix as the on-screen preview: a newline is a line to keep."""
    html = render_markdown_to_html("primera\nsegunda")
    assert "<br>" in html, f"single newlines must survive: {html!r}"


def test_render_escapes_html_in_the_note():
    """The parser escapes first, so a <script> in a note stays inert text."""
    html = render_markdown_to_html("<script>alert(1)</script>")
    assert "<script>" not in html, f"script tag leaked into the PDF: {html!r}"


# ── markdown_to_pdf ──────────────────────────────────────────────

def test_markdown_to_pdf_writes_a_readable_pdf(tmp_path):
    md = (
        "# Informe\n\n"
        "## Requisitos\n\n"
        "- [ ] uno\n"
        "- [x] dos\n\n"
        "| Criterio | Valor |\n"
        "| --- | --- |\n"
        "| Rendimiento | Alto |\n"
    )
    out = tmp_path / "informe.pdf"
    path, pages = markdown_to_pdf(md, title="Informe", output_path=out)

    assert path.is_file()
    assert path.stat().st_size > 1000, "a 1-page PDF is never this small"
    assert pages >= 1

    pymupdf = pytest.importorskip("pymupdf")
    with pymupdf.open(str(path)) as doc:
        text = doc[0].get_text()
    assert "Informe" in text
    assert "Rendimiento" in text, "the table must be typeset, not dropped"


def test_markdown_to_pdf_output_is_not_the_browser_console(tmp_path):
    """The parser logs a load banner; it must not become page content.

    A regression here is easy to miss: the PDF still opens and every heading
    is present, it just starts with a stray line of debug text.
    """
    pymupdf = pytest.importorskip("pymupdf")
    out = tmp_path / "banner.pdf"
    path, _ = markdown_to_pdf("# Solo un titulo\n", title="Banner", output_path=out)
    with pymupdf.open(str(path)) as doc:
        text = doc[0].get_text()
    assert "content-blocks.js loaded" not in text
    assert "Solo un titulo" in text


def test_markdown_to_pdf_rejects_an_empty_note(tmp_path):
    """A blank note must be a clean error, not a 500 and a broken file."""
    with pytest.raises(MdRenderError):
        markdown_to_pdf("   \n\n  ", title="Vacio", output_path=tmp_path / "x.pdf")