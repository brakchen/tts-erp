"""Pure contract tests for tts_erp_v2.monitor_config.contracts.

This slice is intentionally DB-free, plugin-free, network-free and
credential-free. It validates and composes the wire-format
business-settings payload and per-campaign override object that the
backend will later deliver to the monitor plugin.

Acceptance cases derive from docs/monitor-config-sync.md §3 and
§6.1 (response shape). Bounds inherit from the maintained plugin
side (``MIN_CHROME_ALARM_DELAY_SECONDS=30``,
``MIN_QUERY_RECENT_DAYS=1``, finite/positive spend and conversion
thresholds, non-negative integer ``skuOrderLimit``).

``shopPk`` and every delivery/force ``version`` are canonical decimal
identifier strings (docs/monitor-config-sync.md §5.2 / §6.1) so the
plugin can order them with ``BigInt`` without JS ``Number`` precision
loss. The accepted/rejected table below is the shared parity fixture
for all three paths.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from tts_erp_v2.monitor_config.contracts import (
    CampaignRuleOverrides,
    ConfigContractError,
    Current,
    DeliveryEnvelope,
    FORBIDDEN_WIRE_FIELDS,
    MIN_CHROME_ALARM_DELAY_SECONDS,
    MIN_QUERY_RECENT_DAYS,
    MonitorBusinessSettings,
    WIRE_SCHEMA_VERSION,
    config_to_payload,
    merge_business_settings,
    normalize_campaign_thresholds,
    parse_envelope,
    validate_business_settings,
    validate_complete_business_settings,
    validate_settings_override,
)

pytestmark = pytest.mark.layer_unit


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

TEST_SELLER_ID = "TEST_SELLER_A"
TEST_SELLER_ID_OTHER = "TEST_SELLER_B"


def _complete_settings(**overrides: Any) -> dict[str, Any]:
    """Return a syntactically valid complete-settings payload."""
    payload: dict[str, Any] = {
        "globalThreshold": 10,
        "autoCloseSkuOrderLimit": 0,
        "autoCloseMaxSkuOrders": 5,
        "autoCloseSingleConversionCostThreshold": 2.5,
        "autoCloseCampaignWhitelist": ["abc", "def"],
        "closeQueryRecentDays": 7,
        "reportQueryRecentDays": 30,
        "scanIntervalMinSeconds": 30,
        "scanIntervalMaxSeconds": 60,
        "notificationIntervalMinSeconds": 900,
        "notificationIntervalMaxSeconds": 1080,
        "notifyAutoCloseSuccessEnabled": True,
        "notifyAutoCloseFailureRepeatEnabled": True,
        "feishuWebhookUrl": "https://open.feishu.cn/hook/TEST_HOOK",
        "feishuSigningSecret": "TEST_SIGNING",
        "feishuAppId": "TEST_APP_ID",
        "feishuAppSecret": "TEST_APP_SECRET",
        "feishuRecipientIdentifiers": ["ou_test_recipient_1"],
        "autoCloseEnabled": True,
        "campaignThresholds": {
            "campaign-x": {
                "spendThreshold": 20,
                "skuOrderLimit": 1,
                "singleConversionCostThreshold": 3.0,
            }
        },
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Wire identity, forbidden fields, and schema version
# ---------------------------------------------------------------------------


def test_wire_schema_version_is_explicit_int_one() -> None:
    assert WIRE_SCHEMA_VERSION == 1
    assert isinstance(WIRE_SCHEMA_VERSION, int)


def test_seller_id_is_string_and_advertiser_id_not_used() -> None:
    settings = MonitorBusinessSettings(
        seller_id=TEST_SELLER_ID,
        global_threshold=Decimal("10"),
        auto_close_sku_order_limit=0,
        auto_close_max_sku_orders=5,
        auto_close_single_conversion_cost_threshold=Decimal("2.5"),
        auto_close_campaign_whitelist=("abc",),
        close_query_recent_days=7,
        report_query_recent_days=30,
        scan_interval_min_seconds=30,
        scan_interval_max_seconds=60,
        notification_interval_min_seconds=900,
        notification_interval_max_seconds=1080,
        notify_auto_close_success_enabled=True,
        notify_auto_close_failure_repeat_enabled=True,
        feishu_webhook_url="https://open.feishu.cn/hook/TEST",
        feishu_signing_secret="TEST",
        feishu_app_id="TEST",
        feishu_app_secret="TEST",
        feishu_recipient_identifiers=("ou_test",),
        auto_close_enabled=True,
        campaign_thresholds={},
    )
    assert isinstance(settings.seller_id, str)
    # Explicit invariant: advertiserId is never part of the schema identity
    # — it must be rejected by the wire contract if it ever appears.
    assert "advertiserId" in FORBIDDEN_WIRE_FIELDS
    for forbidden in (
        "monitorEnabled",
        "queryRecentDays",
        "advertiserId",
        "token",
        "cookie",
        "serviceUrl",
        "nextScanAt",
        "lastError",
    ):
        assert forbidden in FORBIDDEN_WIRE_FIELDS, forbidden


def test_validate_complete_settings_rejects_untrusted_runtime_fields() -> None:
    payload = _complete_settings()
    payload["monitorEnabled"] = True
    with pytest.raises(ValueError, match="forbidden"):
        validate_complete_business_settings(payload)


def test_validate_complete_settings_rejects_legacy_query_recent_days() -> None:
    payload = _complete_settings(queryRecentDays=7)
    with pytest.raises(ValueError, match="queryRecentDays"):
        validate_complete_business_settings(payload)


def test_validate_complete_settings_rejects_unknown_field() -> None:
    payload = _complete_settings()
    payload["extraUnknown"] = 1
    with pytest.raises(ValueError, match="unknown field"):
        validate_complete_business_settings(payload)


# ---------------------------------------------------------------------------
# Complete-settings required field and type contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
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
    ],
)
def test_complete_settings_requires_every_allowed_field(field: str) -> None:
    payload = _complete_settings()
    payload.pop(field)
    with pytest.raises(ValueError, match=f"missing field.*{field}"):
        validate_complete_business_settings(payload)


def test_validate_complete_settings_round_trip_to_payload() -> None:
    payload = _complete_settings()
    settings = validate_complete_business_settings(payload)
    assert isinstance(settings, MonitorBusinessSettings)
    rebuilt = config_to_payload(settings)
    # The dataclass normalizes wire scalars to their canonical Python
    # types; the rebuilt payload must remain equivalent.
    for key, value in payload.items():
        rebuilt_value = rebuilt[key]
        if key == "globalThreshold":
            # Decimal "10" round-trips to "10"; the wire float is lossy on
            # purpose so we compare as Decimal.
            assert Decimal(str(rebuilt_value)) == Decimal(str(value)), key
        elif key == "autoCloseSingleConversionCostThreshold":
            assert Decimal(str(rebuilt_value)) == Decimal(str(value)), key
        elif key == "campaignThresholds":
            assert set(rebuilt_value) == set(value)
            for campaign_id, original in value.items():
                rebuilt_entry = rebuilt_value[campaign_id]
                if "spendThreshold" in original:
                    assert Decimal(str(rebuilt_entry["spendThreshold"])) == Decimal(str(original["spendThreshold"]))
                if "skuOrderLimit" in original:
                    assert rebuilt_entry["skuOrderLimit"] == original["skuOrderLimit"]
                if "singleConversionCostThreshold" in original:
                    assert Decimal(str(rebuilt_entry["singleConversionCostThreshold"])) == Decimal(str(original["singleConversionCostThreshold"]))
        elif isinstance(value, list):
            assert list(rebuilt_value) == value, key
        else:
            assert rebuilt_value == value, key


def test_validate_complete_settings_accepts_canonical_payload() -> None:
    settings = validate_complete_business_settings(_complete_settings())
    assert settings.seller_id is None  # complete payload has no sellerId
    assert settings.global_threshold == Decimal("10")
    assert settings.auto_close_campaign_whitelist == ("abc", "def")
    assert settings.feishu_recipient_identifiers == ("ou_test_recipient_1",)


# ---------------------------------------------------------------------------
# Numeric bounds, finite numbers, and min<=max constraints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_global_threshold_rejects_non_finite_number(bad_value: float) -> None:
    payload = _complete_settings(globalThreshold=bad_value)
    with pytest.raises(ValueError, match="finite"):
        validate_complete_business_settings(payload)


def test_global_threshold_rejects_negative() -> None:
    payload = _complete_settings(globalThreshold=-1)
    with pytest.raises(ValueError, match="globalThreshold"):
        validate_complete_business_settings(payload)


def test_scan_interval_min_must_meet_chrome_alarm_floor() -> None:
    payload = _complete_settings(scanIntervalMinSeconds=29)
    with pytest.raises(ValueError, match="scanIntervalMinSeconds"):
        validate_complete_business_settings(payload)


def test_scan_interval_min_must_be_less_than_or_equal_to_max() -> None:
    payload = _complete_settings(
        scanIntervalMinSeconds=60, scanIntervalMaxSeconds=30
    )
    with pytest.raises(ValueError, match="scanInterval"):
        validate_complete_business_settings(payload)


def test_notification_interval_min_must_be_less_than_or_equal_to_max() -> None:
    payload = _complete_settings(
        notificationIntervalMinSeconds=1080, notificationIntervalMaxSeconds=900
    )
    with pytest.raises(ValueError, match="notificationInterval"):
        validate_complete_business_settings(payload)


def test_close_query_recent_days_must_be_at_least_minimum() -> None:
    payload = _complete_settings(closeQueryRecentDays=0)
    with pytest.raises(ValueError, match="closeQueryRecentDays"):
        validate_complete_business_settings(payload)


def test_report_query_recent_days_must_be_at_least_minimum() -> None:
    payload = _complete_settings(reportQueryRecentDays=0)
    with pytest.raises(ValueError, match="reportQueryRecentDays"):
        validate_complete_business_settings(payload)


def test_min_constants_match_plugin_side_contract() -> None:
    assert MIN_CHROME_ALARM_DELAY_SECONDS == 30
    assert MIN_QUERY_RECENT_DAYS == 1


def test_auto_close_sku_order_limit_must_be_non_negative_integer() -> None:
    payload = _complete_settings(autoCloseSkuOrderLimit=-1)
    with pytest.raises(ValueError, match="autoCloseSkuOrderLimit"):
        validate_complete_business_settings(payload)


def test_auto_close_sku_order_limit_rejects_non_integer() -> None:
    payload = _complete_settings(autoCloseSkuOrderLimit=1.5)
    with pytest.raises(ValueError, match="autoCloseSkuOrderLimit"):
        validate_complete_business_settings(payload)


# ---------------------------------------------------------------------------
# Nullable-disable semantics: zero / false / empty list are NOT "missing"
# ---------------------------------------------------------------------------


def test_zero_is_not_treated_as_missing() -> None:
    payload = _complete_settings(globalThreshold=0, autoCloseSkuOrderLimit=0)
    settings = validate_complete_business_settings(payload)
    assert settings.global_threshold == Decimal("0")
    assert settings.auto_close_sku_order_limit == 0


def test_false_is_not_treated_as_missing() -> None:
    payload = _complete_settings(
        autoCloseEnabled=False,
        notifyAutoCloseSuccessEnabled=False,
        notifyAutoCloseFailureRepeatEnabled=False,
    )
    settings = validate_complete_business_settings(payload)
    assert settings.auto_close_enabled is False
    assert settings.notify_auto_close_success_enabled is False
    assert settings.notify_auto_close_failure_repeat_enabled is False


def test_empty_array_is_not_treated_as_missing() -> None:
    payload = _complete_settings(
        autoCloseCampaignWhitelist=[], feishuRecipientIdentifiers=[]
    )
    settings = validate_complete_business_settings(payload)
    assert settings.auto_close_campaign_whitelist == ()
    assert settings.feishu_recipient_identifiers == ()


def test_auto_close_max_sku_orders_allows_explicit_null() -> None:
    payload = _complete_settings(autoCloseMaxSkuOrders=None)
    settings = validate_complete_business_settings(payload)
    assert settings.auto_close_max_sku_orders is None


def test_auto_close_single_conversion_cost_threshold_allows_explicit_null() -> None:
    payload = _complete_settings(autoCloseSingleConversionCostThreshold=None)
    settings = validate_complete_business_settings(payload)
    assert settings.auto_close_single_conversion_cost_threshold is None


def test_auto_close_max_sku_orders_rejects_negative_integer() -> None:
    payload = _complete_settings(autoCloseMaxSkuOrders=-1)
    with pytest.raises(ValueError, match="autoCloseMaxSkuOrders"):
        validate_complete_business_settings(payload)


def test_auto_close_single_conversion_cost_threshold_rejects_negative_number() -> None:
    payload = _complete_settings(autoCloseSingleConversionCostThreshold=-0.1)
    with pytest.raises(ValueError, match="autoCloseSingleConversionCostThreshold"):
        validate_complete_business_settings(payload)


def test_auto_close_max_sku_orders_rejects_string_value() -> None:
    payload = _complete_settings(autoCloseMaxSkuOrders="not-a-number")
    with pytest.raises(ValueError, match="autoCloseMaxSkuOrders"):
        validate_complete_business_settings(payload)


def test_settings_payload_must_be_object() -> None:
    with pytest.raises(TypeError):
        validate_complete_business_settings(["not", "an", "object"])


# ---------------------------------------------------------------------------
# Array whole-replacement semantics
# ---------------------------------------------------------------------------


def test_auto_close_campaign_whitelist_replaces_not_concatenates() -> None:
    payload = _complete_settings(autoCloseCampaignWhitelist=["only-one"])
    settings = validate_complete_business_settings(payload)
    assert settings.auto_close_campaign_whitelist == ("only-one",)


def test_auto_close_campaign_whitelist_rejects_non_string_elements() -> None:
    payload = _complete_settings(autoCloseCampaignWhitelist=[1, 2, 3])
    with pytest.raises(ValueError, match="autoCloseCampaignWhitelist"):
        validate_complete_business_settings(payload)


def test_auto_close_campaign_whitelist_rejects_non_list_value() -> None:
    payload = _complete_settings(autoCloseCampaignWhitelist="abc")
    with pytest.raises(ValueError, match="autoCloseCampaignWhitelist"):
        validate_complete_business_settings(payload)


def test_feishu_recipient_identifiers_replaces_not_concatenates() -> None:
    payload = _complete_settings(feishuRecipientIdentifiers=["ou_only"])
    settings = validate_complete_business_settings(payload)
    assert settings.feishu_recipient_identifiers == ("ou_only",)


def test_feishu_recipient_identifiers_rejects_non_string_element() -> None:
    payload = _complete_settings(feishuRecipientIdentifiers=[123])
    with pytest.raises(ValueError, match="feishuRecipientIdentifiers"):
        validate_complete_business_settings(payload)


# ---------------------------------------------------------------------------
# campaignThresholds normalization (server emits object form)
# ---------------------------------------------------------------------------


def test_normalize_campaign_thresholds_object_form_passthrough() -> None:
    payload = {
        "campaign-x": {
            "spendThreshold": 20,
            "skuOrderLimit": 1,
            "singleConversionCostThreshold": 3.0,
        }
    }
    result = normalize_campaign_thresholds(payload)
    assert set(result) == {"campaign-x"}
    overrides = result["campaign-x"]
    assert isinstance(overrides, CampaignRuleOverrides)
    assert overrides.spend_threshold == Decimal("20")
    assert overrides.sku_order_limit == 1
    assert overrides.single_conversion_cost_threshold == Decimal("3.0")


def test_normalize_campaign_thresholds_numeric_shorthand_to_object() -> None:
    payload = {"campaign-y": 15}
    result = normalize_campaign_thresholds(payload)
    assert set(result) == {"campaign-y"}
    assert result["campaign-y"].spend_threshold == Decimal("15")
    assert result["campaign-y"].sku_order_limit is None
    assert result["campaign-y"].single_conversion_cost_threshold is None


def test_normalize_campaign_thresholds_rejects_unknown_keys() -> None:
    payload = {"campaign-z": {"unknownKey": 1}}
    with pytest.raises(ValueError, match="campaignThresholds"):
        normalize_campaign_thresholds(payload)


def test_normalize_campaign_thresholds_rejects_non_finite_number() -> None:
    payload = {"campaign-z": {"spendThreshold": float("nan")}}
    with pytest.raises(ValueError, match="finite"):
        normalize_campaign_thresholds(payload)


def test_normalize_campaign_thresholds_rejects_non_positive_spend() -> None:
    payload = {"campaign-z": {"spendThreshold": 0}}
    with pytest.raises(ValueError, match="spendThreshold"):
        normalize_campaign_thresholds(payload)


def test_normalize_campaign_thresholds_rejects_negative_sku_order_limit() -> None:
    payload = {"campaign-z": {"skuOrderLimit": -1}}
    with pytest.raises(ValueError, match="skuOrderLimit"):
        normalize_campaign_thresholds(payload)


def test_normalize_campaign_thresholds_rejects_non_integer_sku_order_limit() -> None:
    payload = {"campaign-z": {"skuOrderLimit": 1.5}}
    with pytest.raises(ValueError, match="skuOrderLimit"):
        normalize_campaign_thresholds(payload)


def test_normalize_campaign_thresholds_rejects_non_positive_conversion_cost() -> None:
    payload = {"campaign-z": {"singleConversionCostThreshold": 0}}
    with pytest.raises(ValueError, match="singleConversionCostThreshold"):
        normalize_campaign_thresholds(payload)


def test_normalize_campaign_thresholds_rejects_non_object_value() -> None:
    with pytest.raises(ValueError, match="campaignThresholds"):
        normalize_campaign_thresholds(["not", "an", "object"])


def test_normalize_campaign_thresholds_accepts_empty_object() -> None:
    assert normalize_campaign_thresholds({}) == {}


def test_normalize_campaign_thresholds_rejects_non_string_key() -> None:
    with pytest.raises(ValueError, match="campaignThresholds"):
        normalize_campaign_thresholds({1: {"spendThreshold": 1}})


def test_normalize_campaign_thresholds_rejects_empty_campaign_id() -> None:
    with pytest.raises(ValueError, match="campaignThresholds"):
        normalize_campaign_thresholds({"": {"spendThreshold": 1}})


def test_campaign_thresholds_omitted_means_no_overrides() -> None:
    payload = _complete_settings()
    payload.pop("campaignThresholds")
    with pytest.raises(ValueError, match="missing field.*campaignThresholds"):
        validate_complete_business_settings(payload)


def test_campaign_thresholds_full_settings_includes_normalized_overrides() -> None:
    payload = _complete_settings(
        campaignThresholds={
            "campaign-a": 25,
            "campaign-b": {
                "spendThreshold": 40,
                "skuOrderLimit": 2,
                "singleConversionCostThreshold": 4.5,
            },
        }
    )
    settings = validate_complete_business_settings(payload)
    assert set(settings.campaign_thresholds) == {"campaign-a", "campaign-b"}
    assert settings.campaign_thresholds["campaign-a"].spend_threshold == Decimal("25")
    assert (
        settings.campaign_thresholds["campaign-b"].single_conversion_cost_threshold
        == Decimal("4.5")
    )


# ---------------------------------------------------------------------------
# Override validation: subset semantics, absent == inherit
# ---------------------------------------------------------------------------


def test_override_allows_partial_payload() -> None:
    override = {"globalThreshold": 12, "autoCloseSkuOrderLimit": 3}
    normalized = validate_settings_override(override)
    assert normalized == {"globalThreshold": Decimal("12"), "autoCloseSkuOrderLimit": 3}


def test_override_rejects_unknown_field() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        validate_settings_override({"bogus": 1})


def test_override_rejects_untrusted_runtime_field() -> None:
    with pytest.raises(ValueError, match="forbidden"):
        validate_settings_override({"monitorEnabled": True})


def test_override_rejects_non_finite_number() -> None:
    with pytest.raises(ValueError, match="finite"):
        validate_settings_override({"globalThreshold": float("nan")})


def test_override_rejects_array_concatenation_with_global_sharing() -> None:
    """Override arrays always replace, even if the global is non-empty."""
    override = {"autoCloseCampaignWhitelist": []}
    normalized = validate_settings_override(override)
    assert normalized["autoCloseCampaignWhitelist"] == ()


def test_override_distinguishes_empty_array_from_missing() -> None:
    normalized = validate_settings_override({"autoCloseCampaignWhitelist": []})
    assert "autoCloseCampaignWhitelist" in normalized
    assert normalized["autoCloseCampaignWhitelist"] == ()


def test_override_allows_explicit_null_for_disable_only_fields() -> None:
    normalized = validate_settings_override(
        {
            "autoCloseMaxSkuOrders": None,
            "autoCloseSingleConversionCostThreshold": None,
        }
    )
    assert normalized["autoCloseMaxSkuOrders"] is None
    assert normalized["autoCloseSingleConversionCostThreshold"] is None


def test_override_rejects_explicit_null_for_non_nullable_field() -> None:
    with pytest.raises(ValueError, match="globalThreshold"):
        validate_settings_override({"globalThreshold": None})


# ---------------------------------------------------------------------------
# Global defaults + shop overrides merge
# ---------------------------------------------------------------------------


def test_merge_full_override_yields_override_settings() -> None:
    global_default = _complete_settings()
    override = {
        "globalThreshold": 15,
        "autoCloseSkuOrderLimit": 2,
        "autoCloseCampaignWhitelist": ["only-override"],
        "campaignThresholds": {"campaign-x": 99},
    }
    merged = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=global_default,
        shop_override=override,
    )
    assert merged.seller_id == TEST_SELLER_ID
    assert merged.global_threshold == Decimal("15")
    assert merged.auto_close_sku_order_limit == 2
    assert merged.auto_close_campaign_whitelist == ("only-override",)
    assert merged.scan_interval_min_seconds == 30  # inherited
    assert merged.feishu_webhook_url.startswith("https://")
    assert set(merged.campaign_thresholds) == {"campaign-x"}


def test_merge_empty_override_yields_global_settings() -> None:
    global_default = _complete_settings()
    merged = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=global_default,
        shop_override={},
    )
    expected = validate_complete_business_settings(global_default)
    assert merged.seller_id == TEST_SELLER_ID
    assert merged.global_threshold == expected.global_threshold
    assert merged.auto_close_campaign_whitelist == expected.auto_close_campaign_whitelist
    assert merged.feishu_recipient_identifiers == expected.feishu_recipient_identifiers


def test_merge_replaces_arrays_does_not_concatenate() -> None:
    global_default = _complete_settings(
        autoCloseCampaignWhitelist=["a", "b"],
        feishuRecipientIdentifiers=["ou_a", "ou_b"],
    )
    override = {
        "autoCloseCampaignWhitelist": ["c"],
        "feishuRecipientIdentifiers": ["ou_c"],
    }
    merged = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=global_default,
        shop_override=override,
    )
    assert merged.auto_close_campaign_whitelist == ("c",)
    assert merged.feishu_recipient_identifiers == ("ou_c",)


def test_merge_clears_array_when_override_is_empty_list() -> None:
    global_default = _complete_settings(
        autoCloseCampaignWhitelist=["a"], feishuRecipientIdentifiers=["ou_a"]
    )
    override = {
        "autoCloseCampaignWhitelist": [],
        "feishuRecipientIdentifiers": [],
    }
    merged = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=global_default,
        shop_override=override,
    )
    assert merged.auto_close_campaign_whitelist == ()
    assert merged.feishu_recipient_identifiers == ()


def test_merge_replaces_campaign_thresholds_whole_object_not_per_key() -> None:
    global_default = _complete_settings(
        campaignThresholds={
            "campaign-x": 10,
            "campaign-y": 20,
        }
    )
    override = {"campaignThresholds": {"campaign-z": 30}}
    merged = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=global_default,
        shop_override=override,
    )
    assert set(merged.campaign_thresholds) == {"campaign-z"}
    assert merged.campaign_thresholds["campaign-z"].spend_threshold == Decimal("30")


def test_merge_inherits_campaign_thresholds_when_override_omits() -> None:
    global_default = _complete_settings(
        campaignThresholds={"campaign-x": 10, "campaign-y": 20}
    )
    merged = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=global_default,
        shop_override={"globalThreshold": 11},
    )
    assert set(merged.campaign_thresholds) == {"campaign-x", "campaign-y"}


def test_merge_explicit_null_disables_override_field_for_shop() -> None:
    global_default = _complete_settings(
        autoCloseMaxSkuOrders=5, autoCloseSingleConversionCostThreshold=Decimal("2.5")
    )
    override = {
        "autoCloseMaxSkuOrders": None,
        "autoCloseSingleConversionCostThreshold": None,
    }
    merged = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=global_default,
        shop_override=override,
    )
    assert merged.auto_close_max_sku_orders is None
    assert merged.auto_close_single_conversion_cost_threshold is None


def test_merge_rejects_invalid_global_default() -> None:
    bad_global = _complete_settings(globalThreshold=float("nan"))
    with pytest.raises(ValueError, match="globalDefault"):
        merge_business_settings(
            seller_id=TEST_SELLER_ID,
            global_default=bad_global,
            shop_override={},
        )


def test_merge_rejects_invalid_shop_override() -> None:
    with pytest.raises(ValueError, match="shopOverride"):
        merge_business_settings(
            seller_id=TEST_SELLER_ID,
            global_default=_complete_settings(),
            shop_override={"globalThreshold": float("nan")},
        )


def test_merge_requires_non_empty_string_seller_id() -> None:
    with pytest.raises(ValueError, match="sellerId"):
        merge_business_settings(
            seller_id="",
            global_default=_complete_settings(),
            shop_override={},
        )


def test_merge_rejects_non_mapping_shop_override() -> None:
    """P1.1: falsy non-Mapping overrides (e.g. ``[]``, ``""``) MUST be rejected.

    Previously ``if not shop_override`` silently treated any falsy value
    (including lists / strings / scalars) as "inherit everything". Only
    ``None`` and an empty ``Mapping`` are valid empty overrides.
    """
    for bad in ([], "", 0, False, ("tuple",)):
        with pytest.raises(ConfigContractError, match="shopOverride"):
            merge_business_settings(
                seller_id=TEST_SELLER_ID,
                global_default=_complete_settings(),
                shop_override=bad,  # type: ignore[arg-type]
            )


def test_merge_rejects_non_mapping_global_default() -> None:
    for bad in ([], "", 0):
        with pytest.raises(ConfigContractError, match="globalDefault"):
            merge_business_settings(
                seller_id=TEST_SELLER_ID,
                global_default=bad,  # type: ignore[arg-type]
                shop_override={},
            )


def test_merge_none_shop_override_is_inherit_everything() -> None:
    """``None`` is the canonical "no override" sentinel."""
    settings = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=_complete_settings(globalThreshold=42),
        shop_override=None,
    )
    assert settings.global_threshold == Decimal("42")


def test_merge_empty_dict_shop_override_is_inherit_everything() -> None:
    """``{}`` is the canonical "no override" sentinel too."""
    settings = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=_complete_settings(globalThreshold=42),
        shop_override={},
    )
    assert settings.global_threshold == Decimal("42")


def test_merge_rejects_scan_min_only_override_inverting_global_max() -> None:
    """P1.2: cross-layer cross-layer validation. Global {min=30, max=60}
    combined with override {min=80} must produce a cross-layer error.
    """
    global_default = _complete_settings(
        scanIntervalMinSeconds=30, scanIntervalMaxSeconds=60
    )
    with pytest.raises(ConfigContractError, match="scanIntervalMaxSeconds"):
        merge_business_settings(
            seller_id=TEST_SELLER_ID,
            global_default=global_default,
            shop_override={"scanIntervalMinSeconds": 80},
        )


def test_merge_rejects_scan_max_only_override_inverting_global_min() -> None:
    """P1.2: opposite direction. Global {min=30, max=60} + override
    {max=20} must produce a cross-layer error.
    """
    global_default = _complete_settings(
        scanIntervalMinSeconds=30, scanIntervalMaxSeconds=60
    )
    with pytest.raises(ConfigContractError, match="scanIntervalMaxSeconds"):
        merge_business_settings(
            seller_id=TEST_SELLER_ID,
            global_default=global_default,
            shop_override={"scanIntervalMaxSeconds": 20},
        )


def test_merge_rejects_notification_min_only_override_inverting_global_max() -> None:
    """P1.2: same check for the notification interval range."""
    global_default = _complete_settings(
        notificationIntervalMinSeconds=900, notificationIntervalMaxSeconds=1080
    )
    with pytest.raises(ConfigContractError, match="notificationIntervalMaxSeconds"):
        merge_business_settings(
            seller_id=TEST_SELLER_ID,
            global_default=global_default,
            shop_override={"notificationIntervalMinSeconds": 1200},
        )


def test_merge_rejects_notification_max_only_override_inverting_global_min() -> None:
    """P1.2: opposite direction for the notification interval range."""
    global_default = _complete_settings(
        notificationIntervalMinSeconds=900, notificationIntervalMaxSeconds=1080
    )
    with pytest.raises(ConfigContractError, match="notificationIntervalMaxSeconds"):
        merge_business_settings(
            seller_id=TEST_SELLER_ID,
            global_default=global_default,
            shop_override={"notificationIntervalMaxSeconds": 800},
        )


def test_merge_accepts_min_only_override_still_satisfying_max() -> None:
    """P1.2 sanity: override {min=40} on global {min=30, max=60} is
    allowed (override min 40 <= global max 60).
    """
    merged = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=_complete_settings(
            scanIntervalMinSeconds=30, scanIntervalMaxSeconds=60
        ),
        shop_override={"scanIntervalMinSeconds": 40},
    )
    assert merged.scan_interval_min_seconds == 40
    assert merged.scan_interval_max_seconds == 60


def test_merge_normalizes_decimals_for_deterministic_round_trip() -> None:
    """The wire payload must serialize numbers with stable precision.

    The merge layer accepts numeric scalars from the wire surface and
    normalizes them into the typed dataclass. Iteration in settings.py must
    not lose precision (e.g. ``Decimal('15')`` vs ``Decimal('15.0')``).
    """
    global_default = _complete_settings(globalThreshold=15)
    override = {"autoCloseSingleConversionCostThreshold": 2.5}
    merged = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=global_default,
        shop_override=override,
    )
    rebuilt = config_to_payload(merged)
    assert rebuilt["globalThreshold"] == "15"
    assert rebuilt["autoCloseSingleConversionCostThreshold"] == "2.5"


# ---------------------------------------------------------------------------
# Delivery envelope (response shape, from design §6.1)
# ---------------------------------------------------------------------------


def _envelope_settings() -> MonitorBusinessSettings:
    return merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=_complete_settings(),
        shop_override={},
    )


def test_build_envelope_includes_schema_version_and_seller_id_strings() -> None:
    envelope = parse_envelope(
        _envelope_settings(),
        current_version="18",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:abcdef",
    )
    assert envelope.schema_version == WIRE_SCHEMA_VERSION
    assert envelope.seller_id == TEST_SELLER_ID
    assert isinstance(envelope.current_version, str)
    assert envelope.current_version == "18"
    assert envelope.source_versions == {"global": "4", "shop": None}
    assert envelope.content_hash == "sha256:abcdef"
    assert envelope.force is None
    assert envelope.payload["sellerId"] == TEST_SELLER_ID
    assert envelope.payload["schemaVersion"] == WIRE_SCHEMA_VERSION


def test_build_envelope_force_is_distinct_from_current() -> None:
    force_settings = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=_complete_settings(globalThreshold=99),
        shop_override={},
    )
    force_target = parse_envelope(
        force_settings,
        current_version="17",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:forcehash",
    ).current
    envelope = parse_envelope(
        _envelope_settings(),
        current_version="18",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:currenthash",
        force=DeliveryEnvelope.Force(version="17", target=force_target),
    )
    assert envelope.force is not None
    assert envelope.force.version == "17"
    assert envelope.force.target.version == "17"
    assert envelope.force.target.payload.global_threshold == Decimal("99")
    assert envelope.current.payload.global_threshold == Decimal("10")


def test_build_envelope_rejects_unknown_outer_field() -> None:
    with pytest.raises(ValueError, match="unknown"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current={},
            force=None,
            junkField="bogus",
        )


def test_build_envelope_rejects_non_string_seller_id() -> None:
    with pytest.raises(ValueError, match="sellerId"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=123,  # type: ignore[arg-type]
            shopPk="101",
            current={},
            force=None,
        )


def test_build_envelope_rejects_non_string_version() -> None:
    with pytest.raises(ValueError, match="version"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current={
                "version": 18,  # type: ignore[dict-item]
                "sourceVersions": {"global": "4", "shop": None},
                "contentHash": "sha256:abc",
                "payload": {"settings": {}, "campaignThresholds": {}},
            },
            force=None,
        )


def test_build_envelope_rejects_invalid_inner_payload() -> None:
    bad_payload = {"settings": {"monitorEnabled": True}, "campaignThresholds": {}}
    with pytest.raises(ValueError, match="settings"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current={
                "version": "18",
                "sourceVersions": {"global": "4", "shop": None},
                "contentHash": "sha256:abc",
                "payload": bad_payload,
            },
            force=None,
        )


def test_build_envelope_rejects_force_with_legacy_sibling_shape() -> None:
    """P1.3: force MUST be {version, target}. The legacy sibling
    {version, sourceVersions, contentHash, payload} block is rejected.
    """
    with pytest.raises(ConfigContractError, match="force"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current={
                "version": "18",
                "sourceVersions": {"global": "4", "shop": None},
                "contentHash": "sha256:abc",
                "payload": {
                    "sellerId": TEST_SELLER_ID,
                    "schemaVersion": 1,
                    "settings": _complete_settings(),
                },
            },
            force={
                "version": "17",
                "sourceVersions": {"global": "4", "shop": None},
                "contentHash": "sha256:abc",
                "payload": {
                    "sellerId": TEST_SELLER_ID,
                    "schemaVersion": 1,
                    "settings": _complete_settings(),
                },
            },
        )


def test_build_envelope_rejects_force_version_mismatch_with_target() -> None:
    """P1.3: force.version MUST equal force.target.version."""
    settings = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=_complete_settings(globalThreshold=99),
        shop_override={},
    )
    target = Current(
        version="17",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:abc",
        payload=settings,
    )
    with pytest.raises(ConfigContractError, match="force.version"):
        DeliveryEnvelope.Force(version="WRONG", target=target)


def test_build_envelope_rejects_force_with_unknown_field() -> None:
    """P1.3: force only allows {version, target} (wire shape)."""
    settings = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=_complete_settings(),
        shop_override={},
    )
    target = Current(
        version="17",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:abc",
        payload=settings,
    )
    with pytest.raises(ConfigContractError, match="unknown"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current={
                "version": "18",
                "sourceVersions": {"global": "4", "shop": None},
                "contentHash": "sha256:abc",
                "payload": {
                    "sellerId": TEST_SELLER_ID,
                    "schemaVersion": 1,
                    "settings": _complete_settings(),
                },
            },
            force={
                "version": "17",
                "target": {
                    "version": "17",
                    "sourceVersions": {"global": "4", "shop": None},
                    "contentHash": "sha256:abc",
                    "payload": {
                        "sellerId": TEST_SELLER_ID,
                        "schemaVersion": 1,
                        "settings": _complete_settings(),
                    },
                },
                "junk": "bogus",
            },
        )


def test_parse_envelope_accepts_wire_shape_force_block() -> None:
    """P1.3: ``parse_envelope`` must accept a wire-shape ``{version, target}`` dict."""
    force_settings = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=_complete_settings(globalThreshold=99),
        shop_override={},
    )
    force_envelope = parse_envelope(
        force_settings,
        current_version="17",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:forcehash",
    )
    envelope = parse_envelope(
        _envelope_settings(),
        current_version="18",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:currenthash",
        force={
            "version": "17",
            "target": {
                "version": "17",
                "sourceVersions": {"global": "4", "shop": None},
                "contentHash": "sha256:forcehash",
                "payload": force_envelope.payload,
            },
        },
    )
    assert envelope.force is not None
    assert envelope.force.version == "17"
    assert envelope.force.target.version == "17"


def test_parse_envelope_rejects_wire_force_version_mismatch() -> None:
    force_settings = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=_complete_settings(),
        shop_override={},
    )
    force_envelope = parse_envelope(
        force_settings,
        current_version="17",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:forcehash",
    )
    with pytest.raises(ConfigContractError, match="force.version"):
        parse_envelope(
            _envelope_settings(),
            current_version="18",
            source_versions={"global": "4", "shop": None},
            content_hash="sha256:currenthash",
            force={
                "version": "WRONG",
                "target": {
                    "version": "17",
                    "sourceVersions": {"global": "4", "shop": None},
                    "contentHash": "sha256:forcehash",
                    "payload": force_envelope.payload,
                },
            },
        )


def test_parse_envelope_rejects_cross_shop_force_target() -> None:
    """P1.3: force.target.payload.sellerId must equal envelope sellerId."""
    force_settings = merge_business_settings(
        seller_id=TEST_SELLER_ID_OTHER,  # different seller!
        global_default=_complete_settings(),
        shop_override={},
    )
    target = Current(
        version="17",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:abc",
        payload=force_settings,
    )
    with pytest.raises(ConfigContractError, match="sellerId"):
        parse_envelope(
            _envelope_settings(),
            current_version="18",
            source_versions={"global": "4", "shop": None},
            content_hash="sha256:currenthash",
            force=DeliveryEnvelope.Force(version="17", target=target),
        )


def test_build_envelope_rejects_cross_shop_current_payload_seller_id() -> None:
    """P1.3: when current.payload.sellerId is present, it MUST equal
    the outer envelope sellerId; otherwise the snapshot is mixing
    identities.
    """
    with pytest.raises(ConfigContractError, match="sellerId"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current={
                "version": "18",
                "sourceVersions": {"global": "4", "shop": None},
                "contentHash": "sha256:abc",
                "payload": {
                    "sellerId": TEST_SELLER_ID_OTHER,  # mismatch!
                    "schemaVersion": 1,
                    "settings": _complete_settings(),
                },
            },
            force=None,
        )


def test_build_envelope_accepts_current_payload_seller_id_matching_envelope() -> None:
    env = DeliveryEnvelope(
        schemaVersion=1,
        sellerId=TEST_SELLER_ID,
        shopPk="101",
        current={
            "version": "18",
            "sourceVersions": {"global": "4", "shop": None},
            "contentHash": "sha256:abc",
            "payload": {
                "sellerId": TEST_SELLER_ID,  # matches
                "schemaVersion": 1,
                "settings": _complete_settings(),
            },
        },
        force=None,
    )
    assert env.seller_id == TEST_SELLER_ID


def test_build_envelope_accepts_wire_shape_force_mapping_directly() -> None:
    """Final review: a raw wire-shape ``{version, target}`` ``Mapping``
    must round-trip through ``DeliveryEnvelope(...)`` directly (not
    only via ``parse_envelope``). The earlier bug used
    ``force.target`` before coercion, which raised ``AttributeError``
    after a Mapping was coerced to ``typed_force``.
    """
    current_settings = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=_complete_settings(),
        shop_override={},
    )
    force_settings = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=_complete_settings(globalThreshold=99),
        shop_override={},
    )
    force_envelope = parse_envelope(
        force_settings,
        current_version="17",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:forcehash",
    )
    env = DeliveryEnvelope(
        schemaVersion=1,
        sellerId=TEST_SELLER_ID,
        shopPk="101",
        current={
            "version": "18",
            "sourceVersions": {"global": "4", "shop": None},
            "contentHash": "sha256:abc",
            "payload": {
                "sellerId": TEST_SELLER_ID,
                "schemaVersion": 1,
                "settings": config_to_payload(current_settings),
            },
        },
        force={
            "version": "17",
            "target": {
                "version": "17",
                "sourceVersions": {"global": "4", "shop": None},
                "contentHash": "sha256:forcehash",
                "payload": {
                    "sellerId": TEST_SELLER_ID,
                    "schemaVersion": 1,
                    "settings": config_to_payload(force_settings),
                },
            },
        },
    )
    assert env.force is not None
    assert env.force.version == "17"
    assert env.force.target.version == "17"
    assert env.force.target.payload.global_threshold == Decimal("99")
    assert env.current.payload.global_threshold == Decimal("10")


def test_build_envelope_rejects_typed_current_with_mismatched_seller_id() -> None:
    """Final review: the outer/current seller identity guard must
    fire for a typed :class:`Current` whose
    ``payload.seller_id`` differs from the envelope ``sellerId``,
    not only for raw wire-shape Mappings.
    """
    other_settings = merge_business_settings(
        seller_id=TEST_SELLER_ID_OTHER,
        global_default=_complete_settings(),
        shop_override={},
    )
    typed_current = Current(
        version="18",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:abc",
        payload=other_settings,  # seller_id = TEST_SELLER_ID_OTHER
    )
    with pytest.raises(ConfigContractError, match="sellerId"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,  # mismatch with other_settings.seller_id
            shopPk="101",
            current=typed_current,
            force=None,
        )


def test_build_envelope_accepts_typed_current_with_matching_seller_id() -> None:
    """Counterpart: typed :class:`Current` whose ``payload.seller_id``
    matches the envelope is accepted.
    """
    matching_settings = merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=_complete_settings(),
        shop_override={},
    )
    typed_current = Current(
        version="18",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:abc",
        payload=matching_settings,
    )
    env = DeliveryEnvelope(
        schemaVersion=1,
        sellerId=TEST_SELLER_ID,
        shopPk="101",
        current=typed_current,
        force=None,
    )
    assert env.seller_id == TEST_SELLER_ID
    assert env.current.payload.seller_id == TEST_SELLER_ID


def test_build_envelope_accepts_typed_current_with_none_seller_id() -> None:
    """``seller_id is None`` is the canonical global-default state and
    is accepted by the cross-shop guard; the envelope's outer
    ``sellerId`` still identifies the shop for transport purposes.
    """
    # Global default has no seller_id (seller_id is None).
    default_settings = validate_complete_business_settings(_complete_settings())
    assert default_settings.seller_id is None
    typed_current = Current(
        version="18",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:abc",
        payload=default_settings,
    )
    env = DeliveryEnvelope(
        schemaVersion=1,
        sellerId=TEST_SELLER_ID,
        shopPk="101",
        current=typed_current,
        force=None,
    )
    assert env.seller_id == TEST_SELLER_ID
    assert env.current.payload.seller_id is None


# ---------------------------------------------------------------------------
# Canonical decimal wire identifiers (shopPk / delivery / force version)
# ---------------------------------------------------------------------------

#: Accepted canonical decimal identifiers. ``0`` stays valid because the
#: plugin's ``/^\d+$/`` accepts it and it is the documented "not yet
#: assigned" sentinel for ``shopPk``. ``9007199254740993`` is 2^53+1 —
#: exactly the value that loses precision as a JS ``Number``, and the
#: reason these fields are strings at all.
CANONICAL_DECIMAL_IDENTIFIERS = [
    "0",
    "1",
    "7",
    "18",
    "101",
    "9007199254740993",
]

#: Rejected values: empty, whitespace, signs, decimal points, exponents,
#: non-ASCII digits (fullwidth ``１８`` and Arabic-Indic ``١٨`` both
#: match Python's ``\d`` but not the plugin's ASCII-only ``\d``), and
#: anything else that is not a bare digit run.
NON_CANONICAL_DECIMAL_IDENTIFIERS = [
    "",
    " 18",
    "18 ",
    "18\n",
    "+18",
    "-18",
    "18.0",
    "1.8e1",
    "v18",
    "0x12",
    "18_000",
    "1,018",
    "１８",
    "١八",
    "⑱",
]

#: Parity fixture: the plugin gate is the JavaScript literal
#: ``const DECIMAL_IDENTIFIER = /^\d+$/;`` in
#: ``plugins/monitor/src/core/remote-config.ts``, applied by
#: ``requireDecimalIdentifier``. Re-evaluated here in Python so a future
#: change to this table is checked against the same rule the plugin uses,
#: without importing or executing the TypeScript module. Verified
#: value-by-value against ``node`` running the literal regex.
_PLUGIN_DECIMAL_IDENTIFIER = re.compile(r"[0-9]+")


def _plugin_accepts(value: str) -> bool:
    r"""Evaluate the plugin's ``/^\d+$/`` rule for one wire value."""
    return _PLUGIN_DECIMAL_IDENTIFIER.fullmatch(value) is not None


