#!/usr/bin/env python3
# launcher/launch.py
"""Start, stop and inspect the local StudyFlow instance.

Usage
-----
    python3 launcher/launch.py              start (or reopen) and open the browser
    python3 launcher/launch.py --no-browser start without opening a browser
    python3 launcher/launch.py --foreground run in this terminal, Ctrl-C stops
    python3 launcher/launch.py --stop       stop the local instance
    python3 launcher/launch.py status       report what is running

Why a launcher exists at all
----------------------------
A PWA cannot write to the user's disk, so SQLite needs a process next to the
browser. That process must be invisible and require no configuration, because
the person using it is a student opening StudyFlow to revise, not an operator
starting a web server. Everything below exists to make that true: a fixed port
with a scan, no DSN to fill in, no console window left on screen, and the data
outside the repository so deleting or re-cloning the code cannot take it away.

Three decisions worth knowing about
-----------------------------------
**The data lives in the platform's per-user data directory**, not next to the
code. ``engine.data_dir()`` already defaults to
``~/Library/Application Support/studyflow`` on macOS, so this launcher only has
to not fight it.

**The port is remembered, not rediscovered blindly.** If the instance is already
up, the launcher opens the browser and exits instead of starting a second
server on a different port. Two servers on two ports over one SQLite file is a
story nobody wants to debug, and SQLite would serialise them while the app
quietly showed stale data in one of the two windows.

**SECRET_KEY is generated once and kept with the data.** It is the key that
encrypts stored SCORM credentials (``backend/secret_box.py``), so a fresh one on
every launch would silently render every saved credential undecryptable. It is
also written 0600: the app's own fallback is a constant that is public in this
repository, and for a local install protecting a credential with a value
everyone can read is no protection at all.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

#: First port tried. 8477 is arbitrary and unprivileged; the scan below is what
#: actually makes it reliable, since anything else on the machine may hold it.
DEFAULT_PORT = 8477
PORT_SCAN_RANGE = 40

#: How long to wait for /api/health before giving up on the child.
HEALTH_TIMEOUT_S = 45.0

STATE_FILENAME = "launcher.json"
SECRET_FILENAME = "secret.key"
LOG_FILENAME = "launcher.log"


# ─── paths ─────────────────────────────────────────────────────────


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Where the local database and this launcher's state live."""
    sys.path.insert(0, str(repo_root() / "backend"))
    import engine  # noqa: PLC0415  (needs the path above)

    return engine.data_dir()


def state_file() -> Path:
    return data_dir() / STATE_FILENAME


def log_file() -> Path:
    return data_dir() / LOG_FILENAME


# ─── single instance ───────────────────────────────────────────────


def _probe(port: int, timeout: float = 1.5) -> dict | None:
    """Return /api/health if *port* is our app, else None.

    The identity check matters: port 8477 being open does not mean StudyFlow
    is behind it. ``engine`` is in the payload, so a foreign service answers
    with something that has no ``engine`` key and we keep looking.
    """
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=timeout
        ) as resp:
            if resp.status != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) and "engine" in payload else None


def _owns(payload: dict) -> bool:
    """Is this payload *our* instance, serving our database?

    Health only says "a StudyFlow is here". With two data directories on one
    machine -- a developer's real install and an isolated test one, or simply a
    stale copy -- that is not enough: attaching to a server backed by a
    different database would show the user their data was missing, with no
    way to tell where it went. So the comparison is on the database file the
    server reports, which is the thing that has to match.
    """
    engine_info = payload.get("engine") or {}
    if engine_info.get("engine") != "sqlite":
        return False  # a Postgres-backed instance is not the local install
    reported = engine_info.get("path")
    if not reported:
        return False
    return _same_file(reported, _our_database())


