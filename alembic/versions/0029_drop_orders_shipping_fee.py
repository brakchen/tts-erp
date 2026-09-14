"""drop plugin.orders.shipping_fee 列（2026-09-14 用户拍板）

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-14

背景（2026-09-14，REMOVE_ORDER_SHIPPING_FEE lane）：

- ``plugin.orders.shipping_fee`` 由 0017 加进 ``chrome_sync.orders``，随 0022
  rename schema 进 ``plugin``，2022 chrome-sync / 0024 analytics-to-plugin 都未触及
- 该列只解析 ``trade_order_module.shipping_fee.price_val`` 一个上游字段；
  业务侧无人使用（搜索整个 tts-erp 仓库也找不到读这条列的代码或文档），属
  「加进来但从未被消费」的列
- 同语义信息已隐含在 ``total_amount / payment_amount``（grand_total = sum(sku)+shipping_fee），
  无需单独保留；详情见 tech-doc/tiktok-seller-center-api-catalog.md §4.5

变更（destructive, prod-shape dbname 需 ``ALLOW_PROD_DESTRUCTIVE=1``，
由 ``alembic/env.py`` 中央 guard 拦截；agent 不自动跑 alembic upgrade）：

- DROP COLUMN IF EXISTS plugin.orders.shipping_fee
- 删除 ``COMMENT ON COLUMN plugin.orders.shipping_fee IS '...'``（如有遗留）

对应代码侧 commit（同一 lane 同时改）：
- tts_erp_v2/plugin/orders/parser.py: 删 ``shipping_fee = ...`` 提取 + 调用参数
- tts_erp_v2/plugin/orders/repository.py::upsert_order: 删参数 + INSERT/UPDATE
- tts_erp_v2/db/models/plugin.py::ChromeOrder: 删字段
- schema_tts_erp.sql: 同步删列定义
- tech-doc/chrome-ext-order-sync-design.md §3.3 + tech-doc/tiktok-seller-center-api-catalog.md
  §4.5: 同步删/改文档
- chrome-plugins/ads-data-sync 端协议 **未变** —— plugin 端未发 shipping_fee，
  协议契约未破坏

downgrade 把列加回来（无数据回填，因为原始列就是 deprecated 字段，不存档历史值）。
"""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 先删 COMMENT（如果 0017 / 0022 / 0024 迁移期间遗留），再 DROP COLUMN。
    op.execute("COMMENT ON COLUMN plugin.orders.shipping_fee IS NULL")
    op.execute("ALTER TABLE plugin.orders DROP COLUMN IF EXISTS shipping_fee")


def downgrade() -> None:
    # 不做数据回填（deprecated 字段没有"老数据"概念）。
    op.execute(
        "ALTER TABLE plugin.orders ADD COLUMN IF NOT EXISTS shipping_fee NUMERIC(20,4)"
    )
