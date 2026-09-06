# 汇率缓存（fx）接入设计 — ExchangeRate-API

> 2026-09-06 落地。Truth source：本文件 + `schema_tts_erp.sql`（fx schema 段）。
> 端点契约：`tech-doc/external-api.md` 的 **FX rates (`/v2/fx/*`)** 章节。

## 1. 上游与配额预算

- 上游：`v6.exchangerate-api.com`（ExchangeRate-API）。接入方式见
  [Authentication 文档](https://www.exchangerate-api.com/docs/authentication)。
  免费档 **1500 请求/月，超量收费** —— 这是整套缓存设计的出发点。
- 只用 **Standard endpoint**（`GET /v6/{key}/latest/{base}`）：一次请求返回
  base 到全部 ~160 个币种的汇率。免费档数据**每天刷新一次**，响应自带两个
  关键时间戳：
  - `time_last_update_utc` — 这批汇率的上游刷新时刻（快照身份键）；
  - `time_next_update_utc` — 上游声明的**下次刷新时刻**（缓存失效时刻）。
- 配额预算：job 只在上游过期后才拉 → **≈ 1 请求/天**（月 ~30/1500，余量
  >50×）。换算全部本地做，绝不调 pair-conversion endpoint（一次一对，
  打爆配额）。

## 2. 存储（新 schema `fx`，migration 0010）

```
fx.exchange_rate_snapshots    每次拉取一条：base_code / upstream_last_update /
                               next_update_at（=上游 time_next_update_utc，
                               缓存 horizon）/ fetched_at / rates_count
                              UNIQUE (base_code, upstream_last_update) = 幂等键
fx.exchange_rates             汇率换算表：snapshot_id FK(CASCADE) / base_code /
                               target_code / rate NUMERIC(20,8)
                              UNIQUE (snapshot_id, target_code)
```

- 历史保留：上游换日 → 新 snapshot + ~160 行/天，体量可忽略。
- 同 `upstream_last_update` 的重拉（如 force 重试撞上未刷新）只刷新
  `next_update_at` / `fetched_at`，不重复插行。
- 换算以 snapshot 的 base 为桥：`rate(F→T) = rate(base→T) / rate(base→F)`
  （base 为 F/T 时退化为直读/倒数），8dp quantize（`Numeric(20,8)`）。

## 3. 同步路径（唯一的上游调用方）

- `tts_erp_v2/jobs/exchangerate/sync.py`，注册 `sync_worker/scheduler.py`
  JOBS：`fx.sync`，每小时 tick，`is_tiktok=False`。
- tick 语义（`sync_fx_rates`）：
  1. `now() < latest.next_update_at` → **skipped**，零网络（默认状态）。
  2. 过期 → 拉 Standard endpoint → 落 snapshot + rate 行 +
     `integration.raw_records` 审计（endpoint=`exchangerate/v6/{base}/latest`）。
  3. 上游返回 `quota-exceeded` / `inactive-account` / `invalid-key`
     → 记录 SyncIssue + **进程内 24h 冷却**（避免每小时失败行刷屏/继续烧配额）；
     其它错误 → 大声失败（failed tick + 日志），下个 tick 自动重试。
- 环境变量（**只在 sync-worker**，`tts-erp-sync.service` 的 EnvironmentFile）：
  - `EXCHANGERATE_API_KEY` — 必填。明文只存在于服务器 .env，无任何 HTTP 面。
  - `EXCHANGERATE_BASE_CODE` — 默认 `USD`。
  - `EXCHANGERATE_FORCE=1` — 一次性绕开 horizon 检查（手动回填）：
    `EXCHANGERATE_FORCE=1 python -m tts_erp_v2.sync_worker.main run fx.sync`
- 上 `.env` 配好 key 前**不要重启** tts-erp-sync.service —— job 缺 key 会
  每小时记一条 failed。

## 4. 读路径（API / 服务，零上游）

- `tts_erp_v2/fx/rates.py`：`load_rate_map` / `convert(_or_none)`。
  `RateMap.is_stale` = `next_update_at is None or now >= next_update_at`
  （数据超过上游 horizon 仍未刷新 → 告知客户端按近似值处理）。
- `GET /v2/fx/latest?base_code=`（默认 USD）→ 最新快照全量汇率
  （Decimal → JSON 字符串，8dp）+ `stale` 标记。
- `GET /v2/fx/convert?amount&from_code&to_code&base_code=` → 本地换算。
  400 = 币种不在缓存；404 = 该 base 还没拉过。**永不触发上游请求**。
- 角色：readonly（`middleware/auth.py` `/v2/fx/` 前缀）。

## 5. 运维

- 看 job 是否在跑：`journalctl --user -u tts-erp-sync -n 50 | grep fx.sync`，
  或 `python -m tts_erp_v2.sync_worker.main list`。
- 看数据新鲜度：`curl .../v2/fx/latest` 的 `stale` 字段；
  或 `SELECT base_code, next_update_at, now() > next_update_at AS stale
  FROM fx.exchange_rate_snapshots ORDER BY id DESC LIMIT 1`。
- 配额消耗可对比 `integration.raw_records`（endpoint like
  `exchangerate/%`）行数 vs 上游后台用量页。
