"""Phase C：管理编排的纯规则与 secret 释放边界。

这里只放不经过 HTTP 的规则层（命名空间、固定 schema、草稿校验、secret
引用白名单与一次性快照），保证失败点在最靠近原因的位置；路由与事务行为见
``tests/api/test_monitor_config_admin.py``。
"""

from __future__ import annotations

import uuid
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
        "feishuAppId": "",
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


# ─── 白名单飞书字段不得以明文入库（Terra P1-1）────────────────────
#
# 这四个字段是唯一会被解冻下发给浏览器的字段（§4.3）。允许字面量意味着密钥
# 以明文留在 config.runtime_config_items、被 GET draft 原样回显，并进入
# revision 与 delivery 审计面。因此只接受两种写法：
#   * ``secret://<name>`` 引用
#   * ``""`` —— §3.2 固定的空串语义（该通知渠道未启用）
# 其余一律在入库前拒绝。


@pytest.mark.parametrize(
    "field,literal",
    [
        ("feishuWebhookUrl", "https://open.feishu.cn/hook/LITERAL_LEAK"),
        ("feishuSigningSecret", "LITERAL_SIGNING_SECRET"),
        ("feishuAppId", "LITERAL_APP_ID"),
        ("feishuAppSecret", "LITERAL_APP_SECRET"),
    ],
)
def test_draft_rejects_a_literal_value_on_a_released_field(
    field: str, literal: str
) -> None:
    with pytest.raises(admin.LiteralSecretValue):
        admin.validate_draft_payload(GLOBAL_DEFAULTS_KEY, _complete(**{field: literal}))
    with pytest.raises(admin.LiteralSecretValue):
        admin.validate_draft_payload(shop_config_key(7), {field: literal})


def test_draft_rejects_a_literal_even_when_another_field_uses_a_reference() -> None:
    payload = _complete(feishuWebhookUrl=f"secret://{SECRET_A}")
    payload["feishuAppSecret"] = "LITERAL_MIXED"
    with pytest.raises(admin.LiteralSecretValue):
        admin.validate_draft_payload(GLOBAL_DEFAULTS_KEY, payload)


def test_draft_accepts_every_released_field_as_empty_to_mean_disabled() -> None:
    payload = _complete()
    for field in admin.RELEASABLE_SECRET_FIELDS:
        payload[field] = ""
    admin.validate_draft_payload(GLOBAL_DEFAULTS_KEY, payload)
    admin.validate_draft_payload(shop_config_key(7), {})


def test_draft_accepts_a_reference_on_every_released_field() -> None:
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


# ─── 首次草稿的并发创建（Terra P1-2）───────────────────────────
#
# ``locked_item`` 用 ``SELECT ... FOR UPDATE``，但对**尚不存在**的行不加锁：
# 两个并发的 ``expectedDraftVersion=0`` 会同时未命中并同时 INSERT，其中一个撞
# ``runtime_config_items`` 主键。裸的 IntegrityError 会变成 500，而正确的
# 文档化结果是第二个请求得到 409 DraftVersionConflict。
#
# 交错用 ``before_cursor_execute`` 钩子**证成**：B 真的把原子 upsert 发到数据库
# 之后，A 才提交。单靠 ``join(timeout)`` 无法区分“B 堵在 upsert 上”与“B 根本
# 没走到那一步”（与 tests/unit/test_monitor_config_persistence.py 同一手法）。


