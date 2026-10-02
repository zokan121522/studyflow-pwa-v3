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

from calendar_import.notifications import _human_day

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

# A deadline is not a class. Giving it "Grabar clase / Ver clase" told the
# user to record a lecture that does not exist, so the two kinds get their own
# header and their own checklist.
TASK_NOTES_TEMPLATE = "\n".join((
    "- [x] 📝 Pendiente",
    "- [ ] 🔄 En progreso",
    "- [ ] ✅ Hecha",
))

# Moodle's Spanish deadline titles all start this way; the rest is the work's
# own name, which is more useful as the body's headline than as a title the
# session already carries.
_TASK_TITLE_RE = re.compile(
    r"^\s*vencimiento\s+de\s+tarea\b\s*\d*\s*"
    r"(?:\([^)]*\))?\s*[.:\-\u2013\u2014]?\s*",
    re.IGNORECASE,
)
_RA_RE = re.compile(r"\(\s*RA\s*\d+\s*\)", re.IGNORECASE)

# "DAW - 0613. Desarrollo web en entorno servidor - OnLine"
#   -> "DAW · 0613. Desarrollo web en entorno servidor - OnLine"
_CATEGORY_DASHES = " - "


def is_task_event(summary: str) -> bool:
    """True when the event is a deadline rather than a class.

    Only the title decides it: on the real Digitech feed every deadline says
    "Vencimiento de …", which is one string to match and needs no guessing.
    """
    return bool(re.match(r"^\s*vencimiento\b", summary or "", re.IGNORECASE))


def clean_category(raw: str) -> str:
    """The course name Moodle puts in CATEGORIES, tidied for display.

    Every event in the feed carries it — 34 of 34 — and it is the only place
    the subject appears. The deadline titles do not name their subject at all,
    so without this a task is unattributable.
    """
    value = (raw or "").strip().strip('"')
    if not value:
        return ""
    # Digitech separates the module from the name with " - ". Keep the first
    # segment tight, drop the redundant trailing modality.
    value = re.sub(r"\s*-\s*OnLine\s*$", "", value, flags=re.IGNORECASE)
    parts = value.split(_CATEGORY_DASHES)
    if len(parts) >= 2:
        value = f"{parts[0].strip()} · {parts[1].strip()}"
    return value.strip(" ·")


def task_work_name(summary: str) -> str:
    """The work's own name, without Moodle's 'Vencimiento de TAREA 1 (RA1) -'.

    The (RA…) marker is preserved and moved to the end. It sits inside the
    stripped prefix on the real feed ("Vencimiento de TAREA 3 (RA2). …") and
    also inline in some titles, so it is captured from the prefix before the
    prefix is dropped and re-appended — it is the learning-result code and the
    user asked to keep it.
    """
    text = (summary or "").strip()
    prefix = _TASK_TITLE_RE.match(text)
    prefix_ra = _RA_RE.search(prefix.group(0)) if prefix else None
    if prefix:
        text = text[prefix.end():].strip()

    ra = _RA_RE.search(text)
    if ra and ra.start() > 0:
        code = ra.group(0)
        rest = (text[: ra.start()] + text[ra.end():]).strip(" .,-—")
        text = f"{rest} {code}".strip()

    if prefix_ra and prefix_ra.group(0) not in text:
        text = f"{text} {prefix_ra.group(0)}".strip()
    return text


def _tidy_description(text: str) -> str:
    """Moodle's description, with the empty scaffolding removed.

    Moodle pads class descriptions with blank runs and a 'Links:' block that
    duplicates the URL already shown above; task descriptions carry a
    trailing non-breaking space. None of it is information.
    """
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\xa0", " "))
    lines = [ln.rstrip() for ln in text.split("\n")]
    out = []
    for line in lines:
        # "Links:" and its rule only ever repeat the URL shown above.
        if line.strip().lower() in ("links:", "------", "-------", "---", "-----"):
            if out and out[-1] == "":
                out.pop()
            break
        out.append(line)
    return "\n".join(out).strip()


