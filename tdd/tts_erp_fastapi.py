"""tts-erp FastAPI application.

Listens on port 9877 (production) or 9878 (test). Replaces the stdlib
BaseHTTPRequestHandler implementation in tts_erp.py.

Phase 4a-3 MVP: implements the 5 sync endpoints + healthz + oauth-receiver
passthrough. Other endpoints (TikTok proxy, /db/* reads) are still served
by tts_erp.py on 9877 during the migration. After verification we'll
port the rest.

Run:
    uvicorn tts_erp_fastapi:app --host 0.0.0.0 --port 9877
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

# Make tts_business + tts_signing + tts_erp importable
TTS_ERP_ROOT = Path(__file__).resolve().parent.parent
if str(TTS_ERP_ROOT) not in sys.path:
    sys.path.insert(0, str(TTS_ERP_ROOT))

# Load .env
_env_path = TTS_ERP_ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

import tts_erp  # noqa: E402
import tts_business  # noqa: E402
from http_client import PlainHttpClient, TikTokHttpClient  # noqa: E402
from pg_repositories import make_pg_repos  # noqa: E402
from token_provider import OAuthReceiverTokenProvider  # noqa: E402


# ─── App + config ─────────────────────────────────────────────────────

app = FastAPI(title="tts-erp", version="1.0")

# Config
TTS_ERP_PORT = int(os.environ.get("TTS_ERP_PORT", "9877"))
OAUTH_RECEIVER_URL = os.environ.get("OAUTH_RECEIVER_URL", "http://127.0.0.1:9876").rstrip("/")
TIKTOK_API_HOST = os.environ.get("TIKTOK_API_HOST", "https://open-api.tiktokglobalshop.com")
TIKTOK_APP_KEY = os.environ.get("TIKTOK_APP_KEY", "")
TIKTOK_APP_SECRET = os.environ.get("TIKTOK_APP_SECRET", "")
TTS_ERP_HTTP_TIMEOUT = 60  # seconds

# Dependency graph
_plain_http = PlainHttpClient(timeout=10)
_token_provider = OAuthReceiverTokenProvider(base_url=OAUTH_RECEIVER_URL, http=_plain_http)
_repos = make_pg_repos()


def _tiktok_http_for(creds_provider) -> TikTokHttpClient:
    """Build a TikTokHttpClient bound to a per-request token fetcher."""
    return TikTokHttpClient(
        api_host=TIKTOK_API_HOST,
        app_key=TIKTOK_APP_KEY,
        app_secret=TIKTOK_APP_SECRET,
        get_access_token=lambda: creds_provider.access_token,
    )


def _get_creds(shop_id: str):
    """Fetch creds for a shop. Returns Creds or raises HTTPException(502)."""
    try:
        return _token_provider.get(shop_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"token fetch failed: {e}")


def _log_sync(shop_id: str, sync_type: str, status: str,
              rows: int | None = None, error: str | None = None) -> None:
    """Wrapper around tts_erp.log_sync that maps errors to stderr (doesn't break response)."""
    try:
        tts_erp.log_sync(shop_id, sync_type, status, rows=rows, error=error)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[tts-erp-fastapi] log_sync failed: {e}\n")


# ─── Health / meta ────────────────────────────────────────────────────


@app.get("/healthz")
def healthz():
    return {"status": "ok", "ts": time.time(), "version": "TtsErp/1.0-fastapi"}


@app.get("/endpoints")
def endpoints():
    """Return full endpoint list — matches stdlib tts_erp.py schema for compatibility."""
    return {
        "service": "tts-erp",
        "version": "1.0-fastapi",
        "passthrough": [
            "GET /shops",
            "GET /shops/<shop_id>",
            "GET /token/<shop_id>?reveal=1",
        ],
        "orders_api_proxy": [
            "POST /orders/search              (paging/sort in query string)",
            "POST /orders/list",
            "GET  /orders/<order_id>          (→ /order/202309/orders?ids=<id>)",
            "POST /orders/<order_id>/confirm",
            "POST /orders/<order_id>/cancel",
            "POST /orders/<order_id>/update_status",
            "POST /orders/<order_id>/shipping_info",
            "POST /orders/<order_id>/verify_shipping",
            "GET  /orders/<order_id>/tracking",
            "GET  /orders/<order_id>/tracking/get",
            "GET  /orders/<order_id>/risk",
            "GET  /orders/<order_id>/buyer",
            "GET  /orders/<order_id>/recipient",
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
    }


# ─── OAuth-receiver passthrough ───────────────────────────────────────


@app.get("/shops")
def list_shops():
    """Proxy to oauth-receiver /tokens/shops."""
    try:
        with urllib.request.urlopen(f"{OAUTH_RECEIVER_URL}/tokens/shops", timeout=5) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"oauth-receiver /tokens/shops failed: {e}")


@app.get("/shops/{shop_id}")
def get_shop(shop_id: str):
    """Get one shop's metadata from oauth-receiver."""
    try:
        with urllib.request.urlopen(f"{OAUTH_RECEIVER_URL}/tokens/shops", timeout=5) as r:
            data = json.load(r)
        for item in data.get("items", []):
            if item.get("shop_id") == shop_id:
                return item
        raise HTTPException(status_code=404, detail="shop not found")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/token/{shop_id}")
def get_token(shop_id: str, reveal: int = Query(0)):
    """Proxy to oauth-receiver /token/<id>. reveal=1 for plaintext."""
    if not reveal:
        raise HTTPException(status_code=400, detail="reveal=1 required to fetch token")
    url = f"{OAUTH_RECEIVER_URL}/token/{urllib.parse.quote(shop_id)}?reveal=1"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=e.code, detail=body[:300])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))


