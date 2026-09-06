# FX 接入手册（给其他 agent）：货币汇率查询与换汇

> 目的：本仓库任何 agent（新 lane / 维护任务 / 报表任务）需要「某个金额从一种
> 货币换成另一种货币」或「查当前汇率」时，**只准走本手册**。写代码前先读完
> 红线段（§5），避免直连上游把配额烧掉。
>
> 相关文档：`tech-doc/external-api.md`（端点活契约，本手册为其操作版）、
> `tech-doc/fx-exchange-rates.md`（存储/同步/配额设计）。
> 数据源：ExchangeRate-API（v6.exchangerate-api.com），免费档 **1500 请求/月，
> 超量收费**。仓库内唯一上游入口 = sync-worker 的 `fx.sync` job。

## 1. 货币数据存在哪

数据落在第 11 个 schema `fx`，由 `fx.sync` job（每小时 tick、horizon 门控）
从上游拉取缓存，**API 与所有读路径零上游调用**：

| 表 | 含义 |
| --- | --- |
| `fx.exchange_rate_snapshots` | 每次成功拉取的元数据：`base_code`（当前=USD）、`upstream_last_update`（这批汇率的上游刷新时刻）、`next_update_at`（**缓存失效时刻**：`now() < next_update_at` 期间数据权威，job 不碰网络）、`fetched_at`、`rates_count` |
| `fx.exchange_rates` | 汇率换算表：每 `(snapshot_id, target_code)` 一行，`rate NUMERIC(20,8)` = 1 单位 base 兑 target_code |

- 全量 ~166 币种，含 base 自身（rate=1）。查「最新」= 按 `id` 取最新 snapshot。
- 时间一律 **ISO-8601 UTC**（`timestamptz`）。
- 币种码 = ISO 4217 大写三位（如 `USD`/`CNY`/`EUR`/`VND`）；服务端会把小写
  归一化，但请求里写大写最稳。

## 2. 查最新汇率：GET /v2/fx/latest

```bash
curl -sS -H "X-API-Key: $TTS_ERP_RO_KEY" \
  "http://127.0.0.1:9877/v2/fx/latest?base_code=USD"
```

| 参数 | 说明 |
| --- | --- |
| `base_code` | 可选，默认 `USD`（当前只有 USD base 的快照会被拉到；传别的 base 若没拉过 → 404） |

响应（字段与 DB 列一致；`rates` 内每个值都是 **JSON 字符串** Decimal，8 dp）：

```json
{
  "base_code": "USD",
  "upstream_last_update": "2026-09-06T00:00:01Z",
  "next_update_at": "2026-09-07T00:00:01Z",
  "fetched_at": "2026-09-06T06:27:19Z",
  "rate_count": 166,
  "stale": false,
  "rates": { "USD": "1.00000000", "CNY": "6.73440000", "VND": "26006.48620000", "…": "…" }
}
```

- `stale=false` = 数据在权威窗口内（最新）。`stale=true` = 已过上游刷新点、
  尚未拉新（最多旧约 1 个刷新周期）——按近似值处理，别当作实时价。
- 取单币汇率：`rates["CNY"]` 即 1 USD 值多少 CNY。

## 3. 货币换汇：GET /v2/fx/convert

```bash
curl -sS -H "X-API-Key: $TTS_ERP_RO_KEY" \
  "http://127.0.0.1:9877/v2/fx/convert?amount=100&from_code=CNY&to_code=USD"
```

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `amount` | ✔ | 金额（Decimal；可带小数/负值，如退款）。传整数或字符串皆可 |
| `from_code` | ✔ | 源币种（ISO 4217） |
| `to_code` | ✔ | 目标币种 |
| `base_code` | 可选 | 默认 `USD`；换汇基于哪个 base 快照（一般不用传） |

响应：

```json
{
  "base_code": "USD",
  "upstream_last_update": "2026-09-06T00:00:01Z",
  "next_update_at": "2026-09-07T00:00:01Z",
  "stale": false,
  "amount": "100",
  "from_code": "CNY",
  "to_code": "USD",
  "rate": "0.14849133",
  "converted": "14.84913300"
}
```

- `rate` = 1 单位 from_code 值多少 to_code；`converted` = amount × rate。
  两者都是 **JSON 字符串**，精度 8 dp（内部 `Numeric(20,8)` + 8 dp quantize，
  先 quantize rate 再乘 —— 别用浮点，用 `Decimal` 解析）。
