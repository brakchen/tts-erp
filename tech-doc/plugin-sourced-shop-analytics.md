# 插件数据源店铺的利润计算与展示方案

> 状态：设计已定稿 v2（2026-09-14 用户拍板），待实施
> 关联：`tech-doc/chrome-ext-order-sync-design.md`（plugin schema 采集侧）、
> `biz-doc/analytics/spu-roi-profit-calculation.md`（v9 利润口径）

## 1. 背景

部分店铺（或店铺的部分数据域）不来自 TikTok OpenAPI，而来自 Chrome 插件
（`~/chrome-plugins/ads-data-sync`）拦截 Seller Center 页面响应落 `plugin.*`。
现有 `pages/spu-roi` 的计算（`tts_erp_v2/analytics/spu_roi.py`）依赖
commerce / finance / after_sales / fulfillment 四套 API 侧 schema，
纯插件店铺这些表无数据 → 页面无法展示。

## 2. 架构决策（2026-09-14 用户拍板，v2）

1. **两个页面**：现有 `pages/spu-roi`（API 数据源）保留不动，新增插件数据源页面
   （`pages/spu-roi-plugin`）。哪个店看哪个页面是运营选择，系统不做来源判断。
2. **广告永远来自插件**：`plugin.ad_daily`，两个页面共用。**ad_today 已废弃**——
   `jobs/ad_merge_today2daily.py` 固化 job 2026-09-13 起禁用，新代码只读 ad_daily
   （现有 `_SQL_ROI_AD` 仍 UNION ad_today 是旧逻辑，不在本次改动范围）。
3. **非广告数据（订单/结算/物流/售后）两个页面各自独立**：API 页面读
   commerce / finance / after_sales / fulfillment；插件页面读 `plugin.*`。
   不做合并、不做去重、不做跨源回退——页面即数据源声明。
4. **插件是基线**：API 权限申请不下来的店用插件页面看数；不存在"先 API 后插件"
   的自动切换逻辑。
5. **售后也将由插件采集**（规划）：服务端预留 `plugin.after_sales_cases /
   after_sales_case_lines` 表与完整 v9 口径；插件补抓上线前，插件页面的退货桶为空
   （数据未到，非口径降级），数据补齐后页面自动完整，口径代码不改第二遍。

> v1（同日早前）曾定为"一个页面 + 运行时按数据有无选源"；v2 改两个页面后，
> 来源选择逻辑、混合店去重规则全部不再需要。

## 3. 数据源矩阵

| 数据域 | API 页面 | 插件页面 | 备注 |
| --- | --- | --- | --- |
| 广告消耗 | `plugin.ad_daily` | 同左 | 两页面共用；ad_today 已废弃 |
| 订单/订单行 | `commerce.sales_orders/lines` | `plugin.orders/order_lines` | 状态表示不同，见 §4.2 |
| 结算 | `finance.settlement_transactions/components` | `plugin.settlements/settlement_details` | SKU 级可按 `trade_order_id` 聚合回订单级 |
| 物流轨迹 | `fulfillment.shipments/tracking_events` | `plugin.shipments/tracking_events` | 海外判定见 §4.3 |
| 售后 | `after_sales.cases/case_lines` | `plugin.after_sales_*`（预留） | §4.4 |
| 店铺身份 | `commerce.shops` | 同左 | 插件店经 `/v2/admin/shops/register` 人工注册 |
| SPU 目录 | `commerce.products_spu` | 见 §4.1 | 关键缺口 |
| 采购成本 / 汇率 | `procurement.*` / `fx.*` | 同左 | 与数据源无关 |

## 4. 缺口与处理（仅插件页面）

### 4.1 SPU 目录（关键）

插件店无 `products_spu` 行 → 广告花费无法归因 SPU、页面目录为空、
`procurement.manual_product_costs`（按 `spu_pk` 挂）无处挂。

