#!/usr/bin/env python3
"""Verify returns + cancellations data in DB: row counts, JSONB shape, status distribution."""

import sys

import psycopg

from scripts._db_url import normalize_db_url

# Load .env for DB URL
env_path = "/home/schan/tts-erp/.env"
DB_URL = ""
try:
    with open(env_path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln.startswith("TTS_ERP_DB_URL="):
                DB_URL = ln.split("=", 1)[1]
                break
except OSError as e:
    sys.exit(f"cannot read {env_path}: {e}")

if not DB_URL:
    sys.exit("TTS_ERP_DB_URL not set in .env")

# .env stores the URL in SQLAlchemy form (postgresql+psycopg://...).
# Raw psycopg.connect only accepts the plain postgresql:// scheme.
DB_URL = normalize_db_url(DB_URL)

SHOP = "7494763368967603447"

with psycopg.connect(DB_URL) as c, c.cursor() as cur:
    # Total counts
    cur.execute("SELECT count(*) FROM returns WHERE shop_id=%s", (SHOP,))
    row = cur.fetchone()
    print(f"returns: {row[0] if row else 0} rows for shop")
    cur.execute("SELECT count(*) FROM cancellations WHERE shop_id=%s", (SHOP,))
    row = cur.fetchone()
    print(f"cancellations: {row[0] if row else 0} rows for shop")

    # Status distribution
    print("\nreturns by status:")
    cur.execute(
        "SELECT return_status, count(*) FROM returns WHERE shop_id=%s GROUP BY return_status ORDER BY 2 DESC",
        (SHOP,),
    )
    for r in cur.fetchall():
        print(f"  {r[0]:40s}  {r[1]}")
    print("\ncancellations by status:")
    cur.execute(
        "SELECT cancel_status, count(*) FROM cancellations WHERE shop_id=%s GROUP BY cancel_status ORDER BY 2 DESC",
        (SHOP,),
    )
    for r in cur.fetchall():
        print(f"  {r[0]:50s}  {r[1]}")

    # JSONB shape (returns)
    print("\nreturns JSONB fields (sample):")
    cur.execute(
        """
        SELECT return_id,
               jsonb_array_length(raw->'return_line_items') AS line_count,
               raw->>'is_combined_return' AS combined,
               raw->>'handover_method' AS handover,
               raw->>'is_quick_refund' AS quick_refund,
               raw->'refund_amount'->>'refund_total' AS refund_total,
               raw->'refund_amount'->>'currency' AS currency
        FROM returns WHERE shop_id=%s ORDER BY create_time DESC LIMIT 3
    """,
        (SHOP,),
    )
    for r in cur.fetchall():
        print(
            f"  {r[0]}  line_items={r[1]} combined={r[2]} handover={r[3]} quick_refund={r[4]} refund_total={r[5]} {r[6]}"
        )

    # JSONB shape (cancellations)
    print("\ncancellations JSONB fields (sample):")
    cur.execute(
        """
        SELECT cancel_id,
               jsonb_array_length(raw->'cancel_line_items') AS line_count,
               raw->>'cancel_type' AS ctype,
               raw->>'role' AS role
        FROM cancellations WHERE shop_id=%s ORDER BY create_time DESC LIMIT 3
    """,
        (SHOP,),
    )
    for r in cur.fetchall():
        print(f"  {r[0]}  line_items={r[1]} type={r[2]} role={r[3]}")

    # sync_log
    print("\nlast 5 syncs:")
    cur.execute(
        "SELECT sync_type, status, rows_affected, started_at, error_message FROM sync_log WHERE shop_id=%s ORDER BY id DESC LIMIT 5",
        (SHOP,),
    )
    for r in cur.fetchall():
        print(
            f"  {r[0]:20s}  {r[1]:6s}  rows={r[2]:>4}  at={r[3].isoformat() if r[3] else '-'}  err={r[4] or '-'}"
        )
