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

import logging
import os
import sys

sys.path.insert(0, "/home/schan/setup/lib")
from log_helper import get_logger, setup_logging

# Module-level logger (inherits from app logger set by main() or FastAPI)
log = get_logger("tts-erp")

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg
import psycopg.rows
from psycopg import sql

from tts_signing import (
    resolve_path,
    tiktok_request,
)

# ---------- Config ----------
HOST = os.environ.get("TTS_ERP_HOST", "0.0.0.0")
PORT = int(os.environ.get("TTS_ERP_PORT", "9877"))
OAUTH_RECEIVER_URL = os.environ.get(
    "OAUTH_RECEIVER_URL", "http://127.0.0.1:9876"
).rstrip("/")
TIKTOK_APP_KEY = os.environ.get("TIKTOK_APP_KEY", "")
TIKTOK_APP_SECRET = os.environ.get("TIKTOK_APP_SECRET", "")
TIKTOK_API_HOST = os.environ.get(
    "TIKTOK_API_HOST", "https://open-api.tiktokglobalshop.com"
)
TTS_ERP_DB_URL = os.environ.get("TTS_ERP_DB_URL", "")
TTS_ERP_HTTP_TIMEOUT = int(float(os.environ.get("TTS_ERP_HTTP_TIMEOUT", "30")))

# In-memory last sync log (mirrored to PG via sync_log table)
_last_syncs: deque[dict] = deque(maxlen=50)


