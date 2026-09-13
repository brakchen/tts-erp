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

from fastapi import APIRouter, HTTPException, Query, Request
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
    "plugin.ad_today",
    "plugin.ad_daily",
    "plugin.ad_monthly",
    "plugin.ad_raw_log",
    "plugin.plugin_logs",
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


def _is_prod_shaped_db() -> bool:
    """Guard: refuse destructive ops on prod-shape dbnames.

    2026-09-13 incident: ``tests/api/test_admin_purge.py::test_purge_plugin_data_clears_ad_tables``
    was run against ``tts_erp`` (prod) because the worktree's ``.env`` symlinked
    to the main repo's prod ``.env`` and the runner did not source ``.env.test``.
    The wipe blanked 14,719 rows of prod ``plugin.ad_daily`` (246 campaigns ×
    65 days). See ``tech-doc/incident-reports/2026-09-13-ad-daily-purge.md``.
    Any future purge path MUST refuse to run on prod-shape dbnames unless
    ``ALLOW_PROD_PURGE=1`` is explicitly set in the environment.
    """
    from urllib.parse import urlparse

    db_url = os.environ.get("TTS_ERP_DB_URL", "")
    if not db_url:
        # If unset, refuse — fail-closed. Caller can override via ALLOW_PROD_PURGE.
        return True
    try:
        # postgresql+psycopg://u:p@h:port/dbname
        path = urlparse(db_url.replace("postgresql+psycopg://", "postgresql://")).path
        dbname = path.lstrip("/").split("?")[0]
    except Exception:
        return True
    # Prod dbnames: tts_erp / tts_erp_prod (per AGENTS.md §6).
    # Test dbname: tts_erp_v3_test.
    return dbname in {"tts_erp", "tts_erp_prod"} or dbname.startswith("tts_erp_prod_")


@router.post(
    "/purge-plugin-data",
    summary="一键清除所有插件同步数据（admin only）",
)
def purge_plugin_data(
    request: Request,
    confirm: bool = Query(False, description="Must be true to actually delete. Dry-run by default."),
    allow_prod: bool = Query(False, description="Override prod-shape guard (only honored when TTS_ERP_ENVIRONMENT=dev)."),
) -> dict[str, Any]:
    """Delete all Chrome extension synced data from analytics and plugin schemas.

    **Admin role required.** Clears 12 tables in a single transaction:
    - analytics: ad_today, ad_daily, ad_monthly, raw_log, plugin_logs
    - plugin: orders, order_lines, shipments, tracking_events,
      settlements, settlement_details, raw_log

    Two safety gates (2026-09-13 hardening):
    1. ``confirm=true`` query param required to actually delete; without it
       the endpoint returns row counts only (dry-run).
    2. Refuses to run on prod-shape dbnames (``tts_erp`` / ``tts_erp_prod``)
       unless ``ALLOW_PROD_PURGE=1`` is set in the env (or the explicit
       ``allow_prod=true`` query param is passed AND ``TTS_ERP_ENVIRONMENT=dev``).
       Returns 403 with a clear refusal message — does NOT count or touch rows.

    Returns per-table row counts, plus ``dry_run`` and ``executed`` flags.
    """
    require_role_at_least(request, "admin")

    # Gate 1: prod-shape dbname guard.
    is_prod = _is_prod_shaped_db()
    allow_prod_env = os.environ.get("ALLOW_PROD_PURGE", "0") == "1"
    dev_env = os.environ.get("TTS_ERP_ENVIRONMENT", "").lower() == "dev"
    if is_prod and not (allow_prod_env or (allow_prod and dev_env)):
        raise HTTPException(
            status_code=403,
            detail=(
                "Refused: refuse to purge on prod-shape dbname. "
                f"dbname appears prod-shaped (TTS_ERP_DB_URL set). "
                "Set ALLOW_PROD_PURGE=1 in the environment, or run against "
                "the dedicated test database (tts_erp_v3_test via scripts/test.sh)."
            ),
        )

    # Gate 2: dry-run unless confirm=true.
    dry_run = not confirm
    executed = confirm and (not is_prod or allow_prod_env or (allow_prod and dev_env))

    from sqlalchemy import text

    from tts_erp_v2.db.base import get_engine

    engine = get_engine()
    counts: dict[str, int] = {}

    with engine.begin() as conn:
        # Count rows first (for the response — always, even on dry-run).
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

        # Only delete if not dry-run.
        if executed:
            # Delete in FK-safe order: children first, then parent
            for table in (
                _PLUGIN_ORDER_CHILD_TABLES + [_PLUGIN_ORDER_RAW_LOG] + _ANALYTICS_TABLES
            ):
                if counts.get(table, 0) > 0:
                    conn.execute(
                        text(f"DELETE FROM {table}")
                    )  # pi-lens-ignore: python-sql-injection — hardcoded table names

    return {
        "dry_run": dry_run,
        "executed": executed,
        "cleared": {k: v for k, v in counts.items() if v > 0},
        "total_rows_deleted": (
            sum(v for v in counts.values() if v > 0) if executed else 0
        ),
        "purged_by": str(request.scope.get("api_key_hash", "") or "")[:12],
        "purged_at": datetime.now(timezone.utc).isoformat(),
        "prod_guarded": is_prod,
        "next_step": (
            None if executed else
            "Pass ?confirm=true (and ?allow_prod=true on dev env) to actually delete."
        ),
    }


