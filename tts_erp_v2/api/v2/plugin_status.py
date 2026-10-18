"""``/v2/plugin/status/*`` and ``/v2/plugin/admin/instances/*``.

Thin routing only. The protocol rules live in
:mod:`tts_erp_v2.plugin.status.contracts`, the decision policy in
:mod:`tts_erp_v2.plugin.status.policy`, and every write in
:mod:`tts_erp_v2.plugin.status.service`.

Handler structure mirrors the existing dump endpoints
(``api/v2/analytics.py``): the 32 KiB body gate has to read the raw body, which
only an async dependency can do, so the dependency hands bytes to a
**synchronous** handler. No psycopg work ever runs inside an async handler
(AGENTS.md §6).

The report route authenticates through
:func:`tts_erp_v2.plugin.status.service.authenticate_report` rather than the
middleware grant, so its purpose check holds in ``off``/``shadow`` too (§9.2).
The admin routes use the shared ``require_role_at_least`` and the project's
cookie CSRF guard.

This router is mounted by the integration step; ``app.py`` itself only gains one
``include_router`` line and no middleware-order change.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import get_session, require_role_at_least
from tts_erp_v2.plugin.status import service
from tts_erp_v2.plugin.status.contracts import (
    SCHEMA_VERSION,
    AdminDecisionRequest,
    Connectivity,
    InstanceRegistrationRequest,
    PluginId,
    report_payload_hash,
    status_error_payload,
    status_report_from_wire,
    status_request_id,
    status_response_to_wire,
)
from tts_erp_v2.plugin.status.service import StatusServiceError

log = logging.getLogger("tts_erp_v2.plugin.status.api")

router = APIRouter()

PATH_REPORT = "/v2/plugin/status/report"
PATH_INSTANCES = "/v2/plugin/admin/instances"

#: §6.1: one report per request, 32 KiB maximum. A bigger body is refused whole;
#: truncating it into a "valid" report would fabricate a fact (§8).
MAX_BODY_BYTES = 32 * 1024

_NO_STORE = {"Cache-Control": "no-store"}


# ── Responses ─────────────────────────────────────────────────────────


def _error(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    retryable: bool = False,
) -> JSONResponse:
    """The plugin-facing error envelope: ``{requestId, error:{...}}``.

    Messages name the rule that refused and never echo the payload, the token
    or another shop's identity (§9.8).
    """
    return JSONResponse(
        status_code=status_code,
        content=status_error_payload(
            request_id=request_id,
            code=code,
            message=message,
            retryable=retryable,
        ),
        headers={
            **_NO_STORE,
            "X-Request-Id": request_id,
            **({"WWW-Authenticate": "Bearer"} if status_code == 401 else {}),
        },
    )


def _error_from(exc: StatusServiceError, request_id: str) -> JSONResponse:
    return _error(
        status_code=exc.status,
        code=exc.code,
        message=exc.message,
        request_id=request_id,
        retryable=exc.retryable,
    )


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def _csrf_error(request: Request) -> JSONResponse | None:
    """Mirror the cookie-CSRF guard used by the project's other POST routes.

    Bearer callers pick their own headers and are exempt; a browser session must
    prove the request was deliberate.
    """
    if (
        request.scope.get("auth_method") == "cookie"
        and request.headers.get("X-Requested-With") != "tts-erp"
    ):
        return _error(
            status_code=status.HTTP_403_FORBIDDEN,
            code="FORBIDDEN",
            message=(
                "cookie-authed POST must set header X-Requested-With: tts-erp "
                "(CSRF guard)"
            ),
            request_id=status_request_id(request.headers.get("x-request-id")),
        )
    return None


def _require_admin(request: Request) -> JSONResponse | None:
    """Admin role plus the cookie CSRF guard. Returns an error response or None."""
    try:
        require_role_at_least(request, "admin")
    except HTTPException as exc:
        return _error(
            status_code=exc.status_code,
            code="FORBIDDEN",
            message=str(exc.detail),
            request_id=status_request_id(request.headers.get("x-request-id")),
        )
    return _csrf_error(request)


def _service_error_response(exc: StatusServiceError, request: Request) -> JSONResponse:
    return _error(
        status_code=exc.status,
        code=exc.code,
        message=exc.message,
        request_id=status_request_id(request.headers.get("x-request-id")),
        retryable=exc.retryable,
    )


# ── Report (§6, §7, §8) ───────────────────────────────────────────────


async def _bounded_report_body(request: Request) -> bytes | None:
    """Read the raw body, refusing anything over the protocol cap.

    Returning ``None`` rather than raising keeps the size gate in the sync
    handler, where the error envelope and ``no-store`` header already live.
    """
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return None
    return body


@router.post(
    PATH_REPORT,
    summary="插件上报运行状态并取得受限决策（专用 status key）",
)
def post_status_report(
    request: Request,
    raw_body: Annotated[bytes | None, Depends(_bounded_report_body)],
    sess: Annotated[Session, Depends(get_session)],
) -> JSONResponse:
    """Accept one status report; 200 means the report and decision were committed.

    Order matters and follows §10: size gate, then credential/purpose, then
    JSON, then schema, then scope. A caller without the dedicated credential
    cannot use a malformed body as an oracle, and a role that happens to be
    strong enough for other routes still gets nothing here.
    """
    request_id = request.scope.get("plugin_status_request_id")
    if request_id is None:
        request_id = status_request_id(request.headers.get("x-request-id"))
        request.scope["plugin_status_request_id"] = request_id
    now = datetime.now(UTC)

    if raw_body is None:
        return _error(
            status_code=413,
            code="BODY_TOO_LARGE",
            message="report body exceeds the 32 KiB protocol limit",
            request_id=request_id,
        )

    try:
        auth = service.authenticate_report(sess, bearer_token=_bearer_token(request))
    except StatusServiceError as exc:
        return _error_from(exc, request_id)

    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        return _error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="MALFORMED_JSON",
            message="request body is not valid JSON",
            request_id=request_id,
        )
    if not isinstance(payload, dict):
        return _error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="SCHEMA_INVALID",
            message="request body must be a JSON object",
            request_id=request_id,
        )
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        return _error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="UNSUPPORTED_VERSION",
            message="unsupported schemaVersion",
            request_id=request_id,
        )
    try:
        report = status_report_from_wire(payload)
    except ValidationError:
        # Never echo the failing payload: it may carry a scope the caller was
        # not authorised to name.
        return _error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="SCHEMA_INVALID",
            message="report does not satisfy schemaVersion 1",
            request_id=request_id,
        )

    try:
        response = service.ingest_report(
            sess,
            authenticated=auth,
            instance_id=auth.instance_id,
            report=report,
            payload_hash=report_payload_hash(report),
            request_id=request_id,
            now=now,
        )
        sess.commit()
    except StatusServiceError as exc:
        sess.rollback()
        return _error_from(exc, request_id)
    except SQLAlchemyError:
        sess.rollback()
        # DB exception details can include payload SQL parameters.
        log.error("[plugin-status] report store unavailable")
        return _error(
            status_code=503,
            code="STORE_UNAVAILABLE",
            message="the report was not committed; retry the same report",
            request_id=request_id,
            retryable=True,
        )
    except Exception:
        # A write, decision or outbox failure is a 5xx with nothing committed —
        # never a 200 that quietly lost a fact (§8).
        sess.rollback()
        log.exception("[plugin-status] report ingest failed")
        return _error(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="INTERNAL_ERROR",
            message="the report was not committed; retry the same report",
            request_id=request_id,
            retryable=True,
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=status_response_to_wire(response),
        headers={**_NO_STORE, "X-Request-Id": request_id},
    )


# ── Admin: instance provisioning and reads (§9.4) ──────────────────────


@router.post(
    f"{PATH_INSTANCES}",
    summary="开通受管实例并签发专用 status key（admin，明文只返回一次）",
)
def create_instance(
    request: Request,
    command: InstanceRegistrationRequest,
    sess: Annotated[Session, Depends(get_session)],
) -> Any:
    refused = _require_admin(request)
    if refused is not None:
        return refused
    try:
        payload = service.register_instance(
            sess, command=command, now=datetime.now(UTC)
        )
        sess.commit()
    except StatusServiceError as exc:
        sess.rollback()
        return _service_error_response(exc, request)
    return JSONResponse(status_code=200, content=payload, headers=_NO_STORE)


@router.get(
    PATH_INSTANCES,
    summary="分页查询受管实例（admin）",
)
def list_managed_instances(
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    shop_pk: Annotated[int | None, Query(alias="shop_pk")] = None,
    plugin_id: Annotated[PluginId | None, Query(alias="pluginId")] = None,
    connectivity: Annotated[Connectivity | None, Query()] = None,
) -> Any:
    require_role_at_least(request, "admin")
    return service.list_instances(
        sess,
        now=datetime.now(UTC),
        limit=limit,
        offset=offset,
        shop_pk=shop_pk,
        plugin_id=plugin_id.value if plugin_id is not None else None,
        connectivity=connectivity,
    )


@router.get(
    f"{PATH_INSTANCES}/{{instance_id}}",
    summary="查看实例状态、最近上报与当前决策（admin）",
)
def get_managed_instance(
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
    instance_id: Annotated[UUID, Path()],
) -> Any:
    require_role_at_least(request, "admin")
    try:
        return service.instance_detail(
            sess, instance_id=instance_id, now=datetime.now(UTC)
        )
    except StatusServiceError as exc:
        return _error(
            status_code=exc.status,
            code=exc.code,
            message=exc.message,
            request_id=status_request_id(request.headers.get("x-request-id")),
        )


def _lifecycle_response(sess: Session, request: Request, *, enabled: bool) -> Any:
    try:
        payload = service.set_instance_enabled(
            sess,
            instance_id=request.path_params["instance_id"],
            enabled=enabled,
            now=datetime.now(UTC),
        )
        sess.commit()
        return JSONResponse(status_code=200, content=payload, headers=_NO_STORE)
    except StatusServiceError as exc:
        sess.rollback()
        return _service_error_response(exc, request)


@router.post(
    f"{PATH_INSTANCES}/{{instance_id}}/enable",
    summary="启用实例（admin）",
)
def enable_managed_instance(
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
    instance_id: Annotated[UUID, Path()],
) -> Any:
    refused = _require_admin(request)
    if refused is not None:
        return refused
    return _lifecycle_response(sess, request, enabled=True)


@router.post(
    f"{PATH_INSTANCES}/{{instance_id}}/disable",
    summary="停用实例（admin；停用后该 status key 不再取得任何许可）",
)
def disable_managed_instance(
    request: Request,
    sess: Annotated[Session, Depends(get_session)],
    instance_id: Annotated[UUID, Path()],
) -> Any:
    refused = _require_admin(request)
    if refused is not None:
        return refused
    return _lifecycle_response(sess, request, enabled=False)


@router.post(
    f"{PATH_INSTANCES}/{{instance_id}}/decisions",
    summary="人工改变当前决策（admin；不能放行 resume_monitoring）",
)
def decide_managed_instance(
    request: Request,
    command: AdminDecisionRequest,
    sess: Annotated[Session, Depends(get_session)],
    instance_id: Annotated[UUID, Path()],
) -> Any:
    """Hold, recover or demand a user for the *current* episode.

    ``requestId`` makes a retried command idempotent, ``expectedRevision`` is an
    optimistic lock, and ``incidentId``/``expectedBindingEpoch`` must match the
    live context exactly. There is deliberately no operator path to
    ``resume_monitoring`` (§9.5).
    """
    refused = _require_admin(request)
    if refused is not None:
        return refused
    try:
        payload = service.manual_decision(
            sess,
            instance_id=instance_id,
            command=command,
            now=datetime.now(UTC),
        )
        sess.commit()
    except StatusServiceError as exc:
        sess.rollback()
        return _service_error_response(exc, request)
    return JSONResponse(status_code=200, content=payload, headers=_NO_STORE)


__all__ = [
    "MAX_BODY_BYTES",
    "PATH_INSTANCES",
    "PATH_REPORT",
    "post_status_report",
    "router",
]
