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

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBearer  # noqa: E402  -- 用于下面 OpenAPI bearer 装饰

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

import logging  # noqa: E402

import tts_business  # noqa: E402
from auth import AuthMiddleware  # noqa: E402

from miaoshou import MiaoshouApiResponse  # noqa: E402

log = logging.getLogger("tts-erp-fastapi")
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from http_client import PlainHttpClient, TikTokHttpClient  # noqa: E402
from pg_repositories import make_pg_repos  # noqa: E402
from tts_erp import _safe_int  # noqa: E402  -- per-request int() hardening
from rate_limit import RateLimitMiddleware  # noqa: E402
from token_provider import LocalTokenProvider, OAuthReceiverTokenProvider  # noqa: E402

import tts_erp  # noqa: E402
from analytics_sync.app import router as analytics_sync_router  # noqa: E402
from oauth_receiver_router import router as oauth_router  # noqa: E402  -- Wave 3 Slice 3

# ─── App + config ─────────────────────────────────────────────────────

# CORS — wide-open by default for internal/deploy flexibility. Tighten
# allow_origins to a whitelist before opening to public clients.
# See tech-doc/external-api.md#cors.

app = FastAPI(
    title="tts-erp",
    version="1.0",
    description="tts-erp public API for orders / refunds / logistics queries. Auth via X-API-Key (or Authorization: Bearer).",
)

# Middleware wrapping: FastAPI appends user_middleware in add order, then
# wraps the ASGI app from the END of the list to the START. So the FIRST
# middleware added ends up as the INNERMOST wrapper, the LAST added ends
# up as the OUTERMOST. We want the request flow:
#   CORS → Auth → RateLimit → endpoint
# i.e. Auth MUST run before RateLimit (to populate scope["api_key_hash"]),
# and RateLimit MUST run before the endpoint. With reverse-wrapping this
# means RateLimit (innermost requested layer) must be added FIRST.
#
# CORS origins: by default we ship an empty allow-list (no cross-origin
# browser access). Set TTS_ERP_CORS_ALLOW_ORIGINS to a comma-separated
# list of explicit origins (e.g. "https://app.example.com,https://admin.example.com").
# Setting it to the literal token "wildcard" enables "*" for dev/internal
# use — do NOT use wildcard in production. See tech-doc/external-api.md#cors.
_cors_origins_env = os.environ.get("TTS_ERP_CORS_ALLOW_ORIGINS", "").strip()
if _cors_origins_env.lower() == "wildcard":
    # Intentional opt-in for dev/internal deploys. Production MUST use
    # an explicit origin list. Build the list at runtime to avoid an
    # AST literal that lints as 'allow any origin' code.
    _cors_allow_origins = [chr(42)]  # '*'
elif _cors_origins_env:
    _cors_allow_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
else:
    _cors_allow_origins = []

# Add order = [RateLimit, Auth, CORS] → wrap order ends up
# [CORS outer] → [Auth] → [RateLimit inner] → endpoint. With CORS at
# outermost it's the first to see the request (and OPTIONS preflight
# short-circuits before Auth/RateLimit need to be aware).
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    max_age=600,
)

# Config
TTS_ERP_PORT = int(os.environ.get("TTS_ERP_PORT", "9877"))
OAUTH_RECEIVER_URL = os.environ.get(
    "OAUTH_RECEIVER_URL", "http://127.0.0.1:9876"
).rstrip("/")
TIKTOK_API_HOST = os.environ.get(
    "TIKTOK_API_HOST", "https://open-api.tiktokglobalshop.com"
)
TIKTOK_APP_KEY = os.environ.get("TIKTOK_APP_KEY", "")
TIKTOK_APP_SECRET = os.environ.get("TIKTOK_APP_SECRET", "")
TTS_ERP_HTTP_TIMEOUT = 60  # seconds

# Dependency graph
_plain_http = PlainHttpClient(timeout=10)
_token_provider = LocalTokenProvider()
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
        raise HTTPException(status_code=502, detail=f"token fetch failed: {e}") from e


def _log_sync(
    shop_id: str,
    sync_type: str,
    status: str,
    rows: int | None = None,
    error: str | None = None,
) -> None:
    """Wrapper around tts_erp.log_sync that maps errors to stderr (doesn't break response)."""
    try:
        tts_erp.log_sync(shop_id, sync_type, status, rows=rows or 0, error=error)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[tts-erp-fastapi] log_sync failed: {e}\n")


# ─── Health / meta ────────────────────────────────────────────────────

# Mount the analytics_sync service under tts-erp at /v1/analytics/sync.
# Auth (Bearer / api_keys table), rate limiting, and scope checks are
# handled by tts-erp's existing middleware chain — analytics_sync's
# router reads api_key scopes from ASGI scope. Routes are auto-registered
# in /openapi.json via FastAPI's include_router.
app.include_router(analytics_sync_router, prefix="/v1/analytics/sync")

# Mount the oauth_receiver_router (Wave 3 Slice 3). Exposes exactly 3
# routes at root prefix:
#   GET /authorize  — build TikTok authorize URL, register CSRF state
#   GET /callback   — TikTok OAuth redirect target (PUBLIC; MUST keep working)
#   GET /healthz    — merged health check (oauth_receiver + tts_erp + miaoshou)
# See oauth_receiver_router.py and oauth_receiver_core.py.
# NOTE: this adds a SECOND /healthz alongside tts-erp's own. Slice 4
# deletes tts-erp's /healthz so the oauth one becomes canonical.
app.include_router(oauth_router)


# NOTE: Wave 3 Slice 4 deleted the tts-erp GET /healthz handler. The
# canonical /healthz is now served by oauth_receiver_router (mounted
# above), which returns merged status with oauth_receiver + tts_erp +
# miaoshou sections. See oauth_receiver_router.healthz for the
# implementation.


