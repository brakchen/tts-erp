"""FastAPI app factory for tts-erp v2.

Composition rule: include_router + middleware only. Business logic lives
in ``tts_erp_v2/api/v2/*.py``.

Middleware order (FastAPI ``add_middleware`` wraps in REVERSE order,
so the LAST ``add_middleware`` call is the OUTERMOST layer):

    registration order = RateLimit → Auth → CORS → AccessLog
    resulting order    = AccessLog → CORS → Auth → RateLimit

AccessLog is the outermost layer on purpose: it needs to see the
FINAL response status and total request duration (after RateLimit
short-circuits, after Auth sets the role context). It logs ONE
structured line per request to stdout with the NGINX-equivalent
context (real client IP via X-Forwarded-For, X-Forwarded-Proto,
X-Forwarded-Prefix) plus the auth state, so operators don't have
to ``docker exec nginx-gw cat /var/log/nginx/access.log`` to
correlate a browser request to its handler outcome. See
``tts_erp_v2/middleware/access_log.py`` for the format.

Public routes:
- ``GET /healthz`` — liveness probe (no auth)
- ``GET /endpoints`` — operator index of v2 routes (no auth)
- ``GET /openapi.json`` / ``/docs`` — Swagger UI (no auth)

All other routes go through v2 routers and require auth.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from tts_erp_v2.api.v2 import (
    admin,
    analytics,
    auth,
    commerce,
    fx,
    intercept,
    linkage,
    llm_context,
    oauth,
    order_sync,
    pages,
    reporting,
    spu_images,
    tiktok_shop,
)
from tts_erp_v2.middleware.access_log import AccessLogMiddleware
from tts_erp_v2.middleware.auth import AuthMiddleware
from tts_erp_v2.middleware.rate_limit import RateLimitMiddleware


def _build_routes(app: FastAPI) -> None:
    app.include_router(commerce.router)
    app.include_router(linkage.router)
    app.include_router(reporting.router)
    app.include_router(
        fx.router
    )  # cached exchange rates + local conversion (readonly; never dials upstream)
    app.include_router(spu_images.router)  # SPU image upload (presigned MinIO)
    app.include_router(pages.router)
    app.include_router(llm_context.router)
    app.include_router(
        tiktok_shop.router
    )  # TikTok Shop Partner API read-through (live, no DB caching)
    app.include_router(
        oauth.router
    )  # TikTok seller OAuth onboarding (authorize + public callback)
    app.include_router(auth.router)  # browser login + session cookie
    # Admin-only operational endpoints (rate-limit hot-reload, etc.).
    # All paths under /v2/admin are gated to admin role both by the
    # auth middleware (default deny for unknown paths) and by an
    # explicit ``require_role_at_least(request, "admin")`` in the
    # handler for defense-in-depth. See tts_erp_v2/api/v2/admin.py.
    app.include_router(admin.router, prefix="/v2/admin")
    # analytics (Chrome extension ad-data upload + coverage + cursor)。
    # ⚠ 命名历史：URL 前缀 /v2/analytics/sync/* 是 Chrome 扩展的 stable 契约，
    # 改路径需扩展同步发版，故保留 analytics 命名（AGENTS.md §9.1）。
    # 实际数据 2026-09-11 起全部写入 plugin schema（plugin.ad_* 等），
    # 存储层已迁到 tts_erp_v2/plugin/ads/repository.py；
    # 路由 handler 仍在 tts_erp_v2/api/v2/analytics.py（仅文件名层面残留）。
    # Auth + rate-limit 继承父 app 中间件栈；handler 读
    # `request.scope["api_key_hash"]` / `request.scope["api_key_scopes"]`。
    app.include_router(analytics.router)
    # HTTP 请求拦截配置管理和数据接收
    # 详见 tech-doc/intercept-design.md
    app.include_router(intercept.router)
    # Chrome 扩展订单/物流/结算数据同步（readwrite；与 analytics 同级）。
    # 详见 tech-doc/chrome-ext-order-sync-design.md。
    app.include_router(order_sync.router)
    # SPU 实际 ROI 看板主表(GET /v2/analytics/spu-roi, readonly)——
    # 读 plugin.ad_* 表；URL 保持 /v2/analytics/ 前缀与 sync 端点同域。
    # 模块 tts_erp_v2/analytics/spu_roi.py 名称同理为历史残留。
    app.include_router(analytics.roi_router)
    # SPU ROI 钻取面板四端点（D6 拍板：每 tab 一懒加载端点）
    app.include_router(analytics.drilldown_router)

    # Operator-console static assets (vendor/bootstrap.min.css / js/console.js). Auth is
    # readonly-level via the "/static/" prefix in middleware/auth.py —
    # any authenticated session passes; anonymous requests get 401.
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )


def build_app() -> FastAPI:
    """Construct the v2 FastAPI app.

    Idempotent: tests call this per TestClient. Production uses uvicorn
    with ``tts_erp_v2.app:build_app()`` (the factory; uvicorn picks the
    returned instance).
    """
    app = FastAPI(
        title="tts-erp v2",
        version="2.0.0",
        description="Refactored tts-erp API — see tech-doc/refactor-tech-plan-v2.md",
    )

    # --- Middleware registration (LAST = OUTERMOST) ---
    # Innermost first, then Auth (next layer out), then CORS, then
    # AccessLog on the outside so it sees the final status + total
    # duration. Per AGENTS.md §9.3, Auth must sit between RateLimit
    # and the handler so the limiter can bucket by authenticated key.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware)
    # CORS: default DENY (empty allow-origin). Operators set
    # TTS_ERP_CORS_ALLOW_ORIGINS to a comma-list, or the token "wildcard".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_parse_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "X-API-Key", "Content-Type"],
        max_age=600,
    )
    # Outermost: one structured line per request to stdout. The
    # matching uvicorn default access log is suppressed at the
    # systemd unit (--no-access-log) so we get exactly one line per
    # request with every field an operator needs.
    app.add_middleware(AccessLogMiddleware)

    _build_routes(app)
    _register_public_routes(app)
    return app


def _parse_cors_origins() -> list[str]:
    raw = _env_cors()
    if not raw:
        return []
    if raw.strip() == "wildcard":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def _env_cors() -> str:
    import os

    return os.environ.get("TTS_ERP_CORS_ALLOW_ORIGINS", "")


def _register_public_routes(app: FastAPI) -> None:
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "service": "tts-erp-v2",
                "auth_mode": _env_auth_mode(),
            }
        )

    @app.get("/endpoints", include_in_schema=True)
    def endpoints_index() -> JSONResponse:
        """Operator index — v2 routes only.

        The legacy public ``/db/*``, ``/orders/*``, ``/sync/*``, and
        ``/token/*`` endpoints are intentionally NOT exposed in v2
        (hard switch — see tech-doc/refactor-tech-plan-v2 §6).

        Walks ``app.routes`` recursively so routers mounted via
        ``include_router`` (commerce / linkage / reporting / pages /
        llm_context / auth / analytics_sync) all surface. FastAPI 0.141
        wraps every ``include_router`` child in a lazy ``_IncludedRouter``
        proxy that has no ``path`` attribute — a flat walk would
        silently drop every business route. See
        ``tests/api/test_endpoints_index.py`` for the regression
        guard.
        """
        items = sorted(_walk_v2_routes(app.routes), key=lambda x: x["path"])
        return JSONResponse({"endpoints": items, "count": len(items)})


def _env_auth_mode() -> str:
    import os

    return os.environ.get("TTS_ERP_AUTH_MODE", "off")


def _walk_v2_routes(routes, prefix=""):
    """Yield ``{path, methods, name}`` for every routable entry under v2.

    Recurses into ``_IncludedRouter.original_router.routes`` so business
    routers mounted via ``include_router`` (``commerce``, ``linkage``,
    ``reporting``, ``pages``, ``llm_context``, ``auth``,
    ``analytics_sync``) all surface in the operator-facing /endpoints
    index. FastAPI 0.141 wraps every include_router child in a lazy
    ``_IncludedRouter`` proxy that has no ``path`` attribute — a flat
    walk would silently drop every business route.

    Tracks the prefix stack: ``include_router(router, prefix="/foo")``
    stores ``prefix="/foo"`` on ``_IncludedRouter.include_context``
    but leaves the child APIRoute ``.path`` un-prefixed. We re-apply
    the prefix here so the operator sees the *public* path
    (``/v1/analytics/sync/cursor``) rather than the *internal* one
    (``/cursor``).

    The recursion lives here (not on the app object) so it stays a pure
    helper that tests can import without booting the whole app.
    """
    for r in routes:
        # FastAPI 0.141+ lazy router wrapper from include_router().
        if hasattr(r, "original_router") and hasattr(r.original_router, "routes"):
            ctx = getattr(r, "include_context", None)
            extra = getattr(ctx, "prefix", "") if ctx is not None else ""
            child_prefix = prefix + extra
            yield from _walk_v2_routes(r.original_router.routes, child_prefix)
            continue
        path = getattr(r, "path", None) or getattr(r, "path_format", None)
        if not path:
            continue
        methods = getattr(r, "methods", None) or set()
        if not methods:
            continue
        yield {
            "path": prefix + path,
            "methods": sorted(methods - {"HEAD"}),
            "name": getattr(r, "name", None),
        }


# Module-level app for ``uvicorn tts_erp_v2.app:app``.
# Eagerly build so uvicorn gets a real FastAPI instance — a lazy
# ``__getattr__`` shim works for test imports (``from app import app``)
# but uvicorn's middleware stack re-imports / re-attributes the module
# attr and ends up seeing ``None`` (the annotation default) instead of
# the FastAPI callable, which crashes the ASGI dispatch with
# ``TypeError: 'NoneType' object is not callable``.
app: FastAPI = build_app()
