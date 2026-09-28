"""Add service_id column to commerce.shops.

service_id 是 TikTok Partner Center 的 App & Service 页面的标识，
用于开发者授权。不同店铺可能绑不同的 service_id，选填。
"""

from alembic import op

revision: str = "0036_channel_accounts_service_id"
down_revision: str | None = "0035_order_detail_history_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE commerce.shops
        ADD COLUMN IF NOT EXISTS service_id text
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE commerce.shops
        DROP COLUMN IF EXISTS service_id
        """
    )
