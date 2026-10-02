r"""Tests for three bugs reported after generating a video with NotebookLM.

  1. **A duplicate 📝 on every markdown block.** The nav renders a
     type icon (`markdown -> 📝`) *and* the AI block titles already carry
     an emoji (`📚 NotebookLM`, `🎥 YouTube Zen`), so every generated block
     read "📝 📚 Title": two icons for one fact. Dropping the type icon
     only when the title already leads with an emoji keeps hand-made
     blocks ("Nuevo Markdown", no emoji) with their 📝.

  2. **"por tema" inserted a single block.** Two independent causes:

     a. *The mode never arrived.* The worker rewrote `coverage_data` with
        `{"video_title": ..., "video_url": ...}` on success, destroying the
        `mode` that `create_youtube_md_task` had stored. The task therefore
        finished "correctly" while quietly losing the user's choice — the
        one thing that says per-topic was requested.

     b. *The split did not cover this format anyway.* v2's condition was
        `(isKp || format === "ytd_zen")`, and the NotebookLM YouTube
        endpoint posts `format: "markdown"`, so even a delivered `mode`
        would have been ignored.

  3. **"RPC response exceeded 52428800 bytes".** A 7.5 h course
     (27,030 s) asked NotebookLM for the whole video in one shot; the
     client library refuses any RPC response over 50 MiB. The failure
     arrived as a raw byte-count dump after a minute of ingestion. The
     video is now rejected up front with actionable advice, and the raw
     library error is translated in case the pre-flight could not see the
     duration.
"""

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "features" / "studyflow"
SIDEBAR = FRONTEND / "courses-sidebar.js"
AI_JS = FRONTEND / "ai.js"
YOUTUBE_PY = ROOT / "backend" / "ai" / "notebooklm" / "youtube.py"


def _src(path: Path) -> str:
    return path.read_text()


# ── 1. the duplicate icon ────────────────────────────────────────────────────

def test_type_icon_suppressed_when_title_has_emoji():
    src = _src(SIDEBAR)
    assert "hasTitleEmojiPrefix(block.title)" in src, (
        "getBlockIcon must check the title for a leading emoji before "
        "falling back to the type icon"
    )


def test_icon_check_runs_before_the_type_icon_lookup():
    """Order matters: TYPE_ICONS wins if it is consulted first."""
    body = _src(SIDEBAR).split("function getBlockIcon(block) {", 1)[1]
    body = body.split("\n  }", 1)[0]
    assert body.index("hasTitleEmojiPrefix") < body.index("TYPE_ICONS[block.type]"), (
        "the emoji check has to short-circuit before TYPE_ICONS is read"
    )


def test_emoji_prefix_uses_the_unicode_property_not_a_hand_list():
    """A hand-written list of "the emoji we happen to use" fell behind the
    real titles: the browser showed 📄 + 📊, 📄 + 🎵 and ❓ + ❓ still
    double-iconed after the first attempt. Extended_Pictographic is the
    definition of an emoji, so a new title prefix cannot outrun it.
    """
    pattern = _src(SIDEBAR).split("const TITLE_EMOJI_PREFIX =", 1)[1].split(";", 1)[0]
    assert "Extended_Pictographic" in pattern, (
        "TITLE_EMOJI_PREFIX must key off Unicode Extended_Pictographic"
    )


@pytest.mark.parametrize("emoji", [
    "\U0001F916", "\U0001F4DA", "\U0001F3A5", "\U0001F4DD", "\U0001F4C4",
    "\U0001F3B5", "\U0001F4CA", "\u2753", "\u2728", "\U0001F319",
    "\u270F\uFE0F", "\U0001F9E0", "\U0001F48A", "\U0001F5A5\uFE0F",
])
def test_every_emoji_that_reaches_a_title_is_recognised(emoji):
    """Each of these is written by some code path; all must suppress the icon."""
    import subprocess
    js = (
        "const re = /^\\p{Extended_Pictographic}\\s*/u;"
        f"process.stdout.write(String(re.test({emoji!r} + ' titulo')))"
    )
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert out.stdout.strip() == "true", f"{emoji} not recognised as an emoji prefix"


