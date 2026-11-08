# 主题目录索引（docs/ 四段式主题目录）

> 状态：待评审
> 建立时间：2026-11-08 · 维护者：产品经理（受 Chief 指派）· 2026-11-08 修订（Chief 复核后）
> 扫描基线：本地克隆 `F:\MiniMax Work Result\agents\mavis\workspace\tts-erp`，分支 `chore/fix-test-server-address` @ `553f2231`。**结论基线为 `553f2231`**，不是 `origin/master` @ `0b92d97d`——两者 `docs/` 相差 2 个文件（`docs/handoff/bot-team-setup-2026-11-08.md` 新增、`docs/ops/tts-erp.md` 修改），而 §1 三条硬约束的依据正是前者，因此以 `553f2231` 为准。
> 计数复核（2026-11-08）：`553f2231` 的 `docs/` 有 **190 个 markdown**（`docs/README.md` 1 个 + 子目录 189 个），分布在 16 个子目录；`origin/master` @ `2b68b40a` 现有 **196 个**，多出的 6 个全部是 2026-11-08 的协调文档（`handoff/architecture-review-scope-2026-11-08.md`、`handoff/dev-env-checklist.md`、`handoff/p6-retirement-parallel-plan.md`、`handoff/p6-test-side-retirement-plan.md`、`handoff/test-baseline-verify-2026-11-08.md`、`handoff/topic-directory-index.md`），**不是新的主题方案**。按 190 去找文件会漏掉这 6 个；按 196 去找则要知道多出的都是协调文档。
> **本文是目录导航，不是需求依据，也不是功能完成声明。** 每个主题的业务结论仍以该主题目录下的 `01` 文档为准；本文只回答「有哪些主题目录、齐不齐、在做什么」。

---

## 1. 结论摘要

**按 `docs/<主题>/01..04` 四段式建的完整主题目录只有 2 个。**

| # | 结论 | 数量 | 主题 |
| --- | --- | --- | --- |
| 1 | 四段式齐全 | **2** | `spu-profit-deterioration`、`video-publish` |
| 2 | 编号齐全但与标准四段式不对齐 | **1** | `plugins/monitor`（01–05，且实际混了两个主题） |
| 3 | 只建了 01，缺 02/03/04 | **1** | `plugins/ads-data-sync` |
| 4 | 其它编号约定目录，**不是**主题目录 | 4 个子目录 + 4 个单文件目录 / 共 22 个文件 | `aegis/work/`（4 个工作记录目录）、`aegis/plans/`（1）、`architecture/adr/`（3）、`ops/incident-reports/`（1）、`plans/`（17） |
| 5 | 有方案文档但**没建主题目录**的候选主题 | **9** | 媒体资产、运行配置、RunningHub、店铺入驻、前端迁移等（见 §5） |

**给后续新主题的三条硬约束（依据 Chief 指示 + `docs/handoff/bot-team-setup-2026-11-08.md`）：**

1. 新主题一律建 `docs/<主题>/`，四段式文件名固定为 `01-product-proposal.md`、`02-technical-design.md`、`03-implementation-plan.md`、`04-test-plan-and-results.md`。
2. 01 写产品经理，02+03 写开发工程师，04 写测试工程师；缺哪一段就在 §2 表格里如实标缺，**不要用别的名字凑数**（`plugins/monitor` 就是反面例子，见 §3）。
3. 每个文档开头保留状态行（`docs/README.md` §2 要求）：待评审 / 待实施 / 已实施 / 已废弃。

**再加两条本次修订补上的约束（见 §6 第 8、9 条）：** 目录随主题开工而建、不预建空壳；写「已归档」前必须先核实代码里是否真的已退役。

**两个完整四段式目录的内容对齐情况：文件齐，口径也齐。** 早前版本称 `spu-profit-deterioration/`「文件齐了但内容口径没齐」，该结论已按 02/03/04 头部修订声明改正——详见 §2 末尾。需要单独注意的是**代码运行状态**（旧阈值 evaluator 写侧仍在生产跑），那不是文档口径问题。

---

## 2. 完整四段式主题目录（2 个）

