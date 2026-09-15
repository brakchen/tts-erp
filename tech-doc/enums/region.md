# `commerce.shops.region` — 店铺区域

> 卖家中心 OAuth 时填的店铺区域。
> 与 [`sale-region.md`](sale-region.md) 含义相同但来源不同。

## 来源
- DB column: `commerce.shops.region` (Text, nullable)
- 类型: **text**（ISO 3166-1 alpha-2 两字母国家码）
- 上游: TikTok OAuth 授权时卖家填的 region
- 文档锚点: `tts_erp_v2/db/models/commerce.py:60`

## 取值（与 `sale-region.md` 同源）

| 等级 |  值 | 含义 | 实测 |
| :---: | --- | --- | ---: |
| 🟡 |  `VN` | 越南 (Vietnam) | ✓（Bridge nook） |
| 🔴 |  （其他 ISO 3166-1 alpha-2） | — | 未观测 |

## ⚠️ 未固化值速查

- 🟡 **1 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``VN`` — 越南 (Vietnam)

- 🔴 **1 个值未观测** —— 枚举可能存在但本项目无样本，禁止拍脑袋假设。
  - 🔴 `（其他 ISO 3166-1 alpha-2）` — —


## 与 `sale-region` 关系

- **`commerce.shops.region`** —— 店铺注册时填，**静态**
- **`plugin.orders.sale_region`** —— 每笔订单的**销售地区**，**动态**

> 在项目当前数据中**两者应该一致**（VN 店铺只在 VN 卖），但**未来**若店铺跨境销售，理论上订单 sale_region 可能与店铺 region 不同。

## 引用
- 代码: `tts_erp_v2/db/models/commerce.py:60`
