# SPU ROI 数据源映射（API 数据源 + plugin 数据源）

> **本文档回答的问题**：[`spu-roi-profit-calculation.md`](spu-roi-profit-calculation.md) 里定义的每个业务概念
> （有效销售订单 / 全损 / 已结算 / 净收入……）在数据库里**到底是哪张表、哪个字段、什么枚举值**。
> 概念与公式的定义不在这里，在利润口径文档里；本文档只做「概念 → 物理数据」的映射。
> 创建：2026-09-15（从 spu-roi-profit-calculation.md 拆出）

---

## 0. 总览：两条互斥的数据链路

店铺级互斥（同一店铺不会同时走两条路径）：

| 链路 | 判定依据 | 采集方式 | 落库 schema |
| --- | --- | --- | --- |
| **API 数据源** | `commerce.shops.credential_id IS NOT NULL` | sync-worker 定时调 TikTok Shop Open API | `commerce.*` / `finance.*` / `fulfillment.*` / `after_sales.*` |
| **plugin 数据源** | `commerce.shops.credential_id IS NULL` | Chrome 扩展拦截 Seller Center 页面响应，dump 上传 | `plugin.*` |

**三个例外（两路共用 / 交叉）**：

1. **广告消耗**没有 Open API 路径，两路都来自插件抓取 → `plugin.ad_*`（见 §3）
2. **汇率** `fx.*`、**采购成本** `procurement.*` 与订单来源无关，两路共用（见 §4）
3. 店铺注册表 `commerce.shops` 两路共享（插件店铺由运营人工注册，仅用于查询关联）

---

## 1. API 数据源（sync-worker → TikTok Open API）

> 链路细节：`tech-doc/architecture-overview.md`；订单域规则：`tech-doc/order-domain-business-rules.md`
> 当前 SPU ROI 实现（v9）读的就是这套表：`tts_erp_v2/analytics/spu_roi.py`

### 1.1 概念 → 表/字段映射

| 业务概念 | 表.字段 | 说明 |
| --- | --- | --- |
| 订单 | `commerce.sales_orders` | `id` = 内部 PK（`order_pk`）；`order_id` = 平台订单号（text） |
| 订单状态 | `sales_orders.status` | text 枚举，见 §1.2 |
| 订单金额 | `sales_orders.payment_amount`（实付）/ `total_amount` | 店铺当地币种（VN = VND） |
| 订单时间 | `sales_orders.paid_at`（付款）/ `order_time`（下单）/ `cancelled_at` | 窗口裁剪用 `COALESCE(paid_at, order_time)` |
| 商品行 | `commerce.sales_order_lines` | `order_pk` FK；`spu_pk` 关联 SPU；`quantity` 件数 |
| 行 GMV | `sales_order_lines.quantity × unit_price` | 无独立列，计算得出 |
| 已结算判定 | `EXISTS (SELECT 1 FROM finance.settlement_transactions st WHERE st.order_pk = sales_orders.id)` | |
| 实际到账（SETTLEMENT） | `finance.settlement_components.amount`，`component_code = 'SETTLEMENT'`，经 `transaction_id = settlement_transactions.id` 关联 | 按订单 SUM 后按行 GMV 占比分摊 |
| 退货 / 退款售后单 | `after_sales.cases` + `after_sales.case_lines` | 枚举见 §1.3；`case_lines.quantity` = 退货件数，`refund_amount` = 退款额 |
| 售后单完结时间 | `cases.updated_at_source` | 退货桶窗口裁剪按它 |
| 物流 | `fulfillment.shipments`（`order_pk`）→ `fulfillment.tracking_events`（`shipment_id`） | |
| 已到目的国判定 | `tracking_events.action_code = 38301` | integer 列，见 §1.4 |

### 1.2 订单状态枚举（text）

定义在 `tts_erp_v2/db/constants.py`：

```python
PAID_SALES_ORDER_STATUSES = {   # 已付款白名单 = 有效销售订单口径
    "AWAITING_SHIPMENT", "PARTIAL_SHIPPING", "AWAITING_COLLECTION",
    "IN_TRANSIT", "DELIVERED", "COMPLETED",
}
UNPAID_SALES_ORDER_STATUSES = {"UNPAID", "ON_HOLD", "CANCELLED"}
```

生命周期：`AWAITING_SHIPMENT → AWAITING_COLLECTION → IN_TRANSIT → DELIVERED → COMPLETED`，
任意阶段可 → `CANCELLED`。绝对终态 = `COMPLETED` / `CANCELLED`。无 `REFUNDED` 状态（退款不改订单状态）。

### 1.3 售后单枚举（`after_sales.cases`）

