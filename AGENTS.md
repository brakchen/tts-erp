# AGENTS.md — tts-erp

> AI agent 操作指南。改任何东西之前先读这文件。仓库事实（schema / 端点 / 协议）以 `tech-doc/`
> 对应文档为 truth source，本文件只放 agent 不知道就会做错的事。

## 1. Stack（项目栈）

Python 3.14 · FastAPI + uvicorn（`:9877`）· SQLAlchemy 2 + psycopg3 · PostgreSQL 容器（`:5432`，
11 schema / 51 表 + 1 view（v1 `public.*` 业务表 2026-09-05 归档删除；analytics 4 张僵尸表 migration 0007 drop；2026-09-11 `chrome_sync`→`plugin`、`analytics` 并入 `plugin`）· APScheduler（独立 sync-worker 进程）· MinIO · Fernet 加密 · systemd user units。

- **测试 DB 隔离（2026-09-07）**：tests 跑在专用 `tts_erp_v3_test` 库；prod `tts_erp` 仅 systemd API + 人工 dev
  连接，永不被测试污染。`.env.test`（gitignored）= `.env` 的 dbname 替身；`scripts/test.sh` 启动时 source
  它。开发者在 test db 上手动跑 `bash scripts/import_prod_to_test.sh --yes` 可按需把 prod 数据搬进
  test db（multi-pass FK 处理，默认含 credentials 让 FK 走得通，prod Fernet key 不变所以仍可解密）。

- v2（2026-08-29 切流生产）：TikTok Shop 销售 + 妙手采购 → **本地分析库 + 只读 API + 定时同步**
- 下游：TikTok Shop Open API (`open-api.tiktokglobalshop.com`) + 妙手开放平台 (`openapi.wanshifu.com`)
- 打上游 TikTok 的**唯一**路径 = sync-worker jobs（经 `tts_erp_v2/proxy/tts_shop`，内部处理 HMAC 签名 /
  `x-tts-access-token` / shop_cipher 位置 / 翻页 / 过期 token 续期）。不要自己拼上游请求。
- 读数据：`curl http://127.0.0.1:9877/v2/...`（本地直连带端口）
- 公网域名 `daqiang.nat100.top`（NAT **已 strip 9877 端口**）：nginx 只把 `/tts/*` 转发给 API，
  无前缀的 `daqiang.nat100.top/v2/...` 落在 ProfitLens 前端 404。给用户的 URL / TikTok 填的 redirect URL /
  纯公网 curl 一律 `http://daqiang.nat100.top/tts/<path>`；本地直连 `127.0.0.1:9877/v2/...` 才不带 `/tts`
  （前缀取自 `TTS_ERP_EXTERNAL_PREFIX`，当前 `/tts`；middleware 剥前缀匹配豁免，两端一致）

## 2. Commands（命令）

```bash
bash scripts/test.sh fast                          # 日常全量测试（唯一入口；自动 source .env.test 切到 tts_erp_v3_test；migration 域已归档勿跑）
TTS_ERP_TEST_OFF=1 bash scripts/test.sh fast      # 跳过 .env.test source（仅迁移/手动调试用；默认禁走）
.venv/bin/pytest tests/<domain>/ -q                # 单域（如 tests/miaoshou/、tests/jobs_tiktok/）；
                                                    # worktree 内无 .venv，改用 /home/schan/tts-erp/.venv/bin/pytest（见 §11）
bash scripts/import_prod_to_test.sh --dry-run      # 看 import plan（37 张表 + credentials + api_keys）
bash scripts/import_prod_to_test.sh --yes          # 实际把 prod 数据导入 tts_erp_v3_test（multi-pass FK）
bash scripts/import_prod_to_test.sh --exclude-credentials --yes  # 不拷 credentials（部分 FK 表会空）
bash restart.sh                                    # 重启 API = systemctl --user restart tts-erp.service
systemctl --user restart tts-erp-sync.service      # 改了 jobs/ 或 sync_worker/ 后必须单独跑
python3 test_e2e.py / test_e2e_finance.py          # 端到端冒烟（需 :9877 在跑）
bash prod-switch/postswitch-smoke.sh               # 7 步生产冒烟（healthz/auth/v2 读端点/page/sync_jobs）
systemctl --user status tts-erp{,-sync}.service    # 进程状态
journalctl --user -u tts-erp -n 50                 # systemd 日志
# 业务日志：logs/stderr.log 抓 traceback；watchdog 巡检发现看 logs/watchdog.log（约定 agent 定时扫，代告警）
```

