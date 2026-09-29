# 订单/物流同步数据库迁移 Handoff

日期：2026-09-20
涉及版本：订单/物流协议对齐与可恢复同步改造
涉及迁移：`alembic/versions/0034_plugin_tracking_action_code.py`

## 为什么需要执行数据库操作

本次后端会把 TikTok 物流轨迹中的 `action_code` 持久化到
`plugin.tracking_events`，并用终态白名单判断物流是否还需要继续采集。
代码模型、SQL schema 和 Alembic 迁移已经同步，但目标数据库必须先增加该列。

## 迁移内容

```sql
ALTER TABLE plugin.tracking_events
ADD COLUMN IF NOT EXISTS action_code integer;
```

迁移链：

```text
0033_drop_plugin_raw_log -> 0034_plugin_tracking_action_code
```

## 执行顺序

1. 确认当前连接的是目标数据库，并确认不是误连其他环境。
2. 先备份/确认已有数据库备份策略，再查看当前迁移状态：

   ```bash
   alembic current
   ```

3. 执行迁移：

   ```bash
   alembic upgrade head
   ```

4. 确认迁移完成后，再部署并重启 API 服务；随后按现有流程重启同步 worker（如部署环境有独立 worker）。

## 验证

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'plugin'
  AND table_name = 'tracking_events'
  AND column_name = 'action_code';
```

预期返回一行，`data_type` 为 `integer`。

部署后重点观察：

- `plugin.tracking_events.action_code` 是否开始写入；
- `/v2/order-sync/reconcile` 的物流结果是否正确返回 `isTerminal`、`terminalReason`；
- `plugin.plugin_logs` 中订单、物流、结算 dump 是否有成功/失败健康记录；
- 物流失败订单是否能在下一轮从持久化游标继续处理。

## 回滚注意事项

如果必须回滚代码，应先回滚到不读取/写入 `action_code` 的后端版本。只有确认旧代码已运行且经过人工批准后，才考虑执行：

```bash
alembic downgrade 0033_drop_plugin_raw_log
```

该操作会删除列，属于破坏性 schema 变更；不得在未确认目标环境和代码版本前执行。

## 当前验证与限制

- 插件完整测试：52 个测试文件、844 个用例通过。
- 插件 TypeScript 检查和生产构建通过。
- 后端 Python 编译检查通过。
- 当前开发机缺少 SQLAlchemy 与测试数据库，因此未执行真实数据库迁移，也未执行后端数据库集成测试。
