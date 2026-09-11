# 技术方案：逐日/逐月同步 + 结构化存储 + Coverage 断点续传

> 状态：**待评审**
> 影响仓库：`tts-erp`（服务端）+ `chrome-plugins/ads-data-sync`（插件）
> 替代方案：`range-aggregate-history-sync.md`（v3 区间聚合，已搁置——区间覆盖缩窄风险无法结构性解决）
> 关联文档：`dump-architecture.md`（v2 dump 架构）、`reorg-plan.md`（2026-09-05 收敛）

---

## 0. TL;DR

**问题**：当前插件按用户选定的时间范围批量拉取广告数据，存的是原始 JSONB blob，重装/重选范围会覆盖已有数据，导致广告金额波动。查询时靠 VIEW 从 JSONB 实时解析字段，性能差。

**方案**：

1. **表重新设计**：四张表——`ad_today`（今天实时）、`ad_daily`（历史天级）、`ad_monthly`（月级聚合）、`ad_raw_log`（原始请求日志）
2. **写入时预解析**：不再存 JSONB blob，解析 TikTok 响应后存结构化列
3. **独立同步任务**：天级同步和月级同步互相隔离，各自独立
4. **Coverage 断点续传**：`GET /coverage` 批量查询已有数据，插件本地 diff 只补缺失

**核心变化**：

- 新建 `ad_today` / `ad_daily` / `ad_monthly` / `ad_raw_log` 四张表，替代 `ad_raw`
- 核心指标字段名 = TikTok API 原名（`mixed_real_cost` / `onsite_roi2_shopping_sku` 等）
- 插件两个独立同步任务：逐天 + 逐月
- 每个同步任务各自有 coverage 查询，独立断点续传
- 用户只选开始日，结束日由代码固定
- `spu_roi.py` 直接读 `ad_daily` + `ad_today`，不再走 `ad_product_links` VIEW
- ~~`ad_product_links` VIEW 改读新表~~ → **实际结局：删除该视图**（2026-09-11，migration 0020）——`_SQL_ROI_AD` 已直读新表，视图零生产消费者，改读没有意义

### 决策日志（D-*，实施即 lock）

| # | 决策 | 替代/否决 |
| --- | --- | --- |
| D-1 | 四张表：`ad_today` + `ad_daily` + `ad_monthly` + `ad_raw_log` | 单表 `ad_raw`（JSONB blob + VIEW 实时解析） |
| D-2 | 核心指标字段名 = TikTok API 原名 | 重命名为 `real_cost` / `order_sku` 等（需映射记忆） |
| D-3 | 天级和月级同步互相隔离，各自独立 | 月表从天表聚合（耦合，一天数据异常影响月表） |
| D-4 | 写入时预解析结构化字段 | 查询时 VIEW 从 JSONB 解析（性能差） |
| D-5 | 结束日期由代码固定为店铺昨天，用户只选开始日 | 用户可选结束日（重装时选错导致数据丢失） |
| D-6 | 历史天/月数据写入后不可变（`ON CONFLICT DO NOTHING`） | 区间行原地更新（day_end 可被缩窄） |
| D-7 | 今天的数据 30s 滚动刷新，写 `ad_today`（`ON CONFLICT DO UPDATE`） | 今天也固化（数据不准，TikTok 数据会延迟归因） |
| D-8 | `ad_raw_log` 用 `kind` 区分 daily/monthly | 分两张日志表（冗余） |
| D-9 | coverage 查询方案 B：一次返回所有 campaign 的覆盖数据 | 方案 A：逐 campaign 查询（30 次请求） |
| D-10 | `spu_roi.py` 直接读 `ad_daily` + `ad_today` | 继续走 `ad_product_links` VIEW（多一层间接） |
| D-11 | ~~VIEW 改读新表~~ → **已删除**（migration 0020，2026-09-11）| 保留 VIEW 并改读新表 |

---

## 1. 表设计

### 1.1 `plugin.ad_today` — 今天实时表

