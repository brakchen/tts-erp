# `plugin.shipments.status` / `plugin.tracking_events.description` — Chrome ext 物流状态（自由文本）

> Chrome ext 从卖家中心抓到的物流状态**自由文本**。
> 与 API 端 `action_code` 整数码**不是同一套**——`action_code` 在 Chrome ext response 里**不一定存在**。

## 来源
- DB column A: `plugin.shipments.status` (Text) —— `logistic_detail/list` 响应 `package_list[].logistic_detail.track_list[-1].track_status`
- DB column B: `plugin.tracking_events.description` (Text) —— `logistic_detail/list` 响应 `track_list[].track_status`（每个事件一行）
- 类型: **text**（自由）
- 上游: TikTok 卖家中心 `/api/v1/fulfillment/logistic_detail/list`
- 文档锚点: `tech-doc/chrome-ext-order-sync-design.md §3 / §5`、`tech-doc/order-domain-business-rules.md §5`

## 取值

| 等级 | 值 | 含义（推断） | 来源 |
| :---: | --- | --- | --- |
| 🟡 | `Package picked up` | 包裹已揽收 | `tech-doc/chrome-ext-order-sync-design.md §6.2` 示例 |
| 🟡 | `Delivered` | 已签收 | `tech-doc/chrome-ext-order-sync-design.md §6.2` 示例 |
| 🔴 | （其他 - 自由文本） | 任何卖家中心实际显示的文案 | 见已知 gap #1 |

## ⚠️ 未固化值速查

- 🟡 **2 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``Package picked up`` — 包裹已揽收
  - 🟡 ``Delivered`` — 已签收

- 🔴 **1 个值未观测** —— 枚举可能存在但本项目无样本，禁止拍脑袋假设。
  - 🔴 `（其他 - 自由文本）` — 任何卖家中心实际显示的文案


## 已知 gap

- ❌ **完全自由文本** —— 任何下游代码不能依赖特定字符串
- ❌ **Chrome ext 抓的 `logistic_detail/list` 100% 返回 `response.body=null`**（见 `tech-doc/plugin-sourced-shop-analytics.md §8`）
  - 因此 `plugin.shipments` / `plugin.tracking_events` 表目前**零行**——所有"卖家中心物流状态"目前**拿不到**
- ❌ `tech-doc/order-domain-business-rules.md §5` 提到：卖家中心 response **不一定包含 action_code 数字编码**（可能只有 `track_status` 自由文本）
- ❌ **没有 `track_status → 终态` 的文本白名单** —— 物流终态判定在 plugin 路径下目前是**空操作**

## 引用
- 代码: `tts_erp_v2/plugin/orders/parser.py:263,294,301`、`tts_erp_v2/db/models/plugin.py:225,270`
- 文档: `tech-doc/chrome-ext-order-sync-design.md §3, §5, §6.2`、`tech-doc/order-domain-business-rules.md §5`、`tech-doc/plugin-sourced-shop-analytics.md §8`