@pytest.mark.parametrize("identifier", CANONICAL_DECIMAL_IDENTIFIERS)
def test_canonical_table_matches_plugin_regex(identifier: str) -> None:
    assert _plugin_accepts(identifier) is True


@pytest.mark.parametrize("identifier", NON_CANONICAL_DECIMAL_IDENTIFIERS)
def test_non_canonical_table_matches_plugin_regex(identifier: str) -> None:
    assert _plugin_accepts(identifier) is False


def _wire_current_block(version: Any) -> dict[str, Any]:
    return {
        "version": version,
        "sourceVersions": {"global": "4", "shop": None},
        "contentHash": "sha256:abc",
        "payload": {
            "sellerId": TEST_SELLER_ID,
            "schemaVersion": 1,
            "settings": _complete_settings(),
        },
    }


def _wire_force_block(version: Any, target_version: Any) -> dict[str, Any]:
    return {"version": version, "target": _wire_current_block(target_version)}


@pytest.mark.parametrize("identifier", CANONICAL_DECIMAL_IDENTIFIERS)
def test_shop_pk_accepts_canonical_decimal_identifier(identifier: str) -> None:
    env = DeliveryEnvelope(
        schemaVersion=1,
        sellerId=TEST_SELLER_ID,
        shopPk=identifier,
        current=_wire_current_block("18"),
        force=None,
    )
    assert env.shop_pk == identifier


