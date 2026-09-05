# tts-erp CHANGELOG

## 2026-09-05 (fix) — 全量测试稳定性：修两个顺序/残留依赖 bug

修复导致 `scripts/test.sh fast` 偶发红的问题（均与本次 ops 无关的预存问题）：

- `tests/jobs_tiktok/test_after_sales_job.py::test_after_sales_unknown_order_dedup_across_ticks`：
  select 未按 external_id 过滤，会把生产真实未解决的 UNKNOWN_ORDER 行（sync worker 写入）算进计数 →
  断言 3≠1 误挂。改为只查自己那行（`external_id == "R_DUP"`）。
- `tests/api/conftest.py`：确定性 TEST_ key 改 **PostgreSQL upsert（ON CONFLICT DO UPDATE）**。
  残留 key（run 被 kill 等导致 teardown wipe 未跑）会让后续所有 api 测试的 key fixture 撞
  `ix_api_keys_key_hash` 唯一约束 → 401 级联全 api 域（曾观察到 ObjectDeletedError / 单次 run 50+ 失败）。
  幂等覆盖后自愈；验证：手工插入残留 key 后测试照常通过。

## 2026-09-05 (ops) — v1 legacy `public.*` 归档删除（19 张业务表 DROP）

v2 切流的 v1 数据回查窗口提前收口：按 `tech-doc/refactor-tech-plan-v2.md` §7.1 step 5 流程
（先 dump 归档 → 再 DROP）删除 v1 遗留层。**只删 legacy 数据表，不动 v2 基础设施**。

- DROP 19 张 `public` v1 业务表：orders / order_items / order_shippings / payments / shops /
  returns / cancellations / statements / statement_transactions / logistics_tracking(_events|_targets) /
  miaoshou_shops / miaoshou_price_templates / miaoshou_collect_box_details / miaoshou_move_collect_tasks /
  sync_log / api_keys / alembic_version（+ 附属索引、2 个 standalone 序列）。
- 同步清理 3 个仅服务 v1 表的孤儿函数：`touch_updated_at`、`trg_sync_log_retention_fn`、`cleanup_sync_log`。
- **保留** `public` schema 与 `public.fn_touch_updated_at()`——migration 0001 的 updated_at 自动维护
  触发器函数，41 个 v2 表触发器依赖（`tests/db/test_time_fields_convention.py` 锁定）。
- 归档：`/home/schan/backups/tts_erp_public_v1_legacy_20260905T110814Z.sql.gz`（schema+data 1.2MB，可完整恢复）。
  oauth_receiver（独立 DB）未动。
- `scripts/regen_schema.py` 重生成 `schema_tts_erp.sql`（-839 行，public 遗留段落移除）。
- 验证：DROP 后 public 业务表 0 残留、`fn_touch_updated_at` 在、41 触发器完好、相关测试 0 fail。

## 2026-09-05 (refactor) — analytics schema reorg（migration 0007，删 4 张僵尸表 + 审计改文件日志）

依据 `tech-doc/analytics/reorg-plan.md`（2026-09-05），analytics schema 从「5 表 + 1 view」收成「1 表 + 1 view」：

### Schema / migration

- `alembic/versions/0007_analytics_reorg_drop_dead_tables.py`：
  - DROP `analytics.ad_records`（生产零 SELECT,与 ad_raw.response.body 重复存同一 payload）
  - DROP `analytics.ad_daily_completeness`（has-data 已查 ad_raw,该表不参与协议,仅第三次写放大）
  - DROP `analytics.ad_shop_timezones`（生产读写路径均死：fetch_timezone 仅测试调用,
    4 条 SQL 常量都是未执行死 SQL,_today_in_tz 无调用方）
  - DROP `analytics.ad_audit_log`（审计改文件日志，见下）
- 保留 `analytics.ad_raw`（source-of-truth,5 元组 unique 幂等 upsert）与
  `analytics.ad_product_links` VIEW（不变,仅读 ad_raw）

### 代码改动

- `tts_erp_v2/db/models/analytics.py`：删 4 个类（AdRecord / AdDailyCompleteness /
  AdShopTimezone / AdAuditLog）,仅留 AdRaw;`db/models/__init__.py` 同步
- `tts_erp_v2/analytics/repository.py`：删 9 条死 SQL 常量 + `write_audit()` /
  `purge_expired()` / `fetch_timezone()`;`upsert_dump()` 缩为**单表写**
  (只 INSERT ad_raw, ON CONFLICT 5 元组 DO UPDATE, RETURNING xmax=0 判
  inserted/duplicate)
- `tts_erp_v2/api/v2/analytics.py`:删全部 `write_audit` 调用与 import;新增
  `tts_erp_v2.analytics.ingest` logger,get_cursor/post_dumps 的成功与错误
  路径各打一条 key=value 单行（字段 1:1 对齐原 ad_audit_log 列）。
  `_audit_and_error` 仍写 stderr（2026-08-30 事故回归守护点）+ 同一份
  消毒 message 进 logger。HTTP 响应契约逐字节不变。
- `tts_erp_v2/jobs/analytics_retention.py`:整文件删除（4 张表 drop 后无对象可 purge）
- `tts_erp_v2/sync_worker/scheduler.py`:JOBS 摘除 `analytics.retention`
  条目及注释段（JOBS 数 13 → 12）

### 审计迁移语义

- 旧 audit 表职责迁出到 `tts_erp_v2.analytics.ingest` logger,与
  `middleware/access_log.py` 全站请求日志同源（stderr/stdout → logs/ 下文件）
- 成功路径也补一条 log（旧 audit 表只写 success path 但 ops 用得不多;
  现在 records 计数可从 log 看到）
- 历史 54,786 行 ad_audit_log 随 drop 丢失（已接受;如需留底,部署前
  SELECT 导出到日志文件）

### 测试

- `tests/analytics/test_repository.py`:删 fetch_timezone 3 个 + write_audit 2 个 +
  purge_expired 2 个 + _add_days / _subtract_days 6 个测试（共 13 删）;
  cleanup 从 5 张表缩为 1 张（ad_raw）。upsert_dump 行为测试保留并
  加注释说明「派生表已 drop,新代码只写 ad_raw」
- `tests/api/test_analytics_v2_contract.py`:`test_v2_dumps_audit_log_written`
  改写为 `test_v2_dumps_emits_ingest_log_line`（caplog 断言 logger 单行）;
  cleanup 从 5 张表缩为 1 张
