"""Observed source availability, not a certificate of exhaustive ingestion.

Successful runs have no persisted query-through bounds. Their *start* local day
minus one is an inferred operational cutoff; campaign/day presence only supports
observed advertising scope. Neither a cursor nor the newest business fact is a
completeness watermark.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from sqlalchemy import cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from tts_erp_v2.db.constants import (
    PAID_SALES_ORDER_STATUSES,
    UNPAID_SALES_ORDER_STATUSES,
)
from tts_erp_v2.db.models import (
    AdDaily,
    AdRawLog,
    ChannelAccount,
    ChannelProduct,
    SalesOrder,
    SalesOrderLine,
    SyncIssue,
    SyncJob,
)

AD_ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"
SOURCE_JOBS = ("tiktok.orders", "tiktok.after_sales", "tiktok.finance")


class ComparisonUnavailable(RuntimeError):
    """A safe, user-facing explanation for unavailable comparison evidence."""


def days_between(start: date, end: date) -> set[date]:
    return {start + timedelta(days=n) for n in range((end - start).days + 1)}


@dataclass(frozen=True)
class Availability:
    latest_date: date
    sources: dict[str, Any]
    observations: dict[tuple[str, str], frozenset[date]]
    warnings: tuple[str, ...]

    def ad_gaps(self, start: date, end: date) -> set[date]:
        requested = days_between(start, end)
        observed = set().union(*self.observations.values())
        gaps = requested - observed
        # Require continuity inside an observed campaign's lifespan, not forever
        # after a campaign stopped or before it was first observed.
        for observed_days in self.observations.values():
            first, last = min(observed_days), max(observed_days)
            gaps |= {day for day in requested if first <= day <= last} - observed_days
        return gaps

    def coverage(self, anchor: date) -> dict[str, Any]:
        start = anchor - timedelta(days=13)
        return {
            "status": "limited",
            "basis": "observed_sources_and_inferred_operational_cutoff",
            "sources": self.sources,
            "advertising": {
                "endpoint": AD_ENDPOINT,
                "startDate": start.isoformat(),
                "endDate": anchor.isoformat(),
                "missingDates": sorted(
                    day.isoformat() for day in self.ad_gaps(start, anchor)
                ),
                "observedScopes": [
                    {
                        "advertiserId": advertiser,
                        "campaignId": campaign,
                        "days": sorted(
                            d.isoformat() for d in observed if start <= d <= anchor
                        ),
                    }
                    for (advertiser, campaign), observed in sorted(
                        self.observations.items()
                    )
                    if any(start <= d <= anchor for d in observed)
                ],
            },
            "limitations": list(self.warnings),
        }


def resolve_availability(
    session: Session,
    *,
    shop: ChannelAccount,
    timezone: tzinfo,
    now: datetime,
) -> Availability:
    """Combine attributed source health and observed canonical ad days in-snapshot."""
    cutoff = now.astimezone(timezone).date() - timedelta(days=1)
    sources: dict[str, Any] = {}
    warnings = [
        "可用日期为成功采集启动日推断的经营截止日，不是已验证的完整采集水位。",
        "广告覆盖仅限已观察 seller/advertiser/campaign/day；不证明活动发现或分页完整，活动首末观察之外可能缺数据。",
    ]
    for name in SOURCE_JOBS:
        source_scope = (
            SyncJob.job_name == name,
            SyncJob.extra["shop_id"].astext == shop.shop_id,
        )
        ordering = (SyncJob.started_at.desc(), SyncJob.id.desc())
        valid = session.scalar(
            select(SyncJob)
            .where(
                *source_scope,
                SyncJob.status == "succeeded",
                SyncJob.finished_at.is_not(None),
                SyncJob.rows_failed == 0,
                SyncJob.started_at <= SyncJob.finished_at,
                SyncJob.finished_at <= now,
            )
            .order_by(*ordering)
            .limit(1)
        )
        if valid is None or valid.finished_at is None:
            raise ComparisonUnavailable(
                f"{name} 缺少归属本店、已结束且无已报告失败的采集依据"
            )
        finished_at = valid.finished_at
        assert finished_at is not None
        operational = valid.started_at.astimezone(timezone).date() - timedelta(days=1)
        cutoff = min(cutoff, operational)
        sources[name] = {
            "status": "observed",
            "runId": valid.id,
            "startedAt": valid.started_at.isoformat(),
            "finishedAt": finished_at.isoformat(),
            "operationalCutoff": operational.isoformat(),
            "reportedFailures": valid.rows_failed,
        }
        latest_started = session.scalar(
            select(SyncJob.started_at).where(*source_scope).order_by(*ordering).limit(1)
        )
        if latest_started is not None and latest_started > valid.started_at:
            warnings.append(f"{name} 有较晚未成功/未完成采集，保留较早可用数据。")
    observations: dict[tuple[str, str], set[date]] = defaultdict(set)
    daily = session.execute(
        select(
            AdDaily.advertiser_id,
            AdDaily.campaign_id,
            AdDaily.day,
        )
        .where(
            AdDaily.seller_id == shop.shop_id,
            AdDaily.endpoint == AD_ENDPOINT,
            AdDaily.day <= cutoff,
        )
        .distinct()
        .execution_options(yield_per=500)
    )
    for advertiser, campaign, day in daily:
        observations[(advertiser, campaign)].add(day)
    # The archive stores dump.response (status/body). Validate empty success in
    # SQL and stream only distinct scope/day keys, never whole response blobs.
    # Keep all historical days: truncating to a guessed horizon would lose old
    # readable results and campaign-lifespan evidence.
    body = AdRawLog.response_body["body"]
    raw_rows = session.execute(
        select(AdRawLog.advertiser_id, AdRawLog.campaign_id, AdRawLog.day)
        .where(
            AdRawLog.seller_id == shop.shop_id,
            AdRawLog.endpoint == AD_ENDPOINT,
            AdRawLog.kind == "daily",
            AdRawLog.day <= cutoff,
            AdRawLog.campaign_id.is_not(None),
            AdRawLog.response_status >= 200,
            AdRawLog.response_status < 300,
            func.jsonb_typeof(body) == "object",
            func.jsonb_typeof(body["data"]) == "object",
            body["code"] == cast(literal("0"), JSONB),
            body["data"]["table"] == cast(literal("[]"), JSONB),
        )
        .distinct()
        .execution_options(yield_per=500)
    )
    for advertiser, campaign, day in raw_rows:
        assert campaign is not None and day is not None
        observations[(advertiser, campaign)].add(day)
    if not observations:
        raise ComparisonUnavailable(
            "缺少本店盈利广告端点的日观察依据（空响应也必须有效且成功）"
        )
    latest = min(
        cutoff, max(day for observed in observations.values() for day in observed)
    )
    return Availability(
        latest,
        sources,
        {key: frozenset(value) for key, value in observations.items()},
        tuple(warnings),
    )


def unknown_ad_products(
    session: Session,
    *,
    shop: ChannelAccount,
    start: date,
    end: date,
) -> set[str]:
    """A present ad row without cost is not an observed zero cost."""
    return set(
        session.scalars(
            select(AdDaily.product_id)
            .where(
                AdDaily.seller_id == shop.shop_id,
                AdDaily.endpoint == AD_ENDPOINT,
                AdDaily.day.between(start, end),
                AdDaily.mixed_real_cost.is_(None),
            )
            .distinct()
        ).all()
    )


def source_issue_limitations(
    session: Session,
    *,
    shop: ChannelAccount,
    job_names: tuple[str, ...],
) -> list[str]:
    """Unattributed issues conservatively affect every shop, not just one."""
    limitations = []
    # PostgreSQL returns NULL for absent keys and non-object JSON. Treat these
    # as unattributed issues rather than loading blobs or calling dict.get().
    attributed_shop = SyncIssue.details["shop_id"].astext
    for name in sorted(job_names):
        issue_id = session.scalar(
            select(SyncIssue.id)
            .where(
                SyncIssue.job_name == name,
                SyncIssue.resolved_at.is_(None),
                SyncIssue.issue_type != "STALE_OBSERVATION_SKIPPED",
                or_(attributed_shop.is_(None), attributed_shop == shop.shop_id),
            )
            .limit(1)
        )
        if issue_id is not None:
            limitations.append(
                f"{name} 存在未解决解析/关联问题（部分问题未归属店铺），相关事实不可确认。"
            )
    return limitations


def order_limitations(
    session: Session,
    *,
    shop: ChannelAccount,
    timezone: tzinfo,
    start: date | None,
    end: date,
) -> list[str]:
    """Known header/line/link failures must never be reinterpreted as zero orders."""
    end_utc = datetime.combine(end + timedelta(days=1), time.min, timezone)
    event_time = func.coalesce(SalesOrder.order_time, SalesOrder.paid_at)
    unknown_status = or_(
        SalesOrder.status.is_(None),
        SalesOrder.status.not_in(
            PAID_SALES_ORDER_STATUSES | UNPAID_SALES_ORDER_STATUSES
        ),
    )
    conditions = [
        SalesOrder.shop_pk == shop.id,
        or_(
            SalesOrder.status.in_(PAID_SALES_ORDER_STATUSES),
            SalesOrder.status == "CANCELLED",
            unknown_status,
        ),
        or_(event_time < end_utc, event_time.is_(None)),
    ]
    if start is not None:
        conditions.append(
            or_(
                event_time >= datetime.combine(start, time.min, timezone),
                event_time.is_(None),
            )
        )
    has_lines = (
        select(SalesOrderLine.id)
        .where(
            SalesOrderLine.order_pk == SalesOrder.id,
        )
        .exists()
    )
    has_bad_line = (
        select(SalesOrderLine.id)
        .outerjoin(
            ChannelProduct,
            ChannelProduct.id == SalesOrderLine.spu_pk,
        )
        .where(
            SalesOrderLine.order_pk == SalesOrder.id,
            or_(
                ChannelProduct.shop_pk.is_(None),
                ChannelProduct.shop_pk != shop.id,
                SalesOrderLine.quantity.is_(None),
                SalesOrderLine.unit_price.is_(None),
            ),
        )
        .exists()
    )
    missing = session.scalar(
        select(SalesOrder.id)
        .where(
            *conditions,
            or_(unknown_status, event_time.is_(None), ~has_lines, has_bad_line),
        )
        .limit(1)
    )
    limitations = []
    if missing is not None:
        limitations.append(
            "订单存在状态未知、日期、商品行、金额或 SPU 关联缺失，不能确认实际订单数/利润。"
        )
    return limitations + source_issue_limitations(
        session,
        shop=shop,
        job_names=("tiktok.orders",),
    )
