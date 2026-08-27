# tts-erp CHANGELOG

## 2026-08-27 (fix) — Review remediation Waves 1-4（可用性 / 数据正确性 / schema / 测试基建）

按 `plans/review-remediation-2026-08.md` 执行的三路 review 修复：

### Wave 1 — 止血（commit `ec9bf77`）
- **auth**: `lookup_role` 移出 event loop（`anyio.to_thread`）；无效 key 负缓存（20s）；
  PG down fail-closed 503；被拒请求计入限流桶（暴力枚举不再免限流）。
- **analytics_sync**: `post_batches` 不再在 event loop 上跑同步 psycopg；`nextCursor`
  在真分页落地前恒 null（之前是假分页，客户端会死循环）；`fetch_timezone` 改 read-first。
- **persist_shop**: 不再把明文 `shop_cipher` 落库（凭证单源在 oauth-receiver）。
- **_get_creds**: `TokenError.status` 透传（404=店铺未授权 vs 502=上游故障）；
  意外错误返回固定文案 502，内部细节只进日志。
- **sync_cron**: `http_json` 补捕 `TimeoutError/OSError`（一次慢调用不再炸整轮）；
  oauth DB down 退出码 4（之前静默 exit 0）；物流 plan 分批 ≤80 单/次；`flock` 防重叠。
- **tts_business**: payments/statements 中途分页失败置 `error`（窗口不再越过缺口前进）；
  裸 `int()` 全部改 `_body_int`/`_body_int_opt`（脏输入不再 500）。
- **sync_logistics_tracking**: PG advisory lock（重叠返 409）+ `max_per_run` 硬上限 100 + jitter。

### Wave 2 — Schema housekeeping（commit `aa53b9d`）
- 删重复/冗余索引 ×4；`idx_logistics_tracking_overseas` 改部分索引。
- 新增 `idx_orders_shop_ct`（/db/orders 分页走 Index-Only-Scan）、
  `idx_order_shippings_tracking`（cron 每 10 分钟热路径不再全表扫）、
  `idx_orders_status`、`idx_stmt_txns_type`。
- `DROP TABLE logistics_events`（死表，零写入方）；`DROP COLUMN shops.shop_cipher`。
- 清理两库互灌：tts_erp 库的空 `oauth_tokens` + oauth_receiver 库的 23 张空业务表。
- `sync_log` retention 单一入口（trigger 委托 `cleanup_sync_log(60)`）。
- crontab 挂上 analytics_records(90d) / analytics_audit_log(30d) 每日清理。
- `regen_schema.py` 拆成 `schema_oauth.sql` + `schema_tts_erp.sql`（不再一文件灌两库）。
- `order_shippings.raw` 只存物流子集（不再复制整单 JSON）。

### Wave 3 — 结构性（commits `0566571`, `8af34f9`, `ed81fc5`）
- **连接池**：`db_connect()` 走 `psycopg_pool.ConnectionPool`（min 1 / max 10），
  同步批次不再每查询一次 connect/close。
- **tiktok_request**：429/5xx/网络错误有界重试（指数退避+jitter，上限 2 次）；
  4xx 立即返回不浪费配额。
- **/sync/order/{id}**：从 501 变为实现（body.shop_id → 本地 orders 表 → 404）。
- **_invoke_legacy_sync**：`getattr` + 501 兑底（legacy 方法被删时不再 KeyError→500）。
- 删 `PlainHttpClient`（生产零引用）。

### Wave 4 — 杂项 + 测试基建
- **miaoshou**：`_call` 的 HTTP 错误透传真实状态码（`http.client` 不抛异常，
  之前 CDN 502 被误报为 code=0 "无法解析响应"）。
- **analytics_sync scope_grants**：同维度多值改 OR 语义；未知 scope 前缀 fail-closed。
- **DEFAULT_TIMEZONE** 移到 `analytics_sync/domain.py` 单一来源。
- **oauth state**：`not_registered`/`mismatched` 仍放行的行为加了 WARNING 日志 +
  注释说明（若需严格 CSRF 拦截，改 `handle_callback` 一处即可）。
- **测试基建**：修 `test_oa_uath_receiver_url_removed.py` 弹 sys.modules 不恢复
  导致的 pytest session fixture 级联崩溃（tdd/ 从 129 passed + 372 errors 修复为
  491 passed + 0 errors）。

## 2026-08-27 (feat) — analytics_sync protocolVersion 2：页级完整性 cursor

### 问题

v1 协议下，客户端某天只传了第 1 页（共 3 页）时，服务端也会把该日标记为 complete
并推进 cursor —— 多页数据尚未收齐就错误推进每日 cursor，后续页永远丢失。

