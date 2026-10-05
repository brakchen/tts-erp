# SPU 价格统计（采购价 / 原价 / 实付价）专项技术方案

> **状态：待实现（文档组件；不是代码、迁移或发布批准）**
> 本文只定义 `/v2/pages/spu-roi` 与 `/v2/pages/focused-spus` 共用价格统计组件的实现契约。公共盈利业务真相仍由 [`../business/spu-profitability.md`](../business/spu-profitability.md) 与飞书《SPU-ROI 计算口径》拥有；公共深模块方案由 [`spu-profitability-technical-design.md`](spu-profitability-technical-design.md) 拥有。本专项不是第二套盈利口径，发布后应由维护者把本文件链接合并进公共技术方案。
>
> **外部依据声明：** 父会话提供飞书价格章节 readback revision `247`；本会话没有独立抓取外部 Feishu 页面，不把本地 revision `246` 当作“价格章节不存在”的证据。本文将已批准的价格范围与当前代码事实分开标注；若飞书后续修订，先修契约再实现。

## 0. 交付边界、快照与审查纪律

### 0.1 本波唯一交付

- 本波只新增 `docs/design/spu-price-statistics.md`，不改 Python、SQL、Alembic、模板、JavaScript、CSS、测试或其他业务文档。
- 目标是让数据、领域/API、UI、真实 E2E 和集成审查各有可执行的 seam；实现必须由后续 successor lane 按本文件实施。
- 不宣称测试、浏览器、迁移、回填或生产发布已经执行；不把 mock render/source grep 当行为证据。

### 0.2 TaskStartSnapshot（恢复运行）

| 项目 | 记录 |
| --- | --- |
| 主 worktree | `/home/schan/tts-erp`，恢复前干净，HEAD `75302751073b46ba22de28e0ffaebfcfb7d27e8c`，仅含本 lane 的 ACTIVE 注册提交 |
| `origin/master` | `ce3e9e665e1eeaa2c667b786bf541f27db4c5375`（恢复前 fresh fetch 核验） |
| 专属 worktree | `/home/schan/tts-erp/.worktrees/spu-price-stats-design` |
| 分支 / 起点 | `docs/spu-price-stats-design` / `ce3e9e665e1eeaa2c667b786bf541f27db4c5375` |
| owned path | `docs/design/spu-price-statistics.md` only；公共 profitability、UI、API、tests 文件不在本 lane ownership |
| 安全边界 | 不触碰生产数据库、生产迁移、真实回填、服务重启、凭据、数据导出；测试实现只能使用 test-shaped isolated DB |
| 验证策略 | 文档链接、路径、JSON 语法、SQL/Markdown 结构、`git diff --check`；文档-only 不跑应用测试 |
| 恢复证据 | 原 run 的 write toolCall 无 toolResult；恢复后先写入本 bounded section，再分段扩展，避免再次生成未落盘巨型写入 |

### 0.3 已批准结果（不可扩大）

1. 两个页面顶部各有一个 **Prices** summary box，位置紧跟现有 Projected box；三行依次为 Purchase、Original sale、Paid，左列 quantity-weighted mean、右列 quantity-weighted median。Purchase 采用当前 ROI effective CNY `unit_cost_used` 与 `cost_source`；`DEFAULT_K1=40 CNY` 必须显式显示为 estimated。
2. 主 SPU 表新增三组 grouped columns：Purchase、Original sale、Paid；每组 Mean / Median 独立服务端排序。两页必须继续使用同一 page kernel 与 profile adapter，不复制计算。
3. TikTok 价格 authority 只有 `line_items.original_price`（原价）和 `line_items.sale_price`（实付）；Miaoshou `originalPrice` / `discountedPrice` 只用于核验差异，永远不是缺字段回退或正式 observation。
4. population 是 selected shop + applied SPU scope + 当前 operating window 的已付款商品数量；排除 `UNPAID`、`ON_HOLD`、`CANCELLED`、赠品；后续退款/全损不擦除历史 paid price observation。禁止用 `payment.total_amount` 分摊，禁止计入运费。
5. 所有 native money 在同一个 read snapshot 的 FX snapshot 下先转换 CNY，再做均值/中位数；金额 wire 四位小数数字字符串，无样本为 `null`。Totals 必须从全 scope 原始 observations 重聚合，不受 `q`、排序、分页或可见列影响。

### 0.4 当前事实 vs 目标差异（必须保持可见）

| 位置 | 当前可核验事实（基线） | 目标改造 |
| --- | --- | --- |
| `tts_erp_v2/jobs/tiktok/orders.py::_parse_line_payload` | 读取 TikTok `sale_price` 为现有 `unit_price`；若 line 缺 `quantity`，当前 parser 按已记录的“每行一件”证据默认 `Decimal(1)`；目前未归一化 `original_price` | 保留旧字段兼容，同时持久化 authority 字段、来源状态、数量/礼品/币种证据与可回填 provenance |
| `tts_erp_v2/jobs/tiktok/order_detail.py` | detail job 复用 line parser/upsert 路径，但仍需与 orders producer 逐字段 parity 验证 | 两个 producer 必须产生完全相同的价格 observation 规则，不得一条路径漏原价或状态 |
| `tts_erp_v2/db/models/plugin.py` | `plugin.order_details` 有 `origin_sale_price`（订单级 detail price module），不能当作 `line_items.original_price` | 新统计不可把订单级 origin 或 `payment.total_amount` 冒充 line observation |
| `tts_erp_v2/analytics/spu_profitability` | 当前 row 有 `unit_cost_used` 与 `cost_source`，缺失成本会使用 `DEFAULT_K1`；价格六指标尚不存在 | 增加窄 typed seam，保留既有利润公式与估算语义 |
| `tts_erp_v2/static/js/spu-profitability-page.js` | 当前表列定义是平面 `COLUMN_DEFS`，共用 Tabulator kernel；summary 已有 Projected 区 | 采用 Tabulator 6.3.1 nested `columns` grouped header（实现必须用浏览器验证），而非第二张表或全局 toggle |
| `/v2/pages/focused-spus` | focused membership 管理 endpoint 与 ROI analytics endpoint 分离；页面通过同一 analytics `/v2/analytics/spu-roi`，focused 请求带 `scope=focused` | 不向 focused membership GET 添加价格字段；价格只在 analytics response 返回 |

未知的 upstream gift signal、数量域和 currency provenance 仍必须由窄 READ ONLY raw-payload probe / 上游契约核验；line eligibility 使用已批准的父订单 paid whitelist，保留 raw line status，不另造 line-paid enum。在核验前不能声称 gift/currency coverage 完整。

### 0.5 证据对齐表（当前 readiness gate）

以下是本轮 bounded source review 的**事实边界**，不是把目标设计误写成已验证行为：

| 主题 | 当前证据与引用 | 状态 / 对实现的约束 |
| --- | --- | --- |
| paid source | parent review 的 production readback（2026-10-05 11:52 UTC；2176 TikTok orders / 2231 item keys；2230 与 Miaoshou 匹配；paid 2221 exact + 9 ≤0.5 VND）核验了 TikTok `line_items[].sale_price`；维护 parser 也将 `sale_price`（plain 或 dict amount/currency）写入 `unit_price`：`tts_erp_v2/jobs/tiktok/orders.py:244-251`，detail 复用 `_parse_order_payload`、`_parse_line_payload`、`_store_raw`：`tts_erp_v2/jobs/tiktok/order_detail.py:1-25,180-205` | parent-verified production observation，**not independently re-fetched by this lane**；Feishu business revision `247` 已批准 mapping。object/scalar 与 line/parent currency provenance 仍按窄 gate 记录，不能把 synthetic fixture 当唯一官方 contract |
| original sale | 同一 parent production readback 在 2230/2230 matched items 中 `original_price` equal；证据来自 parent review readback（2026-10-05 11:52 UTC），本 lane 未独立抓取 | parent-verified production observation，**not independently re-fetched by this lane**；Feishu revision `247` 批准 `line_items[].original_price` authority。仍需在新 observation parser 中保留 raw presence/status，不得从 `origin_sale_price`、Miaoshou 或 payment 回填 |
| quantity | parser 对缺字段当前默认 `Decimal(1)`，维护者注释记录 2026-09-06 观察 0/3686 lines 有 quantity、历史行一件并与 payment reconciliation；代码/测试证据：`orders.py:257-271`、synthetic tests `tests/jobs_tiktok/test_orders_job.py:55-100,145-190` | one-piece default **PARTIAL**；positive fractional upstream supply **UNKNOWN**。统计契约仍明确拒绝 fractional/zero/negative/nonfinite/bool 并记 diagnostic line count；不声称上游没有 fraction，也不以 universal-negative 证据阻塞整个功能。|
| gift | bounded parser 没有 gift/not-gift mapping；zero、title、Miaoshou flag 都不是证据 | gift semantics **UNKNOWN**；`UNKNOWN` 必须保留并阻断正式 coverage，不能以字段缺失推断 `NOT_GIFT` |
| payment/status | 已批准的父订单 paid whitelist 是 `tts_erp_v2/db/constants.py::PAID_SALES_ORDER_STATUSES` 与业务文档 §4.1；parser 仅原样保留 `display_status`/`line_status`，不建立独立 line-paid enum | parent-order paid eligibility **VERIFIED**；line status 仍 raw-preserved。不得把 line status 当独立 paid whitelist；只有 source proof 支持时才增加实际 canceled-line override |
| currency | parser 支持 line price currency 与已解析 parent/raw currency fallback，但 direct source provenance 尚未由 official payload contract 完成闭合 | currency **PARTIAL**；plain numeric sale price 只有经证实同源 parent currency 才能转换，否则 missing/invalid；不能猜 CNY |
| population/refund | parent paid-order whitelist 已由 `PAID_SALES_ORDER_STATUSES`/业务 §4.1 固定；bounded source 仍未证明 gift absence semantics、currency provenance 或 refund/full-loss fixture linkage | 业务批准的 parent-paid/history-retention 目标保留；gift/currency 仍需窄 READ ONLY probe，refund 以显式 acceptance test 证明，不制造 coverage counter。 |
| cost | `resolve_unit_cost` 读当前 `manual_product_costs(valid_to IS NULL, MANUAL_ENTRY)`：`tts_erp_v2/reporting/cost_snapshots.py:36-75`；`ProductCostSnapshot` 的真实字段/唯一性见 `cost_snapshots.py:94-127`, `db/models/reporting.py:41-80` | 不发明 `snapshotId`；实现只能使用当前 effective manual cost 或真实已存在 snapshot provenance |
| live auth/process | `TTS_ERP_AUTH_MODE` 在 `app.py:362`/middleware 读取且默认 off；cookie 为 `tts_erp_session`：`middleware/session_auth.py:20-30`；Argon2id password helper：`accounts/passwords.py:1-65`；APScheduler 是独立 sync-worker 进程，bounded app scan 未找到 scheduler-disable flag | E2E 必须显式 enforce、真实 TEST login、同一 isolated DB；不得继承生产 env、发明 disable switch 或启动 sync-worker |

`tests/jobs_tiktok/test_orders_job.py` 的 fixture 注释明确是 “realistic-ish” synthetic data；它只能验证 parser regression，不能关闭 gift/absence、quantity-domain、currency provenance 等上表 gates。原价/实付的 parent-verified readback 是 inherited evidence，不是本 lane 的新抓取。最小安全验证输入仍是：经授权取得并脱敏的 direct `/order/202309` raw captures（含 gift/absence semantics、quantity types、currency、display_status），加现有 paid-order mapping；本 lane 不抓取外部资料、不读 secrets、不启动服务、不操作 DB。

## 1. 权威边界、非目标与不可变规则

### 1.1 Authority 与名词

