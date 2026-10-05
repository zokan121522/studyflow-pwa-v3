# PWA Icons

These are **generated**, not hand-placed:

```bash
python3 scripts/generate-icons.py
```

Edit that script to change the mark, then re-run it. The output is validated
by `tests/test_pwa_icons.py`, which checks each file is a real PNG — valid
chunk CRCs and image data that actually inflates — and that everything
`manifest.json` and `index.html` advertise exists and decodes.

## What was wrong before

The three PNGs previously in this directory were not images. Each had a valid
PNG signature and a plausible `IHDR`, so `file` reported `192x192, 8-bit/color
RGB` and any hand-written size check passed. But the `IHDR` CRC did not match
its own bytes and the `IDAT` payload was not zlib data at all. Chrome rejected
them:

```
Error while trying to use the following icon from the Manifest:
http://…/icons/icon-192.png (Download error or resource isn't a valid image)
```

That silently broke the favicon, the installed-app icon, the iOS home-screen
icon, and both manifest shortcuts. `tests/test_pwa_icons.py` now fails the build
if it recurs.

## Files

- `icon-192.png` — 192×192, maskable. Used by the manifest, the favicon and
  `apple-touch-icon`.
- `icon-512.png` — 512×512, maskable. The install icon on most platforms.
- `splash-192x192.png` — 192×192. Currently unreferenced; kept because it was
  already tracked.

## Maskable geometry

`purpose` is `any maskable`, so platforms may crop the icon to a circle or a
squircle. The generator therefore fills the whole canvas with
`#1a1a2e` (the manifest `background_color`, so there is no white fringe) and
keeps the mark inside the central 60% — measured radius 65.5px against a
76.8px limit on the 192px icon.

## On the artwork

The current mark is a plain open book in the manifest's colours: functional and
geometrically correct, but not finished branding. To replace it, either point
`scripts/generate-icons.py` at real artwork, or drop real PNGs in at the paths
above and keep the tests passing.

## Still missing

`index.html` and `manifest.json` reference no iOS splash screens. A previous
revision of this README listed `splash-640x1136.png` through
`splash-2048x2732.png`; they were never generated and nothing references them,
so they are listed here as known-absent rather than as files to wait for.