### 修复（v2 协议）

- **每日数据单元** = `sellerId + advertiserId + storageKey + campaignId + day`。
  仅当 `analytics_daily_pages` 中 `1..expectedPageCount` 全部页码齐后才标记 complete。
- 新表 `analytics_daily_pages`（页级证据）+ `analytics_daily_completeness`
  （每日聚合状态，避免 cursor 查询扫原始 JSON）；`analytics_records` 加
  `expected_page_count` 列；`analytics_cursors` 加 `first_seen_day` 锚点列。
- `latestCompletedDay` 只能是连续完整日期前缀的最后一天，不允许跳过较早缺失日期；
  `nextRequiredDay` 为服务端权威值（latest+1 / first_seen / bootstrap 三段式）。
- 页数冲突（同一单元同日 expectedPageCount 不一致，批内或跨批）→ 逐条 rejected
  `PAGE_COUNT_CONFLICT`，`retryable=false`，cursor 不动。
- 记录写入 + 完整性标记 + cursor 推进在**同一事务**内完成。
- **v1 兼容**：v1 请求继续接受，每条记录视为 implicit `expectedPageCount=1`（单页日），
  行为与旧版一致；v1 记录无法把 v2 声明的多页日错误标记为 complete（会撞
  PAGE_COUNT_CONFLICT）。幂等键算法不变（expectedPageCount 不入键）。

### Files changed

- `analytics_sync/migration_v2.sql` (new) — 幂等迁移 + 存量数据回填
- `analytics_sync/schema.sql` — 新表 + 新列 + 六列组合索引
- `analytics_sync/domain.py` — Record 加 `expected_page_count` / `protocol_version`
- `analytics_sync/pg_repositories.py` — upsert 重写：页跟踪 + 完整性重算 + 连续前缀 cursor
- `analytics_sync/app.py` — 接受 protocolVersion 1/2，v2 校验，`result.rejected` 合并
  （修复仓库层 rejected 被静默丢弃的 bug）
- `analytics_sync/tests/test_protocol_v2.py` (new) — 19 个 v2 验收测试（含并发）
- `analytics_sync/tests/test_batches.py` / `test_concurrency.py` — 适配新签名与连续前缀语义
- `analytics_sync/tech-doc/openapi.yaml` / `analytics-sync.md` / `plugin-integration.md`
  / `compatibility.md` — v2 契约与 v1↔v2 兼容策略文档
- `tdd/test_analytics_sync_integration.py` — unsupported-version 测试改用 99

## 2026-08-25 (fix) — /healthz 撒谎 + tts_erp.shops 空 + schema.sql 严重过时

### 三件据 drift 被发现（读 /db/orders 时磕到）

1. **`/healthz` 报 `oauth_receiver.token_count: 0`，但 `oauth_tokens` 表实际有 2 行。**
   - 根因：`oauth_receiver_router._oauth_receiver_section()` 用了 `len(oc._token_history)`（进程内 deque，记最近 N 次 token-exchange），不是 DB 行数。重启后 deque 为空 → 监控永远错报。
   - 修复：加 `oauth_receiver_core.db_count_shops()` 走 `SELECT COUNT(*)`，healthz 改用这个。

2. **`tts_erp.shops` 表 0 行，但 `oauth_receiver.oauth_tokens` 有 2 行（Bridge nook VN + MOCK_SHOP_12345 US）。**
   - 根因：legacy `tts_erp.py._proxy_order` 每个订单详情 GET 后会调 `persist_shop`。FastAPI 接管订单路由后**完全没继承这一步**——shops 表从未被回写。
   - 修复：
     - `tdd/_backfill.py` 加 `backfill_shops_from_oauth()`（幂等）
     - FastAPI startup lifespan 自动跑一次
     - `POST /admin/shops/backfill`（admin 角色）手动触发
     - `_tiktok_proxy` 在 `persist_order_on_get=True` 路径上**也调** `persist_shop`（双保险）

3. **`schema.sql` 严重过时**：实际 DB 有 **24 张表**（schema.sql 只列了 16），缺 `analytics_*/logistics_*/statement_transactions` 等 7 张；`orders` 表列名是 `order_status_name`（AGENTS.md 写的 `order_status` 是错的）。
   - 根因：手写 schema.sql，没人维护。
   - 修复：`scripts/regen_schema.py` 从真实 PG 拉 schema，自动转 IF NOT EXISTS 幂等化、剔 \restrict 串、合并双 DB dump。
   - 验证：fresh DB clean apply 0 errors；二次 apply 报错都是 "already exists"（PG 限制 ADD CONSTRAINT 不支持 IF NOT EXISTS，header 已说明）。