- schema 变更流程：改 `tts_erp_v2/db/models/` → `python3 scripts/regen_schema.py` 重新生成
  `schema_tts_erp.sql`（`IF NOT EXISTS` 幂等兼容老库）→ 应用
- API key：`python3 api_keys.py create/list/revoke/rotate`（`--prefix` 定位；明文只创建时打印一次）
- 签名调试：`TTS_DEBUG_SIGN=1`（TikTok）/ `MIAOSHOU_DEBUG_SIGN=1`（妙手）在 stderr 打 canonical
- 测试规范：TDD 先写测试再实现；共享 fixtures 在 `tests/conftest.py`（事务回滚隔离、`TEST_%` 哨兵数据）；
  跑不过 0 fail 不收尾
- **测试环境隔离（2026-09-07）**：所有测试默认连 `tts_erp_v3_test`（专用 test db，已 schema 一致）。
  `bash scripts/test.sh` 自动 source `.env.test`（gitignored）切到 test db。prod API service /
  `uvicorn` 本地启动仍读 `.env` 连 prod `tts_erp`，**零变更**。安全护栏：tests/conftest.py 检测到
  `TTS_ERP_DB_URL` 指向 prod-shape dbname（`tts_erp` / `tts_erp_prod`）会往 stderr 打 WARNING；
  scripts/test.sh 会在 .env.test 缺失时直接退出。需要 prod-shaped 数据时运行
  `bash scripts/import_prod_to_test.sh --yes`。

## 3. Code style（写代码风格，带示例）

```python
# 时区：一律 aware UTC —— datetime.utcnow() 已弃用（DeprecationWarning），用 datetime.now(UTC)
from datetime import UTC, datetime
calculated_at = datetime.now(UTC)

# ⚠ 口径提醒（2026-09-06 用户拍板）：存储/时间戳一律 UTC，但【统计与报表的日期归期
#   按店铺当地时区】切分，不是 UTC 日界。当前店铺 Bridge nook 在越南 = UTC+7：
#   某“销售日”= VN 自然日，即 UTC [T-1 17:00, T 17:00)。
#   例：营收按 paid_at 落 VN 日、退款按完结时间 VN 日、广告按 VN 日；给用户的
#   日期口径/筛选与 TikTok 后台订单管理一致（后台默认按下单时间 + 本地时区）。
#   换算：VN 日界 = paid_at - timedelta(hours=7) 后再取 date（或用 AT TIME ZONE 'Asia/Ho_Chi_Minh'）。

# DB：SQLAlchemy 2.0 style（select()/session），不在 async handler 里跑同步 psycopg
#   （会挂死 event loop——2026-08 P1 事故，中间件层已踩过）
# 测试数据一律 TEST_ 前缀（生产表约束/唯一键不会撞）
# 一次性脚本放 scripts/（oneoff_/probe_/smoke_/dump_ 前缀自描述），不 commit 到根目录或业务目录
# type hints + from __future__ import annotations 全开；ruff + pyright 已配置
```

## 4. 关键架构约束

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

凭证 = Fernet 加密的 JSON envelope（key = `.env TTS_ERP_FERNET_KEY`）。加解密 / 掩码 / upsert / 续期的
**唯一**实现是 `token_service.py`（`encrypt` / `decrypt` / `load_credentials` / `upsert_credentials` /
`refresh_if_needed`）。token 续期由 sync-worker 的 `token.refresh` job 每 6h 进程内完成，不需要也不要有
HTTP 续期端点。

### 4.2 TikTok HMAC 签名（最常出错）

```text
canonical POST: {app_secret}{path}{app_key}{value}{shop_cipher}{value}{timestamp}{value}{body}{app_secret}
canonical GET : {app_secret}{path}{app_key}{value}{shop_cipher}{value}{timestamp}{value}{app_secret}
```

- keys 按字母序（app_key < shop_cipher < timestamp）；`shop_cipher` 永远在 **query**（GET/POST 都是）
- body = `json.dumps(..., ensure_ascii=False)` 的**原始字符串**，在 KV 串之后、结尾 secret 之前；
  **千万不要 URL-encode body**（实测全部 106001 invalid sign）
