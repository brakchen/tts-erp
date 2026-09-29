# `plugin.settlement_details.settlement_status` — 结算状态

> SKU 级结算明细的结算状态（与 `plugin.settlements` 头表的 `payment_status` 不同维度）。

## 来源
- DB column: `plugin.settlement_details.settlement_status` (Text, nullable)
- 持久化: `integration.raw_records.payload[].settlement_status`（API 原始 JSON）
- 类型: **text / int**（API 端混合编码；项目实存 text）
- 上游: TikTok OpenAPI `/finance/202309/statements/{statement_id}/statement_transactions` 响应

## 取值

| 等级 | 值 | 含义 | 实测 |
| :---: | --- | --- | ---: |
| 🟡 | `2` | 已结算 | ✓（`586051256575493984` 等） |
| 🔴 | 其他 | （未观测） | — |

> `/api/v1/pay/statement/order/list`（卖家中心 Chrome ext）响应里 `settlement_status` 是 **int** 编码（值 `2`）。

## ⚠️ 未固化值速查

- 🟡 **1 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``2`` — 已结算

- 🔴 **1 个值未观测** —— 枚举可能存在但本项目无样本，禁止拍脑袋假设。
  - 🔴 `其他` — （未观测）


## 已知 gap

- ❌ **完整合法值未文档化** —— 只观察到 `2` = "已结算"
- ❌ **API 编码方式不一致**（int / text 混用），需要实测进一步确认

## 引用
- 代码: `tts_erp_v2/db/models/plugin.py:454`
- 文档: 无
