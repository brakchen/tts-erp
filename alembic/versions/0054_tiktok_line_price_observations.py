"""Add immutable TikTok line price observations."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0054_tiktok_price_obs"
down_revision: str | None = "0052_user_accounts"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "sales_order_line_price_observations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("shop_pk", sa.BigInteger(), nullable=False),
        sa.Column("order_pk", sa.BigInteger(), nullable=False),
        sa.Column("external_line_id", sa.Text(), nullable=False),
        sa.Column("raw_record_id", sa.BigInteger(), nullable=False),
        sa.Column("source_endpoint", sa.Text(), nullable=False),
        sa.Column("source_payload_hash", sa.Text(), nullable=False),
        sa.Column("semantic_observation_hash", sa.Text(), nullable=False),
        sa.Column("source_order_version_at", sa.DateTime(timezone=True)),
        sa.Column("source_captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spu_pk", sa.BigInteger()),
        sa.Column("raw_quantity", sa.Numeric(20, 8)),
        sa.Column("effective_quantity", sa.Numeric(20, 8)),
        sa.Column("quantity_status", sa.Text(), nullable=False),
        sa.Column("line_status_raw", sa.Text()),
        sa.Column("parent_payment_status", sa.Text(), nullable=False),
        sa.Column("gift_status", sa.Text(), nullable=False),
        sa.Column("original_price_native", sa.Numeric(28, 10)),
        sa.Column("paid_price_native", sa.Numeric(28, 10)),
        sa.Column("currency", sa.Text()),
        sa.Column("original_price_status", sa.Text(), nullable=False),
        sa.Column("paid_price_status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["shop_pk"], ["commerce.shops.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_pk"], ["commerce.sales_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["raw_record_id"], ["integration.raw_records.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["spu_pk"], ["commerce.products_spu.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("shop_pk", "order_pk", "external_line_id", "semantic_observation_hash", name="uq_solpo_line_semantic"),
        sa.CheckConstraint("effective_quantity IS NULL OR effective_quantity > 0", name="ck_solpo_effective_quantity_positive"),
        sa.CheckConstraint("original_price_native IS NULL OR original_price_native >= 0", name="ck_solpo_original_price_nonnegative"),
        sa.CheckConstraint("paid_price_native IS NULL OR paid_price_native >= 0", name="ck_solpo_paid_price_nonnegative"),
        sa.CheckConstraint("source_endpoint IN ('ORDER_SEARCH', 'ORDER_DETAIL')", name="ck_solpo_source_endpoint"),
        schema="commerce",
    )
    op.create_index(
        "ix_solpo_line_version",
        "sales_order_line_price_observations",
        ["shop_pk", "order_pk", "external_line_id", "source_order_version_at", "source_captured_at", "semantic_observation_hash"],
        schema="commerce",
    )
    op.create_index(
        "ix_solpo_spu_capture",
        "sales_order_line_price_observations",
        ["shop_pk", "spu_pk", "source_captured_at"],
        schema="commerce",
    )
    op.execute(
        "CREATE TRIGGER trg_commerce_sales_order_line_price_observations_touch "
        "BEFORE UPDATE ON commerce.sales_order_line_price_observations "
        "FOR EACH ROW EXECUTE FUNCTION public.fn_touch_updated_at()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_commerce_sales_order_line_price_observations_touch "
        "ON commerce.sales_order_line_price_observations"
    )
    op.drop_index("ix_solpo_spu_capture", table_name="sales_order_line_price_observations", schema="commerce")
    op.drop_index("ix_solpo_line_version", table_name="sales_order_line_price_observations", schema="commerce")
    op.drop_table("sales_order_line_price_observations", schema="commerce")
