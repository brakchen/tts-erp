"""Contract tests for TikTok line-price normalization and observation identity."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tts_erp_v2.jobs.tiktok.orders import incoming_order_is_stale
from tts_erp_v2.jobs.tiktok.order_prices import (
    DEFAULT_ONE_PER_LINE,
    GIFT,
    INVALID_CURRENCY,
    MISSING,
    NOT_GIFT,
    OBSERVED,
    PriceLineObservation,
    normalize_price_line,
)


CAPTURED = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _line(**overrides: object) -> dict:
    value: dict = {
        "line_id": "TEST_LINE_1",
        "original_price": "10.00",
        "sale_price": "8.00",
        "currency": "USD",
        "quantity": 2,
        "is_gift": False,
        "display_status": "AWAITING_SHIPMENT",
    }
    value.update(overrides)
    return value


def _normalize(raw: dict, **overrides: object) -> PriceLineObservation:
    params = {
        "shop_pk": 11,
        "order_pk": 22,
        "external_line_id": "TEST_LINE_1",
        "raw_record_id": 33,
        "source_endpoint": "ORDER_SEARCH",
        "source_payload_hash": "raw-hash",
        "source_captured_at": CAPTURED,
        "source_order_version_at": datetime(2026, 10, 5, 11, 0, tzinfo=UTC),
        "parent_status": "AWAITING_SHIPMENT",
        "parent_currency": "USD",
        "spu_pk": 44,
    }
    params.update(overrides)
    return normalize_price_line(raw, **params)


def test_normalizer_accepts_zero_and_keeps_each_native_amount() -> None:
    observation = _normalize(_line(original_price="0", sale_price="0"))

    assert observation.original_price_native == Decimal("0")
    assert observation.paid_price_native == Decimal("0")
    assert observation.original_price_status == OBSERVED
    assert observation.paid_price_status == OBSERVED
    assert observation.effective_quantity == Decimal("2")


def test_normalizer_distinguishes_missing_quantity_from_invalid_present_quantity() -> None:
    observed = _normalize(_line(quantity=2))
    assert observed.quantity_status == OBSERVED

    omitted = _normalize({key: value for key, value in _line().items() if key != "quantity"})
    assert omitted.quantity_status == DEFAULT_ONE_PER_LINE
    assert omitted.effective_quantity == Decimal("1")

    defaulted = _normalize(_line(quantity=None))
    assert defaulted.quantity_status == "NULL"
    assert defaulted.effective_quantity is None

    invalid = _normalize(_line(quantity=0))
    assert invalid.quantity_status == "INVALID_ZERO"
    assert invalid.effective_quantity is None


def test_normalizer_uses_strict_boolean_gift_mapping() -> None:
    assert _normalize(_line(is_gift=False)).gift_status == NOT_GIFT
    assert _normalize(_line(is_gift=True)).gift_status == GIFT
    assert _normalize(_line(is_gift="false")).gift_status == "UNKNOWN"
    assert _normalize(_line(is_gift=None)).gift_status == "UNKNOWN"


def test_normalizer_rejects_missing_or_conflicting_currency_without_guessing() -> None:
    missing = _normalize(_line(), parent_currency="USD")
    assert missing.original_price_status == OBSERVED
    assert missing.currency == "USD"

    conflicting = _normalize(_line(currency="SGD"), parent_currency="USD")
    assert conflicting.currency == "SGD"
    assert conflicting.original_price_status == INVALID_CURRENCY
    assert conflicting.paid_price_status == INVALID_CURRENCY
    assert conflicting.original_price_native == Decimal("10.00")


def test_semantic_identity_ignores_capture_raw_id_and_endpoint() -> None:
    first = _normalize(_line(), raw_record_id=33, source_endpoint="ORDER_SEARCH")
    retry = _normalize(
        _line(), raw_record_id=99, source_endpoint="ORDER_DETAIL", source_captured_at=CAPTURED.replace(hour=13)
    )

    assert first.semantic_observation_hash == retry.semantic_observation_hash
    assert first.source_payload_hash == retry.source_payload_hash


def test_order_version_guard_rejects_older_and_missing_incoming_versions() -> None:
    newer = datetime(2026, 10, 6, tzinfo=UTC)
    older = datetime(2026, 10, 5, tzinfo=UTC)

    assert incoming_order_is_stale(older, newer)
    assert incoming_order_is_stale(None, newer)
    assert not incoming_order_is_stale(newer, newer)
    assert not incoming_order_is_stale(newer, None)


def test_new_missing_price_does_not_borrow_old_value() -> None:
    valid = _normalize(_line(sale_price="8.00"))
    missing = _normalize(
        {key: value for key, value in _line().items() if key != "sale_price"},
        source_order_version_at=datetime(2026, 10, 6, tzinfo=UTC),
    )

    assert valid.paid_price_native == Decimal("8.00")
    assert missing.paid_price_native is None
    assert missing.paid_price_status == MISSING
    assert valid.semantic_observation_hash != missing.semantic_observation_hash
