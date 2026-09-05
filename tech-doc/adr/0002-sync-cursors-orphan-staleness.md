# ADR-0002: sync_cursors 孤儿 / 陈旧游标 处理

- **Status**: accepted
- **Date**: 2026-09-04
- **Deciders**: tts-erp backend

## 1. Context(背景)

2026-09-04 数据质量审计发现 `integration.sync_cursors` 表有 2 行 `updated_at` 显著陈旧:

```text
job_name                    | scope     | last_updated (CST)      | staleness
----------------------------+-----------+-------------------------+-----------------
tiktok.orders               | 7494763368967603447 | 2026-09-04 15:56:02 | 8 分钟        ← 活跃
tiktok.after_sales          | 7494763368967603447 | 2026-09-04 15:54:47 | 9 分钟        ← 活跃
tiktok.logistics            | 7494763368967603447 | 2026-09-04 15:53:25 | 10 分钟       ← 活跃
tiktok.finance.statements    | 7494763368967603447 | 2026-09-01 10:44:03 | 3 天 5 小时    ← 陈旧
tiktok.products             | 7494763368967603447 | 2026-08-30 18:45:56 | 4 天 21 小时  ← 陈旧
```

需要判定:这两行 cursor 是 "job 异常" 还是 "上游无数据"?

## 2. 调查结论

### 2.1 `tiktok.finance.statements` — **孤儿 cursor(job 已删除)**

`scheduler.py` 的 startup 日志显示 sync worker 注册 7 个 jobs:

> tts-erp sync-worker starting (7 jobs registered): tiktok.after_sales, tiktok.finance, tiktok.logistics, tiktok.order_detail, tiktok.orders, tiktok.products, token.refresh

`integration.sync_jobs` 表查询 `tiktok.finance.statements` 近 5 天 0 条记录。

**结论**:`tiktok.finance.statements` job 已被合并 / 删除(可能 8/31 跟 `tiktok.finance` 整合),但 cursor row 没被清。cursor 是"无主"的孤儿行,3 天没动是因为根本没 job 跑。

### 2.2 `tiktok.products` — **活跃 + 上游无新数据**

`sync_jobs` 表查询 `tiktok.products` 近 5 天 31 条 run,最后 2026-09-03 22:20:57。

**结论**:job 在正常跑(每 6h 一次),但这个 shop (Bridge nook) 的 products 列表从 8/30 以来**没有新商品**。cursor 停留在 max(source_updated_at) = 8/30,这是真实"无新数据"而非"故障"。

## 3. Decision(决定)

### 3.1 命名约定:`sync_cursors` 永远反映当前活跃的 jobs

**Rule**:删除一个 `JOBS` 条目时,必须同步:

- 1. `sync_jobs` 中的历史记录(保留,仅做审计)
- 1. `sync_cursors` 中的对应 cursor 行(删除 — 没 job 在跑,cursor 无意义)

### 3.2 主动监控 staleness

加 staleness 告警,默认阈值 6 小时:

```sql
-- 写进 ops 看板
SELECT job_name, scope, EXTRACT(EPOCH FROM now() - updated_at) / 3600 AS staleness_hours
FROM integration.sync_cursors
WHERE now() - updated_at > interval '6 hours'
ORDER BY staleness_hours DESC;
```

阈值 6h 是"job 30min 一次"的 12 倍,留出 sync jitter buffer。

### 3.3 staleness ≠ bug 的判断

收到告警时,先查 `integration.sync_jobs`:

- `last_run > now() - 1h` + `status = 'succeeded'` → 上游无新数据,**不是 bug**
- `last_run > 1h` 或 `status = 'failed'` → 真 bug,排查

只有持续 24h+ "上游无新数据" 才需要人工 confirm 是不是该停 job。

## 4. 立即执行(本次落地)

```sql
-- 孤儿 cursor:job 已被合并,清掉 row
DELETE FROM integration.sync_cursors WHERE job_name = 'tiktok.finance.statements';
```

`tiktok.products` cursor 保留(正常,等上游有数据)。

## 5. 长期方案(下次修 scheduler.py 时)

- `scheduler.py` 启动时检测 `sync_cursors` 里的 `(job_name, scope)` 跟当前 `JOBS` 列表的差集,自动清理孤儿 cursor(加 warning log,不直接删 — 保留一段时间供人 review)
- `delete_job()` 工具函数:从 `JOBS` 删一个 job 时,同步清 `sync_cursors` 对应行

## 6. 关联

- `tech-doc/adr/0001-time-fields-convention.md` — `synced_at` / `updated_at` 字段语义
- `tts_erp_v2/sync_worker/scheduler.py` — `JOBS` 列表
- `tts_erp_v2/jobs/tiktok/finance.py` — 当前 `tiktok.finance` 整合实现
