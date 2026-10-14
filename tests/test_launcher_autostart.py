"""Auto-start install must produce a launchd plist that actually keeps
StudyFlow up without a Terminal.

The whole point of this feature is that a student who never opens a Terminal
still finds the red "API" button working after a reboot. That rests on four
things the plist has to get right, each of which is asserted here:

* **RunAtLoad + KeepAlive** -- come up at login *and* be restarted if it dies.
* **--headless --no-browser** -- a service must never pop a browser window,
  and ``launch.py`` needs the headless flag or it would exit right after
  starting the server (and launchd would respawn it in a loop).
* **A stable program path** -- an unzipped folder in ~/Downloads can vanish;
  the LaunchAgent must point at ~/Applications/StudyFlow.
* **--dry-run changes nothing** -- so it can be tested on a real machine.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "launcher" / "install-autostart.command"
UNINSTALL = REPO / "launcher" / "uninstall-autostart.command"
LABEL = "com.studyflow.launcher"


def _run_dry(home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    return subprocess.run(
        ["bash", str(INSTALL), "--dry-run"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture()
def dry(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = _run_dry(home)
    assert result.returncode == 0, result.stderr
    return home, result.stdout


def test_dry_run_emits_a_parseable_plist(dry):
    _, stdout = dry
    # A plist that does not parse is a service that silently never loads.
    data = plistlib.loads(stdout.encode("utf-8"))
    assert data["Label"] == LABEL


def test_plist_has_the_keys_launchd_needs(dry):
    _, stdout = dry
    data = plistlib.loads(stdout.encode("utf-8"))

    assert data["RunAtLoad"] is True, "must start at login"
    assert data["KeepAlive"] is True, "must be restarted if it dies"
    assert data["ThrottleInterval"] == 10


def test_plist_runs_headless_without_a_browser(dry):
    _, stdout = dry
    data = plistlib.loads(stdout.encode("utf-8"))
    argv = data["ProgramArguments"]

    assert "--headless" in argv, "without it launch.py would exit and loop"
    assert "--no-browser" in argv, "a service must never open a window"
    # The interpreter must be the installed venv, not a bare python3.
    assert argv[0].endswith("/.venv/bin/python")


def test_plist_exports_the_homebrew_env_md2pdf_needs(dry):
    """launchd hands jobs a bare environment; without these two the backend
    crash-loops: cffi cannot dlopen pango (OSError, not ImportError) and the
    md->html step cannot find ``node`` outside /opt/homebrew/bin.
    """
    _, stdout = dry
    data = plistlib.loads(stdout.encode("utf-8"))
    envvars = data["EnvironmentVariables"]

    assert envvars["DYLD_LIBRARY_PATH"] == "/opt/homebrew/lib"
    path = envvars["PATH"].split(":")
    assert "/opt/homebrew/bin" in path, "node lives here on Apple Silicon"
    # Replacing launchd's PATH wholesale would hide the system tools the
    # venv and launch.py itself need.
    assert "/usr/bin" in path and "/bin" in path


def test_plist_points_at_the_stable_install_path(dry):
    home, stdout = dry
    data = plistlib.loads(stdout.encode("utf-8"))
    stable = home / "Applications" / "StudyFlow"

    assert data["WorkingDirectory"] == str(stable)
    assert data["ProgramArguments"][1] == str(stable / "launcher" / "launch.py")
    assert str(stable) in data["ProgramArguments"][0]
    # Logs go to a file launchd owns, not to a tty.
    assert data["StandardOutPath"].endswith("studyflow-launcher.log")
    assert data["StandardErrorPath"] == data["StandardOutPath"]


def test_dry_run_changes_nothing(dry):
    home, _ = dry
    # No plist written, no program copied, no venv created.
    assert not (home / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()
    assert not (home / "Applications").exists()
    assert not (home / "Library" / "Logs").exists()


def test_uninstall_dry_run_keeps_everything(tmp_path):
    home = tmp_path / "home"
    (home / "Library" / "LaunchAgents").mkdir(parents=True)
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    plist.write_text("placeholder")

    env = dict(os.environ)
    env["HOME"] = str(home)
    result = subprocess.run(
        ["bash", str(UNINSTALL), "--dry-run"],
        env=env, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0
    assert plist.exists(), "--dry-run must not delete the plist"
    assert f"{LABEL}" in result.stdout


def test_main_uninstaller_knows_the_launcher_label():
    """scripts/uninstall-macos.sh must be able to stop this service too."""
    text = (REPO / "scripts" / "uninstall-macos.sh").read_text()
    assert '"com.studyflow.launcher"' in text
    assert "launcher|com.studyflow.launcher)" in text
