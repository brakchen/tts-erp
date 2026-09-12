# tts-erp 架构概览

> 本文档包含 tts-erp 项目的详细技术栈、架构和业务模型信息。
> 通用约束和边界规则见 `AGENTS.md`。

## 1. 技术栈

Python 3.14 · FastAPI + uvicorn（`:9877`）· SQLAlchemy 2 + psycopg3 · PostgreSQL 容器（`:5432`，
11 schema / 54 表 + 1 view（v1 `public.*` 业务表 2026-09-05 归档删除；analytics 4 张僵尸表 migration 0007 drop；2026-09-11 `chrome_sync`→`plugin`、`analytics` 并入 `plugin`）· APScheduler（独立 sync-worker 进程）· MinIO · Fernet 加密 · systemd user units。

## 2. 业务架构

### 2.1 v2 架构（2026-08-29 切流生产）

**核心业务**：TikTok Shop 销售 + 妙手采购 → **本地分析库 + 只读 API + 定时同步**

**下游系统**：

- TikTok Shop Open API (`open-api.tiktokglobalshop.com`)
- 妙手开放平台 (`openapi.wanshifu.com`)

**打上游 TikTok 的唯一路径**：sync-worker jobs（经 `tts_erp_v2/proxy/tts_shop`，内部处理 HMAC 签名 /
`x-tts-access-token` / shop_cipher 位置 / 翻页 / 过期 token 续期）。**不要自己拼上游请求**。

### 2.2 访问方式

**本地直连**：`curl http://127.0.0.1:9877/v2/...`（带端口）

**公网域名**：`daqiang.nat100.top`（NAT **已 strip 9877 端口**）

- nginx 只把 `/tts/*` 转发给 API
- 无前缀的 `daqiang.nat100.top/v2/...` 落在 ProfitLens 前端 404
- 给用户的 URL / TikTok 填的 redirect URL / 纯公网 curl 一律 `http://daqiang.nat100.top/tts/<path>`
- 本地直连 `127.0.0.1:9877/v2/...` 才不带 `/tts`
- 前缀取自 `TTS_ERP_EXTERNAL_PREFIX`，当前 `/tts`；middleware 剥前缀匹配豁免，两端一致

## 3. 测试 DB 隔离（2026-09-07）

- **测试库**：`tts_erp_v3_test`（专用 test db，已 schema 一致）
- **生产库**：`tts_erp`（仅 systemd API + 人工 dev 连接，永不被测试污染）
- **隔离机制**：`.env.test`（gitignored）= `.env` 的 dbname 替身；`scripts/test.sh` 启动时 source 它
- **数据导入**：`bash scripts/import_prod_to_test.sh --yes` 可按需把 prod 数据搬进 test db（multi-pass FK 处理，默认含 credentials 让 FK 走得通，prod Fernet key 不变所以仍可解密）
- **安全护栏**：tests/conftest.py 检测到 `TTS_ERP_DB_URL` 指向 prod-shape dbname（`tts_erp` / `tts_erp_prod`）会往 stderr 打 WARNING；scripts/test.sh 会在 .env.test 缺失时直接退出

## 4. 凭证管理

### 4.1 凭证单源：`integration.credentials` + `proxy/token_service.py`

```python
# ✓ 正确
from tts_erp_v2.proxy.token_service import load_credentials
cred = load_credentials(session, provider="tiktok", external_account_id=shop_id)
access_token = cred.access_token   # 已解密
shop_cipher = cred.shop_cipher

# ✗ 错误（都是实测踩过的坑）
# 直连 oauth_receiver 库的 oauth_tokens 表      # v1 遗物（库已 2026-09-05 DROP，备份 backups/oauth_receiver_v1_legacy_*.sql.gz）
# 自己拿 Fernet key 解密 integration.credentials # 绕过统一实现（掩码/续期/降级会失效）
```

**凭证格式**：Fernet 加密的 JSON envelope（key = `.env TTS_ERP_FERNET_KEY`）

**唯一实现**：`token_service.py`（`encrypt` / `decrypt` / `load_credentials` / `upsert_credentials` / `refresh_if_needed`）

**token 续期**：由 sync-worker 的 `token.refresh` job 每 6h 进程内完成，不需要也不要有 HTTP 续期端点。

## 5. API key 鉴权（enforce 模式）

**设计文档**：`tech-doc/api-key-auth-design.md`

