# SPU 利润劣化告警：实施计划

> 状态：目标迁移计划。它不授权生产 migration、配置发布、服务重启或数据写操作。

## 1. 目标

将当前“两层独立决策行 + 用户 layer 筛选”迁移为“单一当前 AlertDecision + 内部 persistence evidence”，同步明确 totals grain、前后端计算边界和测试权威。

## 2. Phase 0 — 文档权威收敛

1. 建立本目录并拆分产品、技术、回测、实施、测试文档。
2. 在根 `AGENTS.md` 明确前端展示性简单计算与后端业务计算边界。
3. 更新 business/API/schema/source/test 中的旧文档链接。
4. 退休旧单体 design 和旧 Aegis implementation plan，确保没有活动引用。
5. 把与当前产品决定相反的测试标记为待重写，而不是恢复旧 UI。

## 3. Phase 1 — 决策模型

1. 由 owner 确定 ROI absolute/relative AND/OR。
2. 新增纯领域 `AlertDecision`/`ConfirmationStatus`，组合 current 与 shifted evidence。
3. current evidence 决定用户可见窗口与基础 state；persistence evidence 只影响 confirmation status/最终升级规则。
4. 明确 recovered/insufficient/unavailable 的转换表。
5. 保留 previous≤0 的显式状态与 Decimal/null 语义。

## 4. Phase 2 — 物化与 API

1. 将替换边界改为 `(shop_pk, anchor_date)`；增加跨时区两轮测试。
2. job 校验上游成功水位，并按店铺补齐有界缺失 anchors。
3. 可以暂时保留内部 layer evidence rows，但 read adapter 组合成一个 `AlertDecision`。
4. API 新增 `confirmationStatus`、sample reason、真实 coverage/requestId/warnings。
5. API 直接返回 `currentOrderCount/alertSpuCount/criticalSpuCount`；禁止从决策行计数冒充业务 totals。
6. 审计 `layer` query 的内部消费者；无活动依赖后删除，或给出明确兼容退休版本。

## 5. Phase 3 — 页面与配置

1. 页面保持无 layer/sample 下拉的简化主流程。
2. summary 展示当前 evidence 与 confirmation status，不展示两条独立层记录。
3. `displayChange` 仅用于箭头、差额和展示百分比；所有告警决策继续使用服务端字段。
4. 修复键盘 drill CTA、error/loading 残留旧 summary、publish 后配置刷新。
5. 管理员配置 UI 解释内部 evidence；评估是否把固定 `maturityDays` 移出 payload。
6. 视觉测试只验证文本、图标、结构信号和对比度，不锁死斜纹。

## 6. Phase 4 — 回测与发布准备

1. probe 复用生产 evaluator。
2. 使用 T-1 + shop-local IANA 重跑；记录 evaluator/config hash。
3. owner 审阅 distinct SPU 告警量、样本覆盖、persistence 和 recovery。
4. 通过 runtime config 正式发布；seed fallback 继续显示“回测暂定”。
5. 人工执行 migration/restart/publish；agent 不执行生产动作。

## 7. 发布/回滚边界

- migration 仅通过 `bash scripts/test_isolated.sh --refresh-template ...` 验证；
- jobs/sync_worker/analytics 修改后由人工重启 sync worker；
- API/static 修改后按正式流程重启 API；
- 回滚保留 snapshot/config audit；
- stale/disabled 最终行为必须先在产品方案定稿。

## 8. Definition of done

- 产品方案没有用户可见 layer；
- 一个 API item 对应一个用户 AlertDecision；
- 三个顶部卡片的 grain 有独立后端证据；
- displayChange 不参与业务决策；
- 跨时区替换和多日 catch-up 有集成测试；
- 新回测与生产 evaluator/timezone/anchor 一致；
- 旧 design/plan 没有活动引用；
- 所有 isolated tests 零新增失败。
