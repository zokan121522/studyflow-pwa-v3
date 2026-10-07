"""
Calendar API — save ICS subscriptions + import VEVENTs into sessions.
Port from studyflow-hub v2.

Storage: calendars stored as JSON array in user_settings.calendars_json
(no Fernet in v3 — local single-user app, plain JSON is fine).

ICS import uses icalendar (already installed). When the URL is unreachable
we return {imported:0, updated:0, skipped_out_of_range:0} gracefully — the
import endpoint MUST NOT crash on transient network failures.

Blueprints register at /api prefix (see server.py), so routes are
declared WITHOUT /api.
"""
import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from flask import Blueprint, jsonify, request
from icalendar import Calendar

from backend import database as db
from backend.models import iso_week_key
from backend.routes.auth import token_required
from backend.routes.agenda import _upsert_week, _upsert_day
from backend.calendar_import.vevent import build_event_fields


logger = logging.getLogger(__name__)
bp = Blueprint("calendar", __name__)


# ─── Constants ─────────────────────────────────────────────────────
_MAX_CALENDARS = 5
_FETCH_TIMEOUT_S = 15
_FETCH_MAX_BYTES = 512 * 1024
# Single source of truth with calendar_import.window — 60 is the
# "2 meses" option. Validated by a test that the two agree.
_VALID_DAYS = (7, 15, 30, 60)
_LOCAL_TZ = "Europe/Madrid"
_UID_SANITISE_RE = re.compile(r"[^A-Za-z0-9-]")
_HTML_TAG_RE = re.compile(r"<[^>]*>")
_HTML_DANGEROUS_BODY_RE = re.compile(
    r"<\s*(script|style|iframe|object|embed)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_SAFE_ENTITIES = (
    ("&amp;", "&"), ("&nbsp;", " "),
    ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"),
)

_UPSERT_ICS_SESSION_SQL = """
INSERT INTO sessions (
    id, day_date, week_id, user_id, category, state,
    start_time, end_time, title, notes,
    timer_state, timer_started_at, timer_paused_at,
    timer_paused_duration, timer_elapsed,
    timer_total, timer_paused, position, updated_at
) VALUES (
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    'stopped', NULL, NULL,
    0, 0, NULL, 0, 0, NOW()
)
ON CONFLICT (id) DO UPDATE SET
    title      = EXCLUDED.title,
    -- Same rule as calendar_import/importer.py: the feed fills notes only
    -- while they are empty, so re-importing never discards what the user
    -- wrote or ticked. Keep the two in step — a test asserts the manual and
    -- scheduled paths agree on the notes for the same event.
    notes      = CASE
                     WHEN sessions.notes IS NULL OR btrim(sessions.notes) = ''
                     THEN EXCLUDED.notes
                     ELSE sessions.notes
                 END,
    day_date   = EXCLUDED.day_date,
    start_time = EXCLUDED.start_time,
    end_time   = EXCLUDED.end_time,
    updated_at = NOW()
RETURNING (xmax = 0) AS is_insert
"""


# ─── URL masking / HTML sanitisation ──────────────────────────────
def _mask_url(url: str) -> str:
    try:
        p = urlparse(url)
    except Exception:
        return ""
    if not p.scheme or not p.netloc:
        return ""
    return f"{p.scheme}://{p.netloc}{p.path}"


def _strip_html(value: str) -> str:
    if not value:
        return ""
    out = _HTML_DANGEROUS_BODY_RE.sub("", value)
    out = _HTML_TAG_RE.sub("", out)
    for entity, char in _HTML_SAFE_ENTITIES:
        out = out.replace(entity, char)
    out = out.replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return out.strip()


# ─── Storage helpers ──────────────────────────────────────────────
def _load_calendars(user_id: int) -> list:
    row = db.query_one(
        "SELECT calendars_json FROM user_settings WHERE user_id = %s",
        (user_id,),
    )
    raw = (row or {}).get("calendars_json") or "[]"
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if not item.get("id") or not item.get("name") or not item.get("url"):
            continue
        out.append({
            "id": str(item["id"]),
            "name": str(item["name"]),
            "url": str(item["url"]),
        })
    return out


