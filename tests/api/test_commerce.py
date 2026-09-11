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
- Cleanup of tables the autouse doesn't cover (``products_sku``,
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
    """Seed a TEST_ channel_account + products_spu + variants + sales_orders.

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

    with db_engine.begin() as sess:
        # pi-lens-ignore: python-sql-injection — literal SQL, only :ext/:acct/:cp/:so bound
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status, data_source) "
                "VALUES ('tiktok', :ext, 'TEST acct', 'active', 'api')"
            ),
            {"ext": ext_acct},
        )
        acct_id = sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("SELECT id FROM commerce.shops WHERE shop_id = :ext"),
            {"ext": ext_acct},
        ).scalar()

        # pi-lens-ignore: python-sql-injection — literal SQL, only :acct/:ext bound
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "INSERT INTO commerce.products_spu "
                "(shop_pk, spu_id, title, status) "
                "VALUES (:acct, :ext, 'TEST title', 'active')"
            ),
            {"acct": acct_id, "ext": ext_prod},
        )
        cp_id = sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("SELECT id FROM commerce.products_spu WHERE spu_id = :ext"),
            {"ext": ext_prod},
        ).scalar()

        # pi-lens-ignore: python-sql-injection — literal SQL, only :cp/:ext bound
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "INSERT INTO commerce.products_sku "
                "(spu_pk, sku_id, seller_sku, variant_name) "
                "VALUES (:cp, :ext, 'TEST_SKU', 'TEST variant')"
            ),
            {"cp": cp_id, "ext": ext_var},
        )

        # pi-lens-ignore: python-sql-injection — literal SQL, only :acct/:ext bound
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "INSERT INTO commerce.sales_orders "
                "(shop_pk, order_id, status, currency, "
                " payment_amount, total_amount) "
                "VALUES (:acct, :ext, 'PAID', 'USD', 12.34, 15.00)"
            ),
            {"acct": acct_id, "ext": ext_order},
        )
        order_id = sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("SELECT id FROM commerce.sales_orders WHERE order_id = :ext"),
            {"ext": ext_order},
        ).scalar()

        # pi-lens-ignore: python-sql-injection — literal SQL, only :so/:cp/:cv bound
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "INSERT INTO commerce.sales_order_lines "
                "(order_pk, external_line_id, spu_pk, "
                " sku_pk, quantity, unit_price) "
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
    wipes products_spu / shops by TEST_ external ids;
    this fixture adds the 3 tables the autouse doesn't know about.
    """
    with db_engine.begin() as sess:
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "DELETE FROM commerce.sales_order_lines "
                "WHERE order_pk IN ("
                "  SELECT id FROM commerce.sales_orders "
                "  WHERE order_id LIKE 'TEST_commerce_%'"
                ")"
            )
        )
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "DELETE FROM commerce.sales_orders "
                "WHERE order_id LIKE 'TEST_commerce_%'"
            )
        )
        # pi-lens-ignore: python-sql-injection — literal SQL, LIKE prefix is constant
        sess.execute(
            text(
                "DELETE FROM commerce.products_sku WHERE sku_id LIKE 'TEST_commerce_%'"
            )
        )


# ---------------------------------------------------------------------------
# GET /v2/commerce/channel-accounts
# ---------------------------------------------------------------------------


def test_list_shops_with_data(api_client, readonly_key, seed_commerce_rows):
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
    assert row["shop_id"] == seeded["ext_acct"]
    assert row["status"] == "active"


