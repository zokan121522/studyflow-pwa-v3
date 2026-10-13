#!/usr/bin/env bash
#
# StudyFlow macOS uninstaller — SAFE by default.
#
# Removes the StudyFlow *installation* from this Mac:
#   - stops the local backend (fixed port 8477) and any process running from an
#     app folder (matched by ABSOLUTE PATH, never by generic name),
#   - removes the StudyFlow helper LaunchAgents,
#   - moves the unzipped app copies and backend data to the Trash,
#   - keeps a WAL-safe snapshot of studyflow.db.
#
# DEFAULT = DRY-RUN (prints what would happen, changes nothing).
#   --check           dry-run that only REPORTS what exists (no "would move" list)
#   --yes             execute the uninstall
#   --rm              real delete instead of moving to the Trash (DB snapshot kept)
#   --purge-zips      also remove ~/Downloads/StudyFlow-*.zip release zips
#   --purge-backups   also remove ~/Downloads/studyflow_backup_*.zip + studyflow-backup-*.zip
#                     (requires typing PURGE on stdin as a second confirmation)
#   --keep-plists L   comma-separated LaunchAgents to KEEP (short names or full labels,
#                     case-insensitive): autodeploy, tts-server (=alvaro-tts-server),
#                     launcher, sharedmount, telegram-poll-bridge, opencode-sidecar.
#                     Default: none kept (all removed).
#
# NEVER touches: the repo, other vendors' plists (incl. com.alvaro.wip.plist),
# anything under ~/Library/Application Support other than .../studyflow,
# or the Señor's personal backup zips (unless --purge-backups + PURGE).
#
# Run by the Señor, never by an automated agent in destructive mode.
set -u

# ------------------------------------------------------------------ config
HOME_DIR="$HOME"
TS="$(date +%Y%m%d-%H%M%S)"
KEEP_DIR="$HOME_DIR/.Trash/studyflow-uninstall-$TS"
UID_NUM="$(id -u)"

LA_DIR="$HOME_DIR/Library/LaunchAgents"
LABELS=(
  "com.studyflow.autodeploy"
  "com.studyflow.alvaro-tts-server"
  "com.studyflow.launcher"
  "com.studyflow.opencode-sidecar"
  "com.studyflow.sharedmount"
  "com.studyflow.telegram-poll-bridge"
)

VALID_SHORT_NAMES="autodeploy, tts-server (alvaro-tts-server), launcher, sharedmount, telegram-poll-bridge, opencode-sidecar"
KEEP_LA=()   # full labels the user asked to KEEP (--keep-plists)
LA_KEPT=()   # kept labels, for the final summary

# Map a short name or full label (case-insensitive) to its full label. Non-zero if unknown.
resolve_label() {
  local tok
  tok="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "$tok" in
    autodeploy|com.studyflow.autodeploy) echo "com.studyflow.autodeploy" ;;
    tts-server|alvaro-tts-server|com.studyflow.alvaro-tts-server) echo "com.studyflow.alvaro-tts-server" ;;
    launcher|com.studyflow.launcher) echo "com.studyflow.launcher" ;;
    opencode-sidecar|com.studyflow.opencode-sidecar) echo "com.studyflow.opencode-sidecar" ;;
    sharedmount|com.studyflow.sharedmount) echo "com.studyflow.sharedmount" ;;
    telegram-poll-bridge|com.studyflow.telegram-poll-bridge) echo "com.studyflow.telegram-poll-bridge" ;;
    *) return 1 ;;
  esac
}

is_kept() {
  local l
  for l in ${KEEP_LA[@]+"${KEEP_LA[@]}"}; do [ "$l" = "$1" ] && return 0; done
  return 1
}

APP_COPIES=(
  "$HOME_DIR/Downloads/StudyFlow-macos"
  "$HOME_DIR/Downloads/StudyFlow-macos 2"
  "$HOME_DIR/Downloads/StudyFlow-macos 4"
)

BACKEND_DATA="$HOME_DIR/Library/Application Support/studyflow"
AUTODEPLOY_DIR="$HOME_DIR/.studyflow"

# ------------------------------------------------------------------ cli
MODE_CHECK=0
MODE_DO=0      # --yes
MODE_RM=0      # --rm
PURGE_ZIPS=0
PURGE_BACKUPS=0

usage() {
  sed -n '3,31p' "$0" | sed 's/^# \{0,1\}//'
}

