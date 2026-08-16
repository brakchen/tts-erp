# tts-erp CHANGELOG

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
