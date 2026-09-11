"""Regression tests for ``GET /v2/reporting/profit-daily``.

2026-09-01: this endpoint was returning 500 in production because the
SQL selected columns that don't exist on the actual table — the real
table uses ``profit_date`` / ``gross_revenue`` / ``estimated_cogs`` /
``estimated_gross_profit`` (audit P1-2), while the pydantic schema
``ProfitDailyOut`` exposes them as ``on_date`` / ``revenue`` / ``cost``
/ ``profit``. The fix aliases the columns in SQL.

Two test cases pin the contract:

* empty table (or empty filter set) ⇒ 200 + bare JSON array
* one valid row ⇒ 200 + array with the row, with all aliased fields
  present and correctly typed (Decimal / date / currency)

We do NOT touch the DB directly here — the api_client fixture builds
the full FastAPI app, and any rows the rebuild job writes are visible
through the request handler's own connection. The test runs against
the production-shaped schema (via ``_check_schema_prereq`` in the
shared conftest) so a missing table is a hard skip, not a silent 500.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


@pytest.fixture(autouse=True)
def _cleanup_test_profit_daily(db_engine):
    """Wipe TEST_-prefixed product_profit_daily rows before & after each
    test in this file.

    The shared ``tests/api/conftest.py::_isolate_state`` only wipes
    TEST_* rows from the tables Lane E (this lane's neighbours) touch:
    shops, products_spu, manual_product_costs, spu_images,
    api_keys. reporting.product_profit_daily is OUT of that scope, so
    rows this test commits would otherwise leak across runs and break
    subsequent seeding via the (spu_pk, profit_date,
    calculation_version) UNIQUE constraint.
    """
    _wipe_test_profit_daily(db_engine)
    yield
    _wipe_test_profit_daily(db_engine)


def _wipe_test_profit_daily(db_engine) -> None:
    # SQLAlchemy 2.0: Engine.execute() is gone; use begin() to get a
    # connection. We don't care about the result, only the side effect.
    with db_engine.begin() as conn:
        # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() with literal SQL, no user input
        conn.execute(
            text(
                "DELETE FROM reporting.product_profit_daily "
                "WHERE spu_pk IN ("
                "  SELECT id FROM commerce.products_spu "
                "  WHERE spu_id LIKE 'TEST_%'"
                ")"
            )
        )


def test_profit_daily_no_filter_returns_200_bare_array(api_client, readonly_key):
    """GET with no filters → 200 + JSON array.

    Regression guard (2026-09-01): the SQL used to reference
    ``on_date`` / ``revenue`` / ``cost`` / ``profit`` columns that
    don't exist on ``reporting.product_profit_daily``, so PG returned
    UndefinedColumn and the endpoint 500'd. After aliasing the real
    columns (``profit_date AS on_date`` etc.) the endpoint returns 200.

    The contract is a bare JSON array — same shape as
    ``/v2/reporting/cost-snapshots`` — NOT the envelope the
    procurement UI added to ``/missing-cost-products``.
    """
    r = api_client.get(
        "/v2/reporting/profit-daily?limit=5",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, (
        f"profit-daily 500 regression — likely the column-name bug: {r.text}"
    )
    body = r.json()
    assert isinstance(body, list), (
        f"expected bare array (NOT the /missing-cost-products envelope), got {type(body).__name__}"
    )


def test_profit_daily_with_unmatched_filter_returns_200_empty(
    api_client, readonly_key
):
    """A spu_pk filter that matches no rows → 200 + ``[]``.

    Exercises the ``OR spu_pk = :channel_id`` branch with
    non-NULL params so we know the CAST we added for AmbiguousParameter
    didn't break the OR-short-circuit when filters are supplied.
    """
    r = api_client.get(
        "/v2/reporting/profit-daily"
        "?spu_pk=999999999&on_date=2030-01-01",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_profit_daily_with_only_channel_filter_returns_200(
    api_client, readonly_key
):
    """Single-filter combo: only ``spu_pk`` (date is NULL).

    Confirms the CAST(:on_date AS date) handles a NULL param cleanly.
    """
    r = api_client.get(
        "/v2/reporting/profit-daily?spu_pk=999999999",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_profit_daily_with_only_date_filter_returns_200(api_client, readonly_key):
    """Single-filter combo: only ``on_date`` (channel is NULL)."""
    r = api_client.get(
        "/v2/reporting/profit-daily?on_date=2030-01-01",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


def test_profit_daily_one_row_aliases_all_fields(api_client, readwrite_key, db_engine):
    """Seed one row, GET it back, assert every aliased field is correct.

    This is the load-bearing test for the column-name repair. We seed a
    single product_profit_daily row using the real column names
    (``profit_date`` / ``gross_revenue`` / ``estimated_cogs`` /
    ``estimated_gross_profit``) and assert the API response carries
    the same data under the aliased keys (``on_date`` / ``revenue`` /
    ``cost`` / ``profit``).
    """
    # 1. Seed a channel_product (FK target) + a profit row.
    with Session(db_engine) as seed_sess:
        # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict (see AGENTS.md "Critical Context")
        seed_sess.execute(
            text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status) "
                "VALUES ('tiktok', 'TEST_acct_pf', 'TEST acct pf', 'active')"
            )
        )
        # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict (see AGENTS.md "Critical Context")
        acct_id = seed_sess.execute(
            text(
                "SELECT id FROM commerce.shops "
                "WHERE shop_id = 'TEST_acct_pf'"
            )
        ).scalar()
        # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict (see AGENTS.md "Critical Context")
        seed_sess.execute(
            text(
                "INSERT INTO commerce.products_spu "
                "(shop_pk, spu_id, title, status) "
                "VALUES (:acct, 'TEST_PF_SPU', 'TEST pf product', 'ACTIVATE')"
            ),
            {"acct": acct_id},
        )
        # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict (see AGENTS.md "Critical Context")
        cp_id = seed_sess.execute(
            text(
                "SELECT id FROM commerce.products_spu "
                "WHERE spu_id = 'TEST_PF_SPU'"
            )
        ).scalar()
        # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict (see AGENTS.md "Critical Context")
        seed_sess.execute(
            text(
                "INSERT INTO reporting.product_profit_daily "
                "(spu_pk, profit_date, units_sold, gross_revenue, "
                " estimated_cogs, estimated_gross_profit, currency, "
                " cost_method, calculation_version) "
                "VALUES (:cp, '2030-01-15', 3.0000, 150.00, 30.00, 120.00, "
                " 'USD', 'MANUAL_ENTRY', 999)"
            ),
            {"cp": cp_id},
        )
        seed_sess.commit()

    # 2. GET via the API and find the row by spu_pk.
    r = api_client.get(
        f"/v2/reporting/profit-daily"
        f"?spu_pk={cp_id}&on_date=2030-01-15",
        headers={"Authorization": f"Bearer {readwrite_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1, f"expected 1 row, got {len(rows)}: {rows}"
    row = rows[0]

    # 3. Field-by-field assertions on the aliased response shape.
    assert row["id"] > 0
    assert row["spu_pk"] == cp_id
    # on_date is a DATE column aliased onto the ProfitDailyOut schema
    # (typed datetime | None). SQLAlchemy + JSON serialise it as
    # 'YYYY-MM-DDTHH:MM:SS' (midnight when the source has no time
    # component) — the contract is "ISO-8601", not "no time suffix".
    # We assert the date prefix only; the time is an implementation
    # detail of the JSON encoder.
    on_date_str = row["on_date"]
    assert on_date_str.startswith("2030-01-15"), (
        f"on_date should start with 2030-01-15, got {on_date_str!r}"
    )
    assert Decimal(row["revenue"]) == Decimal("150.00")
    assert Decimal(row["cost"]) == Decimal("30.00")
    assert Decimal(row["profit"]) == Decimal("120.00")
    assert row["currency"] == "USD"


def test_profit_daily_requires_readwrite_for_post(api_client, readonly_key):
    """Sanity: GET is readonly, no body, no auth-state mutations.

    The schema change shouldn't accidentally lock out readonly. Verify
    the GET works with a readonly key as well as a readwrite one (this
    is already implicit in the previous tests, but explicit is better).
    """
    r = api_client.get(
        "/v2/reporting/profit-daily?limit=1",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
