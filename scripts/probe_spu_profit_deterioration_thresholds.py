#!/usr/bin/env python3
"""Read-only backtest for SPU profit-deterioration thresholds.

The database boundary returns daily shop x SPU facts only.  All comparison,
state, gate, persistence, and matrix calculations below are pure Python.  The
query is deliberately bounded by dates and a deterministic top-SPU cap and
opens a READ ONLY transaction with statement and lock timeouts.

This probe must not be used with a test database: it is intended for an
operator-approved production read and emits aggregates only (no shop/SPU
identifiers and no row-level facts).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from tts_erp_v2.analytics.spu_profitability._formula_v10 import FormulaInput, calculate
from tts_erp_v2.analytics.spu_profitability._snapshot import consistent_read_snapshot
from tts_erp_v2.api.deps import is_prod_shaped_db
from tts_erp_v2.db.constants import (
    PAID_SALES_ORDER_STATUSES,
    SHOP_FEE_RATE_CALCULATION_VERSION,
)


_COMPLETED_CASE_STATUSES = ("CANCELLATION_REQUEST_COMPLETE", "RETURN_OR_REFUND_REQUEST_COMPLETE")
_AD_ENDPOINT = "/oec_ads/shopping/v1/oec/stat/post_product_list"
_DEFAULT_USD_CNY = Decimal("7")
_DEFAULT_USD_VND = Decimal("25000")
_DEFAULT_FEE = Decimal("0.308")
_DEFAULT_COST = Decimal("40")


@dataclass(frozen=True, slots=True)
class DailyFact:
    shop_pk: int
    spu_pk: int
    day: date
    spend_usd: Decimal = Decimal(0)
    ad_orders: int = 0
    order_count: int = 0
    cancelled_orders: int = 0
    domestic_cancelled_orders: int = 0
    overseas_cancelled_orders: int = 0
    units_sold: Decimal = Decimal(0)
    full_loss_cancelled_qty: Decimal = Decimal(0)
    full_loss_qty: Decimal = Decimal(0)
    refund_order_count: int = 0
    refund_only_qty: Decimal = Decimal(0)
    refund_return_qty: Decimal = Decimal(0)
    sales_vnd: Decimal = Decimal(0)
    settled_net_vnd: Decimal = Decimal(0)
    settled_sales_vnd: Decimal = Decimal(0)
    unsettled_sales_vnd: Decimal = Decimal(0)
    refund_only_vnd: Decimal = Decimal(0)
    refund_return_vnd: Decimal = Decimal(0)
    refund_cancelled_vnd: Decimal = Decimal(0)
    cancelled_sales_vnd: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class WindowMetric:
    shop_pk: int
    spu_pk: int
    start: date
    end: date
    roi_real: Decimal | None
    net_profit: Decimal
    spend_cny: Decimal
    total_orders: int
    ad_orders: int
    fact_days: int = 1


@dataclass(frozen=True, slots=True)
class AlertConfig:
    roi_abs_delta: Decimal
    roi_relative_decline: Decimal
    net_profit_decline: Decimal
    min_spend_cny: Decimal
    min_orders: int
    min_ad_orders: int


@dataclass(frozen=True, slots=True)
class AlertDecision:
    state: str
    sample_status: str
    roi_decline: Decimal | None
    net_profit_decline: Decimal | None
    sample_sufficient: bool
    alert: bool
    severity: str


# This is the approved comparison calendar expressed relative to anchor A=T-2.
# Both intervals are inclusive and equal length; no rolling overlap is allowed.
def comparison_windows(anchor: date, days: int) -> tuple[tuple[date, date], tuple[date, date]]:
    if days == 1:
        return (anchor, anchor), (anchor - timedelta(days=1), anchor - timedelta(days=1))
    if days == 3:
        return (anchor - timedelta(days=2), anchor), (anchor - timedelta(days=5), anchor - timedelta(days=3))
    if days == 7:
        return (anchor - timedelta(days=6), anchor), (anchor - timedelta(days=13), anchor - timedelta(days=7))
    raise ValueError("days must be one of 1, 3, or 7")


def confirmation_windows(anchor: date, days: int) -> tuple[tuple[date, date], tuple[date, date]]:
    shifted = anchor - timedelta(days=7)
    return comparison_windows(shifted, days)


def classify_state(previous_roi: Decimal | None, current_roi: Decimal | None, previous_profit: Decimal, current_profit: Decimal) -> str:
    if previous_roi is not None and current_roi is not None:
        if previous_roi > 0 and current_roi <= 0:
            return "profit_to_loss"
        if previous_roi <= 0 and current_roi < previous_roi:
            return "loss_expanding"
        if previous_roi <= 0 and current_roi > 0:
            return "loss_to_profit"
        if current_roi < previous_roi:
            return "roi_deterioration"
        if current_roi > previous_roi:
            return "roi_recovery"
    if previous_profit > 0 and current_profit <= 0:
        return "profit_to_loss"
    if previous_profit <= 0 and current_profit < previous_profit:
        return "loss_expanding"
    if current_profit < previous_profit:
        return "net_profit_deterioration"
    if current_profit > previous_profit:
        return "recovery"
    return "stable"


def evaluate_alert(
    previous: WindowMetric,
    current: WindowMetric,
    warning: AlertConfig,
    critical: AlertConfig,
) -> AlertDecision:
    """Apply one normative policy; critical is never inferred from warning.

    Evaluability is decided before state classification.  Missing window facts
    are unavailable; present facts with a null ROI or failed sample gates are
    sample_insufficient.  Neither can become stable merely because thresholds
    or gates are zero.
    """
    def gate(config: AlertConfig) -> bool:
        return (
            min(previous.spend_cny, current.spend_cny) >= config.min_spend_cny
            and min(previous.total_orders, current.total_orders) >= config.min_orders
            and min(previous.ad_orders, current.ad_orders) >= config.min_ad_orders
        )

    if previous.fact_days == 0 or current.fact_days == 0:
        return AlertDecision("unavailable", "unavailable", None, None, False, False, "none")
    if previous.roi_real is None or current.roi_real is None:
        return AlertDecision("sample_insufficient", "sample_insufficient", None, None, False, False, "none")
    if not gate(warning):
        return AlertDecision("sample_insufficient", "sample_insufficient", None, None, False, False, "none")

    state = classify_state(previous.roi_real, current.roi_real, previous.net_profit, current.net_profit)
    roi_decline: Decimal | None = None
    if previous.roi_real > 0:
        roi_decline = (previous.roi_real - current.roi_real) / previous.roi_real
    net_decline: Decimal | None = None
    if previous.net_profit > 0:
        net_decline = (previous.net_profit - current.net_profit) / previous.net_profit

    def numeric_roi_trigger(config: AlertConfig) -> bool:
        absolute = previous.roi_real is not None and current.roi_real is not None and current.roi_real <= previous.roi_real - config.roi_abs_delta
        relative = roi_decline is not None and roi_decline >= config.roi_relative_decline
        return absolute or relative

    transition = state in {"profit_to_loss", "loss_expanding"}
    warning_trigger = transition or numeric_roi_trigger(warning)
    warning_impact = transition or (net_decline is not None and net_decline >= warning.net_profit_decline)
    critical_trigger = numeric_roi_trigger(critical)
    critical_impact = net_decline is not None and net_decline >= critical.net_profit_decline
    critical_alert = gate(critical) and critical_trigger and critical_impact
    warning_alert = gate(warning) and warning_trigger and warning_impact
    severity = "critical" if critical_alert else ("warning" if warning_alert else "none")
    return AlertDecision(state, "sufficient", roi_decline, net_decline, True, warning_alert or critical_alert, severity)


_DAILY_SQL = text(
    """
