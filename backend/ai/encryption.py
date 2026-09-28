"""
API key encryption — Fernet-based symmetric encryption for per-user AI config.

Usage:
    encrypted = encrypt_api_keys({"openai": ["sk-..."]})
    decrypted = decrypt_api_keys(encrypted)

v3 change from v2: v2 required a dedicated `CONFIG_ENCRYPTION_KEY` env var;
v3 reuses `secret_box` (SECRET_KEY-derived Fernet, pattern S7b-B already
used by scorm_credentials) so a single secret protects both sessions and
stored secrets. API is identical to v2 (encrypt_api_keys/decrypt_api_keys).
"""

import json

from secret_box import decrypt_payload, encrypt_payload


def encrypt_api_keys(api_keys: dict) -> str:
    """Encrypt an API keys dict to a Fernet token string.

    Args:
        api_keys: Dict like {"openai": ["sk-...", "sk-..."], "anthropic": ["sk-ant-..."]}

    Returns:
        Fernet-encrypted token as a string (URL-safe base64), or "" for empty.
    """
    if not api_keys:
        return ""
    return encrypt_payload(api_keys)


def decrypt_api_keys(encrypted: str) -> dict:
    """Decrypt a Fernet token string back to an API keys dict.

    Args:
        encrypted: Fernet token string (URL-safe base64).

    Returns:
        Decrypted dict like {"openai": ["sk-..."]}, or empty dict if the
        string is empty or the token cannot be decrypted (e.g. SECRET_KEY
        changed — treated as "no keys" rather than a hard failure).
    """
    if not encrypted or not encrypted.strip():
        return {}
    try:
        data = decrypt_payload(encrypted)
        return data if isinstance(data, dict) else json.loads(json.dumps(data))
    except Exception:
        return {}