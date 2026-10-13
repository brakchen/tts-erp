"""monitor 配置的持久化投影：api_keys 受限用途列 + 店铺 state/delivery 表。

契约来源：docs/monitor-config-sync.md §4.2（受限 Token）与 §5.2（交付投影表）。
阶段 A 只固定 schema 与迁移；阶段 B 才写入 delivery 行。
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from tts_erp_v2.db.models.commerce import ChannelAccount
from tts_erp_v2.db.models.monitor_config import (
    MonitorConfigDelivery,
    MonitorShopState,
)
from tts_erp_v2.db.models.security import ApiKey

SHOP_ID = "TEST_monitor_config_delivery_seller"
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0070_monitor_config_delivery.py"
)


@pytest.fixture()
def monitor_shop(db_session: Session) -> int:
    """Shares the test's rolled-back transaction, so no manual cleanup."""

    shop = ChannelAccount(
        platform="tiktok", shop_id=SHOP_ID, account_name="TEST_monitor_config"
    )
    db_session.add(shop)
    db_session.commit()
    return shop.id


def _delivery(session: Session, shop_pk: int, version: int, **kwargs):
    row = MonitorConfigDelivery(
        shop_pk=shop_pk,
        version=version,
        source_global_version=4,
        source_shop_version=None,
        schema_version=1,
        content_hash="sha256:" + "a" * 64,
        encrypted_payload=b"TEST_fernet_blob",
        is_force=kwargs.pop("is_force", False),
        created_by="TEST_monitor_config_actor",
        **kwargs,
    )
    session.add(row)
    session.commit()
    return row


# ─── security.api_keys 的受限用途列 ──────────────────────────────


def test_api_keys_expose_purpose_and_shop_pk_with_general_default(
    db_session: Session,
) -> None:
    columns = set(
        db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='security' AND table_name='api_keys'"
            )
        )
        .scalars()
        .all()
    )
    assert {"purpose", "shop_pk"} <= columns


def test_existing_api_keys_default_to_general_without_shop(
    db_session: Session,
) -> None:
    key_hash = uuid.uuid4().hex + "0" * 32
    db_session.add(
        ApiKey(
            key_hash=key_hash,
            key_prefix="TEST_monitor_legacy",
            name="TEST_monitor_legacy_key",
            role="readonly",
        )
    )
    db_session.commit()
    row = db_session.execute(
        select(ApiKey).where(ApiKey.key_hash == key_hash)
    ).scalar_one()
    assert row.purpose == "general"
    assert row.shop_pk is None


def test_monitor_purpose_requires_a_shop_binding(db_engine, monitor_shop: int) -> None:
    """0070 的 CHECK：monitor key 必须绑定 shop_pk，general key 不得绑定。"""

    with pytest.raises(IntegrityError):
        with Session(db_engine) as session:
            session.add(
                ApiKey(
                    key_hash=uuid.uuid4().hex + "1" * 32,
                    key_prefix="TEST_monitor_unbound",
                    name="TEST_monitor_unbound_key",
                    role="readonly",
                    purpose="monitor_config",
                    shop_pk=None,
                )
            )
            session.commit()
        pytest.fail("monitor_config without shop_pk must be rejected")

    with pytest.raises(IntegrityError):
        with Session(db_engine) as session:
            session.add(
                ApiKey(
                    key_hash=uuid.uuid4().hex + "2" * 32,
                    key_prefix="TEST_monitor_scoped",
                    name="TEST_monitor_scoped_general_key",
                    role="readonly",
                    purpose="general",
                    shop_pk=monitor_shop,
                )
            )
            session.commit()
        pytest.fail("general purpose carrying a shop_pk must be rejected")


# ─── config.monitor_shop_state ────────────────────────────────────


