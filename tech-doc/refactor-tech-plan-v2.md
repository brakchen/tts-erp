# tts-erp 重构技术方案 V2（定稿）

> 版本：V2 · 2026-08-29（替代 V1，所有评审决策已并入正文）
> **实施状态：已完成**——v2 已于 2026-08-29 切流生产（`:9877` 跑 `tts_erp_v2.app:app`）。
> As-built 与本文的差异：
>
> - 代码布局落在 `tts_erp_v2/` 包内（`app.py` / `api/v2/` / `proxy/` / `sync_worker/` /
>   `db/models/`），不是 §2.2 画的顶层 `app/` `proxy/` 目录；
> - sync-worker 实际注册的 job 及频率以 `tts_erp_v2/sync_worker/scheduler.py` 的
>   `JOBS` 为准（6 个 tiktok job + token.refresh；妙手 4 个 job 与 link-compute /
>   cost-snapshots 重算**未接入调度**，与 §4.1 的计划表不同）；
> - `/miaoshou/callback/*` 未挂进 v2 app（代码保留在 `miaoshou/callbacks/`，实测 404）。
>
> 上游输入：架构决策（§1）+ 数据模型 V3（`tech-doc/data-model-target-v3.md`）
> 现状基线：`tech-doc/_archive/data-model-survey-v1.md`（全表 DDL + demo + 关联分析；2026-08-30 归档改名）
> 妙手接口实测：`miaoshou/README.md`（search_move_collect_list 字段映射 + 关联结论）

---

## 1. 已确认的架构决策（方案前提，不再讨论）

1. **oauth-receiver 融入 tts-erp**：不再是独立服务（:9876 退役）。token 加密存储、续期是
   tts-erp 内部能力，归属 proxy 层。仓内 `tdd/oauth_receiver_core.py`（1111 行
   in-process 实现）作为合并基础。
2. **proxy 层统一出方向集成**：miaoshou 与 tts-shop api 平级，都是 proxy 层的下游。
   不再有独立的 miaoshou router 体系。
3. **sync 与 api 平级解耦**：同步是独立内部定时任务进程（APScheduler），直接读写 PG，
   不再通过 `POST :9877/sync/*` HTTP 回环。
4. **单一数据库**：不再分 `tts_erp` / `oauth_receiver` 两个库，oauth_tokens 并入主库。
5. **数据模型按 V3 定稿**：`integration / commerce / procurement / fulfillment /
   after_sales / finance / linkage / reporting / security` 九 schema。
6. **系统定位只读分析**：`/orders/*` 写操作代理（确认发货/取消/改状态/物流）整体删除。
7. **不做旧接口兼容层**：旧 `/db/*` 直接删除，API 按新模型重设计，硬切换。
8. **DB 访问层用 SQLAlchemy 2.0 ORM + Alembic**：models 即表结构唯一真理源，
   变更走 revision，可 upgrade/downgrade 回退。
9. **保留项**：`/miaoshou/callback/*` 18 个回调节点（有服役中使用方）；
   `analytics_sync/` 广告数据同步子系统（代码留仓、表留 public、不进新模型）。

---

## 2. 目标系统架构

### 2.1 进程拓扑

```text
                        ┌────────────────────────── PG (docker, 单库 tts_erp) ──┐
                        │  integration.  commerce.  procurement.  fulfillment.  │
                        │  after_sales.  finance.  linkage.  reporting.         │
                        │  security.   + public（旧表只读镜像 / analytics_*）    │
                        └───────▲───────────────────────────▲───────────────────┘
                                │ SQLAlchemy                 │ SQLAlchemy
              ┌─────────────────┴───────┐     ┌──────────────┴────────────────┐
外部 client → │ tts-erp-api             │     │ tts-erp-sync-worker           │
(Bearer key)  │ FastAPI :9877           │     │ 独立 systemd 进程 + APScheduler│
              │  ├─ auth / rate limit   │     │  ├─ job: orders/returns/...   │
              │  ├─ /v2/* 查询 API      │     │  ├─ job: products/variants    │
              │  ├─ /v2/* 关联管理      │     │  ├─ job: miaoshou-*           │
              │  ├─ /miaoshou/callback  │     │  ├─ job: token-refresh        │
              │  ├─ /v1/analytics/sync  │     │  ├─ job: link-compute         │
              │  └─（保留现状子系统）    │     │  └─ job: cost-snapshots       │
              └──────────┬──────────────┘     └──────────────┬────────────────┘
                         │            ┌──────────────────────┴───────────────┐
                         └──────────→ │ proxy 层（进程内包，非独立服务）      │
                                      │  ├─ tts_shop_client（签名+token注入）│
                                      │  ├─ miaoshou_client（签名+限流重试） │
                                      │  └─ token_service（加密/解密/续期）  │
                                      └──────────────┬───────────────────────┘
                                                     ▼
                                   TikTok Shop Open API    妙手 openapi.wanshifu.com
```

