"""scorm_credentials.py — per-user Moodle login for the SCORM scraper (S7b-B).

Stored in `scorm_credentials` (username in clear, password Fernet-encrypted).
Resolution order used by the importer: env vars > DB row, mirroring v2's
`_get_credentials` priority (explicit args still win, see
backend/scraping/runner.py).
"""

import logging
import os

from database import execute, fetchone
from secret_box import InvalidToken, decrypt_payload, encrypt_payload

logger = logging.getLogger(__name__)


def load_credentials(user_id: int):
    """Return (username, password) for the user, or (None, None).

    Never raises: a corrupt ciphertext or a rotated SECRET_KEY degrades to
    "no credentials" plus a warning, so an import degrades to the login-page
    PDF instead of a 500.
    """
    env_user = (os.environ.get("SCRAPING_USERNAME") or "").strip()
    env_pass = os.environ.get("SCRAPING_PASSWORD") or ""
    if env_user and env_pass:
        return env_user, env_pass

    row = fetchone(
        "SELECT username, password_enc FROM scorm_credentials WHERE user_id = %s",
        (user_id,),
    )
    if not row:
        return (env_user or None), (env_pass or None)

    try:
        data = decrypt_payload(row["password_enc"])
    except (InvalidToken, ValueError, TypeError) as exc:
        logger.warning("scorm creds decrypt failed user=%s: %s", user_id, exc)
        return None, None

    return (
        (row["username"] or "").strip() or None,
        str(data.get("password") or "") or None,
    )


def save_credentials(user_id: int, username: str, password: str) -> None:
    """Upsert the user's SCORM credentials (password encrypted at rest)."""
    token = encrypt_payload({"password": password})
    execute(
        """
        INSERT INTO scorm_credentials (user_id, username, password_enc, updated_at)
        VALUES (%s, %s, %s, NOW())
        ON CONFLICT (user_id) DO UPDATE
            SET username = EXCLUDED.username,
                password_enc = EXCLUDED.password_enc,
                updated_at = NOW()
        """,
        (user_id, username, token),
    )
