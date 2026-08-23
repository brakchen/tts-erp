"""Shared pytest fixtures for analytics_sync tests.

The repository's tests need a real Postgres connection. We follow the
tts-erp pattern: a session-scoped db_url fixture reads TTS_ERP_DB_URL
from .env, and each test gets a fresh transactional connection that
gets rolled back at the end. Tests that bypass the connection helper
(like the FastAPI test client) clean up after themselves via TEST_
sentinel deletes in the session-level teardown.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterator

import psycopg
import pytest

# Make analytics_sync importable when pytest is invoked from the repo root.
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
    """A connection inside a transaction that is rolled back at teardown.

    Tests that need committed state (e.g. cross-connection visibility
    for the FastAPI client) should NOT use this fixture — use db_url
    directly.
    """
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
    """End-of-session cleanup. TEST_-prefixed seller_id / key_prefix
    rows are deleted from every analytics_sync table."""
    yield
    cleanup = [
        "DELETE FROM analytics_records WHERE seller_id LIKE 'TEST_%'",
        "DELETE FROM analytics_cursors WHERE seller_id LIKE 'TEST_%'",
        "DELETE FROM analytics_shop_timezones WHERE seller_id LIKE 'TEST_%'",
        "DELETE FROM analytics_sync_tokens WHERE key_prefix LIKE 'TEST_%' OR name LIKE 'TEST_%'",
        "DELETE FROM analytics_audit_log WHERE key_prefix LIKE 'TEST_%' OR path LIKE '%TEST_%'",
    ]
    conn = psycopg.connect(db_url)
    try:
        with conn.cursor() as cur:
            for stmt in cleanup:
                cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()


# ─── Sync-token helper ───────────────────────────────────────────────


@pytest.fixture()
def sync_token(db_url: str) -> str:
    """Insert a test sync token, return its plaintext. Cleanup is handled
    by the session-level _cleanup_test_data fixture."""
    import secrets, hashlib
    plaintext = f"anlsync_TEST_{secrets.token_urlsafe(16)}"
    h = hashlib.sha256(plaintext.encode()).hexdigest()
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO analytics_sync_tokens (key_prefix, key_hash, name, enabled)
            VALUES (%s, %s, %s, true)
            """,
            (plaintext[:16], h, "TEST_fixture"),
        )
        conn.commit()
    return plaintext


# ─── FastAPI client ──────────────────────────────────────────────────


@pytest.fixture()
def fastapi_client(db_url: str):
    """A TestClient with the auth cache disabled so we always see fresh
    DB state. The DB connection is the production one — cleanup happens
    via TEST_ sentinels at session end.

    Default mode is 'enforce' so auth tests can verify 401 responses
    without per-test env tweaking. Tests that need to bypass auth can
    pass `ANALYTICS_SYNC_AUTH_MODE=off` in their test environment or
    request a separate fixture.
    """
    os.environ["ANALYTICS_SYNC_AUTH_MODE"] = os.environ.get("ANALYTICS_SYNC_AUTH_MODE", "enforce")
    from fastapi.testclient import TestClient
    from analytics_sync.app import app
    from analytics_sync import auth as auth_mod
    auth_mod.clear_cache()
    return TestClient(app)
