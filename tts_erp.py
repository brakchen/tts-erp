#!/usr/bin/env python3
"""TikTok Shop ERP service — wraps the Partner Order API + persists to PostgreSQL.

This service is intentionally separate from `oauth-receiver`:
- It pulls access_token / shop_cipher from oauth-receiver over HTTP (NEVER
  reads the encrypted DB directly — keeps auth in one place).
- It signs every TikTok call with HMAC-SHA256 (see `tts_signing.py`).
- It stores orders / items / shippings in its own PostgreSQL database
  (`tts_erp`) for downstream analytics / ERP integration.

Endpoints exposed
-----------------
OAuth/shop passthrough (proxies to oauth-receiver):
  GET  /shops              list authorized shops (no decryption on this side)
  GET  /shops/<shop_id>    get one shop's metadata (cipher, name, region)
  GET  /token/<shop_id>    get token for a shop (masked by default, ?reveal=1)

Order API — direct TikTok pass-through (read-only — 202309 spec):
  POST /orders/search                          call TikTok order search
                                                  body: {order_status?, create_time_ge?, create_time_lt?}
                                                  query: ?page_size=50&sort_field=create_time&sort_order=DESC
                                                  (page_size MUST go in query string, NOT body — 36009004;
                                                   sort_order MUST be UPPERCASE)
  GET  /orders/<order_id>                      translate to GET /order/202309/orders?ids=<order_id>
                                                  (path-with-id variant returns 36009009)
  NOTE: /orders/list does NOT exist (36009009)
  NOTE: action endpoints (confirm/cancel/ship/tracking/risk/buyer/recipient)
        all return 36009009 — TikTok 202309 Order module is READ-ONLY.
        Write actions live in separate Fulfillment / Reverse Logistics modules
        not yet exposed by this service.

Order API — local PostgreSQL cache:
  GET  /db/orders                             list orders (filters: shop_id, status)
  GET  /db/orders/<order_id>                 get one order from local DB
  GET  /db/orders/<order_id>/items           get line items
  GET  /db/orders/<order_id>/shipping        get shipping info
  GET  /db/sync_log                           see recent syncs

Sync (TikTok → local DB):
  POST /sync/orders                           run /order/202309/orders/search and persist
                                              body: {shop_id, order_status?, create_time_ge?, create_time_lt?, page_size?}
  POST /sync/order/<order_id>                 fetch one order detail and persist

Finance API — direct TikTok pass-through (get-statements-202309):
  GET  /finance/statements                    call /finance/202309/statements
                                                  query: ?page_size=50&sort_field=statement_time&sort_order=DESC
                                                  (sort_field is REQUIRED — 36009004 otherwise; default injected if missing)
  GET  /finance/payments                      call /finance/202309/payments
                                                  query: ?page_size=50&sort_field=create_time&sort_order=DESC
  NOTE: no detail/sub-records endpoints in 202309 (e.g. /statements/{id}/transactions → 36009009)

Finance API — local PostgreSQL cache:
  GET  /db/statements?shop_id=                list statements from local DB
  GET  /db/payments?shop_id=&status=          list payments from local DB

Sync (TikTok → local DB) for finance:
  POST /sync/statements                       body: {shop_id, statement_time_ge?, statement_time_lt?, page_size?}
  POST /sync/payments                         body: {shop_id, create_time_ge?, create_time_lt?, page_size?}

Return / Refund / Cancellation API (return-refund-and-cancel-202309):
  POST /returns/search                        body: {shop_id, ...filters}    → /return_refund/202309/returns/search
  POST /cancellations/search                  body: {shop_id, ...filters}    → /return_refund/202309/cancellations/search
  POST /returns                               → 501 (CREATE write endpoint, NOT integrated per user instruction)
  POST /cancellations                         → 501 (CREATE write endpoint, NOT integrated per user instruction)
  NOTE: /return_refund/202309/<id> detail endpoints return 36009009 (no path)
  NOTE: /reverse/202309/* (reverse logistics) returns HTTP 404 at CDN — module not available
  Confirmed via probe_refund_v3/v5/v6 2026-08-16.

Local DB reads for returns/cancellations:
  GET  /db/returns?shop_id=&status=&limit=   returns table
  GET  /db/cancellations?shop_id=&status=&limit=  cancellations table

Sync (TikTok → local DB) for return/refund:
  POST /sync/returns                          body: {shop_id, create_time_ge?, create_time_lt?, page_size?}
  POST /sync/cancellations                    body: {shop_id, create_time_ge?, create_time_lt?, page_size?}

Config (env vars — see .env)
---------------------------
  TTS_ERP_HOST            bind host (0.0.0.0)
  TTS_ERP_PORT            bind port (9877)
  OAUTH_RECEIVER_URL      where to fetch tokens (http://127.0.0.1:9876)
  TIKTOK_APP_KEY, TIKTOK_APP_SECRET   HMAC signing
  TIKTOK_API_HOST         open-api.tiktokglobalshop.com
  TTS_ERP_DB_URL          postgresql://...:5432/tts_erp
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "/home/schan/setup/lib")
import contextlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

from log_helper import get_logger
from psycopg import sql

# Module-level logger (inherits from app logger set by main() or FastAPI)
log = get_logger("tts-erp")

# ---------- Config ----------


def _env_int(name: str, default: str) -> int:
    """Parse an int env var with fallback — malformed operator config must
    not crash module import (which would take down every dependent)."""
    try:
        return int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return int(float(default))


# pi-lens-ignore: S104
HOST = os.environ.get("TTS_ERP_HOST", "0.0.0.0")
PORT = _env_int("TTS_ERP_PORT", "9877")
OAUTH_RECEIVER_URL = os.environ.get(
    "OAUTH_RECEIVER_URL", "http://127.0.0.1:9876"
).rstrip("/")
TIKTOK_APP_KEY = os.environ.get("TIKTOK_APP_KEY", "")
TIKTOK_APP_SECRET = os.environ.get("TIKTOK_APP_SECRET", "")
TIKTOK_API_HOST = os.environ.get(
    "TIKTOK_API_HOST", "https://open-api.tiktokglobalshop.com"
)
TTS_ERP_DB_URL = os.environ.get("TTS_ERP_DB_URL", "")
TTS_ERP_HTTP_TIMEOUT = _env_int("TTS_ERP_HTTP_TIMEOUT", "30")

# In-memory last sync log (mirrored to PG via sync_log table)
_last_syncs: deque[dict] = deque(maxlen=50)


# ---------- DB helpers ----------
# W3.1: pooled connections. Previously every query opened a fresh PG
# connection (sync_orders 50 pages × 50 orders = 2500 connect/close per
# tick). psycopg_pool is the officially maintained pool for psycopg3.
#
# Usage stays `with db_connect() as conn:` — the pool's connection()
# context manager has identical semantics (commit on clean exit, rollback
# on exception, connection returned to pool instead of closed).
_pool = None


def _get_pool():
    """Lazy pool singleton — module import must not require a live DB."""
    global _pool
    if _pool is None:
        if not TTS_ERP_DB_URL:
            raise RuntimeError("TTS_ERP_DB_URL not configured")
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(
            TTS_ERP_DB_URL,
            min_size=1,
            max_size=10,
            kwargs={"connect_timeout": 5},
            # Don't block module import / first request on pool warmup
            open=False,
        )
        _pool.open()
        # Pool spawns worker threads; close them at interpreter exit so
        # short-lived processes (pytest, scripts) don't die with
        # PythonFinalizationError noise on stderr.
        import atexit

        atexit.register(db_pool_close_for_test)
    return _pool


def db_connect():
    if not TTS_ERP_DB_URL:
        raise RuntimeError("TTS_ERP_DB_URL not configured")
    return _get_pool().connection()


def db_pool_close_for_test():
    """Dispose the pool (atexit + tests). Never raises."""
    global _pool
    if _pool is not None:
        # Interpreter-shutdown race is harmless — suppress it.
        with contextlib.suppress(Exception):
            _pool.close()
        _pool = None


def db_init():
    """Connect + check table existence; run init SQL if needed."""
    if not TTS_ERP_DB_URL:
        log.error("  TTS_ERP_DB_URL not set — DB store disabled\n")
        return False
    try:
        with db_connect() as conn, conn.cursor() as cur:
            for tbl in (
                "shops",
                "orders",
                "order_items",
                "order_shippings",
                "sync_log",
                "statements",
                "payments",
                "returns",
                "cancellations",
            ):
                # pi-lens-ignore: python-sql-injection
                cur.execute(sql.SQL("SELECT to_regclass(%s)"), (tbl,))
                row = cur.fetchone()
                if row is None or row[0] is None:
                    log.error(
                        f"[tts-erp] WARN: table '{tbl}' missing — run schema_tts_erp.sql\n"
                    )
                    return False
        return True
    except Exception as e:  # noqa: BLE001
        log.error(f"  DB init failed: {e}\n")
        return False


# ---------- OAuth passthrough ----------
def fetch_shop_meta(shop_id: str) -> dict | None:
    """Get shop metadata (cipher, name, region) from oauth-receiver. No secrets."""
    try:
        # pi-lens-ignore: S310
        with urllib.request.urlopen(
            f"{OAUTH_RECEIVER_URL}/tokens/shops", timeout=5
        ) as r:
            data = json.load(r)
        for s in data.get("items", []):
            if s.get("shop_id") == shop_id:
                return s
    except Exception as e:  # noqa: BLE001
        log.error(f"  failed to list shops: {e}\n")
    return None


def fetch_token(shop_id: str, reveal: bool = False) -> dict | None:
    """Get access_token + shop_cipher from oauth-receiver. Must pass ?reveal=1 for plaintext."""
    url = f"{OAUTH_RECEIVER_URL}/token/{urllib.parse.quote(shop_id)}"
    if reveal:
        url += "?reveal=1"
    try:
        # pi-lens-ignore: S310
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        # pi-lens-ignore: S310
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {
            "_error": e.code,
            "_body": e.read().decode("utf-8", errors="replace")[:300],
        }
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


# ---------- Order persistence ----------
def persist_order(shop_id: str, order_raw: dict) -> bool:
    """Upsert an order + its items into local DB. Idempotent on order_id.

    TikTok /order/202309/orders/search response structure (confirmed 2026-08-16):
        order_raw = {
            "id": str,                                # the order ID
            "status": "AWAITING_SHIPMENT",            # STRING, not int
            "create_time": int, "update_time": int,
            "buyer_email": str, "buyer_message": str,
            "fulfillment_type": "FULFILLMENT_BY_SELLER" | "FULFILLMENT_BY_TIKTOK",
            "shipping_provider_id": str, "shipping_provider_name": str,  # top-level
            "payment": { "total_amount": str, "currency": "VND", ... },
            "line_items": [ ... ],
            "recipient_address": { ... },
            ...
        }
    """
    oid = order_raw.get("id") or order_raw.get("order_id")
    if not oid:
        return False
    try:
        with db_connect() as conn, conn.cursor() as cur:
            # TikTok 202309 spec returns status as a string (e.g. "AWAITING_SHIPMENT").
            # No legacy int code exists — only one column `order_status_name` is used.
            raw_status = order_raw.get("status") or order_raw.get("order_status")
            order_status_name: str | None = (
                str(raw_status) if raw_status is not None else None
            )

            # Payment is a nested object in 202309 spec
            payment = order_raw.get("payment") or {}
            payment_amount = (
                payment.get("total_amount") if isinstance(payment, dict) else None
            ) or order_raw.get("payment_amount")
            payment_currency = (
                payment.get("currency") if isinstance(payment, dict) else None
            ) or order_raw.get("payment_currency")

            # Orders table — extract known fields, store raw for full fidelity
            # pi-lens-ignore: python-sql-injection
            cur.execute(
                sql.SQL("""
                INSERT INTO orders
                    (order_id, shop_id, order_status_name, payment_amount, payment_currency,
                     total_amount, buyer_email, buyer_message, create_time, update_time,
                     paid_time, shipped_time, delivered_time, cancelled_time,
                     fulfillment_type, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (order_id) DO UPDATE SET
                    shop_id            = EXCLUDED.shop_id,
                    order_status_name  = EXCLUDED.order_status_name,
                    payment_amount     = EXCLUDED.payment_amount,
                    payment_currency   = EXCLUDED.payment_currency,
                    total_amount       = EXCLUDED.total_amount,
                    buyer_email        = EXCLUDED.buyer_email,
                    buyer_message      = EXCLUDED.buyer_message,
                    create_time        = EXCLUDED.create_time,
                    update_time        = EXCLUDED.update_time,
                    paid_time          = EXCLUDED.paid_time,
                    -- Lifecycle times only overwrite if new raw exposes them
                    -- (COALESCE keeps existing value when new raw omits the
                    -- field — prevents backfilled / previously-recorded times
                    -- from being nulled out on re-sync, 2026-08-19 fix).
                    shipped_time       = COALESCE(EXCLUDED.shipped_time,   orders.shipped_time),
                    delivered_time     = COALESCE(EXCLUDED.delivered_time, orders.delivered_time),
                    cancelled_time     = COALESCE(EXCLUDED.cancelled_time, orders.cancelled_time),
                    fulfillment_type   = EXCLUDED.fulfillment_type,
                    raw                = EXCLUDED.raw,
                    synced_at          = now()
            """),
                (
                    oid,
                    shop_id,
                    order_status_name,
                    _decimal(payment_amount),
                    _str(payment_currency),
                    # total_amount lives nested under payment in 202309 spec.
                    # order_raw.total_amount at top-level does NOT exist —
                    # removing the OR-fallback (fix: dead-code cleanup).
                    _decimal(payment.get("total_amount")),
                    _str(order_raw.get("buyer_email")),
                    _str(order_raw.get("buyer_message") or order_raw.get("buyer_note")),
                    _int(order_raw.get("create_time")),
                    _int(order_raw.get("update_time")),
                    _int(
                        order_raw.get("paid_time") or (payment or {}).get("paid_time")
                    ),
                    # 202309 spec uses rts_time (Ready-To-Ship) and delivery_time;
                    # cancelled orders expose cancel_time. Legacy field names
                    # (shipped_time / delivered_time / cancelled_time) do NOT
                    # exist in the API response — fix: 2026-08-19.
                    _int(order_raw.get("rts_time")),
                    _int(order_raw.get("delivery_time")),
                    _int(order_raw.get("cancel_time")),
                    _str(
                        order_raw.get("fulfillment_type")
                        or (order_raw.get("fulfillment", {}) or {}).get("type")
                    ),
                    json.dumps(order_raw, ensure_ascii=False, default=str),
                ),
            )

            # Items
            for it in order_raw.get("line_items") or order_raw.get("items") or []:
                # pi-lens-ignore: python-sql-injection
                cur.execute(
                    sql.SQL("""
                    INSERT INTO order_items
                        (order_id, item_id, shop_id, sku_id, product_id, product_name,
                         sku_name, sku_image, quantity, sku_price, raw)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (order_id, item_id) DO UPDATE SET
                        sku_id       = EXCLUDED.sku_id,
                        product_id   = EXCLUDED.product_id,
                        product_name = EXCLUDED.product_name,
                        sku_name     = EXCLUDED.sku_name,
                        sku_image    = EXCLUDED.sku_image,
                        quantity     = EXCLUDED.quantity,
                        sku_price    = EXCLUDED.sku_price,
                        raw          = EXCLUDED.raw
                """),
                    (
                        oid,
                        _str(it.get("id") or it.get("item_id")) or "",
                        shop_id,
                        _str(it.get("sku_id")),
                        _str(it.get("product_id")),
                        _str(it.get("product_name")),
                        _str(it.get("sku_name")),
                        _str(it.get("sku_image")),
                        _int(it.get("quantity")) or 1,
                        _decimal(
                            it.get("sale_price")
                            or it.get("sku_price")
                            or it.get("price")
                        ),
                        json.dumps(it, ensure_ascii=False, default=str),
                    ),
                )

            # Shipping — TikTok 202309 has top-level shipping_provider_id /
            # shipping_provider (NOTE: no shipping_provider_name in 202309 spec;
            # the actual carrier name is `shipping_provider`, 2026-08-19 fix).
            # Legacy code may also put them under shipping/fulfillment.
            tracking = (
                order_raw.get("tracking_number")
                or (order_raw.get("shipping") or {}).get("tracking_number")
                or (order_raw.get("fulfillment") or {}).get("tracking_number")
            )
            provider_id = (
                order_raw.get("shipping_provider_id")
                or (order_raw.get("shipping") or {}).get("shipping_provider_id")
                or (order_raw.get("fulfillment") or {}).get("shipping_provider_id")
            )
            provider_name = (
                order_raw.get("shipping_provider")
                or order_raw.get("shipping_provider_name")  # legacy fallback
                or (order_raw.get("shipping") or {}).get("shipping_provider_name")
                or (order_raw.get("fulfillment") or {}).get("shipping_provider_name")
            )
            if any([tracking, provider_id, provider_name]):
                # W2.5: store only the shipping-relevant subset, not the
                # whole order JSON — orders.raw already holds the full
                # payload, so duplicating it here doubles storage for every
                # order with shipping info.
                shipping_raw = {
                    k: v
                    for k, v in {
                        "tracking_number": tracking,
                        "shipping_provider_id": provider_id,
                        "shipping_provider_name": provider_name,
                        "shipping": order_raw.get("shipping"),
                        "fulfillment": order_raw.get("fulfillment"),
                    }.items()
                    if v is not None
                }
                # pi-lens-ignore: python-sql-injection
                cur.execute(
                    sql.SQL("""
                    INSERT INTO order_shippings
                        (order_id, shop_id, tracking_number, shipping_provider_id,
                         shipping_provider_name, raw)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (order_id) DO UPDATE SET
                        tracking_number        = COALESCE(EXCLUDED.tracking_number,        order_shippings.tracking_number),
                        shipping_provider_id   = COALESCE(EXCLUDED.shipping_provider_id,   order_shippings.shipping_provider_id),
                        shipping_provider_name = COALESCE(EXCLUDED.shipping_provider_name, order_shippings.shipping_provider_name),
                        raw                    = EXCLUDED.raw,
                        synced_at              = now()
                """),
                    (
                        oid,
                        shop_id,
                        tracking,
                        provider_id,
                        provider_name,
                        json.dumps(shipping_raw, ensure_ascii=False, default=str),
                    ),
                )

            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.error(f"  persist_order({oid}) failed: {e}\n")
        return False


def persist_statement(shop_id: str, stmt_raw: dict) -> bool:
    """Upsert a statement from /finance/202309/statements into local DB.

    Confirmed 2026-08-16 response fields:
        id, payment_id, currency, payment_status, statement_time, payment_time,
        revenue_amount, fee_amount, net_sales_amount, shipping_cost_amount,
        adjustment_amount, settlement_amount.
    """
    sid = stmt_raw.get("id")
    if not sid:
        return False
    try:
        with db_connect() as conn, conn.cursor() as cur:
            # pi-lens-ignore: python-sql-injection
            cur.execute(
                sql.SQL("""
                INSERT INTO statements
                    (statement_id, shop_id, payment_id, currency, payment_status,
                     statement_time, payment_time,
                     revenue_amount, fee_amount, net_sales_amount,
                     shipping_cost_amount, adjustment_amount, settlement_amount, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (statement_id) DO UPDATE SET
                    shop_id              = EXCLUDED.shop_id,
                    payment_id           = EXCLUDED.payment_id,
                    currency             = EXCLUDED.currency,
                    payment_status       = EXCLUDED.payment_status,
                    statement_time       = EXCLUDED.statement_time,
                    payment_time         = EXCLUDED.payment_time,
                    revenue_amount       = EXCLUDED.revenue_amount,
                    fee_amount           = EXCLUDED.fee_amount,
                    net_sales_amount     = EXCLUDED.net_sales_amount,
                    shipping_cost_amount = EXCLUDED.shipping_cost_amount,
                    adjustment_amount    = EXCLUDED.adjustment_amount,
                    settlement_amount    = EXCLUDED.settlement_amount,
                    raw                  = EXCLUDED.raw,
                    synced_at            = now()
            """),
                (
                    sid,
                    shop_id,
                    _str(stmt_raw.get("payment_id")),
                    _str(stmt_raw.get("currency")),
                    _str(stmt_raw.get("payment_status")),
                    _int(stmt_raw.get("statement_time")),
                    _int(stmt_raw.get("payment_time")),
                    _decimal(stmt_raw.get("revenue_amount")),
                    _decimal(stmt_raw.get("fee_amount")),
                    _decimal(stmt_raw.get("net_sales_amount")),
                    _decimal(stmt_raw.get("shipping_cost_amount")),
                    _decimal(stmt_raw.get("adjustment_amount")),
                    _decimal(stmt_raw.get("settlement_amount")),
                    json.dumps(stmt_raw, ensure_ascii=False, default=str),
                ),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.error(f"  persist_statement({sid}) failed: {e}\n")
        return False


# /finance/202309/statements/{id}/statement_transactions 的全部金额字段
# （2026-08-18 probe_finance_txns.py 实测枚举：58 字段里除 id/order_id/
# order_create_time/type/currency 外全是 *_amount 字符串数字，费用为负值）
STMT_TXN_AMOUNT_FIELDS = (
    "actual_return_shipping_fee_amount",
    "actual_shipping_fee_amount",
    "adjustment_amount",
    "affiliate_ads_commission_amount",
    "affiliate_commission_amount",
    "affiliate_commission_before_pit",
    "affiliate_partner_commission_amount",
    "after_seller_discounts_subtotal_amount",
    "customer_order_refund_amount",
    "customer_paid_shipping_fee_amount",
    "customer_paid_shipping_fee_refund_amount",
    "customer_payment_amount",
    "customer_refund_amount",
    "customer_shipping_fee_amount",
    "customer_shipping_fee_offset_amount",
    "fbm_shipping_cost_amount",
    "fbt_fulfillment_fee_amount",
    "fbt_fulfillment_fee_reimbursement_amount",
    "fbt_shipping_cost_amount",
    "fee_amount",
    "gross_sales_amount",
    "gross_sales_refund_amount",
    "isr_income_tax_amount",
    "iva_vat_amount",
    "net_sales_amount",
    "pit_amount",
    "platform_commission_amount",
    "platform_discount_amount",
    "platform_discount_refund_amount",
    "platform_refund_subsidy_amount",
    "platform_shipping_fee_discount_amount",
    "promo_shipping_incentive_amount",
    "referral_fee_amount",
    "refund_administration_fee_amount",
    "refund_shipping_cost_discount_amount",
    "retail_delivery_fee_amount",
    "retail_delivery_fee_payment_amount",
    "retail_delivery_fee_refund_amount",
    "return_shipping_fee_amount",
    "revenue_amount",
    "sales_tax_amount",
    "sales_tax_payment_amount",
    "sales_tax_refund_amount",
    "seller_discount_amount",
    "seller_discount_refund_amount",
    "settlement_amount",
    "shipping_cost_amount",
    "shipping_cost_discount_amount",
    "shipping_fee_amount",
    "shipping_fee_subsidy_amount",
    "shipping_insurance_fee_amount",
    "signature_confirmation_fee_amount",
    "transaction_fee_amount",
)


def persist_statement_transaction(
    shop_id: str, statement_id: str, txn_raw: dict
) -> bool:
    """Upsert 一条账单交易明细（替代 Excel financial_lines + fee_lines 的接口数据源）。

    幂等按 txn_id；金额字段字符串数字 → NUMERIC，缺失/非数字 → NULL；
    完整响应进 raw jsonb 兜底。
    """
    tid = txn_raw.get("id")
    if not tid:
        return False
    base_cols = (
        "txn_id",
        "statement_id",
        "shop_id",
        "order_id",
        "order_create_time",
        "type",
        "currency",
    )
    cols = base_cols + STMT_TXN_AMOUNT_FIELDS + ("raw",)
    col_list = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "txn_id")
    # noqa comment must NOT be on the f""" opening line — anything after
    # the opening quotes becomes part of the SQL string (broke PG parsing
    # with 'syntax error at or near "#"').
    sql_query = f"""
        INSERT INTO statement_transactions ({col_list})
        VALUES ({placeholders})
        ON CONFLICT (txn_id) DO UPDATE SET {updates}, synced_at = now()
    """  # noqa: S608 -- col_list/updates built from hardcoded constant tuples
    values = (
        [
            tid,
            statement_id,
            shop_id,
            _str(txn_raw.get("order_id")),
            _int(txn_raw.get("order_create_time")),
            _str(txn_raw.get("type")),
            _str(txn_raw.get("currency")),
        ]
        + [_decimal(txn_raw.get(f)) for f in STMT_TXN_AMOUNT_FIELDS]
        + [
            json.dumps(txn_raw, ensure_ascii=False, default=str),
        ]
    )
    try:
        with db_connect() as conn, conn.cursor() as cur:
            # pi-lens-ignore: python-sql-injection
            cur.execute(sql.SQL(sql_query), values)  # type: ignore[reportArgumentType]  # pyright strict: SQL accepts str but pyright types say LiteralString
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.error(f"[tts-erp] persist_statement_transaction({tid}) failed: {e}\n")
        return False


def persist_payment(shop_id: str, pay_raw: dict) -> bool:
    """Upsert a payment from /finance/202309/payments into local DB.

    Confirmed 2026-08-16 response fields (nested objects):
        id, status, bank_account, create_time, paid_time, exchange_rate,
        amount.{currency, value},
        settlement_amount.{currency, value},
        payment_amount_before_exchange.{currency, value},
        reserve_amount.{currency, value}.
    """
    pid = pay_raw.get("id")
    if not pid:
        return False
    try:
        amount = pay_raw.get("amount") or {}
        settlement = pay_raw.get("settlement_amount") or {}
        before = pay_raw.get("payment_amount_before_exchange") or {}
        reserve = pay_raw.get("reserve_amount") or {}
        with db_connect() as conn, conn.cursor() as cur:
            # pi-lens-ignore: python-sql-injection
            cur.execute(
                sql.SQL("""
                INSERT INTO payments
                    (payment_id, shop_id, status, currency,
                     amount_value, settlement_amount_value, payment_amount_before_value,
                     reserve_amount_value, exchange_rate, bank_account,
                     create_time, paid_time, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (payment_id) DO UPDATE SET
                    shop_id                     = EXCLUDED.shop_id,
                    status                      = EXCLUDED.status,
                    currency                    = EXCLUDED.currency,
                    amount_value                = EXCLUDED.amount_value,
                    settlement_amount_value     = EXCLUDED.settlement_amount_value,
                    payment_amount_before_value = EXCLUDED.payment_amount_before_value,
                    reserve_amount_value        = EXCLUDED.reserve_amount_value,
                    exchange_rate               = EXCLUDED.exchange_rate,
                    bank_account                = EXCLUDED.bank_account,
                    create_time                 = EXCLUDED.create_time,
                    paid_time                   = EXCLUDED.paid_time,
                    raw                         = EXCLUDED.raw,
                    synced_at                   = now()
            """),
                (
                    pid,
                    shop_id,
                    _str(pay_raw.get("status")),
                    _str(amount.get("currency") if isinstance(amount, dict) else None),
                    _decimal(amount.get("value") if isinstance(amount, dict) else None),
                    _decimal(
                        settlement.get("value")
                        if isinstance(settlement, dict)
                        else None
                    ),
                    _decimal(before.get("value") if isinstance(before, dict) else None),
                    _decimal(
                        reserve.get("value") if isinstance(reserve, dict) else None
                    ),
                    _str(pay_raw.get("exchange_rate")),
                    _str(pay_raw.get("bank_account")),
                    _int(pay_raw.get("create_time")),
                    _int(pay_raw.get("paid_time")),
                    json.dumps(pay_raw, ensure_ascii=False, default=str),
                ),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.error(f"  persist_payment({pid}) failed: {e}\n")
        return False


def persist_return(shop_id: str, return_raw: dict) -> bool:
    """Upsert a return (buyer-initiated refund / return request) into local DB.

    Confirmed 2026-08-16 response structure from
    POST /return_refund/202309/returns/search:
        return_raw = {
            "id": str,                       # the return order id
            "order_id": str,
            "return_status": "AWAITING_SELLER_RESPONSE" | "REFUND_PENDING" | "CLOSED" | ...,
            "return_reason": str,            # machine code
            "return_type": str,
            "role": "BUYER" | "SELLER",
            "create_time": int, "update_time": int,
            ...,
        }
    Schema is intentionally lenient: store the full raw JSONB and only the
    top-level scalar fields get dedicated columns. Any nested arrays
    (e.g. line_items, evidence) are reachable via raw.
    """
    rid = return_raw.get("id") or return_raw.get("return_id")
    if not rid:
        return False
    try:
        with db_connect() as conn, conn.cursor() as cur:
            # pi-lens-ignore: python-sql-injection
            cur.execute(
                sql.SQL("""
                INSERT INTO returns
                    (return_id, shop_id, order_id, return_status, return_reason,
                     return_type, role, create_time, update_time, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (return_id) DO UPDATE SET
                    shop_id       = EXCLUDED.shop_id,
                    order_id      = EXCLUDED.order_id,
                    return_status = EXCLUDED.return_status,
                    return_reason = EXCLUDED.return_reason,
                    return_type   = EXCLUDED.return_type,
                    role          = EXCLUDED.role,
                    create_time   = EXCLUDED.create_time,
                    update_time   = EXCLUDED.update_time,
                    raw           = EXCLUDED.raw,
                    synced_at     = now()
            """),
                (
                    rid,
                    shop_id,
                    _str(return_raw.get("order_id")),
                    _str(return_raw.get("return_status")),
                    _str(return_raw.get("return_reason")),
                    _str(return_raw.get("return_type")),
                    _str(return_raw.get("role")),
                    _int(return_raw.get("create_time")),
                    _int(return_raw.get("update_time")),
                    json.dumps(return_raw, ensure_ascii=False, default=str),
                ),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.error(f"  persist_return({rid}) failed: {e}\n")
        return False


def persist_cancellation(shop_id: str, cancel_raw: dict) -> bool:
    """Upsert a cancellation (buyer or seller cancel before fulfilment) into DB.

    Confirmed 2026-08-16 response structure from
    POST /return_refund/202309/cancellations/search:
        cancel_raw = {
            "cancel_id": str,
            "order_id": str,
            "cancel_status": "CANCELLATION_REQUEST_COMPLETE" | "AWAITING_SELLER_RESPONSE" | ...,
            "cancel_reason": "ecom_order_to_ship_canceled_reason_*",
            "cancel_reason_text": "No longer needed" | ...,
            "cancel_type": "BUYER_CANCEL" | "SELLER_CANCEL",
            "role": "BUYER" | "SELLER",
            "should_replenish_stock": bool,
            "create_time": int, "update_time": int,
            "cancel_line_items": [
                { "cancel_line_item_id", "order_line_item_id",
                  "product_image": { "url", "width", "height" },
                  "product_name", "sku_id", "sku_name" },
                ...
            ],
            ...
        }
    The line_items array is rich and varies by request — kept only in raw JSONB.
    """
    cid = cancel_raw.get("cancel_id") or cancel_raw.get("id")
    if not cid:
        return False
    try:
        with db_connect() as conn, conn.cursor() as cur:
            # pi-lens-ignore: python-sql-injection
            cur.execute(
                sql.SQL("""
                INSERT INTO cancellations
                    (cancel_id, shop_id, order_id, cancel_status, cancel_reason,
                     cancel_reason_text, cancel_type, role,
                     should_replenish_stock, create_time, update_time, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (cancel_id) DO UPDATE SET
                    shop_id                = EXCLUDED.shop_id,
                    order_id               = EXCLUDED.order_id,
                    cancel_status          = EXCLUDED.cancel_status,
                    cancel_reason          = EXCLUDED.cancel_reason,
                    cancel_reason_text     = EXCLUDED.cancel_reason_text,
                    cancel_type            = EXCLUDED.cancel_type,
                    role                   = EXCLUDED.role,
                    should_replenish_stock = EXCLUDED.should_replenish_stock,
                    create_time            = EXCLUDED.create_time,
                    update_time            = EXCLUDED.update_time,
                    raw                    = EXCLUDED.raw,
                    synced_at              = now()
            """),
                (
                    cid,
                    shop_id,
                    _str(cancel_raw.get("order_id")),
                    _str(cancel_raw.get("cancel_status")),
                    _str(cancel_raw.get("cancel_reason")),
                    _str(cancel_raw.get("cancel_reason_text")),
                    _str(cancel_raw.get("cancel_type")),
                    _str(cancel_raw.get("role")),
                    bool(cancel_raw.get("should_replenish_stock"))
                    if cancel_raw.get("should_replenish_stock") is not None
                    else None,
                    _int(cancel_raw.get("create_time")),
                    _int(cancel_raw.get("update_time")),
                    json.dumps(cancel_raw, ensure_ascii=False, default=str),
                ),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.error(f"  persist_cancellation({cid}) failed: {e}\n")
        return False


# ---------- Logistics tracking helpers ----------
#
# Confirmed 2026-08-16:
#   GET /fulfillment/202309/orders/{order_id}/tracking
#     → { code, message, data: { tracking: [ {action_code, description, update_time_millis} ] } }
#   All 6 status types (AWAITING_COLLECTION/CANCELLED/COMPLETED/DELIVERED/IN_TRANSIT/...) return code=0.
#   /logistics/202604/orders/.../tracking returns 11007009 on this app (path reached but scope not open).
#
# action_code reference (from probe samples, in-progress):
#   10101 Order placed
#   20101 Packed by seller
#   30201 Arrived at sorting center (origin)
#   30301 In transit in origin
#   30401 Handed over to next carrier (origin)
#   30501 Handed over to international carrier
#   30801 Handed over to local carrier (dest)
#   31201-32601 various location events
#   34301 Departed country/region of origin
#   34701 Import clearance completed
#   38301 Arrived in destination country, port of entry
#   38701 Awaiting international departure from origin
#   40101-40501 final-mile delivery
#   40601 Delivery failed (rejected)
#   40801/41001/41101/41801 2nd/3rd delivery attempts
#   50101 Delivered
#   70201 Delivery failed
#   80101 Returned to seller
ACTION_CODE_ARRIVED_DEST = 38301
ACTION_CODE_DEPARTED_ORIG = 34301
ACTION_CODE_IMPORT_CLEAR = 34701
ACTION_CODE_DELIVERED = 50101
ACTION_CODE_RETURNED = 80101


def _classify_tracking_status(events: list) -> tuple[str, bool]:
    """Given the raw tracking list (each item {action_code, ...}), return
    (final_status, arrived_overseas). arrived_overseas is True if any
    cross-border action_code (38301-39999) or last-mile action_code (40000-49999)
    has been seen — i.e. the package has entered the destination country.

    IMPORTANT: action_code is NOT monotonic. TikTok uses 5-digit codes for the
    normal flow (1xxxx=placed 2xxxx=packed 3xxxx=cross-border 4xxxx=last-mile
    5xxxx=delivered 7xxxx=failed 8xxxx=returned) AND 6-digit codes for special
    events (11xxxx=cancel etc.). Comparing `c >= 38301` blindly is WRONG — 110101
    (cancel) would also pass. We must filter to the 5-digit range.
    """
    if not events:
        return "NO_DATA", False
    codes = [
        _safe_int(e.get("action_code"), default=0, source="event.action_code")
        for e in events
        if e.get("action_code") is not None
    ]
    # 5-digit action codes (normal flow)
    codes_5d = [c for c in codes if 10000 <= c <= 99999]
    # arrival events are in 38301-39999 (cross-border 5xxx-3xxx) OR any 4xxxx (last-mile)
    arrived = any(38301 <= c <= 39999 for c in codes_5d) or any(
        40000 <= c <= 49999 for c in codes_5d
    )

    if 50101 in codes_5d:
        return "DELIVERED", arrived
    if 80101 in codes_5d:
        return "RETURNED_TO_SELLER", arrived
    if any(70000 <= c <= 79999 for c in codes_5d):
        return "DELIVERY_FAILED", arrived
    if arrived:
        return "ARRIVED_DEST", True
    if any(34301 <= c <= 38299 for c in codes_5d):
        return "CROSS_BORDER", False
    if any(30201 <= c <= 34299 for c in codes_5d):
        return "IN_ORIGIN", False
    if 20101 in codes_5d:
        return "AWAITING_PICKUP", False
    return "PRE_PICKUP", False


def _extract_location(description: str) -> str | None:
    """Pull a location token out of an English description, e.g.
    'Your package is now in Phường Yên Thắng.' → 'Phường Yên Thắng'
    'Handed over to local carrier'                → None
    'Arrived in Vietnam, port of entry ...'       → 'Vietnam'
    """
    if not description:
        return None
    s = description.strip()
    # "is now in <LOCATION>" / "is now in <LOCATION> and will be transferred to ..."
    for prefix in (
        "is now in ",
        "departed sorting center in ",
        "arrived at sorting center in ",
    ):
        if prefix in s.lower():
            tail = s[s.lower().index(prefix) + len(prefix) :]
            # strip trailing ' and will be transferred to ...' or '.'
            for stop in (" and will be transferred to", "."):
                if stop in tail:
                    tail = tail.split(stop, 1)[0]
                    break
            return tail.strip() or None
    if s.lower().startswith("arrived in "):
        # "Arrived in Vietnam, port of entry ..."  → "Vietnam"
        tail = s[len("Arrived in ") :]
        return tail.split(",", 1)[0].strip() or None
    return None


def persist_logistics_tracking(shop_id: str, order_id: str, resp: dict) -> bool:
    """Persist a /fulfillment/202309/orders/{id}/tracking response into DB.

    Idempotent on (order_id, action_code, event_time) for events. Re-runs
    just update n_events / final_status / last_event_at on the summary row.
    """
    if not isinstance(resp, dict) or resp.get("code") != 0:
        log.error(
            f"[tts-erp] persist_logistics_tracking({order_id}) bad resp: code={resp.get('code')!r}\n"
        )
        return False
    data = resp.get("data") or {}
    events = data.get("tracking") or []
    if not isinstance(events, list):
        return False

    final_status, arrived_overseas = _classify_tracking_status(events)
    n_events = len(events)

    def ts_of(ev):
        v = ev.get("update_time_millis")
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    # TikTok 返回的 tracking 列表是【最新事件在前】。first/last 必须按
    # update_time_millis 时间戳推导，不能信列表位置（2026-08-17 修：
    # 旧实现 events[0]/events[-1] 把 first/last 写反了）。
    timed_events = sorted(
        [ev for ev in events if ts_of(ev) is not None],
        key=lambda ev: ts_of(ev) or 0,
    )
    first = timed_events[0] if timed_events else {}
    last = timed_events[-1] if timed_events else {}

    last_event_at = ts_of(last)
    first_event_at = ts_of(first)

    # find first occurrence of key events (按时间升序找首次出现)
    def first_ts(code):
        for ev in timed_events:
            try:
                if (
                    _safe_int(
                        ev.get("action_code"), default=0, source="event.action_code"
                    )
                    == code
                ):
                    return ts_of(ev)
            except (TypeError, ValueError):
                continue
        return None

    arrived_at = first_ts(ACTION_CODE_ARRIVED_DEST)
    origin_departed_at = first_ts(ACTION_CODE_DEPARTED_ORIG)
    import_cleared_at = first_ts(ACTION_CODE_IMPORT_CLEAR)
    delivered_at = first_ts(ACTION_CODE_DELIVERED)
    returned_at = first_ts(ACTION_CODE_RETURNED)

    tracking_number = None
    if events:
        # Some events embed tracking implicitly; we don't have it here — caller
        # usually passes tracking_number in upsert_target() and we look it up.
        tracking_number = None

    try:
        with db_connect() as conn, conn.cursor() as cur:
            # Upsert summary row
            cur.execute(
                sql.SQL("""
                INSERT INTO logistics_tracking (
                    order_id, shop_id, tracking_number, n_events,
                    first_event_at, last_event_at,
                    last_action_code, last_description,
                    final_status, arrived_overseas, arrived_at,
                    origin_departed_at, import_cleared_at,
                    delivered_at, returned_at, raw
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (order_id) DO UPDATE SET
                    shop_id            = EXCLUDED.shop_id,
                    tracking_number    = COALESCE(EXCLUDED.tracking_number, logistics_tracking.tracking_number),
                    n_events           = EXCLUDED.n_events,
                    first_event_at     = EXCLUDED.first_event_at,
                    last_event_at      = EXCLUDED.last_event_at,
                    last_action_code   = EXCLUDED.last_action_code,
                    last_description   = EXCLUDED.last_description,
                    final_status       = EXCLUDED.final_status,
                    arrived_overseas   = EXCLUDED.arrived_overseas,
                    arrived_at         = EXCLUDED.arrived_at,
                    origin_departed_at = EXCLUDED.origin_departed_at,
                    import_cleared_at  = EXCLUDED.import_cleared_at,
                    delivered_at       = EXCLUDED.delivered_at,
                    returned_at        = EXCLUDED.returned_at,
                    raw                = EXCLUDED.raw,
                    synced_at          = now()
            """),
                (
                    order_id,
                    shop_id,
                    tracking_number,
                    n_events,
                    first_event_at,
                    last_event_at,
                    _int(last.get("action_code")),
                    _str(last.get("description")),
                    final_status,
                    arrived_overseas,
                    arrived_at,
                    origin_departed_at,
                    import_cleared_at,
                    delivered_at,
                    returned_at,
                    json.dumps({"tracking": events}, ensure_ascii=False, default=str),
                ),
            )

            # Upsert per-event rows (idempotent on PK)
            for ev in events:
                t = ts_of(ev)
                ac = ev.get("action_code")
                desc = ev.get("description")
                if t is None or ac is None:
                    continue
                loc = _extract_location(desc or "")
                cur.execute(
                    sql.SQL("""
                    INSERT INTO logistics_tracking_events
                        (order_id, action_code, event_time, description, location)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (order_id, action_code, event_time) DO UPDATE SET
                        description = EXCLUDED.description,
                        location    = EXCLUDED.location,
                        synced_at   = now()
                """),
                    (order_id, int(ac), int(t), _str(desc), loc),
                )

            # Mark sync target as done
            cur.execute(
                sql.SQL("""
                INSERT INTO logistics_sync_targets
                    (order_id, shop_id, last_synced_at, last_n_events, needs_resync)
                VALUES (%s, %s, now(), %s, false)
                ON CONFLICT (order_id) DO UPDATE SET
                    last_synced_at = now(),
                    last_n_events  = EXCLUDED.last_n_events,
                    needs_resync   = false
            """),
                (order_id, shop_id, n_events),
            )

            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.error(f"[tts-erp] persist_logistics_tracking({order_id}) failed: {e}\n")
        return False


def persist_logistics_target(order_id: str, shop_id: str) -> bool:
    """Mark an order_id as a sync target (insert only, ignore on conflict)."""
    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("""
                INSERT INTO logistics_sync_targets (order_id, shop_id, needs_resync)
                VALUES (%s, %s, true)
                ON CONFLICT (order_id) DO UPDATE SET
                    needs_resync = true
            """),
                (order_id, shop_id),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.error(f"[tts-erp] persist_logistics_target({order_id}) failed: {e}\n")
        return False


def persist_logistics_tracking_number(order_id: str, tracking_number: str) -> bool:
    """Backfill the denormalized tracking_number on logistics_tracking row
    (we don't get it from the tracking endpoint itself; pull from order_shippings)."""
    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("""
                UPDATE logistics_tracking
                SET tracking_number = %s
                WHERE order_id = %s AND tracking_number IS NULL
            """),
                (tracking_number, order_id),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.error(
            f"[tts-erp] persist_logistics_tracking_number({order_id}) failed: {e}\n"
        )
        return False


def persist_shop(
    shop_id: str,
    name: str | None,
    region: str | None,
    cipher: str | None,
    seller_type: str | None,
) -> bool:
    # W1.3 (2026-08-27): the `cipher` parameter is accepted for call-site
    # compatibility but NEVER persisted. shop_cipher is a live signing
    # credential whose single source of truth is oauth_receiver
    # (encrypted bytea) — tts_erp must not hold a plaintext copy.
    # The shops.shop_cipher column itself is dropped in Wave 2.
    del cipher
    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("""
                INSERT INTO shops (shop_id, shop_name, shop_region, seller_type, last_seen_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (shop_id) DO UPDATE SET
                    shop_name    = COALESCE(EXCLUDED.shop_name,    shops.shop_name),
                    shop_region  = COALESCE(EXCLUDED.shop_region,  shops.shop_region),
                    seller_type  = COALESCE(EXCLUDED.seller_type,  shops.seller_type),
                    last_seen_at = now()
            """),
                (shop_id, name, region, seller_type),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.error(f"  persist_shop({shop_id}) failed: {e}\n")
        return False


def persist_miaoshou_shop(platform: str, site: str, shop) -> bool:
    """Insert or update a 妙手 ERP shop in the DB.

    Args:
        shop: Shop Pydantic model from miaoshou SDK
    """
    import json as _json

    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("""
                    INSERT INTO miaoshou_shops (
                        shop_id, platform, site, platform_shop_name, shop_nick,
                        parent_shop_id, is_cb, is_cnsc, status, gmt_expire, gmt_last_auth, raw_json
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (platform, site, shop_id) DO UPDATE SET
                        platform_shop_name = EXCLUDED.platform_shop_name,
                        shop_nick          = EXCLUDED.shop_nick,
                        parent_shop_id     = EXCLUDED.parent_shop_id,
                        is_cb              = EXCLUDED.is_cb,
                        is_cnsc            = EXCLUDED.is_cnsc,
                        status             = EXCLUDED.status,
                        gmt_expire         = EXCLUDED.gmt_expire,
                        gmt_last_auth      = EXCLUDED.gmt_last_auth,
                        raw_json           = EXCLUDED.raw_json,
                        synced_at          = now()
                """),
                (
                    shop.shopId,
                    platform,
                    site,
                    shop.platformShopName,
                    shop.shopNick,
                    shop.parentShopId,
                    shop.isCb,
                    shop.isCnsc,
                    shop.status,
                    shop.gmtExpire,
                    shop.gmtLastAuth,
                    _json.dumps(shop.model_dump(), ensure_ascii=False),
                ),
            )
        return True
    except Exception as e:  # noqa: BLE001
        log.error("[tts-erp] persist_miaoshou_shop failed: %s", e)
        return False


def persist_miaoshou_price_template(platform: str, template) -> bool:
    """Insert or update a 妙手 ERP pricing template in the DB.

    Args:
        platform: 平台代号（'tiktok' 等，仅审计；该表 PK 是 price_template_id 全局唯一）
        template: PriceTemplateInfo Pydantic model from miaoshou SDK
            (miaoshou.endpoints.tk_collect_box.PriceTemplateInfo)

    PK: price_template_id.
    Fields stored: schema.sql miaoshou_price_templates 全 44 列 + raw_json。
    """
    import json as _json

    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("""
                    INSERT INTO miaoshou_price_templates (
                        price_template_id, app_account_id, sub_app_account_id,
                        platform, site, name, remark, currency, display_weight_unit,
                        profit_type, profit_percent, fixed_profit_amount, exchange_rate,
                        discount, price_tail_compute_type, price_tail,
                        price_process_decimal_type, logistics_compute_type,
                        weight_ref_type, first_weight_charge, first_weight_interval,
                        continued_weight_charge, continued_weight_interval,
                        logistics_charge, platform_charge_percent, payment_charge_percent,
                        activity_charge_percent, withdraw_charge_percent, other_charge,
                        is_cal_light_cargo, light_cargo_coefficient,
                        weight_logistics_charge_list, domestic_logistics_compute_type,
                        domestic_logistics_first_weight_charge,
                        domestic_logistics_first_weight_interval,
                        domestic_logistics_continued_weight_charge,
                        domestic_logistics_continued_weight_interval,
                        domestic_logistics_charge, buyer_logistic_charge,
                        seller_logistic_charge, has_seller_logistic_charge,
                        official_tpl_mode, official_tpl_logistics_channel,
                        snapshot_id, gmt_create, gmt_modified, raw_json
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (price_template_id) DO UPDATE SET
                        app_account_id          = EXCLUDED.app_account_id,
                        sub_app_account_id      = EXCLUDED.sub_app_account_id,
                        platform                = EXCLUDED.platform,
                        site                    = EXCLUDED.site,
                        name                    = EXCLUDED.name,
                        remark                  = EXCLUDED.remark,
                        currency                = EXCLUDED.currency,
                        display_weight_unit     = EXCLUDED.display_weight_unit,
                        profit_type             = EXCLUDED.profit_type,
                        profit_percent          = EXCLUDED.profit_percent,
                        fixed_profit_amount     = EXCLUDED.fixed_profit_amount,
                        exchange_rate           = EXCLUDED.exchange_rate,
                        discount                = EXCLUDED.discount,
                        price_tail_compute_type = EXCLUDED.price_tail_compute_type,
                        price_tail              = EXCLUDED.price_tail,
                        price_process_decimal_type = EXCLUDED.price_process_decimal_type,
                        logistics_compute_type  = EXCLUDED.logistics_compute_type,
                        weight_ref_type         = EXCLUDED.weight_ref_type,
                        first_weight_charge     = EXCLUDED.first_weight_charge,
                        first_weight_interval   = EXCLUDED.first_weight_interval,
                        continued_weight_charge = EXCLUDED.continued_weight_charge,
                        continued_weight_interval = EXCLUDED.continued_weight_interval,
                        logistics_charge        = EXCLUDED.logistics_charge,
                        platform_charge_percent = EXCLUDED.platform_charge_percent,
                        payment_charge_percent  = EXCLUDED.payment_charge_percent,
                        activity_charge_percent = EXCLUDED.activity_charge_percent,
                        withdraw_charge_percent = EXCLUDED.withdraw_charge_percent,
                        other_charge            = EXCLUDED.other_charge,
                        is_cal_light_cargo      = EXCLUDED.is_cal_light_cargo,
                        light_cargo_coefficient = EXCLUDED.light_cargo_coefficient,
                        weight_logistics_charge_list = EXCLUDED.weight_logistics_charge_list,
                        domestic_logistics_compute_type = EXCLUDED.domestic_logistics_compute_type,
                        domestic_logistics_first_weight_charge = EXCLUDED.domestic_logistics_first_weight_charge,
                        domestic_logistics_first_weight_interval = EXCLUDED.domestic_logistics_first_weight_interval,
                        domestic_logistics_continued_weight_charge = EXCLUDED.domestic_logistics_continued_weight_charge,
                        domestic_logistics_continued_weight_interval = EXCLUDED.domestic_logistics_continued_weight_interval,
                        domestic_logistics_charge = EXCLUDED.domestic_logistics_charge,
                        buyer_logistic_charge   = EXCLUDED.buyer_logistic_charge,
                        seller_logistic_charge  = EXCLUDED.seller_logistic_charge,
                        has_seller_logistic_charge = EXCLUDED.has_seller_logistic_charge,
                        official_tpl_mode       = EXCLUDED.official_tpl_mode,
                        official_tpl_logistics_channel = EXCLUDED.official_tpl_logistics_channel,
                        snapshot_id             = EXCLUDED.snapshot_id,
                        gmt_create              = EXCLUDED.gmt_create,
                        gmt_modified            = EXCLUDED.gmt_modified,
                        raw_json                = EXCLUDED.raw_json,
                        synced_at               = now()
                """),
                (
                    template.priceTemplateId,
                    template.appAccountId,
                    template.subAppAccountId,
                    getattr(template, "platform", platform),
                    template.site,
                    template.name,
                    template.remark,
                    template.currency,
                    template.displayWeightUnit,
                    template.profitType,
                    template.profitPercent,
                    template.fixedProfitAmount,
                    template.exchangeRate,
                    template.discount,
                    template.priceTailComputeType,
                    template.priceTail,
                    template.priceProcessDecimalType,
                    template.logisticsComputeType,
                    template.weightRefType,
                    template.firstWeightCharge,
                    template.firstWeightInterval,
                    template.continuedWeightCharge,
                    template.continuedWeightInterval,
                    template.logisticsCharge,
                    template.platformChargePercent,
                    template.paymentChargePercent,
                    template.activityChargePercent,
                    template.withdrawChargePercent,
                    template.otherCharge,
                    template.isCalLightCargo,
                    template.lightCargoCoefficient,
                    template.weightLogisticsChargeList,
                    template.domesticLogisticsComputeType,
                    template.domesticLogisticsFirstWeightCharge,
                    template.domesticLogisticsFirstWeightInterval,
                    template.domesticLogisticsContinuedWeightCharge,
                    template.domesticLogisticsContinuedWeightInterval,
                    template.domesticLogisticsCharge,
                    template.buyerLogisticCharge,
                    template.sellerLogisticCharge,
                    template.hasSellerLogisticCharge,
                    template.officialTplMode,
                    template.officialTplLogisticsChannel,
                    template.snapshotId,
                    template.gmtCreate,
                    template.gmtModified,
                    _json.dumps(template.model_dump(), ensure_ascii=False),
                ),
            )
        return True
    except Exception as e:  # noqa: BLE001
        log.error("[tts-erp] persist_miaoshou_price_template failed: %s", e)
        return False


def persist_miaoshou_collect_box_detail(platform: str, detail) -> bool:
    """Insert or update a 妙手 公共采集箱详情 in the DB.

    Args:
        platform: 平台代号（PK 组成部分）
        detail: CollectBoxDetailDetail Pydantic model from miaoshou SDK
            (miaoshou.endpoints.tk_collect_box.CollectBoxDetailDetail)

    PK: (platform, common_collect_box_detail_id).
    """
    import json as _json

    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("""
                    INSERT INTO miaoshou_collect_box_details (
                        platform, common_collect_box_detail_id,
                        app_account_id, sub_app_account_id, item_num, title,
                        thumbnail, list_thumbnail, price, min_sku_price,
                        max_sku_price, stock, remark, status, reason,
                        gmt_create, gmt_modified, weight, max_sku_weight,
                        min_sku_weight, common_collect_box_group_id,
                        common_collect_box_group_name, owner_sub_account_alias_name,
                        is_mark, is_cb, is_cnsc, raw_json
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (platform, common_collect_box_detail_id) DO UPDATE SET
                        app_account_id           = EXCLUDED.app_account_id,
                        sub_app_account_id       = EXCLUDED.sub_app_account_id,
                        item_num                 = EXCLUDED.item_num,
                        title                    = EXCLUDED.title,
                        thumbnail                = EXCLUDED.thumbnail,
                        list_thumbnail           = EXCLUDED.list_thumbnail,
                        price                    = EXCLUDED.price,
                        min_sku_price            = EXCLUDED.min_sku_price,
                        max_sku_price            = EXCLUDED.max_sku_price,
                        stock                    = EXCLUDED.stock,
                        remark                   = EXCLUDED.remark,
                        status                   = EXCLUDED.status,
                        reason                   = EXCLUDED.reason,
                        gmt_create               = EXCLUDED.gmt_create,
                        gmt_modified             = EXCLUDED.gmt_modified,
                        weight                   = EXCLUDED.weight,
                        max_sku_weight           = EXCLUDED.max_sku_weight,
                        min_sku_weight           = EXCLUDED.min_sku_weight,
                        common_collect_box_group_id = EXCLUDED.common_collect_box_group_id,
                        common_collect_box_group_name = EXCLUDED.common_collect_box_group_name,
                        owner_sub_account_alias_name = EXCLUDED.owner_sub_account_alias_name,
                        is_mark                  = EXCLUDED.is_mark,
                        is_cb                    = EXCLUDED.is_cb,
                        is_cnsc                  = EXCLUDED.is_cnsc,
                        raw_json                 = EXCLUDED.raw_json,
                        synced_at                = now()
                """),
                (
                    platform,
                    detail.commonCollectBoxDetailId,
                    detail.appAccountId,
                    detail.subAppAccountId,
                    detail.itemNum,
                    detail.title,
                    detail.thumbnail,
                    detail.listThumbnail,
                    detail.price,
                    detail.minSkuPrice,
                    detail.maxSkuPrice,
                    detail.stock,
                    detail.remark,
                    detail.status,
                    detail.reason,
                    detail.gmtCreate,
                    detail.gmtModified,
                    detail.weight,
                    detail.maxSkuWeight,
                    detail.minSkuWeight,
                    detail.commonCollectBoxGroupId,
                    detail.commonCollectBoxGroupName,
                    detail.ownerSubAccountAliasName,
                    detail.isMark,
                    detail.isCb,
                    detail.isCnsc,
                    _json.dumps(detail.model_dump(), ensure_ascii=False),
                ),
            )
        return True
    except Exception as e:  # noqa: BLE001
        log.error("[tts-erp] persist_miaoshou_collect_box_detail failed: %s", e)
        return False


def persist_miaoshou_move_collect_task(platform: str, task) -> bool:
    """Insert or update a 妙手 发布任务 (move collect task) in the DB.

    Args:
        platform: 平台代号（PK 组成部分）
        task: MoveCollectDetail Pydantic model from miaoshou SDK
            (miaoshou.endpoints.tk_collect_box.MoveCollectDetail)

    PK: (platform, move_collect_task_detail_id).
    """
    import json as _json

    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("""
                    INSERT INTO miaoshou_move_collect_tasks (
                        platform, move_collect_task_detail_id,
                        collect_box_detail_id, shop_id, item_num, cid, source,
                        source_site, source_item_id, title, thumbnail, is_timing,
                        status, reason, gmt_create, gmt_modified, platform_item_id,
                        is_renew_item, shop_name, site_name, site, source_item_url,
                        item_edit_url, breadcrumb, owner_sub_app_account_id,
                        owner_sub_account_alias_name, raw_json
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (platform, move_collect_task_detail_id) DO UPDATE SET
                        collect_box_detail_id    = EXCLUDED.collect_box_detail_id,
                        shop_id                  = EXCLUDED.shop_id,
                        item_num                 = EXCLUDED.item_num,
                        cid                      = EXCLUDED.cid,
                        source                   = EXCLUDED.source,
                        source_site              = EXCLUDED.source_site,
                        source_item_id           = EXCLUDED.source_item_id,
                        title                    = EXCLUDED.title,
                        thumbnail                = EXCLUDED.thumbnail,
                        is_timing                = EXCLUDED.is_timing,
                        status                   = EXCLUDED.status,
                        reason                   = EXCLUDED.reason,
                        gmt_create               = EXCLUDED.gmt_create,
                        gmt_modified             = EXCLUDED.gmt_modified,
                        platform_item_id         = EXCLUDED.platform_item_id,
                        is_renew_item            = EXCLUDED.is_renew_item,
                        shop_name                = EXCLUDED.shop_name,
                        site_name                = EXCLUDED.site_name,
                        site                     = EXCLUDED.site,
                        source_item_url          = EXCLUDED.source_item_url,
                        item_edit_url            = EXCLUDED.item_edit_url,
                        breadcrumb               = EXCLUDED.breadcrumb,
                        owner_sub_app_account_id = EXCLUDED.owner_sub_app_account_id,
                        owner_sub_account_alias_name = EXCLUDED.owner_sub_account_alias_name,
                        raw_json                 = EXCLUDED.raw_json,
                        synced_at                = now()
                """),
                (
                    platform,
                    task.moveCollectTaskDetailId,
                    task.collectBoxDetailId,
                    task.shopId,
                    task.itemNum,
                    task.cid,
                    task.source,
                    task.sourceSite,
                    task.sourceItemId,
                    task.title,
                    task.thumbnail,
                    task.isTiming,
                    task.status,
                    task.reason,
                    task.gmtCreate,
                    task.gmtModified,
                    task.platformItemId,
                    task.isRenewItem,
                    task.shopName,
                    task.siteName,
                    task.site,
                    task.sourceItemUrl,
                    task.itemEditUrl,
                    task.breadcrumb,
                    task.ownerSubAppAccountId,
                    task.ownerSubAccountAliasName,
                    _json.dumps(task.model_dump(), ensure_ascii=False),
                ),
            )
        return True
    except Exception as e:  # noqa: BLE001
        log.error("[tts-erp] persist_miaoshou_move_collect_task failed: %s", e)
        return False


def log_sync(
    shop_id: str, sync_type: str, status: str, rows: int = 0, error: str | None = None
) -> None:
    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("""
                INSERT INTO sync_log (shop_id, sync_type, finished_at, rows_affected, status, error_message)
                VALUES (%s, %s, now(), %s, %s, %s)
            """),
                (shop_id, sync_type, rows, status, error),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        log.error(f"  log_sync failed: {e}\n")
    _last_syncs.append(
        {
            "ts": time.time(),
            "shop_id": shop_id,
            "sync_type": sync_type,
            "status": status,
            "rows": rows,
            "error": error,
        }
    )


# ---------- type coercion helpers ----------
def _int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(value, default: int = 0, source: str = "unknown") -> int:
    """Coerce per-request input to int without raising 500 on bad input.

    Use for query-string / body params from external clients — invalid input
    should default + log, not 500. For env-var startup-time ints, keep plain
    int() so misconfiguration fails fast.

    Args:
        value: Raw value from request (str, int, None, etc.).
        default: Returned when value is None/empty/can't parse.
        source: Where the value came from (for log attribution).

    Returns:
        ``int(value)`` on success, else ``default``.
    """
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        log.warning(
            "safe_int: invalid value %r from %s, using default %s",
            value,
            source,
            default,
        )
        return default


def _str(v) -> str | None:
    if v is None:
        return None
    s = str(v)
    return s if s else None


def _decimal(v):
    from decimal import Decimal

    try:
        return Decimal(str(v)) if v is not None else None
    except Exception:  # noqa: BLE001
        return None


# ---------- main ----------
# tts_erp.py 之前是 stdlib BaseHTTPRequestHandler 服务（监听 9877）。
# Wave 3 起 FastAPI (tdd/tts_erp_fastapi:app) 是生产服务；tts_erp.py 缩减为
# 共享 helper 模块（db_connect / persist_* / log_sync / 类型 coercion）。
# 老 stdlib main() 和 Handler 类已随 Wave 4.1 退役删除。
if __name__ == "__main__":
    import sys

    print(
        "tts_erp.py is now a helper module; run the FastAPI service instead:\n"
        "  bash restart.sh  (or: uvicorn tts_erp_fastapi:app --port 9877)",
        file=sys.stderr,
    )
    sys.exit(1)
