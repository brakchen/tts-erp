# after_sales 合并迁移技术方案 v2（待 review）

> 依据：`tech-doc/data-model.md` §6 D4 决策。
> v2 变更（2026-08-27 review）：**砍掉 view 兼容层和观察期**，直接切换 —
> 建新表 → 同步数据 → 改服务代码 → 删旧表。
> 外部契约不变的目标不变，但靠**端点 SQL 列别名**保证，不靠 view。

## 1. 目标与约束

| 项 | 内容 |
| --- | --- |
| 目标 | `returns` + `cancellations` 合并为一张 `after_sales` 表，旧表删除 |
| 硬约束 1 | 外部端点**响应契约**不变：`GET /db/returns`、`GET /db/returns/{id}`（含 `include_raw`、`refund_amount`/`refund_currency` 计算字段、cursor 分页）、`GET /db/cancellations` —— 响应 JSON 字段名/类型/分页行为逐字节一致 |
| 硬约束 2 | 内部端点契约不变：`POST /sync/returns`、`POST /sync/cancellations`、`POST /returns/search`、`POST /cancellations/search` |
| 手段 | 端点 SQL 改查 `after_sales`，用列别名输出旧字段名；`persist_*` 改写新表；验证通过后 `DROP TABLE` 旧表 |
| 数据量 | returns 24 行 + cancellations 160 行，backfill 秒级 |

## 2. 现状读写路径（全部改动点）

**写入方**（仅此 2 个函数）：

| 函数 | 位置 | 现状 |
| --- | --- | --- |
| `persist_return` | `tts_erp.py:719` | `INSERT INTO returns ... ON CONFLICT (return_id)` |
| `persist_cancellation` | `tts_erp.py:782` | `INSERT INTO cancellations ... ON CONFLICT (cancel_id)` |

调用链：`POST /sync/returns|/sync/cancellations`（FastAPI）→ `persist_*`；sync_cron 走 HTTP，不直接触库。

**读取方**（3 个端点 SQL 需要改，列别名保持响应字段名不变）：

| 位置 | 现状 | 改法 |
| --- | --- | --- |
| `tdd/tts_erp_fastapi.py:778` `db_list_returns` | `SELECT return_id, ..., return_status, ... FROM returns` | `FROM after_sales WHERE kind='return'`，`id AS return_id`、`status AS return_status` 等别名 |
| `tdd/tts_erp_fastapi.py:838` returns detail | `FROM returns WHERE return_id = %s` | `FROM after_sales WHERE kind='return' AND id = %s` |
| `tdd/tts_erp_fastapi.py:861` `db_list_cancellations` | `SELECT cancel_id, shop_id, cancel_status, ... FROM cancellations` | 同上模式 |

注意 `db_list_returns` 的 keyset 游标 `(create_time, return_id)`：改后变成
`(create_time, id)`，游标 codec 不变（值不变，只是列名映射）。

**测试**：`tdd/test_sync_returnrefund.py`、`tdd/test_tts_erp_routes*.py` 等 —
直插旧表的 fixture 改插 `after_sales`。

**文档/脚本**：`demo_queries.sql` 有 6 处 `FROM returns/cancellations` —
这次没有 view 兜底，**需要同步改**（查 `after_sales WHERE kind=...`）。

## 3. 目标 schema

```sql
CREATE TABLE IF NOT EXISTS public.after_sales (
    kind         text NOT NULL,           -- 'return' | 'cancellation'
    id           text NOT NULL,           -- 上游单号：return_id / cancel_id 原样
    shop_id      text NOT NULL,
    order_id     text,
    status       text,
    reason       text,
    reason_text  text,                    -- cancellation 侧独有（return 恒 NULL）
    type         text,
    role         text,
    should_replenish_stock boolean,       -- cancellation 侧独有（return 恒 NULL）
    create_time  bigint,
    update_time  bigint,
    raw          jsonb NOT NULL,
    synced_at    timestamptz DEFAULT now() NOT NULL,
    CONSTRAINT after_sales_kind_check CHECK (kind IN ('return', 'cancellation'))
);
ALTER TABLE ONLY public.after_sales
    ADD CONSTRAINT after_sales_pkey PRIMARY KEY (kind, id);
CREATE INDEX IF NOT EXISTS idx_after_sales_order   ON public.after_sales (order_id);
CREATE INDEX IF NOT EXISTS idx_after_sales_shop_ct ON public.after_sales (shop_id, create_time DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_after_sales_status  ON public.after_sales (kind, status);
```

设计要点：

- PK `(kind, id)`：不为"一单只有一次售后"背书；`order_id` 只做索引列。
- `kind` 加 CHECK 约束，防写入侧打错判别值。
- `idx_after_sales_shop_ct` 列序 `(shop_id, create_time DESC, id DESC)` 对齐
  `db_list_returns` 的 `ORDER BY create_time DESC, return_id DESC` keyset 分页。
- 列名用泛化名 `status`/`reason`/`type`，端点 SQL 层别名回 `return_status`
  等旧名（§7 待拍板点 1）。

## 4. 迁移步骤（按序执行）

### Phase 0 — 备份

```bash
docker exec postgres pg_dump -U postgres -d tts_erp \
  -t returns -t cancellations > backup_after_sales_$(date +%Y%m%d).sql
```

### Phase 1 — 建新表 + 全量 backfill（旧表照常服务，零影响）

