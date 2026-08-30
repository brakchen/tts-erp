# tts-erp

> TikTok Shop 销售 + 妙手采购 数据整合分析系统 · 9-schema PostgreSQL + FastAPI/uvicorn (端口 9877) + APScheduler 同步

## 它是干什么的

把 [TikTok Shop Partner API](https://partner.tiktokglobalshop.com/docv2/page/order-api-overview)（销售端：订单/商品/物流/对账/售后）+ [妙手开放平台](https://apifox.com/apidoc) apifox fd54e57e-9b98-4c34-bada-306221c39e68（采购端：店铺/采集箱/搬家任务/采购单）整合到同一个 PostgreSQL 库里，提供：

- **销售端 v2 API**：TikTok 订单/商品/物流/对账/售后的**结构化**读（不再穿透 raw jsonb，58 个金额字段独立成列）
- **采购端 v2 API**：妙手店铺/采集箱/搬家任务/采购单的**结构化**读
- **联动 API**：通过 product_links + link_evidence + effective_product_links view，把"妙手采集箱的商品 ↔ TikTok 渠道上的 SPU"关联起来 → 利润 = 售价 − 妙手采购价（**不带 1688 采集标价**，1688 标价不等于实际采购价，故意不进成本口径）
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
│  │  /v2/reporting/* │  │                 │  │  6 TikTok jobs   │  │
│  │  /v2/pages/*     │  │  共享 in-process │  │  4 miaoshou jobs │  │
│  └────────┬─────────┘  └─────────┬────────┘  │  1 token_refresh  │  │
│           │                      │           └────────┬─────────┘  │
│           └──────────┬───────────┘                    │            │
│                      ▼                                ▼            │
│           ┌─────────────────────────────────────────────┐           │
│           │      PostgreSQL tts_erp (9 schemas)         │           │
│           │  integration / commerce / procurement       │           │
│           │  fulfillment / after_sales / finance         │           │
│           │  linkage / reporting / security             │           │
│           │  (35 张表 + 1 view)                         │           │
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

## 数据模型（9 schema / 35 表 + 1 view）

权威定义：[`tech-doc/data-model-target-v3.md`](tech-doc/data-model-target-v3.md)

| Schema | 域 | 代表表 |
| --- | --- | --- |
| `integration` | 集成 + 同步 | `credentials`, `channel_accounts`, `raw_records`, `sync_jobs`, `sync_issues` |
| `commerce` | TikTok 销售（订单/商品） | `sales_orders`, `sales_order_lines`, `channel_products`, `channel_product_variants` |
| `procurement` | 妙手采购 + 人工成本 | `procurement_accounts`, `procurement_products`, `manual_product_costs` |
| `fulfillment` | 物流 | `shipments`, `tracking_events` |
| `after_sales` | 退货/取消 | `cases`, `case_lines` |
| `finance` | 对账/打款 | `payouts`, `settlement_statements`, `settlement_transactions`, `settlement_components` |
| `linkage` | 销售↔采购关联 | `product_links`, `link_evidence`, `link_overrides`, `link_issues` |
| `reporting` | 利润/成本快照 | `cost_snapshots`, `profit_daily` |
| `security` | API key | `api_keys` |

外加 1 个 view：`linkage.effective_product_links`（product_links + link_overrides 的并集）

## 快速开始

### v2 端点（生产路径）

所有 v2 端点都要求 `?shop_id=XXX` 或 `?channel_account_id=XXX`，内部自动找 token + cipher：

```bash
# 销售端
curl -H "Authorization: Bearer <key>" \
  "http://127.0.0.1:9877/v2/commerce/sales-orders?shop_id=7494763368967603447&limit=20" | jq

curl -H "Authorization: Bearer <key>" \
  "http://127.0.0.1:9877/v2/commerce/sales-orders/5800123456789012345?shop_id=7494763368967603447" | jq

# 采购端
curl -H "Authorization: Bearer <key>" \
  "http://127.0.0.1:9877/v2/linkage/product-links?shop_id=7494763368967603447" | jq

# 联动：哪个 TikTok SPU 对应哪个妙手采集箱
curl -H "Authorization: Bearer <key>" \
  "http://127.0.0.1:9877/v2/linkage/effective-product-links?shop_id=7494763368967603447" | jq

# 报表
curl -H "Authorization: Bearer <key>" \
  "http://127.0.0.1:9877/v2/reporting/profit-daily?shop_id=7494763368967603447" | jq

curl -H "Authorization: Bearer <key>" \
  "http://127.0.0.1:9877/v2/reporting/missing-cost-products" | jq  # 无采购价的在售 SPU 清单（去人工补填）

# 人工成本填写页（浏览器打开）
open "http://127.0.0.1:9877/v2/pages/manual-costs?shop_id=7494763368967603447"
```

完整端点列表见 [`tech-doc/external-api.md`](tech-doc/external-api.md) 或 `GET /endpoints`。

> **legacy 端点 `/orders/*`, `/finance/*`, `/db/*`, `/sync/*`, `/miaoshou/*` 在 v2 已删除**。
> v1 数据保留在 `public.*`（4 周观察期内可回滚，过期后归档 — 见 `prod-switch/observe-archive.sh`）。

## 同步（sync-worker，APScheduler）

sync-worker 是独立 systemd 单元（`tts-erp-sync.service`），与 api 平级直连 PG，跑以下 jobs：

| Job | 来源 | 频率 |
| --- | --- | --- |
| `tiktok.orders` | /orders/search | 每 10 min（按 update_time 增量） |
| `tiktok.order_detail` | /order/202309/orders | on-demand（每 24h 全量补单） |
| `tiktok.products` | /products/search | 每 1h |
| `tiktok.after_sales` | /returns/search + /cancellations/search | 每 30 min |
| `tiktok.finance` | /finance/payouts + /finance/statements | 每天 02:00 |
| `tiktok.logistics` | /logistics/orders/{id}/tracking | 每 10 min（活跃运单） |
| `miaoshou.shops` | /get_shop_list | 每天 03:00 |
| `miaoshou.purchase_orders` | /search_goods_purchase_order_page | 每天 04:00 |
| `miaoshou.collect_box` | /search_collect_box_list | 每天 04:30 |
| `miaoshou.move_collect` | /search_move_collect_list | 每天 05:00 |
| `token.refresh` | oauth-receiver 续期 | 每小时检查到期 token |

每次 run 写一行 `integration.sync_jobs`（rows_total/inserted/failed + status）。失败写 `integration.sync_issues`，**job 不会卡死**，下一个调度继续跑。

## 成本口径（重要）

利润 = 售价 − **MANUAL_ENTRY**（人工填写，**最高优先级**）> 妙手采购单 > 1688 采集标价**禁用**。

为什么不用 1688 采集标价：1688 标价是妙手采集时看到的**初始报价**，实际采购价往往不同（量大价、议价）。把它当成本会污染利润口径。无 1688 价的在售 SPU → `/v2/reporting/missing-cost-products` → 运营手动补。

详细决策见 [`tech-doc/refactor-tech-plan-v2.md`](tech-doc/refactor-tech-plan-v2.md) §6 决策 10/12。

## 鉴权

所有端点（除 `/healthz`, `/endpoints`, `/openapi.json`）需要 `Authorization: Bearer <key>` 或 `X-API-Key: <key>`。三级角色：

- `readonly`：所有 v2 GET 端点
- `readwrite`：v2 POST（如 `/v2/reporting/manual-costs` POST）
- `admin`：`/v2/linkage/overrides` POST（覆盖 product_links）

key 管理：`python3 api_keys.py create --role <role> --name <name>`。库里只存 SHA-256 哈希，完整 key 创建时打印一次。

`/healthz` body 包含 `service: "tts-erp-v2"`（v1 返回纯 `{"status":"ok"}`），smoke 测试用此判断 v2 是否真在跑。

## 本地数据

- **一个库**：`tts_erp`（docker 容器 `postgres`，5432）
- **9 schema**：`integration` / `commerce` / `procurement` / `fulfillment` / `after_sales` / `finance` / `linkage` / `reporting` / `security`
- **35 张表 + 1 view**：见 [`tech-doc/data-model-target-v3.md`](tech-doc/data-model-target-v3.md)
- **Alembic 迁移**：`alembic/versions/20260829_init_nine_schemas.py`（初始 9 schema），`alembic upgrade head` 应用
- **旧 public.* 保留** 4 周观察期后归档 — 见 `prod-switch/observe-archive.sh`

## 安装 / 部署

```bash
# 一次性
cd /home/schan/tts-erp
python3 -m venv .venv && .venv/bin/pip install -e .
# 或: cp .env.example .env  # 编辑 DB URL + 加密 key

# 应用 schema（幂等）
.venv/bin/alembic upgrade head

# 启动（systemd --user 托管，开机自启；详见 prod-switch/）
systemctl --user start tts-erp.service         # v2 API（监听 :9877）
systemctl --user start tts-erp-sync.service    # v2 sync-worker
```

## 生产切换

5 个独立脚本，每个都可单独执行 / 单独回滚（见 [`prod-switch/`](prod-switch/)）：

```bash
bash prod-switch/preflight.sh              # 6 项硬性自检
bash prod-switch/install-sync-worker.sh     # 一次性：装 systemd 单元
bash prod-switch/switch-to-v2.sh            # 切到 v2（自动 rollback 失败）
bash prod-switch/postswitch-smoke.sh        # 8 项冒烟（healthz/auth/角色/rate-limit/CORS/manual-costs/v2 端点/PG 连接数）
bash prod-switch/observe-archive.sh         # 4 周后：停 :9876 + 改 public.* 名为 _deprecated_*
bash prod-switch/rollback.sh                # 紧急：回到 v1 旧栈
```

## 开发方式：TDD

```bash
cd /home/schan/tts-erp
.venv/bin/pytest tests_v2/ -p no:warnings        # 单元 + 仓库 + v2 端点
```

约定（`tests_v2/conftest.py`）：

- **事务回滚隔离**：DB 测试每个用例跑在外层事务里，结束即 rollback，可安全对生产库跑
- **`TEST_%` 哨兵**：落库提交的数据（如 `_seed_channel_product`），`shop_id`/`txn_id` 一律 `TEST_` 前缀
- **Drift-tolerant 断言**：live cron 每 10 min 写新行到 `public.*`，迁移测试用 runtime source count 而非硬编码

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

- **`/miaoshou/callback/*` 18 个回调节点保留**：v2 不再触发，但妙手那边仍有这 18 个 webhook 入口（生产不该关停，只能保留兼容）。删除需要单独 review。
- **回滚 4 周后撤 oauth-receiver**：v2 不依赖 :9876，但 rollback 脚本需要它在 4 周内保持运行。
- **`/returns/*` 和 `/cancellations/*` 不接 CREATE 写端点**：避免在真实店铺创建退货/取消单。详见 `AGENTS.md` §4。
- **`/reverse/202309/*` 不存在**：TikTok 202309 spec 没开放 reverse logistics 模块（HTTP 404 at CDN）。
- **migration 期间会临时写 `public.*` 表**：但 v2 读 `commerce.sales_orders` 等新表，**不会** 读 public.*，所以 migration 期间 v2 端点 0 数据是正常的（看 v2 端点前等 migration 跑完）。

## 相关

- [`AGENTS.md`](AGENTS.md) — AI agent 操作指南（端点速查、签名规范、DO/DON'T）
- [`CHANGELOG.md`](CHANGELOG.md) — 变更历史（按日期）
- [`handoff.md`](handoff.md) — 跨 session 交接笔记
- [`tech-doc/data-model-target-v3.md`](tech-doc/data-model-target-v3.md) — 9 schema V3 真理源
- [`tech-doc/external-api.md`](tech-doc/external-api.md) — v2 端点契约
- [`tech-doc/api-key-auth-design.md`](tech-doc/api-key-auth-design.md) — auth 设计
- [`tech-doc/refactor-tech-plan-v2.md`](tech-doc/refactor-tech-plan-v2.md) — 重构技术方案 V2（已实施）
- [`tech-doc/_archive/`](tech-doc/_archive/) — V1 时代过期文档
- [`miaoshou/README.md`](miaoshou/README.md) — 妙手集成
- [`prod-switch/`](prod-switch/) — 生产切换 / 回滚 / 归档脚本

## License

Internal use.