- 实现见 `tts_erp_v2/proxy/tts_shop/signing.py`；排查用 `TTS_DEBUG_SIGN=1` 打 canonical

### 4.3 API key 鉴权（enforce 模式；设计文档 `tech-doc/api-key-auth-design.md`）

- 除豁免路径（`/healthz`、`/endpoints`、`/openapi.json`、`/docs`、`/redoc`、`/docs/oauth2-redirect`、
  `/v2/auth/{login,logout,me}`）外，所有端点要 `Authorization: Bearer <key>` 或 `X-API-Key: <key>`；
  无 key 401、角色不够 403。完整角色矩阵见 `tech-doc/external-api.md` + `middleware/auth.py::required_role()`
- 三级角色 `readonly` < `readwrite` < `admin`；handler 内再校验：linkage overrides=admin、
  issues/{id}/resolve=readwrite、admin/reset-rate-limit=admin
- 浏览器会话：`POST /v2/auth/login` 用 API key 换 `tts_session` cookie（见 `tech-doc/browser-login-design.md`）；
  cookie 会话做 mutation 必须带 `X-Requested-With: tts-erp`（CSRF 闸）
- key 入库只存 SHA-256 哈希（`security.api_keys`）；模式开关 `.env TTS_ERP_AUTH_MODE=off|shadow|enforce`
  （生产 = enforce）；cron/脚本用 `.env TTS_ERP_SERVICE_KEY`

## 5. 端点速查

**全部端点 + role + 分页/格式**：读 `tech-doc/external-api.md` 顶部 **TL;DR**（活契约，别在本文件复制）；
本机实时清单 `GET /endpoints`。端点契约要点：分页 `limit`(1..500, 默认100)/`offset`；时间 ISO-8601 UTC；
money 列序列化为 JSON 字符串（用 Decimal 解析）；`POST /v2/linkage/overrides`=admin。

**过滤用内部主键**（`shop_pk` / `spu_pk`），不是 `shop_id`——传 `?shop_id=`
不报错但被 FastAPI **静默忽略**（返回全量不过滤）。先查再过滤：

```bash
curl -s -H "X-API-Key: $TTS_ERP_RO_KEY" "http://127.0.0.1:9877/v2/commerce/channel-accounts"
# → external_account_id 即 TikTok shop_id；返回里的 shop_pk 即内部主键（以实际结果为准）
curl -s -H "X-API-Key: $TTS_ERP_RO_KEY" \
  "http://127.0.0.1:9877/v2/commerce/sales-orders?shop_pk=<内部 id>&limit=5"
```

**已拆除、不要再找**：

- 没有 `POST /v2/sync/*` —— 同步全部由 `tts-erp-sync.service` 调度（`sync_worker/scheduler.py` 的 `JOBS`）
- 没有 `/v2/linkage/effective-product-links` —— DB 层 view，无 HTTP 端点
- 没有任何 `/miaoshou/*` 路由（出站代理和回调端点未挂 v2，实测 404）
- 没有 `/v1/analytics/sync/*` —— 2026-09-02 硬切 `/v2/analytics/sync/*`（无别名）；`/batches` 已换
  `/dumps`（单 dump object）；cursor 降级 has-data 预检（协议见 `tech-doc/analytics/dump-architecture.md`）
  —— 2026-09-07 v3 区间聚合（Design A）见 `tech-doc/analytics/range-aggregate-history-sync.md`
- 没有 `/v2/analytics/sync/batches`（同 release 删除；`ad_daily_pages` / `ad_cursors` 表 migration 0005 drop）
- v1 路由（`/shops`、`/token/*`、`/orders/*`、`/finance/*`、`/returns/*`、`/cancellations/*`、`/db/*`）全部 404，
  v1→v2 迁移映射见 `external-api.md` 底部 Stability matrix；v1 代码仍在 git history（仅代码级参照——v1 DB 数据已 2026-09-05 归档删除，回滚需先恢复 dump）

## 6. Boundaries（不要碰）

