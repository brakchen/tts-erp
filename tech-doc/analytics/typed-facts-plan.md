# Analytics Typed 事实表 — 技术方案（typed-facts-plan）

> 状态: **参数已确认，范围/时序待拍板**（决策记录见 context-mode
> `analytics-typed-facts-decisions`，2026-09-05）
> 关联: `tech-doc/analytics/reorg-plan.md`（前一轮：schema 收成 ad_raw + view）、
> `tech-doc/analytics/dump-architecture.md`、`biz-doc/analytics/*`（payload 语义）
> ✅ 2026-09-05 命名 gate 已解锁（ADR-0003 §2.6 已实施，live DB + 代码均已切到
> `shop_pk` / `spu_pk`）；本方案 SQL 以新命名为准（已同步于 §5）。

## 1. 解决的问题（前一轮遗留的最大设计债）

`ad_product_links` VIEW 目前**每次查询对全量历史 ad_raw 做 jsonb 运行时解析**：
`jsonb_array_elements` + 正则校验 cast（`mixed_real_cost ~ '^[0-9]+…'`），
且文档自认缺陷——聚合窗口是全量累计，**没有日维度下钻**（"逐日数据请直接查 ad_raw"）。
运营要看"今天"的分钟级变化时，只能靠这个 parse-on-read view。

## 2. 目标（一句话）

新增 **typed 日事实表** `analytics.ad_campaign_product_daily`，由**高频全量重算 job**
（每 1-2 分钟）从 `ad_raw` 派生；`ad_product_links` VIEW 改读事实表（同一份解析逻辑，
消灭 parse-on-read 双源）；当天数据随扩展 30s-5min 重 dump + 1-2min 重算保持分钟级新鲜。

## 3. 已确认参数（2026-09-05 用户拍板）

| 项 | 值 |
| --- | --- |
| 扩展当天重 dump 频率 | 最长 ≤5 min，可 30s 一次（当日 ad_raw 行分钟级更新，5 元组 upsert 覆盖） |
| typed 层滞后 | ≤1-2 min 可接受 |
| 派生方式 | **高频全量重算 job**（非 ingest 时写、非日级）：数据量小（当前 ~5.9k 行/8 天），全量重算毫秒级 |
| 派生哲学 | ad_raw = 唯一真相源；typed 表可重建（重跑即修复），`calculation_version` + `derived_at` 留痕（对齐 reporting 规范） |
| ingest | 保持单表写 ad_raw，**不回归** reorg 成果 |
| 时区 | server"卖家本地日"可经 commerce 店铺 `region`（国家码）映射推导（现网 VN）；单时区国家无歧义 |

## 4. 范围

**本轮只做 `productAnalyses`**（唯一有消费者、语义文档齐的 storage_key）：
1 张日事实表 + 1 个派生模块 + 1 个 job + VIEW 改底座。

**明确不做**（另案，需先补语义）：

- `sessionAnalyses`（post_session_list：row=session 粒度，语义细节缺 biz-doc）
- `campaignChangeLogs`（campaign_opt_log_list：行语义无文档）
- 扩展协议变更、ad_raw append-only 改造

## 5. 目标表结构

```sql
-- analytics.ad_campaign_product_daily —— typed 日事实（派生，可重建）
CREATE TABLE analytics.ad_campaign_product_daily (
    seller_id        text NOT NULL,          -- TikTok shop_id（ad_raw 5 元组）
    advertiser_id    text NOT NULL,
    campaign_id      text NOT NULL,          -- 广告计划 ID
    product_id       text NOT NULL,          -- SPU ID（post_product_list table[] 行 key）
    day              date NOT NULL,          -- 插件上报日
    orders           bigint NOT NULL DEFAULT 0,          -- onsite_roi2_shopping_sku（出单数，正则 ^[0-9]+$ 校验，脏→0）
    cost             numeric(20,4) NOT NULL DEFAULT 0,   -- mixed_real_cost（消耗，正则校验，脏→0）
    gmv              numeric(20,4) NOT NULL DEFAULT 0,   -- onsite_roi2_shopping_value（出单 GMV，同上）
    product_name     text,                   -- 当日观测快照（最后一行值）
    product_status   text,
    gmv_max_bid_type text,
    calculation_version integer NOT NULL,
    derived_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (seller_id, advertiser_id, campaign_id, product_id, day)
);
-- 索引：按 (campaign_id, product_id) 聚合 + 窗口过滤
CREATE INDEX idx_ad_cpd_scope ON analytics.ad_campaign_product_daily
    (seller_id, advertiser_id, campaign_id, product_id, day);
```

- **语义对齐现 view 口径**：同 (campaign,day) 被反复 dump → ad_raw upsert 只剩最新响应 →
  派生行就是"当天最后观测值"（重算以当前 ad_raw 为准，天然分钟级新鲜）。
  旧 dump 只有 product_id 无业绩字段 → orders/cost/gmv=0（保留关联，与现 view 一致）。
- **currency**：payload 无显式币种；广告账户币种（推测 USD）不在行内。
  本轮**不加 currency 列**（避免臆造口径），文档标注 open item；money 仍 numeric(20,4)。
- **ERP 富化（shop_pk / spu_pk）不进事实表**——留在 view/API 层
  LEFT JOIN（seller_id→commerce.shops、product_id→commerce.products_spu，2026-09-05 后命名；
  此前 channel_account_id / channel_product_id 已由 ADR-0003 §2.6 改名）。解耦命名 churn。

