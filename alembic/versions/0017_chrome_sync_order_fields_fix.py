"""chrome_sync orders/order_lines 字段修正（实测确认）

Revision ID: 0017
Revises: 0016
"""

from alembic import op

revision = "0017"
down_revision = "6c7d680dddec"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── orders 表：删旧字段、加新字段 ──
    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS status")
    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS paid_at")
    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS shipped_at")
    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS delivered_at")
    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS cancelled_at")

    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS main_order_status INTEGER")
    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS sku_display_status INTEGER")
    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS fulfillment_type INTEGER")
    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS pay_method TEXT")
    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS sale_region TEXT")
    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS shipping_fee NUMERIC(20,4)")
    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS update_time TIMESTAMPTZ")
    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS latest_rts_time TIMESTAMPTZ")
    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS latest_tts_time TIMESTAMPTZ")
    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS buyer_nickname TEXT")

    # 旧索引 ix_orders_status(status) → 改为 ix_orders_main_order_status(main_order_status)
    op.execute("DROP INDEX IF EXISTS chrome_sync.ix_orders_status")
    op.execute("CREATE INDEX IF NOT EXISTS ix_orders_main_order_status ON chrome_sync.orders(main_order_status)")

    # ── order_lines 表：删旧字段、加新字段 ──
    op.execute("ALTER TABLE chrome_sync.order_lines DROP COLUMN IF EXISTS seller_sku")
    op.execute("ALTER TABLE chrome_sync.order_lines DROP COLUMN IF EXISTS line_status")

    op.execute("ALTER TABLE chrome_sync.order_lines ADD COLUMN IF NOT EXISTS total_price NUMERIC(20,4)")
    op.execute("ALTER TABLE chrome_sync.order_lines ADD COLUMN IF NOT EXISTS main_order_status INTEGER")
    op.execute("ALTER TABLE chrome_sync.order_lines ADD COLUMN IF NOT EXISTS sku_display_status INTEGER")


def downgrade() -> None:
    # orders
    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS status TEXT")
    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ")
    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS shipped_at TIMESTAMPTZ")
    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ")
    op.execute("ALTER TABLE chrome_sync.orders ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ")

    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS main_order_status")
    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS sku_display_status")
    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS fulfillment_type")
    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS pay_method")
    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS sale_region")
    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS shipping_fee")
    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS update_time")
    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS latest_rts_time")
    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS latest_tts_time")
    op.execute("ALTER TABLE chrome_sync.orders DROP COLUMN IF EXISTS buyer_nickname")

    op.execute("DROP INDEX IF EXISTS chrome_sync.ix_orders_main_order_status")
    op.execute("CREATE INDEX IF NOT EXISTS ix_orders_status ON chrome_sync.orders(status)")

    # order_lines
    op.execute("ALTER TABLE chrome_sync.order_lines ADD COLUMN IF NOT EXISTS seller_sku TEXT")
    op.execute("ALTER TABLE chrome_sync.order_lines ADD COLUMN IF NOT EXISTS line_status TEXT")

    op.execute("ALTER TABLE chrome_sync.order_lines DROP COLUMN IF EXISTS total_price")
    op.execute("ALTER TABLE chrome_sync.order_lines DROP COLUMN IF EXISTS main_order_status")
    op.execute("ALTER TABLE chrome_sync.order_lines DROP COLUMN IF EXISTS sku_display_status")