@pytest.mark.parametrize("identifier", NON_CANONICAL_DECIMAL_IDENTIFIERS)
def test_shop_pk_rejects_non_canonical_decimal_identifier(identifier: str) -> None:
    with pytest.raises(ConfigContractError, match="shopPk"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk=identifier,
            current=_wire_current_block("18"),
            force=None,
        )


@pytest.mark.parametrize("bad_shop_pk", [101, 18.0, None, True])
def test_shop_pk_rejects_non_string_value(bad_shop_pk: Any) -> None:
    """``bool`` is an ``int`` subclass in Python; it must not become ``"1"``."""
    with pytest.raises(ConfigContractError, match="shopPk"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk=bad_shop_pk,  # type: ignore[arg-type]
            current=_wire_current_block("18"),
            force=None,
        )


@pytest.mark.parametrize("identifier", CANONICAL_DECIMAL_IDENTIFIERS)
def test_current_version_accepts_canonical_decimal_identifier(identifier: str) -> None:
    env = DeliveryEnvelope(
        schemaVersion=1,
        sellerId=TEST_SELLER_ID,
        shopPk="101",
        current=_wire_current_block(identifier),
        force=None,
    )
    assert env.current_version == identifier


@pytest.mark.parametrize("identifier", NON_CANONICAL_DECIMAL_IDENTIFIERS)
def test_current_version_rejects_non_canonical_decimal_identifier(
    identifier: str,
) -> None:
    with pytest.raises(ConfigContractError, match="current.version"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_wire_current_block(identifier),
            force=None,
        )


