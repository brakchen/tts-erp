# 货源价查询指南

> 怎么从 dev/prod 库里取一个 TikTok SPU 的"货源价"（1688 挂牌口径）。
> 写于 2026-09-07，涵盖当时 schema 与 job 链路。

## 一、数据存储全景（dev DB 实际现状）

| 层 | 表 | 字段 | 当前行数 | 含义 |
| --- | --- | --- | --- | --- |
| 持久层（权威） | `procurement.procurement_products` | `source_unit_cost` / `source_min_unit_cost` / `source_max_unit_cost` | **812 / 811 / 811** | 1688 挂牌价。权威值。 |
| 桥接键 | 同上 | `source_item_id` / `source_item_url` | — | 1688 offer id（全局唯一，跨 license 桥接用） |
| 派生层（报表） | `reporting.product_cost_snapshots` | `cost_method` / `unit_cost` / `currency` | 114 (MANUAL=5, SOURCE_PRICE=109) | cost_snapshots job 6h tick 重算 |
| 人工覆盖 | `procurement.manual_product_costs` | `unit_cost` / `currency` | 7 | 最高优先级，人工填 |

## 二、读优先级（与 `reporting.cost_snapshots.resolve_unit_cost` 一致）

```
1. MANUAL_ENTRY        — procurement.manual_product_costs (人工)
2. LATEST_PURCHASE_COST— procurement.purchase_order_lines.unit_cost (妙手采购单)
3. SOURCE_PRICE        — procurement_products.source_unit_cost (本次回填的 1688 货源价)
4. (无)                — 不写快照, 进入 active_spus_without_cost 待人工补
```

## 三、查一个 TikTok spu_id 的货源价（主路径）

```sql
-- ① 直接查 TK-side 行 (本次 sync_source_cost_to_master 已回填)
SELECT external_product_id      AS spu_id,
       source_item_id            AS offer_1688_id,
       source_item_url,
       source_unit_cost, source_min_unit_cost, source_max_unit_cost,
       synced_at, source_updated_at
FROM procurement.procurement_products
WHERE external_product_id = :spu_id          -- 例如 '1736929955366339831'
  AND external_product_id ~ '^[0-9]{15,20}$';

-- ② 若 ① 为 NULL (backfill 还没跑到), 走 offer-bridge —— 但 SYNC 后应该 0 NULL
SELECT pp.external_product_id    AS spu_id,
       offer.source_item_id      AS offer_1688_id,
       offer.source_unit_cost,
       offer.source_min_unit_cost,
       offer.source_max_unit_cost
FROM procurement.procurement_products pp
JOIN LATERAL (
    SELECT DISTINCT ON (source_item_id)
           source_item_id, source_unit_cost, source_min_unit_cost, source_max_unit_cost
    FROM procurement.procurement_products
    WHERE NOT (external_product_id ~ '^[0-9]{15,20}$')
      AND source_unit_cost IS NOT NULL
      AND source_item_id = pp.source_item_id
    ORDER BY source_item_id, synced_at DESC NULLS LAST, id DESC
) offer ON true
WHERE pp.external_product_id = :spu_id
  AND pp.external_product_id ~ '^[0-9]{15,20}$';

-- ③ 报表层口径 (cost_snapshots job 已跑过 → cal_version=4)
SELECT cp.spu_id, snap.cost_method, snap.unit_cost, snap.currency,
       snap.calculation_version, snap.valid_from
FROM reporting.product_cost_snapshots snap
JOIN commerce.products_spu cp ON cp.id = snap.spu_pk
WHERE cp.spu_id = :spu_id
ORDER BY snap.calculation_version DESC, snap.valid_from DESC LIMIT 1;
```

## 四、按 1688 offer_id 查（已知是哪个 1688 货源）

```sql
-- 公共采集箱行 (报价)
SELECT external_product_id AS common_box_id,
       source_item_id      AS offer_1688_id,
       source_unit_cost, source_min_unit_cost, source_max_unit_cost, synced_at
FROM procurement.procurement_products
WHERE NOT (external_product_id ~ '^[0-9]{15,20}$')
  AND source_item_id = :offer_1688_id
ORDER BY synced_at DESC NULLS LAST, id DESC LIMIT 5;

-- 反向: 用这个 offer 的价格桥到所有 TK-side 行
SELECT pp.external_product_id    AS spu_id,
       offer.source_unit_cost
FROM procurement.procurement_products pp
JOIN LATERAL (
    SELECT source_unit_cost FROM procurement.procurement_products
    WHERE NOT (external_product_id ~ '^[0-9]{15,20}$')
      AND source_unit_cost IS NOT NULL
      AND source_item_id = pp.source_item_id
    ORDER BY synced_at DESC NULLS LAST, id DESC LIMIT 1
) offer ON true
WHERE pp.external_product_id ~ '^[0-9]{15,20}$'
  AND pp.source_item_id = :offer_1688_id;
```

## 五、Python (本仓库 venv)

