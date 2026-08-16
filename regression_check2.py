#!/usr/bin/env python3
"""Check actual row counts (no limit) + persistent sync_log."""
import psycopg, os
env = {}
for ln in open("/home/schan/tts-erp/.env"):
    ln = ln.strip()
    if "=" in ln and not ln.startswith("#"):
        k, v = ln.split("=", 1)
        env[k.strip()] = v.strip()
DB = env["TTS_ERP_DB_URL"]
SHOP = "7494763368967603447"

with psycopg.connect(DB) as c, c.cursor() as cur:
    print("=== Real row counts (no limit) ===")
    for tbl in ("orders", "statements", "payments", "returns", "cancellations"):
        cur.execute(f"SELECT count(*) FROM {tbl} WHERE shop_id=%s", (SHOP,))
        print(f"  {tbl:15s}  {cur.fetchone()[0]:>4} rows")
    print("\n=== PG sync_log (persistent, all-time) ===")
    cur.execute("""
        SELECT sync_type, status, rows_affected, started_at AT TIME ZONE 'UTC' as at
        FROM sync_log WHERE shop_id=%s ORDER BY id DESC LIMIT 10
    """, (SHOP,))
    for r in cur.fetchall():
        print(f"  {r[0]:20s}  {r[1]:6s}  rows={r[2]:>4}  at={r[3].isoformat()}")