- 任意币对都支持，无需 base 是 from/to：内部以 snapshot base 为桥做除法
  `rate(F→T) = rate(base→T) / rate(base→F)`（F/T 之一是 base 时退化为直读/倒数）。
- `from_code == to_code` → `rate=1`、`converted=amount`。
- **此接口永不触发上游请求** —— 随便调，配额无感。

### 错误语义

| 状态码 | 含义 |
| --- | --- |
| `401` | 没带 API key（`Authorization: Bearer` 或 `X-API-Key`） |
| `403` | key 角色 < readonly（fx 端点一律 readonly） |
| `404` | 该 `base_code` 还没有快照（`fx.sync` 未拉过/尚未拉到） |
| `400` | 币种码不在缓存里（detail 会列出缺的码） |
| `422` | 参数缺失/非法（如 amount 非数字、码长 <3 或 >12） |

## 4. 写后端代码时（进程内调用，等价于上面两个接口）

不要自己拼 SQL 也行——用服务层，纯 DB 读：

```python
from decimal import Decimal
from tts_erp_v2.fx.rates import convert, load_rate_map, UnknownCurrencyError

rm = load_rate_map(session, base_code="USD")   # → RateMap | None（None=还没拉过）
rm.rates["CNY"]                                 # Decimal("6.73440000")

try:
    out = convert(session, amount=Decimal("100"), from_code="CNY", to_code="USD")
except UnknownCurrencyError:
    ...  # 币种不在缓存
# out.rate / out.converted / out.stale / out.upstream_last_update ...
```

DB 直查最新快照的 SQL（只读场景，别绕过上面的函数去拼复杂换算）：

```sql
SELECT r.target_code, r.rate
FROM fx.exchange_rates r
JOIN fx.exchange_rate_snapshots s ON s.id = r.snapshot_id
WHERE s.base_code = 'USD'
ORDER BY s.id DESC LIMIT 1;          -- 注意：这样只取到最新快照的最后 1 行，
                                     -- 查全量请先取最新 snapshot_id 再查全部行
```

## 5. 红线（agent 必须知道，做错会烧钱/报错）

- ❌ **不要直连 `v6.exchangerate-api.com`**（或任何上游 URL、不自己拼 HMAC/鉴权）：
  唯一合法入口是 sync-worker 的 `fx.sync`（`tts_erp_v2/jobs/exchangerate/sync.py`），
  horizon 门控 ≈ 1 请求/天。API/报表代码里出现上游 URL = bug。
- ❌ **不要从 `.env` 读 `EXCHANGERATE_API_KEY` 或把它写进文档/日志/响应**：
  key 只在 sync-worker 环境内使用；API 进程没有任何 key 路径。
- ❌ 不要调 pair-conversion / historical 等其它上游 endpoint（免费档各 endpoint
  独立计费，且我们本地桥式换算已覆盖任意币对）。
- ⚠️ 金额与汇率在 API 响应里是 **Decimal 字符串**（8 dp），解析用 `Decimal`，
  别用 `float`。
- ⚠️ 缓存更新节奏：上游每日 00:00 UTC 前后刷新；`fx.sync` 每小时 tick 但
  到 `next_update_at` 才真正拉取。**当天内重复查同一币对，结果不变**——这是特性。
- ⚠️ 幂等：同 `upstream_last_update` 的重拉只刷新 `next_update_at`，不重复插行；
  别自己再写「防重复」逻辑。
- 手动强制刷新（运维）：`EXCHANGERATE_FORCE=1 python -m tts_erp_v2.sync_worker.main run fx.sync`
  （每用一次 = 烧 1 次上游配额，非必要不用）。

## 6. 常用速查

| 需求 | 怎么做 |
| --- | --- |
| 1 USD = ? CNY（最新） | `GET /v2/fx/latest` → `rates["CNY"]` |
| 100 CNY → USD | `GET /v2/fx/convert?amount=100&from_code=CNY&to_code=USD` → `converted` |
| 利润/成本换汇（后端） | `from tts_erp_v2.fx.rates import convert` |
| 数据有多新 / 何时自动更新 | `latest.next_update_at`（下次自动刷新点）、`stale` |
| 查 job 是否健康 | `journalctl --user -u tts-erp-sync | grep fx.sync` 或 `python -m tts_erp_v2.sync_worker.main list` |
