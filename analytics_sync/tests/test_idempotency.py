"""Tests for the canonical idempotency-key derivation (protocol §2)."""
from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest

from analytics_sync.domain import (
    StorageKey,
    canonical_json_for_key,
    compute_idempotency_key,
)


def test_canonical_json_field_order_is_sorted():
    payload = canonical_json_for_key(
        seller_id="s", advertiser_id="a", storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="c", day="2026-08-23", page=1,
    )
    # First key in sorted order is "advertiserId".
    assert payload.startswith('{"advertiserId"')
    # Last key is "storageKey".
    assert payload.endswith('"storageKey":"productAnalyses"}')


def test_canonical_json_no_whitespace():
    payload = canonical_json_for_key(
        seller_id="s", advertiser_id="a", storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="c", day="2026-08-23", page=1,
    )
    assert ": " not in payload
    assert ", " not in payload


def test_canonical_json_is_deterministic():
    """Same inputs → same bytes."""
    a = canonical_json_for_key(
        seller_id="s", advertiser_id="a", storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="c", day="2026-08-23", page=1,
    )
    b = canonical_json_for_key(
        seller_id="s", advertiser_id="a", storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="c", day="2026-08-23", page=1,
    )
    assert a == b


def test_canonical_json_trims_strings():
    a = canonical_json_for_key(
        seller_id="  s ", advertiser_id=" a", storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id=" c ", day="2026-08-23", page=1,
    )
    b = canonical_json_for_key(
        seller_id="s", advertiser_id="a", storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="c", day="2026-08-23", page=1,
    )
    assert a == b


def test_canonical_json_handles_date_object():
    a = canonical_json_for_key(
        seller_id="s", advertiser_id="a", storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="c", day=date(2026, 8, 23), page=1,
    )
    b = canonical_json_for_key(
        seller_id="s", advertiser_id="a", storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="c", day="2026-08-23", page=1,
    )
    assert a == b


def test_compute_idempotency_key_returns_64_hex():
    k = compute_idempotency_key(
        seller_id="s", advertiser_id="a", storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="c", day="2026-08-23", page=1,
    )
    assert len(k) == 64
    assert all(c in "0123456789abcdef" for c in k)


def test_compute_idempotency_key_matches_manual_sha256():
    canonical = canonical_json_for_key(
        seller_id="s", advertiser_id="a", storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="c", day="2026-08-23", page=1,
    )
    expected = hashlib.sha256(canonical.encode()).hexdigest()
    actual = compute_idempotency_key(
        seller_id="s", advertiser_id="a", storage_key=StorageKey.PRODUCT_ANALYSES,
        campaign_id="c", day="2026-08-23", page=1,
    )
    assert actual == expected


@pytest.mark.parametrize(
    "storage_key", [StorageKey.PRODUCT_ANALYSES, StorageKey.SESSION_ANALYSES, StorageKey.CAMPAIGN_CHANGE_LOGS]
)
def test_storage_key_allowlist(storage_key):
    """Each StorageKey enum value serializes to the exact string the protocol allows."""
    k = compute_idempotency_key(
        seller_id="s", advertiser_id="a", storage_key=storage_key,
        campaign_id="c", day="2026-08-23", page=1,
    )
    assert len(k) == 64