def _run_first_draft_race(db_engine, config_key: str, payload: dict[str, Any]) -> None:
    """Two concurrent first saves must yield one row and one DraftVersionConflict."""

    import threading

    from sqlalchemy import delete, event, select

    from tts_erp_v2.db.models.config import (
        RuntimeConfigItem,
        RuntimeConfigRevision,
    )

    upsert_issued = threading.Event()
    follower_thread: list[threading.Thread] = []
    outcome: dict[str, Any] = {}

    # A：抢先插入草稿行，但**不提交**。
    session_a = Session(db_engine)
    try:
        admin.save_draft(
            session_a,
            config_key=config_key,
            display_name="TEST monitor first draft",
            payload=payload,
            expected_draft_version=0,
        )

        def _follower() -> None:
            try:
                with Session(db_engine) as session_b:
                    admin.save_draft(
                        session_b,
                        config_key=config_key,
                        display_name="TEST monitor first draft",
                        payload=payload,
                        expected_draft_version=0,
                    )
                    session_b.commit()
                outcome["saved"] = True
            except BaseException as exc:  # noqa: BLE001 - reported below
                outcome["error"] = exc

        def _note_upsert(
            conn, cursor, statement, parameters, context, executemany
        ) -> None:
            """Latch when the follower actually emits the atomic upsert."""

            current = threading.current_thread()
            if not follower_thread or current is not follower_thread[0]:
                return
            normalized = " ".join(statement.upper().split())
            if (
                "INSERT INTO CONFIG.RUNTIME_CONFIG_ITEMS" in normalized
                and "ON CONFLICT (CONFIG_KEY) DO NOTHING" in normalized
            ):
                upsert_issued.set()

        event.listen(db_engine, "before_cursor_execute", _note_upsert)
        thread = threading.Thread(target=_follower)
        follower_thread.append(thread)
        thread.start()

        try:
            assert upsert_issued.wait(timeout=30), (
                "follower never issued the atomic upsert before A committed"
            )
            assert thread.is_alive(), (
                "follower must still be blocked on A's uncommitted draft row"
            )
        finally:
            event.remove(db_engine, "before_cursor_execute", _note_upsert)

        session_a.commit()
        thread.join(timeout=30)
        assert not thread.is_alive(), "follower must finish once A commits"

        error = outcome.get("error")
        assert error is not None, "the second first-draft save must be refused"
        assert isinstance(error, admin.DraftVersionConflict), (
            f"concurrent first draft must be a documented 409 conflict, got "
            f"{type(error).__name__}: {error!r}"
        )
    finally:
        session_a.close()

    try:
        with Session(db_engine) as verify:
            rows = (
                verify.execute(
                    select(RuntimeConfigItem).where(
                        RuntimeConfigItem.config_key == config_key
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1, f"exactly one draft row expected, got {len(rows)}"
            assert rows[0].draft_version == 1
    finally:
        with Session(db_engine) as cleanup:
            cleanup.execute(
                delete(RuntimeConfigRevision).where(
                    RuntimeConfigRevision.config_key == config_key
                )
            )
            cleanup.execute(
                delete(RuntimeConfigItem).where(
                    RuntimeConfigItem.config_key == config_key
                )
            )
            cleanup.commit()


def test_concurrent_first_defaults_draft_is_a_conflict_not_a_crash(db_engine) -> None:
    _run_first_draft_race(db_engine, GLOBAL_DEFAULTS_KEY, _complete())


def test_concurrent_first_shop_draft_is_a_conflict_not_a_crash(db_engine) -> None:
    from sqlalchemy import delete

    from tts_erp_v2.db.models.commerce import ChannelAccount

    with Session(db_engine) as setup:
        shop = ChannelAccount(
            platform="tiktok",
            shop_id=f"TEST_monitor_draftrace_{uuid.uuid4().hex[:8]}",
            account_name="TEST_monitor_draftrace",
        )
        setup.add(shop)
        setup.commit()
        shop_pk = shop.id

    try:
        # 店铺草稿是部分覆盖，不要求完整字段集。
        _run_first_draft_race(
            db_engine, shop_config_key(shop_pk), {"globalThreshold": "12.5"}
        )
    finally:
        with Session(db_engine) as cleanup:
            cleanup.execute(delete(ChannelAccount).where(ChannelAccount.id == shop_pk))
            cleanup.commit()
