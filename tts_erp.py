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

import json
import os
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
    tiktok_request,
    build_signed_url,
    sign_request,
    resolve_path,
    ORDER_ENDPOINTS,
)

# ---------- Config ----------
HOST = os.environ.get("TTS_ERP_HOST", "0.0.0.0")
PORT = int(os.environ.get("TTS_ERP_PORT", "9877"))
OAUTH_RECEIVER_URL = os.environ.get("OAUTH_RECEIVER_URL", "http://127.0.0.1:9876").rstrip("/")
TIKTOK_APP_KEY = os.environ.get("TIKTOK_APP_KEY", "")
TIKTOK_APP_SECRET = os.environ.get("TIKTOK_APP_SECRET", "")
TIKTOK_API_HOST = os.environ.get("TIKTOK_API_HOST", "https://open-api.tiktokglobalshop.com")
TTS_ERP_DB_URL = os.environ.get("TTS_ERP_DB_URL", "")
TTS_ERP_HTTP_TIMEOUT = float(os.environ.get("TTS_ERP_HTTP_TIMEOUT", "30"))

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
        sys.stderr.write("[tts-erp] TTS_ERP_DB_URL not set — DB store disabled\n")
        return False
    try:
        with db_connect() as conn, conn.cursor() as cur:
            for tbl in ("shops", "orders", "order_items", "order_shippings",
                        "sync_log", "statements", "payments",
                        "returns", "cancellations"):
                cur.execute("SELECT to_regclass(%s)", (tbl,))
                if cur.fetchone()[0] is None:
                    sys.stderr.write(f"[tts-erp] WARN: table '{tbl}' missing — run schema.sql\n")
                    return False
        return True
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[tts-erp] DB init failed: {e}\n")
        return False


