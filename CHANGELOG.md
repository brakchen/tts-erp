# tts-erp CHANGELOG

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
- 回滚：DROP 6 张新表 + v_order_recon + 各表 xls_ 列（merge_tiktok.sh 末尾有模板）
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
