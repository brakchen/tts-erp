"""analytics_sync handlers — mounted under tts-erp FastAPI at /v1/analytics/sync.

This module provides only handlers, Pydantic models, and pure helpers
(scope_grants, _key_prefix, _scopes, _request_id_from_headers, etc).

Mounted exclusively by ``tts_erp_v2.app:build_app()`` via
``app.include_router(router, prefix="/v1/analytics/sync")``. Auth and
rate-limiting are inherited from the v2 app's middleware stack:

- ``tts_erp_v2.middleware.auth.AuthMiddleware`` populates
  ``request.scope["api_key_hash"]`` and ``request.scope["api_key_scopes"]``
  before each handler runs.
- ``tts_erp_v2.middleware.rate_limit.RateLimitMiddleware`` buckets per
  ``api_key_hash``.

The standalone :9878 service was retired 2026-08-30; ``analytics_sync``
no longer ships its own FastAPI app, auth middleware, or rate-limit
middleware. See ``setup/analytics-sync.md`` for the new deployment
topology (one process — ``tts-erp`` on :9877 — hosts every API).

Per-seller scope check is enforced inside each handler by reading
``request.scope["api_key_scopes"]``.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Load .env from the repo root BEFORE importing anything that reads env
# vars at module import time (e.g. tts_erp via tdd.auth). Works both in
# worktree and main checkout.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_env_path = _REPO_ROOT / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from anyio.to_thread import run_sync  # noqa: E402
from fastapi import APIRouter, Query, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, Field, ValidationError, field_validator  # noqa: E402

from .domain import (  # noqa: E402
    DEFAULT_TIMEZONE,
    Record,
    Scope,
    StorageKey,
    compute_idempotency_key,
)
from .pg_repositories import (  # noqa: E402
    PgAnalyticsRepository,
    fetch_cursor_page,
    fetch_timezone,
    write_audit,
)

# ─── Config ────────────────────────────────────────────────────────────

PROTOCOL_VERSION = 2
SUPPORTED_PROTOCOL_VERSIONS = {1, 2}
PROTOCOL_VERSION_HEADER = "X-Protocol-Version"
DEFAULT_BOOTSTRAP_LOOKBACK_DAYS = 30
MAX_BATCH_RECORDS = 100
MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MB per protocol §5
MAX_RESPONSE_DATA_BYTES = 256 * 1024  # cap individual response_data JSON


# ─── Scope-grant helper (also used by tests) ──────────────────────────


def scope_grants(scopes, *, seller_id, advertiser_id):
    """Return True iff the token's scopes cover the requested scope.

    Empty scopes / wildcard '*' = unrestricted. Within one dimension,
    multiple entries are OR'd (any match grants). Unknown prefixes
    (typos like 'seler:x') fail closed — silently ignoring them would
    make a misspelled scope entry a no-op that looks like it works.

    Mirrors tts-erp/api_keys.py `scopes` semantics (see api_keys CLI
    `--scopes` flag).
    """
    if not scopes or "*" in scopes:
        return True
    seller_grants = [s[len("seller:") :] for s in scopes if s.startswith("seller:")]
    advertiser_grants = [
        s[len("advertiser:") :] for s in scopes if s.startswith("advertiser:")
    ]
    known = len(seller_grants) + len(advertiser_grants)
    if known != len(scopes):
        return False  # unknown prefix present → fail closed
    if seller_grants and seller_id not in seller_grants:
        return False
    return not (advertiser_grants and advertiser_id not in advertiser_grants)


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
    expectedPageCount: int | None = Field(default=None, ge=1)
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
            raise ValueError(
                "capturedAt must include a timezone (use ISO-8601 with 'Z' or '+00:00')"
            )
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
        seller_id=sellerId,
        advertiser_id=advertiserId,
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
    bootstrap_days = _safe_int(
        os.environ.get("ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS"),
        DEFAULT_BOOTSTRAP_LOOKBACK_DAYS,
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
    # W1.8: nextCursor is NOT implemented (fetch_cursor_page returns all
    # rows; the incoming `cursor` param is ignored). Emitting a cursor the
    # server can't consume would send clients into an infinite loop
    # re-fetching page 1 — stay null until real keyset pagination lands
    # (Wave 3.5). Current unit counts are far below pageSize anyway.
    next_cursor = None

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
                        # sellerId/advertiserId are echoed per row: the
                        # Chrome extension's parseCursor strictly matches
                        # items on the requested scope (2026-08-30 protocol
                        # alignment — do not drop these fields).
                        "sellerId": sellerId,
                        "advertiserId": advertiserId,
                        "storageKey": e.storage_key.value,
                        "campaignId": e.campaign_id,
                        "latestCompletedDay": e.latest_completed_day.isoformat()
                        if e.latest_completed_day
                        else None,
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

    All sync psycopg calls (write_audit / upsert_records / fetch_timezone)
    are wrapped in anyio.to_thread.run_sync so they never block the
    event loop.
    """
    request_id = _request_id_from_headers(request)
    key_prefix = _key_prefix(request)
    method = "POST"
    path = "/v1/analytics/sync/batches"

    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > MAX_BODY_BYTES:
                return await _audit_and_error(
                    request=request,
                    request_id=request_id,
                    status=413,
                    code="PAYLOAD_TOO_LARGE",
                    message=f"Content-Length {cl} exceeds maximum {MAX_BODY_BYTES} bytes",
                    retryable=False,
                    key_prefix=key_prefix,
                    error_code="PAYLOAD_TOO_LARGE",
                    method=method,
                    path=path,
                )
        except ValueError:
            pass

    body_bytes = await request.body()
    if len(body_bytes) > MAX_BODY_BYTES:
        return await _audit_and_error(
            request=request,
            request_id=request_id,
            status=413,
            code="PAYLOAD_TOO_LARGE",
            message=f"actual body size {len(body_bytes)} exceeds maximum {MAX_BODY_BYTES} bytes",
            retryable=False,
            key_prefix=key_prefix,
            error_code="PAYLOAD_TOO_LARGE",
            method=method,
            path=path,
        )

    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError as exc:
        return await _audit_and_error(
            request=request,
            request_id=request_id,
            status=400,
            code="MALFORMED_JSON",
            message=f"JSON parse error: {exc.msg}",
            retryable=False,
            key_prefix=key_prefix,
            error_code="MALFORMED_JSON",
            method=method,
            path=path,
        )

    try:
        payload = BatchRequest.model_validate(body)
    except ValidationError as exc:
        # Surface the failing field/path/type to the client so they can
        # diagnose without parsing the free-form Pydantic message. We
        # sanitize: drop `input` / `ctx` (those can carry record-body
        # values) and keep only the safe identifier triple.
        return await _audit_and_error(
            request=request,
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message=str(exc),
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method=method,
            path=path,
            structured_errors=_sanitize_pydantic_errors(exc),
        )
    except Exception as exc:
        # Unexpected: Pydantic schema parsed but something inside the
        # record-level handler exploded (e.g. a downstream validator
        # bug). Treat as SCHEMA_INVALID with no field-level detail so
        # the client gets the same envelope shape; ops get the raw
        # exception class via stderr.
        return await _audit_and_error(
            request=request,
            request_id=request_id,
            status=400,
            code="SCHEMA_INVALID",
            message=str(exc),
            retryable=False,
            key_prefix=key_prefix,
            error_code="SCHEMA_INVALID",
            method=method,
            path=path,
            structured_errors=[{
                "loc": [],
                "msg": type(exc).__name__,
                "type": "internal_error",
            }],
        )

    if payload.protocolVersion not in SUPPORTED_PROTOCOL_VERSIONS:
        return await _audit_and_error(
            request=request,
            request_id=request_id,
            status=400,
            code="UNSUPPORTED_PROTOCOL_VERSION",
            message=(
                f"server supports protocolVersion in "
                f"{sorted(SUPPORTED_PROTOCOL_VERSIONS)}, client sent {payload.protocolVersion}"
            ),
            retryable=False,
            key_prefix=key_prefix,
            error_code="UNSUPPORTED_PROTOCOL_VERSION",
            method=method,
            path=path,
        )

    if not scope_grants(
        tuple(_scopes(request)),
        seller_id=payload.scope.sellerId,
        advertiser_id=payload.scope.advertiserId,
    ):
        await run_sync(
            partial(
                write_audit,
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

    # Within-batch expectedPageCount must be consistent per daily unit.
    expected_by_unit: dict[tuple[str, str, str, str, date], int] = {}

    for idx, rec_in in enumerate(payload.records):
        unit = (
            payload.scope.sellerId,
            payload.scope.advertiserId,
            rec_in.storageKey.value,
            rec_in.campaignId,
            rec_in.day,
        )

        effective_expected = _effective_expected_page_count(
            payload.protocolVersion, rec_in.expectedPageCount
        )
        if effective_expected is None:
            rejected.append(
                {
                    "idempotencyKey": rec_in.idempotencyKey,
                    "code": "SCHEMA_INVALID",
                    "message": (
                        f"records[{idx}].expectedPageCount is required for protocolVersion 2"
                    ),
                    "retryable": False,
                }
            )
            continue

        if rec_in.page > effective_expected and payload.protocolVersion == 2:
            rejected.append(
                {
                    "idempotencyKey": rec_in.idempotencyKey,
                    "code": "SCHEMA_INVALID",
                    "message": (
                        f"records[{idx}].page ({rec_in.page}) cannot exceed "
                        f"expectedPageCount ({effective_expected})"
                    ),
                    "retryable": False,
                }
            )
            continue

        existing = expected_by_unit.get(unit)
        if existing is None:
            expected_by_unit[unit] = effective_expected
        elif existing != effective_expected:
            rejected.append(
                {
                    "idempotencyKey": rec_in.idempotencyKey,
                    "code": "PAGE_COUNT_CONFLICT",
                    "message": (
                        f"records[{idx}] has expectedPageCount={effective_expected}, "
                        f"but the same daily unit already has expectedPageCount={existing} "
                        f"in this batch"
                    ),
                    "retryable": False,
                }
            )
            continue
        try:
            response_size = len(
                json.dumps(rec_in.response, ensure_ascii=False).encode()
            )
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
                expected_page_count=expected_by_unit[unit],
                protocol_version=payload.protocolVersion,
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
            tz_name = await run_sync(fetch_timezone, payload.scope.sellerId)
            today = _today_in_tz(tz_name)
            bootstrap_days = _safe_int(
                os.environ.get(
                    "ANALYTICS_SYNC_BOOTSTRAP_LOOKBACK_DAYS",
                    str(DEFAULT_BOOTSTRAP_LOOKBACK_DAYS),
                ),
                DEFAULT_BOOTSTRAP_LOOKBACK_DAYS,
            )
            bootstrap_day = _subtract_days(today, bootstrap_days)

            result = await run_sync(
                partial(
                    PgAnalyticsRepository().upsert_records,
                    scope,
                    valid_records,
                    request_id=payload.requestId,
                    today_in_shop_tz=today,
                    bootstrap_day=bootstrap_day,
                )
            )
        except Exception as exc:
            exc_class = type(exc).__name__
            sys.stderr.write(
                f"[analytics-sync] persistence failure: {exc_class}: {exc}\n"
            )
            await run_sync(
                partial(
                    write_audit,
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
        for rr in result.rejected:
            rejected.append(
                {
                    "idempotencyKey": rr.idempotency_key,
                    "code": rr.code,
                    "message": rr.message,
                    "retryable": rr.retryable,
                }
            )

    await run_sync(
        partial(
            write_audit,
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


def _effective_expected_page_count(
    protocol_version: int, raw: int | None
) -> int | None:
    """v1 records are implicitly single-page days. v2 records must declare
    expectedPageCount explicitly."""
    if protocol_version == 1:
        return 1
    if raw is None or raw < 1:
        return None
    return raw


def _subtract_days(d: date, n: int) -> date:
    """Subtract n days from d."""
    return d - timedelta(days=n)


def _safe_int(value: str | None, default: int) -> int:
    """Parse an int from an environment string, returning default on failure."""
    if value is None:
        return default
    try:
        return max(1, int(value))
    except ValueError:
        return default


def _encode_cursor(page_size: int, total: int) -> str | None:
    """Opaque base64-encoded cursor. UNUSED since W1.8: nextCursor is
    always null until real keyset pagination is implemented (Wave 3.5).
    Kept for the Wave 3.5 implementation to build on."""
    if total <= page_size:
        return None
    payload = json.dumps(
        {"page_size": page_size, "offset": page_size}, separators=(",", ":")
    )
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
    key_hash = request.scope.get("api_key_hash")
    return key_hash[:16] if isinstance(key_hash, str) else None


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
    structured_errors: list[dict[str, object]] | None = None,
) -> JSONResponse:
    """Build a sanitized error envelope. Never echoes tokens/headers/body.

    ``structured_errors`` is the safe identifier triple (loc/msg/type) from
    Pydantic — clients use it to programmatically identify which record and
    field failed without regex-parsing the free-form ``message``. When None
    (default) the field is omitted; we never emit an empty list because that
    would change the JSON shape for the v1 contract.
    """
    payload: dict[str, object] = {
        "code": code,
        "message": message,
        "requestId": request_id or _request_id_from_headers_fallback(),
        "retryable": retryable,
    }
    if structured_errors:
        payload["errors"] = structured_errors
    return JSONResponse(status_code=status, content=payload)


def _sanitize_pydantic_errors(exc: ValidationError) -> list[dict[str, object]]:
    """Reduce Pydantic's errors() to the safe identifier triple.

    Pydantic's full per-error dict carries:
      - ``type``     → kept (safe identifier, e.g. ``string_too_short``)
      - ``loc``      → kept as list of path segments **with their original
                        Python types** (int for list indices, str for field
                        names). The client uses these to drill into the
                        offending record (``loc == ['records', 0,
                        'capturedAt']`` ⇒ record #0, field capturedAt).
                        Coercing int → str loses the array-index shape;
                        joining with ``.`` would also be ambiguous when a
                        field name happens to contain a dot.
      - ``msg``      → kept (Pydantic's free-form message — already
                        sanitized; no body, no token)
      - ``input``    → DROPPED — can contain the offending record field
                        value verbatim, which for our batch endpoint is
                        usually innocuous but the policy is consistent
                        with the storage-layer redaction (see
                        ``tts_erp_v2/extension/storage.py::SENSITIVE_*``)
      - ``ctx``      → DROPPED — same reason (e.g. ``min_length: 64``
                        leaks the schema constraint which is harmless, but
                        tighter field-by-field values like ``actual_length``
                        could leak; we drop the whole ctx to be safe)
      - ``url``      → DROPPED — internal doc link, no client value

    Output is JSON-serializable, ordering-stable, and bounded by the
    Pydantic error count (one entry per failed field/rule).
    """
    sanitized: list[dict[str, object]] = []
    for err in exc.errors():
        loc_segments: list[object] = []
        for segment in err.get("loc", ()):
            # Pydantic uses int for list indices, str for field names.
            # Preserve both: a client that needs to pluralize "record 0"
            # vs "records[0].capturedAt" gets the right primitive.
            if isinstance(segment, (int, str)):
                loc_segments.append(segment)
            else:
                loc_segments.append(str(segment))
        sanitized.append(
            {
                "loc": loc_segments,
                "msg": str(err.get("msg", "")),
                "type": str(err.get("type", "unknown")),
            }
        )
    return sanitized


def _request_id_from_headers_fallback() -> str:
    import uuid

    return f"req-{uuid.uuid4()}"


async def _audit_and_error(
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
    structured_errors: list[dict[str, object]] | None = None,
) -> JSONResponse:
    """One-line stderr diagnostic so ops can tell WHICH field/rule failed
    without asking the client — the audit table only stores the error
    code (2026-08-30 incident: real Chrome-extension traffic returned
    SCHEMA_INVALID for hours with no field detail anywhere server-side).
    ``message`` is a Pydantic/JSON-parse description (field names +
    truncated input values); it never contains headers, tokens, or the
    request body. Newlines are flattened to keep the line grep-friendly.

    ``structured_errors`` (added 2026-08-31) is the safe identifier triple
    from Pydantic (loc/msg/type, ``input``/``ctx``/``url`` dropped — see
    ``_sanitize_pydantic_errors``). It rides on the response body as
    ``errors[]`` so the Chrome extension can branch on the failing field
    path without regex-parsing the free-form message, AND is persisted
    into ``analytics_audit_log.error_message`` (same 500-char sanitized
    payload as stderr) so ops can ``SELECT ... WHERE error_message LIKE
    '%capturedAt%'`` after the log rotates.
    """
    safe_message = " ".join(str(message).split())[:500]
    sys.stderr.write(
        f"[analytics-sync] reject status={status} code={code} "
        f"request_id={request_id} key_prefix={key_prefix or '-'} "
        f"method={method} path={path} message={safe_message}\n"
    )
    await run_sync(
        partial(
            write_audit,
            request_id=request_id,
            endpoint="batches",
            method=method,
            path=path,
            status=status,
            key_prefix=key_prefix,
            error_code=error_code,
            error_message=safe_message,
        )
    )
    return _error_response(
        status=status,
        code=code,
        message=message,
        request_id=request_id,
        retryable=retryable,
        structured_errors=structured_errors,
    )
