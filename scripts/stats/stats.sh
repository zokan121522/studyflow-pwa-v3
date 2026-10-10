#!/usr/bin/env bash
# StudyFlow — xerador da páxina interna de estadísticas /stats.html
# 1) Sincroniza o access log de nginx (docker logs) a un log agregado no host
# 2) Produce public/stats.html (tema escuro, en galego/castelán, autoexplicativo)
# Uso:  bash ~/studyflow-downloads/stats.sh
# Cron: */5 * * * * bash /home/server/studyflow-downloads/stats.sh
set -euo pipefail

BASE="${STATS_BASE:-$HOME/studyflow-downloads}"
LOGDIR="$BASE/logs"
ACC="$LOGDIR/access.log"
STATE="$LOGDIR/.last_docker_ts"
OUT="$BASE/public/stats.html"
CONTAINER="${STATS_CONTAINER:-studyflow-downloads}"
TZ_NAME="${STATS_TZ:-Europe/Madrid}"

mkdir -p "$LOGDIR" "$(dirname "$OUT")"
touch "$ACC"

# ---------------------------------------------------------------- 1) ingestar
RAW="$LOGDIR/.raw.$$"
docker logs -t "$CONTAINER" 2>/dev/null > "$RAW" || true

python3 - "$RAW" "$ACC" "$STATE" <<'PY'
import os, sys
raw_path, acc_path, state_path = sys.argv[1:4]
last = ""
if os.path.exists(state_path):
    last = open(state_path).read().strip()
parsed = []
with open(raw_path, errors="replace") as fh:
    for line in fh:
        line = line.rstrip("\n")
        if not line:
            continue
        ts, _, rest = line.partition(" ")
        if rest:
            parsed.append((ts, rest))
if not parsed:
    sys.exit(0)
max_ts = max(t for t, _ in parsed)
# Se o ultimo timestamp do contenedor e mais antigo que o gardado, o log
# reiniciou (recreate do contenedor): collemos todo o que hai agora.
if not last or max_ts < last:
    keep = parsed
else:
    keep = [p for p in parsed if p[0] > last]
if keep:
    with open(acc_path, "a") as fh:
        for _, rest in keep:
            fh.write(rest + "\n")
    with open(state_path, "w") as fh:
        fh.write(max(t for t, _ in keep))
PY
rm -f "$RAW"

# ----------------------------------------------------------------- 2) generar
python3 - "$ACC" "$OUT" "$TZ_NAME" <<'PY'
import datetime as dt
import html
import os
import re
import sys
from collections import Counter
from zoneinfo import ZoneInfo

acc_path, out_path, tz_name = sys.argv[1:4]
TZ = ZoneInfo(tz_name)

LINE = re.compile(
    r'^(?P<ip>\S+) - \S+ \[(?P<dt>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>\S+) [^"]*" '
    r'(?P<status>\d+) (?P<bytes>\d+) "(?P<ref>[^"]*)" "(?P<ua>[^"]*)"'
    r'(?: "(?P<xff>[^"]*)")?\s*$')
ZIP = re.compile(r'^/StudyFlow-(windows|macos|linux)-([^/]+)\.zip$', re.I)
DT_FMT = "%d/%b/%Y:%H:%M:%S %z"

entries = []
bad = 0
with open(acc_path, errors="replace") as fh:
    for raw in fh:
        m = LINE.match(raw.rstrip("\n"))
        if not m:
            bad += 1
            continue
        try:
            when = dt.datetime.strptime(m["dt"], DT_FMT).astimezone(TZ)
        except ValueError:
            bad += 1
            continue
        internal = m["ip"] in ("127.0.0.1", "::1")   # healthcheck de nginx
        xff = m["xff"]
        client = xff if xff not in (None, "", "-") else m["ip"]
        entries.append({
            "when": when, "ip": client, "raw_ip": m["ip"], "method": m["method"],
            "path": m["path"], "status": int(m["status"]), "ua": m["ua"],
            "internal": internal,
        })

now = dt.datetime.now(TZ)
today = now.date()