def test_markdown_blocks_get_no_automatic_icon():
    """Markdown gets no painted icon — it was the one the user could not remove.

    It is not stored in the title, so editing the title to drop the AI's
    emoji only made this icon reappear in its place. The two swapped seats
    instead of going away.
    """
    body = _src(SIDEBAR).split("function getBlockIcon(block) {", 1)[1].split("\n  }", 1)[0]
    assert 'if (block.type === "markdown") return "";' in body, (
        "markdown must short-circuit to no icon before the TYPE_ICONS lookup"
    )
    assert body.index('block.type === "markdown"') < body.index(
        "TYPE_ICONS[block.type]"
    ), "the markdown check must run before the type table is consulted"


def test_other_block_types_keep_their_icon():
    """The user's complaint was about markdown; the rest still get a marker."""
    body = _src(SIDEBAR).split("function getBlockIcon(block) {", 1)[1].split("\n  }", 1)[0]
    assert 'if (TYPE_ICONS[block.type]) return TYPE_ICONS[block.type]' in body


def test_ai_titles_carry_no_emoji_prefix():
    """The insert must not decorate the title; the AI leaves it as generated."""
    src = _src(AI_JS)
    assert 'title: `${emoji} ${sourceTitle}`' not in src, (
        "the 🎥 / 📚 prefix is back — that is the icon the user deletes by hand"
    )
    assert 'title: `${emoji} ${sections[i].title}`' not in src, (
        "the por_tema split re-added the same prefix"
    )
    assert "title: sourceTitle," in src


def test_empty_icon_does_not_leave_a_gap_in_the_layout():
    src = _src(SIDEBAR)
    assert '${icon ? `<span class="bi-icon">${icon}</span>` : ""}' in src, (
        "an unconditional <span> would keep its width and leave a hole where "
        "the icon used to be"
    )


# ── 2a. the mode must survive the worker's UPDATE ───────────────────────────

def test_coverage_data_is_merged_not_replaced():
    src = _src(YOUTUBE_PY)
    assert "coverage_data = _merge_coverage(task_id, {" in src
    assert 'json.dumps({"video_title"' not in src, (
        "coverage_data is being overwritten wholesale, which wipes mode/"
        "template_id/language — the user silently loses their choices"
    )


def test_merge_preserves_existing_keys():
    sys.path.insert(0, str(ROOT / "backend"))
    from ai.notebooklm import youtube as yt

    yt.query_one = lambda *a, **k: {
        "coverage_data": json.dumps({"mode": "por_tema", "url": "https://x"})
    }
    merged = json.loads(yt._merge_coverage("t1", {"video_title": "T", "video_url": "U"}))
    assert merged["mode"] == "por_tema", "the mode the user picked was dropped"
    assert merged["video_title"] == "T"
    assert merged["video_url"] == "U"


def test_merge_does_not_resurrect_blank_existing_values():
    sys.path.insert(0, str(ROOT / "backend"))
    from ai.notebooklm import youtube as yt

    yt.query_one = lambda *a, **k: {"coverage_data": json.dumps({"mode": None, "template_id": ""})}
    merged = json.loads(yt._merge_coverage("t1", {"video_title": "T"}))
    assert "mode" not in merged
    assert "template_id" not in merged


def test_merge_survives_unparsable_coverage_data():
    sys.path.insert(0, str(ROOT / "backend"))
    from ai.notebooklm import youtube as yt

    yt.query_one = lambda *a, **k: {"coverage_data": "{not json"}
    merged = json.loads(yt._merge_coverage("t1", {"video_title": "T"}))
    assert merged == {"video_title": "T"}


# ── 2b. the split must cover the NotebookLM format ──────────────────────────

