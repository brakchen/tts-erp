"""Unit tests for the pure v10 SPU profitability formula seam."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tts_erp_v2.analytics.spu_profitability._formula_v10 import (
    FormulaInput,
    ProjectionInput,
    calculate,
    calculate_order_metrics,
    calculate_projection,
)
from tts_erp_v2.analytics.spu_profitability._types import ProjectionStatus

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


def _projection_inputs(**overrides) -> ProjectionInput:
    values = {
        "projection_basis_order_count": 10,
        "projection_basis_qty": Decimal(100),
        "projection_basis_sales_cny": Decimal(1000),
        "projection_basis_refund_amount_cny": Decimal(100),
        "projection_full_loss_basis_order_count": 10,
        "projection_basis_full_loss_order_count": 1,
        "projection_basis_full_loss_qty": Decimal(2),
        "unsettled_order_count": 5,
        "confirmed_unsettled_full_loss_order_count": 0,
        "confirmed_unsettled_full_loss_qty": Decimal(0),
        "unresolved_unsettled_order_count": 5,
        "full_loss_exposure_unsettled_order_count": 5,
        "confirmed_full_loss_exposure_order_count": 0,
        "confirmed_full_loss_exposure_qty": Decimal(0),
        "unresolved_full_loss_exposure_order_count": 5,
        "unresolved_full_loss_exposure_qty": Decimal(20),
        "unresolved_full_loss_exposure_cogs_cny": Decimal(200),
        "unsettled_sales_after_fee_cny": Decimal(400),
        "confirmed_unsettled_refund_after_fee_cny": Decimal(16),
        "unresolved_unsettled_qty": Decimal(20),
        "unresolved_unsettled_sales_after_fee_cny": Decimal(320),
        "unresolved_unsettled_cogs_cny": Decimal(200),
        "settled_net_cny": Decimal(600),
        "observed_full_loss_qty": Decimal(3),
        "observed_full_loss_cost_cny": Decimal(30),
        "current_unsettled_net_cny": Decimal(300),
        "current_cogs_kept_cny": Decimal(800),
        "cogs_total_cny": Decimal(1000),
        "spend_cny": Decimal(100),
        "ad_gmv_cny": Decimal(800),
        "current_net_revenue_cny": Decimal(900),
        "current_net_profit_cny": Decimal(-200),
    }
    values.update(overrides)
    return ProjectionInput(**values)


def test_projection_uses_delivered_full_loss_rate_and_current_profit_delta() -> None:
    result = calculate_projection(_projection_inputs())

    assert result.status is ProjectionStatus.AVAILABLE
    assert result.refund_amount_rate == Decimal("0.10")
    assert result.delivered_full_loss_rate == Decimal("0.10")
    assert result.settled_full_loss_rate == Decimal("0.10")
    assert result.projected_future_full_loss_order_count == Decimal("0.5")
    assert result.projected_future_full_loss_qty == Decimal(1)
    assert result.projected_terminal_full_loss_qty == Decimal(4)
    assert result.projected_full_loss_cost_cny == Decimal(40)
    assert result.projected_terminal_refund_amount_cny == Decimal(40)
    assert result.projected_future_refund_amount_cny == Decimal(24)
    assert result.projected_unsettled_net_cny == Decimal(360)
    assert result.projected_net_revenue_cny == Decimal(960)
    # Current profit already contains 300 of unsettled net. Only the +60 delta
    # is added; COGS and ad spend are not deducted a second time.
    assert result.projected_net_profit_cny == Decimal(-140)
    assert result.projected_nc_prime_cny == Decimal(920)
    assert result.projected_cogs_kept_cny == Decimal(790)
    assert result.projected_roi_real == Decimal("9.2")
    assert result.projected_roi_breakeven == Decimal(920) / Decimal(130)
    assert result.projected_ad_gmv_cny == Decimal(720)
    assert result.projected_ad_system_actual_roi == Decimal("7.2")
    assert result.projected_ad_system_max_ad_spend_cny == Decimal(-40)
    assert result.projected_ad_system_breakeven_roi is None


def test_projection_subtracts_confirmed_outcomes_from_whole_cohort_quota() -> None:
    result = calculate_projection(
        _projection_inputs(
            projection_basis_order_count=100,
            projection_full_loss_basis_order_count=100,
            projection_basis_full_loss_order_count=10,
            projection_basis_full_loss_qty=Decimal(10),
            unsettled_order_count=300,
            confirmed_unsettled_full_loss_order_count=8,
            confirmed_unsettled_full_loss_qty=Decimal(8),
            unresolved_unsettled_order_count=292,
            full_loss_exposure_unsettled_order_count=300,
            confirmed_full_loss_exposure_order_count=8,
            confirmed_full_loss_exposure_qty=Decimal(8),
            unresolved_full_loss_exposure_order_count=292,
            unresolved_full_loss_exposure_qty=Decimal(292),
            unresolved_full_loss_exposure_cogs_cny=Decimal(2920),
            unresolved_unsettled_qty=Decimal(292),
        )
    )

    assert result.settled_full_loss_rate == Decimal("0.10")
    assert result.projected_future_full_loss_order_count == Decimal(22)
    assert result.projected_future_full_loss_qty == Decimal(22)


def test_projection_excludes_delivery_terminal_orders_from_full_loss_exposure() -> None:
    result = calculate_projection(
        _projection_inputs(
            unsettled_order_count=20,
            full_loss_exposure_unsettled_order_count=5,
            confirmed_full_loss_exposure_order_count=1,
            confirmed_full_loss_exposure_qty=Decimal(1),
            unresolved_full_loss_exposure_order_count=4,
            unresolved_full_loss_exposure_qty=Decimal(16),
            unresolved_full_loss_exposure_cogs_cny=Decimal(160),
        )
    )

    # Refund projection still covers all 20 unsettled orders' sales, while the
    # full-loss quota uses only the five orders that have not reached delivery.
    assert result.projected_terminal_refund_amount_cny == Decimal(40)
    assert result.projected_future_full_loss_order_count == Decimal(0)
    assert result.projected_future_full_loss_qty == Decimal(0)


def test_projection_does_not_double_count_confirmed_unsettled_refund() -> None:
    result = calculate_projection(
        _projection_inputs(
            # The whole unsettled cohort expects 40 after-fee refund. Once 50
            # is already confirmed, no additional refund is forecast.
            unsettled_sales_after_fee_cny=Decimal(400),
            confirmed_unsettled_refund_after_fee_cny=Decimal(50),
        )
    )

    assert result.refund_amount_rate == Decimal("0.10")
    assert result.projected_terminal_refund_amount_cny == Decimal(50)
    assert result.projected_future_refund_amount_cny == Decimal(0)
    assert result.projected_unsettled_net_cny == Decimal(350)


def test_projection_calculates_projected_ad_system_breakeven_roi() -> None:
    result = calculate_projection(_projection_inputs(cogs_total_cny=Decimal(500)))

    assert result.projected_ad_gmv_cny == Decimal(720)
    assert result.projected_ad_system_max_ad_spend_cny == Decimal(460)
    assert result.projected_ad_system_actual_roi == Decimal("7.2")
    assert result.projected_ad_system_breakeven_roi == Decimal(720) / Decimal(460)


def test_projection_keeps_delivered_rate_without_a_settlement_sample() -> None:
    result = calculate_projection(
        _projection_inputs(
            projection_basis_order_count=0,
            projection_basis_qty=Decimal(0),
            projection_basis_sales_cny=Decimal(0),
            projection_basis_refund_amount_cny=Decimal(0),
            projection_basis_full_loss_order_count=0,
            projection_basis_full_loss_qty=Decimal(0),
        )
    )

    assert result.status is ProjectionStatus.INSUFFICIENT_SAMPLE
    assert result.refund_amount_rate is None
    assert result.delivered_full_loss_rate == Decimal(0)
    assert result.settled_full_loss_rate == Decimal(0)
    assert result.projected_future_full_loss_qty is None
    assert result.projected_unsettled_net_cny is None
    assert result.projected_net_profit_cny is None
    assert result.projected_roi_real is None
    assert result.projected_ad_system_actual_roi is None
    assert result.projected_ad_system_breakeven_roi is None


def test_projection_without_unsettled_orders_matches_current_actual_result() -> None:
    result = calculate_projection(
        _projection_inputs(
            unsettled_order_count=0,
            confirmed_unsettled_full_loss_order_count=0,
            confirmed_unsettled_full_loss_qty=Decimal(0),
            unresolved_unsettled_order_count=0,
            unsettled_sales_after_fee_cny=Decimal(0),
            confirmed_unsettled_refund_after_fee_cny=Decimal(0),
            unresolved_unsettled_qty=Decimal(0),
            unresolved_unsettled_sales_after_fee_cny=Decimal(0),
            unresolved_unsettled_cogs_cny=Decimal(0),
            settled_net_cny=Decimal(600),
            current_unsettled_net_cny=Decimal(0),
            current_net_revenue_cny=Decimal(600),
            current_net_profit_cny=Decimal(-500),
        )
    )

    assert result.status is ProjectionStatus.NO_UNSETTLED_ORDERS
    assert result.projected_future_full_loss_qty == 0
    assert result.projected_unsettled_net_cny == 0
    assert result.projected_net_revenue_cny == Decimal(600)
    assert result.projected_net_profit_cny == Decimal(-500)
    assert result.projected_roi_real == Decimal("5.7")
    assert result.projected_ad_system_actual_roi == Decimal(8)
    assert result.projected_ad_system_breakeven_roi is None


def test_projection_roi_is_undefined_without_ad_spend() -> None:
    result = calculate_projection(_projection_inputs(spend_cny=Decimal(0)))

    assert result.status is ProjectionStatus.AVAILABLE
    assert result.projected_roi_real is None
    assert result.projected_roi_breakeven is None
    assert result.projected_ad_system_actual_roi is None
