# tts-erp

面向 TikTok Shop 本地分析的 ERP 数据服务。系统把 TikTok Shop、妙手采购、Chrome
扩展采集和汇率快照写入 PostgreSQL，通过 FastAPI 提供读侧 API、运营页面和 SPU 盈利分析，
并由独立 APScheduler worker 持续同步数据。

> 当前主线是 **v2**。v1 `public.*` 业务表、旧 oauth-receiver 和旧 HTTP 路由已经退役。

## 系统能力

- **本地分析库**：订单、商品、物流、售后、结算、采购、广告、汇率和联动关系落入多 schema PostgreSQL。
- **定时同步**：TikTok Shop、妙手、汇率、成本快照和图片镜像由独立 sync-worker 调度。
- **插件接入**：Chrome 扩展上传广告、订单域 dump 和拦截记录；服务端负责协议校验、幂等与健康记录。
- **读侧 API**：销售、联动、报表、汇率、同步状态和分析结果以 `/v2/*` 暴露。
- **运营页面**：dashboard、店铺注册台、人工成本、SPU ROI、拦截管理和枚举映射页面。
- **SPU 盈利 v10**：在一致数据库快照中计算明细、大盘和证据；前端只展示结果，不重复计算。
- **统一访问策略**：API key、浏览器 cookie、角色、反向代理前缀和拒绝限流集中在 access 深模块。

系统是**读侧分析系统**：允许向本地分析库写入同步数据、配置和人工成本，但不提供修改真实
TikTok 店铺订单、退货、取消或发货状态的写端点。

## 总体架构

```text
TikTok Shop Open API ──> proxy/tts_shop ──> sync-worker jobs ──┐
妙手开放平台 ──────────> proxy/miaoshou ──> procurement jobs ─┤
ExchangeRate-API ──────> proxy/exchangerate ──> fx.sync ──────┤
Chrome extensions ─────> analytics/order/intercept ingest ────┤
                                                              ▼
                                                    PostgreSQL 多域 schema
                                                              │
                          ┌───────────────────────────────────┼────────────────────┐
                          ▼                                   ▼                    ▼
                 commerce/linkage API              deep analytics modules   operator pages
                                                     · SPU profitability
                                                     · order dump intake
                                                     · access policy
```

运行时由三个 systemd user 单元组成：

| 单元 | 责任 |
| --- | --- |
| `tts-erp.service` | FastAPI/uvicorn API，监听地址由 `TTS_ERP_HOST` / `TTS_ERP_PORT` 配置（当前端口 9877） |
| `tts-erp-sync.service` | APScheduler worker，运行 `scheduler.JOBS` 注册表 |
| `tts-erp-watchdog.timer` | 周期巡检同步状态 |

API 和 worker 共享代码与 PostgreSQL，但进程相互独立。修改 `jobs/` 或 `sync_worker/` 后必须单独
重启 worker。

## 核心数据流

### 1. 服务端同步

`tts_erp_v2/sync_worker/scheduler.py::JOBS` 是调度单一真相源。当前注册：

- TikTok：订单、订单详情、商品、物流、售后、财务；
- 凭证：`token.refresh`；
- 汇率：`fx.sync`，按上游 `next_update_at` 跳过无效请求；
- 妙手：店铺、采集箱、搬家任务、公共采集箱、货源成本回填；
- 报表：成本快照、旧版利润日报；
- 媒体：SPU 图片镜像。

TikTok job 按已授权店铺扇出；进入 job runner 的每店运行状态记录到
`integration.sync_jobs`。如果在 runner 前构建 proxy 失败，只写 worker 日志。可归因的业务/数据问题
由具体 job 另写 `integration.sync_issues`。单店失败会重试一次，不阻断其他店铺或后续 tick。

### 2. Chrome 插件接入

