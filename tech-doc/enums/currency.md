# 货币（ISO 4217）

> 多处列存的货币代码（`commerce.shops.currency` / `sales_orders.currency` /
> `plugin.orders.currency` / `settlements.currency` / `after_sales.refund_amount` 的 `currency` 字段等）。
> **ISO 4217 三字母**。

## 来源
- 多个 DB column（text）
- 类型: **text**（ISO 4217 alpha-3）
- 文档锚点: 项目通用约定（无单一文件定义）

## 实测样本

| 值 | 含义 | 出现 |
| --- | --- | ---: |
| `VND` | 越南盾 (Vietnamese Dong) | ✓（Bridge nook 全部订单） |
| `USD` | 美元 (US Dollar) | 是（拉美/其他市场） |
| `THB` | 泰铢 (Thai Baht) | 泰国市场 |
| `PHP` | 菲律宾比索 | 菲律宾市场 |
| `MYR` | 马来西亚林吉特 | 马来西亚市场 |
| `SGD` | 新加坡元 | 新加坡市场 |
| `IDR` | 印度尼西亚盾 | 印尼市场 |
| `CNY` | 人民币 | （国内业务，理论上不出现） |
| `EUR` | 欧元 | 欧洲市场（TikTok EU） |

## 已知 gap

- ❌ **没有"已知币种"白名单常量**（`db/constants.py` 仅有产品状态 / 订单状态白名单）
- ❌ 货币汇率口径见 `tech-doc/fx-exchange-rates.md`

## 引用
- 文档: `tech-doc/fx-exchange-rates.md`（汇率口径）
