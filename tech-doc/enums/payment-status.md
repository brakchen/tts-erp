# `plugin.settlements.payment_status` — 结算单支付状态

> 卖家中心结算单的支付状态（头表层），与 `settlement_status`（行项目层）不同维度。

## 来源
- DB column: `plugin.settlements.payment_status` (Text, nullable)
- 持久化: `integration.raw_records.payload[].payment_status`（API 原始 JSON）
- 类型: **int**（卖家中心编码）
- 上游: TikTok 卖家中心 `/api/v1/pay/statement/order/list`（Chrome ext 抓取）

## 取值（🟡 实测）

| 等级 |  int 码 | 含义 | 实测 |
| :---: | ---: | --- | ---: |
| 🟡 |  `1` | 已付款 | ✓（`586051256575493984` 等） |
| 🔴 |  其他 | 待补 | — |

## ⚠️ 未固化值速查

- 🟡 **1 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``1`` — 已付款

- 🔴 **1 个值未观测** —— 枚举可能存在但本项目无样本，禁止拍脑袋假设。
  - 🔴 `其他` — 待补


## 已知 gap

- ❌ **完整合法值未文档化**（如 `0`=未付、`2`=挂起、`3`=失败等）
- ❌ 与 `payment_pending_reason` 字段的边界未理清

## 引用
- 代码: `tts_erp_v2/db/models/plugin.py:420`
- 文档: 无
