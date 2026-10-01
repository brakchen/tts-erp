"""config schema — 可配置的枚举映射 / 元数据。

当前仅 config.enum_map 一张表：SPU ROI 钻取面板枚举值中文化映射。
未来其他可配置项（佣金费率基线、标色阈值等）也可放此 schema。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


class EnumMap(Base):
    """枚举值中文化映射（config.enum_map）。

    enum_type 分类：order_status / case_type / case_status /
    settle_component / cost_source / shipment_status / column_header。
    """

    __tablename__ = "enum_map"
    __table_args__ = (
        UniqueConstraint("enum_type", "enum_value", name="uq_enum_map_type_value"),
        Index("ix_enum_map_type", "enum_type"),
        {"schema": "config"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    enum_type: Mapped[str] = mapped_column(String(64), nullable=False)
    enum_value: Mapped[str] = mapped_column(String(128), nullable=False)
    label_zh: Mapped[str] = mapped_column(String(256), nullable=False)
    sort_order: Mapped[int] = mapped_column(default=0, server_default="0")


class RuntimeConfigItem(Base):
    """One versioned runtime configuration key and its mutable draft."""

    __tablename__ = "runtime_config_items"
    __table_args__ = ({"schema": "config"},)

    config_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    json_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    draft_payload: Mapped[dict | None] = mapped_column(JSONB)
    draft_rollout: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_version: Mapped[int | None] = mapped_column(Integer)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RuntimeConfigRevision(Base):
    """Immutable published payload for a runtime configuration key."""

    __tablename__ = "runtime_config_revisions"
    __table_args__ = (
        UniqueConstraint("config_key", "version", name="uq_runtime_config_revision"),
        Index("ix_runtime_config_revisions_key_version", "config_key", "version"),
        {"schema": "config"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    config_key: Mapped[str] = mapped_column(
        ForeignKey("config.runtime_config_items.config_key", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rollout: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    comment: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RuntimeConfigSecret(Base):
    """Encrypted generic secret referenced by ``secret://<name>`` values."""

    __tablename__ = "runtime_config_secrets"
    __table_args__ = ({"schema": "config"},)

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
