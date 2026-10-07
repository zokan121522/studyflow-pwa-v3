"""Smoke test: every registered route, against SQLite.

Why this file exists
--------------------
The unit tests exercise database.py's helpers directly, and they were all
green while nine call sites across four route modules were broken. The gap
was not coverage of a function, it was that nothing ever called the routes.

The bugs that got through were engine-shaped, and only visible at runtime:

- get_db() yielded the raw connection, so "with conn.cursor() as cur"
  raised "does not support the context manager protocol" -- psycopg2's
  cursor is a context manager, sqlite3's is not. Nine call sites, broken
  on SQLite only, i.e. on the engine nobody exercises by hand.
- models.py called .isoformat() on every created_at, which is a datetime
  under psycopg2 and a str under SQLite. Twenty-three sites, and the
  first course create died on it.

Both were found by calling the endpoints, not by reading them. So this
test calls them, all of them, and asserts only that they do not 500. It is
not asserting the routes are correct -- that is what the per-feature work
and the manual UI pass are for. It asserts the floor: on this engine, a
request does not reach a wall of "this only works on Postgres".

A 4xx is a pass here. Rejecting a missing body, an unknown id or a request
without a token is the route working. A 5xx is a translation gap, a
Postgres-only assumption, or a genuine bug.
"""

from __future__ import annotations

import pytest

# Path params Flask would have to invent; the smoke test only cares that
# the handler is reached, not what it decides.
_SAMPLE_VALUES = {
    "int": "1",
    "path": "1",
    "string": "1",
    "course_id": "1",
    "topic_id": "1",
    "block_id": "1",
    "session_id": "1",
    "week_id": "W1",
    "habit_id": "1",
    "todo_id": "1",
    "card_id": "1",
    "quiz_id": "1",
    "note_id": "1",
    "category_id": "1",
    "file_id": "1",
    "task_id": "1",
    "profile_id": "1",
    "document_id": "1",
    "page_id": "1",
    "item_id": "1",
    "name": "x",
    "kind": "x",
    "date": "2026-01-01",
    "date_str": "2026-01-01",
    "day": "2026-01-01",
    "lang": "es",
    "format": "json",
    "status": "x",
    "filename": "x.txt",
    "path_id": "1",
    "action": "x",
    "field": "x",
    "value": "x",
    "option_id": "1",
    "deck_id": "1",
    "entry_id": "1",
    "module_id": "1",
    "unit_id": "1",
    "prompt": "x",
    "url": "http://localhost",
    "path_param": "1",
    "id": "1",
    "uid": "1",
    "slug": "x",
    "year": "2026",
    "month": "1",
    "day_of_month": "1",
    "start": "2026-01-01T09:00",
    "end": "2026-01-01T10:00",
}

#: Routes whose 500 is the correct answer on a machine without the
#: dependency they need, mapped to why. An explicit, reviewable list rather
#: than a blanket exemption: a 500 that is *not* on this list still fails the
#: suite, and adding an entry means saying out loud which external program the
#: feature shells out to.
_ENVIRONMENT_DEPENDENT = {
    # Opens the desktop Chrome to run a Google login, so it needs Chrome
    # installed. It answers 500 with a message saying exactly that, which is
    # the behaviour being asserted here: a missing dependency is reported, not
    # crashed on. The Docker deployment runs it through VNC instead.
    "POST /api/settings/notebooklm/login-start":
        "requires desktop Google Chrome for the interactive login",
}

#: Methods worth calling with no body. A POST/PUT/PATCH that validates a
#: body will 400, which is a pass; the point is reaching the handler.
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _fill(rule: str) -> str:
    """Substitute every <param> in a Flask rule with something plausible."""
    filled = rule
    for name in _SAMPLE_VALUES:
        filled = filled.replace(f"<{name}>", _SAMPLE_VALUES[name])
    return filled


