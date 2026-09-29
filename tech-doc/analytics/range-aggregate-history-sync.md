# 技术方案：区间聚合历史同步 + 今日快照（campaign 级快照替换）

> 状态：待评审（字段级模型已确认，尚未进入实施）
> 关联文档：`dump-architecture.md`（v2 dump 架构）、`reorg-plan.md`（2026-09-05 收敛）、
> `biz-doc/analytics/*`（口径 truth source）、`spu-real-roi-dashboard.md`（读方）
> 影响仓库：`tts-erp`（服务端）+ `chrome-plugins/ads-data-sync`（插件，同时下发一份 pointer）
> 改动性质：破坏性协议升级（protocol v3）+ schema 演进（migration 0012），**两仓库同窗口上线**

> **⚠ 状态（2026-09-11）：本文所述 v3 区间聚合协议已废弃，遗留对象已删除。**
> `analytics.ad_raw` / `analytics.ad_sync_audit` 两张表与 `analytics.ad_product_links`
> 视图已由 **migration 0020** 删除。背景：`ad_raw` 自 v4 逐日协议上线后即冻结
> （现行 `repository.py` 只写 `ad_raw_log`），而 `ad_product_links` 是 `ad_raw` 的
> 唯一依赖者却**零生产消费者** —— SPU ROI 的 `_SQL_ROI_AD`
> （`tts_erp_v2/analytics/spu_roi.py`）一直直接读 `ad_daily ∪ ad_today` 并自行 JOIN
> `commerce`，从不经过该视图。**本文保留为当时的设计记录，不再反映现状**；
> 现行架构见 `tech-doc/analytics/daily-sync-with-coverage.md`，引用面审计见
> `alembic/versions/0020_drop_v3_analytics_leftovers.py` 的模块 docstring。

---

## 0. TL;DR

- **拉取**：插件不再按 `campaign × day × endpoint` 逐日拉取；历史部分改为对每个
  campaign×endpoint 拉**一次整段 `[S..T-1]` 区间聚合**（S=用户开始日期，T=店铺当地今日），
  今日部分 `[T..T]` 每 30s 刷新一次快照。请求量：历史回填/重装/断档从 `O(天×campaign×3)`
  降为 `O(campaign×3)`；稳态每天每 campaign×endpoint 至多 1 次历史推进 + 30s 今日刷新。
- **存储（Design A，已确认）**：`ad_raw` 每 `(seller_id, advertiser_id, endpoint, campaign_id,
  kind)` **至多一行 live**（`kind='history'` 或 `'today'`），区间 `[day_start..day_end]` 作为
  可变内容**原地更新**；不做软删、不做版本化。被取代的历史只写**元数据审计行**。
- **正确性**：重复上报/重装/改 S/区间重叠/乱序全部收敛到"同键 upsert + capturedAt 单调守卫"；
  不存在跨行累加，因此不存在重复统计；读路径只看 live 行。
- **前提**：接受"T-1 起数据冻结"但内置**全段重拉自愈**（每次 rollover/重装重拉即吸收延迟归因
  修正）；需先在线验证 A-1..A-7（§10），尤其区间聚合==逐日之和、历史 SPU/campaign 覆盖。

### 决策日志（D-*，实施即 lock）

| # | 决策 | 替代/否决 |
|---|---|---|
| D-1 | 同步单元 = (scope × endpoint × campaign) 的两个快照：`history [S..T-1]`、`today [T..T]` | 逐日笛卡尔拉取；区间行多行累计模型 |
| D-2 | 历史整段聚合拉取；**不按天拆分、不做区间减法**（聚合不可逆，谁也不拆） | 任何"把区间拆回按天"的做法（伪需求） |
| D-3 | 存储 Design A：每 (scope,endpoint,campaign,kind) 单行、区间原地更新 | 软删版本化（无读消费方、存储翻倍） |
| D-4 | 被取代内容只写元数据审计 `analytics.ad_sync_audit`（区间/时间/原因），不存旧 JSON | 全版本 JSON 归档 |
| D-5 | `captured_at` 单调守卫；status 扩展 `inserted/updated/duplicate/stale_ignored` | 无条件 DO UPDATE |
| D-6 | legacy 逐日行 `kind='daily'` 物理保留，首个覆盖它们的新 history 写入时**同事务折叠删除** | 迁移期手工重算/整表搬移 |
| D-7 | 读路径（视图/ROI）只看 live 行；`today` 仅当 `today.day_end > history.day_end` 计入；跨天**先推进 history、后重置 today** | 两行同时计入 / 先重置 today |
| D-8 | 跨店零点后的历史推进延迟一个**结算保护窗**（默认 60min，可配；按 A-5 实测调参） | 零点整点立刻结算 |
| D-9 | 插件保持 dumb：不回读服务端数值做任何减法/算术 | 插件耦合服务端分析 schema |
| D-10 | 延迟归因/历史回补的修正 = 每次 rollover/重装/改 S 的全段重拉（自愈）；按天明细补采是独立的可选通道，不进本方案 | 逐日补拉做默认修正 |