def test_shop_state_starts_unconfigured_and_advances_pointers(
    db_session: Session, monitor_shop: int
) -> None:
    state = MonitorShopState(shop_pk=monitor_shop)
    db_session.add(state)
    db_session.commit()
    db_session.expire_all()

    stored = db_session.get(MonitorShopState, monitor_shop)
    assert stored is not None
    assert stored.current_delivery_version is None
    assert stored.last_force_version is None

    state.current_delivery_version = 18
    state.last_force_version = 17
    db_session.commit()
    db_session.expire_all()

    stored = db_session.get(MonitorShopState, monitor_shop)
    assert stored is not None
    assert stored.current_delivery_version == 18
    assert stored.last_force_version == 17
    assert stored.created_at.tzinfo is not None
    assert stored.updated_at.tzinfo is not None


def test_shop_state_is_unique_per_shop(db_session: Session, monitor_shop: int) -> None:
    db_session.add(MonitorShopState(shop_pk=monitor_shop))
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.add(MonitorShopState(shop_pk=monitor_shop))
        db_session.commit()
    db_session.rollback()


# ─── config.monitor_config_deliveries ────────────────────────────


def test_delivery_round_trips_every_frozen_field(
    db_session: Session, monitor_shop: int
) -> None:
    request_id = uuid.uuid4()
    created_at = datetime.now(UTC)
    _delivery(
        db_session,
        monitor_shop,
        18,
        is_force=True,
        request_id=request_id,
        request_digest="sha256:" + "b" * 64,
    )
    db_session.expire_all()

    stored = db_session.execute(
        select(MonitorConfigDelivery).where(
            MonitorConfigDelivery.shop_pk == monitor_shop,
            MonitorConfigDelivery.version == 18,
        )
    ).scalar_one()

    assert stored.source_global_version == 4
    assert stored.source_shop_version is None
    assert stored.schema_version == 1
    assert stored.content_hash == "sha256:" + "a" * 64
    assert stored.encrypted_payload == b"TEST_fernet_blob"
    assert stored.is_force is True
    assert stored.created_by == "TEST_monitor_config_actor"
    assert stored.request_id == request_id
    assert stored.request_digest == "sha256:" + "b" * 64
    assert stored.created_at >= created_at.replace(microsecond=0)
    assert stored.created_at.tzinfo is not None


def test_delivery_version_is_unique_per_shop(
    db_session: Session, monitor_shop: int
) -> None:
    _delivery(db_session, monitor_shop, 18)
    with pytest.raises(IntegrityError):
        _delivery(db_session, monitor_shop, 18)
    db_session.rollback()


def test_delivery_request_id_is_unique_per_shop(
    db_session: Session, monitor_shop: int
) -> None:
    shared = uuid.uuid4()
    _delivery(db_session, monitor_shop, 18, request_id=shared)
    with pytest.raises(IntegrityError):
        _delivery(db_session, monitor_shop, 19, request_id=shared)
    db_session.rollback()


def test_delivery_rejects_unknown_shop(db_session: Session) -> None:
    with pytest.raises((IntegrityError, DBAPIError)):
        _delivery(db_session, 10**12, 1)
    db_session.rollback()


def test_delivery_payload_is_binary_not_plaintext_text(
    db_session: Session, monitor_shop: int
) -> None:
    """明文只经 token_service 进出；列类型必须是二进制密文。"""

    row = _delivery(db_session, monitor_shop, 1)
    db_session.expire_all()
    stored = db_session.get(MonitorConfigDelivery, row.id)
    assert stored is not None
    assert isinstance(stored.encrypted_payload, bytes)


# ─── 迁移契约 ────────────────────────────────────────────────────


def test_migration_is_chained_after_0069() -> None:
    source = MIGRATION.read_text()
    assert 'revision: str = "0070_monitor_config_delivery"' in source
    assert 'down_revision: str | None = "0069_video_publish_confirmation"' in source


