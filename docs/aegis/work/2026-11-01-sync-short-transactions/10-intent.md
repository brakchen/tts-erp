# 定时任务短事务修复

- 用户授权：修复全部定时任务的大事务风险，先缩短边界，再使用 `with session.begin()` 管理事务和 Session 上下文管理生命周期。
- TaskStartSnapshot：`origin/master=ea779d91`；分支 `codex/sync-short-transactions`；独立 managed worktree；主 checkout 的 `plugins/monitor` 为外部 WIP，不触碰。
- Decision: code-change。CanonicalOwner：任务执行器、各任务的读/联网/写边界、token_service。ArchitectureReviewRequired：是，涉及持久化和失败契约。
- 保留：接口、签名、字段解析、金额口径、幂等 upsert、已完成的业务单元；失败不推进未完成同步的游标。
- 退休：任务级大事务、凭据刷新提交业务 Session、失败路径提交未完成业务、提交失败返回成功。
- 不执行生产迁移、同步、重启或数据改写。`plugins/` 不在范围内。
- 验证：仅 `bash scripts/test_isolated.sh`；重点证明联网时无活动事务、开始记录对其他连接可见、完整单元回滚、游标重试、凭据刷新不提交业务、提交失败非零退出；最后 `bash scripts/test_baseline.sh`。
- TDD Mode: off；补充行为回归，不声称完成未执行的 RED/GREEN。复用 SQLAlchemy 2.0 Session.begin / sessionmaker 上下文（MIT，项目已有 sqlalchemy>=2.0）；官方说明：https://docs.sqlalchemy.org/en/20/orm/session_transaction.html 。
- Minimality：在既有 owner 拆事务，不引入自制事务引擎、自动提交代理、兼容 fallback 或新依赖；一致性分析快照与原子快照替换保留必要原子性。

## 计划

1. 执行器和 token：独立生命周期，异常传播，短凭据事务。
2. TikTok：全部网络移出事务，完整实体有界写入，成功后推进游标。
3. 妙手、图片、汇率、报表：账号初始化短事务；实体分批；纯 SQL 更新有界；一致性快照保留。
4. 通知：释放心跳行锁后联网，使用并发校验合并确认，避免覆盖新心跳。
5. 回归、文档、审查，提交和推送；验证通过才考虑集成。
