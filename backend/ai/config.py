"""
AI configuration persistence — vault path, provider, and personality.

Manages the AI config JSON file persisted to disk so that changes
made via the UI survive restarts.

v3 changes from v2:
    - Persisted file location: `~/.studyflow-app/ai_config.json` (local
      macOS) instead of `/data/ai_config.json` (container).
    - DB access uses v3 flat imports (`from database import ...`).
    - Encryption delegates to `secret_box` (SECRET_KEY) via ai.encryption.

Exports:
    Config constants:   VAULT_PATH_DEFAULT,
                        DEFAULT_PROVIDER, DEFAULT_PERSONALITY
    Config persistence: AI_CONFIG_FILE, _load_ai_config, _save_ai_config,
                        _get_ai_config, _invalidate_ai_config_cache,
                        _get_vault_path, _get_priority_context, _set_priority_context
    Provider config:    _get_active_provider, _set_active_provider,
                        _get_personality, _set_personality
"""

import json
import os
from pathlib import Path

# Extended home-relative default so the file survives in the app data dir.
_APP_DATA_DIR = os.path.join(
    os.path.expanduser("~"), ".studyflow-app"
)


# ─── Config ────────────────────────────────────────────────────────

# Default vault path — the Obsidian / Markdown vault used for the agent brain.
VAULT_PATH_DEFAULT = os.environ.get(
    "VAULT_PATH",
    os.path.join(_APP_DATA_DIR, "roadmap", "Obsidian", "02-StudyFlow", "Projects"),
)

# ─── Persisted AI config ──────────────────────────────────────────

AI_CONFIG_FILE = os.environ.get(
    "AI_CONFIG_FILE", os.path.join(_APP_DATA_DIR, "ai_config.json")
)
_ai_config_cache: dict | None = None

DEFAULT_PROVIDER = os.environ.get("AI_PROVIDER", "notebooklm")
DEFAULT_PERSONALITY = os.environ.get("AI_PERSONALITY", "alvaro")


def _load_ai_config() -> dict:
    """Load AI config from JSON file (persisted across restarts).

    Falls back to VAULT_PATH env var or defaults.
    The JSON file, once saved from the UI, takes precedence.
    """
    config = {
        "vault_path": VAULT_PATH_DEFAULT,
        "priority_context": "",
        "voice_mode": False,
        "active_provider": DEFAULT_PROVIDER,
        "personality": DEFAULT_PERSONALITY,
        "api_keys": {},       # e.g. {"anthropic": ["sk-ant-...", ...]}
        "key_index": 0,       # current active key index per provider
    }
    try:
        if os.path.isfile(AI_CONFIG_FILE):
            with open(AI_CONFIG_FILE, "r") as f:
                file_config = json.load(f)
                config.update(file_config)
    except Exception:
        pass
    return config


