"""
Custom gunicorn GeventWorker with WebSocket support.

Built on gunicorn's ``GeventPyWSGIWorker`` (pure-Python ``WSGIServer``) and
``gevent-websocket``'s ``WebSocketHandler`` so that WebSocket upgrade requests
populate ``request.environ["wsgi.websocket"]`` inside Flask.

Usage:
    --worker-class backend.gevent_ws_worker:GeventWebSocketWorker

Why a custom worker instead of the built-in
``geventwebsocket.gunicorn.workers.GeventWebSocketWorker``?
    Same inheritance chain — we keep our own class so we can apply fixes
    (such as stripping ``Upgrade`` / ``Connection`` headers before the
    second WSGI call that ``run_websocket`` makes).
"""

from geventwebsocket.handler import WebSocketHandler
from gunicorn.workers.ggevent import GeventPyWSGIWorker


class CleanWebSocketHandler(WebSocketHandler):
    """Extends WebSocketHandler to fix Werkzeug 400 on the second WSGI call.

    ``WebSocketHandler.run_websocket`` calls ``self.application(environ, …)``
    *a second time* after the upgrade is complete so that the Flask route
    handler can read ``request.environ["wsgi.websocket"]``.  If we leave
    the ``Upgrade`` / ``Connection`` headers in the environ, Werkzeug tries
    to process another WebSocket upgrade → 400 Bad Request.

    This override strips both headers before delegating to the base class.
    """

    def run_websocket(self):
        self.environ.pop("HTTP_UPGRADE", None)
        self.environ.pop("HTTP_CONNECTION", None)
        super().run_websocket()


class GeventWebSocketWorker(GeventPyWSGIWorker):
    """Gevent worker using PyWSGIServer + CleanWebSocketHandler."""

    wsgi_handler = CleanWebSocketHandler