要点：

- **proxy 层是库不是服务**：api 进程和 sync-worker 进程都 import 它。token 的获取/
  解密/续期在 `token_service` 内闭环，调用方只拿 `shop_id` 换明文 token。
- **sync-worker 是独立 systemd 单元**（`tts-erp-sync.service`），APScheduler 进程内调度
  替代 crontab + HTTP 回环；每个 job 幂等，保留 CLI 手动触发入口
  （`python -m sync_worker run <job>`）。
- **token 续期 job 归属 sync-worker**（现 oauth-receiver 的 cron 职责一并迁入）。
- analytics_sync 与 miaoshou callback 继续挂在 api 进程上，不动。

### 2.2 代码结构（目标 repo 布局）

```text
app/
  main.py                  # FastAPI app 组装（只做 include_router + middleware）
  api/
    v2/                    # 新契约路由（commerce / linkage / reporting 查询，linkage 含 override 写接口）
  middleware/              # auth.py / rate_limit.py（沿用，表换 security.api_keys）
proxy/
  tts_shop/                # 签名、HTTP client、端点方法（现 tts_signing.py 演进）
  miaoshou/                # 现 miaoshou/ 包迁入，去掉透传 router；callback 体系保留
  token_service.py         # 加密/解密/续期（oauth_receiver_core.py 演进）
sync_worker/
  scheduler.py             # APScheduler 装配
  jobs/                    # 每类同步一个 job 模块（纯函数 core + 调度壳）
  watermarks.py            # integration.sync_cursors 读写
domain/                    # 解析/转换纯函数（raw json → 规范化行）
repositories/              # SQLAlchemy ORM models + 查询，按 schema 分文件
alembic/                   # 结构迁移 revisions
analytics_sync/            # 保留现状（广告数据同步，不进新模型）
scripts/migrate_v1_to_v2/  # 一次性数据迁移脚本（旧表 + raw 重放）
tests/
```

**明确删除**：`/miaoshou/{domain}/{method}` 透传路由（SDK 能力保留在 proxy 层，不再暴露
HTTP 透传）、`/sync/*` HTTP 端点（worker + CLI 取代）、`/orders/*` 写操作代理、
全部旧 `/db/*` 端点、`/token/*`（token 转内部能力）、`tts_erp.py` legacy 模块
（persist_* 迁入 repositories/domain）。

**明确保留**：`/miaoshou/callback/*`（18 个回调节点）、`analytics_sync/` 全部。

### 2.3 技术选型

| 关注点 | 现状 | 目标 | 理由 |
| --- | --- | --- | --- |
| Web 框架 | FastAPI | 沿用 | 已在生产 |
| DB 访问 | psycopg3 裸 SQL | **SQLAlchemy 2.0 ORM** | 表结构即代码；upsert 走 `postgresql.insert().on_conflict_do_update()` |
| schema 迁移 | regen_schema.py 手工 dump | **Alembic**（基于 ORM metadata autogenerate） | 每次变更 = 可回退 revision；九 schema 多轮演进刚需 |
| 进程内调度 | cron + run_sync_cron.sh | **APScheduler** | 去 HTTP 回环；job 级重试/错过补偿 |
| 加密 | oauth_receiver_core 内实现 | 沿用算法迁入 token_service | 不重造轮子 |
| 限流 | 仅入方向（api key 桶） | 入方向沿用 + **出方向 proxy 层令牌桶** | 妙手 QPS 实测会限流（§9.1） |