KEEP_PLISTS_RAW=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check)         MODE_CHECK=1 ;;
    --yes)           MODE_DO=1 ;;
    --rm)            MODE_RM=1 ;;
    --purge-zips)    PURGE_ZIPS=1 ;;
    --purge-backups) PURGE_BACKUPS=1 ;;
    --keep-plists)
      if [ $# -lt 2 ]; then
        echo "ERROR: --keep-plists requires a comma-separated value." >&2
        echo "Valid names: $VALID_SHORT_NAMES (or full com.studyflow.* labels)." >&2
        exit 2
      fi
      shift
      KEEP_PLISTS_RAW="$1"
      ;;
    --keep-plists=*)
      KEEP_PLISTS_RAW="${1#--keep-plists=}"
      ;;
    -h|--help)       usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

if [ -n "$KEEP_PLISTS_RAW" ]; then
  _old_ifs="$IFS"; IFS=','
  for _tok in $KEEP_PLISTS_RAW; do
    _tok="$(printf '%s' "$_tok" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [ -z "$_tok" ] && continue
    if _lbl="$(resolve_label "$_tok")"; then
      KEEP_LA+=("$_lbl")
    else
      IFS="$_old_ifs"
      echo "ERROR: unknown value for --keep-plists: '$_tok'" >&2
      echo "Valid names: $VALID_SHORT_NAMES" >&2
      echo "Full labels : com.studyflow.autodeploy, com.studyflow.alvaro-tts-server, com.studyflow.launcher, com.studyflow.opencode-sidecar, com.studyflow.sharedmount, com.studyflow.telegram-poll-bridge" >&2
      exit 2
    fi
  done
  IFS="$_old_ifs"
fi

if [ "$MODE_CHECK" = 1 ] && [ "$MODE_DO" = 1 ]; then
  echo "ERROR: --check is read-only and cannot be combined with --yes." >&2
  exit 2
fi

DO_ACTION=0
[ "$MODE_DO" = 1 ] && [ "$MODE_CHECK" = 0 ] && DO_ACTION=1

# ------------------------------------------------------------------ helpers
REMOVED=()
KEPT=()
SKIPPED=()
TRASHED_DIRS=()

hr() { printf '%s\n' "------------------------------------------------------------"; }
sec() { printf '\n[%s]\n' "$1"; }

human() { [ -e "$1" ] && du -sh "$1" 2>/dev/null | awk '{print $1}' || echo "-"; }

# Total size (kB) of a list of paths, printed human-ish.
sum_size() {
  local total=0 p
  for p in "$@"; do
    [ -e "$p" ] || continue
    total=$(( total + $(du -sk "$p" 2>/dev/null | awk '{print $1}') ))
  done
  if [ "$total" -ge 1048576 ]; then
    awk -v k="$total" 'BEGIN{printf "%.1fG", k/1048576}'
  else
    awk -v k="$total" 'BEGIN{printf "%.1fM", k/1024}'
  fi
}

rel_to_home() { printf '%s' "${1#"$HOME_DIR"/}"; }

# Move (default) or delete (--rm) an existing path.
place() {
  local src="$1"
  local rel dest
  rel="$(rel_to_home "$src")"
  dest="$KEEP_DIR/$rel"
  if [ "$MODE_RM" = 1 ]; then
    rm -rf "$src"
    printf '   deleted: %s\n' "$src"
  else
    mkdir -p "$(dirname "$dest")"
    mv "$src" "$dest"
    printf '   trashed: %s  ->  %s\n' "$src" "$dest"
  fi
  REMOVED+=("$src")
}

