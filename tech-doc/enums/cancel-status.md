# `plugin.after_sales.cancel_status` — 取消单状态（TikTok 原始）

> TikTok `cancellations/search` 端点返回的取消单状态。
> 实测中**只观察到一种值**。

## 来源
- DB column: `plugin.after_sales.cancel_status` (Text, NOT NULL)
- 持久化: `integration.raw_records.payload[].cancel_status`
- 类型: **text**（TikTok 内部枚举）
- 上游: TikTok OpenAPI `/return_refund/202309/cancellations/search`
- 文档锚点: `tech-doc/order-domain-business-rules.md §3`

## 取值

| 等级 | 值 | 含义 | 实测 |
| :---: | --- | --- | ---: |
| 🟡 | `CANCELLATION_REQUEST_COMPLETE` | 取消请求已完成（终态） | ✓（2026-08-29 老数据：1443 单全部） |
| 🔴 | `CANCELLATION_REQUEST_PENDING` | 取消请求待处理（推断） | （未观测） |
| 🔴 | `CANCELLATION_REQUEST_REJECTED` | 取消请求被拒（推断） | （未观测） |

> `tech-doc/order-domain-business-rules.md §3` 原文："**全部为 `CANCELLATION_REQUEST_COMPLETE`**"。

## ⚠️ 未固化值速查

- 🟡 **1 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``CANCELLATION_REQUEST_COMPLETE`` — 取消请求已完成（终态）

- 🔴 **2 个值未观测** —— 枚举可能存在但本项目无样本，禁止拍脑袋假设。
  - 🔴 ``CANCELLATION_REQUEST_PENDING`` — 取消请求待处理（推断）
  - 🔴 ``CANCELLATION_REQUEST_REJECTED`` — 取消请求被拒（推断）


## 已知 gap

- 🔴 **其他状态值未观测到** —— 上游 API 完整枚举未在公开文档列出
- 🔴 文档未给完整枚举（也不容易从上游 API 文档直接拿到）

## 引用
- 代码: `tts_erp_v2/db/models/plugin.py:355-360`
- 文档: `tech-doc/order-domain-business-rules.md §3`
