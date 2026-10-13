"""Per-device publish mutex, confirmation audit, and locked-param columns.

Revision ID: 0069_video_publish_confirmation
Revises: 0068_spu_deterioration_alert
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0069_video_publish_confirmation"
down_revision: str | None = "0068_spu_deterioration_alert"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TASK_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("prompt_override", sa.Text()),
    ("params_locked_at", sa.DateTime(timezone=True)),
    ("confirmed_at", sa.DateTime(timezone=True)),
    ("confirmed_by_user_id", sa.BigInteger()),
    ("confirmed_by_key_hash", sa.Text()),
    ("last_verify_verdict", sa.Text()),
)


def upgrade() -> None:
    op.create_table(
        "video_publish_confirmations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=False),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("operator_user_id", sa.BigInteger(), nullable=True),
        sa.Column("operator_key_hash", sa.Text(), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["publishing.video_publish_tasks.id"],
            name="fk_video_publish_confirmation_task_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id", name="uq_video_publish_confirmation_request_id"
        ),
        schema="publishing",
    )
    op.create_index(
        "uq_video_publish_confirm_once",
        "video_publish_confirmations",
        ["task_id"],
        unique=True,
        postgresql_where=text(
            "action = 'confirm_published' AND to_status = 'succeeded'"
        ),
        schema="publishing",
    )
    for name, column_type in _TASK_COLUMNS:
        op.add_column(
            "video_publish_tasks", sa.Column(name, column_type), schema="publishing"
        )
    op.add_column(
        "video_publish_tasks",
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="publishing",
    )
    # The 0053 mutex allowed one running task in the whole system; per-device
    # execution needs one slot per device instead.
    # pi-lens-ignore: python-sql-injection
    op.execute(text("DROP INDEX IF EXISTS publishing.uq_video_publish_one_running"))
    op.create_index(
        "uq_video_publish_one_running_per_device",
        "video_publish_tasks",
        ["target_device_serial"],
        unique=True,
        postgresql_where=text("status = 'running'"),
        schema="publishing",
    )


def downgrade() -> None:
    # Confirmation rows are human audit facts and the 0053 mutex cannot be
    # rebuilt while more than one task runs concurrently: refuse instead of
    # dropping or guessing.
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM publishing.video_publish_confirmations LIMIT 1
            ) THEN
                RAISE EXCEPTION
                    '0069 downgrade refused: manual confirmation audit rows exist';
            END IF;
            IF (
                SELECT count(*)
                FROM publishing.video_publish_tasks
                WHERE status = 'running'
            ) > 1 THEN
                RAISE EXCEPTION
                    '0069 downgrade refused: drain concurrent running tasks first';
            END IF;
        END $$
        """)
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text("DROP INDEX IF EXISTS publishing.uq_video_publish_one_running_per_device")
    )
    # pi-lens-ignore: python-sql-injection
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_video_publish_one_running "
            "ON publishing.video_publish_tasks ((1)) WHERE status = 'running'"
        )
    )
    op.drop_column("video_publish_tasks", "cancel_requested", schema="publishing")
    for name, _column_type in reversed(_TASK_COLUMNS):
        op.drop_column("video_publish_tasks", name, schema="publishing")
    op.drop_table("video_publish_confirmations", schema="publishing")
