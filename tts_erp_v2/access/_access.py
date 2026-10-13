"""Typed access decisions independent of ASGI rendering."""

from __future__ import annotations

import os
from typing import Literal

from anyio.to_thread import run_sync

from tts_erp_v2.access._credentials import authenticate_key, hash_key
from tts_erp_v2.access._policy import (
    MONITOR_CONFIG_DELIVERY_PATH,
    is_monitor_config_delivery_route,
    required_role,
)
from tts_erp_v2.access._types import (
    AccessDecision,
    AccessEffect,
    AccessGrant,
    AccessRequest,
    ApiKeyPurpose,
    AuthMode,
    Credential,
    UserCredential,
)
from tts_erp_v2.access._types import (
    Role as AccessRole,
)
from tts_erp_v2.accounts.pages import required_page_permission
from tts_erp_v2.accounts.service import authenticate_session_cookie

_MISSING_CREDENTIAL = (
    "missing bearer token (Authorization: Bearer <key> or X-API-Key: <key>)"
)
_INVALID_CREDENTIAL = "invalid, disabled or expired api key"
_STORE_UNAVAILABLE = "auth store unavailable"
_MONITOR_KEY_REQUIRED = "requires a shop-bound monitor_config api key"
_MONITOR_KEY_ROUTE_RESTRICTED = (
    "monitor_config api key is limited to " + MONITOR_CONFIG_DELIVERY_PATH
)


def _grant(
    mode: AuthMode,
    credential: Credential | UserCredential | None = None,
    *,
    auth_method: Literal["cookie", "bearer"] | None = None,
    bypass: bool = False,
) -> AccessGrant:
    user = credential if isinstance(credential, UserCredential) else None
    api_key = credential if isinstance(credential, Credential) else None
    return AccessGrant(
        mode=mode,
        role=credential.role if credential else None,
        key_hash=credential.key_hash if credential else None,
        scopes=credential.scopes if isinstance(credential, Credential) else (),
        purpose=api_key.purpose if api_key else ApiKeyPurpose.GENERAL,
        shop_pk=api_key.shop_pk if api_key else None,
        auth_method=auth_method,
        bypass=bypass,
        user=user,
    )


def _purpose_denied(
    request: AccessRequest,
    credential: Credential | UserCredential | None,
) -> str | None:
    """Cross-route rejection for restricted api keys (docs §4.2 约束 2/4).

    ``None`` means the credential is usable on this route.
    """

    api_key = credential if isinstance(credential, Credential) else None
    is_monitor_key = (
        api_key is not None and api_key.purpose is ApiKeyPurpose.MONITOR_CONFIG
    )
    if is_monitor_config_delivery_route(request.method, request.route_path):
        # 只有绑定到单个 shop_pk 的 monitor key 才是下发授权；普通 key 与
        # 浏览器登录 cookie 都不是（管理能力走独立管理接口）。
        if api_key is not None and api_key.purpose is ApiKeyPurpose.MONITOR_CONFIG:
            if api_key.shop_pk is None:
                return _MONITOR_KEY_REQUIRED
            return None
        return _MONITOR_KEY_REQUIRED
    if is_monitor_key:
        # role=readonly 不得让 monitor key 读到 ERP 其他只读数据或管理接口。
        return _MONITOR_KEY_ROUTE_RESTRICTED
    return None


