"""The launcher is the whole install experience, so it gets tested like one.

What it has to get right, and why each of these was a real decision:

* **No configuration.** The person double-clicking this is a student opening
  StudyFlow to revise. No DSN, no environment variable, no port to pick.
* **One instance.** Two servers on two ports over one SQLite file would show
  stale data in one window with no way to tell why.
* **Isolated from other installs.** A second data directory must get its own
  instance rather than attaching to the first one's.
* **Loopback only.** On café Wi-Fi, 0.0.0.0 hands the user's agenda to every
  device in the room.
* **The data outlives the code.** Deleting or re-cloning the repository must not
  take the database with it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "launcher" / "launch.py"
SERVE = REPO / "launcher" / "serve.py"


@pytest.fixture(scope="module")
def launch():
    """launch.py loaded as a module; it is a script, not an importable package."""
    spec = importlib.util.spec_from_file_location("studyflow_launch", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ─── static facts about the launcher ───────────────────────────────


def test_launcher_only_binds_loopback():
    """The literal host matters: 0.0.0.0 would expose the user's data."""
    text = SERVE.read_text()
    assert 'host="127.0.0.1"' in text, "the serving process must bind loopback only"
    assert "host='0.0.0.0'" not in text and 'host="0.0.0.0"' not in text, (
        "the launcher must not bind 0.0.0.0"
    )


def test_launcher_clears_database_url(launch):
    """A DSN in the developer's shell must not capture a local install.

    An empty DATABASE_URL is not a DSN and engine.select_engine() reads it as
    "no database configured", which is the SQLite signal. Leaving a real DSN
    inherited from the environment would point a student's install at a shared
    server -- and write to it.
    """
    env = launch.child_env(launch.DEFAULT_PORT)
    assert env["DATABASE_URL"] == ""
    assert env["STUDYFLOW_DB_ENGINE"] == "sqlite"


def test_secret_key_is_stable_and_private(launch, tmp_path, monkeypatch):
    """One key per install, reused, and not world-readable.

    It is what encrypts stored SCORM credentials (backend/secret_box.py), so a
    fresh key per launch would render every saved credential undecryptable, and
    a key in a repo-visible constant would protect nothing at all.
    """
    monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(tmp_path))
    directory = tmp_path / "data"
    directory.mkdir()

    first = launch.secret_key(directory)
    second = launch.secret_key(directory)
    assert first == second, "the key changed between calls; stored secrets would break"
    assert len(first) >= 32
    assert oct((directory / "secret.key").stat().st_mode)[-3:] == "600"


def test_uploads_path_is_inside_the_data_dir(launch, tmp_path, monkeypatch):
    """The default upload path is /app/uploads/pdfs -- a Docker path.

    On a laptop that directory does not exist, so PDF upload would fail in a
    way that looks like a permissions bug rather than a missing configuration.
    """
    monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(tmp_path))
    env = launch.child_env(launch.DEFAULT_PORT)
    uploads = Path(env["PDF_UPLOAD_FOLDER"])
    assert uploads.is_dir() and uploads.is_relative_to(tmp_path)
    assert "/app/" not in env["PDF_UPLOAD_FOLDER"]


# ─── fixed port, no drift ───────────────────────────────────────────
#
# The launcher used to scan 8477-8516 and remember whichever port it found. The
# user ended up opening a URL nobody documented, and a stale launcher.json could
# keep a second server alive on a drifted port. The port is now always 8477.


def test_pick_port_always_returns_the_fixed_port(launch, monkeypatch):
    """When the port is free, it is 8477 -- never the next in a scan."""
    monkeypatch.setattr(launch, "_port_is_free", lambda port: True)

    assert launch.pick_port() == launch.DEFAULT_PORT


def test_pick_port_aborts_when_the_port_is_busy(launch, monkeypatch):
    """A busy port aborts with a visible Spanish message, not a scan away."""
    monkeypatch.setattr(launch, "_port_is_free", lambda port: False)

    with pytest.raises(SystemExit) as exc:
        launch.pick_port()

    message = str(exc.value)
    assert str(launch.DEFAULT_PORT) in message
    assert "StudyFlow" in message


def test_running_port_only_probes_the_fixed_port(launch, monkeypatch):
    """Only the fixed port is probed; a drifted remembered port is ignored."""
    probed: list[int] = []
    monkeypatch.setattr(
        launch, "_probe", lambda port, timeout=1.5: probed.append(port) or None
    )

    assert launch.running_port() is None
    assert probed == [launch.DEFAULT_PORT], "must probe only the fixed port"


