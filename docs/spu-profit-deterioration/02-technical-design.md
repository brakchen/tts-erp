# SPU 盈利表现对比：技术设计

> 2026-10-24 修订，开发目标契约，未声称已实现/上线。[PRD v3](01-product-proposal.md) 是功能权威；[盈利计算契约](../business/spu-profitability.md) 是业务口径。旧阈值、严重程度、历史确认设计不再适用于本页。

## 1. 所有权与最小边界

- 盈利事实/公式/金额/ROI/全局去重：`analytics.spu_profitability`；复用现有 SQL 与 Decimal，不复制公式。
- 本页范围、日期校验、对比编排、事实标记：`analytics.spu_deterioration_alert` 内的独立比较读取 seam；不再经过旧 evaluator/config/sample gate。
- HTTP：现有 `/v2/analytics/spu-profit-deterioration`，只读授权、严格参数和序列化。
- 浏览器：原值与事实显示、请求生命周期、展示性的 `displayChange`，不拥有范围、盈利公式、订单总数、事实标记或日期上限。
- 保留页面 URL、权限点及全站导航 owner。此次修复不承担整页 React 迁移、全站 shell 改造、生产发布或删除旧物化表/配置。

ArchitectureReviewRequired=yes：producer/consumer 契约改变、只读一致性和日期来源需要跨边界验证。新逻辑不追加到四千行 canonical implementation；只暴露既有 snapshot-aware 读取的最小公共组合出口。旧 job/配置若有独立消费者则保留，不再决定本页可读性。

## 2. 数据流与一致性

```text
shop-local availability + requested anchor
  -> validated date and fixed recent-7-day analysis SPU scope
  -> canonical previous/current profitability in ONE read-only REPEATABLE READ snapshot
  -> one comparison item per SPU + distinct full-scope period order totals
  -> readonly HTTP -> page/details
```

日期依据、活动范围、两期事实共享计算快照及 `calculatedAt`。估值采用统一盈利 owner 的当前成本/汇率和未结算平台费估算，不平均日 ROI；历史经营日期不意味着历史固定会计值。重复调用公共函数时不得无意产生不同的计算时间/快照，新增出口应复用现有 snapshot-aware 实现。

## 3. 周期与范围

截至日 A 为后台实际可用的店铺当地完整日：

| windowDays | current（含首尾） | previous（含首尾） |
| --- | --- | --- |
| 7 | A−6..A | A−13..A−7 |
| 3 | A−2..A | A−5..A−3 |

普通范围由 A−6..A 的活动确定，与所选 3/7 窗口无关：累计已付款订单至少 3 且近期有单的普通样本；近期实际零单但正广告费作为例外保留，不受累计不足 3 单排除。累计仅指库内可用历史，并截止 A。明确精确 SPU 主键查询绕过活动排除；非法/不存在/跨店选择不得悄悄回退整店。允许 `activity=all` 明确调查其他范围，说明已应用条件和数据限制。

某一期零单且广告消耗 >0 的事实标记在后台基于未舍入事实计算。缺失为 unknown/null，不能转成零或 false。ROI 能算则正/零/负正常返回，不能算提供具体原因；保留其他可用值。不输出 severity、sampleStatus、confirmationStatus、thresholds 或策略结论。

## 4. 日期依据与旧结果

`latestAvailableDate` 必须结合必要来源的可用状态，且不晚于店铺当地昨天。区分源数据日期、读取/计算时间、同步观察时间；不能用 MAX(order time)、MAX(ad day)、cursor update-time 或生成快照的时间单独声称所有源数据齐全。

实现须披露每项依据及限制；已知缺来源/范围或无法建立可用上限时返回类型化不可用，不伪造默认昨天或成功空列表。成功空采集和真实零事实应能与未采集区分；不要求所有订单已经结算。

本版本采用 **observed-data availability（观测数据可用性）**，不是穷尽上游采集的认证：在同一快照取订单、售后、财务最近按店归因、已完成且 `rows_failed=0` 的成功作业；推定操作截止日为各作业 `started_at` 的店铺当地日期减一，再与当地昨天及该店相关广告日依据取保守交集。用开始而非结束时点避免跨日作业过度声称覆盖。不能将该推定值命名为已记录的 query-through；当前 JobResult/extra 未保存这样的边界。

广告使用与 canonical 盈利相同 endpoint 的 seller/advertiser/campaign 日级观测，结合固定 14 天 envelope 检查已知缺口（窗口切换不改上限），可复用成功空响应作为“已观测空采集”的依据；不要求所有历史 campaign 永远有行，不声称已发现全部 campaign/页。`coverage.status="limited"` 必须附具体来源、推定方法、观察时间、已知缺口和限制，不能只有笼统标签。保留更晚失败的说明，不抹掉之前可读数据。检查订单行解析/关联的未解决 issue；仅 `rows_failed=0` 不能把已知缺失变成零单或无单投放事实。必要来源完全缺失或已知缺口影响某项事实时，对应事实不可用/请求不可用，不用成功全零替代。

此规则是本页可用性策略，不新增生产采集认证、schema 或插件改造门槛。来源审计已明确：缺少完备 manifest 本身不违反 PRD，但假零、空结果掩盖缺失、或计算时间推进日期上限会违反 AC-010/011/021/022。