- ❌ **不得删除 / 截断 prod 库数据**。生产 schema 是真理之源（§1）；任何 `TRUNCATE` / `DELETE` /
  `DROP TABLE` / `DROP SCHEMA` 必须先开 admin 端点 + 人工决策（`/v2/admin/purge-plugin-data`
  是唯一合法路径），或者**仅在专用 test 库 `tts_erp_v3_test` 操作**。所有测试**只能**连 test 库
  （走 `bash scripts/test.sh`，它自动 source `.env.test`；**禁止裸跑 `.venv/bin/pytest`** —— 它读
  `.env` = prod `tts_erp`）；禁止直接连 prod dbname（`tts_erp` / `tts_erp_prod`）跑测试 / 迁移 /
  手动 SQL。prod schema 改名 / 数据搬迁 migration 由用户**手动触发** `alembic upgrade head`，
  agent **绝不**自动跑（agent 只在 test 库验证）；改名类迁移要与服务重启挨着做，否则旧 schema 名
  的运行进程会报 `relation does not exist`
- ❌ 不要直连 v1 `oauth_tokens` 表（库已 DROP，备份 `backups/oauth_receiver_v1_legacy_*.sql.gz`）/
  不要自己拿 Fernet key 解密 `integration.credentials` —— 凭证只能走 `proxy.token_service`（见 §4.1）
- ❌ 不要重建 / 依赖 `public.*` v1 遗留表（v2 只读 11 schema；v1 业务表 2026-09-05 已 DROP，归档在
  `/home/schan/backups/tts_erp_public_v1_legacy_*.sql.gz`）。`public` schema 现仅存 v2 基础设施：41 个
  updated_at 触发器依赖的 `public.fn_touch_updated_at()`——删它 = 全库 updated_at 停摆，动之前先确认
- ❌ 不要接写端点：`POST /returns|/cancellations`（会在真实店铺创建退货/取消单）、
  `POST /orders/<id>/{confirm,cancel,update_status,shipping_info,verify_shipping}` —— v2 是只读分析架构，
  写操作全部拆除（若未来要接，单独 review）
- ❌ 不要在 .env 里写 app_secret 给客户端调用者（明文暴露）
- ❌ 不要裸跑 `git reset --hard` / `git checkout -- .` / `git clean -f`（会清掉并发 lane 未提交改动——
  08-31 曾一次抹掉 5 条 lane 的全部工作）；看到不属于自己的未提交改动 → 先问，不要清
- ✅ 合法清理替代（按场景选）：
  - `git revert <commit>` —— 撤销已 push 的公共 commit，生成反向 commit（不丢历史）
  - `git stash` / `git stash pop` —— 暂存**自己的** WIP（绝不 stash 别人的）
  - `git restore --staged <file>` —— 仅取消 staged（worktree 字节不动）
  - `git restore --source=<commit> -- <file>` —— 从指定 commit 取单文件版本
  - `git checkout -- <file>`（指定文件，非 `-- .`）—— 取消**自己的** unstaged 改动
- ❌ 不要跑 `tests/migration/` 或 `scripts/migrate_v1_to_v2/`（已归档到
  `tech-doc/_archive/migrate-v1-to-v2-2026-08-29/`，勿恢复；08-31 曾把生产凭证回退成 legacy 格式停摆 22h）
- ❌ 不要假设 TikTok `code: 0` 是唯一 success（也有 `105005` scope 缺失 / `36009004` 字段缺失等）
- ❌ 不要改 `tts_erp_v2/app.py` 中间件顺序（RateLimit 最内 → Auth → CORS → AccessLog 最外；Auth 必须在
  RateLimit 之前才能按 key 分桶）
- `tech-doc/_archive/` = 归档区：v1 时代文档 + 已归档代码（`sync-cron-legacy-2026-08/` v1 cron、
  `migrate-v1-to-v2-2026-08-29/` 迁移脚本），只作历史记录，勿恢复使用

## 7. 常见 bug + 修复

| 症状 | 原因 | 修复 |
| --- | --- | --- |
| `106001 invalid sign` | 签名格式错（最常见） | `TTS_DEBUG_SIGN=1` 看 canonical，对比 §4.2 |
| `105005 Access denied` | app 没勾 scope | Partner Center 改 app scope + 重新授权 |
| `36009004 PageSize is required` | body 字段名/格式错 | 查 TikTok API 文档 Request Body 章节 |
| v2 端点传 `?shop_id=` 没过滤 | v2 只认内部 id，静默忽略 | 先查 `shop_pk`（§5） |
| 物流数据多日不更新 | `tiktok.logistics` job 没在跑 | `systemctl --user status tts-erp-sync.service`；取 tracking 首尾必须按 `update_time_millis` 排序（列表最新在前） |
| `psycopg.OperationalError` | PG 容器 down | `docker exec postgres pg_isready` |

