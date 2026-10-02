# Folding blocks in the body.
#
# Two separate defects live here, and the second only becomes visible once
# the first is fixed:
#
#   1. There was no way to fold a whole topic at once — only a per-card arrow.
#   2. The pencil could not edit a folded card at all. `.is-collapsed
#      .sf-bc-body { display: none }` hides the body, and the editor is
#      rendered *inside* that body, so the pencil produced a form of height 0.
#      Same dead-button symptom as the stale-closure bug, and it would have
#      come straight back the moment blocks were folded by default.

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCKS = ROOT / "frontend/features/studyflow/courses-blocks.js"
COURSES = ROOT / "frontend/features/studyflow/courses.js"
CSS = ROOT / "frontend/features/studyflow/studyflow-blocks.css"


def _src(path):
    return path.read_text(encoding="utf-8")


def test_the_hidden_body_is_what_made_the_pencil_dead():
    """
    Pins the reason the expand-on-edit is mandatory. If this rule ever goes
    away the fix below becomes dead code — harmless, but then it should be
    deleted rather than left behind as a superstition.
    """
    assert ".sf-block-card.is-collapsed .sf-bc-body { display: none; }" in _src(CSS)


def test_editing_a_folded_card_unfolds_it_first():
    """
    The editor lives in the hidden body, so fold must be lifted before open.

    The element matters as much as the action. `is-collapsed` is on the
    .sf-block-card; _ctxFrom's `blockEl` is the inner .sf-td-block. Unfolding
    blockEl is a silent no-op — which is exactly what the first attempt at
    this fix did, and the tests passed because they only checked that
    classList.remove appeared somewhere.
    """
    src = _src(BLOCKS)
    handler = re.search(r"// Edit\n(.*?)\n      // Save", src, re.S)
    assert handler, "edit handler not found"
    fn = handler.group(1)
    # The unfold must act on the card, never on blockEl.
    unfold = re.search(
        r"(\w+)\.classList\.remove\(\"is-collapsed\"\)", fn)
    assert unfold, "must unfold the card"
    assert unfold.group(1) != "blockEl", \
        "is-collapsed is on .sf-block-card; blockEl is the inner .sf-td-block"
    assert re.search(r"const wasCollapsed = !!\(\s*cardEl && cardEl\.classList", fn, re.S), \
        "the folded check must read the card, not the inner block element"
    assert 'card: cardEl' in fn or "card: card" in fn, \
        "the handler must pull the card out of the ctx"
    assert fn.index('remove("is-collapsed")') < fn.index("_renderEditForm"), \
        "unfold before the editor is rendered, not after"


def test_ctx_reports_the_card_separately_from_the_inner_block():
    """
    ctx.blockEl and ctx.card are different elements, and conflating them is
    what made the unfold above a no-op. Pin the distinction.
    """
    src = _src(BLOCKS)
    fn = re.search(r"function _ctxFrom\(.*?\n  \}", src, re.S).group(0)
    assert 'const card = btn.closest(".sf-block-card");' in fn
    assert 'btn.closest(".sf-td-block")' in fn
    assert re.search(r"return \{[^}]*\bcard: card \|\| blockEl", fn, re.S), \
        "ctx must expose the card alongside blockEl"


def test_editing_a_folded_card_persists_the_unfold():
    """
    Without this the card snaps back to folded on the next render, and the
    user reads that as the pencil randomly failing again.
    """
    src = _src(BLOCKS)
    assert re.search(r"if \(wasCollapsed\)\s*\{\s*try \{\s*await updateBlock\([^)]*collapsed: false",
                     src, re.S), "the unfold must be saved, not just visual"


def test_fold_all_control_exists_and_acts_on_the_whole_topic():
    src = _src(COURSES)
    assert 'class="sf-td-fold-all ht-btn"' in src, "no fold-all control in the topic header"
    handler = re.search(r"const foldAllBtn = centerEl\.querySelector\(\"\.sf-td-fold-all\"\);(.*?)\n    \}\n", src, re.S)
    assert handler, "fold-all click handler not found"
    fn = handler.group(1)
    assert 'querySelectorAll(".sf-block-card")' in fn, "must cover every card, not just the first"
    assert re.search(r"await updateBlock\(cId, bid, \{ collapsed: targetCollapsed \}\)", fn), \
        "must go through the API so the flag survives a reload"


def test_fold_all_label_reflects_the_current_state():
    """A control that always says 'plegar' when everything is already folded is a lie."""
    src = _src(COURSES)
    assert "const allCollapsed = allBlocks.length > 0 && allBlocks.every((b) => !!b.collapsed)" in src
    assert '${allCollapsed ? "☰ Desplegar todo" : "☰ Plegar todo"}' in src


def test_fold_all_reads_the_whole_topic_not_the_filtered_view():
    """
    When a single block is focused the view shows one card. Folding 'all'
    from there would silently skip the other blocks, and the label would be
    computed from one block — so the button is hidden in that view.
    """
    src = _src(COURSES)
    assert re.search(r"allBlocks\.every\(\(b\) => !!b\.collapsed\)", src), \
        "label must consider every block in the topic"
    assert re.search(r": \(allBlocks\.length > 1", src), \
        "fold-all belongs to the full-topic view only"


def test_fold_all_folds_if_anything_is_open():
    """
    'Fold' when at least one card is open. Treating a mixed topic as 'already
    folded' would make the first click do nothing visible.
    """
    src = _src(COURSES)
    fn = re.search(r"const foldAllBtn = .*?(?=\n    // Phase)", src, re.S).group(0)
    assert re.search(
        r"cards\.some\(\(c\) => !c\.classList\.contains\(\"is-collapsed\"\)\)", fn
    ), "must fold when anything is still open"


def test_creation_still_defaults_to_expanded():
    """
    Guard rail, not an endorsement. New blocks are created expanded so that
    adding one is visibly acknowledged. Folding them at birth is also unsafe
    today: _enterEditMode() is a no-op, so the sidebar's add-block menu never
    opens the editor and a folded block would give no sign of existing. Once
    _enterEditMode() actually opens the editor, this default can be revisited.
    """
    api = _src(ROOT / "backend/routes/blocks.py")
    inserts = re.findall(r"bool\(payload\.get\('collapsed', (\w+)\)\)", api)
    assert len(inserts) == 2, f"expected both insert paths, found {inserts}"
    assert set(inserts) == {"False"}, f"creation default changed to {inserts}"


def test_enter_edit_mode_is_still_a_noop():
    """
    Documents the reason for the guard rail above. When somebody implements
    this function, the folded-creation default becomes safe and this test
    should be updated in the same commit — not silently left failing or,
    worse, quietly deleted.
    """
    src = _src(COURSES)
    fn = re.search(r"async function _enterEditMode\(blockId\) \{(.*?)\n  \}", src, re.S).group(1)
    body = re.sub(r"//.*", "", fn).strip()
    assert body == "", (
        "_enterEditMode now has a body — new blocks can safely be created "
        "folded. Update test_creation_still_defaults_to_expanded and this test."
    )
