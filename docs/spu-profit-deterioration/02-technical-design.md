# SPU 利润劣化告警：技术设计

> 状态：目标技术契约。盈利公式以 `docs/business/spu-profitability.md` 为唯一业务口径。

## 1. 所有权与不变量

- 盈利事实、ROI 和净利润：`analytics.spu_profitability` canonical seam。
- 告警 evidence、sample gate、state/severity 和最终决策：`analytics.spu_deterioration_alert`。
- 生效阈值：`config.runtime_config_items` 已发布 revision；draft 不是运行时 authority。
- 历史：物化层保存 anchor、basis、配置 source/version/hash 和 Decimal 原值。
- HTTP：校验参数、授权、序列化；不重算业务结论。
- 浏览器：允许展示性 `displayChange`，禁止盈利公式、阈值、状态、去重和业务 totals。

金额和比率使用 Decimal/numeric；null 表示无解或不足，不能伪装为 0。报告日期按店铺 IANA 时区。

## 2. 数据流

```text
canonical profitability facts
        + published runtime config
        -> current evidence
        -> shifted persistence evidence
        -> one AlertDecision
        -> materialized history
        -> readonly API
        -> page / drill-down
```

## 3. 窗口与 evidence

anchor `A` 为店铺当地最新完整数据日 T-1。

当前 evidence：

| window | current | previous |
| --- | --- | --- |
| 1d | T-1 | T-2 |
| 3d | T-3..T-1 | T-6..T-4 |
| 7d | T-7..T-1 | T-14..T-8 |

历史持续性 evidence 整体向前平移 7 天：1d 为 T-8 vs T-9；3d 为 T-10..T-8 vs T-13..T-11；7d 为 T-14..T-8 vs T-21..T-15。

这两组 evidence 不能作为两个用户可见告警行。目标领域对象：

```text
AlertDecision
  shop_pk, spu_pk, anchor_date, window_days
  current_evidence
  persistence_evidence
  confirmation_status
  severity, state, sample_status
  decision_thresholds/config basis
```

实现可暂时保留内部 `layer=fast|confirmation` 行作为 evidence storage，但主 API 必须组合成一个用户决策；`layer` 不属于产品筛选。

## 4. sample、state 与 severity

先判 evaluability/sample，再判 state，最后判 severity：

1. 任一窗口缺 required fact：`unavailable`；
2. ROI 无解或 warning gates 不满足：`sample_insufficient`；
3. 只有两窗 facts、ROI defined 且 gates 满足时才 `sufficient`；
4. previous ROI/net profit ≤0 时不做非法百分比，使用 `profit_to_loss`、`loss_expanding`、`loss_to_profit`；
5. warning/critical 分别使用自己的 gates 与阈值，边界 inclusive。

ROI absolute 与 relative 条件使用 AND 还是 OR 仍是阻塞决策；确定前不得把任一版本写成“回测证明”。

## 5. 业务 totals

API 必须在完整 base scope 内直接计算：

- `currentOrderCount = COUNT(DISTINCT order_pk)`；
- `alertSpuCount = COUNT(DISTINCT spu_pk)` where final severity in warning/critical；
- `criticalSpuCount = COUNT(DISTINCT spu_pk)` where final severity=critical；
- 可选诊断字段 `decisionRowCount`，不得映射成订单/SPU 标签。

SPU 行订单数不能相加得到订单总数；同一订单包含多个 SPU 时仍只计一次。

## 6. Snapshot 与物化

建议内部 evidence 唯一键继续使用：

```text
(shop_pk, spu_pk, anchor_date, window_days, layer)
```

但替换原子边界必须至少是 `(shop_pk, anchor_date)`，不能只按 `anchor_date` 全局删除；不同店铺可能在同一 UTC 时刻处于不同当地日期。

job：

- 在一个只读 `REPEATABLE READ` 快照读取 facts/config；
- 在受控事务写派生 snapshot；
- 失败保留最近成功快照；
- 检查订单、售后、广告等必需上游成功水位；
- 在有界窗口内补齐每店缺失 anchors，而不是只重算当前 T-1；
- 不记录 shop/SPU/订单标识到日志。

## 7. Runtime config

key：`analytics.spu_profit_deterioration_alert.v1`。

published revision 是唯一 authority；缺失时可显式使用 `seed_fallback/回测暂定`，published revision 无效时 fail closed。

当前 payload 的 `fast/confirmation × 1/3/7 × warning/critical` 是内部算法配置。普通业务页面不因隐藏 layer 而复制第二份配置；阈值编辑保留在管理员抽屉或运行配置页。

`maturityDays` 在 v1 固定为 7，如果不允许修改，应在下一版本从用户配置中移为算法常量，避免虚假可配置性。

## 8. API 目标契约

主查询保持 readonly，base query 支持 shop、SPU、window、severity、state、anchor、activity 和分页。普通页面不发送 layer；兼容期若保留 `layer` 参数，仅作为诊断/旧客户端入口，并记录退休条件。

每个 item 至少返回：

- 当前 evidence 的 prior/current ROI、净利润、spend、order/ad-order；
- 服务端精确 `roiDecline/netProfitDecline`；
- `confirmationStatus` 与必要的 persistence 摘要；
- severity/state/sampleStatus 与 sample reason codes；
- anchor/basis/config source/version/hash；
- drilldown URLs。

meta 返回：timezone policy、calculatedAt、真实 coverage/lastSuccessAt/missing windows、warnings、requestId 和 readonly-safe effective config。

前端可从 wire display values 派生 `displayChange`，但 API 精确 decline 继续是告警判定与审计值。

## 9. stale/disabled

产品语义以 [`01-product-proposal.md`](01-product-proposal.md) 最终决策为准。技术层必须区分：

- never materialized；
- stale but readable；
- disabled with retained history；
- config unavailable/invalid；
- snapshot basis incoherent。

这些情况使用类型化错误/状态，不能都映射为 `SNAPSHOT_UNAVAILABLE`。

## 10. 可访问性与视觉边界

要求文本、图标/徽章、结构信号和键盘可达；颜色不能是唯一信号。不规定必须斜纹、某种 gradient 或具体 loading 组件。测试应断言用户可观察结果，不断言 CSS 实现字符串。

## 11. Observability

日志/指标覆盖 job、anchor、window、内部 evidence、配置 source/version、rows、warning/critical/sample counts、duration、snapshot age、fallback/invalid config、API stale 与 persistence denominator；全部 aggregate/redacted。
