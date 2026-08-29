# tts-erp 重构 · MiniMax M3 多 sub-agent 并行开发任务书

> 这是一份自包含的开发任务书。你（MiniMax M3）是总协调 agent，负责把任务拆给多个
> sub-agent 在不同 git worktree 中并行开发，最后按序合并、验证、清理 worktree。
> 严格按本文档执行，不要自由发挥架构决策——所有决策已定稿，你的工作是实现。

---

## 0. 项目背景（3 分钟版）

`tts-erp` 是 TikTok Shop 销售数据 + 妙手采购数据的同步与分析系统，部署在
`/home/schan/tts-erp`（就是当前仓库所在机器，生产环境同机）。

**当前架构的问题**（已全部决策完毕，见 §3 方案）：

- oauth-receiver 是独立服务（:9876），要融入主服务；
- 妙手有独立的 router 透传体系，要降级为 proxy 层的一个客户端；
- sync_cron 通过 HTTP 回环调自己的 `/sync/*`，要改为独立 sync-worker 进程直连 PG；
- 两个 PG 库（tts_erp / oauth_receiver）要合并为单库；
- 数据模型整体重建为九 schema 新模型。

**生产环境关键事实**：

| 项 | 值 |
| --- | --- |
| 仓库路径 | `/home/schan/tts-erp` |
| 生产服务 | `tts-erp.service`（systemd user 单元，uvicorn 跑 `tdd/tts_erp_fastapi.py`，:9877） |
| DB | docker 容器 `postgres`，库 `tts_erp` + `oauth_receiver` |
| DB 访问 | `docker exec postgres psql -U postgres -d tts_erp` |
| 测试 | `/home/schan/tts-erp/.venv/bin/pytest` |
| 现有服务重启 | `bash /home/schan/tts-erp/restart.sh` |
| 环境变量 | `/home/schan/tts-erp/.env`（含 TTS_ERP_DB_URL、妙手/TikTok 凭证） |

**生产数据实测结论**（开发时必须遵循的事实，不许推翻）：

1. 妙手搬家接口 `search_move_collect_list` 的 `platformItemId` 是 TikTok **SPU**
   （= `order_items.product_id`），不是 SKU（已用 59/0 双向匹配证实）。
2. 妙手有账户级 QPS 限流（错误码 `accountApiQpsRateLimit`），限流时返回
   `{"result":"fail","data":null}`——**现有分页循环把它误判为末页导致静默截断
   （237 条只落 20 条），新代码必须带延时 + 重试**。
3. 妙手采购单接口 `search_goods_purchase_order_page` 存在（参数 `page`/`pageSize`，
   注意不是 `pageNo`），当前账户 0 条数据，接入但按空数据处理。
4. 现有库时间字段单位不统一：TikTok 主表 epoch 秒、logistics_*是 epoch 毫秒、
   妙手 gmt_* 是 UTC+8 无时区字符串。迁移脚本必须逐字段处理。
5. 现有库混有测试数据（`MOCK_SHOP_12345`、analytics integration-test 残留），
   迁移时显式剔除。
6. 真实店铺：TikTok shop_id `7494763368967603447`（VN）↔ 妙手 shopId `17060852`。

---

## 1. 必读文档（动手前按顺序读完）

都在仓库内：

1. `AGENTS.md` — 现有系统操作指南（签名规则、端点、坑）
2. `tech-doc/refactor-tech-plan-v2.md` — **技术方案定稿，本任务的上游真理**
3. `tech-doc/data-model-target-v3.md` — **数据模型定稿（九 schema 全部表定义）**
4. `tech-doc/data-model-survey.md` — 现有 25 张表的 DDL + demo 数据 + 关联分析
5. `miaoshou/README.md` — 妙手核心接口字段映射与实测结论

方案与本文档冲突时，以方案文档为准并上报，不要自行改设计。

---

## 2. 全局开发规范（所有 lane 必须遵守）

### 2.1 技术栈纪律

- **Python 3 + FastAPI + SQLAlchemy 2.0 ORM + Alembic + psycopg3 + APScheduler + pydantic v2**。
  除这些和现有依赖外，引入任何新依赖前停下来上报。
- **SQLAlchemy declarative models 是表结构唯一真理源**。禁止手写 SQL DDL 建表。
- Alembic：`alembic revision --autogenerate` 的 diff **必须人工 review**；
  autogenerate 感知不到的东西（CHECK、部分索引、trigger、VIEW）手写进 migration。