| 主题 | 路径 | 四段式是否齐全 | 涉及业务领域 | 一句话说明 |
| --- | --- | --- | --- | --- |
| SPU 盈利表现对比与变化提示 | `docs/spu-profit-deterioration/` | ✅ 01 / 02 / 03 / 04 齐全（另有 `02a-backtest-report.md` 回测报告，编号带字母后缀） | SPU 盈利分析、实际 ROI、广告投放效率、运营看板 | 把旧的「阈值告警」产品方案改写成「7 天主参考 + 3 天看近期变化」的两期对比：看清同一 SPU 两期的 ROI、净利润原值及变化，**不做阈值告警**，不自动给加投/停投结论。 |
| TikTok 视频发布 | `docs/video-publish/` | ✅ 01 / 02 / 03 / 04 齐全 | 内容运营、视频发布、设备调度、人工确认工作流 | 一任务一视频一文案；**AI 报告执行成功不等于视频真的发布成功**，必须人工点「确认已发布」才把任务改成成功；同设备串行、不同设备并行；提交 Artemis 后参数永久只读。 |

**两个目录的最近更新（git 最后提交日期）：**

| 目录 | 01 | 02 | 03 | 04 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `spu-profit-deterioration/` | 2026-10-08 | 2026-10-27 | 2026-10-27 | 2026-10-27 | `02a` 为 2026-10-07 |
| `video-publish/` | 2026-10-08 | 2026-10-13 | 2026-10-13 | 2026-10-13 | — |

**关于「01 与 02/03/04 是否对齐」——已核实，对齐成立（2026-11-08 修订，改正旧结论）：**

早前版本写「`spu-profit-deterioration/01` 明确写了『02/02a/03/04 仍包含旧告警方案』，因此这个四段式目录内部 01 与 02/03/04 业务方案尚未对齐」。**这条不成立**，原因有三：

1. **02/03/04 已在 PRD v3 之后重写过。** 三份文件头部均写明：`02` = 「2026-10-24 修订……旧阈值、严重程度、历史确认设计不再适用于本页」；`03` = 「2026-10-24 修订。依据 PRD v3；本文替代旧阈值告警迁移计划……不得恢复阈值、严重程度、历史确认或策略建议」；`04` = 「2026-10-24 重建。权威为 PRD v3 AC-001～023、技术契约 02」。
2. **真正过时的是 PRD 01 头部第 7 行**那句「02/02a/03/04 仍包含旧告警方案，后续须对齐」——写于 2026-10-07，之后 02/03/04 已按 v3 对齐。该句已在本次修订中改掉。
3. **只有 `02a` 是旧方案**（脚本 `probe_spu_profit_deterioration_thresholds.py`、fast/confirmation seed、warning/critical），但它自己已标注「历史、不可直接外推到当前生产 evaluator……不是产品契约，也不是当前生产阈值的证明」——**这是该留着的历史证据，不是口径冲突**，不构成「未对齐」。

**仍需知道的一处真实状态差异（不是文档口径问题，是代码运行状态）：** 旧阈值告警方案在代码里处于**「读侧已退役、写侧仍在生产跑」**的半退役状态，详见 §5 的对应条目。文档已对齐，但旧 evaluator 代码仍在线并每天产出快照。

---

## 3. 编号存在但不完整或不对齐（2 个）

| 主题 | 路径 | 实际现状 | 涉及业务领域 | 一句话说明 |
| --- | --- | --- | --- | --- |
| Monitor 广告插件接口兼容 ＋ 最小探活 | `docs/plugins/monitor/` | 编号 01–05 **齐全**，但①`01` 是原始抓包记录，不是产品方案；②`03-verification.md` 不是 `03-implementation-plan.md`；③`04-health-reporting-requirements.md` 不是 `04-test-plan-and-results.md`；④**04/05 讲的是另一个主题**（插件探活），与 01–03 的「`post_campaign_list_v2` 接口兼容」不是同一件事 | 广告监控插件、Seller Center 私有接口协议、插件探活与飞书告警 | 01–03 是新旧广告列表接口兼容（URL 决定协议版本 + `table_v2` cell-row 解析，共用同一 Campaign 规范化出口）；04–05 是插件最小探活：四个业务字段上报、服务器端扫描、统一飞书群告警（ERP 已退役 `POST /v2/plugin/monitor/health`）。**两个主题共用一个目录。** |
| ads-data-sync 大重构 | `docs/plugins/ads-data-sync/` | ❌ **只有 `01-refactor-technical-design.md`**；缺 02 / 03 / 04。且这个「01」本身是技术设计，不是产品方案 | 广告数据采集插件、Outbox 可靠交付、同步降级策略 | 插件内部模型重构（Phase B）＋ 产品级增量（Phase C）的技术设计：分阶段交付、Outbox 可靠本地交付、ERP 不可用时不启动新采集先排空队列；不写店铺、不关广告、不发飞书。 |

