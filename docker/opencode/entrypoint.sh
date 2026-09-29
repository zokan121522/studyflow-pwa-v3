#!/bin/bash
# ─── opencode Custom Entrypoint ─────────────────────────────────────────────
# Patches SQLite schema to remove FOREIGN KEY constraints (opencode 1.17.9 bug)
# ──────────────────────────────────────────────────────────────────────────────

DB_PATH="/root/.local/share/opencode/opencode.db"
FLAG="/tmp/opencode_migrated"

# Install dependencies
apt-get update -qq && apt-get install -y -qq sqlite3 python3 2>/dev/null

# ── Migration (runs once) ──────────────────────────────────────────────────
if [ ! -f "$FLAG" ]; then
    echo "→ Phase 1: Boot opencode to initialize DB schema..."

    # Start briefly to let opencode create its schema
    opencode serve --port 54321 --hostname 0.0.0.0 &
    OPID=$!

    # Wait up to 20s for DB creation
    for i in $(seq 1 20); do
        if [ -f "$DB_PATH" ]; then break; fi
        sleep 1
    done

    # Stop opencode so we can safely modify the DB
    kill $OPID 2>/dev/null
    wait $OPID 2>/dev/null
    sleep 2  # Wait for WAL flush

    # Check for FK constraints
    FK_COUNT=$(sqlite3 "$DB_PATH" \
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND sql LIKE '%REFERENCES%' AND name NOT LIKE '%new' AND name NOT LIKE 'sqlite_%';" \
        2>/dev/null || echo "0")

    if [ "$FK_COUNT" -gt "0" ]; then
        echo "→ Found ${FK_COUNT} table(s) with FK constraints. Migrating..."

        python3 <<'PYEOF'
import sqlite3, re, os, sys

db_path = "/root/.local/share/opencode/opencode.db"
backup_path = db_path + ".backup"
tmp_path = db_path + ".tmp"

try:
    # Dump
    src = sqlite3.connect(db_path)
    lines = list(src.iterdump())
    src.close()
    sql = "\n".join(lines)

    # Strip FK constraints
    sql = re.sub(
        r',\s*CONSTRAINT\s+\S+\s+FOREIGN KEY\s*\([^)]+\)\s*REFERENCES\s+\S+\s*\([^)]+\)(\s+ON\s+(DELETE|UPDATE)\s+\w+(\s+ON\s+(DELETE|UPDATE)\s+\w+)?)*',
        "",
        sql,
        flags=re.DOTALL
    )
    # Fix trailing commas
    sql = re.sub(r",\s*\n\s*\)", "\n)", sql)

    # Backup & rebuild
    os.rename(db_path, backup_path)
    dst = sqlite3.connect(tmp_path)
    dst.executescript(sql)
    dst.commit()
    dst.close()

    # Swap
    os.rename(tmp_path, db_path)

    # Cleanup WAL/SHM from backup
    for ext in ("-wal", "-shm"):
        p = backup_path + ext
        if os.path.exists(p):
            os.remove(p)

    print("→ Migration OK. FK constraints removed.")
except Exception as e:
    print(f"→ Migration FAILED: {e}. Restoring backup.")
    if os.path.exists(db_path):
        os.remove(db_path)
    os.rename(backup_path, db_path)
    sys.exit(0)  # Don't block container - opencode will work with FK constraints
PYEOF

    else:
        echo "→ No FK constraints to remove."
    fi

    touch "$FLAG"
fi

# ── Phase 2: Run opencode in foreground ────────────────────────────────────
echo "→ Phase 2: Starting opencode..."
exec opencode serve --port 54321 --hostname 0.0.0.0
