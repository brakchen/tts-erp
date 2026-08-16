#!/usr/bin/env python3
"""Run demo_queries.sql against tts_erp and report row counts per statement.
We split on lines starting with '-- ---' (section markers) and run each section
individually to avoid one section's error blocking later ones.
"""
import os
import re
import sys

import psycopg

# Load .env
env_path = "/home/schan/tts-erp/.env"
env = {}
for ln in open(env_path):
    ln = ln.strip()
    if "=" in ln and not ln.startswith("#"):
        k, v = ln.split("=", 1)
        env[k.strip()] = v.strip()
DB_URL = env["TTS_ERP_DB_URL"]

sql_path = sys.argv[1] if len(sys.argv) > 1 else "/home/schan/tts-erp/demo_queries.sql"
print(f"=== Running {sql_path} against tts_erp ===\n")

with open(sql_path) as f:
    content = f.read()

# Split into sections on "-- ----" lines (separator lines with 5+ dashes)
sections = re.split(r"-- -+\n", content)

ok = 0
err = 0
for i, sec in enumerate(sections):
    sec = sec.strip()
    if not sec or sec.startswith("-- ==="):
        continue
    # First non-comment, non-blank line = section title
    lines = sec.splitlines()
    title = next((ln.strip("-- ").strip() for ln in lines if ln.strip() and not ln.strip().startswith("-- ")), f"section_{i}")
    if not any(kw in sec.lower() for kw in ("select", "with", "union")):
        continue
    try:
        with psycopg.connect(DB_URL) as conn, conn.cursor() as cur:
            cur.execute(sec)
            rows = cur.fetchall()
            n = len(rows)
            cols = [d.name for d in cur.description] if cur.description else []
            print(f"  ✓ [{title[:50]:50s}]  rows={n}  cols={len(cols)}")
            # Print first row for sanity
            if rows and len(rows[0]) <= 8:
                first = " | ".join(str(c)[:30] for c in rows[0])
                print(f"      first: {first}")
        ok += 1
    except Exception as e:
        print(f"  ✗ [{title[:50]:50s}]  ERROR: {str(e)[:120]}")
        err += 1

print(f"\n=== Done: {ok} ok, {err} error(s) ===")
