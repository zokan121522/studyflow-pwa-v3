"""openzen_credentials.py — per-user OpenZen (opencode-acp) subscription.

Stored in `openzen_credentials`: the API key is Fernet-encrypted, while
`server_url` and `model` stay in clear (they are not secrets, they are
endpoints and model names you may want to read back).

Why this exists: the v2 "Configuración → ⛅ OpenZen" tab was a plain
"👤 Mi suscripción" form holding one personal key. The v3 equivalent lives
behind the same floating-button menu as the NotebookLM panel, so opening
OpenZen and authenticating is a two-click job from anywhere in the app.

Resolution order, mirroring backend/scorm_credentials.py:
    env vars  >  DB row  >  built-in default
so a deployment that prefers not to store anything can just export
OPENCODE_API_KEY / OPENCODE_SERVER_URL / OPENCODE_MODEL and never touch
this module.
"""

import logging
import os

from database import execute, fetchone
from openzen_sidecar import apply_sidecar_env
from secret_box import InvalidToken, decrypt_payload, encrypt_payload

logger = logging.getLogger(__name__)

# The local Windows sidecar (see openzen_sidecar) feeds OPENCODE_SERVER_*
# into the environment so the settings panel shows the same endpoint the
# provider actually talks to.
apply_sidecar_env()

# Defaults mirror backend/ai/providers/opencode_provider.py in v3. They
# live here too because the settings panel has to SHOW the defaults
# without importing the provider (which pulls httpx + the whole AI stack).
#
# v3 is a single-machine PWA: the opencode sidecar runs on the same host,
# so the default is 127.0.0.1 (the v2-era "http://opencode:54321" Docker
# hostname only resolves inside a compose network — on a bare Windows box
# it fails name resolution and OpenZen dies with a connection error).
# A deployment that talks to a remote sidecar (e.g. the Señor's Mac
# `opencode serve` on Tailscale) overrides via OPENCODE_SERVER_URL.
DEFAULT_SERVER_URL = "http://127.0.0.1:54321"
DEFAULT_MODEL = "big-pickle"


def _env(name, fallback=None):
    value = (os.environ.get(name) or "").strip()
    return value or fallback


def default_server_url() -> str:
    return _env("OPENCODE_SERVER_URL", DEFAULT_SERVER_URL)


def default_model() -> str:
    return _env("OPENCODE_MODEL", DEFAULT_MODEL)


def load_credentials(user_id: int) -> dict:
    """Return {'api_key', 'server_url', 'model'} for the user.

    Never raises: a corrupt ciphertext or a rotated SECRET_KEY degrades to
    "no key" plus a warning, so a YouTubeZen run fails with a clear
    "autentícate first" instead of a 500.
    """
    row = fetchone(
        """
        SELECT api_key_enc, server_url, model
        FROM openzen_credentials WHERE user_id = %s
        """,
        (user_id,),
    )

    api_key = _env("OPENCODE_API_KEY", None)
    if row:
        if api_key is None:
            try:
                data = decrypt_payload(row["api_key_enc"])
                api_key = (str(data.get("api_key") or "").strip() or None)
            except (InvalidToken, ValueError, TypeError) as exc:
                logger.warning(
                    "openzen key decrypt failed user=%s: %s", user_id, exc
                )
                api_key = None

    return {
        "api_key": api_key,
        "server_url": (row or {}).get("server_url") or default_server_url(),
        "model": (row or {}).get("model") or default_model(),
    }


def save_credentials(user_id: int, api_key: str, server_url: str, model: str) -> None:
    """Upsert the user's OpenZen credentials (API key encrypted at rest)."""
    token = encrypt_payload({"api_key": api_key})
    execute(
        """
        INSERT INTO openzen_credentials
            (user_id, api_key_enc, server_url, model, updated_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (user_id) DO UPDATE
            SET api_key_enc = EXCLUDED.api_key_enc,
                server_url   = EXCLUDED.server_url,
                model        = EXCLUDED.model,
                updated_at   = NOW()
        """,
        (user_id, token, server_url or None, model or None),
    )


def delete_credentials(user_id: int) -> None:
    """Remove the stored row. A subsequent load falls back to env/defaults."""
    execute("DELETE FROM openzen_credentials WHERE user_id = %s", (user_id,))