def is_visit(e):
    p = e["path"]
    if e["internal"] or e["status"] != 200 or p.startswith("/stats"):
        return False
    return p == "/" or p.endswith(".html")

def zip_platform(e):
    m = ZIP.match(e["path"])
    # Solo GET reales (200/206): ignora HEAD de sondeo y errores.
    if not m or e["internal"] or e["method"] != "GET" or e["status"] not in (200, 206):
        return None
    return m.group(1).lower(), m.group(2)

visits = [e for e in entries if is_visit(e)]
downloads = [e for e in entries if zip_platform(e)]
plat = Counter(zip_platform(e)[0] for e in downloads)
variant = Counter(zip_platform(e) for e in downloads)   # (plat, version)

visits_today = sum(1 for e in visits if e["when"].date() == today)
dl_today = sum(1 for e in downloads if e["when"].date() == today)
plat_today = Counter(zip_platform(e)[0] for e in downloads if e["when"].date() == today)

polls = sum(1 for e in entries if e["path"].startswith("/version.json"))
recent = [e for e in reversed(entries)
          if not e["internal"] and not e["path"].startswith("/version.json")][:20]

PLATFORMS = [("windows", "Windows"), ("macos", "macOS"), ("linux", "Linux")]
versions = sorted({v for _, v in variant})

def esc(s):
    return html.escape(str(s), quote=True)

rows_plat = []
for key, label in PLATFORMS:
    tds = "".join(
        f"<td>{variant.get((key, v), 0)}</td>" for v in versions)
    rows_plat.append(
        f"<tr><td class='pl'>{label}</td>{tds}"
        f"<td><b>{plat.get(key, 0)}</b></td><td>{plat_today.get(key, 0)}</td></tr>")

head_plat = "".join(f"<th>{esc(v)}</th>" for v in versions)

tot_by_ver = {v: sum(c for (_, ver), c in variant.items() if ver == v) for v in versions}
tot_ver_cells = "".join(f"<td>{tot_by_ver[v]}</td>" for v in versions)

rows_recent = "".join(
    "<tr><td class='mono'>{}</td><td class='mono ip'>{}</td>"
    "<td class='mono'>{}</td><td class='mono ruta'>{}</td>"
    "<td class='st {}'>{}</td></tr>".format(
        e["when"].strftime("%Y-%m-%d %H:%M:%S"),
        esc(e["ip"]),
        esc(e["method"]),
        esc(e["path"]),
        "ok" if e["status"] == 200 else "err",
        e["status"],
    ) for e in recent) or "<tr><td colspan='5' class='empty'>Sen entradas aínda</td></tr>"

first = min((e["when"] for e in entries), default=None)
last = max((e["when"] for e in entries), default=None)