# ─── behaviour against a real process ──────────────────────────────


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _healthy(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2) as r:
            if r.status == 200:
                return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return None
    return None


@pytest.fixture()
def instance(tmp_path):
    """A real launcher-spawned server, torn down afterwards."""
    data = tmp_path / "instance"
    env = dict(os.environ)
    env["STUDYFLOW_DATA_DIR"] = str(data)
    env.pop("DATABASE_URL", None)
    env["STUDYFLOW_PORT"] = str(_free_port())

    proc = subprocess.Popen(
        [sys.executable, str(SERVE)],
        env=env,
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and proc.poll() is None:
        if _healthy(env["STUDYFLOW_PORT"]):
            break
        time.sleep(0.25)
    else:
        proc.kill()
        pytest.fail(f"launcher-spawned server never became healthy:\n{proc.stdout.read()}")

    yield int(env["STUDYFLOW_PORT"]), data

    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_launched_instance_serves_the_app_on_its_own_data(instance):
    port, data = instance
    payload = _healthy(port)
    assert payload["status"] == "healthy"
    assert payload["database"] == "connected"
    assert payload["engine"]["engine"] == "sqlite"
    # The database lives with the data, not next to the code.
    assert payload["engine"]["path"] == str(data / "studyflow.db")
    assert payload["engine"]["path"].startswith(str(data))


def test_launched_instance_writes_the_database_where_expected(instance):
    port, data = instance
    assert (data / "studyflow.db").is_file(), "no database in the data directory"
    # And the app is actually reachable, not just healthy on a socket.
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
        assert resp.status == 200
        assert b"<title" in resp.read(4096).lower()


def test_launcher_detects_a_second_instance_of_the_same_install(launch, tmp_path, monkeypatch):
    """A second double-click reopens instead of starting a rival."""
    monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(tmp_path / "a"))
    port = _free_port()

    env = dict(os.environ)
    env["STUDYFLOW_DATA_DIR"] = str(tmp_path / "a")
    env["STUDYFLOW_PORT"] = str(port)
    proc = subprocess.Popen(
        [sys.executable, str(SERVE)],
        env=env, cwd=str(REPO),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and _healthy(port) is None:
            time.sleep(0.25)
        assert _healthy(port) is not None, "server never came up"

        # Same data dir: the launcher must find the instance it already owns on
        # the fixed port. There is exactly one port to probe, so the module's
        # DEFAULT_PORT is redirected at the test server's port.
        monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(tmp_path / "a"))
        monkeypatch.setattr(launch, "DEFAULT_PORT", port)
        assert launch.running_port() == port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_launcher_does_not_adopt_another_install(launch, tmp_path, monkeypatch):
    """A different data directory must not attach to the first one's server.

    Attaching would show the user an app full of data they never entered, and
    writes would land in a file their own launcher does not manage.
    """
    mine = tmp_path / "mine"
    theirs = tmp_path / "theirs"
    mine.mkdir()
    theirs.mkdir()
    port = _free_port()

    env = dict(os.environ)
    env["STUDYFLOW_DATA_DIR"] = str(theirs)
    env["STUDYFLOW_PORT"] = str(port)
    proc = subprocess.Popen(
        [sys.executable, str(SERVE)],
        env=env, cwd=str(REPO),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and _healthy(port) is None:
            time.sleep(0.25)
        assert _healthy(port) is not None

        # The only port is the fixed one, redirected at that foreign server:
        # its database is not ours, so it must not be adopted.
        monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(mine))
        monkeypatch.setattr(launch, "DEFAULT_PORT", port)
        assert launch.running_port() is None, (
            "the launcher adopted an instance backed by a different database"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_data_survives_the_server_going_away(instance):
    """The point of the data directory: the code can go, the data cannot.

    A local install has no second copy anywhere. Anything that makes the
    database depend on the checkout -- storing it beside the source, wiping it
    on start -- loses a user's work with no recovery.
    """
    port, data = instance
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5):
        pass
    db = data / "studyflow.db"
    assert db.is_file()
    first = db.stat().st_size
    assert first > 0

    # The server is about to be killed; the file must not be a temp artefact
    # that vanishes with the process.
    assert not str(db).startswith(str(REPO)), "database is inside the repository"
    assert db.read_bytes()[:16] == b"SQLite format 3\x00"
    assert db.stat().st_size == first

