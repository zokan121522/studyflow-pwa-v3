"""
Interactive NotebookLM login (v3).

Two modes, picked at runtime:

  · VNC mode (Linux / Docker) — when ``Xvfb`` and ``x11vnc`` exist. Starts a
    virtual display + an x11vnc server + a headed Playwright Chromium that
    shows Google's login. The screen is streamed to the app as an embedded
    noVNC iframe (routes/novnc_proxy.py), served on the SAME origin so there
    is no port-6080 exposure and no mixed-content block.

  · Chrome-window mode (macOS native dev) — no Xvfb, so it launches
    ``notebooklm login --browser chrome`` as a real desktop Chrome window.

Why VNC instead of ``--browser chrome`` in Docker:
  Playwright's ``channel="chrome"`` resolves a SYSTEM desktop Chrome, which
  the image does not ship on arm64 (the channel does not exist for Linux
  arm64). The bundled Chromium IS installed, so the VNC path drives it
  directly with ``DISPLAY=:99`` and never needs desktop Chrome.

Endpoints (server.py adds the ``/api`` prefix):
    POST /settings/notebooklm/login-start   — start a login session
    GET  /settings/notebooklm/login-status  — poll session state
    POST /settings/notebooklm/login-stop    — kill session, capture cookies

Ported from studyflow-hub v2 (``routes/notebooklm_login_vnc.py``).
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

from flask import Blueprint, jsonify, request

from ai.notebooklm.profiles import (
    register_profile_owner,
    set_active_profile,
)
from ai.notebooklm.utils import COOKIE_DIR
from routes.auth import token_required

bp = Blueprint("notebooklm_login", __name__)

# ── Configuration ──────────────────────────────────────────────────────
LOGIN_TIMEOUT = 600  # 10 minutes
VNC_PORT = 5900
DISPLAY_NUM = 99
DISPLAY = f":{DISPLAY_NUM}"

# Shared state via JSON file. The gevent worker runs a single process, but the
# state file keeps the API stateless across restarts / worker respawns.
STATE_FILE = "/tmp/notebooklm_login.json"

PYTHON = sys.executable  # the python running the Flask app

# Desktop Chrome executables that ``channel="chrome"`` would resolve to. Used
# only by the macOS fallback; probed, never assumed.
_CHROME_BINARIES = (
    "google-chrome", "google-chrome-stable", "chrome", "chromium-browser",
)


def _vnc_available() -> bool:
    """True when the container has the virtual-display toolchain."""
    return bool(shutil.which("Xvfb") and shutil.which("x11vnc"))


def _missing_desktop_chrome() -> bool:
    """True when the desktop Chrome required by ``--browser chrome`` is absent."""
    return not any(shutil.which(name) for name in _CHROME_BINARIES)


# ── Shared state ───────────────────────────────────────────────────────

def _read_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"status": "idle"}


def _write_state(status: str,
                 mode: str | None = None,
                 login_pid: int | None = None,
                 xvfb_pid: int | None = None,
                 x11vnc_pid: int | None = None,
                 account: str | None = None,
                 storage_path: str | None = None) -> None:
    state = {
        "status": status,
        "mode": mode,
        "login_pid": login_pid,
        "xvfb_pid": xvfb_pid,
        "x11vnc_pid": x11vnc_pid,
        "account": account,
        "storage_path": storage_path,
        "started_at": time.time() if status == "running" else None,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


# ── Process helpers ────────────────────────────────────────────────────

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
    """Kill a process and its children (Chromium spawns helper processes)."""
    if pid is None:
        return
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
    """Kill every process of the current session and reset the state file."""
    state = _read_state()
    # Order matters: browser/login first (releases the display), then VNC, then Xvfb.
    _kill_proc_tree(state.get("login_pid"))
    _kill_pid(state.get("x11vnc_pid"))
    _kill_pid(state.get("xvfb_pid"))

    # Remove the X lock left behind by an Xvfb that was killed abruptly.
    lock_file = f"/tmp/.X{DISPLAY_NUM}-lock"
    try:
        if os.path.exists(lock_file):
            os.remove(lock_file)
    except OSError:
        pass

    _write_state("idle")


def _schedule_timeout() -> None:
    """Auto-cleanup after LOGIN_TIMEOUT seconds so Xvfb cannot leak forever."""
    timer = threading.Timer(LOGIN_TIMEOUT, _cleanup)
    timer.daemon = True
    timer.start()


def _check_connected() -> tuple[bool, str | None]:
    """Check if any storage_state.json exists after login (v2 order)."""
    nb_home = COOKIE_DIR
    connected = False
    found_email: str | None = None

    global_path = os.path.join(nb_home, "storage_state.json")
    if os.path.isfile(global_path):
        connected = True

    profiles_dir = os.path.join(nb_home, "profiles")
    if not connected and os.path.isdir(profiles_dir):
        for entry in sorted(os.listdir(profiles_dir)):
            sub_path = os.path.join(profiles_dir, entry, "storage_state.json")
            if os.path.isfile(sub_path):
                # Skip non-email dirs produced by the SDK (000-default, etc.)
                if "@" not in entry and entry != "default":
                    continue
                connected = True
                found_email = entry
                break

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
    """Start a login session (VNC in Docker, Chrome window on macOS)."""
    _cleanup()

    body = request.get_json(silent=True) or {}
    account_email: str | None = body.get("account", "").strip() or None

    if account_email:
        set_active_profile(current_user_id, account_email)

    # Profile convention: <COOKIE_DIR>/profiles/<email>/storage_state.json
    profile_dir = os.path.join(COOKIE_DIR, "profiles", account_email or "default")
    os.makedirs(profile_dir, exist_ok=True)
    storage_path = os.path.join(profile_dir, "storage_state.json")

    # Remove any stale file so login-status can detect a *fresh* one.
    if os.path.isfile(storage_path):
        try:
            os.remove(storage_path)
        except OSError:
            pass

    if _vnc_available():
        return _start_vnc(account_email, storage_path, current_user_id)
    return _start_chrome_window(account_email, storage_path)


def _start_vnc(account_email: str | None, storage_path: str,
               current_user_id: int):
    """Linux/Docker path: Xvfb + x11vnc + headed bundled Chromium."""
    # 1. Xvfb — virtual framebuffer.
    try:
        xvfb = subprocess.Popen(
            ["Xvfb", DISPLAY, "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.8)
        if xvfb.poll() is not None:
            _cleanup()
            return jsonify({"success": False, "message": "Xvfb no pudo iniciarse"}), 500
    except FileNotFoundError:
        return jsonify({"success": False, "message": "Xvfb no instalado en el contenedor"}), 500

    # 2. x11vnc — exposes the display over RFB for the Flask noVNC bridge.
    try:
        x11vnc = subprocess.Popen(
            [
                "x11vnc", "-display", DISPLAY,
                "-forever", "-nopw", "-quiet", "-shared",
                "-rfbport", str(VNC_PORT),
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.5)
        if x11vnc.poll() is not None:
            _cleanup()
            return jsonify({"success": False, "message": "x11vnc no pudo iniciarse"}), 500
    except FileNotFoundError:
        _cleanup()
        return jsonify({"success": False, "message": "x11vnc no instalado"}), 500

    # 3. notebooklm login — headed bundled Chromium on the virtual display.
    #    NOTE: no ``--browser chrome`` here; the SDK drives the bundled Chromium.
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    cmd = [PYTHON, "-m", "notebooklm", "login", "--fresh", "--storage", storage_path]

    log_path = os.path.join(COOKIE_DIR, "login.log")
    log_fh = open(log_path, "ab", buffering=0)
    login_proc = subprocess.Popen(
        cmd, env=env, stdout=log_fh, stderr=log_fh, start_new_session=True,
    )
    time.sleep(1.0)

    if login_proc.poll() is not None:
        log_fh.close()
        _cleanup()
        detail = ""
        try:
            with open(log_path, "r", errors="replace") as fh:
                detail = fh.read().strip().splitlines()[-1][:200]
        except (OSError, IndexError):
            pass
        return jsonify({
            "success": False,
            "message": "El login de NotebookLM falló al arrancar. "
                       + (f"Detalle: {detail}" if detail else "Revisa login.log."),
        }), 500

    _write_state("running", mode="vnc",
                 login_pid=login_proc.pid,
                 xvfb_pid=xvfb.pid,
                 x11vnc_pid=x11vnc.pid,
                 account=account_email,
                 storage_path=storage_path)
    _schedule_timeout()

    # encrypt must follow the request scheme: noVNC derives the WS URL from the
    # page protocol, so forcing encrypt=1 breaks plain-HTTP (localhost, tunnels).
    encrypt = "1" if request.scheme == "https" else "0"
    return jsonify({
        "success": True,
        "mode": "vnc",
        "message": "Completa el login de Google en la pantalla embebida de abajo.",
        # path must NOT start with '/' (noVNC's UI.connect does url += '/' + path).
        "vnc_url": f"/novnc/vnc.html?autoconnect=true&resize=scale"
                   f"&encrypt={encrypt}&path=novnc/ws",
    })


def _start_chrome_window(account_email: str | None, storage_path: str):
    """macOS native dev path: real desktop Chrome window."""
    if _missing_desktop_chrome():
        _write_state("idle")
        return jsonify({
            "success": False,
            "missing_chrome": True,
            # The reassurance is load-bearing, not decoration. This error used
            # to blame the user's Google account, which sent people off to
            # reset a password that was never the problem. tests/
            # test_nb_login_chrome_diagnosis.py pins the phrase on purpose: keep
            # it, or update that test deliberately — not by accident.
            "message": (
                "Falta Google Chrome en este equipo. El login local abre el "
                "Chrome de escritorio; instálalo o ejecuta la app en Docker "
                "(modo VNC, sin Chrome). No es un problema de tu cuenta."
            ),
        }), 500

    cmd = [PYTHON, "-m", "notebooklm", "login", "--browser", "chrome",
           "--storage", storage_path]
    log_path = os.path.join(COOKIE_DIR, "login.log")
    log_fh = open(log_path, "ab", buffering=0)
    login_proc = subprocess.Popen(
        cmd, stdout=log_fh, stderr=log_fh, start_new_session=True,
    )
    time.sleep(1.0)
    if login_proc.poll() is not None:
        log_fh.close()
        _write_state("idle")
        return jsonify({
            "success": False,
            "message": "El proceso de login falló al arrancar. Revisa login.log.",
        }), 500

    _write_state("running", mode="chrome",
                 login_pid=login_proc.pid,
                 account=account_email,
                 storage_path=storage_path)
    _schedule_timeout()
    return jsonify({
        "success": True,
        "mode": "chrome",
        "message": "Se ha abierto Google Chrome. Completa el login y vuelve aquí.",
    })


@bp.route("/settings/notebooklm/login-status", methods=["GET"])
@token_required
def login_status(current_user_id: int):
    """Poll the current login session.

    Returns:
        {running, browser_alive, login_complete, storage_created, started_at}
    """
    state = _read_state()
    running = state["status"] == "running"
    browser_alive = _pid_alive(state.get("login_pid"))
    login_pid = state.get("login_pid")
    login_complete = login_pid is not None and not _pid_alive(login_pid)
    storage_created = _storage_created(state.get("storage_path"))

    # Auto-close once the process finished AND the cookies landed.
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
    """Stop the session, kill the browser/VNC, and check for cookies."""
    try:
        _cleanup()
        connected, found_email = _check_connected()

        if found_email:
            set_active_profile(current_user_id, found_email)
            register_profile_owner(current_user_id, found_email)

        msg = "Cookies guardadas correctamente" if connected else (
            "Sesión cerrada. No se detectaron cookies — prueba de nuevo "
            "o usa el método de subida manual."
        )

        return jsonify({"success": True, "message": msg, "connected": connected})
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({
            "success": False,
            "message": f"Error al detener el login: {exc}",
            "connected": False,
        }), 500