**Alembic 使用纪律（写进开发规范）**：autogenerate 的 diff 必须人工 review——它对
CHECK 约束、部分索引、函数/trigger 感知不可靠。现有库中 `sync_log` retention trigger、
`touch_updated_at()` 等 DB 侧逻辑，如要保留须写成手写 migration，不能依赖 autogenerate。

---

## 3. 数据库落地设计

### 3.1 库与 schema

- 单库 `tts_erp`，九 schema。现有 `public` 下 24 张旧业务表**原样保留为只读镜像**
  （切换期对账参照），新数据只写新 schema；`analytics_*` 6 张表留在 public 继续服役。
- `oauth_receiver.oauth_tokens` 迁入 **`integration.credentials`**（密文 bytea 列原样迁移，
  加密密钥随 .env 移交）。
- **`integration.credentials` 定位**：外部平台凭证表——一次 TikTok 店铺授权 /
  一个妙手 license = 一行，存密文 token / secret、过期时间、granted scopes。
  业务账户表（`commerce.shops` / `procurement.procurement_accounts`）
  通过 `credential_id` FK 引用它，凭证与业务实体解耦。即使长期单店铺，
  oauth_tokens 合库也需要这个落点；不做多凭证的额外设计，未来多店铺/多 license 插行即可。

### 3.2 核心表 DDL 原则（V3 §5-§11 的字段级补充决策）

- **id 策略**：内部代理主键 `bigint generated always as identity`；外部 id 全 text；
  账户范围唯一约束 `UNIQUE(shop_pk, external_*)`。
- **raw 策略**：`integration.raw_records` 存全量原始 JSON（credential_id、
  endpoint、external_id、captured_at、payload jsonb）；规范化表只留 `raw_record_id`
  指针，不再每表复制 raw jsonb（现状 orders 719 行 4.3MB，双写是体积主因）。
- **时间转换映射**（迁移脚本逐字段执行，单位不一致是实测陷阱）：

  | 来源字段 | 现状 | 目标 |
  | --- | --- | --- |
  | orders/returns/... `*_time` | epoch **秒** bigint | timestamptz |
  | logistics_* `event_time` / `*_at` | epoch **毫秒** bigint | timestamptz |
  | 妙手 `gmt_*` | text，UTC+8 无时区 | timestamptz（按 +08:00 解析） |

- **财务组件表**：`settlement_components` 只落**非零**金额行
  （实测 58 列中大量恒 0，全落会 17 倍膨胀）。
- **product_links 唯一性**（对 V3 的修正）：
  `UNIQUE (procurement_product_id, spu_pk, valid_from)`，
  否则有效期叠加的历史版本会重复插入。
- **`linkage.effective_product_links` 用 VIEW 实现**（override 优先 → 有效妙手关系）；
  `reporting.*` 用可重建表（job 全量重算 + `calculation_version`），不用物化视图
  （重算时机要显式可控）。
- **人工成本表** `procurement.manual_product_costs`：`id identity PK、
  spu_pk FK、unit_cost numeric(20,4)、currency、valid_from、valid_to NULL、
  note、created_by、created_at`；同一 SPU 同时只有一条有效记录（视图取最新有效行），
  填写历史全部保留。

### 3.3 迁移路径

- **结构迁移全部走 Alembic**：models 是唯一结构真理源；
  `alembic revision --autogenerate` 辅助出 diff，人工 review 后入库；
  `upgrade head` / `downgrade` 可回退。
- **存量数据迁移**写成一次性脚本（`scripts/migrate_v1_to_v2/*.py`）：
  从 public 旧表 + raw jsonb 重放到新 schema。不走 alembic
  （结构迁移工具不承担数据重放）。
- 重放可行性已验证：orders.raw 含 packages[]/line_items[].package_id（多包裹）、
  returns/cancellations raw 含行项目、statement_transactions raw 与 58 列一一对应。
- 迁移脚本显式剔除测试数据：`MOCK_SHOP_12345`（shops/oauth_tokens）、
  analytics integration-test 残留。

---

## 4. 同步与 proxy 设计

### 4.1 job 清单（sync-worker）

