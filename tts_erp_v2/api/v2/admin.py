"""Admin-only operational endpoints.

These are operator-facing endpoints that mutate cross-cutting runtime
state (rate-limit singleton, etc.) and are not part of any business
domain. The rate-limit endpoint is the only one today; future admin
ops (e.g., feature-flag toggles, runtime config reload) can be added
here.

All endpoints in this module are gated to ``admin`` role both at the
middleware (see ``tts_erp_v2/middleware/auth.py::required_role()`` —
unknown ``/v2/admin/...`` paths default to admin-required) and via
``require_role_at_least(request, "admin")`` for defense-in-depth.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
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
    require_cookie_csrf,
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
from tts_erp_v2.sync_worker.scheduler import (
    JOBS,
    run_scheduled_job_once,
    set_job_enabled,
)

from tts_erp_v2.api.schemas import ChannelAccountOut
from tts_erp_v2.commerce.shop_access import authorized_shop_clause
from tts_erp_v2.commerce.shop_metadata import plugin_sources, upsert_shop_metadata
from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.integration import Credentials

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


# ─── Scheduler job controls ────────────────────────────────────────────


class SyncJobEnabledBody(BaseModel):
    enabled: bool = Field(description="True = allow scheduled ticks; false = skip ticks.")


class SyncJobEnabledResponse(BaseModel):
    job_name: str
    enabled: bool
    updated_at: datetime


class SyncJobTriggerBody(BaseModel):
    shop_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="TikTok shop_id for one-store manual runs. Omit for all stores.",
    )

    @field_validator("shop_id")
    @classmethod
    def _strip_shop_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None


class SyncJobTriggerResponse(BaseModel):
    accepted: bool
    job_name: str
    shop_id: str | None
    accepted_at: datetime
    message: str


def _require_registered_job(job_name: str):
    try:
        return JOBS[job_name]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown job: {job_name}") from exc


def _validate_manual_shop_scope(job_name: str, shop_id: str | None) -> None:
    spec = _require_registered_job(job_name)
    if not spec.is_tiktok:
        if shop_id is not None:
            raise HTTPException(
                status_code=422,
                detail=f"job {job_name} is system-wide and does not accept shop_id",
            )
        return
    if shop_id is None:
        return

    from tts_erp_v2.db.base import get_engine

    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT 1 FROM commerce.shops s "
                "WHERE s.platform = 'tiktok' AND s.shop_id = :shop_id "
                "AND EXISTS ("
                "  SELECT 1 FROM integration.credentials c "
                "  WHERE c.provider = 'tiktok' "
                "    AND c.external_account_id = s.shop_id"
                ")"
            ),
            {"shop_id": shop_id},
        ).first()
    if row is None:
        raise HTTPException(
            status_code=422,
            detail=f"shop_id {shop_id!r} is not an authorised TikTok shop",
        )


@router.patch(
    "/sync-jobs/{job_name}/enabled",
    response_model=SyncJobEnabledResponse,
    summary="启用或停用周期任务调度（admin only）",
)
def set_sync_job_enabled(
    request: Request, job_name: str, body: SyncJobEnabledBody
) -> SyncJobEnabledResponse:
    """Persistently enable/disable scheduled ticks for a registered job.

    Manual triggers are still allowed while disabled; the flag only affects
    APScheduler's periodic tick path in the sync-worker process.
    """
    require_role_at_least(request, "admin")
    require_cookie_csrf(request)
    _require_registered_job(job_name)

    from tts_erp_v2.db.base import get_engine

    engine = get_engine()
    with Session(engine) as session, session.begin():
        set_job_enabled(session, job_name, body.enabled)
    return SyncJobEnabledResponse(
        job_name=job_name,
        enabled=body.enabled,
        updated_at=datetime.now(UTC),
    )


@router.post(
    "/sync-jobs/{job_name}/trigger",
    response_model=SyncJobTriggerResponse,
    summary="立即触发一次周期任务（readwrite+）",
)
def trigger_sync_job(
    request: Request,
    job_name: str,
    body: SyncJobTriggerBody,
    background_tasks: BackgroundTasks,
) -> SyncJobTriggerResponse:
    """Queue one immediate run in the API process background task runner."""
    require_role_at_least(request, "readwrite")
    require_cookie_csrf(request)
    _validate_manual_shop_scope(job_name, body.shop_id)
    background_tasks.add_task(run_scheduled_job_once, job_name, shop_id=body.shop_id)
    return SyncJobTriggerResponse(
        accepted=True,
        job_name=job_name,
        shop_id=body.shop_id,
        accepted_at=datetime.now(UTC),
        message="任务已提交后台执行；运行结果写入 integration.sync_jobs。",
    )


# ─── Plugin sync data purge ────────────────────────────────────────────

# Tables that store Chrome extension synced data (ad 域 + business tables)。
# business tables 已移除 log_id FK (2026-09-17 chore/deprecate-plugin-raw-log
# Phase 3)，现在各业务表互相独立，没有 FK 父子依赖。
_ANALYTICS_TABLES = [
    "plugin.ad_today",
    "plugin.ad_daily",
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
    """Delete all Chrome extension synced data from the plugin schema.

    **Admin role required.** Clears 10 tables in a single transaction:
    - ads/logs: ad_today, ad_daily, ad_raw_log, plugin_logs
    - orders: orders, order_lines, shipments, tracking_events,
      settlements, settlement_details

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

    from sqlalchemy import select, text

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
            except SQLAlchemyError:
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


# Shop metadata is created by manual registration or bound-plugin discovery.
# API access still requires this shop's explicitly linked OAuth credential.
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


class ShopOut(ChannelAccountOut):
    pass


