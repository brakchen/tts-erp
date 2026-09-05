# tts-erp

> TikTok Shop 销售 + 妙手采购 数据整合分析系统 · 10-schema PostgreSQL + FastAPI/uvicorn (端口 9877) + APScheduler 同步

## 它是干什么的

把 [TikTok Shop Partner API](https://partner.tiktokglobalshop.com/docv2/page/order-api-overview)（销售端：订单/商品/物流/对账/售后）+ [妙手开放平台](https://apifox.com/apidoc) apifox fd54e57e-9b98-4c34-bada-306221c39e68（采购端：店铺/采集箱/搬家任务/采购单）整合到同一个 PostgreSQL 库里，提供：

- **销售端 v2 API**：TikTok 订单/商品/物流/对账/售后的**结构化**读（不再穿透 raw jsonb；财务费用拆成 `finance.settlement_components` 行，只落非零组件）
- **采购端数据**：妙手店铺/采集箱/搬家任务/采购单已迁移入库 `procurement.*`（**暂无** `/v2/procurement/*` 读端点，直接查库）
- **联动 API**：`/v2/linkage/*` 读 product_links / link_evidence / link_overrides；DB 层另有 `linkage.effective_product_links` view（override 优先的并集）把"妙手采集箱的商品 ↔ TikTok 渠道上的 SPU"关联起来 → 利润 = 售价 − 妙手采购价（**不带 1688 采集标价**，1688 标价不等于实际采购价，故意不进成本口径）
- **人工成本填写页** `/v2/pages/manual-costs`：无 1688 渠道价的在售 SPU 由运营手动补
- **报表**：`/v2/reporting/cost-snapshots`、`profit-daily`、`coverage`、`missing-cost-products`

业务代码 / 报表直接 `curl :9877/v2/...` 就行，**完全不用管** HMAC 签名 / access_token / shop_cipher / 翻页 — proxy 层全处理好。

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                tts-erp v2 (FastAPI :9877)                         │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────────┐  │
│  │  v2 API 层       │  │  proxy 层        │  │  sync-worker     │  │
│  │  /v2/commerce/*  │  │  (tiktok /        │  │  (APScheduler)   │  │
│  │  /v2/linkage/*   │  │   miaoshou SDK)  │  │                  │  │
│  │  /v2/reporting/* │  │                 │  │                  │  │
│  │  /v2/pages/*     │  │  共享 in-process │  │  6 TikTok jobs   │  │
│  └────────┬─────────┘  └─────────┬────────┘  │  + token.refresh │  │
│           │                      │           │  + 3 妙手 jobs   │  │
│           │                      │           │  + reporting.*   │  │
│           │                      │           │  + analytics.    │  │
│           │                      │           │    retention     │  │
│           └──────────┬───────────┘                    │            │
│                      ▼                                ▼            │
│           ┌─────────────────────────────────────────────┐           │
│           │      PostgreSQL tts_erp (10 schemas)        │           │
│           │  integration / commerce / procurement       │
│           │  fulfillment / after_sales / finance        │
│           │  linkage / reporting / security             │
│           │  analytics                                  │           │
│           └─────────────────────────────────────────────┘           │
└──────────────────────────────────────────────────────────────────┘
                       │              │              │
                       ▼              ▼              ▼
              ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
              │ TikTok Shop   │ │ 妙手开放平台  │ │  oauth-       │
              │ Open API      │ │ openapi.     │ │ receiver     │
              │ (202309)      │ │ wanshifu.com │ │ :9876 (保留   │
              └──────────────┘ └──────────────┘ │  4 周回滚窗) │
                                                 └──────────────┘
```

> **oauth-receiver** 在 v2 中不再被 tts-erp 直接 HTTP 调用 — token 加解密由
> `tts_erp_v2/proxy/token_service.py` 通过 `oauth_receiver_core.py` **in-process** 完成。
> 端口 9876 还活着只是为了 4 周观察期内的紧急回滚（脚本见 `prod-switch/rollback.sh`）。

## 数据模型（10 schema / 60 表 + 1 view）

权威定义：[`tech-doc/data-model-target-v3.md`](tech-doc/data-model-target-v3.md)

| Schema | 域 | 代表表 |
| --- | --- | --- |
| `integration` | 集成 + 同步 | `credentials`, `raw_records`, `sync_jobs`, `sync_cursors`, `sync_issues` |
| `commerce` | TikTok 销售（订单/商品） | `shops`, `sales_orders`, `sales_order_lines`, `products_spu`, `products_sku` |
| `procurement` | 妙手采购 + 人工成本 + SPU 图 | `procurement_accounts`, `procurement_products`, `purchase_orders`, `manual_product_costs`, `spu_images` |
| `fulfillment` | 物流 | `shipments`, `shipment_lines`, `tracking_events` |
| `after_sales` | 退货/取消 | `cases`, `case_lines` |
| `finance` | 对账/打款 | `payouts`, `settlement_statements`, `settlement_transactions`, `settlement_components` |
| `linkage` | 销售↔采购关联 | `account_links`, `product_links`, `variant_links`, `link_evidence`, `link_overrides`, `link_issues` |
| `reporting` | 利润/成本快照 | `product_cost_snapshots`, `product_profit_daily`, `shipment_tracking_summary` |
| `security` | API key | `api_keys` |

外加 1 个 view：`linkage.effective_product_links`（product_links + link_overrides 的并集）

## 快速开始

### v2 端点（生产路径）

所有 v2 读端点查的是**本地 PG 新 schema**（不打上游 TikTok），过滤参数是**内部 id**
（`shop_pk` / `spu_pk`），不是 `shop_id` —— 传了也会被 FastAPI 静默忽略。
先用 external_account_id（即 TikTok shop_id）查出内部 id：

```bash
# 0. 店铺 → 内部 shop_pk（当前生产：7494763368967603447 → 314）
curl -H "Authorization: Bearer <key>" \
  "http://127.0.0.1:9877/v2/commerce/channel-accounts?platform=tiktok" | jq

# 销售端
curl -H "Authorization: Bearer <key>" \
  "http://127.0.0.1:9877/v2/commerce/sales-orders?shop_pk=314&limit=20" | jq

curl -H "Authorization: Bearer <key>" \
  "http://127.0.0.1:9877/v2/commerce/sales-orders/<内部订单 id>" | jq

# 联动：哪个 TikTok SPU 对应哪个妙手采集箱
curl -H "Authorization: Bearer <key>" \
  "http://127.0.0.1:9877/v2/linkage/product-links?limit=20" | jq

# 报表
curl -H "Authorization: Bearer <key>" \
  "http://127.0.0.1:9877/v2/reporting/profit-daily?limit=20" | jq

curl -H "Authorization: Bearer <key>" \
  "http://127.0.0.1:9877/v2/reporting/missing-cost-products" | jq  # 无采购价的在售 SPU 清单（去人工补填）

# 人工成本填写页（浏览器打开；未登录会被 302 引导到 /v2/auth/login）
open "http://127.0.0.1:9877/v2/pages/manual-costs"

# Admin：查看 / 热重载限流（看 [限流与热重载](#限流与热重载) 详细）
curl -H "Authorization: Bearer <admin_key>" \
  "http://127.0.0.1:9877/v2/admin/rate-limit" | jq

# Admin：重读 TTS_ERP_RATE_LIMIT_PER_MIN 环境变量（不传 new_limit 即可）
curl -X POST -H "Authorization: Bearer <admin_key>" \
  -H "Content-Type: application/json" -d '{}' \
  "http://127.0.0.1:9877/v2/admin/reset-rate-limit" | jq
```

完整端点列表见 [`tech-doc/external-api.md`](tech-doc/external-api.md) 或 `GET /endpoints`。

> **legacy 端点 `/orders/*`, `/finance/*`, `/db/*`, `/sync/*`, `/miaoshou/*` 在 v2 已删除**。
> v1 数据保留在 `public.*`（4 周观察期内可回滚，过期后归档 — 见 `prod-switch/observe-archive.sh`）。

## 同步（sync-worker，APScheduler）

sync-worker 是独立 systemd 单元（`tts-erp-sync.service`），与 api 平级直连 PG。调度表以
`tts_erp_v2/sync_worker/scheduler.py` 的 `JOBS` registry 为准，当前注册的 jobs：

| Job | 来源 | 频率 |
| --- | --- | --- |
| `tiktok.orders` | /orders/search | 每 10 min（按 update_time 增量） |
| `tiktok.order_detail` | /order/202309/orders | 每 30 min（补单 gap-filler） |
| `tiktok.products` | /products/search | 每 10 min（2026-09-05 由 6h 改 —— 逐 SPU Get Product 补主图后需更快同步） |
| `tiktok.after_sales` | /returns/search + /cancellations/search | 每 15 min |
| `tiktok.finance` | /finance/payouts + /finance/statements | 每 1h |
| `tiktok.logistics` | /logistics/orders/{id}/tracking | 每 10 min（活跃运单） |
| `token.refresh` | 按 `integration.credentials.expires_at` 提前续期 | 每 6h |
| `miaoshou.shops` | /shop/list | 每 6h（妙手店铺列表） |
| `miaoshou.collect_box` | /collectBox/list | 每 30 min（采集箱 = 联动证据源） |
| `miaoshou.move_collect` | /moveCollect/list | 每 30 min（搬家任务） |
| `reporting.cost_snapshots` | `tts_erp_v2/jobs/reporting.py` | 每 6h（成本输入变化慢） |
| `reporting.profit_daily` | `tts_erp_v2/jobs/reporting.py` | 每 1h（重建当日+昨日 UTC） |
| `analytics.retention` | ad_audit_log / ad_raw TTL | 每 1 d（日级，2026-09-02 上线） |

**未接入调度的 job**（代码在库、未注册进 `JOBS`）：

- `miaoshou.purchase_orders`（`tts_erp_v2/jobs/miaoshou/purchase_orders.py`）：
  scheduler.py 顶部 `NOTE(2026-09-01)` 标注 endpoint 路径 404（routeNotFound），
  v2 实现从 apifox 文档写就但未做线上实拍验证，**有意不注册**直到正确路径从
  apifox doc（fd54e57e…）确认后再加入。
- link-compute（`tts_erp_v2/linkage/compute.py`）：只有库函数无触发方，
  `/v2/linkage/*` 读端点读的是历史 link 表，刷新靠定期的人工 override 或
  `product_links` upsert 路径。

每次 run 写一行 `integration.sync_jobs`（rows_total/inserted/failed + status）。失败写 `integration.sync_issues`，**job 不会卡死**，下一个调度继续跑。

## 成本口径（重要）

利润 = 售价 − **MANUAL_ENTRY**（人工填写，**最高优先级**）> 妙手采购单 > 1688 采集标价**禁用**。

为什么不用 1688 采集标价：1688 标价是妙手采集时看到的**初始报价**，实际采购价往往不同（量大价、议价）。把它当成本会污染利润口径。无 1688 价的在售 SPU → `/v2/reporting/missing-cost-products` → 运营手动补。

详细决策见 [`tech-doc/refactor-tech-plan-v2.md`](tech-doc/refactor-tech-plan-v2.md) §6 决策 10/12。

## 鉴权

除豁免路径外所有端点需要 `Authorization: Bearer <key>` 或 `X-API-Key: <key>`。
豁免：`/healthz`、`/endpoints`、`/openapi.json`、`/docs`、`/redoc`、`/v2/auth/{login,logout,me}`。

浏览器也可以拿 API key 在 `/v2/auth/login` 换 HMAC 签名会话 cookie（`tts_session`，HttpOnly，
12h），之后页面导航和 fetch 自动带 cookie —— 人工成本页走的就是这条流（设计见
[`tech-doc/browser-login-design.md`](tech-doc/browser-login-design.md)）。cookie 会话下的
POST/DELETE 必须带 `X-Requested-With: tts-erp` 头（CSRF guard）。

三级角色（`readonly < readwrite < admin`，分类逻辑在 `tts_erp_v2/middleware/auth.py::required_role`，
部分写端点在 handler 内再校验）：

- `readonly`：所有 v2 GET + `/static/*`
- `readwrite`：+ `POST /v2/reporting/manual-costs`、`POST /v2/spu-images/upload-url`、`POST /v2/spu-images/{id}/confirm`、`DELETE /v2/spu-images/{id}`、`/v2/analytics/sync/*`
- `admin`：`POST /v2/linkage/overrides`（覆盖 product_links，handler 内校验 admin）、`POST /v2/admin/reset-rate-limit`（热重载限流）；未匹配路径默认按 admin 拦截

key 管理：`python3 api_keys.py create --role <role> --name <name>`（另有 `list` / `revoke --prefix` / `rotate --prefix`）。库里只存 SHA-256 哈希，完整 key 创建时打印一次。

`/healthz` body 包含 `service: "tts-erp-v2"` + `auth_mode`（当前 enforce；v1 返回纯 `{"status":"ok"}`），smoke 测试用此判断 v2 是否真在跑。

## 本地数据

- **一个库**：`tts_erp`（docker 容器 `postgres`，5432）
- **10 schema**：`integration` / `commerce` / `procurement` / `fulfillment` / `after_sales` / `finance` / `linkage` / `reporting` / `security` / `analytics`
- **60 张表 + 1 view**：v2 schema 40 张 + 19 张 `public.*` legacy（4 周观察期内可回滚，过期后归档）+ 1 张 `public.alembic_version`；view = `linkage.effective_product_links`。见 [`tech-doc/data-model-target-v3.md`](tech-doc/data-model-target-v3.md)
- **Alembic 迁移**：`alembic/versions/20260829_init_nine_schemas.py`（初始 10 schema；文件名仍含 `nine` 是历史命名，`upgrade head` 会按 mtimes 应用），`alembic upgrade head` 应用
- **旧 public.* 保留** 4 周观察期后归档 — 见 `prod-switch/observe-archive.sh`

## 安装 / 部署

```bash
# 一次性
cd /home/schan/tts-erp
python3 -m venv .venv && .venv/bin/pip install -e .
# .env 无模板文件 —— 参照生产 .env 手写（DB URL + TTS_ERP_FERNET_KEY 等，0600）

# 应用 schema（幂等）
.venv/bin/alembic upgrade head

# 启动（systemd --user 托管，开机自启；详见 prod-switch/）
systemctl --user start tts-erp.service         # v2 API（监听 :9877）
systemctl --user start tts-erp-sync.service    # v2 sync-worker
```

## 生产切换

6 个独立脚本，每个都可单独执行 / 单独回滚（见 [`prod-switch/`](prod-switch/)）：

```bash
bash prod-switch/preflight.sh              # 6 项硬性自检
bash prod-switch/install-sync-worker.sh     # 一次性：装 systemd 单元
bash prod-switch/switch-to-v2.sh            # 切到 v2（自动 rollback 失败）
bash prod-switch/postswitch-smoke.sh        # 7 项冒烟（healthz/auth/角色/CORS/manual-costs/v2 端点/PG 连接数）
bash prod-switch/observe-archive.sh         # 4 周后：停 :9876 + 改 public.* 名为 _deprecated_*
bash prod-switch/rollback.sh                # 紧急：回到 v1 旧栈
```

## 开发方式：TDD

```bash
cd /home/schan/tts-erp
scripts/test.sh fast                # 日常全量（排除 slow + requires_service）
scripts/test.sh commerce            # 按业务域跑单切片（详见 tech-doc/test-domains.md）
.venv/bin/pytest tests/ -p no:warnings   # 直接跑 v2 套件（addopts 默认排除 domain_migration）
```

约定（`tests/conftest.py`）：

- **事务回滚隔离**：DB 测试每个用例跑在外层事务里，结束即 rollback，可安全对生产库跑
- **`TEST_%` 哨兵**：落库提交的数据（如 `_seed_channel_product`），`shop_id`/`txn_id` 一律 `TEST_` 前缀
- **Drift-tolerant 断言**：迁移测试直接对生产库跑，源表行数以 runtime 查询为准，不硬编码

## 进程管理

```bash
# 状态
systemctl --user status tts-erp.service
systemctl --user status tts-erp-sync.service

# 重启
systemctl --user restart tts-erp.service
systemctl --user restart tts-erp-sync.service

# 日志
journalctl --user -u tts-erp.service -n 50
journalctl --user -u tts-erp-sync.service -n 50
```

## 调试

```bash
# 健康检查
curl http://127.0.0.1:9877/healthz | jq
# {"status":"ok","service":"tts-erp-v2","auth_mode":"enforce"}

# 看 sync-worker 最近一次运行的同步结果
PGPASSWORD=... psql -U postgres -d tts_erp -c \
  "SELECT job_name, status, rows_inserted, started_at FROM integration.sync_jobs ORDER BY id DESC LIMIT 20"

# 看同步 issue（某次跑失败的 row）
PGPASSWORD=... psql -U postgres -d tts_erp -c \
  "SELECT job_name, issue_type, external_id, detected_at FROM integration.sync_issues ORDER BY detected_at DESC LIMIT 20"

# 妙手签名调试
MIAOSHOU_DEBUG_SIGN=1 .venv/bin/python -c "from miaoshou.miaoshou_signing import build_sign; import json; print(build_sign({'shopId':'17060852'}, 'TEST_COMPANY_SECRET'))"
```

## 已知问题 / 边界

- **`/miaoshou/callback/*` 在 v2 已无路由（实测 404）**：回调派发代码仍在 `miaoshou/callbacks/`，
  auth 中间件也仍把该前缀分类为公开路径，但 v2 app 从未挂载回调 router。妙手若仍在推送，
  这些 webhook 实际已无人接收；要恢复需把 router 挂回 v2 app 并单独 review。
- **回滚 4 周后撤 oauth-receiver**：v2 不依赖 :9876，但 rollback 脚本需要它在 4 周内保持运行。
- **`/returns/*` 和 `/cancellations/*` 不接 CREATE 写端点**：避免在真实店铺创建退货/取消单。详见 `AGENTS.md` §4。
- **`/reverse/202309/*` 不存在**：TikTok 202309 spec 没开放 reverse logistics 模块（HTTP 404 at CDN）。

## 相关

- [`AGENTS.md`](AGENTS.md) — AI agent 操作指南（端点速查、签名规范、DO/DON'T）
- [`CHANGELOG.md`](CHANGELOG.md) — 变更历史（按日期）
- [`handoff.md`](handoff.md) — 跨 session 交接笔记
- [`tech-doc/data-model-target-v3.md`](tech-doc/data-model-target-v3.md) — 10 schema V3 真理源
- [`tech-doc/external-api.md`](tech-doc/external-api.md) — v2 端点契约
- [`tech-doc/api-key-auth-design.md`](tech-doc/api-key-auth-design.md) — auth 设计
- [`tech-doc/refactor-tech-plan-v2.md`](tech-doc/refactor-tech-plan-v2.md) — 重构技术方案 V2（已实施）
- [`tech-doc/test-domains.md`](tech-doc/test-domains.md) — 测试按域切片 / scripts/test.sh 用法
- [`tech-doc/_archive/`](tech-doc/_archive/) — V1 时代过期文档
- [`miaoshou/README.md`](miaoshou/README.md) — 妙手集成
- [`prod-switch/`](prod-switch/) — 生产切换 / 回滚 / 归档脚本

## License

Internal use.