def test_migration_creates_the_projection_objects() -> None:
    source = MIGRATION.read_text()
    for fragment in (
        '"monitor_shop_state"',
        '"monitor_config_deliveries"',
        "uq_monitor_config_deliveries_shop_version",
        "uq_monitor_config_deliveries_shop_request_id",
        'schema="config"',
        'schema="security"',
    ):
        assert fragment in source, fragment


def test_live_head_is_0070(db_session: Session) -> None:
    assert (
        db_session.scalar(text("SELECT version_num FROM alembic_version"))
        == "0070_monitor_config_delivery"
    )


# ─── f-7691881f-7d1：标准 trigger 与 purpose 取值域 ─────────────


def test_shop_state_has_the_standard_updated_at_trigger(
    db_session: Session,
) -> None:
    triggers = db_session.execute(
        text(
            "SELECT trigger_name, action_timing, event_manipulation "
            "FROM information_schema.triggers "
            "WHERE event_object_schema='config' "
            "AND event_object_table='monitor_shop_state'"
        )
    ).all()
    assert triggers, "monitor_shop_state must carry the standard touch trigger"
    name, timing, event = triggers[0]
    assert timing == "BEFORE"
    assert event == "UPDATE"
    assert name.startswith("trg_config_monitor_shop_state")
    definition = db_session.scalar(
        text("SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE tgname = :n"),
        {"n": name},
    )
    # pg_get_triggerdef 渲染时不带 schema 前缀，只断言函数名。
    assert "fn_touch_updated_at()" in definition


def test_shop_state_updated_at_advances_on_update(
    db_session: Session, monitor_shop: int
) -> None:
    """trigger 必须真正刷新 updated_at，而不是只靠 ORM 的 onupdate。"""

    state = MonitorShopState(shop_pk=monitor_shop)
    db_session.add(state)
    db_session.commit()
    stored = db_session.get(MonitorShopState, monitor_shop)
    assert stored is not None
    before = stored.updated_at

    db_session.execute(
        text(
            "UPDATE config.monitor_shop_state "
            "SET current_delivery_version = 3 WHERE shop_pk = :pk"
        ),
        {"pk": monitor_shop},
    )
    db_session.commit()
    db_session.expire_all()

    refreshed = db_session.get(MonitorShopState, monitor_shop)
    assert refreshed is not None
    after = refreshed.updated_at
    assert after > before


def test_api_keys_reject_purpose_outside_the_allowed_domain(
    db_session: Session,
) -> None:
    """purpose 只允许 general|monitor_config（0070 CHECK）。"""

    with pytest.raises(IntegrityError):
        db_session.add(
            ApiKey(
                key_hash=uuid.uuid4().hex + "3" * 32,
                key_prefix="TEST_monitor_bogus",
                name="TEST_monitor_bogus_purpose",
                role="readonly",
                purpose="not_a_real_purpose",
            )
        )
        db_session.commit()
        pytest.fail("unknown purpose must be rejected by the database")
    db_session.rollback()


def test_api_keys_accept_both_allowed_purposes(
    db_session: Session, monitor_shop: int
) -> None:
    general = ApiKey(
        key_hash=uuid.uuid4().hex + "4" * 32,
        key_prefix="TEST_monitor_ok_general",
        name="TEST_monitor_ok_general",
        role="readonly",
        purpose="general",
    )
    monitor = ApiKey(
        key_hash=uuid.uuid4().hex + "5" * 32,
        key_prefix="TEST_monitor_ok_bound",
        name="TEST_monitor_ok_monitor",
        role="readonly",
        purpose="monitor_config",
        shop_pk=monitor_shop,
    )
    db_session.add_all([general, monitor])
    db_session.commit()
    assert general.purpose == "general"
    assert monitor.shop_pk == monitor_shop


def test_migration_source_pins_the_purpose_domain() -> None:
    source = MIGRATION.read_text()
    assert "purpose IN ('general', 'monitor_config')" in source