def _same_file(a: str, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _our_database() -> Path:
    sys.path.insert(0, str(repo_root() / "backend"))
    import engine  # noqa: PLC0415

    return engine.sqlite_path()


def running_port() -> int | None:
    """The port of a live instance *of this install*, or None."""
    try:
        recorded = json.loads(state_file().read_text()).get("port")
    except (OSError, ValueError, json.JSONDecodeError):
        recorded = None

    # The state file is inside the data directory, so it already scopes the
    # check to this install; try it before scanning.
    if isinstance(recorded, int):
        payload = _probe(recorded)
        if payload and _owns(payload):
            return recorded

    # No usable state file. A server may still be ours -- started by hand, or
    # the file lost while it ran -- so scan, but accept a port only if it
    # serves our database. Anything else is a foreign instance and is left
    # alone; pick_port() will step over it.
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN_RANGE):
        payload = _probe(port, timeout=0.3)
        if payload and _owns(payload):
            return port
    return None


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def pick_port() -> int:
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN_RANGE):
        if _port_is_free(port):
            return port
    raise SystemExit(
        f"No free port in {DEFAULT_PORT}-{DEFAULT_PORT + PORT_SCAN_RANGE - 1}."
    )


# ─── environment ───────────────────────────────────────────────────


def secret_key(directory: Path) -> str:
    """A per-install SECRET_KEY, generated once and reused forever after."""
    path = directory / SECRET_FILENAME
    try:
        existing = path.read_text().strip()
        if existing:
            return existing
    except OSError:
        pass
    key = secrets.token_urlsafe(48)
    path.write_text(key + "\n")
    try:
        path.chmod(0o600)  # a credential should not be world-readable
    except OSError:
        pass  # e.g. an exotic filesystem; the file is in the user's own dir
    return key


def child_env(port: int) -> dict:
    """Environment for the serving process.

    ``DATABASE_URL`` is cleared rather than merely unset-if-absent: a developer
    with Postgres exported in their shell would otherwise silently launch a
    local install against a shared database. An empty value is not a DSN, and
    engine.select_engine() treats "no usable DSN" as SQLite.
    """
    directory = data_dir()
    uploads = directory / "uploads"
    pdfs = uploads / "pdfs"
    uploads.mkdir(parents=True, exist_ok=True)
    pdfs.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update(
        {
            "STUDYFLOW_DB_ENGINE": "sqlite",
            "STUDYFLOW_DATA_DIR": str(directory),
            "STUDYFLOW_PORT": str(port),
            "DATABASE_URL": "",
            "SECRET_KEY": secret_key(directory),
            # Without this the upload path defaults to /app/uploads/pdfs, a
            # Docker path that does not exist on a laptop.
            "PDF_UPLOAD_FOLDER": str(pdfs),
            "SCRAPED_DIR": str(directory / "scraped"),
            # Never off by default here: the hosted deployment sets it
            # explicitly, and leaving scraping on against localhost is the
            # more useful default for the person who set this up.
            "FLASK_DEBUG": "0",
        }
    )
    return env


# ─── lifecycle ─────────────────────────────────────────────────────


def child_executable() -> str:
    """The interpreter to run the server with.

    On Windows a double-clicked launcher leaves a console window on screen, and
    the server never exits, so that window would sit there for the whole
    session -- exactly the "web server on my desktop" impression the launcher
    exists to avoid. ``pythonw.exe`` is the same interpreter without the
    console, so output must go to the log file (which ``spawn`` already does)
    rather than to a tty.

    ``--foreground`` deliberately keeps the console interpreter: the user asked
    for output and Ctrl-C in this terminal.
    """
    if os.name != "nt":
        return sys.executable
    # os.path.join rather than Path.with_name: the Path flavour follows the
    # running OS, so on a Mac CI run Path("...").with_name("pythonw.exe") tries
    # to build a WindowsPath and raises UnsupportedOperation. Here os.name has
    # been faked to "nt" to exercise this branch, and the join must still work.
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return pythonw if os.path.exists(pythonw) else sys.executable


def spawn(port: int, env: dict, foreground: bool = False) -> subprocess.Popen:
    log = log_file()
    handle = open(log, "a", buffering=1)
    handle.write(f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} port={port} ---\n")
    handle.close()
    return subprocess.Popen(
        [sys.executable if foreground else child_executable(),
         str(repo_root() / "launcher" / "serve.py")],
        env=env,
        stdout=open(log, "a", buffering=1),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        # New session: the server must outlive the terminal that launched it,
        # which is what makes a double-click work. Windows has no setsid and
        # ignores the flag, which is why the console is handled by pythonw.exe
        # in child_executable() instead.
        start_new_session=os.name != "nt",
        cwd=str(repo_root()),
    )


