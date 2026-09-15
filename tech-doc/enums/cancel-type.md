# `plugin.after_sales.cancel_type` — 取消类型（TikTok 原始）

> TikTok `cancellations/search` 端点原始的取消类型枚举（**2 类**）。
> 会被 v2 标准化为 [`case-type.md`](case-type.md) 的 `CANCELLATION` 一类。

## 来源
- DB column: `plugin.after_sales.cancel_type` (Text, NOT NULL)
- 持久化: `integration.raw_records.payload[].cancel_type`（API 原始 JSON）
- 类型: **text**（TikTok 内部枚举）
- 上游: TikTok OpenAPI `/return_refund/202309/cancellations/search`
- 文档锚点: `tech-doc/order-domain-business-rules.md §3`

## 取值（✅ 固化）

| 值 | 含义 | 实测样本 |
| --- | --- | ---: |
| `BUYER_CANCEL` | 买家主动取消（"不想要了"、"发现更优惠的价格"等） | 678 (老数据，已 2026-08-29 归档) |
| `CANCEL` | 系统/卖家取消（`returned_to_shipper_other`、超时未付款等） | 765 |

> 文档 `tech-doc/order-domain-business-rules.md §3` 标注："**全部为 `CANCELLATION_REQUEST_COMPLETE`**"。

## v2 标准化映射

| 原始 cancel_type | v2 case_type | 备注 |
| --- | --- | --- |
| `BUYER_CANCEL` | `CANCELLATION` | 包含 `cancel_reason="不想要了"`、`"发现更优惠的价格"` 等 |
| `CANCEL` | `CANCELLATION` | 包含 `cancel_reason="returned_to_shipper_other"`、`"客户超时支付"` 等 |

> v2 把两类合并为 `CANCELLATION` 一类——细分由 `cancel_reason` 字段保留。

## 引用
- 代码: `tts_erp_v2/db/models/plugin.py:340-360`、`tts_erp_v2/jobs/tiktok/cancellations.py`（待建）
- 文档: `tech-doc/order-domain-business-rules.md §3`