- `tests/api/test_analytics_v2_errors.py`:`test_schema_invalid_persists_error_message_in_audit_log`
  改写为 `test_schema_invalid_persists_message_in_ingest_log`（caplog
  断言 logger.warning 行,消毒契约保留）;`_cleanup_audit_rows` fixture 删
- `tests/sync_worker/test_analytics_retention.py`:整文件删除
- `tests/sync_worker/test_scheduler_jobs_coverage.py`:`EXPECTED_JOB_INTERVALS`
  删 `analytics.retention`;`len(JOBS)` 断言 13 → 12
- `tests/sync_worker/test_main.py`:`_fmt_duration` parametrized fixture
  中删除 analytics.retention 注释行;`test_print_jobs_renders_table_without_db`
  去掉 retention 断言

### 文档

- `tech-doc/analytics/dump-architecture.md`:新增 D5「schema 3 → 1」决策;
  §3.1 数据流图（写 3 表 → 1 表）;§10 上线清单（监控项调整）
- `tech-doc/analytics/reorg-plan.md`:实施依据
- `AGENTS.md` §8 目录地图:analytics schema 标注「仅 ad_raw 1 表」
- `setup/analytics-sync.md`:表清单/retention 段落同步;文件树注释更新

### 范围外（明确未做,另记未决）

- `ad_raw` append-only 语义改造
- typed 事实表(`ad_product_daily` 等)
- view 改为基于事实表 / 加窗口参数
- Chrome 扩展协议变更

## 2026-09-05 (feature) — analytics.ad_product_links 视图（广告×商品关联 + 出单量/消耗，migration 0006）

从 `analytics.ad_raw` 的 `post_product_list` 原始 dump 派生「广告(计划) ↔ 商品(SPU)」关联视图。

### Storage / schema（alembic `0006_ad_product_links_view.py`）

- 新 DB 层视图 `analytics.ad_product_links`（无 HTTP 端点，同 `linkage.effective_product_links` 模式）：
  粒度 = (seller_id, advertiser_id, campaign_id, product_id) 一行，跨 ad_raw 已捕获全部 day 聚合。
- 指标：出单量合计 `order_sku_total`（SUM onsite_roi2_shopping_sku，TikTok Orders(SKU) 口径，
  含自然归因单）、广告消耗合计 `real_cost_total`（SUM mixed_real_cost）、出单 GMV 合计
  `order_value_total`；窗口元数据 observed_days/first_day/last_day；商品名/状态取最后观测日。
- ERP 富化：LEFT JOIN commerce.shops（external_account_id=seller_id）/ products_spu
  （external_product_id=SPU）带出内部 shop_pk / spu_pk（目录外为 NULL）。
- 健壮性：JSON 数值字符串先正则校验再 cast（脏值→NULL→0）；修复前无业绩字段的旧 dump
  保留关联行、业绩为 0。
- 语义/口径/查询示例：`biz-doc/analytics/ad-product-links-view.md`；测试
  `tests/analytics/test_ad_product_links_view.py`（5 tests）。验证：视图合计 vs ad_raw 直接
  求和 1207.17 / 139 完全一致；当前 337 对（228 广告计划 / 111 SPU）。

# tts-erp CHANGELOG

## 2026-09-02 (feature) — Analytics ingest dump architecture（migration 0005）

设计文档：`tech-doc/analytics/dump-architecture.md`（4 个 lock-in 决策）。配套 Chrome
扩展端（`tk-adv-cost-monitor`）同步 release（插件 repo commits 3d7ddb7 → 8975e4e）。

### 协议（breaking，随扩展同窗口发布）

- `POST /v2/analytics/sync/batches`（批量 records[]）→ **`POST /v2/analytics/sync/dumps`**：
  body 单 dump object（`{protocolVersion, requestId, scope, dump:{endpoint, method, day,
  campaignId, request, response, capturedAt}}`），严禁批量。响应 `data:{idempotencyKey, status}`。
- `GET /v2/analytics/sync/cursor` 从 work-list（`items[]/nextRequiredDay/pageSize/cursor/timezone`）
  降级为 **has-data 预检**：`?sellerId&advertiserId&endpoint&day[&campaignId]` →
  `data:{day, endpoint, storageKey, hasData}`（查 `ad_raw` existence）。
- `protocolVersion ∈ {1,2}`（2 = dump 单 object 形状）；idempotency key 算法不变（6 字段，page 固定 1）。

### Storage / schema（alembic `0005_ad_raw_per_unit_day.py`）

- 新 `analytics.ad_raw`：source-of-truth，1 dump 1 行完整 HTTP 交换（request/response JSONB）；
  UNIQUE `(seller_id, advertiser_id, endpoint, day, campaign_id)`；无 FK 到派生表；retention 不 purge。
- drop `ad_daily_pages`（page bitmap）、`ad_cursors`（cursor work-list 状态）。
- `ad_records` 去 `page` / `expected_page_count` 列；唯一约束改 5 元组
  `uq_analytics_records_unit_day`；`ad_daily_completeness` 只留 `captured_at`
  （existence 语义 = has-data 查 ad_raw）。
- `tts_erp_v2/db/models/analytics.py` 对齐 5 表现状（删 AdDailyPage/AdCursor，加 AdRaw；
  AdRecord/AdDailyCompleteness 去 page/expected/is_complete 列）。

### Code

- `tts_erp_v2/api/v2/analytics.py`：`post_dumps`（含 size 闸 2MB/256KB、storage_key 由
  `STORAGE_KEY_BY_PATH` 推导、SCHEMA_INVALID structured errors[]）+ `get_cursor` has-data。
- `tts_erp_v2/analytics/domain.py`：删 Record.page/expected_page_count/CursorEntry/CursorPage；
  新增 DumpPayload/DumpResult/HasDataResult。
- `tts_erp_v2/analytics/repository.py`：raw SQL（SQL_INSERT_RAW / SQL_INSERT_DERIVED /
  SQL_UPSERT_COMPLETENESS）+ `has_data()` + `upsert_dump()`；`STORAGE_KEY_BY_PATH` 常量。
- `tts_erp_v2/jobs/analytics_retention.py`：ad_raw 永久保留语义（docstring 同步）。

### Tests

- `tests_v2/api/test_analytics_v2_contract.py` 重写：cursor has-data（before/after dump）、
  `/dumps` 单 dump insert+duplicate、v1 404、unknown endpoint 400、audit log、envelope。
