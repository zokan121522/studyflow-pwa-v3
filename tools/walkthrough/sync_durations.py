"""Ajusta la duración que muestra cada tarjeta al mp4 real.

No lo metas en render_all.py a pelo: se ejecuta justo después de grabar,
pero el HTML de la landing es un fichero independiente y si esto falla en
silencio el vídeo nuevo se sirve con la duración del viejo.

Uso:  python3 tools/walkthrough/sync_durations.py
"""
from __future__ import annotations

import pathlib
import re
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
VIDEOS = HERE.parents[1] / "frontend" / "landing" / "videos"
INDEX = HERE.parents[1] / "frontend" / "landing" / "index.html"

# El <span> de duración es el primero tras el <video> de ese fichero.
CARTA = re.compile(
    r'(src="/landing/videos/(?P<slug>[0-9a-z-]+\.mp4)"[^>]*>'
    r'.*?<div class="video-meta"><span>)(?P<dur>[0-9]+:[0-9]{2})(</span>)',
    re.S,
)


def duracion(mp4: pathlib.Path) -> str:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(mp4)],
        capture_output=True, text=True, check=True).stdout.strip()
    seg = int(round(float(out)))
    return f"{seg // 60}:{seg % 60:02d}"


def main() -> int:
    html = INDEX.read_text(encoding="utf-8")
    cambios = 0

    for mp4 in sorted(VIDEOS.glob("*.mp4")):
        real = duracion(mp4)
        m = CARTA.search(html)
        # Buscamos la carta cuyo slug coincide con este mp4: puede no ser la
        # primera, así que el sub hace el trabajo por cada coincidencia.
        def sustituto(mo: re.Match) -> str:
            nonlocal cambios
            if mo.group("slug") != mp4.name:
                return mo.group(0)
            if mo.group("dur") != real:
                cambios += 1
            return mo.group(1) + real + mo.group(4)

        html = CARTA.sub(sustituto, html)
        print(f"  {mp4.stem:<18} {real}")

    INDEX.write_text(html, encoding="utf-8")
    print(f"\ncartas corregidas: {cambios}")

    # Verificación: releemos del disco. Escribir sin comprobar ya me pasó
    # una vez: el fichero quedó con las duraciones viejas.
    final = {m.group("slug"): m.group("dur") for m in CARTA.finditer(INDEX.read_text())}
    malos = [n for n in (p.name for p in sorted(VIDEOS.glob("*.mp4")))
             if final.get(n) != duracion(VIDEOS / n)]
    if malos:
        print("SIGUEN MAL:", ", ".join(malos))
        return 1
    print("todas las duraciones coinciden con los mp4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())