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

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from tts_erp_v2.db.constants import PAID_SALES_ORDER_STATUSES
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


def _valid_empty_observation(row: AdRawLog) -> bool:
    # The archive stores dump.response (status/body), not just the upstream body.
    response = row.response_body
    if not isinstance(response, dict) or not isinstance(response.get("body"), dict):
        return False
    body = response["body"]
    data = body.get("data")
    return (
        row.response_status is not None
        and 200 <= row.response_status < 300
        and body.get("code") == 0
        and isinstance(data, dict)
        and isinstance(data.get("table"), list)
        and data["table"] == []
        and row.campaign_id is not None
        and row.day is not None
    )


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
            gaps |= (requested & days_between(first, last)) - observed_days
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
        runs = session.scalars(
            select(SyncJob)
            .where(
                SyncJob.job_name == name,
                SyncJob.extra["shop_id"].astext == shop.shop_id,
            )
            .order_by(SyncJob.started_at.desc(), SyncJob.id.desc())
        ).all()
        valid = next(
            (
                run
                for run in runs
                if run.status == "succeeded"
                and run.finished_at is not None
                and run.rows_failed == 0
                and run.started_at <= run.finished_at <= now
            ),
            None,
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
        if any(run.started_at > valid.started_at for run in runs):
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
    ).all()
    for advertiser, campaign, day in daily:
        observations[(advertiser, campaign)].add(day)
    raw_rows = session.scalars(
        select(AdRawLog).where(
            AdRawLog.seller_id == shop.shop_id,
            AdRawLog.endpoint == AD_ENDPOINT,
            AdRawLog.kind == "daily",
            AdRawLog.day <= cutoff,
        )
    ).all()
    for row in raw_rows:
        if _valid_empty_observation(row):
            assert row.campaign_id is not None and row.day is not None
            observations[(row.advertiser_id, row.campaign_id)].add(row.day)
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
    issues = session.scalars(
        select(SyncIssue).where(
            SyncIssue.job_name.in_(job_names),
            SyncIssue.resolved_at.is_(None),
            SyncIssue.issue_type != "STALE_OBSERVATION_SKIPPED",
        )
    ).all()
    affected = {
        issue.job_name
        for issue in issues
        if (issue.details or {}).get("shop_id") is None
        or str((issue.details or {}).get("shop_id")) == shop.shop_id
    }
    return [
        f"{name} 存在未解决解析/关联问题（部分问题未归属店铺），相关事实不可确认。"
        for name in sorted(affected)
    ]


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
    conditions = [
        SalesOrder.shop_pk == shop.id,
        or_(
            SalesOrder.status.in_(PAID_SALES_ORDER_STATUSES),
            SalesOrder.status == "CANCELLED",
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
    missing = session.scalar(
        select(SalesOrder.id)
        .outerjoin(
            SalesOrderLine,
            SalesOrderLine.order_pk == SalesOrder.id,
        )
        .outerjoin(ChannelProduct, ChannelProduct.id == SalesOrderLine.spu_pk)
        .where(
            *conditions,
            or_(
                event_time.is_(None),
                SalesOrderLine.id.is_(None),
                ChannelProduct.shop_pk.is_(None),
                ChannelProduct.shop_pk != shop.id,
                SalesOrderLine.quantity.is_(None),
                SalesOrderLine.unit_price.is_(None),
            ),
        )
        .limit(1)
    )
    limitations = []
    if missing is not None:
        limitations.append(
            "订单存在日期、商品行、金额或 SPU 关联缺失，不能确认实际订单数/利润。"
        )
    return limitations + source_issue_limitations(
        session,
        shop=shop,
        job_names=("tiktok.orders",),
    )