# ─── f-8fb58645-aec：真实 Operations/MigrationContext 降级覆盖 ────


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0070_test", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table_names(conn, schema: str) -> set[str]:
    return set(inspect(conn).get_table_names(schema=schema))


def _column_names(conn, schema: str, table: str) -> set[str]:
    return {row["name"] for row in inspect(conn).get_columns(table, schema=schema)}


def _api_key_columns(conn) -> set[str]:
    return _column_names(conn, "security", "api_keys")


def _monitor_tables(conn) -> set[str]:
    return _table_names(conn, "config") & {
        "monitor_shop_state",
        "monitor_config_deliveries",
    }


def _insert_shop(conn) -> int:
    # pi-lens-ignore: python-sql-injection
    return conn.execute(
        text(
            "INSERT INTO commerce.shops (platform, shop_id, account_name) "
            "VALUES ('tiktok', :shop_id, 'TEST_monitor_downgrade') RETURNING id"
        ),
        {"shop_id": f"TEST_monitor_downgrade_{uuid.uuid4().hex[:12]}"},
    ).scalar_one()


def test_0070_downgrade_on_empty_projection_reverses_and_reapplies(
    db_engine,
) -> None:
    """空投影时 0070 必须能完整回滚并重新 apply（down/up 对称）。"""

    migration = _load_migration()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            assert _monitor_tables(conn) == {
                "monitor_shop_state",
                "monitor_config_deliveries",
            }
            assert {"purpose", "shop_pk"} <= _api_key_columns(conn)

            migration.downgrade()
            assert _monitor_tables(conn) == set()
            assert not {"purpose", "shop_pk"} & _api_key_columns(conn)

            migration.upgrade()
            assert _monitor_tables(conn) == {
                "monitor_shop_state",
                "monitor_config_deliveries",
            }
            assert {"purpose", "shop_pk"} <= _api_key_columns(conn)
            triggers = [
                row[0]
                for row in conn.execute(  # pi-lens-ignore: python-sql-injection
                    text(
                        "SELECT trigger_name FROM information_schema.triggers "
                        "WHERE event_object_schema='config' "
                        "AND event_object_table='monitor_shop_state'"
                    )
                ).all()
            ]
            assert triggers == ["trg_config_monitor_shop_state_touch"]
        finally:
            transaction.rollback()


def test_0070_downgrade_refuses_each_populated_data_guard(db_engine) -> None:
    """三道拒绝护栏必须逐一生效，且清除后能正常回滚。"""

    migration = _load_migration()
    with db_engine.connect() as conn:
        transaction = conn.begin()
        try:
            migration.__dict__["op"] = Operations(MigrationContext.configure(conn))
            shop_pk = _insert_shop(conn)

            # 1) 已发布交付行：不能靠 re-apply 重建，必须拒绝。
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text(
                    "INSERT INTO config.monitor_config_deliveries "
                    "(shop_pk, version, schema_version, content_hash, "
                    "encrypted_payload, created_by) "
                    "VALUES (:shop_pk, 1, 1, 'sha256:test', :payload, "
                    "'TEST_monitor_downgrade')"
                ),
                {"shop_pk": shop_pk, "payload": b"TEST_blob"},
            )
            savepoint = conn.begin_nested()
            with pytest.raises(Exception, match="monitor delivery rows exist"):
                migration.downgrade()
            savepoint.rollback()

            # 2) 已纳管店铺但无交付行：仍不能丢失纳管事实。
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text(
                    "DELETE FROM config.monitor_config_deliveries WHERE shop_pk=:pk"
                ),
                {"pk": shop_pk},
            )
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text("INSERT INTO config.monitor_shop_state (shop_pk) VALUES (:pk)"),
                {"pk": shop_pk},
            )
            savepoint = conn.begin_nested()
            with pytest.raises(Exception, match="onboarded monitor shops exist"):
                migration.downgrade()
            savepoint.rollback()

            # 3) 受限 api key：purpose/shop_pk 列即将消失，带绑定的凭证会成孤儿。
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text("DELETE FROM config.monitor_shop_state WHERE shop_pk=:pk"),
                {"pk": shop_pk},
            )
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text(
                    "INSERT INTO security.api_keys "
                    "(key_hash, key_prefix, name, role, status, purpose, shop_pk) "
                    "VALUES (:h, 'TEST_monitor_down', 'TEST_monitor_downgrade_key', "
                    "'readonly', 'active', 'monitor_config', :pk)"
                ),
                {"h": uuid.uuid4().hex * 2, "pk": shop_pk},
            )
            savepoint = conn.begin_nested()
            with pytest.raises(Exception, match="restricted api keys exist"):
                migration.downgrade()
            savepoint.rollback()

            # 清除事实后必须能真正回滚，而不是永久卡死。
            # pi-lens-ignore: python-sql-injection
            conn.execute(
                text("DELETE FROM security.api_keys WHERE name='TEST_monitor_downgrade_key'")
            )
            migration.downgrade()
            assert _monitor_tables(conn) == set()
            assert not {"purpose", "shop_pk"} & _api_key_columns(conn)
        finally:
            transaction.rollback()


