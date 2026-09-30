"""Add Miaoshou purchase-order raw history and cleaned SPU prices.

Revision ID: 0047_miaoshou_purchase_price
Revises: 0046_drop_unused_tables
Create Date: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0047_miaoshou_purchase_price"
down_revision: str | None = "0046_drop_unused_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "purchase_order_raw_records",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("credential_id", sa.BigInteger),
        sa.Column("external_purchase_order_id", sa.Text, nullable=False),
        sa.Column("endpoint", sa.Text, nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("payload_hash", sa.Text, nullable=False),
        sa.Column(
            "captured_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["integration.credentials.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "credential_id",
            "external_purchase_order_id",
            "payload_hash",
            name="uq_miaoshou_purchase_raw_credential_order_hash",
        ),
        schema="miaoshou",
    )
    op.create_index(
        "ix_miaoshou_purchase_raw_order",
        "purchase_order_raw_records",
        ["credential_id", "external_purchase_order_id"],
        schema="miaoshou",
    )
    op.create_index(
        "ix_miaoshou_purchase_raw_captured",
        "purchase_order_raw_records",
        ["captured_at"],
        schema="miaoshou",
    )

    op.create_table(
        "purchase_prices",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.Column("credential_id", sa.BigInteger, nullable=False),
        sa.Column("miaoshou_shop_id", sa.Text, nullable=False),
        sa.Column("shop_name", sa.Text),
        sa.Column("shop_pk", sa.BigInteger),
        sa.Column("spu_id", sa.Text, nullable=False),
        sa.Column("unit_cost", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.Text, server_default=sa.text("'CNY'"), nullable=False),
        sa.Column("source_purchase_order_sn", sa.Text, nullable=False),
        sa.Column("source_item_id", sa.Text, nullable=False),
        sa.Column("source_purchase_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("source_status", sa.Text),
        sa.Column(
            "calculation_method",
            sa.Text,
            server_default=sa.text("'quantity_weighted_mean'"),
            nullable=False,
        ),
        sa.Column(
            "calculation_version",
            sa.Text,
            server_default=sa.text("'purchase-price-v1'"),
            nullable=False,
        ),
        sa.Column("resolution_status", sa.Text, nullable=False),
        sa.Column("evidence", postgresql.JSONB, nullable=False),
        sa.Column(
            "last_seen_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "synced_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["credential_id"], ["integration.credentials.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["shop_pk"], ["commerce.shops.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "credential_id",
            "miaoshou_shop_id",
            "spu_id",
            name="uq_miaoshou_purchase_price_credential_shop_spu",
        ),
        sa.CheckConstraint(
            "unit_cost > 0",
            name="ck_miaoshou_purchase_price_positive",
        ),
        schema="miaoshou",
    )
    op.create_index(
        "ix_miaoshou_purchase_price_shop",
        "purchase_prices",
        ["shop_pk"],
        schema="miaoshou",
    )
    op.create_index(
        "ix_miaoshou_purchase_price_status",
        "purchase_prices",
        ["resolution_status"],
        schema="miaoshou",
    )
    op.create_index(
        "ix_miaoshou_purchase_price_source_at",
        "purchase_prices",
        ["source_purchase_at"],
        schema="miaoshou",
    )
    op.execute(
        "CREATE OR REPLACE TRIGGER trg_miaoshou_purchase_prices_touch "
        "BEFORE UPDATE ON miaoshou.purchase_prices FOR EACH ROW "
        "EXECUTE FUNCTION public.fn_touch_updated_at()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_miaoshou_purchase_prices_touch "
        "ON miaoshou.purchase_prices"
    )
    op.drop_index(
        "ix_miaoshou_purchase_price_source_at",
        table_name="purchase_prices",
        schema="miaoshou",
    )
    op.drop_index(
        "ix_miaoshou_purchase_price_status",
        table_name="purchase_prices",
        schema="miaoshou",
    )
    op.drop_index(
        "ix_miaoshou_purchase_price_shop",
        table_name="purchase_prices",
        schema="miaoshou",
    )
    op.drop_table("purchase_prices", schema="miaoshou")
    op.drop_index(
        "ix_miaoshou_purchase_raw_captured",
        table_name="purchase_order_raw_records",
        schema="miaoshou",
    )
    op.drop_index(
        "ix_miaoshou_purchase_raw_order",
        table_name="purchase_order_raw_records",
        schema="miaoshou",
    )
    op.drop_table("purchase_order_raw_records", schema="miaoshou")
