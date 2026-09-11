"""TDD 契约测试:GET /v2/analytics/spu-roi(SPU 实际 ROI 看板只读端点)+ /v2/pages/spu-roi。

口径唯一真相 = tech-doc/analytics/spu-real-roi-dashboard.md §4/§5(固定汇率
USD→VND=26330、CNY→USD=0.14774、K1=30 CNY/件、平台佣金基线 0.308（2026-09-06 实测重定）)。
本文件锁定:
1. auth:无 key 401 / readonly 200 / admin 200(端点挂 _READONLY_EXACT)
2. 分页 / spu_id 子串搜索 / 默认排序实际 ROI 升序 / sort+order
3. 业务口径:单 SPU 场景(1 有效订单 + 1 已完结退货退款 + 1 已付被取消订单
   的取消退款 case)按 §4.2 公式精确断言 sales/refund_return/net_profit/
   roi_real/roi_breakeven/platform_fee/return_loss/cpa/unit_cost_used;
   取消桶只进信息列不进净额(DEFAULT_K1 + MANUAL 两分支);
   UNPAID 等异常订单的已完结退款按 §4.2 rule 0 防御性进未归属
   (不进 refund_* 桶/行内金额,meta.unattributed_refund_lines +1);
   fee_rate=NaN/Infinity 非有限值 / 超量级(如 1e9999999) → 422 不 500
4. 行范围:无活动 SPU 默认排除、include_all=true 包含
5. totals(跨分页)与行加总一致;meta 字段齐全
6. 页面 GET 200 text/html + 标题 + 静态资源引用 + 设计 token

数据隔离:共享 api conftest 只自动 wipe shops/products_spu/manual_costs/
api_keys;sales_orders/cases/case_lines/ad_raw 需本模块自清(TEST_ 前缀 +
module autouse 前后各 wipe 一次,依赖 api conftest 的 _isolate_state 保证
teardown 顺序:先清子表,再由 conftest 清父表)。
"""

from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

import tts_erp_v2.analytics.spu_roi as spu_roi_mod

pytestmark = [pytest.mark.domain_api, pytest.mark.layer_integration]

# ─── 口径常量(v7,与实现对齐；期望值推导用)─────────────────────────────
USD_VND = Decimal(26330)
CNY_USD = Decimal("0.14774")
K1_CNY = Decimal(40)  # v7 D1: 原 30 → 40
FEE_BASELINE = Decimal("0.308")  # D10 实测重定
_Q4 = Decimal("0.0001")
_Q2 = Decimal("0.01")

# v7 框架常量（与实现一致）
RUBRIC_VERSION = "v8"
FEE_NOTE_V7 = (
    "平台佣金=平台从销售额直接扣除的全部费用(抽佣/联盟/运费类)；"
    "v7 已结算=实到账(SETTLEMENT，已含扣费)；未结算=sales×r̂×(1−spu退款率)(D5)；"
    "M19 纯信息列，不进净利"
)
COST_ASSUMPTION_V7 = (
    "按 SPU 解析：人工标注采购成交价(MANUAL)优先，其次采购单成交价(PURCHASE)、"
    "1688 货源价(SOURCE_PRICE)；均未命中 → 默认 40 CNY/件 ≈ $5.95/件；"
    "DEFAULT_K1 行页面 ⚠ 可跳 manual-costs 补录"
)

Q = "TEST_ROI_SPU"  # 搜索范围:只命中本模块 TEST SPU
PAID_ORDER_STATUS = "DELIVERED"
DAY = "2026-09-01"


def m4(v: Decimal) -> str:
    return format(v.quantize(_Q4, rounding=ROUND_HALF_UP), ".4f")


def m2(v: Decimal) -> str:
    return format(v.quantize(_Q2, rounding=ROUND_HALF_UP), ".2f")


# ─── 清理(module autouse)────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _wipe_spu_roi_rows(db_engine, _isolate_state):
    """Setup + teardown 都清 TEST_ 行。

    依赖 tests/api/conftest.py 的 _isolate_state:两个 autouse 同 scope 时
    fixture 依赖保证 setup 顺序(_isolate_state 先)与 teardown 顺序(本 fixture
    先),即本模块先删 订单/case/ad_raw 子表行,conftest 再删 products/shops。
    """
    _wipe(db_engine)
    yield
    _wipe(db_engine)


# ─── 在线汇率(2026-09-06:ROI 账页换算已接 fx.* 缓存,D9 常量回退)─────
# autouse 注入一张与旧 D9 常量等值的 USD 快照(2099 时间戳保证最新):既有
# 全部金额/ROI 期望(26330 / 0.14774 派生)不变,同时让实现路径走 fx-cache。
FX_SEED_TS = "2099-09-06T00:00:00+00:00"
FX_SEED_VND = "26330"
FX_SEED_CNY = "6.7686473"  # 1/CNY 量化 8dp = 0.14774001 → .4f 0.1477


def _seed_fx(db_engine) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal insert, bound params
        sid = conn.execute(
            text(
                "INSERT INTO fx.exchange_rate_snapshots "
                "(base_code, upstream_last_update, next_update_at, fetched_at, rates_count) "
                "VALUES ('USD', :ts, :ts2, now(), 3) RETURNING id"
            ),
            {"ts": FX_SEED_TS, "ts2": "2099-09-07T00:00:00+00:00"},
        ).scalar()
        for code, rate in [("USD", "1"), ("VND", FX_SEED_VND), ("CNY", FX_SEED_CNY)]:
            # pi-lens-ignore: python-sql-injection — literal insert, bound params
            conn.execute(
                text(
                    "INSERT INTO fx.exchange_rates "
                    "(snapshot_id, base_code, target_code, rate) "
                    "VALUES (:sid, 'USD', :c, :r)"
                ),
                {"sid": sid, "c": code, "r": rate},
            )


def _del_fx(db_engine) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection — literal delete, bound param
        conn.execute(
            text(
                "DELETE FROM fx.exchange_rate_snapshots "
                "WHERE base_code = 'USD' AND upstream_last_update = :ts"
            ),
            {"ts": FX_SEED_TS},
        )


@pytest.fixture(autouse=True)
def _fx_online_consts(db_engine, _isolate_state):
    """ROI 换算走在线 fx 缓存:注入匹配常量快照;teardown 先于 _wipe 移除。"""
    _seed_fx(db_engine)
    yield
    _del_fx(db_engine)


def _wipe(db_engine) -> None:
    with db_engine.begin() as conn:
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text("DELETE FROM analytics.ad_daily WHERE seller_id LIKE 'TEST_%'")
        )
        conn.execute(
            text("DELETE FROM analytics.ad_today WHERE seller_id LIKE 'TEST_%'")
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM after_sales.case_lines "
                "WHERE case_id IN ("
                "  SELECT id FROM after_sales.cases c "
                "  WHERE c.shop_pk IN ("
                "    SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%'"
                "  )"
                ")"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM after_sales.cases c "
                "WHERE c.shop_pk IN ("
                "  SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%'"
                ")"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM commerce.sales_order_lines "
                "WHERE order_pk IN ("
                "  SELECT id FROM commerce.sales_orders "
                "  WHERE shop_pk IN ("
                "    SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%'"
                "  )"
                ")"
            )
        )
        # pi-lens-ignore: python-sql-injection
        conn.execute(
            text(
                "DELETE FROM commerce.sales_orders "
                "WHERE shop_pk IN ("
                "  SELECT id FROM commerce.shops WHERE shop_id LIKE 'TEST_%'"
                ")"
            )
        )


# ─── 造数 helpers(handler 用独立 session,必须真 commit)────────────────


def _seed_shop(sess, seller: str) -> int:
    # pi-lens-ignore: python-sql-injection
    return sess.execute(
        text(
            "INSERT INTO commerce.shops (platform, shop_id, account_name, status, data_source) "
            "VALUES ('tiktok', :sid, :name, 'active', 'api') RETURNING id"
        ),
        {"sid": seller, "name": f"{seller} 店铺"},
    ).scalar_one()


def _seed_spu(
    sess,
    shop_pk: int,
    spu_id: str,
    *,
    title: str | None = None,
    status: str = "ACTIVATE",
) -> int:
    # pi-lens-ignore: python-sql-injection
    return sess.execute(
        text(
            "INSERT INTO commerce.products_spu "
            "(shop_pk, spu_id, title, status, main_image_url) "
            "VALUES (:shop, :sid, :title, :status, 'https://img.test/x.png') "
            "RETURNING id"
        ),
        {
            "shop": shop_pk,
            "sid": spu_id,
            "title": title or f"{spu_id} 标题",
            "status": status,
        },
    ).scalar_one()


def _seed_ad_dump(
    sess,
    *,
    seller: str,
    product_id: str,
    campaign_id: str,
    spend: str,
    orders: str,
    gmv: str,
    day: str = DAY,
) -> None:
    """post_product_list 一条 ad_daily(1 campaign×SPU×1 day)。"""
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(
            """
            INSERT INTO analytics.ad_daily (
                seller_id, advertiser_id, campaign_id, product_id, endpoint, day,
                mixed_real_cost, onsite_roi2_shopping_sku, onsite_roi2_shopping_value,
                onsite_mixed_real_roi2_shopping, metrics_extra, created_at
            ) VALUES (
                :seller, :advertiser, :campaign, :product_id,
                '/oec_ads/shopping/v1/oec/stat/post_product_list',
                CAST(:day AS date),
                CAST(:spend AS NUMERIC), CAST(:orders AS BIGINT), CAST(:gmv AS NUMERIC),
                NULL, '{}'::JSONB, now()
            )
            ON CONFLICT ON CONSTRAINT uq_ad_daily DO UPDATE SET
                mixed_real_cost = EXCLUDED.mixed_real_cost,
                onsite_roi2_shopping_sku = EXCLUDED.onsite_roi2_shopping_sku,
                onsite_roi2_shopping_value = EXCLUDED.onsite_roi2_shopping_value,
                updated_at = now()
            """
        ),
        {
            "seller": seller,
            "advertiser": "TEST_ADV",
            "campaign": campaign_id,
            "product_id": product_id,
            "day": day,
            "spend": spend,
            "orders": orders,
            "gmv": gmv,
        },
    )