# ─── Phase B：发布编排指针与 force 幂等 ─────────────────────────

def _complete_payload(**overrides: Any) -> dict[str, Any]:
    """Syntactically valid complete-settings payload (synthetic values)."""

    payload: dict[str, Any] = {
        "globalThreshold": "10.0",
        "autoCloseSkuOrderLimit": 0,
        "autoCloseMaxSkuOrders": 5,
        "autoCloseSingleConversionCostThreshold": "2.5",
        "autoCloseCampaignWhitelist": ["abc"],
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
        "campaignThresholds": {},
    }
    payload.update(overrides)
    return payload


def test_publish_advances_state_pointers_transactionally(
    db_session: Session, monitor_shop: int
) -> None:
    from tts_erp_v2.monitor_config.delivery import publish_delivery

    first = publish_delivery(
        db_session,
        shop_pk=monitor_shop,
        payload=_complete_payload(),
        source_global_version=4,
        source_shop_version=None,
        created_by="TEST_monitor_publish",
    )
    db_session.flush()
    state = db_session.get(MonitorShopState, monitor_shop)
    assert state is not None
    assert state.current_delivery_version == first
    # 普通发布不得推进 force 指针（§5.3）。
    assert state.last_force_version is None

    second = publish_delivery(
        db_session,
        shop_pk=monitor_shop,
        payload=_complete_payload(globalThreshold="11.0"),
        source_global_version=5,
        source_shop_version=2,
        created_by="TEST_monitor_publish",
    )
    db_session.flush()
    assert second == first + 1
    assert state.current_delivery_version == second
    assert state.last_force_version is None


def test_force_publish_advances_both_pointers(db_session: Session, monitor_shop: int) -> None:
    from tts_erp_v2.monitor_config.delivery import publish_delivery

    publish_delivery(
        db_session,
        shop_pk=monitor_shop,
        payload=_complete_payload(),
        source_global_version=4,
        source_shop_version=None,
        created_by="TEST_monitor_publish",
    )
    forced = publish_delivery(
        db_session,
        shop_pk=monitor_shop,
        payload=_complete_payload(),
        source_global_version=4,
        source_shop_version=None,
        created_by="TEST_monitor_publish",
        is_force=True,
        request_id=uuid.uuid4(),
        request_digest="sha256:force",
    )
    db_session.flush()
    state = db_session.get(MonitorShopState, monitor_shop)
    assert state is not None
    assert state.current_delivery_version == forced
    assert state.last_force_version == forced

    # 后续普通发布不得撤回强制指令（§5.4）。
    after = publish_delivery(
        db_session,
        shop_pk=monitor_shop,
        payload=_complete_payload(globalThreshold="12.0"),
        source_global_version=6,
        source_shop_version=None,
        created_by="TEST_monitor_publish",
    )
    db_session.flush()
    assert state.current_delivery_version == after
    assert state.last_force_version == forced


