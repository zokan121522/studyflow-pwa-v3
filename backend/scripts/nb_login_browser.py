#!/usr/bin/env python3
"""Real desktop-Chrome interactive login for NotebookLM.

Why this exists (2026-10-06):

    ``notebooklm login --browser chrome`` stopped working against
    notebook.google.com: the SPA shell answers HTTP 200 with the app HTML even
    when unauthenticated, so the CLI's URL-based landing check
    (url_matches_base_host) reports "Already logged in." on a *fresh* profile,
    captures whatever domain cookies exist (SID missing), writes junk to
    storage_state.json, and closes Chrome without ever showing the sign-in
    form. App-Bound Encryption (Chrome 127+) was a red herring there: a fresh
    profile never had a session to encrypt.

    This helper drives Playwright directly:
      1. opens a *persistent* Chrome profile straight at the
         accounts.google.com login form (server-rendered, not the SPA),
      2. waits for the OAuth cookies (SID + __Secure-1PSIDTS) to appear in the
         live browser context,
      3. only then persists storage_state.json.

    Cookies read through the live browser are decrypted by Chrome itself, so
    App-Bound Encryption is a non-issue. If the persistent profile already
    holds a live session, the cookies are present immediately and the helper
    exits fast without bothering the human.

    Exit codes:
        0  success (storage_state.json written)
        2  timeout waiting for login
        3  browser failed to launch
        4  all browser pages closed before login completed
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager

# The pair the SDK's own auth check treats as "logged in".
REQUIRED = {"SID", "__Secure-1PSIDTS"}
# Server-rendered Google login; unlike notebook.google.com's SPA it never
# answers HTTP 200 to an unauthenticated fresh profile.
ACCOUNTS_URL = "https://accounts.google.com/"
POLL_SECS = 2


@contextmanager
def windows_playwright_event_loop() -> Iterator[None]:
    """Playwright's sync API spawns the browser via subprocess, which needs
    ``ProactorEventLoop`` on Windows. Swap the policy in for the Playwright
    section and restore it on exit. No-op on non-Windows platforms.
    (Same pattern as notebooklm._browser.browser_capture.)
    """
    if sys.platform != "win32":
        yield
        return
    original_policy = asyncio.get_event_loop_policy()
    asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    try:
        yield
    finally:
        asyncio.set_event_loop_policy(original_policy)


def _cookie_ready(cookies: list[dict]) -> bool:
    names = {
        c["name"]
        for c in cookies
        if (c.get("domain") or "").endswith(".google.com")
    }
    return REQUIRED.issubset(names)


def _dot_google(c: dict) -> bool:
    return (c.get("domain") or "").endswith(".google.com")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile-dir", required=True)
    ap.add_argument("--storage", required=True)
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - env-dependent
        print(f"AUTH_LAUNCH_ERROR playwright unavailable: {exc}", file=sys.stderr)
        return 3

    deadline = time.monotonic() + args.timeout
    try:
        with windows_playwright_event_loop(), sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=args.profile_dir,
                channel="chrome",
                headless=False,
                # Same anti-automation flags as the SDK (browser_capture.py):
                # without them Google signs you out with "This browser or app
                # may not be secure" (webdriver is detectable).
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    "--password-store=basic",
                ],
                ignore_default_args=["--enable-automation"],
            )
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(ACCOUNTS_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                # Redirect races while the profile initialises; the poll below
                # is the source of truth either way.
                pass

            try:
                while True:
                    if not ctx.pages:
                        print("AUTH_BROWSER_CLOSED", file=sys.stderr)
                        return 4
                    try:
                        cookies = ctx.cookies()
                    except Exception as exc:
                        print(f"AUTH_ERROR reading cookies: {exc}", file=sys.stderr)
                        return 4
                    if _cookie_ready(cookies):
                        state = ctx.storage_state()
                        state["cookies"] = [
                            c for c in state.get("cookies", []) if _dot_google(c)
                        ]
                        dirn = os.path.dirname(args.storage)
                        os.makedirs(dirn, exist_ok=True)
                        fd, tmppath = tempfile.mkstemp(dir=dirn, suffix=".tmp")
                        with os.fdopen(fd, "w") as fh:
                            json.dump(state, fh)
                        os.replace(tmppath, args.storage)
                        print("AUTH_OK", file=sys.stderr)
                        return 0
                    if time.monotonic() >= deadline:
                        print("AUTH_TIMEOUT", file=sys.stderr)
                        return 2
                    time.sleep(POLL_SECS)
            finally:
                try:
                    ctx.close()  # closes Chrome; no orphan windows on exit
                except Exception:
                    pass
    except Exception as exc:  # pragma: no cover - env-dependent
        print(f"AUTH_LAUNCH_ERROR {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())