```sql
CREATE TABLE plugin.ad_today (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- 维度
    seller_id        TEXT NOT NULL,
    advertiser_id    TEXT NOT NULL,
    campaign_id      TEXT NOT NULL,
    product_id       TEXT NOT NULL,
    endpoint         TEXT NOT NULL,
    day              DATE NOT NULL,              -- 店铺当地今天

    -- 核心指标（字段名 = TikTok API 原名）
    mixed_real_cost                   NUMERIC(20,4),
    onsite_roi2_shopping_sku          BIGINT,
    onsite_roi2_shopping_value        NUMERIC(20,4),
    onsite_mixed_real_roi2_shopping   NUMERIC(20,4),

    -- 扩展指标
    metrics_extra    JSONB,

    -- 元数据
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- 唯一键（今天的数据可覆盖）
    CONSTRAINT uq_ad_today UNIQUE (seller_id, advertiser_id, endpoint, campaign_id, product_id, day)
);

CREATE INDEX idx_ad_today_coverage
    ON plugin.ad_today (seller_id, advertiser_id, endpoint, campaign_id, day);
```

**设计要点**：

- 结构和 `ad_daily` 完全一致
- 30s 刷新一次，`ON CONFLICT DO UPDATE`（覆盖）
- 跨天时数据固化到 `ad_daily`，然后清空 `ad_today`

### 1.2 `plugin.ad_daily` — 天级结构化表

```sql
CREATE TABLE plugin.ad_daily (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- 维度
    seller_id        TEXT NOT NULL,
    advertiser_id    TEXT NOT NULL,
    campaign_id      TEXT NOT NULL,
    product_id       TEXT NOT NULL,
    endpoint         TEXT NOT NULL,
    day              DATE NOT NULL,              -- 店铺当地自然日（昨天及以前）

    -- 核心指标（字段名 = TikTok API 原名）
    mixed_real_cost                   NUMERIC(20,4),
    onsite_roi2_shopping_sku          BIGINT,
    onsite_roi2_shopping_value        NUMERIC(20,4),
    onsite_mixed_real_roi2_shopping   NUMERIC(20,4),

    -- 扩展指标（JSONB 透传，query_list 中非核心字段的原始值）
    metrics_extra    JSONB,

    -- 元数据
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- 唯一键：每天每商品每计划每端点只有一行，写入后不可变
    CONSTRAINT uq_ad_daily UNIQUE (seller_id, advertiser_id, endpoint, campaign_id, product_id, day)
);

CREATE INDEX idx_ad_daily_coverage
    ON plugin.ad_daily (seller_id, advertiser_id, endpoint, campaign_id, day);
CREATE INDEX idx_ad_daily_product_day
    ON plugin.ad_daily (product_id, day);
```

**设计要点**：

- 唯一键含 `product_id` + `day` → 每天每商品一行，写入后不可变
- `endpoint` 在键中 → 同一天同一商品可以有 product-analysis 和 session-analysis 两行
- 核心指标字段名 = TikTok API 原名，无需映射
- `metrics_extra` JSONB 放 query_list 中非核心字段，保持扩展性
- 无 `kind` / `day_start` / `day_end` / `idempotency_key` —— 天生就是 daily，语义清晰

### 1.3 `plugin.ad_monthly` — 月级结构化表

```sql
CREATE TABLE plugin.ad_monthly (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- 维度（和 daily 结构一致，day → year_month）
    seller_id        TEXT NOT NULL,
    advertiser_id    TEXT NOT NULL,
    campaign_id      TEXT NOT NULL,
    product_id       TEXT NOT NULL,
    endpoint         TEXT NOT NULL,
    year_month       TEXT NOT NULL,              -- '2026-09'

    -- 核心指标（字段名 = TikTok API 原名，和 daily 完全一致）
    mixed_real_cost                   NUMERIC(20,4),
    onsite_roi2_shopping_sku          BIGINT,
    onsite_roi2_shopping_value        NUMERIC(20,4),
    onsite_mixed_real_roi2_shopping   NUMERIC(20,4),

    -- 扩展指标
    metrics_extra    JSONB,

    -- 元数据
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- 月级唯一键
    CONSTRAINT uq_ad_monthly UNIQUE (seller_id, advertiser_id, endpoint, campaign_id, product_id, year_month)
);

CREATE INDEX idx_ad_monthly_coverage
    ON plugin.ad_monthly (seller_id, advertiser_id, endpoint, campaign_id, year_month);
```

