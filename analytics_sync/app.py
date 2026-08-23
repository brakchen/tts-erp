"""analytics_sync handlers — mounted under tts-erp FastAPI at /v1/analytics/sync.

Routes go through tts-erp's AuthMiddleware (api_keys table) and
RateLimitMiddleware (per api_key_hash). This module provides only
handlers + Pydantic models.

Per-seller scope check is enforced inside each handler by reading
`request.scope["api_key_scopes"]` (populated by tts-erp/auth.py).
No middleware of our own.

Run standalone (NOT recommended):
    uvicorn analytics_sync.app:app --host 0.0.0.0 --port 9878
In production, always mount under tts-erp via include_router.
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

from fastapi import APIRouter, Query, Request
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


# ─── Config ────────────────────────────────────────────────────────────

PROTOCOL_VERSION = 1
PROTOCOL_VERSION_HEADER = "X-Protocol-Version"
DEFAULT_BOOTSTRAP_LOOKBACK_DAYS = 30
DEFAULT_TIMEZONE = "Asia/Shanghai"
MAX_BATCH_RECORDS = 100
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MB per protocol §5
MAX_RESPONSE_DATA_BYTES = 256 * 1024  # cap individual response_data JSON


# ─── Scope-grant helper (also used by tests) ──────────────────────────


def scope_grants(scopes, *, seller_id, advertiser_id):
    """Return True iff the token's scopes cover the requested scope.

    Empty scopes / wildcard '*' = unrestricted. Otherwise each scope
    entry must match the request's corresponding dimension.

    Mirrors tts-erp/api_keys.py `scopes` semantics (see api_keys CLI
    `--scopes` flag).
    """
    if not scopes or "*" in scopes:
        return True
    for s in scopes:
        if s.startswith("seller:"):
            target = s[len("seller:"):]
            if seller_id != target:
                return False
        elif s.startswith("advertiser:"):
            target = s[len("advertiser:"):]
            if advertiser_id != target:
                return False
    return True


# ─── Router (mounted under /v1/analytics/sync by tts-erp) ─────────────


router = APIRouter()


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


@router.get("/cursor")
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

    if not scope_grants(
        tuple(_scopes(request)),
        seller_id=sellerId, advertiser_id=advertiserId,
    ):
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
            message="api key does not grant access to this scope",
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


@router.post("/batches")
async def post_batches(request: Request) -> JSONResponse:
    """Idempotent batch upload with per-record outcomes.

    Limits: 100 records max, 2 MB body max. Body size is checked BEFORE
    JSON parsing so a too-large request returns 413 cleanly.
    """
    request_id = _request_id_from_headers(request)
    key_prefix = _key_prefix(request)
    method = "POST"
    path = "/v1/analytics/sync/batches"

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
            pass

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

    if not scope_grants(
        tuple(_scopes(request)),
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
            records_rej=0,
            error_code="SCOPE_DENIED",
        )
        return _error_response(
            status=403,
            code="SCOPE_DENIED",
            message="api key does not grant access to this scope",
            request_id=payload.requestId,
            retryable=False,
        )

    accepted: list[dict[str, str]] = []
    rejected: list[dict[str, Any]] = []
    valid_records: list[Record] = []

    for idx, rec_in in enumerate(payload.records):
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
    rid = request.headers.get("x-request-id")
    if rid:
        return rid[:128]
    import uuid
    return f"req-{uuid.uuid4()}"


def _key_prefix(request: Request) -> str | None:
    """Read the api key's 16-char prefix from ASGI scope (set by
    tts-erp's AuthMiddleware). None if request is unauthenticated."""
    return request.scope.get("api_key_hash") and request.scope.get("api_key_hash")[:16]


def _scopes(request: Request) -> tuple[str, ...]:
    """Read the api key's scopes tuple from ASGI scope (set by
    tts-erp's AuthMiddleware). Empty tuple means unrestricted."""
    return request.scope.get("api_key_scopes", ())  # type: ignore[no-any-return]


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


# ─── Standalone FastAPI app (port 9878) ────────────────────────────────
# Exposed for `uvicorn analytics_sync.app:app`. When mounted under
# tts-erp via include_router, the parent's auth + rate-limit apply and
# this app instance is unused.

from fastapi import FastAPI as _FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware as _CORS  # noqa: E402
from .auth import SyncAuthMiddleware  # noqa: E402
from .rate_limit import SyncRateLimitMiddleware  # noqa: E402

app = _FastAPI(
    title="analytics_sync",
    version="0.3.0",
    description="Analytics data sync backend. Standalone port 9878; unified auth via tts-erp api_keys.",
)

_cors_origins_env = os.environ.get("ANALYTICS_SYNC_CORS_ALLOW_ORIGINS", "").strip()
if _cors_origins_env.lower() == "wildcard":
    _cors_allow_origins = ["*"]
elif _cors_origins_env:
    _cors_allow_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
else:
    _cors_allow_origins = []

# Order: CORS outer → RateLimit → Auth → endpoint.
app.add_middleware(SyncRateLimitMiddleware)
app.add_middleware(SyncAuthMiddleware)
app.add_middleware(
    _CORS,
    allow_origins=_cors_allow_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Mount the analytics router under /v1/analytics/sync.
app.include_router(router, prefix="/v1/analytics/sync")


@app.get("/healthz")
def _healthz():
    return {"status": "ok", "service": "analytics-sync", "version": "0.3.0"}


@app.get("/endpoints")
def _endpoints():
    return {
        "service": "analytics-sync",
        "version": "0.3.0",
        "protocol_version": PROTOCOL_VERSION,
        "auth": "shared with tts-erp (api_keys table; ANALYTICS_SYNC_AUTH_MODE)",
        "endpoints": [
            {"method": "GET", "path": "/v1/analytics/sync/cursor"},
            {"method": "POST", "path": "/v1/analytics/sync/batches"},
        ],
    }
