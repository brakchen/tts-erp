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
| feat/manual-costs-v2 | manual-costs 页 v2（全部 SPU tab / 事件绑定修复） | 未登记（owner session 见 .worktrees/manual-costs-v2 @ 5324f95） | .worktrees/manual-costs-v2 | tts_erp_v2/api/v2/pages.py、tts_erp_v2/static/js/spu-roi.js、console.js（master WT 在途 M） | **abandoned（2026-09-07，用户拍板直接放弃；核心修复 5324f95/dd21055 已在 master；分支/worktree 均无残留，pages.py/spu-roi.js 文件面释放给 spu-roi-v7）** | 2026-09-07 |
| fix/fx-test-isolation | fx 测试脱敏：fx.sync 每日真实 USD snapshot 入库后，假设 fx 表无真实数据的 fx/rates/sync/api 测试被环境性破坏（HEAD 复现），改为互斥 base_code + 夹具内清理，不含对生产行的假设 | 本 session | .worktrees/fx-test-isolation / fix/fx-test-isolation | tests/fx/*.py、tests/api/test_fx_api.py、tests/conftest.py（如涉及共享夹具） | draft | 2026-09-06T07:14Z |
| feat/cursor-hasdata-cache | cursor has-data 10min 进程内存缓存：(seller,adv,campaign)→(endpoint,day) 集合；get_cursor 命中免 DB、post_dumps write-through；无 campaignId 请求不缓存 | 本 session（master WT 直改） | master（无分支，原子 commit） | tts_erp_v2/analytics/has_data_cache.py(新)、repository.py、tts_erp_v2/api/v2/analytics.py、tests/api/conftest.py、tests/analytics/test_has_data_cache.py(新)、tests/api/test_analytics_v2_cursor_cache.py(新)、tech-doc/analytics/dump-architecture.md | merged (649de06) | 2026-09-06T10:45Z |
| feat/db-pool-sizing | QueuePool 重配：pre_ping off（改 pool_recycle=300）+ size 10/overflow 20 + pool_timeout 10s（09-05 池耗尽 503 的 fast-fail） | 本 session（master WT 直改） | master（无分支，原子 commit） | tts_erp_v2/db/base.py、handoff/ACTIVE.md | merged | 2026-09-06T11:20Z |
| fix/review-findings-cache-pool | review（4241295..HEAD）发现修复：Finding-1 db/base.py 重启风险注释低估；Finding-2/3 has_data_cache.py docstring 补 no-DELETE 不变量红线 + 单进程假设警告 | 本 session（master WT 直改） | master（无分支，原子 commit） | tts_erp_v2/db/base.py、tts_erp_v2/analytics/has_data_cache.py、handoff/ACTIVE.md | merged | 2026-09-06T12:10Z |
| docs/roi-calc-prompt | ROI 计算口径/公式整理为可复用 agent prompt，存 tech-doc/analytics/roi-calc-prompt.md（取消/全损判定、保本 ROI M17、GMVMax 商品ROI、汇率/费率基线等，truth source=spu-real-roi-dashboard.md） | 本 session（master WT 直改，仅新增文档） | master（无分支，原子 commit） | tech-doc/analytics/roi-calc-prompt.md(新)、handoff/ACTIVE.md | merged (39929f6) | 2026-09-07T00:12Z |
| fix/agents-md-multilane-gaps | AGENTS.md 多 agent 协作规则补漏（§6 合法清理手段 / §11 merge 后 master 重测 + branch-D 预检 / §11 .env 警告 / §12.1 单写者规则 / §12.3 接手步骤 6 / §12.4 错峰量化） | 本 session | .worktrees/agents-md-multilane-gaps | AGENTS.md | merged (95b399b) | 2026-09-07T19:50Z |
| fix/agents-md-test-rule-refine | §11 worktree 收尾细化——"merge 后必须 0 fail" 改为按 lane 代码改动面分类判定（文档-only lane 不受 pre-existing fail 阻塞，代码/test lane 仍必须 0 新 fail） | 本 session | .worktrees/agents-md-test-rule-refine | AGENTS.md | merged (a203fd1) | 2026-09-07T21:45Z |
| feat/spu-roi-v7 | pages/spu-roi 按 rubric v7 重构（SETTLEMENT 分层净利 + 全损 38301 货本补扣 + M19 信息列化 + 行点击钻取面板五 tab + 四个懒加载端点）；§3.5 零值落库已落地（merge eba20af + 回填 2,339 行验证通过）；设计稿 tech-doc/analytics/spu-roi-v7-refactor.md | 本 session | （设计完成，待开工） | tech-doc/analytics/spu-roi-v7-refactor.md(新)；实施后扩到 analytics.py/spu_roi.py/pages.py/spu-roi.js/tests | **done(设计)：D1–D7 全拍板，待开工** | 2026-09-07T20:10Z |
| fix/clean-test-pollution | 清理 prod tts_erp 177 行测试污染（154 token.refresh simulated + 22 reporting.cost_snapshots fake + 1 TEST_OAUTH credential）+ 修 api/_wipe_test_rows 补 credentials 清理 + 手动 SQL 清线上数据 | 本 session | .worktrees/clean-test-pollution / fix/clean-test-pollution | tests/api/conftest.py、handoff/ACTIVE.md | draft | 2026-09-07T13:45Z |
| fix/oauth-authorize-readwrite | 降低 /v2/oauth/tiktok/authorize 角色门槛 admin → readwrite（生成操作只插 45min 单次 state + 拼 URL，无破坏性数据变更；callback 仍 public） | 本 session | .worktrees/oauth-authorize-readwrite / fix/oauth-authorize-readwrite | tts_erp_v2/middleware/auth.py、tts_erp_v2/api/v2/oauth.py、tests/api/test_oauth_api.py、tech-doc/api/tiktok-shop-oauth.md、tech-doc/external-api.md | merged (1112409) | 2026-09-07T11:33Z |
| fix/spu-roi-orphan-applycoltoggles | pages/spu-roi 报错"加载失败 · applyColToggles is not defined"：94afd70 删 ⚙ 列开关时漏删 render() 里的孤儿调用，补删一行 + 注释指明原因；D8 测试已断言无 col-toggle-*/data-cg=，所以正确修复就是删调用 | 本 session | .worktrees/spu-roi-orphan-fix / fix/spu-roi-orphan-applycoltoggles | tts_erp_v2/static/js/spu-roi.js、handoff/ACTIVE.md | merged (f30480b) | 2026-09-08T15:08Z |
| fix/spu-roi-drill-panel-visual | 钻取面板视觉回归：利润构成 tab 改 <div> 瀑布结构(净收入/货本/广告/净利润 4 层)与设计稿 §6.2 对齐；pages.py 内联 CSS 补齐钻取面板缺失样式(op-drill-*/op-pnl-*/op-tab-table)；其他 4 tab 类名 op-pnl-table → op-tab-table 消歧义。根因是 CSS 全缺导致浏览器渲染为裸 HTML，用户看到的「出表格」其实是裸 <table>+ 裸 <div> 视觉无差 | 本 session | .worktrees/spu-roi-drill-visual / fix/spu-roi-drill-panel-visual | tts_erp_v2/static/js/spu-roi.js、tts_erp_v2/api/v2/pages.py、handoff/ACTIVE.md | merged (cc2001e) | 2026-09-08T15:35Z |
| fix/spu-roi-drill-auth-403 | spu-roi 钻取 4 端点({spu_pk}/{orders | settlements | cases | ads})返 403：middleware/auth.py 默认 admin，readonly session 命中 default 分支。修法 = _READONLY_PREFIXES 加 /v2/analytics/spu-roi/(主表 _READONLY_EXACT 保留) | 本 session | .worktrees/spu-roi-drill-auth / fix/spu-roi-drill-auth-403 | tts_erp_v2/middleware/auth.py、handoff/ACTIVE.md | merged (876c2c0) | 2026-09-08T16:05Z |
| fix/spu-roi-settlements-sql | spu-roi 钻取 /settlements 端点 500：spu_roi.py::_SQL_DETAIL_SETTLEMENTS 引用了不存在的 st.statement_time / st.transaction_id 列，实际表字段是 transaction_time / external_transaction_id。修法 = st.transaction_time AS statement_time(保持别名兼容前端+handler) + 删无用 st.transaction_id AS statement_id select | 本 session | .worktrees/spu-roi-settlements-sql / fix/spu-roi-settlements-sql | tts_erp_v2/analytics/spu_roi.py、handoff/ACTIVE.md | merged (72d01fb) | 2026-09-08T16:15Z |
| feat/spu-roi-restore-ad-count | spu-roi 主表恢复「关联广告数」列(2026-09-08 用户反馈):D8(2026-09-07)删 ⚙ 列开关时漏删 rowMarkup 里 <td>${adCell}</td> 输出，导致 row 8 td / th 7 列错位。修法 = th 表头加 data-sort="ad_count" 关联广告数列(放第一指标位置),colspan 7→8(加载中+钻取面板),测试断言更新(sortable 集合加 ad_count / forbidden 删「广告数」/ required 加「关联广告数」)。后端 _ROI_SORT_FIELDS 早含 ad_count,SQL 零改 | 本 session | .worktrees/spu-roi-add-ad-count / feat/spu-roi-restore-ad-count | tts_erp_v2/api/v2/pages.py、tests/api/test_spu_roi_api.py、handoff/ACTIVE.md | merged (091fff8) | 2026-09-08T16:45Z |
| fix/spu-roi-product-col-narrow | spu-roi 主表「商品」列宽调整(2026-09-08 用户反馈)：桌面 .td-title max-width 300→180、td-spu-cell gap 12→8；≤lg 屏 max-width 120→110、min-width 190→150。整体商品列桌面从 ~368px 砍到 ~244px(56+8+180)，为 7 个指标列横向腾出空间，避免主表在中等宽度屏出现横滚 | 本 session | .worktrees/spu-roi-product-col-width / fix/spu-roi-product-col-narrow | tts_erp_v2/api/v2/pages.py、handoff/ACTIVE.md | draft | 2026-09-08T17:05Z |

<!-- 新 lane 示例（复制改）：
| lane_id | 主题 | owner(session) | .worktrees/<slug> / branch | 文件列表 | draft | <UTC> |
-->

## 规则速记

- 开新工作（尤其会动 master WT 未提交区 / 共享点文件）→ **先加一行再动工**。
- 每步完成更新状态；被接手 / 被卡住 → 更新 owner / abandoned。
- 找"这是谁的 WIP" → 先查本表，再 `git fetch` 对比 `origin/master` 看 HEAD 是否在动
  （AGENTS.md §12.3 接手协议）。
