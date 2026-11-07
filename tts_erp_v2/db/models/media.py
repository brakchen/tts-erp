"""SQLAlchemy models for the global media asset library.

3 tables in the ``media`` schema:

- ``media.assets`` — one uploaded image or video (the source of truth for the
  object store binding).
- ``media.digital_humans`` — one persona record ("人设档案").
- ``media.digital_human_assets`` — the many-to-many link between a persona and
  the assets it references. An asset may be unattached (global pool) or shared
  by several personas.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


class MediaAsset(Base):
    """One uploaded image or video stored in the media bucket."""

    __tablename__ = "media_assets"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('image','video')",
            name="media_asset_kind_check",
        ),
        CheckConstraint(
            "status IN ('awaiting_upload','ready','failed')",
            name="media_asset_status_check",
        ),
        CheckConstraint("size_bytes > 0", name="media_asset_size_check"),
        Index("ix_media_assets_status", "status"),
        Index("ix_media_assets_created", text("created_at DESC"), text("id DESC")),
        {"schema": "media"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'awaiting_upload'")
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    is_portrait: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    uploaded_by_key_id: Mapped[str | None] = mapped_column(Text)
    uploaded_by_prefix: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class DigitalHuman(Base):
    """One persona record that groups a set of media assets."""

    __tablename__ = "digital_humans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','archived')",
            name="digital_human_status_check",
        ),
        Index("ix_digital_humans_status", "status"),
        Index("ix_digital_humans_name", "name"),
        {"schema": "media"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'")
    )
    created_by: Mapped[str | None] = mapped_column(Text)
    portrait_asset_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("media.media_assets.id", name="fk_digital_humans_portrait_asset"),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    deleted_at: Mapped[datetime | None]


class DigitalHumanAsset(Base):
    """Link row: one persona references one asset (many-to-many)."""

    __tablename__ = "digital_human_assets"
    __table_args__ = (
        UniqueConstraint(
            "digital_human_id",
            "asset_id",
            name="uq_digital_human_assets_pair",
        ),
        Index("ix_digital_human_assets_asset", "asset_id"),
        {"schema": "media"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    digital_human_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("media.digital_humans.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("media.media_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


__all__ = [
    "DigitalHuman",
    "DigitalHumanAsset",
    "MediaAsset",
]
