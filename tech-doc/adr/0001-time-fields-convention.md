# ADR-0001: v2 表双时间字段约定(created_at + updated_at)

- **Status**: accepted
- **Date**: 2026-09-04
- **Deciders**: tts-erp backend
- **Scope**: tts-erp v2 全部 schema(commerce / analytics / integration / linkage / procurement / after_sales / finance / fulfillment / reporting / security)
- **Related**: AGENTS.md §2(schema 变更流程), §6(无灰度要求,本系统为后台运行可直切)

## 1. Context(背景)

### 1.1 现状

2026-09-04 审计发现 tts-erp v2 全部 42 张表里只有 2 张(4.8%)符合"双时间字段"约定:

```text
✅ 完全合规(2): integration.credentials, linkage.product_links
⚠ 缺 created_at(2): analytics.ad_shop_timezones, integration.sync_cursors
⚠ 缺 updated_at(25): commerce.sales_orders, integration.raw_records, ...
✗ 缺双时间(13): integration.sync_jobs, linkage.account_links, ...
```

### 1.2 问题

- **27 处 `synced_at` 引用 + 46 处 `updated_at` 引用**散落在 14+ 文件里
- `synced_at` 在 v2 schema 里**只在 INSERT 写**(`DEFAULT now()`,0 触发器),UPDATE 不变
- `integration.sync_cursors.updated_at` 同样不刷新(测试 `test_watermarks.py:120` 已假设它会刷新)
- 业务侧真实"最近一次 sync 看到这行"的时间 = `source_updated_at`(上游)+ `sync_jobs.finished_at`(sync_worker),**不在 synced_at**
- 报表 SQL `reporting.py:53 ORDER BY pol.synced_at DESC` 拿到的实际是"历史第一条",**不符合用户期望**的"最新"
- API 文档 `external-api.md:171-177` 暴露 `synced_at` 给 client,造成"什么时候更新了"的歧义

### 1.3 用户诉求

> "review 一下所有的表,我认为两个时间很重要,是每个表都必须有的:
> 1. 数据的创建时间,根据表的业务语义不同,可以叫 create_at 也可以叫 sync_at
> 2. 数据的更新时间,每次更新数据都要更新这个字段,可以设置为 db 自动更新"

无灰度,后台系统直接切换。

## 2. Decision(决定)

### 2.1 命名约定(全 v2 schema 统一)

| 字段名 | 语义 | 适用表 |
|---|---|---|
| `created_at` | 行首次入库时间(server 本地) | 大多数业务表 |
| `updated_at` | 行最近一次修改时间(server 本地,BEFORE UPDATE trigger 自动维护) | 任何会被 UPDATE 的表 |
| `synced_at` | 第三方数据首次入库时间(等效 `created_at` 但语义强调"sync 落地") | sync_worker 同步的表(`commerce.*` / `analytics.*` / `integration.*` / `procurement.*`) |
| `captured_at` | 上游 API 数据被截获时间 | `integration.raw_records` |
| `received_at` | server 收到请求时间 | 请求日志表 |
| `started_at` | job 启动时间 | `integration.sync_jobs` |
| `finished_at` | job 完成时间 | `integration.sync_jobs` |
| `source_created_at` | 上游数据自身的创建时间 | 任何引用第三方数据的表 |
| `source_updated_at` | 上游数据自身的更新时间 | 任何引用第三方数据的表 |

**规则**:
- 每张表**必须**有 `created_at`(或等效的 `synced_at` / `captured_at` / `started_at`)
- 每张表**必须**有 `updated_at`,由 DB BEFORE UPDATE trigger 自动维护
- 已有 `synced_at` 命名习惯的 sync 表**保留 `synced_at`**(语义对应"首次 sync 落地"),同时**加 `updated_at`** 表示"最近一次 sync 更新"
- 任何新表必须满足双时间字段

### 2.2 通用 trigger function