---

## 1. 目标与业务前提

1. 只关心从用户所选开始日期 S 到当前时刻、每个 SPU 的累计广告数据；**不需要日级明细**。
2. 三个 endpoint（`post_product_list` / `post_session_list` / `campaign_opt_log_list`）支持按
   时间区间查询并返回区间聚合；因此不再逐日请求。
3. 前提假设：T-1 及以前数据已固定；若实际存在延迟归因/回补，由 D-10 的全段重拉自愈 + 结算
   保护窗（D-8）覆盖，不额外做修正机制（修正 = 重拉即得，机制上不再单独建）。
4. 生命周期：首装/重开 → 一轮历史 + 持续今日刷新；跨店零点 → 结算推进后刷新新一天；改 S →
   按新范围重建；S==今天 → 跳过历史。
5. 幂等/隔离要求（用户原话要点）全部落到 D-3/D-5/D-6/D-7 与既有 scope 授权/唯一键。

## 2. 现状与差距（为什么必须动服务端）

现状事实（代码级，2026-09-07）：

- 插件：`NextPendingDump = {endpoint, day, campaignId}`（`dump-progress.ts:98` `dumpCandidates`
  对 `campaignIds × [start..end] 每天 × 3 endpoint` 求笛卡尔积）；`day` 精确到店铺当地自然日；
  今日单元已有 30s 刷新（`CURRENT_DAY_REFRESH_INTERVAL_MS=30_000`，`shouldRefreshCurrentDay`）。
- 服务端：`ad_raw` 唯一键 5 元组含 `day`（一行=一天），`ON CONFLICT DO UPDATE` 无条件覆盖；
  `ad_product_links` 视图把逐日 product 行 SUM 成 campaign×SPU 累计（ROI 页 `_SQL_ROI_AD` 消费）；
  cursor has-data = 单日存在性；进程内缓存桶 `(scope,campaign) → (endpoint, day)`。
- 差距：**服务端一行=一天**是全链隐含不变量。区间聚合响应放进去会产生"一行覆盖多天却被当一天
  求和"的重复累计/语义漂移 → 存储键、幂等、hasData、缓存、视图必须同步升级（本方案全部覆盖）。

## 3. 总体数据流

```
[用户] 设置开始日期 S（店铺时区语义；结束恒 = 店铺当地今天）
   │ bind 广告页 → 发现 campaigns（post_campaign_list 区间 [S..TD] 遍历，复用现有逻辑）
   ▼
[插件历史轮]  每个 campaign × 启用 endpoint，且仅当需要时（D-2/D-8）：
   ├─ GET  cursor coverage(scope, endpoint, campaign, kind=history)
   │        → 服务端回：live history 行 {day_start, day_end, captured_at} 或空
   ├─ 决策：无行 / day_start≠S / day_end<T-1（rollover/gap/改S）→ 抓取；已覆盖 → 跳过
   ├─ 抓 TikTok：post_xxx_list?oec_seller_id&aadvid  body: start_time=S, end_time=T-1, 翻页
   │        （session 前置：同 campaign 同区间先抓 product 取 spu_id_list；**不传不完整分页**）
   ├─ POST /v2/analytics/sync/dumps (protocolVersion=3, kind=history, dayStart=S, dayEnd=T-1)
   ▼
[服务端 upsert_dump v3]
   ├─ upsert live history 行（ON CONFLICT (…,'history') DO UPDATE，WHERE captured_at 守卫）
   ├─ 同事务：折叠该 campaign 的 legacy daily 行（day_end ∈ [S, T-1]）
   └─ 同事务：ad_sync_audit 写 1 行（event=history_replaced/rollover_advanced/window_rebuilt…）
   ▼
[插件今日轮]  每 30s：对每个 campaign×启用 endpoint 的 kind=today 单元 [T..T]
   ├─ 抓 TikTok（start_time=end_time=T）→ POST dumps v3 (kind=today)
   └─ 服务端 upsert live today 行（同键覆盖 + capturedAt 守卫），**不写审计**
   ▼
[读路径]    analytics.ad_product_links / spu-roi：只读 live 行；每 campaign 至多 2 行，
            today 计入条件 day_end > history.day_end（D-7）→ SUM 语义与旧版等价
```

