# AGENTS.md — tts-erp

> AI agent 操作指南 · 单一真理源 · 改任何东西之前先读这文件

## 1. 服务是什么

`tts-erp`（v2，2026-08-29 起）是 TikTok Shop 销售 + 妙手采购的**本地分析库 + 只读 API + 定时同步**系统。

- **下游数据源**：TikTok Shop Open API (`open-api.tiktokglobalshop.com`) + 妙手开放平台 (`openapi.wanshifu.com`)
- **凭证**：自持在 `integration.credentials` 表（Fernet 加密，key = `.env TTS_ERP_FERNET_KEY`），
  加解密统一走 `tts_erp_v2/proxy/token_service.py`
- **存储**：PostgreSQL `tts_erp` 数据库（`postgres` 容器，端口 5432），10 schema / 60 表 + 1 view（40 张 v2 表 + 19 张 `public.*` legacy + 1 张 `public.alembic_version`）
- **对外端口**：`9877`（FastAPI 只读分析端点 + 人工成本填写页）
- **同步**：独立进程 `tts-erp-sync.service`（APScheduler，`python -m tts_erp_v2.sync_worker.main`）
- **oauth-receiver (`:9876`)**：v2 不再调用它，仅 4 周回滚观察期内保留
- **公网域名**（用户已配 NAT 穿透）：`daqiang.nat100.top` —— **已 strip 9877 端口**，访问任何 endpoint 用 `http://daqiang.nat100.top/<path>` 而不是 `http://daqiang.nat100.top:9877/<path>`
  - 给用户的 deploy URL / TikTok 填的 redirect URL / 文档里的 curl 示例 — 一律 strip 端口
  - 业务代码 / curl 跑本地用 `http://127.0.0.1:9877/...` 仍然带端口（直连本机）

读数据直接 `curl http://127.0.0.1:9877/v2/...` 查本地库即可。打上游 TikTok 的唯一路径是
sync-worker 的 jobs（经 `tts_erp_v2/proxy/tts_shop`），它们内部处理：

- HMAC-SHA256 签名
- `x-tts-access-token` header
- `shop_cipher` 放 query 哪个位置
- 翻页 / `next_page_token`
- 过期 token 续期（`token.refresh` job，每 6h）

## 2. 必读 — 关键设计决策

### 2.1 凭证单源：`integration.credentials` + `proxy/token_service.py`

```python
# ✓ 正确
from tts_erp_v2.proxy.token_service import load_credentials
cred = load_credentials(session, provider="tiktok", external_account_id=shop_id)
access_token = cred.access_token   # 已解密
shop_cipher = cred.shop_cipher

# ✗ 错误
# 直连 oauth_receiver 库的 oauth_tokens 表      # NO（v1 遗物）
# HTTP 调 :9876 oauth-receiver 拿 token        # NO（v2 不跨进程）
# 自己拿 Fernet key 解密 integration.credentials # NO（绕过统一实现）
```

**为什么**：v2 凭证自持在 `integration.credentials`（Fernet 加密的 JSON envelope，
key = `.env TTS_ERP_FERNET_KEY`）。加解密 / 掩码 / upsert / 续期的唯一实现是
`tts_erp_v2/proxy/token_service.py`
（`encrypt` / `decrypt` / `load_credentials` / `upsert_credentials` / `refresh_if_needed`）。

### 2.2 HMAC 签名（最常出错）

```python
# canonical for POST:
#   {app_secret}{path}{app_key}{value}{shop_cipher}{value}{timestamp}{value}{body}{app_secret}
# canonical for GET (no body):
#   {app_secret}{path}{app_key}{value}{shop_cipher}{value}{timestamp}{value}{app_secret}

# 关键点：
# - keys 按字母序排（app_key < shop_cipher < timestamp）
# - body 必须是 json.dumps(..., ensure_ascii=False) 的**原始字符串**
# - body 在 KV 串之后、结尾 secret 之前
# - 千！万！不！要 URL-encode body

sign = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
```

错误示例（**实测都会 106001 invalid sign**）：

- 把 body 放在 canonical 最后（secret 之后）
- 用 SHA256(body) 代替原始 body
- 不放 body 也不放结尾 secret
- URL-encode body

