"""The OpenZen sidecar (local ``opencode serve``) must be launched by macOS.

Without this, nothing starts ``opencode serve`` on a Mac and the app log fills
with ``OpenCode connection error: [Errno 61] Connection refused``. The fix has
to get four things right, each asserted here:

* **LaunchAgent identity + lifecycle** -- label ``com.studyflow.opencode-sidecar``
  with RunAtLoad + KeepAlive, so it comes up at login and restarts if it dies.
* **The exact serve invocation** -- ``<binary> serve --port 54321 --hostname
  127.0.0.1`` (``opencode serve`` has no auth flags, so creds go in the env).
* **The env file** -- ``~/.studyflow/opencode_sidecar.env`` (chmod 600) with the
  three OPENCODE_SERVER_* keys the backend reads via ``apply_sidecar_env()``.
* **Idempotency** -- re-running must NEVER change an existing password.

``--dry-run`` must print the plist it would write and change nothing, and
``--uninstall`` must remove the agent while keeping the credentials.
"""

from __future__ import annotations

import os
import plistlib
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "launcher" / "install-opencode-sidecar.command"
UNINSTALL_WRAPPER = REPO / "launcher" / "uninstall-opencode-sidecar.command"
LABEL = "com.studyflow.opencode-sidecar"
ENV_RELPATH = Path(".studyflow") / "opencode_sidecar.env"
PLIST_RELPATH = Path("Library") / "LaunchAgents" / f"{LABEL}.plist"


def _make_env(home: Path) -> dict:
    """HOME pointing at a temp dir, with a fake opencode binary + stubs.

    ``launchctl`` and ``curl`` are stubbed (exit 0) via PATH so the installer
    can run end to end without touching the real system service manager.
    """
    binary = home / ".opencode" / "bin" / "opencode"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/bash\nexit 0\n")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)

    stub = home / "_stub_bin"
    stub.mkdir(exist_ok=True)
    for name in ("launchctl", "curl"):
        tool = stub / name
        tool.write_text("#!/bin/bash\nexit 0\n")
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{stub}:{env.get('PATH', '')}"
    return env


