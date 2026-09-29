#!/usr/bin/env python3
"""Reimport the v2 backup into the EXISTING v3 user (post-migration).

Unlike tools/try_v2_import.py this does not create a throwaway user: it
targets the single real user already in the database, so the restored
courses carry their real favourites, icons and order.
"""
import os, sys, time, zipfile
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
import psycopg2
from psycopg2.extras import RealDictCursor
import v2_import, v2_parser

ZIP = "/tmp/v2backup.zip"
conn = psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=RealDictCursor)
cur = conn.cursor()
cur.execute("SELECT id, email FROM users ORDER BY id")
users = cur.fetchall()
assert len(users) == 1, f"esperaba 1 usuario, hay {len(users)}"
uid, email = users[0]["id"], users[0]["email"]
print(f"  usuario destino: id={uid} {email}")

t0 = time.time()
with zipfile.ZipFile(ZIP) as zf:
    tables = v2_parser.parse_copy_sections(zf.read("user_data.sql").decode("utf-8", "replace"))
    print(f"  parseadas {sum(len(v) for v in tables.values() if isinstance(v, list))} filas en {time.time()-t0:.1f}s")
    t0 = time.time()
    report = v2_import.migrate_v2(conn, uid, zf, tables)
    conn.commit()
    print(f"  IMPORT OK en {time.time()-t0:.1f}s\n")

for name, count in report["counts"].items():
    print(f"    {name:24} {count}")
print(f"\n  ficheros: {report['files']}")
print(f"  diferidos: {report['deferred']}")
print(f"  no migradas: {report['skipped_tables']}")
print(f"\n  avisos ({len(report['warnings'])}):")
for w in report["warnings"][:15]:
    print(f"    ! {w}")