### Files changed

- `tdd/oauth_receiver_core.py` — 加 `db_count_shops()`
- `tdd/oauth_receiver_router.py` — healthz 用 `db_count_shops()`
- `tdd/_backfill.py` (new) — `backfill_shops_from_oauth()` + `run_startup_backfill_if_configured()`
- `tdd/tts_erp_fastapi.py` — lifespan hook + `POST /admin/shops/backfill` + `_tiktok_proxy` 补 `persist_shop`
- `scripts/regen_schema.py` (new) — schema.sql 重新生成脚本
- `schema.sql` — 重新生成（1143 行，含两个 DB 的全部 24 张表）
- `tdd/test_healthz_token_count_fix.py` (new) — 4 个 healthz 测试
- `tdd/test_shops_backfill.py` (new) — 3 个 backfill 测试
- `tdd/test_tts_erp_routes.py` — 路由计数 54→55

## 2026-08-24 (fix) — sync_cron 每 10 分钟死循环 2h9min (Wave 3 Slice 2 遗留 bug)

### Symptom

`cron.service` 状态、`*/10 * * * *` crontab、`run_sync_cron.sh` wrapper 全部正常,服务也在跑,**但 `sync_log` 2 小时 9 分钟没新条目**。cron 每轮都死于第一步:

```
discover_shops failed: /shops failed: {'_http_status': 404, '_error': True, 'detail': 'Not Found'}
```

`/healthz` / `/endpoints` / `/db/*` 都 200,只有 `sync_log` 停摆——**外部不可见**。

### Root cause

Wave 3 Slice 2 (2026-08-18 批次) 删除了 `GET /shops`、`/shops/<id>`、`/token/<id>` 三个 oauth-receiver 代理路由,改为 in-process 调 `oauth_receiver_core.db_list_shops()`。**`sync_cron.py:discover_shops()` 没跟着改**——还在调 HTTP `/shops`。路由不存在 → 404 → cron 异常退出。

### Fixed

- **`sync_cron.py:discover_shops(base_url, api_key)` → `discover_shops(provider="tiktok")`**：直接 in-process 调 `oauth_receiver_core.db_list_shops(provider="tiktok")`,与 FastAPI app 走相同代码路径。`is_db_ok()` 为 False 时返 `[]`(不抛异常)。
- **`sync_cron.py` top imports**: 加 `tdd` 到 `sys.path`(让 `oauth_receiver_core` 可导入)+ 模块加载前注入 `OAUTH_DB_URL/OAUTH_DB_ENCRYPTION_KEY/OAUTH_DB_TABLE` 到 `os.environ`(否则 oauth_receiver_core 的 module-load `db_init()` 拿到空 env → `_db_ok=False` → `db_list_shops` 返 [])
- **`sync_cron.py` `main()`**: 调 `discover_shops()`(不再传 `base_url, api_key`)
- **`tdd/tts_erp_fastapi.py` `/endpoints` 文档**: 把误导的 `passthrough: [GET /shops, /shops/<id>, /token/<id>]` 改为 `passthrough_removed`, 指向 `oauth_receiver_core` 的 in-process 函数(这三条路由自 Wave 3 Slice 2 起就不存在,文档残留至今才暴露)

### Tests

- `pytest tdd/test_sync_cron_discover.py` → **6 passed** (新增测试,锁定 `discover_shops` 不发 HTTP、走 oauth_receiver_core,过滤 provider,DB 不可达返 [])
- 手动 `python3 sync_cron.py`: discovered **2 shops** (`MOCK_SHOP_12345` + `7494763368967603447`), 7 个 sync plan (orders/payments/statements/returns/cancellations/logistics/stmt_txns) 全部执行完毕, `sync_log` 新增 20+ 条 `status=ok` 行
- `bash restart.sh` + `curl /healthz` → `db_ok: true, last_sync_at: 1787570517` ✓
- `curl /endpoints | jq '.passthrough_removed'` → 3 行迁移说明 ✓

### Notes

- **Cron 调度本身无需改**:`*/10 * * * *` + `run_sync_cron.sh` 调 `python3 sync_cron.py`,下个 tick 自动 pick up 新代码,**无需 restart 任何服务**
- 下一次 cron 触发后, `logs/tts-erp-cron.log` 应该出现 `discovered N shop(s)` 而不是 `discover_shops failed: /shops failed: 404`
- `MOCK_SHOP_12345` 同步仍然 502 `Invalid shop_cipher`(预期内, mock shop 在 oauth_tokens 里没真正 token)
- 同样路径(`oauth_receiver_core` in-process)的 FastAPI app 一直 OK, 因为它在 cwd=`tdd/` 下启动且 `EnvironmentFile` 自动注入所有 `OAUTH_*` env; cron 需手动 `setdefault`,详见 commit diff

