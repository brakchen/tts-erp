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

### 3.1 文件级（粗略）

每个枚举文件按以下等级标注**该枚举的总体固化程度**：

| 标记 | 含义 |
| --- | --- |
| ✅ **固化** | 该枚举全部合法值在 `tts_erp_v2/db/constants.py` 或模型 `CheckConstraint` 中明确约束 |
| 🟡 **实测推断** | 来自生产数据交叉验证，但代码层未固化（`tech-doc/plugin-sourced-shop-analytics.md:106` "待与 Seller Center 页面显示核对后固化映射"） |
| 🔴 **未确定** | 仅有零星样本，未交叉验证，禁止拍脑袋 |

### 3.2 值级（必填，逐行标注）— **2026-09-15 补**

**每一个合法值在"## 取值"表内必须带状态标记**。表统一为 **首列"等级"**：

| 等级 | 含义 | 谁来标 |
| --- | --- | --- |
| ✅ | **已固化** | 在 `db/constants.py` / `CheckConstraint` / 上游官方枚举明确定义 |
| 🟡 | **实测** | 生产数据有样本，但**未在代码里固化**（含推断 / 文档只列 1 种但实际多） |
| 🔴 | **未观测** | 枚举值存在但本项目数据中**未观测到样本**——禁止拍脑袋假设 |

> **重要**：当一个枚举**部分**值是 ✅ 部分是 🟡/🔴 时（最常见情况），**必须逐行标**，不允许"整文件一刀切"。

#### 表头规范

```markdown
## 取值

| 等级 | 值/码 | 含义 | ... |
| :---: | --- | --- | --- |
| ✅ | `ACTIVATE` | 已上架 | ... |
| 🟡 | `RETURN_AND_REFUND` | 退货退款（**未在 after_sales.cases 落码**） | ... |
| 🔴 | `PARTIAL_RETURN` | 部分退货 | （**未观测**） |
```

### 3.3 顶部速查（未固化文件必填）— **2026-09-15 补**

**任何包含 🟡 或 🔴 值的枚举文件**，必须在 `## 取值` 之后、`## 已知 gap` 之前加：

```markdown
## ⚠️ 未固化值速查

- 🟡 `XYZ`（推断，n=3）— 上下文...
- 🔴 `ABC`（未观测）— 上游可能存在但本项目无样本
- 🔴 `DEF`（未观测）— ... 
```

**读者扫这一节就能秒判**"这枚举能不能直接信赖"——不必跳到 gap 段。

## 4. 文件命名

- 字段名直接做文件名：`order-status.md`（不带 `commerce_sales_orders_` 前缀）
- 一字一义：每个文件**一个**枚举值空间，不合并多个字段

## 5. 如何贡献新枚举

1. 在 `tts_erp_v2/db/models/` 找到对应字段
2. 查代码 + 上游 API 文档 + 实测样本，写取值表
3. 标 ✅/🟡/🔴 等级
4. 在 `README.md` 索引表加一行
5. 引用 `# 引用` 节列代码 + 文档锚点

## 6. 维护脚本

本目录含两个同步脚本，在 `scripts/` 下：

| 脚本 | 作用 | 何时跑 |
| --- | --- | --- |
| `scripts/annotate_enums.py` | 给"##/### 取值/实测样本/已知值/实测"段下未加 "等级" 列的表注入"等级"首列 | 新增枚举文件 / PER_FILE_STATUS dict 改值后 |
| `scripts/add_unfixed_summary.py` | 在每个取值/实测段末尾加 "## ⚠️ 未固化值速查" 段 | 新增枚举文件 / 取值表状态变动后 |

两者均支持 `--dry-run` 参数。

**PER_FILE_STATUS**（`scripts/annotate_enums.py` 顶部 dict）是逐值状态知识库——每个枚举文件的每个具体值的 ✅/🟡/🔴 都在这里登记。脚本严格不允许"猜"——遇到未登记的具体值会报错并退出。

## 7. 已知未固化的枚举（高优先级补全候选）

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