def wait_until_healthy(port: int, process: subprocess.Popen) -> bool:
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if _probe(port):
            return True
        time.sleep(0.25)
    return False


def record_state(port: int, pid: int) -> None:
    state_file().write_text(
        json.dumps({"port": port, "pid": pid, "started": time.time()}, indent=2)
    )


def open_browser(port: int) -> None:
    import webbrowser

    webbrowser.open(f"http://127.0.0.1:{port}/")


def stop() -> int:
    port = running_port()
    if port is None:
        print("StudyFlow no está corriendo.")
        try:
            state_file().unlink()
        except OSError:
            pass
        return 0

    try:
        recorded = json.loads(state_file().read_text()).get("pid")
    except (OSError, ValueError, json.JSONDecodeError):
        recorded = None
    if isinstance(recorded, int):
        # The child's own pid, in its own session, so no group signalling is
        # needed and nothing else in the user's session gets caught.
        try:
            os.kill(recorded, signal.SIGTERM)
            print(f"StudyFlow detenido (puerto {port}, pid {recorded}).")
        except ProcessLookupError:
            print("StudyFlow ya estaba parado; limpiando estado.")
        except PermissionError:
            print(f"No puedo parar el pid {recorded}: permiso denegado.", file=sys.stderr)
            return 1
    else:
        print(f"StudyFlow corre en el puerto {port} pero no sé su pid.", file=sys.stderr)
        return 1

    for _ in range(40):
        if _probe(port, timeout=0.3) is None:
            break
        time.sleep(0.25)
    try:
        state_file().unlink()
    except OSError:
        pass
    return 0


def status() -> int:
    port = running_port()
    directory = data_dir()
    if port is None:
        print(f"StudyFlow: parado.  Datos en {directory}")
        return 1
    payload = _probe(port) or {}
    print(f"StudyFlow: corriendo en http://127.0.0.1:{port}/")
    print(f"  motor   : {(payload.get('engine') or {}).get('engine', '?')}")
    print(f"  datos   : {directory}")
    print(f"  log     : {log_file()}")
    return 0


def start(open_it: bool = True) -> int:
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)

    existing = running_port()
    if existing is not None:
        # The whole point of the remembered port: a second double-click opens
        # the app that is already running instead of starting a rival.
        print(f"StudyFlow ya está corriendo en http://127.0.0.1:{existing}/")
        if open_it:
            open_browser(existing)
        return 0

    port = pick_port()
    env = child_env(port)
    process = spawn(port, env)
    if not wait_until_healthy(port, process):
        code = process.poll()
        print(
            f"El servidor no arrancó (código {code}). Log:\n  {log_file()}",
            file=sys.stderr,
        )
        return 1

    record_state(port, process.pid)
    print(f"StudyFlow en http://127.0.0.1:{port}/")
    print(f"  datos : {directory}")
    print(f"  log   : {log_file()}")
    if open_it:
        open_browser(port)
    return 0


def foreground() -> int:
    """Run the server in this terminal. Ctrl-C stops it."""
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    port = pick_port()
    env = child_env(port)
    try:
        # Record the CHILD's pid, not our own: --stop signals the recorded pid,
        # and this launcher is still running while the server serves. Recording
        # os.getpid() here made --stop kill the wrong process and then time out
        # waiting for a port that a live server still holds.
        process = spawn(port, env, foreground=True)
        record_state(port, process.pid)
        process.wait()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            state_file().unlink()
        except OSError:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="launch.py",
        description="Arranca la instancia local de StudyFlow.",
    )
    parser.add_argument("--no-browser", action="store_true", help="no abrir el navegador")
    parser.add_argument("--foreground", action="store_true", help="servir en esta terminal")
    parser.add_argument("--stop", action="store_true", help="detener la instancia local")
    parser.add_argument("command", nargs="?", choices=["start", "stop", "status"])
    args = parser.parse_args(argv)

    if args.stop or args.command == "stop":
        return stop()
    if args.command == "status":
        return status()
    if args.foreground:
        return foreground()
    return start(open_it=not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())