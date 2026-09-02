# analytics_sync v2 化方案

> 状态：**已评审，决策已拍板（2026-09-02）** · 开发中
> 目标：把 `/v1/analytics/sync/*` ingest 链路从「挂载进 v2 进程的孤岛包」变成「v2 原生子系统」——
> schema 入 v2 命名空间、存储走 SQLAlchemy/alembic、路由入 `api/v2/`，**线上协议字节级不变**。

**已拍板决策**：

1. schema = `analytics`；6 表重命名为 `ad_*` 前缀（`ad_records` / `ad_daily_pages` /
   `ad_daily_completeness` / `ad_cursors` / `ad_shop_timezones` / `ad_audit_log`）
2. retention **纳入**本次 scope（sync-worker 日级 job）
3. **不做 /v1 alias 双挂载**，直接单挂 `/v2/analytics/sync/*`。⚠️ 后果：插件路径后缀硬编码
   `/v1/...`，app 上线即 404 —— 必须与插件发版同窗口；零停机备选 = nginx 层 rewrite（不动 app）
4. schema 名保持 `analytics`

## 0. 现状摸底结论（勘察确认）

### 代码与挂载

| 资产 | 位置 | 现状 |
| --- | --- | --- |
| handler + Pydantic 模型 | `analytics_sync/app.py` | `APIRouter`，被 `tts_erp_v2/app.py:76` 以 `prefix="/v1/analytics/sync"` 挂载 |
| 纯领域（幂等键/StorageKey/类型） | `analytics_sync/domain.py` | 纯函数，无 I/O —— 协议契约代码 |
| 存储层 | `analytics_sync/pg_repositories.py` | **裸 psycopg**，自带 `connect()`，不经 v2 `db/base.py` session 工厂 |
| 建表 | `analytics_sync/schema.sql` | 幂等 SQL，**与 alembic 双轨**（测试库 = alembic + 这份 SQL） |
| 清理 | `analytics_sync/retention.sql` | 存在但**无任何 cron/timer 在跑**（`crontab -l` 为空）→ audit 表无界增长（已 55k 行） |
| auth 分类 | `tts_erp_v2/middleware/auth.py:132-134` | 特判 `/v1/analytics/sync` 前缀 → readwrite |

### 数据（生产实测 2026-09-02）

6 张表全在 `public` schema：`analytics_records`(75) / `analytics_daily_pages`(75) /
`analytics_daily_completeness`(59) / `analytics_cursors`(33) / `analytics_shop_timezones`(43) /
`analytics_audit_log`(55k)。`schema_tts_erp.sql` 含这些表（regen 自生产库）。

### 外部依赖方（不可破坏清单的来源）

1. **Chrome extension `tk-adv-cost-monitor`**：代码硬编码 `${baseUrl}/v1/analytics/sync/cursor|batches`
   （`analytics_sync/tech-doc/plugin-integration.md:339,350`）。**baseUrl 可配，路径后缀不可配**。
2. **nginx**：`~/setup/nginx/conf.d/services.conf`（不在本仓库）有 `location /v1/analytics/sync/`
   反代块，`proxy_pass` 不带尾斜杠原样透传。注释里有 2026-08-30 404 事故的教训。
3. **协议契约**：envelope 形状、幂等键推导、错误码、`errors[]` 三元组、cursor items 必须 echo
   `sellerId/advertiserId`、`protocolVersion` 1/2 语义 —— 全部逐字节锁定（mount/errors 测试守着）。

## 1. 目标架构

```text
Chrome extension ──HTTPS──▶ nginx ──▶ tts-erp :9877 (tts_erp_v2.app)
                                          │
                                          ▼
                            api/v2/analytics.py        ← 新路由（handler 层）
                            prefix=/v2/analytics/sync  （/v1 保留 alias，见 D1）
                                          │ Depends(get_session)
                                          ▼
                            tts_erp_v2/analytics/      ← 新领域包
                              domain.py                （纯函数，从 analytics_sync/domain.py 平移）
                              repository.py            （SQLAlchemy 重写 pg_repositories）
                                          │
                                          ▼
                            db/models/analytics.py     ← 6 个 model，{"schema": "analytics"}
                            db/base.py SCHEMAS += "analytics"
                                          │
                                          ▼
                            PostgreSQL: analytics schema（第 10 个）
                              records / daily_pages / daily_completeness /
                              cursors / shop_timezones / audit_log
```

## 2. 关键决策

### D1 路由路径：~~双挂载过渡~~ → 已改为**单挂 /v2，直接切**（用户拍板）

- 唯一路径：`/v2/analytics/sync/{cursor,batches}`；`/v1/analytics/sync/*` 随发布**直接下线**。
- ⚠️ 部署约束：Chrome extension 路径后缀硬编码（`plugin-integration.md:339,350`），
  app 切到 /v2 后旧插件立即 404。**发布窗口必须与插件发版同步**；若需零停机，在 nginx
  加 `location /v1/analytics/sync/ { rewrite ^/v1/analytics/sync/(.*)$ /v2/analytics/sync/$1 break; proxy_pass ...; }`
  做过渡（不动 app 代码）。
