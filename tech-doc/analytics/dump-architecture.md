# Analytics Dump Architecture — 技术方案

> 接 `analytics-v2-migration-plan.md`：v2 切流（`/v2/analytics/sync/{cursor,batches}`）落地后，发现 cursor 协议和 batches 协议仍然承载了过多 **client-side 状态机**（page task 队列、lease、expected_page_count、isCompleteDailyUploadUnit、跨 batch 一致性检查等）。本方案把 plugin 彻底简化为 "dumb dump"，所有派生状态由 tts-erp 从 **ad_raw 源** 推导。

## 1. 现状问题（commit cc04490 之后的代码事实）

### 1.1 plugin 端状态机（`entrypoints/background.ts` ~1700 行）

- `state.pageTasks[]` 队列（max 100）
- `state.pendingRecords[]` 列表
- `claimNextPageTask` / `completePageTask` / `expandPageTasksAfterPageOne` 3 个状态机函数
- `extractExpectedPageCount` 6 路径散弹
- `isCompleteDailyUploadUnit` 等齐才能上传
- `DATA_SYNC_COLLECTION_ALARM` 30s pacing + `PAGE_TASK_LEASE_MS` 30s lease
- 单 flight 模块变量 `remoteSyncFlight` / `queuedRemoteTrigger` / `collectionOwnerSequence`

→ **plugin 在做一个 mini-DB engine**（lease、epoch、跨事件恢复）。这是 TikTok 浏览器抓取的真实需求复杂度，不是 cursor 协议要求的。

### 1.2 server 端 cursor 协议（`/v2/analytics/sync/cursor`）

- 返回 `items: [{storageKey, campaignId, nextRequiredDay}]`（work list）
- 依赖 `ad_cursors` 表（latest_completed_day）+ `ad_daily_pages`（page bitmap）+ `ad_daily_completeness.expected_page_count + is_complete`
- cursor 计算 = "从 anchor day 起连续 complete 前缀的最后一天"

→ **5 张表状态机**只为"plugin 该拉什么"这件事服务。

### 1.3 不一致风险

- plugin 必须严格按 `expected_page_count` 等齐 N 个 page 才上传（漏 1 个 = 永远不 complete）
- 跨 batch 一致性 check（`PAGE_COUNT_CONFLICT`）依赖 plugin 端把 N 算对
- plugin 漏发 / server schema 漂移 = 数据丢失无察觉

---

## 2. 核心设计决策（4 决策，已 lock-in）

### D1. ad_raw 是 source-of-truth

- 不可变原始 dump（plugin 抓的 HTTP 交换，jsonb 直存）
- 唯一约束 `(seller_id, advertiser_id, endpoint, day, campaign_id)` 5 列
- **不与任何 ad_* 表建 FK**（逻辑链接靠 shared 5 元组 key）
- 派生表（ad_records / ad_daily_completeness）坏了 → 从 ad_raw 重跑派生

### D2. /dumps 协议替换 /batches

- **单 dump object** body（不是 list）
- dump 字段：`{endpoint, method, request, response, capturedAt}`
- 1 个 HTTP call = 1 个 (scope, endpoint, day, campaign_id) 行
- plugin **不能批量同步**——dumps 字段是 object 不是 array

### D3. /cursor 只剩 has-data 模式

- 老 work-list 模式（items / nextRequiredDay / pageSize / cursor / timezone）**全删**
- 现在只问"这个 (endpoint, day) 有没有数据" → `SELECT 1 FROM ad_raw WHERE 5 元组 LIMIT 1`
- plugin 用它做防 TikTok 风控的预检闸

### D4. schema 5 → 3 张表

- ❌ `ad_daily_pages`（page bitmap 概念消失）
- ❌ `ad_cursors`（nextRequiredDay 是 per-page 产物）
- ❌ `ad_records.page` / `ad_records.expected_page_count`（page 维度消失）
- ✅ `ad_raw`（新增，源）
- ✅ `ad_records`（无 page 列，unique 改 5 元组）
- ✅ `ad_daily_completeness`（简化为 `captured_at` 时间戳）
- ✅ `ad_shop_timezones`（永久保留，配置类）

### D5. schema 3 → 1 张表（2026-09-05 reorg,tech-doc/analytics/reorg-plan.md）

D1-D4 之后的第二个收敛动作,在 dump 协议稳态下进一步的清理：

- ❌ `ad_records` —— 生产零 SELECT（仅 INSERT + retention 90d DELETE）,
  与 ``ad_raw.response.body`` 重复存同一 payload,删。