**设计要点**：

- 和 daily 结构一致，唯一区别是 `day DATE` → `year_month TEXT`
- 独立同步，不依赖 daily 数据
- TikTok API 传 `start_time=月初, end_time=月末` → 返回月级聚合数据

### 1.4 `plugin.ad_raw_log` — 原始请求日志表

```sql
CREATE TABLE plugin.ad_raw_log (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- 请求标识
    seller_id        TEXT NOT NULL,
    advertiser_id    TEXT NOT NULL,
    endpoint         TEXT NOT NULL,
    campaign_id      TEXT,
    product_id       TEXT,
    kind             TEXT NOT NULL CHECK (kind IN ('daily', 'today', 'monthly')),

    -- 日期标识
    day              DATE,                       -- daily/today 用
    year_month       TEXT,                       -- monthly 用

    -- 原始 HTTP 交换
    request_url      TEXT NOT NULL,
    request_method   TEXT NOT NULL,
    request_body     JSONB,
    response_status  INT,
    response_body    JSONB,

    -- 元数据
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id       TEXT,
    source           TEXT DEFAULT 'tiktok-shop-data-sync'
);

CREATE INDEX idx_ad_raw_log_day ON plugin.ad_raw_log (day);
CREATE INDEX idx_ad_raw_log_request_id ON plugin.ad_raw_log (request_id);
```

**设计要点**：

- 纯日志表，不参与业务查询
- `kind` 区分 daily / today / monthly
- 保留原始 request/response 用于调试、审计、数据恢复
- 建议 retention 90 天自动清理

---

## 2. 读写依赖全景与改造计划

### 2.1 当前 `ad_raw` 读写依赖

```
插件 ──POST /dumps──→ repository.py ──INSERT──→ ad_raw (JSONB blob)
                                                      │
                                                      │ JSONB 解析（查询时）
                                                      ▼
                                              ad_product_links VIEW
                                                      │
                                    ┌─────────────────┼─────────────────┐
                                    ▼                 ▼                 ▼
                              _SQL_ROI_AD      _SQL_ROI_WINDOW   _SQL_ROI_DRILLDOWN
                                    │                 │                 │
                                    └─────────────────┼─────────────────┘
                                                      ▼
                                              spu_roi.py
                                                      │
                                                      ▼
                                          GET /v2/analytics/spu-roi
                                          GET /v2/analytics/spu-roi/{id}/ads
```

### 2.2 新架构读写依赖

```
插件 ──POST /dumps──→ 服务端解析 ──写入──→ ad_today / ad_daily / ad_monthly + ad_raw_log
                                                │
                                                │ 结构化列（查询时直接读）
                                                ▼
                                    ┌───────────┼───────────┐
                                    ▼           ▼           ▼
                              spu_roi.py   ad_product_links  coverage 查询
                              直接读新表    VIEW 改读新表     读新表
```

### 2.3 改造清单

| 文件 | 当前 | 改造后 |
| --- | --- | --- |
| `repository.py` | `upsert_dump()` 写 `ad_raw` | 新增 `upsert_daily_rows()` / `upsert_monthly_rows()` / `upsert_today_rows()` 写新表 + `ad_raw_log` |
| `repository.py` | `has_data()` 查 `ad_raw` | 新增 `get_coverage_daily()` / `get_coverage_monthly()` 查新表 |
| `repository.py` | `load_campaign_live_rows()` 查 `ad_raw` | 不再需要（coverage 改为批量查） |
| `analytics.py` | `POST /dumps` 解析 v2/v3 协议 | 改为解析 rows 数组，提取结构化字段写新表 |
| `analytics.py` | `GET /cursor` has-data/coverage 模式 | 新增 `GET /coverage` 端点（方案 B 批量） |
| `spu_roi.py` | 读 `ad_product_links` VIEW | 直接读 `ad_daily` + `ad_today`（UNION） |
| `schema_tts_erp.sql` | `ad_product_links` VIEW 从 `ad_raw` JSONB 解析 | 改为从 `ad_daily` + `ad_today` 结构化列读取 |
| `has_data_cache.py` | 缓存 `ad_raw` live 行 | 不再需要（coverage 直接查新表） |

