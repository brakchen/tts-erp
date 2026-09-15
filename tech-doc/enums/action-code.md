# `tracking_events.action_code` — 物流事件码

> TikTok 物流事件**类型枚举**（**不是唯一 id**，同 code 在同一订单可重复）。
> 海外取消判据的核心字段：`**38301**`（已到目的国）。

## 来源
- DB column: `fulfillment.tracking_events.action_code` (Integer, nullable)
- 持久化: `integration.raw_records.payload[i].action_code`（API 原始 JSON）
- 类型: **int**（TikTok 内部枚举）
- 上游: TikTok OpenAPI `/fulfillment/202309/orders/{order_id}/tracking` 响应事件数组
- 文档锚点: `tech-doc/order-domain-business-rules.md §5`、`tech-doc/analytics/spu_roi.py:184`、`tech-doc/_archive/data-model-v1.md §3`

## 取值（🟡 实测 23 个，全部来自 VN Bridge nook 货流）

### 下单/打包
| code | 含义 | 首次出现 |
| ---: | --- | --- |
| `10101` | Order placed（用户下单） | T0 |
| `20101` | Packed by seller（卖家已打包） | 0~2h after 10101 |

### 始发国（CN）
| code | 含义 | 间隔（参考） |
| ---: | --- | --- |
| `30201` | Arrived at sorting center in CN | 1~2 day after 20101 |
| `30301` | In transit in CN | 同日 |
| `30401` | Handed over to next carrier in CN | 0.5 day |
| `30501` | Handed over to international carrier | 1~2 day after 30401 |
| `31701` | Export clearance completed | < 1 day |
| `38701` | Awaiting international departure | < 1 day |
| `34301` | Departed CN | 0.5 day after 31701 |

### 跨境
| code | 含义 | **海外取消判据** |
| ---: | --- | :---: |
| **`38301`** | **Arrived in Vietnam, port of entry** | **★ 必含** |

### 目的国境内
| code | 含义 |
| ---: | --- |
| `34701` | Import clearance completed（越南进口清关） |
| `30801` | Handed over to local carrier |
| `31201` | In transit in <address> |
| `31301` | Now in <sub-district> |
| `31401` | Now in <from> and will be transferred to <to> |
| `32401` | In transit（在途，多次出现） |
| `32601` | Now in <address> |

### 末端派送
| code | 含义 |
| ---: | --- |
| `40101` | Now in <address>（末端站） |
| `40501` | Will be delivered soon, please pay attention to delivery information |

### 派送失败
| code | 含义 | 海外取消触发 |
| ---: | --- | :---: |
| **`40601`** | **Customer has rejected the package. Delivery will be rescheduled, so please check for updates.** | **★ 典型** |

### 退件
| code | 含义 |
| ---: | --- |
| `70201` | Returning（**退件途中，可多次重复**） |
| **`80101`** | **Returned to the seller by the shipping provider**（**物流终态**） |
| `110101` | Your package delivery was canceled（**物流终态**） |

### 签收
| code | 含义 |
| ---: | --- |
| `50101` | Your package was delivered!（**物流终态**） |

## 三个物流终态白名单

见 [`logistics-terminal-codes.md`](logistics-terminal-codes.md) —— `{50101, 80101, 110101}`。

## 业务关键判据

| 业务 | 必要 | 可选辅助 |
| --- | --- | --- |
| **海外取消桶** | `38301`（已到目的国） | + `CANCELLED` 订单状态（见 `tech-doc/analytics/spu_roi.py:184`） |
| **完结退货** | `tracking` 显示退件完成（`80101`/`110101`） | + `case_type=RETURN_AND_REFUND/REFUND_ONLY` |
| **正常签收** | `50101` | — |

## 已知 gap

- ❌ **plugin 端没单独存 action_code**——`plugin.tracking_events` 表只有 `description`，没有 `action_code` 列。`tech-doc/plugin-sourced-shop-analytics.md:67-68` 标注为 P0 TODO：
  > "`plugin.tracking_events` 未存 action_code 独立列（仅无 event_id 时拼进 `event_key`）。处理：`plugin.tracking_events` 加 `action_code` 列 + parser 补写，历史数据可从 `event_key` 部分回填。**加列前插件页面海外取消桶为空**。"
- ❌ **Chrome ext 抓的 `track_list[].action_code` 是否每次都拿到** —— `tech-doc/order-domain-business-rules.md §5` 提到卖家中心页面 response 不一定包含 action_code 数字编码（可能只有 `track_status` 自由文本）。需要实测确认。

## 引用
- 代码: `tts_erp_v2/jobs/tiktok/logistics.py:97-100,113`、`tts_erp_v2/db/models/fulfillment.py:121`
- 文档: `tech-doc/order-domain-business-rules.md §5`、`tech-doc/analytics/spu_roi.py:184`、`tech-doc/plugin-sourced-shop-analytics.md §4.3`
- 完整 43 事件样本（VN 拒收→退件→CANCELLED 案例）：见对话历史 `585900098675508729` 订单
