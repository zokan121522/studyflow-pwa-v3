"""Guards for the argument-order bug that broke every openzen retry.

`@token_required` calls the view as `f(LOCAL_USER_ID, *args, **kwargs)` — the
user id always lands in the FIRST positional slot. Any route with a variable in
its URL must therefore declare `current_user_id` first and its path parameters
after, or Flask's `view_args` collide with the decorator's positional and the
request dies before the body runs.

`openzen_retry_chunk` had them the other way round and every call raised:

    TypeError: openzen_retry_chunk() got multiple values for argument 'task_id'

which the blueprint turned into a 500. The "reiniciar sección" button in
OpenZen therefore failed 100% of the time — the kind of bug that survives
because nothing in the suite dispatches these routes: they all need a
database, and the suite runs without one.

So these tests do not need the database either. They assert the *signature*
of every view in routes/ai.py, which is the invariant that broke.
"""

import inspect
import sys
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from routes import ai as ai_routes  # noqa: E402


# The decorator passes LOCAL_USER_ID positionally, so it has to be first.
_FIRST = "current_user_id"

# Path parameters Flask fills from view_args. They must come after.
_PATH_PARAMS = ("task_id", "chunk_num", "block_id", "topic_id", "profile_id")


def _registered_views():
    """Yield (endpoint_name, view_fn) for every route the blueprint declares.

    The blueprint is registered on a throwaway app and the views are read
    back off `url_map`, which is the same mapping Flask dispatches through.
    `dir(ai_routes)` would not do: it also exposes the imported helpers
    (`jsonify`, `query`, and the task factory functions) that are not views.
    Registering a Blueprint touches no database.
    """
    app = Flask(__name__)
    app.register_blueprint(ai_routes.bp)
    for rule in app.url_map.iter_rules():
        yield rule.endpoint, app.view_functions[rule.endpoint]


def _params(fn) -> list[str]:
    return list(inspect.signature(fn).parameters)


@pytest.mark.parametrize("name,fn", list(_registered_views()),
                         ids=lambda v: v if isinstance(v, str) else "")
def test_current_user_id_is_the_first_parameter(name, fn):
    """current_user_id first — @token_required passes it positionally."""
    params = _params(fn)
    if not params:
        pytest.skip(f"{name} takes only *args/**kwargs")
    if _FIRST not in params:
        pytest.skip(f"{name} is not a token_required view")
    assert params[0] == _FIRST, (
        f"{name}() declares {params[0]!r} first but @token_required calls it as "
        f"f({_FIRST}, ...). If {params[0]!r} is also a path parameter, Flask "
        f"passes it a second time and the request 500s with "
        f"'got multiple values for argument'."
    )


def test_openzen_retry_chunk_has_the_retry_order():
    """The route that was actually broken, pinned by name."""
    params = _params(ai_routes.openzen_retry_chunk)
    assert params == [_FIRST, "task_id", "chunk_num"], (
        f"expected [{_FIRST!r}, 'task_id', 'chunk_num'], got {params}"
    )


def test_every_path_parameter_sits_after_current_user_id():
    """No view may declare a path parameter before the user id."""
    offenders = []
    for name, fn in _registered_views():
        params = _params(fn)
        if _FIRST not in params:
            continue
        at = params.index(_FIRST)
        early = [p for p in params[:at] if p in _PATH_PARAMS]
        if early:
            offenders.append(f"{name}{tuple(params)}")
    assert not offenders, (
        "path parameters declared before current_user_id (they would collide "
        "with the decorator's positional argument): " + "; ".join(offenders)
    )


def test_the_check_would_have_caught_the_broken_signature():
    """Guard the guard.

    A signature test that passes on the bug it exists to catch is worse than
    no test: it buys confidence and delivers none. This feeds the test the
    original, broken order and requires it to fail.
    """
    def roto(task_id: str, chunk_num: int, current_user_id: int):
        """The signature as it was before the fix."""

    params = _params(roto)
    assert _FIRST in params
    assert params[0] != _FIRST, "the broken order must be detected"
    early = [p for p in params[:params.index(_FIRST)] if p in _PATH_PARAMS]
    assert early == ["task_id", "chunk_num"], (
        "the broken signature put both path parameters first; if this stops "
        "being true the test no longer models the bug it guards"
    )