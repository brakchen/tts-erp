"""SPU 1736527242804888823 按天 ad spend / GMV / 有效出单量 / 净利润（页面口径 v9）。

页面口径（与 /v2/analytics/spu-roi 端点对齐）：
- ad_spend: plugin.ad_daily 单源
- GMV: valid 订单 line_gmv 之和（页面"有效GMV"列= sales,USD）
- 有效出单量: distinct valid order count
- 净利润(v7/v9):
    net_revenue = settled_net_vnd + unsettled_sales_vnd × (1 - 0.308) × (1 - total_refund/total_sales)
                  ↑ 实际 SETTLEMENT 金额（按 line 比例分摊）
    cogs = (units_sold + flc_qty) × unit_cost
    net_profit = net_revenue - cogs - spend
- 销售日界: COALESCE(paid_at, order_time) UTC
- 退款日界: case.updated_at_source UTC
- 全损海外取消日界: 同销售日界
- FX: fx.exchange_rate_snapshots 在线(USD→VND、CNY→USD),无则回退 D9
- 成本解析链: MANUAL → SOURCE_PRICE → DEFAULT_K1(40 CNY)

v9 全损口径: 完结退货(不论物流) + 海外取消(38301) 计 full_loss_qty
"""

import os

os.environ.setdefault("TTS_ERP_DB_URL", "")

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text  # noqa: E402

from tts_erp_v2.db.base import get_engine
from tts_erp_v2.db.constants import PAID_SALES_ORDER_STATUSES

D9_USD_VND = Decimal(26330)
D9_CNY_USD = Decimal("0.14774")
DEFAULT_K1_CNY = Decimal(40)
RATE = Decimal("0.308")

SPU_ID = "1736527242804888823"

e = get_engine()