# ---------- OAuth passthrough ----------
def fetch_shop_meta(shop_id: str) -> dict | None:
    """Get shop metadata (cipher, name, region) from oauth-receiver. No secrets."""
    try:
        with urllib.request.urlopen(f"{OAUTH_RECEIVER_URL}/tokens/shops", timeout=5) as r:
            data = json.load(r)
        for s in data.get("items", []):
            if s.get("shop_id") == shop_id:
                return s
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[tts-erp] failed to list shops: {e}\n")
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
        return {"_error": e.code, "_body": e.read().decode("utf-8", errors="replace")[:300]}
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
            # Normalize status: TikTok returns string ("AWAITING_SHIPMENT") but
            # some endpoints historically returned int. Keep both as text.
            raw_status = order_raw.get("status") or order_raw.get("order_status")
            order_status: str | None = str(raw_status) if raw_status is not None else None

            # Payment is a nested object in 202309 spec
            payment = order_raw.get("payment") or {}
            payment_amount = (payment.get("total_amount") if isinstance(payment, dict) else None) \
                or order_raw.get("payment_amount")
            payment_currency = (payment.get("currency") if isinstance(payment, dict) else None) \
                or order_raw.get("payment_currency")

            # Orders table — extract known fields, store raw for full fidelity
            cur.execute("""
                INSERT INTO orders
                    (order_id, shop_id, order_status, order_status_name, payment_amount, payment_currency,
                     total_amount, buyer_email, buyer_message, create_time, update_time,
                     paid_time, shipped_time, delivered_time, cancelled_time,
                     fulfillment_type, raw)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (order_id) DO UPDATE SET
                    shop_id            = EXCLUDED.shop_id,
                    order_status       = EXCLUDED.order_status,
                    order_status_name  = EXCLUDED.order_status_name,
                    payment_amount     = EXCLUDED.payment_amount,
                    payment_currency   = EXCLUDED.payment_currency,
                    total_amount       = EXCLUDED.total_amount,
                    buyer_email        = EXCLUDED.buyer_email,
                    buyer_message      = EXCLUDED.buyer_message,
                    create_time        = EXCLUDED.create_time,
                    update_time        = EXCLUDED.update_time,
                    paid_time          = EXCLUDED.paid_time,
                    shipped_time       = EXCLUDED.shipped_time,
                    delivered_time     = EXCLUDED.delivered_time,
                    cancelled_time     = EXCLUDED.cancelled_time,
                    fulfillment_type   = EXCLUDED.fulfillment_type,
                    raw                = EXCLUDED.raw,
                    synced_at          = now()
            """, (
                oid, shop_id,
                # order_status (INT, legacy) — unused for 202309 spec, kept NULL
                # order_status_name (TEXT) — current 202309 string ("AWAITING_SHIPMENT")
                None,
                order_status,
                _decimal(payment_amount),
                _str(payment_currency),
                _decimal(order_raw.get("total_amount")),
                _str(order_raw.get("buyer_email")),
                _str(order_raw.get("buyer_message") or order_raw.get("buyer_note")),
                _int(order_raw.get("create_time")),
                _int(order_raw.get("update_time")),
                _int(order_raw.get("paid_time") or (payment or {}).get("paid_time")),
                _int(order_raw.get("shipped_time") or (order_raw.get("fulfillment", {}) or {}).get("shipped_time")),
                _int(order_raw.get("delivered_time") or (order_raw.get("fulfillment", {}) or {}).get("delivered_time")),
                _int(order_raw.get("cancelled_time")),
                _str(order_raw.get("fulfillment_type") or (order_raw.get("fulfillment", {}) or {}).get("type")),
                json.dumps(order_raw, ensure_ascii=False, default=str),
            ))

            # Items
            for it in order_raw.get("line_items") or order_raw.get("items") or []:
                cur.execute("""
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
                """, (
                    oid,
                    _str(it.get("id") or it.get("item_id")) or "",
                    shop_id,
                    _str(it.get("sku_id")),
                    _str(it.get("product_id")),
                    _str(it.get("product_name")),
                    _str(it.get("sku_name")),
                    _str(it.get("sku_image")),
                    _int(it.get("quantity")) or 1,
                    _decimal(it.get("sale_price") or it.get("sku_price") or it.get("price")),
                    json.dumps(it, ensure_ascii=False, default=str),
                ))

            # Shipping — TikTok 202309 has top-level shipping_provider_id/name
            # but legacy code may put it under shipping/fulfillment. Read both.
            tracking = order_raw.get("tracking_number") \
                or (order_raw.get("shipping") or {}).get("tracking_number") \
                or (order_raw.get("fulfillment") or {}).get("tracking_number")
            provider_id = order_raw.get("shipping_provider_id") \
                or (order_raw.get("shipping") or {}).get("shipping_provider_id") \
                or (order_raw.get("fulfillment") or {}).get("shipping_provider_id")
            provider_name = order_raw.get("shipping_provider_name") \
                or (order_raw.get("shipping") or {}).get("shipping_provider_name") \
                or (order_raw.get("fulfillment") or {}).get("shipping_provider_name")
            if any([tracking, provider_id, provider_name]):
                cur.execute("""
                    INSERT INTO order_shippings
                        (order_id, shop_id, tracking_number, shipping_provider_id,
                         shipping_provider_name, raw)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (order_id) DO UPDATE SET
                        tracking_number        = EXCLUDED.tracking_number,
                        shipping_provider_id   = EXCLUDED.shipping_provider_id,
                        shipping_provider_name = EXCLUDED.shipping_provider_name,
                        raw                    = EXCLUDED.raw,
                        synced_at              = now()
                """, (
                    oid, shop_id, tracking, provider_id, provider_name,
                    json.dumps(order_raw, ensure_ascii=False, default=str),
                ))

            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[tts-erp] persist_order({oid}) failed: {e}\n")
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
            cur.execute("""
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
            """, (
                sid, shop_id,
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
            ))
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[tts-erp] persist_statement({sid}) failed: {e}\n")
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
            cur.execute("""
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
            """, (
                pid, shop_id,
                _str(pay_raw.get("status")),
                _str(amount.get("currency") if isinstance(amount, dict) else None),
                _decimal(amount.get("value") if isinstance(amount, dict) else None),
                _decimal(settlement.get("value") if isinstance(settlement, dict) else None),
                _decimal(before.get("value") if isinstance(before, dict) else None),
                _decimal(reserve.get("value") if isinstance(reserve, dict) else None),
                _str(pay_raw.get("exchange_rate")),
                _str(pay_raw.get("bank_account")),
                _int(pay_raw.get("create_time")),
                _int(pay_raw.get("paid_time")),
                json.dumps(pay_raw, ensure_ascii=False, default=str),
            ))
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[tts-erp] persist_payment({pid}) failed: {e}\n")
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
            cur.execute("""
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
            """, (
                rid, shop_id,
                _str(return_raw.get("order_id")),
                _str(return_raw.get("return_status")),
                _str(return_raw.get("return_reason")),
                _str(return_raw.get("return_type")),
                _str(return_raw.get("role")),
                _int(return_raw.get("create_time")),
                _int(return_raw.get("update_time")),
                json.dumps(return_raw, ensure_ascii=False, default=str),
            ))
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[tts-erp] persist_return({rid}) failed: {e}\n")
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
            cur.execute("""
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
            """, (
                cid, shop_id,
                _str(cancel_raw.get("order_id")),
                _str(cancel_raw.get("cancel_status")),
                _str(cancel_raw.get("cancel_reason")),
                _str(cancel_raw.get("cancel_reason_text")),
                _str(cancel_raw.get("cancel_type")),
                _str(cancel_raw.get("role")),
                bool(cancel_raw.get("should_replenish_stock")) if cancel_raw.get("should_replenish_stock") is not None else None,
                _int(cancel_raw.get("create_time")),
                _int(cancel_raw.get("update_time")),
                json.dumps(cancel_raw, ensure_ascii=False, default=str),
            ))
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[tts-erp] persist_cancellation({cid}) failed: {e}\n")
        return False