# ---------- DB helpers ----------
def db_connect():
    if not TTS_ERP_DB_URL:
        raise RuntimeError("TTS_ERP_DB_URL not configured")
    return psycopg.connect(TTS_ERP_DB_URL, connect_timeout=5)


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
                cur.execute(sql.SQL("SELECT to_regclass(%s)"), (tbl,))
                row = cur.fetchone()
                if row is None or row[0] is None:
                    log.error(
                        f"[tts-erp] WARN: table '{tbl}' missing — run schema.sql\n"
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
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
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
                        json.dumps(order_raw, ensure_ascii=False, default=str),
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
    sql_query = f"""
        INSERT INTO statement_transactions ({col_list})
        VALUES ({placeholders})
        ON CONFLICT (txn_id) DO UPDATE SET {updates}, synced_at = now()
    """
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
                if _safe_int(ev.get("action_code"), default=0, source="event.action_code") == code:
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
    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("""
                INSERT INTO shops (shop_id, shop_name, shop_region, shop_cipher, seller_type, last_seen_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (shop_id) DO UPDATE SET
                    shop_name    = COALESCE(EXCLUDED.shop_name,    shops.shop_name),
                    shop_region  = COALESCE(EXCLUDED.shop_region,  shops.shop_region),
                    shop_cipher  = COALESCE(EXCLUDED.shop_cipher,  shops.shop_cipher),
                    seller_type  = COALESCE(EXCLUDED.seller_type,  shops.seller_type),
                    last_seen_at = now()
            """),
                (shop_id, name, region, cipher, seller_type),
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


# ---------- HTTP handler ----------
class Handler(BaseHTTPRequestHandler):
    server_version = "TtsErp/1.0"

    def _send(self, code: int, obj) -> None:
        data = json.dumps(obj, ensure_ascii=False, indent=2, default=str).encode(
            "utf-8"
        )
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict:
        n = _safe_int(self.headers.get("Content-Length"), default=0, source="http.Content-Length")
        if n == 0:
            return {}
        body = self.rfile.read(n).decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"_raw_body": body}

    def _require_shop_token(self, shop_id: str) -> tuple[str, str, str] | dict:
        """Returns (access_token, shop_cipher, shop_region) or error dict."""
        tok = fetch_token(shop_id, reveal=True)
        if not tok or tok.get("_error"):
            return {"_error": f"failed to fetch token: {tok}"}
        at = tok.get("access_token")
        cipher = tok.get("shop_cipher")
        region = tok.get("shop_region") or ""
        if not at or not cipher:
            return {
                "_error": f"token response missing access_token or shop_cipher: {tok}"
            }
        return at, cipher, region

    # ----- routing -----
    def do_GET(self):
        try:
            url = urllib.parse.urlparse(self.path)
            path = url.path.rstrip("/") or "/"
            params = urllib.parse.parse_qs(url.query)

            if path == "/healthz":
                return self._send(
                    200,
                    {"status": "ok", "ts": time.time(), "version": self.server_version},
                )

            if path == "/" or path == "":
                return self._send(
                    200,
                    {
                        "service": "tts-erp",
                        "version": "1.0",
                        "endpoints": "see /endpoints or AGENTS.md",
                    },
                )

            if path == "/endpoints":
                return self._send(
                    200,
                    {
                        "passthrough": [
                            "GET /shops",
                            "GET /shops/<shop_id>",
                            "GET /token/<shop_id>?reveal=1",
                        ],
                        "order_api_proxy": [
                            "POST /orders/search              (paging/sort in query string)",
                            "GET  /orders/<order_id>          (→ /order/202309/orders?ids=<id>)",
                        ],
                        "finance_api_proxy": [
                            "GET  /finance/statements         (paging/sort in query; sort_field REQUIRED)",
                            "GET  /finance/payments           (paging/sort in query; sort_field REQUIRED)",
                        ],
                        "return_refund_api_proxy": [
                            "POST /returns/search             (→ /return_refund/202309/returns/search, read-only)",
                            "POST /cancellations/search       (→ /return_refund/202309/cancellations/search, read-only)",
                            "POST /returns                    (501: write CREATE endpoint, not integrated)",
                            "POST /cancellations              (501: write CREATE endpoint, not integrated)",
                        ],
                        "logistics_tracking_api_proxy": [
                            "GET  /logistics/orders/<order_id>/tracking  (→ /fulfillment/202309/orders/<id>/tracking, auto-persists)",
                            "NOTE: /logistics/202604/* returns 11007009 on this app; 202309 fulfillment is the working module",
                        ],
                        "miaoshou_open_platform_proxy": [
                            "POST /miaoshou/<domain>/<method>            (36 出站接口；domain ∈ orders/fees/refunds/arbitrations/closes/complaints/queries/accounts/products/logistics/aftersales/tests)",
                            "POST /miaoshou/callback/<node-alias>        (17 个回调节器；orderStatus → node 自动派发)",
                            "POST /miaoshou/callback/all                 (按 orderStatus 字段自动选 model)",
                            "Doc: https://s.apifox.cn/fd54e57e-9b98-4c34-bada-306221c39e68（实际指向 openapi.wanshifu.com）",
                        ],
                        "local_db": [
                            "GET  /db/orders?shop_id=&status=",
                            "GET  /db/orders/<order_id>",
                            "GET  /db/orders/<order_id>/items",
                            "GET  /db/orders/<order_id>/shipping",
                            "GET  /db/statements?shop_id=",
                            "GET  /db/payments?shop_id=&status=",
                            "GET  /db/returns?shop_id=&status=",
                            "GET  /db/cancellations?shop_id=&status=",
                            "GET  /db/logistics_tracking?shop_id=&final_status=&arrived_overseas=&tracking_number=&order_id=",
                            "GET  /db/logistics_events?order_id=&action_code=&limit=",
                            "GET  /db/sync_log",
                        ],
                        "sync": [
                            "POST /sync/orders        body: {shop_id, order_status?, create_time_ge?, create_time_lt?, page_size?}",
                            "POST /sync/order/<order_id>",
                            "POST /sync/statements     body: {shop_id, statement_time_ge?, statement_time_lt?, page_size?}",
                            "POST /sync/payments       body: {shop_id, create_time_ge?, create_time_lt?, page_size?}",
                            "POST /sync/returns        body: {shop_id, create_time_ge?, create_time_lt?, page_size?}",
                            "POST /sync/cancellations  body: {shop_id, create_time_ge?, create_time_lt?, page_size?}",
                            "POST /sync/logistics_tracking  body: {shop_id, order_ids?: [...], all_with_tracking?: bool, limit?, max_per_run?}",
                            "POST /sync/miaoshou_shops        query: {platform, site, page_no?, page_size?}",
                        ],
                    },
                )

            # --- OAuth passthrough ---
            if path == "/shops":
                return self._send(200, _proxy_get(f"{OAUTH_RECEIVER_URL}/tokens/shops"))

            if path.startswith("/shops/"):
                shop_id = path[len("/shops/") :].strip("/")
                return self._send(
                    200,
                    fetch_shop_meta(shop_id) or {"_error": f"shop {shop_id} not found"},
                )

            if path.startswith("/token/"):
                shop_id = path[len("/token/") :].strip("/")
                return self._send(
                    200, fetch_token(shop_id, reveal=True) or {"_error": "no token"}
                )

            # --- Order API (read & write) ---
            if path == "/orders/search":
                return self._send(
                    400, {"_error": "POST /orders/search (use POST handler)"}
                )

            if path.startswith("/orders/"):
                rest = path[len("/orders/") :].rstrip("/")
                parts = rest.split("/")
                if len(parts) == 1:
                    order_id = parts[0]
                    if order_id == "search" or order_id == "list":
                        return self._send(400, {"_error": f"POST /orders/{order_id}"})
                    # GET /orders/<order_id>
                    # 202309 spec: detail endpoint is GET /order/202309/orders?ids=<id>
                    # (path-with-id variant returns 36009009 Invalid path)
                    return self._proxy_order(
                        "GET",
                        "/order/202309/orders",
                        order_id=order_id,
                        params=params,
                        body=None,
                        override_path_qp={"ids": order_id},
                    )
                if len(parts) == 2:
                    order_id, action = parts
                    # 202309 spec: action endpoints don't exist in /order/202309/orders/
                    # (cancel/confirm/ship/tracking all return 36009009 Invalid path)
                    # Return 501 Not Implemented to surface the actual upstream error.
                    return self._send(
                        501,
                        {
                            "_error": f"action '{action}' not available on 202309 Order module",
                            "_note": "TikTok 202309 Order module is read-only. Write actions live in Fulfillment/Reverse Logistics modules. See handoff.md.",
                        },
                    )

            # --- Finance / Statement / Payment proxy (GET) ---
            # /finance/statements   → GET /finance/202309/statements
            # /finance/payments     → GET /finance/202309/payments
            # Both require sort_field in query (TikTok returns 36009004 otherwise)
            if path in ("/finance/statements", "/finance/payments"):
                kind = path.rsplit("/", 1)[-1]  # "statements" or "payments"
                upstream_path = f"/finance/202309/{kind}"
                return self._proxy_finance("GET", upstream_path, params)

            # Logistics tracking proxy (GET): /logistics/orders/{order_id}/tracking
            if path.startswith("/logistics/orders/") and path.endswith("/tracking"):
                rest = path[len("/logistics/orders/") : -len("/tracking")].strip("/")
                if not rest:
                    return self._send(400, {"_error": "missing order_id in path"})
                return self._proxy_logistics_tracking(rest, params)

            # --- Local DB reads ---
            if path == "/db/orders":
                return self._db_list_orders(params)
            if path == "/db/statements":
                return self._db_list_statements(params)
            if path == "/db/payments":
                return self._db_list_payments(params)
            if path == "/db/returns":
                return self._db_list_returns(params)
            if path == "/db/cancellations":
                return self._db_list_cancellations(params)
            if path == "/db/logistics_tracking":
                return self._db_list_logistics_tracking(params)
            if path == "/db/logistics_events":
                return self._db_list_logistics_events(params)
            if path == "/db/miaoshou_shops":
                return self._db_list_miaoshou_shops(params)
            if path == "/db/sync_log":
                return self._send(200, {"items": list(_last_syncs)})
            if path.startswith("/db/orders/"):
                rest = path[len("/db/orders/") :].rstrip("/")
                parts = rest.split("/")
                order_id = parts[0]
                if len(parts) == 1:
                    return self._db_get_order(order_id)
                if len(parts) == 2 and parts[1] == "items":
                    return self._db_get_order_items(order_id)
                if len(parts) == 2 and parts[1] == "shipping":
                    return self._db_get_order_shipping(order_id)

            return self._send(404, {"_error": f"not found: {path}"})

        except Exception as e:  # noqa: BLE001
            import traceback

            log.error(f"[tts-erp] unhandled GET: {e}\n{traceback.format_exc()}\n")
            return self._send(500, {"_error": str(e)})

    def do_POST(self):
        try:
            url = urllib.parse.urlparse(self.path)
            path = url.path.rstrip("/") or "/"
            params = urllib.parse.parse_qs(url.query)
            body = self._read_json_body()

            if path == "/orders/search":
                return self._proxy_order(
                    "POST", "/order/202309/orders/search", body=body, params=params
                )
            if path.startswith("/orders/"):
                rest = path[len("/orders/") :].rstrip("/")
                parts = rest.split("/")
                if len(parts) == 2:
                    # 202309 Order module is READ-ONLY — write actions live in
                    # Fulfillment / Reverse Logistics modules. Surface the actual
                    # upstream error instead of silently forwarding.
                    order_id, action = parts
                    return self._send(
                        501,
                        {
                            "_error": f"action '{action}' not available on 202309 Order module",
                            "_note": "TikTok 202309 Order module is read-only. Write actions live in Fulfillment/Reverse Logistics modules. See handoff.md.",
                            "order_id": order_id,
                        },
                    )

            if path == "/sync/orders":
                return self._sync_orders(body)
            if path.startswith("/sync/order/"):
                order_id = path[len("/sync/order/") :].strip("/")
                return self._sync_one_order(order_id)
            if path == "/sync/statements":
                return self._sync_statements(body)
            if path == "/sync/payments":
                return self._sync_payments(body)
            if path == "/sync/returns":
                return self._sync_returns(body)
            if path == "/sync/cancellations":
                return self._sync_cancellations(body)
            if path == "/sync/logistics_tracking":
                return self._sync_logistics_tracking(body)

            if path == "/sync/miaoshou_shops":
                return self._sync_miaoshou_shops(params)

            # Logistics tracking proxy (read-only, single order)
            if path.startswith("/logistics/orders/") and path.endswith("/tracking"):
                rest = path[len("/logistics/orders/") : -len("/tracking")].strip("/")
                if not rest:
                    return self._send(400, {"_error": "missing order_id in path"})
                return self._proxy_logistics_tracking(rest, params)

            # Return / Refund / Cancellation proxy (read-only list endpoints)
            if path == "/returns/search":
                return self._proxy_refund("returns", body)
            if path == "/cancellations/search":
                return self._proxy_refund("cancellations", body)
            # Write endpoints (POST without /search) — NOT integrated per user
            # instruction (no high-risk write testing). Surface 501 cleanly.
            if path in ("/returns", "/cancellations"):
                kind = path.lstrip("/")  # "returns" or "cancellations"
                return self._send(
                    501,
                    {
                        "_error": f"POST /return_refund/202309/{kind} is a CREATE endpoint (write) — not integrated",
                        "_note": "Per user instruction, no high-risk write endpoints are tested/integrated. See handoff.md §return_refund.",
                    },
                )

            return self._send(404, {"_error": f"not found: {path}"})

        except Exception as e:  # noqa: BLE001
            import traceback

            log.error(f"[tts-erp] unhandled POST: {e}\n{traceback.format_exc()}\n")
            return self._send(500, {"_error": str(e)})

    # ----- Order proxy -----
    def _proxy_order(
        self,
        method: str,
        path_template: str,
        params: dict,
        body: dict | None = None,
        order_id: str | None = None,
        action: str | None = None,
        override_path_qp: dict | None = None,
    ):
        shop_id = (params.get("shop_id") or [None])[0]
        if not shop_id:
            return self._send(
                400,
                {
                    "_error": "missing shop_id query param (e.g. ?shop_id=7494763368967603447)"
                },
            )
        if not order_id and body and "order_id" in body:
            order_id = body["order_id"]
        if not order_id and "{" in path_template and not override_path_qp:
            return self._send(
                400, {"_error": f"missing order_id in path or body for {path_template}"}
            )

        creds = self._require_shop_token(shop_id)
        if isinstance(creds, dict) and creds.get("_error"):
            return self._send(502, creds)
        access_token, shop_cipher, _region = creds

        # Forward URL query params (page_size, page_token, sort_field, order_status
        # etc.) as extra_params — TikTok /orders/search takes these in the query
        # string, not the body. (See 36009004 PageSize is a required field.)
        forwarded: dict[str, str] = {"shop_cipher": shop_cipher}
        for k, v in params.items():
            if k in ("shop_id",):
                continue
            # parse_qs returns list — flatten to first
            forwarded[k] = v[0] if isinstance(v, list) and v else str(v)
        # Some endpoints need order_id in QUERY instead of PATH
        # (e.g. /order/202309/orders?ids=<id>).
        if override_path_qp:
            forwarded.update(override_path_qp)

        path = (
            resolve_path(path_template, order_id=order_id)
            if (order_id and "{" in path_template)
            else path_template
        )
        # shop_cipher is the per-request query param TikTok requires
        result = tiktok_request(
            method=method,
            api_host=TIKTOK_API_HOST,
            path=path,
            access_token=access_token,
            app_key=TIKTOK_APP_KEY,
            app_secret=TIKTOK_APP_SECRET,
            body=body if method != "GET" else None,
            extra_params=forwarded,
            timeout=TTS_ERP_HTTP_TIMEOUT,
        )

        # Persist order details on successful reads
        data = result.get("data")
        if method == "GET" and result.get("code") == 0 and isinstance(data, dict):
            order = data.get("order") or data
            if isinstance(order, dict) and (order.get("id") or order.get("order_id")):
                persist_order(shop_id, order)
                # Refresh shop metadata
                persist_shop(
                    shop_id,
                    name=order.get("shop_name"),
                    region=_region,
                    cipher=shop_cipher,
                    seller_type=order.get("seller_type"),
                )
                log_sync(shop_id, "order_detail", "ok", rows=1)

        return self._send(200, result)

    # ----- Sync -----
    def _sync_orders(self, body: dict):
        shop_id = body.get("shop_id")
        if not shop_id:
            return self._send(400, {"_error": "missing shop_id in body"})
        page_size = _safe_int(body.get("page_size"), default=50, source="body.page_size")
        order_status = body.get("order_status")
        create_time_ge = body.get("create_time_ge")
        create_time_lt = body.get("create_time_lt")

        creds = self._require_shop_token(shop_id)
        if isinstance(creds, dict) and creds.get("_error"):
            return self._send(502, creds)
        access_token, shop_cipher, _region = creds

        # TikTok /orders/search quirk: page_size and other paging/sort params
        # go in the QUERY STRING, not the body. Body carries only filter criteria
        # (order_status, create_time_ge/lt, etc).
        # See probe_alt.py: 2026-08-16 confirmed working call.
        # Note: sort_order must be UPPERCASE "DESC" / "ASC" — lowercase 36009004
        extra_params: dict[str, str] = {
            "shop_cipher": shop_cipher,
            "page_size": str(min(page_size, 100)),
            "sort_field": "create_time",
            "sort_order": "DESC",
        }
        search_body: dict = {}
        if order_status is not None:
            # TikTok expects string, not int (per 36009004 type validation)
            search_body["order_status"] = str(order_status)
        if create_time_ge is not None:
            search_body["create_time_ge"] = _safe_int(create_time_ge, source="qs.create_time_ge")
        if create_time_lt is not None:
            search_body["create_time_lt"] = _safe_int(create_time_lt, source="qs.create_time_lt")

        # First page
        first = tiktok_request(
            "POST",
            TIKTOK_API_HOST,
            "/order/202309/orders/search",
            access_token,
            TIKTOK_APP_KEY,
            TIKTOK_APP_SECRET,
            body=search_body if search_body else None,
            extra_params=extra_params,
            timeout=TTS_ERP_HTTP_TIMEOUT,
        )
        if first.get("code") != 0:
            log_sync(shop_id, "orders_search", "error", error=str(first.get("message")))
            return self._send(502, first)

        data = first.get("data") or {}
        order_list = (
            data.get("order_list") or data.get("orders") or data.get("list") or []
        )
        saved = 0
        for o in order_list:
            if persist_order(shop_id, o):
                saved += 1

        total = data.get("total") or len(order_list)
        next_token = data.get("next_page_token") or data.get("page_token")
        pages = 1
        while next_token and pages < 50:  # safety cap
            extra_params["page_token"] = next_token
            nxt = tiktok_request(
                "POST",
                TIKTOK_API_HOST,
                "/order/202309/orders/search",
                access_token,
                TIKTOK_APP_KEY,
                TIKTOK_APP_SECRET,
                body=search_body if search_body else None,
                extra_params=extra_params,
                timeout=TTS_ERP_HTTP_TIMEOUT,
            )
            if nxt.get("code") != 0:
                break
            d = nxt.get("data") or {}
            for o in d.get("order_list") or d.get("orders") or d.get("list") or []:
                if persist_order(shop_id, o):
                    saved += 1
            next_token = d.get("next_page_token") or d.get("page_token")
            pages += 1

        log_sync(shop_id, "orders_search", "ok", rows=saved)
        return self._send(
            200, {"shop_id": shop_id, "saved": saved, "total": total, "pages": pages}
        )

    def _sync_one_order(self, order_id: str):
        # Use the proxy GET to also persist
        # (re-using the proxy helper)
        return (
            self._proxy_order(
                "GET",
                "/order/202309/orders/{order_id}",
                params={"shop_id": [order_id[:0] or ""]},
            )
            if False
            else self._do_sync_one(order_id)
        )

    def _do_sync_one(self, order_id: str):
        # We need a shop_id to get a token. Look it up from local DB or fail.
        try:
            with db_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT shop_id FROM orders WHERE order_id = %s LIMIT 1"),
                    (order_id,),
                )
                row = cur.fetchone()
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": f"db lookup failed: {e}"})
        if not row:
            return self._send(
                404,
                {"_error": f"order {order_id} not in local DB; need shop_id to fetch"},
            )
        shop_id = row[0]
        result = self._proxy_order(
            "GET",
            "/order/202309/orders",
            params={"shop_id": [shop_id]},
            order_id=order_id,
            override_path_qp={"ids": order_id},
        )
        return result

    # ----- Local DB reads -----

    def _sync_miaoshou_shops(self, params: dict):
        """从妙手 ERP 拉取店铺列表 → upsert 到 miaoshou_shops 表.

        query params:
            platform: 平台代号（默认 tiktok）
            site: 站点代号（默认 VN）
            page_no: 当前页码（默认 1）
            page_size: 每页数量（默认 100）
        """

        def _q(key, default=None):
            v = params.get(key)
            if isinstance(v, list):
                return v[0] if v else default
            return v if v is not None else default

        platform = _q("platform", "tiktok")
        site = _q("site", "VN")
        try:
            page_no = _safe_int(_q("page_no", "1"), default=1, source="qs.page_no")
            page_size = _safe_int(_q("page_size", "100"), default=100, source="qs.page_size")
        except (TypeError, ValueError):
            return self._send(400, {"_error": "page_no/page_size must be int"})

        try:
            from miaoshou import MiaoshouErpClient

            client = MiaoshouErpClient.from_env()
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": f"create MiaoshouErpClient failed: {e}"})

        try:
            result = client.shops.list(
                platform=platform, site=site, page_no=page_no, page_size=page_size
            )
        except Exception as e:  # noqa: BLE001
            return self._send(502, {"_error": f"miaoshou api error: {e}"})

        shops = result.data.shopList if result.data else []
        saved = 0
        for shop in shops:
            if persist_miaoshou_shop(platform, site, shop):
                saved += 1

        log_sync(f"{platform}:{site}", "miaoshou_shops", "ok", rows=saved)
        return self._send(
            200,
            {
                "platform": platform,
                "site": site,
                "saved": saved,
                "total_in_page": len(shops),
            },
        )

    def _sync_miaoshou_price_templates(self, params: dict):
        """从妙手 ERP 拉取定价模板列表 → upsert 到 miaoshou_price_templates 表.

        query params:
            platform: 平台代号（默认 tiktok）
            site: 站点（可选，过滤 SDK 调用）
            page_size: 每页数量（默认 20，SDK 上限 20）
        """
        def _q(key, default=None):
            v = params.get(key)
            if isinstance(v, list):
                return v[0] if v else default
            return v if v is not None else default

        platform = _q("platform", "tiktok")
        site = _q("site")
        try:
            page_size = _safe_int(_q("page_size", "20"), default=20, source="qs.page_size")
        except (TypeError, ValueError):
            return self._send(400, {"_error": "page_size must be int"})

        try:
            from miaoshou import MiaoshouErpClient

            client = MiaoshouErpClient.from_env()
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": f"create MiaoshouErpClient failed: {e}"})

        saved = 0
        total = 0
        page_no = 1
        max_pages = 50  # safety cap
        try:
            while page_no <= max_pages:
                sdk_kwargs: dict = {
                    "page_no": page_no,
                    "page_size": page_size,
                }
                if site:
                    sdk_kwargs["site"] = site
                result = client.tk_collect_box.get_price_template_list(**sdk_kwargs)
                templates = (
                    result.data.priceTemplateList
                    if result and result.data
                    else []
                )
                if page_no == 1 and result and result.data and result.data.total is not None:
                    total = _safe_int(result.data.total, default=0, source="sdk.total")
                if not templates:
                    break
                for t in templates:
                    if persist_miaoshou_price_template(platform, t):
                        saved += 1
                # 末页判定：仅在空页停止，或 total>0 && saved>=total。
                # （price_template SDK 上限 20，可能 total>page_size 仍需多页。
                #  total None/0 → 一直探测直到空页，与 move_collect test 一致。）
                if total > 0 and saved >= total:
                    break
                page_no += 1
        except StopIteration:
            # SDK 側返回列表耗尽，视为页面结束（测试用 side_effect=[] 场景）
            pass
        except Exception as e:  # noqa: BLE001
            return self._send(502, {"_error": f"miaoshou api error: {e}"})

        log_sync(platform, "miaoshou_price_templates", "ok", rows=saved)
        return self._send(
            200,
            {
                "platform": platform,
                "saved": saved,
                "total": total,
            },
        )

    def _sync_miaoshou_collect_box_details(self, params: dict):
        """从妙手 ERP 拉取公共采集箱详情列表 → upsert 到 miaoshou_collect_box_details 表.

        query params:
            platform: 平台代号（默认 tiktok）
            page_size: 每页数量（默认 50，SDK 上限 500）
            status: 可选过滤（normal / abnormal 等）
        """
        def _q(key, default=None):
            v = params.get(key)
            if isinstance(v, list):
                return v[0] if v else default
            return v if v is not None else default

        platform = _q("platform", "tiktok")
        status = _q("status")
        try:
            page_size = _safe_int(_q("page_size", "50"), default=50, source="qs.page_size")
        except (TypeError, ValueError):
            return self._send(400, {"_error": "page_size must be int"})

        try:
            from miaoshou import MiaoshouErpClient

            client = MiaoshouErpClient.from_env()
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": f"create MiaoshouErpClient failed: {e}"})

        try:
            sdk_kwargs: dict = {
                "page_no": 1,
                "page_size": page_size,
            }
            if status:
                sdk_kwargs["status"] = status
            result = client.tk_collect_box.search_collect_box_list(**sdk_kwargs)
        except Exception as e:  # noqa: BLE001
            return self._send(502, {"_error": f"miaoshou api error: {e}"})

        details = (
            result.data.detailList if result and result.data else []
        )
        saved = 0
        for d in details:
            if persist_miaoshou_collect_box_detail(platform, d):
                saved += 1

        log_sync(platform, "miaoshou_collect_box_details", "ok", rows=saved)
        return self._send(
            200,
            {
                "platform": platform,
                "saved": saved,
                "total_in_page": len(details),
            },
        )

    def _sync_miaoshou_move_collect_tasks(self, params: dict):
        """从妙手 ERP 拉取发布任务列表 → upsert 到 miaoshou_move_collect_tasks 表.

        query params:
            platform: 平台代号（默认 tiktok）
            page_size: 每页数量（默认 20，SDK 上限 20）
            status: 可选过滤
        """
        def _q(key, default=None):
            v = params.get(key)
            if isinstance(v, list):
                return v[0] if v else default
            return v if v is not None else default

        platform = _q("platform", "tiktok")
        status = _q("status")
        try:
            page_size = _safe_int(_q("page_size", "20"), default=20, source="qs.page_size")
        except (TypeError, ValueError):
            return self._send(400, {"_error": "page_size must be int"})

        try:
            from miaoshou import MiaoshouErpClient

            client = MiaoshouErpClient.from_env()
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": f"create MiaoshouErpClient failed: {e}"})

        saved = 0
        total = 0
        page_no = 1
        max_pages = 50  # safety cap
        try:
            while page_no <= max_pages:
                sdk_kwargs: dict = {
                    "page_no": page_no,
                    "page_size": page_size,
                }
                if status:
                    sdk_kwargs["status"] = status
                result = client.tk_collect_box.search_move_collect_list(**sdk_kwargs)
                tasks = (
                    result.data.moveCollectDetailList
                    if result and result.data
                    else []
                )
                if page_no == 1 and result and result.data and result.data.total is not None:
                    total = _safe_int(result.data.total, default=0, source="sdk.total")
                if not tasks:
                    break
                for t in tasks:
                    if persist_miaoshou_move_collect_task(platform, t):
                        saved += 1
                # 末页判定：仅在空页停止，或 total>0 && saved>=total。
                # （SDK 上限 20，可能 total>page_size 仍需多页；
                #  total None/0 → 一直探测直到空页。）
                if total > 0 and saved >= total:
                    break
                page_no += 1
        except StopIteration:
            # SDK 側返回列表耗尽，视为页面结束（测试用 side_effect=[] 场景）
            pass
        except Exception as e:  # noqa: BLE001
            return self._send(502, {"_error": f"miaoshou api error: {e}"})

        log_sync(platform, "miaoshou_move_collect_tasks", "ok", rows=saved)
        return self._send(
            200,
            {
                "platform": platform,
                "saved": saved,
                "total": total,
            },
        )

    def _db_list_miaoshou_shops(self, params: dict):
        """GET /db/miaoshou_shops?platform=&site=&limit="""

        def _q(key, default=None):
            v = params.get(key)
            if isinstance(v, list):
                return v[0] if v else default
            return v if v is not None else default

        platform = _q("platform")
        site = _q("site")
        try:
            limit = _safe_int(_q("limit", "100"), default=100, source="qs.limit")
        except (TypeError, ValueError):
            return self._send(400, {"_error": "limit must be int"})

        wh = []
        args: list = []
        if platform:
            wh.append("platform = %s")
            args.append(platform)
        if site:
            wh.append("site = %s")
            args.append(site)

        sql_query = "SELECT * FROM miaoshou_shops"
        if wh:
            sql_query += " WHERE " + " AND ".join(wh)
        sql_query += " ORDER BY synced_at DESC NULLS LAST LIMIT %s"
        args.append(limit)

        try:
            with (
                db_connect() as conn,
                conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
            ):
                cur.execute(sql.SQL(sql_query), args)  # type: ignore[reportArgumentType]
                rows = cur.fetchall()
            for r in rows:
                for k, v in list(r.items()):
                    if hasattr(v, "isoformat"):
                        r[k] = v.isoformat()
            return self._send(200, {"count": len(rows), "items": rows})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": str(e)})

    def _db_list_orders(self, params: dict):
        shop_id = (params.get("shop_id") or [None])[0]
        status = (params.get("status") or [None])[0]
        limit = _safe_int((params.get("limit") or ["50"])[0], default=50, source="qs.limit")
        try:
            with (
                db_connect() as conn,
                conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
            ):
                sql_str = "SELECT order_id, shop_id, order_status_name AS order_status, payment_amount, payment_currency, total_amount, create_time, update_time, synced_at FROM orders"
                args = []
                wh = []
                if shop_id:
                    wh.append("shop_id = %s")
                    args.append(shop_id)
                if status is not None:
                    # 202309 spec uses string status names (AWAITING_SHIPMENT etc.),
                    # not numeric codes. Query order_status_name (TEXT).
                    wh.append("order_status_name = %s")
                    args.append(status)
                if wh:
                    sql_str += " WHERE " + " AND ".join(wh)
                sql_str += " ORDER BY create_time DESC NULLS LAST LIMIT %s"
                args.append(limit)
                cur.execute(sql.SQL(sql_str), args)
                rows = cur.fetchall()
            return self._send(200, {"count": len(rows), "items": rows})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": str(e)})

    def _db_get_order(self, order_id: str):
        try:
            with (
                db_connect() as conn,
                conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
            ):
                cur.execute(
                    sql.SQL("SELECT * FROM orders WHERE order_id = %s"), (order_id,)
                )
                row = cur.fetchone()
            if not row:
                return self._send(404, {"_error": f"order {order_id} not in local DB"})
            # parse timestamps
            for k in ("synced_at", "updated_at"):
                if row.get(k) and hasattr(row[k], "isoformat"):
                    row[k] = row[k].isoformat()
            return self._send(200, row)
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": str(e)})

    def _db_get_order_items(self, order_id: str):
        try:
            with (
                db_connect() as conn,
                conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
            ):
                cur.execute(
                    sql.SQL("SELECT * FROM order_items WHERE order_id = %s"),
                    (order_id,),
                )
                rows = cur.fetchall()
            return self._send(200, {"count": len(rows), "items": rows})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": str(e)})

    def _db_get_order_shipping(self, order_id: str):
        try:
            with (
                db_connect() as conn,
                conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
            ):
                cur.execute(
                    sql.SQL("SELECT * FROM order_shippings WHERE order_id = %s"),
                    (order_id,),
                )
                row = cur.fetchone()
            if not row:
                return self._send(404, {"_error": f"no shipping for {order_id}"})
            for k in ("synced_at",):
                if row.get(k) and hasattr(row[k], "isoformat"):
                    row[k] = row[k].isoformat()
            return self._send(200, row)
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": str(e)})

    # ----- Finance proxy + sync (get-statements-202309) -----
    def _proxy_finance(self, method: str, upstream_path: str, params: dict):
        """Generic proxy for /finance/202309/{statements,payments}.

        Unlike /orders/search, finance endpoints are GET-only and don't
        accept body. Paging + sort go in query string.
        sort_field is REQUIRED (server returns 36009004 otherwise); we
        inject a safe default if caller didn't provide one.
        """
        shop_id = (params.get("shop_id") or [None])[0]
        if not shop_id:
            return self._send(
                400,
                {
                    "_error": "missing shop_id query param (e.g. ?shop_id=7494763368967603447)"
                },
            )

        creds = self._require_shop_token(shop_id)
        if isinstance(creds, dict) and creds.get("_error"):
            return self._send(502, creds)
        access_token, shop_cipher, _region = creds

        forwarded: dict[str, str] = {"shop_cipher": shop_cipher}
        for k, v in params.items():
            if k == "shop_id":
                continue
            forwarded[k] = v[0] if isinstance(v, list) and v else str(v)
        # Ensure sort_field + sort_order are set
        if "sort_field" not in forwarded:
            # Sensible default per endpoint
            forwarded["sort_field"] = (
                "statement_time" if "statements" in upstream_path else "create_time"
            )
        if "sort_order" not in forwarded:
            forwarded["sort_order"] = "DESC"

        result = tiktok_request(
            method=method,
            api_host=TIKTOK_API_HOST,
            path=upstream_path,
            access_token=access_token,
            app_key=TIKTOK_APP_KEY,
            app_secret=TIKTOK_APP_SECRET,
            body=None,
            extra_params=forwarded,
            timeout=TTS_ERP_HTTP_TIMEOUT,
        )
        return self._send(200, result)

    def _sync_statements(self, body: dict):
        shop_id = body.get("shop_id")
        if not shop_id:
            return self._send(400, {"_error": "missing shop_id in body"})
        page_size = _safe_int(body.get("page_size"), default=50, source="body.page_size")
        statement_time_ge = body.get("statement_time_ge")
        statement_time_lt = body.get("statement_time_lt")

        creds = self._require_shop_token(shop_id)
        if isinstance(creds, dict) and creds.get("_error"):
            return self._send(502, creds)
        access_token, shop_cipher, _region = creds

        extra_params: dict[str, str] = {
            "shop_cipher": shop_cipher,
            "page_size": str(min(page_size, 100)),
            "sort_field": "statement_time",
            "sort_order": "DESC",
        }
        if statement_time_ge is not None:
            extra_params["statement_time_ge"] = str(_safe_int(statement_time_ge, source="body.statement_time_ge"))
        if statement_time_lt is not None:
            extra_params["statement_time_lt"] = str(_safe_int(statement_time_lt, source="body.statement_time_lt"))

        first = tiktok_request(
            "GET",
            TIKTOK_API_HOST,
            "/finance/202309/statements",
            access_token,
            TIKTOK_APP_KEY,
            TIKTOK_APP_SECRET,
            body=None,
            extra_params=extra_params,
            timeout=TTS_ERP_HTTP_TIMEOUT,
        )
        if first.get("code") != 0:
            log_sync(shop_id, "statements", "error", error=str(first.get("message")))
            return self._send(502, first)

        data = first.get("data") or {}
        stmts = data.get("statements") or []
        saved = 0
        for s in stmts:
            if persist_statement(shop_id, s):
                saved += 1

        total = data.get("total") or len(stmts)
        next_token = data.get("next_page_token")
        pages = 1
        while next_token and pages < 50:
            extra_params["page_token"] = next_token
            nxt = tiktok_request(
                "GET",
                TIKTOK_API_HOST,
                "/finance/202309/statements",
                access_token,
                TIKTOK_APP_KEY,
                TIKTOK_APP_SECRET,
                body=None,
                extra_params=extra_params,
                timeout=TTS_ERP_HTTP_TIMEOUT,
            )
            if nxt.get("code") != 0:
                break
            d = nxt.get("data") or {}
            for s in d.get("statements") or []:
                if persist_statement(shop_id, s):
                    saved += 1
            next_token = d.get("next_page_token")
            pages += 1

        log_sync(shop_id, "statements", "ok", rows=saved)
        return self._send(
            200, {"shop_id": shop_id, "saved": saved, "total": total, "pages": pages}
        )

    def _sync_payments(self, body: dict):
        shop_id = body.get("shop_id")
        if not shop_id:
            return self._send(400, {"_error": "missing shop_id in body"})
        page_size = _safe_int(body.get("page_size"), default=50, source="body.page_size")
        create_time_ge = body.get("create_time_ge")
        create_time_lt = body.get("create_time_lt")

        creds = self._require_shop_token(shop_id)
        if isinstance(creds, dict) and creds.get("_error"):
            return self._send(502, creds)
        access_token, shop_cipher, _region = creds

        extra_params: dict[str, str] = {
            "shop_cipher": shop_cipher,
            "page_size": str(min(page_size, 100)),
            "sort_field": "create_time",
            "sort_order": "DESC",
        }
        if create_time_ge is not None:
            extra_params["create_time_ge"] = str(_safe_int(create_time_ge, source="body.create_time_ge"))
        if create_time_lt is not None:
            extra_params["create_time_lt"] = str(_safe_int(create_time_lt, source="body.create_time_lt"))

        first = tiktok_request(
            "GET",
            TIKTOK_API_HOST,
            "/finance/202309/payments",
            access_token,
            TIKTOK_APP_KEY,
            TIKTOK_APP_SECRET,
            body=None,
            extra_params=extra_params,
            timeout=TTS_ERP_HTTP_TIMEOUT,
        )
        if first.get("code") != 0:
            log_sync(shop_id, "payments", "error", error=str(first.get("message")))
            return self._send(502, first)

        data = first.get("data") or {}
        pays = data.get("payments") or []
        saved = 0
        for p in pays:
            if persist_payment(shop_id, p):
                saved += 1

        total = data.get("total") or len(pays)
        next_token = data.get("next_page_token")
        pages = 1
        while next_token and pages < 50:
            extra_params["page_token"] = next_token
            nxt = tiktok_request(
                "GET",
                TIKTOK_API_HOST,
                "/finance/202309/payments",
                access_token,
                TIKTOK_APP_KEY,
                TIKTOK_APP_SECRET,
                body=None,
                extra_params=extra_params,
                timeout=TTS_ERP_HTTP_TIMEOUT,
            )
            if nxt.get("code") != 0:
                break
            d = nxt.get("data") or {}
            for p in d.get("payments") or []:
                if persist_payment(shop_id, p):
                    saved += 1
            next_token = d.get("next_page_token")
            pages += 1

        log_sync(shop_id, "payments", "ok", rows=saved)
        return self._send(
            200, {"shop_id": shop_id, "saved": saved, "total": total, "pages": pages}
        )

    # ----- Local DB reads for finance -----
    def _db_list_statements(self, params: dict):
        shop_id = (params.get("shop_id") or [None])[0]
        limit = _safe_int((params.get("limit") or ["50"])[0], default=50, source="qs.limit")
        try:
            with (
                db_connect() as conn,
                conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
            ):
                sql_query = "SELECT statement_id, shop_id, payment_id, currency, payment_status, statement_time, payment_time, revenue_amount, fee_amount, net_sales_amount, shipping_cost_amount, adjustment_amount, settlement_amount, synced_at FROM statements"
                args = []
                if shop_id:
                    sql_query += " WHERE shop_id = %s"
                    args.append(shop_id)
                sql_query += " ORDER BY statement_time DESC NULLS LAST LIMIT %s"
                args.append(limit)
                cur.execute(sql.SQL(sql_query), args)  # type: ignore[reportArgumentType]  # pyright strict: LiteralString is overly strict for psycopg3 SQL()
                rows = cur.fetchall()
            for r in rows:
                if r.get("synced_at") and hasattr(r["synced_at"], "isoformat"):
                    r["synced_at"] = r["synced_at"].isoformat()
            return self._send(200, {"count": len(rows), "items": rows})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": str(e)})

    def _db_list_payments(self, params: dict):
        shop_id = (params.get("shop_id") or [None])[0]
        status = (params.get("status") or [None])[0]
        limit = _safe_int((params.get("limit") or ["50"])[0], default=50, source="qs.limit")
        try:
            with (
                db_connect() as conn,
                conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
            ):
                sql_query = "SELECT payment_id, shop_id, status, currency, amount_value, settlement_amount_value, payment_amount_before_value, reserve_amount_value, exchange_rate, bank_account, create_time, paid_time, synced_at FROM payments"
                args = []
                wh = []
                if shop_id:
                    wh.append("shop_id = %s")
                    args.append(shop_id)
                if status:
                    wh.append("status = %s")
                    args.append(status)
                if wh:
                    sql_query += " WHERE " + " AND ".join(wh)
                sql_query += " ORDER BY paid_time DESC NULLS LAST LIMIT %s"
                args.append(limit)
                cur.execute(sql.SQL(sql_query), args)  # type: ignore[reportArgumentType]  # pyright strict: LiteralString is overly strict for psycopg3 SQL()
                rows = cur.fetchall()
            for r in rows:
                if r.get("synced_at") and hasattr(r["synced_at"], "isoformat"):
                    r["synced_at"] = r["synced_at"].isoformat()
            return self._send(200, {"count": len(rows), "items": rows})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": str(e)})

    # ----- Return / Refund / Cancellation proxy + sync (return-refund-202309) -----
    #
    # Confirmed endpoints (probe_refund_v3/v5 2026-08-16):
    #   POST /return_refund/202309/returns/search      (returns 0 for our shop)
    #   POST /return_refund/202309/cancellations/search (returns 5 for our shop)
    #
    # Detail-by-id (/returns/<id>, /cancellations/<id>) → 36009009 Invalid path.
    # /reverse/202309/* → HTTP 404 at CDN (module not available in 202309 spec).
    # Write endpoints (POST /return_refund/202309/returns and /cancellations,
    # which create new requests) are NOT integrated per user instruction
    # (no high-risk write testing). See handoff.md §return_refund.
    def _proxy_refund(self, kind: str, body: dict):
        """Generic proxy for /returns/search and /cancellations/search.

        `kind` is "returns" or "cancellations" — must match a path the
        tts-erp API exposes (the request body has the shop_id).
        """
        shop_id = body.get("shop_id")
        if not shop_id:
            return self._send(
                400, {"_error": 'missing shop_id in body (e.g. {"shop_id": "..."})'}
            )

        creds = self._require_shop_token(shop_id)
        if isinstance(creds, dict) and creds.get("_error"):
            return self._send(502, creds)
        access_token, shop_cipher, _region = creds

        # TikTok /return_refund/202309/* requires shop_cipher in query.
        # Body carries only the actual filter — page_size/sort_field/sort_order
        # in the query string (same convention as /orders/search).
        # 2026-08-16: page_size cap = 50 for return_refund endpoints
        # (TikTok returns 98001004 "Value Out Of Range" if >50).
        extra_params: dict[str, str] = {
            "shop_cipher": shop_cipher,
            "page_size": str(min(max(_safe_int(body.get("page_size"), default=50, source="body.page_size"), 10), 50)),
            "sort_field": "create_time",
            "sort_order": "DESC",
        }

        # Strip shop_id + paging/sort keys from the upstream body — they belong
        # in extra_params, not in the body.
        upstream_body: dict = {}
        for k, v in body.items():
            if k in ("shop_id", "page_size", "page_token", "sort_field", "sort_order"):
                continue
            if v is not None:
                upstream_body[k] = v

        # Allow caller to pass create_time_ge/lt etc as query string instead.
        # If they appear in body, mirror them as query (TikTok wants query
        # for time filters in some versions).
        for k in ("create_time_ge", "create_time_lt"):
            if k in upstream_body and k not in extra_params:
                extra_params[k] = str(_safe_int(upstream_body.pop(k), source="body." + k))

        upstream_path = f"/return_refund/202309/{kind}/search"
        result = tiktok_request(
            method="POST",
            api_host=TIKTOK_API_HOST,
            path=upstream_path,
            access_token=access_token,
            app_key=TIKTOK_APP_KEY,
            app_secret=TIKTOK_APP_SECRET,
            body=upstream_body if upstream_body else None,
            extra_params=extra_params,
            timeout=TTS_ERP_HTTP_TIMEOUT,
        )
        return self._send(200, result)

    def _sync_returns(self, body: dict):
        shop_id = body.get("shop_id")
        if not shop_id:
            return self._send(400, {"_error": "missing shop_id in body"})
        page_size = _safe_int(body.get("page_size"), default=50, source="body.page_size")

        creds = self._require_shop_token(shop_id)
        if isinstance(creds, dict) and creds.get("_error"):
            return self._send(502, creds)
        access_token, shop_cipher, _region = creds

        # 2026-08-16: return_refund page_size cap = 50 (TikTok 98001004 if >50).
        extra_params: dict[str, str] = {
            "shop_cipher": shop_cipher,
            "page_size": str(min(max(page_size, 10), 50)),
            "sort_field": "create_time",
            "sort_order": "DESC",
        }
        if body.get("create_time_ge") is not None:
            extra_params["create_time_ge"] = str(_safe_int(body["create_time_ge"], source="body.create_time_ge"))
        if body.get("create_time_lt") is not None:
            extra_params["create_time_lt"] = str(_safe_int(body["create_time_lt"], source="body.create_time_lt"))

        first = tiktok_request(
            "POST",
            TIKTOK_API_HOST,
            "/return_refund/202309/returns/search",
            access_token,
            TIKTOK_APP_KEY,
            TIKTOK_APP_SECRET,
            body=None,
            extra_params=extra_params,
            timeout=TTS_ERP_HTTP_TIMEOUT,
        )
        if first.get("code") != 0:
            log_sync(shop_id, "returns", "error", error=str(first.get("message")))
            return self._send(502, first)

        data = first.get("data") or {}
        items = (
            data.get("return_orders") or data.get("returns") or data.get("list") or []
        )
        saved = 0
        for r in items:
            if persist_return(shop_id, r):
                saved += 1

        total = data.get("total_count") or data.get("total") or len(items)
        next_token = data.get("next_page_token")
        pages = 1
        while next_token and pages < 50:
            extra_params["page_token"] = next_token
            nxt = tiktok_request(
                "POST",
                TIKTOK_API_HOST,
                "/return_refund/202309/returns/search",
                access_token,
                TIKTOK_APP_KEY,
                TIKTOK_APP_SECRET,
                body=None,
                extra_params=extra_params,
                timeout=TTS_ERP_HTTP_TIMEOUT,
            )
            if nxt.get("code") != 0:
                break
            d = nxt.get("data") or {}
            for r in d.get("return_orders") or d.get("returns") or d.get("list") or []:
                if persist_return(shop_id, r):
                    saved += 1
            next_token = d.get("next_page_token")
            pages += 1

        log_sync(shop_id, "returns", "ok", rows=saved)
        return self._send(
            200, {"shop_id": shop_id, "saved": saved, "total": total, "pages": pages}
        )

    def _sync_cancellations(self, body: dict):
        shop_id = body.get("shop_id")
        if not shop_id:
            return self._send(400, {"_error": "missing shop_id in body"})
        page_size = _safe_int(body.get("page_size"), default=50, source="body.page_size")

        creds = self._require_shop_token(shop_id)
        if isinstance(creds, dict) and creds.get("_error"):
            return self._send(502, creds)
        access_token, shop_cipher, _region = creds

        # 2026-08-16: return_refund page_size cap = 50 (TikTok 98001004 if >50).
        extra_params: dict[str, str] = {
            "shop_cipher": shop_cipher,
            "page_size": str(min(max(page_size, 10), 50)),
            "sort_field": "create_time",
            "sort_order": "DESC",
        }
        if body.get("create_time_ge") is not None:
            extra_params["create_time_ge"] = str(_safe_int(body["create_time_ge"], source="body.create_time_ge"))
        if body.get("create_time_lt") is not None:
            extra_params["create_time_lt"] = str(_safe_int(body["create_time_lt"], source="body.create_time_lt"))

        first = tiktok_request(
            "POST",
            TIKTOK_API_HOST,
            "/return_refund/202309/cancellations/search",
            access_token,
            TIKTOK_APP_KEY,
            TIKTOK_APP_SECRET,
            body=None,
            extra_params=extra_params,
            timeout=TTS_ERP_HTTP_TIMEOUT,
        )
        if first.get("code") != 0:
            log_sync(shop_id, "cancellations", "error", error=str(first.get("message")))
            return self._send(502, first)

        data = first.get("data") or {}
        items = data.get("cancellations") or data.get("list") or []
        saved = 0
        for c in items:
            if persist_cancellation(shop_id, c):
                saved += 1

        total = data.get("total_count") or data.get("total") or len(items)
        next_token = data.get("next_page_token")
        pages = 1
        while next_token and pages < 50:
            extra_params["page_token"] = next_token
            nxt = tiktok_request(
                "POST",
                TIKTOK_API_HOST,
                "/return_refund/202309/cancellations/search",
                access_token,
                TIKTOK_APP_KEY,
                TIKTOK_APP_SECRET,
                body=None,
                extra_params=extra_params,
                timeout=TTS_ERP_HTTP_TIMEOUT,
            )
            if nxt.get("code") != 0:
                break
            d = nxt.get("data") or {}
            for c in d.get("cancellations") or d.get("list") or []:
                if persist_cancellation(shop_id, c):
                    saved += 1
            next_token = d.get("next_page_token")
            pages += 1

        log_sync(shop_id, "cancellations", "ok", rows=saved)
        return self._send(
            200, {"shop_id": shop_id, "saved": saved, "total": total, "pages": pages}
        )

    # ----- Local DB reads for returns / cancellations -----
    def _db_list_returns(self, params: dict):
        shop_id = (params.get("shop_id") or [None])[0]
        status = (params.get("status") or [None])[0]
        limit = _safe_int((params.get("limit") or ["50"])[0], default=50, source="qs.limit")
        try:
            with (
                db_connect() as conn,
                conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
            ):
                sql_query = "SELECT return_id, shop_id, order_id, return_status, return_reason, return_type, role, create_time, update_time, synced_at FROM returns"
                args = []
                wh = []
                if shop_id:
                    wh.append("shop_id = %s")
                    args.append(shop_id)
                if status:
                    wh.append("return_status = %s")
                    args.append(status)
                if wh:
                    sql_query += " WHERE " + " AND ".join(wh)
                sql_query += " ORDER BY create_time DESC NULLS LAST LIMIT %s"
                args.append(limit)
                cur.execute(sql.SQL(sql_query), args)  # type: ignore[reportArgumentType]  # pyright strict: LiteralString is overly strict for psycopg3 SQL()
                rows = cur.fetchall()
            for r in rows:
                if r.get("synced_at") and hasattr(r["synced_at"], "isoformat"):
                    r["synced_at"] = r["synced_at"].isoformat()
            return self._send(200, {"count": len(rows), "items": rows})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": str(e)})

    def _db_list_cancellations(self, params: dict):
        shop_id = (params.get("shop_id") or [None])[0]
        status = (params.get("status") or [None])[0]
        limit = _safe_int((params.get("limit") or ["50"])[0], default=50, source="qs.limit")
        try:
            with (
                db_connect() as conn,
                conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
            ):
                sql_query = "SELECT cancel_id, shop_id, order_id, cancel_status, cancel_reason, cancel_reason_text, cancel_type, role, should_replenish_stock, create_time, update_time, synced_at FROM cancellations"
                args = []
                wh = []
                if shop_id:
                    wh.append("shop_id = %s")
                    args.append(shop_id)
                if status:
                    wh.append("cancel_status = %s")
                    args.append(status)
                if wh:
                    sql_query += " WHERE " + " AND ".join(wh)
                sql_query += " ORDER BY create_time DESC NULLS LAST LIMIT %s"
                args.append(limit)
                cur.execute(sql.SQL(sql_query), args)  # type: ignore[reportArgumentType]  # pyright strict: LiteralString is overly strict for psycopg3 SQL()
                rows = cur.fetchall()
            for r in rows:
                if r.get("synced_at") and hasattr(r["synced_at"], "isoformat"):
                    r["synced_at"] = r["synced_at"].isoformat()
            return self._send(200, {"count": len(rows), "items": rows})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": str(e)})

    # ----- Logistics tracking proxy + sync (fulfillment/202309) -----
    #
    # Confirmed 2026-08-16 endpoint (probe_logistics_v6.py):
    #   GET /fulfillment/202309/orders/{order_id}/tracking
    #     → {code, message, data: {tracking: [{action_code, description, update_time_millis}, ...]}}
    #   All 6 order statuses return code=0 with a non-empty tracking list (CANCELLED
    #   shows the full outbound + return journey, which is exactly what we want
    #   for the 18 cancelled-but-shipped orders).
    #
    # /logistics/202604/... returns 11007009 on this app (path correct but module
    # not yet open for our app/scope). When TikTok rolls 202604 to us, just
    # swap the upstream_path below to "/logistics/202604/orders/{order_id}/tracking".
    LOGISTICS_UPSTREAM_PATH = "/fulfillment/202309/orders/{order_id}/tracking"

    def _proxy_logistics_tracking(self, order_id: str, params: dict):
        """GET /logistics/orders/{order_id}/tracking?shop_id=...  → upstream + persist."""
        shop_id = (params.get("shop_id") or [None])[0]
        if not shop_id:
            return self._send(400, {"_error": "missing shop_id query param"})
        creds = self._require_shop_token(shop_id)
        if isinstance(creds, dict) and creds.get("_error"):
            return self._send(502, creds)
        access_token, shop_cipher, _region = creds

        upstream_path = self.LOGISTICS_UPSTREAM_PATH.format(order_id=order_id)
        result = tiktok_request(
            method="GET",
            api_host=TIKTOK_API_HOST,
            path=upstream_path,
            access_token=access_token,
            app_key=TIKTOK_APP_KEY,
            app_secret=TIKTOK_APP_SECRET,
            body=None,
            extra_params={"shop_cipher": shop_cipher},
            timeout=TTS_ERP_HTTP_TIMEOUT,
        )
        # Persist if upstream returned 200
        if isinstance(result, dict) and result.get("code") == 0:
            persist_logistics_tracking(shop_id, order_id, result)
            # Backfill tracking_number from order_shippings (denormalized for filtering)
            try:
                with db_connect() as conn, conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            "SELECT tracking_number FROM order_shippings WHERE order_id = %s"
                        ),
                        (order_id,),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        persist_logistics_tracking_number(order_id, row[0])
            except Exception as e:  # noqa: BLE001
                log.error(
                    f"[tts-erp] backfill tracking_number for {order_id} failed: {e}\n"
                )
        return self._send(200, result)

    def _sync_logistics_tracking(self, body: dict):
        """POST /sync/logistics_tracking
        body:
          {shop_id, order_ids?: [...], all_with_tracking?: bool,
           limit?: int, max_per_run?: int}
        If order_ids is given, sync exactly those.
        Else, if all_with_tracking=true, pull all orders with non-null
        tracking_number from order_shippings (paginated via limit).
        Else, sync orders flagged needs_resync in logistics_sync_targets.
        """
        shop_id = body.get("shop_id")
        if not shop_id:
            return self._send(400, {"_error": "missing shop_id in body"})

        creds = self._require_shop_token(shop_id)
        if isinstance(creds, dict) and creds.get("_error"):
            return self._send(502, creds)
        access_token, shop_cipher, _region = creds

        # Resolve target order_ids
        order_ids: list[str] = []
        if body.get("order_ids"):
            order_ids = [str(x) for x in body["order_ids"] if x]
        elif body.get("all_with_tracking"):
            lim = _safe_int(body.get("limit"), default=1000, source="body.limit")
            with db_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    sql.SQL("""
                    SELECT DISTINCT s.order_id
                    FROM order_shippings s
                    WHERE s.shop_id = %s
                      AND s.tracking_number IS NOT NULL
                      AND s.tracking_number <> ''
                    LIMIT %s
                    """),
                    (shop_id, lim),
                )
                order_ids = [r[0] for r in cur.fetchall()]
        else:
            lim = _safe_int(body.get("limit"), default=200, source="body.limit")
            with db_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    sql.SQL("""
                    SELECT order_id FROM logistics_sync_targets
                    WHERE shop_id = %s AND needs_resync = true
                    ORDER BY order_id
                    LIMIT %s
                    """),
                    (shop_id, lim),
                )
                order_ids = [r[0] for r in cur.fetchall()]

        max_per_run = _safe_int(body.get("max_per_run"), default=(len(order_ids) or 100), source="body.max_per_run")
        order_ids = order_ids[:max_per_run]

        if not order_ids:
            return self._send(
                200, {"shop_id": shop_id, "saved": 0, "total": 0, "order_ids": []}
            )

        saved = 0
        errors: list[dict] = []
        for oid in order_ids:
            upstream_path = self.LOGISTICS_UPSTREAM_PATH.format(order_id=oid)
            r = tiktok_request(
                method="GET",
                api_host=TIKTOK_API_HOST,
                path=upstream_path,
                access_token=access_token,
                app_key=TIKTOK_APP_KEY,
                app_secret=TIKTOK_APP_SECRET,
                body=None,
                extra_params={"shop_cipher": shop_cipher},
                timeout=TTS_ERP_HTTP_TIMEOUT,
            )
            if isinstance(r, dict) and r.get("code") == 0:
                if persist_logistics_tracking(shop_id, oid, r):
                    saved += 1
                try:
                    with db_connect() as conn, conn.cursor() as cur:
                        cur.execute(
                            sql.SQL(
                                "SELECT tracking_number FROM order_shippings WHERE order_id = %s"
                            ),
                            (oid,),
                        )
                        row = cur.fetchone()
                        if row and row[0]:
                            persist_logistics_tracking_number(oid, row[0])
                except Exception:  # noqa: BLE001
                    pass
            else:
                errors.append(
                    {
                        "order_id": oid,
                        "code": r.get("code"),
                        "message": r.get("message"),
                    }
                )

        log_sync(
            shop_id,
            "logistics_tracking",
            "ok",
            rows=saved,
            error=(None if not errors else f"{len(errors)} order(s) failed"),
        )
        return self._send(
            200,
            {
                "shop_id": shop_id,
                "saved": saved,
                "total": len(order_ids),
                "errors": errors,
            },
        )

    def _db_list_logistics_tracking(self, params: dict):
        """GET /db/logistics_tracking?shop_id=...&final_status=...&arrived_overseas=true"""
        shop_id = (params.get("shop_id") or [None])[0]
        final_status = (params.get("final_status") or [None])[0]
        arrived = (params.get("arrived_overseas") or [None])[0]
        tracking_number = (params.get("tracking_number") or [None])[0]
        order_id = (params.get("order_id") or [None])[0]
        limit = _safe_int((params.get("limit") or ["100"])[0], default=100, source="qs.limit")

        wh = []
        args: list = []
        if shop_id:
            wh.append("shop_id = %s")
            args.append(shop_id)
        if final_status:
            wh.append("final_status = %s")
            args.append(final_status)
        if arrived is not None and arrived != "":
            wh.append("arrived_overseas = %s")
            args.append(str(arrived).lower() == "true")
        if tracking_number:
            wh.append("tracking_number = %s")
            args.append(tracking_number)
        if order_id:
            wh.append("order_id = %s")
            args.append(order_id)

        sql_query = "SELECT * FROM logistics_tracking"
        if wh:
            sql_query += " WHERE " + " AND ".join(wh)
        sql_query += " ORDER BY last_event_at DESC NULLS LAST LIMIT %s"
        args.append(limit)

        try:
            with (
                db_connect() as conn,
                conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
            ):
                cur.execute(sql.SQL(sql_query), args)  # type: ignore[reportArgumentType]  # pyright strict: LiteralString is overly strict for psycopg3 SQL()
                rows = cur.fetchall()
            for r in rows:
                for k, v in list(r.items()):
                    if hasattr(v, "isoformat"):
                        r[k] = v.isoformat()
            return self._send(200, {"count": len(rows), "items": rows})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": str(e)})

    def _db_list_logistics_events(self, params: dict):
        """GET /db/logistics_events?order_id=...&action_code=...&limit=200"""
        order_id = (params.get("order_id") or [None])[0]
        action_code = (params.get("action_code") or [None])[0]
        limit = _safe_int((params.get("limit") or ["200"])[0], default=200, source="qs.limit")
        wh = []
        args: list = []
        if order_id:
            wh.append("order_id = %s")
            args.append(order_id)
        if action_code:
            try:
                wh.append("action_code = %s")
                args.append(_safe_int(action_code, default=0, source="db.action_code"))
            except ValueError:
                return self._send(400, {"_error": "action_code must be int"})
        sql_query = "SELECT order_id, action_code, event_time, location, description FROM logistics_tracking_events"
        if wh:
            sql_query += " WHERE " + " AND ".join(wh)
        sql_query += " ORDER BY event_time DESC LIMIT %s"
        args.append(limit)
        try:
            with (
                db_connect() as conn,
                conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
            ):
                cur.execute(sql.SQL(sql_query), args)  # type: ignore[reportArgumentType]  # pyright strict: LiteralString is overly strict for psycopg3 SQL()
                rows = cur.fetchall()
            for r in rows:
                if r.get("event_time"):
                    from datetime import datetime, timezone

                    r["event_time_iso"] = datetime.fromtimestamp(
                        int(r["event_time"]) / 1000, tz=timezone.utc
                    ).isoformat()
            return self._send(200, {"count": len(rows), "items": rows})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": str(e)})

    def log_message(self, format, *args):
        log.error(f"  {self.address_string()} - {format % args}\n")


def _proxy_get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


# ---------- main ----------
def main() -> int:
    if not TIKTOK_APP_KEY or not TIKTOK_APP_SECRET:
        log.error("  WARN: TIKTOK_APP_KEY / TIKTOK_APP_SECRET not set\n")
    if not TTS_ERP_DB_URL:
        log.error(
            "[tts-erp] WARN: TTS_ERP_DB_URL not set — DB-backed endpoints will fail\n"
        )
    db_ok = db_init()
    # Setup rotating logger (idempotent)
    setup_logging("tts-erp", "logs", level=logging.INFO, backup_days=7)

    print(f"[tts-erp] listening on http://{HOST}:{PORT}")
    print(f"[tts-erp] oauth receiver: {OAUTH_RECEIVER_URL}")
    print(f"[tts-erp] tiktok api:     {TIKTOK_API_HOST}")
    print(f"[tts-erp] db store:       {'ENABLED' if db_ok else 'disabled'}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[tts-erp] shutting down...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
