"""secret_box.py — symmetric encryption for small secrets at rest.

S7b-B. The SCORM credentials entered in the PWA settings panel are stored
encrypted in PostgreSQL. We reuse the v2 approach (Fernet) but derive the
key from the app SECRET_KEY instead of carrying a second env var, so a
single SECRET_KEY protects both sessions and stored secrets.

Key derivation: base64(sha256(secret)) — Fernet requires a 32-byte
urlsafe-base64 key, and a raw env string never is one.
"""

import base64
import hashlib
import json
import logging

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    """Build a Fernet cipher from the app SECRET_KEY."""
    secret = current_app.config.get("SECRET_KEY") or "dev-secret-change-in-production"
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_payload(payload: dict) -> str:
    """Encrypt a dict to a URL-safe token."""
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _fernet().encrypt(raw).decode("ascii")


def decrypt_payload(token: str) -> dict:
    """Decrypt a token produced by encrypt_payload.

    Raises InvalidToken when the token is corrupt or was encrypted with a
    different SECRET_KEY — callers decide whether that is fatal.
    """
    raw = _fernet().decrypt(token.encode("ascii"))
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


__all__ = ["encrypt_payload", "decrypt_payload", "InvalidToken"]
