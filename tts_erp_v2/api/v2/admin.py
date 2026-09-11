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
from datetime import date, datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text

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

_PLUGIN_ORDER_CHILD_TABLES = [
    "plugin.order_lines",
    "plugin.orders",
    "plugin.shipments",
    "plugin.tracking_events",
    "plugin.settlement_details",
    "plugin.settlements",
]

_PLUGIN_ORDER_RAW_LOG = "plugin.raw_log"


@router.post(
    "/purge-plugin-data",
    summary="一键清除所有插件同步数据（admin only）",
)
def purge_plugin_data(request: Request) -> dict[str, Any]:
    """Delete all Chrome extension synced data from analytics and plugin schemas.

    **Readwrite role required.** Clears 12 tables in a single transaction:
    - analytics: ad_today, ad_daily, ad_monthly, raw_log, plugin_logs
    - plugin: orders, order_lines, shipments, tracking_events,
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
            _ANALYTICS_TABLES + _PLUGIN_ORDER_CHILD_TABLES + [_PLUGIN_ORDER_RAW_LOG]
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
            _PLUGIN_ORDER_CHILD_TABLES + [_PLUGIN_ORDER_RAW_LOG] + _ANALYTICS_TABLES
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


# ─── Manual shop registration (plugin-only shops) ─────────────────────
#
# Background: shops synced via the Chrome extension have no TikTok API
# credential, so the OAuth callback (the only other writer of
# ``commerce.shops``) never creates a row for them — and every
# ``LEFT JOIN commerce.shops`` (spu-roi shop filter etc.) misses them.
# These endpoints let an operator register such a shop MANUALLY:
# the row is created with ``credential_id = NULL``,
# ``data_source = 'plugin'`` and ``status = 'active'`` — 注册即完整店铺，
# 没有「待授权」中间态；同步方式由显式枚举 ``data_source`` 标记
# （'api' | 'plugin'，migration 0022）。
#
# Invariants:
#   * registration NEVER touches ``credential_id`` / ``status`` /
#     ``data_source`` of an existing row — if the shop later obtains API
#     access, the OAuth callback's ``on_conflict_do_update`` backfills
#     credential_id and flips ``data_source`` to 'api'.
#   * data sync does NOT depend on registration: the plugin dumps
#     endpoints write plugin.*/analytics.* regardless.
#   * registration only affects query-time association.

# Non-production shop-id prefixes that must never be registered (mirrors
# sync_worker/scheduler.py::NON_PRODUCTION_SHOP_PREFIXES — a registered
# shop is one step closer to being dialed upstream).
_NON_REGISTERABLE_PREFIXES = ("TEST_", "MOCK_")


class ShopRegisterBody(BaseModel):
    """POST body for ``/v2/admin/shops/register``."""

    platform: Literal["tiktok"] = "tiktok"
    shop_id: str = Field(min_length=1, max_length=64)
    account_name: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, max_length=16)
    seller_type: str | None = Field(default=None, max_length=32)
    opened_date: date | None = Field(
        default=None,
        description="开店时间（天级，YYYY-MM-DD）。可空。",
    )

    @field_validator("shop_id")
    @classmethod
    def _validate_shop_id(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit():
            raise ValueError(
                "shop_id must be a numeric TikTok shop id "
                f"(TEST_/MOCK_ and other non-numeric ids are not registerable): {v!r}"
            )
        if v.startswith(_NON_REGISTERABLE_PREFIXES):
            raise ValueError(f"shop_id prefix not registerable: {v!r}")
        return v


class ShopOut(BaseModel):
    id: int
    platform: str
    shop_id: str
    account_name: str | None = None
    region: str | None = None
    seller_type: str | None = None
    status: str | None = None
    credential_id: int | None = None
    opened_date: date | None = None
    data_source: str | None = None  # 'api' | 'plugin'


class ShopRegisterResponse(BaseModel):
    created: bool
    shop: ShopOut


# INSERT: new plugin-only shop. ON CONFLICT: backfill still-NULL display
# fields ONLY (COALESCE(existing, excluded)) — credential_id / status /
# account_name of an existing row are never overwritten.
# xmax = 0 distinguishes the inserted row from a conflict-updated one.
_SQL_REGISTER_SHOP = text(
    "INSERT INTO commerce.shops "
    "(platform, shop_id, account_name, region, seller_type, status, opened_date, "
    " data_source) "
    "VALUES (:platform, :shop_id, :account_name, :region, :seller_type, "
    "        'active', :opened_date, 'plugin') "
    "ON CONFLICT (platform, shop_id) DO UPDATE SET "
    "  account_name = COALESCE(shops.account_name, EXCLUDED.account_name), "
    "  region = COALESCE(shops.region, EXCLUDED.region), "
    "  seller_type = COALESCE(shops.seller_type, EXCLUDED.seller_type), "
    "  opened_date = COALESCE(shops.opened_date, EXCLUDED.opened_date) "
    "RETURNING id, platform, shop_id, account_name, region, seller_type, "
    "          status, credential_id, opened_date, data_source, (xmax = 0) AS inserted"
)


@router.post(
    "/shops/register",
    response_model=ShopRegisterResponse,
    summary="人工注册店铺（插件同步店铺补登记，readwrite+）",
)
def register_shop(
    request: Request, body: ShopRegisterBody
) -> ShopRegisterResponse:
    """Register a shop row manually (plugin-synced shops without an API
    credential). Idempotent on ``(platform, shop_id)``; re-registering
    backfills still-NULL display fields and never clobbers an existing
    credential link or status.
    """
    require_role_at_least(request, "readwrite")

    from tts_erp_v2.db.base import get_engine

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(  # pi-lens-ignore: python-sql-injection — module-level constant SQL, bound params only
            _SQL_REGISTER_SHOP,
            {
                "platform": body.platform,
                "shop_id": body.shop_id,
                "account_name": body.account_name,
                "region": body.region,
                "seller_type": body.seller_type,
                "opened_date": body.opened_date,
            },
        ).one()
    return ShopRegisterResponse(
        created=bool(row.inserted),
        shop=ShopOut(
            id=row.id,
            platform=row.platform,
            shop_id=row.shop_id,
            account_name=row.account_name,
            region=row.region,
            seller_type=row.seller_type,
            status=row.status,
            credential_id=row.credential_id,
            opened_date=row.opened_date,
            data_source=row.data_source,
        ),
    )


# Shop ids seen in plugin-synced data but with no commerce.shops row.
# Sources: plugin.raw_log.shop_id (order/logistics/settlement dumps)
# + analytics seller_id (ad_today/ad_daily/plugin_logs).
_SQL_UNREGISTERED_SHOPS = text(
    "SELECT shop_id, source FROM ("
    "  SELECT shop_id, 'plugin' AS source FROM plugin.raw_log GROUP BY shop_id"
    "  UNION"
    "  SELECT seller_id, 'analytics' FROM analytics.ad_today GROUP BY seller_id"
    "  UNION"
    "  SELECT seller_id, 'analytics' FROM analytics.ad_daily GROUP BY seller_id"
    "  UNION"
    "  SELECT seller_id, 'analytics' FROM analytics.plugin_logs GROUP BY seller_id"
    ") seen "
    "WHERE NOT EXISTS ("
    "  SELECT 1 FROM commerce.shops s "
    "  WHERE s.platform = 'tiktok' AND s.shop_id = seen.shop_id"
    ") "
    "ORDER BY shop_id"
)


class UnregisteredShop(BaseModel):
    shop_id: str
    sources: list[str]


class UnregisteredShopsResponse(BaseModel):
    candidates: list[UnregisteredShop]


@router.get(
    "/shops/unregistered",
    response_model=UnregisteredShopsResponse,
    summary="列出插件数据里出现但未注册的店铺（readwrite+）",
)
def list_unregistered_shops(request: Request) -> UnregisteredShopsResponse:
    """Shop ids present in plugin-synced tables (plugin / analytics)
    but missing from ``commerce.shops`` — the candidate list for the
    manual registration page.
    """
    require_role_at_least(request, "readwrite")

    from tts_erp_v2.db.base import get_engine

    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(  # pi-lens-ignore: python-sql-injection — module-level constant SQL, no params
            _SQL_UNREGISTERED_SHOPS
        ).all()
    sources_by_shop: dict[str, set[str]] = {}
    for r in rows:
        sources_by_shop.setdefault(r.shop_id, set()).add(r.source)
    return UnregisteredShopsResponse(
        candidates=[
            UnregisteredShop(shop_id=sid, sources=sorted(srcs))
            for sid, srcs in sorted(sources_by_shop.items())
        ]
    )