# ─── Sync endpoints (Phase 4a-3 MVP) ─────────────────────────────────


def _run_sync(sync_type: str, log_type: str, business_fn, body: dict) -> dict:
    """Common sync handler: validate shop_id, get creds, call business fn."""
    shop_id = body.get("shop_id")
    if not shop_id:
        raise HTTPException(status_code=400, detail="missing shop_id in body")
    creds = _get_creds(shop_id)
    http = _tiktok_http_for(creds)

    # Map sync_type to repo name
    repo_map = {
        "orders": "orders", "payments": "payments",
        "statements": "statements", "returns": "returns",
        "cancellations": "cancellations",
    }
    repo = _repos[repo_map[sync_type]]

    result = business_fn(creds, body, http=http, repo=repo)
    if not result.ok:
        _log_sync(shop_id, log_type, "error", error=result.error)
        raise HTTPException(status_code=502, detail=result.error)
    _log_sync(shop_id, log_type, "ok", rows=result.saved)
    return {
        "shop_id": shop_id, "saved": result.saved,
        "total": result.total, "pages": result.pages,
    }


@app.post("/sync/orders")
def sync_orders(body: dict):
    return _run_sync("orders", "orders_search", tts_business.sync_orders, body)


@app.post("/sync/payments")
def sync_payments(body: dict):
    return _run_sync("payments", "payments", tts_business.sync_payments, body)


@app.post("/sync/statements")
def sync_statements(body: dict):
    return _run_sync("statements", "statements", tts_business.sync_statements, body)


@app.post("/sync/returns")
def sync_returns(body: dict):
    return _run_sync("returns", "returns", tts_business.sync_returns, body)


@app.post("/sync/cancellations")
def sync_cancellations(body: dict):
    return _run_sync("cancellations", "cancellations", tts_business.sync_cancellations, body)


# Sync order detail — not yet ported; keep returning 501
@app.post("/sync/order/{order_id}")
def sync_one_order(order_id: str, body: dict):
    raise HTTPException(status_code=501, detail="sync single order not yet ported to FastAPI")


# ─── 501 stubs (preserve AGENTS.md contract) ──────────────────────────