实现见 `tts_erp_v2/proxy/tts_shop/signing.py`（`TTS_DEBUG_SIGN=1` 在 stderr 打 canonical）。

### 2.3 shop_cipher 永远在 query

不论 GET/POST，`shop_cipher` 都是 query 参数：

```text
GET  /order/202309/orders/<id>?app_key=...&shop_cipher=...&timestamp=...&sign=...
POST /order/202309/orders/search?app_key=...&shop_cipher=...&timestamp=...&sign=...
body: {...}
```

### 2.4 v2 端点用内部 id，不用 shop_id

v1 的 `?shop_id=XXX` 代理透传已整体拆除。v2 读端点的过滤参数是**内部主键**
（`channel_account_id` / `channel_product_id`）：

```bash
# 先查内部 id（external_account_id 即 TikTok shop_id）
curl -s -H "X-API-Key: $TTS_ERP_RO_KEY" "http://127.0.0.1:9877/v2/commerce/channel-accounts"
# → 返回的 channel_account_id 即内部主键（以实际查询结果为准）

curl -s -H "X-API-Key: $TTS_ERP_RO_KEY" \
  "http://127.0.0.1:9877/v2/commerce/sales-orders?channel_account_id=<内部 id>&limit=5"
```

传 `?shop_id=` 不会报错，会被 FastAPI **静默忽略**（返回全量不过滤）——最常见的误用。
同步也不再接受 HTTP 触发：由 `tts-erp-sync.service` 按 `integration.credentials`
里的账户全量调度。

### 2.5 API key 鉴权（2026-08-17 上线，**2026-08-20 起 enforce**；设计文档 `tech-doc/api-key-auth-design.md`）

- 除豁免路径（`/healthz`、`/endpoints`、`/openapi.json`、`/docs`、`/redoc`、`/docs/oauth2-redirect`、`/v2/auth/{login,logout,me}`）外所有端点需要 `Authorization: Bearer <key>` 或 `X-API-Key: <key>`；无 key 401、角色不够 403
- 浏览器另有会话 cookie 登录：`POST /v2/auth/login` 用 API key 换 `tts_session` cookie（见 `tech-doc/browser-login-design.md`）；cookie 会话做 mutation 必须带 `X-Requested-With: tts-erp`（CSRF 闸）
- 三级角色：`readonly` < `readwrite` < `admin`；分类逻辑在 **`tts_erp_v2/middleware/auth.py::required_role()`**（未匹配路径默认按 admin 拦截）；个别端点在 handler 内再校验（`POST /v2/linkage/overrides`=admin、`issues/{id}/resolve`=readwrite、`POST /v2/admin/reset-rate-limit`=admin）
- key 管理走 `python3 api_keys.py create/list/revoke/rotate`（`revoke --prefix` / `rotate --prefix`）；表里只有 SHA-256 哈希（`security.api_keys`），完整 key 只在创建时打印一次
- 模式开关：`.env TTS_ERP_AUTH_MODE=off|shadow|enforce`（生产 = enforce）；cron/脚本用 `.env TTS_ERP_SERVICE_KEY`

## 3. 端点速查

**v2 服务**（生产路径，2026-08-29 起）：所有读写走 `tts_erp_v2.app` 的 `/v2/*` 端点。详见
[`tech-doc/external-api.md`](tech-doc/external-api.md)。下面只列概要 + 历史端点状态。