| 概念 | 本专项定义 | 禁止替代 |
| --- | --- | --- |
| `purchase` | 当前 ROI 对每个选中 SPU 解析出的 CNY `unit_cost_used`；`cost_source=MANUAL` 为人工成本，`DEFAULT_K1` 为 40 CNY 估算 | `miaoshou.purchase_prices`、历史采购单分布、订单支付金额 |
| `originalSale` | TikTok 同一 `line_items[]` 元素的 `original_price` 单价 observation | `plugin.order_details.origin_sale_price`、Miaoshou `originalPrice` |
| `paid` | TikTok 同一 `line_items[]` 元素的 `sale_price` 单价 observation | `payment.total_amount`、订单级 GMV 分摊、Miaoshou `discountedPrice` |
| unit / line / order | quantity 是 unit；一条 `sales_order_lines` 是 line；父 `sales_orders` 是 order | 用订单数替代件数、把行均值再平均 |
| operating window | 已付款行按父订单的现有页面窗口归属规则过滤；窗口只由页面当前日期控制 | 预测窗口、结算日、售后完成日 |
| price observation | 同一 `(shop_pk, order_pk, external_line_id)` 的 authority 字段、原始币种、数量、来源版本和 status 的可追溯快照 | 从其它供应商、缺字段猜测、回填实时推断 |

业务事实（付款状态、退款/全损不抹除历史 paid、店铺时区）引用 `docs/business/spu-profitability.md`；本文件只规定存储、解析、聚合、wire 和交互。

### 1.2 明确非目标

- 不改变任何既有利润、ROI、退款、全损、费率、成本 fallback 或 projection 公式；价格模块是并列只读输出。
- 不建立 Miaoshou 价格回退链，不把 Miaoshou 价格写成 TikTok authority，不按订单总支付额分摊价格，不把 shipping fee 纳入任何 observation。
- 不把搜索、排序、分页、Tabulator visible columns、projection toggle 或 page profile 改成 totals 的过滤器；只有 shop、已应用 SPU selection、operating window 和 eligibility 进入 population。
- 不承诺历史原价在 TikTok 原始记录不存在时可重建；回填只能使用匹配的原始 TikTok payload，缺失就记录缺失。
- 不在 agent lane 执行 Alembic、backfill、service restart、scheduler、生产 dry-run 或生产数据导出；发布由人工执行并保留观测窗口。

### 1.3 钱、零值与状态硬规则

1. **显式 `0` 是有效 money observation**：若 TikTok 字段存在、能解析为有限非负 Decimal，`0` 进入数量加权分母；不因 falsy 而变成缺失。
2. **缺失不是 0**：字段 absent、`null`、空字符串或无法证明为 authority 字段时不进均值/中位数，增加该 metric 的 `missingQuantity/missingLineCount`。
3. **非法不是缺失**：负数、非有限/不可解析、币种无法确认、单位不符合数量规则分别记 invalid status 与 invalid counts；不得静默转 0。
4. **partial 不降级为 complete**：单个 metric 的 valid quantity 覆盖率不足 1 时为 `partial`；purchase 的 `DEFAULT_K1` 即使有样本仍 `estimated=true`。
5. 每次 response 在一个 read-only `REPEATABLE READ` snapshot 中固定 `calculatedAt`、FX snapshot、有效成本 snapshot 和 population；同一响应三种 metric 不可混用不同时间点。

## 2. 持久化模型与迁移契约

### 2.1 采用独立 immutable observation 表

不要把新价格字段塞进现有 `unit_price` 以改变旧语义；新增表 `commerce.sales_order_line_price_observations` 是唯一规范化价格 observation owner。现有 raw history 只保存原始 payload，`sales_order_lines` 只有可变当前行（且 `raw_record_id` 会随重采覆盖），两者都不能提供可查询的版本化字段状态，因此“给 line 加两个字段”仍会丢失 valid→missing/invalid 版本审计；新增一张 observation 表是最小能满足版本、状态、去重和 provenance 的方案。旧 `unit_price`、`plugin.order_details.origin_sale_price` 和 Miaoshou 字段均不是第二个 authority，价格聚合只读本表/当前成本 map。正式 migration revision **由实施 lane 根据当前 Alembic head 生成，本文不猜编号**；migration 文件必须 additive，先建 enum/check/index，再让 producer/API 能读空表。

建议字段（SQLAlchemy 类型是约束，不是可直接执行的 migration）：

| 字段 | 类型 / nullability | 语义 |
| --- | --- | --- |
| `id` | bigint identity PK | observation 内部标识 |
| `shop_pk` | bigint NOT NULL | 站内 shop FK；与 line/order 同店 |
| `order_pk` | bigint NOT NULL | 父销售订单 FK |
| `external_line_id` | text NOT NULL | TikTok `line_items[].line_id`/`id` 归一化后的业务键 |
| `raw_record_id` | bigint NOT NULL | 首次保留的 `integration.raw_records.id`；不可用时 producer 失败，不造 provenance；后续重复 capture 不替换它 |
| `source_endpoint` | enum `ORDER_SEARCH`/`ORDER_DETAIL` NOT NULL | 首次保留 capture 的 TikTok producer 来源；重复 endpoint 不产生第二 observation |
| `source_payload_hash` | text NOT NULL | canonical raw line hash，用于重复 payload 审计 |
| `semantic_observation_hash` | char(64) NOT NULL | SHA-256 identity；包含 line 字段 presence/type/normalized values 与继承的 parent status/currency/version，不含 raw id、capture time、endpoint |
| `source_order_version_at` | timestamptz NULL | 父订单 `order_modify_time` / source version；缺失时仍可保留 null observation provenance，但 producer 必须在同一事务提交既有 SyncIssue `MISSING_SOURCE_VERSION`；不能凭空当作新版本 |

| `source_captured_at` | timestamptz NOT NULL | raw record capture time |
| `spu_pk` | bigint NULL | producer 当时解析到的商品；未命中不把行伪造到其它 SPU |
| `raw_quantity` | numeric(20,8) NULL | authority payload quantity 原值（不把默认 1 写回 raw） |
| `effective_quantity` | numeric(20,8) NULL | 通过 check 的统计件数；当前验证的缺 quantity fallback 为 1 |
| `quantity_status` | enum NOT NULL | `OBSERVED` / `DEFAULT_ONE_PER_LINE` / `MISSING` / `INVALID_ZERO` / `INVALID_NEGATIVE` / `INVALID_NON_INTEGER` / `INVALID_NON_NUMERIC` |
| `line_status_raw` | text NULL | 原始 line status，不将未知值改为 paid |
| `parent_payment_status` | enum NOT NULL | Derived only from parent `PAID_SALES_ORDER_STATUSES` plus explicit parent status values; this is not an independent line-paid whitelist. `line_status_raw` remains the raw line field. |
| `gift_status` | enum NOT NULL | `NOT_GIFT` / `GIFT` / `UNKNOWN` |
| `original_price_native` | numeric(28,10) NULL | TikTok `original_price` 数值；只有 authority 字段可填 |
| `paid_price_native` | numeric(28,10) NULL | TikTok `sale_price` 数值；只有 authority 字段可填 |
| `currency` | text NULL | line price 的明确币种；父币种仅在已验证同币种 contract 时使用 |
| `original_price_status` / `paid_price_status` | enum NOT NULL | 每字段 `OBSERVED` / `MISSING` / `INVALID_NEGATIVE` / `INVALID_NON_NUMERIC` / `INVALID_CURRENCY` |
| `created_at` / `updated_at` | timestamptz NOT NULL | local audit timestamps |

约束与索引：

```sql
-- 逻辑约束；实际 revision 由实施者命名，不能复制为未经校验的版本号
UNIQUE (shop_pk, order_pk, external_line_id, semantic_observation_hash);
CHECK (effective_quantity IS NULL OR effective_quantity > 0);
CHECK (original_price_native IS NULL OR original_price_native >= 0);
CHECK (paid_price_native IS NULL OR paid_price_native >= 0);
CHECK (source_endpoint IN ('ORDER_SEARCH', 'ORDER_DETAIL'));
CREATE INDEX ix_solpo_line_version
  ON commerce.sales_order_line_price_observations
    (shop_pk, order_pk, external_line_id, source_order_version_at DESC,
     source_captured_at DESC, semantic_observation_hash ASC);
CREATE INDEX ix_solpo_spu_capture
  ON commerce.sales_order_line_price_observations
    (shop_pk, spu_pk, source_captured_at);
```

`0` 通过上述 check；`NULL` 只表达 absent/invalid 后的无可用数值，不是 0。若数据库 enum 不易向后扩展，使用受约束 text + application enum，但 wire enum 必须稳定并有 migration test。

### 2.2 Observation selection 与新旧竞态

- `semantic_observation_hash` 的输入是 canonical JSON：每个 authority/raw 字段都保留 presence（absent/null/value）、JSON type、规范化 Decimal/text 值；再加入父订单 `shop_pk`、`order_pk`、`external_line_id`、继承的 payment status、currency 和 source version。它不加入 `raw_record_id`、capture time 或 endpoint，因此 `_store_raw` 每次重试产生新 capture ID 也不会产生第二 observation。
- 唯一键 `(shop_pk, order_pk, external_line_id, semantic_observation_hash)` 冲突时保留首次 capture 的 `raw_record_id/source_endpoint/source_captured_at`，后续 raw id 只在 raw history 保留，并写 `DUPLICATE_SEMANTIC_OBSERVATION` 计数；不建立第二个 provenance bridge，也不更新首次来源。checkpoint 重放同样只能 no-op。
- 同一 line 的 canonical **整条最新 observation** 按 `(source_order_version_at NULLS LAST, source_captured_at, semantic_observation_hash ASC)` 选最新；`ORDER_DETAIL` 与 `ORDER_SEARCH` 不因 endpoint 名称互相覆盖。相同 version 以较新 capture 胜出；version 与 capture 都相同则 hash 的确定性升序胜出，并记录 `EQUAL_VERSION_CONFLICT`。旧事务晚到时可以插入审计行，但不得成为 canonical。
- `source_order_version_at IS NULL` 不等于一个新的 source version：producer 必须用既有 SyncIssue owner 写 `MISSING_SOURCE_VERSION`，确定性关联 `(shop_pk, order_pk, external_line_id, source_payload_hash)`，同一 issue key 去重、可重试，拿到非 null source version 后 resolve；不得新增 mirror boolean、独立 issue table 或静默把 null 排在新版本之前。
- 最新 authority observation 的每个 price field status/value 都是该 metric 的唯一权威：新 payload `MISSING`/`INVALID` 时该 metric 为 null/not observed；旧 valid value 只留在 audit history，**不**做 last-known-valid 或 stale-price fallback。一个 valid→missing 两版本 fixture 只能贡献一次 missing line/valid quantity，不能同时贡献旧 observed 与新 missing。
- quantity、payment status、gift status 是 eligibility 状态而非可回退金额：较新的明确 `CANCELLED`/`GIFT`/invalid quantity 必须使该 observation 不合格；未知状态不能自动继承为 paid/not-gift。退款、全损和售后同步不更新 price observation。
- producer 需用数据库 `INSERT ... ON CONFLICT ... DO NOTHING` + canonical selection CTE，或同等事务锁/compare-and-set 保护 `(shop_pk, order_pk, external_line_id)`；sync version/token 不匹配时 rollback 当前 line 写入并记录 issue，不能部分提交订单。

### 2.3 礼品、状态、数量与币种证据门