## 2026-08-24 (chore) — 妙手/万师傅 命名统一 + pyrightconfig.json 修复

### Changed

- **统一称呼为「妙手」**（消除项目里的「万师傅」残留，与 `miaoshou/` 目录名对齐）：
  - `miaoshou/__init__.py` / `miaoshou/miaoshou_client.py` / `miaoshou/miaoshou_signing.py` / `miaoshou/endpoints/__init__.py`：docstring + 注释中的「万师傅」→「妙手」
  - `tdd/tts_erp_fastapi.py`：§miaoshou 区段注释
  - `AGENTS.md`：§10 章节标题（"万师傅 / 妙手开放平台" → "妙手开放平台"）+ §6 文件清单表行
- **`pyrightconfig.json`**：`include` 列表里的 `"wanshifu"` → `"miaoshou"`（**功能性 bug 修复**：之前指向不存在的目录，pyright 完全没扫 miaoshou 包，顺 import 链也没扫 `tdd/tts_erp_fastapi.py`）

### Notes

- `wanshifu` 仅保留在合法上游 URL（`openapi.wanshifu.com` / `user.wanshifu.com` / `test-user.wanshifu.com`）
- AGENTS.md §10 line 355 的自指注释（"不再 `wanshifu`"）保留 —— 它说的是历史重命名本身

## 2026-08-24 (fix) — tts_erp_fastapi.py 3 处 latent bug

修 pyrightconfig.json 后 pi-lens 终于扫到了 `tdd/tts_erp_fastapi.py`，暴露了 3 个先前被遮蔽的问题。

### Fixed

- **`TTS_ERP_PORT = int(os.environ.get(...))` (L118)** — 加 try/except ValueError fallback 到 `9877`，env 配错不再让启动崩溃
- **`_encode_cursor` (L447)** — `int(create_time)` + `json.dumps()` 包 try/except `(TypeError, ValueError)` → 返 None，cursor 编码失败不再 500
- **`_invoke_legacy_sync` (L1419)** — 签名 `-> dict` 改为 `-> dict | JSONResponse`，error 路径返 `JSONResponse(500, ...)` 而非 `{"_error": ...}`

### Behavior change（明确告知）

- **5 个 miaoshou sync/db 端点**的 error 响应从 `200 + {"_error": "..."}` 改为 `500 + {"_error": "..."}`：
  `/sync/miaoshou_shops`、`/sync/miaoshou_price_templates`、`/sync/miaoshou_collect_box_details`、`/sync/miaoshou_move_collect_tasks`、`/db/miaoshou_shops`
- 之前 200+_error 是 bug（应返 4xx/5xx），现在 500 是诚实表达。已通过 restart.sh + 烟雾测验证。

### Tests

- `pytest tests/miaoshou/` → **179 passed**
- `pytest tdd/test_tts_erp_routes.py tdd/test_miaoshou_routes.py tdd/test_auth.py` → **59 passed, 1 skipped**（已知限制，AGENTS.md 已记）
- `restart.sh` + `GET /healthz` → OK
- 烟雾测：`GET /db/miaoshou_shops`、`POST /sync/miaoshou_shops`、`GET /db/orders?cursor=...` → 200

## 2026-08-23 (doc fix) — analytics_sync 幂等键参考向量勘误

### Fixed

- **plugin-integration.md 和 setup/analytics-sync.md 的 idempotencyKey 参考向量从 `ce1ba2e1...` 改为 `73b716cc...`**（原是手抄错误，算法从未产出过 `ce1ba2...`）
- **domain.py**: `canonical_json_for_key` 和 `compute_idempotency_key` 接受 `page: int | str`（`1` 和 `"1"` 同等），更贴近插件端实际行为
- **domain.py**: `import hashlib` / `import json` 移到文件顶部
- **tests/test_idempotency.py**: 新增 6 条锁住正确算法的回归测试（reference vector、page int/str 等价、单字段变化、trim 幂等）

### Added

- `test_reference_canonical_json_is_locked` —— 锁住 canonical JSON 字节串
- `test_reference_hash_is_locked` —— 锁住标准输入下 sha256 = `73b716cc...`，并反向断言不是 `ce1ba2...`（防意外回滚）
- `test_page_int_and_string_produce_same_hash` —— `page=1` ≡ `page="1"`
- `test_page_changes_produce_different_hashes` —— 分页键必须随 page 变
- `test_each_field_change_produces_different_hash` —— 5 个字段任一变化都改 hash（防止静默忽略）
- `test_string_fields_are_trimmed_before_hashing` —— 首尾空格不影响 hash

