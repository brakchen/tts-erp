# SPU 盈利表现对比：实施与修复计划

> 2026-10-24 修订。依据 [PRD v3](01-product-proposal.md)；本文替代旧阈值告警迁移计划。实施授权来自本次用户请求，不含生产迁移、同步、重启或配置发布。执行证据见 [测试记录](04-test-plan-and-results.md)。

## 1. Goal / Architecture / Tech Stack

在现有 `/v2/pages/spu-profit-deterioration` 上实现 R-001～006、AC-001～023：7 天主参考、3 天辅助，显示两期盈利、订单和广告消耗，保留无单投放及旧结果，服务端限制可选日期，支持查证和失败恢复。不得恢复阈值、严重程度、历史确认或策略建议。

Python/FastAPI/SQLAlchemy/PostgreSQL 保持不变。本页仍为现有模板与脚本，不借修复批量迁移 React 或复制全站骨架。计算复用 `analytics.spu_profitability`，不修改统一盈利公式。新对比编排位于 `analytics.spu_deterioration_alert` 既有领域边界，HTTP 仅校验、授权和序列化，浏览器只呈现与计算明确命名的展示差异。

## 2. Baseline / Authority / Snapshot

- 产品：`01-product-proposal.md` v3 是功能权威；旧 02/03/04 的告警描述不是待实现需求。
- 业务：`docs/business/spu-profitability.md`；公共 deep-module seam：`analytics.spu_profitability.read_overview`。
- 安全/测试/工作流：`AGENTS.md`、`docs/guides/{agent-safety,agent-testing,agent-git-workflow,frontend-development}.md`。
- TaskStartSnapshot：独立 `fix/spu-compare-repair`，源 `origin/master` 为 `4bcef08df77172b6cb48007267d3927e4078ca19`；初始干净；主工作区 foreign WIP 不动。实时文件归属与 child reservation 仅在 tower-do lane `spu-compare-repair`。
- ArchitectureReviewRequired：是；本次改变查询与 UI 契约，需审查 producer/consumer、日期来源、快照一致性和旧路径退休边界。

## 3. Change Necessity / Risk / TDD Route

Decision: **code-change**。现有接口绑定旧告警配置、独立 layer 行、样本筛除和 stale 拒读，页面默认 confirmation/sample sufficient；仅改文档或文案无法交付 PRD。

风险为高：财务展示、查询范围、订单去重与日期上限跨 producer/consumer。TDD Mode: auto；TDD Route: strict（本次已记录的风险路由）。每个修复切片先通过唯一隔离入口观察目标断言失败，再实现并验证 GREEN；文档本身不做 RED。测试绿色只证明覆盖范围，不等于产品全部验收。

PatchShape / Ripple：修复领域查询 owner 与 UI consumer，不在浏览器重推样本资格或告警状态。保留 canonical 盈利精度、店铺当地日期和原权限。Minimality：不新增盈利引擎、数据源同步或 schema；旧告警物化/配置无关的公共消费者不顺带删除。Pre-Edit Owner-Fit：现有千行页面含大量无关配置编辑职责，移除这些职责后重写本页只读编排；新对比逻辑不加进四千行 canonical implementation。

## 4. 独立交付切片

### A — 审计与契约收敛

三个只读 subagent 分别检查后端事实/查询、页面交互、AC/测试覆盖。父会话核对发现并更新 02/03/04 与 API 契约，不以旧测试反推需求。确认日期来源的真实含义：最新事实、游标 update-time、计算时间均不自动等于完整数据日期；对未知覆盖明确说明，不能伪称齐全。

### B — 后端 producer

文件：`tts_erp_v2/analytics/spu_deterioration_alert/`、`tts_erp_v2/api/v2/spu_deterioration_alert.py`、相关 analytics/API tests。

