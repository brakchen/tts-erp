"""intercept_configs 加 mode 列（whitelist / blacklist）

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-13
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "0028"
down_revision = "0027_plugin_logs_plugin_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 加 mode 列，默认 whitelist
    op.add_column(
        "intercept_configs",
        sa.Column(
            "mode",
            sa.Text(),
            server_default=sa.text("'whitelist'"),
            nullable=False,
            comment="whitelist = 仅记录匹配请求; blacklist = 完全跳过不上传",
        ),
        schema="plugin",
    )

    # 2. 加 CHECK 约束，限制只能是 whitelist 或 blacklist
    op.execute(
        """
        ALTER TABLE plugin.intercept_configs
        ADD CONSTRAINT intercept_configs_mode_check
        CHECK (mode IN ('whitelist', 'blacklist'))
        """
    )

    # 3. 加索引（blacklist 配置通常少，但查询时需要快速过滤）
    op.execute(
        """
        CREATE INDEX idx_intercept_configs_mode
        ON plugin.intercept_configs (mode)
        WHERE enabled = true
        """
    )


def downgrade() -> None:
    op.drop_index(
        "idx_intercept_configs_mode",
        table_name="intercept_configs",
        schema="plugin",
    )
    op.drop_constraint(
        "intercept_configs_mode_check",
        "intercept_configs",
        schema="plugin",
    )
    op.drop_column("intercept_configs", "mode", schema="plugin")