- `tests_v2/api/test_analytics_v2_errors.py`：payload 形状 records[] → dump 单 object；
  structured errors loc `['records',0,·]` → `['dump',·]`。
- `tests_v2/api/test_endpoints_index.py`：`/batches` 断言 → `/dumps`。

### Docs

- `tech-doc/analytics/dump-architecture.md` 新增（★ 事实源）；旧 5 文档（analytics-sync.md /
  architecture.md / compatibility.md / plugin-integration.md / openapi.yaml）加 superseded banner。
- `tech-doc/external-api.md`：analytics 节重写（/cursor has-data + /dumps 协议/示例/错误表/矩阵）。
- `setup/analytics-sync.md` v0.5.0 → v0.6.0 全量重写 dump 版。
- `AGENTS.md` §3 端点表 `/batches` → `/dumps` + 「已拆除」加 /batches 404 + ad_daily_pages/ad_cursors drop。

## 2026-09-02 (feature) — Analytics ingest v2 化 + /v2 路径硬切

设计文档：`tech-doc/analytics-v2-migration-plan.md`（4 个决策）。迁移实战见
`tech-doc/analytics/`（原 `analytics_sync/tech-doc/` 整包迁入）。

### Storage / schema

- 新 schema：`analytics`（原 `public.analytics_*` 6 表整包 `SET SCHEMA` + `RENAME` → `ad_*`，零拷贝）；
  alembic migration `0004_analytics_ad_schema.py`（dual-path：老库迁移 / 全新建表）。
- 新 SQLAlchemy 模型：`tts_erp_v2/db/models/analytics.py`（6 个表映射 `ad_records` / `ad_daily_pages` /
  `ad_daily_completeness` / `ad_cursors` / `ad_shop_timezones` / `ad_audit_log`）。
- 新 SQLAlchemy repository：`tts_erp_v2/analytics/repository.py`（语义与原 `analytics_sync/pg_repositories.py` 完全一致；
  `FOR UPDATE` → `with_for_update()`，`ON CONFLICT DO NOTHING` → `pg_insert().on_conflict_do_nothing()`）。
- 新 sync-worker daily job `analytics.retention`（`tts_erp_v2/jobs/analytics_retention.py`）：
  删 `analytics.ad_records` `received_at < 90d` + `analytics.ad_audit_log` `created_at < 30d`（env 可调）。
- `tts_erp_v2/db/base.py::SCHEMAS` += `"analytics"`。

### API

- 新路由：`tts_erp_v2/api/v2/analytics.py`（`APIRouter(prefix="/v2/analytics/sync")`），
  handler `get_cursor` / `post_batches`；协议 envelope 与响应形态与 v1 byte-equivalent。
- `tts_erp_v2/app.py`：用新 `analytics.router` 替换原 `analytics_sync_router` 挂载。
- `tts_erp_v2/middleware/auth.py`：路径分类 `/v1/analytics/sync/*` → `/v2/analytics/sync/*`（readwrite）。
- **硬切**：无 `/v1` 别名（单挂载决策）；`/v1/analytics/sync/*` 同 release 下线为 404。
  Chrome 扩展必须同窗口发布只改 path 后缀的更新，否则 404。

### Tests

- 新 `tests_v2/api/test_analytics_v2_contract.py`（11 契约测试：auth 矩阵 401/403/200、
  `/v1` 404、`sellerId/advertiserId` 回显、inserted/duplicate/mismatch 三态）。
- 新 `tests_v2/api/test_analytics_v2_errors.py`（从 `test_analytics_sync_errors.py` 移植并改 /v2）；
  删 autouse `ALTER TABLE` fixture（alembic 单轨接管 schema）。
- 删 `tests_v2/api/test_analytics_sync_mount.py` + `tests_v2/api/test_analytics_sync_errors.py`。
- `tests_v2/api/test_endpoints_index.py` + `scripts/demo_analytics_sync_client.py` 路径改 /v2。

### 拆除

- 删 `analytics_sync/` 包（README / app.py / domain.py / pg_repositories.py / schema.sql / migration_v2.sql / retention.sql）。
- `scripts/demo_analytics_sync_client.py` 路径引用：`analytics_sync/tech-doc/...` → `tech-doc/analytics/...`。
- `api_keys.py --scopes` help 文本：「analytics_sync per-seller restriction」 → 「analytics ingest per-seller restriction」。

### Docs