- nginx：新增 `location /v2/analytics/sync/` 块（复制现有块改路径）；/v1 块下线或改 rewrite。
- `auth.py` 分类规则：`/v1/analytics/sync` 特判**替换**为 `/v2/analytics/sync`（readwrite）。
  注意：**不加规则则未知路径默认按 admin 拦截**，插件会全体 403 —— 本方案最高危的一行，测试先行。

### D2 表迁移：`CREATE SCHEMA` + `SET SCHEMA` + `RENAME`，单个 alembic migration

```sql
CREATE SCHEMA IF NOT EXISTS analytics;
ALTER TABLE public.analytics_records            SET SCHEMA analytics;  -- RENAME TO ad_records
ALTER TABLE public.analytics_daily_pages        SET SCHEMA analytics;  -- RENAME TO ad_daily_pages
ALTER TABLE public.analytics_daily_completeness SET SCHEMA analytics;  -- RENAME TO ad_daily_completeness
ALTER TABLE public.analytics_cursors            SET SCHEMA analytics;  -- RENAME TO ad_cursors
ALTER TABLE public.analytics_shop_timezones     SET SCHEMA analytics;  -- RENAME TO ad_shop_timezones
ALTER TABLE public.analytics_audit_log          SET SCHEMA analytics;  -- RENAME TO ad_audit_log
```

- `SET SCHEMA` 是**元数据操作，零数据拷贝**，索引/约束/owned sequence 连带迁移；最大表 55k 行，锁毫秒级。
- 表名拍板：`analytics_` 前缀 → `ad_` 前缀（广告分析域标识；schema 即命名空间）。
- downgrade = 反向 RENAME + SET SCHEMA 回 `public.analytics_*`。
- ⚠️ 迁移后**裸表名引用全部失效**（`public` 不再是隐式前缀）——这正是 D3 同步落地的原因，
  两件事必须在**同一次发布**里完成，不能拆开。
- 备选（不推荐）：新建表 + `INSERT SELECT` 拷贝。可控但多一倍 moving parts，且 sequences/
  constraints 要手工重建。SET SCHEMA 更笨更安全。

### D3 存储层：psycopg → SQLAlchemy，事务语义逐项映射

现有 `upsert_records` 的四个关键语义，在 SQLAlchemy 下的对应：

| 现有（psycopg） | 新（SQLAlchemy） |
| --- | --- |
| `SELECT ... FOR UPDATE` 锁 completeness 行 | `select(...).with_for_update()` |
| `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING RETURNING id` | `pg_insert(...).on_conflict_do_nothing().returning(...)` |
| 循环后批量 `_recompute_completeness` / `_recompute_cursors` | 同一 `Session` 事务内执行同样的 SQL（Core `text()` 或 ORM） |
| `write_audit` best-effort 独立提交 | 保持独立连接/独立事务（审计失败不影响主请求）——用 `session_scope()` 或独立 engine connection |

- handler 层改 v2 惯例：`sess: Session = Depends(get_session)`（`tts_erp_v2/api/deps.py`）。
- 现有 handler 里 `run_sync(...)` 包装可以去掉 —— v2 其他路由的 handler 是同步 `def`，
  FastAPI 自动丢线程池，与 commerce/reporting 一致。
- `pg_repositories.connect()`、`_db_url.normalize` 依赖全部删除，统一走 `db/base.py` 的
  engine/session 工厂（pool_pre_ping 已配）。

### D4 协议字节级不变（验收红线）

以下任何一项变化都算回归，必须有测试守着：

- 幂等键推导：`canonical_json_for_key` + sha256（`domain.py` 平移，逻辑零改动）
- 成功 envelope：`{code:0, requestId, data:{accepted[], rejected[]}}`；cursor 的
  `{code:0, requestId, data:{timezone, items[], nextCursor:null}}`
- cursor items **必须 echo `sellerId`/`advertiserId`**（2026-08-30 协议事故回归点）
- 错误 envelope：`{code, message, requestId, retryable, errors?}`，`errors[]` 为
  消毒后的 `{loc, msg, type}` 三元组
- 状态码矩阵：413 / 400(MALFORMED_JSON|SCHEMA_INVALID|UNSUPPORTED_PROTOCOL_VERSION) /
  403(SCOPE_DENIED) / 500(INTERNAL_ERROR, retryable=true)
- `scope_grants` 语义：空/`*` 放开；`seller:`/`advertiser:` 组内 OR；未知前缀 fail-closed
- `protocolVersion`（payload 内 1/2）与路由 v1/v2 是**两个独立版本轴**，不联动

### D5 包去向：`analytics_sync/` 整体拆除

