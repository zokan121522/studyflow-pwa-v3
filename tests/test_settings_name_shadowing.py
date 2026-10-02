"""Guard against name shadowing in routes/settings.py.

There are two different `save_credentials` in that module's namespace:

- `openzen_credentials.save_credentials(user_id, api_key, server_url, model)`
- `scorm_credentials.save_credentials(user_id, username, password)` — imported
  under the alias `save_scorm_credentials`

When the POST handler was itself named `save_scorm_credentials`, defining it
rebound the alias to the handler. The handler then called *itself* with four
arguments, and every Moodle credential save died with:

    save_scorm_credentials() takes 1 positional argument but 4 were given

surfacing as an opaque `HTTP 500` with no traceback in the access log. Nothing
in the suite covered this endpoint, so it shipped broken.

The bug is purely a name-resolution problem, so it can be pinned without a
database, a token, or an HTTP call.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "backend" / "routes" / "settings.py"


def _module_namespace() -> dict:
    """Resolve the module's top-level names without importing Flask."""
    tree = ast.parse(SETTINGS.read_text())
    ns: dict = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in {
            "scorm_credentials",
            "openzen_credentials",
        }:
            for alias in node.names:
                ns[alias.asname or alias.name] = (node.module, alias.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        elif isinstance(node, ast.FunctionDef):
            # A module-level def rebinds whatever name it uses. This is the
            # exact mechanism that broke the import alias.
            ns[node.name] = ("routes.settings", node.name)
    return ns


def test_scorm_alias_still_points_at_the_library():
    """The alias must survive: no def may have taken over its name."""
    ns = _module_namespace()
    assert ns.get("save_scorm_credentials") == (
        "scorm_credentials",
        "save_credentials",
    ), (
        "save_scorm_credentials no longer resolves to scorm_credentials."
        "save_credentials — a module-level def has shadowed the import, so "
        "the POST handler calls itself instead of the library"
    )


def _imported_names() -> set:
    """Names this module imports from the two credentials libraries."""
    tree = ast.parse(SETTINGS.read_text())
    out = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in {
            "scorm_credentials",
            "openzen_credentials",
        }:
            out.update(a.asname or a.name for a in node.names)
    return out


def _top_level_defs() -> set:
    tree = ast.parse(SETTINGS.read_text())
    return {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}


def test_no_top_level_def_reuses_an_imported_name():
    """No module-level def may collide with a name imported at module level.

    A def rebinds the name for the whole module, so the import it shadows is
    silently replaced — the call site keeps compiling and only fails at runtime,
    which is exactly how the Moodle 500 shipped.
    """
    collisions = _imported_names() & _top_level_defs()
    assert not collisions, (
        f"module-level def rebinds an import: {sorted(collisions)} — "
        "the call site will resolve to the def, not the library"
    )


def test_route_handlers_use_distinct_names():
    """The POST handler must not be named like the library function it calls."""
    tree = ast.parse(SETTINGS.read_text())
    handlers = [
        n.name
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "route"
            for d in n.decorator_list
        )
    ]
    assert handlers, (
        "no route handlers found — the AST walk broke. Decorators here are "
        "@bp.route(...), i.e. an Attribute, not a bare Name"
    )
    assert "save_scorm_credentials" not in handlers, (
        "a route handler is named save_scorm_credentials; it shadows the "
        "imported alias of the same name and every save 500s"
    )


def test_save_call_site_uses_the_alias_with_three_args():
    """The handler must call the library with (user_id, username, password)."""
    tree = ast.parse(SETTINGS.read_text())
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "post_scorm_credentials"
    )
    calls = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "save_scorm_credentials"
    ]
    assert len(calls) == 1, "expected exactly one call to the library function"
    assert len(calls[0].args) == 3, (
        f"expected 3 args, got {len(calls[0].args)} — the library signature is "
        "save_credentials(user_id, username, password)"
    )