- `AGENTS.md` §3 端点表加 `/v2/analytics/sync/{cursor,batches}` 行 + §3「已拆除」加 /v1 404 条 + §9.5 更新 + §6 文件表加 `tts_erp_v2/analytics/` 行 + `tech-doc/analytics/` 引用。
- `tech-doc/external-api.md` §3 analytics 节全段 /v2 化（mount/路径/env var/curl/稳定性矩阵）。
- `setup/analytics-sync.md` v0.4.0 → v0.5.0 重写头部 + 状态表 + 文件布局 + 表清单 + 端点列表。
- `tech-doc/analytics/`（原 `analytics_sync/tech-doc/`）5 文件 `/v1` → `/v2`、表名 `analytics_*` → `analytics.ad_*`、
`层架构引用更新、`cron` retention → `sync-worker job`。

## 2026-08-31 (feature) — Procurement console redesign + SPU image storage（branch `feature/procurement-ui`）

设计文档：`tech-doc/procurement-ui-redesign.md`（design tokens + API contracts）。

### Backend — MinIO + `/v2/spu-images/*`

- 新 `tts_erp_v2/storage/minio_client.py`：`MinioClient.from_env()` 包装 minio SDK
  （presigned PUT/GET、bucket 自举、head/stat）；配置走 `.env` `MINIO_*` 块。
- 新 `tts_erp_v2/api/v2/spu_images.py`：
  - `POST /v2/spu-images/upload-url`（readwrite）— 签发 presigned PUT，落 `awaiting_upload` 行
  - `POST /v2/spu-images/{id}/confirm`（readwrite）— head 校验对象存在后置 `ready`
  - `GET /v2/spu-images[?spu_pk=]`（readonly）— ready 列表 + presigned GET URL
  - `DELETE /v2/spu-images/{id}`（readwrite）— 软删
  - Cookie 会话下的 mutation 带 CSRF guard（与 manual-costs POST 同款）
- 新 `tts_erp_v2/storage/schema_storage.sql`：`procurement.spu_images` 表。
- **fix**: `GET /v2/spu-images` 不带 filter 时 `CAST(:cp_id AS bigint)` 修
  `AmbiguousParameter` 500（回归测试 `test_list_without_spu_pk_returns_all_ready`）。
- `pyproject.toml`：补 `[project]` 依赖清单（含 `minio>=7.2`），uv/pip 可解析。

### Frontend — operator console 重做

- `tts_erp_v2/api/v2/pages.py` 重写：`/v2/pages/manual-costs` 改为壳页面
  （shop switcher + 三 tab 工作台：Needs cost / Needs photo / Recently filed）。
- 新 `tts_erp_v2/static/{css/console.css,js/console.js}`：`/static/` 挂载
  （readonly 级 auth，匿名 401；浏览器走 cookie 登录流）。
- `middleware/auth.py`：`/static/` 归 readonly 前缀；`/v2/spu-images*` 按
  GET=readonly / mutation=readwrite 分类。

### 顺带修复

- **`/endpoints` 懒加载路由**：FastAPI ≥0.141 `include_router` 变 lazy，
  `app.routes` 里是 `_IncludedRouter` 占位符导致 introspection 丢路由/出 `None` path；
  本分支初版用 `_iter_resolved_routes()`，merge 时统一到 master 的
  `_walk_v2_routes()`（前缀拼回 + 递归，2026-08-30 fix 条目）。

### 测试

- 新 `tests_v2/storage/test_minio_client.py`（mock MinIO）、
  `tests_v2/api/test_spu_images.py`（20 tests，fake MinIO + 真 PG）、
  `tests_v2/api/test_manual_costs_page_v2.py`、`test_missing_cost_photos.py`。
- `tests_v2/api/test_pages.py` 适配新壳页面 + `/endpoints` 懒加载回归。

## 2026-08-30 (fix) — analytics_sync 400 SCHEMA_INVALID 盲区的可观测性修复 + 客户端协议错配定位

### 事故

2026-08-30 17:39 ~ 19:18 UTC，Chrome 扩展真实流量（key_prefix
`c8b767dafb9432b6` / `af2690c861ab0385`，seller `7494763368967603447`）
`POST /v1/analytics/sync/batches` **100% 返回 400 SCHEMA_INVALID**；
cursor GET 同期全部 200。audit 表只有 error_code 没有 Pydantic message，
access log 只有 body 字节数 → 服务端无法定位是哪个字段校验失败。

### 根因（客户端协议错配）

客户端 `chrome-plugins/ads-data-sync/src/core/analytics-sync-v2.ts` 的
`AnalyticsSyncRecord` 只发 `{idempotencyKey, storageKey, campaignId, day,
page, expectedPageCount, payload}`；服务端 `RecordIn` 另外要求
`endpoint / method / response / source / capturedAt` 五个 required 字段。
`BatchRequest.model_validate()` 抛 5×"Field required" → 400。
字节数吻合：客户端结构 385 B + 真实 payload ≈ 194 B = 观测到的 579 B。
另发现 cursor 协议同样错配：客户端 `parseCursor` 要求 items 元素含
sellerId/advertiserId，服务端 items 只含
`{storageKey, campaignId, latestCompletedDay, nextRequiredDay}` →
客户端永远拿到 null cursor（不导致 400，但导致永远全量重采）。

### 修复（本次提交）

- **`analytics_sync/app.py::_audit_and_error`**：所有 4xx/413 拒绝额外写一行
  stderr 诊断（`[analytics-sync] reject status=… code=… request_id=…
  key_prefix=… message=<截断 500 字符的 Pydantic message>`）。message 只含
  字段名 + Pydantic input_value 截断值，不回显 body / Authorization。
- **新增 `tests_v2/api/test_analytics_sync_errors.py`**（5 个用例）：
  SCHEMA_INVALID / MALFORMED_JSON / UNSUPPORTED_PROTOCOL_VERSION /
  PAYLOAD_TOO_LARGE 都写 stderr；且不泄露 token / body。

### 后续（部分已落地）

- ~~cursor items 需补 sellerId/advertiserId~~：**已于 2026-08-30 修复**
  （`get_cursor` items 每行回显请求 scope；测试
  `test_cursor_items_include_scope_fields` 锁定契约；客户端 parseCursor
  保持严格校验不放宽）。
- 客户端 record schema 对齐在 `chrome-plugins/ads-data-sync` **0.2.0**
  落地：上传完整 RecordIn 字段（endpoint/method/requestBody/response/
  source/capturedAt/schemaVersion），旧本地记录（仅有 payload）标记
  LOCAL_RECORD_INVALID 不再重试，等待重采覆盖。

## 2026-08-30 (refactor) — analytics_sync 统一到 tts-erp v2：standalone :9878 退役

把 Chrome 扩展 (`tk-adv-cost-monitor`) 上传 / cursor 后端从独立 FastAPI 进程
(:9878, `uvicorn analytics_sync.app:app`) 合并到 tts-erp v2 进程 (:9877)，
统一鉴权 + 限流 + 访问日志，简化部署 / 调试 / 监控。

### 触发事故

- 2026-08-30 14:59:29 ~ 15:00:25 UTC（56 秒窗口）Chrome 扩展发起 54 次
  `GET /v1/analytics/sync/cursor`，**全部 404**。nginx access.log 显示
  `up="-"` `rt=0.000`，落到了 daqiangcn 静态站 fallback → 912 字节 HTML。
- 根因：`nginx services.conf` 缺 `/v1/analytics/sync/` location；
  即使补上 nginx 转发到 :9878，standalone 进程的 nginx 反代也不存在。
- 客户端 syncBaseUrl 一直是 `http://daqiang.nat100.top/v1/analytics/sync/cursor`
  （按 `:9878` 直连的 standalone 设计填的），**nginx 没接管 = 100% 失败**。

### 修复

- **`tts_erp_v2/app.py`**：`include_router(analytics_sync_router, prefix="/v1/analytics/sync")`，
  router + handlers 从 `analytics_sync/app.py` 导入。v2 `AuthMiddleware` /
  `RateLimitMiddleware` / `AccessLogMiddleware` 统一接管。
- **`setup/nginx/conf.d/services.conf`**：新增 `location /v1/analytics/sync/`
  → `proxy_pass http://127.0.0.1:9877`（无尾 `/`，URI 原样透传避免前缀被剥）。
- **e2e 验证**：公网 `http://daqiang.nat100.top/v1/analytics/sync/cursor?...`
  带 admin token 三种 storageKey 全部 HTTP 200，envelope 标准。

### 退役内容

- **进程**：standalone :9878 (PID 3269937, `uvicorn analytics_sync.app:app`)
  kill，端口释放。
- **模块**：
  - `analytics_sync/auth.py`（`SyncAuthMiddleware` + 5 个 helper）删除
  - `analytics_sync/rate_limit.py`（`SyncRateLimitMiddleware` + sliding window helper）整文件删除
  - `analytics_sync/app.py:736-789`（`app = _FastAPI(...)` + 独立 `/healthz` + `/endpoints` + standalone 中间件挂载）删除
- **测试**：`analytics_sync/tests/`（9 文件 + conftest.py）整目录删除
  —— 测试对象是已死的 standalone app + 中间件。新 `tests_v2/api/test_analytics_sync_mount.py`
  （7/7 passed）覆盖挂载 + 鉴权契约，handler 业务逻辑靠真实 Chrome 扩展流量验证。
- **孤儿**：`pytest.ini`、`conftest.py`（无 tests/ 后无引用）、`.pytest_cache/`、`.ruff_cache/` 删除。
- **文档**：`setup/analytics-sync.md` 顶部 + "当前状态" + "文件布局" 重写；
  `analytics_sync/README.md` 全量重写；
  `analytics_sync/tech-doc/analytics-sync.md` "## 7. Curl examples" + "## 10. Deployment" 重写。
- **DB schema 不动**：5 张 `analytics_*` 表（`analytics_records` /
  `analytics_cursors` / `analytics_shop_timezones` / `api_keys` /
  `analytics_audit_log`）继续被路由使用。

### 不变项

- DB 表、5 张表结构、API 契约（`/v1/analytics/sync/cursor` GET + `/batches` POST）、
  鉴权语义（Bearer / X-API-Key，api_keys 表，readwrite 角色）、限流策略
  （每 key 100/min）、错误 envelope（`{code, message, requestId, retryable}`）、
  Chrome 扩展 syncBaseUrl、nginx 公网入口 —— 全部不变。
- v2 的 AuthMiddleware 早就知道 `/v1/analytics/sync/*` 是 readwrite
  （`tts_erp_v2/middleware/auth.py:91`），无需改鉴权分类。

### 客户端

- Chrome 扩展 **零改动**。原 syncBaseUrl
  `http://daqiang.nat100.top/v1/analytics/sync/cursor` 现在直接 200。
- nginx 上**保留** `/tts/v1/analytics/sync/` 兜底（走 `/tts/` 旧 location，
  剥离前缀后转 `/v1/analytics/sync/cursor` 给 :9877）—— 给任何习惯了
  tts-erp 命名空间的老脚本。

## 2026-08-30 (fix) — v2 `GET /endpoints` 递归修：暴露 `_IncludedRouter` 子路由

`tts_erp_v2.app:build_app()` 通过 `include_router()` 挂载 7 个业务子路由
（commerce / linkage / reporting / pages / llm_context / auth / analytics_sync）。
FastAPI 0.141 把每个 `include_router` 子包成一个 lazy `_IncludedRouter`，
其上没有 `path` 属性——原 `endpoints_index` 用 `for r in app.routes: if not path: continue`
扁平遍历，**静默丢掉所有业务路由**，只返 FastAPI 元端点（`/docs`,
`/openapi.json`, `/healthz`, `/endpoints` 等 6 条，count=6）。运维
通过 `GET /endpoints` 看到的"v2 API 表面"完全是误导：以为挂了 v2 路由
实际一个都没列出来。

### 修复

- **`tts_erp_v2/app.py`**：抽出 `_walk_v2_routes(routes, prefix="")` 纯 helper，
  递归进入 `_IncludedRouter.original_router.routes`，并从
  `_IncludedRouter.include_context.prefix` 拼回 `include_router(prefix=...)`
  传的 prefix —— FastAPI 0.141 在子 APIRoute 上**不** 应用 prefix，
  prefix 只存在 `_IncludedRouter.include_context`。`endpoints_index` 改为
  `sorted(_walk_v2_routes(app.routes), key=lambda x: x["path"])`。
- **`tests_v2/api/test_endpoints_index.py`**：6 个测试覆盖
  - 200 status
  - v2 业务路由在返回中（commerce / linkage / reporting / pages / llm / auth）
  - **analytics_sync 在返回中**（`/v1/analytics/sync/cursor`、
    `/v1/analytics/sync/batches`，regression guard）
  - 含 path-param 的路由（`{order_id}`、`{issue_id}/resolve`）
  - `count == len(endpoints)` 内部一致性 + `count > 6` 防止倒退
  - 排除无 path 的内部 entry

### 验证

- 修后 `GET /tts/endpoints` 返 `count: 34`，含 7 个 analytics_sync / v2
  路由 + 全部含 `{param}` 的路由。
- v2 api 测试套件 69 passed（63 原有 + 6 新 endpoints_index），无回归。

## 2026-08-27 (fix) — Review remediation Waves 1-4（可用性 / 数据正确性 / schema / 测试基建）

按 `plans/review-remediation-2026-08.md` 执行的三路 review 修复：

### Wave 1 — 止血（commit `ec9bf77`）

- **auth**: `lookup_role` 移出 event loop（`anyio.to_thread`）；无效 key 负缓存（20s）；
  PG down fail-closed 503；被拒请求计入限流桶（暴力枚举不再免限流）。
- **analytics_sync**: `post_batches` 不再在 event loop 上跑同步 psycopg；`nextCursor`
  在真分页落地前恒 null（之前是假分页，客户端会死循环）；`fetch_timezone` 改 read-first。
- **persist_shop**: 不再把明文 `shop_cipher` 落库（凭证单源在 oauth-receiver）。
- **_get_creds**: `TokenError.status` 透传（404=店铺未授权 vs 502=上游故障）；
  意外错误返回固定文案 502，内部细节只进日志。
- **sync_cron**: `http_json` 补捕 `TimeoutError/OSError`（一次慢调用不再炸整轮）；
  oauth DB down 退出码 4（之前静默 exit 0）；物流 plan 分批 ≤80 单/次；`flock` 防重叠。
- **tts_business**: payments/statements 中途分页失败置 `error`（窗口不再越过缺口前进）；
  裸 `int()` 全部改 `_body_int`/`_body_int_opt`（脏输入不再 500）。
- **sync_logistics_tracking**: PG advisory lock（重叠返 409）+ `max_per_run` 硬上限 100 + jitter。

### Wave 2 — Schema housekeeping（commit `aa53b9d`）

- 删重复/冗余索引 ×4；`idx_logistics_tracking_overseas` 改部分索引。
- 新增 `idx_orders_shop_ct`（/db/orders 分页走 Index-Only-Scan）、
  `idx_order_shippings_tracking`（cron 每 10 分钟热路径不再全表扫）、
  `idx_orders_status`、`idx_stmt_txns_type`。
- `DROP TABLE logistics_events`（死表，零写入方）；`DROP COLUMN shops.shop_cipher`。
- 清理两库互灌：tts_erp 库的空 `oauth_tokens` + oauth_receiver 库的 23 张空业务表。
- `sync_log` retention 单一入口（trigger 委托 `cleanup_sync_log(60)`）。
- crontab 挂上 analytics_records(90d) / analytics_audit_log(30d) 每日清理。
- `regen_schema.py` 拆成 `schema_oauth.sql` + `schema_tts_erp.sql`（不再一文件灌两库）。
- `order_shippings.raw` 只存物流子集（不再复制整单 JSON）。

### Wave 3 — 结构性（commits `0566571`, `8af34f9`, `ed81fc5`）

- **连接池**：`db_connect()` 走 `psycopg_pool.ConnectionPool`（min 1 / max 10），
  同步批次不再每查询一次 connect/close。
- **tiktok_request**：429/5xx/网络错误有界重试（指数退避+jitter，上限 2 次）；
  4xx 立即返回不浪费配额。
- **/sync/order/{id}**：从 501 变为实现（body.shop_id → 本地 orders 表 → 404）。
- **_invoke_legacy_sync**：`getattr` + 501 兑底（legacy 方法被删时不再 KeyError→500）。
- 删 `PlainHttpClient`（生产零引用）。