```python
import os
os.environ.setdefault("TTS_ERP_DB_URL", "postgresql://...")
from sqlalchemy import create_engine, text
e = create_engine(os.environ["TTS_ERP_DB_URL"])

SPU = "1736929955366339831"
with e.connect() as c:
    row = c.execute(text("""
        SELECT source_unit_cost, source_min_unit_cost, source_max_unit_cost,
               source_item_id, source_item_url
        FROM procurement.procurement_products
        WHERE external_product_id = :s AND external_product_id ~ '^[0-9]{15,20}$'
    """), {"s": SPU}).mappings().first()
    if row and row["source_unit_cost"] is not None:
        print(f"spu_id={SPU} 货源价={row['source_unit_cost']} (CNY)")
        print(f"  1688 offer: {row['source_item_id']}  {row['source_item_url']}")
    else:
        print(f"spu_id={SPU} 没找到（也许 backfill 没跑到，或 spu_id 不存在）")
```

或复用仓库的 `reporting._source_cost_lookup`：

```python
from tts_erp_v2.jobs.reporting import _source_cost_lookup
from sqlalchemy.orm import Session
with Session(e) as s:
    lookup = _source_cost_lookup(s)
    cost, ccy = lookup(int_spu_pk)   # returns (Decimal|None, str|None)
```

## 六、活体验证（拉 1688 当下挂牌价对比）

仓库里没有公共采集箱的"采集时间价"和"当下价"之分（originPrice / source_unit_cost 都是历史快照）。要核对当下 1688 挂牌价：

```bash
# source_item_url 是 1688 detail 页; 但反爬严, 多半抓不到价
curl -sL -A 'Mozilla/5.0' "http://detail.1688.com/offer/<offer_id>.html" \
  | grep -oE '"(price|minPrice|maxPrice|salePrice|offerPrice)":"[0-9.]+"' | head
```

1688 detail 页常被反爬挡, 成功概率 ~ 5%, 不建议作为常规验证手段。

## 七、运维/排错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| ① 查 TK-side 行 source_unit_cost = NULL | backfill 还没跑到 或 该 SPU 无对应 1688 offer | 等 6h tick；或手跑 `scripts/probe_*.py` 类脚本调 `sync_source_cost_to_master(s)` |
| ① 查 TK-side 行 NULL 但 source_item_id 有值 | common-box 行还没采到价 | 触发 `miaoshou.common_collect_box` job |
| ② offer-bridge 查不到 | common-box 整库就没这个 offer | 看该 offer 是不是真存在; 检查 source_item_id 是否拼写对 |
| ③ cost_snapshot 没行 | SPU 不在 ACTIVATE 状态 或所有 cost 来源都缺失 | 看 `active_spus_without_cost` (reporting.active_spus_without_cost) |
| 4 rows 不同 source_unit_cost | 1688 卖家调过价; 我们用的是采集时固化价 | 要最新价就重跑 common_collect_box → sync_source_cost_to_master |
| value 跟 1688 当下挂牌价差很大 | 数据老化 (1688 卖家改价) 或源错误 (TK originPrice 不可信, 不要用) | 用 source_item_url 拉当下核对; 若常错就是源头异常 |

## 八、刷新节奏

| Job | 频率 | 作用 | 延迟 |
| --- | --- | --- | --- |
| `miaoshou.common_collect_box` | 6h | 公共采集箱 → 632+ 行 source_unit_cost | 数据 T+0 到 T+6h |
| `miaoshou.sync_source_cost_to_master` | 6h | bridge → TK-side 行 source_unit_cost | 数据 T+0 到 T+6h |
| `reporting.cost_snapshots` | 6h | product_cost_snapshots 派生快照 | 数据 T+0 到 T+6h |
| `miaoshou.move_collect` | 30min | TK 发布记录 → miaoshou_move_collect_tasks + procurement_products TK-side 行 (含 source_item_id) | 数据 T+0 到 T+30min |
| 人工补 manual_product_costs | 不定 | 高优先人工事实 | 实时 |

## 九、口径与陷阱

- **source_unit_cost 是 1688 挂牌价, 不是成交价**。即使有妙手采购单(`purchase_order_lines.unit_cost`), 也优先用采购单成交价, 次才用 source_unit_cost。
- **source_unit_cost 不等于 originPrice**。originPrice 在 TK collect_box 平台采集中被人工/模板改坏(实测差 7×); source_unit_cost 来自公共采集箱 / 1688 listing, 可信。
- **source_item_id 是全局的 (1688 offer id)**, 不按 procurement_account 隔离。本次 bridge 已特意去掉 account_id 约束。
- **多计算版本**: cost_snapshots 每次跑累加 calculation_version; 取最新口径用 `ORDER BY calculation_version DESC, valid_from DESC LIMIT 1`。
- **跨 unit**: source_unit_cost 默认 CNY (1688 ¥); cost_snapshot 标 `currency` 字段, 默认也是 CNY, profit_daily 会按 fx.exchange_rates 折算。
