#!/usr/bin/env python3
"""probe_shop_fee_rate_definition — 只读验证「平台抽成费率 r̂」的分母口径。

结论（2026-09-29 在生产库 tts_erp 上实测确定）
---------------------------------------------
**r̂ = Σ|FEE| / Σ line_gmv**，其中 ``line_gmv`` = ``sales_order_lines.quantity
× unit_price`` = **客户实付（折扣后）**。

生产库实测（1204 笔已结算订单）::

    Σ(quantity×unit_price)   727,148,240
    CUSTOMER_PAYMENT         728,896,839   ← 只差 0.24%，确认是「实付」
    GROSS_SALES            1,230,117,867   ← 169% of line_gmv，是折扣前挂牌价
                                          （= AFTER_SELLER_DISCOUNTS_SUBTOTAL + |SELLER_DISCOUNT|）

    逐单恒等式（中位残差 0.000%，91.3% 在 ±5% 内）::

        SETTLEMENT ≈ line_gmv + FEE + CUSTOMER_REFUND

即 ``FEE`` 已含全部从卖家结算款扣掉的项目（含运费类），因此：

* r̂ = |FEE| / line_gmv ≈ **21.2%**  ← 公式 `unsettled_sales × (1−r̂) × (1−退款率)`
  里真正使用的变量就是 line_gmv，所以这是自洽的口径。
* r̂ = |FEE| / GROSS_SALES ≈ 12.5%   ← **错**：拿折扣前挂牌价当分母。
* 文档里写的 30.8% 与它自己声称的公式对不上（文档同页的字段表记
  `fee_amount` 实测占毛销售 −11.58%，与 12.5% 吻合、与 30.8% 不吻合）。
* 分母**不可**再叠加运费分项：运费已在 FEE 内，叠加会重复扣。

这个脚本把上面每一条都在目标库上重新量一遍，供人工复核。

安全性
------
**纯只读**：只发 SELECT，事务设为 READ ONLY，并加语句级超时；不写任何表、
不需要 ALLOW_PROD_DESTRUCTIVE，可直接在生产库上跑。

用法::

    set -a; source .env; set +a
    .venv/bin/python scripts/probe_shop_fee_rate_definition.py
    .venv/bin/python scripts/probe_shop_fee_rate_definition.py --days 90
    .venv/bin/python scripts/probe_shop_fee_rate_definition.py --shop-pk 12
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine, text

# 逐订单透视：分子（|FEE|）、分母候选、以及用于对账的分项。
_PER_ORDER = """
WITH settled AS (
    SELECT DISTINCT st.order_pk AS order_pk
    FROM finance.settlement_transactions st
    JOIN commerce.sales_orders so ON so.id = st.order_pk
    WHERE st.order_pk IS NOT NULL
      AND COALESCE(st.transaction_time, st.synced_at) >= :window_start
      AND COALESCE(st.transaction_time, st.synced_at) <  :window_end
      AND (CAST(:shop_pk AS bigint) IS NULL OR so.shop_pk = :shop_pk)
),
line_side AS (
    SELECT sl.order_pk AS order_pk,
           MAX(so.shop_pk) AS shop_pk,
           SUM(sl.quantity * sl.unit_price) AS line_gmv
    FROM commerce.sales_order_lines sl
    JOIN commerce.sales_orders so ON so.id = sl.order_pk
    JOIN settled s ON s.order_pk = sl.order_pk
    GROUP BY sl.order_pk
),
comp AS (
    SELECT st.order_pk AS order_pk,
           -- 费率分子用绝对值；对账恒等式用带符号值（FEE/退款上游均为负）。
           SUM(ABS(sc.amount)) FILTER (WHERE sc.component_code = 'FEE')
               AS fee_abs,
           SUM(sc.amount) FILTER (WHERE sc.component_code = 'FEE')
               AS fee_signed,
           MAX(sc.amount) FILTER (WHERE sc.component_code = 'GROSS_SALES')
               AS gross_sales,
           MAX(sc.amount) FILTER (WHERE sc.component_code = 'CUSTOMER_PAYMENT')
               AS customer_payment,
           MAX(sc.amount) FILTER (
               WHERE sc.component_code = 'AFTER_SELLER_DISCOUNTS_SUBTOTAL'
           ) AS after_discounts,
           MAX(sc.amount) FILTER (WHERE sc.component_code = 'SETTLEMENT')
               AS settlement,
           SUM(sc.amount) FILTER (WHERE sc.component_code = 'CUSTOMER_REFUND')
               AS customer_refund,
           SUM(ABS(sc.amount)) FILTER (WHERE sc.component_code = 'PLATFORM_COMMISSION')
               AS platform_commission,
           SUM(ABS(sc.amount)) FILTER (
               WHERE sc.component_code IN
                   ('SHIPPING_FEE','ACTUAL_SHIPPING_FEE','SHIPPING_COST')
           ) AS shipping
    FROM finance.settlement_transactions st
    JOIN finance.settlement_components sc ON sc.transaction_id = st.id
    JOIN settled s ON s.order_pk = st.order_pk
    GROUP BY st.order_pk
)
SELECT l.shop_pk,
       l.line_gmv,
       COALESCE(c.fee_abs, 0)          AS fee,
       COALESCE(c.fee_signed, 0)       AS fee_signed,
       c.gross_sales,
       c.customer_payment,
       c.after_discounts,
       c.settlement,
       c.customer_refund,
       c.platform_commission,
       c.shipping