### Wave 4 — 杂项 + 测试基建

- **miaoshou**：`_call` 的 HTTP 错误透传真实状态码（`http.client` 不抛异常，
  之前 CDN 502 被误报为 code=0 "无法解析响应"）。
- **analytics_sync scope_grants**：同维度多值改 OR 语义；未知 scope 前缀 fail-closed。
- **DEFAULT_TIMEZONE** 移到 `analytics_sync/domain.py` 单一来源。
- **oauth state**：`not_registered`/`mismatched` 仍放行的行为加了 WARNING 日志 +
  注释说明（若需严格 CSRF 拦截，改 `handle_callback` 一处即可）。
- **测试基建**：修 `test_oa_uath_receiver_url_removed.py` 弹 sys.modules 不恢复
  导致的 pytest session fixture 级联崩溃（tdd/ 从 129 passed + 372 errors 修复为
  491 passed + 0 errors）。

## 2026-08-27 (feat) — analytics_sync protocolVersion 2：页级完整性 cursor

### 问题

v1 协议下，客户端某天只传了第 1 页（共 3 页）时，服务端也会把该日标记为 complete
并推进 cursor —— 多页数据尚未收齐就错误推进每日 cursor，后续页永远丢失。

### 修复（v2 协议）

- **每日数据单元** = `sellerId + advertiserId + storageKey + campaignId + day`。
  仅当 `analytics_daily_pages` 中 `1..expectedPageCount` 全部页码齐后才标记 complete。
