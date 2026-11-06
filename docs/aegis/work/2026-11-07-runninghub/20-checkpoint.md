# 当前检查点

1. 已完成：读取需求/规范、核对官方接口、建立独立工作区，确认 HP desk 隔离测试前置依赖可用。
2. 已完成：官方客户端、版本校验、9表迁移、素材快照与上传缓存、持久化配额/恢复、API/权限、React/HeroUI 页面、规格处理与 ZIP 导出。
3. 已验证：隔离入口 `--refresh-template fast tests/api/test_runninghub.py tests/browser/test_runninghub_page.py tests/frontend/test_components.py --maxfail=1 --tb=short`，35个 pytest 检查通过（28个后端、6个浏览器、1个适配器运行3个组件用例）；后续4个配额/恢复相关定向检查通过。Ruff、TypeScript通过。390/1440/2560的实际编译产物截图已人工查看；无全页横向溢出，保留长文件名和共享应用壳。证据日志在 HP desk `/tmp/rh-final-targeted.log`、`/tmp/rh-final-recovery.log`。
4. 全量自动选择回归已执行，前端52文件/761用例通过；仅3项页面/结构文档登记偏差，已重生成快照、同步页面预期并通过3项最小复测。当前同步目标为 `aa795a4daa1a968be6c3fa6bd8a11bf5051be9ce`。候选提交/推送后进入最终精确 merge commit 验证；最终集成状态以 Git 和交付 SHA 为准。

主要边界：create 只发送一次；SUBMITTING 崩溃/超时保留配额待核对；已知 taskId 永不自动重建；本地阶段失败只恢复本地阶段；每六段组保留稳定图片身份；不访问或修改任何真实素材/付费任务。

恢复入口：本目录意图；docs/api/runninghub.md；git status；origin/master 漂移检查。测试主机 `/home/schan/tts-erp` 存在其他人的插件修改与 node_modules，禁止触碰；任务专属校验 checkout 是 `/home/schan/.tts-erp-worktrees/runninghub-validation`。使用既有 `~/.local/bin/ffmpeg`、ffprobe；私有 `.env` 从既有 runtime.env 单独只读复制，仅供 runner 读取生产 schema 来源；测试全程在临时克隆库。没有执行生产 SQL 写入/迁移/重启或真实付费联调。