def _rules(app):
    """Every concrete rule, with path params resolved to sample values.

    Flask lists the same rule twice when a view has both
    @bp.get and @app.route; dedupe on (rule, method) so the report is not
    padded with duplicates.
    """
    seen = set()
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        if rule.rule.startswith("/novnc"):
            # Proxies to a noVNC websocket that is not running.
            continue
        methods = rule.methods - {"HEAD", "OPTIONS"}
        if not methods:
            continue
        for method in sorted(methods):
            key = (rule.rule, method)
            if key in seen:
                continue
            seen.add(key)
            yield method, _fill(rule.rule)


@pytest.fixture(scope="module")
def smoke_app(tmp_path_factory):
    """The real app, wired against a throwaway SQLite file."""
    data_dir = tmp_path_factory.mktemp("smoke")
    import os

    os.environ["STUDYFLOW_DB_ENGINE"] = "sqlite"
    os.environ["STUDYFLOW_DATA_DIR"] = str(data_dir)
    os.environ.pop("DATABASE_URL", None)

    import database

    database.init_db()

    import server

    app = server.create_app()
    app.config.update(TESTING=True)
    yield app

    database.close_pool()


def test_every_route_answers_on_sqlite(smoke_app):
    """No route may raise a 5xx on SQLite.

    Asserting the floor, deliberately: this does not claim any route is
    correct. It claims none of them is unreachable behind a Postgres-only
    assumption, which is the failure mode that unit tests on the helpers
    cannot see.
    """
    client = smoke_app.test_client()
    failures = []

    for method, rule in _rules(smoke_app):
        try:
            response = client.open(rule, method=method, json={})
        except Exception as exc:  # noqa: BLE001 - the failure IS the finding
            failures.append(f"{method:6} {rule:52} RAISED "
                            f"{type(exc).__name__}: {str(exc)[:120]}")
            continue
        if response.status_code < 500:
            continue
        if f"{method} {rule}" in _ENVIRONMENT_DEPENDENT:
            continue
        failures.append(f"{method:6} {rule:52} {response.status_code}")

    assert not failures, (
        f"{len(failures)} route(s) failed on SQLite:\n  "
        + "\n  ".join(failures)
    )


def test_the_smoke_test_actually_covers_the_routes(smoke_app):
    """Guard the guard.

    A smoke test that silently matches nothing passes forever. This pins
    the floor on how much it must reach, so a blueprint that fails to
    register shows up as a failure here rather than as false confidence.
    """
    rules = list(_rules(smoke_app))
    assert len(rules) > 150, f"only {len(rules)} rules discovered"

    # The templates, not the substituted paths: _rules() replaces every
    # <param>, so /api/agenda/week/<week_id> has already become
    # /api/agenda/week/1 by the time rules() yields it.
    paths = {r.rule for r in smoke_app.url_map.iter_rules() if r.rule.startswith("/api")}
    assert len(rules) > 150, f"only {len(rules)} methods discovered"
    # One real representative per feature area. Spelled out rather than
    # pattern-matched so that a blueprint failing to import shows up as a
    # missing name here, not as a silently smaller rule set.
    for expected in ("/api/health", "/api/courses",
                     "/api/courses/<int:course_id>", "/api/agenda/current",
                     "/api/agenda/week/<week_id>", "/api/habits/<date>",
                     "/api/habits/columns", "/api/quiz/questions",
                     "/api/todos", "/api/todos/<int:todo_id>",
                     "/api/backup/mine/options", "/api/audio/tts"):
        assert expected in paths, f"{expected} not registered"

    # The exemption list is part of the contract: if a route it names stops
    # existing, the entry is stale and should be removed, not left to rot.
    for key in _ENVIRONMENT_DEPENDENT:
        method, rule = key.split(" ", 1)
        assert (method, rule) in set(rules), (
            f"{key} is exempted but no longer registered -- drop it from "
            f"_ENVIRONMENT_DEPENDENT"
        )
