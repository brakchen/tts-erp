"""Add plugin.order_details and plugin.order_timeline tables.

- plugin.order_details: order/get 全量详情（独立于 plugin.orders）
- plugin.order_timeline: order/history 状态变更时间线
"""

from alembic import op

revision: str = "0035_order_detail_history_tables"
down_revision: str | None = "0034_plugin_tracking_action_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── plugin.order_details（order/get 全量详情）──
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin.order_details (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            shop_id text NOT NULL,
            order_id text NOT NULL,
            -- trade_order_module
            create_time timestamptz,
            payment_time timestamptz,
            pay_method text,
            sale_region text,
            fulfillment_type integer,
            latest_rts_time timestamptz,
            latest_tts_time timestamptz,
            close_sla_time timestamptz,
            -- price_module
            sub_total numeric(20,4),
            grand_total numeric(20,4),
            shipping_fee numeric(20,4),
            platform_discount numeric(20,4),
            seller_discount numeric(20,4),
            origin_sale_price numeric(20,4),
            shipping_origin_fee numeric(20,4),
            shipping_fee_discount_seller numeric(20,4),
            shipping_fee_discount_platform numeric(20,4),
            currency text,
            promotion_infos jsonb,
            -- buyer_info_module
            buyer_nickname text,
            buyer_address jsonb,
            -- reverse_module（退货，可能多条，取第一条摘要）
            reverse_status integer,
            reverse_type integer,
            reverse_reason text,
            reverse_order_id text,
            cancelled_time timestamptz,
            -- delivery_module（物流摘要）
            tracking_number text,
            warehouse_id text,
            warehouse_name text,
            warehouse_region text,
            buyer_region text,
            logistics_service_name text,
            logistics_service_level text,
            carrier_name text,
            carrier_id text,
            -- pkg_attr
            weight_value text,
            weight_unit integer,
            dimension_length text,
            dimension_width text,
            dimension_height text,
            dimension_unit integer,
            -- order_status_module
            main_order_status integer,
            main_sub_order_status integer,
            sku_display_status integer,
            -- raw
            raw_payload jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_order_details_shop_order UNIQUE (shop_id, order_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_order_details_shop "
        "ON plugin.order_details (shop_id)"
    )

    # ── plugin.order_timeline（order/history 时间线）──
    op.execute("""
        CREATE TABLE IF NOT EXISTS plugin.order_timeline (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            shop_id text NOT NULL,
            order_id text NOT NULL,
            event_index integer NOT NULL,
            description text,
            event_at timestamptz,
            detail text,
            raw_payload jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_order_timeline_shop_order_idx
                UNIQUE (shop_id, order_id, event_index)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_order_timeline_shop_order "
        "ON plugin.order_timeline (shop_id, order_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS plugin.order_timeline")
    op.execute("DROP TABLE IF EXISTS plugin.order_details")
