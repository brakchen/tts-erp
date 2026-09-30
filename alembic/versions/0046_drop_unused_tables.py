"""Remove unused placeholder tables and purchase-order projections.

Revision ID: 0046_drop_unused_tables
Revises: 0045_miaoshou_package_schema
Create Date: 2026-09-30

The removed tables were empty in production. The three purchase provenance
columns on reporting.product_cost_snapshots contained no non-NULL values.
The user explicitly chose not to create a new archive for this cleanup.

Monthly analytics remains supported through plugin.ad_raw_log; only the unused
structured plugin.ad_monthly projection is removed.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0046_drop_unused_tables"
down_revision: str | None = "0045_miaoshou_package_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reporting.shipment_tracking_summary")
    op.execute("DROP TABLE IF EXISTS fulfillment.shipment_lines")
    op.execute("DROP TABLE IF EXISTS procurement.purchase_order_lines")
    op.execute("DROP TABLE IF EXISTS procurement.purchase_orders")
    op.execute("DROP TABLE IF EXISTS procurement.procurement_product_variants")
    op.execute("DROP TABLE IF EXISTS plugin.ad_monthly")
    op.execute(
        "ALTER TABLE reporting.product_cost_snapshots "
        "DROP COLUMN IF EXISTS source_purchase_quantity, "
        "DROP COLUMN IF EXISTS source_purchase_amount, "
        "DROP COLUMN IF EXISTS source_line_count"
    )


def downgrade() -> None:
    raise RuntimeError(
        "0046 removes retired schema objects without a new archive; "
        "restore from an older full database backup instead of downgrading"
    )
