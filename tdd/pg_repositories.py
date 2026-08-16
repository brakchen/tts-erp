"""Production PG-backed repository implementations.

These wrap the existing persist_* functions in tts_erp.py. We don't
re-implement the SQL — the original code has been validated against
real TikTok responses for months. We just adapt the module-level
functions to the OrderRepository/PaymentRepository/etc. protocols.

Performance note: each upsert() opens its own DB connection. For
high-volume sync (50 orders/sync), this is inefficient. Future
optimization: accept a connection in the constructor.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make tts_erp.py importable
TTS_ERP_ROOT = Path(__file__).resolve().parent.parent
if str(TTS_ERP_ROOT) not in sys.path:
    sys.path.insert(0, str(TTS_ERP_ROOT))

import tts_erp  # noqa: E402


class PgOrderRepository:
    """Wraps tts_erp.persist_order. Implements OrderRepository protocol."""

    def upsert(self, shop_id: str, order_raw: dict) -> bool:
        return tts_erp.persist_order(shop_id, order_raw)


class PgPaymentRepository:
    """Wraps tts_erp.persist_payment."""

    def upsert(self, shop_id: str, payment_raw: dict) -> bool:
        return tts_erp.persist_payment(shop_id, payment_raw)


class PgStatementRepository:
    """Wraps tts_erp.persist_statement."""

    def upsert(self, shop_id: str, statement_raw: dict) -> bool:
        return tts_erp.persist_statement(shop_id, statement_raw)


class PgReturnRepository:
    """Wraps tts_erp.persist_return."""

    def upsert(self, shop_id: str, return_raw: dict) -> bool:
        return tts_erp.persist_return(shop_id, return_raw)


class PgCancellationRepository:
    """Wraps tts_erp.persist_cancellation."""

    def upsert(self, shop_id: str, cancel_raw: dict) -> bool:
        return tts_erp.persist_cancellation(shop_id, cancel_raw)


def make_pg_repos(db_url: str | None = None) -> dict:
    """Factory: returns a dict of all 5 PG repos.

    `db_url` is currently unused (persist_* functions read TTS_ERP_DB_URL
    from env). Kept for future connection-pooling.
    """
    return {
        "orders": PgOrderRepository(),
        "payments": PgPaymentRepository(),
        "statements": PgStatementRepository(),
        "returns": PgReturnRepository(),
        "cancellations": PgCancellationRepository(),
    }
