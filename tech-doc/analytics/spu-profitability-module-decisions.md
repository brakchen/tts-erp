# SPU 盈利 deep module — 已确认决策

> 状态：interface 已确认并进入实现；业务约束仍以本文与 v10 口径为准。
> 业务口径唯一 truth source：`biz-doc/analytics/spu-roi-profit-calculation.md` v10。

## 1. 权威口径

- v10 是“SPU 盈利”的唯一权威口径。
- `reporting.product_profit_daily` 只是旧版粗略毛利快照，不得新增消费者。
- 删除旧模型前必须先迁移或退役 `GET /v2/reporting/profit-daily` 与对应定时重算，再确认历史数据保留期；删表必须通过 destructive guard，并由用户手动执行迁移。

## 2. 结果范围

- 同一个 deep module 同时负责 SPU 盈利明细与盈利大盘。
- 明细针对单个 SPU；大盘针对盈利范围内的所有 SPU。
- 两者使用完全相同的 v10 口径：`effective_sales`、`effective_order_count`、`refund_order_count/refund_rate`、`full_loss_order_count/full_loss_rate`、`domestic_cancelled_order_count/cancel_rate` 在单 SPU 范围必须逐项相等。
- `refund_rate` / `full_loss_rate` / `cancel_rate` 均为订单维度且分母统一为全部订单；金额退款率与件数全损率只能通过显式解释字段 `refund_amount_rate` / `full_loss_qty_rate` 暴露。
- 大盘的订单级事实必须在整体范围内去重，不能简单累加 SPU 行。
- 盈利范围跟随店铺、日期窗口与“是否包含非在售 SPU”。
- 文本搜索、排序与分页只改变明细展示，不改变盈利大盘。
- 同一个 deep module 负责盈利结果与盈利证据；盈利证据包括订单、结算、售后与广告事实，用于解释结果但不等同于结果。
- deep module 返回精确领域值（Decimal、数量、分类、盈利依据与状态），不返回为 HTTP 准备的字符串。
- HTTP adapter 负责 JSON 字段、金额/比例序列化与错误 envelope；前端只展示，不重新计算或修正精度。
- deep module 自己拥有 PostgreSQL 事实查询与归一化；调用者只提供盈利范围和数据库会话，不传递巨大 facts 对象。
- PostgreSQL 属于 local-substitutable 依赖，使用专用 test DB 验证；当前只有一个真实数据库 adapter，不建立假想 repository interface。
- 纯公式计算可以作为 implementation 内部 seam，但不暴露给调用者。

## 3. 计算时点与估算

- v10 保持实时计算，不预先写入利润日报。
- 每次盈利计算必须使用一个只读、一致的数据库快照；SPU 盈利明细、盈利大盘、汇率和盈利依据共享同一个 `calculatedAt`，不得在一个结果中混用不同数据时点。
- 该要求是请求级一致性，不是跨请求强一致性；数据约十分钟同步一次，主表与稍后请求的盈利证据存在细微差异可以接受。
- 打开盈利证据时，deep module 在一个新的一致快照中同时重新计算该 SPU 与对应证据，并返回新的 `calculatedAt`；不保存或重放旧利润快照。
- 允许使用 v10 已定义的估算：未结算订单使用平台费率基线；无人工价或货源价时使用成本兜底。
- 估算不能静默冒充实际数据；结果必须带盈利依据，包括成本来源、结算覆盖、汇率时点与兜底警告。

## 4. 广告系统 ROI

