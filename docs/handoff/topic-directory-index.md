# 主题目录索引（docs/ 四段式主题目录）

> 建立时间：2026-11-08 · 维护者：产品经理（受 Chief 指派）
> 扫描基线：本地克隆 `F:\MiniMax Work Result\agents\mavis\workspace\tts-erp`，分支 `chore/fix-test-server-address` @ `553f2231`。该分支的 `docs/` 与 `origin/master` @ `0b92d97d` **逐文件一致**，无差异，因此下文结论对当前 master 同样成立。
> 扫描范围：`docs/` 下共 **190 个 markdown**（其中 `docs/README.md` 1 个，子目录下 189 个），分布在 16 个子目录。
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

**需要知道的一处不一致：** `spu-profit-deterioration/01`（PRD v3）明确写了「02/02a/03/04 仍包含旧告警方案，后续须对齐，不能用旧阈值、严重程度、历史确认规则替代本版本需求」。也就是说——**这个四段式目录内部，01 与 02/03/04 的业务方案尚未对齐**。文件齐了，但内容口径没齐。

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
| 前端渐进迁移 | `docs/plans/frontend-progressive-migration.md`、`frontend-p01-implementation.md`、`frontend-p02-implementation.md`、`frontend-remaining-implementation.md` | React/TS/Vite 迁移、HeroUI 应用壳 | 建 `docs/frontend-migration/` |
| 插件架构清理 / dumps 重构 | `docs/plans/plugin-arch-cleanup-plan.md`、`docs/plans/dumps-tts-erp-refactor-proposal.md` | 插件接入协议、dumps 契约 | 建 `docs/plugin-arch-cleanup/` |
| 测试基建提速 | `docs/plans/2026-11-06-test-feedback-speed.md`、`2026-11-07-test-throughput.md`、`docs/testing/baseline-*.md` | 隔离测试 runner、基线比对 | 建 `docs/test-infra/` |
| SPU 利润劣化告警（旧方案） | `docs/design/spu-profit-deterioration-alert.md`、`docs/aegis/plans/2026-10-05-spu-profit-deterioration-alert.md` | — | **已被 §2 的 `spu-profit-deterioration/` PRD v3 取代**（PRD 明确「旧阈值、严重程度、历史确认规则不再适用」）。建议移 `docs/archive/`，保留取证价值 |
| TikTok 视频发布（原单体方案） | `docs/design/tiktok-video-publish.md` | — | 是 `docs/video-publish/` 四段式的**拆分来源**（原 2781 行单体文档），当前状态行仍写 `Implemented`。建议标注指向四段式目录，或在四段式稳定后归档 |

---

## 6. 维护规则

1. **命名固定**：`docs/<主题>/01-product-proposal.md`、`02-technical-design.md`、`03-implementation-plan.md`、`04-test-plan-and-results.md`。需要附加文档时用字母后缀（如 `02a-backtest-report.md`），**不要插数字占位**。
2. **四段各写什么**：01 = 需求摘要 + 事实/目标/边界（【已确认需求】【已确认事实】【方案建议】【待验证假设】四类标注）+ 不做什么 + 验收标准；02 = 技术契约与所有权边界；03 = 实施顺序、任务边界、交付物、部署与回滚；04 = 测试策略 + 按日期固化的执行结果。
3. **状态行必写**：待评审 / 待实施 / 已实施 / 已废弃；变了就改，否则它是错误引导（`docs/README.md` §2）。
4. **文档 ≠ 实现**：01–04 里写「已实现」必须能指到具体的实施记录；PRD 本身永远只是需求依据。
5. **口径单点定义**：盈利 / ROI / 费率只在 `docs/business/spu-profitability.md` 定义一次，别处只引用。
6. **索引更新**：新建或补齐主题目录后，把 §2 / §3 表格同步改掉，并更新 §1 摘要计数。
7. **目录归属提醒**：`docs/README.md` 把 `handoff/` 定义为「实时 lane 登记表（协调状态，不是文档）」。本索引放在这里是 Chief 指定的位置；`docs/handoff/bot-team-setup-2026-11-08.md` 已经是同目录的普通文档，先例存在。但如果你认为导航类文档更适合 `docs/README.md` 或 `docs/design/`，可以改挂载位置——**本索引不维护任何实时状态**，不会像 `ACTIVE.md` 那样过期。

---

## 7. 本次扫描没有做的事

- **没有核对每个主题的业务内容是否与代码一致**——本次只做目录索引和文档头部阅读，没有查实现代码。
- **没有评估主题的实现完成度**——各目录内部状态行自相矛盾的情况（见 §2 末尾）已如实标注，未做判定。
- **没有移动、归档或改写任何既有文档**——§5 的「建议动作」全部只是建议，等 Chief 确认后再由对应 Bot 执行。
- **没有提交到 git**——等待 Chief 确认。当前分支 `chore/fix-test-server-address`，本文件是工作区**唯一**新增/改动，未动任何既有文档，也未提交、未推送。