---

## 3. 写入流程（单事务）

```
插件抓到 TikTok 响应（daily / today / monthly）
  → POST /dumps (kind=daily|today|monthly, rows=[...])
  → 服务端:
      1. 解析 rows 数组，提取结构化字段
      2. 对每一行 product:
         kind=daily:   INSERT INTO ad_daily (...) ON CONFLICT DO NOTHING
         kind=today:   INSERT INTO ad_today (...) ON CONFLICT DO UPDATE SET ...
         kind=monthly: INSERT INTO ad_monthly (...) ON CONFLICT DO NOTHING
      3. INSERT INTO ad_raw_log (kind, day/year_month, request/response)
      4. COMMIT
```

---

## 4. 总体数据流

```
[插件同步任务 1: 逐天同步]
  首次安装 → 用户选开始日 S
  → GET /coverage?kind=daily (方案 B，一次返回所有 campaign)
  → missing = [S..T-1] - coveredDays
  → 逐天: 抓 TikTok → POST /dumps (kind=daily)
  → 服务端: ad_daily + ad_raw_log（单事务）
  → 每天增量: 零点后拉昨天（不可变）+ 30s 刷新今天（ad_today）

[插件同步任务 2: 逐月同步]（独立于任务 1）
  → GET /coverage?kind=monthly (方案 B)
  → missing = [S月..T-1月] - coveredMonths
  → 逐月: 抓 TikTok → POST /dumps (kind=monthly)
  → 服务端: ad_monthly + ad_raw_log（单事务）

[重装恢复]
  任务 1: GET /coverage?kind=daily → 只补缺失天
  任务 2: GET /coverage?kind=monthly → 只补缺失月
  互不影响

[跨天固化]
  店铺零点后（保护窗 60min）:
  → INSERT INTO ad_daily SELECT * FROM ad_today WHERE day = 昨天
  → DELETE FROM ad_today WHERE day = 昨天
  → 开始新一天的 30s 刷新
```

---

## 5. 服务端设计

### 5.1 新增 `GET /v2/analytics/sync/coverage`（方案 B 批量查询）

一个端点同时支持天级和月级 coverage 查询，一次返回所有 campaign 的覆盖数据。

#### 请求（kind=daily）

```
GET /v2/analytics/sync/coverage
  ?sellerId=xxx
  &advertiserId=xxx
  &endpoint=/oec_ads/.../post_product_list
  &kind=daily
  &startDay=2026-01-01
  &endDay=2026-10-08
```

#### 响应（kind=daily）

```json
{
  "code": 0,
  "requestId": "req-xxx",
  "data": {
    "kind": "daily",
    "endpoint": "/oec_ads/.../post_product_list",
    "startDay": "2026-01-01",
    "endDay": "2026-10-08",
    "totalRequested": 281,
    "campaigns": {
      "campaign-1": {
        "coveredPeriods": ["2026-01-01", "2026-01-02", ..., "2026-10-07"],
        "totalCovered": 280
      },
      "campaign-2": {
        "coveredPeriods": ["2026-03-15", "2026-03-16", ...],
        "totalCovered": 207
      }
    }
  }
}
```

#### 请求（kind=monthly）

```
GET /v2/analytics/sync/coverage
  ?sellerId=xxx
  &advertiserId=xxx
  &endpoint=/oec_ads/.../post_product_list
  &kind=monthly
  &startMonth=2026-01
  &endMonth=2026-09
```

#### 响应（kind=monthly）

```json
{
  "code": 0,
  "requestId": "req-xxx",
  "data": {
    "kind": "monthly",
    "endpoint": "/oec_ads/.../post_product_list",
    "startMonth": "2026-01",
    "endMonth": "2026-09",
    "totalRequested": 9,
    "campaigns": {
      "campaign-1": {
        "coveredPeriods": ["2026-01", "2026-02", ..., "2026-08"],
        "totalCovered": 8
      }
    }
  }
}
```

