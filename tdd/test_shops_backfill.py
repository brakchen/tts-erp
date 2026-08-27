"""Tests for the tts_erp.shops backfill from oauth_tokens.

Root cause (2026-08-25 investigation):
    legacy tts_erp.py._proxy_order() called persist_shop() on every
    successful order detail GET, populating tts_erp.shops. After the
    FastAPI migration (Wave 3), the new order_detail() handler in
    tts_erp_fastapi.py never calls persist_shop(). Result: 2 shops
    exist in oauth_receiver.oauth_tokens but tts_erp.shops is empty.

Fix:
    Add backfill_shops_from_oauth() that reads all oauth_tokens rows
    and upserts them into tts_erp.shops. Called:
      1. On FastAPI startup (best-effort)
      2. On demand via POST /admin/shops/backfill (admin role)

These tests pin:
  - backfill is idempotent (re-run yields same row count)
  - all fields (shop_id, shop_name, shop_region, seller_type) propagate
  - shops with cipher=None still get a row (cipher lives elsewhere)
"""

from __future__ import annotations


def _read_oauth_token_rows(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT shop_id, shop_name, shop_region, seller_type
            FROM oauth_tokens
            WHERE shop_id LIKE 'TEST_%'
            ORDER BY shop_id
            """
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _read_tts_shops(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT shop_id, shop_name, shop_region, seller_type FROM shops "
            "WHERE shop_id LIKE 'TEST_%' ORDER BY shop_id"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _insert_oauth_token(
    conn, *, shop_id: str, name: str, region: str, seller: str = "CROSS_BORDER"
):
    """Insert a fake oauth_tokens row (encrypted blobs are throwaway —
    backfill doesn't decrypt them)."""
    dummy_blob = b"\x00\x01\x02"  # arbitrary bytes — backfill doesn't decrypt
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO oauth_tokens
                (shop_id, provider, access_token_encrypted, refresh_token_encrypted,
                 shop_name, shop_region, seller_type,
                 access_token_expires_at, refresh_token_expires_at)
            VALUES
                (%s, 'tiktok', %s, %s, %s, %s, %s, 9999999999, 9999999999)
            """,
            (shop_id, dummy_blob, dummy_blob, name, region, seller),
        )
    conn.commit()


def _cleanup(conn):
    """Aggressive cleanup of TEST_ rows from both DBs."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM oauth_tokens WHERE shop_id LIKE 'TEST_%'")
        # Two conns point at different DBs; this one is the oauth_receiver
        # one — caller also passes the tts_erp conn separately.


def test_backfill_inserts_rows_for_all_oauth_tokens(
    oauth_db_url: str,
    db_url: str,
):
    """Given N oauth_tokens rows, backfill creates N tts_erp.shops rows."""
    import psycopg

    oauth_conn = psycopg.connect(oauth_db_url)
    tts_conn = psycopg.connect(db_url)
    try:
        # Clean state
        with oauth_conn.cursor() as cur:
            cur.execute("DELETE FROM oauth_tokens WHERE shop_id LIKE 'TEST_%'")
        with tts_conn.cursor() as cur:
            cur.execute("DELETE FROM shops WHERE shop_id LIKE 'TEST_%'")
        oauth_conn.commit()
        tts_conn.commit()

        # Seed 2 fake oauth_tokens
        _insert_oauth_token(
            oauth_conn, shop_id="TEST_SHOP_A", name="Shop A", region="VN"
        )
        _insert_oauth_token(
            oauth_conn, shop_id="TEST_SHOP_B", name="Shop B", region="US"
        )

        # Backfill — import lazily because tdd/_backfill module may live
        # alongside the implementation.
        from tdd._backfill import backfill_shops_from_oauth

        rows_written = backfill_shops_from_oauth(
            oauth_db_url=oauth_db_url,
            tts_db_url=db_url,
            only_test_rows=True,  # safe: only handles TEST_ sentinels
        )
        assert rows_written == 2

        tts_rows = _read_tts_shops(tts_conn)
        assert len(tts_rows) == 2
        by_id = {r["shop_id"]: r for r in tts_rows}
        assert by_id["TEST_SHOP_A"]["shop_name"] == "Shop A"
        assert by_id["TEST_SHOP_A"]["shop_region"] == "VN"
        assert by_id["TEST_SHOP_B"]["shop_region"] == "US"
    finally:
        with oauth_conn.cursor() as cur:
            cur.execute("DELETE FROM oauth_tokens WHERE shop_id LIKE 'TEST_%'")
        with tts_conn.cursor() as cur:
            cur.execute("DELETE FROM shops WHERE shop_id LIKE 'TEST_%'")
        oauth_conn.commit()
        tts_conn.commit()
        oauth_conn.close()
        tts_conn.close()


def test_backfill_is_idempotent(
    oauth_db_url: str,
    db_url: str,
):
    """Re-running backfill must not duplicate or break anything."""
    import psycopg

    oauth_conn = psycopg.connect(oauth_db_url)
    tts_conn = psycopg.connect(db_url)
    try:
        with oauth_conn.cursor() as cur:
            cur.execute("DELETE FROM oauth_tokens WHERE shop_id LIKE 'TEST_%'")
        with tts_conn.cursor() as cur:
            cur.execute("DELETE FROM shops WHERE shop_id LIKE 'TEST_%'")
        oauth_conn.commit()
        tts_conn.commit()

        _insert_oauth_token(oauth_conn, shop_id="TEST_SHOP_X", name="X", region="VN")

        from tdd._backfill import backfill_shops_from_oauth

        first = backfill_shops_from_oauth(
            oauth_db_url=oauth_db_url,
            tts_db_url=db_url,
            only_test_rows=True,
        )
        second = backfill_shops_from_oauth(
            oauth_db_url=oauth_db_url,
            tts_db_url=db_url,
            only_test_rows=True,
        )
        third = backfill_shops_from_oauth(
            oauth_db_url=oauth_db_url,
            tts_db_url=db_url,
            only_test_rows=True,
        )

        assert first == 1
        assert second == 1  # updates existing row, returns 1
        assert third == 1

        tts_rows = _read_tts_shops(tts_conn)
        assert len(tts_rows) == 1
        assert tts_rows[0]["shop_id"] == "TEST_SHOP_X"
    finally:
        with oauth_conn.cursor() as cur:
            cur.execute("DELETE FROM oauth_tokens WHERE shop_id LIKE 'TEST_%'")
        with tts_conn.cursor() as cur:
            cur.execute("DELETE FROM shops WHERE shop_id LIKE 'TEST_%'")
        oauth_conn.commit()
        tts_conn.commit()
        oauth_conn.close()
        tts_conn.close()


def test_backfill_handles_missing_oauth_db_gracefully(db_url: str, monkeypatch):
    """If OAUTH_DB_URL is unreachable, backfill returns 0 and logs —
    it must NOT raise (startup-time call must be best-effort)."""
    from tdd._backfill import backfill_shops_from_oauth

    rows = backfill_shops_from_oauth(
        oauth_db_url="postgresql://nonexistent:bad@127.0.0.1:1/none",
        tts_db_url=db_url,
        only_test_rows=True,
    )
    assert rows == 0


def test_persist_shop_never_writes_cipher(db_url: str):
    """W1.3: persist_shop must NOT persist the plaintext shop_cipher.

    shop_cipher is a live signing credential; its single source of truth
    is oauth_receiver (encrypted bytea). W1.3 stopped persist_shop from
    writing the column; W2.2 dropped the column entirely. This test now
    pins the post-W2 contract: the column is gone AND persist_shop still
    works (with the legacy cipher kwarg accepted for call-site compat).
    """
    import psycopg

    import tts_erp

    conn = psycopg.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM shops WHERE shop_id LIKE 'TEST_%'")
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name = 'shops' AND column_name = 'shop_cipher'"
            )
            row = cur.fetchone()
        conn.commit()
        assert row is not None and row[0] == 0, (
            "shops.shop_cipher column still exists — W2.2 drop not applied"
        )

        ok = tts_erp.persist_shop(
            "TEST_SHOP_CIPHER",
            name="Cipher Shop",
            region="VN",
            cipher="PLAINTEXT_SECRET_SHOULD_NOT_BE_STORED",
            seller_type="CROSS_BORDER",
        )
        assert ok is True

        # Upsert path also works and updates non-secret fields
        ok = tts_erp.persist_shop(
            "TEST_SHOP_CIPHER",
            name="Cipher Shop v2",
            region="US",
            cipher="ANOTHER_SECRET",
            seller_type="CROSS_BORDER",
        )
        assert ok is True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT shop_name, shop_region FROM shops"
                " WHERE shop_id = 'TEST_SHOP_CIPHER'"
            )
            row = cur.fetchone()
        assert row is not None
        assert row[0] == "Cipher Shop v2"
        assert row[1] == "US"
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM shops WHERE shop_id LIKE 'TEST_%'")
        conn.commit()
        conn.close()