# ------------------------------------------------------------------ check mode
run_check() {
  echo "StudyFlow macOS uninstall — CHECK (read-only)"
  hr

  local pids
  pids="$(lsof -ti tcp:8477 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    echo "Backend port 8477: LISTENING (pids: $(echo "$pids" | tr '\n' ' '))"
  else
    echo "Backend port 8477: not listening"
  fi

  local found=0 l f disp
  for l in "${LABELS[@]}"; do
    f="$LA_DIR/$l.plist"
    if is_kept "$l"; then disp="kept"; else disp="would remove"; fi
    if [ -f "$f" ]; then found=$((found+1)); echo "LaunchAgent present: $l  [$disp]"; else echo "LaunchAgent absent : $l  [$disp]"; fi
  done
  echo "LaunchAgents found: $found/${#LABELS[@]}"
  [ "${#KEEP_LA[@]}" -gt 0 ] && echo "Disposition: ${#KEEP_LA[@]} kept via --keep-plists, $(( ${#LABELS[@]} - ${#KEEP_LA[@]} )) would be removed" \
    || echo "Disposition: none kept (all ${#LABELS[@]} would be removed; use --keep-plists to keep some)"

  local n=0 total=0
  for f in "${APP_COPIES[@]}"; do
    if [ -e "$f" ]; then n=$((n+1)); echo "App copy present: $f ($(human "$f"))"; fi
  done
  [ "$n" = 0 ] && echo "App copies: none present"

  if [ -d "$BACKEND_DATA" ]; then
    local dbc bkc
    dbc="$(find "$BACKEND_DATA" -maxdepth 1 -name 'studyflow.db' 2>/dev/null | wc -l | tr -d ' ')"
    bkc="$(find "$BACKEND_DATA" -maxdepth 1 -name 'studyflow.db.backup-*' 2>/dev/null | wc -l | tr -d ' ')"
    echo "Backend data: present ($(human "$BACKEND_DATA")) | studyflow.db: $dbc | backups: $bkc"
  else
    echo "Backend data: absent"
  fi

  [ -d "$AUTODEPLOY_DIR" ] && echo "~/.studyflow: present ($(human "$AUTODEPLOY_DIR"))" || echo "~/.studyflow: absent"

  local zb=0 zs=0 zf
  shopt -s nullglob
  for zf in "$HOME_DIR"/Downloads/studyflow_backup_*.zip "$HOME_DIR"/Downloads/studyflow-backup-*.zip; do zb=$((zb+1)); done
  for zf in "$HOME_DIR"/Downloads/StudyFlow-*.zip; do zs=$((zs+1)); done
  shopt -u nullglob
  echo "Protected personal backups: $zb zip(s)"
  echo "Protected release zips    : $zs zip(s)"

  [ -f "$LA_DIR/com.alvaro.wip.plist" ] \
    && echo "com.alvaro.wip.plist: present (NOT touched by this script)" \
    || echo "com.alvaro.wip.plist: absent"

  hr
  echo "Nothing was changed. Run without --check to see the full dry-run plan."
}

# ------------------------------------------------------------------ stop backend
stop_backend() {
  sec "1) STOP BACKEND / PROCESSES"
  local any=0 pids pid cmd matched

  pids="$(lsof -ti tcp:8477 2>/dev/null || true)"
  if [ -n "$pids" ]; then
    any=1
    for pid in $pids; do
      cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      if [ "$DO_ACTION" = 1 ]; then
        kill -TERM "$pid" 2>/dev/null || true
        sleep 0.3
        kill -KILL "$pid" 2>/dev/null || true
        printf '   stopped port 8477: pid %s (%s)\n' "$pid" "$cmd"
      else
        printf '   would stop port 8477: pid %s (%s)\n' "$pid" "$cmd"
      fi
      REMOVED+=("pid:$pid")
    done
  else
    echo "   no listener on tcp:8477"
  fi

  # Kill only processes whose command line contains one of the ABSOLUTE app paths.
  while read -r pid cmd; do
    [ -z "${pid:-}" ] && continue
    [ "$pid" = "$$" ] && continue
    matched=0
    for ap in "${APP_COPIES[@]}"; do
      case "$cmd" in
        *"$ap"*) matched=1 ;;
      esac
    done
    [ "$matched" = 1 ] || continue
    any=1
    if [ "$DO_ACTION" = 1 ]; then
      kill -TERM "$pid" 2>/dev/null || true
      sleep 0.3
      kill -KILL "$pid" 2>/dev/null || true
      printf '   stopped app process: pid %s (%s)\n' "$pid" "$cmd"
    else
      printf '   would stop app process: pid %s (%s)\n' "$pid" "$cmd"
    fi
  done < <(ps -axo pid=,command=)

  [ "$any" = 0 ] && echo "   nothing running from StudyFlow app folders"
  SKIPPED+=("no unrelated processes were matched by generic name patterns")
}

# ------------------------------------------------------------------ launchagents
do_launchagents() {
  sec "2) HELPER LAUNCHAGENTS"
  local l plist
  for l in "${LABELS[@]}"; do
    plist="$LA_DIR/$l.plist"
    if is_kept "$l"; then
      echo "   $l: kept (user decision) — plist NOT moved"
      LA_KEPT+=("$l")
      continue
    fi
    if [ ! -f "$plist" ]; then
      echo "   $l: skipped (not found)"
      SKIPPED+=("LaunchAgent $l (not found)")
      continue
    fi
    if [ "$DO_ACTION" = 1 ]; then
      launchctl bootout "gui/$UID_NUM/$l" >/dev/null 2>&1 || true
      echo "   $l: bootout requested (tolerating 'not loaded')"
      place "$plist"
    else
      echo "   $l: found -> would bootout + move plist"
    fi
  done
  echo "   (other vendors' plists, incl. com.alvaro.wip.plist, are NOT touched)"
}