## 4. 字段级数据模型（定稿，Design A）

### 4.1 `analytics.ad_raw`（目标态）

```sql
-- kind: 'history' 历史整段快照 [S..T-1]（1 行/campaign，原地推进）
--       'today'   今日快照 [T..T]（1 行/campaign，30s 原地覆盖）
--       'daily'   legacy 逐日行（迁移期保留，被首个 history 折叠；旧插件写入兼容）
CREATE TABLE analytics.ad_raw (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key  TEXT NOT NULL,                  -- 幂等键，公式 §4.4
    seller_id        TEXT NOT NULL,
    advertiser_id    TEXT NOT NULL,
    endpoint         TEXT NOT NULL,
    method           TEXT NOT NULL,
    campaign_id      TEXT NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('history','today','daily')),
    day_start        DATE NOT NULL,                  -- 覆盖起：history=S；today=daily=自身
    day_end          DATE NOT NULL,                  -- 覆盖止：history=T-1；today=daily=自身
    request          JSONB NOT NULL,
    response         JSONB NOT NULL,
    captured_at      TIMESTAMPTZ NOT NULL,
    received_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    source           TEXT,
    request_id       TEXT,
    protocol_version INT  NOT NULL DEFAULT 3,
    schema_version   INT  NOT NULL DEFAULT 2,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- live 行唯一键：区间是内容，不进键（原地更新模型）
CREATE UNIQUE INDEX uq_analytics_raw_live
    ON analytics.ad_raw (seller_id, advertiser_id, endpoint, campaign_id, kind)
    WHERE kind IN ('history','today');

-- legacy daily 行唯一键：沿用旧 5 元组（含日），直到被折叠
CREATE UNIQUE INDEX uq_analytics_raw_daily
    ON analytics.ad_raw (seller_id, advertiser_id, endpoint, day_end, campaign_id)
    WHERE kind = 'daily';

CREATE INDEX idx_analytics_raw_scope   ON analytics.ad_raw (seller_id, advertiser_id, endpoint, day_end);
CREATE INDEX idx_analytics_raw_request ON analytics.ad_raw (request_id);
CREATE INDEX idx_analytics_raw_received ON analytics.ad_raw (received_at);
```

> 用 **partial unique index** 而非表级 UNIQUE：live 与 daily 两套唯一语义并存，
> 迁移期间旧约束删除前新索引已建好（窗口内仍有唯一性兜底）。

### 4.2 迁移（alembic `0012_range_aggregate_ad_raw`，纯增量可灰度）

```sql
ALTER TABLE analytics.ad_raw ADD COLUMN kind TEXT;
ALTER TABLE analytics.ad_raw ADD COLUMN day_start DATE;
ALTER TABLE analytics.ad_raw ADD COLUMN day_end  DATE;

UPDATE analytics.ad_raw SET kind='daily', day_start=day, day_end=day WHERE kind IS NULL;

ALTER TABLE analytics.ad_raw RENAME COLUMN day TO day_end;   -- 语义：day → 区间末日

ALTER TABLE analytics.ad_raw ALTER COLUMN kind SET NOT NULL;
ALTER TABLE analytics.ad_raw ALTER COLUMN day_start SET NOT NULL;
ALTER TABLE analytics.ad_raw ALTER COLUMN day_end SET NOT NULL;

ALTER TABLE analytics.ad_raw DROP CONSTRAINT IF EXISTS uq_analytics_raw_unit_day;
-- 建 §4.1 两把 partial unique + 三个索引
```

`down_revision = "0011_oauth_states"`（当前 head）。同步 `scripts/regen_schema.py` →
`schema_tts_erp.sql`。

### 4.3 `analytics.ad_sync_audit`（元数据审计，D-4）