@pytest.mark.parametrize("bad_version", [18, 18.0, None, True])
def test_current_version_rejects_non_string_value(bad_version: Any) -> None:
    """An ``int`` version must not be silently coerced to ``"18"``: the
    plugin rejects a non-string version outright.
    """
    with pytest.raises(ConfigContractError, match="current.version"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_wire_current_block(bad_version),
            force=None,
        )


@pytest.mark.parametrize("identifier", NON_CANONICAL_DECIMAL_IDENTIFIERS)
def test_force_version_rejects_non_canonical_decimal_identifier(
    identifier: str,
) -> None:
    with pytest.raises(ConfigContractError, match="force.version"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_wire_current_block("18"),
            force=_wire_force_block(identifier, "18"),
        )


@pytest.mark.parametrize("identifier", NON_CANONICAL_DECIMAL_IDENTIFIERS)
def test_force_target_version_rejects_non_canonical_decimal_identifier(
    identifier: str,
) -> None:
    with pytest.raises(ConfigContractError, match="force.target.version"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_wire_current_block("18"),
            force=_wire_force_block("18", identifier),
        )


@pytest.mark.parametrize("identifier", CANONICAL_DECIMAL_IDENTIFIERS)
def test_force_version_and_target_accept_canonical_decimal_identifier(
    identifier: str,
) -> None:
    env = DeliveryEnvelope(
        schemaVersion=1,
        sellerId=TEST_SELLER_ID,
        shopPk="101",
        current=_wire_current_block("18"),
        force=_wire_force_block(identifier, identifier),
    )
    assert env.force is not None
    assert env.force.version == identifier
    assert env.force.target.version == identifier