| 端点 | 用途 |
| ----------------------------------------------- | ------------------------------- |
| `GET /healthz` | 健康检查（`{status, service:"tts-erp-v2", auth_mode}`，公开） |
| `GET /endpoints` | 全部路由清单（公开） |
| `GET\|POST /v2/auth/login`、`POST /v2/auth/logout`、`GET /v2/auth/me` | 浏览器会话登录（公开；API key 换 `tts_session` cookie） |
| `GET /v2/pages/manual-costs?channel_account_id=` | 人工成本填写页（HTML，readonly+） |
| `GET /v2/commerce/channel-accounts` / `/{account_id}` / `/{account_id}/order-stats` | 平台账户列表 / 详情 / 订单统计 |
| `GET /v2/commerce/channel-products` / `/{product_id}` / `/{product_id}/variants` | TikTok 渠道商品 / 详情 / SKU 变体 |
| `GET /v2/commerce/sales-orders` / `/{order_id}` / `/{order_id}/lines` | 销售订单列表 / 详情 / 明细行 |
| `GET /v2/linkage/{product-links,evidence,issues,overrides}` | 妙手采购↔TikTok 销售关联读（readonly） |
| `POST /v2/linkage/issues/{issue_id}/resolve` | 标记关联问题已解决（readwrite，handler 内校验） |
| `POST /v2/linkage/overrides` | 人工覆盖 product_links（admin，handler 内校验） |
| `POST /v2/reporting/manual-costs` | 提交人工成本（readwrite） |
| `GET /v2/reporting/{cost-snapshots,profit-daily,coverage,missing-cost-products}` | 成本快照 / 日利润 / 覆盖率 / 待补成本清单 |
| `GET /v2/spu-images`、`POST /v2/spu-images/upload-url`、`POST /v2/spu-images/{image_id}/confirm`、`GET\|DELETE /v2/spu-images/{image_id}` | SPU 参考图（MinIO presign 上传） |
| `GET /v2/llm-context` | 给 LLM agent 的上下文包（experimental） |
| `GET /v2/admin/rate-limit` | 查看当前限流状态（singleton 存在时 limit / window_s / active_buckets + env var） |
| `POST /v2/admin/reset-rate-limit` | 热重载限流（admin）；body `{new_limit?, reset_buckets?}`，不传 `new_limit` 即重读 `TTS_ERP_RATE_LIMIT_PER_MIN` |
| `GET /v2/analytics/sync/cursor`、`POST /v2/analytics/sync/dumps` | Chrome 扩展广告分析 ingest（readwrite + key scope；自有 envelope；2026-09-02 从 /v1 硬切无别名；同日 dump 化：cursor 降级 has-data，`/batches` → `/dumps` 单 dump，见 tech-doc/analytics/dump-architecture.md） |

**已拆除、不要再找**：

- 没有 `POST /v2/sync/*` —— ad-hoc 同步 HTTP 触发已随 v1 拆除，同步全部由
  `tts-erp-sync.service` 调度（频率见 `tts_erp_v2/sync_worker/scheduler.py` 的 `JOBS`）
- 没有 `/v2/linkage/effective-product-links` —— 那只是 DB 层 view，无 HTTP 端点
- 没有任何 `/miaoshou/*` 路由 —— 出站代理和回调端点都未挂进 v2（见 §10）
- 没有 `/v1/analytics/sync/*` —— 2026-09-02 硬切到 `/v2/analytics/sync/*`（单挂载无别名，legacy `analytics_sync/` 包同 release 删除）
- 没有 `/v2/analytics/sync/batches` —— dump 架构（同 release）换成 `/v2/analytics/sync/dumps`（单 dump object），cursor 从 work-list（items/nextRequiredDay）降级为 has-data 预检；`ad_daily_pages` / `ad_cursors` 表已随 migration 0005 drop
- 读端点过滤参数是内部 id（`channel_account_id` 等），`shop_id` 会被静默忽略（见 §2.4）

**legacy v1 端点状态**：v1 路由（`/shops`、`/token/*`、`/orders/*`、`/finance/*`、`/returns/*`、`/cancellations/*`、`/db/*`、`/miaoshou/*`）已全部拆除，运行时一律 404，**不要再找**。v1→v2 端点迁移映射见 `tech-doc/external-api.md` 底部 Stability matrix；v1 代码留存在 git history（`master` 之前 commits），4 周观察期内仅作紧急回滚参照。

## 4. 改代码时的 do / don't

### DO