@app.post("/returns")
def returns_create():
    raise HTTPException(status_code=501, detail="returns CREATE not supported (AGENTS.md §4)")


@app.post("/cancellations")
def cancellations_create():
    raise HTTPException(status_code=501, detail="cancellations CREATE not supported (AGENTS.md §4)")


# ─── DB read endpoints (Phase 4a-3 expansion) ─────────────────────────


def _isoformat_timestamps(row: dict, keys: tuple[str, ...] = ("synced_at", "updated_at")):
    for k in keys:
        if row.get(k) and hasattr(row[k], "isoformat"):
            row[k] = row[k].isoformat()
    return row


def _db_query_dict(sql: str, args: tuple = ()) -> list[dict]:
    """Run a SELECT, return list of dict rows."""
    with tts_erp.db_connect() as conn, conn.cursor(row_factory=__import__("psycopg").rows.dict_row) as cur:
        cur.execute(sql, args)
        return list(cur.fetchall())


@app.get("/db/orders")
def db_list_orders(shop_id: str | None = None, status: str | None = None, limit: int = 50):
    sql = "SELECT order_id, shop_id, order_status_name AS order_status, payment_amount, payment_currency, total_amount, create_time, update_time, synced_at FROM orders"
    wh = []
    args: list = []
    if shop_id:
        wh.append("shop_id = %s")
        args.append(shop_id)
    if status is not None:
        wh.append("order_status_name = %s")
        args.append(status)
    if wh:
        sql += " WHERE " + " AND ".join(wh)
    sql += " ORDER BY create_time DESC NULLS LAST LIMIT %s"
    args.append(limit)
    rows = _db_query_dict(sql, tuple(args))
    for r in rows:
        _isoformat_timestamps(r)
    return {"count": len(rows), "items": rows}