def _seed_order_line(
    sess,
    *,
    shop_pk: int,
    spu_pk: int,
    order_id: str,
    status: str,
    line_ext: str,
    qty: str,
    unit_price: str,
    paid: bool,
    paid_iso: str | None = None,
) -> int:
    """插入一单(可带多行);返回 order id。

    paid_iso 自定义 paid_at(ISO,默认 2026-09-01),供窗口裁剪测试。
    """
    paid_at = None
    if paid:
        paid_at = paid_iso or "2026-09-01T08:00:00+00:00"
    # pi-lens-ignore: python-sql-injection
    order_pk = sess.execute(
        text(
            "INSERT INTO commerce.sales_orders "
            "(shop_pk, order_id, status, currency, paid_at) "
            "VALUES (:shop, :oid, :status, 'VND', CAST(:paid AS timestamptz)) "
            "RETURNING id"
        ),
        {"shop": shop_pk, "oid": order_id, "status": status, "paid": paid_at},
    ).scalar_one()
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(
            "INSERT INTO commerce.sales_order_lines "
            "(order_pk, external_line_id, spu_pk, quantity, unit_price, currency) "
            "VALUES (:o, :ext, :spu, CAST(:qty AS numeric), "
            "CAST(:price AS numeric), 'VND')"
        ),
        {
            "o": order_pk,
            "ext": line_ext,
            "spu": spu_pk,
            "qty": qty,
            "price": unit_price,
        },
    )
    return order_pk


def _seed_case(
    sess,
    *,
    shop_pk: int,
    order_pk: int,
    ext_case: str,
    case_type: str,
    status: str,
    lines: list[
        tuple[int, str, str, str | None]
    ],  # (sales_order_line_id, ext, qty, refund_amt)
    updated_iso: str | None = None,
) -> None:
    updated = updated_iso or "2026-09-03T00:00:00+00:00"
    case_pk = sess.execute(
        text(
            "INSERT INTO after_sales.cases "
            "(shop_pk, order_pk, external_case_id, case_type, status, "
            " created_at_source, updated_at_source, currency) "
            "VALUES (:shop, :o, :ec, :ct, :st, "
            " CAST('2026-09-02T00:00:00+00:00' AS timestamptz), "
            " CAST(:updated AS timestamptz), 'VND') "
            "RETURNING id"
        ),
        {
            "shop": shop_pk,
            "o": order_pk,
            "ec": ext_case,
            "ct": case_type,
            "st": status,
            "updated": updated,
        },
    ).scalar_one()
    for line_pk, ext, qty, amt in lines:
        sess.execute(
            text(
                "INSERT INTO after_sales.case_lines "
                "(case_id, sales_order_line_id, external_case_line_id, "
                " quantity, refund_amount, currency) "
                "VALUES (:c, :sl, :ext, CAST(:qty AS numeric), "
                " CAST(:amt AS numeric), 'VND')"
            ),
            {"c": case_pk, "sl": line_pk, "ext": ext, "qty": qty, "amt": amt},
        )


def _fetch_spu_line_id(sess, order_id: str) -> int:
    return sess.execute(
        text(
            "SELECT id FROM commerce.sales_order_lines "
            "WHERE order_pk = (SELECT id FROM commerce.sales_orders "
            "WHERE order_id = :oid LIMIT 1) LIMIT 1"
        ),
        {"oid": order_id},
    ).scalar_one()


def _seed_scenario_a(sess) -> int:
    """主口径场景 SPU A(含默认成本 DEFAULT_K1):

    - 广告:campaign C1 × TEST_ROI_SPU_A,spend=10.00 USD、ad_orders=5、gmv=50
    - 有效销售:1 单(DELIVERED,已付)5 件 × 526,600 VND(=$20/件) → sales=$100
    - 退货退款:1 条已完结 RETURN_AND_REFUND,1 件退款 526,600 VND(=$20)
    - 取消退款:另 1 张 CANCELLED 单(已付后被取消),case 完结,2 行取消:
      1 行有金额(526,600 VND=$20)、1 行金额 NULL(missing_lines=1)

    期望(见测试断言):sales=100.0000 / refund_net=20.0000(取消桶不入净额)/
    net_profit=17.0390 / roi_real=7.56 / roi_breakeven=2.79 / cpa=2.0000。
    """
    seller = "TEST_SELLER_A"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_A")
    _seed_ad_dump(
        sess,
        seller=seller,
        product_id="TEST_ROI_SPU_A",
        campaign_id="TEST_CAMP_A",
        spend="10.00",
        orders="5",
        gmv="50.00",
    )
    # 有效销售单(1 行 5 件 × $20)
    o1 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_A1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_A1",
        qty="5",
        unit_price="526600",
        paid=True,
    )
    line1 = _fetch_spu_line_id(sess, "TEST_ORDER_A1")
    # 已付被取消单(不计销售,取消退款进信息列)
    o2 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_A2",
        status="CANCELLED",
        line_ext="TEST_LINE_A2",
        qty="2",
        unit_price="526600",
        paid=True,
    )
    line2 = _fetch_spu_line_id(sess, "TEST_ORDER_A2")
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o1,
        ext_case="TEST_CASE_A1",
        case_type="RETURN_AND_REFUND",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(line1, "TEST_CLINE_A1", "1", "526600")],
    )
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o2,
        ext_case="TEST_CASE_A2",
        case_type="CANCELLATION",
        status="CANCELLATION_REQUEST_COMPLETE",
        lines=[
            (line2, "TEST_CLINE_A2", "1", "526600"),
            (line2, "TEST_CLINE_A3", "1", None),
        ],
    )
    return spu_pk


def _seed_extra_order_line(
    sess, *, order_id: str, spu_pk: int, line_ext: str, qty: str, unit_price: str
) -> None:
    """给已存在订单补插一行(跨 SPU 订单用:同一 order_id 多行)。"""
    # pi-lens-ignore: python-sql-injection
    sess.execute(
        text(
            "INSERT INTO commerce.sales_order_lines "
            "(order_pk, external_line_id, spu_pk, quantity, unit_price, currency) "
            "SELECT id, :ext, :spu, CAST(:qty AS numeric), "
            "CAST(:price AS numeric), 'VND' FROM commerce.sales_orders "
            "WHERE order_id = :oid"
        ),
        {
            "ext": line_ext,
            "spu": spu_pk,
            "qty": qty,
            "price": unit_price,
            "oid": order_id,
        },
    )


def _seed_cross_spu_orders(sess) -> tuple[int, int]:
    """跨 SPU 去重场景(review MINOR 补测):两个 SPU X/Y 共享同一张订单。

    - TEST_ROI_SPU_X / TEST_ROI_SPU_Y(同店铺,均 ACTIVATE)
    - 有效订单 O1(DELIVERED,已付):两行,分别挂 X(1×$10)与 Y(1×$10)
      → 每 SPU 行 order_count=1,但全局 distinct 有效单只有 1
    - 已付被取消订单 O2(CANCELLED,已付):两行 X/Y(各 1×$5)
      → 全局 distinct 取消单 = 1;取消原额 10 计入 GMV

    期望:行加总(∑order_count=2)≠ totals.order_count=1;GMV = 20+10。
    """
    seller = "TEST_SELLER_XY"
    shop_pk = _seed_shop(sess, seller)
    x = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_X")
    y = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_Y")
    # O1: 有效单(首行挂 X,补一行挂 Y → 同一张单跨两 SPU)
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=x,
        order_id="TEST_ORDER_XY1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_XY1",
        qty="1",
        unit_price="263300",
        paid=True,  # $10
    )
    _seed_extra_order_line(
        sess,
        order_id="TEST_ORDER_XY1",
        spu_pk=y,
        line_ext="TEST_LINE_XY2",
        qty="1",
        unit_price="263300",  # $10
    )
    # O2: 已付被取消,同样跨两 SPU(各 $5)
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=x,
        order_id="TEST_ORDER_XY2",
        status="CANCELLED",
        line_ext="TEST_LINE_XY3",
        qty="1",
        unit_price="131650",
        paid=True,  # $5
    )
    _seed_extra_order_line(
        sess,
        order_id="TEST_ORDER_XY2",
        spu_pk=y,
        line_ext="TEST_LINE_XY4",
        qty="1",
        unit_price="131650",  # $5
    )
    return x, y


def _seed_spu_b(sess) -> int:
    """SPU B:spend=50、销售 $90(3×$30)、无退款 → roi_real=1.80。"""
    seller = "TEST_SELLER_B"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_B")
    _seed_ad_dump(
        sess,
        seller=seller,
        product_id="TEST_ROI_SPU_B",
        campaign_id="TEST_CAMP_B",
        spend="50.00",
        orders="2",
        gmv="100.00",
    )
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_B1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_B1",
        qty="3",
        unit_price="789900",  # $30/件 → sales=$90
        paid=True,
    )
    return spu_pk


def _seed_spu_c(sess) -> int:
    """SPU C:spend=10、销售 $30(3×$10)、无退款 → roi_real=3.00。"""
    seller = "TEST_SELLER_C"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_C")
    _seed_ad_dump(
        sess,
        seller=seller,
        product_id="TEST_ROI_SPU_C",
        campaign_id="TEST_CAMP_C",
        spend="10.00",
        orders="2",
        gmv="20.00",
    )
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_C1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_C1",
        qty="3",
        unit_price="263300",  # $10/件 → sales=$30
        paid=True,
    )
    return spu_pk


def _seed_inactive_spu(sess) -> int:
    """无任何活动的 SPU(仅目录行)。"""
    shop_pk = _seed_shop(sess, "TEST_SELLER_INACTIVE")
    return _seed_spu(sess, shop_pk, "TEST_ROI_SPU_INACTIVE")


def _seed_spu_manual(sess) -> int:
    """命中人工成本的 SPU(unit_cost=25 CNY → MANUAL)。"""
    seller = "TEST_SELLER_MANUAL"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_MANUAL")
    sess.execute(
        text(
            "INSERT INTO procurement.manual_product_costs "
            "(spu_pk, unit_cost, currency, valid_from, valid_to, note, created_by) "
            "VALUES (:spu, 25.0000, 'CNY', now(), NULL, 'TEST 成本', 'test')"
        ),
        {"spu": spu_pk},
    )
    _seed_ad_dump(
        sess,
        seller=seller,
        product_id="TEST_ROI_SPU_MANUAL",
        campaign_id="TEST_CAMP_M",
        spend="10.00",
        orders="5",
        gmv="50.00",
    )
    o1 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_M1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_M1",
        qty="5",
        unit_price="526600",
        paid=True,
    )
    line1 = _fetch_spu_line_id(sess, "TEST_ORDER_M1")
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o1,
        ext_case="TEST_CASE_M1",
        case_type="RETURN_AND_REFUND",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(line1, "TEST_CLINE_M1", "1", "526600")],
    )
    return spu_pk


