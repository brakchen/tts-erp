# `tech-doc/enums/` — 枚举值参考手册

> **目的**：把所有"枚举型"字段（status / type / mode / reason / event code / role / …）的合法值、来源、边界条件**集中沉淀**，避免到处 grep "这个 int 码是啥意思"。
>
> **背景**：本项目对账 / 反查 / 写同步 job 时，经常遇到"上游返回了一个 int/text，但我不知道它代表什么"的问题。状态码散落在：
>
> - 自己的 SQL 表结构 (`tts_erp_v2/db/models/`)
> - 上游 API 响应（`integration.raw_records.payload` / `plugin.intercepted_requests.response_body`）
> - 各处 `tech-doc/*.md` 的"实战笔记"
>
> 没有一处把它们**集中 + 标注来源**。本目录就是为这个目的建的。

---

## 怎么读

每个文件一个独立枚举值空间，文件名 = 字段名（或组合字段名）。文件结构统一：

```markdown
# <枚举名>

> <一句话定义>

## 来源
- DB column: `<schema>.<table>.<column>`
- 类型: int | text
- 上游: <TikTok API path | Chrome ext endpoint | 内部定义>
- 文档锚点: <相关 tech-doc 链接>

## 取值
| 值 | 含义 | 实测样本 | 出现条件 |
| --- | --- | --- | --- |
| `1` | 含义 | n=... | ... |

## 已知 gap
- <任何未固化的部分>

## 引用
- 代码: `<file:line>`
- 文档: `<file:line>`
```

---

## 索引

### 订单 / 物流（核心交易域）

| 字段 | 类型 | 文件 |
| --- | --- | --- |
| `commerce.sales_orders.status` | text | [`order-status.md`](order-status.md) |
| `plugin.orders.main_order_status` | int (卖家中心) | [`main-order-status.md`](main-order-status.md) |
| `plugin.orders.sku_display_status` | int (卖家中心) | [`sku-display-status.md`](sku-display-status.md) |
| `fulfillment_type` | text / int 双口径 | [`fulfillment-type.md`](fulfillment-type.md) |
| `pay_method` | text (自由文本) | [`pay-method.md`](pay-method.md) |
| `sale_region` | text (ISO 3166-1) | [`sale-region.md`](sale-region.md) |
| `sales_order_lines.line_status` | text | [`line-status.md`](line-status.md) |

### 物流事件（关键！）

| 字段 | 类型 | 文件 |
| --- | --- | --- |
| `tracking_events.action_code` | int (TikTok) | [`action-code.md`](action-code.md) |
| 物流终态白名单 | int (项目自有) | [`logistics-terminal-codes.md`](logistics-terminal-codes.md) |
| Chrome ext `track_status` | text (自由) | [`track-status.md`](track-status.md) |

### 售后 / 取消

| 字段 | 类型 | 文件 |
| --- | --- | --- |
| `after_sales.cases.case_type` | text (内部定义) | [`case-type.md`](case-type.md) |
| `plugin.after_sales.cancel_type` | text (TikTok) | [`cancel-type.md`](cancel-type.md) |
| `plugin.after_sales.cancel_status` | text (TikTok) | [`cancel-status.md`](cancel-status.md) |
| `plugin.after_sales.reason` / `cancel_reason` | text (TikTok 自由) | [`cancel-reason.md`](cancel-reason.md) |
| `order/list reverse_module.reverse_type` | int (卖家中心) | [`reverse-type.md`](reverse-type.md) |
| `order/list reverse_module.reverse_status` | int (卖家中心) | [`reverse-status.md`](reverse-status.md) |

### 结算

| 字段 | 类型 | 文件 |
| --- | --- | --- |
| `plugin.settlement_details.settlement_status` | int / text | [`settlement-status.md`](settlement-status.md) |
| `plugin.settlements.payment_status` | int | [`payment-status.md`](payment-status.md) |
| `plugin.settlements.statement_type` | int | [`statement-type.md`](statement-type.md) |
| `plugin.settlements.payment_pending_reason` | int | [`payment-pending-reason.md`](payment-pending-reason.md) |
| `finance.settlement_components.component_code` | text (EAV 58 字段) | [`settlement-component-code.md`](settlement-component-code.md) |

### 商品 / 链接

| 字段 | 类型 | 文件 |
| --- | --- | --- |
| `commerce.products_spu.status` | text (TikTok) | [`product-status.md`](product-status.md) |
| `linkage.product_links.relation_type` | text (内部) | [`product-link-relation-type.md`](product-link-relation-type.md) |
| `linkage.link_overrides.decision` | text (内部) | [`link-override-decision.md`](link-override-decision.md) |
| `linkage.link_issues.issue_type` | text (内部) | [`link-issue-type.md`](link-issue-type.md) |

### 成本 / 采购

| 字段 | 类型 | 文件 |
| --- | --- | --- |
| `reporting.product_cost_snapshots.cost_method` | text (内部) | [`cost-method.md`](cost-method.md) |
| `procurement.procurement_products.product_type` | text (内部) | [`procurement-product-type.md`](procurement-product-type.md) |
| `procurement.procurement_products.status` / `accounts.status` | text (妙手 free-text) | [`procurement-products-status.md`](procurement-products-status.md) |

### 集成 / 同步 / 安全

| 字段 | 类型 | 文件 |
| --- | --- | --- |
| `integration.credentials.provider` | text (内部) | [`provider.md`](provider.md) |
| `commerce.shops.platform` | text (内部) | [`platform.md`](platform.md) |
| `integration.sync_jobs.status` | text (内部) | [`sync-job-status.md`](sync-job-status.md) |
| `plugin.plugin_logs.level` | text (CK) | [`plugin-log-level.md`](plugin-log-level.md) |
| `plugin.ad_raw_log.kind` | text (CK) | [`ad-raw-log-kind.md`](ad-raw-log-kind.md) |
| `security.api_keys.role` | text (内部) | [`api-key-role.md`](api-key-role.md) |
| `security.api_keys.status` | text (内部) | [`api-key-status.md`](api-key-status.md) |
| `plugin.intercept_configs.mode` | text (内部) | [`intercept-mode.md`](intercept-mode.md) |

### 通用（地理 / 货币）

| 字段 | 类型 | 文件 |
| --- | --- | --- |
| `commerce.shops.region` | text (ISO 3166-1) | [`region.md`](region.md) |
| ISO 4217 currency | text | [`currency.md`](currency.md) |

---

## 约定

详见 [`conventions.md`](conventions.md)。

---

## 当前覆盖

- **38 个枚举值空间**已沉淀（含本目录）
- **5 个**枚举仅观察到部分合法值（含 `cancel-status`、`reverse-type`、`reverse-status`、 `api-key-status`、 `procurement-products-status`）
- **3 个**枚举完全无样本（`statement-type`、`payment-pending-reason`、`line-status`）
