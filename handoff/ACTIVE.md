# ACTIVE.md — tts-erp 在途工作注册表（谁在改什么 / 谁接手）

> **机器可读**：markdown 表格，是"当前谁拥有 master 未提交改动 / 分支"的唯一 truth
> source（AGENTS.md §12.1）。**任何 agent 在 master 工作区开新工作前，必先更新本表**
> （注册自己的 lane，或确认已有注册覆盖你要动的文件面），再动第一行代码。
> 收尾 / 换手 / 放弃都必须改表；merge 后删行。历史交接见根目录 `handoff.md`。

## 状态机

`draft`（进行中）→ `done`（待合入）→ merge 后 **删行**；放弃 = `abandoned` + 日期 + 原因。

| lane_id | 主题 | owner(session) | branch/worktree | 拥有的文件/目录 | 状态 | updated(UTC) |
| --- | --- | --- | --- | --- | --- | --- |
| feat/spu-roi-page-ui | ROI 看板 UI：主图点击放大 / 缩略图加大(34→56)+单元格布局 / 汇率 stamp 与汇总重叠修复 | (接管 manual-costs-v2 之后的独立 UI lane) | .worktrees/spu-roi-page-ui | tts_erp_v2/api/v2/pages.py(ROI 模板段)、tts_erp_v2/static/js/spu-roi.js、handoff/ACTIVE.md | merged (9253965) | 2026-09-06T06:16Z |
| feat/manual-costs-v2 | manual-costs 页 v2（全部 SPU tab / 事件绑定修复） | 未登记（owner session 见 .worktrees/manual-costs-v2 @ 5324f95） | .worktrees/manual-costs-v2 | tts_erp_v2/api/v2/pages.py、tts_erp_v2/static/js/spu-roi.js、console.js（master WT 在途 M） | draft | 2026-09-06 |
| feat/tiktok-shop-oauth | TikTok seller OAuth 收尾部署（接手 Lane E 无主 WIP：rebase master 51→0、migration 0008→0011 接 0010、公网 /tts 前缀修正、部署验证） | 本 session（oauth 修复 agent，§12.3 接手：无登记 + HEAD 静止 e9f3310） | .worktrees/shop-oauth-auth / feature/shop-oauth-auth | tts_erp_v2/api/v2/oauth.py、tts_erp_v2/proxy/tiktok_oauth.py、tts_erp_v2/proxy/tiktok_auth.py、tts_erp_v2/app.py、tts_erp_v2/middleware/auth.py、tts_erp_v2/db/models/integration.py、alembic/versions/（0011 迁移）、schema_tts_erp.sql、tech-doc/api/tiktok-shop-oauth.md、tests/api|proxy 新增测试 | draft | 2026-09-06T06:46Z |

<!-- 新 lane 示例（复制改）：
| lane_id | 主题 | owner(session) | .worktrees/<slug> / branch | 文件列表 | draft | <UTC> |
-->

## 规则速记

- 开新工作（尤其会动 master WT 未提交区 / 共享点文件）→ **先加一行再动工**。
- 每步完成更新状态；被接手 / 被卡住 → 更新 owner / abandoned。
- 找"这是谁的 WIP" → 先查本表，再 `git fetch` 对比 `origin/master` 看 HEAD 是否在动
  （AGENTS.md §12.3 接手协议）。