- ❌ `ad_daily_completeness` —— D3 后 has-data 已查 ``ad_raw``,
  该表不参与协议、仅每次 dump 第三次写放大,删。
- ❌ `ad_shop_timezones` —— 生产读写路径均死（详见 reorg-plan §5.5）,
  11 行是 v1 cursor 协议遗留,删。配置概念随需随取,不占 schema。
- ❌ `ad_audit_log` —— 审计是日志不是数据,生产零 SELECT;与
  ``middleware/access_log.py``（全站每请求一行 stdout.log）重叠。
  改为 ``tts_erp_v2.analytics.ingest`` logger 单行 key=value 结构化
  文件日志。
- ✅ ``ad_raw`` —— 唯一保留的表（source-of-truth）。

对应 migration：``alembic/versions/0007_analytics_reorg_drop_dead_tables.py``,
代码改动详见 reorg-plan §5。Chrome 扩展协议不变。

---

## 3. 完整架构

### 3.1 数据流

```
plugin:
  1. dailySyncOnce() 入口
  2. state.nextPendingDump = (endpoint, day, campaignId)   ← 单指针
  3. GET /v2/analytics/sync/cursor?endpoint&day&campaignId
     → {hasData: bool}
  4. hasData=true  → 标记 done,选下一个
  5. hasData=false → fetch page-1 from TikTok
                   → 组装 dump
                   → POST /v2/analytics/sync/dumps
                   → 标记 done
  6. 触发: collection alarm (单 fire, 30s 节奏) + 手动按钮

server (/dumps, 1 事务写 **1** 张表 —— 2026-09-05 reorg 后由 3 张缩为 1 张）:
  1. INSERT ad_raw (ON CONFLICT (5 col) DO UPDATE, RETURNING xmax=0 判 inserted/duplicate)
  2. COMMIT

（reorg 前写 ad_raw + ad_records + ad_daily_completeness 三张；2026-09-05
 之后 ad_records / ad_daily_completeness 已 drop —— 详见 reorg-plan §5.3）

server (/cursor has-data):
  SELECT 1 FROM analytics.ad_raw
  WHERE seller_id=:s AND advertiser_id=:a
    AND endpoint=:e AND day=:d
    AND (:c IS NULL OR campaign_id=:c)
  LIMIT 1
```

### 3.2 5 元组 key 的 2 种表达

| 表 | unique 列 | 为什么不一样 |
| --- | --- | --- |
| `ad_raw` | `(seller_id, advertiser_id, endpoint, day, campaign_id)` | endpoint = plugin 抓的原始 path |
| `ad_records` | `(seller_id, advertiser_id, storage_key, campaign_id, day)` | storage_key = 从 endpoint 推导的 enum（**2026-09-05 已 drop**） |

**逻辑等价**：`STORAGE_KEY_BY_PATH[endpoint] = storage_key`（4 路径 1:1 映射）。两个表的 5 元组指向同一个数据 unit。

### 3.3 plugin 状态机（极简后）

```typescript
interface DataSyncState {
  settings: SyncSettings;            // syncBaseUrl + syncToken
  boundTab: BoundTab | null;         // 绑定的 TikTok 广告 tab
  campaignIds: string[];             // 从 all-campaign-discovery 拿
  nextPendingDump: {                 // ★ 单指针,取代 pageTasks + pendingRecords
    endpoint: string;
    day: string;                     // YYYY-MM-DD
    campaignId: string;
  } | null;
  // [删] pageTasks
  // [删] pendingRecords
  // [删] requestTemplates (现在 endpoint/method 直接从 storageKey 推)
}
```

### 3.4 dump wire contract

```http
POST /v2/analytics/sync/dumps
Headers:
  Authorization: Bearer <token>
  content-type: application/json
  x-protocol-version: 2
  x-request-id: <uuid>

Body:
{
  "protocolVersion": 2,
  "requestId": "<uuid>",
  "scope": {"sellerId": "...", "advertiserId": "..."},
  "dump": {                              ← ★ 单 object,不是 list
    "endpoint":  "/oec_ads/.../post_product_list",
    "method":    "POST",
    "request":   {"url": "...", "headers": {...}, "body": {...}},
    "response":  {"status": 200, "headers": {...}, "body": {...}},
    "capturedAt": "2026-08-23T03:00:00.000Z"
  }
}

Response 200:
{
  "code": 0,
  "requestId": "<uuid>",
  "data": {
    "idempotencyKey": "73b716cce7f8b2c4220b1be3e5ab6327c3a963eaf424af84412402ef8607dae3",
    "status": "inserted",                // or "duplicate"
  }
}
```