WITH bounded_keys AS MATERIALIZED (
    SELECT so.shop_pk, sl.spu_pk,
           count(DISTINCT so.id) AS activity_orders
    FROM commerce.sales_order_lines sl
    JOIN commerce.sales_orders so ON so.id = sl.order_pk
    WHERE sl.spu_pk IS NOT NULL
      AND (so.status = ANY(CAST(:paid_statuses AS text[])) OR so.status = 'CANCELLED')
      AND (coalesce(so.order_time, so.paid_at) AT TIME ZONE 'UTC')::date BETWEEN :start_day AND :end_day
    GROUP BY so.shop_pk, sl.spu_pk
    ORDER BY activity_orders DESC, so.shop_pk ASC, sl.spu_pk ASC
    LIMIT :max_spus
), scoped_order_lines AS MATERIALIZED (
    SELECT so.shop_pk, so.id AS order_pk, sl.id AS line_id, sl.spu_pk,
           so.status, coalesce(so.order_time, so.paid_at) AS order_at,
           coalesce(sl.quantity, 0) AS qty, coalesce(sl.unit_price, 0) AS unit_price
    FROM commerce.sales_order_lines sl
    JOIN commerce.sales_orders so ON so.id = sl.order_pk
    JOIN bounded_keys bk ON bk.shop_pk = so.shop_pk AND bk.spu_pk = sl.spu_pk
    WHERE sl.spu_pk IS NOT NULL
      AND (so.status = ANY(CAST(:paid_statuses AS text[])) OR so.status = 'CANCELLED')
      AND (coalesce(so.order_time, so.paid_at) AT TIME ZONE 'UTC')::date BETWEEN :start_day AND :end_day
), selected_orders AS (
    SELECT DISTINCT order_pk
    FROM scoped_order_lines
), order_gmv AS (
    SELECT sl.order_pk, sum(coalesce(sl.quantity, 0) * coalesce(sl.unit_price, 0)) AS gmv_vnd
    FROM commerce.sales_order_lines sl JOIN selected_orders x ON x.order_pk = sl.order_pk
    GROUP BY sl.order_pk
), settlements AS (
    SELECT st.order_pk, sum(sc.amount) FILTER (WHERE sc.component_code = 'SETTLEMENT') AS settlement_vnd,
           count(*) FILTER (WHERE sc.component_code = 'SETTLEMENT') > 0 AS is_settled
    FROM finance.settlement_transactions st JOIN selected_orders x ON x.order_pk = st.order_pk
    LEFT JOIN finance.settlement_components sc ON sc.transaction_id = st.id
    GROUP BY st.order_pk
), refund_lines AS (
    SELECT cl.sales_order_line_id,
           sum(cl.quantity) FILTER (WHERE c.case_type = 'REFUND_ONLY') AS refund_only_qty,
           sum(cl.refund_amount) FILTER (WHERE c.case_type = 'REFUND_ONLY') AS refund_only_vnd,
           sum(cl.quantity) FILTER (WHERE c.case_type = 'RETURN_AND_REFUND') AS refund_return_qty,
           sum(cl.refund_amount) FILTER (WHERE c.case_type = 'RETURN_AND_REFUND') AS refund_return_vnd,
           sum(cl.quantity) FILTER (WHERE c.case_type IN ('CANCELLATION','CANCEL')) AS refund_cancelled_qty,
           sum(cl.refund_amount) FILTER (WHERE c.case_type IN ('CANCELLATION','CANCEL')) AS refund_cancelled_vnd
    FROM after_sales.cases c JOIN after_sales.case_lines cl ON cl.case_id = c.id
    JOIN scoped_order_lines scope ON scope.line_id = cl.sales_order_line_id
    WHERE c.status IN (:case_status_0, :case_status_1)
    GROUP BY cl.sales_order_line_id
), full_loss_lines AS (
    SELECT scope.line_id, sum(cl.quantity) AS full_loss_qty, 0::numeric AS cancelled_qty
    FROM after_sales.cases c JOIN after_sales.case_lines cl ON cl.case_id = c.id
    JOIN scoped_order_lines scope ON scope.line_id = cl.sales_order_line_id
    WHERE c.case_type IN ('REFUND_ONLY','RETURN_AND_REFUND')
      AND c.status = :case_status_1 AND scope.status = ANY(CAST(:paid_statuses AS text[]))
    GROUP BY scope.line_id
    UNION ALL
    SELECT scope.line_id, sum(scope.qty), sum(scope.qty)
    FROM scoped_order_lines scope
    WHERE scope.status = 'CANCELLED'
      AND EXISTS (SELECT 1 FROM fulfillment.shipments sh JOIN fulfillment.tracking_events te ON te.shipment_id = sh.id
                  WHERE sh.order_pk = scope.order_pk AND te.action_code = 38301)
    GROUP BY scope.line_id
), lines AS (
    SELECT scope.shop_pk, scope.spu_pk,
           (scope.order_at AT TIME ZONE 'UTC')::date AS day,
           scope.order_pk, scope.status, scope.qty,
           scope.qty * scope.unit_price AS line_gmv_vnd,
           og.gmv_vnd, se.settlement_vnd, se.is_settled,
           coalesce(rl.refund_only_qty,0) AS refund_only_qty,
           coalesce(rl.refund_only_vnd,0) AS refund_only_vnd,
           coalesce(rl.refund_return_qty,0) AS refund_return_qty,
           coalesce(rl.refund_return_vnd,0) AS refund_return_vnd,
           coalesce(rl.refund_cancelled_vnd,0) AS refund_cancelled_vnd,
           coalesce(fl.full_loss_qty,0) AS full_loss_qty,
           coalesce(fl.cancelled_qty,0) AS full_loss_cancelled_qty
    FROM scoped_order_lines scope
    JOIN order_gmv og ON og.order_pk = scope.order_pk
    LEFT JOIN settlements se ON se.order_pk = scope.order_pk
    LEFT JOIN refund_lines rl ON rl.sales_order_line_id = scope.line_id
    LEFT JOIN full_loss_lines fl ON fl.line_id = scope.line_id
), grouped AS (
    SELECT shop_pk, spu_pk, day,
           count(DISTINCT order_pk) FILTER (WHERE status = ANY(CAST(:paid_statuses AS text[])))::int AS order_count,
           count(DISTINCT order_pk) FILTER (WHERE status = 'CANCELLED')::int AS cancelled_orders,
           count(DISTINCT order_pk) FILTER (WHERE status = 'CANCELLED' AND full_loss_cancelled_qty = 0)::int AS domestic_cancelled_orders,
           count(DISTINCT order_pk) FILTER (WHERE status = 'CANCELLED' AND full_loss_cancelled_qty > 0)::int AS overseas_cancelled_orders,
           sum(qty) FILTER (WHERE status = ANY(CAST(:paid_statuses AS text[]))) AS units_sold,
           sum(line_gmv_vnd) FILTER (WHERE status = ANY(CAST(:paid_statuses AS text[]))) AS sales_vnd,
           sum(settlement_vnd * line_gmv_vnd / NULLIF(gmv_vnd,0)) FILTER (WHERE is_settled) AS settled_net_vnd,
           sum(line_gmv_vnd) FILTER (WHERE is_settled) AS settled_sales_vnd,
           sum(line_gmv_vnd) FILTER (WHERE NOT coalesce(is_settled,false) AND status = ANY(CAST(:paid_statuses AS text[]))) AS unsettled_sales_vnd,
           sum(refund_only_qty) FILTER (WHERE status = ANY(CAST(:paid_statuses AS text[]))) AS refund_only_qty,
           sum(refund_return_qty) FILTER (WHERE status = ANY(CAST(:paid_statuses AS text[]))) AS refund_return_qty,
           sum(refund_only_vnd) FILTER (WHERE status = ANY(CAST(:paid_statuses AS text[]))) AS refund_only_vnd,
           sum(refund_return_vnd) FILTER (WHERE status = ANY(CAST(:paid_statuses AS text[]))) AS refund_return_vnd,
           sum(refund_cancelled_vnd) FILTER (WHERE status = 'CANCELLED') AS refund_cancelled_vnd,
           sum(line_gmv_vnd) FILTER (WHERE status = 'CANCELLED') AS cancelled_sales_vnd,
           count(DISTINCT order_pk) FILTER (WHERE status = ANY(CAST(:paid_statuses AS text[])) AND (refund_only_qty > 0 OR refund_return_qty > 0))::int AS refund_order_count,
           sum(full_loss_qty) FILTER (WHERE status = ANY(CAST(:paid_statuses AS text[]))) AS full_loss_qty,
           sum(full_loss_cancelled_qty) FILTER (WHERE status = 'CANCELLED') AS full_loss_cancelled_qty
    FROM lines GROUP BY shop_pk, spu_pk, day
)
SELECT * FROM grouped
ORDER BY shop_pk, spu_pk, day
"""
)

_AD_SQL = text(
    """
