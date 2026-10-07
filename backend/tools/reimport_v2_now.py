#!/usr/bin/env python3
r"""Reimport a v2 backup into an existing v3 user.

Unlike tools/try_v2_import.py this does not create a throwaway user: it
targets the user already in the database, so the restored courses carry
their real favourites, icons and order.

    STUDYFLOW_DATA_DIR=~/Library/Application\ Support/studyflow \
        python3 backend/tools/reimport_v2_now.py /path/to/backup.zip

The connection comes from ``database.get_connection()`` so this follows the
configured engine (SQLite for a local install) instead of reaching for
psycopg2 and a DSN. That matters: under local-first there is no server to
write to, and a DSN left over in someone's shell used to be enough to send
a bulk import at the old Postgres.
"""
import os
import sys
import time
import zipfile

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

import database  # noqa: E402
import engine  # noqa: E402
import v2_import  # noqa: E402
import v2_parser  # noqa: E402

DEFAULT_ZIP = "/tmp/v2backup.zip"


def _pick_user(conn) -> int:
    """Return the id of the single user to import into.

    Refuses to guess. A local install can legitimately accumulate more than
    one account, and a bulk import aimed at the wrong one is not something
    to discover afterwards.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id, email FROM users ORDER BY id")
        users = cur.fetchall()
    if not users:
        raise SystemExit("no hay usuarios en la base de datos; nada que reimportar")
    if len(users) > 1:
        listing = ", ".join(f"{u['id']}={u['email']}" for u in users)
        raise SystemExit(
            f"hay {len(users)} usuarios ({listing}).\n"
            "usa backup_restore o indica el id a mano: esto importa en bloque."
        )
    uid = users[0]["id"]
    print(f"  usuario destino: id={uid} {users[0]['email']}")
    return uid


def main() -> int:
    zip_path = next((a for a in sys.argv[1:] if not a.startswith("--")),
                    DEFAULT_ZIP)
    if not os.path.exists(zip_path):
        raise SystemExit(f"no existe el backup: {zip_path}")

    database.init_db()
    print(f"  motor: {engine.describe()}")
    if engine.active_engine() == engine.ENGINE_POSTGRES:
        print("  AVISO: importando en el Postgres del servidor, no en local.",
              file=sys.stderr)
    conn = database.get_connection()
    uid = _pick_user(conn)

    t0 = time.time()
    with zipfile.ZipFile(zip_path) as zf:
        tables = v2_parser.parse_copy_sections(
            zf.read("user_data.sql").decode("utf-8", "replace"))
        print(f"  parseadas {sum(len(v) for v in tables.values() if isinstance(v, list))}"
              f" filas en {time.time() - t0:.1f}s")
        t0 = time.time()
        try:
            report = v2_import.migrate_v2(conn, uid, zf, tables)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"  IMPORT FALLIDO tras {time.time() - t0:.1f}s: "
                  f"{type(exc).__name__}: {str(exc)[:400]}")
            return 1
        print(f"  IMPORT OK en {time.time() - t0:.1f}s\n")

    for name, count in report["counts"].items():
        print(f"    {name:24} {count}")
    print(f"\n  ficheros: {report['files']}")
    print(f"  diferidos: {report['deferred']}")
    print(f"  no migradas: {report['skipped_tables']}")
    print(f"\n  avisos ({len(report['warnings'])}):")
    for w in report["warnings"][:15]:
        print(f"    ! {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
