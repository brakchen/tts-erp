"""One-off backfill: payload_hash on existing integration.raw_records rows.

Background
----------
``integration.raw_records.payload_hash`` (sha256 of the canonical
sorted-keys JSON) is the dedup + "seen this payload before?" key that the
sync jobs write on insert (jobs/tiktok/orders.py::_store_raw,
jobs/runner.py::record_raw_payload, and — since the 2026-09-06 ops fix —
jobs/tiktok/finance.py::_store_raw). Rows written BEFORE those writers
existed (finance rows especially) have ``payload_hash = NULL``.

This script fills the hash for every NULL row using the SAME canonical
form the writers use (``json.dumps(payload, ensure_ascii=False,
sort_keys=True)``), so lookups/upgrades match exactly. Non-destructive.

Why not SQL ``sha256(payload::text)``?
-------------------------------------
Postgres jsonb::text is not byte-identical to Python's canonical dumps in
every edge case (number formatting, escaping). Hash consistency with the
writers matters more than doing it in SQL, so we compute in Python and
bulk-update via executemany.

USAGE
-----
    # 1. dry-run preview (no writes; prints how many rows need a hash)
    python3 scripts/oneoff_backfill_raw_payload_hashes.py --dry-run

    # 2. actually do it (prints + executes)
    python3 scripts/oneoff_backfill_raw_payload_hashes.py --confirm
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def _load_env() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def _resolve_db_url() -> str:
    raw = os.environ.get("TTS_ERP_DB_URL", "").strip()
    if not raw:
        sys.exit(
            "TTS_ERP_DB_URL is not set; export it (or have it in .env) "
            "before running this script."
        )
    scheme_end = raw.find("://")
    if "+" in raw[:scheme_end]:
        return "postgresql://" + raw[scheme_end + 3 :]
    return raw


def _canonical_hash(payload) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="preview only")
    parser.add_argument("--confirm", action="store_true", help="execute the fill")
    args = parser.parse_args()
    if not args.dry_run and not args.confirm:
        parser.error("pass either --dry-run (preview) or --confirm (execute)")

    try:
        import psycopg
    except ImportError:  # pragma: no cover — operator env guard
        sys.exit("psycopg3 not importable; activate the repo venv (.venv)")

    conn = psycopg.connect(_resolve_db_url())
    # Streaming cursor so a 60k+ row payload scan never lands in memory.
    total = 0
    updates: list[tuple[str, int]] = []
    with conn.cursor("backfill_hash") as cur:
        cur.itersize = 5000
        cur.execute(
            "SELECT id, payload FROM integration.raw_records WHERE payload_hash IS NULL"
        )
        for row_id, payload in cur:
            updates.append((_canonical_hash(payload), row_id))
            total += 1
    print(f"rows with NULL payload_hash: {total}")
    if args.confirm and updates:
        ids = [row_id for _, row_id in updates]
        hashes = [h for h, _ in updates]
        cur = conn.execute(
            "UPDATE integration.raw_records r "
            "SET payload_hash = f.h "
            "FROM unnest(%s::bigint[], %s::text[]) AS f(id, h) "
            "WHERE r.id = f.id",
            (ids, hashes),
        )
        print(f"updated {cur.rowcount} rows")
    conn.commit()
    conn.close()
    print("dry-run complete — no writes" if args.dry_run else "backfill complete")


if __name__ == "__main__":
    _run()