#### Repository SQL

```python
SQL_COVERAGE_DAILY = """
SELECT campaign_id, array_agg(DISTINCT day ORDER BY day) AS days
FROM plugin.ad_daily
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint
  AND day BETWEEN :start_day AND :end_day
GROUP BY campaign_id
"""

SQL_COVERAGE_MONTHLY = """
SELECT campaign_id, array_agg(DISTINCT year_month ORDER BY year_month) AS months
FROM plugin.ad_monthly
WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
  AND endpoint = :endpoint
  AND year_month BETWEEN :start_month AND :end_month
GROUP BY campaign_id
"""
```

### 5.2 改造 `POST /v2/analytics/sync/dumps`（protocol v4）

#### 请求 body（kind=daily）

```jsonc
{
  "protocolVersion": 4,
  "requestId": "uuid",
  "scope": { "sellerId": "...", "advertiserId": "..." },
  "dump": {
    "kind": "daily",
    "endpoint": "/oec_ads/.../post_product_list",
    "day": "2026-09-08",
    "campaignId": "xxx",
    "rows": [
      {
        "product_id": "123",
        "mixed_real_cost": "150.50",
        "onsite_roi2_shopping_sku": 10,
        "onsite_roi2_shopping_value": "2000.00",
        "onsite_mixed_real_roi2_shopping": "13.29",
        "session_info": { ... },
        "spu_bi_appeal_info": { ... }
      }
    ],
    "request": { "url": "...", "body": { ... } },
    "response": { "status": 200, "body": { ... } },
    "createdAt": "2026-09-09T02:15:00.000Z"
  }
}
```

#### 请求 body（kind=today）

和 daily 结构一致，`kind: "today"`，`day` = 店铺今天。

#### 请求 body（kind=monthly）

```jsonc
{
  "protocolVersion": 4,
  "requestId": "uuid",
  "scope": { "sellerId": "...", "advertiserId": "..." },
  "dump": {
    "kind": "monthly",
    "endpoint": "/oec_ads/.../post_product_list",
    "yearMonth": "2026-08",
    "campaignId": "xxx",
    "rows": [
      {
        "product_id": "123",
        "mixed_real_cost": "4500.00",
        "onsite_roi2_shopping_sku": 300,
        "onsite_roi2_shopping_value": "60000.00",
        "onsite_mixed_real_roi2_shopping": "13.33"
      }
    ],
    "request": { "url": "...", "body": { ... } },
    "response": { "status": 200, "body": { ... } },
    "createdAt": "2026-09-01T02:00:00.000Z"
  }
}
```

#### 响应

```json
{
  "code": 0,
  "requestId": "req-xxx",
  "data": {
    "kind": "daily",
    "day": "2026-09-08",
    "rowCount": 15,
    "inserted": 15,
    "duplicates": 0
  }
}
```

### 5.3 Repository 层改造

#### 新增写入函数

