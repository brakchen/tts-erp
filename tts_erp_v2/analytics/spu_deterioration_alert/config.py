"""Typed runtime configuration for deterioration alerts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

ALERT_CONFIG_KEY = "analytics.spu_profit_deterioration_alert.v1"


@dataclass(frozen=True, slots=True)
class AlertThresholds:
    roi_abs_delta: Decimal
    roi_relative_decline: Decimal
    net_profit_decline: Decimal
    min_spend_cny: Decimal
    min_orders: int
    min_ad_orders: int


@dataclass(frozen=True, slots=True)
class AlertLayerConfig:
    warning: AlertThresholds
    critical: AlertThresholds


@dataclass(frozen=True, slots=True)
class AlertConfig:
    enabled: bool
    maturity_days: int
    fast: dict[int, AlertLayerConfig]
    confirmation: dict[int, AlertLayerConfig]
    source: str = "runtime_config"
    version: int | None = None
    payload_hash: str | None = None
    updated_at: Any | None = None
    updated_by: str | None = None


def _thresholds(
    w: str, r: str, n: str, spend: str, orders: int, ad_orders: int
) -> AlertThresholds:
    return AlertThresholds(
        Decimal(w), Decimal(r), Decimal(n), Decimal(spend), orders, ad_orders
    )


def _layer(
    w: tuple[str, str, str, str, int, int], c: tuple[str, str, str, str, int, int]
) -> AlertLayerConfig:
    return AlertLayerConfig(_thresholds(*w), _thresholds(*c))


_SEED_FAST = {
    1: _layer((".20", ".20", ".25", "100", 3, 0), (".40", ".40", ".40", "300", 5, 0)),
    3: _layer((".20", ".20", ".25", "100", 3, 0), (".40", ".40", ".40", "300", 5, 0)),
    7: _layer((".20", ".20", ".25", "100", 3, 0), (".40", ".40", ".40", "300", 5, 0)),
}
_SEED_CONFIRMATION = {
    1: _layer((".15", ".15", ".20", "100", 3, 0), (".30", ".30", ".35", "300", 5, 0)),
    3: _layer((".15", ".15", ".20", "100", 3, 0), (".30", ".30", ".35", "300", 5, 0)),
    7: _layer((".15", ".15", ".20", "100", 3, 0), (".30", ".30", ".35", "300", 5, 0)),
}
SEED_FALLBACK_CONFIG = AlertConfig(
    True, 7, _SEED_FAST, _SEED_CONFIRMATION, "seed_fallback"
)


def _decimal(value: Any, path: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{path} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{path} must be a finite decimal")
    return result


def _threshold(payload: Mapping[str, Any], path: str) -> AlertThresholds:
    expected = {
        "roiAbsDelta",
        "roiRelativeDecline",
        "netProfitDecline",
        "minSpendCny",
        "minOrders",
        "minAdOrders",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise ValueError(f"{path} has unexpected or missing fields")
    result = AlertThresholds(
        _decimal(payload["roiAbsDelta"], f"{path}.roiAbsDelta"),
        _decimal(payload["roiRelativeDecline"], f"{path}.roiRelativeDecline"),
        _decimal(payload["netProfitDecline"], f"{path}.netProfitDecline"),
        _decimal(payload["minSpendCny"], f"{path}.minSpendCny"),
        payload["minOrders"],
        payload["minAdOrders"],
    )
    if (
        not isinstance(result.min_orders, int)
        or isinstance(result.min_orders, bool)
        or not 0 <= result.min_orders <= 100000
    ):
        raise ValueError(f"{path}.minOrders out of range")
    if (
        not isinstance(result.min_ad_orders, int)
        or isinstance(result.min_ad_orders, bool)
        or not 0 <= result.min_ad_orders <= 100000
    ):
        raise ValueError(f"{path}.minAdOrders out of range")
    if (
        not 0 <= result.roi_abs_delta <= 10
        or not 0 <= result.roi_relative_decline <= 1
        or not 0 <= result.net_profit_decline <= 1
        or not 0 <= result.min_spend_cny <= 10_000_000
    ):
        raise ValueError(f"{path} threshold out of range")
    return result


def validate_alert_config(
    payload: Mapping[str, Any],
    *,
    source: str = "runtime_config",
    version: int | None = None,
    payload_hash: str | None = None,
) -> AlertConfig:
    """Validate and normalize the sole global alert payload."""
    if not isinstance(payload, Mapping):
        raise TypeError("alert payload must be an object")
    if set(payload) != {"enabled", "maturityDays", "fast", "confirmation"}:
        raise ValueError(
            "alert payload must contain exactly enabled, maturityDays, fast, confirmation"
        )
    if not isinstance(payload["enabled"], bool) or payload["maturityDays"] != 7:
        raise ValueError("enabled must be boolean and maturityDays must equal 7")
    layers: dict[str, dict[int, AlertLayerConfig]] = {}
    for layer_name in ("fast", "confirmation"):
        layer_payload = payload[layer_name]
        if not isinstance(layer_payload, Mapping) or set(layer_payload) != {
            "1",
            "3",
            "7",
        }:
            raise ValueError(f"{layer_name} must contain windows 1, 3 and 7")
        layers[layer_name] = {}
        for days in (1, 3, 7):
            entry = layer_payload[str(days)]
            if not isinstance(entry, Mapping) or set(entry) != {"warning", "critical"}:
                raise ValueError(
                    f"{layer_name}.{days} must contain warning and critical"
                )
            warning = _threshold(entry["warning"], f"{layer_name}.{days}.warning")
            critical = _threshold(entry["critical"], f"{layer_name}.{days}.critical")
            if any(
                getattr(critical, field) < getattr(warning, field)
                for field in (
                    "roi_abs_delta",
                    "roi_relative_decline",
                    "net_profit_decline",
                    "min_spend_cny",
                    "min_orders",
                    "min_ad_orders",
                )
            ):
                raise ValueError(f"{layer_name}.{days}.critical must be >= warning")
            layers[layer_name][days] = AlertLayerConfig(warning, critical)
    return AlertConfig(
        payload["enabled"],
        7,
        layers["fast"],
        layers["confirmation"],
        source,
        version,
        payload_hash,
    )


def config_to_payload(config: AlertConfig) -> dict[str, Any]:
    def out_threshold(t: AlertThresholds) -> dict[str, Any]:
        return {
            "roiAbsDelta": str(t.roi_abs_delta),
            "roiRelativeDecline": str(t.roi_relative_decline),
            "netProfitDecline": str(t.net_profit_decline),
            "minSpendCny": str(t.min_spend_cny),
            "minOrders": t.min_orders,
            "minAdOrders": t.min_ad_orders,
        }

    def out_layer(layer: AlertLayerConfig) -> dict[str, Any]:
        return {
            "warning": out_threshold(layer.warning),
            "critical": out_threshold(layer.critical),
        }

    return {
        "enabled": config.enabled,
        "maturityDays": 7,
        "fast": {str(k): out_layer(v) for k, v in config.fast.items()},
        "confirmation": {str(k): out_layer(v) for k, v in config.confirmation.items()},
    }
