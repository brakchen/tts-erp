"""Unit tests for the pure v10 SPU profitability formula seam."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tts_erp_v2.analytics.spu_profitability._formula_v10 import (
    FormulaInput,
    calculate,
    calculate_order_metrics,
)

pytestmark = [pytest.mark.domain_reporting, pytest.mark.layer_unit]

USD_CNY = Decimal(1) / Decimal("0.14774")


def cny_from_usd(value: str) -> Decimal:
    return Decimal(value) * USD_CNY


def cny_from_vnd(value: str) -> Decimal:
    return Decimal(value) / (Decimal(26330) / USD_CNY)


def _inputs(**overrides) -> FormulaInput:
    values = {
        "spend_usd": Decimal("10"),
        "ad_gmv_usd": Decimal("80"),
        "ad_orders": 5,
        "order_count": 1,
        "cancelled_orders": 1,
        "domestic_cancelled_orders": 1,
        "overseas_cancelled_orders": 0,
        "units_sold": 5,
        "full_loss_cancelled_qty": 0,
        "full_loss_qty": 1,
        "refund_order_count": 1,
        "refund_only_qty": 0,
        "refund_return_qty": 1,
        "sales_vnd": Decimal("2633000"),
        "settled_net_vnd": Decimal(0),
        "settled_sales_vnd": Decimal(0),
        "unsettled_sales_vnd": Decimal("2633000"),
        "refund_only_vnd": Decimal(0),
        "refund_return_vnd": Decimal("526600"),
        "refund_cancelled_vnd": Decimal(0),
        "cancelled_sales_vnd": Decimal("1053200"),
        "unit_cost_cny": Decimal(40),
        "usd_cny": USD_CNY,
        "usd_vnd": Decimal(26330),
        "unsettled_fee_rate": Decimal("0.308"),
    }
    values.update(overrides)
    return FormulaInput(**values)


def test_v10_order_metrics_use_one_shared_order_dimension_formula() -> None:
    metrics = calculate_order_metrics(
        order_count=8,
        cancelled_orders=2,
        domestic_cancelled_orders=1,
        overseas_cancelled_orders=1,
        refund_order_count=2,
    )

    assert metrics.total_orders == 10
    assert metrics.effective_order_count == 6
    assert metrics.refund_order_count == 2
    assert metrics.full_loss_order_count == 3
    assert metrics.refund_rate == Decimal("0.2")
    assert metrics.full_loss_rate == Decimal("0.3")
    assert metrics.cancel_rate == Decimal("0.1")


def test_v10_formula_keeps_exact_domain_decimals() -> None:
    result = calculate(_inputs())

    spend_cny = cny_from_usd("10")
    ad_gmv_cny = cny_from_usd("80")
    sales_cny = cny_from_vnd("2633000")
    refund_cny = cny_from_vnd("526600")
    net_revenue_cny = sales_cny * Decimal("0.692") * Decimal("0.8")
    max_ad_spend_cny = net_revenue_cny - Decimal(200)

    assert result.spend_cny == spend_cny
    assert result.ad_gmv_cny == ad_gmv_cny
    assert result.unit_cost_cny == Decimal(40)
    assert result.net_revenue_cny == net_revenue_cny
    assert result.unsettled_net_cny == net_revenue_cny
    assert result.cogs_sold_cny == Decimal(200)
    assert result.cogs_full_loss_cancelled_cny == 0
    assert result.cogs_all_cny == Decimal(200)
    assert result.return_loss_cny == Decimal(40)
    assert result.net_profit_cny == max_ad_spend_cny - spend_cny
    assert result.roi_real == (net_revenue_cny - Decimal(40)) / spend_cny
    assert result.ad_system_actual_roi == Decimal(8)
    assert result.ad_system_max_ad_spend_cny == max_ad_spend_cny
    assert result.ad_system_remaining_ad_spend_capacity_cny == (
        max_ad_spend_cny - spend_cny
    )
    assert result.ad_system_breakeven_roi == ad_gmv_cny / max_ad_spend_cny
    assert result.platform_fee_cny == sales_cny * Decimal("0.308")
    assert result.effective_sales_cny == sales_cny - refund_cny
    assert result.total_orders == 2
    assert result.effective_order_count == 0
    assert result.refund_order_count == 1
    assert result.full_loss_order_count == 1
    assert result.refund_rate == Decimal("0.5")
    assert result.full_loss_rate == Decimal("0.5")
    assert result.cancel_rate == Decimal("0.5")
    assert result.refund_amount_rate == Decimal("0.2")
    assert result.full_loss_qty_rate == Decimal("0.2")


def test_v10_formula_uses_settlement_as_actual_net_revenue() -> None:
    result = calculate(
        _inputs(
            settled_net_vnd=Decimal("1316500"),
            settled_sales_vnd=Decimal("2633000"),
            unsettled_sales_vnd=Decimal(0),
            refund_return_vnd=Decimal(0),
            refund_order_count=0,
            refund_return_qty=0,
        )
    )

    assert result.settled_net_cny == cny_from_vnd("1316500")
    assert result.net_revenue_cny == cny_from_vnd("1316500")
    assert result.platform_fee_cny == 0


def test_v10_formula_refund_only_reduces_kept_cogs_for_breakeven() -> None:
    result = calculate(
        _inputs(
            units_sold=10,
            refund_only_qty=1,
            refund_return_qty=0,
            sales_vnd=Decimal("2633000"),
            unsettled_sales_vnd=Decimal("2633000"),
            refund_only_vnd=Decimal("263300"),
            refund_return_vnd=Decimal(0),
            full_loss_qty=1,
            domestic_cancelled_orders=0,
            cancelled_sales_vnd=Decimal(0),
        )
    )

    assert result.cogs_kept_cny == Decimal(9) * Decimal(40)
    assert result.roi_breakeven is not None
    assert result.roi_real is not None
    assert result.net_profit_cny < 0
    assert result.roi_real < result.roi_breakeven


def test_v10_formula_uses_exact_snapshot_usd_cny_rate() -> None:
    result = calculate(
        _inputs(
            spend_usd=Decimal(1_000_000),
            ad_gmv_usd=Decimal(2_000_000),
            usd_cny=Decimal("6.9"),
            usd_vnd=Decimal(26000),
        )
    )

    assert result.spend_cny == Decimal(6_900_000)
    assert result.ad_gmv_cny == Decimal(13_800_000)
    assert result.ad_system_actual_roi == Decimal(2)


def test_v10_formula_marks_undefined_roi_without_ad_spend() -> None:
    result = calculate(_inputs(spend_usd=Decimal(0), ad_orders=0))

    assert result.roi_real is None
    assert result.roi_breakeven is None
    assert result.cpa_cny is None
    assert result.roi_l0 is None
    assert result.ad_system_actual_roi is None
    expected_net_revenue = cny_from_vnd("2633000") * Decimal("0.692") * Decimal("0.8")
    assert result.ad_system_breakeven_roi == (
        cny_from_usd("80") / (expected_net_revenue - Decimal(200))
    )


def test_v10_ad_system_breakeven_is_undefined_without_positive_capacity() -> None:
    result = calculate(
        _inputs(
            units_sold=20,
            ad_gmv_usd=Decimal("80"),
        )
    )

    assert result.ad_system_max_ad_spend_cny < 0
    assert result.ad_system_remaining_ad_spend_capacity_cny < 0
    assert result.ad_system_breakeven_roi is None
