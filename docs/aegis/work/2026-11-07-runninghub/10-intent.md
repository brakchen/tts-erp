# RunningHub 换装工作流接入

来源：用户指定的 `RunningHub官方API接入_开发Agent_Prompt.md`（2026-11-07）。
目标：在 ERP 内交付单任务闭环、六段批次计划、凭据级数据库配额、可恢复 Worker、成片处理、导出和操作界面。默认 dry-run，不执行付费生成，不部署或迁移生产。

TaskStartSnapshot：origin/master `5884b9863a2e4cde68c24f61f85b6f1c8744eaea`；主工作区干净；分支 `codex/runninghub-outfits`。ArchitectureReviewRequired：是，评估数据库状态机、凭据边界和重复计费窗口。新增独立业务模块，复用 SQLAlchemy、token_service、MinIO、媒体资产和 React/HeroUI 应用壳，不引入第二套队列框架。

基线：AGENTS.md；agent-safety/testing/git-workflow；architecture-overview/process-architecture；external-api；media-asset-library；frontend-development/frontend-progressive-migration。官方五个接口已通过公开 Markdown 核对，API 响应结构与来源文档一致。复用搜索：仓库无 RunningHub 接入；官方 GitHub 技能不是多实例持久化队列 SDK；沿用已安装 httpx（BSD-3-Clause）、SQLAlchemy（MIT）和 MinIO（Apache-2.0），不新增 Python 运行依赖。

验收：mock HTTP 和真实隔离 PostgreSQL 验证计划/幂等/并发/恢复；FFmpeg 验证最终规格；组件和浏览器验证只读/写入状态与恢复操作。所有测试仅由 test_isolated.sh 或 test_baseline.sh 包装进入。生产迁移、服务重启、真实付费联调不在本次执行范围。
