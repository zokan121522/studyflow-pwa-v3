# backend/routes/addons.py
"""Addon catalog + state endpoints (Sub-phase SA — Addons Foundation).

Single-user v3: per-slug rows hold installed/enabled/hidden state globally
(no per-user table — v2's `user_addons` collapses into the addon row
itself). The catalog is seeded at startup; the seed UPSERT only refreshes
name/description/version/url_prefix, never user state.

Registered under /api by the app factory, so paths here are relative
('/addons', '/addons/<slug>/install', …) — see server.py.

Endpoints (Sub-phase SA):
  GET    /addons                       → catalog + state for every slug
  POST   /addons/<slug>/install        → installed=TRUE
  POST   /addons/<slug>/uninstall      → installed=FALSE, enabled=FALSE
  POST   /addons/<slug>/enable         → installed=TRUE, enabled=TRUE
  POST   /addons/<slug>/disable        → enabled=FALSE (installed stays)
  POST   /addons/<slug>/set-hidden     → hidden=bool (body: {hidden: bool})
  GET    /addons/<slug>/status         → {slug, installed, enabled, hidden}
"""

from flask import Blueprint, jsonify, request

from backend.database import execute, fetchone, fetchall
from backend.routes.auth import token_required


bp = Blueprint("addons", __name__)


def _serialize(row):
    """Public addon payload — strips timestamp columns to keep it small."""
    return {
        "slug": row["slug"],
        "name": row["name"],
        "description": row.get("description") or "",
        "version": row.get("version") or "0.0.0",
        "installed": bool(row.get("installed")),
        "enabled": bool(row.get("enabled")),
        "hidden": bool(row.get("hidden")),
        "url_prefix": row.get("url_prefix"),
    }


def _addon_or_404(slug):
    """Return (row, None) on hit, (None, error_response) on miss."""
    row = fetchone("SELECT * FROM addons WHERE slug = %s", (slug,))
    if not row:
        return None, (jsonify(error="Addon not found"), 404)
    return row, None


@bp.get("/addons")
@token_required
def list_addons(_user_id):
    """Catalog with per-row state, ordered by display name."""
    rows = fetchall("SELECT * FROM addons ORDER BY name")
    return jsonify({"addons": [_serialize(r) for r in rows]})


@bp.post("/addons/<slug>/install")
@token_required
def install_addon(_user_id, slug):
    _row, err = _addon_or_404(slug)
    if err:
        return err
    execute(
        "UPDATE addons SET installed = TRUE, updated_at = NOW() "
        "WHERE slug = %s",
        (slug,),
    )
    return jsonify(_serialize(fetchone("SELECT * FROM addons WHERE slug = %s",
                                       (slug,))))


@bp.post("/addons/<slug>/uninstall")
@token_required
def uninstall_addon(_user_id, slug):
    _row, err = _addon_or_404(slug)
    if err:
        return err
    execute(
        "UPDATE addons SET installed = FALSE, enabled = FALSE, "
        "updated_at = NOW() WHERE slug = %s",
        (slug,),
    )
    return jsonify(_serialize(fetchone("SELECT * FROM addons WHERE slug = %s",
                                       (slug,))))


@bp.post("/addons/<slug>/enable")
@token_required
def enable_addon(_user_id, slug):
    _row, err = _addon_or_404(slug)
    if err:
        return err
    execute(
        "UPDATE addons SET installed = TRUE, enabled = TRUE, "
        "updated_at = NOW() WHERE slug = %s",
        (slug,),
    )
    return jsonify(_serialize(fetchone("SELECT * FROM addons WHERE slug = %s",
                                       (slug,))))


@bp.post("/addons/<slug>/disable")
@token_required
def disable_addon(_user_id, slug):
    _row, err = _addon_or_404(slug)
    if err:
        return err
    execute(
        "UPDATE addons SET enabled = FALSE, updated_at = NOW() "
        "WHERE slug = %s",
        (slug,),
    )
    return jsonify(_serialize(fetchone("SELECT * FROM addons WHERE slug = %s",
                                       (slug,))))


@bp.post("/addons/<slug>/set-hidden")
@token_required
def set_hidden_addon(_user_id, slug):
    _row, err = _addon_or_404(slug)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    hidden = bool(data.get("hidden"))
    execute(
        "UPDATE addons SET hidden = %s, updated_at = NOW() WHERE slug = %s",
        (hidden, slug),
    )
    return jsonify(_serialize(fetchone("SELECT * FROM addons WHERE slug = %s",
                                       (slug,))))


@bp.get("/addons/<slug>/status")
@token_required
def addon_status(_user_id, slug):
    """Compact status view — used by the marketplace gate helper."""
    row = fetchone(
        "SELECT slug, installed, enabled, hidden FROM addons WHERE slug = %s",
        (slug,),
    )
    if not row:
        return jsonify(error="Addon not found"), 404
    return jsonify({
        "slug": row["slug"],
        "installed": bool(row["installed"]),
        "enabled": bool(row["enabled"]),
        "hidden": bool(row["hidden"]),
    })