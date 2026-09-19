"""Shared HTTP helpers for /v2/* dump endpoints.

Deduplicates identical _request_id / _key_prefix / _error_response /
_audit_and_error between order_sync.py and analytics.py.
"""

from __future__ import annotations

import logging
import sys
import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger("tts_erp_v2.api._common")


# ─── Request helpers ─────────────────────────────────────────────────


def request_id(request: Request) -> str:
    """Extract x-request-id header or generate one."""
    rid = request.headers.get("x-request-id")
    if rid:
        return rid[:128]
    return f"req-{uuid.uuid4()}"


def key_prefix(request: Request) -> str | None:
    """Read the api key's 16-char prefix from ASGI scope."""
    key_hash = request.scope.get("api_key_hash")
    return key_hash[:16] if isinstance(key_hash, str) else None


# ─── Message sanitization ────────────────────────────────────────────


def sanitize_message(message: Any) -> str:
    """Flatten whitespace, cap at 500 chars, single-line grep-friendly."""
    return " ".join(str(message).split())[:500]


# ─── Structured audit log ────────────────────────────────────────────


def log_event(
    *,
    logger: logging.Logger,
    level: int,
    request_id: str | None,
    key_prefix: str | None,
    method: str,
    path: str,
    status: int,
    records_in: int | None = None,
    records_ok: int | None = None,
    records_rej: int | None = None,
    error_code: str | None = None,
    message: str | None = None,
) -> None:
    """Emit a single key=value audit log line."""
    parts: list[str] = [
        f"request_id={request_id or '-'}",
        f"key_prefix={key_prefix or '-'}",
        f"method={method}",
        f"path={path}",
        f"status={status}",
    ]
    if records_in is not None:
        parts.append(f"records_in={records_in}")
    if records_ok is not None:
        parts.append(f"records_ok={records_ok}")
    if records_rej is not None:
        parts.append(f"records_rej={records_rej}")
    if error_code:
        parts.append(f"error_code={error_code}")
    if message:
        parts.append(f"message={sanitize_message(message)}")
    logger.log(level, " ".join(parts))


# ─── Response builders ───────────────────────────────────────────────


def error_response(
    *,
    status: int,
    code: str,
    message: str,
    request_id: str | None,
    retryable: bool = False,
    structured_errors: list[dict[str, object]] | None = None,
) -> JSONResponse:
    """Build a sanitized error envelope.

    ``retryable`` and ``structured_errors`` are optional — order_sync ignores
    them (defaults), analytics passes them through.
    """
    payload: dict[str, object] = {
        "code": code,
        "message": message,
        "requestId": request_id or f"req-{uuid.uuid4()}",
    }
    if retryable:
        payload["retryable"] = retryable
    if structured_errors:
        payload["errors"] = structured_errors
    return JSONResponse(status_code=status, content=payload)


def ok_response(
    *,
    request_id: str,
    data: dict[str, Any],
) -> JSONResponse:
    """AGENTS.md §2.5: 4-field envelope (code/message/requestId/data)."""
    return JSONResponse(
        status_code=200,
        content={
            "code": 0,
            "message": "success",
            "requestId": request_id,
            "data": data,
        },
    )


def audit_and_error(
    *,
    request_id: str,
    status: int,
    code: str,
    message: str,
    key_prefix: str | None,
    method: str,
    path: str,
    logger: logging.Logger,
    retryable: bool = False,
    structured_errors: list[dict[str, object]] | None = None,
) -> JSONResponse:
    """Structured audit log + return error response."""
    safe_message = sanitize_message(message)
    log.warning(
        "reject status=%d code=%s request_id=%s key_prefix=%s method=%s path=%s message=%s",
        status,
        code,
        request_id,
        key_prefix or "-",
        method,
        path,
        safe_message,
    )
    log_event(
        logger=logger,
        level=logging.WARNING,
        request_id=request_id,
        key_prefix=key_prefix,
        method=method,
        path=path,
        status=status,
        error_code=code,
        message=safe_message,
    )
    return error_response(
        status=status,
        code=code,
        message=message,
        request_id=request_id,
        retryable=retryable,
        structured_errors=structured_errors,
    )
