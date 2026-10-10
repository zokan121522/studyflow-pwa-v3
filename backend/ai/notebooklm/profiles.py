"""
NotebookLM cookie profile management — ownership, discovery, active profile.

Helpers ported from v2 ``routes/notebooklm_settings.py`` (Phase 30 — per-user
isolation) with v3 adaptations:

* ``COOKIE_DIR`` / ``ACTIVE_FILE`` come from ``ai.notebooklm.utils`` (single
  source of truth instead of the v2 duplicate constant).
* Database calls use the flat v3 API: ``execute`` / ``fetchone`` / ``fetchall``
  (v2 used ``db.execute`` / ``db.query`` / ``db.query_one``).
* ``current_user_id`` is passed explicitly (v3 uses the ``@token_required``
  decorator, which injects it as the first argument).
"""

import json
import os
import shutil

from database import execute, fetchall
from ai.notebooklm.utils import COOKIE_DIR, ACTIVE_FILE, _local_part

STORAGE_FILE = "storage_state.json"


def register_profile_owner(user_id: int, email: str) -> None:
    """Record that *user_id* owns the cookie profile for *email*.

    Idempotent — duplicate (user_id, email) pairs are silently ignored.
    """
    try:
        execute(
            "INSERT INTO notebooklm_owned_profiles (user_id, email) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, email),
        )
    except Exception:
        pass  # non-fatal


def get_owned_emails(user_id: int) -> set[str]:
    """Return the set of profile emails owned by *user_id*."""
    rows = fetchall("SELECT email FROM notebooklm_owned_profiles WHERE user_id = %s", (user_id,))
    return {r["email"] for r in rows}


def get_all_owned_emails() -> set[str]:
    """Return the set of ALL profile emails that have **any** owner."""
    rows = fetchall("SELECT DISTINCT email FROM notebooklm_owned_profiles")
    return {r["email"] for r in rows}


def assert_profile_access(user_id: int, email: str) -> None:
    """Reject mutations of a profile the current user does not own.

    Security (audit run-1, notebooklm-profile-idor): switch/disconnect/
    delete previously operated on any profile dir without an ownership
    check, letting any user delete or hijack other users' cookie profiles
    (live Google auth).  A profile may only be mutated by its owner, or
    by anyone until it has been claimed (legacy unowned profiles).
    """
    owned_emails = get_owned_emails(user_id)
    if email in owned_emails:
        return  # owner — OK
    # Legacy unclaimed profile: first mutator may claim it.
    if email not in get_all_owned_emails():
        return
    raise PermissionError(f"El perfil '{email}' pertenece a otro usuario")


def ensure_cookie_dir() -> None:
    """Create the NotebookLM cookie directory if it doesn't exist."""
    os.makedirs(COOKIE_DIR, exist_ok=True)


def resolve_profile_dir(email: str) -> str | None:
    """Check both profiles/{email} and {email} inside COOKIE_DIR.

    The ``notebooklm`` third-party library expects profiles under the
    ``profiles/{email}/`` convention.  Export scripts and uploads both
    follow it, but legacy profiles may still exist directly under
    ``{email}/``.  This helper returns whichever exists, preferring the
    ``profiles/`` variant.

    v3 fix: also include the bare local-part variant (e.g. ``zokan121522``
    instead of ``zokan121522@gmail.com``) because the upload path creates
    directories named after the local part.  Priority is:
      1. First candidate containing a non-empty storage_state.json (size > 0)
      2. Fallback: first candidate that exists as a directory
    """
    local = _local_part(email)
    candidates = [
        os.path.join(COOKIE_DIR, "profiles", email),
        os.path.join(COOKIE_DIR, "profiles", local),
        os.path.join(COOKIE_DIR, email),
        os.path.join(COOKIE_DIR, local),
    ]

    # First pass: return first candidate with a non-empty storage_state.json
    for c in candidates:
        storage_path = os.path.join(c, STORAGE_FILE)
        if os.path.isdir(c) and os.path.isfile(storage_path):
            try:
                if os.path.getsize(storage_path) > 0:
                    return c
            except OSError:
                pass

    # Fallback: first candidate that exists as a directory
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def discover_profiles(user_id: int | None = None) -> list[dict]:
    """Scan COOKIE_DIR and COOKIE_DIR/profiles/ for storage_state.json.

    When *user_id* is given, only profiles owned by that user are returned.
    Legacy (unowned) profiles are **auto-claimed** for the user — the first
    user to call this function after deploy inherits any profiles that have
    no owner yet.  This prevents cross-user visibility of NB accounts.

    Returns:
        List of {email, path, active} dicts, sorted by email.
    """
    ensure_cookie_dir()
    profiles: list[dict] = []
    active_email = read_active_profile()

    # Directories to scan (new convention first, legacy fallback)
    scan_dirs = [
        os.path.join(COOKIE_DIR, "profiles"),  # export-script / upload convention
        COOKIE_DIR,                            # legacy direct profiles
    ]

    seen_dirs: set[str] = set()    # dedup by normalized path
    seen_emails: set[str] = set()  # dedup by email (prevent duplicates)

    # Collect all valid cookie paths + emails first
    found_entries: list[tuple[str, str]] = []  # (email, cookie_path)

    for scan_root in scan_dirs:
        if not os.path.isdir(scan_root):
            continue
        try:
            for entry in sorted(os.listdir(scan_root)):
                entry_path = os.path.join(scan_root, entry)
                cookie_path = os.path.join(entry_path, STORAGE_FILE)
                if not (os.path.isdir(entry_path) and os.path.isfile(cookie_path)):
                    continue
                # Normalize to avoid duplicates (e.g. profiles/foo + foo)
                norm = os.path.normpath(cookie_path)
                if norm in seen_dirs:
                    continue
                seen_dirs.add(norm)

                email = extract_email(cookie_path) or entry

                # If the directory name doesn't match the extracted email,
                # rename it so _resolve_profile_dir() and the notebooklm
                # library can find it by email alone.
                if email != entry:
                    new_entry_path = os.path.join(scan_root, email)
                    if not os.path.exists(new_entry_path):
                        try:
                            os.rename(entry_path, new_entry_path)
                            cookie_path = os.path.join(new_entry_path, STORAGE_FILE)
                        except OSError:
                            pass  # non-fatal — profile will still be listed

                if email in seen_emails:
                    continue
                seen_emails.add(email)
                found_entries.append((email, cookie_path))
        except OSError:
            pass

    # 2. Legacy global file: COOKIE_DIR/storage_state.json
    global_path = os.path.join(COOKIE_DIR, STORAGE_FILE)
    global_email: str | None = None
    if os.path.isfile(global_path):
        global_email = extract_email(global_path) or "default"
        if global_email not in seen_emails:
            seen_emails.add(global_email)
            found_entries.append((global_email, global_path))

    # ── Ownership filtering & auto-claim ──────────────────────────────
    if user_id:
        owned = get_owned_emails(user_id)
        all_owned = get_all_owned_emails()

        for email, cpath in found_entries:
            if email in owned:
                # Explicitly owned by this user — include
                profiles.append({
                    "email": email,
                    "path": cpath,
                    "active": email == active_email,
                })
            elif email not in all_owned:
                # Legacy profile: no owner at all → auto-claim for this user
                register_profile_owner(user_id, email)
                profiles.append({
                    "email": email,
                    "path": cpath,
                    "active": email == active_email,
                })
            # else: owned by a different user → skip
    else:
        # No user_id context → return everything (backward compat for SDK)
        for email, cpath in found_entries:
            profiles.append({
                "email": email,
                "path": cpath,
                "active": email == active_email,
            })

    return profiles