def _save_calendars(user_id: int, calendars: list) -> None:
    payload = json.dumps(calendars)
    exists = db.query_one(
        "SELECT 1 FROM user_settings WHERE user_id = %s", (user_id,)
    )
    if exists:
        db.execute(
            "UPDATE user_settings SET calendars_json = %s, updated_at = NOW() "
            "WHERE user_id = %s",
            (payload, user_id),
        )
    else:
        db.execute(
            "INSERT INTO user_settings (user_id, calendars_json, updated_at) "
            "VALUES (%s, %s, NOW())",
            (user_id, payload),
        )


def _validate_url(url: str):
    if not isinstance(url, str) or not url.strip():
        return "empty"
    try:
        p = urlparse(url.strip())
    except Exception:
        return "parse"
    if p.scheme not in ("https",):
        return "scheme"
    if not p.netloc:
        return "host"
    return None


def _clean_calendar_item(idx, item):
    name = (item.get("name") or "").strip()
    url = (item.get("url") or "").strip()
    if not name:
        return None, f"item {idx} missing name"
    if not url:
        # Used to be "silently drop entries without url". That turned a
        # truncated PUT into a deletion: this route replaces the whole
        # stored list, so any item the client failed to send was destroyed
        # and the caller still got 200 OK. A missing url is now a 400 —
        # the request is refused, and the saved calendars are untouched.
        return None, f"item {idx} missing url"
    err = _validate_url(url)
    if err:
        return None, f"item {idx} invalid url ({err})"
    cid = (item.get("id") or "").strip()
    if cid and (_UID_SANITISE_RE.search(cid) or len(cid) > 64):
        return None, f"item {idx} invalid id"
    if not cid:
        cid = uuid.uuid4().hex[:12]
    return {"id": cid, "name": name, "url": url}, None


# ─── ICS fetch + parse ────────────────────────────────────────────
def _fetch_ics(url: str) -> bytes:
    host = urlparse(url).netloc
    logger.info("calendar_fetch host=%s", host)
    resp = requests.get(url, timeout=_FETCH_TIMEOUT_S, stream=True)
    try:
        resp.raise_for_status()
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=16 * 1024):
            if not chunk:
                break
            total += len(chunk)
            if total > _FETCH_MAX_BYTES:
                raise ValueError("feed too large")
            chunks.append(chunk)
        body = b"".join(chunks)
    finally:
        resp.close()

    looks_ics = (
        "text/calendar" in resp.headers.get("Content-Type", "").lower()
        or b"BEGIN:VCALENDAR" in body[:512]
    )
    if not looks_ics:
        raise ValueError(
            _classify_non_feed(resp.headers, body) or "not an ICS feed"
        )
    return body
  
  
# Why a response is not an ICS feed, phrased so the user can act on it.
# A Moodle `import.php` URL answers 200 with an HTML login page; calling that
# just "not a valid ICS feed" left the user with no way to know an
# authentication wall was the actual problem. These strings are a UI
# contract — keep them stable and free of URLs/credentials.
_FEED_REASONS = {
    "login_page": (
        "Esa URL devuelve una pagina de inicio de sesion, no un calendario. "
        "Moodle exige un enlace con token: usa el enlace personal de "
        "exportacion (…/calendar/export.php?token=…) o, si tienes la "
        "suspeccion en Google Calendar, la direccion ICS de "
        "calendar.google.com (Ajustes > Calendario > Integrar calendario)."
    ),
    "not_html_calendar_page": (
        "Esa URL devuelve una pagina web, no un calendario. "
        "Hace falta la direccion del feed (.ics), no la de la pagina."
    ),
    "empty_response": "Esa URL ha devuelto una respuesta vacia.",
    "not_an_ics_feed": (
        "La respuesta no tiene formato de calendario (.ics). "
        "Comprueba que el enlace sea el feed y no una pagina web."
    ),
}
  
  
def _classify_non_feed(headers, body) -> str:
    """Return why this response is not an ICS feed, or None if it is one."""
    ctype = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
    if not body.strip():
        return _FEED_REASONS["empty_response"]
    if b"BEGIN:VCALENDAR" in body[:4096]:
        return None
    # An HTML page that looks like a sign-in form is the Moodle case: it is
    # reachable and returns 200, so only the content gives it away. The body
    # is already capped at _FETCH_MAX_BYTES, and the real Moodle login form
    # sits ~15 KB in behind the stylesheet, so scan all of it rather than
    # a prefix.
    if "html" in ctype or b"<html" in body[:2048].lower():
        lowered = body.lower()
        looks_like_login = (
            b'name="logintoken"' in lowered
            or (b'name="username"' in lowered and b'name="password"' in lowered)
            or b"type=\"password\"" in lowered
            or b"type='password'" in lowered
        )
        return _FEED_REASONS[
            "login_page" if looks_like_login else "not_html_calendar_page"
        ]
    return _FEED_REASONS["not_an_ics_feed"]