- DB 读写统一走 ORM session；upsert 用
  `sqlalchemy.dialects.postgresql.insert().on_conflict_do_update()`。
- 时间一律 `timestamptz`；金额一律 `numeric(20,4)` + 显式币种；外部 id 一律 `text`；
  内部主键一律 `bigint generated always as identity`。

### 2.2 TDD 纪律

- 先写测试再实现；测试隔离沿用 `tdd/conftest.py` 的事务回滚模式（为其写 ORM 版等价物）。
- 外部 HTTP（TikTok/妙手）一律 mock，测试不触网。
- 每个 lane 完成标准包含：自己新增的测试全绿 + 不打破存量测试。

### 2.3 禁止事项

- ❌ 禁止修改 `public` schema 下任何现有表（它们是只读镜像）
- ❌ 禁止删除/修改 `analytics_sync/`、`miaoshou/callbacks/`（保留服役中）
- ❌ 禁止把明文 app_secret/token 写进代码、测试、日志
- ❌ 禁止绕过 `integration.credentials` 直接读 oauth token
- ❌ 禁止在标题/店名/图片 URL 上建立任何数据关联（V3 §14 铁律）
- ❌ 禁止新增 `/orders/*` 写操作端点（系统定位只读分析）

---

## 3. 任务拆解（Lane 划分）

### 总原则：**Lane 0 独占全部 schema/models，其余 lane 不许碰 alembic**

这是并行开发不打架的关键：所有九 schema 的 SQLAlchemy models + 初始 alembic revision
由 Lane 0 一次建全（包括 reporting/linkage 等后期才用到的表）。后续 lane 只写代码。
如果开发中发现模型要改，停下来上报，由协调者统一处理，不许各自加 revision
（多 worktree 各自加 revision 必然产生多 head 冲突）。

### Wave 1（串行先行，阻塞所有其他 lane）

#### Lane 0：地基 —— repo 布局 + 全部 models + alembic + 测试基座

**目标**：新骨架可启动、空 schema 可迁移、测试可跑。

交付物：

```text
tts_erp_v2/                  # 新包根（避免与存量代码同名冲突）
  db/
    base.py                  # DeclarativeBase、engine/session factory（读 TTS_ERP_DB_URL）
    models/                  # 九 schema 全量 models，按 schema 分文件：
      integration.py         #   credentials / raw_records / sync_jobs / sync_cursors / sync_issues
      commerce.py            #   channel_accounts / channel_products / channel_product_variants
                             #   / sales_orders / sales_order_lines
      procurement.py         #   procurement_accounts / procurement_products
                             #   / procurement_product_variants / purchase_orders
                             #   / purchase_order_lines / manual_product_costs
      fulfillment.py         #   shipments / shipment_lines / tracking_events
      after_sales.py         #   cases / case_lines
      finance.py             #   payouts / settlement_statements / settlement_transactions
                             #   / settlement_components
      linkage.py             #   account_links / product_links / variant_links
                             #   / link_evidence / link_overrides / link_issues
      reporting.py           #   product_cost_snapshots / product_profit_daily
                             #   / shipment_tracking_summary
      security.py            #   api_keys
alembic/                     # alembic 初始化 + 初始 revision（九 schema 全量建表
                             #   + linkage.effective_product_links VIEW，VIEW 手写）
alembic.ini
tests_v2/
  conftest.py                # 事务回滚隔离 fixture（ORM 版）+ TEST_ 哨兵约定
  test_models_smoke.py       # 每张表 insert/select 冒烟
```

字段级规范照抄 `data-model-target-v3.md` §5-§11 + `refactor-tech-plan-v2.md` §3.2，
特别注意：

- `product_links` 要有 `UNIQUE (procurement_product_id, channel_product_id, valid_from)`
  （这是对 V3 的修正，方案 §3.2 已记录）；
- `manual_product_costs`：`channel_product_id FK + unit_cost + currency + valid_from +
  valid_to NULL + note + created_by + created_at`，同一 SPU 视图取最新有效行；
- `effective_product_links` 是 VIEW（override 优先 → 有效妙手关系），写在 alembic
  手写 migration 里；
- `settlement_components` 无唯一约束外的特殊要求，但写入方只落非零行（Lane D 的职责，
  你只管建表）；