def _seed_unpaid_refund_spu(sess) -> int:
    """异常订单退款场景(§4.2 rule 0):有效销售单 + UNPAID 订单已完结退款。

    - TEST_ORDER_AB1:有效销售(DELIVERED,已付)5 件×$20 → sales $100
    - TEST_ORDER_AB2:UNPAID(白名单外且非 CANCELLED)订单,1 行已完结
      RETURN_AND_REFUND case,退 1 件 526,600 VND(=$20),有 spu_pk

    期望:该退款不进 refund_net/refund_cancelled 桶、不进净额与行内金额
    (net_profit 视同无此退款),只在 meta.unattributed_refund_lines 显式
    +1 计数(防御性未归属),不静默丢。
    """
    seller = "TEST_SELLER_AB"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_AB")
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_AB1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_AB1",
        qty="5",
        unit_price="526600",  # $20/件 → sales $100
        paid=True,
    )
    o2 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_AB2",
        status="UNPAID",
        line_ext="TEST_LINE_AB2",
        qty="1",
        unit_price="526600",
        paid=False,
    )
    line2 = _fetch_spu_line_id(sess, "TEST_ORDER_AB2")
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o2,
        ext_case="TEST_CASE_AB1",
        case_type="RETURN_AND_REFUND",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(line2, "TEST_CLINE_AB1", "1", "526600")],
    )
    return spu_pk


def _seed_window_spu(sess) -> int:
    """窗口裁剪场景 SPU(仅销售+退款,无广告):

    - 窗内有效销售单 1(paid 2026-09-10)3 件×$20= $60
    - 窗外有效销售单 2(paid 2026-08-10)2 件×$20= $40
    - 窗内完结退货退款 1 件 $20(updated 2026-09-12)
    - 窗外完结退货退款 1 件 $20(updated 2026-08-20)

    默认(不传 w_start/w_end)= 全历史累计:units=5、sales=$100、
    refund_return_qty=2;传 2026-09-01~09-30 → 只留窗内:units=3、
    sales=$60、refund_return_qty=1。
    """
    seller = "TEST_SELLER_WIN"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_WIN")
    o1 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_W1",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_W1",
        qty="3",
        unit_price="526600",  # $20/件 → $60
        paid=True,
        paid_iso="2026-09-10T08:00:00+00:00",
    )
    line1 = _fetch_spu_line_id(sess, "TEST_ORDER_W1")
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_W2",
        status=PAID_ORDER_STATUS,
        line_ext="TEST_LINE_W2",
        qty="2",
        unit_price="526600",  # $20/件 → $40
        paid=True,
        paid_iso="2026-08-10T08:00:00+00:00",
    )
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o1,
        ext_case="TEST_CASE_W1",
        case_type="RETURN_AND_REFUND",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(line1, "TEST_CLINE_W1", "1", "526600")],
        updated_iso="2026-09-12T00:00:00+00:00",
    )
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o1,
        ext_case="TEST_CASE_W2",
        case_type="RETURN_AND_REFUND",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(line1, "TEST_CLINE_W2", "1", "526600")],
        updated_iso="2026-08-20T00:00:00+00:00",
    )
    return spu_pk


def _seed_status_mix(sess) -> None:
    """include_all 行范围(§5.1-7):ACTIVATE vs DEACTIVATE 目录状态。

    - TEST_ROI_SPU_ACTIVE_ON / DEACTIVE_ON 各有有效销售单(有活动)
    - TEST_ROI_SPU_ACTIVE_IDLE / DEACTIVE_IDLE 仅目录行(无活动)
    """
    for sid, status in (
        ("TEST_ROI_SPU_ACTIVE_ON", "ACTIVATE"),
        ("TEST_ROI_SPU_DEACTIVE_ON", "DEACTIVATE"),
    ):
        shop = _seed_shop(sess, f"TEST_SELLER_{sid}")
        spu = _seed_spu(sess, shop, sid, status=status)
        _seed_order_line(
            sess,
            shop_pk=shop,
            spu_pk=spu,
            order_id=f"TEST_ORDER_{sid}",
            status=PAID_ORDER_STATUS,
            line_ext=f"TEST_LINE_{sid}",
            qty="1",
            unit_price="263300",  # $10
            paid=True,
        )
    for sid, status in (
        ("TEST_ROI_SPU_ACTIVE_IDLE", "ACTIVATE"),
        ("TEST_ROI_SPU_DEACTIVE_IDLE", "DEACTIVATE"),
    ):
        shop = _seed_shop(sess, f"TEST_SELLER_{sid}")
        _seed_spu(sess, shop, sid, status=status)


def _commit_all(sess) -> None:
    sess.commit()


def _seed(sess, fn) -> int:
    """在真 commit 的 session 里执行一个 seed 函数。"""
    result = fn(sess)
    sess.commit()
    return result


# ─── auth ─────────────────────────────────────────────────────────────


def test_spu_roi_requires_auth(api_client):
    assert api_client.get("/v2/analytics/spu-roi").status_code == 401


def test_spu_roi_readonly_and_admin_ok(api_client, readonly_key, admin_key):
    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": Q, "limit": 1},
    )
    assert r.status_code == 200, r.text
    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {admin_key}"},
        params={"q": Q, "limit": 1},
    )
    assert r.status_code == 200, r.text


# ─── 业务口径(§4.2 公式)──────────────────────────────────────────────


def test_spu_roi_math_single_spu_default_k1(api_client, readonly_key, db_engine):
    """单 SPU(1 有效单 + 1 已完结退货退款 + 1 已付被取消单)v7 公式断言。

    K1=40 CNY/件（v7 D1）；净利 v7（D1/D5）:
      net_revenue = settled(0) + 100×(1−0.308)×(1−refund_rate_spu)
      refund_rate_spu = refund_net / sales = 20/100 = 0.20
      net_revenue = 0 + 100×0.692×0.80 = 55.36
      cogs_all = 5件 × 40×0.14774 = 29.5440
      net_profit = 55.36 − 29.5440 − 10 = 15.8160
      return_loss = full_loss_qty×cost = 0×... = 0（场景无 38301 物流）
      roi_real = 55.36/10 = 5.54
      COGS_kept = (5−1)×5.9088 = 23.6352；breakeven = 55.36/(55.36−23.6352) = 1.75
      platform_fee = 0.308×100 = 30.80
      full_loss_rate = 0/(5+0) = 0.00
    """
    with Session(db_engine) as sess:
        spu_pk = _seed(sess, _seed_scenario_a)

    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_A"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1, body["items"]
    item = body["items"][0]

    assert item["spu_pk"] == spu_pk
    assert item["spu_id"] == "TEST_ROI_SPU_A"
    assert item["title"] == "TEST_ROI_SPU_A 标题"
    assert item["status"] == "ACTIVATE"
    assert item["shop_name"] == "TEST_SELLER_A 店铺"

    # 广告侧(USD 原生)
    assert item["ad_count"] == 1
    assert item["ad_orders"] == 5
    assert item["spend"] == "10.0000"
    assert item["gmv_ad"] == "50.0000"
    assert item["roi_l0"] == "5.00"
    assert item["ad_first_day"] == DAY
    assert item["ad_last_day"] == DAY

    # 销售侧(有效单,VND→USD)
    assert item["order_count"] == 1
    assert item["units_sold"] == 5
    assert item["sales"] == "100.0000"

    # v7 分层字段（场景无 SETTLEMENT → settled=0, unsettled=100）
    assert item["settled_order_count"] == 0
    assert Decimal(item["net_revenue"]) == Decimal("55.3600")
    assert Decimal(item["settled_sales"]) == Decimal("0.0000")
    assert Decimal(item["unsettled_sales"]) == Decimal("100.0000")
    assert Decimal(item["settled_net"]) == Decimal("0.0000")

    # 退款桶不变
    assert item["refund_only_qty"] == 0
    assert item["refund_only_amount"] == "0.0000"
    assert item["refund_return_qty"] == 1
    assert item["refund_return_amount"] == "20.0000"
    assert item["refund_net_qty"] == 1
    assert item["refund_net_amount"] == "20.0000"
    assert item["refund_rate"] == "0.20"

    # 已付被取消订单退款（信息列）
    assert item["refund_cancelled_qty"] == 2
    assert item["refund_cancelled_amount"] == "20.0000"
    assert item["refund_cancelled_missing_lines"] == 1

    # v7 全损（场景无 38301 → full_loss_qty=0；分母 5 → rate=0.00）
    assert item["full_loss_qty"] == 0
    assert item["full_loss_cancelled_qty"] == 0
    assert item["full_loss_rate"] == "0.00"

    # 成本：DEFAULT_K1 = 40 CNY × 0.14774 ≈ 5.9088
    assert item["cost_source"] == "DEFAULT_K1"
    assert Decimal(item["unit_cost_used"]) == Decimal(K1_CNY * CNY_USD).quantize(
        _Q4, rounding=ROUND_HALF_UP
    )

    # v7 利润域
    assert Decimal(item["return_loss"]) == Decimal("0.0000")
    assert Decimal(item["platform_fee"]) == Decimal("30.8000")
    assert Decimal(item["net_profit"]) == Decimal("15.8120")
    assert item["roi_real"] == "5.54"
    assert item["roi_breakeven"] == "1.75"
    assert item["cpa"] == "2.0000"

    # meta v7
    assert body["meta"]["rubric_version"] == RUBRIC_VERSION
    assert body["meta"]["fee"]["note"] == FEE_NOTE_V7
    assert body["meta"]["cost_assumption"] == COST_ASSUMPTION_V7

    # totals
    assert body["totals"]["row_count"] == 1
    assert body["totals"]["spend"] == "10.0000"
    assert body["totals"]["sales"] == "100.0000"
    assert body["totals"]["gmv"] == "140.0000"
    assert body["totals"]["order_count"] == 1
    assert body["totals"]["cancelled_order_count"] == 1
    assert body["totals"]["total_orders"] == 2
    assert body["totals"]["refund_net_amount"] == "20.0000"
    assert body["totals"]["net_profit"] == "15.8120"


