"""Route classification for the access policy module."""

from __future__ import annotations

from tts_erp_v2.access._types import Role

_EXEMPT_PATHS = {
    "/healthz",
    "/endpoints",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/docs/oauth2-redirect",
    "/v2/auth/login",
    "/v2/auth/logout",
    "/v2/auth/me",
    "/v2/oauth/tiktok/callback",
    "/static/",
}

_READONLY_PREFIXES = (
    "/v2/commerce/",
    "/v2/reporting/",
    "/v2/fx/",
    "/v2/sync/",
    "/v2/pages/",
    "/v2/spu-images/",
    "/v2/analytics/spu-roi/",
    "/v2/config/",
    "/v2/tiktok-shop/",
    "/v2/intercept/configs",
    "/v2/intercept/requests",
)

_READWRITE_EXACT = {
    "/v2/reporting/manual-costs",
}

_READWRITE_PREFIXES = (
    "/v2/intercept/configs",
    "/v2/intercept/sync",
)

_READONLY_EXACT = {
    "/v2/llm-context",
    "/v2/spu-images",
    "/v2/analytics/spu-roi",
    "/v2/intercept/config",
    "/v2/intercept/requests/stats",
    "/v2/oauth/tiktok/onboard",
    "/v2/oauth/tiktok/authorize",
    # 改自己的密码：任何登录用户可调（handler 自校验会话 cookie，
    # API key 凭据无会话 → 401）；不落入默认 ADMIN。
    "/v2/auth/change-password",
}


def required_role(method: str, route_path: str) -> Role | None:
    """Return the minimum role for a route-relative path."""

    normalized_method = method.upper()
    path = route_path.split("?", 1)[0]
    if path in _EXEMPT_PATHS or path.startswith("/static/"):
        return None
    if path.startswith(("/v2/analytics/sync", "/v2/order-sync")):
        return Role.READWRITE
    if path == "/v2/pages/runtime-configs":
        return Role.READWRITE
    if path == "/v2/config/runtime" or path.startswith("/v2/config/runtime/"):
        return Role.READWRITE
    if path.startswith("/v2/admin/shops/"):
        return Role.READWRITE
    if (
        normalized_method == "POST"
        and path.startswith("/v2/admin/sync-jobs/")
        and path.endswith("/trigger")
    ):
        return Role.READWRITE
    if normalized_method == "PATCH" and path.startswith("/v2/reporting/focused-spus/"):
        return Role.READWRITE
    if path.startswith("/miaoshou/callback"):
        return None
    if normalized_method == "POST" and path in _READWRITE_EXACT:
        return Role.READWRITE
    if path.startswith(_READWRITE_PREFIXES):
        return Role.READWRITE
    if normalized_method == "POST" and path == "/v2/spu-images/upload-url":
        return Role.READWRITE
    if (
        normalized_method == "POST"
        and path.startswith("/v2/spu-images/")
        and path.endswith("/confirm")
    ):
        return Role.READWRITE
    if normalized_method == "DELETE" and path.startswith("/v2/spu-images/"):
        return Role.READWRITE
    if path in _READONLY_EXACT or path.startswith(_READONLY_PREFIXES):
        return Role.READONLY
    return Role.ADMIN
