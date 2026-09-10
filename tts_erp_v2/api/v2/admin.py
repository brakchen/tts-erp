"""Admin-only operational endpoints.

These are operator-facing endpoints that mutate cross-cutting runtime
state (rate-limit singleton, etc.) and are not part of any business
domain. The rate-limit endpoint is the only one today; future admin
ops (e.g., feature-flag toggles, runtime config reload) can be added
here.

All endpoints in this module are gated to ``admin`` role both at the
middleware (see ``tts_erp_v2/middleware/auth.py::required_role()`` —
unknown ``/v2/admin/...`` paths default to admin-required) and via
``require_role_at_least(request, "admin")`` for defense-in-depth, same
pattern as ``tts_erp_v2/api/v2/linkage.py::overrides``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from tts_erp_v2.api.deps import require_role_at_least
from tts_erp_v2.middleware.rate_limit import (
    ENV_VAR_NAME,
    reset_shared,
    shared_config,
)

router = APIRouter()


# ─── Schemas ────────────────────────────────────────────────────────────


class ResetRateLimitBody(BaseModel):
    """POST body for ``/v2/admin/reset-rate-limit``.

    All fields are optional. With an empty body ``{}`` the endpoint
    re-reads the current ``TTS_ERP_RATE_LIMIT_PER_MIN`` env var and
    clears the per-key buckets — this is the canonical "hot-reload"
    after editing ``.env`` without restarting the service.
    """

    new_limit: int | None = Field(
        default=None,
        ge=1,
        le=1_000_000,
        description=(
            "New per-key per-minute limit. Omit (or pass null) to re-read "
            "from the ``TTS_ERP_RATE_LIMIT_PER_MIN`` env var instead. "
            "1_000_000 = ~16 666 QPS — the cap is a sanity bound; raise "
            "this if you have a legitimate higher-throughput tenant."
        ),
    )
    reset_buckets: bool = Field(
        default=True,
        description=(
            "Clear all per-key sliding-window buckets before applying the "
            "new limit. Default ``true`` — keys that were throttled at "
            "429 get a fresh 60-second window. Pass ``false`` to preserve "
            "current counts (e.g., if you only want to *raise* the cap "
            "without invalidating in-flight state)."
        ),
    )


class ResetRateLimitResponse(BaseModel):
    """Response shape for the reset endpoint.

    Every value is also written to the access log via the standard
    middleware, so ops can grep ``reset_by=abcdef123456`` to find the
    admin that triggered a particular change.
    """

    old_limit: int | None
    new_limit: int
    window_s: float
    buckets_cleared: int
    active_buckets: int
    reset_buckets: bool
    limit_source: str  # "override" if new_limit given, else "env"
    env_var_source: str  # always ENV_VAR_NAME today; reserved for future
    reset_by: str  # admin's key hash prefix (12 hex chars); audit trail
    reset_by_role: str
    reset_at: datetime


class RateLimitConfigResponse(BaseModel):
    """Response shape for ``GET /v2/admin/rate-limit`` (read-only)."""

    limit: int | None  # None if no authenticated request has been served yet
    window_s: float | None
    active_buckets: int | None
    env_var_name: str
    env_var_current_value: str | None  # current env-var raw string, for diff
    env_var_effective_value: int | None  # what the next reset_shared(None) would use
    middleware_initialized: bool


# ─── Handlers ──────────────────────────────────────────────────────────


@router.get(
    "/rate-limit",
    response_model=RateLimitConfigResponse,
    summary="Read current per-key rate-limit configuration (admin only).",
)
def get_rate_limit(request: Request) -> RateLimitConfigResponse:
    """Return the in-process rate-limit singleton's current state and
    the underlying env-var values so an operator can see what a reset
    would do *before* triggering it.

    No side effects. Safe to poll.
    """
    require_role_at_least(request, "admin")
    config = shared_config()
    env_raw = os.environ.get(ENV_VAR_NAME)
    env_effective: int | None = None
    if env_raw is not None:
        try:
            env_effective = int(env_raw)
        except ValueError:
            env_effective = None  # malformed env falls back to DEFAULT_LIMIT
    return RateLimitConfigResponse(
        limit=config["limit"] if config else None,
        window_s=config["window_s"] if config else None,
        active_buckets=config["active_buckets"] if config else None,
        env_var_name=ENV_VAR_NAME,
        env_var_current_value=env_raw,
        env_var_effective_value=env_effective,
        middleware_initialized=config is not None,
    )


@router.post(
    "/reset-rate-limit",
    response_model=ResetRateLimitResponse,
    summary="Reset the rate-limit singleton (admin only).",
)
def reset_rate_limit(
    request: Request, body: ResetRateLimitBody
) -> ResetRateLimitResponse:
    """Drop the in-process rate-limit singleton and rebuild with new config.

    **Admin only.** This is the hot-reload path for the rate limit —
    the middleware reads ``TTS_ERP_RATE_LIMIT_PER_MIN`` only on first
    request, so changing the env var requires either a service
    restart or this endpoint.

    To re-read the current env var without changing the limit, POST
    with an empty body ``{}``.

    No persistence — the change lives in the worker process. After
    restart the env var wins again.
    """
    require_role_at_least(request, "admin")
    info: dict[str, Any] = reset_shared(
        limit=body.new_limit, reset_buckets=body.reset_buckets
    )
    return ResetRateLimitResponse(
        old_limit=info["old_limit"],
        new_limit=info["new_limit"],
        window_s=info["window_s"],
        buckets_cleared=info["buckets_cleared"],
        active_buckets=info["active_buckets"],
        reset_buckets=info["reset_buckets"],
        limit_source=info["limit_source"],
        env_var_source=ENV_VAR_NAME,
        # Audit trail: 12-char sha256 hex prefix of the admin's bearer token.
        # Same format AccessLogMiddleware logs; grep "reset_by=<prefix>" to
        # correlate the access log entry with the change. We never write
        # the full token — only its first-12 hex prefix.
        reset_by=str(request.scope.get("api_key_hash", "") or "")[:12],
        reset_by_role=str(request.scope.get("api_key_role", "") or ""),
        reset_at=datetime.now(timezone.utc),
    )


# ─── Plugin sync data purge ────────────────────────────────────────────

# Tables that store Chrome extension synced data. Deletion order matters:
# child tables (FK → raw_log.id) first, then the parent raw_log.
_ANALYTICS_TABLES = [
    "analytics.ad_today",
    "analytics.ad_daily",
    "analytics.ad_monthly",
    "analytics.ad_raw_log",
    "analytics.plugin_logs",
]

_CHROME_SYNC_CHILD_TABLES = [
    "chrome_sync.order_lines",
    "chrome_sync.orders",
    "chrome_sync.shipments",
    "chrome_sync.tracking_events",
    "chrome_sync.settlement_details",
    "chrome_sync.settlements",
]

_CHROME_SYNC_RAW_LOG = "chrome_sync.raw_log"


@router.post(
    "/purge-plugin-data",
    summary="一键清除所有插件同步数据（admin only）",
)
def purge_plugin_data(request: Request) -> dict[str, Any]:
    """Delete all Chrome extension synced data from analytics and chrome_sync schemas.

    **Readwrite role required.** Clears 12 tables in a single transaction:
    - analytics: ad_today, ad_daily, ad_monthly, raw_log, plugin_logs
    - chrome_sync: orders, order_lines, shipments, tracking_events,
      settlements, settlement_details, raw_log

    Returns per-table row counts before deletion.
    """
    require_role_at_least(request, "readwrite")

    from sqlalchemy import text

    from tts_erp_v2.db.base import get_engine

    engine = get_engine()
    counts: dict[str, int] = {}

    with engine.begin() as conn:
        # Count rows first (for the response)
        all_tables = (
            _ANALYTICS_TABLES + _CHROME_SYNC_CHILD_TABLES + [_CHROME_SYNC_RAW_LOG]
        )
        for table in all_tables:
            try:
                row = conn.execute(
                    text(f"SELECT COUNT(*) FROM {table}")
                )  # pi-lens-ignore: python-sql-injection — hardcoded table names
                counts[table] = int(row.scalar() or 0)
            except Exception:
                counts[table] = -1  # table doesn't exist

        # Delete in FK-safe order: children first, then parent
        for table in (
            _CHROME_SYNC_CHILD_TABLES + [_CHROME_SYNC_RAW_LOG] + _ANALYTICS_TABLES
        ):
            if counts.get(table, 0) > 0:
                conn.execute(
                    text(f"DELETE FROM {table}")
                )  # pi-lens-ignore: python-sql-injection — hardcoded table names

    return {
        "cleared": {k: v for k, v in counts.items() if v > 0},
        "total_rows_deleted": sum(v for v in counts.values() if v > 0),
        "purged_by": str(request.scope.get("api_key_hash", "") or "")[:12],
        "purged_at": datetime.now(timezone.utc).isoformat(),
    }