def test_same_request_id_replays_the_same_delivery(
    db_session: Session, monitor_shop: int
) -> None:
    from tts_erp_v2.monitor_config.delivery import publish_delivery

    request_id = uuid.uuid4()
    first = publish_delivery(
        db_session,
        shop_pk=monitor_shop,
        payload=_complete_payload(),
        source_global_version=4,
        source_shop_version=None,
        created_by="TEST_monitor_publish",
        request_id=request_id,
        request_digest="sha256:idem",
    )
    db_session.flush()
    replay = publish_delivery(
        db_session,
        shop_pk=monitor_shop,
        payload=_complete_payload(),
        source_global_version=4,
        source_shop_version=None,
        created_by="TEST_monitor_publish",
        request_id=request_id,
        request_digest="sha256:idem",
    )
    db_session.flush()
    assert replay == first
    rows = db_session.execute(
        select(MonitorConfigDelivery).where(
            MonitorConfigDelivery.shop_pk == monitor_shop
        )
    ).scalars().all()
    assert len(rows) == 1


def test_same_request_id_with_different_metadata_conflicts(
    db_session: Session, monitor_shop: int
) -> None:
    from tts_erp_v2.monitor_config.delivery import PublishConflict, publish_delivery

    request_id = uuid.uuid4()
    publish_delivery(
        db_session,
        shop_pk=monitor_shop,
        payload=_complete_payload(),
        source_global_version=4,
        source_shop_version=None,
        created_by="TEST_monitor_publish",
        request_id=request_id,
        request_digest="sha256:first",
    )
    db_session.flush()
    with pytest.raises(PublishConflict):
        publish_delivery(
            db_session,
            shop_pk=monitor_shop,
            payload=_complete_payload(),
            source_global_version=4,
            source_shop_version=None,
            created_by="TEST_monitor_publish",
            request_id=request_id,
            request_digest="sha256:second",
        )
    db_session.rollback()


def test_delivery_payload_is_encrypted_at_rest(db_session: Session, monitor_shop: int) -> None:
    """明文只经 token_service 进出，库里不得留明文。"""

    from tts_erp_v2.monitor_config.delivery import publish_delivery

    publish_delivery(
        db_session,
        shop_pk=monitor_shop,
        payload=_complete_payload(),
        source_global_version=4,
        source_shop_version=None,
        created_by="TEST_monitor_publish",
    )
    db_session.flush()
    row = db_session.execute(
        select(MonitorConfigDelivery).where(
            MonitorConfigDelivery.shop_pk == monitor_shop
        )
    ).scalar_one()
    assert b"globalThreshold" not in row.encrypted_payload


# ─── Phase B Terra 修复：发布前的校验与 force 前置条件 ───────────




def test_incomplete_payload_creates_no_row_and_no_state(
    db_session: Session, monitor_shop: int
) -> None:
    """校验必须早于加密与指针推进，非法载荷不得留下任何痕迹。"""

    from tts_erp_v2.monitor_config import contracts
    from tts_erp_v2.monitor_config.delivery import publish_delivery

    with pytest.raises(contracts.ConfigContractError):
        publish_delivery(
            db_session,
            shop_pk=monitor_shop,
            # 只给一个字段：不足以构成完整业务配置。
            payload={"globalThreshold": "10.0"},
            source_global_version=4,
            source_shop_version=None,
            created_by="TEST_monitor_publish",
        )
    db_session.rollback()
    assert db_session.execute(
        select(MonitorConfigDelivery).where(
            MonitorConfigDelivery.shop_pk == monitor_shop
        )
    ).scalars().all() == []
    assert db_session.get(MonitorShopState, monitor_shop) is None


