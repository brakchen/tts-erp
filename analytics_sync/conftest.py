"""Shared pytest fixtures for analytics_sync tests.

Uses tts-erp's `api_keys` table (unified auth). Analytics_sync no
longer has its own token table.
"""

from __future__ import annotations

import os
import secrets
import sys
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

# Make analytics_sync + tdd.auth importable when pytest is invoked from
# the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_env_at_import() -> None:
    """Load .env at conftest import time.

    pytest imports conftest before any test module, so doing this here
    guarantees TTS_ERP_DB_URL and friends are present before test modules
    import analytics_sync.auth -> tdd.auth -> tts_erp (which reads env at
    module import time).
    """
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_at_import()


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
    conn = psycopg.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM analytics_records WHERE seller_id LIKE 'TEST_%'")
            cur.execute(
                "DELETE FROM analytics_daily_pages WHERE seller_id LIKE 'TEST_%'"
            )
            cur.execute(
                "DELETE FROM analytics_daily_completeness WHERE seller_id LIKE 'TEST_%'"
            )
            cur.execute("DELETE FROM analytics_cursors WHERE seller_id LIKE 'TEST_%'")
            cur.execute(
                "DELETE FROM analytics_shop_timezones WHERE seller_id LIKE 'TEST_%'"
            )
            cur.execute(
                "DELETE FROM security.api_keys WHERE name LIKE 'TEST_%' OR key_prefix LIKE 'ttserp_%%TEST%%'"
            )
            cur.execute(
                "DELETE FROM analytics_audit_log WHERE key_prefix LIKE 'ttserp_%%'"
            )
        conn.commit()
    finally:
        conn.close()


# ─── API key helper (unified auth) ────────────────────────────────────


def _insert_test_key(
    db_url: str, *, role: str, name: str, scopes: list[str], enabled: bool = True
) -> str:
    """Insert a TEST api_keys row and return the plaintext token.

    The key_prefix column is UNIQUE and only 16 chars wide, so the token
    layout puts entropy early: 'ttserp_<role>_T' + urlsafe random. The
    trailing 'T' marks test rows for the session cleanup DELETE.
    """
    import hashlib

    role_prefix = {"readonly": "ro", "readwrite": "rw", "admin": "admin"}[role]
    plaintext = f"ttserp_{role_prefix}_T" + secrets.token_urlsafe(24)
    h = hashlib.sha256(plaintext.encode()).hexdigest()
    # V3 schema dropped the ``scopes`` and ``enabled`` columns; the
    # function signature keeps them for back-compat with existing
    # callers, but the SQL only writes the V3 columns. ``enabled``
    # is mapped to ``status='active' | 'disabled'`` here.
    status = "active" if enabled else "disabled"
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO security.api_keys (key_prefix, key_hash, name, role, status)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (plaintext[:16], h, name, role, status),
        )
        conn.commit()
    # Clear cache so the new token is recognized on first request.
    from tdd.auth import clear_cache

    clear_cache()
    return plaintext


@pytest.fixture()
def sync_token(db_url: str) -> str:
    """Insert a TEST_-prefixed api_keys row with role=readwrite, return the
    plaintext. The token is also kept in the in-process cache."""
    return _insert_test_key(db_url, role="readwrite", name="TEST_fixture", scopes=[])


@pytest.fixture()
def seller_scoped_token(db_url: str) -> str:
    """An api_key whose scopes[] restrict it to seller='TEST_scoped_seller'."""
    return _insert_test_key(
        db_url,
        role="readwrite",
        name="TEST_scoped",
        scopes=["seller:TEST_scoped_seller"],
    )


# ─── FastAPI client ──────────────────────────────────────────────────


@pytest.fixture()
def fastapi_client(db_url: str):
    """A TestClient wrapping analytics_sync.app:app. Default auth mode
    is 'enforce' so 401/403 tests work without per-test env tweaking."""
    os.environ["ANALYTICS_SYNC_AUTH_MODE"] = os.environ.get(
        "ANALYTICS_SYNC_AUTH_MODE", "enforce"
    )
    from fastapi.testclient import TestClient

    from analytics_sync import rate_limit as rl_mod
    from analytics_sync.app import app
    from tdd.auth import clear_cache as _auth_clear

    _auth_clear()
    rl_mod.reset_buckets()
    return TestClient(app)
