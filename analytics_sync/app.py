"""FastAPI application for analytics_sync.

Endpoints (per protocol):
    GET  /v1/analytics/sync/cursor
    POST /v1/analytics/sync/batches

Plus ops endpoints (auth-exempt):
    GET  /healthz
    GET  /endpoints

Run:
    uvicorn analytics_sync.app:app --host 0.0.0.0 --port 9878

Middleware order (FastAPI wraps in reverse; add = innermost-first):

    1. RateLimitMiddleware  (innermost — bucket by token prefix)
    2. AuthMiddleware       (sets sync_token_prefix/scope/sync_token_scopes)
    3. CORS                 (outermost — preflight short-circuit)

Scope validation: token's scopes[] is read off `scope["sync_token_scopes"]`
by handler-level checks. We do NOT pass scopes through URL params (per
protocol §3 — only sync-token Bearer).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

# Load .env from the repo root (works both in worktree and main checkout).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_env_path = _REPO_ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from .auth import AuthMiddleware, scope_grants
from .domain import (
    Record,
    Scope,
    StorageKey,
    compute_idempotency_key,
)
from .pg_repositories import (
    PgAnalyticsRepository,
    fetch_cursor_page,
    fetch_timezone,
    write_audit,
)
from .rate_limit import RateLimitMiddleware


# ─── Config ────────────────────────────────────────────────────────────

PROTOCOL_VERSION = 1
PROTOCOL_VERSION_HEADER = "X-Protocol-Version"
DEFAULT_BOOTSTRAP_LOOKBACK_DAYS = 30
DEFAULT_TIMEZONE = "Asia/Shanghai"
MAX_BATCH_RECORDS = 100
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MB per protocol §5
MAX_RESPONSE_DATA_BYTES = 256 * 1024  # cap individual response_data JSON


# ─── App + middleware ─────────────────────────────────────────────────

app = FastAPI(
    title="analytics-sync",
    version="0.2.0",
    description="Analytics data sync backend for the Chrome extension. Replaces the retired CloudBase path.",
)

# Add order = [RateLimit, Auth, CORS] → wrap order ends up
# [Auth outer] → [RateLimit inner] → endpoint. Auth must run first so
# rate limiter can bucket by token prefix. (No CORS for MVP — internal.)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuthMiddleware)


# ─── Ops endpoints ────────────────────────────────────────────────────


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": "analytics-sync", "version": "0.2.0"}


@app.get("/endpoints")
def endpoints() -> dict[str, Any]:
    return {
        "service": "analytics-sync",
        "version": "0.2.0",
        "protocol_version": PROTOCOL_VERSION,
        "endpoints": [
            {
                "method": "GET",
                "path": "/v1/analytics/sync/cursor",
                "description": "Latest persisted day per (scope, storageKey, campaignId).",
                "auth": "Bearer (sync token)",
                "scope": "tokens must grant access to sellerId/advertiserId in query",
            },
            {
                "method": "POST",
                "path": "/v1/analytics/sync/batches",
                "description": "Idempotent batch upload.",
                "auth": "Bearer (sync token)",
                "scope": "tokens must grant access to sellerId/advertiserId in body",
                "limits": {
                    "max_records": MAX_BATCH_RECORDS,
                    "max_body_bytes": MAX_BODY_BYTES,
                },
            },
        ],
        "error_contract": {
            "400": "malformed JSON or schema invalid; retryable=false",
            "401": "missing or invalid bearer token; retryable=false",
            "403": "scope mismatch; retryable=false",
            "413": "request body exceeds 2MB; retryable=false (split the batch)",
            "429": "rate limited; retryable=true; check Retry-After header",
            "5xx": "server error; retryable=true with bounded backoff",
        },
    }


# ─── Models ───────────────────────────────────────────────────────────


class ScopeIn(BaseModel):
    sellerId: str = Field(min_length=1, max_length=128)
    advertiserId: str = Field(min_length=1, max_length=128)
    shopName: str | None = None


class RecordIn(BaseModel):
    idempotencyKey: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    sourceRecordId: str | None = None
    storageKey: StorageKey
    campaignId: str = Field(min_length=1, max_length=128)
    day: date
    page: int = Field(ge=1)
    endpoint: str = Field(min_length=1, max_length=512)
    method: str = Field(min_length=1, max_length=16)
    requestBody: dict[str, Any] | None = None
    response: dict[str, Any]
    source: str = Field(min_length=1, max_length=64)
    capturedAt: datetime
    schemaVersion: int = Field(default=1, ge=1)

    @field_validator("capturedAt")
    @classmethod
    def _captured_at_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("capturedAt must include a timezone (use ISO-8601 with 'Z' or '+00:00')")
        return v


class BatchRequest(BaseModel):
    protocolVersion: int = Field(default=PROTOCOL_VERSION)
    requestId: str | None = Field(default=None, min_length=1, max_length=128)
    scope: ScopeIn
    records: list[RecordIn] = Field(min_length=1, max_length=MAX_BATCH_RECORDS)


# ─── Cursor endpoint ──────────────────────────────────────────────────


@app.get("/v1/analytics/sync/cursor")
def get_cursor(
    request: Request,
    sellerId: str = Query(min_length=1, max_length=128),
    advertiserId: str = Query(min_length=1, max_length=128),
    storageKey: StorageKey | None = Query(default=None),
    campaignId: str | None = Query(default=None, max_length=128),
    cursor: str | None = Query(default=None, max_length=4096),
    pageSize: int = Query(default=50, ge=1, le=100),
) -> JSONResponse:
    """Return the canonical latest-day state for every (storageKey, campaignId)
    pair known for this scope. Returns timezone + nextRequiredDay per row.

    nextRequiredDay is authoritative: if no record exists, the server
    returns a bootstrap date (today - ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS,
    default 30) in the shop's canonical timezone.
    """
    request_id = _request_id_from_headers(request)
    key_prefix = _key_prefix(request)

    # Scope check (skip when auth mode=off; in tests/off-mode scopes=()).
    if not scope_grants(_scopes(request), seller_id=sellerId, advertiser_id=advertiserId):
        write_audit(
            request_id=request_id,
            endpoint="cursor",
            method="GET",
            path=f"/v1/analytics/sync/cursor?sellerId={sellerId}&advertiserId={advertiserId}",
            status=403,
            key_prefix=key_prefix,
            error_code="SCOPE_DENIED",
        )
        return _error_response(
            status=403,
            code="SCOPE_DENIED",
            message="sync token does not grant access to this scope",
            request_id=request_id,
            retryable=False,
        )

    tz_name = fetch_timezone(sellerId)
    today = _today_in_tz(tz_name)
    bootstrap_days = int(
        os.environ.get("ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS", DEFAULT_BOOTSTRAP_LOOKBACK_DAYS)
    )

    entries = fetch_cursor_page(
        seller_id=sellerId,
        advertiser_id=advertiserId,
        storage_key=storageKey,
        campaign_id=campaignId,
        timezone_name=tz_name,
        today_in_shop_tz=today,
        bootstrap_lookback_days=bootstrap_days,
    )

    page = entries[:pageSize]
    next_cursor = _encode_cursor(pageSize, len(entries)) if len(entries) > pageSize else None

    write_audit(
        request_id=request_id,
        endpoint="cursor",
        method="GET",
        path=f"/v1/analytics/sync/cursor?sellerId={sellerId}&advertiserId={advertiserId}",
        status=200,
        key_prefix=key_prefix,
        records_in=len(page),
    )

    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "requestId": request_id,
            "data": {
                "timezone": tz_name,
                "items": [
                    {
                        "storageKey": e.storage_key.value,
                        "campaignId": e.campaign_id,
                        "latestCompletedDay": e.latest_completed_day.isoformat() if e.latest_completed_day else None,
                        "nextRequiredDay": e.next_required_day.isoformat(),
                    }
                    for e in page
                ],
                "nextCursor": next_cursor,
            },
        },
    )


# ─── Batch endpoint ───────────────────────────────────────────────────


@app.post("/v1/analytics/sync/batches")
async def post_batches(request: Request) -> JSONResponse:
    """Idempotent batch upload with per-record outcomes.

    Limits: 100 records max, 2 MB body max. Body size is checked BEFORE
    JSON parsing so a too-large request returns 413 cleanly.
    """
    request_id = _request_id_from_headers(request)
    key_prefix = _key_prefix(request)
    method = "POST"
    path = "/v1/analytics/sync/batches"

    # Content-Length pre-check (fast path).
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > MAX_BODY_BYTES:
                return _audit_and_error(
                    request=request, request_id=request_id, status=413,
                    code="PAYLOAD_TOO_LARGE",
                    message=f"Content-Length {cl} exceeds maximum {MAX_BODY_BYTES} bytes",
                    retryable=False,
                    key_prefix=key_prefix,
                    error_code="PAYLOAD_TOO_LARGE",
                    method=method, path=path,
                )
        except ValueError:
            pass  # malformed content-length → fall through to body read

    body_bytes = await request.body()
    if len(body_bytes) > MAX_BODY_BYTES:
        return _audit_and_error(
            request=request, request_id=request_id, status=413,
            code="PAYLOAD_TOO_LARGE",
            message=f"actual body size {len(body_bytes)} exceeds maximum {MAX_BODY_BYTES} bytes",
            retryable=False,
            key_prefix=key_prefix,
            error_code="PAYLOAD_TOO_LARGE",
            method=method, path=path,
        )

    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError as exc:
        return _audit_and_error(
            request=request, request_id=request_id, status=400,
            code="MALFORMED_JSON",
            message=f"JSON parse error: {exc.msg}",
            retryable=False,
            key_prefix=key_prefix,
            error_code="MALFORMED_JSON",
            method=method, path=path,
        )

    try:
        payload = BatchRequest.model_validate(body)
    except Exception as exc:
        return _audit_and_error(
            request=request, request_id=request_id, status=400,
            code="SCHEMA_INVALID",
            message=str(exc),
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method=method, path=path,
        )

    if payload.protocolVersion != PROTOCOL_VERSION:
        return _audit_and_error(
            request=request, request_id=request_id, status=400,
            code="UNSUPPORTED_PROTOCOL_VERSION",
            message=f"server expects protocolVersion={PROTOCOL_VERSION}, client sent {payload.protocolVersion}",
            retryable=False,
            key_prefix=key_prefix,
            error_code="UNSUPPORTED_PROTOCOL_VERSION",
            method=method, path=path,
        )

    # Per-record validation: idempotency key must match the canonical
    # server-computed value.
    accepted: list[dict[str, str]] = []
    rejected: list[dict[str, Any]] = []
    valid_records: list[Record] = []

    for idx, rec_in in enumerate(payload.records):
        # Cap individual response payloads to avoid one chatty record
        # blowing the table.
        try:
            response_size = len(json.dumps(rec_in.response, ensure_ascii=False).encode())
        except (TypeError, ValueError):
            response_size = 0
        if response_size > MAX_RESPONSE_DATA_BYTES:
            rejected.append(
                {
                    "idempotencyKey": rec_in.idempotencyKey,
                    "code": "RESPONSE_TOO_LARGE",
                    "message": (
                        f"records[{idx}].response is {response_size} bytes; "
                        f"max {MAX_RESPONSE_DATA_BYTES}"
                    ),
                    "retryable": False,
                }
            )
            continue

        canonical_key = compute_idempotency_key(
            seller_id=payload.scope.sellerId,
            advertiser_id=payload.scope.advertiserId,
            storage_key=rec_in.storageKey,
            campaign_id=rec_in.campaignId,
            day=rec_in.day,
            page=rec_in.page,
        )
        if canonical_key != rec_in.idempotencyKey:
            rejected.append(
                {
                    "idempotencyKey": rec_in.idempotencyKey,
                    "code": "SCHEMA_INVALID",
                    "message": (
                        f"idempotencyKey mismatch at records[{idx}]: "
                        f"client={rec_in.idempotencyKey[:16]}… "
                        f"server={canonical_key[:16]}…"
                    ),
                    "retryable": False,
                }
            )
            continue
        valid_records.append(
            Record(
                idempotency_key=rec_in.idempotencyKey,
                source_record_id=rec_in.sourceRecordId,
                storage_key=rec_in.storageKey,
                campaign_id=rec_in.campaignId,
                day=rec_in.day,
                page=rec_in.page,
                endpoint=rec_in.endpoint,
                method=rec_in.method,
                request_body=rec_in.requestBody,
                response=rec_in.response,
                source=rec_in.source,
                captured_at=rec_in.capturedAt,
                schema_version=rec_in.schemaVersion,
            )
        )

    # Scope check now that we know the request body.
    if not scope_grants(
        _scopes(request),
        seller_id=payload.scope.sellerId,
        advertiser_id=payload.scope.advertiserId,
    ):
        write_audit(
            request_id=payload.requestId,
            endpoint="batches",
            method=method,
            path=path,
            status=403,
            key_prefix=key_prefix,
            records_in=len(payload.records),
            records_ok=0,
            records_rej=len(rejected),
            error_code="SCOPE_DENIED",
        )
        return _error_response(
            status=403,
            code="SCOPE_DENIED",
            message="sync token does not grant access to this scope",
            request_id=payload.requestId,
            retryable=False,
        )

    # Persist + advance cursors.
    scope = Scope(
        seller_id=payload.scope.sellerId,
        advertiser_id=payload.scope.advertiserId,
        shop_name=payload.scope.shopName,
    )

    if valid_records:
        try:
            result = PgAnalyticsRepository().upsert_records(
                scope, valid_records, request_id=payload.requestId
            )
        except Exception as exc:
            exc_class = type(exc).__name__
            sys.stderr.write(
                f"[analytics-sync] persistence failure: {exc_class}: {exc}\n"
            )
            write_audit(
                request_id=payload.requestId,
                endpoint="batches",
                method=method,
                path=path,
                status=500,
                key_prefix=key_prefix,
                records_in=len(payload.records),
                records_ok=0,
                records_rej=len(rejected),
                error_code=f"INTERNAL_ERROR:{exc_class}",
            )
            return _error_response(
                status=500,
                code="INTERNAL_ERROR",
                message="persistence failure (see server logs)",
                request_id=payload.requestId,
                retryable=True,
            )

        for ar in result.accepted:
            accepted.append({"idempotencyKey": ar.idempotency_key, "status": ar.status})

    write_audit(
        request_id=payload.requestId,
        endpoint="batches",
        method=method,
        path=path,
        status=200,
        key_prefix=key_prefix,
        records_in=len(payload.records),
        records_ok=len(accepted),
        records_rej=len(rejected),
    )

    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "requestId": payload.requestId,
            "data": {"accepted": accepted, "rejected": rejected},
        },
    )


# ─── Exception handlers ───────────────────────────────────────────────


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Last-resort handler. Logs a sanitized message and returns 500.
    We never echo the request body or any token/header.
    """
    sys.stderr.write(f"[analytics-sync] unhandled exception on {request.url.path}: {type(exc).__name__}: {exc}\n")
    sys.stderr.write(traceback.format_exc())
    write_audit(
        request_id=_request_id_from_headers(request),
        endpoint="unknown",
        method=request.method,
        path=request.url.path,
        status=500,
        key_prefix=_key_prefix(request),
        error_code=type(exc).__name__,
    )
    return _error_response(
        status=500,
        code="INTERNAL_ERROR",
        message="an internal error occurred; see server logs",
        request_id=_request_id_from_headers(request),
        retryable=True,
    )