## 8. 进程托管 / 目录地图

systemd user units（`Linger=yes` 开机自启，无需登录）：`tts-erp.service`（uvicorn API，cwd=仓库根，
`EnvironmentFile=.env`）、`tts-erp-sync.service`（APScheduler worker，安装脚本
`prod-switch/install-sync-worker.sh`）、`tts-erp-watchdog.timer`
（每 10min 巡检 → `logs/watchdog.log`）。

```text
tts_erp_v2/
├── app.py               # FastAPI build_app() 工厂（中间件顺序见 §6）
├── api/v2/              # 路由：commerce / linkage / reporting / pages / spu_images / auth /
│                        #   llm_context / admin（rate-limit / purge-plugin-data / shops 注册：插件店铺人工登记进
│                        #   commerce.shops，data_source='plugin'（枚举 api|plugin），仅服务查询关联，readwrite 角色） /
│                        #   analytics（插件广告 dump ingest：/v2/analytics/sync/* → 落 plugin.* schema）
├── middleware/          # auth.py（角色矩阵）、session_auth.py、rate_limit.py、access_log.py
├── proxy/               # 出站层：tts_shop/（TikTok 签名+客户端）、miaoshou/、token_service.py
├── jobs/                # 同步 job 实现：tiktok/*、miaoshou/*、
│                        #   reporting（cost_snapshots 6h / profit_daily 1h）、token_refresh（6h）、
│                        #   ad_merge_today2daily（plugin.ad_merge_today2daily，ad_today→ad_daily 跨天固化）、runner
├── sync_worker/         # APScheduler；JOBS 注册表 + 调度状态（顶部 NOTE，以它为准）
├── db/models/           # 11 schema SQLAlchemy 模型 — plugin.py 为插件 dump 的全部 12 张表：
│                        #   订单/物流/结算 7 张（orders/order_lines/shipments/tracking_events/settlements/
│                        #   settlement_details/raw_log，原 chrome_sync.py）+ 广告 5 张（ad_today/ad_daily/
│                        #   ad_monthly/ad_raw_log/plugin_logs，原 analytics.py）
├── plugin/orders/       # 插件 dump 数据访问层：解析 TikTok 响应 + upsert 到 plugin.*
│                        #   （订单/物流/结算；原 tts_erp_v2/chrome_sync/）
├── plugin/ads/          # 插件广告 dump 数据访问层：coverage 查询 / upsert ad_* / plugin_logs
│                        #   （原 tts_erp_v2/analytics/{domain,repository}.py）
├── analytics/ linkage/ reporting/ storage/
│                        # analytics/ 只留读侧（spu_roi.py ROI 看板，读 plugin.ad_*）
└── static/

miaoshou/                # 妙手 SDK 包（独立包：client + miaoshou_signing.py；无 HTTP 路由，进程内用）
api_keys.py              # key 管理 CLI     schema_tts_erp.sql   restart.sh
tests/                   # v2 测试（api/jobs_*/linkage/middleware/proxy/reporting/storage/sync_worker）
tech-doc/                # 设计文档（external-api.md 端点活契约；analytics/；test-domains.md）
setup/                   # 用户向 setup 文档（tts-erp.md / analytics-sync.md）

根目录：conftest.py（pytest 路径引导）· .env（0600，勿 commit）· handoff.md（跨 session 交接）·
CHANGELOG.md · pyproject.toml（ruff/pytest 配置）
```

## 9. External API（外部契约）

FastAPI `:9877` 对外暴露 stable `/v2/*` 契约（dashboards / BI / internal apps）。**完整活契约**（auth /
限流 / CORS / 分页 / 每端点 schema + curl + Stability matrix）= `tech-doc/external-api.md`——加/改任何
外部端点前先读它。下面只列该文档没有、agent 需要记住的：

### 9.1 不稳定端点（可随时 break，外部 client 勿依赖）

`GET /v2/analytics/sync/*`（Chrome 扩展 ingest 契约，随扩展发布节奏演进）、`GET /v2/llm-context`
（experimental）、`POST /v2/linkage/overrides` 等 mutation body 字段。Miaoshou 已无任何 HTTP 面，不要等它回来。

### 9.2 改 app.py / middleware 后验证

