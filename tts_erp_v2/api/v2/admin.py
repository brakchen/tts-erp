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
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import (
    # Re-exported as `_is_prod_shaped_db` for the prod-shape guard in
    # ``purge-plugin-data`` (single source of truth lives in tts_erp_v2.api.deps
    # as of 2026-09-13 fix/unify-destructive-guard; moved here from mid-file
    # on 2026-09-28 feat/shops-page-meta-auth to avoid the module-level-import
    # linter flag — alias name is unchanged for back-compat).
    is_prod_shaped_db as _is_prod_shaped_db,
)
from tts_erp_v2.api.deps import (
    require_role_at_least,
)
from tts_erp_v2.middleware.rate_limit import (
    ENV_VAR_NAME,
    reset_shared,
    shared_config,
)
from tts_erp_v2.proxy.errors import SigningError
from tts_erp_v2.proxy.token_service import (
    resolve_tiktok_app_credentials,
    upsert_tiktok_app_credentials,
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
        reset_at=datetime.now(UTC),
    )


# ─── Plugin sync data purge ────────────────────────────────────────────

# Tables that store Chrome extension synced data (ad 域 + business tables)。
# business tables 已移除 log_id FK (2026-09-17 chore/deprecate-plugin-raw-log
# Phase 3)，现在各业务表互相独立，没有 FK 父子依赖。
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