### 3.5 has-data wire contract

```http
GET /v2/analytics/sync/cursor
Query:
  sellerId      (required)
  advertiserId  (required)
  endpoint      (required)
  day           (required, YYYY-MM-DD)
  campaignId    (optional)

Response 200:
{
  "code": 0,
  "requestId": "<uuid>",
  "data": {
    "day": "2026-08-23",
    "endpoint": "/oec_ads/.../post_product_list",
    "storageKey": "productAnalyses",
    "campaignId": "camp-1",             ← 仅传时
    "hasData": true
  }
}
```

---

## 4. 实施步骤（commit-by-commit）

| # | 内容 | commit | 状态 |
| --- | --- | --- | --- |
| 1 | alembic migration 0005_ad_raw_per_unit_day | `3ebc9ed` | ✅ done |
| 2 | server: `STORAGE_KEY_BY_PATH` 常量 + `has_data()` (查 ad_raw) | pending | next |
| 3 | server: `/dumps` handler (3 表单事务) | pending | |
| 4 | server: `/cursor` 改 has-data only | pending | |
| 5 | server: 删 `/batches`、删 `fetch_cursor_page`、删 `SQL_LOCK_COMPLETENESS` | pending | |
| 6 | plugin: `createAnalyticsSyncDump` + `uploadDumps` (单 object) | pending | |
| 7 | plugin: `getAnalyticsSyncCursor` 改 has-data 模式 | pending | |
| 8 | plugin: 删 `analytics-page-queue.ts`、删 `completePageTask` / `expandPageTasksAfterPageOne` / `isCompleteDailyUploadUnit` | pending | |
| 9 | plugin: `background.ts` 重写为单 dump 模式 | pending | |
| 10 | tests: `/dumps` + has-data contract | pending | |
| 11 | tests: plugin dump + cursor has-data | pending | |
| 12 | docs: external-api.md / setup/analytics-sync.md / AGENTS.md / CHANGELOG.md | pending | |

**必须同窗口上线**（per AGENTS.md 约束）：migration 0005 + server /dumps + server /cursor 重写 + plugin dump 协议改造。缺任何一个 = DB schema 与代码不匹配 → 500。

---

## 5. 数据影响（commit 0005 之后）

### 5.1 ad_records 现有 75 行

- `page` 列直接 drop（隐式 = 1，新协议不再有 page 概念）
- `expected_page_count` 列直接 drop
- 5 元组 unique 取代 `UNIQUE(idempotency_key)`：所有同 `(scope, day, campaign_id)` 的多 page 行会被合并为 1 行

### 5.2 ad_daily_completeness 现有 59 行

- `is_complete=true` 的行 backfill `completed_at=now()` → 改 `captured_at`
- `is_complete=false` 的行 **直接删除**（未完成的脏数据从 ad_raw 重建）
- `expected_page_count` / `last_recomputed_at` / `is_complete` 列 drop

### 5.3 ad_daily_pages / ad_cursors 整表 drop

- 历史页位图和 cursor 状态在 dump 架构下无意义
- 任何依赖这 2 张表的代码（之前 `tts_erp_v2/analytics/repository.py` 里多处）一并失效

### 5.4 ad_raw 初始为空

- 由新 /dumps 协议首次写入
- 老数据无法回填到 ad_raw（raw response 不可重建）

---

## 6. 跨 endpoint 行为不变性（4 件事仍然成立）

| 不变量 | 新架构如何维持 |
| --- | --- |
| 幂等键 6 字段 SHA-256 | server 端 `compute_idempotency_key(seller, advertiser, storage_key, campaign_id, day, page=1)` — page 隐式 = 1 |
| 同事务原子写 | sess.commit() 一次,3 张表要么都写要么都回滚 |
| 跨 dump 一致性 | ad_raw `UNIQUE(5 col)` 保证 dump 幂等(ON CONFLICT DO UPDATE) |
| cursor 是服务端权威 | has-data 查询 ad_raw 源表,plugin 信任 |

---

## 7. 5 个迁移期风险

| 风险 | 缓解 |
| --- | --- |
| ad_records.page drop 丢 75 行的 page 字段 | 接受(v1/v2 batch 协议已废,page 维度无意义) |
| ad_daily_pages / ad_cursors drop 丢历史 | 接受(数据无业务价值) |
| plugin 旧代码引用 page / expected_page_count | migration 0005 后旧代码必崩(故意,提醒同窗口切换) |
| 老 /batches 调用未切换 plugin | 老 plugin 仍会 200(服务端代码不动),但写入的 ad_records 没 page 维度 → 与 dump 写入的行 unique 冲突 |
| ad_raw 初始空,等 plugin 第一次发 dump | 接受,新协议上线前 cursor has-data 全返 false(预期) |