- `gift_status=GIFT` 只有通过 TikTok 原始 payload 中已核验的 gift flag/赠品 line 结构或项目批准的明确映射才能设置；不要把 `price=0`、Miaoshou gift 或 SKU 名称猜成 gift。
- `gift_status=UNKNOWN` 默认不进入正式 statistics，`unknownGiftQuantity/unknownGiftLineCount` 单独暴露并产生 warning；实现前必须补一个真实 raw fixture 与映射审查。若产品要把 unknown 纳入，必须先更新本文件而不是在代码中默选。
- payment eligibility 复用已批准的父订单 `PAID_SALES_ORDER_STATUSES`（`tts_erp_v2/db/constants.py` 与业务 §4.1）；不建立独立 line-paid whitelist。`line_status_raw` 始终保留，只有 source proof 支持时才增加实际 canceled-line override；父订单不在 paid whitelist 时按 mutually-exclusive `UNPAID`/`ON_HOLD`/`CANCELLED`/`UNKNOWN_STATUS` 分类，均不得 silently include，并记录对应 coverage。
- 价格统计契约只接受有限正整数 physical units；0、负数、非有限、非数字、bool、fractional quantity 均拒绝，按 `INVALID_ZERO`/`INVALID_NEGATIVE`/`INVALID_NON_NUMERIC`/`INVALID_NON_INTEGER` 记录 **line count**，不制造物理件数。该规则是批准的统计算术边界，不声称上游永远不会发送 fraction，也不因等待一个 universal-negative 证据而阻塞整个设计；若 raw capture 发现 fraction，保留 raw、发诊断并按 invalid 排除。缺 quantity 可沿用维护者记录的每行一件语义（2026-09-06 观察 0/3686 lines 有 quantity），写 `DEFAULT_ONE_PER_LINE` 与 evidence/version；未来若显式 quantity 或 reconciliation 违反该语义，必须发 contradiction issue 并停该 line，不能 silent infer。
- currency 必须来自 line price 的明确字段；plain numeric `sale_price` 的 currency 只能使用已验证且与该 line 同源的 order currency，否则 `INVALID_CURRENCY`。未知 FX、缺 currency、未找到同一 snapshot rate 均不转换、不聚合，绝不猜 CNY。
- 本专项不使用 `plugin.order_details.origin_sale_price`、Miaoshou price、`payment.total_amount` 或 shipping 作为补偿来源。Miaoshou 仅可在核验报告中并排比较，不落为正式 observation。

## 3. Producer、解析、upsert 与安全回填

### 3.1 两条 TikTok producer 必须 parity

`tts_erp_v2/jobs/tiktok/orders.py` 的 search producer 与 `tts_erp_v2/jobs/tiktok/order_detail.py` 的 detail producer 必须调用同一个 typed line normalizer（建议放在订单 intake 共享模块；不在两个 job 各复制一套 `_parse_line_payload`）。normalizer 接收 raw line、父订单 status/currency/version、`shop_pk/order_pk/raw_record_id`，输出完整 `PriceLineObservation`，包括 raw/effective quantity、原价/实付各自 status、gift/status/currency provenance 和 hash。

Parity 验收必须逐字段比较相同 fixture 的两个入口：

- line id/product id/SKU、原价、实付、currency、quantity、gift/status、source version、SPU resolution 结果一致；只有 `source_endpoint` 与 raw record provenance 可以不同。
- maintained parser currently accepts plain numeric and dict `sale_price` shapes (`orders.py:244-251`), but upstream shape/currency remains partial; implementation must add evidence before calling either shape an official contract. 原价必须从同一 `line_items[]` raw element 的 `original_price` 读取，不能从 payment 或 order detail price module 拼装。
- 缺 `quantity` 的现有默认 1 逻辑来自维护者观察注释（`orders.py:257-271`），只能在 official/raw evidence 复核后保留；它必须在 status 与 metrics 中可见，不能伪装成 observed quantity。现有 object `sale_price` tests 是 synthetic regression fixtures，不是 upstream contract。
- line parser 错误按 line issue 记录并继续其它 lines；缺少 order id/line id/raw record provenance 是 producer 级失败，不能写一条无来源的统计行。

### 3.2 严格 idempotent upsert 流程

1. 在同一同步事务中先持久化/定位 `integration.raw_records`，取得本次新的 `raw_record_id` 与 capture time；随后读取父 `sales_orders` 的 `order_pk`、`shop_pk`、已付款状态（复用 `PAID_SALES_ORDER_STATUSES`）、currency 与 source version。验证 line external id 属于该 parent；不得用另一个店的同名 id。source version 缺失时照常保留 raw-linked observation provenance，但同事务 upsert 既有 SyncIssue `MISSING_SOURCE_VERSION`，按 `(shop_pk, order_pk, external_line_id, source_payload_hash)` 去重并允许 retry/resolve。
2. 以 §2.2 的 canonical JSON 生成 `semantic_observation_hash`，包含字段 presence/type/normalized values 与继承父字段，但不含 raw id、capture time、endpoint；不同 raw capture 的同一语义必然相同 hash。
3. 对每个 line 执行唯一键 `(shop_pk, order_pk, external_line_id, semantic_observation_hash)` 的 `INSERT ... ON CONFLICT DO NOTHING`。首次成功的 capture 固定为 observation provenance；冲突 capture 只保留 raw history 与 duplicate metric，绝不更新首次 raw id 或增加数量。
4. canonical 读取使用具体 window CTE（见 §4.2）按 version/capture/hash 选择一条；同一 line 的较旧 replay 可插入不同语义审计行，但不能覆盖 canonical。相同 version/capture 的 hash 冲突按 hash ASC，记录 `EQUAL_VERSION_CONFLICT`。
5. 一个订单所有 line 的 observation 成功后再 resolve 对应 sync issue；任一必须字段或事务冲突失败则回滚该订单的 observation 写入，保留 raw record 与 issue 供重试。
6. 重试、checkpoint replay、orders/detail 双 producer 重复采集都必须只产生 no-op 或审计行；不得以重试次数、capture 时间或 Miaoshou 数据制造新价格。不同语义的较新 observation 才能改变 canonical 值，较新 missing/invalid 直接使对应 metric null。

可观测 issue/status 至少包括：`PARSE_ERROR`、`MISSING_AUTHORITY_PRICE`、`INVALID_PRICE`、`MISSING_CURRENCY`、`INVALID_QUANTITY`、`UNKNOWN_GIFT_SIGNAL`、`UNKNOWN_LINE_STATUS`、`MISSING_SOURCE_VERSION`、`STALE_OBSERVATION_SKIPPED`、`FX_UNAVAILABLE`、`UPSERT_CONFLICT`。`MISSING_SOURCE_VERSION` 使用既有 SyncIssue owner 与上述 deterministic key 去重/retry/resolve，不新建镜像表；每类要有 raw/order/line/shop 维度计数，API 只暴露聚合后的安全计数，不暴露凭据或完整 buyer payload。

### 3.3 Backfill 设计（仅人工 guarded deploy）

目标是为旧 raw TikTok records 补齐观察，不从 Miaoshou 或实时页面推断。backfill CLI/job 需有 `--dry-run` 默认模式、显式人工确认与 production destructive/script guard；agent 不执行真实 backfill。

**匹配键与来源：** 按 `shop_pk` 精确限定，使用 raw record 的 endpoint/order id 与已存在的 `sales_orders.id`/`order_pk`、`external_line_id` 精确匹配；raw record、order、line 不同 shop 或 external id 不一致时 `stale_or_unmatched`，不猜。每条回填记录保留 `raw_record_id`、payload hash、原始 capture/version 与 backfill run id。

**批次与恢复：** 先按 raw record id 升序分页（keyset，不用 offset），每批短事务；dry-run 输出 eligible/missing/invalid/gift/status/fx/unmatched/stale 数量与 sample ids；real mode 每批 commit 后 checkpoint `(shop_pk, raw_record_id)`。同一 checkpoint 重跑必须幂等，进程中止可从最后成功批次恢复，禁止整库 DELETE/TRUNCATE。

**并发与新鲜度：** backfill 写入前比较 raw version/capture 与同步 worker 当前 canonical version；线上更新较新时 backfill stale-skip，不覆盖新 producer 写入。使用语义唯一约束 + canonical selection CTE，冲突捕获后 rollback 当前 batch、指数退避重试有限次数；持续失败进入 `BACKFILL_RETRY_EXHAUSTED`，不吞错。每批记录 `scanned/inserted/duplicate_noop/stale_skip/unmatched/invalid/unknown_gift/fx_missing/retried/failed`，完成前必须有 reconciliation report；duplicate raw captures 和 checkpoint replay 的数量必须保持不变。

**缺口与停止门：** 任一 shop 的 raw coverage、unknown gift、unknown status、currency/FX 缺口未解释时，backfill 状态为 `INCOMPLETE`，不可宣布完成。只允许 additive schema/observation insert；不删除旧 raw、不改原 line 价格、不把回填值标作实时 observation。生产部署、API/sync-worker restart、回填确认和 rollback 均 human-only guarded。

## 4. 领域模块与 SQL 聚合

### 4.1 窄 seam 与调用流程

公共 `read_overview()` / `SpuProfitability` 仍是页面入口；新增独立 typed seam，例如：

```python
@dataclass(frozen=True)
class PriceStatsRequest:
    shop_pk: int
    selection: SpuSelection
    window: OperatingWindow
    sort: PriceSort | None

@dataclass(frozen=True)
class PriceMetric:
    mean_cny: Decimal | None
    median_cny: Decimal | None
    eligible_quantity: int
    observed_quantity: int
    missing_quantity: int
    invalid_price_quantity: int
    status: PriceCoverageStatus
    source: PriceSource
    estimated: bool
```

`read_overview()` 在同一个 snapshot 创建 `PriceStatsBasis`（calculated_at、FX snapshot、当前 effective cost map、内部 scope selection），调用窄 `read_price_stats(session, basis, request)`，再由 HTTP adapter 将结果合入旧 envelope。价格 seam 不 import HTTP request、模板 DOM 或私有大 SQL function；利润 formula 不反向依赖价格 fields。`focused-spus` 只改变 `SpuSelection=FocusedSelection`，不复制统计。

流程顺序：解析 shop/selection/window → 在事务内锁定 read-only `REPEATABLE READ` → push down shop/SPU/window/payment/status/gift predicates → materialize eligible line relation → 读取同一 FX/cost basis → CNY conversion → per-SPU and full-scope aggregation → apply independent sort/page only to items → serialize totals from unpaginated relation。

### 4.2 Eligibility relation 与 source selection

逻辑 CTE（名称与列依赖必须保持具体；不得实现成不存在的 function/view）：

