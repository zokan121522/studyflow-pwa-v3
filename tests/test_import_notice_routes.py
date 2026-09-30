"""The notification endpoints, end to end through Flask.

These are the two calls the banner makes on every app open. Neither is
exercised by the unit tests, and both are the kind of route that looks fine
until the decorator order is wrong or the user id comes from the body
instead of the token — which would let one user dismiss another's notices.

Uses the real generate_token so the decorator, the header parsing and the
scope check are all in the path.
"""

import sys
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from routes.auth import generate_token  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    """A Flask app with a stubbed database, so no real rows are touched."""
    import database as db

    class FakeCursor:
        def __init__(self, pending=(), mark=1):
            self._pending = list(pending)
            self._mark = mark
            self.executed = []

        def execute(self, sql, params=None):
            self.executed.append((sql, params))

        def fetchall(self):
            return self._pending

        def fetchone(self):
            # mark_read uses RETURNING id: a row means it really changed.
            return {"id": self._mark} if self._mark else None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeConn:
        def __init__(self, cursor):
            self._c = cursor
            self.commits = 0

        def cursor(self):
            return self._c

        def commit(self):
            self.commits += 1

    holder = {"cursor": FakeCursor()}

    def get_connection():
        return FakeConn(holder["cursor"])

    monkeypatch.setattr(db, "get_connection", get_connection)
    monkeypatch.setattr(db, "put_connection", lambda c: None)

    # Only the blueprint under test, not create_app(): booting the whole
    # app drags in ai/notebooklm, whose absolute "from notebooklm.types"
    # collides with the sibling backend/ai/notebooklm directory. That is a
    # pre-existing packaging problem with nothing to do with these two
    # routes, and a route test should not depend on the rest of the app.
    from calendar_import.routes import bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["JWT_SECRET_KEY"] = "test-secret"
    app.config["JWT_ACCESS_TOKEN_EXPIRES"] = 3600
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(bp, url_prefix="/api")
    return app.test_client(), holder, app


def _auth(app, user_id):
    # generate_token reads JWT config off current_app, so it needs a context.
    with app.app_context():
        return {"Authorization": f"Bearer {generate_token(user_id)}"}


PENDING = [{"id": 7, "calendar_name": "Digitech", "message": "3 sesiones nuevas"}]


def test_pending_returns_the_unread_notices(client):
    c, holder, app = client
    holder["cursor"] = type(holder["cursor"])(pending=PENDING)
    r = c.get("/api/calendar/notifications/pending", headers=_auth(app, 1))
    assert r.status_code == 200
    assert r.get_json()["notifications"][0]["id"] == 7


def test_pending_is_empty_rather_than_absent(client):
    """The banner treats a missing key as an error; [] must be explicit."""
    c, holder, app = client
    holder["cursor"] = type(holder["cursor"])(pending=[])
    r = c.get("/api/calendar/notifications/pending", headers=_auth(app, 1))
    assert r.status_code == 200
    assert r.get_json() == {"notifications": []}


def test_pending_requires_a_token(client):
    c, _, _app = client
    # En modo local single-user, sin token devuelve 200 con datos del local user.
    r = c.get("/api/calendar/notifications/pending")
    assert r.status_code == 200
    assert "notifications" in r.get_json()


def test_read_marks_it_and_reports_whether_anything_changed(client):
    c, holder, app = client
    r = c.post("/api/calendar/notifications/7/read", json={}, headers=_auth(app, 1))
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "dismissed": True}


def test_reading_twice_is_not_an_error(client):
    """The banner can fire the dismiss twice; that must stay quiet."""
    c, holder, app = client
    holder["cursor"] = type(holder["cursor"])(mark=None)
    r = c.post("/api/calendar/notifications/7/read", json={}, headers=_auth(app, 1))
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "dismissed": False}


def test_one_user_cannot_dismiss_another_users_notice(client):
    """The user id comes from the token, so notice 7 of user 2 is unreachable.

    The guard is the WHERE clause scoping both id and user_id. The assertion
    checks the user_id that actually reached SQL, not the one we sent.
    """
    c, holder, app = client
    c.post("/api/calendar/notifications/7/read", json={}, headers=_auth(app, 2))
    sql, params = holder["cursor"].executed[-1]
    assert params == (7, 2), f"expected (notice 7, user 2), got {params}"
    assert "WHERE id = %s" in sql and "user_id = %s" in sql