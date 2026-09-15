# `fulfillment_type` — 履约类型（双口径）

> **同一个业务概念有两套编码**——API 同步的 text 版（commerce）和 Chrome ext 抓的 int 版（plugin）。
> 这两套**目前没有自动转换关系**，跨表 join 时要小心。

## 来源 A：commerce（✅ 固化）
- DB column: `commerce.sales_orders.fulfillment_type` (Text, nullable)
- 类型: **text**
- 上游: TikTok OpenAPI `/order/202309/orders/search` 响应 `fulfillment_type` 字段

### 取值
| 等级 |  值 | 含义 |
| :---: | --- | --- |
| ✅ |  `FULFILLMENT_BY_SELLER` | 卖家自发物流（FBM，自有仓/自发货） |
| 🔴 |  `FULFILLMENT_BY_TIKTOK` | TikTok 仓配 / 官方物流（FBT） |
| 🔴 |  （其他待补） | — |

> ⚠️ **注意**：仅观察到 `FULFILLMENT_BY_SELLER`。TikTok 仓配枚举值在 prod 数据中**暂未捕获样本**。

## ⚠️ 未固化值速查

- 🔴 **2 个值未观测** —— 枚举可能存在但本项目无样本，禁止拍脑袋假设。
  - 🔴 ``FULFILLMENT_BY_TIKTOK`` — TikTok 仓配 / 官方物流（FBT）
  - 🔴 `（其他待补）` — —


## 来源 B：plugin（🟡 实测推断）
- DB column: `plugin.orders.fulfillment_type` (Integer, nullable)
- 类型: **int**（卖家中心原始码）
- 上游: TikTok 卖家中心 `/api/fulfillment/order/list` 响应 `trade_order_module.fulfillment_type`

### 取值
| 等级 |  int 码 | 实测样本 | 推断含义 |
| :---: | ---: | ---: | --- |
| 🟡 |  `0` | 是 | 与 text 版 `FULFILLMENT_BY_SELLER` 配对（VN 端均为 0） |
| 🔴 |  其他 | 否 | 待发现 |

## ⚠️ 未固化值速查

- 🟡 **1 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``0`` — 是

- 🔴 **1 个值未观测** —— 枚举可能存在但本项目无样本，禁止拍脑袋假设。
  - 🔴 `其他` — 否


## 已知 gap

- ❌ **plugin int → commerce text 的映射未实现** —— 跨表 join 时必须先选一种口径，禁止直接 compare `plugin.fulfillment_type = commerce.fulfillment_type`
- ❌ plugin int 1/2/3 等其他值的语义未确认

## 引用
- 代码: `tts_erp_v2/db/models/commerce.py:194`、`tts_erp_v2/db/models/plugin.py:105`、`tts_erp_v2/plugin/orders/parser.py:106`
- 文档: `tech-doc/order-domain-business-rules.md §2`
