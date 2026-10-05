# docs/ — tts-erp 文档中心

> 2026-10-03 起，仓库所有专题文档统一收敛到本目录。原有的 `tech-doc/`、`biz-doc/`、
> `setup/`、`handoff/` 四个顶层目录已撤销，根目录只保留 `README.md`、`AGENTS.md`、
> `CHANGELOG.md` 三个入口文件。

## 1. 目录结构

| 目录 | 放什么 | 例子 |
| --- | --- | --- |
| [`business/`](business/) | **业务口径与语义（事实层）**：指标怎么算、字段在业务上代表什么 | [`business/spu-profitability.md`](business/spu-profitability.md)（盈利口径唯一事实文档）、[`business/domain-language.md`](business/domain-language.md) |
| [`api/`](api/) | 端点与协议契约（对外约定，live contract） | [`api/external-api.md`](api/external-api.md)、[`api/dumps-data-contract.md`](api/dumps-data-contract.md) |
| [`reference/`](reference/) | 事实参考手册：枚举值、接口目录、上游平台资料 | [`reference/enums/`](reference/enums/)、[`reference/tiktok-seller-center-api-catalog.md`](reference/tiktok-seller-center-api-catalog.md) |
| [`architecture/`](architecture/) | 系统结构、数据模型、ADR、退役记录 | [`architecture/architecture-overview.md`](architecture/architecture-overview.md)、[`architecture/adr/`](architecture/adr/) |
| [`design/`](design/) | 已落地的技术方案 / 模块设计（含实现决策） | [`design/spu-profitability-technical-design.md`](design/spu-profitability-technical-design.md) |
| [`plans/`](plans/) | **尚未落地**的方案、草案、待评审提案 | [`plans/plugin-sourced-shop-analytics.md`](plans/plugin-sourced-shop-analytics.md) |
| [`guides/`](guides/) | 流程、操作、排障指南（agent 流程、测试、命令、常见坑） | [`guides/agent-safety.md`](guides/agent-safety.md)、[`guides/common-bugs.md`](guides/common-bugs.md) |
| [`ops/`](ops/) | 部署、运行维护、备份、事故复盘 | [`ops/tts-erp.md`](ops/tts-erp.md)、[`ops/incident-reports/`](ops/incident-reports/) |
| [`handoff/`](handoff/) | 实时 lane 登记表（协调状态，不是文档） | [`handoff/ACTIVE.md`](handoff/ACTIVE.md) |
| [`archive/`](archive/) | 归档区：过时、已完成、被取代、仅供取证的内容 | 见 [`archive/README.md`](archive/README.md) |

写新文档时按上表选目录；`design/` 与 `plans/` 的区别是**是否已落地**，落地后把
`plans/` 里的方案移进 `design/`（或按情况进 `archive/`）并改状态行。

## 2. 维护原则

- **口径单点定义**：盈利 / ROI / 费率等计算口径只在
  [`business/spu-profitability.md`](business/spu-profitability.md) 定义一次，其他文档只引用，
  不得另行定义或改写公式。同一字段（如 `onsite_roi2_shopping_sku`）的业务语义同理只定义一次。
- **来源标记**：字段语义必须标注来源（`user spec` / `TikTok OEC 文档` / `dump 推断` / 生产实测），
  未验证的标 ⚠️。
- **状态行**：方案类文档开头必须有状态行（待评审 / 待实施 / 已实施 / 已废弃）。状态变了就更新，
  否则它就是"错误引导"。已过时但有取证价值的文档移入 `archive/` 并保留原状态行。
- **变更追踪**：业务语义变更在 commit message 写 `biz:` 前缀，与代码改动同步提交。
- **文档与代码同改**：改端点、字段、口径时同步改契约文档与测试注释里的引用路径。

## 3. 旧目录 → 新目录映射（2026-10-03 迁移）

| 旧路径 | 新路径 |
| --- | --- |
| `biz-doc/analytics/spu-roi-profit-calculation.md` + `spu-roi-data-sources.md` + `tech-doc/analytics/roi-calc-prompt.md` + `shop-fee-rate-definition-gap.md`（口径部分） | **合并为** [`business/spu-profitability.md`](business/spu-profitability.md) |
| `biz-doc/**`（其余） | `business/`（`ad-product-links-view.md` → `archive/`） |
| `tech-doc/*.md`（指南类：agent-*、commands-reference、common-bugs、test-domains、fx-agent-handbook 等） | `guides/` |
| `tech-doc/external-api.md`、`tech-doc/dumps-data-contract.md`、`tech-doc/api/*` | `api/` |
| `tech-doc/enums/`、`tiktok-*`、`miaoshou-*` | `reference/` |
| `tech-doc/architecture-overview.md`、`process-architecture.md`、`data-model-target-v3.md`、`linkage-retirement.md`、`adr/` | `architecture/` |
| `tech-doc/*-module.md`、`*-design.md`、`analytics/`（已落地方案） | `design/` |
| `tech-doc/*proposal*`、`plugin-sourced-shop-analytics.md` | `plans/` |
| `tech-doc/pg-backup-design.md`、`database-maintenance-todos.md`、`incident-reports/` | `ops/` |
| `setup/*.md` | `ops/` |
| `handoff/ACTIVE.md` | `handoff/ACTIVE.md`（目录上移进 docs/） |
| `handoff/spu-roi-full-loss-rubric.md`、`order-sync-action-code-migration-2026-09-20.md`、`handoff.md`、`SYNC_CRON_LEGACY.md`、`scripts/MIGRATE_V1_TO_V2_ARCHIVED.md` | `archive/` |
| `tech-doc/_archive/**` | `archive/`（平铺；`migrate-v1-to-v2-2026-08-29/`、`sync-cron-legacy-2026-08/` 两个代码目录原样保留） |
| `CONTEXT.md` | [`business/domain-language.md`](business/domain-language.md) |

全仓（含 `tests/`、`alembic/`、`scripts/`、`CHANGELOG.md`）的旧路径引用已同步改写。
历史遗留引用如 `tech-doc/intercept-plugin-canonical.md`（已并入 `api/dumps-data-contract.md`）
按合并后的位置指向。