FROM line_side l
JOIN comp c ON c.order_pk = l.order_pk
WHERE l.line_gmv > 0
"""


def _ratio(num: float, den: float) -> str:
    return f"{num / den * 100:6.2f}%" if den else "     —"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shop-pk", type=int, default=None)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument(
        "--statement-timeout-ms",
        type=int,
        default=120_000,
        help="单条 SQL 的语句级超时（默认 120s），防止在生产库上跑长查询。",
    )
    args = parser.parse_args()

    url = os.environ.get("TTS_ERP_DB_URL", "").strip()
    if not url:
        print(
            "ERR: TTS_ERP_DB_URL 未设置；先 `set -a; source .env; set +a`",
            file=sys.stderr,
        )
        return 1
    url = url.replace("postgresql+psycopg://", "postgresql://", 1)

    window_end = datetime.now(UTC)
    window_start = window_end - timedelta(days=args.days)

    engine = create_engine(url)
    with engine.connect() as conn:
        # READ ONLY 必须是事务里的第一条语句；随后加语句级超时。
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(
            text(f"SET LOCAL statement_timeout = {int(args.statement_timeout_ms)}")
        )
        dbname = conn.execute(text("SELECT current_database()")).scalar_one()
        is_prod = not ("test" in dbname or dbname.endswith("_v3"))
        print(f"# probe_shop_fee_rate_definition  db={dbname}  days={args.days}  "
              f"shop_pk={args.shop_pk}")
        print(f"# 事务=READ ONLY  语句超时={args.statement_timeout_ms}ms  "
              f"{'⚠ 生产库（只读）' if is_prod else '测试库'}\n")

        rows = conn.execute(
            text(_PER_ORDER),
            {
                "window_start": window_start,
                "window_end": window_end,
                "shop_pk": args.shop_pk,
            },
        ).mappings().all()

    if not rows:
        print("（窗口内没有已结算且 line_gmv > 0 的订单）")
        return 0

    n = len(rows)
    line_gmv = sum(float(r["line_gmv"]) for r in rows)
    fee = sum(float(r["fee"]) for r in rows)
    gross = sum(float(r["gross_sales"] or 0) for r in rows)
    cust_pay = sum(float(r["customer_payment"] or 0) for r in rows)
    after_disc = sum(float(r["after_discounts"] or 0) for r in rows)
    settlement = sum(float(r["settlement"] or 0) for r in rows)
    refund = sum(float(r["customer_refund"] or 0) for r in rows)
    shipping = sum(float(r["shipping"]) for r in rows)
    commission = sum(float(r["platform_commission"]) for r in rows)

    print(f"## 窗口内已结算订单 {n:,} 笔\n")
    print("## 分母候选（同一个分子 |FEE|）")
    print(f"  line_gmv = Σ(quantity × unit_price)   {line_gmv:>18,.0f}  100.00%")
    print(f"  CUSTOMER_PAYMENT（客户实付）           {cust_pay:>18,.0f}  "
          f"{_ratio(cust_pay, line_gmv)}")
    print(f"  AFTER_SELLER_DISCOUNTS_SUBTOTAL       {after_disc:>18,.0f}  "
          f"{_ratio(after_disc, line_gmv)}")
    print(f"  GROSS_SALES（折扣前挂牌价）            {gross:>18,.0f}  "
          f"{_ratio(gross, line_gmv)}")
    print()
    print(f"  |FEE| = {fee:,.0f}")
    print(f"    ÷ line_gmv            {_ratio(fee, line_gmv)}   ← 正确（公式实际使用的变量）")
    print(f"    ÷ AFTER_SELLER_DISCOUNTS {_ratio(fee, after_disc)}")
    print(f"    ÷ GROSS_SALES         {_ratio(fee, gross)}   ← 错（折扣前分母）")
    print()
    print("## 分项规模（解释口径，非费率）")
    print(f"  |PLATFORM_COMMISSION|（抽佣分项）      {commission:>18,.0f}  "
          f"{_ratio(commission, line_gmv)}")
    print(f"  |运费类|                              {shipping:>18,.0f}  "
          f"{_ratio(shipping, line_gmv)}")
    print(f"  * FEE 已含运费类 → 不可相加（会重复扣）")
    print()

    print("## 逐单恒等式核验：SETTLEMENT ≈ line_gmv + FEE + CUSTOMER_REFUND")
    print("   （FEE / CUSTOMER_REFUND 均为上游负值，此处用带符号值）")
    resid = [
        float(r["settlement"] or 0)
        - (
            float(r["line_gmv"])
            + float(r["fee_signed"] or 0)
            + float(r["customer_refund"] or 0)
        )
        for r in rows
    ]
    rel = sorted(abs(x) / float(r["line_gmv"]) for x, r in zip(resid, rows, strict=True))
    within1 = sum(1 for v in rel if v <= 0.01)
    within5 = sum(1 for v in rel if v <= 0.05)

    def pct(p: float) -> float:
        return rel[min(len(rel) - 1, int(len(rel) * p))]

    print(f"  |残差| ≤ 1% line_gmv     {within1:>5}  ({within1 / n * 100:.1f}%)")
    print(f"  |残差| ≤ 5% line_gmv     {within5:>5}  ({within5 / n * 100:.1f}%)")
    print(f"  相对残差 中位数 {pct(0.5) * 100:.3f}%   P90 {pct(0.9) * 100:.3f}%")
    signed_fee = sum(float(r["fee_signed"] or 0) for r in rows)
    print(f"  汇总口径差     "
          f"{settlement - (line_gmv + signed_fee + refund):>18,.0f}  "
          f"({(settlement - (line_gmv + signed_fee + refund)) / line_gmv * 100:+.3f}% "
          "of line_gmv)")
    print()

    print("## 逐店铺（正确口径 |FEE| / line_gmv = analytics.shop_fee_rate 任务算的东西）")
    by_shop: dict[int, list[float]] = {}
    for r in rows:
        acc = by_shop.setdefault(int(r["shop_pk"]), [0.0, 0.0, 0.0])
        acc[0] += float(r["line_gmv"])
        acc[1] += float(r["fee"])
        acc[2] += 1
    print(f"  {'shop_pk':>12}  {'r̂':>8}  {'订单数':>8}  {'line_gmv':>18}")
    for spk, (g, f, c) in sorted(by_shop.items(), key=lambda kv: -kv[1][0]):
        print(f"  {spk:>12}  {_ratio(f, g):>8}  {int(c):>8}  {g:>18,.0f}")

    print()
    print("## 对实现的影响")
    print("  · analytics.shop_fee_rate 的分母 = line_gmv（已按此实现）。")
    print("  · FEE_RATE_BASELINE 兜底值仍是文档里的 0.308 —— 若要改成实测值，")
    print("    需人工拍板（改 tts_erp_v2/analytics/spu_profitability/")
    print("    _implementation.py::FEE_RATE_BASELINE 并同步文档）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
