"""VEVENT → session fields. Pure: no Flask, no database, no network.

Lives in the feature package rather than in routes/calendar.py so the
dependency runs the right way — the HTTP layer depends on this, not the
other way round. routes/calendar.py imported its own copy of this logic for
a long time; that copy drifted and the class-notes template reached the
scheduled import without reaching the manual one, so _build_event_fields
there now delegates to build_event_fields instead of repeating it.

Behaviour here is pinned by tests/test_calendar_characterization.py. Three
things are surprising and are deliberately NOT tidied up in this pass:

- the UID is sanitised with [^A-Za-z0-9-], so `46102@campus.digitechfp.com`
  becomes `cal-46102campusdigitechfpcom`, and `a.b@x` collides with `ab@x`;
- a floating time (no `Z`, no `TZID`) is stamped UTC and then shifted, though
  RFC 5545 calls it local. Moodle sends explicit `Z`, so the real feed is fine;
- VALUE=DATE all-day events are not dropped, they land at 02:00–02:00.

Changing any of them means renumbering existing `cal-*` session ids or
reinterpreting stored times, which is a migration and a decision, not a
cleanup.
"""

import re
import uuid
from datetime import date, datetime, timezone

UID_SANITISE_RE = re.compile(r"[^A-Za-z0-9-]")

_HTML_TAG_RE = re.compile(r"<[^>]*>")
_HTML_DANGEROUS_BODY_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_HTML_SAFE_ENTITIES = (
    ("&amp;", "&"), ("&nbsp;", " "),
    ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"),
)

NOTES_MAX_CHARS = 800

# The checklist every imported class session opens with. Digitech's own
# events carry either nothing or Moodle's "Unirse a la reunión en Teams"
# boilerplate, so an import used to land with an empty description and no
# way to tick anything off. This is the note the class actually needs.
CLASS_NOTES_TEMPLATE = "\n".join((
    "- [ ] Grabar clase.",
    "- [ ] Resumen en markdown, audios, infografias, ejercicios ...",
    "- [ ] Ver clase",
    "- [ ] Ver Resumen",
))


def build_import_notes(description: str) -> str:
    """Notes for an imported event: the checklist, then whatever it brought.

    The description is kept rather than replaced because on a real Digitech
    feed it is the only thing carrying the Teams join link, and dropping it
    would take away the way into the class. Empty description is the common
    case and becomes the checklist on its own.

    This runs only inside the import paths, so sessions created by hand are
    never touched by it.
    """
    desc = (description or "").strip()
    if not desc:
        return CLASS_NOTES_TEMPLATE
    return f"{CLASS_NOTES_TEMPLATE}\n\n{desc}"


def strip_html(value):
    if not value:
        return ""
    out = _HTML_DANGEROUS_BODY_RE.sub("", value)
    out = _HTML_TAG_RE.sub("", out)
    for entity, char in _HTML_SAFE_ENTITIES:
        out = out.replace(entity, char)
    out = out.replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return out.strip()


def normalise_dt(value):
    """Coerce an icalendar DT value to an aware UTC datetime.

    Naive datetimes are stamped UTC. For Moodle that is correct — it sends
    `Z` — but a floating time is really local, hence the quirk documented
    in the module docstring.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    raise ValueError(f"unsupported DT value: {value!r}")


def extract_event_times(component, local_tz):
    """Return (start, end) in `local_tz`, or None when the event is unusable.

    None covers a missing DTSTART/DTEND. It does NOT cover an all-day event:
    see the module docstring.
    """
    try:
        dtstart_raw = component.decoded("DTSTART")
        dtend_raw = component.decoded("DTEND")
    except (KeyError, AttributeError):
        return None
    try:
        return (
            normalise_dt(dtstart_raw).astimezone(local_tz),
            normalise_dt(dtend_raw).astimezone(local_tz),
        )
    except Exception:
        return None


def build_event_fields(component, dtstart_local, dtend_local):
    """Session row values for one VEVENT, or None when it has no UID.

    A UID is the only identity an event has, so without one it cannot be
    deduplicated and is dropped.
    """
    raw_uid = str(component.get("UID") or "")
    if not raw_uid:
        return None
    safe_uid = UID_SANITISE_RE.sub("", raw_uid) or uuid.uuid4().hex
    session_id = f"cal-{safe_uid}"

    summary = strip_html(component.get("SUMMARY")) or "Sin título"
    desc_raw = component.get("DESCRIPTION")
    desc_str = strip_html(str(desc_raw) if desc_raw is not None else "")
    desc_str = desc_str.replace("\\n", "\n").strip()
    notes = build_import_notes(desc_str)
    if len(notes) > NOTES_MAX_CHARS:
        notes = notes[:NOTES_MAX_CHARS - 3] + "..."

    return {
        "session_id": session_id,
        "day_date": dtstart_local.date().isoformat(),
        "start_time": dtstart_local.strftime("%H:%M"),
        "end_time": dtend_local.strftime("%H:%M"),
        "summary": summary,
        "notes": notes,
    }