```sql
CREATE TABLE analytics.ad_sync_audit (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    seller_id        TEXT NOT NULL,
    advertiser_id    TEXT NOT NULL,
    endpoint         TEXT NOT NULL,
    campaign_id      TEXT NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('history','today','daily')),
    event            TEXT NOT NULL CHECK (event IN
                       ('history_replaced','rollover_advanced','window_rebuilt',
                        'legacy_collapsed','today_reset')),
    prev_day_start   DATE,
    prev_day_end     DATE,
    prev_captured_at TIMESTAMPTZ,
    new_day_start    DATE,
    new_day_end      DATE,
    new_captured_at  TIMESTAMPTZ,
    reason           TEXT,        -- reinstall | rollover | start_change | manual | retry
    request_id       TEXT
);
CREATE INDEX idx_ad_sync_audit_scope
    ON analytics.ad_sync_audit (seller_id, advertiser_id, campaign_id, occurred_at DESC);
```

写入时机：只在**内容被取代**的事件写 1 行（history 替换/推进/重建、legacy 折叠、今日行跨天
重置），与主写同事务原子提交；**30s 今日常规刷新不写审计**（防 2880 行/campaign/天）。

### 4.4 幂等键公式（protocol v3，服务端自算）

```
sha256( canonical_json{
    "sellerId", "advertiserId",
    "storageKey" = STORAGE_KEY_BY_PATH[endpoint],   -- 3 个枚举
    "campaignId",
    "kind"     ∈ {history, today},
    "dayStart" = day_start.isoformat(),              -- YYYY-MM-DD
    "dayEnd"   = day_end.isoformat(),
} )
```

canonical 规则沿用 `domain.canonical_json_for_key`（strip / sort_keys / 无空白 / UTF-8）。
同一 campaign 同 kind 的重复同步（重装/重试）产生同一 hash → upsert 而非累积。
v2 旧键（`day`+`page=1`）代码保留仅供旧行核对；新旧 JSON 形状不同，hash 天然不冲突。

### 4.5 capturedAt 单调守卫与响应 status

```sql
INSERT INTO analytics.ad_raw (…, kind, day_start, day_end, request, response, captured_at, …)
VALUES (…)
ON CONFLICT (seller_id, advertiser_id, endpoint, campaign_id, kind)
    WHERE kind IN ('history','today')
DO UPDATE SET request=EXCLUDED.request, response=EXCLUDED.response,
              day_start=EXCLUDED.day_start, day_end=EXCLUDED.day_end,
              captured_at=EXCLUDED.captured_at, received_at=now()
    WHERE analytics.ad_raw.captured_at <= EXCLUDED.captured_at;
```

| DO UPDATE 结果 | status | 插件处理 |
|---|---|---|
| 首次插入（xmax=0） | `inserted` | 记确认 |
| 更新成功（新 capturedAt ≥ 活动行） | `updated` | 记确认 |
| 命中但守卫拒绝（旧 capturedAt < 活动行，迟到重试/竞态） | `stale_ignored` | **视为成功**，保留活动行，不入失败队列 |
| 幂等同键等值重放 | `duplicate` | 记确认 |

## 5. 协议变更（protocol v3）

### 5.1 `POST /v2/analytics/sync/dumps` — 请求增量

```jsonc
{
  "protocolVersion": 3,                       // v2 兼容：缺省视 kind/day 为 daily 单日
  "requestId": "<uuid>",
  "scope": { "sellerId": "...", "advertiserId": "..." },
  "dump": {
    "endpoint":  "/oec_ads/shopping/v1/oec/stat/post_product_list",
    "method":    "POST",
    "day":       "2026-09-09",                // 兼容字段 = dayEnd（v3 仍带）
    "kind":      "history",                   // 新增，必带（v3）
    "dayStart":  "2026-07-01",                // 新增，必带（v3）
    "dayEnd":    "2026-09-09",                // 新增（= day，二选一冗余校验）
    "request":   { "url": "...", "body": { "start_time": "2026-07-01", "end_time": "2026-09-09", ... } },
    "response":  { "status": 200, "body": { "data": { "table": [...] } } },
    "capturedAt": "2026-09-10T02:15:00.000Z",
    "schemaVersion": 2
  }
}
```

响应 `data.status ∈ {inserted, updated, duplicate, stale_ignored}`（v2 客户端只认前两者，映射兼容）。