- 所有表带 `synced_at`/`created_at`（timestamptz default now()）。

**验收**：

- `alembic upgrade head` 在 tts_erp 库建出九 schema 全部对象；
- `alembic downgrade base` 能干净回退；
- `tests_v2` 冒烟全绿；存量 `tests/` 与 `tdd/` 测试不受影响（你没动它们）。

### Wave 2（Lane 0 合并后并行，4 个 lane）

#### Lane A：proxy 层

**目录所有权**：`tts_erp_v2/proxy/`

交付物：

```text
tts_erp_v2/proxy/
  tts_shop/
    signing.py             # 从 tts_signing.py 原样迁移 HMAC canonical 逻辑（AGENTS.md §2.2），
                           #   附现有测试向量回归
    client.py              # httpx/urllib 客户端：签名 + token 注入 + shop_cipher + 翻页
  miaoshou/
    client.py              # 从 miaoshou/ 包迁入 MiaoshouClient/MiaoshouErpClient 核心
                           #   （不动 miaoshou/callbacks/）
    rate_limit.py          # 令牌桶（默认 1 req/1.2s 可配）+ 限流错误码识别
    retry.py               # 指数退避（上限 6 次）；限流返回空列表时重试而非当末页 ★修复实测 bug
  token_service.py         # 从 tdd/oauth_receiver_core.py 合并：加密/解密/续期；
                           #   读写 integration.credentials；按 expires_at 判断续期时机
  errors.py                # 统一异常层级
```

**验收**：签名用 AGENTS.md §2.2 的已知向量测试通过；妙手限流重试有
"第一次限流返回空、第二次返回数据"的回归测试；token_service 对
`oauth_receiver_core.py` 现有测试用例保持行为等价。

#### Lane D：linkage + reporting 计算引擎

**目录所有权**：`tts_erp_v2/linkage/`、`tts_erp_v2/reporting/`

交付物：

```text
tts_erp_v2/linkage/
  compute.py               # link-compute 核心：move_collect 证据 → link_evidence
                           #   → product_links（relation_type=MIAOSHOU_PUBLISHED_TO_TIKTOK）；
                           #   fail 任务只留 evidence 不建 link；
                           #   多有效来源 → link_issues(AMBIGUOUS_SOURCE)，不生成成本
  issues.py                # PRODUCT_LINK_MISSING / MULTIPLE_PRIMARY_LINKS 等检测器
tts_erp_v2/reporting/
  cost_snapshots.py        # 成本快照计算：MANUAL_ENTRY（procurement.manual_product_costs
                           #   最新有效行）> 妙手采购单（三种成本法）；
                           #   无来源 → 不生成快照，进"在售无成本 SPU 清单"查询
                           #   ★1688 采集标价不是成本口径，禁止用作兜底
  profit_daily.py          # product_profit_daily 重建（calculation_version 递增，
                           #   全量可重建）
  coverage.py              # §16 验收指标查询集（解析率/覆盖率/冲突率等）
```

**验收**：用 `data-model-survey.md` 里的真实 demo 数据形状构造测试夹具；
优先级链（人工 > 采购单）、AMBIGUOUS_SOURCE 不生成快照、无来源进清单，各有测试。

#### Lane E：API v2 + 中间件 + 人工成本填写页

**目录所有权**：`tts_erp_v2/api/`、`tts_erp_v2/middleware/`、`tts_erp_v2/app.py`

交付物：

```text
tts_erp_v2/
  app.py                   # FastAPI 组装（只 include_router + middleware）
  middleware/
    auth.py                # 从 tdd/auth.py 迁移，表换 security.api_keys；
                           #   角色体系 readonly<readwrite<admin 不变
    rate_limit.py          # 从 tdd/rate_limit.py 迁移
  api/v2/
    commerce.py            # 店铺/商品/SKU/订单/订单行查询（只读）
    linkage.py             # 关联查询 + link_overrides 人工写接口 + link_issues 处理
    reporting.py           # 成本/利润/覆盖率报表 +
                           #   POST /v2/reporting/manual-costs（readwrite）
    pages.py               # 人工成本填写页：服务端渲染单页（不上前端框架），
                           #   首屏="在售但无有效成本的 SPU 清单"+ 填写表单，
                           #   表单提交走 POST /v2/reporting/manual-costs
```

