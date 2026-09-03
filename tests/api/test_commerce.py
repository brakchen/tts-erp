"""Coverage lift for ``tts_erp_v2/api/v2/commerce.py``.

Targets the previously-uncovered handler bodies (lines 114, 127,
160-165, 173-176, 187-197, 205-208, 219-220, 258-261, 272-273, 299-300
per the 2026-09-03 coverage report) plus the AGENTS.md §2.4 contract
that ``?shop_id=`` is silently ignored on v2 read endpoints.

Patterns reused from existing api/ tests:
- ``api_client`` / ``readonly_key`` fixtures from tests/api/conftest.py
  (FastAPI TestClient + auth header).
- TEST_-prefixed identifiers everywhere so the autouse ``_isolate_state``
  wipes them at teardown.
- Cleanup of tables the autouse doesn't cover (``channel_product_variants``,
  ``sales_orders``, ``sales_order_lines``) goes through ``db_engine.begin()``
  directly, mirroring the analytics v2 contract test pattern.

Not in scope here: the role-gating tests already locked by
``tests/api/test_admin.py`` / ``test_spu_images.py`` — the commerce
router is all GETs classified ``readonly`` by middleware so a single
``readonly_key`` round-trip is enough.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def seed_commerce_rows(db_engine):
    """Seed a TEST_ channel_account + channel_products + variants + sales_orders.

    Yields the dict of new ids so tests can address them. Cleanup runs in
    teardown through ``db_engine.begin()`` so it survives the request
    handler's transactional commit.
    """
    ext_acct = "TEST_commerce_acct"
    ext_prod = "TEST_commerce_prod"
    ext_var = "TEST_commerce_var"
    ext_order = "TEST_commerce_order"

    # Wipe any prior leftovers (idempotent across re-runs).
    _wipe(db_engine)

    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, only :ext/:acct/:cp/:so bound
        conn.execute(
            text(
                "INSERT INTO commerce.channel_accounts "
                "(platform, external_account_id, account_name, status) "
                "VALUES ('tiktok', :ext, 'TEST acct', 'active')"
            ),
            {"ext": ext_acct},
        )
        acct_id = conn.execute(
            text(
                "SELECT id FROM commerce.channel_accounts "
                "WHERE external_account_id = :ext"
            ),
            {"ext": ext_acct},
        ).scalar()

        # pi-lens-ignore: python-sql-injection — literal SQL, only :acct/:ext bound
        conn.execute(
            text(
                "INSERT INTO commerce.channel_products "
                "(channel_account_id, external_product_id, title, status) "
                "VALUES (:acct, :ext, 'TEST title', 'active')"
            ),
            {"acct": acct_id, "ext": ext_prod},
        )
        cp_id = conn.execute(
            text(
                "SELECT id FROM commerce.channel_products "
                "WHERE external_product_id = :ext"
            ),
            {"ext": ext_prod},
        ).scalar()

        # pi-lens-ignore: python-sql-injection — literal SQL, only :cp/:ext bound
        conn.execute(
            text(
                "INSERT INTO commerce.channel_product_variants "
                "(channel_product_id, external_variant_id, seller_sku, variant_name) "
                "VALUES (:cp, :ext, 'TEST_SKU', 'TEST variant')"
            ),
            {"cp": cp_id, "ext": ext_var},
        )

        # pi-lens-ignore: python-sql-injection — literal SQL, only :acct/:ext bound
        conn.execute(
            text(
                "INSERT INTO commerce.sales_orders "
                "(channel_account_id, external_order_id, status, currency, "
                " payment_amount, total_amount) "
                "VALUES (:acct, :ext, 'PAID', 'USD', 12.34, 15.00)"
            ),
            {"acct": acct_id, "ext": ext_order},
        )
        order_id = conn.execute(
            text(
                "SELECT id FROM commerce.sales_orders "
                "WHERE external_order_id = :ext"
            ),
            {"ext": ext_order},
        ).scalar()

        # pi-lens-ignore: python-sql-injection — literal SQL, only :so/:cp/:cv bound
        conn.execute(
            text(
                "INSERT INTO commerce.sales_order_lines "
                "(sales_order_id, external_line_id, channel_product_id, "
                " channel_product_variant_id, quantity, unit_price) "
                "VALUES (:so, 'TEST_line_1', :cp, :cv, 2, 6.17)"
            ),
            {"so": order_id, "cp": cp_id, "cv": None},
        )

    yield {
        "account_id": acct_id,
        "product_id": cp_id,
        "order_id": order_id,
        "ext_acct": ext_acct,
        "ext_prod": ext_prod,
        "ext_order": ext_order,
    }
    _wipe(db_engine)


def _wipe(db_engine) -> None:
    """Delete TEST_-prefixed rows in FK order (lines → orders → ... → accounts).

    The autouse ``_isolate_state`` in tests/api/conftest.py already
    wipes channel_products / channel_accounts by TEST_ external ids;
    this fixture adds the 3 tables the autouse doesn't know about.
    """
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text(
                "DELETE FROM commerce.sales_order_lines "
                "WHERE sales_order_id IN ("
                "  SELECT id FROM commerce.sales_orders "
                "  WHERE external_order_id LIKE 'TEST_commerce_%'"
                ")"
            )
        )
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text(
                "DELETE FROM commerce.sales_orders "
                "WHERE external_order_id LIKE 'TEST_commerce_%'"
            )
        )
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        conn.execute(
            text(
                "DELETE FROM commerce.channel_product_variants "
                "WHERE external_variant_id LIKE 'TEST_commerce_%'"
            )
        )


# ---------------------------------------------------------------------------
# GET /v2/commerce/channel-accounts
# ---------------------------------------------------------------------------


def test_list_channel_accounts_with_data(api_client, readonly_key, seed_commerce_rows):
    """Lines 114 / 127: the response body is built by _row_to_channel_account."""
    r = api_client.get(
        "/v2/commerce/channel-accounts?limit=200",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    by_id = {row["id"]: row for row in rows}
    seeded = seed_commerce_rows
    assert seeded["account_id"] in by_id, (
        f"seeded account {seeded['account_id']} missing from response: {by_id!r}"
    )
    row = by_id[seeded["account_id"]]
    assert row["platform"] == "tiktok"
    assert row["external_account_id"] == seeded["ext_acct"]
    assert row["status"] == "active"


def test_list_channel_accounts_shop_id_is_silently_ignored(
    api_client, readonly_key, seed_commerce_rows
):
    """AGENTS.md §2.4: ``?shop_id=`` MUST NOT filter — it's a v1 leftover.

    The seeded TEST_ account must still appear when an arbitrary shop_id
    is supplied (otherwise the operator would have to learn which prefix
    works).
    """
    r = api_client.get(
        "/v2/commerce/channel-accounts?shop_id=9999999",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    seeded = seed_commerce_rows
    ids = {row["id"] for row in r.json()}
    assert seeded["account_id"] in ids, (
        "?shop_id= silently filtered — AGENTS §2.4 contract broken"
    )


def test_list_channel_accounts_filter_by_platform(
    api_client, readonly_key, seed_commerce_rows
):
    """Happy path of the platform= filter (does match the seeded row)."""
    r = api_client.get(
        "/v2/commerce/channel-accounts?platform=tiktok",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert seed_commerce_rows["account_id"] in ids


def test_get_channel_account_404(api_client, readonly_key):
    """Line 163: 404 for an unknown id (the .first() returns None branch)."""
    r = api_client.get(
        "/v2/commerce/channel-accounts/999999999",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 404, r.text
    assert "channel account not found" in r.text


def test_get_channel_account_200_with_row(
    api_client, readonly_key, seed_commerce_rows
):
    """Lines 173-176: get by id returns the seeded row."""
    r = api_client.get(
        f"/v2/commerce/channel-accounts/{seed_commerce_rows['account_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == seed_commerce_rows["account_id"]
    assert body["external_account_id"] == seed_commerce_rows["ext_acct"]


def test_channel_account_order_stats_empty(
    api_client, readonly_key, seed_commerce_rows
):
    """Lines 299-300: account with no orders → {0, '0'}.

    The seeded account HAS orders, so use an unused account id instead.
    """
    r = api_client.get(
        "/v2/commerce/channel-accounts/999999998/order-stats",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["channel_account_id"] == 999999998
    assert body["distinct_orders"] == 0
    # SUM of empty set → COALESCE → 0.0, serialized as Decimal str.
    assert body["total_payment_amount"] in ("0", "0.0000", "0.0")


def test_channel_account_order_stats_with_data(
    api_client, readonly_key, seed_commerce_rows
):
    """Lines 299-300: account with 1 order → {1, sum of payment_amount}.

    Regression guard for the COALESCE(SUM(...), 0) — when orders exist
    the value must be the numeric SUM, not 0 or NULL.
    """
    r = api_client.get(
        f"/v2/commerce/channel-accounts/"
        f"{seed_commerce_rows['account_id']}/order-stats",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["distinct_orders"] == 1
    # payment_amount was inserted as 12.34 → numeric(20,4) → "12.3400".
    assert float(body["total_payment_amount"]) == 12.34


# ---------------------------------------------------------------------------
# GET /v2/commerce/channel-products
# ---------------------------------------------------------------------------


def test_list_channel_products_with_data(
    api_client, readonly_key, seed_commerce_rows
):
    """Lines 187-197: response body built by _row_to_channel_product."""
    r = api_client.get(
        f"/v2/commerce/channel-products"
        f"?channel_account_id={seed_commerce_rows['account_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    by_id = {row["id"]: row for row in r.json()}
    seeded = seed_commerce_rows
    assert seeded["product_id"] in by_id
    row = by_id[seeded["product_id"]]
    assert row["channel_account_id"] == seeded["account_id"]
    assert row["external_product_id"] == seeded["ext_prod"]
    assert row["title"] == "TEST title"
    assert row["status"] == "active"


def test_list_channel_products_filter_by_account(
    api_client, readonly_key, seed_commerce_rows
):
    """channel_account_id= filter must scope to the seeded account."""
    r = api_client.get(
        f"/v2/commerce/channel-products"
        f"?channel_account_id={seed_commerce_rows['account_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert all(
        row["channel_account_id"] == seed_commerce_rows["account_id"]
        for row in rows
    )
    assert seed_commerce_rows["product_id"] in [row["id"] for row in rows]


def test_list_channel_products_shop_id_is_silently_ignored(
    api_client, readonly_key, seed_commerce_rows
):
    """AGENTS.md §2.4: ``?shop_id=`` MUST NOT filter on /channel-products either.

    We pass an unscoped shop_id and verify the seeded product_id still
    appears in the response — proving shop_id is silently dropped. The
    unfiltered list may include production rows; the assertion is on
    membership, not exhaustiveness.
    """
    r = api_client.get(
        f"/v2/commerce/channel-products"
        f"?channel_account_id={seed_commerce_rows['account_id']}&shop_id=9999999",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    # channel_account_id filter still applied (shop_id silently dropped).
    rows = r.json()
    assert all(
        row["channel_account_id"] == seed_commerce_rows["account_id"]
        for row in rows
    )
    assert seed_commerce_rows["product_id"] in [row["id"] for row in rows]


def test_get_channel_product_404(api_client, readonly_key):
    """Line 207: 404 path when .first() returns None."""
    r = api_client.get(
        "/v2/commerce/channel-products/999999999",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 404, r.text
    assert "channel product not found" in r.text


def test_get_channel_product_200(api_client, readonly_key, seed_commerce_rows):
    """Lines 205-208: get by id returns the seeded row."""
    r = api_client.get(
        f"/v2/commerce/channel-products/{seed_commerce_rows['product_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == seed_commerce_rows["product_id"]
    assert body["external_product_id"] == seed_commerce_rows["ext_prod"]


def test_list_channel_product_variants_with_data(
    api_client, readonly_key, seed_commerce_rows
):
    """Lines 219-220: variants endpoint body."""
    r = api_client.get(
        f"/v2/commerce/channel-products/"
        f"{seed_commerce_rows['product_id']}/variants",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 1
    row = rows[0]
    assert row["channel_product_id"] == seed_commerce_rows["product_id"]
    assert row["external_variant_id"] == "TEST_commerce_var"
    assert row["seller_sku"] == "TEST_SKU"
    assert row["variant_name"] == "TEST variant"


def test_list_channel_product_variants_empty(api_client, readonly_key):
    """Empty result for an unused product id (lines 219-220 with [] list)."""
    r = api_client.get(
        "/v2/commerce/channel-products/999999999/variants",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == []


# ---------------------------------------------------------------------------
# GET /v2/commerce/sales-orders
# ---------------------------------------------------------------------------


def test_list_sales_orders_with_data(
    api_client, readonly_key, seed_commerce_rows
):
    """Lines 258-261: response body built by _row_to_sales_order.

    Scoped by channel_account_id so the seeded TEST_ row is the only
    match — production rows outnumber the default limit and would push
    a NULL-source_updated_at seed below the cut.
    """
    r = api_client.get(
        f"/v2/commerce/sales-orders"
        f"?channel_account_id={seed_commerce_rows['account_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    by_id = {row["id"]: row for row in r.json()}
    seeded = seed_commerce_rows
    assert seeded["order_id"] in by_id
    row = by_id[seeded["order_id"]]
    assert row["channel_account_id"] == seeded["account_id"]
    assert row["external_order_id"] == seeded["ext_order"]
    assert row["status"] == "PAID"
    assert row["currency"] == "USD"
    # Decimal serialization keeps the column scale.
    assert float(row["payment_amount"]) == 12.34
    assert float(row["total_amount"]) == 15.00


def test_list_sales_orders_filter_by_status(
    api_client, readonly_key, seed_commerce_rows
):
    """status= filter scopes correctly to the seeded PAID row.

    The seed is the only row for the test account, so any filter
    narrower than that returns either the row or [] — both demonstrate
    that the WHERE clause plumbed through. We scope by channel_account_id
    first to make the membership check deterministic.
    """
    r = api_client.get(
        f"/v2/commerce/sales-orders"
        f"?channel_account_id={seed_commerce_rows['account_id']}&status=PAID",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert seed_commerce_rows["order_id"] in ids


def test_list_sales_orders_shop_id_is_silently_ignored(
    api_client, readonly_key, seed_commerce_rows
):
    """AGENTS.md §2.4: ``?shop_id=`` MUST NOT filter on /sales-orders either.

    Pass channel_account_id so the membership assertion is deterministic,
    then add a bogus shop_id and confirm it does NOT narrow the scope.
    """
    r = api_client.get(
        f"/v2/commerce/sales-orders"
        f"?channel_account_id={seed_commerce_rows['account_id']}&shop_id=9999999",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    # channel_account_id filter still applied.
    assert all(
        row["channel_account_id"] == seed_commerce_rows["account_id"]
        for row in rows
    )
    assert seed_commerce_rows["order_id"] in [row["id"] for row in rows]


def test_get_sales_order_404(api_client, readonly_key):
    """Line 261: 404 path."""
    r = api_client.get(
        "/v2/commerce/sales-orders/999999999",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 404, r.text
    assert "sales order not found" in r.text


def test_get_sales_order_200(api_client, readonly_key, seed_commerce_rows):
    """Lines 258-261: get by id returns the seeded row."""
    r = api_client.get(
        f"/v2/commerce/sales-orders/{seed_commerce_rows['order_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == seed_commerce_rows["order_id"]
    assert body["external_order_id"] == seed_commerce_rows["ext_order"]
    assert float(body["payment_amount"]) == 12.34


def test_list_sales_order_lines_with_data(
    api_client, readonly_key, seed_commerce_rows
):
    """Lines 272-273: response body built by SalesOrderLineOut."""
    r = api_client.get(
        f"/v2/commerce/sales-orders/{seed_commerce_rows['order_id']}/lines",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 1
    row = rows[0]
    assert row["sales_order_id"] == seed_commerce_rows["order_id"]
    assert row["external_line_id"] == "TEST_line_1"
    assert row["channel_product_id"] == seed_commerce_rows["product_id"]
    assert float(row["quantity"]) == 2
    assert float(row["unit_price"]) == 6.17


def test_list_sales_order_lines_empty(api_client, readonly_key):
    """Empty result for an unused order id (lines 272-273 with [] list)."""
    r = api_client.get(
        "/v2/commerce/sales-orders/999999999/lines",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == []


# ---------------------------------------------------------------------------
# Auth gating (smoke)
# ---------------------------------------------------------------------------


def test_channel_accounts_anonymous_is_401(api_client):
    """Smoke: any /v2/commerce/* requires readonly+."""
    r = api_client.get("/v2/commerce/channel-accounts")
    assert r.status_code == 401