```sql
WITH ranked_price_observations AS (
  SELECT p.*,
         row_number() OVER (
           PARTITION BY p.shop_pk, p.order_pk, p.external_line_id
           ORDER BY p.source_order_version_at DESC NULLS LAST,
                    p.source_captured_at DESC,
                    p.semantic_observation_hash ASC
         ) AS canonical_rank
  FROM commerce.sales_order_line_price_observations AS p
), canonical_price_observations AS (
  SELECT *
  FROM ranked_price_observations
  WHERE canonical_rank = 1
), cost_basis(spu_pk, unit_cost_used, cost_source) AS (
  -- These arrays are materialized from the existing current ROI effective-cost
  -- map in the same read snapshot; no historical cost is persisted here.
  SELECT u.spu_pk, u.unit_cost_used, u.cost_source
  FROM unnest(
    CAST(:cost_spu_pks AS bigint[]),
    CAST(:cost_unit_costs_cny AS numeric[]),
    CAST(:cost_sources AS text[])
  ) AS u(spu_pk, unit_cost_used, cost_source)
), selected_lines AS (
  SELECT o.shop_pk, l.order_pk, l.external_line_id, l.spu_pk,
         o.order_time, o.paid_at, o.status AS order_status
  FROM commerce.sales_order_lines AS l
  JOIN commerce.sales_orders AS o ON o.id = l.order_pk
  WHERE o.shop_pk = :shop_pk
    AND l.spu_pk = ANY(:selected_spu_pks)
    AND COALESCE(o.order_time, o.paid_at) >= :window_start_utc
    AND COALESCE(o.order_time, o.paid_at) < :window_end_exclusive_utc
), population_lines AS (
  SELECT s.*,
         p.id AS observation_id,
         p.effective_quantity, p.original_price_native, p.paid_price_native,
         p.original_price_status, p.paid_price_status, p.currency,
         p.gift_status, p.quantity_status, p.line_status_raw,
         c.unit_cost_used, c.cost_source,
         CASE
           WHEN p.id IS NULL THEN 'MISSING_OBSERVATION'
           WHEN p.quantity_status NOT IN ('OBSERVED','DEFAULT_ONE_PER_LINE')
             OR p.effective_quantity IS NULL OR p.effective_quantity <= 0
             OR p.effective_quantity <> trunc(p.effective_quantity)
             THEN 'INVALID_QUANTITY'
           WHEN s.order_status = 'CANCELLED' THEN 'EXCLUDED_CANCELLED'
           WHEN s.order_status = 'ON_HOLD' THEN 'EXCLUDED_ON_HOLD'
           WHEN s.order_status = 'UNPAID' THEN 'EXCLUDED_UNPAID'
           WHEN s.order_status NOT IN ('AWAITING_SHIPMENT','PARTIAL_SHIPPING',
                                       'AWAITING_COLLECTION','IN_TRANSIT',
                                       'DELIVERED','COMPLETED')
             THEN 'UNKNOWN_STATUS'
           WHEN p.gift_status IS NULL OR p.gift_status = 'UNKNOWN'
             THEN 'UNKNOWN_GIFT'
           WHEN p.gift_status = 'GIFT' THEN 'EXCLUDED_GIFT'
           ELSE 'ELIGIBLE'
         END AS exclusion_reason,
         CASE
           WHEN p.quantity_status IN ('OBSERVED','DEFAULT_ONE_PER_LINE')
            AND p.effective_quantity IS NOT NULL AND p.effective_quantity > 0
            AND p.effective_quantity = trunc(p.effective_quantity)
           THEN p.effective_quantity::bigint
         END AS known_valid_quantity
  FROM selected_lines AS s
  LEFT JOIN canonical_price_observations AS p
    ON p.shop_pk = s.shop_pk
   AND p.order_pk = s.order_pk
   AND p.external_line_id = s.external_line_id
  LEFT JOIN cost_basis AS c ON c.spu_pk = s.spu_pk
), coverage_by_reason AS (
  SELECT
    COUNT(*) AS selected_line_count,
    COUNT(*) FILTER (WHERE exclusion_reason = 'ELIGIBLE') AS eligible_line_count,
    COUNT(*) FILTER (WHERE exclusion_reason = 'EXCLUDED_UNPAID') AS excluded_unpaid_line_count,
    COUNT(*) FILTER (WHERE exclusion_reason = 'EXCLUDED_ON_HOLD') AS excluded_on_hold_line_count,
    COUNT(*) FILTER (WHERE exclusion_reason = 'EXCLUDED_CANCELLED') AS excluded_cancelled_line_count,
    COUNT(*) FILTER (WHERE exclusion_reason = 'EXCLUDED_GIFT') AS excluded_gift_line_count,
    COUNT(*) FILTER (WHERE exclusion_reason = 'UNKNOWN_GIFT') AS unknown_gift_line_count,
    COUNT(*) FILTER (WHERE exclusion_reason = 'UNKNOWN_STATUS') AS unknown_status_line_count,
    COALESCE(SUM(known_valid_quantity) FILTER (WHERE exclusion_reason = 'ELIGIBLE'), 0) AS eligible_quantity,
    COALESCE(SUM(known_valid_quantity) FILTER (WHERE exclusion_reason = 'EXCLUDED_UNPAID'), 0) AS excluded_unpaid_quantity,
    COALESCE(SUM(known_valid_quantity) FILTER (WHERE exclusion_reason = 'EXCLUDED_ON_HOLD'), 0) AS excluded_on_hold_quantity,
    COALESCE(SUM(known_valid_quantity) FILTER (WHERE exclusion_reason = 'EXCLUDED_CANCELLED'), 0) AS excluded_cancelled_quantity,
    COALESCE(SUM(known_valid_quantity) FILTER (WHERE exclusion_reason = 'EXCLUDED_GIFT'), 0) AS excluded_gift_quantity,
    COALESCE(SUM(known_valid_quantity) FILTER (WHERE exclusion_reason = 'UNKNOWN_GIFT'), 0) AS unknown_gift_quantity,
    COALESCE(SUM(known_valid_quantity) FILTER (WHERE exclusion_reason = 'UNKNOWN_STATUS'), 0) AS unknown_status_quantity,
    COUNT(*) FILTER (WHERE exclusion_reason = 'INVALID_QUANTITY') AS invalid_quantity_line_count,
    COUNT(*) FILTER (WHERE exclusion_reason = 'MISSING_OBSERVATION') AS missing_observation_line_count,
    COALESCE(SUM(known_valid_quantity) FILTER (WHERE exclusion_reason NOT IN ('ELIGIBLE','MISSING_OBSERVATION','INVALID_QUANTITY')), 0) AS excluded_valid_quantity
  FROM population_lines
), scope_lines AS (
  SELECT p.*, c.selected_line_count, c.eligible_line_count,
         c.excluded_unpaid_line_count, c.excluded_on_hold_line_count,
         c.excluded_cancelled_line_count, c.excluded_gift_line_count,
         c.unknown_gift_line_count, c.unknown_status_line_count,
         c.eligible_quantity,
         c.excluded_unpaid_quantity, c.excluded_on_hold_quantity,
         c.excluded_cancelled_quantity, c.excluded_gift_quantity,
         c.unknown_gift_quantity, c.unknown_status_quantity,
         c.invalid_quantity_line_count, c.missing_observation_line_count,
         c.excluded_valid_quantity
  FROM population_lines AS p
  CROSS JOIN coverage_by_reason AS c
  WHERE p.exclusion_reason = 'ELIGIBLE'
), converted_lines AS (
  SELECT s.*,
         CASE WHEN s.original_price_status = 'OBSERVED'
              THEN convert_to_cny(s.original_price_native, s.currency, :fx_snapshot)
         END AS original_cny,
         CASE WHEN s.paid_price_status = 'OBSERVED'
              THEN convert_to_cny(s.paid_price_native, s.currency, :fx_snapshot)
         END AS paid_cny
  FROM scope_lines AS s
), coverage_by_price AS (
  SELECT
    COALESCE(SUM(effective_quantity) FILTER (
      WHERE (original_price_status = 'OBSERVED' OR paid_price_status = 'OBSERVED')
        AND (currency IS NULL OR currency = '')
    ), 0) AS missing_currency_quantity,
    COALESCE(SUM(effective_quantity) FILTER (
      WHERE (original_price_status = 'OBSERVED' AND original_cny IS NULL)
         OR (paid_price_status = 'OBSERVED' AND paid_cny IS NULL)
    ), 0) AS fx_unavailable_quantity
  FROM converted_lines
), cny_lines AS (
  SELECT l.*, c.missing_currency_quantity, c.fx_unavailable_quantity
  FROM converted_lines AS l
  CROSS JOIN coverage_by_price AS c
)
```

`population_lines` is the pre-eligibility LEFT JOIN relation: every selected legacy line is retained even when it has no canonical observation. Its `exclusion_reason` is mutually exclusive, and `known_valid_quantity` is populated only from a positive integral observation quantity. `coverage_by_reason` is the sole producer for line/exclusion counters and known-valid-unit sums: it counts each reason explicitly, emits zero for empty reasons, and asserts the diagnostic partition `selected_line_count = eligible + unpaid + on_hold + cancelled + gift + unknown_gift + unknown_status + invalid_quantity + missing_observation`. `MISSING_OBSERVATION` and `INVALID_QUANTITY` contribute line counts only; the wire does not invent unit quantities for them. `coverage_by_price` is the sole producer for currency/FX counters over eligible known units; it is joined to the response coverage and emits zero when no eligible observed price requires the counter. No counter uses a guessed quantity or a second refund join.

`ranked_price_observations` is the concrete canonical relation: it selects the newest authoritative whole observation, including newest missing/invalid fields; it never falls back to an older valid amount. `cost_basis` is the existing ROI current effective-cost map materialized as bound arrays by `spu_pk=l.spu_pk` in the same snapshot; `unit_cost_used`/`cost_source` are not observation columns. `o.shop_pk` is authoritative because `sales_order_lines` has no `shop_pk`. Parent status filters reuse existing ROI constants; exact local-time boundary comes from the common profitability module, not a second timezone map.

When a price is missing/invalid, `cny_lines` keeps the valid quantity for that line so metric-specific missing/invalid price counts conserve units; when quantity itself is invalid, the row is absent from `scope_lines` and only its invalid-quantity line count is reported. FX conversion is attempted only for an `OBSERVED` field with verified currency; a missing/unknown rate is counted as global FX gap and follows the HTTP error policy rather than silently becoming zero.

Purchase rows use `unit_cost_used` from this current ROI cost map. `MANUAL` is current observed cost; `DEFAULT_K1` is a CNY estimate and must set `estimated=true`/warning. A changed current cost changes purchase output in a new snapshot without changing historical TikTok price observations. No historical procurement distribution is invented.

### 4.3 Quantity-weighted mean and median without row explosion

For each metric independently, filter `price_cny IS NOT NULL` and group identical CNY prices first:

```sql
WITH observations AS (
  SELECT spu_pk, 'paid' AS metric, paid_cny AS price_cny,
         effective_quantity::bigint AS qty
  FROM cny_lines
  WHERE paid_price_status = 'OBSERVED' AND paid_cny IS NOT NULL
), buckets AS (
  SELECT spu_pk, metric, price_cny, SUM(qty)::bigint AS qty
  FROM observations
  GROUP BY spu_pk, metric, price_cny
), ranked AS (
  SELECT b.*,
         SUM(qty) OVER (
           PARTITION BY spu_pk, metric ORDER BY price_cny
           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
         ) AS cumulative_qty,
         SUM(qty) OVER (PARTITION BY spu_pk, metric) AS total_qty,
         SUM(price_cny * qty) OVER (PARTITION BY spu_pk, metric) AS weighted_sum
  FROM buckets AS b
), targets AS (
  SELECT DISTINCT spu_pk, metric, total_qty, weighted_sum,
         CASE WHEN total_qty % 2 = 1
              THEN (total_qty + 1) / 2 ELSE total_qty / 2 END AS lower_rank,
         CASE WHEN total_qty % 2 = 1
              THEN (total_qty + 1) / 2 ELSE total_qty / 2 + 1 END AS upper_rank
  FROM ranked
), medians AS (
  SELECT t.spu_pk, t.metric,
         t.weighted_sum / NULLIF(t.total_qty, 0) AS mean_cny,
         MIN(r.price_cny) FILTER (WHERE r.cumulative_qty >= t.lower_rank)
             AS lower_price_cny,
         MIN(r.price_cny) FILTER (WHERE r.cumulative_qty >= t.upper_rank)
             AS upper_price_cny,
         t.total_qty
  FROM targets AS t JOIN ranked AS r
    ON r.spu_pk=t.spu_pk AND r.metric=t.metric
  GROUP BY t.spu_pk, t.metric, t.weighted_sum, t.total_qty
)
SELECT *, CASE WHEN total_qty % 2 = 1 THEN lower_price_cny
               ELSE (lower_price_cny + upper_price_cny) / 2 END AS median_cny
FROM medians;
```

The implementation may replace this with a more efficient equivalent, but must retain bucketed cumulative quantities and must not use `AVG(row_mean)`, unweighted `percentile_cont`, or per-piece row explosion. For even total quantity, ranks are `N/2` and `N/2+1`; for odd, rank `(N+1)/2`; ties are naturally one price bucket. Purchase, original sale and paid each run independently, including independent null/status counts.

