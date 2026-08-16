"""Repository protocols + PG implementations for tts-erp.

These wrap persist_order / persist_payment / etc. so the business
functions can be tested with in-memory fakes.
"""
from __future__ import annotations

from typing import Any, Protocol


class OrderRepository(Protocol):
    """Persistence for orders (orders table + order_items + order_shippings)."""

    def upsert(self, shop_id: str, order_raw: dict[str, Any]) -> bool:
        """Insert or update one order. Returns True if persisted, False if skipped."""
        ...

    def get(self, order_id: str) -> dict[str, Any] | None:
        ...

    def list(
        self,
        shop_id: str,
        *,
        status: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        ...


class PaymentRepository(Protocol):
    def upsert(self, shop_id: str, payment_raw: dict[str, Any]) -> bool: ...


class StatementRepository(Protocol):
    def upsert(self, shop_id: str, statement_raw: dict[str, Any]) -> bool: ...


class ReturnRepository(Protocol):
    def upsert(self, shop_id: str, return_raw: dict[str, Any]) -> bool: ...


class CancellationRepository(Protocol):
    def upsert(self, shop_id: str, cancel_raw: dict[str, Any]) -> bool: ...


class ShopRepository(Protocol):
    def upsert(self, shop_id: str, name: str, region: str, cipher: str, seller_type: str) -> None: ...
