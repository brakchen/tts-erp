"""SQLAlchemy models for the TikTok video publishing workflow."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tts_erp_v2.db.base import Base


class VideoPublishTask(Base):
    __tablename__ = "video_publish_tasks"
    __table_args__ = (
        Index(
            "ix_video_publish_queue",
            "next_attempt_at",
            "queued_at",
            "id",
            postgresql_where=text(
                "status = 'pending' AND stage IN ('queued','waiting_device')"
            ),
        ),
        Index("ix_video_publish_history", "created_at", "id"),
        Index(
            "uq_video_publish_one_running",
            text("(1)"),
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        {"schema": "publishing"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    public_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        unique=True,
        nullable=False,
        server_default=text("gen_random_uuid()"),
    )
    client_request_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), unique=True, nullable=False
    )
    created_by_user_id: Mapped[int | None] = mapped_column(BigInteger)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_bucket: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    object_etag: Mapped[str | None] = mapped_column(Text)
    object_sha256: Mapped[str | None] = mapped_column(Text)
    object_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    object_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    stage: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'awaiting_upload'")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    target_device_serial: Mapped[str] = mapped_column(Text, nullable=False)
    target_app_package: Mapped[str] = mapped_column(Text, nullable=False)
    device_path: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(Text)
    last_error_message: Mapped[str | None] = mapped_column(Text)
    device_cleanup_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'not_started'")
    )
    device_cleanup_error: Mapped[str | None] = mapped_column(Text)
    spool_cleanup_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'not_started'")
    )
    spool_cleanup_error: Mapped[str | None] = mapped_column(Text)
    object_cleanup_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'not_started'")
    )
    object_cleanup_error: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    attempts: Mapped[list[VideoPublishAttempt]] = relationship(
        back_populates="task",
        foreign_keys="VideoPublishAttempt.task_id",
        order_by="VideoPublishAttempt.sequence_no",
    )


class VideoPublishAttempt(Base):
    __tablename__ = "video_publish_attempts"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "sequence_no", name="uq_video_publish_attempt_task_seq"
        ),
        Index("ix_video_publish_attempt_task_seq", "task_id", "sequence_no"),
        Index(
            "ix_video_publish_attempt_status",
            "status",
            "updated_at",
            postgresql_where=text(
                "status IN ('created','submitting','queued','running','unknown')"
            ),
        ),
        Index(
            "uq_video_publish_task_active_attempt",
            "task_id",
            unique=True,
            postgresql_where=text("status IN ('submitting','queued','running')"),
        ),
        {"schema": "publishing"},
    )
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        server_default=text("generate_always_as_identity()"),
    )
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("publishing.video_publish_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    related_attempt_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("publishing.video_publish_attempts.id", ondelete="RESTRICT"),
    )
    artemis_session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'created'")
    )
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    device_serial: Mapped[str] = mapped_column(Text, nullable=False)
    device_path: Mapped[str | None] = mapped_column(Text)
    artemis_output: Mapped[dict | None] = mapped_column(JSONB)
    artemis_error: Mapped[str | None] = mapped_column(Text)
    steps_count: Mapped[int | None] = mapped_column(Integer)
    submit_retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_classification: Mapped[str | None] = mapped_column(Text)
    retry_safe: Mapped[bool | None] = mapped_column(Boolean)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    task: Mapped[VideoPublishTask] = relationship(
        back_populates="attempts", foreign_keys=[task_id]
    )


class PublishWorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    __table_args__ = ({"schema": "publishing"},)
    instance_id: Mapped[str] = mapped_column(Text, primary_key=True)
    hostname: Mapped[str] = mapped_column(Text, nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


__all__ = ["PublishWorkerHeartbeat", "VideoPublishAttempt", "VideoPublishTask"]