### 4.4 Totals, partial coverage and sort

- `items` are SPU aggregates after full-scope computation; `totals.priceStats` is recomputed directly from all selected raw eligible observations, never from `items` means/medians and never from visible rows.
- A metric with no eligible quantity has `mean=null`, `median=null`, `status=no_samples`; zero is returned only for a real observed zero price.
- 对每个 metric，wire `observedQuantity + missingQuantity + invalidQuantity` 必须等于该 metric 的 valid-quantity universe；内部 Python `invalid_price_quantity` 只在 adapter 映射一次为 wire `invalidQuantity`，不得并列输出两个名字。price invalid 使用有效 quantity 计数；invalid physical quantity 本身没有物理件数，不得求和或填 arbitrary positive value，只在 `priceCoverage.invalidQuantityLineCount` 计行数；gift/status 排除的 valid units 用 mutually-exclusive `excludedValidQuantity` 计数。
- Query `q` affects the matched row `items` and row `total` only; page `limit/offset`, sort, and visible-column toggles affect presentation/items only. Business `totals`, price coverage, calculatedAt/FX/cost basis remain invariant under q/sort/page/visible-column transformations when the underlying facts and read basis are unchanged. Empty focused membership is an intentional empty selection, never a full-shop fallback.
- New sort identifiers are exactly `purchasePriceMean`, `purchasePriceMedian`, `originalSalePriceMean`, `originalSalePriceMedian`, `paidPriceMean`, `paidPriceMedian`. Nulls sort last in both directions, then stable `spend` and `spu_pk ASC`; sort must occur before page slicing and return the identifier in meta.

## 5. HTTP wire contract

### 5.1 Endpoints、scope 与兼容

页面仍是 `GET /v2/pages/spu-roi` 与 `GET /v2/pages/focused-spus`；两页都由共享 kernel 请求 `GET /v2/analytics/spu-roi`。focused 请求必须带 `shop_pk` 与 `scope=focused`，而 `GET /v2/reporting/focused-spus/{shop_pk}` 只管理 membership，不返回六个价格，也不被 enrichment。

Analytics request 保留既有 snake_case 参数：

```text
shop_pk=<positive int>                 # required
scope=focused                          # focused page only
spu_ids=<comma list>                   # exact page selection, existing limit
w_start=YYYY-MM-DD&w_end=YYYY-MM-DD    # operating window, existing timezone rules
q=<search>&sort=<SortField>&order=asc|desc&limit=<1..500>&offset=<>=0
include_all=true|false
```

新 `sort` 值只加入 §4.4 六个 camelCase identifiers；不新增“价格开关”，不允许 UI 通过隐藏列改变查询。认证、角色、rate limit、2xx/4xx/5xx 和 `requestId` 继续以公共 external API contract 为准。旧 snake_case item/totals/meta 字段原样保留；新价格字段只用下列 camelCase，不制造同义 snake/camel 双写。

### 5.2 精确新字段

`items[]` 与 `totals` 都新增 `priceStats`：

```json
{
  "priceStats": {
    "purchase": {
      "mean": "37.0000", "median": "40.0000",
      "eligibleQuantity": 10, "observedQuantity": 10,
      "missingQuantity": 0, "invalidQuantity": 0,
      "observedLineCount": 2, "missingLineCount": 0,
      "invalidLineCount": 0, "coverageRatio": "1.0000",
      "status": "complete", "source": "roi_unit_cost",
      "estimated": true
    },
    "originalSale": {
      "mean": "55.0000", "median": "60.0000",
      "eligibleQuantity": 10, "observedQuantity": 9,
      "missingQuantity": 1, "invalidQuantity": 0,
      "observedLineCount": 2, "missingLineCount": 1,
      "invalidLineCount": 0, "coverageRatio": "0.9000",
      "status": "partial", "source": "tiktok_line_item_original_price",
      "estimated": false
    },
    "paid": {
      "mean": "49.0000", "median": "54.0000",
      "eligibleQuantity": 10, "observedQuantity": 10,
      "missingQuantity": 0, "invalidQuantity": 0,
      "observedLineCount": 3, "missingLineCount": 0,
      "invalidLineCount": 0, "coverageRatio": "1.0000",
      "status": "complete", "source": "tiktok_line_item_sale_price",
      "estimated": false
    }
  }
}
```

Rules for this object:

- `mean`/`median` are CNY money strings with exactly four fractional digits or JSON `null`; no sample means both null, never `"0.0000"`.
- Counts are integers in JSON; quantities are integer units as the approved price-statistics contract; upstream quantity-domain evidence remains bounded and the implementation gate is explicit in §2.3. `coverageRatio` is a four-decimal string, null only when `eligibleQuantity=0`.
- `eligibleQuantity` is the paid/non-gift/valid-quantity population before this metric’s price validity; `observedQuantity`, `missingQuantity`, `invalidQuantity` are independent per metric and `invalidQuantity` means invalid **price** units, not invalid physical quantity. Line counts are likewise metric-specific. Invalid physical quantity has no unit count and appears only as `priceCoverage.invalidQuantityLineCount`; gift/status/FX exclusions that prevent entering the metric universe are reported in `priceCoverage`, not silently added to missing price.
- `source` enum is exactly `roi_unit_cost`, `tiktok_line_item_original_price`, `tiktok_line_item_sale_price`, or `none`. `status` enum is exactly `complete`, `partial`, `no_samples`, `unavailable`; `estimated=true` is only permitted for purchase with any `DEFAULT_K1` cost. No prices are returned with `source=none` and non-null values.
- `priceCoverage` is added alongside `priceStats` in `items[]`/`totals` for explicit population exclusions:

```json
{
  "priceCoverage": {
    "eligibleLineCount": 3, "eligibleQuantity": 10,
    "excludedUnpaidQuantity": 0, "excludedOnHoldQuantity": 1,
    "excludedCancelledQuantity": 1, "excludedGiftQuantity": 2,
    "unknownGiftQuantity": 0, "unknownStatusQuantity": 0,
    "invalidQuantityLineCount": 0, "excludedValidQuantity": 0,
    "missingCurrencyQuantity": 0,
    "fxUnavailableQuantity": 0,
    "missingObservationLineCount": 0
  }
}
```

The coverage object counts source population and exclusions, while each metric object counts only its own price-field coverage. `invalidQuantityLineCount` and `missingObservationLineCount` are line counts only; there is deliberately no physical-unit sum when quantity/observation is unknown. `excludedValidQuantity` counts known valid units excluded by the mutually-exclusive population classification. Every selected line belongs to exactly one classification: eligible, unpaid, on-hold, cancelled, unknown-status, gift, unknown-gift, invalid-quantity, or missing-observation; the producer must aggregate each reason from `population_lines` and emit zero for empty known reasons. If the implementation cannot establish a required mapping, response status is `503`/documented `422` rather than a misleading complete `200`.

`meta` adds only:

```json
{
  "calculatedAt": "2026-10-05T12:00:00+00:00",
  "priceCurrency": "CNY",
  "priceFx": {
    "snapshotId": 901, "asOfAt": "2026-10-05T11:59:00+00:00",
    "conversionPolicy": "native_line_currency_to_cny_before_aggregation"
  },
  "priceCost": {
    "basisFingerprint": "sha256:response-local-cost-map-v1",
    "asOfAt": "2026-10-05T12:00:00+00:00",
    "defaultK1Cny": "40.0000", "estimated": true
  },
  "priceSort": "paidPriceMedian"
}
```

Existing `computed_at` and other legacy snake fields remain for compatibility; clients should use `calculatedAt` for this feature once present. `calculatedAt`, FX snapshot and cost basis must be identical throughout one response. Across separate HTTP requests they may legitimately advance or change when facts, rates, or current costs change; invariance claims are conditional on the same unchanged read basis. `basisFingerprint` is an opaque response-local digest of the ordered `(spu_pk, unit_cost_used, cost_source)` map and `calculatedAt`; it is not a persisted cost snapshot id or a new table. It lets logs correlate one response without claiming a historical procurement snapshot.

### 5.3 Mutually consistent examples

**Normal standard page, two-SPU weighted example (legacy snake fields remain alongside these fields):**

```http
GET /v2/analytics/spu-roi?shop_pk=314&w_start=2026-10-04&w_end=2026-10-04&sort=paidPriceMedian&order=asc&limit=50&offset=0
```

The fixture has SPU A with purchase 10 × qty 1 and SPU B with purchase 40 × qty 9. A uses `MANUAL`, B uses `DEFAULT_K1`; each row therefore has mean=median cost, while totals are purchase mean 37 and median 40. Original sale is 20 × 1 plus 50 × 9; paid is 18 × 1 plus 45 × 9.

```json
{
  "items": [
    {"spu_pk":12,"spu_id":"A","priceStats":{"purchase":{"mean":"10.0000","median":"10.0000","eligibleQuantity":1,"observedQuantity":1,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"roi_unit_cost","estimated":false},"originalSale":{"mean":"20.0000","median":"20.0000","eligibleQuantity":1,"observedQuantity":1,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_original_price","estimated":false},"paid":{"mean":"18.0000","median":"18.0000","eligibleQuantity":1,"observedQuantity":1,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_sale_price","estimated":false}},"priceCoverage":{"eligibleLineCount":1,"eligibleQuantity":1,"excludedUnpaidQuantity":0,"excludedOnHoldQuantity":0,"excludedCancelledQuantity":0,"excludedGiftQuantity":0,"unknownGiftQuantity":0,"unknownStatusQuantity":0,"invalidQuantityLineCount":0,"excludedValidQuantity":0,"missingCurrencyQuantity":0,"fxUnavailableQuantity":0,"missingObservationLineCount":0}},
    {"spu_pk":13,"spu_id":"B","priceStats":{"purchase":{"mean":"40.0000","median":"40.0000","eligibleQuantity":9,"observedQuantity":9,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"roi_unit_cost","estimated":true},"originalSale":{"mean":"50.0000","median":"50.0000","eligibleQuantity":9,"observedQuantity":9,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_original_price","estimated":false},"paid":{"mean":"45.0000","median":"45.0000","eligibleQuantity":9,"observedQuantity":9,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_sale_price","estimated":false}},"priceCoverage":{"eligibleLineCount":1,"eligibleQuantity":9,"excludedUnpaidQuantity":0,"excludedOnHoldQuantity":0,"excludedCancelledQuantity":0,"excludedGiftQuantity":0,"unknownGiftQuantity":0,"unknownStatusQuantity":0,"invalidQuantityLineCount":0,"excludedValidQuantity":0,"missingCurrencyQuantity":0,"fxUnavailableQuantity":0,"missingObservationLineCount":0}}
  ],
  "total":2,
  "totals":{"priceStats":{"purchase":{"mean":"37.0000","median":"40.0000","eligibleQuantity":10,"observedQuantity":10,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":2,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"roi_unit_cost","estimated":true},"originalSale":{"mean":"47.0000","median":"50.0000","eligibleQuantity":10,"observedQuantity":10,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":2,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_original_price","estimated":false},"paid":{"mean":"42.3000","median":"45.0000","eligibleQuantity":10,"observedQuantity":10,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":2,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_sale_price","estimated":false}},"priceCoverage":{"eligibleLineCount":2,"eligibleQuantity":10,"excludedUnpaidQuantity":0,"excludedOnHoldQuantity":0,"excludedCancelledQuantity":0,"excludedGiftQuantity":0,"unknownGiftQuantity":0,"unknownStatusQuantity":0,"invalidQuantityLineCount":0,"excludedValidQuantity":0,"missingCurrencyQuantity":0,"fxUnavailableQuantity":0,"missingObservationLineCount":0}},
  "meta":{"calculatedAt":"2026-10-05T12:00:00+00:00","priceCurrency":"CNY","priceFx":{"snapshotId":901,"asOfAt":"2026-10-05T11:59:00+00:00","conversionPolicy":"native_line_currency_to_cny_before_aggregation"},"priceCost":{"basisFingerprint":"sha256:response-local-cost-map-v1","asOfAt":"2026-10-05T12:00:00+00:00","defaultK1Cny":"40.0000","estimated":true},"priceSort":"paidPriceMedian"}
}
```