# ─── Manual shop registration (plugin-only shops) ─────────────────────
#
# Background: shops synced via the Chrome extension have no TikTok API
# credential, so the OAuth callback (the only other writer of
# ``commerce.shops``) never creates a row for them — and every
# ``LEFT JOIN commerce.shops`` (spu-roi shop filter etc.) misses them.
# These endpoints let an operator register such a shop MANUALLY:
# the row is created with ``credential_id = NULL`` and ``status = 'active'``
# —— 注册即完整店铺，没有「待授权」中间态。
#
# 2026-09-11（PLUGIN_ARCH_CLEANUP）：原先还有 ``data_source`` 枚举列标记
# 同步方式，现已删除 —— 「是否走 API 同步」已可由 ``credential_id IS NOT NULL``
# 完整推出，且 api/plugin 数据已按 schema 物理隔离（`plugin.*` / `commerce.*`
# 等），无需来源判定。插件广告 dump 没有 server-side 替代路径，不再拦截。
#
# Invariants:
#   * registration NEVER touches ``credential_id`` / ``status`` of an
#     existing row — if the shop later obtains API access, the OAuth
#     callback's ``on_conflict_do_update`` backfills ``credential_id``.
#   * data sync does NOT depend on registration: the plugin dumps
#     endpoints write plugin.* regardless.
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


class ShopRegisterResponse(BaseModel):
    created: bool
    shop: ShopOut


# INSERT: new plugin-only shop. ON CONFLICT: backfill still-NULL display
# fields ONLY (COALESCE(existing, excluded)) — credential_id / status /
# account_name of an existing row are never overwritten.
# xmax = 0 distinguishes the inserted row from a conflict-updated one.
_SQL_REGISTER_SHOP = text(
    "INSERT INTO commerce.shops "
    "(platform, shop_id, account_name, region, seller_type, status, opened_date) "
    "VALUES (:platform, :shop_id, :account_name, :region, :seller_type, "
    "        'active', :opened_date) "
    "ON CONFLICT (platform, shop_id) DO UPDATE SET "
    "  account_name = COALESCE(shops.account_name, EXCLUDED.account_name), "
    "  region = COALESCE(shops.region, EXCLUDED.region), "
    "  seller_type = COALESCE(shops.seller_type, EXCLUDED.seller_type), "
    "  opened_date = COALESCE(shops.opened_date, EXCLUDED.opened_date) "
    "RETURNING id, platform, shop_id, account_name, region, seller_type, "
    "          status, credential_id, opened_date, (xmax = 0) AS inserted"
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
        ),
    )


# ─── Update shop opened_date ─────────────────────────────────────────

_SQL_UPDATE_OPENED_DATE = text(
    "UPDATE commerce.shops SET opened_date = :opened_date "
    "WHERE id = :shop_pk "
    "RETURNING id, platform, shop_id, account_name, region, seller_type, "
    "          status, credential_id, opened_date"
)


class ShopUpdateBody(BaseModel):
    """PATCH body for ``/v2/admin/shops/{shop_pk}``."""

    opened_date: date | None = Field(
        description="开店时间（天级，YYYY-MM-DD）。设为 null 清空。",
    )


class ShopUpdateResponse(BaseModel):
    shop: ShopOut


@router.patch(
    "/shops/{shop_pk}",
    response_model=ShopUpdateResponse,
    summary="更新店铺开业时间（readwrite+）",
)
def update_shop(
    request: Request, shop_pk: int, body: ShopUpdateBody
) -> ShopUpdateResponse:
    """Update a shop's ``opened_date``. Only this field is mutable; all
    other columns are left untouched.
    """
    require_role_at_least(request, "readwrite")

    from tts_erp_v2.db.base import get_engine

    engine = get_engine()
    with engine.begin() as conn:
        row = conn.execute(  # pi-lens-ignore: python-sql-injection — module-level constant SQL, bound params only
            _SQL_UPDATE_OPENED_DATE,
            {"shop_pk": shop_pk, "opened_date": body.opened_date},
        ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"shop {shop_pk} not found")
    return ShopUpdateResponse(
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
        ),
    )


# Shop ids seen in plugin-synced data but with no commerce.shops row.
# Sources: plugin.raw_log.shop_id (order/logistics/settlement dumps)
# + analytics seller_id (ad_today/ad_daily/plugin_logs).
_SQL_UNREGISTERED_SHOPS = text(
    "SELECT shop_id, source FROM ("
    "  SELECT shop_id, 'plugin' AS source FROM plugin.raw_log GROUP BY shop_id"
    "  UNION"
    "  SELECT seller_id, 'analytics' FROM plugin.ad_today GROUP BY seller_id"
    "  UNION"
    "  SELECT seller_id, 'analytics' FROM plugin.ad_daily GROUP BY seller_id"
    "  UNION"
    "  SELECT seller_id, 'analytics' FROM plugin.plugin_logs GROUP BY seller_id"
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
