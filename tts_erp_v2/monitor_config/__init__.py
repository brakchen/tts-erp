"""Pure contract layer for the monitor remote-configuration wire schema.

This package is intentionally:

- DB-free (no SQLAlchemy, no models)
- Plugin-free (no Chrome storage, no TypeScript interop)
- Network-free (no HTTP)
- Secret-decryption-free (no ``token_service``)
- Logger-free (no structlog, no audit trail; secret values never touch this layer)

It validates and composes only the wire payload. Later slices will
introduce the persistence, HTTP, and delivery layers; this slice is
the typed single source of truth for the contract.

Invariants inherited from the maintained plugin side
(``plugins/monitor/src/core/{scheduler,campaign-query-window,thresholds,types}.ts``):

- ``MIN_CHROME_ALARM_DELAY_SECONDS = 30``
- ``MIN_QUERY_RECENT_DAYS = 1``
- Spend and conversion-cost thresholds must be finite positive numbers
- ``skuOrderLimit`` is a non-negative integer

The wire schema is the strict subset of the local plugin settings
defined in ``docs/monitor-config-sync.md`` §3.1. Runtime state (``monitorEnabled``,
``status``, ``nextScanAt``, ...), browser cookies, ERP tokens,
service URLs, and the deprecated ``queryRecentDays`` field are
explicitly forbidden — see :data:`FORBIDDEN_WIRE_FIELDS`.
"""

from __future__ import annotations

from tts_erp_v2.monitor_config.contracts import (
    FORBIDDEN_WIRE_FIELDS,
    MIN_CHROME_ALARM_DELAY_SECONDS,
    MIN_QUERY_RECENT_DAYS,
    WIRE_SCHEMA_VERSION,
    CampaignRuleOverrides,
    ConfigContractError,
    Current,
    DeliveryEnvelope,
    MonitorBusinessSettings,
    WireShapeForce,
    config_to_payload,
    merge_business_settings,
    normalize_campaign_thresholds,
    parse_envelope,
    validate_business_settings,
    validate_complete_business_settings,
    validate_settings_override,
)

__all__ = [
    "FORBIDDEN_WIRE_FIELDS",
    "MIN_CHROME_ALARM_DELAY_SECONDS",
    "MIN_QUERY_RECENT_DAYS",
    "WIRE_SCHEMA_VERSION",
    "CampaignRuleOverrides",
    "ConfigContractError",
    "Current",
    "DeliveryEnvelope",
    "MonitorBusinessSettings",
    "WireShapeForce",
    "config_to_payload",
    "merge_business_settings",
    "normalize_campaign_thresholds",
    "parse_envelope",
    "validate_business_settings",
    "validate_complete_business_settings",
    "validate_settings_override",
]