# NOTE: with e.connect() 不创建新 scope（Python 的 with 块不隔离变量）。
# sales/ad/flc/refunds/USD_VND 等变量在 with 块内定义，在块外的 for 循环中使用。
# 这是合法的 Python，但拆分两段容易让人误以为变量不可用。
with e.connect() as c:
    # ====== 1. 在线 fx ======
    snap = c.execute(
        text("""
        SELECT id FROM fx.exchange_rate_snapshots
        WHERE base_code='USD' AND upstream_last_update <= now()
        ORDER BY upstream_last_update DESC LIMIT 1
    """)
    ).first()
    rates = {}
    if snap:
        rates = {
            r.target_code: r.rate
            for r in c.execute(
                text("""
            SELECT target_code, rate FROM fx.exchange_rate_snapshots s
            JOIN fx.exchange_rates r ON r.snapshot_id = s.id
            WHERE s.id = :sid
        """),
                {"sid": snap.id},
            ).all()
        }
    if {"USD", "VND", "CNY"} <= set(rates):
        USD_VND = Decimal(str(rates["VND"]))
        CNY_USD = (Decimal(1) / Decimal(str(rates["CNY"]))).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        )
        fx_source = "fx-cache"
    else:
        USD_VND = D9_USD_VND
        CNY_USD = D9_CNY_USD
        fx_source = "fixed-const"
    print(f"fx: USD→VND={USD_VND}, CNY→USD={CNY_USD} ({fx_source})")

    spu_pk = c.execute(
        text("SELECT id FROM commerce.products_spu WHERE spu_id=:sid"), {"sid": SPU_ID}
    ).scalar()
    if spu_pk is None:
        raise SystemExit(f"SPU {SPU_ID} not found in commerce.products_spu")

    # ====== 2. 成本解析链(D1) ======
    row = c.execute(
        text("""
        SELECT unit_cost FROM procurement.manual_product_costs
        WHERE spu_pk=:spk AND (valid_to IS NULL OR valid_to > now())
        ORDER BY valid_from DESC NULLS LAST LIMIT 1
    """),
        {"spk": spu_pk},
    ).first()
    if row and row.unit_cost:
        unit_cost_cny = Decimal(str(row.unit_cost))
        cost_source = "MANUAL"
    else:
        # L2: SOURCE_PRICE（1688 货源价）—— direct external_product_id = spu_id
        row2 = c.execute(
            text("""
            SELECT pp.source_unit_cost AS unit_cost
            FROM procurement.procurement_products pp
            WHERE pp.external_product_id = :sid
              AND pp.source_unit_cost IS NOT NULL
            ORDER BY pp.synced_at DESC NULLS LAST, pp.id DESC
            LIMIT 1
        """),
            {"sid": SPU_ID},
        ).first()
        if row2 and row2.unit_cost:
            unit_cost_cny = Decimal(str(row2.unit_cost))
            cost_source = "SOURCE_PRICE"
        else:
            # L2b: SOURCE_PRICE via source_item_id
            row3 = c.execute(
                text("""
                WITH offer AS (
                    SELECT pp.source_item_id
                    FROM procurement.procurement_products pp
                    WHERE pp.external_product_id = :sid
                      AND pp.source_item_id IS NOT NULL
                    ORDER BY pp.synced_at DESC NULLS LAST, pp.id DESC
                    LIMIT 1
                )
                SELECT pp.source_unit_cost AS unit_cost
                FROM offer o
                JOIN procurement.procurement_products pp
                  ON pp.source_item_id = o.source_item_id
                WHERE pp.source_unit_cost IS NOT NULL
                ORDER BY pp.synced_at DESC NULLS LAST, pp.id DESC
                LIMIT 1
            """),
                {"sid": SPU_ID},
            ).first()
            if row3 and row3.unit_cost:
                unit_cost_cny = Decimal(str(row3.unit_cost))
                cost_source = "SOURCE_PRICE"
            else:
                unit_cost_cny = DEFAULT_K1_CNY
                cost_source = "DEFAULT_K1"
    UNIT_COST_USD = (unit_cost_cny * CNY_USD).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    print(
        f"cost: {unit_cost_cny} CNY × {CNY_USD} = {UNIT_COST_USD} USD/件 ({cost_source})\n"
    )

    PAID = list(PAID_SALES_ORDER_STATUSES)

    # ====== 3. per-day settled/unsettled 拆分（关键：避免 v7 公式估算误差）======
    # 复刻 spu_roi.py 的 _SQL_ROI_SALES CTE，按 day 分组
    sales_rows = c.execute(
        text("""
        WITH order_settlement AS (
            SELECT st.order_pk, SUM(sc.amount) AS settlement_vnd
            FROM finance.settlement_transactions st
            JOIN finance.settlement_components sc
              ON sc.transaction_id = st.id AND sc.component_code = 'SETTLEMENT'
            WHERE st.order_pk IS NOT NULL
            GROUP BY st.order_pk
        ),
        order_gmv AS (
            SELECT order_pk, SUM(quantity * unit_price) AS order_gmv_vnd
            FROM commerce.sales_order_lines
            WHERE spu_pk = :spk
            GROUP BY order_pk
        ),
        lines AS (
            SELECT sl.spu_pk,
                   sl.order_pk,
                   sl.quantity,
                   sl.quantity * sl.unit_price AS line_gmv_vnd,
                   og.order_gmv_vnd,
                   os.settlement_vnd,
                   (coalesce(so.paid_at, so.order_time) AT TIME ZONE 'UTC')::date AS event_day
            FROM commerce.sales_order_lines sl
            JOIN commerce.sales_orders so ON so.id = sl.order_pk
            JOIN order_gmv og ON og.order_pk = sl.order_pk
            LEFT JOIN order_settlement os ON os.order_pk = sl.order_pk
            WHERE sl.spu_pk = :spk
              AND so.status = ANY(CAST(:paid AS text[]))
        )
        SELECT event_day AS d,
               count(DISTINCT order_pk) AS valid_orders,
               sum(quantity) AS valid_units,
               sum(line_gmv_vnd) AS valid_sales_vnd,
               sum(line_gmv_vnd) FILTER (WHERE settlement_vnd IS NULL) AS unsettled_sales_vnd,
               -- 实际 SETTLEMENT 金额（按 line 比例分摊；NUll 时按 0）
               sum(coalesce(settlement_vnd, 0) * line_gmv_vnd
                   / NULLIF(order_gmv_vnd, 0)) AS settled_net_vnd
        FROM lines
        GROUP BY event_day
        ORDER BY event_day
    """),
        {"spk": spu_pk, "paid": PAID},
    ).all()
    sales = {}
    for r in sales_rows:
        sales[str(r.d)] = {
            "valid_orders": r.valid_orders,
            "valid_units": int(r.valid_units or 0),
            "valid_sales_vnd": Decimal(str(r.valid_sales_vnd or 0)),
            "unsettled_sales_vnd": Decimal(str(r.unsettled_sales_vnd or 0)),
            "settled_net_vnd": Decimal(str(r.settled_net_vnd or 0)),
        }

    # ====== 4. 每日 ad spend ======
    ad = {
        str(r.day): float(r.spend)
        for r in c.execute(
            text("""
        SELECT day, sum(mixed_real_cost) AS spend
        FROM plugin.ad_daily
        WHERE product_id=:p AND endpoint='/oec_ads/shopping/v1/oec/stat/post_product_list'
        GROUP BY day ORDER BY day
    """),
            {"p": SPU_ID},
        ).all()
    }

    # ====== 5. 每日退款（case.updated_at_source）======
    refunds = {
        str(r.d): float(r.refund_vnd or 0)
        for r in c.execute(
            text("""
        SELECT (c.updated_at_source AT TIME ZONE 'UTC')::date AS d,
               sum(cl.refund_amount) AS refund_vnd
        FROM after_sales.cases c
        JOIN after_sales.case_lines cl ON cl.case_id=c.id
        JOIN commerce.sales_order_lines sl ON sl.id=cl.sales_order_line_id
        JOIN commerce.sales_orders so ON so.id = sl.order_pk
        WHERE sl.spu_pk=:spk
          AND so.status = ANY(CAST(:paid AS text[]))
          AND c.status IN ('CANCELLATION_REQUEST_COMPLETE','RETURN_OR_REFUND_REQUEST_COMPLETE')
          AND c.case_type IN ('REFUND_ONLY','RETURN_AND_REFUND')
        GROUP BY d
    """),
            {"spk": spu_pk, "paid": PAID},
        ).all()
    }

    # ====== 6. 每日海外取消件数（v9 全损取消桶）======
    flc = {
        str(r.d): int(r.flc_qty or 0)
        for r in c.execute(
            text("""
        SELECT (coalesce(so.paid_at, so.order_time) AT TIME ZONE 'UTC')::date AS d,
               sum(sl.quantity) AS flc_qty
        FROM commerce.sales_orders so
        JOIN commerce.sales_order_lines sl ON sl.order_pk=so.id
        WHERE sl.spu_pk=:spk
          AND so.status='CANCELLED'
          AND EXISTS (SELECT 1 FROM fulfillment.shipments sh
                      JOIN fulfillment.tracking_events te ON te.shipment_id=sh.id AND te.action_code=38301
                      WHERE sh.order_pk=so.id)
        GROUP BY d
    """),
            {"spk": spu_pk},
        ).all()
    }

