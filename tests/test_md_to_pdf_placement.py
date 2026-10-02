"""Where the generated pdf-ref block must land.

The placement logic is subtle and was wrong twice, so it is pinned here:

* ``POST /courses/blocks/<id>/move`` sets an ABSOLUTE ``order_index`` and does
  NOT shift its neighbours, even though its docstring claims "move + reindex".
  Writing ``sourceIdx + 1`` therefore lands on top of whatever already occupies
  that slot, and the two rows tie — resolved only by id.
* The fix is to advance past occupied slots instead of rewriting rows the user
  owns. The PDF still ends up directly below its markdown.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCKS_JS = ROOT / "frontend" / "features" / "studyflow" / "courses-blocks.js"
SOURCE = BLOCKS_JS.read_text(encoding="utf-8")


def _embed_body() -> str:
    start = SOURCE.index("async function _embedPdfBlock")
    return SOURCE[start:start + 3000]


def test_embedding_uses_an_absolute_index_not_an_offset():
    """`indexOffset` is silently ignored by the endpoint; `index` is honoured."""
    body = _embed_body()
    assert "index:" in body, "the move call must send an absolute `index`"
    assert "indexOffset" not in body, (
        "the move endpoint reads `index`; `indexOffset` is ignored, which is "
        "why the block used to land in the wrong place"
    )


def test_placement_advances_past_occupied_slots():
    """A taken slot must be stepped over rather than duplicated."""
    body = _embed_body()
    assert "while (list.some" in body, (
        "the target index must be walked forward while the slot is occupied"
    )


def test_placement_starts_directly_below_the_source():
    body = _embed_body()
    assert "sourceIdx + 1" in body, "the first slot tried is the one below the markdown"


def test_move_block_is_imported():
    """Without the import the repositioning call is a silent no-op."""
    assert re.search(
        r"listTopicBlocks,\s*fetchCourseDetail,\s*moveBlock", SOURCE
    ), "moveBlock must be destructured from CoursesAPI or the call does nothing"


def test_the_helper_does_not_use_the_captured_closure_ids():
    """Its parameters are named domCourse/domTopic so they cannot be confused
    with the stale `courseId` captured by the click handler's closure — that
    one would write into the previously viewed course."""
    body = _embed_body()
    assert "async function _embedPdfBlock(domCourse, domTopic" in body
    assert "updateBlock(courseId," not in body
    assert "addBlock(courseId," not in body


def test_existing_ref_below_the_source_is_reused():
    """Re-exporting must not stack a second identical block."""
    body = _embed_body()
    assert 'existing.url === url' in body, (
        "the block already sitting below the markdown is the one to refresh"
    )
    assert "updateBlock(domCourse, existing.id" in body


def test_a_pdf_the_user_added_is_not_overwritten():
    """Only a ref this button created may be rewritten, never the user's own."""
    body = _embed_body()
    assert "existing.url === url" in body, (
        "the reuse branch must be gated on the url matching, so a PDF the user "
        "added by hand is never repointed at a freshly generated file"
    )


def test_pdf_ref_matches_the_shape_of_the_users_own_pdfs():
    """Stored PDFs use url = /api/pdf/<id>; anything else will not open."""
    body = _embed_body()
    assert 'const url = `/api/pdf/${pdf.id}`;' in body
    assert 'type: "pdf-ref"' in body