**验收**：middleware 顺序 = CORS → Auth → RateLimit（与现状一致，AGENTS.md §9.3）；
旧 `/db/*`、`/orders/*`、`/sync/*`、`/token/*` 一个都不实现（硬切换，直接不存在）；
`/healthz` 保留；人工填写页可用 curl + 浏览器各验证一遍。

#### Lane F：存量数据迁移脚本

**目录所有权**：`scripts/migrate_v1_to_v2/`

交付物：

```text
scripts/migrate_v1_to_v2/
  README.md                # 运行顺序 + 回退说明
  migrate_shops.py         # shops + oauth_receiver.oauth_tokens → commerce.channel_accounts
                           #   + integration.credentials（剔除 MOCK_SHOP_12345）
  migrate_orders.py        # orders/order_items → sales_orders/sales_order_lines
                           #   （epoch 秒 → timestamptz；行上 product/variant 精确 id 回填，
                           #   解析不了写 sync_issues，禁止标题匹配）
  migrate_logistics.py     # order_shippings + logistics_* → shipments/shipment_lines/
                           #   tracking_events（★epoch 毫秒 → timestamptz；
                           #   raw.packages[] 多包裹展开）
  migrate_after_sales.py   # returns/cancellations → cases/case_lines
                           #   （raw 里 return_line_items/cancel_line_items 拆出）
  migrate_finance.py       # payments/statements/statement_transactions → payouts/
                           #   settlement_statements/settlement_transactions/
                           #   settlement_components（58 列只落非零组件）
  migrate_miaoshou.py      # miaoshou_* 四表 → procurement.* + link_evidence
                           #   （★gmt_* 是 UTC+8 字符串 → timestamptz）
  reconcile.py             # 对账：新旧库行数/金额总和/关联覆盖率三维 diff 报告
```

每个脚本：幂等（可重复跑）、分批（默认 500 行/批）、`--dry-run` 模式、
显式剔除测试数据。

**验收**：对当前生产库跑 `--dry-run` 输出完整映射报告；
`reconcile.py` 在重放后 diff 全零（除显式剔除项）。

### Wave 3（Lane A 合并后并行，2 个 lane）

#### Lane B：sync-worker 骨架 + TikTok 同步 jobs

**目录所有权**：`tts_erp_v2/sync_worker/`、`tts_erp_v2/jobs/tiktok/`

交付物：

```text
tts_erp_v2/sync_worker/
  scheduler.py             # APScheduler 装配（job 注册表驱动）
  watermarks.py            # integration.sync_cursors 读写
  cli.py                   # python -m tts_erp_v2.sync_worker run <job> 手动触发
tts_erp_v2/jobs/tiktok/
  orders.py                # 10min，update_time watermark 增量
  order_detail.py          # 新单详情补抓
  products.py              # 1h 全量 + 订单驱动补抓（订单行出现未知 product_id →
                           #   补抓 + sync_issues）★新接入 Product API
  finance.py               # statements/payments/statement_transactions，1h 时间窗
  after_sales.py           # returns/cancellations，10min watermark
  logistics.py             # shipments/tracking_events，10min cursor
```

每个 job：写 `integration.sync_jobs` 运行记录；原始 JSON 落 `integration.raw_records`
（规范化表只存 `raw_record_id` 指针）；解析失败进 `sync_issues` 不阻塞主链路；
幂等可重入。

**验收**：mock proxy 层测试 job 逻辑；用生产库只读副本跑一次真实增量同步冒烟
（由协调者在集成阶段执行，lane 内只交 mock 测试）。

#### Lane C：妙手 jobs + token-refresh job

**目录所有权**：`tts_erp_v2/jobs/miaoshou/`、`tts_erp_v2/jobs/token_refresh.py`

交付物：

```text
tts_erp_v2/jobs/miaoshou/
  shops.py                 # 1h
  collect_box.py           # 1h 全量翻页（当前 0 数据也要跑通空路径）
  move_collect.py          # 1h 全量翻页（237 条/12 页规模）★必须用 Lane A 的限流重试
  purchase_orders.py       # 1h（search_goods_purchase_order_page，参数 page/pageSize；
                           #   当前 0 数据，跑通空链路）
tts_erp_v2/jobs/
  token_refresh.py         # 1d，按 integration.credentials 里 expires_at 提前续期
                           #   （从 oauth-receiver 迁入的职责）
```

