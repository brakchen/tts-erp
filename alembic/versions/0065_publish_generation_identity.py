"""Fence upload generations and complete immutable attempt identity.

Revision ID: 0065_publish_generation_identity
Revises: 0064_publish_spool_ownership
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0065_publish_generation_identity"
down_revision: str | None = "0064_publish_spool_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "video_publish_tasks",
        sa.Column(
            "object_generation",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        schema="publishing",
    )
    op.add_column(
        "video_publish_tasks",
        sa.Column("object_upload_expires_at", sa.DateTime(timezone=True)),
        schema="publishing",
    )
    op.create_unique_constraint(
        "uq_video_publish_object_generation",
        "video_publish_tasks",
        ["object_generation"],
        schema="publishing",
    )
    op.add_column(
        "video_publish_attempts",
        sa.Column(
            "target_app_package",
            sa.Text(),
            server_default=sa.text("'com.zhiliaoapp.musically'"),
        ),
        schema="publishing",
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_attempts AS attempt
        SET target_app_package = task.target_app_package
        FROM publishing.video_publish_tasks AS task
        WHERE task.id = attempt.task_id
        """)
    )
    op.alter_column(
        "video_publish_attempts",
        "target_app_package",
        nullable=False,
        schema="publishing",
    )

    # Extend the existing forward-only audit fence; do not rewrite migration 0062.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        CREATE OR REPLACE FUNCTION publishing.fn_immutable_video_publish_attempt_identity()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.task_id IS DISTINCT FROM OLD.task_id
               OR NEW.sequence_no IS DISTINCT FROM OLD.sequence_no
               OR NEW.kind IS DISTINCT FROM OLD.kind
               OR NEW.related_attempt_id IS DISTINCT FROM OLD.related_attempt_id
               OR NEW.artemis_session_id IS DISTINCT FROM OLD.artemis_session_id
               OR NEW.prompt_version IS DISTINCT FROM OLD.prompt_version
               OR NEW.prompt_snapshot IS DISTINCT FROM OLD.prompt_snapshot
               OR NEW.device_serial IS DISTINCT FROM OLD.device_serial
               OR NEW.device_path IS DISTINCT FROM OLD.device_path
               OR NEW.target_app_package IS DISTINCT FROM OLD.target_app_package THEN
                RAISE EXCEPTION
                    'video publish attempt identity fields are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DROP TRIGGER trg_video_publish_attempt_identity
        ON publishing.video_publish_attempts;
        CREATE TRIGGER trg_video_publish_attempt_identity
        BEFORE UPDATE OF task_id, sequence_no, kind, related_attempt_id,
                         artemis_session_id, prompt_version, prompt_snapshot,
                         device_serial, device_path, target_app_package
        ON publishing.video_publish_attempts
        FOR EACH ROW
        EXECUTE FUNCTION publishing.fn_immutable_video_publish_attempt_identity()
        """)
    )


def downgrade() -> None:
    # Do not discard generation or submission identity for state-bearing rows.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM publishing.video_publish_tasks LIMIT 1)
               OR EXISTS (SELECT 1 FROM publishing.video_publish_attempts LIMIT 1) THEN
                RAISE EXCEPTION '0065 downgrade refused: publishing state is not empty';
            END IF;
        END $$
        """)
    )
    # Restore the predecessor trigger shape only for an empty schema.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DROP TRIGGER trg_video_publish_attempt_identity
        ON publishing.video_publish_attempts;
        CREATE OR REPLACE FUNCTION publishing.fn_immutable_video_publish_attempt_identity()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.task_id IS DISTINCT FROM OLD.task_id
               OR NEW.sequence_no IS DISTINCT FROM OLD.sequence_no
               OR NEW.kind IS DISTINCT FROM OLD.kind
               OR NEW.related_attempt_id IS DISTINCT FROM OLD.related_attempt_id
               OR NEW.artemis_session_id IS DISTINCT FROM OLD.artemis_session_id THEN
                RAISE EXCEPTION
                    'video publish attempt identity fields are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER trg_video_publish_attempt_identity
        BEFORE UPDATE OF task_id, sequence_no, kind, related_attempt_id,
                         artemis_session_id
        ON publishing.video_publish_attempts
        FOR EACH ROW
        EXECUTE FUNCTION publishing.fn_immutable_video_publish_attempt_identity()
        """)
    )
    op.drop_column("video_publish_attempts", "target_app_package", schema="publishing")
    op.drop_constraint(
        "uq_video_publish_object_generation",
        "video_publish_tasks",
        schema="publishing",
        type_="unique",
    )
    op.drop_column(
        "video_publish_tasks", "object_upload_expires_at", schema="publishing"
    )
    op.drop_column("video_publish_tasks", "object_generation", schema="publishing")