async def evaluate_access(
    request: AccessRequest,
    *,
    mode: AuthMode,
) -> AccessDecision:
    """Evaluate one canonical request and return a rendering-neutral decision."""

    needed = required_role(request.method, request.route_path)
    monitor_delivery = is_monitor_config_delivery_route(
        request.method, request.route_path
    )
    if mode is AuthMode.OFF:
        if monitor_delivery:
            # 匿名放行不得成为释放店铺配置的通道（docs §4.2 约束 4）。
            return AccessDecision(
                effect=AccessEffect.DENY,
                grant=_grant(mode, bypass=True),
                required_role=needed,
                status=403,
                detail=_MONITOR_KEY_REQUIRED,
            )
        return AccessDecision(
            effect=AccessEffect.ALLOW,
            grant=_grant(mode, bypass=True),
            required_role=needed,
        )
    if needed is None:
        return AccessDecision(
            effect=AccessEffect.ALLOW,
            grant=_grant(mode),
        )

    credential: Credential | UserCredential | None = None
    auth_method: Literal["cookie", "bearer"] | None = None
    auth_state = "none"
    attempted_key = request.bearer_key or request.api_key

    if request.session_cookie:
        try:
            credential = await run_sync(
                authenticate_session_cookie, request.session_cookie
            )
        except Exception:  # noqa: BLE001 — map auth-store failure to outcome
            return _store_unavailable(mode, needed)
        if credential is not None:
            auth_state = "credential"
            auth_method = "cookie"
        else:
            auth_state = "invalid"

    if credential is None and attempted_key:
        try:
            credential = await run_sync(authenticate_key, attempted_key)
        except Exception:  # noqa: BLE001 — map auth-store failure to outcome
            return _store_unavailable(mode, needed)
        if credential is not None:
            auth_state = "credential"
            auth_method = "bearer"
        elif auth_state == "none":
            auth_state = "invalid"

    if (
        isinstance(credential, Credential)
        and request.route_path.startswith("/v2/video-publish")
        and os.environ.get("TTS_ERP_VIDEO_PUBLISH_ALLOW_READWRITE_API_KEYS") != "1"
    ):
        needed = AccessRole.ADMIN
    grant = _grant(
        mode,
        credential,
        auth_method=auth_method,
        bypass=mode is AuthMode.SHADOW,
    )
    denied_status: int | None = None
    detail: str | None = None
    if auth_state == "none":
        denied_status = 401
        detail = _MISSING_CREDENTIAL
    elif auth_state == "invalid":
        denied_status = 401
        detail = _INVALID_CREDENTIAL
    elif credential is not None and not grant.allows(needed):
        denied_status = 403
        detail = f"requires {needed.value}"
    else:
        # 页面权限只约束 session user。API key 不持有 page permission；
        # video-publish 的 admin-first API-key gate 已在上方单独收紧。
        needed_page = required_page_permission(request.route_path)
        if (
            needed_page is not None
            and isinstance(credential, UserCredential)
            and needed_page not in credential.pages
        ):
            denied_status = 403
            detail = f"requires {needed_page}"

    purpose_detail = _purpose_denied(request, credential)
    if denied_status is None and purpose_detail is not None:
        denied_status = 403
        detail = purpose_detail

    if denied_status is None:
        return AccessDecision(
            effect=AccessEffect.ALLOW,
            grant=grant,
            required_role=needed,
        )
    if purpose_detail is not None and mode is not AuthMode.ENFORCE:
        # 受限下发边界不放宽：shadow 模式不得把拒绝降级为放行
        # （docs/monitor-config-sync.md §4.2 约束 4）。
        return AccessDecision(
            effect=AccessEffect.DENY,
            grant=grant,
            required_role=needed,
            status=403,
            detail=purpose_detail,
        )
    if mode is AuthMode.SHADOW:
        return AccessDecision(
            effect=AccessEffect.SHADOW_ALLOW,
            grant=grant,
            required_role=needed,
            status=denied_status,
            detail=detail,
            challenge=denied_status == 401,
        )

    from tts_erp_v2.middleware import rate_limit

    bucket_id = _denied_bucket(
        request=request,
        credential=credential,
        attempted_key=attempted_key,
    )
    retry_after = rate_limit.shared_hit(bucket_id)
    if retry_after is not None:
        counter = rate_limit.shared_counter()
        return AccessDecision(
            effect=AccessEffect.RATE_LIMITED,
            grant=grant,
            required_role=needed,
            status=429,
            detail="too many requests",
            retry_after=retry_after,
            rate_limit=counter.limit,
        )

    effect = (
        AccessEffect.REDIRECT
        if denied_status == 401
        and request.method.upper() == "GET"
        and request.accepts_html
        else AccessEffect.DENY
    )
    return AccessDecision(
        effect=effect,
        grant=grant,
        required_role=needed,
        status=302 if effect is AccessEffect.REDIRECT else denied_status,
        detail=detail,
        challenge=effect is AccessEffect.DENY and denied_status == 401,
    )


def _store_unavailable(mode: AuthMode, needed) -> AccessDecision:
    if mode is AuthMode.SHADOW:
        return AccessDecision(
            effect=AccessEffect.SHADOW_ALLOW,
            grant=_grant(mode, bypass=True),
            required_role=needed,
            status=503,
            detail=_STORE_UNAVAILABLE,
        )
    return AccessDecision(
        effect=AccessEffect.UNAVAILABLE,
        grant=_grant(mode),
        required_role=needed,
        status=503,
        detail=_STORE_UNAVAILABLE,
    )


def _denied_bucket(
    *,
    request: AccessRequest,
    credential: Credential | UserCredential | None,
    attempted_key: str | None,
) -> str:
    if credential is not None:
        return credential.key_hash
    if attempted_key:
        return hash_key(attempted_key)
    return f"ip:{request.client_ip}"
