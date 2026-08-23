"""FastAPI application for analytics_sync.

Endpoints (per protocol):
    GET  /v1/analytics/sync/cursor
    POST /v1/analytics/sync/batches

Plus ops endpoints (auth-exempt):
    GET  /healthz
    GET  /endpoints

Run:
    uvicorn analytics_sync.app:app --host 0.0.0.0 --port 9878
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

# Load .env from the repo root (works both in worktree and main checkout).
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _line in (_REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines() if (_REPO_ROOT / ".env").exists() else []:
    _line = _line.strip()
    if not _line or _line.startswith("#") or "=" not in _line:
        continue
    _k, _v = _line.split("=", 1)
    os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from .auth import AuthMiddleware
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
DEFAULT_BOOTSTRAP_LOOKBACK_DAYS = 30
DEFAULT_TIMEZONE = "Asia/Shanghai"
MAX_BATCH_RECORDS = 100
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MB per protocol


# ─── App + middleware ─────────────────────────────────────────────────

app = FastAPI(
    title="analytics-sync",
    version="0.1.0",
    description="Analytics data sync backend for the Chrome extension. Replaces the retired CloudBase path.",
)

app.add_middleware(AuthMiddleware)


# ─── Ops endpoints ────────────────────────────────────────────────────


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": "analytics-sync", "version": "0.1.0"}


@app.get("/endpoints")
def endpoints() -> dict[str, Any]:
    return {
        "service": "analytics-sync",
        "version": "0.1.0",
        "protocol_version": PROTOCOL_VERSION,
        "endpoints": [
            {
                "method": "GET",
                "path": "/v1/analytics/sync/cursor",
                "description": "Latest persisted day per (scope, storageKey, campaignId).",
            },
            {
                "method": "POST",
                "path": "/v1/analytics/sync/batches",
                "description": "Idempotent batch upload.",
            },
        ],
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
        # Pydantic accepts both naive and aware datetimes; for protocol
        # compliance we require an explicit timezone (the plugin always
        # sends UTC Z-suffixed ISO-8601).
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
    sellerId: str = Query(min_length=1, max_length=128),
    advertiserId: str = Query(min_length=1, max_length=128),
    storageKey: StorageKey | None = Query(default=None),
    campaignId: str | None = Query(default=None, max_length=128),
    cursor: str | None = Query(default=None, max_length=4096, description="Opaque pagination cursor (unused in MVP)."),
    pageSize: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    """Return the canonical latest-day state for every (storageKey, campaignId)
    pair known for this scope. Returns timezone + nextRequiredDay per row.

    nextRequiredDay is authoritative: if no record exists, the server
    returns a bootstrap date (today - ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS,
    default 30) in the shop's canonical timezone. The plugin must NOT
    infer this from its monitoring queryRecentDays setting.
    """
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

    # Cap to pageSize; pagination cursor is opaque and unused for MVP.
    page = entries[:pageSize]
    next_cursor = _encode_cursor(pageSize, len(entries)) if len(entries) > pageSize else None

    write_audit(
        request_id=None,
        endpoint="cursor",
        method="GET",
        path=f"/v1/analytics/sync/cursor?sellerId={sellerId}&advertiserId={advertiserId}",
        status=200,
        key_prefix=_scope_token_prefix(),
    )

    return {
        "code": 0,
        "requestId": _request_id(None),
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
    }


# ─── Batch endpoint ───────────────────────────────────────────────────


@app.post("/v1/analytics/sync/batches")
async def post_batches(request: Request) -> JSONResponse:
    """Idempotent batch upload with per-record outcomes."""
    body_bytes = await request.body()
    if len(body_bytes) > MAX_BODY_BYTES:
        return _error_response(
            status=413,
            code="PAYLOAD_TOO_LARGE",
            message=f"request body exceeds {MAX_BODY_BYTES} bytes",
            retryable=False,
        )

    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError as exc:
        return _error_response(
            status=400,
            code="MALFORMED_JSON",
            message=f"JSON parse error: {exc.msg}",
            retryable=False,
        )

    try:
        payload = BatchRequest.model_validate(body)
    except Exception as exc:
        return _error_response(
            status=400,
            code="SCHEMA_INVALID",
            message=str(exc),
            retryable=False,
        )

    # Validate protocolVersion matches server expectation.
    if payload.protocolVersion != PROTOCOL_VERSION:
        return _error_response(
            status=400,
            code="UNSUPPORTED_PROTOCOL_VERSION",
            message=f"server expects protocolVersion={PROTOCOL_VERSION}, client sent {payload.protocolVersion}",
            retryable=False,
        )

    # Validate per-record fields that aren't expressible in the schema
    # (e.g., the idempotencyKey must match the canonical derivation).
    accepted: list[dict[str, str]] = []
    rejected: list[dict[str, Any]] = []
    valid_records: list[Record] = []

    for idx, rec_in in enumerate(payload.records):
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
            return _error_response(
                status=500,
                code="INTERNAL_ERROR",
                message=f"persistence failure: {type(exc).__name__}",
                retryable=True,
            )

        for ar in result.accepted:
            accepted.append({"idempotencyKey": ar.idempotency_key, "status": ar.status})

    write_audit(
        request_id=payload.requestId,
        endpoint="batches",
        method="POST",
        path="/v1/analytics/sync/batches",
        status=200,
        key_prefix=_scope_token_prefix(),
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
    """Last-resort handler. We log a sanitized message and return 500.
    We never echo the request body or any token/header.
    """
    import traceback
    sys.stderr.write(f"[analytics-sync] unhandled exception on {request.url.path}: {exc!r}\n")
    sys.stderr.write(traceback.format_exc())
    return _error_response(
        status=500,
        code="INTERNAL_ERROR",
        message="an internal error occurred; see server logs",
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
    """Opaque base64-encoded cursor. MVP: a simple offset hint.

    A real implementation would keyset-paginate on
    (storage_key, campaign_id) and verify the page hasn't shifted.
    """
    if total <= page_size:
        return None
    payload = json.dumps({"page_size": page_size, "offset": page_size}, separators=(",", ":"))
    import base64
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _request_id(supplied: str | None) -> str:
    if supplied:
        return supplied
    import uuid
    return f"req-{uuid.uuid4()}"


def _scope_token_prefix() -> str | None:
    """Best-effort read of the current request's token prefix from
    contextvars. Returns None when called outside a request."""
    try:
        from contextvars import ContextVar
        prefix_var: ContextVar[str | None] = ContextVar("sync_token_prefix", default=None)
        return prefix_var.get()
    except Exception:
        return None


def _error_response(
    *,
    status: int,
    code: str,
    message: str,
    retryable: bool,
    request_id: str | None = None,
) -> JSONResponse:
    """Build a sanitized error envelope. Never echoes tokens/headers/body."""
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "requestId": request_id or _request_id(None),
            "retryable": retryable,
        },
    )