def test_list_shops_shop_id_is_silently_ignored(
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


def test_list_shops_filter_by_platform(api_client, readonly_key, seed_commerce_rows):
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


def test_get_channel_account_200_with_row(api_client, readonly_key, seed_commerce_rows):
    """Lines 173-176: get by id returns the seeded row."""
    r = api_client.get(
        f"/v2/commerce/channel-accounts/{seed_commerce_rows['account_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == seed_commerce_rows["account_id"]
    assert body["shop_id"] == seed_commerce_rows["ext_acct"]


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
    assert body["shop_pk"] == 999999998
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
        f"/v2/commerce/channel-accounts/{seed_commerce_rows['account_id']}/order-stats",
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


def test_list_products_spu_with_data(api_client, readonly_key, seed_commerce_rows):
    """Lines 187-197: response body built by _row_to_channel_product."""
    r = api_client.get(
        f"/v2/commerce/channel-products?shop_pk={seed_commerce_rows['account_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    by_id = {row["id"]: row for row in r.json()}
    seeded = seed_commerce_rows
    assert seeded["product_id"] in by_id
    row = by_id[seeded["product_id"]]
    assert row["shop_pk"] == seeded["account_id"]
    assert row["spu_id"] == seeded["ext_prod"]
    assert row["title"] == "TEST title"
    assert row["status"] == "active"


def test_list_products_spu_filter_by_account(
    api_client, readonly_key, seed_commerce_rows
):
    """shop_pk= filter must scope to the seeded account."""
    r = api_client.get(
        f"/v2/commerce/channel-products?shop_pk={seed_commerce_rows['account_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert all(row["shop_pk"] == seed_commerce_rows["account_id"] for row in rows)
    assert seed_commerce_rows["product_id"] in [row["id"] for row in rows]


def test_list_products_spu_shop_id_is_silently_ignored(
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
        f"?shop_pk={seed_commerce_rows['account_id']}&shop_id=9999999",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    # shop_pk filter still applied (shop_id silently dropped).
    rows = r.json()
    assert all(row["shop_pk"] == seed_commerce_rows["account_id"] for row in rows)
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
    assert body["spu_id"] == seed_commerce_rows["ext_prod"]


def test_list_products_sku_with_data(api_client, readonly_key, seed_commerce_rows):
    """Lines 219-220: variants endpoint body."""
    r = api_client.get(
        f"/v2/commerce/channel-products/{seed_commerce_rows['product_id']}/variants",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 1
    row = rows[0]
    assert row["spu_pk"] == seed_commerce_rows["product_id"]
    assert row["sku_id"] == "TEST_commerce_var"
    assert row["seller_sku"] == "TEST_SKU"
    assert row["variant_name"] == "TEST variant"


def test_list_products_sku_empty(api_client, readonly_key):
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


def test_list_sales_orders_with_data(api_client, readonly_key, seed_commerce_rows):
    """Lines 258-261: response body built by _row_to_sales_order.

    Scoped by shop_pk so the seeded TEST_ row is the only
    match — production rows outnumber the default limit and would push
    a NULL-order_modify_time seed below the cut.
    """
    r = api_client.get(
        f"/v2/commerce/sales-orders?shop_pk={seed_commerce_rows['account_id']}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    by_id = {row["id"]: row for row in r.json()}
    seeded = seed_commerce_rows
    assert seeded["order_id"] in by_id
    row = by_id[seeded["order_id"]]
    assert row["shop_pk"] == seeded["account_id"]
    assert row["order_id"] == seeded["ext_order"]
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
    that the WHERE clause plumbed through. We scope by shop_pk
    first to make the membership check deterministic.
    """
    r = api_client.get(
        f"/v2/commerce/sales-orders"
        f"?shop_pk={seed_commerce_rows['account_id']}&status=PAID",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert seed_commerce_rows["order_id"] in ids


def test_list_sales_orders_shop_id_is_silently_ignored(
    api_client, readonly_key, seed_commerce_rows
):
    """AGENTS.md §2.4: ``?shop_id=`` MUST NOT filter on /sales-orders either.

    Pass shop_pk so the membership assertion is deterministic,
    then add a bogus shop_id and confirm it does NOT narrow the scope.
    """
    r = api_client.get(
        f"/v2/commerce/sales-orders"
        f"?shop_pk={seed_commerce_rows['account_id']}&shop_id=9999999",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    # shop_pk filter still applied.
    assert all(row["shop_pk"] == seed_commerce_rows["account_id"] for row in rows)
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
    assert body["order_id"] == seed_commerce_rows["ext_order"]
    assert float(body["payment_amount"]) == 12.34


def test_list_sales_order_lines_with_data(api_client, readonly_key, seed_commerce_rows):
    """Lines 272-273: response body built by SalesOrderLineOut."""
    r = api_client.get(
        f"/v2/commerce/sales-orders/{seed_commerce_rows['order_id']}/lines",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 1
    row = rows[0]
    assert row["order_pk"] == seed_commerce_rows["order_id"]
    assert row["external_line_id"] == "TEST_line_1"
    assert row["spu_pk"] == seed_commerce_rows["product_id"]
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


def test_shops_anonymous_is_401(api_client):
    """Smoke: any /v2/commerce/* requires readonly+."""
    r = api_client.get("/v2/commerce/channel-accounts")
    assert r.status_code == 401


# 2026-09-06 all-SPU tab: channel-products carries current cost + mirror
# ---------------------------------------------------------------------------


def test_list_products_spu_includes_cost_and_image_fields(
    api_client, readonly_key, db_engine
):
    """channel-products rows expose the effective manual cost + mirror URL.

    The 全部 SPU tab needs per-SPU status / current cost / currency and a
    renderable image. Back-compat: the legacy fields stay; the new ones
    are null when absent.
    """
    cp_id = _seed_spu(db_engine, "TEST_commerce_costfield")
    # Seed an effective manual cost for that SPU.
    with db_engine.begin() as sess:
        sess.execute(
            text(
                "INSERT INTO procurement.manual_product_costs "
                "(spu_pk, unit_cost, currency, valid_from, valid_to) "
                "VALUES (:cp, '12.50', 'CNY', now(), NULL)"
            ),
            {"cp": cp_id},
        )

    r = api_client.get(
        "/v2/commerce/channel-products?limit=500",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    row = next((x for x in r.json() if x["id"] == cp_id), None)
    assert row is not None, f"seeded SPU missing: {r.text[:300]}"
    assert row["unit_cost"] is not None
    assert row["currency"] == "CNY"
    assert row["cost_method"] == "MANUAL_ENTRY"
    assert "image_url" in row and "main_image_url" in row


def _seed_spu(db_engine, external_id: str) -> int:
    """Seed a minimal TEST_ shop + SPU; return the SPU id."""
    with db_engine.begin() as sess:
        sess.execute(
            text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status, data_source) "
                "VALUES ('tiktok', :ext, 'TEST acct', 'active', 'api')"
            ),
            {"ext": external_id},
        )
        acct_id = sess.execute(
            text("SELECT id FROM commerce.shops WHERE shop_id = :ext"),
            {"ext": external_id},
        ).scalar()
        sess.execute(
            text(
                "INSERT INTO commerce.products_spu "
                "(shop_pk, spu_id, title, status) "
                "VALUES (:acct, :ext, 'TEST title', 'active')"
            ),
            {"acct": acct_id, "ext": external_id},
        )
        cp_id = sess.execute(
            text("SELECT id FROM commerce.products_spu WHERE spu_id = :ext"),
            {"ext": external_id},
        ).scalar()
    return cp_id


# ---------------------------------------------------------------------------
# GET /v2/commerce/channel-products — sort / order (2026-09-06 all-SPU tab)
# ---------------------------------------------------------------------------


@pytest.fixture()
def seed_spus_with_times(db_engine):
    """Three TEST_ SPUs under one shop with distinct source times + costs.

    The all-SPU catalogue needs clickable sorting on created / updated
    / unit_cost; this fixture pins deterministic values so the response
    order is assertable without touching other shops' rows.
    """
    ext_acct = "TEST_MCSORT_acct"
    with db_engine.begin() as sess:
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status, data_source) "
                "VALUES ('tiktok', :ext, 'TEST acct', 'active', 'api')"
            ),
            {"ext": ext_acct},
        )
        acct_id = sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("SELECT id FROM commerce.shops WHERE shop_id = :ext"),
            {"ext": ext_acct},
        ).scalar()
        spu_ids = {}
        # spu_a: oldest created, mid updated, highest cost
        # spu_b: mid created, newest updated, lowest cost
        # spu_c: newest created, oldest updated, mid cost (no manual cost row)
        for ext, title, created, updated in (
            (
                "TEST_MCSORT_a",
                "A title",
                "2026-01-01T00:00:00+00:00",
                "2026-06-01T00:00:00+00:00",
            ),
            (
                "TEST_MCSORT_b",
                "B title",
                "2026-02-01T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00",
            ),
            (
                "TEST_MCSORT_c",
                "C title",
                "2026-03-01T00:00:00+00:00",
                "2026-03-01T00:00:00+00:00",
            ),
        ):
            sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
                text(
                    "INSERT INTO commerce.products_spu "
                    "(shop_pk, spu_id, title, status, "
                    " source_created_at, source_updated_at) "
                    "VALUES (:acct, :ext, :title, 'active', :created, :updated)"
                ),
                {
                    "acct": acct_id,
                    "ext": ext,
                    "title": title,
                    "created": created,
                    "updated": updated,
                },
            )
            pk = sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
                text("SELECT id FROM commerce.products_spu WHERE spu_id = :ext"),
                {"ext": ext},
            ).scalar()
            spu_ids[ext] = pk
        # Costs: A=30 (highest), B=10 (lowest), C none.
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "INSERT INTO procurement.manual_product_costs "
                "(spu_pk, unit_cost, currency, valid_from, valid_to) "
                "VALUES (:a, 30.0, 'CNY', now(), NULL), "
                "       (:b, 10.0, 'CNY', now(), NULL)"
            ),
            {"a": spu_ids["TEST_MCSORT_a"], "b": spu_ids["TEST_MCSORT_b"]},
        )

    yield {"acct_id": acct_id, "spu_ids": spu_ids}

    with db_engine.begin() as sess:
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "DELETE FROM procurement.manual_product_costs "
                "WHERE spu_pk IN ("
                "  SELECT id FROM commerce.products_spu "
                "  WHERE shop_pk = :acct)"
            ),
            {"acct": acct_id},
        )
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("DELETE FROM commerce.products_spu WHERE shop_pk = :acct"),
            {"acct": acct_id},
        )
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("DELETE FROM commerce.shops WHERE id = :acct"),
            {"acct": acct_id},
        )


def _sort_products(api_client, readonly_key, acct_id, **query):
    qs = "&".join(f"{k}={v}" for k, v in query.items())
    url = f"/v2/commerce/channel-products?limit=50&shop_pk={acct_id}"
    if qs:
        url += "&" + qs
    r = api_client.get(url, headers={"Authorization": f"Bearer {readonly_key}"})
    assert r.status_code == 200, r.text
    return [row["spu_id"] for row in r.json()]


def test_channel_products_sort_created_at_desc(
    api_client, readonly_key, seed_spus_with_times
):
    """sort=created_at&order=desc → newest source_created_at first (c,b,a)."""
    acct = seed_spus_with_times["acct_id"]
    assert _sort_products(
        api_client, readonly_key, acct, sort="created_at", order="desc"
    ) == ["TEST_MCSORT_c", "TEST_MCSORT_b", "TEST_MCSORT_a"]


def test_channel_products_sort_created_at_asc(
    api_client, readonly_key, seed_spus_with_times
):
    acct = seed_spus_with_times["acct_id"]
    assert _sort_products(
        api_client, readonly_key, acct, sort="created_at", order="asc"
    ) == ["TEST_MCSORT_a", "TEST_MCSORT_b", "TEST_MCSORT_c"]


def test_channel_products_sort_updated_at_desc(
    api_client, readonly_key, seed_spus_with_times
):
    """sort=updated_at&order=desc → newest source_updated_at first (b,a,c)."""
    acct = seed_spus_with_times["acct_id"]
    assert _sort_products(
        api_client, readonly_key, acct, sort="updated_at", order="desc"
    ) == ["TEST_MCSORT_b", "TEST_MCSORT_a", "TEST_MCSORT_c"]


def test_channel_products_sort_unit_cost_desc(
    api_client, readonly_key, seed_spus_with_times
):
    """sort=unit_cost&order=desc → highest cost first; cost-less SPU last.

    NULL manual costs sort to the tail on both directions.
    """
    acct = seed_spus_with_times["acct_id"]
    assert _sort_products(
        api_client, readonly_key, acct, sort="unit_cost", order="desc"
    ) == ["TEST_MCSORT_a", "TEST_MCSORT_b", "TEST_MCSORT_c"]


def test_channel_products_sort_unit_cost_asc(
    api_client, readonly_key, seed_spus_with_times
):
    acct = seed_spus_with_times["acct_id"]
    assert _sort_products(
        api_client, readonly_key, acct, sort="unit_cost", order="asc"
    ) == ["TEST_MCSORT_b", "TEST_MCSORT_a", "TEST_MCSORT_c"]


def test_channel_products_sort_rejects_unknown_field(
    api_client, readonly_key, seed_spus_with_times
):
    """Unknown sort keys must 422 (FastAPI Literal validation), not pass through."""
    acct = seed_spus_with_times["acct_id"]
    qs = "sort=banana&order=desc"
    r = api_client.get(
        f"/v2/commerce/channel-products?limit=50&shop_pk={acct}&{qs}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Status: Chinese labels / filter / sort (2026-09-06 all-SPU catalogue)
# ---------------------------------------------------------------------------

_STATUS_SPUS = {
    "TEST_MCST_activate": ("商家产品", "ACTIVATE"),
    "TEST_MCST_deleted": ("删除产品", "DELETED"),
    "TEST_MCST_seller": ("停售产品", "SELLER_DEACTIVATED"),
}


@pytest.fixture()
def seed_spus_statuses(db_engine):
    """Three TEST_ SPUs under one shop, one per upstream status value."""
    ext_acct = "TEST_MCST_acct"
    with db_engine.begin() as sess:
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status, data_source) "
                "VALUES ('tiktok', :ext, 'TEST acct', 'active', 'api')"
            ),
            {"ext": ext_acct},
        )
        acct_id = sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("SELECT id FROM commerce.shops WHERE shop_id = :ext"),
            {"ext": ext_acct},
        ).scalar()
        spu_ids = {}
        for ext, (title, status) in _STATUS_SPUS.items():
            sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
                text(
                    "INSERT INTO commerce.products_spu "
                    "(shop_pk, spu_id, title, status, "
                    " source_created_at, source_updated_at) "
                    "VALUES (:acct, :ext, :title, :status, now(), now())"
                ),
                {"acct": acct_id, "ext": ext, "title": title, "status": status},
            )
            pk = sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
                text("SELECT id FROM commerce.products_spu WHERE spu_id = :ext"),
                {"ext": ext},
            ).scalar()
            spu_ids[ext] = pk

    yield {"acct_id": acct_id, "spu_ids": spu_ids}

    with db_engine.begin() as sess:
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("DELETE FROM commerce.products_spu WHERE shop_pk = :acct"),
            {"acct": acct_id},
        )
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("DELETE FROM commerce.shops WHERE id = :acct"),
            {"acct": acct_id},
        )


def _products_for_status(api_client, readonly_key, acct_id, **query):
    qs = "&".join(f"{k}={v}" for k, v in query.items())
    url = f"/v2/commerce/channel-products?limit=50&shop_pk={acct_id}"
    if qs:
        url += "&" + qs
    r = api_client.get(url, headers={"Authorization": f"Bearer {readonly_key}"})
    assert r.status_code == 200, r.text
    return [row["spu_id"] for row in r.json()]


def test_channel_products_filter_by_status(
    api_client, readonly_key, seed_spus_statuses
):
    """?status=DELETED returns only DELETED rows (raw upstream code filter)."""
    acct = seed_spus_statuses["acct_id"]
    got = _products_for_status(api_client, readonly_key, acct, status="DELETED")
    assert got == ["TEST_MCST_deleted"], f"expected only DELETED row, got {got}"


def test_channel_products_filter_by_status_all_statuses_present(
    api_client, readonly_key, seed_spus_statuses
):
    """Without ?status= all three statuses come back."""
    acct = seed_spus_statuses["acct_id"]
    got = _products_for_status(api_client, readonly_key, acct)
    assert set(got) == set(_STATUS_SPUS.keys())


def test_channel_products_sort_status_asc(api_client, readonly_key, seed_spus_statuses):
    """sort=status&order=asc → 商家(ACTIVATE) first, then 下架(DELETED),
    then 停售(SELLER_DEACTIVATED) — the fixed weight order."""
    acct = seed_spus_statuses["acct_id"]
    got = _products_for_status(
        api_client, readonly_key, acct, sort="status", order="asc"
    )
    assert got == ["TEST_MCST_activate", "TEST_MCST_deleted", "TEST_MCST_seller"], got


def test_channel_products_sort_status_desc(
    api_client, readonly_key, seed_spus_statuses
):
    acct = seed_spus_statuses["acct_id"]
    got = _products_for_status(
        api_client, readonly_key, acct, sort="status", order="desc"
    )
    assert got == ["TEST_MCST_seller", "TEST_MCST_deleted", "TEST_MCST_activate"], got


def test_channel_products_exposes_total_count_header(
    api_client, readonly_key, seed_spus_with_times
):
    """channel-products returns X-Total-Count matching the filtered set.

    2026-09-06 pager: the bare-array body is a stable external contract,
    so the matching row count travels in a header instead. With a status
    filter applied the header reflects the FILTERED total, not the whole
    table.
    """
    acct = seed_spus_with_times["acct_id"]
    r = api_client.get(
        f"/v2/commerce/channel-products?limit=2&shop_pk={acct}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert "X-Total-Count" in r.headers, "X-Total-Count header missing"
    # The fixture seeds 3 TEST_ SPUs under this shop.
    assert r.headers["X-Total-Count"] == "3", r.headers["X-Total-Count"]
    # limit=2 paged the body but the header still counts all 3.
    assert len(r.json()) == 2

    # Offset paging: skipping 2 leaves 1 row.
    r2 = api_client.get(
        f"/v2/commerce/channel-products?limit=2&offset=2&shop_pk={acct}",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r2.status_code == 200
    assert len(r2.json()) == 1
    assert r2.headers["X-Total-Count"] == "3"


# ---------------------------------------------------------------------------
# has_orders filter (2026-09-06 "仅看有单" catalogue toggle)
# ---------------------------------------------------------------------------


@pytest.fixture()
def seed_spu_one_ordered(db_engine):
    """Two TEST_ SPUs under one shop; only ONE appears on an order line."""
    ext_acct = "TEST_MCHO_acct"
    ext_sold = "TEST_MCHO_sold"
    ext_never = "TEST_MCHO_never"
    with db_engine.begin() as sess:
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status, data_source) "
                "VALUES ('tiktok', :ext, 'TEST acct', 'active', 'api')"
            ),
            {"ext": ext_acct},
        )
        acct_id = sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("SELECT id FROM commerce.shops WHERE shop_id = :ext"),
            {"ext": ext_acct},
        ).scalar()
        for ext in (ext_sold, ext_never):
            sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
                text(
                    "INSERT INTO commerce.products_spu "
                    "(shop_pk, spu_id, title, status) "
                    "VALUES (:acct, :ext, 'T', 'ACTIVATE')"
                ),
                {"acct": acct_id, "ext": ext},
            )
        sold_pk = sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("SELECT id FROM commerce.products_spu WHERE spu_id = :ext"),
            {"ext": ext_sold},
        ).scalar()
        order_id = sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "INSERT INTO commerce.sales_orders "
                "(shop_pk, order_id, status, currency, payment_amount) "
                "VALUES (:acct, :ext, 'PAID', 'USD', 5.00) RETURNING id"
            ),
            {"acct": acct_id, "ext": "TEST_MCHO_order"},
        ).scalar()
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "INSERT INTO commerce.sales_order_lines "
                "(order_pk, external_line_id, spu_pk, quantity) "
                "VALUES (:o, 'L1', :spu, 1)"
            ),
            {"o": order_id, "spu": sold_pk},
        )

    yield {"acct_id": acct_id, "sold": ext_sold, "never": ext_never}

    with db_engine.begin() as sess:
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "DELETE FROM commerce.sales_order_lines WHERE order_pk IN "
                "(SELECT id FROM commerce.sales_orders WHERE order_id = 'TEST_MCHO_order')"
            )
        )
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("DELETE FROM commerce.sales_orders WHERE order_id = 'TEST_MCHO_order'")
        )
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("DELETE FROM commerce.products_spu WHERE shop_pk = :acct"),
            {"acct": acct_id},
        )
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("DELETE FROM commerce.shops WHERE id = :acct"),
            {"acct": acct_id},
        )


def test_channel_products_has_orders_true_only_sold(
    api_client, readonly_key, seed_spu_one_ordered
):
    acct = seed_spu_one_ordered["acct_id"]
    got = _sort_products(api_client, readonly_key, acct, has_orders="true")
    assert got == [seed_spu_one_ordered["sold"]], got


def test_channel_products_has_orders_default_returns_all(
    api_client, readonly_key, seed_spu_one_ordered
):
    acct = seed_spu_one_ordered["acct_id"]
    got = _sort_products(api_client, readonly_key, acct)
    assert set(got) == {
        seed_spu_one_ordered["sold"],
        seed_spu_one_ordered["never"],
    }
