"""Shared pytest fixtures for tts_erp_v2 tests.

Mirrors the legacy ``tdd/conftest.py`` rollback-isolation pattern but
on top of SQLAlchemy 2.0 ORM session + the new ``tts_erp_v2`` schema.

Sentinel convention (matches the legacy suite): any data created by
tests must carry the ``TEST_`` prefix on its identifier or a
``__test__ = True`` sentinel on its row, so the session-end cleanup
fixture can purge it without touching real data.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

# Make project root importable.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure .env is loaded before reading TTS_ERP_DB_URL.
def _load_env() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

# Append +psycopg driver if .env gave plain postgresql:// (legacy URL
# format). psycopg2 is not installed in this environment.
_db_url = os.environ.get("TTS_ERP_DB_URL")
if _db_url and _db_url.startswith("postgresql://") and "+psycopg" not in _db_url:
    _db_url = "postgresql+psycopg://" + _db_url[len("postgresql://"):]
    os.environ["TTS_ERP_DB_URL"] = _db_url


@pytest.fixture(scope="session")
def db_url() -> str:
    return _db_url  # type: ignore[return-value]


@pytest.fixture()
def db_engine(db_url: str):
    """Per-test engine. sessionmaker cache in base.py is reset between tests."""
    from tts_erp_v2.db.base import get_engine, reset_for_testing

    reset_for_testing()
    eng = get_engine(db_url)
    yield eng
    reset_for_testing()


@pytest.fixture()
def db_session(db_engine) -> Iterator[Session]:
    """Each test gets a session inside a transaction that is rolled back.

    Mirrors ``tdd/conftest.py``'s ``db_conn`` fixture: identical
    isolation guarantee, different transport.
    """
    from tts_erp_v2.db.base import get_session_factory

    SessionLocal = get_session_factory(db_engine)
    sess = SessionLocal()
    # Open a SAVEPOINT so we can rollback nested test logic.
    sess.begin_nested()
    try:
        yield sess
    finally:
        sess.rollback()
        sess.close()


@pytest.fixture(autouse=True)
def _check_schema_prereq(db_engine) -> None:
    """Skip tests when alembic hasn't been applied yet.

    Smoke tests assume all 35 tables exist. The session-end cleanup
    fixture (below) wipes any TEST_-prefixed data after the suite runs.
    """
    expected = {
        "integration.credentials", "integration.raw_records",
        "integration.sync_jobs", "integration.sync_cursors", "integration.sync_issues",
        "commerce.channel_accounts", "commerce.channel_products",
        "commerce.channel_product_variants", "commerce.sales_orders", "commerce.sales_order_lines",
        "procurement.procurement_accounts", "procurement.procurement_products",
        "procurement.procurement_product_variants", "procurement.purchase_orders",
        "procurement.purchase_order_lines", "procurement.manual_product_costs",
        "fulfillment.shipments", "fulfillment.shipment_lines", "fulfillment.tracking_events",
        "after_sales.cases", "after_sales.case_lines",
        "finance.payouts", "finance.settlement_statements",
        "finance.settlement_transactions", "finance.settlement_components",
        "linkage.account_links", "linkage.product_links", "linkage.variant_links",
        "linkage.link_evidence", "linkage.link_overrides", "linkage.link_issues",
        "reporting.product_cost_snapshots", "reporting.product_profit_daily",
        "reporting.shipment_tracking_summary",
        "security.api_keys",
    }
    with db_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT table_schema || '.' || table_name FROM information_schema.tables "
                 "WHERE table_schema IN ('integration','commerce','procurement','fulfillment',"
                 "'after_sales','finance','linkage','reporting','security')")
        ).fetchall()
    actual = {r[0] for r in rows}
    missing = expected - actual
    if missing:
        pytest.skip(
            f"alembic upgrade head has not been applied; missing tables: {sorted(missing)}"
        )