- 新表 `analytics_daily_pages`（页级证据）+ `analytics_daily_completeness`
  （每日聚合状态，避免 cursor 查询扫原始 JSON）；`analytics_records` 加
  `expected_page_count` 列；`analytics_cursors` 加 `first_seen_day` 锚点列。
- `latestCompletedDay` 只能是连续完整日期前缀的最后一天，不允许跳过较早缺失日期；
  `nextRequiredDay` 为服务端权威值（latest+1 / first_seen / bootstrap 三段式）。
- 页数冲突（同一单元同日 expectedPageCount 不一致，批内或跨批）→ 逐条 rejected
  `PAGE_COUNT_CONFLICT`，`retryable=false`，cursor 不动。
- 记录写入 + 完整性标记 + cursor 推进在**同一事务**内完成。
- **v1 兼容**：v1 请求继续接受，每条记录视为 implicit `expectedPageCount=1`（单页日），
  行为与旧版一致；v1 记录无法把 v2 声明的多页日错误标记为 complete（会撞
  PAGE_COUNT_CONFLICT）。幂等键算法不变（expectedPageCount 不入键）。

### Files changed

- `analytics_sync/migration_v2.sql` (new) — 幂等迁移 + 存量数据回填
- `analytics_sync/schema.sql` — 新表 + 新列 + 六列组合索引
- `analytics_sync/domain.py` — Record 加 `expected_page_count` / `protocol_version`
- `analytics_sync/pg_repositories.py` — upsert 重写：页跟踪 + 完整性重算 + 连续前缀 cursor
- `analytics_sync/app.py` — 接受 protocolVersion 1/2，v2 校验，`result.rejected` 合并
  （修复仓库层 rejected 被静默丢弃的 bug）
- `analytics_sync/tests/test_protocol_v2.py` (new) — 19 个 v2 验收测试（含并发）
- `analytics_sync/tests/test_batches.py` / `test_concurrency.py` — 适配新签名与连续前缀语义
- `analytics_sync/tech-doc/openapi.yaml` / `analytics-sync.md` / `plugin-integration.md`
  / `compatibility.md` — v2 契约与 v1↔v2 兼容策略文档
- `tdd/test_analytics_sync_integration.py` — unsupported-version 测试改用 99

## 2026-08-25 (fix) — /healthz 撒谎 + tts_erp.shops 空 + schema.sql 严重过时

### 三件据 drift 被发现（读 /db/orders 时磕到）

1. **`/healthz` 报 `oauth_receiver.token_count: 0`，但 `oauth_tokens` 表实际有 2 行。**
   - 根因：`oauth_receiver_router._oauth_receiver_section()` 用了 `len(oc._token_history)`（进程内 deque，记最近 N 次 token-exchange），不是 DB 行数。重启后 deque 为空 → 监控永远错报。
   - 修复：加 `oauth_receiver_core.db_count_shops()` 走 `SELECT COUNT(*)`，healthz 改用这个。