- 广告系统 ROI 与财务 ROI 必须分开：广告系统实际 ROI = 广告归因 GMV ÷ 广告实际消耗；历史字段 `roi_l0` 作为兼容别名保留。
- 最大可承受广告费 = 预计净结算收入 − 同范围采购成本 − 净结算未包含的其他必要成本。
- 广告系统保本 ROI = 广告归因 GMV ÷ 最大可承受广告费；分母 ≤ 0 或无广告归因 GMV 时为不可计算，不得填 0。
- 预计净结算收入沿用 v10：已结算实际到账 + 未结算净额估算；采购成本沿用同日期范围内的售出货本 + 海外取消全损货本，禁止收入/成本范围错配。
- 当前数据库尚未结构化录入退货运费、采购退款失败、提现费、汇兑损失、包装耗材等结算外必要成本。接口返回 `estimated_known_costs` 状态和 `ad_system_other_necessary_costs_not_modeled` warning；前端用 `≈` 标记已知成本下限估算，不得冒充最终保本线。
- HTTP 同时返回 `ad_system_actual_roi`、`ad_system_max_ad_spend`、`ad_system_remaining_ad_spend_capacity` 与 `ad_system_breakeven_roi`，并在 `meta.ad_system_roi` 说明公式、范围和缺失成本。

## 5. CNY 金额与汇率契约

- v10 的所有金额领域值、HTTP `items/totals` 金额和盈利证据金额统一为人民币 CNY；`ProfitabilityBasis.display_currency` 与 `meta.currency.display` 固定为 `CNY`。
- 广告消耗与广告归因 GMV 原生 USD，按 `USD × rates[CNY]` 换算 CNY；销售、退款、结算原生 VND，按 `VND ÷ (rates[VND] / rates[CNY])` 换算 CNY；采购成本原生 CNY，不再先换成 USD。`FxBasis` 必须保留快照的精确 `usd_cny`，不得通过已量化的倒数恢复。
- 同一个结果中的所有换算必须使用同一个数据库汇率快照；前端只加 `¥` 和格式化，不得二次换汇。
- ROI、退款率等无量纲比例不因展示币种改变；广告系统实际 ROI 可直接使用同源 USD 分子/分母计算以避免 Decimal 换算尾差。
- v10 只能使用数据库中的汇率快照。
- 禁止使用编译期固定汇率或无来源兜底汇率。
- 数据库中没有可用汇率快照时，整个盈利结果不可计算，不返回部分 `items` 或 `totals`。
- HTTP 返回 `503 Service Unavailable`，错误码为 `FX_RATE_UNAVAILABLE`。
- SPU ROI 页面必须显示整页错误状态：“汇率数据缺失，无法计算结果”，并提供重试入口；不得显示空表或全零。

## 6. 已确认 public interface

```python
read_overview(
    session,
    *,
    scope: ProfitScope,
    view: RowView,
) -> ProfitabilityOverview

explain_spu(
    session,
    *,
    scope: ProfitScope,
    spu_pk: int,
    evidence: EvidenceRequest,
) -> SpuProfitExplanation
```

- `ProfitScope` 只包含店铺、日期窗口与是否包含非在售 SPU。
- `RowView` 只包含搜索、排序、分页；它不能改变盈利大盘。
- `EvidenceRequest` 明确选择订单、结算、售后与广告证据。
- 两个入口各自建立一个只读 `REPEATABLE READ` 快照。
- `explain_spu` 在同一快照内重新计算 SPU 盈利与证据，二者共享一个 `calculated_at`。
- 领域结果保持 `Decimal`、`date`、`datetime` 与枚举；历史 `/v2/analytics/spu-roi` adapter 负责字符串格式化。
- 历史 `fee_rate` query param 暂由私有 compatibility adapter 承接；它不进入新的 public interface，也不得扩散到新调用者。

## 7. implementation locality

- module：`tts_erp_v2.analytics.spu_profitability`
- interface：包级 `read_overview`、`explain_spu` 与 immutable domain types
- implementation：`_implementation.py` 拥有 PostgreSQL 查询和归一化；`_formula_v10.py` 是纯公式 seam；`_snapshot.py` 拥有请求级一致快照。
- adapter：`tts_erp_v2.analytics.spu_roi` 仅保留稳定 URL、参数校验、HTTP error envelope 与 wire serialization。
- frontend：只显示 adapter 返回的盈利结果和分解字段，不重新计算净收入、COGS、净利润或 ROI。
