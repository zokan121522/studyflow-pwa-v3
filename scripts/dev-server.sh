#!/usr/bin/env bash
# Start the StudyFlow PWA v3 dev server with every required environment
# variable set. Replaces ad-hoc `python3 -m backend.server` invocations,
# which failed with ModuleNotFoundError unless DATABASE_URL and the
# PYTHONPATH happened to be exported by hand.
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

# ── Defaults ───────────────────────────────────────────────────────────
export FLASK_RUN_PORT="${FLASK_RUN_PORT:-8082}"
export DATABASE_URL="${DATABASE_URL:-postgresql://postgres@localhost:5432/studyflow}"
export PDF_UPLOAD_FOLDER="${PDF_UPLOAD_FOLDER:-$REPO_ROOT/uploads/pdfs}"
export SCRAPING_ENABLED="${SCRAPING_ENABLED:-1}"
export MAX_FILE_SIZE="${MAX_FILE_SIZE:-52428800}"
export SCRAPED_DIR="${SCRAPED_DIR:-/tmp/scraped_pdfs}"
# SECRET_KEY protects both sessions and the encrypted SCORM credentials.
export SECRET_KEY="${SECRET_KEY:-dev-secret-change-me}"

mkdir -p "$PDF_UPLOAD_FOLDER" "$SCRAPED_DIR"

# ── Preflight: fail loudly here instead of on a confusing traceback ────
if ! python3 -c "import psycopg2" >/dev/null 2>&1; then
  echo "error: psycopg2 is not installed for $(command -v python3)" >&2
  exit 1
fi

echo "▶ StudyFlow PWA v3 — http://localhost:${FLASK_RUN_PORT}/"
echo "  DATABASE_URL      = ${DATABASE_URL%%:*}://…@$(echo "$DATABASE_URL" | sed -E 's#.*@([^/]*)/.*#\1#')/$(basename "${DATABASE_URL%%\?*}")"
echo "  PDF_UPLOAD_FOLDER = $PDF_UPLOAD_FOLDER"
echo "  SCRAPING_ENABLED  = $SCRAPING_ENABLED"
echo "  SCRAPED_DIR       = $SCRAPED_DIR"
echo

# backend/ goes first so the flat imports in server.py resolve.
export PYTHONPATH="$REPO_ROOT/backend:$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -m backend.server