**Focused page (same two SPUs, complete new contract):** the membership GET remains separate; only analytics carries prices.

```http
GET /v2/analytics/spu-roi?shop_pk=314&scope=focused&w_start=2026-10-04&w_end=2026-10-04&sort=paidPriceMedian&order=asc&limit=50&offset=0
```

```json
{"items":[{"spu_pk":12,"spu_id":"A","priceStats":{"purchase":{"mean":"10.0000","median":"10.0000","eligibleQuantity":1,"observedQuantity":1,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"roi_unit_cost","estimated":false},"originalSale":{"mean":"20.0000","median":"20.0000","eligibleQuantity":1,"observedQuantity":1,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_original_price","estimated":false},"paid":{"mean":"18.0000","median":"18.0000","eligibleQuantity":1,"observedQuantity":1,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_sale_price","estimated":false}},"priceCoverage":{"eligibleLineCount":1,"eligibleQuantity":1,"excludedUnpaidQuantity":0,"excludedOnHoldQuantity":0,"excludedCancelledQuantity":0,"excludedGiftQuantity":0,"unknownGiftQuantity":0,"unknownStatusQuantity":0,"invalidQuantityLineCount":0,"excludedValidQuantity":0,"missingCurrencyQuantity":0,"fxUnavailableQuantity":0,"missingObservationLineCount":0}},{"spu_pk":13,"spu_id":"B","priceStats":{"purchase":{"mean":"40.0000","median":"40.0000","eligibleQuantity":9,"observedQuantity":9,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"roi_unit_cost","estimated":true},"originalSale":{"mean":"50.0000","median":"50.0000","eligibleQuantity":9,"observedQuantity":9,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_original_price","estimated":false},"paid":{"mean":"45.0000","median":"45.0000","eligibleQuantity":9,"observedQuantity":9,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_sale_price","estimated":false}},"priceCoverage":{"eligibleLineCount":1,"eligibleQuantity":9,"excludedUnpaidQuantity":0,"excludedOnHoldQuantity":0,"excludedCancelledQuantity":0,"excludedGiftQuantity":0,"unknownGiftQuantity":0,"unknownStatusQuantity":0,"invalidQuantityLineCount":0,"excludedValidQuantity":0,"missingCurrencyQuantity":0,"fxUnavailableQuantity":0,"missingObservationLineCount":0}}],"total":2,"totals":{"priceStats":{"purchase":{"mean":"37.0000","median":"40.0000","eligibleQuantity":10,"observedQuantity":10,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":2,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"roi_unit_cost","estimated":true},"originalSale":{"mean":"47.0000","median":"50.0000","eligibleQuantity":10,"observedQuantity":10,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":2,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_original_price","estimated":false},"paid":{"mean":"42.3000","median":"45.0000","eligibleQuantity":10,"observedQuantity":10,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":2,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_sale_price","estimated":false}},"priceCoverage":{"eligibleLineCount":2,"eligibleQuantity":10,"excludedUnpaidQuantity":0,"excludedOnHoldQuantity":0,"excludedCancelledQuantity":0,"excludedGiftQuantity":0,"unknownGiftQuantity":0,"unknownStatusQuantity":0,"invalidQuantityLineCount":0,"excludedValidQuantity":0,"missingCurrencyQuantity":0,"fxUnavailableQuantity":0,"missingObservationLineCount":0}},"meta":{"calculatedAt":"2026-10-05T12:00:00+00:00","priceCurrency":"CNY","priceFx":{"snapshotId":901,"asOfAt":"2026-10-05T11:59:00+00:00","conversionPolicy":"native_line_currency_to_cny_before_aggregation"},"priceCost":{"basisFingerprint":"sha256:response-local-cost-map-v1","asOfAt":"2026-10-05T12:00:00+00:00","defaultK1Cny":"40.0000","estimated":true},"priceSort":"paidPriceMedian"}}
```

**Empty focused scope (complete envelope):**

```json
{"items":[],"total":0,"totals":{"priceStats":{"purchase":{"mean":null,"median":null,"eligibleQuantity":0,"observedQuantity":0,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":0,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":null,"status":"no_samples","source":"none","estimated":false},"originalSale":{"mean":null,"median":null,"eligibleQuantity":0,"observedQuantity":0,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":0,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":null,"status":"no_samples","source":"none","estimated":false},"paid":{"mean":null,"median":null,"eligibleQuantity":0,"observedQuantity":0,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":0,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":null,"status":"no_samples","source":"none","estimated":false}},"priceCoverage":{"eligibleLineCount":0,"eligibleQuantity":0,"excludedUnpaidQuantity":0,"excludedOnHoldQuantity":0,"excludedCancelledQuantity":0,"excludedGiftQuantity":0,"unknownGiftQuantity":0,"unknownStatusQuantity":0,"invalidQuantityLineCount":0,"excludedValidQuantity":0,"missingCurrencyQuantity":0,"fxUnavailableQuantity":0,"missingObservationLineCount":0}},"meta":{"calculatedAt":"2026-10-05T12:00:00+00:00","priceCurrency":"CNY","priceFx":{"snapshotId":901,"asOfAt":"2026-10-05T11:59:00+00:00","conversionPolicy":"native_line_currency_to_cny_before_aggregation"},"priceCost":{"basisFingerprint":"sha256:response-local-cost-map-v1","asOfAt":"2026-10-05T12:00:00+00:00","defaultK1Cny":"40.0000","estimated":false},"priceSort":"paidPriceMedian"}}
```

**Partial coverage (two SPUs, original price missing on B):** valid paid quantity is 10; original has one observed unit and nine missing units, so only original is partial. Purchase and paid remain complete.

```json
{"items":[{"spu_pk":12,"spu_id":"A","priceStats":{"purchase":{"mean":"10.0000","median":"10.0000","eligibleQuantity":1,"observedQuantity":1,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"roi_unit_cost","estimated":false},"originalSale":{"mean":"20.0000","median":"20.0000","eligibleQuantity":1,"observedQuantity":1,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_original_price","estimated":false},"paid":{"mean":"18.0000","median":"18.0000","eligibleQuantity":1,"observedQuantity":1,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_sale_price","estimated":false}},"priceCoverage":{"eligibleLineCount":1,"eligibleQuantity":1,"excludedUnpaidQuantity":0,"excludedOnHoldQuantity":0,"excludedCancelledQuantity":0,"excludedGiftQuantity":0,"unknownGiftQuantity":0,"unknownStatusQuantity":0,"invalidQuantityLineCount":0,"excludedValidQuantity":0,"missingCurrencyQuantity":0,"fxUnavailableQuantity":0,"missingObservationLineCount":0}},{"spu_pk":13,"spu_id":"B","priceStats":{"purchase":{"mean":"40.0000","median":"40.0000","eligibleQuantity":9,"observedQuantity":9,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"roi_unit_cost","estimated":true},"originalSale":{"mean":null,"median":null,"eligibleQuantity":9,"observedQuantity":0,"missingQuantity":9,"invalidQuantity":0,"observedLineCount":0,"missingLineCount":1,"invalidLineCount":0,"coverageRatio":"0.0000","status":"partial","source":"tiktok_line_item_original_price","estimated":false},"paid":{"mean":"45.0000","median":"45.0000","eligibleQuantity":9,"observedQuantity":9,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_sale_price","estimated":false}},"priceCoverage":{"eligibleLineCount":1,"eligibleQuantity":9,"excludedUnpaidQuantity":0,"excludedOnHoldQuantity":0,"excludedCancelledQuantity":0,"excludedGiftQuantity":0,"unknownGiftQuantity":0,"unknownStatusQuantity":0,"invalidQuantityLineCount":0,"excludedValidQuantity":0,"missingCurrencyQuantity":0,"fxUnavailableQuantity":0,"missingObservationLineCount":0}}],"total":2,"totals":{"priceStats":{"purchase":{"mean":"37.0000","median":"40.0000","eligibleQuantity":10,"observedQuantity":10,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":2,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"roi_unit_cost","estimated":true},"originalSale":{"mean":"20.0000","median":"20.0000","eligibleQuantity":10,"observedQuantity":1,"missingQuantity":9,"invalidQuantity":0,"observedLineCount":1,"missingLineCount":1,"invalidLineCount":0,"coverageRatio":"0.1000","status":"partial","source":"tiktok_line_item_original_price","estimated":false},"paid":{"mean":"42.3000","median":"45.0000","eligibleQuantity":10,"observedQuantity":10,"missingQuantity":0,"invalidQuantity":0,"observedLineCount":2,"missingLineCount":0,"invalidLineCount":0,"coverageRatio":"1.0000","status":"complete","source":"tiktok_line_item_sale_price","estimated":false}},"priceCoverage":{"eligibleLineCount":2,"eligibleQuantity":10,"excludedUnpaidQuantity":0,"excludedOnHoldQuantity":0,"excludedCancelledQuantity":0,"excludedGiftQuantity":0,"unknownGiftQuantity":0,"unknownStatusQuantity":0,"invalidQuantityLineCount":0,"excludedValidQuantity":0,"missingCurrencyQuantity":0,"fxUnavailableQuantity":0,"missingObservationLineCount":0}},"meta":{"calculatedAt":"2026-10-05T12:00:00+00:00","priceCurrency":"CNY","priceFx":{"snapshotId":901,"asOfAt":"2026-10-05T11:59:00+00:00","conversionPolicy":"native_line_currency_to_cny_before_aggregation"},"priceCost":{"basisFingerprint":"sha256:response-local-cost-map-v1","asOfAt":"2026-10-05T12:00:00+00:00","defaultK1Cny":"40.0000","estimated":true},"priceSort":"paidPriceMedian"}}
```

**Default-cost estimate (same two-SPU envelope, cost provenance explicit):** the normal and focused examples already include the complete default-cost behavior: B’s purchase metric is `40.0000/40.0000`, `source:"roi_unit_cost"`, `estimated:true`, while its TikTok original/paid metrics remain `50.0000/50.0000` and `45.0000/45.0000`. A changed current cost map changes only purchase values in a later response; it never rewrites these TikTok observations.

**Error:** missing FX for a non-CNY observed line returns `503` with no partial 200:

```json
{"code":"PRICE_FX_UNAVAILABLE","message":"price currency cannot be converted in the read snapshot","requestId":"req-price-901","retryable":true}
```

The error must not expose raw payloads, credentials or buyer data. Existing HTTP error envelope/request-id conventions remain authoritative.

## 6. UI 与共享 page kernel

### 6.1 页面布局与字段

两个页面必须继续加载 `templates/pages/spu-profitability.html`、`static/js/spu-profitability-page.js` 和各自 profile（`static/js/spu-roi.js` / `static/js/focused-spus.js`）。价格不跟随 projection 30/90 toggle，而跟随 operating window。

现有 summary 区中的 **Projected** group 后立即放置且只放置一个 **Prices** box（不是 purchase/sale/paid 三个独立 card，也不是新的全局 dashboard toggle）：

```text
[现有 Projected box] [Prices box                                      ]
                     Prices                 Mean              Median
                     Purchase               CNY 37.0000        CNY 40.0000
                     Original sale          CNY 55.0000        CNY 60.0000
                     Paid                   CNY 49.0000        CNY 54.0000
```

