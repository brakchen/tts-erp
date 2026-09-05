"""integration.* — raw API captures, sync bookkeeping, credentials.

5 tables: credentials / raw_records / sync_jobs / sync_cursors / sync_issues.

All times timestamptz, internal PK bigint identity, external ids text.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


# ─── credentials ──────────────────────────────────────────────────────
# Single-table credentials store. TikTok shop auth OR a miaoshou license
# = one row. ciphertext is bytea (Fernet envelope), plaintext is held only
# in process memory inside token_service.
class Credentials(Base):
    __tablename__ = "credentials"
    __table_args__ = (
        UniqueConstraint(
            "provider", "external_account_id", name="uq_credentials_provider_account"
        ),
        Index("ix_credentials_provider", "provider"),
        {"schema": "integration"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)  # 'tiktok' | 'miaoshou'
    external_account_id: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # shop_id | licenseId
    account_label: Mapped[str | None] = mapped_column(Text)
    ciphertext: Mapped[bytes] = mapped_column(nullable=False)
    # TikTok-specific:
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    granted_scopes: Mapped[list | None] = mapped_column(JSONB)
    # Miaoshou-specific (kept here so the table stays single):
    company_secret_ciphertext: Mapped[bytes | None]
    extra: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )


# ─── raw_records ──────────────────────────────────────────────────────
class RawRecord(Base):
    """Full original API JSON. Normalized tables reference its id."""

    __tablename__ = "raw_records"
    __table_args__ = (
        Index("ix_raw_records_endpoint_account", "endpoint", "credential_id"),
        Index("ix_raw_records_external_id", "external_id"),
        Index("ix_raw_records_captured_at", "captured_at"),
        {"schema": "integration"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    credential_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("integration.credentials.id", use_alter=True, ondelete="SET NULL"),
    )
    endpoint: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # e.g. 'tiktok.order.search'
    external_id: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str | None] = mapped_column(
        String(64)
    )  # sha256 hex of payload
    synced_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )

    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


# ─── sync_jobs ────────────────────────────────────────────────────────
class SyncJob(Base):
    """One row per job run. Lifecycle: status='running' → 'succeeded'/'failed'."""

    __tablename__ = "sync_jobs"
    __table_args__ = (
        Index("ix_sync_jobs_name_started", "job_name", "started_at"),
        {"schema": "integration"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    job_name: Mapped[str] = mapped_column(Text, nullable=False)
    credential_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("integration.credentials.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    finished_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'running'")
    )
    rows_total: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    rows_inserted: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    rows_updated: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    rows_failed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )


# ─── sync_cursors ─────────────────────────────────────────────────────
class SyncCursor(Base):
    """Watermark per (job_name, scope) for incremental jobs."""

    __tablename__ = "sync_cursors"
    __table_args__ = (
        UniqueConstraint("job_name", "scope", name="uq_sync_cursors_job_scope"),
        {"schema": "integration"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    job_name: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # e.g. shop_id, 'all', etc.
    cursor_value: Mapped[str | None] = mapped_column(Text)
    cursor_epoch_ms: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )


# ─── sync_issues ──────────────────────────────────────────────────────
class SyncIssue(Base):
    """Per-row parse failures / unresolved foreign keys. Job does NOT block on these."""

    __tablename__ = "sync_issues"
    __table_args__ = (
        Index("ix_sync_issues_job_resolved", "job_name", "resolved_at"),
        {"schema": "integration"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    job_name: Mapped[str] = mapped_column(Text, nullable=False)
    issue_type: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)
    detected_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()")
    )
