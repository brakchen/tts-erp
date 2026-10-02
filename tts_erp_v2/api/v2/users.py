"""/v2/users, /v2/roles — 用户与角色管理 API（设计 §9.1）。

均需 `page:users` 权限（access 模块按路由判定）+ api_tier=admin（路由矩阵
默认拒绝）。管理页两个页签（用户 / 角色权限）的配套接口。
"""

from __future__ import annotations

import logging
import sys

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tts_erp_v2.accounts import service
from tts_erp_v2.accounts.pages import permission_catalog
from tts_erp_v2.accounts.passwords import PasswordPolicyError
from tts_erp_v2.api.deps import SessionDep

router = APIRouter(tags=["users"])

# 审计日志：账号/角色变更全部落这里（操作者、目标、动作、IP），不记密码。
audit_logger = logging.getLogger("tts_erp_v2.accounts.audit")
audit_logger.setLevel(logging.INFO)
if not any(
    isinstance(h, logging.StreamHandler) and h.stream is sys.stdout
    for h in audit_logger.handlers
):
    _stdout = logging.StreamHandler(sys.stdout)
    _stdout.setFormatter(logging.Formatter("%(message)s"))
    audit_logger.addHandler(_stdout)
    audit_logger.propagate = False


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    displayName: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
    roles: list[str] = Field(default_factory=list)


class UserPatch(BaseModel):
    displayName: str | None = Field(default=None, max_length=64)
    status: str | None = None  # active | disabled
    roles: list[str] | None = None


class PasswordReset(BaseModel):
    newPassword: str = Field(min_length=1, max_length=128)


