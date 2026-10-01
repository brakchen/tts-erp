"""Add indexes for SPU ROI shop-scoped fact queries.

Revision ID: 0051_spu_roi_query_indexes
Revises: 0050_runtime_config_lifecycle
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0051_spu_roi_query_indexes"
down_revision: str | None = "0050_runtime_config_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INDEX_SQL = (
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ad_daily_roi_seller_product_day
    ON plugin.ad_daily (seller_id, product_id, day)
    INCLUDE (
        campaign_id,
        mixed_real_cost,
        onsite_roi2_shopping_sku,
        onsite_roi2_shopping_value
    )
    WHERE endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_sales_order_lines_spu_order
    ON commerce.sales_order_lines (spu_pk, order_pk)
    INCLUDE (id, quantity, unit_price)
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tracking_events_shipment_action
    ON fulfillment.tracking_events (shipment_id, action_code)
    """,
)

_DROP_SQL = (
    "DROP INDEX CONCURRENTLY IF EXISTS plugin.ix_ad_daily_roi_seller_product_day",
    "DROP INDEX CONCURRENTLY IF EXISTS commerce.ix_sales_order_lines_spu_order",
    "DROP INDEX CONCURRENTLY IF EXISTS fulfillment.ix_tracking_events_shipment_action",
)


def upgrade() -> None:
    ctx = op.get_context()
    with ctx.autocommit_block():
        for statement in _INDEX_SQL:
            op.execute(text(statement))


def downgrade() -> None:
    ctx = op.get_context()
    with ctx.autocommit_block():
        for statement in _DROP_SQL:
            op.execute(text(statement))
