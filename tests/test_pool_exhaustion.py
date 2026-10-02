"""Regressions for the connection-pool exhaustion bug.

`scheduler._run_once` borrowed a connection from the pool with
`getconn()` and then released it with `conn.close()`. Closing does not
return a connection to `ThreadedConnectionPool` — the pool counts it as
checked out until `putconn()` does. Every import therefore consumed one of
the `maxconn` slots permanently, and after ten of them `getconn()` raised
`PoolError: connection pool exhausted` for the whole app while Postgres sat
idle with nothing on it.

The naive fix (swap `close()` for `putconn()` and stop there) is worse than
the original bug, and fails silently: the advisory lock that serialises the
import is session-scoped, so a reused connection would keep holding it and
every future tick would be skipped. The calendar would stop importing and the
status panel would keep showing the last good run. So these tests pin the
*pair*: unlock, then return.
"""

import ast
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
SCHEDULER = BACKEND / "calendar_import" / "scheduler.py"
DATABASE = BACKEND / "database.py"

SOURCE = SCHEDULER.read_text(encoding="utf-8")
DB_SOURCE = DATABASE.read_text(encoding="utf-8")


def _run_once_node() -> ast.FunctionDef:
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_once":
            return node
    raise AssertionError("_run_once not found in scheduler.py")


def _run_once_source() -> str:
    return ast.get_source_segment(SOURCE, _run_once_node()) or ""


def _calls_in(node) -> set[str]:
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            names.add(f.attr if isinstance(f, ast.Attribute) else f.id)
    return names


class TestConnectionGoesBackToThePool:
    def test_run_once_borrows_through_the_injected_factory(self):
        node = _run_once_node()
        assert "conn_factory" in _calls_in(node), (
            "_run_once must borrow through the injected conn_factory, not by "
            "reaching for the pool itself"
        )
        # And the factory is the pool in every real call site: server.py and
        # routes.py both pass db.get_connection.
        for path in ("server.py", "calendar_import/routes.py"):
            src = (BACKEND / path).read_text(encoding="utf-8")
            assert "get_connection" in src, f"{path} must supply a real pool factory"

    def test_run_once_returns_the_connection(self):
        assert "put_connection" in _calls_in(_run_once_node()), (
            "the connection must be returned with put_connection(); close() "
            "consumes a pool slot permanently and exhausts the pool"
        )

    def test_run_once_does_not_close_the_connection(self):
        body = _run_once_source()
        # A close() on the pooled handle is the original bug. psycopg2's
        # putconn(close=True) is the only legitimate way to close one.
        assert not re.search(r"(?<!putconn\()\bconn\.close\(\)", body), (
            "conn.close() consumes a pool slot; use put_connection() instead"
        )


class TestAdvisoryLockIsReleasedBeforeTheReturn:
    """The part that fails silently, so it is pinned hardest."""

    def test_run_once_unlocks_explicitly(self):
        assert "pg_advisory_unlock" in _run_once_source(), (
            "the advisory lock is session-scoped: reusing the connection "
            "without unlocking leaves it held forever and every future import "
            "is skipped in silence"
        )

    def test_unlock_comes_before_the_return(self):
        body = _run_once_source()
        unlock = body.index("pg_advisory_unlock")
        put_back = body.index("put_connection")
        assert unlock < put_back, (
            "unlock must run BEFORE the connection goes back to the pool"
        )

    def test_unlock_is_itself_guarded(self):
        # If the unlock raises, the connection still has to be returned: a
        # leaked slot is exactly the failure being fixed here.
        body = _run_once_source()
        try_i = body.index("try:", body.index("finally:"))
        assert "except" in body[try_i : body.index("put_connection")], (
            "the unlock needs its own except so cleanup cannot be skipped"
        )
        assert "finally:" in body[body.index("except") : body.index("put_connection")], (
            "put_connection() must sit in a finally of its own"
        )

    def test_transaction_is_cleared_before_unlocking(self):
        assert re.search(r"conn\.rollback\(\)", _run_once_source()), (
            "an aborted transaction makes the unlock fail with "
            "InFailedSqlTransaction, which would leave the lock held"
        )


class TestPoolHasHeadroom:
    """maxconn has to cover a worker's threads plus the scheduler thread.

    Note what this does NOT claim: that a bigger number is the fix. The fix is
    returning the connection. maxconn is margin, and no test can prove margin
    is "enough" — only that it is not smaller than the concurrency it serves.
    """

    def _concurrent_demand(self) -> int:
        dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
        threads = re.search(r'--threads"?\s*,?\s*"?(\d+)', dockerfile)
        assert threads, "could not read --threads from backend/Dockerfile"
        # Request threads, plus the calendar scheduler daemon thread, which
        # borrows a connection on its own schedule and is not counted in
        # --threads.
        return int(threads.group(1)) + 1

    def test_maxconn_covers_a_workers_threads_and_the_scheduler(self):
        match = re.search(r"maxconn=(\d+)", DB_SOURCE)
        assert match, "maxconn not found in database.py"
        demand = self._concurrent_demand()
        assert int(match.group(1)) > demand, (
            f"a per-process pool can be asked for up to {demand} connections "
            f"at once (gunicorn threads + the scheduler thread); maxconn must "
            f"exceed that or requests queue behind each other"
        )

    def test_scheduler_and_its_caller_stay_paired(self):
        """sync_now calls _run_once, so the fix must be in _run_once itself."""
        routes = (BACKEND / "calendar_import" / "routes.py").read_text(encoding="utf-8")
        assert "scheduler._run_once" in routes, (
            "sync_now reuses _run_once; if that changes, re-check the pairing"
        )
