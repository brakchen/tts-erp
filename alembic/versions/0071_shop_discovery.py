"""Store plugin discovery metadata on the existing shops table."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0071_shop_discovery"
down_revision = "0070_monitor_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shops", sa.Column("shop_code", sa.Text()), schema="commerce")
    op.add_column("shops", sa.Column("discovered_at", sa.DateTime(timezone=True)), schema="commerce")
    op.add_column("shops", sa.Column("plugin_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")), schema="commerce")


def downgrade() -> None:
    op.drop_column("shops", "plugin_metadata", schema="commerce")
    op.drop_column("shops", "discovered_at", schema="commerce")
    op.drop_column("shops", "shop_code", schema="commerce")