```python
def upsert_daily_rows(sess, *, seller_id, advertiser_id, endpoint, campaign_id,
                      day, rows, request_url, request_body, response_status,
                      response_body, created_at, request_id, source):
    """解析 rows → INSERT ad_daily + ad_raw_log，单事务。"""
    inserted = 0
    for row in rows:
        # 提取核心指标
        product_id = row["product_id"]
        mixed_real_cost = row.get("mixed_real_cost")
        onsite_roi2_shopping_sku = row.get("onsite_roi2_shopping_sku")
        onsite_roi2_shopping_value = row.get("onsite_roi2_shopping_value")
        onsite_mixed_real_roi2_shopping = row.get("onsite_mixed_real_roi2_shopping")

        # 非核心字段放 metrics_extra
        core_keys = {"product_id", "mixed_real_cost", "onsite_roi2_shopping_sku",
                     "onsite_roi2_shopping_value", "onsite_mixed_real_roi2_shopping"}
        metrics_extra = {k: v for k, v in row.items() if k not in core_keys}

        # INSERT ad_daily ON CONFLICT DO NOTHING
        result = sess.execute(text(SQL_UPSERT_DAILY_ROW), {
            "seller_id": seller_id, "advertiser_id": advertiser_id,
            "campaign_id": campaign_id, "product_id": product_id,
            "endpoint": endpoint, "day": day,
            "mixed_real_cost": mixed_real_cost,
            "onsite_roi2_shopping_sku": onsite_roi2_shopping_sku,
            "onsite_roi2_shopping_value": onsite_roi2_shopping_value,
            "onsite_mixed_real_roi2_shopping": onsite_mixed_real_roi2_shopping,
            "metrics_extra": json.dumps(metrics_extra, ensure_ascii=False),
            "created_at": created_at,
        })
        if result.rowcount > 0:
            inserted += 1

    # INSERT ad_raw_log
    sess.execute(text(SQL_INSERT_RAW_LOG), {
        "seller_id": seller_id, "advertiser_id": advertiser_id,
        "endpoint": endpoint, "campaign_id": campaign_id,
        "kind": "daily", "day": day, "year_month": None,
        "request_url": request_url, "request_method": "POST",
        "request_body": json.dumps(request_body, ensure_ascii=False),
        "response_status": response_status,
        "response_body": json.dumps(response_body, ensure_ascii=False),
        "created_at": created_at, "request_id": request_id, "source": source,
    })

    sess.commit()
    return inserted
```

`upsert_today_rows` 和 `upsert_monthly_rows` 结构类似。

#### 新增 coverage 函数

```python
def get_coverage_daily(sess, *, seller_id, advertiser_id, endpoint,
                       start_day, end_day) -> dict[str, list[str]]:
    """返回 {campaign_id: [day1, day2, ...]} 的映射。"""
    rows = sess.execute(text(SQL_COVERAGE_DAILY), {
        "seller_id": seller_id, "advertiser_id": advertiser_id,
        "endpoint": endpoint, "start_day": start_day, "end_day": end_day,
    }).all()
    return {row[0]: [d.isoformat() for d in row[1]] for row in rows}
```

### 5.4 `spu_roi.py` 改造

当前读 `ad_product_links` VIEW 的三处改为直接读 `ad_daily` + `ad_today`：

```python
# _SQL_ROI_AD 改造前:
#   FROM analytics.ad_product_links
# 改造后:
_SQL_ROI_AD = text("""
    SELECT spu_pk,
           count(DISTINCT campaign_id)::int          AS ad_count,
           coalesce(sum(mixed_real_cost), 0)         AS spend,
           coalesce(sum(onsite_roi2_shopping_value), 0) AS gmv_ad,
           coalesce(sum(onsite_roi2_shopping_sku), 0)::bigint AS ad_orders,
           min(day)                                  AS ad_first_day,
           max(day)                                  AS ad_last_day
    FROM (
        SELECT d.campaign_id, d.product_id, d.day,
               d.mixed_real_cost, d.onsite_roi2_shopping_sku,
               d.onsite_roi2_shopping_value,
               cp.id AS spu_pk
        FROM plugin.ad_daily d
        LEFT JOIN commerce.shops ca ON ca.platform = 'tiktok' AND ca.shop_id = d.seller_id
        LEFT JOIN commerce.products_spu cp ON cp.shop_pk = ca.id AND cp.spu_id = d.product_id
        WHERE d.endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'
        UNION ALL
        SELECT t.campaign_id, t.product_id, t.day,
               t.mixed_real_cost, t.onsite_roi2_shopping_sku,
               t.onsite_roi2_shopping_value,
               cp.id AS spu_pk
        FROM plugin.ad_today t
        LEFT JOIN commerce.shops ca ON ca.platform = 'tiktok' AND ca.shop_id = t.seller_id
        LEFT JOIN commerce.products_spu cp ON cp.shop_pk = ca.id AND cp.spu_id = t.product_id
        WHERE t.endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'
    ) combined
    WHERE spu_pk IS NOT NULL
    GROUP BY spu_pk
""")
```

`_SQL_ROI_WINDOW` 和 `_SQL_ROI_DRILLDOWN_ADS` 同理改造。

### 5.5 `ad_product_links` VIEW 改造

