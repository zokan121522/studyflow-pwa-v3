"""Guards for the session-card action row.

Regression from the user's own report: starting a session and pressing stop
made the delete / move / copy buttons vanish.

The cause was not a lock on the buttons themselves. `.btn-stop` patches the
session to `state="completed"`, and `_buildActionsHtml` had an early `return`
for completed and cancelled rows that replaced the *entire* row with a bare
"✅ Hecho" / "⏹ Cancelado" span. So stopping the timer — the most ordinary way
to finish a session — silently removed every action, including the three that
remain meaningful once a session is done.

What is load-bearing now:
  * settled sessions still show delete / move / copy;
  * only the timer controls are disabled, because a running clock is the one
    thing that genuinely does not apply to a finished session.

Static source assertions, in line with the rest of the suite: CI has no
browser, and what regressed was the *source*, not the runtime behaviour.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

AGENDA_CORE_JS = ROOT / "frontend" / "features" / "agenda" / "agenda-core.js"


def _actions_html_fn() -> str:
    src = AGENDA_CORE_JS.read_text(encoding="utf-8")
    start = src.find("function _buildActionsHtml")
    assert start != -1, "_buildActionsHtml is missing from agenda-core.js"
    # The function ends at the next top-level definition.
    rest = src[start + 10:]
    end = rest.find("\n  function ")
    return rest[:end] if end != -1 else rest


def test_no_early_return_swallows_the_action_row():
    """A completed/cancelled row must not `return` before building the buttons.

    This is the exact shape of the bug: `return '<span>✅ Hecho</span>'` meant
    the buttons below it were unreachable for every settled session.
    """
    fn = _actions_html_fn()
    early_returns = [
        m for m in re.findall(r"return\s+'[^']*Hecho[^']*'|return\s+'[^']*Cancelado[^']*'", fn)
    ]
    assert not early_returns, (
        "_buildActionsHtml must not return the status label on its own — that "
        "deletes the action row for completed sessions. Found: %r" % early_returns
    )
    assert "return statusTag +" in fn, (
        "the status label must be a prefix of the button markup, not a replacement"
    )


def test_settled_sessions_keep_delete_move_copy():
    """delete / move / copy must still render once a session is done."""
    fn = _actions_html_fn()
    for cls in ("btn-del", "btn-move", "btn-copy"):
        assert cls in fn, (
            "%s must still be rendered for completed/cancelled sessions — "
            "tidy-up is most often wanted *after* finishing" % cls
        )
    # moveDelDisabled is the only gate that ever touched these two, and it keys
    # off the timer, not off session state.
    assert re.search(
        r"var moveDelDisabled = \(ts === \"running\" \|\| ts === \"paused\"\)", fn
    ), (
        "delete/move must stay disabled only while the clock runs; keying this "
        "on session state is what hid the buttons after stopping the timer"
    )
    assert not re.search(r"moveDelDisabled = .*settled", fn), (
        "delete/move must not be disabled merely because a session is settled"
    )


def test_timer_buttons_are_disabled_when_settled():
    """The timer controls are the only ones that stop applying once done."""
    fn = _actions_html_fn()
    assert re.search(
        r"var playDisabled\s+= \(settled \|\| ts === \"running\"\)", fn
    ), "play must be disabled on a settled session"
    assert re.search(
        r"var pauseDisabled = \(settled \|\| ts !== \"running\"\)", fn
    ), "pause must be disabled on a settled session"
    assert re.search(
        r"var stopDisabled\s+= \(settled \|\|", fn
    ), "stop must be disabled on a settled session"


def test_status_tag_covers_both_settled_states():
    """Both completed and cancelled keep a visible label next to the buttons."""
    fn = _actions_html_fn()
    assert "✅ Hecho" in fn, "completed sessions must still be labelled"
    assert "⏹ Cancelado" in fn, "cancelled sessions must still be labelled"
