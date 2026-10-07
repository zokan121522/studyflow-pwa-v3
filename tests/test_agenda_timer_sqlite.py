"""The play/pause/stop timer, end to end, against the real SQLite engine.

The bug these exist for: ``agenda_state`` clamped the elapsed time with
Postgres ``GREATEST``, which SQLite has no such function for. Pausing a
session raised ``no such function: GREATEST``, the endpoint answered 500, and
the frontend never learned the timer had been paused -- so it kept ticking,
and the next click looked like a fresh start that reset the clock to zero.

Two earlier test files missed this. ``test_sqlite_routes_smoke.py`` calls
every route *without* a token, so it only proves the route resolves to 401 --
the SQL never runs. And the frontend tests assert on HTML, so they cannot see
what the database does. This one authenticates and writes, so the statement
actually executes on the engine the portable build ships.
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
REPO_ROOT = Path(__file__).resolve().parent.parent
# The routes import ``from backend import database``, so the test has to reach
# the module the same way. Importing a second copy as plain ``database`` would
# give the test its own connection and its own engine state, and the fixture
# would be testing a database the app never touches.
for _path in (str(REPO_ROOT), str(BACKEND)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


@pytest.fixture()
def timer_app(tmp_path, monkeypatch):
    """A real app on a real SQLite file, with one user and one session."""
    target = tmp_path / "data"
    target.mkdir()
    monkeypatch.setenv("STUDYFLOW_DATA_DIR", str(target))
    monkeypatch.setenv("STUDYFLOW_DB_ENGINE", "sqlite")
    monkeypatch.setenv("SECRET_KEY", "timer-test-secret")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # Both modules cache state: ``database`` caches its connection and
    # ``engine`` caches the resolved engine. Reset the pair so a second test
    # in the session opens a fresh file instead of reusing the first one.
    import database as db_mod
    import engine

    db_mod.close_pool()
    monkeypatch.setattr(engine, "_active_engine", None, raising=False)

    from backend import database as db
    from backend.routes.auth import generate_token
    import server

    # Explicit init before any query. create_app()'s own bootstrap path
    # re-enters the schema builder from inside _seed_local_user, and the
    # inner get_db() then falls through to the Postgres branch and dies on a
    # None pool. Initialising up front is what the app does on a clean
    # launch, and it keeps this fixture out of that unrelated bug.
    db.init_db()

    app = server.create_app()
    app.config.update(TESTING=True, JWT_ACCESS_TOKEN_EXPIRES=3600)

    with app.app_context():
        user_id = db.execute_returning(
            "INSERT INTO users (email, password_hash, created_at) "
            "VALUES (%s, %s, NOW()) RETURNING id",
            ("timer@test.local", "x"),
        )
        session_id = db.execute_returning(
            "INSERT INTO sessions "
            "(id, user_id, day_date, start_time, end_time, title, notes, "
            " timer_state, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW()) RETURNING id",
            ("timer-session-1", user_id["id"], "2026-10-06", "10:00", "11:00",
             "Timer", "", "idle"),
        )
        token = generate_token(user_id["id"])

    yield {
        "app": app,
        "client": app.test_client(),
        "token": token,
        "session_id": session_id["id"],
        "db": db,
    }

    db_mod.close_pool()


def _patch_state(timer_app, state, timer_state):
    client = timer_app["client"]
    return client.post(
        "/api/agenda/session/state",
        json={
            "session_id": timer_app["session_id"],
            "state": state,
            "timer_state": timer_state,
        },
        headers={"Authorization": "Bearer " + timer_app["token"]},
    )


def _row(timer_app):
    return timer_app["db"].query_one(
        "SELECT timer_state, timer_started_at, timer_paused_at, "
        "timer_elapsed, timer_paused_duration FROM sessions WHERE id = %s",
        (timer_app["session_id"],),
    )


def _iso_seconds_ago(seconds):
    """An ISO-8601 UTC timestamp ``seconds`` in the past.

    The route writes ``datetime.utcnow().isoformat()`` with no offset, so this
    matches that spelling exactly. Mixing in a ``+00:00`` would make the
    timestamp un-parseable by SQLite's date functions and the elapsed time
    would silently collapse to NULL.
    """
    from datetime import datetime, timedelta

    return (datetime.utcnow() - timedelta(seconds=seconds)).isoformat()


class TestPlayPauseStop:
    def test_play_starts_the_clock(self, timer_app):
        resp = _patch_state(timer_app, "in_progress", "running")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        row = _row(timer_app)
        assert row["timer_state"] == "running"
        assert row["timer_started_at"] is not None

    def test_pause_does_not_error(self, timer_app):
        """The regression: this used to answer 500, no such function: GREATEST."""
        _patch_state(timer_app, "in_progress", "running")
        resp = _patch_state(timer_app, "in_progress", "paused")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        row = _row(timer_app)
        assert row["timer_state"] == "paused"
        assert row["timer_paused_at"] is not None
        assert row["timer_elapsed"] >= 0

    def test_paused_elapsed_does_not_move_on_its_own(self, timer_app):
        _patch_state(timer_app, "in_progress", "running")
        _patch_state(timer_app, "in_progress", "paused")
        first = _row(timer_app)["timer_elapsed"]
        second = _row(timer_app)["timer_elapsed"]
        assert first == second

    def test_resume_keeps_the_elapsed_time(self, timer_app):
        """Pause then resume must not restart the clock at zero."""
        _patch_state(timer_app, "in_progress", "running")
        _patch_state(timer_app, "in_progress", "paused")
        elapsed_when_paused = _row(timer_app)["timer_elapsed"]

        # Backdate the pause mark instead of sleeping. A pause that lasts
        # under a second contributes zero seconds, which would make the
        # accumulation below assert nothing at all.
        timer_app["db"].execute(
            "UPDATE sessions SET timer_paused_at = ?, timer_paused_duration = 30",
            (_iso_seconds_ago(30),),
        )
        resp = _patch_state(timer_app, "in_progress", "running")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        row = _row(timer_app)
        assert row["timer_state"] == "running"
        assert row["timer_paused_at"] is None, "resume must clear the pause mark"
        # The pause is folded into the offset instead of being discarded:
        # 30 already banked plus the 30 backdated seconds.
        assert row["timer_paused_duration"] == pytest.approx(60, abs=2)
        assert row["timer_elapsed"] == pytest.approx(elapsed_when_paused, abs=1)

    def test_second_pause_still_pauses(self, timer_app):
        """The reported symptom: pause, play, pause -- and it reset to zero."""
        _patch_state(timer_app, "in_progress", "running")
        _patch_state(timer_app, "in_progress", "paused")
        resp = _patch_state(timer_app, "in_progress", "running")
        assert resp.status_code == 200
        resp = _patch_state(timer_app, "in_progress", "paused")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        row = _row(timer_app)
        assert row["timer_state"] == "paused"
        assert row["timer_paused_at"] is not None
        assert row["timer_elapsed"] >= 0

    def test_stop_accumulates_and_clears(self, timer_app):
        _patch_state(timer_app, "in_progress", "running")
        _patch_state(timer_app, "in_progress", "paused")
        resp = _patch_state(timer_app, "completed", "stopped")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        row = _row(timer_app)
        assert row["timer_state"] == "stopped"
        assert row["timer_started_at"] is None
        assert row["timer_paused_at"] is None

    def test_restored_session_with_null_start_still_pauses(self, timer_app):
        """A backup-restored row can carry a NULL timer_started_at."""
        timer_app["db"].execute(
            "UPDATE sessions SET timer_started_at = NULL, timer_elapsed = NULL"
        )
        resp = _patch_state(timer_app, "in_progress", "paused")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert _row(timer_app)["timer_elapsed"] == 0

    def test_invalid_timer_state_is_rejected(self, timer_app):
        resp = _patch_state(timer_app, "in_progress", "sideways")
        assert resp.status_code == 400

    def test_unknown_session_is_404(self, timer_app):
        client = timer_app["client"]
        resp = client.post(
            "/api/agenda/session/state",
            json={"session_id": 999999, "state": "in_progress",
                  "timer_state": "running"},
            headers={"Authorization": "Bearer " + timer_app["token"]},
        )
        assert resp.status_code == 404
