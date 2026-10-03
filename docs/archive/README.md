# docs/archive/ — 归档区

**归档 ≠ 删除**：这里的文档只作历史记录与取证用，**不得作为当前口径、契约或操作依据**。
现行文档看 [`docs/README.md`](../README.md) 的目录表。

- 顶层 `.md`：2026-10-03 文档整理时迁入的过时 / 已完成 / 被取代文档，以及原 `tech-doc/_archive/`
  的 v1 时代文档（平铺）。
- `migrate-v1-to-v2-2026-08-29/`、`sync-cron-legacy-2026-08/`：**冻结的历史代码**（v1→v2 迁移脚本与
  旧 cron 同步脚本），仅供取证，**禁止执行或恢复**（见 `AGENTS.md` §3）。

## 2026-10-03 新归档清单（原位置 → 归档原因）

| 文件 | 原位置 | 归档原因 |
| --- | --- | --- |
| `spu-real-roi-dashboard.md` | `tech-doc/analytics/` | SPU ROI 看板历史设计稿：M1–M19 旧公式（如 M17 含 `fee_est`）与现行代码 `_formula_v10.py` 不一致，且口径已唯一化到 `business/spu-profitability.md`（M 码对照见该文附录 D） |
| `roi-calc-prompt.md` | `tech-doc/analytics/` | 旧分析 prompt（v9 时代口径，与 v10 冲突）；有用内容已并入 `business/spu-profitability.md` 附录 B/C |
| `shop-fee-rate-definition-gap.md` | `tech-doc/analytics/` | 费率 r̂ 口径定位全过程（2026-09-29 定案）；结论与反证已并入 `business/spu-profitability.md` 附录 B |
| `spu-roi-full-loss-rubric.md` | `handoff/` | v3–v9 版本史 + 2026-09-07 时点数字，本来就是"非计算依据"的历史档案 |
| `spu-roi-v7-refactor.md`、`spu-roi-v8-ad-window.md` | `tech-doc/analytics/` | v7 / v8 时代技术方案，已被 v10 口径取代 |
| `typed-facts-plan.md`、`range-aggregate-history-sync.md` | `tech-doc/analytics/` | v3 区间聚合协议已废弃（migration 0020 清理遗留对象） |
| `reorg-plan.md` | `tech-doc/analytics/` | 状态：已实施（2026-09-05，migration 0007 系列） |
| `dump-architecture.md` | `tech-doc/analytics/` | 自带声明"不再反映现状"；现行 dumps 契约见 `api/dumps-data-contract.md`，广告逐日协议见 `design/daily-sync-with-coverage.md` |
| `ad-product-links-ui.md`、`ad-product-links-view.md` | `tech-doc/analytics/`、`biz-doc/analytics/` | 依赖的 `analytics.ad_product_links` 视图已由 migration 0020 删除，勿作取数依据 |
| `chrome-ext-order-sync-design.md` | `tech-doc/` | 设计稿；`plugin.raw_log` 已下线（Phase 3），§3.2 等内容失效 |
| `browser-login-design.md` | `tech-doc/` | 已被 `design/user-account-authz-design.md` 取代 |
| `procurement-ui-redesign.md` | `tech-doc/` | 自带 SUPERSEDED 声明（2026-08-31） |
| `refactor-tech-plan-v2.md` | `tech-doc/` | v2 重构已切流完成（2026-08-29） |
| `access-policy-module-review.md`、`access-policy-implementation-review.md`、`order-dump-intake-module-review.md`、`dumps-tts-erp-review.md` | `tech-doc/` | 评审过程记录，决策均已落地；模块设计见 `design/` |
| `audit-report-2026-09-12.md` | `tech-doc/` | 一次性审计报告（2026-09-12），结论已处理 |
| `handoff.md` | 仓库根 | 跨 session 交接笔记，最后更新 2026-09-15，已被 tower-do + `handoff/ACTIVE.md` 取代 |
| `sync-cron-legacy.md`（原 `SYNC_CRON_LEGACY.md`）、`migrate-v1-to-v2-archived.md`（原 `scripts/MIGRATE_V1_TO_V2_ARCHIVED.md`） | 仓库根 / `scripts/` | 早已自declare ARCHIVED 的指向性说明 |
| `order-sync-action-code-migration-2026-09-20.md` | `handoff/` | 迁移已完成的交接记录 |
| `biz-doc-readme.md` | `biz-doc/README.md` | 目录说明已并入 `docs/README.md` |

原 `tech-doc/_archive/` 内容（`analytics-sync.md`、`architecture.md`、`compatibility.md`、
`database-schema-design-v1.md`、`data-model-v1.md`、`data-model-survey-v1.md`、
`analytics-v2-migration-plan.md`、`after-sales-migration-v1.md`、`plugin-integration.md`、
`m3-parallel-dev-prompt.md`、`review-remediation-2026-08.md`、`openapi.yaml` 等）按原样平铺迁入，
归档事由沿用各自文档自带的声明。
