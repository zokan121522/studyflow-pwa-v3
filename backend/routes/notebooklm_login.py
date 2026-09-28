"""
Interactive NotebookLM login (v3 — macOS/local).

Port of v2's notebooklm_login_vnc.py without Xvfb/x11vnc/noVNC
(those are headless-server tools that don't exist on macOS; Google
also blocks iframes of accounts.google.com via X-Frame-Options: DENY).

v3 flow:
  POST /api/settings/notebooklm/login-start — launch `notebooklm login
        --browser chrome --storage <path>` as a real Chrome window
  GET  /api/settings/notebooklm/login-status — poll session state
  POST /api/settings/notebooklm/login-stop   — kill session, capture cookies

The SDK (notebooklm-py 0.7.2) detects the login automatically and saves
storage_state.json to the profile dir; no terminal interaction needed.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time

from flask import Blueprint, jsonify, request

from ai.notebooklm.profiles import (
    register_profile_owner,
    save_user_notebooklm_profile,
    write_active_profile,
)
from ai.notebooklm.utils import COOKIE_DIR
from routes.auth import token_required

bp = Blueprint("notebooklm_login", __name__)

# ── Configuration ──────────────────────────────────────────────────────
LOGIN_TIMEOUT = 600  # 10 minutes

# Shared state via JSON file (survives dev-server restarts)
STATE_FILE = "/tmp/notebooklm_login.json"

PYTHON = sys.executable  # python running the Flask app


def _read_state() -> dict:
    """Read session state from shared JSON file."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"status": "idle"}


def _write_state(status: str,
                 login_pid: int | None = None,
                 account: str | None = None,
                 storage_path: str | None = None) -> None:
    state = {
        "status": status,
        "login_pid": login_pid,
        "account": account,
        "storage_path": storage_path,
        "started_at": time.time() if status == "running" else None,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def _pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _kill_pid(pid: int | None, sig=signal.SIGTERM) -> None:
    if pid is None:
        return
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, OSError):
        pass


