"""Pure window and alert policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from statistics import mean, median
from typing import Any

from .config import AlertThresholds
from .facts import WindowMetric


class AlertSeverity(StrEnum):
    NONE = "none"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertState(StrEnum):
    PROFIT_TO_LOSS = "profit_to_loss"
    LOSS_EXPANDING = "loss_expanding"
    LOSS_TO_PROFIT = "loss_to_profit"
    ROI_DETERIORATION = "roi_deterioration"
    NET_PROFIT_DETERIORATION = "net_profit_deterioration"
    STABLE = "stable"
    SAMPLE_INSUFFICIENT = "sample_insufficient"
    UNAVAILABLE = "unavailable"


class SampleStatus(StrEnum):
    SUFFICIENT = "sufficient"
    SAMPLE_INSUFFICIENT = "sample_insufficient"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class WindowPair:
    current_start: date
    current_end: date
    previous_start: date
    previous_end: date


def _pair(anchor: date, days: int, shift: int = 0) -> WindowPair:
    end = anchor - timedelta(days=shift)
    current_start = end - timedelta(days=days - 1)
    previous_end = current_start - timedelta(days=1)
    return WindowPair(
        current_start, end, previous_end - timedelta(days=days - 1), previous_end
    )


def fast_windows(anchor: date) -> dict[int, WindowPair]:
    """Return exact non-overlapping fast comparisons ending at the anchor date."""
    return {days: _pair(anchor, days) for days in (1, 3, 7)}


def confirmation_windows(anchor: date) -> dict[int, WindowPair]:
    """Return comparisons shifted seven days before the fast comparison."""
    return {days: _pair(anchor, days, 7) for days in (1, 3, 7)}


def compare_windows(
    anchor: date, *, confirmation: bool = False
) -> dict[int, WindowPair]:
    return confirmation_windows(anchor) if confirmation else fast_windows(anchor)


@dataclass(frozen=True, slots=True)
class AlertDecision:
    state: AlertState
    severity: AlertSeverity
    sample_status: SampleStatus
    roi_decline: Decimal | None
    net_profit_decline: Decimal | None
    current: WindowMetric
    previous: WindowMetric


def _decline(previous: Decimal | None, current: Decimal | None) -> Decimal | None:
    if previous is None or current is None or previous <= 0:
        return None
    return (previous - current) / previous


def _state(previous: WindowMetric, current: WindowMetric) -> AlertState:
    if previous.roi_real is not None and current.roi_real is not None:
        if previous.roi_real > 0 >= current.roi_real:
            return AlertState.PROFIT_TO_LOSS
        if previous.roi_real <= 0 and current.roi_real < previous.roi_real:
            return AlertState.LOSS_EXPANDING
        if previous.roi_real <= 0 < current.roi_real:
            return AlertState.LOSS_TO_PROFIT
    if previous.net_profit_cny is not None and current.net_profit_cny is not None:
        if previous.net_profit_cny > 0 >= current.net_profit_cny:
            return AlertState.PROFIT_TO_LOSS
        if previous.net_profit_cny <= 0 < current.net_profit_cny:
            return AlertState.LOSS_TO_PROFIT
    roi_decline = _decline(previous.roi_real, current.roi_real)
    profit_decline = _decline(previous.net_profit_cny, current.net_profit_cny)
    if roi_decline is not None and roi_decline > 0:
        return AlertState.ROI_DETERIORATION
    if profit_decline is not None and profit_decline > 0:
        return AlertState.NET_PROFIT_DETERIORATION
    return AlertState.STABLE


def _gates_met(
    previous: WindowMetric, current: WindowMetric, thresholds: AlertThresholds
) -> bool:
    """Return whether a comparison is credible: both windows must clear the gates.

    The gates answer "can these two windows be compared", so they bind the
    weaker of the pair. The design doc
    (``docs/design/spu-profit-deterioration-alert.md`` §2.2 evaluability) and
    ``scripts/probe_spu_profit_deterioration_thresholds.py`` both gate on
    ``min(previous, current)``; ``evaluate()`` shares this one helper so the
    sample_status gates and the severity gates cannot drift apart again.
    """
    return (
        min(previous.spend_cny, current.spend_cny) >= thresholds.min_spend_cny
        and min(previous.order_count, current.order_count) >= thresholds.min_orders
        and min(previous.ad_orders, current.ad_orders) >= thresholds.min_ad_orders
    )


def evaluate(
    previous: WindowMetric,
    current: WindowMetric,
    warning: AlertThresholds,
    critical: AlertThresholds,
) -> AlertDecision:
    """Evaluate gates before classifying severity; all threshold boundaries inclusive."""
    if not previous.has_facts or not current.has_facts:
        status = SampleStatus.UNAVAILABLE
    elif (
        previous.roi_real is None
        or current.roi_real is None
        or previous.net_profit_cny is None
        or current.net_profit_cny is None
        or previous.spend_cny <= 0
        or current.spend_cny <= 0
    ):
        status = SampleStatus.SAMPLE_INSUFFICIENT
    else:
        status = SampleStatus.SUFFICIENT
    if status is SampleStatus.SUFFICIENT and not _gates_met(
        previous, current, warning
    ):
        status = SampleStatus.SAMPLE_INSUFFICIENT
    roi_decline = _decline(previous.roi_real, current.roi_real)
    net_profit_decline = _decline(previous.net_profit_cny, current.net_profit_cny)
    state = _state(previous, current)
    if status is not SampleStatus.SUFFICIENT:
        return AlertDecision(
            state
            if status is SampleStatus.SUFFICIENT
            else (
                AlertState.UNAVAILABLE
                if status is SampleStatus.UNAVAILABLE
                else AlertState.SAMPLE_INSUFFICIENT
            ),
            AlertSeverity.NONE,
            status,
            roi_decline,
            net_profit_decline,
            current,
            previous,
        )

    def qualifies(t: AlertThresholds, *, allow_transition: bool) -> bool:
        roi_abs = (
            previous.roi_real is not None
            and current.roi_real is not None
            and previous.roi_real - current.roi_real >= t.roi_abs_delta
        )
        roi_rel = roi_decline is not None and roi_decline >= t.roi_relative_decline
        impact = (
            net_profit_decline is not None
            and net_profit_decline >= t.net_profit_decline
        )
        gates = _gates_met(previous, current, t)
        transition = allow_transition and state in {
            AlertState.PROFIT_TO_LOSS,
            AlertState.LOSS_EXPANDING,
        }
        # A warning transition is itself the deterioration trigger: warning
        # gates are sufficient. Critical never uses this shortcut and therefore
        # still requires independent ROI and net-profit thresholds.
        return gates and (transition or (impact and roi_abs and roi_rel))

    severity = AlertSeverity.NONE
    if qualifies(warning, allow_transition=True):
        severity = AlertSeverity.WARNING
        if qualifies(critical, allow_transition=False):
            severity = AlertSeverity.CRITICAL
    return AlertDecision(
        state, severity, status, roi_decline, net_profit_decline, current, previous
    )


def paired_recovery(
    fast_alerts: list[tuple[Any, AlertDecision]],
    confirmations: dict[Any, AlertDecision],
) -> dict[str, Decimal | int | None]:
    """Pair every fast alert, including non-alert confirmations, by the same key."""
    denominator = len(fast_alerts)
    if denominator == 0:
        return {"numerator": 0, "denominator": 0, "rate": None}
    recovered_states = {AlertState.LOSS_TO_PROFIT}
    numerator = sum(
        1
        for key, _ in fast_alerts
        if key in confirmations and confirmations[key].state in recovered_states
    )
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": Decimal(numerator) / Decimal(denominator),
    }


def per_anchor_summary(decisions: list[AlertDecision]) -> dict[str, Decimal | int]:
    warning = sum(d.severity is AlertSeverity.WARNING for d in decisions)
    critical = sum(d.severity is AlertSeverity.CRITICAL for d in decisions)
    values = [warning, critical]
    return {
        "warning": warning,
        "critical": critical,
        "anchor_count": 1,
        "mean": Decimal(str(mean(values))),
        "median": Decimal(str(median(values))),
        "max": max(values),
    }
