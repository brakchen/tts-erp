"""Typed TikTok line-price normalization and immutable observation persistence."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.commerce import SalesOrderLinePriceObservation
from tts_erp_v2.db.models.integration import SyncIssue

OBSERVED = "OBSERVED"
MISSING = "MISSING"
NULL = "NULL"
INVALID_NEGATIVE = "INVALID_NEGATIVE"
INVALID_NON_NUMERIC = "INVALID_NON_NUMERIC"
INVALID_NONFINITE = "INVALID_NONFINITE"
INVALID_CURRENCY = "INVALID_CURRENCY"
DEFAULT_ONE_PER_LINE = "DEFAULT_ONE_PER_LINE"
INVALID_ZERO = "INVALID_ZERO"
INVALID_NON_INTEGER = "INVALID_NON_INTEGER"
INVALID_BOOLEAN = "INVALID_BOOLEAN"
NOT_GIFT = "NOT_GIFT"
GIFT = "GIFT"
UNKNOWN = "UNKNOWN"

PRICE_FIELDS = ("original_price", "sale_price")


def _decimal(value: Any) -> tuple[Decimal | None, str]:
    if value is None:
        return None, MISSING
    if isinstance(value, bool):
        return None, INVALID_NON_NUMERIC
    if isinstance(value, float) and not math.isfinite(value):
        return None, INVALID_NONFINITE
    if value == "":
        return None, NULL
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None, INVALID_NON_NUMERIC
    if not amount.is_finite():
        return None, INVALID_NONFINITE
    if amount < 0:
        return None, INVALID_NEGATIVE
    return amount, OBSERVED


def _quantity(raw: dict[str, Any]) -> tuple[Decimal | None, Decimal | None, str]:
    if "quantity" not in raw:
        return None, Decimal(1), DEFAULT_ONE_PER_LINE
    value = raw["quantity"]
    if value is None:
        return None, None, NULL
    if isinstance(value, bool):
        return None, None, INVALID_BOOLEAN
    amount, status = _decimal(value)
    if amount is None:
        return None, None, status
    if amount == 0:
        return amount, None, INVALID_ZERO
    if amount < 0:
        return amount, None, "INVALID_NEGATIVE"
    if amount != amount.to_integral_value():
        return amount, None, INVALID_NON_INTEGER
    return amount, amount, OBSERVED


def _field(raw: dict[str, Any], name: str) -> tuple[Decimal | None, str, str | None]:
    if name not in raw:
        return None, MISSING, None
    value = raw[name]
    embedded_currency: str | None = None
    if isinstance(value, dict):
        embedded_currency = value.get("currency")
        value = value.get("amount")
    amount, status = _decimal(value)
    return amount, status, embedded_currency


def _canonical_value(raw: dict[str, Any], name: str) -> dict[str, Any]:
    if name not in raw:
        return {"presence": "absent"}
    value = raw[name]
    if isinstance(value, dict):
        return {
            "presence": "value" if value.get("amount") is not None else "null",
            "type": "object",
            "amount": str(value.get("amount")),
            "currency": value.get("currency"),
        }
    return {
        "presence": "null" if value is None else "value",
        "type": type(value).__name__,
        "value": str(value) if value is not None else None,
    }


@dataclass(frozen=True)
class PriceLineObservation:
    shop_pk: int
    order_pk: int
    external_line_id: str
    raw_record_id: int
    source_endpoint: str
    source_payload_hash: str
    semantic_observation_hash: str
    source_order_version_at: datetime | None
    source_captured_at: datetime
    spu_pk: int | None
    raw_quantity: Decimal | None
    effective_quantity: Decimal | None
    quantity_status: str
    line_status_raw: str | None
    parent_payment_status: str
    gift_status: str
    original_price_native: Decimal | None
    paid_price_native: Decimal | None
    currency: str | None
    original_price_status: str
    paid_price_status: str

    def as_insert_values(self) -> dict[str, Any]:
        return self.__dict__.copy()


def normalize_price_line(
    raw: dict[str, Any],
    *,
    shop_pk: int,
    order_pk: int,
    external_line_id: str,
    raw_record_id: int,
    source_endpoint: str,
    source_payload_hash: str,
    source_captured_at: datetime,
    source_order_version_at: datetime | None,
    parent_status: str | None,
    parent_currency: str | None,
    spu_pk: int | None = None,
) -> PriceLineObservation:
    """Normalize only TikTok ``line_items`` authority fields.

    The legacy ``unit_price`` parser remains untouched. This object is an
    immutable audit row and therefore retains valid native amounts even when
    currency provenance makes a metric unusable.
    """
    original, original_status, original_embedded_currency = _field(raw, "original_price")
    paid, paid_status, paid_embedded_currency = _field(raw, "sale_price")
    explicit_currency = raw.get("currency")
    currencies = {
        str(value).upper()
        for value in (explicit_currency, original_embedded_currency, paid_embedded_currency)
        if value
    }
    currency = str(explicit_currency or original_embedded_currency or paid_embedded_currency).upper() if currencies else None
    currency_valid = len(currencies) == 1 and (not parent_currency or currency == str(parent_currency).upper())
    if not currency_valid:
        if original_status == OBSERVED:
            original_status = INVALID_CURRENCY
        if paid_status == OBSERVED:
            paid_status = INVALID_CURRENCY

    raw_quantity, effective_quantity, quantity_status = _quantity(raw)
    gift_value = raw.get("is_gift") if "is_gift" in raw else None
    gift_status = NOT_GIFT if gift_value is False else GIFT if gift_value is True else UNKNOWN
    canonical = {
        "shop_pk": shop_pk,
        "order_pk": order_pk,
        "external_line_id": external_line_id,
        "original_price": _canonical_value(raw, "original_price"),
        "sale_price": _canonical_value(raw, "sale_price"),
        "quantity": _canonical_value(raw, "quantity"),
        "currency": currency,
        "parent_status": parent_status,
        "parent_currency": parent_currency,
        "source_order_version_at": source_order_version_at.isoformat() if source_order_version_at else None,
        "gift": _canonical_value(raw, "is_gift"),
        "line_status": raw.get("display_status") or raw.get("line_status"),
        "normalizer_version": "tiktok-line-price-v1",
    }
    semantic_hash = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PriceLineObservation(
        shop_pk=shop_pk,
        order_pk=order_pk,
        external_line_id=external_line_id,
        raw_record_id=raw_record_id,
        source_endpoint=source_endpoint,
        source_payload_hash=source_payload_hash,
        semantic_observation_hash=semantic_hash,
        source_order_version_at=source_order_version_at,
        source_captured_at=source_captured_at.astimezone(UTC),
        spu_pk=spu_pk,
        raw_quantity=raw_quantity,
        effective_quantity=effective_quantity,
        quantity_status=quantity_status,
        line_status_raw=raw.get("display_status") or raw.get("line_status"),
        parent_payment_status=parent_status or UNKNOWN,
        gift_status=gift_status,
        original_price_native=original,
        paid_price_native=paid,
        currency=currency,
        original_price_status=original_status,
        paid_price_status=paid_status,
    )


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def upsert_price_observation(
    session: Session, observation: PriceLineObservation
) -> bool:
    """Insert an immutable observation; return false for semantic replay."""
    stmt = pg_insert(SalesOrderLinePriceObservation).values(observation.as_insert_values())
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["shop_pk", "order_pk", "external_line_id", "semantic_observation_hash"]
    )
    result = session.execute(stmt)
    return bool(result.rowcount)


def record_price_issue(
    session: Session,
    *,
    issue_type: str,
    shop_pk: int,
    order_pk: int,
    external_line_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Use the existing safe issue table without retaining buyer payloads."""
    external_id = f"{shop_pk}:{order_pk}:{external_line_id}"
    existing = session.execute(
        select(SyncIssue)
        .where(SyncIssue.job_name == "tiktok.order_prices")
        .where(SyncIssue.issue_type == issue_type)
        .where(SyncIssue.external_id == external_id)
        .where(SyncIssue.resolved_at.is_(None))
    ).scalar_one_or_none()
    if existing is not None:
        existing.details = details or {}
        return
    session.add(
        SyncIssue(
            job_name="tiktok.order_prices",
            issue_type=issue_type,
            external_id=external_id,
            details=details or {},
        )
    )