- **TDD：先写/改测试，再实现到通过**；v2 测试在 `tests/`（共享 fixtures 在 `tests/conftest.py`：事务回滚隔离、`TEST_%` 哨兵），妙手 SDK 测试在 `tests/miaoshou/`
- **跑测试用 `scripts/test.sh fast`**（日常全量；`tests/migration/` 域已随 v1→v2 迁移脚本一起归档到 `tech-doc/_archive/migrate-v1-to-v2-2026-08-29/`，勿跑勿找，详见 `tech-doc/test-domains.md`）
- 改完调 `bash /home/schan/tts-erp/restart.sh` 验证 healthz 200（注意：只重启 `tts-erp.service`；改了 `jobs/` / `sync_worker/` 要另跑 `systemctl --user restart tts-erp-sync.service`）
- 改完跑 `python3 test_e2e.py`（仓库根，端到端冒烟，需服务在跑）
- 看 `logs/stderr.log` 抓 traceback
- 用 `TTS_DEBUG_SIGN=1` env var 看 canonical string 排查签名问题
- 改 schema 走 schema_tts_erp.sql / schema_oauth.sql（按库拆分），`IF NOT EXISTS` 兼容老库
- **优先复用成熟开源组件**（评估护栏：最近 release < 1 年、issues 关闭率 ≥ 80%、有公司/组织背书优先；无合适开源再自研并留替换 seam）
- **一次性 / 临时脚本放 `scripts/`**（与 `scripts/migrate_v1_to_v2/`、`scripts/regen_schema.py` 同级）：debug 探测、一次性数据导出、smoke / regression 演练等跑一次就丢的小脚本都进这里，**根目录不放**。`scripts/` 里有 `__init__.py` 让它能被 pytest 收集；脚本名前缀 `oneoff_` / `probe_` / `smoke_` / `dump_` 自描述用途。不要把这类脚本 commit 到根目录或业务目录里。
- **Sub-agent 并发改动必须开 worktree**(2026-09-03 起约定):父 agent 派生 sub-agent 做并行改动时(多 lane 任务 / 拆分 PR / 大面积 bug 修复),**每个 sub-agent 都必须**在 `git worktree add .worktrees/<slug> -b <branch>` 下手,`<slug>` 用 kebab-case 描述改动主题(如 `fix-auth-loop` / `redesign-console-ui` / `fix-migration-prod-guardrails`),`<branch>` 用 `<prefix>/<slug>` 命名(`fix/` / `feature/` / `redesign/` / `chore/`)。`.worktrees/` 已在 `.gitignore` 第 55 行,worktree 内的 commit 留本地、**不** push 远端。**禁止**在 master worktree 上让 sub-agent 直接 `git add` / `git commit` —— master 是公共区,只跑读 / 跑测试 / 跑文档 / 跑 `git merge`。起因:08-31 一次 `git reset --hard` 把 master 上 5 条修复 lane 的全部未提交改动一并抹掉,之后所有并发 lane 才统一改走 worktree。
- **Worktree 收尾必须 merge + 清理**(2026-09-03 起约定):sub-agent 完成后,**合并方**(通常父 agent)按序走: (1) `cd` 进 master worktree 拉 sub-agent 的分支;(2) `git merge <branch> --no-ff -m "merge: <slug> (lane <lane-id>)"`;(3) 跑 `bash scripts/test.sh fast` 必须 0 fail;(4) 收尾 `git worktree remove .worktrees/<slug>` + `git branch -D <branch>` + `git worktree prune`;(5) 确认 `git worktree list` 没有残留;(6) 紧接下面「master 改动必须 push」条款推远端。**禁止**用 `git add -A && git commit -m "merge <slug>"` 偷工(那不是 merge,是把别人未审的代码污染进 master);也**禁止** "先合了再说,worktree 留到周末清"—— `.worktrees/` 8 月起累积了 8 条修复 lane(见 `git worktree list` 输出),是这条规范出台前留下的历史产物;新 lane 完成后必须当场收尾,避免无限堆积。
- **master 改动完成必须 push**(2026-09-03 起约定):所有改动完成收尾(测试 0 fail、文档已更新、工作区干净)后,**必须** `git push origin master` 推到远端,不留 unpushed state。**严禁** `--force` / `-f` push(覆盖远端历史); push 失败若为非 fast-forward(origin 上有 master 未见的新 commit),先 `git fetch` + `git rebase` 或 `git merge --no-ff origin/master`,解冲突,**再 push**。这条不覆盖 sub-agent 在 worktree 里的 commit——它们按上面「Sub-agent 并发改动」条款走(本地 commit → 父 agent merge 到 master → 走本条 push)。同一行 commit 后接「Worktree 收尾」或「直接修改 master」都适用本条;半成品 / WIP / 个人 draft commit 一律不得留在 master 上不推。