def _run(args: list[str], home: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(INSTALL), *args],
        env=_make_env(home),
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture()
def home(tmp_path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture()
def dry(home: Path):
    result = _run(["--dry-run"], home)
    assert result.returncode == 0, result.stderr
    return home, result.stdout


# ---------------------------------------------------------------- dry-run plist

def test_dry_run_emits_a_parseable_plist(dry):
    _, stdout = dry
    data = plistlib.loads(stdout.encode("utf-8"))
    assert data["Label"] == LABEL


def test_plist_has_the_keys_launchd_needs(dry):
    _, stdout = dry
    data = plistlib.loads(stdout.encode("utf-8"))
    assert data["RunAtLoad"] is True, "must start at login"
    assert data["KeepAlive"] is True, "must be restarted if it dies"
    assert data["ThrottleInterval"] == 10


def test_plist_runs_opencode_serve_on_the_right_host_port(dry):
    home, stdout = dry
    data = plistlib.loads(stdout.encode("utf-8"))
    argv = data["ProgramArguments"]

    assert argv[0] == str(home / ".opencode" / "bin" / "opencode")
    assert argv[1] == "serve"
    assert argv[argv.index("--port") + 1] == "54321"
    assert argv[argv.index("--hostname") + 1] == "127.0.0.1"


def test_plist_exports_the_sidecar_credentials(dry):
    _, stdout = dry
    data = plistlib.loads(stdout.encode("utf-8"))
    env = data["EnvironmentVariables"]

    assert env["OPENCODE_SERVER_URL"] == "http://127.0.0.1:54321"
    assert env["OPENCODE_SERVER_USERNAME"]
    assert env["OPENCODE_SERVER_PASSWORD"]


def test_plist_logs_go_to_a_file_launchd_owns(dry):
    _, stdout = dry
    data = plistlib.loads(stdout.encode("utf-8"))
    assert data["StandardOutPath"].endswith("studyflow-opencode-sidecar.log")
    assert data["StandardErrorPath"] == data["StandardOutPath"]


def test_dry_run_changes_nothing(dry):
    home, _ = dry
    assert not (home / PLIST_RELPATH).exists()
    assert not (home / ENV_RELPATH).exists()
    assert not (home / ".studyflow").exists()
    assert not (home / "Library").exists()


# ---------------------------------------------------------------- env file

def test_install_writes_env_file_and_plist(home):
    result = _run([], home)
    assert result.returncode == 0, result.stderr

    env_file = home / ENV_RELPATH
    plist = home / PLIST_RELPATH
    assert env_file.is_file()
    assert plist.is_file()

    mode = stat.S_IMODE(env_file.stat().st_mode)
    assert mode == 0o600, f"credentials must be private, got {oct(mode)}"

    text = env_file.read_text()
    assert "OPENCODE_SERVER_URL=http://127.0.0.1:54321" in text
    assert "OPENCODE_SERVER_USERNAME=" in text
    assert "OPENCODE_SERVER_PASSWORD=" in text

    data = plistlib.loads(plist.read_bytes())
    assert data["EnvironmentVariables"]["OPENCODE_SERVER_PASSWORD"] in text


def test_reinstall_preserves_existing_password(home):
    _run([], home)
    env_file = home / ENV_RELPATH

    def password() -> str:
        for line in env_file.read_text().splitlines():
            if line.startswith("OPENCODE_SERVER_PASSWORD="):
                return line.split("=", 1)[1]
        raise AssertionError("no password line")

    first = password()
    assert _run([], home).returncode == 0
    assert password() == first, "re-running must not rotate the password"


def test_install_respects_a_preexisting_env_file(home):
    env_file = home / ENV_RELPATH
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        "OPENCODE_SERVER_URL=http://127.0.0.1:54321\n"
        "OPENCODE_SERVER_USERNAME=keepme\n"
        "OPENCODE_SERVER_PASSWORD=secret-keep\n"
    )
    assert _run([], home).returncode == 0
    text = env_file.read_text()
    assert "OPENCODE_SERVER_USERNAME=keepme" in text
    assert "OPENCODE_SERVER_PASSWORD=secret-keep" in text

    data = plistlib.loads((home / PLIST_RELPATH).read_bytes())
    assert data["EnvironmentVariables"]["OPENCODE_SERVER_PASSWORD"] == "secret-keep"


# ---------------------------------------------------------------- uninstall

def test_uninstall_removes_agent_but_keeps_credentials(home):
    _run([], home)
    plist = home / PLIST_RELPATH
    env_file = home / ENV_RELPATH
    assert plist.is_file() and env_file.is_file()

    result = _run(["--uninstall"], home)
    assert result.returncode == 0, result.stderr
    assert not plist.exists(), "--uninstall must remove the plist"
    assert env_file.is_file(), "credentials must be preserved"


def test_uninstall_dry_run_keeps_the_plist(home):
    _run([], home)
    plist = home / PLIST_RELPATH
    result = _run(["--dry-run", "--uninstall"], home)
    assert result.returncode == 0, result.stderr
    assert plist.exists(), "--dry-run --uninstall must not delete the plist"
    assert LABEL in result.stderr


def test_uninstall_wrapper_delegates(home):
    _run([], home)
    plist = home / PLIST_RELPATH
    assert plist.is_file()

    result = subprocess.run(
        ["bash", str(UNINSTALL_WRAPPER)],
        env=_make_env(home), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert not plist.exists()


# ---------------------------------------------------------------- app uninstaller

def test_main_uninstaller_knows_the_sidecar_label():
    """scripts/uninstall-macos.sh must be able to stop this service too."""
    text = (REPO / "scripts" / "uninstall-macos.sh").read_text()
    assert '"com.studyflow.opencode-sidecar"' in text
    assert "opencode-sidecar|com.studyflow.opencode-sidecar)" in text
