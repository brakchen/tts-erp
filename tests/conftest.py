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
def _coerce_psycopg(url: str) -> str:
    """Translate a plain ``postgresql://`` URL to ``postgresql+psycopg://``.

    The legacy DSN format (``postgresql://user:pass@host/db``) is what
    ``.env`` and most Docker setups emit. SQLAlchemy 2 + psycopg3 needs
    the explicit ``+psycopg`` driver suffix to pick the right DBAPI.
    """
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


# Test-DB override resolution (2026-09-07):
#
# The project historically had every test connect to the production
# database (``TTS_ERP_DB_URL`` from ``.env``). That shared-DB coupling
# is what allowed test commits to leak into prod (the 2026-09-07
# audit found 150 ``integration.sync_jobs`` rows and 1
# ``integration.credentials`` row with ``TEST_`` prefixes sitting in
# the live ``tts_erp`` DB). The fix is env-driven:
#
#   1. ``scripts/test.sh fast`` sources ``.env.test`` (gitignored) which
#      sets ``TTS_ERP_DB_URL_TEST`` to the dedicated ``tts_erp_v3_test``
#      database.
#   2. ``tests/conftest.py`` (here) prefers ``TTS_ERP_DB_URL_TEST``
#      over the prod ``TTS_ERP_DB_URL``; tests run against the test DB.
#   3. The prod API service (``tts-erp.service``) still reads ``.env``
#      unchanged and keeps talking to ``tts_erp``. Zero restart, zero
#      docker change.
#
# We do NOT hard-fail when ``TTS_ERP_DB_URL_TEST`` is unset: that
# keeps direct ``pytest`` invocations (e.g. ``pytest tests/db/`` for
# a one-off introspection) working. Instead we print a one-line
# warning the first time we resolve to a prod-shaped dbname so the
# developer notices. The hard guard for the full suite lives in
# ``scripts/test.sh`` (refuses to run without ``.env.test``).
from urllib.parse import urlparse as _urlparse

_db_url_test = os.environ.get("TTS_ERP_DB_URL_TEST")
_db_url_prod = os.environ.get("TTS_ERP_DB_URL")

if _db_url_test:
    _db_url = _coerce_psycopg(_db_url_test)
    os.environ["TTS_ERP_DB_URL"] = _db_url  # propagate so SQLAlchemy
                                           # picks up the override too
elif _db_url_prod:
    _db_url = _coerce_psycopg(_db_url_prod)
    # Defensive: warn if we're about to run tests against what looks
    # like the production DB AND the caller didn't opt in via
    # ``TTS_ERP_DB_URL_TEST``. This catches ``pytest`` invoked without
    # ``scripts/test.sh`` when a developer has only ``.env`` on disk.
    try:
        _dbname = (_urlparse(_db_url).path or "").lstrip("/")
        if _dbname in {"tts_erp", "tts_erp_prod"} or _dbname.startswith("tts_erp_prod_"):
            # 2026-09-13 incident: warning was not loud enough. The
            # ``tests/api/test_admin_purge.py::test_purge_plugin_data_clears_ad_tables``
            # ran against prod ``tts_erp`` from a worktree whose ``.env``
            # symlinked to the main repo's prod ``.env`` and the runner
            # did not source ``.env.test``. The wipe blanked 14,719 rows
            # of ``plugin.ad_daily`` (246 campaigns × 65 days). We now
            # FAIL FAST on prod-shaped dbnames by default — only an
            # explicit env opt-in (TTS_ERP_TEST_OFF=1) can override, and
            # even then stderr still gets a loud banner. See
            # ``tech-doc/incident-reports/2026-09-13-ad-daily-purge.md``.
            test_off = os.environ.get("TTS_ERP_TEST_OFF", "0") == "1"
            if not test_off:
                # NOTE: We use ``sys.exit(2)`` instead of ``pytest.exit()``
                # because pytest catches its own Exit class to set
                # returncode and continue collection. ``sys.exit`` raises
                # SystemExit which propagates through pytest's collect
                # phase as a collection error — session aborts immediately.
                import sys as _sys2
                _sys2.stderr.write(
                    "\n[conftest] REFUSED: prod-shaped DB ``"
                    f"{_dbname}``\n"
                    "             Use ``bash scripts/test.sh fast`` (sources "
                    "``.env.test``),\n"
                    "             or set TTS_ERP_DB_URL_TEST to point at the\n"
                    "             dedicated test DB. TTS_ERP_TEST_OFF=1 bypasses\n"
                    "             this guard (NOT recommended; you will run\n"
                    "             tests against prod and may damage live data).\n\n"
                )
                _sys2.stderr.flush()
                raise SystemExit(2)
            sys.stderr.write(
                "\n[conftest] !!! TTS_ERP_TEST_OFF=1 !!! Running tests against\n"
                f"             prod-shaped DB ``{_dbname}``. LIVE DATA AT RISK.\n\n"
            )
    except Exception:  # noqa: BLE001 — defensive: URL parse failure
        # must never block a test run; we already have a usable _db_url.
        pass
