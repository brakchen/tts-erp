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
from pathlib import Path
from typing import Iterator

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
        "orders", "order_items", "order_shippings",
        "payments", "statements", "returns", "cancellations",
    ]
    conn = psycopg.connect(db_url)
    try:
        with conn.cursor() as cur:
            for tbl in cleanup_tables:
                cur.execute(f"DELETE FROM {tbl} WHERE shop_id LIKE 'TEST_%'")
        conn.commit()
    finally:
        conn.close()
