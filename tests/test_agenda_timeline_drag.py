"""Regression guard: the agenda timeline drag handlers must stay wired.

bindDrag (day) and bindWeekDrag (week) were defined and exported by the
timeline modules but never called by anything, so dragging a session in
those views silently did nothing. Only bindMonthDrag was wired, inside
agenda-month-view.js. This test fails if a view's binder is defined but
never invoked again.

It reads the source instead of driving a browser: a headless drag would
pass even if the handler silently no-ops, whereas the actual defect was
"nothing calls this".
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "features" / "agenda"
CORE = FRONTEND / "agenda-core.js"
MONTH_VIEW = FRONTEND / "agenda-month-view.js"
TIMELINE = FRONTEND / "agenda-timeline.js"
TIMELINE_WEEK = FRONTEND / "agenda-timeline-week.js"


def _src(path: Path) -> str:
    return path.read_text()


def _calls(path: Path, name: str) -> int:
    """Count real invocations of `name(...)`, ignoring the definition/export."""
    src = _src(path)
    hits = re.findall(r"\b" + name + r"\s*\(", src)
    # drop the "function name(" definition and any "name =" assignment
    body = re.sub(r"function\s+" + name + r"\s*\(", "", src)
    body = re.sub(r"\b" + name + r"\s*=\s*", "", body)
    return len(re.findall(r"\b" + name + r"\s*\(", body))


def test_bind_week_drag_is_invoked():
    assert _calls(TIMELINE_WEEK, "bindWeekDrag") == 0, "definition/export only"
    assert _calls(CORE, "bindWeekDrag") >= 1, (
        "bindWeekDrag is exported but nothing calls it: week drag is dead code"
    )


def test_bind_drag_is_invoked():
    assert _calls(TIMELINE, "bindDrag") == 0, "definition/export only"
    assert _calls(CORE, "bindDrag") >= 1, (
        "bindDrag is exported but nothing calls it: day drag is dead code"
    )


def test_bind_month_drag_stays_wired():
    """The month view was the only one wired; keep it that way."""
    assert _calls(MONTH_VIEW, "bindMonthDrag") >= 1


def test_core_wires_drag_after_each_render():
    """The binder must run after innerHTML is set, or it binds a dead node."""
    src = _src(CORE)
    assert src.count("el.innerHTML = centerHtml;") == 1
    inner = src.index("el.innerHTML = centerHtml;")
    wire = src.index("_wireTimelineDrag(el, onRefresh)")
    assert wire > inner, "drag must be wired after the render replaces innerHTML"


def test_document_drag_listeners_are_registered_once():
    """bindPointerEvents puts move/up on document; re-adding per render leaks."""
    src = _src(TIMELINE)
    for listener in ("mousemove", "mouseup", "touchmove", "touchend", "touchcancel"):
        # exactly one registration inside _bindDocumentDragOnce, guarded by a flag
        count = src.count('document.addEventListener("%s"' % listener)
        assert count == 1, (
            "%s is registered %d times on document; it must be bound once"
            % (listener, count)
        )
    assert "_docDragBound" in src, "the once-guard flag is missing"
    assert re.search(r"if \(_docDragBound\) return;", src), "guard is not checked before binding"