@router.post(
    "/purge-plugin-data",
    summary="一键清除所有插件同步数据（admin only）",
)
def purge_plugin_data(
    request: Request,
    confirm: bool = Query(
        False, description="Must be true to actually delete. Dry-run by default."
    ),
    allow_prod: bool = Query(
        False,
        description="Override prod-shape guard (only honored when TTS_ERP_ENVIRONMENT=dev).",
    ),
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
                "dbname appears prod-shaped (TTS_ERP_DB_URL set). "
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
        all_tables = _ANALYTICS_TABLES + _PLUGIN_ORDER_CHILD_TABLES
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
            # ad 域和 business tables 现在互相独立（raw_log FK 链 2026-09-17 drop），
            # 无 FK 依赖顺序要求，之间任意顺序 DELETE 都可以。
            for table in _ANALYTICS_TABLES + _PLUGIN_ORDER_CHILD_TABLES:
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
        "purged_at": datetime.now(UTC).isoformat(),
        "prod_guarded": is_prod,
        "next_step": (
            None
            if executed
            else "Pass ?confirm=true (and ?allow_prod=true on dev env) to actually delete."
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
    service_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="TikTok Partner Center service_id。",
    )
    app_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        repr=False,
        description="与 service_id 配套的 App Key；必须与 app_secret 同时提交。",
    )
    app_secret: SecretStr | None = Field(
        default=None,
        repr=False,
        description="与 service_id 配套的 App Secret；只加密存储，不返回。",
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

    @field_validator("service_id", "app_key")
    @classmethod
    def _strip_optional_value(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None


class ShopOut(BaseModel):
    id: int
    platform: str
    shop_id: str
    account_name: str | None = None
    region: str | None = None
    seller_type: str | None = None
    status: str | None = None
    credential_id: int | None = None
    service_id: str | None = None
    app_credentials_configured: bool = False
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
    "(platform, shop_id, account_name, region, seller_type, status, "
    " opened_date, service_id) "
    "VALUES (:platform, :shop_id, :account_name, :region, :seller_type, "
    "        'active', :opened_date, :service_id) "
    "ON CONFLICT (platform, shop_id) DO UPDATE SET "
    "  account_name = COALESCE(shops.account_name, EXCLUDED.account_name), "
    "  region = COALESCE(shops.region, EXCLUDED.region), "
    "  seller_type = COALESCE(shops.seller_type, EXCLUDED.seller_type), "
    "  opened_date = COALESCE(shops.opened_date, EXCLUDED.opened_date), "
    "  service_id = COALESCE(shops.service_id, EXCLUDED.service_id) "
    "RETURNING id, platform, shop_id, account_name, region, seller_type, "
    "          status, credential_id, service_id, opened_date, "
    "          (xmax = 0) AS inserted"
)


def _validated_plaintext_app_secret(
    *,
    service_id: str | None,
    app_key: str | None,
    app_secret: SecretStr | None,
) -> str | None:
    """Validate the secret pair without echoing plaintext in 422 responses."""
    plaintext = app_secret.get_secret_value() if app_secret is not None else None
    if (app_key is None) != (plaintext is None):
        raise HTTPException(
            status_code=422,
            detail="app_key and app_secret must be provided together",
        )
    if plaintext is None:
        return None
    if not plaintext or len(plaintext) > 512:
        raise HTTPException(status_code=422, detail="app_secret length is invalid")
    if service_id is None:
        raise HTTPException(
            status_code=422,
            detail="service_id is required with app_key/app_secret",
        )
    return plaintext


def _app_credentials_configured(
    session: Session,
    *,
    service_id: str | None,
) -> bool:
    if service_id is None:
        return False
    try:
        resolve_tiktok_app_credentials(session, service_id=service_id)
    except SigningError:
        return False
    return True


def _configure_or_validate_app_credentials(
    session: Session,
    *,
    service_id: str | None,
    app_key: str | None,
    app_secret: str | None,
) -> bool:
    """Write a supplied pair or verify that service_id already resolves."""
    if service_id is None:
        return False
    if app_key is not None and app_secret is not None:
        upsert_tiktok_app_credentials(
            session,
            service_id=service_id,
            app_key=app_key,
            plaintext_app_secret=app_secret,
        )
        return True
    if not _app_credentials_configured(session, service_id=service_id):
        try:
            resolve_tiktok_app_credentials(session, service_id=service_id)
        except SigningError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return True


@router.post(
    "/shops/register",
    response_model=ShopRegisterResponse,
    summary="人工注册店铺（插件同步店铺补登记，readwrite+）",
)
def register_shop(request: Request, body: ShopRegisterBody) -> ShopRegisterResponse:
    """Register a shop and optionally configure its service App pair.

    App Key/App Secret writes are readwrite and share the same transaction as
    the shop/service_id write. Secret is only stored encrypted, never returned.
    """
    require_role_at_least(request, "readwrite")
    plaintext_app_secret = _validated_plaintext_app_secret(
        service_id=body.service_id,
        app_key=body.app_key,
        app_secret=body.app_secret,
    )

    from tts_erp_v2.db.base import get_engine

    engine = get_engine()
    with Session(engine) as session, session.begin():
        row = session.execute(  # pi-lens-ignore: python-sql-injection — module-level constant SQL, bound params only
            _SQL_REGISTER_SHOP,
            {
                "platform": body.platform,
                "shop_id": body.shop_id,
                "account_name": body.account_name,
                "region": body.region,
                "seller_type": body.seller_type,
                "opened_date": body.opened_date,
                "service_id": body.service_id,
            },
        ).one()
        if body.app_key is not None and row.service_id != body.service_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    "shop already uses a different service_id; update the shop "
                    "explicitly before rotating App credentials"
                ),
            )
        if body.service_id is not None or body.app_key is not None:
            app_credentials_configured = _configure_or_validate_app_credentials(
                session,
                service_id=row.service_id,
                app_key=body.app_key,
                app_secret=plaintext_app_secret,
            )
        else:
            app_credentials_configured = _app_credentials_configured(
                session,
                service_id=row.service_id,
            )
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
            service_id=row.service_id,
            app_credentials_configured=app_credentials_configured,
            opened_date=row.opened_date,
        ),
    )


# ─── Update shop metadata ─────────────────────────────────────────

_SQL_UPDATE_SHOP = text(
    "UPDATE commerce.shops "
    "SET account_name = COALESCE(:account_name, account_name), "
    "    region = COALESCE(:region, region), "
    "    opened_date = COALESCE(:opened_date, opened_date), "
    "    service_id = COALESCE(:service_id, service_id), "
    "    updated_at = now() "
    "WHERE id = :shop_pk "
    "RETURNING id, platform, shop_id, account_name, region, seller_type, "
    "          status, credential_id, service_id, opened_date"
)