def _kill_proc_tree(pid: int | None) -> None:
    """Kill a process and its children (Chrome spawns helpers)."""
    if pid is None:
        return
    # Children first, then parent
    try:
        out = subprocess.run(
            ["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=5
        )
        for child in out.stdout.split():
            _kill_proc_tree(int(child))
    except Exception:
        pass
    _kill_pid(pid)
    time.sleep(0.3)
    if _pid_alive(pid):
        _kill_pid(pid, signal.SIGKILL)


def _storage_created(storage_path: str | None) -> bool:
    return bool(storage_path and os.path.isfile(storage_path))


def _cleanup() -> None:
    state = _read_state()
    _kill_proc_tree(state.get("login_pid"))
    _write_state("idle")


def _check_connected() -> tuple[bool, str | None]:
    """Check if any storage_state.json exists after login (v2 order)."""
    nb_home = COOKIE_DIR
    connected = False
    found_email: str | None = None

    # 1. Global file: ~/.notebooklm/storage_state.json — ignore (v3 uses
    #    per-profile paths only, but keep for SDK backward compat)
    global_path = os.path.join(nb_home, "storage_state.json")
    if os.path.isfile(global_path):
        connected = True

    # 2. Profile subdirs: ~/.notebooklm/profiles/{email}/
    profiles_dir = os.path.join(nb_home, "profiles")
    if not connected and os.path.isdir(profiles_dir):
        for entry in sorted(os.listdir(profiles_dir)):
            sub_path = os.path.join(profiles_dir, entry, "storage_state.json")
            if os.path.isfile(sub_path):
                # Skip non-email dirs produced by the SDK (000-default? etc.)
                if "@" not in entry and entry != "default":
                    continue
                connected = True
                found_email = entry
                break

    # 3. Legacy: ~/.notebooklm/{email}/
    if not connected and os.path.isdir(nb_home):
        for entry in sorted(os.listdir(nb_home)):
            sub_path = os.path.join(nb_home, entry, "storage_state.json")
            if os.path.isfile(sub_path) and entry not in ("profiles",) and \
                    ("@" in entry or entry == "default"):
                connected = True
                found_email = entry
                break

    return connected, found_email


# ── Endpoints ──────────────────────────────────────────────────────────

@bp.route("/settings/notebooklm/login-start", methods=["POST"])
@token_required
def login_start(current_user_id: int):
    """Launch `notebooklm login --browser chrome` for a real Chrome window.

    Returns JSON with success + message. The browser window is the
    "embedded screen" v3 equivalent: no Xvfb/VNC needed on macOS.
    """
    # Always clean up any previous session first (stale or running)
    _cleanup()

    body = request.get_json(silent=True) or {}
    account_email: str | None = body.get("account", "").strip() or None

    # Save the NB profile preference to user_config (Phase 30)
    if account_email:
        save_user_notebooklm_profile(current_user_id, account_email)

    # Determine storage path (profile convention: ~/.notebooklm/profiles/{email}/)
    profile_dir = os.path.join(COOKIE_DIR, "profiles", account_email or "default")
    os.makedirs(profile_dir, exist_ok=True)
    storage_path = os.path.join(profile_dir, "storage_state.json")

    # Remove stale storage file so login-status can detect a fresh one
    if os.path.isfile(storage_path):
        try:
            os.remove(storage_path)
        except OSError:
            pass

    cmd = [
        PYTHON, "-m", "notebooklm", "login",
        "--browser", "chrome",
        "--storage", storage_path,
    ]
    login_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(1.0)

    # Sanity: if the process died instantly, report error
    if login_proc.poll() is not None:
        _write_state("idle")
        return jsonify({
            "success": False,
            "message": (
                "El proceso de login falló al arrancar. "
                "Verifica que `notebooklm` está disponible y playwright instalado."
            ),
        }), 500

    _write_state("running",
                 login_pid=login_proc.pid,
                 account=account_email,
                 storage_path=storage_path)

    return jsonify({
        "success": True,
        "message": (
            "Se ha abierto una ventana de Google Chrome. "
            "Completa el login en esa ventana y vuelve aquí."
        ),
    })


@bp.route("/settings/notebooklm/login-status", methods=["GET"])
@token_required
def login_status(current_user_id: int):
    """Check current login session state.

    Returns:
        {
            "running": bool,          # login session active
            "browser_alive": bool,    # Chrome still open
            "login_complete": bool,   # login subprocess finished
            "storage_created": bool,  # storage_state.json exists
            "started_at": float|None
        }
    """
    state = _read_state()
    running = state["status"] == "running"
    browser_alive = _pid_alive(state.get("login_pid"))
    login_pid = state.get("login_pid")
    login_complete = login_pid is not None and not _pid_alive(login_pid)
    storage_created = _storage_created(state.get("storage_path"))

    # Auto-close when the process finished AND cookies landed
    if running and login_complete and storage_created:
        _write_state("idle", storage_path=state.get("storage_path"))

    return jsonify({
        "running": running,
        "browser_alive": browser_alive,
        "login_complete": login_complete,
        "storage_created": storage_created,
        "started_at": state.get("started_at"),
    })


@bp.route("/settings/notebooklm/login-stop", methods=["POST"])
@token_required
def login_stop(current_user_id: int):
    """Stop the login session, kill Chrome, check for cookies.

    Returns:
        {
            "success": bool,
            "message": str,
            "connected": bool,   # True if storage_state.json was created
        }
    """
    try:
        _cleanup()

        connected, found_email = _check_connected()

        # If a new profile was detected, set it as active + register ownership
        if found_email:
            write_active_profile(found_email)
            register_profile_owner(current_user_id, found_email)

        msg = "Cookies guardadas correctamente" if connected else (
            "Sesión cerrada. No se detectaron cookies — prueba de nuevo "
            "o usa el método de subida manual."
        )

        return jsonify({
            "success": True,
            "message": msg,
            "connected": connected,
        })
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({
            "success": False,
            "message": f"Error al detener el login: {exc}",
            "connected": False,
        }), 500