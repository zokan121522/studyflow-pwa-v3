"""The downloads landing page must never ship a stale hardcoded version.

``deploy/downloads/public/index.html`` (https://studyflowhub.dev) renders
before ``version.json`` arrives, so whatever sits in the HTML is what a
no-JS crawler — and a user on a slow connection — sees. Historically the
JSON-LD ``softwareVersion`` and the macOS/Linux download hrefs stayed
pinned to ``3.0.0`` forever while the app moved on, so the page offered
ZIP files that no longer existed (404).

The page is now version-proof by construction:

* download hrefs point at ``StudyFlow-<os>-latest.zip``, the alias that
  ``publish.sh`` rewrites on every publish for every OS;
* ``publish.sh`` rewrites the JSON-LD ``softwareVersion`` at publish time;
* the visible fallback texts are refreshed from ``version.json`` at runtime.

These tests pin that state: the current version always comes from
``APP_VERSION`` in ``backend/routes/health.py`` (single source of truth),
never from a literal in the test.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path[:0] = [str(BACKEND), str(ROOT)]

PAGE = ROOT / "deploy" / "downloads" / "public" / "index.html"
HEALTH = ROOT / "backend" / "routes" / "health.py"

# Version literals allowed in the page besides the current APP_VERSION.
# Only the "Histórico de versiones" table may name a superseded release.
ALLOWED_HISTORY = {"3.0.0"}

# X.Y.Z that is not part of a longer dotted run (skips 127.0.0.1 etc.).
VERSION_RE = re.compile(r"(?<![\d.])\d+\.\d+\.\d+(?![\d.])")


def app_version() -> str:
    """Current release, read from the single source of truth."""
    m = re.search(
        r"^APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]",
        HEALTH.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert m, f"APP_VERSION not found in {HEALTH}"
    return m.group(1)


def test_no_versioned_download_href():
    """(a) No href may point at a ZIP of a specific (stale) version."""
    current = app_version()
    hrefs = re.findall(r'href="([^"]+\.zip)"', PAGE.read_text(encoding="utf-8"))
    stale = sorted(
        h for h in hrefs
        for v in VERSION_RE.findall(h)
        if v != current
    )
    assert not stale, (
        f"download hrefs referencing a version other than {current}: {stale}"
    )


def test_version_literals_only_in_history():
    """(b) Any other X.Y.Z literal must belong to the histórico list."""
    current = app_version()
    allowed = ALLOWED_HISTORY | {current}
    found = sorted(set(VERSION_RE.findall(PAGE.read_text(encoding="utf-8"))))
    unexpected = [v for v in found if v not in allowed]
    assert not unexpected, (
        f"version literals outside {{{', '.join(sorted(allowed))}}} "
        f"(current or histórico): {unexpected}"
    )


def test_jsonld_software_version_matches_app_version():
    """(c) The JSON-LD literal must equal APP_VERSION (publish.sh rewrites it)."""
    m = re.search(
        r'"softwareVersion"\s*:\s*"([^"]+)"',
        PAGE.read_text(encoding="utf-8"),
    )
    assert m, "JSON-LD softwareVersion missing from the downloads page"
    assert m.group(1) == app_version(), (
        f"JSON-LD softwareVersion {m.group(1)} != APP_VERSION {app_version()}"
    )
