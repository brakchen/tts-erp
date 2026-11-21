"""Add an operator-maintained alias for each shop."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0077_shop_alias"
down_revision = "0076_media_packs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shops", sa.Column("shop_alias", sa.Text(), nullable=True), schema="commerce")


def downgrade() -> None:
    op.drop_column("shops", "shop_alias", schema="commerce")