def build_task_notes(summary, description, subject, due_local):
    """Notes for an imported deadline: what it is, when, and a state.

    Order is deliberate — subject, deadline, then what the work is — because
    that is the order the user reads it in when opening the session.
    """
    lines = []
    if subject:
        lines.append(f"📚 {subject}")
    if due_local:
        lines.append(f"⏰ Entrega: {_human_day(due_local.date())} · {due_local:%H:%M}")
    work = task_work_name(summary)
    if work:
        lines.append(f"📄 {work}")
    desc = (description or "").strip()
    if desc:
        lines.append("")
        lines.append(_tidy_description(desc))
    lines.append("")
    lines.append(TASK_NOTES_TEMPLATE)
    return "\n".join(lines)


def build_class_notes(summary, description, subject, location):
    """Notes for an imported class, with the Teams link when the feed has it.

    LOCATION is read because 6 of the 24 real classes carry their join link
    there and an empty DESCRIPTION, and those classes were landing with no way
    in. Only used when there is nothing of the user's own to preserve.
    """
    desc = (description or "").strip()
    link = (location or "").strip()
    # Pull any URL out before dropping the 'Links:' block: Moodle repeats the
    # join link there, and if LOCATION is empty that block is the only copy.
    # Tearing it out and losing the link would leave a class with no way in.
    desc_links = re.findall(r"https?://\S+", desc)
    desc = _tidy_description(desc)
    if not link:
        link = next((u.rstrip(".,") for u in desc_links), "")
    lines = []
    if subject:
        lines.append(f"📚 {subject}")
    if link:
        lines.append(f"🔗 {link}")
    if desc:
        lines.append("")
        lines.append(desc)
    # The link is already on its own line above; repeating it inside the
    # description reads as noise.
    if link and desc:
        desc = "\n".join(ln for ln in desc.split("\n") if link not in ln).strip()
    while lines and lines[0] == "":
        lines.pop(0)
    if not lines:
        return CLASS_NOTES_TEMPLATE
    lines.append("")
    lines.append(CLASS_NOTES_TEMPLATE)
    return "\n".join(lines)


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
    desc_str = desc_str.replace("\\n", "\n").replace("\\,", ",")
    desc_str = desc_str.replace("\xa0", " ").strip()

    subject = clean_category(_component_text(component, "CATEGORIES"))
    location = strip_html(_component_text(component, "LOCATION"))

    if is_task_event(summary):
        # The deadline instant is DTSTART; a task has no duration, so it is the
        # moment that matters, not a span.
        notes = build_task_notes(summary, desc_str, subject, dtstart_local)
    else:
        notes = build_class_notes(summary, desc_str, subject, location)

    if len(notes) > NOTES_MAX_CHARS:
        notes = notes[:NOTES_MAX_CHARS - 3] + "..."

    return {
        "session_id": session_id,
        "day_date": dtstart_local.date().isoformat(),
        "start_time": dtstart_local.strftime("%H:%M"),
        "end_time": dtend_local.strftime("%H:%M"),
        "summary": summary,
        "notes": notes,
        "subject": subject,
    }


def _untext(value):
    """Plain str from an icalendar vText/vCategory, whatever it wraps."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if hasattr(value, "to_ical"):
        raw = value.to_ical()
        if isinstance(raw, bytes):
            return raw.decode("utf-8", "replace")
        return str(raw)
    return str(value)


def _component_text(component, name):
    """A VEVENT property as plain text, or '' when absent.

    icalendar may hand back a vText or a list depending on the property and
    the library version, so both are normalised here rather than at each call
    site.
    """
    value = component.get(name)
    if value is None:
        return ""
    # icalendar wraps CATEGORIES in vCategory([vText(b'...')]) and its
    # repr leaks into the notes when the text is taken with str().
    if hasattr(value, "cats"):
        value = value.cats
    if isinstance(value, list):
        value = ", ".join(
            _untext(item) for item in value if item is not None
        )
    return _untext(value)