def test_spu_roi_totals_cross_spu_dedup_and_gmv_split(
    api_client, readonly_key, db_engine
):
    """review MINOR:一张跨 SPU 的订单(两 SPU 各一行)在 totals 里只计一次。

    - 行加总 ∑order_count = 2(X/Y 各 1)≠ totals.order_count = 1(全局去重)
    - 已付被取消同理:totals.cancelled_order_count = 1
    - GMV 拆分:X/Y 各分摊有效 $10 + 取消原额 $5 → totals.gmv = 20+10
    """
    with Session(db_engine) as sess:
        _seed(sess, _seed_cross_spu_orders)  # 返回 (X,Y) 主键,此处仅造数

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get("/v2/analytics/spu-roi", headers=h, params={"q": Q})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2, body["items"]
    row_sum_orders = sum(i["order_count"] for i in body["items"])
    assert row_sum_orders == 2  # X、Y 行各计 1
    t = body["totals"]
    assert t["row_count"] == 2
    assert t["order_count"] == 1, "跨 SPU 订单在 totals 应全局去重"
    assert t["cancelled_order_count"] == 1
    assert t["total_orders"] == 2
    assert t["sales"] == "20.0000"  # 10+10(行级各自归属)
    assert t["gmv"] == "30.0000"  # 有效 20 + 取消原额 10
    # 单行归属校验:每 SPU 只带自己那行金额
    by_id = {i["spu_id"]: i for i in body["items"]}
    assert by_id["TEST_ROI_SPU_X"]["sales"] == "10.0000"
    assert by_id["TEST_ROI_SPU_Y"]["sales"] == "10.0000"
    assert by_id["TEST_ROI_SPU_X"]["order_count"] == 1
    assert by_id["TEST_ROI_SPU_Y"]["order_count"] == 1


def _seed_refund_on_shared_order_y_line(sess) -> tuple[int, int]:
    """跨 SPU 订单退款归属(P2-1 回归):O1 含 X/Y 两行,仅 Y 行有退款 case。

    行级退款订单数必须按【行】归属:Y 的 refund_order_count=1/refund_rate_qty=1.00,
    X 保持 0.00(不能因同订单另一 SPU 行退款而虚增)。
    """
    seller = "TEST_SELLER_RY"
    shop_pk = _seed_shop(sess, seller)
    x = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_RY_X")
    y = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_RY_Y")
    o1 = _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=x,
        order_id="TEST_ORDER_RY1",
        status="DELIVERED",
        line_ext="TEST_LINE_RY1",
        qty="1",
        unit_price="263300",
        paid=True,  # $10
    )
    _seed_extra_order_line(
        sess,
        order_id="TEST_ORDER_RY1",
        spu_pk=y,
        line_ext="TEST_LINE_RY2",
        qty="1",
        unit_price="263300",  # $10
    )
    y_line = sess.execute(
        text(
            "SELECT id FROM commerce.sales_order_lines "
            "WHERE order_pk = (SELECT id FROM commerce.sales_orders "
            "WHERE order_id = 'TEST_ORDER_RY1' LIMIT 1) "
            "AND external_line_id = 'TEST_LINE_RY2'"
        )
    ).scalar_one()
    _seed_case(
        sess,
        shop_pk=shop_pk,
        order_pk=o1,
        ext_case="TEST_CASE_RY1",
        case_type="RETURN_AND_REFUND",
        status="RETURN_OR_REFUND_REQUEST_COMPLETE",
        lines=[(y_line, "TEST_CLINE_RY1", "1", "263300")],
    )
    return x, y


def test_spu_roi_refund_order_count_attributed_to_own_line(
    api_client, readonly_key, db_engine
):
    """P2-1:退款订单数按行归属——共享订单仅 Y 行退款时,X 的退货率不虚增。"""
    with Session(db_engine) as sess:
        _seed(sess, _seed_refund_on_shared_order_y_line)

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get(
        "/v2/analytics/spu-roi", headers=h, params={"q": "TEST_ROI_SPU_RY"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {i["spu_id"]: i for i in body["items"]}
    assert set(by_id) == {"TEST_ROI_SPU_RY_X", "TEST_ROI_SPU_RY_Y"}
    assert by_id["TEST_ROI_SPU_RY_X"]["refund_rate_qty"] == "0.00"
    assert by_id["TEST_ROI_SPU_RY_X"]["refund_net_amount"] == "0.0000"
    assert (
        by_id["TEST_ROI_SPU_RY_Y"]["refund_rate_qty"] == "1.00"
    )  # 1 退货单 / 1 有效单
    assert by_id["TEST_ROI_SPU_RY_Y"]["refund_net_amount"] == "10.0000"


def _seed_cod_and_unpaid_cancelled(sess) -> int:
    """全链状态口径回归场景(2026-09-06):COD 在途单 + 未收款取消单。

    - TEST_ROI_SPU_COD: 有效单(DELIVERED,paid,3×$10=$30)
    - COD 在途单(IN_TRANSIT,paid_at=NULL,行 2×$10=$20) → 算"有效订单"
    - 未收款取消单(CANCELLED,paid_at=NULL,行 1×$10=$10) → 算"取消单"

    2026-09-06 全链状态口径:行级与 totals 都不再卡 paid_at ——
    order_count=2(有效已付+COD在途)、cancelled=1、total=3;
    sales=50(含在途COD 20,不含取消)、gmv=60(含取消原额);
    派生 net_profit/units_sold 等也随行级 sales 走状态口径。
    """
    seller = "TEST_SELLER_COD"
    shop_pk = _seed_shop(sess, seller)
    spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_COD")
    _seed_ad_dump(
        sess,
        seller=seller,
        product_id="TEST_ROI_SPU_COD",
        campaign_id="TEST_CAMP_COD",
        spend="10.00",
        orders="3",
        gmv="30.00",
    )
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_COD1",
        status="DELIVERED",
        line_ext="TEST_LINE_COD1",
        qty="3",
        unit_price="263300",
        paid=True,  # $30
    )
    # COD 在途:状态白名单但钱未收(paid_at 不落)
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_COD2",
        status="IN_TRANSIT",
        line_ext="TEST_LINE_COD2",
        qty="2",
        unit_price="263300",
        paid=False,  # $20 未收款
    )
    # 未收款取消:CANCELLED 且 paid_at 无
    _seed_order_line(
        sess,
        shop_pk=shop_pk,
        spu_pk=spu_pk,
        order_id="TEST_ORDER_COD3",
        status="CANCELLED",
        line_ext="TEST_LINE_COD3",
        qty="1",
        unit_price="263300",
        paid=False,  # $10 未收款取消
    )
    return spu_pk


def test_spu_roi_totals_order_status_scope_cod_shop(
    api_client, readonly_key, db_engine
):
    """2026-09-06:全链状态口径(COD 店下单即算单)。

    行级与 totals 一致:销售/件数/单量不看 paid_at(COD 在途计入有效、
    未收款取消计入取消),GMV=全部原始行金额。金额主指标(净利润/ROI)
    跟随行级 sales 同步为状态口径。
    """
    with Session(db_engine) as sess:
        _seed(sess, _seed_cod_and_unpaid_cancelled)

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get(
        "/v2/analytics/spu-roi", headers=h, params={"q": "TEST_ROI_SPU_COD"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    # 行级也按状态口径(2026-09-06 全链):COD 在途算有效订单/件数/销售
    assert item["order_count"] == 2, "行级有效订单数含 COD 在途单"
    assert item["units_sold"] == 5  # 3(已收) + 2(在途)
    assert item["sales"] == "50.0000"  # 30 + 20,含在途 COD
    assert item["refund_net_amount"] == "0.0000"
    # 派生:净现金 50 − COGS_all(5×4.4322=22.161) − spend 10 − fee(50×0.1156=5.78)
    assert item["platform_fee"] == m4(Decimal(50) * FEE_BASELINE)  # 5.7800
    assert item["net_profit"] == m4(
        Decimal(50)
        - Decimal(5) * K1_CNY * CNY_USD
        - Decimal(10)
        - Decimal(50) * FEE_BASELINE
    )
    # 行内新列(2026-09-06 列集):销售=GMV全单(50+10 取消原额)、取消单量、取消率、退货率(单量)
    assert item["gmv_sales"] == "60.0000", "行内销售 = 有效销售 + 取消原额"
    assert item["cancelled_order_count"] == 1
    assert item["cancel_rate"] == m2(Decimal(1) / Decimal(3))  # 1/(2+1)=0.33
    assert item["refund_rate_qty"] == "0.00"  # 0 退货订单 / 2 有效单
    t = body["totals"]
    assert t["order_count"] == 2
    assert t["cancelled_order_count"] == 1, "未收款取消单应计入取消单(状态口径)"
    assert t["total_orders"] == 3
    assert t["sales"] == "50.0000"  # totals.sales 现在与行级一致 = 状态口径
    assert t["gmv"] == "60.0000", "GMV=全部订单原始行金额(含在途COD与取消原额)"


def test_spu_roi_manual_cost_source(api_client, readonly_key, db_engine):
    """命中 manual_product_costs 有效行 → cost_source=MANUAL,unit_cost_used 用真值。

    v7 公式（D1 MANUAL + D4 B 全损口径）:
      cost=25 CNY → unit_cost=25×0.14774=3.6935；no tracking → full_loss_qty=0
      net_revenue = 0 + 100×(1−0.308)×(1−0.20) = 55.36
      cogs = 5 × 3.6935 = 18.4675
      net_profit = 55.36 − 18.4675 − 10 = 26.8925
    """
    with Session(db_engine) as sess:
        spu_pk = _seed(sess, _seed_spu_manual)

    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_MANUAL"},
    )
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["spu_pk"] == spu_pk
    assert item["cost_source"] == "MANUAL"
    assert Decimal(item["unit_cost_used"]) == Decimal(25) * CNY_USD  # 3.6935
    # D4 B: return_loss 基于 38301 全损口径（场景无物流 → 0）
    assert Decimal(item["return_loss"]) == Decimal("0.0000")
    # v7 net_profit（D1 MANUAL 25 CNY 成本 + D5 未结算折算）
    assert Decimal(item["net_profit"]) == Decimal("26.8925")


def test_spu_roi_unpaid_order_refund_defensive_unattributed(
    api_client, readonly_key, db_engine
):
    """§4.2 rule 0:UNPAID 等异常订单的已完结退款防御性进未归属。

    该退款行有 spu_pk 但订单状态不在白名单也不是 CANCELLED → 不进
    refund_net/refund_cancelled 桶、不进净额与行内金额(net_profit 视同
    无此退款),只在 meta.unattributed_refund_lines 显式 +1,不静默丢。
    """
    h = {"Authorization": f"Bearer {readonly_key}"}
    params = {"q": "TEST_ROI_SPU_AB"}
    # 造数据前的基线 meta(未归属计数是全局口径,delta 断言不受其它残留行影响)
    r0 = api_client.get("/v2/analytics/spu-roi", headers=h, params=params)
    assert r0.status_code == 200, r0.text
    base = r0.json()["meta"]["unattributed_refund_lines"]

    with Session(db_engine) as sess:
        _seed(sess, _seed_unpaid_refund_spu)

    r = api_client.get("/v2/analytics/spu-roi", headers=h, params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1, body["items"]
    item = body["items"][0]

    # 销售侧不受影响:UNPAID 单不进有效销售(DELIVERED 单 5 件×$20)
    assert item["order_count"] == 1
    assert item["units_sold"] == 5
    assert item["sales"] == "100.0000"

    # 异常订单退款不进 refund_net 桶(REFUND_ONLY/RETURN 桶都保持 0)
    assert item["refund_return_qty"] == 0
    assert item["refund_return_amount"] == "0.0000"
    assert item["refund_net_qty"] == 0
    assert item["refund_net_amount"] == "0.0000"
    assert item["refund_rate"] == "0.00"
    # 也不进取消桶 / 货损
    assert item["refund_cancelled_qty"] == 0
    assert item["refund_cancelled_amount"] == "0.0000"
    assert item["refund_cancelled_missing_lines"] == 0
    assert item["return_loss"] == "0.0000"
    # 行内金额视同无此退款:v7 net_profit = 100×(1−0.308)×1.0 − 5×5.9096 − 0
    # = 69.2 − 29.5480 = 39.6520
    assert item["platform_fee"] == "30.8000"
    assert Decimal(item["net_profit"]) == Decimal("39.6520")

    # 防御性进未归属:meta 计数 +1(不静默)
    assert body["meta"]["unattributed_refund_lines"] == base + 1, body["meta"]


# ─── 行范围(include_all)──────────────────────────────────────────────


def test_spu_roi_excludes_inactive_by_default_includes_with_flag(
    api_client, readonly_key, db_engine
):
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)
        inactive_pk = _seed(sess, _seed_inactive_spu)

    # 默认:无活动 SPU 不在行范围
    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": Q},
    )
    body = r.json()
    assert body["total"] == 1
    assert [i["spu_id"] for i in body["items"]] == ["TEST_ROI_SPU_A"]

    # include_all=true:全量目录行,无活动行金额全 0、ROI null
    r2 = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": Q, "include_all": "true"},
    )
    body2 = r2.json()
    assert body2["total"] == 2
    by_id = {i["spu_id"]: i for i in body2["items"]}
    assert inactive_pk == by_id["TEST_ROI_SPU_INACTIVE"]["spu_pk"]
    zero = by_id["TEST_ROI_SPU_INACTIVE"]
    assert zero["ad_count"] == 0
    assert zero["spend"] == "0.0000"
    assert zero["sales"] == "0.0000"
    assert zero["units_sold"] == 0
    assert zero["refund_net_amount"] == "0.0000"
    assert zero["net_profit"] == "0.0000"
    assert zero["roi_real"] is None
    assert zero["roi_breakeven"] is None
    assert zero["roi_l0"] is None
    assert zero["refund_rate"] is None
    assert zero["cpa"] is None


