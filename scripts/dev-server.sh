#!/usr/bin/env bash
# Start the StudyFlow PWA v3 dev server with every required environment
# variable set. Replaces ad-hoc `python3 -m backend.server` invocations,
# which failed with ModuleNotFoundError unless DATABASE_URL and the
# PYTHONPATH happened to be exported by hand.
#
# The portable SQLite database is the single source of truth. This script
# forces the SQLite engine below after sourcing .env, so a stray
# DATABASE_URL cannot silently repoint the dev server at Postgres, which
# is legacy and kept only for the docker deployment.
#
# Usage:  ./scripts/dev-server.sh          (foreground, Ctrl-C to stop)
#         PORT=9000 ./scripts/dev-server.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Load .env if present (never committed).
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# ── Engine: portable SQLite is the single source of truth ──────────────
# Forced AFTER sourcing .env (and before the defaults) so a DATABASE_URL
# in .env cannot repoint the dev server at the diverged Postgres instance.
# Postgres is legacy, kept only for the docker deployment.
export STUDYFLOW_DB_ENGINE=sqlite
export STUDYFLOW_DATA_DIR="$HOME/Library/Application Support/studyflow"
export DATABASE_URL=""

# ── Defaults ───────────────────────────────────────────────────────────
export FLASK_RUN_PORT="${FLASK_RUN_PORT:-8082}"
export PDF_UPLOAD_FOLDER="${PDF_UPLOAD_FOLDER:-$REPO_ROOT/uploads/pdfs}"
export SCRAPING_ENABLED="${SCRAPING_ENABLED:-1}"
export MAX_FILE_SIZE="${MAX_FILE_SIZE:-52428800}"
export SCRAPED_DIR="${SCRAPED_DIR:-/tmp/scraped_pdfs}"
# SECRET_KEY protects both sessions and the encrypted SCORM credentials.
export SECRET_KEY="${SECRET_KEY:-dev-secret-change-me}"

mkdir -p "$PDF_UPLOAD_FOLDER" "$SCRAPED_DIR"

# ── Preflight: fail loudly here instead of on a confusing traceback ────
if ! python3 -c "import sqlite3" >/dev/null 2>&1; then
  echo "error: Python has no sqlite3 module ($(command -v python3))" >&2
  exit 1
fi

echo "▶ StudyFlow PWA v3 — http://localhost:${FLASK_RUN_PORT}/"
echo "  DB_ENGINE         = ${STUDYFLOW_DB_ENGINE}  (portable SQLite — single source of truth)"
echo "  DB_FILE           = ${STUDYFLOW_DATA_DIR}/studyflow.db"
echo "  DATABASE_URL      = (empty — Postgres is legacy, docker-only)"
echo "  PDF_UPLOAD_FOLDER = $PDF_UPLOAD_FOLDER"
echo "  SCRAPING_ENABLED  = $SCRAPING_ENABLED"
echo "  SCRAPED_DIR       = $SCRAPED_DIR"
echo

# backend/ goes first so the flat imports in server.py resolve.
export PYTHONPATH="$REPO_ROOT/backend:$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -m backend.server