def persist_shop(shop_id: str, name: str | None, region: str | None,
                 cipher: str | None, seller_type: str | None) -> bool:
    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO shops (shop_id, shop_name, shop_region, shop_cipher, seller_type, last_seen_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (shop_id) DO UPDATE SET
                    shop_name    = COALESCE(EXCLUDED.shop_name,    shops.shop_name),
                    shop_region  = COALESCE(EXCLUDED.shop_region,  shops.shop_region),
                    shop_cipher  = COALESCE(EXCLUDED.shop_cipher,  shops.shop_cipher),
                    seller_type  = COALESCE(EXCLUDED.seller_type,  shops.seller_type),
                    last_seen_at = now()
            """, (shop_id, name, region, cipher, seller_type))
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[tts-erp] persist_shop({shop_id}) failed: {e}\n")
        return False


def log_sync(shop_id: str, sync_type: str, status: str,
             rows: int = 0, error: str | None = None) -> None:
    try:
        with db_connect() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sync_log (shop_id, sync_type, finished_at, rows_affected, status, error_message)
                VALUES (%s, %s, now(), %s, %s, %s)
            """, (shop_id, sync_type, rows, status, error))
            conn.commit()
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[tts-erp] log_sync failed: {e}\n")
    _last_syncs.append({
        "ts": time.time(), "shop_id": shop_id, "sync_type": sync_type,
        "status": status, "rows": rows, "error": error,
    })


