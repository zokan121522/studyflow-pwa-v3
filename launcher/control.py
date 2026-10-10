#!/usr/bin/env python3
# launcher/control.py
"""Resident control helper for the local StudyFlow install.

Why this exists
---------------
``launch.py`` exits as soon as the local server is healthy (double-click ->
server up -> browser open -> launcher gone). That is exactly right for a
launcher, but it means that once the server goes down nothing is left
listening, so the PWA -- which cannot start a local process on its own -- has
no way to ask for it to be brought back. This helper is the small, always-on
piece that stays behind precisely so the web page can.

Security posture
----------------
``127.0.0.1`` only, never ``0.0.0.0``: on a laptop joined to café Wi-Fi, a
0.0.0.0 bind would let every device in the room ask this process to act. On
loopback the reachable set is the user's own machine, which is the machine
whose browser is the only intended caller anyway.

Exactly two endpoints, and neither takes a target:

* ``GET  /status`` -- is the server up, on which port, what version, where the
  log is. Read-only.
* ``POST /start``  -- bring the local server up. The thing that gets started is
  a constant (``launcher/serve.py``); the request body is ignored.

There is no endpoint to stop anything, to read the database, or to run a
command, and no request data ever reaches a shell. The worst an attacker who
already has local code execution could do is start a server the user could
have started by double-clicking the icon. That is an acceptable surface for a
feature whose whole point is to let a web page revive a down server without
making the user find a terminal.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

# The launcher's helpers are the single source of truth for "what is running"
# and "how to start it". Import it rather than re-implementing port/probe
# logic, which would silently drift from the real one.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import launch  # noqa: E402

DEFAULT_CONTROL_PORT = 8478

#: In-memory start state, shared between the request handler and the worker
#: thread. ``starting`` is True only while a start is in flight.
_STATE = {"starting": False, "started_at": None, "error": None}
_LOCK = threading.Lock()


def control_port() -> int:
    """Port to bind. Env override exists so tests can use an ephemeral port."""
    raw = os.environ.get("STUDYFLOW_CONTROL_PORT")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return DEFAULT_CONTROL_PORT


def _log(message: str) -> None:
    """Write a prefixed line to the shared launcher.log."""
    try:
        with open(launch.log_file(), "a", buffering=1) as handle:
            handle.write(f"[control {time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass  # a missing data dir must not take the helper down


def reset_state() -> None:
    """Clear the in-memory state (used by tests; harmless otherwise)."""
    with _LOCK:
        _STATE.update(starting=False, started_at=None, error=None)


def _probe_up(port: int | None) -> dict | None:
    if port is None:
        return None
    payload = launch._probe(port)
    return payload if isinstance(payload, dict) else None


def snapshot() -> dict:
    """The /status payload. Never raises: a probe error is reported as down."""
    with _LOCK:
        starting = _STATE["starting"]
        started_at = _STATE["started_at"]
        error = _STATE["error"]

    port = launch.running_port()
    payload = _probe_up(port)
    if port is not None and payload is not None:
        return {
            "server": "up",
            "port": port,
            "version": payload.get("version"),
            "log": str(launch.log_file()),
            "started_at": started_at,
            "error": None,
        }
    if starting:
        return {
            "server": "starting",
            "port": None,
            "version": None,
            "log": str(launch.log_file()),
            "started_at": started_at,
            "error": error,
        }
    return {
        "server": "down",
        "port": None,
        "version": None,
        "log": str(launch.log_file()),
        "started_at": None,
        "error": error,
    }


def _start_worker() -> None:
    """Run launch.start() off the request thread and record the outcome."""
    try:
        code = launch.start(open_it=False, windowed=False)
        port = launch.running_port()
        with _LOCK:
            _STATE["starting"] = False
            if code == 0 and port is not None:
                _STATE["started_at"] = time.time()
                _STATE["error"] = None
            else:
                _STATE["error"] = f"launch.start returned {code}"
    except Exception as exc:  # noqa: BLE001 -- must never kill the helper
        with _LOCK:
            _STATE["starting"] = False
            _STATE["error"] = f"{type(exc).__name__}: {exc}"


def request_start() -> tuple[int, dict]:
    """Decide what /start should answer and kick off a start if allowed."""
    port = launch.running_port()
    if port is not None and _probe_up(port) is not None:
        return 200, {"status": "already-up", "port": port}

    with _LOCK:
        if _STATE["starting"]:
            return 409, {"status": "busy"}
        _STATE["starting"] = True
        _STATE["error"] = None

    threading.Thread(target=_start_worker, name="studyflow-start", daemon=True).start()
    return 202, {"status": "starting"}


class _Handler(BaseHTTPRequestHandler):
    server_version = "StudyFlowControl/1.0"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 -- stdlib naming
        if urlsplit(self.path).path == "/status":
            self._send(200, snapshot())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 -- stdlib naming
        if urlsplit(self.path).path == "/start":
            code, payload = request_start()
            self._send(code, payload)
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        _log("control " + (fmt % args))


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(port: int | None = None) -> ControlServer:
    """Bind 127.0.0.1 only. ``port=None`` -> env/default; ``0`` -> ephemeral."""
    if port is None:
        port = control_port()
    # The literal loopback address is deliberate and must stay literal: there is
    # no configuration knob that could turn this into a 0.0.0.0 bind.
    return ControlServer(("127.0.0.1", port), _Handler)


def main(argv: list[str] | None = None) -> int:
    server = make_server()
    host, port = server.server_address
    _log(f"helper listening on http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        _log("helper stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
