"""/v2/sync/* — sync-worker 作业同步状态（readonly）。

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
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from tts_erp_v2.api.deps import SessionDep
from tts_erp_v2.db.models import ChannelAccount
from tts_erp_v2.db.models.integration import Credentials, SyncJob
from tts_erp_v2.sync_worker.scheduler import (
    CONTROL_DISABLED,
    JOBS,
    NON_PRODUCTION_SHOP_PREFIXES,
    get_job_control_values,
)

router = APIRouter(prefix="/v2/sync", tags=["sync"])


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


@router.get("/jobs", response_model=SyncJobsOut)
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


__all__ = ["router"]