- 视觉完全复用 Projected box 的 warm-paper card、边框、标题、响应式 grid 和现有 tooltip button；`Prices` 标题旁 tooltip 说明“当前经营窗口、已付款、按件数加权、原价/实付来自 TikTok line item”。
- 每格显示 four-decimal money（页面可以沿用现有 display localization，但 tooltip/raw accessible text 保留 CNY 四位）；null 显示 `—`，不显示 0。
- purchase 的 `estimated=true` 或任一 row 使用 `cost_source=DEFAULT_K1` 时，Mean/Median 旁显示 `≈` 或现有 estimate badge，tooltip 明确 `K1=40 CNY/件，非人工成本`；MANUAL 行不误标 estimate。
- 三行的 missing/invalid/partial/unknown coverage 由 metric tooltip 和 accessible description 展示，不把三种 coverage 合并为一个颜色。颜色之外必须有文本状态。
- `Prices` box 只读；不新增“按价格筛选”“显示/隐藏价格摘要”“切换未付款”之类不受批准的 global toggle。

主表在现有商品列之后、广告/销售等现有业务列之前插入三组 grouped columns，顺序固定：

```text
商品 | Purchase [Mean | Median] | Original sale [Mean | Median]
     | Paid [Mean | Median] | 广告消耗 | ...既有列...
```

每个子列宽度建议 104px（窄屏可降至 96px），group header ≥208px；价格列不挤压商品首列。默认 visible 是六个子列；profile column toggles 可分别隐藏六个子列，但隐藏只改变渲染，不改变 API totals/聚合。列开关必须沿用 kernel 现有 `details.op-colswitch`，不添加另一套 preference store。

Tabulator 6.3.1 支持 nested `columns` definitions；实现 lane 必须以 vendored JS 的实际 browser smoke 证实 group header、子列 sort、resize、responsive horizontal scroll，不能只凭静态字符串测试。API 保持嵌套 `priceStats`，不复制/扁平化计算字段；若当前 vendored Tabulator 对 nested field path 的 accessor 行为不满足 smoke，先停在 `NEEDS_CONTEXT`，再由 UI owner 提交窄 presentation adapter（仍不重算指标）。六列必须共享以下唯一 mapping：

| column id | Tabulator `field`（nested API path） | server `sortField` | label | formatter/accessor | kernel/header state |
| --- | --- | --- | --- | --- | --- |
| `purchasePriceMean` | `priceStats.purchase.mean` | `purchasePriceMean` | Purchase · Mean | `formatMoneyOrDash`；读取 nested value 与 `estimated` badge | `PRICE_PURCHASE_MEAN`；同一 sort event/`aria-sort` |
| `purchasePriceMedian` | `priceStats.purchase.median` | `purchasePriceMedian` | Purchase · Median | `formatMoneyOrDash`；读取 nested value 与 `estimated` badge | `PRICE_PURCHASE_MEDIAN`；同一 sort event/`aria-sort` |
| `originalSalePriceMean` | `priceStats.originalSale.mean` | `originalSalePriceMean` | Original sale · Mean | `formatMoneyOrDash`；读取 nested value/status | `PRICE_ORIGINAL_MEAN`；同一 sort event/`aria-sort` |
| `originalSalePriceMedian` | `priceStats.originalSale.median` | `originalSalePriceMedian` | Original sale · Median | `formatMoneyOrDash`；读取 nested value/status | `PRICE_ORIGINAL_MEDIAN`；同一 sort event/`aria-sort` |
| `paidPriceMean` | `priceStats.paid.mean` | `paidPriceMean` | Paid · Mean | `formatMoneyOrDash`；读取 nested value/status | `PRICE_PAID_MEAN`；同一 sort event/`aria-sort` |
| `paidPriceMedian` | `priceStats.paid.median` | `paidPriceMedian` | Paid · Median | `formatMoneyOrDash`；读取 nested value/status | `PRICE_PAID_MEDIAN`；同一 sort event/`aria-sort` |

`COLUMN_DEFS`/profile allowlist 使用 column id；kernel 将 id 映射为 nested `field` 与 independent server `sortField`，而不是把 server sort 名直接当作 API field。Formatter/accessor 只能读取该 metric 的 nested value/status/estimated metadata，不能复制价格计算。Browser tests 必须用实际 API nested payload 断言六个值均渲染，并对六个 `sortField` 各断言一次 outgoing request；无扁平化 row copy。

### 6.2 排序、键盘与 tooltip 可用性

- 点击任意六个子列标题发送完整当前 query，仅更改 `sort/order`；sort icon 与 `aria-sort="ascending|descending|none"` 只标记当前 active column，其他列为 none。null-last 由服务端完成，Tabulator 不做本地重新排序。
- 标题可聚焦，Enter/Space 与 click 等效；加载期间保持 focus、显示 pending，响应返回后更新 header label/aria-sort，不跳焦点。
- 每个 Prices 行 tooltip button 位于 label 右侧、可 Tab 到达，有 `aria-label`（例如“查看 Paid 价格统计口径”）和 `aria-expanded`；click/Enter/Space 打开，Escape 关闭并把焦点还给 button，点击 outside 关闭；移动端不依赖 hover。异步刷新重绘不得留下幽灵 tooltip 或把焦点送到 body。
- Tooltip 文本至少包含 authority、weight、window、coverage/status、FX snapshot 与 current-cost-basis tuple；不得把 tooltip 当作唯一错误通道。

### 6.3 两个 profile 与状态竞态

共享 kernel 的 `renderSummary(payload)`、`COLUMN_DEFS`/nested columns、formatters、request cancellation、stale response guard 只维护一份。profile 仅声明 `id`, endpoint selection (`standard` / `scope=focused`), default includeAll, default visible columns 和 page path。

以下事件都递增 `loadVersion` 并 abort 前一个 overview：shop 切换、SPU apply/clear、日期 start/end、搜索 q、排序、分页、refresh。dispatch 时捕获不可变的本地 request identity：`loadVersion`、shop_pk、applied-selection hash、operating window、q、page/limit、sort/order；response 写 DOM 前逐项与当前 client state 比较，不匹配按 stale 丢弃，不能用旧 price box 覆盖新 window。FX/cost 不是请求前可知的 key，只检查该次 response 内 `calculatedAt`、FX tuple、cost basis tuple 的一致性。

- `q` 只影响匹配行与 `total`，价格 business totals 不重算；sort/page/limit/visible toggle 只影响 rows/DOM。用户快速 page→sort→date 时只允许最后一个 response 成为可见状态。
- loading 时 Prices box 显示 skeleton/“加载中”，不得保留上一窗口数字冒充新结果；success 替换为 snapshot 数字；empty focused 显示 no samples；partial 显示 warning + 可解释计数；503 FX/422 参数错误/5xx requestId 显示同一 box 的可读 error 与 retry。
- 清空 focused membership 后仍请求 `scope=focused`，得到空 totals；不能因为 focused GET 返回空而请求整店 analytics。切换 focused membership 的 mutation queue 完成后递增 `appliedSelectionVersion`、重新请求 analytics，并丢弃旧 selection version 的 response；不新增 server fingerprint contract。
- refresh、日期 apply、search、column toggle 和 row expand 不改变已应用 selection；row drill cache 失效只在 shop/scope/window/cost/FX 变化时清除。价格不进入 projection toggle 的 cache key，但 operating window 必须进入。

### 6.4 响应式与表格列显隐

- 桌面：Prices box 与 Projected 同一 summary row，Mean/Median 固定左右两列；主表 grouped header 横向不换成两张表。
- 移动：summary card 变一列或两列；表格保留水平滚动，商品首列冻结，价格三组可横向阅读。不得用 `nth-child` 静默隐藏价格；列开关是显式、键盘可用的 profile preference。
- 固定首列必须经过 Playwright viewport 断言；如果 vendored Tabulator frozen group header 在移动端不支持，保留普通水平滚动而不制造不可访问的 overlay。
- 搜索日期 apply/clear、focused mutation、分页、sort、refresh 的 loading/empty/error/stale 文案均通过 `aria-live=polite`；价格 null/status/estimate 同时有文本和可访问名称。

## 7. 测试、数字 oracle 与证据边界

### 7.1 独立 handwritten oracle（禁止只测对称样本）

测试必须有不依赖实现 helper 的独立 Decimal oracle，按 unit 展开仅在测试 oracle 内展开（生产 SQL 不展开）。最小 fixture：

| unit price（CNY） | qty | 说明 |
| --- | ---: | --- |
| purchase A | 10 × 1 | 低价 SPU A |
| purchase B | 40 × 9 | 高价 SPU B；**总均值 37、加权中位数 40**，行均值/简单 line median 会错 |
| original sale | 20 × 1, 50 × 2, 80 × 2 | 5 units，odd median 50 |
| paid | 18 × 1, 45 × 2, 72 × 2 | 5 units，odd median 45 |
| even case | 10 × 2, 40 × 2 | median (10+40)/2 = 25 |
| explicit zero | 0 × 1 | valid zero, not missing |
| missing | absent × 1 | metric missing count +1, never zero |
| invalid | -1 / non-numeric | invalid count, excluded |
| gift/status | valid price × qty | excluded before metric; explicit exclusion count |
| FX | 100 THB × 1 with fixed rate 0.2 | convert 20 CNY before mean/median |

Use at least two SPUs with unequal quantities to prove totals are not average-of-SPU means/medians. Add an explicit acceptance test (for example `test_later_refund_or_full_loss_keeps_paid_observation`) where a paid line is later marked refunded/full-loss; assert the historical paid observation remains in the paid population. Do not join refund facts merely to manufacture a coverage counter. Add shipping/payment total values intentionally inconsistent and assert neither affects result. Add Miaoshou values intentionally different and assert they never appear in output.

Expected core oracle for purchase A10 qty1 + B40 qty9:

```text
weighted mean = (10*1 + 40*9) / 10 = 37.0000
weighted median = unit rank 5 and 6 in [10,40,40,40,40,40,40,40,40,40] = 40.0000
```

Test odd/even ties, zero-vs-null, missing/invalid independent original vs paid coverage, DEFAULT_K1 estimated state, FX conversion, refund retention, gift/status exclusions, and null/no-sample rendering. A symmetric mean=median fixture alone is insufficient.

### 7.2 Test layers and concrete matrix

| Layer | Required test | Evidence / command |
| --- | --- | --- |
| parser unit | orders and order_detail same raw fixture; original/sale source, plain/object shape, quantity/gift/status/currency and provenance parity | narrow parser tests through `bash scripts/test_isolated.sh unit ...` |
| upsert/DB | idempotent retry; same hash no-op; older response cannot overwrite newer; missing/invalid field rules; exact shop/order/line/raw linkage; test-shaped migration checks | isolated DB fixture; no production DB |
| backfill | dry-run counts; keyset checkpoint resume; stale skip; retry rollback; concurrent newer sync wins; no destructive SQL; gaps remain visible | guarded script tests on ephemeral DB |
| domain/unit | independent oracle mean/median, cumulative SQL ranks, totals vs row averages, six sort IDs/null-last/tie-breaker, coverage statuses, snapshot metadata | `bash scripts/test_isolated.sh unit` / analytics domain |
| HTTP/API | real FastAPI route + isolated DB; standard and `scope=focused`; empty/partial/default/error; totals invariant under q/sort/page/columns; legacy snake fields unchanged | `bash scripts/test_isolated.sh fast tests/api/test_spu_price_stats.py` after released ownership |
| browser static | shared kernel/profile markup, aria, grouped columns contract | useful regression only, never E2E evidence |
| live browser E2E | real Playwright browser → temporary uvicorn → actual API → same isolated DB; seed then authenticate; prices, sort, totals invariance, errors/retry, mobile | `bash scripts/test_isolated.sh e2e tests/browser/test_spu_price_stats_live.py`; collection must be >0 and unavailable browser/API is failure, not skip-green |

