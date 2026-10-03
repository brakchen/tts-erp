"""Tests for the extended ``GET /v2/reporting/missing-cost-products``.

Spec (docs/archive/procurement-ui-redesign.md §3.5):
- Back-compat: no ``?shop_pk=`` → global view, same as today.
- New: ``?shop_pk=X`` → scope to one shop.
- New: each row has ``missing_photo`` (bool) and the response carries a
  top-level ``total_missing_photo`` summary field for the tab badge.

Missing-cost membership is based on actual cost availability: active products
with neither a current manual cost nor a current cost snapshot. This keeps the
operator queue aligned with the scheduled cost resolver instead of an indirect
cross-system mapping state.

Regression coverage keeps the production ``status='ACTIVATE'`` shape because
a case-sensitive ``status = 'active'`` filter previously emptied the queue.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session


@pytest.fixture()
def seed_unmatched_active_product(db_engine):
    """Seed a channel_account + channel_product that should appear in the
    missing-cost-products list.

    Reproduces the production data shape: ``status='ACTIVATE'`` (TikTok's
    actual value) plus no manual cost and no current cost snapshot.
    """
    ext_acct = "TEST_acct_for_missing_cost"
    ext_prod = "TEST_prod_for_missing_cost"
    with Session(db_engine) as sess:
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status) "
                "VALUES ('tiktok', :ext, 'TEST acct', 'active')"
            ),
            {"ext": ext_acct},
        )
        acct_id = sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text("SELECT id FROM commerce.shops WHERE shop_id = :ext"),
            {"ext": ext_acct},
        ).scalar()
        # IMPORTANT: status='ACTIVATE' (uppercase) — same as production
        # data; this is the case the original ``= 'active'`` filter missed.
        sess.execute(  # pi-lens-ignore opengrep.sqlalchemy.sql-injection: text() + :param bound-param dict
            text(
                "INSERT INTO commerce.products_spu "
                "(shop_pk, spu_id, title, status) "
                "VALUES (:acct, :ext, 'TEST title', 'ACTIVATE')"
            ),
            {"acct": acct_id, "ext": ext_prod},
        )
        sess.commit()
    yield {"shop_pk": acct_id, "spu_id": ext_prod}


def test_status_activate_is_included(
    api_client, readonly_key, seed_unmatched_active_product
):
    """Regression: TikTok-stored 'ACTIVATE' must be picked up.

    Before the fix, ``WHERE cp.status = 'active'`` (lowercase) silently
    filtered out every product in production. The Needs cost tab was
    therefore always empty.
    """
    r = api_client.get(
        "/v2/reporting/missing-cost-products"
        f"?shop_pk={seed_unmatched_active_product['shop_pk']}"
        "&limit=200",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    ext_ids = [row["spu_id"] for row in r.json()["items"]]
    assert seed_unmatched_active_product["spu_id"] in ext_ids


def test_coverage_reports_actual_cost_availability(api_client, readonly_key):
    response = api_client.get(
        "/v2/reporting/coverage",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "costed_spus" in body
    assert "linked_spus" not in body


def test_product_with_current_cost_snapshot_is_excluded(
    api_client, readonly_key, db_engine, seed_unmatched_active_product
):
    """A current resolved cost removes the product from the operator queue."""
    with db_engine.begin() as conn:
        spu_pk = conn.execute(
            text(
                "SELECT id FROM commerce.products_spu "
                "WHERE shop_pk = :shop_pk AND spu_id = :spu_id"
            ),
            seed_unmatched_active_product,
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO reporting.product_cost_snapshots "
                "(spu_pk, cost_method, unit_cost, currency, valid_from, "
                " calculation_version, calculated_at) "
                "VALUES (:spu_pk, 'SOURCE_PRICE', 10, 'CNY', now(), 1, now())"
            ),
            {"spu_pk": spu_pk},
        )

    response = api_client.get(
        "/v2/reporting/missing-cost-products",
        params={"shop_pk": seed_unmatched_active_product["shop_pk"], "limit": 200},
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert response.status_code == 200, response.text
    assert seed_unmatched_active_product["spu_id"] not in {
        row["spu_id"] for row in response.json()["items"]
    }


def test_response_shape_no_filter(api_client, readonly_key):
    """No shop_pk → response has {items, total_missing_photo}.

    Items is a list of objects each carrying the legacy keys plus the
    new ``missing_photo`` flag.
    """
    r = api_client.get(
        "/v2/reporting/missing-cost-products?limit=5",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, dict)
    assert "items" in body and isinstance(body["items"], list)
    assert "total_missing_photo" in body and isinstance(
        body["total_missing_photo"], int
    )
    for row in body["items"]:
        assert "spu_pk" in row
        assert "spu_id" in row
        assert "title" in row
        assert "missing_photo" in row and isinstance(row["missing_photo"], bool)


def test_shop_pk_filter_runs_without_error(api_client, readonly_key):
    """shop_pk=… is accepted; SQL executes successfully.

    The query path must remain valid for arbitrary shop ids.
    """
    r = api_client.get(
        "/v2/reporting/missing-cost-products?shop_pk=1&limit=10",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body
    assert "total_missing_photo" in body


def test_items_have_missing_photo_column(api_client, readonly_key):
    """Every row carries a boolean ``missing_photo``."""
    r = api_client.get(
        "/v2/reporting/missing-cost-products?limit=200",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for row in body["items"]:
        assert "missing_photo" in row
        assert row["missing_photo"] in (True, False)


def test_total_missing_photo_consistent_with_items(api_client, readonly_key):
    """total_missing_photo == count of items with missing_photo=True."""
    r = api_client.get(
        "/v2/reporting/missing-cost-products?limit=200",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    actual = sum(1 for row in body["items"] if row["missing_photo"])
    assert body["total_missing_photo"] == actual


def test_total_missing_photo_consistent_with_items_filtered(api_client, readonly_key):
    """Same consistency check under shop_pk= filter."""
    r = api_client.get(
        "/v2/reporting/missing-cost-products?shop_pk=1&limit=200",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    actual = sum(1 for row in body["items"] if row["missing_photo"])
    assert body["total_missing_photo"] == actual


# --- 2026-09-05 mirror lane: image fields ---------------------------------


def test_response_rows_expose_image_fields(api_client, readonly_key):
    """Every missing-cost row carries main_image_url + image_url keys.

    The manual-costs page renders the SPU's TikTok main image from its
    local MinIO mirror (spu-image-mirror lane). ``image_url`` is null
    when the mirror job hasn't finished for a row yet — the frontend
    then shows the default missing-image icon instead of a broken img.
    """
    r = api_client.get(
        "/v2/reporting/missing-cost-products?limit=200",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for row in body["items"]:
        assert "main_image_url" in row
        assert "image_url" in row
        # image_url is either a resolvable URL or None (mirror pending).
        assert row["image_url"] is None or isinstance(row["image_url"], str)