### DON'T

- ❌ 不要直连 oauth_receiver 库的 `oauth_tokens` 表，也不要 HTTP 调 :9876 拿 token —— v2 凭证在 `integration.credentials`，**只能**走 `tts_erp_v2.proxy.token_service`（Fernet 加解密自持，`encrypt/decrypt/load_credentials/refresh_if_needed`）
- ❌ 不要在 .env 里写 app_secret 给客户端调用者（明文暴露，app_secret 应只服务自己用）
- ❌ 不要在 canonical 里 URL-encode body（用 raw JSON 字符串）
- ❌ 不要绕过 `token_service` 自己拿 Fernet key 解密 credentials（统一实现才有掩码/续期/降级逻辑）
- ❌ 不要假设 `code: 0` 是唯一的 success —— TikTok 也会返回 `code: 105005` (scope 缺失) `code: 36009004` (字段缺失) 等
- ❌ **不要**接 `POST /returns` / `POST /cancellations`（CREATE write endpoint，会在真实店铺创建退货/取消单）。
     这两个端点 2026-08-17 起已从代码中**整个删除**（不再返回 501，而是无路由 404）。v2 在 `tts_erp_v2/jobs/tiktok/after_sales.py` 只读，同上原则如果以后要接，单独 review。
- ❌ **不要**在 v2 端点里读 `public.*` 表。v2 完全读新 10 schema。4 周观察期内 `public.*` 仅作 rollback safety，**是**旧代码路径，但 v2 代码不会直接查。
- ❌ **不要**接 `POST /orders/<id>/{confirm,cancel,update_status,shipping_info,verify_shipping}`。v2 架构是只读分析，写操作已全部拆除。
- ❌ **不要裸跑 `git reset --hard` / `git checkout -- .` / `git clean -f`**(2026-09-01 起约定):多 agent 并发工作时,这类命令会把别人未提交的改动直接清掉(08-31 曾一次抹掉 5 条修复 lane 的全部未提交工作)。看到不属于自己的未提交改动 → 先问,不要清。
- ❌ **不要跑 `tests/migration/` 域测试或 `scripts/migrate_v1_to_v2/` 脚本** —— 它们会真实写生产库(08-31 曾因此把生产凭证回退成 legacy 格式、全线停摆 22h)。这两个目录已随 2026-09-03 归档到 `tech-doc/_archive/migrate-v1-to-v2-2026-08-29/`,原位只留了 README 指针。`TTS_ERP_ALLOW_PROD_MIGRATION=1` 闸已随归档走,v2 服务不再读 `public.*` legacy 表,即使有人把它们 git checkout 出来跑也是空操作。

## 5. 常见 bug + 修复

| 症状                                | 原因                                  | 修复                                          |
|-------------------------------------|---------------------------------------|-----------------------------------------------|
| `106001 invalid sign`               | 签名格式错（最常见）                  | 看 `TTS_DEBUG_SIGN=1` 输出的 canonical 串，对比 2.2 节 |
| `105005 Access denied`              | app 没勾对应 scope                    | Partner Center 改 app scope + 重新授权         |
| `36009004 PageSize is required`     | body 字段名/格式错                    | 查 TikTok API 文档的 Request Body 章节         |
| v2 端点传 `?shop_id=` 数据没过滤     | v2 只认内部 id，`shop_id` 被静默忽略    | 先 `GET /v2/commerce/channel-accounts` 查 `channel_account_id`（§2.4） |
| `TTS_ERP_DB_URL not configured`     | `.env` 缺 DB URL                      | 配 .env                                       |
| `psycopg.OperationalError`         | PG 容器 down / 网络不通                | `docker exec postgres pg_isready`              |
| 物流数据多日不更新                  | 先查 `integration.sync_jobs` 里 `tiktok.logistics` 是否按 10min 在跑（`tts-erp-sync.service` 是否 active） | `systemctl --user status tts-erp-sync.service`；历史 bug：TikTok tracking 列表是**最新事件在前**，取首尾必须按 `update_time_millis` 排序（2026-08-17 已修） |

