"""Tests for the extended ``GET /v2/reporting/missing-cost-products``.

Spec (tech-doc/procurement-ui-redesign.md §3.5):
- Back-compat: no ``?channel_account_id=`` → global view, same as today.
- New: ``?channel_account_id=X`` → scope to one shop.
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
"""

from __future__ import annotations


def test_response_shape_no_filter(api_client, readonly_key):
    """No channel_account_id → response has {items, total_missing_photo}.

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
        assert "channel_product_id" in row
        assert "external_product_id" in row
        assert "title" in row
        assert "missing_photo" in row and isinstance(row["missing_photo"], bool)


def test_channel_account_id_filter_runs_without_error(api_client, readonly_key):
    """channel_account_id=… is accepted; SQL executes successfully.

    We can't assert which rows come back without knowing the view's
    data state (see module docstring), so we just confirm the query
    path is wired up.
    """
    r = api_client.get(
        "/v2/reporting/missing-cost-products?channel_account_id=1&limit=10",
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
    """Same consistency check under channel_account_id= filter."""
    r = api_client.get(
        "/v2/reporting/missing-cost-products?channel_account_id=1&limit=200",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    actual = sum(1 for row in body["items"] if row["missing_photo"])
    assert body["total_missing_photo"] == actual
