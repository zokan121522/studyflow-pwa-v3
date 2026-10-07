"""Regressions for two bugs that only exist in the browser.

Both were found by creating a session through the UI, not by any backend test.
The suite could stay green indefinitely while a user silently lost the time
they had just entered, because nothing below the HTTP layer looks at
frontend JS.

1. `_getTimerSeconds` ignored its argument. It was declared with no parameter
   and hardcoded the "so-te" fields, but both call sites passed a prefix
   ("so-te", "so-tp"). Every session therefore stored a pause duration equal to
   its elapsed time, and since effective time is computed as
   `elapsed - paused`, it was always zero and the "Efectivas" stat read 0m.

2. The create path never sent the timer fields at all. Only editing an existing
   session persisted them, so entering effective time while *adding* a session
   threw it away with no error and no warning.

These are checked by reading the source rather than by driving a browser: the
browser round-trip was done by hand and is what found them, and asserting on
the text keeps the check runnable in CI where there is no browser. The comment
in each file explains the why, so a future edit that reintroduces the
parameterless signature fails here rather than in someone's study statistics.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
UI_COMMON = REPO / "frontend" / "shared" / "ui-common.js"
AGENDA_SESSION = REPO / "frontend" / "features" / "agenda" / "agenda-session.js"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_get_timer_seconds_accepts_a_prefix():
    """The signature has to take the prefix, or both callers read the same fields."""
    body = re.search(
        r"function _getTimerSeconds\(([^)]*)\)\s*\{(.*?)\n\}",
        _source(UI_COMMON),
        re.S,
    )
    assert body, "_getTimerSeconds not found in ui-common.js"
    params, source = body.group(1), body.group(2)

    assert params.strip(), (
        "_getTimerSeconds takes no parameter, so _getTimerSeconds('so-tp') "
        "silently reads the effective-time fields and pause time is lost"
    )

    # Every id lookup must go through the prefix, not a literal "so-te".
    lookups = re.findall(r"getElementById\(([^)]*)\)", source)
    assert lookups, "no field lookups found in _getTimerSeconds"
    for lookup in lookups:
        assert "so-te" not in lookup, (
            f"_getTimerSeconds hardcodes {lookup} instead of using its prefix"
        )


def test_both_timer_prefixes_are_read_at_the_call_sites():
    """Sanity check that the prefixes in use match the fields in index.html.

    A typo here ("so-tp" vs "so-pt") is just as silent as the original bug: the
    lookup returns undefined, `|| 0` swallows it, and the pause is zero.
    """
    html = (REPO / "frontend" / "index.html").read_text(encoding="utf-8")
    source = _source(AGENDA_SESSION)
    for prefix in ("so-te", "so-tp"):
        assert f'_getTimerSeconds("{prefix}")' in source, (
            f"the {prefix} fields are never read at the save call site"
        )
        for unit in ("h", "m", "s"):
            assert f'id="{prefix}-{unit}"' in html, (
                f"{prefix}-{unit} is read by the code but absent from index.html"
            )


def test_creating_a_session_sends_the_timer_fields():
    """Both save paths must persist the same fields.

    Asserted on the whole file because the create and edit branches are two
    separate object literals a few lines apart, which is exactly the kind of
    duplication where one gets updated and the other does not.
    """
    source = _source(AGENDA_SESSION)
    for field in ("timer_elapsed", "timer_paused_duration"):
        assert source.count(field) >= 3, (
            f"{field} appears {source.count(field)} times; the create branch is "
            "probably missing it, so time entered when adding a session is dropped"
        )


def test_effective_time_is_derived_from_elapsed_minus_paused():
    """The identity that made bug 1 visible as "Efectivas 0m".

    Guards the arithmetic against being "fixed" the wrong way -- for instance by
    treating a pause as a reset rather than as a subtraction -- which would make
    the stat read plausibly while over-reporting study time.
    """
    core = (REPO / "frontend" / "features" / "agenda" / "agenda-core.js").read_text(
        encoding="utf-8"
    )
    # Located by statement rather than by one big regex: the file's formatting
    # (`var`, parenthesised `|| 0` defaults, line breaks) has changed more than
    # once, and a test that breaks on refactoring gets deleted instead of fixed.
    statement = next(
        (line for line in core.splitlines() if "effective" in line and "=" in line
         and "timer_elapsed" in line),
        None,
    )
    assert statement, "no line computes effective time from timer_elapsed"
    assert "timer_paused_duration" in statement, (
        "effective time ignores the pause duration"
    )
    # Order matters: elapsed - paused, not paused - elapsed.
    assert statement.index("timer_elapsed") < statement.index("timer_paused_duration"), (
        "effective time is computed as paused minus elapsed"
    )