## 6. 文件清单

| 路径 | 用途 |
| ------------------------------------------------- | -------------------------------------------- |
| **`tts_erp_v2/app.py`** | **主服务**（FastAPI `build_app()` 工厂；`tts-erp.service` 跑 `uvicorn tts_erp_v2.app:app`，监听 9877） |
| `tts_erp_v2/api/v2/` | 路由：commerce / linkage / reporting / pages / spu_images / auth / llm_context / **admin** (限流热重载等) / **analytics**（Chrome 扩展 ingest） |
| `tts_erp_v2/middleware/` | `auth.py`（API key + 角色矩阵）、`session_auth.py`（cookie 会话）、`rate_limit.py`（60s 滑动窗，env 配）、`access_log.py` |
| `tts_erp_v2/proxy/` | 出站层：`tts_shop/`（TikTok 签名 + 客户端）、`miaoshou/`、`token_service.py`（凭证 Fernet 加解密 + 续期） |
| `tts_erp_v2/jobs/` | 同步 job 实现：`tiktok/*`、`miaoshou/*`、`analytics_retention.py`（日级）、`reporting.py`（cost_snapshots 6h / profit_daily 1h）、`token_refresh.py`（6h）、`runner.py`；**注册状态以 `sync_worker/scheduler.py` 的 `JOBS` + 顶部 NOTE 为准** |
| `tts_erp_v2/sync_worker/` | APScheduler worker（`scheduler.py` 的 `JOBS` 注册表；独立跑在 `tts-erp-sync.service`） |
| `tts_erp_v2/db/models/` | 10 schema SQLAlchemy 模型（`analytics` schema 5 张表：`ad_audit_log` / `ad_daily_completeness` / `ad_raw` / `ad_records` / `ad_shop_timezones`） |
| `tts_erp_v2/analytics/` | Chrome 扩展广告分析 ingest 的 domain + SQLAlchemy repository（路由在 `api/v2/analytics.py`，表在 `analytics` schema） |
| `tts_erp_v2/linkage/` / `reporting/` / `storage/` | 关联计算 / 成本利润重算 / MinIO 对象存储 |
| `api_keys.py` | API key 管理 CLI（create/list/revoke/rotate；表在 `security.api_keys`） |
| `schema_tts_erp.sql` / `schema_oauth.sql` | PG 表结构（按库拆分；`python3 scripts/regen_schema.py` 重新生成） |
| **`miaoshou/`** | 妙手 SDK 包（客户端 + MD5 签名 + 36 出站 endpoint + 18 回调 payload；**无 HTTP 路由**，进程内被 jobs 用） |
| `conftest.py` | pytest 根 conftest（仅 path 引导；业务 fixtures 在 `tests/conftest.py`） |
| `tests/` | v2 测试套件（api / jobs_* / linkage / middleware / proxy / reporting / storage / sync_worker 域；migration 域已归档，见 `tech-doc/_archive/` 行） |
| `tests/miaoshou/` | 妙手 SDK 单测（91 个 test function，15 文件） |
| `test_e2e.py` / `test_e2e_finance.py` | 端到端冒烟（仓库根；需服务在跑） |
| `.env` | 配置（0600，含 app_key/secret/DB URL/Fernet key/MinIO） |
| `restart.sh` | 重启脚本（只重启 `tts-erp.service`；sync-worker 要单独 restart） |
| `prod-switch/` | v1→v2 切换演练 6 脚本（preflight / switch / install-sync-worker / postswitch-smoke / observe-archive / rollback） |
| `tech-doc/_archive/` | **归档区（勿恢复使用）**：v1 时代设计文档（`*v1*.md` / `openapi.yaml` / 旧 analytics 协议文档等）+ 已归档代码（`sync-cron-legacy-2026-08/` = v1 cron 同步脚本；`migrate-v1-to-v2-2026-08-29/` = v1→v2 一次性迁移脚本 + 测试；`review-remediation-2026-08.md` = 已执行完的修复计划） |
| `tech-doc/` | 设计文档目录（`external-api.md` 是端点活契约；`analytics/` 是 Chrome 扩展 ingest 协议规范） |
| `setup/` | 用户向 setup 文档（`tts-erp.md` / `analytics-sync.md`）+ schema 备份 |
| `AGENTS.md` / `README.md` / `handoff.md` / `CHANGELOG.md` | 本文件 / 人类说明 / 跨 session 交接 / 变更日志 |