@pytest.mark.parametrize("bad_version", [18, 18.0, None])
def test_force_version_rejects_non_string_value(bad_version: Any) -> None:
    with pytest.raises(ConfigContractError, match="force.version"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_wire_current_block("18"),
            force=_wire_force_block(bad_version, "18"),
        )


@pytest.mark.parametrize("identifier", NON_CANONICAL_DECIMAL_IDENTIFIERS)
def test_typed_current_rejects_non_canonical_version(identifier: str) -> None:
    """Typed/mapping coercion parity: a typed :class:`Current` must not
    smuggle a non-decimal version past the wire-shape guard.
    """
    with pytest.raises(ConfigContractError, match="version"):
        Current(
            version=identifier,
            source_versions={"global": "4", "shop": None},
            content_hash="sha256:abc",
            payload=_envelope_settings(),
        )


@pytest.mark.parametrize("identifier", NON_CANONICAL_DECIMAL_IDENTIFIERS)
def test_typed_force_rejects_non_canonical_version(identifier: str) -> None:
    target = Current(
        version="17",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:abc",
        payload=_envelope_settings(),
    )
    with pytest.raises(ConfigContractError, match="force.version"):
        DeliveryEnvelope.Force(version=identifier, target=target)


@pytest.mark.parametrize("identifier", NON_CANONICAL_DECIMAL_IDENTIFIERS)
def test_parse_envelope_rejects_non_canonical_current_version(
    identifier: str,
) -> None:
    with pytest.raises(ConfigContractError, match="currentVersion"):
        parse_envelope(
            _envelope_settings(),
            current_version=identifier,
            source_versions={"global": "4", "shop": None},
            content_hash="sha256:abc",
        )


