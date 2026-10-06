"""/v2/sync/* — sync-worker 作业同步状态与调度管理。

供 dashboard「数据同步状态」卡片消费：对每个已注册的周期作业
（``tts_erp_v2.sync_worker.scheduler.JOBS``）返回最近一次运行时间、
按周期推算的下次预计运行时间、以及落后周期数。

红灯规则（2026-09-28 用户需求）：最近一次同步时间落后 **≥2 个周期**
（``now - last_run_at >= 2 * interval_seconds``）→ ``severity="crit"``；
落后 ≥1 个周期 → ``"warn"``；一个周期内 → ``"ok"``；从未运行 → ``"unknown"``。

数据来源
--------
* 周期注册表：``sync_worker.scheduler.JOBS``（job_name → interval_seconds，
  单一真相源——不在本模块复制间隔配置）。
* 运行记录：``integration.sync_jobs``（每次 run 一行，tiktok 类作业按
  shop 扇出多行，这里按 job_name 聚合取最新一行）。

同步 worker 只写 sync_jobs 不读本端点；本端点只读，无任何上游外呼。

``GET /v2/sync/freshness`` 给 SPU ROI 页头用：当前店铺的广告 / 订单 / 物流
最近同步时间，以及妙手流水线最近一次成功同步。红灯规则与本模块一致
（落后 ≥1 个周期 warn，≥2 个周期 crit）。广告没有周期作业，按插件
今日刷新的可观察间隔（15 分钟）判灯。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import SessionDep, require_role_at_least
from tts_erp_v2.db.models import ChannelAccount
from tts_erp_v2.db.models.integration import Credentials, SyncCursor, SyncJob
from tts_erp_v2.db.models.plugin import AdDaily, AdToday
from tts_erp_v2.sync_worker.scheduler import (
    CONTROL_DISABLED,
    JOBS,
    NON_PRODUCTION_SHOP_PREFIXES,
    get_job_control_values,
)

router = APIRouter(prefix="/v2/sync", tags=["sync"])


def _require_readwrite(request: Request) -> None:
    """Scheduler management data is visible only to readwrite+ operators."""
    require_role_at_least(request, "readwrite")


class SyncJobStatusOut(BaseModel):
    """一个周期作业的同步健康状态。"""

    job_name: str
    #: 调度周期（秒）；DB 里出现但注册表没有的 job 为 None（无法判红灯）。
    interval_seconds: int | None
    #: 最近一次 run 的开始时间（UTC）；从未运行为 None。
    last_run_at: datetime | None
    #: 最近一次 run 的结束时间（running 中的 job 为 None）。
    last_finished_at: datetime | None
    #: 最近一次 run 状态：running / succeeded / failed；从未运行为 None。
    last_status: str | None
    #: 最近一次 run 的错误信息（failed 时）。
    last_error: str | None
    #: 按 last_run_at + interval 推算的下次预计运行时间；无 last_run 或无周期时为 None。
    next_expected_at: datetime | None
    #: 距上次同步已过去的秒数；从未运行为 None。
    lag_seconds: float | None
    #: 落后的周期数（lag / interval）；无周期时为 None。
    cycles_late: float | None
    #: ok / warn(≥1 周期) / crit(≥2 周期，红灯) / unknown(从未运行或无周期)。
    severity: str


class SyncStatusOut(BaseModel):
    server_time: datetime
    jobs: list[SyncJobStatusOut]


class SyncJobDefinitionOut(BaseModel):
    """Registered scheduler job plus operator control state."""

    job_name: str
    module_path: str
    entrypoint: str
    interval_seconds: int
    is_tiktok: bool
    enabled: bool
    scope_type: str
    last_status: SyncJobStatusOut


class SyncJobShopOut(BaseModel):
    """TikTok shop that can be selected for a per-shop manual run."""

    shop_pk: int
    shop_id: str
    account_name: str | None = None
    status: str | None = None


class SyncJobsOut(BaseModel):
    server_time: datetime
    jobs: list[SyncJobDefinitionOut]
    tiktok_shops: list[SyncJobShopOut]


def _as_utc(ts: datetime) -> datetime:
    """sync_jobs 时间列是 TIMESTAMP WITHOUT TIME ZONE（UTC 语义）；
    读出来可能是 naive——统一按 UTC 解释再参与比较。"""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts


def _latest_sync_jobs(session: Session) -> dict[str, SyncJob]:
    """Return latest sync_jobs row per job_name."""
    latest_rows = session.execute(
        select(SyncJob)
        .ext(distinct_on(SyncJob.job_name))
        .order_by(SyncJob.job_name, SyncJob.started_at.desc())
    ).scalars()
    return {row.job_name: row for row in latest_rows}


def _list_tiktok_shops(session: Session) -> list[SyncJobShopOut]:
    rows = session.execute(
        select(
            ChannelAccount.id,
            ChannelAccount.shop_id,
            ChannelAccount.account_name,
            ChannelAccount.status,
        )
        .where(
            ChannelAccount.platform == "tiktok",
            exists(
                select(Credentials.id).where(
                    Credentials.provider == "tiktok",
                    Credentials.external_account_id == ChannelAccount.shop_id,
                )
            ),
        )
        .order_by(ChannelAccount.shop_id)
    ).all()
    return [
        SyncJobShopOut(
            shop_pk=row.id,
            shop_id=row.shop_id,
            account_name=row.account_name,
            status=row.status,
        )
        for row in rows
        if row.shop_id and not row.shop_id.startswith(NON_PRODUCTION_SHOP_PREFIXES)
    ]


def _build_status(
    job_name: str,
    interval_seconds: int | None,
    last: SyncJob | None,
    now: datetime,
) -> SyncJobStatusOut:
    last_run_at = _as_utc(last.started_at) if last is not None else None
    last_finished_at = (
        _as_utc(last.finished_at)
        if last is not None and last.finished_at is not None
        else None
    )
    lag_seconds: float | None = None
    cycles_late: float | None = None
    next_expected_at: datetime | None = None
    if last_run_at is None:
        severity = "unknown"
    else:
        lag_seconds = (now - last_run_at).total_seconds()
        if interval_seconds:
            next_expected_at = last_run_at + timedelta(seconds=interval_seconds)
            cycles_late = lag_seconds / interval_seconds
            if cycles_late >= 2:
                severity = "crit"
            elif cycles_late >= 1:
                severity = "warn"
            else:
                severity = "ok"
        else:
            severity = "unknown"
    return SyncJobStatusOut(
        job_name=job_name,
        interval_seconds=interval_seconds,
        last_run_at=last_run_at,
        last_finished_at=last_finished_at,
        last_status=last.status if last is not None else None,
        last_error=last.error_message if last is not None else None,
        next_expected_at=next_expected_at,
        lag_seconds=lag_seconds,
        cycles_late=cycles_late,
        severity=severity,
    )


@router.get("/status", response_model=SyncStatusOut)
def sync_status(session: SessionDep) -> SyncStatusOut:
    """返回每个注册周期作业的最近同步时间 / 预计下次同步 / 红灯判定。"""
    latest_by_name = _latest_sync_jobs(session)

    now = datetime.now(UTC)
    out: list[SyncJobStatusOut] = []
    # 注册表里的 job（排序稳定）。
    for name in sorted(JOBS):
        out.append(
            _build_status(name, JOBS[name].interval_seconds, latest_by_name.get(name), now)
        )
    # DB 里出现但注册表没有的 job（历史残留），周期未知 → 不参与红灯判定。
    for name in sorted(set(latest_by_name) - set(JOBS)):
        out.append(_build_status(name, None, latest_by_name[name], now))

    return SyncStatusOut(server_time=now, jobs=out)


@router.get(
    "/jobs", response_model=SyncJobsOut, dependencies=[Depends(_require_readwrite)]
)
def sync_jobs(session: SessionDep) -> SyncJobsOut:
    """返回周期任务定义、启停状态、最近运行状态和可选店铺。"""
    latest_by_name = _latest_sync_jobs(session)
    controls = get_job_control_values(session)
    now = datetime.now(UTC)
    jobs = [
        SyncJobDefinitionOut(
            job_name=name,
            module_path=spec.module_path,
            entrypoint=spec.entrypoint,
            interval_seconds=spec.interval_seconds,
            is_tiktok=spec.is_tiktok,
            enabled=controls.get(name) != CONTROL_DISABLED,
            scope_type="tiktok_shop" if spec.is_tiktok else "system",
            last_status=_build_status(
                name, spec.interval_seconds, latest_by_name.get(name), now
            ),
        )
        for name, spec in sorted(JOBS.items())
    ]
    return SyncJobsOut(
        server_time=now,
        jobs=jobs,
        tiktok_shops=_list_tiktok_shops(session),
    )


# 插件今日广告按 30s 刷新。页头不按 30s 判灯，否则一次网络抖动就红。
# 15 分钟 ≈ 30 个错过的刷新，作为「广告同步落后一个周期」。
_ADS_INTERVAL_SECONDS = 15 * 60
# 按店归因的 sync_jobs.extra.shop_id 从本次改动才开始写。只回看近期，
# 避免历史无 shop_id 的行把「没有归因记录」扫成全表。
_JOB_LOOKBACK = timedelta(days=14)
_SEV_RANK = {"unknown": 0, "ok": 1, "warn": 2, "crit": 3}


class FreshnessSourceOut(BaseModel):
    """一个数据源的最近同步时间。"""

    key: str
    label: str
    synced_at: datetime | None
    #: shop = 当前店铺；system = 不按店拆（妙手账号级流水线）。
    scope: str
    #: job = sync_jobs 成功完成时间；cursor = 该店游标更新时间；
    #: rows = 广告事实最近写入；none = 没有可展示的时间。
    basis: str
    job_name: str | None = None
    last_status: str | None = None
    severity: str
    detail: str | None = None


class SyncFreshnessOut(BaseModel):
    server_time: datetime
    shop_pk: int
    shop_id: str
    sources: list[FreshnessSourceOut]


def _freshness_severity(
    synced_at: datetime | None,
    interval_seconds: int | None,
    now: datetime,
) -> str:
    """Same cycle rule as ``/v2/sync/status``, measured from the displayed time."""
    if synced_at is None or not interval_seconds:
        return "unknown"
    lag = (now - _as_utc(synced_at)).total_seconds()
    if lag >= 2 * interval_seconds:
        return "crit"
    if lag >= interval_seconds:
        return "warn"
    return "ok"


def _raise_severity(current: str, floor: str) -> str:
    if _SEV_RANK.get(current, 0) >= _SEV_RANK.get(floor, 0):
        return current
    return floor


def _latest_attributed_job(
    session: Session,
    *,
    job_name: str,
    shop_id: str,
    status: str | None,
    now: datetime,
) -> SyncJob | None:
    filters = [
        SyncJob.job_name == job_name,
        SyncJob.extra["shop_id"].astext == shop_id,
        SyncJob.started_at >= now - _JOB_LOOKBACK,
    ]
    if status is not None:
        filters.append(SyncJob.status == status)
    return session.execute(
        select(SyncJob).where(*filters).order_by(SyncJob.started_at.desc()).limit(1)
    ).scalar_one_or_none()


def _cursor_updated_at(
    session: Session, job_name: str, shop_id: str
) -> datetime | None:
    stamp = session.execute(
        select(SyncCursor.updated_at).where(
            SyncCursor.job_name == job_name,
            SyncCursor.scope == shop_id,
        )
    ).scalar_one_or_none()
    return _as_utc(stamp) if stamp is not None else None


def _job_synced_at(row: SyncJob | None) -> datetime | None:
    if row is None:
        return None
    stamp = row.finished_at if row.finished_at is not None else row.started_at
    return _as_utc(stamp)


def _failed_after(latest: SyncJob | None, success: SyncJob | None) -> bool:
    if latest is None or latest.status != "failed":
        return False
    if success is None:
        return True
    return _as_utc(latest.started_at) > _as_utc(success.started_at)


def _source(
    *,
    key: str,
    label: str,
    scope: str,
    synced_at: datetime | None,
    basis: str,
    interval_seconds: int | None,
    job_name: str | None,
    last_status: str | None,
    now: datetime,
    failed_recently: bool,
    detail: str,
) -> FreshnessSourceOut:
    severity = _freshness_severity(synced_at, interval_seconds, now)
    if failed_recently:
        severity = _raise_severity(severity, "warn")
        detail = f"{detail} · 最近一次运行失败"
    return FreshnessSourceOut(
        key=key,
        label=label,
        synced_at=_as_utc(synced_at) if synced_at is not None else None,
        scope=scope,
        basis=basis,
        job_name=job_name,
        last_status=last_status,
        severity=severity,
        detail=detail,
    )


def _shop_job_source(
    session: Session,
    *,
    key: str,
    label: str,
    job_name: str,
    shop_id: str,
    now: datetime,
) -> FreshnessSourceOut:
    interval = JOBS[job_name].interval_seconds
    success = _latest_attributed_job(
        session, job_name=job_name, shop_id=shop_id, status="succeeded", now=now
    )
    latest = _latest_attributed_job(
        session, job_name=job_name, shop_id=shop_id, status=None, now=now
    )
    synced_at = _job_synced_at(success)
    if synced_at is not None:
        basis = "job"
        detail = "最近一次成功同步"
    else:
        synced_at = _cursor_updated_at(session, job_name, shop_id)
        if synced_at is not None:
            basis = "cursor"
            detail = "游标更新时间（作业尚未按店归因）"
        else:
            basis = "none"
            detail = "尚未同步"
    return _source(
        key=key,
        label=label,
        scope="shop",
        synced_at=synced_at,
        basis=basis,
        interval_seconds=interval,
        job_name=job_name,
        last_status=latest.status if latest is not None else None,
        now=now,
        failed_recently=_failed_after(latest, success),
        detail=detail,
    )


def _ads_synced_at(session: Session, shop_id: str) -> datetime | None:
    stamps: list[datetime] = []
    for model in (AdDaily, AdToday):
        stamp = session.execute(
            select(func.max(model.updated_at)).where(model.seller_id == shop_id)
        ).scalar_one()
        if stamp is not None:
            stamps.append(_as_utc(stamp))
    return max(stamps) if stamps else None


def _ads_source(session: Session, shop_id: str, now: datetime) -> FreshnessSourceOut:
    synced_at = _ads_synced_at(session, shop_id)
    return _source(
        key="ads",
        label="广告",
        scope="shop",
        synced_at=synced_at,
        basis="rows" if synced_at is not None else "none",
        interval_seconds=_ADS_INTERVAL_SECONDS,
        job_name=None,
        last_status=None,
        now=now,
        failed_recently=False,
        detail="该店广告事实最近写入" if synced_at is not None else "尚未写入广告数据",
    )


def _latest_miaoshou(
    session: Session, *, status: str | None, now: datetime
) -> SyncJob | None:
    names = [name for name in JOBS if name.startswith("miaoshou.")]
    filters = [
        SyncJob.job_name.in_(names),
        SyncJob.started_at >= now - _JOB_LOOKBACK,
    ]
    if status is not None:
        filters.append(SyncJob.status == status)
    return session.execute(
        select(SyncJob).where(*filters).order_by(SyncJob.started_at.desc()).limit(1)
    ).scalar_one_or_none()


def _miaoshou_source(session: Session, now: datetime) -> FreshnessSourceOut:
    success = _latest_miaoshou(session, status="succeeded", now=now)
    latest = _latest_miaoshou(session, status=None, now=now)
    synced_at = _job_synced_at(success)
    job_name = success.job_name if success is not None else None
    interval = JOBS[job_name].interval_seconds if job_name in JOBS else None
    if synced_at is not None:
        basis = "job"
        detail = f"最近成功：{job_name}"
    else:
        basis = "none"
        detail = "尚未同步"
    return _source(
        key="miaoshou",
        label="妙手",
        scope="system",
        synced_at=synced_at,
        basis=basis,
        interval_seconds=interval,
        job_name=job_name,
        last_status=latest.status if latest is not None else None,
        now=now,
        failed_recently=_failed_after(latest, success),
        detail=detail,
    )


@router.get("/freshness", response_model=SyncFreshnessOut)
def sync_freshness(
    session: SessionDep,
    shop_pk: int = Query(..., ge=1),
) -> SyncFreshnessOut:
    """当前店铺四类数据的最近同步时间。只读，零上游外呼。"""
    shop = session.execute(
        select(ChannelAccount).where(
            ChannelAccount.id == shop_pk,
            ChannelAccount.platform == "tiktok",
        )
    ).scalar_one_or_none()
    if shop is None:
        raise HTTPException(status_code=404, detail="shop not found")

    now = datetime.now(UTC)
    sources = [
        _ads_source(session, shop.shop_id, now),
        _shop_job_source(
            session,
            key="orders",
            label="订单",
            job_name="tiktok.orders",
            shop_id=shop.shop_id,
            now=now,
        ),
        _shop_job_source(
            session,
            key="logistics",
            label="物流",
            job_name="tiktok.logistics",
            shop_id=shop.shop_id,
            now=now,
        ),
        _miaoshou_source(session, now),
    ]
    return SyncFreshnessOut(
        server_time=now,
        shop_pk=shop.id,
        shop_id=shop.shop_id,
        sources=sources,
    )


__all__ = ["router"]
