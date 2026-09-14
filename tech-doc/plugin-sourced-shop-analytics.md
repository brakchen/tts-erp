# 插件数据源店铺的利润计算与展示方案

> 状态：设计已定稿（2026-09-14 用户拍板），待实施
> 关联：`tech-doc/chrome-ext-order-sync-design.md`（plugin schema 采集侧）、
> `biz-doc/analytics/spu-roi-profit-calculation.md`（v9 利润口径）

## 1. 背景

部分店铺（或店铺的部分数据域）不来自 TikTok OpenAPI，而来自 Chrome 插件
（`~/chrome-plugins/ads-data-sync`）拦截 Seller Center 页面响应落 `plugin.*`。
现有 `pages/spu-roi` 的计算（`tts_erp_v2/analytics/spu_roi.py`）依赖
commerce / finance / after_sales / fulfillment 四套 API 侧 schema，
纯插件店铺这些表无数据 → 页面无法展示。

## 2. 架构决策（2026-09-14 用户拍板）

1. **一个页面**：`pages/spu-roi` 不分拆，数据源差异在服务端消化，页面无感。
2. **广告永远来自插件**：`plugin.ad_daily / ad_today`，所有店铺如此（现状即如此，不变）。
3. **非广告数据（订单/结算/物流/售后）：API 与插件两套独立实现，不做运行时合并去重。**
   每个店铺按其实际可用来源走其中一套，各自算完输出同一响应结构。
4. **插件是基线**：API 权限申请不下来 → 插件数据为准；API 店凭证失效/数据缺口 →
   可落回插件路径。判定规则：**店铺 × 数据域，API 侧有数据走 API，否则走插件**
   （运行时按数据有无求值，不维护配置表）。混合店同域两边都有数据时以 API 为准，
   插件同域数据不参与计算（不合并）。
5. **售后也将由插件采集**（规划）：服务端预留 `plugin.after_sales_cases /
   after_sales_case_lines` 表与完整 v9 口径；插件补抓上线前，插件店的退货桶为空
   （数据未到，非口径降级），数据补齐后页面自动完整，口径代码不改第二遍。

## 3. 数据源矩阵

| 数据域 | API 店 | 纯插件店 | 备注 |
| --- | --- | --- | --- |
| 广告消耗 | `plugin.ad_daily/ad_today` | 同左 | 两路径共用 |
| 订单/订单行 | `commerce.sales_orders/lines` | `plugin.orders/order_lines` | 状态表示不同，见 §4.2 |
| 结算 | `finance.settlement_transactions/components` | `plugin.settlements/settlement_details` | SKU 级可按 `trade_order_id` 聚合回订单级 |
| 物流轨迹 | `fulfillment.shipments/tracking_events` | `plugin.shipments/tracking_events` | 海外判定见 §4.3 |
| 售后 | `after_sales.cases/case_lines` | `plugin.after_sales_*`（预留） | §4.4 |
| 店铺身份 | `commerce.shops`（插件店经 `/v2/admin/shops/register` 人工注册） | 同左 | 已有机制 |
| SPU 目录 | `commerce.products_spu` | 见 §4.1 | 关键缺口 |
| 采购成本 / 汇率 | `procurement.*` / `fx.*` | 同左 | 与店铺数据源无关 |

## 4. 缺口与处理

### 4.1 SPU 目录（关键）

插件店无 `products_spu` 行 → 广告花费无法归因 SPU、页面目录为空、
`procurement.manual_product_costs`（按 `spu_pk` 挂）无处挂。

处理：插件路径的 catalog 查询 UNION `plugin.order_lines` 出现过的
`(shop_id, product_id)`（title / image 取 order_lines 快照），不回写
`products_spu`。成本标注入口对插件 SPU 另行支持（ keyed by
`(shop_id, product_id)` 或先补一行 products_spu，实施时定）。

### 4.2 订单状态码映射

`plugin.orders.main_order_status` 是 Seller Center int 状态码，
v9 口径用文本白名单 `PAID_SALES_ORDER_STATUSES`。需建 int → 文本状态映射
常量（`db/constants.py`），**先用 prod 数据实测码值分布再固化**，禁止拍脑袋。

### 4.3 海外取消判定（action_code=38301）

`plugin.tracking_events` 未存 action_code 独立列（仅无 event_id 时拼进
`event_key`）。处理：`plugin.tracking_events` 加 `action_code` 列 + parser 补写，
历史数据可从 `event_key` 部分回填。加列前插件店海外取消桶为空（同售后：数据未到）。

### 4.4 售后采集（插件侧规划）

服务端：建 `plugin.after_sales_cases / after_sales_case_lines`
（仿 orders 模式：raw_log + 业务表 + log_id 溯源）。插件侧：拦截 Seller Center
售后列表/详情接口，另开发版。服务端表结构先行，插件上线即可灌数。

## 5. 读侧结构

`spu_roi.py` 拆成两个数据源实现（commerce 路径 = 现有 6 条 SQL 不动；
plugin 路径 = 对应 6 条基于 `plugin.*` 的 SQL），入口按 §2.4 规则选择路径，
输出同一响应结构。钻取端点（orders / settlements / cases / ads 明细）同样按路径分发。

## 6. 明确不做

- ❌ 不把 plugin 数据 ETL 进 commerce/finance（违反 migration 0025 物理隔离决策）
- ❌ 不做运行时双源合并去重（用户拍板两套独立实现）
- ❌ 不建聚合结果表 / 定时计算 job（远期性能优化项，需要时可平滑叠加，页面不改）
- ❌ 不建数据源配置表（来源判定 = 运行时按数据有无求值）

## 7. 实施阶段

| 阶段 | 内容 |
| --- | --- |
| P0 | 状态码实测固化映射；`plugin.tracking_events.action_code` 加列 + parser 补写 |
| P1 | `plugin.after_sales_*` 建表 + dump 解析落库（等插件侧接口） |
| P2 | spu_roi plugin 路径 SQL（catalog union / 订单 / 结算 / 物流 / 售后）+ 路径选择 |
| P3 | 钻取端点 plugin 路径；成本标注对插件 SPU 的支持 |
| P4 | 插件侧售后拦截发版，回灌历史 |

## 8. 待验证清单

- [ ] `plugin.orders.main_order_status` 实际码值分布（prod 实测）→ 映射表
- [ ] `plugin.settlement_details` 对订单的覆盖率（是否所有已结算订单都有 detail）
- [ ] `plugin.settlement_details.fee_components` 与 finance SETTLEMENT 口径对账
- [ ] Seller Center 售后列表接口可拦截性（插件侧 spike）
