# `plugin.after_sales.cancel_status` — 取消单状态（TikTok 原始）

> TikTok `cancellations/search` 端点返回的取消单状态。
> 实测中**只观察到一种值**。

## 来源
- DB column: `plugin.after_sales.cancel_status` (Text, NOT NULL)
- 持久化: `integration.raw_records.payload[].cancel_status`
- 类型: **text**（TikTok 内部枚举）
- 上游: TikTok OpenAPI `/return_refund/202309/cancellations/search`
- 文档锚点: `tech-doc/order-domain-business-rules.md §3`

## 取值（🟡 仅观察到 1 种，可能未穷尽）

| 值 | 含义 | 实测 |
| --- | --- | ---: |
| `CANCELLATION_REQUEST_COMPLETE` | 取消请求已完成（终态） | ✓（2026-08-29 老数据：1443 单全部） |

> `tech-doc/order-domain-business-rules.md §3` 原文："**全部为 `CANCELLATION_REQUEST_COMPLETE`**"。

## 已知 gap

- 🔴 **其他状态值未观测到** —— 上游 API 可能有 `CANCELLATION_REQUEST_PENDING` / `CANCELLATION_REQUEST_REJECTED` 等中间态，本项目未触发任何样本
- 🔴 文档未给完整枚举（也不容易从上游 API 文档直接拿到）

## 引用
- 代码: `tts_erp_v2/db/models/plugin.py:355-360`
- 文档: `tech-doc/order-domain-business-rules.md §3`
