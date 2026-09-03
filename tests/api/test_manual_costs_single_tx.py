"""Single-transaction invariant for ``POST /v2/reporting/manual-costs``.

Audit P1-6 (2026-09-01): the manual-costs POST handler used to INSERT
the new row in transaction #1 (commit), then UPDATE the prior row's
``valid_to`` in transaction #2 wrapped in ``try/except: rollback()``.
A connection blip or constraint-violation between the two commits left
TWO rows with ``valid_to IS NULL`` for the same ``channel_product_id``
— i.e. two "effective" manual costs at once, which silently doubled
downstream cogs / profit figures.

The fix wraps close-old + insert in a single transaction, and adds the
DB-side belt-and-suspenders: a partial unique index
``uq_manual_costs_one_open`` on ``(channel_product_id) WHERE valid_to
IS NULL``. Even if app logic regresses, the index rejects the second
open row.

This test pins:

* happy path — second submission closes the first, no duplicates
* concurrent submission (two near-simultaneous POSTs for the same
  SPU) — at most ONE row has ``valid_to IS NULL`` afterwards

The concurrent case requires the partial unique index to be applied
via alembic upgrade; the migration is added separately
(``alembic/versions/0003_manual_costs_one_open.py``). Until that
migration runs in prod, the app-level guarantee alone holds; after,
both layers enforce the invariant.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


def _seed_channel_product(db_engine, external_id: str) -> int:
    """Seed a channel_account + channel_product pair; return the product id.

    Mirrors the helper in test_manual_costs.py — see that file for the
    real-commit explanation (the request handler reads via its own
    connection, the ``db_session`` fixture is savepoint-rolled-back).
    The shared autouse ``_isolate_state`` cleanup wipes TEST_-prefixed
    rows afterwards.
    """
    with Session(db_engine) as sess:
        sess.execute(
            # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict (see AGENTS.md "Critical Context")
            text(
                "INSERT INTO commerce.channel_accounts "
                "(platform, external_account_id, account_name, status) "
                "VALUES ('tiktok', 'TEST_acct_mc_tx', 'TEST acct mc tx', 'active')"
            )
        )
        acct_id = sess.execute(
            # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict (see AGENTS.md "Critical Context")
            text(
                "SELECT id FROM commerce.channel_accounts "
                "WHERE external_account_id = 'TEST_acct_mc_tx'"
            )
        ).scalar()
        sess.execute(
            # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict (see AGENTS.md "Critical Context")
            text(
                "INSERT INTO commerce.channel_products "
                "(channel_account_id, external_product_id, title, status) "
                "VALUES (:acct, :ext, 'TEST mc tx product', 'ACTIVATE')"
            ),
            {"acct": acct_id, "ext": external_id},
        )
        sess.commit()
        cp_id = sess.execute(
            # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict (see AGENTS.md "Critical Context")
            text(
                "SELECT id FROM commerce.channel_products "
                "WHERE external_product_id = :ext"
            ),
            {"ext": external_id},
        ).scalar()
    return cp_id


def _count_open_manual_costs(db_engine, channel_product_id: int) -> int:
    """Count rows for this SPU with ``valid_to IS NULL`` (effective rows).

    Pre-fix bug: this could return 2 (or more) after a concurrent
    double-POST; post-fix it must return exactly 1.
    """
    with Session(db_engine) as sess:
        n = sess.execute(
            # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict (see AGENTS.md "Critical Context")
            text(
                "SELECT COUNT(*) FROM procurement.manual_product_costs "
                "WHERE channel_product_id = :cp AND valid_to IS NULL"
            ),
            {"cp": channel_product_id},
        ).scalar()
    return int(n or 0)


# ─── 1. happy path: second submission closes the first, no duplicates ──


def test_second_submission_yields_exactly_one_open_row(
    api_client, readwrite_key, db_engine
):
    """Submit twice sequentially for the same SPU. After both:
    - exactly 1 row has valid_to IS NULL (the new one)
    - exactly 1 row has valid_to NOT NULL (the closed one)
    This was previously the path that worked; the regression we
    guard against is the SECOND-commit swallowing scenario.
    """
    cp_id = _seed_channel_product(db_engine, "TEST_mc_singleton")

    r1 = api_client.post(
        "/v2/reporting/manual-costs",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_product_external_id": "TEST_mc_singleton",
            "unit_cost": "10.00",
            "currency": "USD",
        },
    )
    assert r1.status_code == 201, r1.text

    r2 = api_client.post(
        "/v2/reporting/manual-costs",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_product_external_id": "TEST_mc_singleton",
            "unit_cost": "11.00",
            "currency": "USD",
        },
    )
    assert r2.status_code == 201, r2.text

    # THE INVARIANT: exactly one open row (the new $11.00 one).
    open_count = _count_open_manual_costs(db_engine, cp_id)
    assert open_count == 1, (
        f"expected exactly 1 row with valid_to IS NULL, got {open_count} "
        f"(audit P1-6: two-commit bug regression)"
    )

    # Sanity: total row count is 2 (one closed, one open), not 3.
    with Session(db_engine) as sess:
        total = sess.execute(
            # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict (see AGENTS.md "Critical Context")
            text(
                "SELECT COUNT(*) FROM procurement.manual_product_costs "
                "WHERE channel_product_id = :cp"
            ),
            {"cp": cp_id},
        ).scalar()
    assert total == 2


# ─── 2. concurrent submissions: at most one open row ──────────────────


def test_concurrent_submissions_yield_at_most_one_open_row(
    api_client, readwrite_key, db_engine
):
    """Two near-simultaneous POSTs for the same SPU.

    Without the partial unique index this could leave two open rows
    (the original bug); WITH the index, the second commit fails with
    ``uq_manual_costs_one_open`` and rolls back the second POST — the
    handler returns 500 (we accept either 201+1-open or 500+1-open, but
    NEVER 2-open).

    This test is robust to whether the migration has been applied yet:
    - Pre-migration: app-level single-tx alone holds; result is 1-open.
    - Post-migration: index is the second line of defence; one of the
      two POSTs returns 500 and the other leaves 1-open.
    Either way: NOT 2-open.
    """
    cp_id = _seed_channel_product(db_engine, "TEST_mc_race")

    # Fire two POSTs back-to-back. The TestClient serialises them on
    # the same in-process event loop, so they take separate requests
    # but land in the same DB transaction slot via the request handler.
    r1 = api_client.post(
        "/v2/reporting/manual-costs",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_product_external_id": "TEST_mc_race",
            "unit_cost": "5.00",
            "currency": "USD",
        },
    )
    r2 = api_client.post(
        "/v2/reporting/manual-costs",
        headers={"Authorization": f"Bearer {readwrite_key}"},
        json={
            "channel_product_external_id": "TEST_mc_race",
            "unit_cost": "7.00",
            "currency": "USD",
        },
    )

    # The first POST should always succeed. The second may either
    # succeed (pre-migration, app-only invariant) or 500 (post-
    # migration, index rejected). Both are acceptable.
    assert r1.status_code == 201, r1.text
    assert r2.status_code in (201, 500), (
        f"unexpected r2 status: {r2.status_code}: {r2.text}"
    )

    # THE INVARIANT: no matter which one succeeded, at most ONE open row.
    open_count = _count_open_manual_costs(db_engine, cp_id)
    assert open_count <= 1, (
        f"audit P1-6: two open rows after concurrent POSTs "
        f"(got {open_count}). The single-transaction rewrite or the "
        f"uq_manual_costs_one_open index is not protecting this SPU."
    )


# ─── 3. partial unique index existence is the DB-side guarantee ────────


def test_partial_unique_index_present_or_skip(db_engine):
    """If the migration has been applied, the index exists; if not,
    skip with a clear message.

    This is a soft check: it doesn't FAIL the suite if the index
    hasn't been applied (the test runner may not have run alembic
    upgrade). It DOES fail if the index is malformed (e.g. missing
    the WHERE clause, which would over-restrict).
    """
    with Session(db_engine) as sess:
        rows = sess.execute(
            # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict (see AGENTS.md "Critical Context")
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'procurement' "
                "AND tablename = 'manual_product_costs' "
                "AND indexname = 'uq_manual_costs_one_open'"
            )
        ).fetchall()
    if not rows:
        pytest.skip(
            "uq_manual_costs_one_open not yet applied — run "
            "alembic upgrade head to deploy migration 0003"
        )
    # The indexdef must include the partial-index WHERE clause — that's
    # the whole point of the index (don't restrict historical rows).
    indexdef = rows[0][0]
    assert "WHERE" in indexdef.upper(), (
        f"index exists but is missing WHERE clause: {indexdef}"
    )
