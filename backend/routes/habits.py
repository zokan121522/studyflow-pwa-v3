"""Habits API — daily tracking with dynamic columns.

Each day has checkboxes, text inputs, or number fields per column definition.
Users can create / delete / edit columns from the columns overlay. New users
start with zero columns — all are created from the UI.

v3 conventions:
- `@token_required` injects `current_user_id` (single-user local fallback).
- All routes are registered under `/api` by the app factory, so the blueprint
  has NO url_prefix — paths here are relative (`/habits/columns`, …).
- Route order matters: literal segments (`/columns`, `/week/<week_id>`,
  `/dates`, `/all`, `/grid`) MUST be registered before the catch-all
  `/habits/<date>` so Werkzeug doesn't route `GET /habits/columns` to
  `get_day("columns")`. Even though Werkzeug's specificity rules usually
  prefer literal segments over converters, we keep an explicit ordering
  defensively.
"""
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify

from database import execute, fetchone, fetchall
from routes.auth import token_required


bp = Blueprint("habits", __name__)


# ─── Helpers ──────────────────────────────────────────────────────

def _load_columns(user_id: int, conn=None) -> dict:
    """Return ALL columns for user as {key: {...}} dict."""
    sql = 'SELECT * FROM habit_columns WHERE user_id = %s ORDER BY "order"'
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id,))
            rows = cur.fetchall()
    else:
        rows = db.query(sql, (user_id,))
    return {r["key"]: r for r in rows}


def _entries_for_day(date_str: str, user_id: int, conn=None) -> list:
    sql = "SELECT key, value FROM habit_entries WHERE date = %s AND user_id = %s"
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (date_str, user_id))
            return cur.fetchall()
    return db.query(sql, (date_str, user_id))


def _notes_for_day(date_str: str, user_id: int, conn=None) -> list:
    sql = "SELECT key, note FROM habit_notes WHERE date = %s AND user_id = %s"
    if conn is not None:
        with conn.cursor() as cur:
            cur.execute(sql, (date_str, user_id))
            return cur.fetchall()
    return db.query(sql, (date_str, user_id))


def _day_data(date_str: str, cols: dict, user_id: int, conn=None) -> dict:
    """Reconstruct { date, habits, notes, progress } for a single day."""
    # Default values per column type
    habits = {}
    for key, col in cols.items():
        if col["type"] == "checkbox":
            habits[key] = False
        else:
            habits[key] = ""

    # Load actual values from DB (reuse conn if available)
    for e in _entries_for_day(date_str, user_id, conn):
        key = e["key"]
        col_type = cols.get(key, {}).get("type", "checkbox")
        val = e["value"]
        if col_type == "checkbox":
            habits[key] = val == "true"
        elif col_type == "number":
            try:
                habits[key] = float(val) if val else ""
            except (ValueError, TypeError):
                habits[key] = ""
        else:
            habits[key] = val if val else ""

    notes = {r["key"]: r["note"] for r in _notes_for_day(date_str, user_id, conn)}

    # Progress: % of checked checkboxes (only meaningful if any checkbox col)
    checkbox_keys = [k for k, c in cols.items() if c["type"] == "checkbox"]
    checked = sum(1 for k in checkbox_keys if habits.get(k) is True)
    progress = round((checked / len(checkbox_keys)) * 100) if checkbox_keys else 0

    return {"date": date_str, "habits": habits, "notes": notes, "progress": progress}


# ─── GET /habits/columns ──────────────────────────────────────────
@bp.get("/habits/columns")
@token_required
def list_columns(current_user_id: int):
    cols = _load_columns(current_user_id)
    sorted_cols = sorted(cols.values(), key=lambda c: c.get("order", 99))
    return jsonify({
        "columns": [
            {"key": c["key"], "label": c["label"],
             "type": c["type"], "order": c["order"],
             "note": c.get("note", "")}
            for c in sorted_cols
        ],
    })


# ─── POST /habits/columns ─────────────────────────────────────────
@bp.post("/habits/columns")
@token_required
def add_column(current_user_id: int):
    body = request.json or {}
    key = body.get("key", "").strip().lower().replace(" ", "_")
    label = body.get("label", key)
    col_type = body.get("type", "checkbox")

    if not key:
        return jsonify(error="Column key is required"), 400
    if col_type not in ("checkbox", "text", "number", "media", "progress"):
        return jsonify(error="Invalid type"), 400

    existing = db.query_one(
        "SELECT key FROM habit_columns WHERE key = %s AND user_id = %s",
        (key, current_user_id),
    )
    if existing:
        return jsonify(error="Column already exists"), 409

    max_row = db.query_one(
        'SELECT MAX("order") AS max_ord FROM habit_columns WHERE user_id = %s',
        (current_user_id,),
    )
    next_order = (
        (max_row["max_ord"] + 1) if max_row and max_row["max_ord"] is not None else 0
    )

    db.execute(
        """
        INSERT INTO habit_columns (key, user_id, label, type, "order")
        VALUES (%s, %s, %s, %s, %s)
        """,
        (key, current_user_id, label, col_type, next_order),
    )
    return jsonify({
        key: {"key": key, "label": label,
              "type": col_type, "order": next_order}
    }), 201


