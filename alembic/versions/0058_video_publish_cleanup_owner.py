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
          AND (SELECT a.status FROM publishing.video_publish_attempts AS a
               WHERE a.task_id = t.id ORDER BY a.sequence_no DESC LIMIT 1) = 'success'
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
    # A legacy cleaning row with a confirmed publish/verify success keeps the
    # success result and gets an explicit finalize plan. Other rows are ambiguous.
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
          AND (SELECT a.status FROM publishing.video_publish_attempts AS a
               WHERE a.task_id = t.id ORDER BY a.sequence_no DESC LIMIT 1) = 'success'
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
          AND (SELECT a.status FROM publishing.video_publish_attempts AS a
               WHERE a.task_id = t.id ORDER BY a.sequence_no DESC LIMIT 1) = 'success'
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
                AND device_cleanup_status NOT IN ('pending','failed')
                AND spool_cleanup_status NOT IN ('pending','failed')
                AND object_cleanup_status NOT IN ('pending','failed')) OR
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
