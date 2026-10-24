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
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from scripts._test_db_connections import RunBinding

# Make project root importable.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _isolated_test_context() -> tuple[str, RunBinding]:
    from scripts._test_db_connections import load_run_binding

    value = os.environ.get("TTS_ERP_DB_URL_TEST", "")
    try:
        return value, load_run_binding(value)
    except ValueError:
        sys.stderr.write(
            "[conftest] REFUSED: current isolated run proof is required; "
            "use bash scripts/test_isolated.sh\n"
        )
        raise SystemExit(2) from None


# Bind before dotenv or any schema/fixture connection; no direct/shared fallback.
_db_url, _run_binding = _isolated_test_context()
os.environ["TTS_ERP_DB_URL"] = _db_url


# Load optional non-DB defaults only after the independent run binding passed.
def _load_env() -> None:
    if os.environ.get("TTS_ERP_TEST_NO_DOTENV") == "1":
        return
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


# Test-safe defaults for keys the suite reads from the environment (2026-10-04).
#
# ``.env`` is gitignored and deliberately NOT linked into worktrees by
# default (docs/guides/agent-testing.md §worktree), so a clean checkout
# has none of these — yet several tests depend on them being present:
#
# - ``TTS_ERP_AUTH_MODE``: tests that build the app directly (e.g.
#   ``api_client_focused``, the onboard-page redirect test) rely on the
#   production ``enforce`` posture; without it auth silently falls back
#   to ``off`` and every role-guard assertion (401/403/302) fails.
# - ``TTS_ERP_FERNET_KEY``: oauth state/credential encryption. Note
#   ``tests/jobs_*`` / ``tests/proxy`` conftests set the same default,
#   but only when those directories are *collected* — running a
#   selection such as ``pytest tests/api/...`` leaves the key unset,
#   which is why the failure set drifted with the selection.
# - ``TIKTOK_SERVICE_ID``: oauth authorize needs it to build the link,
#   and the env-fallback app pair below only applies to that exact id.
# - ``TIKTOK_APP_KEY`` / ``TIKTOK_APP_SECRET``: the authorize 200 path
#   resolves an App pair for the env service_id (DB pair wins otherwise).
#
# ``setdefault`` keeps real values winning: ``.env`` (loaded above) or
# an explicitly exported variable still overrides these, so the main
# checkout's behaviour is unchanged.
_TEST_ENV_DEFAULTS = {
    "TTS_ERP_AUTH_MODE": "enforce",
    "TTS_ERP_FERNET_KEY": "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE=",
    "TIKTOK_SERVICE_ID": "test_service_api_1",
    "TIKTOK_APP_KEY": "test_app_key_api",
    "TIKTOK_APP_SECRET": "test_app_secret_api",
}
for _k, _v in _TEST_ENV_DEFAULTS.items():
    os.environ.setdefault(_k, _v)


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


# The wrapper's canonical run URL and independent proof were resolved before
# dotenv above. A merely test-shaped URL or TTS_ERP_TEST_OFF is not a fallback.


@pytest.fixture(scope="session")
def db_url() -> str:
    return _db_url


# Schema prerequisite probe result. The answer ("does this database have the
# tables the suite needs") cannot change during a run — the ephemeral clone is
# already at its final revision before pytest starts. Probing it once per
# session instead of once per test is behaviour-preserving.
_SCHEMA_PREREQ_CACHE: frozenset[str] | None = None