# ─── PATCH /habits/columns/<key> ──────────────────────────────────
@bp.patch("/habits/columns/<key>")
@token_required
def update_column(current_user_id: int, key: str):
    """Edit a column's label, type, or order."""
    body = request.json or {}

    sets, params = [], {"key": key, "user_id": current_user_id}
    if "label" in body:
        sets.append("label = %(label)s"); params["label"] = body["label"]
    if "type" in body:
        if body["type"] not in ("checkbox", "text", "number", "media", "progress"):
            return jsonify(error="Invalid type"), 400
        sets.append("type = %(type)s"); params["type"] = body["type"]
    if "order" in body:
        sets.append('"order" = %(order)s'); params["order"] = body["order"]
    if "note" in body:
        sets.append("note = %(note)s"); params["note"] = body["note"]
    if not sets:
        return jsonify(error="No updatable fields"), 400

    db.execute(
        f"UPDATE habit_columns SET {', '.join(sets)} "
        "WHERE key = %(key)s AND user_id = %(user_id)s",
        params,
    )
    updated = db.query_one(
        "SELECT * FROM habit_columns WHERE key = %s AND user_id = %s",
        (key, current_user_id),
    )
    if updated:
        return jsonify({key: updated})
    return jsonify(error="Column not found"), 404


# ─── PUT /habits/columns/reorder ──────────────────────────────────
@bp.put("/habits/columns/reorder")
@token_required
def reorder_columns(current_user_id: int):
    """Bulk-reorder columns. Body: {"order": ["col_a", "col_b", ...]}"""
    body = request.json or {}
    order = body.get("order")
    if not order or not isinstance(order, list):
        # v2 used "keys"; accept both for forward-compat.
        order = body.get("keys")
    if not order or not isinstance(order, list):
        return jsonify(error="order must be a non-empty list"), 400
    for idx, key in enumerate(order):
        db.execute(
            'UPDATE habit_columns SET "order" = %s WHERE key = %s AND user_id = %s',
            (idx, key, current_user_id),
        )
    return jsonify(ok=True)


# ─── DELETE /habits/columns/<key> ─────────────────────────────────
@bp.delete("/habits/columns/<key>")
@token_required
def delete_column(current_user_id: int, key: str):
    """Delete a column and ALL its data entries (CASCADE)."""
    db.execute(
        "DELETE FROM habit_columns WHERE key = %s AND user_id = %s",
        (key, current_user_id),
    )
    return jsonify(ok=True)


# ─── GET /habits/week/<week_id> ───────────────────────────────────
@bp.get("/habits/week/<week_id>")
@token_required
def get_week(current_user_id: int, week_id: str):
    """Return all habits for a given ISO week (e.g. '2026-W23').
    Single DB connection reused for all 7 days.
    """
    try:
        year_s, wn_s = week_id.split("-W")
        year = int(year_s); wn = int(wn_s)
    except (ValueError, IndexError, AttributeError):
        return jsonify(error="Invalid week format, use YYYY-Www"), 400

    jan4 = datetime(year, 1, 4)
    start = jan4 - timedelta(days=jan4.isoweekday() - 1) + timedelta(weeks=wn - 1)

    conn = db.get_connection()
    try:
        cols = _load_columns(current_user_id, conn=conn)
        results = {}
        for i in range(7):
            d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            results[d] = _day_data(d, cols, current_user_id, conn=conn)
        return jsonify({"week_id": week_id, "days": results})
    finally:
        db.put_connection(conn)


# ─── GET /habits/dates ────────────────────────────────────────────
@bp.get("/habits/dates")
@token_required
def list_dates(current_user_id: int):
    """Return all dates that have habit data, sorted."""
    rows = db.query(
        "SELECT DISTINCT date FROM habit_entries WHERE user_id = %s ORDER BY date",
        (current_user_id,),
    )
    return jsonify([r["date"] for r in rows])


# ─── GET /habits/all ──────────────────────────────────────────────
@bp.get("/habits/all")
@token_required
def get_all_habits(current_user_id: int):
    """Return habit data for ALL dates that have data."""
    conn = db.get_connection()
    try:
        cols = _load_columns(current_user_id, conn=conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT date FROM habit_entries WHERE user_id = %s ORDER BY date",
                (current_user_id,),
            )
            date_rows = cur.fetchall()
        results = {}
        for r in date_rows:
            results[r["date"]] = _day_data(r["date"], cols, current_user_id, conn=conn)
        return jsonify(results)
    finally:
        db.put_connection(conn)