def _normalise_dt(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    raise ValueError(f"unsupported DT value: {value!r}")


def _resolve_zone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(_LOCAL_TZ)
    except Exception as exc:
        logger.error("zoneinfo_unavailable: %s", exc)
        return None


# ─── GET /api/calendar/calendars ──────────────────────────────────
@bp.get("/calendar/calendars")
@token_required
def list_calendars(current_user_id: int):
    calendars = _load_calendars(current_user_id)
    return jsonify({
        "calendars": [
            {"id": c["id"], "name": c["name"], "url_masked": _mask_url(c["url"])}
            for c in calendars
        ]
    })


# ─── PUT /api/calendar/calendars ──────────────────────────────────
@bp.put("/calendar/calendars")
@token_required
def put_calendars(current_user_id: int):
    body = request.get_json(silent=True) or {}
    raw = body.get("calendars")
    if not isinstance(raw, list):
        return jsonify(error="calendars must be a list"), 400
    if len(raw) > _MAX_CALENDARS:
        return jsonify(error=f"max {_MAX_CALENDARS} calendars"), 400

    cleaned, seen_ids, err = _normalise_calendar_list(raw)
    if err is not None:
        return jsonify(error=err), 400

    try:
        _save_calendars(current_user_id, cleaned)
    except Exception:
        logger.exception("calendars_save_failed user=%s", current_user_id)
        return jsonify(error="failed to save calendars"), 500

    return jsonify({
        "calendars": [
            {"id": c["id"], "name": c["name"], "url_masked": _mask_url(c["url"])}
            for c in cleaned
        ]
    })


def _normalise_calendar_list(raw):
    cleaned, seen_ids = [], set()
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            return [], set(), f"item {idx} is not an object"
        item_clean, err = _clean_calendar_item(idx, item)
        if err is not None:
            return [], set(), err
        if item_clean is None:
            continue
        if item_clean["id"] in seen_ids:
            return [], set(), f"item {idx} duplicate id"
        seen_ids.add(item_clean["id"])
        cleaned.append(item_clean)
    return cleaned, seen_ids, None


# ─── POST /api/calendar/import ────────────────────────────────────
@bp.post("/calendar/import")
@token_required
def import_calendar(current_user_id: int):
    body = request.get_json(silent=True) or {}
    if body.get("days") not in _VALID_DAYS:
        return jsonify(error=f"days must be one of {_VALID_DAYS}"), 400

    url, err_resp = _resolve_import_url(body, current_user_id)
    if err_resp is not None:
        return err_resp

    cal, err_resp = _load_calendar_from_url(url)
    if err_resp is not None:
        return err_resp

    local_tz = _resolve_zone()
    if local_tz is None:
        return jsonify(error="server timezone data missing"), 500

    today = date.today()
    counters = {"imported": 0, "updated": 0, "skipped_out_of_range": 0}
    result, err_resp = _walk_calendar_into_sessions(
        cal, today, body["days"], local_tz, current_user_id, counters,
    )
    if err_resp is not None:
        return err_resp
    return jsonify(result)


def _resolve_import_url(body, user_id):
    calendar_id = body.get("calendar_id")
    raw_url = body.get("url")
    if calendar_id and raw_url:
        return None, (jsonify(error="supply only one of calendar_id or url"), 400)
    if not calendar_id and not raw_url:
        return None, (jsonify(error="calendar_id or url required"), 400)
    if calendar_id:
        calendars = _load_calendars(user_id)
        match = next((c for c in calendars if c["id"] == calendar_id), None)
        if not match:
            return None, (jsonify(error="calendar not found"), 404)
        return match["url"], None
    err = _validate_url(raw_url)
    if err:
        return None, (jsonify(error=f"invalid url ({err})"), 400)
    return raw_url, None


def _load_calendar_from_url(url):
    try:
        body_bytes = _fetch_ics(url)
    except requests.RequestException as exc:
        logger.warning("calendar_fetch_failed host=%s err=%s",
                       urlparse(url).netloc, exc.__class__.__name__)
        return None, (jsonify(imported=0, updated=0, skipped_out_of_range=0), 200)
    except ValueError as exc:
        logger.warning("calendar_rejected host=%s reason=%s",
                       urlparse(url).netloc, exc)
        # Surface the real reason: "not an ICS feed" told the user nothing
        # about the Moodle login wall that was actually in the way.
        return None, (jsonify(error=str(exc) or "the response is not a valid ICS feed"), 400)
    try:
        return Calendar.from_ical(body_bytes), None
    except Exception as exc:
        logger.warning("calendar_parse_failed host=%s err=%s",
                       urlparse(url).netloc, exc.__class__.__name__)
        return None, (jsonify(error="the response is not a valid ICS feed"), 400)


def _walk_calendar_into_sessions(cal, today, days, local_tz, user_id, counters):
    window_end = today + timedelta(days=days)
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            for component in cal.walk("VEVENT"):
                _process_one_vevent(cur, component, today, window_end,
                                    local_tz, user_id, counters)
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("calendar_import_failed user=%s", user_id)
        return None, (jsonify(error="failed to import calendar"), 500)
    finally:
        db.put_connection(conn)
    return dict(counters), None


def _process_one_vevent(cur, component, today, window_end,
                        local_tz, user_id, counters):
    times = _extract_event_times(component, local_tz)
    if times is None:
        return
    dtstart_local, dtend_local = times
    event_day = dtstart_local.date()
    if not (today <= event_day < window_end):
        counters["skipped_out_of_range"] += 1
        return

    fields = _build_event_fields(component, dtstart_local, dtend_local)
    if fields is None:
        return

    week_id = iso_week_key(event_day)
    _upsert_week(week_id, user_id)
    _upsert_day(fields["day_date"], week_id, user_id)

    cur.execute(_UPSERT_ICS_SESSION_SQL, (
        fields["session_id"], fields["day_date"], week_id, user_id,
        "formal_study", "pending",
        fields["start_time"], fields["end_time"],
        fields["summary"], fields["notes"],
    ))
    row = cur.fetchone()
    # get_connection() sets RealDictCursor, so the row is a dict keyed by the
    # RETURNING alias — row[0] raises KeyError: 0 and 500s the import.
    is_insert = bool(row and row.get("is_insert"))
    counters["imported" if is_insert else "updated"] += 1


def _extract_event_times(component, local_tz):
    try:
        dtstart_raw = component.decoded("DTSTART")
        dtend_raw = component.decoded("DTEND")
    except (KeyError, AttributeError):
        return None
    try:
        return (
            _normalise_dt(dtstart_raw).astimezone(local_tz),
            _normalise_dt(dtend_raw).astimezone(local_tz),
        )
    except Exception:
        return None


def _build_event_fields(component, dtstart_local, dtend_local):
    # Delegated rather than reimplemented. This used to be a verbatim copy
    # of calendar_import.vevent.build_event_fields, and the copy drifted
    # once already: the class-notes template landed here weeks after it
    # landed there, so a manually imported class opened with no checklist
    # while the scheduled import gave it one. One implementation, two
    # callers, no way for them to disagree.
    return build_event_fields(component, dtstart_local, dtend_local)