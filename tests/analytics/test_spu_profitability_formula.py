"""Unit tests for the pure v10 SPU profitability formula seam."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tts_erp_v2.analytics.spu_profitability._formula_v10 import (
    FormulaInput,
    calculate,
)

pytestmark = [pytest.mark.domain_reporting, pytest.mark.layer_unit]


def _inputs(**overrides) -> FormulaInput:
    values = {
        "spend_usd": Decimal("10"),
        "ad_gmv_usd": Decimal("80"),
        "ad_orders": 5,
        "order_count": 1,
        "units_sold": 5,
        "domestic_cancelled_orders": 1,
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
        "cny_usd": Decimal("0.14774"),
        "usd_vnd": Decimal(26330),
        "unsettled_fee_rate": Decimal("0.308"),
    }
    values.update(overrides)
    return FormulaInput(**values)


def test_v10_formula_keeps_exact_domain_decimals() -> None:
    result = calculate(_inputs())

    assert result.net_revenue_usd == Decimal("55.3600")
    assert result.unsettled_net_usd == Decimal("55.3600")
    assert result.cogs_sold_usd == Decimal("29.54800")
    assert result.cogs_full_loss_cancelled_usd == 0
    assert result.cogs_all_usd == Decimal("29.54800")
    assert result.return_loss_usd == Decimal("5.90960")
    assert result.net_profit_usd == Decimal("15.81200")
    assert result.roi_real == Decimal("4.94504")
    assert result.platform_fee_usd == Decimal("30.800")
    assert result.refund_rate == Decimal("0.2")
    assert result.full_loss_rate == Decimal("0.2")
    assert result.cancel_rate == Decimal("0.5")


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

    assert result.settled_net_usd == Decimal("50")
    assert result.net_revenue_usd == Decimal("50.000")
    assert result.platform_fee_usd == 0


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

    assert result.cogs_kept_usd == Decimal(9) * Decimal(40) * Decimal("0.14774")
    assert result.roi_breakeven is not None
    assert result.roi_real is not None
    assert result.net_profit_usd < 0
    assert result.roi_real < result.roi_breakeven


def test_v10_formula_marks_undefined_roi_without_ad_spend() -> None:
    result = calculate(_inputs(spend_usd=Decimal(0), ad_orders=0))

    assert result.roi_real is None
    assert result.roi_breakeven is None
    assert result.cpa_usd is None
    assert result.roi_l0 is None