**另有 3 个非编号文件混在 `plugins/monitor/` 里**：`zabbix-monitor.md`、`zabbix-https-cutover.md`、`zabbix-monitor.yaml`——不属于四段式体系，但确实在同一个目录，索引时容易误读。

---

## 4. 其它编号约定目录（不是四段式主题目录，别误当主题用）

| 路径 | 编号约定 | 含义 | 现有条目 |
| --- | --- | --- | --- |
| `docs/aegis/work/<日期>-<slug>/` | `10-intent` / `20-checkpoint` / `90-evidence` | 单次工作的**执行记录**（授权、TaskStartSnapshot、证据），不是主题文档 | `2026-10-23-board-lane-registry`、`2026-11-01-runtime-config-ux`、`2026-11-01-sync-short-transactions`、`2026-11-07-runninghub` |
| `docs/aegis/plans/` | `YYYY-MM-DD-<slug>.md` | 历史方案草稿 | `2026-10-05-spu-profit-deterioration-alert.md` |
| `docs/architecture/adr/` | `0001`–`0003` | 架构决策记录（ADR），独立体系 | 时间字段约定、同步游标孤儿与陈旧、commerce 命名重构 |
| `docs/ops/incident-reports/` | `YYYY-MM-DD-<slug>.md` | 事故复盘 | `2026-09-13-ad-daily-purge.md` |
| `docs/plans/` | 无编号或日期前缀 | 未落地的方案、草案、待评审提案（`docs/README.md` 定义：落地后移入 `design/`） | 17 个文件，全部是单文件方案，不构成主题目录 |

**`docs/plans/` 里另外 5 份还没归到任何候选主题**：`2026-10-30-unified-workspace-design.md`（全站工作台视觉统一 + 采购查询修复）、`plugin-sourced-shop-analytics.md`（插件数据源店铺的利润计算与展示）、`spu-react-migration-2026-11-01.md`（SPU ROI 与重点关注页 React 迁移）、`compact-page-layouts-2026-11-02.md`（页面留白与布局密度修复）、`2026-11-02-heroui-controls.md`（HeroUI 控件迁移收口）。前三份偏业务、后两份偏前端 UI 工程，混在同一个扁平目录里，检索时容易漏。

---

## 5. 有方案文档但还没建主题目录的候选主题（9 项）

这些主题的文档是真实存在的、且散落在 `design/` `plans/` `aegis/` `api/` `ops/` 里，但没有 `docs/<主题>/01..04` 目录。**新主题开工前，先查这里，避免重复造方案。**