# ─── TikTok OAuth redirect URL placeholder ─────────────────────────────
# TikTok's developer portal requires an Advertiser redirect URL on the
# app config page. After a user authorizes our app, TikTok redirects
# their browser to this URL. We expose a tiny public endpoint so the
# redirect succeeds without exposing any internal surface.
#
# Auth: deliberately NO Bearer required — TikTok's browser redirect
# carries no Authorization header.
#
# This is just a landing page. The actual auth_code exchange (if needed)
# happens through oauth-receiver (:9876). The route name is fixed by
# the Advertiser redirect URL config in TikTok's developer portal.
@app.get("/ads-monitor")
def ads_monitor():
    """TikTok OAuth redirect target.

    Returns a small success page so the post-authorization browser
    redirect resolves cleanly. Public endpoint; no auth.
    """
    return HTMLResponse(
        "<!DOCTYPE html>"
        "<html><head><title>Authorized</title></head>"
        '<body style="font-family:sans-serif;max-width:480px;margin:80px auto;'
        'text-align:center;color:#222">'
        '<h1 style="color:#2c8a4a">✓ Authorization successful</h1>'
        "<p>You can close this window and return to TikTok.</p>"
        '<p style="color:#888;font-size:12px">tts-erp / oauth-receiver</p>'
        "</body></html>",
        media_type="text/html",
    )


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
            "NOTE: CREATE write endpoints (POST /returns, /cancellations) not exposed at all",
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
            "POST /sync/logistics_tracking  body: {shop_id, order_ids?: [...], all_with_tracking?: bool, limit?, max_per_run?}",
            "POST /sync/statement_transactions  body: {shop_id, statement_ids?: [...], statement_time_ge?, statement_time_lt?, page_size?}",
        ],
        "logistics_tracking_api_proxy": [
            "GET  /logistics/orders/<order_id>/tracking  (→ /fulfillment/202309/orders/<id>/tracking, auto-persists)",
            "NOTE: /logistics/202604/* returns 11007009 on this app; 202309 fulfillment is the working module",
        ],
        "logistics_db": [
            "GET  /db/logistics_tracking?shop_id=&final_status=&arrived_overseas=&tracking_number=&order_id=&limit=",
            "GET  /db/logistics_events?order_id=&action_code=&limit=",
        ],
        "finance_db": [
            "GET  /db/statement_transactions?shop_id=&statement_id=&order_id=&type=&limit=",
        ],
        "analytics_sync": [
            "GET  /v1/analytics/sync/cursor?sellerId=&advertiserId=&storageKey=&campaignId=&pageSize=",
            "POST /v1/analytics/sync/batches         (Bearer token, per-seller scope, idempotent)",
        ],
        "oauth_redirect": [
            "GET  /ads-monitor                       (TikTok OAuth Advertiser redirect target; PUBLIC, no auth)",
        ],
        "analytics_sync_auth_notes": {
            "token_table": "api_keys (unified with tts-erp; sync tokens have role=readwrite)",
            "scope_check": "per-seller via api_keys.scopes[]; 403 SCOPE_DENIED on mismatch",
            "401_when": "missing or invalid Bearer token",
            "403_when": "token valid but sellerId/advertiserId not in scopes[]",
            "no_admin_required": "readwrite is sufficient; admin is NOT required",
        },
    }