### Migration

- **不需要迁移**：算法从未变过，只是文档里手抄的参考值错了。没有生产数据使用错 hash。协议版本无需升级。

## 2026-08-23 (refactor) — analytics_sync 鉴权统一到 api_keys

### Changed

- **删除 `analytics_sync_tokens` 表**（2026-08-23 早上刚加的，现在不用了）；Chrome 扩展的 sync token 直接走 tts-erp 的 `api_keys` 表
- **`api_keys` 表加 `scopes TEXT[]` 列**（`NOT NULL DEFAULT '{}'`），保留空的 scope 表示无限制；Chrome 扩展用 `--scopes "seller:<id>"` 拿受限 token
- **`api_keys.py` CLI 加 `--scopes` flag**；`rotate` 默认拷贝老 key 的 scopes
- **`tdd/auth.py::lookup_role` 返回值改为 `(role_level, scopes_tuple)`**，在 `scope["api_key_scopes"]` 暴露给下游；Cache key/value shape 同步调整
- **`tdd/auth.py::required_role`**：analytics_sync 路径要求 `readwrite`（默认 fallback 是 admin，太严）
- **`analytics_sync/auth.py`** 重写：从 `tdd.auth.lookup_role` 读 api_keys；保留 scope_grants / X-Sync-Token 兼容
- **`analytics_sync/app.py`** 的所有 handler 改用 `request.scope["api_key_scopes"]` 做 per-seller 校验
- **路由挂载策略**：保留 analytics_sync 独立 FastAPI 进程（端口 9878），不再尝试 mount 到 tts_erp_fastapi（后者当前未跑通，跨进程共享 auth 中间件成本高，独立进程 + 共享 api_keys 表 已足够达到"统一鉴权"）

### Removed

- `analytics_sync/analytics_sync_tokens.py` CLI（用 `api_keys.py create` 替代，文档里说明 `--scopes` 用法）
- `analytics_sync_tokens` 表的所有引用

### Tests

- 63/63 仍通过：所有 fixture 改为向 `api_keys` 插 readwrite 临时 token

## 2026-08-23 — analytics_sync 后端服务（Chrome 插件替代 CloudBase）

### Added

- **`analytics_sync/` 包**：独立 FastAPI 后端，端口 9878，替代已退役的 CloudBase 分析上传路径。插件（`tk-adv-cost-monitor`）每日拉 cursor 决定补传区间，批量上传分析明细
- **2 个端点**：
  - `GET /v1/analytics/sync/cursor?sellerId=&advertiserId=&storageKey=&campaignId=&pageSize=` — 返回每个 `(storageKey, campaignId)` 的 `latestCompletedDay` / 权威 `nextRequiredDay`（空时返回 bootstrap 日期 = today - 30 天）
  - `POST /v1/analytics/sync/batches` — 幂等批量上传，最多 100 条 / 2 MB；accepted/rejected 部分成功
- **Schema（5 张表，幂等 CREATE IF NOT EXISTS）**：
  - `analytics_records` — raw 响应 JSON + 规范化字段，`UNIQUE(idempotency_key)`
  - `analytics_cursors` — `(seller, advertiser, storageKey, campaignId)` → `latest_completed_day`，原子 UPSERT + `GREATEST()` 防回退
  - `analytics_shop_timezones` — 每店铺 IANA 时区，默认 `Asia/Shanghai`
  - `analytics_sync_tokens` — Bearer token 只存 SHA-256 + 16 字符前缀
  - `analytics_audit_log` — requestId 审计轨迹，无密钥
- **鉴权 / 限流 / Scope 校验**：
  - `auth.py`：Bearer / X-Sync-Token，中间件 + 60s 缓存；scope 语法 `seller:<id>` / `advertiser:<id>` / `*`，空 scopes 默认无限制
  - `rate_limit.py`：每 token prefix 滑窗 60s 限流（默认 100/min），429 带 `Retry-After`