# SPU 全局 refund_rate（页面公式用的 total_refund/total_sales，per-day 都用同一个）
total_valid_sales_vnd = sum(s["valid_sales_vnd"] for s in sales.values())
total_refund_vnd = sum(refunds.values())
refund_rate_spu = (
    min(
        Decimal(1),
        max(
            Decimal(0),
            Decimal(str(total_refund_vnd)) / Decimal(str(total_valid_sales_vnd)),
        ),
    )
    if total_valid_sales_vnd > 0
    else Decimal(0)
)
print(
    f"SPU 全局: total_sales_vnd={total_valid_sales_vnd:.0f}  total_refund_vnd={total_refund_vnd:.0f}  refund_rate={refund_rate_spu:.4f}\n"
)

# ====== 输出 ======
all_days = sorted(set(ad) | set(sales) | set(refunds) | set(flc))
print(
    f"{'day':<11}  {'ad_spend':>10}  {'GMV':>10}  {'valid_orders':>12}  {'valid_units':>11}  {'net_profit':>11}  (USD)"
)
print("-" * 90)

total_spend = Decimal(0)
total_gmv = Decimal(0)
total_valid_orders = 0
total_valid_units = 0
total_net_profit = Decimal(0)

for d in all_days:
    s = sales.get(
        d,
        {
            "valid_orders": 0,
            "valid_units": 0,
            "valid_sales_vnd": 0.0,
            "settled_net_vnd": 0.0,
            "unsettled_sales_vnd": 0.0,
        },
    )
    spend = Decimal(str(ad.get(d, 0.0)))
    gmv_vnd = Decimal(str(s["valid_sales_vnd"]))
    gmv_usd = gmv_vnd / USD_VND
    valid_orders = s["valid_orders"]
    valid_units = s["valid_units"]
    flc_qty = flc.get(d, 0)

    # 关键修正：用 actual settled_net + unsettled 估算
    settled_net_usd = Decimal(str(s["settled_net_vnd"])) / USD_VND
    unsettled_sales_usd = Decimal(str(s["unsettled_sales_vnd"])) / USD_VND
    net_revenue_usd = settled_net_usd + unsettled_sales_usd * (Decimal(1) - RATE) * (
        Decimal(1) - refund_rate_spu
    )
    cogs_all_usd = (Decimal(valid_units) + Decimal(flc_qty)) * UNIT_COST_USD
    net_profit_usd = net_revenue_usd - cogs_all_usd - spend

    total_spend += spend
    total_gmv += gmv_usd
    total_valid_orders += valid_orders
    total_valid_units += valid_units
    total_net_profit += net_profit_usd

    print(
        f"{d:<11}  {spend:>10.2f}  {gmv_usd:>10.2f}  {valid_orders:>12}  {valid_units:>11}  {float(net_profit_usd):>11.2f}"
    )

print("-" * 90)
print(
    f"{'TOTAL':<11}  {total_spend:>10.2f}  {total_gmv:>10.2f}  {total_valid_orders:>12}  {total_valid_units:>11}  {float(total_net_profit):>11.2f}"
)
print()
print("公式 (与 /v2/analytics/spu-roi 端点逐行一致):")
print(
    f"  net_revenue = settled_net_vnd/USD_VND + unsettled_sales_vnd/USD_VND × (1-0.308) × (1-{refund_rate_spu})"
)
print(f"  cogs = (units + flc) × {UNIT_COST_USD} USD")
print("  net_profit = net_revenue - cogs - spend")
print(f"\nfx: USD→VND={USD_VND} ({fx_source})")