def extract_email(cookie_path: str) -> str | None:
    """Extract the account email from a storage_state.json, if present."""
    try:
        with open(cookie_path) as f:
            data = json.load(f)
        nb_meta = data.get("notebooklm", {})
        acct = nb_meta.get("account", {})
        raw = acct.get("email")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    except Exception:
        pass
    return None


def read_active_profile() -> str | None:
    """Read the active profile email from active.txt.

    Returns:
        The email string, or None if the file doesn't exist / is empty.
    """
    try:
        import pathlib
        raw = pathlib.Path(ACTIVE_FILE).read_text().strip()
        return raw if raw else None
    except (OSError, ValueError):
        return None


def write_active_profile(email: str | None) -> None:
    """Write (or clear) the active profile in active.txt."""
    import pathlib
    ensure_cookie_dir()
    if email:
        pathlib.Path(ACTIVE_FILE).write_text(email.strip())
    else:
        try:
            os.remove(ACTIVE_FILE)
        except FileNotFoundError:
            pass


def cookie_path(profile_email: str | None = None) -> str | None:
    """Resolve the cookie path for a given profile email.

    Resolution order:
      1. If profile_email is given, check profiles/{email}/ then {email}/
      2. If no email, check the active profile from active.txt
      3. Fall back to global COOKIE_DIR/storage_state.json
      4. Return None if nothing exists

    Returns the first existing path, or the most specific candidate.
    """
    candidates: list[str] = []

    # 1. Explicit email → check profiles/{email} and {email}
    if profile_email:
        profile_dir = resolve_profile_dir(profile_email)
        if profile_dir:
            candidates.append(os.path.join(profile_dir, STORAGE_FILE))

    # 2. Active profile from file
    active = read_active_profile() if not profile_email else None
    if active:
        profile_dir = resolve_profile_dir(active)
        if profile_dir:
            candidates.append(os.path.join(profile_dir, STORAGE_FILE))

    # 3. Legacy global
    candidates.append(os.path.join(COOKIE_DIR, STORAGE_FILE))

    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0] if candidates else None


def set_active_profile(user_id: int, email: str) -> None:
    """Set the active NotebookLM profile in BOTH stores from a single place.

    The UI reads ``user_config.notebooklm_profile`` while content generation
    reads ``active.txt``; writing them from one function is the only way to
    keep them from diverging. The 2026-10-03 bug was exactly this: the login
    flow wrote the DB but left ``active.txt`` pointing at a dead profile, so
    generation failed with "CSRF token not found".
    """
    save_user_notebooklm_profile(user_id, email)
    write_active_profile(email)


def clear_active_profile(user_id: int) -> None:
    """Clear the active profile from BOTH stores."""
    save_user_notebooklm_profile(user_id, "")
    write_active_profile(None)


def save_user_notebooklm_profile(user_id: int, email: str) -> None:
    """Save the NotebookLM profile email to the user's per-user config."""
    existing = fetchall("SELECT 1 FROM user_config WHERE user_id = %s", (user_id,))
    if existing:
        execute(
            "UPDATE user_config SET notebooklm_profile = %s, updated_at = NOW() WHERE user_id = %s",
            (email, user_id),
        )
    else:
        execute(
            "INSERT INTO user_config (user_id, notebooklm_profile) VALUES (%s, %s)",
            (user_id, email),
        )