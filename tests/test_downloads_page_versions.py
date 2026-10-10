"""The downloads landing page must never ship a stale hardcoded version.

``deploy/downloads/public/index.html`` (https://studyflowhub.dev) renders
before ``version.json`` arrives, so whatever sits in the HTML is what a
no-JS crawler — and a user on a slow connection — sees. Historically the
JSON-LD ``softwareVersion`` and the macOS/Linux download hrefs stayed
pinned to ``3.0.0`` forever while the app moved on, so the page offered
ZIP files that no longer existed (404).

The page is version-proof by construction:

* download hrefs point at ``StudyFlow-<os>-latest.zip``, the alias that
  ``publish.sh`` rewrites on every publish for every OS;
* ``publish.sh`` rewrites the JSON-LD ``softwareVersion`` at publish time;
* the visible fallback texts are refreshed from ``version.json`` at runtime.

These tests pin that state: the page's *official* version is the stable
``version`` field of ``deploy/downloads/public/version.json`` (single
source of truth for what the channel ships), while a release still under
test lives in the separate ``beta`` block and may only appear inside the
explicitly-labelled beta section of the page.

A beta build must never be advertised as the current official release:
the page's version literals and download hrefs are checked against the
stable version, and the beta version is accepted only when it carries a
``-beta`` name and sits under the ``#beta`` section.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PAGE = ROOT / "deploy" / "downloads" / "public" / "index.html"
VERSION_JSON = ROOT / "deploy" / "downloads" / "public" / "version.json"

# Version literals allowed in the page besides the current stable version.
# Only the "Histórico de versiones" table may name a superseded release.
ALLOWED_HISTORY = {"3.0.0", "3.1.0"}

# X.Y.Z that is not part of a longer dotted run (skips 127.0.0.1 etc.).
VERSION_RE = re.compile(r"(?<![\d.])\d+\.\d+\.\d+(?![\d.])")


def _manifest() -> dict:
    return json.loads(VERSION_JSON.read_text(encoding="utf-8"))


def stable_version() -> str:
    """Current official release, read from the downloads manifest."""
    version = _manifest().get("version")
    assert version, f"'version' missing from {VERSION_JSON}"
    return version


def beta_versions() -> set[str]:
    """Versions published on the beta channel (may appear, but only as beta)."""
    beta = _manifest().get("beta") or {}
    version = beta.get("version")
    return {version} if version else set()


def test_no_versioned_download_href():
    """(a) Stable hrefs are version-less; only beta ZIPs may be versioned."""
    current = stable_version()
    betas = beta_versions()
    hrefs = re.findall(r'href="([^"]+\.zip)"', PAGE.read_text(encoding="utf-8"))
    stale = []
    for href in hrefs:
        for version in VERSION_RE.findall(href):
            if version == current:
                continue
            if version in betas and href.endswith(f"-{version}-beta.zip"):
                continue
            stale.append(href)
            break
    assert not stale, (
        f"download hrefs referencing a version other than {current}: "
        f"{sorted(set(stale))}"
    )


def test_version_literals_only_in_history_or_beta():
    """(b) Any other X.Y.Z literal must be histórico or the accepted beta."""
    current = stable_version()
    allowed = ALLOWED_HISTORY | {current} | beta_versions()
    found = sorted(set(VERSION_RE.findall(PAGE.read_text(encoding="utf-8"))))
    unexpected = [v for v in found if v not in allowed]
    assert not unexpected, (
        f"version literals outside {{{', '.join(sorted(allowed))}}} "
        f"(stable, histórico or beta): {unexpected}"
    )


def test_jsonld_software_version_matches_stable():
    """(c) The JSON-LD literal must equal version.json's stable version."""
    m = re.search(
        r'"softwareVersion"\s*:\s*"([^"]+)"',
        PAGE.read_text(encoding="utf-8"),
    )
    assert m, "JSON-LD softwareVersion missing from the downloads page"
    assert m.group(1) == stable_version(), (
        f"JSON-LD softwareVersion {m.group(1)} != stable {stable_version()}"
    )


def test_current_version_texts_match_stable():
    """(d) Every visible "current version" text matches the stable release."""
    current = stable_version()
    page = PAGE.read_text(encoding="utf-8")
    for element_id in ("tag-version", "m-version", "rel-version"):
        m = re.search(
            rf'id="{element_id}"[^>]*>\s*([^<]+?)\s*<',
            page,
        )
        assert m, f'element #{element_id} not found on the downloads page'
        assert m.group(1) == current, (
            f'#{element_id} shows {m.group(1)!r}, stable is {current!r}'
        )


def test_beta_version_is_labelled_as_beta():
    """(e) A beta version may only appear under the labelled #beta section."""
    betas = beta_versions()
    if not betas:
        return
    page = PAGE.read_text(encoding="utf-8")
    beta = next(iter(betas))
    assert f'id="beta"' in page, "page has a beta version but no #beta section"
    assert "BETA" in page, "beta section has no visible BETA badge"
    # Every beta-versioned download href must be a -beta.zip file.
    for href in re.findall(r'href="([^"]*\.zip)"', page):
        if beta in href:
            assert href.endswith(f"-{beta}-beta.zip"), (
                f"beta version referenced by a non-beta ZIP: {href}"
            )
