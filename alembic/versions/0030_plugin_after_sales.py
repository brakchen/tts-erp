"""plugin.after_sales 表：售后/退款结构化数据（2026-09-13 lane feat/after-sales-table）

背景（AGENTS.md §3.6 + order-domain-business-rules.md §3 TODO #2）：

- 当前 chrome 扩展抓的 /return_refund/202309/cancellations/search 响应只落
  plugin.raw_log + integration.raw_records，**无结构化业务表**
- 同 schema 7 张表（orders / order_lines / shipments / tracking_events /
  settlements / settlement_details / ad_*）的 FK 链断裂，售后数据无法
  跨域 JOIN
- 11:31 burst / 13:14+ settlement burst 均**未抓到**该 endpoint（卖家未操作
  取消/售后 tab），本次按 order-domain-business-rules.md §3 的描述设计表
  结构（BUYER_CANCEL/CANCEL 两种 cancel_type、CANCELLATION_REQUEST_COMPLETE 状态、
  行项目级 cancel_line_items）

变更（prod-shape dbname 需 ``ALLOW_PROD_DESTRUCTIVE=1``；
由 ``alembic/env.py`` 中央 guard 拦截；agent 不自动跑 alembic upgrade）：

- CREATE TABLE IF NOT EXISTS plugin.after_sales
- CREATE TABLE IF NOT EXISTS plugin.after_sale_items

对应代码侧 commit（同一 lane 同时改）：
- tts_erp_v2/db/models/plugin.py: 加 ChromeAfterSale + ChromeAfterSaleItem
- tts_erp_v2/plugin/orders/repository.py: 加 upsert_after_sale + upsert_after_sale_item
- tts_erp_v2/plugin/orders/parser.py: 加 parse_after_sales_response (stub, 0 hit 数据)
- schema_tts_erp.sql: 同步加 2 张表 CREATE TABLE
- tech-doc/intercept-plugin-canonical.md: §1 加表清单 + §3 加 4 域 ID 映射
- tech-doc/tiktok-seller-center-api-catalog.md: §7.4.4 reverse_module 加跨表 cross-ref
- tests/plugin/orders/test_parser_after_sales.py: 加 parser 测试

downgrade DROP 两张表（无数据回填，因为扩展尚未抓到该 endpoint）。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0030_plugin_after_sales"
down_revision: str | None = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. plugin.after_sales (header)
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin.after_sales (
            id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            log_id              BIGINT NOT NULL REFERENCES plugin.raw_log(id),
            shop_id             TEXT NOT NULL,
            cancel_id           TEXT NOT NULL,
            cancel_type         TEXT NOT NULL,
            cancel_status       TEXT NOT NULL,
            main_order_id       TEXT,
            reason              TEXT,
            request_time        TIMESTAMP WITH TIME ZONE,
            complete_time       TIMESTAMP WITH TIME ZONE,
            raw_payload         JSONB,
            created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT uq_after_sales_shop_cancel UNIQUE (shop_id, cancel_id)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_after_sales_shop_order "
        "ON plugin.after_sales (shop_id, main_order_id);"
    )

    # 2. plugin.after_sale_items (line items)
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin.after_sale_items (
            id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            log_id                  BIGINT NOT NULL REFERENCES plugin.raw_log(id),
            shop_id                 TEXT NOT NULL,
            cancel_id               TEXT NOT NULL,
            line_item_id            TEXT NOT NULL,
            order_line_item_id      TEXT,
            sku_id                  TEXT,
            product_id              TEXT,
            quantity                NUMERIC(20, 4),
            refund_amount           NUMERIC(20, 4),
            currency                TEXT,
            raw_payload             JSONB,
            created_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            updated_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT uq_after_sale_items_shop_line UNIQUE (shop_id, line_item_id)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_after_sale_items_shop_cancel "
        "ON plugin.after_sale_items (shop_id, cancel_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_after_sale_items_shop_order_line "
        "ON plugin.after_sale_items (shop_id, order_line_item_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS plugin.after_sale_items CASCADE;")
    op.execute("DROP TABLE IF EXISTS plugin.after_sales CASCADE;")