@app.get("/db/orders/{order_id}")
def db_get_order(order_id: str):
    rows = _db_query_dict("SELECT * FROM orders WHERE order_id = %s", (order_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"order {order_id} not in local DB")
    return _isoformat_timestamps(rows[0])


@app.get("/db/orders/{order_id}/items")
def db_get_order_items(order_id: str):
    rows = _db_query_dict("SELECT * FROM order_items WHERE order_id = %s", (order_id,))
    return {"count": len(rows), "items": rows}


@app.get("/db/orders/{order_id}/shipping")
def db_get_order_shipping(order_id: str):
    rows = _db_query_dict("SELECT * FROM order_shippings WHERE order_id = %s", (order_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"no shipping for {order_id}")
    return _isoformat_timestamps(rows[0])


@app.get("/db/statements")
def db_list_statements(shop_id: str | None = None, limit: int = 50):
    sql = "SELECT statement_id, shop_id, payment_id, currency, payment_status, statement_time, payment_time, revenue_amount, fee_amount, net_sales_amount, shipping_cost_amount, adjustment_amount, settlement_amount, synced_at FROM statements"
    args: list = []
    if shop_id:
        sql += " WHERE shop_id = %s"
        args.append(shop_id)
    sql += " ORDER BY statement_time DESC NULLS LAST LIMIT %s"
    args.append(limit)
    rows = _db_query_dict(sql, tuple(args))
    for r in rows:
        _isoformat_timestamps(r)
    return {"count": len(rows), "items": rows}


@app.get("/db/payments")
def db_list_payments(shop_id: str | None = None, status: str | None = None, limit: int = 50):
    sql = "SELECT payment_id, shop_id, status, currency, amount_value, settlement_amount_value, exchange_rate, create_time, paid_time, synced_at FROM payments"
    wh = []
    args: list = []
    if shop_id:
        wh.append("shop_id = %s")
        args.append(shop_id)
    if status is not None:
        wh.append("status = %s")
        args.append(status)
    if wh:
        sql += " WHERE " + " AND ".join(wh)
    sql += " ORDER BY create_time DESC NULLS LAST LIMIT %s"
    args.append(limit)
    rows = _db_query_dict(sql, tuple(args))
    for r in rows:
        _isoformat_timestamps(r)
    return {"count": len(rows), "items": rows}


@app.get("/db/returns")
def db_list_returns(shop_id: str | None = None, status: str | None = None, limit: int = 50):
    sql = "SELECT return_id, shop_id, return_status, return_type, create_time, update_time, synced_at FROM returns"
    wh = []
    args: list = []
    if shop_id:
        wh.append("shop_id = %s")
        args.append(shop_id)
    if status is not None:
        wh.append("return_status = %s")
        args.append(status)
    if wh:
        sql += " WHERE " + " AND ".join(wh)
    sql += " ORDER BY create_time DESC NULLS LAST LIMIT %s"
    args.append(limit)
    rows = _db_query_dict(sql, tuple(args))
    for r in rows:
        _isoformat_timestamps(r)
    return {"count": len(rows), "items": rows}


@app.get("/db/cancellations")
def db_list_cancellations(shop_id: str | None = None, status: str | None = None, limit: int = 50):
    sql = "SELECT cancel_id, shop_id, cancel_status, cancel_type, create_time, update_time, synced_at FROM cancellations"
    wh = []
    args: list = []
    if shop_id:
        wh.append("shop_id = %s")
        args.append(shop_id)
    if status is not None:
        wh.append("cancel_status = %s")
        args.append(status)
    if wh:
        sql += " WHERE " + " AND ".join(wh)
    sql += " ORDER BY create_time DESC NULLS LAST LIMIT %s"
    args.append(limit)
    rows = _db_query_dict(sql, tuple(args))
    for r in rows:
        _isoformat_timestamps(r)
    return {"count": len(rows), "items": rows}


@app.get("/db/sync_log")
def db_sync_log(limit: int = 50):
    """Return last N sync log entries from in-memory deque."""
    return {"items": list(tts_erp._last_syncs)[-limit:]}


# ─── TikTok proxy endpoints (Phase 4a-3 expansion) ───────────────────


def _tiktok_proxy(
    method: str,
    upstream_path: str,
    *,
    shop_id: str,
    body: dict | None = None,
    extra_query: dict[str, str] | None = None,
    persist_order_on_get: bool = False,
):
    """Generic proxy: get creds, call tiktok_request, return JSON.

    shop_id is required (we use it to fetch creds).
    extra_query: forwarded as TikTok query params (page_size, sort_field, ...).
    persist_order_on_get: if True and GET succeeds with order data, persist locally.
    """
    creds = _get_creds(shop_id)
    forwarded: dict[str, str] = {"shop_cipher": creds.shop_cipher}
    if extra_query:
        forwarded.update({k: str(v) for k, v in extra_query.items()})
    http = _tiktok_http_for(creds)
    result = http.request(
        method, upstream_path,
        body=body if method != "GET" else None,
        extra_params=forwarded,
        timeout=TTS_ERP_HTTP_TIMEOUT,
    )

    if persist_order_on_get and method == "GET" and result.get("code") == 0 and result.get("data"):
        data = result["data"]
        order = (data.get("order") if isinstance(data, dict) else None) or data
        if isinstance(order, dict) and (order.get("id") or order.get("order_id")):
            tts_erp.persist_order(shop_id, order)

    if result.get("code") == 0:
        return result
    # Error: bubble up as 502
    raise HTTPException(status_code=502, detail=result)


# Order endpoints ──────────────────────────────────────────────────────


@app.post("/orders/search")
def orders_search(shop_id: str, body: dict):
    return _tiktok_proxy("POST", "/order/202309/orders/search", shop_id=shop_id, body=body)


@app.post("/orders/list")
def orders_list(shop_id: str, body: dict):
    return _tiktok_proxy("POST", "/order/202309/orders/search", shop_id=shop_id, body=body)


@app.get("/orders/{order_id}")
def order_detail(order_id: str, shop_id: str):
    # 202309: /orders?ids=<id> (not /orders/{id})
    return _tiktok_proxy(
        "GET", "/order/202309/orders",
        shop_id=shop_id, extra_query={"ids": order_id},
        persist_order_on_get=True,
    )


@app.post("/orders/{order_id}/confirm")
def order_confirm(order_id: str, shop_id: str, body: dict | None = None):
    body = body or {}
    return _tiktok_proxy("POST", f"/order/202309/orders/{order_id}/confirm", shop_id=shop_id, body=body)


@app.post("/orders/{order_id}/cancel")
def order_cancel(order_id: str, shop_id: str, body: dict | None = None):
    body = body or {}
    return _tiktok_proxy("POST", f"/order/202309/orders/{order_id}/cancel", shop_id=shop_id, body=body)


@app.post("/orders/{order_id}/update_status")
def order_update_status(order_id: str, shop_id: str, body: dict | None = None):
    body = body or {}
    return _tiktok_proxy("POST", f"/order/202309/orders/{order_id}/update_status", shop_id=shop_id, body=body)


@app.post("/orders/{order_id}/shipping_info")
def order_shipping_info(order_id: str, shop_id: str, body: dict | None = None):
    body = body or {}
    return _tiktok_proxy("POST", f"/order/202309/orders/{order_id}/shipping_info", shop_id=shop_id, body=body)


@app.post("/orders/{order_id}/verify_shipping")
def order_verify_shipping(order_id: str, shop_id: str, body: dict | None = None):
    body = body or {}
    return _tiktok_proxy("POST", f"/order/202309/orders/{order_id}/verify_shipping", shop_id=shop_id, body=body)


@app.get("/orders/{order_id}/tracking")
def order_tracking(order_id: str, shop_id: str):
    return _tiktok_proxy("GET", f"/order/202309/orders/{order_id}/tracking", shop_id=shop_id)


@app.get("/orders/{order_id}/tracking/get")
def order_tracking_get(order_id: str, shop_id: str):
    return _tiktok_proxy("GET", f"/order/202309/orders/{order_id}/tracking", shop_id=shop_id)


@app.get("/orders/{order_id}/risk")
def order_risk(order_id: str, shop_id: str):
    return _tiktok_proxy("GET", f"/order/202309/orders/{order_id}/risk", shop_id=shop_id)


@app.get("/orders/{order_id}/buyer")
def order_buyer(order_id: str, shop_id: str):
    return _tiktok_proxy("GET", f"/order/202309/orders/{order_id}/buyer", shop_id=shop_id)


@app.get("/orders/{order_id}/recipient")
def order_recipient(order_id: str, shop_id: str):
    return _tiktok_proxy("GET", f"/order/202309/orders/{order_id}/recipient", shop_id=shop_id)


# Finance endpoints ────────────────────────────────────────────────────


@app.get("/finance/statements")
def finance_statements(shop_id: str, page_size: int = 50, sort_field: str = "statement_time", sort_order: str = "DESC"):
    return _tiktok_proxy(
        "GET", "/finance/202309/statements", shop_id=shop_id,
        extra_query={"page_size": page_size, "sort_field": sort_field, "sort_order": sort_order},
    )


@app.get("/finance/payments")
def finance_payments(shop_id: str, page_size: int = 50, sort_field: str = "create_time", sort_order: str = "DESC"):
    return _tiktok_proxy(
        "GET", "/finance/202309/payments", shop_id=shop_id,
        extra_query={"page_size": page_size, "sort_field": sort_field, "sort_order": sort_order},
    )


# Return / Cancellation search proxy ───────────────────────────────────


@app.post("/returns/search")
def returns_search(shop_id: str, body: dict | None = None):
    body = body or {}
    return _tiktok_proxy("POST", "/return_refund/202309/returns/search", shop_id=shop_id, body=body)


@app.post("/cancellations/search")
def cancellations_search(shop_id: str, body: dict | None = None):
    body = body or {}
    return _tiktok_proxy("POST", "/return_refund/202309/cancellations/search", shop_id=shop_id, body=body)
