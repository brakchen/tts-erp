"""Shared pytest fixtures for analytics_sync tests.

Uses tts-erp's `api_keys` table (unified auth). Analytics_sync no
longer has its own token table.
"""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path
from typing import Iterator

import psycopg
import pytest

# Make analytics_sync + tdd.auth importable when pytest is invoked from
# the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_db_url() -> str:
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    url = os.environ.get("TTS_ERP_DB_URL") or os.environ.get("ANALYTICS_SYNC_DB_URL")
    if not url:
        pytest.skip("TTS_ERP_DB_URL not configured; set it in .env")
    return url


@pytest.fixture(scope="session")
def db_url() -> str:
    return _load_db_url()


@pytest.fixture()
def db_conn(db_url: str) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(db_url, autocommit=False)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture()
def db_cursor(db_conn: psycopg.Connection) -> Iterator[psycopg.Cursor]:
    with db_conn.cursor() as cur:
        yield cur


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_data(db_url: str):
    """End-of-session cleanup for TEST_-prefixed seller_ids and api_keys
    named with TEST_."""
    yield
    cleanup = [
        "DELETE FROM analytics_records WHERE seller_id LIKE 'TEST_%'",
        "DELETE FROM analytics_cursors WHERE seller_id LIKE 'TEST_%'",
        "DELETE FROM analytics_shop_timezones WHERE seller_id LIKE 'TEST_%'",
        "DELETE FROM api_keys WHERE name LIKE 'TEST_%' OR key_prefix LIKE 'ttserp_%%TEST%%'",
        "DELETE FROM analytics_audit_log WHERE key_prefix LIKE 'ttserp_%%'",
    ]
    conn = psycopg.connect(db_url)
    try:
        with conn.cursor() as cur:
            for stmt in cleanup:
                cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()


# ─── API key helper (unified auth) ────────────────────────────────────


@pytest.fixture()
def sync_token(db_url: str) -> str:
    """Insert a TEST_-prefixed api_keys row with role=readwrite, return the
    plaintext. The token is also kept in the in-process cache."""
    import hashlib
    plaintext = "ttserp_rw_TEST_" + secrets.token_urlsafe(16)
    h = hashlib.sha256(plaintext.encode()).hexdigest()
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO api_keys (key_prefix, key_hash, name, role, scopes, enabled)
            VALUES (%s, %s, %s, 'readwrite', ARRAY[]::TEXT[], true)
            """,
            (plaintext[:16], h, "TEST_fixture"),
        )
        conn.commit()
    # Clear cache so the new token is recognized on first request.
    from tdd.auth import clear_cache
    clear_cache()
    return plaintext


@pytest.fixture()
def seller_scoped_token(db_url: str) -> str:
    """An api_key whose scopes[] restrict it to seller='TEST_scoped_seller'."""
    import hashlib
    plaintext = "ttserp_rw_TEST_" + secrets.token_urlsafe(16)
    h = hashlib.sha256(plaintext.encode()).hexdigest()
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO api_keys (key_prefix, key_hash, name, role, scopes, enabled)
            VALUES (%s, %s, %s, 'readwrite', ARRAY['seller:TEST_scoped_seller']::TEXT[], true)
            """,
            (plaintext[:16], h, "TEST_scoped"),
        )
        conn.commit()
    from tdd.auth import clear_cache
    clear_cache()
    return plaintext


# ─── FastAPI client ──────────────────────────────────────────────────


@pytest.fixture()
def fastapi_client(db_url: str):
    """A TestClient wrapping analytics_sync.app:app. Default auth mode
    is 'enforce' so 401/403 tests work without per-test env tweaking."""
    os.environ["ANALYTICS_SYNC_AUTH_MODE"] = os.environ.get("ANALYTICS_SYNC_AUTH_MODE", "enforce")
    from fastapi.testclient import TestClient
    from analytics_sync.app import app
    from tdd.auth import clear_cache as _auth_clear
    from analytics_sync import rate_limit as rl_mod
    _auth_clear()
    rl_mod.reset_buckets()
    return TestClient(app)