# ─── GET /habits/grid ─────────────────────────────────────────────
@bp.get("/habits/grid")
@token_required
def get_habit_grid(current_user_id: int):
    """Return daily progress grid for the last N days (default 21)."""
    days = request.args.get("days", 21, type=int)
    days = max(7, min(days, 90))  # clamp 7–90

    conn = db.get_connection()
    try:
        all_cols = _load_columns(current_user_id, conn=conn)
        checkbox_cols = {k: v for k, v in all_cols.items() if v["type"] == "checkbox"}

        today = datetime.now().date()
        grid = []
        for i in range(days - 1, -1, -1):
            d = today - timedelta(days=i)
            date_str = d.isoformat()
            try:
                data = _day_data(date_str, all_cols, current_user_id, conn=conn)
            except Exception:
                data = {"date": date_str, "habits": {}, "notes": {}, "progress": 0}
            grid.append(data)

        return jsonify({
            "columns": [
                {"key": c["key"], "label": c["label"], "type": c["type"]}
                for c in sorted(checkbox_cols.values(), key=lambda x: x.get("order", 99))
            ],
            "grid": grid,
        })
    finally:
        db.put_connection(conn)


# ─── GET /habits/<date> ───────────────────────────────────────────
@bp.get("/habits/<date>")
@token_required
def get_day(current_user_id: int, date: str):
    cols = _load_columns(current_user_id)
    return jsonify(_day_data(date, cols, current_user_id))


# ─── PUT /habits/<date> ───────────────────────────────────────────
@bp.put("/habits/<date>")
@token_required
def save_day(current_user_id: int, date: str):
    body = request.json or {}
    cols = _load_columns(current_user_id)

    if "habits" in body:
        for key, val in body["habits"].items():
            if key not in cols:
                continue
            stored = "true" if val is True else "false" if val is False else str(val)
            db.execute(
                """
                INSERT INTO habit_entries (date, key, user_id, value)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (date, key, user_id) DO UPDATE SET value = EXCLUDED.value
                """,
                (date, key, current_user_id, stored),
            )

    if "notes" in body:
        for key, note in body["notes"].items():
            if key not in cols:
                continue
            db.execute(
                """
                INSERT INTO habit_notes (date, key, user_id, note)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (date, key, user_id) DO UPDATE SET note = EXCLUDED.note
                """,
                (date, key, current_user_id, str(note) if note else ""),
            )

    return jsonify(_day_data(date, cols, current_user_id))


# ─── PATCH /habits/<date>/habit ───────────────────────────────────
@bp.patch("/habits/<date>/habit")
@token_required
def toggle_habit(current_user_id: int, date: str):
    body = request.json or {}
    key = body.get("key")
    if not key:
        return jsonify(error="key is required"), 400

    cols = _load_columns(current_user_id)
    if key not in cols:
        return jsonify(error=f"Unknown column: {key}"), 400

    col_type = cols[key]["type"]
    if col_type == "checkbox":
        value = "true" if body.get("checked", False) else "false"
    elif col_type == "number":
        raw = body.get("value")
        try:
            value = str(float(raw)) if raw not in (None, "") else ""
        except (ValueError, TypeError):
            value = ""
    else:
        value = str(body.get("value", "")) if body.get("value") is not None else ""

    db.execute(
        """
        INSERT INTO habit_entries (date, key, user_id, value)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (date, key, user_id) DO UPDATE SET value = EXCLUDED.value
        """,
        (date, key, current_user_id, value),
    )
    return jsonify(_day_data(date, cols, current_user_id))


# ─── PATCH /habits/<date>/cell ────────────────────────────────────
@bp.patch("/habits/<date>/cell")
@token_required
def save_cell(current_user_id: int, date: str):
    """Update a single cell (checkbox, text, or number)."""
    body = request.json or {}
    key = body.get("key")
    if not key:
        return jsonify(error="key is required"), 400

    cols = _load_columns(current_user_id)
    if key not in cols:
        return jsonify(error=f"Unknown column: {key}"), 400

    col_type = cols[key]["type"]
    value = body.get("value")
    if col_type == "checkbox":
        stored = "true" if value else "false"
    elif col_type == "number":
        try:
            stored = str(float(value)) if value not in (None, "") else ""
        except (ValueError, TypeError):
            stored = ""
    else:
        stored = str(value) if value is not None else ""

    db.execute(
        """
        INSERT INTO habit_entries (date, key, user_id, value)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (date, key, user_id) DO UPDATE SET value = EXCLUDED.value
        """,
        (date, key, current_user_id, stored),
    )
    return jsonify(_day_data(date, cols, current_user_id))


# ─── PATCH /habits/<date>/note ────────────────────────────────────
@bp.patch("/habits/<date>/note")
@token_required
def save_note(current_user_id: int, date: str):
    body = request.json or {}
    key = body.get("key")
    note = body.get("note", "")
    if not key:
        return jsonify(error="key is required"), 400

    db.execute(
        """
        INSERT INTO habit_notes (date, key, user_id, note)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (date, key, user_id) DO UPDATE SET note = EXCLUDED.note
        """,
        (date, key, current_user_id, str(note) if note else ""),
    )
    cols = _load_columns(current_user_id)
    return jsonify(_day_data(date, cols, current_user_id))