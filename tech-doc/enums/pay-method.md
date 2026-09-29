# `plugin.orders.pay_method` — 支付方式（自由文本）

> Chrome ext 从卖家中心抓到的支付方式原文。**不是枚举**——是 TikTok 用自然语言描述。

## 来源
- DB column: `plugin.orders.pay_method` (Text, nullable)
- 类型: **text**（自由）
- 上游: TikTok 卖家中心 `/api/fulfillment/order/list` 响应 `trade_order_module.pay_method`
- 文档锚点: `tech-doc/chrome-ext-order-sync-design.md:158,185`、`tech-doc/tiktok-seller-center-api-catalog.md:195,599`

## 取值（🟡 自由文本，需查 `is_cod` 字段判定是否货到付款）

| 等级 |  实测值 | 含义（推断） | 出现条件 |
| :---: | --- | --- | --- |
| 🟡 |  `"Cash on delivery"` | 货到付款 | 与 `is_cod=true` 配对 |
| 🟡 |  `"Zalopay"` | 越南本地电子钱包 | 越南市场（VN） |
| 🔴 |  （其他） | 待补 | — |

## ⚠️ 未固化值速查

- 🟡 **2 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``"Cash on delivery"`` — 货到付款
  - 🟡 ``"Zalopay"`` — 越南本地电子钱包

- 🔴 **1 个值未观测** —— 枚举可能存在但本项目无样本，禁止拍脑袋假设。
  - 🔴 `（其他）` — 待补


## 已知 gap

- ❌ **完全未归一化**。代码 `tts_erp_v2/plugin/orders/parser.py:107` 直接 `tom.get("pay_method")` 存原值
- ❌ 跨国家枚举值会变（VN/TH/PH 不同支付渠道），需要 `sale_region` 一起看
- ✅ 替代判定：用 `is_cod=true` 字段判定货到付款（`commerce.sales_orders` 不存此字段，需在 `integration.raw_records.payload.is_cod` 查）

## 引用
- 代码: `tts_erp_v2/plugin/orders/parser.py:107`、`tts_erp_v2/db/models/plugin.py:106`
- 文档: `tech-doc/chrome-ext-order-sync-design.md §3 / §4`、`tech-doc/tiktok-seller-center-api-catalog.md §3`