WITH bounded_keys AS MATERIALIZED (
    SELECT so.shop_pk, sl.spu_pk, count(DISTINCT so.id) AS activity_orders
    FROM commerce.sales_order_lines sl
    JOIN commerce.sales_orders so ON so.id = sl.order_pk
    WHERE sl.spu_pk IS NOT NULL
      AND (so.status = ANY(CAST(:paid_statuses AS text[])) OR so.status = 'CANCELLED')
      AND (coalesce(so.order_time, so.paid_at) AT TIME ZONE 'UTC')::date BETWEEN :start_day AND :end_day
    GROUP BY so.shop_pk, sl.spu_pk
    ORDER BY activity_orders DESC, so.shop_pk ASC, sl.spu_pk ASC
    LIMIT :max_spus
)
SELECT sh.id AS shop_pk, p.id AS spu_pk, d.day,
       coalesce(sum(d.mixed_real_cost),0) AS spend_usd,
       coalesce(sum(d.onsite_roi2_shopping_sku),0)::int AS ad_orders
FROM plugin.ad_daily d
JOIN commerce.shops sh ON sh.platform = 'tiktok' AND sh.shop_id = d.seller_id
JOIN commerce.products_spu p ON p.shop_pk = sh.id AND p.spu_id = d.product_id
JOIN bounded_keys bk ON bk.shop_pk = sh.id AND bk.spu_pk = p.id
WHERE d.endpoint = :endpoint AND d.day BETWEEN :start_day AND :end_day
GROUP BY sh.id, p.id, d.day
"""
)


def self_check_sql_scope() -> None:
    """Guard that expensive order/after-sales CTEs cannot escape probe scope."""
    sql = _DAILY_SQL.text
    bounded = sql.index("bounded_keys AS")
    scoped = sql.index("scoped_order_lines AS")
    refund = sql.index("refund_lines AS")
    full_loss = sql.index("full_loss_lines AS")
    required = (
        "WITH bounded_keys AS MATERIALIZED",
        "scoped_order_lines AS MATERIALIZED",
        "AND (so.status = ANY(CAST(:paid_statuses AS text[])) OR so.status = 'CANCELLED')",
        "JOIN bounded_keys bk ON bk.shop_pk = so.shop_pk AND bk.spu_pk = sl.spu_pk",
        "FROM scoped_order_lines",
        "JOIN scoped_order_lines scope ON scope.line_id = cl.sales_order_line_id",
        "SELECT scope.line_id, sum(scope.qty), sum(scope.qty)",
        "FROM scoped_order_lines scope\n    WHERE scope.status = 'CANCELLED'",
    )
    status_predicate = "AND (so.status = ANY(CAST(:paid_statuses AS text[])) OR so.status = 'CANCELLED')"
    status_in_bounded = status_predicate in sql[bounded:scoped]
    status_in_scoped = status_predicate in sql[scoped:refund]
    if not bounded < scoped < refund < full_loss or any(fragment not in sql for fragment in required) or not (status_in_bounded and status_in_scoped):
        raise AssertionError("daily SQL scope regression: bounded/scoped status predicates missing")


def self_check_policy() -> None:
    """Executable boundary checks for evaluability and zero-threshold safety."""
    warning = AlertConfig(Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0, 0)
    critical = warning
    missing = WindowMetric(1, 2, date(2026, 1, 1), date(2026, 1, 1), None, Decimal(0), Decimal(0), 0, 0, 0)
    zero_spend = WindowMetric(1, 2, date(2026, 1, 1), date(2026, 1, 1), None, Decimal(0), Decimal(0), 0, 0, 1)
    if evaluate_alert(missing, missing, warning, critical).sample_status != "unavailable":
        raise AssertionError("missing facts must be unavailable")
    if evaluate_alert(zero_spend, zero_spend, warning, critical).sample_status != "sample_insufficient":
        raise AssertionError("zero-spend null ROI must be sample_insufficient")


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _read_facts(session: Session, *, start_day: date, end_day: date, max_spus: int) -> list[DailyFact]:
    params = {
        "start_day": start_day,
        "end_day": end_day,
        "case_status_0": _COMPLETED_CASE_STATUSES[0],
        "case_status_1": _COMPLETED_CASE_STATUSES[1],
        "paid_statuses": list(PAID_SALES_ORDER_STATUSES),
        "max_spus": max_spus,
    }
    rows = session.execute(_DAILY_SQL, params).mappings().all()
    # bounded_keys applies the deterministic cap inside SQL before materializing
    # line facts; this Python set is only a join guard for the ad query.
    allowed = {(int(row["shop_pk"]), int(row["spu_pk"])) for row in rows}
    facts: dict[tuple[int, int, date], DailyFact] = {}
    for row in rows:
        key = (int(row["shop_pk"]), int(row["spu_pk"]))
        if key not in allowed:
            continue
        day = row["day"]
        facts[(key[0], key[1], day)] = DailyFact(
            shop_pk=key[0], spu_pk=key[1], day=day,
            order_count=int(row["order_count"] or 0), cancelled_orders=int(row["cancelled_orders"] or 0),
            domestic_cancelled_orders=int(row["domestic_cancelled_orders"] or 0), overseas_cancelled_orders=int(row["overseas_cancelled_orders"] or 0),
            units_sold=_decimal(row["units_sold"]), full_loss_cancelled_qty=_decimal(row["full_loss_cancelled_qty"]), full_loss_qty=_decimal(row["full_loss_qty"]),
            refund_order_count=int(row["refund_order_count"] or 0), refund_only_qty=_decimal(row["refund_only_qty"]), refund_return_qty=_decimal(row["refund_return_qty"]),
            sales_vnd=_decimal(row["sales_vnd"]), settled_net_vnd=_decimal(row["settled_net_vnd"]), settled_sales_vnd=_decimal(row["settled_sales_vnd"]), unsettled_sales_vnd=_decimal(row["unsettled_sales_vnd"]),
            refund_only_vnd=_decimal(row["refund_only_vnd"]), refund_return_vnd=_decimal(row["refund_return_vnd"]), refund_cancelled_vnd=_decimal(row["refund_cancelled_vnd"]), cancelled_sales_vnd=_decimal(row["cancelled_sales_vnd"]),
        )
    for row in session.execute(_AD_SQL, {"endpoint": _AD_ENDPOINT, "start_day": start_day, "end_day": end_day, "max_spus": max_spus, "paid_statuses": list(PAID_SALES_ORDER_STATUSES)}).mappings():
        key = (int(row["shop_pk"]), int(row["spu_pk"]))
        if key not in allowed:
            continue
        k = (*key, row["day"])
        old = facts.get(k, DailyFact(shop_pk=key[0], spu_pk=key[1], day=row["day"]))
        facts[k] = DailyFact(**{**asdict(old), "spend_usd": _decimal(row["spend_usd"]), "ad_orders": int(row["ad_orders"] or 0)})
    return list(facts.values())


def _costs_and_rates(
    session: Session,
    keys: set[tuple[int, int]],
    *,
    as_of: datetime,
) -> tuple[dict[int, Decimal], dict[int, Decimal], dict[str, int], Decimal, Decimal]:
    pks = [spu for _, spu in sorted(keys)]
    costs = {int(row["spu_pk"]): _decimal(row["unit_cost"]) for row in session.execute(text("SELECT spu_pk, unit_cost FROM procurement.manual_product_costs WHERE valid_to IS NULL AND spu_pk = ANY(:pks)"), {"pks": pks}).mappings()}
    fee_sources = {"shop_estimate": 0, "stale_fallback": 0, "baseline": 0}
    fee_shop_states: dict[int, str] = {}
    rates: dict[int, Decimal] = {}
    if session.execute(text("SELECT to_regclass('reporting.shop_fee_rate_estimates') IS NOT NULL")).scalar():
        fee_rows = session.execute(text("""
            SELECT DISTINCT ON (shop_pk) shop_pk, fee_rate, calculated_on, calculated_at
            FROM reporting.shop_fee_rate_estimates
            WHERE calculation_version = :calculation_version
            ORDER BY shop_pk, calculated_at DESC
        """), {"calculation_version": SHOP_FEE_RATE_CALCULATION_VERSION}).mappings().all()
        # Match analytics.spu_profitability._implementation exactly: freshness
        # is based on calculated_on versus calculated_at.date() - 7 days.
        fresh_before = as_of.date() - timedelta(days=7)
        for row in fee_rows:
            shop_pk = int(row["shop_pk"])
            calculated_on = row["calculated_on"]
            if calculated_on is not None and calculated_on >= fresh_before:
                rates[shop_pk] = _decimal(row["fee_rate"])
                fee_shop_states[shop_pk] = "shop_estimate"
            else:
                fee_shop_states[shop_pk] = "stale_fallback"
    fx = session.execute(text("""
        SELECT er.target_code, er.rate FROM fx.exchange_rates er
        JOIN fx.exchange_rate_snapshots es ON es.id = er.snapshot_id
        WHERE es.base_code = 'USD' AND es.id = (SELECT id FROM fx.exchange_rate_snapshots WHERE base_code='USD' ORDER BY upstream_last_update DESC, id DESC LIMIT 1)
          AND er.target_code IN ('CNY','VND')
    """)).mappings().all()
    fx_map = {row["target_code"]: _decimal(row["rate"]) for row in fx}
    for shop_pk, _ in keys:
        fee_sources[fee_shop_states.get(shop_pk, "baseline")] += 1
    return costs, rates, fee_sources, fx_map.get("CNY", _DEFAULT_USD_CNY), fx_map.get("VND", _DEFAULT_USD_VND)


def _aggregate(facts: list[DailyFact], *, key: tuple[int, int], start: date, end: date, costs: dict[int, Decimal], rates: dict[int, Decimal], usd_cny: Decimal, usd_vnd: Decimal) -> WindowMetric:
    rows = [row for row in facts if (row.shop_pk, row.spu_pk) == key and start <= row.day <= end]
    total = {name: sum((getattr(row, name) for row in rows), Decimal(0)) for name in ("spend_usd", "units_sold", "full_loss_cancelled_qty", "full_loss_qty", "refund_only_qty", "refund_return_qty", "sales_vnd", "settled_net_vnd", "settled_sales_vnd", "unsettled_sales_vnd", "refund_only_vnd", "refund_return_vnd", "refund_cancelled_vnd", "cancelled_sales_vnd")}
    ints = {name: sum(getattr(row, name) for row in rows) for name in ("order_count", "cancelled_orders", "domestic_cancelled_orders", "overseas_cancelled_orders", "refund_order_count", "ad_orders")}
    formula = calculate(FormulaInput(spend_usd=total["spend_usd"], ad_gmv_usd=Decimal(0), ad_orders=ints["ad_orders"], order_count=ints["order_count"], cancelled_orders=ints["cancelled_orders"], domestic_cancelled_orders=ints["domestic_cancelled_orders"], overseas_cancelled_orders=ints["overseas_cancelled_orders"], units_sold=int(total["units_sold"]), full_loss_cancelled_qty=int(total["full_loss_cancelled_qty"]), full_loss_qty=int(total["full_loss_qty"]), refund_order_count=ints["refund_order_count"], refund_only_qty=int(total["refund_only_qty"]), refund_return_qty=int(total["refund_return_qty"]), sales_vnd=total["sales_vnd"], settled_net_vnd=total["settled_net_vnd"], settled_sales_vnd=total["settled_sales_vnd"], unsettled_sales_vnd=total["unsettled_sales_vnd"], refund_only_vnd=total["refund_only_vnd"], refund_return_vnd=total["refund_return_vnd"], refund_cancelled_vnd=total["refund_cancelled_vnd"], cancelled_sales_vnd=total["cancelled_sales_vnd"], unit_cost_cny=costs.get(key[1], _DEFAULT_COST), usd_cny=usd_cny, usd_vnd=usd_vnd, unsettled_fee_rate=rates.get(key[0], _DEFAULT_FEE)))
    return WindowMetric(*key, start, end, formula.roi_real, formula.net_profit_cny, formula.spend_cny, formula.total_orders, ints["ad_orders"], len(rows))


def _distribution(values: list[Decimal]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "p25": None, "median": None, "p75": None, "max": None}
    ordered = sorted(values)
    def percentile(rank: Decimal) -> Decimal:
        index = min(len(ordered) - 1, int((len(ordered) - 1) * rank))
        return ordered[index]
    return {"count": len(ordered), "min": ordered[0], "p25": percentile(Decimal("0.25")), "median": percentile(Decimal("0.50")), "p75": percentile(Decimal("0.75")), "max": ordered[-1]}


def _critical_candidate(config: AlertConfig) -> AlertConfig:
    return AlertConfig(
        roi_abs_delta=config.roi_abs_delta * 2,
        roi_relative_decline=min(Decimal(1), config.roi_relative_decline * 2),
        net_profit_decline=min(Decimal(1), config.net_profit_decline * 2),
        min_spend_cny=config.min_spend_cny * 3,
        min_orders=config.min_orders + 2,
        min_ad_orders=config.min_ad_orders,
    )


def _volume_summary(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"median": None, "mean": None, "max": None, "anchor_count": 0, "anchor_total": 0, "30_anchor_total": None}
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    total = sum(values)
    return {"median": median, "mean": total / len(values), "max": max(values), "anchor_count": len(values), "anchor_total": total, "30_anchor_total": total if len(values) == 30 else None}


def _per_anchor_volumes(
    rows: list[tuple[date, tuple[int, int], WindowMetric, WindowMetric, WindowMetric, WindowMetric]],
    decisions: list[AlertDecision],
    anchors: list[date],
) -> list[dict[str, Any]]:
    by_anchor: dict[date, dict[str, int]] = {anchor: {"warning": 0, "critical": 0} for anchor in anchors}
    for row, decision in zip(rows, decisions, strict=True):
        anchor = row[0]
        if decision.severity in {"warning", "critical"}:
            by_anchor[anchor][decision.severity] += 1
    return [{"anchor": anchor.isoformat(), "warning": counts["warning"], "critical": counts["critical"], "total": counts["warning"] + counts["critical"]} for anchor, counts in sorted(by_anchor.items())]


def _run_backtest(facts: list[DailyFact], *, anchor_start: date, anchor_end: date, costs: dict[int, Decimal], rates: dict[int, Decimal], usd_cny: Decimal, usd_vnd: Decimal) -> dict[str, Any]:
    keys = sorted({(row.shop_pk, row.spu_pk) for row in facts})
    anchors = [anchor_start + timedelta(days=i) for i in range((anchor_end - anchor_start).days + 1)]
    configs = [AlertConfig(Decimal(a), Decimal(r), Decimal(n), Decimal(s), o, 0) for a in ("0.10", "0.20", "0.30", "0.50") for r in ("0.10", "0.20", "0.30") for n in ("0.10", "0.25", "0.40") for s in ("50", "100", "300") for o in (2, 5)]
    # Materialize each comparison once.  This keeps a bounded probe predictable
    # even when the production database has many daily rows.
    comparisons: dict[int, list[tuple[date, tuple[int, int], WindowMetric, WindowMetric, WindowMetric, WindowMetric]]] = defaultdict(list)
    for days in (1, 3, 7):
        for anchor in anchors:
            fast_current, fast_previous = comparison_windows(anchor, days)
            confirm_current, confirm_previous = confirmation_windows(anchor, days)
            for key in keys:
                current = _aggregate(facts, key=key, start=fast_current[0], end=fast_current[1], costs=costs, rates=rates, usd_cny=usd_cny, usd_vnd=usd_vnd)
                previous = _aggregate(facts, key=key, start=fast_previous[0], end=fast_previous[1], costs=costs, rates=rates, usd_cny=usd_cny, usd_vnd=usd_vnd)
                confirmed_current = _aggregate(facts, key=key, start=confirm_current[0], end=confirm_current[1], costs=costs, rates=rates, usd_cny=usd_cny, usd_vnd=usd_vnd)
                confirmed_previous = _aggregate(facts, key=key, start=confirm_previous[0], end=confirm_previous[1], costs=costs, rates=rates, usd_cny=usd_cny, usd_vnd=usd_vnd)
                if current.fact_days or previous.fact_days:
                    comparisons[days].append((anchor, key, previous, current, confirmed_previous, confirmed_current))
    matrix: list[dict[str, Any]] = []
    distributions: dict[str, Any] = {}
    for days, rows in comparisons.items():
        distributions[str(days)] = {
            "previous": {"roiReal": _distribution([metric.roi_real for _, _, metric, _, _, _ in rows if metric.roi_real is not None]), "netProfitCny": _distribution([metric.net_profit for _, _, metric, _, _, _ in rows]), "spendCny": _distribution([metric.spend_cny for _, _, metric, _, _, _ in rows]), "totalOrders": _distribution([Decimal(metric.total_orders) for _, _, metric, _, _, _ in rows])},
            "current": {"roiReal": _distribution([metric.roi_real for _, _, _, metric, _, _ in rows if metric.roi_real is not None]), "netProfitCny": _distribution([metric.net_profit for _, _, _, metric, _, _ in rows]), "spendCny": _distribution([metric.spend_cny for _, _, _, metric, _, _ in rows]), "totalOrders": _distribution([Decimal(metric.total_orders) for _, _, _, metric, _, _ in rows])},
        }
        for config in configs:
            decisions = [evaluate_alert(previous, current, config, _critical_candidate(config)) for _, _, previous, current, _, _ in rows]

            alerts = sum(d.alert for d in decisions)
            warning_alerts = sum(d.severity == "warning" for d in decisions)
            critical_alerts = sum(d.severity == "critical" for d in decisions)
            sufficient = sum(d.sample_sufficient for d in decisions)
            matrix.append({"window_days": days, "config": asdict(config), "comparisons": len(decisions), "sample_sufficient": sufficient, "alerts": alerts, "warning_alerts": warning_alerts, "critical_alerts": critical_alerts, "alert_rate_on_sufficient": float(Decimal(alerts) / Decimal(sufficient)) if sufficient else None, "states": {state: sum(d.state == state for d in decisions) for state in ("profit_to_loss", "loss_expanding", "roi_deterioration", "net_profit_deterioration", "stable", "roi_recovery", "recovery", "loss_to_profit", "sample_insufficient", "unavailable")}})
    matrix.sort(key=lambda row: (row["alert_rate_on_sufficient"] is None, row["alert_rate_on_sufficient"] or 0, -row["sample_sufficient"]))
    defaults = {
        "fast": (AlertConfig(Decimal("0.20"), Decimal("0.20"), Decimal("0.25"), Decimal("100"), 3, 0), AlertConfig(Decimal("0.40"), Decimal("0.40"), Decimal("0.40"), Decimal("300"), 5, 0)),
        "confirmation": (AlertConfig(Decimal("0.15"), Decimal("0.15"), Decimal("0.20"), Decimal("100"), 3, 0), AlertConfig(Decimal("0.30"), Decimal("0.30"), Decimal("0.35"), Decimal("300"), 5, 0)),
    }
    operational: dict[str, Any] = {}
    for name, (warning_config, critical_config) in defaults.items():
        by_days: dict[str, Any] = {}
        for days, rows in comparisons.items():
            fast_decisions = [evaluate_alert(previous, current, warning_config, critical_config) for _, _, previous, current, _, _ in rows]
            confirmation_decisions = [evaluate_alert(confirmed_previous, confirmed_current, warning_config, critical_config) for _, _, _, _, confirmed_previous, confirmed_current in rows]
            alert_keys = {(anchor, key) for (anchor, key, *_), decision in zip(rows, fast_decisions, strict=True) if decision.alert}
            confirmed_keys = {(anchor, key) for (anchor, key, *_), decision in zip(rows, confirmation_decisions, strict=True) if decision.alert}
            persistence = len(alert_keys & confirmed_keys) / len(alert_keys) if alert_keys else None
            eligible_recovery = sum(decision.alert for decision in fast_decisions)
            recovered = sum(
                fast_decision.alert and confirmation_decision.state in {"loss_to_profit", "recovery", "roi_recovery"}
                for fast_decision, confirmation_decision in zip(fast_decisions, confirmation_decisions, strict=True)
            )
            recovery = recovered / eligible_recovery if eligible_recovery else None
            fast_per_anchor = _per_anchor_volumes(rows, fast_decisions, anchors)
            confirmation_per_anchor = _per_anchor_volumes(rows, confirmation_decisions, anchors)
            by_days[str(days)] = {
                "fast": {
                    "per_anchor": fast_per_anchor,
                    "warning": _volume_summary([item["warning"] for item in fast_per_anchor]),
                    "critical": _volume_summary([item["critical"] for item in fast_per_anchor]),
                },
                "confirmation": {
                    "per_anchor": confirmation_per_anchor,
                    "warning": _volume_summary([item["warning"] for item in confirmation_per_anchor]),
                    "critical": _volume_summary([item["critical"] for item in confirmation_per_anchor]),
                },
                "persistence_rate": persistence,
                "recovery": {"numerator": recovered, "denominator": eligible_recovery, "rate": recovery},
                "fast_sample_status_counts": {status: sum(d.sample_status == status for d in fast_decisions) for status in ("sufficient", "sample_insufficient", "unavailable")},
                "confirmation_sample_status_counts": {status: sum(d.sample_status == status for d in confirmation_decisions) for status in ("sufficient", "sample_insufficient", "unavailable")},
            }
        operational[name] = by_days
    return {"candidate_threshold_matrix": matrix[:24], "matrix_candidates_evaluated": len(matrix), "window_days": [1, 3, 7], "anchors": len(anchors), "distributions": distributions, "operational_metrics": operational}


def _config_wire(config: AlertConfig) -> dict[str, Any]:
    return {**asdict(config), "label": "回测暂定", "runtimeUse": "seed_fallback_only"}


def _stable_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _digest_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in artifact.items() if key not in {"generated_at", "evidence_artifact"}}


def verify_artifact(path: Path) -> None:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    evidence = artifact.get("evidence_artifact")
    if not isinstance(evidence, dict) or evidence.get("immutable") is not True or evidence.get("aggregateOnly") is not True:
        raise SystemExit("invalid evidence_artifact envelope")
    expected = _stable_digest(_digest_payload(artifact))
    actual = evidence.get("digest")
    if actual != expected:
        raise SystemExit(f"evidence digest mismatch: expected {expected}, got {actual}")
    print(f"evidence-digest: passed ({actual})")


def _database_name(database_url: str) -> str:
    try:
        database_name = make_url(database_url).database
    except Exception as exc:  # noqa: BLE001 - malformed operator input must fail closed.
        raise SystemExit("invalid --database-url") from exc
    if not database_name:
        raise SystemExit("--database-url has no database name")
    return database_name


def _is_prod_shaped_url(database_url: str | None) -> bool:
    if not database_url or not database_url.strip():
        return True
    previous = os.environ.get("TTS_ERP_DB_URL")
    os.environ["TTS_ERP_DB_URL"] = database_url
    try:
        return is_prod_shaped_db()
    finally:
        if previous is None:
            os.environ.pop("TTS_ERP_DB_URL", None)
        else:
            os.environ["TTS_ERP_DB_URL"] = previous


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.environ.get("TTS_ERP_DB_URL"), help="SQLAlchemy URL; production read only")
    parser.add_argument("--confirm-read-only-production", action="store_true", help="required for a production-shaped database")
    parser.add_argument("--end-date", type=date.fromisoformat, default=datetime.now(UTC).date() - timedelta(days=2))
    parser.add_argument("--lookback-days", type=int, default=90)
    parser.add_argument("--max-spus", type=int, default=1000)
    parser.add_argument("--statement-timeout-ms", type=int, default=120000)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-artifact", type=Path, help="verify an existing aggregate-only artifact without database access")
    return parser.parse_args()


def main() -> int:
    self_check_sql_scope()
    self_check_policy()
    args = parse_args()
    if args.verify_artifact:
        verify_artifact(args.verify_artifact)
        return 0
    if not args.database_url or not args.database_url.strip():
        raise SystemExit("--database-url or TTS_ERP_DB_URL is required")
    database_name = _database_name(args.database_url)
    if database_name.startswith("tts_erp_test") or database_name in {"tts_erp_v3_test", "tts_erp_test_template"}:
        raise SystemExit("refusing test-shaped database; this probe is production-read-only")
    if _is_prod_shaped_url(args.database_url) and not args.confirm_read_only_production:
        raise SystemExit("production-shaped database requires --confirm-read-only-production")
    if args.lookback_days < 1 or args.lookback_days > 365:
        raise SystemExit("--lookback-days must be 1..365")
    anchor_end = args.end_date
    anchor_start = anchor_end - timedelta(days=args.lookback_days - 1)
    query_start = anchor_start - timedelta(days=22)
    engine = create_engine(args.database_url, pool_pre_ping=True)
    # Reuse the maintained profitability snapshot seam: REPEATABLE READ + READ ONLY before any fact SELECT.
    with engine.connect() as connection, Session(bind=connection) as session:
            with consistent_read_snapshot(session) as snapshot_calculated_at:
                session.execute(text("SELECT set_config('statement_timeout', :timeout, true)"), {"timeout": f"{args.statement_timeout_ms}ms"})
                session.execute(text("SELECT set_config('lock_timeout', '2s', true)"))
                isolation = session.execute(text("SHOW transaction_isolation")).scalar_one()
                read_only = session.execute(text("SHOW transaction_read_only")).scalar_one()
                if isolation != "repeatable read" or read_only != "on":
                    raise RuntimeError(f"snapshot settings not enforced: {isolation}/{read_only}")
                facts = _read_facts(session, start_day=query_start, end_day=anchor_end, max_spus=args.max_spus)
            keys = {(row.shop_pk, row.spu_pk) for row in facts}
            as_of = snapshot_calculated_at
            if keys:
                costs, rates, fee_sources, usd_cny, usd_vnd = _costs_and_rates(session, keys, as_of=as_of)
            else:
                costs, rates, fee_sources, usd_cny, usd_vnd = {}, {}, {"shop_estimate": 0, "stale_fallback": 0, "baseline": 0}, _DEFAULT_USD_CNY, _DEFAULT_USD_VND
            result = _run_backtest(facts, anchor_start=anchor_start, anchor_end=anchor_end, costs=costs, rates=rates, usd_cny=usd_cny, usd_vnd=usd_vnd)
            result["self_checks"] = {"sql_scope": "passed", "evaluability_boundaries": "passed"}
            result.update({"generated_at": datetime.now(UTC).isoformat(), "snapshot": {"calculatedAt": snapshot_calculated_at.isoformat(), "transactionIsolation": isolation, "transactionReadOnly": read_only}, "provisionalLabel": "回测暂定", "provisionalStatus": "seed_fallback_only", "data_window": {"query_start": query_start.isoformat(), "anchor_start": anchor_start.isoformat(), "anchor_end": anchor_end.isoformat(), "observation_anchor": "T-2"}, "coverage": {"daily_fact_rows": len(facts), "shop_spu_count": len(keys), "shops": len({row.shop_pk for row in facts}), "manual_cost_share": float(Decimal(len(costs)) / Decimal(len(keys))) if keys else None, "fee_rate_source_counts": fee_sources, "fx_source": "fx.exchange_rates latest USD snapshot" if usd_cny != _DEFAULT_USD_CNY or usd_vnd != _DEFAULT_USD_VND else "fallback constants; inspect limitation"}, "provisional_defaults": {"fast_warning": _config_wire(AlertConfig(Decimal("0.20"), Decimal("0.20"), Decimal("0.25"), Decimal("100"), 3, 0)), "fast_critical": _config_wire(AlertConfig(Decimal("0.40"), Decimal("0.40"), Decimal("0.40"), Decimal("300"), 5, 0)), "confirmation_warning": _config_wire(AlertConfig(Decimal("0.15"), Decimal("0.15"), Decimal("0.20"), Decimal("100"), 3, 0)), "confirmation_critical": _config_wire(AlertConfig(Decimal("0.30"), Decimal("0.30"), Decimal("0.35"), Decimal("300"), 5, 0))}, "limitations": ["ROI and net profit are recomputed through analytics.spu_profitability._formula_v10.calculate after SQL aggregation; identifiers are intentionally omitted from output.", "The probe uses UTC calendar dates for bounded reproducibility; production alert implementation must apply each shop's IANA timezone before materialization.", "A latest FX snapshot and current effective manual cost/rate are used, matching the current profitability valuation basis rather than historical accounting valuation.", "Rows with no order facts are not counted; ad-only rows are retained only when they join an SPU fact. Missing ad_daily coverage can understate spend.", "Persistence/reversal requires contiguous anchor observations; this bounded report supplies candidate matrix and state counts but does not infer missing-day continuity."], "metric_definitions": {"usable_coverage": "shop x SPU daily fact keys with at least one paid/cancelled order in the bounded query window", "roi_decline": "(prior ROI - current ROI) / prior ROI only when prior ROI > 0; prior ROI <= 0 uses explicit state", "net_profit_decline": "(prior net profit - current net profit) / prior net profit only when prior net profit > 0", "sample_status": "unavailable when either comparison window has no required fact row; sample_insufficient when facts exist but ROI is null or warning gates fail; sufficient only when both ROI values are defined and gates pass", "sample_sufficient": "sample_status=sufficient; zero thresholds do not bypass missing/null ROI evaluability", "persistence": "a fast alert whose same-key shifted confirmation comparison remains an alert", "reversal_recovery": "for every fast-alert (anchor,shop×SPU), pair the same confirmation decision regardless of confirmation alert; recovered states loss_to_profit/recovery/roi_recovery are numerator, fast-alert count is denominator, zero denominator is null", "per_anchor_volume": "each anchor emits warning/critical/total; summaries include median, mean, max, anchor_count, anchor_total and 30_anchor_total when anchor_count=30"}})
    result["evidence_artifact"] = {
        "schemaVersion": "spu-profit-deterioration-backtest.v1",
        "immutable": True,
        "aggregateOnly": True,
        "rowLevelIdentifiers": "omitted",
        "digest": _stable_digest(_digest_payload(result)),
        "digestInput": "canonical sorted JSON excluding generated_at and evidence_artifact",
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