- **CLI `analytics_sync_tokens.py`**：create / list / revoke / rotate（仿 `api_keys.py`）
- **错误契约**：400/401/403/413/429/5xx 全部带 `{code, message, requestId, retryable}`，永不回显 token / cookie / header
- **OpenAPI 3.1 正式规范**：`analytics_sync/tech-doc/openapi.yaml`
- **设计文档**：`analytics_sync/tech-doc/architecture.md`（架构 + 14 个协议歧义解决）/ `plugin-integration.md`（插件对接说明）/ `compatibility.md`（版本演进 + 保留策略）/ `analytics-sync.md`（API 契约 + curl 示例）
- **保留策略**：`analytics_sync/retention.sql`（90 天 records / 30 天 audit log）
- **测试 63 个全过**：canonical key 推导（10）+ auth（7）+ batches（12）+ cursor（6）+ 并发去重（2）+ 跨店隔离（3）+ 错误路径（6：413 / 5xx / audit / 信息泄露防护）+ 限流（5）+ scope 校验（12）

### Notes

- 端口 9878（区别于 tts-erp 的 9877 和 oauth-receiver 的 9876）
- PG 数据库沿用 `tts_erp`，表名前缀 `analytics_*` 隔离
- 启动命令：`uvicorn analytics_sync.app:app --host 0.0.0.0 --port 9878`
- 协议文档（任务输入）位于 conversation 记录；正式 OpenAPI 在 `tech-doc/openapi.yaml`

## 2026-08-18 — 财务明细切换接口数据源，Excel 数据全量删除

### Added

- **`statement_transactions` 表 + `POST /sync/statement_transactions` + `GET /db/statement_transactions`**：账单内逐交易明细，数据源 `GET /finance/202309/statements/{id}/statement_transactions`（2026-08-18 `probe_finance_txns.py` 实测，58 字段/条含 order_id，`sort_field` 只接受 `order_create_time`）。52 个金额字段全建 NUMERIC 显式列 + raw jsonb
- cron 第 7 个同步计划 `stmt_txns`（statement_time_ge 时间窗口，与 statements 同机制）
- TDD：`tdd/test_sync_statement_transactions.py` 9 用例（persist 4 + business 5，fake http/repo）
- 顺带探明的 Finance API 面：`withdrawals?types=WITHDRAW` 可用（未接）；`transactions/unsettled` 全版本 404；`202501` 只开放两个 statement_transactions 端点（SKU 级 fee_tax_breakdown 比 202309 更细，未接）

### 对账验证（删除前的放行条件）

- 全量回填 189 条（25 个账单 / 33 页），逐单抽查 10/10：`customer_payment`/`fee_amount` 与 Excel **分毫不差**
- 佣金/实际运费/商家运费逐项一致；增值税/交易手续费/订单处理手续费 API 对 VN 店铺**不拆分**（含在 fee_amount 里，对应字段为 0）
- Excel 自身费用纵表对不上账（单行 total_fee -194,753 ≠ fee 行合计 -220,053），所谓独有细分质量存疑 → 实际信息损失≈0

### Removed（用户确认）

- **Excel 数据全删**：6 张表（sku/erp_orders/erp_order_items/return_items/financial_lines/fee_lines）+ 5 张表的 75 个 xls_ 列 + `v_order_recon` 视图 + `/db/finance/*` 4 端点 + `merge_tiktok.sh/sql` + `tdd/test_finance_merge.py`
- **`schan_db.tiktok` schema 一并 DROP CASCADE**（同日早些时候还用它做过对账源）
- 唯一不可再生数据：financial_lines 的 estimated_settle/unsettle_reason（接口 unsettled 端点不存在）；源头 Excel 在 Windows 工作站，要恢复需重跑 ETL
- schema.sql 同步移除全部 Excel DDL（重放 0 错误）

### Notes

- pytest：152 passed（149 + 9 新增 − 6 删除）
- final_smoke：8 条 sync 路由，`/db/statement_transactions` 冒烟替换 finance 冒烟

## 2026-08-17（晚二）— 物流追踪 bug 修复 + cron 接入物流同步

### Fixed

- **`persist_logistics_tracking` first/last 事件写反**（tts_erp.py）：TikTok 返回的 tracking 列表是**最新事件在前**，旧实现取 `events[0]`/`events[-1]` 当 first/last，导致 `logistics_tracking` 汇总表 `first_event_at`/`last_event_at`/`last_action_code`/`last_description` 全部写反（last_description 恒为 "Order placed."）。改为按 `update_time_millis` 排序后取首尾；既有 18 行已用 `logistics_tracking_events` 明细回填修正
- TDD：`tdd/test_logistics_tracking.py` 3 个用例（最新在前 / 最旧在前 / 缺时间戳容错）

### Added

- **cron 接入物流同步**（sync_cron.py 第 6 个 plan）：之前 `/sync/logistics_tracking` 端点存在但**从不在 cron 里**，只手动跑过 2 次（8-16 晚），导致 8-15 起的新订单（有运单号）一直没有物流数据
- 目标选择 `logistics_target_ids()`：有运单号、且（未同步过物流 OR final_status 非终态）的订单；终态 = `DELIVERED`/`RETURNED_TO_SELLER`，单轮上限 300

