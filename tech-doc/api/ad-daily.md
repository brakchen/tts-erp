# 广告日明细浏览页

## 用途

`plugin.ad_daily` 是 Chrome 插件写入的逐日广告事实表，一行对应一个
`(seller_id, advertiser_id, endpoint, campaign_id, product_id, day)` 自然键。
广告日明细页用于核查插件实际采集结果，不替代 SPU ROI 看板，也不重算经营口径。

- 页面：`GET /v2/pages/ad-daily`
- 数据：`GET /v2/reporting/ad-daily`
- 筛选项：`GET /v2/reporting/ad-daily/options`
- 最低角色：`readonly`
- 导航入口：控制台的“广告日明细”快捷卡片

页面和 API 均只读，不触发上游请求，也不修改广告数据。

## 页面行为

页面支持按店铺（`seller_id`）、广告账户、采集接口、日期区间，以及计划/商品 ID
子串筛选。筛选条件同步到 URL，刷新和分享链接后可以恢复。表格固定按
`day DESC, updated_at DESC, id DESC` 排序，并使用 `limit` / `offset` 分页。

主表展示源表的一等字段：

- 日期、店铺、广告账户、计划 ID、商品 ID；
- `mixed_real_cost`（实际消耗）；
- `onsite_roi2_shopping_sku`（广告归因订单）；
- `onsite_roi2_shopping_value`（广告归因 GMV）；
- `onsite_mixed_real_roi2_shopping`（广告实际 ROI）；
- 最近更新时间。

`metrics_extra` 在每行“更多”面板中按 JSON 展示。若 seller/product 能映射到
`commerce.shops` / `commerce.products_spu`，API 会补充店铺名和商品标题；映射失败不影响
源行展示。

> 金额保持广告源币种 **USD**。该页面不应用 SPU ROI 看板的 CNY 汇率快照。

## `GET /v2/reporting/ad-daily`

Query parameters：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `seller_id` | string | — | 精确匹配插件 seller_id（TikTok shop_id） |
| `advertiser_id` | string | — | 精确匹配广告账户 |
| `endpoint` | string | — | 精确匹配采集接口 |
| `day_from` | date | — | 起始日，含当天 |
| `day_to` | date | — | 截止日，含当天；早于 `day_from` 返回 422 |
| `q` | string | — | campaign_id / product_id 字面子串，`%` 与 `_` 不作通配符 |
| `limit` | int | 50 | 1..500 |
| `offset` | int | 0 | ≥0 |

响应：

```json
{
  "items": [
    {
      "id": 123,
      "seller_id": "749…",
      "shop_pk": 31,
      "shop_name": "Shop A",
      "advertiser_id": "712…",
      "campaign_id": "183…",
      "product_id": "172…",
      "product_title": "Product A",
      "endpoint": "/oec_ads/…/post_product_list",
      "day": "2026-09-30",
      "mixed_real_cost": "12.3400",
      "onsite_roi2_shopping_sku": 4,
      "onsite_roi2_shopping_value": "50.0000",
      "onsite_mixed_real_roi2_shopping": "4.0519",
      "metrics_extra": {},
      "created_at": "2026-09-30T01:02:03+00:00",
      "updated_at": "2026-09-30T01:02:03+00:00"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0,
  "summary": {
    "row_count": 1,
    "spend": "12.3400",
    "attributed_orders": 4,
    "attributed_gmv": "50.0000",
    "weighted_roi": "4.0519",
    "currency": "USD"
  }
}
```

所有 numeric 字段序列化为字符串；缺失源指标返回 `null`。`summary` 对完整筛选集合聚合，
不受当前分页影响；`weighted_roi = Σ attributed_gmv / Σ spend`，总消耗为 0 时返回 `null`。

## `GET /v2/reporting/ad-daily/options`

返回：

- `sellers[]`：`seller_id`, 可选 `shop_name`, `row_count`；
- `advertisers[]`：`seller_id`, `advertiser_id`, `row_count`；
- `endpoints[]`：`endpoint`, `row_count`；
- `min_day` / `max_day`：当前表中观测日期边界。

这些值只用于浏览页筛选，不是店铺或广告账户的主数据契约。