---

## 8. 1 个未决问题（需用户确认）

**plugin 抓 N 页不允许抓 N 页**（用户明确）→ dump 协议只 dump **page-1**。

**多页日的 pages 2..N 数据怎么办？**

| 选项 | 行为 | 数据完整性 |
| --- | --- | --- |
| **A. 接受丢失** | plugin 只 dump page-1,N>1 天就丢数据 | ❌ |
| **B. 改协议** | 允许 dumps 字段是 list(N 个 dump),但破坏 5 元组 unique | ❌ 破坏 5 元组 |
| **C. 后端重抓** | 存 cursor "已 dump 但 page < N",tts-erp 主动调 TikTok 补齐剩余 | ⚠️ 不在 dump 架构 scope |

**当前选择 A**（最简单,符合 plugin 不批量的约束）。如果 N=1 天的数据完整性重要,需要后续讨论 C。

---

## 9. 改动文件清单

### 9.1 tts-erp

- ✅ `alembic/versions/0005_ad_raw_per_unit_day.py` (commit 3ebc9ed)
- ⏳ `tts_erp_v2/api/v2/analytics.py` — 改 /cursor handler + 加 /dumps handler + 删 /batches
- ⏳ `tts_erp_v2/analytics/repository.py` — 删 _recompute_cursors / fetch_cursor_page / SQL_LOCK_COMPLETENESS;加 has_data() + upsert_dump()
- ⏳ `tts_erp_v2/analytics/domain.py` — Record.expected_page_count / Record.page 字段删除;加 DumpRecord dataclass
- ⏳ `schema_tts_erp.sql` — regen 同步
- ⏳ `tests/api/test_analytics_v2_contract.py` — 重写 cursor/dumps 测试
- ⏳ `tech-doc/external-api.md` — 删 /batches,加 /dumps + has-data
- ⏳ `setup/analytics-sync.md` — 更新
- ⏳ `AGENTS.md` — §3/§9.5 端点表
- ⏳ `CHANGELOG.md` — 写 v2.1 dump architecture

### 9.2 chrome plugin

- ⏳ `src/core/analytics-sync-v2.ts` — `createAnalyticsSyncDump` + `uploadDumps` + 改 `getAnalyticsSyncCursor`
- ⏳ `src/core/analytics-page-queue.ts` — **删整文件**
- ⏳ `entrypoints/background.ts` — 重写为单 dump 模式
- ⏳ `src/core/types.ts` — `DataSyncState` 简化,删 `pageTasks` / `pendingRecords`
- ⏳ `tests/analytics-sync-v2.test.ts` — 重写 dump 测试
- ⏳ `tests/background-page-queue.test.ts` — **删**

---

## 10. 同窗口上线检查清单（上线前打钩）

- [ ] tts-erp: alembic upgrade head 跑过
- [ ] tts-erp: /dumps handler 部署
- [ ] tts-erp: /cursor 改 has-data 部署
- [ ] tts-erp: /batches 路由删
- [ ] chrome plugin: 新 build 部署到 Chrome Web Store
- [ ] nginx: 不动(路径不变 `/v2/analytics/sync/{cursor,dumps}`)
- [ ] smoke test: 真实 plugin dump 一个 (scope, endpoint, day),verify ad_raw +1 行（2026-09-05 reorg 后仅 1 张表）
- [ ] smoke test: 重复 dump 同一个 (scope, endpoint, day),verify ad_raw 1 行(ON CONFLICT DO UPDATE) + 返回 duplicate
- [ ] smoke test: 跨 day dump,verify has-data 返 true(同 day 重复),false(新 day)
- [ ] 监控: ingest logger 24h 内出现 dumps / cursor 行（替代原 audit_log 写入量检查）
- [ ] 监控: 24h 内 ad_raw 写入量 > 0
- [ ] 24h 后无 ad_records / ad_daily_completeness / ad_shop_timezones / ad_audit_log 引用错误日志(老代码清理完)
- [ ] 24h 后无 /batches 404 之外的 5xx(老 client 完全切走)

> **2026-09-05 reorg 上线补充**（tech-doc/analytics/reorg-plan.md §9）：
> 同窗口上线迁移 0007 + 代码 + regen schema + 重启 tts-erp + tts-erp-sync。
> 详见 reorg-plan.md。