def test_spu_roi_include_all_filters_non_active_status(
    api_client, readonly_key, db_engine
):
    """§5.1-7 include_all = 全部 ACTIVE SPU:DEACTIVATE 状态不进行范围。

    默认(不含 include_all)仍按"有活动"返回(不按状态裁剪),include_all
    目录查询加 status ILIKE 'activate' 后 DEACTIVATE 目录行被排除。
    """
    with Session(db_engine) as sess:
        _seed(sess, _seed_status_mix)

    h = {"Authorization": f"Bearer {readonly_key}"}
    # 默认:有活动的 ACTIVATE + DEACTIVATE 都在(状态不参与默认行范围)
    r = api_client.get("/v2/analytics/spu-roi", headers=h, params={"q": Q})
    body = r.json()
    assert body["total"] == 2
    by_id = {i["spu_id"] for i in body["items"]}
    assert by_id == {"TEST_ROI_SPU_ACTIVE_ON", "TEST_ROI_SPU_DEACTIVE_ON"}

    # include_all=true:只含 ACTIVE 状态(含无活动),DEACTIVATE 一律排除
    r2 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": Q, "include_all": "true"},
    )
    body2 = r2.json()
    assert body2["total"] == 2
    by_id2 = {i["spu_id"] for i in body2["items"]}
    assert by_id2 == {"TEST_ROI_SPU_ACTIVE_ON", "TEST_ROI_SPU_ACTIVE_IDLE"}
    assert "TEST_ROI_SPU_DEACTIVE_ON" not in by_id2
    assert "TEST_ROI_SPU_DEACTIVE_IDLE" not in by_id2


def test_spu_roi_window_params_clip_sales_and_refunds(
    api_client, readonly_key, db_engine
):
    """w_start/w_end 裁剪销售(paid_at)与退款(updated_at_source);
    不传 = 全历史累计(§4.5)。"""
    with Session(db_engine) as sess:
        _seed(sess, _seed_window_spu)
        win_shop_pk = sess.execute(
            text("SELECT id FROM commerce.shops WHERE shop_id = 'TEST_SELLER_WIN'")
        ).scalar_one()

    h = {"Authorization": f"Bearer {readonly_key}"}
    # 默认(无窗口参数):全历史累计 → 两单两退款都在(带 shop_pk:coverage 只算本店,
    # 断言确定性,不受共享库其它店铺真实数据影响)
    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": "TEST_ROI_SPU_WIN", "shop_pk": win_shop_pk},
    )
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["order_count"] == 2
    assert item["units_sold"] == 5
    assert item["sales"] == "100.0000"
    assert item["refund_return_qty"] == 2
    assert item["refund_return_amount"] == "40.0000"
    # meta.window 如实注记:ad=视图全窗口累计;销售/退款=全历史(未裁剪)
    assert "ad=视图全窗口累计" in body["meta"]["window"]["note"]
    assert "未裁剪" in body["meta"]["window"]["note"]
    # 日期可裁剪数据(销售∪退款)的真实跨度:窗外 2026-08-10/2026-08-20 +
    # 窗内 2026-09-10/2026-09-12 → min=08-10, max=09-12(页面回填日期框)
    w = body["meta"]["window"]
    assert w["coverage_first_day"] == "2026-08-10"
    assert w["coverage_last_day"] == "2026-09-12"

    # 传窗口:早于 2026-09-01 的销售单/退款 case 被排除(窗口边界含 w_end 当日)
    r2 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={
            "q": "TEST_ROI_SPU_WIN",
            "w_start": "2026-09-01",
            "w_end": "2026-09-30",
        },
    )
    body2 = r2.json()
    assert body2["total"] == 1
    item2 = body2["items"][0]
    assert item2["order_count"] == 1, item2  # 窗外 TEST_ORDER_W2 被排除
    assert item2["units_sold"] == 3
    assert item2["sales"] == "60.0000"
    assert item2["refund_return_qty"] == 1  # 窗外 2026-08-20 case 被排除
    assert item2["refund_return_amount"] == "20.0000"
    assert "已裁剪" in body2["meta"]["window"]["note"]
    # 结余带 totals 同窗口裁剪(2026-09-06):GMV/单量按 COALESCE(paid_at, order_time) 裁剪(全已付场景=paid_at)
    assert body2["totals"]["sales"] == "60.0000"
    assert body2["totals"]["gmv"] == "60.0000"  # 无取消单 → GMV = sales
    assert body2["totals"]["order_count"] == 1
    assert body2["totals"]["cancelled_order_count"] == 0
    assert body2["totals"]["total_orders"] == 1
    # 不传窗口 = 全历史:两单都在(与上面 item 断言同源)
    assert body["totals"]["sales"] == "100.0000"
    assert body["totals"]["gmv"] == "100.0000"
    assert body["totals"]["order_count"] == 2
    assert body["totals"]["total_orders"] == 2


def test_spu_roi_date_window_does_not_clip_ad(api_client, readonly_key, db_engine):
    """review 结论实证:起始/截止日只裁剪销售(paid_at)与退款(updated_at_source),
    广告 spend/gmv/ad_count 不受 w_start/w_end 影响(ad=视图全窗口累计供参考)。

    回归护栏:防止将来有人把 ad 也悄悄按日期切片 → ROI 分母(全窗口 spend)
    与分子(裁剪后净现金)口径错配而无提示。
    """
    with Session(db_engine) as sess:
        shop_pk = _seed_shop(sess, "TEST_SELLER_WAD")
        spu_pk = _seed_spu(sess, shop_pk, "TEST_ROI_SPU_WAD")
        # 广告行落在窗口之外(2026-06-01)——若 ad 被日期裁剪就会消失
        _seed_ad_dump(
            sess,
            seller="TEST_SELLER_WAD",
            product_id="TEST_ROI_SPU_WAD",
            campaign_id="CAMP_OUTSIDE_WINDOW",
            spend="25",
            orders="0",
            gmv="30",
            day="2026-06-01",
        )
        # 两笔销售:窗内(09-10)与窗外(08-10)各 $20
        _seed_order_line(
            sess,
            shop_pk=shop_pk,
            spu_pk=spu_pk,
            order_id="TEST_ORDER_WAD1",
            status=PAID_ORDER_STATUS,
            line_ext="TEST_LINE_WAD1",
            qty="1",
            unit_price="526600",  # $20
            paid=True,
            paid_iso="2026-09-10T08:00:00+00:00",
        )
        _seed_order_line(
            sess,
            shop_pk=shop_pk,
            spu_pk=spu_pk,
            order_id="TEST_ORDER_WAD2",
            status=PAID_ORDER_STATUS,
            line_ext="TEST_LINE_WAD2",
            qty="1",
            unit_price="526600",
            paid=True,
            paid_iso="2026-08-10T08:00:00+00:00",
        )
        sess.commit()  # handler 用独立连接读,必须真提交(SQLAlchemy 2 上下文不自动 commit)
    h = {"Authorization": f"Bearer {readonly_key}"}
    # 不限窗口:ad spend 25 / sales 40 / 2 单
    r_all = api_client.get(
        "/v2/analytics/spu-roi", headers=h, params={"q": "TEST_ROI_SPU_WAD"}
    )
    item_all = r_all.json()["items"][0]
    assert item_all["ad_count"] == 1
    assert item_all["spend"] == "25.0000"
    assert item_all["sales"] == "40.0000"
    assert item_all["order_count"] == 2
    # 裁剪到 09-01~09-30:销售只剩 1 单 $20;ad 依旧全窗口(窗外广告行仍在)
    r_crop = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={
            "q": "TEST_ROI_SPU_WAD",
            "w_start": "2026-09-01",
            "w_end": "2026-09-30",
        },
    )
    item_crop = r_crop.json()["items"][0]
    assert item_crop["ad_count"] == 1, item_crop  # 广告不被日期裁剪
    assert item_crop["spend"] == "25.0000"
    assert item_crop["sales"] == "20.0000"
    assert item_crop["order_count"] == 1
    assert "已裁剪" in r_crop.json()["meta"]["window"]["note"]


