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
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator

from flask import Blueprint, jsonify, request

from ai.notebooklm.profiles import (
    register_profile_owner,
    set_active_profile,
)
from ai.notebooklm.utils import COOKIE_DIR
from routes.auth import token_required

bp = Blueprint("notebooklm_login", __name__)

log = logging.getLogger("notebooklm_login")

# ── Configuration ──────────────────────────────────────────────────────
LOGIN_TIMEOUT = 600  # 10 minutes
VNC_PORT = 5900
DISPLAY_NUM = 99
DISPLAY = f":{DISPLAY_NUM}"

# Shared state via JSON file. The gevent worker runs a single process, but the
# state file keeps the API stateless across restarts / worker respawns.
# NOT a hardcoded "/tmp/...": Windows has no /tmp by default, and writing to
# C:\tmp (sic) would raise FileNotFoundError on every state write.
STATE_FILE = os.path.join(tempfile.gettempdir(), "notebooklm_login.json")

PYTHON = sys.executable  # the python running the Flask app

# Desktop Chrome executables that ``channel="chrome"`` would resolve to via
# PATH. Used by the Chrome-window fallback; probed, never assumed.
_CHROME_BINARIES = (
    "google-chrome", "google-chrome-stable", "chrome", "chromium-browser",
)

# The cookie pair the NotebookLM SDK requires for an authenticated session.
# A storage_state.json with only __Host-GAPS / NID is NOT a session: reporting
# it as "conectado" made the SDK fail later with
# "Missing required cookies: SID, __Secure-1PSIDTS".
REQUIRED_COOKIES = {"SID", "__Secure-1PSIDTS"}


