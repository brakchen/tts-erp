"""Align publishing metadata and enforce attempt/cleanup invariants.

Revision ID: 0060_video_publish_invariants
Revises: 0059_publish_stage_started_at
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0060_video_publish_invariants"
down_revision: str | None = "0059_publish_stage_started_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        ALTER TABLE publishing.video_publish_attempts
        ADD CONSTRAINT video_publish_attempt_related_check CHECK (
            (kind = 'publish' AND related_attempt_id IS NULL) OR
            (kind = 'verify' AND related_attempt_id IS NOT NULL)
        )
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        ALTER TABLE publishing.video_publish_tasks
        ADD CONSTRAINT video_publish_task_cleanup_status_check CHECK (
            device_cleanup_status IN ('not_started','pending','succeeded','failed') AND
            spool_cleanup_status IN ('not_started','pending','succeeded','failed') AND
            object_cleanup_status IN ('not_started','pending','succeeded','failed')
        )
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'video_publish_attempts_task_id_sequence_no_key'
                  AND conrelid = 'publishing.video_publish_attempts'::regclass
            ) AND NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_video_publish_attempt_task_seq'
                  AND conrelid = 'publishing.video_publish_attempts'::regclass
            ) THEN
                ALTER TABLE publishing.video_publish_attempts
                RENAME CONSTRAINT video_publish_attempts_task_id_sequence_no_key
                TO uq_video_publish_attempt_task_seq;
            END IF;
        END $$
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        CREATE INDEX IF NOT EXISTS ix_video_publish_attempt_task_seq
        ON publishing.video_publish_attempts (task_id, sequence_no)
        """)
    )


def downgrade() -> None:
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("DROP INDEX IF EXISTS publishing.ix_video_publish_attempt_task_seq")
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        ALTER TABLE publishing.video_publish_tasks
        DROP CONSTRAINT IF EXISTS video_publish_task_cleanup_status_check
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        ALTER TABLE publishing.video_publish_attempts
        DROP CONSTRAINT IF EXISTS video_publish_attempt_related_check
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_video_publish_attempt_task_seq'
                  AND conrelid = 'publishing.video_publish_attempts'::regclass
            ) AND NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'video_publish_attempts_task_id_sequence_no_key'
                  AND conrelid = 'publishing.video_publish_attempts'::regclass
            ) THEN
                ALTER TABLE publishing.video_publish_attempts
                RENAME CONSTRAINT uq_video_publish_attempt_task_seq
                TO video_publish_attempts_task_id_sequence_no_key;
            END IF;
        END $$
        """)
    )
