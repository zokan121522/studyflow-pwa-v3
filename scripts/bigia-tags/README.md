# Bigia TAG labels — parche v2 (APLICADO)

Feed por línea: `[HH:MM:SS] [agent] [project] [TAG]`. HH:MM:SS=arranque sesión (local);
TAG=1ª línea del prompt si es `[TAG]` (`_`/`-`→espacio: `[VOZ-JORGE]`→`[VOZ JORGE]`); si no,
`Delegation: <nombre>`/título. Defensivo ante tabla/columna/JSON ausente (no crashea).

Real: `~/.config/opencode/scripts/bigia_live.py` · Backup: `bigia_live.py.bak-20261011-184938`.

Aplicar (cd ~/.config/opencode/scripts):
`patch -p0 < /Users/jaimevilaplanarejon/orca/workspaces/studyflow-pwa-v3/scripts/bigia-tags/bigia_tags.patch && python3 -m py_compile bigia_live.py`
Revertir: `cp ~/.config/opencode/scripts/bigia_live.py.bak-20261011-184938 ~/.config/opencode/scripts/bigia_live.py`
o `patch -R -p0 < .../bigia_tags.patch`. Aplicar 2 veces falla (Reversed...), esperado.
