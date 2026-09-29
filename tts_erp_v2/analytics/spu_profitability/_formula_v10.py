"""Pure v10 SPU profitability formula.

No SQL, HTTP, formatting, or clock access belongs here.  Keeping the formula pure
makes the business rubric independently testable while the enclosing deep module
retains ownership of facts and persistence details.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class FormulaInput:
    spend_usd: Decimal
    ad_gmv_usd: Decimal
    ad_orders: int
    order_count: int
    cancelled_orders: int
    domestic_cancelled_orders: int
    overseas_cancelled_orders: int
    units_sold: int
    full_loss_cancelled_qty: int
    full_loss_qty: int
    refund_order_count: int
    refund_only_qty: int
    refund_return_qty: int
    sales_vnd: Decimal
    settled_net_vnd: Decimal
    settled_sales_vnd: Decimal
    unsettled_sales_vnd: Decimal
    refund_only_vnd: Decimal
    refund_return_vnd: Decimal
    refund_cancelled_vnd: Decimal
    cancelled_sales_vnd: Decimal
    unit_cost_cny: Decimal
    cny_usd: Decimal
    usd_vnd: Decimal
    unsettled_fee_rate: Decimal


@dataclass(frozen=True, slots=True)
class FormulaOutput:
    unit_cost_usd: Decimal
    sales_usd: Decimal
    effective_sales_usd: Decimal
    total_orders: int
    effective_order_count: int
    refund_order_count: int
    full_loss_order_count: int
    settled_net_usd: Decimal
    settled_sales_usd: Decimal
    unsettled_sales_usd: Decimal
    refund_only_usd: Decimal
    refund_return_usd: Decimal
    refund_net_usd: Decimal
    refund_cancelled_usd: Decimal
    cancelled_sales_usd: Decimal
    net_revenue_usd: Decimal
    unsettled_net_usd: Decimal
    cogs_sold_usd: Decimal
    cogs_full_loss_cancelled_usd: Decimal
    cogs_all_usd: Decimal
    cogs_kept_usd: Decimal
    platform_fee_usd: Decimal
    return_loss_usd: Decimal
    net_profit_usd: Decimal
    roi_real: Decimal | None
    roi_breakeven: Decimal | None
    ad_system_actual_roi: Decimal | None
    ad_system_breakeven_roi: Decimal | None
    ad_system_max_ad_spend_usd: Decimal
    ad_system_remaining_ad_spend_capacity_usd: Decimal
    cpa_usd: Decimal | None
    roi_l0: Decimal | None
    refund_rate: Decimal | None
    refund_amount_rate: Decimal | None
    refund_rate_qty: Decimal | None
    cancel_rate: Decimal | None
    full_loss_rate: Decimal | None
    full_loss_qty_rate: Decimal | None
    gmv_sales_usd: Decimal


@dataclass(frozen=True, slots=True)
class OrderMetrics:
    """Canonical order-dimension metrics shared by SPU rows and the dashboard."""

    total_orders: int
    effective_order_count: int
    refund_order_count: int
    full_loss_order_count: int
    refund_rate: Decimal | None
    full_loss_rate: Decimal | None
    cancel_rate: Decimal | None


def calculate_order_metrics(
    *,
    order_count: int,
    cancelled_orders: int,
    domestic_cancelled_orders: int,
    overseas_cancelled_orders: int,
    refund_order_count: int,
) -> OrderMetrics:
    """Apply the v10 order funnel definitions at any aggregation scope."""

    total_orders = order_count + cancelled_orders
    effective_order_count = max(0, order_count - refund_order_count)
    full_loss_order_count = refund_order_count + overseas_cancelled_orders

    def rate(numerator: int) -> Decimal | None:
        if total_orders <= 0:
            return None
        return Decimal(numerator) / Decimal(total_orders)

    return OrderMetrics(
        total_orders=total_orders,
        effective_order_count=effective_order_count,
        refund_order_count=refund_order_count,
        full_loss_order_count=full_loss_order_count,
        refund_rate=rate(refund_order_count),
        full_loss_rate=rate(full_loss_order_count),
        cancel_rate=rate(domestic_cancelled_orders),
    )


def calculate(inputs: FormulaInput) -> FormulaOutput:
    """Calculate one SPU using the canonical v10 profitability rubric."""

    unit_cost_usd = inputs.unit_cost_cny * inputs.cny_usd
    sales_usd = inputs.sales_vnd / inputs.usd_vnd
    settled_net_usd = inputs.settled_net_vnd / inputs.usd_vnd
    settled_sales_usd = inputs.settled_sales_vnd / inputs.usd_vnd
    unsettled_sales_usd = inputs.unsettled_sales_vnd / inputs.usd_vnd
    refund_only_usd = inputs.refund_only_vnd / inputs.usd_vnd
    refund_return_usd = inputs.refund_return_vnd / inputs.usd_vnd
    refund_net_usd = refund_only_usd + refund_return_usd
    effective_sales_usd = sales_usd - refund_net_usd
    refund_cancelled_usd = inputs.refund_cancelled_vnd / inputs.usd_vnd
    cancelled_sales_usd = inputs.cancelled_sales_vnd / inputs.usd_vnd

    order_metrics = calculate_order_metrics(
        order_count=inputs.order_count,
        cancelled_orders=inputs.cancelled_orders,
        domestic_cancelled_orders=inputs.domestic_cancelled_orders,
        overseas_cancelled_orders=inputs.overseas_cancelled_orders,
        refund_order_count=inputs.refund_order_count,
    )

    refund_rate_spu = Decimal(0)
    if inputs.sales_vnd > 0:
        refund_rate_spu = min(
            Decimal(1),
            max(
                Decimal(0),
                (inputs.refund_only_vnd + inputs.refund_return_vnd)
                / inputs.sales_vnd,
            ),
        )

    unsettled_net_usd = unsettled_sales_usd * (
        Decimal(1) - inputs.unsettled_fee_rate
    ) * (Decimal(1) - refund_rate_spu)
    net_revenue_usd = settled_net_usd + unsettled_net_usd
    cogs_sold_usd = Decimal(inputs.units_sold) * unit_cost_usd
    cogs_full_loss_cancelled_usd = (
        Decimal(inputs.full_loss_cancelled_qty) * unit_cost_usd
    )
    cogs_all_usd = cogs_sold_usd + cogs_full_loss_cancelled_usd
    platform_fee_usd = inputs.unsettled_fee_rate * unsettled_sales_usd
    return_loss_usd = Decimal(inputs.full_loss_qty) * unit_cost_usd
    net_profit_usd = net_revenue_usd - cogs_all_usd - inputs.spend_usd

    roi_real = None
    if inputs.spend_usd != 0:
        roi_real = (net_revenue_usd - return_loss_usd) / inputs.spend_usd

    cogs_kept_usd = max(
        Decimal(0),
        Decimal(
            inputs.units_sold
            - inputs.refund_only_qty
            - inputs.refund_return_qty
        )
        * unit_cost_usd,
    )
    breakeven_denom = (net_revenue_usd - return_loss_usd) - cogs_kept_usd
    roi_breakeven = None
    if inputs.spend_usd != 0 and breakeven_denom > 0:
        roi_breakeven = (net_revenue_usd - return_loss_usd) / breakeven_denom

    cpa_usd = inputs.spend_usd / Decimal(inputs.ad_orders) if inputs.ad_orders else None
    ad_system_actual_roi = (
        inputs.ad_gmv_usd / inputs.spend_usd if inputs.spend_usd != 0 else None
    )

    # 广告后台口径使用广告归因 GMV 作为分子。当前系统尚未结构化录入
    # 退货运费、提现费、汇兑损失、包装耗材等结算外成本，因此这里给出的
    # 是“已知成本下限”估算；调用层必须通过状态与 warning 明示该限制。
    ad_system_max_ad_spend_usd = net_revenue_usd - cogs_all_usd
    ad_system_remaining_ad_spend_capacity_usd = (
        ad_system_max_ad_spend_usd - inputs.spend_usd
    )
    ad_system_breakeven_roi = None
    if ad_system_max_ad_spend_usd > 0 and inputs.ad_gmv_usd > 0:
        ad_system_breakeven_roi = (
            inputs.ad_gmv_usd / ad_system_max_ad_spend_usd
        )

    # roi_l0 是历史字段；保留为广告后台实际 ROI 的兼容别名。
    roi_l0 = ad_system_actual_roi

    # 保留金额/件数旧口径的显式字段，仅用于解释，不能再冒充大盘三率。
    refund_amount_rate = refund_net_usd / sales_usd if sales_usd > 0 else None
    refund_rate_qty = (
        Decimal(inputs.refund_order_count) / Decimal(inputs.order_count)
        if inputs.order_count > 0
        else None
    )
    full_loss_denom = inputs.units_sold + inputs.full_loss_cancelled_qty
    full_loss_qty_rate = (
        Decimal(inputs.full_loss_qty) / Decimal(full_loss_denom)
        if full_loss_denom > 0
        else None
    )

    return FormulaOutput(
        unit_cost_usd=unit_cost_usd,
        sales_usd=sales_usd,
        effective_sales_usd=effective_sales_usd,
        total_orders=order_metrics.total_orders,
        effective_order_count=order_metrics.effective_order_count,
        refund_order_count=order_metrics.refund_order_count,
        full_loss_order_count=order_metrics.full_loss_order_count,
        settled_net_usd=settled_net_usd,
        settled_sales_usd=settled_sales_usd,
        unsettled_sales_usd=unsettled_sales_usd,
        refund_only_usd=refund_only_usd,
        refund_return_usd=refund_return_usd,
        refund_net_usd=refund_net_usd,
        refund_cancelled_usd=refund_cancelled_usd,
        cancelled_sales_usd=cancelled_sales_usd,
        net_revenue_usd=net_revenue_usd,
        unsettled_net_usd=unsettled_net_usd,
        cogs_sold_usd=cogs_sold_usd,
        cogs_full_loss_cancelled_usd=cogs_full_loss_cancelled_usd,
        cogs_all_usd=cogs_all_usd,
        cogs_kept_usd=cogs_kept_usd,
        platform_fee_usd=platform_fee_usd,
        return_loss_usd=return_loss_usd,
        net_profit_usd=net_profit_usd,
        roi_real=roi_real,
        roi_breakeven=roi_breakeven,
        ad_system_actual_roi=ad_system_actual_roi,
        ad_system_breakeven_roi=ad_system_breakeven_roi,
        ad_system_max_ad_spend_usd=ad_system_max_ad_spend_usd,
        ad_system_remaining_ad_spend_capacity_usd=(
            ad_system_remaining_ad_spend_capacity_usd
        ),
        cpa_usd=cpa_usd,
        roi_l0=roi_l0,
        refund_rate=order_metrics.refund_rate,
        refund_amount_rate=refund_amount_rate,
        refund_rate_qty=refund_rate_qty,
        cancel_rate=order_metrics.cancel_rate,
        full_loss_rate=order_metrics.full_loss_rate,
        full_loss_qty_rate=full_loss_qty_rate,
        gmv_sales_usd=sales_usd + cancelled_sales_usd,
    )