def test_spu_roi_sort_whitelist_covers_page_sortable_columns(
    api_client, readonly_key, db_engine
):
    """页面 spu-roi.js 可排序列名 ⊆ 端点 sort 白名单(不再 422)。

    spu-roi.js 的 SORTABLE 集合与端点 Literal 白名单必须同步:遍历
    JS 中每个可排序列名 → sort=<列> 请求必须 200。
    """
    import re
    from pathlib import Path

    js_path = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "spu-roi.js"
    )
    src = js_path.read_text(encoding="utf-8")
    m = re.search(r"(?s)var SORTABLE = new Set\(\[(.*?)\]\);", src)
    assert m, "spu-roi.js 找不到 SORTABLE 集合"
    fields = re.findall(r'"([a-z0-9_]+)"', m.group(1))
    assert fields, "SORTABLE 集合为空"

    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)
    h = {"Authorization": f"Bearer {readonly_key}"}
    for field in fields:
        r = api_client.get(
            "/v2/analytics/spu-roi",
            headers=h,
            params={"q": Q, "sort": field},
        )
        assert r.status_code == 200, f"sort={field} 应 200,得 {r.status_code}"
    # 未知 sort 值仍 422(白名单收紧语义)
    assert (
        api_client.get(
            "/v2/analytics/spu-roi", headers=h, params={"sort": "nope"}
        ).status_code
        == 422
    )


def test_spu_roi_totals_roi_real_native_reconciliation(
    api_client, readonly_key, db_engine
):
    """totals.roi_real v7 对账：(Σnet_revenue − Σreturn_loss)/Σspend，USD 口径。

    v7：net_revenue 已含汇率换算，Σ跨 SPU 累加后除 Σspend（§5.4-4）。
    直接断言现计算值（各 SPUs 已在全 USD 累加）；不强求旧的「全为整」的
    Σ原币=198 USD=整数（v5 行为）。
    """
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)  # spend 10
        _seed(sess, _seed_spu_b)  # spend 50
        _seed(sess, _seed_spu_c)  # spend 10

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get("/v2/analytics/spu-roi", headers=h, params={"q": Q})
    body = r.json()
    assert body["total"] == 3

    items = {it["spu_id"]: it for it in body["items"]}
    # 场景 B/C 走默认 DEFAULT_K1 = 40 CNY
    # v7：net_revenue = sales × (1−0.308) × (1−refund_rate_spu)
    # return_loss = full_loss_qty × unit_cost（场景无 38301 → 0）
    # 三 SPU 默认成本 × 5件（unit_cost = 40 × 0.14774 = 5.9096）
    # A：sales=$100, refund=$20, units=5；net=100×0.692×0.80=55.36
    # B：sales=?, refund=?, units=? （按 _seed_spu_b）
    # C：sales=?, refund=?, units=? （按 _seed_spu_c）
    # 统一验证：totals.roi_real == (Σnet_revenue − Σreturn_loss) / Σspend
    total_net_revenue_usd = sum(Decimal(it["net_revenue"]) for it in items.values())
    total_return_loss_usd = sum(Decimal(it["return_loss"]) for it in items.values())
    total_spend_usd = sum(Decimal(it["spend"]) for it in items.values())
    expected = (total_net_revenue_usd - total_return_loss_usd) / total_spend_usd
    assert body["totals"]["roi_real"] == m2(Decimal(expected))


def test_spu_roi_totals_roi_real_single_row_matches_item(
    api_client, readonly_key, db_engine
):
    """单行场景:totals.roi_real == 该行 roi_real(同源同公式)。

    v7 单 SPU：净收入按已结算/未结算分层，D1 DEFAULT=40 CNY 后 net_profit
    同步变动；此处仅断言 totals == items[0].roi_real。
    """
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)
    r2 = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_A"},
    )
    body2 = r2.json()
    assert body2["total"] == 1
    assert body2["totals"]["roi_real"] == body2["items"][0]["roi_real"]


# ─── 分页 / 搜索 / 排序 ───────────────────────────────────────────────


def test_spu_roi_default_sort_roi_asc_pagination_and_totals(
    api_client, readonly_key, db_engine
):
    """3 个活动 SPU:v7 默认 ROI 升序(D8 保留端点默认 sort=roi_real 不动);
    分页 limit/offset;totals 跨分页加总。

    v7 ROI 值变动（成本默认 40 CNY + 未结算 ×(1−r̂)×(1−rate)）。校验 ROI
    排序顺序与具体数值一同产出（不写死数字，从 net_revenue/return_loss/
    spend 反推）。
    """
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)
        _seed(sess, _seed_spu_b)
        _seed(sess, _seed_spu_c)

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get(
        "/v2/analytics/spu-roi", headers=h, params={"q": Q, "sort": "roi_real"}
    )
    body = r.json()
    assert body["total"] == 3

    # 从实际响应推导期望顺序与值
    items = body["items"]
    spu_roi = {it["spu_id"]: Decimal(it["roi_real"]) for it in items}
    # A spend=10, B=50, C=10（已知常数）
    expected_order = sorted(spu_roi, key=lambda k: spu_roi[k])
    assert [i["spu_id"] for i in items] == expected_order
    for spu_id, _ in spu_roi.items():
        # 不再写死：从行 net_revenue/return_loss/spend 反推（舍入到 2dp）
        row = next(it for it in items if it["spu_id"] == spu_id)
        nc = Decimal(row["net_revenue"])
        rl = Decimal(row["return_loss"])
        sp = Decimal(row["spend"])
        if sp != 0:
            expected = (nc - rl) / sp
            # 服务端 quantize 2dp（HALF_UP）
            assert Decimal(row["roi_real"]) == expected.quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

    totals = body["totals"]
    assert totals["row_count"] == 3
    assert totals["order_count"] == 3
    assert totals["cancelled_order_count"] == 1
    assert totals["total_orders"] == 4
    assert totals["spend"] == "70.0000"
    assert totals["sales"] == "220.0000"
    assert totals["gmv"] == "260.0000"
    assert totals["refund_net_amount"] == "20.0000"

    # totals = 行加总（money 4 位）
    row_sum = {
        "spend": sum((Decimal(i["spend"]) for i in items), Decimal(0)),
        "sales": sum((Decimal(i["sales"]) for i in items), Decimal(0)),
        "refund_net_amount": sum(
            (Decimal(i["refund_net_amount"]) for i in items), Decimal(0)
        ),
        "net_profit": sum((Decimal(i["net_profit"]) for i in items), Decimal(0)),
    }
    assert m4(row_sum["spend"]) == totals["spend"]
    assert m4(row_sum["sales"]) == totals["sales"]
    assert m4(row_sum["refund_net_amount"]) == totals["refund_net_amount"]
    assert m4(row_sum["net_profit"]) == totals["net_profit"]

    # 分页：limit=2 → B,C;offset=2 → A
    r2 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": Q, "sort": "roi_real", "limit": 2, "offset": 0},
    )
    b2 = r2.json()
    assert [i["spu_id"] for i in b2["items"]] == expected_order[:2]
    assert b2["total"] == 3
    assert b2["totals"]["row_count"] == 3

    r3 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": Q, "sort": "roi_real", "limit": 2, "offset": 2},
    )
    b3 = r3.json()
    assert [i["spu_id"] for i in b3["items"]] == expected_order[2:]


def test_spu_roi_sort_order_desc_and_search(api_client, readonly_key, db_engine):
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)
        _seed(sess, _seed_spu_b)
        _seed(sess, _seed_spu_c)

    h = {"Authorization": f"Bearer {readonly_key}"}
    # 搜索 spu_id 子串
    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": "TEST_ROI_SPU_C"},
    )
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["spu_id"] == "TEST_ROI_SPU_C"

    # sort=net_profit + order=desc
    r2 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": Q, "sort": "net_profit", "order": "desc"},
    )
    body2 = r2.json()
    profits = [Decimal(i["net_profit"]) for i in body2["items"]]
    assert profits == sorted(profits, reverse=True)
    # A 17.0390(新基线 30.8%)… 排序断言见下(以实际为准)
    assert body2["items"][0]["spu_id"] == "TEST_ROI_SPU_A"

    # sort=spend desc 与 sort=sales asc
    r3 = api_client.get(
        "/v2/analytics/spu-roi",
        headers=h,
        params={"q": Q, "sort": "spend", "order": "desc"},
    )
    b3 = r3.json()
    assert [i["spu_id"] for i in b3["items"]] == [
        "TEST_ROI_SPU_B",  # spend 50
        "TEST_ROI_SPU_A",
        "TEST_ROI_SPU_C",
    ]


def test_spu_roi_rejects_bad_params(api_client, readonly_key):
    h = {"Authorization": f"Bearer {readonly_key}"}
    # 未知 sort → 422;limit 超上限 → 422;非法 fee_rate → 422
    assert (
        api_client.get(
            "/v2/analytics/spu-roi", headers=h, params={"sort": "bogus"}
        ).status_code
        == 422
    )
    assert (
        api_client.get(
            "/v2/analytics/spu-roi", headers=h, params={"limit": 99999}
        ).status_code
        == 422
    )
    assert (
        api_client.get(
            "/v2/analytics/spu-roi", headers=h, params={"fee_rate": "abc"}
        ).status_code
        == 422
    )
    # 非有限 Decimal(NaN/Infinity)比较不报错但会穿透到 quantize → 一律 422,不能 500
    assert (
        api_client.get(
            "/v2/analytics/spu-roi", headers=h, params={"fee_rate": "NaN"}
        ).status_code
        == 422
    )
    assert (
        api_client.get(
            "/v2/analytics/spu-roi", headers=h, params={"fee_rate": "Infinity"}
        ).status_code
        == 422
    )
    # 有限但指数量级巨大(1e9999999)会穿透到乘法/_fmt_money quantize → 量级上限 422,不能 500
    assert (
        api_client.get(
            "/v2/analytics/spu-roi", headers=h, params={"fee_rate": "1e9999999"}
        ).status_code
        == 422
    )


