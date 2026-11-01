# 同步任务的事务边界

适用于 `sync_worker.scheduler.JOBS` 的全部任务。任务入口接收**没有活动事务**的
SQLAlchemy Session；调度器使用 `with session_factory() as session` 关闭会话，
任务使用 `with session.begin()` 提交或回滚每个明确的数据单元。不要在调度器、
底层凭据函数或异常处理里额外调用 `session.commit()`。

## 执行与失败

1. 开始记录使用独立短事务，执行中即可观察到 `running`。
2. 配置、账号、游标与凭据在短读事务内转换为值快照；跨事务不得依赖会触发
   lazy load / expired refresh 的 ORM 实体。
3. HTTP、MinIO、限流等待和重试在事务外执行。
4. 完整订单及其行、商品及其 SKU、包裹及其明细/赠品分别原子落库。
   财务 header 是独立源事实；每条财务 transaction 与全部 components 原子落库。
5. 运行结果独立提交。异常单元由 SQLAlchemy 回滚，已完整提交的单元保留。
   任务/提交异常向上传播，单次运行 CLI 返回非零；不能记录 `ok` 后吞掉提交失败。

`jobs.runner.run_job` 只管理审计生命周期，不包裹业务事务。
`sync_worker.job_runner.run_with_sync_job` 复用这一 owner，将 `JobResult` 写入审计。
测试布置产生的活动事务必须在进入任务前结束；测试 fixture 的外层隔离事务不变。

## 游标与重试

全部读取结果成功完成持久化之后才更新游标。任何必需实体或子实体失败都冻结
该任务/子任务的游标，避免“旧数据失败、新数据成功”把失败数据永远推到查询范围外。
商品图片失败同样保留旧产品游标，以便下次补齐。遇到持续解析问题应修复数据契约，
不能靠推进游标隐藏问题。

完成单元先提交、最终游标随后提交：若进程在两者之间退出，下次会重放旧窗口。
订单、商品、包裹和财务写入复用既有自然键 upsert；游标不会领先于事实。
审计记录不承担游标职责。

## 其他任务

- 妙手账号初始化独立提交，公共账号行锁不跨分页与重试。
- 图片下载、对象上传在事务外；写回前校验源 URL，避免旧图覆盖新产品来源。
- 成本/利润重算按最多 100 个 SPU 分批；采购价桥接与订单行关联按主键分批。
- 费率先读取聚合值，按店铺写入。
- 盈利变化物化保留必需的只读 repeatable-read 计算快照，并按 anchor 原子替换，
  避免读到半份派生快照；它没有跨网络的写事务。不得随意把单份替换拆成裸 DELETE
  和独立 INSERT。
- monitor health 在短事务中领取带过期时间的通知 lease，释放心跳行锁后发飞书；
  回写按 owner 和故障/恢复发生时间确认，不覆盖发送期间的新心跳。远端已接受而进程
  尚未持久化确认时崩溃，仍可能重发，因为飞书 bot 无远端幂等键。

## 凭据

`refresh_if_needed` 的 refresher 接收不可变 `CredentialsView`，HTTP 使用的 refresh
token、Service ID 与写回 CAS 来自同一快照，不能另行读取一份 token。App 配置按
同一快照的店铺、Service ID 和 App revision 解析。HTTP 期间不持有凭据行锁；写回
短事务重新校验密文，发现新授权则保留新授权。凭据持久化异常不能当成上游 advisory
错误吞掉。活动业务事务调用刷新会被 `Session.begin()` 拒绝，不能连带提交业务。

SQLAlchemy 是唯一事务实现，使用项目已有的 `sqlalchemy>=2.0`（MIT）：
[Transactions and Connection Management](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)。

验证仅经 `bash scripts/test_isolated.sh ...`；最终基线检查为
`bash scripts/test_baseline.sh`。生产部署和 sync-worker 重启仍由人工操作。