class RoleCreate(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    apiTier: str = "readwrite"
    permissions: list[str] = Field(default_factory=list)


class RolePatch(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    apiTier: str | None = None
    permissions: list[str] | None = None


def _actor(request: Request) -> str:
    return str(request.scope.get("username") or request.scope.get("api_key_hash") or "?")


def _audit(request: Request, action: str, target: str, **extra: object) -> None:
    parts = " ".join(f"{k}={v}" for k, v in extra.items())
    ip = request.client.host if request.client else "?"
    audit_logger.info(
        "action=%s target=%s actor=%s ip=%s %s", action, target, _actor(request), ip, parts
    )


def _error(exc: Exception, code: int = status.HTTP_400_BAD_REQUEST) -> JSONResponse:
    return JSONResponse(status_code=code, content={"detail": str(exc)})


# ── users ───────────────────────────────────────────────────────────


@router.get("/v2/users")
def list_users(session: SessionDep) -> dict:
    return {"users": service.list_users(session)}


@router.post("/v2/users", status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, request: Request, session: SessionDep) -> Response:
    try:
        user = service.create_user(
            session,
            username=body.username,
            display_name=body.displayName,
            password=body.password,
            roles=body.roles,
            actor=_actor(request),
        )
    except (service.AccountError, PasswordPolicyError) as exc:
        return _error(exc)
    _audit(request, "user.create", user.username, roles=",".join(body.roles))
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={"id": user.id, "username": user.username},
    )


@router.get("/v2/users/{user_id}")
def show_user(user_id: int, session: SessionDep) -> Response:
    try:
        return JSONResponse(content=service.get_user_detail(session, user_id))
    except service.NotFoundError as exc:
        return _error(exc, status.HTTP_404_NOT_FOUND)


@router.patch("/v2/users/{user_id}")
def patch_user(
    user_id: int, body: UserPatch, request: Request, session: SessionDep
) -> Response:
    actor_id = request.scope.get("user_id")
    try:
        if body.displayName is not None:
            service.update_user(session, user_id=user_id, display_name=body.displayName)
        if body.roles is not None:
            service.set_user_roles(session, user_id=user_id, roles=body.roles)
            _audit(request, "user.roles", str(user_id), roles=",".join(body.roles))
        if body.status is not None:
            service.set_user_status(
                session, user_id=user_id, status=body.status, actor_id=actor_id
            )
            _audit(request, "user.status", str(user_id), status=body.status)
    except (service.AccountError, service.NotFoundError) as exc:
        code = (
            status.HTTP_404_NOT_FOUND
            if isinstance(exc, service.NotFoundError)
            else status.HTTP_400_BAD_REQUEST
        )
        return _error(exc, code)
    return JSONResponse(content={"ok": True})


@router.post("/v2/users/{user_id}/password")
def reset_password(
    user_id: int, body: PasswordReset, request: Request, session: SessionDep
) -> Response:
    try:
        service.reset_password(
            session,
            user_id=user_id,
            password=body.newPassword,
            actor=_actor(request),
        )
    except (service.AccountError, PasswordPolicyError, service.NotFoundError) as exc:
        code = (
            status.HTTP_404_NOT_FOUND
            if isinstance(exc, service.NotFoundError)
            else status.HTTP_400_BAD_REQUEST
        )
        return _error(exc, code)
    _audit(request, "user.reset_password", str(user_id))
    return JSONResponse(content={"ok": True})


@router.get("/v2/users/{user_id}/sessions")
def list_sessions(user_id: int, session: SessionDep) -> Response:
    try:
        return JSONResponse(
            content={"sessions": service.list_user_sessions(session, user_id)}
        )
    except service.NotFoundError as exc:
        return _error(exc, status.HTTP_404_NOT_FOUND)


@router.delete("/v2/users/{user_id}/sessions/{session_id}")
def revoke_one_session(
    user_id: int, session_id: int, request: Request, session: SessionDep
) -> Response:
    return _revoke(request, session, user_id, session_id)


@router.delete("/v2/users/{user_id}/sessions")
def revoke_all_sessions(
    user_id: int, request: Request, session: SessionDep
) -> Response:
    return _revoke(request, session, user_id, None)


def _revoke(
    request: Request, session: SessionDep, user_id: int, session_id: int | None
) -> Response:
    try:
        count = service.revoke_session_for_user(
            session, user_id=user_id, session_id=session_id
        )
    except (service.NotFoundError, service.AccountError) as exc:
        code = (
            status.HTTP_404_NOT_FOUND
            if isinstance(exc, service.NotFoundError)
            else status.HTTP_400_BAD_REQUEST
        )
        return _error(exc, code)
    _audit(request, "session.revoke", str(user_id), count=count)
    return JSONResponse(content={"ok": True, "revoked": count})


# ── roles ───────────────────────────────────────────────────────────


@router.get("/v2/roles")
def list_roles(session: SessionDep) -> dict:
    return {
        "roles": service.list_roles(session),
        "allPermissions": permission_catalog(),
    }


@router.post("/v2/roles", status_code=status.HTTP_201_CREATED)
def create_role(body: RoleCreate, request: Request, session: SessionDep) -> Response:
    try:
        role = service.create_role(
            session,
            code=body.code,
            name=body.name,
            api_tier=body.apiTier,
            permissions=body.permissions,
        )
    except service.AccountError as exc:
        return _error(exc)
    _audit(request, "role.create", role.code)
    return JSONResponse(
        status_code=status.HTTP_201_CREATED, content={"code": role.code}
    )


@router.patch("/v2/roles/{code}")
def patch_role(
    code: str, body: RolePatch, request: Request, session: SessionDep
) -> Response:
    try:
        service.update_role(
            session,
            code=code,
            name=body.name,
            api_tier=body.apiTier,
            permissions=body.permissions,
        )
    except (service.AccountError, service.NotFoundError) as exc:
        code_ = (
            status.HTTP_404_NOT_FOUND
            if isinstance(exc, service.NotFoundError)
            else status.HTTP_400_BAD_REQUEST
        )
        return _error(exc, code_)
    _audit(request, "role.update", code)
    return JSONResponse(content={"ok": True})


@router.delete("/v2/roles/{code}")
def delete_role(code: str, request: Request, session: SessionDep) -> Response:
    try:
        service.delete_role(session, code=code)
    except (service.AccountError, service.NotFoundError) as exc:
        code_ = (
            status.HTTP_404_NOT_FOUND
            if isinstance(exc, service.NotFoundError)
            else status.HTTP_400_BAD_REQUEST
        )
        return _error(exc, code_)
    _audit(request, "role.delete", code)
    return JSONResponse(content={"ok": True})