def test_spu_roi_empty_result_and_meta(api_client, readonly_key):
    """q 不命中 → items 空 + envelope 结构 + meta 字段齐全。"""
    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_NO_MATCH_XYZ"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["totals"] == {
        "row_count": 0,
        "order_count": 0,
        "cancelled_order_count": 0,
        "total_orders": 0,
        "spend": "0.0000",
        "sales": "0.0000",
        "gmv": "0.0000",
        "refund_net_amount": "0.0000",
        "return_loss": "0.0000",
        "net_profit": "0.0000",
        "roi_real": None,  # Σspend=0 → null(页面显示 —)
    }
    meta = body["meta"]
    assert meta["fx"] == {
        "usd_vnd": "26330.0000",
        "cny_usd": "0.1477",
        "as_of": "2099-09-06",  # 在线 fx 快照注入(autouse),非 D9 常量日期
        "source": "fx-cache",
    }
    assert meta["fee"]["mode"] == "baseline"
    assert meta["fee"]["rate"] == "0.308"
    assert meta["fee"]["override"] is None
    assert meta["cost_assumption"]
    assert "first_day" in meta["window"]
    assert "last_day" in meta["window"]
    # meta.window = ad 视图全窗口供参考(§4.5:销售/退款默认不裁剪)
    assert "ad=视图全窗口累计" in meta["window"]["note"]
    assert "w_start/w_end" in meta["window"]["note"]
    assert meta["unattributed_refund_lines"] >= 0
    assert meta["computed_at"]
    assert meta["currency"] == {
        "display": "USD",
        "native": {"ad": "USD", "sales_refund": "VND", "cost": "CNY"},
    }
    # 金额列可 JSON 序列化(Decimal 已转字符串)
    json.dumps(body)


def test_spu_roi_fee_rate_override_meta(api_client, readonly_key, db_engine):
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)

    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_ROI_SPU_A", "fee_rate": "0.20"},
    )
    body = r.json()
    assert body["total"] == 1
    meta = body["meta"]
    assert meta["fee"]["mode"] == "override"
    assert meta["fee"]["rate"] == "0.20"
    assert meta["fee"]["override"] == "0.20"
    # v7：fee_rate=0.20 + 无结算 + refund_rate=0.20
    # net_revenue = 0 + 100×0.80×0.80 = 64；cogs=5×5.9096=29.5480
    # net_profit = 64−29.5480−10 = 24.4520
    item = body["items"][0]
    assert item["platform_fee"] == "20.0000"
    assert Decimal(item["net_profit"]) == Decimal("24.4520")


# ─── 页面契约 ─────────────────────────────────────────────────────────


def test_spu_roi_page_returns_html(api_client, readonly_key):
    r = api_client.get(
        "/v2/pages/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html"), r.headers


def test_spu_roi_page_requires_auth(api_client):
    assert api_client.get("/v2/pages/spu-roi").status_code == 401