`bash prod-switch/postswitch-smoke.sh`（7 步冒烟）+ `.venv/bin/pytest tests/ -q`（含 middleware/ + api/ 契约测试）。

## 10. 妙手开放平台

apifox 标题“妙手开放平台”，底层 endpoint 指向 `openapi.wanshifu.com`。SDK 包在 `miaoshou/`
（`MiaoshouClient` / `MiaoshouErpClient` + `miaoshou_signing.py`；36 出站 endpoint + 18 回调 payload；
进程内被 jobs 用，**不**单独起服务）。接入（申请 licenseId + companySecret 写 .env `MIAOSHOU_*`）是
人类开发者操作，agent 不代办；测试账号在 test-user.wanshifu.com。

- 调度状态：谁注册了 / 谁故意不注册 → `sync_worker/scheduler.py` 顶部 `NOTE`（以那里为准，勿重复维护）
- 签名（apifox doc-824327）：`busData = base64(json.dumps(params, ensure_ascii=False))`；
  `sign = MD5(busData + companySecret).upper()`；envelope = `licenseId / companySecret / sign / busData /
  timestamp`（毫秒）。锁定向量 `tests/miaoshou/test_signing.py::test_build_sign_doc_824327_vector`
- 测试：`.venv/bin/pytest tests/miaoshou/ -q`（91 个 test / 15 文件）+ `tests/jobs_miaoshou/ -q`

## 11. Git 协作约定

- **并发 sub-agent 必须开 worktree**：`git worktree add .worktrees/<slug> -b <prefix>/<slug>`
  （slug = kebab-case 主题，prefix = fix/feature/redesign/chore）。worktree 内 commit 留本地**不 push**；
  master worktree 是公共区，只跑读 / 测试 / 文档 / merge。`.worktrees/` 已在 .gitignore
- **新 worktree 环境准备（开完第一步，必做）**：worktree 只含 tracked 文件，`.env`（gitignored）和 `.venv`
  都不会跟过去——不先补环境，第一发 DB / curl / 测试必炸（`TTS_ERP_DB_URL not set`、API key 401、
  `ModuleNotFoundError`），且症状看起来像代码问题，会白烧大量 token 排查（09-05 实测教训）。开完即做：

  ```bash
  git worktree add .worktrees/<slug> -b <prefix>/<slug>
  cd .worktrees/<slug> && ln -s ../../.env .env   # 软链主仓 .env；已有 worktree 全是软链，永远最新不过期
  # 备选：cp /home/schan/tts-erp/.env .env —— 独立副本也行，但 .env 一改（key rotate / DB URL /
  #   TTS_ERP_AUTH_MODE 切换）副本就过期，症状更诡异；软链是仓库惯例，优先软链
  bash scripts/test.sh fast                      # ✓ 能跑（test.sh 已自动 fallback 主仓 venv）
  /home/schan/tts-erp/.venv/bin/pytest tests/<domain>/ -q   # 裸 pytest 用绝对路径，别用 .venv/bin/pytest
  ```

  - venv 同理不在 worktree：一律显式用主仓绝对路径 `/home/schan/tts-erp/.venv/bin/...`，不要在 worktree 里
    新造 venv；§2 的 `.venv/bin/pytest` 只对 master 有效
  - **不要改 / 删 worktree 里的 .env**：软链会写穿/穿透到主仓 `.env`（全 lane 共享凭证），只由 master 维护
  - **调试 .env 副作用警告**：所有 worktree 软链到主仓同一 `.env`，任一 lane 临时改 `.env`（比如
    `TTS_ERP_AUTH_MODE=off` 本地调试、`TTS_ERP_DB_URL` 切测试库）会**立即污染所有 lane**。调试后
    **必须立即还原**，或用 `.env.local` 覆盖软链（gitignored，不写穿到主仓）
  - pi-lens 自动检查报 `spawn python ENOENT` / “test runner error” = 已知假报错（runner 在 worktree 找不到
    python），忽略即可，以自己用绝对 venv 实测的结果为准
  - bash / edit / read 的路径按**当前 cwd 的 worktree** 解析：先 `cd .worktrees/<slug>` 或全程写绝对路径，
    别用相对路径跨 worktree 操作（实测多次把 edit 落进 master 公共区，还要 stash/pop 收拾）