def _save_ai_config(config: dict) -> None:
    """Save AI config to JSON file (persisted across restarts)."""
    dirname = os.path.dirname(AI_CONFIG_FILE)
    if dirname and not os.path.isdir(dirname):
        Path(dirname).mkdir(parents=True, exist_ok=True)
    with open(AI_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def _get_ai_config() -> dict:
    """Get cached AI config, loading from file on first call."""
    global _ai_config_cache
    if _ai_config_cache is None:
        _ai_config_cache = _load_ai_config()
    return _ai_config_cache


def _invalidate_ai_config_cache() -> None:
    """Force reload of config on next access."""
    global _ai_config_cache
    _ai_config_cache = None


def _get_vault_path() -> str:
    """Get the current vault path from persisted config."""
    return _get_ai_config().get("vault_path", VAULT_PATH_DEFAULT)


def _get_priority_context() -> str:
    """Get the current priority context from persisted config."""
    return _get_ai_config().get("priority_context", "")


def _get_voice_mode() -> bool:
    """Get the current voice mode setting from persisted config."""
    return bool(_get_ai_config().get("voice_mode", False))


def _set_priority_context(text: str) -> None:
    """Set the priority context and persist to config file."""
    config = _get_ai_config()
    config["priority_context"] = text
    _save_ai_config(config)
    _invalidate_ai_config_cache()


# ─── Provider & personality ────────────────────────────────────────


def _get_active_provider() -> str:
    """Get the active AI provider from persisted config (or env var / default)."""
    return _get_ai_config().get("active_provider", DEFAULT_PROVIDER)


def _set_active_provider(provider: str) -> None:
    """Set the active AI provider and persist."""
    config = _get_ai_config()
    config["active_provider"] = provider
    _save_ai_config(config)
    _invalidate_ai_config_cache()


def _get_personality() -> str:
    """Get the active agent personality from persisted config."""
    return _get_ai_config().get("personality", DEFAULT_PERSONALITY)


def _set_personality(personality: str) -> None:
    """Set the active agent personality and persist."""
    config = _get_ai_config()
    config["personality"] = personality
    _save_ai_config(config)
    _invalidate_ai_config_cache()


# ─── Per-user config ──────────────────────────────────────────────


def _get_user_config(user_id: str) -> dict | None:
    """Fetch a user's per-user AI config from the DB.

    Returns None if no row exists (caller should fall back to global config).
    API keys in the returned dict are DECRYPTED.
    """
    from database import query_one

    row = query_one("SELECT * FROM user_config WHERE user_id = %s", (user_id,))
    if not row:
        return None

    # Decrypt API keys
    from ai.encryption import decrypt_api_keys
    api_keys = decrypt_api_keys(row.get("api_keys_encrypted") or "")

    return {
        "provider": row.get("provider") or "notebooklm",
        "personality": row.get("personality") or "",
        "api_keys": api_keys,
        "key_index": row.get("key_index") or 0,
        "vault_path": row.get("vault_path") or "",
        "priority_context": row.get("priority_context") or "",
        "voice_mode": bool(row.get("voice_mode")),
        "notebooklm_profile": row.get("notebooklm_profile") or "",
    }


def _save_user_config(user_id: str, updates: dict) -> None:
    """Upsert a user's per-user AI config.

    The `api_keys` key, if present, is ENCRYPTED before storage.
    """
    from database import execute, query_one

    sets = []
    params = []

    for field in ("provider", "personality", "vault_path", "priority_context", "notebooklm_profile"):
        if field in updates:
            val = updates[field]
            sets.append(f"{field} = %s")
            params.append(str(val) if val else "")

    if "key_index" in updates:
        sets.append("key_index = %s")
        params.append(int(updates["key_index"]))

    if "voice_mode" in updates:
        sets.append("voice_mode = %s")
        params.append(bool(updates["voice_mode"]))

    if "api_keys" in updates:
        from ai.encryption import encrypt_api_keys
        encrypted = encrypt_api_keys(updates["api_keys"])
        sets.append("api_keys_encrypted = %s")
        params.append(encrypted)

    if not sets:
        return

    sets.append("updated_at = NOW()")
    params.append(user_id)

    existing = query_one("SELECT 1 FROM user_config WHERE user_id = %s", (user_id,))
    if existing:
        sql = f"UPDATE user_config SET {', '.join(sets)} WHERE user_id = %s"
        execute(sql, tuple(params))
    else:
        field_names = [s.rpartition(" = ")[0] for s in sets]
        placeholders = ", ".join("%s" for _ in field_names)
        sql = f"INSERT INTO user_config (user_id, {', '.join(field_names)}) VALUES (%s, {placeholders})"
        execute(sql, tuple([user_id] + params))


def _get_user_provider(user_id: str) -> str:
    """Resolve the active provider for a user.

    Returns the user's configured provider, or empty string if the user
    has no configuration at all (new user = blocked, must configure first).
    Unlike the global getter, this does NOT fall back to the global config
    — a user without per-user config has no access until they set it up.
    """
    user_cfg = _get_user_config(user_id)
    if user_cfg and user_cfg.get("provider"):
        return user_cfg["provider"]
    return ""


def _get_user_personality(user_id: str) -> str:
    """Resolve the active personality for a user.

    Returns empty string if user has no per-user config.
    """
    user_cfg = _get_user_config(user_id)
    if user_cfg and user_cfg.get("personality"):
        return user_cfg["personality"]
    return ""


def _get_user_api_keys(user_id: str) -> dict:
    """Resolve API keys for a user.

    Returns the user's own keys, or empty dict if the user has no
    per-user config. Global API keys are NOT shared — each user
    must bring their own keys or use a provider without keys.
    """
    user_cfg = _get_user_config(user_id)
    if user_cfg and user_cfg.get("api_keys"):
        return user_cfg["api_keys"]
    return {}


# ─── DAILY USAGE LIMITS per task_type ───────────────────────────────
# Counter is reset at UTC midnight. Used by GET /api/ai/usage.
# Types/limits map to the buttons in renderMdAiButtonsHtml().
DAILY_LIMITS: dict[str, int] = {
    "generate_content": 50,       # 🤖 Markdown (from PDF), ☀️ HTML, 🌙 HTMLdark
    "generate_test": 50,          # 🤖 Test
    "notebooklm": 50,             # ✨ NotebookLM (PDF→MD, PDF→HTML)
    "notebooklm_test": 50,        # ✨ Nb Test
    "notebooklm_enhance_md": 50,  # ✨ Nb MD
    "notebooklm_md_to_html": 50,  # ✨ Nb HTML
    "opencode_pdf": 50,           # 🤖 OpenZEN (local PDF + AI structuring)
    "opencode_audio": 10,         # 🎵 Audio (OpenZEN + edge-tts)
    "notebooklm_audio": 10,       # 🎵 Audio (NotebookLM/Gemini + edge-tts)
    "notebooklm_infographic": 10, # 📊 Infographic (Gemini, expensive)
    "knowledge_pipeline": 50,     # 🧠 Gen. Contenido (NotebookLM)
    "generate_grammar": 50,       # ✏️ English Exercises (NotebookLM)
    "youtube": 50,                # YouTube transcription
}