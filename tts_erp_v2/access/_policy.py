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
    "/v2/analytics/spu-profit-deterioration",
    "/v2/config/",
    "/v2/tiktok-shop/",
    "/v2/intercept/configs",
    "/v2/intercept/requests",
    "/v2/video-publish/",
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
    "/v2/analytics/spu-profit-deterioration",
    "/v2/intercept/config",
    "/v2/intercept/requests/stats",
    "/v2/oauth/tiktok/onboard",
    "/v2/oauth/tiktok/authorize",
    # 改自己的密码：任何登录用户可调（handler 自校验会话 cookie，
    # API key 凭据无会话 → 401）；不落入默认 ADMIN。
    "/v2/auth/change-password",
}

#: 插件读取店铺 monitor 配置的唯一路径（docs/monitor-config-sync.md §6.1）。
#: 该路由的最低角色是 readonly，但真正的准入条件是 access 层要求的
#: “绑定到单个 shop_pk 的 monitor_config 专用 key”，而非角色。
MONITOR_CONFIG_DELIVERY_PATH = "/v2/monitor/config"

#: monitor 管理面（§6.3）：草稿、发布、强制覆盖均为 readwrite。
#: monitor 专用 key 不能调用它们 —— ``_access`` 的用途层跨路由拒绝已在
#: §4.2 约束 2/4 生效，这里只声明角色下限。
MONITOR_ADMIN_PATH_PREFIX = "/v2/monitor/admin"


def is_monitor_config_delivery_route(method: str, route_path: str) -> bool:
    """True only for the shop-scoped monitor config read.

    Phase A owns the classification only; the route handler arrives in Phase B.
    """

    return (
        method.upper() == "GET"
        and route_path.split("?", 1)[0] == MONITOR_CONFIG_DELIVERY_PATH
    )


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
    if normalized_method == "POST" and path.startswith("/v2/video-publish/tasks"):
        return Role.READWRITE
    if path == "/v2/video-publish/config" or path.startswith("/v2/video-publish/tasks"):
        return Role.READONLY
    if normalized_method == "POST" and path in _READWRITE_EXACT:
        return Role.READWRITE
    if path == MONITOR_CONFIG_DELIVERY_PATH and normalized_method == "GET":
        # 角色只是下限；用途/店铺绑定在 ``_access`` 层单独收紧。
        return Role.READONLY
    if path.startswith(MONITOR_ADMIN_PATH_PREFIX):
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
