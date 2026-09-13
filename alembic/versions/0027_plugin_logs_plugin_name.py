"""add plugin_name to plugin_logs

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-04 12:00:00.000000
"""
from __future__ import annotations

from alembic import op

# revision identifiers
revision: str = '0027_plugin_logs_plugin_name'
down_revision: str | None = '0026_intercept_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE plugin.plugin_logs
        ADD COLUMN plugin_name text NOT NULL DEFAULT '';
    """)
    op.execute("""
        CREATE INDEX idx_plugin_logs_plugin_name
        ON plugin.plugin_logs (plugin_name);
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS plugin.idx_plugin_logs_plugin_name;")
    op.execute("ALTER TABLE plugin.plugin_logs DROP COLUMN IF EXISTS plugin_name;")
