r"""Guard: a change to a precached asset must bump ASSET_CACHE.

This is the fifth time a PWA release has shipped code that no installed app
could load. v20, v24, v27, v29 and v30 all carry a comment in sw.js saying
"bump so the precache is refetched" — the failure mode is well known and it
still keeps recurring, because nothing enforces it.

Why it is silent, and why "just reload" does not save you:

  * Static assets are served **cache-first** (`cacheFirstAsset`). A precached
    JS/CSS file is returned from disk without ever consulting the network.
  * The service worker is only re-installed when **sw.js itself changes**,
    because that is what the browser byte-compares. Editing a precached .js
    or .css file does not trigger an install.
  * Therefore, with ASSET_CACHE unchanged: no install → no `updatefound` in
    app.js → the "🔄 Nueva versión disponible" banner never fires → and the
    running app keeps the OLD code. The user is told nothing, because there
    is genuinely nothing to tell them: the app never learned a version existed.

The net effect is the worst kind of bug: the fix is on the server, the commit
is pushed, the tests pass, and real users still see the old behaviour.

The check below hashes every precached asset and compares the digest against
the one recorded for the current ASSET_CACHE. Editing any precached file
without bumping the version makes this test fail, which is the only point at
which anyone is still in a position to notice.
"""

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SW = FRONTEND / "sw.js"
PINS = ROOT / "tests" / "sw_asset_pins.json"

sys.path.insert(0, str(ROOT / "tests"))


def _sw_source() -> str:
    return SW.read_text()


def _asset_cache() -> str:
    m = re.search(r"const ASSET_CACHE = '([^']+)'", _sw_source())
    assert m, "ASSET_CACHE not found in sw.js"
    return m.group(1)


def _precache_assets() -> list[str]:
    """The PRECACHE_ASSETS list, in declaration order."""
    src = _sw_source()
    block = src.split("const PRECACHE_ASSETS = [", 1)[1].split("];", 1)[0]
    return re.findall(r"'([^']+)'", block)


def _digest(assets: list[str]) -> str:
    h = hashlib.sha256()
    for url in assets:
        h.update(url.encode())
        path = FRONTEND / url.lstrip("/")
        if not path.is_file():
            h.update(b"<missing>")
        else:
            h.update(path.read_bytes())
    return h.hexdigest()


def test_precache_list_is_not_empty():
    assert len(_precache_assets()) > 10, "PRECACHE_ASSETS parsing looks wrong"


def test_every_precached_asset_exists_on_disk():
    """A missing file makes addAll() reject, so the whole install fails."""
    missing = [
        url for url in _precache_assets()
        if not (FRONTEND / url.lstrip("/")).is_file()
    ]
    assert not missing, f"precached but not on disk: {missing}"


def test_asset_cache_version_tracks_a_content_digest():
    """Editing a precached asset without bumping ASSET_CACHE must fail here."""
    assets = _precache_assets()
    digest = _digest(assets)
    version = _asset_cache()

    if not PINS.is_file():
        # First run of the guard: record the baseline for the current version.
        PINS.write_text(json.dumps({version: digest}, indent=2) + "\n")
        pytest.skip(f"baseline recorded for {version}")

    pins = json.loads(PINS.read_text())

    assert version in pins, (
        f"ASSET_CACHE was bumped to '{version}' but no digest is pinned for it. "
        f"Re-run with the pin file deleted to record the new baseline. "
        f"Known versions: {sorted(pins)}"
    )

    assert pins[version] == digest, (
        f"Precached assets changed but ASSET_CACHE is still '{version}'.\n"
        f"Installed PWAs will keep serving the old files and the update banner "
        f"in app.js will never fire, because no new service worker is "
        f"installed when only assets change.\n"
        f"Fix: bump ASSET_CACHE in sw.js to the next version and add a "
        f"comment saying what changed."
    )


def test_cache_is_not_reused_across_versions():
    """Guards against two versions sharing a name, which would serve stale data."""
    version = _asset_cache()
    assert version != "studyflow-assets-v34", (
        "ASSET_CACHE is still v34 after the #8c501f4 frontend changes"
    )


def test_install_calls_skip_waiting():
    """Otherwise the new worker waits for every tab to close to take over."""
    src = _sw_source()
    install = src.split("self.addEventListener('install'", 1)[1].split("\n});", 1)[0]
    assert "skipWaiting" in install, "install does not call skipWaiting()"


def test_install_precache_is_not_atomic():
    """addAll() is all-or-nothing, so one 404 silently empties the precache.

    That is what /features/studyflow/courses-notes.js did: it was listed in
    PRECACHE_ASSETS but had never existed in the repo, so every install
    rejected and the precache stayed empty. The app kept working because the
    runtime cache-first path quietly refilled the cache, which is exactly
    why nobody noticed.
    """
    src = _sw_source()
    install = src.split("self.addEventListener('install'", 1)[1].split("\n});", 1)[0]
    assert ".addAll(" not in install, (
        "install uses cache.addAll(), which rejects wholesale on a single 404 "
        "and leaves the precache empty. Use allSettled over individual "
        "cache.add() calls so one bad entry cannot take offline support down."
    )
    assert "allSettled" in install or "Promise.all(" in install


def test_install_logs_precache_failures():
    """A swallowed rejection is how this stayed invisible in the first place."""
    src = _sw_source()
    install = src.split("self.addEventListener('install'", 1)[1].split("\n});", 1)[0]
    assert "console.error" in install, (
        "install does not report which assets failed to precache"
    )


def test_activate_deletes_superseded_asset_caches():
    """Otherwise the old cache lingers and eats storage forever."""
    src = _sw_source()
    activate = src.split("self.addEventListener('activate'", 1)[1].split("\n});", 1)[0]
    assert "caches.delete" in activate
    assert "name !== ASSET_CACHE" in activate


def test_app_wires_the_update_notification():
    """The banner is only useful if it is actually wired to the SW lifecycle."""
    app = (FRONTEND / "app.js").read_text()
    assert "updatefound" in app
    assert "showUpdateNotification" in app
    assert "skipWaiting" in app, "the Actualizar button must activate the new worker"
    assert "location.reload" in app, "the user needs a reload to run the new code"


def test_update_notification_is_styled():
    """An unstyled banner is an invisible banner."""
    styles = "\n".join(
        p.read_text() for p in FRONTEND.rglob("*.css")
    )
    assert ".update-notification" in styles