def test_forbidden_wire_field_is_rejected_before_any_write(
    db_session: Session, monitor_shop: int
) -> None:
    """运行时字段绝不能被冻结进下发配置。"""

    from tts_erp_v2.monitor_config import contracts
    from tts_erp_v2.monitor_config.delivery import publish_delivery

    for forbidden in ("monitorEnabled", "cookie", "lastError"):
        with pytest.raises(contracts.ConfigContractError):
            publish_delivery(
                db_session,
                shop_pk=monitor_shop,
                payload=_complete_payload(**{forbidden: "leak"}),
                source_global_version=4,
                source_shop_version=None,
                created_by="TEST_monitor_publish",
            )
        db_session.rollback()
    assert db_session.execute(
        select(MonitorConfigDelivery).where(
            MonitorConfigDelivery.shop_pk == monitor_shop
        )
    ).scalars().all() == []
    assert db_session.get(MonitorShopState, monitor_shop) is None


def test_out_of_range_threshold_is_rejected_before_any_write(
    db_session: Session, monitor_shop: int
) -> None:
    from tts_erp_v2.monitor_config import contracts
    from tts_erp_v2.monitor_config.delivery import publish_delivery

    with pytest.raises(contracts.ConfigContractError):
        publish_delivery(
            db_session,
            shop_pk=monitor_shop,
            payload=_complete_payload(scanIntervalMinSeconds=5),
            source_global_version=4,
            source_shop_version=None,
            created_by="TEST_monitor_publish",
        )
    db_session.rollback()
    assert db_session.get(MonitorShopState, monitor_shop) is None


def test_payload_is_normalized_before_encryption(
    db_session: Session, monitor_shop: int
) -> None:
    """冻结的是规范化结果，不是调用方传入的原始形状。"""

    import json

    from tts_erp_v2.monitor_config.delivery import publish_delivery
    from tts_erp_v2.proxy import token_service

    publish_delivery(
        db_session,
        shop_pk=monitor_shop,
        payload=_complete_payload(
            autoCloseCampaignWhitelist=["abc", "def"],
            scanIntervalMinSeconds=30,
        ),
        source_global_version=4,
        source_shop_version=None,
        created_by="TEST_monitor_publish",
    )
    db_session.flush()
    row = db_session.execute(
        select(MonitorConfigDelivery).where(
            MonitorConfigDelivery.shop_pk == monitor_shop
        )
    ).scalar_one()
    stored = json.loads(token_service.decrypt(row.encrypted_payload))
    # Tuples normalize to JSON lists; Decimal thresholds serialize as strings.
    assert stored["autoCloseCampaignWhitelist"] == ["abc", "def"]
    assert stored["globalThreshold"] == "10.0"
    assert "sellerId" not in stored


def test_force_requires_an_existing_published_delivery(
    db_session: Session, monitor_shop: int
) -> None:
    """未发布草稿不得被强制覆盖隐式发布（§5.4）。"""

    from tts_erp_v2.monitor_config.delivery import (
        ForceRequiresPublishedDelivery,
        publish_delivery,
    )

    with pytest.raises(ForceRequiresPublishedDelivery):
        publish_delivery(
            db_session,
            shop_pk=monitor_shop,
            payload=_complete_payload(),
            source_global_version=4,
            source_shop_version=None,
            created_by="TEST_monitor_publish",
            is_force=True,
            request_id=uuid.uuid4(),
            request_digest="sha256:force",
        )
    db_session.rollback()
    assert db_session.get(MonitorShopState, monitor_shop) is None