| 接口族 | 数据 | 存储/实现 |
| --- | --- | --- |
| `/v2/analytics/sync/*` | 广告 dump、coverage、plugin logs | `plugin.ad_*`，`tts_erp_v2/plugin/ads/` |
| `/v2/order-sync/*` | 订单、详情、历史、物流、结算、售后 | `plugin.*`，`tts_erp_v2/plugin/orders/intake/` |
| `/v2/intercept/*` | 拦截配置、请求记录、浏览器会话 | `plugin.intercept_*` |

订单 dump 的公开 seam 是 `intake_dump(session, *, request: DumpIntakeRequest) -> DumpIntakeOutcome`。module 内部拥有
六域 dispatch、payload 解释、savepoint、业务落库、health 记录和 commit/rollback 顺序；HTTP
adapter 只处理 wire schema 和响应 envelope。

协议详情见 [`tech-doc/dumps-data-contract.md`](tech-doc/dumps-data-contract.md) 和
[`tech-doc/order-dump-intake-module.md`](tech-doc/order-dump-intake-module.md)。

### 3. SPU 盈利分析

`tts_erp_v2.analytics.spu_profitability` 是“SPU 盈利”的唯一权威 module：

```python
read_overview(session, *, scope, view) -> ProfitabilityOverview
explain_spu(session, *, scope, spu_pk, evidence) -> SpuProfitExplanation
```

关键约束：

- 业务口径以 [`biz-doc/analytics/spu-roi-profit-calculation.md`](biz-doc/analytics/spu-roi-profit-calculation.md) v10 为准；
- 同一请求的明细、大盘、证据和汇率使用同一个只读一致快照；
- 汇率只读数据库快照，缺失时整页返回 `503 FX_RATE_UNAVAILABLE`；
- 成本优先级为 **人工价 > 妙手货源价 > 40 CNY/件兜底**；
- 前端不得重算净收入、COGS、净利润或 ROI；
- `reporting.product_profit_daily` 是旧版粗略毛利快照，不是 SPU 盈利真相源。

详细决策见
[`tech-doc/analytics/spu-profitability-module-decisions.md`](tech-doc/analytics/spu-profitability-module-decisions.md)。

## 主要 module 与 interface

| Module | Interface / 责任 |
| --- | --- |
| `tts_erp_v2/access/` | `canonicalize_path()`、`evaluate_access()`；部署路径、角色、凭证、模式与 typed decision |
| `tts_erp_v2/plugin/orders/intake/` | `intake_dump()`；订单域 dump 解释、原子落库和 health outcome |
| `tts_erp_v2/analytics/spu_profitability/` | `read_overview()`、`explain_spu()`；v10 盈利、证据和一致快照 |
| `tts_erp_v2/proxy/token_service.py` | 凭证加解密、加载、写入和续期的唯一实现 |
| `tts_erp_v2/proxy/tts_shop/` | TikTok HMAC 签名、分页、shop cipher、token refresh 与 read-through client |
| `tts_erp_v2/sync_worker/` | job 注册、店铺扇出、执行记录和调度 |

架构设计见：

- [`tech-doc/access-policy-module.md`](tech-doc/access-policy-module.md)
- [`tech-doc/access-policy-implementation-review.md`](tech-doc/access-policy-implementation-review.md)
- [`tech-doc/order-dump-intake-module.md`](tech-doc/order-dump-intake-module.md)
- [`tech-doc/architecture-overview.md`](tech-doc/architecture-overview.md)

## HTTP 接口

所有稳定业务接口使用 `/v2`。完整活契约见
[`tech-doc/external-api.md`](tech-doc/external-api.md)，运行实例可查询 `GET /endpoints`。

