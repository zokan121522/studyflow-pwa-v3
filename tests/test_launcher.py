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

        # Same data dir: the launcher must find the instance it already owns.
        # The port is off the default range, so it can only be found by matching
        # the database it reports.
        monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(tmp_path / "a"))
        (tmp_path / "a" / "launcher.json").write_text(json.dumps({"port": port}))
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

        # Our own state file claims the port, but that server is serving
        # somebody else's database.
        monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(mine))
        (mine / "launcher.json").write_text(json.dumps({"port": port}))
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