### 5.2 `GET /v2/analytics/sync/cursor` — 语义改为 coverage（has-data 的区间版）

```
GET /cursor?sellerId&advertiserId&endpoint&kind&dayStart&dayEnd[&campaignId]
```

- 带 `campaignId`：返回该 (scope,endpoint,campaign) live 行状态：
  `{kind, hasRow, dayStart, dayEnd, capturedAt}`。插件据此决策 D-2（无行 / day_start≠S /
  day_end<T-1 → 抓；否则跳过）。
- 不带 `campaignId`（scope 级，低频）：返回该 (scope,endpoint) 任意 live 行存在性，语义不变。
- 缓存：桶 value 从 `(endpoint, day)` 集合 → `(endpoint, kind, dayStart, dayEnd)` live 行集；
  TTL 600s 不变；dumps 写穿透（upsert 后 `mark_present` 更新桶）。**红线注释同步更新**：
  物理删除只发生在 `kind='daily'` legacy 行折叠，live 行只 upsert 不删 → 无 stale-true，
  `has_data_cache` 的不变量论证保持成立。

### 5.3 不变量（升级后仍成立）

1. dumps 单 object、无 page 维度；2. 同 scope 不跨店铺复用；3. server 端推导 storageKey；
4. **live 行只 upsert 不删**（daily 折叠除外）；5. 插件不批量、不传不完整分页。

## 6. 插件端技术设计（ads-data-sync）

### 6.1 状态与类型

```ts
// NextPendingDump 语义升级（types.ts）
interface NextPendingDump {
  endpoint: string;
  kind: 'history' | 'today';        // 新增
  dayStart: string;                 // history=S；today=T
  dayEnd: string;                   // history=T-1；today=T（原 day）
  campaignId: string;
}
// DataSyncState 新增（storage 迁移归一化）
historyState: {                     // scope-keyed：本地"服务端已结算到哪"缓存
  startDay: string; settledThrough: string; capturedAt: string | null;
} | null;
rolloverDay: string | null;         // 最近一次已处理的店铺当地日（防重复历史轮）
```

- 本地断点/延迟/缺失键：`dumpUnitKey(scope, endpoint, kind, dayEnd, campaignId)`（history 用
  dayEnd=settledThrough，today 用 day=T）。
- 保留 30s 今日刷新逻辑，判定从"day==今天"平移为"kind==='today' 且 dayEnd==店铺今天"。
- 历史确认断点不参与 31 天滚动裁剪（历史行随 S/E 推进原地更新，本地只存最新一条标记）；
  今日确认断点裁剪规则沿用。

### 6.2 调度与触发（防"每次唤醒重复全量历史"）

| 触发 | 动作 |
|---|---|
| MV3 worker 冷启 / 心跳 | `recoverInterruptedSync` + resume：**只**按到期单元执行；历史仅当 `historyState` 缺失、或 `settledThrough < TD-1`、或 `startDay≠S` 且已过结算保护窗（D-8）才进历史轮 |
| 绑定 + 首次发现完成 | 历史轮立即执行（不等保护窗：首装语义） |
| 每 30s 心跳 | 今日刷新轮（kind=today，dayEnd=TD，超 30s 未确认的单元） |
| 跨店零点（心跳发现 TD 变化） | 置 `rolloverDay=新TD` → 历史轮在 `店铺当地时刻 ≥ 00:30`（默认 `HISTORY_SETTLE_GUARD_MINUTES=60`，可配）后执行；**先推进 history 成功，再开始新一天 today 刷新**（D-7 顺序） |
| 修改 S（popup 保存） | 失效 `historyState`，历史轮立即重建 `[S..新TD-1]` |
| 手动"立即同步/重建" | 历史轮立即执行 |
| TikTok 登录失效（11000 / not_login） | 沿用：停同步、清绑定、保留断点 |

历史轮与今日轮共享现有单一调度通道与 rate limiter（cursor 500QPS 独立 pacing、TikTok
加速 5–8QPS/8 并发 or 常规 2s 批次+2.5s 分页，**均不变**）。

### 6.3 逐文件改动清单

