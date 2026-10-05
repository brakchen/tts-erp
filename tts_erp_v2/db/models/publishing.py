"""SQLAlchemy models for the TikTok video publishing workflow."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
        CheckConstraint(
            "status IN ('pending','running','succeeded','failed','needs_review','cancelled')",
            name="video_publish_task_status_check",
        ),
        CheckConstraint(
            "stage IN ('awaiting_upload','queued','waiting_device','downloading',"
            "'staging_device','dispatching_artemis','waiting_artemis','verifying',"
            "'cleaning','done')",
            name="video_publish_task_stage_check",
        ),
        CheckConstraint("size_bytes > 0", name="video_publish_task_size_check"),
        CheckConstraint(
            "cleanup_intent IN ('none','finalize_success','requeue_publish','preserve_state')",
            name="video_publish_task_cleanup_intent_check",
        ),
        CheckConstraint(
            "(status = 'pending' AND stage IN ('awaiting_upload','queued','waiting_device')) OR "
            "(status = 'running' AND stage IN ('downloading','staging_device',"
            "'dispatching_artemis','waiting_artemis','verifying')) OR "
            "(status IN ('succeeded','failed','needs_review','cancelled') AND stage = 'done')",
            name="video_publish_task_status_stage_check",
        ),
        CheckConstraint(
            "(cleanup_intent = 'none' AND cleanup_lease_owner IS NULL AND "
            "cleanup_lease_expires_at IS NULL AND ((status = 'running' AND "
            "lease_owner IS NOT NULL AND stage IN ('downloading','staging_device',"
            "'dispatching_artemis','waiting_artemis','verifying') AND "
            "object_cleanup_status NOT IN ('pending','failed')) OR "
            "(device_cleanup_status NOT IN ('pending','failed') AND "
            "spool_cleanup_status NOT IN ('pending','failed') AND "
            "object_cleanup_status NOT IN ('pending','failed')))) OR "
            "(cleanup_intent = 'finalize_success' AND status = 'succeeded' AND stage = 'done') OR "
            "(cleanup_intent = 'requeue_publish' AND status = 'pending' AND "
            "stage IN ('queued','waiting_device') AND "
            "object_cleanup_status NOT IN ('pending','failed')) OR "
            "(cleanup_intent = 'preserve_state' AND "
            "status IN ('succeeded','failed','needs_review','cancelled') AND stage = 'done')",
            name="video_publish_task_cleanup_owner_check",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND publish_budget_used >= 0 "
            "AND publish_budget_used <= attempt_count",
            name="video_publish_task_budget_check",
        ),
        Index(
            "ix_video_publish_queue",
            "next_attempt_at",
            "queued_at",
            "id",
            postgresql_where=text(
                "status = 'pending' AND stage IN ('queued','waiting_device')"
            ),
        ),
        Index(
            "ix_video_publish_history",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_video_publish_cleanup_queue",
            "cleanup_lease_expires_at",
            "id",
            postgresql_where=text(
                "cleanup_intent <> 'none' AND "
                "(device_cleanup_status IN ('pending','failed') OR "
                "spool_cleanup_status IN ('pending','failed') OR "
                "object_cleanup_status IN ('pending','failed'))"
            ),
        ),
        CheckConstraint(
            "device_cleanup_status IN ('not_started','pending','succeeded','failed') "
            "AND spool_cleanup_status IN ('not_started','pending','succeeded','failed') "
            "AND object_cleanup_status IN ('not_started','pending','succeeded','failed')",
            name="video_publish_task_cleanup_status_check",
        ),
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
    created_by_key_hash: Mapped[str | None] = mapped_column(Text)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    object_filename: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'video.mp4'")
    )
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_bucket: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    object_etag: Mapped[str | None] = mapped_column(Text)
    object_sha256: Mapped[str | None] = mapped_column(Text)
    object_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    object_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_intent: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'none'")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    stage: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'awaiting_upload'")
    )
    stage_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    publish_budget_used: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_lease_owner: Mapped[str | None] = mapped_column(Text)
    cleanup_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    cleanup_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    row_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    target_device_serial: Mapped[str] = mapped_column(Text, nullable=False)
    target_app_package: Mapped[str] = mapped_column(Text, nullable=False)
    device_path: Mapped[str | None] = mapped_column(Text)
    spool_path: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(Text)
    last_error_message: Mapped[str | None] = mapped_column(Text)
    device_cleanup_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'not_started'")
    )
    device_cleanup_error: Mapped[str | None] = mapped_column(Text)
    device_cleanup_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    device_cleanup_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    spool_cleanup_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'not_started'")
    )
    spool_cleanup_error: Mapped[str | None] = mapped_column(Text)
    spool_cleanup_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    spool_cleanup_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    object_cleanup_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'not_started'")
    )
    object_cleanup_error: Mapped[str | None] = mapped_column(Text)
    object_cleanup_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    object_cleanup_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
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
        CheckConstraint(
            "kind IN ('publish','verify')",
            name="video_publish_attempt_kind_check",
        ),
        CheckConstraint(
            "status IN ('created','submitting','queued','running','success','failed',"
            "'rejected','cancelled','unknown')",
            name="video_publish_attempt_status_check",
        ),
        UniqueConstraint(
            "task_id", "sequence_no", name="uq_video_publish_attempt_task_seq"
        ),
        CheckConstraint(
            "(kind = 'publish' AND related_attempt_id IS NULL) OR "
            "(kind = 'verify' AND related_attempt_id IS NOT NULL)",
            name="video_publish_attempt_related_check",
        ),
        Index(
            "ix_video_publish_attempt_task_seq",
            "task_id",
            text("sequence_no DESC"),
        ),
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
            postgresql_where=text(
                "status IN ('created','submitting','queued','running','unknown')"
            ),
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
    __table_args__ = (
        CheckConstraint(
            "status IN ('starting','ready','stopping')",
            name="worker_heartbeat_status_check",
        ),
        CheckConstraint(
            "device_status IN ('ready','busy','offline','locked','unknown')",
            name="worker_heartbeat_device_status_check",
        ),
        {"schema": "publishing"},
    )
    instance_id: Mapped[str] = mapped_column(Text, primary_key=True)
    hostname: Mapped[str] = mapped_column(Text, nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str | None] = mapped_column(Text)
    device_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unknown'")
    )
    device_message: Mapped[str | None] = mapped_column(Text)
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
