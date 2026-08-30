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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from tts_erp_v2.api.v2 import auth, commerce, linkage, llm_context, pages, reporting
from tts_erp_v2.middleware.access_log import AccessLogMiddleware
from tts_erp_v2.middleware.auth import AuthMiddleware
from tts_erp_v2.middleware.rate_limit import RateLimitMiddleware


def _build_routes(app: FastAPI) -> None:
    app.include_router(commerce.router)
    app.include_router(linkage.router)
    app.include_router(reporting.router)
    app.include_router(pages.router)
    app.include_router(llm_context.router)
    app.include_router(auth.router)  # browser login + session cookie


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
        """
        items = []
        for r in app.routes:
            path = getattr(r, "path", None)
            if not path:
                continue
            methods = getattr(r, "methods", None) or set()
            if not methods:
                continue
            items.append(
                {
                    "path": path,
                    "methods": sorted(methods - {"HEAD"}),
                    "name": getattr(r, "name", None),
                }
            )
        items.sort(key=lambda x: x["path"])
        return JSONResponse({"endpoints": items, "count": len(items)})


def _env_auth_mode() -> str:
    import os

    return os.environ.get("TTS_ERP_AUTH_MODE", "off")


# Module-level app for ``uvicorn tts_erp_v2.app:app``.
# Eagerly build so uvicorn gets a real FastAPI instance — a lazy
# ``__getattr__`` shim works for test imports (``from app import app``)
# but uvicorn's middleware stack re-imports / re-attributes the module
# attr and ends up seeing ``None`` (the annotation default) instead of
# the FastAPI callable, which crashes the ASGI dispatch with
# ``TypeError: 'NoneType' object is not callable``.
app: FastAPI = build_app()
