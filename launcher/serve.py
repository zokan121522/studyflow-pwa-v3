# launcher/serve.py
"""The child process behind the StudyFlow launcher.

Kept separate from launch.py so that nothing in the serving path can ever
re-enter the single-instance, port-picking or browser-opening logic. This
module starts the app and then blocks; that is the whole job.

Two things here are deliberately different from ``backend/server.py``'s
``__main__``, which is still there for the hosted v3 deployment:

* **Binds 127.0.0.1, not 0.0.0.0.** The hosted app sits behind a tunnel and
  needs to be reachable from the network; a local install must not be. On a
  laptop joined to a café Wi-Fi, 0.0.0.0 means every device in the room can
  read the user's agenda and write to their database.
* **debug=False, no reloader.** The reloader spawns a child process, which
  would fight the launcher for the pidfile and leave an orphan behind on
  every relaunch.
"""

from __future__ import annotations

import os
import sys


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# NOTE: do NOT strip the RFC 6455 upgrade headers (HTTP_CONNECTION /
# HTTP_UPGRADE) before handing the environ to the WSGI app.
#
# gevent-websocket performs the ``101 Switching Protocols`` upgrade and then
# calls the WSGI application with the ORIGINAL environ. A previous revision
# popped ``HTTP_CONNECTION``/``HTTP_UPGRADE`` here on the theory that Werkzeug
# >= 2.3 answered such a request with ``400 Bad Request`` before routing. That
# theory is wrong *now that the noVNC routes are declared* ``websocket=True``
# (routes/novnc_proxy.py): Werkzeug derives the URL scheme from those very
# headers (``werkzeug/routing/map.py::bind_to_environ`` → scheme ``ws`` when
# ``HTTP_CONNECTION`` contains ``upgrade`` and ``HTTP_UPGRADE == websocket``,
# then ``MapAdapter.websocket = scheme in {"ws", "wss"}``). Stripping them made
# Werkzeug compute scheme ``http`` → ``websocket=False`` → the ``websocket=True``
# rules no longer matched → ``WebsocketMismatch`` (400) and the views never ran
# (the browser saw the socket close with code 1005 and noVNC stayed black).
# Keeping the headers lets Werkzeug route to the live socket in
# ``environ["wsgi.websocket"]``.


def _force_utf8_stdio() -> None:
    """stdout/stderr as UTF-8 whatever the launcher handed us.

    The Windows launcher opens the child's stdout on a file in the locale
    encoding (cp1252), where '✅' does not exist: a print of a success line
    raised UnicodeEncodeError AFTER a restore had already committed its
    rows and files, and the client saw a 500 for work that had succeeded.
    Fixed here, once, at the door, for every print in the process;
    backup_user_restore._log() is the per-line second line of defence.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass  # StringIO or a stream that refuses: _log() covers the rest


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    port = int(os.environ.get("STUDYFLOW_PORT", "8477"))

    root = _repo_root()
    # backend/ first so server.py's flat imports (import engine, not
    # import backend.engine) resolve. Same ordering as scripts/dev-server.sh.
    sys.path.insert(0, os.path.join(root, "backend"))
    sys.path.insert(0, root)

    # WebSocket support for the noVNC login bridge (/novnc/ws, see
    # routes/novnc_proxy.py). Werkzeug's dev server (app.run) cannot perform
    # the RFC 6455 upgrade — it answers 400 — so serve through gevent's
    # WSGIServer with gevent-websocket's handler. gevent's monkey-patch MUST
    # run before the app (and its DB/socket libs) is imported so sockets and
    # threads become greenlets. If gevent is missing we fall back to the
    # plain dev server: the app still boots, only the WS bridge degrades.
    gevent_server = _build_gevent_server(port)

    import server  # noqa: E402  (import after sys.path is set up)

    app = server.create_app()

    if gevent_server is not None:
        WSGIServer, WebSocketHandler = gevent_server
        WSGIServer(("127.0.0.1", port), app,
                   handler_class=WebSocketHandler).serve_forever()
        return 0

    # threaded=True is safe now: database.get_connection() hands out one
    # connection per thread. It was not before, and the shared handle
    # deadlocked under load. See the commit on backend/database.py.
    app.run(
        host="127.0.0.1",
        port=port,
        debug=False,
        threaded=True,
        use_reloader=False,
    )
    return 0


def _build_gevent_server(port: int):
    """Monkey-patch gevent and return (WSGIServer, WebSocketHandler).

    Returns ``None`` when gevent / gevent-websocket are unavailable, so the
    launcher can fall back to Flask's dev server. Must be called before
    importing the application on the gevent path.
    """
    try:
        from gevent import monkey

        monkey.patch_all()
        # Prefer GeventWebSocketServer for proper WebSocket upgrade handling
        try:
            from geventwebsocket.server import GeventWebSocketServer
            from geventwebsocket.handler import WebSocketHandler

            return GeventWebSocketServer, WebSocketHandler
        except (ImportError, AttributeError):
            try:
                from geventwebsocket.server import WebSocketServer as GeventWebSocketServer
                from geventwebsocket.handler import WebSocketHandler

                return GeventWebSocketServer, WebSocketHandler
            except (ImportError, AttributeError):
                from gevent.pywsgi import WSGIServer
                from geventwebsocket.handler import WebSocketHandler

                return WSGIServer, WebSocketHandler
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())