| 前缀 | 用途 |
| --- | --- |
| `/v2/commerce/*` | 店铺、SPU/SKU、销售订单和履约读模型 |
| `/v2/linkage/*` | 销售商品与妙手采购商品的关联、证据、问题和 override |
| `/v2/reporting/*` | 成本快照、旧版利润日报、覆盖率、缺成本商品和人工成本 |
| `/v2/analytics/spu-roi*` | v10 SPU 盈利主表与订单/结算/售后/广告证据 |
| `/v2/fx/*` | 数据库汇率快照与本地换算 |
| `/v2/sync/status` | 调度状态、延迟周期和严重级别 |
| `/v2/pages/*` | 运营页面 |
| `/v2/auth/*` | 浏览器登录、退出和当前会话 |
| `/v2/oauth/tiktok/*` | 新店授权控制台与 OAuth；`onboard` 为 readonly、`authorize` 为 readwrite，`callback` 为公开回调并执行上游 token exchange |
| `/v2/admin/*` | 本地管理操作；高风险操作同时受 destructive guard 保护 |
| `/v2/tiktok-shop/*` | **例外：实时 read-through TikTok Partner API**，不走本地缓存 |

除 `/v2/tiktok-shop/*` 明确标注的 read-through 接口外，业务读接口默认读取本地 PostgreSQL，
不会在请求链路中临时访问上游。

### 内部主键

多数 v2 过滤器使用内部 `shop_pk` / `spu_pk`，不是外部 `shop_id`。先查内部主键：

```bash
export TTS_ERP_KEY='<readonly-or-higher-key>'

curl -sS -H "Authorization: Bearer $TTS_ERP_KEY" \
  'http://127.0.0.1:9877/v2/commerce/channel-accounts?platform=tiktok' | jq

curl -sS -H "Authorization: Bearer $TTS_ERP_KEY" \
  'http://127.0.0.1:9877/v2/commerce/sales-orders?shop_pk=<shop_pk>&limit=20' | jq

curl -sS -H "Authorization: Bearer $TTS_ERP_KEY" \
  'http://127.0.0.1:9877/v2/analytics/spu-roi?shop_pk=<shop_pk>' | jq
```

## 数据域

SQLAlchemy models 位于 `tts_erp_v2/db/models/`。不要依赖 README 中易漂移的表数量；当前主要
schema 及责任如下：

| Schema | 责任 |
| --- | --- |
| `integration` | 凭证引用、同步游标、job 运行记录和问题 |
| `commerce` | 店铺、渠道商品、订单和订单行 |
| `procurement` | 妙手账号、采集箱、货源、人工成本和 SPU 图片 |
| `fulfillment` | 运单、包裹和轨迹 |
| `after_sales` | 退货、退款和取消 case |
| `finance` | payout、statement、transaction 和费用组件 |
| `linkage` | 销售与采购商品关联、证据、override 和问题 |
| `reporting` | 成本快照、旧版利润日报和跟踪汇总 |
| `plugin` | Chrome 广告/订单 dump、health log 和 intercept 数据 |
| `fx` | 汇率快照和 rate rows |
| `config` | 业务枚举翻译配置 |
| `security` | API key 哈希、角色和状态 |

时间统一存储为 aware UTC；报表日期按店铺本地时区解释。

## 鉴权与访问路径

生产使用 `TTS_ERP_AUTH_MODE=enforce`。非公开接口接受：

```http
Authorization: Bearer <key>
```

或：

```http
X-API-Key: <key>
```

优先级固定为：**有效 cookie > Bearer > X-API-Key**。角色顺序：

```text
readonly < readwrite < admin
```

未匹配路径默认要求 `admin`。浏览器可在 `/v2/auth/login` 用 API key 换取 HttpOnly HMAC
cookie；cookie role 不是授权真相，实际 role 每次通过数据库/cache 复查。cookie mutation 还必须
携带 `X-Requested-With: tts-erp`。

API-key 豁免路径为 `/healthz`、`/endpoints`、`/openapi.json`、`/docs`、`/redoc`、
`/docs/oauth2-redirect`、`/v2/auth/login`、`/v2/auth/logout`、`/v2/auth/me`、
`/v2/oauth/tiktok/callback` 和 `/static/*`。Docs 路径在配置
`TTS_ERP_DOCS_USER` / `TTS_ERP_DOCS_PASSWORD` 后仍受独立 Basic Auth 保护。