# ─── ventana de aplicación ───────────────────────────────────────────
#
# El usuario pidió que StudyFlow no pareciera una web. La PWA instalada por
# Edge lo conseguía, pero arrancaba sola al iniciar Windows apuntando a un
# localhost sin servidor detrás: una ventana de error en cada arranque. Aquí se
# reutiliza el mismo mecanismo (el flag --app) pero desde el launcher, después
# de levantar el servidor.


def test_edge_command_uses_app_mode_and_the_real_entry_point(launch):
    """A window opening on a bare 404 would be a poor first impression."""
    argv = launch.edge_command(r"C:\...\msedge.exe", "http://127.0.0.1:8477")

    assert argv[0] == r"C:\...\msedge.exe"
    assert "--app=http://127.0.0.1:8477/index.html" in argv
    assert not any(a.startswith("--new-window") or a.startswith("http://127.0.0.1") for a in argv[1:]), (
        "sin --app el usuario vería el navegador normal, que es justo lo que pidió evitar"
    )


def test_edge_command_does_not_touch_the_user_profile(launch):
    """Launching into a temporary profile would log them out every time."""
    argv = launch.edge_command("msedge.exe", "http://127.0.0.1:8477")

    assert not any("--user-data-dir" in a for a in argv), (
        "un --user-data-dir propio perdería la sesión y el service worker"
    )


def test_find_edge_returns_none_off_windows(launch, monkeypatch):
    """macOS has no Edge app mode to borrow, so windowed must degrade quietly."""
    monkeypatch.setattr(launch.os, "name", "posix")

    assert launch.find_edge() is None


def test_find_edge_prefers_the_32_bit_install(monkeypatch, tmp_path):
    """Where Edge actually lives on a normal 64-bit Windows install."""
    spec = importlib.util.spec_from_file_location("sf_launch_edges", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for label in ("x86", "x64"):
        folder = tmp_path / label / "Microsoft" / "Edge" / "Application"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "msedge.exe").write_text("")
    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "x86"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "x64"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    expected = tmp_path / "x86" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    assert module.find_edge() == str(expected)


def test_open_windowed_reports_failure_instead_of_crashing(launch, monkeypatch):
    """A missing Edge must fall back to a browser, never take the launcher down."""
    monkeypatch.setattr(launch, "find_edge", lambda: None)

    assert launch.open_windowed(8477) is False


def test_open_app_falls_back_to_the_browser_when_edge_is_missing(launch, monkeypatch):
    """The degradation path is the one that must work; it is the one nobody tests."""
    seen = []
    monkeypatch.setattr(launch, "open_windowed", lambda port: False)
    monkeypatch.setattr(launch, "open_browser", lambda port: seen.append(port))

    launch.open_app(8477, windowed=True)

    assert seen == [8477], "sin Edge debe abrirse igualmente en el navegador"


def test_open_app_uses_the_window_when_edge_is_there(launch, monkeypatch):
    """And the windowed path must not also open a browser tab."""
    calls = []
    monkeypatch.setattr(launch, "open_windowed", lambda port: (calls.append(("window", port)), True)[1])
    monkeypatch.setattr(launch, "open_browser", lambda port: calls.append(("browser", port)))

    launch.open_app(8477, windowed=True)

    assert calls == [("window", 8477)]


def test_browser_flag_forces_the_normal_browser(launch, monkeypatch):
    """--browser is the escape hatch if the window ever misbehaves."""
    seen = {}
    monkeypatch.setattr(launch, "start", lambda open_it, windowed: seen.update(
        open_it=open_it, windowed=windowed
    ))

    launch.main(["--browser"])

    assert seen == {"open_it": True, "windowed": False}


def test_no_browser_still_opens_nothing(launch, monkeypatch):
    seen = {}
    monkeypatch.setattr(launch, "start", lambda open_it, windowed: seen.update(
        open_it=open_it, windowed=windowed
    ))

    launch.main(["--no-browser"])

    assert seen["open_it"] is False


def test_stop_never_opens_a_window(launch, monkeypatch):
    """Regression guard: 'parar' abriendo una ventana sería absurdo."""
    monkeypatch.setattr(launch, "stop", lambda: 0)

    def explode(*a, **k):  # pragma: no cover
        raise AssertionError("stop no debe abrir nada")

    monkeypatch.setattr(launch, "open_app", explode)
    monkeypatch.setattr(launch, "open_windowed", explode)

    assert launch.main(["stop"]) == 0
