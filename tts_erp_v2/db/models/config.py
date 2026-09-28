"""config schema — 可配置的枚举映射 / 元数据。

当前仅 config.enum_map 一张表：SPU ROI 钻取面板枚举值中文化映射。
未来其他可配置项（佣金费率基线、标色阈值等）也可放此 schema。
"""

from __future__ import annotations

from sqlalchemy import Index, String, Text, UniqueConstraint
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