```sql
-- §3 DDL 全量执行，然后：
INSERT INTO after_sales (kind, id, shop_id, order_id, status, reason, reason_text,
                         type, role, should_replenish_stock, create_time, update_time,
                         raw, synced_at)
SELECT 'return', return_id, shop_id, order_id, return_status, return_reason, NULL,
       return_type, role, NULL, create_time, update_time, raw, synced_at
FROM returns
UNION ALL
SELECT 'cancellation', cancel_id, shop_id, order_id, cancel_status, cancel_reason,
       cancel_reason_text, cancel_type, role, should_replenish_stock, create_time,
       update_time, raw, synced_at
FROM cancellations
ON CONFLICT (kind, id) DO NOTHING;
```

### Phase 2 — 校验（全过才继续）

```sql
-- 行数对齐（两个都应为 true）：
SELECT (SELECT COUNT(*) FROM after_sales WHERE kind='return')       = (SELECT COUNT(*) FROM returns),
       (SELECT COUNT(*) FROM after_sales WHERE kind='cancellation') = (SELECT COUNT(*) FROM cancellations);
-- 逐行 raw 一致性（应 0 行）：
SELECT r.return_id FROM returns r JOIN after_sales a ON a.kind='return' AND a.id=r.return_id
WHERE a.raw <> r.raw LIMIT 5;
-- refund 计算字段平价（应 0 行）：
SELECT r.return_id FROM returns r JOIN after_sales a ON a.kind='return' AND a.id=r.return_id
WHERE COALESCE((a.raw->'refund_amount'->>'refund_total')::numeric, -1)
   <> COALESCE((r.raw->'refund_amount'->>'refund_total')::numeric, -1) LIMIT 5;
```

### Phase 3 — 代码改造（TDD：先改测试）

1. **测试先红**：改 `tdd/test_sync_returnrefund.py` 等 — 断言写入落在
   `after_sales` 且 `kind` 正确；直插旧表的 fixture 改插 `after_sales`；
   端点响应字段名断言**保持不变**（契约守卫）。
2. `persist_return` / `persist_cancellation`：INSERT 目标改 `after_sales`，
   列名映射（`return_id→id`、`return_status→status`…），`kind` 写字面量，
   `ON CONFLICT (kind, id)`。
3. 3 个读端点 SQL 改查 `after_sales`，SELECT 列加别名保持响应字段名不变
   （`id AS return_id` 等），加 `WHERE kind = '...'`；游标谓词
   `(create_time, return_id)` → `(create_time, id)`。
4. `demo_queries.sql` 6 处查询同步改。

### Phase 4 — 部署 + 追平增量

1. `bash restart.sh` 部署新代码（此刻起旧表不再有任何读写）。
2. **追平 backfill**：Phase 1 到部署之间 sync cron 可能往旧表写过新行，
   重跑一次 Phase 1 的幂等 INSERT（`ON CONFLICT DO NOTHING`）补齐。
3. 重跑 Phase 2 行数校验，确认对齐。

### Phase 5 — 验证

- 契约测试：`bash /tmp/verify_external_api.sh`（/db/returns 分页、
  /db/returns/{id} + include_raw、时间过滤、游标 400）；
- 增量同步冒烟：`curl -X POST :9877/sync/returns -d '{"shop_id":"..."}'` 后
  `SELECT COUNT(*) FROM after_sales WHERE kind='return'` 应增长；
- `python3 /tmp/test_tts_erp.py` 端到端。

### Phase 6 — 删旧表

```sql
DROP TABLE returns, cancellations;
```

同步更新：`schema_tts_erp.sql`（`scripts/regen_schema.py`）、`AGENTS.md`
端点表、`tech-doc/data-model.md` §2.1/§3/§4、CHANGELOG。

## 5. 回滚预案

- **Phase 5 验证失败、旧表未删**：`git revert` Phase 3 代码 + `restart.sh`，
  新数据在 `after_sales` 里保留，重跑 Phase 1 backfill 反向灌回旧表即可
  （写一段对称的 INSERT … FROM after_sales，或直接用 Phase 0 备份）。
- **旧表已删后才发现问题**：从 Phase 0 的 pg_dump 备份恢复两张表。

## 6. 风险清单

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| 端点 SQL 别名写错导致响应字段名变化（契约破坏） | 中 | 测试断言响应字段名不变（Phase 3 第 1 步）；verify_external_api.sh 兜底 |
| Phase 1→4 之间旧表收到新行 | 低 | Phase 4 幂等追平 backfill + 行数复核 |
| 游标谓词列名映射遗漏（return_id→id） | 低 | 分页测试用例覆盖；索引列序已对齐 |
| 遗漏直插旧表的测试 fixture | 中 | 旧表删除后所有残留引用直接报错，CI 全量测试兜底 |
| `demo_queries.sql` 等文档 SQL 失修 | 低 | Phase 3 第 4 步同步改 |

## 7. 待 review 拍板的点

1. **§3 列名**：泛化名 `status`/`reason`/`type`（端点别名回旧名）。
   若倾向新表保留 `return_status` 等原名可以换 —— 代价是 cancellation 行
   列名语义错位。
2. **Phase 4→6 的节奏**：验证通过后**当天**删旧表，还是留到下次部署？
   （备份已有，建议当天删，避免"僵尸表又被人查"的窗口。）
3. **`reason_text` / `should_replenish_stock` 对 return 恒 NULL** 是否接受
   （当前 returns 响应里本就没有这两个字段，建议保持 NULL）。
