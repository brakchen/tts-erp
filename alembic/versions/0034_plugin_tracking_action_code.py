"""Persist TikTok logistics action codes for terminal-state reconciliation."""

from alembic import op


revision: str = "0034_plugin_tracking_action_code"
down_revision: str | None = "0033_drop_plugin_raw_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE plugin.tracking_events "
        "ADD COLUMN IF NOT EXISTS action_code integer"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE plugin.tracking_events "
        "DROP COLUMN IF EXISTS action_code"
    )
