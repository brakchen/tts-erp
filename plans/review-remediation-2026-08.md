# tts-erp Review 修复计划（2026-08-27）

> 来源：2026-08-26 三路并行 review（DB / FastAPI 主服务 / 同步链路+legacy）+ 主线核实。
> 目标：修复全部确认问题（P1×11 + P2 批量），分 4 个 Wave 推进，每个 Wave 独立可部署、独立验证。
> 已验证的误报不进计划：token 续期链路（实际走的是独立 oauth-receiver 服务，cron 日志正常）。

## Context

Review 发现的核心问题分三类：

1. **可用性**：Auth 中间件在 event loop 上跑同步 psycopg（PG 抖动 = 全站挂死）；无效 API key 无负缓存且 401 绕过限流（零成本 DB 放大攻击面）；analytics_sync 的 async handler 同步写 PG。
2. **数据正确性**：cron 超时异常未捕获（一次慢调用炸整轮）；payments/statements 中途失败记 ok 导致水位前进、永久漏抓；物流 plan cron/服务端超时预算倒挂导致日志自相矛盾；analytics cursor 假分页会让客户端死循环。
3. **安全与 housekeeping**：`shops.shop_cipher` 明文写入路径与 backfill 写 NULL 自相矛盾；schema 有重复索引/死表/双 retention 实现；全链路无连接池；HTTP/重试 5 处重复实现且主链路最弱。

**已核实的前置事实**（决定排期安全性）：

- `shops.shop_cipher` 生产库当前全 NULL、无任何 SELECT 读取方 → 停写 + DROP COLUMN 安全
- `logistics_events` 0 行 → 可直接 DROP TABLE
- orders 652 行 / order_shippings 639 行 / analytics_records 1 行 → 索引变更无锁窗口压力
- venv 有 psycopg 3.3.4 但**无 psycopg_pool** → Wave 3 需 `pip install 'psycopg[pool]'`
- crontab 现状：仅有 token refresh（oauth-receiver 侧）、sync_cron（10min）、`cleanup_sync_log(60)`（每天）——analytics retention 确认未挂调度

## Approach

按"**先止血、再清 schema、后做结构、最后退役 legacy**"的节奏，4 个 Wave：

- **Wave 1（止血）**：纯代码改动，不动 schema，每个修复独立可回滚。修可用性 + 数据正确性 + shop_cipher 停写。
- **Wave 2（schema housekeeping）**：一批 DDL 迁移一次做完，走 `regen_schema.py` 流程。依赖 Wave 1 的 shop_cipher 停写先上线。
- **Wave 3（结构性）**：连接池 + HTTP 收敛 + 输入校验 + legacy 解耦。行为变化最大，放 schema 稳定后。
- **Wave 4（legacy 退役 + 杂项 + 文档）**：抽取共享模块、删死代码、修小 bug、更新文档。

每个 Wave 内遵守项目 TDD 约定：先写/改 `tdd/test_*.py`（事务回滚隔离 + `TEST_%` 哨兵，见 `tdd/conftest.py`），再实现到通过。每个 Wave 结束：`restart.sh` + healthz 200 + e2e（`tests/test_e2e.py`）+ 外部 API 验证脚本。

---

## Wave 1 — 止血（可用性 / 数据正确性 / 安全）

**预估 1.5–2 天。全部为代码改动，无 schema 变更，可逐个独立部署。**

### 1.1 Auth 中间件移出 event loop + 负缓存 + 拒绝请求计数

- 文件：`tdd/auth.py`、`tdd/rate_limit.py`、`tdd/tts_erp_fastapi.py`（middleware 顺序注释同步更新）
- 改动：
  - `AuthMiddleware.__call__` 中 `lookup_role` 包 `anyio.to_thread.run_sync`
  - 无效 key 写短 TTL（10–30s）负缓存
  - PG down（`psycopg.OperationalError`）时 fail-closed 返回 503（非 500 冒泡）
  - 被拒请求（401/403）也计入限流：在 Auth 拒绝路径按 `sha256(key)` 或 client IP 分桶计数（保持 middleware 顺序不变，在 auth.py 内部调用 rate_limit 的计数函数）
