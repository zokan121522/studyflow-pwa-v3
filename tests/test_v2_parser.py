# tests/test_v2_parser.py
"""Unit tests for the v2 COPY-dump parser (issue #8).

The parser is the one place where a silent mistake loses data: a wrong
unescape or a dropped row would look like a successful import. These tests
pin the escaping rules and the section parsing.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from v2_parser import _unescape, parse_copy_sections  # noqa: E402


class TestUnescape:
    """COPY text format: backslash is the escape character."""

    @pytest.mark.parametrize("raw,expected", [
        (r"\N", None),                      # the NULL marker
        (r"", ""),                          # empty string is not NULL
        (r"a\nb", "a\nb"),                  # newline
        (r"a\tb", "a\tb"),                  # tab — the column separator
        (r"a\rb", "a\rb"),
        (r"a\bb", "a\bb"),
        (r"a\fb", "a\fb"),
        (r"a\vb", "a\vb"),
        (r"a\\b", "a\\b"),                  # escaped backslash
        (r"\\", "\\"),
        (r"\\\\", "\\\\"),                   # two literal backslashes
        (r"a\qb", "aqb"),                   # unknown escape → itself
        (r"100%", "100%"),                  # no false positive on %
    ])
    def test_unescape(self, raw, expected):
        assert _unescape(raw) == expected

    def test_literal_not_null(self):
        r"""`\N` is NULL, but a word starting with N is not."""
        assert _unescape("NULL") == "NULL"
        assert _unescape("N") == "N"


class TestParseCopySections:
    DUMP = (
        'COPY "courses" ("id", "title", "notes") FROM stdin;\n'
        "eng-1\tEnglish B2\tline1\\nline2\n"
        "eng-2\t\t\n"
        "\\.\n"
        'COPY "blocks" ("id", "type") FROM stdin;\n'
        "b1\tcontent\n"
        "\\.\n"
    )

    def test_parses_each_table(self):
        tables = parse_copy_sections(self.DUMP)
        assert set(tables) == {"courses", "blocks"}

    def test_column_count_maps_to_keys(self):
        tables = parse_copy_sections(self.DUMP)
        assert tables["courses"][0] == {
            "id": "eng-1", "title": "English B2", "notes": "line1\nline2"}

    def test_empty_field_is_empty_string_not_null(self):
        tables = parse_copy_sections(self.DUMP)
        assert tables["courses"][1]["notes"] == ""

    def test_literal_backslash_n_is_newline(self):
        tables = parse_copy_sections(self.DUMP)
        assert "line2" in tables["courses"][0]["notes"]

    def test_terminator_not_treated_as_data(self):
        tables = parse_copy_sections(self.DUMP)
        assert len(tables["courses"]) == 2

    def test_embedded_tabs_inside_a_value(self):
        """A tab inside a field only separates columns if it is unescaped."""
        dump = ('COPY "t" ("a", "b") FROM stdin;\n'
                "one\\ttwo\tthree\n\\.\n")
        tables = parse_copy_sections(dump)
        assert tables["t"][0] == {"a": "one\ttwo", "b": "three"}

    def test_empty_dump(self):
        assert parse_copy_sections("") == {}

    def test_real_unicode_content(self):
        dump = ('COPY "t" ("a") FROM stdin;\n'
                "Assignments de español 🎓\n\\.\n")
        assert parse_copy_sections(dump)["t"][0]["a"] == \
            "Assignments de español 🎓"

    def test_large_content_block_not_truncated(self):
        """The 488 KB English IFP document must survive intact."""
        big = "<p>" + ("contenido " * 50_000) + "</p>"
        dump = f'COPY "t" ("a") FROM stdin;\n{big}\n\\.\n'
        assert parse_copy_sections(dump)["t"][0]["a"] == big