2. **`tts_erp.shops` 表 0 行，但 `oauth_receiver.oauth_tokens` 有 2 行（Bridge nook VN + MOCK_SHOP_12345 US）。**
   - 根因：legacy `tts_erp.py._proxy_order` 每个订单详情 GET 后会调 `persist_shop`。FastAPI 接管订单路由后**完全没继承这一步**——shops 表从未被回写。
   - 修复：
     - `tdd/_backfill.py` 加 `backfill_shops_from_oauth()`（幂等）
     - FastAPI startup lifespan 自动跑一次
     - `POST /admin/shops/backfill`（admin 角色）手动触发
     - `_tiktok_proxy` 在 `persist_order_on_get=True` 路径上**也调** `persist_shop`（双保险）

3. **`schema.sql` 严重过时**：实际 DB 有 **24 张表**（schema.sql 只列了 16），缺 `analytics_*/logistics_*/statement_transactions` 等 7 张；`orders` 表列名是 `order_status_name`（AGENTS.md 写的 `order_status` 是错的）。
   - 根因：手写 schema.sql，没人维护。
   - 修复：`scripts/regen_schema.py` 从真实 PG 拉 schema，自动转 IF NOT EXISTS 幂等化、剔 \restrict 串、合并双 DB dump。
   - 验证：fresh DB clean apply 0 errors；二次 apply 报错都是 "already exists"（PG 限制 ADD CONSTRAINT 不支持 IF NOT EXISTS，header 已说明）。

### Files changed

- `tdd/oauth_receiver_core.py` — 加 `db_count_shops()`
- `tdd/oauth_receiver_router.py` — healthz 用 `db_count_shops()`
- `tdd/_backfill.py` (new) — `backfill_shops_from_oauth()` + `run_startup_backfill_if_configured()`
- `tdd/tts_erp_fastapi.py` — lifespan hook + `POST /admin/shops/backfill` + `_tiktok_proxy` 补 `persist_shop`
- `scripts/regen_schema.py` (new) — schema.sql 重新生成脚本
- `schema.sql` — 重新生成（1143 行，含两个 DB 的全部 24 张表）
- `tdd/test_healthz_token_count_fix.py` (new) — 4 个 healthz 测试
- `tdd/test_shops_backfill.py` (new) — 3 个 backfill 测试
- `tdd/test_tts_erp_routes.py` — 路由计数 54→55

## 2026-08-24 (fix) — sync_cron 每 10 分钟死循环 2h9min (Wave 3 Slice 2 遗留 bug)

### Symptom

`cron.service` 状态、`*/10 * * * *` crontab、`run_sync_cron.sh` wrapper 全部正常,服务也在跑,**但 `sync_log` 2 小时 9 分钟没新条目**。cron 每轮都死于第一步:

```
discover_shops failed: /shops failed: {'_http_status': 404, '_error': True, 'detail': 'Not Found'}
```

`/healthz` / `/endpoints` / `/db/*` 都 200,只有 `sync_log` 停摆——**外部不可见**。

### Root cause

Wave 3 Slice 2 (2026-08-18 批次) 删除了 `GET /shops`、`/shops/<id>`、`/token/<id>` 三个 oauth-receiver 代理路由,改为 in-process 调 `oauth_receiver_core.db_list_shops()`。**`sync_cron.py:discover_shops()` 没跟着改**——还在调 HTTP `/shops`。路由不存在 → 404 → cron 异常退出。

### Fixed

- **`sync_cron.py:discover_shops(base_url, api_key)` → `discover_shops(provider="tiktok")`**：直接 in-process 调 `oauth_receiver_core.db_list_shops(provider="tiktok")`,与 FastAPI app 走相同代码路径。`is_db_ok()` 为 False 时返 `[]`(不抛异常)。
- **`sync_cron.py` top imports**: 加 `tdd` 到 `sys.path`(让 `oauth_receiver_core` 可导入)+ 模块加载前注入 `OAUTH_DB_URL/OAUTH_DB_ENCRYPTION_KEY/OAUTH_DB_TABLE` 到 `os.environ`(否则 oauth_receiver_core 的 module-load `db_init()` 拿到空 env → `_db_ok=False` → `db_list_shops` 返 [])
- **`sync_cron.py` `main()`**: 调 `discover_shops()`(不再传 `base_url, api_key`)
- **`tdd/tts_erp_fastapi.py` `/endpoints` 文档**: 把误导的 `passthrough: [GET /shops, /shops/<id>, /token/<id>]` 改为 `passthrough_removed`, 指向 `oauth_receiver_core` 的 in-process 函数(这三条路由自 Wave 3 Slice 2 起就不存在,文档残留至今才暴露)

### Tests

- `pytest tdd/test_sync_cron_discover.py` → **6 passed** (新增测试,锁定 `discover_shops` 不发 HTTP、走 oauth_receiver_core,过滤 provider,DB 不可达返 [])
- 手动 `python3 sync_cron.py`: discovered **2 shops** (`MOCK_SHOP_12345` + `7494763368967603447`), 7 个 sync plan (orders/payments/statements/returns/cancellations/logistics/stmt_txns) 全部执行完毕, `sync_log` 新增 20+ 条 `status=ok` 行
- `bash restart.sh` + `curl /healthz` → `db_ok: true, last_sync_at: 1787570517` ✓
- `curl /endpoints | jq '.passthrough_removed'` → 3 行迁移说明 ✓

### Notes

- **Cron 调度本身无需改**:`*/10 * * * *` + `run_sync_cron.sh` 调 `python3 sync_cron.py`,下个 tick 自动 pick up 新代码,**无需 restart 任何服务**
- 下一次 cron 触发后, `logs/tts-erp-cron.log` 应该出现 `discovered N shop(s)` 而不是 `discover_shops failed: /shops failed: 404`
- `MOCK_SHOP_12345` 同步仍然 502 `Invalid shop_cipher`(预期内, mock shop 在 oauth_tokens 里没真正 token)
- 同样路径(`oauth_receiver_core` in-process)的 FastAPI app 一直 OK, 因为它在 cwd=`tdd/` 下启动且 `EnvironmentFile` 自动注入所有 `OAUTH_*` env; cron 需手动 `setdefault`,详见 commit diff

## 2026-08-24 (chore) — 妙手/万师傅 命名统一 + pyrightconfig.json 修复

### Changed