class ShopUpdateBody(BaseModel):
    """PATCH body for ``/v2/admin/shops/{shop_pk}``.

    All fields are optional; ``null``/缺省 = 保持原值（COALESCE 语义，
    与 register 的 backfill-only 不同 —— PATCH 会覆盖已有非空值）。
    """

    account_name: str | None = Field(
        default=None,
        max_length=200,
        description="店铺名称。设为 null 不修改。",
    )
    region: str | None = Field(
        default=None,
        max_length=16,
        description="国家/地区代码（如 VN）。设为 null 不修改。",
    )
    opened_date: date | None = Field(
        default=None,
        description="开店时间（天级，YYYY-MM-DD）。设为 null 不修改。",
    )
    service_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="TikTok Partner Center service_id。设为 null 不修改。",
    )
    app_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        repr=False,
        description="配套 App Key；与 app_secret 同时提交（readwrite+）。",
    )
    app_secret: SecretStr | None = Field(
        default=None,
        repr=False,
        description="配套 App Secret；只加密存储，不返回（readwrite+）。",
    )

    @field_validator("service_id", "app_key")
    @classmethod
    def _strip_optional_value(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None


class ShopUpdateResponse(BaseModel):
    shop: ShopOut


@router.patch(
    "/shops/{shop_pk}",
    response_model=ShopUpdateResponse,
    summary="更新店铺元信息（名称/区域/开店日期/service_id，readwrite+）",
)
def update_shop(
    request: Request, shop_pk: int, body: ShopUpdateBody
) -> ShopUpdateResponse:
    """Update shop metadata and optionally rotate its service App pair.

    App Key/App Secret writes are readwrite and are committed atomically with
    the service_id change. A service_id-only change is accepted only when the
    target pair already resolves from the encrypted table or exact legacy env
    fallback.
    """
    require_role_at_least(request, "readwrite")
    plaintext_app_secret = _validated_plaintext_app_secret(
        service_id=body.service_id,
        app_key=body.app_key,
        app_secret=body.app_secret,
    )

    from tts_erp_v2.db.base import get_engine

    engine = get_engine()
    with Session(engine) as session, session.begin():
        row = session.execute(  # pi-lens-ignore: python-sql-injection — module-level constant SQL, bound params only
            _SQL_UPDATE_SHOP,
            {
                "shop_pk": shop_pk,
                "account_name": body.account_name,
                "region": body.region,
                "opened_date": body.opened_date,
                "service_id": body.service_id,
            },
        ).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail=f"shop {shop_pk} not found")
        if body.app_key is not None and row.service_id is None:
            raise HTTPException(
                status_code=409,
                detail="shop must have service_id before App credentials can be saved",
            )
        if body.service_id is not None or body.app_key is not None:
            app_credentials_configured = _configure_or_validate_app_credentials(
                session,
                service_id=row.service_id,
                app_key=body.app_key,
                app_secret=plaintext_app_secret,
            )
        else:
            app_credentials_configured = _app_credentials_configured(
                session,
                service_id=row.service_id,
            )
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
            service_id=row.service_id,
            app_credentials_configured=app_credentials_configured,
            opened_date=row.opened_date,
        ),
    )


# Shop ids seen in plugin-synced data but with no commerce.shops row.
# Sources: analytics seller_id (ad_today/ad_daily/plugin_logs)。plugin 业务表
# 的 shop_id 在订单 INSERT 时已校验过（FK → commerce.shops.shop_id），所以不再
# 从 raw_log / 业务表枚举未注册店铺（2026-09-17 raw_log 表已 drop）。
_SQL_UNREGISTERED_SHOPS = text(
    "SELECT shop_id, source FROM ("
    "  SELECT seller_id AS shop_id, 'analytics' AS source FROM plugin.ad_today GROUP BY seller_id"
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