- **worktree 收尾**：master 上 `git merge <branch> --no-ff -m "merge: <slug> (lane <lane-id>)"` →
  **立即在 master WT 重跑 `bash scripts/test.sh fast`，按 lane 代码改动面分类判定**：
  - **文档/config-only lane**（无 `tts_erp_v2/**` 或 `tests/**` 改动）：merge 前后 fail 集合差异全属
    pre-existing（master HEAD 既有 fx-test-isolation / test-prod-isolation / db test_time_fields_convention
    等正在修的 fail），lane 自身无代码风险 → push 通过。判定命令：
    `git diff <merge-base>..HEAD -- 'tts_erp_v2/**' 'tests/**' | wc -l` = 0
  - **代码/test lane**（改了 `tts_erp_v2/**` 或 `tests/**`）：merge 后 fail 集合对比合并前 master HEAD
    baseline（`bash scripts/test.sh fast 2>&1 | grep '^FAILED' | sort > /tmp/fail-{before,after}.txt`
    在 lane merge 前后各跑一次，`diff` 对比）—— **新 fail = lane 引入，必修复才能 push**。"fail 来源"
    按 §12.4 隔离重跑协议：稳定 fail 进 §7 排查；flake 重跑通过
  - 历史背景：本规上版硬规则 "merge 后必须 0 fail" 在 master HEAD pre-existing fail 存在下不可达
    （任何 lane merge 后都跑不到 0 fail）。本次细化按 lane 改动面分类，文档/config-only lane 不受
    pre-existing fail 阻塞；代码/test lane 仍必须 0 新 fail（merge 引入的冲突解错 / cherry-pick 漏
    依赖只有在这里才能兜住，lane 内 pre-merge 测试不够）
  → `git worktree remove .worktrees/<slug>` + **`git log --oneline master..<branch>` 预检必须为空**
  （否则 lane commit 未完全 merge，-D 会丢 commit）→ `git branch -D <branch>` + `git worktree prune`
  → 确认 `git worktree list` 无残留 → push。禁止 `git add -A && git commit` 冒充 merge；禁止
  "先合了再说、worktree 留到周末清"
- **lane 冲突处理**：
  - **派活时先声明文件所有权**：并行的 lane 尽量不碰同一文件；仓库里最容易被多 lane 同改的共享点 =
    `sync_worker/scheduler.py`、`tests/conftest.py`、`tts_erp_v2/db/models/`、schema SQL / `regen_schema.py`、
    `restart.sh`。父 agent 派活时若两个 lane 都要动同一文件，先约定谁改（或拆成不重叠的改动面）
  - **冲突时先别删 worktree**：收尾流程的 `git worktree remove` 只在 merge 成功之后做。merge 报冲突 =
    lane 分支落后于 master → 在 lane worktree 内先 `git rebase master`（lane 是私有分支，rebase 比 merge 干净），
    逐个冲突文件解：保留**双方意图**（先看两边改了什么再合，不要图快选一边）；解完在 lane worktree 跑
    `bash scripts/test.sh fast` 必须 0 fail，再回 master `git merge --no-ff`
  - **禁止一刀切**：不得用 `git checkout --theirs/--ours` 或全局 `-X theirs` 静默丢弃任何一方改动——
    lane 是别人未审的代码，丢了一方等于丢整条 lane 的工作（08-31 教训同源）
  - **语义冲突靠全量测试兜底**：两个 lane 改同一模块的不同函数时 git 可能不报 conflict，但运行时互相踩——
    因此同文件或同模块的多 lane 合并后，`scripts/test.sh fast` 0 fail 是硬门槛，不能只跑自己 lane 的域测试
  - **解不了就重排**：`git merge --abort` 恢复原状，换 merge 顺序（先合依赖方 / 改动面小的），或找 lane owner
    重开一个干净分支重做冲突部分；禁止硬解出一个能过测试但行为错的合并
- **master 改动完成必须 push**：测试 0 fail、文档已更新、工作区干净后 `git push origin master`，不留
  unpushed state。严禁 `--force`；push 非 fast-forward 时先 `git fetch` + rebase 或
  `git merge --no-ff origin/master`，解冲突再 push。半成品 / WIP / draft commit 不得留在 master 不推
- commit message 带类型前缀（`feat/fix/chore/docs/style/merge`）+ 中文描述；merge 消息格式见上

## 12. WIP 归属与交接（防“无主 WIP”复现——2026-09-06 fx 教训）

