"""One-shot and idempotent backfill utilities.

Used to repair data drift between OAuth source-of-truth and tts_erp
local cache. Currently:

  - backfill_shops_from_oauth(): reads oauth_receiver.oauth_tokens and
    upserts into tts_erp.shops. Triggered on FastAPI startup AND
    exposed as POST /admin/shops/backfill for manual invocation.

Design choices:

  - Two explicit DB URLs are required (oauth_receiver + tts_erp). We
    never read these from the tts_erp process's own env because the
    startup hook needs to work even before env is fully loaded.

  - When `only_test_rows=True`, the source query filters to
    `shop_id LIKE 'TEST_%'`. Tests use this to avoid touching
    production data; production runs default to False.

  - The function is fully idempotent: ON CONFLICT (shop_id) DO UPDATE
    preserves existing rows, refreshes metadata, and bumps last_seen_at.

  - Errors are caught and logged, never raised — this is invoked at
    process startup and MUST NOT block service boot.
"""

from __future__ import annotations

import sys

import psycopg


def backfill_shops_from_oauth(
    *,
    oauth_db_url: str,
    tts_db_url: str,
    only_test_rows: bool = False,
) -> int:
    """Upsert every oauth_tokens row into tts_erp.shops.

    Returns the number of rows written/updated. Returns 0 (and logs to
    stderr) if either DB is unreachable or empty — never raises.
    """
    try:
        with (
            psycopg.connect(oauth_db_url, connect_timeout=5) as oauth_conn,
            oauth_conn.cursor() as cur,
        ):
            if only_test_rows:
                cur.execute(
                    """
                    SELECT shop_id, shop_name, shop_region, seller_type
                    FROM oauth_tokens
                    WHERE shop_id LIKE 'TEST_%'
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT shop_id, shop_name, shop_region, seller_type
                    FROM oauth_tokens
                    """
                )
            rows = cur.fetchall()
    except Exception as e:  # noqa: BLE001
        print(f"[backfill] oauth DB unavailable: {e}", file=sys.stderr)
        return 0

    if not rows:
        return 0

    try:
        with (
            psycopg.connect(tts_db_url, connect_timeout=5) as tts_conn,
            tts_conn.cursor() as cur,
        ):
            cur.executemany(  # nosemgrep: python.lang.security.audit.formatted-sql-query
                """
                INSERT INTO shops
                    (shop_id, shop_name, shop_region, seller_type, last_seen_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (shop_id) DO UPDATE SET
                    shop_name    = COALESCE(EXCLUDED.shop_name,    shops.shop_name),
                    shop_region  = COALESCE(EXCLUDED.shop_region,  shops.shop_region),
                    seller_type  = COALESCE(EXCLUDED.seller_type,  shops.seller_type),
                    last_seen_at = now()
                """,
                rows,
            )
            tts_conn.commit()
        return len(rows)
    except Exception as e:  # noqa: BLE001
        print(f"[backfill] tts_erp upsert failed: {e}", file=sys.stderr)
        return 0


def run_startup_backfill_if_configured() -> int | None:
    """Called from FastAPI startup event. Reads env, runs backfill.

    Returns number of rows written, or None if env not configured.
    Never raises.
    """
    import os

    oauth_url = os.environ.get("OAUTH_DB_URL")
    tts_url = os.environ.get("TTS_ERP_DB_URL")
    if not oauth_url or not tts_url:
        return None
    return backfill_shops_from_oauth(
        oauth_db_url=oauth_url, tts_db_url=tts_url, only_test_rows=False
    )
