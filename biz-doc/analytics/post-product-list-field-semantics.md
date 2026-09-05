# post_product_list — 字段业务语义

> endpoint: `/oec_ads/shopping/v1/oec/stat/post_product_list`
> server storage_key: `productAnalyses`
> raw record 来源:Chrome 扩展(tiktok-shop-data-sync)抓 TikTok OEC Seller Center

## 1. endpoint 角色

每个 campaign × day 的 **"商品分析"** 视图 — 列出该广告计划当天挂的每个 SPU 的:

- 基础信息(spu_id / name / picture / status / bid 策略)
- 业绩指标(出单数 / 出单金额 / 消耗 / ROI / 单价)

`table` 数组每个 element 代表**一个 SPU(对应一个 product_id)** — 不是 campaign 整体。

## 2. Request body 关键字段

修复后的标准入参(从 `id=2657` dump 提取):

```json
{
    "page": 1,
    "end_time": "2026-09-04",
    "page_size": 10,
    "order_type": 1,
    "query_list": [ /* 17 个字段名,见下表 */ ],
    "start_time": "2026-09-04",
    "campaign_id": "1871317807708722",
    "order_field": "onsite_roi2_shopping_value"
}
```

### query_list 17 字段(告诉 TikTok API 要返回哪些列)

```text
product_name                                ← 商品名称
campaign_id                                 ← 广告计划 ID
product_id                                  ← SPU ID(主键,数值字符串)
product_status                              ← 上架状态
product_picture                             ← 商品主图 URL
session_info                                ← session 关联信息(json)
campaign_no_bid_budget                      ← 广告计划无竞价预算
gmv_max_bid_type                            ← GMV max bid 类型(1 / 其他)
campaign_target_roi_budget_mode             ← 目标 ROI 预算模式
campaign_target_roi_budget                  ← 目标 ROI 预算
active_creative_boost_count                 ← 活跃创意 boost 数量
mixed_real_cost                             ← 真实消耗
onsite_roi2_shopping_sku                    ← 站内 ROI2 shopping SKU(出单数)
mixed_real_cost_per_onsite_roi2_shopping_sku ← 真实消耗 / 出单数(单价)
onsite_roi2_shopping_value                  ← 站内 ROI2 shopping 价值(出单金额)
onsite_mixed_real_roi2_shopping             ← 真实 ROI(消耗 / 价值)
spu_bi_appeal_info                          ← SPU 商业吸引力信息(json)
```

## 3. Response.body.data.table 字段业务语义

每个 row = 一个 SPU(product_id 唯一标识)。

### 3.1 业绩核心字段

| 字段 | 类型 | 业务含义 | 单位 | 来源 |
| --- | --- | --- | --- | --- |
| **`onsite_roi2_shopping_sku`** | string(int) | **这个 campaign_id 这天卖出了多少单**(该 SPU 当天带来的站内 ROI2 shopping 订单数) | 件 | user spec ⚠️ |
| **`mixed_real_cost`** | string(decimal) | **这个 SPU(product_id)的消耗**(广告真实消耗金额) | 货币(广告账户币种,通常 USD) | user spec ⚠️ |
| `onsite_roi2_shopping_value` | string(decimal) | 该 SPU 当天带来的站内 ROI2 shopping GMV(出单金额) | 货币 | 推断 ⚠️ |
| `mixed_real_cost_per_onsite_roi2_shopping_sku` | string(decimal) | 单订单真实消耗(消耗 ÷ 出单数) | 货币/件 | 推断 ⚠️ |
| `onsite_mixed_real_roi2_shopping` | string(decimal) | 真实 ROI 比(消耗 ÷ 价值) | 比率 | 推断 ⚠️ |
| `active_creative_boost_count` | string(int) | 该 SPU 的活跃创意 boost 数量 | 件 | 推断 ⚠️ |

**关键澄清**:

- `onsite_roi2_shopping_sku` 是**每个 row(每个 SPU)独立计数**,
  不是 campaign 整体聚合。要看 campaign 整体出单数,需对 table 数组 SUM 该字段。
- `mixed_real_cost` 是**每个 SPU 自己的消耗**,看 campaign 总消耗需要 SUM。

### 3.2 基础信息字段

| 字段 | 类型 | 业务含义 |
| --- | --- | --- |
| `product_id` | string | SPU 数字 ID(主键) |
| `spu_id` (仅 table_v2) | string | 同 product_id(冗余字段,TikTok OEC 内部命名差异) |
| `product_name` | string | 商品名称(可能含 unicode / 多语言) |
| `product_status` | string | 上架状态:`available` / `unavailable` / 其他 |
| `product_picture` | string | 商品主图 URL(TikTok CDN) |
| `gmv_max_bid_type` | string | GMV max 竞价类型:`1` / 其他 |

### 3.3 table_v2 列描述符

`table_v2[0][i]` = `{name, data}`,列出该 row 包含的列。和 `table` 同一份数据,只是结构化列描述。

## 4. 度量计算公式

```text
混合 ROI = mixed_real_cost / onsite_roi2_shopping_value
           (越小越好,广告成本 / 带来的 GMV)

单订单成本 = mixed_real_cost / onsite_roi2_shopping_sku
            (mixed_real_cost_per_onsite_roi2_shopping_sku 字段就是它)

campaign 总消耗 = SUM(table[*].mixed_real_cost)
campaign 总出单 = SUM(table[*].onsite_roi2_shopping_sku)
campaign 总 GMV  = SUM(table[*].onsite_roi2_shopping_value)
```

## 5. 样本(修复后 `id=2657`)

```json
{
    "product_id": "1736527322824279287",
    "product_name": "Áo thun nam tay ngắn bằng vải lụa băng thoáng khí, ...",
    "product_status": "available",
    "mixed_real_cost": "0.00",                              ← 这天这个 SPU 没消耗
    "onsite_roi2_shopping_sku": "0",                        ← 这天这个 SPU 0 单
    "onsite_roi2_shopping_value": "0.00",
    "onsite_mixed_real_roi2_shopping": "0.00",
    "mixed_real_cost_per_onsite_roi2_shopping_sku": "0.00",
    "gmv_max_bid_type": "1"
}
```

## 6. 已知坑

1. **schema 漂移风险**:`mixed_real_cost / onsite_roi2_shopping_*` 是字符串(不是 number),
   在做 numeric 聚合前需先 `regexp_matches` 检查格式。
2. **修复前历史数据无业绩字段**:ad_raw 在 2026-09-04 之前的 1249 行,
   `table` 元素**只有 `product_id` 一个 key**(Chrome 扩展入参问题导致),
   那批数据没有业绩指标,只能用于"知道当天挂了哪些 SPU"。
3. **修复时间点**:2026-09-04 ~01:28 CST 之后才出现完整 schema(id=2598+),
   那之前的 dump 都是精简版。
4. **campaign_id 是聚合维度**:`onsite_roi2_shopping_sku` 是 per-SPU,聚合到 campaign 需要 SUM。
   如果只看 table 第一个 element 会**低估**真实出单数。

## 7. 来源

- **修复后 schema 验证**: ad_raw id=2657 (2026-09-04 09:43:54 CST)
- **修复前 schema 残留**: ad_raw id=52-1835 (~2026-08-26 - 2026-09-03)
- **Chrome 扩展源码**: `/home/schan/chrome-plugins/ads-data-sync/entrypoints/background.ts:518`
  - `executeTikTokRequestInBoundPage` 在 Seller Center tab MAIN world 调 fetch
  - `createCollectionRequestBody` 构造 body
- **tts-erp server schema**:
  - `tts_erp_v2/api/v2/analytics.py:107-130` `DumpBodyIn` 接受任意 dict 不做字段过滤
  - `tts_erp_v2/analytics/repository.py:142-237` `upsert_dump` 原样存
