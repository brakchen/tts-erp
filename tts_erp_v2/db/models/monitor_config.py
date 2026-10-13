"""config.monitor_* — monitor 按店铺同步 ERP 配置的交付投影。

对应 docs/monitor-config-sync.md §5.2。既有真相仍是
``config.runtime_config_items`` / ``_revisions`` / ``_secrets``（草稿、发布、
回滚、secret 加密）；本模块只保存**发布时冻结**的店铺交付快照，不参与草稿。

2 tables:
  monitor_shop_state        每店一行；版本分配与发布的行锁锚点。
  monitor_config_deliveries 不可变的完整店铺配置快照，``(shop_pk, version)`` 唯一。

明文只经 ``tts_erp_v2.proxy.token_service`` 加解密；``encrypted_payload`` 永不
写入明文，日志与审计也不得回读。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


class MonitorShopState(Base):
    """Per-shop delivery pointers and the row-lock anchor for publishing.

    Row created when a shop is first onboarded. ``current_delivery_version`` /
    ``last_force_version`` stay NULL while the shop has no published delivery;
    the delivery GET answers 404 in that state rather than inventing one.

    The version pointers carry no foreign key to ``monitor_config_deliveries``:
    publishing locks this row first, then writes the delivery and advances the
    pointer in one transaction, and history rows are never deleted. A FK would
    only constrain the lock ordering that §5.3 depends on.
    """

    __tablename__ = "monitor_shop_state"
    __table_args__ = ({"schema": "config"},)

    shop_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commerce.shops.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    current_delivery_version: Mapped[int | None] = mapped_column(BigInteger)
    last_force_version: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MonitorConfigDelivery(Base):
    """One immutable, fully composed shop configuration snapshot.

    ``version`` doubles as the delivery identifier; a force directive reuses the
    version of the ``is_force=True`` delivery it points at rather than keeping a
    separate counter. ``source_shop_version`` is NULL when the shop inherits the
    global revision and has no override of its own.

    Rows are append-only. ``request_id``/``request_digest`` make a publish
    operation idempotent per shop; the digest covers non-sensitive request
    metadata only.
    """

    __tablename__ = "monitor_config_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "shop_pk", "version", name="uq_monitor_config_deliveries_shop_version"
        ),
        UniqueConstraint(
            "shop_pk",
            "request_id",
            name="uq_monitor_config_deliveries_shop_request_id",
        ),
        Index("ix_monitor_config_deliveries_shop_force", "shop_pk", "is_force"),
        {"schema": "config"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    shop_pk: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("commerce.shops.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_global_version: Mapped[int | None] = mapped_column(BigInteger)
    source_shop_version: Mapped[int | None] = mapped_column(BigInteger)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)  # sha256:<64 hex>
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    is_force: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)  # opaque actor id
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    request_digest: Mapped[str | None] = mapped_column(Text)