@pytest.mark.parametrize("identifier", NON_CANONICAL_DECIMAL_IDENTIFIERS)
def test_parse_envelope_rejects_non_canonical_shop_pk(identifier: str) -> None:
    with pytest.raises(ConfigContractError, match="shopPk"):
        parse_envelope(
            _envelope_settings(),
            current_version="18",
            source_versions={"global": "4", "shop": None},
            content_hash="sha256:abc",
            shop_pk=identifier,
        )


def test_parse_envelope_default_shop_pk_is_canonical() -> None:
    """The documented ``shop_pk="0"`` default must stay a valid
    canonical decimal identifier.
    """
    envelope = parse_envelope(
        _envelope_settings(),
        current_version="18",
        source_versions={"global": "4", "shop": None},
        content_hash="sha256:abc",
    )
    assert envelope.shop_pk == "0"


def test_source_versions_stay_plain_non_empty_strings() -> None:
    """Parity guard against over-tightening: the plugin validates
    ``sourceVersions`` entries with ``requireNonEmptyString``, NOT with
    the decimal-identifier rule, so non-decimal source revisions stay
    legal here too.
    """
    env = DeliveryEnvelope(
        schemaVersion=1,
        sellerId=TEST_SELLER_ID,
        shopPk="101",
        current={
            "version": "18",
            "sourceVersions": {"global": "global-rev-a", "shop": None},
            "contentHash": "sha256:abc",
            "payload": {
                "sellerId": TEST_SELLER_ID,
                "schemaVersion": 1,
                "settings": _complete_settings(),
            },
        },
        force=None,
    )
    assert env.source_versions == {"global": "global-rev-a", "shop": None}