```sql
CREATE OR REPLACE FUNCTION public.fn_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

- 单一函数,所有 v2 表共用
- BEFORE UPDATE,自动覆盖 `updated_at`
- 应用层无需关心

### 2.3 旧数据初始化策略

- 新加的 `updated_at` 列:`DEFAULT now()` — 旧行初始化为 migration 执行时刻
- 旧行**没有真实"最近一次修改时间"**,初始化值是**最差情况的可接受近似**
- 历史 `synced_at` 保留不动(业务语义:首次入库时间)

### 2.4 API contract 同步

- API response 从 `synced_at` 改为 **`updated_at`**(或并存)
- `external-api.md` 同步更新
- 保持向后兼容(加字段不删字段)

### 2.5 SQLAlchemy model 同步

- 每个 v2 model 必须显式声明 `created_at` + `updated_at` 两个列
- `Base = declarative_base()` 提供 `default=func.now()` 工厂
- 新加的 `updated_at` 列加 `onupdate=func.now()`(虽然 trigger 兜底,但 ORM 层也写,保证应用直连 SQL 不走 trigger 时也正确)

## 3. Consequences(后果)

### 3.1 正面

- ✅ **42 张表**统一双时间字段,审计/排障/数据陈旧度检测有可靠依据
- ✅ `max(updated_at) FROM xxx` = "最近一次修改",**所有人用的语义一致**
- ✅ `test_watermarks.py:120` 的假设被 trigger 满足,无需改测试
- ✅ `reporting.py:53 ORDER BY synced_at` 行为**潜在改善**(如果同步保留 synced_at 不动,行为不变;如果业务改用 updated_at,语义变正确)
- ✅ 后续新表必须满足约定,降低 schema 漂移

### 3.2 风险

- ⚠ **`reporting.py:53`** 当前依赖 `synced_at` 是 INSERT 时间,**不是真实"最新"**;新设计下,如果只保留 `synced_at` 语义不变,行为不变;但**未来若改用 `updated_at`,业务行为会变** — 需业务确认
- ⚠ **API client** 已依赖 `synced_at` 字段;加 `updated_at` 不破坏,但需要文档同步
- ⚠ **migration 大、列多**(200+ 列 COMMENT ON COLUMN),一次执行可能较慢,需在低峰期执行
- ⚠ **8 个 schema 域同时改动**,失败回滚复杂 — 必须先在测试环境验证

### 3.3 兼容性

- 应用层**不破坏**:`synced_at` 字段保留(虽语义不变),`updated_at` 新加(默认 `now()`)
- v1 legacy `public.*` 表**不参与本次改动**(保持兼容 v1 客户端)
- 测试环境先跑 → 0 fail → 生产应用

## 4. Alternatives Considered(已考虑的替代方案)

### 4.1 不加 trigger,只在应用层显式 set `updated_at`

- ❌ 任何直连 SQL / DB 工具会绕过,数据陈旧
- ❌ 27 处引用都要审计,工作量大
- ✅ 选了 trigger 方案,一处定义全表受益

### 4.2 把 `synced_at` 改名 `updated_at`(语义升级)

- ❌ 破坏 27 处引用,需要全部改
- ❌ 业务语义混淆("sync 时间" vs "任意更新时间")
- ✅ 选了并存方案,`synced_at` 保留 + 加 `updated_at`

### 4.3 灰度发布(老库先不动,新表满足约定)

- ❌ 用户明确"不需要灰度,直接切换"
- ❌ 后台系统,可接受 brief downtime
- ✅ 选了直接切换

## 5. Migration Plan(执行计划)

1. **Phase 1**: ADR(本文件) — 锁定决策
2. **Phase 2**: TDD 红 — 写 `tests/db/test_time_fields_convention.py`
3. **Phase 3**: SQLAlchemy models 更新(每个 v2 表加 `updated_at` / `created_at` Column)
4. **Phase 4**: migration SQL
   - `public.fn_touch_updated_at()` 函数
   - 40+ 张表加列 + BEFORE UPDATE trigger
   - 200+ 列 `COMMENT ON COLUMN`(语义清晰无歧义)
5. **Phase 5**: `python3 scripts/regen_schema.py` 同步 `schema_tts_erp.sql`
6. **Phase 6**: 跑 `bash scripts/test.sh fast` 全量测试
7. **Phase 7**: 直接应用 migration 到生产(用户授权)

## 6. Rollback Plan(回滚)

如果 migration 应用后出问题:

```sql
-- 1. 删 triggers
DROP TRIGGER IF EXISTS trg_xxx_touch ON schema.table;

-- 2. 删列(可逆,数据没丢)
ALTER TABLE schema.table DROP COLUMN IF EXISTS updated_at;
ALTER TABLE schema.table DROP COLUMN IF EXISTS created_at;

-- 3. 删函数
DROP FUNCTION IF EXISTS public.fn_touch_updated_at();
```

无数据丢失(只删列),但 trigger 删了之后原 INSERT 行为会回来。

## 7. Reference

- 现状审计:42 张 v2 表合规率 4.8%
- `test_watermarks.py:120-152` 假设的 trigger 行为(已存在)
- `reporting.py:53` ORDER BY `synced_at` 语义
- `external-api.md:171-177` API response schema
- `tech-doc/_archive/data-model-v1.md` v1 时代的 `trg_orders_touch` / `trg_shops_touch` 触发器(可参考)
