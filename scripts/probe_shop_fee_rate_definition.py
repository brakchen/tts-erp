#!/usr/bin/env python3
"""probe_shop_fee_rate_definition — 只读探查「平台抽成费率 r̂」的实际口径。

为什么需要这个脚本
------------------
仓库文档在多个地方写：

    r̂ = Σ|fee_amount| ÷ Σgross_sales_amount ≈ 30.8%   （2026-09-06 D10 重定）

但同一份文档的「费用字段字典」又记 `fee_amount` 实测仅占毛销售 **−11.58%**。
在测试库上按文档公式实测 `Σ|FEE| / ΣGROSS_SALES` = **12.2%** —— 与字段字典吻合、
与 30.8% 差 2.5 倍。也就是说 30.8% 与它自己声称的公式对不上，
是仓库既有的口径矛盾（不是 2026-09-29 店铺级改造引入的）。

这个脚本把矛盾量清楚，供人工拍板「r̂ 到底算哪个口径」。

安全性
------
**纯只读**：只发 SELECT，且整个事务设为 READ ONLY；不写任何表、
不需要 ALLOW_PROD_DESTRUCTIVE，可以直接在生产库上跑。

用法::

    set -a; source .env; set +a
    .venv/bin/python scripts/probe_shop_fee_rate_definition.py
    # 可选：只看某店铺
    .venv/bin/python scripts/probe_shop_fee_rate_definition.py --shop-pk 12
    # 可选：只看近 N 天（默认 180，与 analytics.shop_fee_rate 任务一致）
    .venv/bin/python scripts/probe_shop_fee_rate_definition.py --days 90
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, text


def _pct(numerator: Decimal | None, denominator: Decimal | None) -> str:
    if not numerator or not denominator:
        return "     —"
    return f"{float(numerator) / float(denominator) * 100:6.2f}%"


# 候选口径。每个 = (标签, 说明, SQL 片段: 对单笔交易的「扣费」聚合表达式)
# 全部用 ABS 防御上游符号方向。
CANDIDATES: list[tuple[str, str, str]] = [
    (
        "A. |FEE| / GROSS_SALES",
        "文档声称的公式（fee_amount 汇总字段）",
        "SUM(ABS(sc.amount)) FILTER (WHERE sc.component_code = 'FEE')",
    ),
    (
        "B. |FEE| + 运费类 / GROSS_SALES",
        "FEE 外再补 shipping / actual_shipping_fee / shipping_cost",
        "SUM(ABS(sc.amount)) FILTER (WHERE sc.component_code = 'FEE') "
        "+ SUM(ABS(sc.amount)) FILTER (WHERE sc.component_code IN "
        "('SHIPPING_FEE','ACTUAL_SHIPPING_FEE','SHIPPING_COST',"
        "'FBM_SHIPPING_COST','FBT_SHIPPING_COST'))",
    ),
    (
        "C. 抽佣+联盟+运费 / GROSS_SALES",
        "按文档「口径 = 抽佣+联盟+运费类+其它扣款」分项相加",
        "SUM(ABS(sc.amount)) FILTER (WHERE sc.component_code IN "
        "('PLATFORM_COMMISSION','AFFILIATE_COMMISSION',"
        "'AFFILIATE_ADS_COMMISSION','AFFILIATE_PARTNER_COMMISSION',"
        "'TRANSACTION_FEE','REFERRAL_FEE',"
        "'SHIPPING_FEE','ACTUAL_SHIPPING_FEE','SHIPPING_COST'))",
    ),
    (
        "D. |FEE| / (GROSS_SALES−退款)",
        "换分母：净销售口径",
        "SUM(ABS(sc.amount)) FILTER (WHERE sc.component_code = 'FEE')",
    ),
    (
        "E. 1 − SETTLEMENT/GROSS_SALES",
        "文档警告的「假象」（含卖家折扣与退款，不应作费率）",
        "0",
    ),
]

# 逐交易透视：一笔结算交易一行。
_PIVOT = """
WITH txn AS (
    SELECT st.id,
           so.shop_pk,
           MAX(sc.amount) FILTER (WHERE sc.component_code = 'GROSS_SALES')
               AS gross_sales,
           MAX(sc.amount) FILTER (WHERE sc.component_code = 'SETTLEMENT')
               AS settlement,
           SUM(ABS(sc.amount)) FILTER (WHERE sc.component_code = 'FEE')
               AS fee,
           SUM(ABS(sc.amount)) FILTER (WHERE sc.component_code IN
               ('SHIPPING_FEE','ACTUAL_SHIPPING_FEE','SHIPPING_COST',
                'FBM_SHIPPING_COST','FBT_SHIPPING_COST')) AS shipping,
           SUM(ABS(sc.amount)) FILTER (WHERE sc.component_code IN
               ('PLATFORM_COMMISSION','AFFILIATE_COMMISSION',
                'AFFILIATE_ADS_COMMISSION','AFFILIATE_PARTNER_COMMISSION',
                'TRANSACTION_FEE','REFERRAL_FEE',
                'SHIPPING_FEE','ACTUAL_SHIPPING_FEE','SHIPPING_COST'))
               AS itemized,
           SUM(ABS(sc.amount)) FILTER (WHERE sc.component_code IN
               ('SELLER_DISCOUNT','PLATFORM_DISCOUNT')) AS discounts,
           SUM(ABS(sc.amount)) FILTER (WHERE sc.component_code IN
               ('CUSTOMER_REFUND','GROSS_SALES_REFUND')) AS refunds,
           BOOL_OR(sc.component_code = 'FEE') AS has_fee
    FROM finance.settlement_transactions st
    JOIN finance.settlement_components sc ON sc.transaction_id = st.id
    JOIN commerce.sales_orders so ON so.id = st.order_pk
    WHERE st.order_pk IS NOT NULL
      AND COALESCE(st.transaction_time, st.synced_at) >= :window_start
      AND COALESCE(st.transaction_time, st.synced_at) <  :window_end
      AND (CAST(:shop_pk AS bigint) IS NULL OR so.shop_pk = :shop_pk)
    GROUP BY st.id, so.shop_pk
)
SELECT count(*) AS txn_count,
       COALESCE(SUM(gross_sales), 0) AS gross,
       COALESCE(SUM(settlement), 0)  AS settlement,
       COALESCE(SUM(fee), 0)         AS fee,
       COALESCE(SUM(shipping), 0)    AS shipping,
       COALESCE(SUM(itemized), 0)    AS itemized,
       COALESCE(SUM(discounts), 0)   AS discounts,
       COALESCE(SUM(refunds), 0)     AS refunds,
       COUNT(*) FILTER (WHERE gross_sales > 0 AND has_fee) AS eligible
