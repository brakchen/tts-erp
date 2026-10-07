# SPU 利润劣化告警：产品方案

> 状态：目标产品契约。当前实现与本文的差异由 [`03-implementation-plan.md`](03-implementation-plan.md) 跟踪。

## 1. 产品目标

页面帮助经营人员回答三个问题：

1. 当前哪些 SPU 的利润表现值得处理？
2. 劣化主要表现为 ROI、净利润、由盈转亏还是亏损扩大？
3. 这个信号是否有足够样本，是否得到历史持续性证据支持？

页面不要求用户理解内部 evaluator、物化行、`fast/confirmation` 层或 runtime-config 数据结构。

## 2. 用户可见对象

用户看到的基本对象是一个 `AlertDecision`，grain 为：

```text
shop × SPU × anchorDate × windowDays
```

一条决策包含：

- 当前比较窗口的 ROI、净利润、消耗和订单证据；
- 服务端确定的 `severity`、`state`、`sampleStatus`；
- `confirmationStatus`，表示历史平移窗口是否支持“持续劣化”；
- 配置版本、计算时间和 drill-down。

`fast` 与 `confirmation` 只描述服务端的两组 evidence，不是两个用户可见告警实体，也不提供普通页面筛选控件。

### 2.1 confirmationStatus

建议 wire 枚举：

- `confirmed`：当前告警成立，历史平移 evidence 也达到确认条件；
- `unconfirmed`：当前告警成立，但历史 evidence 未达到确认条件；
- `recovered`：历史曾劣化，当前已经恢复或扭亏；
- `insufficient`：任一 evidence 样本不足；
- `unavailable`：缺事实，无法判断；
- `not_applicable`：当前没有告警，不需要确认。

最终枚举需要与 ROI AND/OR 决策一起由 owner 确认。

## 3. 页面统计卡片

统计值必须使用业务 grain，由后端直接返回；前端不得通过 items 或 snapshot 行求和。

推荐契约：

- `currentOrderCount`：base scope 内、所选窗口 current period 的 `COUNT(DISTINCT order_pk)`；
- `alertSpuCount`：base scope 内最终 severity 为 warning 或 critical 的 `COUNT(DISTINCT spu_pk)`；
- `criticalSpuCount`：base scope 内最终 severity 为 critical 的 `COUNT(DISTINCT spu_pk)`。

其中 base scope 包含 shop、显式 SPU 范围、activity、anchor 和 window；不受分页影响。severity/state 只是明细钻取条件，不改变顶部卡片。若 owner 希望 severity/state 改变卡片，必须在本文明确，不能由实现猜测。

`decisionRowCount` 仅供诊断，不得显示为“订单”或“SPU”。

## 4. 页面控制

普通页面保留：

- 店铺；
- 精确 SPU scope；
- 1d/3d/7d 窗口；
- severity；
- anchor date；
- 近 14 天出单大于 3 单的 activity 开关；
- 状态列表头循环筛选；
- 刷新。

普通页面不显示：

- `fast/confirmation` 层筛选；
- sample status 下拉；
- evaluator、rollout 或内部配置对象。

`sample` 和内部 evidence 可以保留在 API/URL 作为受支持的诊断入口，但不能成为业务用户必须理解的主流程。

## 5. 明细与展示计算

明细展示上期/本期 ROI、净利润、消耗、订单数和广告订单数，以及服务端的状态、阈值、配置版本和样本解释。

前端允许基于**已经展示的值**计算非权威的：

- 上升/下降箭头；
- 展示差额；
- 展示百分比（分母为 0、负数或 null 时按组件规则显示“—”）。

这些值统一称为 `displayChange`：

- 不参与 severity/state/sample gate；
- 不参与筛选、排序的服务端业务语义、顶部 totals 或持久化；
- 不能覆盖或冒充服务端 `decisionDecline`；
- 如果二者可能因精度不同而不一致，页面注明“展示变化按页面值计算；告警判定使用服务端精确值”。

ROI、净利润、阈值命中、状态分类、跨记录聚合、去重和财务比例必须由后端使用 Decimal/数据库 numeric 计算。

## 6. 页面状态

- loading：明确正在加载，新 anchor 未返回前旧结果不能冒充新结果；具体使用 skeleton 或稳定占位行由 UI 决定。
- empty：说明当前 scope 没有达到阈值的告警，并显示 anchor/config source。
- insufficient/unavailable：用业务可读原因解释缺 spend、orders、ad-orders 或事实。
- error：显示可读错误、requestId 和重试；401/403/422/503 不伪装成空结果。
- warning：必须同时有文字、图标/徽章和非颜色结构信号；不强制斜纹这一具体 CSS 技法。
- drill-down：键盘和指针均可进入现有 SPU 盈利详情。

## 7. stale 与 disabled（待 owner 最终确认）

推荐：

- 有最后成功快照时，stale 返回只读旧快照 + 明显 stale banner；从未成功、配置不可解析或 basis 不一致时才 503。
- `enabled=false` 停止生成新告警，但保留最后快照只读并显示 disabled banner。

如果产品希望 disabled/stale 一律隐藏数据，应在本文改成唯一语义，并同步 API/测试；不能同时保留两套行为。

## 8. 非目标

- 不提供自动短信/邮件/IM；
- 不提供跨店排名或机器学习阈值；
- 不在浏览器计算盈利事实或告警结论；
- 不要求业务用户理解内部 evidence layer；
- 不把回测 seed 包装成正式生产阈值。