1. 先 RED：默认唯一 7 天、只接受 3/7；日期越界 422 且返回上限；不受旧 enabled/config/severity/sample/layer 影响。
2. 建立店铺当地可用日期依据，区分没有可用依据、旧数据、合法历史日期、日期越界；在同一只读一致性快照读取日期依据与两期 canonical 盈利。
3. 范围以 anchor 近 7 天固定；普通样本沿用累计已付款至少 3 单且近 7 天有单；近 7 天零单但正广告费例外保留；精确内部主键查询不退回整店，窗口切换不改变范围。
4. 每 SPU 一行，正/零/负/缺失区分；真实事实标记由后台给出，不按舍入值判断。汇总在完整入选范围按订单全局去重，不相加行级订单数。
5. GREEN：周期、日期、无单投放、精度/null、跨 SPU 同订单、分页/窗口稳定性与错误契约。

### C — 页面 consumer

文件：`static/js/spu-profit-deterioration.js`、专属 CSS、模板及 browser tests。独立 worktree 单 writer，先冻结 B/C 响应契约再并行修改。

1. 先 RED：7 天默认、3 天辅助，无告警控件/配置编辑/样本门槛。
2. 店铺/SPU/日期/活动范围/分页可恢复；非法输入保留；日期 max 来自后台；改变条件重置分页并关闭旧明细。
3. 原值、展示差额/百分比、两期订单/消耗、无单投放事实紧邻显示；百分比仅正的上期分母可用；ROI 负值不得隐藏。
4. 加载与失败清除旧结果、汇总和明细；序号或取消机制覆盖所有异步入口，旧请求不能覆盖新条件；401/403/422/503 与真实空结果分开。
5. 详情同店同 SPU 同日期，键盘打开/关闭/焦点返回；桌面和窄屏能读全部信息，不只靠颜色。
6. GREEN：浏览器真实交互回归、root prefix、迟到响应、失败恢复、URL、键盘与窄屏。

### D — 集成验证与交付

父会话集成两个明确提交的组件，更新 `tests/api/test_pages.py` 中被 PRD 废弃的旧 UI 断言，更新必要 API 文档。fresh-context reviewers 独立检查业务与交互/验收。所有有效问题修复后重跑最小影响集。

精确命令按 runner 支持范围：

```bash
bash scripts/envsetup/install-test-deps.sh --check
bash scripts/test_isolated.sh api tests/api/test_spu_deterioration_alert_api.py tests/api/test_pages.py
bash scripts/test_isolated.sh unit tests/analytics/test_spu_deterioration_alert.py
bash scripts/test_isolated.sh browser tests/browser/test_spu_deterioration_alert_page.py
bash scripts/test_isolated.sh e2e tests/e2e/test_spu_deterioration_alert_smoke.py -rs
bash scripts/test_baseline.sh
```

新测试文件以最终记录命令为准。只在隔离 clone 内测试；依赖失败修前置，不改用直接 runner。fast baseline 按项目规则每正常阶段一次，失败仅重跑最小 selection；不更新 master baseline 来吸收失败。

## 5. Compatibility / Retirement / Stop

保留页面 URL、权限点、内部 SPU 主键和 canonical 盈利公式。本次 API 从旧告警模型收敛为 PRD v3 的比较模型；废弃筛选不得悄悄忽略后扩大范围。错误码、响应字段与调用方在技术/API 文档同步。

Retirement Track：本页的阈值编辑、severity/state/sample/layer、告警计数和错误 freshness 叙述退休；旧 job/config/物化存储若仍有独立消费者则原样保留，但不再决定本页可读性或对比结果。不会删除源事实或生产表。生产生效必须走人类发布流程。

遇到文件归属、不可证明日期来源、产品冲突、权限或生产动作边界则明确记录并继续独立可做项，不猜测或将部分工作标完成。完成要求：AC 证据、零新增稳定失败、独立复审、提交/推送/合并流程证据与 board 对账；缺失的浏览器/运行时验证必须诚实列出。