def persist_price_observation(
    session: Session,
    *,
    raw_line: dict[str, Any],
    shop_pk: int,
    order_pk: int,
    raw_record_id: int,
    source_endpoint: str,
    source_captured_at: datetime,
    source_order_version_at: datetime | None,
    parent_status: str | None,
    parent_currency: str | None,
    spu_pk: int | None,
) -> PriceLineObservation:
    """Normalize, diagnose, and insert one line observation in this transaction."""
    external_line_id = str(raw_line.get("line_id") or raw_line.get("id") or "")
    observation = normalize_price_line(
        raw_line,
        shop_pk=shop_pk,
        order_pk=order_pk,
        external_line_id=external_line_id,
        raw_record_id=raw_record_id,
        source_endpoint=source_endpoint,
        source_payload_hash=payload_hash(raw_line),
        source_captured_at=source_captured_at,
        source_order_version_at=source_order_version_at,
        parent_status=parent_status,
        parent_currency=parent_currency,
        spu_pk=spu_pk,
    )
    if source_order_version_at is None:
        record_price_issue(session, issue_type="MISSING_SOURCE_VERSION", shop_pk=shop_pk, order_pk=order_pk, external_line_id=external_line_id)
    if observation.gift_status == UNKNOWN:
        record_price_issue(session, issue_type="UNKNOWN_GIFT_SIGNAL", shop_pk=shop_pk, order_pk=order_pk, external_line_id=external_line_id)
    if observation.quantity_status not in {OBSERVED, DEFAULT_ONE_PER_LINE}:
        record_price_issue(session, issue_type="INVALID_QUANTITY", shop_pk=shop_pk, order_pk=order_pk, external_line_id=external_line_id, details={"status": observation.quantity_status})
    if observation.original_price_status != OBSERVED:
        record_price_issue(session, issue_type="MISSING_OR_INVALID_ORIGINAL_PRICE", shop_pk=shop_pk, order_pk=order_pk, external_line_id=external_line_id, details={"status": observation.original_price_status})
    if observation.paid_price_status != OBSERVED:
        record_price_issue(session, issue_type="MISSING_OR_INVALID_PAID_PRICE", shop_pk=shop_pk, order_pk=order_pk, external_line_id=external_line_id, details={"status": observation.paid_price_status})
    if observation.original_price_status == INVALID_CURRENCY or observation.paid_price_status == INVALID_CURRENCY:
        record_price_issue(session, issue_type="INVALID_CURRENCY", shop_pk=shop_pk, order_pk=order_pk, external_line_id=external_line_id)
    upsert_price_observation(session, observation)
    return observation


__all__ = [
    "DEFAULT_ONE_PER_LINE", "GIFT", "INVALID_CURRENCY", "MISSING", "NOT_GIFT",
    "OBSERVED", "PriceLineObservation", "normalize_price_line", "payload_hash",
    "record_price_issue", "persist_price_observation", "upsert_price_observation",
]
