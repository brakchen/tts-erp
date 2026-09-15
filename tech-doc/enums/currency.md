# 货币（ISO 4217）

> 多处列存的货币代码（`commerce.shops.currency` / `sales_orders.currency` /
> `plugin.orders.currency` / `settlements.currency` / `after_sales.refund_amount` 的 `currency` 字段等）。
> **ISO 4217 三字母**。

## 来源
- 多个 DB column（text）
- 类型: **text**（ISO 4217 alpha-3）
- 文档锚点: 项目通用约定（无单一文件定义）

## 取值

| 等级 |  值 | 含义 | 出现 |
| :---: | --- | --- | ---: |
| 🟡 |  `VND` | 越南盾 (Vietnamese Dong) | ✓（Bridge nook 全部订单） |
| 🟡 |  `USD` | 美元 (US Dollar) | 是（拉美/其他市场） |
| 🟡 |  `THB` | 泰铢 (Thai Baht) | 泰国市场 |
| 🟡 |  `PHP` | 菲律宾比索 | 菲律宾市场 |
| 🟡 |  `MYR` | 马来西亚林吉特 | 马来西亚市场 |
| 🟡 |  `SGD` | 新加坡元 | 新加坡市场 |
| 🟡 |  `IDR` | 印度尼西亚盾 | 印尼市场 |
| 🔴 |  `CNY` | 人民币 | （国内业务，理论上不出现） |
| 🔴 |  `EUR` | 欧元 | 欧洲市场（TikTok EU） |

## ⚠️ 未固化值速查

- 🟡 **7 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``VND`` — 越南盾 (Vietnamese Dong)
  - 🟡 ``USD`` — 美元 (US Dollar)
  - 🟡 ``THB`` — 泰铢 (Thai Baht)
  - 🟡 ``PHP`` — 菲律宾比索
  - 🟡 ``MYR`` — 马来西亚林吉特
  - …（其余 2 个见下方"## 取值"表）

- 🔴 **2 个值未观测** —— 枚举可能存在但本项目无样本，禁止拍脑袋假设。
  - 🔴 ``CNY`` — 人民币
  - 🔴 ``EUR`` — 欧元


## 已知 gap

- ❌ **没有"已知币种"白名单常量**（`db/constants.py` 仅有产品状态 / 订单状态白名单）
- ❌ 货币汇率口径见 `tech-doc/fx-exchange-rates.md`

## 引用
- 文档: `tech-doc/fx-exchange-rates.md`（汇率口径）
