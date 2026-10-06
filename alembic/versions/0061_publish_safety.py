"""Close publication-safety, readiness, and attempt-integrity gaps.

Revision ID: 0061_publish_safety
Revises: 0060_video_publish_invariants
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0061_publish_safety"
down_revision: str | None = "0060_video_publish_invariants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # attempt_count is the append-only audit count. Budget consumption is
    # separate so a proven pre-admission rejection can refund only the budget.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks "
            "ADD COLUMN publish_budget_used INTEGER NOT NULL DEFAULT 0"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks AS t
        SET attempt_count = (
                SELECT count(*)::integer
                FROM publishing.video_publish_attempts AS a
                WHERE a.task_id = t.id AND a.kind = 'publish'
            ),
            publish_budget_used = (
                SELECT count(*)::integer
                FROM publishing.video_publish_attempts AS a
                WHERE a.task_id = t.id AND a.kind = 'publish'
                  AND NOT (
                    a.status = 'rejected' AND a.retry_safe IS TRUE
                    AND upper(coalesce(a.retry_classification, ''))
                        IN ('DEVICE_LOCKED', 'DEVICE_BUSY')
                  )
            )
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        ALTER TABLE publishing.video_publish_tasks
        ADD CONSTRAINT video_publish_task_budget_check CHECK (
            attempt_count >= 0 AND publish_budget_used >= 0
            AND publish_budget_used <= attempt_count
        )
        """)
    )

    # The worker owns this probe result. API consumers never infer readiness
    # from a configured serial or expose raw ADB diagnostics.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.worker_heartbeats "
            "ADD COLUMN device_status TEXT NOT NULL DEFAULT 'unknown', "
            "ADD COLUMN device_message TEXT"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        ALTER TABLE publishing.worker_heartbeats
        ADD CONSTRAINT worker_heartbeat_device_status_check CHECK (
            device_status IN ('ready','busy','offline','locked','unknown')
        )
        """)
    )

    # Match the keyset query direction documented by the public API contract.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("DROP INDEX IF EXISTS publishing.ix_video_publish_attempt_task_seq")
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        CREATE INDEX ix_video_publish_attempt_task_seq
        ON publishing.video_publish_attempts (task_id, sequence_no DESC)
        """)
    )

    # A CHECK cannot inspect the referenced attempt. Reject pre-existing drift,
    # keep the local shape CHECK, and enforce cross-row integrity at the edge.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1
                FROM publishing.video_publish_attempts AS verify
                LEFT JOIN publishing.video_publish_attempts AS related
                  ON related.id = verify.related_attempt_id
                WHERE verify.kind = 'verify'
                  AND (related.id IS NULL OR related.kind <> 'publish'
                       OR related.task_id <> verify.task_id)
            ) THEN
                RAISE EXCEPTION
                    '0061 upgrade refused: invalid verify related attempt';
            END IF;
        END $$
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        CREATE OR REPLACE FUNCTION publishing.fn_validate_video_publish_related_attempt()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE related_task_id bigint;
        DECLARE related_kind text;
        BEGIN
            IF NEW.kind = 'verify' THEN
                SELECT task_id, kind INTO related_task_id, related_kind
                FROM publishing.video_publish_attempts
                WHERE id = NEW.related_attempt_id;
                IF related_task_id IS NULL
                   OR related_task_id <> NEW.task_id
                   OR related_kind <> 'publish' THEN
                    RAISE EXCEPTION
                        'verify related attempt must reference a publish attempt in the same task'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        CREATE TRIGGER trg_video_publish_attempt_related
        BEFORE INSERT OR UPDATE OF kind, task_id, related_attempt_id
        ON publishing.video_publish_attempts
        FOR EACH ROW
        EXECUTE FUNCTION publishing.fn_validate_video_publish_related_attempt()
        """)
    )


def downgrade() -> None:
    # Both new columns carry operational/audit state and must not be discarded.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM publishing.video_publish_tasks LIMIT 1)
               OR EXISTS (SELECT 1 FROM publishing.worker_heartbeats LIMIT 1) THEN
                RAISE EXCEPTION '0061 downgrade refused: publishing state is not empty';
            END IF;
        END $$
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "DROP TRIGGER IF EXISTS trg_video_publish_attempt_related "
            "ON publishing.video_publish_attempts"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "DROP FUNCTION IF EXISTS "
            "publishing.fn_validate_video_publish_related_attempt()"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("DROP INDEX IF EXISTS publishing.ix_video_publish_attempt_task_seq")
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        CREATE INDEX ix_video_publish_attempt_task_seq
        ON publishing.video_publish_attempts (task_id, sequence_no)
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        ALTER TABLE publishing.worker_heartbeats
        DROP CONSTRAINT IF EXISTS worker_heartbeat_device_status_check,
        DROP COLUMN IF EXISTS device_message,
        DROP COLUMN IF EXISTS device_status
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        ALTER TABLE publishing.video_publish_tasks
        DROP CONSTRAINT IF EXISTS video_publish_task_budget_check,
        DROP COLUMN IF EXISTS publish_budget_used
        """)
    )
