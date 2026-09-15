# `plugin.orders.sku_display_status` — 卖家中心 int SKU 展示状态

> Chrome ext 从卖家中心抓到的**SKU 级别的展示状态码**。
> 🟡 **实测推断** —— 与 `main_order_status` 配套，int → text 同样未固化。

## 来源
- DB column: `plugin.orders.sku_display_status` (Integer, nullable) / `plugin.order_lines.sku_display_status`
- 类型: **int**（卖家中心原始码）
- 上游: TikTok 卖家中心 `/api/fulfillment/order/list` 响应 `order_status_module[].sku_display_status`
- 文档锚点: `tech-doc/tiktok-seller-center-api-catalog.md:142-148`

## 取值（🟡 实测样本，未固化）

> 实测分布（prod 894 单 + 494 单混合）：

| 等级 |  int 码 | 出现次数 | 出现条件（推断） |
| :---: | ---: | ---: | --- |
| 🟡 |  `100` | 1 | 与 `main=100` 同订单（未付款） |
| 🟡 |  `111` | 36 | 与 `main=101` 配对（待发货 + SKU 展示中） |
| 🟡 |  `112` | 35 | 与 `main=101` 配对（待发货 + SKU 备货中） |
| 🟡 |  `121` | 152 | 与 `main=102` 配对（在途 + SKU 已发货） |
| 🟡 |  `122` | 370 | 与 `main=102` 配对（在途 + SKU 运输中） |
| 🟡 |  `130` | 421 | 与 `main=103` 配对（售后中 + SKU 退货中） |
| 🟡 |  `140` | 372 | 与 `main=104` 配对（已取消 + SKU 已取消） |

## ⚠️ 未固化值速查

- 🟡 **7 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``100`` — 1
  - 🟡 ``111`` — 36
  - 🟡 ``112`` — 35
  - 🟡 ``121`` — 152
  - 🟡 ``122`` — 370
  - …（其余 2 个见下方"## 取值"表）


## 已知 gap

- ❌ **完全未固化**。`tech-doc/plugin-sourced-shop-analytics.md §4.2` 把它和 `main_order_status` 列在同一条 TODO 下。
- ❌ int 末位（0/1/2）的细分规则未在代码里映射过。

## 引用
- 代码: `tts_erp_v2/plugin/orders/parser.py:100-105`、`tts_erp_v2/db/models/plugin.py:101,152`
- 文档: `tech-doc/plugin-sourced-shop-analytics.md §4.2`、`tech-doc/tiktok-seller-center-api-catalog.md:142-148`
