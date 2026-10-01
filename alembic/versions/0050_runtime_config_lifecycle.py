"""Add soft-retirement lifecycle to runtime configuration records.

Revision ID: 0050_runtime_config_lifecycle
Revises: 0049_runtime_config_management
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0050_runtime_config_lifecycle"
down_revision: str | None = "0049_runtime_config_management"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("runtime_config_items", "runtime_config_secrets"):
        op.execute(
            text(
                f"""
                ALTER TABLE config.{table}
                  ADD COLUMN retired_at TIMESTAMPTZ,
                  ADD COLUMN retired_by VARCHAR(128)
                """
            )
        )
    op.execute(
        text(
            """
            CREATE INDEX ix_runtime_config_items_active
            ON config.runtime_config_items (config_key)
            WHERE retired_at IS NULL
            """
        )
    )
    op.execute(
        text(
            """
            CREATE INDEX ix_runtime_config_secrets_active
            ON config.runtime_config_secrets (name)
            WHERE retired_at IS NULL
            """
        )
    )


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS config.ix_runtime_config_secrets_active"))
    op.execute(text("DROP INDEX IF EXISTS config.ix_runtime_config_items_active"))
    for table in ("runtime_config_secrets", "runtime_config_items"):
        op.execute(
            text(
                f"""
                ALTER TABLE config.{table}
                  DROP COLUMN retired_by,
                  DROP COLUMN retired_at
                """
            )
        )