def test_por_tema_recognised_for_the_notebooklm_markdown_format():
    """The endpoint posts format "markdown"; v2 only matched "ytd_zen"."""
    src = _src(AI_JS)
    body = src.split("function _isPorTemaTask(task, format) {", 1)[1].split("\n  }", 1)[0]
    for fmt in ("markdown", "md", "ytd_zen", "knowledge_pipeline"):
        assert f'"{fmt}"' in body, f"por_tema would be ignored for format {fmt}"


def test_por_tema_requires_the_mode():
    body = _src(AI_JS).split("function _isPorTemaTask(task, format) {", 1)[1].split("\n  }", 1)[0]
    assert 'mode !== "por_tema"' in body, "unitema must not be split"


def test_split_splits_on_h2_only():
    body = _src(AI_JS).split("function _parsePorTemaSections(content) {", 1)[1].split("\n  }", 1)[0]
    assert 'split(/(?=^##\\s)/m)' in body, (
        "must split only on level-2 headings so ### stays inside its section"
    )


def test_split_runs_before_the_single_block_insert():
    src = _src(AI_JS)
    split_at = src.index("if (_isPorTemaTask(task, format))")
    insert_at = src.index("await _addBlockAfterSource(courseId, blockId, {", split_at)
    assert split_at < insert_at, "the single-block insert must be skipped in por_tema"
    assert "return; // do not also insert the whole thing as one block" in src


def test_multi_block_insert_keeps_section_order():
    """Every insert targets the same slot, so an offset is required.

    The expression this asserts on changed when the AI path stopped treating an
    array position as an order_index: the target is now derived from the
    source's real order_index. The requirement is unchanged — the offset must
    reach the index that gets written.
    """
    src = _src(AI_JS)
    assert "indexOffset = 0" in src
    assert "(indexOffset || 0)" in src, (
        "without the offset each new section is inserted at the same slot and "
        "pushes the previous one down — the blocks come out reversed"
    )
    # ...and the offset must actually be added to the value written, not just
    # present in the signature.
    assert re.search(r"order_index\) \+ 1 \+ \(indexOffset \|\| 0\)", src), (
        "indexOffset must be added to the order_index the move endpoint receives"
    )
    assert "}, i);" in src, "the per-section offset must be passed when inserting"


# ── 3. the oversized video ──────────────────────────────────────────────────

def test_long_videos_are_rejected_before_ingestion():
    src = _src(YOUTUBE_PY)
    assert "_LONG_VIDEO_SECONDS" in src
    block = src.split('if output_format == "markdown":', 1)[1].split("_update_progress(", 1)[0]
    assert "_fetch_video_meta" in block
    assert "duration > _LONG_VIDEO_SECONDS" in block


def test_duration_preflight_is_advisory_not_fatal_on_lookup_failure():
    """A missing cookie file must not make every video fail."""
    block = _src(YOUTUBE_PY).split('if output_format == "markdown":', 1)[1].split("_update_progress(", 1)[0]
    assert "except ValueError:" in block, "the long-video refusal must not be swallowed"
    assert re.search(r"except ValueError:\s*\n\s*raise", block)
    assert "duration pre-flight skipped" in block, (
        "an unknown duration must log and continue, not fail the task"
    )


def test_rpc_error_is_translated_into_advice():
    src = _src(YOUTUBE_PY)
    assert "def _humanise_task_error" in src
    assert '"RPC response exceeded" in text' in src
    assert "return _LONG_VIDEO_MESSAGE" in src


def test_unfamiliar_errors_pass_through_verbatim():
    """Rewriting an unknown error is worse than showing it raw."""
    sys.path.insert(0, str(ROOT / "backend"))
    from ai.notebooklm import youtube as yt

    assert yt._humanise_task_error(RuntimeError("algo raro de Gemini")) == "algo raro de Gemini"


