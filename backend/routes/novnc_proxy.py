"""
noVNC Proxy — serve noVNC static files and bridge WebSocket ↔ RFB.

Why this exists:
  The user accesses studyflow-hub exclusively through a Cloudflare tunnel that
  exposes only port 8080 (HTTPS). The noVNC login flow previously required the
  browser to connect directly to port 6080 (HTTP), which caused mixed-content
  blocks and DNS mismatches.

  This module serves noVNC from the **same origin** (port 8080):
    GET  /novnc/<path>  → static files from /opt/novnc/
    WS   /novnc/ws      → bridge: browser WebSocket ↔ raw RFB to x11vnc:5900
    WS   /novnc/echo    → diagnostic echo WebSocket

Architecture:
  The bridge uses **two greenlets** (one per direction) instead of polling
  with ``settimeout`` — gevent-websocket's ``WebSocket`` does not have that
  method (it belongs to the client-side ``websocket-client`` library).
"""

import logging
from flask import Blueprint, abort, send_from_directory

import gevent
import gevent.socket as gsocket

log = logging.getLogger("novnc_proxy")
log.setLevel(logging.DEBUG)

bp = Blueprint("novnc_proxy", __name__)

NOVNC_DIR = "/opt/novnc"
RFB_HOST = "localhost"
RFB_PORT = 5900


# ── Static file serving ────────────────────────────────────────────────

@bp.route("/novnc/<path:filename>")
def serve_novnc(filename):
    return send_from_directory(NOVNC_DIR, filename)


@bp.route("/novnc/")
def novnc_root():
    return "", 302, {"Location": "/novnc/vnc.html"}


# ── Diagnostic endpoint ────────────────────────────────────────────────

@bp.route("/novnc/ping")
def novnc_ping():
    """Simple GET — confirms the Blueprint is registered."""
    return "pong"


# ── WebSocket bridge (two-greenlet relay) ──────────────────────────────

@bp.route("/novnc/ws")
def vnc_websocket():
    from geventwebsocket import WebSocketError
    from flask import request as flask_req

    ws = _get_websocket()
    if ws is None:
        log.warning("WS upgrade failed — no wsgi.websocket in environ")
        abort(400, "This endpoint requires a WebSocket upgrade connection")

    log.info("Browser WS connected from %s", flask_req.remote_addr)

    # Connect to x11vnc
    try:
        rfb = gsocket.create_connection((RFB_HOST, RFB_PORT), timeout=5)
        log.info("Connected to x11vnc:%d", RFB_PORT)
    except Exception as exc:
        log.error("Cannot reach x11vnc:%d: %s", RFB_PORT, exc)
        abort(502, f"Cannot reach x11vnc: {exc}")

    def _browser_to_rfb():
        """Relay: browser WebSocket → x11vnc TCP."""
        try:
            while True:
                msg = ws.receive()
                if msg is None:
                    log.debug("Browser WS closed")
                    return
                if isinstance(msg, str):
                    msg = msg.encode("utf-8")
                rfb.sendall(msg)
        except WebSocketError:
            pass

    def _rfb_to_browser():
        """Relay: x11vnc TCP → browser WebSocket."""
        try:
            while True:
                chunk = rfb.recv(65536)
                if not chunk:
                    log.debug("RFB socket closed")
                    return
                ws.send(chunk)
        except OSError:
            log.debug("RFB socket error")

    try:
        glets = [gevent.spawn(_browser_to_rfb), gevent.spawn(_rfb_to_browser)]
        gevent.joinall(glets)
    except Exception as exc:
        log.error("Bridge error: %s", exc)
    finally:
        _close_sock(rfb)
        log.info("WS bridge disconnected")
    return ""


# ── Echo diagnostic WebSocket ──────────────────────────────────────────

@bp.route("/novnc/echo")
def vnc_echo():
    """Echo WebSocket — used to verify gevent-websocket works."""
    from geventwebsocket import WebSocketError

    ws = _get_websocket()
    if ws is None:
        abort(400, "WS upgrade required")

    log.info("Echo WS connected")
    try:
        while True:
            msg = ws.receive()
            if msg is None:
                break
            ws.send(msg)
    except WebSocketError:
        pass
    log.info("Echo WS disconnected")
    return ""


# ── Helpers ────────────────────────────────────────────────────────────

def _get_websocket():
    try:
        from flask import request
        return request.environ.get("wsgi.websocket")
    except (RuntimeError, ImportError):
        return None


def _close_sock(sock):
    if sock is None:
        return
    try:
        sock.close()
    except Exception:
        pass
