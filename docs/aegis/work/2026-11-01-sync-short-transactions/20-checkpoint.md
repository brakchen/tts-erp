# 当前进度

- 状态：已按用户明确指示合并并推送 master；合并提交 `1b1ae17708eeb6b3ac8ff90d06bd46e7da580eda`。完整 fast 仍有 372 项既有失败，不声称全仓测试通过。
- 基线：前轮审计覆盖 scheduler.JOBS 的 21 个任务及 systemd timers/publishing loops；本轮读取安全、测试、Git 工作流并 fetch，master 未变化。
- 测试：用户已提供 Linux 测试主机；登录后只读核查仓库环境，在独立测试 worktree 运行隔离 runner。凭据不写入记录或文件。
- 已完成：两套执行器统一审计 owner；21 个任务短事务；凭据快照/CAS/App revision；按实体提交；有界回填/报表；子实体失败冻结游标；通知 lease 和发送期间恢复确认。
- 验证：133 个定向回归通过；完整 fast 的相关 482 个用例全部通过。最终 6520dc31 的凭据/代理回调契约复核 16 个通过。独立只读审查已收敛，无剩余实质性发现。
- 测试适配：旧 shop fixtures 缺少当前已存在的 shop-owned App 授权，已补齐种子；旧手动 commit/rollback 注入改为 SQLAlchemy before_commit；新跨连接测试仅清理自身插入的 issue ID，避免污染其他测试。
- 当前业务 HEAD：6520dc31（分支已推送；111eb779 之后仅统一回调类型与修正旧事务注释）。服务器验证副本位于 `/home/schan/tts-erp/.worktrees/codex-sync-short-transactions`，仅测试；共享主仓 WIP 不触碰。
- 完整 baseline：3272 cases，372 failures，15 skipped（含 xfail），exit 1。全部 372 个失败已在干净 ea779d91 上分三个最小失败选集复现，失败 ID 集合完全相同；没有修改提交基线。
- 收尾：合并提交已隔离验证并推送，482 个相关用例全过，失败集合与原有 372 项完全一致。仅清理本任务工作树；保留服务器 `/tmp/tts-sync-merge-*` 日志，保留主 checkout 原有 `plugins/monitor` WIP。生产部署与服务重启由人工操作。
- Resume：先读用户最新指令、本目录 intent/checkpoint、git diff；只在本 worktree 工作，不触碰主 checkout/其他 worktree。
- Drift：merged；用户在已披露既有失败后明确要求合并；专项通过、额外失败为零，机械基线仍为红。未部署或重启服务。
