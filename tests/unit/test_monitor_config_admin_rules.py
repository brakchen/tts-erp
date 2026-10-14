"""Phase C：管理编排的纯规则与 secret 释放边界。

这里只放不经过 HTTP 的规则层（命名空间、固定 schema、草稿校验、secret
引用白名单与一次性快照），保证失败点在最靠近原因的位置；路由与事务行为见
``tests/api/test_monitor_config_admin.py``。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy.orm import Session

from tts_erp_v2.monitor_config import admin, contracts
from tts_erp_v2.monitor_config.keys import (
    GLOBAL_DEFAULTS_KEY,
    ReservedConfigKeyError,
    is_reserved_config_key,
    shop_config_key,
    shop_pk_from_config_key,
)
from tts_erp_v2.runtime_config.validation import validate_schema

pytestmark = pytest.mark.layer_integration

SECRET_A = "monitor_test_release_a"
SECRET_B = "monitor_test_release_b"
PLAINTEXT_A = "TEST_RELEASE_PLAINTEXT_A"
PLAINTEXT_B = "TEST_RELEASE_PLAINTEXT_B"


# ─── 保留命名空间（§3.2 / §5.1）────────────────────────────────


@pytest.mark.parametrize(
    "config_key,reserved",
    [
        ("monitor.defaults", True),
        ("monitor.shop.42", True),
        ("monitor.shopx", True),
        ("analytics.spu_profit_deterioration_alert.v1", False),
        ("video_publish.device_slot", False),
    ],
)
def test_is_reserved_config_key(config_key: str, reserved: bool) -> None:
    assert is_reserved_config_key(config_key) is reserved


def test_shop_config_key_round_trips_through_the_pk() -> None:
    key = shop_config_key(42)
    assert key == "monitor.shop.42"
    assert shop_pk_from_config_key(key) == 42


@pytest.mark.parametrize(
    "config_key", ["monitor.shop", "monitor.shop.", "monitor.shop.x", "monitor"]
)
def test_shop_pk_from_config_key_rejects_non_shop_keys(config_key: str) -> None:
    assert shop_pk_from_config_key(config_key) is None


def test_global_defaults_key_is_inside_the_reserved_namespace() -> None:
    assert is_reserved_config_key(GLOBAL_DEFAULTS_KEY)


def test_reserved_key_error_points_at_the_monitor_admin_entry() -> None:
    with pytest.raises(ReservedConfigKeyError) as excinfo:
        admin.ensure_generic_mutation_allowed("monitor.defaults")
    assert "/v2/monitor/admin" in str(excinfo.value)
    # 非保留键不受影响。
    admin.ensure_generic_mutation_allowed("video_publish.device_slot")


# ─── 固定受控 schema（§5.1：不采用自动推断的宽松 schema）─────────


def test_defaults_schema_is_legal_and_requires_every_field() -> None:
    schema = validate_schema(admin.defaults_settings_schema())
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == contracts._ALLOWED_SETTINGS_FIELDS
    assert set(schema["properties"]) == contracts._ALLOWED_SETTINGS_FIELDS


def test_override_schema_is_legal_and_requires_nothing() -> None:
    schema = validate_schema(admin.override_settings_schema())
    assert schema["additionalProperties"] is False
    assert "required" not in schema
    assert set(schema["properties"]) == contracts._ALLOWED_SETTINGS_FIELDS


def test_schemas_reject_a_field_outside_the_contract() -> None:
    for schema in (admin.defaults_settings_schema(), admin.override_settings_schema()):
        assert "monitorEnabled" not in schema["properties"]


# ─── 草稿校验 ────────────────────────────────────────────────────


def _complete(**overrides: Any) -> dict[str, Any]:
    payload = {
        "globalThreshold": "10.0",
        "autoCloseSkuOrderLimit": 0,
        "autoCloseMaxSkuOrders": 5,
        "autoCloseSingleConversionCostThreshold": "2.5",
        "autoCloseCampaignWhitelist": ["TEST_a"],
        "closeQueryRecentDays": 7,
        "reportQueryRecentDays": 30,
        "scanIntervalMinSeconds": 30,
        "scanIntervalMaxSeconds": 60,
        "notificationIntervalMinSeconds": 900,
        "notificationIntervalMaxSeconds": 1080,
        "notifyAutoCloseSuccessEnabled": True,
        "notifyAutoCloseFailureRepeatEnabled": True,
        "feishuWebhookUrl": f"secret://{SECRET_A}",
        "feishuSigningSecret": "",
        "feishuAppId": "TEST_app",
        "feishuAppSecret": "",
        "feishuRecipientIdentifiers": ["ou_TEST"],
        "autoCloseEnabled": True,
        "campaignThresholds": {},
    }
    payload.update(overrides)
    return payload


def test_defaults_draft_requires_a_complete_payload() -> None:
    incomplete = _complete()
    del incomplete["autoCloseEnabled"]
    with pytest.raises(contracts.ConfigContractError):
        admin.validate_draft_payload(GLOBAL_DEFAULTS_KEY, incomplete)


def test_shop_draft_accepts_a_partial_override() -> None:
    admin.validate_draft_payload(shop_config_key(7), {"globalThreshold": "1.5"})


def test_shop_draft_accepts_an_empty_override_as_inherit_everything() -> None:
    admin.validate_draft_payload(shop_config_key(7), {})


def test_shop_draft_rejects_explicit_null_on_a_non_nullable_field() -> None:
    with pytest.raises(contracts.ConfigContractError):
        admin.validate_draft_payload(shop_config_key(7), {"autoCloseEnabled": None})


def test_shop_draft_allows_null_only_on_the_two_threshold_disable_fields() -> None:
    admin.validate_draft_payload(
        shop_config_key(7),
        {"autoCloseMaxSkuOrders": None, "autoCloseSingleConversionCostThreshold": None},
    )


def test_draft_rejects_a_forbidden_runtime_field() -> None:
    with pytest.raises(contracts.ConfigContractError):
        admin.validate_draft_payload(shop_config_key(7), {"cookie": "TEST"})
    with pytest.raises(contracts.ConfigContractError):
        admin.validate_draft_payload(shop_config_key(7), {"token": "TEST"})


def test_draft_rejects_a_secret_reference_outside_the_whitelist() -> None:
    with pytest.raises(admin.ForeignSecretReference):
        admin.validate_draft_payload(
            shop_config_key(7),
            {
                "campaignThresholds": {
                    "TEST_c": {"spendThreshold": f"secret://{SECRET_A}"}
                }
            },
        )
    with pytest.raises(admin.ForeignSecretReference):
        admin.validate_draft_payload(
            shop_config_key(7), {"feishuRecipientIdentifiers": [f"secret://{SECRET_A}"]}
        )


def test_draft_allows_a_secret_reference_on_every_whitelisted_field() -> None:
    for field in sorted(admin.RELEASABLE_SECRET_FIELDS):
        payload = _complete()
        payload[field] = f"secret://{SECRET_A}"
        admin.validate_draft_payload(GLOBAL_DEFAULTS_KEY, payload)


# ─── secret 快照与释放（§4.3 / §5.3）───────────────────────────


def test_release_substitutes_only_whitelisted_references() -> None:
    snapshot = admin.SecretSnapshot({SECRET_A: PLAINTEXT_A})
    released = admin.release_secret_references(
        {
            "feishuWebhookUrl": f"secret://{SECRET_A}",
            "feishuSigningSecret": "TEST_literal_secret",
            "globalThreshold": "10.0",
        },
        snapshot,
    )
    assert released["feishuWebhookUrl"] == PLAINTEXT_A
    assert released["feishuSigningSecret"] == "TEST_literal_secret"
    assert released["globalThreshold"] == "10.0"


def test_release_rejects_a_missing_secret_instead_of_keeping_the_reference() -> None:
    with pytest.raises(admin.MissingSecret):
        admin.release_secret_references(
            {"feishuWebhookUrl": f"secret://{SECRET_A}"}, admin.SecretSnapshot({})
        )


def test_release_refuses_to_run_on_an_unreviewed_payload() -> None:
    """非白名单字段的引用必须在释放前就被拒绝，而不是静默跳过。"""

    with pytest.raises(admin.ForeignSecretReference):
        admin.release_secret_references(
            {"globalThreshold": f"secret://{SECRET_A}"}, admin.SecretSnapshot({})
        )


def test_secret_snapshot_reads_and_decrypts_each_name_once(db_session: Session) -> None:
    from tts_erp_v2.runtime_config.repository import upsert_secret

    upsert_secret(db_session, name=SECRET_A, value=PLAINTEXT_A)
    upsert_secret(db_session, name=SECRET_B, value=PLAINTEXT_B)
    db_session.flush()

    snapshot = admin.load_secret_snapshot(db_session, {SECRET_B, SECRET_A, SECRET_A})
    assert dict(snapshot.values) == {SECRET_A: PLAINTEXT_A, SECRET_B: PLAINTEXT_B}


def test_secret_snapshot_reports_a_retired_or_missing_name(db_session: Session) -> None:
    from datetime import UTC, datetime

    from tts_erp_v2.db.models.config import RuntimeConfigSecret
    from tts_erp_v2.runtime_config.repository import upsert_secret

    upsert_secret(db_session, name=SECRET_A, value=PLAINTEXT_A)
    db_session.flush()
    retired = db_session.get(RuntimeConfigSecret, SECRET_A)
    assert retired is not None
    retired.retired_at = datetime.now(UTC)
    db_session.flush()

    with pytest.raises(admin.MissingSecret):
        admin.load_secret_snapshot(db_session, {SECRET_A})


def test_composed_payload_is_revalidated_rather_than_trusted() -> None:
    """发布前对合成结果再跑一次契约：已发布的草稿不能绕过范围检查。

    刻意不用 DB：``finalize_composed_payload`` 必须是纯函数式的“解冻 + 复验”，
    否则一份历史遗留的非法 revision 会被直接冻结进交付快照。
    """

    snapshot = admin.SecretSnapshot({SECRET_A: PLAINTEXT_A})
    # scanIntervalMaxSeconds=10 小于 scanIntervalMinSeconds=30。
    composed = _complete(scanIntervalMaxSeconds=10)
    with pytest.raises(contracts.ConfigContractError):
        admin.finalize_composed_payload(composed, snapshot)


def test_finalize_returns_the_plain_payload_for_a_healthy_composition() -> None:
    snapshot = admin.SecretSnapshot({SECRET_A: PLAINTEXT_A})
    settings = admin.finalize_composed_payload(
        {"feishuWebhookUrl": f"secret://{SECRET_A}", **_complete()}, snapshot
    )
    assert settings.feishu_webhook_url == PLAINTEXT_A
    assert settings.seller_id is None


def test_composed_payload_keys_must_be_within_the_contract() -> None:
    payload: Mapping[str, Any] = {"__unknown__": "TEST"}
    with pytest.raises(contracts.ConfigContractError):
        contracts.validate_complete_business_settings(payload)