def test_content_hash_stays_plain_non_empty_string() -> None:
    """``contentHash`` is not a decimal identifier on either side."""
    env = DeliveryEnvelope(
        schemaVersion=1,
        sellerId=TEST_SELLER_ID,
        shopPk="101",
        current={
            "version": "18",
            "sourceVersions": {"global": "4", "shop": None},
            "contentHash": "sha256:abcdef",
            "payload": {
                "sellerId": TEST_SELLER_ID,
                "schemaVersion": 1,
                "settings": _complete_settings(),
            },
        },
        force=None,
    )
    assert env.content_hash == "sha256:abcdef"


# ---------------------------------------------------------------------------
# force.target is a full delivery block, not an unvalidated mapping
# ---------------------------------------------------------------------------

_DELIVERY_BLOCK_FIELDS = ("version", "sourceVersions", "contentHash", "payload")


def _force_over(target: Any) -> dict[str, Any]:
    return {"version": "18", "target": target}


@pytest.mark.parametrize("missing", _DELIVERY_BLOCK_FIELDS)
def test_force_target_missing_field_is_a_contract_error(missing: str) -> None:
    """``force.target`` must be shape-validated like ``current``; a
    missing key must surface as ``ConfigContractError``, never ``KeyError``.
    """
    target = _wire_current_block("18")
    del target[missing]
    with pytest.raises(
        ConfigContractError, match=f"force.target missing field: {missing}"
    ):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_wire_current_block("18"),
            force=_force_over(target),
        )


def test_force_target_unknown_field_is_a_contract_error() -> None:
    target = dict(_wire_current_block("18"), bogusField="x")
    with pytest.raises(ConfigContractError, match="force.target has unknown field"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_wire_current_block("18"),
            force=_force_over(target),
        )


def test_force_target_payload_must_be_an_object() -> None:
    target = dict(_wire_current_block("18"), payload=["not", "an", "object"])
    with pytest.raises(
        ConfigContractError, match="force.target.payload must be an object"
    ):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_wire_current_block("18"),
            force=_force_over(target),
        )


def test_force_target_payload_settings_must_be_an_object() -> None:
    target = dict(_wire_current_block("18"))
    target["payload"] = {"sellerId": TEST_SELLER_ID, "schemaVersion": 1}
    with pytest.raises(ConfigContractError, match="force.target.payload.settings"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_wire_current_block("18"),
            force=_force_over(target),
        )


def test_force_target_payload_settings_reject_forbidden_fields() -> None:
    """The forbidden-field guard must name the ``force.target`` shape
    instead of falling through to the generic settings validator.
    """
    target = dict(_wire_current_block("18"))
    target["payload"] = {
        "sellerId": TEST_SELLER_ID,
        "schemaVersion": 1,
        "settings": {"monitorEnabled": True},
    }
    with pytest.raises(
        ConfigContractError,
        match="force.target.payload.settings contains forbidden field",
    ):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_wire_current_block("18"),
            force=_force_over(target),
        )


def test_force_target_source_versions_must_be_an_object() -> None:
    target = dict(_wire_current_block("18"), sourceVersions=["4"])
    with pytest.raises(
        ConfigContractError, match="force.target.sourceVersions must be an object"
    ):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_wire_current_block("18"),
            force=_force_over(target),
        )


# ---------------------------------------------------------------------------
# contentHash is never str()-coerced (both delivery paths)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_hash", [123, 18.5, None, True, "", ["x"], {"a": 1}, b"abc"]
)
def test_current_content_hash_must_be_a_non_empty_string(bad_hash: Any) -> None:
    block = _wire_current_block("18")
    block["contentHash"] = bad_hash
    with pytest.raises(ConfigContractError, match="current.contentHash"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=block,
            force=None,
        )


@pytest.mark.parametrize(
    "bad_hash", [123, 18.5, None, True, "", ["x"], {"a": 1}, b"abc"]
)
def test_force_target_content_hash_must_be_a_non_empty_string(
    bad_hash: Any,
) -> None:
    """Regression: ``contentHash: 123`` used to be accepted because the
    coercion path called ``str()`` on it, producing ``"123"``.
    """
    target = dict(_wire_current_block("18"), contentHash=bad_hash)
    with pytest.raises(ConfigContractError, match="force.target.contentHash"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_wire_current_block("18"),
            force=_force_over(target),
        )


def test_force_target_content_hash_is_preserved_verbatim() -> None:
    """No ``str()`` normalization: the validated string is stored as-is."""
    env = DeliveryEnvelope(
        schemaVersion=1,
        sellerId=TEST_SELLER_ID,
        shopPk="101",
        current=_wire_current_block("18"),
        force=_force_over(
            dict(_wire_current_block("18"), contentHash="sha256:target-hash")
        ),
    )
    assert env.force is not None
    assert env.force.target.content_hash == "sha256:target-hash"


# ---------------------------------------------------------------------------
# sourceVersions values follow the plugin's requireNonEmptyString
# ---------------------------------------------------------------------------


def _current_with_source_versions(source_versions: Any) -> dict[str, Any]:
    return dict(_wire_current_block("18"), sourceVersions=source_versions)


@pytest.mark.parametrize("empty_value", ["", {}])
def test_current_source_versions_reject_empty_string_value(empty_value: Any) -> None:
    with pytest.raises(
        ConfigContractError, match="current.sourceVersions.global"
    ):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_current_with_source_versions({"global": empty_value}),
            force=None,
        )


@pytest.mark.parametrize("empty_value", ["", {}])
def test_force_target_source_versions_reject_empty_string_value(
    empty_value: Any,
) -> None:
    target = dict(_wire_current_block("18"))
    target["sourceVersions"] = {"shop": empty_value}
    with pytest.raises(
        ConfigContractError, match="force.target.sourceVersions.shop"
    ):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_wire_current_block("18"),
            force=_force_over(target),
        )


@pytest.mark.parametrize("bad_value", [4, 4.5, True, ["4"]])
def test_current_source_versions_reject_non_string_value(bad_value: Any) -> None:
    with pytest.raises(ConfigContractError, match="current.sourceVersions.global"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=_current_with_source_versions({"global": bad_value}),
            force=None,
        )


def test_source_versions_still_accept_null_and_arbitrary_strings() -> None:
    """``null`` means "no such source revision" and non-decimal revision
    labels are legal; only the empty string is rejected.
    """
    env = DeliveryEnvelope(
        schemaVersion=1,
        sellerId=TEST_SELLER_ID,
        shopPk="101",
        current=_current_with_source_versions(
            {"global": "global-rev-a", "shop": None}
        ),
        force=None,
    )
    assert env.source_versions == {"global": "global-rev-a", "shop": None}


