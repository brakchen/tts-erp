"""Migration-test fixtures.

Migration tests run against the real DB. They share the same
``tests_v2/conftest.py`` outer fixtures (db_engine, db_session, schema
prereq check) and layer in:

* A session-level fixture that runs all migrations once at the start of
  the test session (in dry-run + apply mode).
* Per-test fixtures that exercise individual migration runs in dry-run
  mode so the test never persists partial state on top of the session-
  level migration.

Note: we intentionally do NOT truncate v2 tables at session start —
the migration tests need the data they migrate to exist already (in
``public.*``). Cleanup is left to the integration team post-merge.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _ensure_migrations_applied() -> None:
    """Run every migrate_* script once at session start (idempotent).

    Tests then exercise individual scripts / dry-run paths on top. We
    don't truncate first because the source ``public.*`` data is what
    the migrations copy from.
    """
    from scripts.migrate_v1_to_v2 import (
        migrate_after_sales,
        migrate_finance,
        migrate_logistics,
        migrate_miaoshou,
        migrate_orders,
        migrate_shops,
    )
    migrate_shops.run(dry_run=False, verbose=False)
    migrate_orders.run(dry_run=False, verbose=False)
    migrate_logistics.run(dry_run=False, verbose=False)
    migrate_after_sales.run(dry_run=False, verbose=False)
    migrate_finance.run(dry_run=False, verbose=False)
    migrate_miaoshou.run(dry_run=False, verbose=False)


@pytest.fixture()
def dry_run_runner():
    """Return a small helper that runs a migration in dry-run mode and
    returns the stats dataclass.
    """
    from scripts.migrate_v1_to_v2 import (
        migrate_after_sales,
        migrate_finance,
        migrate_logistics,
        migrate_miaoshou,
        migrate_orders,
        migrate_shops,
    )

    runners = {
        "shops": migrate_shops.run,
        "orders": migrate_orders.run,
        "logistics": migrate_logistics.run,
        "after_sales": migrate_after_sales.run,
        "finance": migrate_finance.run,
        "miaoshou": migrate_miaoshou.run,
    }

    def _run(name: str):
        return runners[name](dry_run=True, verbose=False)

    return _run


@pytest.fixture()
def real_runner():
    """Run a migration in real (apply) mode. Used only for idempotency
    checks; never in dry-run-only tests.
    """
    from scripts.migrate_v1_to_v2 import (
        migrate_after_sales,
        migrate_finance,
        migrate_logistics,
        migrate_miaoshou,
        migrate_orders,
        migrate_shops,
    )
    runners = {
        "shops": migrate_shops.run,
        "orders": migrate_orders.run,
        "logistics": migrate_logistics.run,
        "after_sales": migrate_after_sales.run,
        "finance": migrate_finance.run,
        "miaoshou": migrate_miaoshou.run,
    }

    def _run(name: str):
        return runners[name](dry_run=False, verbose=False)

    return _run