| 文件 | 改动 |
|---|---|
| `src/core/types.ts` | `NextPendingDump` 加 kind/dayStart；`DataSyncState` 加 historyState/rolloverDay；断点类型对齐 |
| `src/core/settings.ts` | 移除"结束日期"固定语义（`syncHistoricalData` 停用或仅兼容）；S = `campaignDiscoveryWindow.startDay`；加 `historySettleGuardMinutes` |
| `src/core/dump-progress.ts` | 候选生成改两段（每 campaign×endpoint 一个 history 单元 + 今日单元）；`isCurrentDayDump/shouldRefreshCurrentDay` 平移；跨天推进/结算窗/重放去重逻辑 |
| `src/core/tiktok-endpoints.ts` / `-schemas.ts` | `createCollectionRequestBody` start/end 取 dayStart..dayEnd；session 前置 product 区间抓取；schema 允许区间 |
| `src/core/analytics-sync-v2.ts` | dump 加 kind/dayStart/dayEnd + protocolVersion 3；status 解析扩展 4 值；cursor coverage 客户端 |
| `src/extension/storage.ts` | state 迁移（day 断点→(kind,dayEnd) 键、historyState 推导）；新字段归一化 |
| `entrypoints/background.ts` | 历史轮/今日轮编排、跨天状态机、结算保护窗、historyState 持久化；复用现有 recover/心跳 |
| `entrypoints/popup/main.tsx` | 日期输入只剩开始日期；状态区展示"历史已结算至 X / 下次推进"、待重试/待补数据 |
| `tests/*` | §9 场景全覆盖 |

### 6.4 分页中断/部分失败/重试（沿用，行为不变）

- 历史整段抓取 = 一次 dump（多页本地合并后上传，**不传不完整分页**）；分页中断 → 单元失败 →
  `deferDump` 60s 冷却重试；失败不影响其他 campaign/endpoint。
- 部分 endpoint 失败：三个 endpoint 独立单元/独立行，互不牵连。
- 同区间重试（网络抖动）：服务端同键 upsert → 收敛；capturedAt 更旧的迟到重试 → `stale_ignored`。
- 插件重装/断档恢复：cursor coverage → 缺什么补什么（整段 [S..TD-1] 一次补齐）。

## 7. 服务端技术设计（tts-erp）

| 文件 | 改动 |
|---|---|
| `alembic/versions/0012_range_aggregate_ad_raw.py` | §4.1/4.2 DDL（加列/回填/改名/换 partial unique） |
| `alembic/versions/0013_ad_sync_audit.py` | §4.3 审计表（可并入 0012，视评审意见） |
| `tts_erp_v2/db/models/analytics.py` | AdRaw 镜像：kind/day_start/day_end、partial 索引；AdSyncAudit 模型 |
| `tts_erp_v2/analytics/domain.py` | `DumpPayload` 加 kind/day_start/day_end；`compute_idempotency_key_v3`（保留 v2 分支） |
| `tts_erp_v2/analytics/repository.py` | `SQL_INSERT_RAW` → live partial upsert + 守卫 + daily 折叠 DELETE + audit insert（单事务）；`SQL_CAMPAIGN_DAYS` → live 行集；`has_data`/`load_campaign_pairs` 改 coverage |
| `tts_erp_v2/api/v2/analytics.py` | `DumpBodyIn` 加 dayStart/kind（v3 校验）；status 枚举；cursor 参数与响应扩展；错误路径/审计日志字段不变 |
| `tts_erp_v2/analytics/has_data_cache.py` | 桶 value 与写穿透改 live 行（§5.2）；红线注释更新（live 只 upsert 不删） |
| 视图 `0006_ad_product_links_view` → 新 migration | CTE 改读 live product 行；**未转换 campaign 回退读 legacy daily 行**（保持口径连续，见 §8）；`observed_days/first_day/last_day` 由区间推导 |
| `tts_erp_v2/api/v2/analytics.py`（ROI SQL） | `_SQL_ROI_AD`/`_SQL_ROI_WINDOW` 改读 live（含 today 计入条件 day_end > history.day_end） |
| `schema_tts_erp.sql` / `scripts/regen_schema.py` | regen |
| `tech-doc/external-api.md` / `setup/analytics-sync.md` / `AGENTS.md` / `CHANGELOG.md` | 同步 |

upsert+fold+audit 单事务伪码：

