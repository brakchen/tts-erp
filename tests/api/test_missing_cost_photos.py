"""Tests for the extended ``GET /v2/reporting/missing-cost-products``.

Spec (tech-doc/procurement-ui-redesign.md §3.5):
- Back-compat: no ``?shop_pk=`` → global view, same as today.
- New: ``?shop_pk=X`` → scope to one shop.
- New: each row has ``missing_photo`` (bool) and the response carries a
  top-level ``total_missing_photo`` summary field for the tab badge.

Note on data state: the existing SQL uses ``NOT EXISTS (SELECT 1 FROM
linkage.effective_product_links ...)``, and that view LEFT-JOINs over
every channel_product (so every product appears as a row in the view).
The legacy endpoint therefore typically returns 0 items unless the
operator has cleaned up `link_overrides` / `product_links` for a
product. We test the *contract* changes here (response shape + filter
plumbing + photo column presence) rather than asserting specific item
counts, so the tests stay valid regardless of the legacy view's
data-state quirks.

Regression coverage added 2026-09-01 — both bugs reproduced the
"Needs cost tab empty" symptom in production:
- ``cp.status = 'active'`` missed TikTok's actual ``'ACTIVATE'``
  (uppercase) — fixed by switching to ILIKE.
- ``NOT EXISTS (... effective_product_links ...)`` was tautologically
  false because the view is a LEFT JOIN that emits one row per
  channel_product regardless of link presence — fixed by adding
  ``AND epl.effective_relation_type IS NOT NULL``.
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
    actual value) plus NO manual cost and NO ``effective_relation_type``
    link. The seeded product must be returned by the endpoint, proving
    both the case-insensitive status filter and the
    ``effective_relation_type IS NOT NULL`` filter work.
    """
    ext_acct = "TEST_acct_for_missing_cost"
    ext_prod = "TEST_prod_for_missing_cost"
    with Session(db_engine) as sess:
        sess.execute(
            text(
                "INSERT INTO commerce.shops "
                "(platform, shop_id, account_name, status) "
                "VALUES ('tiktok', :ext, 'TEST acct', 'active')"
            ),
            {"ext": ext_acct},
        )
        acct_id = sess.execute(
            text(
                "SELECT id FROM commerce.shops "
                "WHERE shop_id = :ext"
            ),
            {"ext": ext_acct},
        ).scalar()
        # IMPORTANT: status='ACTIVATE' (uppercase) — same as production
        # data; this is the case the original ``= 'active'`` filter missed.
        sess.execute(
            text(
                "INSERT INTO commerce.products_spu "
                "(shop_pk, spu_id, title, status) "
                "VALUES (:acct, :ext, 'TEST title', 'ACTIVATE')"
            ),
            {"acct": acct_id, "ext": ext_prod},
        )
        sess.commit()
    yield {"shop_pk": acct_id, "spu_id": ext_prod}


def test_status_activate_is_included(api_client, readonly_key, seed_unmatched_active_product):
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


def test_unlinked_product_survives_left_join_view(
    api_client, readonly_key, seed_unmatched_active_product
):
    """Regression: the LEFT-JOIN view emits one row per channel_product
    regardless of whether a real link exists. Without filtering on
    ``effective_relation_type IS NOT NULL``, ``NOT EXISTS (... epl ...)``
    was tautologically false and the product was wrongly treated as
    already linked (so excluded from the missing-cost list).

    The fixture creates a product with NO manual cost and NO link at all,
    so it must appear. The fix filters out the LEFT-JOIN phantom rows.
    """
    r = api_client.get(
        "/v2/reporting/missing-cost-products"
        f"?shop_pk={seed_unmatched_active_product['shop_pk']}"
        "&limit=200",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    cp_id = None
    for row in items:
        if row["spu_id"] == seed_unmatched_active_product["spu_id"]:
            cp_id = row["spu_pk"]
            break
    assert cp_id is not None, "seeded product should appear in items"
    # Sanity-check the view really does emit a phantom row for this
    # product (proving the test setup actually exercises the LEFT JOIN).
    # Without that phantom row, this regression test would pass even on
    # the unfixed code.
    from sqlalchemy import create_engine as _ce
    # Use a fresh connection so we don't depend on the handler's session.
    with _ce(__import__("os").environ["TTS_ERP_DB_URL"]).connect() as conn:
        phantom = conn.execute(
            text(
                "SELECT effective_relation_type FROM linkage.effective_product_links "
                "WHERE spu_pk = :cp"
            ),
            {"cp": cp_id},
        ).first()
    assert phantom is not None, (
        "expected the LEFT-JOIN view to emit at least one phantom row "
        "for the seeded product — otherwise the regression test cannot "
        "distinguish fixed from broken code"
    )
    assert phantom[0] is None, (
        f"expected NULL effective_relation_type on phantom row, got {phantom[0]!r}"
    )


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
    assert "total_missing_photo" in body and isinstance(body["total_missing_photo"], int)
    for row in body["items"]:
        assert "spu_pk" in row
        assert "spu_id" in row
        assert "title" in row
        assert "missing_photo" in row and isinstance(row["missing_photo"], bool)


def test_shop_pk_filter_runs_without_error(api_client, readonly_key):
    """shop_pk=… is accepted; SQL executes successfully.

    We can't assert which rows come back without knowing the view's
    data state (see module docstring), so we just confirm the query
    path is wired up.
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


def test_total_missing_photo_consistent_with_items_filtered(
    api_client, readonly_key
):
    """Same consistency check under shop_pk= filter."""
    r = api_client.get(
        "/v2/reporting/missing-cost-products?shop_pk=1&limit=200",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    actual = sum(1 for row in body["items"] if row["missing_photo"])
    assert body["total_missing_photo"] == actual
