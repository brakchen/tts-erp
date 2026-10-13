"""monitor 配置的持久化投影：api_keys 受限用途列 + 店铺 state/delivery 表。

契约来源：docs/monitor-config-sync.md §4.2（受限 Token）与 §5.2（交付投影表）。
阶段 A 只固定 schema 与迁移；阶段 B 才写入 delivery 行。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text
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