```python
def upsert_dump(sess, dump, request_id):
    idem = compute_idempotency_key_v3(dump)          # 或 v2 分支
    was_ignored = False
    # 1) live upsert（capturedAt 守卫）
    if dump.kind in ('history', 'today'):
        row = sess.execute(SQL_UPSERT_LIVE, {...}).scalar()   # RETURNING status
        if row == 'ignored': was_ignored = True
        if dump.kind == 'history':
            # 2) 折叠 legacy daily（幂等 DELETE，命中 0 无害）
            sess.execute(SQL_FOLD_DAILY, {scope, campaign, S, E})
            # 3) 审计（history 事件：replaced / rollover_advanced / window_rebuilt 由 prev 区间判定）
            sess.execute(SQL_INSERT_AUDIT, {...prev, new, reason=dump.meta.reason or inferred})
    elif dump.kind == 'daily':                        # 旧插件 v2 兼容
        upsert daily（旧唯一键）
    sess.commit()
    return status
```

## 8. 迁移与兼容（两仓库同窗口上线）

1. **上线顺序**：migration 0012(+0013) → 服务端代码（接受 v2+v3）→ 插件新版本发布。
   旧插件（v2 daily 写入）在服务端新代码下仍 200（`kind='daily'` 分支），其行在首个 v3
   history 写入时被折叠——**无需冻结窗口**，但完成全部 campaign 折叠前，视图按"该 campaign
   有 live history 行则忽略其 daily 行，否则回退 daily 行"过渡（§7 视图），口径不跳变。
2. **首次切换基准校验**（迁移期一次性，A-1b）：对 1~3 个 campaign，SQL 比对
   `SUM(legacy daily 行指标)` vs 首个 v3 history 快照对应值，记录差异；若 TikTok 区间聚合
   口径与逐日求和系统性不等 → 在 §10 A-1 结论落地前不得切全量。
3. **遗留 daily 行**：折叠后即死数据（视图不可见）；清理由后续维护任务（retention）处理，
   不在 0012 内物理清库。
4. 幂等键 v2/v3 共存；`has_data_cache` 只跟踪 live 行，折叠删除不影响缓存正确性（§5.2）。

## 9. 测试计划

### 9.1 服务端（pytest，`tests/analytics/*` + `tests/api/test_analytics_v2_*`）

| # | 场景 | 断言 |
|---|---|---|
| S1 | v3 history 首插 | inserted；live 行 day_start=S/day_end=E；daily 同 campaign 行被折叠 |
| S2 | 同 (…,kind) 同区间重放 | updated/duplicate，行数不变 |
| S3 | capturedAt 更旧的重试 | stale_ignored，活动行内容不变（含 day_start/day_end 不被回退） |
| S4 | rollover 推进 [S,T-1]→[S,T] | history 行原地 day_end=T；prev 审计行正确 |
| S5 | 改 S 向前（superset） | history 行 day_start 前移 + 内容替换；无残留重叠 |
| S6 | 改 S 向后（subrange 重建） | history 行 day_start 后移 + 整段替换；旧区间仅审计可见 |
| S7 | legacy 行 + 无 live history（过渡期） | 视图回退 daily 行，口径与迁移前一致 |
| S8 | 折叠后视图 | 视图只读 live；today 计入仅当 day_end > history.day_end |
| S9 | 30s today 刷新连发 | 单行覆盖；无审计行增长 |
| S10 | cursor coverage 各分支 + 缓存写穿透/过期 | hasRow/dayStart/dayEnd 正确；删 daily 无 stale-true |
| S11 | 多店铺/账户隔离（TEST_ 前缀）+ scope 授权拒绝 | 互不串 |
| S12 | 视图/ROI 与旧口径对账（迁移后第一个完整日） | 与手工 SUM 校验一致 |

### 9.2 插件（vitest，覆盖率门槛 语句/函数/行 95%、分支 90% 不变）

| # | 场景 |
|---|---|
| P1 | 首次绑定+发现：每 campaign×endpoint 恰一个 history 单元，然后进入 today 轮 |
| P2 | 重装（清 state）：cursor coverage 命中 → 跳过历史；不命中 → 整段一次补齐 |
| P3 | 30s 刷新只选中 kind=today 单元；history 确认后不再被选中 |
| P4 | 跨店零点（假钟，VN/GB DST）：结算保护窗前不推进；窗后先 history 后 today |
| P5 | 长时间关闭后重开：整段 [S,TD-1] 一次补齐（无逐日回补） |
| P6 | 改 S（前/后）：historyState 失效 → 重建；S==今天 → 仅 today |
| P7 | 分页中断/HTTP 失败/429/登录失效：defer 冷却、missing map、停止绑定语义不变 |
| P8 | 上传 status 4 值解析（stale_ignored 视为成功） |
| P9 | settings/popup 展示：只剩开始日期；历史已结算至 X 状态 |