- 测试：`tdd/test_auth.py` 补负缓存 TTL、PG down → 503、无效 key 连发被 429 的用例

### 1.2 analytics_sync async handler 移出 event loop

- 文件：`analytics_sync/app.py`
- 改动：`post_batches` 内 `upsert_records` / `write_audit` 包 `anyio.to_thread.run_sync`（或 handler 改同步 `def` 返回 JSONResponse，取改动小者）
- 顺带修：文件末尾中间件顺序注释与实际相反（CORS→Auth→RateLimit）

### 1.3 `persist_shop` 停止写明文 shop_cipher

- 文件：`tts_erp.py`（`persist_shop`，约 1083–1110 行）
- 改动：INSERT/UPDATE 不再写 `shop_cipher` 列（与 `_backfill.py:79` 的 NULL 语义对齐）；列本身留到 Wave 2 DROP
- 注意：`tts_erp.py` 是 legacy 但 `persist_shop` 仍被 FastAPI `_tiktok_proxy` 调用，此改动影响生产路径，需 e2e 验证 order detail GET

### 1.4 `TokenError.status` 透传 + 错误文案收敛

- 文件：`tdd/tts_erp_fastapi.py`（`_get_creds`，178–182）
- 改动：先捕 `TokenError` → `HTTPException(status_code=e.status)`（404 = shop 未授权）；其余异常 → 502 固定文案，内部细节只进日志
- 测试：`tdd/test_tts_erp_routes.py` 补 404/502 分流用例

### 1.5 cron 失败语义三连修

- 文件：`sync_cron.py`、`tdd/tts_business.py`
- 改动：
  - `http_json`（197–211）补捕 `TimeoutError/OSError` → 返回 `_error` dict，单 plan 失败不扩散
  - `sync_payments`/`sync_statements` 中途页 `code != 0` 时置 `SyncResult.error`（保留已存数据，整体记失败）→ cron 窗口不前进，下 tick 重试
  - `main()` 中 `oauth_receiver_core.is_db_ok()` 为 False 时 `log.error` + 非零退出码（如 4），与"真没店铺"区分
  - `oauth_receiver_core.py` 末尾 module-load `db_init()` 失败只 print 不 raise 与其 docstring 矛盾——改 raise（fail-fast）或修正 docstring，取前者
- 测试：`tdd/test_sync_payments.py` / `test_sync_cron_discover.py` 补中途失败、DB down 退出码用例

### 1.6 物流 plan 超时预算对齐 + 防重叠

- 文件：`tdd/tts_erp_fastapi.py`（`sync_logistics_tracking`，841–936）、`sync_cron.py`
- 改动：
  - 服务端 `max_per_run` 硬上限 100，循环内加 jitter sleep（对齐 sync_orders 的 0.5–1.5s 模式）
  - `pg_try_advisory_lock`（key 用 `hashtext('logistics_tracking')`）防 cron/手动重叠，拿不到锁立即 409
  - cron 侧 `order_ids` 分批（每批 ≤80）顺序多次调用，超时按批重算
- 测试：补 advisory lock 冲突 → 409 用例

### 1.7 cron flock

- 文件：`run_sync_cron.sh`
- 改动：`exec flock -n /tmp/tts-erp-sync.lock ...` 防 tick 重叠

### 1.8 analytics cursor 假分页止血

- 文件：`analytics_sync/app.py`
- 改动：真分页实现前 `nextCursor` 恒 null（当前 analytics_records 仅 1 行，够用）；TODO 注释指向 Wave 3 的真实现
- 顺带修：`GET /cursor` 每次两次 DB 写（`fetch_timezone` INSERT + `write_audit`）合并为一次

**Wave 1 验证**：`pytest tdd/ tests/miaoshou/` 全绿 → `restart.sh` → healthz 200 → e2e → `verify_external_api.sh`（重点：401/403/429 行为变化）→ 观察一轮 cron（10 分钟）日志无整轮崩溃。