有合法可用上限后，默认取该日；请求更晚日期 422 并返回上限，禁止静默钳制；更早历史日期允许读取。旧结果可用时 `stale=true` 仍返回并显示实际数据日期及“数据未更新”，不受旧告警任务 enabled/config version 影响。`calculatedAt` 变化不代表数据更新。

## 5. HTTP 查询契约（本次 producer/consumer 冻结字段）

| 参数 | 规则 |
| --- | --- |
| shop_pk | 必需正整数；只读访问沿用既有授权；校验对应店铺 |
| spu_ids | 可重复内部主键，最多 100 个正整数；空/非法不扩大查询 |
| window_days | 单一 3 或 7，默认 7；多窗口、1/全部等返回 422 |
| activity | `recent` 默认，或 `all`；精确调查标记活动排除不生效 |
| anchor_date | 当地 ISO 日期；默认后台最新可用日；越界 422 |
| limit / offset | 默认 100/0，1..500 / >=0；只影响列表，不影响业务统计 |

旧 layer/severity/state/sample 筛选退出此 endpoint，不得被忽略后伪装成成功查询；返回明确修正提示。旧物化诊断函数可保留给独立内部消费者，不为本页新增第二条兼容业务路径。

响应形状保持 `{items, total, totals, meta}`：

### items（一个 SPU 一行）

- `shopPk`, `spuPk`（内部）, `spuId`（业务 ID）, `windowDays`, `anchorDate`。
- `previousRoi`, `currentRoi`；`previousNetProfitCny`, `currentNetProfitCny`；`previousSpendCny`, `currentSpendCny`：Decimal 字符串或 null。
- `previousOrderCount`, `currentOrderCount`：int 或 null，真实零不转空。
- `previousNoOrderAdSpend`, `currentNoOrderAdSpend`：bool 或 null，由后端未舍入事实决定。
- `previousRoiUnavailableReason`, `currentRoiUnavailableReason`：可空的具体说明；`warnings` 为数据/估值限制说明列表。
- `basisCalculatedAt`；`drilldown.pageUrl` 为完整的既有盈利页面 URL，使用该页面实际接受的业务 SPU ID 和日期字段；`drilldown.profitabilityUrl` 如保留则使用 API 自身正确的内部主键契约。链接应说明对应期间且浏览器历史可回原查询。

### totals（完整已应用范围）

`analysisSpuCount`, `previousOrderCount`, `currentOrderCount`, `unavailableReason`。SPU 去重；每期订单跨 SPU `COUNT(DISTINCT order_pk)`，不得相加分页/批次/行数。不可用值为 null 并说明，不伪造 0。`total` 只是分页记录数。

### meta

- `requestId`, `shopPk`, `anchorDate`, `latestAvailableDate`, `windowDays`, `reportingTimezone`。
- `periods.current.{startDate,endDate}` / `periods.previous.{startDate,endDate}`（含首尾）。
- `dataAsOf`（实际数据日）, `calculatedAt`（UTC 计算时间）, `stale`。
- `coverage`（真实来源依据、可用/不足状态及原因；不预设 materialized=true/stale=false），`warnings`（说明列表）。
- `activityBypassed`（精确调查绕过），`scopeDescription`（已应用范围说明）。

2xx 只表示契约读取/处理成功。401/403 权限、422 参数/日期、503 必要来源或读取不可用分开。错误保留请求编号；日期错误还给出 `latestAvailableDate`；HTTP 层不泄露异常、SQL 或凭证。

## 6. 页面与失败恢复

只呈现 7 天主参考/3 天近期观察、店铺/SPU/日期/活动范围、两期原值及订单/消耗、范围统计和盈利详情。保持现有共享导航，最小专属样式；本页不再编辑 runtime config。

差额 = 本期−上期；仅两值存在且上期 >0 显示百分比。它们是展示值，不形成状态或总计。两项指标方向、3/7 天方向可不同。零广告费用 ROI 无解、事实缺失和真实零/负值分别解释；3 天有短期波动说明。

加载新条件时关闭旧明细、清除旧列表/汇总/期间说明；所有请求（包含店铺/日期依据初始化）受 latest-wins 约束，旧响应和旧错误都不能覆盖新条件。失败保留输入并可重试，真实空范围说明可调整条件，不称全店正常。非法 URL/SPU/window 不静默扩范围，保留输入与错误。

详情使用原生链接/按钮，不能拦截链接 Enter；打开/关闭有焦点进入与返回。当前/上期无单投放标注含文字/结构信号，不只靠颜色。桌面表格和窄屏卡片必须能读取相同信息。

## 7. Verification / Retirement

AC-001～023 的测试映射与实际执行见 [04](04-test-plan-and-results.md)，顺序见 [03](03-implementation-plan.md)。公共盈利 seam、真实查询去重/时间边界、HTTP 错误与 browser consumer 均需验证；mock、构建或截图不能单独证明全部验收。

退休本页旧告警控件、写请求、错误统计和 stale 拒读。保留旧后台 job/config/表仅因其独立消费者/迁移安全边界，不再影响本页；未做生产删除、配置发布或重启。未来清理这些后台资产须独立审计消费者与生产授权。
