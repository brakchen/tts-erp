"""Shared pytest fixtures and configuration for tts-erp TDD suite.

Out of TDD scope (documented for clarity, not aspirational coverage):

- TikTok API real responses: third-party contract, not our code
- HMAC acceptance by TikTok server: needs real env, covered by e2e smoke
- PG trigger behavior under high concurrency: rely on production monitoring
- PG connection pool / network jitter: integration/chaos scope
- oauth-receiver token renewal: that project owns this
- PG schema DDL correctness: IF NOT EXISTS idempotent, manual psql apply
- TikTok rate-limit backoff: not implemented yet (direct fail)
- systemd/container deployment: ops scope
- BaseHTTPRequestHandler routing: framework concern, not business logic
- HTTP frame format: framework concern, not business logic
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

# Make /home/schan/tts-erp importable so tests can `from tts_signing import ...`
TTS_ERP_ROOT = Path(__file__).resolve().parent.parent
if str(TTS_ERP_ROOT) not in sys.path:
    sys.path.insert(0, str(TTS_ERP_ROOT))


def _load_env_file() -> None:
    """Load /home/schan/tts-erp/.env into os.environ if not already set.

    tts_erp.persist_* functions read TTS_ERP_DB_URL from os.environ at
    call time, not at import time. We populate it once at session start
    so the Pg*Repository tests can actually write to PG.
    """
    env_path = TTS_ERP_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        # Don't override if already set in the test runner environment
        os.environ.setdefault(k, v)


_load_env_file()


# ─── PG fixtures (transactional rollback isolation) ────────────────────


def _load_db_url() -> str:
    env_path = TTS_ERP_ROOT / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("TTS_ERP_DB_URL="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("TTS_ERP_DB_URL not in .env")


@pytest.fixture(scope="session")
def db_url() -> str:
    return _load_db_url()


@pytest.fixture()
def db_conn(db_url: str) -> Iterator[psycopg.Connection]:
    """Each test gets a connection inside a transaction that is rolled back.

    Standard pattern for DB unit tests with pytest — safe to run against
    the real production DB without polluting it.
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
def _cleanup_test_shop_ids(db_url: str):
    """After the entire test session, delete any rows with shop_id starting with TEST_.

    Tests that touch the production tables (orders, payments, etc.) via
    Pg*Repository.upsert() can't rely on the per-test transaction rollback
    because upsert() opens its own connection and commits.

    Sentinel convention: any shop_id starting with "TEST_" is fair game
    to delete at session end. Production shop_ids never start with TEST_.
    """
    yield
    cleanup_tables = [
        "orders",
        "order_items",
        "order_shippings",
        "payments",
        "statements",
        "returns",
        "cancellations",
    ]
    conn = psycopg.connect(db_url)
    try:
        with conn.cursor() as cur:
            for tbl in cleanup_tables:
                cur.execute(f"DELETE FROM {tbl} WHERE shop_id LIKE 'TEST_%'")
        conn.commit()
    finally:
        conn.close()


# ─── OAuth-receiver fixtures (shared by test_oauth_receiver_core + adversarial) ─


_OAUTH_RECEIVER_ENV = Path("/home/schan/oauth-receiver/.env")


def _load_oauth_env(monkeypatch: pytest.MonkeyPatch) -> str | None:
    """Inject OAUTH_DB_URL + OAUTH_DB_ENCRYPTION_KEY from oauth-receiver/.env.

    Returns the OAUTH_DB_URL or None if .env not present.
    """
    if not _OAUTH_RECEIVER_ENV.exists():
        return None
    db_url = None
    for line in _OAUTH_RECEIVER_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("OAUTH_DB_URL="):
            db_url = line.split("=", 1)[1].strip()
            monkeypatch.setenv("OAUTH_DB_URL", db_url)
        if line.startswith("OAUTH_DB_ENCRYPTION_KEY="):
            monkeypatch.setenv("OAUTH_DB_ENCRYPTION_KEY", line.split("=", 1)[1].strip())
    return db_url


@pytest.fixture()
def fernet_key(monkeypatch: pytest.MonkeyPatch):
    """Inject a fresh Fernet key per test. Resets module cache."""
    from cryptography.fernet import Fernet

    import oauth_receiver_core as oc

    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("OAUTH_DB_ENCRYPTION_KEY", key)
    oc._reset_for_testing()
    yield key
    oc._reset_for_testing()


@pytest.fixture()
def no_fernet(monkeypatch: pytest.MonkeyPatch):
    """Tests needing get_fernet() to return None (missing key)."""
    import oauth_receiver_core as oc

    monkeypatch.delenv("OAUTH_DB_ENCRYPTION_KEY", raising=False)
    oc._reset_for_testing()
    yield
    oc._reset_for_testing()


@pytest.fixture()
def oauth_db_url(monkeypatch: pytest.MonkeyPatch):
    """OAUTH_DB_URL from oauth-receiver/.env. Skip if .env not present."""
    import oauth_receiver_core as oc

    db_url = _load_oauth_env(monkeypatch)
    if not db_url:
        pytest.skip("oauth-receiver .env not present or OAUTH_DB_URL missing")
    oc._reset_for_testing()
    yield db_url
    oc._reset_for_testing()


@pytest.fixture()
def oauth_db_conn(oauth_db_url: str) -> Iterator[psycopg.Connection]:
    """Connection to oauth_receiver DB; rolled back at teardown."""
    conn = psycopg.connect(oauth_db_url)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture()
def clean_test_shops(oauth_db_conn: psycopg.Connection):
    """Delete TEST_ rows AND __default__ before/after the test."""
    with oauth_db_conn.cursor() as cur:
        cur.execute("DELETE FROM oauth_tokens WHERE shop_id LIKE 'TEST_%'")
        cur.execute("DELETE FROM oauth_tokens WHERE shop_id = '__default__'")
    yield
    with oauth_db_conn.cursor() as cur:
        cur.execute("DELETE FROM oauth_tokens WHERE shop_id LIKE 'TEST_%'")
        cur.execute("DELETE FROM oauth_tokens WHERE shop_id = '__default__'")
    oauth_db_conn.commit()