| 候选主题 | 现有散落文档 | 业务领域 | 建议动作 |
| --- | --- | --- | --- |
| 媒体资产库 / 数字人 | `docs/design/media-asset-library.md`、`docs/plans/2026-11-05-media-interaction-implementation.md`、`docs/plans/2026-11-08-media-packs.md` | 媒体资产、数字人、MinIO 对象存储 | 建 `docs/media-asset-library/`；design 迁 01，plans 迁 02/03 |
| 运行配置交互改造 | `docs/plans/2026-11-01-runtime-config-ux-design.md`、`docs/aegis/work/2026-11-01-runtime-config-ux/` | 运行配置 UI、原子发布、只读检查 | 建 `docs/runtime-config-ux/`；`aegis/work` 是执行记录，留在原处 |
| RunningHub 换装工作流 | `docs/api/runninghub.md`、`docs/aegis/work/2026-11-07-runninghub/` | AI 工作流接入、凭据级配额、批量任务 | 建 `docs/runninghub/`；`api/runninghub.md` 是活契约，**留在原处只做引用，不要移动** |
| 店铺入驻 / OAuth 授权 | `docs/plans/2026-10-31-shop-onboarding-design.md`、`docs/ops/shop-onboarding-upgrade.md` | 店铺注册台、OAuth、运营流程 | 建 `docs/shop-onboarding/`；注意 `api/tiktok-shop-oauth.md` 是活契约，别动 |
| 前端渐进迁移 | `docs/plans/frontend-progressive-migration.md`、`docs/plans/frontend-p01-implementation.md`、`docs/plans/frontend-p2-implementation.md`、`docs/plans/frontend-remaining-implementation.md` | React/TS/Vite 迁移、HeroUI 应用壳 | 建 `docs/frontend-migration/` |
| 插件架构清理 / dumps 重构 | `docs/plans/plugin-arch-cleanup-plan.md`、`docs/plans/dumps-tts-erp-refactor-proposal.md` | 插件接入协议、dumps 契约 | 建 `docs/plugin-arch-cleanup/` |
| 测试基建提速 | `docs/plans/2026-11-06-test-feedback-speed.md`、`docs/plans/2026-11-07-test-throughput.md`、`docs/testing/baseline-*.md` | 隔离测试 runner、基线比对 | 建 `docs/test-infra/` |
| SPU 利润劣化告警（旧方案） | `docs/design/spu-profit-deterioration-alert.md`、`docs/aegis/plans/2026-10-05-spu-profit-deterioration-alert.md` | SPU 盈利、阈值告警 | **不能归档，只能标注**（2026-11-08 核实，详见下方说明） |
| TikTok 视频发布（原单体方案） | `docs/design/tiktok-video-publish.md` | 内容运营、视频发布 | 是 `docs/video-publish/` 四段式的**拆分来源**（原 2781 行单体文档），当前状态行仍写 `Implemented`。建议标注指向四段式目录，或在四段式稳定后归档 |

### 5.1 「SPU 利润劣化告警（旧方案）」为什么只能标注、不能归档

旧方案（severity / sample_status / confirmation_status / thresholds / fast|confirmation）**在代码里处于「读侧已退役、写侧仍在生产跑」的半退役状态**，不是死代码。归档它等于文档说一套、代码跑另一套。

**读侧已退役（已核实）：**

- HTTP `/v2/analytics/spu-profit-deterioration` 只返回两期对比结果，**不吐** severity / sample_status 等旧字段。
- `read_alerts()`（`tts_erp_v2/analytics/spu_deterioration_alert/read.py:310`）**没有任何 HTTP 调用方**——全仓仅这一处定义，无引用。
- 前端直接挡掉旧筛选：`tts_erp_v2/static/js/spu-profit-deterioration.js` 中 `retired = ['layer', 'sample', 'severity', 'state']` 命中即返回「不支持旧告警筛选……请从链接移除」。

**写侧仍在生产跑（已核实）：**

- `sync_worker/scheduler.py` 仍注册 `analytics.spu_profit_deterioration_alert`（`interval_seconds=86400`）→ `tts_erp_v2/jobs/spu_deterioration_alert.py` → `read.py` 的 `evaluate()` → `materialize.py` 落库 `analytics.spu_deterioration_alerts`（表模型 `tts_erp_v2/db/models/analytics.py:29`，索引仍含 severity）。
- 该 runtime config key `analytics.spu_profit_deterioration_alert.v1` 仍接通用 runtime-config API（create/draft/publish/rollback，见 `api/v2/config.py`、`runtime_config/workflow.py`）。

**因此本索引的处理方式：** §5 该行只写「读侧已退役、写侧仍在运行、快照表无读取方」，**不写「已归档」**。旧方案文档本身也不移入 `docs/archive/`——归档状态与实际运行状态不符，比不归档更危险。

