# 验证记录

- 所有数据库测试只经服务器独立 worktree 的 `scripts/test_isolated.sh`，没有运行任务、生产迁移或服务重启。
- 环境检查：`scripts/envsetup/install-test-deps.sh --check` 通过。首次发现模板缺 `shops.shop_code`，通过 `--refresh-template fast tests/sync_worker/test_transaction_boundaries.py` 刷新模板并运行 10 个边界测试，exit 0。
- 29a93a79 定向回归：44 tests，exit 0，日志 `/tmp/tts-sync-short-final-targeted.log`，JUnit `/tmp/tts-sync-short-final-targeted.xml`。
- 新增覆盖：开始记录跨连接可见；业务单元回滚与已完成单元保留；活动业务事务不能被 token 刷新提交；六个 TikTok 入口联网无事务；五个妙手入口联网时共享账号可被另一连接 NOWAIT 加锁；采购网页/图片 I/O 无事务；旧失败/新成功与必需子实体失败冻结游标；凭据 CAS；提交失败 one-shot 返回 1；同 Service ID 改 App revision 后响应式刷新和重试使用新 App。
- 独立只读审查指出生命周期、凭据快照和子实体游标遗漏；均纳入修复。111eb779 最终只读审查无剩余实质性发现；审查本身不替代测试。
- `ruff check --select F,I` 在全部任务改动 Python 文件通过；`git diff --check` 通过。语法/静态检查不替代集成测试。
- 111eb779 定向回归：`bash scripts/test_isolated.sh fast tests/sync_worker/test_transaction_boundaries.py tests/sync_worker/test_proxy_call.py tests/jobs_tiktok --tb=short --junitxml=/tmp/tts-sync-short-boundaries.xml`，133 passed，exit 0。
- 完整 fast baseline（仅一次）：`PYTEST_ADDOPTS='--junitxml=/tmp/tts-sync-short-baseline.xml' bash scripts/test_baseline.sh --keep-log /tmp/tts-sync-short-baseline-suite.log`，3272 cases，372 failures，15 skipped（其中 1 expected failure），2885 passed，exit 1；耗时 1704.259 秒。原始输出、JUnit 与 driver 均保留在服务器 `/tmp/tts-sync-short-baseline*`。
- 完整基线内：`tests.jobs*`、`tests.sync_worker`、`tests.fx`、`tests.reporting`、monitor health repository、token_service 与 tiktok_auth 共 482 cases，482 passed，0 failed/0 skipped。
- 完整基线失败分组：API 289，browser 49，scripts 21，proxy 11，frontend 1，publishing 1。提交基线记录为 0，故机械 DoD 为红；不修改基线文件，不以抽样通过宣布全仓通过。
- 干净 master 对照：独立 worktree `/home/schan/tts-erp/.worktrees/codex-sync-master-reference-01a1962b` 固定 ea779d91，无业务改动；所有执行仍经 isolated runner。观察到的 289 个 API 失败选集全部复现（`/tmp/tts-sync-master-api-failing.xml`）；34 个非 API/browser 失败全部复现（`/tmp/tts-sync-master-other-failing.xml`）；49 个 browser 失败全部复现（`/tmp/tts-sync-master-browser-failing.xml`，870.266 秒）。集合比较：lane=372、master=372、lane-only=0、master-only=0。365 项首行错误文本相同，其余含不同测试自增 ID 或前端测试输出片段。
- 最终 6520dc31 回调类型/注释调整后的窄验证：`bash scripts/test_isolated.sh fast tests/proxy/test_tiktok_auth.py tests/sync_worker/test_proxy_call.py --tb=short --junitxml=/tmp/tts-sync-short-final-contract.xml`，16 passed，exit 0。
- 复杂度：统一既有生命周期 owner，退休执行器手动提交/回滚路径，复用 SQLAlchemy 原生上下文，无新增依赖。测试种子 helper 仅处理现有 SAVEPOINT fixture；生产代码不增加自动提交适配层。必要的一致性读快照和原子 anchor 替换保留；monitor lease 远端确认前崩溃仍可能重发，见架构契约。
- 完成边界：事务修复实现与专项证据成立；全仓 DoD 未通过，分支不进入 ready、不合入 master。置信度 B（任务边界）；全仓发布就绪未证明。
- 环境和交付：Linux 现有 Python 3.13 venv / PostgreSQL / Playwright；没有声称运行 Python 3.14 测试。业务提交 6520dc31 已推送，后续仅本工作记录；origin/master 仍为 ea779d91。两个服务器测试 worktree 与 `/tmp/tts-sync-*` 日志保留用于复查；没有活动测试进程，runner 已清理每次临时数据库。