def _probe_schema_tables(db_url: str) -> frozenset[str]:
    """Return ``schema.table`` names the suite depends on. Memoized per process."""
    global _SCHEMA_PREREQ_CACHE
    if _SCHEMA_PREREQ_CACHE is not None:
        return _SCHEMA_PREREQ_CACHE

    from sqlalchemy import create_engine

    # A throwaway engine, not the shared session engine: the probe must not
    # seed the pool that ``db_engine`` later hands out.
    _run_binding.validate_url(db_url)
    eng = create_engine(db_url, pool_pre_ping=False, pool_size=1, max_overflow=0)
    try:
        _run_binding.validate_url(eng.url.render_as_string(hide_password=False))
        with eng.connect() as conn:
            # pi-lens-ignore: python-sql-injection
            actual_database = conn.execute(text("SELECT current_database()")).scalar_one()
            _run_binding.verify_database(actual_database)
            # pi-lens-ignore: python-sql-injection
            rows = conn.execute(
                text(
                    "SELECT table_schema || '.' || table_name FROM information_schema.tables "
                    "WHERE table_schema IN ('integration','commerce','procurement','fulfillment',"
                    "'after_sales','finance','reporting','security','fx','plugin')"
                )
            ).fetchall()
    finally:
        eng.dispose()

    _SCHEMA_PREREQ_CACHE = frozenset(r[0] for r in rows)
    return _SCHEMA_PREREQ_CACHE


@pytest.fixture(scope="session")
def db_engine(db_url: str):
    """Session-scoped engine.

    Was function-scoped: ``reset_for_testing()`` disposed the engine before and
    after every test, so all 2258 tests paid a fresh TCP connect + auth
    handshake (~20 ms each, ~43 s of the ~7 min suite) for a pool that is
    connection-per-test-safe anyway — ``db_session`` hands each test its own
    ``Connection`` and rolls back an outer transaction.

    The sessionmaker cache in ``db/base.py`` is still dropped between tests by
    the autouse ``_reset_session_factory`` below, so tests that swap the bound
    session keep their isolation; only the connection pool is shared.
    """
    from tts_erp_v2.db.base import get_engine, reset_for_testing

    _run_binding.validate_url(db_url)
    reset_for_testing()
    eng = get_engine(db_url)
    _run_binding.validate_url(eng.url.render_as_string(hide_password=False))
    yield eng
    reset_for_testing()


