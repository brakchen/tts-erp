"""Read-side facts and amount-first aggregation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast


@dataclass(frozen=True, slots=True)
class DailyFact:
    shop_pk: int
    spu_pk: int
    day: date
    spend_usd: Decimal = Decimal(0)
    ad_gmv_usd: Decimal = Decimal(0)
    ad_orders: int = 0
    order_count: int = 0
    units_sold: int = 0
    sales_vnd: Decimal = Decimal(0)
    settled_net_vnd: Decimal = Decimal(0)
    settled_sales_vnd: Decimal = Decimal(0)
    unsettled_sales_vnd: Decimal = Decimal(0)
    confirmed_unsettled_refund_vnd: Decimal = Decimal(0)
    refund_only_vnd: Decimal = Decimal(0)
    refund_return_vnd: Decimal = Decimal(0)
    refund_cancelled_vnd: Decimal = Decimal(0)
    cancelled_sales_vnd: Decimal = Decimal(0)
    cancelled_orders: int = 0
    domestic_cancelled_orders: int = 0
    overseas_cancelled_orders: int = 0
    full_loss_cancelled_qty: int = 0
    full_loss_qty: int = 0
    refund_order_count: int = 0
    refund_only_qty: int = 0
    refund_return_qty: int = 0
    unit_cost_cny: Decimal = Decimal(0)
    usd_cny: Decimal = Decimal(1)
    usd_vnd: Decimal = Decimal(1)
    unsettled_fee_rate: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class WindowMetric:
    start: date
    end: date
    spend_cny: Decimal
    order_count: int
    ad_orders: int
    roi_real: Decimal | None
    net_profit_cny: Decimal | None
    has_facts: bool = True


def aggregate_facts(facts: Iterable[DailyFact], *, calculate=None) -> WindowMetric:
    """Aggregate monetary facts before calling canonical profitability.calculate."""
    if calculate is None:
        from tts_erp_v2.analytics.spu_profitability._formula_v10 import (
            calculate as canonical_calculate,
        )

        calculate = canonical_calculate
    rows = list(facts)
    if not rows:
        raise ValueError("cannot aggregate an empty fact window")
    from tts_erp_v2.analytics.spu_profitability._formula_v10 import FormulaInput

    sums: dict[str, Decimal | int] = {}
    integer_fields = {
        "ad_orders",
        "order_count",
        "units_sold",
        "cancelled_orders",
        "domestic_cancelled_orders",
        "overseas_cancelled_orders",
        "full_loss_cancelled_qty",
        "full_loss_qty",
        "refund_order_count",
        "refund_only_qty",
        "refund_return_qty",
    }
    for name in DailyFact.__dataclass_fields__:
        if name in {
            "shop_pk",
            "spu_pk",
            "day",
            "unit_cost_cny",
            "usd_cny",
            "usd_vnd",
            "unsettled_fee_rate",
        }:
            continue
        values = [getattr(row, name) for row in rows]
        sums[name] = sum(values, 0 if name in integer_fields else Decimal(0))
    first = rows[0]

    def money(name: str) -> Decimal:
        return cast(Decimal, sums[name])

    inputs = FormulaInput(
        spend_usd=money("spend_usd"),
        ad_gmv_usd=money("ad_gmv_usd"),
        ad_orders=int(sums["ad_orders"]),
        order_count=int(sums["order_count"]),
        cancelled_orders=int(sums["cancelled_orders"]),
        domestic_cancelled_orders=int(sums["domestic_cancelled_orders"]),
        overseas_cancelled_orders=int(sums["overseas_cancelled_orders"]),
        units_sold=int(sums["units_sold"]),
        full_loss_cancelled_qty=int(sums["full_loss_cancelled_qty"]),
        full_loss_qty=int(sums["full_loss_qty"]),
        refund_order_count=int(sums["refund_order_count"]),
        refund_only_qty=int(sums["refund_only_qty"]),
        refund_return_qty=int(sums["refund_return_qty"]),
        sales_vnd=money("sales_vnd"),
        settled_net_vnd=money("settled_net_vnd"),
        settled_sales_vnd=money("settled_sales_vnd"),
        unsettled_sales_vnd=money("unsettled_sales_vnd"),
        confirmed_unsettled_refund_vnd=money("confirmed_unsettled_refund_vnd"),
        refund_only_vnd=money("refund_only_vnd"),
        refund_return_vnd=money("refund_return_vnd"),
        refund_cancelled_vnd=money("refund_cancelled_vnd"),
        cancelled_sales_vnd=money("cancelled_sales_vnd"),
        unit_cost_cny=first.unit_cost_cny,
        usd_cny=first.usd_cny,
        usd_vnd=first.usd_vnd,
        unsettled_fee_rate=first.unsettled_fee_rate,
    )
    output = calculate(inputs)
    return WindowMetric(
        min(row.day for row in rows),
        max(row.day for row in rows),
        output.spend_cny,
        int(sums["order_count"]),
        int(sums["ad_orders"]),
        output.roi_real,
        output.net_profit_cny,
    )