FROM txn
WHERE gross_sales > 0
"""

_BY_SHOP = """
WITH txn AS (
    SELECT st.id, so.shop_pk,
           MAX(sc.amount) FILTER (WHERE sc.component_code = 'GROSS_SALES')
               AS gross_sales,
           SUM(ABS(sc.amount)) FILTER (WHERE sc.component_code = 'FEE') AS fee
    FROM finance.settlement_transactions st
    JOIN finance.settlement_components sc ON sc.transaction_id = st.id
    JOIN commerce.sales_orders so ON so.id = st.order_pk
    WHERE st.order_pk IS NOT NULL
      AND sc.component_code IN ('FEE', 'GROSS_SALES')
      AND COALESCE(st.transaction_time, st.synced_at) >= :window_start
      AND COALESCE(st.transaction_time, st.synced_at) <  :window_end
    GROUP BY st.id, so.shop_pk
)
SELECT s.shop_id,
       t.shop_pk,
       COALESCE(SUM(t.gross_sales), 0) AS gross,
       COALESCE(SUM(t.fee), 0) AS fee,
       COUNT(*) FILTER (WHERE t.fee IS NOT NULL) AS n_fee,
       COUNT(*) AS n_all
FROM txn t
JOIN commerce.shops s ON s.id = t.shop_pk
WHERE t.gross_sales > 0
GROUP BY s.shop_id, t.shop_pk
ORDER BY 3 DESC
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shop-pk", type=int, default=None)
    parser.add_argument("--days", type=int, default=180)
    args = parser.parse_args()

    url = os.environ.get("TTS_ERP_DB_URL", "").strip()
    if not url:
        print("ERR: TTS_ERP_DB_URL 未设置；先 `set -a; source .env; set +a`", file=sys.stderr)
        return 1
    url = url.replace("postgresql+psycopg://", "postgresql://", 1)

    engine = create_engine(url)
    window_end = datetime.now(UTC)
    window_start = window_end - timedelta(days=args.days)
    with engine.connect() as conn:
        # 显式只读：即使脚本被改坏也不会写库。
        conn.execute(text("SET TRANSACTION READ ONLY"))
        dbname = conn.execute(text("SELECT current_database()")).scalar_one()
        print(f"# probe_shop_fee_rate_definition  db={dbname}  days={args.days}  "
              f"shop_pk={args.shop_pk}\n")

        row = conn.execute(
            text(_PIVOT),
            {
                "window_start": window_start,
                "window_end": window_end,
                "shop_pk": args.shop_pk,
            },
        ).mappings().one()

        gross = Decimal(row["gross"])
        print("## 窗口内已结算交易（gross_sales > 0）")
        print(f"  交易数 = {row['txn_count']:,}   其中带 FEE 分项 = {row['eligible']:,}")
        print(f"  GROSS_SALES       = {gross:>20,.0f}")
        for key, label in [
            ("settlement", "SETTLEMENT"),
            ("fee", "|FEE|"),
            ("shipping", "|运费类|"),
            ("itemized", "|抽佣+联盟+运费|"),
            ("discounts", "|卖家/平台折扣|"),
            ("refunds", "|退款类|"),
        ]:
            val = Decimal(row[key])
            print(f"  {label:<18} = {val:>20,.0f}   = {_pct(val, gross)} of gross")
        print()

        print("## 候选口径（同一个分母 GROSS_SALES，除特别说明）")
        cands = [
            ("A. |FEE| / GROSS_SALES", row["fee"]),
            ("B. (|FEE|+运费类) / GROSS_SALES", Decimal(row["fee"]) + Decimal(row["shipping"])),
            ("C. |抽佣+联盟+运费| / GROSS_SALES", row["itemized"]),
        ]
        for label, num in cands:
            print(f"  {label:<38} = {_pct(Decimal(num), gross)}")
        print(f"  {'E. 1 − SETTLEMENT/GROSS_SALES':<38} = "
              f"{_pct(gross - Decimal(row['settlement']), gross)}   ← 文档警告的假象")
        print()

        print("## 与文档基线的对照")
        print("  文档声称：r̂ = Σ|fee_amount| ÷ Σgross_sales_amount ≈ 30.8%")
        print(f"  本库实测：A 口径 = {_pct(Decimal(row['fee']), gross)}")
        doc_field = Decimal("11.58")
        print(f"  文档「费用字段字典」记 fee_amount = −11.58% of gross → 与 A 口径吻合")
        print()

        shop_rows = conn.execute(
            text(_BY_SHOP),
            {
                "window_start": window_start,
                "window_end": window_end,
            },
        ).fetchall() if args.shop_pk is None else []

        if shop_rows:
            print("## 逐店铺 A 口径（= analytics.shop_fee_rate 任务算的东西）")
            print(f"  {'shop_id':>22}  {'A 口径':>9}  {'材料覆盖':>10}  {'gross':>18}")
            for r in shop_rows:
                g = Decimal(r[2])
                f = Decimal(r[3])
                cov = f"{r[4] / r[5] * 100:9.1f}%" if r[5] else "        —"
                print(f"  {r[0]:>22}  {_pct(f, g):>9}  {cov:>10}  {g:>18,.0f}")

    print()
    print("## 结论 / 待拍板")
    print("  · 若 r̂ 应等于「Σ|fee_amount| / Σgross_sales」→ 当前实现正确，")
    print("    文档里的 30.8% 是过期/错算的常量，应改文档并把基线重定。")
    print("  · 若 r̂ 应含运费/联盟等更宽口径 → 需改 analytics.shop_fee_rate 的")
    print("    component 集合（候选 B/C），并同步改 FEE_RATE_BASELINE。")
    print("  · 两种口径对未结算订单净利估算影响很大（费率差 1 倍 → 净利估算同量级偏差），")
    print("    上线前必须确认。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
