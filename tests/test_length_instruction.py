"""_length_instruction must make the three verbosity levels actually differ.

The bug: the UI advertised "📚 Extensa / Máximo detalle y ejemplos" (and the
knowledge pipeline "Detallado / Curso completo"), but the instruction handed
to the model for `detailed` read:

    "Expand each section moderately: ... Avoid exhaustive catalogues."

That is the WEAKEST of the three directives — it explicitly tells Gemini to
pull back — while `standard` said "FULL DEVELOPMENT ... expand each idea
enough to stand alone". So the most verbose-looking button produced the
shortest output. Measured on the same template (infografia-textual):

    standard  n=4   11 602 chars
    detailed  n=1   10 709 chars

The user's report ("le doy a Extensa y no hace caso") was exact.

These tests pin the ordering rather than the exact wording, so the phrasing
can still be edited — but the semantics cannot silently invert again.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from ai.notebooklm.md_templates import _length_instruction as v3_length  # noqa: E402
from ai.generation.prompts import _length_instruction as v2_length  # noqa: E402

COPIES = {"v3 (NotebookLM)": v3_length, "v2 (generation)": v2_length}
LEVELS = ["concise", "standard", "detailed"]


class TestCopiesHaveNotDrifted:
    """Two copies of this function exist. When they drift, fixes land on one."""

    @pytest.mark.parametrize("level", LEVELS + ["", "bogus"])
    def test_copies_agree(self, level):
        assert v3_length(level) == v2_length(level), (
            "_length_instruction has diverged between "
            "ai/notebooklm/md_templates.py and ai/generation/prompts.py. "
            "A fix applied to only one copy silently leaves the other broken."
        )


class TestLevelsAreDistinct:
    @pytest.mark.parametrize("level", LEVELS)
    def test_every_level_returns_something(self, level):
        assert v3_length(level).strip(), f"{level!r} produced an empty instruction"

    def test_the_three_levels_are_not_the_same_text(self):
        rendered = {lv: v3_length(lv).strip().lower() for lv in LEVELS}
        assert len(set(rendered.values())) == 3, (
            "two or more depth levels render the same instruction, so the "
            "selector cannot possibly change the output"
        )

    def test_unknown_level_falls_through(self):
        assert v3_length("banana") == ""
        assert v3_length("") == ""
        assert v3_length(None) == ""


class TestDetailedIsNotSelfSabotaging:
    """The exact regression: detailed used to ask for LESS than standard."""

    @pytest.mark.parametrize("name,fn", COPIES.items(), ids=list(COPIES))
    @pytest.mark.parametrize(
        "forbidden",
        [
            "avoid exhaustive",
            "moderately",
            "moderate",
        ],
    )
    def test_detailed_contains_no_self_defeating_language(self, name, fn, forbidden):
        text = fn("detailed").lower()
        assert forbidden not in text, (
            f"{name}: the 'detailed' instruction contains {forbidden!r}, which "
            "tells the model to hold back. The UI promises 'máximo detalle' / "
            "'curso completo', so the instruction must not restrain it."
        )

    @pytest.mark.parametrize("name,fn", COPIES.items(), ids=list(COPIES))
    def test_detailed_asks_for_more_than_standard(self, name, fn):
        """detailed must explicitly outrank the standard treatment."""
        text = fn("detailed").lower()
        assert any(
            marker in text
            for marker in ("maximum", "more than a standard", "richer and longer")
        ), (
            f"{name}: 'detailed' never states that it exceeds standard, so a "
            "model has no basis for choosing between them"
        )

    @pytest.mark.parametrize("name,fn", COPIES.items(), ids=list(COPIES))
    def test_concise_is_the_only_one_that_asks_to_omit(self, name, fn):
        """Only concise may tell the model to drop material."""
        assert "omit" in fn("concise").lower(), (
            f"{name}: concise no longer asks to omit anything, so it will not "
            "actually be concise"
        )
        for level in ("standard", "detailed"):
            assert "omit" not in fn(level).lower(), (
                f"{name}: {level!r} must not ask to omit material"
            )


class TestSourceOfTruthConstraint:
    """The user's rule: more content, but never outside the source."""

    @pytest.mark.parametrize("name,fn", COPIES.items(), ids=list(COPIES))
    def test_detailed_may_not_invent_facts(self, name, fn):
        assert "do not invent" in fn("detailed").lower(), (
            f"{name}: 'detailed' pushes for more content; without an explicit "
            "no-inventing rule the model will pad the course with material "
            "that is not in the PDF"
        )