### 7.3 Real E2E execution contract

The live test files are `tests/browser/test_spu_price_stats_live.py` and `tests/support/spu_price_stats_live.py` after explicit release (projection owner released these paths; this doc lane still does not edit them). The test module must declare the verified registry markers exactly:

```python
pytestmark = (
    pytest.mark.domain_e2e,
    pytest.mark.requires_service,
    pytest.mark.requires_browser,
    pytest.mark.requires_db,
)
```

The fixture must:

1. Let outer `scripts/test_isolated.sh` provide one ephemeral `TTS_ERP_DB_URL_TEST`; derive only a test-shaped `TTS_ERP_DB_URL` and pass **both variables with the identical outer ephemeral URL** to the child app. Use an explicit allowlisted environment (`TTS_ERP_AUTH_MODE=enforce`, deterministic test Fernet key, safe test settings), remove inherited service/proxy credentials, and do not let cwd/import-time dotenv load production `.env` values.
2. Seed a `TEST_PRICE_<run-id>` user/account and fixture rows in that same DB through SQLAlchemy/psycopg before server launch, commit, then close. Obtain the browser cookie through the real `POST /v2/auth/login` TEST-user path; do not read/decrypt credentials directly. Cleanup only TEST rows. No `--keep-db` is needed for normal process lifetime because fixture cleanup occurs before wrapper exit; retain it only for human failure inspection.
3. Start the app with `sys.executable -m uvicorn tts_erp_v2.app:app` on a random free loopback port; poll `/healthz`; capture stdout/stderr. Bounded source evidence places APScheduler in the separate `tts_erp_v2/sync_worker` process and found no scheduler-disable flag; do not invent a switch—verify the actual app lifespan before implementation and ensure the test child has no sync-worker/upstream process. Never monkeypatch the target route or call production `:9877`.
4. Run `bash scripts/test_isolated.sh e2e tests/browser/test_spu_price_stats_live.py`; collection must report >0. A browser, app, auth, DB, or readiness failure must fail the test and be reported as a blocker, not be converted to `pytest.skip` or a green empty collection.
5. Launch Playwright Chromium headless; assert Prices box and grouped headers on both page routes, and feed the actual nested API payload (`priceStats.purchase|originalSale|paid`); verify all six mapped values render through the nested Tabulator fields. Trigger each of the six sorts and assert the corresponding independent `sortField` request (`purchasePriceMean`, `purchasePriceMedian`, `originalSalePriceMean`, `originalSalePriceMedian`, `paidPriceMean`, `paidPriceMedian`), plus `scope=focused` analytics mapping, empty/partial/null/estimate states, totals invariance under q/page/visible columns when facts/basis are unchanged, latest-response-wins race, API error/retry, keyboard tooltip/aria-sort, and mobile horizontal scroll/frozen first column.
6. Always stop browser/server process group with bounded terminate→kill escalation, close logs, and preserve screenshot/trace/stdout on failure. The same isolated DB must be used by seed, child app (`TTS_ERP_DB_URL` and `_TEST`), API, and browser; no target-API mock is allowed.

Source grep, static string tests, mocked JSON render tests, existing canned browser fixtures, and existing external-service smoke tests are regression evidence only; none proves DB/API/browser full-stack behavior.

## 8. 发布、混合版本与回滚

### 8.1 兼容发布顺序

1. **Schema first（人工）**：执行 additive migration 到 test-shaped DB 验证，再由人工在生产窗口执行；创建 observation 表/enum/check/index，不删旧字段、不改既有 `unit_price` 语义。
2. **Producer second**：部署 orders 与 order_detail 共享 normalizer；旧 API 仍可读，producer 写入新表但不要求旧页面立刻展示。sync-worker 按项目规则人工重启并核对 sync issue、stale skip、unknown gift/status、FX 缺口。
3. **Domain/API third**：API 先以 capability 检测 observation schema/price basis；旧 rows 没有 price data 时返回明确 null/no_samples/coverage，不回退 Miaoshou。保留旧利润 JSON fields 与 HTTP status semantics。
4. **UI fourth**：模板/kernel 在 API capability 后展示 Prices 与 grouped columns；API 不支持时隐藏新增区域并保留现有利润页，不能渲染旧缓存价格。
5. **Backfill last**：人工 dry-run→审查 gap report→分批 guarded real backfill；确认 raw match、coverage、stale race 与 retry metrics 后才宣称历史覆盖。部署期间 capability、static asset version 和 response shape 都必须支持旧 producer/new API、new producer/old API 的混合窗口。

### 8.2 观测与完成门

发布 runbook 必须记录 migration revision（由实际 head 生成）、producer/API/UI commit、response-local calculatedAt/FX/cost-basis tuple、每 shop coverage 和 open sync issues。完成门：

- schema migration 成功且无 destructive statements；
- orders/detail parity fixture 通过；
- API response 的 legacy fields、六 price fields、four-decimal/null/coverage/status 均通过；
- q/sort/page/visible column 不改变 totals；
- backfill unmatched/unknown/invalid/FX gap 有解释或保持 `INCOMPLETE`；
- live E2E 真正启动临时 API、真实浏览器和 isolated DB，并保存证据。浏览器不可用时是 blocker，不得以静态测试替代。

### 8.3 回滚

- Producer/API/UI 可按 commit 回滚；保留 additive observation 表与数据，旧代码忽略新表即可。不要先删 schema。
- API 回滚时 capability guard 保证旧 API 不读取半成品；UI 回滚时旧资产不调用新字段，缓存 key/version 使新旧 HTML/JS 不混用。
- 发现错误 observation 时停止 producer/backfill、保留 raw/provenance 和 issue，修正 parser 后从 checkpoint 重放；不能用 destructive delete 清历史。
- schema downgrade 不作为常规 rollback，尤其不执行生产 `DROP`/数据回写；迁移 retention 由人工在独立审查后决定。利润计算始终走原有字段和公式，不因价格模块故障改变利润。

## 9. 独占文件地图、依赖图与实施阶段

### 9.1 文件 ownership（后续 lane，非本 doc lane）

| 组件 | 独占路径 | 允许职责 | 明确禁止 |
| --- | --- | --- | --- |
| data producer/schema | `tts_erp_v2/db/models/commerce.py`, `tts_erp_v2/jobs/tiktok/orders.py`, `tts_erp_v2/jobs/tiktok/order_detail.py`, the new revision file under `alembic/versions/` selected after checking `alembic heads`, `scripts/oneoff_backfill_tiktok_price_stats.py` | observation model, parser parity, guarded backfill | profit/UI/API changes; guessed migration number; production run |
| price math helper | `tts_erp_v2/analytics/spu_profitability/_price_math.py` plus its dedicated unit tests in the math lane worktree | standalone pure-Decimal weighted mean/median algorithm and oracle-facing contract only | DB/HTTP/UI policy, eligibility/filtering, duplicate implementation, or overwriting the helper by analytics/API |
| analytics/API | `tts_erp_v2/analytics/spu_profitability/**`, `tts_erp_v2/analytics/spu_roi.py`, API adapter, `docs/api/external-api.md` when released | consume or compare the approved helper; typed price seam, SQL, wire, sort, snapshot | direct DOM, second focused calculation, replacing old profit fields, or duplicating `_price_math.py` |
| shared UI | `tts_erp_v2/templates/pages/spu-profitability.html`, `static/js/spu-profitability-page.js`, `static/js/spu-roi.js`, `static/js/focused-spus.js`, relevant CSS | one Prices box, grouped columns, kernel races/a11y/mobile | price formula in browser, new global toggle, focused membership enrichment |
| real E2E | `tests/support/spu_price_stats_live.py`, `tests/browser/test_spu_price_stats_live.py` | cold-start uvicorn, isolated DB seed/auth/browser/network evidence | external :9877, mock-only proof, production env, optional/skipped acceptance |
| integration-only | temporary integration worktree and final acceptance records | merge already reviewed lanes, run required checks, reconcile docs | modify component implementation while integrating |
| this lane | `docs/design/spu-price-statistics.md` | companion design only | any source/test/application edit |

Projection owner changes are now present in the synchronized `origin/master` merge (lane HEAD includes merge `d0d5607`; this lane did not edit those source/common files). Price implementation still requires successor lanes to re-read the merged contracts and resolve any field/path drift before coding; this document only links to them and does not modify their ownership.

### 9.2 Dependency graph

```text
price-stats-design (this document)
  ├── price-stats-data: schema → producer parity → backfill contract
  │       └── price-stats-backend: observation query → CNY/median → HTTP
  ├── price-stats-backend + existing shared page kernel
  │       └── price-stats-ui: summary/grouped columns/profile/race/a11y
  └── price-stats-data + price-stats-backend + price-stats-ui
          └── price-stats-e2e: real isolated DB → uvicorn → API → Playwright
                  └── independent Terra review (data / API / UI / E2E)
                          └── integration-only Luna merge + final owner acceptance
```

No child should modify public common files before the projection owner releases the relevant successor scope. If an approved product decision changes authority, unknown gift inclusion, fractional quantity, or FX fallback, stop with `NEEDS_CONTEXT`, update this doc first, and do not infer a policy from existing code.

### 9.3 Gates, commits and handoff

- **Gate 0 — design:** this file reviewed for current-vs-target facts, exact wire fields, SQL median, raw provenance, no fallback, and evidence boundary. Commit `docs: ...价格统计技术方案` on this branch.
- **Gate 1 — data:** handwritten parser parity and test-shaped migration tests RED/GREEN; commit only owned data paths; dry-run/backfill remains human-only.
- **Gate 2 — backend:** numeric oracle + isolated HTTP/DB tests; prove totals reaggregation and same-response FX/current-cost-basis consistency; commit backend paths only.
- **Gate 3 — UI:** real browser contract verifies nested grouped Tabulator headers and accessible interactions on both profiles; commit shared UI paths only.
- **Gate 4 — E2E/review:** dedicated live test uses actual isolated DB/API/browser. A missing browser, verified app lifespan, or DB isolation is blocker; do not skip to green.
- **Gate 5 — integration:** fresh Terra review findings are fixed by original component writer, then integration-only merge checks exact commit set, `git diff --check`, isolated fast and dedicated E2E evidence. Production deploy/backfill stays human-owned.

Each handoff must include branch/HEAD/base, exact changed paths, commands and results, test DB identity, residual risks, no staged foreign files, and whether push succeeded. This doc lane does not mark application components ready or claim final acceptance.

## 10. 当前文档交付记录

- 本恢复运行只写本文件；未添加测试、未执行数据库/迁移/服务/浏览器、未声明 E2E 通过。
- 已同步最新 `origin/master` 到本 lane 专属 worktree；同步带来的 projection/common source changes 是上游合并历史，不属于本 lane owned edits。后续实现者必须在各 successor lane 重新核对合并后的 API/types/UI seams。
- 本轮验证：Markdown links/paths、JSON code blocks/duplicate keys、加权 oracle 数学、SQL/Markdown structural checks、`git diff --check`；只 stage 本文件并提交/推送 doc branch。
- source-evidence alignment：parent review inherited production readback（2026-10-05 11:52 UTC，2176 orders/2231 item keys，original 2230/2230 equal，paid 2221 exact + 9 ≤0.5 VND）与 Feishu revision `247` 支持 `line_items.original_price`/`sale_price` authority；本 lane 未独立 re-fetch。父订单 paid whitelist 复用 `PAID_SALES_ORDER_STATUSES`/业务 §4.1，line status 仍 raw-preserved。gift/absence、currency provenance 和 raw quantity contradiction 仍需窄 READ ONLY probe；不宣称 implementation readiness。
- 若后续 push 凭据或网络不可用，必须报告准确 local HEAD 与 unpushed 状态，不 force-push、不改 remote、不将未推送伪装成完成。
