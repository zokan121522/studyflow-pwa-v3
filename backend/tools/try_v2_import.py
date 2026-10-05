#!/usr/bin/env python3
"""Run a v2 backup import against a throwaway SQLite database (issue #8 dev tool).

    python3 backend/tools/try_v2_import.py [zip_path]

Throws away the database afterwards, so it cannot touch a real install.
The connection comes from ``database.get_connection()``, which means it
follows the configured engine: SQLite by default, so no server and no
DATABASE_URL is needed to exercise the import path.
"""

import os
import shutil
import sys
import tempfile
import time
import zipfile

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

import database  # noqa: E402
import engine  # noqa: E402
import v2_import  # noqa: E402
import v2_parser  # noqa: E402

DEFAULT_ZIP = os.path.expanduser("~/Downloads/studyflow_backup_2026-09-29.zip")
TEST_USER = "v2import@test.local"


def main() -> int:
    zip_path = next((a for a in sys.argv[1:] if not a.startswith("--")),
                    DEFAULT_ZIP)
    if not os.path.exists(zip_path):
        print(f"no existe el backup: {zip_path}", file=sys.stderr)
        return 2

    # An isolated data dir: a dev tool that imports in bulk should not be
    # one env var away from the user's actual courses.
    scratch = tempfile.mkdtemp(prefix="studyflow-v2import-")
    os.environ[engine.DATA_DIR_ENV] = scratch
    os.environ.pop("DATABASE_URL", None)
    os.environ[engine.ENGINE_ENV] = engine.ENGINE_SQLITE

    try:
        database.init_db()
        print(f"  motor: {engine.describe()}")
        conn = database.get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE email = %s", (TEST_USER,))
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (email, password_hash) "
                        "VALUES (%s, 'x') RETURNING id", (TEST_USER,))
            user_id = cur.fetchone()["id"]
        conn.commit()

        t0 = time.time()
        with zipfile.ZipFile(zip_path) as zf:
            tables = v2_parser.parse_copy_sections(
                zf.read("user_data.sql").decode("utf-8", "replace"))
            print(f"parsed {sum(len(v) for v in tables.values() if isinstance(v, list))}"
                  f" rows in {time.time() - t0:.1f}s")
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
    finally:
        database.close_pool()
        shutil.rmtree(scratch, ignore_errors=True)

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
