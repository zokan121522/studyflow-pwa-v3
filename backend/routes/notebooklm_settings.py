"""
NotebookLM Settings — Connection status, upload, multi-account profiles.

Endpoints (all under /api, registered in server.py):
    GET    /api/settings/notebooklm/status        — active profile + all profiles
    POST   /api/settings/notebooklm/upload        — upload storage_state.json
    GET    /api/settings/notebooklm/profiles      — list all profiles
    POST   /api/settings/notebooklm/switch        — switch active profile
    POST   /api/settings/notebooklm/disconnect    — remove active profile cookies
    DELETE /api/settings/notebooklm/profile/<email> — delete a specific profile
    POST   /api/settings/notebooklm/verify        — test connection via SDK
    GET    /api/settings/notebooklm/export-script — download export script

Ported from v2 ``routes/notebooklm_settings.py`` (866 lines) split into:
* ``ai/notebooklm/profiles.py`` — ownership/discovery/cookie helpers
* ``ai/notebooklm/export_script.py`` — embedded export script fallback
* this module — the Flask blueprint only
"""

import json
import os
import shutil

from flask import Blueprint, jsonify, request, Response

from database import fetchone
from routes.auth import token_required
from ai.notebooklm.profiles import (
    register_profile_owner,
    assert_profile_access,
    resolve_profile_dir,
    discover_profiles,
    read_active_profile,
    set_active_profile,
    clear_active_profile,
    STORAGE_FILE,
    COOKIE_DIR,
)
from ai.notebooklm.export_script import EXPORT_SCRIPT_CONTENT
from ai.notebooklm.client import _sync_run

bp = Blueprint("notebooklm_settings", __name__)


# ── helpers ───────────────────────────────────────────────────────────

def _user_profile(user_id: int) -> str | None:
    """Read the per-user notebooklm_profile from user_config."""
    row = fetchone(
        "SELECT notebooklm_profile FROM user_config WHERE user_id = %s",
        (user_id,),
    )
    if row and row.get("notebooklm_profile"):
        return row["notebooklm_profile"]
    return None


def _resolve_active(user_id: int, profiles: list[dict]) -> str | None:
    """Phase 30 hotfix: user_config is the ONLY source of truth.

    Never falls back to the global active.txt.  Auto-migrates legacy users
    (owned profiles but no user_config row) and repairs stale references.
    """
    user_profile = _user_profile(user_id)

    if user_profile is None and profiles:
        first_email = profiles[0]["email"]
        set_active_profile(user_id, first_email)
        user_profile = first_email

    if user_profile and profiles:
        owned_emails = {p["email"] for p in profiles}
        if user_profile not in owned_emails:
            first_email = profiles[0]["email"]
            set_active_profile(user_id, first_email)
            user_profile = first_email

    return user_profile


# ── endpoints ─────────────────────────────────────────────────────────

@bp.route("/settings/notebooklm/status")
@token_required
def status(user_id: int):
    """Check connection state — active profile + list all profiles."""
    profiles = discover_profiles(user_id)
    active_profile = _resolve_active(user_id, profiles)

    cookie_path: str | None = None
    email: str | None = None

    if active_profile:
        owned_match = [p for p in profiles if p["email"] == active_profile]
        if owned_match:
            cookie_path = owned_match[0]["path"]
            email = owned_match[0]["email"]

    connected = (
        active_profile is not None
        and cookie_path is not None
        and os.path.exists(cookie_path)
    )

    for p in profiles:
        p["active"] = (p["email"] == active_profile)

    return jsonify({
        "connected": connected,
        "active_profile": active_profile,
        "cookie_path": cookie_path,
        "email": email,
        "profiles": profiles,
        "user_id": user_id,
        "user_notebooklm_profile": active_profile,
    })


@bp.route("/settings/notebooklm/upload", methods=["POST"])
@token_required
def upload(user_id: int):
    """Upload a storage_state.json file and register it as a new profile.

    Expects multipart/form-data with the file in the ``file`` field.
    """
    if "file" not in request.files:
        return jsonify({"success": False, "message": "No se recibió ningún archivo"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"success": False, "message": "Archivo sin nombre"}), 400

    try:
        raw = file.read()
        data = json.loads(raw)
    except json.JSONDecodeError:
        return jsonify({"success": False, "message": "El archivo no es un JSON válido"}), 400

    nb_meta = data.get("notebooklm", {})
    acct = nb_meta.get("account", {})
    email_raw = acct.get("email", "")
    email: str = str(email_raw).strip() if isinstance(email_raw, str) else ""

    if not email:
        stem = os.path.splitext(os.path.basename(file.filename))[0]
        email = stem.replace(" ", "_").lower()
        if not email:
            email = "uploaded_account"

    os.makedirs(COOKIE_DIR, exist_ok=True)
    profile_dir = os.path.join(COOKIE_DIR, "profiles", email)
    os.makedirs(profile_dir, exist_ok=True)
    dest = os.path.join(profile_dir, STORAGE_FILE)

    with open(dest, "wb") as f:
        f.write(raw)

    set_active_profile(user_id, email)
    register_profile_owner(user_id, email)

    return jsonify({
        "success": True,
        "message": f"Perfil '{email}' configurado correctamente",
        "profile": {
            "email": email,
            "path": dest,
            "active": True,
        },
    })


