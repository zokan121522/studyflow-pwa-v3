#!/usr/bin/env python3
"""Renderiza TODOS los storyboards (secuencial) y copia los mp4 a la landing.

Uso:
    python3 tools/walkthrough/render_all.py

Cada storyboard vive en tools/walkthrough/storyboards/<slug>.json y su mp4
sale a tools/walkthrough/pilot/<slug>/<id>.mp4; se copia a
frontend/landing/videos/<slug>.mp4 (servido en /landing/videos/<slug>.mp4).
"""
import glob
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SB_DIR = os.path.join(HERE, "storyboards")
VID_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "frontend", "landing", "videos"))


def main() -> int:
    os.makedirs(VID_DIR, exist_ok=True)
    boards = sorted(glob.glob(os.path.join(SB_DIR, "*.json")))
    if not boards:
        print("No hay storyboards en", SB_DIR)
        return 1
    ok, failed = [], []
    for sb in boards:
        slug = os.path.splitext(os.path.basename(sb))[0]
        print(f"\n===== {slug} =====", flush=True)
        r = subprocess.run([sys.executable, os.path.join(HERE, "render.py"), sb])
        if r.returncode != 0:
            print(f"  !! render falló: {slug}")
            failed.append(slug)
            continue
        try:
            mp4 = os.path.join(HERE, "pilot", slug, json.load(open(sb))["id"] + ".mp4")
        except Exception:
            mp4 = None
        if mp4 and os.path.isfile(mp4):
            shutil.copy2(mp4, os.path.join(VID_DIR, slug + ".mp4"))
            print(f"  -> frontend/landing/videos/{slug}.mp4")
            ok.append(slug)
        else:
            failed.append(slug)
    print(f"\n== LISTO ==  ok={len(ok)}  fail={len(failed)}")
    if failed:
        print("  fallidos:", ", ".join(failed))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
