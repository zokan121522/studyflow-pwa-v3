"""The resident control helper: the PWA's only way to revive a down server.

The helper is deliberately tiny, so these tests pin the whole contract:

* loopback only -- a 0.0.0.0 bind would expose "start my server" to the café;
* two endpoints and nothing else;
* ``/start`` is idempotent when up (200), refuses to race itself (409) and
  answers immediately when it does start (202);
* ``/status`` has a stable shape.

No test ever spawns a real server: ``launch.start`` is monkeypatched.
"""

from __future__ import annotations

import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CONTROL = REPO / "launcher" / "control.py"


@pytest.fixture()
def control():
    """control.py loaded fresh per test, so its in-memory state is clean."""
    spec = importlib.util.spec_from_file_location("studyflow_control", CONTROL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def server(control):
    srv = control.make_server(0)  # ephemeral port
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _request(port: int, method: str, path: str) -> tuple[int, dict]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _get(port, path):
    return _request(port, "GET", path)


def _post(port, path):
    return _request(port, "POST", path)


def _until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ─── static facts ──────────────────────────────────────────────────


def test_control_binds_loopback_only():
    text = CONTROL.read_text()
    assert '("127.0.0.1",' in text
    # No quoted 0.0.0.0 host literal anywhere (the prose may mention the risk).
    assert '"0.0.0.0"' not in text
    assert "'0.0.0.0'" not in text


def test_control_port_defaults_and_env_override(control, monkeypatch):
    monkeypatch.delenv("STUDYFLOW_CONTROL_PORT", raising=False)
    assert control.control_port() == 8478
    monkeypatch.setenv("STUDYFLOW_CONTROL_PORT", "9123")
    assert control.control_port() == 9123


# ─── /status ───────────────────────────────────────────────────────


def test_status_shape_when_down(control, server, monkeypatch):
    port = server.server_address[1]
    monkeypatch.setattr(control.launch, "running_port", lambda: None)
    monkeypatch.setattr(control.launch, "log_file", lambda: Path("/tmp/launcher.log"))

    code, body = _get(port, "/status")

    assert code == 200
    assert set(body) >= {"server", "port", "version", "log", "started_at"}
    assert body["server"] == "down"
    assert body["port"] is None
    assert body["version"] is None
    assert body["started_at"] is None
    assert body["log"].endswith("launcher.log")
    assert Path(body["log"]).is_absolute()


def test_status_reports_up_port_and_version(control, server, monkeypatch):
    port = server.server_address[1]
    monkeypatch.setattr(control.launch, "running_port", lambda: 9999)
    monkeypatch.setattr(
        control.launch,
        "_probe",
        lambda p, timeout=1.5: {"engine": {"engine": "sqlite"}, "version": "9.9.9"},
    )
    monkeypatch.setattr(control.launch, "log_file", lambda: Path("/tmp/launcher.log"))

    code, body = _get(port, "/status")

    assert code == 200
    assert body["server"] == "up"
    assert body["port"] == 9999
    assert body["version"] == "9.9.9"


# ─── routing ───────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/", "/nope", "/start", "/status/extra"])
def test_unknown_get_paths_are_404(control, server, monkeypatch, path):
    monkeypatch.setattr(control.launch, "running_port", lambda: None)
    port = server.server_address[1]
    assert _get(port, path)[0] == 404


@pytest.mark.parametrize("path", ["/", "/nope", "/status"])
def test_unknown_post_paths_are_404(control, server, monkeypatch, path):
    monkeypatch.setattr(control.launch, "running_port", lambda: None)
    port = server.server_address[1]
    assert _post(port, path)[0] == 404


# ─── /start ────────────────────────────────────────────────────────


def test_start_when_already_up_is_200(control, server, monkeypatch):
    port = server.server_address[1]
    monkeypatch.setattr(control.launch, "running_port", lambda: 9999)
    monkeypatch.setattr(
        control.launch, "_probe", lambda p, timeout=1.5: {"engine": {}, "version": "1"}
    )

    code, body = _post(port, "/start")

    assert code == 200
    assert body == {"status": "already-up", "port": 9999}


def test_start_is_202_and_busy_while_in_flight(control, server, monkeypatch):
    port = server.server_address[1]
    state = {"port": None}
    entered = threading.Event()
    release = threading.Event()

    monkeypatch.setattr(control.launch, "running_port", lambda: state["port"])
    monkeypatch.setattr(
        control.launch,
        "_probe",
        lambda p, timeout=1.5: {"engine": {"engine": "sqlite"}, "version": "1"} if p else None,
    )
    monkeypatch.setattr(control.launch, "log_file", lambda: Path("/tmp/launcher.log"))

    def fake_start(open_it=True, windowed=True):
        entered.set()
        release.wait(5)
        state["port"] = 9999
        return 0

    monkeypatch.setattr(control.launch, "start", fake_start)

    code, body = _post(port, "/start")
    assert code == 202
    assert body == {"status": "starting"}

    assert entered.wait(5), "the worker never called launch.start"

    # While the start is in flight, a second request must not race it.
    assert _post(port, "/start") == (409, {"status": "busy"})
    assert _get(port, "/status")[1]["server"] == "starting"

    release.set()
    assert _until(lambda: _get(port, "/status")[1]["server"] == "up")
    status = _get(port, "/status")[1]
    assert status["port"] == 9999
    assert status["started_at"] is not None


def test_start_failure_is_recorded_for_status(control, server, monkeypatch):
    port = server.server_address[1]
    monkeypatch.setattr(control.launch, "running_port", lambda: None)
    monkeypatch.setattr(control.launch, "_probe", lambda p, timeout=1.5: None)
    monkeypatch.setattr(control.launch, "log_file", lambda: Path("/tmp/launcher.log"))
    monkeypatch.setattr(
        control.launch, "start", lambda open_it=True, windowed=True: 1
    )

    assert _post(port, "/start")[0] == 202

    assert _until(lambda: _get(port, "/status")[1]["server"] == "down")
    assert _until(lambda: _get(port, "/status")[1]["error"] is not None)
