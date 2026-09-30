"""Pure v10 SPU profitability formula.

No SQL, HTTP, formatting, or clock access belongs here.  Keeping the formula pure
makes the business rubric independently testable while the enclosing deep module
retains ownership of facts and persistence details.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tts_erp_v2.analytics.spu_profitability._types import ProjectionStatus


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
    usd_cny: Decimal
    usd_vnd: Decimal
    unsettled_fee_rate: Decimal


@dataclass(frozen=True, slots=True)
class FormulaOutput:
    unit_cost_cny: Decimal
    spend_cny: Decimal
    ad_gmv_cny: Decimal
    sales_cny: Decimal
    effective_sales_cny: Decimal
    total_orders: int
    effective_order_count: int
    refund_order_count: int
    full_loss_order_count: int
    settled_net_cny: Decimal
    settled_sales_cny: Decimal
    unsettled_sales_cny: Decimal
    refund_only_cny: Decimal
    refund_return_cny: Decimal
    refund_net_cny: Decimal
    refund_cancelled_cny: Decimal
    cancelled_sales_cny: Decimal
    net_revenue_cny: Decimal
    unsettled_net_cny: Decimal
    cogs_sold_cny: Decimal
    cogs_full_loss_cancelled_cny: Decimal
    cogs_all_cny: Decimal
    cogs_kept_cny: Decimal
    platform_fee_cny: Decimal
    return_loss_cny: Decimal
    net_profit_cny: Decimal
    roi_real: Decimal | None
    roi_breakeven: Decimal | None
    ad_system_actual_roi: Decimal | None
    ad_system_breakeven_roi: Decimal | None
    ad_system_max_ad_spend_cny: Decimal
    ad_system_remaining_ad_spend_capacity_cny: Decimal
    cpa_cny: Decimal | None
    roi_l0: Decimal | None
    refund_rate: Decimal | None
    refund_amount_rate: Decimal | None
    refund_rate_qty: Decimal | None
    cancel_rate: Decimal | None
    full_loss_rate: Decimal | None
    full_loss_qty_rate: Decimal | None
    gmv_sales_cny: Decimal


@dataclass(frozen=True, slots=True)
class ProjectionInput:
    """Date-scoped facts for projecting only the unsettled order cohort."""

    projection_basis_order_count: int
    projection_basis_qty: Decimal
    projection_basis_sales_cny: Decimal
    projection_basis_refund_amount_cny: Decimal
    projection_basis_full_loss_order_count: int
    projection_basis_full_loss_qty: Decimal
    unsettled_order_count: int
    confirmed_unsettled_full_loss_order_count: int
    confirmed_unsettled_full_loss_qty: Decimal
    unresolved_unsettled_order_count: int
    full_loss_exposure_unsettled_order_count: int
    confirmed_full_loss_exposure_order_count: int
    confirmed_full_loss_exposure_qty: Decimal
    unresolved_full_loss_exposure_order_count: int
    unresolved_full_loss_exposure_qty: Decimal
    unresolved_full_loss_exposure_cogs_cny: Decimal
    unsettled_sales_after_fee_cny: Decimal
    confirmed_unsettled_refund_after_fee_cny: Decimal
    unresolved_unsettled_qty: Decimal
    unresolved_unsettled_sales_after_fee_cny: Decimal
    unresolved_unsettled_cogs_cny: Decimal
    settled_net_cny: Decimal
    observed_full_loss_qty: Decimal
    observed_full_loss_cost_cny: Decimal
    current_unsettled_net_cny: Decimal
    current_cogs_kept_cny: Decimal
    cogs_total_cny: Decimal
    spend_cny: Decimal
    ad_gmv_cny: Decimal
    current_net_revenue_cny: Decimal
    current_net_profit_cny: Decimal


@dataclass(frozen=True, slots=True)
class ProjectionOutput:
    status: ProjectionStatus
    refund_amount_rate: Decimal | None
    settled_full_loss_rate: Decimal | None
    full_loss_qty_rate: Decimal | None
    projected_future_refund_amount_cny: Decimal | None
    projected_terminal_refund_amount_cny: Decimal | None
    projected_future_full_loss_order_count: Decimal | None
    projected_future_full_loss_qty: Decimal | None
    projected_terminal_full_loss_qty: Decimal | None
    projected_full_loss_cost_cny: Decimal | None
    projected_unsettled_net_cny: Decimal | None
    projected_net_revenue_cny: Decimal | None
    projected_net_profit_cny: Decimal | None
    projected_roi_real: Decimal | None
    projected_roi_breakeven: Decimal | None
    projected_nc_prime_cny: Decimal | None
    projected_cogs_kept_cny: Decimal | None
    projected_ad_gmv_cny: Decimal | None
    projected_ad_system_actual_roi: Decimal | None
    projected_ad_system_max_ad_spend_cny: Decimal | None
    projected_ad_system_breakeven_roi: Decimal | None


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


def calculate_projection(inputs: ProjectionInput) -> ProjectionOutput:
    """Project future changes without counting confirmed outcomes twice.

    Settled orders provide two independent bases: refund amount severity and
    order-level full-loss probability. Refund severity applies to the whole
    unsettled cohort. Full-loss probability applies only to unsettled orders
    that have not reached delivery terminal status; confirmed outcomes inside
    that risk cohort are then subtracted to obtain only the future increment.
    Existing COGS and ad spend stay in current profit and are never deducted a
    second time.
    """

    has_reliable_sample = (
        inputs.projection_basis_order_count > 0
        and inputs.projection_basis_sales_cny > 0
    )
    refund_amount_rate: Decimal | None = None
    settled_full_loss_rate: Decimal | None = None
    if has_reliable_sample:
        refund_amount_rate = min(
            Decimal(1),
            max(
                Decimal(0),
                inputs.projection_basis_refund_amount_cny
                / inputs.projection_basis_sales_cny,
            ),
        )
        settled_full_loss_rate = min(
            Decimal(1),
            max(
                Decimal(0),
                Decimal(inputs.projection_basis_full_loss_order_count)
                / Decimal(inputs.projection_basis_order_count),
            ),
        )

    current_nc_prime = (
        inputs.current_net_revenue_cny - inputs.observed_full_loss_cost_cny
    )
    current_ad_max_spend = inputs.current_net_revenue_cny - inputs.cogs_total_cny

    if inputs.unsettled_order_count <= 0:
        projected_roi_real = (
            current_nc_prime / inputs.spend_cny if inputs.spend_cny != 0 else None
        )
        breakeven_denom = current_nc_prime - inputs.current_cogs_kept_cny
        projected_roi_breakeven = None
        if inputs.spend_cny != 0 and breakeven_denom > 0:
            projected_roi_breakeven = current_nc_prime / breakeven_denom
        projected_ad_system_actual_roi = (
            inputs.ad_gmv_cny / inputs.spend_cny
            if inputs.spend_cny != 0
            else None
        )
        projected_ad_system_breakeven_roi = None
        if current_ad_max_spend > 0 and inputs.ad_gmv_cny > 0:
            projected_ad_system_breakeven_roi = (
                inputs.ad_gmv_cny / current_ad_max_spend
            )
        return ProjectionOutput(
            status=ProjectionStatus.NO_UNSETTLED_ORDERS,
            refund_amount_rate=refund_amount_rate,
            settled_full_loss_rate=settled_full_loss_rate,
            full_loss_qty_rate=settled_full_loss_rate,
            projected_future_refund_amount_cny=Decimal(0),
            projected_terminal_refund_amount_cny=Decimal(0),
            projected_future_full_loss_order_count=Decimal(0),
            projected_future_full_loss_qty=Decimal(0),
            projected_terminal_full_loss_qty=inputs.observed_full_loss_qty,
            projected_full_loss_cost_cny=inputs.observed_full_loss_cost_cny,
            projected_unsettled_net_cny=Decimal(0),
            projected_net_revenue_cny=inputs.current_net_revenue_cny,
            projected_net_profit_cny=inputs.current_net_profit_cny,
            projected_roi_real=projected_roi_real,
            projected_roi_breakeven=projected_roi_breakeven,
            projected_nc_prime_cny=current_nc_prime,
            projected_cogs_kept_cny=inputs.current_cogs_kept_cny,
            projected_ad_gmv_cny=inputs.ad_gmv_cny,
            projected_ad_system_actual_roi=projected_ad_system_actual_roi,
            projected_ad_system_max_ad_spend_cny=current_ad_max_spend,
            projected_ad_system_breakeven_roi=(
                projected_ad_system_breakeven_roi
            ),
        )

    if not has_reliable_sample:
        return ProjectionOutput(
            status=ProjectionStatus.INSUFFICIENT_SAMPLE,
            refund_amount_rate=None,
            settled_full_loss_rate=None,
            full_loss_qty_rate=None,
            projected_future_refund_amount_cny=None,
            projected_terminal_refund_amount_cny=None,
            projected_future_full_loss_order_count=None,
            projected_future_full_loss_qty=None,
            projected_terminal_full_loss_qty=None,
            projected_full_loss_cost_cny=None,
            projected_unsettled_net_cny=None,
            projected_net_revenue_cny=None,
            projected_net_profit_cny=None,
            projected_roi_real=None,
            projected_roi_breakeven=None,
            projected_nc_prime_cny=None,
            projected_cogs_kept_cny=None,
            projected_ad_gmv_cny=None,
            projected_ad_system_actual_roi=None,
            projected_ad_system_max_ad_spend_cny=None,
            projected_ad_system_breakeven_roi=None,
        )

    assert refund_amount_rate is not None
    assert settled_full_loss_rate is not None
    expected_terminal_refund = inputs.unsettled_sales_after_fee_cny * (
        refund_amount_rate
    )
    projected_terminal_refund = min(
        inputs.unsettled_sales_after_fee_cny,
        max(expected_terminal_refund, inputs.confirmed_unsettled_refund_after_fee_cny),
    )
    projected_future_refund = max(
        Decimal(0),
        projected_terminal_refund - inputs.confirmed_unsettled_refund_after_fee_cny,
    )
    projected_unsettled_net = (
        inputs.unsettled_sales_after_fee_cny - projected_terminal_refund
    )

    expected_terminal_full_loss_orders = (
        Decimal(inputs.full_loss_exposure_unsettled_order_count)
        * settled_full_loss_rate
    )
    projected_future_full_loss_orders = min(
        Decimal(inputs.unresolved_full_loss_exposure_order_count),
        max(
            Decimal(0),
            expected_terminal_full_loss_orders
            - Decimal(inputs.confirmed_full_loss_exposure_order_count),
        ),
    )
    expected_terminal_full_loss_qty = Decimal(
        inputs.full_loss_exposure_unsettled_order_count
    ) * (
        inputs.projection_basis_full_loss_qty
        / Decimal(inputs.projection_basis_order_count)
    )
    projected_future_full_loss_qty = min(
        inputs.unresolved_full_loss_exposure_qty,
        max(
            Decimal(0),
            expected_terminal_full_loss_qty
            - inputs.confirmed_full_loss_exposure_qty,
        ),
    )
    projected_terminal_full_loss_qty = (
        inputs.observed_full_loss_qty + projected_future_full_loss_qty
    )
    average_unresolved_unit_cost = (
        inputs.unresolved_full_loss_exposure_cogs_cny
        / inputs.unresolved_full_loss_exposure_qty
        if inputs.unresolved_full_loss_exposure_qty > 0
        else Decimal(0)
    )
    projected_future_full_loss_cost = (
        projected_future_full_loss_qty * average_unresolved_unit_cost
    )
    projected_full_loss_cost = (
        inputs.observed_full_loss_cost_cny + projected_future_full_loss_cost
    )

    unsettled_net_delta = (
        projected_unsettled_net - inputs.current_unsettled_net_cny
    )
    projected_net_revenue = inputs.current_net_revenue_cny + unsettled_net_delta
    projected_net_profit = inputs.current_net_profit_cny + unsettled_net_delta
    projected_nc_prime = projected_net_revenue - projected_full_loss_cost
    projected_cogs_kept = max(
        Decimal(0),
        inputs.current_cogs_kept_cny - projected_future_full_loss_cost,
    )
    projected_roi_real = (
        projected_nc_prime / inputs.spend_cny if inputs.spend_cny != 0 else None
    )
    projected_roi_breakeven = None
    breakeven_denom = projected_nc_prime - projected_cogs_kept
    if inputs.spend_cny != 0 and breakeven_denom > 0:
        projected_roi_breakeven = projected_nc_prime / breakeven_denom

    projected_ad_gmv = inputs.ad_gmv_cny * (Decimal(1) - refund_amount_rate)
    projected_ad_system_actual_roi = (
        projected_ad_gmv / inputs.spend_cny if inputs.spend_cny != 0 else None
    )
    projected_ad_system_max_ad_spend = (
        projected_net_revenue - inputs.cogs_total_cny
    )
    projected_ad_system_breakeven_roi = None
    if projected_ad_system_max_ad_spend > 0 and projected_ad_gmv > 0:
        projected_ad_system_breakeven_roi = (
            projected_ad_gmv / projected_ad_system_max_ad_spend
        )

    return ProjectionOutput(
        status=ProjectionStatus.AVAILABLE,
        refund_amount_rate=refund_amount_rate,
        settled_full_loss_rate=settled_full_loss_rate,
        full_loss_qty_rate=settled_full_loss_rate,
        projected_future_refund_amount_cny=projected_future_refund,
        projected_terminal_refund_amount_cny=projected_terminal_refund,
        projected_future_full_loss_order_count=(
            projected_future_full_loss_orders
        ),
        projected_future_full_loss_qty=projected_future_full_loss_qty,
        projected_terminal_full_loss_qty=projected_terminal_full_loss_qty,
        projected_full_loss_cost_cny=projected_full_loss_cost,
        projected_unsettled_net_cny=projected_unsettled_net,
        projected_net_revenue_cny=projected_net_revenue,
        projected_net_profit_cny=projected_net_profit,
        projected_roi_real=projected_roi_real,
        projected_roi_breakeven=projected_roi_breakeven,
        projected_nc_prime_cny=projected_nc_prime,
        projected_cogs_kept_cny=projected_cogs_kept,
        projected_ad_gmv_cny=projected_ad_gmv,
        projected_ad_system_actual_roi=projected_ad_system_actual_roi,
        projected_ad_system_max_ad_spend_cny=(
            projected_ad_system_max_ad_spend
        ),
        projected_ad_system_breakeven_roi=(
            projected_ad_system_breakeven_roi
        ),
    )


def calculate(inputs: FormulaInput) -> FormulaOutput:
    """Calculate one SPU in CNY using the canonical v10 profitability rubric."""

    vnd_per_cny = inputs.usd_vnd / inputs.usd_cny
    unit_cost_cny = inputs.unit_cost_cny
    spend_cny = inputs.spend_usd * inputs.usd_cny
    ad_gmv_cny = inputs.ad_gmv_usd * inputs.usd_cny
    sales_cny = inputs.sales_vnd / vnd_per_cny
    settled_net_cny = inputs.settled_net_vnd / vnd_per_cny
    settled_sales_cny = inputs.settled_sales_vnd / vnd_per_cny
    unsettled_sales_cny = inputs.unsettled_sales_vnd / vnd_per_cny
    refund_only_cny = inputs.refund_only_vnd / vnd_per_cny
    refund_return_cny = inputs.refund_return_vnd / vnd_per_cny
    refund_net_cny = refund_only_cny + refund_return_cny
    effective_sales_cny = sales_cny - refund_net_cny
    refund_cancelled_cny = inputs.refund_cancelled_vnd / vnd_per_cny
    cancelled_sales_cny = inputs.cancelled_sales_vnd / vnd_per_cny

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
                (inputs.refund_only_vnd + inputs.refund_return_vnd) / inputs.sales_vnd,
            ),
        )

    unsettled_net_cny = (
        unsettled_sales_cny
        * (Decimal(1) - inputs.unsettled_fee_rate)
        * (Decimal(1) - refund_rate_spu)
    )
    net_revenue_cny = settled_net_cny + unsettled_net_cny
    cogs_sold_cny = Decimal(inputs.units_sold) * unit_cost_cny
    cogs_full_loss_cancelled_cny = (
        Decimal(inputs.full_loss_cancelled_qty) * unit_cost_cny
    )
    cogs_all_cny = cogs_sold_cny + cogs_full_loss_cancelled_cny
    platform_fee_cny = inputs.unsettled_fee_rate * unsettled_sales_cny
    return_loss_cny = Decimal(inputs.full_loss_qty) * unit_cost_cny
    net_profit_cny = net_revenue_cny - cogs_all_cny - spend_cny

    roi_real = None
    if spend_cny != 0:
        roi_real = (net_revenue_cny - return_loss_cny) / spend_cny

    cogs_kept_cny = max(
        Decimal(0),
        Decimal(inputs.units_sold - inputs.refund_only_qty - inputs.refund_return_qty)
        * unit_cost_cny,
    )
    breakeven_denom = (net_revenue_cny - return_loss_cny) - cogs_kept_cny
    roi_breakeven = None
    if spend_cny != 0 and breakeven_denom > 0:
        roi_breakeven = (net_revenue_cny - return_loss_cny) / breakeven_denom

    cpa_cny = spend_cny / Decimal(inputs.ad_orders) if inputs.ad_orders else None
    # ROI 无币种；直接使用同源广告 USD 可避免两次 Decimal 换算引入尾差。
    ad_system_actual_roi = (
        inputs.ad_gmv_usd / inputs.spend_usd if inputs.spend_usd != 0 else None
    )

    # 广告后台口径使用广告归因 GMV 作为分子。当前系统尚未结构化录入
    # 退货运费、提现费、汇兑损失、包装耗材等结算外成本，因此这里给出的
    # 是“已知成本下限”估算；调用层必须通过状态与 warning 明示该限制。
    ad_system_max_ad_spend_cny = net_revenue_cny - cogs_all_cny
    ad_system_remaining_ad_spend_capacity_cny = ad_system_max_ad_spend_cny - spend_cny
    ad_system_breakeven_roi = None
    if ad_system_max_ad_spend_cny > 0 and ad_gmv_cny > 0:
        ad_system_breakeven_roi = ad_gmv_cny / ad_system_max_ad_spend_cny

    # roi_l0 是历史字段；保留为广告后台实际 ROI 的兼容别名。
    roi_l0 = ad_system_actual_roi

    # 保留金额/件数旧口径的显式字段，仅用于解释，不能再冒充大盘三率。
    refund_amount_rate = refund_net_cny / sales_cny if sales_cny > 0 else None
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
        unit_cost_cny=unit_cost_cny,
        spend_cny=spend_cny,
        ad_gmv_cny=ad_gmv_cny,
        sales_cny=sales_cny,
        effective_sales_cny=effective_sales_cny,
        total_orders=order_metrics.total_orders,
        effective_order_count=order_metrics.effective_order_count,
        refund_order_count=order_metrics.refund_order_count,
        full_loss_order_count=order_metrics.full_loss_order_count,
        settled_net_cny=settled_net_cny,
        settled_sales_cny=settled_sales_cny,
        unsettled_sales_cny=unsettled_sales_cny,
        refund_only_cny=refund_only_cny,
        refund_return_cny=refund_return_cny,
        refund_net_cny=refund_net_cny,
        refund_cancelled_cny=refund_cancelled_cny,
        cancelled_sales_cny=cancelled_sales_cny,
        net_revenue_cny=net_revenue_cny,
        unsettled_net_cny=unsettled_net_cny,
        cogs_sold_cny=cogs_sold_cny,
        cogs_full_loss_cancelled_cny=cogs_full_loss_cancelled_cny,
        cogs_all_cny=cogs_all_cny,
        cogs_kept_cny=cogs_kept_cny,
        platform_fee_cny=platform_fee_cny,
        return_loss_cny=return_loss_cny,
        net_profit_cny=net_profit_cny,
        roi_real=roi_real,
        roi_breakeven=roi_breakeven,
        ad_system_actual_roi=ad_system_actual_roi,
        ad_system_breakeven_roi=ad_system_breakeven_roi,
        ad_system_max_ad_spend_cny=ad_system_max_ad_spend_cny,
        ad_system_remaining_ad_spend_capacity_cny=(
            ad_system_remaining_ad_spend_capacity_cny
        ),
        cpa_cny=cpa_cny,
        roi_l0=roi_l0,
        refund_rate=order_metrics.refund_rate,
        refund_amount_rate=refund_amount_rate,
        refund_rate_qty=refund_rate_qty,
        cancel_rate=order_metrics.cancel_rate,
        full_loss_rate=order_metrics.full_loss_rate,
        full_loss_qty_rate=full_loss_qty_rate,
        gmv_sales_cny=sales_cny + cancelled_sales_cny,
    )