### 12.1 固定交接目录 handoff/（先注册再动工）

- `handoff/ACTIVE.md` = 机器可读的**在途工作注册表**，是“当前谁拥有 master 未提交改动 /
  分支”的唯一 truth source。在 master 工作区开新工作前**必先更新 ACTIVE.md**（或确认已有
  注册覆盖你要动的文件面），再动第一行代码：

  ```markdown
  | lane_id | 主题 | owner(session) | branch/worktree | 拥有的文件/目录 | 状态 | updated(UTC) |
  ```

  状态机：draft → done(待合) → merged(删行) / abandoned(标日期+原因)。收尾 / 换手 /
  放弃都必须改表；merge 后删行。根目录 `handoff.md` 保留为**历史**交接（收尾追加 TL;DR），
  其头部放一行指针指向 ACTIVE.md（2026-09-06 起）。
- watchdog（可选增强）：扫描 master WT 中 untracked / 未提交文件的存在时长，超阈值记
  `logs/watchdog.log` 提示"登记归属或开分支"。
- **ACTIVE.md 单写者规则**：master 上同一时刻只允许一个会话 / agent 持有 ACTIVE.md 写权（写入 = 增行 /
  改状态 / 删行）。多个 lane 并发改 ACTIVE.md 必须串行——否则会撞内容或丢行。实操：
  - 写前 `git diff handoff/ACTIVE.md` 确认无他人 in-flight 改动；有 → 等合并 / 接手
  - 写后立即 `git add handoff/ACTIVE.md`（不依赖 commit 时机，丢 staged 也比丢 unstaged 安全）
  - 高频编辑考虑加 `flock /tmp/active-md.lock` 防并发（按需启用，单 lane 不必）

### 12.2 master 工作区纪律（fx 根因①：成块 WIP 裸奔）

- ❌ 成块新功能（>1 文件 / 预计跨多步）禁止以 untracked / 未提交状态长期躺在 master。
  两条路二选一：立即开 worktree 分支（§11），或每完成一个原子单元立即 commit。
- 未完成的改动 = 分支上的 commit 或 ACTIVE.md 里的一行，**不允许裸奔**。

### 12.3 接手“无主”改动的强制协议（fx 根因②③）

1. 读 `handoff/ACTIVE.md`：有注册者 → 找它收尾；无 → 继续。
2. `git fetch` 后对比 `origin/master`：**HEAD 是否在动 / 最近谁在提交**——“看起来无主”
   常常是别人正提交到一半（本次 fx 就是：我 worktree 开发期间原 lane 自己冒出来提交了
   同源内容，merge 时 add/add 冲突）。
3. 先快照保全（`git diff > /tmp/<topic>_wip.patch` + untracked 打包）再动。
4. **合回前必做去重检查**：`git fetch && git diff --stat <分支基址> <新 master>`；
   发现同源内容已被提交 → 丢弃冗余分支/提交，只保留真实增量（bug 修复/文档）再 merge。
5. 清 master 上被接手文件的 WIP 前，快照必须已留存（§6 “先问不清”不变）。
6. **接手后 ACTIVE.md 必须更新**：原 owner 那行改 `abandoned (<日期>，接手给 <新 owner>)` + 新增一行
   `接手: <新 lane_id> from <原 owner> at <UTC>`。否则下一个人接手时不知道当前状态是谁的。

### 12.4 并发测试互清（已知坑）

- 共享 dev DB：两个会话同时跑 `scripts/test.sh fast` 会互清 TEST_api_keys / 哨兵行 →
  大规模 401 / error 假失败。需全量跑时**串行执行**，任选一种：
  - **file lock**：`flock -n /tmp/tts-erp-test.lock bash scripts/test.sh fast`（锁被占则立即失败，不互等）
  - **轮询等前 run 完**：`while pgrep -f 'scripts/test.sh\|pytest tests/' >/dev/null; do sleep 5; done;
    bash scripts/test.sh fast`
  - **分 ephemeral DB**：lane A 用 `TTS_ERP_DB_URL=postgresql://...test_a`，lane B 用 `test_b`，
    互不影响（最稳，但需独立 DB 实例）
- 失败先挑 FAILED/ERROR **隔离重跑一次**（只跑那几条 + 它们的依赖），全绿即 flake 不是真失败；
  隔离重跑仍 fail 才算真失败，进 §7 排查。
