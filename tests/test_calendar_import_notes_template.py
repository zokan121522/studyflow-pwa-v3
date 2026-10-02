"""Notes on an imported class session.

The bug this covers: Digitech's feed carries either no DESCRIPTION or
Moodle's "Unirse a la reunión en Teams" boilerplate, so an imported class
session arrived with an empty description and no way to tick anything off.
The user keeps a four-item checklist in the event template in Digitech and
expected it to come across with the import.

Two halves, and the second is the one that protects the first:

- the import composes the checklist onto whatever the event brought;
- the upsert refuses to overwrite notes that are already there, so a
  scheduled re-sync cannot wipe a tick the user made.

That second half used to be `notes = EXCLUDED.notes`. It is load-bearing:
with the template in place and the old SQL still there, every nightly sync
would have replaced the checklist with a fresh copy and silently undone
every tick. Fixing only the template would have looked correct for exactly
one day.

Real icalendar components throughout, no hand-rolled doubles — the bugs in
this area have all come from assuming what icalendar hands back.
"""

import inspect
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from icalendar import Calendar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from calendar_import.importer import _UPSERT_ICS_SESSION_SQL  # noqa: E402
from calendar_import.vevent import (  # noqa: E402
    CLASS_NOTES_TEMPLATE,
    build_class_notes,
    build_event_fields,
)
from routes.calendar import _build_event_fields  # noqa: E402
from routes import calendar as routes_calendar  # noqa: E402

ITEM = re.compile(r"^- \[[ xX]\] ", re.M)

WHEN = datetime(2026, 9, 29, 19, 0, tzinfo=timezone.utc)


def vevent(**lines):
    """A one-VEVENT calendar built from raw ICS property lines.

    DESCRIPTION is omitted entirely when not passed, because "absent" and
    "present but empty" are different things on a real feed and the bug
    report is about the absent case.
    """
    body = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\nBEGIN:VEVENT\r\n"
    body += "".join(f"{k}:{v}\r\n" for k, v in lines.items())
    body += "END:VEVENT\r\nEND:VCALENDAR\r\n"
    # the builder wants the VEVENT, not the VCALENDAR wrapped round it —
    # handing it the calendar makes it return None with no error
    return next(iter(Calendar.from_ical(body.encode()).walk("VEVENT")))


def _fields(description=None, summary="Clase 2/16 DIW"):
    lines = {"UID": "46113@campus.digitechfp.com", "SUMMARY": summary}
    if description is not None:
        lines["DESCRIPTION"] = description
    return build_event_fields(vevent(**lines), WHEN, WHEN)


# ── the checklist itself ────────────────────────────────────────────────────

def test_template_is_the_four_items_the_user_wrote():
    assert CLASS_NOTES_TEMPLATE.splitlines() == [
        "- [ ] Grabar clase.",
        "- [ ] Resumen en markdown, audios, infografias, ejercicios ...",
        "- [ ] Ver clase",
        "- [ ] Ver Resumen",
    ]


def test_every_template_line_is_a_tickable_task():
    """A box the preview cannot render is not a checklist.

    The note lands in the agenda card, which runs the same _renderMd, and
    only `- [ ]` / `- [x]` become a real checkbox there.
    """
    lines = CLASS_NOTES_TEMPLATE.splitlines()
    assert all(ITEM.match(line) for line in lines), CLASS_NOTES_TEMPLATE
    assert len(lines) == 4


# ── composition ─────────────────────────────────────────────────────────────

def test_absent_description_gets_the_checklist():
    """The actual bug: a Digitech class with no DESCRIPTION imported blank."""
    notes = _fields()["notes"]
    assert notes == CLASS_NOTES_TEMPLATE
    assert notes.strip(), "notes must not come back empty"


def test_empty_and_whitespace_description_gets_the_checklist():
    assert _fields(description="")["notes"] == CLASS_NOTES_TEMPLATE
    assert _fields(description="   \r\n  ")["notes"] == CLASS_NOTES_TEMPLATE


