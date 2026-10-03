# SPU 盈利 deep module — 接口与实现决策

> 本文只记录 deep module 的**接口与实现决策**。盈利口径（概念 / 公式 / 参数 / 预测）的
> 唯一事实文档是 [`docs/business/spu-profitability.md`](../business/spu-profitability.md)，
> 本文不复述任何口径公式；两者冲突时以事实文档为准。
> 状态：interface 已确认并进入实现。原 `docs/design/spu-profitability-module.md`，
> 2026-10-03 裁掉口径复述章节后迁入本文。

## 1. 模块边界与责任

- 同一个 deep module 同时负责 SPU 盈利明细、盈利大盘与盈利证据（范围与去重规则见事实文档 §2.5）。
- deep module 返回精确领域值（Decimal、数量、分类、盈利依据与状态），不返回为 HTTP 准备的字符串；
  HTTP adapter 负责 JSON 字段、金额/比例序列化与错误 envelope；前端只展示，不重新计算或修正精度。
- 盈亏、ROI 正负、未结算估算、默认成本、高退款警戒均由领域结果 / HTTP adapter 返回结构化状态；
  警戒阈值与 P&L 公式说明通过 `meta.presentation` 暴露。前端不得保存阈值、从金额/计数反推状态或复制业务公式。
- deep module 自己拥有 PostgreSQL 事实查询与归一化；调用者只提供盈利范围和数据库会话，不传递巨大 facts 对象。
- PostgreSQL 属于 local-substitutable 依赖，使用专用 test DB 验证；当前只有一个真实数据库 adapter，
  不建立假想 repository interface。纯公式计算可作为 implementation 内部 seam，但不暴露给调用者。

## 2. 计算时点与一致快照

- v10 保持实时计算，不预先写入利润日报；每次盈利计算使用一个只读、一致的数据库快照。
  SPU 盈利明细、盈利大盘、汇率和盈利证据共享同一个 `calculatedAt`，不得在一个结果中混用不同数据时点。
- 该要求是请求级一致性，不是跨请求强一致性：数据约十分钟同步一次，主表与稍后请求的盈利证据
  存在细微差异可以接受。
- 打开盈利证据时，deep module 在一个新的一致快照中同时重新计算该 SPU 与对应证据，并返回新的
  `calculatedAt`；不保存或重放旧利润快照。
- 估算不能静默冒充实际数据；结果必须带盈利依据（成本来源、结算覆盖、汇率时点与兜底警告）。

## 3. 已确认 public interface

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
- 历史 `fee_rate` query param 暂由私有 compatibility adapter 承接；它不进入新的 public interface，
  也不得扩散到新调用者。

## 4. implementation locality

- module：`tts_erp_v2.analytics.spu_profitability`
- interface：包级 `read_overview`、`explain_spu` 与 immutable domain types
- implementation：`_implementation.py` 拥有 PostgreSQL 查询和归一化；`_formula_v10.py` 是纯公式 seam；
  `_snapshot.py` 拥有请求级一致快照。
- adapter：`tts_erp_v2.analytics.spu_roi` 仅保留稳定 URL、参数校验、HTTP error envelope 与 wire serialization。
- frontend：只显示 adapter 返回的盈利结果和分解字段，不重新计算净收入、COGS、净利润或 ROI。

## 5. 旧模型退役约束

- `reporting.product_profit_daily` 只是旧版粗略毛利快照，不得新增消费者。
- 删除旧模型前必须先迁移或退役 `GET /v2/reporting/profit-daily` 与对应定时重算，再确认历史数据保留期；
  删表必须通过 destructive guard，并由用户手动执行迁移。
