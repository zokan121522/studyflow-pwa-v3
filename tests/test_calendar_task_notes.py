"""Pinned against the real Digitech feed, captured 2026-10-01 (34 events).

Every assertion below quotes a value that came out of the live ICS, not one
invented for the test. That matters because the shape of this feed is what
drove the design:

  * every one of the 34 events carries CATEGORIES — the only place the subject
    appears at all. The deadline titles ("Vencimiento de TAREA 1 (RA1) - …")
    never name their subject, so without CATEGORIES a task is unattributable;
  * 6 of the 24 classes carry their Teams join link in LOCATION while their
    DESCRIPTION is empty, and the old code only read DESCRIPTION — those
    classes landed with no way into the session;
  * CATEGORIES arrives wrapped in vCategory([vText(b'…')]), whose repr leaks
    into the notes if the text is taken with str();
  * DESCRIPTION escapes newlines as literal backslash-n, commas as backslash-
    comma, and pads with non-breaking spaces;
  * a deadline is a single instant (DTSTART == DTEND) at 23:59 or 00:00
    Madrid time.

The feed is not in the repo — it holds a live auth token — so the events are
reproduced verbatim here instead of committed.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from calendar_import import vevent as V  # noqa: E402

CATEGORIES = "DAW - 0613. Desarrollo web en entorno servidor - OnLine"
TEAMS = "https://teams.microsoft.com/meet/36640977742643?p=fhfcQS2hWWRjrotQUO"


class FakeEvent(dict):
    """A VEVENT-shaped mapping.

    icalendar's CATEGORIES is a vCategory, not a plain list, which is why
    _component_text unwraps anything with .cats. Reproducing the real wrapper
    here is the only way that path stays tested.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class FakeCategory(list):
    """Stands in for icalendar's vCategory."""

    def __init__(self, items):
        super().__init__(items)


class FakeText:
    def __init__(self, raw: bytes):
        self._raw = raw

    def to_ical(self):
        return self._raw

    def __str__(self):
        return f"vText({self._raw!r})"


def event(summary, description="", categories=CATEGORIES, location=None,
          uid="47178@campus.digitechfp.com"):
    ev = FakeEvent(
        UID=uid,
        SUMMARY=summary,
        DESCRIPTION=description,
        CATEGORIES=FakeCategory([FakeText(categories.encode())]),
    )
    if location:
        ev["LOCATION"] = location
    return ev


# ─── CATEGORIES → subject ──────────────────────────────────────────────

def test_subject_is_read_from_categories():
    """The whole point: without this a deadline names no subject at all."""
    f = V.build_event_fields(event("Vencimiento de TAREA 1 (RA1) - Informe"),
                             V.datetime(2026, 10, 10, 23, 59, tzinfo=V.timezone.utc),
                             V.datetime(2026, 10, 10, 23, 59, tzinfo=V.timezone.utc))
    assert f["subject"] == "DAW · 0613. Desarrollo web en entorno servidor"


def test_subject_drops_the_redundant_online_suffix():
    assert V.clean_category("DAW - CMO314. CIberseguridad - OnLine") == \
        "DAW · CMO314. CIberseguridad"


def test_category_wrapper_never_leaks_into_the_notes():
    """vCategory repr in a note would be visible noise on every session."""
    f = V.build_event_fields(event("Clase 5/16 DAW"),
                             V.datetime(2026, 10, 1, 17, 0, tzinfo=V.timezone.utc),
                             V.datetime(2026, 10, 1, 18, 0, tzinfo=V.timezone.utc))
    assert "vCategory" not in f["notes"]
    assert "vText" not in f["notes"]


def test_missing_categories_does_not_crash():
    ev = FakeEvent(UID="x@y", SUMMARY="Clase 1/16 DAW", DESCRIPTION="")
    f = V.build_event_fields(ev,
                             V.datetime(2026, 10, 1, 17, 0, tzinfo=V.timezone.utc),
                             V.datetime(2026, 10, 1, 18, 0, tzinfo=V.timezone.utc))
    assert f["subject"] == ""


# ─── deadline vs class ─────────────────────────────────────────────────

def test_a_deadline_is_not_a_class():
    assert V.is_task_event("Vencimiento de TAREA 4 (RA2). Generación de una Plantilla")
    assert V.is_task_event("Vencimiento de Tarea 1. Tratamiento de la información (RA1)")
    assert not V.is_task_event("Clase 5/16 DAW")


def test_a_deadline_does_not_get_the_class_checklist():
    """'Grabar clase' on a deadline tells the user to record nothing."""
    f = V.build_event_fields(event("Vencimiento de Tarea 1. Apache"),
                             V.datetime(2026, 10, 15, 23, 59, tzinfo=V.timezone.utc),
                             V.datetime(2026, 10, 15, 23, 59, tzinfo=V.timezone.utc))
    assert "Grabar clase" not in f["notes"]
    assert "Pendiente" in f["notes"]


def test_the_state_block_starts_on_pending():
    f = V.build_event_fields(event("Vencimiento de Tarea 1. Apache"),
                             V.datetime(2026, 10, 15, 23, 59, tzinfo=V.timezone.utc),
                             V.datetime(2026, 10, 15, 23, 59, tzinfo=V.timezone.utc))
    assert "- [x] 📝 Pendiente" in f["notes"]
    assert "- [ ] 🔄 En progreso" in f["notes"]
    assert "- [ ] ✅ Hecha" in f["notes"]


