"""OpenZen PDF chunking: the seams that the NotebookLM path does not have.

NotebookLM hands the whole PDF over in one call, so "Extensa" could never
actually add detail and a failure lost everything. The OpenZen path splits the
PDF per section so a section can be retried or regenerated on its own.

These tests exercise the real PDF splitting against real PDFs (built on the
fly with PyMuPDF) — no OpenZen sidecar needed, because the splitting is the
part that must not be wrong.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import ai.generation.openzen_source as openzen_source  # noqa: E402

from ai.generation.openzen_source import (  # noqa: E402
    MAX_SECTION_CHARS,
    _pdf_pages_text,
    _insert_chunk,
    _markdown_sections,
    _sections_for,
    _section_prompt,
    _system_message,
    _assemble,
    _extract_sections,
    _sections_from_headings,
    _sections_from_toc,
    _split_windows,
    CHUNK_MARKER,
)

fitz = pytest.importorskip("fitz")


def _make_pdf(tmp_path, pages):
    """Build a real PDF so the tests hit the real text extractor."""
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
    path = tmp_path / "sample.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def _para(n_words=120):
    return " ".join(f"palabra{i}" for i in range(n_words))


def norm(text) -> str:
    """Whitespace-insensitive comparison: splitting re-joins without the
    separators we add back, so a byte comparison would be meaningless."""
    return "".join(str(text).split())


class TestSplitWindows:
    def test_short_text_is_one_chunk(self):
        assert _split_windows("short") == ["short"]

    def test_long_text_is_split(self):
        text = "\n\n".join(_para(80) for _ in range(40))
        parts = _split_windows(text)
        assert len(parts) > 1, "a long document must be split"
        assert all(len(p) <= MAX_SECTION_CHARS + 200 for p in parts)

    def test_nothing_is_lost(self):
        text = "\n\n".join(f"seccion{i}\n{_para(60)}" for i in range(30))
        rejoin = "".join(_split_windows(text))
        assert norm(rejoin) == norm(text), "splitting dropped content"

    def test_single_giant_paragraph_is_still_split(self):
        """No blank lines at all: one dense page. Must not be one giant call."""
        text = _para(4000)
        parts = _split_windows(text)
        assert len(parts) > 1, "an oversized single paragraph was not split"
        assert all(len(p) <= MAX_SECTION_CHARS + 50 for p in parts)
        assert norm("".join(parts)) == norm(text), "hard split dropped words"


class TestSectionsFromToc:
    def test_groups_pages_by_toc_entry(self):
        pages = [f"Contenido de la pagina {i}. {_para(40)}" for i in range(6)]
        toc = [[1, "Introduccion", 1], [1, "Metodologia", 3], [1, "Resultados", 5]]
        sections = _sections_from_toc(pages, toc)
        assert [s["title"] for s in sections] == [
            "Introduccion", "Metodologia", "Resultados"]
        # Section 1 covers pages 1-2, section 3 starts at page 5
        assert "Metodologia" in sections[1]["title"]
        assert all(s["text"].strip() for s in sections)

    def test_empty_toc_yields_nothing(self):
        assert _sections_from_toc(["texto"], []) == []


class TestSectionsFromHeadings:
    def test_finds_headings(self):
        text = "\n".join(
            line for i in range(1, 6)
            for line in ([f"## Capitulo {i}"] + [_para(60).split(" ")[0]] * 0
                         + _para(60).split(" ")[:80])
        )
        sections = _sections_from_headings(text)
        assert len(sections) >= 3, "heading-like lines should become sections"
        assert all(s["text"].strip() for s in sections)

    def test_prose_without_headings_yields_nothing(self):
        assert _sections_from_headings(" ".join(f"palabra{i}" for i in range(500))) == []


class TestExtractSections:
    def test_never_returns_empty(self, tmp_path):
        path = _make_pdf(tmp_path, [f"Pagina {i}. {_para(50)}" for i in range(4)])
        sections = _extract_sections(path)
        assert sections, "a real PDF must always yield at least one section"
        assert all(s["title"].strip() for s in sections)

    def test_real_text_survives_extraction(self, tmp_path):
        marker = "ZARAGOZAINDICADOR"
        path = _make_pdf(tmp_path, [f"Este texto contiene {marker}. {_para(30)}"])
        joined = " ".join(s["text"] for s in _extract_sections(path))
        assert marker in joined, (
            "the extracted text must be the PDF's own text — the PDF is the "
            "source of truth, so this cannot be paraphrased or generated"
        )

    def test_oversized_sections_are_resplit(self, tmp_path):
        # Enough pages that the extracted text genuinely exceeds the window.
        # (insert_text does not wrap, so the text has to span several pages.)
        pages = ["\n".join(_para(90) for _ in range(30)) for _ in range(6)]
        path = _make_pdf(tmp_path, pages)
        sections = _extract_sections(path)
        assert len(sections) > 1, "an oversized document must be split"
        assert all(len(s["text"]) <= MAX_SECTION_CHARS + 500 for s in sections)

    def test_oversized_split_keeps_all_the_text(self, tmp_path):
        pages = ["\n".join(_para(90) for _ in range(30)) for _ in range(6)]
        path = _make_pdf(tmp_path, pages)
        extracted = "".join(s["text"] for s in _extract_sections(path))
        original = "".join(_pdf_pages_text(path))
        assert norm(extracted) == norm(original), (
            "splitting the PDF lost source text — the PDF is the source of truth"
        )


class TestAssemble:
    def test_orders_by_chunk_number_not_insertion_order(self):
        out = _assemble({3: "tercera", 1: "primera", 2: "segunda"})
        assert out.index("primera") < out.index("segunda") < out.index("tercera")

    def test_marks_every_chunk_for_later_splicing(self):
        out = _assemble({1: "a", 2: "b"})
        assert CHUNK_MARKER.format(n=1) in out
        assert CHUNK_MARKER.format(n=2) in out

    def test_missing_chunks_leave_gaps_not_crashes(self):
        out = _assemble({1: "a", 3: "c"})
        assert "a" in out and "c" in out and "b" not in out


class TestGrounding:
    """The PDF is the source of truth — the prompt must carry it."""

    def test_prompt_contains_the_source_text(self):
        section = {"title": "Herencia", "text": "texto unico xyzzy del pdf"}
        prompt = _section_prompt(section, 2, 5, "Herencia")
        assert "xyzzy" in prompt, (
            "el prompt NO lleva el material fuente: el modelo inventaría el "
            "capítulo entero"
        )

    def test_prompt_says_use_only_the_source(self):
        p = _section_prompt({"title": "T", "text": "x"}, 1, 1, "T")
        low = p.lower()
        assert "only the source material" in low
        assert "do not" in low and "general knowledge" in low

    def test_auto_language_is_not_sent_literally(self):
        """'auto' must be resolved, not shipped as the word 'auto'."""
        from ai.notebooklm.md_templates import _lang_instruction

        clause = _lang_instruction("auto").lower()
        assert "same language" in clause
        # and the generator must consume that helper, not f-string the raw value
        sent = _system_message("auto", "", "length")
        assert "same language" in sent.lower()
        assert "producing auto" not in sent.lower()

    def test_es_language_is_respected(self):
        from ai.notebooklm.md_templates import _lang_instruction

        assert "spanish" in _lang_instruction("es").lower()
        assert "spanish" in _system_message("es", "", "len").lower()


class TestChunkOrdering:
    """A regenerated chunk must land in its own slot, not at the end."""

    def _doc(self, *nums):
        return _assemble({n: f"cuerpo{n}" for n in nums})

    def test_missing_chunk_is_inserted_before_higher_ones(self):
        doc = self._doc(1, 3, 4)
        out = _insert_chunk(doc, 2, "cuerpo2")
        order = [int(m.group(1)) for m in re.finditer(r"<!--chunk:(\d+)-->", out)]
        assert order == [1, 2, 3, 4], f"orden roto: {order}"
        assert "cuerpo2" in out

    def test_missing_last_chunk_is_appended(self):
        doc = self._doc(1, 2)
        out = _insert_chunk(doc, 3, "cuerpo3")
        order = [int(m.group(1)) for m in re.finditer(r"<!--chunk:(\d+)-->", out)]
        assert order == [1, 2, 3]

    def test_existing_chunk_is_replaced_in_place(self):
        doc = self._doc(1, 2, 3)
        out = _insert_chunk(doc, 2, "NUEVO2")
        order = [int(m.group(1)) for m in re.finditer(r"<!--chunk:(\d+)-->", out)]
        assert order == [1, 2, 3]
        assert "NUEVO2" in out and "cuerpo2" not in out

    def test_no_duplicate_markers_after_retry(self):
        doc = self._doc(1, 3)
        for _ in range(3):
            doc = _insert_chunk(doc, 2, "cuerpo2")
        assert doc.count("<!--chunk:2-->") == 1

    def test_insert_into_empty_document(self):
        assert "cuerpo1" in _insert_chunk("", 1, "cuerpo1")


class TestMarkdownSource:
    """A Markdown block states its own structure — read it, do not guess it."""

    MD = (
        "Estas son notas previas sobre el tema.\n"
        "Segunda linea del preambulo.\n\n"
        "# Introduccion\n"
        + ("texto de la introduccion " * 30) + "\n\n"
        "## Subtema uno\n"
        + ("detalle del subtema uno " * 30) + "\n\n"
        "## Subtema dos\n"
        + ("detalle del subtema dos " * 30) + "\n"
    )

    def test_splits_on_the_authors_headings(self):
        secs = _markdown_sections(self.MD)
        titles = [s["title"] for s in secs]
        assert "Introduccion" in titles
        assert "Subtema uno" in titles and "Subtema dos" in titles

    def test_keeps_the_preamble_before_the_first_heading(self):
        secs = _markdown_sections(self.MD)
        assert secs[0]["title"] == "Introduccion preamble" or \
               "notas previas" in secs[0]["text"]
        assert any("notas previas" in s["text"] for s in secs), \
            "el texto anterior al primer encabezado es material fuente"

    def test_no_body_text_is_lost(self):
        """Headings become the section titles, which travel in the prompt.

        So the invariant is: every heading is preserved as a title, and every
        remaining character of the source appears in some body.
        """
        secs = _markdown_sections(self.MD)
        without_headings = "\n".join(
            l for l in self.MD.splitlines() if not l.lstrip().startswith("#"))
        assert norm("".join(s["text"] for s in secs)) == norm(without_headings)
        for heading in ("Introduccion", "Subtema uno", "Subtema dos"):
            assert any(heading in s["title"] for s in secs), \
                f"el encabezado '{heading}' no se conservó como título"

    def test_markdown_without_headings_falls_back_to_windows(self):
        md = "\n\n".join(_para(200) for _ in range(20))
        secs = _markdown_sections(md)
        assert len(secs) > 1
        assert norm("".join(s["text"] for s in secs)) == norm(md)

    def test_empty_markdown_yields_nothing(self):
        assert _markdown_sections("") == []
        assert _markdown_sections("   \n  ") == []

    def test_oversized_markdown_section_is_resplit(self):
        md = "## Capitulo\n" + "\n\n".join(_para(200) for _ in range(30))
        secs = _markdown_sections(md)
        assert len(secs) > 1
        assert all(len(s["text"]) <= MAX_SECTION_CHARS + 500 for s in secs)


class TestSectionsFor:
    """_sections_for() is the single door in: PDF path or Markdown text."""

    def test_markdown_needs_no_file(self):
        secs = _sections_for("markdown", "# A\n" + _para(60))
        assert secs and secs[0]["title"] == "A"

    def test_unknown_source_type_is_not_silently_pdf(self):
        try:
            _sections_for("docx", "/tmp/no-existe")
        except RuntimeError as e:
            assert "no encontrado" in str(e)
        else:
            raise AssertionError("un tipo desconocido no debe tratarse como PDF")

    def test_missing_markdown_raises(self):
        try:
            _sections_for("markdown", "")
        except RuntimeError:
            pass
        else:
            raise AssertionError("markdown vacío debe fallar, no devolver []")


class TestValidateBlock:
    """Which sources are accepted is decided here — so it gets tested here."""

    def _patch(self, monkeypatch, row):
        monkeypatch.setattr(openzen_source, "query_one", lambda *a, **k: row)

    def test_pdf_block_yields_the_url(self, monkeypatch):
        self._patch(monkeypatch, {"id": 7, "type": "pdf", "url": "/tmp/a.pdf",
                                  "content": None})
        _, _, kind, ref = openzen_source._validate_block(7, 3, 1)
        assert kind == "pdf" and ref == "/tmp/a.pdf"

    def test_pdf_ref_block_yields_the_url(self, monkeypatch):
        self._patch(monkeypatch, {"id": 7, "type": "pdf-ref", "url": "/tmp/a.pdf"})
        _, _, kind, ref = openzen_source._validate_block(7, 3, 1)
        assert kind == "pdf" and ref == "/tmp/a.pdf"

    def test_markdown_block_yields_its_content(self, monkeypatch):
        self._patch(monkeypatch, {"id": 7, "type": "markdown", "url": None,
                                  "content": "# Hola\ntexto"})
        _, _, kind, ref = openzen_source._validate_block(7, 3, 1)
        assert kind == "markdown" and ref.startswith("# Hola")

    def test_content_block_is_markdown(self, monkeypatch):
        self._patch(monkeypatch, {"id": 7, "type": "content", "content": "texto"})
        _, _, kind, _ = openzen_source._validate_block(7, 3, 1)
        assert kind == "markdown"

    def test_empty_markdown_block_is_rejected(self, monkeypatch):
        """Silently accepting it would produce a task that fails much later."""
        self._patch(monkeypatch, {"id": 7, "type": "markdown", "content": "   "})
        with pytest.raises(ValueError, match="vacío"):
            openzen_source._validate_block(7, 3, 1)

    def test_pdf_without_url_is_rejected(self, monkeypatch):
        self._patch(monkeypatch, {"id": 7, "type": "pdf", "url": "  ", "content": "x"})
        with pytest.raises(ValueError):
            openzen_source._validate_block(7, 3, 1)

    def test_unsupported_block_type_is_rejected(self, monkeypatch):
        self._patch(monkeypatch, {"id": 7, "type": "video", "content": "x"})
        with pytest.raises(ValueError):
            openzen_source._validate_block(7, 3, 1)

    def test_missing_block_is_rejected(self, monkeypatch):
        self._patch(monkeypatch, None)
        with pytest.raises(ValueError, match="not found"):
            openzen_source._validate_block(7, 3, 1)