def test_source_versions_empty_object_is_allowed() -> None:
    """An empty ``sourceVersions`` object is a valid object on the plugin
    side (``requireObject`` only rejects non-objects).
    """
    env = DeliveryEnvelope(
        schemaVersion=1,
        sellerId=TEST_SELLER_ID,
        shopPk="101",
        current=_current_with_source_versions({}),
        force=None,
    )
    assert env.source_versions == {}


# ---------------------------------------------------------------------------
# Typed constructors enforce the same delivery-block invariants
# ---------------------------------------------------------------------------


def _typed_payload() -> MonitorBusinessSettings:
    """A seller-bound typed settings object usable as a delivery payload."""
    return merge_business_settings(
        seller_id=TEST_SELLER_ID,
        global_default=_complete_settings(),
        shop_override={},
    )


def _tampered_current(
    *,
    version: str = "17",
    source_versions: Any = None,
    content_hash: Any = "sha256:abc",
) -> Current:
    """Build a :class:`Current` bypassing ``__post_init__``.

    Used to prove that :class:`DeliveryEnvelope.Force` enforces the
    delivery-block contract on its own, rather than only relying on the
    target having passed :class:`Current` construction. ``frozen=True``
    blocks normal assignment, so the fields are written directly.
    """
    if source_versions is None:
        source_versions = {"global": "4", "shop": None}
    current = Current.__new__(Current)
    object.__setattr__(current, "version", version)
    object.__setattr__(current, "source_versions", source_versions)
    object.__setattr__(current, "content_hash", content_hash)
    object.__setattr__(current, "payload", _typed_payload())
    return current


@pytest.mark.parametrize("bad_hash", ["", None, 123, 18.5, True, ["x"], b"abc"])
def test_typed_current_rejects_non_empty_string_content_hash(
    bad_hash: Any,
) -> None:
    """A typed ``Current`` must not smuggle a contentHash past the
    mapping-path guard; ``Current`` is also used as ``force.target``.
    """
    with pytest.raises(ConfigContractError, match="current.contentHash"):
        Current(
            version="18",
            source_versions={"global": "4", "shop": None},
            content_hash=bad_hash,  # type: ignore[arg-type]
            payload=_typed_payload(),
        )


@pytest.mark.parametrize("bad_value", ["", 4, 4.5, True, ["4"]])
def test_typed_current_rejects_invalid_source_versions_value(
    bad_value: Any,
) -> None:
    with pytest.raises(ConfigContractError, match="current.sourceVersions.shop"):
        Current(
            version="18",
            source_versions={"shop": bad_value},  # type: ignore[dict-item]
            content_hash="sha256:abc",
            payload=_typed_payload(),
        )


@pytest.mark.parametrize("bad_source_versions", ["4", ["4"], None, 4])
def test_typed_current_rejects_non_mapping_source_versions(
    bad_source_versions: Any,
) -> None:
    with pytest.raises(ConfigContractError, match="current.sourceVersions"):
        Current(
            version="18",
            source_versions=bad_source_versions,  # type: ignore[arg-type]
            content_hash="sha256:abc",
            payload=_typed_payload(),
        )


def test_typed_current_accepts_valid_source_versions_and_content_hash() -> None:
    """Counterpart: ``null`` entries and non-decimal revision labels stay
    legal so the typed path is not stricter than the mapping path.
    """
    current = Current(
        version="18",
        source_versions={"global": "global-rev-a", "shop": None},
        content_hash="sha256:abc",
        payload=_typed_payload(),
    )
    assert current.source_versions == {"global": "global-rev-a", "shop": None}
    assert current.content_hash == "sha256:abc"


def test_typed_current_accepts_empty_source_versions_object() -> None:
    current = Current(
        version="18",
        source_versions={},
        content_hash="sha256:abc",
        payload=_typed_payload(),
    )
    assert current.source_versions == {}


@pytest.mark.parametrize("bad_hash", ["", None, 123])
def test_typed_force_target_rejects_non_empty_string_content_hash(
    bad_hash: Any,
) -> None:
    """``Force.target`` must carry the delivery-block contract even when
    the target was not built through ``Current.__post_init__``, and must
    report the force-scoped path.
    """
    with pytest.raises(ConfigContractError, match="force.target.contentHash"):
        DeliveryEnvelope.Force(
            version="17",
            target=_tampered_current(content_hash=bad_hash),
        )


@pytest.mark.parametrize("bad_value", ["", 4])
def test_typed_force_target_rejects_invalid_source_versions_value(
    bad_value: Any,
) -> None:
    with pytest.raises(
        ConfigContractError, match="force.target.sourceVersions.shop"
    ):
        DeliveryEnvelope.Force(
            version="17",
            target=_tampered_current(
                source_versions={"shop": bad_value},
            ),
        )


def test_typed_force_target_rejects_non_mapping_source_versions() -> None:
    with pytest.raises(
        ConfigContractError, match="force.target.sourceVersions must be an object"
    ):
        DeliveryEnvelope.Force(
            version="17",
            target=_tampered_current(source_versions=["4"]),
        )


def test_typed_force_accepts_valid_target() -> None:
    """Counterpart: a well-formed typed force block is accepted."""
    force = DeliveryEnvelope.Force(
        version="17",
        target=Current(
            version="17",
            source_versions={"global": "global-rev-a", "shop": None},
            content_hash="sha256:abc",
            payload=_typed_payload(),
        ),
    )
    assert force.version == "17"
    assert force.target.content_hash == "sha256:abc"


def test_typed_current_with_empty_content_hash_cannot_reach_envelope() -> None:
    """End-to-end guard for the typed ``current=`` bypass: an envelope
    built from a tampered typed block must still be rejected.
    """
    current = _tampered_current(version="18", content_hash="")
    with pytest.raises(ConfigContractError, match="current.contentHash"):
        DeliveryEnvelope(
            schemaVersion=1,
            sellerId=TEST_SELLER_ID,
            shopPk="101",
            current=current,
            force=None,
        )


def test_typed_force_with_tampered_target_cannot_reach_envelope() -> None:
    """Same guard for the typed ``force=`` bypass: a force block whose
    target carries an invalid ``sourceVersions`` entry is rejected at
    construction, before it can be handed to :class:`DeliveryEnvelope`.
    """
    with pytest.raises(
        ConfigContractError, match="force.target.sourceVersions.shop"
    ):
        DeliveryEnvelope.Force(
            version="17",
            target=_tampered_current(source_versions={"shop": ""}),
        )



# ---------------------------------------------------------------------------
# Immutability and immutability-of-derived-views
# ---------------------------------------------------------------------------


def test_business_settings_dataclass_is_frozen() -> None:
    settings = validate_complete_business_settings(_complete_settings())
    with pytest.raises(Exception):
        settings.seller_id = "OTHER"  # type: ignore[misc]


def test_business_settings_array_fields_are_tuples() -> None:
    settings = validate_complete_business_settings(_complete_settings())
    assert isinstance(settings.auto_close_campaign_whitelist, tuple)
    assert isinstance(settings.feishu_recipient_identifiers, tuple)


def test_business_settings_views_do_not_alias_input() -> None:
    """Mutating the source dict must not change a built settings instance."""
    payload = _complete_settings()
    settings = validate_complete_business_settings(deepcopy(payload))
    payload["autoCloseCampaignWhitelist"].append("leaked")
    assert settings.auto_close_campaign_whitelist == ("abc", "def")


def test_normalized_campaign_thresholds_views_do_not_alias_input() -> None:
    payload = {"campaign-x": {"spendThreshold": 10}}
    result = normalize_campaign_thresholds(payload)
    payload["campaign-x"]["skuOrderLimit"] = 999
    assert result["campaign-x"].sku_order_limit is None


def test_business_settings_preserves_mapping_proxy_for_thresholds() -> None:
    settings = validate_complete_business_settings(_complete_settings())
    view = settings.campaign_thresholds
    assert isinstance(view, Mapping)
    with pytest.raises(TypeError):
        view["campaign-z"] = 1  # type: ignore[index]


# ---------------------------------------------------------------------------
# validate_business_settings is a thin alias
# ---------------------------------------------------------------------------


def test_validate_business_settings_accepts_complete() -> None:
    settings = validate_business_settings(_complete_settings(), require_complete=True)
    assert isinstance(settings, MonitorBusinessSettings)


def test_validate_business_settings_accepts_partial() -> None:
    normalized = validate_business_settings({"globalThreshold": 7})
    assert normalized == {"globalThreshold": Decimal("7")}