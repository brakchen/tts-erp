# `plugin.orders.sale_region` — 销售地区（ISO 3166-1 alpha-2）

> Chrome ext 从卖家中心抓到的销售地区。
> 📌 **ISO 3166-1 alpha-2 国家码**（两字母缩写）。

## 来源
- DB column: `plugin.orders.sale_region` (Text, nullable)
- 类型: **text**（两字母 ISO 3166-1 alpha-2）
- 上游: TikTok 卖家中心 `/api/fulfillment/order/list` 响应 `trade_order_module.sale_region`
- 文档锚点: `tech-doc/chrome-ext-order-sync-design.md:160`

## 取值（✅ 项目自有限定）

| 等级 |  值 | 含义 | 实测 |
| :---: | --- | --- | ---: |
| ✅ |  `VN` | 越南 (Vietnam) | ✓（Bridge nook 店铺） |
| 🔴 |  `TH` | 泰国 (Thailand) | — |
| 🔴 |  `PH` | 菲律宾 (Philippines) | — |
| 🔴 |  `MY` | 马来西亚 (Malaysia) | — |
| 🔴 |  `SG` | 新加坡 (Singapore) | — |
| 🔴 |  `ID` | 印度尼西亚 (Indonesia) | — |
| 🔴 |  （其他） | ISO 3166-1 alpha-2 全集 | — |

> 当前项目只跑 **Bridge nook VN**（`shop_id=7494763368967603447`）+ 一个**未确认**店铺
> （`shop_id=7494864868604150914`），但卖家中心响应里也只观察到 `VN`。
> 其余枚举值**未实测**。

## ⚠️ 未固化值速查

- 🔴 **6 个值未观测** —— 枚举可能存在但本项目无样本，禁止拍脑袋假设。
  - 🔴 ``TH`` — 泰国 (Thailand)
  - 🔴 ``PH`` — 菲律宾 (Philippines)
  - 🔴 ``MY`` — 马来西亚 (Malaysia)
  - 🔴 ``SG`` — 新加坡 (Singapore)
  - 🔴 ``ID`` — 印度尼西亚 (Indonesia)
  - …（其余 1 个见下方"## 取值"表）


## 关联字段

- `commerce.shops.region` —— 也是 ISO 3166-1 alpha-2，但来自卖家中心 OAuth 时填的 `region`，
  在数据库 schema 上**与 sale_region 重复含义但不同来源**。详见 [`region.md`](region.md)。

## 引用
- 代码: `tts_erp_v2/plugin/orders/parser.py:108`、`tts_erp_v2/db/models/plugin.py:107`
- 文档: `tech-doc/chrome-ext-order-sync-design.md §3`