### Notes

- pytest：149 passed（新增 3）

## 2026-08-17（晚）— API key 鉴权系统上线（shadow 阶段）

按 `tech-doc/api-key-auth-design.md` 实施（TDD：先 test_auth.py 15 个用例，后 auth.py）。

### Added

- **`tdd/auth.py`**：`AuthMiddleware`，Bearer key 鉴权，三级角色 readonly/readwrite/admin，路径规则默认拒绝（未分类路径=admin 级），60s 进程内缓存，`last_used_at` 1h 节流更新
- **`api_keys` 表**（schema.sql）：只存 SHA-256 哈希 + prefix；**`api_keys.py` CLI**：create/list/revoke/rotate，完整 key 只在创建时打印一次
- **三档模式** `.env TTS_ERP_AUTH_MODE=off|shadow|enforce`；当前部署为 **shadow**（放行 + 记 would-deny 日志）
- 调用方全部带 key：`sync_cron.py`（读 `.env TTS_ERP_SERVICE_KEY`）、`final_smoke.py`、`regression_check.py`、`test_e2e.py`；enforce 模式下 smoke 会断言无 key 请求 401
- 已建 key：`cron-sync`（readwrite，在 .env）、`schan-admin`（admin，人工持有）

### 切换 enforce 的步骤（观察期后执行）

1. 观察 `grep would-deny logs/stderr.log` 应只剩历史记录、无新增合法调用被拒
2. `.env` 改 `TTS_ERP_AUTH_MODE=enforce` → `bash restart.sh`
3. `python3 final_smoke.py`（末尾会断言无 key 401）+ 等一轮 cron 实测
4. 回滚：`.env` 改回 `shadow` 或 `off` → restart（30 秒级）

## 2026-08-17 — Excel 财务数据融合（schan_db.tiktok → tts_erp.public）

### Added

- **schan_db.tiktok 全量融合进 public schema**（`merge_tiktok.sh` + `merge_tiktok.sql`，幂等可重跑）：
  - 冲突表只补 `xls_` 前缀列（不覆盖 API 同步列）：orders(+29 列, 270 行)、order_items(+12 列, 278 源行)、payments(+9 列, 5 行)、statements(+7 列, 25 行)、returns(+14 列, 11 行)
  - 新建 6 张 Excel 独有表：sku(152)、erp_orders(230)、erp_order_items(230)、return_items(11)、financial_lines(267)、fee_lines(1375)，ID 统一转 TEXT 遵循 tts_erp 惯例，不设指向既有表的 FK（保证可重跑）
  - `v_order_recon` 视图：API 实时订单 × Excel 财务行，订单级 实结/预计 对账
- **4 个只读端点**：`GET /db/finance/lines`（order_id/statement_id/source 过滤）、`GET /db/finance/fees`（支持 `aggregate=true` 按费用名聚合）、`GET /db/finance/sku`（q 模糊搜索）、`GET /db/finance/recon`
- `tdd/test_finance_merge.py`：6 tests（TEST_ 哨兵数据，模块自清）
- schema.sql 回写欠账：logistics_tracking / logistics_tracking_events / logistics_sync_targets 3 张 Phase 5 表

### Notes

- tiktok.orders 里 2 行 `stub:` 调整单占位行**未**并入 orders（非真实订单）
- 刷新路径：Windows 工作站重新 ETL 到 schan_db.tiktok 后跑 `bash merge_tiktok.sh`
- 回滚：DROP 6 张新表 + v_order_recon + 各表 xls_列（merge_tiktok.sh 末尾有模板）
- pytest：131 passed（新增 6）

### Removed（同日）

- **删除 `POST /returns` 和 `POST /cancellations` 两个 501 占位端点**（用户确认）：原本恒返 501 的护栏桩，现整个移除，调用得到无路由 404。AGENTS.md §4 的禁令从「不要写转发逻辑」改为「不要接（端点已删除）」
- `final_smoke.py` 的「501 protection」段替换为 `/db/finance` 冒烟
- 注意：`POST /sync/order/<id>` 的 501 桩保留（那是未移植功能，与 CREATE 护栏无关）

## 2026-08-16 — FastAPI migration + TDD coverage

### Changed

- **tts_erp 服务从 stdlib BaseHTTPRequestHandler 迁移到 FastAPI**（uvicorn）
  - 30 端点全部 FastAPI 化，URL 路径完全兼容
  - restart.sh 改启 `uvicorn tts_erp_fastapi:app` 而非 `python3 tts_erp.py`
  - 旧 `tts_erp.py` 保留在仓库，回滚 1 行命令即可
