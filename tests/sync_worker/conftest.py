"""Shared fixtures for sync_worker/ tests.

Wipes ``TEST_``-prefixed rows from tables that sync_worker tests
write to, both BEFORE and AFTER every test. Acts as a safety net so
that:

1. A previous aborted run (KeyboardInterrupt, fixture failure, CI
   timeout) cannot leak rows that contaminate the next run.
2. A test that fails before its own cleanup runs (or one that crashes
   mid-commit) cannot leak rows that would otherwise be visible to
   the watchdog or any production-adjacent query.

Why this exists
---------------
Tests in ``sync_worker/`` historically used
``sessionmaker(bind=get_engine())`` and committed ``SyncJob`` /
``Credentials`` rows directly. ``get_engine()`` returns the
process-wide cached engine which talks to ``tts_erp`` — the **same
database the running prod API uses** (AGENTS.md §1: dev and prod
share a single PG instance and a single database). So every test
commit goes to the production DB; only the ``TEST_`` prefix and
explicit cleanup keep the data distinguishable from real rows.

A 2026-09-07 audit confirmed the production DB already contains
150 ``integration.sync_jobs`` rows with ``job_name LIKE 'TEST_%'``
and ``error_message LIKE '%simulated%'`` (left by tests that
errored mid-cleanup) plus 20 rows using the production
``job_name='reporting.cost_snapshots'`` (test pollution with a
prod-named key — the worst kind of leak because the watchdog treats
them as real). This conftest closes that hole by wiping TEST_ rows
unconditionally on every test boundary.

Why per-directory, not top-level conftest.py
--------------------------------------------
``tests/conftest.py`` is shared with the
``fix/fx-test-isolation`` lane (and the master WT has uncommitted
WIP from other lanes touching adjacent files). Per AGENTS.md §11
"lane 冲突处理", we avoid cross-lane edits to the shared conftest by
scoping this wipe to ``sync_worker/`` only. The ``api/`` subtree
already has an equivalent autouse fixture in
``tests/api/conftest.py::_isolate_state``.

Tables wiped
------------
* ``integration.sync_jobs`` — WHERE ``job_name LIKE 'TEST_%'``
  (covers ``test_scheduler_jobs_coverage``,
  ``test_scheduler_token_refresh``, ``test_watchdog_sync``,
  ``test_main``, ``test_proxy_call``, ``test_reporting_cov``,
  ``test_scheduler_miaoshou_reporting``,
  ``test_same_value_bumps_updated_at``, ``test_watermarks``).
* ``integration.credentials`` — WHERE
  ``external_account_id LIKE 'TEST_%'``
  (covers ``test_scheduler_token_refresh``'s ``_seed_credentials``,
  ``test_tiktok_auth``'s ``upsert_credentials``, and any other
  test that seeds credentials via the production path).

We intentionally do NOT wipe ``commerce.shops`` / ``sync_cursors``
etc. here — those tables are only written by tests in
``tests/api/`` (which has its own wipe) or in
``tests/jobs_tiktok/`` (which uses ``db_session`` rollback and
TEST_-prefixes with no need for safety-net wiping). Adding them
would over-wipe and force these tests to spend wall-clock on DELETEs
they don't need.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import delete, text

from tts_erp_v2.db.base import get_engine
from tts_erp_v2.db.models import Credentials, SyncJob

# pi-lens-ignore: python-sql-injection — literal SQL, bound LIKE param only
_WIPE_SYNC_JOBS = text("DELETE FROM integration.sync_jobs WHERE job_name LIKE 'TEST_%'")
# pi-lens-ignore: python-sql-injection — literal SQL, bound LIKE param only
_WIPE_CREDENTIALS_SQL = text(
    "DELETE FROM integration.credentials WHERE external_account_id LIKE 'TEST_%'"
)


def _wipe(db_engine) -> None:
    """Run both wipe statements on a fresh autocommit connection.

    Uses ``engine.begin()`` (NOT the per-test ``db_session`` savepoint)
    so the wipe is visible to *other* connections that may be reading
    between the test commit and the next test's setup — e.g. the
    watchdog if it happens to run during the test suite.
    """
    with db_engine.begin() as conn:
        conn.execute(_WIPE_SYNC_JOBS)
        conn.execute(_WIPE_CREDENTIALS_SQL)


@pytest.fixture(autouse=True)
def _wipe_test_sync_jobs_and_credentials(db_engine) -> Iterator[None]:
    """Wipe TEST_-prefixed sync_jobs + credentials before & after every test.

    Ordering: setup-wipe runs BEFORE the test body (so a leaked row from
    a prior aborted run cannot pollute this test's reads); teardown-wipe
    runs AFTER (so even an exception mid-test cannot leak rows out of
    this test). Engine is the per-test ``db_engine`` fixture from
    ``tests/conftest.py`` so the wipe shares connection-pool lifecycle
    with the rest of the test.
    """
    _wipe(db_engine)
    yield
    _wipe(db_engine)