# ─── Helpers ──────────────────────────────────────────────────────────


def _today_in_tz(tz_name: str) -> date:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    return datetime.now(tz).date()


def _encode_cursor(page_size: int, total: int) -> str | None:
    """Opaque base64-encoded cursor. MVP: a simple offset hint."""
    if total <= page_size:
        return None
    payload = json.dumps({"page_size": page_size, "offset": page_size}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _request_id_from_headers(request: Request) -> str:
    """Pull X-Request-Id if supplied; otherwise mint a UUID."""
    rid = request.headers.get("x-request-id")
    if rid:
        return rid[:128]
    import uuid
    return f"req-{uuid.uuid4()}"


def _key_prefix(request: Request) -> str | None:
    """Best-effort read of the current request's token prefix from the
    ASGI scope (set by AuthMiddleware). Returns None when called outside
    a request (e.g., handler unit tests) or when auth mode is 'off'."""
    return request.scope.get("sync_token_prefix")  # type: ignore[no-any-return]


def _scopes(request: Request) -> tuple[str, ...]:
    """Read the token's scopes tuple from ASGI scope state. Empty tuple
    means unrestricted (or auth mode=off)."""
    return request.scope.get("sync_token_scopes", ())  # type: ignore[no-any-return]


def _error_response(
    *,
    status: int,
    code: str,
    message: str,
    request_id: str | None,
    retryable: bool,
) -> JSONResponse:
    """Build a sanitized error envelope. Never echoes tokens/headers/body."""
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "requestId": request_id or _request_id_from_headers_fallback(),
            "retryable": retryable,
        },
    )


def _request_id_from_headers_fallback() -> str:
    import uuid
    return f"req-{uuid.uuid4()}"


def _audit_and_error(
    *,
    request: Request,
    request_id: str,
    status: int,
    code: str,
    message: str,
    retryable: bool,
    key_prefix: str | None,
    error_code: str,
    method: str,
    path: str,
) -> JSONResponse:
    """Audit-then-error helper. Single-shot pattern used by all early-return
    paths in post_batches."""
    write_audit(
        request_id=request_id,
        endpoint="batches",
        method=method,
        path=path,
        status=status,
        key_prefix=key_prefix,
        error_code=error_code,
    )
    return _error_response(
        status=status, code=code, message=message,
        request_id=request_id, retryable=retryable,
    )