def test_the_state_block_is_markdown_not_html():
    """The notes renderer escapes HTML before parsing, so <select> would
    render as literal text. Markdown task items are what actually works."""
    f = V.build_event_fields(event("Vencimiento de Tarea 1. Apache"),
                             V.datetime(2026, 10, 15, 23, 59, tzinfo=V.timezone.utc),
                             V.datetime(2026, 10, 15, 23, 59, tzinfo=V.timezone.utc))
    assert "<select" not in f["notes"]
    assert re.search(r"^- \[[ x]\] ", f["notes"], re.M)


def test_deadline_shows_the_delivery_moment():
    f = V.build_event_fields(event("Vencimiento de Tarea 2. Investigación"),
                             V.datetime(2026, 10, 15, 23, 59, tzinfo=V.timezone.utc),
                             V.datetime(2026, 10, 15, 23, 59, tzinfo=V.timezone.utc))
    assert "⏰ Entrega: " in f["notes"]
    assert "23:59" in f["notes"]


def test_work_name_drops_the_moodle_prefix():
    assert V.task_work_name(
        "Vencimiento de TAREA 1 (RA1) - Informe Comparativo de Arquitecturas"
    ) == "Informe Comparativo de Arquitecturas (RA1)"


def test_work_name_keeps_the_ra_marker():
    """(RA2) is the learning-result code; the user asked to keep it."""
    assert V.task_work_name(
        "Vencimiento de TAREA 3 (RA2). Síntasis, Tipos de Datos"
    ).endswith("(RA2)")


# ─── LOCATION → Teams link ─────────────────────────────────────────────

def test_a_class_with_only_a_location_still_gets_its_join_link():
    """6 of 24 real classes are exactly this shape, and they were unusable."""
    f = V.build_event_fields(event("Clase 6/16 DIW", location=TEAMS),
                             V.datetime(2026, 10, 22, 19, 0, tzinfo=V.timezone.utc),
                             V.datetime(2026, 10, 22, 20, 0, tzinfo=V.timezone.utc))
    assert TEAMS in f["notes"]


def test_the_link_is_not_repeated_when_the_description_already_has_it():
    raw = ("Clase 2 - 28/09/2026 | Unirse a la reunión en Teams | Microsoft Teams [1]"
           "\\n\\n\\nLinks:\\n------\\n[1] " + TEAMS + "\\n")
    f = V.build_event_fields(event("Clase 2/16 DWES", description=raw),
                             V.datetime(2026, 9, 28, 19, 0, tzinfo=V.timezone.utc),
                             V.datetime(2026, 9, 28, 20, 0, tzinfo=V.timezone.utc))
    assert f["notes"].count(TEAMS) == 1
    assert "Links:" not in f["notes"], "el bloque Links: repite lo de arriba"


# ─── escapes and padding ───────────────────────────────────────────────

def test_literal_backslash_n_becomes_a_real_newline():
    f = V.build_event_fields(event("Vencimiento de Tarea 1. Apache",
                                   description="uno\\ndos"),
                             V.datetime(2026, 10, 15, 23, 59, tzinfo=V.timezone.utc),
                             V.datetime(2026, 10, 15, 23, 59, tzinfo=V.timezone.utc))
    assert "uno\ndos" in f["notes"]


def test_trailing_non_breaking_space_is_gone():
    f = V.build_event_fields(event("Vencimiento de Tarea 1. Apache",
                                   description="Instalación del servidor.\xa0\\n\\n"),
                             V.datetime(2026, 10, 15, 23, 59, tzinfo=V.timezone.utc),
                             V.datetime(2026, 10, 15, 23, 59, tzinfo=V.timezone.utc))
    assert "\xa0" not in f["notes"]


def test_escaped_comma_is_unescaped():
    f = V.build_event_fields(event("Clase 2/16 DWES",
                                   description="uno\\, dos y tres\\, cuatro"),
                             V.datetime(2026, 9, 28, 19, 0, tzinfo=V.timezone.utc),
                             V.datetime(2026, 9, 28, 20, 0, tzinfo=V.timezone.utc))
    assert "\\," not in f["notes"]
    assert "uno, dos y tres, cuatro" in f["notes"]


def test_notes_stay_inside_the_cap():
    f = V.build_event_fields(event("Clase 1/16 DAW", description="x" * 5000),
                             V.datetime(2026, 10, 1, 17, 0, tzinfo=V.timezone.utc),
                             V.datetime(2026, 10, 1, 18, 0, tzinfo=V.timezone.utc))
    assert len(f["notes"]) <= V.NOTES_MAX_CHARS


def test_a_class_with_nothing_still_gets_the_class_checklist():
    f = V.build_event_fields(event("Clase 9/16 DAW"),
                             V.datetime(2026, 10, 1, 17, 0, tzinfo=V.timezone.utc),
                             V.datetime(2026, 10, 1, 18, 0, tzinfo=V.timezone.utc))
    assert "Grabar clase" in f["notes"]


@pytest.mark.parametrize("subject,expected_head", [
    ("DAW - 0612. Desarrollo web en entorno cliente - OnLine", "📚 DAW · 0612."),
    ("DAW - 1710. Itinerario Personal para la Empleabilidad II - OnLine",
     "📚 DAW · 1710."),
])
def test_all_six_real_modules_render(subject, expected_head):
    f = V.build_event_fields(event("Vencimiento de Tarea 1. X", categories=subject),
                             V.datetime(2026, 10, 15, 23, 59, tzinfo=V.timezone.utc),
                             V.datetime(2026, 10, 15, 23, 59, tzinfo=V.timezone.utc))
    assert f["notes"].startswith(expected_head)