def test_oversized_rpc_error_is_not_verbatim():
    sys.path.insert(0, str(ROOT / "backend"))
    from ai.notebooklm import youtube as yt

    raw = "RPC response exceeded 52428800 bytes (read 52449345 bytes before aborting)"
    out = yt._humanise_task_error(RuntimeError(raw))
    assert "52428800" not in out and "52449345" not in out
    assert "YouTube" in out, "the message must point at the path that works"
    # the working path is the chunked one — saying so is the whole point
    assert "chunk" in out, "the advice must say which path actually chunks"


def test_error_message_is_actionable_not_just_longer():
    src = _src(YOUTUBE_PY)
    message = src.split('_LONG_VIDEO_MESSAGE = (', 1)[1].split("\n)", 1)[0]
    for hint in ("3 horas", "50 MB", "YouTube", "chunk"):
        assert hint in message, f"the advice should mention {hint!r}"


def test_error_message_explains_why_chunking_is_impossible_here():
    """A user who knows the chunked flow exists must be told why this path
    cannot offer it — otherwise the refusal reads as a brush-off.

    The reason is concrete: a YouTube URL is one source, and the chat call
    takes no time range, so there is nothing to slice.
    """
    src = _src(YOUTUBE_PY)
    message = src.split('_LONG_VIDEO_MESSAGE = (', 1)[1].split("\n)", 1)[0]
    assert "no se puede" in message and "trocear" in message
    assert "URL de YouTube es una única fuente" in message
    assert "tramo de tiempo" in message


def test_raise_does_not_leave_a_notebook_behind():
    """The pre-flight runs before any notebook is created, so nothing to clean."""
    src = _src(YOUTUBE_PY)
    worker = src.split("def _run_youtube_task", 1)[1]
    preflight = worker.index("duration > _LONG_VIDEO_SECONDS")
    assert preflight < worker.index("youtube_to_markdown("), (
        "the check must run before the expensive call, not after it"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ── provenance header (option A: two lines INSIDE the markdown block) ───────

def test_provenance_helper_is_exported_for_testing():
    assert "_provenance," in _src(AI_JS).split("return {", 1)[-1], (
        "_provenance must be reachable so its output can be asserted"
    )


def test_provenance_prepends_both_lines_as_a_blockquote():
    """Two blockquote lines then a blank line, ahead of the generated content."""
    body = _src(AI_JS).split("function _provenanceHeader(task, format) {", 1)[1]
    body = body.split("\n  }", 1)[0]
    assert 'lines.push(`> ${p.chip}`)' in body
    assert 'lines.push(`> 🎬 ${p.link}`)' in body
    assert 'return lines.join("\\n") + "\\n\\n"' in body, (
        "the header needs a trailing blank line or it fuses into the first "
        "heading of the generated content"
    )


def test_provenance_is_applied_to_both_insert_paths():
    """The single-block path AND the por_tema split, or half the blocks lose it."""
    src = _src(AI_JS)
    assert src.count("_provenanceHeader(task, format)") >= 3, (
        "expected the helper call in the por_tema split, the single-block "
        "path, and the helper's own definition"
    )


def test_provenance_never_touches_audio_or_infographic():
    """A blockquote in front of a bare <audio>/<img> tag is literal text."""
    src = _src(AI_JS)
    assert 'if (blockType === "markdown") {' in src, (
        "audio/infographic/html content is raw markup; the header must be "
        "gated on markdown or it lands as visible text above the media"
    )


def test_por_tema_every_section_gets_the_header():
    """A section dragged out of its siblings should still say what made it."""
    split = _src(AI_JS).split("for (let i = 0; i < sections.length; i++)", 1)[1]
    split = split.split("}", 1)[0]
    assert "_provenanceHeader(task, format) + sections[i].body" in split, (
        "only the first section is getting the header; the rest are orphans"
    )


def test_no_trailing_emoji_prefix_leaks_back_into_titles():
    """Titles must stay exactly as generated (see the 🎥 regression)."""
    src = _src(AI_JS)
    assert "title: `${emoji}" not in src
    assert "title: sourceTitle," in src