- **统一称呼为「妙手」**（消除项目里的「万师傅」残留，与 `miaoshou/` 目录名对齐）：
  - `miaoshou/__init__.py` / `miaoshou/miaoshou_client.py` / `miaoshou/miaoshou_signing.py` / `miaoshou/endpoints/__init__.py`：docstring + 注释中的「万师傅」→「妙手」
  - `tdd/tts_erp_fastapi.py`：§miaoshou 区段注释
  - `AGENTS.md`：§10 章节标题（"万师傅 / 妙手开放平台" → "妙手开放平台"）+ §6 文件清单表行
- **`pyrightconfig.json`**：`include` 列表里的 `"wanshifu"` → `"miaoshou"`（**功能性 bug 修复**：之前指向不存在的目录，pyright 完全没扫 miaoshou 包，顺 import 链也没扫 `tdd/tts_erp_fastapi.py`）

### Notes

- `wanshifu` 仅保留在合法上游 URL（`openapi.wanshifu.com` / `user.wanshifu.com` / `test-user.wanshifu.com`）
- AGENTS.md §10 line 355 的自指注释（"不再 `wanshifu`"）保留 —— 它说的是历史重命名本身

## 2026-08-24 (fix) — tts_erp_fastapi.py 3 处 latent bug

修 pyrightconfig.json 后 pi-lens 终于扫到了 `tdd/tts_erp_fastapi.py`，暴露了 3 个先前被遮蔽的问题。

### Fixed

- **`TTS_ERP_PORT = int(os.environ.get(...))` (L118)** — 加 try/except ValueError fallback 到 `9877`，env 配错不再让启动崩溃
- **`_encode_cursor` (L447)** — `int(create_time)` + `json.dumps()` 包 try/except `(TypeError, ValueError)` → 返 None，cursor 编码失败不再 500
- **`_invoke_legacy_sync` (L1419)** — 签名 `-> dict` 改为 `-> dict | JSONResponse`，error 路径返 `JSONResponse(500, ...)` 而非 `{"_error": ...}`

### Behavior change（明确告知）

- **5 个 miaoshou sync/db 端点**的 error 响应从 `200 + {"_error": "..."}` 改为 `500 + {"_error": "..."}`：
  `/sync/miaoshou_shops`、`/sync/miaoshou_price_templates`、`/sync/miaoshou_collect_box_details`、`/sync/miaoshou_move_collect_tasks`、`/db/miaoshou_shops`
- 之前 200+_error 是 bug（应返 4xx/5xx），现在 500 是诚实表达。已通过 restart.sh + 烟雾测验证。

### Tests

- `pytest tests/miaoshou/` → **179 passed**
- `pytest tdd/test_tts_erp_routes.py tdd/test_miaoshou_routes.py tdd/test_auth.py` → **59 passed, 1 skipped**（已知限制，AGENTS.md 已记）
- `restart.sh` + `GET /healthz` → OK
- 烟雾测：`GET /db/miaoshou_shops`、`POST /sync/miaoshou_shops`、`GET /db/orders?cursor=...` → 200

## 2026-08-23 (doc fix) — analytics_sync 幂等键参考向量勘误

### Fixed

- **plugin-integration.md 和 setup/analytics-sync.md 的 idempotencyKey 参考向量从 `ce1ba2e1...` 改为 `73b716cc...`**（原是手抄错误，算法从未产出过 `ce1ba2...`）
- **domain.py**: `canonical_json_for_key` 和 `compute_idempotency_key` 接受 `page: int | str`（`1` 和 `"1"` 同等），更贴近插件端实际行为
- **domain.py**: `import hashlib` / `import json` 移到文件顶部
- **tests/test_idempotency.py**: 新增 6 条锁住正确算法的回归测试（reference vector、page int/str 等价、单字段变化、trim 幂等）

### Added

- `test_reference_canonical_json_is_locked` —— 锁住 canonical JSON 字节串
- `test_reference_hash_is_locked` —— 锁住标准输入下 sha256 = `73b716cc...`，并反向断言不是 `ce1ba2...`（防意外回滚）
- `test_page_int_and_string_produce_same_hash` —— `page=1` ≡ `page="1"`
- `test_page_changes_produce_different_hashes` —— 分页键必须随 page 变
- `test_each_field_change_produces_different_hash` —— 5 个字段任一变化都改 hash（防止静默忽略）
- `test_string_fields_are_trimmed_before_hashing` —— 首尾空格不影响 hash

### Migration

- **不需要迁移**：算法从未变过，只是文档里手抄的参考值错了。没有生产数据使用错 hash。协议版本无需升级。

## 2026-08-23 (refactor) — analytics_sync 鉴权统一到 api_keys

### Changed

- **删除 `analytics_sync_tokens` 表**（2026-08-23 早上刚加的，现在不用了）；Chrome 扩展的 sync token 直接走 tts-erp 的 `api_keys` 表
- **`api_keys` 表加 `scopes TEXT[]` 列**（`NOT NULL DEFAULT '{}'`），保留空的 scope 表示无限制；Chrome 扩展用 `--scopes "seller:<id>"` 拿受限 token
- **`api_keys.py` CLI 加 `--scopes` flag**；`rotate` 默认拷贝老 key 的 scopes
- **`tdd/auth.py::lookup_role` 返回值改为 `(role_level, scopes_tuple)`**，在 `scope["api_key_scopes"]` 暴露给下游；Cache key/value shape 同步调整
- **`tdd/auth.py::required_role`**：analytics_sync 路径要求 `readwrite`（默认 fallback 是 admin，太严）
- **`analytics_sync/auth.py`** 重写：从 `tdd.auth.lookup_role` 读 api_keys；保留 scope_grants / X-Sync-Token 兼容
- **`analytics_sync/app.py`** 的所有 handler 改用 `request.scope["api_key_scopes"]` 做 per-seller 校验
- **路由挂载策略**：保留 analytics_sync 独立 FastAPI 进程（端口 9878），不再尝试 mount 到 tts_erp_fastapi（后者当前未跑通，跨进程共享 auth 中间件成本高，独立进程 + 共享 api_keys 表 已足够达到"统一鉴权"）

### Removed

- `analytics_sync/analytics_sync_tokens.py` CLI（用 `api_keys.py create` 替代，文档里说明 `--scopes` 用法）
- `analytics_sync_tokens` 表的所有引用

### Tests

- 63/63 仍通过：所有 fixture 改为向 `api_keys` 插 readwrite 临时 token

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
- 回滚：DROP 6 张新表 + v_order_recon + 各表 xls_列（merge_tiktok.sh 末尾有模板）
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