---

## Wave 2 — Schema housekeeping（DDL 迁移）

**预估 0.5 天 + 迁移窗口。依赖 Wave 1.3 先上线。操作前 `pg_dump` 备份两个库。**

### 2.1 索引清理 + 补缺

- 删：`idx_logistics_tracking_final`（≡`_final_status`）、`idx_logistics_tracking_tracking`（≡`_tracking_number`）、`idx_lt_events_order`（冗余于 PK 前导列）、`idx_oauth_tokens_provider`（单值列）、`idx_logistics_tracking_overseas`（布尔全索引 → 改部分索引 `WHERE arrived_overseas` 或删）
- 增：
  - `orders(shop_id, create_time DESC, order_id DESC)` — /db/orders keyset 分页主路径
  - `order_shippings(shop_id, order_id) WHERE tracking_number IS NOT NULL AND tracking_number <> ''` — cron 每 10 分钟热路径
  - `orders(order_status_name)`、`statement_transactions(type)` — /db/* 的 status/type 过滤参数

### 2.2 死表 + 死列

- `DROP TABLE logistics_events`（已核实 0 行；端点 `/db/logistics_events` 实际读 `logistics_tracking_events`，考虑端点改名或加注释说明）
- `DROP COLUMN shops.shop_cipher`（Wave 1.3 已停写，已核实无读取方）
- FK 策略决议：**维持无 FK**（sync 镜像表惯例），在 schema.sql 头注释写明，删死表后全库一致

### 2.3 sync_log retention 单一入口

- `trg_sync_log_retention_fn` 改为 `PERFORM cleanup_sync_log(60)`，消除两套实现漂移

### 2.4 `regen_schema.py` 拆双文件

- `scripts/regen_schema.py` 输出拆为 `schema_oauth.sql` / `schema_tts_erp.sql`，各自只灌自己的库；清理两库中互灌产生的对方空表（tts_erp 库里的空 `oauth_tokens` 等）
- 顺带修：`ADD CONSTRAINT` 非幂等问题用 `DO $$ ... EXCEPTION WHEN duplicate_object THEN NULL; END $$;` 包裹，让重灌真正幂等

### 2.5 `order_shippings.raw` 停存整单 JSON

- 文件：`tts_erp.py`（`persist_order` shipping 分支）
- 改动：raw 只存 shipping 子 dict；存量行可选 `UPDATE order_shippings SET raw = raw->'shipping' ...`（数据量小，顺手做）

### 2.6 analytics retention 挂调度

- 按 `analytics_sync/retention.sql` 的建议函数，crontab 增加：`analytics_records` 90 天、`analytics_audit_log` 30 天每日清理（当前仅 1 行，属防无界增长的未雨绸缪）

**Wave 2 验证**：regen 后 diff 干净 → 备份 → 双库 apply → `pytest` + e2e + `verify_external_api.sh` → `EXPLAIN` 确认 /db/orders 走新复合索引、cron 物流 target 查询走部分索引。

---

## Wave 3 — 结构性（连接池 / HTTP 收敛 / 输入校验 / legacy 解耦）

**预估 2–3 天。行为变化最大，放 schema 稳定后。**

### 3.1 连接池

- `pip install 'psycopg[pool]'`（官方维护，符合 AGENTS.md"优先复用成熟组件"）
- `tts_erp.db_connect()` 改为从 `psycopg_pool.ConnectionPool` 取连接（保持函数签名不变，所有调用方——`auth.py` / `tts_erp_fastapi._db_query_dict` / `pg_repositories`——无感切换）
- `tdd/pg_repositories.py` 兑现文件头承诺的 seam：upsert 接受外部连接，一个 sync 批次一个事务（statement_transactions 数百条明细的收益最大）
- 测试：`tdd/conftest.py` 事务回滚隔离需兼容池化连接（重点回归点）

### 3.2 HTTP/重试收敛

- 引入 `httpx`（+ 简单 tenacity 或手写有限重试），收敛 5 处重复实现（`tts_signing.tiktok_request` / `sync_cron.http_json` / `oauth_receiver_core._open_http_get` / `PlainHttpClient` / miaoshou 两处）
- 优先改主链路：`tiktok_request` 加 429/5xx 有限重试（指数退避 + jitter，上限 3 次）——当前收到 429 直接 502 等下 tick
- `PlainHttpClient` 生产零引用 → 删除（测试改用 FakeHttpClient）
- 分批做：主链路先行，oauth_receiver_core / miaoshou 跟随，每步全量测试

### 3.3 `/sync/*` 输入校验

- 文件：`tdd/tts_erp_fastapi.py`、`tdd/tts_business.py`
- 改动：`/sync/*` body 定义 pydantic model（FastAPI 原生 422）；business 层裸 `int()` 消除，脏输入从 500 变 422
- 顺带决议 `/sync/order/{order_id}` 501：实现它（复用 `tts_business` 已有单订单同步逻辑）并在 `/endpoints` + AGENTS.md 对齐

### 3.4 `_invoke_legacy_sync` 解耦

- 文件：`tdd/tts_erp_fastapi.py`（1479–1500）、`tts_erp.py`
- 改动：把 `Handler._sync_miaoshou_*` / `_db_list_miaoshou_*` 抽成模块级函数（miaoshou 包内或新 `tdd/miaoshou_sync.py`），FastAPI 直接 import，删掉 `_StubHandler` 和 `__dict__[method_name]` 硬耦合
- 为 Wave 4 删 legacy 解锁

### 3.5 analytics cursor 真分页（可选，看 1.8 后实际诉求）

- `fetch_cursor_page` 加 LIMIT/OFFSET 或 keyset；`get_cursor` 真正消费 cursor 参数
- 若届时数据量仍小可继续恒 null，本项降级为 backlog

**Wave 3 验证**：全量 pytest（重点 conftest 池化兼容）→ restart → e2e ×2 轮 → verify_external_api.sh → 观察 cron 窗口时长变化（连接池收益应可见）→ `TTS_DEBUG_SIGN=1` 抽查签名路径未变。

---

## Wave 4 — legacy 退役 + 杂项 + 文档

**预估 1–1.5 天。**

### 4.1 legacy `tts_erp.py` 退役

- 抽取仍被依赖的部分到共享模块：`db_connect`（Wave 3.1 已池化，移到如 `tdd/db.py`）、`persist_*` ×10（已在 `pg_repositories` 有包装，内联进去）、`_safe_int`、`log_sync`
- 删除 ~1700 行死代码（`do_GET/do_POST/_proxy_*`/`Handler._sync_orders` 等与 `tts_business.py` 完全重复的部分）+ `fetch_token`/`fetch_shop_meta`（打已不存在的 9876 直连）
- 仓库根 3 个 `.bak` 文件移出版本控制
- 更新 AGENTS.md §6 文件清单

### 4.2 miaoshou 小修

- `MiaoshouClient._call` 删死的 `except HTTPError`，HTTP 层错误透传真实状态码（不再误报"无法解析响应"）
- `PROD_BASE == TEST_BASE` 启动期显式日志（`MIAOSHOU_ENV` 配错可见）
- 测试：`tests/miaoshou/` 补状态码透传用例

### 4.3 oauth / analytics 语义小修

- `oauth_receiver_core.handle_callback`：`not_registered`/`mismatched` state 仍放行落库——与 owner 确认是否"宁可多存也不错失授权"的有意取舍；是则加注释，否则拦截（**此项需用户决策**）
- `analytics_sync` scope_grants：同维度多值改 OR 语义；非 `seller:`/`advertiser:` 前缀的 scope 拒绝而非静默忽略
- `upsert_records` 时区硬编码 `"Asia/Shanghai"` 改用 `DEFAULT_TIMEZONE` 配置

### 4.4 死代码与误导注释清理（一个 PR）

- `tts_erp_fastapi.py:570-575` 描述已修复 bug 的 TODO、文件头过时 docstring、`:67` CORS 注释
- `rate_limit.py` 的 `reset_for_key`/`_MODULE_BUCKETS` 死代码、空 deque 桶不回收
- `sync_cron.py` 头部过时 docstring、`summary` 的 `shop_err` 死字段

### 4.5 文档对齐

- AGENTS.md：写清 token 续期归 oauth-receiver 仓库管（crontab 实证）；`/sync/order/{id}` 状态；`/db/logistics_events` 端点改名说明；schema 拆分后的 apply 流程
- `CHANGELOG.md` + `handoff.md` 按日期记录四个 Wave

**Wave 4 验证**：全量 pytest + e2e + verify_external_api.sh + miaoshou 96 测试 → restart → healthz → 观察 24h cron。

---

## Files to modify（按 Wave 汇总）

| Wave | 文件 |
| --- | --- |
| 1 | `tdd/auth.py`、`tdd/rate_limit.py`、`tdd/tts_erp_fastapi.py`、`analytics_sync/app.py`、`tts_erp.py`(persist_shop)、`sync_cron.py`、`tdd/tts_business.py`、`tdd/oauth_receiver_core.py`、`run_sync_cron.sh` + 对应 `tdd/test_*.py` |
| 2 | `schema.sql`（regen 产物）、`scripts/regen_schema.py`、`tts_erp.py`(persist_order)、crontab、`analytics_sync/retention.sql` |
| 3 | `tts_erp.py`(db_connect)、`tdd/pg_repositories.py`、`tts_signing.py`、`tdd/tts_erp_fastapi.py`、`tdd/tts_business.py`、`tdd/http_client.py`、新 `tdd/miaoshou_sync.py`、requirements/.venv |
| 4 | `tts_erp.py`（退役）、`tdd/db.py`（新）、`miaoshou/miaoshou_client.py`、`tdd/oauth_receiver_core.py`、`analytics_sync/app.py`、`AGENTS.md`、`CHANGELOG.md` |

## Reuse（已确认的现成资产）

- `anyio.to_thread.run_sync` — FastAPI 自带 anyio，无需新依赖
- `pg_try_advisory_lock` — PG 内置，防重叠无需引入分布式锁组件
- `psycopg[pool]` / `httpx` — AGENTS.md §4 明确"优先复用成熟开源组件"
- `tdd/conftest.py` 事务回滚隔离 + `TEST_%` 哨兵 — 所有新测试沿用
- `_safe_int`（`tts_erp.py`）— Wave 3.3 前可过渡使用
- `cleanup_sync_log()` — Wave 2.3 直接复用为 trigger 唯一入口
- `_backfill.py:79` 的 NULL 写法 — Wave 1.3 对齐目标

## Verification（每个 Wave 的统一门槛）

1. `.venv/bin/pytest tdd/ tests/miaoshou/ -q` 全绿（新测试先行）
2. `bash restart.sh` → `curl localhost:9877/healthz` 200
3. `python3 tests/test_e2e.py`（+ finance 版）
4. `verify_external_api.sh`（401/403/429/CORS/分页/限流）
5. Wave 特有验证见各节；Wave 2 额外要求 `pg_dump` 备份 + regen diff 干净
6. 每个 Wave 部署后观察至少一轮 cron（10min）日志

## 风险与备注

- **最大风险点是 Wave 3.1 连接池**：`conftest.py` 的事务回滚隔离假设每测试一个连接，池化后需重点回归；建议 3.1 单独一个 PR 先合。
- Wave 2 的 DDL 全部小表操作，无锁窗口压力，但 `DROP COLUMN/TABLE` 不可逆，备份先行。
- Wave 4.3 的 oauth state 取舍是唯一需要用户拍板的语义问题，可提前回答以解除阻塞。
- 节奏可按"每天 1 个 Wave 内的子项 + 部署验证"推进；Wave 1 子项之间无依赖，也可并行多个 session 做。