处理：插件页面的 catalog 查询基于 `plugin.order_lines` 出现过的
`(shop_id, product_id)`（title / image 取 order_lines 快照），不回写
`products_spu`。成本标注入口对插件 SPU 另行支持（keyed by
`(shop_id, product_id)` 或先补一行 products_spu，实施时定）。

### 4.2 订单状态码映射

`plugin.orders.main_order_status` 是 Seller Center int 状态码，
v9 口径用文本白名单 `PAID_SALES_ORDER_STATUSES`。需建 int → 文本状态映射
常量（`db/constants.py`），**先用 prod 数据实测码值分布再固化**，禁止拍脑袋。

### 4.3 海外取消判定（action_code=38301）

`plugin.tracking_events` 未存 action_code 独立列（仅无 event_id 时拼进
`event_key`）。处理：`plugin.tracking_events` 加 `action_code` 列 + parser 补写，
历史数据可从 `event_key` 部分回填。加列前插件页面海外取消桶为空（同售后：数据未到）。

### 4.4 售后采集（插件侧规划）

服务端：建 `plugin.after_sales_cases / after_sales_case_lines`
（仿 orders 模式：raw_log + 业务表 + log_id 溯源）。插件侧：拦截 Seller Center
售后列表/详情接口，另开发版。服务端表结构先行，插件上线即可灌数。

## 5. 读侧结构

- API 页面 `pages/spu-roi`：现有 `GET /v2/analytics/spu-roi` 及钻取端点**一行不动**。
- 插件页面 `pages/spu-roi-plugin`：新增一套端点（如 `GET /v2/analytics/plugin-spu-roi`
  + 对应钻取端点），SQL 全部基于 `plugin.*`（广告部分与现有共用同一查询），
  输出响应结构与现有端点对齐，前端展示组件尽量复用。

## 6. 明确不做

- ❌ 不把 plugin 数据 ETL 进 commerce/finance（违反 migration 0025 物理隔离决策）
- ❌ 不做双源合并去重、不做来源自动判断（两个页面各自只读自己的源）
- ❌ 不建聚合结果表 / 定时计算 job（远期性能优化项，需要时可平滑叠加）
- ❌ 现有 API 页面及其端点零改动

## 7. 实施阶段

| 阶段 | 内容 |
| --- | --- |
| P0 | 状态码实测固化映射；`plugin.tracking_events.action_code` 加列 + parser 补写 |
| P1 | `plugin.after_sales_*` 建表 + dump 解析落库（等插件侧接口） |
| P2 | 插件页面后端：`/v2/analytics/plugin-spu-roi` + 钻取端点（v9 口径 plugin 版 SQL） |
| P3 | 插件页面前端（复用 spu-roi 展示组件）；成本标注对插件 SPU 的支持 |
| P4 | 插件侧售后拦截发版，回灌历史 |

## 8. 待验证清单（含 2026-09-14 prod 实测结果）

实测快照（prod，店铺 7494864868604150914，494 单 / 504 行）：

- [x] `plugin.order_lines`：币种全 VND，product_id 全覆盖（504/504）✓
- [x] `plugin.orders.main_order_status` 码值分布：100×1 / 101×71 / 102×329 / 103×7 / 104×86
      → 待与 Seller Center 页面显示核对后固化映射
- [ ] **`plugin.orders.order_time` 494 行全 NULL、update_time 仅 8 行非空**——
      parser 时间字段路径有 bug，不修则窗口切日不可用（P0 前置）
- [ ] **`plugin.settlements / settlement_details` 0 行**——raw_log 无 statement 端点记录，
      插件尚未抓结算（P0 前置；此前已结算净额只能全走 0.692 估算）
- [ ] **`plugin.shipments / tracking_events` 0 行**但 raw_log 有数百条 logistic_detail dump
      ——parser 未写出行，需排查 parse_error / package_list 为空（P0 前置）
- [ ] `plugin.settlement_details.fee_components` 与 finance SETTLEMENT 口径对账（有数据后）
- [ ] Seller Center 售后列表接口可拦截性（插件侧 spike）
