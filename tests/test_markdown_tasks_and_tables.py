"""Task lists and tables in the markdown preview.

Two separate failures, reported together as "the View tab renders this
wrong":

  * Checkboxes. The parser had no idea what `- [ ]` is. It matched the
    plain bullet branch and emitted `<li>[ ] text</li>`, so the marker
    survived as literal text — the note showed a bracketed character
    where a box should be. The parser now recognises the task syntax and
    emits a real checkbox.

  * Tables. The parser was always correct here: it built a proper
    `<table>` with `thead`/`tbody` and one cell per column. What was
    missing was a CSS selector. The table rules were scoped to
    `.md-view`, `.sf-td-block-content`, `.md-preview` and `.ct-preview`,
    but the session preview is `.so-notes-rendered` — so the markup
    arrived with `border: 0` and `padding: 0` and read as flat text.

This test executes the real parser through node rather than asserting on
its source text. A source assertion cannot tell whether a `<table>` is
emitted; running it can. The stylesheet is still checked by reading it,
since CSS has no runtime to interrogate here.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BLOCKS_JS = ROOT / "frontend" / "features" / "studyflow" / "content-blocks.js"
BLOCKS_CSS = ROOT / "frontend" / "features" / "studyflow" / "content-blocks.css"

# Built by concatenation rather than str.format: the shim contains JS object
# literals, and their braces would be read as format placeholders.
_HARNESS = """
// Minimal window shim: the module assigns to window.App and reads
// window.App.UI.escHtml, which it already falls back from if absent.
global.window = { App: {} };
const fs = require('fs');
eval(fs.readFileSync(__JS__, 'utf8'));
const out = window.App.ContentBlocks._renderMd(__MD__);
console.log(JSON.stringify(out));
"""


def render(md: str) -> str:
    """Run the real _renderMd and return its HTML."""
    script = (
        _HARNESS
        .replace("__JS__", json.dumps(str(BLOCKS_JS)))
        .replace("__MD__", json.dumps(md))
    )
    proc = subprocess.run(
        ["node", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, "node failed: %s" % proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def css() -> str:
    return BLOCKS_CSS.read_text(encoding="utf-8")


def test_task_item_renders_a_real_checkbox():
    html = render("- [ ] pendiente")
    assert "<input" in html, (
        "- [ ] must render an <input>, not the literal text. Got: %r" % html
    )
    assert 'type="checkbox"' in html
    # The marker must be gone, not merely hidden by CSS.
    assert "[ ]" not in html, (
        "the literal [ ] marker survived into the output: %r" % html
    )


def test_checked_task_is_checked_and_pending_is_not():
    pending = render("- [ ] pendiente")
    checked = render("- [x] hecha")
    assert "checked" not in pending.lower(), "an empty box must not be checked"
    assert "checked" in checked.lower(), "a ticked task must render checked"
    # Capital X is the other spelling people actually type.
    assert "checked" in render("- [X] hecha").lower()


def test_task_list_is_marked_so_it_can_drop_the_bullet():
    html = render("- [ ] a\n- [x] b")
    assert 'class="task-list"' in html, (
        "a list of tasks needs the task-list class to lose its bullets: %r" % html
    )


def test_plain_bullets_are_untouched():
    """A normal list must not sprout checkboxes."""
    html = render("- uno\n- dos")
    assert "<input" not in html, "plain bullets must not become tasks: %r" % html
    assert "class=\"task-list\"" not in html


def test_mixed_list_keeps_plain_items_as_plain():
    html = render("- [ ] tarea\n- solo texto")
    assert html.count("<input") == 1, "only the task item gets a box: %r" % html
    assert "solo texto" in html


def test_table_has_one_cell_per_column():
    html = render("| Dia | Tarea | Estado |\n| --- | --- | --- |\n| Lun | DIW | Hecho |")
    # Counted on the closing tags: "<th" also matches "<thead>".
    assert html.count("</th>") == 3, "header must have 3 cells: %r" % html
    assert html.count("</td>") == 3, "body must have 3 cells: %r" % html
    assert "<thead>" in html and "<tbody>" in html


def test_table_rows_keep_their_own_cells():
    """The regression the user saw: rows collapsing into one run of text."""
    html = render(
        "| Dia | Tarea |\n| --- | --- |\n| Lun | DIW |\n| Mar | ADBD |"
    )
    # Two rows, so two <tr> in the body — a flattened table loses this.
    body = html.split("<tbody>")[1]
    assert body.count("<tr>") == 2, "both rows must survive: %r" % html
    assert "ADBD" in html and "DIW" in html


def test_session_preview_is_in_the_table_rules(css):
    """The actual cause of the invisible table: a missing CSS selector."""
    for rule in (
        r"\.so-notes-rendered\s+table",
        r"\.so-notes-rendered\s+th,\s*\.so-notes-rendered\s+td",
        r"\.so-notes-rendered\s+th\b",
    ):
        assert re.search(rule, css), (
            "content-blocks.css must style tables inside .so-notes-rendered "
            "(the session preview). Without it the parser's correct <table> "
            "renders with border:0 and padding:0. Missing: %s" % rule
        )


def test_table_cells_get_a_border_and_padding(css):
    """Belt and braces: the rule has to actually draw the cells."""
    block = re.search(
        r"\.so-notes-rendered\s+th,\s*\.so-notes-rendered\s+td\s*\{([^}]*)\}", css
    )
    assert block, "no .so-notes-rendered th/td rule found"
    body = block.group(1)
    assert "border" in body, "cells need a border to be visible: %r" % body
    assert "padding" in body, "cells need padding to be readable: %r" % body


def test_task_list_is_styled(css):
    for needle in (".task-list", ".task-item"):
        assert needle in css, "%s has no styling in content-blocks.css" % needle


def test_task_label_is_wrapped_so_it_can_be_struck_through():
    """The row is flex, and a ticked task needs an element to strike.

    Bare text after the input is unreachable for both: the flex layout has
    nothing to align, and the line-through has nothing to land on. This is
    what a missing `.task-label` wrapper looks like from the outside — the
    checkbox renders but the "done" state stays invisible.
    """
    html = render("- [x] hecha")
    assert 'class="task-label"' in html, (
        "the task text must be wrapped so the ticked state is visible: %r" % html
    )


def test_strikethrough_targets_the_label_not_a_bare_child(css):
    """`> *:not(input)` matches nothing when the text is unwrapped."""
    assert "> *:not(input)" not in css, (
        "this selector was the bug: with the label as a bare text node it "
        "matched no element and the tick state never showed"
    )
    assert re.search(r":has\(input:checked\)\s+\.task-label", css), (
        "the ticked state must be struck through .task-label"
    )


def test_checkbox_is_not_clickable(css):
    """A box you can tick in a read-only preview but cannot save is a lie."""
    block = re.search(r'\.task-list \.task-item input\[type="checkbox"\][^{]*\{([^}]*)\}', css)
    assert block, "the checkbox itself needs a rule to sit on the text baseline"
    assert "cursor" in block.group(1), "the cursor must say it is not a control"