**验收**：move_collect job 有"限流后重试翻完 12 页"的回归测试（mock 出
`accountApiQpsRateLimit` 交替响应）；purchase_orders 空列表路径有测试。

---

## 4. Worktree 并行开发规范（严格遵守）

### 4.1 建 worktree

每个 lane 一个独立 worktree + 分支，在**合并顺序确定前互不依赖对方文件**：

```bash
cd /home/schan/tts-erp
git worktree add ../tts-erp-lane-0 -b lane/0-foundation
git worktree add ../tts-erp-lane-a -b lane/a-proxy
git worktree add ../tts-erp-lane-d -b lane/d-linkage-reporting
git worktree add ../tts-erp-lane-e -b lane/e-api-v2
git worktree add ../tts-erp-lane-f -b lane/f-migration
git worktree add ../tts-erp-lane-b -b lane/b-sync-tiktok
git worktree add ../tts-erp-lane-c -b lane/c-sync-miaoshou
```

### 4.2 并行规则

- **Wave 串行、Wave 内并行**：Wave 1（Lane 0）合并进 main 后，Wave 2 各 lane 从
  最新 main 重开/ rebase worktree；Wave 2 合并后 Wave 3 同理。
- **目录所有权即边界**：每个 lane 只能在自己所有的目录内新增/修改文件。
  共享文件（如根 `requirements.txt`、`pyproject.toml`）的改动全部上报协调者统一改，
  lane 内不许动。
- lane 内提交粒度随意；合并前用 `git rebase main` 保持线性。
- 每个 lane 完成时输出：变更文件清单 + 测试运行结果 + 遗留风险三条以内。

### 4.3 合并顺序（协调者执行）

```text
lane/0-foundation → lane/a-proxy → lane/e-api-v2 → lane/d-linkage-reporting
→ lane/f-migration → lane/b-sync-tiktok → lane/c-sync-miaoshou
```

每次合并后跑一次全量测试（存量 `tests/` + `tdd/` + 新 `tests_v2/`）再合下一个。

### 4.4 合并完成后删除 worktree（必做清理）

```bash
# 每个 lane 合并并验证后：
git worktree remove ../tts-erp-lane-0
git branch -d lane/0-foundation
# ... 全部 lane 同理，最后检查：
git worktree list        # 应只剩主 worktree
git branch               # 应无 lane/* 残留
```

若某 lane 废弃不合并：`git worktree remove --force ../tts-erp-lane-x && git branch -D lane/x`。
**任何情况下不允许遗留 worktree**——它们会锁住分支删除并污染磁盘。

---

## 5. 集成验收（所有 lane 合并后，协调者执行）

按顺序执行，任何一步失败停下来修，不许跳过：

1. **结构**：`alembic upgrade head` 干净；`alembic downgrade base && upgrade head` 往返干净。
2. **全量测试**：`.venv/bin/pytest tests/ tdd/ tests_v2/ -q` 全绿。
3. **迁移干跑**：`scripts/migrate_v1_to_v2/` 全部 `--dry-run` 报告合理。
4. **迁移实跑 + 对账**：实跑迁移脚本 → `reconcile.py` 三维 diff 为零
   （除 MOCK/integration-test 剔除项，报告里要单列）。
5. **sync-worker 冒烟**：手动 `run orders`、`run move_collect`（验证限流修复）、
   `run token_refresh --dry-run` 各一次，sync_jobs 记录正确。
6. **API 冒烟**：新 app 起在**非 9877** 端口（如 9878），验证
   `/healthz`、v2 各只读端点、auth 401/403 行为、manual-costs 页面；
   存量生产服务（9877）全程不受影响。
7. **产出集成报告**：以上每步的命令 + 结果 + 遗留问题清单。

**注意**：本任务书只到"新系统并行建好、迁移验证完成"为止。
**不做生产切换**（不停旧服务、不动 9877、不删旧端点、不动 public 表）——
切换按 `refactor-tech-plan-v2.md` §7.1 操作手册另行执行。

---

## 6. 完成定义（DoD）

- [ ] 七个 lane 全部合并进 main，worktree 与 lane 分支全部清理
- [ ] 全量测试绿（存量 + 新增）
- [ ] alembic 往返干净
- [ ] 迁移 + 对账 diff 为零
- [ ] sync-worker 与 API 冒烟通过
- [ ] 集成报告产出
- [ ] 生产 9877 服务全程无感知（不许碰）