def _windows_chrome_paths() -> list[str]:
    """Known install locations of Chrome on Windows.

    On a normal Windows box Chrome is NOT on PATH: it lives under Program
    Files. ``shutil.which("chrome")`` therefore reports it as missing even
    though Playwright's ``channel="chrome"`` resolves it fine through the
    registry (we verified that on the target machine: version 154 launched).
    The guard in _missing_desktop_chrome() must probe these dirs too, or the
    login button would answer "Falta Google Chrome" with Chrome installed.
    """
    dirs = [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    return [
        os.path.join(d, "Google", "Chrome", "Application", "chrome.exe")
        for d in dirs if d
    ]


def _macos_chrome_paths() -> list[str]:
    """Known install locations of Chrome on macOS.

    On macOS Chrome is NOT on PATH either: ``shutil.which("chrome")`` answers
    None for a normal install under /Applications, yet Playwright's
    ``channel="chrome"`` resolves the ``.app`` bundle fine. macOS only allows
    Chrome in /Applications or ~/Applications, so probing both covers a real
    install. Without this the guard would claim "Falta Google Chrome" on a Mac
    that has it (the mirror of the Windows bug fixed in _windows_chrome_paths).
    """
    return [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        os.path.expanduser(
            "~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        ),
    ]


def _vnc_available() -> bool:
    """True when the container has the virtual-display toolchain."""
    return bool(shutil.which("Xvfb") and shutil.which("x11vnc"))


def _missing_desktop_chrome() -> bool:
    """True when the desktop Chrome required by ``--browser chrome`` is absent.

    Pure predicate (only shutil.which / os.path.isfile). Probes PATH binaries
    plus the known Windows and macOS install dirs, because on both those
    platforms Chrome lives outside PATH.
    """
    if any(shutil.which(name) for name in _CHROME_BINARIES):
        return False
    return not any(
        os.path.isfile(path)
        for path in (*_windows_chrome_paths(), *_macos_chrome_paths())
    )


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
    if os.name == "nt":
        return _win_pid_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _win_pid_alive(pid: int) -> bool:
    """Windows liveness probe that CANNOT kill the target.

    ``os.kill(pid, 0)`` is a harmless probe on POSIX but on Windows CPython
    documents that any signal other than CTRL_C_EVENT/CTRL_BREAK_EVENT causes
    the process to be unconditionally killed by TerminateProcess (exit code =
    sig). That murdered our login helper on the first login-status poll: the
    Chrome window blinked out and the app reported a failed login. Probe with
    OpenProcess instead — it fails for dead PIDs and never writes the target.
    """
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


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


def _kill_leftovers() -> None:
    """Kill login-related processes left over from previous sessions.

    ``_cleanup`` only knows the PIDs recorded in the state file. A previous
    session that lost its state (container restart, timer race) leaves its
    Xvfb / x11vnc / Chromium running; those zombies pinned the server at
    load 256 until a manual restart. pkill patterns are scoped to THIS user
    and to the login helpers only — never a broad ``pkill Xvfb``. No-op on
    Windows (no pkill) where the Chrome-window path leaves nothing behind.
    """
    if os.name == "nt" or not shutil.which("pkill"):
        return
    for pattern in (
        "nb_login_browser.py",
        f"Xvfb {DISPLAY}",
        f"x11vnc -display {DISPLAY}",
        "notebooklm login",
    ):
        try:
            subprocess.run(
                ["pkill", "-f", pattern],
                capture_output=True, timeout=5,
            )
        except Exception:
            # Best-effort reaping: a leftover-cleanup hiccup must never abort
            # the login itself (or make the endpoint 500 before it launches).
            pass


def _storage_created(storage_path: str | None) -> bool:
    return bool(storage_path and os.path.isfile(storage_path))


def _storage_has_required_cookies(path: str) -> bool:
    """True only when ``path`` holds BOTH required Google cookies.

    A partial login (only __Host-GAPS/NID) is what the user hit on the
    server: the file existed, so the old check said "conectado", but the
    SDK then errored with "Missing required cookies: SID, __Secure-1PSIDTS".
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    names = {
        c.get("name")
        for c in data.get("cookies", [])
        if (c.get("domain") or "").endswith(".google.com")
    }
    return REQUIRED_COOKIES.issubset(names)


def _iter_storage_candidates() -> Iterator[tuple[str | None, str]]:
    """Yield ``(email_or_None, storage_path)`` in the v2 search order.

    global file → profiles/<email>/ → <COOKIE_DIR>/<email>/. Skips the
    SDK-generated non-email dirs (000-default, etc.).
    """
    nb_home = COOKIE_DIR
    global_path = os.path.join(nb_home, "storage_state.json")
    if os.path.isfile(global_path):
        yield None, global_path

    profiles_dir = os.path.join(nb_home, "profiles")
    if os.path.isdir(profiles_dir):
        for entry in sorted(os.listdir(profiles_dir)):
            if "@" not in entry and entry != "default":
                continue
            sub_path = os.path.join(profiles_dir, entry, "storage_state.json")
            if os.path.isfile(sub_path):
                yield entry, sub_path

    if os.path.isdir(nb_home):
        for entry in sorted(os.listdir(nb_home)):
            sub_path = os.path.join(nb_home, entry, "storage_state.json")
            if os.path.isfile(sub_path) and entry != "profiles" and \
                    ("@" in entry or entry == "default"):
                yield entry, sub_path


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
    """True ONLY when a saved storage file holds BOTH required cookies.

    Existence is not connection: a storage_state.json with only
    __Host-GAPS/NID is a partial login. The old check returned True for any
    file and the UI showed "conectado"; the SDK then died with
    "Missing required cookies: SID, __Secure-1PSIDTS".
    """
    for email, path in _iter_storage_candidates():
        if _storage_has_required_cookies(path):
            return True, email
    return False, None


def _remove_partial_storage() -> None:
    """Delete storage files that lack the required cookies.

    Mirrors the disconnect route's cleanup (routes/notebooklm_settings.py:
    it removes the profile cookies). A partial file left behind makes the
    next login-start skip the real sign-in and keeps the SDK broken.
    """
    profiles_root = os.path.realpath(os.path.join(COOKIE_DIR, "profiles"))
    for _email, path in _iter_storage_candidates():
        if _storage_has_required_cookies(path):
            continue
        parent = os.path.realpath(os.path.dirname(path))
        # A profile under profiles/<email>/ is wiped whole (same as the
        # disconnect route); the global file and its dir are left alone.
        if parent != profiles_root and parent.startswith(profiles_root + os.sep):
            shutil.rmtree(parent, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                pass


# ── Endpoints ──────────────────────────────────────────────────────────

@bp.route("/settings/notebooklm/login-start", methods=["POST"])
@token_required
def login_start(current_user_id: int):
    """Start a login session (VNC in Docker, Chrome window on macOS)."""
    _cleanup()
    # Also reap zombies from sessions that lost their state file.
    _kill_leftovers()

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
    return _start_chrome_window(
        account_email, storage_path, os.path.join(profile_dir, "browser_profile")
    )


def _wait_for_tcp(host: str, port: int, timeout: float = 2.0,
                  interval: float = 0.1) -> bool:
    """Poll until ``host:port`` accepts a TCP connection or ``timeout`` elapses.

    Used after spawning x11vnc so noVNC's WS bridge finds a live RFB server
    instead of racing it (which surfaces as an immediate WS close 1005).
    """
    deadline = time.time() + timeout
    while True:
        try:
            with socket.create_connection((host, port), timeout=interval):
                return True
        except OSError:
            if time.time() >= deadline:
                return False
            time.sleep(interval)


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
                # -localhost/-nolookup: only the in-container noVNC bridge
                # (127.0.0.1:5900) reaches RFB, and reverse DNS on the peer IP
                # is skipped. Without -localhost x11vnc would bind all
                # interfaces; the beta runs with host networking, so :5900
                # would then be exposed on the LAN/Tailnet.
                "-localhost", "-nolookup",
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

    # 2b. Wait until x11vnc is actually accepting RFB connections on :5900.
    #     Otherwise noVNC connects before the socket is live and the WS bridge
    #     closes immediately (browser code 1005).
    if not _wait_for_tcp("127.0.0.1", VNC_PORT, timeout=2.0):
        if x11vnc.poll() is not None:
            _cleanup()
            return jsonify({"success": False,
                            "message": "x11vnc no escucha en el puerto 5900"}), 500
        log.warning("x11vnc no confirmó :%d en 2s; continúo de todas formas", VNC_PORT)

    # 3. Interactive login — headed BUNDLED Chromium on the virtual display.
    #    We do NOT use ``notebooklm login`` here: its landing check treats
    #    notebook.google.com's SPA (HTTP 200 even unauthenticated) as "Already
    #    logged in", captures only __Host-GAPS/NID, and closes the browser in
    #    ~1s — so the VNC screen never showed a sign-in form and the SDK stayed
    #    broken with "Missing required cookies: SID, __Secure-1PSIDTS".
    #    scripts/nb_login_browser.py opens accounts.google.com (server-rendered)
    #    and waits for the REAL OAuth cookies, keeping the window open for the
    #    user to type credentials over VNC. ``--channel chromium`` = bundled
    #    Chromium (no desktop Chrome needed; the beta is x86_64 but this also
    #    works on arm64 where Playwright ships no chrome channel).
    browser_profile = os.path.join(os.path.dirname(storage_path), "browser_profile")
    helper = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts", "nb_login_browser.py",
    )
    env = os.environ.copy()
    env["DISPLAY"] = DISPLAY
    cmd = [PYTHON, helper,
           "--profile-dir", browser_profile,
           "--storage", storage_path,
           "--channel", "chromium",
           "--timeout", str(LOGIN_TIMEOUT)]

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

    # encrypt must follow the request scheme (respect proxy headers behind tunnels)
    proto = (
        request.headers.get("X-Forwarded-Proto")
        or request.headers.get("X-Forwarded-Protocol")
        or request.headers.get("Forwarded", "")
    )
    if "https" in proto:
        proto_scheme = "https"
    else:
        proto_scheme = request.scheme
    # encrypt must follow the request scheme: noVNC derives the WS URL from the
    # page protocol, so forcing encrypt=1 breaks plain-HTTP (localhost, tunnels).
    encrypt = "1" if proto_scheme == "https" else "0"
    return jsonify({
        "success": True,
        "mode": "vnc",
        "message": "Completa el login de Google en la pantalla embebida de abajo.",
        # path must NOT start with '/' (noVNC's UI.connect does url += '/' + path).
        "vnc_url": f"/novnc/vnc.html?autoconnect=true&resize=scale"
                   f"&encrypt={encrypt}&path=novnc/ws",
    })


def _start_chrome_window(account_email: str | None, storage_path: str,
                         browser_profile: str):
    """Real desktop Chrome window (macOS native dev / Windows local install).

    Drives our own Playwright helper (scripts/nb_login_browser.py) instead of
    ``notebooklm login --browser chrome``: the CLI's URL-based landing check
    false-positives against notebook.google.com's SPA (HTTP 200 unauthenticated
    → "Already logged in" → partial cookies → Chrome closed without the sign-in
    form ever appearing). The helper opens the accounts.google.com form and
    waits for the real OAuth cookies before persisting.
    """
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

    helper = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "scripts", "nb_login_browser.py",
    )
    cmd = [PYTHON, helper,
           "--profile-dir", browser_profile,
           "--storage", storage_path,
           "--timeout", str(LOGIN_TIMEOUT)]
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

        if connected:
            if found_email:
                set_active_profile(current_user_id, found_email)
                register_profile_owner(current_user_id, found_email)
            return jsonify({
                "success": True,
                "message": "Cookies guardadas correctamente",
                "connected": True,
            })

        # Honest failure: the file may exist but lacks the Google session
        # cookies. Wipe the partial login so the next attempt starts clean.
        _remove_partial_storage()
        return jsonify({
            "success": True,
            "connected": False,
            "message": (
                "Login incompleto: no se guardaron las cookies de Google "
                "(SID, __Secure-1PSIDTS). Rehaz el login y completa el "
                "acceso a Google."
            ),
        })
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({
            "success": False,
            "message": f"Error al detener el login: {exc}",
            "connected": False,
        }), 500