## 7. 部署 / 启动

```bash
# 一次性
ssh schan@192.168.47.130 "mkdir -p /home/schan/tts-erp/{logs,setup,tests}"

# 部署
scp F:\path\to\tts-erp\* schan@192.168.47.130:/home/schan/tts-erp/
ssh schan@192.168.47.130 "chmod 600 /home/schan/tts-erp/.env && chmod +x /home/schan/tts-erp/restart.sh"

# PG schema (幂等)
cat schema_tts_erp.sql | docker exec -i postgres psql -U postgres -d tts_erp

# 启动（systemd --user 托管，开机自启；restart.sh 内部走 systemctl --user restart）
ssh schan@192.168.47.130 "bash /home/schan/tts-erp/restart.sh"
```

## 7.1 进程托管（2026-08-18 起；v2 切流后 3 个服务）

都由 **systemd user 单元**托管（`~/.config/systemd/user/`，`Linger=yes` 已开，开机自启，无需登录）：

- `tts-erp.service` — `.venv/bin/python -m uvicorn tts_erp_v2.app:app --host $TTS_ERP_HOST --port $TTS_ERP_PORT`
  （cwd = 仓库根），`EnvironmentFile=.env`
- `tts-erp-sync.service` — `.venv/bin/python -m tts_erp_v2.sync_worker.main`
  （APScheduler sync-worker；安装脚本 `prod-switch/install-sync-worker.sh`）
- `oauth-receiver.service` — 仅 4 周回滚观察期保留（见 oauth-receiver 的 setup 文档）
- `tts-erp-watchdog.timer` — 同步健康巡检（每 10min 跑 `scripts/watchdog_sync.py`, findings 追加到 `logs/watchdog.log`; 未配 webhook, 约定由 agent 会话定时扫该日志代告警)

```bash
systemctl --user status tts-erp.service       # API 状态
systemctl --user status tts-erp-sync.service  # sync-worker 状态
systemctl --user restart tts-erp.service      # = restart.sh（只重启 API）
systemctl --user restart tts-erp-sync.service # 改了 jobs/ 或 sync_worker/ 后必须单独跑这个
journalctl --user -u tts-erp -n 50            # systemd 日志（业务日志仍看 logs/）
```

## 8. Token 续期

v2 起 token 续期由 **sync-worker 的 `token.refresh` job**（每 6h，见
`tts_erp_v2/sync_worker/scheduler.py` JOBS）进程内完成：
`tts_erp_v2/jobs/token_refresh.py` 扫 `integration.credentials` 里临近过期的行，
调 `proxy/token_service.refresh_if_needed()` 续期并重新加密写回。不再依赖
oauth-receiver 的 HTTP 续期端点；oauth-receiver :9876 仅在 4 周回滚观察期内保留。
v1 的 `refresh_shop_token()` / `oauth_receiver_core.py` 已随 v1 代码删除（历史实现在 git history）。

## 9. External API（外部端点契约）

The FastAPI service at `:9877` exposes **stable external API contracts** under
`/v2/*` for clients (dashboards, BI tools, internal apps). The **full live
contract** — auth, rate limiting, CORS, pagination, every endpoint's schema
and curl examples, plus the Stability matrix (role requirements + v1 status
for every endpoint) — lives in:

**[`tech-doc/external-api.md`](tech-doc/external-api.md)** — read this before
adding or changing any external-facing endpoint. Below are only the facts an
agent must remember without re-reading it.

### 9.1 Key facts