| 字段 | 枚举 | 说明 |
| --- | --- | --- |
| `case_type` | `RETURN_AND_REFUND` / `REFUND_ONLY` / `CANCELLATION` | 前两个 = v9 的「退货」；`CANCELLATION` 只进退款拆分，不进全损 |
| `status`（完结） | `RETURN_OR_REFUND_REQUEST_COMPLETE` / `CANCELLATION_REQUEST_COMPLETE` | 代码常量 `_CASE_COMPLETED_STATUSES`；退货桶只用前者 |

### 1.4 物流 action_code 速查（`fulfillment.tracking_events.action_code`）

| code | 含义 | v9 用途 |
| ---: | --- | --- |
| **38301** | Arrived in destination country/region | **海外取消 = 全损的判定** |
| 50101 | Delivered（签收） | 物流终态 |
| 80101 | Returned to seller | 物流终态 |
| 110101 | Delivery canceled | 物流终态 |

### 1.5 结算费用构成（验证用，`finance.settlement_components.component_code`）

`SETTLEMENT`（卖家实际到账，v9 净收入用）、`PLATFORM_COMMISSION`、`AFFILIATE_COMMISSION`、
`SHIPPING_FEE`、`CUSTOMER_REFUND`。

---

## 2. plugin 数据源（Chrome 扩展拦截 Seller Center）

> 取数口径权威文档：`tech-doc/intercept-plugin-canonical.md`（endpoint 清单 / dump 协议 / 4 域关联）
> 核心原则：`plugin.raw_log` 是 source-of-truth，业务表是 derived view，所有业务表 `log_id` FK 可溯源到具体 dump。

### 2.1 endpoint → 表

| 来源 endpoint | 落库表 | 内容 |
| --- | --- | --- |
| `POST /api/fulfillment/order/list` | `plugin.orders` + `plugin.order_lines` | 订单头 + SKU 行（sku_module[]） |
| `POST /logistic_detail/list` | `plugin.shipments` + `plugin.tracking_events` | 包裹 + 轨迹事件 |
| `POST /api/v1/pay/statement/order/list?settlement_status=1` | `plugin.settlements` | 结算单头 |
| `POST /api/v1/pay/statement/transaction/detail` | `plugin.settlement_details` | SKU 级费用明细 |
| `POST /return_refund/202309/cancellations/search` | `plugin.after_sales` + `plugin.after_sale_items` | 售后/取消单头 + 行项目 |

### 2.2 概念 → 表/字段映射

| 业务概念 | 表.字段 | 原始响应字段 | 说明 |
| --- | --- | --- | --- |
| 订单 | `plugin.orders.order_id` | `main_order_id` | text 平台订单号 |
| 订单状态 | `plugin.orders.main_order_status` | `main_order_status` | **int 码**，枚举见 §2.3 |
| 订单金额 | `orders.payment_amount` / `total_amount` | `price_module.grand_total.price_val` / `sub_total` | |
| 订单时间 | `orders.order_time` | `time_order_module.create_time`（秒级字符串） | |
| 商品行 | `plugin.order_lines` | `sku_module[]` | `quantity` 件数 |
| 行 GMV | `order_lines.total_price`（或 `quantity × unit_price`） | `sku_total_price` / `sku_unit_price.price_val` | |
| 行级状态 | `order_lines.main_order_status` / `sku_display_status` | 同名 | int 码 |
| 物流包裹 | `plugin.shipments` | package_list[] | `order_id → package_id`、`tracking_number`、`carrier_name` |
| 物流轨迹 | `plugin.tracking_events` | track_list[] | ⚠ 见 §2.4 缺口 |
| 售后/取消单 | `plugin.after_sales` | cancellations[] | `main_order_id` 关联订单；枚举见 §2.5 |
| 售后行项目 | `plugin.after_sale_items` | cancel_line_items[] | `quantity`、`refund_amount`，支持部分取消 |
| 结算单头 | `plugin.settlements` | statement list | statement 级：`settle_amount` / `earning_amount` / `fee_amount` / `payment_status` |
| 结算明细 | `plugin.settlement_details` | transaction detail | SKU 级；**`trade_order_id` = 订单号，是结算 → 订单的关联键** |
| 已结算判定 | `plugin.settlement_details` 存在该 `trade_order_id` 的记录（`settlement_status` / `settlement_time` 辅助） | | 与 API 侧「存在结算记录」同义 |
| 实际到账 | `settlement_details.settlement_amount`（或 `earning_amount`）按 `trade_order_id` 汇总 | | 费用拆分在 `fee_components` jsonb |

### 2.3 订单状态码（`main_order_status`，int）

raw 响应**只有 int 码，无文本枚举**。以下为 2026-09-14 prod 494 单实测交叉验证
（`tech-doc/intercept-plugin-canonical.md` §3.5+）：