else:
    _db_url = None  # type: ignore[assignment]


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
    """Each test gets a session bound into an outer transaction that is
    rolled back at teardown — the canonical SQLAlchemy 2.0
    ``join_transaction_mode="create_savepoint"`` pattern.

    The previous implementation (plain session + ``begin_nested()``) let
    ``session.commit()`` escape to the real database, leaking TEST_* rows
    into shared dev data. With an external-transaction join, commit() only
    releases the per-test SAVEPOINT; the outer connection transaction is
    always rolled back here.
    """
    conn = db_engine.connect()
    outer = conn.begin()
    sess = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield sess
    finally:
        sess.close()
        outer.rollback()
        conn.close()


@pytest.fixture(autouse=True)
def _check_schema_prereq(db_engine) -> None:
    """Skip tests when alembic hasn't been applied yet.

    Smoke tests assume all 35 tables exist. The session-end cleanup
    fixture (below) wipes any TEST_-prefixed data after the suite runs.
    """
    expected = {
        "integration.credentials",
        "integration.raw_records",
        "integration.sync_jobs",
        "integration.sync_cursors",
        "integration.sync_issues",
        "commerce.shops",
        "commerce.products_spu",
        "commerce.products_sku",
        "commerce.sales_orders",
        "commerce.sales_order_lines",
        "procurement.procurement_accounts",
        "procurement.procurement_products",
        "procurement.procurement_product_variants",
        "procurement.purchase_orders",
        "procurement.purchase_order_lines",
        "procurement.manual_product_costs",
        "fulfillment.shipments",
        "fulfillment.shipment_lines",
        "fulfillment.tracking_events",
        "after_sales.cases",
        "after_sales.case_lines",
        "finance.payouts",
        "finance.settlement_statements",
        "finance.settlement_transactions",
        "finance.settlement_components",
        "fx.exchange_rate_snapshots",
        "fx.exchange_rates",
        "linkage.account_links",
        "linkage.product_links",
        "linkage.variant_links",
        "linkage.link_evidence",
        "linkage.link_overrides",
        "linkage.link_issues",
        "reporting.product_cost_snapshots",
        "reporting.product_profit_daily",
        "reporting.shipment_tracking_summary",
        "security.api_keys",
        "plugin.raw_log",
        "plugin.orders",
        "plugin.order_lines",
        "plugin.shipments",
        "plugin.tracking_events",
        "plugin.settlements",
        "plugin.settlement_details",
    }
    with db_engine.connect() as conn:
        # pi-lens-ignore: python-sql-injection — static schema introspection, no user input
        rows = conn.execute(
            text(
                "SELECT table_schema || '.' || table_name FROM information_schema.tables "
                "WHERE table_schema IN ('integration','commerce','procurement','fulfillment',"
                "'after_sales','finance','linkage','reporting','security','fx','plugin')"
            )
        ).fetchall()
    actual = {r[0] for r in rows}
    missing = expected - actual
    if missing:
        pytest.skip(
            f"alembic upgrade head has not been applied; missing tables: {sorted(missing)}"
        )