```sql
CREATE VIEW analytics.ad_product_links AS
WITH all_rows AS (
    SELECT seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
           mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value
    FROM plugin.ad_daily
    WHERE endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'
    UNION ALL
    SELECT seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
           mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value
    FROM plugin.ad_today
    WHERE endpoint = '/oec_ads/shopping/v1/oec/stat/post_product_list'
)
SELECT
    ar.seller_id,
    ar.advertiser_id,
    ar.campaign_id,
    ar.product_id,
    ar.day,
    ar.mixed_real_cost   AS real_cost,
    ar.onsite_roi2_shopping_sku AS order_sku,
    ar.onsite_roi2_shopping_value AS order_value,
    ca.id AS shop_pk,
    cp.id AS spu_pk,
    1 AS observed_days,
    ar.day AS first_day,
    ar.day AS last_day
FROM all_rows ar
LEFT JOIN commerce.shops ca ON ca.platform = 'tiktok' AND ca.shop_id = ar.seller_id
LEFT JOIN commerce.products_spu cp ON cp.shop_pk = ca.id AND cp.spu_id = ar.product_id;
```

从 70 行 5 层 CTE 简化为 ~25 行简单 UNION ALL。

### 5.6 跨天固化逻辑

```python
def solidify_yesterday(sess, *, seller_id, advertiser_id, yesterday):
    """ad_today 昨天数据 → ad_daily（固化），然后清空 ad_today。"""
    sess.execute(text("""
        INSERT INTO plugin.ad_daily (
            seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
            mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
            onsite_mixed_real_roi2_shopping, metrics_extra, created_at
        )
        SELECT seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
               mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
               onsite_mixed_real_roi2_shopping, metrics_extra, created_at
        FROM plugin.ad_today
        WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
          AND day = :yesterday
        ON CONFLICT ON CONSTRAINT uq_ad_daily DO NOTHING
    """), {"seller_id": seller_id, "advertiser_id": advertiser_id, "yesterday": yesterday})

    sess.execute(text("""
        DELETE FROM plugin.ad_today
        WHERE seller_id = :seller_id AND advertiser_id = :advertiser_id
          AND day = :yesterday
    """), {"seller_id": seller_id, "advertiser_id": advertiser_id, "yesterday": yesterday})

    sess.commit()
```

---

## 6. 插件端设计

### 6.1 两个独立同步任务

```ts
const dailySyncScheduler = createSyncScheduler({
  ensureAlarm: () => ensureAlarm('daily-sync:heartbeat'),
  readDue: () => readDailySyncDue(),
  runUnit: () => executeDailySyncOnce(),
  reportError: reportSchedulerError,
});

const monthlySyncScheduler = createSyncScheduler({
  ensureAlarm: () => ensureAlarm('monthly-sync:heartbeat'),
  readDue: () => readMonthlySyncDue(),
  runUnit: () => executeMonthlySyncOnce(),
  reportError: reportSchedulerError,
});
```

### 6.2 监控区数据模型

```ts
interface SyncProgress {
  campaignsDiscovered: string[];
  discoveryStatus: 'pending' | 'done' | 'error';

  dailyProgress: {
    [unitKey: string]: {
      campaignId: string;
      endpoint: string;
      totalDays: number;
      serverExisting: number;
      syncedThisSession: number;
      pending: number;
      status: 'syncing' | 'done' | 'error';
    }
  };

  monthlyProgress: {
    [unitKey: string]: {
      campaignId: string;
      endpoint: string;
      totalMonths: number;
      serverExisting: number;
      syncedThisSession: number;
      pending: number;
      status: 'syncing' | 'done' | 'error';
    }
  };

  todayStatus: {
    day: string;
    lastRefreshAt: string | null;
    status: 'refreshing' | 'idle' | 'error';
  };
}
```

### 6.3 Rate Limiting 策略

| 层级 | 场景 | 速度 | 批次 | 预计耗时 |
| --- | --- | --- | --- | --- |
| 层级 1 | 首次历史同步 | 1 QPS | 每次心跳 30 unit（1 天） | ~2.2 小时 |
| 层级 2 | 每日增量 | 2 QPS | ~30 次 | ~15 秒 |
| 层级 3 | 今天实时刷新 | 每 30s | ~30 次 | 瞬间 |
| 层级 4 | 月级增量 | 1 QPS | ~30 次 | ~30 秒 |