doc = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>StudyFlow · Estadísticas internas</title>
<style>
:root{{--bg:#0f1117;--surface:#171a22;--border:#262a35;--text:#e8eaf0;--muted:#98a0b3;
--accent:#5b8cff;--ok:#3ecf8e;--warn:#e8b93f;--err:#ff6b6b;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
font-size:15px;line-height:1.5}}
.wrap{{max-width:960px;margin:0 auto;padding:28px 20px 60px}}
header{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:6px}}
.logo{{width:30px;height:30px;border-radius:8px;background:var(--accent);color:#fff;
display:grid;place-items:center;font-weight:700;font-size:14px;flex:none}}
h1{{font-size:21px;margin:0}}
.lock{{font-family:var(--mono);font-size:11px;color:var(--warn);
border:1px solid rgba(232,185,63,.4);background:rgba(232,185,63,.1);
padding:3px 8px;border-radius:99px;text-transform:uppercase;letter-spacing:.6px}}
.sub{{color:var(--muted);font-size:13px;margin:0 0 22px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:24px}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:16px 18px}}
.lab{{font-size:11px;text-transform:uppercase;letter-spacing:.9px;color:var(--muted);margin-bottom:6px}}
.val{{font-size:30px;font-weight:700;font-family:var(--mono)}}
.val.ok{{color:var(--ok)}} .val.acc{{color:var(--accent)}} .val.warn{{color:var(--warn)}}
.hint{{font-size:12px;color:var(--muted);margin-top:4px}}
h2{{font-size:15px;margin:28px 0 10px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted)}}
table{{width:100%;border-collapse:collapse;background:var(--surface);
border:1px solid var(--border);border-radius:14px;overflow:hidden;font-size:14px}}
th,td{{padding:9px 12px;text-align:left;border-bottom:1px solid var(--border)}}
th{{font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:var(--muted);
background:#12151d;font-weight:600}}
tr:last-child td{{border-bottom:none}}
td.pl{{font-weight:600}}
.mono{{font-family:var(--mono);font-size:13px}}
.ip{{color:var(--muted)}}
.ruta{{word-break:break-all;color:var(--text)}}
.st{{font-family:var(--mono);font-size:12px}}
.st.ok{{color:var(--ok)}} .st.err{{color:var(--err)}}
.empty{{color:var(--muted);text-align:center}}
.note{{color:var(--muted);font-size:13px;border-left:3px solid var(--border);
padding:2px 0 2px 12px;margin:14px 0}}
footer{{margin-top:34px;color:var(--muted);font-size:12px;font-family:var(--mono)}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">SF</div>
    <h1>StudyFlow · Estadísticas internas</h1>
    <span class="lock">privado · basic auth</span>
  </header>
  <p class="sub">Descargas dos zips e visitas da páxina de descargas
     (studyflowhub.dev). Rexenerado cada 5 minutos por <code>stats.sh</code>.</p>

  <div class="grid">
    <div class="card"><div class="lab">Visitas totais</div>
      <div class="val acc">{len(visits)}</div>
      <div class="hint">GET /, index.html e *.html (sen healthchecks)</div></div>
    <div class="card"><div class="lab">Visitas hoxe</div>
      <div class="val acc">{visits_today}</div>
      <div class="hint">xornada {today.isoformat()} ({tz_name})</div></div>
    <div class="card"><div class="lab">Descargas totais</div>
      <div class="val ok">{len(downloads)}</div>
      <div class="hint">GET *.zip con resposta 200</div></div>
    <div class="card"><div class="lab">Descargas hoxe</div>
      <div class="val ok">{dl_today}</div>
      <div class="hint">{today.isoformat()} ({tz_name})</div></div>
  </div>

  <h2>Descargas por plataforma</h2>
  <table>
    <thead><tr><th>Plataforma</th>{head_plat}<th>Total</th><th>Hoxe</th></tr></thead>
    <tbody>{"".join(rows_plat)}
      <tr><td class="pl">Todos</td>
      {tot_ver_cells}
      <td><b>{len(downloads)}</b></td><td>{dl_today}</td></tr>
    </tbody>
  </table>

  <h2>Últimas 20 peticiones relevantes</h2>
  <table>
    <thead><tr><th>Hora ({tz_name})</th><th>IP do cliente</th><th>Método</th><th>Ruta</th><th>Status</th></tr></thead>
    <tbody>{rows_recent}</tbody>
  </table>

  <p class="note">O contador de visitas ignora o healthcheck interno de nginx
     (127.0.0.1), as peticións a <code>/version.json</code> ({polls} polls do
     auto-updater) e a propia páxina <code>/stats</code>. As IP son as que
     chegan en <code>X-Forwarded-For</code> (túnel de Cloudflare).</p>

  <footer>
    Rexenerado: {now.strftime("%Y-%m-%d %H:%M:%S")} ({tz_name}) ·
    liñas no log: {len(entries)} · descartadas: {bad}<br>
    {"Rango: " + first.strftime("%Y-%m-%d %H:%M") + " → " + last.strftime("%Y-%m-%d %H:%M") if first else "Sen datos"} ·
    actualización automática cada 5 min
  </footer>
</div>
</body>
</html>
"""

tmp = out_path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    fh.write(doc)
os.replace(tmp, out_path)
PY

echo "[stats] $(date '+%F %T') -> $OUT ($(wc -l < "$ACC") liñas de log)"