| 码 | 推断文本态 | 置信度 |
| ---: | --- | --- |
| 100 | UNPAID（待付款） | 中 |
| 101 | AWAITING_SHIPMENT（待发货） | 中（待 Seller Center tab 终验） |
| 102 | 已发货 / 运输中（IN_TRANSIT 一类） | 中（待终验） |
| 103 | 售后 / 退货中 | 高（7/7 伴随 reverse_type=3） |
| 104 | CANCELLED | 高（86/86 伴随 reverse_type=4） |

逆向信号另一来源：订单 dump 的 `reverse_module[]`（**未结构化，在 `plugin.raw_log.response_body`**），
`reverse_type` 3 = 退货 / 4 = 取消（⚠ 样本推断待核实）。

### 2.4 ⚠ 已知缺口：plugin 侧无 `action_code` 结构化列

`plugin.tracking_events` 只有 `event_key / event_at / description(=track_status) / location`，
**没有 `action_code` 列**——raw 里的 action_code 只被拼进 `event_key` 复合串。
因此 v9 的「海外取消 = 已取消 ∧ action_code=38301」在 plugin 侧**无法直接套 SQL**，需要：
回 `plugin.raw_log.response_body` 重解析，或解析 `event_key`，或给 `plugin.tracking_events` 补列（TODO）。

### 2.5 售后单枚举（`plugin.after_sales`）

| 字段 | 枚举 | 说明 |
| --- | --- | --- |
| `cancel_type` | `BUYER_CANCEL` / `CANCEL` | 买家取消 / 系统·卖家取消 |
| `cancel_status` | `CANCELLATION_REQUEST_COMPLETE` | 完结（实测全部为此值） |

### 2.6 其他已知缺口 / TODO

- 101 / 102 码值待 Seller Center 页面 tab 对照终验
- 售后解析器字段名基于设计文档假设，chrome 扩展尚未抓到过真实响应（0 hit），首次真实响应后需校准
- 时间字段解析曾踩坑：raw 是数字字符串（秒/毫秒/微秒混合），字段级解析失败不落 `parse_error`，只看 `log.warning`（2026-09-14 教训）

---

## 3. 广告数据源（两路共用，只有插件一条采集路径）

| 表 | 用途 |
| --- | --- |
| `plugin.ad_daily` | 历史天级，**当前 SPU ROI 唯一取数源**（v8.1 起，按日切片） |
| `plugin.ad_today` | 今天实时（30s 滚动）；暂不进 ROI 取数路径 |
| `plugin.ad_monthly` | 月级聚合 |
| `plugin.ad_raw_log` | 原始 dump（source-of-truth） |

关键字段（沿用 TikTok 原名）：`mixed_real_cost` = 真实消耗；`onsite_roi2_shopping_sku` = 出单量；
`onsite_roi2_shopping_value` = 出单 GMV。SPU 关联：`product_id` → `commerce.products_spu.spu_id`
（经 `commerce.shops.shop_id = seller_id`）。endpoint 过滤
`'/oec_ads/shopping/v1/oec/stat/post_product_list'`。

> ⚠ 历史注记：`analytics.ad_product_links` 视图（migration 0006/0014）已于 migration 0020 删除；
> `biz-doc/analytics/ad-product-links-view.md` 为历史档案，勿再作为取数依据。
> 已知窗口缺口：merge job 2026-09-13 禁用后 `ad_daily` 未增，09-14+ 的广告消耗为 0（P0 follow-up）。

## 4. 汇率与采购（两路共用）

| 业务概念 | 表.字段 | 说明 |
| --- | --- | --- |
| 汇率快照 | `fx.exchange_rate_snapshots` + `fx.exchange_rates` | 在线快照，禁用过期硬编码常量；详见 `tech-doc/fx-exchange-rates.md` |
| 人工标注成本（优先级 1） | `procurement.manual_product_costs.unit_cost`，`valid_to IS NULL` = 当前有效 | 按 `spu_pk` |
| 货源价（优先级 2） | `procurement.procurement_products.source_unit_cost` | 按 `synced_at DESC` 取最新 |
| 兜底（优先级 3） | 硬编码 40 CNY/件 | |

## 5. 锚点

| 类型 | 位置 |
| --- | --- |
| 概念/公式定义 | `biz-doc/analytics/spu-roi-profit-calculation.md`（v9） |
| API 侧实现 | `tts_erp_v2/analytics/spu_roi.py`（v9 SQL 全集） |
| 状态枚举代码 | `tts_erp_v2/db/constants.py` |
| plugin 解析器 | `tts_erp_v2/plugin/orders/parser.py` |
| plugin 取数口径 | `tech-doc/intercept-plugin-canonical.md` |
| 订单域业务规则 | `tech-doc/order-domain-business-rules.md` |