`TTS_ERP_EXTERNAL_PREFIX` 是外部部署前缀的单一真相源，当前生产值为 `/tts`。
本机直连使用 `http://127.0.0.1:9877/v2/...`；当前公网使用
`http://daqiang.nat100.top/tts/v2/...`，不带端口且不能省略 `/tts`。
access module 同时兼容代理保留或剥离前缀的请求，并让 Auth 与 Docs Basic Auth
使用相同 route-relative path。

更多细节：

- [`tech-doc/external-api.md`](tech-doc/external-api.md#authentication)
- [`tech-doc/browser-login-design.md`](tech-doc/browser-login-design.md)
- [`tech-doc/access-policy-module.md`](tech-doc/access-policy-module.md)

## 本地环境与启动

运行环境：Python 3.14（包声明支持 Python 3.13+）、PostgreSQL、MinIO 和 systemd user units。

```bash
cd /home/schan/tts-erp
python3.14 -m venv .venv
.venv/bin/pip install -e .
```

环境变量保存在本地 `.env`，权限必须为 `0600`。仓库不提供含凭证的模板；从运维人员处取得
所需配置，不要提交数据库 URL、Fernet key、API key、cookie、token 或 MinIO 凭证。

推荐通过 systemd 启动：

```bash
systemctl --user start tts-erp.service
systemctl --user start tts-erp-sync.service

curl -sS http://127.0.0.1:9877/healthz | jq
# {"status":"ok","service":"tts-erp-v2","auth_mode":"enforce"}
```

完整部署说明见 [`setup/tts-erp.md`](setup/tts-erp.md)。数据库 migration 的生产执行由人工运维
完成；agent 只能在 `tts_erp_v3_test` 验证 migration。

## 测试

测试的唯一入口是 `scripts/test.sh`。运行前必须准备 gitignored 的 `.env.test`，
其中 `TTS_ERP_DB_URL`（或显式设置的 `TTS_ERP_DB_URL_TEST`）必须指向专用数据库
`tts_erp_v3_test`。脚本仅在该文件存在且未直接设置 `TTS_ERP_DB_URL_TEST` 时加载它；
`tests/conftest.py` 会拒绝已识别的 production-shaped 数据库。

```bash
# 日常快速套件
flock -n /tmp/tts-erp-test.lock bash scripts/test.sh fast

# 会访问共享测试库的单域套件也必须加锁
flock -n /tmp/tts-erp-test.lock bash scripts/test.sh api
flock -n /tmp/tts-erp-test.lock bash scripts/test.sh commerce
flock -n /tmp/tts-erp-test.lock bash scripts/test.sh reporting

# 纯 unit layer
bash scripts/test.sh unit
```

安全规则：

- 不直接运行 `pytest`；
- 不让测试连接 `tts_erp`、`tts_erp_prod` 或其他 production-shaped 数据库；
- 不设置 `TTS_ERP_TEST_OFF=1` 绕过隔离；
- agent 不运行 `scripts/test.sh all`、`coverage` 或归档 migration suite；
- 测试数据使用 `TEST_` 前缀；
- 共享测试库运行通过 `/tmp/tts-erp-test.lock` 串行化。

详见 [`tech-doc/agent-testing.md`](tech-doc/agent-testing.md)。

## 运维

```bash
# API
bash restart.sh
systemctl --user status tts-erp.service
journalctl --user -u tts-erp -n 50

# sync-worker；修改 jobs/ 或 sync_worker/ 后单独重启
systemctl --user restart tts-erp-sync.service
systemctl --user status tts-erp-sync.service

# 当前调度状态
curl -sS -H "Authorization: Bearer $TTS_ERP_KEY" \
  http://127.0.0.1:9877/v2/sync/status | jq
```

API 日志由 access log middleware 记录最终状态、耗时、认证方式和代理信息。sync-worker 测试日志
写入测试专用文件，不应污染生产 `logs/sync_worker.log`。

## 仓库结构

```text
tts_erp_v2/
├── access/                  # deployment path + access policy 深模块
├── analytics/
│   └── spu_profitability/   # v10 盈利 module
├── api/v2/                  # FastAPI adapters 和稳定 URL
├── db/models/               # SQLAlchemy 多 schema models
├── jobs/                    # TikTok / 妙手 / 汇率 / 报表 jobs
├── middleware/              # ASGI auth/rate-limit/access-log adapters
├── plugin/
│   ├── ads/                 # Chrome 广告 dump persistence
│   └── orders/intake/       # 订单域 dump 深模块
├── proxy/                   # TikTok / 妙手 / 汇率 clients；token_service
├── reporting/               # 成本和旧版报表实现
├── static/                  # 运营页面 CSS/JS/vendor
└── sync_worker/             # APScheduler registry 与 runner

tests/                       # 按业务域和 layer 标记的测试
scripts/                     # 运维、探针、一次性和测试入口
tech-doc/                    # 技术契约、ADR、架构和运维文档
biz-doc/                     # 业务口径
setup/                       # 人工部署文档
handoff/ACTIVE.md            # 当前 lane 文件所有权
```

## 重要边界

- 不存在 store-writing TikTok HTTP 接口；不要新增确认、取消、退货或发货写操作。
- `/miaoshou/*` 没有 v2 HTTP surface；妙手是进程内 SDK + scheduled jobs。
- `miaoshou.purchase_orders` 代码存在，但当前路径在生产妙手 ERP API 返回 `routeNotFound`；正确路径尚未确认，因此没有注册进 scheduler。
- v1 `/orders/*`、`/finance/*`、`/db/*`、`/sync/*` 等路由已删除。
- TikTok signing 必须保持 `shop_cipher` query、排序签名键和原始 JSON body 语义；见
  [`tech-doc/tiktok-hmac-signing.md`](tech-doc/tiktok-hmac-signing.md)。
- 凭证只能通过 `tts_erp_v2.proxy.token_service`，禁止直接解密 `integration.credentials`。
- destructive HTTP/CLI/migration/job 必须使用 `tts_erp_v2.api.deps` 的对应共享 guard。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [`AGENTS.md`](AGENTS.md) | 仓库安全边界、命令和 agent 工作流 |
| [`tech-doc/architecture-overview.md`](tech-doc/architecture-overview.md) | 系统、数据和凭证架构 |
| [`tech-doc/process-architecture.md`](tech-doc/process-architecture.md) | 进程与目录地图 |
| [`tech-doc/external-api.md`](tech-doc/external-api.md) | 外部 API 活契约与角色矩阵 |
| [`tech-doc/dumps-data-contract.md`](tech-doc/dumps-data-contract.md) | Chrome dump wire/HTTP 契约 |
| [`tech-doc/access-policy-module.md`](tech-doc/access-policy-module.md) | 访问策略深模块设计与实现 |
| [`tech-doc/order-dump-intake-module.md`](tech-doc/order-dump-intake-module.md) | 订单 dump intake 设计 |
| [`biz-doc/analytics/spu-roi-profit-calculation.md`](biz-doc/analytics/spu-roi-profit-calculation.md) | SPU 盈利 v10 业务公式 |
| [`tech-doc/fx-exchange-rates.md`](tech-doc/fx-exchange-rates.md) | 汇率快照与同步 |
| [`tech-doc/miaoshou-platform.md`](tech-doc/miaoshou-platform.md) | 妙手 SDK 与数据语义 |
| [`tech-doc/agent-safety.md`](tech-doc/agent-safety.md) | 数据库、凭证、生产与 destructive guard |
| [`CHANGELOG.md`](./CHANGELOG.md) | 历史变更 |

## License

Internal use.
