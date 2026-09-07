"""analytics.ad_product_links 视图升级 — live-only + daily 过渡回退（方案 0014）

Revision ID: 0014_ad_product_links_range
Revises: 0013_ad_sync_audit
Create Date: 2026-09-07

语义（tech-doc/analytics/range-aggregate-history-sync.md §7/§8，Design A D-7）：

- 每个 (scope, endpoint, campaign) 的 live 行（kind history/today）是权威累计快照：
  history [S..T-1] 整段聚合 + today [T..T] 30s 快照（today 仅当
  today.day_end > history.day_end 时计入，防跨天推进间隙双计/漏计）。
- 未转换 campaign（无任何 live 行）回退读 legacy daily 行 —— 视图口径不跳变。
- 已转换 campaign 的 legacy daily 行被 live 快照逻辑遮蔽（物理行保留，后续由
  repository 折叠 / retention 清理），不会与 live 重复累计。
- observed_days 由区间推导：live 行按覆盖跨度 (day_end-day_start+1) 计，daily 行
  计 1；first_day=min(day_start)、last_day=max(day_end)。
- (review P2b) campaign 是否已由 live 模型接管（遮蔽 daily）直接判 ad_raw 的
  live 行存在性（live_spans CTE），与响应是否含 product 行解耦。

注意：视图 JOIN 的 commerce 列名 = 当前生产（naming-refactor 后）命名
（commerce.shops.shop_id / products_spu.spu_id / products_spu.shop_pk）。若在
一个未做 naming-refactor 的裸 alembic 库上直接跑本迁移会缺列 —— 生产/测试库均按
“先 alembic 后 naming-refactor” 或 schema_tts_erp.sql + stamp 的方式保证列名一致。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op  # pyright: ignore[reportAttributeAccessIssue]

revision: str = "0014_ad_product_links_range"
down_revision: str | None = "0013_ad_sync_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQL_VIEW = """
CREATE OR REPLACE VIEW analytics.ad_product_links AS
-- 候选原始 product 行（三种 kind 全量，聚合前先做 live/daily 择源）
WITH product_rows AS (
    SELECT r.seller_id,
           r.advertiser_id,
           r.campaign_id,
           r.kind,
           r.day_start,
           r.day_end,
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
-- 该 campaign 有没有任何 live 行（直接查 ad_raw 的 live 行，与响应是否含
-- product 行解耦 —— review P2b）：有 → 遮蔽其 daily 行。
-- live 行即使是“空表快照”（区间内无 SPU），也说明该 campaign 已由 live
-- 模型接管，残留 daily 行不得浮出（防重复累计/口径混用）。
live_spans AS (
    SELECT DISTINCT seller_id, advertiser_id, campaign_id, kind, day_start, day_end
    FROM analytics.ad_raw
    WHERE endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'
      AND kind IN ('history', 'today')
),
-- live 有效集：history 全取；today 仅当不存在 day_end >= 它的 history 行
-- （防跨天推进间隙双计）。同样基于 ad_raw 的 live 行 span 判定，不依赖响应行。
live AS (
    SELECT pr.*
    FROM product_rows pr
    WHERE pr.kind = 'history'
       OR (pr.kind = 'today' AND NOT EXISTS (
               SELECT 1 FROM live_spans hs
               WHERE hs.kind = 'history'
                 AND hs.seller_id = pr.seller_id
                 AND hs.advertiser_id = pr.advertiser_id
                 AND hs.campaign_id = pr.campaign_id
                 AND hs.day_end >= pr.day_end
           ))
),
-- 未转换 campaign（无任何 live 行）回退 legacy daily 行，口径与旧版一致
daily_fallback AS (
    SELECT pr.*
    FROM product_rows pr
    WHERE pr.kind = 'daily'
      AND NOT EXISTS (
              SELECT 1 FROM live_spans hl
              WHERE hl.seller_id = pr.seller_id
                AND hl.advertiser_id = pr.advertiser_id
                AND hl.campaign_id = pr.campaign_id
          )
),
src AS (
    SELECT * FROM live
    UNION ALL
    SELECT * FROM daily_fallback
),
-- 每 (campaign, SPU) 最后覆盖日（live 或 daily）的商品信息
latest AS (
    SELECT DISTINCT ON (seller_id, advertiser_id, campaign_id, product_id)
        seller_id, advertiser_id, campaign_id, product_id,
        product_name, product_status, gmv_max_bid_type
    FROM src
    ORDER BY seller_id, advertiser_id, campaign_id, product_id,
             day_end DESC
)
SELECT d.seller_id,
       d.advertiser_id,
       d.campaign_id,
       d.product_id,
       l.product_name,
       l.product_status,
       l.gmv_max_bid_type,
       COALESCE(SUM((d.day_end - d.day_start) + 1), 0)::bigint AS observed_days,
       MIN(d.day_start)                          AS first_day,
       MAX(d.day_end)                            AS last_day,
       COALESCE(SUM(d.order_sku), 0)::bigint               AS order_sku_total,
       COALESCE(SUM(d.real_cost), 0)::numeric(20, 4)       AS real_cost_total,
       COALESCE(SUM(d.order_value), 0)::numeric(20, 4)     AS order_value_total,
       ca.id AS shop_pk,
       cp.id AS spu_pk
FROM src d
JOIN latest l USING (seller_id, advertiser_id, campaign_id, product_id)
LEFT JOIN commerce.shops ca
       ON ca.platform = 'tiktok'
      AND ca.shop_id = d.seller_id
LEFT JOIN commerce.products_spu cp
       ON cp.shop_pk = ca.id
      AND cp.spu_id = d.product_id
GROUP BY d.seller_id, d.advertiser_id, d.campaign_id, d.product_id,
         l.product_name, l.product_status, l.gmv_max_bid_type,
         ca.id, cp.id
"""


def upgrade() -> None:
    # pi-lens-ignore: python-sql-injection — literal DDL view body, no interpolation
    op.execute(text(_SQL_VIEW))


def downgrade() -> None:
    # 还原到 0006 语义（逐日 daily 行聚合）。downgrade 仅作参考——
    # 生产回滚以 schema_tts_erp.sql / 旧 migration 为准。
    op.execute(text("DROP VIEW IF EXISTS analytics.ad_product_links"))
