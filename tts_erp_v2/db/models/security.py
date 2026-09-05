"""security.* — API key storage.

1 table: api_keys. SHA-256 hashed key only; plaintext is printed ONCE
on creation. Role ∈ {readonly, readwrite, admin}. The auth middleware
buckets requests by (api_key, role).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        # Lookup is by SHA-256 hash (used in middleware); keep an index on it.
        Index("ix_api_keys_key_hash", "key_hash", unique=True),
        Index("ix_api_keys_role", "role"),
        {"schema": "security"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)  # sha256 hex
    key_prefix: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # e.g. ttserp_rw_abc...  — for human-friendly listing
    name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # readonly | readwrite | admin
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'active'")
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    rotated_to_key_hash: Mapped[str | None] = mapped_column(Text)