`tech-doc/external-api.md` 顶部的 **TL;DR — quick reference for agents** 就是为此准备的
（每端点 URL + role、internal-id 过滤、limit/offset 分页、money-as-string、ISO-8601、
CSRF header），读它，不要在本文件重复维护。

### 9.2 Creating an API key

```bash
python3 api_keys.py create --role readonly --name "external-orders-reader"
python3 api_keys.py list
python3 api_keys.py revoke --prefix "ttserp_ro_…"   # revoke; rotate --prefix to rotate
```

### 9.3 Middleware order (do not change without thought)

FastAPI `add_middleware` wraps in reverse, so add order = innermost-first
(`tts_erp_v2/app.py::build_app()`):

1. `RateLimitMiddleware` (innermost requested layer)
2. `AuthMiddleware`
3. `CORSMiddleware`
4. `AccessLogMiddleware` (outermost — sees final status + duration)

Auth runs BEFORE RateLimit — the rate limiter buckets by key. Do not put auth
outermost or rate limiting breaks (key_id will be None).

### 9.4 Validation harness

- `bash prod-switch/postswitch-smoke.sh` — 7-step prod smoke (healthz / auth 401 / v2 read endpoints / page / sync_jobs freshness)
- `.venv/bin/pytest tests/ -q` — incl. `tests/middleware/` (auth/ratelimit/session) + `tests/api/` endpoint contract tests

Run after any change to `tts_erp_v2/app.py`, `tts_erp_v2/middleware/*`.

### 9.5 What is NOT external-stable

`GET /v2/analytics/sync/*` (extension ingest contract, follows extension
release cadence), `GET /v2/llm-context` (experimental), and mutation body
fields such as `POST /v2/linkage/overrides` may break without notice.
External clients should NOT depend on these. Miaoshou 已无任何 HTTP 面
（见 §10），不要等它回来。

## 10. 妙手开放平台（apifox fd54e57e-9b98-4c34-bada-306221c39e68）

apifox 标题“妙手开放平台”，实际底层 endpoint 指向 `openapi.wanshifu.com`。
集成代码在 **`miaoshou/`**（独立 SDK 包：`MiaoshouClient` / `MiaoshouErpClient` + `miaoshou_signing.py`，
36 出站 endpoint + 18 回调 payload；**不**单独起服务，进程内被 jobs 用）。

- **接入（人类开发者操作，agent 不代办）**：user.wanshifu.com 申请 `licenseId` + `companySecret`
  → 写 .env（`MIAOSHOU_LICENSE_ID` / `MIAOSHOU_COMPANY_SECRET` / `MIAOSHOU_ENV` /
  `MIAOSHOU_HTTP_TIMEOUT`）；测试账号在 test-user.wanshifu.com
- **v2 没有任何 `/miaoshou/*` HTTP 路由**（v1 出站代理 + 回调端点已拆，实测 404）：出站走
  `tts_erp_v2/jobs/miaoshou/*` 进程内调 SDK（经 `tts_erp_v2/proxy/miaoshou/`）落 `procurement.*`；
  回调 payload 模型 + `dispatch_callback()` 保留在 `miaoshou/callbacks/` 但**无 HTTP 入口**，
  要恢复需在 v2 app 挂路由（单独评审）
- **调度状态**：谁注册了 / 谁故意不注册 → 看 `tts_erp_v2/sync_worker/scheduler.py` 顶部
  `NOTE`（以那里为准，勿在本文件重复维护）
- **签名**（apifox doc-824327）：`busData = base64(json.dumps(params, ensure_ascii=False))`，
  `sign = MD5(busData + companySecret).upper()`；envelope 含 `licenseId / companySecret /
  sign / busData / timestamp`（毫秒）。实现见 `miaoshou/miaoshou_signing.py`（无独立 SIGNING.md）；
  调试 `MIAOSHOU_DEBUG_SIGN=1` 打 canonical + sign；锁定向量在
  `tests/miaoshou/test_signing.py::test_build_sign_doc_824327_vector`
- **测试**：`.venv/bin/pytest tests/miaoshou/ -q`（91 个 test function / 15 文件）+ `.venv/bin/pytest tests/jobs_miaoshou/ -q`