## 6. 派生实现（单点解析）

- 新模块 `tts_erp_v2/analytics/derive.py`：
  - `extract_product_daily_rows(raw_row) -> list[DailyRow]`：解析 post_product_list 的
    table[]（复用现 view 的正则校验语义，抽成函数单点）；
  - `recompute_product_daily(sess, calculation_version) -> int`：单事务 **DELETE 全表 +
    全量 INSERT**（数据量小，原子重建；逐行带 version/derived_at），返回写入行数。
- 新 job `tts_erp_v2/jobs/analytics_derive.py`（仿 reporting profit_daily / 旧 retention 的
  `run_job` 模式，只读 ad_raw → 写 typed 表，sync_issues 承接失败）：
  - `JOB_NAME = "analytics.derive"`，注册进 `sync_worker/scheduler.py` `JOBS`，
    `interval_seconds=120`（≤2min 滞后目标；max_instances=1 由 JobSpec 统一保证）。
- 每次重算 bump `calculation_version`（可简化为 重算时间戳序列或自增常量+derived_at）。
- ad_raw 只被读；**ingest / has-data / /dumps 协议零改动**。

## 7. VIEW 改底座（与 job 同窗口）

- `analytics.ad_product_links` 的 `daily` CTE 从"parse ad_raw jsonb"改为
  `SELECT ... FROM analytics.ad_campaign_product_daily`（输出列名/口径逐列不变：
  order_sku_total=SUM(orders)、real_cost_total=SUM(cost)、order_value_total=SUM(gmv)、
  observed_days/first_day/last_day、latest name/status/bid、ERP 富化 JOIN 不变）。
- **上线顺序**：job 先跑成功（typed 表有数据）→ 再 CREATE OR REPLACE VIEW 切底座；
  中间空窗不存在（切之前 view 仍读 ad_raw）。
- 切换后 parse 逻辑只剩 derive.py 一份（消灭双源）。

## 8. 测试（TDD）

- `tests/analytics/test_derive.py`（新）：
  - ad_raw TEST_ 行（正/脏/缺字段）→ derive → typed 行断言（值、0 语义、快照列）；
  - recompute 幂等（跑两遍行数不变）；calculation_version 递增；
- `tests/analytics/test_ad_product_links_view.py`（改）：现有 5 个 view 测试改为
  先插 ad_raw → 调 derive（同一函数）→ 查 view 断言（口径断言不变，仅加 derive 前置）；
- `tests/sync_worker/test_scheduler_jobs_coverage.py`：`EXPECTED_JOB_INTERVALS` /
  JOBS 断言加 `analytics.derive: 120`（JOBS 12 → 13）；
- `tests/api/test_analytics_ad_products_api.py`（若存在，先 grep）：依赖 view 的口径测试
  同样加 derive 前置或保持（view 输出不变 → 应天然通过）。
- 门禁：`bash scripts/test.sh fast` 0 fail。

## 9. 文档同步

- `biz-doc/analytics/ad-product-links-view.md`：视图数据源改 typed 表 + 日粒度下钻说明；
- `tech-doc/analytics/dump-architecture.md` / `AGENTS.md`（analytics schema 表数 1→2）/
  `CHANGELOG.md`；本文件勾状态。

## 10. 上线清单

- [ ] naming lane merge（commerce 新表名合入 master）后基于新基线 rebase 本方案改动
- [ ] migration 0008：建 typed 表（+ 索引）
- [ ] derive 模块 + job + JOBS 注册；重启 tts-erp-sync（job 生效）
- [ ] job 首跑成功 → typed 表有数（与 ad_raw 口径对账：337 对 / 1207.17 / 139）
- [ ] VIEW 切底座（CREATE OR REPLACE）+ 对账（口径不变）
- [ ] `bash scripts/test.sh fast` 0 fail；restart API；冒烟：ad-products 页 337 对不变、
      日维度查询（SQL 直查 typed 表按 day 过滤）可用
- [ ] 观察：当天数值随扩展 dump 分钟级刷新（stdout.log 见 dumps 行 + typed 行数增长）

## 11. 风险与回退

| 风险 | 缓解 |
| --- | --- |
| 与 naming lane 撞车 | 实施 gate：其 merge 后才动 commerce 相关 SQL；事实表只存 text 外键，JOIN 富化留 view 层 |
| view 切底座空窗 | job 先跑通再切；切换原子 CREATE OR REPLACE；回退 = 用 git 里的旧 view 定义重建 |
| parse 语义漂移 | 解析抽单点 derive.extract_product_daily_rows；view 测试锁口径 |
| 脏/缺字段 | 正则校验 + 0 语义与现 view 完全一致，测试覆盖 |
| 扩展端 day 时区语义未钉死 | 本轮不引入 server day 校验/换算；"今天"= typed 行中 day=当天（客户端传），口径不变 |

## 12. 开放项（不影响本轮实施）

1. `sessionAnalyses` / `campaignChangeLogs` typed 化（需先补 payload 语义文档 + 找消费者）；
2. currency 显式化（payload 无币种 → 需广告账户配置或扩展上报）；
3. 店铺显式 timezone 列（region 映射默认 + 人工覆盖）——属 commerce 表改动，排 naming lane 后。