# ------------------------------------------------------------------ app copies
do_app_copies() {
  sec "3) APP COPIES IN ~/Downloads"
  local ap
  for ap in "${APP_COPIES[@]}"; do
    if [ -e "$ap" ]; then
      if [ "$DO_ACTION" = 1 ]; then
        printf '   %s (%s): %s\n' "$ap" "$(human "$ap")" "$([ "$MODE_RM" = 1 ] && echo deleted || echo trashed)"
        place "$ap"
      else
        printf '   would %s: %s (%s)\n' "$([ "$MODE_RM" = 1 ] && echo delete || echo trash)" "$ap" "$(human "$ap")"
      fi
    else
      echo "   skipped (not found): $ap"
    fi
  done
}

# ------------------------------------------------------------------ backend data
snapshot_db() {
  local src="$BACKEND_DATA/studyflow.db"
  local dst_dir="$KEEP_DIR/db-snapshot"
  local base="studyflow.db.snapshot-$TS"
  [ -f "$src" ] || return 0
  if [ "$DO_ACTION" = 1 ]; then
    mkdir -p "$dst_dir"
    cp -p "$src" "$dst_dir/$base"
    [ -f "$src-wal" ] && cp -p "$src-wal" "$dst_dir/$base-wal"
    [ -f "$src-shm" ] && cp -p "$src-shm" "$dst_dir/$base-shm"
    echo "   DB snapshot saved: $dst_dir/$base (WAL-safe: db+wal+shm together)"
  else
    echo "   would save DB snapshot (WAL-safe) to: $dst_dir/$base"
    [ -f "$src-wal" ] && echo "     (a -wal exists -> db+wal+shm copied together)"
  fi
}

do_backend_data() {
  sec "4) BACKEND DATA"
  if [ -d "$BACKEND_DATA" ]; then
    echo "   $BACKEND_DATA ($(human "$BACKEND_DATA"))"
    snapshot_db
    if [ "$DO_ACTION" = 1 ]; then
      place "$BACKEND_DATA"
    else
      printf '   would %s: %s\n' "$([ "$MODE_RM" = 1 ] && echo delete || echo trash)" "$BACKEND_DATA"
    fi
  else
    echo "   skipped (not found): $BACKEND_DATA"
  fi
}

# ------------------------------------------------------------------ ~/.studyflow
do_autodeploy_dir() {
  sec "5) ~/.studyflow"
  if [ -d "$AUTODEPLOY_DIR" ]; then
    if [ "$DO_ACTION" = 1 ]; then
      printf '   %s (%s): %s\n' "$AUTODEPLOY_DIR" "$(human "$AUTODEPLOY_DIR")" "$([ "$MODE_RM" = 1 ] && echo deleted || echo trashed)"
      place "$AUTODEPLOY_DIR"
    else
      printf '   would %s: %s (%s)\n' "$([ "$MODE_RM" = 1 ] && echo delete || echo trash)" "$AUTODEPLOY_DIR" "$(human "$AUTODEPLOY_DIR")"
    fi
  else
    echo "   skipped (not found): $AUTODEPLOY_DIR"
  fi
}

# ------------------------------------------------------------------ protected
do_protected() {
  sec "6) PROTECTED DATA (must NOT be touched unless purged explicitly)"
  local f n size

  n=0; size="0"
  shopt -s nullglob
  for f in "$HOME_DIR"/Downloads/studyflow_backup_*.zip "$HOME_DIR"/Downloads/studyflow-backup-*.zip; do
    n=$((n+1)); KEPT+=("$f")
  done
  [ "$n" -gt 0 ] && size="$(sum_size "$HOME_DIR"/Downloads/studyflow_backup_*.zip "$HOME_DIR"/Downloads/studyflow-backup-*.zip)"
  echo "   personal backups: $n zip(s) (~$size)"
  if [ "$PURGE_BACKUPS" = 1 ]; then
    echo "   --purge-backups ENABLED: these WILL be removed (requires typing PURGE on stdin)"
    if [ "$DO_ACTION" = 1 ]; then
      printf 'Type PURGE to confirm deleting the personal backup zips: '
      local answer=""
      read -r answer || true
      if [ "$answer" = "PURGE" ]; then
        for f in "$HOME_DIR"/Downloads/studyflow_backup_*.zip "$HOME_DIR"/Downloads/studyflow-backup-*.zip; do
          place "$f"
        done
      else
        echo "   confirmation failed -> personal backups KEPT"
      fi
    else
      echo "   (dry-run: a real run would prompt for PURGE before deleting)"
    fi
  else
    echo "   -> KEPT (use --purge-backups + type PURGE to delete)"
  fi

  n=0
  for f in "$HOME_DIR"/Downloads/StudyFlow-*.zip; do
    n=$((n+1)); KEPT+=("$f")
  done
  echo "   release zips (StudyFlow-*.zip): $n file(s)"
  if [ "$PURGE_ZIPS" = 1 ]; then
    echo "   --purge-zips ENABLED"
    if [ "$DO_ACTION" = 1 ]; then
      for f in "$HOME_DIR"/Downloads/StudyFlow-*.zip; do place "$f"; done
    else
      echo "   (dry-run: a real run would remove them)"
    fi
  else
    echo "   -> KEPT (use --purge-zips to delete)"
  fi
  shopt -u nullglob

  echo "   repo, other LaunchAgents, other Application Support folders: NOT touched"
}

