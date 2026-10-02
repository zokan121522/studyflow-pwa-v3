"""Tests for the three things that were wrong with an AI insert.

All three were reported together, from one generation, and all three were
silent: nothing errored, nothing looked broken, the result was just slightly
wrong in a way that only shows up when you look at the screen for longer than
it takes to click Insert.

**The timer never stopped.** `clearInterval` for the clock lived only in
`_hideStreamModal`, which runs when the modal closes — i.e. when the user
clicks Insert. So the number on screen was not the time the generation took;
it was the time the user had spent deciding whether to insert, still ticking
while they read the result. The generation time is the interesting number, and
it is the one that was impossible to read.

**The block landed in the wrong place.** `move_block` takes `index` as an
ABSOLUTE `order_index` and does not shift its neighbours (see blocks.py). The
AI path passed `blocks.findIndex(...)` — a position in a 0..N-1 array — as if
it were an order_index. Real topics carry sparse, historical order_index values
(1, 6, 9, 14, 22, 23, 30, 43…), so the two numbers are unrelated and the block
landed somewhere else entirely. courses-blocks.js gets this right for PDFs;
these tests pin the AI path to the same rule, including the skip-over-occupied
step, because writing source.order_index + 1 blindly ties with a neighbour.

**The icon was 📄 instead of 📊.** `getBlockIcon` looked up `TYPE_ICONS` by type
before the branch that checks title prefixes — and `TYPE_ICONS.content` is
"📄", a truthy string, so that branch was unreachable dead code. It read as
though "📊 for a content block starting with 📊" worked; it only worked for
blocks whose title happened to start with that emoji. Since the user asked for
the title to read plain "Infografía", every such block fell to the generic 📄.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "frontend/features/studyflow/ai-tasks.js"
AI = ROOT / "frontend/features/studyflow/ai.js"
SIDEBAR = ROOT / "frontend/features/studyflow/courses-sidebar.js"
BLOCKS_PY = ROOT / "backend/routes/blocks.py"

tasks = TASKS.read_text(encoding="utf-8")
ai = AI.read_text(encoding="utf-8")
sidebar = SIDEBAR.read_text(encoding="utf-8")
blocks_py = BLOCKS_PY.read_text(encoding="utf-8")


# ── 1. the timer must freeze on the terminal state, not on modal close ────

def test_stop_timer_exists_and_freezes():
    assert "function _stopTimer" in tasks
    body = tasks.split("function _stopTimer", 1)[1].split("\n  }", 1)[0]
    assert "clearInterval(_timerInterval)" in body
    # Freezing means keeping the value: it must not reset startTime, or the
    # rendered number would be recomputed from zero.
    assert "_streamState.startTime = 0" not in body
    assert 'classList.add("done")' in body


def _stream_poll() -> str:
    """Only the streaming poll. There is a second `task.status === "done"` in
    startPoll, which drives _showStatus and has no timer at all — searching the
    whole file finds that one first and the test then asserts against a branch
    that was never supposed to stop a clock."""
    return tasks.split("function startStreamPoll", 1)[1]


def test_timer_is_stopped_on_every_terminal_state():
    """done, error and cancelled all end the task, so all three must stop it."""
    stream = _stream_poll()
    for state in ('"done"', '"error"', '"cancelled"'):
        idx = stream.find(f"task.status === {state}")
        assert idx != -1, f"no branch for {state} in the stream poll"
        assert "_stopTimer(true)" in stream[idx:idx + 900], (
            f"timer keeps running after {state}"
        )


def test_timer_only_stopped_by_completion_path():
    """The old bug was that ONLY _hideStreamModal cleared the clock."""
    hide = tasks.split("function _hideStreamModal", 1)[1].split("\n  }", 1)[0]
    # Still cleared on close (that is correct), but now it is not the only place.
    assert "clearInterval(_timerInterval)" in hide
    assert tasks.count("_stopTimer(true)") >= 3


# ── 2. insertion position: absolute order_index, skipping taken slots ─────

def test_move_endpoint_takes_an_absolute_order_index():
    """The premise of the fix. If blocks.py ever shifts neighbours, the
    skip-occupied loop below becomes wrong and these tests should be revisited."""
    body = blocks_py.split("def move_block", 1)[1]
    assert "order_index = int(index) if index is not None else 0" in body
    assert "UPDATE blocks SET topic_id = %s, order_index = %s" in body
    # The update is scoped to the single block: no neighbour-shifting pass.
    assert body.count("UPDATE blocks") == 1
    assert "WHERE id = %s" in body


def test_insertion_uses_order_index_not_array_position():
    fn = ai.split("async function _addBlockAfterSource", 1)[1].split("\n  }", 1)[0]
    assert "Number(source.order_index) + 1" in fn
    # The bug: a findIndex result is a 0..N-1 position, not an order_index.
    assert "findIndex" not in fn, "positional index must not drive order_index"
    assert "Number(b.id) === Number(sourceBlockId)" in fn


def test_insertion_skips_occupied_slots():
    fn = ai.split("async function _addBlockAfterSource", 1)[1].split("\n  }", 1)[0]
    assert "while (taken(target)) target += 1;" in fn
    # ...and the tie check must exclude the block being placed.
    assert "Number(b.id) !== Number(newBlock.id)" in fn


def test_insertion_reports_a_missing_source_instead_of_failing_silent():
    """idx === -1 used to leave the block at the topic end with no trace."""
    fn = ai.split("async function _addBlockAfterSource", 1)[1].split("\n  }", 1)[0]
    assert "source block not found" in fn


def test_ai_path_matches_the_pdf_path_rule():
    """Both must read the source's order_index and step over taken slots."""
    pdf = (ROOT / "frontend/features/studyflow/courses-blocks.js").read_text(encoding="utf-8")
    assert "while (list.some" in pdf, "PDF insert rule changed — recheck the AI path"


# ── 3. the icon: 📊 for an infographic, and the dead branch is alive ──────

def test_infographic_icon_is_found_in_the_content():
    sidebar_fn = sidebar.split("function getBlockIcon", 1)[1].split("\n  }", 1)[0]
    assert "contentMarkerIcon(block)" in sidebar_fn
    assert "CONTENT_MARKER_ICONS" in sidebar
    assert "📊" in sidebar


def test_content_marker_precedes_the_type_lookup():
    """The dead-code bug: TYPE_ICONS.content ("📄") returned first, so the
    title-prefix branch below it could never run."""
    sidebar_fn = sidebar.split("function getBlockIcon", 1)[1].split("\n  }", 1)[0]
    assert sidebar_fn.index("contentMarkerIcon") < sidebar_fn.index("TYPE_ICONS[block.type]")


def test_audio_still_gets_its_own_icon():
    """Same content-type bucket; it must not fall into the generic 📄."""
    assert r"/<audio\b/i" in sidebar
    assert "🎵" in sidebar


def test_title_emoji_prefix_still_wins_so_icons_do_not_double():
    sidebar_fn = sidebar.split("function getBlockIcon", 1)[1].split("\n  }", 1)[0]
    assert sidebar_fn.index("hasTitleEmojiPrefix") < sidebar_fn.index("contentMarkerIcon")


def test_plain_hand_written_content_block_keeps_the_generic_icon():
    """A content block with no marker and no prefix is a text note: 📄 stands."""
    sidebar_fn = sidebar.split("function getBlockIcon", 1)[1].split("\n  }", 1)[0]
    assert sidebar_fn.rstrip().endswith('return "📄";')
