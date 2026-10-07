"""Engine selection for StudyFlow — Phase LF (local-first).

database.py used to be hard-wired to psycopg2. This module lets the same
helper functions serve either engine, chosen at startup:

- **SQLite** when ``STUDYFLOW_DB_ENGINE=sqlite`` (or no ``DATABASE_URL``).
  One file on the user's disk; this is the local-first default.
- **PostgreSQL** when ``DATABASE_URL`` is set and the engine is not forced
  to sqlite. The v3 server deployment path, still covered by the tests.

The selection is deliberately narrow. It is not an ORM and it does not
abstract SQL: the dialect differences are handled once in
``sqlite_compat``, and the ~539 call sites keep issuing the same
statements they always did.

Why the default flips to SQLite
-------------------------------
``DATABASE_URL`` unset is the signal. In local-first there is no server,
so there is nothing for a DSN to point at; treating "no DSN" as "use a
local file" makes the zero-config install work with no environment
variable at all, which is what the double-click installer relies on.
Setting ``STUDYFLOW_DB_ENGINE=postgres`` forces the server path even when
a DSN is absent, so the tests can assert on the choice explicitly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Filename of the local database inside the data directory.
SQLITE_FILENAME = "studyflow.db"

#: Environment variable that pins the engine regardless of DSN presence.
ENGINE_ENV = "STUDYFLOW_DB_ENGINE"

#: Environment variable overriding the data directory.
DATA_DIR_ENV = "STUDYFLOW_DATA_DIR"

#: Resolved engine, set by select_engine() during init_db().
_active_engine: Optional[str] = None

ENGINE_SQLITE = "sqlite"
ENGINE_POSTGRES = "postgres"


def _env_flag(name: str) -> Optional[bool]:
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    return value.strip().lower() in ("1", "true", "yes", "on", "sqlite", "postgres")


def data_dir() -> Path:
    """Directory holding the local database and its sidecar files.

    Order of preference:

    1. ``STUDYFLOW_DATA_DIR`` — explicit override, used by the tests and
       by the installer's "portable mode".
    2. ``$XDG_DATA_HOME/studyflow`` — the conventional per-user location
       on Linux.
    3. ``~/Library/Application Support/studyflow`` on macOS, matching
       where the platform expects mutable app state, falling back to
       ``~/.local/share/studyflow`` elsewhere.

    The directory is created on demand. Note this is a directory, not the
    database: a single ``.db`` file is what the user copies to move their
    data to another machine, and sidecar files (WAL, backups) sit beside
    it rather than inside it.
    """
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        path = Path(override).expanduser()
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            path = Path(xdg).expanduser() / "studyflow"
        elif os.name == "nt":  # pragma: no cover - not the target platform
            base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
            path = Path(base) / "studyflow"
        else:
            # macOS keeps app state under Application Support; anything
            # else follows the XDG default.
            if os.uname().sysname == "Darwin":
                path = Path.home() / "Library" / "Application Support" / "studyflow"
            else:
                path = Path.home() / ".local" / "share" / "studyflow"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sqlite_path() -> Path:
    """Full path of the local database file."""
    return data_dir() / SQLITE_FILENAME


def active_engine() -> Optional[str]:
    """The engine chosen by the last select_engine() call."""
    return _active_engine


def select_engine() -> str:
    """Decide which engine to use and record the decision.

    Returns ``"sqlite"`` or ``"postgres"``.

    Resolution order, most explicit first:

    1. ``STUDYFLOW_DB_ENGINE=sqlite|postgres`` — always wins.
    2. ``STUDYFLOW_DB_ENGINE`` set to anything truthy without a recognised
       value is ignored rather than guessed, so a typo surfaces as the
       DSN-based default instead of a mystery engine.
    3. No ``DATABASE_URL`` → sqlite. This is the local-first default.
    4. Otherwise → postgres, the v3 deployment path.
    """
    global _active_engine

    forced = (os.environ.get(ENGINE_ENV) or "").strip().lower()
    if forced in (ENGINE_SQLITE, "sqlite3", "file"):
        _active_engine = ENGINE_SQLITE
    elif forced in (ENGINE_POSTGRES, "postgresql", "psql", "pg"):
        _active_engine = ENGINE_POSTGRES
    elif not os.environ.get("DATABASE_URL"):
        _active_engine = ENGINE_SQLITE
    else:
        _active_engine = ENGINE_POSTGRES
    return _active_engine


def describe() -> Dict[str, Any]:
    """A small dict describing the active configuration.

    Called by /api/health so the local-first build can be identified at a
    glance, and by the tests to assert the engine choice.
    """
    engine = _active_engine or select_engine()
    info: Dict[str, Any] = {"engine": engine}
    if engine == ENGINE_SQLITE:
        info["path"] = str(sqlite_path())
    else:
        info["dsn_configured"] = bool(os.environ.get("DATABASE_URL"))
    return info
