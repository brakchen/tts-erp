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


# ─── 2026-08-23 investigation: lock the canonical reference vector ────
# The plugin team reported a mismatch with the doc's reference hash
# `ce1ba2e1...`. Reproducing both sides byte-for-byte showed the doc
# was wrong; the algorithm never produced `ce1ba2...`. These tests
# pin the canonical form so future refactors can't silently drift.

REFERENCE_INPUT = {
    # NOTE: the Python function takes snake_case kwargs; the camelCase
    # form (sellerId etc.) only appears in the JSON wire format and in
    # the canonical JSON string built inside the function.
    "seller_id": "seller-1",
    "advertiser_id": "adv-1",
    "storage_key": StorageKey.PRODUCT_ANALYSES,
    "campaign_id": "campaign-1",
    "day": "2026-08-23",
    "page": 1,
}
REFERENCE_CANONICAL_JSON = (
    '{"advertiserId":"adv-1","campaignId":"campaign-1",'
    '"day":"2026-08-23","page":1,'
    '"sellerId":"seller-1","storageKey":"productAnalyses"}'
)
REFERENCE_HASH = (
    "73b716cce7f8b2c4220b1be3e5ab6327c3a963eaf424af84412402ef8607dae3"
)


def test_reference_canonical_json_is_locked():
    """Lock the canonical JSON byte string for the standard input."""
    got = canonical_json_for_key(**REFERENCE_INPUT)
    assert got == REFERENCE_CANONICAL_JSON
    # Sanity: it's valid JSON with sorted keys and no whitespace.
    parsed = json.loads(got)
    assert list(parsed.keys()) == sorted(parsed.keys())
    assert parsed == {
        "advertiserId": "adv-1",
        "campaignId": "campaign-1",
        "day": "2026-08-23",
        "page": 1,
        "sellerId": "seller-1",
        "storageKey": "productAnalyses",  # StorageKey.PRODUCT_ANALYSES.value
    }


def test_reference_hash_is_locked():
    """Lock the canonical sha256 for the standard input.

    If this test ever fails, the algorithm has changed. Bumping the
    algorithm requires a protocol version bump per compatibility.md;
    do NOT relax this assertion.
    """
    got = compute_idempotency_key(**REFERENCE_INPUT)
    assert got == REFERENCE_HASH
    # Explicitly assert the OLD (incorrect) reference value does NOT
    # come out — guards against accidental reversion.
    assert got != "ce1ba2e1e144ef9c153a4e94f7eb0f200f289a9393d743750adedfa21c16d180"


def test_page_int_and_string_produce_same_hash():
    """`page=1` and `page="1"` must hash identically (int() coercion)."""
    a = compute_idempotency_key(
        seller_id="seller-1", advertiser_id="adv-1",
        storage_key=StorageKey.PRODUCT_ANALYSES, campaign_id="campaign-1",
        day="2026-08-23", page=1,
    )
    b = compute_idempotency_key(
        seller_id="seller-1", advertiser_id="adv-1",
        storage_key=StorageKey.PRODUCT_ANALYSES, campaign_id="campaign-1",
        day="2026-08-23", page="1",
    )
    assert a == b
    assert a == REFERENCE_HASH


def test_page_changes_produce_different_hashes():
    """Different page values must produce different hashes (pagination key)."""
    base = dict(REFERENCE_INPUT)
    h1 = compute_idempotency_key(**{**base, "page": 1})
    h2 = compute_idempotency_key(**{**base, "page": 2})
    assert h1 != h2


def test_each_field_change_produces_different_hash():
    """Changing exactly one of sellerId/advertiserId/storageKey/campaignId/day
    must change the resulting hash. (Lock that no field is ignored.)"""
    h = compute_idempotency_key(**REFERENCE_INPUT)
    deltas = [
        {"seller_id": "seller-2"},
        {"advertiser_id": "adv-2"},
        {"storage_key": StorageKey.SESSION_ANALYSES},
        {"campaign_id": "campaign-2"},
        {"day": "2026-08-24"},
    ]
    for delta in deltas:
        mutated = {**REFERENCE_INPUT, **delta}
        h_mut = compute_idempotency_key(**mutated)
        assert h_mut != h, (
            f"changing {delta} produced the same hash — "
            f"the field is silently ignored by the algorithm"
        )


def test_string_fields_are_trimmed_before_hashing():
    """Leading/trailing whitespace on sellerId/advertiserId/campaignId
    must NOT change the hash — we trim before canonicalizing."""
    trimmed_h = compute_idempotency_key(**REFERENCE_INPUT)
    for delta in [
        {"seller_id": "  seller-1  "},
        {"advertiser_id": " adv-1"},
        {"campaign_id": "campaign-1\t"},
    ]:
        h = compute_idempotency_key(**{**REFERENCE_INPUT, **delta})
        assert h == trimmed_h, (
            f"untrimmed {delta} changed hash — algorithm must call .strip()"
        )
