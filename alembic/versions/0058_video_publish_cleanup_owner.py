"""Separate cleanup intent and lease ownership from publish execution.

Revision ID: 0058_video_publish_cleanup_owner
Revises: 0057_publish_owner_key
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0058_video_publish_cleanup_owner"
down_revision: str | None = "0057_publish_owner_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks ADD COLUMN cleanup_intent TEXT NOT NULL DEFAULT 'none'"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks ADD COLUMN cleanup_lease_owner TEXT"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks ADD COLUMN cleanup_lease_expires_at TIMESTAMPTZ"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks ADD COLUMN cleanup_heartbeat_at TIMESTAMPTZ"
        )
    )

    # Reconcile rows written by the pre-0058 implementation before constraints
    # make the state owner explicit. No object is removed by this backfill.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks AS t
        SET status = 'succeeded', stage = 'done', cleanup_intent = CASE
                WHEN device_cleanup_status IN ('pending','failed')
                  OR spool_cleanup_status IN ('pending','failed')
                  OR object_cleanup_status IN ('pending','failed')
                THEN 'finalize_success' ELSE 'none' END,
            lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL
        WHERE t.status = 'running' AND t.stage = 'done'
          AND (SELECT (a.kind = 'publish' AND a.status = 'success') OR
                      (a.kind = 'verify' AND a.status = 'success'
                       AND a.artemis_output ->> 'verdict' = 'published')
               FROM publishing.video_publish_attempts AS a
               WHERE a.task_id = t.id ORDER BY a.sequence_no DESC LIMIT 1) IS TRUE
    """)
    )
    # Rows without a confirming terminal attempt are ambiguous and must not be
    # upgraded into a successful business result.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks AS t
        SET status = 'needs_review', stage = 'done', cleanup_intent = 'preserve_state',
            lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL
        WHERE t.status = 'running' AND t.stage = 'done'
    """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks
        SET status = 'cancelled', stage = 'done', cleanup_intent = 'preserve_state'
        WHERE status = 'cancelled' AND stage = 'cleaning'
    """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks
        SET status = 'pending', stage = 'waiting_device', cleanup_intent = 'requeue_publish'
        WHERE status = 'pending' AND stage = 'cleaning'
    """)
    )
    # pi-lens-ignore: python-sql-injection
    # A legacy cleaning row keeps success only for a successful publish, or a
    # successful verify whose exact verdict is published. Other rows are ambiguous.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks AS t
        SET device_cleanup_status = CASE
                WHEN t.device_path IS NOT NULL AND t.device_cleanup_status = 'not_started' THEN 'pending'
                ELSE t.device_cleanup_status END,
            spool_cleanup_status = CASE
                WHEN t.spool_cleanup_status = 'not_started' THEN 'pending'
                ELSE t.spool_cleanup_status END,
            object_cleanup_status = CASE
                WHEN t.object_cleanup_status = 'not_started' THEN 'pending'
                ELSE t.object_cleanup_status END
        WHERE t.status = 'running' AND t.stage = 'cleaning'
          AND (SELECT (a.kind = 'publish' AND a.status = 'success') OR
                      (a.kind = 'verify' AND a.status = 'success'
                       AND a.artemis_output ->> 'verdict' = 'published')
               FROM publishing.video_publish_attempts AS a
               WHERE a.task_id = t.id ORDER BY a.sequence_no DESC LIMIT 1) IS TRUE
    """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks AS t
        SET status = 'succeeded', stage = 'done', cleanup_intent = CASE
                WHEN device_cleanup_status IN ('pending','failed')
                  OR spool_cleanup_status IN ('pending','failed')
                  OR object_cleanup_status IN ('pending','failed')
                THEN 'finalize_success' ELSE 'none' END,
            lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL
        WHERE t.status = 'running' AND t.stage = 'cleaning'
          AND (SELECT (a.kind = 'publish' AND a.status = 'success') OR
                      (a.kind = 'verify' AND a.status = 'success'
                       AND a.artemis_output ->> 'verdict' = 'published')
               FROM publishing.video_publish_attempts AS a
               WHERE a.task_id = t.id ORDER BY a.sequence_no DESC LIMIT 1) IS TRUE
    """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks
        SET status = 'needs_review', stage = 'done', cleanup_intent = 'preserve_state',
            lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL
        WHERE status = 'running' AND stage = 'cleaning'
    """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks
        SET stage = 'done'
        WHERE status IN ('succeeded','failed','needs_review','cancelled')
          AND stage <> 'done'
    """)
    )
    # Every terminal legacy row is normalized away from publish ownership,
    # including rows whose stage was already done and whose stale lease did not
    # participate in one of the running/cleaning conversions above.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks
        SET lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL
        WHERE status IN ('succeeded','failed','needs_review','cancelled')
          AND stage = 'done'
    """)
    )
    # Legacy publish continuations were persisted before cleanup ownership was
    # explicit. Requeue only device/spool residue; object residue is
    # conservative needs-review because publication safety is unproven.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks
        SET status = CASE
                WHEN object_cleanup_status IN ('pending','failed') THEN 'needs_review'
                ELSE status END,
            stage = CASE
                WHEN object_cleanup_status IN ('pending','failed') THEN 'done'
                WHEN device_cleanup_status IN ('pending','failed') THEN 'waiting_device'
                ELSE stage END,
            cleanup_intent = CASE
                WHEN object_cleanup_status IN ('pending','failed') THEN 'preserve_state'
                ELSE 'requeue_publish' END,
            lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
            cleanup_lease_owner = NULL, cleanup_lease_expires_at = NULL,
            cleanup_heartbeat_at = NULL
        WHERE status = 'pending'
          AND stage IN ('queued','waiting_device')
          AND (device_cleanup_status IN ('pending','failed')
            OR spool_cleanup_status IN ('pending','failed')
            OR object_cleanup_status IN ('pending','failed'))
    """)
    )
    # Legacy rows without cleanup work must remain claimable with no cleanup
    # ownership; any stale lease is not safe to carry across the migration.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks
        SET cleanup_intent = 'none', lease_owner = NULL,
            lease_expires_at = NULL, heartbeat_at = NULL,
            cleanup_lease_owner = NULL, cleanup_lease_expires_at = NULL,
            cleanup_heartbeat_at = NULL
        WHERE status = 'pending' AND stage IN ('queued','waiting_device')
          AND device_cleanup_status NOT IN ('pending','failed')
          AND spool_cleanup_status NOT IN ('pending','failed')
          AND object_cleanup_status NOT IN ('pending','failed')
    """)
    )
    # Ambiguous running rows must retain their MinIO object. Device/spool
    # residue may still be retried, but object deletion is cancelled.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks
        SET object_cleanup_status = 'not_started',
            object_cleanup_error = 'OBJECT_CLEANUP_CANCELLED_MIGRATION',
            object_cleanup_next_attempt_at = NULL,
            object_deleted_at = NULL
        WHERE status = 'needs_review' AND stage = 'done'
          AND cleanup_intent = 'preserve_state'
          AND object_cleanup_status IN ('pending','failed')
    """)
    )
    # Object cancellation can leave a terminal ambiguous row with no resource
    # work. Such rows must not retain an unclaimable cleanup owner state.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks
        SET cleanup_intent = 'none',
            cleanup_lease_owner = NULL,
            cleanup_lease_expires_at = NULL,
            cleanup_heartbeat_at = NULL
        WHERE status IN ('succeeded','failed','needs_review','cancelled')
          AND stage = 'done'
          AND cleanup_intent = 'preserve_state'
          AND device_cleanup_status NOT IN ('pending','failed')
          AND spool_cleanup_status NOT IN ('pending','failed')
          AND object_cleanup_status NOT IN ('pending','failed')
    """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks
        SET cleanup_intent = CASE
            WHEN status IN ('succeeded', 'cancelled')
                 AND (device_cleanup_status IN ('pending','failed')
                   OR spool_cleanup_status IN ('pending','failed')
                   OR object_cleanup_status IN ('pending','failed'))
                THEN 'preserve_state'
            WHEN status IN ('failed', 'needs_review')
                 AND (device_cleanup_status IN ('pending','failed')
                   OR spool_cleanup_status IN ('pending','failed')
                   OR object_cleanup_status IN ('pending','failed'))
                THEN 'preserve_state'
            ELSE 'none'
        END
        WHERE cleanup_intent = 'none'
    """)
    )

    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        ALTER TABLE publishing.video_publish_tasks
        ADD CONSTRAINT video_publish_task_cleanup_intent_check CHECK (
            cleanup_intent IN ('none','finalize_success','requeue_publish','preserve_state')
        )
    """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        ALTER TABLE publishing.video_publish_tasks
        ADD CONSTRAINT video_publish_task_status_stage_check CHECK (
            (status = 'pending' AND stage IN ('awaiting_upload','queued','waiting_device')) OR
            (status = 'running' AND stage IN ('downloading','staging_device','dispatching_artemis','waiting_artemis','verifying')) OR
            (status IN ('succeeded','failed','needs_review','cancelled') AND stage = 'done')
        )
    """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        ALTER TABLE publishing.video_publish_tasks
        ADD CONSTRAINT video_publish_task_cleanup_owner_check CHECK (
            (cleanup_intent = 'none' AND cleanup_lease_owner IS NULL AND cleanup_lease_expires_at IS NULL
                AND ((status = 'running' AND lease_owner IS NOT NULL
                      AND stage IN ('downloading','staging_device','dispatching_artemis','waiting_artemis','verifying')
                      AND object_cleanup_status NOT IN ('pending','failed'))
                  OR (device_cleanup_status NOT IN ('pending','failed')
                      AND spool_cleanup_status NOT IN ('pending','failed')
                      AND object_cleanup_status NOT IN ('pending','failed')))) OR
            (cleanup_intent = 'finalize_success' AND status = 'succeeded' AND stage = 'done') OR
            (cleanup_intent = 'requeue_publish' AND status = 'pending' AND stage IN ('queued','waiting_device')
                AND object_cleanup_status NOT IN ('pending','failed')) OR
            (cleanup_intent = 'preserve_state' AND status IN ('succeeded','failed','needs_review','cancelled') AND stage = 'done')
        )
    """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        CREATE INDEX ix_video_publish_cleanup_queue
        ON publishing.video_publish_tasks (cleanup_lease_expires_at, id)
        WHERE cleanup_intent <> 'none'
          AND (device_cleanup_status IN ('pending','failed')
            OR spool_cleanup_status IN ('pending','failed')
            OR object_cleanup_status IN ('pending','failed'))
    """)
    )


def downgrade() -> None:
    # Cleanup ownership is state-bearing audit data. Never discard it while
    # tasks exist; operators must drain/retain the table before rollback.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM publishing.video_publish_tasks LIMIT 1) THEN
                RAISE EXCEPTION '0058 downgrade refused: publishing.video_publish_tasks is not empty';
            END IF;
        END $$
    """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(text("DROP INDEX IF EXISTS publishing.ix_video_publish_cleanup_queue"))
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks DROP CONSTRAINT IF EXISTS video_publish_task_cleanup_owner_check"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks DROP CONSTRAINT IF EXISTS video_publish_task_status_stage_check"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks DROP CONSTRAINT IF EXISTS video_publish_task_cleanup_intent_check"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks DROP COLUMN IF EXISTS cleanup_heartbeat_at"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks DROP COLUMN IF EXISTS cleanup_lease_expires_at"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks DROP COLUMN IF EXISTS cleanup_lease_owner"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks DROP COLUMN IF EXISTS cleanup_intent"
        )
    )