| job | 数据源 | 频率 | 增量策略 |
| --- | --- | --- | --- |
| orders | TikTok | 10min | update_time watermark（沿用现有 L1） |
| order-detail 补抓 | TikTok | 随 orders | 新单详情 |
| **products / variants**（新） | TikTok Product API | 1h + 订单驱动 | 订单行出现的未知 product_id 触发补抓；写 sync_issues |
| statements / payments | TikTok | 1h | 时间窗 |
| statement_transactions | TikTok | 1h | 跟随 statements |
| returns / cancellations | TikTok | 10min | update_time watermark |
| shipments / tracking_events | TikTok | 10min | sync_cursors（现 logistics_sync_targets 演进） |
| miaoshou-shops / collect-box / move-collect | 妙手 | 1h | 全量翻页（数据量小） |
| **miaoshou-purchase-orders**（新） | 妙手 | 1h | `search_goods_purchase_order_page`，page/pageSize 全量翻页；当前 0 数据，先跑通链路 |
| **token-refresh**（新，从 oauth-receiver 迁入） | TikTok auth | 1d | 按 expires_at 提前续 |
| link-compute（新） | 内部 | 10min | 证据 → product_links → issues |
| cost-snapshots（新） | 内部 | 1h | effective_links → reporting |

### 4.2 proxy 层出方向约束

- **妙手限流**（实测 `accountApiQpsRateLimit`）：client 内置令牌桶（默认 1 req/1.2s
  可配）+ 限流错误码指数退避重试（上限 6 次）。**修复现状 bug**：限流返回空列表
  会被分页循环误判为末页（首次同步 237 条只落 20 条）。
- **TikTok 签名**：沿用 `tts_signing.py` canonical 规则（AGENTS.md §2.2），原样迁移。
- **统一重试/超时**：proxy 出口收敛到一个 `call_with_retry` helper。

### 4.3 幂等与异常队列

- 所有 job 写 `integration.sync_jobs`（开始/结束/行数/状态/错误）；
  游标写 `integration.sync_cursors`。
- 解析失败、外键解析不了（如订单行的 product 尚未同步）写 `integration.sync_issues`，
  后续 job 兜底重试，不阻塞主链路。
- 关联类异常写 `linkage.link_issues`（PRODUCT_LINK_MISSING / MULTIPLE_PRIMARY_LINKS /
  AMBIGUOUS_SOURCE 等）。

---

## 5. 关联计算设计（本系统核心增量）

数据源已实测验证（2026-08-29，详见 `miaoshou/README.md`）：

- `search_move_collect_list` 的 `platformItemId` = TikTok **SPU**（product_id），
  与 sku_id 零匹配（59/0 双向验证）→ `variant_links` 按空表设计正确；
- 当前窗口覆盖率 59/59 = 100%；182 个已发布 SPU 中 123 个零销售；
- 妙手 shopId 17060852 ↔ TikTok shop 7494763368967603447 有事实证据（account_links）。

`link-compute` job 逻辑：

1. 读 move_collect 证据（success 且有 platformItemId）→ upsert `linkage.link_evidence`；
2. 按证据 upsert `product_links`（relation_type=MIAOSHOU_PUBLISHED_TO_TIKTOK），
   fail 任务不建 link 但留 evidence；
3. `effective_product_links` 视图：有效 override (ALLOW/DENY/PRIMARY) > 有效妙手关系；
4. 一个 channel_product 有多个有效来源且无法定口径 → `link_issues(AMBIGUOUS_SOURCE)`，
   不生成成本快照；
5. `cost-snapshots` job 从 effective links + 成本源计算。**成本源三档，优先级从高到低**：
   - **人工填写**（`cost_method=MANUAL_ENTRY`，2026-08-29 决策）：在售 SPU 数量少
     （百级）且只关注长期在售品，由运营在内部网页逐个手填成本，存入
     `procurement.manual_product_costs`（按 channel_product 维度、带生效区间和填写人）。
     下架 SPU 无需填写。
   - 妙手采购单 `search_goods_purchase_order_page`（接口已实测存在；
     **当前账户 0 条采购单**，业务尚未使用妙手采购功能；关联键预期
     `collectBoxDetailId` 或 `sourceItemId`，待有数据后确认字段映射）。
   - **无兜底**：1688 采集标价不作成本口径（标价 ≠ 实际采购价，会污染报表）。
     既无人工填写又无采购单的 SPU 不生成成本快照，进"在售无成本 SPU 清单"
     监控，由人工补填闭环。

