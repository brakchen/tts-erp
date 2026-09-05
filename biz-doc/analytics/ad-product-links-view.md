# ad_product_links 视图 — 广告(计划) × 商品(SPU) 关联 + 出单量/消耗

> `analytics.ad_product_links`（alembic migration `0006_ad_product_links_view`，
> DB 层视图，无 HTTP 端点——与 `linkage.effective_product_links` 同款模式）
> 从 `analytics.ad_raw` 的 `post_product_list` 原始 dump 派生。

## 1. 解决的问题

`ad_raw` 每条 dump = 一次 TikTok OEC 广告原始 HTTP 交换，按 5 元组
(seller, advertiser, endpoint, day, campaign_id) 唯一。其中
`post_product_list`（商品分析）响应的 `data.table[]` **每一行 = 该广告计划
当天挂的一个 SPU**（`product_id` == `table_v2` 的 `spu_id`，见
post-product-list-field-semantics.md），每行自带当天的出单数 / 消耗 / GMV。

要回答「哪些广告(计划)在推哪些商品、各自花了多少、出了多少单」，
需要把 campaign ↔ SPU 关联从 JSONB 原始记录里解出来并跨天聚合——
就是本视图。

## 2. 粒度与口径

| 项 | 定义 |
| --- | --- |
| 粒度 | 每 (seller_id, advertiser_id, campaign_id, product_id) 一行 |
| 数据源 | `analytics.ad_raw`，仅 endpoint = `/oec_ads/shopping/v1/oec/stat/post_product_list` |
| 聚合窗口 | 视图每次查询实时计算 ad_raw 里**已捕获的全部 day**；行内 `observed_days` / `first_day` / `last_day` 标注窗口（ad_raw 不 purge，会随插件持续 dump 增长） |
| 同 (campaign, day) 重复 dump | ad_raw 5 元组 upsert 保证只有最新响应，无重复计数 |
| 行数上限 | 一个 campaign 一天可挂多个 SPU（观测到最多 26），同 SPU 也可被多个 campaign 挂 → 对级 N:M |

### 2.1 指标列（业务语义，来源 `user spec` / TikTok "Orders (SKU)" 口径）

| 列 | 业务含义 | 算法 |
| --- | --- | --- |
| `order_sku_total` | **出单量合计**（TikTok "Orders (SKU)"，按 SKU 计件；GMV Max 归因以 SPU 为 key，**含自然单**，非"纯广告增量"口径） | `SUM(onsite_roi2_shopping_sku)`，行值字符串先过 `^[0-9]+$` 校验再 `::bigint` |
| `real_cost_total` | **广告消耗合计**（真实消耗） | `SUM(mixed_real_cost)`，字符串先过 `^[0-9]+(\.[0-9]+)?$` 校验再 `::numeric(20,4)` |
| `order_value_total` | 出单 GMV 合计 | `SUM(onsite_roi2_shopping_value)`，同上 |

缺失 / 非数字业绩字段按 NULL 处理（SUM 忽略 → 计 0）。修复前 schema
（`2026-09-04 ~01:28` 之前，dump 行只有 `product_id`）的旧行在视图里
**保留关联、业绩为 0**——不会让"挂了哪些商品"的关联信息丢失。

### 2.2 商品信息列（取该 campaign×SPU **最后观测日**那一行的值）

`product_name` / `product_status` / `gmv_max_bid_type`。

### 2.3 ERP 富化列（LEFT JOIN，不命中为 NULL）

| 列 | 关联 | 说明 |
| --- | --- | --- |
| `shop_pk` | `commerce.shops`（platform='tiktok'，`external_account_id` = ad seller_id） | seller 级；同店铺所有行相同 |
| `spu_pk` | `commerce.products_spu`（shop_pk + `external_product_id` = SPU） | 商品级；SPU 不在同步目录（下架/未同步）时为 NULL |

## 3. 关键澄清

1. **campaign_id = 广告(计划)ID**，不是行内字段——来自 dump 的 5 元组作用域。
   product 行自身不带 campaign_id（query_list 里请求了该列但响应不返回）。
2. **出单数/消耗是 per-SPU 独立计数**：看 campaign 整体表现需要按
   campaign 聚合（`SUM`），不能只看第一行（多 SPU campaign 会低估）。
3. **广告≠纯广告效果**：Product GMV Max 归因含付费 + 自然订单，
   平台报表不能直接当"广告增量利润"用（见 GMV Max 归因边界）。
4. 货币 = 广告账户币种，无汇率换算；day 为插件请求的自然日（上海时区窗口）。

## 4. 常用查询

```sql
-- 广告计划 × 商品排行（按消耗）
SELECT campaign_id, product_id, left(product_name, 40) AS name,
       order_sku_total AS 出单量, real_cost_total AS 消耗,
       round(order_value_total / NULLIF(real_cost_total, 0), 2) AS roi
FROM analytics.ad_product_links
ORDER BY real_cost_total DESC LIMIT 50;

-- 某商品的广告花费分布（同 SPU 被多个广告推）
SELECT campaign_id, real_cost_total AS 消耗, order_sku_total AS 出单量
FROM analytics.ad_product_links
WHERE product_id = '1736527242804888823'
ORDER BY real_cost_total DESC;

-- 按广告计划汇总（多 SPU campaign 合计）→ 与 session 维度对照
SELECT campaign_id, sum(order_sku_total) AS 出单量, sum(real_cost_total) AS 消耗
FROM analytics.ad_product_links GROUP BY campaign_id;

-- 只算最近 N 天：本视图是聚合窗口视图，逐日数据请直接查 ad_raw
-- （展开 SQL 见 endpoint-join-keys.md §4；要按天过滤就在 daily CTE 上展开）
```

## 5. 验证记录（2026-09-05）

- 视图合计 vs 直接对 ad_raw 行正则求和：消耗 1207.17 == 1207.17，出单 139 == 139 ✓
- 当前数据形态：337 个 campaign×SPU 对（228 个广告计划 / 111 个 SPU；
  219 个单商品广告、9 个多商品广告，最多挂 26 个商品）；窗口 2026-08-28 ~ 2026-09-05。
- 该 seller 下 111 个被投 SPU 中 106 个已在 `commerce.products_spu` 目录，
  视图可带出内部 `spu_pk` 继续 JOIN 销售/成本报表。

## 6. 变更方式

改视图语义 = 新 alembic migration 里 `CREATE OR REPLACE VIEW` + 重跑
`scripts/regen_schema.py` 同步 `schema_tts_erp.sql`；测试在
`tests/analytics/test_ad_product_links_view.py`（TEST_ 前缀数据）。
