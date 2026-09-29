# `tracking_events.action_code` — 物流事件码

> TikTok 物流事件**类型枚举**（**不是唯一 id**，同 code 在同一订单可重复）。
> 海外取消判据的核心字段：`**38301**`（已到目的国）。

## 来源
- DB column: `fulfillment.tracking_events.action_code` (Integer, nullable)
- 持久化: `integration.raw_records.payload[i].action_code`（API 原始 JSON）
- 类型: **int**（TikTok 内部枚举）
- 上游: TikTok OpenAPI `/fulfillment/202309/orders/{order_id}/tracking` 响应事件数组
- 文档锚点: `tech-doc/order-domain-business-rules.md §5`、`tech-doc/analytics/spu_roi.py:184`、`tech-doc/_archive/data-model-v1.md §3`

## 取值

| 等级 | code | 区段 | 含义 | 备注 |
| :---: | ---: | --- | --- | --- |
| 🟡 | `10101` | 下单/打包 | Order placed（用户下单） | T0 |
| 🟡 | `20101` | 下单/打包 | Packed by seller（卖家已打包） | 0~2h after 10101 |
| 🟡 | `30201` | 始发国(CN) | Arrived at sorting center in CN | 1~2 day after 20101 |
| 🟡 | `30301` | 始发国(CN) | In transit in CN | 同日 |
| 🟡 | `30401` | 始发国(CN) | Handed over to next carrier in CN | 0.5 day |
| 🟡 | `30501` | 始发国(CN) | Handed over to international carrier | 1~2 day after 30401 |
| 🟡 | `31701` | 始发国(CN) | Export clearance completed | < 1 day |
| 🟡 | `38701` | 始发国(CN) | Awaiting international departure | < 1 day |
| 🟡 | `34301` | 始发国(CN) | Departed CN | 0.5 day after 31701 |
| 🟡 | **`38301`** | **跨境** | **Arrived in Vietnam, port of entry** | **★ 海外取消判据必含** |
| 🟡 | `34701` | 目的国境内 | Import clearance completed（越南进口清关） | — |
| 🟡 | `30801` | 目的国境内 | Handed over to local carrier | — |
| 🟡 | `31201` | 目的国境内 | In transit in `<address>` | — |
| 🟡 | `31301` | 目的国境内 | Now in `<sub-district>` | — |
| 🟡 | `31401` | 目的国境内 | Now in `<from>` and will be transferred to `<to>` | — |
| 🟡 | `32401` | 目的国境内 | In transit（在途，多次出现） | — |
| 🟡 | `32601` | 目的国境内 | Now in `<address>` | — |
| 🟡 | `40101` | 末端派送 | Now in `<address>`（末端站） | — |
| 🟡 | `40501` | 末端派送 | Will be delivered soon, please pay attention to delivery information | — |
| 🟡 | **`40601`** | **派送失败** | **Customer has rejected the package. Delivery will be rescheduled, so please check for updates.** | **★ 海外取消触发典型** |
| 🟡 | `70201` | 退件 | Returning（**退件途中，可多次重复**） | — |
| 🟡 | `50101` | 签收 | Your package was delivered!（**物流终态**） | — |
| 🟡 | **`80101`** | **退件** | **Returned to the seller by the shipping provider**（**物流终态**） | — |
| 🟡 | `110101` | 退件 | Your package delivery was canceled（**物流终态**） | — |

## ⚠️ 未固化值速查

- 🟡 **24 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``10101`` — 下单/打包
  - 🟡 ``20101`` — 下单/打包
  - 🟡 ``30201`` — 始发国(CN)
  - 🟡 ``30301`` — 始发国(CN)
  - 🟡 ``30401`` — 始发国(CN)
  - …（其余 19 个见下方"## 取值"表）


## 三个物流终态白名单

见 [`logistics-terminal-codes.md`](logistics-terminal-codes.md) —— `{50101, 80101, 110101}`。

## 业务关键判据

| 业务 | 必要 | 可选辅助 |
| --- | --- | --- |
| **海外取消桶** | `38301`（已到目的国） | + `CANCELLED` 订单状态（见 `tech-doc/analytics/spu_roi.py:184`） |
| **完结退货** | `tracking` 显示退件完成（`80101`/`110101`） | + `case_type=RETURN_AND_REFUND/REFUND_ONLY` |
| **正常签收** | `50101` | — |

## 已知 gap

- ✅ **plugin 端已单独存 action_code**——migration `0034_plugin_tracking_action_code` 增加列，parser 写入新抓取轨迹；迁移前历史行的 NULL 按未知状态处理。
- ❌ **Chrome ext 抓的 `track_list[].action_code` 是否每次都拿到** —— `tech-doc/order-domain-business-rules.md §5` 提到卖家中心页面 response 不一定包含 action_code 数字编码（可能只有 `track_status` 自由文本）。需要实测确认。
- 🔴 **完整 action_code 总集未知** —— 本表 24 个码来自 VN Bridge nook 单店样本，TikTok 其他区域可能有未观测码

## 引用
- 代码: `tts_erp_v2/jobs/tiktok/logistics.py:97-100,113`、`tts_erp_v2/db/models/fulfillment.py:121`
- 文档: `tech-doc/order-domain-business-rules.md §5`、`tech-doc/analytics/spu_roi.py:184`、`tech-doc/plugin-sourced-shop-analytics.md §4.3`
- 完整 43 事件样本（VN 拒收→退件→CANCELLED 案例）：见对话历史 `585900098675508729` 订单