def test_spu_roi_page_toolbar_shop_and_date_filters(api_client, readonly_key):
    """§7.1 工具条 [店铺▾][日期▾] 入口:页面 HTML 含筛选控件 id。

    review finding B:店铺/日期窗口能力此前只在 API 层,页面无入口。
    仅锁 HTML 元素契约(控件行为在 static/js/spu-roi.js,JS 运行时
    不在契约范围);默认全部店铺(空值=不传 shop_pk=全历史)。
    """
    r = api_client.get(
        "/v2/pages/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    # 店铺下拉(含"全部店铺"占位,其余由 JS 从 channel-accounts 拉取填充)
    assert 'id="filter-shop"' in body
    assert '<option value="">全部店铺</option>' in body
    # 日期范围输入(空 = 不限 → w_start/w_end 不传 = 全历史)
    assert 'id="filter-w-start"' in body
    assert 'id="filter-w-end"' in body
    assert 'type="date"' in body
    # 含无活动 hover 问号解释(? 悬停出现,data-tip 委托)
    assert "含无活动" in body
    assert 'class="op-hint"' in body
    assert "没有任意活动" in body
    # 概览 10 格: sum-refund / sum-loss 仍存在;M13b 已迁钻取面板
    assert "sum-refund" in body
    assert "sum-loss" in body
    assert "REFUND_ONLY" in body
    # 无内联事件处理器(既有 shell 约束)
    for forbidden in ("onchange=", "onclick="):
        assert forbidden not in body, f"inline handler found: {forbidden}"


def test_spu_roi_page_shell_contract(api_client, readonly_key):
    """页面 HTML shell:标题、静态资源相对路径、warm-paper token。"""
    r = api_client.get(
        "/v2/pages/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert "SPU 实际 ROI" in body, "missing page title"
    assert "../../static/vendor/bootstrap.min.css" in body, "missing bootstrap link"
    assert "../../static/js/spu-roi.js" in body, "missing spu-roi.js link"
    # 前缀安全(2026-08-31 回归):无根绝对路径资源引用
    assert 'href="/' not in body
    assert 'src="/' not in body
    # warm-paper 工业操作台 token
    assert "--paper:" in body
    assert "--accent:" in body
    assert "--mono:" in body
    assert "border-radius: 0" in body
    # 无外链字体
    assert "fonts.googleapis.com" not in body
    assert "fonts.gstatic.com" not in body
    # 无内联事件处理器
    for forbidden in ("onclick=", "onsubmit=", "onchange="):
        assert forbidden not in body, f"inline handler found: {forbidden}"


def test_spu_roi_js_targets_dashboard_hooks():
    """spu-roi.js 必须存在且渲染表格与结余带。"""
    from pathlib import Path

    js_path = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "spu-roi.js"
    )
    src = js_path.read_text(encoding="utf-8")
    # 必须消费 /v2/analytics/spu-roi(带 PREFIX 推导)
    assert "/v2/analytics/spu-roi" in src
    assert "PREFIX" in src
    assert "unwrap" in src
    assert "401" in src  # 401 → login 跳转
    assert "roi_breakeven" in src  # 红绿判据字段
    assert "cost_source" in src
    assert "DEFAULT_K1" in src  # ⚠ 判断


def test_spu_roi_page_header_summary_extended_band(api_client, readonly_key):
    """§7.1 结余带(2026-09-06):去 SPU 数;新增 GMV/有效单量/总单量/取消单量;
    全损货损改名全损退款(数值仍 = totals.return_loss);每个概览格带 ? 口径说明。
    """
    from pathlib import Path

    r = api_client.get(
        "/v2/pages/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    # 10 格指标 id 齐全(顺序 = 页面骨架)
    for cell_id in (
        "sum-spend",
        "sum-sales",
        "sum-gmv",
        "sum-orders",
        "sum-total-orders",
        "sum-refund",
        "sum-loss",
        "sum-cancelled-orders",
        "sum-profit",
        "sum-roi",
    ):
        assert f'id="{cell_id}"' in body, f"结余带缺 {cell_id} 格"
    # 不再展示 SPU 个数
    assert 'id="sum-n"' not in body
    assert "全损退款" in body  # 全损货损改名
    # D8(2026-09-07)主表精确匹配 th 表头文本
    main_th_labels = re.findall(r'<th[^>]*scope="col"[^>]*>([^<]+)</th>', body)
    for col_label in (
        "商品",
        "广告消耗",
        "有效GMV",
        "有效出单量",
        "取消率%",
        "全损退款率%",
        "净利润",
    ):
        assert col_label in main_th_labels, f"主表缺列 {col_label}"
    # D8 删除:原 13 列里只在 th 表头出现过的标签
    for removed in (
        "销售$",
        "有效销售$",
        "退货$",
        "退货率%",
        "全损退款$",
        "实际ROI",
        "保本ROI",
    ):
        assert removed not in main_th_labels, f"D8 已删:th 表头残留 {removed}"
    # D8 删除 ⚙ 列开关组
    for toggle in (
        "col-toggle-adref",
        "col-toggle-structure",
        "col-toggle-refundsplit",
        "col-toggle-cancel",
        "col-toggle-fee",
    ):
        assert toggle not in body
    # M13b 已迁钻取面板利润构成 tab(D4 B 切 38301 全损口径)
    assert "M13b" in body
    # 钻取面板存在(D7 行内 accordion)
    assert "op-drill" in body
    assert "tpl-drilldown-panel" in body
    # 每个概览格都有 ? 口径悬停
    assert body.count('class="op-hint"') >= 10
    # JS 必须填充全损格与新格
    js_src = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "spu-roi.js"
    ).read_text(encoding="utf-8")
    assert '("#sum-loss")' in js_src
    assert "totals.return_loss" in js_src
    assert '("#sum-gmv")' in js_src
    assert '("#sum-total-orders")' in js_src
    assert '("#sum-cancelled-orders")' in js_src


def test_spu_roi_page_d8_no_column_toggles(api_client, readonly_key):
    """D8(2026-09-07):⚙ 列开关组全部删除;主表无 data-cg/col-hidden 信息列;
    可排序列 = D8 新白名单 spend/sales/order_count/cancel_rate/
    full_loss_rate/net_profit。
    """
    r = api_client.get(
        "/v2/pages/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert "col-toggle-" not in body
    assert "data-colgroup=" not in body
    assert "data-cg=" not in body
    sortable = re.findall(
        r'class="op-th[^"]*op-th-sort[^"]*" data-sort="([a-z0-9_]+)"', body
    )
    assert set(sortable) == {
        "spend",
        "sales",
        "order_count",
        "cancel_rate",
        "full_loss_rate",
        "net_profit",
    }, f"D8 主表可点列异常: {sortable}"


def test_spu_roi_page_sortable_headers_within_endpoint_whitelist(
    api_client, readonly_key, db_engine
):
    """页面可点列头 ⊆ 端点 sort 白名单:每个 data-sort 请求 200(不再 422)。"""
    import re

    r = api_client.get(
        "/v2/pages/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    fields = re.findall(
        r'class="op-th[^"]*op-th-sort[^"]*" data-sort="([a-z0-9_]+)"', body
    )
    assert fields
    with Session(db_engine) as sess:
        _seed(sess, _seed_scenario_a)
    h = {"Authorization": f"Bearer {readonly_key}"}
    for field in fields:
        r2 = api_client.get(
            "/v2/analytics/spu-roi",
            headers=h,
            params={"q": Q, "sort": field},
        )
        assert r2.status_code == 200, f"sort={field} 应 200,得 {r2.status_code}"


def test_spu_roi_js_review_fixes_present():
    """review 修复守卫:操作员身份 / 无投放 / 标色常量 / 服务端 ROI。"""
    from pathlib import Path

    js_path = (
        Path(__file__).resolve().parents[2]
        / "tts_erp_v2"
        / "static"
        / "js"
        / "spu-roi.js"
    )
    src = js_path.read_text(encoding="utf-8")
    # finding 6:loadMe 用 authenticated===true 守卫(而非不存在的 key_prefix)
    assert "authenticated === true" in src
    # D8 删除 §7.2 标色阈值常量(C3:仅按净利判)
    assert "ROI_HARD_LOSS = 1.0" not in src
    assert "PASS_LINE = 1.5" not in src
    assert "REFUND_RATE_ALERT" in src
    # 无投放文案(保留)
    assert "无投放" in src
    # D8 删除列开关 + 信息列字段
    assert "op-th col-hidden" not in src
    assert "td.col-hidden" not in src
    # D7/D6 钻取面板:accordion + tab 懒加载
    assert "openDrillPanel" in src
    assert "bindRowAccordion" in src
    assert "fetchDrillTab" in src
    assert "tpl-drilldown-panel" in src
    # A2:页面 JS 显式传 sort=net_profit&order=asc
    assert "DEFAULT_SORT" in src
    assert '"net_profit"' in src
    assert '"asc"' in src
    # finding 2:结余带直接消费 totals.roi_real,页面不反推 ROI
    assert "totals.roi_real" in src
    # 2026-09-06:日期框按数据真实跨度回填(meta.window.coverage_*)只读一次
    assert "coverage_first_day" in src
    assert "datesTouched" in src


# ─── 在线汇率接入(D1 落地 2026-09-06)─────────────────────────────────


def _fx_meta_of_empty_query(api_client, readonly_key) -> dict:
    """q 不命中 → items 空,meta.fx 仍按当前汇率解析给出(在线或回退)。"""
    r = api_client.get(
        "/v2/analytics/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
        params={"q": "TEST_NO_MATCH_XYZ"},
    )
    assert r.status_code == 200, r.text
    return r.json()["meta"]["fx"]


def test_spu_roi_meta_uses_live_fx_rates(api_client, readonly_key, monkeypatch):
    """换算汇率来自在线 fx 快照:monkeypatch 一张非 D9 值的 USD 快照,
    验证实现真正走 fx-cache —— 值/来源/日期都取自已注入的快照(不是常量)。
    派生数学:CNY→USD = 1/rates[CNY] 量化 8dp;USD→VND = rates[VND]。
    """
    from datetime import UTC, datetime

    from tts_erp_v2.fx.rates import RateMap

    rm = RateMap(
        snapshot_id=999_000_001,
        base_code="USD",
        upstream_last_update=datetime(2026, 9, 6, 0, 0, 1, tzinfo=UTC),
        next_update_at=datetime(2026, 9, 7, 0, 0, 1, tzinfo=UTC),
        fetched_at=datetime(2026, 9, 6, 6, 0, 0, tzinfo=UTC),
        rates={
            "USD": Decimal(1),
            "VND": Decimal(26000),
            "CNY": Decimal("6.9"),
        },
    )
    monkeypatch.setattr(spu_roi_mod, "load_rate_map", lambda sess, base_code="USD": rm)
    fx = _fx_meta_of_empty_query(api_client, readonly_key)
    assert fx == {
        "usd_vnd": "26000.0000",
        "cny_usd": "0.1449",  # 1/6.9 = 0.14492753… → .4f
        "as_of": "2026-09-06",
        "source": "fx-cache",
    }


def test_spu_roi_fx_fallback_to_fixed_const_when_no_snapshot(
    api_client, readonly_key, monkeypatch
):
    """缓存未就绪(无 USD 快照)→ 回退 D9 固定常量,金额换算不空白。"""
    monkeypatch.setattr(
        spu_roi_mod, "load_rate_map", lambda sess, base_code="USD": None
    )
    fx = _fx_meta_of_empty_query(api_client, readonly_key)
    assert fx == {
        "usd_vnd": "26330.0000",
        "cny_usd": "0.1477",
        "as_of": "2026-09-05",
        "source": "fixed-const",
    }


# ─── D7/D6 钻取面板(行内 accordion + tab 懒加载)──────────────────────────────


def test_spu_roi_page_drilldown_template_present(api_client, readonly_key):
    """D7(2026-09-07):页面含 #tpl-drilldown-panel 模板 + 5 tab + 摘要/正文 region。"""
    r = api_client.get(
        "/v2/pages/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    assert 'id="tpl-drilldown-panel"' in body
    for tab in ("pnl", "orders", "settlements", "cases", "ads"):
        assert f'data-tab="{tab}"' in body, f"D7 缺 tab {tab}"
    assert 'data-region="summary"' in body
    assert 'data-region="body"' in body
    assert 'data-banner="warn"' in body


def test_spu_roi_page_no_old_columns(api_client, readonly_key):
    """D8(2026-09-07)主表无隐藏列、无 ⚙ 开关、无 ROI 列;6 列标签齐全。"""
    r = api_client.get(
        "/v2/pages/spu-roi",
        headers={"Authorization": f"Bearer {readonly_key}"},
    )
    body = r.text
    main_th_labels = re.findall(r'<th[^>]*scope="col"[^>]*>([^<]+)</th>', body)
    for forbidden in (
        "实际ROI",
        "保本ROI",
        "销售$",
        "有效销售$",
        "退货$",
        "取消单量",
        "退货率%",
        "全损退款$",
        "关联广告数",
    ):
        assert forbidden not in main_th_labels, f"D8 已删:th 表头残留 {forbidden}"
    for col in (
        "商品",
        "广告消耗",
        "有效GMV",
        "有效出单量",
        "取消率%",
        "全损退款率%",
        "净利润",
    ):
        assert col in main_th_labels, f"D8 主表缺列 {col}"
    assert "op-th col-hidden" not in body
    assert "td.col-hidden" not in body


# ═════════════════════════════════════════════════════════════════════
# §3.5 D2 零值落库 + D4 全损 38301 + D5/COGS_kept 防御钳位 + 成本层穿透
# + §6.3 钻取端点契约（review 后续补测）
# ═════════════════════════════════════════════════════════════════════


def _seed_source_price_direct(
    sess, *, shop_pk: int, spu_id: str, spu_pk: int, cost: str
) -> None:
    """在 procurement_products 插入一条 1688 货源价（TK-side 直取路径 L3a）。"""
    # 借一个现有账户（“North Nook”= id 2288,对应测试 shop TEST_SELLER_A）
    sess.execute(
        text(
            "INSERT INTO procurement.procurement_products ("
            " procurement_account_id, external_product_id, product_type, title,"
            " source_platform, source_unit_cost, synced_at"
            ") VALUES (2288, :ext, 'SPU', 'TEST 货源价', '1688',"
            " CAST(:cost AS numeric), now())"
        ),
        {"ext": spu_id, "cost": cost},
    )


def _seed_source_price_via_offer(
    sess, *, spu_id: str, offer_item_id: str, cost: str
) -> None:
    """L3b：仅插入公共采集箱行（external_product_id ≠ spu_id），让 SOURCE_PRICE 走 source_item_id 桥路径。"""
    sess.execute(
        text(
            "INSERT INTO procurement.procurement_products ("
            " procurement_account_id, external_product_id, product_type, title,"
            " source_platform, source_item_id, source_unit_cost, synced_at"
            ") VALUES (2288, :ext, 'OFFER', 'TEST 采集箱行',"
            " '1688', :item_id, CAST(:cost AS numeric), now())"
        ),
        {"ext": "OFFER_" + offer_item_id, "item_id": offer_item_id, "cost": cost},
    )


def _seed_tracking_event_overseas(
    sess, *, order_pk: int, action_code: int = 38301
) -> int:
    """插入包裹 + 38301 海外到达事件，助 D4 B 全损口径测试。"""
    ship_pk = sess.execute(
        text(
            "INSERT INTO fulfillment.shipments ("
            " shop_pk, order_pk, external_package_id, tracking_number,"
            " provider_name, status, shipped_at"
            ") SELECT s.id, :op, 'TEST_PKG', 'TN_001', 'TEST',"
            " 'in_transit', coalesce(so.paid_at, so.order_time)"
            " FROM commerce.sales_orders so"
            " JOIN commerce.shops s ON s.id = so.shop_pk"
            " WHERE so.id = :op"
            " RETURNING id"
        ),
        {"op": order_pk},
    ).scalar_one()
    sess.execute(
        text(
            "INSERT INTO fulfillment.tracking_events ("
            " shipment_id, action_code, event_at, description"
            ") VALUES (:sp, :ac, now(), 'TEST 到达海外')"
        ),
        {"sp": ship_pk, "ac": action_code},
    )
    return ship_pk


def test_spu_roi_cost_source_price_direct_layer(api_client, readonly_key, db_engine):
    """D1 L3a：1688 货源价直取（procurement_products.external_product_id = spu_id）。

    验证 reviewer 修的 latent bug（`=` 被 sed 吃掉导致 L3a 实际 no-op）
    被彻底修复：SOURCE_PRICE 路径要能正确返回 cost_source='SOURCE_PRICE' 和
    对应 source_unit_cost。
    """
    # 清理前次运行残留（unique constraint 防重复）
    with Session(db_engine) as sess:
        sess.execute(
            text(
                "DELETE FROM procurement.procurement_products "
                "WHERE external_product_id = :ext"
            ),
            {"ext": "TEST_ROI_SPU_PRICE_DIRECT"},
        )
        sess.commit()

    with Session(db_engine) as sess:
        spu_id = "TEST_ROI_SPU_PRICE_DIRECT"
        shop_pk = _seed_shop(sess, "TEST_SELLER_PRICE")
        spu_pk = _seed_spu(sess, shop_pk, spu_id)
        _seed_source_price_direct(
            sess, shop_pk=shop_pk, spu_id=spu_id, spu_pk=spu_pk, cost="35.0000"
        )
        o1 = _seed_order_line(
            sess,
            shop_pk=shop_pk,
            spu_pk=spu_pk,
            order_id="TEST_ORDER_PRICE",
            status=PAID_ORDER_STATUS,
            line_ext="TEST_LINE_PRICE",
            qty="2",
            unit_price="526600",
            paid=True,  # $20
        )
        sess.commit()

    h = {"Authorization": f"Bearer {readonly_key}"}
    r = api_client.get("/v2/analytics/spu-roi", headers=h, params={"q": spu_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["cost_source"] == "SOURCE_PRICE", item
    assert Decimal(item["unit_cost_used"]) == Decimal("35") * CNY_USD  # ≈5.1718


def test_spu_roi_drilldown_requires_auth(api_client):
    """钻取端点 readonly 鉴权（与主表一致：401 无 key）。"""
    for tab in ("orders", "settlements", "cases", "ads"):
        assert api_client.get(f"/v2/analytics/spu-roi/1/{tab}").status_code == 401