# ---------- type coercion helpers ----------
def _int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


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
        data = json.dumps(obj, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
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
            return {"_error": f"token response missing access_token or shop_cipher: {tok}"}
        return at, cipher, region

    # ----- routing -----
    def do_GET(self):  # noqa: N802
        try:
            url = urllib.parse.urlparse(self.path)
            path = url.path.rstrip("/") or "/"
            params = urllib.parse.parse_qs(url.query)

            if path == "/healthz":
                return self._send(200, {"status": "ok", "ts": time.time(), "version": self.server_version})

            if path == "/" or path == "":
                return self._send(200, {
                    "service": "tts-erp",
                    "version": "1.0",
                    "endpoints": "see /endpoints or AGENTS.md",
                })

            if path == "/endpoints":
                return self._send(200, {
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
                    "local_db": [
                        "GET  /db/orders?shop_id=&status=",
                        "GET  /db/orders/<order_id>",
                        "GET  /db/orders/<order_id>/items",
                        "GET  /db/orders/<order_id>/shipping",
                        "GET  /db/statements?shop_id=",
                        "GET  /db/payments?shop_id=&status=",
                        "GET  /db/returns?shop_id=&status=",
                        "GET  /db/cancellations?shop_id=&status=",
                        "GET  /db/sync_log",
                    ],
                    "sync": [
                        "POST /sync/orders        body: {shop_id, order_status?, create_time_ge?, create_time_lt?, page_size?}",
                        "POST /sync/order/<order_id>",
                        "POST /sync/statements     body: {shop_id, statement_time_ge?, statement_time_lt?, page_size?}",
                        "POST /sync/payments       body: {shop_id, create_time_ge?, create_time_lt?, page_size?}",
                        "POST /sync/returns        body: {shop_id, create_time_ge?, create_time_lt?, page_size?}",
                        "POST /sync/cancellations  body: {shop_id, create_time_ge?, create_time_lt?, page_size?}",
                    ],
                })

            # --- OAuth passthrough ---
            if path == "/shops":
                return self._send(200, _proxy_get(f"{OAUTH_RECEIVER_URL}/tokens/shops"))

            if path.startswith("/shops/"):
                shop_id = path[len("/shops/"):].strip("/")
                return self._send(200, fetch_shop_meta(shop_id) or {"_error": f"shop {shop_id} not found"})

            if path.startswith("/token/"):
                shop_id = path[len("/token/"):].strip("/")
                return self._send(200, fetch_token(shop_id, reveal=True) or {"_error": "no token"})

            # --- Order API (read & write) ---
            if path == "/orders/search":
                return self._send(400, {"_error": "POST /orders/search (use POST handler)"})

            if path.startswith("/orders/"):
                rest = path[len("/orders/"):].rstrip("/")
                parts = rest.split("/")
                if len(parts) == 1:
                    order_id = parts[0]
                    if order_id == "search" or order_id == "list":
                        return self._send(400, {"_error": f"POST /orders/{order_id}"})
                    # GET /orders/<order_id>
                    # 202309 spec: detail endpoint is GET /order/202309/orders?ids=<id>
                    # (path-with-id variant returns 36009009 Invalid path)
                    return self._proxy_order(
                        "GET", "/order/202309/orders",
                        order_id=order_id, params=params,
                        body=None, override_path_qp={"ids": order_id},
                    )
                if len(parts) == 2:
                    order_id, action = parts
                    # 202309 spec: action endpoints don't exist in /order/202309/orders/
                    # (cancel/confirm/ship/tracking all return 36009009 Invalid path)
                    # Return 501 Not Implemented to surface the actual upstream error.
                    return self._send(501, {
                        "_error": f"action '{action}' not available on 202309 Order module",
                        "_note": "TikTok 202309 Order module is read-only. Write actions live in Fulfillment/Reverse Logistics modules. See handoff.md.",
                    })

            # --- Finance / Statement / Payment proxy (GET) ---
            # /finance/statements   → GET /finance/202309/statements
            # /finance/payments     → GET /finance/202309/payments
            # Both require sort_field in query (TikTok returns 36009004 otherwise)
            if path in ("/finance/statements", "/finance/payments"):
                kind = path.rsplit("/", 1)[-1]            # "statements" or "payments"
                upstream_path = f"/finance/202309/{kind}"
                return self._proxy_finance("GET", upstream_path, params)

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
            if path == "/db/sync_log":
                return self._send(200, {"items": list(_last_syncs)})
            if path.startswith("/db/orders/"):
                rest = path[len("/db/orders/"):].rstrip("/")
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
            sys.stderr.write(f"[tts-erp] unhandled GET: {e}\n{traceback.format_exc()}\n")
            return self._send(500, {"_error": str(e)})

    def do_POST(self):  # noqa: N802
        try:
            url = urllib.parse.urlparse(self.path)
            path = url.path.rstrip("/") or "/"
            params = urllib.parse.parse_qs(url.query)
            body = self._read_json_body()

            if path == "/orders/search":
                return self._proxy_order("POST", "/order/202309/orders/search", body=body, params=params)
            if path.startswith("/orders/"):
                rest = path[len("/orders/"):].rstrip("/")
                parts = rest.split("/")
                if len(parts) == 2:
                    # 202309 Order module is READ-ONLY — write actions live in
                    # Fulfillment / Reverse Logistics modules. Surface the actual
                    # upstream error instead of silently forwarding.
                    order_id, action = parts
                    return self._send(501, {
                        "_error": f"action '{action}' not available on 202309 Order module",
                        "_note": "TikTok 202309 Order module is read-only. Write actions live in Fulfillment/Reverse Logistics modules. See handoff.md.",
                        "order_id": order_id,
                    })

            if path == "/sync/orders":
                return self._sync_orders(body)
            if path.startswith("/sync/order/"):
                order_id = path[len("/sync/order/"):].strip("/")
                return self._sync_one_order(order_id)
            if path == "/sync/statements":
                return self._sync_statements(body)
            if path == "/sync/payments":
                return self._sync_payments(body)
            if path == "/sync/returns":
                return self._sync_returns(body)
            if path == "/sync/cancellations":
                return self._sync_cancellations(body)

            # Return / Refund / Cancellation proxy (read-only list endpoints)
            if path == "/returns/search":
                return self._proxy_refund("returns", body)
            if path == "/cancellations/search":
                return self._proxy_refund("cancellations", body)
            # Write endpoints (POST without /search) — NOT integrated per user
            # instruction (no high-risk write testing). Surface 501 cleanly.
            if path in ("/returns", "/cancellations"):
                kind = path.lstrip("/")               # "returns" or "cancellations"
                return self._send(501, {
                    "_error": f"POST /return_refund/202309/{kind} is a CREATE endpoint (write) — not integrated",
                    "_note": "Per user instruction, no high-risk write endpoints are tested/integrated. See handoff.md §return_refund.",
                })

            return self._send(404, {"_error": f"not found: {path}"})

        except Exception as e:  # noqa: BLE001
            import traceback
            sys.stderr.write(f"[tts-erp] unhandled POST: {e}\n{traceback.format_exc()}\n")
            return self._send(500, {"_error": str(e)})

    # ----- Order proxy -----
    def _proxy_order(self, method: str, path_template: str, params: dict,
                     body: dict | None = None, order_id: str | None = None,
                     action: str | None = None, override_path_qp: dict | None = None):
        shop_id = (params.get("shop_id") or [None])[0]
        if not shop_id:
            return self._send(400, {"_error": "missing shop_id query param (e.g. ?shop_id=7494763368967603447)"})
        if not order_id and body and "order_id" in body:
            order_id = body["order_id"]
        if not order_id and "{" in path_template and not override_path_qp:
            return self._send(400, {"_error": f"missing order_id in path or body for {path_template}"})

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

        path = resolve_path(path_template, order_id=order_id) if (order_id and "{" in path_template) else path_template
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
        if method == "GET" and result.get("code") == 0 and result.get("data"):
            order = (result["data"].get("order") if isinstance(result["data"], dict) else None) or result["data"]
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
        page_size = int(body.get("page_size") or 50)
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
            search_body["create_time_ge"] = int(create_time_ge)
        if create_time_lt is not None:
            search_body["create_time_lt"] = int(create_time_lt)

        # First page
        first = tiktok_request(
            "POST", TIKTOK_API_HOST, "/order/202309/orders/search",
            access_token, TIKTOK_APP_KEY, TIKTOK_APP_SECRET,
            body=search_body if search_body else None,
            extra_params=extra_params,
            timeout=TTS_ERP_HTTP_TIMEOUT,
        )
        if first.get("code") != 0:
            log_sync(shop_id, "orders_search", "error", error=str(first.get("message")))
            return self._send(502, first)

        data = first.get("data") or {}
        order_list = data.get("order_list") or data.get("orders") or data.get("list") or []
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
                "POST", TIKTOK_API_HOST, "/order/202309/orders/search",
                access_token, TIKTOK_APP_KEY, TIKTOK_APP_SECRET,
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
        return self._send(200, {"shop_id": shop_id, "saved": saved, "total": total, "pages": pages})

    def _sync_one_order(self, order_id: str):
        # Use the proxy GET to also persist
        # (re-using the proxy helper)
        return self._proxy_order("GET", "/order/202309/orders/{order_id}", params={"shop_id": [order_id[:0] or ""]}) \
            if False else self._do_sync_one(order_id)

    def _do_sync_one(self, order_id: str):
        # We need a shop_id to get a token. Look it up from local DB or fail.
        try:
            with db_connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT shop_id FROM orders WHERE order_id = %s LIMIT 1", (order_id,))
                row = cur.fetchone()
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": f"db lookup failed: {e}"})
        if not row:
            return self._send(404, {"_error": f"order {order_id} not in local DB; need shop_id to fetch"})
        shop_id = row[0]
        result = self._proxy_order(
            "GET", "/order/202309/orders",
            params={"shop_id": [shop_id]},
            order_id=order_id,
            override_path_qp={"ids": order_id},
        )
        return result

    # ----- Local DB reads -----
    def _db_list_orders(self, params: dict):
        shop_id = (params.get("shop_id") or [None])[0]
        status = (params.get("status") or [None])[0]
        limit = int((params.get("limit") or ["50"])[0])
        try:
            with db_connect() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
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
                cur.execute(sql_str, args)
                rows = cur.fetchall()
            return self._send(200, {"count": len(rows), "items": rows})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": str(e)})

    def _db_get_order(self, order_id: str):
        try:
            with db_connect() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT * FROM orders WHERE order_id = %s", (order_id,))
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
            with db_connect() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT * FROM order_items WHERE order_id = %s", (order_id,))
                rows = cur.fetchall()
            return self._send(200, {"count": len(rows), "items": rows})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": str(e)})

    def _db_get_order_shipping(self, order_id: str):
        try:
            with db_connect() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                cur.execute("SELECT * FROM order_shippings WHERE order_id = %s", (order_id,))
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
            return self._send(400, {"_error": "missing shop_id query param (e.g. ?shop_id=7494763368967603447)"})

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
            forwarded["sort_field"] = "statement_time" if "statements" in upstream_path else "create_time"
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
        page_size = int(body.get("page_size") or 50)
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
            extra_params["statement_time_ge"] = str(int(statement_time_ge))
        if statement_time_lt is not None:
            extra_params["statement_time_lt"] = str(int(statement_time_lt))

        first = tiktok_request(
            "GET", TIKTOK_API_HOST, "/finance/202309/statements",
            access_token, TIKTOK_APP_KEY, TIKTOK_APP_SECRET,
            body=None, extra_params=extra_params, timeout=TTS_ERP_HTTP_TIMEOUT,
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
                "GET", TIKTOK_API_HOST, "/finance/202309/statements",
                access_token, TIKTOK_APP_KEY, TIKTOK_APP_SECRET,
                body=None, extra_params=extra_params, timeout=TTS_ERP_HTTP_TIMEOUT,
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
        return self._send(200, {"shop_id": shop_id, "saved": saved, "total": total, "pages": pages})

    def _sync_payments(self, body: dict):
        shop_id = body.get("shop_id")
        if not shop_id:
            return self._send(400, {"_error": "missing shop_id in body"})
        page_size = int(body.get("page_size") or 50)
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
            extra_params["create_time_ge"] = str(int(create_time_ge))
        if create_time_lt is not None:
            extra_params["create_time_lt"] = str(int(create_time_lt))

        first = tiktok_request(
            "GET", TIKTOK_API_HOST, "/finance/202309/payments",
            access_token, TIKTOK_APP_KEY, TIKTOK_APP_SECRET,
            body=None, extra_params=extra_params, timeout=TTS_ERP_HTTP_TIMEOUT,
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
                "GET", TIKTOK_API_HOST, "/finance/202309/payments",
                access_token, TIKTOK_APP_KEY, TIKTOK_APP_SECRET,
                body=None, extra_params=extra_params, timeout=TTS_ERP_HTTP_TIMEOUT,
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
        return self._send(200, {"shop_id": shop_id, "saved": saved, "total": total, "pages": pages})

    # ----- Local DB reads for finance -----
    def _db_list_statements(self, params: dict):
        shop_id = (params.get("shop_id") or [None])[0]
        limit = int((params.get("limit") or ["50"])[0])
        try:
            with db_connect() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                sql = "SELECT statement_id, shop_id, payment_id, currency, payment_status, statement_time, payment_time, revenue_amount, fee_amount, net_sales_amount, shipping_cost_amount, adjustment_amount, settlement_amount, synced_at FROM statements"
                args = []
                if shop_id:
                    sql += " WHERE shop_id = %s"
                    args.append(shop_id)
                sql += " ORDER BY statement_time DESC NULLS LAST LIMIT %s"
                args.append(limit)
                cur.execute(sql, args)
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
        limit = int((params.get("limit") or ["50"])[0])
        try:
            with db_connect() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                sql = "SELECT payment_id, shop_id, status, currency, amount_value, settlement_amount_value, payment_amount_before_value, reserve_amount_value, exchange_rate, bank_account, create_time, paid_time, synced_at FROM payments"
                args = []
                wh = []
                if shop_id:
                    wh.append("shop_id = %s")
                    args.append(shop_id)
                if status:
                    wh.append("status = %s")
                    args.append(status)
                if wh:
                    sql += " WHERE " + " AND ".join(wh)
                sql += " ORDER BY paid_time DESC NULLS LAST LIMIT %s"
                args.append(limit)
                cur.execute(sql, args)
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
            return self._send(400, {"_error": "missing shop_id in body (e.g. {\"shop_id\": \"...\"})"})

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
            "page_size": str(min(max(int(body.get("page_size") or 50), 10), 50)),
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
                extra_params[k] = str(int(upstream_body.pop(k)))

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
        page_size = int(body.get("page_size") or 50)

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
        # 2026-08-16 fix: TikTok return_refund search endpoints strictly type-check
        # query string (returns "actual type:string, expected type:int64" if we put
        # create_time_ge in extra_params). Move time filter into POST body as int,
        # mirroring the working _sync_orders pattern. Statements endpoint is more
        # lenient so it stays in query.
        filter_body: dict = {}
        if body.get("create_time_ge") is not None:
            filter_body["create_time_ge"] = int(body["create_time_ge"])
        if body.get("create_time_lt") is not None:
            filter_body["create_time_lt"] = int(body["create_time_lt"])

        first = tiktok_request(
            "POST", TIKTOK_API_HOST, "/return_refund/202309/returns/search",
            access_token, TIKTOK_APP_KEY, TIKTOK_APP_SECRET,
            body=filter_body if filter_body else None,
            extra_params=extra_params, timeout=TTS_ERP_HTTP_TIMEOUT,
        )
        if first.get("code") != 0:
            log_sync(shop_id, "returns", "error", error=str(first.get("message")))
            return self._send(502, first)

        data = first.get("data") or {}
        items = data.get("return_orders") or data.get("returns") or data.get("list") or []
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
                "POST", TIKTOK_API_HOST, "/return_refund/202309/returns/search",
                access_token, TIKTOK_APP_KEY, TIKTOK_APP_SECRET,
                body=filter_body if filter_body else None,
                extra_params=extra_params, timeout=TTS_ERP_HTTP_TIMEOUT,
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
        return self._send(200, {"shop_id": shop_id, "saved": saved, "total": total, "pages": pages})

    def _sync_cancellations(self, body: dict):
        shop_id = body.get("shop_id")
        if not shop_id:
            return self._send(400, {"_error": "missing shop_id in body"})
        page_size = int(body.get("page_size") or 50)

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
        # 2026-08-16 fix: TikTok return_refund search endpoints strictly type-check
        # query string (returns "actual type:string, expected type:int64" if we put
        # create_time_ge in extra_params). Move time filter into POST body as int,
        # mirroring the working _sync_orders pattern. Statements endpoint is more
        # lenient so it stays in query.
        filter_body: dict = {}
        if body.get("create_time_ge") is not None:
            filter_body["create_time_ge"] = int(body["create_time_ge"])
        if body.get("create_time_lt") is not None:
            filter_body["create_time_lt"] = int(body["create_time_lt"])

        first = tiktok_request(
            "POST", TIKTOK_API_HOST, "/return_refund/202309/cancellations/search",
            access_token, TIKTOK_APP_KEY, TIKTOK_APP_SECRET,
            body=filter_body if filter_body else None,
            extra_params=extra_params, timeout=TTS_ERP_HTTP_TIMEOUT,
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
                "POST", TIKTOK_API_HOST, "/return_refund/202309/cancellations/search",
                access_token, TIKTOK_APP_KEY, TIKTOK_APP_SECRET,
                body=filter_body if filter_body else None,
                extra_params=extra_params, timeout=TTS_ERP_HTTP_TIMEOUT,
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
        return self._send(200, {"shop_id": shop_id, "saved": saved, "total": total, "pages": pages})

    # ----- Local DB reads for returns / cancellations -----
    def _db_list_returns(self, params: dict):
        shop_id = (params.get("shop_id") or [None])[0]
        status = (params.get("status") or [None])[0]
        limit = int((params.get("limit") or ["50"])[0])
        try:
            with db_connect() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                sql = "SELECT return_id, shop_id, order_id, return_status, return_reason, return_type, role, create_time, update_time, synced_at FROM returns"
                args = []
                wh = []
                if shop_id:
                    wh.append("shop_id = %s")
                    args.append(shop_id)
                if status:
                    wh.append("return_status = %s")
                    args.append(status)
                if wh:
                    sql += " WHERE " + " AND ".join(wh)
                sql += " ORDER BY create_time DESC NULLS LAST LIMIT %s"
                args.append(limit)
                cur.execute(sql, args)
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
        limit = int((params.get("limit") or ["50"])[0])
        try:
            with db_connect() as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                sql = "SELECT cancel_id, shop_id, order_id, cancel_status, cancel_reason, cancel_reason_text, cancel_type, role, should_replenish_stock, create_time, update_time, synced_at FROM cancellations"
                args = []
                wh = []
                if shop_id:
                    wh.append("shop_id = %s")
                    args.append(shop_id)
                if status:
                    wh.append("cancel_status = %s")
                    args.append(status)
                if wh:
                    sql += " WHERE " + " AND ".join(wh)
                sql += " ORDER BY create_time DESC NULLS LAST LIMIT %s"
                args.append(limit)
                cur.execute(sql, args)
                rows = cur.fetchall()
            for r in rows:
                if r.get("synced_at") and hasattr(r["synced_at"], "isoformat"):
                    r["synced_at"] = r["synced_at"].isoformat()
            return self._send(200, {"count": len(rows), "items": rows})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"_error": str(e)})

    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write(f"[tts-erp] {self.address_string()} - {fmt % args}\n")


def _proxy_get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


# ---------- main ----------
def main() -> int:
    if not TIKTOK_APP_KEY or not TIKTOK_APP_SECRET:
        sys.stderr.write("[tts-erp] WARN: TIKTOK_APP_KEY / TIKTOK_APP_SECRET not set\n")
    if not TTS_ERP_DB_URL:
        sys.stderr.write("[tts-erp] WARN: TTS_ERP_DB_URL not set — DB-backed endpoints will fail\n")
    db_ok = db_init()
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
