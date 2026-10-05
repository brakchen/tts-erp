"""Preserve attempt identity and original upload filenames.

Revision ID: 0062_publish_attempt_identity
Revises: 0061_publish_safety
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0062_publish_attempt_identity"
down_revision: str | None = "0061_publish_safety"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Preserve the sanitized basename separately. Legacy rows used
    # original_filename for this value, so that is the only safe backfill.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks ADD COLUMN object_filename TEXT"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        UPDATE publishing.video_publish_tasks
        SET object_filename = left(
            coalesce(nullif(regexp_replace(object_key, '^.*/', ''), ''),
                     original_filename),
            180
        )
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks "
            "ALTER COLUMN object_filename SET DEFAULT 'video.mp4', "
            "ALTER COLUMN object_filename SET NOT NULL"
        )
    )

    # Attempts are append-only audit rows. Their relationship identity cannot
    # change after insertion, while status/result/retry fields remain mutable.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        CREATE OR REPLACE FUNCTION publishing.fn_immutable_video_publish_attempt_identity()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.task_id IS DISTINCT FROM OLD.task_id
               OR NEW.kind IS DISTINCT FROM OLD.kind
               OR NEW.related_attempt_id IS DISTINCT FROM OLD.related_attempt_id THEN
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
        CREATE TRIGGER trg_video_publish_attempt_identity
        BEFORE UPDATE OF task_id, kind, related_attempt_id
        ON publishing.video_publish_attempts
        FOR EACH ROW
        EXECUTE FUNCTION publishing.fn_immutable_video_publish_attempt_identity()
        """)
    )


def downgrade() -> None:
    # This migration protects persisted audit identity and original filename
    # semantics. Do not silently remove either while state-bearing rows exist.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM publishing.video_publish_tasks LIMIT 1)
               OR EXISTS (SELECT 1 FROM publishing.video_publish_attempts LIMIT 1) THEN
                RAISE EXCEPTION '0062 downgrade refused: publishing state is not empty';
            END IF;
        END $$
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "DROP TRIGGER IF EXISTS trg_video_publish_attempt_identity "
            "ON publishing.video_publish_attempts"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "DROP FUNCTION IF EXISTS "
            "publishing.fn_immutable_video_publish_attempt_identity()"
        )
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "ALTER TABLE publishing.video_publish_tasks "
            "DROP COLUMN IF EXISTS object_filename"
        )
    )
