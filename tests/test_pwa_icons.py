"""The PWA icons must be real images.

These three PNGs were not images. Each had a valid signature and a plausible
IHDR, so ``file`` reported "192x192, 8-bit/color RGB" and every size check
anyone wrote by hand passed -- but the IHDR CRC did not match its own bytes and
the IDAT payload was not zlib data. Browsers refused them:

    Image.decode -> decode failed
    "Error while trying to use the following icon from the Manifest"

That broke the favicon, the installed-app icon, the iOS home-screen icon and
both manifest shortcuts, silently, on every load. No test looked at them.

Deliberately stdlib-only. The corruption is a structural property of the file
-- chunk CRCs and whether the IDAT inflates -- so it can be checked without
PIL, and the test must not depend on an optional package being installed.
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
ICONS = FRONTEND / "icons"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def read_chunks(blob: bytes) -> list[tuple[str, bytes]]:
    """Every chunk as (type, data), asserting the container is well formed."""
    assert blob[:8] == PNG_SIGNATURE, "not a PNG: bad signature"
    chunks: list[tuple[str, bytes]] = []
    offset = 8
    while offset < len(blob):
        (length,) = struct.unpack(">I", blob[offset : offset + 4])
        ctype = blob[offset + 4 : offset + 8]
        data = blob[offset + 8 : offset + 8 + length]
        stored_crc = struct.unpack(">I", blob[offset + 8 + length : offset + 12 + length])[0]
        # The CRC covers the type and the data together. Checking either half
        # alone would pass a file whose payload had been tampered with.
        assert stored_crc == zlib.crc32(ctype + data) & 0xFFFFFFFF, (
            f"corrupt {ctype.decode()}: CRC mismatch"
        )
        chunks.append((ctype.decode("latin1"), data))
        offset += 12 + length
    assert offset == len(blob), "trailing bytes after IEND"
    assert chunks[-1][0] == "IEND", "file does not end with IEND"
    return chunks


def dimensions(blob: bytes) -> tuple[int, int]:
    for ctype, data in read_chunks(blob):
        if ctype == "IHDR":
            return struct.unpack(">II", data[:8])
    raise AssertionError("no IHDR")


def pixel_data_decodes(blob: bytes) -> bool:
    """Whether the compressed image data actually inflates."""
    raw = b"".join(data for ctype, data in read_chunks(blob) if ctype == "IDAT")
    try:
        zlib.decompress(raw)
    except zlib.error:
        return False
    return True


ICON_FILES = ["icon-192.png", "icon-512.png", "splash-192x192.png"]


@pytest.mark.parametrize("name", ICON_FILES)
def test_icon_is_a_decodable_image(name):
    blob = (ICONS / name).read_bytes()
    assert pixel_data_decodes(blob), f"{name}: IDAT is not valid zlib -- not an image"
    width, height = dimensions(blob)
    assert (width, height) == (int(re.search(r"(\d+)", name).group(1)),) * 2, (
        f"{name} is {width}x{height}, but its own name says otherwise"
    )


@pytest.mark.parametrize("name", ICON_FILES)
def test_icon_is_not_a_placeholder(name):
    """A flat single-colour file is not an icon; it is a missed delivery.

    The corrupt originals were 545 bytes of nothing. Real marks at these sizes
    compress to a few hundred bytes, so size alone proves nothing -- but a
    picture that inflates to fewer than a few hundred identical pixels is
    blank, and that is worth refusing to ship.
    """
    blob = (ICONS / name).read_bytes()
    raw = b"".join(data for ctype, data in read_chunks(blob) if ctype == "IDAT")
    pixels = zlib.decompress(raw)
    assert len(set(pixels)) > 8, (
        f"{name} inflates to {len(pixels)} bytes of a single flat colour"
    )


def test_manifest_icons_exist_and_are_valid():
    """Everything the manifest advertises has to actually load.

    A manifest entry pointing at a missing or unreadable file is exactly the
    failure Chrome was warning about, and it only shows up at install time.
    """
    manifest = json.loads((FRONTEND / "manifest.json").read_text())
    entries = list(manifest.get("icons", []))
    for shortcut in manifest.get("shortcuts", []):
        entries.extend(shortcut.get("icons", []))
    assert entries, "manifest declares no icons at all"

    for entry in entries:
        src = entry["src"]
        assert src.startswith("/"), f"unexpected icon src {src}"
        path = FRONTEND / src.lstrip("/")
        assert path.is_file(), f"manifest advertises {src}, which does not exist"
        blob = path.read_bytes()
        assert pixel_data_decodes(blob), f"manifest advertises {src}, which is not an image"
        if "sizes" in entry:
            declared = int(re.match(r"(\d+)x", entry["sizes"]).group(1))
            assert declared == dimensions(blob)[0], (
                f"{src} is declared {entry['sizes']} but is actually "
                f"{dimensions(blob)[0]}x{dimensions(blob)[1]}"
            )


def test_index_html_icon_links_exist():
    html = (FRONTEND / "index.html").read_text()
    refs = set(
        re.findall(r'<link[^>]+rel="(?:icon|apple-touch-icon)"[^>]*href="([^"]+)"', html)
    )
    assert refs, "index.html links no icons at all"
    for href in refs:
        if href.startswith("data:"):
            continue
        path = FRONTEND / href.lstrip("/")
        assert path.is_file(), f"index.html links {href}, which does not exist"
        assert pixel_data_decodes(path.read_bytes()), f"{href} is not a decodable image"