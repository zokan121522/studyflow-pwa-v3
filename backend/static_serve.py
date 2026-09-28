# backend/static_serve.py
"""Serve the PWA frontend from the same origin as the API.

Single-origin = the Cloudflare tunnel URL works on any device with zero
CORS/base-url configuration. API blueprints keep priority: Werkzeug sorts
routes by specificity, so the catch-all below only answers what the API
did not match.
"""

import mimetypes
import os

from flask import send_from_directory
from werkzeug.exceptions import NotFound
from werkzeug.security import safe_join

_MIME = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".wasm": "application/wasm",
}

FRONTEND_DIR = os.environ.get(
    "FRONTEND_DIR",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend")),
)


def _register_extra_mimes() -> None:
    for ext, mime in _MIME.items():
        mimetypes.add_type(mime, ext)


def _index() -> str:
    return send_from_directory(FRONTEND_DIR, "index.html")


def _asset(path: str):
    full = safe_join(FRONTEND_DIR, path)
    if full is None or not os.path.isfile(full):
        raise NotFound()
    return send_from_directory(FRONTEND_DIR, path)


def init_static(app) -> None:
    """Mount the SPA: root, service worker scope root, hashed assets."""
    if not os.path.isdir(FRONTEND_DIR):
        return
    _register_extra_mimes()

    @app.get("/")
    def _root():
        return _index()

    @app.get("/<path:subpath>")
    def _files(subpath: str):
        if subpath.startswith("api/"):
            raise NotFound()
        try:
            return _asset(subpath)
        except NotFound:
            return _index()
