r"""Pure contract layer for the monitor remote-configuration wire schema.

This module is intentionally:

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
- ``shopPk`` and every delivery / force ``version`` are canonical
  decimal identifier strings (see :func:`_decimal_identifier`), matching
  the plugin's ``DECIMAL_IDENTIFIER = /^\d+$/`` in
  ``plugins/monitor/src/core/remote-config.ts``

The wire schema is the strict subset of the local plugin settings
defined in ``docs/monitor-config-sync.md`` §3.1. Runtime state
(``monitorEnabled``, ``status``, ``nextScanAt``, ...), browser cookies,
ERP tokens, service URLs, and the deprecated ``queryRecentDays`` field
are explicitly forbidden — see :data:`FORBIDDEN_WIRE_FIELDS`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, ClassVar, Optional, Union, cast

#: Public type alias for the documented wire-shape ``force`` block.
#: Matches ``docs/monitor-config-sync.md`` §6.1 (``{"version": "...", "target":
#: <complete delivery block>}``). The runtime contract is enforced
#: exclusively by :func:`_coerce_force` — a static type checker that
#: sees a dict literal can accept it via this alias without widening
#: the runtime semantics. Keep ``Mapping`` (not ``dict``) so callers
#: that pass ``MappingProxyType`` or other read-only mappings are also
#: statically accepted.
WireShapeForce = Mapping[str, Any]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Sole advertised wire schema version. ``int`` per docs/monitor-config-sync.md §6.1.
WIRE_SCHEMA_VERSION: int = 1

#: Matches ``plugins/monitor/src/core/scheduler.ts::MIN_CHROME_ALARM_DELAY_SECONDS``.
MIN_CHROME_ALARM_DELAY_SECONDS: int = 30

#: Matches ``plugins/monitor/src/core/campaign-query-window.ts::MIN_QUERY_RECENT_DAYS``.
MIN_QUERY_RECENT_DAYS: int = 1

#: Field names that MUST NEVER appear in a wire payload. The forbidden
#: set includes runtime state (e.g. ``monitorEnabled``, ``status``,
#: ``nextScanAt``), deprecated fields (``queryRecentDays``),
#: identity-only fields (``advertiserId``), transport secrets
#: (``token``, ``cookie``), and service URLs. The server-side
#: publication layer rejects any payload containing these.
FORBIDDEN_WIRE_FIELDS: frozenset[str] = frozenset(
    {
        # Runtime toggles
        "monitorEnabled",
        "status",
        "nextScanAt",
        "nextNotificationAt",
        "lastManualRefreshAt",
        "lastError",
        "consecutiveScanTimeouts",
        "shopName",
        # Deprecated fields
        "queryRecentDays",
        # Identity-only (per docs/monitor-config-sync.md §3.1)
        "advertiserId",
        # Transport secrets (never wire-released)
        "token",
        "cookie",
        "serviceUrl",
        # Runtime structures (per docs/monitor-config-sync.md §3.1: "不得下发/覆盖")
        "requestTemplate",
        "campaigns",
        "campaignsComplete",
        "pendingAutoCloseFailures",
        "shopCredentials",
        "boundAdTab",
        "logs",
    }
)

#: All allowed top-level settings field names. Any field outside this
#: set is rejected as unknown. The set also drives the "missing field"
#: completeness check for global-default payloads.
_ALLOWED_SETTINGS_FIELDS: frozenset[str] = frozenset(
    {
        "globalThreshold",
        "autoCloseSkuOrderLimit",
        "autoCloseMaxSkuOrders",
        "autoCloseSingleConversionCostThreshold",
        "autoCloseCampaignWhitelist",
        "closeQueryRecentDays",
        "reportQueryRecentDays",
        "scanIntervalMinSeconds",
        "scanIntervalMaxSeconds",
        "notificationIntervalMinSeconds",
        "notificationIntervalMaxSeconds",
        "notifyAutoCloseSuccessEnabled",
        "notifyAutoCloseFailureRepeatEnabled",
        "feishuWebhookUrl",
        "feishuSigningSecret",
        "feishuAppId",
        "feishuAppSecret",
        "feishuRecipientIdentifiers",
        "autoCloseEnabled",
        "campaignThresholds",
    }
)

#: Fields that explicitly allow ``null`` in the override payload to
#: represent "disable this threshold for the shop". Every other field
#: rejects explicit null at validation time.
_NULLABLE_DISABLE_FIELDS: frozenset[str] = frozenset(
    {
        "autoCloseMaxSkuOrders",
        "autoCloseSingleConversionCostThreshold",
    }
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConfigContractError(ValueError):
    """A wire payload violates the documented monitor-config contract."""


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------


def _finite_decimal(value: Any, *, path: str) -> Decimal:
    """Validate and convert to a finite ``Decimal``.

    Booleans are explicitly rejected (Python treats ``bool`` as a
    subclass of ``int``; accepting it would let ``True`` masquerade as
    ``1``).
    """
    if isinstance(value, bool):
        raise ConfigContractError(f"{path} must be a number")
    if not isinstance(value, (int, float, str, Decimal)):
        raise ConfigContractError(f"{path} must be a number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConfigContractError(f"{path} must be a number") from exc
    if not result.is_finite():
        raise ConfigContractError(f"{path} must be a finite number")
    return result


def _non_negative_integer(value: Any, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigContractError(f"{path} must be a non-negative integer")
    if value < 0:
        raise ConfigContractError(f"{path} must be >= 0")
    return value


def _positive_integer(value: Any, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigContractError(f"{path} must be a positive integer")
    if value < MIN_QUERY_RECENT_DAYS:
        raise ConfigContractError(
            f"{path} must be >= {MIN_QUERY_RECENT_DAYS}"
        )
    return value


def _finite_positive_number(value: Any, *, path: str) -> Decimal:
    result = _finite_decimal(value, path=path)
    if result <= 0:
        raise ConfigContractError(f"{path} must be > 0")
    return result


def _finite_non_negative_number(value: Any, *, path: str) -> Decimal:
    result = _finite_decimal(value, path=path)
    if result < 0:
        raise ConfigContractError(f"{path} must be >= 0")
    return result


def _delay_seconds(value: Any, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigContractError(f"{path} must be an integer")
    if value < MIN_CHROME_ALARM_DELAY_SECONDS:
        raise ConfigContractError(
            f"{path} must be >= {MIN_CHROME_ALARM_DELAY_SECONDS}"
        )
    return value


#: Canonical decimal wire identifiers (docs/monitor-config-sync.md §5.2 /
#: §6.1). Mirrors the plugin's
#: ``DECIMAL_IDENTIFIER = /^\d+$/`` (``remote-config.ts``). Written as an
#: explicit ASCII character class rather than ``\d`` because Python's
#: ``\d`` also matches non-ASCII digits (fullwidth ``１８``, Arabic-Indic
#: ``١٨``) which the plugin's ASCII-only regex rejects.
_DECIMAL_IDENTIFIER = re.compile(r"[0-9]+")


def _decimal_identifier(value: Any, *, path: str) -> str:
    r"""Validate a canonical decimal wire identifier.

    These fields (``shopPk``, delivery ``version``, ``force.version``,
    ``force.target.version``) cross the plugin contract as strings so the
    plugin can order them with ``BigInt`` without JS ``Number`` precision
    loss. The plugin therefore requires ``/^\d+$/`` and this side must not
    be laxer — a value this layer accepts but the plugin rejects is a
    silently broken delivery.

    Accepted: a non-empty run of ASCII digits (``"0"``, ``"18"``,
    ``"9007199254740993"``). Rejected: empty strings, surrounding or
    embedded whitespace, ``+``/``-`` signs, decimal points, exponents,
    digit separators, ``0x``/``0b`` prefixes, and non-ASCII digits.

    Leading zeros are accepted (``"007"``) to stay exactly at parity with
    the plugin's ``/^\d+$/`` and with ``BigInt("007") === 7n``: the
    version ordering contract is numeric, so ``"007"`` and ``"7"`` are
    the same version on both sides. ``"0"`` is likewise accepted because
    the plugin accepts it and it is the documented "no shop assigned yet"
    sentinel (:func:`parse_envelope`'s ``shop_pk`` default).

    ``bool`` and ``int`` are rejected: Python's ``bool`` is an ``int``
    subclass, and coercing either to ``str`` would let ``True`` become
    ``"1"`` — a value the plugin never sees from a server.
    """
    if not isinstance(value, str) or _DECIMAL_IDENTIFIER.fullmatch(value) is None:
        raise ConfigContractError(
            f"{path} must be a canonical decimal identifier string "
            "(one or more ASCII digits, no sign/whitespace/separator)"
        )
    return value


def _validate_source_versions(value: Any, *, path: str) -> None:
    """Validate a delivery block's ``sourceVersions`` map.

    Shared by the wire-shape path (:func:`_validate_delivery_block`) and the
    typed path (:class:`Current`) so both enforce one rule:

    - the value must be a mapping (the plugin's ``requireObject``);
    - every key must be a string;
    - every entry is either ``None`` (that source has no revision yet) or a
      **non-empty** string — the plugin applies ``requireNonEmptyString`` to
      each entry, so ``""`` is a protocol violation and ``null`` is the only
      way to say "no revision".
    """
    if not isinstance(value, Mapping):
        raise ConfigContractError(f"{path} must be an object")
    for key, entry in value.items():
        if not isinstance(key, str):
            raise ConfigContractError(f"{path} keys must be strings")
        if entry is None:
            continue
        if not isinstance(entry, str):
            raise ConfigContractError(
                f"{path}.{key} must be a string or null"
            )
        if entry == "":
            raise ConfigContractError(
                f"{path}.{key} must be a non-empty string or null"
            )


def _validate_content_hash(value: Any, *, path: str) -> None:
    """Validate a delivery block's ``contentHash``.

    Shared by the wire-shape path and :class:`Current`. The plugin applies
    ``requireNonEmptyString``; it is deliberately *not* a decimal identifier,
    so any non-empty string is accepted and no ``str()`` coercion happens
    here.
    """
    if not isinstance(value, str) or value == "":
        raise ConfigContractError(f"{path} must be a non-empty string")


def _validate_current_fields(
    version: Any,
    source_versions: Any,
    content_hash: Any,
    *,
    prefix: str,
) -> None:
    """Enforce the delivery-block contract on one block's scalar fields.

    Single implementation shared by the typed dataclasses
    (:class:`Current`, :class:`DeliveryEnvelope.Force`) and the coercion
    boundaries (:func:`_coerce_current_block`, :func:`_coerce_force`), so a
    typed block cannot bypass the rules the mapping path enforces — whether
    it was constructed normally or assembled with ``object.__setattr__`` on
    a frozen dataclass.
    """
    _decimal_identifier(version, path=f"{prefix}.version")
    _validate_source_versions(source_versions, path=f"{prefix}.sourceVersions")
    _validate_content_hash(content_hash, path=f"{prefix}.contentHash")


def _string_tuple(values: Any, *, path: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, list):
        raise ConfigContractError(f"{path} must be an array of strings")
    result: list[str] = []
    for index, item in enumerate(values):
        if not isinstance(item, str):
            raise ConfigContractError(f"{path}[{index}] must be a string")
        result.append(item)
    return tuple(result)


# ---------------------------------------------------------------------------
# Per-campaign overrides
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CampaignRuleOverrides:
    """Per-campaign rule overrides (canonical object form).

    Each field defaults to ``None`` meaning "not overridden; inherit the
    global default for this dimension". A present value uses that value
    instead. The wire form serializes only the present overrides.
    """

    spend_threshold: Optional[Decimal] = None
    sku_order_limit: Optional[int] = None
    single_conversion_cost_threshold: Optional[Decimal] = None


def normalize_campaign_thresholds(
    payload: Any,
) -> Mapping[str, CampaignRuleOverrides]:
    """Normalize wire ``campaignThresholds`` to the canonical object form.

    Accepts:

    - Empty object ``{}`` — returns ``{}``.
    - Object form ``{campaignId: {spendThreshold, skuOrderLimit,
      singleConversionCostThreshold}}`` — preserved as-is.
    - Numeric shorthand ``{campaignId: N}`` — legacy backup form;
      normalized to ``{campaignId: CampaignRuleOverrides(spend_threshold=N)}``.

    Rejects non-object payloads, non-string / empty keys, unknown fields,
    non-finite or non-positive thresholds, and non-integer / negative
    ``skuOrderLimit``.

    The returned mapping is a ``MappingProxyType`` so consumers cannot
    accidentally mutate the normalized view.
    """
    if not isinstance(payload, Mapping):
        raise ConfigContractError("campaignThresholds must be an object")
    result: dict[str, CampaignRuleOverrides] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            raise ConfigContractError(
                "campaignThresholds key must be a string campaignId"
            )
        if key == "":
            raise ConfigContractError(
                "campaignThresholds key must be a non-empty campaignId"
            )
        result[key] = _normalize_one_campaign_threshold(
            value, path=f"campaignThresholds.{key}"
        )
    return MappingProxyType(result)


def _normalize_one_campaign_threshold(
    value: Any, *, path: str
) -> CampaignRuleOverrides:
    # Numeric shorthand: a bare positive number coerces to {spendThreshold: N}.
    # Booleans are excluded because ``bool`` is a subclass of ``int``.
    if isinstance(value, bool):
        raise ConfigContractError(
            f"{path} must be an object or positive number"
        )
    if isinstance(value, (int, float, str, Decimal)):
        spend = _finite_positive_number(value, path=f"{path}.spendThreshold")
        return CampaignRuleOverrides(spend_threshold=spend)
    if not isinstance(value, Mapping):
        raise ConfigContractError(
            f"{path} must be an object or positive number"
        )
    # Explicit cast for type checkers: after the two early returns above,
    # ``value`` is statically a ``Mapping`` but pyright loses the narrowing
    # across the isinstance union at the top of the function.
    mapping_value: Mapping[str, Any] = cast(Mapping[str, Any], value)
    allowed = {
        "spendThreshold",
        "skuOrderLimit",
        "singleConversionCostThreshold",
    }
    unknown = set(mapping_value) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ConfigContractError(f"{path} has unknown field(s): {names}")
    spend_threshold: Optional[Decimal] = None
    sku_order_limit: Optional[int] = None
    single_conversion_cost_threshold: Optional[Decimal] = None
    if "spendThreshold" in mapping_value:
        spend_threshold = _finite_positive_number(
            mapping_value["spendThreshold"], path=f"{path}.spendThreshold"
        )
    if "skuOrderLimit" in mapping_value:
        sku_order_limit = _non_negative_integer(
            mapping_value["skuOrderLimit"], path=f"{path}.skuOrderLimit"
        )
    if "singleConversionCostThreshold" in mapping_value:
        single_conversion_cost_threshold = _finite_positive_number(
            mapping_value["singleConversionCostThreshold"],
            path=f"{path}.singleConversionCostThreshold",
        )
    return CampaignRuleOverrides(
        spend_threshold=spend_threshold,
        sku_order_limit=sku_order_limit,
        single_conversion_cost_threshold=single_conversion_cost_threshold,
    )


# ---------------------------------------------------------------------------
# Settings dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MonitorBusinessSettings:
    """Complete, immutable monitor business settings (post-merge).

    The dataclass carries the merged result of a global default and a
    shop override. ``seller_id`` is only set after merge (the global
    default itself has no seller identity). All array fields are stored
    as tuples for immutability, and the campaign-thresholds view is a
    ``MappingProxyType`` to prevent accidental post-construction edits.
    """

    global_threshold: Decimal
    auto_close_sku_order_limit: int
    auto_close_max_sku_orders: Optional[int]
    auto_close_single_conversion_cost_threshold: Optional[Decimal]
    auto_close_campaign_whitelist: tuple[str, ...]
    close_query_recent_days: int
    report_query_recent_days: int
    scan_interval_min_seconds: int
    scan_interval_max_seconds: int
    notification_interval_min_seconds: int
    notification_interval_max_seconds: int
    notify_auto_close_success_enabled: bool
    notify_auto_close_failure_repeat_enabled: bool
    feishu_webhook_url: str
    feishu_signing_secret: str
    feishu_app_id: str
    feishu_app_secret: str
    feishu_recipient_identifiers: tuple[str, ...]
    auto_close_enabled: bool
    campaign_thresholds: Mapping[str, CampaignRuleOverrides] = field(
        default_factory=dict
    )
    seller_id: Optional[str] = None


# Wire-name ↔ dataclass-attribute mapping. Keeping it explicit avoids
# reflection and makes the wire contract easy to audit.
_WIRE_TO_ATTR: Mapping[str, str] = {
    "globalThreshold": "global_threshold",
    "autoCloseSkuOrderLimit": "auto_close_sku_order_limit",
    "autoCloseMaxSkuOrders": "auto_close_max_sku_orders",
    "autoCloseSingleConversionCostThreshold": "auto_close_single_conversion_cost_threshold",
    "autoCloseCampaignWhitelist": "auto_close_campaign_whitelist",
    "closeQueryRecentDays": "close_query_recent_days",
    "reportQueryRecentDays": "report_query_recent_days",
    "scanIntervalMinSeconds": "scan_interval_min_seconds",
    "scanIntervalMaxSeconds": "scan_interval_max_seconds",
    "notificationIntervalMinSeconds": "notification_interval_min_seconds",
    "notificationIntervalMaxSeconds": "notification_interval_max_seconds",
    "notifyAutoCloseSuccessEnabled": "notify_auto_close_success_enabled",
    "notifyAutoCloseFailureRepeatEnabled": "notify_auto_close_failure_repeat_enabled",
    "feishuWebhookUrl": "feishu_webhook_url",
    "feishuSigningSecret": "feishu_signing_secret",
    "feishuAppId": "feishu_app_id",
    "feishuAppSecret": "feishu_app_secret",
    "feishuRecipientIdentifiers": "feishu_recipient_identifiers",
    "autoCloseEnabled": "auto_close_enabled",
    "campaignThresholds": "campaign_thresholds",
}


def _validate_settings_common(
    payload: Any, *, path: str
) -> dict[str, Any]:
    """Validate a settings dict (object form) and return normalized dict.

    Returns a dict keyed by wire name (not Python attribute name) so
    that :func:`merge_business_settings` can map wire names back to
    attribute names via :data:`_WIRE_TO_ATTR`. The ``campaignThresholds``
    entry holds a normalized ``MappingProxyType`` view.
    """
    if not isinstance(payload, Mapping):
        raise ConfigContractError(f"{path} must be an object")
    forbidden_present = set(payload) & FORBIDDEN_WIRE_FIELDS
    if forbidden_present:
        names = ", ".join(sorted(forbidden_present))
        raise ConfigContractError(f"{path} contains forbidden field(s): {names}")
    unknown = set(payload) - _ALLOWED_SETTINGS_FIELDS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ConfigContractError(f"{path} has unknown field(s): {names}")

    result: dict[str, Any] = {}
    for wire_name, value in payload.items():
        if wire_name == "globalThreshold":
            result[wire_name] = _finite_non_negative_number(
                value, path=f"{path}.globalThreshold"
            )
        elif wire_name == "autoCloseSkuOrderLimit":
            result[wire_name] = _non_negative_integer(
                value, path=f"{path}.autoCloseSkuOrderLimit"
            )
        elif wire_name == "autoCloseMaxSkuOrders":
            if value is None:
                result[wire_name] = None
            else:
                result[wire_name] = _non_negative_integer(
                    value, path=f"{path}.autoCloseMaxSkuOrders"
                )
        elif wire_name == "autoCloseSingleConversionCostThreshold":
            if value is None:
                result[wire_name] = None
            else:
                result[wire_name] = _finite_non_negative_number(
                    value,
                    path=f"{path}.autoCloseSingleConversionCostThreshold",
                )
        elif wire_name == "autoCloseCampaignWhitelist":
            result[wire_name] = _string_tuple(
                value, path=f"{path}.autoCloseCampaignWhitelist"
            )
        elif wire_name == "closeQueryRecentDays":
            result[wire_name] = _positive_integer(
                value, path=f"{path}.closeQueryRecentDays"
            )
        elif wire_name == "reportQueryRecentDays":
            result[wire_name] = _positive_integer(
                value, path=f"{path}.reportQueryRecentDays"
            )
        elif wire_name == "scanIntervalMinSeconds":
            result[wire_name] = _delay_seconds(
                value, path=f"{path}.scanIntervalMinSeconds"
            )
        elif wire_name == "scanIntervalMaxSeconds":
            result[wire_name] = _delay_seconds(
                value, path=f"{path}.scanIntervalMaxSeconds"
            )
        elif wire_name == "notificationIntervalMinSeconds":
            result[wire_name] = _delay_seconds(
                value, path=f"{path}.notificationIntervalMinSeconds"
            )
        elif wire_name == "notificationIntervalMaxSeconds":
            result[wire_name] = _delay_seconds(
                value, path=f"{path}.notificationIntervalMaxSeconds"
            )
        elif wire_name == "notifyAutoCloseSuccessEnabled":
            if not isinstance(value, bool):
                raise ConfigContractError(
                    f"{path}.notifyAutoCloseSuccessEnabled must be boolean"
                )
            result[wire_name] = value
        elif wire_name == "notifyAutoCloseFailureRepeatEnabled":
            if not isinstance(value, bool):
                raise ConfigContractError(
                    f"{path}.notifyAutoCloseFailureRepeatEnabled must be boolean"
                )
            result[wire_name] = value
        elif wire_name == "feishuWebhookUrl":
            if not isinstance(value, str):
                raise ConfigContractError(
                    f"{path}.feishuWebhookUrl must be a string"
                )
            result[wire_name] = value
        elif wire_name == "feishuSigningSecret":
            if not isinstance(value, str):
                raise ConfigContractError(
                    f"{path}.feishuSigningSecret must be a string"
                )
            result[wire_name] = value
        elif wire_name == "feishuAppId":
            if not isinstance(value, str):
                raise ConfigContractError(
                    f"{path}.feishuAppId must be a string"
                )
            result[wire_name] = value
        elif wire_name == "feishuAppSecret":
            if not isinstance(value, str):
                raise ConfigContractError(
                    f"{path}.feishuAppSecret must be a string"
                )
            result[wire_name] = value
        elif wire_name == "feishuRecipientIdentifiers":
            result[wire_name] = _string_tuple(
                value, path=f"{path}.feishuRecipientIdentifiers"
            )
        elif wire_name == "autoCloseEnabled":
            if not isinstance(value, bool):
                raise ConfigContractError(
                    f"{path}.autoCloseEnabled must be boolean"
                )
            result[wire_name] = value
        elif wire_name == "campaignThresholds":
            result[wire_name] = normalize_campaign_thresholds(value)

    if (
        "scanIntervalMinSeconds" in result
        and "scanIntervalMaxSeconds" in result
        and result["scanIntervalMaxSeconds"] < result["scanIntervalMinSeconds"]
    ):
        raise ConfigContractError(
            "settings.scanIntervalMaxSeconds must be >= scanIntervalMinSeconds"
        )
    if (
        "notificationIntervalMinSeconds" in result
        and "notificationIntervalMaxSeconds" in result
        and result["notificationIntervalMaxSeconds"]
        < result["notificationIntervalMinSeconds"]
    ):
        raise ConfigContractError(
            "settings.notificationIntervalMaxSeconds must be "
            ">= notificationIntervalMinSeconds"
        )
    return result


def validate_complete_business_settings(
    payload: Any,
) -> MonitorBusinessSettings:
    """Validate a complete settings payload (all fields required).

    A complete payload is the canonical global-default form. Every
    allowed field must be present and non-null (with the exception of
    the two ``_NULLABLE_DISABLE_FIELDS`` that explicitly accept ``None``
    for "disable this threshold"). Unknown fields and forbidden
    fields are rejected.

    Raises :class:`ConfigContractError` for any contract failure and
    :class:`TypeError` when ``payload`` is not a JSON object.
    """
    if not isinstance(payload, Mapping):
        raise TypeError("settings payload must be an object")
    missing = _ALLOWED_SETTINGS_FIELDS - set(payload)
    if missing:
        names = ", ".join(sorted(missing))
        raise ConfigContractError(f"settings missing field(s): {names}")
    normalized = _validate_settings_common(payload, path="settings")
    kwargs: dict[str, Any] = {}
    for wire_name, attr_name in _WIRE_TO_ATTR.items():
        kwargs[attr_name] = normalized[wire_name]
    return MonitorBusinessSettings(**kwargs)


def validate_settings_override(payload: Any) -> dict[str, Any]:
    """Validate a partial shop-override payload.

    Returns a normalized dict keyed by wire name. Missing fields are not
    represented at all (caller treats absent as "inherit global"). Explicit
    ``None`` is allowed only for fields in
    :data:`_NULLABLE_DISABLE_FIELDS`; everywhere else ``None`` is
    rejected so that "absent" and "explicit null" cannot be confused.
    """
    if not isinstance(payload, Mapping):
        raise TypeError("override payload must be an object")
    normalized = _validate_settings_common(payload, path="settingsOverride")
    # Reject explicit null on non-nullable settings.
    for wire_name, value in normalized.items():
        if value is None and wire_name not in _NULLABLE_DISABLE_FIELDS:
            raise ConfigContractError(
                f"settingsOverride.{wire_name} must not be null; omit the field to inherit"
            )
    return normalized


def validate_business_settings(
    payload: Any, *, require_complete: bool = False
) -> Union[MonitorBusinessSettings, dict[str, Any]]:
    """Single dispatcher around the two strict validators.

    ``require_complete=True`` → :func:`validate_complete_business_settings`.
    ``require_complete=False`` → :func:`validate_settings_override`.

    Provided so callers that branch on a single flag (e.g. a CLI entry)
    do not need to know both validator names.
    """
    if require_complete:
        return validate_complete_business_settings(payload)
    return validate_settings_override(payload)


# ---------------------------------------------------------------------------
# Merge layer
# ---------------------------------------------------------------------------


def merge_business_settings(
    *,
    seller_id: str,
    global_default: Mapping[str, Any],
    shop_override: Optional[Mapping[str, Any]] = None,
) -> MonitorBusinessSettings:
    """Compose the global default with a shop override.

    Semantics:

    - ``sellerId`` must be a non-empty string.
    - ``globalDefault`` is validated as a complete payload.
    - ``shopOverride`` must be either ``None`` or a ``Mapping``; an
      empty mapping (``{}``) is the canonical "inherit everything"
      sentinel. Every other non-``Mapping`` value (e.g. ``[]``, ``""``,
      a bare scalar) is rejected — this prevents falsy-non-mapping
      values from silently being treated as "no override".
    - Missing fields in the override inherit from the global default.
    - Present fields override the global value (must be valid; ``None``
    is allowed only for fields in :data:`_NULLABLE_DISABLE_FIELDS`).
    - Arrays are whole-replaced, never concatenated.
    - ``campaignThresholds`` is whole-object replaced (per-campaign
      overrides are shop-only by design).
    - Cross-layer min/max invariants are validated **after**
      composition, so an override that touches only ``min`` or only
      ``max`` cannot produce an invalid complete payload.
    - Order of validation: seller id → global → override type →
      override content → composition → cross-field invariants.
    """
    if not isinstance(seller_id, str) or seller_id == "":
        raise ConfigContractError("sellerId must be a non-empty string")
    if not isinstance(global_default, Mapping):
        raise ConfigContractError(
            f"globalDefault must be a mapping, got "
            f"{type(global_default).__name__}"
        )
    if shop_override is not None and not isinstance(shop_override, Mapping):
        raise ConfigContractError(
            f"shopOverride must be a mapping or None, got "
            f"{type(shop_override).__name__}"
        )
    try:
        base = validate_complete_business_settings(global_default)
    except ConfigContractError as exc:
        raise ConfigContractError(f"globalDefault invalid: {exc}") from exc
    if shop_override is None or len(shop_override) == 0:
        composed = replace(
            base,
            seller_id=seller_id,
            campaign_thresholds=MappingProxyType(dict(base.campaign_thresholds)),
        )
        _validate_cross_field_invariants(composed)
        return composed
    try:
        override = validate_settings_override(shop_override)
    except ConfigContractError as exc:
        raise ConfigContractError(f"shopOverride invalid: {exc}") from exc

    composed_kwargs: dict[str, Any] = {"seller_id": seller_id}
    for wire_name, attr_name in _WIRE_TO_ATTR.items():
        if attr_name == "campaign_thresholds":
            if "campaignThresholds" in override:
                composed_kwargs[attr_name] = MappingProxyType(
                    dict(override["campaignThresholds"])
                )
            else:
                composed_kwargs[attr_name] = MappingProxyType(
                    dict(base.campaign_thresholds)
                )
            continue
        if wire_name in override:
            composed_kwargs[attr_name] = override[wire_name]
        else:
            composed_kwargs[attr_name] = getattr(base, attr_name)
    composed = MonitorBusinessSettings(**composed_kwargs)
    _validate_cross_field_invariants(composed)
    return composed


def _validate_cross_field_invariants(settings: MonitorBusinessSettings) -> None:
    """Validate cross-field invariants on the merged settings.

    Each source payload validates its own min/max internally; this
    stage guarantees that composing a partial override that touches only
    one of ``min`` / ``max`` still produces a complete delivery whose
    min <= max. Without it, ``global {min=30, max=60}`` combined with
    ``override {max=20}`` would silently produce ``min=30, max=20``.
    """
    if settings.scan_interval_max_seconds < settings.scan_interval_min_seconds:
        raise ConfigContractError(
            "settings.scanIntervalMaxSeconds must be >= scanIntervalMinSeconds "
            "after composition"
        )
    if (
        settings.notification_interval_max_seconds
        < settings.notification_interval_min_seconds
    ):
        raise ConfigContractError(
            "settings.notificationIntervalMaxSeconds must be "
            ">= notificationIntervalMinSeconds after composition"
        )


# ---------------------------------------------------------------------------
# Round-trip serialization
# ---------------------------------------------------------------------------


def config_to_payload(settings: MonitorBusinessSettings) -> dict[str, Any]:
    """Serialize a ``MonitorBusinessSettings`` back to wire payload shape.

    Decimals serialize to ``str`` (matching
    docs/monitor-config-sync.md §3.1's preference
    for stable precision). Tuples serialize to lists. The normalized
    ``campaignThresholds`` view serializes back to object form.
    """
    payload: dict[str, Any] = {}
    for wire_name, attr_name in _WIRE_TO_ATTR.items():
        value = getattr(settings, attr_name)
        if attr_name == "campaign_thresholds":
            payload[wire_name] = _serialize_campaign_thresholds(value)
        elif isinstance(value, tuple):
            payload[wire_name] = list(value)
        elif isinstance(value, Decimal):
            payload[wire_name] = str(value)
        else:
            payload[wire_name] = value
    return payload


def _serialize_campaign_thresholds(
    view: Mapping[str, CampaignRuleOverrides],
) -> dict[str, dict[str, Any]]:
    serialized: dict[str, dict[str, Any]] = {}
    for campaign_id, overrides in view.items():
        entry: dict[str, Any] = {}
        if overrides.spend_threshold is not None:
            entry["spendThreshold"] = str(overrides.spend_threshold)
        if overrides.sku_order_limit is not None:
            entry["skuOrderLimit"] = overrides.sku_order_limit
        if overrides.single_conversion_cost_threshold is not None:
            entry["singleConversionCostThreshold"] = str(
                overrides.single_conversion_cost_threshold
            )
        serialized[campaign_id] = entry
    return serialized


# ---------------------------------------------------------------------------
# Delivery envelope (response shape, from docs/monitor-config-sync.md §6.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Current:
    """Typed view of one ``current`` (or ``force.target``) delivery block.

    ``payload`` is a fully-typed :class:`MonitorBusinessSettings`. The
    wire-shape ``{"sellerId","schemaVersion","settings",...}`` is produced
    by :attr:`DeliveryEnvelope.payload`, not stored on this object, so the
    contract layer keeps one canonical typed source.

    ``version`` is validated at construction time so a typed instance
    cannot carry a version the wire-shape path would have rejected —
    typed and mapping construction stay at parity.

    ``source_versions`` and ``content_hash`` are validated by the same
    :func:`_source_versions` / :func:`_content_hash` helpers the mapping
    path uses, so a typed :class:`Current` cannot bypass the delivery-block
    contract (it is also used as ``force.target``).
    """

    version: str
    source_versions: Mapping[str, Optional[str]]
    content_hash: str
    payload: MonitorBusinessSettings

    def __post_init__(self) -> None:
        _validate_current_fields(
            self.version,
            self.source_versions,
            self.content_hash,
            prefix="current",
        )


# Allowed keys for a complete delivery block (used by both ``current``
# and ``force.target``). The wire format is identical for both, per
# docs/monitor-config-sync.md §6.1 ("target 与 current 相同结构的完整快照").
_DELIVERY_BLOCK_ALLOWED_KEYS: frozenset[str] = frozenset(
    {"version", "sourceVersions", "contentHash", "payload"}
)

# Allowed keys for the outer ``force`` envelope: just ``version`` and
# ``target`` (docs/monitor-config-sync.md §6.1 force-block). Any other key is unknown.
_FORCE_BLOCK_ALLOWED_KEYS: frozenset[str] = frozenset({"version", "target"})


class DeliveryEnvelope:
    """Wire envelope for one monitor delivery snapshot.

    The constructor accepts wire-form kwargs (``schemaVersion``,
    ``sellerId``, ``shopPk``, ``current``, ``force``) and rejects any
    unknown outer key with :class:`ConfigContractError`. The instance
    exposes snake_case accessors (``schema_version``, ``seller_id``,
    ``current_version``, ``source_versions``, ``content_hash``,
    ``payload``) so callers do not have to reach through the wire
    shape for the common case.

    The ``current`` constructor kwarg may be either a wire-shape ``Mapping``
    (validated and wrapped into :class:`Current`) or a pre-built
    :class:`Current` instance.

    When ``force`` is provided, it is the document-correct shape from
    docs/monitor-config-sync.md §6.1::

        {"version": "...", "target": <complete delivery block>}

    where ``force.version == force.target.version`` and the inner
    ``target`` has the same ``{version, sourceVersions, contentHash,
    payload}`` shape as ``current``. Force with any other shape (the
    old sibling ``sourceVersions``/``contentHash``/``payload`` block)
    is rejected as malformed.
    """

    # Class-level annotations make slot attributes visible to static
    # type checkers (pyright, etc.) without depending on dataclass
    # auto-``__init__``.
    schema_version: int
    seller_id: str
    shop_pk: str
    current: Current
    force: Optional["DeliveryEnvelope.Force"]

    __slots__ = ("schema_version", "seller_id", "shop_pk", "current", "force")

    @dataclass(frozen=True, slots=True)
    class Force:
        """Force-delivery block (docs/monitor-config-sync.md §6.1).

        Field names mirror the wire format. ``target`` is a complete
        delivery block with the same shape as :class:`Current`; the
        invariant ``version == target.version`` is enforced by the
        surrounding :class:`DeliveryEnvelope` constructor (and by
        :func:`parse_envelope`).
        """

        version: str
        target: Current

        def __post_init__(self) -> None:
            # ``force.version`` and ``force.target.version`` are two separate
            # wire fields the plugin validates independently, so a
            # non-decimal value cannot be excused by the other one being
            # equal to it.
            _decimal_identifier(self.version, path="force.version")
            _validate_current_fields(
                self.target.version,
                self.target.source_versions,
                self.target.content_hash,
                prefix="force.target",
            )
            # Defensive check; the surrounding constructor already
            # enforces this, but the dataclass contract should stand
            # on its own so callers cannot construct an inconsistent
            # Force directly.
            if self.version != self.target.version:
                raise ConfigContractError(
                    "force.version must equal force.target.version "
                    f"(got {self.version!r} vs {self.target.version!r})"
                )

    def __init__(
        self,
        *,
        schemaVersion: int,
        sellerId: str,
        shopPk: str,
        current: Any,
        force: Optional[Union["DeliveryEnvelope.Force", WireShapeForce]] = None,
        **extra: Any,
    ) -> None:
        if extra:
            names = ", ".join(sorted(extra))
            raise ConfigContractError(f"unknown envelope field(s): {names}")
        if not isinstance(schemaVersion, int) or isinstance(schemaVersion, bool):
            raise ConfigContractError("schemaVersion must be an integer")
        if schemaVersion != WIRE_SCHEMA_VERSION:
            raise ConfigContractError(
                f"schemaVersion must equal {WIRE_SCHEMA_VERSION}"
            )
        if not isinstance(sellerId, str) or sellerId == "":
            raise ConfigContractError("sellerId must be a non-empty string")
        _decimal_identifier(shopPk, path="shopPk")
        normalized_current = _coerce_current_block(current)
        typed_force = _coerce_force(force, default_seller_id=sellerId)
        # Cross-shop snapshot guard. The wire payload may carry a
        # ``sellerId`` inside ``payload`` (docs/monitor-config-sync.md §6.1 response
        # shape) and a typed :class:`Current` may carry an explicit
        # ``payload.seller_id``. Both paths converge after coercion:
        # ``_extract_typed_settings`` already promotes the wire
        # ``payload.sellerId`` onto ``MonitorBusinessSettings.seller_id``
        # when present, so a single check on the typed view covers the
        # raw wire form, the typed :class:`Current` form, and every
        # ``seller_id`` that survived coercion.
        #
        # ``seller_id is None`` is the canonical global-default state
        # (no shop yet) and is accepted; only a non-``None`` identity
        # mismatch is a cross-shop snapshot.
        current_seller_id = normalized_current.payload.seller_id
        if (
            current_seller_id is not None
            and current_seller_id != sellerId
        ):
            raise ConfigContractError(
                f"envelope sellerId ({sellerId!r}) must equal "
                f"current.payload.sellerId ({current_seller_id!r}); "
                "cross-shop snapshots are forbidden"
            )
        if typed_force is not None:
            force_seller_id = typed_force.target.payload.seller_id
            if force_seller_id is not None and force_seller_id != sellerId:
                raise ConfigContractError(
                    f"envelope sellerId ({sellerId!r}) must equal "
                    f"force.target.payload.sellerId "
                    f"({force_seller_id!r}); cross-shop force snapshots "
                    "are forbidden"
                )
        object.__setattr__(self, "schema_version", schemaVersion)
        object.__setattr__(self, "seller_id", sellerId)
        object.__setattr__(self, "shop_pk", shopPk)
        object.__setattr__(self, "current", normalized_current)
        object.__setattr__(self, "force", typed_force)

    # Wire-name aliases (read-only) -------------------------------------
    @property
    def schemaVersion(self) -> int:
        return self.schema_version

    @property
    def sellerId(self) -> str:
        return self.seller_id

    @property
    def shopPk(self) -> str:
        return self.shop_pk

    @property
    def current_version(self) -> str:
        return self.current.version

    @property
    def source_versions(self) -> Mapping[str, Optional[str]]:
        return self.current.source_versions

    @property
    def content_hash(self) -> str:
        return self.current.content_hash

    @property
    def payload(self) -> Mapping[str, Any]:
        """Wire-shape payload (docs/monitor-config-sync.md §6.1 ``payload`` block).

        Returns the nested dict containing ``sellerId``,
        ``schemaVersion``, ``settings``, and the serialized campaign
        thresholds. Callers that want the typed settings should read
        :attr:`current` or :attr:`force.target.payload`.
        """
        settings = self.current.payload
        return {
            "sellerId": settings.seller_id or "",
            "schemaVersion": WIRE_SCHEMA_VERSION,
            "settings": config_to_payload(settings),
        }


_DELIVERY_BLOCK_ALLOWED_KEYS: frozenset[str] = frozenset(
    {"version", "sourceVersions", "contentHash", "payload"}
)

_FORCE_BLOCK_ALLOWED_KEYS: frozenset[str] = frozenset({"version", "target"})


def _validate_delivery_block(block: Mapping[str, Any], *, shape: str) -> None:
    """Validate a complete delivery block (current or force.target).

    The block must have exactly ``{version, sourceVersions,
    contentHash, payload}`` keys. The ``payload.settings`` sub-object
    must not contain any forbidden field.
    """
    unknown = set(block) - _DELIVERY_BLOCK_ALLOWED_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ConfigContractError(f"{shape} has unknown field(s): {names}")
    for key in ("version", "sourceVersions", "contentHash", "payload"):
        if key not in block:
            raise ConfigContractError(f"{shape} missing field: {key}")
    version = block["version"]
    _decimal_identifier(version, path=f"{shape}.version")
    _validate_source_versions(block["sourceVersions"], path=f"{shape}.sourceVersions")
    _validate_content_hash(block["contentHash"], path=f"{shape}.contentHash")
    payload = block["payload"]
    if not isinstance(payload, Mapping):
        raise ConfigContractError(f"{shape}.payload must be an object")
    # The settings inside the payload must NOT contain forbidden fields.
    settings = payload.get("settings")
    if not isinstance(settings, Mapping):
        raise ConfigContractError(f"{shape}.payload.settings must be an object")
    forbidden = set(settings) & FORBIDDEN_WIRE_FIELDS
    if forbidden:
        names = ", ".join(sorted(forbidden))
        raise ConfigContractError(
            f"{shape}.payload.settings contains forbidden field(s): {names}"
        )


def _validate_force_block(force_block: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    """Validate the outer ``force`` envelope shape.

    Must contain exactly ``{version, target}`` keys. The ``target`` is
    a complete delivery block (validated by :func:`_validate_delivery_block`).
    Returns ``(version, target)`` for the caller to coerce.
    """
    unknown = set(force_block) - _FORCE_BLOCK_ALLOWED_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ConfigContractError(
            f"force has unknown field(s): {names}; force must be "
            "{version, target} per docs/monitor-config-sync.md §6.1"
        )
    if "version" not in force_block:
        raise ConfigContractError("force missing field: version")
    if "target" not in force_block:
        raise ConfigContractError("force missing field: target")
    version = force_block["version"]
    version = _decimal_identifier(version, path="force.version")
    target = force_block["target"]
    if not isinstance(target, Mapping):
        raise ConfigContractError("force.target must be an object")
    target_mapping = cast(Mapping[str, Any], target)
    # ``force.target`` is a full delivery block, not a loose mapping. The
    # plugin validates it with ``validateDeliveryBlock(raw.target,
    # "force.target")`` before reading anything out of it, so an unvalidated
    # target would otherwise leak ``KeyError`` (missing keys) or smuggle in
    # unknown fields and non-string values the plugin would reject.
    _validate_delivery_block(target_mapping, shape="force.target")
    return version, target_mapping


def _coerce_current_block(value: Any) -> Current:
    """Validate a current-block wire mapping or accept a typed :class:`Current`.

    A typed block is re-validated rather than passed through: the
    constructor already ran the checks, but a frozen dataclass can still be
    assembled with ``object.__setattr__``, and the envelope must not become
    a way to smuggle an unchecked block onto the wire.
    """
    if isinstance(value, Current):
        _validate_current_fields(
            value.version,
            value.source_versions,
            value.content_hash,
            prefix="current",
        )
        return value
    if not isinstance(value, Mapping):
        raise ConfigContractError("current must be an object or Current")
    _validate_delivery_block(value, shape="current")
    typed_settings = _extract_typed_settings(value["payload"])
    return Current(
        version=value["version"],
        source_versions=dict(value["sourceVersions"]),
        content_hash=value["contentHash"],
        payload=typed_settings,
    )


def parse_envelope(
    settings: MonitorBusinessSettings,
    *,
    current_version: str,
    source_versions: Mapping[str, Optional[str]],
    content_hash: str,
    force: Optional[Union[DeliveryEnvelope.Force, WireShapeForce]] = None,
    shop_pk: str = "0",
) -> DeliveryEnvelope:
    """Build the response envelope for one delivery snapshot.

    The settings come from :func:`merge_business_settings`; this helper
    wraps them in the wire-shape envelope (docs/monitor-config-sync.md §6.1) without
    exposing forbidden runtime fields or leaking empty content.

    The envelope's ``current.payload`` mirrors docs/monitor-config-sync.md §6.1: a
    nested object that includes ``sellerId``, ``schemaVersion``, and the
    serialized business settings under ``settings``. When ``force`` is
    provided, the force block is the document-correct shape::

        {"version": "...", "target": <complete delivery block>}

    where ``force.version == force.target.version`` and the inner
    ``target`` has the same shape as ``current``. Callers may either
    pass a pre-built :class:`DeliveryEnvelope.Force`, or pass a
    wire-shape ``Mapping`` (``{"version": "...", "target": {...}}``)
    which will be validated and coerced.
    """
    current_version = _decimal_identifier(current_version, path="currentVersion")
    if not isinstance(source_versions, Mapping):
        raise ConfigContractError("sourceVersions must be an object")
    if not isinstance(content_hash, str) or content_hash == "":
        raise ConfigContractError("contentHash must be a non-empty string")
    seller_id = settings.seller_id or ""
    current_block = _build_current_block(
        settings,
        version=current_version,
        source_versions=source_versions,
        content_hash=content_hash,
    )
    typed_force = _coerce_force(force, default_seller_id=seller_id)
    return DeliveryEnvelope(
        schemaVersion=WIRE_SCHEMA_VERSION,
        sellerId=seller_id,
        shopPk=shop_pk,
        current=current_block,
        force=typed_force,
    )


def _build_current_block(
    settings: MonitorBusinessSettings,
    *,
    version: str,
    source_versions: Mapping[str, Optional[str]],
    content_hash: str,
) -> dict[str, Any]:
    """Assemble the wire-shape ``current`` block (4 keys + nested payload)."""
    return {
        "version": version,
        "sourceVersions": dict(source_versions),
        "contentHash": content_hash,
        "payload": {
            "sellerId": settings.seller_id or "",
            "schemaVersion": WIRE_SCHEMA_VERSION,
            "settings": config_to_payload(settings),
        },
    }


def _coerce_force(
    value: Any, *, default_seller_id: str
) -> Optional[DeliveryEnvelope.Force]:
    """Validate and convert a wire ``force`` block to :class:`DeliveryEnvelope.Force`.

    Accepts:

    - ``None`` — no force present.
    - A pre-built :class:`DeliveryEnvelope.Force` — passed through (with
      a cross-shop seller-id guard).
    - A wire-shape ``Mapping`` matching ``{version, target}`` — coerced.
    """
    if value is None:
        return None
    if isinstance(value, DeliveryEnvelope.Force):
        # Same re-validation reasoning as :func:`_coerce_current_block`.
        _decimal_identifier(value.version, path="force.version")
        _validate_current_fields(
            value.target.version,
            value.target.source_versions,
            value.target.content_hash,
            prefix="force.target",
        )
        target_settings = value.target.payload
        if (
            target_settings.seller_id is not None
            and target_settings.seller_id != default_seller_id
        ):
            raise ConfigContractError(
                f"force.target.payload.sellerId "
                f"({target_settings.seller_id!r}) must equal envelope "
                f"sellerId ({default_seller_id!r}); cross-shop force "
                "snapshots are forbidden"
            )
        return value
    if not isinstance(value, Mapping):
        raise ConfigContractError(
            "force must be a DeliveryEnvelope.Force or {version, target} object"
        )
    version_str, target_mapping = _validate_force_block(value)
    target_settings = _extract_typed_settings(target_mapping["payload"])
    target = Current(
        version=target_mapping["version"],
        source_versions=dict(target_mapping["sourceVersions"]),
        # ``_validate_force_block`` already guaranteed a non-empty string;
        # calling ``str()`` here would silently accept a numeric value that
        # the plugin rejects, so the validated value is used verbatim.
        content_hash=target_mapping["contentHash"],
        payload=target_settings,
    )
    if version_str != target.version:
        raise ConfigContractError(
            f"force.version ({version_str!r}) must equal "
            f"force.target.version ({target.version!r})"
        )
    if (
        target_settings.seller_id is not None
        and target_settings.seller_id != default_seller_id
    ):
        raise ConfigContractError(
            f"force.target.payload.sellerId ({target_settings.seller_id!r}) "
            f"must equal envelope sellerId ({default_seller_id!r}); "
            "cross-shop force snapshots are forbidden"
        )
    return DeliveryEnvelope.Force(version=version_str, target=target)


def _extract_typed_settings(wire_payload: Any) -> MonitorBusinessSettings:
    """Build typed :class:`MonitorBusinessSettings` from a wire payload mapping."""
    if isinstance(wire_payload, MonitorBusinessSettings):
        return wire_payload
    if not isinstance(wire_payload, Mapping) or not isinstance(
        wire_payload.get("settings"), Mapping
    ):
        raise ConfigContractError(
            "delivery payload must be MonitorBusinessSettings or "
            "{sellerId, schemaVersion, settings, ...}"
        )
    settings = validate_complete_business_settings(wire_payload["settings"])
    wire_seller_id = wire_payload.get("sellerId")
    if isinstance(wire_seller_id, str) and wire_seller_id != "":
        settings = replace(settings, seller_id=wire_seller_id)
    return settings