@bp.route("/settings/notebooklm/profiles")
@token_required
def list_profiles(user_id: int):
    """List all available NotebookLM profiles (filtered by user)."""
    user_profile = _user_profile(user_id)
    profiles = discover_profiles(user_id)

    for p in profiles:
        p["active"] = (p["email"] == user_profile)

    return jsonify({
        "profiles": profiles,
        "active_profile": user_profile,
    })


@bp.route("/settings/notebooklm/switch", methods=["POST"])
@token_required
def switch_profile(user_id: int):
    """Switch the active NotebookLM profile (JSON body: ``email``)."""
    body = request.get_json(silent=True) or {}
    email: str | None = body.get("email")

    if not email:
        return jsonify({"success": False, "message": "Se requiere 'email'"}), 400

    try:
        assert_profile_access(user_id, email)
    except PermissionError as e:
        return jsonify({"success": False, "message": str(e)}), 403

    profile_dir = resolve_profile_dir(email)
    if not profile_dir or not os.path.isfile(os.path.join(profile_dir, STORAGE_FILE)):
        return jsonify({
            "success": False,
            "message": f"No existe el perfil '{email}'",
        }), 404

    set_active_profile(user_id, email)
    register_profile_owner(user_id, email)

    return jsonify({
        "success": True,
        "message": f"Perfil cambiado a '{email}'",
        "active_profile": email,
    })


@bp.route("/settings/notebooklm/disconnect", methods=["POST"])
@token_required
def disconnect(user_id: int):
    """Remove active profile cookies (directory + storage file)."""
    active_profile = read_active_profile()

    if active_profile:
        try:
            assert_profile_access(user_id, active_profile)
        except PermissionError as e:
            return jsonify({"success": False, "message": str(e)}), 403

        profile_dir = resolve_profile_dir(active_profile)
        if profile_dir and os.path.isdir(profile_dir):
            try:
                shutil.rmtree(profile_dir)
                clear_active_profile(user_id)
                return jsonify({"success": True, "message": f"Perfil '{active_profile}' eliminado"})
            except OSError as e:
                return jsonify({"success": False, "message": f"Error al eliminar: {e}"}), 500

    global_path = os.path.join(COOKIE_DIR, STORAGE_FILE)
    if os.path.exists(global_path):
        try:
            os.remove(global_path)
            return jsonify({"success": True, "message": "Cookies globales eliminadas"})
        except OSError as e:
            return jsonify({"success": False, "message": f"Error al eliminar: {e}"}), 500

    return jsonify({"success": True, "message": "No había cookies"}), 200


@bp.route("/settings/notebooklm/profile/<email>", methods=["DELETE"])
@token_required
def delete_profile(user_id: int, email: str):
    """Delete a specific NotebookLM profile by email."""
    try:
        assert_profile_access(user_id, email)
    except PermissionError as e:
        return jsonify({"success": False, "message": str(e)}), 403

    profile_dir = resolve_profile_dir(email)
    if not profile_dir or not os.path.isdir(profile_dir):
        return jsonify({
            "success": False,
            "message": f"No existe el perfil '{email}'",
        }), 404

    try:
        shutil.rmtree(profile_dir)
    except OSError as e:
        return jsonify({
            "success": False,
            "message": f"Error al eliminar el perfil '{email}': {e}",
        }), 500

    active = read_active_profile()
    if active and active == email:
        clear_active_profile(user_id)

    return jsonify({
        "success": True,
        "message": f"Perfil '{email}' eliminado correctamente",
    })


@bp.route("/settings/notebooklm/verify", methods=["POST"])
@token_required
def verify(user_id: int):
    """Verify credentials by listing notebooks via the SDK (active profile)."""
    profiles = discover_profiles(user_id)
    active_profile = _resolve_active(user_id, profiles)

    try:
        async def _test():
            from notebooklm import NotebookLMClient

            kwargs = {}
            if active_profile:
                kwargs["profile"] = active_profile

            async with NotebookLMClient.from_storage(**kwargs) as client:
                nbs = await client.notebooks.list()
                return {
                    "success": True,
                    "message": f"Conectado correctamente ({len(nbs)} notebooks activos)",
                    "limits": {
                        "chat_api": "50/día",
                        "artifacts": "~10/día",
                        "audio_video": "3/día",
                        "deep_research": "10/mes",
                    },
                }

        result = _sync_run(_test())
        return jsonify(result)

    except Exception as e:
        err_msg = str(e)
        if any(kw in err_msg.lower() for kw in ("auth", "login", "unauthorized")):
            return jsonify({
                "success": False,
                "message": "Sesión expirada. Reconecta desde Configuración.",
                "limits": None,
            })
        return jsonify({
            "success": False,
            "message": f"Error de conexión: {err_msg}",
            "limits": None,
        })


@bp.route("/settings/notebooklm/export-script")
@token_required
def export_script(user_id: int):
    """Download the export_storage_state.py script (email embedded)."""
    account = request.args.get("account", "").strip()

    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "export_storage_state.py"),
    ]
    for p in candidates:
        resolved = os.path.normpath(p)
        if os.path.isfile(resolved):
            with open(resolved) as f:
                content = f.read()
            break
    else:
        content = EXPORT_SCRIPT_CONTENT

    if account:
        content = content.replace("{{EMAIL}}", account)

    return Response(
        content,
        mimetype="text/x-python",
        headers={
            "Content-Disposition": "attachment; filename=export_storage_state.py",
            "Content-Type": "text/x-python; charset=utf-8",
        },
    )