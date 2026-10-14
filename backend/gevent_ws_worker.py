"""
Gunicorn worker entrypoint with WebSocket support.

The container command uses ``--worker-class gevent_ws_worker.GeventWebSocketWorker``
(see backend/Dockerfile). It subclasses the official gevent-websocket gunicorn
worker so the class name keeps resolving:

    geventwebsocket.gunicorn.workers.GeventWebSocketWorker
        -> gunicorn.workers.ggevent.GeventPyWSGIWorker
        -> geventwebsocket.handler.WebSocketHandler

Why we do NOT strip the RFC 6455 upgrade headers:
    gevent-websocket performs the ``101 Switching Protocols`` upgrade and then
    calls the WSGI application with the ORIGINAL environ, which still carries
    ``HTTP_CONNECTION: Upgrade`` and ``HTTP_UPGRADE: websocket``.

    An earlier revision popped those ``HTTP_*`` keys here (and in
    launcher/serve.py) on the theory that Werkzeug >= 2.3 answered such a
    request with ``400 Bad Request`` before routing. That is wrong once the
    noVNC routes are declared ``websocket=True`` (routes/novnc_proxy.py):
    Werkzeug derives the URL scheme from those very headers
    (``werkzeug/routing/map.py::bind_to_environ`` sets scheme ``ws`` when
    ``HTTP_CONNECTION`` contains ``upgrade`` and ``HTTP_UPGRADE == websocket``;
    then ``MapAdapter.websocket = scheme in {"ws", "wss"}``). Stripping them
    forced scheme ``http`` → ``websocket=False`` → the ``websocket=True`` rules
    stopped matching → ``WebsocketMismatch`` (400) and the views never ran, so
    ``/novnc/ws`` and ``/novnc/echo`` upgraded (101) and closed immediately
    (browser close code 1005, "no close frame") without relaying a single RFB
    frame. Keeping the headers lets Werkzeug route to the live socket in
    ``environ["wsgi.websocket"]``.

Usage:
    --worker-class gevent_ws_worker.GeventWebSocketWorker
"""

from geventwebsocket.gunicorn.workers import GeventWebSocketWorker as _Base


class GeventWebSocketWorker(_Base):
    """Gunicorn worker with gevent-websocket's WebSocket handler (no stripping)."""


__all__ = ["GeventWebSocketWorker"]