错误处理：

- TikTok 429 → 指数退避 5s → 10s → 20s → 40s → 最大 5min
- TikTok 11000 → 停止同步，通知用户重新登录
- 服务端 5xx → 重试 3 次（间隔 2s），失败记入 deferredDumps
- MV3 worker 被杀 → 进度在 storage，重启后从检查点继续

---

## 7. 迁移与兼容

### 7.1 上线顺序

1. 服务端：migration 创建 `ad_today` / `ad_daily` / `ad_monthly` / `ad_raw_log`
2. 服务端：新增 `GET /coverage` 端点 + 改造 `POST /dumps` 端点
3. 服务端：`spu_roi.py` 改读新表
4. 服务端：`ad_product_links` VIEW 改读新表
5. 插件：改为逐天/逐月独立同步 + coverage 驱动

### 7.2 数据迁移

- 现有 `ad_raw` 表保留不动（历史数据）
- 新表从零开始填充（插件首次同步时写入）
- 过渡期 `spu_roi.py` 可 UNION 新旧表（视需要）

---

## 8. 测试计划

### 8.1 服务端（pytest）

| 测试 | 说明 |
| --- | --- |
| coverage daily 批量 | 一次请求返回所有 campaign 的 coveredDays |
| coverage monthly 批量 | 一次请求返回所有 campaign 的 coveredMonths |
| dumps daily 写入 | 解析 rows → ad_daily + ad_raw_log |
| dumps today 写入 | 解析 rows → ad_today（覆盖） + ad_raw_log |
| dumps monthly 写入 | 解析 rows → ad_monthly + ad_raw_log |
| dumps 重复写入 | daily/monthly ON CONFLICT DO NOTHING |
| 跨天固化 | ad_today → ad_daily → 清空 ad_today |
| spu_roi 读新表 | ROI 计算数值正确 |
| ad_product_links VIEW | 读新表，JOIN shops/products_spu |

### 8.2 联调 checklist

- [ ] 服务端：四张表 migration 成功
- [ ] 服务端：coverage 端点部署
- [ ] 服务端：dumps 端点改造部署
- [ ] 服务端：spu_roi 切换新表
- [ ] 服务端：ad_product_links VIEW 改造
- [ ] 插件：新版本发布
- [ ] 首次同步：daily + monthly 各自独立拉取 → 成功
- [ ] 重装恢复：coverage 返回已有 → 只补缺失 → 数据无波动
- [ ] ROI 查询：spu_roi 读新表 → 数值正确

---

## 9. 实施任务清单

| # | 任务 | 仓库 | 依赖 |
| --- | --- | --- | --- |
| T1 | migration: 创建 ad_today / ad_daily / ad_monthly / ad_raw_log | tts-erp | - |
| T2 | repository: get_coverage_daily/monthly + upsert_daily/today/monthly_rows | tts-erp | T1 |
| T3 | API: GET /coverage 端点（方案 B 批量） | tts-erp | T2 |
| T4 | API: POST /dumps 改造（protocol v4，支持新表写入） | tts-erp | T2 |
| T5 | repository: solidify_yesterday 跨天固化 | tts-erp | T1 |
| T6 | spu_roi.py: 改读 ad_daily + ad_today | tts-erp | T1 |
| T7 | VIEW: ad_product_links 改读新表 | tts-erp | T1 |
| T8 | 测试: coverage + dumps + solidify + spu_roi + VIEW | tts-erp | T3-T7 |
| T9 | 插件: fetchCoverage() + dumps 新协议 | ads-data-sync | T3, T4 |
| T10 | 插件: 逐天同步任务重构 | ads-data-sync | T9 |
| T11 | 插件: 逐月同步任务（新增） | ads-data-sync | T9 |
| T12 | 插件: 监控区 UI | ads-data-sync | T10, T11 |
| T13 | 插件: 测试 | ads-data-sync | T10-T12 |
| T14 | 联调 + 冒烟 | 两者 | T8, T13 |