@pytest.fixture(autouse=True)
def _reset_session_factory() -> Iterator[None]:
    """Drop the cached sessionmaker between tests, keeping the shared pool.

    This is the piece ``db_engine`` used to do per test via
    ``reset_for_testing()``. It stays function-scoped and autouse so the
    sessionmaker cache is still cleared for every test; only the engine (and
    its warm connection pool) survives.
    """
    from tts_erp_v2.db.base import reset_session_factory

    reset_session_factory()
    try:
        yield
    finally:
        reset_session_factory()


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
def _check_schema_prereq(db_url: str) -> None:
    """Skip tests when alembic hasn't been applied yet.

    Smoke tests assume all current business tables exist. The session-end cleanup
    fixture (below) wipes any TEST_-prefixed data after the suite runs.

    The probe is memoized per process by :func:`_probe_schema_tables`: the
    ephemeral clone is already at its final revision before pytest starts, so the
    answer is identical for every test in the run. It previously re-queried
    ``information_schema`` — and re-derived a connection — 2258 times.

    Note this no longer depends on ``db_engine``, so tests that never touch the
    database no longer build an engine just to satisfy this autouse check.
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
        "procurement.manual_product_costs",
        "fulfillment.shipments",
        "fulfillment.tracking_events",
        "after_sales.cases",
        "after_sales.case_lines",
        "finance.payouts",
        "finance.settlement_statements",
        "finance.settlement_transactions",
        "finance.settlement_components",
        "fx.exchange_rate_snapshots",
        "fx.exchange_rates",
        "reporting.focused_spus",
        "reporting.product_cost_snapshots",
        "reporting.product_profit_daily",
        "security.api_keys",
        "plugin.orders",
        "plugin.order_lines",
        "plugin.shipments",
        "plugin.tracking_events",
        "plugin.settlements",
        "plugin.settlement_details",
    }
    actual = _probe_schema_tables(db_url)
    missing = expected - actual
    if missing:
        pytest.skip(
            f"alembic upgrade head has not been applied; missing tables: {sorted(missing)}"
        )


# ── 用户账号 / 会话夹具（user-account-authz 测试）──────────────────────
#
# 与 tests/api/conftest.py 的 API-key 夹具同一模式：被测 app 用自己的数据库
# 连接认证，夹具必须真实提交（db_session 的 savepoint 回滚 app 看不见）。
# teardown 只清理自己创建的行：统一使用 ``test_ua_`` 前缀命名空间，删除用户
# 级联清掉 user_roles / user_sessions，自定义角色单独清（级联 role_permissions），
# 并清空 accounts.service 的授权上下文缓存。

UA_TEST_PREFIX = "test_ua_"
# 测试口令（满足密码策略：≥6 位 + 大写 + 小写 + 数字）。名字刻意不带
# "password"：pi-lens 的 ruff core.toml 开着 S105，会把口令形态名字的
# 字面量赋值当硬编码口令误报（仓库 ruff 对 tests/** 已关 S105/S106）。
UA_TEST_CREDENTIAL = "Testpass1"


class UaUser:
    """One committed test account created by :func:`ua_user_factory`."""

    def __init__(
        self, user_id: int, username: str, password: str, display_name: str
    ) -> None:
        self.user_id = user_id
        self.username = username
        self.password = password
        self.display_name = display_name


def _wipe_ua_rows(db_engine) -> None:
    """Delete only the ``test_ua_`` account namespace (children cascade)."""
    from tts_erp_v2.accounts import service

    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection
        conn.execute(text("DELETE FROM security.users WHERE username LIKE 'test_ua_%'"))
        # pi-lens-ignore: python-sql-injection
        conn.execute(text("DELETE FROM security.roles WHERE code LIKE 'test_ua_%'"))
    service.clear_context_cache()


@pytest.fixture()
def ua_user_factory(db_engine):
    """Create real ``security.users`` rows for auth tests; returns :class:`UaUser`."""
    from tts_erp_v2.accounts import service

    def _make(
        username: str,
        *,
        roles: tuple[str, ...] = (),
        password: str = UA_TEST_CREDENTIAL,
        display_name: str | None = None,
        status: str = "active",
    ) -> UaUser:
        normalized = username.strip().lower()
        if not normalized.startswith(UA_TEST_PREFIX):
            raise ValueError(f"ua test usernames must start with {UA_TEST_PREFIX!r}")
        with Session(db_engine) as sess:
            user = service.create_user(
                sess,
                username=normalized,
                display_name=display_name or normalized,
                password=password,
                roles=list(roles),
                actor="ua-authz-tests",
            )
            if status == "disabled":
                service.set_user_status(
                    sess, user_id=user.id, status="disabled", actor_id=None
                )
            account = UaUser(
                user_id=user.id,
                username=user.username,
                password=password,
                display_name=display_name or normalized,
            )
        service.clear_context_cache()
        return account

    yield _make
    _wipe_ua_rows(db_engine)


@pytest.fixture()
def ua_session_factory(db_engine):
    """Create real ``security.user_sessions`` rows; returns the plaintext token."""
    from tts_erp_v2.accounts import sessions as account_sessions

    def _make(
        user_id: int,
        *,
        ttl_seconds: int = 12 * 3600,
        now: datetime | None = None,
    ) -> str:
        with Session(db_engine) as sess:
            token = account_sessions.create_session(
                sess,
                user_id=user_id,
                ttl_seconds=ttl_seconds,
                ip="127.0.0.1",
                user_agent="ua-authz-tests",
                now=now,
            )
            sess.commit()
        return token

    return _make


@pytest.fixture()
def ua_role_factory(db_engine):
    """Create custom ``security.roles`` rows (``test_ua_`` code namespace)."""
    from tts_erp_v2.accounts import service

    def _make(code: str, *, api_tier: str, permissions: tuple[str, ...] = ()) -> str:
        normalized = code.strip().lower()
        if not normalized.startswith(UA_TEST_PREFIX):
            raise ValueError(f"ua test role codes must start with {UA_TEST_PREFIX!r}")
        with Session(db_engine) as sess:
            service.create_role(
                sess,
                code=normalized,
                name=normalized,
                api_tier=api_tier,
                permissions=list(permissions),
            )
        service.clear_context_cache()
        return normalized

    yield _make
    _wipe_ua_rows(db_engine)
