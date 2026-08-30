"""Tests for scope validation (per-token scopes[]).

Each token's scopes[] controls which (sellerId, advertiserId) pairs it
may act on. Empty scopes = unrestricted (operator default). `*` = wildcard.
Otherwise, every reference must be matched by at least one scope entry.
"""

from __future__ import annotations

import hashlib
import secrets

import psycopg
import pytest

from analytics_sync.auth import scope_grants

# ─── Pure-function tests on scope_grants ──────────────────────────────


def test_empty_scopes_grant_unrestricted():
    assert scope_grants((), seller_id="any", advertiser_id="any") is True


def test_wildcard_scope_grants_unrestricted():
    assert scope_grants(("*",), seller_id="any", advertiser_id="any") is True


def test_seller_scope_matches_same_seller():
    assert scope_grants(("seller:abc",), seller_id="abc", advertiser_id="adv-1") is True


def test_seller_scope_rejects_different_seller():
    assert (
        scope_grants(("seller:abc",), seller_id="xyz", advertiser_id="adv-1") is False
    )


def test_advertiser_scope_matches_same_advertiser():
    assert (
        scope_grants(("advertiser:adv-1",), seller_id="s", advertiser_id="adv-1")
        is True
    )


def test_advertiser_scope_rejects_different_advertiser():
    assert (
        scope_grants(("advertiser:adv-1",), seller_id="s", advertiser_id="adv-2")
        is False
    )


def test_multi_scope_union_semantics():
    """Token with multiple scopes grants union — any one matching suffices."""
    scopes = ("seller:a", "advertiser:adv-2")
    assert scope_grants(scopes, seller_id="a", advertiser_id="adv-2") is True
    assert scope_grants(scopes, seller_id="b", advertiser_id="adv-2") is False
    assert scope_grants(scopes, seller_id="a", advertiser_id="adv-1") is False


def test_unmentioned_dimension_is_unrestricted():
    """A scope entry restricts only the dimension it names. Dimensions
    the token does not mention are unrestricted.

    - Token [seller:abc] does not constrain advertiser.
    - Token [advertiser:adv-1] does not constrain seller.
    """
    assert (
        scope_grants(("seller:abc",), seller_id="abc", advertiser_id="anything") is True
    )
    assert (
        scope_grants(("seller:abc",), seller_id="abc", advertiser_id="adv-other")
        is True
    )
    assert (
        scope_grants(("advertiser:adv-1",), seller_id="any", advertiser_id="adv-1")
        is True
    )
    assert (
        scope_grants(("advertiser:adv-1",), seller_id="other", advertiser_id="adv-1")
        is True
    )


def test_both_dimensions_must_be_satisfied():
    """A token with BOTH seller and advertiser scopes requires both
    to match the request."""
    scopes = ("seller:abc", "advertiser:adv-1")
    # Both match → True.
    assert scope_grants(scopes, seller_id="abc", advertiser_id="adv-1") is True
    # Seller mismatch → False.
    assert scope_grants(scopes, seller_id="xyz", advertiser_id="adv-1") is False
    # Advertiser mismatch → False.
    assert scope_grants(scopes, seller_id="abc", advertiser_id="adv-2") is False
    # Both mismatch → False.
    assert scope_grants(scopes, seller_id="xyz", advertiser_id="adv-2") is False


# ─── End-to-end: token with restricted scope ─────────────────────────


@pytest.fixture()
def seller_scoped_token(db_url: str):
    """Mint a token whose scopes restrict it to seller='TEST_scoped_seller'."""
    plaintext = f"anlsync_TEST_{secrets.token_urlsafe(16)}"
    h = hashlib.sha256(plaintext.encode()).hexdigest()
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO security.api_keys (key_prefix, key_hash, name, role, status)
            VALUES (%s, %s, %s, 'readwrite', 'active')
            """,
            (plaintext[:16], h, "TEST_scoped"),
        )
        conn.commit()
    return plaintext


def test_restricted_token_can_access_its_scope(fastapi_client, seller_scoped_token):
    resp = fastapi_client.get(
        "/v1/analytics/sync/cursor?sellerId=TEST_scoped_seller&advertiserId=adv-1",
        headers={"Authorization": f"Bearer {seller_scoped_token}"},
    )
    assert resp.status_code == 200


def test_restricted_token_cannot_access_other_seller(
    fastapi_client, seller_scoped_token
):
    resp = fastapi_client.get(
        "/v1/analytics/sync/cursor?sellerId=TEST_other_seller&advertiserId=adv-1",
        headers={"Authorization": f"Bearer {seller_scoped_token}"},
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "SCOPE_DENIED"


def test_restricted_token_cannot_upload_for_other_seller(
    fastapi_client, seller_scoped_token
):
    """A token scoped to seller A cannot upload records for seller B
    even with a valid token."""
    from datetime import date

    from analytics_sync.domain import StorageKey, compute_idempotency_key

    seller = "TEST_other_seller"
    idem = compute_idempotency_key(
        seller_id=seller,
        advertiser_id="adv-1",
        storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="c-1",
        day=date(2026, 8, 23),
        page=1,
    )
    resp = fastapi_client.post(
        "/v1/analytics/sync/batches",
        headers={"Authorization": f"Bearer {seller_scoped_token}"},
        json={
            "protocolVersion": 1,
            "scope": {"sellerId": seller, "advertiserId": "adv-1"},
            "records": [
                {
                    "idempotencyKey": idem,
                    "storageKey": "productAnalyses",
                    "campaignId": "c-1",
                    "day": "2026-08-23",
                    "page": 1,
                    "endpoint": "/",
                    "method": "POST",
                    "response": {},
                    "source": "x",
                    "capturedAt": "2026-08-23T00:00:00Z",
                    "schemaVersion": 1,
                }
            ],
        },
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "SCOPE_DENIED"
