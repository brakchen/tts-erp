"""Persist shop-scoped focused SPU memberships.

Revision ID: 0043_focused_spus
Revises: 0042_shop_fee_rate_v2
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0043_focused_spus"
down_revision: str | None = "0042_shop_fee_rate_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "focused_spus",
        sa.Column("shop_pk", sa.BigInteger, nullable=False),
        sa.Column("spu_id", sa.Text, nullable=False),
        sa.Column("active", sa.Boolean, server_default=sa.true(), nullable=False),
        sa.Column("added_by", sa.Text, nullable=True),
        sa.Column("removed_by", sa.Text, nullable=True),
        sa.Column("removed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
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
        sa.PrimaryKeyConstraint("shop_pk", "spu_id", name="pk_focused_spus"),
        sa.ForeignKeyConstraint(
            ["shop_pk"],
            ["commerce.shops.id"],
            name="fk_focused_spus_shop",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shop_pk", "spu_id"],
            ["commerce.products_spu.shop_pk", "commerce.products_spu.spu_id"],
            name="fk_focused_spus_product",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "length(spu_id) BETWEEN 1 AND 128",
            name="ck_focused_spus_spu_id_length",
        ),
        schema="reporting",
    )
    op.create_index(
        "ix_focused_spus_active_membership",
        "focused_spus",
        ["shop_pk", "spu_id"],
        unique=False,
        schema="reporting",
        postgresql_where=sa.text("active IS TRUE"),
    )
    op.create_index(
        "ix_focused_spus_active_updated",
        "focused_spus",
        ["shop_pk", sa.text("updated_at DESC"), "spu_id"],
        unique=False,
        schema="reporting",
        postgresql_where=sa.text("active IS TRUE"),
    )
    op.execute(
        "CREATE OR REPLACE TRIGGER trg_reporting_focused_spus_touch "
        "BEFORE UPDATE ON reporting.focused_spus FOR EACH ROW "
        "EXECUTE FUNCTION public.fn_touch_updated_at()"
    )
    op.execute(
        "COMMENT ON TABLE reporting.focused_spus IS "
        "'店铺级重点关注 SPU 当前状态；active=false 为软移除，非完整事件历史'"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_reporting_focused_spus_touch "
        "ON reporting.focused_spus"
    )
    op.drop_index(
        "ix_focused_spus_active_updated",
        table_name="focused_spus",
        schema="reporting",
    )
    op.drop_index(
        "ix_focused_spus_active_membership",
        table_name="focused_spus",
        schema="reporting",
    )
    op.drop_table("focused_spus", schema="reporting")