---

## 6. API 设计

- **新契约**：`/v2/commerce/...`（店铺/商品/订单查询）、`/v2/linkage/...`
  （关联查询 + 人工 override 写接口 + issues 队列处理）、`/v2/reporting/...`
  （成本/利润/覆盖率报表 + **人工成本填写** `POST /v2/reporting/manual-costs`）。
  沿用 Bearer key + role（security.api_keys 表平移，key 哈希不变，已有 key 继续可用）。
- **人工成本填写页**：api 进程挂载一个极简内部页面（服务端渲染单页，无前端框架），
  列出"在售但无有效成本的 SPU 清单"+ 填写表单（成本、币种、生效时间、备注）。
  页面操作走同一个 `POST /v2/reporting/manual-costs` 接口，权限 readwrite。
- **硬切换，无兼容层**：旧 `/db/*`、`/orders/*` 写代理、`/sync/*`、`/token/*` 直接删除；
  `tech-doc/external-api.md` 随 v2 重写。
- **保留不动**：`/miaoshou/callback/*`、`/v1/analytics/sync`、`/healthz`、`/endpoints`。

---

## 7. 实施计划（对齐 V3 §15，补技术动作）

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| 0. 地基 | SQLAlchemy models + Alembic 接入；九 schema 空库可起；repo 布局调整；sync-worker 空壳 + systemd 单元 | `alembic upgrade head` 绿；CI 绿 |
| 1. 销售主干 | products/variants 新接入（Product API spike 先行）；orders/lines 迁移重放；行↔商品精确 id 绑定 | 订单行商品解析率 ≈100%；无标题绑定 |
| 2. 采购域 | 妙手账户/采集箱/搬家记录迁入；procurement_products 建立；purchase-orders job 链路跑通（0 数据也要跑通） | 采购侧链路全绿 |
| 3. 商品桥梁 | link_evidence / product_links / effective view / issues 队列；link-compute job | 关联覆盖率基线报表产出 |
| 4. 物流/售后/财务 | 多包裹重放；case_lines 拆出；settlement_components（非零过滤） | 金额一致率 100%（对账） |
| 5. 成本利润 | cost-snapshots + product_profit_daily + 冲突规则（MANUAL_ENTRY 口径） | 可计算成本销售额占比可监控 |
| 6. 切换 | 详见 §7.1 切换操作手册：新库重建 → 双跑对账 → 切流 → 观察期 → 旧表归档删除 | 对账 diff 为零；观察期满无问题；:9876 下线 |

每阶段 TDD（沿用 tdd/conftest.py 事务回滚隔离模式），部署沿用 systemd user 单元，
新增 `tts-erp-sync.service`。

### 7.1 切换操作手册（原则：保留原数据 → 新表重建 → 验证通过后才删旧）

```text
public（旧表，原样保留）          九 schema（新模型）
     │ 不停写                          │
     │  ① 初始重放 ──────────────────→ │ scripts/migrate_v1_to_v2 全量灌入
     │ 双跑期：旧 cron 照写 public     │
     │        新 sync-worker 照写新库   │ ② 增量追平 + 对账
     │                                │
     │  ③ 对账通过后：停旧 cron        │ ④ api 切 /v2，删旧端点
     ▼                                ▼
public 降级只读镜像 ──观察期（4 周）──→ ⑤ pg_dump 归档 public 业务表 → DROP
```

具体步骤：

1. **新表重建**：`alembic upgrade head` 建九 schema；跑 `scripts/migrate_v1_to_v2/`
   从 public 旧表 + raw jsonb 全量重放（显式剔除 MOCK/integration-test 数据）。
   **public 一个字节都不动**。
2. **双跑对账**：旧 cron 继续写 public，新 sync-worker 同时写新 schema；
   每日跑对账脚本（行数 / 金额总和 / 关联覆盖率三维 diff），diff 连续 3 天为零进入下一步。
3. **切流**：停旧 cron；api 上新版本（旧端点删除，/v2 上线）；oauth-receiver 的
   token 续期 cron 切到 sync-worker 的 token-refresh job，验证一轮续期成功后 :9876 下线。