class ShopRegisterResponse(BaseModel):
    created: bool
    shop: ShopOut


def _validated_plaintext_app_secret(
    *, service_id: str | None, app_key: str | None, app_secret: SecretStr | None,
) -> str | None:
    plaintext = app_secret.get_secret_value() if app_secret is not None else None
    if (app_key is None) != (plaintext is None):
        raise HTTPException(422, "app_key and app_secret must be provided together")
    if plaintext is not None:
        if not plaintext or len(plaintext) > 512:
            raise HTTPException(422, "app_secret length is invalid")
        if not service_id:
            raise HTTPException(422, "service_id is required with App credentials")
    return plaintext


def _app_credentials_configured(session: Session, *, shop_pk: int, service_id: str | None) -> bool:
    if not service_id:
        return False
    try:
        resolve_tiktok_app_credentials(session, shop_pk=shop_pk, service_id=service_id)
    except SigningError:
        return False
    return True


def _configure_or_validate_app_credentials(
    session: Session, *, shop_pk: int, service_id: str | None,
    app_key: str | None, app_secret: str | None,
) -> bool:
    if not service_id:
        return False
    if app_key is not None and app_secret is not None:
        upsert_tiktok_app_credentials(session, shop_pk=shop_pk, service_id=service_id,
                                     app_key=app_key, plaintext_app_secret=app_secret)
        return True
    if not _app_credentials_configured(session, shop_pk=shop_pk, service_id=service_id):
        raise HTTPException(409, "configure this shop's App Key and App Secret first")
    return True


def _shop_out(session: Session, shop: ChannelAccount) -> ShopOut:
    configured = _app_credentials_configured(session, shop_pk=shop.id, service_id=shop.service_id)
    credential = session.get(Credentials, shop.credential_id) if shop.credential_id else None
    auth_state = "unauthorized"
    if credential:
        authorized = session.execute(
            text("SELECT " + authorized_shop_clause() + " FROM commerce.shops WHERE id = :id"),
            {"id": shop.id},
        ).scalar()
        if authorized:
            auth_state = "authorized"
        elif credential.provider == "tiktok" and credential.external_account_id == shop.shop_id:
            auth_state = "reauthorize"
        else:
            auth_state = "unknown"
    return ShopOut(
        id=shop.id, platform=shop.platform, shop_id=shop.shop_id,
        account_name=shop.account_name, region=shop.region, shop_code=shop.shop_code,
        discovered_at=shop.discovered_at, plugin_sources=plugin_sources(shop.plugin_metadata),
        seller_type=shop.seller_type, status=shop.status,
        credential_id=shop.credential_id, service_id=shop.service_id,
        app_credentials_configured=configured, opened_date=shop.opened_date,
        synced_at=shop.synced_at, updated_at=shop.updated_at, auth_state=auth_state,
    )


@router.post("/shops/register", response_model=ShopRegisterResponse)
def register_shop(request: Request, body: ShopRegisterBody) -> ShopRegisterResponse:
    require_role_at_least(request, "readwrite")
    require_cookie_csrf(request)
    plaintext = _validated_plaintext_app_secret(service_id=body.service_id,
        app_key=body.app_key, app_secret=body.app_secret)
    from tts_erp_v2.db.base import get_engine
    with Session(get_engine()) as session, session.begin():
        shop, created = upsert_shop_metadata(session, shop_id=body.shop_id,
            account_name=body.account_name, region=body.region,
            seller_type=body.seller_type, opened_date=body.opened_date,
            service_id=body.service_id)
        if body.app_key is not None and shop.service_id != body.service_id:
            raise HTTPException(409, "shop already uses another service_id; update its App configuration explicitly")
        if body.service_id is not None or body.app_key is not None:
            _configure_or_validate_app_credentials(session, shop_pk=shop.id,
                service_id=shop.service_id, app_key=body.app_key, app_secret=plaintext)
        result = ShopRegisterResponse(created=created, shop=_shop_out(session, shop))
    return result


class ShopUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opened_date: date | None = None
    service_id: str | None = Field(default=None, min_length=1, max_length=64)
    app_key: str | None = Field(default=None, min_length=1, max_length=128, repr=False)
    app_secret: SecretStr | None = Field(default=None, repr=False)

    @field_validator("service_id", "app_key")
    @classmethod
    def strip_value(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class ShopUpdateResponse(BaseModel):
    shop: ShopOut


@router.patch("/shops/{shop_pk}", response_model=ShopUpdateResponse)
def update_shop(request: Request, shop_pk: int, body: ShopUpdateBody) -> ShopUpdateResponse:
    """Edit opening date or this shop's App; shop name and region are immutable."""
    require_role_at_least(request, "readwrite")
    require_cookie_csrf(request)
    from tts_erp_v2.db.base import get_engine
    with Session(get_engine()) as session, session.begin():
        shop = session.scalar(select(ChannelAccount).where(ChannelAccount.id == shop_pk).with_for_update())
        if shop is None:
            raise HTTPException(404, "shop not found")
        service_id = body.service_id if body.service_id is not None else shop.service_id
        plaintext = _validated_plaintext_app_secret(service_id=service_id,
            app_key=body.app_key, app_secret=body.app_secret)
        if "opened_date" in body.model_fields_set:
            shop.opened_date = body.opened_date
        if body.service_id is not None or body.app_key is not None:
            _configure_or_validate_app_credentials(session, shop_pk=shop.id,
                service_id=service_id, app_key=body.app_key, app_secret=plaintext)
            shop.service_id = service_id
        shop.updated_at = datetime.now(UTC)
        session.flush()
        result = ShopUpdateResponse(shop=_shop_out(session, shop))
    return result