| 旧 | 新 |
| --- | --- |
| `analytics_sync/domain.py` | `tts_erp_v2/analytics/domain.py`（平移，零逻辑改动） |
| `analytics_sync/pg_repositories.py` | `tts_erp_v2/analytics/repository.py`（SQLAlchemy 重写） |
| `analytics_sync/app.py` | `tts_erp_v2/api/v2/analytics.py`（handler + Pydantic 模型） |
| `analytics_sync/schema.sql` | 废弃 → alembic migration（schema 单轨化，测试库也不再双轨） |
| `analytics_sync/retention.sql` | 逻辑移植为 sync-worker job（见 D6） |
| `analytics_sync/tech-doc/*` | 合并进 `tech-doc/analytics-sync*.md` |
| `analytics_sync/README.md` | 内容并入上述文档，删包 |

### D6 顺手补齐 retention 运维（建议纳入 scope）

现状：`retention.sql` 无 cron 执行 → audit 表无界增长。v2 化时注册为 sync-worker
`JOBS` 里的日级 job（`analytics.retention`，90d records + 30d audit），与
`token.refresh` 等 job 同模式。若评审认为超 scope，则至少落一个 `scripts/` cron 入口 +
安装说明。

### D7 测试计划（TDD 顺序）

1. **先红**：`tests_v2/api/test_analytics_v2_contract.py`
   - `/v2/analytics/sync/cursor` 正常返回 envelope（readwrite key）；匿名 401 / readonly 403
   - `/v1/analytics/sync/*` 不再存在（404 —— v2 挂载后旧路径无路由）
   - batches 全错误矩阵（413/400×4/403/500）在 v2 路径下形状不变
   - cursor items echo sellerId/advertiserId（v2 路径）
2. **改**：`test_analytics_sync_mount.py` / `test_analytics_sync_errors.py`
   - 去掉 session 级 autouse `ALTER TABLE` fixture（alembic 单轨后不需要）
   - 表名引用改 `analytics.*`
3. **conftest**：测试库初始化去掉 `analytics_sync/schema.sql` 这轨，纯 alembic
   （migration 里 `CREATE SCHEMA` + 建表由 models 注册驱动）。
4. 跑 `scripts/test.sh fast` 全量 + `python3 test_e2e.py` 冒烟。

### D8 文档与运维面

- `AGENTS.md`：§3 端点表加 `/v2/analytics/sync/*`，§9.5 stability matrix 更新
  （仍为 internal / not external-stable）
- `tech-doc/external-api.md`：路径条目更新
- `setup/analytics-sync.md`：重写（无独立 schema.sql 步骤，改 alembic）
- `schema_tts_erp.sql`：`python3 scripts/regen_schema.py` 重生成
- `CHANGELOG.md`、`handoff.md`
- nginx 块更新（`~/setup/nginx/conf.d/services.conf`，不在本仓库，部署时改）

## 3. 分阶段执行

| Phase | 内容 | 验收 |
| --- | --- | --- |
| 0 | 契约基线：v2 路径测试先行（红） | 测试写好且失败 |
| 1 | models + alembic migration（D2）+ repository 重写（D3）+ /v2 路由 + auth 分类 + retention job（D6） | D7 测试全绿；`alembic upgrade head` 后 `\dt analytics.*` 6 表 |
| 2 | 部署：migration → restart → nginx 加 /v2 块（/v1 块下线或 rewrite）→ **同窗口插件发版** → 冒烟 | 生产 audit_log 出现 /v2 路径行；插件恢复上传 |
| 3 | 文档收尾（D8）+ `analytics_sync/` 删包 + schema regen | 仓库无 analytics_sync 引用 |

Phase 1+2 是同一发布单元（D2/D3 不可拆）。插件发版与 Phase 2 同窗口。

## 4. 回滚

- DB：`alembic downgrade`（表回 `public.analytics_*`，数据无损——SET SCHEMA/RENAME 全程可逆）
- 代码：git revert 恢复 /v1 挂载
- nginx：/v1 块若改 rewrite 则还原
- ⚠️ 回滚窗口内插件若已切 /v2 路径，需同步回滚插件配置

## 5. 风险清单

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| `auth.py` 漏加 /v2 前缀分类 → 默认 admin → 插件全体 403 | **高** | D7.1 auth 分类测试先行；部署后立刻用 readwrite key 冒烟 |
| SET SCHEMA 与代码切换不同步 → 裸表名 42P01 | 高 | D2/D3 同发布单元；migration 与 restart 在同一窗口；冒烟含 batches 写路径 |
| 单挂 /v2 后旧插件立即 404 | **高（已接受）** | 发布窗口与插件发版同步；零停机备选 = nginx rewrite（D1） |
| SQLAlchemy 重写改变并发语义（FOR UPDATE / ON CONFLICT） | 中 | PAGE_COUNT_CONFLICT 并发契约测试；逐条映射表（D3）评审 |
| retention job 误删 | 低 | 保留期与现状文档一致（90d/30d）；job 先于删包上线观察 |
| `.worktrees/fix-auth-loop` 里有旧拷贝 | 低 | 不动 worktree；只改主 checkout |

## 6. 开放问题

已全部拍板（见文首决策清单）。
