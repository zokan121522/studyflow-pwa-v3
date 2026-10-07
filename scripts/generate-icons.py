#!/usr/bin/env python3
"""Generate the PWA icons, so they are reproducible instead of mystery binaries.

Why this exists
---------------
The three PNGs that were in frontend/icons/ were not images. Each had a valid
PNG signature and a plausible IHDR -- which is why ``file`` reported "192x192,
8-bit/color RGB" and the sizes in the manifest looked satisfied -- but the
IHDR CRC did not match its own bytes and the IDAT payload was not zlib data at
all. Browsers rejected them outright:

    Image.decode -> "decode failed"
    console: "Error while trying to use the following icon from the Manifest"

So the favicon, the installed-app icon, the iOS home-screen icon and both
manifest shortcuts were all broken, and nothing in the test suite noticed
because no test looked at them.

Run this after changing the mark:

    python3 scripts/generate-icons.py

``tests/test_pwa_icons.py`` validates the output with nothing but the standard
library, so hand-edited or truncated icons fail the build again.

On the artwork
--------------
This draws a plain book on the manifest's background colour. It is a
functional placeholder with the right geometry and the right colours, not a
finished brand. Replace it by pointing this script at real artwork -- keeping
the maskable safe area below -- or drop real PNGs in and skip the script.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(os.path.dirname(HERE), "frontend", "icons")

#: Taken from frontend/manifest.json so the icon cannot drift from the theme.
BACKGROUND = (26, 26, 46)      # #1a1a2e
PAPER = (244, 240, 230)
PAGE_LINE = (206, 200, 188)

#: Maskable icons get cropped to a circle inscribed in the safe zone, so the
#: mark stays inside the middle 60% and the colour covers the whole canvas.
SAFE = 0.60


def _book(size: int) -> Image.Image:
    """An open book, drawn in fractional coordinates so it scales cleanly."""
    img = Image.new("RGB", (size, size), BACKGROUND)
    draw = ImageDraw.Draw(img)

    def pt(x: float, y: float) -> tuple[float, float]:
        """Fractional (0-1) canvas point, inset into the maskable safe area."""
        inset = (1.0 - SAFE) / 2.0
        return ((inset + x * SAFE) * size, (inset + y * SAFE) * size)

    line = max(2, round(size / 64))

    # Two pages meeting at a spine, with the top edge dipping toward the middle
    # the way an open book's does.
    left = [pt(0.02, 0.30), pt(0.47, 0.22), pt(0.47, 0.92), pt(0.02, 0.80)]
    right = [pt(0.98, 0.30), pt(0.53, 0.22), pt(0.53, 0.92), pt(0.98, 0.80)]
    draw.polygon(left, fill=PAPER)
    draw.polygon(right, fill=PAPER)

    # A sliver of shadow along the spine, so the pages read as separate.
    draw.rectangle([pt(0.485, 0.24), pt(0.515, 0.92)], fill=BACKGROUND)

    # Text lines on each page. Two per page is the most that stays legible at
    # 192px; more turns to mush at launcher size.
    for top in (0.40, 0.56):
        draw.line([pt(0.10, top), pt(0.40, top - 0.05)], fill=PAGE_LINE, width=line)
        draw.line([pt(0.60, top - 0.05), pt(0.90, top)], fill=PAGE_LINE, width=line)

    return img


def main() -> int:
    os.makedirs(ICON_DIR, exist_ok=True)
    written = []
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512),
                       ("splash-192x192.png", 192)):
        path = os.path.join(ICON_DIR, name)
        _book(size).save(path, "PNG", optimize=True)
        written.append((name, size, os.path.getsize(path)))

    # iOS picks the largest apple-touch-icon it is offered; a plain 512 is
    # fine and avoids shipping another file that nothing validates.
    for name, size, size_bytes in written:
        print(f"  {name:22} {size}x{size}  {size_bytes:,} bytes")
    print(f"\n  {ICON_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())