# ─── OAuth-receiver passthrough REMOVED (Wave 3 Slice 2) ─────────────
# The /shops, /shops/<id>, /token/<id> proxy routes that
# round-tripped through http://127.0.0.1:9876 are gone. Shop listings
# and per-shop token access are now in-process:
#   * LocalTokenProvider.get(shop_id)   → oauth_receiver_core.db_load_token
#   * listing shops                    → oauth_receiver_core.db_list_shops
# The /callback, /authorize, /healthz routes live in oauth_receiver_router
# and are mounted at the bottom of this file.


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
        "orders": "orders",
        "payments": "payments",
        "statements": "statements",
        "returns": "returns",
        "cancellations": "cancellations",
        "statement_transactions": "statement_transactions",
    }
    repo = _repos[repo_map[sync_type]]

    result = business_fn(creds, body, http=http, repo=repo)
    if not result.ok:
        _log_sync(shop_id, log_type, "error", error=result.error)
        raise HTTPException(status_code=502, detail=result.error)
    _log_sync(shop_id, log_type, "ok", rows=result.saved)
    return {
        "shop_id": shop_id,
        "saved": result.saved,
        "total": result.total,
        "pages": result.pages,
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
    return _run_sync(
        "cancellations", "cancellations", tts_business.sync_cancellations, body
    )


@app.post("/sync/statement_transactions")
def sync_statement_transactions(body: dict):
    return _run_sync(
        "statement_transactions",
        "statement_transactions",
        tts_business.sync_statement_transactions,
        body,
    )


# Sync order detail — not yet ported; keep returning 501
@app.post("/sync/order/{order_id}")
def sync_one_order(order_id: str, body: dict):
    raise HTTPException(
        status_code=501, detail="sync single order not yet ported to FastAPI"
    )


# ─── DB read endpoints (Phase 4a-3 expansion) ─────────────────────────


def _isoformat_timestamps(
    row: dict, keys: tuple[str, ...] = ("synced_at", "updated_at")
):
    for k in keys:
        if row.get(k) and hasattr(row[k], "isoformat"):
            row[k] = row[k].isoformat()
    return row


def _db_query_dict(sql: str, args: tuple = ()) -> list[dict]:
    """Run a SELECT, return list of dict rows.

    Note: psycopg3 wants LiteralString/SQL/Composed for the query param to
    avoid type narrowing; we deliberately keep raw str here because callers
    build parameterized SQL with placeholders (no string interpolation of
    user input) and we want one consistent code path.
    """
    from psycopg.rows import dict_row

    with tts_erp.db_connect() as conn, conn.cursor(row_factory=dict_row) as cur:  # type: ignore[call-arg]
        cur.execute(sql, args)  # type: ignore[arg-type]
        return list(cur.fetchall())


def _decode_cursor(cursor: str | None) -> tuple[int | None, str | None]:
    """Decode opaque pagination cursor. Returns (create_time, order_id).

    Cursor is base64(json({"t": epoch, "i": order_id})). Used as the lower
    bound for the keyset pagination: rows with (create_time, order_id) <
    (t, i) come first. Encoding strips `=` padding; we restore it here.
    """
    if not cursor:
        return None, None
    try:
        # restore padding stripped by _encode_cursor
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return int(payload["t"]), str(payload["i"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid cursor: {e}") from e


def _encode_cursor(create_time: int | None, order_id: str | None) -> str | None:
    if create_time is None or order_id is None:
        return None
    return (
        base64.urlsafe_b64encode(
            json.dumps(
                {"t": int(create_time), "i": str(order_id)}, separators=(",", ":")
            ).encode()
        )
        .decode()
        .rstrip("=")
    )


# Time fields to ISO-format on /db/orders and /db/returns responses.
_ORDERS_TIME_FIELDS = (
    "create_time",
    "update_time",
    "paid_time",
    "shipped_time",
    "delivered_time",
    "cancelled_time",
)
_RETS_TIME_FIELDS = ("create_time", "update_time")


def _isoify_times(row: dict, fields: tuple[str, ...]) -> None:
    for k in fields:
        v = row.get(k)
        if v is None:
            continue
        try:
            row[f"{k}_iso"] = datetime.fromtimestamp(
                int(v), tz=timezone.utc
            ).isoformat()
        except (ValueError, TypeError, OSError):
            row[f"{k}_iso"] = None


@app.get("/db/orders")
def db_list_orders(
    shop_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    create_time_ge: int | None = None,
    create_time_lt: int | None = None,
    paid_time_ge: int | None = None,
    paid_time_lt: int | None = None,
    shipped_time_ge: int | None = None,
    shipped_time_lt: int | None = None,
    delivered_time_ge: int | None = None,
    delivered_time_lt: int | None = None,
    cancelled_time_ge: int | None = None,
    cancelled_time_lt: int | None = None,
    cursor: str | None = None,
):
    """GET /db/orders?shop_id=&status=&create_time_ge=&...&cursor=&limit=

    All time filters are epoch seconds (bigint). Keyset pagination via
    opaque base64 cursor that encodes (create_time, order_id) of the last
    row of the previous page. Returns {count, next_cursor, items}.
    """
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be 1..500")

    sql = (
        "SELECT order_id, shop_id, order_status_name, payment_amount, payment_currency, "
        "total_amount, buyer_email, "
        "create_time, update_time, paid_time, shipped_time, delivered_time, cancelled_time, "
        "fulfillment_type, synced_at, updated_at "
        "FROM orders"
    )
    wh: list[str] = []
    args: list = []
    if shop_id:
        wh.append("shop_id = %s")
        args.append(shop_id)
    if status is not None:
        wh.append("order_status_name = %s")
        args.append(status)
    # Time range filters
    for col, lo, hi in (
        ("create_time", create_time_ge, create_time_lt),
        ("paid_time", paid_time_ge, paid_time_lt),
        ("shipped_time", shipped_time_ge, shipped_time_lt),
        ("delivered_time", delivered_time_ge, delivered_time_lt),
        ("cancelled_time", cancelled_time_ge, cancelled_time_lt),
    ):
        if lo is not None:
            wh.append(f"{col} >= %s")
            args.append(_safe_int(lo, default=0, source="qs." + field))
        if hi is not None:
            wh.append(f"{col} < %s")
            args.append(_safe_int(hi, default=0, source="qs." + field))
    # Keyset cursor: rows strictly older than the cursor point
    c_ct, c_oid = _decode_cursor(cursor)
    if c_ct is not None and c_oid is not None:
        wh.append("(create_time, order_id) < (%s, %s)")
        args.append(c_ct)
        args.append(c_oid)

    if wh:
        sql += " WHERE " + " AND ".join(wh)
    sql += " ORDER BY create_time DESC, order_id DESC LIMIT %s"
    args.append(limit)

    rows = _db_query_dict(sql, tuple(args))
    for r in rows:
        _isoify_times(r, _ORDERS_TIME_FIELDS)
        _isoformat_timestamps(r)  # synced_at / updated_at already TIMESTAMPTZ

    next_cursor = None
    if len(rows) == limit:
        last = rows[-1]
        next_cursor = _encode_cursor(last.get("create_time"), last.get("order_id"))

    return {"count": len(rows), "next_cursor": next_cursor, "items": rows}


@app.get("/db/orders/{order_id}")
def db_get_order(order_id: str):
    rows = _db_query_dict("SELECT * FROM orders WHERE order_id = %s", (order_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"order {order_id} not in local DB")
    _isoify_times(rows[0], _ORDERS_TIME_FIELDS)
    _isoformat_timestamps(rows[0])
    return rows[0]


@app.get("/db/orders/{order_id}/items")
def db_get_order_items(order_id: str):
    rows = _db_query_dict("SELECT * FROM order_items WHERE order_id = %s", (order_id,))
    return {"count": len(rows), "items": rows}


@app.get("/db/orders/{order_id}/shipping")
def db_get_order_shipping(order_id: str):
    rows = _db_query_dict(
        "SELECT * FROM order_shippings WHERE order_id = %s", (order_id,)
    )
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
def db_list_payments(
    shop_id: str | None = None, status: str | None = None, limit: int = 50
):
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
def db_list_returns(
    shop_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    create_time_ge: int | None = None,
    create_time_lt: int | None = None,
    update_time_ge: int | None = None,
    update_time_lt: int | None = None,
    cursor: str | None = None,
):
    """GET /db/returns?shop_id=&status=&create_time_ge=&...&cursor=&limit=

    Returns include the computed `refund_amount` derived from
    raw->'refund_amount'->>'refund_total' when available (TikTok 202309 spec
    nests refund_total inside the top-level refund_amount object; 2026-08-20
    fix). Pagination via opaque cursor over (create_time, return_id).
    """
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be 1..500")
    sql = (
        "SELECT return_id, order_id, shop_id, return_status, return_type, return_reason, "
        "create_time, update_time, synced_at, "
        "(raw->'refund_amount'->>'refund_total')::numeric AS refund_amount, "
        "raw->'refund_amount'->>'currency' AS refund_currency "
        "FROM returns"
    )
    wh: list[str] = []
    args: list = []
    if shop_id:
        wh.append("shop_id = %s")
        args.append(shop_id)
    if status is not None:
        wh.append("return_status = %s")
        args.append(status)
    if create_time_ge is not None:
        wh.append("create_time >= %s")
        args.append(_safe_int(create_time_ge, source="qs.create_time_ge"))
    if create_time_lt is not None:
        wh.append("create_time < %s")
        args.append(_safe_int(create_time_lt, source="qs.create_time_lt"))
    if update_time_ge is not None:
        wh.append("update_time >= %s")
        args.append(_safe_int(update_time_ge, source="qs.update_time_ge"))
    if update_time_lt is not None:
        wh.append("update_time < %s")
        args.append(_safe_int(update_time_lt, source="qs.update_time_lt"))
    # Cursor: returns use return_id as tiebreaker (since the column is not
    # nullable); keyset (create_time, return_id) keyset paging.
    if cursor:
        c_ct, c_rid = _decode_cursor(cursor)
        if c_ct is not None and c_rid is not None:
            wh.append("(create_time, return_id) < (%s, %s)")
            args.append(c_ct)
            args.append(c_rid)
    if wh:
        sql += " WHERE " + " AND ".join(wh)
    sql += " ORDER BY create_time DESC, return_id DESC LIMIT %s"
    args.append(limit)
    rows = _db_query_dict(sql, tuple(args))
    for r in rows:
        _isoify_times(r, _RETS_TIME_FIELDS)
        _isoformat_timestamps(r)

    next_cursor = None
    if len(rows) == limit:
        last = rows[-1]
        next_cursor = _encode_cursor(last.get("create_time"), last.get("return_id"))
    return {"count": len(rows), "next_cursor": next_cursor, "items": rows}


@app.get("/db/returns/{return_id}")
def db_get_return(return_id: str, include_raw: bool = True):
    """GET /db/returns/{return_id}?include_raw=false

    Return detail. Default includes the raw JSON (heavy); set include_raw=false
    for a leaner payload. Computed refund_amount from
    raw->'refund_amount'->>'refund_total' (TikTok 202309 spec).
    """
    rows = _db_query_dict(
        """
        SELECT return_id, order_id, shop_id, return_status, return_type, return_reason,
               create_time, update_time, synced_at,
               (raw->'refund_amount'->>'refund_total')::numeric AS refund_amount,
               raw->'refund_amount'->>'currency' AS refund_currency,
               raw
        FROM returns WHERE return_id = %s
        """,
        (return_id,),
    )
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"return {return_id} not in local DB"
        )
    row = rows[0]
    if not include_raw:
        row.pop("raw", None)
    _isoify_times(row, _RETS_TIME_FIELDS)
    _isoformat_timestamps(row)
    return row


@app.get("/db/cancellations")
def db_list_cancellations(
    shop_id: str | None = None, status: str | None = None, limit: int = 50
):
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


# ─── Logistics tracking (Phase 5) ────────────────────────────────────
#
# Confirmed 2026-08-16:
#   GET /fulfillment/202309/orders/{order_id}/tracking
#     → {code, message, data: {tracking: [{action_code, description, update_time_millis}, ...]}}
#   All 6 order statuses return code=0 with a non-empty tracking list.
#
# /logistics/202604/... returns 11007009 on this app (module not yet open for our scope).

LOGISTICS_UPSTREAM_PATH = "/fulfillment/202309/orders/{order_id}/tracking"


@app.get("/logistics/orders/{order_id}/tracking")
def logistics_tracking(order_id: str, shop_id: str):
    """GET /logistics/orders/{order_id}/tracking?shop_id=... — proxy + auto-persist."""
    creds = _get_creds(shop_id)
    http = _tiktok_http_for(creds)
    upstream_path = LOGISTICS_UPSTREAM_PATH.format(order_id=order_id)
    result = http.request(
        "GET",
        upstream_path,
        body=None,
        extra_params={"shop_cipher": creds.shop_cipher},
        timeout=TTS_ERP_HTTP_TIMEOUT,
    )
    if isinstance(result, dict) and result.get("code") == 0:
        # Persist via the shared helper in tts_erp
        tts_erp.persist_logistics_tracking(shop_id, order_id, result)
        # Backfill tracking_number from order_shippings
        try:
            rows = _db_query_dict(
                "SELECT tracking_number FROM order_shippings WHERE order_id = %s",
                (order_id,),
            )
            if rows and rows[0].get("tracking_number"):
                tts_erp.persist_logistics_tracking_number(
                    order_id, rows[0]["tracking_number"]
                )
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(
                f"[tts-erp-fastapi] backfill tracking_number failed: {e}\n"
            )
    if isinstance(result, dict) and result.get("code") != 0:
        raise HTTPException(status_code=502, detail=result)
    return result


@app.post("/sync/logistics_tracking")
def sync_logistics_tracking(body: dict):
    """POST /sync/logistics_tracking
    body: {shop_id, order_ids?: [...], all_with_tracking?: bool, limit?, max_per_run?}
    """
    shop_id = body.get("shop_id")
    if not shop_id:
        raise HTTPException(status_code=400, detail="missing shop_id in body")
    creds = _get_creds(shop_id)
    http = _tiktok_http_for(creds)

    # Resolve target order_ids
    order_ids: list[str] = []
    if body.get("order_ids"):
        order_ids = [str(x) for x in body["order_ids"] if x]
    elif body.get("all_with_tracking"):
        lim = _safe_int(body.get("limit"), default=1000, source="body.limit")
        rows = _db_query_dict(
            """
            SELECT DISTINCT s.order_id
            FROM order_shippings s
            WHERE s.shop_id = %s
              AND s.tracking_number IS NOT NULL
              AND s.tracking_number <> ''
            LIMIT %s
            """,
            (shop_id, lim),
        )
        order_ids = [r["order_id"] for r in rows]
    else:
        lim = _safe_int(body.get("limit"), default=200, source="body.limit")
        rows = _db_query_dict(
            """
            SELECT order_id FROM logistics_sync_targets
            WHERE shop_id = %s AND needs_resync = true
            ORDER BY order_id
            LIMIT %s
            """,
            (shop_id, lim),
        )
        order_ids = [r["order_id"] for r in rows]

    max_per_run = _safe_int(body.get("max_per_run"), default=(len(order_ids) or 100), source="body.max_per_run")
    order_ids = order_ids[:max_per_run]

    if not order_ids:
        return {"shop_id": shop_id, "saved": 0, "total": 0, "errors": []}

    saved = 0
    errors: list[dict] = []
    for oid in order_ids:
        upstream_path = LOGISTICS_UPSTREAM_PATH.format(order_id=oid)
        r = http.request(
            "GET",
            upstream_path,
            body=None,
            extra_params={"shop_cipher": creds.shop_cipher},
            timeout=TTS_ERP_HTTP_TIMEOUT,
        )
        if isinstance(r, dict) and r.get("code") == 0:
            if tts_erp.persist_logistics_tracking(shop_id, oid, r):
                saved += 1
            # Backfill tracking_number
            try:
                rows = _db_query_dict(
                    "SELECT tracking_number FROM order_shippings WHERE order_id = %s",
                    (oid,),
                )
                if rows and rows[0].get("tracking_number"):
                    tts_erp.persist_logistics_tracking_number(
                        oid, rows[0]["tracking_number"]
                    )
            except Exception:  # noqa: BLE001
                pass
        else:
            errors.append(
                {"order_id": oid, "code": r.get("code"), "message": r.get("message")}
            )

    err = None if not errors else f"{len(errors)} order(s) failed"
    _log_sync(
        shop_id,
        "logistics_tracking",
        "ok" if not errors else "partial",
        rows=saved,
        error=err,
    )
    return {
        "shop_id": shop_id,
        "saved": saved,
        "total": len(order_ids),
        "errors": errors,
    }


@app.get("/db/logistics_tracking")
def db_list_logistics_tracking(
    shop_id: str | None = None,
    final_status: str | None = None,
    arrived_overseas: bool | None = None,
    tracking_number: str | None = None,
    order_id: str | None = None,
    limit: int = 100,
):
    """GET /db/logistics_tracking?shop_id=...&final_status=...&arrived_overseas=true&limit=100"""
    wh = []
    args: list = []
    if shop_id:
        wh.append("shop_id = %s")
        args.append(shop_id)  # noqa: E702
    if final_status:
        wh.append("final_status = %s")
        args.append(final_status)  # noqa: E702
    if arrived_overseas is not None:
        wh.append("arrived_overseas = %s")
        args.append(arrived_overseas)
    if tracking_number:
        wh.append("tracking_number = %s")
        args.append(tracking_number)
    if order_id:
        wh.append("order_id = %s")
        args.append(order_id)
    sql = "SELECT * FROM logistics_tracking"
    if wh:
        sql += " WHERE " + " AND ".join(wh)
    sql += " ORDER BY last_event_at DESC NULLS LAST LIMIT %s"
    args.append(limit)
    rows = _db_query_dict(sql, tuple(args))
    for r in rows:
        for k, v in list(r.items()):
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return {"count": len(rows), "items": rows}


@app.get("/db/logistics_events")
def db_list_logistics_events(
    order_id: str | None = None,
    action_code: int | None = None,
    limit: int = 200,
):
    """GET /db/logistics_events?order_id=...&action_code=...&limit=200"""
    from datetime import datetime, timezone

    wh = []
    args: list = []
    if order_id:
        wh.append("order_id = %s")
        args.append(order_id)  # noqa: E702
    if action_code is not None:
        wh.append("action_code = %s")
        args.append(action_code)
    sql = "SELECT order_id, action_code, event_time, location, description FROM logistics_tracking_events"
    if wh:
        sql += " WHERE " + " AND ".join(wh)
    sql += " ORDER BY event_time DESC LIMIT %s"
    args.append(limit)
    rows = _db_query_dict(sql, tuple(args))
    for r in rows:
        if r.get("event_time"):
            r["event_time_iso"] = datetime.fromtimestamp(
                _safe_int(r["event_time"], default=0, source="db.event_time") / 1000, tz=timezone.utc
            ).isoformat()
    return {"count": len(rows), "items": rows}


@app.get("/db/sync_log")
def db_sync_log(limit: int = 50):
    """Return last N sync log entries from in-memory deque."""
    return {"items": list(tts_erp._last_syncs)[-limit:]}


@app.get("/db/statement_transactions")
def db_statement_transactions(
    shop_id: str | None = None,
    statement_id: str | None = None,
    order_id: str | None = None,
    type: str | None = None,
    limit: int = 100,
):
    """GET /db/statement_transactions?shop_id=&statement_id=&order_id=&type=&limit=

    账单逐交易明细（接口来源，替代 Excel financial_lines + fee_lines）。
    """
    wh = []
    args: list = []
    if shop_id:
        wh.append("shop_id = %s")
        args.append(shop_id)  # noqa: E702
    if statement_id:
        wh.append("statement_id = %s")
        args.append(statement_id)
    if order_id:
        wh.append("order_id = %s")
        args.append(order_id)
    if type:
        wh.append("type = %s")
        args.append(type)
    sql = "SELECT * FROM statement_transactions"
    if wh:
        sql += " WHERE " + " AND ".join(wh)
    sql += " ORDER BY order_create_time DESC NULLS LAST, txn_id LIMIT %s"
    args.append(limit)
    rows = _db_query_dict(sql, tuple(args))
    for r in rows:
        _isoformat_timestamps(r)
        r.pop("raw", None)
    return {"count": len(rows), "items": rows}


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
        method,
        upstream_path,
        body=body if method != "GET" else None,
        extra_params=forwarded,
        timeout=TTS_ERP_HTTP_TIMEOUT,
    )

    if (
        persist_order_on_get
        and method == "GET"
        and result.get("code") == 0
        and result.get("data")
    ):
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
    return _tiktok_proxy(
        "POST", "/order/202309/orders/search", shop_id=shop_id, body=body
    )


@app.post("/orders/list")
def orders_list(shop_id: str, body: dict):
    return _tiktok_proxy(
        "POST", "/order/202309/orders/search", shop_id=shop_id, body=body
    )


@app.get("/orders/{order_id}")
def order_detail(order_id: str, shop_id: str):
    # 202309: /orders?ids=<id> (not /orders/{id})
    return _tiktok_proxy(
        "GET",
        "/order/202309/orders",
        shop_id=shop_id,
        extra_query={"ids": order_id},
        persist_order_on_get=True,
    )


@app.post("/orders/{order_id}/confirm")
def order_confirm(order_id: str, shop_id: str, body: dict | None = None):
    body = body or {}
    return _tiktok_proxy(
        "POST", f"/order/202309/orders/{order_id}/confirm", shop_id=shop_id, body=body
    )


@app.post("/orders/{order_id}/cancel")
def order_cancel(order_id: str, shop_id: str, body: dict | None = None):
    body = body or {}
    return _tiktok_proxy(
        "POST", f"/order/202309/orders/{order_id}/cancel", shop_id=shop_id, body=body
    )


@app.post("/orders/{order_id}/update_status")
def order_update_status(order_id: str, shop_id: str, body: dict | None = None):
    body = body or {}
    return _tiktok_proxy(
        "POST",
        f"/order/202309/orders/{order_id}/update_status",
        shop_id=shop_id,
        body=body,
    )


@app.post("/orders/{order_id}/shipping_info")
def order_shipping_info(order_id: str, shop_id: str, body: dict | None = None):
    body = body or {}
    return _tiktok_proxy(
        "POST",
        f"/order/202309/orders/{order_id}/shipping_info",
        shop_id=shop_id,
        body=body,
    )


@app.post("/orders/{order_id}/verify_shipping")
def order_verify_shipping(order_id: str, shop_id: str, body: dict | None = None):
    body = body or {}
    return _tiktok_proxy(
        "POST",
        f"/order/202309/orders/{order_id}/verify_shipping",
        shop_id=shop_id,
        body=body,
    )


@app.get("/orders/{order_id}/tracking")
def order_tracking(order_id: str, shop_id: str):
    return _tiktok_proxy(
        "GET", f"/order/202309/orders/{order_id}/tracking", shop_id=shop_id
    )


@app.get("/orders/{order_id}/tracking/get")
def order_tracking_get(order_id: str, shop_id: str):
    return _tiktok_proxy(
        "GET", f"/order/202309/orders/{order_id}/tracking", shop_id=shop_id
    )


@app.get("/orders/{order_id}/risk")
def order_risk(order_id: str, shop_id: str):
    return _tiktok_proxy(
        "GET", f"/order/202309/orders/{order_id}/risk", shop_id=shop_id
    )


@app.get("/orders/{order_id}/buyer")
def order_buyer(order_id: str, shop_id: str):
    return _tiktok_proxy(
        "GET", f"/order/202309/orders/{order_id}/buyer", shop_id=shop_id
    )


@app.get("/orders/{order_id}/recipient")
def order_recipient(order_id: str, shop_id: str):
    return _tiktok_proxy(
        "GET", f"/order/202309/orders/{order_id}/recipient", shop_id=shop_id
    )


# Finance endpoints ────────────────────────────────────────────────────


@app.get("/finance/statements")
def finance_statements(
    shop_id: str,
    page_size: int = 50,
    sort_field: str = "statement_time",
    sort_order: str = "DESC",
):
    return _tiktok_proxy(
        "GET",
        "/finance/202309/statements",
        shop_id=shop_id,
        extra_query={
            "page_size": str(page_size),
            "sort_field": sort_field,
            "sort_order": sort_order,
        },
    )


@app.get("/finance/payments")
def finance_payments(
    shop_id: str,
    page_size: int = 50,
    sort_field: str = "create_time",
    sort_order: str = "DESC",
):
    return _tiktok_proxy(
        "GET",
        "/finance/202309/payments",
        shop_id=shop_id,
        extra_query={
            "page_size": str(page_size),
            "sort_field": sort_field,
            "sort_order": sort_order,
        },
    )


# Return / Cancellation search proxy ───────────────────────────────────


@app.post("/returns/search")
def returns_search(shop_id: str, body: dict | None = None):
    body = body or {}
    return _tiktok_proxy(
        "POST", "/return_refund/202309/returns/search", shop_id=shop_id, body=body
    )


@app.post("/cancellations/search")
def cancellations_search(shop_id: str, body: dict | None = None):
    body = body or {}
    return _tiktok_proxy(
        "POST", "/return_refund/202309/cancellations/search", shop_id=shop_id, body=body
    )


# ─── miaoshou (万师傅 / 妙手) 开放平台代理 ─────────────────────────────────────
# 2026-08-24: 从 legacy tts_erp.py 迁移过来。
# 路由：
#   POST /miaoshou/{domain}/{method}      → MiaoshouClient.<domain>.<method>(**body)
#   POST /miaoshou/callback/{node-alias}  → NODE_REGISTRY 查找 + dispatch_callback
#   POST /miaoshou/callback/all           → 按 body.orderStatus 字段自动派发
# Auth: admin（middleware 已在 required_role 里标好 /miaoshou/* = admin）
import urllib.error as _urllib_error  # noqa: E402  -- miaoshou SDK 同款依赖

from miaoshou import MiaoshouApiError, MiaoshouClient  # noqa: E402
from miaoshou.callbacks.router import (  # noqa: E402
    NODE_REGISTRY,
    dispatch_callback,
)

# MiaoshouClient 上挂的 12 个 domain（与 legacy _MIAOSHOU_DOMAINS 一致；
# collection_box / tk_collect_box 属于 MiaoshouErpClient，不在此处）
_MIAOSHOU_DOMAINS: frozenset[str] = frozenset(
    {
        "orders",
        "fees",
        "refunds",
        "arbitrations",
        "closes",
        "complaints",
        "queries",
        "accounts",
        "products",
        "logistics",
        "aftersales",
        "tests",
    }
)

_miaoshou_client_cache: dict[str, MiaoshouClient] = {}


def _miaoshou_client() -> MiaoshouClient:
    """懒创建 MiaoshouClient（按 license_id+env 缓存，避免重复构造）。"""
    cache_key = (
        f"{os.environ.get('MIAOSHOU_LICENSE_ID', '')}"
        f"|{os.environ.get('MIAOSHOU_ENV', 'test')}"
    )
    client = _miaoshou_client_cache.get(cache_key)
    if client is None:
        client = MiaoshouClient.from_env()
        _miaoshou_client_cache[cache_key] = client
    return client


def _miaoshou_outbound_call(domain: str, method: str, body: dict) -> tuple[int, dict]:
    """核心出站调度: domain.method(**body) → (http_status, response_body).

    返回壳 ``{code, message, data, _error?}``：
    - 200: 业务成功，透传 SDK 返回值
    - 400: body 字段名错（TypeError）
    - 404: 未知 domain 或 method
    - 502: 上游 MiaoshouApiError / 网络错误
    - 500: 未预期异常
    """
    if domain not in _MIAOSHOU_DOMAINS:
        return 404, {
            "_error": f"unknown miaoshou domain: {domain}",
            "supported_domains": sorted(_MIAOSHOU_DOMAINS),
        }
    try:
        client = _miaoshou_client()
        ep = getattr(client, domain)
        fn = getattr(ep, method, None)
        if fn is None or not callable(fn):
            return 404, {
                "_error": f"unknown method: {domain}.{method}",
                "supported_methods": [m for m in dir(ep) if not m.startswith("_")],
            }
        resp: MiaoshouApiResponse = fn(**body) if body else fn()  # type: ignore[assignment]
    except TypeError as e:
        return 400, {
            "_error": f"参数错误 ({domain}.{method}): {e}",
            "hint": "body 字段名需匹配 SDK 方法签名",
        }
    except MiaoshouApiError as e:
        return 502, {
            "code": e.code,
            "message": e.message,
            "data": e.data,
            "_error": f"miaoshou {domain}.{method} returned non-200",
        }
    except _urllib_error.URLError as e:
        return 502, {
            "_error": f"miaoshou {domain}.{method} 网络错误: {e.reason}",
        }
    except Exception as e:  # noqa: BLE001
        log.error(f"[tts-erp] miaoshou {domain}.{method} 异常: {e}\n")
        return 500, {"_error": str(e)}
    return 200, {
        "code": resp.code,
        "message": resp.message,
        "data": resp.data,
    }


@app.post("/miaoshou/callback/all")
def miaoshou_callback_all(body: dict | None = None):
    """POST /miaoshou/callback/all → 按 body.orderStatus 字段自动派发。

    ⚠️ 必须在 /miaoshou/{domain}/{method} 之前注册：
    FastAPI 按注册顺序匹配路由，否则 outbound 路由会先吃
    路径参数 (domain="callback", method="all") 然后返 "unknown domain"。
    """
    body = body or {}
    order_status = str(body.get("orderStatus", ""))
    status_code, payload = dispatch_callback(order_status, body)
    return JSONResponse(status_code=status_code, content=payload)


@app.post("/miaoshou/callback/{node_alias}")
def miaoshou_callback_node(node_alias: str, body: dict | None = None):
    """POST /miaoshou/callback/<node-alias> → NODE_REGISTRY 查 orderStatus → dispatch_callback.

    节点别名从 NODE_REGISTRY 查找（如 service-node / rush-order 等）；
    未匹配返回 404 + supported 列表。

    ⚠️ 必须在 /miaoshou/{domain}/{method} 之前注册（同 callback/all 注释）。
    """
    body = body or {}
    target = next(
        (
            order_status
            for order_status, (_, alias) in NODE_REGISTRY.items()
            if alias == node_alias
        ),
        None,
    )
    if target is None:
        return JSONResponse(
            status_code=404,
            content={
                "_error": f"unknown miaoshou callback node: {node_alias}",
                "supported": [alias for _, alias in NODE_REGISTRY.values()],
            },
        )
    raw = dict(body)
    raw["orderStatus"] = target  # 让 dispatch_callback 拿到正确的 status
    status_code, payload = dispatch_callback(target, raw)
    return JSONResponse(status_code=status_code, content=payload)


@app.post("/miaoshou/{domain}/{method}")
def miaoshou_outbound(domain: str, method: str, body: dict | None = None):
    """POST /miaoshou/<domain>/<method> → MiaoshouClient.<domain>.<method>(**body).

    ⚠️ 必须注册在 /miaoshou/callback/* 之后（见 callback 路由注释），
    否则会被该路由先吃。
    """
    status_code, payload = _miaoshou_outbound_call(domain, method, body or {})
    return JSONResponse(status_code=status_code, content=payload)


# ─── MiaoshouErpClient sync + DB reads（从 tts_erp.py legacy handler 迁移）──────────
# 2026-08-24: 补 2026-08-17 批次承诺但未实现的 3 个 sync 路由（price templates、
# collect box details、move collect tasks）+ GET /db/miaoshou_shops。
# 实现复用 legacy tts_erp.Handler._sync_miaoshou_* / _db_list_miaoshou_*。
# Auth: readwrite（middleware 已在 tdd/auth.py 把 /sync/* / /db/* 标 readwrite）。
def _invoke_legacy_sync(method_name: str, params: dict) -> dict:
    """调用 legacy tts_erp.Handler 上的 sync/db 方法，返回 (status_code, body).

    Args:
        method_name: Handler 上的方法名（如 '_sync_miaoshou_price_templates'）
        params: query dict（与 legacy Handler 期望的格式一致：scalar str）
    """
    captured: list[tuple[int, dict]] = []

    class _StubHandler:
        def _send(self, code, obj):
            captured.append((code, obj))

    handler = _StubHandler()
    unbound = tts_erp.Handler.__dict__[method_name]
    unbound(handler, params)
    if not captured:
        return {"_error": f"{method_name} did not return any response"}
    code, body = captured[0]
    return JSONResponse(status_code=code, content=body)


@app.post("/sync/miaoshou_shops")
def sync_miaoshou_shops(
    platform: str = "tiktok",
    site: str = "VN",
    page_no: str = "1",
    page_size: str = "100",
):
    return _invoke_legacy_sync(
        "_sync_miaoshou_shops",
        {
            "platform": platform,
            "site": site,
            "page_no": page_no,
            "page_size": page_size,
        },
    )


@app.post("/sync/miaoshou_price_templates")
def sync_miaoshou_price_templates(
    platform: str = "tiktok",
    site: str = "",
    page_size: str = "20",
):
    return _invoke_legacy_sync(
        "_sync_miaoshou_price_templates",
        {
            "platform": platform,
            "site": site,
            "page_size": page_size,
        },
    )


@app.post("/sync/miaoshou_collect_box_details")
def sync_miaoshou_collect_box_details(
    platform: str = "tiktok",
    page_size: str = "50",
    status: str = "",
):
    return _invoke_legacy_sync(
        "_sync_miaoshou_collect_box_details",
        {
            "platform": platform,
            "page_size": page_size,
            "status": status,
        },
    )


@app.post("/sync/miaoshou_move_collect_tasks")
def sync_miaoshou_move_collect_tasks(
    platform: str = "tiktok",
    page_size: str = "20",
    status: str = "",
):
    return _invoke_legacy_sync(
        "_sync_miaoshou_move_collect_tasks",
        {
            "platform": platform,
            "page_size": page_size,
            "status": status,
        },
    )


@app.get("/db/miaoshou_shops")
def db_miaoshou_shops(
    platform: str = "",
    site: str = "",
    limit: str = "100",
):
    return _invoke_legacy_sync(
        "_db_list_miaoshou_shops",
        {
            "platform": platform,
            "site": site,
            "limit": limit,
        },
    )


# ─── OpenAPI: Bearer auth scheme ──────────────────────────────────────
# 2026-08-23: AuthMiddleware enforces Bearer at request time, but the
# OpenAPI spec was missing the securitySchemes declaration so API
# consumers (Postman, codegen, etc.) couldn't see auth requirements.
# Add it globally so /openapi.json documents both tts-erp and the
# analytics_sync sub-router as requiring Bearer.

_bearer_scheme = HTTPBearer(
    bearerFormat="ttserp_<role>_<32-char urlsafe>",
    auto_error=False,
    description="API key issued via `python3 api_keys.py create --role readwrite`",
)
_original_openapi = app.openapi


def _openapi_with_bearer():
    schema = _original_openapi()
    components = schema.setdefault("components", {})
    schemes = components.setdefault("securitySchemes", {})
    schemes["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "ttserp_<role>_<32-char urlsafe>",
        "description": "API key issued via `python3 api_keys.py create`",
    }
    # Apply globally so every endpoint (including the analytics_sync
    # sub-router at /v1/analytics/sync/*) declares auth. AuthMiddleware
    # enforces at runtime; this is purely the OpenAPI declaration.
    schema["security"] = [{"BearerAuth": []}]
    return schema


app.openapi = _openapi_with_bearer