- **业务逻辑从 handler 抽出**到 `tdd/tts_business.py`（纯函数）
- **DI 抽象**：HttpClient / TokenProvider / Repository 5 个 protocol
- **真实实现**：`http_client.py`（TikTokHttpClient / PlainHttpClient）、`token_provider.py`（OAuthReceiverTokenProvider）、`pg_repositories.py`（5 个 Pg*Repository）

### Added

- **TDD 完整覆盖**：111 tests passing
  - 17 tests: tts_signing.py HMAC 契约
  - 11 tests: compute_window 增量窗口
  - 9 tests: sync_log 60 天 retention (PL/pgSQL + trigger)
  - 15 tests: sync_orders 业务函数
  - 12 tests: sync_payments 业务函数
  - 9 tests: sync_statements 业务函数
  - 13 tests: sync_returns + sync_cancellations
  - 5 tests: TikTokHttpClient 真实实现
  - 7 tests: OAuthReceiverTokenProvider 真实实现
  - 13 tests: Pg*Repository 真实实现
- **sync_cron.py**：每 10 分钟同步 5 类数据（5 个 /sync/* 端点，自动发现 shop，增量窗口）
- **60 天 retention trigger + cleanup function** + 每天 0:30 兜底 crontab

### Fixed

- **tts_erp._sync_returns / _sync_cancellations 类型校验 bug**：
  - 原来把 `create_time_ge` 放 query string（被 str(int(...)) 转成 string）
  - TikTok 严格类型校验返回 `actual type:string, expected type:int64`
  - 修：移到 POST body（json int），参考 `_sync_orders` 成熟 pattern
- **/db/returns / /db/cancellations 字段名错误**（columns are `return_status` / `cancel_status`, not `status`）

### Backups

- `tts_erp.py.bak.phase0_prefastapi` — Phase 0 时的 stdlib 版本
- `tts_erp.py.bak.20260816_210500` — returns/cancellations body fix 后版本
- `tts_erp.py` 保留在仓库（不再被 restart.sh 启动）

### Verification

- `python3 -m pytest` → 111 passed in 0.94s
- `python3 final_smoke.py` → 全过（5 sync + 5 db read + 501 保护 + endpoints schema）
- `python3 test_e2e.py` → 全过（7 步骤：shops / token / sync / db / sync_log / endpoints）
- `python3 regression_check.py` → 全过（5 类 sync 都有 entries）
- `bash run_sync_cron.sh` → 22:18 手动跑全过，*/10 cron 也会自动跑

### Architecture

Before:

```
tts_erp.py (1500+ lines BaseHTTPRequestHandler)
├── inline tiktok_request calls
├── inline _require_shop_token
├── inline db_connect in every persist_*
├── inline SQL everywhere
└── no tests
```

After:

```
tts-erp/
├── tts_erp.py                       # legacy stdlib, kept for rollback
├── tts_signing.py                   # HMAC + raw HTTP (unchanged)
├── schema.sql                       # PG schema (unchanged)
├── sync_cron.py                     # cron 同步脚本 (unchanged)
├── restart.sh                       # now starts uvicorn
├── tdd/                             # new: TDD workspace
│   ├── conftest.py                  # pytest fixtures + env loader
│   ├── domain.py                    # SyncResult / Creds / protocols
│   ├── repositories.py              # 5 Repository protocols
│   ├── tts_business.py              # 5 sync business fns (pure)
│   ├── http_client.py               # TikTokHttpClient + PlainHttpClient
│   ├── token_provider.py            # OAuthReceiverTokenProvider
│   ├── pg_repositories.py           # 5 Pg*Repository implementations
│   ├── tts_erp_fastapi.py           # FastAPI app + 30 routes
│   └── test_*.py                    # 9 test files, 111 tests
└── ...
```

### Out-of-TDD-Scope (documented in conftest.py)

- TikTok API real responses
- HMAC acceptance by TikTok server
- PG trigger concurrency
- PG connection pool / network jitter
- oauth-receiver token renewal
- PG schema DDL migration
- TikTok rate-limit backoff
- systemd / container deployment
- BaseHTTPRequestHandler routing (irrelevant now)
- HTTP frame format

### Known limitations

- `POST /sync/order/<order_id>` (single order detail sync) returns 501
- `tts_erp._last_syncs` deque is in-memory; lost on restart
  (sync history still queryable from PG `sync_log` table)
- All persist_* functions open their own DB connection (50 conns for 50-order sync)
  Future: accept connection in repo constructor for connection pooling
