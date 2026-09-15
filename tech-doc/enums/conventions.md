# 约定

> 本目录所有枚举文件遵循的统一约定。

## 1. 字段命名映射

| 文档里的术语 | DB / API 里的实际字段 |
| --- | --- |
| "订单状态" | `commerce.sales_orders.status` (text) |
| "main_order_status" | `plugin.orders.main_order_status` (int) ← 卖家中心原始 int |
| "主订单状态" | 同上 |
| "履约类型" | `commerce.sales_orders.fulfillment_type` (text) 或 `plugin.orders.fulfillment_type` (int) |

> **重要**：同一个业务概念可能有 **两套编码**——一套是 API 原始 int（plugin 端按原样落库），另一套是 v2 标准化后的 text（commerce 端）。文档里必须**两个都说清楚**，禁止混用。

## 2. 来源标注

每个文件必标：

- **DB column**：`<schema>.<table>.<column>`，明确 schema 是 plugin/commerce/finance/...
- **类型**：`int` / `text` / `bool` / `bool+CK`
- **上游**：哪个 TikTok 端点 / 哪个 Chrome ext endpoint / 项目内部定义
- **实测样本**：从 `integration.raw_records` / `plugin.intercepted_requests` / `commerce.sales_orders` 取过的真实数据
- **代码锚点**：`tts_erp_v2/<file>.py:NNN` 形式
- **文档锚点**：`tech-doc/<file>.md` 形式

## 3. 状态码等级

每个枚举文件按以下等级标注**该值是否被项目固化**：

| 标记 | 含义 |
| --- | --- |
| ✅ **固化** | 在 `tts_erp_v2/db/constants.py` 或模型 `CheckConstraint` 中明确约束 |
| 🟡 **实测推断** | 来自生产数据交叉验证，但代码层未固化（`tech-doc/plugin-sourced-shop-analytics.md:106` "待与 Seller Center 页面显示核对后固化映射"） |
| 🔴 **未确定** | 仅有零星样本，未交叉验证，禁止拍脑袋 |

## 4. 文件命名

- 字段名直接做文件名：`order-status.md`（不带 `commerce_sales_orders_` 前缀）
- 一字一义：每个文件**一个**枚举值空间，不合并多个字段

## 5. 如何贡献新枚举

1. 在 `tts_erp_v2/db/models/` 找到对应字段
2. 查代码 + 上游 API 文档 + 实测样本，写取值表
3. 标 ✅/🟡/🔴 等级
4. 在 `README.md` 索引表加一行
5. 引用 `# 引用` 节列代码 + 文档锚点

## 6. 已知未固化的枚举（高优先级补全候选）

| 枚举 | 阻塞业务 | 来源 |
| --- | --- | --- |
| `plugin.orders.main_order_status` int 码 → text 状态 | 海外取消桶、ROI 计算 | `tech-doc/plugin-sourced-shop-analytics.md:106` |
| `plugin.orders.sku_display_status` | 同上 | 同上 |
| `plugin.orders.fulfillment_type` int | 履约口径 | 待补 |
| `plugin.settlements.statement_type` int | 结算类型 | 待补 |
| `plugin.settlements.payment_pending_reason` int | 付款挂起原因 | 待补 |
| `cancel_reason` 全集 | 取消原因统计 | `tech-doc/order-domain-business-rules.md §3` 只列了 `returned_to_shipper_other` 一类 |
| Chrome ext `track_status` 自由文本 → 终态 | 物流缺口判定 | `tech-doc/order-domain-business-rules.md §5` |
| `reverse_type` 全部枚举值（只见过 1/3/4） | 售后分类 | `tech-doc/plugin-sourced-shop-analytics.md:117` 标注 "枚举待核实" |
