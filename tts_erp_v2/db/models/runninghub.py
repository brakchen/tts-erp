"""Durable RunningHub generation state. Remote IDs are always text."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tts_erp_v2.db.base import Base


def new_id() -> str:
    return str(uuid4())


class Record:
    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class WorkflowVersion(Record, Base):
    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "content_hash"),
        {"schema": "runninghub"},
    )
    workflow_id: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    graph: Mapped[dict[str, Any]] = mapped_column(JSONB)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB)


class GenerationProfile(Record, Base):
    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("concurrency BETWEEN 1 AND 5"),
        {"schema": "runninghub"},
    )
    credential_ref: Mapped[str] = mapped_column(Text, default="env:RUNNINGHUB_API_KEY")
    workflow_id: Mapped[str] = mapped_column(Text, default="2111762608670990337")
    instance_type: Mapped[str] = mapped_column(Text, default="plus")
    concurrency: Mapped[int] = mapped_column(Integer, default=5)
    workflow_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("runninghub.workflow_versions.id")
    )


class VideoTemplate(Record, Base):
    __tablename__ = "templates"
    __table_args__ = ({"schema": "runninghub"},)
    human_id: Mapped[int] = mapped_column(ForeignKey("media.digital_humans.id"))
    name: Mapped[str] = mapped_column(Text)
    # Immutable reference snapshot; editing creates another template ID.
    clips: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


class GenerationBatch(Record, Base):
    __tablename__ = "batches"
    __table_args__ = ({"schema": "runninghub"},)
    request_hash: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text, default="dry-run")
    status: Mapped[str] = mapped_column(Text, default="PLANNED")
    error: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[str | None] = mapped_column(Text)


class GenerationJob(Record, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("batch_id", "identity"),
        Index("ix_rh_jobs_dispatch", "status", "created_at"),
        {"schema": "runninghub"},
    )
    batch_id: Mapped[str] = mapped_column(ForeignKey("runninghub.batches.id"))
    identity: Mapped[str] = mapped_column(Text)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    seed: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, default="PLANNED")
    error: Mapped[str | None] = mapped_column(Text)
    current_attempt_id: Mapped[str | None] = mapped_column(Text)


class CloudQuota(Base):
    __tablename__ = "quotas"
    __table_args__ = (
        CheckConstraint("limit_count BETWEEN 1 AND 5"),
        {"schema": "runninghub"},
    )
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    limit_count: Mapped[int] = mapped_column(Integer, default=5)
    paused_reason: Mapped[str | None] = mapped_column(Text)


class GenerationAttempt(Record, Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "number"),
        UniqueConstraint("task_id"),
        Index("ix_rh_attempts_quota", "quota_id", "reserved"),
        {"schema": "runninghub"},
    )
    job_id: Mapped[str] = mapped_column(ForeignKey("runninghub.jobs.id"))
    number: Mapped[int] = mapped_column(Integer)
    quota_id: Mapped[str] = mapped_column(ForeignKey("runninghub.quotas.id"))
    reserved: Mapped[bool] = mapped_column(Boolean, default=True)
    task_id: Mapped[str | None] = mapped_column(Text)
    cloud_status: Mapped[str | None] = mapped_column(Text)
    request_hash: Mapped[str | None] = mapped_column(Text)
    receipt: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    asset_hashes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    lease_token: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[datetime | None] = mapped_column()
    next_poll_at: Mapped[datetime | None] = mapped_column()
    last_polled_at: Mapped[datetime | None] = mapped_column()
    poll_failures: Mapped[int] = mapped_column(Integer, default=0)
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AssetUpload(Record, Base):
    __tablename__ = "asset_uploads"
    __table_args__ = (UniqueConstraint("quota_id", "sha256"), {"schema": "runninghub"})
    quota_id: Mapped[str] = mapped_column(ForeignKey("runninghub.quotas.id"))
    sha256: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column()


class GenerationArtifact(Record, Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("attempt_id", "kind"), {"schema": "runninghub"})
    attempt_id: Mapped[str] = mapped_column(ForeignKey("runninghub.attempts.id"))
    kind: Mapped[str] = mapped_column(Text)
    bucket: Mapped[str] = mapped_column(Text)
    object_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