# ------------------------------------------------------------------ verify
verify_protected() {
  sec "VERIFY — protected data still present (must-not-touch check)"
  local f n=0
  shopt -s nullglob
  for f in "$HOME_DIR"/Downloads/studyflow_backup_*.zip "$HOME_DIR"/Downloads/studyflow-backup-*.zip; do n=$((n+1)); done
  echo "   personal backup zips present: $n"
  n=0
  for f in "$HOME_DIR"/Downloads/StudyFlow-*.zip; do n=$((n+1)); done
  echo "   release zips present: $n"
  shopt -u nullglob
  if [ "$DO_ACTION" != 1 ]; then
    [ -f "$BACKEND_DATA/studyflow.db" ] && echo "   studyflow.db: intact (dry-run, expected)" || echo "   studyflow.db: not present"
  elif [ -f "$BACKEND_DATA/studyflow.db" ]; then
    echo "   WARNING: studyflow.db still present at $BACKEND_DATA"
  else
    echo "   studyflow.db: removed from original location (snapshot kept in SAFE-KEEP)"
  fi
  for l in "${LABELS[@]}"; do
    [ -f "$LA_DIR/$l.plist" ] && echo "   plist still present: $l.plist" || true
  done
  [ -f "$LA_DIR/com.alvaro.wip.plist" ] && echo "   com.alvaro.wip.plist: present (untouched)"
}

# ------------------------------------------------------------------ final
final_summary() {
  local suffix=""
  [ "$MODE_RM" = 1 ] && suffix=" (REAL DELETE mode)"
  hr
  if [ "$MODE_DO" = 1 ]; then
    echo "SUMMARY — executed$suffix"
  else
    echo "SUMMARY — DRY-RUN (nothing was changed)"
  fi
  echo "Removed : ${#REMOVED[@]} item(s)"
  echo "Kept    : ${#KEPT[@]} protected item(s)"
  printf 'LaunchAgents kept:\n'
  if [ "${#LA_KEPT[@]}" -gt 0 ]; then
    local _l
    for _l in "${LA_KEPT[@]}"; do echo "   - $_l (kept by user decision)"; done
  else
    echo "   - (none — all targeted LaunchAgents were removed)"
  fi
  echo "SAFE-KEEP / trash folder: $KEEP_DIR"
  if [ "$MODE_RM" = 1 ] && [ "$MODE_DO" = 1 ]; then
    echo "Recover : DB snapshot kept in $KEEP_DIR/db-snapshot/"
  else
    echo "Recover : mv \"$KEEP_DIR/<relative-path>\" \"$HOME_DIR/<relative-path>\"   (paths are preserved)"
  fi
  echo
  echo "To remove the installed PWA (a script cannot do this), if StudyFlow appears in Chrome:"
  echo "  1. Open Chrome and go to  chrome://apps"
  echo "  2. Right-click the StudyFlow icon"
  echo "  3. Choose 'Uninstall' / 'Remove from Chrome'"
  hr
}

# ------------------------------------------------------------------ main
main() {
  if [ "$MODE_CHECK" = 1 ]; then
    run_check
    exit 0
  fi

  echo "StudyFlow macOS uninstaller"
  echo "Mode: $([ "$DO_ACTION" = 1 ] && echo "EXECUTE$([ "$MODE_RM" = 1 ] && echo ' + --rm (real delete)')" || echo 'DRY-RUN (use --yes to execute)')"
  echo "SAFE-KEEP folder: $KEEP_DIR"
  hr

  stop_backend
  do_launchagents
  do_app_copies
  do_backend_data
  do_autodeploy_dir
  do_protected
  verify_protected
  final_summary
}

main
