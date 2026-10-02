r"""Guard: no chunk sent to OpenZEN may exceed the size it can answer.

A 424k-char transcript in standard depth died with
`OpenCode request timed out (900s)` and was left in 'processing' with nothing
to retry. Two independent defects, both fixed here:

1. `_chunk_text()` only flushed when `current` was non-empty, so a FIRST
   paragraph longer than max_chars was appended to an empty buffer and emitted
   whole. One giant paragraph = one oversized chunk = the 900s timeout.
2. Chunking was gated on `depth == "detailed"`, making the protection a
   function of a style choice rather than the payload size.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZEN = ROOT / "backend" / "ai" / "notebooklm" / "youtube_zen.py"
MAX = 8000


def _ns() -> dict:
    """Exec only the splitting helpers (the module needs heavy deps)."""
    src = ZEN.read_text()
    ns = {"re": re, "_CHUNK_MAX_CHARS": MAX}
    for fn in ("_chunk_text", "_hard_split"):
        i = src.index(f"def {fn}(")
        j = src.index("\ndef ", i + 1)
        exec(src[i:j], ns)
    return ns


def test_oversized_single_paragraph_is_split_not_emitted_whole():
    """The regression: a paragraph bigger than max_chars used to pass through."""
    chunk_text = _ns()["_chunk_text"]
    cs = chunk_text("palabra " * 60_000)  # one paragraph, ~420k chars
    assert max(len(c) for c in cs) <= MAX, (
        "a single oversized paragraph is still emitted whole — this is what "
        "produced the 900s timeout"
    )
    assert len(cs) > 1


def test_no_chunk_exceeds_the_ceiling_on_a_real_sized_transcript():
    """424k chars of VTT-shaped paragraphs, the size that actually failed."""
    chunk_text = _ns()["_chunk_text"]
    text = "\n\n".join(["frase de subtitulos con palabras varias " * 40] * 600)
    text = text[:424_731]
    cs = chunk_text(text)
    assert max(len(c) for c in cs) <= MAX
    assert len(cs) > 50, "expected the transcript to split into many chunks"


def test_order_is_preserved_across_the_hard_split():
    chunk_text = _ns()["_chunk_text"]
    text = "\n\n".join(["A" * 100, "B" * 120_000, "C" * 100])
    cs = chunk_text(text)
    assert cs[0].startswith("A"), "content before the giant paragraph was lost"
    assert cs[-1].strip().endswith("C"), "content after it was lost"


def test_short_text_is_still_a_single_chunk():
    chunk_text = _ns()["_chunk_text"]
    assert chunk_text("texto corto") == ["texto corto"]


def test_hard_split_prefers_sentence_boundaries():
    hard = _ns()["_hard_split"]
    out = hard("Uno. Dos. Tres. Cuatro.", MAX)
    assert len(out) == 1, "must not split when it already fits"
    original = ". ".join(["palabra"] * 6000)
    long = hard(original, MAX)
    assert all(len(p) <= MAX for p in long), (
        "the ceiling must be strict, not max_chars+1"
    )
    assert "".join(long) == original, (
        "the split must be lossless — re-inserting separators that re.split "
        "already consumed silently inflates the text the model reads"
    )


def test_chunking_is_gated_on_size_not_only_on_detailed_depth():
    """A huge transcript in standard depth must still be chunked."""
    src = ZEN.read_text()
    assert "_CHUNK_THRESHOLD_CHARS" in src, (
        "the size threshold that decouples chunking from depth is missing"
    )
    dispatch = src.split("if depth == \"detailed\":", 1)
    assert len(dispatch) > 1, "the depth dispatch point moved — re-check this guard"
    # the non-detailed branch must chunk too, which it does via _chunk_text
    assert "_chunk_text(clean_text)" in src, (
        "the non-detailed branch lost its chunking"
    )
