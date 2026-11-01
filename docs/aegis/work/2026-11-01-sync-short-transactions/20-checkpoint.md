# 当前进度

- Active：实现已完成，完整 fast baseline 未通过；正在对照干净 master 分类失败，不作合入就绪声明。
- 基线：前轮审计覆盖 scheduler.JOBS 的 21 个任务及 systemd timers/publishing loops；本轮读取安全、测试、Git 工作流并 fetch，master 未变化。
- 测试：用户已提供 Linux 测试主机；登录后只读核查仓库环境，在独立测试 worktree 运行隔离 runner。凭据不写入记录或文件。
- 已完成：两套执行器统一审计 owner；21 个任务短事务；凭据快照/CAS/App revision；按实体提交；有界回填/报表；子实体失败冻结游标；通知 lease 和发送期间恢复确认。
- 验证：133 个定向回归通过；完整 fast 的相关 482 个用例全部通过。最终 6520dc31 的凭据/代理回调契约复核 16 个通过。独立只读审查已收敛，无剩余实质性发现。
- 测试适配：旧 shop fixtures 缺少当前已存在的 shop-owned App 授权，已补齐种子；旧手动 commit/rollback 注入改为 SQLAlchemy before_commit；新跨连接测试仅清理自身插入的 issue ID，避免污染其他测试。
- 当前业务 HEAD：6520dc31（分支已推送；111eb779 之后仅统一回调类型与修正旧事务注释）。服务器验证副本位于 `/home/schan/tts-erp/.worktrees/codex-sync-short-transactions`，仅测试；共享主仓 WIP 不触碰。
- 完整 baseline：3272 cases，372 failures，15 skipped（含 xfail），exit 1。289 个 API 与 34 个其他失败已在干净 ea779d91 上全部复现；49 个浏览器失败仍在对照。不得把未复现的项统称主仓问题。
- 下步：读取 `/tmp/tts-sync-master-browser-failing.xml`，完成证据记录、提交和推送。保持分支未合入；生产部署与服务重启由人工操作。
- Resume：先读用户最新指令、本目录 intent/checkpoint、git diff；只在本 worktree 工作，不触碰主 checkout/其他 worktree。
- Drift：needs-verification；代码专项通过，仓库 DoD 为红，未合入/部署，保留工作树供复查。
