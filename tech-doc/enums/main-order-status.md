# `plugin.orders.main_order_status` — 卖家中心 int 主订单状态

> Chrome ext 从卖家中心抓到的**原始 int 状态码**，未翻译为 v2 标准 text。
> 这是**与 `commerce.sales_orders.status` 同维度的不同编码**，不是更细的子状态。
>
> 🟡 **实测推断** —— 项目尚未把 int → text 映射固化为 `db/constants.py` 常量。
> 任何依赖此字段的下游代码（`tech-doc/plugin-sourced-shop-analytics.md §4.2`）目前只能基于 prod 样本交叉验证。

## 来源
- DB column: `plugin.orders.main_order_status` (Integer, nullable) / `plugin.order_lines.main_order_status`
- 类型: **int**（卖家中心原始码）
- 上游: TikTok 卖家中心 `/api/fulfillment/order/list` 响应 `order_status_module[].main_order_status`
- 文档锚点: `tech-doc/plugin-sourced-shop-analytics.md §4.2 / §8`、`tech-doc/dumps-data-contract.md §5.5 / §11`

## 取值（🟡 实测推断，**未固化**）

> 推断依据 = `tech-doc/plugin-sourced-shop-analytics.md §8` prod 样本（494 单）按
> `tracking_no`（有无面单）+ `reverse_module`（有无售后/取消）交叉分类。

| 等级 |  int 码 | 推断文本 | 推断依据 | 实测 n |
| :---: | ---: | --- | --- | ---: |
| 🟡 |  `100` | UNPAID（未付款） | 1 单无面单无 tracking | 1 |
| 🟡 |  `101` | AWAITING_SHIPMENT（待发货） | 71 单有面单无 reverse | 71 |
| 🟡 |  `102` | IN_TRANSIT（在途）/ AWAITING_COLLECTION / DELIVERED | 329 单有 tracking_no，18 单带 reverse | 329 |
| 🟡 |  `103` | 售后/退货中 | 7/7 带 `reverse_type=3`（"商品与描述不符"） | 7 |
| 🟡 |  `104` | CANCELLED | 86/86 全部带 `reverse_type=4`（买家取消） | 86 |

## ⚠️ 未固化值速查

- 🟡 **5 个值实测但未固化** —— 含义命名按 prod `description` 字段直译/推断。
  - 🟡 ``100`` — UNPAID（未付款）
  - 🟡 ``101`` — AWAITING_SHIPMENT（待发货）
  - 🟡 ``102`` — IN_TRANSIT（在途）/ AWAITING_COLLECTION / DELIVERED
  - 🟡 ``103`` — 售后/退货中
  - 🟡 ``104`` — CANCELLED


## 与 `commerce.sales_orders.status` 关系

| commerce 状态 | plugin int 推断 | 备注 |
| --- | ---: | --- |
| `UNPAID` | 100 | 推断 |
| `AWAITING_SHIPMENT` | 101 | 推断 |
| `IN_TRANSIT` / `AWAITING_COLLECTION` / `DELIVERED` | 102 | 102 范围内可能细分多个 v2 状态，**单一 int 容纳多个 v2 状态**——粒度比 v2 粗 |
| （无对应 — plugin 单独细分） | 103 | 卖家中心特有"售后中"细分 |
| `CANCELLED` | 104 | 推断 |

## 已知 gap

1. ❌ **未固化** —— `tech-doc/plugin-sourced-shop-analytics.md §4.2` 明确："**需建 int → 文本状态映射常量（`db/constants.py`），先用 prod 数据实测码值分布再固化，禁止拍脑袋**"
2. ❌ **102 粒度不够** —— 单 int 102 包含 `IN_TRANSIT` + `AWAITING_COLLECTION` + `DELIVERED` 三个 v2 状态；只有 `tracking_events` 才能进一步细分
3. ❌ `main_sub_order_status` 100/101/102/103/104 + `sku_display_status` 子状态码同样未固化

## 引用
- 代码: `tts_erp_v2/plugin/orders/parser.py:100-105`、`tts_erp_v2/db/models/plugin.py:100,151`
- 文档: `tech-doc/plugin-sourced-shop-analytics.md §4.2 / §8`、`tech-doc/dumps-data-contract.md §11`、`tech-doc/chrome-ext-order-sync-design.md §3 / §5`
- API 文档: `tech-doc/tiktok-seller-center-api-catalog.md:142-148`
