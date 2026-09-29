# tests/test_prompts.py
"""Unit tests for the NotebookLM prompt templates.

Every prompt in `prompts.py` is consumed through `str.format(...)`, and most
of them embed a JSON example of the shape the model must reply with. Those
examples contain literal braces:

    "{\n"
    '  "question": "Question text?",\n'
    "}\n"

Left unescaped, `str.format` reads the whole example as a *field name*, so
`TEST_GENERATION_PROMPT.format(num_questions=20)` died with

    KeyError: '\\n  "question"'

which surfaced to the user only as a task-level "Error en la tarea" with a
meaningless message. These tests pin every template so an added JSON example
cannot silently reintroduce the bug.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from ai.notebooklm import prompts as P  # noqa: E402

# Placeholder values used to render each template. Any value works: the point
# is that `.format()` finds every field the template declares.
KW = {
    "num_questions": 20,
    "max_cards": 20,
    "num_cards": 10,
    "language": "es",
    "minutes": 5,
    "words": 750,
    "num_items": 5,
    "length": "standard",
}


def _templates() -> dict:
    return {
        name: getattr(P, name)
        for name in dir(P)
        if name.isupper() and isinstance(getattr(P, name), str)
    }


class TestPromptTemplates:
    """Every template must survive a real `.format()` call."""

    def test_there_are_templates(self):
        # Guard against the loop below silently passing on an empty namespace.
        assert len(_templates()) >= 14

    @pytest.mark.parametrize("name", sorted(_templates()))
    def test_format_does_not_raise(self, name):
        template = getattr(P, name)
        # A KeyError here means an unescaped `{` (almost always a JSON
        # example); an IndexError means a stray `{}`.
        rendered = template.format(**KW)
        assert rendered
        # `.format` collapses the doubled braces, so the rendered prompt must
        # still show the example as literal JSON.
        assert "{{" not in rendered, f"{name} leaked escaped braces"

    def test_test_prompt_keeps_json_example(self):
        """The regression itself: the JSON shape must survive verbatim."""
        out = P.make_test_prompt(num_questions=20)
        assert '"question": "Question text?"' in out
        assert '"explanation": "Why this is correct"' in out
        # Literal braces in the output, and the count was interpolated.
        assert '{\n  "question"' in out
        assert "exactly 20 question objects" in out

    def test_test_prompt_with_block_title(self):
        out = P.make_test_prompt(3, block_title="Cardiovascular")
        assert "# SOURCE TITLE" in out
        assert "Cardiovascular" in out
        assert "exactly 3 question objects" in out

    def test_flashcards_prompt_keeps_nested_json(self):
        out = P.FLASHCARDS_GENERATION_PROMPT.format(max_cards=20)
        assert '"front": "Concise concept or question"' in out
        assert '"back": "Brief clear explanation"' in out
        # Nested object braces must be literal, not consumed as placeholders.
        assert '"cards": [' in out
        assert "at most 20 cards" in out.lower()