def test_existing_description_is_kept_and_the_checklist_stays():
    """The Teams text is the way into the class; it must survive."""
    notes = build_class_notes("Clase 2/16 DIW",
                              "Unirse a la reunión en Teams | Microsoft Teams",
                              "", "")
    assert "Unirse a la reunión en Teams | Microsoft Teams" in notes
    assert notes.endswith(CLASS_NOTES_TEMPLATE), (
        "la checklist va al final: es lo accionable; arriba va la información"
    )


def test_description_containing_checkboxes_is_not_double_templated():
    notes = build_class_notes("Clase 2/16 DIW", "- [ ] ya estaba en el evento",
                              "", "")
    assert notes.count("- [ ] Grabar clase.") == 1
    assert "- [ ] ya estaba en el evento" in notes


def test_checklist_survives_html_stripping_and_newline_unescaping():
    """Moodle sends HTML and literal \\n; neither may eat the checklist."""
    notes = _fields(description="Clase 2&lt;br&gt;\\nUnirse a Teams")["notes"]
    assert notes.endswith(CLASS_NOTES_TEMPLATE)
    assert "<br>" not in notes
    assert "\n" in notes
    assert "\\n" not in notes


def test_long_description_is_capped_but_keeps_the_checklist():
    notes = _fields(description="x" * 5000)["notes"]
    assert len(notes) <= 800
    assert notes.endswith("...")


def test_template_reaches_no_session_created_by_hand():
    """A session the user makes is never handed a checklist.

    The helper is only called from the two import paths; this asserts the
    normal session routes cannot reach it, so a future caller cannot quietly
    start stamping ticks onto sessions the user typed.
    """
    src = (ROOT / "backend" / "routes" / "agenda_sessions.py").read_text(encoding="utf-8")
    assert "build_class_notes" not in src
    assert "build_task_notes" not in src
    assert "CLASS_NOTES_TEMPLATE" not in src
    assert "TASK_NOTES_TEMPLATE" not in src


# ── the upsert must not clobber ─────────────────────────────────────────────

def _sql_without_comments(sql: str) -> str:
    """Strip `--` comments before asserting on the SQL.

    Not cosmetic: the comment explaining the old `notes = EXCLUDED.notes`
    wipe quotes that exact string, so an assertion over the raw text fails
    on its own explanation of the bug.
    """
    return re.sub(r"--[^\n]*", "", sql)


def test_scheduled_upsert_only_fills_empty_notes():
    sql = _sql_without_comments(_UPSERT_ICS_SESSION_SQL)
    assert "notes = EXCLUDED.notes" not in sql, (
        "this is the wipe: a re-sync replaces the notes with the feed's copy, "
        "discarding the user's ticks"
    )
    assert re.search(r"notes\s*=\s*CASE", sql, re.I)
    assert "btrim(sessions.notes) = ''" in sql


def test_manual_upsert_only_fills_empty_notes():
    sql = _sql_without_comments(routes_calendar._UPSERT_ICS_SESSION_SQL)
    assert "notes      = EXCLUDED.notes" not in sql
    assert re.search(r"notes\s*=\s*CASE", sql, re.I)
    assert "btrim(sessions.notes) = ''" in sql


# ── the two paths must not drift apart ──────────────────────────────────────

def test_both_import_paths_agree_on_the_notes():
    """The drift this file exists partly to prevent.

    The manual path used to carry a verbatim copy of the scheduled one, and
    the copy drifted: the template reached the scheduled import and not the
    manual one, so the same Digitech class imported two different ways gave
    two different notes.
    """
    for desc in (None, "", "Unirse a la reunión en Teams", "- [ ] ya estaba"):
        cal = vevent(UID="46113@campus.digitechfp.com", SUMMARY="Clase 2/16 DIW",
                     **({"DESCRIPTION": desc} if desc is not None else {}))
        scheduled = build_event_fields(cal, WHEN, WHEN)
        manual = _build_event_fields(cal, WHEN, WHEN)
        assert manual == scheduled, f"diverged for description={desc!r}"


def test_manual_path_delegates_instead_of_reimplementing():
    """Guards the delegation: a copied body would drift again, silently."""
    src = inspect.getsource(routes_calendar._build_event_fields)
    assert "build_event_fields(" in src
    assert "notes[:797]" not in src and "NOTES_MAX_CHARS" not in src
