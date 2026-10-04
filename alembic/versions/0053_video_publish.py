"""TikTok video publishing workflow.

Revision ID: 0053_video_publish
Revises: 0052_user_accounts
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0053_video_publish"
down_revision: str | None = "0052_user_accounts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pi-lens-ignore: python-sql-injection
    op.execute(text("CREATE SCHEMA IF NOT EXISTS publishing"))
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        CREATE TABLE publishing.video_publish_tasks (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          public_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
          client_request_id UUID NOT NULL UNIQUE,
          created_by_user_id BIGINT,
          caption TEXT NOT NULL, original_filename TEXT NOT NULL,
          content_type TEXT NOT NULL, size_bytes BIGINT NOT NULL,
          object_bucket TEXT NOT NULL, object_key TEXT NOT NULL UNIQUE,
          object_etag TEXT, object_sha256 TEXT, object_uploaded_at TIMESTAMPTZ,
          object_deleted_at TIMESTAMPTZ, status TEXT NOT NULL DEFAULT 'pending',
          stage TEXT NOT NULL DEFAULT 'awaiting_upload', attempt_count INTEGER NOT NULL DEFAULT 0,
          next_attempt_at TIMESTAMPTZ, lease_owner TEXT, lease_expires_at TIMESTAMPTZ,
          heartbeat_at TIMESTAMPTZ, row_version INTEGER NOT NULL DEFAULT 1,
          target_device_serial TEXT NOT NULL, target_app_package TEXT NOT NULL,
          device_path TEXT, last_error_code TEXT, last_error_message TEXT,
          device_cleanup_status TEXT NOT NULL DEFAULT 'not_started', device_cleanup_error TEXT,
          spool_cleanup_status TEXT NOT NULL DEFAULT 'not_started', spool_cleanup_error TEXT,
          object_cleanup_status TEXT NOT NULL DEFAULT 'not_started', object_cleanup_error TEXT,
          queued_at TIMESTAMPTZ, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT video_publish_task_status_check CHECK (status IN ('pending','running','succeeded','failed','needs_review','cancelled')),
          CONSTRAINT video_publish_task_stage_check CHECK (stage IN ('awaiting_upload','queued','waiting_device','downloading','staging_device','dispatching_artemis','waiting_artemis','verifying','cleaning','done')),
          CONSTRAINT video_publish_task_size_check CHECK (size_bytes > 0)
        )
    """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        CREATE TABLE publishing.video_publish_attempts (
          id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          task_id BIGINT NOT NULL REFERENCES publishing.video_publish_tasks(id) ON DELETE RESTRICT,
          sequence_no INTEGER NOT NULL, kind TEXT NOT NULL, related_attempt_id BIGINT REFERENCES publishing.video_publish_attempts(id) ON DELETE RESTRICT,
          artemis_session_id UUID NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'created',
          prompt_version TEXT NOT NULL, prompt_snapshot TEXT NOT NULL, device_serial TEXT NOT NULL, device_path TEXT,
          artemis_output JSONB, artemis_error TEXT, steps_count INTEGER, submit_retry_count INTEGER NOT NULL DEFAULT 0,
          last_polled_at TIMESTAMPTZ, retry_classification TEXT, retry_safe BOOLEAN, submitted_at TIMESTAMPTZ,
          started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT video_publish_attempt_kind_check CHECK (kind IN ('publish','verify')),
          CONSTRAINT video_publish_attempt_status_check CHECK (status IN ('created','submitting','queued','running','success','failed','rejected','cancelled','unknown')),
          UNIQUE(task_id, sequence_no)
        )
    """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        CREATE TABLE publishing.worker_heartbeats (
          instance_id TEXT PRIMARY KEY, hostname TEXT NOT NULL, pid INTEGER NOT NULL, status TEXT NOT NULL,
          version TEXT, started_at TIMESTAMPTZ NOT NULL, heartbeat_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT worker_heartbeat_status_check CHECK (status IN ('starting','ready','stopping'))
        )
    """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_video_publish_one_running ON publishing.video_publish_tasks ((1)) WHERE status = 'running'"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_video_publish_task_active_attempt ON publishing.video_publish_attempts (task_id) WHERE status IN ('submitting','queued','running')"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "CREATE INDEX ix_video_publish_queue ON publishing.video_publish_tasks (next_attempt_at, queued_at, id) WHERE status='pending' AND stage IN ('queued','waiting_device')"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "CREATE INDEX ix_video_publish_history ON publishing.video_publish_tasks (created_at DESC, id DESC)"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "CREATE INDEX ix_video_publish_attempt_status ON publishing.video_publish_attempts (status, updated_at) WHERE status IN ('created','submitting','queued','running','unknown')"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "CREATE OR REPLACE TRIGGER trg_video_publish_tasks_touch BEFORE UPDATE ON publishing.video_publish_tasks FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()"
        )
    )
    op.execute(
        text(
            "CREATE OR REPLACE TRIGGER trg_video_publish_attempts_touch BEFORE UPDATE ON publishing.video_publish_attempts FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "CREATE OR REPLACE TRIGGER trg_worker_heartbeats_touch BEFORE UPDATE ON publishing.worker_heartbeats FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "INSERT INTO security.permissions (code, kind, name) VALUES ('page:video-publish','page','视频发布') ON CONFLICT (code) DO NOTHING"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "INSERT INTO security.role_permissions (role_code, permission_code) VALUES ('admin','page:video-publish'),('operator','page:video-publish') ON CONFLICT DO NOTHING"
        )
    )


def downgrade() -> None:
    # Preserve audit data by requiring an explicit empty-table check.
    bind = op.get_bind()
    if bind.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM publishing.video_publish_tasks) OR EXISTS (SELECT 1 FROM publishing.video_publish_attempts) OR EXISTS (SELECT 1 FROM publishing.worker_heartbeats)"
        )
    ).scalar():
        raise RuntimeError("refusing downgrade while video publish audit data exists")
    # pi-lens-ignore: python-sql-injection
    op.execute(text("DROP SCHEMA publishing CASCADE"))
    # pi-lens-ignore: python-sql-injection
    op.execute(text("DELETE FROM security.permissions WHERE code='page:video-publish'"))