def test_force_requires_a_request_id_and_digest(
    db_session: Session, monitor_shop: int
) -> None:
    """force 必须带幂等键，否则双击/重试无法安全重放（§5.4）。"""

    from tts_erp_v2.monitor_config.delivery import (
        ForceRequiresRequestId,
        publish_delivery,
    )

    _publish_one(db_session, monitor_shop)
    db_session.commit()

    with pytest.raises(ForceRequiresRequestId):
        publish_delivery(
            db_session,
            shop_pk=monitor_shop,
            payload=_complete_payload(),
            source_global_version=4,
            source_shop_version=None,
            created_by="TEST_monitor_publish",
            is_force=True,
            request_id=None,
            request_digest="sha256:force",
        )
    db_session.rollback()

    with pytest.raises(ForceRequiresRequestId):
        publish_delivery(
            db_session,
            shop_pk=monitor_shop,
            payload=_complete_payload(),
            source_global_version=4,
            source_shop_version=None,
            created_by="TEST_monitor_publish",
            is_force=True,
            request_id=uuid.uuid4(),
            request_digest=None,
        )
    db_session.rollback()

    state = db_session.get(MonitorShopState, monitor_shop)
    assert state is not None
    assert state.last_force_version is None


def _publish_one(session: Session, shop_pk: int, *, is_force: bool = False,
                 request_id: Any = None, request_digest: str | None = None) -> int:
    from tts_erp_v2.monitor_config.delivery import publish_delivery

    return publish_delivery(
        session,
        shop_pk=shop_pk,
        payload=_complete_payload(),
        source_global_version=4,
        source_shop_version=None,
        created_by="TEST_monitor_publish",
        is_force=is_force,
        request_id=request_id,
        request_digest=request_digest,
    )


def test_concurrent_retries_of_one_request_id_replay_a_single_delivery(
    db_engine,
) -> None:
    """同一 request_id 的并发重试必须只落一行并返回同一 version。

    重放复查在按店铺行锁之后进行，第二个事务因此看不到未提交的行，
    但会在拿到锁后读到已提交的那一行并直接重放。
    """

    import threading

    from tts_erp_v2.monitor_config.delivery import publish_delivery

    with Session(db_engine) as setup:
        shop = ChannelAccount(
            platform="tiktok",
            shop_id=f"TEST_monitor_concurrent_{uuid.uuid4().hex[:8]}",
            account_name="TEST_monitor_concurrent",
        )
        setup.add(shop)
        setup.commit()
        shop_pk = shop.id

    request_id = uuid.uuid4()
    barrier = threading.Barrier(2)
    results: list[Any] = []
    errors: list[BaseException] = []

    def _attempt() -> None:
        try:
            with Session(db_engine) as session:
                barrier.wait(timeout=10)
                results.append(
                    publish_delivery(
                        session,
                        shop_pk=shop_pk,
                        payload=_complete_payload(),
                        source_global_version=4,
                        source_shop_version=None,
                        created_by="TEST_monitor_concurrent",
                        request_id=request_id,
                        request_digest="sha256:concurrent",
                    )
                )
                session.commit()
        except BaseException as exc:  # noqa: BLE001 - reported to the main thread
            errors.append(exc)

    threads = [threading.Thread(target=_attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    try:
        assert not errors, f"concurrent publish raised: {errors!r}"
        assert len(results) == 2, results
        # 两次重试得到同一个 version，即只有一次真实写入。
        assert results[0] == results[1], results
        with Session(db_engine) as verify:
            rows = verify.execute(
                select(MonitorConfigDelivery).where(
                    MonitorConfigDelivery.shop_pk == shop_pk
                )
            ).scalars().all()
            assert len(rows) == 1
            state = verify.get(MonitorShopState, shop_pk)
            assert state is not None
            assert state.current_delivery_version == rows[0].version
    finally:
        with Session(db_engine) as cleanup:
            cleanup.execute(
                delete(MonitorConfigDelivery).where(
                    MonitorConfigDelivery.shop_pk == shop_pk
                )
            )
            cleanup.execute(
                delete(MonitorShopState).where(MonitorShopState.shop_pk == shop_pk)
            )
            cleanup.execute(
                delete(ChannelAccount).where(ChannelAccount.id == shop_pk)
            )
            cleanup.commit()
