"""Fence execution resources and historical upload capabilities.

Revision ID: 0066_publish_execution_fences
Revises: 0065_publish_generation_identity
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0066_publish_execution_fences"
down_revision: str | None = "0065_publish_generation_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A 0065 worker may already own unversioned spool/device paths or an
    # in-flight Artemis session. Neither identity can be reconstructed after
    # the fact, so deployment must drain these rows instead of guessing.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM publishing.video_publish_tasks
                WHERE status = 'running'
            ) THEN
                RAISE EXCEPTION
                    '0066 upgrade refused: drain every running task before migration';
            END IF;
            IF EXISTS (
                SELECT 1 FROM publishing.video_publish_attempts
                WHERE status IN ('created','submitting','queued','running','unknown')
            ) THEN
                RAISE EXCEPTION
                    '0066 upgrade refused: resolve every active attempt before migration';
            END IF;
            IF EXISTS (
                SELECT 1 FROM publishing.video_publish_tasks
                WHERE spool_path IS NOT NULL OR device_path IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    '0066 upgrade refused: reconcile every legacy spool/device path before migration';
            END IF;
            IF EXISTS (
                SELECT 1 FROM publishing.video_publish_tasks
                WHERE object_upload_expires_at IS NULL
                  AND object_deleted_at IS NOT NULL
                  AND (status NOT IN ('succeeded','failed','needs_review','cancelled')
                       OR stage <> 'done')
            ) THEN
                RAISE EXCEPTION
                    '0066 upgrade refused: reconcile nonterminal deleted object generation before migration';
            END IF;
        END $$
        """)
    )
    op.add_column(
        "video_publish_tasks",
        sa.Column("execution_generation", postgresql.UUID(as_uuid=True)),
        schema="publishing",
    )
    op.create_unique_constraint(
        "uq_video_publish_execution_generation",
        "video_publish_tasks",
        ["execution_generation"],
        schema="publishing",
    )
    # 0053–0064 did not persist PUT-ticket expiry. Fence every generation that
    # reaches 0066 without one, including a generation previously considered
    # deleted: an old ticket may complete after that observation. Previously
    # deleted terminal generations are reopened for generation-bound,
    # idempotent selector cleanup after the maximum TTL plus selector grace.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        WITH historical_fence AS (
            SELECT clock_timestamp() + interval '7 days' AS expires_at
        )
        UPDATE publishing.video_publish_tasks AS task
        SET object_upload_expires_at = historical_fence.expires_at,
            object_deleted_at = CASE
                WHEN task.object_deleted_at IS NOT NULL THEN NULL
                ELSE task.object_deleted_at
            END,
            cleanup_intent = CASE
                WHEN task.object_deleted_at IS NOT NULL THEN 'preserve_state'
                ELSE task.cleanup_intent
            END,
            object_cleanup_status = CASE
                WHEN task.object_deleted_at IS NOT NULL THEN 'pending'
                ELSE task.object_cleanup_status
            END,
            object_cleanup_error = CASE
                WHEN task.object_deleted_at IS NOT NULL
                    THEN 'HISTORICAL_PUT_CAPABILITY_REOPENED'
                ELSE task.object_cleanup_error
            END,
            object_cleanup_next_attempt_at = CASE
                WHEN task.object_deleted_at IS NOT NULL
                    THEN historical_fence.expires_at
                ELSE task.object_cleanup_next_attempt_at
            END,
            cleanup_lease_owner = CASE
                WHEN task.object_deleted_at IS NOT NULL THEN NULL
                ELSE task.cleanup_lease_owner
            END,
            cleanup_lease_expires_at = CASE
                WHEN task.object_deleted_at IS NOT NULL THEN NULL
                ELSE task.cleanup_lease_expires_at
            END,
            cleanup_heartbeat_at = CASE
                WHEN task.object_deleted_at IS NOT NULL THEN NULL
                ELSE task.cleanup_heartbeat_at
            END
        FROM historical_fence
        WHERE task.object_upload_expires_at IS NULL
        """)
    )
    # Legacy terminal attempts retain honest unknown snapshots. Active attempts
    # were rejected above; every new attempt begins active and is constrained to
    # carry both exact values before it can be inserted.
    op.add_column(
        "video_publish_attempts",
        sa.Column("artemis_profile", sa.Text()),
        schema="publishing",
    )
    op.add_column(
        "video_publish_attempts",
        sa.Column("artemis_verification_level", sa.Text()),
        schema="publishing",
    )
    op.create_check_constraint(
        "video_publish_attempt_active_snapshot_check",
        "video_publish_attempts",
        "status NOT IN ('created','submitting','queued','running','unknown') OR "
        "(NULLIF(BTRIM(artemis_profile), '') IS NOT NULL AND "
        "NULLIF(BTRIM(artemis_verification_level), '') IS NOT NULL)",
        schema="publishing",
    )
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
               OR NEW.target_app_package IS DISTINCT FROM OLD.target_app_package
               OR NEW.artemis_profile IS DISTINCT FROM OLD.artemis_profile
               OR NEW.artemis_verification_level IS DISTINCT FROM OLD.artemis_verification_level THEN
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
                         device_serial, device_path, target_app_package,
                         artemis_profile, artemis_verification_level
        ON publishing.video_publish_attempts
        FOR EACH ROW
        EXECUTE FUNCTION publishing.fn_immutable_video_publish_attempt_identity()
        """)
    )


def downgrade() -> None:
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM publishing.video_publish_tasks LIMIT 1)
               OR EXISTS (SELECT 1 FROM publishing.video_publish_attempts LIMIT 1) THEN
                RAISE EXCEPTION '0066 downgrade refused: publishing state is not empty';
            END IF;
        END $$
        """)
    )
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
        $$;
        CREATE TRIGGER trg_video_publish_attempt_identity
        BEFORE UPDATE OF task_id, sequence_no, kind, related_attempt_id,
                         artemis_session_id, prompt_version, prompt_snapshot,
                         device_serial, device_path, target_app_package
        ON publishing.video_publish_attempts
        FOR EACH ROW
        EXECUTE FUNCTION publishing.fn_immutable_video_publish_attempt_identity()
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_attempts "
            "DROP CONSTRAINT IF EXISTS video_publish_attempt_active_snapshot_check"
        )
    )
    op.drop_column(
        "video_publish_attempts", "artemis_verification_level", schema="publishing"
    )
    op.drop_column("video_publish_attempts", "artemis_profile", schema="publishing")
    op.drop_constraint(
        "uq_video_publish_execution_generation",
        "video_publish_tasks",
        schema="publishing",
        type_="unique",
    )
    op.drop_column("video_publish_tasks", "execution_generation", schema="publishing")
