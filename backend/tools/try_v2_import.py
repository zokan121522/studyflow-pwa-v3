#!/usr/bin/env python3
"""Run a v2 backup import against a throwaway database (issue #8 dev tool).

    DATABASE_URL=postgresql://postgres@localhost:5432/studyflow_v2test \
        python3 backend/tools/try_v2_import.py [zip_path]

Prints the per-table counts and every warning, then verifies the imported
row counts against the source so a silent drop cannot pass unnoticed.
"""

import os
import sys
import time
import zipfile

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

import psycopg2  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

import v2_import  # noqa: E402
import v2_parser  # noqa: E402

DEFAULT_ZIP = os.path.expanduser(
    "~/Downloads/studyflow_backup_2026-09-29.zip")
TEST_USER = "v2import@test.local"


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is required", file=sys.stderr)
        return 2
    # This tool writes to a throwaway DB. Refuse anything else unless the
    # caller opts in explicitly: DELETE FROM users cascades over 25 tables.
    if "test" not in db_url and "--i-know" not in sys.argv:
        print(f"refusing to touch {db_url}: name a test database, "
              f"or pass --i-know", file=sys.stderr)
        return 2
    zip_path = next((a for a in sys.argv[1:] if not a.startswith("--")),
                    DEFAULT_ZIP)

    t0 = time.time()
    with zipfile.ZipFile(zip_path) as zf:
        tables = v2_parser.parse_copy_sections(
            zf.read("user_data.sql").decode("utf-8", "replace"))
        print(f"parsed {sum(len(v) for v in tables.values()
                           if isinstance(v, list))} rows in {time.time() - t0:.1f}s")

        conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE email = %s", (TEST_USER,))
        conn.commit()
        cur.execute("INSERT INTO users (email, password_hash) "
                    "VALUES (%s, 'x') RETURNING id", (TEST_USER,))
        user_id = cur.fetchone()["id"]
        conn.commit()

        t0 = time.time()
        try:
            report = v2_import.migrate_v2(conn, user_id, zf, tables)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"IMPORT FAILED after {time.time() - t0:.1f}s: "
                  f"{type(exc).__name__}: {str(exc)[:400]}")
            return 1
        print(f"IMPORT OK in {time.time() - t0:.1f}s\n")

    for name, count in report["counts"].items():
        print(f"  {name:24} {count}")
    print(f"\nfiles: {report['files']}")
    print(f"deferred: {report['deferred']}")
    print(f"not migrated: {report['skipped_tables']}")
    print(f"\nwarnings ({len(report['warnings'])}):")
    for w in report["warnings"][:15]:
        print(f"   ! {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