### 9.3 真实联调 checklist（发布后）

- [ ] 新插件对真实店铺做首轮：ad_raw 每 campaign 恰 1 history + 1 today；无 daily 残留
- [ ] 次日零点后推进正确；观察结算保护窗内视图无缺口/无双计
- [ ] 卸载重装一次：无重复、无丢失（对账 spu-roi 页广告列 vs 前一天）
- [ ] 改 S 一次（向前 1 周）：新增周数据出现、重叠段不翻倍
- [ ] `analytics.ad_sync_audit` 行数与事件符合预期；ingest 日志无 5xx
- [ ] hasData cache 命中率回升；TikTok 风控无异常（无 11000）

## 10. 在线接口能力验证清单（实施前 gate，需真实账号/页面抓包）

| # | 验证 | 影响 |
|---|---|---|
| A-1 | 区间聚合 == 逐日之和（同一 campaign：[D,D] 三段求和 vs [D-2,D] 一次响应） | 迁移切换基准；若不等需先定口径 |
| A-2 | 区间响应是否包含"区间内曾挂载、现已下架/停投"的 SPU | 漏则历史 SPU 静默丢数（致命） |
| A-3 | post_session_list 区间粒度（per-SPU / per-SPU×session / per-session）与 `spu_id_list` 长度上限 | session 合并正确性 |
| A-4 | campaign_opt_log_list 区间上限（天数/页数/是否截断） | 日志区间可行性 |
| A-5 | T-1 稳定性：跨天后 0h/6h/24h/72h 重拉 [T-1,T-1] 数值变化 | 决定结算保护窗默认值（D-8） |
| A-6 | post_campaign_list 区间是否含"区间内活跃、现已停止"的 campaign | 发现窗口覆盖，改 S 向后安全 |
| A-7 | 登录态/风控行为（11000/not_login、长区间响应 pacing）不变 | 安全网 |

## 11. 风险与开放项

1. **A-2/A-6 是数据源前提**：若 TikTok 范围查询只返回"当前状态"，扩展开始日期/补历史本身
   不安全——这是存储模型解决不了的，验证失败则需引入"按天补采兜底"（P2 级通道，D-10 预留）。
2. 结算保护窗是经验值（A-5 实测调参）；窗内视图显示的是"昨日最后快照"，业务可接受（与旧版
   逐日"最后一次 30s 快照即终值"相比是改善而非回退）。
3. 多插件实例并发写（重装间隙）：capturedAt 守卫保证旧不压新；同刻相等值任选，窗口可忽略。
4. 全段重拉请求体/响应体积：2MB/256KiB 闸不变；区间响应行数≈SPU/session/log 量级，分页合并
   后单 dump 上限需用真实 campaign 压测确认（超出则需按 SPU 子集分片，追加决策）。
5. 插件本地 historyState 仅是缓存，丢失后由 cursor coverage 自愈（不做权威）。

## 12. 实施任务清单（建议顺序）

**阶段 0（gate，需业务/线上环境）**：A-1..A-7 在线验证，产出结论记录；决定结算保护窗默认值。
**阶段 1（服务端先行，可 TDD）**
1. migration 0012（+0013）与 regen schema；2. domain 幂等 v3；3. repository upsert/fold/audit
   + 单测 S1–S11；4. api v3 校验/status/cursor coverage + 测试；5. has_data_cache live 化；
6. 视图/ROI 升级 + 对账测试 S8/S12。
**阶段 2（插件）**：7. types/settings；8. tiktok-endpoints 区间 body + schema；9. dump-progress
   两段候选/跨天/结算窗（P1–P6）；10. analytics-sync-v2 v3 客户端（P8）；11. background 编排；
12. storage 迁移 + popup（P9）。
**阶段 3（联调发布）**：13. 同窗口上线（§8 顺序）；14. 真实联调 checklist；15. 文档/AGENTS/
   CHANGELOG/版本号一致；插件 ZIP 构建+覆盖率门槛。

---

（本方案由 2026-09-07 方案讨论收敛：Design A + 元数据审计；字段级模型已确认。）