4. **观察期（4 周）**：public 旧表降级只读（`REVOKE INSERT/UPDATE/DELETE`），
   作为问题回查的数据源；新库任何问题可随时对照。
5. **归档删除**：观察期满无问题 → `pg_dump -n public` 归档到文件后
   `DROP` public 下 24 张旧业务表（`analytics_*` 保留）。oauth_receiver 库同样
   先 dump 再 drop。

   > ✅ **2026-09-05 提前执行（原定观察期 ~09-26 满）**：已按本步流程 pg_dump 归档到
   > `/home/schan/backups/tts_erp_public_v1_legacy_20260905T110814Z.sql.gz` 后 DROP 19 张 v1 业务表
   > （实为 19 非 24：analytics_* 已由 migration 0004 迁出 analytics schema；oauth 表本就在独立
   > oauth_receiver 库）。`public` schema 保留 `fn_touch_updated_at()`（41 个 v2 updated_at 触发器依赖，
   > 属 v2 基础设施非 legacy）。oauth_receiver 库未动，届时同样先 dump 再 drop。

任何一步出问题：新链路停用、旧 cron 拉起即可回退（public 全程未被修改）。

---

## 8. 风险登记

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| 妙手采购单零数据，成本口径依赖人工填写 | 中 | 已决策：人工填写为主口径（在售 SPU 百级，工作量可控）；报表监控"在售无成本 SPU 清单"防止漏填 |
| 妙手采购单字段映射未最终确认 | 中 | 建一笔测试采购单后拉取定稿；不阻塞阶段 0-3 |
| TikTok Product API 未接过，字段/权限待验证 | 中 | token 已含 products scope；阶段 1 先做 spike |
| 订单历史窗口有限（API 可回拉深度有限） | 中 | 阶段 1 评估可回拉深度，定历史回补范围 |
| 硬切换无兼容层，外部 client 需同步改造 | 中 | 切换前盘点 /db/* 调用方；对账 diff 为零再删旧端点 |

---

## 9. 已确认的前置事实（本调研阶段产出）

1. 妙手 QPS 限流导致搬家记录同步静默截断（237 条只落 20 条）——调研中已手动补齐全量，
   代码修复纳入 sync-worker 限流设计。
2. `platformItemId` = SPU，59/0 双向匹配证实。
3. 当前关联覆盖率 100%（窗口内），基线良好。
4. 妙手采购单接口存在（参数 `page`/`pageSize`，响应 `goodsPurchaseOrderList`+`total`），
   当前账户 0 条采购单。
5. 订单数 693 vs 720 差异已澄清：无重复（720 行 = 720 distinct order_id，全属真实店铺），
   差值为查看后台后新产生的订单——693 笔的时刻点与库内精确吻合。
6. `shops`/`oauth_tokens` 含 MOCK 测试数据、`analytics_*` 含 integration-test 残留——
   迁移脚本显式剔除。

---

## 10. 决策记录（2026-08-29 评审闭环）

1. oauth-receiver 融入 tts-erp，:9876 退役。
2. proxy 层统一出方向；miaoshou 无独立 router。
3. sync-worker 独立进程（APScheduler），与 api 平级，直连 PG。
4. 单库九 schema；oauth_tokens 并入主库。
5. 数据模型按 V3 定稿（含 product_links 唯一约束修正）。
6. `/orders/*` 写操作代理删除，系统定位只读分析。
7. 不做旧接口兼容层，硬切换。
8. DB 访问层 SQLAlchemy 2.0 ORM + Alembic。
9. 保留 miaoshou callback 体系与 analytics_sync 子系统（不动）。
10. 成本口径：人工填写为主，妙手采购单为辅（待业务启用）；**1688 标价不作成本口径**，
    无成本来源的 SPU 不生成快照、进监控清单由人工补填。
11. 凭证表命名定稿为 `integration.credentials`（单表，不拆 connections/credentials）。
12. 采购价以**人工填写**为主口径（`procurement.manual_product_costs` + 内部填写页）；
    妙手采购单为辅助（待业务启用）；1688 标价不入成本；下架 SPU 不填。
13. 切换原则：保留原数据 → 新表重建 → 双跑对账 → 观察期 4 周 → 归档后删除（§7.1）。
