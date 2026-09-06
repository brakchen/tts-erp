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
| fix/fx-test-isolation | fx 测试脱敏：fx.sync 每日真实 USD snapshot 入库后，假设 fx 表无真实数据的 fx/rates/sync/api 测试被环境性破坏（HEAD 复现），改为互斥 base_code + 夹具内清理，不含对生产行的假设 | 本 session | .worktrees/fx-test-isolation / fix/fx-test-isolation | tests/fx/*.py、tests/api/test_fx_api.py、tests/conftest.py（如涉及共享夹具） | draft | 2026-09-06T07:14Z |
| feat/cursor-hasdata-cache | cursor has-data 10min 进程内存缓存：(seller,adv,campaign)→(endpoint,day) 集合；get_cursor 命中免 DB、post_dumps write-through；无 campaignId 请求不缓存 | 本 session（master WT 直改） | master（无分支，原子 commit） | tts_erp_v2/analytics/has_data_cache.py(新)、repository.py、tts_erp_v2/api/v2/analytics.py、tests/api/conftest.py、tests/analytics/test_has_data_cache.py(新)、tests/api/test_analytics_v2_cursor_cache.py(新)、tech-doc/analytics/dump-architecture.md | done | 2026-09-06T10:45Z |

<!-- 新 lane 示例（复制改）：
| lane_id | 主题 | owner(session) | .worktrees/<slug> / branch | 文件列表 | draft | <UTC> |
-->

## 规则速记

- 开新工作（尤其会动 master WT 未提交区 / 共享点文件）→ **先加一行再动工**。
- 每步完成更新状态；被接手 / 被卡住 → 更新 owner / abandoned。
- 找"这是谁的 WIP" → 先查本表，再 `git fetch` 对比 `origin/master` 看 HEAD 是否在动
  （AGENTS.md §12.3 接手协议）。