- 除豁免路径（`/healthz`、`/endpoints`、`/openapi.json`、`/docs`、`/redoc`、`/docs/oauth2-redirect`、
  `/v2/auth/{login,logout,me}`）外，所有端点要 `Authorization: Bearer <key>` 或 `X-API-Key: <key>`；
  无 key 401、角色不够 403。完整角色矩阵见 `tech-doc/external-api.md` + `middleware/auth.py::required_role()`
- 三级角色 `readonly` < `readwrite` < `admin`；handler 内再校验：linkage overrides=admin、
  issues/{id}/resolve=readwrite、admin/reset-rate-limit=admin
- 浏览器会话：`POST /v2/auth/login` 用 API key 换 `tts_session` cookie（见 `tech-doc/browser-login-design.md`）；
  cookie 会话做 mutation 必须带 `X-Requested-With: tts-erp`（CSRF 闸）
- key 入库只存 SHA-256 哈希（`security.api_keys`）；模式开关 `.env TTS_ERP_AUTH_MODE=off|shadow|enforce`
  （生产 = enforce）；cron/脚本用 `.env TTS_ERP_SERVICE_KEY`

## 6. External API（外部契约）

**完整活契约**（auth / 限流 / CORS / 分页 / 每端点 schema + curl + Stability matrix）= `tech-doc/external-api.md`——加/改任何外部端点前先读它。

**本机实时清单**：`GET /endpoints`

**端点契约要点**：

- 分页 `limit`(1..500, 默认100)/`offset`
- 时间 ISO-8601 UTC
- money 列序列化为 JSON 字符串（用 Decimal 解析）
- `POST /v2/linkage/overrides`=admin

**过滤用内部主键**（`shop_pk` / `spu_pk`），不是 `shop_id`——传 `?shop_id=` 不报错但被 FastAPI **静默忽略**（返回全量不过滤）。先查再过滤：

```bash
curl -s -H "X-API-Key: $TTS_ERP_RO_KEY" "http://127.0.0.1:9877/v2/commerce/channel-accounts"
# → external_account_id 即 TikTok shop_id；返回里的 shop_pk 即内部主键（以实际结果为准）
curl -s -H "X-API-Key: $TTS_ERP_RO_KEY" \
  "http://127.0.0.1:9877/v2/commerce/sales-orders?shop_pk=<内部 id>&limit=5"
```

### 6.1 不稳定端点（可随时 break，外部 client 勿依赖）

`GET /v2/analytics/sync/*`（Chrome 扩展 ingest 契约，随扩展发布节奏演进）、`GET /v2/llm-context`
（experimental）、`POST /v2/linkage/overrides` 等 mutation body 字段。Miaoshou 已无任何 HTTP 面，不要等它回来。

### 6.2 改 app.py / middleware 后验证

`bash prod-switch/postswitch-smoke.sh`（7 步冒烟）+ `.venv/bin/pytest tests/ -q`（含 middleware/ + api/ 契约测试）。

### 6.3 已拆除、不要再找

- 没有 `POST /v2/sync/*` —— 同步全部由 `tts-erp-sync.service` 调度（`sync_worker/scheduler.py` 的 `JOBS`）
- 没有 `/v2/linkage/effective-product-links` —— DB 层 view，无 HTTP 端点
- 没有任何 `/miaoshou/*` 路由（出站代理和回调端点未挂 v2，实测 404）
- 没有 `/v1/analytics/sync/*` —— 2026-09-02 硬切 `/v2/analytics/sync/*`（无别名）；`/batches` 已换
  `/dumps`（单 dump object）；cursor 降级 has-data 预检（协议见 `tech-doc/analytics/dump-architecture.md`）
  —— 2026-09-07 v3 区间聚合（Design A）见 `tech-doc/analytics/range-aggregate-history-sync.md`
- 没有 `/v2/analytics/sync/batches`（同 release 删除；`ad_daily_pages` / `ad_cursors` 表 migration 0005 drop）
- v1 路由（`/shops`、`/token/*`、`/orders/*`、`/finance/*`、`/returns/*`、`/cancellations/*`、`/db/*`）全部 404，
  v1→v2 迁移映射见 `external-api.md` 底部 Stability matrix；v1 代码仍在 git history（仅代码级参照——v1 DB 数据已 2026-09-05 归档删除，回滚需先恢复 dump）
