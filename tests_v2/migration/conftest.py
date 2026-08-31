"""Migration-test fixtures.

Migration tests run against the real DB. They share the same
``tests_v2/conftest.py`` outer fixtures (db_engine, db_session, schema
prereq check) and layer in:

* A session-level fixture that runs all migrations once at the start of
  the test session (in dry-run + apply mode).
* Per-test fixtures that exercise individual migration runs in dry-run
  mode so the test never persists partial state on top of the session-
  level migration.

SAFETY (2026-08-30 / 2026-08-31)
---------------------------------
This fixture used to be ``autouse=True`` — which meant ANY pytest run
under ``tests_v2/`` (including the pi-web test runner) would re-run
``migrate_shops.run(dry_run=False)`` against the PRODUCTION database
and overwrite ``integration.credentials.ciphertext`` with the legacy
(312-byte, non-JSON-envelope) format. That silently broke the sync
worker's ``load_credentials`` (JSONDecodeError) every time tests ran.

The fixture is now OPT-IN at two levels:

  1. Request-level: migration tests request it explicitly
     (``pytestmark = pytest.mark.usefixtures("_ensure_migrations_applied")``
     or a ``request`` fixture). Nothing applies migrations automatically.
  2. Env-level: even when explicitly requested, the session fixture
     refuses to run unless ``TTS_ERP_ALLOW_PROD_MIGRATION=1`` is set in
     the environment. The check is implemented in
     ``scripts.migrate_v1_to_v2.common.require_prod_guard`` and the
     fixture itself short-circuits with ``pytest.skip`` when the env
     var is absent.

Default behavior: tests in this directory are skipped unless the
operator opts in via the env var. The 2026-08-31 belt-and-braces guard
makes it impossible to replay migrations against the prod DB without
explicit opt-in, even if a future regression re-enables ``autouse``.
"""
from __future__ import annotations

import os

import pytest

# Module-level kill-switch (2026-08-31). ``pytest.skip(allow_module_level=True)``
# prevents pytest from collecting any tests in this directory when the
# operator hasn't set the env var. The session-scoped fixture below
# also re-checks for defense-in-depth.
if os.environ.get("TTS_ERP_ALLOW_PROD_MIGRATION") != "1":
    pytest.skip(
        "Migration domain is opt-in (TTS_ERP_ALLOW_PROD_MIGRATION=1). "
        "Re-running migrate_* against the prod DB clobbered the v2 "
        "credentials ciphertext with the legacy format on 2026-08-30 and "
        "broke the sync worker for ~22 hours. Set the env var to opt in.",
        allow_module_level=True,
    )


@pytest.fixture(scope="session")
def _ensure_migrations_applied() -> None:
    """Run every migrate_* script once at session start (idempotent).

    OPT-IN — NOT autouse. Callers (migration tests) request it explicitly.
    This prevents the pi-web/CI test runner from replaying migrations
    against the production DB, which overwrote the credentials
    ciphertext with the legacy format and broke the sync worker.

    Defense-in-depth (2026-08-31): the module-level skip above already
    blocks collection of this directory when the env var is unset, but
    we re-check at the top of the fixture too. If a future regression
    removes the module-level skip, this check still prevents the
    session-scoped migration replay from firing.

    Tests then exercise individual scripts / dry-run paths on top. We
    don't truncate first because the source ``public.*`` data is what
    the migrations copy from.
    """
    if os.environ.get("TTS_ERP_ALLOW_PROD_MIGRATION") != "1":
        pytest.skip(
            "_ensure_migrations_applied requires TTS_ERP_ALLOW_PROD_MIGRATION=1 "
            "to opt in to running migrations against the real DB "
            "(see scripts/migrate_v1_to_v2/common.py).",
        )
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
