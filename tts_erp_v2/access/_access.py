"""Typed access decisions independent of ASGI rendering."""

from __future__ import annotations

import os
from typing import Literal

from anyio.to_thread import run_sync

from tts_erp_v2.access._credentials import authenticate_key, hash_key
from tts_erp_v2.access._policy import required_role
from tts_erp_v2.access._types import (
    AccessDecision,
    AccessEffect,
    AccessGrant,
    AccessRequest,
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


def _grant(
    mode: AuthMode,
    credential: Credential | UserCredential | None = None,
    *,
    auth_method: Literal["cookie", "bearer"] | None = None,
    bypass: bool = False,
) -> AccessGrant:
    user = credential if isinstance(credential, UserCredential) else None
    return AccessGrant(
        mode=mode,
        role=credential.role if credential else None,
        key_hash=credential.key_hash if credential else None,
        scopes=credential.scopes if isinstance(credential, Credential) else (),
        auth_method=auth_method,
        bypass=bypass,
        user=user,
    )


async def evaluate_access(
    request: AccessRequest,
    *,
    mode: AuthMode,
) -> AccessDecision:
    """Evaluate one canonical request and return a rendering-neutral decision."""

    needed = required_role(request.method, request.route_path)
    if mode is AuthMode.OFF:
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

    if denied_status is None:
        return AccessDecision(
            effect=AccessEffect.ALLOW,
            grant=grant,
            required_role=needed,
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
