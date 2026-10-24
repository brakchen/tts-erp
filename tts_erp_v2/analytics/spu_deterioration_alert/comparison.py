"""PRD v3 read orchestration; legacy alert jobs and policy remain independent."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta, tzinfo
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_deterioration_alert.availability import (
    AD_ENDPOINT,
    Availability,
    ComparisonUnavailable,
    order_limitations,
    resolve_availability,
    source_issue_limitations,
    unknown_ad_products,
)
from tts_erp_v2.analytics.spu_profitability import (
    ExactIdsSelection,
    ProfitScope,
    RowView,
    SpuProfitability,
    consistent_read_snapshot,
    count_orders_in_snapshot,
    read_overview_in_snapshot,
    reporting_timezone,
)
from tts_erp_v2.db.constants import PAID_SALES_ORDER_STATUSES
from tts_erp_v2.db.models import (
    AdDaily,
    ChannelAccount,
    ChannelProduct,
    SalesOrder,
    SalesOrderLine,
)


class ComparisonInputError(ValueError):
    def __init__(self, message: str, *, latest_date: date | None = None):
        super().__init__(message)
        self.latest_date = latest_date


def periods(
    anchor: date, window_days: int
) -> tuple[tuple[date, date], tuple[date, date]]:
    if window_days not in (3, 7):
        raise ComparisonInputError("window_days must be 3 or 7")
    return (
        (
            anchor - timedelta(days=2 * window_days - 1),
            anchor - timedelta(days=window_days),
        ),
        (anchor - timedelta(days=window_days - 1), anchor),
    )


def no_order_ad_spend(orders: int | None, spend: Decimal | None) -> bool | None:
    if orders is None or spend is None:
        return None
    return orders == 0 and spend > 0


def _recent_scope(
    session: Session,
    *,
    shop: ChannelAccount,
    products: list[ChannelProduct],
    anchor: date,
    timezone: tzinfo,
    availability: Availability,
) -> list[ChannelProduct]:
    start = anchor - timedelta(days=6)
    problems = order_limitations(
        session, shop=shop, timezone=timezone, start=None, end=anchor
    )
    if (
        problems
        or availability.ad_gaps(start, anchor)
        or unknown_ad_products(
            session,
            shop=shop,
            start=start,
            end=anchor,
        )
    ):
        raise ComparisonUnavailable(
            "活动范围所需订单/广告事实不完整；可用精确 SPU 或 activity=all 调查并查看限制。"
        )
    local_end = datetime.combine(anchor + timedelta(days=1), time.min, timezone)
    local_start = datetime.combine(start, time.min, timezone)
    event_time = func.coalesce(SalesOrder.order_time, SalesOrder.paid_at)
    order_counts = session.execute(
        select(
            SalesOrderLine.spu_pk,
            func.count(func.distinct(SalesOrder.id)),
            func.count(func.distinct(SalesOrder.id)).filter(event_time >= local_start),
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.order_pk)
        .where(
            SalesOrder.shop_pk == shop.id,
            SalesOrder.status.in_(PAID_SALES_ORDER_STATUSES),
            event_time < local_end,
        )
        .group_by(SalesOrderLine.spu_pk)
    ).all()
    counts = {pk: (lifetime, recent) for pk, lifetime, recent in order_counts}
    spend_rows = session.execute(
        select(AdDaily.product_id, func.sum(AdDaily.mixed_real_cost))
        .where(
            AdDaily.seller_id == shop.shop_id,
            AdDaily.endpoint == AD_ENDPOINT,
            AdDaily.day.between(start, anchor),
        )
        .group_by(AdDaily.product_id)
    ).all()
    spend = {pid: Decimal(str(value)) for pid, value in spend_rows if value is not None}
    return [
        product
        for product in products
        if (
            (
                counts.get(product.id, (0, 0))[0] >= 3
                and counts.get(product.id, (0, 0))[1] > 0
            )
            or (
                counts.get(product.id, (0, 0))[1] == 0
                and spend.get(product.spu_id, 0) > 0
            )
        )
    ]


def _window(
    session: Session,
    *,
    shop: ChannelAccount,
    products: list[ChannelProduct],
    start: date,
    end: date,
    calculated_at: datetime,
) -> tuple[dict[int, SpuProfitability], list[str]]:
    items = {}
    warnings: list[str] = []
    for offset in range(0, len(products), 100):
        batch = products[offset : offset + 100]
        overview = read_overview_in_snapshot(
            session,
            scope=ProfitScope(
                shop_pk=shop.id,
                start_date=start,
                end_date=end,
                include_inactive=True,
                selection=ExactIdsSelection(tuple(product.spu_id for product in batch)),
            ),
            view=RowView(limit=100),
            calculated_at=calculated_at,
        )
        items.update({item.spu_pk: item for item in overview.items})
        warnings.extend(overview.basis.warnings)
    if len(items) != len(products):
        raise ComparisonUnavailable("盈利计算未返回完整已应用 SPU 范围")
    return items, list(dict.fromkeys(warnings))


def read_comparison(
    session: Session,
    *,
    shop_pk: int,
    spu_pks: list[int] | None = None,
    window_days: int = 7,
    activity: str = "recent",
    anchor_date: date | None = None,
    limit: int = 100,
    offset: int = 0,
    availability_resolver: Callable[..., Availability] = resolve_availability,
) -> dict[str, Any]:
    """Read availability, scope, both periods and full-scope totals in one snapshot."""
    if window_days not in (3, 7) or activity not in ("recent", "all"):
        raise ComparisonInputError(
            "window_days must be 3 or 7; activity must be recent or all"
        )
    if spu_pks is not None and (
        not spu_pks or len(spu_pks) > 100 or any(pk < 1 for pk in spu_pks)
    ):
        raise ComparisonInputError("spu_ids must contain 1..100 positive internal ids")
    with consistent_read_snapshot(session) as calculated_at:
        shop = session.get(ChannelAccount, shop_pk)
        if shop is None or shop.platform != "tiktok":
            raise ComparisonInputError("shop_pk does not identify a TikTok shop")
        timezone = reporting_timezone(session, shop_pk=shop_pk)
        availability = availability_resolver(
            session, shop=shop, timezone=timezone, now=calculated_at
        )
        anchor = anchor_date or availability.latest_date
        if anchor > availability.latest_date:
            raise ComparisonInputError(
                "anchor_date exceeds latestAvailableDate",
                latest_date=availability.latest_date,
            )
        previous, current = periods(anchor, window_days)
        products = list(
            session.scalars(
                select(ChannelProduct)
                .where(
                    ChannelProduct.shop_pk == shop_pk,
                )
                .order_by(ChannelProduct.id)
            ).all()
        )
        if spu_pks is not None:
            requested = set(spu_pks)
            products = [product for product in products if product.id in requested]
            if len(products) != len(requested):
                raise ComparisonInputError(
                    "spu_ids contains unknown or cross-shop internal ids"
                )
        elif activity == "recent":
            products = _recent_scope(
                session,
                shop=shop,
                products=products,
                anchor=anchor,
                timezone=timezone,
                availability=availability,
            )
        warnings = list(availability.warnings)
        warnings.append(
            "历史经营日期按当前成本/汇率及已确认退款重新估值；未结算平台费可能为估算，并非最终会计利润。"
        )
        if window_days == 3:
            warnings.append(
                "3 天仅观察近期变化，易受少量订单和投放节奏影响，不代表持续改善或恶化。"
            )
        page_products = products[offset : offset + limit]
        financial_problems = source_issue_limitations(
            session,
            shop=shop,
            job_names=("tiktok.after_sales", "tiktok.finance"),
        )
        warnings.extend(financial_problems)
        values: dict[str, dict[int, SpuProfitability]] = {}
        order_problems: dict[str, list[str]] = {}
        ad_problems: dict[str, list[str]] = {}
        unknown_costs: dict[str, set[str]] = {}
        totals: dict[str, Any] = {
            "analysisSpuCount": len(products),
            "unavailableReason": None,
        }
        product_pks = tuple(product.id for product in products)
        for label, (start, end) in (("previous", previous), ("current", current)):
            values[label], basis_warnings = _window(
                session,
                shop=shop,
                products=page_products,
                start=start,
                end=end,
                calculated_at=calculated_at,
            )
            warnings.extend(basis_warnings)
            order_problems[label] = order_limitations(
                session, shop=shop, timezone=timezone, start=start, end=end
            )
            gaps = availability.ad_gaps(start, end)
            unknown_costs[label] = unknown_ad_products(
                session, shop=shop, start=start, end=end
            )
            if unknown_costs[label]:
                warnings.append(
                    "广告行缺少消耗金额，涉及 SPU 的消耗及盈利指标不可确认。"
                )
            ad_problems[label] = (
                [
                    "广告观察缺日，消耗及依赖盈利指标不可确认："
                    + ", ".join(d.isoformat() for d in sorted(gaps))
                ]
                if gaps
                else []
            )
            totals[label + "OrderCount"] = (
                None
                if order_problems[label]
                else count_orders_in_snapshot(
                    session,
                    shop_pk=shop_pk,
                    spu_pks=product_pks,
                    start_date=start,
                    end_date=end,
                )
            )
            if order_problems[label]:
                totals["unavailableReason"] = (
                    "订单解析或关联事实缺失，订单统计不可确认。"
                )
            warnings.extend(order_problems[label] + ad_problems[label])
        items = []
        for product in page_products:
            item: dict[str, Any] = {
                "shopPk": shop_pk,
                "spuPk": product.id,
                "spuId": product.spu_id,
                "anchorDate": anchor,
                "windowDays": window_days,
                "basisCalculatedAt": calculated_at,
                "warnings": [],
            }
            for label in ("previous", "current"):
                metric = values[label][product.id]
                orders = metric.order_count if not order_problems[label] else None
                spend_problems = ad_problems[label] + (
                    ["广告行缺少消耗金额，消耗及盈利指标不可确认。"]
                    if product.spu_id in unknown_costs[label]
                    else []
                )
                spend = metric.spend if not spend_problems else None
                problems = order_problems[label] + spend_problems + financial_problems
                roi = metric.roi_real if not problems else None
                item.update(
                    {
                        label + "OrderCount": orders,
                        label + "SpendCny": spend,
                        label + "NetProfitCny": metric.net_profit
                        if not problems
                        else None,
                        label + "Roi": roi,
                        label + "NoOrderAdSpend": no_order_ad_spend(orders, spend),
                        label + "RoiUnavailableReason": "; ".join(problems)
                        if problems
                        else ("广告消耗为零，ROI 无法计算。" if roi is None else None),
                    }
                )
                item["warnings"].extend(problems)
            item["warnings"] = list(dict.fromkeys(item["warnings"] + warnings))
            dates = {"w_start": current[0].isoformat(), "w_end": current[1].isoformat()}
            item["drilldown"] = {
                "pageUrl": "/v2/pages/spu-roi?"
                + urlencode({"shop_pk": shop_pk, "spu_ids": product.spu_id, **dates}),
                "period": "current",
                "startDate": current[0],
                "endDate": current[1],
            }
            items.append(item)
        return {
            "items": items,
            "total": len(products),
            "totals": totals,
            "meta": {
                "shopPk": shop_pk,
                "anchorDate": anchor,
                "latestAvailableDate": availability.latest_date,
                "windowDays": window_days,
                "reportingTimezone": str(timezone),
                "periods": {
                    label: {"startDate": span[0], "endDate": span[1]}
                    for label, span in (("previous", previous), ("current", current))
                },
                "dataAsOf": anchor,
                "calculatedAt": calculated_at,
                "stale": availability.latest_date
                < calculated_at.astimezone(timezone).date() - timedelta(days=1),
                "coverage": availability.coverage(anchor),
                "warnings": list(dict.fromkeys(warnings)),
                "activityBypassed": spu_pks is not None,
                "scopeDescription": (
                    "精确内部 SPU 主键调查；未应用活动排除，累计仅限库内历史。"
                    if spu_pks is not None
                    else "全部本店 SPU 调查；未应用活动排除。"
                    if activity == "all"
                    else "截至日近7天有单且库内累计已付款至少3单；另保留近7天实际零单且正广告费的 SPU。"
                ),
            },
        }
