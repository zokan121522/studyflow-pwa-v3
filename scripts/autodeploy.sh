#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# StudyFlow — autodeploy de la página de descargas (LaunchAgent poller)
#
# Observa origin/main; si cambia su SHA, construye los paquetes portables
# (Windows, macOS y Linux) desde un worktree limpio (NUNCA toca el working
# tree del dev, que suele estar sucio) y sincroniza la public/ por rsync a la
# página del server vía Tailscale.
#
# Instalación:   ~/Library/LaunchAgents/com.studyflow.autodeploy.plist
# Logs:          ~/.studyflow/autodeploy.log
# Estado:        ~/.studyflow/autodeploy.state  (SHA de main ya desplegado)
# Test manual:   echo 0000000000000000000000000000000000000000 \
#                    > ~/.studyflow/autodeploy.state
#                bash scripts/autodeploy.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$HOME/orca/workspaces/studyflow-pwa-v3"
STATE_DIR="$HOME/.studyflow"
STATE="$STATE_DIR/autodeploy.state"
LOG="$STATE_DIR/autodeploy.log"
PIDF="$STATE_DIR/autodeploy.pid"
BUILD_ROOT="$STATE_DIR/build"
PUBLISH_HOST="${PUBLISH_HOST:-server}"
REMOTE_DIR="${PUBLISH_REMOTE_DIR:-~/studyflow-downloads/public}"

mkdir -p "$STATE_DIR"
log(){ echo "[$(date '+%F %T')] $*" >>"$LOG"; }

# ── una sola instancia (pidfile portable en macOS) ──────────────────────────
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF" 2>/dev/null)" 2>/dev/null; then
  exit 0
fi
echo $$ >"$PIDF"
trap 'rm -f "$PIDF"' EXIT

# ── 1. fetch remoto; sin red → silencio, reintento en el siguiente tick ─────
git -C "$REPO_ROOT" fetch origin main --quiet 2>>"$LOG" || { log "fetch falló (¿red?)."; exit 0; }
NEW_SHA="$(git -C "$REPO_ROOT" rev-parse origin/main)"
OLD_SHA="$(cat "$STATE" 2>/dev/null || echo '')"
[ "$NEW_SHA" = "$OLD_SHA" ] && exit 0   # sin cambios → no-op

log "cambio detectado: ${OLD_SHA:-<ninguno>} → $NEW_SHA"

# ── 2. worktree limpio de origin/main ───────────────────────────────────────
git -C "$REPO_ROOT" worktree remove --force "$BUILD_ROOT" 2>/dev/null || true
git -C "$REPO_ROOT" worktree add --detach "$BUILD_ROOT" origin/main >>"$LOG" 2>&1 \
  || { log "worktree add falló."; exit 1; }
trap 'git -C "$REPO_ROOT" worktree remove --force "$BUILD_ROOT" >>"$LOG" 2>&1 || true; rm -f "$PIDF"' EXIT

# ── 2b. deploy/ NO está versionado → copiar del repo dev al worktree ────────
#     publish.sh y los estáticos de la página viven solo en el tree local;
#     un worktree limpio de main no los tiene.
if [ -d "$REPO_ROOT/deploy" ]; then
  mkdir -p "$BUILD_ROOT/deploy"
  cp -R "$REPO_ROOT/deploy/." "$BUILD_ROOT/deploy/" 2>/dev/null || true
fi

# ── 3. construir los 3 paquetes portables (windows / macos / linux) ─────────
#     Se hacen AQUÍ, en el worktree limpio, para que publish.sh solo tenga
#     que copiarlos. Si uno falla, se aborta: publicar la mitad de las
#     plataformas deja la página de descargas mintiendo.
for BUILD in build-windows-portable.sh build-macos-portable.sh build-linux-portable.sh; do
  if ! bash "$BUILD_ROOT/scripts/$BUILD" >>"$LOG" 2>&1; then
    log "$BUILD falló."; exit 1
  fi
  log "$BUILD OK"
done

# ── 4. publicar (copia los 3 ZIP + version.json multi-plataforma) ───────────
if ! bash "$BUILD_ROOT/deploy/downloads/publish.sh" >>"$LOG" 2>&1; then
  log "publish.sh falló."; exit 1
fi

# ── 5. estáticos de la página (NO versionados) → al public/ del worktree ────
#     index.html e img/ viven solo en el repo local; sin este paso el rsync
#     --delete borraría la página del server.
for item in index.html img; do
  if [ -e "$REPO_ROOT/deploy/downloads/public/$item" ]; then
    cp -R "$REPO_ROOT/deploy/downloads/public/$item" "$BUILD_ROOT/deploy/downloads/public/" 2>/dev/null || true
  fi
done

# ── 6. sincronizar al server (Tailscale) ────────────────────────────────────
if ! rsync -avz --delete "$BUILD_ROOT/deploy/downloads/public/" "$PUBLISH_HOST:$REMOTE_DIR/" >>"$LOG" 2>&1; then
  log "rsync falló."; exit 1
fi

# ── 7. marcar estado ────────────────────────────────────────────────────────
echo "$NEW_SHA" >"$STATE"
log "OK publicada $NEW_SHA"