**待 Chief / 运维决定的一个残留风险（不属于文档范畴，但影响是否停写）：** 开发工程师 2026-11-08 核查生产形状库时发现，该 config key 的 `published_version` 为空、`runtime_config_revisions` 表无该 key 的任何 revision，只有 `draft_version=1`，实际生效的是代码内 `SEED_FALLBACK_CONFIG`（回测暂定、enabled=true），落库 `effective_config_source=seed_fallback`。即：**这套阈值从未被人正式 publish，却每天在生产上产出判定，而快照表已无读取方。** 是否停写、是否归档，等该决定做出后再更新本行。

---

## 6. 维护规则

1. **命名固定**：`docs/<主题>/01-product-proposal.md`、`02-technical-design.md`、`03-implementation-plan.md`、`04-test-plan-and-results.md`。需要附加文档时用字母后缀（如 `02a-backtest-report.md`），**不要插数字占位**。
2. **四段各写什么**：01 = 需求摘要 + 事实/目标/边界（【已确认需求】【已确认事实】【方案建议】【待验证假设】四类标注）+ 不做什么 + 验收标准；02 = 技术契约与所有权边界；03 = 实施顺序、任务边界、交付物、部署与回滚；04 = 测试策略 + 按日期固化的执行结果。
3. **状态行必写**：待评审 / 待实施 / 已实施 / 已废弃；变了就改，否则它是错误引导（`docs/README.md` §2）。
4. **文档 ≠ 实现**：01–04 里写「已实现」必须能指到具体的实施记录；PRD 本身永远只是需求依据。
5. **口径单点定义**：盈利 / ROI / 费率只在 `docs/business/spu-profitability.md` 定义一次，别处只引用。
6. **索引更新**：新建或补齐主题目录后，把 §2 / §3 表格同步改掉，并更新 §1 摘要计数。
7. **目录归属提醒**：本索引留在 `docs/handoff/`，这是 Chief 2026-11-08 的决定——`handoff/` 实际已经是跨会话协调文档目录（已有 `bot-team-setup`、`dev-env-checklist`、P6 两份计划、`test-baseline-verify`、`architecture-review-scope` 等普通文档），本索引不含实时状态，不违反 `handoff/README.md`「不要维护实时状态表」。`docs/README.md` 里 `handoff/` 那一行的描述已同步改准。
8. **目录随主题开工而建，不预先批量补空壳**（2026-11-08 Chief 决定）：新建 `docs/<主题>/` 目录的时机是**主题开工**，不是索引盘点。开工时由产品经理出 `01-product-proposal.md`、开发工程师出 `02`/`03`、测试工程师出 `04`。**不要为还没开工的主题预建目录**——空壳目录会让本索引自身制造它要避免的那种「文件齐了但内容没齐」的误导。已被取代的旧方案同理：先核实它在代码里是否已退役，再决定归档还是只加指向标注。
9. **「已归档」必须有代码依据**：写某个方案已归档前，先确认它在当前 `origin/master` 上确实不再运行。文档与代码不一致时，索引按代码事实写，并标注「读侧已退役、写侧仍在运行」这类半退役状态（见 §5.1）。

---

## 7. 本次扫描没有做的事

- **没有核对每个主题的业务内容是否与代码一致**——除 §5.1 的 SPU 旧告警方案外，其余主题的业务内容与代码是否一致未做核对（本次是目录索引和文档头部阅读）。
- **没有评估主题的实现完成度**——各目录内部状态行自相矛盾的情况已如实标注，未做判定。
- **没有移动、归档或改写任何既有方案文档**——§5 的「建议动作」只是建议，等 Chief 确认后再由对应 Bot 执行。

**本次修订（2026-11-08）做了什么：**

- 修正扫描基线（原写「与 `origin/master` @ `0b92d97d` 逐文件一致」，实为差 2 个文件）。
- 补 `状态：待评审` 状态行；更新计数说明（190 → 196 的构成）。
- 改正 §2「01 与 02/03/04 未对齐」的结论（依据 02/03/04 头部修订声明）。
- 按代码核实结果重写 §5 中 SPU 旧告警方案的处置（读侧退役 / 写侧在跑，不能归档）。
- 维护规则新增第 8、9 条。

**提交状态：** 本索引已提交（`13fa8074`），并已在 `origin/master` 中。本次修订基于 `origin/master` @ `2b68b40a`，在分支 `docs/fix-topic-directory-index` 上提交并推送。
