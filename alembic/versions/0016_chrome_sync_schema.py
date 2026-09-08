"""chrome_sync schema — Chrome 扩展订单/物流/结算数据同步（方案 0016）

Revision ID: 0016_chrome_sync_schema
Revises: 0014_ad_product_links_range
Create Date: 2026-09-08

Chrome 扩展从 TikTok Seller Center 抓取订单/物流/结算 HTTP 响应，
通过 /v2/order-sync/dumps 端点写入本 schema。

7 张表：
- raw_log：同步流水日志（完整 dump 存档，source-of-truth）
- orders / order_lines：订单头 + SKU 级订单行
- shipments / tracking_events：物流包裹 + 轨迹事件
- settlements / settlement_details：结算单头 + SKU 级结算明细

详见 tech-doc/chrome-ext-order-sync-design.md。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0016_chrome_sync_schema"
down_revision: str | None = "0014_ad_product_links_range"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── schema ──────────────────────────────────────────────────────
    op.execute(text("CREATE SCHEMA IF NOT EXISTS chrome_sync"))

    # ── raw_log ─────────────────────────────────────────────────────
    op.execute(
        text(
            """
            CREATE TABLE chrome_sync.raw_log (
                id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                domain          TEXT NOT NULL,
                shop_id         TEXT NOT NULL,
                endpoint        TEXT NOT NULL,
                captured_at     TIMESTAMPTZ NOT NULL,
                request_params  JSONB,
                request_body    JSONB,
                response_body   JSONB NOT NULL,
                parse_error     TEXT,
                rows_written    INT NOT NULL DEFAULT 0,
                source          TEXT NOT NULL DEFAULT 'chrome-ext',
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX ix_raw_log_domain_shop "
            "ON chrome_sync.raw_log (domain, shop_id)"
        )
    )
    op.execute(
        text("CREATE INDEX ix_raw_log_created ON chrome_sync.raw_log (created_at)")
    )
    op.execute(
        text("CREATE INDEX ix_raw_log_endpoint ON chrome_sync.raw_log (endpoint)")
    )
    # comments
    op.execute(text(
        "COMMENT ON TABLE chrome_sync.raw_log IS "
        "'Chrome 扩展同步流水日志。每条 dump 请求一行，只追加不修改，"
        "存完整原始响应，用于审计和数据回溯。'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.raw_log.id IS '自增主键'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.raw_log.domain IS "
        "'同步域：orders=订单, logistics=物流, statements=结算'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.raw_log.shop_id IS 'TikTok 外部店铺 ID'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.raw_log.endpoint IS "
        "'TikTok API 路径，如 /api/fulfillment/order/list'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.raw_log.captured_at IS "
        "'插件在 TikTok 页面抓取响应的时间'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.raw_log.request_params IS "
        "'URL query params，如 {main_order_id: \"...\", offset: 0}'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.raw_log.request_body IS "
        "'POST 请求 body（GET 请求为 NULL）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.raw_log.response_body IS "
        "'TikTok 完整原始响应，source-of-truth，可重跑解析修复业务表'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.raw_log.parse_error IS "
        "'解析失败原因；NULL 表示解析成功'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.raw_log.rows_written IS "
        "'本次解析写入业务表的行数'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.raw_log.source IS "
        "'数据来源标识，默认 chrome-ext'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.raw_log.created_at IS "
        "'后端收到并写入的时间'"
    ))

    # ── orders ──────────────────────────────────────────────────────
    op.execute(
        text(
            """
            CREATE TABLE chrome_sync.orders (
                id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                log_id          BIGINT NOT NULL REFERENCES chrome_sync.raw_log(id),
                shop_id         TEXT NOT NULL,
                order_id        TEXT NOT NULL,
                status          TEXT,
                currency        TEXT,
                payment_amount  NUMERIC(20,4),
                total_amount    NUMERIC(20,4),
                order_time      TIMESTAMPTZ,
                paid_at         TIMESTAMPTZ,
                shipped_at      TIMESTAMPTZ,
                delivered_at    TIMESTAMPTZ,
                cancelled_at    TIMESTAMPTZ,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_orders_shop_order UNIQUE (shop_id, order_id)
            )
            """
        )
    )
    op.execute(
        text("CREATE INDEX ix_orders_shop ON chrome_sync.orders (shop_id)")
    )
    op.execute(
        text("CREATE INDEX ix_orders_status ON chrome_sync.orders (status)")
    )
    op.execute(text(
        "COMMENT ON TABLE chrome_sync.orders IS "
        "'Chrome 扩展同步的 TikTok 订单头，来自 order/list 响应'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.id IS '自增主键'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.log_id IS "
        "'关联 raw_log.id，溯源本次数据来自哪条 dump'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.shop_id IS 'TikTok 外部店铺 ID'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.order_id IS 'TikTok main_order_id'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.status IS "
        "'订单状态（待实测确认字段路径）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.currency IS "
        "'订单币种，ISO 4217（待实测确认字段路径）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.payment_amount IS "
        "'买家实付金额（待实测确认字段路径）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.total_amount IS "
        "'订单总金额（待实测确认字段路径）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.order_time IS "
        "'下单时间（待实测确认字段路径）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.paid_at IS "
        "'付款时间（paid_time，待实测确认；0 或缺失为 NULL）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.shipped_at IS "
        "'发货时间（shipped_time，待实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.delivered_at IS "
        "'签收时间（delivered_time，待实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.cancelled_at IS "
        "'取消时间（cancelled_time，待实测确认；0 或缺失为 NULL）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.created_at IS '数据入库时间'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.orders.updated_at IS '最后更新时间'"
    ))

    # ── order_lines ─────────────────────────────────────────────────
    op.execute(
        text(
            """
            CREATE TABLE chrome_sync.order_lines (
                id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                log_id          BIGINT NOT NULL REFERENCES chrome_sync.raw_log(id),
                shop_id         TEXT NOT NULL,
                order_id        TEXT NOT NULL,
                sku_id          TEXT NOT NULL,
                product_id      TEXT,
                product_name    TEXT,
                variant_name    TEXT,
                image_url       TEXT,
                seller_sku      TEXT,
                quantity        NUMERIC(20,4),
                unit_price      NUMERIC(20,4),
                currency        TEXT,
                line_status     TEXT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_order_lines_order_sku UNIQUE (shop_id, order_id, sku_id)
            )
            """
        )
    )
    op.execute(text(
        "COMMENT ON TABLE chrome_sync.order_lines IS "
        "'Chrome 扩展同步的 TikTok 订单行（SKU 级），"
        "来自 order/list 的 sku_module/fulfill_line_module'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.id IS '自增主键'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.log_id IS "
        "'关联 raw_log.id，溯源本次数据来自哪条 dump'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.shop_id IS 'TikTok 外部店铺 ID'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.order_id IS "
        "'关联 chrome_sync.orders.order_id'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.sku_id IS "
        "'TikTok sku_id，同订单内唯一'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.product_id IS 'TikTok product_id'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.product_name IS '商品名称快照'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.variant_name IS 'SKU 名称快照'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.image_url IS 'SKU 图片 URL 快照'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.seller_sku IS "
        "'卖家自定义 SKU 编码'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.quantity IS '购买数量'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.unit_price IS "
        "'SKU 单价（sale_price.amount）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.currency IS 'SKU 币种，ISO 4217'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.line_status IS "
        "'行状态，如 DELIVERED/CANCELLED'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.created_at IS '数据入库时间'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.order_lines.updated_at IS '最后更新时间'"
    ))

    # ── shipments ───────────────────────────────────────────────────
    op.execute(
        text(
            """
            CREATE TABLE chrome_sync.shipments (
                id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                log_id          BIGINT NOT NULL REFERENCES chrome_sync.raw_log(id),
                shop_id         TEXT NOT NULL,
                order_id        TEXT NOT NULL,
                package_id      TEXT NOT NULL,
                tracking_number TEXT,
                carrier_name    TEXT,
                status          TEXT,
                shipped_at      TIMESTAMPTZ,
                delivered_at    TIMESTAMPTZ,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_shipments_shop_pkg UNIQUE (shop_id, package_id)
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX ix_shipments_order "
            "ON chrome_sync.shipments (shop_id, order_id)"
        )
    )
    op.execute(text(
        "COMMENT ON TABLE chrome_sync.shipments IS "
        "'Chrome 扩展同步的 TikTok 物流包裹，"
        "来自 logistic_detail/list 的 package_list[]'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.shipments.id IS '自增主键'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.shipments.log_id IS "
        "'关联 raw_log.id，溯源本次数据来自哪条 dump'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.shipments.shop_id IS 'TikTok 外部店铺 ID'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.shipments.order_id IS "
        "'关联 chrome_sync.orders.order_id'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.shipments.package_id IS 'TikTok package_id'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.shipments.tracking_number IS "
        "'运单号（tracking_no）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.shipments.carrier_name IS "
        "'物流服务商（logistic_supplier）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.shipments.status IS "
        "'最新轨迹状态（track_list 最后一条）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.shipments.shipped_at IS "
        "'发货时间（首条轨迹时间）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.shipments.delivered_at IS "
        "'签收时间（仅 status 含 delivered 时填入）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.shipments.created_at IS '数据入库时间'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.shipments.updated_at IS '最后更新时间'"
    ))

    # ── tracking_events ─────────────────────────────────────────────
    op.execute(
        text(
            """
            CREATE TABLE chrome_sync.tracking_events (
                id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                log_id          BIGINT NOT NULL REFERENCES chrome_sync.raw_log(id),
                shop_id         TEXT NOT NULL,
                package_id      TEXT NOT NULL,
                event_key       TEXT NOT NULL,
                event_at        TIMESTAMPTZ,
                description     TEXT,
                location        TEXT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_tracking_events_pkg_key
                    UNIQUE (shop_id, package_id, event_key)
            )
            """
        )
    )
    op.execute(text(
        "COMMENT ON TABLE chrome_sync.tracking_events IS "
        "'Chrome 扩展同步的物流轨迹事件，"
        "来自 logistic_detail/list 的 track_list[]'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.tracking_events.id IS '自增主键'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.tracking_events.log_id IS "
        "'关联 raw_log.id，溯源本次数据来自哪条 dump'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.tracking_events.shop_id IS "
        "'TikTok 外部店铺 ID'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.tracking_events.package_id IS "
        "'关联 chrome_sync.shipments.package_id'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.tracking_events.event_key IS "
        "'合成唯一键，如 {package_id}_{index}'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.tracking_events.event_at IS '轨迹发生时间'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.tracking_events.description IS "
        "'轨迹描述原文（track_status）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.tracking_events.location IS '轨迹地点'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.tracking_events.created_at IS '数据入库时间'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.tracking_events.updated_at IS '最后更新时间'"
    ))

    # ── settlements ─────────────────────────────────────────────────
    op.execute(
        text(
            """
            CREATE TABLE chrome_sync.settlements (
                id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                log_id              BIGINT NOT NULL REFERENCES chrome_sync.raw_log(id),
                shop_id             TEXT NOT NULL,
                statement_id        TEXT NOT NULL,
                statement_version   INT NOT NULL DEFAULT 0,
                bill_period         TEXT,
                period_start        DATE,
                period_end          DATE,
                settlement_time     TIMESTAMPTZ,
                settlement_id       TEXT,
                payment_id          TEXT,
                payment_status      TEXT,
                statement_type      INT,
                payment_pending_reason INT,
                settle_amount       NUMERIC(20,4),
                earning_amount      NUMERIC(20,4),
                fee_amount          NUMERIC(20,4),
                adjust_amount       NUMERIC(20,4),
                payable_amount      NUMERIC(20,4),
                shipping_amount     NUMERIC(20,4),
                total_reserve_amount NUMERIC(20,4),
                currency            TEXT,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_settlements_shop_stmt
                    UNIQUE (shop_id, statement_id, statement_version)
            )
            """
        )
    )
    op.execute(
        text("CREATE INDEX ix_settlements_shop ON chrome_sync.settlements (shop_id)")
    )
    op.execute(text(
        "COMMENT ON TABLE chrome_sync.settlements IS "
        "'Chrome 扩展同步的 TikTok 结算单头，来自 statement/list/detail'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.id IS '自增主键'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.log_id IS "
        "'关联 raw_log.id，溯源本次数据来自哪条 dump'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.shop_id IS "
        "'TikTok 外部店铺 ID'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.statement_id IS "
        "'TikTok statement_id（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.statement_version IS "
        "'结算版本号（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.bill_period IS "
        "'账期原始文本（✅ 实测确认），如 2026-09-01~2026-09-07'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.period_start IS "
        "'账期起始日（从 bill_period 解析派生）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.period_end IS "
        "'账期结束日（从 bill_period 解析派生）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.settlement_time IS "
        "'结算时间（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.settlement_id IS "
        "'TikTok settlement_id（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.payment_id IS "
        "'TikTok payment_id（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.payment_status IS "
        "'打款状态（✅ 实测确认，int → TEXT）：PENDING / PAID / FAILED'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.statement_type IS "
        "'结算单类型（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.payment_pending_reason IS "
        "'打款待处理原因（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.settle_amount IS "
        "'结算金额（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.earning_amount IS "
        "'收入金额（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.fee_amount IS "
        "'费用金额（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.adjust_amount IS "
        "'调整金额（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.payable_amount IS "
        "'应付金额（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.shipping_amount IS "
        "'运费金额（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.total_reserve_amount IS "
        "'预留金额（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.currency IS "
        "'币种，ISO 4217（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.created_at IS '数据入库时间'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlements.updated_at IS '最后更新时间'"
    ))

    # ── settlement_details ──────────────────────────────────────────
    op.execute(
        text(
            """
            CREATE TABLE chrome_sync.settlement_details (
                id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                log_id                  BIGINT NOT NULL REFERENCES chrome_sync.raw_log(id),
                shop_id                 TEXT NOT NULL,
                statement_id            TEXT NOT NULL,
                statement_version       INT NOT NULL DEFAULT 0,
                sku_detail_id           TEXT NOT NULL,
                trade_order_id          TEXT,
                sku_id                  TEXT,
                product_name            TEXT,
                sku_name                TEXT,
                quantity                NUMERIC(20,4),
                settlement_status       TEXT,
                placed_time             TIMESTAMPTZ,
                settlement_amount       NUMERIC(20,4),
                earning_amount          NUMERIC(20,4),
                fees_amount             NUMERIC(20,4),
                currency                TEXT,
                fee_components          JSONB,
                seller_web_cut_flow     BOOL,
                seller_app_cut_flow     BOOL,
                created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_settlement_details_shop_sku
                    UNIQUE (shop_id, sku_detail_id)
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX ix_settlement_details_stmt "
            "ON chrome_sync.settlement_details (shop_id, statement_id)"
        )
    )
    op.execute(text(
        "COMMENT ON TABLE chrome_sync.settlement_details IS "
        "'Chrome 扩展同步的 SKU 级结算明细 + 费用拆分，"
        "来自 statement/transaction/detail'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.id IS '自增主键'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.log_id IS "
        "'关联 raw_log.id，溯源本次数据来自哪条 dump'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.shop_id IS "
        "'TikTok 外部店铺 ID'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.statement_id IS "
        "'关联 chrome_sync.settlements.statement_id（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.statement_version IS "
        "'关联 chrome_sync.settlements.statement_version（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.sku_detail_id IS "
        "'TikTok statement_sku_detail_id（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.trade_order_id IS "
        "'TikTok trade_order_id（✅ 实测确认，与 main_order_id 映射关系待验证）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.sku_id IS "
        "'TikTok sku_id（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.product_name IS "
        "'商品名称（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.sku_name IS "
        "'SKU 名称（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.quantity IS "
        "'购买数量（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.settlement_status IS "
        "'结算状态（✅ 实测确认，int → TEXT）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.placed_time IS "
        "'下单时间（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.settlement_amount IS "
        "'结算金额（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.earning_amount IS "
        "'收入金额（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.fees_amount IS "
        "'费用总金额（✅ 实测确认: fees.amount）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.currency IS "
        "'币种，ISO 4217（✅ 实测确认）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.fee_components IS "
        "'递归展开后的扁平费用列表 [{code, amount, currency}]"
        "（✅ 实测确认: in_come.fee_list + out_come.fee_list）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.seller_web_cut_flow IS "
        "'卖家网页端扣款流程标记（✅ 实测确认，顶层字段）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.seller_app_cut_flow IS "
        "'卖家 APP 端扣款流程标记（✅ 实测确认，顶层字段）'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.created_at IS "
        "'数据入库时间'"
    ))
    op.execute(text(
        "COMMENT ON COLUMN chrome_sync.settlement_details.updated_at IS "
        "'最后更新时间'"
    ))


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS chrome_sync.settlement_details CASCADE"))
    op.execute(text("DROP TABLE IF EXISTS chrome_sync.settlements CASCADE"))
    op.execute(text("DROP TABLE IF EXISTS chrome_sync.tracking_events CASCADE"))
    op.execute(text("DROP TABLE IF EXISTS chrome_sync.shipments CASCADE"))
    op.execute(text("DROP TABLE IF EXISTS chrome_sync.order_lines CASCADE"))
    op.execute(text("DROP TABLE IF EXISTS chrome_sync.orders CASCADE"))
    op.execute(text("DROP TABLE IF EXISTS chrome_sync.raw_log CASCADE"))
    op.execute(text("DROP SCHEMA IF EXISTS chrome_sync"))
