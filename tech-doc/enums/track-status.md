# `plugin.shipments.status` / `plugin.tracking_events.description` — Chrome ext 物流状态（自由文本）

> Chrome ext 从卖家中心抓到的物流状态**自由文本**。
> 与 API 端 `action_code` 整数码**不是同一套**——`action_code` 在 Chrome ext response 里**不一定存在**。

## 来源
- DB column A: `plugin.shipments.status` (Text) —— `logistic_detail/list` 响应 `package_list[].logistic_detail.track_list[-1].track_status`
- DB column B: `plugin.tracking_events.description` (Text) —— `logistic_detail/list` 响应 `track_list[].track_status`（每个事件一行）
- 类型: **text**（自由）
- 上游: TikTok 卖家中心 `/api/v1/fulfillment/logistic_detail/list`
- 文档锚点: `tech-doc/chrome-ext-order-sync-design.md §3 / §5`、`tech-doc/order-domain-business-rules.md §5`

## 实测样本

> `tech-doc/chrome-ext-order-sync-design.md §6.2` 给出的样本：

```json
[
  { "time": "2026-09-08T10:00:00Z", "track_status": "Package picked up" },
  { "time": "2026-09-10T14:00:00Z", "track_status": "Delivered" }
]
```

## 已知 gap

- ❌ **完全自由文本** —— 任何下游代码不能依赖特定字符串
- ❌ **Chrome ext 抓的 `logistic_detail/list` 100% 返回 `response.body=null`**（见 `tech-doc/plugin-sourced-shop-analytics.md §8`）
  - 因此 `plugin.shipments` / `plugin.tracking_events` 表目前**零行**——所有"卖家中心物流状态"目前**拿不到**
- ❌ `tech-doc/order-domain-business-rules.md §5` 提到：卖家中心 response **不一定包含 action_code 数字编码**（可能只有 `track_status` 自由文本）
- ❌ **没有 `track_status → 终态` 的文本白名单** —— 物流终态判定在 plugin 路径下目前是**空操作**

## 引用
- 代码: `tts_erp_v2/plugin/orders/parser.py:263,294,301`、`tts_erp_v2/db/models/plugin.py:225,270`
- 文档: `tech-doc/chrome-ext-order-sync-design.md §3, §5, §6.2`、`tech-doc/order-domain-business-rules.md §5`、`tech-doc/plugin-sourced-shop-analytics.md §8`
