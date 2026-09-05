"""analytics.ad_product_links — 广告计划 × 商品(SPU) 关联 + 业绩视图

Revision ID: 0006_analytics_ad_product_links_view
Revises: 0005_ad_raw_per_unit_day
Create Date: 2026-09-05

背景（用户需求）:
- analytics.ad_raw 存的是 Chrome 扩展抓的 TikTok OEC 广告原始 HTTP 交换。
  post_product_list（商品分析）响应的 ``data.table[]`` 每一行 = 一个 SPU
  （product_id == table_v2 的 spu_id），挂在 dump 的 campaign_id（广告计划）
  作用域下；同一天同一 (campaign, day) 会被反复 dump，5 元组 upsert 保证
  ad_raw 里每个 (seller, advertiser, campaign, day) 只有一行最新响应。
- 需要一张视图，从这些原始记录里把「广告(计划) ↔ 商品(SPU)」的关联和
  出单量 / 广告消耗 挖出来：一个 campaign × SPU 一对行。

本视图 = 派生视图，无 HTTP 端点（DB 层 view，参照 linkage.effective_product_links）:
- 数据源: analytics.ad_raw，仅 post_product_list endpoint（该端点响应行带 SPU）
- 粒度: (seller_id, advertiser_id, campaign_id, product_id) 一 行，
  跨 ad_raw 里已捕获的全部 day 聚合
- 出单量 = SUM(onsite_roi2_shopping_sku)（TikTok "Orders (SKU)"，per-SPU 逐日计数）
- 广告消耗 = SUM(mixed_real_cost)（真实消耗）
- 窗口元数据: observed_days / first_day / last_day —— 让"累计"口径可解释
  （ad_raw 不 purge，随插件持续 dump 而增长）
- ERP 富化: LEFT JOIN commerce.channel_accounts / channel_products
  （seller_id = channel_accounts.external_account_id,SPU = external_product_id），
  命中则带出内部 channel_account_id / channel_product_id，否则 NULL
- 健壮性: 数值字段来自 JSON 字符串,先正则校验再 ::numeric / ::bigint；
  缺失/脏值按 NULL 处理（SUM 忽略），修复前 schema（仅 product_id）的旧行
  仍保留关联但业绩为 0

语义/口径详见 biz-doc/analytics/ad-product-links-view.md。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0006_ad_product_links_view"
down_revision: str | None = "0005_ad_raw_per_unit_day"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pi-lens-ignore: python-sql-injection — literal DDL view body, no interpolation
    op.execute(
        text(
            """
            CREATE OR REPLACE VIEW analytics.ad_product_links AS
            -- daily: post_product_list 响应行 → 每天每 SPU 一行（同 (campaign, day)
            -- 被反复 dump 时 ad_raw 5 元组 upsert 已保证只有最新响应）
            WITH daily AS (
                SELECT r.seller_id,
                       r.advertiser_id,
                       r.campaign_id,
                       r.day,
                       el->>'product_id'                 AS product_id,
                       el->>'product_name'               AS product_name,
                       el->>'product_status'             AS product_status,
                       el->>'gmv_max_bid_type'           AS gmv_max_bid_type,
                       CASE
                           WHEN el->>'mixed_real_cost' ~ '^[0-9]+([.][0-9]+)?$'
                               THEN (el->>'mixed_real_cost')::numeric
                       END                                AS real_cost,
                       CASE
                           WHEN el->>'onsite_roi2_shopping_sku' ~ '^[0-9]+$'
                               THEN (el->>'onsite_roi2_shopping_sku')::bigint
                       END                                AS order_sku,
                       CASE
                           WHEN el->>'onsite_roi2_shopping_value' ~ '^[0-9]+([.][0-9]+)?$'
                               THEN (el->>'onsite_roi2_shopping_value')::numeric
                       END                                AS order_value
                FROM analytics.ad_raw r
                CROSS JOIN LATERAL jsonb_array_elements(
                    r.response -> 'body' -> 'data' -> 'table'
                ) AS el
                WHERE r.endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'
                  AND el ? 'product_id'
                  AND NULLIF(el->>'product_id', '') IS NOT NULL
            ),
            -- latest: 每个 (campaign, SPU) 最后一个观测日的商品信息（名称/状态）
            latest AS (
                SELECT DISTINCT ON (
                    seller_id, advertiser_id, campaign_id, product_id
                )
                    seller_id, advertiser_id, campaign_id, product_id,
                    product_name, product_status, gmv_max_bid_type
                FROM daily
                ORDER BY seller_id, advertiser_id, campaign_id, product_id,
                         day DESC
            )
            SELECT d.seller_id,
                   d.advertiser_id,
                   d.campaign_id,                                  -- 广告计划(广告) ID
                   d.product_id,                                   -- 商品 SPU ID
                   l.product_name,                                 -- 商品名(最后观测日)
                   l.product_status,                               -- 上架状态(最后观测日)
                   l.gmv_max_bid_type,
                   COUNT(DISTINCT d.day)::bigint    AS observed_days,
                   MIN(d.day)                        AS first_day,
                   MAX(d.day)                        AS last_day,
                   COALESCE(SUM(d.order_sku), 0)::bigint AS order_sku_total,  -- 出单量合计
                   COALESCE(SUM(d.real_cost), 0)::numeric(20, 4)    AS real_cost_total,  -- 广告消耗合计
                   COALESCE(SUM(d.order_value), 0)::numeric(20, 4)  AS order_value_total, -- 出单 GMV 合计
                   ca.id                             AS channel_account_id,  -- ERP 内部渠道账户
                   cp.id                             AS channel_product_id    -- ERP 内部商品 key
            FROM daily d
            JOIN latest l USING (seller_id, advertiser_id, campaign_id, product_id)
            LEFT JOIN commerce.channel_accounts ca
                   ON ca.platform = 'tiktok'
                  AND ca.external_account_id = d.seller_id
            LEFT JOIN commerce.channel_products cp
                   ON cp.channel_account_id = ca.id
                  AND cp.external_product_id = d.product_id
            GROUP BY d.seller_id, d.advertiser_id, d.campaign_id, d.product_id,
                     l.product_name, l.product_status, l.gmv_max_bid_type,
                     ca.id, cp.id
            """
        )
    )


def downgrade() -> None:
    # pi-lens-ignore: python-sql-injection — literal DDL, no interpolation
    op.execute(text("DROP VIEW IF EXISTS analytics.ad_product_links"))
